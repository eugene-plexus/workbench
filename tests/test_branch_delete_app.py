"""Row 28 high-risk acceptance: HTTP, real app, fake gateway and MCP memory server."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench.store import MemoryNote

from .conftest import World
from .test_branch_delete import DAY, MARK
from .test_memory_servers import connected, remote, until
from .test_person_removal import _holding

__all__ = ["remote"]


@pytest.fixture(autouse=True)
def clear_eugene_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("EUGENE_PLEXUS_"):
            monkeypatch.delenv(key)


def on_app(world: World, work: Coroutine[Any, Any, Any]) -> Any:
    state = world.server.server.config.app.state
    loop = state.memory_courier._task.get_loop()
    return asyncio.run_coroutine_threadsafe(work, loop).result(30)


def test_branch_delete_app(world: World, remote: Any) -> None:
    kept_text = "A preference deliberately kept"
    assert world.server.port not in range(8079, 8084)
    assert not any(k.startswith("EUGENE_PLEXUS_") for k in os.environ)
    memory = remote()
    ada = connected(world, memory)
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "First question"})
    first = ada.wait_answer(chat)
    first_user = ada.get(f"/api/chats/{chat}").json()["messages"][0]["id"]
    page = f"<h1>{MARK}</h1>"
    world.gateway.words = [f"```html\n{page}\n```"]
    tried = ada.post(f"/api/chats/{chat}/messages/{first['id']}/retry")
    assert tried.status_code == 201
    second = ada.wait_answer(chat)
    mid = second["id"]
    artifact = ada.post(
        f"/api/chats/{chat}/messages/{mid}/artifacts",
        json={"kind": "html", "title": MARK, "original": page, "source": page + MARK},
    )
    assert artifact.status_code == 201, artifact.text
    upload = ada.post(
        f"/api/chats/{chat}/files", files={"file": (f"{MARK}.txt", MARK.encode(), "text/plain")}
    )
    assert upload.status_code == 201, upload.text
    fid = upload.json()["id"]
    ada.wait_read(chat, fid)
    for i in range(2):
        sent = ada.post(
            f"/api/chats/{chat}/messages",
            json={"content": f"{MARK} turn {i}", "attachments": [fid] if i == 0 else []},
        )
        assert sent.status_code == 201, sent.text
        ada.wait_answer(chat)
    state = world.server.server.config.app.state
    until(lambda: any(MARK in r["content"] for r in memory.retained))
    branch_messages = ada.get(f"/api/chats/{chat}").json()["messages"][1:]
    branch_ids = {m["id"] for m in branch_messages}
    owed = {
        r["document_id"]
        for r in memory.retained
        if r["document_id"].rsplit(":", 1)[-1] in branch_ids
    }
    # A third sibling stays byte-identical, except its intentional chosen flag.
    world.gateway.words = ["Third answer"]
    third = ada.post(f"/api/chats/{chat}/messages/{mid}/retry").json()["answer"]
    ada.wait_answer(chat)
    assert ada.post(f"/api/chats/{chat}/messages/{mid}/choose").status_code == 200
    later = ada.new_chat()
    ada.post(f"/api/chats/{later}/messages", json={"content": "Unrelated question"})
    later_answer = ada.wait_answer(later)

    async def seed_related() -> None:
        for name, status in (("pending", "pending"), ("kept", "active")):
            words = MARK if status == "pending" else kept_text
            await state.store.add_memory_note(
                MemoryNote(
                    name,
                    "p-ada",
                    f"{name} {words}",
                    "fact",
                    0,
                    0,
                    status=status,
                    source_chat_id=chat,
                    source_message_id=mid,
                    quote=words,
                    body=words,
                ),
                limit=100,
            )
        await state.notes_index.put("p-ada", ["kept"])
        await state.store.update_message(
            later_answer["id"],
            memory_used={
                "pastChats": {"exchanges": [{"chatId": chat, "messageId": mid, "text": MARK}]},
                "notes": [{"id": "kept", "text": kept_text}],
            },
            tool_rounds=[
                {
                    "calls": [
                        {
                            "serverId": "past-chats",
                            "chats": [chat],
                            "result": MARK,
                            "content": [{"text": MARK}],
                        }
                    ]
                }
            ],
        )
        await state.exchanges_index.sync_chat("p-ada", chat)

        # Include both a run wholly in the branch and a shared projection.
        def records() -> None:
            db = state.store._conn()
            for run, messages in (
                ("gone", [{"id": mid, "content": MARK}]),
                (
                    "shared",
                    [{"id": mid, "content": MARK}, {"id": first["id"], "content": "First answer"}],
                ),
            ):
                data = {
                    "messages": messages,
                    "seedIds": [],
                    "answerId": mid if run == "gone" else first["id"],
                    "state": {"secret": MARK},
                }
                db.execute(
                    "INSERT INTO agui_runs VALUES (?,?,?,?,?,?,?)",
                    (chat, run, "p-ada", "done", 1, 1, json.dumps(data)),
                )
                db.execute(
                    "INSERT INTO agui_events VALUES (?,?,?,?)",
                    (
                        chat,
                        run,
                        1,
                        json.dumps(
                            {"type": "TEXT_MESSAGE_CONTENT", "messageId": mid, "delta": MARK}
                        ),
                    ),
                )
            for suffix in (".schema-28.bak", ".schema-27.1.bak"):
                copy = sqlite3.connect(str(state.store.path) + suffix)
                try:
                    db.backup(copy)
                finally:
                    copy.close()
            Path(str(state.store.path) + ".schema-28.bak.part").write_bytes(MARK.encode())

        await state.store._run(records)

    on_app(world, seed_related())
    kept = on_app(world, state.store.memory_note("p-ada", "kept", with_body=True))
    pending = on_app(world, state.store.memory_note("p-ada", "pending", with_body=True))
    untouched_documents = set(memory.memories) - owed
    exported = ada.post("/api/me/export")
    assert exported.status_code == 202
    until(lambda: ada.get("/api/exports").json()["exports"][0]["state"] == "ready")
    before_other = ada.get(f"/api/chats/{chat}?via={third['id']}").json()["messages"][-1]
    summary = ada.get(f"/api/chats/{chat}/messages/{mid}/branch")
    assert summary.status_code == 200
    assert summary.json()["messages"] == 5
    assert summary.json()["files"] == 1 and summary.json()["artifacts"] == 1
    assert summary.json()["pendingNotes"] == 1 and summary.json()["keptNotes"] == 1
    removed = ada.delete(f"/api/chats/{chat}/messages/{mid}")
    assert removed.status_code == 200, removed.text
    assert [m["id"] for m in removed.json()["messages"]] == [first_user, first["id"]]
    assert ada.get(f"/api/files/{fid}").status_code == 404
    assert ada.get(f"/api/chats/{chat}?via={mid}").status_code == 404
    assert MARK not in ada.get(f"/api/chats/{later}").text
    assert kept_text in ada.get(f"/api/chats/{later}").text
    assert on_app(world, state.store.memory_note("p-ada", "kept", with_body=True)) == kept
    assert on_app(world, state.store.memory_note("p-ada", "pending")) is None
    assert "kept" in on_app(world, state.saved.index("p-ada").note_digests())
    assert owed <= set(memory.memories)  # Undo has not expired.
    restored = ada.post(f"/api/chats/{chat}/messages/{mid}/undo")
    assert restored.status_code == 200, restored.text
    assert {m["id"] for m in restored.json()["messages"][1:]} == branch_ids
    assert ada.get(f"/api/files/{fid}").status_code == 200
    assert on_app(world, state.store.memory_note("p-ada", "pending", with_body=True)) == pending
    assert on_app(world, state.store.memory_note("p-ada", "kept", with_body=True)) == kept
    assert ada.delete(f"/api/chats/{chat}/messages/{mid}").status_code == 200
    world.restart_workbench()
    state = world.server.server.config.app.state
    state.branches.clock = lambda: time.time() + 31 * DAY
    on_app(world, state.branches.due())
    assert on_app(world, state.store.deleted_branches(chat)) == []
    assert owed <= set(memory.forgotten)
    assert not owed & set(memory.memories)
    assert untouched_documents <= set(memory.memories)
    assert memory.purged == 0
    kept.source_message_id = None
    assert on_app(world, state.store.memory_note("p-ada", "kept", with_body=True)) == kept
    assert on_app(world, state.store.memory_note("p-ada", "pending")) is None
    assert "kept" in on_app(world, state.saved.index("p-ada").note_digests())
    assert kept_text in ada.get(f"/api/chats/{later}").text
    after_other = ada.get(f"/api/chats/{chat}?via={third['id']}").json()["messages"][-1]
    before_other.pop("versions")
    after_other.pop("versions")
    assert MARK not in json.dumps(after_other["memoryUsed"])
    before_other.pop("memoryUsed")
    after_other.pop("memoryUsed")
    assert after_other == before_other
    world.server.stop()
    with sqlite3.connect(f"file:{state.store.path.as_posix()}?mode=ro", uri=True) as db:
        assert not branch_ids & {r[0] for r in db.execute("SELECT id FROM messages")}
        assert db.execute("SELECT COUNT(*) FROM agui_runs WHERE run_id='shared'").fetchone()[0] == 1
    for copy in world.data.rglob("*.bak"):
        with sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True) as db:
            assert db.execute(
                "SELECT id, source_message_id FROM memory_notes WHERE id IN ('kept', 'pending')"
            ).fetchall() == [("kept", None)]
    assert _holding(world.data, MARK) == []


def test_undo_index_failure_keeps_a_recovery_record(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eugene_plexus_workbench import branch_index

    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Question"})
    answer = ada.wait_answer(chat)
    state = world.server.server.config.app.state
    on_app(world, state.exchanges_index.sync_chat("p-ada", chat))
    path = f"/api/chats/{chat}/messages/{answer['id']}"
    assert ada.delete(path).status_code == 200
    original = branch_index.restore

    async def failed(*args: Any, **kwargs: Any) -> None:
        raise OSError("document is locked")

    monkeypatch.setattr(branch_index, "restore", failed)
    assert ada.post(path + "/undo").status_code == 503
    state = world.server.server.config.app.state
    records = on_app(world, state.store.deleted_branches(chat))
    assert records[0]["state"] == "restoring"
    monkeypatch.setattr(branch_index, "restore", original)
    on_app(world, state.branches.due())
    assert on_app(world, state.store.deleted_branches(chat)) == []
    assert ada.get(f"/api/chats/{chat}").json()["messages"][-1]["id"] == answer["id"]
