"""Search by meaning (MR5), through a real Workbench over a fake Eugene: documents become
searchable by words first and meaning catches up; a question with none of a passage's words
finds it by what it says; a stopped, changed or limited search model is named and the words
search goes on; a rebuild never mixes two models' numbers; and what the Documents screen
and the chat are told is read from a live look, never assumed.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from eugene_plexus_workbench.knowledge import embedders

from .conftest import ADMIN_TOKEN, World, search_model
from .meaning import (
    ARRIVALS,
    EMBEDDER,
    KITCHEN,
    LEDGER,
    ODD,
    RARE,
    asked_for_batches,
    asked_for_questions,
    built_with,
    config,
    corpus,
    filler,
    listing,
    no_models,
    quick,  # noqa: F401
    ready_world,
    search_of,
    try_question,
    until,
    vectors_of,
)
from .saved import add, ask, by_name, chat_on, keep, last, md, raw, start, user_text
from .test_saved_scopes import (
    attach,
    choose_search,
    long_survey,
    search_state,
    wait_searchable,
)

PARAPHRASE = "who records the boats arriving?"
WORDS_ONLY = "Searching by words only. No search model is running."


def big_document(sections: int = 80) -> bytes:
    return "\n\n".join(
        f"# Part {n}\n\nParagraph {n} about the harbour and its vessels." for n in range(sections)
    ).encode()


def total_passages(person: Any) -> int:
    return int(listing(person)["totals"]["passages"])


# --------------------------------------------------------------------------- #
# keyword first, meaning catches up
# --------------------------------------------------------------------------- #


def test_a_document_is_found_by_words_at_once_then_meaning_catches_up_and_it_is_ready(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    world.gateway.embed_delay = 0.25
    ada = start(world)
    assert add(ada, ("big.md", big_document(), "text/markdown")).status_code == 201
    rows: list[dict[str, Any]] = []
    found_while_adding: list[dict[str, Any]] = []
    end = time.perf_counter() + 90
    while time.perf_counter() < end:
        doc = by_name(ada, "big.md")
        rows.append(doc)
        if doc["state"] == "embedding" and len(found_while_adding) < 1:
            found_while_adding.append(try_question(ada, "paragraph harbour vessels"))
        if doc["state"] == "ready":
            break
        time.sleep(0.02)
    states = [d["state"] for d in rows]
    assert states[-1] == "ready", states[-5:]
    order = [s for i, s in enumerate(states) if i == 0 or s != states[i - 1]]
    assert order[-3:] == ["searchable", "embedding", "ready"], order
    assert "queued" in order[:2] or order[0] in ("reading", "searchable"), order
    first = next(d for d in rows if d["state"] == "searchable")
    assert first["embedded"] == 0 and first["meaning"]["state"] == "adding"
    assert first["meaning"]["etaSeconds"] is None, "nothing measured yet: no time promised"
    assert first["meaning"]["message"] == "Ready for words. Adding meaning: 0 of 80 passages."
    middle = [d for d in rows if d["state"] == "embedding"]
    counts = [d["embedded"] for d in middle]
    assert counts == sorted(counts) and 0 < counts[0] < 80, "n of N goes up and never down"
    assert all(d["meaning"]["state"] == "adding" for d in middle)
    assert all(
        d["meaning"]["message"].startswith("Ready for words. Adding meaning: ")
        and " of 80 passages" in d["meaning"]["message"]
        for d in middle
    )
    timed = [d for d in middle if d["meaning"]["etaSeconds"] is not None]
    assert timed and all(0 < d["meaning"]["etaSeconds"] < 120 for d in timed)
    assert "left." in timed[-1]["meaning"]["message"], "the time comes from what was measured"
    done = rows[-1]
    assert (done["embedded"], done["passages"], done["meaning"]["state"]) == (80, 80, "ready")
    assert done["meaning"]["message"] is None
    # It was found by words while meaning was still being added.
    [seen] = found_while_adding
    assert seen["status"] == "found" and {p["via"] for p in seen["passages"]} <= {"words", "both"}
    assert all(p["why"]["terms"] for p in seen["passages"]), "every one has the words asked for"
    totals = listing(ada)["totals"]
    assert (totals["embedded"], totals["owed"], totals["byState"]["ready"]) == (80, 0, 1)
    head = search_of(ada)
    assert head["adding"]["state"] == "idle" and head["adding"]["embedded"] == 80


def test_a_question_with_none_of_a_passages_words_finds_it_by_meaning_where_words_find_nothing(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    done = keep(ada, *corpus())
    assert {d["state"] for d in done["documents"]} == {"ready"}
    head = search_of(ada)
    assert head["headline"] == "Searching by meaning and words." and head["code"] == "ok"
    assert head["mode"] == "meaning and words" and head["meaning"] and head["words"]
    words = try_question(ada, PARAPHRASE, ways=["words"])
    assert (
        words["status"] == "nothing_matched"
        and words["mode"] == "words"
        and words["passages"] == []
    )
    both = try_question(ada, PARAPHRASE)
    assert both["status"] == "found" and both["mode"] == "meaning and words"
    texts = [p["text"] for p in both["passages"]]
    assert any(ARRIVALS in t for t in texts) and any(LEDGER in t for t in texts)
    assert {p["via"] for p in both["passages"]} == {"meaning"}
    top = both["passages"][0]
    assert top["why"]["ranks"] == {"meaning": 1} and top["why"]["terms"] == []
    assert 0.0 < top["why"]["similarity"] <= 1.0, "how near the question, on its own scale"
    assert both["meaning"]["used"] is True and both["meaning"]["model"] == EMBEDDER
    assert both["meaning"]["code"] is None and both["meaning"]["best"] == top["why"]["similarity"]


def test_a_passage_only_the_words_find_and_one_only_meaning_finds_both_appear_beside_each_other(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    registry = "Ship records are kept in the registry by the clerk on duty."
    keep(ada, *corpus(), filler(), ("registry.md", md(registry, title="Registry")))
    question = f"ship records {RARE}"
    got = try_question(ada, question, passages=10)
    by_text = {p["text"]: p for p in got["passages"]}
    only_words = next(p for t, p in by_text.items() if ODD in t)
    only_meaning = next(p for t, p in by_text.items() if LEDGER in t)
    both = next(p for t, p in by_text.items() if registry in t)
    assert only_words["via"] == "words" and only_words["why"]["terms"] == [RARE]
    assert only_meaning["via"] == "meaning" and only_meaning["why"]["terms"] == []
    assert both["via"] == "both" and both["why"]["ranks"].keys() == {"words", "meaning"}
    assert both["rank"] < only_words["rank"], "found by both comes before found by one"


def test_a_run_that_names_its_ways_searches_by_only_those(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus(), filler())
    meaning = try_question(ada, f"ship records {RARE}", ways=["meaning"], passages=10)
    assert meaning["mode"] == "meaning" and {p["via"] for p in meaning["passages"]} == {"meaning"}
    assert all(RARE not in p["text"] for p in meaning["passages"])
    words = try_question(ada, f"ship records {RARE}", ways=["words"], passages=10)
    assert words["mode"] == "words" and {p["via"] for p in words["passages"]} == {"words"}
    assert any(ODD in p["text"] for p in words["passages"])
    assert words["meaning"]["used"] is False and words["meaning"]["code"] == "not_asked"
    for bad in ([], ["words", "meaning", "words"], ["both"], "words"):
        refused = ada.post("/api/documents/test", json={"question": "x", "ways": bad})
        assert refused.status_code == 422, bad


# --------------------------------------------------------------------------- #
# a stopped, changed or limited search model
# --------------------------------------------------------------------------- #


def test_stopping_the_search_model_mid_import_names_it_and_words_search_goes_on_and_it_resumes(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    world.gateway.embed_delay = 0.2
    ada = start(world)
    assert add(ada, ("big.md", big_document(), "text/markdown")).status_code == 201
    until(lambda: by_name(ada, "big.md")["embedded"] >= 16)
    entry = world.gateway.search_models[0]
    entry["x_eugene_plexus"]["ready_backends"] = 0  # the search model stops
    head = until(lambda: (h := search_of(ada))["code"] == "not_running" and h)
    assert head["headline"] == WORDS_ONLY and head["meaning"] is False and head["mode"] == "words"
    assert head["detail"] == (
        f"Searching by words only: the search model {EMBEDDER} is not running. "
        "Start it in Eugene's console under Backends."
    )
    assert head["adding"]["state"] == "waiting"
    assert head["adding"]["message"] == (
        f"Meaning is waiting: the search model {EMBEDDER} is not running. "
        "Start it in Eugene's console under Backends."
    )
    stuck = by_name(ada, "big.md")
    assert stuck["state"] == "embedding" and stuck["meaning"]["state"] == "waiting"
    assert stuck["meaning"]["message"].startswith(
        "Ready for words. Meaning is waiting: the search model"
    )
    # Words search goes on, and says that meaning did not run, with why.
    got = try_question(ada, "paragraph harbour vessels")
    assert got["status"] == "found" and {p["via"] for p in got["passages"]} == {"words"}
    assert got["mode"] == "words" and got["meaning"]["used"] is False
    assert got["meaning"]["code"] == "not_running" and got["meaning"]["quiet"] is False
    assert "is not running" in got["meaning"]["message"]
    before = by_name(ada, "big.md")["embedded"]
    time.sleep(1.2)
    assert by_name(ada, "big.md")["embedded"] == before, "nothing is added while it is stopped"
    entry["x_eugene_plexus"]["ready_backends"] = 1  # and it is back
    until(lambda: by_name(ada, "big.md")["state"] == "ready", seconds=60)
    assert by_name(ada, "big.md")["embedded"] == 80, "the import resumed where it stopped"
    assert search_of(ada)["code"] == "ok"
    assert len(vectors_of(world)) == 80


def test_a_search_model_that_changed_pauses_meaning_offers_rebuild_and_never_mixes_spaces(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world, "model-a")
    world.gateway.embed_dims = {"model-a": 24, "model-b": 12}
    ada = start(world)
    keep(ada, *corpus())
    built = built_with(world)
    assert built is not None and built["model"] == "model-a" and built["dim"] == 24
    before = vectors_of(world)
    assert {size for _, size in before} == {96} and len(before) == total_passages(ada)
    # Another search model is started, and the first is gone.
    world.gateway.search_models = [search_model("model-b")]
    head = until(lambda: (h := search_of(ada))["code"] == "changed" and h)
    assert head["headline"] == (
        "Search by meaning is paused: this search was built with model-a and model-b is running now."
    )
    assert head["meaning"] is False and head["mode"] == "words" and head["actions"] == ["rebuild"]
    assert "searched by words until you choose Rebuild" in head["detail"]
    assert head["model"]["builtWith"] == "model-a" and head["model"]["resolved"] == "model-b"
    assert head["rebuild"]["passages"] == len(before) and head["rebuild"]["model"] == "model-b"
    # Words search goes on, and meaning is said to be paused: no vector of model-b is used.
    got = try_question(ada, "pantry flour soup")
    assert got["status"] == "found" and {p["via"] for p in got["passages"]} == {"words"}
    assert got["meaning"]["used"] is False and got["meaning"]["code"] == "changed"
    # A document added while paused is found by words, and gets no vector from the new model.
    assert (
        add(
            ada, ("later.md", md("The lamp room is cleaned on Sundays.", title="Later"))
        ).status_code
        == 201
    )
    until(lambda: by_name(ada, "later.md")["state"] == "searchable")
    time.sleep(0.8)
    assert (
        by_name(ada, "later.md")["state"] == "searchable"
        and by_name(ada, "later.md")["embedded"] == 0
    )
    assert by_name(ada, "later.md")["meaning"]["state"] == "waiting"
    assert by_name(ada, "later.md")["meaning"]["message"] == (
        "Ready for words. Meaning is paused until you choose Rebuild."
    )
    assert vectors_of(world) == before, "not one number of the new model is in the index"
    assert built_with(world)["model"] == "model-a"  # type: ignore[index]
    # Rebuild: every vector of the old model goes, and meaning is added again with the new one.
    made = ada.post("/api/documents/meaning/rebuild")
    assert made.status_code == 200, made.text
    assert made.json()["passages"] == total_passages(ada)
    until(lambda: all(d["state"] == "ready" for d in listing(ada)["documents"]), seconds=60)
    after = vectors_of(world)
    assert {size for _, size in after} == {48} and len(after) == total_passages(ada)
    assert built_with(world)["model"] == "model-b" and built_with(world)["dim"] == 12  # type: ignore[index]
    assert search_of(ada)["code"] == "ok" and search_of(ada)["meaning"] is True
    again = try_question(ada, PARAPHRASE)
    assert again["status"] == "found" and {p["via"] for p in again["passages"]} == {"meaning"}


def test_the_same_model_at_another_size_is_said_and_refused_too(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus())
    world.gateway.embed_dims = {EMBEDDER: 18}
    head = until(lambda: (h := search_of(ada))["code"] == "dimension" and h)
    assert head["headline"] == (
        f"Search by meaning is paused: {EMBEDDER} now gives 18 numbers for each passage, "
        "and this search was built with 24."
    )
    assert head["actions"] == ["rebuild"] and head["meaning"] is False


def test_the_key_at_its_limit_backs_off_as_the_gateway_asks_and_carries_on_by_itself(
    world: World,
) -> None:
    # No short memories here: a screen asking which model answers must not be the request that
    # the gateway refuses; it is the worker's.
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus())
    world.gateway.embed_script.append(
        (429, {"error": {"message": "concurrency limit: 2 at once"}}, {"retry-after": "1"})
    )
    later = b"# One\n\nThe lamp room is cleaned on Sundays.\n\n# Two\n\nThe stairs are steep.\n"
    assert add(ada, ("later.md", later)).status_code == 201
    head = until(lambda: (h := search_of(ada))["adding"]["state"] == "limited" and h)
    message = head["adding"]["message"]
    assert "slow right now" in message and "at its limit (concurrency limit: 2 at once)" in message
    assert "carry on by itself" in message
    assert "raise the key's limits in Eugene's console" in message
    until(lambda: by_name(ada, "later.md")["state"] == "ready", seconds=30)
    assert search_of(ada)["adding"]["state"] == "idle"
    asked = [r["at"] for r in world.gateway.embed_requests if len(r["body"]["input"]) > 1]
    assert asked[-1] - asked[-2] >= 0.9, "it waited the second the gateway asked for"


def test_a_passage_too_long_for_the_search_model_is_skipped_with_its_words_and_the_rest_are_kept(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    world.gateway.embed_refuse = "TOOLONG"
    ada = start(world)
    text = (
        b"# One\n\nThe lamp room is cleaned on Sundays.\n\n"
        b"# Two\n\nTOOLONG passage that the search model will not take.\n\n"
        b"# Three\n\nThe harbour master keeps the ledger.\n"
    )
    done = keep(ada, ("lamps.md", text, "text/markdown"))
    doc = next(d for d in done["documents"] if d["name"] == "lamps.md")
    assert doc["state"] == "ready" and doc["embedded"] == 3 and doc["passages"] == 3
    assert doc["meaning"]["refused"] == 1 and doc["meaning"]["state"] == "ready"
    [note] = doc["meaning"]["notes"]
    assert note.startswith("The search model refused one passage (") and note.endswith(
        "skipped lamps.md."
    )
    assert "it said: input (1631 tokens) is too large to process" in note
    sizes = sorted(size for _, size in vectors_of(world))
    assert sizes == [0, 96, 96], "the refused one is marked, and is neither retried nor searched"
    found = try_question(ada, "TOOLONG passage")
    assert any("TOOLONG" in p["text"] for p in found["passages"]), "still found by its words"


def test_a_search_model_that_gives_numbers_that_are_not_numbers_is_never_stored(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    world.gateway.embed_nan = "Paragraph 1 "
    ada = start(world)
    assert add(ada, ("big.md", big_document(4), "text/markdown")).status_code == 201
    head = until(lambda: (h := search_of(ada))["adding"]["state"] == "waiting" and h)
    assert "gave numbers Workbench cannot use" in head["adding"]["message"]
    assert vectors_of(world) == [] and by_name(ada, "big.md")["state"] == "searchable"
    world.gateway.embed_nan = None
    until(lambda: by_name(ada, "big.md")["state"] == "ready", seconds=40)


# --------------------------------------------------------------------------- #
# who may be asked
# --------------------------------------------------------------------------- #


def test_no_text_goes_to_a_search_model_outside_the_machines_unless_it_is_named_and_allowed(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    no_models(world)
    world.gateway.search_models = [
        search_model("cloud-embed", locality="external", owned_by="openai")
    ]
    ada = start(world)
    from .saved import settings

    settings(world, documentsEmbedRequestsPerMinute=6000)
    keep(ada, *corpus())
    head = search_of(ada)
    assert head["code"] == "outside_only" and head["meaning"] is False
    assert "the owner has not allowed sending your documents' text there" in head["detail"]
    assert world.gateway.embed_requests == [], "auto never picks it"
    assert all(d["state"] == "ready" for d in listing(ada)["documents"]), "words only, and ready"
    assert config(world, documentsExternalEmbedding=True).status_code == 200
    assert search_of(ada)["code"] == "outside_only", "allowed, but automatic never picks one"
    assert world.gateway.embed_requests == []
    assert (
        config(
            world, documentsEmbeddingModel="cloud-embed", documentsExternalEmbedding=False
        ).status_code
        == 200
    )
    named = until(lambda: (h := search_of(ada))["code"] == "outside" and h)
    assert named["detail"] == (
        "Searching by words only: cloud-embed runs on openai, outside your machines, and the "
        "owner has not allowed sending your documents' text there."
    )
    assert world.gateway.embed_requests == [], "named, and still not allowed: nothing sent"
    assert config(world, documentsExternalEmbedding=True).status_code == 200
    until(
        lambda: all(d["state"] == "ready" and d["embedded"] > 0 for d in listing(ada)["documents"])
    )
    assert {r["body"]["model"] for r in world.gateway.embed_requests} == {"cloud-embed"}
    on = search_of(ada)
    assert (
        on["code"] == "ok"
        and on["model"]["locality"] == "external"
        and on["model"]["runsOn"] == "openai"
    )
    assert on["model"]["allowExternal"] is True


def test_a_question_is_asked_of_the_search_model_the_owner_allowed_and_no_other(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world, "mine", "other")
    ada = start(world)
    keep(ada, *corpus())
    try_question(ada, PARAPHRASE)
    assert {r["body"]["model"] for r in world.gateway.embed_requests} == {"mine"}, (
        "the first that runs here"
    )
    assert config(world, documentsEmbeddingModel="other").status_code == 200
    head = until(lambda: (h := search_of(ada))["code"] == "changed" and h)
    assert head["model"]["setting"] == "other" and head["model"]["automatic"] is False


# --------------------------------------------------------------------------- #
# what a person is told, read from a live look
# --------------------------------------------------------------------------- #


def test_with_no_search_model_documents_are_ready_by_words_and_the_header_says_where_to_get_one(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    no_models(world)
    ada = start(world)
    done = keep(ada, *corpus())
    assert {d["state"] for d in done["documents"]} == {"ready"}
    head = done["search"]
    assert head["headline"] == WORDS_ONLY and head["code"] == "none_set_up"
    assert head["detail"] == "Searching by words only: no search model is set up."
    assert head["actions"] == ["get_search_model"] and head["meaning"] is False
    assert head["model"]["resolved"] is None and head["model"]["setting"] == "auto"
    assert all(d["meaning"]["state"] == "words_only" for d in done["documents"])
    got = try_question(ada, "pantry flour soup")
    assert got["status"] == "found" and got["meaning"]["quiet"] is True, "never in play: not said"
    assert world.gateway.embed_requests == []


def test_a_search_model_started_after_the_documents_were_read_is_noticed_and_meaning_is_added(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    no_models(world)
    ada = start(world)
    keep(ada, *corpus())
    assert search_of(ada)["code"] == "none_set_up"
    ready_world(world)  # the owner starts a search model
    until(lambda: search_of(ada)["code"] in ("ok",), seconds=30)  # the person opens the screen
    until(
        lambda: all(d["meaning"]["state"] == "ready" for d in listing(ada)["documents"]), seconds=60
    )
    assert len(vectors_of(world)) == total_passages(ada)
    assert try_question(ada, PARAPHRASE)["status"] == "found"


def test_the_listing_has_every_field_the_documents_screen_reads(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus())
    found = listing(ada)
    assert set(found["totals"]) >= {"documents", "passages", "bytes", "byState", "embedded", "owed"}
    doc = found["documents"][0]
    assert set(doc["meaning"]) == {
        "embedded",
        "total",
        "etaSeconds",
        "refused",
        "notes",
        "state",
        "message",
    }
    head = found["search"]
    assert set(head) == {
        "mode", "words", "meaning", "headline", "code", "detail", "actions", "model", "adding", "rebuild",
    }  # fmt: skip
    assert set(head["model"]) == {
        "setting", "automatic", "resolved", "label", "locality", "runsOn", "allowExternal",
        "outside", "builtWith", "dimension", "prefixes", "floor",
    }  # fmt: skip
    assert head["model"]["label"] == f"In use: {EMBEDDER} (automatic)"
    assert head["model"]["builtWith"] == EMBEDDER and head["model"]["dimension"] == 24
    assert set(head["model"]["prefixes"]) == {"known", "family", "query", "document", "note"}
    assert set(head["adding"]) == {
        "state", "message", "passages", "embedded", "owed", "wordsOnly", "etaSeconds", "ratePerSecond",
    }  # fmt: skip
    chip = ada.get(f"/api/chats/{ada.new_chat()}/documents").json()
    assert chip["search"]["headline"] == "Searching by meaning and words."
    one = ada.get(f"/api/documents/{doc['id']}").json()
    assert one["meaning"]["state"] == "ready"


def test_a_search_model_the_table_knows_is_given_its_prefixes_and_one_it_does_not_is_given_none(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world, "Qwen3-Embedding-0.6B-Q8_0")
    ada = start(world)
    keep(ada, *corpus())
    try_question(ada, PARAPHRASE)
    head = search_of(ada)
    assert head["model"]["prefixes"]["known"] is True and head["model"]["prefixes"]["note"] is None
    assert head["model"]["prefixes"]["family"] == "qwen3-embedding"
    assert head["model"]["floor"] == embedders.QWEN3_FLOOR
    passages = asked_for_batches(world)
    assert passages and not any(t.startswith("Instruct:") for t in passages), (
        "no prefix on a passage"
    )
    questions = [q for q in asked_for_questions(world) if q != "ping"]
    assert questions and questions[-1] == (
        "Instruct: Given a question, retrieve passages from the person's documents that answer it"
        f"\nQuery:{PARAPHRASE}"
    )


def test_an_unknown_search_model_is_used_without_prefixes_and_the_screen_says_so(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world, "some-new-embedder")
    ada = start(world)
    keep(ada, *corpus())
    try_question(ada, PARAPHRASE)
    prefixes = search_of(ada)["model"]["prefixes"]
    assert prefixes["known"] is False and prefixes["query"] == "" and prefixes["document"] == ""
    assert prefixes["note"].startswith(
        "Workbench does not know which prefixes some-new-embedder wants"
    )
    assert PARAPHRASE in asked_for_questions(world), "the question goes as it is"


# --------------------------------------------------------------------------- #
# a removal, a boot
# --------------------------------------------------------------------------- #


def test_removing_a_document_ends_the_request_that_was_only_for_it_and_nothing_of_it_comes_back(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    world.gateway.embed_gate = threading.Event()
    ada = start(world)
    made = add(ada, ("big.md", big_document(40), "text/markdown"))
    assert made.status_code == 201
    doc_id = made.json()["documents"][0]["id"]
    until(lambda: world.gateway.embed_in_flight == 1)
    assert ada.delete(f"/api/documents/{doc_id}").status_code == 204
    until(lambda: world.gateway.embed_disconnects == 1, seconds=20)
    assert world.gateway.embed_in_flight == 0, "the request was ended, not merely ignored"
    world.gateway.embed_gate.set()
    assert listing(ada)["documents"] == [] and vectors_of(world) == []
    keep(ada, *corpus())  # and the worker went on
    assert len(vectors_of(world)) == total_passages(ada)


def test_a_stop_in_the_middle_is_picked_up_at_boot_from_the_passages_that_have_no_vector(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    world.gateway.embed_delay = 0.3
    ada = start(world)
    assert add(ada, ("big.md", big_document(), "text/markdown")).status_code == 201
    until(lambda: by_name(ada, "big.md")["embedded"] >= 32)
    world.restart_workbench()
    ada = start(world)
    stopped_at = len(world.gateway.embed_requests)
    have = len(vectors_of(world))
    assert 32 <= have < 80
    until(lambda: by_name(ada, "big.md")["state"] == "ready", seconds=60)
    assert len(vectors_of(world)) == 80 and by_name(ada, "big.md")["embedded"] == 80
    later = [
        t
        for r in world.gateway.embed_requests[stopped_at:]
        for t in r["body"]["input"]
        if t != "ping"
    ]
    assert len(later) <= 80 - have + 16, (
        "only what had no vector, and at most the batch the stop cut off"
    )


# --------------------------------------------------------------------------- #
# two people
# --------------------------------------------------------------------------- #


def test_one_persons_meaning_is_never_in_anothers_search(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada, bo = start(world, "p-ada"), start(world, "p-bo")
    keep(ada, *corpus())
    keep(bo, ("bo.md", md("The cook keeps recipes in the pantry.", title="Bo's notes")))
    mine = try_question(bo, PARAPHRASE)
    assert mine["status"] in ("found", "nothing_matched")
    assert all(ARRIVALS not in p["text"] and LEDGER not in p["text"] for p in mine["passages"])
    hers = try_question(ada, "recipes pantry cook")
    assert all("Bo's notes" not in (p["heading"] or "") for p in hers["passages"])
    assert len(vectors_of(world, "p-ada")) == total_passages(ada)
    assert len(vectors_of(world, "p-bo")) == total_passages(bo) == 1
    assert ada.get(f"/api/documents/{by_name(bo, 'bo.md')['id']}").status_code == 404


# --------------------------------------------------------------------------- #
# the settings
# --------------------------------------------------------------------------- #


def test_the_search_model_setting_shows_what_it_comes_to_and_the_new_settings_refuse_what_they_cannot_take(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    good = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    no_models(world)

    def schema() -> dict[str, Any]:
        return {
            f["key"]: f
            for f in httpx.get(
                f"{world.workbench}/v1/config/schema", headers=good, trust_env=False
            ).json()["fields"]
        }

    none = schema()["documentsEmbeddingModel"]
    assert none["default"] == "auto" and none["suggestions"] == ["auto"]
    assert none["status"] == {
        "level": "warning",
        "text": "Searching by words only: no search model is set up.",
    }
    ready_world(world, "mine", "other")
    world.gateway.search_models.append(
        search_model("cloud", locality="external", owned_by="openai", ready=False)
    )
    time.sleep(0.1)
    model = schema()["documentsEmbeddingModel"]
    assert model["suggestions"] == ["auto", "mine", "other", "cloud"]
    assert model["status"] == {"level": "info", "text": "In use: mine (automatic)."}
    assert config(world, documentsEmbeddingModel="other").status_code == 200
    time.sleep(0.1)
    assert schema()["documentsEmbeddingModel"]["status"] == {
        "level": "info",
        "text": "In use: other.",
    }
    for key, value, ok in [
        ("documentsEmbeddingModel", "  ", False),
        ("documentsEmbeddingModel", 7, False),
        ("documentsEmbeddingModel", "x" * 300, False),
        ("documentsEmbeddingModel", "a-model", True),
        ("documentsExternalEmbedding", "yes", False),
        ("documentsExternalEmbedding", True, True),
        ("documentsEmbedRequestsPerMinute", 0, False),
        ("documentsEmbedRequestsPerMinute", 6001, False),
        ("documentsEmbedRequestsPerMinute", True, False),
        ("documentsEmbedRequestsPerMinute", 120, True),
    ]:
        done = config(world, **{key: value}).json()
        assert (done["applied"] == [key]) is ok, (key, value, done)
    current = httpx.get(f"{world.workbench}/v1/config", headers=good, trust_env=False).json()
    assert (
        current["documentsEmbeddingModel"] == "a-model"
        and current["documentsEmbedRequestsPerMinute"] == 120
    )
    assert (
        config(world, documentsEmbeddingModel=None, documentsEmbedRequestsPerMinute=None).json()[
            "rejected"
        ]
        == []
    )
    back = httpx.get(f"{world.workbench}/v1/config", headers=good, trust_env=False).json()
    assert (
        back["documentsEmbeddingModel"] == "auto" and back["documentsEmbedRequestsPerMinute"] == 30
    )


def test_the_rebuild_of_meaning_needs_the_documents_to_be_on_and_owes_nothing_when_nothing_is_kept(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    empty = ada.post("/api/documents/meaning/rebuild")
    assert empty.status_code == 200 and empty.json()["passages"] == 0
    assert empty.json()["search"]["code"] == "ok"
    keep(ada, *corpus())
    from .saved import settings

    settings(world, documentsEnabled=False)
    off = ada.post("/api/documents/meaning/rebuild")
    assert off.status_code == 403 and "turned off documents" in off.text


# --------------------------------------------------------------------------- #
# in a chat
# --------------------------------------------------------------------------- #


def test_a_chat_is_sent_what_meaning_found_and_says_how_it_searched_and_how_each_was_found(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus())
    chat = chat_on(ada)
    answer = ask(ada, chat, PARAPHRASE)
    assert answer["status"] == "done"
    text = user_text(last(world)[-1])
    assert ARRIVALS in text and LEDGER in text, "found by what they say, with none of their words"
    view = answer["retrieval"]
    assert view["status"] == "used" and view["mode"] == "meaning and words"
    assert view["meaning"]["used"] is True and view["meaning"]["model"] == EMBEDDER
    assert view["notes"] == [] and view["passages"] >= 2 and view["terms"] != []
    passages = [s for s in answer["sources"] if s.get("passage")]
    assert {s["via"] for s in passages} == {"meaning"} and len(passages) == view["passages"]
    assert all(s["terms"] == [] and s["snippet"] for s in passages)
    assert answer["finish"] != "documents_only"


def test_when_meaning_cannot_run_the_chat_goes_on_by_words_and_the_answer_says_so(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus())
    world.gateway.search_models[0]["x_eugene_plexus"]["ready_backends"] = 0
    chat = chat_on(ada)
    answer = ask(ada, chat, "pantry flour soup")
    assert answer["status"] == "done"
    assert KITCHEN in user_text(last(world)[-1]), "the words search found it"
    view = answer["retrieval"]
    assert view["status"] == "used" and view["mode"] == "words" and view["meaning"]["used"] is False
    assert view["meaning"]["code"] == "not_running"
    [note] = view["notes"]
    assert note["code"] == "meaning_not_running" and "is not running" in note["message"]
    assert {s["via"] for s in answer["sources"] if s.get("passage")} == {"words"}


def test_nothing_matched_by_words_is_not_said_plainly_when_meaning_was_off(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus())
    world.gateway.search_models[0]["x_eugene_plexus"]["ready_backends"] = 0
    chat = chat_on(ada, documentsOnly=True)
    answer = ask(ada, chat, PARAPHRASE)
    assert answer["finish"] == "documents_only" and answer["model"] is None
    assert answer["content"].startswith("I could not find that in your documents.")
    assert "the search model" in answer["content"] and "is not running" in answer["content"]
    assert answer["retrieval"]["status"] == "nothing_matched"
    assert answer["retrieval"]["message"].startswith(
        "Nothing in your documents matched. Searching by words only:"
    )


def test_below_its_floor_the_best_meaning_is_not_an_answer_and_with_a_word_it_always_counts(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world, "Qwen3-Embedding-0.6B-Q8_0")
    ada = start(world)
    keep(ada, *corpus())
    far = try_question(ada, "What is the airspeed of an unladen swallow?")
    assert far["status"] == "nothing_matched", (
        "words found nothing and meaning is not near anything"
    )
    assert far["meaning"]["belowFloor"] is True
    assert far["meaning"]["floor"] == embedders.QWEN3_FLOOR
    assert far["meaning"]["best"] is not None and far["meaning"]["best"] < embedders.QWEN3_FLOOR
    assert far["passages"] == []
    # A question whose words match anything makes every passage meaning found count, near or not.
    mixed = try_question(ada, "pantry airspeed swallow", passages=10)
    assert mixed["status"] == "found" and mixed["meaning"]["belowFloor"] is False
    assert any(KITCHEN in p["text"] for p in mixed["passages"]) and len(mixed["passages"]) > 1
    # A run that names its ways shows what meaning found, whatever it scored.
    raw_view = try_question(ada, "What is the airspeed of an unladen swallow?", ways=["meaning"])
    assert raw_view["status"] == "found" and raw_view["meaning"]["belowFloor"] is False


def test_a_model_the_table_has_no_floor_for_has_no_such_rule(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus())
    far = try_question(ada, "What is the airspeed of an unladen swallow?")
    assert far["status"] == "found" and far["meaning"]["floor"] is None
    assert far["passages"], (
        "an unknown model: the nearest passages are sent, and the model is told they may not answer"
    )


def test_a_chat_file_searched_instead_is_embedded_and_found_by_meaning_in_its_own_chat_only(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    world.gateway.context_length = 8192
    ada = start(world)
    keep(ada, *corpus())
    chat, other = ada.new_chat(), ada.new_chat()
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    assert choose_search(ada, chat, file_id).status_code == 200
    wait_searchable(ada, chat, file_id)
    until(lambda: (search_state(ada, chat, file_id) or {}).get("status") == "ready", seconds=60)
    with raw(world) as db:
        mine = db.execute(
            "SELECT COUNT(*), COUNT(c.vec) FROM chunks c JOIN documents d ON d.id = c.doc_id "
            "WHERE d.scope = ?",
            (f"chat:{chat}",),
        ).fetchone()
    assert mine[0] >= 12 and mine[0] == mine[1], "the chat's own file has its meaning too"
    question = "weather tide gulls harbour observations"
    own = try_question(ada, question, chatId=chat, passages=10)
    assert any(p["name"] == "survey.pdf" for p in own["passages"]), (
        "found in the chat it was added to"
    )
    library = try_question(ada, question, passages=10)
    assert all(p["name"] != "survey.pdf" for p in library["passages"]), "never in another search"
    elsewhere = try_question(ada, question, chatId=other, passages=10)
    assert all(p["name"] != "survey.pdf" for p in elsewhere["passages"])
    by_meaning = try_question(ada, question, chatId=chat, ways=["meaning"], passages=10)
    assert any(p["name"] == "survey.pdf" and p["via"] == "meaning" for p in by_meaning["passages"])


def test_a_document_added_after_a_question_is_found_by_meaning_in_the_next_one(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    """The vectors in memory are let go of when the index is written to, or this question
    would be searched in the numbers as they were before the document came."""
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus())
    assert try_question(ada, PARAPHRASE)["status"] == "found"  # the vectors are read into memory
    lamps = md("The lamp room is cleaned on Sundays.", title="Lamps")
    assert add(ada, ("lamps.md", lamps)).status_code == 201
    until(lambda: by_name(ada, "lamps.md")["state"] == "ready", seconds=30)
    got = try_question(ada, "who cleans the beacon?")
    assert got["status"] == "found" and got["mode"] == "meaning and words"
    assert any("lamp room" in p["text"] and p["via"] == "meaning" for p in got["passages"])


def test_a_question_the_key_refuses_is_searched_by_words_and_the_answer_says_why(
    world: World,
    quick: None,  # noqa: F811
) -> None:
    ready_world(world)
    ada = start(world)
    keep(ada, *corpus())
    world.gateway.embed_script.append(
        (429, {"error": {"message": "60 requests per minute"}}, {"retry-after": "30"})
    )
    got = try_question(ada, "pantry flour soup")
    assert got["status"] == "found" and got["mode"] == "words"
    assert {p["via"] for p in got["passages"]} == {"words"} and KITCHEN in got["passages"][0][
        "text"
    ]
    assert got["meaning"]["used"] is False and got["meaning"]["code"] == "limited"
    assert (
        "this Workbench's key is at its limit (60 requests per minute)" in got["meaning"]["message"]
    )
    again = try_question(ada, "pantry flour soup")
    assert again["mode"] == "meaning and words", "the next question is asked as ever"
