"""Sorting what a search found, through a real Workbench over a fake Eugene (MR6b): the best
passages of a question are put in order by a result sorter the gateway lists, when one is running
on the install's own machines and is quick enough; and a sorter that is off, stopped, outside, too
slow or failing leaves the search whole, and says why in its own words.

What the Documents screen, Test a question and an answer's search record are told is read from a
live look, and every number in it is one the decision was made of.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
import pytest

from .conftest import ADMIN_TOKEN, World, search_model, sorter_model
from .meaning import (
    ARRIVALS,
    FEES,
    KITCHEN,
    LEDGER,
    ODD,
    config,
    corpus,
    quick,  # noqa: F401
    search_of,
    try_question,
    until,
)
from .saved import ask, block_of, chat_on, keep, md, start
from .sorting import (
    QUESTION,
    SORTER,
    places,
    quick_sorting,  # noqa: F401
    sort_of,
    sorter_world,
    texts,
    unsorted,
)
from .test_documents_page import interface
from .test_saved_page import base_mode, body, literals


def long_handbook(sections: int = 6) -> tuple[str, bytes]:
    """A file of passages the size of a passage (about 320 tokens as Workbench counts them), each
    under a heading of its own, every one of which the words of the question find."""
    sentence = (
        "{k} of the harbour handbook says how the vessel registry, the ledger and the berthing "
        "fees are kept by the master's clerk. "
    )
    body = "# Handbook\n\n" + "\n\n".join(
        f"## Section {k}\n\n" + sentence.format(k=f"Section {k}") * 7
        for k in range(1, sections + 1)
    )
    return "handbook.md", body.encode()


def source_keys(answer: dict[str, Any]) -> set[frozenset[str]]:
    return {frozenset(s) for s in answer["sources"]}


def schema(world: World) -> dict[str, dict[str, Any]]:
    got = httpx.get(
        f"{world.workbench}/v1/config/schema",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        trust_env=False,
    ).json()
    return {f["key"]: f for f in got["fields"]}


def measured(person: Any) -> dict[str, Any]:
    """The Documents screen's word on the sorter once it has been measured: the small request that
    asks how fast it reads was asked, answered, and its time kept (not merely begun)."""
    return dict(
        until(
            lambda: (s := search_of(person)["sort"])["ratePerSecond"] is not None and s,
            seconds=30,
        )
    )


def seconds_status(world: World) -> dict[str, Any]:
    """The Sort time setting's status, once the sorter has been measured."""

    def done() -> dict[str, Any] | None:
        status = schema(world)["documentsSortSeconds"]["status"]
        return None if status["text"].startswith("Measuring") else dict(status)

    return dict(until(done, seconds=30))


# --------------------------------------------------------------------------- #
# a question is sorted (design calls 1, 2, 3 and 6)
# --------------------------------------------------------------------------- #


def test_the_best_passages_are_sent_to_the_first_local_running_sorter_as_their_headings_and_words(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world, SORTER, "second-sorter")
    ada = start(world)
    keep(ada, *corpus())
    found = try_question(ada, QUESTION)
    assert found["status"] == "found"
    assert found["mode"] == f"meaning and words, sorted by {SORTER}"
    assert base_mode(found["mode"]) in literals("TestedQuestion", "mode"), "the page knows the mode"
    block = sort_of(found)
    assert set(block) == interface("SortRun"), "the page's types are the server's fields"
    assert block["used"] is True and block["code"] is None and block["message"] is None
    assert block["model"] == SORTER and block["setting"] == "auto" and block["quiet"] is False
    assert block["passages"] == 5 and block["tokens"] > 0 and block["budgetSeconds"] == 3.0
    assert block["ratePerSecond"] > 0 and block["seconds"] is not None
    # What the gateway was asked: the first sorter, the question, each passage as headings then words.
    [sort] = world.gateway.sorts()
    assert sort["model"] == SORTER
    assert sort["query"] == QUESTION
    assert set(sort) == {"model", "query", "documents"}, (
        "Jina's body: no top_n, no return_documents"
    )
    assert sort["documents"] == [
        f"Harbour office > Arrivals\n{ARRIVALS}",
        f"Harbour office > Ledger\n{LEDGER}",
        f"Harbour office > Fees\n{FEES}",
        f"Harbour office > Kitchen\n{KITCHEN}",
        f"Odd jobs\n{ODD}",
    ]
    assert len(world.gateway.probes()) == 1, "the sorter was measured once, before its first sort"


def test_each_passage_says_where_the_sorter_put_it_beside_where_words_and_meaning_did(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    ada = start(world)
    keep(ada, *corpus())
    found = try_question(ada, QUESTION)
    ranks = places(found)
    assert [r["sorter"] for r in ranks] == sorted(r["sorter"] for r in ranks), "in its order"
    assert ranks[0]["sorter"] == 1
    assert all(set(r) <= interface("Places") for r in ranks), "the page's types know the place"
    assert any({"words", "meaning", "sorter"} <= set(r) for r in ranks), (
        "found by both, then sorted"
    )
    assert all(isinstance(r["sorter"], int) and r["sorter"] >= 1 for r in ranks)
    assert {p["via"] for p in found["passages"]} <= {"words", "meaning", "both"}, (
        "how a passage was found is not the sorter's to change"
    )
    assert "score" not in json.dumps(found).lower().replace("relevance", ""), (
        "places, never a score: the sorter's 0 to 1 means nothing beside the others"
    )


def test_the_sorters_order_is_the_order_the_passages_are_sent_in_and_not_the_one_they_were_found_in(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    ada = start(world)
    keep(ada, *corpus())
    found_order = texts(unsorted(ada, world))
    assert found_order[0] != FEES and FEES in found_order
    world.gateway.rerank_prefer = {FEES: 0.99, ODD: 0.98}  # the sorter likes these two best
    got = try_question(ada, QUESTION)
    assert texts(got)[:2] == [FEES, ODD], "the sorter's order"
    assert sorted(texts(got)) == sorted(found_order), "the same passages, in another order"
    assert [p["rank"] for p in got["passages"]] == [1, 2, 3, 4], "cited in the new order"
    assert places(got)[0]["sorter"] == 1 and places(got)[1]["sorter"] == 2
    assert got["found"] == 5 and got["status"] == "found"


def test_with_the_sorter_off_the_search_is_as_it_was_and_the_gateway_is_not_asked(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    assert config(world, documentsSortModel="off").status_code == 200
    ada = start(world)
    keep(ada, *corpus())  # and every look at the Documents screen on the way
    got = try_question(ada, QUESTION)
    assert got["mode"] == "meaning and words" and got["status"] == "found"
    block = sort_of(got)
    assert block["used"] is False and block["code"] == "off" and block["quiet"] is True
    assert block["setting"] == "off" and block["model"] is None
    assert block["message"] == "Not sorted: sorting is turned off (Result sorter is set to off)."
    assert world.gateway.rerank_requests == [], "off asks no sorter anything, not even how fast"
    assert places(got)[0].get("sorter") is None
    on = config(world, documentsSortModel="auto")
    assert on.status_code == 200
    assert try_question(ada, QUESTION)["mode"] == f"meaning and words, sorted by {SORTER}"


def test_a_named_sorter_is_the_one_asked_and_auto_goes_back_to_the_first(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world, SORTER, "second-sorter")
    # Before the screen is first looked at: a look at it measures the sorter that is in use.
    assert config(world, documentsSortModel="second-sorter").status_code == 200
    ada = start(world)
    keep(ada, *corpus())
    got = try_question(ada, QUESTION)
    assert got["mode"] == "meaning and words, sorted by second-sorter"
    assert sort_of(got)["model"] == "second-sorter" and sort_of(got)["setting"] == "second-sorter"
    assert {r["body"]["model"] for r in world.gateway.rerank_requests} == {"second-sorter"}
    assert config(world, documentsSortModel=None).status_code == 200
    again = try_question(ada, QUESTION)
    assert again["mode"] == f"meaning and words, sorted by {SORTER}"
    assert sort_of(again)["setting"] == "auto"


def test_a_name_that_is_not_a_sorter_is_told_from_one_that_is_not_there(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    assert config(world, documentsSortModel="mr5-embed").status_code == 200
    ada = start(world)
    keep(ada, *corpus())
    chat = sort_of(try_question(ada, QUESTION))
    assert chat["used"] is False and chat["code"] == "not_sorter" and chat["model"] == "mr5-embed"
    assert "mr5-embed is not a result sorter" in chat["message"]
    assert config(world, documentsSortModel="ghost").status_code == 200
    ghost = sort_of(try_question(ada, QUESTION))
    assert (
        ghost["code"] == "not_listed" and "lists no result sorter named ghost" in ghost["message"]
    )
    assert world.gateway.rerank_requests == []


# --------------------------------------------------------------------------- #
# never an outside sorter by itself; never a stopped one (design call 3)
# --------------------------------------------------------------------------- #


def test_auto_never_picks_a_sorter_outside_the_machines_even_when_it_is_the_only_one(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    world.gateway.sorters = [sorter_model("cloud", locality="external", owned_by="openai")]
    ada = start(world)
    keep(ada, *corpus())
    assert config(world, documentsExternalEmbedding=True).status_code == 200, "even when allowed"
    got = try_question(ada, QUESTION)
    block = sort_of(got)
    assert got["mode"] == "meaning and words" and got["status"] == "found"
    assert block["used"] is False and block["code"] == "outside_only" and block["quiet"] is False
    assert "outside your machines (cloud)" in block["message"]
    assert world.gateway.rerank_requests == [], "no passage left the install"


def test_auto_leaves_a_stopped_sorter_alone_and_a_local_one_beside_it_is_the_one_used(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    world.gateway.sorters = [
        sorter_model("cloud", locality="external", owned_by="openai"),
        sorter_model("stopped", ready=False),
    ]
    ada = start(world)
    keep(ada, *corpus())
    stopped = sort_of(try_question(ada, QUESTION))
    assert stopped["used"] is False and stopped["code"] == "not_running"
    assert stopped["model"] == "stopped" and stopped["quiet"] is False
    assert stopped["message"] == (
        "Not sorted: the sorter stopped is not running. Start it in Eugene's console under "
        "Backends."
    )
    assert world.gateway.rerank_requests == []
    world.gateway.sorters.append(sorter_model("running"))
    time.sleep(0.1)
    got = try_question(ada, QUESTION)
    assert got["mode"] == "meaning and words, sorted by running"
    assert {r["body"]["model"] for r in world.gateway.rerank_requests} == {"running"}


def test_a_named_sorter_outside_the_machines_is_sent_passages_only_when_the_owner_allows_it(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    world.gateway.sorters = [sorter_model("cloud", locality="external", owned_by="openai")]
    ada = start(world)
    keep(ada, *corpus())
    assert config(world, documentsSortModel="cloud").status_code == 200
    refused = sort_of(try_question(ada, QUESTION))
    assert refused["code"] == "outside" and refused["used"] is False
    assert refused["message"] == (
        "Not sorted: cloud runs on openai, outside your machines, and the owner has not "
        "allowed sending the text of your documents there."
    )
    assert world.gateway.rerank_requests == []
    assert config(world, documentsExternalEmbedding=True).status_code == 200
    allowed = try_question(ada, QUESTION)
    assert allowed["mode"] == "meaning and words, sorted by cloud"
    assert world.gateway.sorts(), "named, and allowed: it was sent the passages"


# --------------------------------------------------------------------------- #
# only when quick enough, both ways (design call 4)
# --------------------------------------------------------------------------- #


def test_a_sorter_too_slow_for_the_time_allowed_is_not_asked_and_the_page_is_told_why_with_the_numbers(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    world.gateway.rerank_rate = 1500.0  # tokens a second, as Workbench counts them
    ada = start(world)
    keep(ada, *corpus(), long_handbook())
    assert config(world, documentsSortSeconds=0.5).status_code == 200
    found_order = texts(unsorted(ada, world))
    got = try_question(ada, QUESTION)
    block = sort_of(got)
    assert got["status"] == "found" and got["mode"] == "meaning and words"
    assert block["used"] is False and block["code"] == "over_budget" and block["quiet"] is False
    assert block["model"] == SORTER and block["budgetSeconds"] == 0.5
    assert block["passages"] >= 10 and block["tokens"] > 1500 * 0.5
    assert 400 < block["ratePerSecond"] < 1600, "what the probe measured of a sorter at 1,500"
    assert block["expectedSeconds"] > 0.5
    message = block["message"]
    assert message.startswith(f"Not sorted: {SORTER} sorts about ")
    assert (
        f"here, so sorting these {block['passages']} passages ({block['tokens']:,} tokens) "
        in message
    )
    assert "would take about " in message and "and Sort time is 0.5 s." in message
    assert message.endswith(
        "Raise Sort time in Workbench's settings (in Eugene's console) to sort anyway."
    )
    assert texts(got) == found_order, "in the order the search found them"
    assert world.gateway.sorts() == [], "no sort was asked of the sorter"
    assert len(world.gateway.probes()) == 1 and len(world.gateway.rerank_requests) == 1, (
        "only the small request that measured how fast it reads"
    )


def test_a_raised_budget_sorts_the_same_question(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    world.gateway.rerank_rate = 1500.0
    ada = start(world)
    keep(ada, *corpus(), long_handbook())
    assert config(world, documentsSortSeconds=0.5).status_code == 200
    assert sort_of(try_question(ada, QUESTION))["code"] == "over_budget"
    assert config(world, documentsSortSeconds=10).status_code == 200
    got = try_question(ada, QUESTION)
    block = sort_of(got)
    assert block["used"] is True and block["budgetSeconds"] == 10.0
    assert got["mode"] == f"meaning and words, sorted by {SORTER}"
    assert block["seconds"] is not None and block["seconds"] >= block["expectedSeconds"] * 0.5
    assert len(world.gateway.sorts()) == 1


def test_the_screen_says_the_same_thing_before_any_question_is_asked(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    world.gateway.rerank_rate = 400.0  # a sort of the usual size takes about 14 seconds
    ada = start(world)
    keep(ada, *corpus())
    search_of(ada)  # opening the screen is enough: the sorter is measured behind it
    sort = measured(ada)
    head = search_of(ada)
    assert sort["setting"] == "auto" and sort["automatic"] is True and sort["model"] == SORTER
    assert sort["sorting"] is False and sort["code"] == "over_budget" and sort["quiet"] is False
    assert sort["budgetSeconds"] == 3.0 and 200 < sort["ratePerSecond"] < 450
    assert sort["rateFrom"] == "probe"
    assert sort["typical"]["passages"] == 18 and sort["typical"]["tokens"] == 18 * 320
    assert sort["typical"]["seconds"] == pytest.approx(5760 / sort["ratePerSecond"], rel=0.01)
    assert sort["message"].startswith(f"Not sorted: {SORTER} sorts about ")
    assert (
        "so sorting the 18 passages a question usually finds (about 300 tokens each)"
        in sort["message"]
    )
    assert "and Sort time is 3 s. Raise Sort time" in sort["message"]
    assert head["mode"] == "meaning and words", "not sorted, so not said to be"
    assert config(world, documentsSortSeconds=60).status_code == 200
    now = search_of(ada)
    assert now["sort"]["sorting"] is True and now["sort"]["code"] == "ok"
    assert now["mode"] == f"meaning and words, sorted by {SORTER}"
    assert base_mode(now["mode"]) in literals("SearchFacts", "mode"), "the page knows the mode"
    assert now["sort"]["budgetSeconds"] == 60.0 and now["sort"]["message"] is None
    chip = ada.get(f"/api/chats/{ada.new_chat()}/documents").json()
    assert chip["search"]["sort"]["sorting"] is True and chip["search"]["mode"] == now["mode"]


def test_the_header_names_every_field_the_documents_screen_reads_of_the_sorter(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    world.gateway.sorters.append(sorter_model("cloud", locality="external", owned_by="openai"))
    ada = start(world)
    keep(ada, *corpus())
    search_of(ada)
    sort = measured(ada)
    assert set(sort) == {
        "setting", "automatic", "sorting", "code", "message", "quiet", "model", "named", "label",
        "locality", "runsOn", "allowExternal", "outside", "budgetSeconds", "ratePerSecond",
        "rateFrom", "typical",
    }  # fmt: skip
    assert set(sort) == interface("SortFacts"), "the page's types are the server's fields"
    assert set(sort["typical"]) == {"passages", "passageTokens", "tokens", "seconds"}
    assert sort["label"] == f"In use: {SORTER} (automatic)" and sort["locality"] == "local"
    assert sort["outside"] == ["cloud"] and sort["allowExternal"] is False
    assert sort["code"] == "ok" and sort["sorting"] is True and sort["quiet"] is False


def test_the_header_with_no_sorter_says_so_quietly_and_the_mode_is_as_it_was(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    world.gateway.sorters = []
    ada = start(world)
    keep(ada, *corpus())
    head = search_of(ada)
    assert head["mode"] == "meaning and words"
    assert head["sort"]["code"] == "none_set_up" and head["sort"]["quiet"] is True
    assert head["sort"]["model"] is None and head["sort"]["ratePerSecond"] is None
    assert head["sort"]["sorting"] is False
    got = try_question(ada, QUESTION)
    assert sort_of(got)["code"] == "none_set_up" and sort_of(got)["quiet"] is True
    assert got["mode"] == "meaning and words" and world.gateway.rerank_requests == []


# --------------------------------------------------------------------------- #
# a sorter that fails never fails a search (design call 5)
# --------------------------------------------------------------------------- #


def _failing(world: World, ada: Any) -> tuple[dict[str, Any], list[str]]:
    """The search as it is found with the sorter measured and sorting well, so that the next
    sort is the one that is made to fail."""
    first = try_question(ada, QUESTION)
    assert sort_of(first)["used"] is True
    return first, texts(unsorted(ada, world))


@pytest.mark.parametrize(
    ("answer", "code", "words"),
    [
        (
            (503, {"error": {"message": "No backend for 'mr6b-sorter' is ready."}}, {}),
            "not_running",
            "the sorter mr6b-sorter is not running",
        ),
        (
            (404, {"error": {"message": "No reachable driver serves 'mr6b-sorter'."}}, {}),
            "not_running",
            "the sorter mr6b-sorter is not running",
        ),
        (
            (
                400,
                {"error": {"message": "input (2301 tokens) is too large to process."}},
                {},
            ),
            "refused",
            "refused the request (input (2301 tokens) is too large to process.)",
        ),
        (
            (500, {"error": {"message": "the engine fell over"}}, {}),
            "error",
            "failed (the engine fell over)",
        ),
        (
            (429, {"error": {"message": "60 a minute"}}, {"retry-after": "30"}),
            "limited",
            "key is at its limit (60 a minute)",
        ),
        (
            (401, {"error": {"message": "invalid key"}}, {}),
            "key",
            "Eugene refused the key this Workbench uses",
        ),
        (
            (200, {"object": "list", "results": []}, {}),
            "unusable",
            "it held 0 scores for 5 passages",
        ),
        (
            (200, {"results": [{"index": 0, "relevance_score": "high"}] * 5}, {}),
            "unusable",
            "a score was missing, repeated or not a number",
        ),
        ((200, "this is not json", {}), "unusable", "it was not JSON"),
    ],
)
def test_a_sorter_that_fails_leaves_the_search_whole_and_says_what_it_said(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
    answer: tuple[int, Any, dict[str, str]],
    code: str,
    words: str,
) -> None:
    sorter_world(world)
    ada = start(world)
    keep(ada, *corpus())
    _, found_order = _failing(world, ada)
    world.gateway.rerank_requests.clear()
    world.gateway.rerank_script.append(answer)
    got = try_question(ada, QUESTION)
    assert got["status"] == "found", "a sorter that fails never fails a search"
    assert texts(got) == found_order, "in the order the search found them"
    assert got["mode"] == "meaning and words"
    block = sort_of(got)
    assert block["used"] is False and block["code"] == code and block["quiet"] is False
    assert block["message"].startswith("Not sorted: ") and words in block["message"]
    assert block["model"] == SORTER and block["passages"] == 5
    assert all("sorter" not in r for r in places(got))
    again = try_question(ada, QUESTION)
    assert sort_of(again)["used"] is True, "and the next question is sorted again"


def test_a_gateway_holding_requests_back_after_a_sorter_was_started_again_is_not_said_to_be_stopped(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    """Measured live (MR6b acceptance): the sorter's engine was ended and started again, and the
    gateway answered the questions that came in the next second with a 503 that asks for a wait
    (a backend that failed is tried again before it is trusted). The sorter was running, and each
    of those questions told a person it was not and to start it in Eugene's console."""
    cooling = (
        "A backend serving this model is not ready yet: Eligible backends are cooling down or "
        "already probing recovery. Try a new request later.. If an engine is still loading its "
        "weights this will clear on its own."
    )
    sorter_world(world)
    ada = start(world)
    keep(ada, *corpus())
    _, found_order = _failing(world, ada)
    world.gateway.rerank_requests.clear()
    world.gateway.rerank_script.append((503, {"error": {"message": cooling}}, {"retry-after": "1"}))
    got = try_question(ada, QUESTION)
    assert got["status"] == "found" and texts(got) == found_order, "the search is whole"
    block = sort_of(got)
    assert block["used"] is False and block["code"] == "error" and block["quiet"] is False
    assert block["message"] == (
        f"Not sorted: Eugene's gateway asked Workbench to wait 1 s before asking the sorter "
        f"{SORTER} again ({cooling}), so the passages are in the order the search found them."
    )
    assert "is not running" not in block["message"] and "Start it" not in block["message"]
    assert block["model"] == SORTER and block["passages"] == 5
    assert sort_of(try_question(ada, QUESTION))["used"] is True, "and the next is sorted"


def test_a_sorter_that_stops_is_noticed_and_the_questions_go_on_in_the_order_found(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    ada = start(world)
    keep(ada, *corpus())
    _, found_order = _failing(world, ada)
    world.gateway.sorters = [sorter_model(SORTER, ready=False)]
    time.sleep(0.1)
    got = try_question(ada, QUESTION)
    block = sort_of(got)
    assert got["status"] == "found" and texts(got) == found_order
    assert block["code"] == "not_running" and block["model"] == SORTER
    assert block["message"] == (
        f"Not sorted: the sorter {SORTER} is not running. Start it in Eugene's console under "
        "Backends."
    )
    world.gateway.sorters = [sorter_model(SORTER)]
    time.sleep(0.1)
    assert sort_of(try_question(ada, QUESTION))["used"] is True, "and it is used again when it runs"


def test_a_sorter_that_takes_over_twice_the_budget_is_given_up_on_and_the_search_is_whole(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    ada = start(world)
    keep(ada, *corpus())
    assert config(world, documentsSortSeconds=0.5).status_code == 200
    _, found_order = _failing(world, ada)
    world.gateway.rerank_requests.clear()
    world.gateway.rerank_delay = 2.5  # it has stopped answering
    started = time.perf_counter()
    got = try_question(ada, QUESTION)
    waited = time.perf_counter() - started
    block = sort_of(got)
    assert 0.9 < waited < 3.0, f"given up on after twice the budget, 1 second ({waited:.1f} s)"
    assert (
        got["status"] == "found"
        and texts(got) == found_order
        and got["mode"] == "meaning and words"
    )
    assert block["code"] == "slow" and block["used"] is False and block["quiet"] is False
    assert block["message"] == (
        f"Not sorted: {SORTER} did not answer within 1 s (twice Sort time, 0.5 s), so the "
        "passages are in the order the search found them."
    )
    world.gateway.rerank_delay = 0.0


# --------------------------------------------------------------------------- #
# in a chat, and under an answer (design call 6)
# --------------------------------------------------------------------------- #


def test_a_chat_is_sent_the_sorted_passages_and_its_search_record_says_so(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    ada = start(world)
    keep(ada, *corpus())
    world.gateway.rerank_prefer = {FEES: 0.99}
    chat = chat_on(ada)
    answer = ask(ada, chat, QUESTION)
    assert answer["status"] == "done"
    _, _, passages = block_of(world)
    assert FEES in passages[0][4], "the model is shown the sorter's first passage first"
    assert [p[1] for p in passages] == [str(n) for n in range(1, len(passages) + 1)]
    record = answer["retrieval"]
    assert record["status"] == "used" and record["mode"] == f"meaning and words, sorted by {SORTER}"
    assert set(record["sort"]) == interface("SortRun")
    assert base_mode(record["mode"]) in literals("SearchRecord", "mode"), "the page knows the mode"
    assert record["sort"]["used"] is True and record["sort"]["model"] == SORTER
    assert record["sort"]["passages"] == 5 and record["sort"]["budgetSeconds"] == 3.0
    assert record["ms"] is not None
    assert record["notes"] == [], "nothing is added under an answer for a sorter that worked"
    sources = [s for s in answer["sources"] if s.get("passage")]
    assert FEES[:20] in sources[0]["snippet"] and sources[0]["rank"] == 1


def test_under_an_answer_the_sources_are_the_same_whether_or_not_the_passages_were_sorted(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    ada = start(world)
    keep(ada, *corpus())
    sorted_ = ask(ada, chat_on(ada), QUESTION)
    assert config(world, documentsSortModel="off").status_code == 200
    plain = ask(ada, chat_on(ada), QUESTION)
    assert sorted_["retrieval"]["sort"]["used"] and not plain["retrieval"]["sort"]["used"]
    assert source_keys(sorted_) == source_keys(plain), (
        "a source names its file, page, headings and rank, as ever"
    )


def test_a_chat_whose_sorter_is_too_slow_is_sent_the_passages_as_found_and_says_why_in_its_record(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    world.gateway.rerank_rate = 1500.0
    ada = start(world)
    keep(ada, *corpus(), long_handbook())
    assert config(world, documentsSortSeconds=0.5).status_code == 200
    answer = ask(ada, chat_on(ada), QUESTION)
    record = answer["retrieval"]
    assert answer["status"] == "done" and record["status"] == "used"
    assert record["mode"] == "meaning and words"
    assert record["sort"]["code"] == "over_budget" and record["sort"]["used"] is False
    assert record["sort"]["message"].startswith(f"Not sorted: {SORTER} sorts about ")
    assert record["notes"] == [], "Sources are unchanged: the reason is in the record, not a note"


def test_with_meaning_not_running_the_words_are_still_sorted_and_the_mode_says_both(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    ada = start(world)
    keep(ada, *corpus())
    world.gateway.search_models = [search_model("mr5-embed", ready=False)]
    time.sleep(0.1)
    got = try_question(ada, QUESTION)
    assert got["mode"] == f"words, sorted by {SORTER}"
    assert got["meaning"]["used"] is False and sort_of(got)["used"] is True
    assert {p["via"] for p in got["passages"]} == {"words"}
    assert all("meaning" not in r and "sorter" in r for r in places(got))


def test_what_one_person_asks_sends_the_sorter_only_their_own_passages(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    ada, bo = start(world, "p-ada"), start(world, "p-bo")
    keep(ada, *corpus())
    two = (
        "# Bo\n\n## One\n\nThe zzbo vessel registry is kept by the clerk.\n\n"
        "## Two\n\nThe zzbo ledger and the berthing fees are the clerk's too.\n"
    )
    keep(bo, ("bo.md", two.encode()))
    assert try_question(ada, QUESTION)["status"] == "found"
    for_ada = json.dumps(world.gateway.sorts())
    assert "Harbour office" in for_ada and "zzbo" not in for_ada
    world.gateway.rerank_requests.clear()
    assert try_question(bo, QUESTION)["status"] == "found"
    for_bo = json.dumps(world.gateway.sorts())
    assert "zzbo" in for_bo and "Harbour office" not in for_bo


def test_one_passage_found_is_not_sent_to_a_sorter_and_says_nothing_of_one(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    ada = start(world)
    keep(ada, ("alone.md", md("The quillwort basin is drained on the first of the month.")))
    got = try_question(ada, "when is the quillwort basin drained?", ways=["words"])
    assert got["status"] == "found" and len(got["passages"]) == 1
    block = sort_of(got)
    assert block["used"] is False and block["code"] == "nothing_to_sort" and block["quiet"] is True
    assert world.gateway.sorts() == [], "one passage has no order to be put in"


def test_a_question_with_nothing_to_search_does_not_ask_a_sorter(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    ada = start(world)
    empty = try_question(ada, QUESTION)
    assert empty["status"] == "no_documents" and sort_of(empty)["code"] == "not_asked"
    keep(ada, *corpus())
    nothing = try_question(ada, "xx yy")
    assert nothing["status"] == "no_words" and sort_of(nothing)["used"] is False
    assert world.gateway.sorts() == [], (
        "a look at the screen measures the sorter; nothing is sorted"
    )


# --------------------------------------------------------------------------- #
# the two settings (design calls 3 and 4; settings never lie)
# --------------------------------------------------------------------------- #


def test_the_two_settings_are_in_the_config_with_their_defaults_and_say_what_they_come_to(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world, SORTER, "second-sorter")
    ada = start(world)
    keep(ada, *corpus())
    fields = schema(world)
    model, seconds = fields["documentsSortModel"], fields["documentsSortSeconds"]
    assert model["label"] == "Result sorter for My documents" and model["category"] == "documents"
    assert model["valueType"] == "string" and model["default"] == "auto"
    assert model["suggestions"] == ["auto", "off", SORTER, "second-sorter"]
    assert model["status"] == {"level": "info", "text": f"In use: {SORTER} (automatic)."}
    assert seconds["label"] == "Sort time (seconds)" and seconds["category"] == "documents"
    assert seconds["valueType"] == "number" and seconds["default"] == 3
    assert (seconds["minimum"], seconds["maximum"]) == (0.5, 120.0)
    assert "twice this" in seconds["description"], "a sort that takes more is given up on"
    again = seconds_status(world)
    assert again["level"] == "info" and again["text"].startswith(f"{SORTER} sorts about ")
    assert again["text"].endswith("within this, so passages are sorted.")
    assert config(world, documentsSortModel="second-sorter").status_code == 200
    assert schema(world)["documentsSortModel"]["status"]["text"] == "In use: second-sorter."
    assert config(world, documentsSortModel="off").status_code == 200
    off = schema(world)
    assert off["documentsSortModel"]["status"]["text"] == (
        "Off: passages are left in the order the search found them."
    )
    assert off["documentsSortSeconds"]["status"]["text"] == (
        "Nothing is sorted now, so this changes nothing yet."
    )


def test_the_budget_setting_says_when_the_sorter_is_too_slow_for_it(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    world.gateway.rerank_rate = 400.0
    ada = start(world)
    keep(ada, *corpus())
    schema(world)  # looking at the settings measures the sorter, behind the look
    over = seconds_status(world)
    assert over["level"] == "warning"
    assert over["text"].startswith(f"{SORTER} sorts about ")
    assert "18 passages of about 300 tokens take about " in over["text"]
    assert over["text"].endswith("over this, so passages are not sorted.")
    assert search_of(ada)["sort"]["sorting"] is False


def test_the_settings_take_what_they_can_and_refuse_what_they_cannot(world: World) -> None:
    good = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    for key, value, ok in [
        ("documentsSortModel", "  ", False),
        ("documentsSortModel", 7, False),
        ("documentsSortModel", True, False),
        ("documentsSortModel", "x" * 300, False),
        ("documentsSortModel", "off", True),
        ("documentsSortModel", "auto", True),
        ("documentsSortModel", "Qwen3-Reranker-0.6B-Q8_0", True),
        ("documentsSortSeconds", 0, False),
        ("documentsSortSeconds", 0.4, False),
        ("documentsSortSeconds", 120.5, False),
        ("documentsSortSeconds", 1e9, False),
        ("documentsSortSeconds", True, False),
        ("documentsSortSeconds", "3", False),
        ("documentsSortSeconds", [3], False),
        ("documentsSortSeconds", 0.5, True),
        ("documentsSortSeconds", 120, True),
        ("documentsSortSeconds", 2.5, True),
        ("documentsSortSeconds", 3, True),
    ]:
        done = config(world, **{key: value}).json()
        assert (done["applied"] == [key]) is ok, (key, value, done)
    config(world, documentsSortModel="mine", documentsSortSeconds=7.5)
    current = httpx.get(f"{world.workbench}/v1/config", headers=good, trust_env=False).json()
    assert current["documentsSortModel"] == "mine" and current["documentsSortSeconds"] == 7.5
    assert (
        config(world, documentsSortModel=None, documentsSortSeconds=None).json()["rejected"] == []
    )
    back = httpx.get(f"{world.workbench}/v1/config", headers=good, trust_env=False).json()
    assert back["documentsSortModel"] == "auto" and back["documentsSortSeconds"] == 3


def test_a_setting_that_was_saved_wrongly_is_the_default_and_never_stops_a_search(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    import sqlite3

    sorter_world(world)
    ada = start(world)
    keep(ada, *corpus())
    with sqlite3.connect(world.data / "workbench.sqlite3") as db:
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("documentsSortSeconds", '"soon"'),
        )
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("documentsSortModel", "7"),
        )
    got = try_question(ada, QUESTION)
    assert got["status"] == "found"
    assert sort_of(got)["budgetSeconds"] == 3.0 and sort_of(got)["setting"] == "auto"


def test_a_screen_is_not_stopped_by_a_sorter_that_cannot_be_looked_at(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from eugene_plexus_workbench.knowledge.sorting import Sorting

    sorter_world(world)
    ada = start(world)
    keep(ada, *corpus())

    async def fell_over(self: Sorting) -> Any:
        raise RuntimeError("fell over")

    monkeypatch.setattr(Sorting, "standing", fell_over)
    monkeypatch.setattr(Sorting, "fields", fell_over)
    head = search_of(ada)
    assert head["sort"] is None, "no word of the sorter, and no failure"
    assert (
        head["mode"] == "meaning and words"
        and head["headline"] == "Searching by meaning and words."
    )
    assert ada.get(f"/api/chats/{ada.new_chat()}/documents").status_code == 200
    fields = schema(world)
    assert "status" not in fields["documentsSortModel"], "the settings page does not fail either"
    assert fields["documentsSortSeconds"]["default"] == 3


def test_the_pages_types_name_the_mode_a_sorted_search_has() -> None:
    """The mode of a search a sorter put in order is `meaning and words, sorted by <sorter>`: the
    three places the page reads a mode say so in their types, so a mode that is not one of the
    plain ones is not a mode the page's types do not know."""
    from .test_saved_page import types

    assert re.search(r"export type SortedMode = `\$\{[^`]*\}, sorted by \$\{string\}`;", types()), (
        "types.ts says what a sorted mode is"
    )
    for name in ("SearchFacts", "TestedQuestion", "SearchRecord"):
        assert re.search(r"^  mode: .*\| SortedMode;$", body(name), re.MULTILINE), name
    assert base_mode("meaning and words, sorted by Qwen3-Reranker, a-b") == "meaning and words"
    assert base_mode("words") == "words"
