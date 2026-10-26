"""Downloading a person's data as one ZIP file (row 7, `specs/docs/design/rows/row-07.md`).

**Everyone can download their own data, at any time** (`POST /api/me/export`): their chats
(each as Markdown, as shown, and every version of every message in `chats.json`), their files,
media and artifacts, today's memory export (`memory.json`, `memory.md`, and what their own
memory server lists, `memory-server.json`), the documents they kept, their skills (row 32, each
as its folder), their projects, and their connections to other apps (never a token).

**The owner can download another person's data** (`POST /api/people/{sub}/export`):

- of a person who uses Workbench now, only while `ownerReadsChats` is on, and then only their
  chats, files, media and artifacts: never their notes or documents (W4; the memory design's
  4.9: nobody reads another person's notes);
- of a person removed from Eugene, always (Troy, R7-OWNER-EXPORT, Choice 2: legal requests),
  and then everything Workbench holds of them but what their own memory server lists, which
  Workbench does not ask.

**Every download is logged**: who asked, for whom, when, and what it held (the `exports` table,
kept after the file goes, and a line in the log). A file is built in the background, kept for
`EXPORT_SECONDS`, and downloaded only by the person who asked for it. A removed person's
downloads, theirs and of them, are deleted with their data (`Exports.drop`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse

from . import config, files
from .knowledge.index import INDEX_FILE, PRIVATE
from .store import Chat, Message, Person, Store, path

log = logging.getLogger(__name__)

router = APIRouter()

#: How long a download is kept.
EXPORT_SECONDS = 24 * 3600.0
#: A person's own download, and the owner's of another person.
SELF, OWNER = "self", "owner"
#: What a download a stop cut off says, at the next start.
INTERRUPTED = "The download was cut off when Workbench stopped. Make it again."


class _Dropped(Exception):
    """A download was deleted while it was being written: nothing of it is kept."""


def _stamp(moment: float | None) -> str | None:
    if moment is None:
        return None
    return datetime.fromtimestamp(moment, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe(name: str, fallback: str = "file") -> str:
    """A name a ZIP and every system unpacking it can hold: one path part, no control
    characters, no reserved characters, at most 80 characters."""
    text = unicodedata.normalize("NFC", name)
    text = "".join(c for c in text if unicodedata.category(c)[0] != "C")
    text = re.sub(r'[\\/:*?"<>|]+', "_", text).strip(" .")
    return text[:80].rstrip(" .") or fallback


def _message(m: Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "parentId": m.parent_id,
        "chosen": m.chosen,
        "role": m.role,
        "status": m.status,
        "model": m.model,
        "content": m.content,
        "reasoning": m.reasoning,
        "attachments": m.attachments,
        "sources": m.sources,
        "toolRounds": m.tool_rounds,
        "error": m.error,
        "createdAt": _stamp(m.created_at),
        "finishedAt": _stamp(m.finished_at),
    }


def _chat_markdown(chat: Chat, tree: list[Message], names: dict[str, str]) -> str:
    """A chat as it is shown: its chosen path, each message with who said it."""
    lines = [f"# {chat.title}", "", f"Started {_stamp(chat.created_at)}.", ""]
    for m in path(tree):
        who = "You" if m.role == "user" else (m.model or "The model")
        lines += [f"## {who}", ""]
        if m.content:
            lines += [m.content, ""]
        if m.error:
            lines += [f"*{m.error}*", ""]
        attached = [names.get(a, a) for a in m.attachments]
        if attached:
            lines += ["Attached: " + ", ".join(attached), ""]
    return "\n".join(lines)


def _documents(index_dir: Path) -> list[tuple[str, str, str]]:
    """The documents a person kept, read from their index without opening it for writing:
    `(id, scope, name)` of each that is a document (not a note or a past chat's exchange)."""
    found = index_dir / INDEX_FILE
    if not found.is_file():
        return []
    db = sqlite3.connect(f"{found.as_uri()}?mode=ro", uri=True, timeout=5.0)
    try:
        marks = ", ".join("?" for _ in PRIVATE)
        return [
            (str(r[0]), str(r[1]), str(r[2]))
            for r in db.execute(
                f"SELECT id, scope, name FROM documents WHERE scope NOT IN ({marks}) "
                "ORDER BY added_at",
                PRIVATE,
            )
        ]
    finally:
        db.close()


class Exports:
    """Downloads being made and kept, one per request. One per Workbench."""

    def __init__(self, store: Store, data_dir: Path, state: Any) -> None:
        self.store = store
        self.data_dir = data_dir
        self.dir = data_dir / "exports"
        self.state = state
        self._tasks: dict[str, asyncio.Task[None]] = {}
        #: Each download's writer: told to stop, and its thread, which runs on after the task
        #: that waits for it is cancelled (`drop` and `aclose` wait for it).
        self._writers: dict[str, tuple[threading.Event, asyncio.Future[Any]]] = {}

    async def resume(self) -> int:
        """Start: a download a stop cut off fails, saying so, and its half-written file goes, so
        the person can make it again at once. How many."""
        cut = await self.store.fail_unfinished_exports(INTERRUPTED)
        for export_id in cut:
            with contextlib.suppress(OSError, ValueError):
                self.path_of(export_id).with_suffix(".part").unlink(missing_ok=True)
        return len(cut)

    def path_of(self, export_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", export_id):
            raise ValueError("not a download's id")
        return self.dir / f"{export_id}.zip"

    async def request(
        self, asker: Person, subject: str, subject_name: str, scope: str, *, full: bool
    ) -> dict[str, Any] | None:
        """Start making a download, log it, and say where it stands. None, and nothing begun,
        when the subject's data is being deleted (checked where the download is recorded)."""
        now = time.time()
        row: dict[str, Any] = {
            "id": secrets.token_urlsafe(18),
            "asker": asker.sub,
            "asker_name": asker.name,
            "subject": subject,
            "subject_name": subject_name,
            "scope": scope,
            "created_at": now,
            "expires_at": now + EXPORT_SECONDS,
        }
        if not await self.store.add_export(row):
            return None
        log.info(
            "%s asked for a download of %s data (%s)",
            asker.name,
            "their own" if scope == SELF else f"{subject_name}'s",
            "everything" if full else "chats, files and media",
        )
        task = asyncio.create_task(self._make(row, full=full), name=f"export-{row['id']}")
        self._tasks[row["id"]] = task
        task.add_done_callback(lambda _: self._tasks.pop(row["id"], None))
        made = await self.store.export_row(row["id"])
        assert made is not None
        return made

    async def _make(self, row: dict[str, Any], *, full: bool) -> None:
        try:
            holds, size = await self._build(row, full=full)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            cause = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
            log.warning("a download could not be made: %s", type(exc).__name__)
            with contextlib.suppress(OSError, ValueError):
                self.path_of(row["id"]).with_suffix(".part").unlink(missing_ok=True)
            await self.store.put_export(
                row["id"], state="failed", error=f"The download could not be made: {cause}."
            )
            return
        await self.store.put_export(row["id"], state="ready", holds=holds, size=size)

    async def _build(self, row: dict[str, Any], *, full: bool) -> tuple[dict[str, int], int]:
        sub, name = row["subject"], row["subject_name"]
        snapshot = await self.store.export_snapshot(sub)
        memory: tuple[dict[str, Any], str] | None = None
        #: Row 32: each of their own skills, as its folder (never one Eugene gives everyone).
        skills: list[tuple[str, Path]] = []
        if full:
            from .memory_api import memory_export

            memory = await memory_export(self.state, sub, name, server_too=row["scope"] == SELF)
            keeper = getattr(self.state, "skills", None)
            if keeper is not None:
                skills = await keeper.exported(sub)
        root, target = self.data_dir, self.path_of(row["id"])
        abort = threading.Event()

        def write() -> tuple[dict[str, int], int]:
            self.dir.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(".part")
            holds = {
                "chats": 0,
                "files": 0,
                "media": 0,
                "artifacts": 0,
                "notes": 0,
                "documents": 0,
                "skills": 0,
            }
            used: set[str] = set()

            def unique(wanted: str) -> str:
                stem, dot, ext = wanted.rpartition(".")
                candidate, n = wanted, 1
                while candidate in used:
                    n += 1
                    candidate = f"{stem} ({n}).{ext}" if dot else f"{wanted} ({n})"
                used.add(candidate)
                return candidate

            records = snapshot["files"]
            names = {r.id: r.name for r in records}
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                chats = []
                for number, chat in enumerate(snapshot["chats"], start=1):
                    tree = snapshot["trees"].get(chat.id, [])
                    archive.writestr(
                        unique(f"chats/{number:03d} {_safe(chat.title, 'chat')}.md"),
                        _chat_markdown(chat, tree, names),
                    )
                    chats.append(
                        {
                            "id": chat.id,
                            "title": chat.title,
                            "model": chat.model,
                            "createdAt": _stamp(chat.created_at),
                            "updatedAt": _stamp(chat.updated_at),
                            "messages": [_message(m) for m in tree],
                        }
                    )
                holds["chats"] = len(chats)
                archive.writestr("chats.json", json.dumps({"chats": chats}, indent=2))
                for record in records:
                    stored = files.path_of(root, sub, record.id)
                    if not stored.is_file():
                        continue
                    place = "media" if record.media_id else "files"
                    archive.write(stored, unique(f"{place}/{_safe(record.name)}"))
                    holds["media" if record.media_id else "files"] += 1
                archive.writestr(
                    "media.json",
                    json.dumps(
                        {
                            "media": [
                                {
                                    "id": m.id,
                                    "screen": m.door,
                                    "kind": m.kind,
                                    "model": m.model,
                                    "status": m.status,
                                    "request": m.request,
                                    "text": m.text,
                                    "createdAt": _stamp(m.created_at),
                                    "files": [r.name for r in records if r.media_id == m.id],
                                }
                                for m in snapshot["media"]
                            ]
                        },
                        indent=2,
                    ),
                )
                artifacts = [
                    {
                        "title": a.title,
                        "kind": a.kind,
                        "chatId": a.chat_id,
                        "messageId": a.message_id,
                        "createdAt": _stamp(a.created_at),
                        "revisions": [
                            {**r, "created_at": _stamp(r["created_at"])} for r in revisions
                        ],
                    }
                    for a, revisions in snapshot["artifacts"]
                ]
                holds["artifacts"] = len(artifacts)
                archive.writestr("artifacts.json", json.dumps({"artifacts": artifacts}, indent=2))
                if full:
                    archive.writestr(
                        "projects.json",
                        json.dumps(
                            {
                                "projects": [
                                    {"name": p.name, "createdAt": _stamp(p.created_at)}
                                    for p in snapshot["projects"]
                                ]
                            },
                            indent=2,
                        ),
                    )
                    archive.writestr(
                        "connections.json",
                        json.dumps({"connections": snapshot["connections"]}, indent=2),
                    )
                if memory is not None:
                    document, markdown = memory
                    server = document.pop("memoryServer", None)
                    holds["notes"] = len(document.get("notes") or [])
                    archive.writestr("memory.json", json.dumps(document, indent=2))
                    archive.writestr("memory.md", markdown)
                    if server is not None:
                        archive.writestr("memory-server.json", json.dumps(server, indent=2))
                if full:
                    index_dir = root / "knowledge" / files.person_key(sub)
                    for doc_id, scope, doc_name in _documents(index_dir):
                        original = index_dir / "originals" / doc_id
                        if not original.is_file():
                            continue
                        where = scope.split(":", 1)[0]
                        archive.write(original, unique(f"documents/{where}/{_safe(doc_name)}"))
                        holds["documents"] += 1
                for skill_name, folder in skills:
                    # A skill's name is lowercase letters, digits and hyphens: a safe folder.
                    kept = [p for p in sorted(folder.rglob("*")) if p.is_file()]
                    for found in kept:
                        if found.is_symlink():
                            continue
                        inside = found.relative_to(folder).as_posix()
                        archive.write(found, f"skills/{skill_name}/{inside}")
                    holds["skills"] += bool(kept)
                archive.writestr("README.txt", _readme(row, holds, full))
            size = partial.stat().st_size
            if abort.is_set():  # deleted while it was written: nothing of it is kept
                partial.unlink(missing_ok=True)
                raise _Dropped
            os.replace(partial, target)
            return holds, size

        export_id = row["id"]
        writer: asyncio.Future[Any] = asyncio.ensure_future(asyncio.to_thread(write))
        self._writers[export_id] = (abort, writer)
        writer.add_done_callback(lambda _: self._writers.pop(export_id, None))
        result: tuple[dict[str, int], int] = await asyncio.shield(writer)
        return result

    async def drop(self, sub: str, *, before: float | None = None) -> int:
        """Delete every download that may hold the person's data, theirs or of them, and stop
        any being made. Their log rows stay, marked deleted. How many files went."""
        gone = 0
        for row in await self.store.exports_holding(sub):
            if before is not None and row["created_at"] > before:
                continue
            writing = self._writers.get(row["id"])
            if writing is not None:
                writing[0].set()
            task = self._tasks.pop(row["id"], None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if writing is not None:
                # Its thread runs on after the task is cancelled: no file is removed, or the
                # row marked deleted, until it has ended (and kept nothing).
                await asyncio.gather(writing[1], return_exceptions=True)
            target = self.path_of(row["id"])
            for candidate in (target, target.with_suffix(".part")):
                if candidate.exists():
                    await asyncio.to_thread(candidate.unlink)
                    gone += 1
            await self.store.put_export(row["id"], state="deleted")
        return gone

    async def sweep(self, now: float | None = None) -> int:
        """Delete the files of downloads kept past their day; their log rows stay."""
        now = time.time() if now is None else now
        gone = 0
        for row in await self.store.exports_due(now):
            if row["id"] in self._tasks:
                continue
            target = self.path_of(row["id"])
            for candidate in (target, target.with_suffix(".part")):
                with contextlib.suppress(FileNotFoundError):
                    await asyncio.to_thread(candidate.unlink)
                    gone += 1
            await self.store.put_export(row["id"], state="expired")
        return gone

    async def aclose(self) -> None:
        writers = list(self._writers.values())
        for abort, _ in writers:
            abort.set()
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, *(w for _, w in writers), return_exceptions=True)


def _readme(row: dict[str, Any], holds: dict[str, int], full: bool) -> str:
    whose = (
        "your own data"
        if row["scope"] == SELF
        else f"the data of {row['subject_name']}, downloaded by {row['asker_name']}"
    )
    lines = [
        f"Workbench download: {whose}.",
        f"Made {_stamp(row['created_at'])}. Kept by Workbench until {_stamp(row['expires_at'])}.",
        "",
        "chats/          each chat as it was shown, in Markdown",
        "chats.json      every chat, with every version of every message",
        "files/          files attached to chats, and files tools returned",
        "media/          images, speech, transcripts and video",
        "media.json      what each media request asked for",
        "artifacts.json  artifacts, with every revision",
    ]
    if full:
        lines += [
            "memory.json     notes, earlier versions and tags (memory.md to read)",
            "documents/      the documents kept, as they were added",
            "skills/         each skill, as its folder with its SKILL.md",
            "projects.json   projects",
            "connections.json  connections to other apps (no tokens)",
        ]
        if row["scope"] == SELF:
            lines.append("memory-server.json  what your own memory server lists, if you have one")
    else:
        lines += [
            "",
            "Notes and documents are not in a download the owner makes of a person who uses "
            "Workbench: nobody reads another person's notes.",
        ]
    lines += ["", "Counts: " + ", ".join(f"{k} {v}" for k, v in holds.items()), ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the routes
# --------------------------------------------------------------------------- #


def _problem(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"message": message})


def view(row: dict[str, Any]) -> dict[str, Any]:
    """A download as the page shows it: never a path."""
    return {
        "id": row["id"],
        "subject": row["subject"],
        "subjectName": row["subject_name"],
        "askerName": row["asker_name"],
        "scope": row["scope"],
        "state": row["state"],
        "holds": row["holds"],
        "size": row["size"],
        "error": row["error"],
        "createdAt": row["created_at"],
        "expiresAt": row["expires_at"],
    }


async def _person(request: Request) -> Person:
    person: Person = await request.app.state.sessions.person(request)
    return person


@router.post("/api/me/export", status_code=status.HTTP_202_ACCEPTED)
async def export_mine(request: Request) -> dict[str, Any]:
    """Make a download of everything Workbench holds of the asker."""
    person = await _person(request)
    exports: Exports = request.app.state.exports
    row = await exports.request(person, person.sub, person.name, SELF, full=True)
    if row is None:
        raise _problem(status.HTTP_410_GONE, "Your data in this Workbench is being deleted.")
    return {"export": view(row)}


@router.post("/api/people/{sub}/export", status_code=status.HTTP_202_ACCEPTED)
async def export_person(request: Request, sub: str) -> dict[str, Any]:
    """The owner's download of another person's data (see the module)."""
    person = await _person(request)
    store: Store = request.app.state.store
    if not person.is_owner:
        raise _problem(status.HTTP_403_FORBIDDEN, "Only the owner of this install does this.")
    if sub == person.sub:
        raise _problem(status.HTTP_400_BAD_REQUEST, "Download your own data from Settings.")
    removed = await store.removed_person(sub)
    if removed is not None and removed["state"] in ("purging", "purged"):
        raise _problem(status.HTTP_410_GONE, f"{removed['name']}'s data is deleted.")
    if removed is not None:
        name, full = str(removed["name"]), True
    else:
        found = await store.person(sub)
        if found is None:
            raise _problem(status.HTTP_404_NOT_FOUND, "Nobody by that id uses Workbench.")
        if not await config.owner_reads_chats(store):
            raise _problem(
                status.HTTP_403_FORBIDDEN,
                "People's chats are their own on this Workbench. The owner can change that in "
                "Workbench's settings on Eugene's console.",
            )
        name, full = found.name, False
    exports: Exports = request.app.state.exports
    row = await exports.request(person, sub, name, OWNER, full=full)
    if row is None:
        raise _problem(status.HTTP_410_GONE, f"{name}'s data is being deleted.")
    return {"export": view(row)}


@router.get("/api/exports")
async def list_exports(request: Request) -> dict[str, Any]:
    """The downloads the asker asked for, newest first."""
    person = await _person(request)
    store: Store = request.app.state.store
    return {"exports": [view(r) for r in await store.exports_by(person.sub)]}


@router.get("/api/exports/{export_id}")
async def download(request: Request, export_id: str) -> FileResponse:
    """A download, to the person who asked for it and nobody else."""
    person = await _person(request)
    store: Store = request.app.state.store
    exports: Exports = request.app.state.exports
    row = await store.export_row(export_id)
    if row is None or row["asker"] != person.sub:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such download.")
    if row["state"] != "ready" or row["expires_at"] <= time.time():
        raise _problem(
            status.HTTP_404_NOT_FOUND,
            "This download is not kept any more. Make a new one."
            if row["state"] in ("expired", "deleted") or row["expires_at"] <= time.time()
            else "This download is not ready.",
        )
    target = exports.path_of(export_id)
    if not target.is_file():
        raise _problem(status.HTTP_404_NOT_FOUND, "This download is not kept any more.")
    whose = "my" if row["scope"] == SELF else _safe(row["subject_name"], "person")
    day = datetime.fromtimestamp(row["created_at"], UTC).strftime("%Y-%m-%d")
    return FileResponse(
        target,
        media_type="application/zip",
        filename=f"workbench-{whose}-data-{day}.zip",
        headers={"Cache-Control": "private, no-store"},
    )
