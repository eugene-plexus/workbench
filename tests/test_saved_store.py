"""Schema 12 (MR4): what belongs with the chats. An older store opens and keeps every
chat; a message keeps what searching a person's documents came to; a person's own
settings for their documents are kept one key at a time; and the boot can ask whose
chats exist.
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
from eugene_plexus_workbench.store import SCHEMA_VERSION, Chat, Message, Store

from .test_documents_store import schema_10


def run(path: Path, body: Callable[[Store], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        store = Store(path)
        await store.open()
        try:
            return await body(store)
        finally:
            await store.close()

    return asyncio.run(go())


def schema_11(path: Path) -> None:
    """A store as Workbench left it the day before this: documents read, no search."""
    schema_10(path)
    with sqlite3.connect(path) as db:
        for statement in storage._MIGRATIONS[11]:
            if callable(statement):
                statement(db)
            else:
                db.execute(statement)
        db.execute("UPDATE meta SET value = '11' WHERE key = 'schema'")
        db.execute(
            "INSERT INTO messages (id, chat_id, seq, role, status, created_at, content, sources) "
            "VALUES ('old1', 'c1', 50, 'assistant', 'done', 1.0, 'An answer from before.', ?)",
            (json.dumps([{"kind": "web", "url": "https://a.example", "title": "A"}]),),
        )


def test_a_schema_11_store_gains_retrieval_and_the_prefs_table_and_keeps_every_chat(
    tmp_path: Path,
) -> None:
    path = tmp_path / "w.sqlite3"
    schema_11(path)

    async def body(store: Store) -> None:
        [chat] = await store.chats("ada")
        assert chat.id == "c1"
        [message] = [m for m in await store.tree("c1") if m.id == "old1"]
        assert message.content == "An answer from before." and message.retrieval is None
        assert message.sources == [{"kind": "web", "url": "https://a.example", "title": "A"}]
        await store.update_message("old1", retrieval={"status": "used", "passages": 2})
        assert (await store.message("old1")).retrieval == {"status": "used", "passages": 2}  # type: ignore[union-attr]
        assert await store.documents_prefs("ada") == {"new_chats": False, "external": None}

    run(path, body)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone() == (
            str(SCHEMA_VERSION),
        )
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "documents_prefs" in tables
        assert "retrieval" in {r[1] for r in db.execute("PRAGMA table_info(messages)")}
    run(path, lambda store: store.open())  # a second boot does not run it again


def test_a_new_store_and_a_migrated_one_have_the_same_messages_and_prefs_tables(
    tmp_path: Path,
) -> None:
    old, new = tmp_path / "old.sqlite3", tmp_path / "new.sqlite3"
    schema_11(old)
    run(old, lambda store: store.open())
    run(new, lambda store: store.open())

    def shape(path: Path, table: str) -> list[tuple[Any, ...]]:
        with sqlite3.connect(path) as db:
            return [tuple(r[1:3]) for r in db.execute(f"PRAGMA table_info({table})")]

    for table in ("messages", "documents_prefs"):
        assert shape(old, table) == shape(new, table), table
    assert shape(new, "messages")[-1] == ("retrieval", "TEXT")


def test_a_messages_retrieval_is_kept_and_read_back_and_none_is_none(tmp_path: Path) -> None:
    async def body(store: Store) -> None:
        await store.create_chat(Chat("c1", "ada", "t", "m", 1.0, 1.0))
        made = await store.add_message(
            Message("m1", "c1", 0, "assistant", "running", 1.0), parent_id=None
        )
        assert made.retrieval is None
        got = {"status": "nothing_matched", "message": "Nothing in your documents matched."}
        await store.update_message("m1", retrieval=got)
        assert (await store.message("m1")).retrieval == got  # type: ignore[union-attr]
        await store.update_message("m1", retrieval=None)
        assert (await store.message("m1")).retrieval is None  # type: ignore[union-attr]
        with sqlite3.connect(tmp_path / "w.sqlite3") as db:
            db.execute("UPDATE messages SET retrieval = 'not json' WHERE id = 'm1'")
        assert (await store.message("m1")).retrieval is None  # type: ignore[union-attr]

    run(tmp_path / "w.sqlite3", body)


def test_a_persons_documents_settings_are_changed_one_key_at_a_time_and_nobody_elses(
    tmp_path: Path,
) -> None:
    async def body(store: Store) -> None:
        assert await store.documents_prefs("ada") == {"new_chats": False, "external": None}
        assert await store.put_documents_prefs("ada", new_chats=True) == {
            "new_chats": True,
            "external": None,
        }
        assert await store.put_documents_prefs("ada", external="never") == {
            "new_chats": True,
            "external": "never",
        }
        assert await store.put_documents_prefs("ada", external=None) == {
            "new_chats": True,
            "external": None,
        }
        assert await store.put_documents_prefs("ada") == await store.documents_prefs("ada")
        assert await store.documents_prefs("bo") == {"new_chats": False, "external": None}
        await store.put_documents_prefs("bo", external="always")  # bo has a row of their own now
        await store.put_documents_prefs("ada", external="never")
        assert await store.documents_prefs("bo") == {"new_chats": False, "external": "always"}
        assert await store.documents_prefs("ada") == {"new_chats": True, "external": "never"}
        await store.put_documents_prefs("ada", external=None)
        with pytest.raises(ValueError, match="not documents settings"):
            await store.put_documents_prefs("ada", colour="red")
        with pytest.raises(sqlite3.IntegrityError):
            await store.put_documents_prefs("ada", external="sometimes")
        assert (await store.documents_prefs("ada"))["external"] is None, "refused, not kept"

    run(tmp_path / "w.sqlite3", body)


def test_the_boot_can_ask_whose_each_of_these_chats_is_and_which_are_gone(tmp_path: Path) -> None:
    async def body(store: Store) -> None:
        for chat_id, owner in (("c1", "ada"), ("c2", "bo")):
            await store.create_chat(Chat(chat_id, owner, "t", "m", 1.0, 1.0))
        assert await store.chat_owners(["c1", "c2", "gone"]) == {"c1": "ada", "c2": "bo"}
        assert await store.chat_owners({"c2"}) == {"c2": "bo"}
        assert await store.chat_owners([]) == {}
        many = [f"x{n}" for n in range(1200)] + ["c1"]
        assert await store.chat_owners(many) == {"c1": "ada"}

    run(tmp_path / "w.sqlite3", body)
