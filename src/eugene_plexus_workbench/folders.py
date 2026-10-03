"""Person-specific folder capabilities for built-in tools, not arbitrary code."""

from __future__ import annotations

import asyncio
import errno
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

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


class Folders:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store, self.settings = store, settings
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
            raise folder_io.FolderError(problem(exc)) from None

    async def add(self, grant: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            if reason := self.unavailable():
                raise folder_io.FolderError(reason)
            if await self.store.person(grant["subject"]) is None:
                raise folder_io.FolderError("Choose someone who has signed into this Workbench.")
            if len(await self.store.folder_grants()) >= 64:
                raise folder_io.FolderError("There are already 64 folder grants. Remove one first.")
            grant["path"] = await self._io(folder_io.check_root_path, grant["path"], self.protected)
            grant["identity"] = await self._io(folder_io.inspect, grant["path"], self.protected)
            await self.store.add_folder_grant(grant)
            return grant

    async def remove(self, grant_id: str) -> None:
        async with self.lock:
            await self.store.delete_folder_grant(grant_id)

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
