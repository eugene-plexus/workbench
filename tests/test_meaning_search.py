"""Words and meaning together (MR5, design 4.4, MRC13): reciprocal rank fusion at k = 60 with
equal weights, a passage only one way finds still appearing, a way that cannot run leaving the
rest to go on and saying so, and the one evidence rule.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import pytest

from eugene_plexus_workbench.knowledge import search
from eugene_plexus_workbench.knowledge.index import Hit, Index
from eugene_plexus_workbench.knowledge.search import (
    Query,
    Retriever,
    SignalUnavailable,
    WordSignal,
    mode_of,
    plan_query,
)

NO_INDEX = cast(Index, None)


def hit(chunk_id: int, text: str = "a passage", score: float = 0.0, doc: str | None = None) -> Hit:
    """A passage of a document of its own (so passages that follow each other are not merged)."""
    doc = doc or f"doc{chunk_id:05d}"
    return Hit(
        chunk_id=chunk_id,
        doc_id=doc,
        ord=chunk_id,
        text=f"{text} {chunk_id}",
        heading_path=None,
        page_start=None,
        page_end=None,
        line_start=None,
        line_end=None,
        char_start=None,
        char_end=None,
        tokens_est=10,
        name=f"{doc}.md",
        scope="library",
        kind="markdown",
        score=score,
    )


class Fixed:
    """A way of searching that finds what a test says, in that order."""

    def __init__(
        self,
        name: str,
        hits: Sequence[Hit] = (),
        *,
        similarity: bool = False,
        floor: float | None = None,
        unavailable: SignalUnavailable | None = None,
    ) -> None:
        self.name = name
        self.similarity = similarity
        self._floor = floor
        self._hits = list(hits)
        self._unavailable = unavailable
        self.asked = 0

    async def search(
        self, index: Index, query: Query, scopes: Sequence[str], limit: int
    ) -> list[Hit]:
        self.asked += 1
        if self._unavailable is not None:
            raise self._unavailable
        return self._hits

    async def floor(self, index: Index) -> float | None:
        return self._floor


async def run(*signals: Any, ways: Sequence[str] | None = None, k: int = 5) -> search.Retrieval:
    return await Retriever(list(signals)).retrieve(
        NO_INDEX,
        plan_query("a question about things"),
        ["library"],
        question="a question about things",
        budget=None,
        overhead=0,
        k=k,
        ways=ways,
    )


# --------------------------------------------------------------------------- #
# fusing
# --------------------------------------------------------------------------- #


def test_a_passage_only_the_words_find_still_appears_and_so_does_one_only_meaning_finds() -> None:
    words_only, both, meaning_only = hit(1), hit(2), hit(3)
    fused = search.fuse({"words": [words_only, both], "meaning": [meaning_only, both]})
    got = {c.hit.chunk_id: c for c in fused}
    assert set(got) == {1, 2, 3}, "nothing one way found is lost because the other did not"
    assert (
        got[1].via == ("words",)
        and got[3].via == ("meaning",)
        and got[2].via == ("words", "meaning")
    )
    assert fused[0].hit.chunk_id == 2, "found by both is first"


def test_each_way_adds_one_over_sixty_plus_the_rank_with_equal_weights() -> None:
    assert search.RRF_K == 60
    a, b = hit(1), hit(2)
    fused = search.fuse({"words": [a, b], "meaning": [b, a]})
    # Both are found by both, at ranks 1 and 2: 1/61 + 1/62 each. A tie, in the order given.
    assert [c.hit.chunk_id for c in fused] == [1, 2]
    top = search.fuse({"words": [a], "meaning": [a, b]})
    assert [c.hit.chunk_id for c in top] == [1, 2]
    assert top[0].ranks == (("words", 1), ("meaning", 1)) and top[1].ranks == (("meaning", 2),)


def test_a_passage_found_by_both_at_modest_ranks_beats_one_found_by_one_at_the_top() -> None:
    """1/61 at the top of one list is less than 1/90 + 1/90 at rank 30 of both."""
    top_of_words, modest = hit(1), hit(2)
    words = [top_of_words, *[hit(100 + n) for n in range(28)], modest]  # modest is 30th
    meaning = [*[hit(200 + n) for n in range(29)], modest]  # and 30th here too
    order = [c.hit.chunk_id for c in search.fuse({"words": words, "meaning": meaning})]
    assert order.index(2) < order.index(1), "both agreeing outweighs one being sure"
    assert order[0] == 2


def test_one_way_is_its_own_order_and_meaning_keeps_how_near_each_was() -> None:
    near, far = hit(1, score=0.8), hit(2, score=0.3)
    one = search.fuse({"meaning": [near, far]}, similar={"meaning"})
    assert [(c.hit.chunk_id, c.similarity, c.via) for c in one] == [
        (1, 0.8, ("meaning",)),
        (2, 0.3, ("meaning",)),
    ]
    words = search.fuse({"words": [hit(5, score=-3.2)]})
    assert words[0].similarity is None, "a BM25 score is not a similarity and is never shown as one"
    mixed = search.fuse(
        {"words": [hit(1, score=-3.2)], "meaning": [hit(1, score=0.7)]}, similar={"meaning"}
    )
    assert mixed[0].similarity == 0.7 and mixed[0].via == ("words", "meaning")


# --------------------------------------------------------------------------- #
# the whole search
# --------------------------------------------------------------------------- #


async def test_words_and_meaning_are_searched_together_and_each_passage_says_how_it_was_found() -> (
    None
):
    got = await run(
        Fixed("words", [hit(1), hit(2)]),
        Fixed("meaning", [hit(3, score=0.6), hit(2, score=0.5)], similarity=True),
    )
    assert got.mode == "meaning and words" and got.ways == ("words", "meaning")
    vias = {p.chunk_ids[0]: p.via for p in got.passages}
    assert vias == {1: "words", 2: "both", 3: "meaning"}
    both = next(p for p in got.passages if p.chunk_ids == (2,))
    assert dict(both.ranks) == {"words": 2, "meaning": 2} and both.similarity == 0.5
    assert got.found == 3 and got.reports["meaning"].ran and got.reports["words"].hits == 2


async def test_a_way_that_cannot_run_is_left_out_and_said_and_the_other_goes_on() -> None:
    stopped = SignalUnavailable(
        "not_running", "Searching by words only: the search model m is not running."
    )
    got = await run(
        Fixed("words", [hit(1)]), Fixed("meaning", unavailable=stopped, similarity=True)
    )
    assert [p.chunk_ids for p in got.passages] == [(1,)]
    assert got.mode == "words" and got.ways == ("words",)
    said = got.reports["meaning"]
    assert (
        not said.ran
        and said.code == "not_running"
        and said.message == stopped.message
        and not said.quiet
    )


async def test_nothing_matched_by_words_is_still_said_to_be_by_words_only_when_meaning_could_not_run() -> (
    None
):
    stopped = SignalUnavailable("not_running", "the search model is not running")
    got = await run(Fixed("words", []), Fixed("meaning", unavailable=stopped, similarity=True))
    assert got.passages == [] and got.found == 0
    assert got.mode == "words" and not got.reports["meaning"].ran, (
        "not a plain nothing: it says why"
    )


async def test_a_way_that_was_never_in_play_is_quiet() -> None:
    never = SignalUnavailable("none_set_up", "no search model", quiet=True)
    got = await run(Fixed("words", [hit(1)]), Fixed("meaning", unavailable=never, similarity=True))
    assert got.reports["meaning"].quiet and got.mode == "words"


async def test_a_way_that_breaks_any_other_way_raises_and_is_never_nothing_matched() -> None:
    class Broken(Fixed):
        async def search(self, *_: Any) -> list[Hit]:
            raise RuntimeError("fell over")

    with pytest.raises(RuntimeError, match="fell over"):
        await run(Fixed("words", [hit(1)]), Broken("meaning"))


async def test_when_no_way_can_run_nothing_ran_and_the_mode_says_none() -> None:
    gone = SignalUnavailable("not_running", "stopped")
    got = await run(Fixed("meaning", unavailable=gone, similarity=True), ways=["meaning"])
    assert got.mode == "none" and got.ways == () and got.passages == []


async def test_a_run_that_names_its_ways_asks_only_those() -> None:
    words, meaning = Fixed("words", [hit(1)]), Fixed("meaning", [hit(2)], similarity=True)
    got = await run(words, meaning, ways=["meaning"])
    assert (words.asked, meaning.asked) == (0, 1)
    assert [p.chunk_ids for p in got.passages] == [(2,)] and got.mode == "meaning"
    got = await run(words, meaning, ways=["words"])
    assert got.mode == "words" and [p.chunk_ids for p in got.passages] == [(1,)]


def test_how_a_search_is_told_comes_from_the_ways_that_ran() -> None:
    assert mode_of(["words", "meaning"]) == "meaning and words"
    assert (
        mode_of(["meaning"]) == "meaning"
        and mode_of(["words"]) == "words"
        and mode_of([]) == "none"
    )


def test_a_retriever_with_meaning_configured_is_told_as_both_before_any_search() -> None:
    assert Retriever([WordSignal(), Fixed("meaning")]).mode == "meaning and words"
    assert Retriever().mode == "words"


# --------------------------------------------------------------------------- #
# the one evidence rule (design 4.4, item 7)
# --------------------------------------------------------------------------- #


async def test_when_the_words_found_nothing_and_the_best_meaning_is_under_its_floor_nothing_matched() -> (
    None
):
    meaning = Fixed("meaning", [hit(1, score=0.2), hit(2, score=0.1)], similarity=True, floor=0.25)
    got = await run(Fixed("words", []), meaning)
    assert got.passages == [] and got.found == 0
    said = got.reports["meaning"]
    assert said.ran and said.below_floor and said.best == 0.2 and said.floor == 0.25


async def test_a_best_meaning_at_the_floor_is_evidence_and_is_kept() -> None:
    meaning = Fixed("meaning", [hit(1, score=0.25)], similarity=True, floor=0.25)
    got = await run(Fixed("words", []), meaning)
    assert [p.chunk_ids for p in got.passages] == [(1,)] and not got.reports["meaning"].below_floor


async def test_when_any_word_matched_every_passage_meaning_found_counts_whatever_it_scored() -> (
    None
):
    """No score threshold: the floor is evidence about the whole result, never a filter."""
    meaning = Fixed(
        "meaning", [hit(2, score=0.05), hit(3, score=0.04)], similarity=True, floor=0.25
    )
    got = await run(Fixed("words", [hit(1)]), meaning)
    assert {p.chunk_ids[0] for p in got.passages} == {1, 2, 3}
    assert not got.reports["meaning"].below_floor and got.reports["meaning"].best == 0.05


async def test_a_model_with_no_floor_has_no_such_rule() -> None:
    meaning = Fixed("meaning", [hit(1, score=0.01)], similarity=True, floor=None)
    got = await run(Fixed("words", []), meaning)
    assert [p.chunk_ids for p in got.passages] == [(1,)] and got.reports["meaning"].floor is None


async def test_a_run_that_names_its_ways_shows_what_each_found_whatever_it_scored() -> None:
    meaning = Fixed("meaning", [hit(1, score=0.01)], similarity=True, floor=0.25)
    got = await run(Fixed("words", []), meaning, ways=["meaning"])
    assert [p.chunk_ids for p in got.passages] == [(1,)] and not got.reports["meaning"].below_floor


# --------------------------------------------------------------------------- #
# the question
# --------------------------------------------------------------------------- #


def test_the_question_a_search_by_meaning_reads_is_the_latest_message_then_a_little_of_the_one_before() -> (
    None
):
    query = plan_query("What does the second GPU cost?", "x" * 500 + " earlier")
    assert query.asked == "What does the second GPU cost?\n\n" + "x" * 300
    assert plan_query("Just this").asked == "Just this"
    assert plan_query("a" * 2000).asked == "a" * 1000, (
        "the latest message is cut at a thousand characters"
    )


def test_a_question_with_no_words_to_search_for_is_still_empty_whatever_it_asks_by_meaning() -> (
    None
):
    query = plan_query("why?")
    assert query.empty and query.asked == "why?"
