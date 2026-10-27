"""Learn from my chats (MR8): when Workbench asks a model for memory suggestions, and what it
keeps of the answer.

The rules of the call and of its answer are `memory_suggest.py`, pure and measured
(`specs/scripts/m-memory-measurement.py`). This module is the part with a clock, a store and a
gateway.

**When.** A chat that has gone quiet: `poke` after each message a person sends starts (or
restarts) a timer of `IDLE_SECONDS`, and when it ends with no answer being written, the
person's messages that have not been read yet are read once. A chat is read up to the newest of
them whatever comes of it, so the same words are never sent twice; only a gateway that could
not be reached leaves them to be read next time. A restart loses a waiting timer and nothing
else: the next message reads what was left.

**Whose, and how much.** Only the chat's own person's messages, only with the chat's own model
(so their words go nowhere they have not gone), only while memory is on for them, for this
chat and for the install, and only while the chat's model learns for them (`effective`). At most
`MAX_PER_CHAT` suggestions a chat ever makes, `MAX_PER_DAY` a person gets in a day, and none at
the notes limit. The notes they already keep are shown to the model, so it can skip or replace
them, only where their notes may go (a model on their machines, or one they let notes go to in
this chat).

**Which models, and how** (MV3, `memory_models`): the person's choice for the chat's model
(Off, Suggest, Keep automatically), else their choice for models they have not set, else, under
the install's `memorySuggestions: measured`, Suggest for a model whose measurement met the
bar, Eugene's shipped one (`memory_measured.json`: today Gemma 4 E4B and 12B,
Qwen3.6-35B-A3B and Qwen3.8-27B) or the install's own (Measure this model). Keep automatically
is only ever the person's own choice, and arrives with the write policy (MV3b); until then it
suggests. The person's tag list goes in the request only for a model whose measurement with a
list met the same bar (MV1-C1).

Logs carry counts, reasons and causes; never what anybody wrote.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import (
    config,
    memory,
    memory_measure,
    memory_models,
    memory_policy,
    memory_suggest,
    memory_write,
    projects,
)
from .hub import Hub, HubError
from .store import Chat, MemoryNote, Person, Store, tag_key

log = logging.getLogger(__name__)

IDLE_SECONDS = 90.0
DAY_SECONDS = 86_400.0
HOUR_SECONDS = 3_600.0
#: Suggestion calls a person's chats may make in an hour, whatever they come to.
CALLS_PER_HOUR = 20

#: Whose word `learn` is (`memory_models`'s words, kept here for the callers that read them).
YOU, OWNER, MEASURED, DEFAULT = (
    memory_models.YOU,
    memory_models.OWNER,
    memory_models.MEASURED,
    memory_models.DEFAULT,
)


def passed(model: str | None, local: dict[str, dict[str, Any]] | None = None) -> bool:
    """Whether `model`'s measurement recommends Suggest."""
    return memory_models.recommended_for_suggest(
        memory_models.measurement_for(model, local, for_default=True)
    )


def effective(
    install: config.MemorySettings,
    prefs: dict[str, Any],
    model: str | None,
    *,
    choice: str | None = None,
    local: dict[str, dict[str, Any]] | None = None,
    keep_allowed: bool = False,
) -> tuple[str, str]:
    """What a chat on `model` does for this person: `(off | suggest | auto, whose word)`."""
    return memory_models.effective(
        install,
        prefs,
        model,
        choice=choice,
        measured=memory_models.measurement_for(model, local, for_default=True),
        keep_allowed=keep_allowed,
    )


@dataclass
class Outcome:
    """What one read of a chat came to (for the tests and the log)."""

    asked: bool = False
    offered: list[MemoryNote] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)
    skipped: str | None = None


async def _tags_elsewhere(store: Store, owner: str, chat: Chat) -> set[str]:
    """The keys of tags carried only by notes a chat may not be given (MV7)."""
    every = await store.memory_notes(owner)
    here = {n.id for n in projects.in_place(every, await projects.place_of(store, chat))}
    used_here = {tag_key(t) for n in every if n.id in here for t in n.tags}
    used_there = {tag_key(t) for n in every if n.id not in here for t in n.tags}
    return used_there - used_here


class Learner:
    """One per Workbench: a timer per chat, and the read itself (`read`)."""

    def __init__(
        self,
        store: Store,
        hub: Hub,
        busy: Callable[[str], bool] = lambda _: False,
        *,
        idle: float | None = None,
        clock: Callable[[], float] = time.time,
        today: Callable[[], dt.date] = dt.date.today,
    ) -> None:
        self._store = store
        self._hub = hub
        self._busy = busy
        #: None: IDLE_SECONDS as it is when a timer starts.
        self._idle = idle
        self._clock = clock
        self._today = today
        self._timers: dict[str, asyncio.Task[None]] = {}
        #: Chats being read now: a message sent meanwhile is read after, never by cancelling.
        self._reading: set[str] = set()
        self._again: dict[str, Person] = {}
        #: One call at a time for a person, and at most `CALLS_PER_HOUR` an hour.
        self._locks: dict[str, asyncio.Lock] = {}
        #: Told of the notes a model kept automatically in a chat, for its receipt (MV3b).
        self.on_learned: Callable[[str, list[MemoryNote]], None] = lambda chat_id, notes: None
        self._calls: dict[str, deque[float]] = {}

    def poke(self, person: Person, chat_id: str) -> None:
        """A person sent a message in this chat: read it once the chat is quiet."""
        if chat_id in self._reading:
            self._again[chat_id] = person
            return
        waiting = self._timers.pop(chat_id, None)
        if waiting is not None:
            waiting.cancel()
        self._timers[chat_id] = asyncio.create_task(self._later(person, chat_id))

    async def _later(self, person: Person, chat_id: str) -> None:
        try:
            idle = self._idle if self._idle is not None else IDLE_SECONDS
            await asyncio.sleep(idle)
            # Answers say whether one is running, not when it ends: a quiet chat is checked
            # again a few times a minute, which costs nothing beside a model's answer.
            while self._busy(chat_id):  # noqa: ASYNC110
                await asyncio.sleep(idle / 3)
            self._reading.add(chat_id)
            try:
                outcome = await self.read(person, chat_id)
            finally:
                self._reading.discard(chat_id)
            if outcome.asked:
                log.info(
                    "memory suggestions for a chat: %d offered, %d turned away%s",
                    len(outcome.offered),
                    sum(outcome.rejected.values()),
                    f" ({', '.join(f'{k} {v}' for k, v in sorted(outcome.rejected.items()))})"
                    if outcome.rejected
                    else "",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("memory suggestions for a chat failed: %s: %s", type(exc).__name__, exc)
        finally:
            if self._timers.get(chat_id) is asyncio.current_task():
                del self._timers[chat_id]
            follow = self._again.pop(chat_id, None)
            if follow is not None:
                self.poke(follow, chat_id)

    async def aclose(self) -> None:
        for task in list(self._timers.values()):
            task.cancel()
        for task in list(self._timers.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._timers.clear()

    async def drain_chat(self, chat_id: str) -> None:
        """A branch is being hidden: finish no pass from the old path after it goes."""
        self._again.pop(chat_id, None)
        task = self._timers.pop(chat_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _budget_left(self, owner: str, now: float) -> bool:
        calls = self._calls.setdefault(owner, deque())
        while calls and now - calls[0] > HOUR_SECONDS:
            calls.popleft()
        return len(calls) < CALLS_PER_HOUR

    async def read(self, person: Person, chat_id: str) -> Outcome:
        """Read the chat's unread messages from its person once, if every rule allows."""
        lock = self._locks.setdefault(person.sub, asyncio.Lock())
        async with lock:
            return await self._read(person, chat_id)

    async def _read(self, person: Person, chat_id: str) -> Outcome:
        from . import memory_api
        from .person_context import acting_as

        store, owner = self._store, person.sub
        chat = await store.chat(chat_id)
        if chat is None or chat.owner != owner:
            return Outcome(skipped="no_chat")
        install = await config.memory_settings(store)
        prefs = await store.memory_prefs(owner)
        learning = await store.memory_learning(chat_id, owner)
        if learning is None:
            return Outcome(skipped="no_chat")
        read_at, suggested = learning
        path = await store.messages(chat_id)
        unread = [
            (i, m)
            for i, m in enumerate(path)
            if m.role == "user" and m.status == "done" and m.created_at > (read_at or 0.0)
        ]
        if not unread:
            return Outcome(skipped="nothing_new")
        newest = max(m.created_at for _, m in unread)
        local = await store.memory_measurements()
        # What the install does: Eugene's measurement, or the owner's own run (section 30).
        measured = memory_models.measurement_for(chat.model, local, for_default=True)
        choices = await store.memory_model_choices(owner)
        policy, _ = memory_write.policy_for(install, prefs)
        mode, _ = memory_models.effective(
            install,
            prefs,
            chat.model,
            choice=choices.get(chat.model or ""),
            measured=measured,
            keep_allowed=policy.get("keepAuto") == "on",
        )

        async def done(reason: str) -> Outcome:
            # What was not read while learning was off, or for another model, is never read
            # later: turning it on, or changing the chat's model, sends nothing older.
            await store.add_suggestions(owner, chat_id, [], read_at=newest, limit=install.max_notes)
            return Outcome(skipped=reason)

        if mode == "off" or chat.settings.get("memory") == "off" or not chat.model:
            return await done("off")
        now = self._clock()
        if not self._budget_left(owner, now):
            return Outcome(skipped="budget")  # left to read on a later message
        # Only messages the chat's model itself answered: their words go to no model they have
        # not gone to already.
        theirs = [
            m
            for i, m in unread
            if i + 1 < len(path)
            and path[i + 1].role == "assistant"
            and path[i + 1].model == chat.model
        ][-memory_suggest.MAX_SOURCE_MESSAGES :]
        room = min(
            memory_suggest.MAX_PER_CHAT - suggested,
            memory_suggest.MAX_PER_DAY - await store.suggested_since(owner, now - DAY_SECONDS),
            install.max_notes - await store.memory_count(owner),
        )
        if room <= 0:
            return await done("no_room")
        # What a note may be drawn from is the write policy's `source` row (MV3b): the
        # person's own words (MR8), or under Open their whole messages and the answers too.
        sources, read = memory_write.sources_for(path, theirs, policy)
        if not theirs or not memory_suggest.worth_asking(sources):
            return await done("nothing_to_read")
        untrusted = memory_write.untrusted_in(
            read if policy.get("source") == "any" else theirs,
            answers_too=policy.get("source") == "any",
        )
        # The notes they keep go to the model only where their notes may go.
        known: list[memory_suggest.Known] = []
        tags: list[memory_suggest.TagChoice] = []
        try:
            facts = await memory_api.model_facts(self._hub, chat.model)
            if memory_api._placement(facts, install, prefs, chat).send:
                # Call 12: a note a model kept and the person has not seen stays local.
                local_only = facts.locality != "local" and policy.get("unseenLocal") == "yes"
                known = await self._known(
                    owner, " ".join(sources), local_only=local_only, chat=chat
                )
                # Their tag list is their words too: it goes only where their notes may.
                # MV7: and a tag used only by another place's notes is that place's.
                elsewhere = await _tags_elsewhere(store, owner, chat)
                tags = [
                    memory_suggest.TagChoice(t["tag"], t["description"])
                    for t in await store.memory_tags(owner)
                    if t["key"] not in elsewhere
                ]
        except HubError as exc:
            log.warning("memory suggestions could not ask about the chat's model: %s", exc.message)
            return Outcome(skipped="gateway")
        today = self._today()
        body = memory_suggest.request_body(
            chat.model,
            sources,
            known,
            today,
            secrets.token_hex(4),
            tags[: memory_measure.TAGS_MEASURED] if memory_models.tags_in_request(measured) else (),
        )
        self._calls.setdefault(owner, deque()).append(now)
        try:
            with acting_as(owner, person.session_id):
                # Background (row 11): it waits behind people's chats, and a refusal
                # (a full line) is the pass's `skipped`, tried again at the next one.
                answer = await self._hub.complete(body, band="background")
        except HubError as exc:
            log.warning("memory suggestions were not made: the gateway said %s", exc.message)
            return Outcome(asked=True, skipped="gateway")
        content = ((answer.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        # Every note they have, offered or kept, is one not to offer again, whether or not
        # the model was shown it; so is every suggestion they turned away.
        shown = {k.id for k in known}
        held = [
            n.text
            for status in ("active", "pending")
            for n in await projects.chat_notes(store, owner, chat, status=status)
            if n.id not in shown
        ]
        outcome = memory_suggest.read_answer(
            content,
            sources=sources,
            known=known,
            dismissed=[*await store.dismissed_words(owner), *held],
            today=today,
            room=room,
            secrets_ok=policy.get("secrets") == "allow",
            sensitive_ok=policy.get("sensitive") != "refuse",
        )
        rejected: dict[str, int] = {}
        for r in outcome.rejected:
            rejected[r.reason] = rejected.get(r.reason, 0) + 1
        if outcome.error is not None:
            rejected[outcome.error] = rejected.get(outcome.error, 0) + 1
        # The write policy decides where each goes, in code (MV3b): kept, offered or dropped.
        named = [p.replaces for p in outcome.proposals if p.replaces]
        routed = memory_write.route(
            outcome.proposals,
            policy,
            auto=mode == memory_models.AUTO,
            untrusted=untrusted,
            replaced={n.id: n for n in await store.memory_notes_by_ids(owner, named)}
            if named
            else None,
        )
        notes = []
        for w in routed:
            if w.action == memory_policy.DROP:
                rejected[f"policy_{w.why}"] = rejected.get(f"policy_{w.why}", 0) + 1
                continue
            notes.append(
                memory_write.note_of(
                    owner,
                    chat_id,
                    read[w.proposal.message].id,
                    w,
                    chat.model,
                    policy,
                    w.proposal.tags or memory_suggest.tags_for(w.proposal.text, tags),
                    now,
                    chat.project_id,
                )
            )
        offered = await store.add_suggestions(
            owner,
            chat_id,
            notes,
            read_at=newest,
            limit=install.max_notes,
            per_chat=memory_suggest.MAX_PER_CHAT,
            per_day=memory_suggest.MAX_PER_DAY,
            since=now - DAY_SECONDS,
        )
        kept = [n for n in offered if n.auto]
        if kept:
            self.on_learned(chat_id, kept)
        return Outcome(asked=True, offered=offered, rejected=rejected)

    async def _known(
        self, owner: str, words: str, *, local_only: bool = False, chat: Chat | None = None
    ) -> list[memory_suggest.Known]:
        """The notes most likely to be repeated or replaced: those sharing words with the
        messages, then the newest, up to what the model is shown. With `local_only`, none a
        model kept that the person has not seen (call 12). Only notes the chat may be given
        (MV7): a suggestion never repeats or replaces a note of another place."""
        cap = memory_suggest.MAX_KNOWN_SHOWN
        place = await projects.place_of(self._store, chat)
        expression = memory.match_expression(memory.search_words(words))
        found = (
            projects.in_place(
                await self._store.search_memory(owner, expression, limit=cap * 4), place
            )[:cap]
            if expression
            else []
        )
        seen = {n.id for n in found}
        rest = [
            n
            for n in projects.in_place(await self._store.memory_notes(owner), place)
            if n.id not in seen
        ]
        both = [*found, *rest]
        if local_only:
            both = memory.shareable(both)
        return [memory_suggest.Known(n.id, n.text) for n in both[:cap]]


def dismissed_words(text: str) -> str:
    """What is kept of a suggestion the person turned away: its content words' stems."""
    return memory_suggest.stems(text)
