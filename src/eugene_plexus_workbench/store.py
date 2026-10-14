"""Where Workbench keeps people's chats, its sessions and its settings.

**One interface, SQLite behind it** (`workbench-v1.md` W8). Every read and
write goes through `Store`'s methods and nothing else names a table, so a
Postgres store for a business that outgrows SQLite implements this class
and nothing else changes. No migration tool: `SCHEMA_VERSION`, the statements
under it and `_MIGRATIONS` are the whole history, from `OLDEST_SCHEMA` (what
Workbench v0.1 wrote) on. A store older than that, or newer than this knows, is
refused and left as it is.

**A store is migrated in one transaction with its version, after a copy of it is
kept** (`workbench.sqlite3.schema-<N>.bak`, N the schema it was at): steps 6, 7 and
11 rewrite chats, so what was there is kept whole. A copy is never written over: a
store that has changed since the copy of its schema was kept (the older Workbench ran
again between two starts) gets the next free name, `.schema-<N>.1.bak` and so on, and
one that has not changed makes no second copy. A migration that fails changes
nothing, and `Store.open` raises with the cause; Workbench's lifespan serves a
degraded page naming it instead of crashing (`app.py`).

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

**Memory is a person's notes** (`workbench-memory.md`, schema 10): short
notes the person wrote, each filtered by `owner` *in its own query* -- a
note that is not yours is a note that does not exist. A full-text index
over their words (FTS5, kept by triggers) finds what "forget ..." means.
Deleting a note is real: its row and its index entry go, the text of every
event in its log is nulled, and any chat's frozen copy of it is dropped.

**A document is read** (schema 11): a file row of a PDF, Word, Markdown, text,
CSV, HTML or code file carries `reading`, JSON saying how reading it went and
what the person chose to have read of it; the text itself is beside the original
on disk (`files.read_path_of`). A message's `sources` are a union, each with a
`kind`: `web` for a search's citation, `document` for a file sent with the answer.

**Saved documents are not here** (schema 12): a person's documents, their passages
and their search index are one SQLite file each (`knowledge/index.py`), so that
removing a document is one transaction and a damaged index never touches a chat.
What is here is what belongs with the chats: a message's `retrieval`, JSON saying
what searching the person's documents for that answer came to (the words searched
for, how many passages went, why none did), and `documents_prefs`, a person's own
settings for them.

**Folders** (schema 13): `document_folders` is the granted folders a person has pointed their
Documents at (MR7), each filtered by `owner` in its own query. What was read from them is not
here: those are documents in the person's own index, named by `folder:<grant id>/<path>`.

**Artifacts** (schema 15): a block of an answer the person changed, kept with every revision
(`artifacts.py`). `artifacts` names the message, the block (`digest`, the number of its first
revision's words) and its kind and title; `artifact_revisions` holds each revision's source,
numbered from 1 and never rewritten. They go with their chat.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sqlite3
import time
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 15

#: The oldest schema this migrates from: what Workbench v0.1.0 wrote. Schema 1 predates
#: it, and nobody holds one.
OLDEST_SCHEMA = 2


class StoreProblem(RuntimeError):
    """The store cannot be opened, for a reason Workbench can name itself.

    The exception's own text is the whole account, with every path in it, and goes to the log.
    `public` says the same with none: a file by its own name alone. A Workbench that cannot open
    its store cannot sign anyone in, so what it says on `/healthz`, to the API and on its page is
    said to whoever asks (`app.py`).
    """

    def __init__(self, message: str, public: str) -> None:
        super().__init__(message)
        self.public = public


def public_cause(exc: BaseException) -> str:
    """Why `exc` stopped the store opening, in words that name no directory.

    Only what is known to carry none is passed on. SQLite's own messages name a table or a
    kind of fault, never a file. An operating system error is its `strerror`: its text adds the
    path. Anything else is named by its type, and the log has the rest.
    """
    if isinstance(exc, StoreProblem):
        return exc.public
    if isinstance(exc, sqlite3.Error):
        return str(exc) or type(exc).__name__
    if isinstance(exc, OSError):
        return exc.strerror or type(exc).__name__
    return f"an unexpected {type(exc).__name__}"


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
    updated_at REAL NOT NULL,
    memory_snapshot TEXT
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
    search_suggestions TEXT NOT NULL DEFAULT '[]',
    output_cap TEXT,
    retrieval TEXT
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
    height INTEGER,
    reading TEXT,
    message_id TEXT,
    origin TEXT NOT NULL DEFAULT 'upload' CHECK (origin IN ('upload', 'tool'))
);
CREATE INDEX IF NOT EXISTS files_by_chat ON files (chat_id);
CREATE INDEX IF NOT EXISTS files_by_message ON files (message_id);
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
CREATE TABLE IF NOT EXISTS documents_prefs (
    owner TEXT PRIMARY KEY,
    new_chats INTEGER NOT NULL DEFAULT 0 CHECK (new_chats IN (0, 1)),
    external TEXT CHECK (external IS NULL OR external IN ('never', 'ask', 'always'))
);
CREATE TABLE IF NOT EXISTS document_folders (
    owner TEXT NOT NULL,
    grant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    ignore TEXT NOT NULL DEFAULT '[]',
    include_hidden INTEGER NOT NULL DEFAULT 0 CHECK (include_hidden IN (0, 1)),
    use_gitignore INTEGER NOT NULL DEFAULT 1 CHECK (use_gitignore IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'withdrawn')),
    problem TEXT,
    report TEXT,
    added_at REAL NOT NULL,
    synced_at REAL,
    checked_at REAL,
    PRIMARY KEY (owner, grant_id)
);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('markdown', 'svg', 'html', 'react')),
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (message_id, kind, digest)
);
CREATE INDEX IF NOT EXISTS artifacts_by_chat ON artifacts (chat_id);
CREATE TABLE IF NOT EXISTS artifact_revisions (
    artifact_id TEXT NOT NULL,
    number INTEGER NOT NULL,
    source TEXT NOT NULL,
    size INTEGER NOT NULL,
    author TEXT NOT NULL CHECK (author IN ('answer', 'person')),
    created_at REAL NOT NULL,
    PRIMARY KEY (artifact_id, number)
);
"""

#: Memory (schema 10), one statement each: a trigger holds semicolons, so these
#: are never split the way `_SCHEMA` is. Every one is `IF NOT EXISTS`, so the
#: same list makes a new store's memory tables and is migration 10's body.
#:
#: Times are epoch seconds, as everywhere in this store. `expires_at` and
#: `revisit_at` are dates, kept for the slices that use them. The index is an
#: *external content* FTS5 table over `memory_notes`; its triggers keep it in
#: step, and the delete trigger also nulls the text of the note's events, so
#: no path that deletes a note can leave its words in the log.
_MEMORY_SCHEMA: list[str] = [
    "CREATE TABLE IF NOT EXISTS memory_notes ("
    "id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
    "text TEXT NOT NULL CHECK (length(text) <= 240), "
    "kind TEXT NOT NULL CHECK (kind IN ('fact','preference','instruction','project','event')), "
    "status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','pending','dismissed')), "
    "pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)), "
    "source TEXT NOT NULL CHECK (source IN ('typed','said','button','suggested','imported')), "
    "source_chat_id TEXT, source_message_id TEXT, quote TEXT, "
    "expires_at TEXT, revisit_at TEXT, "
    "created_at REAL NOT NULL, updated_at REAL NOT NULL, last_used_at REAL, "
    "use_count INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS memory_notes_by_owner ON memory_notes (owner, status)",
    "CREATE TABLE IF NOT EXISTS memory_events ("
    "id INTEGER PRIMARY KEY, owner TEXT NOT NULL, note_id TEXT NOT NULL, ts REAL NOT NULL, "
    "event TEXT NOT NULL, actor TEXT NOT NULL, old_text TEXT, new_text TEXT)",
    "CREATE INDEX IF NOT EXISTS memory_events_by_note ON memory_events (note_id)",
    # `external` is NULL until the person chooses, and then follows the
    # install's own default (`memoryExternalModels`); a row made by changing
    # some other setting must not pin the person to today's default.
    "CREATE TABLE IF NOT EXISTS memory_prefs ("
    "owner TEXT PRIMARY KEY, "
    "enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)), "
    "learn TEXT NOT NULL DEFAULT 'off' CHECK (learn IN ('off','suggest','auto')), "
    "external TEXT CHECK (external IS NULL OR external IN ('never','ask','always')), "
    "use_mode TEXT NOT NULL DEFAULT 'relevant' CHECK (use_mode IN ('relevant','all')), "
    "budget_tokens INTEGER)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_notes_fts USING fts5("
    "text, content='memory_notes', tokenize='porter unicode61 remove_diacritics 2')",
    "CREATE TRIGGER IF NOT EXISTS memory_notes_ai AFTER INSERT ON memory_notes BEGIN "
    "INSERT INTO memory_notes_fts (rowid, text) VALUES (new.rowid, new.text); END",
    "CREATE TRIGGER IF NOT EXISTS memory_notes_ad AFTER DELETE ON memory_notes BEGIN "
    "INSERT INTO memory_notes_fts (memory_notes_fts, rowid, text) "
    "VALUES ('delete', old.rowid, old.text); "
    "UPDATE memory_events SET old_text = NULL, new_text = NULL WHERE note_id = old.id; END",
    "CREATE TRIGGER IF NOT EXISTS memory_notes_au AFTER UPDATE OF text ON memory_notes BEGIN "
    "INSERT INTO memory_notes_fts (memory_notes_fts, rowid, text) "
    "VALUES ('delete', old.rowid, old.text); "
    "INSERT INTO memory_notes_fts (rowid, text) VALUES (new.rowid, new.text); END",
]


def _sources_get_kinds(db: sqlite3.Connection) -> None:
    """Schema 11: a source is `{kind: "web", url, title}` or `{kind: "document", ...}`.
    Every source an answer already has came from a web search, so it gains its
    kind here; the reader of a message adds none (no shim for an older store)."""
    for row in db.execute("SELECT id, sources FROM messages WHERE sources != '[]'").fetchall():
        try:
            found = json.loads(row["sources"])
        except ValueError:
            continue  # unreadable before this, and left as it was
        if not isinstance(found, list) or not any(
            isinstance(s, dict) and "kind" not in s for s in found
        ):
            continue
        rebuilt = [
            {"kind": "web", **s} if isinstance(s, dict) and "kind" not in s else s for s in found
        ]
        db.execute("UPDATE messages SET sources = ? WHERE id = ?", (json.dumps(rebuilt), row["id"]))


#: What brings a store written at version N-1 to N, for N above `OLDEST_SCHEMA`.
#: `_SCHEMA` already holds the result, for a store made new. A step is a statement,
#: or a function of the connection for what SQL alone cannot say.
_MIGRATIONS: dict[int, list[str | Callable[[sqlite3.Connection], None]]] = {
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
    # The output cap the gateway sent for the answer and where it came from,
    # so a length stop names the setting that made it (2026-10-10).
    9: [
        "ALTER TABLE messages ADD COLUMN output_cap TEXT",
    ],
    # Memory (2026-10-11): a person's notes, their log, their settings, a
    # full-text index over the notes, and the copy of what a chat was first
    # sent (JSON: the notes' ids and words, the block's hash and tokens, what
    # did not fit). `chats` as schema 1 made it comes first for a file that
    # has none, as `files` did at 7.
    10: [
        "CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
        "title TEXT NOT NULL, model TEXT, settings TEXT NOT NULL DEFAULT '{}', "
        "search INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL)",
        "ALTER TABLE chats ADD COLUMN memory_snapshot TEXT",
        *_MEMORY_SCHEMA,
    ],
    # Documents in the prompt (2026-10-11): what was read from an attached file
    # (JSON: its state, size in tokens, the person's choice of how much to read),
    # and a kind on every source an answer lists.
    11: [
        "ALTER TABLE files ADD COLUMN reading TEXT",
        _sources_get_kinds,
    ],
    # Saved documents (2026-10-11): what searching a person's documents came to for
    # an answer (JSON), and a person's own settings for them. Their documents are in
    # files of their own (`knowledge/index.py`), not in this store.
    12: [
        "ALTER TABLE messages ADD COLUMN retrieval TEXT",
        "CREATE TABLE IF NOT EXISTS documents_prefs (owner TEXT PRIMARY KEY, "
        "new_chats INTEGER NOT NULL DEFAULT 0 CHECK (new_chats IN (0, 1)), "
        "external TEXT CHECK (external IS NULL OR external IN ('never', 'ask', 'always')))",
    ],
    # Folders (2026-10-11): the granted folders a person has pointed their Documents at, with
    # their own ignore rules, how the last check went and what it found (JSON). The files read
    # from them are documents in the person's own index, named by `folder:<grant id>/<path>`.
    13: [
        "CREATE TABLE IF NOT EXISTS document_folders (owner TEXT NOT NULL, grant_id TEXT NOT NULL, "
        "name TEXT NOT NULL, ignore TEXT NOT NULL DEFAULT '[]', "
        "include_hidden INTEGER NOT NULL DEFAULT 0 CHECK (include_hidden IN (0, 1)), "
        "use_gitignore INTEGER NOT NULL DEFAULT 1 CHECK (use_gitignore IN (0, 1)), "
        "status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'withdrawn')), "
        "problem TEXT, report TEXT, added_at REAL NOT NULL, synced_at REAL, checked_at REAL, "
        "PRIMARY KEY (owner, grant_id))",
    ],
    # Tool results (rich content RC3, 2026-10-13): a picture, a sound or a file a tool
    # returned is a file of its chat, made for one answer (`message_id`) by a tool
    # (`origin`), apart from what a person uploads.
    14: [
        "ALTER TABLE files ADD COLUMN message_id TEXT",
        "ALTER TABLE files ADD COLUMN origin TEXT NOT NULL DEFAULT 'upload' "
        "CHECK (origin IN ('upload', 'tool'))",
        "CREATE INDEX IF NOT EXISTS files_by_message ON files (message_id)",
    ],
    # Artifacts (rich content RC5, 2026-10-14): a block of an answer the person changed,
    # with every revision; new tables only, so nothing that was there changes.
    15: [
        "CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
        "chat_id TEXT NOT NULL, message_id TEXT NOT NULL, digest TEXT NOT NULL, "
        "kind TEXT NOT NULL CHECK (kind IN ('markdown', 'svg', 'html', 'react')), "
        "title TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE (message_id, kind, digest))",
        "CREATE INDEX IF NOT EXISTS artifacts_by_chat ON artifacts (chat_id)",
        "CREATE TABLE IF NOT EXISTS artifact_revisions (artifact_id TEXT NOT NULL, "
        "number INTEGER NOT NULL, source TEXT NOT NULL, size INTEGER NOT NULL, "
        "author TEXT NOT NULL CHECK (author IN ('answer', 'person')), created_at REAL NOT NULL, "
        "PRIMARY KEY (artifact_id, number))",
    ],
}


def _stored_version(db: sqlite3.Connection) -> int | None:
    """The schema a store says it is at, or None for a store with none (a new one)."""
    has_meta = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone()
    found = (
        db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone() if has_meta else None
    )
    return int(found[0]) if found is not None else None


def _copy_name(path: Path, version: int, number: int) -> Path:
    """Where copy `number` of the store at `version` is kept: `<name>.schema-<N>.bak` for the
    first, then `.schema-<N>.1.bak`, `.2.bak` and so on."""
    suffix = f".{number}" if number else ""
    return path.with_name(f"{path.name}.schema-{version}{suffix}.bak")


def _copies_of(path: Path, version: int) -> list[tuple[int, Path]]:
    """The copies kept of this store at `version`, oldest first, each with its number."""
    named = re.compile(rf"{re.escape(path.name)}\.schema-{version}(?:\.(\d+))?\.bak")
    found = []
    for entry in path.parent.iterdir():
        match = named.fullmatch(entry.name)
        if match:
            found.append((int(match.group(1) or 0), entry))
    return sorted(found)


def _same_bytes(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as one, second.open("rb") as other:
        while True:
            a, b = one.read(1 << 20), other.read(1 << 20)
            if a != b:
                return False
            if not a:
                return True


def _keep_backup(db: sqlite3.Connection, path: Path, version: int) -> Path:
    """A copy of the store as it is, kept beside it before a migration changes it.

    SQLite's own online backup, so a store with a write-ahead log is copied whole
    and the copy is one file that opens on its own. It is written under another name
    and renamed, so a copy that exists is a finished one.

    A copy is never written over: it may be the only one of what it held. A start that
    stopped or failed after the copy and began again finds the store as that copy has it,
    and makes no second one, so restarts do not fill the disk. A store that has changed
    since (the older Workbench ran again between the two starts) is not in that copy, so
    it gets the next free name, `.schema-<N>.1.bak` and so on. What is returned is the
    copy that holds this store's content.
    """
    copies = _copies_of(path, version)
    newest = copies[-1][1] if copies else None
    if newest is not None and not newest.is_file():
        raise StoreProblem(
            f"{newest} is not a file, so the store cannot be copied there",
            f"{newest.name} is not a file, so the store cannot be copied there",
        )
    partial = path.with_name(f"{path.name}.schema-{version}.bak.part")
    partial.unlink(missing_ok=True)
    try:
        copy = sqlite3.connect(partial)
        try:
            db.backup(copy)
            copy.execute("PRAGMA journal_mode=DELETE")
        finally:
            copy.close()
        if newest is not None and _same_bytes(partial, newest):
            partial.unlink()
            return newest
        kept = _copy_name(path, version, copies[-1][0] + 1 if copies else 0)
        os.replace(partial, kept)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return kept


def _initialize_schema(
    db: sqlite3.Connection, path: Path, on_backup: Callable[[Path], None] = lambda _: None
) -> None:
    """Commit schema changes and their version together, including first boot.

    `on_backup` is told of the copy that holds the store before a migration the moment it
    is settled, so a migration that then fails can still say where it is. Do not use executescript
    here: it commits an existing transaction. `_SCHEMA` contains only individual DDL
    statements, which is why it may be split on semicolons; the memory triggers hold
    semicolons, so `_MEMORY_SCHEMA` is a list of whole statements. Migrations are
    transactional: a failure rolls back every column and the version with it.
    """
    before = _stored_version(db)
    if before is not None and OLDEST_SCHEMA <= before < SCHEMA_VERSION:
        on_backup(_keep_backup(db, path, before))
    db.execute("BEGIN IMMEDIATE")
    try:
        version = _stored_version(db)
        if version is not None and (version < OLDEST_SCHEMA or version > SCHEMA_VERSION):
            origin = (
                " from a newer Workbench"
                if version > SCHEMA_VERSION
                else " from before Workbench v0.1"
            )
            known = f"this Workbench knows schemas {OLDEST_SCHEMA} through {SCHEMA_VERSION}"
            raise StoreProblem(
                f"{path} has unsupported schema {version}{origin}; {known}. "
                "Restore matching software and state.",
                f"unsupported schema {version}{origin}; {known}",
            )
        if version is not None:
            for target in range(version + 1, SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS[target]:
                    if callable(statement):
                        statement(db)
                        continue
                    db.execute(statement)
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        for statement in _MEMORY_SCHEMA:
            db.execute(statement)
        db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema', ?)",
            (str(SCHEMA_VERSION),),
        )
        db.execute("COMMIT")
    except BaseException:
        # SQLite may have rolled back by itself (a full disk does), and then there is nothing
        # left to roll back: what is raised is the cause, not that.
        with contextlib.suppress(sqlite3.Error):
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
    #: What the first request that carried notes was sent, kept so every later
    #: turn sends the same bytes (the engine's prompt cache) and the page can
    #: say exactly what was sent (`memory.Snapshot`'s JSON). None until then.
    memory_snapshot: dict[str, Any] | None = None


@dataclass
class MemoryNote:
    """One thing a person asked Workbench to remember (`memory.py`)."""

    id: str
    owner: str
    text: str
    kind: str
    created_at: float
    updated_at: float
    status: str = "active"
    pinned: bool = False
    #: Who wrote it: `typed` on the Memory page, `said` as a command in a
    #: chat, `button` from a message's Remember, `suggested` and `imported`
    #: for the slices that follow.
    source: str = "typed"
    source_chat_id: str | None = None
    source_message_id: str | None = None
    quote: str | None = None
    expires_at: str | None = None
    revisit_at: str | None = None
    last_used_at: float | None = None
    use_count: int = 0


@dataclass
class FolderRecord:
    """A granted folder a person pointed their Documents at (`knowledge/folder_sync.py`): which
    grant, what the owner called it, the person's own rules for it, and how it stands. `status` is
    `active`, or `withdrawn` once the owner took the grant away (its documents are gone and the
    record stays, with `problem` saying so, until the person dismisses it). `problem` is what is
    wrong now, as `{code, message, at}`; `report` what the last check found; `synced_at` when a
    check last finished well and `checked_at` when one last ended, either way."""

    owner: str
    grant_id: str
    name: str
    added_at: float
    ignore: list[str] = field(default_factory=list)
    include_hidden: bool = False
    use_gitignore: bool = True
    status: str = "active"
    problem: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    synced_at: float | None = None
    checked_at: float | None = None


class MemoryFull(Exception):
    """The person has as many notes as this install allows."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"{limit} notes is the limit")
        self.limit = limit


def note_key(text: str) -> str:
    """What makes two notes the same note: their words, ignoring case,
    spacing and the forms Unicode allows for one character."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


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
    #: The output cap the gateway sent for this answer and where it came
    #: from (`{"tokens", "source"}`, gateway.yaml `OutputCap`); None when it
    #: sent none or did not say.
    output_cap: dict[str, Any] | None = None
    #: What searching the person's saved documents came to for this answer
    #: (`saved_api.retrieval_view`); None when none was searched.
    retrieval: dict[str, Any] | None = None


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
    #: What was read from a document (`knowledge/reader.py`): its `state`
    #: (`reading`, `ready`, `skipped`, `failed`), how large its text is in
    #: tokens, the delimiter suffix its text is sent in, and how much of it the
    #: person chose to read. None for a file that is not a document.
    reading: dict[str, Any] | None = None
    #: The answer a tool made it for (rich content RC3); None for an upload.
    message_id: str | None = None
    #: `upload` (a person's attachment or a media file) or `tool` (a tool's result).
    origin: str = "upload"


class AttachmentGone(Exception):
    """A message names a file its chat no longer has (workbench#3)."""


class FileInUse(Exception):
    """A message refers to the file, so it goes with the chat (workbench#3)."""


@dataclass
class ArtifactRecord:
    """A block of an answer the person changed (`artifacts.py`), with how many revisions
    it has and when the last was saved."""

    id: str
    owner: str
    chat_id: str
    message_id: str
    digest: str
    kind: str
    title: str
    created_at: float
    revisions: int = 0
    updated_at: float = 0.0


@dataclass
class Revision:
    """One revision of an artifact, by the answer (revision 1, when the answer holds it)
    or the person; `source` only when it was asked for."""

    number: int
    author: str
    size: int
    created_at: float
    source: str | None = None


class ArtifactLimit(Exception):
    """An artifact, or a revision of one, past what one keeps; its words say which."""


class StaleRevision(Exception):
    """An edit begun from one revision, when a later one (`last`) has been saved since."""

    def __init__(self, base: int, last: int) -> None:
        super().__init__(f"revision {last} was saved after revision {base}")
        self.base = base
        self.last = last


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


def _snapshot(value: str | None) -> dict[str, Any] | None:
    """A chat's saved copy of its notes, or None when it has none or the
    column holds something that is not one (the chat then takes a new copy)."""
    if not value:
        return None
    try:
        found = json.loads(value)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


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
        memory_snapshot=_snapshot(row["memory_snapshot"]),
    )


def _note(row: sqlite3.Row) -> MemoryNote:
    return MemoryNote(
        id=row["id"],
        owner=row["owner"],
        text=row["text"],
        kind=row["kind"],
        status=row["status"],
        pinned=bool(row["pinned"]),
        source=row["source"],
        source_chat_id=row["source_chat_id"],
        source_message_id=row["source_message_id"],
        quote=row["quote"],
        expires_at=row["expires_at"],
        revisit_at=row["revisit_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_used_at=row["last_used_at"],
        use_count=row["use_count"],
    )


#: What a person's memory settings are until they change one. `external` None
#: is "whatever the install's default is" (it is `ask` unless the owner says).
MEMORY_PREFS_DEFAULT: dict[str, Any] = {
    "enabled": True,
    "learn": "off",
    "external": None,
    "use_mode": "relevant",
    "budget_tokens": None,
}


#: What a person's saved-documents settings are until they change one. `external`
#: None is "whatever the install's default is" (it is `ask` unless the owner says);
#: `new_chats` is whether a new chat starts with Documents on (off: a switch that
#: injects passages into every chat is not one a person did not choose).
DOCUMENTS_PREFS_DEFAULT: dict[str, Any] = {"new_chats": False, "external": None}


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
        output_cap=json.loads(row["output_cap"]) if row["output_cap"] else None,
        retrieval=_snapshot(row["retrieval"]),
    )


def _reading(value: str | None) -> dict[str, Any] | None:
    """A file's saved reading, or None when it has none or the column holds
    something that is not one."""
    if not value:
        return None
    try:
        found = json.loads(value)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


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
        reading=_reading(row["reading"]),
        message_id=row["message_id"],
        origin=row["origin"],
    )


#: An artifact with its revisions counted (`artifacts` joined to `artifact_revisions`).
_ARTIFACT_SELECT = (
    "SELECT a.id, a.owner, a.chat_id, a.message_id, a.digest, a.kind, a.title, a.created_at, "
    "COUNT(r.number) AS revisions, MAX(r.created_at) AS updated_at FROM artifacts a "
    "LEFT JOIN artifact_revisions r ON r.artifact_id = a.id"
)


def _artifact(row: sqlite3.Row) -> ArtifactRecord:
    return ArtifactRecord(
        id=row["id"],
        owner=row["owner"],
        chat_id=row["chat_id"],
        message_id=row["message_id"],
        digest=row["digest"],
        kind=row["kind"],
        title=row["title"],
        created_at=row["created_at"],
        revisions=int(row["revisions"] or 0),
        updated_at=float(row["updated_at"] or row["created_at"]),
    )


def _artifact_room(db: sqlite3.Connection, owner: str, size: int, limit: int) -> None:
    """`ArtifactLimit` when `size` more bytes would take a person's artifacts past `limit`."""
    kept = int(
        db.execute(
            "SELECT COALESCE(SUM(r.size), 0) FROM artifact_revisions r "
            "JOIN artifacts a ON a.id = r.artifact_id WHERE a.owner = ?",
            (owner,),
        ).fetchone()[0]
    )
    if kept + size > limit:
        mb = 1024 * 1024
        raise ArtifactLimit(
            f"Your artifacts keep {kept // mb} MB of the {limit // mb} MB they may; "
            "deleting a chat that holds some makes room"
        )


def _append_revision(
    db: sqlite3.Connection,
    artifact_id: str,
    revision: Revision,
    *,
    base: int | None,
    most: int,
    person_bytes: int,
) -> Revision:
    """`revision` kept as the artifact's next, numbered here, in the caller's transaction;
    the last one, as it is, when its source is the same. `StaleRevision` when `base` (the
    revision the edit began from) is not the last; `ArtifactLimit` past `most`
    revisions, or the person's `person_bytes`, or when the artifact is no longer kept."""
    owner = db.execute("SELECT owner FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
    if owner is None:
        raise ArtifactLimit("This artifact is no longer kept")
    last = db.execute(
        "SELECT number, source, size, author, created_at FROM artifact_revisions "
        "WHERE artifact_id = ? ORDER BY number DESC LIMIT 1",
        (artifact_id,),
    ).fetchone()
    if last is not None and last["source"] == revision.source:
        return Revision(
            number=last["number"],
            author=last["author"],
            size=last["size"],
            created_at=last["created_at"],
        )
    if base is not None and last is not None and last["number"] != base:
        raise StaleRevision(base, last["number"])
    number = (last["number"] if last is not None else 0) + 1
    if number > most:
        raise ArtifactLimit(
            f"This artifact keeps {most} revisions, the most one may; download this one to keep it"
        )
    _artifact_room(db, owner[0], revision.size, person_bytes)
    db.execute(
        "INSERT INTO artifact_revisions (artifact_id, number, source, size, author, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (artifact_id, number, revision.source, revision.size, revision.author, revision.created_at),
    )
    return Revision(
        number=number, author=revision.author, size=revision.size, created_at=revision.created_at
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
    "output_cap",
    "retrieval",
}
_CHAT_FIELDS = {"title", "model", "settings", "search", "updated_at"}
_JSON_FIELDS = {
    "attachments",
    "sources",
    "settings",
    "tool_rounds",
    "search_suggestions",
    "output_cap",
    "retrieval",
}


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


_FOLDER_FIELDS = frozenset(
    {
        "name",
        "ignore",
        "include_hidden",
        "use_gitignore",
        "status",
        "problem",
        "report",
        "synced_at",
        "checked_at",
    }
)


def _folder_record(row: sqlite3.Row) -> FolderRecord:
    def loaded(text: str | None, empty: Any) -> Any:
        if text is None:
            return empty
        try:
            return json.loads(text)
        except ValueError:
            return empty

    ignore = loaded(row["ignore"], [])
    problem = loaded(row["problem"], None)
    report = loaded(row["report"], None)
    return FolderRecord(
        owner=row["owner"],
        grant_id=row["grant_id"],
        name=row["name"],
        added_at=row["added_at"],
        ignore=[str(p) for p in ignore] if isinstance(ignore, list) else [],
        include_hidden=bool(row["include_hidden"]),
        use_gitignore=bool(row["use_gitignore"]),
        status=row["status"],
        problem=problem if isinstance(problem, dict) else None,
        report=report if isinstance(report, dict) else None,
        synced_at=row["synced_at"],
        checked_at=row["checked_at"],
    )


def _encode_folder(column: str, value: Any) -> Any:
    if column in ("ignore", "problem", "report"):
        return json.dumps(value) if value is not None else None
    if isinstance(value, bool):
        return int(value)
    return value


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
        #: The copy that holds this store as it was before it was migrated, if it was: a
        #: new one, or the one an earlier start kept of the same content. What a failure
        #: after the copy points the person to.
        self.backup: Path | None = None

    @property
    def path(self) -> Path:
        return self._path

    # --- plumbing -------------------------------------------------------

    def _kept(self, backup: Path) -> None:
        self.backup = backup

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
                _initialize_schema(db, self._path, self._kept)
            except sqlite3.OperationalError as exc:
                db.close()
                if "fts5" in str(exc).lower():
                    # Memory's search needs it; name the cause rather than a
                    # bare "no such module".
                    raise StoreProblem(
                        f"{self._path}: this Python's SQLite was built without full-text search "
                        f"(FTS5), which Workbench's memory needs ({exc}). Reinstall Workbench "
                        "from Eugene's console, which brings a Python that has it.",
                        "this Python's SQLite was built without full-text search (FTS5), which "
                        "Workbench's memory needs",
                    ) from exc
                raise
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
                db.execute(
                    "DELETE FROM artifact_revisions WHERE artifact_id IN "
                    "(SELECT id FROM artifacts WHERE chat_id = ?)",
                    (chat_id,),
                )
                db.execute("DELETE FROM artifacts WHERE chat_id = ?", (chat_id,))
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
                "media_id, width, height, reading, message_id, origin) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    json.dumps(record.reading) if record.reading is not None else None,
                    record.message_id,
                    record.origin,
                ),
            )

        await self._run(go)

    async def tool_bytes(self, owner: str) -> int:
        """How much a person's tool results hold, in bytes (rich content RC3)."""

        def go() -> int:
            row = (
                self._conn()
                .execute(
                    "SELECT COALESCE(SUM(size), 0) FROM files WHERE owner = ? AND origin = 'tool'",
                    (owner,),
                )
                .fetchone()
            )
            return int(row[0])

        return await self._run(go)

    async def tool_orphans(self) -> list[FileRecord]:
        """Tool results no answer refers to: kept, then Workbench stopped before
        the answer that made them was saved with them. Their bytes are the
        caller's to remove."""

        def go() -> list[FileRecord]:
            db = self._conn()
            out = []
            for row in db.execute("SELECT * FROM files WHERE origin = 'tool'").fetchall():
                message = db.execute(
                    "SELECT tool_rounds FROM messages WHERE id = ?", (row["message_id"],)
                ).fetchone()
                if message is None or '"' + row["id"] + '"' not in (message["tool_rounds"] or ""):
                    out.append(_file(row))
            return out

        return await self._run(go)

    async def delete_files(self, file_ids: list[str]) -> None:
        def go() -> None:
            self._conn().executemany("DELETE FROM files WHERE id = ?", [(i,) for i in file_ids])

        await self._run(go)

    async def merge_file_reading(
        self, file_id: str, changes: dict[str, Any], *, chat_id: str | None = None
    ) -> FileRecord | None:
        """Set these keys of a file's reading, and keep the others. The file
        after the change; None when there is no such file (it was removed while
        it was being read), or, with `chat_id`, when it is not that chat's.
        Read, change and write are one step, so a choice made while the reader
        finishes is not lost to it."""

        def go() -> FileRecord | None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                sql, params = "SELECT * FROM files WHERE id = ?", [file_id]
                if chat_id is not None:
                    sql, params = sql + " AND chat_id = ?", [file_id, chat_id]
                row = db.execute(sql, params).fetchone()
                if row is None:
                    db.execute("COMMIT")
                    return None
                reading = _reading(row["reading"]) or {}
                reading.update(changes)
                db.execute(
                    "UPDATE files SET reading = ? WHERE id = ?", (json.dumps(reading), file_id)
                )
                fresh = db.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return _file(fresh)

        return await self._run(go)

    async def files_reading(self) -> list[FileRecord]:
        """The files whose reading had not ended (Workbench was stopped while it
        ran): the reader starts them again at boot."""

        def go() -> list[FileRecord]:
            rows = self._conn().execute("SELECT * FROM files WHERE reading IS NOT NULL")
            return [
                r
                for r in (_file(row) for row in rows)
                if (r.reading or {}).get("state") == "reading"
            ]

        return await self._run(go)

    async def file(self, file_id: str) -> FileRecord | None:
        def go() -> FileRecord | None:
            r = self._conn().execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            return _file(r) if r is not None else None

        return await self._run(go)

    async def files_for_chat(self, chat_id: str, *, uploads: bool = False) -> list[FileRecord]:
        """A chat's files; with `uploads`, only what people attached, not what tools
        returned (rich content RC3)."""

        def go() -> list[FileRecord]:
            sql = "SELECT * FROM files WHERE chat_id = ?"
            if uploads:
                sql += " AND origin = 'upload'"
            rows = self._conn().execute(sql, (chat_id,))
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
                    "SELECT * FROM files WHERE id = ? AND chat_id = ? AND origin = 'upload'",
                    (file_id, chat_id),
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

    # --- artifacts (rich content RC5) -------------------------------------

    async def artifacts_for_chat(self, chat_id: str) -> list[ArtifactRecord]:
        """A chat's artifacts, the oldest first, each with its revisions counted."""

        def go() -> list[ArtifactRecord]:
            rows = self._conn().execute(
                f"{_ARTIFACT_SELECT} WHERE a.chat_id = ? GROUP BY a.id ORDER BY a.created_at",
                (chat_id,),
            )
            return [_artifact(r) for r in rows]

        return await self._run(go)

    async def artifact(self, artifact_id: str) -> ArtifactRecord | None:
        def go() -> ArtifactRecord | None:
            row = (
                self._conn()
                .execute(f"{_ARTIFACT_SELECT} WHERE a.id = ? GROUP BY a.id", (artifact_id,))
                .fetchone()
            )
            return _artifact(row) if row is not None else None

        return await self._run(go)

    async def add_artifact(
        self,
        record: ArtifactRecord,
        first: Revision,
        *,
        then: Revision | None = None,
        base: int | None = None,
        per_message: int,
        most: int,
        person_bytes: int,
    ) -> tuple[ArtifactRecord, bool]:
        """The message's artifact for this block, kept now with `first` as its revision 1
        when there is none yet, and whether it was kept now; with `then`, the person's
        edit begun from revision `base`, added in the same transaction (`_append_revision`).
        `ArtifactLimit` when the message has `per_message` already, or past a limit;
        nothing is kept then."""

        def go() -> tuple[str, bool]:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                found = db.execute(
                    "SELECT id FROM artifacts WHERE message_id = ? AND kind = ? AND digest = ?",
                    (record.message_id, record.kind, record.digest),
                ).fetchone()
                artifact_id, made = (
                    (str(found[0]), False) if found is not None else (record.id, True)
                )
                if made:
                    count = db.execute(
                        "SELECT COUNT(*) FROM artifacts WHERE message_id = ?",
                        (record.message_id,),
                    ).fetchone()[0]
                    if int(count) >= per_message:
                        raise ArtifactLimit(
                            f"This answer keeps {per_message} artifacts already, the most one may"
                        )
                    _artifact_room(db, record.owner, first.size, person_bytes)
                    db.execute(
                        "INSERT INTO artifacts (id, owner, chat_id, message_id, digest, kind, "
                        "title, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            record.id,
                            record.owner,
                            record.chat_id,
                            record.message_id,
                            record.digest,
                            record.kind,
                            record.title,
                            record.created_at,
                        ),
                    )
                    db.execute(
                        "INSERT INTO artifact_revisions (artifact_id, number, source, size, "
                        "author, created_at) VALUES (?, 1, ?, ?, ?, ?)",
                        (record.id, first.source, first.size, first.author, first.created_at),
                    )
                if then is not None:
                    _append_revision(
                        db, artifact_id, then, base=base, most=most, person_bytes=person_bytes
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return artifact_id, made

        artifact_id, made = await self._run(go)
        kept = await self.artifact(artifact_id)
        assert kept is not None
        return kept, made

    async def add_revision(
        self,
        artifact_id: str,
        revision: Revision,
        *,
        base: int | None = None,
        most: int,
        person_bytes: int,
    ) -> Revision:
        """`revision` kept as the artifact's next (`_append_revision`)."""

        def go() -> Revision:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                kept = _append_revision(
                    db, artifact_id, revision, base=base, most=most, person_bytes=person_bytes
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return kept

        return await self._run(go)

    async def revisions(self, artifact_id: str) -> list[Revision]:
        """An artifact's revisions, the oldest first, without their source."""

        def go() -> list[Revision]:
            rows = self._conn().execute(
                "SELECT number, author, size, created_at FROM artifact_revisions "
                "WHERE artifact_id = ? ORDER BY number",
                (artifact_id,),
            )
            return [
                Revision(
                    number=r["number"],
                    author=r["author"],
                    size=r["size"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

        return await self._run(go)

    async def revision(self, artifact_id: str, number: int) -> Revision | None:
        def go() -> Revision | None:
            r = (
                self._conn()
                .execute(
                    "SELECT number, author, size, created_at, source FROM artifact_revisions "
                    "WHERE artifact_id = ? AND number = ?",
                    (artifact_id, number),
                )
                .fetchone()
            )
            if r is None:
                return None
            return Revision(
                number=r["number"],
                author=r["author"],
                size=r["size"],
                created_at=r["created_at"],
                source=r["source"],
            )

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

    # --- memory ---------------------------------------------------------
    #
    # Every method takes the owner and puts it in its own query. None of them
    # can be asked for a note by id alone.

    @staticmethod
    def _event(
        db: sqlite3.Connection,
        owner: str,
        note_id: str,
        event: str,
        actor: str,
        old: str | None,
        new: str | None,
    ) -> None:
        db.execute(
            "INSERT INTO memory_events (owner, note_id, ts, event, actor, old_text, new_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (owner, note_id, time.time(), event, actor, old, new),
        )

    @staticmethod
    def _drop_from_chats(db: sqlite3.Connection, owner: str, note_ids: set[str] | None) -> None:
        """Take the person's chats' saved copies of these notes (all notes when
        None) away, so the next request in each takes a new copy without them.
        A deleted note is not sent again, and its words are kept nowhere."""
        if note_ids is None:
            db.execute(
                "UPDATE chats SET memory_snapshot = NULL "
                "WHERE owner = ? AND memory_snapshot IS NOT NULL",
                (owner,),
            )
            return
        for row in db.execute(
            "SELECT id, memory_snapshot FROM chats WHERE owner = ? AND memory_snapshot IS NOT NULL",
            (owner,),
        ).fetchall():
            saved = _snapshot(row["memory_snapshot"])
            # A copy that cannot be read is dropped too: it is not worth keeping.
            if saved is None or note_ids & set(saved.get("ids") or []):
                db.execute("UPDATE chats SET memory_snapshot = NULL WHERE id = ?", (row["id"],))

    async def memory_notes(self, owner: str, *, status: str | None = "active") -> list[MemoryNote]:
        """The person's notes, newest first; every status when `status` is None."""

        def go() -> list[MemoryNote]:
            sql, params = "SELECT * FROM memory_notes WHERE owner = ?", [owner]
            if status is not None:
                sql += " AND status = ?"
                params.append(status)
            rows = self._conn().execute(sql + " ORDER BY created_at DESC, rowid DESC", params)
            return [_note(r) for r in rows]

        return await self._run(go)

    async def memory_note(self, owner: str, note_id: str) -> MemoryNote | None:
        def go() -> MemoryNote | None:
            row = (
                self._conn()
                .execute("SELECT * FROM memory_notes WHERE id = ? AND owner = ?", (note_id, owner))
                .fetchone()
            )
            return _note(row) if row is not None else None

        return await self._run(go)

    async def memory_count(self, owner: str) -> int:
        def go() -> int:
            row = (
                self._conn()
                .execute("SELECT COUNT(*) AS n FROM memory_notes WHERE owner = ?", (owner,))
                .fetchone()
            )
            return int(row["n"])

        return await self._run(go)

    async def add_memory_note(
        self, note: MemoryNote, *, limit: int, actor: str = "person"
    ) -> tuple[MemoryNote, bool]:
        """Keep a note: `(the note, True)`. The same words already kept are not
        kept twice: `(the one already there, False)`. Raises `MemoryFull` at
        `limit` notes. The count, the check and the insert are one step."""

        def go() -> tuple[MemoryNote, bool]:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                wanted = note_key(note.text)
                kept = db.execute(
                    "SELECT * FROM memory_notes WHERE owner = ? AND status = 'active'",
                    (note.owner,),
                ).fetchall()
                twin = next((r for r in kept if note_key(r["text"]) == wanted), None)
                if twin is not None:
                    db.execute("COMMIT")
                    return _note(twin), False
                total = db.execute(
                    "SELECT COUNT(*) AS n FROM memory_notes WHERE owner = ?", (note.owner,)
                ).fetchone()["n"]
                if total >= limit:
                    raise MemoryFull(limit)
                # "Newest first" must be a fact, not a coin toss: a clock that
                # ticks in 15 ms steps gives two notes made together one moment,
                # so a note is always later than the person's latest.
                latest = db.execute(
                    "SELECT MAX(created_at) AS at FROM memory_notes WHERE owner = ?", (note.owner,)
                ).fetchone()["at"]
                if latest is not None and note.created_at <= latest:
                    note.created_at = note.updated_at = latest + 1e-6
                db.execute(
                    "INSERT INTO memory_notes (id, owner, text, kind, status, pinned, source, "
                    "source_chat_id, source_message_id, quote, expires_at, revisit_at, "
                    "created_at, updated_at, last_used_at, use_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        note.id,
                        note.owner,
                        note.text,
                        note.kind,
                        note.status,
                        int(note.pinned),
                        note.source,
                        note.source_chat_id,
                        note.source_message_id,
                        note.quote,
                        note.expires_at,
                        note.revisit_at,
                        note.created_at,
                        note.updated_at,
                        note.last_used_at,
                        note.use_count,
                    ),
                )
                self._event(db, note.owner, note.id, "add", actor, None, note.text)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return note, True

        return await self._run(go)

    async def update_memory_note(
        self,
        owner: str,
        note_id: str,
        *,
        text: str | None = None,
        kind: str | None = None,
        pinned: bool | None = None,
        actor: str = "person",
    ) -> MemoryNote | None:
        """Change a note's words, kind or pin. None when the person has no such
        note. The log keeps the words before and after, until the note goes."""

        def go() -> MemoryNote | None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM memory_notes WHERE id = ? AND owner = ?", (note_id, owner)
                ).fetchone()
                if row is None:
                    db.execute("COMMIT")
                    return None
                changes: dict[str, Any] = {}
                if text is not None and text != row["text"]:
                    changes["text"] = text
                if kind is not None and kind != row["kind"]:
                    changes["kind"] = kind
                if pinned is not None and int(pinned) != row["pinned"]:
                    changes["pinned"] = int(pinned)
                if changes:
                    columns = ", ".join(f"{k} = ?" for k in changes)
                    db.execute(
                        f"UPDATE memory_notes SET {columns}, updated_at = ? "
                        "WHERE id = ? AND owner = ?",
                        (*changes.values(), time.time(), note_id, owner),
                    )
                    self._event(
                        db,
                        owner,
                        note_id,
                        "edit",
                        actor,
                        row["text"] if "text" in changes else None,
                        changes.get("text"),
                    )
                fresh = db.execute("SELECT * FROM memory_notes WHERE id = ?", (note_id,)).fetchone()
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return _note(fresh)

        return await self._run(go)

    async def delete_memory_note(self, owner: str, note_id: str) -> bool:
        """Forget a note, for real: its row and its index entry go, its log
        keeps no words, and no chat keeps a copy. False when it is not theirs."""

        def go() -> bool:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                cursor = db.execute(
                    "DELETE FROM memory_notes WHERE id = ? AND owner = ?", (note_id, owner)
                )
                gone = cursor.rowcount == 1
                if gone:
                    self._event(db, owner, note_id, "delete", "person", None, None)
                    self._drop_from_chats(db, owner, {note_id})
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return gone

        return await self._run(go)

    async def delete_memory_notes(self, owner: str) -> int:
        """Forget every note the person has, as `delete_memory_note` does each."""

        def go() -> int:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO memory_events (owner, note_id, ts, event, actor) "
                    "SELECT owner, id, ?, 'delete', 'person' FROM memory_notes WHERE owner = ?",
                    (time.time(), owner),
                )
                count = db.execute("DELETE FROM memory_notes WHERE owner = ?", (owner,)).rowcount
                self._drop_from_chats(db, owner, None)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return count

        return await self._run(go)

    async def search_memory(self, owner: str, match: str, *, limit: int = 5) -> list[MemoryNote]:
        """The person's active notes that match an FTS5 expression, best first.
        The owner is part of the query that finds them, so what ranks is never
        someone else's note."""

        def go() -> list[MemoryNote]:
            rows = self._conn().execute(
                "SELECT n.* FROM memory_notes_fts JOIN memory_notes AS n "
                "ON n.rowid = memory_notes_fts.rowid "
                "WHERE memory_notes_fts MATCH ? AND n.owner = ? AND n.status = 'active' "
                "ORDER BY bm25(memory_notes_fts), n.created_at DESC LIMIT ?",
                (match, owner, limit),
            )
            return [_note(r) for r in rows]

        return await self._run(go)

    async def mark_memory_used(self, owner: str, note_ids: Sequence[str], now: float) -> None:
        """These notes went into a chat's first request."""
        if not note_ids:
            return

        def go() -> None:
            marks = ", ".join("?" for _ in note_ids)
            self._conn().execute(
                "UPDATE memory_notes SET last_used_at = ?, use_count = use_count + 1 "
                f"WHERE owner = ? AND id IN ({marks})",
                (now, owner, *note_ids),
            )

        await self._run(go)

    async def memory_prefs(self, owner: str) -> dict[str, Any]:
        """The person's memory settings, with every one filled in."""

        def go() -> dict[str, Any]:
            row = (
                self._conn()
                .execute("SELECT * FROM memory_prefs WHERE owner = ?", (owner,))
                .fetchone()
            )
            if row is None:
                return dict(MEMORY_PREFS_DEFAULT)
            return {
                "enabled": bool(row["enabled"]),
                "learn": row["learn"],
                "external": row["external"],
                "use_mode": row["use_mode"],
                "budget_tokens": row["budget_tokens"],
            }

        return await self._run(go)

    async def put_memory_prefs(self, owner: str, **values: Any) -> dict[str, Any]:
        """Change the settings named; the others stay as they were."""
        unknown = set(values) - set(MEMORY_PREFS_DEFAULT)
        if unknown:
            raise ValueError(f"not memory settings: {sorted(unknown)}")

        def go() -> None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("INSERT OR IGNORE INTO memory_prefs (owner) VALUES (?)", (owner,))
                if values:
                    columns = ", ".join(f"{k} = ?" for k in values)
                    params = [_encode(k, v) for k, v in values.items()]
                    db.execute(
                        f"UPDATE memory_prefs SET {columns} WHERE owner = ?", (*params, owner)
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

        await self._run(go)
        return await self.memory_prefs(owner)

    async def set_memory_snapshot(
        self, chat_id: str, owner: str, snapshot: dict[str, Any] | None
    ) -> bool:
        """Keep what this chat was first sent (None drops it). False when the
        person has no such chat."""

        def go() -> bool:
            cursor = self._conn().execute(
                "UPDATE chats SET memory_snapshot = ? WHERE id = ? AND owner = ?",
                (json.dumps(snapshot) if snapshot is not None else None, chat_id, owner),
            )
            return cursor.rowcount == 1

        return await self._run(go)

    # --- saved documents' settings --------------------------------------

    async def documents_prefs(self, owner: str) -> dict[str, Any]:
        """A person's settings for their saved documents, with every one filled in."""

        def go() -> dict[str, Any]:
            row = (
                self._conn()
                .execute("SELECT * FROM documents_prefs WHERE owner = ?", (owner,))
                .fetchone()
            )
            if row is None:
                return dict(DOCUMENTS_PREFS_DEFAULT)
            return {"new_chats": bool(row["new_chats"]), "external": row["external"]}

        return await self._run(go)

    async def put_documents_prefs(self, owner: str, **values: Any) -> dict[str, Any]:
        """Change the settings named; the others stay as they were."""
        unknown = set(values) - set(DOCUMENTS_PREFS_DEFAULT)
        if unknown:
            raise ValueError(f"not documents settings: {sorted(unknown)}")

        def go() -> None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("INSERT OR IGNORE INTO documents_prefs (owner) VALUES (?)", (owner,))
                if values:
                    columns = ", ".join(f"{k} = ?" for k in values)
                    params = [_encode(k, v) for k, v in values.items()]
                    db.execute(
                        f"UPDATE documents_prefs SET {columns} WHERE owner = ?", (*params, owner)
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

        await self._run(go)
        return await self.documents_prefs(owner)

    # --- the folders a person's documents are read from -------------------

    async def folder_records(self, owner: str | None = None) -> list[FolderRecord]:
        """Every folder, or one person's, oldest first."""

        def go() -> list[FolderRecord]:
            where, params = ("", ()) if owner is None else (" WHERE owner = ?", (owner,))
            rows = self._conn().execute(
                f"SELECT * FROM document_folders{where} ORDER BY added_at, rowid", params
            )
            return [_folder_record(r) for r in rows]

        return await self._run(go)

    async def folder_record(self, owner: str, grant_id: str) -> FolderRecord | None:
        def go() -> FolderRecord | None:
            row = (
                self._conn()
                .execute(
                    "SELECT * FROM document_folders WHERE owner = ? AND grant_id = ?",
                    (owner, grant_id),
                )
                .fetchone()
            )
            return _folder_record(row) if row is not None else None

        return await self._run(go)

    async def add_folder_record(self, record: FolderRecord) -> bool:
        """Keep a folder. False when the person already has it."""

        def go() -> bool:
            cursor = self._conn().execute(
                "INSERT OR IGNORE INTO document_folders (owner, grant_id, name, ignore, "
                "include_hidden, use_gitignore, status, problem, report, added_at, synced_at, "
                "checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.owner,
                    record.grant_id,
                    record.name,
                    json.dumps(record.ignore),
                    int(record.include_hidden),
                    int(record.use_gitignore),
                    record.status,
                    json.dumps(record.problem) if record.problem is not None else None,
                    json.dumps(record.report) if record.report is not None else None,
                    record.added_at,
                    record.synced_at,
                    record.checked_at,
                ),
            )
            return cursor.rowcount == 1

        return await self._run(go)

    async def update_folder_record(
        self, owner: str, grant_id: str, **changes: Any
    ) -> FolderRecord | None:
        """Change the fields named, and only those. None when the person has no such folder."""
        unknown = set(changes) - _FOLDER_FIELDS
        if unknown:
            raise ValueError(f"not folder fields: {sorted(unknown)}")

        def go() -> FolderRecord | None:
            db = self._conn()
            if changes:
                columns = ", ".join(f"{k} = ?" for k in changes)
                params = [_encode_folder(k, v) for k, v in changes.items()]
                db.execute(
                    f"UPDATE document_folders SET {columns} WHERE owner = ? AND grant_id = ?",
                    (*params, owner, grant_id),
                )
            row = db.execute(
                "SELECT * FROM document_folders WHERE owner = ? AND grant_id = ?",
                (owner, grant_id),
            ).fetchone()
            return _folder_record(row) if row is not None else None

        return await self._run(go)

    async def delete_folder_record(self, owner: str, grant_id: str) -> bool:
        def go() -> bool:
            cursor = self._conn().execute(
                "DELETE FROM document_folders WHERE owner = ? AND grant_id = ?", (owner, grant_id)
            )
            return cursor.rowcount == 1

        return await self._run(go)

    async def chat_owners(self, chat_ids: Iterable[str]) -> dict[str, str]:
        """Whose each of these chats is, for the ones that exist: what the boot
        reconcile asks to find a chat's own documents whose chat is gone."""
        ids = list(chat_ids)
        if not ids:
            return {}

        def go() -> dict[str, str]:
            found: dict[str, str] = {}
            for start in range(0, len(ids), 500):
                part = ids[start : start + 500]
                marks = ", ".join("?" for _ in part)
                for row in self._conn().execute(
                    f"SELECT id, owner FROM chats WHERE id IN ({marks})", part
                ):
                    found[row["id"]] = row["owner"]
            return found

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
