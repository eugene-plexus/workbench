"""Memory v2's MV6: the tidy pass. Deterministic; it proposes, and the person decides.

Run when the Memory page is read, at most once a day for a person (`tidy_at`). It proposes:

* **notes that say the same thing**: two current notes of the same kind whose titles are equal
  once normalised (`note_key`), or whose content words' stems overlap at least
  `NEAR_DUPLICATE` (MR8's measure) and that could make the same claim
  (`memory_suggest.same_claim`: the same no, the same numbers, the shared words in the same
  order). Two events must also fall on the same day. Candidates are found by prefix filtering
  (each note's rarest stems, as many as the overlap bar needs), so 20,000 notes cost a pass over
  them, not every pair;
* **events long past**: an event whose day was more than `PAST_DAYS` ago.

A proposal answered is kept with its answer (`memory_tidy.settled`), so a pair the person kept
both of, or an event they kept, is never proposed again. Supersession needs nothing: MV1 keeps
one current version of a chain when it is written. The pass checks that, and logs a chain that
branches.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import combinations
from typing import Any

from . import memory_suggest
from .store import MemoryNote, Store, note_key

log = logging.getLogger(__name__)

#: The pass runs at most this often for a person.
RUN_EVERY_SECONDS = 86_400.0
#: An event is proposed for removal this many days after its day (MV6-C5).
PAST_DAYS = 30
#: Proposals waiting at once, at most.
MAX_WAITING = 20
#: A stem shared by more notes than this finds no candidates (it is not rare).
BUCKET_LIMIT = 200


def _stems(note: MemoryNote) -> frozenset[str]:
    return frozenset(memory_suggest.stems(note.text).split())


def _same(a: MemoryNote, b: MemoryNote) -> bool:
    """Two notes of one kind and one place (MV7) that may say the same thing; two events, on
    the same day."""
    return (
        a.kind == b.kind
        and a.project_id == b.project_id
        and (a.kind != "event" or a.expires_at == b.expires_at)
    )


def _prefix(size: int) -> int:
    """How many of a note's rarest stems must be indexed so that any note it overlaps at
    least `NEAR_DUPLICATE` with shares one of them (prefix filtering)."""
    return max(1, size - math.ceil(memory_suggest.NEAR_DUPLICATE * size) + 1)


def duplicates(notes: list[MemoryNote]) -> list[tuple[str, str]]:
    """Pairs of current notes of one kind that say the same thing, oldest pairs first."""
    current = sorted((n for n in notes if n.status == "active"), key=lambda n: (n.created_at, n.id))
    order = {n.id: i for i, n in enumerate(current)}
    by_id = {n.id: n for n in current}
    pairs: set[tuple[str, str]] = set()
    same: dict[tuple[str, str, str | None, str | None], list[str]] = defaultdict(list)
    for n in current:
        day = n.expires_at if n.kind == "event" else None
        same[(n.kind, note_key(n.text), day, n.project_id)].append(n.id)
    for ids in same.values():
        pairs.update((ids[0], other) for other in ids[1:])
    words = {n.id: _stems(n) for n in current}
    df: Counter[str] = Counter(s for w in words.values() for s in w)
    buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
    for n in current:
        held = sorted(words[n.id], key=lambda s: (df[s], s))
        for stem in held[: _prefix(len(held))]:
            buckets[(n.kind, stem)].append(n.id)
    for ids in buckets.values():
        if len(ids) > BUCKET_LIMIT:
            continue
        for a, b in combinations(ids, 2):
            pair = (a, b) if order[a] < order[b] else (b, a)
            if pair in pairs:
                continue
            x, y = words[a], words[b]
            if not x or not y or len(x & y) / len(x | y) < memory_suggest.NEAR_DUPLICATE:
                continue
            first, second = by_id[a], by_id[b]
            if _same(first, second) and memory_suggest.same_claim(first.text, second.text):
                pairs.add(pair)
    return sorted(pairs, key=lambda p: (order[p[0]], order[p[1]]))


def past_events(notes: list[MemoryNote], today: str) -> list[str]:
    """Current events whose day was more than `PAST_DAYS` ago, oldest first."""
    before = (date.fromisoformat(today) - timedelta(days=PAST_DAYS)).isoformat()
    found = [
        n
        for n in notes
        if n.status == "active" and n.kind == "event" and n.expires_at and n.expires_at < before
    ]
    return [n.id for n in sorted(found, key=lambda n: (n.expires_at or "", n.id))]


def branching(notes: list[MemoryNote]) -> list[str]:
    """Notes two others both say they were replaced by: a chain that branches (none should)."""
    seen = Counter(n.superseded_by for n in notes if n.superseded_by)
    return sorted(k for k, v in seen.items() if k is not None and v > 1)


def _find(every: list[MemoryNote], today: str) -> list[tuple[str, list[str]]]:
    for note_id in branching(every):
        log.warning("a note's versions branch at %s; the tidy pass leaves it as it is", note_id)
    return [
        *(("duplicate", list(p)) for p in duplicates(every)),
        *(("past", [i]) for i in past_events(every, today)),
    ]


async def run(store: Store, owner: str, today: str, *, force: bool = False) -> int:
    """Propose what the pass finds, unless it ran for the person within the day. How many
    proposals it added. The finding is done off the event loop: it is the one part that
    grows with the person's notes."""
    prefs = await store.memory_prefs(owner)
    now = time.time()
    last = prefs.get("tidy_at")
    if not force and last is not None and now - last < RUN_EVERY_SECONDS:
        return 0
    every = await store.memory_notes(owner, status=None)
    found = await asyncio.to_thread(_find, every, today)
    rows = await store.tidy_rows(owner)
    known = {(r["kind"], tuple(sorted(r["note_ids"]))) for r in rows}
    waiting = sum(1 for r in rows if r["settled"] is None)
    added = 0
    for kind, members in found:
        if waiting >= MAX_WAITING:
            break
        if (kind, tuple(sorted(members))) in known:
            continue
        if await store.add_tidy(owner, kind, members):
            waiting += 1
            added += 1
    # Only once it has run: a pass that failed is tried again on the next read.
    await store.put_memory_prefs(owner, tidy_at=now)
    return added


async def proposals(store: Store, owner: str, today: str) -> list[dict[str, Any]]:
    """The proposals waiting for the person, each with its notes. One whose notes changed
    since (a note gone or replaced, an event moved) is dropped, and so is an answered one
    whose notes are all gone."""
    rows = await store.tidy_rows(owner)
    if not rows:
        return []
    waiting = [r for r in rows if r["settled"] is None]
    answered = [r for r in rows if r["settled"] is not None]
    notes = {
        n.id: n
        for n in await store.memory_notes_by_ids(
            owner, [i for r in waiting for i in r["note_ids"]], with_body=True
        )
    }
    there = {
        n.id
        for n in await store.memory_notes_by_ids(
            owner, [i for r in answered for i in r["note_ids"]]
        )
    }
    past = set(past_events(list(notes.values()), today))
    stale = [r["id"] for r in answered if not any(i in there for i in r["note_ids"])]
    out: list[dict[str, Any]] = []
    for r in waiting:
        members = [notes.get(i) for i in r["note_ids"]]
        held = [m for m in members if m is not None]
        current = len(held) == len(members) and all(m.status == "active" for m in held)
        if r["kind"] == "duplicate":
            fits = current and len(held) == 2 and _same(held[0], held[1])
        else:
            fits = current and held[0].id in past
        if not fits:
            stale.append(r["id"])
            continue
        out.append({"id": r["id"], "kind": r["kind"], "notes": held})
    if stale:
        await store.drop_tidy(owner, stale)
    return out
