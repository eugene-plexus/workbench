"""A store written by an older Workbench opens, and keeps every chat.

The oldest is the one Workbench v0.1.0 wrote, schema 2 (`test_conversions.py` opens a real one,
with chats, files and sessions in it). Each step from there is checked from the store the step
expects: made by hand from `_V2_STORE` and the steps before it.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.answers import answer_text, message_view
from eugene_plexus_workbench.store import SCHEMA_VERSION, Message, Store

#: A store as Workbench v0.1.0 (pin 5ed0001) made it: schema 2, whole, with the one
#: message the tests below open it for. The statements are `_SCHEMA` of that release,
#: word for word (`git show 5ed0001:src/eugene_plexus_workbench/store.py`).
_V2_STORE = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS people (
    sub TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    username TEXT,
    role TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id_hash TEXT PRIMARY KEY,
    secret_hash TEXT NOT NULL,
    sub TEXT NOT NULL,
    refresh_token TEXT,
    access_expires_at REAL NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    title TEXT NOT NULL,
    model TEXT,
    settings TEXT NOT NULL DEFAULT '{}',
    search INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS chats_by_owner ON chats (owner, updated_at);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    reasoning TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    error TEXT,
    sources TEXT NOT NULL DEFAULT '[]',
    searches INTEGER NOT NULL DEFAULT 0,
    search INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    finish TEXT,
    created_at REAL NOT NULL,
    finished_at REAL,
    answer_from INTEGER,
    reasoning_from INTEGER
);
CREATE INDEX IF NOT EXISTS messages_by_chat ON messages (chat_id, seq);
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS files_by_chat ON files (chat_id);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta (key, value) VALUES ('schema', '2');
INSERT INTO messages (id, chat_id, seq, role, content, status, created_at)
VALUES ('m1', 'c1', 1, 'assistant', 'An answer from before.', 'done', 1.0);
"""


def test_a_v0_1_store_keeps_its_messages_and_takes_the_marks(tmp_path: Path) -> None:
    path = tmp_path / "workbench.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V2_STORE)

    async def go() -> list[Message]:
        store = Store(path)
        await store.open()
        try:
            await store.update_message("m1", answer_from=4, reasoning_from=0)
            return await store.messages("c1")
        finally:
            await store.close()

    (message,) = asyncio.run(go())
    assert message.content == "An answer from before."
    assert message.answer_from == 4 and message.reasoning_from == 0
    with sqlite3.connect(path) as db:
        (version,) = db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
    assert int(version) == SCHEMA_VERSION == 21


def test_schema_3_http_connections_survive_local_server_migration(tmp_path: Path) -> None:
    path = tmp_path / "schema3.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V2_STORE)
        for target in (3,):
            for statement in storage._MIGRATIONS[target]:
                db.execute(statement)
        db.execute("UPDATE meta SET value = '3' WHERE key = 'schema'")
        db.execute(
            "INSERT INTO tool_servers VALUES ('one', 'Saved', 'https://example.org/mcp', 'private')"
        )

    async def read() -> None:
        store = Store(path)
        await store.open()
        try:
            [server] = await store.tool_servers()
            assert server == {
                "id": "one",
                "name": "Saved",
                "url": "https://example.org/mcp",
                "token": "private",
                "transport": "http",
                "command": "",
                "args": [],
                "environment": {},
            }
        finally:
            await store.close()

    asyncio.run(read())
    asyncio.run(read())  # A second boot must not repeat ALTER TABLE.


def test_a_schema_6_store_keeps_its_attachments_and_gains_media(tmp_path: Path) -> None:
    """Schema 7 makes `files` again so a file can belong to a media row
    instead of a chat; an attachment written before keeps its chat."""
    path = tmp_path / "schema6.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V2_STORE)
        for target in range(3, 7):
            for statement in storage._MIGRATIONS[target]:
                db.execute(statement)
        db.execute("INSERT INTO files VALUES ('f1', 'ann', 'c1', 'a.png', 'image/png', 9, 1.0)")
        db.execute("UPDATE meta SET value = '6' WHERE key = 'schema'")

    async def go() -> tuple[list[storage.FileRecord], list[storage.FileRecord]]:
        store = Store(path)
        await store.open()
        try:
            await store.add_media(
                storage.MediaRow(
                    id="m1", owner="ann", door="images", kind="made", status="done", created_at=2.0
                )
            )
            await store.add_file(
                storage.FileRecord(
                    id="f2",
                    owner="ann",
                    chat_id=None,
                    name="barn.png",
                    media_type="image/png",
                    size=5,
                    created_at=2.0,
                    media_id="m1",
                    width=512,
                    height=512,
                )
            )
            return await store.files_for_chat("c1"), await store.files_for_media(["m1"])
        finally:
            await store.close()

    attached, made = asyncio.run(go())
    assert [(f.id, f.chat_id, f.media_id) for f in attached] == [("f1", "c1", None)]
    assert [(f.id, f.chat_id, f.width) for f in made] == [("f2", None, 512)]
    with sqlite3.connect(path) as db:
        (version,) = db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
    assert int(version) == SCHEMA_VERSION == 21


def test_a_new_store_starts_at_the_current_schema(tmp_path: Path) -> None:
    path = tmp_path / "workbench.sqlite3"

    async def go() -> None:
        store = Store(path)
        await store.open()
        await store.close()

    asyncio.run(go())
    with sqlite3.connect(path) as db:
        (version,) = db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
    assert int(version) == SCHEMA_VERSION
    assert {"answer_from", "reasoning_from"} <= columns


def test_schema_15_is_backed_up_before_interaction_tables_are_added(tmp_path: Path) -> None:
    path = tmp_path / "schema15.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V2_STORE)
        for target in range(3, 16):
            for statement in storage._MIGRATIONS[target]:
                if callable(statement):
                    db.row_factory = sqlite3.Row
                    statement(db)
                else:
                    db.execute(statement)
        db.execute("UPDATE meta SET value='15' WHERE key='schema'")

    async def migrate() -> Path | None:
        store = Store(path)
        await store.open()
        try:
            assert (await store.message("m1")).content == "An answer from before."
            return store.backup
        finally:
            await store.close()

    backup = asyncio.run(migrate())
    assert backup is not None
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] == "15"
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name='interaction_surfaces'"
            ).fetchone()
            is None
        )
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] == str(
            SCHEMA_VERSION
        )
        assert db.execute("SELECT count(*) FROM interaction_surfaces").fetchone()[0] == 0


def test_schema_16_keeps_interactions_and_adds_agui_tables(tmp_path: Path) -> None:
    path = tmp_path / "schema16.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V2_STORE)
        for target in range(3, 17):
            for statement in storage._MIGRATIONS[target]:
                if callable(statement):
                    db.row_factory = sqlite3.Row
                    statement(db)
                else:
                    db.execute(statement)
        db.execute("UPDATE meta SET value='16' WHERE key='schema'")
        db.execute("INSERT INTO interaction_cursors VALUES ('c1', 17)")

    async def migrate() -> None:
        store = Store(path)
        await store.open()
        try:
            assert (await store.message("m1")).content == "An answer from before."
            assert await store.agui.agents() == []
            assert store.backup is not None
            with sqlite3.connect(store.backup) as db:
                assert db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] == "16"
                assert (
                    db.execute("SELECT name FROM sqlite_master WHERE name='agui_runs'").fetchone()
                    is None
                )
        finally:
            await store.close()

    asyncio.run(migrate())
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT value FROM interaction_cursors WHERE chat_id='c1'").fetchone()[0]
            == 17
        )
        assert db.execute("SELECT count(*) FROM agui_runs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM agui_events").fetchone()[0] == 0


def test_failed_migration_rolls_back_columns_and_version(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "failed.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V2_STORE)
    monkeypatch.setitem(storage._MIGRATIONS, 3, [storage._MIGRATIONS[3][0], "INVALID SQL"])
    store = Store(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            store._conn()
    finally:
        asyncio.run(store.close())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == "2"
        assert "tool_rounds" not in {row[1] for row in db.execute("PRAGMA table_info(messages)")}


def test_a_store_from_before_v0_1_is_refused_and_left_as_it_is(tmp_path: Path) -> None:
    """Schema 1 predates the first release; nobody holds one, so nothing converts it."""
    path = tmp_path / "schema1.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V2_STORE)
        db.execute("UPDATE meta SET value = '1' WHERE key = 'schema'")
    store = Store(path)
    try:
        with pytest.raises(RuntimeError, match=r"unsupported schema 1 from before Workbench v0\.1"):
            store._conn()
    finally:
        asyncio.run(store.close())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == "1"
        assert "tool_rounds" not in {row[1] for row in db.execute("PRAGMA table_info(messages)")}
    assert store.backup is None and list(tmp_path.glob("*.bak")) == []


def test_future_schema_is_refused_before_creating_tables(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT); INSERT INTO meta VALUES ('schema', '999');"
        )
    store = Store(path)
    try:
        with pytest.raises(RuntimeError, match="unsupported schema 999"):
            store._conn()
    finally:
        asyncio.run(store.close())
    with sqlite3.connect(path) as db:
        assert [
            r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ] == ["meta"]


@pytest.mark.parametrize(
    ("content", "answer_from", "expected"),
    [
        ("Draft.\n\nAnswer.", 6, "Answer."),
        ("Just an answer.", None, "Just an answer."),
        # Stopped right after the search: the draft is all it said.
        ("Draft.", 6, "Draft."),
    ],
)
def test_the_answer_is_the_text_after_the_last_search(
    content: str, answer_from: int | None, expected: str
) -> None:
    message = Message(
        id="m",
        chat_id="c",
        seq=1,
        role="assistant",
        status="done",
        created_at=0.0,
        content=content,
        answer_from=answer_from,
    )
    assert answer_text(message) == expected


def test_a_mark_is_given_to_the_page_in_its_own_string_length() -> None:
    message = Message(
        id="m",
        chat_id="c",
        seq=1,
        role="assistant",
        status="done",
        created_at=0.0,
        content="🎉 ok\n\nAnswer.",
        reasoning="🎉 think",
        answer_from=4,
        reasoning_from=2,
    )
    view = message_view(message)
    assert view["answerFrom"] == 5 and view["reasoningFrom"] == 3
