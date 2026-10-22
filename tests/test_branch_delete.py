"""Row 28: recover for 30 days, then erase only the deleted branch."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.interaction_store import InteractionError
from eugene_plexus_workbench.store import Chat, FileRecord, MemoryNote, Message, Person, Store

from .conftest import World
from .test_memory_api import config
from .test_person_removal import _holding
from .test_versions import _shown, _two_turns

MARK = "zqxtbranchsecretmarker"
DAY = 86400


async def seed(store: Store) -> None:
    await store.upsert_person(Person("p", "Person", "member"))
    await store.create_chat(Chat("c", "p", "Chat", None, 0, 0))
    for mid, parent, role, text in [
        ("u", None, "user", "Question"),
        ("a1", "u", "assistant", "First"),
        ("a2", "u", "assistant", MARK),
        ("u2", "a2", "user", MARK),
        ("a4", "u2", "assistant", MARK),
        ("a3", "u", "assistant", "Third"),
    ]:
        await store.add_message(
            Message(mid, "c", 0, role, "done", 0, content=text), parent_id=parent
        )
    await store.choose("c", "a2")


@pytest.mark.asyncio
async def test_delete_leaves_nothing_of_the_branch(tmp_path: Path) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)
        await store.add_file(
            FileRecord("f-secret", "p", "c", MARK, "text/plain", 1, 0, message_id="a2")
        )
        await store.add_memory_note(
            MemoryNote(
                "note",
                "p",
                MARK,
                "fact",
                0,
                0,
                status="pending",
                source_message_id="a2",
                source_chat_id="c",
            ),
            limit=100,
        )
        before = {m.id: asdict(m) for m in await store.tree("c") if m.id in {"u", "a1", "a3"}}
        await store.close()
        with (
            sqlite3.connect(store.path) as db,
            sqlite3.connect(str(store.path) + ".schema-28.bak") as backup,
        ):
            db.backup(backup)
        store = Store(store.path)
        await store.open()
        deleted = await store.delete_branch("c", "a2", now=0)
        assert deleted["deleteAt"] == 30 * DAY
        assert [m.id for m in await store.messages("c")] == ["u", "a1"]
        assert await store.message("a2") is None
        await store.purge_branch("a2", now=30 * DAY)
        assert _holding(tmp_path, MARK) == []
        after = {m.id: asdict(m) for m in await store.tree("c")}
        # The chosen flag is the only intentional change to the surviving version.
        before["a1"]["chosen"] = True
        assert before == after
        assert await store.memory_note("p", "note") is None
        assert await store.file("f-secret") is None
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["delete", "undo", "purge"])
async def test_branch_removal_preserves_kept_notes(tmp_path: Path, action: str) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)
        for name, status in (("pending", "pending"), ("kept", "active")):
            await store.add_memory_note(
                MemoryNote(
                    name,
                    "p",
                    f"{name} note",
                    "fact",
                    0,
                    0,
                    status=status,
                    source_message_id="a2",
                    source_chat_id="c",
                    body=f"{name} body",
                    tags=[name],
                ),
                limit=100,
            )
        pending = await store.memory_note("p", "pending", with_body=True)
        kept = await store.memory_note("p", "kept", with_body=True)
        summary = await store.branch_summary("c", "a2")
        assert summary["pendingNotes"] == 1 and summary["keptNotes"] == 1
        if action == "purge":

            def backup() -> None:
                with sqlite3.connect(str(store.path) + ".schema-28.bak") as copy:
                    store._conn().backup(copy)

            await store._run(backup)
        await store.delete_branch("c", "a2", now=0)
        if action == "undo":
            # Kept notes remain editable, and Undo must not replace their newer words.
            assert await store.update_memory_note("p", "kept", text="Edited kept note")
            kept = await store.memory_note("p", "kept", with_body=True)
            assert await store.undo_branch("c", "a2", now=1)
        elif action == "purge":
            assert await store.purge_branch("a2", now=30 * DAY)
            kept.source_message_id = None
            with sqlite3.connect(str(store.path) + ".schema-28.bak") as copy:
                assert copy.execute(
                    "SELECT id, source_message_id FROM memory_notes"
                ).fetchall() == [("kept", None)]
        assert await store.memory_note("p", "kept", with_body=True) == kept
        assert await store.memory_note("p", "pending", with_body=True) == (
            pending if action == "undo" else None
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_undo_restores_the_whole_branch_after_restart(tmp_path: Path) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)
        before = [asdict(m) for m in await store.tree("c")]
        await store.delete_branch("c", "a2", now=10)
        await store.close()
        store = Store(store.path)
        await store.open()
        assert await store.undo_branch("c", "a2", now=11)
        assert [asdict(m) for m in await store.tree("c")] == before
        await store.delete_branch("c", "a2", now=12)
        assert not await store.undo_branch("c", "a2", now=12 + 30 * DAY)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_shared_attachment_survives_deleting_its_edit(tmp_path: Path) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)
        await store.add_file(FileRecord("shared", "p", "c", "shared.txt", "text/plain", 1, 0))
        for mid in ("a1", "a2"):
            await store.update_message(mid, attachments=["shared"])
        await store.delete_branch("c", "a2", now=0)
        await store.purge_branch("a2", now=30 * DAY)
        assert await store.file("shared") is not None
    finally:
        await store.close()


def test_delete_refused_while_running(world: World) -> None:
    ada, chat, messages = _two_turns(world)
    world.gateway.delay = 2
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Running"})
    refused = ada.delete(f"/api/chats/{chat}/messages/{messages[1]['id']}")
    assert refused.status_code == 409


def test_index_failure_deletes_nothing(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    ada, chat, messages = _two_turns(world)

    async def failed(*args: Any, **kwargs: Any) -> None:
        raise OSError("index unavailable")

    monkeypatch.setattr(
        world.server.server.config.app.state.exchanges_index, "remove_exchanges", failed
    )
    before = _shown(ada, chat)
    got = ada.delete(f"/api/chats/{chat}/messages/{messages[1]['id']}")
    assert got.status_code == 503
    assert _shown(ada, chat) == before


def test_delete_from_here_on_single_version(world: World) -> None:
    ada, chat, messages = _two_turns(world)
    path = f"/api/chats/{chat}/messages/{messages[2]['id']}"
    summary = ada.get(path + "/branch")
    assert summary.status_code == 200
    assert summary.json()["messages"] == 2
    got = ada.delete(path)
    assert got.status_code == 200
    assert len(got.json()["messages"]) == 2
    assert got.json()["deletedBranches"][0]["id"] == messages[2]["id"]
    assert ada.post(path + "/undo").status_code == 200
    assert len(_shown(ada, chat)) == 4
    assert ada.delete(f"/api/chats/{chat}/messages/{messages[0]['id']}").status_code == 409


def test_name_round_trips_and_hidden_name_hidden(world: World) -> None:
    ada, chat, messages = _two_turns(world)
    path = f"/api/chats/{chat}/messages/{messages[1]['id']}/name"
    got = ada.put(path, json={"name": "Shorter tone"})
    assert got.status_code == 200
    assert got.json()["versions"]["names"] == ["Shorter tone"]
    assert _shown(ada, chat)[1]["branchName"] == "Shorter tone"
    for invalid in ("", "x" * 61, "bad\nname", "bad\x7fname"):
        assert ada.put(path, json={"name": invalid}).status_code == 422
    assert ada.put(path, json={"name": None}).status_code == 200
    owner = world.browser()
    owner.sign_in("operator")
    assert owner.put(path, json={"name": "No"}).status_code == 403
    assert owner.delete(path.removesuffix("/name")).status_code == 403
    assert config(world, ownerReadsChats=True).status_code == 200
    assert ada.put(path, json={"name": "Hidden name"}).status_code == 200
    state = world.server.server.config.app.state
    asyncio.run(
        state.store.update_message(
            messages[1]["id"],
            tool_rounds=[{"calls": [{"jobSite": True, "mode": "production", "site": "node"}]}],
        )
    )
    hidden = owner.get(f"/api/chats/{chat}").json()["messages"][1]
    assert hidden["redacted"]
    assert hidden["versions"]["names"] == [None]
    assert not hidden.get("branchName")


def test_migration_adds_branch_name(tmp_path: Path) -> None:
    assert storage.SCHEMA_VERSION == 29
    assert set(storage._MIGRATIONS) == set(range(storage.OLDEST_SCHEMA + 1, 30))

    async def go() -> None:
        store = Store(tmp_path / "wb.sqlite3")
        await store.open()
        await store.close()
        with sqlite3.connect(store.path) as db:
            db.execute("DROP TABLE deleted_branches")
            db.execute("ALTER TABLE messages DROP COLUMN branch_name")
            db.execute("UPDATE meta SET value = '28' WHERE key = 'schema'")
        store = Store(store.path)
        await store.open()
        await seed(store)
        assert await store.name_version("c", "a2", "Name")
        assert (await store.message("a2")).branch_name == "Name"
        await store.close()

    asyncio.run(go())


@pytest.mark.asyncio
async def test_shared_file_survives_sequential_hides_and_undo(tmp_path: Path) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)
        await store.add_file(FileRecord("shared", "p", "c", "shared.txt", "text/plain", 1, 0))
        for mid in ("a1", "a2"):
            await store.update_message(mid, attachments=["shared"])
        await store.delete_branch("c", "a1", now=0)
        await store.delete_branch("c", "a2", now=1)
        assert await store.file("shared") is None
        assert await store.undo_branch("c", "a1", now=2)
        assert await store.file("shared") is not None
        await store.purge_branch("a2", now=31 * DAY)
        assert await store.file("shared") is not None
        await store.delete_branch("c", "a1", now=3)
        await store.purge_branch("a1", now=32 * DAY)
        assert await store.file("shared") is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_nested_hides_keep_their_original_deadlines(tmp_path: Path) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)
        child_mark = "qzxtuniquenestedchild"
        for mid in ("u2", "a4"):
            await store.update_message(mid, content=child_mark)
        await store.add_file(
            FileRecord("child-file", "p", "c", child_mark, "text/plain", 1, 0, message_id="u2")
        )
        await store.update_message("u2", attachments=["child-file"])
        await store.delete_branch("c", "u2", now=0)
        await store.delete_branch("c", "a2", now=29 * DAY)
        rows = await store.deleted_branches("c")
        assert {r["id"]: r["delete_at"] for r in rows} == {"u2": 30 * DAY, "a2": 59 * DAY}
        await store.purge_branch("u2", now=30 * DAY)
        assert _holding(tmp_path, child_mark) == []
        assert await store.undo_branch("c", "a2", now=31 * DAY)
        assert await store.message("u2") is None
        assert await store.message("a4") is None
        assert await store.file("child-file") is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_undo_after_new_notes_and_versions_has_one_chosen(tmp_path: Path) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)
        await store.add_memory_note(
            MemoryNote("oldnote", "p", "Old note", "fact", 0, 0, source_message_id="a2"), limit=100
        )
        await store.delete_branch("c", "a2", now=0)
        await store.add_memory_note(MemoryNote("newnote", "p", "New note", "fact", 0, 0), limit=100)
        newer = await store.add_message(
            Message("a-new", "c", 0, "assistant", "done", 0), parent_id="u"
        )
        assert newer.seq > 6
        assert await store.undo_branch("c", "a2", now=1)
        assert len([m for m in await store.tree("c") if m.parent_id == "u" and m.chosen]) == 1
        assert await store.memory_note("p", "oldnote") is not None
        assert await store.memory_note("p", "newnote") is not None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_failed_rewrite_is_recovered_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    await seed(store)
    await store.delete_branch("c", "a2", now=0)
    rewrite = storage._rewrite

    def fail(db: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("checkpoint is busy")

    monkeypatch.setattr(storage, "_rewrite", fail)
    with pytest.raises(sqlite3.OperationalError, match="checkpoint is busy"):
        await store.purge_branch("a2", now=30 * DAY)
    assert await store.setting("branchRewrite") is True
    await store.close()
    monkeypatch.setattr(storage, "_rewrite", rewrite)
    store = Store(store.path)
    await store.open()
    await store.finish_branch_rewrite()
    assert await store.setting("branchRewrite") is None
    assert _holding(tmp_path, MARK) == []
    await store.close()


@pytest.mark.asyncio
async def test_deleting_the_chat_does_not_cancel_its_branch_purge(tmp_path: Path) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)
        await store.delete_branch("c", "a2", now=0)
        await store.delete_chat("c")
        assert len(await store.deleted_branches()) == 1
        assert not await store.undo_branch("c", "a2", now=1)
        assert await store.purge_branch("a2", now=30 * DAY)
        assert await store.deleted_branches() == []
        assert _holding(tmp_path, MARK) == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_stale_send_cannot_attach_to_a_deleted_parent(tmp_path: Path) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)
        await store.delete_branch("c", "a2", now=0)
        with pytest.raises(InteractionError, match="deleted"):
            await store.add_message(Message("late", "c", 0, "user", "done", 0), parent_id="a2")
        assert await store.message("late") is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_a_running_answer_cannot_write_forgotten_excerpts_back(tmp_path: Path) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)
        await store.create_chat(Chat("later", "p", "Later", None, 0, 0))
        stale = {"pastChats": {"exchanges": [{"chatId": "c", "messageId": "a2", "text": MARK}]}}
        await store.add_message(
            Message("running", "later", 0, "assistant", "running", 0), parent_id=None
        )
        await store.update_message("running", memory_used=stale)
        await store.delete_branch("c", "a2", now=1)
        await store.purge_branch("a2", now=31 * DAY)
        await store.update_message(
            "running",
            memory_used=stale,
            tool_rounds=[{"calls": [{"serverId": "past-chats", "chats": ["c"], "result": MARK}]}],
        )
        saved = await store.message("running")
        assert MARK not in str(asdict(saved))
        assert _holding(tmp_path, MARK) == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_a_busy_wal_never_reports_a_completed_true_delete(tmp_path: Path) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    reader = sqlite3.connect(store.path)
    try:
        await seed(store)
        await store.delete_branch("c", "a2", now=0)
        reader.execute("BEGIN")
        reader.execute("SELECT payload FROM deleted_branches").fetchall()
        with pytest.raises(sqlite3.OperationalError, match="reader"):
            await store.purge_branch("a2", now=30 * DAY)
        assert await store.setting("branchRewrite") is True
        reader.rollback()
        await store.finish_branch_rewrite()
        assert _holding(tmp_path, MARK) == []
    finally:
        reader.close()
        await store.close()


@pytest.mark.asyncio
async def test_purge_scrubs_an_attachment_present_only_in_an_old_backup(tmp_path: Path) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)
        await store.add_file(
            FileRecord("historical", "p", "c", "qzxthistoricalfile", "text/plain", 1, 0)
        )
        await store.update_message("a2", attachments=["historical"])

        def backup() -> None:
            copy = sqlite3.connect(str(store.path) + ".schema-28.bak")
            try:
                store._conn().backup(copy)
            finally:
                copy.close()

        await store._run(backup)
        await store.update_message("a2", attachments=[])
        await store.delete_files(["historical"])
        await store.delete_branch("c", "a2", now=0)
        await store.purge_branch("a2", now=30 * DAY)
        assert _holding(tmp_path, "qzxthistoricalfile") == []
    finally:
        await store.close()


def test_a_stale_confirmation_deletes_nothing(world: World) -> None:
    ada, chat, messages = _two_turns(world)
    path = f"/api/chats/{chat}/messages/{messages[1]['id']}"
    token = ada.get(path + "/branch").json()["token"]
    ada.post(f"/api/chats/{chat}/messages", json={"content": "A new turn"})
    ada.wait_answer(chat)
    before = _shown(ada, chat)
    assert ada.delete(path + f"?expected={token}").status_code == 409
    assert _shown(ada, chat) == before


def test_index_rewrite_failure_restores_documents(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eugene_plexus_workbench import branch_index

    ada, chat, messages = _two_turns(world)
    state = world.server.server.config.app.state

    async def note() -> None:
        await state.store.add_memory_note(
            MemoryNote(
                "source-note", "p-ada", MARK, "fact", 0, 0, source_message_id=messages[1]["id"]
            ),
            limit=100,
        )
        await state.notes_index.put("p-ada", ["source-note"])

    asyncio.run(note())
    original = branch_index._rewrite
    calls = 0

    def fail_once(db: sqlite3.Connection) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("checkpoint is busy")
        original(db)

    monkeypatch.setattr(branch_index, "_rewrite", fail_once)
    before = _shown(ada, chat)
    assert ada.delete(f"/api/chats/{chat}/messages/{messages[1]['id']}").status_code == 503
    assert _shown(ada, chat) == before
    assert not ada.get(f"/api/chats/{chat}").json()["deletedBranches"]
    assert "source-note" in asyncio.run(state.saved.index("p-ada").note_digests())


@pytest.mark.asyncio
@pytest.mark.parametrize("external", [False, True])
async def test_agui_copies_cannot_survive_or_return_from_another_undo(
    tmp_path: Path, external: bool
) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)

        def projection() -> None:
            db = store._conn()
            data = {
                "direction": "connect" if external else "serve",
                "answerId": "a2",
                "seedIds": [],
                "messages": [{"id": "external-id" if external else "a2", "content": MARK}],
                "state": {"text": MARK},
            }
            if not external:
                data["messages"].append({"id": "a1", "content": "First"})
            db.execute(
                "INSERT INTO agui_runs VALUES ('c','mixed','p','done',2,2,?)", (json.dumps(data),)
            )
            for seq, event in enumerate(
                (
                    {"type": "STATE_SNAPSHOT", "snapshot": {"text": MARK}},
                    {"type": "CUSTOM", "name": "payload", "value": {"text": MARK}},
                ),
                1,
            ):
                db.execute(
                    "INSERT INTO agui_events VALUES ('c','mixed',?,?)", (seq, json.dumps(event))
                )

        await store._run(projection)
        if not external:
            await store.delete_branch("c", "a1", now=0)
        await store.delete_branch("c", "a2", now=1)
        await store.purge_branch("a2", now=31 * DAY)
        assert _holding(tmp_path, MARK) == []
        if not external:
            assert await store.undo_branch("c", "a1", now=2)
            assert _holding(tmp_path, MARK) == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_identical_words_in_another_persons_memory_stay(tmp_path: Path) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)
        await store.update_message("a2", content="a")
        await store.upsert_person(Person("other", "Other", "member"))
        await store.create_chat(Chat("other-chat", "other", "Other", None, 0, 0))
        await store.add_message(
            Message("other-message", "other-chat", 0, "assistant", "done", 0), parent_id=None
        )
        await store.update_message(
            "other-message", memory_used={"memoryServer": {"memories": [{"text": "a"}]}}
        )
        before = asdict(await store.message("other-message"))
        await store.delete_branch("c", "a2", now=0)
        await store.purge_branch("a2", now=30 * DAY)
        assert asdict(await store.message("other-message")) == before
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_legacy_server_excerpts_pause_purge_without_guessing(tmp_path: Path) -> None:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    try:
        await seed(store)
        await store.create_chat(Chat("later", "p", "Later", None, 0, 0))
        await store.add_message(
            Message("legacy", "later", 0, "assistant", "done", 0), parent_id=None
        )
        await store.update_message(
            "legacy",
            memory_used={
                "memoryServer": {
                    "memories": [{"text": "Independent words with no source identity"}]
                }
            },
        )
        before = asdict(await store.message("legacy"))
        await store._run(
            lambda: store._conn().execute(
                "INSERT INTO memory_server_sent (document_id,owner,chat_id,message_id,state,created_at,next_at) VALUES ('c:a2','p','c','a2','sent',0,0)"
            )
        )
        await store.delete_branch("c", "a2", now=0)
        with pytest.raises(InteractionError, match="no source ID"):
            await store.purge_branch("a2", now=30 * DAY)
        assert await store.deleted_branches()
        assert asdict(await store.message("legacy")) == before
    finally:
        await store.close()
