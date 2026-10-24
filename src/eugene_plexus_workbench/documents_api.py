"""Documents in a chat: what was read, what a model is given, and the routes (MR2).

The rules are `context.py` (pure); the reading is `knowledge/`; the request is
built in `conversation.py`. This module is the part that reads the store, asks
the gateway about the model, waits for a file to be read, and answers the page.

**A file is the asker's.** Every route takes the person from the session, and a
file that is not theirs is the same 404 as one that does not exist, whether it is
the original (`GET /api/files/{id}`, `api.py`) or its text (`GET /api/files/{id}/text`
here). The text is kept per person beside the original and is read by the same
check, so another person never reads it; the install's owner, where they may read
a person's chats (`ownerReadsChats`), may read what that person attached.

**Sending waits for reading** (`plan_for`): a file is read when it is attached, in
a child process, and a message that carries one does not go until its reading has
ended. What cannot go says so, and stops the send with a 409 that names each file
and what to do (`Blocked`): still being read, no text in it (and why), longer than
is read by default (the person chooses), too long for this model's window (they
choose a part), or no room left. **Nothing is ever cut without being chosen**, and
what was chosen is kept with the file and said under the answer.

**What an answer lists** is what was sent (`context.source_entry`): each document,
by name, with the pages or lines that went and whether it was all of it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from . import config, context, conversation, files, memory_api, token_count
from .answers import Progress, Refused
from .api import _may_read_file, _own_chat, _person, _problem, _state
from .hub import HubError
from .knowledge import extract, whole
from .knowledge import index as index_module
from .knowledge import reader as reading_module
from .knowledge.index import Document, IndexUnavailable, chat_scope
from .store import Chat, FileRecord, Message, Person, Store

log = logging.getLogger(__name__)

router = APIRouter()


def _wait_seconds() -> float:
    """How long a send waits for a file to be read: its reader's own limit, and a margin."""
    return reading_module.TIMEOUT_SECONDS + 20.0


class Blocked(Refused):
    """The documents of a request cannot go as things stand. `detail` is the
    409 a send answers with, or the words an answer fails with."""

    def __init__(self, message: str, listed: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.detail: dict[str, Any] = {"message": message, "code": "documents", "files": listed}


def _is_document(record: FileRecord) -> bool:
    return record.media_type in files.DOCUMENT_TYPES


def kind_of(record: FileRecord) -> str:
    """What a file is to the page: an `image` or `audio` clip, or a `document`."""
    found = files.KINDS.get(record.media_type)
    return found if found in ("image", "audio") else "document"


# --------------------------------------------------------------------------- #
# how things are said
# --------------------------------------------------------------------------- #


def _choice_of(record: FileRecord) -> context.Choice | None:
    return context.Choice.from_json((record.reading or {}).get("choice"))


def _attached(
    path: list[Message],
    records: dict[str, FileRecord],
    searches: dict[str, dict[str, Any]] | None = None,
) -> list[context.Attached]:
    """Every document the path carries, in order. Those on its last message are
    decided now; the rest were attached earlier and are replayed. `searches` is how each
    file the person chose to search instead is getting on (`_search_states`)."""
    out: list[context.Attached] = []
    last = path[-1] if path else None
    for message in path:
        if message.role != "user":
            continue
        for file_id in message.attachments:
            record = records.get(file_id)
            if record is None or not _is_document(record):
                continue
            out.append(
                context.Attached(
                    id=record.id,
                    name=record.name,
                    size=record.size,
                    media_type=record.media_type,
                    current=message is last,
                    reading=record.reading,
                    choice=_choice_of(record),
                    nonce=str((record.reading or {}).get("nonce") or ""),
                    search=(searches or {}).get(record.id),
                )
            )
    return out


def _searched(record: FileRecord) -> bool:
    """Whether the person chose to search this file instead of reading it whole."""
    choice = _choice_of(record)
    return choice is not None and choice.mode == "search"


def _search_view(doc: Document) -> dict[str, Any]:
    return {
        "docId": doc.id,
        "status": doc.status,
        "message": doc.reason,
        "passages": doc.chunks,
    }


async def _search_states(
    request: Request, chat: Chat, records: dict[str, FileRecord]
) -> dict[str, dict[str, Any]]:
    """For each file of the chat the person chose to search instead ("Search it
    instead"): how its own document in their index is getting on, by file id. A file
    with no document (yet) is not in it. An index that cannot be used is said, in each
    such file's state, so a chat never fails to open because of it."""
    wanted = [r for r in records.values() if _searched(r)]
    if not wanted:
        return {}
    index = _state(request).saved.index(chat.owner, create=False)
    if index is None:
        return {}
    try:
        mine = {d.source: d for d in await index.documents(chat_scope(chat.id))}
    except IndexUnavailable as exc:
        return {
            r.id: {"docId": None, "status": "failed", "message": exc.message, "passages": 0}
            for r in wanted
        }
    return {
        r.id: _search_view(mine[f"attachment:{r.id}"])
        for r in wanted
        if f"attachment:{r.id}" in mine
    }


async def _make_searchable(request: Request, chat: Chat, record: FileRecord) -> Document:
    """The document made from a file the person chose to search instead: made if it
    is not there, as it is when asked again."""
    root = _state(request).settings.data_dir
    reading = record.reading or {}
    found = files.Detected(
        media_type=record.media_type,
        kind=str(reading.get("kind") or "text"),
        language=reading.get("language"),
    )
    doc: Document = await _state(request).saved.search_attachment(
        chat.owner,
        chat_id=chat.id,
        file_id=record.id,
        name=record.name,
        detected=found,
        source_path=files.path_of(root, record.owner, record.id),
    )
    return doc


async def _model(request: Request, model_id: str | None) -> tuple[context.Model, dict[str, Any]]:
    """What the gateway says of the model: its window, and whether it takes PDFs
    as files. A model it cannot say anything of is a window nobody knows, which
    documents are read against by default only (`context.plan`)."""
    unknown = {"id": model_id, "known": False, "window": None, "fileInput": False}
    if not model_id:
        return context.Model(None), unknown
    try:
        facts = await memory_api.model_facts(_state(request).hub, model_id)
    except HubError as exc:
        log.warning("documents could not ask about model %s: %s", model_id, exc.message)
        return context.Model(None), unknown
    return (
        context.Model(window=facts.window, file_input=facts.file_input),
        {
            "id": model_id,
            "known": facts.window is not None,
            "window": facts.window,
            "fileInput": facts.file_input,
        },
    )


def _room(
    chat: Chat,
    path: list[Message],
    records: dict[str, FileRecord],
    root: Path,
    model_id: str,
    notes: str | None,
    history: int | None = None,
) -> context.Room:
    """What the request holds before any document: the tokens of its messages
    (the chat's instructions and notes included), the room kept for the answer,
    and the bytes of the images and audio it carries. `history` is the tokens of its
    messages as a long chat's plan has them (row 26: earlier messages summarised)."""
    if history is None:
        lean = conversation.request_for(
            chat, path, list(records.values()), root=root, model=model_id, search=False,
            memory=notes, lean=True,
        )  # fmt: skip
        history = context.count_messages(lean["messages"])
    wanted = chat.settings.get("maxTokens")
    media = sum(
        records[i].size
        for m in path
        for i in m.attachments
        if i in records and files.travels(records[i].media_type)
    )
    return context.Room(
        history=history,
        reserve=int(wanted) if isinstance(wanted, int) and wanted > 0 else context.ANSWER_RESERVE,
        memory=token_count.estimate(notes) if notes else 0,
        media_bytes=media,
    )


def _loader(root: Path, records: dict[str, FileRecord]) -> context.Loader:
    """Read a file's text from beside it, once each. Raises `context.TextMissing`."""
    cache: dict[str, extract.Extracted] = {}

    def load(file_id: str) -> extract.Extracted:
        if file_id not in cache:
            record = records[file_id]
            try:
                data = json.loads(
                    files.read_path_of(root, record.owner, record.id).read_text(encoding="utf-8")
                )
                cache[file_id] = extract.Extracted.from_json(data)
            except (OSError, ValueError) as exc:
                raise context.TextMissing(record.id, record.name) from exc
        return cache[file_id]

    return load


async def plan_for(
    request: Request,
    chat: Chat,
    path: list[Message],
    records: dict[str, FileRecord],
    model_id: str | None,
    *,
    notes: str | None,
    render: bool,
    wait: bool,
    progress: Progress | None = None,
    override: FileRecord | None = None,
    history: int | None = None,
) -> tuple[
    context.Plan, list[context.Attached], context.Model, dict[str, Any], dict[str, FileRecord]
]:
    """Decide the path's documents for this model. With `wait`, a file still being
    read is waited for first (a send does not go before its files are read); the
    records are read again after, so the plan is of what was found."""
    store: Store = _state(request).store
    root = _state(request).settings.data_dir
    settings = await config.document_settings(store)
    model, about = await _model(request, model_id)
    searches = await _search_states(request, chat, records)
    attached = _attached(path, records, searches)
    if wait:
        reader: reading_module.Reader = _state(request).reader
        # A model that takes the PDF as a file does not need its text.
        need = [
            records[a.id]
            for a in attached
            if a.state == "reading"
            and not (a.is_pdf and model.file_input and a.size <= files.LIMITS["file"])
        ]
        if need:
            if progress is not None:
                progress({"stage": "documents", "phase": "reading", "files": len(need)})
            for record in need:
                await reader.start(record)  # one whose task is gone is started again
            await reader.wait([r.id for r in need], _wait_seconds())
            records = {r.id: r for r in await store.files_for_chat(chat.id, uploads=True)}
        # A file chosen to be searched is searched once its own document can be: made
        # if it is gone (derived data heals), and waited for, as a file being read is.
        searches = await _await_searches(request, chat, records, progress)
        attached = _attached(path, records, searches)
    if override is not None:
        records = {**records, override.id: override}
        attached = _attached(path, records, await _search_states(request, chat, records))

    async def decide(now: dict[str, FileRecord], items: list[context.Attached]) -> context.Plan:
        return await asyncio.to_thread(
            context.plan,
            items,
            model=model,
            room=_room(chat, path, now, root, model_id or "", notes, history),
            load=_loader(root, now),
            render=render,
            ceiling=settings.whole_up_to_tokens,
        )

    if (
        wait
        and settings.whole_by_parts != "never"
        and path
        and whole.asked_for_whole(path[-1].content)
    ):
        # This question searches the large attachments in their chat's own scope.
        # The person's saved first-part/whole choice is left intact.
        large = {
            item.id
            for item in attached
            if item.state == "ready"
            and (item.reading or {}).get("tokens", 0)
            > min(settings.whole_up_to_tokens, int((model.window or 24000) * 0.25))
            and not (item.is_pdf and model.file_input and item.size <= files.LIMITS["file"])
        }
        if large:
            records = {
                key: replace(
                    record, reading={**(record.reading or {}), "choice": {"mode": "search"}}
                )
                if key in large
                else record
                for key, record in records.items()
            }
            searches = await _await_searches(request, chat, records, progress)
            attached = _attached(path, records, searches)

    plan = await decide(records, attached)
    if lost := [d.id for d in plan.decisions if d.repair]:
        # Derived data heals: a text that has gone is read again, from the file.
        records = await _read_again(request, chat, lost, wait=wait)
        if override is not None:
            records = {**records, override.id: override}
        attached = _attached(path, records, await _search_states(request, chat, records))
        plan = await decide(records, attached)
        if wait and (still := next((d for d in plan.decisions if d.repair), None)):
            raise context.TextMissing(still.id, still.name)
    return plan, attached, model, about, records


async def _await_searches(
    request: Request, chat: Chat, records: dict[str, FileRecord], progress: Progress | None
) -> dict[str, dict[str, Any]]:
    """Before a send: make the document of each file chosen to be searched if it is
    gone, and wait for it to be read (for at most as long as a file is). The states
    as they are after."""
    wanted = [r for r in records.values() if _searched(r)]
    if not wanted:
        return {}
    waiting: list[str] = []
    try:
        for record in wanted:
            doc = await _make_searchable(request, chat, record)
            if doc.status in index_module.PENDING:
                waiting.append(doc.id)
        if waiting:
            if progress is not None:
                progress({"stage": "documents", "phase": "reading", "files": len(waiting)})
            await _state(request).saved.ingest.wait(chat.owner, waiting, _wait_seconds())
    except IndexUnavailable as exc:
        log.warning("a file chosen to be searched could not be made searchable: %s", exc.message)
    return await _search_states(request, chat, records)


async def _read_again(
    request: Request, chat: Chat, ids: list[str], *, wait: bool
) -> dict[str, FileRecord]:
    """Start reading these files again, for their text has gone from beside them,
    and, when asked, wait until it has ended. The chat's files as they are after."""
    store: Store = _state(request).store
    reader: reading_module.Reader = _state(request).reader
    for file_id in ids:
        log.warning("the text of file %s is gone from beside it; reading it again", file_id)
        again = await store.merge_file_reading(
            file_id, {"state": "reading", "finishedAt": None}, chat_id=chat.id
        )
        if again is not None:
            await reader.start(again)
    if wait:
        await reader.wait(ids, _wait_seconds())
    return {r.id: r for r in await store.files_for_chat(chat.id, uploads=True)}


def _blocked(plan: context.Plan) -> Blocked | None:
    """The refusal a plan comes to, or None when every document can go."""
    if plan.blocked:
        listed = [{"id": d.id, "name": d.name, "decision": d.view()} for d in plan.blocked]
        first = plan.blocked[0]
        text = (
            f"{first.name} is still being read. Try again in a moment."
            if first.kind == "pending"
            else f"{first.name}: {first.message}"
        )
        if len(plan.blocked) > 1:
            more = len(plan.blocked) - 1
            text += (
                " (1 more file needs attention too.)"
                if more == 1
                else f" ({more} more files need attention too.)"
            )
        return Blocked(text, listed)
    if plan.full is not None:
        return Blocked(plan.full, [])
    return None


def _missing(exc: context.TextMissing) -> Blocked:
    return Blocked(
        f"Workbench's copy of the text of {exc.name} is missing or damaged. Remove the file "
        "and attach it again.",
        [],
    )


def _has_documents(path: list[Message], records: dict[str, FileRecord]) -> bool:
    return any(
        (r := records.get(i)) is not None and _is_document(r)
        for m in path
        if m.role == "user"
        for i in m.attachments
    )


# --------------------------------------------------------------------------- #
# the steps a send and an answer take
# --------------------------------------------------------------------------- #


async def check_send(
    request: Request,
    person: Person,
    chat: Chat,
    shown: list[Message],
    content: str,
    attachments: list[str],
    records: dict[str, FileRecord],
    model_id: str,
) -> None:
    """Before a message is stored: wait for its files to be read, and refuse, with
    a 409 that names what to do, if the request they would make cannot go. Nothing
    is kept when it is refused, so the person fixes it and sends again. A chat too long
    for its model is refused here too, documents or not (row 26, `summaries.check`)."""
    prospective = Message(
        id="(next)",
        chat_id=chat.id,
        seq=0,
        role="user",
        status="done",
        created_at=time.time(),
        content=content,
        attachments=list(attachments),
    )
    path = [*shown, prospective]
    from . import summaries

    notes = await memory_api.block_for(request, person, chat, model_id, freeze=False)
    planned = await summaries.check(request, person, chat, path, records, model_id, notes)
    if not _has_documents(path, records):
        return
    blocked: Blocked | None
    try:
        plan, _, _, _, _ = await plan_for(
            request,
            chat,
            path,
            records,
            model_id,
            notes=notes,
            render=False,
            wait=True,
            history=planned.plan.lean,
        )
    except context.TextMissing as exc:
        blocked = _missing(exc)
    else:
        blocked = _blocked(plan)
    if blocked is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=blocked.detail)


@dataclass
class Prepared:
    """What an answer is built from: how each document goes, and the sources the
    answer starts with; and, for the search of the person's saved documents that comes
    next (`saved_api.retrieve`), the tokens the request already holds and whether a
    document goes whole in it."""

    sent: dict[str, conversation.SentDocument]
    sources: list[dict[str, Any]]
    used: int = 0
    sent_whole: bool = False


async def prepare(
    request: Request,
    chat: Chat,
    path: list[Message],
    records: dict[str, FileRecord],
    model_id: str,
    notes: str | None,
    progress: Progress,
    history: int | None = None,
) -> Prepared:
    """The documents of the request an answer is about to make, decided for the
    model it goes to, with their text. Raises `Blocked` (an answer fails with its
    words) when they cannot go: the model, or the person's choices, changed since.
    `history` is the tokens of the request's messages as a long chat's plan has them."""
    if not _has_documents(path, records):
        root = _state(request).settings.data_dir
        return Prepared(
            {}, [], used=_room(chat, path, records, root, model_id, notes, history).history
        )
    try:
        plan, attached, _, _, fresh = await plan_for(
            request,
            chat,
            path,
            records,
            model_id,
            notes=notes,
            render=True,
            wait=True,
            progress=progress,
            history=history,
        )
    except context.TextMissing as exc:
        raise _missing(exc) from exc
    if (blocked := _blocked(plan)) is not None:
        raise blocked
    sent: dict[str, conversation.SentDocument] = {}
    sources: list[dict[str, Any]] = []
    by_id = {a.id: a for a in attached}
    for decision in plan.decisions:
        sent[decision.id] = conversation.SentDocument(
            id=decision.id, text=decision.text, native=decision.native
        )
        if decision.kind == "search":
            continue  # not sent whole: its passages are what is sent, and they are listed
        sources.append(
            context.source_entry(decision, by_id[decision.id], fresh[decision.id].reading)
        )
    return Prepared(
        sent,
        sources,
        used=plan.used,
        sent_whole=any(d.send and d.kind != "search" for d in plan.decisions),
    )


# --------------------------------------------------------------------------- #
# the chat's files, as the composer shows them
# --------------------------------------------------------------------------- #


def _item(
    record: FileRecord,
    *,
    sent: bool,
    decision: context.Decision | None,
    search: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "mediaType": record.media_type,
        "size": record.size,
        "createdAt": record.created_at,
        "kind": kind_of(record),
        "sent": sent,
        "reading": context.reading_view(record),
        "decision": decision.view() if decision is not None else None,
        #: For a file the person chose to search instead: how its own document is
        #: getting on (`docId`, `status`, `message`, `passages`); None otherwise.
        "search": search,
    }


async def files_view(
    request: Request,
    chat: Chat,
    model_id: str | None,
    *,
    override: FileRecord | None = None,
) -> dict[str, Any]:
    """A chat's files: each with how reading it went, and what a request to this
    model would do with it. Files not yet sent are decided together, as the one
    message the person is writing, after everything the chat already carries."""
    store: Store = _state(request).store
    records = sorted(
        await store.files_for_chat(chat.id, uploads=True), key=lambda r: (r.created_at, r.id)
    )
    by_id = {r.id: r for r in records}
    if override is not None:
        by_id[override.id] = override
        records = [by_id[r.id] for r in records]
    tree = await store.tree(chat.id)
    sent_ids = {i for m in tree for i in m.attachments}
    shown = await store.messages(chat.id)
    unsent = [r.id for r in records if r.id not in sent_ids]
    prospective = Message(
        id="(next)",
        chat_id=chat.id,
        seq=0,
        role="user",
        status="done",
        created_at=time.time(),
        attachments=unsent,
    )
    path = [*shown, prospective]
    decisions: dict[str, context.Decision] = {}
    request_view: dict[str, Any] | None = None
    about: dict[str, Any]
    searches = await _search_states(request, chat, by_id)
    if _has_documents(path, by_id):
        person = await _person(request)
        notes = await memory_api.block_for(request, person, chat, model_id or "", freeze=False)
        plan, _, _, about, fresh = await plan_for(
            request,
            chat,
            path,
            by_id,
            model_id,
            notes=notes,
            render=False,
            wait=False,
            override=override,
        )
        records = [fresh.get(r.id, r) for r in records]
        decisions = {d.id: d for d in plan.decisions}
        request_view = {
            "used": plan.used,
            "window": plan.window,
            "reserve": plan.reserve,
            "full": plan.full,
        }
    else:
        _, about = await _model(request, model_id)
    return {
        "model": about,
        "files": [
            _item(r, sent=r.id in sent_ids, decision=decisions.get(r.id), search=searches.get(r.id))
            for r in records
        ],
        "request": request_view,
    }


@router.get("/api/chats/{chat_id}/files")
async def list_files(request: Request, chat_id: str, model: str | None = None) -> dict[str, Any]:
    """The chat's files, each with its reading and the decision for `model` (the
    model the person is about to send to; the chat's own when not said)."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    return await files_view(request, chat, (model or chat.model or None))


class ReadingChoice(BaseModel):
    """How much of a document to read: `whole` ("read it all anyway"), `first`
    with `tokens` (the first part, at most that many), `search` ("search it instead":
    nothing of it is read into the message; its passages are searched for each question,
    and its own document is deleted with the chat), or `null` to clear it."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["whole", "first", "search"] | None = None
    tokens: int | None = Field(default=None, ge=1, le=100_000_000, strict=True)


@router.put("/api/chats/{chat_id}/files/{file_id}/reading")
async def choose_reading(
    request: Request, chat_id: str, file_id: str, body: ReadingChoice, model: str | None = None
) -> dict[str, Any]:
    """Choose how much of a document is read, and keep the choice with the file:
    every later request reads it that way. Refused (409, nothing kept) when the
    file has no text yet, or the choice does not fit `model`; the answer says what
    does. The file is the person's own and in this chat, or it is a 404."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    store: Store = _state(request).store
    record = await store.file(file_id)
    if record is None or record.chat_id != chat.id or record.owner != person.sub:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such file in this chat.")
    if (body.mode == "first") != (body.tokens is not None):
        raise _problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Choose `first` with a number of tokens, or `whole`, or null to clear.",
        )
    reading = record.reading
    if reading is None:
        raise _problem(
            status.HTTP_409_CONFLICT,
            "Workbench does not read this file, so there is nothing to choose.",
        )
    if reading.get("state") == "reading":
        raise _problem(
            status.HTTP_409_CONFLICT, "This file is still being read. Try again in a moment."
        )
    if reading.get("state") != "ready":
        raise _problem(
            status.HTTP_409_CONFLICT,
            str(reading.get("message") or "Workbench could not read this file."),
        )
    choice = (
        None
        if body.mode is None
        else context.Choice("whole")
        if body.mode == "whole"
        else context.Choice("search")
        if body.mode == "search"
        else context.Choice("first", body.tokens)
    )
    stored = choice.to_json() if choice else None
    was_searched = _searched(record)
    if choice is not None and choice.mode == "search":
        if not (await config.document_settings(store)).enabled:
            raise _problem(
                status.HTTP_403_FORBIDDEN, "The owner of this Workbench has turned off documents."
            )
        try:
            # Its own document is made first, so the answer shows it getting ready.
            await _make_searchable(request, chat, record)
        except IndexUnavailable as exc:
            raise _problem(status.HTTP_503_SERVICE_UNAVAILABLE, exc.message) from None
    trial = replace(record, reading={**reading, "choice": stored})
    model_id = model or chat.model or None
    view = await files_view(request, chat, model_id, override=trial)
    item: dict[str, Any] = next(i for i in view["files"] if i["id"] == record.id)
    decision = item["decision"]
    if decision is not None and decision["choiceNoLongerFits"] and view["model"]["known"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": decision["message"],
                "code": "choice",
                "files": [{"id": record.id, "name": record.name, "decision": decision}],
            },
        )
    await store.merge_file_reading(record.id, {"choice": stored}, chat_id=chat.id)
    if was_searched and (choice is None or choice.mode != "search"):
        # Taken back, or another way chosen: the document made from it goes.
        with contextlib.suppress(IndexUnavailable):
            await _state(request).saved.drop_attachment(person.sub, chat.id, record.id)
        item["search"] = None
    return item


@router.get("/api/files/{file_id}/text")
async def read_text(request: Request, file_id: str) -> Response:
    """The text Workbench read from a document, as a model is given it whole (a
    PDF's pages marked): the place to check when an answer misses something that
    is in the file. Read by the same check as the original, so another person's
    file is a 404."""
    person = await _person(request)
    store: Store = _state(request).store
    record = await store.file(file_id)
    if record is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such file.")
    await _may_read_file(request, person, record)
    reading = record.reading
    if reading is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "Workbench did not read this file as text.")
    if reading.get("state") != "ready":
        raise _problem(
            status.HTTP_409_CONFLICT,
            str(reading.get("message") or "This file is still being read."),
        )
    root = _state(request).settings.data_dir

    def read() -> str:
        return extract.render(_loader(root, {record.id: record})(record.id).parts)

    try:
        text = await asyncio.to_thread(read)
    except context.TextMissing:
        raise _problem(
            status.HTTP_404_NOT_FOUND, "The text read from this file is no longer on disk."
        ) from None
    return Response(
        text,
        media_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
