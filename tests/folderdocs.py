"""Helpers for the folder tests (MR7): a granted folder on a real disk, a person's folder as the page
reads it, and waits that wait for what they are about. Real requests to a real Workbench.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .conftest import Browser, World
from .saved import start

TERMINAL = ("ready", "skipped", "failed")


def allow_folders(world: World) -> None:
    """Workbench runs as its own account, which is what folder grants need."""
    world.settings.account_kind = "windows_service" if os.name == "nt" else "systemd"


def grant(
    world: World,
    owner: Browser,
    path: Path,
    *,
    subject: str = "p-ada",
    name: str = "Work notes",
    writable: bool = False,
) -> str:
    """The owner grants a folder to a person: the grant's id."""
    allow_folders(world)
    made = owner.post(
        "/api/folders",
        json={"name": name, "path": str(path), "subject": subject, "writable": writable},
    )
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


def folder_of(person: Browser, grant_id: str) -> dict[str, Any]:
    found = person.get(f"/api/documents/folders/{grant_id}")
    assert found.status_code == 200, found.text
    return dict(found.json())


def folders_of(person: Browser) -> dict[str, Any]:
    found = person.get("/api/documents/folders")
    assert found.status_code == 200, found.text
    return dict(found.json())


def files_of(person: Browser, grant_id: str, **query: Any) -> dict[str, Any]:
    found = person.get(f"/api/documents/folders/{grant_id}/files", params=query)
    assert found.status_code == 200, found.text
    return dict(found.json())


def add_folder(person: Browser, grant_id: str, **more: Any) -> Any:
    return person.post("/api/documents/folders", json={"grantId": grant_id, **more})


def sync_now(person: Browser, grant_id: str, **body: Any) -> Any:
    return person.post(f"/api/documents/folders/{grant_id}/sync", json=body)


def checked_at(person: Browser, grant_id: str) -> float:
    return float(folder_of(person, grant_id)["checkedAt"] or 0.0)


def settle_folder(
    person: Browser,
    grant_id: str,
    *,
    after: float = 0.0,
    seconds: float = 90.0,
    ends: tuple[str, ...] = ("synced",),
) -> dict[str, Any]:
    """The folder once a check that began after `after` (the `checkedAt` it had) has finished
    and every file of it has stopped being read (any end). What it waits for is the check, then
    the reading: not an earlier check's `synced`. A check that ends any way but `ends` is a failed
    wait that says how it ended, never a state that a later assertion has to guess at."""
    end = time.perf_counter() + seconds
    seen: dict[str, Any] = {}
    while time.perf_counter() < end:
        seen = folder_of(person, grant_id)
        by = seen["counts"]["byState"]
        reading = sum(by[s] for s in ("queued", "reading", "searchable", "embedding"))
        if (
            seen["state"] in ("synced", "problem", "unavailable", "withdrawn")
            and (seen["checkedAt"] or 0.0) > after
            and seen["syncing"] is None
            and reading == 0
        ):
            assert seen["state"] in ends, f"the check ended as {seen['state']}: {seen['problem']}"
            return seen
        time.sleep(0.03)
    raise AssertionError(f"the folder was not checked and read in time: {seen}")


def sync_and_settle(person: Browser, grant_id: str, **body: Any) -> dict[str, Any]:
    """Sync now, and the folder once that check is done and its files are read."""
    before = checked_at(person, grant_id)
    made = sync_now(person, grant_id, **body)
    assert made.status_code == 202, made.text
    return settle_folder(person, grant_id, after=before)


def add_and_settle(person: Browser, grant_id: str, **more: Any) -> dict[str, Any]:
    made = add_folder(person, grant_id, **more)
    assert made.status_code == 201, made.text
    return settle_folder(person, grant_id)


def by_path(person: Browser, grant_id: str) -> dict[str, dict[str, Any]]:
    """Every document of a folder, by its path below it."""
    out: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        page = files_of(person, grant_id, offset=offset, limit=500)
        for entry in page["files"]:
            out[entry["path"]] = entry
        offset += len(page["files"])
        if not page["files"] or offset >= page["total"]:
            return out


def write(folder: Path, name: str, content: str | bytes) -> Path:
    """A file in the granted folder, as a person's editor would save it."""
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode("utf-8")
    path.write_bytes(content)
    return path


def junction(link: Path, target: Path) -> None:
    """A directory link: a junction on Windows (which needs no privilege), else a symlink."""
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
        )
    else:
        link.symlink_to(target, target_is_directory=True)


def unlink_directory(link: Path) -> None:
    if os.name == "nt":
        link.rmdir()
    else:
        link.unlink()


def owner_and_ada(world: World) -> tuple[Browser, Browser]:
    return start(world, "operator"), start(world, "p-ada")


def snapshot(folder: Path) -> dict[str, tuple[bytes | None, int]]:
    """Every file and directory below a folder: its bytes (None for a directory) and modified time,
    exactly, for a test that says a check changed nothing in it."""
    seen: dict[str, tuple[bytes | None, int]] = {}
    for found in sorted(folder.rglob("*")):
        stat = found.stat()
        seen[str(found.relative_to(folder))] = (
            found.read_bytes() if found.is_file() else None,
            stat.st_mtime_ns,
        )
    return seen


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target)
