"""Schema 11 (MR2): what was read of a file, and a kind on every source.

An older store opens and keeps every chat; a source that was a search's citation
becomes `{"kind": "web", ...}`; a file's reading is kept and changed one key at a
time; a store made new and a store migrated have the same shape.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.store import SCHEMA_VERSION, Chat, FileRecord, Store

from .test_memory_store import _V9


def run(path: Path, body: Callable[[Store], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        store = Store(path)
        await store.open()
        try:
            return await body(store)
        finally:
            await store.close()

    return asyncio.run(go())


def schema_10(path: Path) -> None:
    """A store as Workbench left it the day before this: memory, and no reading."""
    with sqlite3.connect(path) as db:
        db.executescript(_V9)
        for statement in storage._MIGRATIONS[10]:
            assert isinstance(statement, str)
            db.execute(statement)
        db.execute("UPDATE meta SET value = '10' WHERE key = 'schema'")


WEB = [{"url": "https://a.example/x", "title": "A"}, {"url": "https://b.example/y", "title": "B"}]


def put_sources(path: Path, rows: dict[str, str]) -> None:
    with sqlite3.connect(path) as db:
        for message_id, sources in rows.items():
            db.execute(
                "INSERT INTO messages (id, chat_id, seq, role, status, created_at, parent_id, sources) "
                "VALUES (?, 'c1', ?, 'assistant', 'done', 1.0, 'm1', ?)",
                (message_id, 10 + len(message_id), sources),
            )


def sources_of(path: Path, message_id: str) -> str:
    with sqlite3.connect(path) as db:
        [(found,)] = db.execute(
            "SELECT sources FROM messages WHERE id = ?", (message_id,)
        ).fetchall()
    return str(found)


def test_a_schema_10_file_gains_reading_and_every_source_a_kind_and_keeps_every_chat(
    tmp_path: Path,
) -> None:
    path = tmp_path / "w.sqlite3"
    schema_10(path)
    put_sources(path, {"web": json.dumps(WEB), "none": "[]"})
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO files (id, owner, chat_id, name, media_type, size, created_at) "
            "VALUES ('f1', 'ada', 'c1', 'old.pdf', 'application/pdf', 10, 1.0)"
        )

    async def body(store: Store) -> None:
        [chat] = await store.chats("ada")
        assert (chat.id, chat.title) == ("c1", "Quarterly numbers")
        assert [m.id for m in await store.messages("c1")][:2] == ["m1", "m2"]
        [old] = await store.files_for_chat("c1")
        assert old.reading is None, "a file from before is not a file that was read"

    run(path, body)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone() == (
            str(SCHEMA_VERSION),
        )
        assert SCHEMA_VERSION == 31
        assert "reading" in {r[1] for r in db.execute("PRAGMA table_info(files)")}
    expected = [{"kind": "web", **s} for s in WEB]
    assert json.loads(sources_of(path, "web")) == expected
    assert list(json.loads(sources_of(path, "web"))[0]) == ["kind", "url", "title"], "kind first"
    assert sources_of(path, "none") == "[]"
    # The store reads them back as the kind they are.
    message = run(path, lambda store: store.message("web"))
    assert message is not None and message.sources == expected
    # A second boot repeats nothing that fails or changes.
    run(path, lambda store: store.open())
    assert json.loads(sources_of(path, "web")) == expected


def test_a_source_that_has_a_kind_already_or_is_not_a_list_of_objects_is_left_as_it_was(
    tmp_path: Path,
) -> None:
    path = tmp_path / "w.sqlite3"
    schema_10(path)
    document = {"kind": "document", "doc_id": "f1", "name": "r.pdf"}
    put_sources(
        path,
        {
            "mixed": json.dumps([document, {"url": "https://c.example", "title": "C"}]),
            "typed": json.dumps([document]),
            "weird": json.dumps([1, "two", None, {"url": "https://d.example", "title": "D"}]),
            "object": json.dumps({"url": "https://e.example"}),
            "broken": "not json at all",
        },
    )
    run(path, lambda store: store.open())
    assert json.loads(sources_of(path, "mixed")) == [
        document,
        {"kind": "web", "url": "https://c.example", "title": "C"},
    ]
    assert json.loads(sources_of(path, "typed")) == [document]
    assert json.loads(sources_of(path, "weird")) == [
        1,
        "two",
        None,
        {"kind": "web", "url": "https://d.example", "title": "D"},
    ]
    assert json.loads(sources_of(path, "object")) == {"url": "https://e.example"}
    assert sources_of(path, "broken") == "not json at all", "unreadable before, and not made worse"


def test_a_schema_9_file_reaches_the_current_schema_in_one_boot(tmp_path: Path) -> None:
    path = tmp_path / "w.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V9)
    put_sources(path, {"web": json.dumps(WEB)})
    run(path, lambda store: store.open())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone() == (
            str(SCHEMA_VERSION),
        )
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"memory_notes", "memory_prefs"} <= tables
    assert json.loads(sources_of(path, "web")) == [{"kind": "web", **s} for s in WEB]


def test_a_new_store_and_a_migrated_one_have_the_same_files_table(tmp_path: Path) -> None:
    old, new = tmp_path / "old.sqlite3", tmp_path / "new.sqlite3"
    schema_10(old)
    run(old, lambda store: store.open())
    run(new, lambda store: store.open())

    def columns(path: Path) -> list[tuple[Any, ...]]:
        with sqlite3.connect(path) as db:
            return [tuple(r[1:3]) for r in db.execute("PRAGMA table_info(files)")]

    assert columns(old) == columns(new)
    assert columns(new)[-3:] == [("reading", "TEXT"), ("message_id", "TEXT"), ("origin", "TEXT")]


def record(
    file_id: str = "f1", chat_id: str | None = "c1", reading: dict[str, Any] | None = None
) -> FileRecord:
    return FileRecord(file_id, "ada", chat_id, "r.pdf", "application/pdf", 10, 1.0, reading=reading)


def test_a_files_reading_is_kept_and_changed_one_key_at_a_time(tmp_path: Path) -> None:
    async def body(store: Store) -> None:
        await store.create_chat(
            Chat(id="c1", owner="ada", title="t", model="m", created_at=1, updated_at=1)
        )
        await store.add_file(
            record(reading={"state": "reading", "nonce": "7f3a9c1e", "choice": None})
        )
        [found] = await store.files_for_chat("c1")
        assert found.reading == {"state": "reading", "nonce": "7f3a9c1e", "choice": None}
        done = await store.merge_file_reading("f1", {"state": "ready", "tokens": 412})
        assert done is not None and done.reading == {
            "state": "ready",
            "nonce": "7f3a9c1e",
            "choice": None,
            "tokens": 412,
        }, "the suffix and the choice are kept when the reading ends"
        chosen = await store.merge_file_reading("f1", {"choice": {"mode": "whole"}}, chat_id="c1")
        assert chosen is not None and chosen.reading is not None
        assert chosen.reading["choice"] == {"mode": "whole"} and chosen.reading["tokens"] == 412
        assert (await store.file("f1")).reading == chosen.reading  # type: ignore[union-attr]

    run(tmp_path / "w.sqlite3", body)


def test_a_change_to_a_reading_reaches_only_the_file_and_the_chat_it_names(tmp_path: Path) -> None:
    async def body(store: Store) -> None:
        await store.add_file(record("f1", "c1", {"state": "ready"}))
        await store.add_file(record("f2", "c2", {"state": "ready"}))
        assert (
            await store.merge_file_reading("f1", {"choice": {"mode": "whole"}}, chat_id="c2")
            is None
        )
        assert (await store.file("f1")).reading == {"state": "ready"}  # type: ignore[union-attr]
        assert await store.merge_file_reading("nope", {"state": "ready"}) is None
        assert (await store.file("f2")).reading == {"state": "ready"}  # type: ignore[union-attr]

    run(tmp_path / "w.sqlite3", body)


def test_a_file_that_is_not_read_has_no_reading_and_a_bad_one_is_none(tmp_path: Path) -> None:
    path = tmp_path / "w.sqlite3"

    async def body(store: Store) -> None:
        await store.add_file(record("f1"))
        assert (await store.file("f1")).reading is None  # type: ignore[union-attr]
        assert await store.merge_file_reading("f1", {"state": "ready"}) is not None

    run(path, body)
    with sqlite3.connect(path) as db:
        for junk in ("not json", "[1, 2]", "42", ""):
            db.execute("UPDATE files SET reading = ?", (junk,))
            [(value,)] = db.execute("SELECT reading FROM files").fetchall()
            assert storage._reading(value) is None


def test_the_files_still_being_read_are_found_for_the_boot_to_read_again(tmp_path: Path) -> None:
    async def body(store: Store) -> None:
        await store.add_file(record("a", reading={"state": "reading"}))
        await store.add_file(record("b", reading={"state": "ready"}))
        await store.add_file(record("c", reading={"state": "failed"}))
        await store.add_file(record("d", reading=None))
        await store.add_file(record("e", reading={"state": "reading"}))
        assert sorted(r.id for r in await store.files_reading()) == ["a", "e"]

    run(tmp_path / "w.sqlite3", body)
