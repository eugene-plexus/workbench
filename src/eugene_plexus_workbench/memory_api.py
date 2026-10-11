"""Memory's routes, and what a chat's request does with a person's notes (MR1).

The rules themselves -- what a note may be, what a model is shown, what a
command is -- are `memory.py`, pure. This module is the part that reads the
store, asks the gateway where a model runs, and answers the page.

**Everything is the asker's.** Every route takes the person from the session
and nothing from the client, and every store call puts that person in its
own query, so **a note that is not yours is the same 404 as one that does
not exist** (W4). `ownerReadsChats` does not reach memory: there is no route
that reads another person's notes, and in the owner's read-only view of a
chat the memory shows counts, taken from the chat's saved copy, without ever
reading the notes table (`workbench-memory.md`).

**A chat is sent its notes once, then the same bytes.** The first request that
carries notes keeps a snapshot with the chat (`memory.Snapshot`), and every
later turn sends that text again so the engine's prompt cache holds. A
request for another model takes a new one: its window sets the budget and
where it runs sets whether notes may go. "Refresh memory" takes a new one on
request. A note that is deleted is dropped from every chat's copy at once
(`Store.delete_memory_note`): a deleted note is not sent again.

**Notes go to a model that runs on the person's own machines.** For one that
runs outside them, or that Eugene cannot place, they are held back unless
the person has said otherwise (MRC6): `ask` (the default) holds them until
the person chooses "Send for this chat", `never` always holds them, `always`
sends them. The install's `memoryExternalModels` is the default for a person
who has not chosen, except that its `never` is the owner's rule and final
(`resolve_external`): an owner can forbid it for everyone, and otherwise a
person's own choice stands.

**Memory never stops an answer.** Anything that goes wrong choosing the block
is logged with its cause and the answer is sent without notes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import config, memory
from .api import _new_id, _own_chat, _person, _problem, _readable_chat, _state
from .hub import Hub, HubError
from .media_api import _common as served
from .store import Chat, MemoryFull, MemoryNote, Message, Person, Store

log = logging.getLogger(__name__)

router = APIRouter()

_OWNER_OFF = "The owner of this Workbench has turned Memory off."
_PERSON_OFF = "Memory is off for you. Turn it on in Memory to share notes with models."


def _store(request: Request) -> Store:
    store: Store = _state(request).store
    return store


def _hub(request: Request) -> Hub:
    hub: Hub = _state(request).hub
    return hub


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


def note_view(note: MemoryNote) -> dict[str, Any]:
    return {
        "id": note.id,
        "text": note.text,
        "kind": note.kind,
        "pinned": note.pinned,
        # Instructions and pinned notes are used first (the page's "Always used").
        "always": memory.is_always(note),
        "source": note.source,
        "sourceChatId": note.source_chat_id,
        "sourceMessageId": note.source_message_id,
        "createdAt": note.created_at,
        "updatedAt": note.updated_at,
        "lastUsedAt": note.last_used_at,
        "useCount": note.use_count,
    }


def prefs_view(prefs: dict[str, Any], install: config.MemorySettings) -> dict[str, Any]:
    """What a person has set, what is in effect now, and whose word it is.

    A setting the person has not made is `null`, and `effective` says what
    that comes to. The page shows `effective`, never a stand-in that looks like
    a choice (settings never lie)."""
    external, set_by = resolve_external(install.external, prefs["external"])
    return {
        "enabled": prefs["enabled"],
        "external": prefs["external"],
        "budgetTokens": prefs["budget_tokens"],
        "effective": {
            "enabled": install.enabled and prefs["enabled"],
            "disabledBy": ("owner" if not install.enabled else None if prefs["enabled"] else "you"),
            "external": external,
            "externalSetBy": set_by,
            "budgetTokens": prefs["budget_tokens"] or memory.BUDGET_CEILING,
        },
    }


def _limits(install: config.MemorySettings) -> dict[str, Any]:
    return {
        "maxChars": memory.MAX_NOTE_CHARS,
        "maxNotes": install.max_notes,
        "kinds": list(memory.KINDS),
        "minWindow": memory.MIN_WINDOW,
        "budgetCeiling": memory.BUDGET_CEILING,
        "budgetSharePercent": memory.BUDGET_SHARE_PERCENT,
        "budgetFloor": memory.BUDGET_FLOOR,
    }


# --------------------------------------------------------------------------- #
# the Memory page
# --------------------------------------------------------------------------- #


@router.get("/api/memory")
async def get_memory(request: Request) -> dict[str, Any]:
    """The asker's notes by section, their settings, and what the notes cost.

    `usage.tokens` is what the notes would take of `usage.budget` in a chat
    whose model has room; a chat on a small window gets less, and its own
    drawer (`GET /api/chats/{id}/memory`) says how much."""
    person = await _person(request)
    store = _store(request)
    install = await config.memory_settings(store)
    prefs = await store.memory_prefs(person.sub)
    notes = await store.memory_notes(person.sub)
    budget = memory.budget_for(None, prefs["budget_tokens"])
    shown = memory.build_snapshot(notes, budget=budget, window=None, model=None, now=0.0)
    return {
        "installEnabled": install.enabled,
        "ownerReadsChats": await config.owner_reads_chats(store),
        "prefs": prefs_view(prefs, install),
        "sections": {
            "always": [note_view(n) for n in notes if memory.is_always(n)],
            "other": [note_view(n) for n in notes if not memory.is_always(n)],
        },
        "usage": {
            "tokens": shown.tokens,
            "budget": budget,
            "notes": len(notes),
            "maxNotes": install.max_notes,
            "sent": len(shown.sent),
            "notSent": len(shown.dropped),
        },
        "limits": _limits(install),
    }


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=2000)
    kind: Literal["fact", "preference", "instruction", "project", "event"] = "fact"
    pinned: StrictBool = False
    #: `button` for a message's Remember; then the chat and message it came from.
    source: Literal["typed", "button"] = "typed"
    chatId: str | None = Field(default=None, max_length=200)
    messageId: str | None = Field(default=None, max_length=200)


class NoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(default=None, max_length=2000)
    kind: Literal["fact", "preference", "instruction", "project", "event"] | None = None
    pinned: StrictBool | None = None


class PrefsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool | None = None
    external: Literal["never", "ask", "always"] | None = None
    budgetTokens: int | None = Field(default=None, ge=memory.BUDGET_FLOOR, le=20000, strict=True)


def _vetted(text: str) -> str:
    try:
        return memory.validate_note(text)
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
    chat_id = message_id = None
    if body.chatId is not None:
        chat = await _own_chat(request, person, body.chatId)
        chat_id = chat.id
        if body.messageId is not None:
            message = await store.message(body.messageId)
            if message is None or message.chat_id != chat.id:
                raise _problem(status.HTTP_404_NOT_FOUND, "There is no such message in this chat.")
            message_id = message.id
    elif body.messageId is not None:
        raise _problem(status.HTTP_400_BAD_REQUEST, "A message needs the chat it is in.")
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
    )
    try:
        kept, created = await store.add_memory_note(note, limit=install.max_notes)
    except MemoryFull as exc:
        raise _refused(status.HTTP_409_CONFLICT, _full(exc.limit)) from None
    return JSONResponse(
        {"note": note_view(kept), "created": created},
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@router.patch("/api/memory/notes/{note_id}")
async def edit_note(request: Request, note_id: str, body: NoteUpdate) -> dict[str, Any]:
    person = await _person(request)
    store = _store(request)
    if not (await config.memory_settings(store)).enabled:
        raise _refused(status.HTTP_403_FORBIDDEN, {"message": _OWNER_OFF, "reason": "off"})
    text = _vetted(body.text) if body.text is not None else None
    note = await store.update_memory_note(
        person.sub, note_id, text=text, kind=body.kind, pinned=body.pinned
    )
    if note is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such note.")
    return {"note": note_view(note)}


@router.delete("/api/memory/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(request: Request, note_id: str) -> Response:
    """Forget a note for real: it, its index entry, its log's words and every
    chat's copy of it. Allowed whatever the owner has set."""
    person = await _person(request)
    if not await _store(request).delete_memory_note(person.sub, note_id):
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
    return {"deleted": await _store(request).delete_memory_notes(person.sub)}


@router.put("/api/memory/prefs")
async def put_prefs(request: Request, body: PrefsUpdate) -> dict[str, Any]:
    """Change the settings sent; the others stay. `null` returns one to its
    default."""
    person = await _person(request)
    store = _store(request)
    values: dict[str, Any] = {}
    defaults = {"enabled": True, "external": None, "budgetTokens": None}
    columns = {"enabled": "enabled", "external": "external", "budgetTokens": "budget_tokens"}
    for key in body.model_fields_set:
        given = getattr(body, key)
        values[columns[key]] = defaults[key] if given is None else given
    prefs = await store.put_memory_prefs(person.sub, **values)
    return prefs_view(prefs, await config.memory_settings(store))


def _day(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).strftime("%Y-%m-%d")


def _stamp(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("/api/memory/export")
async def export(request: Request, format: Literal["md", "json"] = "md") -> Response:
    """The asker's notes as a plain file: Markdown to read, JSON to keep."""
    person = await _person(request)
    notes = await _store(request).memory_notes(person.sub)
    now = time.time()
    headers = {"Cache-Control": "private, no-store"}
    if format == "json":
        return JSONResponse(
            {
                "exportedAt": _stamp(now),
                "notes": [
                    {
                        "text": n.text,
                        "kind": n.kind,
                        "pinned": n.pinned,
                        "source": n.source,
                        "createdAt": _stamp(n.created_at),
                        "updatedAt": _stamp(n.updated_at),
                    }
                    for n in notes
                ],
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
    ):
        lines += [f"## {title}", ""]
        if not members:
            lines += ["Nothing here.", ""]
        for n in members:
            kept = f"{n.kind}{', pinned' if n.pinned else ''}, added {_day(n.created_at)}"
            lines.append(f"- {n.text} *({kept})*")
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
    store = _store(request)
    install = await config.memory_settings(store)
    prefs = await store.memory_prefs(person.sub)
    if (off := _switches(install, prefs, chat)) is not None:
        return off
    if not model_id:
        return Decision(False, "no_model", "Choose a model first, and notes can be sent to it.")
    try:
        facts = await model_facts(_hub(request), model_id)
    except HubError as exc:
        return Decision(
            False,
            "model_unknown",
            f"Workbench could not ask Eugene about this model ({exc.message}), so no notes "
            "were sent.",
        )
    return _placement(facts, install, prefs, chat)


async def _freeze(
    store: Store, person: Person, chat: Chat, facts: ModelFacts, prefs: dict[str, Any]
) -> memory.Snapshot:
    """Choose the notes for this chat and model, and keep what was chosen with
    the chat. Nothing is kept when there is nothing to send."""
    notes = await store.memory_notes(person.sub)
    now = time.time()
    snapshot = memory.build_snapshot(
        notes,
        budget=memory.budget_for(facts.window, prefs["budget_tokens"]),
        window=facts.window,
        model=facts.id,
        now=now,
    )
    if snapshot.text:
        await store.set_memory_snapshot(chat.id, person.sub, snapshot.to_json())
        await store.mark_memory_used(person.sub, snapshot.ids, now)
        log.info(
            "%d memory note(s), %d of %d tokens, went with a chat's first request%s",
            len(snapshot.sent),
            snapshot.tokens,
            snapshot.budget,
            f"; {len(snapshot.dropped)} did not fit" if snapshot.dropped else "",
        )
    return snapshot


async def block_for(request: Request, person: Person, chat: Chat, model_id: str) -> str | None:
    """The block of notes the next request carries, or None for none.

    Reuses the chat's snapshot byte for byte; takes one at the first request
    that carries notes, and again for another model. Never raises: memory
    does not stop an answer."""
    try:
        store = _store(request)
        install = await config.memory_settings(store)
        prefs = await store.memory_prefs(person.sub)
        if _switches(install, prefs, chat) is not None:
            return None
        snapshot = memory.Snapshot.from_json(chat.memory_snapshot)
        if snapshot is None and await store.memory_count(person.sub) == 0:
            return None  # nothing to send, so there is no model to ask about
        facts = await model_facts(_hub(request), model_id)
        if not _placement(facts, install, prefs, chat).send:
            return None
        if snapshot is None or snapshot.model != model_id:
            snapshot = await _freeze(store, person, chat, facts, prefs)
        return snapshot.text or None
    except Exception as exc:
        log.warning("a chat's notes were left out of its request: %s: %s", type(exc).__name__, exc)
        return None


async def chat_memory(
    request: Request, person: Person, chat: Chat, *, read_only: bool, refresh: bool = False
) -> dict[str, Any]:
    """What a chat's notes are, as its drawer shows them: what the model was
    shown, what it was not and why, and whether the next request carries them.

    For the owner reading someone else's chat (`read_only`), counts only: the
    chat's saved copy is read, and the notes table is not."""
    saved = memory.Snapshot.from_json(chat.memory_snapshot)
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
        }
    store = _store(request)
    if refresh:
        # Take the notes again: the copy the chat kept goes, whatever follows.
        await store.set_memory_snapshot(chat.id, person.sub, None)
        saved = None
    prefs = await store.memory_prefs(person.sub)
    notes = await store.memory_notes(person.sub)
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
    }
    if decision.send and not notes:
        out["reason"], out["reasonCode"] = "You have no notes yet.", "no_notes"
    if not sending or decision.facts is None:
        return out
    facts = decision.facts
    snapshot = saved if saved is not None and saved.model == facts.id else None
    if snapshot is None:
        if refresh:
            snapshot = await _freeze(store, person, chat, facts, prefs)
        else:
            snapshot = memory.build_snapshot(
                notes,
                budget=memory.budget_for(facts.window, prefs["budget_tokens"]),
                window=facts.window,
                model=facts.id,
                now=time.time(),
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
                    "reasonText": memory.over_budget_reason(snapshot.budget),
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
            if n.id not in seen
        )
        live = memory.build_snapshot(
            notes,
            budget=snapshot.budget,
            window=snapshot.window,
            model=snapshot.model,
            now=snapshot.frozen_at,
        )
        out["changed"] = live.hash != snapshot.hash
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
            }
            for s in snapshot.sent
        ],
        notSent=not_sent,
    )
    out["counts"] = {"notes": len(notes), "sent": len(snapshot.sent), "notSent": len(not_sent)}
    return out


@router.get("/api/chats/{chat_id}/memory")
async def get_chat_memory(request: Request, chat_id: str) -> dict[str, Any]:
    """What this chat's model is shown of the person's notes, and what it is
    not, with the reason. The chat's owner sees the notes; the install's owner,
    reading it read-only, sees counts."""
    person = await _person(request)
    chat, read_only = await _readable_chat(request, person, chat_id)
    return await chat_memory(request, person, chat, read_only=read_only)


@router.post("/api/chats/{chat_id}/memory/refresh")
async def refresh_chat_memory(request: Request, chat_id: str) -> dict[str, Any]:
    """Take the notes again for this chat, from now: the next request, and
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
            found = await store.search_memory(person.sub, expression, limit=5)
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
                ),
                limit=install.max_notes,
            )
        except MemoryFull as exc:
            return {"command": "remember", "refused": _full(exc.limit)}
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
