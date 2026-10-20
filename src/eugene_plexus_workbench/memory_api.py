"""Memory's routes, and what a chat's request does with a person's notes (MR1; MV1).

The rules themselves -- what a note may be, what a model is shown, what a
command is -- are `memory.py`, pure. This module is the part that reads the
store, asks the gateway where a model runs, and answers the page.

**Everything is the asker's.** Every route takes the person from the session
and nothing from the client, and every store call puts that person in its
own query, so **a note that is not yours is the same 404 as one that does
not exist** (W4). `ownerReadsChats` does not reach memory: there is no route
that reads another person's notes, and in the owner's read-only view of a
chat the memory shows counts, taken from the chat's saved copy and its answers'
records, without ever reading the notes table (`workbench-memory.md`).

**What a model is sent comes in two parts** (MV1, `workbench-memory-v2.md` section 6):

- **The pinned block** (instructions and pinned notes, and the bodies of pinned notes that
  fit) goes in the chat's system message. The first request that carries notes keeps it with
  the chat (`memory.Snapshot`), and every later turn sends that text again so the engine's
  prompt cache holds. A request for another model takes a new one; "Refresh memory" takes a
  new one on request; a note that is deleted or replaced is dropped from every chat's copy at
  once.
- **Relevant memory** is chosen again for every turn (`turn_for`, `memory_relevance`) from
  what the person just asked, and goes in their latest message only, as data, as passages of
  their documents do. Each answer keeps what its turn was sent (`Message.memory_used`).
- **`search_memory`**, for a model that calls tools: the same search, run by Workbench, its
  results the same delimited data (`run_search_tool`).

**Notes go to a model that runs on the person's own machines.** For one that
runs outside them, or that Eugene cannot place, they are held back unless
the person has said otherwise (MRC6): `ask` (the default) holds them until
the person chooses "Send for this chat", `never` always holds them, `always`
sends them. The install's `memoryExternalModels` is the default for a person
who has not chosen, except that its `never` is the owner's rule and final
(`resolve_external`): an owner can forbid it for everyone, and otherwise a
person's own choice stands. One decision (`_placement`) covers the pinned block, relevant
memory and the tool, and a `search_memory` result kept from an earlier turn is withheld from a
request that may not carry notes (`conversation.request_for`).

**Memory never stops an answer.** Anything that goes wrong choosing the notes is
logged with its cause and the answer is sent without them.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import (
    config,
    memory,
    memory_learn,
    memory_policy,
    memory_provenance,
    memory_relevance,
    memory_tidy,
    memory_write,
    projects,
)
from .api import _new_id, _own_chat, _person, _problem, _readable_chat, _state
from .hub import Hub, HubError
from .media_api import _common as served
from .store import Chat, MemoryFull, MemoryNote, Message, Person, Store, TagListFull, tag_key

log = logging.getLogger(__name__)

router = APIRouter()

_OWNER_OFF = "The owner of this Workbench has turned Memory off."
_PERSON_OFF = "Memory is off for you. Turn it on in Memory to share notes with models."
#: The newest notes the Memory page lists outside the pinned ones; a search finds the rest.
PAGE_NOTES = 200
#: What one search of notes (the page, `search_memory`) returns at most.
SEARCH_LIMIT = 50


def _store(request: Request) -> Store:
    store: Store = _state(request).store
    return store


def _hub(request: Request) -> Hub:
    hub: Hub = _state(request).hub
    return hub


def _finder(state: Any) -> memory_relevance.Finder:
    found = getattr(state, "finder", None)
    if found is None:
        found = state.finder = memory_relevance.Finder(state.store, getattr(state, "saved", None))
    return found


async def _reindex(state: Any, owner: str, note_ids: list[str]) -> None:
    """Keep the person's index in step with these notes (`notes_index`). Never raises."""
    keeper = getattr(state, "notes_index", None)
    if keeper is not None and note_ids:
        await keeper.put(owner, note_ids)


async def _unindex(state: Any, owner: str, note_ids: list[str]) -> None:
    """Take these notes' words out of the person's index, before the notes go. Raises when
    it cannot, so the delete that asked does not go on (`notes_index`)."""
    keeper = getattr(state, "notes_index", None)
    if keeper is not None and note_ids:
        await keeper.remove(owner, note_ids)


def _refused(code: int, detail: dict[str, Any]) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def resolve_external(install: str, chosen: str | None) -> tuple[str, str]:
    """How far a person's notes may travel (to a model outside the machines),
    and whose word that is: `(never | ask | always, owner | you | default)`.

    The owner's `never` is final. Otherwise the person's own choice stands,
    and a person who has not chosen gets the owner's default, which is `ask`
    until the owner says. So no owner setting takes a person's `always` away
    except the one that forbids it, and a person never gets more than they
    asked for."""
    if install == "never":
        return "never", "owner"
    if chosen is not None:
        return chosen, "you"
    return install, "default"


# --------------------------------------------------------------------------- #
# how things are said
# --------------------------------------------------------------------------- #


async def leaving(store: Store, owner: str, notes: list[MemoryNote]) -> list[MemoryNote]:
    """The notes that may go to a model outside the machines or a connection (call 12, MV3b):
    under the person's write policy's `unseenLocal`, none a model kept that they have not
    seen yet."""
    install = await config.memory_settings(store)
    policy, _ = memory_write.policy_for(install, await store.memory_prefs(owner))
    return memory.shareable(notes) if policy.get("unseenLocal") == "yes" else notes


async def unseen_withheld(store: Store, owner: str) -> bool:
    """Whether the person has a note a model kept that they have not seen, under a policy that
    keeps such notes on the machines (call 12)."""
    held = await store.memory_notes(owner)
    return len(await leaving(store, owner, held)) != len(held)


def _policy_name(policy: str | None) -> str | None:
    try:
        return str(json.loads(policy)["name"]) if policy else None
    except (ValueError, KeyError, TypeError):
        return None


def note_view(note: MemoryNote) -> dict[str, Any]:
    out = {
        "id": note.id,
        "text": note.text,
        "kind": note.kind,
        "pinned": note.pinned,
        # Instructions and pinned notes are in every turn (the page's "Always used").
        "always": memory.is_always(note),
        "status": note.status,
        "source": note.source,
        "sourceChatId": note.source_chat_id,
        # MR9b: the connection that suggested it, by the name it had then.
        "sourceConnection": note.source_connection,
        "sourceMessageId": note.source_message_id,
        "createdAt": note.created_at,
        "updatedAt": note.updated_at,
        "lastUsedAt": note.last_used_at,
        "useCount": note.use_count,
        # MR8: an event leaves chats after `expiresAt`; a project is asked about from `revisitAt`.
        "expiresAt": note.expires_at,
        "revisitAt": note.revisit_at,
        # MV1: its tags, how long its body is, and its place among its versions.
        "tags": list(note.tags),
        "bodyChars": note.body_chars,
        "validFrom": note.valid_from,
        "supersededAt": note.superseded_at,
        "supersededBy": note.superseded_by,
        # MV3b: the model that wrote it, the write policy then, whether it was kept with
        # nobody approving it, and whether its person has seen it since.
        "model": note.model,
        "policy": _policy_name(note.policy),
        "auto": note.auto,
        "seen": note.seen_at is not None,
        # MV7: the project it is kept to, or null for a general note.
        "projectId": note.project_id,
    }
    if note.body is not None:
        out["body"] = note.body
    return out


def suggestion_view(
    note: MemoryNote, replaces: MemoryNote | None, listed: set[str]
) -> dict[str, Any]:
    """A suggestion as Suggested notes shows it: the note, the person's words it came from,
    the note it would replace, if the model said one, and the tags it came with, saying which
    are not on the person's list ("Add to my tags")."""
    return {
        **note_view(note),
        "quote": note.quote,
        "replaces": note_view(replaces) if replaces is not None else None,
        "proposedTags": [{"tag": t, "listed": tag_key(t) in listed} for t in note.proposed_tags],
    }


def policy_choices(install: config.MemorySettings) -> list[str]:
    """The presets a person may choose: any, if the owner allows; else those at least as strict
    as the owner's, row by row."""
    if install.policy_people == memory_policy.ANY:
        return list(memory_policy.PRESETS_NAMES)
    return [
        name
        for name in memory_policy.PRESETS_NAMES
        if memory_policy.is_stricter_or_equal(memory_policy.preset(name), install.policy)
    ]


def prefs_view(prefs: dict[str, Any], install: config.MemorySettings) -> dict[str, Any]:
    """What a person has set, what is in effect now, and whose word it is.

    A setting the person has not made is `null`, and `effective` says what
    that comes to. The page shows `effective`, never a stand-in that looks like
    a choice (settings never lie)."""
    external, set_by = resolve_external(install.external, prefs["external"])
    learn, learn_by = memory_learn.effective(install, prefs, None)
    policy, policy_by = memory_write.policy_for(install, prefs)
    return {
        "enabled": prefs["enabled"],
        "external": prefs["external"],
        "budgetTokens": prefs["budget_tokens"],
        "relevantBudgetTokens": prefs.get("relevant_budget_tokens"),
        "searchTool": bool(prefs.get("search_tool", True)),
        "chatsTool": bool(prefs.get("chats_tool", True)),
        # MR8: `learn` is null until the person chooses; `useMode` is theirs, `relevant` until
        # they choose `all`.
        "learn": ("off" if prefs["learn"] == "off" else "suggest")
        if prefs.get("learn_set")
        else None,
        "useMode": prefs.get("use_mode") or "relevant",
        # MV3b: what the person chose (null: the owner's), what is in effect and whose word it
        # is, and what they may choose.
        "writePolicy": prefs.get("write_policy"),
        "policyChoices": policy_choices(install),
        "policyPeople": install.policy_people,
        "effective": {
            "enabled": install.enabled and prefs["enabled"],
            "disabledBy": ("owner" if not install.enabled else None if prefs["enabled"] else "you"),
            "external": external,
            "externalSetBy": set_by,
            "budgetTokens": prefs["budget_tokens"] or memory.BUDGET_CEILING,
            "relevantBudgetTokens": prefs.get("relevant_budget_tokens") or memory.RELEVANT_CEILING,
            # For no model in particular; a chat on a measured model may differ (`learnByModel`).
            "learn": learn,
            "learnSetBy": learn_by,
            "learnByModel": install.suggestions == "measured" and not prefs.get("learn_set"),
            "suggestionsAllowed": install.enabled and install.suggestions != "off",
            "policy": {**policy.view(), "setBy": policy_by, "owners": install.policy.name},
        },
    }


def _limits(install: config.MemorySettings) -> dict[str, Any]:
    return {
        "maxChars": memory.MAX_NOTE_CHARS,
        "maxBodyChars": install.max_body_chars,
        "maxNotes": install.max_notes,
        "kinds": list(memory.KINDS),
        "minWindow": memory.MIN_WINDOW,
        "budgetCeiling": memory.BUDGET_CEILING,
        "budgetSharePercent": memory.BUDGET_SHARE_PERCENT,
        "relevantCeiling": memory.RELEVANT_CEILING,
        "relevantSharePercent": memory.RELEVANT_SHARE_PERCENT,
        "budgetFloor": memory.BUDGET_FLOOR,
        "maxTagChars": memory.MAX_TAG_CHARS,
        "maxTagsPerNote": memory.MAX_TAGS_PER_NOTE,
        "maxDescriptionChars": memory.MAX_DESCRIPTION_CHARS,
        "pageNotes": PAGE_NOTES,
    }


# --------------------------------------------------------------------------- #
# the Memory page
# --------------------------------------------------------------------------- #


@router.get("/api/memory")
async def get_memory(request: Request, where: str = "all") -> dict[str, Any]:
    """The asker's notes by section, their tags, their settings, and what the notes cost.

    `usage.pinned` is what the pinned block would take of its budget in a chat whose model
    has room; `usage.relevant` is the most relevant memory may take a turn, and from how
    many notes it is chosen. A chat on a small window gets less, and its own drawer
    (`GET /api/chats/{id}/memory`) says how much. "Other notes" lists the newest
    `PAGE_NOTES`; `GET /api/memory/notes` searches them all."""
    person = await _person(request)
    store = _store(request)
    install = await config.memory_settings(store)
    prefs = await store.memory_prefs(person.sub)
    notes = await store.memory_notes(person.sub)
    today = _today()
    budget = memory.budget_for(None, prefs["budget_tokens"])
    # What a chat in no project is given (MV7): the figures are a general chat's.
    always = [n for n in projects.in_place(notes, projects.GENERAL) if memory.is_always(n)]
    shown = memory.build_snapshot(
        always,
        budget=budget,
        window=None,
        model=None,
        now=0.0,
        today=today,
        bodies=await _bodies(store, person.sub, always),
        nonce="00000000",
    )
    # MV7: the notes of one place when `where` says so (`all`, `general`, a project's id).
    placed = _where(notes, where)
    current = [n for n in placed if not memory.is_past(n, today)]
    others = [n for n in current if not memory.is_always(n)]
    by_id = {n.id: n for n in notes}
    # A suggestion left waiting a month is not one they want: it goes, quote and all.
    await store.expire_suggestions(person.sub, time.time() - SUGGESTION_DAYS * 86_400.0)
    pending = await store.memory_notes(person.sub, status="pending")
    tags = await store.memory_tags(person.sub)
    listed = {t["key"] for t in tags}
    relevance = dict(
        zip(
            ("available", "why"),
            await memory_relevance.available(
                getattr(getattr(_state(request), "saved", None), "meaning", None)
            ),
            strict=True,
        )
    )
    return {
        "installEnabled": install.enabled,
        "ownerReadsChats": await config.owner_reads_chats(store),
        "prefs": prefs_view(prefs, install),
        "sections": {
            "always": [note_view(n) for n in current if memory.is_always(n)],
            "other": [note_view(n) for n in others[:PAGE_NOTES]],
            "otherTotal": len(others),
            # MR8: events whose day has gone, kept until the person deletes them.
            "past": [note_view(n) for n in placed if memory.is_past(n, today)],
        },
        "suggestions": [
            suggestion_view(n, by_id.get(n.replaces) if n.replaces else None, listed)
            for n in pending
        ],
        "revisit": await _revisits(request, person.sub, notes, prefs, today),
        # MV6: what the tidy pass proposes, each with its notes.
        "tidy": await _tidy(request, person.sub, today),
        # MV3b: what models kept by themselves lately, each with its model and an Undo.
        "learned": [
            note_view(n)
            for n in await store.learned_notes(person.sub, time.time() - LEARNED_DAYS * 86_400.0)
        ],
        "tags": tags,
        # Whether choosing by meaning can be done now (settings never lie).
        "relevance": relevance,
        "usage": {
            # Kept for the page as it was: the pinned block's figures.
            "tokens": shown.tokens,
            "budget": budget,
            "pinned": {"tokens": shown.tokens, "budget": budget, "notes": len(shown.sent)},
            "relevant": {
                "budget": memory.relevant_budget_for(None, prefs.get("relevant_budget_tokens")),
                "from": len(others),
                "how": (
                    memory_relevance.ALL_THAT_FIT
                    if prefs.get("use_mode") == "all"
                    else memory_relevance.WORDS_AND_MEANING
                    if relevance["available"]
                    else memory_relevance.WORDS
                ),
            },
            "notes": len(notes),
            "maxNotes": install.max_notes,
            "sent": len(shown.sent),
            "notSent": len(shown.dropped),
        },
        "limits": _limits(install),
    }


async def _bodies(store: Store, owner: str, notes: list[MemoryNote]) -> dict[str, str]:
    """The bodies of these notes, by id, for the ones that have one."""
    wanted = [n.id for n in notes if n.body_chars]
    if not wanted:
        return {}
    return {
        n.id: n.body or ""
        for n in await store.memory_notes_by_ids(owner, wanted, with_body=True)
        if n.body
    }


@router.get("/api/memory/notes")
async def list_notes(
    request: Request,
    query: str = "",
    tag: str | None = None,
    offset: int = 0,
    limit: int = PAGE_NOTES,
    where: str = "all",
) -> dict[str, Any]:
    """The asker's current notes: newest first, or the ones that match `query` (by words
    and meaning; `#tag` filters), and only those with `tag` when it is given. `where` (MV7):
    `all`, `general`, or a project's id."""
    person = await _person(request)
    store = _store(request)
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    notes = _where(await store.memory_notes(person.sub), where)
    if tag:
        key = tag_key(tag)
        notes = [n for n in notes if key in {tag_key(t) for t in n.tags}]
    how = None
    if query.strip():
        chosen = await _finder(_state(request)).search(person.sub, query[:1000], notes, today=None)
        by_id = {n.id: n for n in notes}
        notes = [by_id[c.id] for c in chosen.candidates if c.id in by_id]
        how = {"how": chosen.how, "why": chosen.why}
    return {
        "notes": [note_view(n) for n in notes[offset : offset + limit]],
        "total": len(notes),
        "offset": offset,
        "search": how,
    }


@router.get("/api/memory/notes/{note_id}")
async def get_note(request: Request, note_id: str) -> dict[str, Any]:
    """One note whole, and the versions it replaced, newest first."""
    person = await _person(request)
    store = _store(request)
    note = await store.memory_note(person.sub, note_id)
    if note is None or note.status not in ("active", "superseded"):
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such note.")
    history = await store.note_history(person.sub, note_id)
    links = (await store.note_documents(person.sub, [note_id])).get(note_id, [])
    return {
        "note": note_view(note),
        "earlier": [note_view(n) for n in history],
        # MV6: the documents it rests on, as they were when linked.
        "documents": [memory_provenance.link_view(link) for link in links],
    }


class NoteDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    docId: str = Field(min_length=1, max_length=200)


@router.post("/api/memory/notes/{note_id}/documents")
async def link_document(request: Request, note_id: str, body: NoteDocument) -> dict[str, Any]:
    """Based on a document (MV6): the person names one of their documents (My documents or a
    folder's file) a current note of theirs rests on. 409 at `MAX_LINKS`."""
    person = await _person(request)
    store = _store(request)
    if not (await config.memory_settings(store)).enabled:
        raise _refused(status.HTTP_403_FORBIDDEN, {"message": _OWNER_OFF, "reason": "off"})
    saved = getattr(_state(request), "saved", None)
    index = saved.index(person.sub, create=False) if saved is not None else None
    doc = await index.document(body.docId) if index is not None else None
    if doc is None or doc.scope != "library":
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such document in your documents.")
    held = (await store.note_documents(person.sub, [note_id])).get(note_id, [])
    if len(held) >= memory_provenance.MAX_LINKS and all(h["doc_id"] != doc.id for h in held):
        raise _problem(
            status.HTTP_409_CONFLICT,
            f"A note can rest on at most {memory_provenance.MAX_LINKS} documents.",
        )
    link = {
        "doc_id": doc.id,
        "scope": doc.scope,
        "source": doc.source,
        "name": doc.name,
        "sha256": doc.sha256,
        "place": [],
    }
    count = await store.link_note_documents(
        person.sub, note_id, [link], "person", memory_provenance.MAX_LINKS
    )
    if count < 0:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such note.")
    links = (await store.note_documents(person.sub, [note_id])).get(note_id, [])
    return {"documents": [memory_provenance.link_view(x) for x in links]}


@router.delete(
    "/api/memory/notes/{note_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def unlink_document(request: Request, note_id: str, doc_id: str) -> Response:
    """The note no longer rests on that document. The document is not touched."""
    person = await _person(request)
    if not await _store(request).unlink_note_document(person.sub, note_id, doc_id):
        raise _problem(status.HTTP_404_NOT_FOUND, "The note does not name that document.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


_KIND = Literal["fact", "preference", "instruction", "project", "event", "procedure"]
_SUGGESTED_KIND = Literal["fact", "preference", "instruction", "project", "event"]


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=2000)
    #: MV1: Markdown, up to the install's `memoryMaxBodyChars`.
    body: str = Field(default="", max_length=40_000)
    kind: _KIND = "fact"
    pinned: StrictBool = False
    tags: list[str] = Field(default_factory=list, max_length=50)
    #: `button` for a message's Remember; then the chat and message it came from.
    source: Literal["typed", "button"] = "typed"
    chatId: str | None = Field(default=None, max_length=200)
    messageId: str | None = Field(default=None, max_length=200)
    #: MV7: typed on a project's page, the project; from a chat, its chat's project instead.
    projectId: str | None = Field(default=None, max_length=200)


class NoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(default=None, max_length=2000)
    body: str | None = Field(default=None, max_length=40_000)
    kind: _KIND | None = None
    pinned: StrictBool | None = None
    tags: list[str] | None = Field(default=None, max_length=50)
    #: MV1: "Save as a new version" (the fact changed: the note is kept as history), not
    #: "Save" (a typo: changed in place).
    asNewVersion: StrictBool = False


class PrefsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool | None = None
    external: Literal["never", "ask", "always"] | None = None
    budgetTokens: int | None = Field(default=None, ge=memory.BUDGET_FLOOR, le=20000, strict=True)
    relevantBudgetTokens: int | None = Field(
        default=None, ge=memory.BUDGET_FLOOR, le=20000, strict=True
    )
    searchTool: StrictBool | None = None
    #: MV2: whether models may search the person's past chats (`search_chats`).
    chatsTool: StrictBool | None = None
    #: MR8. `null` returns Learn from my chats to the install's default.
    learn: Literal["off", "suggest"] | None = None
    useMode: Literal["relevant", "all"] | None = None
    offerDismissed: StrictBool | None = None
    #: MV3b: the person's own write policy; `null` returns to the owner's.
    writePolicy: Literal["strict", "standard", "open"] | None = None


def _vetted(text: str) -> str:
    try:
        return memory.validate_note(text)
    except memory.NoteRefused as exc:
        raise _refused(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.view()) from None


def _vetted_body(body: str, install: config.MemorySettings) -> str:
    try:
        return memory.validate_body(body, install.max_body_chars)
    except memory.NoteRefused as exc:
        raise _refused(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.view()) from None


def _vetted_tags(tags: list[str]) -> list[str]:
    try:
        return memory.validate_tags(tags)
    except memory.NoteRefused as exc:
        raise _refused(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.view()) from None


def _full(limit: int) -> dict[str, Any]:
    return {
        "message": (
            f"You have {limit:,} notes, the limit. Delete some, or ask the owner to raise the "
            "limit in Eugene's console."
        ),
        "reason": "full",
        "limit": limit,
    }


def _tags_full(limit: int) -> dict[str, Any]:
    return {
        "message": f"Your tag list has {limit:,} tags, the limit. Delete or merge some first.",
        "reason": "tags_full",
        "limit": limit,
    }


@router.post("/api/memory/notes")
async def add_note(request: Request, body: NoteCreate) -> JSONResponse:
    """Keep a note. 201 with the note; 200 when the same words were already
    kept (`created: false`). 422 names why words cannot be kept, 409 says the
    person is at their limit, 403 that the owner has turned memory off."""
    person = await _person(request)
    store = _store(request)
    install = await config.memory_settings(store)
    if not install.enabled:
        raise _refused(status.HTTP_403_FORBIDDEN, {"message": _OWNER_OFF, "reason": "off"})
    text = _vetted(body.text)
    note_body = _vetted_body(body.body, install)
    tags = _vetted_tags(body.tags)
    chat_id = message_id = None
    answer: Message | None = None
    project_id = None
    if body.chatId is not None:
        chat = await _own_chat(request, person, body.chatId)
        chat_id = chat.id
        # A note belongs where it was said (MV7-C2): in the chat's project.
        project_id = chat.project_id
        if body.messageId is not None:
            message = await store.message(body.messageId)
            if message is None or message.chat_id != chat.id:
                raise _problem(status.HTTP_404_NOT_FOUND, "There is no such message in this chat.")
            message_id = message.id
            answer = message if message.role == "assistant" else None
    elif body.messageId is not None:
        raise _problem(status.HTTP_400_BAD_REQUEST, "A message needs the chat it is in.")
    elif body.projectId is not None:
        if await store.project(person.sub, body.projectId) is None:
            raise _problem(status.HTTP_404_NOT_FOUND, "There is no such project.")
        project_id = body.projectId
    now = time.time()
    note = MemoryNote(
        id=_new_id(),
        owner=person.sub,
        text=text,
        kind=body.kind,
        created_at=now,
        updated_at=now,
        pinned=body.pinned,
        source=body.source,
        source_chat_id=chat_id,
        source_message_id=message_id,
        body=note_body,
        tags=tags,
        project_id=project_id,
    )
    try:
        kept, created = await store.add_memory_note(note, limit=install.max_notes)
    except MemoryFull as exc:
        raise _refused(status.HTTP_409_CONFLICT, _full(exc.limit)) from None
    except TagListFull as exc:
        raise _refused(status.HTTP_409_CONFLICT, _tags_full(exc.limit)) from None
    if created:
        await _reindex(_state(request), person.sub, [kept.id])
        if answer is not None:
            # MV6: kept from an answer, it rests on the documents the answer cited.
            await memory_provenance.link_answer(_state(request), person.sub, kept.id, answer)
    return JSONResponse(
        {"note": note_view(kept), "created": created},
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@router.patch("/api/memory/notes/{note_id}")
async def edit_note(request: Request, note_id: str, body: NoteUpdate) -> dict[str, Any]:
    """Change a current note in place ("Save"), or save what changed as a new version that
    replaces it (`asNewVersion`: the old one is kept as history and never sent again).
    `replaced` names the note a new version replaced."""
    person = await _person(request)
    store = _store(request)
    install = await config.memory_settings(store)
    if not install.enabled:
        raise _refused(status.HTTP_403_FORBIDDEN, {"message": _OWNER_OFF, "reason": "off"})
    text = _vetted(body.text) if body.text is not None else None
    note_body = _vetted_body(body.body, install) if body.body is not None else None
    tags = _vetted_tags(body.tags) if body.tags is not None else None
    state = _state(request)
    try:
        if body.asNewVersion:
            old = await store.memory_note(person.sub, note_id)
            if old is None or old.status != "active":
                raise _problem(status.HTTP_404_NOT_FOUND, "There is no such note.")
            now = time.time()
            new = MemoryNote(
                id=_new_id(),
                owner=person.sub,
                text=text if text is not None else old.text,
                kind=body.kind or old.kind,
                created_at=now,
                updated_at=now,
                pinned=old.pinned if body.pinned is None else body.pinned,
                source="typed",
                body=note_body if note_body is not None else old.body or "",
                tags=tags if tags is not None else list(old.tags),
                expires_at=old.expires_at,
                revisit_at=old.revisit_at,
                project_id=old.project_id,
            )
            kept = await store.supersede_memory_note(
                person.sub, note_id, new, limit=install.max_notes
            )
            if kept is None:
                raise _problem(status.HTTP_404_NOT_FOUND, "There is no such note.")
            await _reindex(state, person.sub, [kept.id, note_id])
            # MV6: the new version rests on its documents as they are now.
            await memory_provenance.settle_all(state, person.sub, kept.id)
            return {"note": note_view(kept), "replaced": note_id}
        note = await store.update_memory_note(
            person.sub,
            note_id,
            text=text,
            body=note_body,
            kind=body.kind,
            pinned=body.pinned,
            tags=tags,
        )
    except MemoryFull as exc:
        raise _refused(status.HTTP_409_CONFLICT, _full(exc.limit)) from None
    except TagListFull as exc:
        raise _refused(status.HTTP_409_CONFLICT, _tags_full(exc.limit)) from None
    if note is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such note.")
    await _reindex(state, person.sub, [note.id])
    return {"note": note_view(note), "replaced": None}


async def forget(state: Any, owner: str, note_id: str) -> list[str]:
    """Forget a note for real (and, for a current one, its earlier versions): first its words
    in the person's index, then it, its log's words, its tags and every chat's copy of it.
    The ids that went; none when it is not theirs. Raises when the index cannot let go of
    its words: then nothing was deleted."""
    store: Store = state.store
    note = await store.memory_note(owner, note_id, with_body=False)
    if note is None:
        return []
    chain = [note_id] + (
        [n.id for n in await store.note_history(owner, note_id)]
        if note.status != "superseded"
        else []
    )
    await _unindex(state, owner, chain)
    gone = await store.delete_memory_note(owner, note_id)
    if set(gone) != set(chain):
        # Another version was made or forgotten between the two steps: what the store
        # holds now is what the index must hold.
        await _reindex(state, owner, sorted(set(gone) | set(chain)))
    return gone


@router.delete("/api/memory/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(request: Request, note_id: str) -> Response:
    """Forget a note for real: it, its earlier versions (unless it is one), its words in the
    index, its log's words, its tags and every chat's copy of it. Allowed whatever the owner
    has set. 503 when its words could not be taken out of the index: then nothing went."""
    person = await _person(request)
    try:
        gone = await forget(_state(request), person.sub, note_id)
    except Exception as exc:
        log.warning("a note was not deleted: its index said %s: %s", type(exc).__name__, exc)
        raise _problem(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Workbench could not remove this note's words from your search index, so nothing "
            f"was deleted ({type(exc).__name__}). Try again in a moment.",
        ) from None
    if not gone:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such note.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/memory/notes")
async def delete_all_notes(request: Request, confirm: str | None = None) -> dict[str, Any]:
    """Forget every note the person has. The page asks them to type a
    confirmation; this asks for `?confirm=all`, so a stray call does nothing."""
    person = await _person(request)
    if confirm != "all":
        raise _problem(
            status.HTTP_400_BAD_REQUEST, "To delete every note, say so: add ?confirm=all."
        )
    state = _state(request)
    store = _store(request)
    every = [n.id for n in await store.memory_notes(person.sub, status=None)]
    try:
        await _unindex(state, person.sub, every)
    except Exception as exc:
        raise _problem(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Workbench could not remove your notes' words from your search index, so nothing "
            f"was deleted ({type(exc).__name__}). Try again in a moment.",
        ) from None
    deleted = await store.delete_memory_notes(person.sub)
    # MV5: the person's own memory server is cleared too, and listed to check.
    from . import memory_servers

    cleared = await memory_servers.clear(state, person.sub)
    return {"deleted": deleted, **({"server": cleared} if cleared is not None else {})}


@router.put("/api/memory/prefs")
async def put_prefs(request: Request, body: PrefsUpdate) -> dict[str, Any]:
    """Change the settings sent; the others stay. `null` returns one to its
    default."""
    person = await _person(request)
    store = _store(request)
    values: dict[str, Any] = {}
    defaults = {
        "enabled": True,
        "external": None,
        "budgetTokens": None,
        "relevantBudgetTokens": None,
        "searchTool": True,
        "chatsTool": True,
        "learn": "off",
        "useMode": "relevant",
        "offerDismissed": False,
        "writePolicy": None,
    }
    columns = {
        "enabled": "enabled",
        "external": "external",
        "budgetTokens": "budget_tokens",
        "relevantBudgetTokens": "relevant_budget_tokens",
        "searchTool": "search_tool",
        "chatsTool": "chats_tool",
        "learn": "learn",
        "useMode": "use_mode",
        "offerDismissed": "offer_dismissed",
        "writePolicy": "write_policy",
    }
    install = await config.memory_settings(store)
    if body.writePolicy is not None and body.writePolicy not in policy_choices(install):
        raise _problem(
            422,
            "The owner of this Workbench lets people choose only a policy stricter than "
            f"theirs ({install.policy.name.title()}).",
        )
    for key in body.model_fields_set:
        given = getattr(body, key)
        values[columns[key]] = defaults[key] if given is None else given
        if key == "learn":
            values["learn_set"] = given is not None
    prefs = await store.put_memory_prefs(person.sub, **values)
    return prefs_view(prefs, await config.memory_settings(store))


# --------------------------------------------------------------------------- #
# the tag list (MV1, call 14)
# --------------------------------------------------------------------------- #


class TagCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tag: str = Field(max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class TagUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: A new name: every note follows; a name already on the list merges the two.
    tag: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class TagImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: The file's text: CSV `tag,description` (a header row optional), or one tag a line.
    text: str = Field(max_length=500_000)
    format: Literal["csv", "txt"] = "csv"
    #: "Replace my list": tags not in the file are deleted, from the list and their notes.
    replace: StrictBool = False
    #: For `replace`: the tags the person was told would go, sent back to say yes. Until it
    #: is, nothing changes and the answer says what would.
    confirm: list[str] | None = Field(default=None, max_length=2000)


def _vetted_tag(tag: str) -> str:
    try:
        return memory.validate_tag(tag)
    except memory.NoteRefused as exc:
        raise _refused(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.view()) from None


def _vetted_description(text: str) -> str:
    try:
        return memory.validate_description(text)
    except memory.NoteRefused as exc:
        raise _refused(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.view()) from None


@router.get("/api/memory/tags")
async def list_tags(request: Request) -> dict[str, Any]:
    person = await _person(request)
    return {"tags": await _store(request).memory_tags(person.sub)}


@router.post("/api/memory/tags")
async def add_tag(request: Request, body: TagCreate) -> dict[str, Any]:
    """Add a tag to the list, or change the description of one already on it."""
    person = await _person(request)
    tag = _vetted_tag(body.tag)
    description = _vetted_description(body.description) if body.description is not None else None
    try:
        kept = await _store(request).put_memory_tag(person.sub, tag, description)
    except TagListFull as exc:
        raise _refused(status.HTTP_409_CONFLICT, _tags_full(exc.limit)) from None
    return {"tag": kept}


async def _tagged(store: Store, owner: str, tag: str) -> list[str]:
    key = tag_key(tag)
    return [n.id for n in await store.memory_notes(owner) if key in {tag_key(t) for t in n.tags}]


@router.patch("/api/memory/tags/{tag:path}")
async def edit_tag(request: Request, tag: str, body: TagUpdate) -> dict[str, Any]:
    """Rename a tag (every note follows; onto a tag already on the list, the two merge), or
    change its description. 404 when the person has no such tag."""
    person = await _person(request)
    store = _store(request)
    known = {t["key"] for t in await store.memory_tags(person.sub)}
    if tag_key(tag) not in known:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such tag.")
    notes: int | None = None
    name = tag
    if body.tag is not None:
        name = _vetted_tag(body.tag)
        merged = tag_key(name) in known and tag_key(name) != tag_key(tag)
        affected = await _tagged(store, person.sub, tag)
        notes = await store.rename_memory_tag(person.sub, tag, name)
        if notes is None:
            raise _problem(status.HTTP_404_NOT_FOUND, "There is no such tag.")
        await _reindex(_state(request), person.sub, affected)
    else:
        merged = False
    if body.description is not None:
        await store.put_memory_tag(person.sub, name, _vetted_description(body.description))
    kept = next(t for t in await store.memory_tags(person.sub) if t["key"] == tag_key(name))
    return {"tag": kept, "merged": merged, "notes": notes}


@router.delete("/api/memory/tags/{tag:path}")
async def delete_tag(request: Request, tag: str) -> dict[str, Any]:
    """Take a tag off the list and off every note; the notes stay. `notes` is how many lost
    it (the page's dialog says so first, from the list's counts)."""
    person = await _person(request)
    store = _store(request)
    affected = await _tagged(store, person.sub, tag)
    notes = await store.delete_memory_tag(person.sub, tag)
    if notes is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such tag.")
    await _reindex(_state(request), person.sub, affected)
    return {"deleted": tag, "notes": notes}


@router.get("/api/memory/tags/export")
async def export_tags(request: Request) -> Response:
    """The tag list as CSV, `tag,description`, with a header row: any spreadsheet or text
    editor opens it, and Import takes it back."""
    person = await _person(request)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["tag", "description"])
    for t in await _store(request).memory_tags(person.sub):
        writer.writerow([t["tag"], _csv_cell(t["description"])])
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'attachment; filename="workbench-tags.csv"',
        },
    )


#: What a spreadsheet takes a cell starting with as a formula.
_FORMULA_START = ("=", "+", "-", "@")


def _csv_cell(text: str) -> str:
    """A description as a CSV cell no spreadsheet runs: one that would start a formula
    (after any quote marks) gets a quote mark first, which `read_tag_file` takes off."""
    return "'" + text if text.lstrip("'").startswith(_FORMULA_START) else text


def _from_csv_cell(text: str) -> str:
    return (
        text[1:]
        if text.startswith("'") and text[1:].lstrip("'").startswith(_FORMULA_START)
        else text
    )


def read_tag_file(text: str, fmt: str) -> tuple[list[tuple[str, str | None]], list[dict[str, Any]]]:
    """A tag file's rows, `(tag, description or None)`, and every line it could not take,
    `{line, text, message}`. CSV is `tag,description` (a first row `tag,description` is a
    header); a `.txt` is one tag a line. Blank lines are skipped; a tag twice keeps the
    last description given."""
    rows: dict[str, tuple[str, str | None]] = {}
    problems: list[dict[str, Any]] = []
    if text.startswith("﻿"):
        text = text[1:]
    if fmt == "txt":
        lines = [(i, [line]) for i, line in enumerate(text.splitlines(), 1)]
    else:
        lines = list(enumerate(csv.reader(io.StringIO(text)), 1))
    for number, cells in lines:
        if not cells or not any(c.strip() for c in cells):
            continue
        if (
            fmt == "csv"
            and number == 1
            and cells[0].strip().casefold() == "tag"
            and (len(cells) < 2 or cells[1].strip().casefold() == "description")
        ):
            continue
        if fmt == "csv" and len(cells) > 2:
            problems.append(
                {
                    "line": number,
                    "text": ",".join(cells)[:120],
                    "message": "A row has at most two cells: tag, description. Quote a "
                    "description that holds a comma.",
                }
            )
            continue
        try:
            tag = memory.validate_tag(cells[0])
            description = (
                memory.validate_description(_from_csv_cell(cells[1]) if fmt == "csv" else cells[1])
                if len(cells) > 1
                else None
            )
        except memory.NoteRefused as exc:
            problems.append({"line": number, "text": cells[0][:120], "message": exc.message})
            continue
        rows[tag_key(tag)] = (tag, description or None)
    return list(rows.values()), problems


@router.post("/api/memory/tags/import")
async def import_tags(request: Request, body: TagImport) -> dict[str, Any]:
    """Bring in a tag list. By default it adds new tags and updates descriptions, and
    removes nothing. With `replace`, the first answer only says what would be removed and
    from how many notes (`confirmNeeded`); sending again with `confirm` naming exactly those
    tags does it. Lines that cannot be taken are named, and the rest are taken."""
    person = await _person(request)
    store = _store(request)
    rows, problems = read_tag_file(body.text, body.format)
    removed: list[dict[str, Any]] = []
    if body.replace:
        would = await store.tags_removed_by(person.sub, [t for t, _ in rows])
        named = sorted(tag_key(w["tag"]) for w in would)
        if body.confirm is None or sorted(tag_key(t) for t in body.confirm) != named:
            return {
                "confirmNeeded": True,
                "wouldRemove": would,
                "rows": len(rows),
                "problems": problems,
            }
    affected: list[str] = []
    if body.replace:
        wanted = {tag_key(t) for t, _ in rows}
        affected = [
            n.id
            for n in await store.memory_notes(person.sub)
            if any(tag_key(t) not in wanted for t in n.tags)
        ]
    try:
        done = await store.import_memory_tags(person.sub, rows, replace=body.replace)
    except TagListFull as exc:
        raise _refused(status.HTTP_409_CONFLICT, _tags_full(exc.limit)) from None
    removed = done["removed"]
    await _reindex(_state(request), person.sub, affected)
    return {
        "confirmNeeded": False,
        "added": done["added"],
        "updated": done["updated"],
        "removed": removed,
        "problems": problems,
    }


# --------------------------------------------------------------------------- #
# suggestions (MR8): the inbox, "Still true?", and the offer
# --------------------------------------------------------------------------- #

#: "Still true?" is asked about at most one project a week.
REVISIT_EVERY_SECONDS = 7 * 86_400.0
#: The offer of suggestions is made after this many chats.
OFFER_AFTER_CHATS = 5
#: A suggestion waits this many days for the person, then goes.
SUGGESTION_DAYS = 30
#: MV3b: Recently learned lists what models kept in this many days.
LEARNED_DAYS = 30


async def _revisits(
    request: Request,
    owner: str,
    notes: list[MemoryNote],
    prefs: dict[str, Any],
    today: str,
) -> list[dict[str, Any]]:
    """Still true?: every note whose documents changed, each with why (MV6, at once), then the
    project the timer is due on, if the week allows."""
    changed = await memory_provenance.due(_state(request), owner)
    by_id = {n.id: n for n in notes}
    out = [
        {**note_view(by_id[k]), "reasons": [c.view() for c in v if not c.snoozed]}
        for k, v in changed.items()
        if k in by_id
    ]
    asked = {o["id"] for o in out}
    out += [
        {**note_view(n), "reasons": []}
        for n in _due_revisits(notes, prefs, today)
        if n.id not in asked
    ]
    return out


def tidy_view(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "kind": item["kind"],
        "notes": [note_view(n) for n in item["notes"]],
    }


async def _tidy(request: Request, owner: str, today: str) -> list[dict[str, Any]]:
    """Run the tidy pass if it is due (once a day), and what waits from it. Never raises: the
    page is read whatever the pass came to."""
    store = _store(request)
    try:
        await memory_tidy.run(store, owner, today)
        return [tidy_view(i) for i in await memory_tidy.proposals(store, owner, today)]
    except Exception as exc:
        log.warning("the tidy pass did not run: %s: %s", type(exc).__name__, exc)
        return []


def _where(notes: list[MemoryNote], where: str) -> list[MemoryNote]:
    """Notes of one place (MV7): `all`, `general`, or a project's id."""
    if where == "all":
        return notes
    if where == "general":
        return [n for n in notes if n.project_id is None]
    return [n for n in notes if n.project_id == where]


def _due_revisits(notes: list[MemoryNote], prefs: dict[str, Any], today: str) -> list[MemoryNote]:
    """The project to ask "Still true?" about now: the one longest due, unless the person
    was asked within the week."""
    asked = prefs.get("revisit_asked_at")
    if asked is not None and time.time() - asked < REVISIT_EVERY_SECONDS:
        return []
    due = sorted(
        (n for n in notes if n.kind == "project" and n.revisit_at and n.revisit_at <= today),
        key=lambda n: (n.revisit_at or "", n.created_at),
    )
    return due[:1]


class KeepSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: The person's own edit of the words, and of the kind, before keeping it. Never a
    #: procedure: a suggestion is a model's words, and a procedure is the person's (MV1).
    text: str | None = Field(default=None, max_length=2000)
    kind: _SUGGESTED_KIND | None = None
    #: For a suggestion that replaces a note: Keep new (true: the old one becomes its
    #: earlier version) or Keep both (false).
    replace: StrictBool = False
    #: The tags kept on it; a tag not on the list is added to it ("Add to my tags"). None
    #: keeps the ones it came with that are on the list.
    tags: list[str] | None = Field(default=None, max_length=50)


@router.post("/api/memory/suggestions/{note_id}/keep")
async def keep_suggestion(request: Request, note_id: str, body: KeepSuggestion) -> dict[str, Any]:
    """Keep a suggestion as a note, as it is or as the person edited it. With `replace`, the
    note it would replace becomes its earlier version (kept as history, never sent). 404 when
    the person has no such suggestion; 422 names why edited words cannot be kept."""
    person = await _person(request)
    store = _store(request)
    if not (await config.memory_settings(store)).enabled:
        raise _refused(status.HTTP_403_FORBIDDEN, {"message": _OWNER_OFF, "reason": "off"})
    text = _vetted(body.text) if body.text is not None else None
    tags = _vetted_tags(body.tags) if body.tags is not None else None
    try:
        kept = await store.keep_suggestion(
            person.sub, note_id, text=text, kind=body.kind, replace=body.replace, tags=tags
        )
    except TagListFull as exc:
        raise _refused(status.HTTP_409_CONFLICT, _tags_full(exc.limit)) from None
    if kept is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such suggestion.")
    note, replaced = kept
    await _reindex(_state(request), person.sub, [note.id, *([replaced] if replaced else [])])
    return {"note": note_view(note), "replaced": replaced}


@router.post("/api/memory/suggestions/{note_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_suggestion(request: Request, note_id: str) -> Response:
    """Turn a suggestion away: it goes, and it is never suggested again (only its words'
    stems are kept for that). 404 when the person has no such suggestion."""
    person = await _person(request)
    store = _store(request)
    note = await store.memory_note(person.sub, note_id)
    if note is None or note.status != "pending":
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such suggestion.")
    if not await store.dismiss_suggestion(
        person.sub, note_id, memory_learn.dismissed_words(note.text)
    ):
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such suggestion.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class Seen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(max_length=500)


@router.post("/api/memory/learned/seen")
async def learned_seen(request: Request, body: Seen) -> dict[str, Any]:
    """The person saw these notes a model kept, in Recently learned (call 12): from now on
    they go where their notes go."""
    person = await _person(request)
    return {"seen": await _store(request).mark_learned_seen(person.sub, body.ids)}


async def _undo(request: Request, owner: str, ids: list[str]) -> dict[str, Any]:
    """Undo notes a model kept: out of the index first (as a delete), then the store, and the
    notes they replaced back in."""
    state = _state(request)
    store = _store(request)
    mine = {n.id for n in await store.memory_notes(owner) if n.auto}
    wanted = [i for i in ids if i in mine]
    if not wanted:
        return {"removed": [], "restored": []}
    try:
        await _unindex(state, owner, wanted)
    except Exception as exc:  # as a delete: nothing goes while the index cannot follow
        raise _problem(503, f"Workbench could not take the notes out of search ({exc}).") from exc
    gone, back = await store.undo_learned(owner, wanted)
    await _reindex(state, owner, back)
    return {"removed": gone, "restored": back}


@router.post("/api/memory/learned/{note_id}/undo")
async def undo_learned(request: Request, note_id: str) -> dict[str, Any]:
    """Undo one note a model kept: it goes, and the note it replaced is current again."""
    person = await _person(request)
    done = await _undo(request, person.sub, [note_id])
    if not done["removed"]:
        raise _problem(404, "There is no such note a model kept.")
    return done


def _since(day: str) -> float:
    try:
        return datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp()
    except ValueError as exc:
        raise _problem(422, "`since` is a date, YYYY-MM-DD.") from exc


@router.get("/api/memory/learned/removal")
async def removal_preview(request: Request, since: str, model: str | None = None) -> dict[str, Any]:
    """How many notes `model` (any model when not given) kept since `since`: what Remove would
    take, named before it does."""
    person = await _person(request)
    ids = await _store(request).learned_since(person.sub, model, _since(since))
    return {"count": len(ids), "model": model, "since": since}


class Removal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    since: str = Field(min_length=10, max_length=10)
    model: str | None = Field(default=None, max_length=256)


@router.post("/api/memory/learned/removal")
async def remove_learned(request: Request, body: Removal) -> dict[str, Any]:
    """Remove everything a model kept since a date, each undone as Undo does, in one step."""
    person = await _person(request)
    ids = await _store(request).learned_since(person.sub, body.model, _since(body.since))
    done = await _undo(request, person.sub, ids)
    return {**done, "count": len(done["removed"])}


class Revisit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    noteId: str = Field(max_length=200)
    #: `yes`, still true (asked again in 60 days); `no`, forget it; `later`, ask next week.
    answer: Literal["yes", "no", "later"]


@router.post("/api/memory/revisit")
async def revisit(request: Request, body: Revisit) -> dict[str, Any]:
    """The person's answer to "Still true?": about one of their projects (the timer), or about
    a note whose documents changed (MV6: `yes` moves its links to the documents as they are
    now, `later` puts them off a week)."""
    person = await _person(request)
    store = _store(request)
    note = await store.memory_note(person.sub, body.noteId)
    if note is None or note.status != "active":
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such note.")
    if body.answer != "no":
        await memory_provenance.answer(_state(request), person.sub, note.id, body.answer)
    timer = note.kind == "project" and note.revisit_at is not None and note.revisit_at <= _today()
    if not timer and body.answer != "no":
        return {"answer": body.answer}
    if body.answer == "yes":
        days = memory_learn.memory_suggest.PROJECT_REVISIT_DAYS
        later = (datetime.now().date() + timedelta(days=days)).isoformat()
        await store.set_revisit(person.sub, note.id, later)
    elif body.answer == "no":
        try:
            await forget(_state(request), person.sub, note.id)
        except Exception as exc:
            raise _problem(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Workbench could not remove this note's words from your search index, so it "
                f"was kept ({type(exc).__name__}). Try again in a moment.",
            ) from None
    if timer:
        # The timer's one project a week; a question a document raised is not held to it.
        await store.put_memory_prefs(person.sub, revisit_asked_at=time.time())
    return {"answer": body.answer}


class TidyAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: `keep` (two notes: keep `keepId`, the other becomes its earliest version), `keep_both`;
    #: `remove` (an event long past: forget it), `keep_event`.
    action: Literal["keep", "keep_both", "remove", "keep_event"]
    keepId: str | None = Field(default=None, max_length=200)


@router.post("/api/memory/tidy/{tidy_id}")
async def answer_tidy(request: Request, tidy_id: str, body: TidyAnswer) -> dict[str, Any]:
    """The person's answer to what the tidy pass proposed (MV6). 404 when nothing waits under
    that id; 409 when its notes changed since it was proposed (it is dropped)."""
    person = await _person(request)
    store = _store(request)
    state = _state(request)
    waiting = {i["id"]: i for i in await memory_tidy.proposals(store, person.sub, _today())}
    item = waiting.get(tidy_id)
    if item is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "Nothing waits under that id.")
    ids = [n.id for n in item["notes"]]
    if item["kind"] == "duplicate":
        if body.action == "keep_both":
            await store.settle_tidy(person.sub, tidy_id, "kept")
            return {"action": body.action}
        keep = body.keepId
        if body.action != "keep" or keep is None or keep not in ids:
            raise _problem(status.HTTP_400_BAD_REQUEST, "Say which of the two notes to keep.")
        if not (await config.memory_settings(store)).enabled:
            raise _refused(status.HTTP_403_FORBIDDEN, {"message": _OWNER_OFF, "reason": "off"})
        other = next(i for i in ids if i != keep)
        kept = await store.merge_memory_notes(person.sub, keep, other)
        if kept is None:
            await store.drop_tidy(person.sub, [tidy_id])
            raise _problem(status.HTTP_409_CONFLICT, "These notes changed since; nothing was done.")
        await _reindex(state, person.sub, [kept.id, other])
        await store.settle_tidy(person.sub, tidy_id, "merged")
        return {"action": body.action, "note": note_view(kept), "replaced": other}
    if body.action == "keep_event":
        await store.settle_tidy(person.sub, tidy_id, "kept")
        return {"action": body.action}
    if body.action != "remove":
        raise _problem(status.HTTP_400_BAD_REQUEST, "Remove the event, or keep it.")
    try:
        gone = await forget(state, person.sub, ids[0])
    except Exception as exc:
        raise _problem(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Workbench could not remove this note's words from your search index, so it "
            f"was kept ({type(exc).__name__}). Try again in a moment.",
        ) from None
    await store.settle_tidy(person.sub, tidy_id, "removed")
    return {"action": body.action, "removed": gone}


@router.get("/api/memory/offer")
async def offer(request: Request) -> dict[str, Any]:
    """Whether to offer Learn from my chats now: after the person's fifth chat, while it is
    off only because nobody chose (not because they or the owner turned it off), until they
    answer the offer."""
    person = await _person(request)
    store = _store(request)
    install = await config.memory_settings(store)
    prefs = await store.memory_prefs(person.sub)
    learn, by = memory_learn.effective(install, prefs, None)
    if learn != "off" or by != memory_learn.DEFAULT or prefs.get("offer_dismissed"):
        return {"show": False}
    chats = len(await store.chats(person.sub))
    return {"show": chats >= OFFER_AFTER_CHATS, "chats": chats}


def _day(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).strftime("%Y-%m-%d")


def _stamp(moment: float | None) -> str | None:
    if moment is None:
        return None
    return datetime.fromtimestamp(moment, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("/api/memory/export")
async def export(request: Request, format: Literal["md", "json"] = "md") -> Response:
    """The asker's notes as a plain file: Markdown to read, JSON to keep. Both carry each
    note's body and tags, the versions a note replaced, and the tag list (MV1)."""
    person = await _person(request)
    store = _store(request)
    every = await store.memory_notes(person.sub, status=None, with_body=True)
    notes = [n for n in every if n.status == "active"]
    earlier = [n for n in every if n.status == "superseded"]
    tags = await store.memory_tags(person.sub)
    # MV6: the documents each note rests on.
    links = await store.note_documents(person.sub)
    names = {p.id: p.name for p in await store.projects(person.sub)}
    # MV5: what the person's own memory server lists, or why it is not here.
    from . import memory_servers

    server = await memory_servers.export_items(_state(request), person.sub)
    now = time.time()
    headers = {"Cache-Control": "private, no-store"}
    if format == "json":

        def entry(n: MemoryNote) -> dict[str, Any]:
            return {
                "id": n.id,
                "text": n.text,
                "body": n.body or "",
                "kind": n.kind,
                "pinned": n.pinned,
                "tags": list(n.tags),
                "source": n.source,
                "createdAt": _stamp(n.created_at),
                "updatedAt": _stamp(n.updated_at),
                "validFrom": _stamp(n.valid_from),
                "supersededAt": _stamp(n.superseded_at),
                "supersededBy": n.superseded_by,
                # MV7: where it is kept, by the project's name then; null for General.
                "project": names.get(n.project_id) if n.project_id else None,
                "documents": [
                    {
                        "name": link["name"],
                        "sha256": link["sha256"],
                        "how": link["how"],
                        "place": link["place"],
                        "linkedAt": _stamp(link["linked_at"]),
                    }
                    for link in links.get(n.id, [])
                ],
            }

        return JSONResponse(
            {
                "exportedAt": _stamp(now),
                "notes": [entry(n) for n in notes],
                "earlierVersions": [entry(n) for n in earlier],
                "tags": [{"tag": t["tag"], "description": t["description"]} for t in tags],
                **({"memoryServer": server} if server is not None else {}),
            },
            headers={
                **headers,
                "Content-Disposition": 'attachment; filename="workbench-memory.json"',
            },
        )
    lines = [
        "# Memory",
        "",
        f"What Workbench remembers for {person.name}. Exported {_day(now)}.",
        "",
    ]
    for title, members in (
        ("Always used (instructions and pinned notes)", [n for n in notes if memory.is_always(n)]),
        ("Other notes", [n for n in notes if not memory.is_always(n)]),
        ("Earlier versions", earlier),
    ):
        lines += [f"## {title}", ""]
        if not members:
            lines += ["Nothing here.", ""]
        for n in members:
            kept = f"{n.kind}{', pinned' if n.pinned else ''}, added {_day(n.created_at)}"
            if n.superseded_at is not None:
                kept += f", replaced {_day(n.superseded_at)}"
            if n.tags:
                kept += ", tags: " + ", ".join(n.tags)
            lines.append(f"- {n.text} *({kept})*")
            if n.body:
                lines += ["", *("    " + line if line else "" for line in n.body.split("\n")), ""]
        lines.append("")
    lines += ["## Tags", ""]
    if not tags:
        lines += ["Nothing here.", ""]
    for t in tags:
        lines.append(f"- {t['tag']}" + (f": {t['description']}" if t["description"] else ""))
    lines.append("")
    if server is not None:
        lines += [f"## From your memory server ({server['server'] or server['url']})", ""]
        if server["note"]:
            lines += [server["note"], ""]
        for item in server["memories"] or []:
            text = " ".join(str(item["text"]).split())
            lines.append(f"- {text}" + (f" *({item['date']})*" if item["date"] else ""))
        if server["memories"] == []:
            lines.append("Nothing here.")
        lines.append("")
    return Response(
        "\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={**headers, "Content-Disposition": 'attachment; filename="workbench-memory.md"'},
    )


# --------------------------------------------------------------------------- #
# a chat and its notes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ModelFacts:
    """What the gateway says of the model a chat uses: its window, and where
    it runs (`local`, `external` or `unknown`), with whom."""

    id: str
    window: int | None
    locality: str
    who: str | None
    #: Whether it takes a PDF as a file (`file_input`): documents use this.
    file_input: bool = False
    #: Whether a request for it may carry tools (`tool_calling`; assumed when not said).
    tools: bool = True


@dataclass
class Decision:
    """Whether a chat's next request carries the person's notes, and if not,
    why, in words a person reads."""

    send: bool
    code: str | None = None
    reason: str | None = None
    hint: str | None = None
    facts: ModelFacts | None = None
    #: How far notes may travel, for a model outside the machines
    #: (`resolve_external`), and whose word that is.
    choice: str | None = None
    set_by: str | None = None


async def model_facts(hub: Hub, model_id: str) -> ModelFacts:
    """Ask the gateway about a model. Raises `HubError` when it cannot say.
    A model the listing does not name is `unknown`, which is not trusted."""
    listing = await hub.models()
    entry = next((m for m in listing.get("data") or [] if m.get("id") == model_id), None)
    if entry is None:
        return ModelFacts(model_id, None, "unknown", None)
    info = entry.get("x_eugene_plexus") or {}
    window = info.get("context_length")
    common = served(entry)
    return ModelFacts(
        id=model_id,
        window=window if isinstance(window, int) and not isinstance(window, bool) else None,
        locality=str(common["locality"]),
        who=common["provider"] or common["account"],
        file_input=bool(info.get("file_input")),
        tools=info.get("tool_calling") is not False,
    )


def _switches(install: config.MemorySettings, prefs: dict[str, Any], chat: Chat) -> Decision | None:
    """The settings that say no whatever the model is, or None."""
    if not install.enabled:
        return Decision(False, "install_off", _OWNER_OFF)
    if not prefs["enabled"]:
        return Decision(False, "person_off", _PERSON_OFF)
    if chat.settings.get("memory") == "off":
        return Decision(False, "chat_off", "Memory is off for this chat.")
    return None


def _placement(
    facts: ModelFacts, install: config.MemorySettings, prefs: dict[str, Any], chat: Chat
) -> Decision:
    """Whether notes may go to this model: its window, and where it runs."""
    small = memory.window_reason(facts.window)
    if small is not None:
        return Decision(False, "window_too_small", small, facts=facts)
    if facts.locality == "local":
        return Decision(True, facts=facts)
    choice, set_by = resolve_external(install.external, prefs["external"])
    if choice == "always" or (choice == "ask" and chat.settings.get("memoryExternal") is True):
        return Decision(True, facts=facts, choice=choice, set_by=set_by)
    if facts.locality == "external":
        reason = (
            f"Your notes were not sent: this chat uses {facts.who or 'another service'}, "
            "which runs outside your machines."
        )
    else:
        reason = (
            "Your notes were not sent: Eugene cannot tell whether this chat's model runs on "
            "your machines or on an outside account."
        )
    if choice == "ask":
        hint = "Choose Send for this chat to send them anyway."
    elif set_by == "owner":
        hint = "The owner of this Workbench has set notes never to go to such models."
    else:
        hint = "You have set notes never to go to such models."
    return Decision(False, "outside_model", reason, hint, facts=facts, choice=choice, set_by=set_by)


async def _decide(request: Request, person: Person, chat: Chat, model_id: str | None) -> Decision:
    """Every reason a chat's next request would carry no notes, in the order a
    person can act on them. The request and the chat's drawer both ask this,
    so the drawer says what the request does."""
    return await decide(_state(request), person, chat, model_id)


async def decide(state: Any, person: Person, chat: Chat, model_id: str | None) -> Decision:
    """`_decide`, for a caller that holds Workbench's state rather than a request."""
    store: Store = state.store
    install = await config.memory_settings(store)
    prefs = await store.memory_prefs(person.sub)
    if (off := _switches(install, prefs, chat)) is not None:
        return off
    if not model_id:
        return Decision(False, "no_model", "Choose a model first, and notes can be sent to it.")
    try:
        facts = await model_facts(state.hub, model_id)
    except HubError as exc:
        return Decision(
            False,
            "model_unknown",
            f"Workbench could not ask Eugene about this model ({exc.message}), so no notes "
            "were sent.",
        )
    return _placement(facts, install, prefs, chat)


def _today() -> str:
    """Today on this machine, as the dates events and projects carry are written."""
    return datetime.now().date().isoformat()


async def _pinned(
    store: Store,
    person: Person,
    facts: ModelFacts,
    prefs: dict[str, Any],
    notes: list[MemoryNote] | None = None,
    *,
    chat: Chat | None = None,
) -> memory.Snapshot:
    """The pinned block for this person and model, as it would be taken now: of the notes
    `chat` may be given (MV7: its project's, and general ones as its project says)."""
    held = notes if notes is not None else await projects.chat_notes(store, person.sub, chat)
    if facts.locality != "local":
        held = await leaving(store, person.sub, held)
    always = [n for n in held if memory.is_always(n)]
    return memory.build_snapshot(
        always,
        budget=memory.budget_for(facts.window, prefs["budget_tokens"]),
        window=facts.window,
        model=facts.id,
        now=time.time(),
        today=_today(),
        bodies=await _bodies(store, person.sub, always),
    )


async def _freeze(
    store: Store, person: Person, chat: Chat, facts: ModelFacts, prefs: dict[str, Any]
) -> memory.Snapshot:
    """Choose the pinned block for this chat and model, and keep it with the chat. Nothing
    is kept when there is nothing to send."""
    snapshot = await _pinned(store, person, facts, prefs, chat=chat)
    if snapshot.text:
        await store.set_memory_snapshot(chat.id, person.sub, snapshot.to_json())
        await store.mark_memory_used(person.sub, snapshot.ids, snapshot.frozen_at)
        log.info(
            "%d pinned note(s), %d of %d tokens, went with a chat's first request%s",
            len(snapshot.sent),
            snapshot.tokens,
            snapshot.budget,
            f"; {len(snapshot.dropped)} did not fit" if snapshot.dropped else "",
        )
    return snapshot


async def _current_pinned(
    request: Request, person: Person, chat: Chat, model_id: str, *, freeze: bool
) -> tuple[memory.Snapshot | None, Decision | None]:
    """The chat's pinned block for this model, and the decision that let notes go; `(None,
    None)` when none may go. Takes one at the first request that carries notes, and again
    for another model."""
    store = _store(request)
    install = await config.memory_settings(store)
    prefs = await store.memory_prefs(person.sub)
    if _switches(install, prefs, chat) is not None:
        return None, None
    snapshot = memory.Snapshot.from_json(chat.memory_snapshot)
    if snapshot is None and await store.memory_count(person.sub) == 0:
        return None, None  # nothing to send, so there is no model to ask about
    facts = await model_facts(_hub(request), model_id)
    decision = _placement(facts, install, prefs, chat)
    if not decision.send:
        return None, None
    if snapshot is None or snapshot.model != model_id:
        snapshot = (
            await _freeze(store, person, chat, facts, prefs)
            if freeze
            else await _pinned(store, person, facts, prefs, chat=chat)
        )
    return snapshot, decision


async def block_for(
    request: Request, person: Person, chat: Chat, model_id: str, *, freeze: bool = True
) -> str | None:
    """The pinned block the next request carries in its system message, or None for none.

    Reuses the chat's snapshot byte for byte; takes one at the first request
    that carries notes, and again for another model. Never raises: memory
    does not stop an answer. With `freeze=False` nothing is kept and no note
    is counted as used: the block a request *would* carry, for a check made
    before one is sent."""
    try:
        snapshot, _ = await _current_pinned(request, person, chat, model_id, freeze=freeze)
        return (snapshot.text or None) if snapshot is not None else None
    except Exception as exc:
        log.warning("a chat's notes were left out of its request: %s: %s", type(exc).__name__, exc)
        return None


@dataclass
class TurnMemory:
    """What one turn carries of the person's notes, chosen for it (MV1): the block for their
    latest message, or None; what the answer keeps of it (`record`); and whether this
    request may carry notes at all (which `search_memory` and the replay of its results
    follow), and whether the tool is offered."""

    block: str | None = None
    record: dict[str, Any] | None = None
    tokens: int = 0
    allowed: bool = False
    tool: bool = False
    #: The decision's facts, for the tool's own budget.
    facts: ModelFacts | None = None
    found: list[str] = field(default_factory=list)


async def turn_for(
    request: Request,
    person: Person,
    chat: Chat,
    history: list[Message],
    model_id: str,
    *,
    used: int,
    reserve: int,
    spent: int = 0,
) -> TurnMemory:
    """Relevant memory for the turn about to be asked: chosen from the person's latest
    message (and the one before it), in budget less what past chats took of it (`spent`,
    MV2), and in the room the window has left after `used` tokens and the answer's
    `reserve`. Never raises: memory does not stop an answer."""
    try:
        return await _turn_for(request, person, chat, history, model_id, used, reserve, spent)
    except Exception as exc:
        log.warning(
            "a turn's relevant notes were left out of its request: %s: %s", type(exc).__name__, exc
        )
        return TurnMemory()


async def _turn_for(
    request: Request,
    person: Person,
    chat: Chat,
    history: list[Message],
    model_id: str,
    used: int,
    reserve: int,
    spent: int = 0,
) -> TurnMemory:
    store = _store(request)
    fresh = await store.chat(chat.id) or chat
    snapshot, decision = await _current_pinned(request, person, fresh, model_id, freeze=False)
    if decision is None or decision.facts is None:
        return TurnMemory()
    prefs = await store.memory_prefs(person.sub)
    notes = await projects.chat_notes(store, person.sub, fresh)
    if decision.facts.locality != "local":
        notes = await leaving(store, person.sub, notes)
    others = [n for n in notes if not memory.is_always(n)]
    deferred = snapshot.deferred if snapshot is not None else []
    facts = decision.facts
    tool = bool(prefs.get("search_tool", True)) and facts.tools and bool(others or deferred)
    out = TurnMemory(allowed=True, tool=tool, facts=facts)
    users = [m for m in history if m.role == "user"]
    latest = users[-1].content if users else ""
    if not latest.strip() or not (others or deferred):
        return out
    budget = max(
        0,
        memory.relevant_budget_for(facts.window, prefs.get("relevant_budget_tokens")) - spent,
    )
    room = None if facts.window is None else max(0, facts.window - used - reserve)
    allowed = budget if room is None else min(budget, room)
    chosen = await _finder(_state(request)).choose(
        person.sub,
        latest,
        users[-2].content if len(users) >= 2 else None,
        notes,
        pinned=snapshot.ids if snapshot is not None else [],
        deferred=deferred,
        today=_today(),
        everything=prefs.get("use_mode") == "all",
    )
    turn = memory.pack_relevant(chosen.candidates, allowed)
    record = {
        **turn.record(),
        "how": chosen.how,
        "why": chosen.why,
        "from": chosen.of,
        "window": facts.window,
        "room": room,
    }
    if turn.sent:
        await store.mark_memory_used(person.sub, [c.id for c in turn.sent], time.time())
        log.info(
            "%d relevant note(s), %d of %d tokens, went with a turn (%s)",
            len(turn.sent),
            turn.tokens,
            allowed,
            chosen.how,
        )
    out.block = turn.text or None
    out.record = record
    out.tokens = turn.tokens
    out.found = [c.id for c in turn.sent]
    return out


# --------------------------------------------------------------------------- #
# search_memory: the tool a model may call (MV1)
# --------------------------------------------------------------------------- #


async def run_search_tool(
    state: Any,
    person: Person,
    chat_id: str,
    model_id: str | None,
    arguments: dict[str, Any],
) -> str:
    """One `search_memory` call for this person: the notes that match, as the same delimited
    data a turn carries, at most `SEARCH_TOOL_TOKENS`. The decision is asked again as the
    call runs (MV2 found it was not): a switch turned off, or the chat moved to a model
    notes may not go to, while the answer ran, withholds it. Never raises: what went wrong
    is the result, in words."""
    owner = person.sub
    query = str(arguments.get("query") or "").strip()[:500]
    limit = arguments.get("limit") or 8
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
        limit = 8
    if not query:
        return "Nothing to search for: give a query."
    try:
        chat = await state.store.chat(chat_id)
        if chat is None or chat.owner != owner:
            return "This chat is gone."
        decision = await decide(state, person, chat, model_id)
        if not decision.send:
            return memory.WITHHELD
        notes = await projects.chat_notes(state.store, owner, chat)
        if decision.facts is None or decision.facts.locality != "local":
            notes = await leaving(state.store, owner, notes)
        chosen = await _finder(state).search(owner, query, notes, today=_today())
        turn = memory.pack_relevant(chosen.candidates[:limit], memory.SEARCH_TOOL_TOKENS)
    except Exception as exc:
        log.warning("search_memory failed: %s: %s", type(exc).__name__, exc)
        return f"Searching the person's notes failed ({type(exc).__name__})."
    if not turn.sent:
        return "No note matched."
    return turn.text


# --------------------------------------------------------------------------- #
# the chat's Memory drawer
# --------------------------------------------------------------------------- #


def _turn_view(message: Message | None) -> dict[str, Any] | None:
    """What one answer's turn was sent, as the drawer shows it."""
    if message is None or message.memory_used is None:
        return None
    return {"messageId": message.id, **message.memory_used}


async def chat_memory(
    request: Request,
    person: Person,
    chat: Chat,
    *,
    read_only: bool,
    refresh: bool = False,
    message_id: str | None = None,
) -> dict[str, Any]:
    """What a chat's notes are, as its drawer shows them: the pinned block (what the model
    is shown in every turn, what it was not and why), what one turn was sent of relevant
    memory (the latest answer's, or `message_id`'s), and whether the next request carries
    notes.

    For the owner reading someone else's chat (`read_only`), counts only: the
    chat's saved copy and the answers' records are read, and the notes table is not."""
    store = _store(request)
    saved = memory.Snapshot.from_json(chat.memory_snapshot)
    answers = [m for m in await store.messages(chat.id) if m.role == "assistant"]
    chosen_turn = (
        next(
            (m for m in answers if m.id == message_id),
            None,
        )
        if message_id
        else next((m for m in reversed(answers) if m.memory_used), None)
    )
    turn = _turn_view(chosen_turn)
    turns = [m.id for m in answers if m.memory_used]
    if read_only:
        return {
            "chatId": chat.id,
            "readOnly": True,
            "frozen": saved is not None,
            "frozenAt": saved.frozen_at if saved else None,
            "tokens": saved.tokens if saved else 0,
            "budget": saved.budget if saved else None,
            "counts": {
                "sent": len(saved.sent) if saved else 0,
                "notSent": len(saved.dropped) if saved else 0,
            },
            "turn": (
                {
                    "messageId": turn["messageId"],
                    "notes": len(turn.get("notes") or []),
                    "tokens": turn.get("tokens"),
                    "budget": turn.get("budget"),
                    "pastChats": len((turn.get("pastChats") or {}).get("exchanges") or []),
                    "memoryServer": len((turn.get("memoryServer") or {}).get("memories") or []),
                }
                if turn
                else None
            ),
        }
    if refresh:
        # Take the notes again: the copy the chat kept goes, whatever follows.
        await store.set_memory_snapshot(chat.id, person.sub, None)
        saved = None
    prefs = await store.memory_prefs(person.sub)
    notes = await projects.chat_notes(store, person.sub, chat)
    decision = await _decide(request, person, chat, chat.model)
    sending = decision.send and bool(notes)
    out: dict[str, Any] = {
        "chatId": chat.id,
        "readOnly": False,
        "sending": sending,
        "reason": decision.reason,
        "reasonCode": decision.code,
        "hint": decision.hint,
        "chatSwitch": "off" if chat.settings.get("memory") == "off" else "on",
        "model": (
            {
                "id": decision.facts.id,
                "window": decision.facts.window,
                "locality": decision.facts.locality,
                "runsOn": decision.facts.who,
            }
            if decision.facts
            else None
        ),
        # Set only for a model that runs outside the machines.
        "outside": (
            {
                "choice": decision.choice,
                "setBy": decision.set_by,
                "canSendForThisChat": decision.choice == "ask",
                "sendForThisChat": chat.settings.get("memoryExternal") is True,
            }
            if decision.choice is not None
            else None
        ),
        "counts": {"notes": len(notes), "sent": 0, "notSent": 0},
        "frozen": False,
        "frozenAt": None,
        "changed": False,
        "budget": None,
        "tokens": 0,
        "text": None,
        "sent": [],
        "notSent": [],
        # MV1: what a turn was sent, chosen for it, and which answers have a record.
        "turn": turn,
        "turns": turns,
        "relevantBudget": None,
        "searchTool": None,
        # MV2: Use past chats in this chat, and whether this chat's model may receive them.
        "pastChats": await _past_chats_state(request, person, chat),
    }
    if decision.send and not notes:
        out["reason"], out["reasonCode"] = "You have no notes yet.", "no_notes"
    if not sending or decision.facts is None:
        return out
    facts = decision.facts
    out["relevantBudget"] = memory.relevant_budget_for(
        facts.window, prefs.get("relevant_budget_tokens")
    )
    out["searchTool"] = bool(prefs.get("search_tool", True)) and facts.tools
    snapshot = saved if saved is not None and saved.model == facts.id else None
    if snapshot is None:
        snapshot = (
            await _freeze(store, person, chat, facts, prefs)
            if refresh
            else await _pinned(store, person, facts, prefs, notes)
        )
        frozen = refresh and bool(snapshot.text)
    else:
        frozen = True
    kept = {n.id: n for n in notes}
    not_sent: list[dict[str, Any]] = []
    sent_ids = set(snapshot.ids)
    for dropped in snapshot.dropped:
        if (note := kept.get(dropped.id)) is not None:
            not_sent.append(
                {
                    **note_view(note),
                    "reason": dropped.reason,
                    "reasonText": memory.reason_text(dropped.reason, snapshot.budget),
                }
            )
    seen = sent_ids | {d.id for d in snapshot.dropped}
    if frozen:
        not_sent.extend(
            {
                **note_view(n),
                "reason": memory.ADDED_SINCE,
                "reasonText": memory.added_since_reason(),
            }
            for n in notes
            if memory.is_always(n) and n.id not in seen
        )
        live = await _pinned(store, person, facts, prefs, notes)
        out["changed"] = [s for s in live.sent] != [s for s in snapshot.sent] or [
            (d.id, d.reason) for d in live.dropped
        ] != [(d.id, d.reason) for d in snapshot.dropped]
    out.update(
        frozen=frozen,
        frozenAt=snapshot.frozen_at if frozen else None,
        budget=snapshot.budget,
        tokens=snapshot.tokens,
        text=snapshot.text or None,
        sent=[
            {
                "id": s.id,
                "text": s.text,
                "kind": s.kind,
                "pinned": s.pinned,
                "always": s.pinned or s.kind == "instruction",
                "body": s.body,
            }
            for s in snapshot.sent
        ],
        notSent=not_sent,
    )
    out["counts"] = {"notes": len(notes), "sent": len(snapshot.sent), "notSent": len(not_sent)}
    return out


async def _past_chats_state(request: Request, person: Person, chat: Chat) -> dict[str, Any]:
    """What the drawer says of past chats in this chat (MV2): whether the person turned them
    on, whether this chat's model may receive them and why not, and whether models may
    search them."""
    from . import past_chats

    state = _state(request)
    decision = await past_chats.decide(state, person, chat, chat.model)
    prefs = await _store(request).memory_prefs(person.sub)
    return {
        "on": chat.settings.get("pastChats") is True,
        "allowed": decision.send,
        "reason": None if decision.send else decision.reason,
        "reasonCode": None if decision.send else decision.code,
        "hint": None if decision.send else decision.hint,
        "searchTool": bool(prefs.get("chats_tool", True))
        and decision.send
        and bool(decision.facts and decision.facts.tools),
        "budget": (
            memory.relevant_budget_for(decision.facts.window, prefs.get("relevant_budget_tokens"))
            // past_chats.TURN_SHARE
            if decision.send and decision.facts
            else None
        ),
    }


@router.get("/api/chats/{chat_id}/memory")
async def get_chat_memory(
    request: Request, chat_id: str, message: str | None = None
) -> dict[str, Any]:
    """What this chat's model is shown of the person's notes, and what it is
    not, with the reason; and what one turn (`message`, else the latest) was sent. The
    chat's owner sees the notes; the install's owner, reading it read-only, sees counts."""
    person = await _person(request)
    chat, read_only = await _readable_chat(request, person, chat_id)
    return await chat_memory(request, person, chat, read_only=read_only, message_id=message)


@router.post("/api/chats/{chat_id}/memory/refresh")
async def refresh_chat_memory(request: Request, chat_id: str) -> dict[str, Any]:
    """Take the pinned notes again for this chat, from now: the next request, and
    every one after it, carries what is returned."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    return await chat_memory(request, person, chat, read_only=False, refresh=True)


# --------------------------------------------------------------------------- #
# "remember that ..." in a person's own message
# --------------------------------------------------------------------------- #


async def handle_command(
    request: Request, person: Person, chat: Chat, message: Message
) -> dict[str, Any] | None:
    """What a person's message does to their notes, if it begins with a
    command (`memory.parse_command`); None when it does not. The message is
    sent to the model either way.

    "Remember" keeps the person's words, or says why not. "Forget" only finds
    candidates, and the person chooses: it never deletes on a guess. Never
    raises: a note that could not be kept does not stop the message."""
    command = memory.parse_command(message.content)
    if command is None:
        return None
    try:
        store = _store(request)
        if command.verb == "forget":
            words = memory.search_words(command.text)
            expression = memory.match_expression(words)
            if expression is None:
                return None
            # MV7: only notes this chat may be given; another place's are not offered.
            place = await projects.place_of(store, chat)
            found = projects.in_place(
                await store.search_memory(person.sub, expression, limit=50), place
            )[:5]
            return {
                "command": "forget",
                "words": " ".join(words),
                "candidates": [note_view(n) for n in found],
            }
        install = await config.memory_settings(store)
        prefs = await store.memory_prefs(person.sub)
        if not install.enabled:
            return {"command": "remember", "refused": {"message": _OWNER_OFF, "reason": "off"}}
        if not prefs["enabled"]:
            return {
                "command": "remember",
                "refused": {
                    "message": "Memory is off for you, so nothing was kept. Turn it on in Memory.",
                    "reason": "off",
                },
            }
        try:
            text = memory.validate_note(command.text)
        except memory.NoteRefused as exc:
            return {"command": "remember", "refused": exc.view()}
        now = time.time()
        try:
            kept, created = await store.add_memory_note(
                MemoryNote(
                    id=_new_id(),
                    owner=person.sub,
                    text=text,
                    kind=memory.DEFAULT_KIND,
                    created_at=now,
                    updated_at=now,
                    source="said",
                    source_chat_id=chat.id,
                    source_message_id=message.id,
                    project_id=chat.project_id,
                ),
                limit=install.max_notes,
            )
        except MemoryFull as exc:
            return {"command": "remember", "refused": _full(exc.limit)}
        if created:
            await _reindex(_state(request), person.sub, [kept.id])
        return {"command": "remember", "remembered": note_view(kept), "created": created}
    except Exception as exc:
        log.warning("a memory command failed: %s: %s", type(exc).__name__, exc)
        return {
            "command": command.verb,
            "refused": {
                "message": f"Workbench could not do that with your notes ({type(exc).__name__}). "
                "Your message was still sent.",
                "reason": "error",
            },
        }
