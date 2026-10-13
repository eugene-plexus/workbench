"""Sorting what was found (MR6b, design `workbench-sorting.md` calls 1, 2 and 5): which passages a
sorter is given, as what, what is done with the order that comes back, and a sorter that fails
never failing a search.

`Retriever` is driven with ways of searching that find what a test says and a sorter that does
what a test says, so each step is exact.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any, cast

import pytest

from eugene_plexus_workbench.knowledge import search
from eugene_plexus_workbench.knowledge.index import Hit, Index
from eugene_plexus_workbench.knowledge.search import (
    SORT_FROM_EACH,
    Query,
    Retriever,
    Sheet,
    Sorted,
    SortReport,
    sheet_of,
    sort_pool,
    sorted_mode,
)

NO_INDEX = cast(Index, None)


def hit(
    chunk_id: int,
    text: str = "a passage",
    *,
    heading: str | None = None,
    doc: str | None = None,
    tokens: int = 10,
    score: float = 0.0,
    ord_: int | None = None,
) -> Hit:
    """A passage of a document of its own, and never the one after another's (so passages are not
    merged unless a test says they follow each other: `ord_`)."""
    doc = doc or f"doc{chunk_id:05d}"
    return Hit(
        chunk_id=chunk_id,
        doc_id=doc,
        ord=chunk_id * 10 if ord_ is None else ord_,
        text=f"{text} {chunk_id}",
        heading_path=heading,
        page_start=None,
        page_end=None,
        line_start=None,
        line_end=None,
        char_start=None,
        char_end=None,
        tokens_est=tokens,
        name=f"{doc}.md",
        scope="library",
        kind="markdown",
        score=score,
    )


class Fixed:
    """A way of searching that finds what a test says, in that order."""

    def __init__(self, name: str, hits: Sequence[Hit], *, similarity: bool = False) -> None:
        self.name = name
        self.similarity = similarity
        self._hits = list(hits)

    async def search(
        self, index: Index, query: Query, scopes: Sequence[str], limit: int
    ) -> list[Hit]:
        return self._hits


class Sorter:
    """A result sorter that does what a test says, and keeps what it was asked."""

    def __init__(
        self,
        order: Callable[[Sequence[Sheet]], Sequence[int]] | None = None,
        *,
        report: SortReport | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self._order = order or (lambda sheets: list(reversed(range(len(sheets)))))
        self._report = report
        self._raises = raises
        self.asked: list[tuple[str, list[Sheet]]] = []

    async def sort(self, query: str, sheets: Sequence[Sheet]) -> Sorted:
        self.asked.append((query, list(sheets)))
        if self._raises is not None:
            raise self._raises
        if self._report is not None and not self._report.sorted:
            return Sorted(None, self._report)
        report = self._report or SortReport(
            sorted=True, model="the-sorter", passages=len(sheets), setting="auto"
        )
        return Sorted(tuple(self._order(sheets)), report)


async def run(
    *signals: Any,
    sorter: Any = None,
    question: str = "a question about things",
    k: int = 5,
    ways: Sequence[str] | None = None,
    per_document: int = 3,
    budget: int | None = None,
    overhead: int = 0,
) -> search.Retrieval:
    return await Retriever(list(signals), sorter=sorter).retrieve(
        NO_INDEX,
        search.plan_query(question),
        ["library"],
        question=question,
        budget=budget,
        overhead=overhead,
        k=k,
        ways=ways,
        per_document=per_document,
    )


def chunks(got: search.Retrieval) -> list[int]:
    """The passages to send, as the first passage each is made of."""
    return [p.chunk_ids[0] for p in got.passages]


def sent(sorter: Sorter) -> list[str]:
    """The text of what the sorter was given, in the order it was given."""
    return [s.text for s in sorter.asked[0][1]]


# --------------------------------------------------------------------------- #
# which passages (call 2)
# --------------------------------------------------------------------------- #


def test_the_best_ten_of_each_way_are_given_each_passage_once_and_up_to_twenty() -> None:
    words = [hit(n) for n in range(1, 31)]  # 1..30, best first
    meaning = [hit(n) for n in (5, 3, *range(101, 119))]  # 5 and 3 are found by both
    candidates = search.fuse({"words": words, "meaning": meaning})
    pool = sort_pool(candidates, {"words": words, "meaning": meaning})
    ids = [c.hit.chunk_id for c in pool]
    assert SORT_FROM_EACH == 10
    assert set(ids) == set(range(1, 11)) | {5, 3, *range(101, 109)}, (
        "words' 1..10 and meaning's first ten (5, 3, 101..108)"
    )
    assert len(ids) == len(set(ids)) == 18, "each passage once: 5 and 3 are in both lists"
    assert 11 not in ids and 109 not in ids, "the eleventh of a way is not given"


def test_the_most_a_sorter_is_given_is_twenty_when_the_ways_find_different_passages() -> None:
    words = [hit(n) for n in range(1, 21)]
    meaning = [hit(n) for n in range(101, 121)]
    pool = sort_pool(
        search.fuse({"words": words, "meaning": meaning}), {"words": words, "meaning": meaning}
    )
    assert len(pool) == 20


def test_with_one_way_running_the_sorter_is_given_its_best_ten() -> None:
    words = [hit(n) for n in range(1, 31)]
    pool = sort_pool(search.fuse({"words": words}), {"words": words})
    assert [c.hit.chunk_id for c in pool] == list(range(1, 11))


def test_a_way_that_found_fewer_than_ten_gives_what_it_found() -> None:
    words, meaning = [hit(1), hit(2)], [hit(3)]
    pool = sort_pool(
        search.fuse({"words": words, "meaning": meaning}), {"words": words, "meaning": meaning}
    )
    assert sorted(c.hit.chunk_id for c in pool) == [1, 2, 3]


def test_a_way_that_found_nothing_gives_nothing_and_the_other_gives_its_best_ten() -> None:
    meaning = [hit(n) for n in range(1, 15)]
    found = {"words": [], "meaning": meaning}
    pool = sort_pool(search.fuse(found), found)
    assert [c.hit.chunk_id for c in pool] == list(range(1, 11))


def test_the_passages_are_given_in_the_order_the_ways_were_fused_in() -> None:
    """A tie in the sorter's scores is the fused order's, so the order given is that order."""
    words = [hit(1), hit(2), hit(3)]
    meaning = [hit(3), hit(1), hit(9)]
    found = {"words": words, "meaning": meaning}
    fused = search.fuse(found)
    assert [c.hit.chunk_id for c in sort_pool(fused, found)] == [c.hit.chunk_id for c in fused]


async def test_the_sorter_is_given_the_union_through_the_retriever_and_the_question_asked() -> None:
    words = [hit(n) for n in range(1, 31)]
    meaning = [hit(n) for n in (7, 8, *range(101, 119))]
    sorter = Sorter()
    await run(
        Fixed("words", words),
        Fixed("meaning", meaning, similarity=True),
        sorter=sorter,
        question="the vessel registry",
    )
    [(query, sheets)] = sorter.asked
    assert query == "the vessel registry", "what the person asked, as the search was given it"
    wanted = {*range(1, 11), 7, 8, *range(101, 109)}
    assert {s.text for s in sheets} == {f"a passage {n}" for n in wanted}
    assert len(sheets) == len(wanted) == 18


async def test_a_follow_up_is_sorted_by_what_the_meaning_search_was_asked_too() -> None:
    sorter = Sorter()
    query = search.plan_query("and the second one?", "the harbour office's ledger")
    await Retriever([Fixed("words", [hit(1), hit(2)])], sorter=sorter).retrieve(
        NO_INDEX,
        query,
        ["library"],
        question="and the second one?",
        budget=None,
        overhead=0,
    )
    assert sorter.asked[0][0] == "and the second one?\n\nthe harbour office's ledger"


# --------------------------------------------------------------------------- #
# as what (call 1)
# --------------------------------------------------------------------------- #


def test_a_passage_is_given_as_its_heading_path_a_new_line_and_its_words() -> None:
    got = sheet_of(hit(4, "The fees are paid at the quayside.", heading="Harbour office > Fees"))
    assert got == Sheet("Harbour office > Fees\nThe fees are paid at the quayside. 4", 10)


def test_a_passage_with_no_heading_is_given_as_its_words_alone() -> None:
    assert sheet_of(hit(4, "No headings here.", heading=None)).text == "No headings here. 4"


def test_what_a_passage_is_worth_is_its_own_count() -> None:
    assert sheet_of(hit(1, tokens=317)).tokens == 317


def test_a_passage_is_given_as_a_search_model_is_given_it_without_that_models_prefix() -> None:
    """It is `embedders.passage_input`, with no model's prefix: a sorter is not a search model."""
    from eugene_plexus_workbench.knowledge import embedders

    one = hit(2, "Words.", heading="A > B")
    assert sheet_of(one).text == embedders.passage_input(embedders.PLAIN, "A > B", "Words. 2")
    nomic = embedders.identity_for("nomic-embed-text-v1.5", 3)
    assert embedders.passage_input(nomic, "A > B", "Words. 2").startswith("search_document: ")
    assert not sheet_of(one).text.startswith("search_document: ")


async def test_the_headings_go_to_the_sorter_through_the_retriever() -> None:
    sorter = Sorter()
    await run(
        Fixed(
            "words",
            [hit(1, "Ships.", heading="Harbour office > Arrivals"), hit(2, "Fees.", heading=None)],
        ),
        sorter=sorter,
    )
    assert sent(sorter) == ["Harbour office > Arrivals\nShips. 1", "Fees. 2"]


# --------------------------------------------------------------------------- #
# the order that comes back, and what follows (call 2)
# --------------------------------------------------------------------------- #


async def test_the_sorters_order_is_the_order_the_passages_are_sent_in() -> None:
    words = [hit(n) for n in range(1, 7)]
    got = await run(Fixed("words", words), sorter=Sorter(lambda sheets: [5, 4, 3, 2, 1, 0]), k=6)
    assert chunks(got) == [6, 5, 4, 3, 2, 1]
    assert [p.rank for p in got.passages] == [1, 2, 3, 4, 5, 6], "cited by the new order"


async def test_the_sorted_come_first_and_the_rest_follow_in_the_fused_order() -> None:
    """Three of one file's passages may be chosen, so the sorted ones that are passed over leave
    room for the passages that were not sorted: the best ten were, the rest follow as they were."""
    words = [hit(n, doc="big") for n in range(1, 9)] + [hit(n) for n in range(9, 16)]
    got = await run(
        Fixed("words", words),
        sorter=Sorter(lambda sheets: list(range(len(sheets)))),  # the sorter keeps the order
        k=10,
        per_document=3,
    )
    assert chunks(got) == [1, 2, 3, 9, 10, 11, 12, 13, 14, 15]
    sorted_ones = [dict(p.ranks).get("sorter") for p in got.passages]
    assert sorted_ones == [1, 2, 3, 9, 10, None, None, None, None, None], (
        "the sorter placed the best ten, and the others have no place by it"
    )
    reversed_ = await run(
        Fixed("words", words),
        sorter=Sorter(lambda sheets: list(reversed(range(len(sheets))))),
        k=10,
        per_document=3,
    )
    assert chunks(reversed_) == [10, 9, 8, 7, 6, 11, 12, 13, 14, 15], (
        "the sorter decides which three of the file are chosen; the rest follow as they were"
    )


async def test_each_passage_says_where_the_sorter_put_it_beside_where_each_way_did() -> None:
    words = [hit(1), hit(2), hit(3)]
    meaning = [hit(3, "x"), hit(4)]
    got = await run(
        Fixed("words", words),
        Fixed("meaning", meaning, similarity=True),
        sorter=Sorter(lambda sheets: list(reversed(range(len(sheets))))),
    )
    places = {p.chunk_ids[0]: dict(p.ranks) for p in got.passages}
    # Fused: 3 (found by both), 1, 2, 4 (2 and 4 tie, the first found first). The sorter reversed
    # those four.
    assert places[3] == {"words": 3, "meaning": 1, "sorter": 4}
    assert places[1] == {"words": 1, "sorter": 3}
    assert places[2] == {"words": 2, "sorter": 2}
    assert places[4] == {"meaning": 2, "sorter": 1}
    assert chunks(got) == [4, 2, 1, 3]


async def test_the_ways_that_found_a_passage_are_not_the_sorters_to_change() -> None:
    got = await run(
        Fixed("words", [hit(1), hit(2)]),
        Fixed("meaning", [hit(2), hit(3)], similarity=True),
        sorter=Sorter(),
    )
    assert {p.chunk_ids[0]: p.via for p in got.passages} == {1: "words", 2: "both", 3: "meaning"}
    both = next(p for p in got.passages if p.chunk_ids == (2,))
    assert "sorter" in dict(both.ranks) and {"words", "meaning"} <= set(dict(both.ranks))


async def test_a_passage_the_sorter_was_not_given_has_no_place_by_it() -> None:
    words = [hit(n, doc="big") for n in range(1, 6)] + [hit(n) for n in range(6, 13)]
    got = await run(
        Fixed("words", words),
        sorter=Sorter(lambda sheets: list(range(len(sheets)))),
        k=10,
        per_document=3,
    )
    places = {p.chunk_ids[0]: dict(p.ranks) for p in got.passages}
    assert 11 in places and 12 in places, "chosen, though beyond the best ten"
    assert "sorter" not in places[11] and "sorter" not in places[12]
    assert all("sorter" in places[n] for n in (1, 2, 3, 6, 7, 8, 9, 10))


async def test_the_choice_after_the_sort_is_as_before_at_most_three_from_one_file_unless_named() -> (
    None
):
    words = [hit(n, doc="big") for n in range(1, 7)] + [hit(10 + n) for n in range(1, 5)]
    got = await run(
        Fixed("words", words),
        sorter=Sorter(lambda sheets: list(range(len(sheets)))),
        per_document=3,
    )
    big = [p for p in got.passages if p.doc_id == "big"]
    assert len(big) <= 3 and len(got.passages) == 5
    named = await run(
        Fixed("words", words),
        sorter=Sorter(lambda sheets: list(range(len(sheets)))),
        question="what does big say?",
        per_document=3,
    )
    assert len([p for p in named.passages if p.doc_id == "big"]) >= 4, "a file the question names"


async def test_the_budget_still_decides_how_many_of_the_sorted_fit() -> None:
    words = [hit(n) for n in range(1, 7)]  # "a passage 6": 4 tokens, as a model is shown it
    got = await run(
        Fixed("words", words),
        sorter=Sorter(lambda sheets: [5, 4, 3, 2, 1, 0]),
        k=6,
        budget=8,
        overhead=0,
    )
    assert chunks(got) == [6, 5] and got.did_not_fit == 4, "the best two of the sorted order fit"


async def test_passages_that_follow_each_other_in_a_file_are_still_joined_after_a_sort() -> None:
    first = hit(1, "First half.", heading="Notes", doc="one", ord_=1)
    second = hit(2, "Second half.", heading="Notes", doc="one", ord_=2)
    other = hit(3, "Elsewhere.", doc="two")
    got = await run(Fixed("words", [first, second, other]), sorter=Sorter(lambda sheets: [2, 0, 1]))
    assert [p.chunk_ids for p in got.passages] == [(3,), (1, 2)], "the pair is one passage"


# --------------------------------------------------------------------------- #
# what it says of itself (call 6)
# --------------------------------------------------------------------------- #


async def test_when_the_passages_were_sorted_the_mode_says_by_which_sorter() -> None:
    got = await run(
        Fixed("words", [hit(1), hit(2)]),
        Fixed("meaning", [hit(3)], similarity=True),
        sorter=Sorter(report=SortReport(sorted=True, model="Qwen3-Reranker-0.6B", passages=3)),
    )
    assert got.mode == "meaning and words, sorted by Qwen3-Reranker-0.6B"
    assert got.ways == ("words", "meaning"), "the ways that ran are the ways that ran"
    assert got.sort is not None and got.sort.sorted and got.sort.model == "Qwen3-Reranker-0.6B"


async def test_each_mode_is_told_with_the_sorter_after_it() -> None:
    sorted_by = SortReport(sorted=True, model="m", passages=2)
    two = [hit(1), hit(2)]
    one = await run(Fixed("words", two), sorter=Sorter(report=sorted_by))
    assert one.mode == "words, sorted by m"
    only = await run(Fixed("meaning", two, similarity=True), sorter=Sorter(report=sorted_by))
    assert only.mode == "meaning, sorted by m"


async def test_when_they_were_not_the_mode_is_as_it_was_and_the_report_says_why() -> None:
    why = SortReport(
        code="over_budget",
        message="Not sorted: m sorts about 820 tokens a second here, so ...",
        model="m",
        passages=2,
        tokens=5760,
        rate=820.0,
        expected=7.0,
        budget=3.0,
    )
    got = await run(Fixed("words", [hit(1), hit(2)]), sorter=Sorter(report=why))
    assert got.mode == "words" and got.sort is why
    assert got.sort.message and got.sort.message.startswith("Not sorted: ")
    assert (got.sort.rate, got.sort.tokens, got.sort.expected, got.sort.budget) == (
        820.0,
        5760,
        7.0,
        3.0,
    )
    assert chunks(got) == [1, 2], "in the order the search found them"


async def test_with_no_sorter_there_is_no_report_and_the_search_is_as_it_was() -> None:
    got = await run(Fixed("words", [hit(1), hit(2)]))
    assert got.sort is None and got.mode == "words" and chunks(got) == [1, 2]


async def test_with_nothing_found_there_is_nothing_to_sort_and_the_sorter_is_not_asked() -> None:
    sorter = Sorter()
    got = await run(Fixed("words", []), sorter=sorter)
    assert got.passages == [] and sorter.asked == [] and got.sort is None


async def test_one_passage_has_no_order_to_be_put_in_and_the_sorter_is_not_asked() -> None:
    sorter = Sorter()
    got = await run(Fixed("words", [hit(1)]), sorter=sorter)
    assert chunks(got) == [1] and sorter.asked == []
    assert got.sort is not None and got.sort.code == "nothing_to_sort" and got.sort.quiet
    assert not got.sort.sorted and got.mode == "words"


async def test_a_run_that_names_its_ways_is_not_sorted_so_each_way_shows_what_it_finds_alone() -> (
    None
):
    """ "Try one way at a time" is to see what each way finds by itself. A sorter's order over it
    would be the sorter's, not the way's (the review found a words-only run come back as "words,
    sorted by the-sorter" in the sorter's order), so a run that names its ways is not sorted."""
    sorter = Sorter()
    for way, found in (("words", [1, 2, 3]), ("meaning", [4, 5])):
        got = await run(
            Fixed("words", [hit(1), hit(2), hit(3)]),
            Fixed("meaning", [hit(4), hit(5)], similarity=True),
            sorter=sorter,
            ways=[way],
        )
        assert got.mode == way and chunks(got) == found, "in the order that way found them"
        assert got.sort is None and "sorter" not in got.reports and got.ways == (way,)
        assert all("sorter" not in dict(p.ranks) for p in got.passages)
    assert sorter.asked == [], "the sorter was not asked anything"
    # With no ways named, the same signals are sorted (the sorter reverses what it is given).
    every = await run(
        Fixed("words", [hit(1), hit(2), hit(3)]),
        Fixed("meaning", [hit(4), hit(5)], similarity=True),
        sorter=sorter,
    )
    assert every.mode == "meaning and words, sorted by the-sorter" and len(sorter.asked) == 1


def test_the_mode_of_a_search_that_was_not_sorted_is_its_own() -> None:
    assert sorted_mode("words", None) == "words"
    assert sorted_mode("words", SortReport(code="off")) == "words"
    assert sorted_mode("meaning and words", SortReport(sorted=True, model="m")) == (
        "meaning and words, sorted by m"
    )
    assert sorted_mode("none", SortReport(sorted=True, model=None)) == "none"


# --------------------------------------------------------------------------- #
# a sorter that fails never fails a search (call 5)
# --------------------------------------------------------------------------- #


async def test_a_sorter_that_raises_is_a_report_and_the_search_is_whole() -> None:
    words = [hit(n) for n in range(1, 5)]
    got = await run(Fixed("words", words), sorter=Sorter(raises=RuntimeError("fell over")))
    assert chunks(got) == [1, 2, 3, 4], "in the order the search found them"
    assert got.mode == "words" and got.sort is not None and not got.sort.sorted
    assert got.sort.code == "error" and not got.sort.quiet and got.sort.passages == 4
    assert got.sort.message == (
        "Not sorted: sorting failed in Workbench itself (RuntimeError), so the passages are in "
        "the order the search found them. See Workbench's log."
    )


async def test_an_order_that_is_not_every_passage_once_is_not_believed() -> None:
    words = [hit(n) for n in range(1, 5)]
    for bad in ([0, 1, 2], [0, 1, 2, 2], [0, 1, 2, 9], [3, 2, 1, 0, 0], []):
        got = await run(Fixed("words", words), sorter=Sorter(lambda sheets, bad=bad: bad))
        assert chunks(got) == [1, 2, 3, 4], bad
        assert got.sort is not None and not got.sort.sorted and got.sort.code == "unusable"
        assert got.sort.message and got.sort.message.startswith("Not sorted: ")
        assert got.mode == "words"


async def test_a_sorter_that_says_it_did_not_sort_leaves_the_order_and_its_words_are_kept() -> None:
    said = SortReport(
        code="not_running", message="Not sorted: the sorter m is not running.", model="m"
    )
    got = await run(Fixed("words", [hit(1), hit(2)]), sorter=Sorter(report=said))
    assert chunks(got) == [1, 2] and got.sort is said


async def test_a_search_that_is_ended_is_not_made_to_look_like_a_sorter_that_failed() -> None:
    with pytest.raises(asyncio.CancelledError):
        await run(Fixed("words", [hit(1), hit(2)]), sorter=Sorter(raises=asyncio.CancelledError()))


async def test_a_way_that_cannot_run_and_a_sorter_that_cannot_run_are_each_said() -> None:
    from eugene_plexus_workbench.knowledge.search import SignalUnavailable

    class Gone(Fixed):
        async def search(self, *_: Any) -> list[Hit]:
            raise SignalUnavailable("not_running", "Searching by words only: stopped")

    said = SortReport(code="not_running", message="Not sorted: stopped", model="m")
    got = await run(
        Fixed("words", [hit(1), hit(2)]),
        Gone("meaning", [], similarity=True),
        sorter=Sorter(report=said),
    )
    assert got.reports["meaning"].message == "Searching by words only: stopped"
    assert got.sort is said and got.mode == "words"


async def test_the_sorters_report_is_among_the_reports_beside_each_ways_and_says_the_same() -> None:
    three = [hit(1), hit(2), hit(3)]
    done = await run(
        Fixed("words", three),
        Fixed("meaning", [hit(4)], similarity=True),
        sorter=Sorter(report=SortReport(sorted=True, model="m", passages=4)),
    )
    said = done.reports["sorter"]
    assert said.name == "sorter" and said.ran and said.hits == 4
    assert said.code is None and said.message is None and not said.quiet
    assert set(done.reports) == {"words", "meaning", "sorter"}, "beside the ways', not instead"

    why = SortReport(
        code="over_budget",
        message="Not sorted: m sorts about 820 tokens a second here, so ...",
        model="m",
        passages=3,
    )
    held = await run(Fixed("words", three), sorter=Sorter(report=why))
    said = held.reports["sorter"]
    assert not said.ran and said.hits == 0 and not said.quiet
    assert said.code == "over_budget" and said.message == why.message

    quiet = await run(
        Fixed("words", three),
        sorter=Sorter(report=SortReport(code="off", message="Not sorted: off.", quiet=True)),
    )
    assert quiet.reports["sorter"].quiet and quiet.reports["sorter"].code == "off"


async def test_a_sorter_that_raises_says_so_among_the_reports_and_one_that_is_not_there_says_nothing() -> (
    None
):
    broke = await run(Fixed("words", [hit(1), hit(2)]), sorter=Sorter(raises=RuntimeError("x")))
    said = broke.reports["sorter"]
    assert not said.ran and said.code == "error" and (said.message or "").startswith("Not sorted: ")
    bare = await run(Fixed("words", [hit(1), hit(2)]))
    assert "sorter" not in bare.reports and set(bare.reports) == {"words"}
