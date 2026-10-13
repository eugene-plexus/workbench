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
import json
import sqlite3
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


# --- converted correctly, kept, recorded -------------------------------------------------------


def test_the_store_v0_1_wrote_is_migrated_and_every_chat_comes_through(tmp_path: Path) -> None:
    path = v0_1_store(tmp_path)
    before = everything(path)
    assert schema_of(path) == "2", "the fixture is the old shape"

    store = open_store(path)
    try:
        # The version is recorded.
        assert schema_of(path) == str(SCHEMA_VERSION) == "13"
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
    assert everything(path) == after_first and schema_of(path) == "13"


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

    assert schema_of(path) == "13" and again.backup == copy
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

    assert schema_of(path) == "13" and backups_of(path) == [copy]
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
    """One copy per schema: the one there is the older, and may be the only one of what it held."""
    path = v0_1_store(tmp_path)
    earlier = path.with_name(path.name + ".schema-2.bak")
    with sqlite3.connect(earlier) as db:
        db.execute("CREATE TABLE older (note TEXT)")
        db.execute("INSERT INTO older VALUES ('an earlier start kept this')")

    store = open_store(path)
    close(store)

    assert store.backup == earlier and backups_of(path) == [earlier]
    assert rows(earlier, "older") == [("an earlier start kept this",)]
    assert schema_of(path) == "13"


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

    # Named three ways, with the observed cause and where the copy is.
    reason = health["reason"]
    assert health["status"] == "degraded"
    assert "could not open its saved chats" in reason
    assert "OperationalError" in reason and "nothing_here" in reason
    assert str(path.with_name(path.name + ".schema-2.bak")) in reason
    assert api == {"detail": {"message": reason}}
    assert "nothing_here" in page and "Nothing was changed" in page
    assert "default-src 'self'" in policy, "the page carries Workbench's own headers"
    assert reason in caplog.text
    # And the store is exactly as it was: the transaction rolled back every step.
    assert schema_of(path) == "2" and everything(path) == before


def test_the_page_says_the_cause_as_text_not_as_markup() -> None:
    page = web.degraded_page("OperationalError: near <script>alert(1)</script> & more")

    assert "<script>" not in page
    assert "near &lt;script&gt;alert(1)&lt;/script&gt; &amp; more" in page


def test_a_store_from_a_newer_workbench_is_left_alone_and_named(tmp_path: Path) -> None:
    path = tmp_path / "workbench.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT); INSERT INTO meta VALUES ('schema', '999');"
        )

    health, _api, page, _policy = _degraded(tmp_path)

    assert "unsupported schema 999 from a newer Workbench" in health["reason"]
    assert f"schemas {OLDEST_SCHEMA} through {SCHEMA_VERSION}" in page
    assert schema_of(path) == "999" and backups_of(path) == []


def test_a_copy_that_cannot_be_kept_stops_the_migration_before_it_starts(tmp_path: Path) -> None:
    path = v0_1_store(tmp_path)
    before = everything(path)
    in_the_way = tmp_path / "workbench.sqlite3.schema-2.bak"
    in_the_way.mkdir()  # a place for the copy that is not a file

    health, _api, _page, _policy = _degraded(tmp_path)

    assert "is not a file, so the store cannot be copied there" in health["reason"]
    assert "OSError" in health["reason"]
    assert schema_of(path) == "2" and everything(path) == before, "no copy, no migration"


def test_a_file_that_is_not_a_store_degrades_and_is_not_touched(tmp_path: Path) -> None:
    path = tmp_path / "workbench.sqlite3"
    garbage = b"this is not a database at all" * 100
    path.write_bytes(garbage)

    health, _api, _page, _policy = _degraded(tmp_path)

    assert "DatabaseError" in health["reason"] and "not a database" in health["reason"]
    assert path.read_bytes() == garbage


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

    assert schema_of(path) == "13"
    assert len(backups_of(path)) == 1, "the retry reused the copy the first start kept"


def test_a_healthy_workbench_is_not_degraded(tmp_path: Path) -> None:
    with TestClient(create_app(settings_in(tmp_path))) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/api/status").status_code == 200
