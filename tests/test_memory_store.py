"""A person's notes in the store: filtered by owner in every query, deleted for
real, found by their words, and carried across from schema 9 (MR1).

The store is exercised directly, on a real SQLite file, because the claims here
are about rows: what a query can reach, what a delete leaves behind, what the
index and the log keep.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.store import (
    Chat,
    MemoryFull,
    MemoryNote,
    Store,
    note_key,
)

LIMIT = 300


def make(owner: str, text: str, *, id: str | None = None, at: float = 1.0, **kw: Any) -> MemoryNote:
    return MemoryNote(
        id=id or f"{owner}-{abs(hash(text))}",
        owner=owner,
        text=text,
        kind=kw.pop("kind", "fact"),
        created_at=at,
        updated_at=at,
        **kw,
    )


def run(path: Path, body: Callable[[Store], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        store = Store(path)
        await store.open()
        try:
            return await body(store)
        finally:
            await store.close()

    return asyncio.run(go())


def rows(path: Path, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with sqlite3.connect(path) as db:
        return db.execute(sql, params).fetchall()


def test_this_python_has_full_text_search(tmp_path: Path) -> None:
    """Memory's search is FTS5, and it is built into this Python's SQLite.
    A Python without it fails here, as it would fail to open the store."""
    with sqlite3.connect(tmp_path / "x.sqlite3") as db:
        db.execute("CREATE VIRTUAL TABLE t USING fts5(text)")
        db.execute("INSERT INTO t (text) VALUES ('one two')")
        assert db.execute("SELECT count(*) FROM t WHERE t MATCH 'two'").fetchone() == (1,)


def test_a_python_without_full_text_search_says_so_when_the_store_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = [s.replace("USING fts5(", "USING fts5x(") for s in storage._MEMORY_SCHEMA]
    monkeypatch.setattr(storage, "_MEMORY_SCHEMA", broken)
    store = Store(tmp_path / "workbench.sqlite3")
    try:
        with pytest.raises(RuntimeError) as caught:
            store._conn()
    finally:
        asyncio.run(store.close())
    assert "without full-text search" in str(caught.value) and "FTS5" in str(caught.value)
    assert "Reinstall Workbench" in str(caught.value)


def test_a_note_is_kept_with_its_owner_and_read_back(tmp_path: Path) -> None:
    async def body(store: Store) -> None:
        note, created = await store.add_memory_note(
            make("ada", "I use metric units", id="a1", source="said", source_chat_id="c1"),
            limit=LIMIT,
        )
        assert created and note.id == "a1"
        [found] = await store.memory_notes("ada")
        assert (found.text, found.kind, found.status, found.pinned) == (
            "I use metric units",
            "fact",
            "active",
            False,
        )
        assert (found.source, found.source_chat_id, found.use_count) == ("said", "c1", 0)
        assert await store.memory_note("ada", "a1") == found
        assert await store.memory_count("ada") == 1

    run(tmp_path / "w.sqlite3", body)


def test_no_method_reaches_a_note_that_is_not_the_askers(tmp_path: Path) -> None:
    """Every read and write puts the owner in its own query: not one of them
    can be asked for a note by its id alone."""

    async def body(store: Store) -> None:
        await store.add_memory_note(make("ada", "My code word is plum", id="a1"), limit=LIMIT)
        await store.add_memory_note(make("bo", "I like tea", id="b1"), limit=LIMIT)
        assert [n.id for n in await store.memory_notes("bo")] == ["b1"]
        assert await store.memory_notes("nobody") == []
        assert await store.memory_note("bo", "a1") is None
        assert await store.memory_count("bo") == 1
        assert await store.update_memory_note("bo", "a1", text="hacked") is None
        assert await store.update_memory_note("bo", "a1", pinned=True) is None
        assert await store.delete_memory_note("bo", "a1") is False
        assert [n.text for n in await store.search_memory("bo", '"plum"')] == []
        assert [n.text for n in await store.search_memory("ada", '"plum"')] == [
            "My code word is plum"
        ]
        await store.mark_memory_used("bo", ["a1"], 5.0)
        assert (await store.memory_note("ada", "a1")).use_count == 0  # type: ignore[union-attr]
        await store.create_chat(
            Chat(id="c-ada", owner="ada", title="t", model=None, created_at=1, updated_at=1)
        )
        assert await store.set_memory_snapshot("c-ada", "bo", {"x": 1}) is False
        assert (await store.chat("c-ada")).memory_snapshot is None  # type: ignore[union-attr]
        # Their settings are their own too.
        await store.put_memory_prefs("ada", enabled=False, external="never")
        assert (await store.memory_prefs("bo"))["enabled"] is True
        assert (await store.memory_prefs("bo"))["external"] is None
        # And what is left is untouched.
        [mine] = await store.memory_notes("ada")
        assert (mine.text, mine.pinned) == ("My code word is plum", False)
        assert await store.delete_memory_notes("bo") == 1
        assert [n.id for n in await store.memory_notes("ada")] == ["a1"]

    run(tmp_path / "w.sqlite3", body)


def test_the_same_words_are_not_kept_twice_and_the_limit_is_kept(tmp_path: Path) -> None:
    async def body(store: Store) -> None:
        _, made = await store.add_memory_note(make("ada", "I use metric", id="a1"), limit=2)
        assert made
        again, made = await store.add_memory_note(
            make("ada", "  i USE   Metric ", id="a2"), limit=2
        )
        assert not made and again.id == "a1", "case and spacing do not make a new note"
        await store.add_memory_note(make("ada", "I like tea", id="a3"), limit=2)
        with pytest.raises(MemoryFull) as caught:
            await store.add_memory_note(make("ada", "I like coffee", id="a4"), limit=2)
        assert caught.value.limit == 2 and await store.memory_count("ada") == 2
        # The same words are not refused at the limit: they are not a new note.
        _, made = await store.add_memory_note(make("ada", "I like tea", id="a5"), limit=2)
        assert not made
        # Another person has their own room.
        await store.add_memory_note(make("bo", "I use metric", id="b1"), limit=2)
        assert [n.id for n in await store.memory_notes("bo")] == ["b1"]

    run(tmp_path / "w.sqlite3", body)
    assert note_key("  Café  AU  lait ") == note_key("café au lait")


def test_notes_come_back_newest_first_and_an_edit_changes_what_it_names(tmp_path: Path) -> None:
    async def body(store: Store) -> None:
        for i, text in enumerate(("old", "middle", "new")):
            await store.add_memory_note(make("ada", text, id=f"n{i}", at=float(i)), limit=LIMIT)
        assert [n.text for n in await store.memory_notes("ada")] == ["new", "middle", "old"]
        edited = await store.update_memory_note("ada", "n0", text="older", kind="instruction")
        assert edited is not None
        assert (edited.text, edited.kind, edited.pinned) == ("older", "instruction", False)
        assert edited.updated_at > edited.created_at
        pinned = await store.update_memory_note("ada", "n0", pinned=True)
        assert pinned is not None and pinned.pinned and pinned.text == "older"
        assert (await store.update_memory_note("ada", "missing", text="x")) is None
        # Nothing to change is not a change: the row is left as it was.
        same = await store.update_memory_note("ada", "n1")
        assert same is not None and same.updated_at == same.created_at

    run(tmp_path / "w.sqlite3", body)


def test_notes_made_in_the_same_instant_are_still_newest_first(tmp_path: Path) -> None:
    """A clock that ticks in 15 ms steps gives notes made together one moment;
    "newest first" is how a block fills its budget, so it must not be a toss-up."""

    async def body(store: Store) -> None:
        for i, text in enumerate(("first", "second", "third")):
            await store.add_memory_note(make("ada", text, id=f"n{i}", at=5.0), limit=LIMIT)
        found = await store.memory_notes("ada")
        assert [n.text for n in found] == ["third", "second", "first"]
        assert len({n.created_at for n in found}) == 3
        assert all(n.created_at >= 5.0 for n in found) and found[-1].created_at == 5.0
        # Another person's notes do not share the sequence.
        await store.add_memory_note(make("bo", "theirs", id="b0", at=5.0), limit=LIMIT)
        assert (await store.memory_note("bo", "b0")).created_at == 5.0  # type: ignore[union-attr]
        # A note made in a later moment keeps its own time.
        await store.add_memory_note(make("ada", "later", id="n3", at=9.0), limit=LIMIT)
        assert (await store.memory_note("ada", "n3")).created_at == 9.0  # type: ignore[union-attr]

    run(tmp_path / "w.sqlite3", body)


def test_the_index_follows_every_insert_edit_and_delete(tmp_path: Path) -> None:
    path = tmp_path / "w.sqlite3"

    async def body(store: Store) -> None:
        await store.add_memory_note(make("ada", "Lives in Denver", id="a1"), limit=LIMIT)
        await store.add_memory_note(make("ada", "Prefers short answers", id="a2"), limit=LIMIT)
        # Words are stemmed, as people say them: "lives" is found by "live".
        assert [n.id for n in await store.search_memory("ada", '"live"')] == ["a1"]
        await store.update_memory_note("ada", "a1", text="Lives in Austin")
        assert await store.search_memory("ada", '"denver"') == []
        assert [n.id for n in await store.search_memory("ada", '"austin"')] == ["a1"]
        # A change that is not to the words leaves the index alone.
        await store.update_memory_note("ada", "a1", pinned=True)
        assert [n.id for n in await store.search_memory("ada", '"austin"')] == ["a1"]
        await store.delete_memory_note("ada", "a1")
        assert await store.search_memory("ada", '"austin"') == []
        assert [n.id for n in await store.search_memory("ada", '"short"')] == ["a2"]
        # Best match first, and only the person's own are ranked.
        await store.add_memory_note(make("ada", "short short short", id="a3"), limit=LIMIT)
        await store.add_memory_note(make("bo", "short short short short", id="b1"), limit=LIMIT)
        found = await store.search_memory("ada", '"short" OR "answers"')
        assert [n.id for n in found] == ["a2", "a3"] or [n.id for n in found] == ["a3", "a2"]
        assert "b1" not in [n.id for n in found]
        assert len(await store.search_memory("ada", '"short"', limit=1)) == 1

    run(path, body)
    # The index agrees with the notes it covers.
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO memory_notes_fts (memory_notes_fts) VALUES ('integrity-check')")


def test_deleting_a_note_is_real(tmp_path: Path) -> None:
    """The row, its index entry, the words in its log and every chat's copy of
    it: nothing of it is left but the fact that a note was deleted."""
    path = tmp_path / "w.sqlite3"
    secret = "My code word is plum"

    async def body(store: Store) -> None:
        await store.add_memory_note(make("ada", secret, id="a1"), limit=LIMIT)
        await store.add_memory_note(make("ada", "I like tea", id="a2"), limit=LIMIT)
        await store.update_memory_note("ada", "a1", text="My code word is damson")
        await store.create_chat(
            Chat(id="c1", owner="ada", title="t", model="m", created_at=1, updated_at=1)
        )
        await store.create_chat(
            Chat(id="c2", owner="ada", title="t", model="m", created_at=1, updated_at=1)
        )
        for chat_id, ids in (("c1", ["a1", "a2"]), ("c2", ["a2"])):
            await store.set_memory_snapshot(
                chat_id,
                "ada",
                {
                    "text": "x",
                    "ids": ids,
                    "notes": [{"id": i, "text": "My code word"} for i in ids],
                },
            )
        assert (await store.chat("c1")).memory_snapshot is not None  # type: ignore[union-attr]
        assert await store.delete_memory_note("ada", "a1")

    run(path, body)
    assert rows(path, "SELECT id FROM memory_notes WHERE id = 'a1'") == []
    assert (
        rows(path, "SELECT rowid FROM memory_notes_fts WHERE memory_notes_fts MATCH 'damson'") == []
    )
    assert (
        rows(path, "SELECT rowid FROM memory_notes_fts WHERE memory_notes_fts MATCH 'plum'") == []
    )
    events = rows(path, "SELECT event, old_text, new_text FROM memory_events WHERE note_id = 'a1'")
    assert [e[0] for e in events] == ["add", "edit", "delete"], (
        "the log says a note was kept and lost"
    )
    assert all(old is None and new is None for _, old, new in events), "and keeps none of its words"
    assert "plum" not in json.dumps(rows(path, "SELECT * FROM memory_events"))
    assert "damson" not in json.dumps(rows(path, "SELECT * FROM memory_events"))
    # The other note's log is untouched.
    assert rows(path, "SELECT new_text FROM memory_events WHERE note_id = 'a2'") == [
        ("I like tea",)
    ]
    # Chat c1 had been sent the deleted note, so its copy is gone and the next
    # request takes a new one; c2 never had it and keeps its copy.
    saved = dict(rows(path, "SELECT id, memory_snapshot IS NULL FROM chats"))
    assert saved == {"c1": 1, "c2": 0}
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO memory_notes_fts (memory_notes_fts) VALUES ('integrity-check')")


def test_deleting_every_note_leaves_no_words_and_no_chat_copy(tmp_path: Path) -> None:
    path = tmp_path / "w.sqlite3"

    async def body(store: Store) -> None:
        for i, text in enumerate(("alpha note", "beta note", "gamma note")):
            await store.add_memory_note(make("ada", text, id=f"a{i}"), limit=LIMIT)
        await store.add_memory_note(make("bo", "delta note", id="b0"), limit=LIMIT)
        await store.create_chat(
            Chat(id="c1", owner="ada", title="t", model="m", created_at=1, updated_at=1)
        )
        await store.create_chat(
            Chat(id="c2", owner="bo", title="t", model="m", created_at=1, updated_at=1)
        )
        await store.set_memory_snapshot("c1", "ada", {"ids": ["a0"]})
        await store.set_memory_snapshot("c2", "bo", {"ids": ["b0"]})
        assert await store.delete_memory_notes("ada") == 3
        assert await store.memory_notes("ada") == []
        assert [n.id for n in await store.memory_notes("bo")] == ["b0"]

    run(path, body)
    assert (
        rows(path, "SELECT rowid FROM memory_notes_fts WHERE memory_notes_fts MATCH 'note'") != []
    )
    assert [r[0] for r in rows(path, "SELECT text FROM memory_notes")] == ["delta note"]
    assert all(
        old is None and new is None
        for old, new in rows(
            path, "SELECT old_text, new_text FROM memory_events WHERE note_id IN ('a0','a1','a2')"
        )
    )
    assert dict(rows(path, "SELECT id, memory_snapshot IS NULL FROM chats")) == {"c1": 1, "c2": 0}


def test_the_database_itself_refuses_what_a_note_may_not_be(tmp_path: Path) -> None:
    """The CHECKs hold whatever wrote the row."""
    path = tmp_path / "w.sqlite3"
    run(path, lambda store: store.open())
    insert = (
        "INSERT INTO memory_notes (id, owner, text, kind, status, source, created_at, updated_at) "
        "VALUES ('x', 'ada', ?, ?, ?, ?, 1, 1)"
    )
    with sqlite3.connect(path) as db:
        db.execute(insert, ("fine", "fact", "active", "typed"))
        db.execute("DELETE FROM memory_notes")
        for text, kind, status, source in (
            ("a" * 241, "fact", "active", "typed"),
            ("fine", "secret", "active", "typed"),
            ("fine", "fact", "gone", "typed"),
            ("fine", "fact", "active", "robot"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(insert, (text, kind, status, source))
        db.execute(insert, ("a" * 240, "event", "pending", "suggested"))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO memory_prefs (owner, external) VALUES ('ada', 'sometimes')")


def test_a_persons_memory_settings_start_as_the_defaults_and_change_one_at_a_time(
    tmp_path: Path,
) -> None:
    async def body(store: Store) -> None:
        assert await store.memory_prefs("ada") == storage.MEMORY_PREFS_DEFAULT
        # Changing one setting does not choose another for the person: the
        # outside-model setting stays "whatever the install's default is".
        changed = await store.put_memory_prefs("ada", enabled=False)
        assert changed["enabled"] is False and changed["external"] is None
        changed = await store.put_memory_prefs("ada", external="never")
        assert changed["external"] == "never" and changed["enabled"] is False
        changed = await store.put_memory_prefs("ada", enabled=True, budget_tokens=500)
        assert (changed["enabled"], changed["external"], changed["budget_tokens"]) == (
            True,
            "never",
            500,
        )
        assert (await store.put_memory_prefs("ada", external=None))["external"] is None
        await store.put_memory_prefs("ada", external="never")
        assert (await store.put_memory_prefs("ada", budget_tokens=None))["budget_tokens"] is None
        with pytest.raises(ValueError):
            await store.put_memory_prefs("ada", favourite_colour="red")
        with pytest.raises(sqlite3.IntegrityError):
            await store.put_memory_prefs("ada", external="sometimes")
        assert (await store.memory_prefs("ada"))["external"] == "never", "a refusal changes nothing"

    run(tmp_path / "w.sqlite3", body)


def test_a_notes_use_is_counted_when_a_chat_is_sent_it(tmp_path: Path) -> None:
    async def body(store: Store) -> None:
        await store.add_memory_note(make("ada", "I use metric", id="a1"), limit=LIMIT)
        await store.add_memory_note(make("ada", "I like tea", id="a2"), limit=LIMIT)
        await store.mark_memory_used("ada", ["a1"], 9.0)
        await store.mark_memory_used("ada", ["a1", "a2"], 10.0)
        await store.mark_memory_used("ada", [], 11.0)
        used = {n.id: (n.use_count, n.last_used_at) for n in await store.memory_notes("ada")}
        assert used == {"a1": (2, 10.0), "a2": (1, 10.0)}

    run(tmp_path / "w.sqlite3", body)


def test_a_chats_copy_of_its_notes_is_kept_and_a_bad_one_is_none(tmp_path: Path) -> None:
    path = tmp_path / "w.sqlite3"

    async def body(store: Store) -> None:
        await store.create_chat(
            Chat(id="c1", owner="ada", title="t", model="m", created_at=1, updated_at=1)
        )
        assert (await store.chat("c1")).memory_snapshot is None  # type: ignore[union-attr]
        assert await store.set_memory_snapshot("c1", "ada", {"text": "x", "ids": ["a"]})
        assert (await store.chat("c1")).memory_snapshot == {"text": "x", "ids": ["a"]}  # type: ignore[union-attr]
        assert await store.set_memory_snapshot("c1", "ada", None)
        assert (await store.chat("c1")).memory_snapshot is None  # type: ignore[union-attr]
        assert await store.set_memory_snapshot("nope", "ada", {}) is False

    run(path, body)
    with sqlite3.connect(path) as db:
        for junk in ("not json", "[1, 2]", "42", ""):
            db.execute("UPDATE chats SET memory_snapshot = ?", (junk,))
            [(value,)] = db.execute("SELECT memory_snapshot FROM chats").fetchall()
            assert storage._snapshot(value) is None


# --------------------------------------------------------------------------- #
# schema 9 to 10
# --------------------------------------------------------------------------- #

#: Workbench's schema 9 as `530740f` made it: the file a person's install holds
#: the day this lands, chats and all.
_V9 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta (key, value) VALUES ('schema', '9');
CREATE TABLE people (sub TEXT PRIMARY KEY, name TEXT NOT NULL, username TEXT, role TEXT NOT NULL,
    first_seen REAL NOT NULL, last_seen REAL NOT NULL);
CREATE TABLE sessions (id_hash TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, sub TEXT NOT NULL,
    refresh_token TEXT, access_expires_at REAL NOT NULL, created_at REAL NOT NULL,
    expires_at REAL NOT NULL);
CREATE TABLE chats (id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, model TEXT,
    settings TEXT NOT NULL DEFAULT '{}', search INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX chats_by_owner ON chats (owner, updated_at);
CREATE TABLE messages (id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, seq INTEGER NOT NULL,
    role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', reasoning TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL, error TEXT,
    sources TEXT NOT NULL DEFAULT '[]', searches INTEGER NOT NULL DEFAULT 0,
    search INTEGER NOT NULL DEFAULT 0, model TEXT, finish TEXT, created_at REAL NOT NULL,
    finished_at REAL, answer_from INTEGER, reasoning_from INTEGER,
    tool_rounds TEXT NOT NULL DEFAULT '[]', parent_id TEXT, chosen INTEGER NOT NULL DEFAULT 1,
    search_suggestions TEXT NOT NULL DEFAULT '[]', output_cap TEXT);
CREATE INDEX messages_by_chat ON messages (chat_id, seq);
CREATE TABLE files (id TEXT PRIMARY KEY, owner TEXT NOT NULL, chat_id TEXT, name TEXT NOT NULL,
    media_type TEXT NOT NULL, size INTEGER NOT NULL, created_at REAL NOT NULL, media_id TEXT,
    width INTEGER, height INTEGER);
CREATE TABLE media (id TEXT PRIMARY KEY, owner TEXT NOT NULL, door TEXT NOT NULL,
    kind TEXT NOT NULL, model TEXT, request TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL,
    created_at REAL NOT NULL, finished_at REAL, served TEXT, units TEXT, text TEXT, error TEXT,
    job TEXT);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE tool_servers (id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,
    token TEXT NOT NULL, transport TEXT NOT NULL DEFAULT 'http', command TEXT NOT NULL DEFAULT '',
    args TEXT NOT NULL DEFAULT '[]', environment TEXT NOT NULL DEFAULT '{}');
CREATE TABLE folder_grants (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
    identity TEXT NOT NULL, subject TEXT NOT NULL, writable INTEGER NOT NULL);
INSERT INTO chats (id, owner, title, model, settings, search, created_at, updated_at)
VALUES ('c1', 'ada', 'Quarterly numbers', 'local-model',
        '{"instructions": "Be brief.", "temperature": 0.2}', 1, 100.0, 200.0);
INSERT INTO messages (id, chat_id, seq, role, content, status, created_at, parent_id)
VALUES ('m1', 'c1', 1, 'user', 'How much?', 'done', 101.0, NULL),
       ('m2', 'c1', 2, 'assistant', 'About 40.', 'done', 102.0, 'm1');
INSERT INTO settings (key, value) VALUES ('ownerReadsChats', 'true');
"""


def test_a_schema_9_file_gains_memory_and_keeps_every_chat(tmp_path: Path) -> None:
    path = tmp_path / "schema9.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_V9)

    async def body(store: Store) -> None:
        [chat] = await store.chats("ada")
        assert (chat.id, chat.title, chat.model, chat.search) == (
            "c1",
            "Quarterly numbers",
            "local-model",
            True,
        )
        assert chat.settings == {"instructions": "Be brief.", "temperature": 0.2}
        assert chat.memory_snapshot is None, "an old chat has had nothing sent from memory"
        assert [(m.role, m.content) for m in await store.messages("c1")] == [
            ("user", "How much?"),
            ("assistant", "About 40."),
        ]
        assert await store.setting("ownerReadsChats") is True
        # Memory works at once, index and all.
        await store.add_memory_note(make("ada", "Lives in Denver", id="a1"), limit=LIMIT)
        assert [n.id for n in await store.search_memory("ada", '"denver"')] == ["a1"]
        assert await store.set_memory_snapshot("c1", "ada", {"ids": ["a1"]})
        assert await store.delete_memory_note("ada", "a1")
        assert (await store.chat("c1")).memory_snapshot is None  # type: ignore[union-attr]

    run(path, body)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone() == ("15",)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        triggers = {
            r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        }
        columns = {r[1] for r in db.execute("PRAGMA table_info(chats)")}
    assert {"memory_notes", "memory_events", "memory_prefs", "memory_notes_fts"} <= tables
    assert triggers == {"memory_notes_ai", "memory_notes_ad", "memory_notes_au"}
    assert "memory_snapshot" in columns
    # A second boot repeats nothing that fails.
    run(path, lambda store: store.open())


def test_a_new_store_and_a_migrated_one_have_the_same_memory_tables(tmp_path: Path) -> None:
    old, new = tmp_path / "old.sqlite3", tmp_path / "new.sqlite3"
    with sqlite3.connect(old) as db:
        db.executescript(_V9)
    run(old, lambda store: store.open())
    run(new, lambda store: store.open())

    def shape(path: Path) -> dict[str, list[tuple[Any, ...]]]:
        with sqlite3.connect(path) as db:
            names = [
                r[0]
                for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'memory_%' "
                    "AND name NOT LIKE 'memory_notes_fts_%' ORDER BY name"
                )
            ]
            out = {n: db.execute(f"PRAGMA table_info({n})").fetchall() for n in names}
            out["chats"] = db.execute("PRAGMA table_info(chats)").fetchall()
            out["sql"] = sorted(
                db.execute(
                    "SELECT name, sql FROM sqlite_master WHERE name LIKE 'memory_%' "
                    "AND name NOT LIKE 'memory_notes_fts_%'"
                ).fetchall()
            )
            return out

    assert shape(old)["memory_notes"] == shape(new)["memory_notes"]
    assert shape(old)["sql"] == shape(new)["sql"]
    assert [c[1] for c in shape(old)["chats"]] == [c[1] for c in shape(new)["chats"]]
