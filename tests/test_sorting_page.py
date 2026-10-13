"""The page and the server agree about the result sorter (MR6b, the page side).

Workbench's own API is private and has no schema file, so the page's TypeScript
(`web/src/lib/types.ts`, `documentSorting.ts`) is written by hand. These read it as text and hold
it to what the real server answers with in each state the sorter can be in, as
`test_meaning_page.py` does for search by meaning: a field the server renames, adds or drops, or a
code or a sentence it starts to say, is a failure here and not a blank spot on a screen or a
sentence the page does not have.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from eugene_plexus_workbench.hub import HubError
from eugene_plexus_workbench.knowledge import embedders, sorting

from .conftest import World, sorter_model
from .meaning import (
    config,
    corpus,
    quick,  # noqa: F401
    search_of,
    try_question,
    until,
)
from .saved import ask, chat_on, keep, start
from .sorting import (
    QUESTION,
    SORTER,
    quick_sorting,  # noqa: F401
    sort_of,
    sorter_world,
)
from .test_documents_page import LIB, interface
from .test_saved_page import base_mode, literals, server_text, shape, union
from .test_sorting_api import long_handbook, measured

FIXTURES = Path(__file__).resolve().parent / "fixtures"
#: The page's words for a code, read as text: every code the server can say is a key of its table.
DOCUMENT_SORTING = (LIB / "documentSorting.ts").read_text(encoding="utf-8")
#: A sorter's name as the page's tests show it.
NAMED = "Qwen3-Reranker-0.6B-Q8_0"
#: What a sort of the usual size and an over-budget one are priced at in the shared table.
RATE, BUDGET, PASSAGES, TOKENS = 820.0, 3.0, 18, 5_760
#: Times and speeds the page says as the server does, none of them a half that is exact in a double
#: (where Python goes to the even digit and a browser's `toFixed` goes up).
SECONDS = [
    0.001, 0.0449, 0.45, 0.5, 0.999, 1.0, 1.04, 1.26, 3.0, 6.94, 7.02, 9.96, 10.0, 10.4, 10.6,
    12.4, 59.9, 120.0,
]  # fmt: skip
RATES = [
    5.0, 45.6, 99.6, 100.0, 104.9, 105.1, 124.4, 820.0, 849.9, 1234.5, 4100.0, 18765.0, 123456.7,
]  # fmt: skip


def table_keys(name: str) -> set[str]:
    """The keys of `export const NAME: Record<...> = { key: ..., }` in documentSorting.ts."""
    found = re.search(rf"export const {name}[^=]*= \{{(.*?)\n\}};", DOCUMENT_SORTING, re.DOTALL)
    assert found, f"documentSorting.ts has no table {name}"
    return set(re.findall(r"^  (\w+):", found.group(1), re.MULTILINE))


# --------------------------------------------------------------------------- #
# every sentence and every code the page has words for
# --------------------------------------------------------------------------- #


def sorter_says() -> dict[str, str]:
    """What the server says for each code that has a sentence, as its own functions make it, from
    sample names: the Documents screen's and a tried question's differ only for a sorter that is
    too slow (a usual sort, and the sort of these passages). The page's tests read the same table
    (`tests/fixtures/sort_codes.json`), so the sentences they check are the server's, and the next
    test fails when the server's change."""
    stopped = embedders.SearchModel(NAMED, "local", False)
    cloud = embedders.SearchModel("rerank-v3", "external", True, "Cohere")
    made: dict[str, tuple[str, str | None]] = {}

    def chose(*given: Any) -> tuple[str, str | None]:
        found = sorting.choose(*given)
        return found.code, found.message

    made["off"] = chose([], set(), "off", False)
    made["none_set_up"] = chose([], set(), "auto", False)
    made["not_running"] = chose([stopped], {stopped.id}, "auto", False)
    made["not_listed"] = chose([], set(), "rerank-x", False)
    made["not_sorter"] = chose([], {"gemma-3"}, "gemma-3", False)
    made["outside"] = chose([cloud], {cloud.id}, cloud.id, False)
    made["outside_only"] = chose([cloud], {cloud.id}, "auto", False)
    for code, trouble in {
        "unreachable": HubError("Eugene's gateway could not be reached.", kind="unreachable"),
        "key": HubError("The gateway refused Workbench's key.", kind="key", status=401),
        "limited": HubError("limit", status=429, words="too many requests"),
        "slow": HubError("took too long", kind="timeout", status=504),
        "unusable": HubError(
            "The sorter's answer held 0 scores for 5 passages.", kind="unusable", status=502
        ),
        "refused": HubError("refused", status=400, words="the input is too large"),
        "error": HubError("failed", status=500, words="the engine stopped"),
    }.items():
        made[code] = sorting.problem(trouble, NAMED, BUDGET)
    says: dict[str, str] = {}
    for code, (said, words) in made.items():
        assert said == code, (code, said)
        assert words and words.startswith(sorting.NOT_SORTED), (code, words)
        says[code] = words
    says["over_budget"] = sorting.typical_words(
        NAMED, RATE, sorting.typical_tokens() / RATE, BUDGET
    )
    says["over_budget_question"] = sorting.over_budget_words(
        NAMED, RATE, PASSAGES, TOKENS, TOKENS / RATE, BUDGET
    )
    return says


def test_the_sentences_the_pages_tests_use_are_the_ones_the_server_says() -> None:
    table = json.loads((FIXTURES / "sort_codes.json").read_text(encoding="utf-8"))
    assert table == sorter_says()


def test_a_time_and_a_speed_are_said_the_way_the_server_says_them() -> None:
    """The Documents screen says what a usual sort costs from a time and a speed the server gave,
    and Test a question how long a sort took: in the words the server says them in (`7 s`,
    `0.45 s`, `about 820 tokens a second`). One table, which the page's own test reads too
    (`web/src/lib/documentSorting.test.ts`), so the two never say a figure differently."""
    table = json.loads((FIXTURES / "sort_words.json").read_text(encoding="utf-8"))
    assert len(table["seconds"]) >= 15 and len(table["rates"]) >= 10
    for seconds, words in table["seconds"]:
        assert sorting.seconds_words(seconds) == words, seconds
    for rate, words in table["rates"]:
        assert sorting.rate_words(rate) == words, rate


def codes_said() -> set[str]:
    """Every code the sorter can come to, on the screen or for a question, read off the server's
    own source: which sorter (`choose`), what a failure was (`problem`), the screen's own
    (`standing`), and the question's own (`Retriever._sort`, `sort_block`)."""
    choosing = server_text("knowledge/sorting.py")
    search = server_text("knowledge/search.py")
    said = set(re.findall(r'Choice\(\s*(?:None|[^,]+),\s*"(\w+)"', choosing))
    said |= set(re.findall(r'return\s*\(?\s*"(\w+)",', choosing))
    said |= set(re.findall(r'stand\(\s*"(\w+)"', choosing))
    said |= set(re.findall(r'Verdict\(\s*"(\w+)"', choosing))
    said |= set(re.findall(r'code="(\w+)"', choosing))
    said |= set(re.findall(r'code="(\w+)"', search))
    said |= set(re.findall(r'code="\w+" if \w+ else "(\w+)"', search))
    pair = re.search(r'"code": "(\w+)" if \w+ else "(\w+)"', server_text("saved_api.py"))
    assert pair, "saved_api.py no longer says why a question was not sorted"
    return said | set(pair.groups())


def test_every_code_the_sorter_can_say_is_one_the_page_has_words_for() -> None:
    said = codes_said()
    # The sources really name the codes (a regular expression that finds none checks nothing).
    assert {"ok", "measuring", "over_budget", "off", "none_set_up", "not_running", "error"} <= said
    assert {"limited", "nothing_to_sort", "no_question", "not_asked", "none"} <= said
    assert said == union("SortCode"), sorted(said ^ union("SortCode"))
    # And the page says how each one looks: the table is as long as the list.
    assert table_keys("SORT_TONE") == said


def test_the_codes_that_name_the_console_are_codes_the_server_says() -> None:
    """The page tells a person who is not the owner to ask them, for the codes whose mending is in
    the console; a code it names that the server never says is a sentence that is never shown."""
    listed = re.search(r"SORT_OWNER_FIXES[^=]*= \[(.*?)\];", DOCUMENT_SORTING, re.DOTALL)
    assert listed, "documentSorting.ts has no SORT_OWNER_FIXES"
    named = set(re.findall(r'"(\w+)"', listed.group(1)))
    assert named and named <= codes_said(), sorted(named - codes_said())


# --------------------------------------------------------------------------- #
# the Documents screen, in each state the sorter can be in
# --------------------------------------------------------------------------- #


def test_the_header_has_the_fields_the_page_reads_in_each_state_the_sorter_can_be_in(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    # No sorter listed: quiet, and the mode is as it was.
    sorter_world(world)
    world.gateway.sorters = []
    ada = start(world)
    keep(ada, *corpus())
    head = search_of(ada)
    assert set(head) == interface("SearchFacts"), "the page reads the sorter of the screen"
    none = head["sort"]
    assert set(none) == interface("SortFacts") and none["code"] in union("SortCode")
    assert none["code"] == "none_set_up" and none["quiet"] is True and none["label"] is None
    assert set(none["typical"]) == shape("SortFacts", "typical")
    # One that is being measured: chosen, no sentence yet (the page says what is being done), and
    # nothing it could be priced at. The measuring takes a second, which a screen does not wait for.
    world.gateway.sorters = [sorter_model(SORTER)]
    world.gateway.rerank_delay = 1.0
    seen = until(lambda: (s := search_of(ada)["sort"])["code"] != "none_set_up" and s)
    assert seen["code"] == "measuring" and seen["message"] is None and seen["quiet"] is False
    assert seen["model"] == SORTER, "the sorter that is chosen"
    assert seen["label"] is None, "and not said to be in use while nothing is sorted"
    assert seen["ratePerSecond"] is None and seen["rateFrom"] is None and seen["sorting"] is False
    assert seen["typical"]["seconds"] is None
    # Measured, and too slow for the time allowed (a second for two passages): the server's own
    # sentence with the numbers, and the speed it was measured at, from a quick test.
    over = measured(ada)
    assert set(over) == interface("SortFacts") and over["code"] in union("SortCode")
    assert over["code"] == "over_budget" and over["sorting"] is False and over["quiet"] is False
    assert over["message"].startswith(f"Not sorted: {SORTER} sorts about ")
    assert over["rateFrom"] in literals("SortFacts", "rateFrom") | {None}
    assert over["rateFrom"] == "probe" and over["locality"] in literals("SortFacts", "locality")
    assert over["label"] is None, "chosen, and too slow: not in use"
    # Raised, it is quick enough: nothing to explain, and the label is true.
    assert config(world, documentsSortSeconds=120).status_code == 200
    ok = until(lambda: (s := search_of(ada)["sort"])["code"] == "ok" and s)
    assert ok["sorting"] is True and ok["message"] is None and ok["budgetSeconds"] == 120.0
    assert ok["label"] == f"In use: {SORTER} (automatic)" and ok["locality"] == "local"
    # Stopped: not running, which names the sorter, with no speed to say.
    world.gateway.sorters[0]["x_eugene_plexus"]["ready_backends"] = 0
    stopped = until(lambda: (s := search_of(ada)["sort"])["code"] == "not_running" and s)
    assert stopped["sorting"] is False and stopped["quiet"] is False and stopped["label"] is None
    assert stopped["named"] == SORTER and stopped["message"].startswith("Not sorted: the sorter ")
    for state in (none, seen, over, ok, stopped):
        assert state["code"] in union("SortCode"), state["code"]
        assert (state["locality"] or "local") in literals("SortFacts", "locality")


# --------------------------------------------------------------------------- #
# a question, and a chat's answer
# --------------------------------------------------------------------------- #


def test_a_question_that_was_sorted_has_the_fields_the_page_reads(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    ada = start(world)
    keep(ada, *corpus())
    found = try_question(ada, QUESTION)
    assert set(found) == interface("TestedQuestion"), "the page reads what sorting came to"
    assert found["mode"] == f"meaning and words, sorted by {SORTER}"
    assert base_mode(found["mode"]) in literals("TestedQuestion", "mode")
    block = sort_of(found)
    assert set(block) == interface("SortRun") and block["used"] is True and block["code"] is None
    # The sorter is given the best of both ways; what is sent with the question is chosen from them.
    assert block["seconds"] is not None and block["passages"] >= len(found["passages"])
    # Where the sorter put each passage is one of the places the page knows, never a score.
    for hit in found["passages"]:
        assert set(hit["why"]["ranks"]) <= interface("Places"), hit["why"]["ranks"]
        assert isinstance(hit["why"]["ranks"]["sorter"], int)
    assert "sorter" in interface("Places")


def test_a_question_that_was_not_sorted_says_why_in_a_code_the_page_knows(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    world.gateway.rerank_rate = 1500.0
    ada = start(world)
    keep(ada, *corpus(), long_handbook())
    assert config(world, documentsSortSeconds=0.5).status_code == 200
    over = try_question(ada, QUESTION)
    block = sort_of(over)
    assert set(block) == interface("SortRun") and block["code"] in union("SortCode")
    assert block["code"] == "over_budget" and block["used"] is False and block["quiet"] is False
    assert block["message"].startswith(f"Not sorted: {SORTER} sorts about ")
    assert block["ratePerSecond"] > 0 and block["expectedSeconds"] > block["budgetSeconds"]
    assert over["mode"] == "meaning and words", "not sorted, so not said to be"
    # No place by the sorter for a passage it was never given.
    assert all("sorter" not in hit["why"]["ranks"] for hit in over["passages"])


def test_what_a_chat_keeps_of_sorting_has_the_fields_the_page_reads(
    world: World,
    quick: None,  # noqa: F811
    quick_sorting: None,  # noqa: F811
) -> None:
    sorter_world(world)
    ada = start(world)
    keep(ada, *corpus())
    answer = ask(ada, chat_on(ada), QUESTION)
    record = answer["retrieval"]
    assert set(record) == interface("SearchRecord"), "the page reads what sorting came to"
    assert set(record["sort"]) == interface("SortRun") and record["sort"]["used"] is True
    assert base_mode(record["mode"]) in literals("SearchRecord", "mode")
    assert record["mode"] == f"meaning and words, sorted by {SORTER}"
    assert record["sort"]["code"] is None


def make_the_shared_tables_again() -> None:
    """When the server's words or rounding change on purpose, the tables the page's tests read are
    made again from the server's own functions: `python -m tests.test_sorting_page` (from the
    repo's root, with its venv)."""
    (FIXTURES / "sort_codes.json").write_text(
        json.dumps(sorter_says(), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (FIXTURES / "sort_words.json").write_text(
        json.dumps(
            {
                "seconds": [[s, sorting.seconds_words(s)] for s in SECONDS],
                "rates": [[r, sorting.rate_words(r)] for r in RATES],
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    make_the_shared_tables_again()
