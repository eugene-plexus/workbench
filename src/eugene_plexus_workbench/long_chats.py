"""Long chats that outgrow the context (row 26, `docs/design/rows/row-26.md`).

Pure rules, no I/O, as `context.py` is: how full a chat's next request is, when it is summarised,
which earlier messages a summary stands for, where the summary goes and when one may be used. The
summary call and the store are `summaries.py`; the request is built in `conversation.py`.

**Workbench never changes or deletes a message.** A summary is a new text that stands for a run of
the path's messages in the request only; the person still reads every message.

**When.** The window `W` is what the gateway says the model holds (`/v1/models`), else the window
the last answer's stream said applied. The trigger is `W x percent` (the person's group sets the
percent, else the install; 80 by default). A model that runs outside the install's machines is
also summarised at a ceiling (128,000 tokens by default), because every turn bills the whole
history. A chat is summarised when its next request and the room kept for the answer would pass
the trigger; a window under 8,192 tokens is too small to summarise into, and a chat that does not
fit it is refused.

**What.** The path is a list of exchanges: a person's message and what answers it. The first
exchange is the head and is sent as it is: it holds the task, and with the system message it is
the engine's cached prefix (the first message alone, when the first exchange is more than a
quarter of the window). An exchange whose message carries an attached document is never
summarised (MR2: a document is never cut unless the person chooses how). The tail is the newest
exchanges that fit in a quarter of the window, the person's new message always among them. What
is between is summarised, oldest first, until the request is at most half the window.

**Between summaries every request is the one before plus new messages.** A summary the last
request used is used again while it is valid, so the engine's cache holds from the head through
the summary to the newest message. A summary is valid while its run is a stretch of the path the
request is built from and its digest still matches the run as it would be sent; a version switch
inside the run takes it off the path, and switching back makes it valid again.

**Size.** A summary aims at `clamp(8% of W, 512, 4,000)` tokens. When the summaries in a request
pass a quarter of the window, the oldest two that are next to each other merge into one.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from . import token_count

# --------------------------------------------------------------------------- #
# the numbers
# --------------------------------------------------------------------------- #

#: The share of the window past which a chat is summarised, unless a group or the install says.
SUMMARISE_AT_PERCENT = 80
MIN_PERCENT, MAX_PERCENT = 50, 95
#: A model outside the install's machines is also summarised at this many tokens (0 is none).
OUTSIDE_CEILING_TOKENS = 128_000
MIN_CEILING, MAX_CEILING = 16_000, 2_000_000
#: A window under this is too small to summarise into.
MIN_WINDOW = 8_192
#: The head may be at most this share of the window, and the tail is what fits in this share.
HEAD_SHARE_PERCENT = 25
TAIL_SHARE_PERCENT = 25
#: Summarising goes on until the request is at most this share of the window.
AFTER_SHARE_PERCENT = 50
#: What one summary aims at: this share of the window, between these.
SUMMARY_SHARE_PERCENT = 8
SUMMARY_MIN_TOKENS, SUMMARY_MAX_TOKENS = 512, 4_000
#: The summaries in one request may take this share of the window before two merge.
MERGE_SHARE_PERCENT = 25
#: A summary request (what it replays, the instruction and the summary) fills at most this share.
REQUEST_SHARE_PERCENT = 90
#: After a backend refuses a request as too long, the next is planned against this share of it.
OVERFLOW_SHARE_PERCENT = 90
#: How far the real count of the last request may move the estimate, either way.
SCALE_LOW, SCALE_HIGH = 0.5, 2.0
#: Before a backend has counted a chat, a request is refused only when even this share of the
#: estimate (`ceil(characters / 3)`) passes the window: about four characters a token.
UNMEASURED_FLOOR = 0.75
#: The version of the instruction a summary is written to (kept with each summary).
INSTRUCTION_VERSION = 1

#: The words of a backend's refusal for want of room, as the gateway lists them
#: (`gateway/responses.py` `_CONTEXT_WORDS`). Copied, for components share no code:
#: `tests/test_long_chats.py` holds the two lists together.
OVERFLOW_WORDS = (
    "exceed_context_size",
    "context size",
    "context length",
    "context_length_exceeded",
    "maximum context",
)

#: Where a refusal names the window that applied: llama-server's body and message, and the
#: OpenAI and vLLM wording.
_WINDOW_SAID = (
    re.compile(r'n_ctx\\?"?\s*:\s*(\d+)'),
    re.compile(r"available context size \((\d+)"),
    re.compile(r"maximum context length is (\d+)", re.IGNORECASE),
)


def is_overflow(words: str | None) -> bool:
    """Whether a backend's refusal says the request did not fit the model's window."""
    lowered = (words or "").lower()
    return any(word in lowered for word in OVERFLOW_WORDS)


def window_said(words: str | None) -> int | None:
    """The window a refusal names, when it names one."""
    for pattern in _WINDOW_SAID:
        found = pattern.search(words or "")
        if found:
            value = int(found.group(1))
            return value if value > 0 else None
    return None


# --------------------------------------------------------------------------- #
# the window and the trigger
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Limits:
    """The numbers a chat's next request is planned against."""

    #: The model's window, when it is known.
    window: int | None
    #: Summarise when the request and the answer's room would pass this.
    trigger: int | None
    #: The window the plan's shares are of: `window`, or, where the outside ceiling sets the
    #: trigger, the window at which the percent would come to the ceiling.
    span: int | None
    percent: int
    #: The ceiling that set the trigger (a model outside the machines), else None.
    ceiling: int | None = None
    outside: bool = False

    def view(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "trigger": self.trigger,
            "percent": self.percent,
            "ceiling": self.ceiling,
        }


def limits(window: int | None, *, percent: int, outside: bool, ceiling: int) -> Limits:
    """The trigger: `window x percent`, or the outside ceiling when it is less (or the window is
    not known). With neither, nothing is planned: only a refusal from the backend summarises."""
    percent = min(MAX_PERCENT, max(MIN_PERCENT, percent))
    trigger = None if window is None else window * percent // 100
    applied = None
    if outside and ceiling > 0 and (trigger is None or ceiling < trigger):
        trigger, applied = ceiling, ceiling
    if trigger is None:
        return Limits(window, None, None, percent, None, outside)
    span = window if applied is None and window is not None else trigger * 100 // percent
    return Limits(window, trigger, span, percent, applied, outside)


def summary_tokens(span: int) -> int:
    """What one summary aims at: 8% of the window, at least 512 and at most 4,000 tokens."""
    return max(SUMMARY_MIN_TOKENS, min(SUMMARY_MAX_TOKENS, span * SUMMARY_SHARE_PERCENT // 100))


def summary_words(tokens: int) -> int:
    """The words a summary of `tokens` is asked to keep to: about three for every four."""
    return max(100, tokens * 3 // 4)


def scale_of(real: int | None, estimate: int) -> float:
    """How the last request's real count compares with what Workbench estimated of it. The
    estimate is deliberately high (`token_count`); a backend's own count brings it down, never
    below half and never above twice."""
    if real is None or real <= 0 or estimate <= 0:
        return 1.0
    return min(SCALE_HIGH, max(SCALE_LOW, real / estimate))


# --------------------------------------------------------------------------- #
# the path as the plan sees it
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Item:
    """One message of the path: its id, its role, and its tokens as it would be sent whole
    (its documents' blocks included, `docs` of them); `pinned` when it carries an attached
    document."""

    id: str
    role: str
    tokens: int
    pinned: bool = False
    docs: int = 0


@dataclass(frozen=True)
class Kept:
    """A summary that is valid for this request: the items it stands for (`first` to `last`,
    both included), its block's tokens, and whether the last request used it."""

    id: str
    first: int
    last: int
    tokens: int
    sticky: bool = False


def exchanges(items: Sequence[Item]) -> list[tuple[int, int]]:
    """The path's exchanges, as `(first, last)` item positions: each starts at a person's
    message and runs to the message before the next one."""
    out: list[list[int]] = []
    for at, item in enumerate(items):
        if item.role == "user" or not out:
            out.append([at, at])
        else:
            out[-1][1] = at
    return [(a, b) for a, b in out]


@dataclass
class Plan:
    """How a request goes: whole, or with summaries (`use` are kept ones, `make` the runs to
    summarise now, oldest first). `tokens` is what it comes to as planned (a new summary counted
    at its aim); `refuse` says why it cannot go: `off` (summarising is off and it does not fit),
    `too_small` (a window under 8,192 tokens), `no_room` (it does not fit even summarised)."""

    use: list[Kept]
    make: list[tuple[int, int]]
    tokens: int
    whole: int
    limits: Limits
    reserve: int
    refuse: str | None = None
    #: What summarising cannot touch: the system message, the head, the documents' exchanges
    #: and the tail, in tokens; and whether a document is among them.
    fixed: int = 0
    documents: bool = False
    #: The items kept verbatim (their documents' blocks counted apart: `docs`).
    lean: int = 0
    #: The tokens a new summary is counted at.
    aim: int = 0
    forced: bool = False

    @property
    def summarised(self) -> bool:
        return bool(self.use or self.make)


#: What a summary's block adds to its text: its tags and its line.
BLOCK_TOKENS = 80


def plan(
    items: Sequence[Item],
    *,
    system: int,
    limits: Limits,
    reserve: int,
    kept: Sequence[Kept] = (),
    enabled: bool = True,
    force: bool = False,
    scale: float = 1.0,
    floor: float = 1.0,
) -> Plan:
    """Plan the next request (see the module).

    `system` is the system message's tokens. `kept` are the valid summaries; one the last
    request used (`sticky`) is used again while the chat is not back under half the window, so
    that turns between summaries only append. `force` summarises down to half the window even
    under the trigger: the backend said it dropped part of the last request, or an answer's tool
    results filled the window. `scale` is `scale_of` the last request. `floor` is how far below
    the estimate the request may really be: a request is refused only when it would not fit even
    so (`UNMEASURED_FLOOR` while no backend has counted this chat), never on an estimate that is
    high on purpose."""

    def sized(tokens: int) -> int:
        return round(tokens * scale)

    sizes = [sized(i.tokens) for i in items]
    head_system = sized(system)
    whole = head_system + sum(sizes)
    window, trigger, span = limits.window, limits.trigger, limits.span
    out = Plan(
        use=[],
        make=[],
        tokens=whole,
        whole=whole,
        limits=limits,
        reserve=reserve,
        lean=head_system + sum(sizes[i] - sized(it.docs) for i, it in enumerate(items)),
        forced=force,
    )
    if trigger is None or span is None:
        return out

    # A request is refused only where the backend would refuse it: a prompt past the window. An
    # answer with less room than it is kept is cut short and says so; it does not fail.
    def fits(tokens: int) -> bool:
        return window is None or round(tokens * floor) <= window

    fits_window = fits(whole)
    if not enabled:
        if not fits_window:
            out.refuse = "off"
        return out
    after = span * AFTER_SHARE_PERCENT // 100
    sticky = [k for k in kept if k.sticky]
    if not force and whole + reserve <= trigger and (not sticky or whole + reserve <= after):
        return out
    if span < MIN_WINDOW:
        if not fits_window:
            out.refuse = "too_small"
        return out
    aim = summary_tokens(span) + BLOCK_TOKENS
    out.aim = aim
    spans = exchanges(items)
    blocked: set[int] = set()
    # The head: the first exchange, or its first message alone when the exchange is too large.
    if spans:
        first, last = spans[0]
        head_last = last
        if len(spans) > 1 and sum(sizes[first : last + 1]) > span * HEAD_SHARE_PERCENT // 100:
            head_last = first
        blocked.update(range(first, head_last + 1))
    # The tail: the newest exchanges that fit in a quarter of the window, the newest always.
    if spans:
        start, end = spans[-1]
        tail_start, held = start, sum(sizes[start : end + 1])
        for a, b in reversed(spans[:-1]):
            more = sum(sizes[a : b + 1])
            if held + more > span * TAIL_SHARE_PERCENT // 100:
                break
            held, tail_start = held + more, a
        blocked.update(range(tail_start, len(items)))
    # A document's exchange stays as it is.
    documents = False
    for a, b in spans:
        if any(items[i].pinned for i in range(a, b + 1)):
            blocked.update(range(a, b + 1))
            documents = True
    out.documents = documents
    out.fixed = head_system + sum(sizes[i] for i in sorted(blocked))

    def total(use: Sequence[Kept], make: Sequence[tuple[int, int]]) -> int:
        covered: set[int] = set()
        tokens = head_system
        for k in use:
            covered.update(range(k.first, k.last + 1))
            tokens += sized(k.tokens)
        for a, b in make:
            covered.update(range(a, b + 1))
            tokens += aim
        return tokens + sum(sizes[i] for i in range(len(items)) if i not in covered)

    def lean_total(use: Sequence[Kept], make: Sequence[tuple[int, int]]) -> int:
        covered = {i for k in use for i in range(k.first, k.last + 1)}
        covered.update(i for a, b in make for i in range(a, b + 1))
        docs = sum(sized(items[i].docs) for i in range(len(items)) if i not in covered)
        return total(use, make) - docs

    goal = after if force else trigger
    # Kept summaries first, oldest first: the longest at each start (a merge before its parts).
    usable = sorted(
        (k for k in kept if not blocked.intersection(range(k.first, k.last + 1))),
        key=lambda k: (k.first, -k.last),
    )
    chosen: list[Kept] = []
    reach = -1
    for k in usable:
        if k.first > reach:
            chosen.append(k)
            reach = k.last
    use: list[Kept] = []
    for k in chosen:
        if k.sticky or total(use, []) + reserve > goal:
            use.append(k)
    make: list[tuple[int, int]] = []
    if total(use, []) + reserve > goal:
        covered = blocked | {i for k in use for i in range(k.first, k.last + 1)}
        gaps: list[tuple[int, int]] = []
        for at in range(len(items)):
            if at in covered:
                continue
            if gaps and gaps[-1][1] == at - 1:
                gaps[-1] = (gaps[-1][0], at)
            else:
                gaps.append((at, at))
        limit = (window or span) * REQUEST_SHARE_PERCENT // 100 - aim - INSTRUCTION_TOKENS
        starts = {a for a, _ in spans}
        for a, b in gaps:
            if total(use, make) + reserve <= after:
                break
            if sum(sizes[a : b + 1]) <= aim:
                continue  # its summary would be no shorter
            before = _before(a, [(k.first, k.last, sized(k.tokens)) for k in use], make, sizes, aim)
            make.extend(
                _pieces(a, b, sizes, starts, before=head_system + before, limit=limit, aim=aim)
            )
    out.use, out.make = use, make
    out.tokens = total(use, make)
    out.lean = lean_total(use, make)
    if not fits(out.tokens):
        out.refuse = "no_room"
    return out


#: What the instruction of a summary request comes to, at most.
INSTRUCTION_TOKENS = 200


def _before(
    at: int,
    use: Sequence[tuple[int, int, int]],
    make: Sequence[tuple[int, int]],
    sizes: Sequence[int],
    aim: int,
) -> int:
    """The tokens of the path up to (not including) item `at`, with the kept summaries in `use`
    (`first`, `last`, tokens) and the new ones of `make` (at their aim) in place."""
    covered: set[int] = set()
    tokens = 0
    for first, last, size in use:
        if last < at:
            covered.update(range(first, last + 1))
            tokens += size
    for a, b in make:
        if b < at:
            covered.update(range(a, b + 1))
            tokens += aim
    return tokens + sum(sizes[i] for i in range(at) if i not in covered)


def _pieces(
    first: int,
    last: int,
    sizes: Sequence[int],
    starts: set[int],
    *,
    before: int,
    limit: int,
    aim: int,
) -> list[tuple[int, int]]:
    """A run cut, at exchanges, into pieces whose summary request fits: what comes before the
    piece (its earlier pieces summarised), the piece, the instruction and the summary. An
    exchange that alone does not fit is left as it is."""
    pieces: list[tuple[int, int]] = []
    bounds = [i for i in range(first, last + 1) if i in starts or i == first]
    bounds.append(last + 1)
    at = 0
    prefix = before
    while at < len(bounds) - 1:
        start = bounds[at]
        end = at
        size = 0
        while end < len(bounds) - 1:
            step = sum(sizes[bounds[end] : bounds[end + 1]])
            if prefix + size + step > limit:
                break
            size += step
            end += 1
        if end == at:
            # This exchange alone does not fit a summary request: it stays, and the next starts.
            prefix += sum(sizes[bounds[at] : bounds[at + 1]])
            at += 1
            continue
        if size > aim:
            pieces.append((start, bounds[end] - 1))
            prefix += aim
        else:
            prefix += size
        at = end
    return pieces


def merge_pair(used: Sequence[Kept], span: int) -> tuple[int, int] | None:
    """When the summaries a request uses pass a quarter of the window: the positions in `used`
    of the oldest two that are next to each other, to be merged into one. None when they are
    within it, or no two are next to each other."""
    if sum(k.tokens for k in used) <= span * MERGE_SHARE_PERCENT // 100:
        return None
    ordered = sorted(range(len(used)), key=lambda i: used[i].first)
    for one, two in itertools.pairwise(ordered):
        if used[two].first == used[one].last + 1:
            return one, two
    return None


# --------------------------------------------------------------------------- #
# a summary, as it is sent
# --------------------------------------------------------------------------- #


def new_nonce(texts: Sequence[str]) -> str:
    """A block's suffix: 32 random bits, chosen again until it is in none of `texts`."""
    while True:
        nonce = secrets.token_hex(4)
        if not any(nonce in text for text in texts):
            return nonce


def day_of(made_at: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(made_at))


def render_block(
    nonce: str, first: int, last: int, made_at: float, model: str, text: str, cut: bool
) -> str:
    """A summary as the model reads it: a delimited block whose suffix is the summary's own, so
    it is the same bytes every turn, opened by a line saying what it is. `first` and `last` are
    the positions of its run on the path, from 1."""
    intro = (
        f"A summary of turns {first} to {last} of this conversation, written by {model}. The "
        "original messages are kept in Workbench and not sent here."
    )
    if cut:
        intro += " The summary was cut at its limit."
    return (
        f'<earlier-turns-{nonce} from="{first}" to="{last}" date="{day_of(made_at)}">\n'
        f"{intro}\n\n{text.strip()}\n</earlier-turns-{nonce}>"
    )


def instruction(first: int, last: int, words: int) -> str:
    """What the model is asked, after the conversation up to the end of the run (version 1)."""
    return (
        f"Summarise turns {first} to {last} of the conversation above, so that it can go on "
        "without them. Keep what the person wants and their exact requirements; decisions and "
        "why; names, numbers, paths, code identifiers and commands exactly as written; what "
        "tools found that still matters; open questions and unfinished tasks. Leave out what "
        f"later turns replaced. At most {words} words, in the conversation's language. Do not "
        "answer the person."
    )


def merge_instruction(first: int, last: int, words: int) -> str:
    """What the model is asked to merge two summaries that are next to each other."""
    return (
        f"The two blocks above summarise turns {first} to {last} of a conversation. Write one "
        "summary of them, so that the conversation can go on without them. Keep what the person "
        "wants and their exact requirements; decisions and why; names, numbers, paths, code "
        "identifiers and commands exactly as written; open questions and unfinished tasks. "
        f"At most {words} words, in the conversation's language. Do not answer the person."
    )


_THINKING = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)


def clean_text(text: str) -> str:
    """A summary's words: what a model that thinks aloud wrote before its answer left out."""
    return _THINKING.sub("", text or "").strip()


def digest(run: Sequence[tuple[str, Sequence[dict[str, Any]]]]) -> str:
    """SHA-256 of a run as it is sent: each message's id and the request messages it is."""
    data = json.dumps(
        [[mid, list(parts)] for mid, parts in run],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def block_tokens(block: str) -> int:
    return token_count.estimate(block) + 4


# --------------------------------------------------------------------------- #
# what is said
# --------------------------------------------------------------------------- #


def off_words(model: str, about: int, window: int) -> str:
    return (
        f"This chat is about {about:,} tokens, more than {model}'s window of {window:,} leaves "
        "room for. Turn on summarising for this chat, start a new chat, or pick a model with a "
        "larger window."
    )


def too_small_words(model: str, about: int, window: int) -> str:
    return (
        f"This chat is about {about:,} tokens, more than {model}'s window of {window:,} leaves "
        f"room for, and a window under {MIN_WINDOW:,} tokens is too small to summarise into. "
        "Start a new chat, or pick a model with a larger window."
    )


def no_room_words(model: str, fixed: int, window: int, documents: Sequence[str]) -> str:
    if documents:
        return (
            f"The first message and attached documents fill about {fixed:,} of {model}'s "
            f"{window:,} tokens, so summarising cannot make room. Start a new chat, choose "
            f"Search it instead for {documents[0]}, or pick a model with a larger window."
        )
    return (
        f"The first message and the newest messages fill about {fixed:,} of {model}'s "
        f"{window:,} tokens, so summarising cannot make room. Start a new chat, or pick a model "
        "with a larger window."
    )


def failed_words(words: str, about: int, window: int | None) -> str:
    of = f"about {about:,} of {window:,} tokens" if window else f"about {about:,} tokens"
    return f"Workbench could not summarise earlier messages: {words.rstrip('.')}. The chat is {of}."


def truncated_words(model: str | None) -> str:
    return (
        f"{model or 'The model'} dropped part of this chat to fit its window, so this answer may "
        "miss earlier messages. The next message is sent with a summary."
    )


def tools_filled_words(model: str | None, rounds: int) -> str:
    return (
        f"This answer's tool results filled {model or 'the model'}'s window after {rounds} "
        f"round{'' if rounds == 1 else 's'}. Send again: earlier messages will be summarised."
    )


def progress_of(messages: int, model: str) -> dict[str, Any]:
    """An answer's progress while a summary is written."""
    return {"stage": "summarising", "messages": messages, "model": model}


@dataclass
class Record:
    """What an answer was sent of one summary (`Message.summaries`)."""

    id: str
    first_id: str
    last_id: str
    first: int
    last: int
    tokens: int
    made: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "firstId": self.first_id,
            "lastId": self.last_id,
            "from": self.first,
            "to": self.last,
            "messages": self.last - self.first + 1,
            "tokens": self.tokens,
            "made": self.made,
            **self.extra,
        }
