"""The folders a person points their Documents at (MR7, `knowledge/folder_sync.py`).

A person adds a folder the owner granted them (`POST /api/documents/folders`); Workbench reads its
files, read-only, into the person's own index and checks it again every few minutes, when they
choose Sync now, and when they change its rules. The files are documents like any other, found by
every chat that has Documents on, named by their folder and path.

**Everything is the asker's.** The person comes from the session and nothing else: a folder is
looked up by `(person, grant id)`, so **a folder that is not yours is the same 404 as one that does
not exist**, and a grant that is not yours cannot be added (the same 404). The owner adds only the
grants that name the owner: granting a person a folder does not let the owner index it. A person
never sees the host path of a folder (only the owner does, in `GET /api/folders`).

Routes are under `/api/documents/folders`, so this router is included **before**
`saved_api`'s, whose `/api/documents/{doc_id}` would otherwise take the listing for a document.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import config
from .api import _person, _problem, _state
from .folders import visible
from .knowledge import folder_rules
from .knowledge.folder_sync import READS, AlreadyAdded, NoSuchGrant, Run, plural
from .knowledge.index import (
    STATES,
    DeleteFailed,
    Document,
    FolderCounts,
    Index,
    split_source,
)
from .saved_api import _OWNER_OFF, document_view, meaning_row, meaning_status
from .store import FolderRecord, Person

router = APIRouter()

#: What a person is told of a folder on another machine (a Job Site's): not yet.
_NODE_FOLDER = (
    "Folders on other machines (Job Sites) cannot be added to your documents yet. Only a folder "
    "on this machine can."
)
_NO_FOLDER = "There is no such folder."
_UNAVAILABLE = "folders_unavailable"


def _refusal(code: int, message: str, tag: str) -> HTTPException:
    """A refusal the page can act on: the words, and a code."""
    return HTTPException(status_code=code, detail={"message": message, "code": tag})


def _sync(request: Request) -> Any:
    return _state(request).folder_sync


def _index(request: Request, person: Person) -> Index | None:
    index: Index | None = _state(request).saved.index(person.sub, create=False)
    return index


def file_view(doc: Document, meaning: dict[str, Any] | None) -> dict[str, Any]:
    """One file of a folder, as the Documents screen reads a document (`saved_api.document_view`:
    its state, and for one that was skipped or failed, why), with the folder it is in and its path
    below it."""
    view = document_view(doc, meaning)
    split = split_source(doc.source)
    view["folderId"] = split[0] if split else None
    view["path"] = split[1] if split else doc.name
    return view


def describe_when(seconds: float) -> str:
    """How long ago, as a person reads it."""
    if seconds < 45:
        return "just now"
    if seconds < 90:
        return "a minute ago"
    if seconds < 3600:
        return f"{round(seconds / 60)} minutes ago"
    if seconds < 86400 * 1.5:
        return f"{round(seconds / 3600)} hours ago"
    return f"{round(seconds / 86400)} days ago"


# --------------------------------------------------------------------------- #
# how a folder is said
# --------------------------------------------------------------------------- #


def folder_state(record: FolderRecord, run: Run | None, waiting: bool) -> str:
    """`withdrawn` (the owner took the grant away), `unavailable` (the folder is not there, or was
    replaced), `syncing` (a check is under way), `waiting` (it is its turn soon, or it has never
    been checked), `problem` (the last check could not finish) or `synced`."""
    code = (record.problem or {}).get("code")
    if record.status == "withdrawn":
        return "withdrawn"
    if code in ("gone", "replaced"):
        return "unavailable"
    if run is not None:
        return "syncing"
    if waiting:
        return "waiting"
    if record.problem is not None:
        return "problem"
    return "waiting" if record.checked_at is None else "synced"


def count_words(counts: FolderCounts) -> str:
    """What a folder holds, in a line: `412 files: 380 ready, 12 being read, 9 skipped`."""
    by = counts.by_state
    reading = by["queued"] + by["reading"] + by["searchable"] + by["embedding"]
    parts = [
        f"{n:,} {what}"
        for n, what in (
            (by["ready"], "ready"),
            (reading, "being read"),
            (by["skipped"], "skipped"),
            (by["failed"], "failed"),
        )
        if n
    ]
    return f"{plural(counts.files, 'file')}" + (": " + ", ".join(parts) if parts else "")


def headline(
    record: FolderRecord,
    state: str,
    run: Run | None,
    counts: FolderCounts,
    sync_minutes: int,
    now: float,
) -> str:
    """One sentence for the folder's state, from what is true of it now."""
    if state in ("withdrawn", "unavailable") and record.problem is not None:
        return str(record.problem["message"])
    if state == "syncing" and run is not None:
        if run.phase == "reading" and run.total:
            return f"Reading this folder: {run.done:,} of {run.total:,} files."
        return "Looking at this folder."
    if state == "problem" and record.problem is not None:
        ago = (
            f" It was last checked well {describe_when(now - record.synced_at)}."
            if record.synced_at
            else ""
        )
        return f"{record.problem['message']}{ago}"
    if state == "waiting":
        return (
            "Looking at this folder soon, for the first time."
            if record.checked_at is None
            else "This folder will be checked in a moment."
        )
    when = describe_when(now - record.synced_at) if record.synced_at else "just now"
    return (
        f"Checked {when}, and again every {plural(sync_minutes, 'minute')}. {count_words(counts)}."
    )


def last_view(record: FolderRecord) -> dict[str, Any] | None:
    """What the last check did, in numbers; None before one has finished."""
    report = record.report
    if report is None:
        return None
    return {
        "finishedAt": report.get("finishedAt"),
        "seconds": report.get("seconds"),
        "mode": report.get("mode"),
        "added": report.get("added", 0),
        "changed": report.get("changed", 0),
        "moved": report.get("moved", 0),
        "removed": report.get("removed", 0),
        "skipped": report.get("skipped", 0),
        "unchanged": report.get("unchanged", 0),
        "files": report.get("files", 0),
        "directories": report.get("directories", 0),
        "ignored": report.get("ignored") or {"files": 0, "folders": 0},
        "links": report.get("links", 0),
        "unlistedTotal": report.get("unlistedTotal", 0),
    }


def caps_view(
    record: FolderRecord,
    counts: FolderCounts,
    settings: config.FolderSettings,
    install: config.DocumentSettings,
) -> dict[str, Any]:
    """The limits a folder is held to, and how near it is: stated on screen."""
    caps = (record.report or {}).get("caps") or {}
    return {
        "files": {
            "limit": settings.max_files,
            "found": (record.report or {}).get("files", 0),
            "reached": bool(caps.get("files")),
        },
        "bytes": {
            "limit": settings.max_bytes,
            "used": counts.copied,
            "reached": bool(caps.get("bytes")),
        },
        "depth": {"limit": settings.max_depth, "reached": int(caps.get("depth") or 0)},
        "entries": {"reached": bool(caps.get("entries"))},
        "fileBytes": {"limit": install.max_bytes},
    }


async def folder_view(
    request: Request,
    person: Person,
    record: FolderRecord,
    *,
    detail: bool = False,
    grants: list[dict[str, Any]] | None = None,
    install: config.DocumentSettings | None = None,
    settings: config.FolderSettings | None = None,
) -> dict[str, Any]:
    """A folder as the Documents screen reads it. With `detail`, also everything the last check left
    out (what was ignored, not followed or too deep, and what could not be looked into)."""
    store = _state(request).store
    sync = _sync(request)
    install = install or await config.document_settings(store)
    settings = settings or await config.folder_settings(store)
    if grants is None:
        grants = await store.folder_grants()
    index = _index(request, person)
    counts = (
        await index.folder_counts(record.grant_id)
        if index is not None
        else FolderCounts(0, 0, 0, 0, 0, 0, dict.fromkeys(STATES, 0), {})
    )
    run = sync.progress(person.sub, record.grant_id)
    state = folder_state(record, run, sync.waiting(person.sub, record.grant_id))
    now = time.time()
    grant = next((g for g in grants if g["id"] == record.grant_id and visible(g, person)), None)
    interval = settings.sync_minutes * 60
    view: dict[str, Any] = {
        "id": record.grant_id,
        "projectId": record.project_id,
        "name": record.name,
        "state": state,
        "headline": headline(record, state, run, counts, settings.sync_minutes, now),
        "granted": grant is not None and record.status == "active",
        "writable": bool(grant["writable"]) if grant is not None else False,
        "ignore": list(record.ignore),
        "includeHidden": record.include_hidden,
        "useGitignore": record.use_gitignore,
        "addedAt": record.added_at,
        "syncedAt": record.synced_at,
        "checkedAt": record.checked_at,
        "nextAt": (
            record.checked_at + interval
            if state == "synced" and record.checked_at is not None
            else None
        ),
        "syncing": (
            {
                "phase": run.phase,
                "done": run.done,
                "total": run.total,
                "since": run.started,
                "manual": run.manual,
                "deep": run.full,
            }
            if run is not None
            else None
        ),
        "counts": {
            "files": counts.files,
            "byState": dict(counts.by_state),
            "byCode": dict(counts.by_code),
            "passages": counts.passages,
            "embedded": counts.embedded,
            "owed": counts.owed,
            "bytes": counts.bytes,
        },
        "caps": caps_view(record, counts, settings, install),
        "last": last_view(record),
        "problem": record.problem,
    }
    if detail:
        report = record.report or {}
        view["left"] = report.get("left") or []
        view["leftCounts"] = report.get("leftCounts") or {}
        view["unlisted"] = report.get("unlisted") or []
    return view


def limits_view(
    settings: config.FolderSettings, install: config.DocumentSettings
) -> dict[str, Any]:
    """Every limit and the rules that are always in force, for the screen to state."""
    return {
        "syncMinutes": settings.sync_minutes,
        "maxFiles": settings.max_files,
        "maxMB": settings.max_mb,
        "maxBytes": settings.max_bytes,
        "maxDepth": settings.max_depth,
        "maxFileMB": install.max_file_mb,
        "maxFileBytes": install.max_bytes,
        "maxPassages": install.max_passages,
        "maxIgnore": folder_rules.MAX_PATTERNS,
        "maxPattern": folder_rules.MAX_PATTERN,
        "reads": READS,
        "rules": folder_rules.describe(),
    }


# --------------------------------------------------------------------------- #
# the routes
# --------------------------------------------------------------------------- #


async def _folder(request: Request, person: Person, grant_id: str) -> FolderRecord:
    """The asker's folder, or the 404 a folder that does not exist gets."""
    record: FolderRecord | None = await _state(request).store.folder_record(person.sub, grant_id)
    if record is None:
        raise _problem(status.HTTP_404_NOT_FOUND, _NO_FOLDER)
    return record


async def _enabled(request: Request) -> None:
    if not (await config.document_settings(_state(request).store)).enabled:
        raise _problem(status.HTTP_403_FORBIDDEN, _OWNER_OFF)


@router.get("/api/documents/folders")
async def list_folders(request: Request, projectId: str | None = None) -> dict[str, Any]:
    """The asker's folders, each with its state, counts and last check; the granted folders they
    may add (`grants`, never with a path); every limit and the rules always in force (`limits`); and
    whether folders can be used on this Workbench (`available`, and `reason` when not)."""
    person = await _person(request)
    state = _state(request)
    install = await config.document_settings(state.store)
    settings = await config.folder_settings(state.store)
    grants = await state.store.folder_grants()
    records = await state.store.folder_records(person.sub)
    if projectId is not None and await state.store.project(person.sub, projectId) is None:
        raise _problem(404, "There is no such project.")
    reason = state.tools.folders.unavailable()
    added = {r.grant_id for r in records}
    return {
        "available": reason is None,
        "reason": reason,
        "enabled": install.enabled,
        "folders": [
            await folder_view(request, person, r, grants=grants, install=install, settings=settings)
            for r in records
            if r.project_id == projectId
        ],
        "grants": [
            {
                "id": g["id"],
                "name": g["name"],
                "writable": bool(g["writable"]),
                "added": g["id"] in added,
            }
            for g in grants
            if visible(g, person)
        ],
        "limits": limits_view(settings, install),
    }


class FolderAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: One of the granted folders in `GET /api/documents/folders`'s `grants`.
    grantId: str = Field(min_length=1, max_length=256)
    #: Patterns to ignore, written like `.gitignore` lines (see `limits.rules`).
    ignore: list[str] | None = Field(default=None, max_length=folder_rules.MAX_PATTERNS + 1)
    #: Read hidden files and folders too (names with a dot in front).
    includeHidden: StrictBool = False
    #: Respect the folder's own `.gitignore` files.
    useGitignore: StrictBool = True
    projectId: str | None = Field(default=None, max_length=200)


class FolderChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ignore: list[str] | None = Field(default=None, max_length=folder_rules.MAX_PATTERNS + 1)
    includeHidden: StrictBool | None = None
    useGitignore: StrictBool | None = None
    projectId: str | None = Field(default=None, max_length=200)


class SyncNow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Read and hash every file, not only the ones whose size or time moved.
    deep: StrictBool = False


@router.post("/api/documents/folders", status_code=status.HTTP_201_CREATED)
async def add_folder(request: Request, body: FolderAdd) -> dict[str, Any]:
    """Point the asker's Documents at a folder the owner granted them. Its first check starts at
    once: poll `GET /api/documents/folders`. 404 when the grant is not theirs (the same as one
    that does not exist), 400 for a folder on another machine, 409 when it is there already, 422
    for a pattern Workbench cannot use (`message` says which), 503 when this Workbench cannot use
    folders."""
    person = await _person(request)
    await _enabled(request)
    state = _state(request)
    if body.projectId is not None and await state.store.project(person.sub, body.projectId) is None:
        raise _problem(404, "There is no such project.")
    if body.grantId.startswith("node:"):
        raise _problem(status.HTTP_400_BAD_REQUEST, _NODE_FOLDER)
    if reason := state.tools.folders.unavailable():
        raise _refusal(status.HTTP_503_SERVICE_UNAVAILABLE, reason, _UNAVAILABLE)
    try:
        record = await _sync(request).add(
            person,
            body.grantId,
            ignore=body.ignore,
            include_hidden=body.includeHidden,
            use_gitignore=body.useGitignore,
            project_id=body.projectId,
        )
    except NoSuchGrant:
        raise _problem(status.HTTP_404_NOT_FOUND, _NO_FOLDER) from None
    except AlreadyAdded:
        raise _problem(
            status.HTTP_409_CONFLICT, "This folder is already in your documents."
        ) from None
    except folder_rules.BadPattern as exc:
        raise _problem(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    except ValueError as exc:
        raise _problem(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return await folder_view(request, person, record)


@router.get("/api/documents/folders/{grant_id}")
async def get_folder(request: Request, grant_id: str) -> dict[str, Any]:
    """One folder, with what its last check left out: `left` (up to 200 things that were ignored,
    not followed or too deep, each with its path and reason), `leftCounts` (every one, by reason)
    and `unlisted` (directories that could not be looked into, with the cause)."""
    person = await _person(request)
    return await folder_view(request, person, await _folder(request, person, grant_id), detail=True)


@router.patch("/api/documents/folders/{grant_id}")
async def change_folder(request: Request, grant_id: str, body: FolderChange) -> dict[str, Any]:
    """Change the folder's own rules. It is checked again at once, so a file that is ignored now
    leaves the index, and one that is not comes in. 409 for a folder the owner withdrew."""
    person = await _person(request)
    record = await _folder(request, person, grant_id)
    await _enabled(request)
    if record.status != "active":
        raise _problem(status.HTTP_409_CONFLICT, "The owner took away your access to this folder.")
    if "projectId" in body.model_fields_set:
        if (
            body.projectId is not None
            and await _state(request).store.project(person.sub, body.projectId) is None
        ):
            raise _problem(404, "There is no such project.")
        try:
            record = await _sync(request).move(person.sub, grant_id, body.projectId) or record
        except Exception as exc:
            raise _problem(503, f"The folder could not be moved: {exc}") from None
        if not body.model_fields_set.intersection({"ignore", "includeHidden", "useGitignore"}):
            return await folder_view(request, person, record)
    try:
        changed = await _sync(request).update(
            person.sub,
            grant_id,
            ignore=body.ignore,
            include_hidden=body.includeHidden,
            use_gitignore=body.useGitignore,
        )
    except folder_rules.BadPattern as exc:
        raise _problem(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    if changed is None:
        raise _problem(status.HTTP_404_NOT_FOUND, _NO_FOLDER)
    return await folder_view(request, person, changed)


@router.post("/api/documents/folders/{grant_id}/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_folder(
    request: Request, grant_id: str, body: SyncNow | None = None
) -> dict[str, Any]:
    """Sync now: the folder is checked as soon as it is its turn, and every file that was skipped
    for a reason that may have passed, or that failed, is tried again. With `deep`, every file is
    read and hashed, not only the ones whose size or time moved. 202: poll the folder. 409 for a
    folder the owner withdrew."""
    person = await _person(request)
    record = await _folder(request, person, grant_id)
    await _enabled(request)
    if record.status != "active":
        raise _problem(status.HTTP_409_CONFLICT, "The owner took away your access to this folder.")
    _sync(request).request(person.sub, grant_id, manual=True, full=bool(body and body.deep))
    return await folder_view(request, person, record)


@router.delete("/api/documents/folders/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_folder(request: Request, grant_id: str) -> Response:
    """Take the folder out of the asker's documents: every document read from it leaves the index
    (and anything reading them is ended), in the same words as when the owner withdraws the grant.
    The folder, its files and the grant are never touched. Also dismisses a folder that was
    withdrawn. If a file of its documents cannot be removed, nothing is (500, with the cause)."""
    person = await _person(request)
    await _folder(request, person, grant_id)
    try:
        removed = await _sync(request).remove(person.sub, grant_id)
    except DeleteFailed as exc:
        raise _problem(status.HTTP_500_INTERNAL_SERVER_ERROR, exc.message) from None
    if removed is None:
        raise _problem(status.HTTP_404_NOT_FOUND, _NO_FOLDER)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/documents/folders/{grant_id}/files")
async def folder_files(
    request: Request,
    grant_id: str,
    state: str | None = None,
    code: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    """A page of a folder's documents in path order, each with its `path`, state and, for one that
    was skipped or failed, why (`message`, `code`); `total` is how many fit the filters (`state`,
    `code` for the reason, `q` for a word in the path) and `counts` how many the folder holds, by
    state and by reason."""
    person = await _person(request)
    await _folder(request, person, grant_id)
    if state is not None and state not in STATES:
        raise _problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"A state is one of {', '.join(STATES)}."
        )
    index = _index(request, person)
    documents: list[Document] = []
    total = 0
    if index is None:
        counts = FolderCounts(0, 0, 0, 0, 0, 0, dict.fromkeys(STATES, 0), {})
    else:
        documents, total = await index.folder_documents(
            grant_id, state=state, code=code, match=q, offset=offset, limit=limit
        )
        counts = await index.folder_counts(grant_id)
    status_ = await meaning_status(request, person)
    saved = _state(request).saved
    return {
        "folderId": grant_id,
        "files": [file_view(d, meaning_row(saved, d, status_)) for d in documents],
        "total": total,
        "offset": offset,
        "limit": limit,
        "counts": {
            "files": counts.files,
            "byState": dict(counts.by_state),
            "byCode": dict(counts.by_code),
            "passages": counts.passages,
            "embedded": counts.embedded,
            "owed": counts.owed,
            "bytes": counts.bytes,
        },
    }
