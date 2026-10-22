"""Person-specific folder capabilities for built-in tools, not arbitrary code."""

from __future__ import annotations

import asyncio
import errno
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlencode

import httpx

from . import folder_io
from .local_tools import unavailable
from .settings import Settings
from .store import Person, Store


def visible(grant: dict[str, Any], person: Person | None) -> bool:
    return person is not None and grant["subject"] == person.sub


def public(grant: dict[str, Any], person: Person) -> dict[str, Any]:
    out = {key: grant[key] for key in ("id", "name", "subject")}
    out["writable"] = bool(grant["writable"])
    out["usable"] = visible(grant, person)
    if person.is_owner:
        out["path"] = grant["path"]
    return out


def definitions(writable: bool) -> list[tuple[str, str, dict[str, Any]]]:
    path = {
        "type": "string",
        "minLength": 1,
        "maxLength": 1024,
        "description": "Relative path inside this folder, using / separators. No .. or links.",
    }
    result = []
    for name, description, properties in (
        ("list_directory", "List up to 200 names. Use path '.' for this folder.", {"path": path}),
        (
            "read_text",
            "Read a UTF-8 text file, at most 32 KiB/16384 characters, and its SHA-256.",
            {"path": path},
        ),
        (
            "write_text",
            "Create or edit a UTF-8 text file. expectedSha256 must be the hash from "
            "read_text for an edit, or empty for create-only. No directories are created. "
            "The person approves the exact text. A write is not automatically retried or undone.",
            {
                "path": path,
                "text": {"type": "string", "maxLength": 8192},
                "expectedSha256": {"type": "string", "pattern": "^(?:[a-f0-9]{64})?$"},
            },
        ),
    ):
        if name != "write_text" or writable:
            result.append(
                (
                    name,
                    description,
                    {
                        "type": "object",
                        "properties": properties,
                        "required": list(properties),
                        "additionalProperties": False,
                    },
                )
            )
    return result


def problem(exc: OSError) -> str:
    if isinstance(exc, FileExistsError):
        return "This file already exists. Read it before proposing an edit with its SHA-256."
    if isinstance(exc, FileNotFoundError):
        return "The file or folder was not found. Check its relative path."
    if isinstance(exc, PermissionError):
        return (
            "Workbench's OS account cannot access this folder or file. "
            "Ask the machine administrator to check its permissions."
        )
    if exc.errno in {errno.ENOSYS, errno.EOPNOTSUPP}:
        return "This host does not support the required safe file operations. Update its OS."
    return (
        "The file could not be opened safely. Links, mounted subfolders, busy files "
        "and special files are not supported."
    )


class GrantGone(folder_io.FolderError):
    """The grant is not there any more, or is not this person's. No file operation ran."""

    def __init__(self) -> None:
        super().__init__("This folder grant is no longer available to you. No file operation ran.")


class Folders:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store, self.settings = store, settings
        self.http: httpx.AsyncClient | None = None
        # Grant mutations and small file operations serialize. Once revocation
        # returns, an earlier queued call cannot open a file under that grant.
        self.lock = asyncio.Lock()
        self.protected = [settings.data_dir, Path(sys.prefix), Path(__file__).parent]
        for path in (settings.key_file, settings.oidc_secret_file):
            if path is not None:
                self.protected.append(path.parent)

    def unavailable(self) -> str | None:
        return unavailable(self.settings)

    async def _io[T](self, function: Callable[..., T], *args: Any) -> T:
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # Repeated Stop/shutdown cancellation must not cancel the
                # worker's waiter and release the grant lock prematurely.
                cancelled = True
            except Exception:
                break
        if cancelled:
            task.exception()  # Retrieve any worker failure before propagating Stop.
            raise asyncio.CancelledError
        try:
            return task.result()
        except OSError as exc:
            raise folder_io.FolderError(problem(exc), cause=exc) from None

    async def add(self, grant: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            if reason := self.unavailable():
                raise folder_io.FolderError(reason)
            if await self.store.person(grant["subject"]) is None:
                raise folder_io.FolderError("Choose someone who has signed into this Workbench.")
            if len(await self.store.folder_grants()) >= 64:
                raise folder_io.FolderError("There are already 64 folder grants. Remove one first.")
            try:
                grant["path"] = await self._io(
                    folder_io.check_root_path, grant["path"], self.protected
                )
                grant["identity"] = await self._io(folder_io.inspect, grant["path"], self.protected)
            except folder_io.FolderError as exc:
                if isinstance(exc.__cause__, PermissionError) or isinstance(
                    exc.cause, PermissionError
                ):
                    raise folder_io.FolderError(
                        f"Workbench's account cannot open {grant['path']}. {exc.cause}",
                        cause=exc.cause,
                    ) from None
                raise
            await self.store.add_folder_grant(grant)
            return grant

    async def remove(self, grant_id: str) -> str | None:
        async with self.lock:
            grant = next((g for g in await self.store.folder_grants() if g["id"] == grant_id), None)
            await self.store.delete_folder_grant(grant_id)
            if grant is not None and not any(
                g["identity"] == grant["identity"] or _inside(g["path"], grant["path"])
                for g in await self.store.folder_grants()
            ):
                try:
                    await self.release(grant["path"])
                except (OSError, ValueError, httpx.HTTPError) as exc:
                    return (
                        f"Workbench no longer uses {grant['path']}, but its access stays: "
                        f"{str(exc) or type(exc).__name__}. Remove it in Eugene."
                    )
            return None

    def give_access_url(self, path: str, writable: bool = False) -> str | None:
        if not self.settings.oidc_issuer:
            return None
        query = urlencode(
            {
                "sel": f"app:{self.settings.id}",
                "path": path,
                "mode": "write" if writable else "read",
            }
        )
        return self.settings.oidc_issuer.removesuffix("/oidc") + "/apps/app?" + query

    async def release(self, path: str) -> None:
        if not self.settings.agent_url:
            # A launcher from before folder access cannot have added managed
            # permissions. Its manual provisioning remains the expert override.
            return
        if self.http is None or not self.settings.agent_url or not self.settings.key_file:
            raise ValueError("This Workbench has no local agent address or app key")
        key = await asyncio.to_thread(self.settings.key_file.read_text, encoding="utf-8")
        url = (
            self.settings.agent_url.rstrip("/")
            + f"/v1/apps/{self.settings.id}/folder-access/release"
        )
        headers = {"Authorization": f"Bearer {key.strip()}"}

        async def ask(body: dict[str, str]) -> list[dict[str, Any]]:
            assert self.http is not None
            response = await self.http.post(url, headers=headers, json=body)
            if response.status_code != 200:
                try:
                    detail = response.json().get("detail", {})
                    cause = detail.get("detail", detail) if isinstance(detail, dict) else detail
                except ValueError:
                    cause = response.text[:400]
                raise ValueError(f"Eugene answered {response.status_code}: {cause}")
            records: list[dict[str, Any]] = response.json()["grants"]
            if not records:
                raise ValueError("Eugene returned no matching folder access record")
            return records

        records = await ask({"path": path})
        remaining = await self.store.folder_grants()
        for record in records:
            if any(_inside(g["path"], record["path"]) for g in remaining):
                continue
            for outcome in await ask({"id": record["id"]}):
                if outcome["state"] != "removed":
                    raise ValueError(outcome["message"])

    async def _grant_of(self, grant_id: str, sub: str) -> dict[str, Any]:
        """The grant, if it is still there and names this person; else `GrantGone`."""
        grant = next((g for g in await self.store.folder_grants() if g["id"] == grant_id), None)
        if grant is None or grant["subject"] != sub or await self.store.person(sub) is None:
            raise GrantGone
        return grant

    async def scan[T](self, grant_id: str, sub: str, walker: Callable[[folder_io.Tree], T]) -> T:
        """Walk a granted folder for its owner's saved documents, read-only (`folder_io.scan`).
        One call under the same lock as every other file call, so a grant withdrawn while it waits
        is never walked. `GrantGone` when the grant is not there or not this person's."""
        async with self.lock:
            if reason := self.unavailable():
                raise folder_io.FolderError(reason)
            grant = await self._grant_of(grant_id, sub)
            return await self._io(
                folder_io.scan, grant["path"], grant["identity"], self.protected, walker
            )

    async def read(
        self,
        grant_id: str,
        sub: str,
        names: list[str],
        dest_fd: int,
        limit: int,
        head_size: int,
        head_check: Callable[[bytes, int], folder_io.Declined | None],
    ) -> folder_io.Taken | folder_io.Declined:
        """Copy one file of a granted folder to `dest_fd` (`folder_io.read_file`), under the
        lock."""
        async with self.lock:
            if reason := self.unavailable():
                raise folder_io.FolderError(reason)
            grant = await self._grant_of(grant_id, sub)
            return await self._io(
                folder_io.read_file,
                grant["path"],
                grant["identity"],
                self.protected,
                names,
                dest_fd,
                limit,
                head_size,
                head_check,
            )

    async def execute(
        self, grant_id: str, person: Person | None, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        async with self.lock:
            if reason := self.unavailable():
                raise folder_io.FolderError(reason)
            grant = next((g for g in await self.store.folder_grants() if g["id"] == grant_id), None)
            current = await self.store.person(person.sub) if person else None
            if grant is None or not visible(grant, current):
                raise folder_io.FolderError(
                    "This folder grant is no longer available to you. No file operation ran."
                )
            if tool == "write_text" and not grant["writable"]:
                raise folder_io.FolderError("This folder is read-only. No write ran.")
            return await self._io(
                folder_io.operate, grant["path"], grant["identity"], tool, arguments, self.protected
            )


def _inside(child: str, parent: str) -> bool:
    cls = PureWindowsPath if ":" in parent or parent.startswith("\\\\") else PurePosixPath
    return cls(child).is_relative_to(cls(parent))
