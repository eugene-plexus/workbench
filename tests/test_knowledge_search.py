"""Finding passages (MR4, design 4.4): the query, BM25 over the person's passages, the
scope filter, and what is chosen, joined and fitted. Real indexes in a temporary
directory; the ranking claims are about real FTS5 output, not about stand-ins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eugene_plexus_workbench import token_count
from eugene_plexus_workbench.knowledge import search
from eugene_plexus_workbench.knowledge.index import LIBRARY, Hit, Index, Indexes, chat_scope
from eugene_plexus_workbench.knowledge.search import (
    Candidate,
    Query,
    Retriever,
    WordSignal,
    plan_query,
)

from .test_knowledge_index import ADA, indexes, put  # noqa: F401  (the fixture)

# --------------------------------------------------------------------------- #
# the query
# --------------------------------------------------------------------------- #


def test_a_query_is_the_words_that_say_something_in_the_order_they_were_asked() -> None:
    got = plan_query("What is the code word for the Lighthouse inspection?")
    assert got.terms == ("code", "word", "lighthouse", "inspection")
    assert got.phrases == ()
    assert got.match() == '"code" OR "word" OR "lighthouse" OR "inspection"'
    assert got.text == "code word lighthouse inspection"


def test_a_follow_up_still_searches_something_by_the_message_before_it() -> None:
    got = plan_query("and the second one?", "How much did the GPU cost last quarter?")
    assert got.terms == ("second", "one", "much", "gpu", "cost", "last", "quarter")
    assert plan_query("and the second one?").terms == ("second", "one")


def test_the_latest_message_comes_first_and_twelve_terms_are_the_most() -> None:
    latest = " ".join(f"alpha{i}" for i in range(10))
    got = plan_query(latest, "bravo one two three four five")
    assert len(got.terms) == 12
    assert got.terms[:10] == tuple(f"alpha{i}" for i in range(10))
    assert got.terms[10:] == ("bravo", "one")


def test_the_latest_message_is_cut_at_a_thousand_characters_and_the_one_before_at_three_hundred() -> (
    None
):
    filler = "x " * 600
    assert "tail" not in plan_query(filler + "tail").terms
    assert "late" not in plan_query("hello", ("y " * 200) + "late").terms


def test_stop_words_and_short_words_say_nothing_and_are_left_out_but_numbers_may() -> None:
    got = plan_query("Is it in my 42 or the 6042 of an ox?")
    assert got.terms == ("6042",)
    assert plan_query("the and of?").empty and plan_query("the and of?").match() is None


def test_case_and_accents_are_taken_off_on_both_sides_of_the_search() -> None:
    assert plan_query("Café Zürich").terms == ("cafe", "zurich")


def test_an_identifier_is_searched_as_an_exact_phrase_as_well_as_by_its_parts() -> None:
    got = plan_query("What is the code word TAMARIND-6042 and part 135-SD-12?")
    assert got.phrases == ("tamarind 6042", "135 sd 12")
    assert "tamarind" in got.terms and "6042" in got.terms and "135" in got.terms
    assert got.match() is not None and '"tamarind 6042"' in got.match()  # type: ignore[operator]


def test_a_quoted_string_is_searched_as_an_exact_phrase() -> None:
    got = plan_query('what does "second GPU" cost, and “the cooling loop”?')
    assert got.phrases == ("second gpu", "the cooling loop")


def test_a_quoted_word_the_question_did_not_otherwise_search_is_still_searched() -> None:
    assert plan_query('is "it" there?').phrases == ("it",)
    assert plan_query('is "harbour" there?').phrases == (), "already a term"


def test_nothing_a_person_types_is_search_syntax() -> None:
    nasty = 'NEAR(a b) OR "x AND (y*) -z col:foo ^bar AND NOT'
    got = plan_query(nasty)
    match = got.match()
    assert match is not None
    for token in ("(", ")", "*", ":", "^", "-"):
        assert token not in match.replace('"', "").replace(" OR ", "")


async def test_no_text_a_person_types_makes_a_search_fail(indexes: Indexes) -> None:  # noqa: F811
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", "Some words about harbours.")
    for text in (
        'NEAR(a b) OR "x AND (y*) -z',
        "a:b c^d \" ' ) (",
        "OR OR AND NOT",
        "\u0000 " + chr(0x2028),
    ):
        got = await Retriever().retrieve(
            index, plan_query(text), [LIBRARY], question=text, budget=None, overhead=0
        )
        assert got.passages == []


# --------------------------------------------------------------------------- #
# ranking
# --------------------------------------------------------------------------- #


async def corpus(index: Index) -> None:
    await put(
        index, "doc00001", "# Budget\n\nThe second GPU cost is a lot of money.\n", name="plan.md"
    )
    await put(index, "doc00002", "# Notes\n\nThe cooling loop needs a pump.\n", name="notes.md")
    await put(index, "doc00003", "# Misc\n\nNothing about hardware at all here.\n", name="misc.md")
    await put(
        index, "doc00004", "# Fans\n\nA GPU fan and a GPU shroud and a GPU cable.\n", name="fans.md"
    )


async def ask(index: Index, question: str, *scopes: str, **kw: object) -> search.Retrieval:
    return await Retriever().retrieve(
        index,
        plan_query(question),
        scopes or [LIBRARY],
        question=question,
        budget=kw.pop("budget", None),  # type: ignore[arg-type]
        overhead=0,
        **kw,  # type: ignore[arg-type]
    )


async def test_the_passage_with_more_of_the_rare_words_is_first_by_bm25(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await corpus(index)
    got = await ask(index, "How much does the GPU cost?")
    # cost is rare (one passage), GPU is common: the passage that has both wins, and
    # the one with three GPUs and no cost is second. There is no stemming: "costs" is not "cost".
    assert [p.name for p in got.passages] == ["plan.md", "fans.md"]
    assert got.mode == "words" and got.ways == ("words",)
    assert all(p.via == "words" for p in got.passages)
    assert [p.rank for p in got.passages] == list(range(1, len(got.passages) + 1))


async def test_a_word_in_a_heading_outweighs_the_same_word_in_the_text(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    # The passage that has the word in its text is the shorter one and the first by id, so
    # on equal weights it would win; it is the heading's double weight that puts the other first.
    await put(index, "doc00001", "# Visits\n\nThe lighthouse opens.\n", name="b.md")
    await put(
        index, "doc00002", "# Lighthouse\n\nOpening hours are posted at the door.\n", name="a.md"
    )
    for n in range(3, 9):  # so that the word is a rare one
        await put(
            index,
            f"doc{n:05d}",
            f"# Misc\n\nNothing about hardware at all here and more plain words {n}.\n",
            name=f"m{n}.md",
        )
    got = await ask(index, "lighthouse")
    assert [p.name for p in got.passages] == ["a.md", "b.md"]
    assert got.passages[0].in_heading == ("lighthouse",) and got.passages[1].in_heading == ()


async def test_an_exact_phrase_outranks_a_passage_with_only_one_of_its_words(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", "The tamarind trees grow near the river.", name="a.md")
    await put(index, "doc00002", "The code word is TAMARIND-6042, said twice.", name="b.md")
    got = await ask(index, "what is TAMARIND-6042?")
    assert got.passages[0].name == "b.md" and got.passages[0].phrases == ("tamarind 6042",)


async def test_accents_and_case_do_not_hide_a_passage(indexes: Indexes) -> None:  # noqa: F811
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", "We met at the Café on Rue Saint-Honoré yesterday.")
    assert (await ask(index, "CAFE")).passages[0].terms == ("cafe",)
    assert (await ask(index, "honore")).passages
    assert (await ask(index, "café")).passages


async def test_nothing_matched_is_an_empty_result_and_not_an_error(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await corpus(index)
    got = await ask(index, "What is the airspeed of a swallow?")
    assert got.passages == [] and got.found == 0 and got.did_not_fit == 0


async def test_a_way_of_searching_that_breaks_raises_and_is_never_nothing_matched(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await corpus(index)

    class Broken:
        name = "words"

        async def search(self, *_: object) -> list[Hit]:
            raise RuntimeError("the way of searching fell over")

    with pytest.raises(RuntimeError, match="fell over"):
        await Retriever([Broken()]).retrieve(
            index, plan_query("cost"), [LIBRARY], question="cost", budget=None, overhead=0
        )


# --------------------------------------------------------------------------- #
# the scope is part of the query that ranks
# --------------------------------------------------------------------------- #


async def test_a_passage_of_another_scope_is_never_a_candidate(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", "The harbour master lives here.", name="lib.md")
    await put(
        index,
        "doc00002",
        "The harbour master of chat one.",
        name="c1.md",
        scope=chat_scope("c1"),
        source="a:1",
    )
    await put(
        index,
        "doc00003",
        "The harbour master of chat two.",
        name="c2.md",
        scope=chat_scope("c2"),
        source="a:2",
    )
    one = await ask(index, "harbour master", LIBRARY, chat_scope("c1"))
    assert {p.name for p in one.passages} == {"lib.md", "c1.md"}
    two = await ask(index, "harbour master", chat_scope("c2"))
    assert [p.name for p in two.passages] == ["c2.md"]
    nowhere = await ask(index, "harbour master", chat_scope("c3"))
    assert nowhere.passages == []
    assert await index.search('"harbour"', [], 10) == []


async def test_other_scopes_cannot_crowd_a_scopes_passage_out_of_the_candidates(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    for n in range(12):
        await put(index, f"doc{n:05d}", f"Harbour harbour harbour harbour {n}.", name=f"lib{n}.md")
    await put(
        index,
        "doc90001",
        "A single harbour in the chat's file.",
        name="c.md",
        scope=chat_scope("c1"),
        source="a:1",
    )
    hits = await index.search('"harbour"', [chat_scope("c1")], 3)
    assert [h.name for h in hits] == ["c.md"], "ranked among its own scope's, not the library's"
    assert [h.name for h in await index.search('"harbour"', [LIBRARY], 3)] == [
        h.name for h in await index.search('"harbour"', [LIBRARY], 3)
    ]
    assert all(h.scope == LIBRARY for h in await index.search('"harbour"', [LIBRARY], 20))


# --------------------------------------------------------------------------- #
# choosing, joining, fitting
# --------------------------------------------------------------------------- #


def hit(
    chunk_id: int,
    doc_id: str = "d1",
    ord_: int = 0,
    text: str = "words",
    heading: str | None = None,
    pages: tuple[int | None, int | None] = (None, None),
    name: str = "plan.pdf",
) -> Hit:
    return Hit(
        chunk_id=chunk_id,
        doc_id=doc_id,
        ord=ord_,
        text=text,
        heading_path=heading,
        page_start=pages[0],
        page_end=pages[1],
        line_start=None,
        line_end=None,
        char_start=0,
        char_end=len(text),
        tokens_est=token_count.estimate(text),
        name=name,
        scope=LIBRARY,
        kind="pdf",
        score=-1.0,
    )


def candidates(*hits: Hit) -> list[Candidate]:
    return [Candidate(h, ("words",)) for h in hits]


def test_at_most_three_passages_come_from_one_document_unless_the_question_names_it() -> None:
    six = candidates(*(hit(i, "d1", i) for i in range(6)), hit(10, "d2", 0))
    chosen = search.select(six, k=5, per_document=3, named=set())
    assert [c.hit.chunk_id for c in chosen] == [0, 1, 2, 10]
    named = search.select(six, k=5, per_document=3, named={"d1"})
    assert [c.hit.chunk_id for c in named] == [0, 1, 2, 3, 4]
    assert len(search.select(six, k=2, per_document=3, named=set())) == 2


def test_a_question_names_a_file_when_every_word_of_its_name_is_in_it() -> None:
    assert search.names_document("hardware-plan.pdf", "how much in the hardware plan for GPUs?")
    assert search.names_document("Hardware_Plan.PDF", "Hardware plan: second GPU?")
    assert not search.names_document("hardware-plan.pdf", "what is the plan?")
    assert not search.names_document("a.pdf", "what is a?")
    assert not search.names_document("", "anything")


def test_passages_that_follow_each_other_in_one_file_are_one_passage_with_one_citation() -> None:
    chosen = candidates(
        hit(5, "d1", 4, "Second half.", "Budget", (2, 2)),
        hit(9, "d2", 0, "Other file.", None, (1, 1), "other.pdf"),
        hit(4, "d1", 3, "First half.", "Budget", (1, 2)),
    )
    merged = search.merge(chosen)
    assert [(p.rank, p.name) for p in merged] == [(1, "plan.pdf"), (2, "other.pdf")]
    one = merged[0]
    assert one.text == "First half.\n\nSecond half." and one.chunk_ids == (4, 5)
    assert (one.page_start, one.page_end) == (1, 2) and one.heading == "Budget"


def test_passages_under_different_headings_or_not_next_to_each_other_stay_apart() -> None:
    chosen = candidates(
        hit(1, "d1", 1, "A", "One"), hit(2, "d1", 2, "B", "Two"), hit(3, "d1", 4, "C", "Two")
    )
    assert [p.chunk_ids for p in search.merge(chosen)] == [(1,), (2,), (3,)]


def test_a_merged_passage_is_never_longer_than_the_limit() -> None:
    big = "word " * 400  # about 670 tokens by the cautious rule
    chosen = candidates(hit(1, "d1", 1, big), hit(2, "d1", 2, big))
    assert [p.chunk_ids for p in search.merge(chosen)] == [(1,), (2,)]
    assert [p.chunk_ids for p in search.merge(chosen, limit=5_000)] == [(1, 2)]


def passage(rank: int, tokens: int, doc: str = "d1") -> search.Retrieved:
    return search.Retrieved(
        rank=rank,
        doc_id=doc,
        name="f.pdf",
        scope=LIBRARY,
        kind="pdf",
        text="x",
        heading=None,
        page_start=None,
        page_end=None,
        line_start=None,
        line_end=None,
        chunk_ids=(rank,),
        tokens=tokens,
        via="words",
    )


def test_passages_are_fitted_to_the_budget_in_order_and_what_did_not_fit_is_counted() -> None:
    ranked = [passage(1, 300), passage(2, 300), passage(3, 300), passage(4, 100)]
    fit, dropped = search.pack(ranked, 700, 20)
    assert [p.tokens for p in fit] == [300, 300] and dropped == 2
    # 320 + 320 = 640; 320 more is 960 > 700; and 120 fits in what is left? 640 + 120 = 760 > 700.
    fit, dropped = search.pack(ranked, 800, 20)
    assert [p.chunk_ids for p in fit] == [(1,), (2,), (4,)] and dropped == 1
    assert [p.rank for p in fit] == [1, 2, 3], "numbered again, from 1, with no gap"


def test_no_budget_is_no_limit_and_a_budget_too_small_for_one_passage_sends_none() -> None:
    ranked = [passage(1, 300), passage(2, 300)]
    assert search.pack(ranked, None, 20) == (ranked, 0)
    assert search.pack(ranked, 200, 20) == ([], 2)


def test_two_ways_of_searching_are_joined_by_reciprocal_rank_and_a_lone_find_still_appears() -> (
    None
):
    a, b, c, d = (hit(i) for i in (1, 2, 3, 4))
    one = search.fuse({"words": [a, b, c]})
    assert [x.hit.chunk_id for x in one] == [1, 2, 3] and {x.via for x in one} == {("words",)}
    both = search.fuse({"words": [a, b, c], "meaning": [c, d, a]})
    order = [(x.hit.chunk_id, x.via) for x in both]
    assert order[0][0] in (1, 3) and set(order[0][1]) == {"words", "meaning"}
    assert {i for i, _ in order} == {1, 2, 3, 4}, "found by one way only, and still there"
    assert dict(order)[4] == ("meaning",) and dict(order)[2] == ("words",)
    assert search._via(["words", "meaning"]) == "both" and search._via(["words"]) == "words"
    # 1 / (60 + rank): the top of a list counts for more, and being in two lists for most.
    skewed = search.fuse({"words": [a, b, c, d], "meaning": [d]})
    assert [x.hit.chunk_id for x in skewed] == [4, 1, 2, 3]
    tied = search.fuse({"words": [a, b, c], "meaning": [c, d, a]})
    assert [x.hit.chunk_id for x in tied] == [1, 3, 2, 4], (
        "equal scores keep the order they came in"
    )


async def test_the_retriever_picks_chooses_joins_and_fits_in_that_order(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    text = "\n\n".join(f"# Part {i}\n\n" + ("The harbour is busy. " * 30) for i in range(8))
    await put(index, "doc00001", text, name="records.md")
    got = await Retriever().retrieve(
        index, plan_query("harbour"), [LIBRARY], question="harbour", budget=None, overhead=0
    )
    assert len(got.passages) == 3, "three from one file"
    assert got.found == 8 and got.did_not_fit == 0
    named = await Retriever().retrieve(
        index,
        plan_query("harbour"),
        [LIBRARY],
        question="the records file harbour",
        budget=None,
        overhead=0,
    )
    assert len(named.passages) == 5, "named: as many as k allows"
    tight = await Retriever().retrieve(
        index, plan_query("harbour"), [LIBRARY], question="harbour", budget=400, overhead=40
    )
    assert len(tight.passages) == 1 and tight.did_not_fit == 2


# --------------------------------------------------------------------------- #
# how a passage reads in a list
# --------------------------------------------------------------------------- #


def test_a_snippet_is_at_most_240_characters_around_the_words_searched_for() -> None:
    text = ("alpha " * 100) + "the lighthouse inspection code is RAVEN-77 " + ("omega " * 100)
    got = search.snippet(text, ["lighthouse"])
    assert len(got) <= 240 and "lighthouse" in got
    assert got.startswith("…") and got.endswith("…")
    short = "A short passage."
    assert search.snippet(short, ["short"]) == short
    assert search.snippet("  a  b\n\nc  ", []) == "a b c"
    assert len(search.snippet("x" * 1000)) <= 240
    assert search.snippet("word " * 100).endswith("…")


def test_a_snippet_is_around_where_the_most_words_searched_for_are_together_as_whole_words() -> (
    None
):
    text = (
        "Plain words and more plain words about nothing in particular, " * 6
        + "The code word for the lighthouse inspection is RAVEN-7731. "
        + "More plain words at the end of it all. " * 6
    )
    got = search.snippet(text, ["code", "word", "lighthouse", "inspection"])
    assert "RAVEN-7731" in got and len(got) <= 240, "not around 'word' inside 'words'"
    assert search.snippet(text, ["lighthouse"]).count("lighthouse") == 1
    assert search.snippet(text, ["absent"]).startswith("Plain words"), "from its start"


def test_a_passage_says_which_of_the_words_searched_for_are_in_it_and_in_its_heading() -> None:
    query = Query(("gpu", "cost", "cooling"), ("second gpu",))
    one = search.Retrieved(
        rank=1,
        doc_id="d",
        name="f",
        scope=LIBRARY,
        kind="md",
        text="The second GPU costs a lot, and the GPU is loud.",
        heading="Cooling > GPUs",
        page_start=None,
        page_end=None,
        line_start=None,
        line_end=None,
        chunk_ids=(1,),
        tokens=10,
        via="words",
    )
    got = search.explain(query, one)
    assert got.terms == ("gpu",), "costs is not cost: no stemming"
    assert got.phrases == ("second gpu",) and got.in_heading == ("cooling",)


def test_the_word_signal_has_nothing_to_search_for_when_the_query_is_empty() -> None:
    assert Query().empty and Query().match() is None
    assert WordSignal.name == "words"


@pytest.mark.parametrize("name", ["words"])
async def test_the_retriever_says_how_it_searches(name: str, tmp_path: Path) -> None:
    assert Retriever().mode == "words" and [s.name for s in Retriever().signals] == [name]

    class Meaning:
        name = "meaning"

        async def search(self, *_: object) -> list[Hit]:
            return []

    assert Retriever([WordSignal(), Meaning()]).mode == "meaning and words"
