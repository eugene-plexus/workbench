"""A stand-in search model for the tests (MR5): meaning that a test can reason about.

A real embedding model is a file of 600 MB; the tests need to say "this question has none of
that passage's words and still means the same", and to know it will, so the vectors here are
made from **concepts**: each group of words below is one dimension, and a text's vector is
how much of each concept it mentions (plus a small share for every other word, so that a rare
word is not nothing). "Ships" and "boats" are the same dimension, so a question about the
boats finds a passage about the vessels, and a question that has none of the concepts finds
nothing in particular, the way a real model's nearest passages are when nothing is near.

Everything is deterministic. The vectors are of length one, as llama-server's are.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable

#: Each group is one dimension of the concept space.
GROUPS: dict[str, str] = {
    "ships": "ship ships vessel vessels boat boats freighter freighters schooner trawler ferry",
    "records": "record records registry register ledger log logbook archive archives paperwork",
    "money": "money cost costs price prices pay paid payment budget fee fees cash expense",
    "weather": "weather storm storms rain wind winds forecast gale fog",
    "people": "person people staff crew worker workers employee employees sailor sailors",
    "food": "food meal meals kitchen cook cooking recipe recipes pantry lunch dinner",
    "repair": "repair repairs fix fixing broken mend maintenance engine engines",
    "danger": "danger dangerous hazard risk risks safety safe unsafe warning warnings",
    "light": "light lights lamp lamps lighthouse beacon lantern lamplight",
    "water": "water sea ocean tide tides harbour harbor port dock docks",
    "time": "time hour hours day days week weeks schedule timetable calendar",
    "law": "law laws rule rules permit permits licence license regulation regulations",
}
CONCEPTS = len(GROUPS)
#: The words of a concept, each to its dimension.
_DIMENSION = {w: i for i, (_, words) in enumerate(GROUPS.items()) for w in words.split()}
HASHED = 12
DIM = CONCEPTS + HASHED
_WORD = re.compile(r"[a-z0-9]+")
#: Words that say nothing: they are in every text, and are no part of its meaning.
_STOP = frozenset(
    {
        "the", "a", "an", "of", "is", "are", "at", "in", "on", "by", "for", "to", "and", "or",
        "what", "who", "how", "why", "when", "where", "which", "that", "this", "with", "from",
        "it", "its", "be", "as", "was", "were", "do", "does", "can", "could", "will", "would",
    }
)  # fmt: skip
#: How much of a vector a word that is no concept's gives: a rare word is not nothing.
OTHER_WEIGHT = 0.18


def vector(text: str, dim: int = DIM) -> list[float]:
    """The unit vector for this text. A text of no words is one fixed vector, never zeros."""
    numbers = [0.0] * dim
    for word in _WORD.findall(text.lower()):
        at = _DIMENSION.get(word)
        if at is not None and at < dim:
            numbers[at] += 1.0
        elif dim > CONCEPTS and word not in _STOP:
            digest = hashlib.sha256(word.encode()).digest()
            numbers[CONCEPTS + digest[0] % (dim - CONCEPTS)] += OTHER_WEIGHT
    norm = math.sqrt(sum(n * n for n in numbers))
    if norm == 0:
        numbers[-1] = 1.0
        return numbers
    return [n / norm for n in numbers]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


Embedder = Callable[[str, int], list[float]]
