"""A store written by an older Workbench opens, and keeps every chat."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.answers import answer_text, message_view
from eugene_plexus_workbench.store import SCHEMA_VERSION, Message, Store

#: The messages table as schema 1 made it (Workbench v1, 2026-10-01).
_V1_MESSAGES = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta (key, value) VALUES ('schema', '1');
CREATE TABLE messages (
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
    finished_at REAL
);
INSERT INTO messages (id, chat_id, seq, role, content, status, created_at)
VALUES ('m1', 'c1', 1, 'assistant', 'An answer from before.', 'done', 1.0);
"""


def test_a_schema_1_store_gains_the_marks_and_keeps_its_messages(tmp_path: Path) -> None:
    path = tmp_path / "workbench.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V1_MESSAGES)

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
    assert int(version) == SCHEMA_VERSION == 6


def test_schema_3_http_connections_survive_local_server_migration(tmp_path: Path) -> None:
    path = tmp_path / "schema3.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V1_MESSAGES)
        for target in (2, 3):
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


def test_interrupted_legacy_migration_recovers(tmp_path: Path) -> None:
    path = tmp_path / "partial.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V1_MESSAGES)
        db.execute("ALTER TABLE messages ADD COLUMN answer_from INTEGER")
    store = Store(path)
    try:
        db = store._conn()
        assert (
            db.execute("SELECT content FROM messages WHERE id = 'm1'").fetchone()[0]
            == "An answer from before."
        )
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == str(
            SCHEMA_VERSION
        )
    finally:
        asyncio.run(store.close())


def test_failed_migration_rolls_back_columns_and_version(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "failed.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V1_MESSAGES)
    monkeypatch.setitem(storage._MIGRATIONS, 2, [storage._MIGRATIONS[2][0], "INVALID SQL"])
    store = Store(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            store._conn()
    finally:
        asyncio.run(store.close())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == "1"
        assert "answer_from" not in {row[1] for row in db.execute("PRAGMA table_info(messages)")}


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
