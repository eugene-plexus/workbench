"""Saved documents: My documents, the chat's Documents switch, and what an answer
is sent from them (MR4).

A person keeps documents; Workbench reads and indexes them in the background
(`knowledge/ingest.py`); a chat with **Documents** turned on is sent the passages
that best match what the person just asked, cited by number, and the answer lists
each passage's file and page under it. The rules that can be unit-tested are
elsewhere (`knowledge/` finds, `context.py` budgets and renders); this module is
the part that knows the session, the store and the gateway.

**Everything is the asker's.** Every route takes the person from the session and
nothing from the client: the index is `knowledge/<their key>/index.sqlite3`, a file of
their own that no query can leave, so **a document that is not yours is the same 404 as
one that does not exist** (W4). `ownerReadsChats` does not reach saved documents: no
route here reads another person's, and in the owner's read-only view of a chat the
passages show as "a document", with no name and no words (`api.redact_for_owner`).

**The switch is a choice, and off by default.** A new chat has Documents off unless the
person has said otherwise for new chats (`PUT /api/documents/prefs`), because a switch
that put unrelated passages in every chat would be a surprise; the chat's chip says
what its next request does and why (`GET /api/chats/{id}/documents`), from the same
decision the request makes. Files attached to one chat that the person chose to
**search instead** are searched in that chat whatever the switch says: they are the
person's own act, for that chat, and die with it.

**Passages go to a model that runs on the person's own machines.** For one that runs
outside them, or that Eugene cannot place, the person's saved documents are held back
unless the person has said otherwise (`ask` by default: "Send for this chat"; the
owner's `never` is final), as notes are. A file attached to the chat is their own act
and is not held back.

**Search by meaning** (MR5, `knowledge/meaning.py`) is the second way a passage is found,
beside its words. The Documents screen's header says which is in effect from a live look
(`search`: `Searching by meaning and words.`, `Searching by words only. No search model is
running.`, or `Search by meaning is paused: ...` with Rebuild), each document says how much of its
meaning is added (`meaning`), and each passage found says how it was found (`via`: `words`,
`meaning` or `both`). The words search never depends on it: when meaning cannot run, the answer
says it was searched by words only (`retrieval.meaning`), and never that nothing matched.

**A result sorter** (MR6b, `knowledge/sorting.py`) is the step after the two ways of searching: the
best passages of each are sent to it, and it puts them in the order that answers the question best,
when one is running on the install's own machines and is quick enough (Sort time). The mode says
so (`meaning and words, sorted by Qwen3-Reranker`), each passage's `ranks` says where the sorter
put it, and `sort` says why when it did not, with the numbers. A sorter that cannot run never fails
a search: the passages are in the order they were found. Under an answer, Sources are unchanged.

**A search that cannot run is never "nothing matched".** What searching came to is kept
with the answer (`Message.retrieval`): `used`, `nothing_matched` (a normal result),
`skipped` (and why: the window, the room, the model, no words) or `failed` (and the
cause as it was observed), and the answer says which. A chat set to answer **only from
the person's documents** does not ask a model when there is nothing to answer from:
the answer is Workbench's own words saying so, with a way to ask the model anyway.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import config, context, files, memory_api
from .answers import Progress
from .api import _own_chat, _person, _problem, _save_upload, _state
from .hub import HubError
from .knowledge import extract
from .knowledge import index as index_module
from .knowledge import reader as reading_module
from .knowledge.embedders import Identity
from .knowledge.folder_sync import IgnoreListFull
from .knowledge.index import (
    FOUND,
    LIBRARY,
    PENDING,
    DeleteFailed,
    Document,
    Index,
    IndexUnavailable,
    chat_scope,
    valid_id,
)
from .knowledge.meaning import Queue, Status
from .knowledge.saved import OriginalMissing, SavedDocuments, hash_file, new_id
from .knowledge.search import Retrieval, Retrieved, plan_query
from .knowledge.sorting import Standing
from .store import Chat, Message, Person, Store

log = logging.getLogger(__name__)

router = APIRouter()

_OWNER_OFF = "The owner of this Workbench has turned off documents."
_NO_DOCUMENTS = "You have no documents to search yet. Add some in Documents, then ask again."
#: At most this many files in one request.
MAX_FILES = 200
#: The questions a person may test, and the ids one passage lookup may name.
MAX_QUESTION = 2_000
MAX_IDS = 20


def _saved(request: Request) -> SavedDocuments:
    saved: SavedDocuments = _state(request).saved
    return saved


def _store(request: Request) -> Store:
    store: Store = _state(request).store
    return store


async def index_unavailable(request: Request, exc: Exception) -> JSONResponse:
    """The exception handler for an index that cannot be used: the words, and a
    code the page can act on. A search that could not run is never an empty result."""
    assert isinstance(exc, IndexUnavailable)
    return JSONResponse(
        {
            "detail": {
                "message": exc.message,
                "code": exc.code,
                "canRebuild": exc.code == "index_damaged",
            }
        },
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


# --------------------------------------------------------------------------- #
# how things are said
# --------------------------------------------------------------------------- #


def document_view(doc: Document, meaning: dict[str, Any] | None = None) -> dict[str, Any]:
    """A document as the Documents screen reads it. `state` is `queued`, `reading`,
    `searchable` (it can be found by words, and its meaning is waiting to be added),
    `embedding` (its meaning is being added), `ready`, `skipped` or `failed`; for the last
    two `message` says why, in words a person reads. `meaning` is how much of its meaning
    is added, and what is true of it now (`Meaning.document_view`); null until it is read."""
    return {
        "id": doc.id,
        "name": doc.name,
        "mediaType": doc.media_type,
        "kind": doc.kind,
        "label": extract.LABELS.get(doc.kind),
        "language": doc.language,
        "scope": "chat" if doc.scope.startswith(index_module.CHAT) else "library",
        "size": doc.bytes,
        "pages": doc.pages,
        "lines": doc.lines,
        "tokens": doc.tokens_est,
        "state": doc.status,
        "searchable": doc.searchable,
        "message": doc.reason,
        "code": doc.code,
        "passages": doc.chunks,
        "embedded": doc.embedded,
        "notes": doc.notes,
        "addedAt": doc.added_at,
        "readAt": doc.read_at,
        "indexedAt": doc.indexed_at,
        "meaning": meaning,
    }


async def meaning_status(request: Request, person: Person) -> Status | None:
    """How this person's documents are searched now, from a live look; None when this
    Workbench has no gateway to ask about a search model (words only)."""
    saved = _saved(request)
    if saved.meaning is None:
        return None
    return await saved.meaning.status(saved.index(person.sub, create=False), person.sub)


async def sort_standing(request: Request) -> Standing | None:
    """How passages are sorted now, from a live look (`Sorting.standing`); None when this
    Workbench has no gateway to ask about a sorter. A sorter that cannot be looked at never
    stops a screen: it is a Standing that says why."""
    sorting = _saved(request).sorting
    if sorting is None:
        return None
    try:
        return await sorting.standing()
    except Exception:  # a screen never fails because of a sorter
        log.warning("looking at the result sorter failed in Workbench itself", exc_info=True)
        return None


def search_facts(status: Status | None, sort: Standing | None = None) -> dict[str, Any]:
    """How documents are searched right now, for the Documents screen's header: the mode in
    effect (`meaning and words` or `words`, and `, sorted by <the sorter>` after it while a
    result sorter is putting the best passages in order), the sentence that says it, why when it
    is not both, what the person can do (`actions`: `rebuild`, `get_search_model`), which search
    model and whether its prefixes are known, and how adding meaning is going (`adding`). `sort`
    is the sorter: which one (`setting`, `model`), how fast it reads (`ratePerSecond`), the
    time allowed (`budgetSeconds`), and whether a sort of the usual size is quick enough
    (`sorting`) or why not (`code`, `message`, with the numbers). Said from a live look, never from
    what was true when the last document was added."""
    if status is None:
        facts: dict[str, Any] = {
            "mode": "words",
            "words": True,
            "meaning": False,
            "headline": "Searching by words only.",
            "code": "no_gateway",
            "detail": None,
            "actions": [],
            "model": None,
            "adding": None,
            "rebuild": None,
        }
    else:
        facts = status.view()
    facts["sort"] = sort.view() if sort is not None else None
    if sort is not None and sort.sorting:
        facts["mode"] = f"{facts['mode']}, sorted by {sort.model}"
    return facts


def meaning_row(
    saved: SavedDocuments, doc: Document, status: Status | None
) -> dict[str, Any] | None:
    if saved.meaning is None:
        return None
    return saved.meaning.document_view(doc, status.queue if status else Queue(), status)


def prefs_view(prefs: dict[str, Any], install: config.DocumentSettings) -> dict[str, Any]:
    """What a person has set, what is in effect, and whose word that is. A setting the
    person has not made is `null`, and `effective` says what that comes to."""
    external, set_by = memory_api.resolve_external(install.external, prefs["external"])
    return {
        "newChats": prefs["new_chats"],
        "external": prefs["external"],
        "effective": {
            "enabled": install.enabled,
            "disabledBy": None if install.enabled else "owner",
            "newChats": prefs["new_chats"] and install.enabled,
            "external": external,
            "externalSetBy": set_by,
        },
    }


def limits_view(install: config.DocumentSettings) -> dict[str, Any]:
    return {
        "maxFileBytes": install.max_bytes,
        "maxFileMB": install.max_file_mb,
        "maxPassages": install.max_passages,
        "readsKinds": list(extract.KINDS),
        "reads": files.READS,
        "readTimeoutSeconds": reading_module.TIMEOUT_SECONDS,
    }


async def _own_document(
    request: Request, person: Person, doc_id: str, *, library: bool = True
) -> tuple[Index, Document]:
    """The asker's document, or the 404 a document that does not exist gets. The id is
    looked for in the person's own index; it is never joined to a path before it is
    known to be one there."""
    missing = _problem(status.HTTP_404_NOT_FOUND, "There is no such document.")
    if not valid_id(doc_id):
        raise missing
    index = _saved(request).index(person.sub, create=False)
    if index is None:
        raise missing
    doc = await index.document(doc_id)
    if doc is None or (library and doc.scope != LIBRARY):
        raise missing
    return index, doc


# --------------------------------------------------------------------------- #
# My documents
# --------------------------------------------------------------------------- #


@router.get("/api/documents")
async def list_documents(request: Request) -> dict[str, Any]:
    """The asker's documents (newest first), each with its state and counts; the totals,
    the limits, how documents are searched, and the asker's own settings. 503 with the
    words and `canRebuild` when the index cannot be used."""
    person = await _person(request)
    store, saved = _store(request), _saved(request)
    install = await config.document_settings(store)
    prefs = await store.documents_prefs(person.sub)
    index = saved.index(person.sub, create=False)
    documents: list[Document] = []
    totals = None
    kept = 0
    if index is not None:
        # The files the person added themselves: a folder's files are listed under the folder
        # (`GET /api/documents/folders/{id}/files`), so that a folder of ten thousand files is
        # not part of every poll.
        documents = await index.documents(LIBRARY, only="upload")
        totals = await index.totals(LIBRARY, only="upload")
        kept = await index.passages_total()
    status = await meaning_status(request, person)
    return {
        "documents": [document_view(d, meaning_row(saved, d, status)) for d in documents],
        "totals": {
            "documents": totals.documents if totals else 0,
            "passages": totals.passages if totals else 0,
            "bytes": totals.bytes if totals else 0,
            "byState": dict(totals.by_state) if totals else dict.fromkeys(index_module.STATES, 0),
            "passagesKept": kept,
            #: Passages that have their meaning, and passages still owed it.
            "embedded": totals.embedded if totals else 0,
            "owed": totals.owed if totals else 0,
        },
        "limits": limits_view(install),
        "search": search_facts(status, await sort_standing(request)),
        "prefs": prefs_view(prefs, install),
        "installEnabled": install.enabled,
        # The standing line: an answer can quote a document, so a person who is in an
        # owner's reach is told so (W4); the documents themselves are never the owner's.
        "ownerReadsChats": await config.owner_reads_chats(store),
    }


def _refusal(name: str, code: int, message: str) -> dict[str, Any]:
    return {"name": name, "status": code, "message": message}


def _storage_refusal(name: str, exc: OSError, where: Path) -> dict[str, Any]:
    """A file that could not be written, said as it was observed: a full disk names
    how much is free, as a media job's does; anything else, what the system said."""
    if exc.errno == errno.ENOSPC:
        try:
            free = f" ({files.describe_size(shutil.disk_usage(where).free)} free)"
        except OSError:
            free = ""
        return _refusal(
            name,
            status.HTTP_507_INSUFFICIENT_STORAGE,
            f"{name} was not added: the disk Workbench keeps your documents on is full{free}.",
        )
    return _refusal(
        name,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        f"Workbench could not store {name} ({exc.strerror or type(exc).__name__}).",
    )


def _clean_name(raw: str | None) -> str:
    """The name a file is kept under: its own, without any folders in front."""
    base = (raw or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return base[:255] or "document"


@router.post("/api/documents", status_code=status.HTTP_201_CREATED)
async def add_documents(
    request: Request, uploads: list[UploadFile] = File(..., alias="files")
) -> dict[str, Any]:
    """Keep these files in My documents (many at once: a multipart field `files`, once
    for each). Each is judged by its bytes (`files.detect`), and a file Workbench does
    not read, or is too large, or would take the person past their passage limit, is
    **refused on its own** (`refused`, with a status and the words) while the rest are
    kept. Each kept file is `queued` and read in the background: poll `GET /api/documents`.
    A file of the same name as one already kept replaces it (`replaced`), and its old
    words are never found again; one that is the same byte for byte is left
    (`unchanged`). 201 when at least one was kept; otherwise the first refusal's status."""
    person = await _person(request)
    store, saved = _store(request), _saved(request)
    install = await config.document_settings(store)
    if not install.enabled:
        raise _problem(status.HTTP_403_FORBIDDEN, _OWNER_OFF)
    if len(uploads) > MAX_FILES:
        raise _problem(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"That is {len(uploads):,} files at once; add at most {MAX_FILES} at a time.",
        )
    index = saved.index(person.sub)
    assert index is not None
    kept: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for upload in uploads:
        name = _clean_name(upload.filename)
        head = await upload.read(files.HEAD_BYTES)
        try:
            found = files.detect(head, upload.content_type, name)
        except files.Unsupported as exc:
            refused.append(_refusal(name, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, exc.message))
            continue
        if not found.is_document:
            refused.append(
                _refusal(
                    name,
                    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    f"{name} is {'an image' if found.kind == 'image' else 'an audio clip'}, not "
                    f"a document. My documents keeps PDF, Word, Markdown, text, CSV, HTML and "
                    "code files.",
                )
            )
            continue
        # A person who has kept nothing has no index yet, and a refusal makes none.
        if index.path.is_file() and await index.passages_total() >= install.max_passages:
            refused.append(
                _refusal(
                    name,
                    status.HTTP_409_CONFLICT,
                    f"You already keep {install.max_passages:,} passages, the limit. Remove a "
                    "document to add another, or ask the owner to raise it.",
                )
            )
            continue
        doc_id = new_id()
        target = index.part_path(doc_id)
        try:
            written = await _save_upload(upload, head, target, install.max_bytes)
        except OSError as exc:
            target.unlink(missing_ok=True)
            refused.append(_storage_refusal(name, exc, _state(request).settings.data_dir))
            continue
        except BaseException:
            target.unlink(missing_ok=True)  # a connection lost part-way leaves nothing
            raise
        if written is None:
            target.unlink(missing_ok=True)
            refused.append(
                _refusal(
                    name,
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    f"{name} is larger than the {files.describe_size(install.max_bytes)} "
                    "Workbench reads of one file.",
                )
            )
            continue
        if found.zipped and await asyncio.to_thread(files.zip_holds_document, target) is False:
            target.unlink(missing_ok=True)
            refused.append(
                _refusal(
                    name,
                    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    files.not_word(name).message,
                )
            )
            continue
        size, digest = await asyncio.to_thread(hash_file, target)
        added = await saved.add(
            person.sub,
            doc_id=doc_id,
            scope=LIBRARY,
            name=name,
            detected=found,
            size=size,
            sha256=digest,
            source="upload",
        )
        view = document_view(added.document)
        view["unchanged"] = added.unchanged
        view["replaced"] = [d.id for d in added.replaced]
        kept.append(view)
    if not kept and refused:
        raise HTTPException(
            status_code=refused[0]["status"],
            detail={"message": refused[0]["message"], "refused": refused},
        )
    return {"documents": kept, "refused": refused}


@router.post("/api/documents/rebuild")
async def rebuild_documents(request: Request) -> dict[str, Any]:
    """Make the person's search index again from their own files (Rebuild search): the
    index that is there, damaged or not, is thrown away, and each document their files
    still describe is made again, `queued`, and read again; poll `GET /api/documents`.
    Their original files are never touched. `documents` is how many were made again;
    `left` how many files no document could be made of."""
    person = await _person(request)
    store = _store(request)
    if not (await config.document_settings(store)).enabled:
        raise _problem(status.HTTP_403_FORBIDDEN, _OWNER_OFF)
    made = await _saved(request).rebuild(person.sub, store.chat_owners)
    # The index held the person's notes and past chats too (MV1, MV2): they are put back
    # from the store, in the background.
    state = _state(request)
    notes = getattr(state, "notes_index", None)
    if notes is not None:
        notes.dirty.add(person.sub)
        notes._later()
    exchanges = getattr(state, "exchanges_index", None)
    if exchanges is not None:
        exchanges.refill(person.sub)
    return {"documents": made.documents, "left": made.left}


@router.post("/api/documents/meaning/rebuild")
async def rebuild_meaning(request: Request) -> dict[str, Any]:
    """Add meaning again with the search model that is running ("Rebuild", on the paused
    header): every vector of this person's documents goes, with the record of the model that
    made them, and each document is owed all of its meaning again, `searchable` while it is
    added. **Words are never touched**: documents are found by their words throughout, and
    nothing is read again from the files. Poll `GET /api/documents`. `passages` is how many
    are owed; `search` is the header as it is now. 409 when this Workbench has no gateway to
    ask."""
    person = await _person(request)
    store, saved = _store(request), _saved(request)
    if not (await config.document_settings(store)).enabled:
        raise _problem(status.HTTP_403_FORBIDDEN, _OWNER_OFF)
    if saved.meaning is None:
        raise _problem(
            status.HTTP_409_CONFLICT,
            "Workbench has no gateway to ask about a search model, so it cannot add meaning.",
        )
    owed = await saved.meaning.rebuild(person.sub)
    return {
        "passages": owed,
        "search": search_facts(await meaning_status(request, person), await sort_standing(request)),
    }


@router.get("/api/documents/prefs")
async def get_prefs(request: Request) -> dict[str, Any]:
    person = await _person(request)
    store = _store(request)
    return prefs_view(
        await store.documents_prefs(person.sub), await config.document_settings(store)
    )


class PrefsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Whether a new chat starts with Documents on. Off until the person says.
    newChats: StrictBool | None = None
    #: Whether passages of saved documents may go to a model that runs outside the
    #: person's machines: `never`, `ask` (the default) or `always`.
    external: Literal["never", "ask", "always"] | None = None


@router.put("/api/documents/prefs")
async def put_prefs(request: Request, body: PrefsUpdate) -> dict[str, Any]:
    """Change the settings sent; the others stay. `null` returns one to its default."""
    person = await _person(request)
    store = _store(request)
    columns = {"newChats": "new_chats", "external": "external"}
    defaults: dict[str, Any] = {"newChats": False, "external": None}
    values: dict[str, Any] = {}
    for key in body.model_fields_set:
        given = getattr(body, key)
        values[columns[key]] = defaults[key] if given is None else given
    prefs = await store.put_documents_prefs(person.sub, **values)
    return prefs_view(prefs, await config.document_settings(store))


class QuestionToTry(BaseModel):
    """A question to try on the person's documents (Test a question)."""

    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    #: The chat whose own files are searched too, and whose model's window sets the
    #: budget; with `model`, another model's window.
    chatId: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=256)
    #: How many passages at most (1 to 10; 5 when not said).
    passages: int | None = Field(default=None, ge=1, le=10, strict=True)
    #: Search by only these ways, to see what each finds: `words`, `meaning`, or both (the
    #: default). A run that names its ways has no evidence rule: it shows what each way
    #: found, whatever it scored.
    ways: Annotated[list[Literal["words", "meaning"]], Field(min_length=1, max_length=2)] | None = (
        None
    )


def _hit_view(passage: Retrieved) -> dict[str, Any]:
    entry = context.passage_entry(passage)
    return {
        "rank": passage.rank,
        "docId": passage.doc_id,
        "name": passage.name,
        "scope": entry["scope"],
        "pageStart": passage.page_start,
        "pageEnd": passage.page_end,
        "lineStart": passage.line_start,
        "lineEnd": passage.line_end,
        "heading": passage.heading,
        "snippet": entry["snippet"],
        "text": passage.text,
        "tokens": passage.tokens,
        "chunkIds": list(passage.chunk_ids),
        "via": passage.via,
        # Why it matched: which of the words searched for are in it, and in its heading;
        # where each way put it (1 is best), and how near the question it was by meaning.
        "why": {
            "via": passage.via,
            "terms": list(passage.terms),
            "phrases": list(passage.phrases),
            "inHeading": list(passage.in_heading),
            "ranks": dict(passage.ranks),
            "similarity": (
                round(passage.similarity, 3) if passage.similarity is not None else None
            ),
        },
    }


def meaning_block(result: Retrieval | None, model: str | None, configured: bool) -> dict[str, Any]:
    """What a search by meaning came to, for a person to be told: whether it ran and with
    which search model; if not, why, in words (`quiet` when it was never in play, and is not
    worth saying beside every answer); and its best similarity, the floor under which that
    is not an answer, and whether its passages were set aside for it."""
    report = result.reports.get("meaning") if result is not None else None
    if report is None:
        return {
            "used": False,
            "code": "not_asked" if configured else "none",
            "message": None,
            "quiet": True,
            "model": None,
            "best": None,
            "floor": None,
            "belowFloor": False,
        }
    return {
        "used": report.ran,
        "code": report.code,
        "message": report.message,
        "quiet": report.quiet,
        "model": model if report.ran else None,
        "best": round(report.best, 3) if report.best is not None else None,
        "floor": report.floor,
        "belowFloor": report.below_floor,
    }


def sort_block(result: Retrieval | None, configured: bool) -> dict[str, Any]:
    """What sorting the passages came to, for a person to be told: whether the best passages were
    put in a result sorter's order (`used`) and by which sorter (`model`); if not, why, in words
    (`quiet` when sorting was never in play, and is not worth saying beside every answer), with
    the numbers it was decided by: how many passages, their `tokens`, the speed the sorter was
    priced at (`ratePerSecond`), the seconds that would take (`expectedSeconds`) and the time
    allowed (`budgetSeconds`, the Sort time setting); and, when it ran, how long it took
    (`seconds`). `setting` is the Result sorter setting as it is. Never a score: a passage's
    place by the sorter is in its `ranks`, beside its places by words and by meaning."""
    report = result.sort if result is not None else None
    if report is None:
        return {
            "used": False,
            "code": "not_asked" if configured else "none",
            "message": None,
            "quiet": True,
            "model": None,
            "setting": None,
            "passages": 0,
            "tokens": None,
            "ratePerSecond": None,
            "expectedSeconds": None,
            "budgetSeconds": None,
            "seconds": None,
        }
    return {
        "used": report.sorted,
        "code": report.code,
        "message": report.message,
        "quiet": report.quiet,
        "model": report.model,
        "setting": report.setting,
        "passages": report.passages,
        "tokens": report.tokens,
        "ratePerSecond": round(report.rate, 1) if report.rate is not None else None,
        "expectedSeconds": round(report.expected, 2) if report.expected is not None else None,
        "budgetSeconds": report.budget,
        "seconds": round(report.seconds, 3) if report.seconds is not None else None,
    }


async def built_model(index: Index) -> str | None:
    """The search model this person's meaning was built with, or None while there is none."""
    try:
        built = Identity.from_json(await index.embedder())
    except ValueError:
        return None
    return built.model if built is not None else None


@router.post("/api/documents/test")
async def test_question(request: Request, body: QuestionToTry) -> dict[str, Any]:
    """The passages Workbench would send for this question, with the file, page and
    headings of each and why it matched. **No model is asked** (the question is embedded by
    the search model, which is not a model that answers), and nothing leaves: it is the asker's
    own documents. `status` is `found`, `nothing_matched` (a normal result), `no_words`
    (nothing in the question to search for), `no_documents`, or `unavailable` (only with
    `ways`: none of the ways asked for could run; `meaning` says why); a search that cannot
    run is a 503 with its words, never `nothing_matched`. `mode` is how it searched (and,
    when a result sorter put the best passages in order, `, sorted by <the sorter>` after it), and
    `meaning` says whether meaning ran, with which search model, or why not. `sort` says whether
    the passages were sorted and by which sorter, or why not, with the numbers (the tokens, the
    sorter's speed, the seconds that would take and the Sort time allowed); each passage's
    `why.ranks` holds its place by words, by meaning and by the sorter (`sorter`), never a score."""
    person = await _person(request)
    store, saved = _store(request), _saved(request)
    install = await config.document_settings(store)
    if not install.enabled:
        raise _problem(status.HTTP_403_FORBIDDEN, _OWNER_OFF)
    scopes = [LIBRARY]
    ways = list(dict.fromkeys(body.ways)) if body.ways else None
    model_id = body.model
    if body.chatId is not None:
        chat = await _own_chat(request, person, body.chatId)
        scopes.append(chat_scope(chat.id))
        model_id = model_id or chat.model
    window = None
    if model_id:
        try:
            window = (await memory_api.model_facts(_state(request).hub, model_id)).window
        except HubError as exc:
            log.warning("a test question could not ask about model %s: %s", model_id, exc.message)
    budget = context.retrieval_budget(window, 0, context.ANSWER_RESERVE)
    block, each = context.passages_overhead()
    query = plan_query(body.question)
    out: dict[str, Any] = {
        "query": {"text": query.text, "terms": list(query.terms), "phrases": list(query.phrases)},
        "mode": saved.retriever.mode,
        "passages": [],
        "found": 0,
        "didNotFit": 0,
        "budget": {**budget.view(), "block": block, "perPassage": each},
        "searched": {"documents": 0, "passages": 0, "waiting": 0},
        "meaning": meaning_block(None, None, saved.meaning is not None),
        "sort": sort_block(None, saved.sorting is not None),
    }
    index = saved.index(person.sub, create=False)
    documents, passages, waiting = (
        await index.count_found(scopes) if index is not None else (0, 0, 0)
    )
    out["searched"] = {"documents": documents, "passages": passages, "waiting": waiting}
    if index is None or documents == 0:
        out["status"] = "no_documents"
        return out
    if query.empty:
        out["status"] = "no_words"
        return out
    result = await saved.retriever.retrieve(
        index,
        query,
        scopes,
        question=body.question,
        budget=max(0, budget.allowed - block),
        overhead=each,
        k=body.passages or 5,
        ways=ways,
    )
    out["mode"] = result.mode
    out["meaning"] = meaning_block(result, await built_model(index), saved.meaning is not None)
    out["sort"] = sort_block(result, saved.sorting is not None)
    out["passages"] = [_hit_view(p) for p in result.passages]
    out["found"] = result.found
    out["didNotFit"] = result.did_not_fit
    if result.mode == "none":
        out["status"] = "unavailable"  # every way asked for was unable to run: said, not "nothing"
    else:
        out["status"] = "found" if result.passages else "nothing_matched"
    return out


@router.get("/api/documents/{doc_id}")
async def get_document(request: Request, doc_id: str) -> dict[str, Any]:
    person = await _person(request)
    _, doc = await _own_document(request, person, doc_id)
    status = await meaning_status(request, person)
    return document_view(doc, meaning_row(_saved(request), doc, status))


@router.delete("/api/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_document(request: Request, doc_id: str) -> Response:
    """Remove a document for real: its row, passages, search entries, original and
    text, in one transaction, and its reader if it was being read. If a file cannot
    be removed, nothing is (500, with the cause), and it can be tried again."""
    person = await _person(request)
    _, doc = await _own_document(request, person, doc_id)
    split = index_module.split_source(doc.source)
    if split is not None:
        # A file of a folder would come back at the next check: removing it is telling Workbench
        # to stop reading it, so its path goes on the folder's ignore list.
        try:
            await _state(request).folder_sync.ignore_file(person.sub, *split)
        except IgnoreListFull:
            raise _problem(
                status.HTTP_409_CONFLICT,
                "This folder's ignore list is full, so this file would come back at the next "
                "check. Remove a pattern from the list first.",
            ) from None
    try:
        gone = await _saved(request).remove(person.sub, doc_id)
    except DeleteFailed as exc:
        raise _problem(status.HTTP_500_INTERNAL_SERVER_ERROR, exc.message) from None
    if gone is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such document.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/documents/{doc_id}/reread")
async def reread_document(request: Request, doc_id: str) -> dict[str, Any]:
    """Read a document again, from its original (Re-read): its old passages go now,
    so nothing of the old reading is found, and it is `queued`. 409 when its original is
    not on the disk any more."""
    person = await _person(request)
    _, doc = await _own_document(request, person, doc_id)
    if not (await config.document_settings(_store(request))).enabled:
        raise _problem(status.HTTP_403_FORBIDDEN, _OWNER_OFF)
    try:
        again = await _saved(request).reread(person.sub, doc_id)
    except OriginalMissing:
        if index_module.split_source(doc.source) is not None:
            raise _problem(
                status.HTTP_409_CONFLICT,
                "Workbench has no copy of this file to read again (it was not read). Use Sync now "
                "on its folder.",
            ) from None
        raise _problem(
            status.HTTP_409_CONFLICT,
            "Workbench's copy of this file is not on its disk any more. Add the file again.",
        ) from None
    if again is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such document.")
    return document_view(again)


_TEXT_HEADERS = {
    "Cache-Control": "private, no-store",
    "Content-Security-Policy": "sandbox",
    "X-Content-Type-Options": "nosniff",
}


@router.get("/api/documents/{doc_id}/text")
async def document_text(request: Request, doc_id: str) -> Response:
    """The text Workbench indexed from a document (Show indexed text): all of it, a
    PDF's pages marked `[page 3]`, as the passages were cut from it. The place to check
    when an answer misses something that is in the file. 409 while it is not read, or
    when it could not be (with the reason); a text that has gone is read again, and says so."""
    person = await _person(request)
    index, doc = await _own_document(request, person, doc_id)
    if doc.status in PENDING:
        raise _problem(status.HTTP_409_CONFLICT, "This document is still being read.")
    if doc.status not in FOUND:
        raise _problem(
            status.HTTP_409_CONFLICT, doc.reason or "Workbench could not read this document."
        )
    path = index.text_path(doc.id)

    def read() -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    text = await asyncio.to_thread(read)
    if text is None:
        # Derived data heals: the text is read again from the original.
        log.warning("the text of saved document %s is gone; reading it again", doc.id)
        with contextlib.suppress(OriginalMissing):
            await _saved(request).reread(person.sub, doc.id)
        raise _problem(
            status.HTTP_409_CONFLICT,
            "The text read from this document had gone, so Workbench is reading it again. "
            "Try again in a moment.",
        )
    return Response(text, media_type="text/plain; charset=utf-8", headers=_TEXT_HEADERS)


@router.get("/api/documents/{doc_id}/original")
async def document_original(request: Request, doc_id: str) -> Response:
    """The file as it was added (Open original), as a download: never shown in the
    page, which is the sandbox every attachment is served under too."""
    person = await _person(request)
    index, doc = await _own_document(request, person, doc_id)
    path = index.original_path(doc.id)
    if not await asyncio.to_thread(path.is_file):
        raise _problem(status.HTTP_404_NOT_FOUND, "That file is no longer on disk.")
    return FileResponse(
        path,
        media_type=doc.media_type,
        # A file of a folder is named by its folder and path; the download is the file's own name.
        filename=doc.name.rsplit("/", 1)[-1],
        content_disposition_type="attachment",
        headers={"Content-Security-Policy": "sandbox", "Cache-Control": "private, no-store"},
    )


@router.get("/api/documents/{doc_id}/passages")
async def document_passages(request: Request, doc_id: str, ids: str) -> dict[str, Any]:
    """Passages of a document by id (`ids=12,13`), as stored: what the Sources panel
    opens. A passage whose document was removed is a 404 ("removed"); one that is no
    longer there (the document was read again) is in `missing`."""
    person = await _person(request)
    index, doc = await _own_document(request, person, doc_id, library=False)
    try:
        wanted = [int(i) for i in ids.split(",") if i.strip()]
    except ValueError:
        raise _problem(status.HTTP_422_UNPROCESSABLE_CONTENT, "Name passages by number.") from None
    if not wanted or len(wanted) > MAX_IDS:
        raise _problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Ask for between 1 and {MAX_IDS} passages."
        )
    found = await index.passages(doc.id, wanted)
    return {
        "document": {"id": doc.id, "name": doc.name, "state": doc.status},
        "passages": [
            {
                "id": h.chunk_id,
                "order": h.ord,
                "text": h.text,
                "heading": h.heading_path,
                "pageStart": h.page_start,
                "pageEnd": h.page_end,
                "lineStart": h.line_start,
                "lineEnd": h.line_end,
            }
            for h in found
        ],
        "missing": [i for i in wanted if i not in {h.chunk_id for h in found}],
    }


# --------------------------------------------------------------------------- #
# a chat and its documents
# --------------------------------------------------------------------------- #


@dataclass
class Access:
    """What a chat's next request may do with the person's saved documents, and why not."""

    #: May it search My documents (the person's saved documents)?
    library: bool
    #: Why not, in the words a person reads, and a code a page can act on.
    code: str | None = None
    reason: str | None = None
    hint: str | None = None
    #: What the gateway said of the model, or None when it could not say.
    facts: memory_api.ModelFacts | None = None
    window: int | None = None
    #: For a model that runs outside the machines: how far documents may go, whose
    #: word that is, and whether the person chose "Send for this chat".
    choice: str | None = None
    set_by: str | None = None
    #: The window is too small for any documents (so chat files are not searched either).
    too_small: bool = False


def window_words(window: int | None) -> str | None:
    return context.too_small(window)


def outside_words(facts: memory_api.ModelFacts, choice: str, set_by: str) -> tuple[str, str | None]:
    """Why saved documents were held back from a model that runs outside the
    machines, and what the person can do about it."""
    if facts.locality == "external":
        reason = (
            f"Your documents were not sent: this chat uses {facts.who or 'another service'}, "
            "which runs outside your machines."
        )
    else:
        reason = (
            "Your documents were not sent: Eugene cannot tell whether this chat's model runs "
            "on your machines or on an outside account."
        )
    if choice == "ask":
        hint: str | None = "Choose Send for this chat to send them anyway."
    elif set_by == "owner":
        hint = "The owner of this Workbench has set documents never to go to such models."
    else:
        hint = "You have set documents never to go to such models."
    return reason, hint


async def decide_access(
    request: Request, person: Person, chat: Chat, model_id: str | None
) -> Access:
    """Whether a chat's next request, to this model, may search the person's saved
    documents. The request asks this, and so does the chat's chip, so the chip says
    what the request does."""
    store, hub = _store(request), _state(request).hub
    install = await config.document_settings(store)
    if not install.enabled:
        return Access(False, "install_off", _OWNER_OFF)
    if not model_id:
        return Access(False, "no_model", "Choose a model first, and documents can be sent to it.")
    try:
        facts = await memory_api.model_facts(hub, model_id)
    except HubError as exc:
        log.warning("documents could not ask about model %s: %s", model_id, exc.message)
        return Access(
            False,
            "model_unknown",
            f"Workbench could not ask Eugene about this model ({exc.message}), so no saved "
            "documents were sent.",
            window=None,
        )
    if (small := window_words(facts.window)) is not None:
        return Access(
            False, "window_too_small", small, facts=facts, window=facts.window, too_small=True
        )
    if facts.locality == "local":
        return Access(True, facts=facts, window=facts.window)
    prefs = await store.documents_prefs(person.sub)
    choice, set_by = memory_api.resolve_external(install.external, prefs["external"])
    if choice == "always" or (choice == "ask" and chat.settings.get("documentsExternal") is True):
        return Access(True, facts=facts, window=facts.window, choice=choice, set_by=set_by)
    reason, hint = outside_words(facts, choice, set_by)
    return Access(
        False,
        "outside_model",
        reason,
        hint,
        facts=facts,
        window=facts.window,
        choice=choice,
        set_by=set_by,
    )


async def chat_documents_view(
    request: Request, person: Person, chat: Chat, model: str | None, *, read_only: bool
) -> dict[str, Any]:
    """What this chat does with documents, for its chip: the switch, whether the next
    request searches (and if not, why), and what is there to search."""
    switch = "on" if chat.settings.get("documents") == "on" else "off"
    strict = bool(chat.settings.get("documentsOnly"))
    if read_only:
        return {
            "chatId": chat.id,
            "readOnly": True,
            "switch": switch,
            "documentsOnly": strict,
        }
    saved = _saved(request)
    index = saved.index(person.sub, create=False)
    library = (0, 0, 0)
    mine = (0, 0, 0)
    chat_docs: list[Document] = []
    if index is not None:
        library = await index.count_found([LIBRARY])
        mine = await index.count_found([chat_scope(chat.id)])
        chat_docs = await index.documents(chat_scope(chat.id))
    access = await decide_access(request, person, chat, model or chat.model)
    status = await meaning_status(request, person)
    facts = access.facts
    searching_library = switch == "on" and access.library
    searching_chat = bool(chat_docs) and not access.too_small and access.code != "install_off"
    reason, code, hint = access.reason, access.code, access.hint
    if switch == "off" and not searching_chat:
        reason, code, hint = "Documents are off for this chat.", "chat_off", None
    elif switch == "off":
        reason = code = hint = None
    return {
        "chatId": chat.id,
        "readOnly": False,
        "switch": switch,
        "documentsOnly": strict,
        "sending": searching_library or searching_chat,
        "searching": {"library": searching_library, "chat": searching_chat},
        "reason": reason,
        "reasonCode": code,
        "hint": hint,
        "model": (
            {
                "id": facts.id,
                "window": facts.window,
                "locality": facts.locality,
                "runsOn": facts.who,
            }
            if facts
            else None
        ),
        "outside": (
            {
                "choice": access.choice,
                "setBy": access.set_by,
                "canSendForThisChat": access.choice == "ask",
                "sendForThisChat": chat.settings.get("documentsExternal") is True,
            }
            if access.choice is not None
            else None
        ),
        "counts": {
            "library": {"documents": library[0], "passages": library[1], "waiting": library[2]},
            "chat": {"documents": mine[0], "passages": mine[1], "waiting": mine[2]},
        },
        "chatDocuments": [document_view(d, meaning_row(saved, d, status)) for d in chat_docs],
        "budget": {
            "limit": context.retrieval_limit(access.window),
            "window": access.window,
        },
        "search": search_facts(status, await sort_standing(request)),
    }


@router.get("/api/chats/{chat_id}/documents")
async def get_chat_documents(
    request: Request, chat_id: str, model: str | None = None
) -> dict[str, Any]:
    """This chat's Documents switch and what its next request does with the person's
    saved documents: whether it searches them, and if not, why. `model` is the model
    the person is about to send to (the chat's own when not said). The chat's owner
    sees all of it; the install's owner reading a chat read-only sees the switch and
    nothing of the person's documents."""
    from .api import _readable_chat

    person = await _person(request)
    chat, read_only = await _readable_chat(request, person, chat_id)
    return await chat_documents_view(request, person, chat, model, read_only=read_only)


# --------------------------------------------------------------------------- #
# what an answer is sent from them
# --------------------------------------------------------------------------- #


@dataclass
class Found:
    """What searching the person's documents came to for one answer."""

    #: The block of passages for the latest user message, or None.
    block: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    #: What the message keeps (`Message.retrieval`), or None when nothing was searched.
    retrieval: dict[str, Any] | None = None
    #: Workbench's own answer, when the chat is documents-only and there is nothing to say.
    reply: str | None = None


def _asked_before(history: list[Message]) -> str | None:
    """The person's message before the latest, for a follow-up like "and the second one?"."""
    users = [m for m in history if m.role == "user"]
    return users[-2].content if len(users) >= 2 else None


def retrieval_view(
    status_: str,
    *,
    message: str | None = None,
    code: str | None = None,
    query: Any = None,
    mode: str = "words",
    scopes: dict[str, bool] | None = None,
    strict: bool = False,
    asked_anyway: bool = False,
    passages: list[Retrieved] | None = None,
    found: int = 0,
    did_not_fit: int = 0,
    budget: context.RetrievalBudget | None = None,
    notes: list[dict[str, str]] | None = None,
    searched: tuple[int, int, int] | None = None,
    took: float | None = None,
    meaning: dict[str, Any] | None = None,
    sort: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What a message keeps of a search of the person's documents. `status` is `used`
    (passages were sent), `nothing_matched` (a normal result), `skipped` (not searched,
    and `code` says why) or `failed` (it could not run, and `message` says what was
    observed); never a failure that reads as nothing matched."""
    passages = passages or []
    return {
        "status": status_,
        "code": code,
        "message": message,
        "query": query.text if query is not None else None,
        "terms": list(query.terms) if query is not None else [],
        "phrases": list(query.phrases) if query is not None else [],
        "mode": mode,
        "scope": scopes or {"library": False, "chat": False},
        "documentsOnly": strict,
        "askedAnyway": asked_anyway,
        "answeredBy": None,
        "passages": len(passages),
        "documents": len({p.doc_id for p in passages}),
        "found": found,
        "didNotFit": did_not_fit,
        "tokens": sum(p.tokens for p in passages),
        "budget": budget.view() if budget is not None else None,
        "searched": (
            {"documents": searched[0], "passages": searched[1], "waiting": searched[2]}
            if searched is not None
            else None
        ),
        "notes": notes or [],
        "cited": [],
        "ms": None if took is None else round(took * 1000),
        #: Whether search by meaning ran (`used`), and with which search model; if not, why
        #: (`code`, `message`; `quiet` when it was never in play): "searched by words only: ...".
        "meaning": meaning,
        #: Whether the best passages were put in a result sorter's order (`used`), by which
        #: sorter, and if not, why, with the numbers (`sort_block`). The mode says it too.
        "sort": sort,
    }


def _strict_reply(retrieval: dict[str, Any]) -> str:
    """Workbench's own words when a chat that answers only from the person's documents
    has none to answer from: what was observed, and that the model was not asked."""
    status_, message = retrieval["status"], retrieval.get("message")
    if status_ == "nothing_matched":
        return context.NOTHING_IN_DOCUMENTS + _meaning_was_off(retrieval)
    if status_ == "no_documents":
        return _NO_DOCUMENTS
    tail = " This chat answers only from your documents, so it did not ask the model."
    if status_ == "failed":
        return f"{message}{tail}"
    return f"{message}{tail}" if message else context.NOTHING_IN_DOCUMENTS


def _meaning_was_off(retrieval: dict[str, Any]) -> str:
    """A sentence to follow "nothing matched" when search by meaning could not run: the
    nothing was by words only, and is said so (never a failed search as a plain "nothing")."""
    block = retrieval.get("meaning") or {}
    if block.get("used") or block.get("quiet") or not block.get("message"):
        return ""
    return f" {block['message']}"


async def retrieve(
    request: Request,
    person: Person,
    chat: Chat,
    history: list[Message],
    model_id: str,
    *,
    used: int,
    sent_whole: bool,
    progress: Progress,
    ask_anyway: bool = False,
) -> Found:
    """Search the person's saved documents for the answer about to be asked, and make
    the block of passages that goes in their latest message. A search that breaks says
    so (`failed`) and the answer goes on without it. `used` is the tokens the request
    already holds; `sent_whole` says an attached document goes whole, which a
    documents-only chat may answer from when nothing is found."""
    last = history[-1] if history and history[-1].role == "user" else None
    install = await config.document_settings(_store(request))
    if last is None or not install.enabled:
        return Found()
    saved = _saved(request)
    switch = chat.settings.get("documents") == "on"
    here = chat_scope(chat.id)
    strict = _is_strict(chat, ask_anyway)
    reserve = _reserve(chat)
    index = saved.index(person.sub, create=False)
    flags = {"library": False, "chat": False}
    notes: list[dict[str, str]] = []
    #: What the view says of this search, as it becomes known: the query, the
    #: budget, how much there was to search.
    known: dict[str, Any] = {}

    def done(status_: str, **said: Any) -> Found:
        view = retrieval_view(
            status_,
            scopes=flags,
            strict=strict,
            asked_anyway=ask_anyway,
            notes=notes,
            **known,
            **said,
        )
        return _finish(view, strict=strict, sent_whole=sent_whole)

    try:
        has_chat_docs = index is not None and bool(await index.documents(here))
        if not switch and not has_chat_docs:
            return Found()
        if index is None:
            # Nothing has ever been kept, so there is nothing to search and no model to
            # ask about: a chat that answers only from documents says so.
            if not strict:
                return Found()
            return done("no_documents", code="no_documents", message=_NO_DOCUMENTS)
        access = await decide_access(request, person, chat, model_id)
        known["budget"] = context.retrieval_budget(access.window, used, reserve)
        if access.too_small:
            return done("skipped", code="window_too_small", message=access.reason)
        if access.code in ("install_off", "no_model"):
            return Found()
        library_ok = switch and access.library
        if switch and not access.library:
            notes.append({"code": str(access.code), "message": str(access.reason)})
            if access.hint:
                notes[-1]["hint"] = access.hint
        flags.update(library=library_ok, chat=has_chat_docs)
        scopes = [s for s, wanted in ((LIBRARY, library_ok), (here, has_chat_docs)) if wanted]
        if not scopes:
            return done("skipped", code=access.code or "unavailable", message=access.reason)
        documents, passages_n, waiting = await index.count_found(scopes)
        known["searched"] = (documents, passages_n, waiting)
        if waiting:
            notes.append(
                {
                    "code": "still_reading",
                    "message": (
                        f"{waiting} of your documents {'is' if waiting == 1 else 'are'} still "
                        "being read, so not searched yet."
                    ),
                }
            )
        if documents == 0:
            if waiting:
                return done("skipped", code="still_reading", message=notes[-1]["message"])
            if not strict:
                return Found()  # nothing is kept, so nothing was searched, and nothing is said
            return done("no_documents", code="no_documents", message=_NO_DOCUMENTS)
        query = plan_query(last.content, _asked_before(history))
        known["query"] = query
        if query.empty:
            return done(
                "skipped",
                code="no_words",
                message="Your message has no words to search for, so no passages were added.",
            )
        budget: context.RetrievalBudget = known["budget"]
        block_cost, each = context.passages_overhead()
        if budget.allowed < block_cost + each + 1:
            return done("skipped", code="chat_full", message=_no_room(used, reserve, budget))
        started = time.perf_counter()
        progress({"stage": "documents", "phase": "searching"})
        result = await saved.retriever.retrieve(
            index,
            query,
            scopes,
            question=last.content,
            budget=budget.allowed - block_cost,
            overhead=each,
        )
        known.update(mode=result.mode, found=result.found, did_not_fit=result.did_not_fit)
        known["took"] = time.perf_counter() - started
        known["meaning"] = meaning_block(
            result, await built_model(index), saved.meaning is not None
        )
        known["sort"] = sort_block(result, saved.sorting is not None)
        gone = result.reports.get("meaning")
        if gone is not None and not gone.ran and not gone.quiet:
            # Meaning could not run for this answer: searched by words only, and said so.
            notes.append({"code": f"meaning_{gone.code}", "message": str(gone.message)})
        if not result.passages:
            if result.found:
                # Found, and not one fits what the window leaves: said, never "nothing matched".
                return done("skipped", code="chat_full", message=_no_room(used, reserve, budget))
            return done(
                "nothing_matched",
                message="Nothing in your documents matched." + _meaning_was_off(known),
            )
    except IndexUnavailable as exc:
        log.warning("searching a person's documents failed: %s (%s)", exc.code, exc.message)
        return done("failed", code=exc.code, message=exc.message)
    except Exception as exc:
        log.warning(
            "searching a person's documents failed: %s: %s", type(exc).__name__, exc, exc_info=True
        )
        return done(
            "failed",
            code="error",
            message=(
                f"Searching your documents failed ({type(exc).__name__}), so this answer "
                "used none. See Workbench's log."
            ),
        )
    texts = [last.content]
    for passage in result.passages:
        texts += [passage.text, passage.name, passage.heading or ""]
    block = context.render_passages(
        context.new_retrieval_nonce(texts), result.passages, strict=strict
    )
    view = retrieval_view(
        "used",
        scopes=flags,
        strict=strict,
        asked_anyway=ask_anyway,
        notes=notes,
        passages=result.passages,
        **known,
    )
    log.info(
        "%d passage(s) from %d document(s), about %d tokens, went with an answer (%s, %d ms)",
        view["passages"],
        view["documents"],
        view["tokens"],
        result.mode,
        view["ms"],
    )
    return Found(
        block=block, sources=[context.passage_entry(p) for p in result.passages], retrieval=view
    )


def _no_room(used: int, reserve: int, budget: context.RetrievalBudget) -> str:
    if budget.window is None:
        return "Documents were not used: there is no room for them."
    return context.documents_full(used, reserve, budget.window)


def _reserve(chat: Chat) -> int:
    wanted = chat.settings.get("maxTokens")
    return int(wanted) if isinstance(wanted, int) and wanted > 0 else context.ANSWER_RESERVE


def _is_strict(chat: Chat, ask_anyway: bool) -> bool:
    return bool(chat.settings.get("documentsOnly")) and not ask_anyway


def _finish(view: dict[str, Any], *, strict: bool, sent_whole: bool) -> Found:
    """A search that sent no passages: said with the answer. A chat that answers only
    from the person's documents, with none attached whole to answer from, does not ask a
    model: its answer is Workbench's own words."""
    if strict and not sent_whole:
        view["answeredBy"] = "workbench"
        return Found(retrieval=view, reply=_strict_reply(view))
    return Found(retrieval=view)
