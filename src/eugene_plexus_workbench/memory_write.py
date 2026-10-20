"""Every note a model writes goes through here (MV3b, design sections 32-33).

The model only proposes. `memory_suggest.read_answer` judges a proposal's content (the
person's words quoted, supported, not a secret, not sensitive unless the policy routes those),
`memory_policy.route` decides where it goes under the write policy in effect (keep, inbox or
drop), and only then does the store write it (`Store.add_suggestions`, the one store method
for a model's notes). Nothing here is said to the model as a rule: a page in the chat that
argues with a rule cannot argue with this code.

Two writers use it: the background pass (`memory_learn`) and `remember`, the in-turn tool a
model set to Keep automatically may call.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from . import config, memory_models, memory_policy, memory_suggest, projects
from .store import MemoryNote, Message, Person, Store, note_key

log = logging.getLogger(__name__)

#: What `remember` answers the model: never which rule said no.
KEPT, OFFERED, NOT_KEPT = "Kept.", "Offered to the person to review.", "Not kept."
#: The most `remember` calls one answer may make.
REMEMBER_CALLS = 3
DAY_SECONDS = 86_400.0


def policy_for(
    install: config.MemorySettings, prefs: dict[str, Any]
) -> tuple[memory_policy.Policy, str]:
    """The write policy in effect for a person, and whose word it is."""
    return memory_policy.effective(install.policy, install.policy_people, prefs.get("write_policy"))


def _stripped(message: str) -> bool:
    """Whether pasted material was taken out of a person's message before it was read."""
    normal = unicodedata.normalize("NFKC", message).replace("\r\n", "\n").replace("\r", "\n")
    kept = memory_suggest.source_text(message)
    return " ".join(kept.split()) != " ".join(normal.split())


def _answer_untrusted(answer: Message) -> bool:
    """Whether an answer read web pages, tool results, document passages or past chats."""
    if answer.searches or any(s.get("kind") in ("web", "document") for s in answer.sources):
        return True
    if answer.retrieval:
        return True
    for turn in answer.tool_rounds:
        for call in turn.get("calls") or []:
            if call.get("serverId") not in ("memory", "memory-write"):
                return True
    used = answer.memory_used or {}
    return bool((used.get("pastChats") or {}).get("exchanges"))


#: A person's message this long, or of several paragraphs, may hold pasted text the paste
#: rules missed (review, MV3b): a note drawn while one is in the turns read is not kept by
#: itself under Standard.
LONG_MESSAGE = 400
#: Words that make a note an instruction, whatever kind the model gave it: routed as one.
_IMPERATIVE = re.compile(
    r"\b(?:always|never|must|should|every (?:answer|reply|time)|from now on|do not|don't|"
    r"make sure|be sure)\b",
    re.IGNORECASE,
)


def _long(message: str) -> bool:
    return len(message) > LONG_MESSAGE or bool(re.search(r"\n\s*\n", message.strip()))


def supersedable(note: MemoryNote | None, kind: str) -> bool:
    """Whether a model may replace `note` with no one approving (review, MV3b): only a note a
    model wrote itself, of the same kind, neither pinned nor given details by the person. Any
    other change waits in Suggested notes, where Keep new shows the old note beside the new."""
    return (
        note is not None
        and note.source == "suggested"
        and note.kind == kind
        and not note.pinned
        and not (note.body or note.body_chars)
    )


def untrusted_in(messages: Sequence[Message], *, answers_too: bool) -> bool:
    """Whether the turns read hold anything the `untrusted` row is about: a person's message
    with attachments or with pasted material taken out, and (`answers_too`, when answers are
    read or were in the model's context) an answer that read pages, tools, documents or past
    chats."""
    for m in messages:
        if m.role == "user" and (m.attachments or _stripped(m.content) or _long(m.content)):
            return True
        if answers_too and m.role == "assistant" and _answer_untrusted(m):
            return True
    return False


def sources_for(
    path: Sequence[Message], theirs: Sequence[Message], policy: memory_policy.Policy
) -> tuple[list[str], list[Message]]:
    """What a note may be drawn from, and the message each text is: under `source: own`, the
    person's own words with pasted material removed (MR8); under `any`, their messages whole
    and the answers' final text as well."""
    if policy.get("source") == "own":
        return memory_suggest.sources_of([m.content for m in theirs]), list(theirs)[
            -memory_suggest.MAX_SOURCE_MESSAGES :
        ]
    wanted = {m.id for m in theirs}
    texts: list[str] = []
    messages: list[Message] = []
    for i, m in enumerate(path):
        if m.id not in wanted:
            continue
        texts.append(unicodedata.normalize("NFKC", m.content))
        messages.append(m)
        if i + 1 < len(path) and path[i + 1].role == "assistant":
            answer = path[i + 1]
            texts.append(unicodedata.normalize("NFKC", answer.content[answer.answer_from or 0 :]))
            messages.append(answer)
    keep = memory_suggest.MAX_SOURCE_MESSAGES * 2
    return texts[-keep:], messages[-keep:]


@dataclass
class Routed:
    """A proposal that passed the content checks, and where the policy sends it."""

    proposal: memory_suggest.Proposal
    action: str
    why: str | None


def route(
    proposals: Sequence[memory_suggest.Proposal],
    policy: memory_policy.Policy,
    *,
    auto: bool,
    untrusted: bool,
    replaced: dict[str, MemoryNote] | None = None,
) -> list[Routed]:
    """Where each proposal goes: the policy's word, then two rules of the write itself. A
    note worded as an instruction is routed as one, whatever kind the model gave it; and a
    keep that would replace a note `supersedable` refuses goes to Suggested notes instead
    (`replaced`: the notes proposals name, by id)."""
    out = []
    for p in proposals:
        kind = "instruction" if _IMPERATIVE.search(p.text) else p.kind
        action, why = memory_policy.route(
            policy, kind=kind, sensitive=p.sensitive, untrusted=untrusted, auto=auto
        )
        if (
            action == memory_policy.KEEP
            and p.replaces
            and not supersedable((replaced or {}).get(p.replaces), p.kind)
        ):
            action, why = memory_policy.INBOX, "replaces"
        out.append(Routed(p, action, why))
    return out


def note_of(
    owner: str,
    chat_id: str,
    message_id: str | None,
    routed: Routed,
    model: str | None,
    policy: memory_policy.Policy,
    tags: Sequence[str],
    now: float,
    project_id: str | None = None,
) -> MemoryNote:
    """A model's proposal as a note, kept where its chat is (MV7: the chat's project)."""
    p = routed.proposal
    return MemoryNote(
        id=uuid.uuid4().hex,
        owner=owner,
        text=p.text,
        kind=p.kind,
        created_at=now,
        updated_at=now,
        status="pending",
        source="suggested",
        source_chat_id=chat_id,
        source_message_id=message_id,
        quote=p.quote,
        expires_at=p.expires_at,
        revisit_at=p.revisit_at,
        replaces=p.replaces,
        proposed_tags=list(tags),
        model=model,
        policy=policy.to_json(),
        auto=routed.action == memory_policy.KEEP,
        project_id=project_id,
    )


# --------------------------------------------------------------------------- #
# remember: the in-turn tool
# --------------------------------------------------------------------------- #


@dataclass
class TurnContext:
    """What the answer calling `remember` has read so far in this turn, beside the chat."""

    web: bool = False
    tools: bool = False
    documents: bool = False
    past_chats: bool = False
    #: Text an app's surface (MCP app, A2UI) put in the chat's context (review, MV3b).
    interactions: bool = False

    @property
    def untrusted(self) -> bool:
        return self.web or self.tools or self.documents or self.past_chats or self.interactions


async def remember_allowed(
    store: Store, owner: str, chat_settings: dict[str, Any], model: str | None
) -> bool:
    """Whether `remember` may be offered: memory on for the install, the person and the chat,
    learning not off, and the chat's model set to Keep automatically under a policy allowing
    it."""
    if not model or chat_settings.get("memory") == "off":
        return False
    install = await config.memory_settings(store)
    prefs = await store.memory_prefs(owner)
    policy, _ = policy_for(install, prefs)
    choice = (await store.memory_model_choices(owner)).get(model)
    mode, _ = memory_models.effective(
        install,
        prefs,
        model,
        choice=choice,
        keep_allowed=policy.get("keepAuto") == "on",
    )
    return mode == memory_models.AUTO


async def run_remember(
    store: Store,
    person: Person,
    chat_id: str,
    model: str | None,
    arguments: dict[str, Any],
    context: TurnContext,
    *,
    today: dt.date | None = None,
) -> tuple[str, MemoryNote | None]:
    """One `remember` call: judged and routed as the pass's proposals are. Returns what the
    model is told and the note written (kept or offered), if any. Never raises."""
    try:
        return await _remember(store, person, chat_id, model, arguments, context, today)
    except Exception as exc:
        log.warning("remember failed: %s: %s", type(exc).__name__, exc)
        return NOT_KEPT, None


async def _remember(
    store: Store,
    person: Person,
    chat_id: str,
    model: str | None,
    arguments: dict[str, Any],
    context: TurnContext,
    today: dt.date | None,
) -> tuple[str, MemoryNote | None]:
    owner = person.sub
    chat = await store.chat(chat_id)
    if chat is None or chat.owner != owner:
        return NOT_KEPT, None
    # Asked again as the call runs: a switch turned off mid-answer keeps nothing.
    if not await remember_allowed(store, owner, chat.settings, model):
        return NOT_KEPT, None
    install = await config.memory_settings(store)
    prefs = await store.memory_prefs(owner)
    policy, _ = policy_for(install, prefs)
    path = await store.messages(chat_id)
    theirs = [m for m in path if m.role == "user" and m.status == "done"]
    sources, messages = sources_for(path, theirs, policy)
    # Everything the model had in its context: the chat's path, and this turn so far.
    untrusted = context.untrusted or untrusted_in(path, answers_too=True)
    # MV7: what the chat may be given is all it may repeat or replace.
    held = [
        n
        for status in ("active", "pending")
        for n in await projects.chat_notes(store, owner, chat, status=status)
    ]
    title = arguments.get("replaces")
    replaced = next(
        (
            n
            for n in held
            if n.status == "active"
            and isinstance(title, str)
            and note_key(n.text) == note_key(title)
        ),
        None,
    )
    known = [memory_suggest.Known(replaced.id, replaced.text)] if replaced else []
    item = {
        "text": arguments.get("text"),
        "kind": arguments.get("kind"),
        "quote": arguments.get("quote"),
        "date": arguments.get("date") or "",
        "replaces": 1 if replaced else 0,
    }
    when = today or dt.date.today()
    outcome = memory_suggest.read_answer(
        json.dumps({"notes": [item]}),
        sources=sources,
        known=known,
        dismissed=[
            *await store.dismissed_words(owner),
            *[n.text for n in held if replaced is None or n.id != replaced.id],
        ],
        today=when,
        room=1,
        secrets_ok=policy.get("secrets") == "allow",
        sensitive_ok=policy.get("sensitive") != "refuse",
    )
    if not outcome.proposals:
        reasons = [r.reason for r in outcome.rejected] or [outcome.error or "none"]
        log.info("remember kept nothing: %s", ", ".join(reasons))
        return NOT_KEPT, None
    [routed] = route(
        outcome.proposals,
        policy,
        auto=True,
        untrusted=untrusted,
        replaced={replaced.id: replaced} if replaced else None,
    )
    if routed.action == memory_policy.DROP:
        log.info("remember kept nothing: the write policy's %s rule", routed.why)
        return NOT_KEPT, None
    tags = [
        memory_suggest.TagChoice(t["tag"], t["description"]) for t in await store.memory_tags(owner)
    ]
    now = time.time()
    message = messages[routed.proposal.message] if routed.proposal.message < len(messages) else None
    note = note_of(
        owner,
        chat_id,
        message.id if message is not None else None,
        routed,
        model,
        policy,
        memory_suggest.tags_for(routed.proposal.text, tags),
        now,
        chat.project_id,
    )
    written = await store.add_suggestions(
        owner,
        chat_id,
        [note],
        read_at=0.0,
        limit=install.max_notes,
        per_chat=memory_suggest.MAX_PER_CHAT,
        per_day=memory_suggest.MAX_PER_DAY,
        since=now - DAY_SECONDS,
    )
    if not written:
        return NOT_KEPT, None
    return (KEPT if written[0].auto else OFFERED), written[0]
