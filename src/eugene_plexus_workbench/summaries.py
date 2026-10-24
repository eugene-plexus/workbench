"""Summaries of a long chat's earlier messages (row 26): the summary call, the store and merging.

The rules are `long_chats.py` (pure); the request is built in `conversation.py`. This module reads
the store, asks the gateway about the model, writes summaries with the chat's own model, and
answers the page.

**The summary request is the request before plus one message.** It replays the previous request's
messages up to the end of the run, the same bytes (the system message, the head, the summaries
already in use, every message after them), then asks, in one user message, for the summary. The
engine reuses its cache of all of it but the question, and the first messages being the same, the
gateway sends it to the same replica. It carries no tools and no web search. A request may not
carry the person's notes or past chats (an outside model, memory off): the run is replayed with
the same allowance, and a summary records what it carries, so a later request that may not carry
those never uses it.

**A summary is a new copy of chat text** (AGENTS.md: said to Troy). It is kept in the same store,
deleted with its chat and with a branch delete that removes a message of its run, never indexed
as a past chat, never read by memory learning, never exported: the originals are.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from . import config, context, conversation, files, long_chats
from .answers import Progress, Refused
from .hub import Hub, HubError
from .long_chats import Item, Kept, Limits, Plan, Record
from .store import Chat, FileRecord, Message, Person, Store, SummaryRow

log = logging.getLogger(__name__)

router = APIRouter()

#: How long one summary request may take: a model reading a long run cold can take minutes.
SUMMARY_SECONDS = 600.0
#: Room for a model that thinks before it writes, when the chat sets no longest answer.
THINKING_TOKENS = 4_096

#: Chats whose last answer's tool results filled the model's window: their next message is sent
#: with earlier messages summarised (`long_chats.tools_filled_words`). Kept while Workbench runs.
_FILLED: set[str] = set()


def mark_filled(chat_id: str) -> None:
    _FILLED.add(chat_id)


# --------------------------------------------------------------------------- #
# what a request is planned from
# --------------------------------------------------------------------------- #


@dataclass
class Inputs:
    """One chat's next request, as far as planning it goes."""

    store: Store
    hub: Hub
    root: Path
    person: Person
    chat: Chat
    #: The path the request is built from, the person's new message last (when there is one).
    history: list[Message]
    records: Mapping[str, FileRecord]
    model: str
    #: The person's pinned notes, in the system message (`memory.block_for`).
    notes: str | None
    #: Whether the request may carry kept `search_memory` and `search_chats` results.
    allow_notes: bool
    allow_chats: bool


async def allowance(state: Any, person: Person, chat: Chat, model: str) -> tuple[bool, bool]:
    """Whether a request to `model` may carry what the person's notes and past chats returned
    (a kept `search_memory` or `search_chats` result): the memory decision, and for an outside
    model, not while a note a model kept is unseen (call 12) and not when documents may never go
    there (MV2-C1). Never raises: what cannot be told is no."""
    from . import memory_api, past_chats

    try:
        notes = await memory_api.decide(state, person, chat, model)
        facts = notes.facts
        allow_notes = bool(notes.send and facts is not None)
        if allow_notes and facts is not None and facts.locality != "local":
            allow_notes = not await memory_api.unseen_withheld(state.store, person.sub)
        chats = await past_chats.decide(state, person, chat, model)
        return allow_notes, bool(chats.send and chats.facts is not None)
    except Exception as exc:
        log.warning("a long chat's allowance was taken as none: %s: %s", type(exc).__name__, exc)
        return False, False


def _documents_of(message: Message, records: Mapping[str, FileRecord]) -> list[FileRecord]:
    return [
        r
        for i in message.attachments
        if (r := records.get(i)) is not None and r.media_type in files.DOCUMENT_TYPES
    ]


def _document_tokens(record: FileRecord) -> int:
    """What an attached document adds to its message, at most: its text as the person chose to
    have it read, in its block; the file itself for a PDF that was never read; nothing for one
    searched instead (its passages are found for each question) or not read yet."""
    reading = record.reading
    if reading is None:
        return context.MEDIA_TOKENS if record.media_type == files.PDF else 0
    choice = context.Choice.from_json(reading.get("choice"))
    if choice is not None and choice.mode == "search":
        return 0
    if reading.get("state") != "ready":
        return 0
    tokens = int(reading.get("tokens") or 0)
    if choice is not None and choice.mode == "first" and choice.tokens is not None:
        tokens = min(tokens, choice.tokens)
    return tokens + context.overhead_tokens(record.name)


def rendered(
    inputs: Inputs, messages: Sequence[Message], *, notes: bool, chats: bool
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Each message as it is sent (lean: no file is read), with this allowance."""
    return [
        (
            m.id,
            conversation.message_parts(
                m, inputs.records, root=inputs.root, lean=True, notes=notes, chats=chats
            ),
        )
        for m in messages
    ]


def _items(inputs: Inputs) -> list[Item]:
    """The path as the plan sees it. The documents of the message being answered are not
    counted here: how much of each goes is decided for the room the plan leaves (MR2,
    `documents_api.prepare`), and they say themselves when they cannot go."""
    out = []
    last = inputs.history[-1] if inputs.history and inputs.history[-1].role == "user" else None
    for m, (_, parts) in zip(
        inputs.history,
        rendered(inputs, inputs.history, notes=inputs.allow_notes, chats=inputs.allow_chats),
        strict=True,
    ):
        docs = (
            0 if m is last else sum(_document_tokens(r) for r in _documents_of(m, inputs.records))
        )
        out.append(
            Item(
                id=m.id,
                role=m.role,
                tokens=context.count_messages(parts) + docs,
                pinned=bool(_documents_of(m, inputs.records)),
                docs=docs,
            )
        )
    return out


def _carries(inputs: Inputs, run: Sequence[Message]) -> dict[str, bool]:
    """What a run carries, with this request's allowance: a kept `search_memory` result
    (`notes`) or `search_chats` result (`chats`) that the strict view withholds."""
    strict = long_chats.digest(rendered(inputs, run, notes=False, chats=False))
    notes = inputs.allow_notes and (
        long_chats.digest(rendered(inputs, run, notes=True, chats=False)) != strict
    )
    chats = inputs.allow_chats and (
        long_chats.digest(rendered(inputs, run, notes=False, chats=True)) != strict
    )
    return {"notes": notes, "chats": chats}


def _digest(inputs: Inputs, run: Sequence[Message], carries: Mapping[str, bool]) -> str:
    return long_chats.digest(
        rendered(inputs, run, notes=bool(carries.get("notes")), chats=bool(carries.get("chats")))
    )


def _block(row: SummaryRow, first: int, last: int) -> str:
    return long_chats.render_block(
        row.nonce, first + 1, last + 1, row.made_at, row.model, row.text, row.cut
    )


def _previous(history: Sequence[Message]) -> Message | None:
    """The answer the request before this one wrote: the last answer before the new message."""
    for message in reversed(history[:-1] if history and history[-1].role == "user" else history):
        if message.role == "assistant":
            return message
    return None


@dataclass
class Valid:
    """A kept summary that may go in this request, where it goes on the path."""

    row: SummaryRow
    first: int
    last: int
    block: str

    def kept(self, sticky: bool) -> Kept:
        return Kept(self.row.id, self.first, self.last, long_chats.block_tokens(self.block), sticky)


async def _valid(inputs: Inputs) -> list[Valid]:
    """The chat's summaries this request may use: its run is a stretch of this path, the run as
    it would be sent now is what was summarised (its digest), and the request may carry what it
    carries. A version switch inside a run takes it off the path; it is kept, so switching back
    uses it again."""
    at = {m.id: i for i, m in enumerate(inputs.history)}
    out = []
    for row in await inputs.store.chat_summaries(inputs.chat.id):
        first, last = at.get(row.first_id), at.get(row.last_id)
        if first is None or last is None or first > last:
            continue
        if (row.carries.get("notes") and not inputs.allow_notes) or (
            row.carries.get("chats") and not inputs.allow_chats
        ):
            continue
        if _digest(inputs, inputs.history[first : last + 1], row.carries) != row.digest:
            continue
        out.append(Valid(row, first, last, _block(row, first, last)))
    return out


@dataclass
class Planned:
    """A request planned: the plan, the summaries placed (kept ones, and once made the new
    ones), and what the answer records of them."""

    plan: Plan
    placed: list[conversation.Placed] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)
    #: The kept summaries in use, by id, and every valid one.
    valid: dict[str, Valid] = field(default_factory=dict)
    items: list[Item] = field(default_factory=list)
    system: int = 0

    @property
    def limits(self) -> Limits:
        return self.plan.limits

    @property
    def about(self) -> int:
        """The request's tokens as planned."""
        return self.plan.tokens

    def record(self) -> list[dict[str, Any]]:
        return [r.to_json() for r in self.records]


def _percent(person: Person, install: config.LongChatSettings) -> tuple[int, str]:
    """The share of the window a chat is summarised at, and whose word it is: the person's
    permission group's (row 70's `summaryContextPercent`), else the install's."""
    policies = person.policies or {}
    value = policies.get("summaryContextPercent")
    if type(value) is int and long_chats.MIN_PERCENT <= value <= long_chats.MAX_PERCENT:
        return value, "group"
    return install.percent, "install"


async def _window(hub: Hub, model: str, history: Sequence[Message]) -> tuple[int | None, bool]:
    """The model's window, and whether it runs outside the install's machines: the gateway's
    word, else the window the last answer's stream said applied."""
    from . import memory_api

    window: int | None = None
    outside = True
    try:
        facts = await memory_api.model_facts(hub, model)
        window, outside = facts.window, facts.locality != "local"
    except Exception as exc:  # a long chat never stops an answer for want of the model's facts
        log.warning(
            "a long chat could not ask about model %s: %s: %s", model, type(exc).__name__, exc
        )
    if window is None:
        for message in reversed(history):
            if message.role == "assistant" and message.model == model and message.context_length:
                window = message.context_length
                break
    return window, outside


def _reserve(chat: Chat) -> int:
    wanted = chat.settings.get("maxTokens")
    return int(wanted) if isinstance(wanted, int) and wanted > 0 else context.ANSWER_RESERVE


def _scale(inputs: Inputs, items: Sequence[Item], system: int) -> float | None:
    """How the last request's real count compares with its estimate, for the same model; None
    when no backend counted it."""
    previous = _previous(inputs.history)
    if (
        previous is None
        or previous.model != inputs.model
        or previous.prompt_tokens is None
        or previous.completion_tokens is None
    ):
        return None
    at = {item.id: i for i, item in enumerate(items)}
    end = at.get(previous.id)
    if end is None:
        return None
    sizes = [item.tokens for item in items[: end + 1]]
    for record in previous.summaries or []:
        first, last = at.get(str(record.get("firstId"))), at.get(str(record.get("lastId")))
        if first is None or last is None or last > end:
            continue
        for i in range(first, last + 1):
            sizes[i] = 0
        sizes[first] = int(record.get("tokens") or 0)
    return long_chats.scale_of(
        previous.prompt_tokens + previous.completion_tokens, system + sum(sizes)
    )


async def plan_request(
    inputs: Inputs, *, overflow: tuple[int, int | None] | None = None
) -> Planned:
    """Plan the next request (`long_chats.plan`); nothing is made or kept. `overflow` is the
    refused request's tokens and the window the refusal named, when the backend refused it."""
    install = await config.long_chat_settings(inputs.store)
    percent, _ = _percent(inputs.person, install)
    window, outside = await _window(inputs.hub, inputs.model, inputs.history)
    force = inputs.chat.id in _FILLED
    previous = _previous(inputs.history)
    if previous is not None and previous.prompt_truncated:
        force = True
    if overflow is not None:
        refused, said = overflow
        shrunk = refused * long_chats.OVERFLOW_SHARE_PERCENT // 100 if refused > 0 else None
        known = [w for w in (window, said, shrunk) if w is not None]
        window = min(known) if known else None
        force = True
    limits = long_chats.limits(window, percent=percent, outside=outside, ceiling=install.ceiling)
    items = _items(inputs)
    system = context.count_messages(conversation.system_messages(inputs.chat, inputs.notes))
    valid = await _valid(inputs)
    used_before = {str(r.get("id")) for r in (previous.summaries or [])} if previous else set()
    scale = _scale(inputs, items, system)
    plan = long_chats.plan(
        items,
        system=system,
        limits=limits,
        reserve=_reserve(inputs.chat),
        kept=[v.kept(v.row.id in used_before) for v in valid],
        enabled=install.summarise and inputs.chat.settings.get("summarise") is not False,
        force=force,
        scale=1.0 if scale is None else scale,
        floor=long_chats.UNMEASURED_FLOOR if scale is None else 1.0,
    )
    out = Planned(plan, valid={v.row.id: v for v in valid}, items=items, system=system)
    for kept in plan.use:
        out.placed.append(
            conversation.Placed(
                inputs.history[kept.first].id,
                inputs.history[kept.last].id,
                out.valid[kept.id].block,
            )
        )
        out.records.append(_record(out.valid[kept.id].row, kept.first, kept.last))
    return out


def _record(row: SummaryRow, first: int, last: int, made: bool = False) -> Record:
    return Record(
        id=row.id,
        first_id=row.first_id,
        last_id=row.last_id,
        first=first + 1,
        last=last + 1,
        tokens=row.tokens,
        made=made,
    )


def refusal(inputs: Inputs, planned: Planned) -> str | None:
    """Why the planned request cannot go, in the person's words; None when it can."""
    plan = planned.plan
    window = plan.limits.window or 0
    if plan.refuse == "off":
        return long_chats.off_words(inputs.model, plan.whole, window)
    if plan.refuse == "too_small":
        if any(item.pinned for item in planned.items):
            # Its documents say it themselves, with what to choose (MR2's `chat_full`).
            return None
        return long_chats.too_small_words(inputs.model, plan.whole, window)
    if plan.refuse == "no_room":
        names = [
            r.name
            for item, m in zip(planned.items, inputs.history, strict=True)
            if item.pinned
            for r in _documents_of(m, inputs.records)
        ]
        return long_chats.no_room_words(inputs.model, plan.fixed, window, names)
    return None


# --------------------------------------------------------------------------- #
# writing summaries
# --------------------------------------------------------------------------- #


async def _summary_model(inputs: Inputs, install: config.LongChatSettings) -> list[str]:
    """Who is asked to write a summary, in order: the expert's choice when it runs on the
    install's machines (content never goes anywhere new), then the chat's own model."""
    from . import memory_api

    wanted = install.model
    if wanted and wanted != inputs.model:
        try:
            facts = await memory_api.model_facts(inputs.hub, wanted)
        except Exception:
            facts = None
        if facts is not None and facts.locality == "local":
            return [wanted, inputs.model]
        log.warning("the summary model %s is not on this install's machines; not used", wanted)
    return [inputs.model]


def _max_tokens(inputs: Inputs, aim: int) -> int:
    """A summary's room: its aim, and a thinking model's room besides (the chat's own longest
    answer, else `THINKING_TOKENS`)."""
    thinks = any(
        m.role == "assistant" and (m.reasoning or m.reasoning_tokens) for m in inputs.history
    )
    if not thinks:
        return aim
    wanted = inputs.chat.settings.get("maxTokens")
    return aim + (int(wanted) if isinstance(wanted, int) and wanted > 0 else THINKING_TOKENS)


async def _ask(
    inputs: Inputs, models: Sequence[str], messages: list[dict[str, Any]], max_tokens: int
) -> tuple[str, str, bool]:
    """One summary request, to each model in turn until one answers: `(text, model, cut)`.
    Raises `HubError` with the last model's words."""
    problem: HubError | None = None
    for model in models:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False}
        try:
            answer = await inputs.hub.complete(body, seconds=SUMMARY_SECONDS)
        except HubError as exc:
            problem = exc
            continue
        choice = (answer.get("choices") or [{}])[0] or {}
        text = long_chats.clean_text(str((choice.get("message") or {}).get("content") or ""))
        if not text:
            problem = HubError(f"{model} wrote no summary", kind="summary")
            continue
        return text, model, choice.get("finish_reason") == "length"
    assert problem is not None
    raise problem


def _placed(planned: Planned, inputs: Inputs, kept: Sequence[tuple[int, int, str]]) -> None:
    planned.placed = [
        conversation.Placed(inputs.history[a].id, inputs.history[b].id, block)
        for a, b, block in sorted(kept)
    ]


async def make(
    inputs: Inputs,
    planned: Planned,
    *,
    documents: Mapping[str, conversation.SentDocument] | None,
    progress: Progress | None = None,
    how: str = "auto",
) -> Planned:
    """Write the summaries the plan wants, oldest first, each from the request before it, and
    merge the oldest two in use when they pass a quarter of the window. Raises `Refused` with the
    cause when one cannot be written: the answer fails, and Try again works."""
    plan = planned.plan
    if not plan.make:
        return await _merge(inputs, planned, progress)
    install = await config.long_chat_settings(inputs.store)
    models = await _summary_model(inputs, install)
    span = plan.limits.span or plan.limits.window or long_chats.MIN_WINDOW
    aim = long_chats.summary_tokens(span)
    words = long_chats.summary_words(aim)
    placed: list[tuple[int, int, str]] = [
        (k.first, k.last, planned.valid[k.id].block) for k in plan.use
    ]
    for a, b in plan.make:
        run = inputs.history[a : b + 1]
        if progress is not None:
            progress(long_chats.progress_of(len(run), models[0]))
        before = [
            conversation.Placed(inputs.history[x].id, inputs.history[y].id, block)
            for x, y, block in sorted(placed)
            if y < a
        ]
        body = conversation.request_for(
            inputs.chat,
            inputs.history[: b + 1],
            list(inputs.records.values()),
            root=inputs.root,
            model=inputs.model,
            search=False,
            memory=inputs.notes,
            documents=documents,
            memory_allowed=inputs.allow_notes,
            chats_allowed=inputs.allow_chats,
            summaries=before,
        )
        messages = [
            *body["messages"],
            {"role": "user", "content": long_chats.instruction(a + 1, b + 1, words)},
        ]
        try:
            text, writer, cut = await _ask(inputs, models, messages, _max_tokens(inputs, aim))
        except HubError as exc:
            raise Refused(
                long_chats.failed_words(exc.message, plan.whole, plan.limits.window)
            ) from exc
        carries = _carries(inputs, run)
        made_at = time.time()
        nonce = long_chats.new_nonce([text])
        block = long_chats.render_block(nonce, a + 1, b + 1, made_at, writer, text, cut)
        row = SummaryRow(
            id=uuid.uuid4().hex,
            chat_id=inputs.chat.id,
            owner=inputs.chat.owner,
            first_id=run[0].id,
            last_id=run[-1].id,
            digest=_digest(inputs, run, carries),
            text=text,
            nonce=nonce,
            tokens=long_chats.block_tokens(block),
            source_tokens=sum(item.tokens for item in planned.items[a : b + 1]),
            model=writer,
            made_at=made_at,
            carries=carries,
            cut=cut,
            how=how,
            instruction=long_chats.INSTRUCTION_VERSION,
        )
        if not await inputs.store.add_summary(row):
            raise Refused(
                "This chat changed while its earlier messages were summarised. Try again."
            )
        log.info(
            "summarised messages %d to %d of a chat (%d tokens into %d) with %s",
            a + 1,
            b + 1,
            row.source_tokens,
            row.tokens,
            writer,
        )
        planned.valid[row.id] = Valid(row, a, b, block)
        placed.append((a, b, block))
        planned.records.append(_record(row, a, b, made=True))
    planned.records.sort(key=lambda r: r.first)
    _placed(planned, inputs, placed)
    if progress is not None:
        progress(None)
    return await _merge(inputs, planned, progress)


async def _merge(inputs: Inputs, planned: Planned, progress: Progress | None) -> Planned:
    """When the summaries in use pass a quarter of the window, the oldest two next to each other
    become one, written from their two texts. The prompt changes from the first of them on; the
    head stays cached. A merge that fails leaves the two as they are."""
    span = planned.plan.limits.span
    if span is None or len(planned.records) < 2:
        return planned
    at = {m.id: i for i, m in enumerate(inputs.history)}
    used = [
        Kept(r.id, at[r.first_id], at[r.last_id], r.tokens)
        for r in planned.records
        if r.first_id in at and r.last_id in at and r.id in planned.valid
    ]
    pair = long_chats.merge_pair(used, span)
    if pair is None:
        return planned
    one, two = (planned.valid[used[i].id] for i in pair)
    first, last = one.first, two.last
    install = await config.long_chat_settings(inputs.store)
    models = await _summary_model(inputs, install)
    aim = long_chats.summary_tokens(span)
    messages = [
        *conversation.system_messages(inputs.chat, inputs.notes),
        {
            "role": "user",
            "content": f"{one.block}\n\n{two.block}\n\n"
            + long_chats.merge_instruction(first + 1, last + 1, long_chats.summary_words(aim)),
        },
    ]
    if progress is not None:
        progress(long_chats.progress_of(last - first + 1, models[0]))
    try:
        text, writer, cut = await _ask(inputs, models, messages, _max_tokens(inputs, aim))
    except HubError as exc:
        log.warning("two summaries of a chat could not be merged: %s", exc.message)
        if progress is not None:
            progress(None)
        return planned
    carries = {
        "notes": bool(one.row.carries.get("notes") or two.row.carries.get("notes")),
        "chats": bool(one.row.carries.get("chats") or two.row.carries.get("chats")),
    }
    run = inputs.history[first : last + 1]
    made_at = time.time()
    nonce = long_chats.new_nonce([text])
    block = long_chats.render_block(nonce, first + 1, last + 1, made_at, writer, text, cut)
    row = SummaryRow(
        id=uuid.uuid4().hex,
        chat_id=inputs.chat.id,
        owner=inputs.chat.owner,
        first_id=run[0].id,
        last_id=run[-1].id,
        digest=_digest(inputs, run, carries),
        text=text,
        nonce=nonce,
        tokens=long_chats.block_tokens(block),
        source_tokens=one.row.source_tokens + two.row.source_tokens,
        model=writer,
        made_at=made_at,
        carries=carries,
        cut=cut,
        how="merge",
        instruction=long_chats.INSTRUCTION_VERSION,
    )
    if progress is not None:
        progress(None)
    if not await inputs.store.add_summary(row):
        return planned
    planned.valid[row.id] = Valid(row, first, last, block)
    keep = [r for r in planned.records if r.id not in (one.row.id, two.row.id)]
    keep.append(_record(row, first, last, made=True))
    planned.records = sorted(keep, key=lambda r: r.first)
    _placed(
        planned,
        inputs,
        [(at[r.first_id], at[r.last_id], planned.valid[r.id].block) for r in planned.records],
    )
    return planned


# --------------------------------------------------------------------------- #
# what the page reads
# --------------------------------------------------------------------------- #


def fold_view(
    row: SummaryRow,
    path_: Sequence[Message],
    first: int,
    last: int,
    *,
    read_only: bool = False,
    hidden: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One summary as the page reads it, with its text. The owner's read-only view
    (`ownerReadsChats`) shows the same, but not the text of a summary of what the person's notes
    or past chats returned, or of a run with a call the owner may not read."""
    hide = None
    if read_only and (row.carries.get("notes") or row.carries.get("chats")):
        hide = "It holds what the person's notes or past chats returned."
    elif read_only and any(m.id in (hidden or {}) for m in path_[first : last + 1]):
        hide = "It holds a call you may not read."
    return {
        "id": row.id,
        "firstId": row.first_id,
        "lastId": row.last_id,
        "from": first + 1,
        "to": last + 1,
        "messages": last - first + 1,
        "tokens": row.tokens,
        "sourceTokens": row.source_tokens,
        "model": row.model,
        "madeAt": row.made_at,
        "cut": row.cut,
        "how": row.how,
        "text": None if hide else row.text,
        "hidden": hide,
    }


def folds(
    rows: Sequence[SummaryRow],
    path_: Sequence[Message],
    *,
    read_only: bool = False,
    hidden: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The runs of the path shown folded: the summaries the newest answer that went through a
    plan was sent (`fold_view`)."""
    latest = next(
        (m for m in reversed(path_) if m.role == "assistant" and m.summaries is not None), None
    )
    if latest is None:
        return []
    at = {m.id: i for i, m in enumerate(path_)}
    by_id = {r.id: r for r in rows}
    out = []
    for record in latest.summaries or []:
        row = by_id.get(str(record.get("id")))
        if row is None:
            continue
        first, last = at.get(row.first_id), at.get(row.last_id)
        if first is None or last is None or first > last:
            continue
        out.append(fold_view(row, path_, first, last, read_only=read_only, hidden=hidden))
    return out


@router.get("/api/chats/{chat_id}/summaries/{summary_id}")
async def summary_view(request: Request, chat_id: str, summary_id: str) -> dict[str, Any]:
    """One summary of the chat's shown path, as Read the summary shows it."""
    from .api import _person, _readable_chat, _state, hidden_from_owner, install_mode
    from .store import path as path_of

    person = await _person(request)
    chat, read_only = await _readable_chat(request, person, chat_id)
    store: Store = _state(request).store
    tree = await store.tree(chat.id)
    shown = path_of(tree)
    row = next((r for r in await store.chat_summaries(chat.id) if r.id == summary_id), None)
    at = {m.id: i for i, m in enumerate(shown)}
    if row is None or row.first_id not in at or row.last_id not in at:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="There is no such summary of the messages shown.",
        )
    hidden = hidden_from_owner(tree, (await install_mode(request))["mode"]) if read_only else {}
    return fold_view(
        row, shown, at[row.first_id], at[row.last_id], read_only=read_only, hidden=hidden
    )


async def inputs_for(
    request: Request,
    person: Person,
    chat: Chat,
    history: list[Message],
    records: Mapping[str, FileRecord],
    model: str,
    notes: str | None,
) -> Inputs:
    from .api import _state

    state = _state(request)
    allow_notes, allow_chats = await allowance(state, person, chat, model)
    return Inputs(
        store=state.store,
        hub=state.hub,
        root=state.settings.data_dir,
        person=person,
        chat=chat,
        history=history,
        records=records,
        model=model,
        notes=notes,
        allow_notes=allow_notes,
        allow_chats=allow_chats,
    )


async def check(
    request: Request,
    person: Person,
    chat: Chat,
    path_: list[Message],
    records: Mapping[str, FileRecord],
    model: str,
    notes: str | None,
) -> Planned:
    """Before a message is kept: the plan for the request it would make, or a 409 with the cause
    when it cannot go (summarising off, a window too small, no room even summarised)."""
    inputs = await inputs_for(request, person, chat, path_, records, model, notes)
    planned = await plan_request(inputs)
    words = refusal(inputs, planned)
    if words is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"message": words, "code": "long_chat"}
        )
    return planned


@router.get("/api/chats/{chat_id}/long")
async def long_view(request: Request, chat_id: str) -> dict[str, Any]:
    """How full the chat is for its model, what it is summarised at and whose word that is, the
    summaries in use, and whether summarising is on: the numbers in effect (settings never lie)."""
    from . import memory_api
    from .api import _person, _readable_chat, _state

    person = await _person(request)
    chat, read_only = await _readable_chat(request, person, chat_id)
    store: Store = _state(request).store
    install = await config.long_chat_settings(store)
    on = install.summarise and chat.settings.get("summarise") is not False
    owner = person if not read_only else (await store.person(chat.owner) or person)
    percent, set_by = _percent(owner, install)
    view: dict[str, Any] = {
        "model": chat.model,
        "on": on,
        "installOn": install.summarise,
        "chatOn": chat.settings.get("summarise") is not False,
        "percent": percent,
        "percentSetBy": set_by,
        "window": None,
        "trigger": None,
        "ceiling": None,
        "about": None,
        "measured": False,
        "summaries": 0,
    }
    if not chat.model:
        return view
    path_ = await store.messages(chat.id)
    records = {r.id: r for r in await store.files_for_chat(chat.id, uploads=True)}
    notes = await memory_api.block_for(request, owner, chat, chat.model, freeze=False)
    inputs = await inputs_for(request, owner, chat, path_, records, chat.model, notes)
    planned = await plan_request(inputs)
    limits = planned.limits
    previous = next((m for m in reversed(path_) if m.role == "assistant"), None)
    measured = (
        previous is not None
        and previous.model == chat.model
        and previous.prompt_tokens is not None
        and previous.completion_tokens is not None
    )
    view.update(
        window=limits.window,
        trigger=limits.trigger,
        ceiling=limits.ceiling,
        about=(previous.prompt_tokens or 0) + (previous.completion_tokens or 0)
        if measured and previous is not None
        else planned.plan.tokens,
        measured=measured,
        summaries=len(previous.summaries or []) if previous is not None else 0,
    )
    return view


def settled(chat_id: str, planned: Planned) -> None:
    """A plan that summarised because the last answer's tool results filled the window has
    done what that answer said would happen."""
    if planned.plan.forced:
        _FILLED.discard(chat_id)
