"""Which of a person's notes are about what a chat is asking (MR8, design 4.8 "read side").

Models do not ignore memory that has nothing to do with the question (PersistBench: a median
failure of 53% across domains), so a person's default, `use_mode = relevant`, sends the notes
that are always used (instructions and pinned ones) and then only notes about what the chat
began with: the person's message at the moment the chat's notes are chosen.

**Found two ways, as documents are** (MR5): by words (the notes' FTS5 index, BM25) and by
meaning, through the search model the person's documents already use (`knowledge/meaning`'s
choice: a local one that is running, unless the owner allowed an outside one). **No search
model is started for this** (MRC28): when none is running, picking by words alone would leave
out "Is vegetarian." from "What should I cook tonight?", so every note that fits is used
instead, and the page and the chat's drawer say why. The two rankings are fused as documents'
are (RRF, k = 60); by meaning, a note counts only above the model's measured floor, else only
the best `MEANING_TOP`.

A note's vector is kept in this process, by model and by the words' hash, so a chat embeds only
the question and notes that are new or edited since. Meaning gets `MEANING_SECONDS`; past
that, or on any failure, the notes are chosen by words and meaning is named as missing. Logs
carry counts and causes, never a note's words.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from . import memory
from .hub import HubError
from .knowledge import embedders
from .store import MemoryNote, Store

log = logging.getLogger(__name__)

RRF_K = 60
WORDS_LIMIT = 50
MEANING_TOP = 8
MEANING_SECONDS = 3.0
#: Notes embedded per request.
BATCH = 32

#: How the relevant notes were found.
WORDS_AND_MEANING = "meaning and words"
ALL_THAT_FIT = "all"


@dataclass(frozen=True)
class Relevance:
    #: Note ids, most relevant first; None means "every note that fits" (see `how`).
    ids: list[str] | None
    #: `meaning and words`, or `all`.
    how: str
    #: Why every note that fits was used instead, in words a person reads; None when it was
    #: what they asked for or relevance ran.
    why: str | None = None


NO_SEARCH_MODEL = (
    "Using every note that fits: choosing only the relevant ones needs a search model on your "
    "machines, and none is running. Start one in Eugene's console under Backends."
)


class NoteVectors:
    """Each note's vector by `(model, sha256 of its words)`, for this process."""

    def __init__(self, cap: int = 20_000) -> None:
        self._kept: dict[tuple[str, str], np.ndarray] = {}
        self._cap = cap

    @staticmethod
    def key(model: str, text: str) -> tuple[str, str]:
        return model, hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, model: str, text: str) -> np.ndarray | None:
        return self._kept.get(self.key(model, text))

    def put(self, model: str, text: str, vector: np.ndarray) -> None:
        if len(self._kept) >= self._cap:
            self._kept.clear()
        self._kept[self.key(model, text)] = vector


def _unit(vector: Sequence[float]) -> np.ndarray:
    row = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(row))
    return row / norm if norm > 0 else row


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


def by_meaning(
    question: np.ndarray, notes: Sequence[tuple[str, np.ndarray]], floor: float | None
) -> list[str]:
    """Note ids by similarity to the question: those above `floor` where the model has one
    measured, else the best `MEANING_TOP`."""
    scored = sorted(((float(np.dot(question, v)), i) for i, v in notes), key=lambda s: -s[0])
    if floor is not None:
        return [i for s, i in scored if s > floor]
    return [i for _, i in scored[:MEANING_TOP]]


async def by_words(store: Store, owner: str, question: str) -> list[str]:
    expression = memory.match_expression(memory.search_words(question))
    if expression is None:
        return []
    return [n.id for n in await store.search_memory(owner, expression, limit=WORDS_LIMIT)]


async def available(meaning: object | None) -> tuple[bool, str | None]:
    """Whether relevance can run now (a local search model is running), and if not, why, for
    the Memory page: it says "used when relevant" only when that is what happens."""
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


async def relevant(
    store: Store,
    meaning: object | None,
    vectors: NoteVectors,
    owner: str,
    notes: Sequence[MemoryNote],
    question: str,
) -> Relevance:
    """The notes about `question`, or why every note that fits is used instead. Never raises."""
    candidates = [n for n in notes if n.status == "active" and not memory.is_always(n)]
    if not candidates:
        return Relevance([], WORDS_AND_MEANING)
    if meaning is None:
        return Relevance(None, ALL_THAT_FIT, NO_SEARCH_MODEL)
    try:
        resolved = await meaning.resolve()  # type: ignore[attr-defined]
    except Exception as exc:
        log.warning(
            "memory relevance could not find a search model: %s: %s", type(exc).__name__, exc
        )
        return Relevance(None, ALL_THAT_FIT, NO_SEARCH_MODEL)
    model = resolved.model
    if model is None:
        # The documents' own words for why (`resolved.message`) are about documents; a
        # search model that is not running, or runs where notes may not go, is the cause.
        log.info(
            "memory relevance has no search model (%s); every note that fits is used", resolved.code
        )
        return Relevance(None, ALL_THAT_FIT, NO_SEARCH_MODEL)
    if not model.local:
        # The owner may let documents' text go to an outside search model; notes, a profile
        # of the person, do not go with it.
        return Relevance(None, ALL_THAT_FIT, NO_SEARCH_MODEL)
    try:
        words = await by_words(store, owner, question)
        found = await asyncio.wait_for(
            _meaning(meaning.gateway, vectors, model.id, candidates, question),  # type: ignore[attr-defined]
            MEANING_SECONDS,
        )
    except (TimeoutError, HubError, ValueError) as exc:
        cause = (
            "it took too long" if isinstance(exc, TimeoutError) else str(exc) or type(exc).__name__
        )
        log.warning("memory relevance by meaning failed (%s); every note that fits is used", cause)
        return Relevance(
            None,
            ALL_THAT_FIT,
            f"Using every note that fits: the search model {model.id} did not answer ({cause}).",
        )
    return Relevance(fuse(words, found), WORDS_AND_MEANING)


async def _meaning(
    gateway: object, vectors: NoteVectors, model: str, notes: Sequence[MemoryNote], question: str
) -> list[str]:
    identity = embedders.identity_for(model, 1)
    missing = [n for n in notes if vectors.get(model, n.text) is None]
    for start in range(0, len(missing), BATCH):
        batch = missing[start : start + BATCH]
        answer = await gateway.embeddings(  # type: ignore[attr-defined]
            model,
            [embedders.passage_input(identity, None, n.text) for n in batch],
            seconds=MEANING_SECONDS,
        )
        if len(answer.vectors) != len(batch):
            raise ValueError("the search model answered a different number of vectors")
        for note, vector in zip(batch, answer.vectors, strict=True):
            vectors.put(model, note.text, _unit(vector))
    asked = await gateway.embeddings(  # type: ignore[attr-defined]
        model, [embedders.question_input(identity, question)], seconds=MEANING_SECONDS
    )
    q = _unit(asked.vectors[0])
    kept = [
        (n.id, v)
        for n in notes
        if (v := vectors.get(model, n.text)) is not None and v.shape == q.shape
    ]
    return by_meaning(q, kept, embedders.floor_for(model))
