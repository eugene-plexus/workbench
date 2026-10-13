"""Finding the passages that answer a question (MR4, design 4.4).

A person's question becomes a search of their documents, and what it finds becomes
the passages a model is shown. This module is the middle of that: it turns the words
of a chat into a query (`plan_query`), asks each way of searching for candidates
(`Signal`: **words**, FTS5 with BM25 over the passages, and **meaning** (MR5,
`meaning.MeaningSignal`): the passages whose vectors are nearest the question's), joins
what they found (`fuse`: reciprocal rank fusion, k = 60, equal weights), chooses which to
keep (`select`: at most `k`, at most three from one document unless the question names it),
joins the passages that follow each other (`merge`), and fits them to a number of tokens
(`pack`). `Retriever` is those steps in order, and **the way to add a way of searching**.
Everything but a `Signal`'s own search is a function of its arguments.

**A way of searching that cannot run does not stop the others.** The search by meaning
needs a search model, and it is a stranger to this process: it may be stopped, or changed,
or at its limit. A signal that raises `SignalUnavailable` is left out of that search, the
rest go on, and what it said (`Report`) is kept with the result, so a person is told "by
words only: the search model is not running" and is never told "nothing matched" for a
search that did not run. A way of searching that fails any other way raises, as before.

**Sorting what was found** (MR6b, design `workbench-sorting.md`). The best `SORT_FROM_EACH` (10)
passages of each way of searching, each passage once (up to 20), may be sent to a result sorter,
each as its headings, a new line and its words (`sheet_of`); what comes back is the order they are
put in, ahead of the rest, which follow in the order they were fused in. `Retriever.retrieve` does
it between `fuse` and `select` when it has a `Sorter` (`sorting.Sorting`). **A sorter that cannot
or should not run never fails a search**: its passages stay in the fused order, and the
`SortReport` says why, in the sorter's own words. Which sorter, and whether it is quick enough,
are `sorting.py`'s to decide; this module asks, and applies the answer. A sorter's score is never
shown, only the place it gave. A run that names its ways (Test a question, "try one way at a time")
is not sorted, so that each way shows what it finds alone.

**The one evidence rule** (design 4.4, item 7): there is no score threshold, and a
passage is never left out for a low score. But a signal whose scores are similarities
(meaning) may say a best similarity under its floor is not evidence by itself; then, and
only when the words found nothing, its passages are dropped and nothing matched. A
run that names its ways (`ways`: Test a question, to see what each finds) has no such rule.

**What a query is.** The latest message (at most 1,000 characters), then the one
before it (at most 300), so that "and the second one?" still searches something; no
model rewrites it, and the words that were searched for are shown under the answer.
Its terms are the words of at least three characters that are not stop words, at most
twelve, the latest message's first; each is quoted, so nothing a person typed can be
read as search syntax. Words a person quoted, and identifier-like tokens (those with a
digit or a hyphen: `135-SD-12`, `TAMARIND-6042`, the case words search finds and
meaning does not), are also searched as exact phrases.

**There is no score threshold.** A threshold that filters the answer away is the
complaint; the best passages are sent and the model is told they may not answer. What
the search finds nothing for is `nothing matched`, a normal result; a search that
could not run raises, and is never that.

BM25 scores are of one scale and mean nothing beside another's, so what a person is
shown is a rank (the order the passages are in) and how each was found (`via`), never a
number.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from .. import token_count
from . import embedders
from .index import Hit, Index

log = logging.getLogger(__name__)

#: Candidates a way of searching hands on, before the best are chosen.
CANDIDATES = 50
#: Passages each way of searching hands a result sorter, best first (design call 2). Measured on
#: MR5's 60 questions: the best 10 of each, with their headings, gave recall@5 0.750 against the
#: fused order's 0.600; the fused order's own 10 gave 0.667.
SORT_FROM_EACH = 10
#: What a result sorter's place is called in `ranks`, beside `words` and `meaning`.
SORTER = "sorter"
#: Passages sent by default, the most a person may ask for, and from one document.
PASSAGES = 5
PASSAGES_MAX = 10
PER_DOCUMENT = 3
#: A merged passage is never longer than this, in tokens.
MERGE_MAX_TOKENS = 1_000
#: What `fuse` adds to a rank: the usual constant of reciprocal rank fusion.
RRF_K = 60
LATEST_CHARS = 1_000
PREVIOUS_CHARS = 300
MAX_TERMS = 12
MAX_PHRASES = 6
MIN_TERM = 3
SNIPPET = 240

#: Words that say nothing about what a question is about: English function words, and
#: the words of asking about a file ("in the PDF, what ... on page 4").
_STOP_WORDS = (
    "about above after again all also and any are because been before being below between both "
    "but can could did does doing down during each few for from further had has have having "
    "her here hers him his how into its just may might more most must not now off once only "
    "other our ours out over own same shall she should some such than that the their theirs "
    "them then there these they this those through too under until upon very was were what "
    "when where which while who whom whose why will with would you your yours yourself "
    "tell show give please find list explain describe say says said mentioned according "
    "document documents file files pdf page pages"
)
_STOP = frozenset(_STOP_WORDS.split())
_WORD = re.compile(r"[^\W_]+")
_IDENTIFIER = re.compile(r"[^\W_]+(?:[-./:][^\W_]+)+")
_QUOTED = re.compile('"([^"]{2,200})"|“([^”]{2,200})”')
_CJK = re.compile(
    "[\U00003040-\U000030ff\U00003400-\U00004dbf\U00004e00-\U00009fff\U0000ac00-\U0000d7af]"
)


def fold(text: str) -> str:
    """Text as the search compares it: case and accents taken off (the tokenizer's
    `remove_diacritics 2` does the same to the index, so "Café" is "cafe" on both sides)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def words_of(text: str) -> list[str]:
    return _WORD.findall(fold(text))


# --------------------------------------------------------------------------- #
# the query
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Query:
    """What is searched for: single words, and exact phrases."""

    terms: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()
    #: The question as a search by meaning reads it: the latest message and, after it,
    #: the start of the one before (design 4.4, step 1). The words search reads `terms`.
    asked: str = ""

    @property
    def empty(self) -> bool:
        return not self.terms and not self.phrases

    @property
    def text(self) -> str:
        """The query as a person reads it under the answer: "Searched for: ..."."""
        return " ".join([*self.terms, *(f'"{p}"' for p in self.phrases)])

    def match(self) -> str | None:
        """An FTS5 expression finding any of these (each quoted, so nothing a person
        typed is search syntax), or None when there is nothing to search for."""
        parts = [f'"{t}"' for t in self.terms] + [f'"{p}"' for p in self.phrases]
        return " OR ".join(parts) if parts else None


def _worth(word: str) -> bool:
    return (len(word) >= MIN_TERM or _CJK.search(word) is not None) and word not in _STOP


def plan_query(latest: str, previous: str | None = None) -> Query:
    """The query for a message, and the one before it when there is one."""
    latest = latest[:LATEST_CHARS]
    terms: list[str] = []
    for word in words_of(latest) + words_of((previous or "")[:PREVIOUS_CHARS]):
        if _worth(word) and word not in terms:
            terms.append(word)
        if len(terms) >= MAX_TERMS:
            break
    phrases: list[str] = []
    quoted = [a or b for a, b in _QUOTED.findall(latest)]
    identifiers = [
        m for m in _IDENTIFIER.findall(latest) if any(c.isdigit() or c == "-" for c in m)
    ]
    for found in [*quoted, *identifiers]:
        parts = words_of(found)
        phrase = " ".join(parts)
        if phrase and phrase not in phrases and (len(parts) > 1 or phrase not in terms):
            phrases.append(phrase)
        if len(phrases) >= MAX_PHRASES:
            break
    asked = latest.strip()
    before = (previous or "")[:PREVIOUS_CHARS].strip()
    if before:
        asked = f"{asked}\n\n{before}"
    return Query(tuple(terms), tuple(phrases), asked)


# --------------------------------------------------------------------------- #
# ways of searching
# --------------------------------------------------------------------------- #


class SignalUnavailable(Exception):
    """A way of searching could not run now, and why, in the words a person reads. The
    search goes on without it, and says so (`Report`)."""

    def __init__(self, code: str, message: str, *, quiet: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        #: Not worth saying beside every answer: the way of searching was never in play
        #: (a person with no search model is not told, each time, that it is not running).
        self.quiet = quiet


class Signal(Protocol):
    """One way of finding passages. Its hits are best first.

    It may also have `similarity = True` (its hits' scores are similarities, cosine, higher
    better), and `async floor(index) -> float | None`, the best similarity that is not by
    itself an answer; `Retriever` uses both for the one evidence rule. Raises
    `SignalUnavailable` when it cannot run now."""

    #: What a person is told found a passage: `words`, and `meaning` (MR5).
    name: str

    async def search(
        self, index: Index, query: Query, scopes: Sequence[str], limit: int
    ) -> list[Hit]: ...


class WordSignal:
    """Words: the passages whose text or heading has any of the query's words, or one
    of its phrases, best first by BM25 (a heading weighs twice a word of the text)."""

    name = "words"

    async def search(
        self, index: Index, query: Query, scopes: Sequence[str], limit: int
    ) -> list[Hit]:
        match = query.match()
        if match is None:
            return []
        return await index.search(match, scopes, limit)


@dataclass(frozen=True)
class Candidate:
    """A passage a search found, and which ways of searching found it."""

    hit: Hit
    via: tuple[str, ...]
    #: Where each way that found it put it (1 is best), `(name, rank)`: why it is where it is.
    ranks: tuple[tuple[str, int], ...] = ()
    #: How near the question it was, for a way whose scores are similarities (meaning).
    similarity: float | None = None


def fuse(found: dict[str, list[Hit]], similar: Collection[str] = frozenset()) -> list[Candidate]:
    """What several ways of searching found, as one list, best first.

    One way: its own order. Several: reciprocal rank fusion (each list adds
    `1 / (60 + rank)` to a passage, so a passage found by both is first, and one found
    by a single way still appears), equal weights, ties in the order the ways were given.
    `similar` names the ways whose scores are similarities, kept with each candidate."""
    if len(found) == 1:
        [(name, hits)] = found.items()
        return [
            Candidate(h, (name,), ((name, rank),), h.score if name in similar else None)
            for rank, h in enumerate(hits, start=1)
        ]
    scores: dict[int, float] = {}
    first: dict[int, int] = {}
    by_chunk: dict[int, Hit] = {}
    via: dict[int, list[str]] = {}
    ranks: dict[int, list[tuple[str, int]]] = {}
    similarity: dict[int, float] = {}
    for name, hits in found.items():
        for rank, hit in enumerate(hits, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            first.setdefault(hit.chunk_id, len(first))
            by_chunk.setdefault(hit.chunk_id, hit)
            via.setdefault(hit.chunk_id, []).append(name)
            ranks.setdefault(hit.chunk_id, []).append((name, rank))
            if name in similar:
                similarity[hit.chunk_id] = hit.score
    order = sorted(scores, key=lambda c: (-scores[c], first[c]))
    return [
        Candidate(by_chunk[c], tuple(via[c]), tuple(ranks[c]), similarity.get(c)) for c in order
    ]


# --------------------------------------------------------------------------- #
# sorting what was found
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Sheet:
    """A passage as a result sorter is sent it."""

    #: Its headings, a new line, its words: the text a search model is given for it
    #: (`embedders.passage_input`), without any model's prefix.
    text: str
    #: What reading it is worth, in tokens: the passage's own count (`Hit.tokens_est`).
    tokens: int


def sheet_of(hit: Hit) -> Sheet:
    """The passage as a sorter is sent it. Without its headings a sorter loses what the words
    leave out ("Trade-off: ..." under "J25. Windows, while the person is signed out"): measured,
    text alone gained nothing (recall@5 +0.000 on the fused 10) and with the headings +0.067."""
    return Sheet(
        embedders.passage_input(embedders.PLAIN, hit.heading_path, hit.text), hit.tokens_est
    )


@dataclass
class SortReport:
    """What sorting the passages a search found came to, in what a person is told of it."""

    #: Whether the passages were put in the sorter's order. When not, `code` and `message` say
    #: why, in words (`not_running`, `over_budget`, `slow`, ...; `off` and `none_set_up` are
    #: `quiet`: the sorter was never in play, and that is not worth saying beside every answer).
    sorted: bool = False
    #: The sorter that sorted, or that would have: the one named, or the one chosen.
    model: str | None = None
    code: str | None = None
    message: str | None = None
    quiet: bool = False
    #: The setting as it is: `auto`, `off`, or the id the owner named.
    setting: str | None = None
    #: How many passages were (or would have been) sent, and what reading them is worth.
    passages: int = 0
    tokens: int | None = None
    #: The tokens a second the sorter was priced at, the seconds the sort was expected to take,
    #: and the seconds Sort time allows: what the decision was made of.
    rate: float | None = None
    expected: float | None = None
    budget: float | None = None
    #: How long the sort took, when it ran.
    seconds: float | None = None


@dataclass(frozen=True)
class Sorted:
    """A sorter's answer: the order it put the sheets in, or why it did not."""

    #: Where each sheet is, best first: every position of the sheets sent, each once. None when
    #: they were not sorted (`report` says why).
    order: tuple[int, ...] | None
    report: SortReport


class Sorter(Protocol):
    """A result sorter: puts the passages a search found in the order that answers the
    question best (`sorting.Sorting` is one). It decides for itself whether to run, and says
    so in the report; it may raise, and the search goes on without it."""

    async def sort(self, query: str, sheets: Sequence[Sheet]) -> Sorted: ...


def sort_pool(
    candidates: Sequence[Candidate],
    found: Mapping[str, Sequence[Hit]],
    each: int = SORT_FROM_EACH,
) -> list[Candidate]:
    """The passages a sorter is given: the best `each` of every way of searching, each passage
    once, in the order the ways were fused in (so a tie in the sorter's scores is the fused
    order's). A way that found fewer gives what it found; one way alone gives its best `each`."""
    wanted = {hit.chunk_id for hits in found.values() for hit in hits[:each]}
    return [c for c in candidates if c.hit.chunk_id in wanted]


def sorted_by(
    candidates: Sequence[Candidate], pool: Sequence[Candidate], order: Sequence[int]
) -> list[Candidate]:
    """`candidates` with the passages of `pool` first, in `order` (positions in `pool`, best
    first), each with the place the sorter gave it (`ranks`), and then the rest in the order
    they were in. The ways that found a passage (`via`) are not the sorter's to change."""
    placed = [
        replace(pool[position], ranks=(*pool[position].ranks, (SORTER, place)))
        for place, position in enumerate(order, start=1)
    ]
    sent = {c.hit.chunk_id for c in pool}
    return [*placed, *(c for c in candidates if c.hit.chunk_id not in sent)]


def sorted_mode(mode: str, sort: SortReport | None) -> str:
    """How a search is told when its passages were sorted: `meaning and words, sorted by
    Qwen3-Reranker`; as it was when they were not."""
    if sort is not None and sort.sorted and sort.model:
        return f"{mode}, sorted by {sort.model}"
    return mode


# --------------------------------------------------------------------------- #
# choosing, joining, fitting
# --------------------------------------------------------------------------- #


def names_document(name: str, question: str) -> bool:
    """Whether a question names a file: every word of its name (without the
    extension) is in the question, and at least one of them is a real word."""
    name = name.rsplit("/", 1)[-1]  # a file of a folder is named `folder/path/file.md`
    stem = name.rsplit(".", 1)[0] if "." in name else name
    parts = words_of(stem)
    if not parts or all(len(p) < MIN_TERM for p in parts):
        return False
    asked = set(words_of(question))
    return all(p in asked for p in parts)


def select(
    candidates: Sequence[Candidate], *, k: int, per_document: int, named: set[str]
) -> list[Candidate]:
    """The best `k`, taking at most `per_document` from one file, unless the question
    names that file (then as many as `k` allows)."""
    chosen: list[Candidate] = []
    count: dict[str, int] = {}
    for candidate in candidates:
        doc = candidate.hit.doc_id
        if doc not in named and count.get(doc, 0) >= per_document:
            continue
        chosen.append(candidate)
        count[doc] = count.get(doc, 0) + 1
        if len(chosen) >= k:
            break
    return chosen


@dataclass(frozen=True)
class Retrieved:
    """A passage as a model is shown it: one, or several that follow each other in one file."""

    #: 1, 2, 3 ... best first: the number the model is asked to cite, `[1]`.
    rank: int
    doc_id: str
    name: str
    scope: str
    kind: str
    text: str
    heading: str | None
    page_start: int | None
    page_end: int | None
    line_start: int | None
    line_end: int | None
    chunk_ids: tuple[int, ...]
    tokens: int
    via: str
    #: Which of the query's words and phrases are in it, and its heading: why it was found.
    terms: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()
    in_heading: tuple[str, ...] = ()
    #: Where each way of searching put it, `(name, rank)`, 1 best: never a score, which is
    #: of a scale that means nothing beside another's.
    ranks: tuple[tuple[str, int], ...] = ()
    #: How near the question it was by meaning, when meaning found it (cosine).
    similarity: float | None = None


def _via(names: Sequence[str]) -> str:
    seen = sorted(set(names))
    return seen[0] if len(seen) == 1 else "both"


def merge(chosen: Sequence[Candidate], *, limit: int = MERGE_MAX_TOKENS) -> list[Retrieved]:
    """The chosen passages, with those that follow each other in one file under one
    heading made one passage with one citation (never longer than `limit` tokens, and
    as good as its best member). Best first, numbered from 1."""
    order = {c.hit.chunk_id: i for i, c in enumerate(chosen)}
    by_doc: dict[str, list[Candidate]] = {}
    for candidate in chosen:
        by_doc.setdefault(candidate.hit.doc_id, []).append(candidate)
    runs: list[list[Candidate]] = []
    for group in by_doc.values():
        group.sort(key=lambda c: c.hit.ord)
        run = [group[0]]
        for candidate in group[1:]:
            last = run[-1].hit
            joined = token_count.estimate("\n\n".join(c.hit.text for c in [*run, candidate]))
            if (
                candidate.hit.ord == last.ord + 1
                and candidate.hit.heading_path == last.heading_path
                and joined <= limit
            ):
                run.append(candidate)
            else:
                runs.append(run)
                run = [candidate]
        runs.append(run)
    runs.sort(key=lambda r: min(order[c.hit.chunk_id] for c in r))
    out: list[Retrieved] = []
    for number, run in enumerate(runs, start=1):
        first = run[0].hit
        text = "\n\n".join(c.hit.text for c in run)
        pages = [p for c in run for p in (c.hit.page_start, c.hit.page_end) if p is not None]
        starts = [c.hit.line_start for c in run if c.hit.line_start is not None]
        ends = [c.hit.line_end for c in run if c.hit.line_end is not None]
        best: dict[str, int] = {}
        for c in run:
            for name, place in c.ranks:
                best[name] = min(place, best.get(name, place))
        similar = [c.similarity for c in run if c.similarity is not None]
        out.append(
            Retrieved(
                rank=number,
                doc_id=first.doc_id,
                name=first.name,
                scope=first.scope,
                kind=first.kind,
                text=text,
                heading=first.heading_path,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                line_start=min(starts) if starts else None,
                line_end=max(ends) if ends else None,
                chunk_ids=tuple(c.hit.chunk_id for c in run),
                tokens=token_count.estimate(text),
                via=_via([v for c in run for v in c.via]),
                ranks=tuple(sorted(best.items())),
                similarity=max(similar) if similar else None,
            )
        )
    return out


def pack(
    passages: Sequence[Retrieved], budget: int | None, overhead: int
) -> tuple[list[Retrieved], int]:
    """The passages that fit `budget` tokens (each costs its text and `overhead` for
    its tags), taken in order, renumbered from 1, and how many did not fit. A passage
    too big for what is left is passed over, and a smaller one after it is still taken.
    No budget is no limit."""
    if budget is None:
        return list(passages), 0
    fit: list[Retrieved] = []
    used = 0
    for passage in passages:
        cost = passage.tokens + overhead
        if used + cost <= budget:
            fit.append(passage)
            used += cost
    return [replace(p, rank=i) for i, p in enumerate(fit, start=1)], len(passages) - len(fit)


# --------------------------------------------------------------------------- #
# why a passage was found, and how it reads in a list
# --------------------------------------------------------------------------- #


def explain(query: Query, passage: Retrieved) -> Retrieved:
    """The passage with which of the query's words and phrases it holds filled in."""
    body = words_of(passage.text)
    in_body = set(body)
    in_heading = set(words_of(passage.heading or ""))
    flat = " " + " ".join(body) + " "
    return replace(
        passage,
        terms=tuple(t for t in query.terms if t in in_body),
        phrases=tuple(p for p in query.phrases if f" {p} " in flat),
        in_heading=tuple(t for t in query.terms if t in in_heading),
    )


def snippet(text: str, terms: Sequence[str] = (), limit: int = SNIPPET) -> str:
    """At most `limit` characters of a passage on one line, around the place where the
    most of the words that were searched for are together (or from its start when none
    is there), cut at spaces and marked with an ellipsis where it is cut. A word is a
    whole word: "word" is not found inside "words"."""
    one = " ".join(text.split())
    if len(one) <= limit:
        return one
    wanted = set(terms)
    found = [(m.start(), w) for m in _WORD.finditer(one) if (w := fold(m.group())) in wanted]
    at, best = 0, 0
    for here, _ in found:
        together = {w for s, w in found if here - limit // 4 <= s <= here + limit // 2}
        if len(together) > best:
            at, best = here, len(together)
    start = max(0, at - limit // 4)
    if start > 0:
        space = one.find(" ", start)
        start = space + 1 if 0 <= space < at else start
    room = limit - (1 if start > 0 else 0) - 1
    end = min(len(one), start + room)
    if end < len(one):
        space = one.rfind(" ", start, end)
        end = space if space > start + room // 2 else end
    cut = one[start:end].strip()
    return ("…" if start > 0 else "") + cut + ("…" if end < len(one) else "")


# --------------------------------------------------------------------------- #
# the whole search
# --------------------------------------------------------------------------- #


@dataclass
class Report:
    """What one way of searching came to, in what a person is told of it."""

    name: str
    #: Whether it ran. When not, `code` and `message` say why (`SignalUnavailable`).
    ran: bool
    hits: int = 0
    code: str | None = None
    message: str | None = None
    quiet: bool = False
    #: For a way whose scores are similarities: the best of its hits, the floor under which
    #: that is not evidence, and whether its hits were set aside for it.
    best: float | None = None
    floor: float | None = None
    below_floor: bool = False


@dataclass
class Retrieval:
    """What a search came to."""

    query: Query
    #: The passages to send, in order, numbered from 1.
    passages: list[Retrieved] = field(default_factory=list)
    #: Candidates the ways of searching found, before the best were chosen.
    found: int = 0
    #: Passages that were chosen and did not fit the budget.
    did_not_fit: int = 0
    #: How it searched: `words`, `meaning`, `meaning and words`, or `none` when no way of
    #: searching could run; and, when a result sorter put the passages in order, `, sorted by
    #: <the sorter>` after it (`meaning and words, sorted by Qwen3-Reranker`).
    mode: str = "words"
    #: The ways that ran, and what each said; and what the sorter said, under `sorter` (`ran` is
    #: whether it sorted, `hits` how many passages it put in order).
    ways: tuple[str, ...] = ("words",)
    reports: dict[str, Report] = field(default_factory=dict)
    #: What sorting came to: whether it sorted and, when not, why, with the numbers it was
    #: decided by. None when this Workbench has no sorter, nothing was found to sort, or the run
    #: named its ways (those are shown as each found them).
    sort: SortReport | None = None


def mode_of(ran: Sequence[str]) -> str:
    """How a search is told to a person, from the ways of searching that ran."""
    both = "meaning" in ran and "words" in ran
    if both:
        return "meaning and words"
    if "meaning" in ran:
        return "meaning"
    return "words" if "words" in ran else "none"


class Retriever:
    """Every way of searching, and the steps that follow them. One per Workbench."""

    def __init__(
        self, signals: Sequence[Signal] | None = None, sorter: Sorter | None = None
    ) -> None:
        self.signals: list[Signal] = list(signals) if signals else [WordSignal()]
        #: A result sorter that puts the best passages in order (MR6b), or None: the fused order.
        self.sorter = sorter

    @property
    def mode(self) -> str:
        """The ways of searching this Workbench has, told as a person reads them: not what
        a search came to (`Retrieval.mode`), for a way may not be able to run now."""
        return "meaning and words" if any(s.name == "meaning" for s in self.signals) else "words"

    async def retrieve(
        self,
        index: Index,
        query: Query,
        scopes: Sequence[str],
        *,
        question: str,
        budget: int | None,
        overhead: int,
        k: int = PASSAGES,
        per_document: int = PER_DOCUMENT,
        ways: Sequence[str] | None = None,
    ) -> Retrieval:
        """Search `scopes` of one person's index for `query`. `question` is what the
        person asked (a file it names is not held to `per_document`); `budget` is the
        tokens the passages may take, and `overhead` what each one's tags cost. `ways`
        names the ways of searching to use (Test a question, to see what each finds alone);
        none means all, and then the evidence rule applies and the passages are sorted (when a
        sorter runs). **A run that names its ways is not sorted**: what each way finds alone is
        shown as it found it, in its own order, so a person can tell the way from the sorter.

        Raises what a search raises (`IndexUnavailable`, an `sqlite3` error): a search
        that could not run is never an empty result. A way of searching that says it
        is unavailable (`SignalUnavailable`) is left out and reported. A result sorter that
        cannot run, or is not quick enough, or fails any way at all, is left out and reported
        too (`Retrieval.sort`): the passages are in the fused order, and the search is whole."""
        signals = [s for s in self.signals if ways is None or s.name in ways]
        found: dict[str, list[Hit]] = {}
        reports: dict[str, Report] = {}
        for signal in signals:
            try:
                hits = await signal.search(index, query, scopes, CANDIDATES)
            except SignalUnavailable as exc:
                reports[signal.name] = Report(
                    signal.name, False, code=exc.code, message=exc.message, quiet=exc.quiet
                )
                continue
            found[signal.name] = hits
            reports[signal.name] = Report(signal.name, True, hits=len(hits))
        await self._weigh(signals, index, found, reports, apply_floor=ways is None)
        ran = tuple(found)
        similar = frozenset(s.name for s in signals if getattr(s, "similarity", False))
        candidates = fuse(found, similar) if found else []
        if not candidates:
            return Retrieval(query, mode=mode_of(ran), ways=ran, reports=reports)
        sort: SortReport | None = None
        if self.sorter is not None and ways is None:
            candidates, sort = await self._sort(query, question, candidates, found)
            # Said among the reports, beside each way's: the sorter ran when it sorted, and when
            # it did not, `code` and `message` say why, in its own words.
            reports[SORTER] = Report(
                SORTER,
                sort.sorted,
                hits=sort.passages if sort.sorted else 0,
                code=sort.code,
                message=sort.message,
                quiet=sort.quiet,
            )
        named = {c.hit.doc_id for c in candidates if names_document(c.hit.name, question)}
        chosen = select(
            candidates, k=max(1, min(k, PASSAGES_MAX)), per_document=per_document, named=named
        )
        fit, dropped = pack(merge(chosen), budget, overhead)
        return Retrieval(
            query,
            passages=[explain(query, p) for p in fit],
            found=len(candidates),
            did_not_fit=dropped,
            mode=sorted_mode(mode_of(ran), sort),
            ways=ran,
            reports=reports,
            sort=sort,
        )

    async def _sort(
        self,
        query: Query,
        question: str,
        candidates: list[Candidate],
        found: Mapping[str, Sequence[Hit]],
    ) -> tuple[list[Candidate], SortReport]:
        """The candidates with the sorter's best first (`sorted_by`), and what sorting came to.
        Never raises: a sorter that fails is a search without a sorter."""
        assert self.sorter is not None
        pool = sort_pool(candidates, found)
        asked = (query.asked or question[:LATEST_CHARS]).strip()
        if len(pool) < 2 or not asked:
            # One passage has no order to be put in, and nothing asked has nothing to sort by.
            return candidates, SortReport(
                code="nothing_to_sort" if asked else "no_question", quiet=True, passages=len(pool)
            )
        try:
            done = await self.sorter.sort(asked, [sheet_of(c.hit) for c in pool])
        except Exception as exc:  # a sorter's failure is never a search's
            log.warning(
                "sorting the passages of a search failed in Workbench itself: %s",
                type(exc).__name__,
                exc_info=True,
            )
            return candidates, SortReport(
                code="error",
                message=(
                    f"Not sorted: sorting failed in Workbench itself ({type(exc).__name__}), so "
                    "the passages are in the order the search found them. See Workbench's log."
                ),
                passages=len(pool),
            )
        if done.order is None:
            return candidates, done.report
        if sorted(done.order) != list(range(len(pool))):
            log.warning(
                "a sorter gave an order that is not of the %d passages it was sent", len(pool)
            )
            return candidates, replace(
                done.report,
                sorted=False,
                code="unusable",
                message=(
                    "Not sorted: the sorter's answer did not place each of the "
                    f"{len(pool)} passages once, so they are in the order the search found them."
                ),
            )
        return sorted_by(candidates, pool, done.order), done.report

    @staticmethod
    async def _weigh(
        signals: Sequence[Signal],
        index: Index,
        found: dict[str, list[Hit]],
        reports: dict[str, Report],
        *,
        apply_floor: bool,
    ) -> None:
        """For a way of searching whose scores are similarities: its best, its floor, and the
        one evidence rule (design 4.4, item 7). Its passages are set aside, and nothing
        matched, only when it is the only way that found anything and its best is under its
        floor; any word that matched makes every passage meaning found count as it is."""
        for signal in signals:
            hits = found.get(signal.name)
            if hits is None or not getattr(signal, "similarity", False):
                continue
            report = reports[signal.name]
            if hits:
                report.best = max(h.score for h in hits)
            floor_of = getattr(signal, "floor", None)
            report.floor = await floor_of(index) if floor_of is not None else None
            others = any(found.get(name) for name in found if name != signal.name)
            if (
                apply_floor
                and report.floor is not None
                and report.best is not None
                and not others
                and report.best < report.floor
            ):
                found[signal.name] = []
                report.below_floor = True
