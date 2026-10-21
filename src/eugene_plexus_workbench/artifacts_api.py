"""Artifacts over HTTP (workbench-rich-content-roadmap.md RC5; `artifacts.py`).

**An artifact is its chat's.** Reading one takes a chat the asker may read: their
own, or, read-only, the install owner's view of another's, where an artifact of a
message hidden from the owner does not exist (as its message's files do not,
`api._hidden_file`). Keeping one, or a revision of one, takes the asker's own chat.

Nothing here runs or serves an artifact as a page: its source is JSON, and the
page shows it (web/src/components/Artifact.tsx).
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from . import artifacts
from .api import (
    _answers,
    _own_chat,
    _person,
    _problem,
    _readable_chat,
    _state,
    hidden_from_owner,
    install_mode,
)
from .store import ArtifactLimit, ArtifactRecord, Chat, Revision, StaleRevision, Store

router = APIRouter()

Kind = Literal["markdown", "svg", "html", "react"]


class Keep(BaseModel):
    """Keep a block of an answer as an artifact: the block's words as the answer
    has them (`original`), and the person's edit of them (`source`), if any."""

    kind: Kind
    title: str = Field(default="", max_length=1_000)
    original: str = Field(min_length=1, max_length=artifacts.MAX_SOURCE)
    source: str | None = Field(default=None, max_length=artifacts.MAX_SOURCE)
    #: The revision the edit began from: 1, the answer's block, unless the page knew of
    #: a later one.
    base: int | None = Field(default=None, ge=1)


class Revise(BaseModel):
    source: str = Field(max_length=artifacts.MAX_SOURCE)
    #: The revision the edit began from; a later one saved since refuses it (409).
    base: int | None = Field(default=None, ge=1)


def revision_view(revision: Revision, origin: dict[str, str] | None = None) -> dict[str, Any]:
    view: dict[str, Any] = {
        "number": revision.number,
        "author": revision.author,
        "size": revision.size,
        "createdAt": revision.created_at,
    }
    if revision.source is not None:
        view["source"] = revision.source
    if revision.number == 1 and origin:
        view.update(origin)
    return view


async def _origin(store: Store, record: ArtifactRecord) -> dict[str, str]:
    """Re-find revision one's origin, without a new column or migration."""
    first = await store.revision(record.id, 1)
    message = await store.message(record.message_id)
    if first is None or first.source is None or message is None:
        return {}
    return artifacts.found_in(first.source, message) or {}


async def _view(store: Store, record: ArtifactRecord) -> dict[str, Any]:
    origin = await _origin(store, record)
    return {
        **artifacts.summary(record),
        **origin,
        "messageId": record.message_id,
        "revisionList": [revision_view(r, origin) for r in await store.revisions(record.id)],
    }


def _sized(source: str, what: str) -> int:
    # A lone surrogate never gets here: the request is refused as not its shape
    # (web.invalid_request).
    size = len(source.encode("utf-8"))
    if size > artifacts.MAX_SOURCE:
        raise _problem(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"{what} is {size // 1024} KB; an artifact keeps at most "
            f"{artifacts.MAX_SOURCE // 1024} KB a revision.",
        )
    return size


def _stale(exc: StaleRevision) -> HTTPException:
    return _problem(
        status.HTTP_409_CONFLICT,
        f"Revision {exc.last} was saved after revision {exc.base}, where this edit began. "
        f"The edit is still here: save it again to keep it after revision {exc.last}.",
    )


async def _readable_artifact(
    request: Request, chat_id: str, artifact_id: str
) -> tuple[Chat, ArtifactRecord, bool]:
    person = await _person(request)
    chat, read_only = await _readable_chat(request, person, chat_id)
    store: Store = _state(request).store
    record = await store.artifact(artifact_id)
    if record is None or record.chat_id != chat.id:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such artifact.")
    if read_only:
        tree = await store.tree(chat.id)
        hidden = hidden_from_owner(tree, (await install_mode(request))["mode"])
        if record.message_id in hidden:
            raise _problem(status.HTTP_404_NOT_FOUND, "There is no such artifact.")
    return chat, record, read_only


@router.post("/api/chats/{chat_id}/messages/{message_id}/artifacts")
async def keep_artifact(
    request: Request, chat_id: str, message_id: str, body: Keep, response: Response
) -> dict[str, Any]:
    """The answer's block kept as an artifact, revision 1 as the answer wrote it and
    the person's edit after it; the one already kept for this block, with the edit
    added, when there is one. 201 when it was kept now."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    store: Store = _state(request).store
    message = await store.message(message_id)
    if message is None or message.chat_id != chat.id or message.role != "assistant":
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such answer in this chat.")
    running = _answers(request).running(chat.id)
    if message.status == "running" or (running is not None and running.message.id == message.id):
        raise _problem(
            status.HTTP_409_CONFLICT,
            "This answer is still being written. Save the artifact when it has ended.",
        )
    size = _sized(body.original, "The block")
    edited = body.source if body.source is not None and body.source != body.original else None
    edited_size = _sized(edited, "The edit") if edited is not None else 0
    now = time.time()
    digest = await asyncio.to_thread(artifacts.digest, body.original)
    if artifacts.found_in(body.original, message) is None:
        raise _problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This block is not in the answer or its tools' results",
        )
    try:
        record, made = await store.add_artifact(
            ArtifactRecord(
                id=secrets.token_urlsafe(12),
                owner=chat.owner,
                chat_id=chat.id,
                message_id=message.id,
                digest=digest,
                kind=body.kind,
                title=artifacts.title_of(body.title, body.kind),
                created_at=now,
            ),
            Revision(number=1, author="answer", size=size, created_at=now, source=body.original),
            then=(
                Revision(number=0, author="person", size=edited_size, created_at=now, source=edited)
                if edited is not None
                else None
            ),
            base=body.base or 1,
            per_message=artifacts.MAX_PER_MESSAGE,
            most=artifacts.MAX_REVISIONS,
            person_bytes=artifacts.MAX_PERSON_BYTES,
        )
    except ArtifactLimit as exc:
        raise _problem(status.HTTP_409_CONFLICT, f"{exc}.") from None
    except StaleRevision as exc:
        raise _stale(exc) from None
    kept = await store.artifact(record.id)
    assert kept is not None
    response.status_code = status.HTTP_201_CREATED if made else status.HTTP_200_OK
    return await _view(store, kept)


@router.get("/api/chats/{chat_id}/artifacts/{artifact_id}")
async def get_artifact(request: Request, chat_id: str, artifact_id: str) -> dict[str, Any]:
    _, record, _ = await _readable_artifact(request, chat_id, artifact_id)
    return await _view(_state(request).store, record)


@router.get("/api/chats/{chat_id}/artifacts/{artifact_id}/revisions/{number}")
async def get_revision(
    request: Request, chat_id: str, artifact_id: str, number: int
) -> dict[str, Any]:
    _, record, _ = await _readable_artifact(request, chat_id, artifact_id)
    revision = await _state(request).store.revision(record.id, number)
    if revision is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such revision.")
    origin = await _origin(_state(request).store, record) if number == 1 else None
    return revision_view(revision, origin)


@router.post(
    "/api/chats/{chat_id}/artifacts/{artifact_id}/revisions",
    status_code=status.HTTP_201_CREATED,
)
async def add_revision(
    request: Request, chat_id: str, artifact_id: str, body: Revise
) -> dict[str, Any]:
    """The person's edit kept as the artifact's next revision (the last one, unchanged,
    when it is the same)."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    store: Store = _state(request).store
    record = await store.artifact(artifact_id)
    if record is None or record.chat_id != chat.id:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such artifact.")
    size = _sized(body.source, "The edit")
    try:
        await store.add_revision(
            record.id,
            Revision(
                number=0, author="person", size=size, created_at=time.time(), source=body.source
            ),
            base=body.base,
            most=artifacts.MAX_REVISIONS,
            person_bytes=artifacts.MAX_PERSON_BYTES,
        )
    except ArtifactLimit as exc:
        raise _problem(status.HTTP_409_CONFLICT, f"{exc}.") from None
    except StaleRevision as exc:
        raise _stale(exc) from None
    kept = await store.artifact(record.id)
    assert kept is not None
    return await _view(store, kept)
