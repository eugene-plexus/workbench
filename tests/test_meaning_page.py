"""The page and the server agree about search by meaning (MR5).

Workbench's own API is private and has no schema file, so the page's TypeScript
(`web/src/lib/types.ts`, `documentMeaning.ts`) is written by hand. These read it as text and hold
it to what the real server answers with in each state it can be in, as `test_saved_page.py` does
for the rest of the Documents screen: a field the server renames, adds or drops, or a code, a
state or a note it starts to say, is a failure here and not a blank spot on a screen or a sentence
the page does not have.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from eugene_plexus_workbench import saved_api
from eugene_plexus_workbench.hub import HubError
from eugene_plexus_workbench.knowledge import embedders, meaning

from .conftest import World, search_model
from .meaning import (
    ARRIVALS,
    EMBEDDER,
    corpus,
    no_models,
    quick,  # noqa: F401
    ready_world,
    search_of,
    try_question,
    until,
)
from .saved import add, ask, by_name, chat_on, keep, listing, start
from .test_documents_page import LIB, interface
from .test_saved_page import base_mode, literals, server_text, union

PARAPHRASE = "who records the boats arriving?"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
#: The page's words for a code, read as text: every code the server can say is a key of this table.
DOCUMENT_MEANING = (LIB / "documentMeaning.ts").read_text(encoding="utf-8")


def table_keys(name: str) -> set[str]:
    """The keys of `export const NAME: Record<...> = { key: ..., }` in documentMeaning.ts."""
    found = re.search(rf"export const {name}[^=]*= \{{(.*?)\n\}};", DOCUMENT_MEANING, re.DOTALL)
    assert found, f"documentMeaning.ts has no table {name}"
    return set(re.findall(r"^  (\w+):", found.group(1), re.MULTILINE))


# --------------------------------------------------------------------------- #
# every code the header can say
# --------------------------------------------------------------------------- #


def server_says() -> dict[str, dict[str, Any]]:
    """What the header says for each code, as the server's own functions make it: the headline,
    the detail and what a person can do, from sample names. The page's tests read the same table
    (`tests/fixtures/search_codes.json`), so the sentences they check are the server's, and the
    next test fails when the server's change."""
    qwen = embedders.SearchModel("Qwen3-Embedding-0.6B-Q8_0", "local", False)
    cloud = embedders.SearchModel("text-embedding-3-small", "external", True, "OpenAI")
    made: dict[str, tuple[str, str | None]] = {}

    def chose(*given: Any) -> tuple[str, str | None]:
        found = embedders.choose(*given)
        return found.code, found.message

    made["ok"] = ("ok", None)
    made["none_set_up"] = chose([], set(), "auto", False)
    made["not_running"] = chose([qwen], {qwen.id}, "auto", False)
    made["not_listed"] = chose([], set(), "nomic-embed", False)
    made["not_search_model"] = chose([], {"gemma-3"}, "gemma-3", False)
    made["outside"] = chose([cloud], {cloud.id}, cloud.id, False)
    made["outside_only"] = chose([cloud], {cloud.id}, "auto", False)
    for code, trouble in {
        "unreachable": HubError("Eugene's gateway could not be reached.", kind="unreachable"),
        "key": HubError("The gateway refused Workbench's key.", kind="key", status=401),
        "limited": HubError("limit", status=429, words="too many requests"),
        "slow": HubError("The gateway took too long to answer.", kind="timeout"),
        "refused": HubError("refused", status=400, words="the input is empty"),
        "error": HubError("failed", status=500, words="the engine stopped"),
    }.items():
        made[code] = meaning.problem(trouble, qwen.id)
    built = embedders.identity_for(qwen.id, 1024)
    for code, other in {
        "changed": meaning.Seen("nomic-embed-text-v1.5", 1024, 0.0),
        "dimension": meaning.Seen(qwen.id, 768, 0.0),
    }.items():
        mismatch = meaning.Meaning._mismatch(built, other)
        assert mismatch is not None
        made[code] = mismatch
    made["damaged"] = ("damaged", meaning.DAMAGED.format(why="not JSON"))
    says: dict[str, dict[str, Any]] = {}
    for code, (said, detail) in made.items():
        assert said == code, (code, said)
        says[code] = {
            "headline": meaning.headline_for(code, detail),
            # `Meaning.status`: nothing more when meaning is in use, the paused sentence for a
            # model that changed, and nothing for a record that cannot be read.
            "detail": (
                None
                if code in ("ok", "damaged")
                else meaning.PAUSED_HINT
                if code in ("changed", "dimension")
                else detail
            ),
            "actions": [
                *(["rebuild"] if code in meaning.REBUILD_CODES else []),
                *(["get_search_model"] if code == "none_set_up" else []),
            ],
        }
    gone = saved_api.search_facts(None)
    says["no_gateway"] = {"headline": gone["headline"], "detail": gone["detail"], "actions": []}
    return says


def test_the_sentences_the_pages_tests_use_are_the_ones_the_server_says() -> None:
    table = json.loads((FIXTURES / "search_codes.json").read_text(encoding="utf-8"))
    assert table == server_says()


def codes_said() -> set[str]:
    """Every code the header can come to, read off the server's own source."""
    meaning = server_text("knowledge/meaning.py")
    choosing = server_text("knowledge/embedders.py")
    said = set(re.findall(r'return "(\w+)",', meaning))  # a refused request; a changed model
    said |= set(re.findall(r'code, detail = "(\w+)"', meaning))  # an unreadable record
    said |= set(re.findall(r'Choice\(\s*(?:None|[^,]+),\s*"(\w+)"', choosing))  # which model
    said |= set(re.findall(r'"code": "(no_gateway)"', server_text("saved_api.py")))
    return said


def test_every_code_the_header_can_say_is_one_the_page_has_words_for() -> None:
    said = codes_said()
    # The sources really name the codes (a regular expression that finds none checks nothing).
    assert {"ok", "none_set_up", "not_running", "changed", "limited", "damaged"} <= said
    assert said == union("SearchCode"), sorted(said ^ union("SearchCode"))
    # And the page says how each one looks: the table is as long as the list.
    assert table_keys("SEARCH_TONE") == said


def test_every_action_the_header_offers_is_one_the_page_has_a_button_or_a_link_for() -> None:
    source = server_text("knowledge/meaning.py")
    offered = set(re.findall(r'actions\.append\("(\w+)"\)', source))
    assert offered == union("SearchAction"), sorted(offered ^ union("SearchAction"))


def test_the_codes_that_name_the_console_are_codes_the_server_says() -> None:
    """The page tells a person who is not the owner to ask them, for the codes whose mending is in
    the console; a code it names that the server never says is a sentence that is never shown."""
    listed = re.search(r"OWNER_FIXES[^=]*= \[(.*?)\];", DOCUMENT_MEANING, re.DOTALL)
    assert listed, "documentMeaning.ts has no OWNER_FIXES"
    named = set(re.findall(r'"(\w+)"', listed.group(1)))
    assert named and named <= codes_said(), sorted(named - codes_said())


# --------------------------------------------------------------------------- #
# the header, and each document, with a search model in use
# --------------------------------------------------------------------------- #


def test_the_header_has_the_fields_the_page_reads_down_to_each_number(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world, "Qwen3-Embedding-0.6B-Q8_0")
    ada = start(world)
    keep(ada, *corpus())
    found = listing(ada)
    head = found["search"]
    assert set(head) == interface("SearchFacts")
    assert head["code"] == "ok" and base_mode(head["mode"]) in literals("SearchFacts", "mode")
    assert set(head["model"]) == interface("SearchModelView")
    assert set(head["model"]["prefixes"]) == interface("SearchPrefixes")
    assert set(head["adding"]) == interface("AddingView")
    assert head["adding"]["state"] in literals("AddingView", "state")
    assert head["model"]["locality"] in literals("SearchModelView", "locality")
    assert head["model"]["label"] == "In use: Qwen3-Embedding-0.6B-Q8_0 (automatic)"
    # A model the table knows has a floor, and its prefixes are known: what the page says of them.
    assert head["model"]["floor"] == embedders.QWEN3_FLOOR and head["model"]["prefixes"]["known"]
    doc = found["documents"][0]
    assert set(doc["meaning"]) == interface("DocumentMeaning")
    assert doc["meaning"]["state"] in union("MeaningState")
    totals = found["totals"]
    assert totals["embedded"] == head["adding"]["embedded"] and totals["owed"] == 0
    # A chat's Documents says the same, from the same look.
    chat = ada.get(f"/api/chats/{ada.new_chat()}/documents").json()
    assert set(chat["search"]) == interface("SearchFacts")
    assert set(chat["search"]["model"]) == interface("SearchModelView")


def test_a_time_is_said_the_way_the_server_says_it() -> None:
    """The header's Rebuild says how long it takes from seconds the server gave, and each row
    says its time left in the server's words: one table, which the page's own test reads too
    (`web/src/lib/documentMeaning.test.ts`), so the two never differ by a minute at a half."""
    table = json.loads((FIXTURES / "duration_words.json").read_text(encoding="utf-8"))
    assert len(table) >= 20
    for seconds, words in table:
        assert embedders.duration_words(seconds) == words, seconds


def test_a_model_the_table_does_not_know_says_so_in_the_words_the_page_shows() -> None:
    """The sentence is the server's, shown as it came, so it must not be one the page has to
    explain: it is a sentence, with no field the page would have to turn into one."""
    said = embedders.unknown_prefixes(embedders.identity_for("some-other-model", 8))
    assert said is not None and "does not know which prefixes" in said


def test_a_header_that_is_paused_offers_rebuild_with_the_fields_the_page_reads(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world, "model-a")
    world.gateway.embed_dims = {"model-a": 24, "model-b": 12}
    ada = start(world)
    keep(ada, *corpus())
    world.gateway.search_models = [search_model("model-b")]
    head = until(lambda: (h := search_of(ada))["code"] == "changed" and h)
    assert set(head) == interface("SearchFacts")
    assert head["actions"] == ["rebuild"] and set(head["rebuild"]) == interface("RebuildView")
    made = ada.post("/api/documents/meaning/rebuild")
    assert made.status_code == 200, made.text
    assert set(made.json()) == interface("MeaningRebuilt")
    assert set(made.json()["search"]) == interface("SearchFacts")


# --------------------------------------------------------------------------- #
# each state a document's meaning can be in
# --------------------------------------------------------------------------- #


def test_a_documents_meaning_goes_through_every_state_the_page_has_words_for_but_the_limit(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    no_models(world)
    ada = start(world)
    done = keep(ada, *corpus())
    # Read while no search model ran: found by words only, and the page says why.
    states = {d["meaning"]["state"] for d in done["documents"]}
    heads = {done["search"]["adding"]["state"]}
    assert states == {"words_only"}
    assert all(d["meaning"]["message"] is None for d in done["documents"])
    # A search model starts, and its answers are held: each document is seen being given meaning.
    # Held before the model is listed: a worker that wakes at once could otherwise give every
    # document its meaning before the hold, and "adding" would never be seen (Windows CI).
    gate = threading.Event()
    world.gateway.embed_gate = gate
    try:
        ready_world(world)
        until(lambda: any(d["meaning"]["state"] == "adding" for d in listing(ada)["documents"]))
        now = listing(ada)
        states |= {d["meaning"]["state"] for d in now["documents"]}
        heads.add(now["search"]["adding"]["state"])
        adding = next(d for d in now["documents"] if d["meaning"]["state"] == "adding")
        assert adding["meaning"]["message"].startswith("Ready for words. Adding meaning: ")
        # The search model stops, and what was being added waits, and says what for.
        entry = world.gateway.search_models[0]
        entry["x_eugene_plexus"]["ready_backends"] = 0
        until(lambda: search_of(ada)["code"] == "not_running")
        now = listing(ada)
        states |= {d["meaning"]["state"] for d in now["documents"]}
        heads.add(now["search"]["adding"]["state"])
        waiting = next(d for d in now["documents"] if d["meaning"]["state"] == "waiting")
        assert waiting["meaning"]["message"].startswith("Ready for words. Meaning is waiting: ")
        entry["x_eugene_plexus"]["ready_backends"] = 1
    finally:
        gate.set()
    until(lambda: all(d["meaning"]["state"] == "ready" for d in listing(ada)["documents"]), 60)
    states.add("ready")
    assert states == {"words_only", "adding", "waiting", "ready"}
    assert states <= union("MeaningState") and heads <= literals("AddingView", "state")
    assert heads == {"idle", "working", "waiting"}


def test_the_key_at_its_limit_is_a_state_the_page_has_words_for(world: World) -> None:
    # No short memories here: the request the gateway refuses must be the worker's.
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus())
    world.gateway.embed_script.append(
        (429, {"error": {"message": "concurrency limit: 2 at once"}}, {"retry-after": "2"})
    )
    later = b"# One\n\nThe lamp room is cleaned on Sundays.\n\n# Two\n\nThe stairs are steep.\n"
    assert add(ada, ("later.md", later)).status_code == 201
    head = until(lambda: (h := search_of(ada))["adding"]["state"] == "limited" and h)
    row = by_name(ada, "later.md")
    assert row["meaning"]["state"] == "limited", row["meaning"]
    assert row["meaning"]["state"] in union("MeaningState")
    assert head["adding"]["state"] in literals("AddingView", "state")
    assert {"ready", "words_only", "adding", "waiting", "limited"} == union("MeaningState")
    assert {"idle", "working", "waiting", "limited"} == literals("AddingView", "state")
    until(lambda: by_name(ada, "later.md")["state"] == "ready", seconds=30)


# --------------------------------------------------------------------------- #
# a question, and a chat's answer
# --------------------------------------------------------------------------- #


def test_a_passage_found_by_meaning_has_the_fields_the_page_reads(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus())
    found = try_question(ada, PARAPHRASE)
    assert found["status"] == "found" and found["mode"] == "meaning and words"
    assert set(found["meaning"]) == interface("MeaningRun") and found["meaning"]["used"] is True
    assert found["meaning"]["model"] == EMBEDDER
    by_meaning = [p for p in found["passages"] if p["via"] == "meaning"]
    assert by_meaning and any(ARRIVALS in p["text"] for p in by_meaning)
    for hit in found["passages"]:
        assert hit["via"] in union("Via") and set(hit["why"]["ranks"]) <= interface("Places")
        assert hit["why"]["via"] == hit["via"]
    # Found by meaning alone, it has none of the words, a place in meaning's list, and a closeness.
    first = by_meaning[0]
    assert first["why"]["terms"] == [] and "meaning" in first["why"]["ranks"]
    assert isinstance(first["why"]["similarity"], float)
    # One way at a time: the page asks for it by its name, and the server answers by only it.
    words = try_question(ada, PARAPHRASE, ways=["words"])
    assert words["mode"] == "words" and words["meaning"]["code"] == "not_asked"
    only = try_question(ada, PARAPHRASE, ways=["meaning"])
    assert only["mode"] == "meaning" and {p["via"] for p in only["passages"]} == {"meaning"}
    assert union("Way") == {"words", "meaning"}


def test_what_a_chat_keeps_of_meaning_has_the_fields_the_page_reads(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus())
    answer = ask(ada, chat_on(ada), PARAPHRASE)
    record = answer["retrieval"]
    assert set(record["meaning"]) == interface("MeaningRun") and record["meaning"]["used"] is True
    assert base_mode(record["mode"]) in literals("SearchRecord", "mode")
    passages = [s for s in answer["sources"] if s.get("passage")]
    assert passages and {s["via"] for s in passages} <= union("Via")


def test_a_note_about_meaning_in_an_answer_is_one_of_the_codes_the_page_knows(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus())
    world.gateway.search_models[0]["x_eugene_plexus"]["ready_backends"] = 0
    until(lambda: search_of(ada)["code"] == "not_running")
    answer = ask(ada, chat_on(ada), "pantry flour soup")
    record = answer["retrieval"]
    notes = [n for n in record["notes"] if n["code"].startswith("meaning_")]
    assert notes, record["notes"]
    for note in notes:
        # `meaning_not_running`: the page shows it in its own words, whatever the rest of the code.
        assert note["code"].removeprefix("meaning_") in union("SearchCode") | {"unusable"}
        assert note["message"].startswith("Searching by words only: ")
    assert record["mode"] == "words" and record["meaning"]["used"] is False


def make_the_shared_tables_again() -> None:
    """When the server's words or rounding change on purpose, the tables the page's tests read are
    made again from the server's own functions: `python -m tests.test_meaning_page` (from the
    repo's root, with its venv). The times to say are the ones already in the table."""
    (FIXTURES / "search_codes.json").write_text(
        json.dumps(server_says(), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    path = FIXTURES / "duration_words.json"
    seconds = [row[0] for row in json.loads(path.read_text(encoding="utf-8"))]
    path.write_text(
        json.dumps([[s, embedders.duration_words(s)] for s in seconds], indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    make_the_shared_tables_again()
