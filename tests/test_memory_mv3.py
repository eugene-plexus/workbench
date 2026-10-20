"""MV3a: the measurement (sets, scorer, bars), Eugene's recommendation, the per-model choice,
Measure this model over HTTP, and the store's migration to schema 24.

The measurement's rules are pure and tested here directly; the routes run on real servers with
the fake gateway (`conftest.py`), which answers a pass request with `suggest_answer`.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import memory_learn, memory_measure, memory_models
from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.config import MemorySettings
from eugene_plexus_workbench.store import SCHEMA_VERSION, MemoryNote, Store

from .conftest import MODEL, World
from .test_memory_api import ask, signed_in
from .test_memory_learn import chat_of, note, notes, quick, wait_suggest_calls  # noqa: F401

INSTALL = MemorySettings(enabled=True, max_notes=300, external="ask", suggestions="measured")
UNSET = {"enabled": True, "learn": "off", "learn_set": False}


def metrics(**over: Any) -> dict[str, Any]:
    """A pass that meets every bar, with `over` changed."""
    base = {
        "records": 100,
        "answers": 100,
        "unreadable": 0,
        "kept": 100,
        "facts": 70,
        "expected": 100,
        "false": 1,
        "leaks": 0,
        "injections": 0,
        "sensitive": 0,
        "noise": 0,
        "noiseSets": 60,
        "datesRight": 0,
        "datesAsked": 0,
        "replacesRight": 6,
        "replacesWrong": 0,
        "changed": 7,
        "medianSeconds": 1.0,
    }
    return {**base, **over}


# --------------------------------------------------------------------------- #
# the sets, the scorer and the bars
# --------------------------------------------------------------------------- #


def test_the_sets_are_unique_and_hold_the_new_question_types() -> None:
    ids = [s["id"] for s in memory_measure.SETS]
    assert len(ids) == len(set(ids))
    categories = {s["category"] for s in memory_measure.SETS}
    assert {"update", "time", "sessions", "transient"} <= categories
    # Every changed fact names the note it replaces; every twin names none.
    for s in memory_measure.SETS:
        if s["category"] == "update" and s["replaces"]:
            assert s["known"] and s["expect"]


def test_a_note_naming_a_note_it_does_not_replace_is_a_wrong_supersession() -> None:
    helix = next(s for s in memory_measure.SETS if s["id"] == "helix")
    right = memory_measure.score(helix, [{"text": "Uses Helix as their editor.", "replaces": 1}])
    assert right["replaces_right"] == 1 and right["replaces_wrong"] == 0
    cello = next(s for s in memory_measure.SETS if s["id"] == "cello")
    wrong = memory_measure.score(cello, [{"text": "Is learning the cello.", "replaces": 2}])
    assert wrong["replaces_wrong"] == 1 and wrong["facts"] == ["cello"]


def test_the_bars() -> None:
    assert memory_measure.verdict(metrics(), tool=metrics())["keep"] is True
    # A model that calls tools is not recommended for Keep without its tool path measured.
    v = memory_measure.verdict(metrics())
    assert v["suggest"] and v["why"]["keep"] == "the remember tool was not measured"
    assert memory_measure.verdict(metrics(), tools=False)["keep"] is True
    # Suggest's bar holds at 3% false memories; Keep automatically's does not.
    v = memory_measure.verdict(metrics(false=3))
    assert v["suggest"] and not v["keep"] and "false memories 3%" in v["why"]["keep"]
    assert not memory_measure.verdict(metrics(injections=1))["suggest"]
    assert (
        memory_measure.verdict(metrics(facts=50))["why"]["suggest"] == "kept 50% of what it should"
    )
    v = memory_measure.verdict(metrics(replacesWrong=1))
    assert v["suggest"] and v["why"]["keep"] == "replaced a note it should have left alone"
    v = memory_measure.verdict(metrics(replacesRight=2))
    assert "named the note a change replaces" in v["why"]["keep"]
    # The tool path must meet Keep automatically's bar too.
    v = memory_measure.verdict(metrics(), tool=metrics(facts=10))
    assert v["suggest"] and not v["keep"] and v["why"]["keep"].startswith("with the remember tool")
    # The tag list: only when the run with it meets the bar the run without it meets.
    assert memory_measure.verdict(metrics(), tool=metrics(), tags=metrics())["tags"] is True
    assert memory_measure.verdict(metrics(), tool=metrics(), tags=metrics(false=3))["tags"] is False
    # Suggest is judged on MR8's sets (call MV3-C1); Keep on every set.
    v = memory_measure.verdict(metrics(false=30), suggest=metrics())
    assert v["suggest"] and not v["keep"]
    # A reason never quotes the figure its bar allows: 5.04% is "5.1%", 59.96% "59.9%".
    assert memory_measure.failed(metrics(false=504, kept=10000), memory_measure.SUGGEST_BAR) == (
        "false memories 5.1% of what it kept"
    )
    assert memory_measure.failed(
        metrics(facts=5996, expected=10000), memory_measure.SUGGEST_BAR
    ) == ("kept 59.9% of what it should")
    assert memory_measure.verdict(metrics(false=3), tags=metrics(false=3))["tags"] is True
    assert memory_measure.verdict(metrics(facts=10), tags=metrics())["tags"] is False


def test_tool_calls_are_judged_as_the_pass_is() -> None:
    answer = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "remember",
                                "arguments": json.dumps(
                                    {
                                        "text": "Uses Helix as their editor.",
                                        "kind": "fact",
                                        "quote": "I've switched from Vim to Helix as my editor",
                                        "replaces": "uses vim as their editor.",
                                    }
                                ),
                            }
                        },
                        {"function": {"name": "web_search", "arguments": "{}"}},
                        {"function": {"name": "remember", "arguments": "not json"}},
                    ]
                }
            }
        ]
    }
    helix = next(s for s in memory_measure.SETS if s["id"] == "helix")
    record = memory_measure.record_of(helix, "tool", 1, answer, 0.5)
    assert [k["text"] for k in record["kept"]] == ["Uses Helix as their editor."]
    assert record["kept"][0]["replaces"] == 1
    assert record["score"]["replaces_right"] == 1
    assert [r["reason"] for r in record["rejected"]] == ["not_a_note"]


def test_a_measurement_through_any_complete() -> None:
    sent: list[dict[str, Any]] = []

    async def complete(body: dict[str, Any]) -> dict[str, Any]:
        sent.append(body)
        if body.get("tools"):
            return {}  # no completion: an unreadable answer
        return {"choices": [{"message": {"content": '{"notes": []}'}}]}

    seen: list[int] = []
    result = asyncio.run(
        memory_measure.measure(complete, "m", tools=True, progress=lambda d, t: seen.append(d))
    )
    assert result["pass"]["unreadable"] == 0 and result["tool"]["unreadable"] == len(
        memory_measure.SETS
    )
    assert result["tags"] is None  # the pass missed Suggest's bar: no run with the list
    assert result["verdict"]["suggest"] is False
    assert seen[-1] == memory_measure.requests_for(True)
    assert all(b["model"] == "m" for b in sent)


# --------------------------------------------------------------------------- #
# Eugene's recommendation, and what a model does for a person
# --------------------------------------------------------------------------- #


def test_shipped_measurements_match_the_starter_models_and_never_an_abliterated_one() -> None:
    gemma = memory_models.measurement_for("gemma-4-E4B-it-Q4_K_M")
    assert gemma is not None and gemma.by == "eugene" and gemma.verdict["suggest"]
    assert memory_models.measurement_for("Huihui-Qwen3.8-27B-abliterated") is None
    assert memory_models.measurement_for("gemma-4-e4b-uncensored-Q4_K_M") is None
    assert memory_models.measurement_for("never-measured-model") is None
    assert memory_learn.passed("gemma-4-12b") and not memory_learn.passed("qwen3.5-4b")


def test_the_recommendation_says_why() -> None:
    assert memory_models.recommendation(None) == {
        "level": "unmeasured",
        "text": "Not measured yet.",
        "measuredOn": None,
        "by": None,
    }
    local = {
        "mine": {
            "measuredAt": 1_792_000_000.0,
            "owner": "ada",
            "result": {
                "pass": metrics(facts=7),
                "verdict": memory_measure.verdict(metrics(facts=7)),
            },
        }
    }
    found = memory_models.measurement_for("mine", local)
    said = memory_models.recommendation(found)
    assert said["level"] == "no" and said["by"] == "install"
    assert said["text"].startswith("Not recommended: measured on ")
    assert "kept 7% of what it should" in said["text"]
    keep = {"pass": metrics(), "verdict": memory_measure.verdict(metrics(), tool=metrics())}
    said = memory_models.recommendation(
        memory_models.Measurement("x", "eugene", "2026-10-20", keep)
    )
    assert said["text"] == (
        "Recommended for Keep automatically: measured on 2026-10-20, false memories 1%, recall 70%."
    )


def test_the_install_measurement_of_an_exact_id_wins_over_eugenes() -> None:
    model = "gemma-4-E4B-it-Q4_K_M"
    local = {
        model: {
            "measuredAt": 1.0,
            "owner": "ada",
            "result": {"verdict": {"suggest": False}, "startedByOwner": True},
        }
    }
    assert memory_learn.passed(model) and not memory_learn.passed(model, local)


def test_what_a_model_does_for_a_person() -> None:
    effective = memory_learn.effective
    assert effective(INSTALL, UNSET, "gemma-4-E4B-it-Q4_K_M") == ("suggest", "measured")
    assert effective(INSTALL, UNSET, "gemma-4-E4B-it-Q4_K_M", choice="off") == ("off", "you")
    assert effective(INSTALL, UNSET, "never-measured", choice="suggest") == ("suggest", "you")
    # Keep automatically suggests until the write policy allows it (MV3b).
    assert effective(INSTALL, UNSET, "m", choice="auto") == ("suggest", "you")
    assert effective(INSTALL, UNSET, "m", choice="auto", keep_allowed=True) == ("auto", "you")
    # The owner's off is final; the person's own Memory off too.
    off = MemorySettings(enabled=True, max_notes=300, external="ask", suggestions="off")
    assert effective(off, UNSET, "m", choice="suggest") == ("off", "owner")
    assert effective(INSTALL, {**UNSET, "enabled": False}, "m", choice="suggest") == ("off", "you")
    # Never Keep automatically by default, whatever the measurement says.
    keep = {
        "m": {
            "measuredAt": 1.0,
            "owner": None,
            "result": {"verdict": {"suggest": True, "keep": True}, "startedByOwner": True},
        }
    }
    assert effective(INSTALL, UNSET, "m", local=keep) == ("suggest", "measured")


# --------------------------------------------------------------------------- #
# the routes and the learning pass
# --------------------------------------------------------------------------- #


def test_the_models_list_and_a_choice_per_model(world: World) -> None:
    ada = signed_in(world)
    page = ada.get("/api/memory/models")
    assert page.status_code == 200, page.text
    [model] = [m for m in page.json()["models"] if m["id"] == MODEL]
    assert model["mode"] == "off" and model["by"] == "default" and model["chosen"] is None
    assert model["recommendation"]["level"] == "unmeasured"
    chose = ada.put(f"/api/memory/models/{MODEL}", json={"mode": "suggest"})
    assert chose.status_code == 200, chose.text
    [model] = [m for m in chose.json()["models"] if m["id"] == MODEL]
    assert (model["mode"], model["by"], model["chosen"], model["defaultMode"]) == (
        "suggest",
        "you",
        "suggest",
        "off",
    )
    # Keep automatically is a mode like any other (MV3b); an unknown mode is refused.
    assert ada.put(f"/api/memory/models/{MODEL}", json={"mode": "always"}).status_code == 422
    # Only a model Eugene lists for them, so the choices cannot grow without end.
    assert ada.put("/api/memory/models/not-listed", json={"mode": "off"}).status_code == 404
    # Another person's choices are theirs.
    bo = signed_in(world, "p-bo")
    [theirs] = [m for m in bo.get("/api/memory/models").json()["models"] if m["id"] == MODEL]
    assert theirs["chosen"] is None
    forgot = ada.put(f"/api/memory/models/{MODEL}", json={"mode": None})
    assert [m["chosen"] for m in forgot.json()["models"] if m["id"] == MODEL] == [None]


def test_a_choice_for_one_model_makes_its_chats_suggest(world: World) -> None:
    ada = signed_in(world)
    assert ada.put(f"/api/memory/models/{MODEL}", json={"mode": "suggest"}).status_code == 200
    assert ada.post("/api/memory/tags", json={"tag": "zephyrine"}).status_code == 200
    world.gateway.suggest_answer = notes(note("Works as a nurse.", "I'm a nurse"))
    chat = chat_of(ada)
    ask(world, ada, chat, "I'm a nurse working night shifts.")
    [call] = wait_suggest_calls(world, 1)
    assert call["body"]["model"] == MODEL
    # MV1-C1: a model not measured with a tag list is not sent one.
    assert "zephyrine" not in json.dumps(call["body"])
    page = ada.get("/api/memory").json()
    [offered] = page["suggestions"]
    assert offered["text"] == "Works as a nurse."


def test_the_model_that_suggested_a_note_is_kept_with_it(tmp_path: Path) -> None:
    async def go() -> MemoryNote:
        store = Store(tmp_path / "w.sqlite3")
        await store.open()
        try:
            await store.create_chat(storage.Chat("c", "ada", "C", "m", 1.0, 1.0))
            note_ = MemoryNote(
                id="n1",
                owner="ada",
                text="Works as a nurse.",
                kind="fact",
                created_at=1.0,
                updated_at=1.0,
                model="gemma-4-e4b",
            )
            await store.add_suggestions("ada", "c", [note_], read_at=1.0, limit=100)
            [found] = await store.memory_notes("ada", status="pending")
            return found
        finally:
            await store.close()

    found = asyncio.run(go())
    assert found.model == "gemma-4-e4b" and found.auto is False and found.seen_at is None


def test_measure_this_model_runs_and_its_result_is_the_installs(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    ada = signed_in(world)
    world.gateway.more_chat_models.append(
        {
            "id": "small-local",
            "object": "model",
            "x_eugene_plexus": {"surfaces": ["chat"], "locality": "local", "tool_calling": False},
        }
    )
    started = ada.post("/api/memory/measurements", json={"model": "small-local"})
    assert started.status_code == 202, started.text
    assert started.json()["measuring"]["model"] == "small-local"
    busy = ada.post("/api/memory/measurements", json={"model": "small-local"})
    assert busy.status_code in (202, 409)
    deadline = time.perf_counter() + 20
    while time.perf_counter() < deadline:
        if ada.get("/api/memory/measurements").json()["measuring"] is None:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the measurement did not end")
    bo = signed_in(world, "p-bo")
    [measured] = [
        m for m in bo.get("/api/memory/models").json()["models"] if m["id"] == "small-local"
    ]
    assert measured["recommendation"]["by"] == "install"
    assert measured["recommendation"]["level"] == "no"  # it kept nothing
    # Each request went as the person who asked.
    assert {r["person"] for r in world.gateway.suggest_requests} - {None}


def test_an_outside_model_needs_confirming_and_three_a_day(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    ada = signed_in(world)
    world.gateway.more_chat_models.append(
        {
            "id": "cloud",
            "object": "model",
            "owned_by": "openai",
            "x_eugene_plexus": {
                "surfaces": ["chat"],
                "locality": "external",
                "tool_calling": False,
            },
        }
    )
    asked = ada.post("/api/memory/measurements", json={"model": "cloud"})
    assert asked.status_code == 409
    detail = asked.json()["detail"]
    assert detail["code"] == "confirm_outside" and "made-up messages" in detail["message"]
    assert ada.post("/api/memory/measurements", json={"model": "nope"}).status_code == 404

    from eugene_plexus_workbench import memory_models_api

    monkeypatch.setattr(memory_models_api, "RUNS_PER_DAY", 1)
    first = ada.post("/api/memory/measurements", json={"model": "cloud", "confirmOutside": True})
    assert first.status_code == 202, first.text
    assert ada.delete("/api/memory/measurements/current").status_code == 204
    [cloud] = [m for m in ada.get("/api/memory/models").json()["models"] if m["id"] == "cloud"]
    assert cloud["lastRun"]["outcome"] == "cancelled"
    again = ada.post("/api/memory/measurements", json={"model": "cloud", "confirmOutside": True})
    assert again.status_code == 429


# --------------------------------------------------------------------------- #
# the store: schema 23 to 24
# --------------------------------------------------------------------------- #


def test_a_schema_23_store_gains_provenance_choices_and_measurements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "workbench.sqlite3"

    async def make() -> None:
        store = Store(path)
        await store.open()
        await store.add_memory_note(
            MemoryNote(
                id="n1",
                owner="ada",
                text="Lives in Leeds.",
                kind="fact",
                created_at=1.0,
                updated_at=1.0,
            ),
            limit=100,
        )
        await store.close()

    with monkeypatch.context() as m:
        m.setattr(storage, "SCHEMA_VERSION", 23)
        m.setattr(storage, "_learning", lambda db: None)
        # A schema 23 store's notes have no provenance columns to insert.
        m.setattr(
            storage, "_NOTE_LIST", storage._NOTE_LIST.replace(", model, policy, auto, seen_at", "")
        )
        insert = storage.Store._insert_note

        def old_insert(db: sqlite3.Connection, note: MemoryNote) -> None:
            db.execute(
                "INSERT INTO memory_notes (id, owner, text, kind, status, pinned, source, "
                "created_at, updated_at, valid_from) VALUES (?, ?, ?, ?, 'active', 0, 'typed', ?, ?, ?)",
                (note.id, note.owner, note.text, note.kind, 1.0, 1.0, 1.0),
            )

        m.setattr(storage.Store, "_insert_note", staticmethod(old_insert))
        asyncio.run(make())
        assert insert is not None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == "23"

    async def check() -> tuple[list[MemoryNote], dict[str, str], dict[str, Any]]:
        store = Store(path)
        await store.open()
        try:
            await store.put_memory_model_choice("ada", "m", "suggest")
            await store.put_memory_measurement("m", "ada", {"verdict": {"suggest": True}})
            return (
                await store.memory_notes("ada"),
                await store.memory_model_choices("ada"),
                await store.memory_measurements(),
            )
        finally:
            await store.close()

    [kept], choices, measured = asyncio.run(check())
    assert kept.text == "Lives in Leeds." and kept.model is None and kept.auto is False
    assert choices == {"m": "suggest"} and measured["m"]["result"]["verdict"]["suggest"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == str(
            SCHEMA_VERSION
        )
    assert list(tmp_path.glob("*.schema-23*")), "the store's backup before the migration"


def test_only_the_owners_own_run_sets_the_installs_default() -> None:
    passing = {"verdict": {"suggest": True, "keep": False}}
    theirs = {"m": {"measuredAt": 1.0, "owner": "p-ada", "result": passing}}
    owners = {"m": {"measuredAt": 1.0, "owner": "o", "result": {**passing, "startedByOwner": True}}}
    # Shown as advice beside the choice...
    found = memory_models.measurement_for("m", theirs)
    assert found is not None and memory_models.recommendation(found)["advice"] is True
    # ...but it turns nothing on for anyone who has not chosen.
    assert memory_learn.effective(INSTALL, UNSET, "m", local=theirs) == ("off", "default")
    assert memory_learn.effective(INSTALL, UNSET, "m", local=owners) == ("suggest", "measured")


def test_a_gateway_that_does_not_answer_is_not_a_measurement() -> None:
    async def complete(body: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("connection refused")

    with pytest.raises(memory_measure.MeasurementFailed):
        asyncio.run(memory_measure.measure(complete, "m", tools=False))
