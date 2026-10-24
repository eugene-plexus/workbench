"""What Workbench v0.1 saved is migrated once, kept as it was, and a migration that cannot
finish degrades: it never crashes the process.

The fixture is not a look-alike: `fixtures/workbench-v0.1.0.sql` is a dump of a store written by
Workbench at the v0.1.0 pin (5ed0001, schema 2) through its own `Store`: two people, a session, two
chats with a search answer, an attachment, a reply cut off mid-stream, and a setting.

Every conversion is held to the same six claims: converted correctly, a copy of the old data kept,
the new version recorded, a second start a no-op, a start that stopped midway rerun cleanly, and a
failure that degrades naming its cause.
"""

from __future__ import annotations

import asyncio
import errno
import html
import json
import os
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench import web
from eugene_plexus_workbench.app import create_app
from eugene_plexus_workbench.settings import Settings
from eugene_plexus_workbench.store import OLDEST_SCHEMA, SCHEMA_VERSION, Store

FIXTURE = Path(__file__).parent / "fixtures" / "workbench-v0.1.0.sql"
TABLES = ("people", "sessions", "chats", "messages", "files", "settings")


class Crash(BaseException):
    """The process dying: nothing between the failing statement and the next start runs."""


def v0_1_store(directory: Path) -> Path:
    path = directory / "workbench.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(FIXTURE.read_text(encoding="utf-8"))
    return path


def rows(path: Path, table: str, *columns: str) -> list[tuple[Any, ...]]:
    names = ", ".join(columns) if columns else "*"
    with sqlite3.connect(path) as db:
        return db.execute(f"SELECT {names} FROM {table} ORDER BY 1").fetchall()


def schema_of(path: Path) -> str:
    with sqlite3.connect(path) as db:
        return str(db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0])


def everything(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    return {table: rows(path, table) for table in TABLES}


def backups_of(path: Path) -> list[Path]:
    return sorted(path.parent.glob(path.name + ".schema-*"))


def open_store(path: Path) -> Store:
    store = Store(path)
    asyncio.run(store.open())
    return store


def close(store: Store) -> None:
    asyncio.run(store.close())


def settings_in(directory: Path) -> Settings:
    return Settings(data_dir=directory)


def write(path: Path, statement: str, *params: Any) -> None:
    """One change to a store, as the older Workbench would make it, and the file let go."""
    db = sqlite3.connect(path)
    try:
        db.execute(statement, params)
        db.commit()
    finally:
        db.close()


def add_a_chat(path: Path, chat_id: str) -> None:
    write(
        path,
        "INSERT INTO chats (id, owner, title, settings, search, created_at, updated_at) "
        "VALUES (?, 'owner-1', 'Written after', '{}', 0, 1.0, 1.0)",
        chat_id,
    )


def chat_ids(path: Path) -> list[str]:
    return [chat for (chat,) in rows(path, "chats", "id")]


def fail_to_open(path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    """A start whose migration fails after the copy: the store is as it was."""
    with monkeypatch.context() as patch:
        patch.setitem(
            storage._MIGRATIONS, 6, [*storage._MIGRATIONS[6], "ALTER TABLE nothing_here ADD x TEXT"]
        )
        store = Store(path)
        with pytest.raises(sqlite3.OperationalError, match="nothing_here"):
            store._conn()
        asyncio.run(store.close())
    return store


# --- converted correctly, kept, recorded -------------------------------------------------------


def test_the_store_v0_1_wrote_is_migrated_and_every_chat_comes_through(tmp_path: Path) -> None:
    path = v0_1_store(tmp_path)
    before = everything(path)
    assert schema_of(path) == "2", "the fixture is the old shape"

    store = open_store(path)
    try:
        # The version is recorded.
        assert schema_of(path) == str(SCHEMA_VERSION) == "32"
        # Everything v0.1 held is there, column for column.
        for table, columns in {
            "people": "sub, name, username, role, first_seen, last_seen",
            "sessions": "id_hash, secret_hash, sub, refresh_token, access_expires_at, created_at, expires_at",
            "chats": "id, owner, title, model, settings, search, created_at, updated_at",
            "settings": "key, value",
        }.items():
            assert rows(path, table, *columns.split(", ")) == before[table], table
        kept = (
            "id, chat_id, seq, role, content, reasoning, attachments, status, error, searches, "
            "search, model, finish, created_at, finished_at, answer_from, reasoning_from"
        )
        # `sources` (the tenth column) is the one the later steps rewrite: see below.
        assert rows(path, "messages", *kept.split(", ")) == [
            r[:9] + r[10:] for r in before["messages"]
        ]
        assert (
            rows(path, "files", "id, owner, chat_id, name, media_type, size, created_at")
            == before["files"]
        )
        # What the later steps add to old rows: each message follows the one before it, a
        # source is a web source, and an attachment has no reading yet.
        assert rows(path, "messages", "id", "parent_id", "chosen") == [
            ("m1", None, 1),
            ("m2", "m1", 1),
            ("m3", "m2", 1),
            ("m4", "m3", 1),
            ("m5", None, 1),
        ]
        assert json.loads(dict(rows(path, "messages", "id", "sources"))["m2"]) == [
            {"kind": "web", "url": "https://a.example/q3", "title": "Q3 report"},
            {"kind": "web", "url": "https://b.example/news", "title": "Newswire"},
        ]
        assert rows(path, "files", "reading") == [(None,)]
        # And it reads through the store.
        chats = asyncio.run(store.chats("owner-1"))
        assert [c.title for c in chats] == ["Quarterly numbers"]
        messages = asyncio.run(store.messages("chat-1"))
        assert [m.id for m in messages] == ["m1", "m2", "m3", "m4"]
        assert messages[1].answer_from == 24 and messages[1].sources[0]["kind"] == "web"
        assert asyncio.run(store.person("user-2")) is not None
    finally:
        close(store)


def test_a_copy_of_what_v0_1_saved_is_kept_before_anything_changes(tmp_path: Path) -> None:
    path = v0_1_store(tmp_path)
    before = everything(path)

    store = open_store(path)
    try:
        copy = path.with_name(path.name + ".schema-2.bak")
        assert store.backup == copy and backups_of(path) == [copy]
        # It is a store of its own, as it was: schema 2 and every row.
        assert schema_of(copy) == "2"
        assert everything(copy) == before
        # One file that opens on its own, with nothing half-made beside it.
        assert sorted(p.name for p in tmp_path.glob("*.part")) == []
        assert not any(p.name.endswith(("-wal", "-shm")) for p in tmp_path.glob("*.bak*"))
        with sqlite3.connect(copy) as db:
            assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        close(store)


def test_the_copy_holds_what_the_write_ahead_log_had_not_yet_folded_in(tmp_path: Path) -> None:
    """Workbench keeps its store in WAL mode, so a store that was not closed cleanly has rows
    only in the log. A file copy would miss them; SQLite's own backup does not."""
    path = v0_1_store(tmp_path)
    writer = sqlite3.connect(path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO chats (id, owner, title, settings, search, created_at, updated_at) "
            "VALUES ('in-the-log', 'owner-1', 'Only in the log', '{}', 0, 1.0, 1.0)"
        )
        writer.commit()
        assert path.with_name(path.name + "-wal").stat().st_size > 0

        store = open_store(path)
        try:
            assert store.backup is not None
            assert ("in-the-log",) in rows(store.backup, "chats", "id")
        finally:
            close(store)
    finally:
        writer.close()


def test_a_store_that_is_current_or_new_has_nothing_to_keep_a_copy_of(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    store = open_store(fresh / "workbench.sqlite3")
    close(store)
    assert store.backup is None and backups_of(fresh / "workbench.sqlite3") == []

    again = open_store(fresh / "workbench.sqlite3")
    close(again)
    assert again.backup is None and list(fresh.glob("*.bak*")) == []


def test_the_copy_is_named_for_the_schema_it_was_taken_at(tmp_path: Path) -> None:
    from .test_memory_store import _V9

    path = tmp_path / "workbench.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V9)
    store = open_store(path)
    close(store)

    assert store.backup == path.with_name(path.name + ".schema-9.bak")
    assert schema_of(store.backup) == "9"


# --- a second start ----------------------------------------------------------------------------


def test_a_second_start_changes_nothing_and_keeps_no_second_copy(tmp_path: Path) -> None:
    path = v0_1_store(tmp_path)
    first = open_store(path)
    close(first)
    copy = first.backup
    assert copy is not None
    kept = copy.read_bytes(), copy.stat().st_mtime_ns
    after_first = everything(path)

    second = open_store(path)
    close(second)

    assert second.backup is None
    assert backups_of(path) == [copy] and (copy.read_bytes(), copy.stat().st_mtime_ns) == kept
    assert everything(path) == after_first and schema_of(path) == str(SCHEMA_VERSION)


# --- a start that stopped midway -----------------------------------------------------------------


def test_a_migration_that_stopped_part_way_changed_nothing_and_reruns_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = v0_1_store(tmp_path)
    before = everything(path)

    def die(db: sqlite3.Connection) -> None:
        raise Crash

    # After steps 3 to 6 have run (step 6 rewrites every message's parent), and before 7.
    monkeypatch.setitem(storage._MIGRATIONS, 7, [die])
    store = Store(path)
    with pytest.raises(Crash):
        store._conn()
    asyncio.run(store.close())
    monkeypatch.undo()

    assert schema_of(path) == "2" and everything(path) == before, "the transaction left no trace"
    copy = path.with_name(path.name + ".schema-2.bak")
    assert store.backup == copy and everything(copy) == before

    again = open_store(path)
    close(again)

    assert schema_of(path) == str(SCHEMA_VERSION) and again.backup == copy
    assert backups_of(path) == [copy] and everything(copy) == before


def test_a_start_that_stopped_after_the_copy_and_before_the_migration_reruns_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = v0_1_store(tmp_path)
    before = everything(path)
    real = storage._keep_backup

    def keep_then_stop(*args: Any) -> Path:
        real(*args)
        raise Crash

    monkeypatch.setattr(storage, "_keep_backup", keep_then_stop)
    store = Store(path)
    with pytest.raises(Crash):
        store._conn()
    asyncio.run(store.close())
    monkeypatch.undo()
    copy = path.with_name(path.name + ".schema-2.bak")
    assert schema_of(path) == "2" and everything(path) == before and copy.exists()

    again = open_store(path)
    close(again)

    assert schema_of(path) == str(SCHEMA_VERSION) and backups_of(path) == [copy]
    assert everything(copy) == before


def test_the_cause_survives_a_rollback_sqlite_already_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk makes SQLite roll the transaction back itself; the rollback that follows then
    has nothing to undo and raises too. What a person is told is the first, which is the cause."""
    path = v0_1_store(tmp_path)

    def rolled_back_by_sqlite(db: sqlite3.Connection) -> None:
        db.execute("ROLLBACK")
        raise RuntimeError("the disk is full, which is the cause")

    monkeypatch.setitem(storage._MIGRATIONS, 3, [rolled_back_by_sqlite])
    store = Store(path)
    with pytest.raises(RuntimeError, match="the disk is full, which is the cause"):
        store._conn()
    asyncio.run(store.close())


def test_a_copy_that_is_already_there_is_kept_not_replaced(tmp_path: Path) -> None:
    """A copy may be the only one of what it held: it is never written over. A store that is not
    as the copy has it is kept beside it, under the next free name."""
    path = v0_1_store(tmp_path)
    before = everything(path)
    earlier = path.with_name(path.name + ".schema-2.bak")
    write(earlier, "CREATE TABLE older (note TEXT)")
    write(earlier, "INSERT INTO older VALUES ('an earlier start kept this')")
    kept = earlier.read_bytes(), earlier.stat().st_mtime_ns

    store = open_store(path)
    close(store)

    beside = path.with_name(path.name + ".schema-2.1.bak")
    assert store.backup == beside and set(backups_of(path)) == {earlier, beside}
    assert (earlier.read_bytes(), earlier.stat().st_mtime_ns) == kept
    assert rows(earlier, "older") == [("an earlier start kept this",)]
    assert schema_of(beside) == "2" and everything(beside) == before
    assert schema_of(path) == str(SCHEMA_VERSION)


def test_a_store_that_changed_after_a_failed_start_gets_a_copy_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The older Workbench ran again after a start that failed, so what it saved since is in
    no copy that start kept. The next start copies the store as it is, and keeps the first."""
    path = v0_1_store(tmp_path)
    before = everything(path)
    fail_to_open(path, monkeypatch)
    first = path.with_name(path.name + ".schema-2.bak")
    assert backups_of(path) == [first] and everything(first) == before
    kept = first.read_bytes(), first.stat().st_mtime_ns

    add_a_chat(path, "written-after")
    assert schema_of(path) == "2"

    store = open_store(path)
    close(store)

    second = path.with_name(path.name + ".schema-2.1.bak")
    assert store.backup == second and set(backups_of(path)) == {first, second}
    assert (first.read_bytes(), first.stat().st_mtime_ns) == kept, "the first copy is as it was"
    assert "written-after" not in chat_ids(first)
    assert "written-after" in chat_ids(second) and schema_of(second) == "2"
    assert schema_of(path) == str(SCHEMA_VERSION) and "written-after" in chat_ids(path)
    assert list(tmp_path.glob("*.part")) == []


def test_a_store_that_has_not_changed_makes_no_second_copy_however_often_it_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = v0_1_store(tmp_path)
    before = everything(path)
    first = fail_to_open(path, monkeypatch)
    copy = first.backup
    assert copy is not None
    kept = copy.read_bytes(), copy.stat().st_mtime_ns

    for _ in range(3):
        again = fail_to_open(path, monkeypatch)
        assert again.backup == copy
    store = open_store(path)
    close(store)

    assert store.backup == copy and backups_of(path) == [copy]
    assert (copy.read_bytes(), copy.stat().st_mtime_ns) == kept and everything(copy) == before
    assert schema_of(path) == str(SCHEMA_VERSION)


def test_a_store_is_compared_with_the_newest_copy_and_every_earlier_one_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = v0_1_store(tmp_path)
    chats = len(chat_ids(path))
    for generation in range(3):
        fail_to_open(path, monkeypatch)
        add_a_chat(path, f"written-{generation}")
    names = [f"{path.name}.schema-2{number}.bak" for number in ("", ".1", ".2")]
    assert sorted(p.name for p in backups_of(path)) == sorted(names)
    held = [path.with_name(name) for name in names]
    kept = [(p.read_bytes(), p.stat().st_mtime_ns) for p in held]

    last = path.with_name(path.name + ".schema-2.3.bak")
    assert fail_to_open(path, monkeypatch).backup == last, "the third change is in no copy yet"
    assert fail_to_open(path, monkeypatch).backup == last, "the newest copy has what the store has"
    store = open_store(path)
    close(store)
    assert store.backup == last

    assert len(backups_of(path)) == 4, "three earlier copies, one for the last change, no more"
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in held] == kept
    assert [len(chat_ids(p)) for p in (*held, last)] == [chats + n for n in range(4)]


def test_a_copy_that_was_never_finished_is_not_taken_for_one(tmp_path: Path) -> None:
    """The copy is written under another name and renamed: a leftover of a start that
    died while writing it is replaced, and is never the copy that is kept."""
    path = v0_1_store(tmp_path)
    before = everything(path)
    (tmp_path / "workbench.sqlite3.schema-2.bak.part").write_bytes(b"half a copy")

    store = open_store(path)
    close(store)

    assert store.backup is not None and everything(store.backup) == before
    assert list(tmp_path.glob("*.part")) == []


# --- a failure -----------------------------------------------------------------------------------


def _degraded(directory: Path) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Start Workbench on `directory`; what /healthz, an API address and a page say."""
    with TestClient(create_app(settings_in(directory))) as client:
        health = client.get("/healthz")
        assert health.status_code == 200, "a degraded Workbench is up: the agent sees it running"
        api = client.get("/api/status")
        assert api.status_code == 503
        page = client.get("/")
        assert page.status_code == 503
        sign_in = client.get("/signin")
        assert sign_in.status_code == 503
        return health.json(), api.json(), page.text, page.headers["content-security-policy"]


def test_a_migration_that_fails_degrades_naming_the_cause_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = v0_1_store(tmp_path)
    before = everything(path)
    monkeypatch.setitem(
        storage._MIGRATIONS, 6, [*storage._MIGRATIONS[6], "ALTER TABLE nothing_here ADD x TEXT"]
    )

    health, api, page, policy = _degraded(tmp_path)

    # Named three ways, in the same words: the state, the observed cause, and the copy by name.
    copy = path.with_name(path.name + ".schema-2.bak")
    reason = health["reason"]
    assert health["status"] == "degraded"
    assert reason == (
        "Workbench could not open its chat store: no such table: nothing_here. "
        f"A copy is kept beside it as {copy.name}."
    )
    assert api == {"detail": {"message": reason}}
    assert html.escape(reason) in page and "Nothing was changed" in page
    assert "default-src 'self'" in policy, "the page carries Workbench's own headers"
    # The log has the whole account: the kind of error, where the store is, where the copy is.
    assert "OperationalError: no such table: nothing_here" in caplog.text
    assert str(path) in caplog.text and str(copy) in caplog.text
    # And the store is exactly as it was: the transaction rolled back every step.
    assert schema_of(path) == "2" and everything(path) == before


def test_the_page_says_the_cause_as_text_not_as_markup() -> None:
    page = web.degraded_page("OperationalError: near <script>alert(1)</script> & more")

    assert "<script>" not in page
    assert "near &lt;script&gt;alert(1)&lt;/script&gt; &amp; more" in page


def test_a_store_from_a_newer_workbench_is_left_alone_and_named(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "workbench.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT); INSERT INTO meta VALUES ('schema', '999');"
        )

    health, _api, page, _policy = _degraded(tmp_path)

    assert health["reason"] == (
        "Workbench could not open its chat store: unsupported schema 999 from a newer Workbench; "
        f"this Workbench knows schemas {OLDEST_SCHEMA} through {SCHEMA_VERSION}."
    )
    assert f"schemas {OLDEST_SCHEMA} through {SCHEMA_VERSION}" in page
    assert f"{path} has unsupported schema 999" in caplog.text
    assert schema_of(path) == "999" and backups_of(path) == []


def test_a_copy_that_cannot_be_kept_stops_the_migration_before_it_starts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = v0_1_store(tmp_path)
    before = everything(path)
    in_the_way = tmp_path / "workbench.sqlite3.schema-2.bak"
    in_the_way.mkdir()  # a place for the copy that is not a file

    health, _api, _page, _policy = _degraded(tmp_path)

    assert health["reason"] == (
        "Workbench could not open its chat store: workbench.sqlite3.schema-2.bak is not a file, "
        "so the store cannot be copied there."
    )
    assert f"{in_the_way} is not a file, so the store cannot be copied there" in caplog.text
    assert schema_of(path) == "2" and everything(path) == before, "no copy, no migration"


def test_a_file_that_is_not_a_store_degrades_and_is_not_touched(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "workbench.sqlite3"
    garbage = b"this is not a database at all" * 100
    path.write_bytes(garbage)

    health, _api, _page, _policy = _degraded(tmp_path)

    assert health["reason"] == "Workbench could not open its chat store: file is not a database."
    assert "DatabaseError: file is not a database" in caplog.text and str(path) in caplog.text
    assert path.read_bytes() == garbage


# --- what anyone who asks is told ----------------------------------------------------------------
#
# A Workbench that cannot open its store cannot sign anyone in, so `/healthz`, the API's answers
# and the page are said to whoever asks. They name the state and the cause, and no path. The log,
# which only whoever runs Workbench reads, has the whole account.

API_ADDRESSES = ("/api/status", "/v1/models", "/oidc/callback", "/signin")
PAGE_ADDRESSES = ("/", "/chats/c1")
#: Something that points at a place on disk: a drive, or a separator before a file's own name.
A_PLACE = re.compile(r"[A-Za-z]:[\\/]|[\\/][\w.-]+\.(?:sqlite3|bak|part)")


def _a_migration_that_fails(directory: Path, patch: pytest.MonkeyPatch) -> None:
    v0_1_store(directory)
    patch.setitem(
        storage._MIGRATIONS, 6, [*storage._MIGRATIONS[6], "ALTER TABLE nothing_here ADD x TEXT"]
    )


def _a_newer_workbenchs_store(directory: Path, patch: pytest.MonkeyPatch) -> None:
    write(directory / "workbench.sqlite3", "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    write(directory / "workbench.sqlite3", "INSERT INTO meta VALUES ('schema', '999')")


def _a_file_that_is_not_a_database(directory: Path, patch: pytest.MonkeyPatch) -> None:
    (directory / "workbench.sqlite3").write_bytes(b"this is not a database at all" * 100)


def _a_copy_in_the_way(directory: Path, patch: pytest.MonkeyPatch) -> None:
    v0_1_store(directory)
    (directory / "workbench.sqlite3.schema-2.bak").mkdir()


def _a_copy_the_system_refuses(directory: Path, patch: pytest.MonkeyPatch) -> None:
    v0_1_store(directory)
    real = os.replace

    def refuse(source: Any, target: Any) -> None:
        if str(target).endswith(".bak"):
            raise PermissionError(errno.EACCES, "Permission denied", str(target))
        real(source, target)

    patch.setattr(os, "replace", refuse)


def _a_python_without_full_text_search(directory: Path, patch: pytest.MonkeyPatch) -> None:
    broken = [s.replace("USING fts5(", "USING fts5x(") for s in storage._MEMORY_SCHEMA]
    patch.setattr(storage, "_MEMORY_SCHEMA", broken)


#: (what sets it up, the cause the public reason carries, what the log carries besides the store's path)
CASES = [
    pytest.param(
        _a_migration_that_fails,
        "no such table: nothing_here",
        lambda d: [
            "OperationalError: no such table: nothing_here",
            str(d / "workbench.sqlite3.schema-2.bak"),
        ],
        id="a migration that fails",
    ),
    pytest.param(
        _a_newer_workbenchs_store,
        "unsupported schema 999 from a newer Workbench",
        lambda d: [f"{d / 'workbench.sqlite3'} has unsupported schema 999 from a newer Workbench"],
        id="a newer Workbench's store",
    ),
    pytest.param(
        _a_file_that_is_not_a_database,
        "file is not a database",
        lambda d: ["DatabaseError: file is not a database"],
        id="a file that is not a database",
    ),
    pytest.param(
        _a_copy_in_the_way,
        "workbench.sqlite3.schema-2.bak is not a file",
        lambda d: [f"{d / 'workbench.sqlite3.schema-2.bak'} is not a file"],
        id="a copy that cannot be made: something is in its place",
    ),
    pytest.param(
        _a_copy_the_system_refuses,
        "Permission denied",
        # An operating system error carries its path in quotes, as repr writes it.
        lambda d: ["PermissionError", repr(str(d / "workbench.sqlite3.schema-2.bak"))],
        id="a copy that cannot be made: the system refuses it",
    ),
    pytest.param(
        _a_python_without_full_text_search,
        "built without full-text search (FTS5)",
        lambda d: [f"{d / 'workbench.sqlite3'}: this Python's SQLite was built without full-text"],
        id="a Python without full-text search",
    ),
]


@pytest.mark.parametrize(("setup", "cause", "logged"), CASES)
def test_a_degraded_workbench_names_no_path_to_anyone_and_its_log_does(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    setup: Callable[[Path, pytest.MonkeyPatch], None],
    cause: str,
    logged: Callable[[Path], list[str]],
) -> None:
    setup(tmp_path, monkeypatch)

    with TestClient(create_app(settings_in(tmp_path))) as client:
        responses = {a: client.get(a) for a in ("/healthz", *API_ADDRESSES, *PAGE_ADDRESSES)}

    # All four answers carry one reason, which leads with the state and names the cause.
    assert responses["/healthz"].status_code == 200
    reason = responses["/healthz"].json()["reason"]
    assert reason.startswith("Workbench could not open its chat store: ") and cause in reason
    assert "\n" not in reason, "one line"
    for address in API_ADDRESSES:
        assert responses[address].status_code == 503, address
        assert responses[address].json() == {"detail": {"message": reason}}, address
    for address in PAGE_ADDRESSES:
        assert responses[address].status_code == 503, address
        assert html.escape(reason) in responses[address].text, address
    # And no body points at a place on disk: not the data directory, absolute or as a name in a
    # relative path, and no separator before a file's name.
    for address, response in responses.items():
        for place in (str(tmp_path), tmp_path.as_posix(), tmp_path.name, tmp_path.parent.name):
            assert place not in response.text, (address, place)
        assert not A_PLACE.search(response.text), (address, A_PLACE.search(response.text))
    # The log line has them all.
    assert str(tmp_path / "workbench.sqlite3") in caplog.text
    for text in logged(tmp_path):
        assert text in caplog.text, text


def test_a_data_directory_given_as_a_relative_path_is_not_named_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "somewhere-odd").mkdir()
    (tmp_path / "somewhere-odd" / "workbench.sqlite3").write_bytes(b"not a database" * 100)

    with TestClient(create_app(settings_in(Path("somewhere-odd")))) as client:
        bodies = [client.get(a).text for a in ("/healthz", "/api/status", "/signin", "/")]

    assert all("somewhere-odd" not in body and not A_PLACE.search(body) for body in bodies)
    assert all("file is not a database" in body for body in bodies)
    assert str(Path("somewhere-odd") / "workbench.sqlite3") in caplog.text


def test_a_system_error_is_told_by_what_it_says_and_not_by_the_path_it_carries() -> None:
    refused = PermissionError(errno.EACCES, "Permission denied", r"C:\Users\someone\data\x.sqlite3")

    assert "someone" in str(refused), "what str() says carries the path"
    assert storage.public_cause(refused) == "Permission denied"
    assert storage.public_cause(OSError("with no number")) == "OSError"
    assert storage.public_cause(sqlite3.OperationalError("no such table: t")) == "no such table: t"
    assert storage.public_cause(sqlite3.DatabaseError()) == "DatabaseError"
    assert storage.public_cause(KeyError(r"C:\Users\someone")) == "an unexpected KeyError"


def test_a_workbench_that_starts_degraded_starts_again_when_the_cause_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = v0_1_store(tmp_path)
    broken = [*storage._MIGRATIONS[6], "ALTER TABLE nothing_here ADD x TEXT"]
    with monkeypatch.context() as patch:
        patch.setitem(storage._MIGRATIONS, 6, broken)
        health, _api, _page, _policy = _degraded(tmp_path)
        assert health["status"] == "degraded"

    with TestClient(create_app(settings_in(tmp_path))) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        assert client.get("/api/status").status_code == 200

    assert schema_of(path) == str(SCHEMA_VERSION)
    assert len(backups_of(path)) == 1, "the retry reused the copy the first start kept"


def test_a_healthy_workbench_is_not_degraded(tmp_path: Path) -> None:
    with TestClient(create_app(settings_in(tmp_path))) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/api/status").status_code == 200
