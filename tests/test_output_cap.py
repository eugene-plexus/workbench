"""A length stop names the setting that made it (2026-10-10).

A Strata answer stopped mid-thought at 4,096 tokens, and Workbench told Troy
to raise his chat's own setting; the cap was his install's. The gateway now
says what it sent and where it came from (`x_eugene_plexus.output_cap`), and
the answer keeps it.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.answers import message_view
from eugene_plexus_workbench.store import Store

from .conftest import World
from .test_store_schema import _V1_MESSAGES

INSTALL = {"tokens": 4096, "source": "install"}


def _answered(world: World) -> tuple[object, str, dict]:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Think hard."})
    return ada, chat, ada.wait_answer(chat)


def test_the_cap_is_kept_with_the_answer_it_stopped(world: World) -> None:
    world.gateway.finish = "length"
    world.gateway.output_cap = dict(INSTALL)
    ada, chat, answer = _answered(world)
    assert answer["finish"] == "length"
    assert answer["outputCap"] == INSTALL
    world.restart_workbench()
    ada.sign_in("p-ada")
    assert ada.get(f"/api/chats/{chat}").json()["messages"][-1]["outputCap"] == INSTALL


def test_no_cap_said_is_none(world: World) -> None:
    _, _, answer = _answered(world)
    assert answer["outputCap"] is None


def test_a_cap_in_another_shape_is_not_kept(world: World) -> None:
    world.gateway.output_cap = {"tokens": "4096", "source": "somewhere"}
    _, _, answer = _answered(world)
    assert answer["outputCap"] is None


def test_a_message_stored_before_schema_9_loads_without_a_cap(tmp_path: Path) -> None:
    path = tmp_path / "schema8.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V1_MESSAGES)
        for target in range(2, 9):
            for statement in storage._MIGRATIONS[target]:
                db.execute(statement)
        db.execute("UPDATE meta SET value = '8' WHERE key = 'schema'")

    async def go() -> dict:
        store = Store(path)
        await store.open()
        try:
            [message] = await store.messages("c1")
            await store.update_message("m1", output_cap=dict(INSTALL))
            [saved] = await store.messages("c1")
            assert saved.output_cap == INSTALL
            return message_view(message)
        finally:
            await store.close()

    assert asyncio.run(go())["outputCap"] is None
