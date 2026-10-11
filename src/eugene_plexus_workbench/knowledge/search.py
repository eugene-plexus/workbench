"""Finding the passages that answer a question (MR4, design 4.4).

A person's question becomes a search of their documents, and what it finds becomes
the passages a model is shown. This module is the middle of that: it turns the words
of a chat into a query (`plan_query`), asks each way of searching for candidates
(`Signal`; MR4 has one, **words**: FTS5 with BM25 over the passages), joins what they
found (`fuse`), chooses which to keep (`select`: at most `k`, at most three from one
document unless the question names it), joins the passages that follow each other
(`merge`), and fits them to a number of tokens (`pack`). `Retriever` is those steps in
order, and **the way to add a way of searching**: search by meaning (MR5) is another
`Signal` whose hits `fuse` joins by reciprocal rank (k = 60), with nothing here
changed. Everything but a `Signal`'s own search is a function of its arguments.

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

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from .. import token_count
from .index import Hit, Index

#: Candidates a way of searching hands on, before the best are chosen.
CANDIDATES = 50
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
    return Query(tuple(terms), tuple(phrases))


# --------------------------------------------------------------------------- #
# ways of searching
# --------------------------------------------------------------------------- #


class Signal(Protocol):
    """One way of finding passages. Its hits are best first."""

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


def fuse(found: dict[str, list[Hit]]) -> list[Candidate]:
    """What several ways of searching found, as one list, best first.

    One way: its own order. Several: reciprocal rank fusion (each list adds
    `1 / (60 + rank)` to a passage, so a passage found by both is first, and one found
    by a single way still appears), equal weights, ties in the order the ways were given."""
    if len(found) == 1:
        [(name, hits)] = found.items()
        return [Candidate(h, (name,)) for h in hits]
    scores: dict[int, float] = {}
    first: dict[int, int] = {}
    by_chunk: dict[int, Hit] = {}
    via: dict[int, list[str]] = {}
    for name, hits in found.items():
        for rank, hit in enumerate(hits, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            first.setdefault(hit.chunk_id, len(first))
            by_chunk.setdefault(hit.chunk_id, hit)
            via.setdefault(hit.chunk_id, []).append(name)
    order = sorted(scores, key=lambda c: (-scores[c], first[c]))
    return [Candidate(by_chunk[c], tuple(via[c])) for c in order]


# --------------------------------------------------------------------------- #
# choosing, joining, fitting
# --------------------------------------------------------------------------- #


def names_document(name: str, question: str) -> bool:
    """Whether a question names a file: every word of its name (without the
    extension) is in the question, and at least one of them is a real word."""
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
class Retrieval:
    """What a search came to."""

    query: Query
    #: The passages to send, in order, numbered from 1.
    passages: list[Retrieved] = field(default_factory=list)
    #: Candidates the ways of searching found, before the best were chosen.
    found: int = 0
    #: Passages that were chosen and did not fit the budget.
    did_not_fit: int = 0
    mode: str = "words"
    ways: tuple[str, ...] = ("words",)


class Retriever:
    """Every way of searching, and the steps that follow them. One per Workbench."""

    def __init__(self, signals: Sequence[Signal] | None = None) -> None:
        self.signals: list[Signal] = list(signals) if signals else [WordSignal()]

    @property
    def mode(self) -> str:
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
    ) -> Retrieval:
        """Search `scopes` of one person's index for `query`. `question` is what the
        person asked (a file it names is not held to `per_document`); `budget` is the
        tokens the passages may take, and `overhead` what each one's tags cost.

        Raises what a search raises (`IndexUnavailable`, an `sqlite3` error): a search
        that could not run is never an empty result."""
        found: dict[str, list[Hit]] = {}
        for signal in self.signals:
            found[signal.name] = await signal.search(index, query, scopes, CANDIDATES)
        candidates = fuse(found)
        if not candidates:
            return Retrieval(query, mode=self.mode, ways=tuple(found))
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
            mode=self.mode,
            ways=tuple(found),
        )
