"""Which of a person's notes bear on what they are asking (MR8; MV1, `workbench-memory-v2.md` 6).

Models do not ignore memory that has nothing to do with the question (PersistBench: a median
failure of 53% across domains), so a turn is sent the notes that are always used (the pinned
block, frozen in the system message) and then **only notes about what the person just asked**,
chosen again for every turn (`choose`), and placed in their latest message.

**Found three ways, at the level of notes**: by words in the person's knowledge index (the
`notes` scope, where every passage's heading holds the note's tags and title, weighed twice
the text), by words in the store's own index of titles (stemmed: "cooking" finds "cook"), and
by meaning through the search model the person's documents already use, **only when it runs
on the install's own machines** (a note is a profile of the person; no search model is started
for this, MRC28). The three rankings of notes are fused (RRF, k = 60). By meaning, a note
counts above the model's measured floor, else only the best `MEANING_TOP`.

**Without meaning** (no local search model, it failed, or it took more than `MEANING_SECONDS`):
the notes found by words, then the newest, until the budget is full, and the reason is said.
So a person with a few notes gets them all, as before ("Is vegetarian." still reaches "What
should I cook tonight?"), and one with 20,000 gets the ones that match rather than only the
newest. Without the index at all, the titles' index stands in, and the cause is said.

**Every passage is checked against the store** before it is used: its note must be the
person's, current, and at the version the index holds (`notes_index.digest`), so an index
that lags can cost recall, never send a deleted, replaced or edited-away word.

Logs carry counts and causes, never a note's words.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import memory, notes_index
from .knowledge.index import NOTES, Hit, IndexUnavailable
from .knowledge.search import Query, SignalUnavailable, WordSignal, plan_query
from .store import MemoryNote, Store, tag_key

log = logging.getLogger(__name__)

RRF_K = 60
#: Notes and passages each way returns at most.
WORDS_LIMIT = 200
MEANING_LIMIT = 200
MEANING_TOP = 8
MEANING_SECONDS = 3.0
#: Passages of one note's body sent in one turn.
PASSAGES_PER_NOTE = 3

#: How the notes were found.
WORDS_AND_MEANING = "meaning and words"
WORDS = "words"
ALL_THAT_FIT = "all"

NO_SEARCH_MODEL = (
    "Chosen by words: choosing by meaning needs a search model on your machines, and none is "
    "running. Start one in Eugene's console under Backends."
)


NOT_YET = (
    "Chosen by words: your notes are still being added to search by meaning. It runs by "
    "itself while a search model is running."
)


@dataclass
class Chosen:
    """The notes for one turn or one search, best first, and how they were found."""

    candidates: list[memory.Candidate] = field(default_factory=list)
    #: `meaning and words`, `words`, or `all` (the person asked for every note that fits).
    how: str = WORDS_AND_MEANING
    #: Why meaning was not used, in words a person reads; None when it was, or was not wanted.
    why: str | None = None
    #: How many notes there were to choose from.
    of: int = 0


def fuse(*rankings: Sequence[str]) -> list[str]:
    """Reciprocal-rank fusion of rankings of ids (k = 60), best first; ties keep the order
    of the first ranking that has them."""
    scores: dict[str, float] = {}
    order: dict[str, int] = {}
    for ranking in rankings:
        for rank, note_id in enumerate(ranking):
            scores[note_id] = scores.get(note_id, 0.0) + 1.0 / (RRF_K + rank + 1)
            order.setdefault(note_id, len(order))
    return sorted(scores, key=lambda i: (-scores[i], order[i]))


_TAG_MARK = re.compile(r"(?<![\w#])#([^\s#,]{1,40})")


def split_tags(query: str) -> tuple[str, list[str]]:
    """A search's `#tag` filters and the rest of it: `("lisbon trip", ["travel"])`."""
    tags = [m.group(1) for m in _TAG_MARK.finditer(query)]
    rest = " ".join(_TAG_MARK.sub(" ", query).split())
    return rest, tags


def _added(note: MemoryNote) -> str:
    moment = note.valid_from or note.created_at
    return datetime.fromtimestamp(moment, UTC).strftime("%Y-%m-%d")


async def available(meaning: object | None) -> tuple[bool, str | None]:
    """Whether choosing by meaning can run now (a local search model is running), and if
    not, why, for the Memory page: it says "by meaning" only when that is what happens."""
    if meaning is None:
        return False, NO_SEARCH_MODEL
    try:
        resolved = await meaning.resolve()  # type: ignore[attr-defined]
    except Exception as exc:
        log.warning(
            "memory relevance could not find a search model: %s: %s", type(exc).__name__, exc
        )
        return False, NO_SEARCH_MODEL
    if resolved.model is None or not resolved.model.local:
        return False, NO_SEARCH_MODEL
    return True, None


class Finder:
    """Finds notes. `saved` is the people's `SavedDocuments` (their indexes and the search by
    meaning), or None: then the titles' index only."""

    def __init__(self, store: Store, saved: Any) -> None:
        self.store = store
        self.saved = saved

    async def choose(
        self,
        owner: str,
        latest: str,
        previous: str | None,
        notes: Sequence[MemoryNote],
        *,
        pinned: Sequence[str] = (),
        deferred: Sequence[str] = (),
        today: str | None = None,
        everything: bool = False,
    ) -> Chosen:
        """The notes for a turn whose latest message is `latest`, best first. `notes` are
        the person's current notes (no bodies needed); `pinned` are the ids the pinned block
        already sends, and `deferred` those of them whose bodies it could not (their passages
        may still go). With `everything` (the person chose to use every note that fits): the
        newest first, unsearched. Never raises."""
        # A note always used is the pinned block's: one pinned after the chat froze its
        # block waits for Refresh, as the drawer says; only a pinned body that did not fit
        # goes by relevance.
        del pinned
        bodies = set(deferred)
        pool = [
            n
            for n in notes
            if n.status == "active"
            and (not memory.is_always(n) or n.id in bodies)
            and not memory.is_past(n, today)
        ]
        newest = sorted(pool, key=lambda n: (-n.created_at, n.id))
        if everything:
            return Chosen(
                [self._candidate(n, (), "recent") for n in newest], ALL_THAT_FIT, of=len(pool)
            )
        if not pool:
            return Chosen(of=0)
        # The latest message alone: a turn's notes follow what is asked now. The one before it
        # is searched only when the latest has nothing to search ("and the second one?").
        query = plan_query(latest)
        if query.empty and previous:
            query = plan_query(latest, previous)
        try:
            return await self._search(owner, query, pool, newest, fill=True)
        except Exception as exc:  # memory never stops an answer
            log.warning("choosing notes for a turn failed: %s: %s", type(exc).__name__, exc)
            return Chosen(
                [self._candidate(n, (), "recent") for n in newest],
                WORDS,
                "Chosen as the newest that fit: searching your notes failed "
                f"({type(exc).__name__}).",
                of=len(pool),
            )

    async def search(
        self,
        owner: str,
        text: str,
        notes: Sequence[MemoryNote],
        *,
        today: str | None = None,
    ) -> Chosen:
        """The notes that match a search a person or a model made (`#tag` filters), best
        first; only those that match. Raises what the index raises."""
        rest, tags = split_tags(text)
        wanted = {tag_key(t) for t in tags}
        pool = [
            n
            for n in notes
            if n.status == "active"
            and not memory.is_past(n, today)
            and wanted <= {tag_key(t) for t in n.tags}
        ]
        if not rest.strip():
            newest = sorted(pool, key=lambda n: (-n.created_at, n.id))
            return Chosen([self._candidate(n, (), "recent") for n in newest], WORDS, of=len(pool))
        return await self._search(owner, plan_query(rest), pool, [], fill=False)

    # --- the ways of searching -------------------------------------------

    @staticmethod
    def _candidate(
        note: MemoryNote, passages: Sequence[tuple[int, str]], via: str
    ) -> memory.Candidate:
        return memory.Candidate(
            id=note.id,
            text=note.text,
            kind=note.kind,
            tags=tuple(note.tags),
            added=_added(note),
            passages=tuple(sorted(passages)),
            via=via,
        )

    async def _titles(self, owner: str, query: Query) -> list[str]:
        expression = memory.match_expression(memory.search_words(query.asked or query.text))
        if expression is None:
            return []
        return [n.id for n in await self.store.search_memory(owner, expression, limit=WORDS_LIMIT)]

    async def _meaning(self, index: Any, query: Query) -> tuple[list[Hit] | None, str | None]:
        """Hits by meaning, or `(None, why not)`. A search model outside the machines is
        never asked: not for a note, and not for the question that would find one."""
        meaning = getattr(self.saved, "meaning", None)
        if meaning is None:
            return None, NO_SEARCH_MODEL
        resolved = await meaning.resolve(meaning._built(await index.embedder()))
        model = resolved.model
        if model is None or not model.local:
            return None, NO_SEARCH_MODEL
        # A search model that came after the notes were put is noticed here (MV2's live run:
        # passages left words only by a restart waited until something else woke the worker).
        await meaning.touch(index)
        if not (await index.meaning_counts(NOTES)).embedded:
            # Nothing of the notes is searchable by meaning yet (just upgraded, or just
            # started): what meaning would find is nothing, which is not an answer.
            return None, NOT_YET
        try:
            hits = await asyncio.wait_for(
                meaning.signal.search(index, query, [NOTES], MEANING_LIMIT), MEANING_SECONDS
            )
        except TimeoutError:
            return None, f"Chosen by words: the search model {model.id} took too long."
        except SignalUnavailable as exc:
            return None, f"Chosen by words: {exc.message}" if exc.message else NO_SEARCH_MODEL
        floor = await meaning.floor(index)
        if floor is not None:
            return [h for h in hits if h.score > floor], None
        best: list[Hit] = []
        seen: set[str] = set()
        for hit in hits:
            if hit.doc_id not in seen:
                seen.add(hit.doc_id)
                if len(seen) > MEANING_TOP:
                    break
            best.append(hit)
        return best, None

    async def _search(
        self,
        owner: str,
        query: Query,
        pool: Sequence[MemoryNote],
        newest: Sequence[MemoryNote],
        *,
        fill: bool,
    ) -> Chosen:
        by_id = {n.id: n for n in pool}
        titles = (
            [i for i in await self._titles(owner, query) if i in by_id] if not query.empty else []
        )
        index = self.saved.index(owner, create=False) if self.saved is not None else None
        why: str | None = None
        words_hits: list[Hit] = []
        meaning_hits: list[Hit] | None = None
        if index is None:
            why = NO_SEARCH_MODEL
        else:
            try:
                if not query.empty:
                    words_hits = await WordSignal().search(index, query, [NOTES], WORDS_LIMIT)
                meaning_hits, why = await self._meaning(index, query)
            except IndexUnavailable as exc:
                log.warning(
                    "a person's notes could not be searched in their index: %s", exc.message
                )
                why = f"Chosen by the titles of your notes: {exc.message}"
                words_hits, meaning_hits = [], None
        hits = [*words_hits, *(meaning_hits or [])]
        fresh = await self._fresh(owner, index, hits, by_id) if hits else set()
        words_order = _note_order(h for h in words_hits if h.doc_id in fresh)
        meaning_order = _note_order(h for h in (meaning_hits or []) if h.doc_id in fresh)
        ranked = fuse(words_order, titles, meaning_order)
        passages: dict[str, dict[int, str]] = {}
        for hit in hits:
            if hit.doc_id in fresh and hit.ord > 0:
                kept = passages.setdefault(hit.doc_id, {})
                if len(kept) < PASSAGES_PER_NOTE:
                    kept[hit.ord] = hit.text
        by_words = set(words_order) | set(titles)
        by_meaning = set(meaning_order)
        out = [
            self._candidate(
                by_id[i],
                list(passages.get(i, {}).items()),
                "both"
                if i in by_words and i in by_meaning
                else "meaning"
                if i in by_meaning
                else "words",
            )
            for i in ranked
            if i in by_id
        ]
        if meaning_hits is None and fill:
            seen = set(ranked)
            out += [self._candidate(n, (), "recent") for n in newest if n.id not in seen]
        return Chosen(out, WORDS if meaning_hits is None else WORDS_AND_MEANING, why, of=len(pool))

    async def _fresh(
        self, owner: str, index: Any, hits: Sequence[Hit], by_id: dict[str, MemoryNote]
    ) -> set[str]:
        """The notes among the hits whose passages are of the version the store has now."""
        wanted = {h.doc_id for h in hits if h.doc_id in by_id}
        if not wanted or index is None:
            return set()
        held = await index.note_digests()
        current = await self.store.memory_notes_by_ids(owner, sorted(wanted), with_body=True)
        return {
            n.id
            for n in current
            if n.status == "active" and held.get(n.id) == notes_index.digest(n)
        }


def _note_order(hits: Any) -> list[str]:
    order: list[str] = []
    for hit in hits:
        if hit.doc_id not in order:
            order.append(hit.doc_id)
    return order
