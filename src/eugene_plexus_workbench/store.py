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

**A chat is a tree** (`workbench-answer-versions.md`). Each message names
the one it follows (`parent_id`); messages that follow the same one are
versions of each other, and one in each group is `chosen`. The **path**,
the chat as it is shown and sent, starts at the chosen first message and
follows each message's chosen version. Nothing in a chat is deleted but the
whole chat.

**Media is a person's bins** (`workbench-media-screens.md`, schema 7): one
`media` row per request a media screen sent, or per file brought in, and
its files under `files/` like an attachment's, with `media_id` in place of
`chat_id`. A media row and its files can be deleted on their own (M8).
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

SCHEMA_VERSION = 8

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
    tool_rounds TEXT NOT NULL DEFAULT '[]',
    parent_id TEXT,
    chosen INTEGER NOT NULL DEFAULT 1,
    search_suggestions TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS messages_by_chat ON messages (chat_id, seq);
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    chat_id TEXT,
    name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL,
    media_id TEXT,
    width INTEGER,
    height INTEGER
);
CREATE INDEX IF NOT EXISTS files_by_chat ON files (chat_id);
CREATE INDEX IF NOT EXISTS files_by_media ON files (media_id);
CREATE TABLE IF NOT EXISTS media (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    door TEXT NOT NULL,
    kind TEXT NOT NULL,
    model TEXT,
    request TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    finished_at REAL,
    served TEXT,
    units TEXT,
    text TEXT,
    error TEXT,
    job TEXT
);
CREATE INDEX IF NOT EXISTS media_by_owner ON media (owner, door, created_at);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tool_servers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, token TEXT NOT NULL,
    transport TEXT NOT NULL DEFAULT 'http', command TEXT NOT NULL DEFAULT '',
    args TEXT NOT NULL DEFAULT '[]', environment TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS folder_grants (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
    identity TEXT NOT NULL, subject TEXT NOT NULL, writable INTEGER NOT NULL
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
    5: [
        "CREATE TABLE folder_grants (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL, "
        "identity TEXT NOT NULL, subject TEXT NOT NULL, writable INTEGER NOT NULL)",
    ],
    # Versions of a message (workbench-answer-versions.md §4): each existing
    # row follows the one before it, and every row is chosen, so a chat
    # opens as the same conversation with no versions.
    6: [
        "ALTER TABLE messages ADD COLUMN parent_id TEXT",
        "ALTER TABLE messages ADD COLUMN chosen INTEGER NOT NULL DEFAULT 1",
        "UPDATE messages SET parent_id = (SELECT p.id FROM messages p "
        "WHERE p.chat_id = messages.chat_id AND p.seq < messages.seq "
        "ORDER BY p.seq DESC LIMIT 1)",
    ],
    # Media (workbench-media-screens.md §7): a file belongs to a chat or to a
    # media row, so `chat_id` loses NOT NULL, which SQLite changes only by
    # making the table again. The `media` table itself is `_SCHEMA`'s.
    7: [
        # A store made before attachments had none; schema 1's files table.
        "CREATE TABLE IF NOT EXISTS files (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
        "chat_id TEXT NOT NULL, name TEXT NOT NULL, media_type TEXT NOT NULL, "
        "size INTEGER NOT NULL, created_at REAL NOT NULL)",
        "CREATE TABLE files_7 (id TEXT PRIMARY KEY, owner TEXT NOT NULL, chat_id TEXT, "
        "name TEXT NOT NULL, media_type TEXT NOT NULL, size INTEGER NOT NULL, "
        "created_at REAL NOT NULL, media_id TEXT, width INTEGER, height INTEGER)",
        "INSERT INTO files_7 (id, owner, chat_id, name, media_type, size, created_at) "
        "SELECT id, owner, chat_id, name, media_type, size, created_at FROM files",
        "DROP TABLE files",
        "ALTER TABLE files_7 RENAME TO files",
    ],
    # Google's Search Suggestions, kept with the answer they came with so the
    # person sees them in their own history (google-search-account.md GS4).
    8: [
        "ALTER TABLE messages ADD COLUMN search_suggestions TEXT NOT NULL DEFAULT '[]'",
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
    # Request-only context. Never persisted with the person or sent to a browser.
    session_id: str | None = None

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
    #: The message this one follows; None for a chat's first message.
    parent_id: str | None = None
    #: Whether this is the version shown, among those that follow its parent.
    chosen: bool = True
    #: A search provider's required suggestions, as HTML, one per search that
    #: had any, in the order the searches ran. Shown unmodified to the person
    #: who asked; never logged, measured, or sent to a model (GS4).
    search_suggestions: list[str] = field(default_factory=list)


@dataclass
class FileRecord:
    id: str
    owner: str
    #: The chat it is attached to, or None for a media file.
    chat_id: str | None
    name: str
    media_type: str
    size: int
    created_at: float
    #: The media row it belongs to, or None for an attachment.
    media_id: str | None = None
    #: An image's own pixel size, read from its bytes (§2.4).
    width: int | None = None
    height: int | None = None


class AttachmentGone(Exception):
    """A message names a file its chat no longer has (workbench#3)."""


class FileInUse(Exception):
    """A message refers to the file, so it goes with the chat (workbench#3)."""


@dataclass
class MediaRow:
    """One request a media screen sent, or one file brought in (`kind`)."""

    id: str
    owner: str
    #: The screen: images, speech, transcription or video.
    door: str
    #: `made` (a request to the gateway) or `upload` (a file brought in).
    kind: str
    status: str
    created_at: float
    model: str | None = None
    #: What was asked: prompt or text, and the settings sent.
    request: dict[str, Any] = field(default_factory=dict)
    finished_at: float | None = None
    #: What served it: driver, backend, latency, attempts, request id.
    served: dict[str, Any] | None = None
    #: Units as measured: images and sizes, characters, seconds.
    units: dict[str, Any] | None = None
    text: str | None = None
    #: The gateway's words: message, param, status.
    error: dict[str, Any] | None = None
    #: A long job's handle and last poll (video, slice 3).
    job: dict[str, Any] | None = None


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
        parent_id=row["parent_id"],
        chosen=bool(row["chosen"]),
        search_suggestions=json.loads(row["search_suggestions"] or "[]"),
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
        media_id=row["media_id"],
        width=row["width"],
        height=row["height"],
    )


def _json_or_none(value: str | None) -> Any:
    return json.loads(value) if value is not None else None


def _media(row: sqlite3.Row) -> MediaRow:
    return MediaRow(
        id=row["id"],
        owner=row["owner"],
        door=row["door"],
        kind=row["kind"],
        status=row["status"],
        created_at=row["created_at"],
        model=row["model"],
        request=json.loads(row["request"] or "{}"),
        finished_at=row["finished_at"],
        served=_json_or_none(row["served"]),
        units=_json_or_none(row["units"]),
        text=row["text"],
        error=_json_or_none(row["error"]),
        job=_json_or_none(row["job"]),
    )


_MEDIA_FIELDS = {"status", "finished_at", "served", "units", "text", "error", "job"}
_MEDIA_JSON = {"served", "units", "error", "job"}


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
    "search_suggestions",
}
_CHAT_FIELDS = {"title", "model", "settings", "search", "updated_at"}
_JSON_FIELDS = {"attachments", "sources", "settings", "tool_rounds", "search_suggestions"}


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
            elif call["status"] == "signing":
                # Sent again while it waited (J14b): signed at the machine, it
                # may have run on the last try.
                call.pop("held", None)
                call.update(
                    status="uncertain",
                    result="The call was interrupted while it waited for your signature. If you "
                    "signed it on the machine, it may have run. Check before trying again.",
                )


def _encode(column: str, value: Any) -> Any:
    if column in _JSON_FIELDS:
        return json.dumps(value)
    if isinstance(value, bool):
        return int(value)
    return value


def _groups(tree: list[Message]) -> dict[str | None, list[Message]]:
    """Each group of versions, under the message they follow, oldest first."""
    groups: dict[str | None, list[Message]] = {}
    for message in tree:
        groups.setdefault(message.parent_id, []).append(message)
    return groups


def path(tree: list[Message], via: str | None = None) -> list[Message]:
    """The chat as shown, from all of its rows in `seq` order.

    From the chosen first message, each message's chosen version follows.
    With `via`, the path through that message instead: what it follows on
    its own branch, it, then its own chosen versions after it. An unknown
    `via` is an empty path.
    """
    groups = _groups(tree)
    out: list[Message] = []
    seen: set[str] = set()
    at: str | None = None
    if via is not None:
        by_id = {m.id: m for m in tree}
        node = by_id.get(via)
        if node is None:
            return []
        while node is not None and node.id not in seen:
            out.append(node)
            seen.add(node.id)
            node = by_id.get(node.parent_id) if node.parent_id is not None else None
        out.reverse()
        at = via
    while group := groups.get(at):
        # Exactly one is chosen; were none, the newest is shown, not nothing.
        step = next((m for m in group if m.chosen), group[-1])
        if step.id in seen:
            break
        out.append(step)
        seen.add(step.id)
        at = step.id
    return out


def versions(tree: list[Message]) -> dict[str, dict[str, Any]]:
    """Each message's place among its versions: `{index, count, ids}`, from 1."""
    out: dict[str, dict[str, Any]] = {}
    for group in _groups(tree).values():
        ids = [m.id for m in group]
        for index, message in enumerate(group, start=1):
            out[message.id] = {"index": index, "count": len(ids), "ids": ids}
    return out


def _choose(db: sqlite3.Connection, chat_id: str, message_id: str) -> bool:
    """Put `message_id` on the path: it, and each message before it on its
    own branch, becomes the chosen version in its group. A group already
    right is left alone, so the usual case is one statement."""
    rows = db.execute(
        "SELECT id, parent_id, chosen FROM messages WHERE chat_id = ?", (chat_id,)
    ).fetchall()
    parent = {r["id"]: r["parent_id"] for r in rows}
    if message_id not in parent:
        return False
    chosen = {r["id"] for r in rows if r["chosen"]}
    count: dict[str | None, int] = {}
    for r in rows:
        if r["chosen"]:
            count[r["parent_id"]] = count.get(r["parent_id"], 0) + 1
    node: str | None = message_id
    seen: set[str] = set()
    while node is not None and node in parent and node not in seen:
        seen.add(node)
        above = parent[node]
        if node not in chosen or count.get(above) != 1:
            db.execute(
                "UPDATE messages SET chosen = (id = ?) WHERE chat_id = ? AND parent_id IS ?",
                (node, chat_id, above),
            )
        node = above
    return True


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

    async def people(self) -> list[tuple[Person, int, int]]:
        """Everyone who has signed in, with how many chats and how many media
        results each has."""

        def go() -> list[tuple[Person, int, int]]:
            rows = self._conn().execute(
                "SELECT p.*, (SELECT COUNT(*) FROM chats c WHERE c.owner = p.sub) AS chats, "
                "(SELECT COUNT(*) FROM media m WHERE m.owner = p.sub) AS media "
                "FROM people p ORDER BY p.name COLLATE NOCASE"
            )
            return [
                (
                    Person(sub=r["sub"], name=r["name"], role=r["role"], username=r["username"]),
                    int(r["chats"]),
                    int(r["media"]),
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

    async def add_message(self, message: Message, *, parent_id: str | None) -> Message:
        """Add `message` after `parent_id`, numbered after the chat's last row.

        It becomes the chosen version among those that follow its parent, so
        the path now runs through it. A new message's parent is the path's
        last message; a version's parent is its sibling's.

        Its attachments must be files of its chat: a file removed after the
        send checked it raises `AttachmentGone`, and nothing is added.
        """

        def go() -> Message:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                if message.attachments:
                    marks = ", ".join("?" for _ in message.attachments)
                    found = {
                        r["id"]
                        for r in db.execute(
                            f"SELECT id FROM files WHERE chat_id = ? AND id IN ({marks})",
                            (message.chat_id, *message.attachments),
                        )
                    }
                    if not found.issuperset(message.attachments):
                        raise AttachmentGone
                row = db.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS last FROM messages WHERE chat_id = ?",
                    (message.chat_id,),
                ).fetchone()
                message.seq = int(row["last"]) + 1
                message.parent_id, message.chosen = parent_id, True
                db.execute(
                    "INSERT INTO messages (id, chat_id, seq, role, content, reasoning, "
                    "attachments, status, error, sources, searches, search, model, finish, "
                    "created_at, finished_at, parent_id, chosen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
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
                        parent_id,
                    ),
                )
                _choose(db, message.chat_id, message.id)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return message

        return await self._run(go)

    async def tree(self, chat_id: str) -> list[Message]:
        """Every row of a chat, every version, in the order they were made."""

        def go() -> list[Message]:
            rows = self._conn().execute(
                "SELECT * FROM messages WHERE chat_id = ? ORDER BY seq", (chat_id,)
            )
            return [_message(r) for r in rows]

        return await self._run(go)

    async def messages(self, chat_id: str, via: str | None = None) -> list[Message]:
        """The chat's path (see `path`): what is shown and what the model is sent."""
        return path(await self.tree(chat_id), via)

    async def choose(self, chat_id: str, message_id: str) -> bool:
        """Show this version, and the branch it remembers after it. False when
        the chat has no such message."""

        def go() -> bool:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                found = _choose(db, chat_id, message_id)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return found

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
                "INSERT INTO files (id, owner, chat_id, name, media_type, size, created_at, "
                "media_id, width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.owner,
                    record.chat_id,
                    record.name,
                    record.media_type,
                    record.size,
                    record.created_at,
                    record.media_id,
                    record.width,
                    record.height,
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

    async def delete_unsent_file(self, chat_id: str, file_id: str) -> FileRecord | None:
        """Delete a file of this chat that no message refers to: an upload
        taken back before it was sent (workbench#3). None when the chat has
        no such file; `FileInUse` when a message refers to it. The bytes are
        the caller's to remove."""

        def go() -> FileRecord | None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM files WHERE id = ? AND chat_id = ?", (file_id, chat_id)
                ).fetchone()
                if row is not None:
                    for m in db.execute(
                        "SELECT attachments FROM messages WHERE chat_id = ?", (chat_id,)
                    ):
                        if file_id in json.loads(m["attachments"] or "[]"):
                            raise FileInUse
                    db.execute("DELETE FROM files WHERE id = ?", (file_id,))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return _file(row) if row is not None else None

        return await self._run(go)

    async def files_for_media(self, media_ids: list[str]) -> list[FileRecord]:
        """The files of these media rows, in the order they were made."""
        if not media_ids:
            return []

        def go() -> list[FileRecord]:
            marks = ", ".join("?" for _ in media_ids)
            rows = self._conn().execute(
                f"SELECT * FROM files WHERE media_id IN ({marks}) ORDER BY created_at, rowid",
                media_ids,
            )
            return [_file(r) for r in rows]

        return await self._run(go)

    # --- media ----------------------------------------------------------

    async def add_media(self, row: MediaRow) -> None:
        def go() -> None:
            self._conn().execute(
                "INSERT INTO media (id, owner, door, kind, model, request, status, created_at, "
                "finished_at, served, units, text, error, job) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row.id,
                    row.owner,
                    row.door,
                    row.kind,
                    row.model,
                    json.dumps(row.request),
                    row.status,
                    row.created_at,
                    row.finished_at,
                    *(json.dumps(v) if v is not None else None for v in (row.served, row.units)),
                    row.text,
                    *(json.dumps(v) if v is not None else None for v in (row.error, row.job)),
                ),
            )

        await self._run(go)

    async def media(self, media_id: str) -> MediaRow | None:
        def go() -> MediaRow | None:
            r = self._conn().execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
            return _media(r) if r is not None else None

        return await self._run(go)

    async def media_list(
        self, owner: str, door: str, *, before: float | None = None, limit: int = 30
    ) -> list[MediaRow]:
        """A person's results on one screen, newest first, a page at a time."""

        def go() -> list[MediaRow]:
            rows = self._conn().execute(
                "SELECT * FROM media WHERE owner = ? AND door = ? AND created_at < ? "
                "ORDER BY created_at DESC LIMIT ?",
                (owner, door, before if before is not None else float("inf"), limit),
            )
            return [_media(r) for r in rows]

        return await self._run(go)

    async def update_media(self, media_id: str, **values: Any) -> None:
        unknown = set(values) - _MEDIA_FIELDS
        if unknown:
            raise ValueError(f"not media fields: {sorted(unknown)}")
        if not values:
            return

        def go() -> None:
            columns = ", ".join(f"{k} = ?" for k in values)
            params = [
                (json.dumps(v) if v is not None else None) if k in _MEDIA_JSON else v
                for k, v in values.items()
            ]
            self._conn().execute(f"UPDATE media SET {columns} WHERE id = ?", (*params, media_id))

        await self._run(go)

    async def delete_media(self, media_ids: list[str]) -> list[FileRecord]:
        """Delete these rows and their file records together; returns the
        records, so the caller can remove the bytes."""
        if not media_ids:
            return []

        def go() -> list[FileRecord]:
            db = self._conn()
            marks = ", ".join("?" for _ in media_ids)
            db.execute("BEGIN")
            try:
                gone = [
                    _file(r)
                    for r in db.execute(
                        f"SELECT * FROM files WHERE media_id IN ({marks})", media_ids
                    ).fetchall()
                ]
                db.execute(f"DELETE FROM files WHERE media_id IN ({marks})", media_ids)
                db.execute(f"DELETE FROM media WHERE id IN ({marks})", media_ids)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return gone

        return await self._run(go)

    async def media_ids(self, owner: str, door: str) -> list[str]:
        def go() -> list[str]:
            rows = self._conn().execute(
                "SELECT id FROM media WHERE owner = ? AND door = ?", (owner, door)
            )
            return [r["id"] for r in rows]

        return await self._run(go)

    async def media_bytes(self, owner: str) -> int:
        """What a person's bins hold on disk, every screen together (M4)."""

        def go() -> int:
            row = (
                self._conn()
                .execute(
                    "SELECT COALESCE(SUM(size), 0) AS total FROM files "
                    "WHERE owner = ? AND media_id IS NOT NULL",
                    (owner,),
                )
                .fetchone()
            )
            return int(row["total"])

        return await self._run(go)

    async def media_jobs_running(self) -> list[MediaRow]:
        """Boot: long jobs the gateway accepted that had not ended (M11).
        Their handles are kept, so the provider's work is polled again."""

        def go() -> list[MediaRow]:
            rows = self._conn().execute(
                "SELECT * FROM media WHERE status = 'running' AND job IS NOT NULL "
                "ORDER BY created_at"
            )
            return [_media(r) for r in rows]

        return await self._run(go)

    async def mark_media_interrupted(self) -> int:
        """Boot: a media request still `running` was cut off by a restart.
        The gateway may have been asked, so the provider may have billed it."""

        def go() -> int:
            cursor = self._conn().execute(
                "UPDATE media SET status = 'interrupted', finished_at = ? "
                "WHERE status = 'running' AND job IS NULL",
                (time.time(),),
            )
            return cursor.rowcount

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

    async def folder_grants(self) -> list[dict[str, Any]]:
        return await self._run(
            lambda: [dict(row) for row in self._conn().execute("SELECT * FROM folder_grants")]
        )

    async def add_folder_grant(self, grant: dict[str, Any]) -> None:
        await self._run(
            lambda: self._conn().execute(
                "INSERT INTO folder_grants (id, name, path, identity, subject, writable) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                tuple(grant[k] for k in ("id", "name", "path", "identity", "subject", "writable")),
            )
        )

    async def delete_folder_grant(self, grant_id: str) -> None:
        await self._run(
            lambda: self._conn().execute("DELETE FROM folder_grants WHERE id = ?", (grant_id,))
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
