"""Which search model, and what it needs of its text (MR5, design 4.2, 4.9, MRC7, MRC10, MRC20).
Pure: every rule is a function of its arguments.
"""

from __future__ import annotations

from typing import Any

import pytest

from eugene_plexus_workbench.knowledge import embedders
from eugene_plexus_workbench.knowledge.embedders import (
    Identity,
    SearchModel,
    choose,
    duration_words,
    identity_for,
    passage_input,
    question_input,
    search_models,
)

QWEN = "Qwen3-Embedding-0.6B-Q8_0"
NOMIC = "nomic-embed-text-v1.5.Q8_0"


# --------------------------------------------------------------------------- #
# the prefix table
# --------------------------------------------------------------------------- #


def test_qwen3_embedding_is_asked_with_its_instruction_and_a_passage_has_none() -> None:
    who = identity_for(QWEN, 1024)
    assert who.known and who.family == "qwen3-embedding" and who.dim == 1024
    assert who.query_prefix == (
        "Instruct: Given a question, retrieve passages from the person's documents "
        "that answer it\nQuery:"
    )
    assert who.document_prefix == ""
    assert question_input(who, "How much?") == who.query_prefix + "How much?"
    # The headings above a passage go with it, on a line of their own; the words alone otherwise.
    assert passage_input(who, "Notes > Budget", "Two GPUs.") == "Notes > Budget\nTwo GPUs."
    assert passage_input(who, None, "Two GPUs.") == "Two GPUs."


def test_nomic_has_a_prefix_for_a_question_and_another_for_a_passage() -> None:
    who = identity_for(NOMIC, 768)
    assert who.known and who.family == "nomic-embed-text"
    assert question_input(who, "how much") == "search_query: how much"
    assert passage_input(who, "A > B", "text") == "search_document: A > B\ntext"


def test_nomic_version_two_has_the_same_prefixes_and_a_window_of_512_tokens() -> None:
    v1, v2 = (
        identity_for("nomic-embed-text-v1.5", 768),
        identity_for("nomic-embed-text-v2-moe", 768),
    )
    assert (v1.max_input_tokens, v2.max_input_tokens) == (2_048, 512)
    assert v1.query_prefix == v2.query_prefix == "search_query: "


def test_embeddinggemma_puts_the_headings_where_its_template_has_a_title() -> None:
    who = identity_for("embeddinggemma-2-Q8_0", 768)
    assert who.known and who.family == "embeddinggemma"
    assert question_input(who, "q") == "task: search result | query: q"
    assert passage_input(who, "A > B", "t") == "title: A > B | text: t"
    assert passage_input(who, None, "t") == "title: none | text: t"


def test_an_unknown_model_gets_no_prefixes_and_that_is_said() -> None:
    who = identity_for("bge-m3-q8_0", 1024)
    assert not who.known and who.family is None
    assert (who.query_prefix, who.document_prefix, who.max_input_tokens) == ("", "", None)
    assert question_input(who, "q") == "q" and passage_input(who, None, "t") == "t"
    note = embedders.unknown_prefixes(who)
    assert note is not None and "bge-m3-q8_0" in note and "uses none" in note
    assert embedders.unknown_prefixes(identity_for(QWEN, 1024)) is None


@pytest.mark.parametrize(
    "name",
    [
        "Qwen3-Embedding-0.6B-Q8_0",
        "C:/models/qwen3-embedding-4b.gguf",
        "QWEN3_EMBEDDING_8B",
        "qwen3.embedding",
    ],
)
def test_a_model_is_known_by_the_words_of_its_id_whatever_the_case_or_folder(name: str) -> None:
    assert embedders.lookup(name) is not None and embedders.lookup(name).family == "qwen3-embedding"  # type: ignore[union-attr]


def test_a_question_is_cut_at_fourteen_hundred_characters() -> None:
    who = identity_for(QWEN, 1024)
    cut = question_input(who, "x" * 5000)
    assert cut == who.query_prefix + "x" * embedders.MAX_QUESTION_CHARS


def test_only_qwen3_has_a_floor_because_only_it_was_measured() -> None:
    assert embedders.floor_for(QWEN) == embedders.QWEN3_FLOOR
    assert embedders.floor_for(NOMIC) is None and embedders.floor_for("bge-m3") is None


# --------------------------------------------------------------------------- #
# what an index is built with
# --------------------------------------------------------------------------- #


def test_what_a_search_is_built_with_survives_the_index_and_comes_back_whole() -> None:
    who = identity_for(QWEN, 1024, now=1234.5)
    again = Identity.from_json(who.to_json())
    assert again == who and again is not None and again.built_at == 1234.5


def test_nothing_recorded_is_none_and_what_cannot_be_read_is_said() -> None:
    assert Identity.from_json(None) is None
    for raw in ("not json", "[]", '{"model": ""}', '{"model": "m", "dim": 0}', '{"model": "m"}'):
        with pytest.raises(ValueError, match="unreadable"):
            Identity.from_json(raw)


# --------------------------------------------------------------------------- #
# which search model
# --------------------------------------------------------------------------- #


def models(*specs: tuple[str, str, bool]) -> list[SearchModel]:
    return [
        SearchModel(i, locality, ready, who="openai" if locality == "external" else None)
        for i, locality, ready in specs
    ]


def test_auto_is_the_first_search_model_that_runs_here_and_is_ready() -> None:
    found = models(
        ("far", "external", True),
        ("down", "local", False),
        ("near", "local", True),
        ("other", "local", True),
    )
    picked = choose(found, {m.id for m in found}, "auto", False)
    assert picked.model is not None and picked.model.id == "near" and picked.code == "ok"
    assert picked.automatic and picked.outside == ("far",)


def test_auto_keeps_the_model_a_search_was_built_with_while_it_runs() -> None:
    found = models(("a", "local", True), ("b", "local", True))
    assert choose(found, {"a", "b"}, "auto", False, built_with="b").model.id == "b"  # type: ignore[union-attr]
    assert choose(found, {"a", "b"}, "auto", False, built_with="gone").model.id == "a"  # type: ignore[union-attr]
    down = models(("a", "local", True), ("b", "local", False))
    assert choose(down, {"a", "b"}, "auto", False, built_with="b").model.id == "a"  # type: ignore[union-attr]


def test_auto_never_picks_a_model_outside_the_machines_even_when_the_owner_allows_it() -> None:
    found = models(("cloud", "external", True), ("odd", "unknown", True))
    for allowed in (False, True):
        picked = choose(found, {"cloud", "odd"}, "auto", allowed)
        assert picked.model is None and picked.code == "outside_only"
        assert "cloud" in (picked.message or "") and "the owner has not allowed" in (
            picked.message or ""
        )


def test_auto_says_which_search_model_is_not_running_when_one_exists_and_none_is() -> None:
    found = models(("mine", "local", False), ("cloud", "external", True))
    picked = choose(found, {"mine", "cloud"}, "auto", False)
    assert picked.model is None and picked.code == "not_running" and picked.named == "mine"
    assert picked.message == (
        "Searching by words only: the search model mine is not running. "
        "Start it in Eugene's console under Backends."
    )


def test_with_no_search_model_at_all_it_says_none_is_set_up() -> None:
    picked = choose([], {"a-chat-model"}, "auto", False)
    assert picked.model is None and picked.code == "none_set_up"
    assert picked.message == "Searching by words only: no search model is set up."


def test_the_model_a_search_was_built_with_is_named_when_it_has_left_the_list_or_stopped() -> None:
    """Saying "no search model is set up" of a search that was built with one would be untrue:
    it is waiting for that model, by name."""
    said = (
        "Searching by words only: the search model qwen3 is not running. "
        "Start it in Eugene's console under Backends."
    )
    gone = choose([], {"a-chat-model"}, "auto", False, built_with="qwen3")
    assert gone.model is None and gone.code == "not_running" and gone.named == "qwen3"
    assert gone.message == said
    cloud = models(("cloud", "external", True))
    assert choose(cloud, {"cloud"}, "auto", False, built_with="qwen3").message == said
    stopped = models(("other", "local", False), ("qwen3", "local", False))
    named = choose(stopped, {"other", "qwen3"}, "auto", False, built_with="qwen3")
    assert named.code == "not_running" and named.named == "qwen3", "it, and not the first listed"
    assert choose(stopped, {"other", "qwen3"}, "auto", False).named == "other"


def test_a_blank_setting_is_auto() -> None:
    found = models(("near", "local", True))
    assert choose(found, {"near"}, "  ", False).model.id == "near"  # type: ignore[union-attr]


def test_a_named_model_that_runs_here_is_used_when_it_is_ready() -> None:
    found = models(("a", "local", True), ("b", "local", True))
    picked = choose(found, {"a", "b"}, "b", False)
    assert picked.model is not None and picked.model.id == "b" and not picked.automatic
    down = choose(models(("b", "local", False)), {"b"}, "b", False)
    assert down.model is None and down.code == "not_running" and down.named == "b"


def test_a_model_outside_the_machines_is_used_only_when_it_is_named_and_the_owner_allows_it() -> (
    None
):
    found = models(("cloud", "external", True), ("odd", "unknown", True))
    refused = choose(found, {"cloud", "odd"}, "cloud", False)
    assert refused.model is None and refused.code == "outside"
    assert refused.message == (
        "Searching by words only: cloud runs on openai, outside your machines, and the "
        "owner has not allowed sending your documents' text there."
    )
    allowed = choose(found, {"cloud", "odd"}, "cloud", True)
    assert allowed.model is not None and allowed.model.id == "cloud"
    unplaced = choose(found, {"cloud", "odd"}, "odd", False)
    assert unplaced.code == "outside" and "somewhere Eugene cannot place" in (
        unplaced.message or ""
    )


def test_a_name_that_is_not_a_search_model_is_told_from_one_that_is_not_there() -> None:
    found = models(("a", "local", True))
    chat = choose(found, {"a", "a-chat-model"}, "a-chat-model", False)
    assert chat.code == "not_search_model" and "is not a search model" in (chat.message or "")
    missing = choose(found, {"a"}, "nothing-by-that-name", False)
    assert missing.code == "not_listed" and "lists no search model named" in (missing.message or "")


def test_the_gateways_list_is_read_for_surface_locality_readiness_and_who_runs_it() -> None:
    listing: dict[str, Any] = {
        "data": [
            {"id": "chat", "x_eugene_plexus": {"surfaces": ["chat"]}},
            {
                "id": "local-embed",
                "x_eugene_plexus": {
                    "surfaces": ["embeddings"],
                    "locality": "local",
                    "ready_backends": 1,
                },
            },
            {
                "id": "cloud/embed",
                "owned_by": "openai",
                "x_eugene_plexus": {
                    "surfaces": ["embeddings"],
                    "locality": "external",
                    "ready_backends": 0,
                },
            },
            {
                "id": "bare",
                "x_eugene_plexus": {"surfaces": ["embeddings"], "drivers": ["only-driver"]},
            },
            {"not": "a model"},
        ]
    }
    found, every = search_models(listing)
    assert every == {"chat", "local-embed", "cloud/embed", "bare"}
    by_id = {m.id: m for m in found}
    assert set(by_id) == {"local-embed", "cloud/embed", "bare"}
    assert (by_id["local-embed"].local, by_id["local-embed"].ready) == (True, True)
    assert (
        by_id["cloud/embed"].locality,
        by_id["cloud/embed"].ready,
        by_id["cloud/embed"].who,
    ) == ("external", False, "openai")
    assert (by_id["bare"].locality, by_id["bare"].who) == ("unknown", "only-driver")


# --------------------------------------------------------------------------- #
# how a time is said
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("seconds", "said"),
    [
        (5, "less than a minute"),
        (59, "less than a minute"),
        (60, "about 1 minute"),
        (14 * 60 + 10, "about 14 minutes"),
        (90 * 60, "about 1.5 hours"),
        (2 * 3600, "about 2 hours"),
        (3 * 86400, "about 3 days"),
    ],
)
def test_a_time_is_said_the_way_a_person_says_it(seconds: float, said: str) -> None:
    assert duration_words(seconds) == said
