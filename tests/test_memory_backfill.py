"""Row 27a: explicit past-chat sending, using the live policy and a durable cursor."""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import config
from eugene_plexus_workbench import memory_servers as ms
from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.store import Chat, Message, Store


class Remote:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call(self, server: Any, mode: str, verb: str, args: Any, *rest: Any) -> Any:
        self.calls.append({"verb": verb, **args})
        return {}


@pytest.fixture
async def queued(tmp_path: Path):
    store = Store(tmp_path / "queue.sqlite3")
    await store.open()
    for i in range(12):
        await seed(store, f"past-{i:02}")
    await store.put_memory_server(
        "ada", "https://chosen.example", "", "Chosen", {"retain": "retain", "forget": "forget"}
    )
    feeder = ms.Backfill(store)
    await feeder.start("ada")
    await feeder.top_up("ada")
    try:
        yield store, feeder
    finally:
        await store.close()


async def test_stop_resumes_and_new_address_ends_the_send(queued) -> None:
    store, feeder = queued

    assert len(await store.memory_server_due(9999999999)) == 20
    remote = Remote()
    courier = ms.Courier(store, remote)
    await feeder.stop("ada")
    await courier.once()
    assert remote.calls == []
    await feeder.start("ada")
    await courier.once()
    assert len(remote.calls) == 20
    await store.put_memory_server(
        "ada", "https://different.example", "", "Different", {"retain": "retain"}
    )
    await courier.once()
    assert len(remote.calls) == 20 and not (await store.memory_server("ada"))["backfill"]


async def test_live_exchange_preempts_backfill_between_sends(queued) -> None:
    store, _ = queued
    await seed(store, "live")
    remote = Remote()
    original = remote.call

    async def call(*args: Any) -> Any:
        result = await original(*args)
        if len(remote.calls) == 1:
            await store.queue_memory_server_send("ada", "live", "live-a2")
        return result

    remote.call = call
    courier = ms.Courier(store, remote)
    await courier.once()
    await courier.once()
    assert [c["document_id"] for c in remote.calls[:2]] == ["past-00:past-00-a0", "live:live-a2"]


@pytest.mark.parametrize("switch", ["servers", "install", "person"])
async def test_memory_off_mid_send_pauses_without_consuming_backfill(queued, switch) -> None:
    store, feeder = queued

    async def enabled(value: bool) -> None:
        if switch == "person":
            await store.put_memory_prefs("ada", enabled=value)
        else:
            await store.put_setting(
                config.MEMORY_SERVERS if switch == "servers" else config.MEMORY_ENABLED,
                ("outside" if value else "off") if switch == "servers" else value,
            )

    class Switching(Remote):
        async def call(self, *args: Any) -> Any:
            result = await super().call(*args)
            if len(self.calls) == 1:
                await enabled(False)
            return result

    remote = Switching()
    courier = ms.Courier(store, remote)
    await courier.once()
    paused = json.loads((await store.memory_server("ada"))["backfill"])
    assert len(remote.calls) == paused["sent"] == 1
    assert paused["skipped"] == 12  # Only the secrets encountered while filling the queue.
    assert len(await store.memory_server_pending("ada")) == len(paused["pending"]) == 19
    assert (await feeder.preview("ada"))["backfill"]["pauseReason"] == "Paused: memory is off"
    await feeder.top_up("ada")
    await courier.once()
    assert json.loads((await store.memory_server("ada"))["backfill"]) == paused
    # A pause lasting more than the normal give-up day must still resume its queue.
    await store._run(
        lambda: store._conn().execute(
            "UPDATE memory_servers SET backfill = json_set(backfill, '$.pausedAt', "
            "json_extract(backfill, '$.pausedAt') - 172800) WHERE owner = 'ada'"
        )
    )
    await store._run(
        lambda: store._conn().execute(
            "UPDATE memory_server_sent SET created_at = created_at - 172800, "
            "next_at = next_at - 172800 WHERE state = 'send'"
        )
    )
    await enabled(True)
    for _ in range(4):
        await courier.once()
    done = json.loads((await store.memory_server("ada"))["backfill"])
    assert done["state"] == "done" and done["sent"] == 24 and done["skipped"] == 12
    assert not done.get("pauseReason")
    assert len({c["document_id"] for c in remote.calls}) == len(remote.calls) == 24


async def test_legacy_reconnect_at_same_url_keeps_progress(queued) -> None:
    store, _ = queued
    before = (await store.memory_server("ada"))["backfill"]
    await store.put_memory_server(
        "ada", "https://chosen.example", "new-token", "Chosen", {"retain": "retain"}
    )
    assert (await store.memory_server("ada"))["backfill"] == before


async def test_deleted_pending_exchanges_do_not_leave_backfill_running(queued) -> None:
    store, feeder = queued
    for chat in await store.chats("ada"):
        await store.delete_chat(chat.id)
    await feeder.top_up("ada")
    progress = json.loads((await store.memory_server("ada"))["backfill"])
    assert progress["state"] == "done" and progress["pending"] == []


async def test_three_failed_backfill_rows_stop_with_the_observed_cause(queued) -> None:
    store, _ = queued

    class Failed(Remote):
        async def call(self, *args: Any) -> Any:
            raise ms.ServerProblem("connection refused")

    await ms.Courier(store, Failed()).once()
    progress = json.loads((await store.memory_server("ada"))["backfill"])
    assert progress["state"] == "stopped" and progress["failures"] == 3
    assert progress["error"] == "connection refused"


async def test_delete_during_send_still_forgets_that_exchange(queued) -> None:
    store, _ = queued

    class Deleting(Remote):
        async def call(self, *args: Any) -> Any:
            result = await super().call(*args)
            if len(self.calls) == 1:
                await store.delete_chat("past-00")
            return result

    remote = Deleting()
    courier = ms.Courier(store, remote)
    await courier.once()
    await courier.once()
    assert any(
        c["verb"] == "forget" and c["document_id"] == "past-00:past-00-a0" for c in remote.calls
    )


async def seed(store: Store, chat_id: str, **kw: Any) -> None:
    await store.create_chat(Chat(chat_id, "ada", chat_id, "m", 1, 1, **kw))
    parent = None
    for i, words in enumerate(["I grow tomatoes.", "My password is hunter2", "I grow beans."]):
        asked = Message(
            f"{chat_id}-q{i}",
            chat_id,
            i * 2,
            "user",
            "done",
            1 + i,
            content=words,
            parent_id=parent,
        )
        reply = Message(
            f"{chat_id}-a{i}",
            chat_id,
            i * 2 + 1,
            "assistant",
            "done",
            1 + i,
            content="Understood.",
            parent_id=asked.id,
        )
        await store.add_message(asked, parent_id=parent)
        await store.add_message(reply, parent_id=asked.id)
        parent = reply.id


async def test_past_chats_skip_secrets_even_when_custom_notes_allow_them(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    await store.open()
    try:
        await seed(store, "plain")
        await store.put_setting(config.MEMORY_WRITE_POLICY, "custom")
        await store.put_setting(config.POLICY_ROW_KEYS["secrets"], "allow")
        preview = await ms.Backfill(store).preview("ada")
        assert preview["exchanges"] == 2 and preview["skipped"] == 1
    finally:
        await store.close()


async def test_backfill_sends_what_live_would_and_resumes(tmp_path: Path) -> None:
    store = Store(tmp_path / "store.sqlite3")
    await store.open()
    try:
        await seed(store, "plain")
        await seed(store, "project", project_id="garden")
        await seed(store, "off", settings={"memory": "off"})
        await store.put_memory_server(
            "ada", "https://chosen.example/mcp", "", "Chosen", {"retain": "retain"}
        )
        feeder = ms.Backfill(store)
        preview = await feeder.preview("ada")
        assert preview["exchanges"] == 2 and preview["chats"] == 1
        assert preview["estimateSeconds"] is None
        assert not await store.memory_server_due(9999999999)
        await feeder.start("ada")
        await feeder.top_up("ada")
        rows = await store.memory_server_due(9999999999)
        assert {r["chat_id"] for r in rows} == {"plain"}
        remote = Remote()
        courier = ms.Courier(store, remote)  # type: ignore[arg-type]
        await courier._one(rows[0])
        await store.close()
        store = Store(tmp_path / "store.sqlite3")
        await store.open()
        courier = ms.Courier(store, remote)  # type: ignore[arg-type]
        for _ in range(4):
            await courier.once()
        assert [r["document_id"] for r in remote.calls] == ["plain:plain-a0", "plain:plain-a2"]
        assert all("password" not in r["content"] for r in remote.calls)
        state = json.loads((await store.memory_server("ada"))["backfill"])  # type: ignore[index]
        assert state["state"] == "done" and state["sent"] == 2 and state["skipped"] == 1
    finally:
        await store.close()


def test_migration_31_from_30(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    storage._initialize_schema(db, path)
    columns = {
        "memory_servers": ["backfill"],
        "projects": ["model"],
        "memory_tidy": ["model", "merged"],
        "document_folders": ["project_id"],
    }
    db.execute("DROP INDEX IF EXISTS document_folders_by_project")
    for table, names in columns.items():
        present = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        for name in names:
            if name in present:
                db.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
    db.execute("UPDATE meta SET value = '30' WHERE key = 'schema'")
    db.execute(
        "INSERT INTO projects (id, owner, name, created_at, updated_at) VALUES ('p', 'ada', 'Garden', 1, 2)"
    )
    db.execute(
        "INSERT INTO memory_servers (owner, url, token, name, verbs, connected_at) VALUES ('ada', 'https://chosen.example', 'private-token', 'Chosen', '{}', 2)"
    )
    db.execute(
        "INSERT INTO document_folders (owner, grant_id, name, added_at) VALUES ('ada', 'g', 'Garden files', 2)"
    )
    db.commit()
    db.close()
    migrated = sqlite3.connect(path)
    storage._initialize_schema(migrated, path)
    migrated.close()
    with sqlite3.connect(path) as db:
        assert storage.SCHEMA_VERSION == 33
        assert db.execute("SELECT name, model FROM projects WHERE id = 'p'").fetchone() == (
            "Garden",
            None,
        )
        assert db.execute(
            "SELECT url, token, backfill FROM memory_servers WHERE owner = 'ada'"
        ).fetchone() == ("https://chosen.example", "private-token", None)
        assert db.execute(
            "SELECT name, project_id FROM document_folders WHERE owner = 'ada'"
        ).fetchone() == ("Garden files", None)
        for table, names in columns.items():
            present = {r[1]: r for r in db.execute(f"PRAGMA table_info({table})")}
            assert all(n in present and present[n][3] == 0 for n in names)
            assert (table, "owner") in storage.PERSON_TABLES
        storage._memory_extras(db)
        storage._memory_extras(db)
