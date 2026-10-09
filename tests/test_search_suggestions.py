"""A search provider's Search Suggestions are kept with the answer they came
with, for the person who asked, and go nowhere else
(`google-search-account.md` GS4)."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path

import pytest

from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.answers import message_view
from eugene_plexus_workbench.store import Store

from .conftest import World
from .test_store_schema import _V1_MESSAGES

#: Google's shape: its own style, its logo, chips that are links.
GOOGLE = (
    "<style>.container{padding:8px 12px}.chip{height:36px}</style>"
    '<div class="container"><a class="chip" href="https://www.google.com/search?q=barns">'
    "barns &amp; silos</a></div>"
)


def _searched(world: World, chat_text: str = "News?") -> tuple[object, str, dict]:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": chat_text, "search": True})
    return ada, chat, ada.wait_answer(chat)


def test_the_suggestions_are_kept_with_the_answer_byte_for_byte(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.gateway.suggestions = [GOOGLE, GOOGLE + "<!-- second -->"]
    with caplog.at_level(logging.DEBUG):
        ada, chat, answer = _searched(world)
        assert answer["status"] == "done"
        assert answer["searchSuggestions"] == [GOOGLE, GOOGLE + "<!-- second -->"]
        # Kept: a fresh read, and a restart, show the same.
        again = ada.get(f"/api/chats/{chat}").json()["messages"][-1]
        assert again["searchSuggestions"] == answer["searchSuggestions"]
        world.restart_workbench()
        ada.sign_in("p-ada")
        restarted = ada.get(f"/api/chats/{chat}").json()["messages"][-1]
        assert restarted["searchSuggestions"] == answer["searchSuggestions"]
    assert "google.com/search" not in caplog.text, "no log line carries the suggestions"


def test_a_search_without_suggestions_and_an_unsearched_answer_have_none(world: World) -> None:
    _, _, answer = _searched(world)
    assert answer["searches"] == 1 and answer["searchSuggestions"] == []
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    world.gateway.suggestions = [GOOGLE]
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Hi"})
    assert ada.wait_answer(chat)["searchSuggestions"] == [], "no search, none kept"


def test_the_suggestions_are_not_sent_to_a_model_on_the_next_turn(world: World) -> None:
    world.gateway.suggestions = [GOOGLE]
    ada, chat, answer = _searched(world)
    assert answer["searchSuggestions"] == [GOOGLE]
    ada.post(f"/api/chats/{chat}/messages", json={"content": "And then?", "search": True})
    ada.wait_answer(chat)
    sent = json.dumps(world.gateway.requests[-1])
    assert len(world.gateway.requests[-1]["messages"]) == 3, "the first turn is in it"
    assert "google.com" not in sent and "container" not in sent


def test_each_version_of_an_answer_keeps_its_own_suggestions(world: World) -> None:
    world.gateway.suggestions = ['<div class="container">for request {n}</div>']
    ada, chat, first = _searched(world)
    assert first["searchSuggestions"] == ['<div class="container">for request 1</div>']
    tried = ada.post(f"/api/chats/{chat}/messages/{first['id']}/retry")
    assert tried.status_code == 201, tried.text
    second = ada.wait_answer(chat)
    assert second["id"] != first["id"]
    assert second["searchSuggestions"] == ['<div class="container">for request 2</div>']
    old = ada.get(f"/api/chats/{chat}?via={first['id']}").json()["messages"][-1]
    assert old["id"] == first["id"]
    assert old["searchSuggestions"] == ['<div class="container">for request 1</div>']


def test_a_message_stored_before_schema_8_loads_without_suggestions(tmp_path: Path) -> None:
    path = tmp_path / "schema7.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V1_MESSAGES)
        for target in range(2, 8):
            for statement in storage._MIGRATIONS[target]:
                db.execute(statement)
        db.execute("UPDATE meta SET value = '7' WHERE key = 'schema'")

    async def go() -> dict:
        store = Store(path)
        await store.open()
        try:
            [message] = await store.messages("c1")
            await store.update_message("m1", search_suggestions=[GOOGLE])
            [saved] = await store.messages("c1")
            assert saved.search_suggestions == [GOOGLE]
            return message_view(message)
        finally:
            await store.close()

    assert asyncio.run(go())["searchSuggestions"] == []
