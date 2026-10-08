"""Kept versions of an answer: a chat is a tree (`workbench-answer-versions.md`)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.store import SCHEMA_VERSION, Message, Store

from .conftest import Browser, World
from .test_chats import _events
from .test_store_schema import _V1_MESSAGES
from .test_tools import Remote, decide, pending, remote, setup

__all__ = ["remote"]  # the fixture, for the tool transcript test

# --------------------------------------------------------------------------- #
# the store (§4)
# --------------------------------------------------------------------------- #


def _message(mid: str, role: str = "user") -> Message:
    return Message(id=mid, chat_id="c", seq=0, role=role, status="done", created_at=0.0)


def _ids(messages: list[Message]) -> list[str]:
    return [m.id for m in messages]


def _chosen_per_group(tree: list[Message]) -> dict[str | None, list[str]]:
    groups: dict[str | None, list[str]] = {}
    for m in tree:
        groups.setdefault(m.parent_id, [])
        if m.chosen:
            groups[m.parent_id].append(m.id)
    return groups


def _run(tmp_path: Path, steps: object) -> None:
    async def go() -> None:
        store = Store(tmp_path / "wb.sqlite3")
        await store.open()
        try:
            await steps(store)  # type: ignore[operator]
        finally:
            await store.close()

    asyncio.run(go())


def test_a_schema_5_chat_opens_as_the_same_conversation(tmp_path: Path) -> None:
    path = tmp_path / "wb.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V1_MESSAGES)
        for target in (2, 3, 4, 5):
            for statement in storage._MIGRATIONS[target]:
                db.execute(statement)
        db.execute("UPDATE meta SET value = '5' WHERE key = 'schema'")
        # Two chats, their rows interleaved: a parent is the row before in
        # the same chat, never another chat's.
        for mid, chat, seq, role in [
            ("k1-1", "k1", 1, "user"),
            ("k2-1", "k2", 1, "user"),
            ("k1-2", "k1", 2, "assistant"),
            ("k2-2", "k2", 2, "assistant"),
            ("k1-3", "k1", 3, "user"),
            ("k1-4", "k1", 4, "assistant"),
        ]:
            db.execute(
                "INSERT INTO messages (id, chat_id, seq, role, content, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'done', 1.0)",
                (mid, chat, seq, role, f"text of {mid}"),
            )

    async def steps(store: Store) -> None:
        tree = await store.tree("k1")
        assert [(m.id, m.parent_id, m.chosen) for m in tree] == [
            ("k1-1", None, True),
            ("k1-2", "k1-1", True),
            ("k1-3", "k1-2", True),
            ("k1-4", "k1-3", True),
        ]
        assert _ids(await store.messages("k1")) == ["k1-1", "k1-2", "k1-3", "k1-4"]
        assert [(m.id, m.parent_id) for m in await store.tree("k2")] == [
            ("k2-1", None),
            ("k2-2", "k2-1"),
        ]
        assert all(v["count"] == 1 for v in storage.versions(tree).values())

    _run(tmp_path, steps)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == str(
            SCHEMA_VERSION
        )


async def _branching(store: Store) -> None:
    """u1 → a1 → u2 → a2, then a1 tried again (a1b) and continued (u3 → a3)."""
    await store.add_message(_message("u1"), parent_id=None)
    await store.add_message(_message("a1", "assistant"), parent_id="u1")
    await store.add_message(_message("u2"), parent_id="a1")
    await store.add_message(_message("a2", "assistant"), parent_id="u2")
    await store.add_message(_message("a1b", "assistant"), parent_id="u1")
    await store.add_message(_message("u3"), parent_id="a1b")
    await store.add_message(_message("a3", "assistant"), parent_id="u3")


def test_each_group_of_versions_has_exactly_one_chosen(tmp_path: Path) -> None:
    async def steps(store: Store) -> None:
        await _branching(store)
        await store.add_message(_message("u1b"), parent_id=None)  # an edit of the first
        groups = _chosen_per_group(await store.tree("c"))
        assert all(len(chosen) == 1 for chosen in groups.values()), groups
        assert groups[None] == ["u1b"] and groups["u1"] == ["a1b"]
        tree = await store.tree("c")
        assert storage.versions(tree)["a1b"] == {"index": 2, "count": 2, "ids": ["a1", "a1b"]}
        assert storage.versions(tree)["u1b"]["index"] == 2

    _run(tmp_path, steps)


def test_the_path_follows_the_chosen_and_a_branch_remembers_where_it_was(
    tmp_path: Path,
) -> None:
    async def steps(store: Store) -> None:
        await _branching(store)
        assert _ids(await store.messages("c")) == ["u1", "a1b", "u3", "a3"]
        assert await store.choose("c", "a1")
        assert _ids(await store.messages("c")) == ["u1", "a1", "u2", "a2"]
        assert await store.choose("c", "a1b")
        assert _ids(await store.messages("c")) == ["u1", "a1b", "u3", "a3"]
        # A message off the path is chosen with the branch it is on.
        assert await store.choose("c", "u2")
        assert _ids(await store.messages("c")) == ["u1", "a1", "u2", "a2"]
        assert not await store.choose("c", "nope")
        groups = _chosen_per_group(await store.tree("c"))
        assert all(len(chosen) == 1 for chosen in groups.values()), groups

    _run(tmp_path, steps)


def test_the_path_never_holds_a_row_off_it_and_via_only_looks(tmp_path: Path) -> None:
    async def steps(store: Store) -> None:
        await _branching(store)
        before = [(m.id, m.chosen) for m in await store.tree("c")]
        shown = _ids(await store.messages("c"))
        assert not {"a1", "u2", "a2"} & set(shown)
        assert _ids(await store.messages("c", via="a1")) == ["u1", "a1", "u2", "a2"]
        assert _ids(await store.messages("c", via="u2")) == ["u1", "a1", "u2", "a2"]
        assert await store.messages("c", via="nope") == []
        assert [(m.id, m.chosen) for m in await store.tree("c")] == before

    _run(tmp_path, steps)


# --------------------------------------------------------------------------- #
# the API (§5, §6)
# --------------------------------------------------------------------------- #


def _two_turns(world: World) -> tuple[Browser, str, list[dict[str, Any]]]:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    for text in ("One", "Two"):
        ada.post(f"/api/chats/{chat}/messages", json={"content": text})
        ada.wait_answer(chat)
    return ada, chat, ada.get(f"/api/chats/{chat}").json()["messages"]


def _shown(ada: Browser, chat: str, via: str | None = None) -> list[dict[str, Any]]:
    query = f"?via={via}" if via else ""
    got = ada.get(f"/api/chats/{chat}{query}")
    assert got.status_code == 200, got.text
    return list(got.json()["messages"])


def _texts(messages: list[dict[str, Any]]) -> list[str]:
    return [m["content"] for m in messages]


def test_try_again_on_an_earlier_answer_starts_a_branch_and_keeps_the_old_one(
    world: World,
) -> None:
    ada, chat, before = _two_turns(world)
    first_answer = before[1]
    world.gateway.words = ["Another", " answer."]
    tried = ada.post(f"/api/chats/{chat}/messages/{first_answer['id']}/retry")
    assert tried.status_code == 201, tried.text
    ada.wait_answer(chat)
    shown = _shown(ada, chat)
    assert _texts(shown) == ["One", "Another answer."]
    assert shown[1]["versions"]["ids"] == [first_answer["id"], tried.json()["answer"]["id"]]
    # The model was sent the path up to the answer tried again, and no more.
    assert _texts(world.gateway.requests[-1]["messages"]) == ["One"]
    # The old branch is whole.
    old = _shown(ada, chat, via=first_answer["id"])
    assert [m["id"] for m in old] == [m["id"] for m in before]
    assert _texts(old) == _texts(before)


def test_an_edit_keeps_the_old_message_and_its_branch(world: World) -> None:
    ada, chat, before = _two_turns(world)
    edited = ada.post(f"/api/chats/{chat}/messages/{before[0]['id']}/edit", json={"content": "Uno"})
    assert edited.status_code == 201, edited.text
    ada.wait_answer(chat)
    assert _texts(_shown(ada, chat)) == ["Uno", "Hello from the model."]
    assert _texts(_shown(ada, chat, via=before[0]["id"])) == _texts(before)
    # The edit is a version of the first message; the old one is version 1.
    assert _shown(ada, chat)[0]["versions"]["ids"] == [before[0]["id"], edited.json()["user"]["id"]]


def test_via_only_looks_and_choose_saves(world: World) -> None:
    ada, chat, before = _two_turns(world)
    ada.post(f"/api/chats/{chat}/messages/{before[1]['id']}/retry")
    ada.wait_answer(chat)
    after = _shown(ada, chat)
    assert len(_shown(ada, chat, via=before[3]["id"])) == 4
    assert [m["id"] for m in _shown(ada, chat)] == [m["id"] for m in after], "via saved nothing"
    assert ada.get(f"/api/chats/{chat}?via=nope").status_code == 404
    chose = ada.post(f"/api/chats/{chat}/messages/{before[1]['id']}/choose")
    assert chose.status_code == 200, chose.text
    assert [m["id"] for m in chose.json()["messages"]] == [m["id"] for m in before]
    assert [m["id"] for m in _shown(ada, chat)] == [m["id"] for m in before]
    assert ada.post(f"/api/chats/{chat}/messages/nope/choose").status_code == 404
    bo = world.browser()
    bo.sign_in("p-bo")
    assert bo.post(f"/api/chats/{chat}/messages/{before[1]['id']}/choose").status_code == 404
    assert bo.post(f"/api/chats/{chat}/messages/{before[1]['id']}/retry").status_code == 404
    assert bo.get(f"/api/chats/{chat}?via={before[1]['id']}").status_code == 404


def test_choose_then_send_sends_the_chosen_path_and_nothing_else(
    world: World, remote: Remote
) -> None:
    """Each version ran its own tools; the next turn is sent only the
    transcript of the version shown."""
    _, ada, chat, _ = setup(world, remote)
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Use echo"})
    first = pending(ada, chat)
    assert decide(ada, chat, first, True).status_code == 204
    assert ada.wait_answer(chat)["status"] == "done"
    world.gateway.tool_arguments = '{"text":"other"}'
    assert ada.post(f"/api/chats/{chat}/messages/{first['id']}/retry").status_code == 201
    second = pending(ada, chat)
    assert second["id"] != first["id"]
    assert decide(ada, chat, second, True).status_code == 204
    assert ada.wait_answer(chat)["status"] == "done"
    assert remote.calls == ["hello", "other"], "each version made its own call"
    assert ada.post(f"/api/chats/{chat}/messages/{first['id']}/choose").status_code == 200
    world.gateway.mode = "answer"
    ada.post(f"/api/chats/{chat}/messages", json={"content": "What happened?"})
    ada.wait_answer(chat)
    sent = world.gateway.requests[-1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "tool", "assistant", "user"]
    assert sent[2]["content"] == 'hello\n{"result": "hello"}'
    assert "other" not in json.dumps(sent)
    shown = _shown(ada, chat)
    assert [m["id"] for m in shown[:2]] == [shown[0]["id"], first["id"]]


def _running(ada: Browser, chat: str) -> dict[str, Any]:
    deadline = time.perf_counter() + 10
    while time.perf_counter() < deadline:
        last = _shown(ada, chat)[-1]
        if last["status"] == "running" and last["content"]:
            return last
        time.sleep(0.05)
    raise AssertionError("no answer started")


def test_choose_is_refused_while_an_answer_runs(world: World) -> None:
    ada, chat, before = _two_turns(world)
    world.gateway.delay = 0.3
    ada.post(f"/api/chats/{chat}/messages/{before[3]['id']}/retry")
    _running(ada, chat)
    refused = ada.post(f"/api/chats/{chat}/messages/{before[3]['id']}/choose")
    assert refused.status_code == 409
    assert "Wait for it, or stop it" in refused.json()["detail"]["message"]
    assert _shown(ada, chat)[-1]["status"] == "running", "the running answer is still shown"


def test_try_again_while_an_answer_runs_keeps_it_as_a_stopped_version(world: World) -> None:
    world.gateway.delay = 0.3
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Hi"})
    running = _running(ada, chat)
    world.gateway.delay = 0.0
    tried = ada.post(f"/api/chats/{chat}/messages/{running['id']}/retry")
    assert tried.status_code == 201, tried.text
    done = ada.wait_answer(chat)
    assert done["id"] == tried.json()["answer"]["id"] and done["status"] == "done"
    assert done["versions"]["ids"] == [running["id"], done["id"]]
    (kept,) = [m for m in _shown(ada, chat, via=running["id"]) if m["id"] == running["id"]]
    assert kept["status"] == "stopped" and kept["content"]
    assert kept["content"] != "Hello from the model."


def test_every_tab_is_told_the_path_changed(world: World) -> None:
    ada, chat, before = _two_turns(world)
    for act in ("retry", "choose", "edit"):
        seen: list[dict[str, Any]] = []
        tab = _events(world, ada, chat, seen, until="path")
        target = before[0] if act == "edit" else before[1]
        body = {"content": "Uno"} if act == "edit" else None
        answer = ada.post(f"/api/chats/{chat}/messages/{target['id']}/{act}", json=body)
        assert answer.status_code in (200, 201), answer.text
        tab.join(timeout=10)
        assert seen and seen[-1] == {"type": "path"}, (act, seen)
        ada.wait_answer(chat)
