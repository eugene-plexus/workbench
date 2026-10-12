"""A stand-in result sorter for the tests (MR6b): a score a test can reason about.

A real sorter is a model of 600 MB that reads a question and a passage together. The tests need to
say "this passage answers that question, and that one does not", and to know the order that comes
back, so the score here is the closeness of the two in the concept space of `fake_embedding`: a
passage that mentions what the question asks is near it, whatever its other words. Everything is
deterministic, 0 to 1, as the gateway's scores are.

`rerank_prefer` of the fake gateway overrides it for a test that must put one passage first: a
passage with that text in it scores what the test says.
"""

from __future__ import annotations

from collections.abc import Mapping

from . import fake_embedding


def score(query: str, document: str, prefer: Mapping[str, float] | None = None) -> float:
    """How well `document` answers `query`: 0 to 1. A passage that holds one of the texts in
    `prefer` is scored what that says (the first that matches); any other, half its closeness
    when there is something preferred (so what a test prefers, at 0.9 or more, is always first),
    and its closeness when there is not."""
    for text, wanted in (prefer or {}).items():
        if text in document:
            return wanted
    a, b = fake_embedding.vector(query), fake_embedding.vector(document)
    closeness = max(0.0, min(1.0, fake_embedding.cosine(a, b)))
    return closeness / 2 if prefer else closeness
