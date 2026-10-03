"""Where Workbench keeps people's chats, its sessions and its settings.

**One interface, SQLite behind it** (`workbench-v1.md` W8). Every read and
write goes through `Store`'s methods and nothing else names a table, so a
Postgres store for a business that outgrows SQLite implements this class
and nothing else changes. No migration tool yet: `SCHEMA_VERSION`, the
statements under it and `_MIGRATIONS` are the whole history.

**One thread owns the connection.** SQLite is fast, but a write that waits
on the disk must not stall every stream on the event loop, so each call
runs on a single dedicated thread. One thread also serializes access, so
there is no lock to get wrong.

**Bytes are not here.** An attachment's name, type, size and owner are;
the file itself is under `files/` in the data directory (W7).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 4

_SCHEMA = """
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
    reasoning_from INTEGER,
    tool_rounds TEXT NOT NULL DEFAULT '[]'
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
CREATE TABLE IF NOT EXISTS tool_servers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, token TEXT NOT NULL,
    transport TEXT NOT NULL DEFAULT 'http', command TEXT NOT NULL DEFAULT '',
    args TEXT NOT NULL DEFAULT '[]', environment TEXT NOT NULL DEFAULT '{}'
);
"""

#: What brings a store written at version N-1 to N. `_SCHEMA` already holds
#: the result, for a store made new.
_MIGRATIONS: dict[int, list[str]] = {
    # Where a reply's text after its last search begins (workbench#1).
    2: [
        "ALTER TABLE messages ADD COLUMN answer_from INTEGER",
        "ALTER TABLE messages ADD COLUMN reasoning_from INTEGER",
    ],
    3: [
        "ALTER TABLE messages ADD COLUMN tool_rounds TEXT NOT NULL DEFAULT '[]'",
        "CREATE TABLE IF NOT EXISTS tool_servers (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "url TEXT NOT NULL, token TEXT NOT NULL)",
    ],
    4: [
        "ALTER TABLE tool_servers ADD COLUMN transport TEXT NOT NULL DEFAULT 'http'",
        "ALTER TABLE tool_servers ADD COLUMN command TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE tool_servers ADD COLUMN args TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE tool_servers ADD COLUMN environment TEXT NOT NULL DEFAULT '{}'",
    ],
}


def _initialize_schema(db: sqlite3.Connection, path: Path) -> None:
    """Commit schema changes and their version together, including first boot.

    Do not use executescript here: it commits an existing transaction. The
    schema contains only individual DDL statements, with no triggers or scripts.
    Version 2 also accepts the partial additions left by the old autocommit
    migration runner; subsequent migrations must be transactional.
    """
    db.execute("BEGIN IMMEDIATE")
    try:
        has_meta = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
        ).fetchone()
        found = (
            db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
            if has_meta
            else None
        )
        version = int(found[0]) if found is not None else None
        if version is not None and (version < 1 or version > SCHEMA_VERSION):
            origin = " from a newer Workbench" if version > SCHEMA_VERSION else ""
            raise RuntimeError(
                f"{path} has unsupported schema {version}{origin}; this Workbench knows "
                f"schemas 1 through {SCHEMA_VERSION}. Restore matching software and state."
            )
        if version is not None:
            for target in range(version + 1, SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS[target]:
                    if target == 2:
                        columns = {
                            row[1]: row[2] for row in db.execute("PRAGMA table_info(messages)")
                        }
                        partial = next(
                            (
                                name
                                for name in ("answer_from", "reasoning_from")
                                if statement == f"ALTER TABLE messages ADD COLUMN {name} INTEGER"
                                and name in columns
                            ),
                            None,
                        )
                        if partial is not None:
                            if columns[partial].upper() != "INTEGER":
                                raise RuntimeError(
                                    f"{path}: incompatible partial migration: {partial}"
                                )
                            continue
                    db.execute(statement)
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema', ?)",
            (str(SCHEMA_VERSION),),
        )
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


@dataclass
class Person:
    sub: str
    name: str
    role: str
    username: str | None = None

    @property
    def is_owner(self) -> bool:
        return self.role == "operator"


@dataclass
class SessionRow:
    id_hash: str
    secret_hash: str
    sub: str
    refresh_token: str | None
    access_expires_at: float
    created_at: float
    expires_at: float


@dataclass
class Chat:
    id: str
    owner: str
    title: str
    model: str | None
    created_at: float
    updated_at: float
    settings: dict[str, Any] = field(default_factory=dict)
    search: bool = False


@dataclass
class Message:
    id: str
    chat_id: str
    seq: int
    role: str
    status: str
    created_at: float
    content: str = ""
    reasoning: str = ""
    attachments: list[str] = field(default_factory=list)
    error: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    searches: int = 0
    search: bool = False
    model: str | None = None
    finish: str | None = None
    finished_at: float | None = None
    #: Where the text written after the reply's last web search begins, in
    #: `content` and `reasoning` (Python indexes). None when nothing marks
    #: one: no search ran, or the reply predates schema 2. Text before it
    #: was written before that search (workbench#1).
    answer_from: int | None = None
    reasoning_from: int | None = None
    tool_rounds: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FileRecord:
    id: str
    owner: str
    chat_id: str
    name: str
    media_type: str
    size: int
    created_at: float


def _chat(row: sqlite3.Row) -> Chat:
    return Chat(
        id=row["id"],
        owner=row["owner"],
        title=row["title"],
        model=row["model"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        settings=json.loads(row["settings"] or "{}"),
        search=bool(row["search"]),
    )


def _message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        chat_id=row["chat_id"],
        seq=row["seq"],
        role=row["role"],
        status=row["status"],
        created_at=row["created_at"],
        content=row["content"],
        reasoning=row["reasoning"],
        attachments=json.loads(row["attachments"] or "[]"),
        error=row["error"],
        sources=json.loads(row["sources"] or "[]"),
        searches=row["searches"],
        search=bool(row["search"]),
        model=row["model"],
        finish=row["finish"],
        finished_at=row["finished_at"],
        answer_from=row["answer_from"],
        reasoning_from=row["reasoning_from"],
        tool_rounds=json.loads(row["tool_rounds"]),
    )


def _file(row: sqlite3.Row) -> FileRecord:
    return FileRecord(
        id=row["id"],
        owner=row["owner"],
        chat_id=row["chat_id"],
        name=row["name"],
        media_type=row["media_type"],
        size=row["size"],
        created_at=row["created_at"],
    )


_MESSAGE_FIELDS = {
    "content",
    "reasoning",
    "attachments",
    "status",
    "error",
    "sources",
    "searches",
    "search",
    "model",
    "finish",
    "finished_at",
    "answer_from",
    "reasoning_from",
    "tool_rounds",
}
_CHAT_FIELDS = {"title", "model", "settings", "search", "updated_at"}
_JSON_FIELDS = {"attachments", "sources", "settings", "tool_rounds"}


def interrupt_tools(rounds: list[dict[str, Any]]) -> None:
    for turn in rounds:
        for call in turn["calls"]:
            if call["status"] == "running":
                call.update(
                    status="uncertain",
                    result="The call was interrupted. It may have acted. "
                    "Check the server before trying again.",
                )
            elif call["status"] == "pending":
                call.update(status="cancelled", result="The call was not approved and did not run.")


def _encode(column: str, value: Any) -> Any:
    if column in _JSON_FIELDS:
        return json.dumps(value)
    if isinstance(value, bool):
        return int(value)
    return value


class Store:
    """Every read and write Workbench makes, as methods. See the module."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workbench-store")
        self._db: sqlite3.Connection | None = None

    # --- plumbing -------------------------------------------------------

    async def _run[T](self, fn: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn, *args)

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            try:
                _initialize_schema(db, self._path)
            except BaseException:
                db.close()
                raise
            self._db = db
        return self._db

    async def open(self) -> None:
        await self._run(self._conn)

    async def close(self) -> None:
        def shut() -> None:
            if self._db is not None:
                self._db.close()
                self._db = None

        await self._run(shut)
        self._executor.shutdown(wait=True)

    # --- people ---------------------------------------------------------

    async def upsert_person(self, person: Person) -> None:
        def go() -> None:
            now = time.time()
            self._conn().execute(
                "INSERT INTO people (sub, name, username, role, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(sub) DO UPDATE SET "
                "name = excluded.name, username = excluded.username, role = excluded.role, "
                "last_seen = excluded.last_seen",
                (person.sub, person.name, person.username, person.role, now, now),
            )

        await self._run(go)

    async def person(self, sub: str) -> Person | None:
        def go() -> Person | None:
            row = self._conn().execute("SELECT * FROM people WHERE sub = ?", (sub,)).fetchone()
            if row is None:
                return None
            return Person(
                sub=row["sub"], name=row["name"], role=row["role"], username=row["username"]
            )

        return await self._run(go)

    async def people(self) -> list[tuple[Person, int]]:
        """Everyone who has signed in, with how many chats each has."""

        def go() -> list[tuple[Person, int]]:
            rows = self._conn().execute(
                "SELECT p.*, (SELECT COUNT(*) FROM chats c WHERE c.owner = p.sub) AS chats "
                "FROM people p ORDER BY p.name COLLATE NOCASE"
            )
            return [
                (
                    Person(sub=r["sub"], name=r["name"], role=r["role"], username=r["username"]),
                    int(r["chats"]),
                )
                for r in rows
            ]

        return await self._run(go)

    # --- sessions -------------------------------------------------------

    async def put_session(self, row: SessionRow) -> None:
        def go() -> None:
            self._conn().execute(
                "INSERT INTO sessions (id_hash, secret_hash, sub, refresh_token, "
                "access_expires_at, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row.id_hash,
                    row.secret_hash,
                    row.sub,
                    row.refresh_token,
                    row.access_expires_at,
                    row.created_at,
                    row.expires_at,
                ),
            )

        await self._run(go)

    async def session(self, id_hash: str) -> SessionRow | None:
        def go() -> SessionRow | None:
            r = (
                self._conn()
                .execute("SELECT * FROM sessions WHERE id_hash = ?", (id_hash,))
                .fetchone()
            )
            if r is None:
                return None
            return SessionRow(
                id_hash=r["id_hash"],
                secret_hash=r["secret_hash"],
                sub=r["sub"],
                refresh_token=r["refresh_token"],
                access_expires_at=r["access_expires_at"],
                created_at=r["created_at"],
                expires_at=r["expires_at"],
            )

        return await self._run(go)

    async def refreshed(self, id_hash: str, refresh_token: str | None, expires_at: float) -> None:
        def go() -> None:
            self._conn().execute(
                "UPDATE sessions SET refresh_token = COALESCE(?, refresh_token), "
                "access_expires_at = ? WHERE id_hash = ?",
                (refresh_token, expires_at, id_hash),
            )

        await self._run(go)

    async def delete_session(self, id_hash: str) -> None:
        await self._run(
            lambda: self._conn().execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
        )

    async def sweep_sessions(self, now: float) -> None:
        await self._run(
            lambda: self._conn().execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        )

    # --- chats ----------------------------------------------------------

    async def create_chat(self, chat: Chat) -> None:
        def go() -> None:
            self._conn().execute(
                "INSERT INTO chats (id, owner, title, model, settings, search, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chat.id,
                    chat.owner,
                    chat.title,
                    chat.model,
                    json.dumps(chat.settings),
                    int(chat.search),
                    chat.created_at,
                    chat.updated_at,
                ),
            )

        await self._run(go)

    async def chat(self, chat_id: str) -> Chat | None:
        def go() -> Chat | None:
            r = self._conn().execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
            return _chat(r) if r is not None else None

        return await self._run(go)

    async def chats(self, owner: str) -> list[Chat]:
        def go() -> list[Chat]:
            rows = self._conn().execute(
                "SELECT * FROM chats WHERE owner = ? ORDER BY updated_at DESC", (owner,)
            )
            return [_chat(r) for r in rows]

        return await self._run(go)

    async def update_chat(self, chat_id: str, **values: Any) -> None:
        unknown = set(values) - _CHAT_FIELDS
        if unknown:
            raise ValueError(f"not chat fields: {sorted(unknown)}")
        if not values:
            return

        def go() -> None:
            columns = ", ".join(f"{k} = ?" for k in values)
            params = [_encode(k, v) for k, v in values.items()]
            self._conn().execute(f"UPDATE chats SET {columns} WHERE id = ?", (*params, chat_id))

        await self._run(go)

    async def delete_chat(self, chat_id: str) -> None:
        def go() -> None:
            db = self._conn()
            db.execute("BEGIN")
            try:
                db.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
                db.execute("DELETE FROM files WHERE chat_id = ?", (chat_id,))
                db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

        await self._run(go)

    # --- messages -------------------------------------------------------

    async def add_message(self, message: Message) -> Message:
        """Append `message` to its chat, numbering it after the last one."""

        def go() -> Message:
            db = self._conn()
            row = db.execute(
                "SELECT COALESCE(MAX(seq), 0) AS last FROM messages WHERE chat_id = ?",
                (message.chat_id,),
            ).fetchone()
            message.seq = int(row["last"]) + 1
            db.execute(
                "INSERT INTO messages (id, chat_id, seq, role, content, reasoning, attachments, "
                "status, error, sources, searches, search, model, finish, created_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message.id,
                    message.chat_id,
                    message.seq,
                    message.role,
                    message.content,
                    message.reasoning,
                    json.dumps(message.attachments),
                    message.status,
                    message.error,
                    json.dumps(message.sources),
                    message.searches,
                    int(message.search),
                    message.model,
                    message.finish,
                    message.created_at,
                    message.finished_at,
                ),
            )
            return message

        return await self._run(go)

    async def messages(self, chat_id: str) -> list[Message]:
        def go() -> list[Message]:
            rows = self._conn().execute(
                "SELECT * FROM messages WHERE chat_id = ? ORDER BY seq", (chat_id,)
            )
            return [_message(r) for r in rows]

        return await self._run(go)

    async def message(self, message_id: str) -> Message | None:
        def go() -> Message | None:
            r = (
                self._conn()
                .execute("SELECT * FROM messages WHERE id = ?", (message_id,))
                .fetchone()
            )
            return _message(r) if r is not None else None

        return await self._run(go)

    async def update_message(self, message_id: str, **values: Any) -> None:
        unknown = set(values) - _MESSAGE_FIELDS
        if unknown:
            raise ValueError(f"not message fields: {sorted(unknown)}")
        if not values:
            return

        def go() -> None:
            columns = ", ".join(f"{k} = ?" for k in values)
            params = [_encode(k, v) for k, v in values.items()]
            self._conn().execute(
                f"UPDATE messages SET {columns} WHERE id = ?", (*params, message_id)
            )

        await self._run(go)

    async def delete_messages_from(self, chat_id: str, seq: int) -> None:
        """Delete the message numbered `seq` and every one after it."""
        await self._run(
            lambda: self._conn().execute(
                "DELETE FROM messages WHERE chat_id = ? AND seq >= ?", (chat_id, seq)
            )
        )

    async def mark_interrupted(self) -> int:
        """Boot: an answer still `running` was cut off by a restart (W1)."""

        def go() -> int:
            # A sent call may have acted. Never replay it after a restart.
            for row in self._conn().execute("SELECT * FROM messages WHERE status = 'running'"):
                rounds = json.loads(row["tool_rounds"])
                interrupt_tools(rounds)
                self._conn().execute(
                    "UPDATE messages SET tool_rounds = ? WHERE id = ?",
                    (json.dumps(rounds), row["id"]),
                )
            cursor = self._conn().execute(
                "UPDATE messages SET status = 'interrupted', finished_at = ? "
                "WHERE status = 'running'",
                (time.time(),),
            )
            return cursor.rowcount

        return await self._run(go)

    # --- files ----------------------------------------------------------

    async def add_file(self, record: FileRecord) -> None:
        def go() -> None:
            self._conn().execute(
                "INSERT INTO files (id, owner, chat_id, name, media_type, size, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.owner,
                    record.chat_id,
                    record.name,
                    record.media_type,
                    record.size,
                    record.created_at,
                ),
            )

        await self._run(go)

    async def file(self, file_id: str) -> FileRecord | None:
        def go() -> FileRecord | None:
            r = self._conn().execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            return _file(r) if r is not None else None

        return await self._run(go)

    async def files_for_chat(self, chat_id: str) -> list[FileRecord]:
        def go() -> list[FileRecord]:
            rows = self._conn().execute("SELECT * FROM files WHERE chat_id = ?", (chat_id,))
            return [_file(r) for r in rows]

        return await self._run(go)

    # --- settings -------------------------------------------------------

    async def tool_servers(self) -> list[dict[str, Any]]:
        def go() -> list[dict[str, Any]]:
            rows = []
            for row in self._conn().execute("SELECT * FROM tool_servers"):
                server = dict(row)
                for key in ("args", "environment"):
                    server[key] = json.loads(server[key])
                rows.append(server)
            return rows

        return await self._run(go)

    async def add_tool_server(self, server: dict[str, Any]) -> None:
        await self._run(
            lambda: self._conn().execute(
                "INSERT INTO tool_servers (id, name, url, token, transport, command, args, "
                "environment) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    server["id"],
                    server["name"],
                    server.get("url", ""),
                    server.get("token", ""),
                    server.get("transport", "http"),
                    server.get("command", ""),
                    json.dumps(server.get("args", [])),
                    json.dumps(server.get("environment", {})),
                ),
            )
        )

    async def delete_tool_server(self, server_id: str) -> None:
        await self._run(
            lambda: self._conn().execute("DELETE FROM tool_servers WHERE id = ?", (server_id,))
        )

    async def setting(self, key: str) -> Any:
        def go() -> Any:
            r = self._conn().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return json.loads(r["value"]) if r is not None else None

        return await self._run(go)

    async def put_setting(self, key: str, value: Any) -> None:
        def go() -> None:
            if value is None:
                self._conn().execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                self._conn().execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )

        await self._run(go)
