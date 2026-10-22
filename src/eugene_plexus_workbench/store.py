"""Where Workbench keeps people's chats, its sessions and its settings.

**One interface, SQLite behind it** (`workbench-v1.md` W8). Every read and
write goes through `Store` and its transactional `interactions` storage helper;
nothing outside storage code names a table. A Postgres store implements this interface
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

**A removed person** (schema 28, row 7): `people.known_at` is when Eugene last said it has the
person; `removed_people` is each person it no longer has, hidden (`held`, `hidden`, `kept`) until
their data is deleted (`purging`, then `purged`, which keeps only the id, name and dates). Every
read of a person, a chat, a file or a media row leaves out a hidden person's (`_HIDDEN`). The
purge deletes every row `PERSON_TABLES` names, from the store and from every copy kept beside it
(`*.schema-N.bak`), and rewrites each file so no free page or log still holds their words.
`exports` is the log of every download of a person's data, and where each file is while it is
kept.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agui_store import SCHEMA as AGUI_SCHEMA
from .agui_store import AGUIStore, allow_chat_write
from .interaction_store import SCHEMA as INTERACTION_SCHEMA
from .interaction_store import InteractionStore

SCHEMA_VERSION = 28

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
    access_token TEXT,
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
    retrieval TEXT,
    reasoning_tokens INTEGER,
    reasoning_seconds REAL
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
#:
#: Memory v2 (schema 22, `workbench-memory-v2.md` MV1): a note's `text` is its title and
#: `body` its Markdown; `procedure` is a kind; a changed fact is a new version and the old
#: one `superseded` (`valid_from`, `superseded_at`, `superseded_by`); a person's tags are
#: `memory_tags`, and a note's are `memory_note_tags`, whose rows the note's delete
#: trigger removes. `proposed_tags` holds the tags a suggestion came with (JSON).
_NOTES_TABLE = (
    "CREATE TABLE IF NOT EXISTS memory_notes ("
    "id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
    "text TEXT NOT NULL CHECK (length(text) <= 240), "
    "kind TEXT NOT NULL CHECK (kind IN "
    "('fact','preference','instruction','project','event','procedure')), "
    "status TEXT NOT NULL DEFAULT 'active' CHECK (status IN "
    "('active','pending','dismissed','superseded')), "
    "pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)), "
    "source TEXT NOT NULL CHECK (source IN ('typed','said','button','suggested','imported')), "
    "source_chat_id TEXT, source_message_id TEXT, quote TEXT, "
    "expires_at TEXT, revisit_at TEXT, "
    "created_at REAL NOT NULL, updated_at REAL NOT NULL, last_used_at REAL, "
    "use_count INTEGER NOT NULL DEFAULT 0, "
    "replaces TEXT, source_connection_id TEXT, source_connection TEXT, "
    "body TEXT NOT NULL DEFAULT '' CHECK (length(body) <= 20000), "
    "valid_from REAL, superseded_at REAL, superseded_by TEXT, proposed_tags TEXT)"
)
#: Every column of `_NOTES_TABLE`, in order: what a rebuild copies.
_NOTE_COLUMNS = (
    "id, owner, text, kind, status, pinned, source, source_chat_id, source_message_id, quote, "
    "expires_at, revisit_at, created_at, updated_at, last_used_at, use_count, replaces, "
    "source_connection_id, source_connection, body, valid_from, superseded_at, superseded_by, "
    "proposed_tags"
)
_NOTES_FTS = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_notes_fts USING fts5("
    "text, content='memory_notes', tokenize='porter unicode61 remove_diacritics 2')",
    "CREATE TRIGGER IF NOT EXISTS memory_notes_ai AFTER INSERT ON memory_notes BEGIN "
    "INSERT INTO memory_notes_fts (rowid, text) VALUES (new.rowid, new.text); END",
    "CREATE TRIGGER IF NOT EXISTS memory_notes_ad AFTER DELETE ON memory_notes BEGIN "
    "INSERT INTO memory_notes_fts (memory_notes_fts, rowid, text) "
    "VALUES ('delete', old.rowid, old.text); "
    "UPDATE memory_events SET old_text = NULL, new_text = NULL WHERE note_id = old.id; "
    "DELETE FROM memory_note_tags WHERE note_id = old.id; END",
    "CREATE TRIGGER IF NOT EXISTS memory_notes_au AFTER UPDATE OF text ON memory_notes BEGIN "
    "INSERT INTO memory_notes_fts (memory_notes_fts, rowid, text) "
    "VALUES ('delete', old.rowid, old.text); "
    "INSERT INTO memory_notes_fts (rowid, text) VALUES (new.rowid, new.text); END",
]
_MEMORY_SCHEMA: list[str] = [
    _NOTES_TABLE,
    "CREATE INDEX IF NOT EXISTS memory_notes_by_owner ON memory_notes (owner, status)",
    "CREATE TABLE IF NOT EXISTS memory_tags ("
    "owner TEXT NOT NULL, key TEXT NOT NULL, tag TEXT NOT NULL CHECK (length(tag) <= 40), "
    "description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 200), "
    "created_at REAL NOT NULL, PRIMARY KEY (owner, key))",
    "CREATE TABLE IF NOT EXISTS memory_note_tags ("
    "owner TEXT NOT NULL, note_id TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY (note_id, key))",
    "CREATE INDEX IF NOT EXISTS memory_note_tags_by_tag ON memory_note_tags (owner, key)",
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
    *_NOTES_FTS,
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


#: Memory suggestions (MR8, schema 20), as `(table, column, declaration)`: the note a
#: proposal would replace; whether a person chose Learn from my chats themselves (so a
#: default can follow the model until they do), turned the offer away, and when they
#: were last asked whether a project is still true; how far a chat's messages have
#: been read for suggestions, and how many it has made.
_MEMORY_LEARNING_COLUMNS: list[tuple[str, str, str]] = [
    ("memory_notes", "replaces", "TEXT"),
    ("memory_prefs", "learn_set", "INTEGER NOT NULL DEFAULT 0 CHECK (learn_set IN (0, 1))"),
    (
        "memory_prefs",
        "offer_dismissed",
        "INTEGER NOT NULL DEFAULT 0 CHECK (offer_dismissed IN (0, 1))",
    ),
    ("memory_prefs", "revisit_asked_at", "REAL"),
    ("chats", "memory_read_at", "REAL"),
    ("chats", "memory_suggested", "INTEGER NOT NULL DEFAULT 0"),
]


def _memory_learning(db: sqlite3.Connection) -> None:
    """Schema 20's columns, and the words of proposals a person turned away.

    A function, not statements, because memory's tables are made by the same list
    for a new store and for migration 10, so the columns are added wherever they
    are missing. A turned-away proposal keeps only its content words' stems
    (`memory_suggest`'s rule), enough never to propose it again and not the
    sentence the person said no to."""
    for table, column, declaration in _MEMORY_LEARNING_COLUMNS:
        have = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    db.execute(
        "CREATE TABLE IF NOT EXISTS memory_dismissed (owner TEXT NOT NULL, words TEXT NOT NULL, "
        "at REAL NOT NULL)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS memory_dismissed_by_owner ON memory_dismissed (owner, at)"
    )


#: Schema 21's columns on a note: the connection that suggested it (MR9b), and that
#: connection's name when it did, so a kept note still says where it came from after the
#: connection is revoked.
_CONNECTION_NOTE_COLUMNS: list[tuple[str, str]] = [
    ("source_connection_id", "TEXT"),
    ("source_connection", "TEXT"),
]


def _connections(db: sqlite3.Connection) -> None:
    """Schema 21 (MR9b): the connections a person made for outside apps, and where a
    note a connection suggested came from.

    A connection keeps the SHA-256 of its token, never the token. Its rights are three
    switches; what it did is counted per day, never what it asked."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS connections ("
        "id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
        "name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80), "
        "token_hash TEXT NOT NULL UNIQUE, "
        "read_notes INTEGER NOT NULL CHECK (read_notes IN (0, 1)), "
        "search_documents INTEGER NOT NULL CHECK (search_documents IN (0, 1)), "
        "suggest_notes INTEGER NOT NULL CHECK (suggest_notes IN (0, 1)), "
        "created_at REAL NOT NULL, expires_at REAL NOT NULL, last_used_at REAL, "
        "calls_day TEXT, calls_today INTEGER NOT NULL DEFAULT 0)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS connections_by_owner ON connections (owner)")
    have = {row[1] for row in db.execute("PRAGMA table_info(memory_notes)")}
    for column, declaration in _CONNECTION_NOTE_COLUMNS:
        if column not in have:
            db.execute(f"ALTER TABLE memory_notes ADD COLUMN {column} {declaration}")


#: Schema 22's columns outside `memory_notes` (MV1): the person's ceiling for relevant
#: memory a turn, whether models may search their notes, and what each answer's turn was
#: sent of them.
_MEMORY_V2_COLUMNS: list[tuple[str, str, str]] = [
    ("memory_prefs", "relevant_budget_tokens", "INTEGER"),
    (
        "memory_prefs",
        "search_tool",
        "INTEGER NOT NULL DEFAULT 1 CHECK (search_tool IN (0, 1))",
    ),
    ("messages", "memory_used", "TEXT"),
]


def _memory_v2(db: sqlite3.Connection) -> None:
    """Schema 22 (MV1): `memory_notes` in its v2 shape, and the columns beside it.

    SQLite cannot change a `CHECK`, so a table without `body` is rebuilt: renamed,
    made again, its rows copied with their `rowid`s (which the full-text index is
    keyed by), the old one dropped, and the index, the full-text table (rebuilt from
    the rows) and the triggers made again. Every version a note has is its own row,
    so a note that exists is its own first version (`valid_from` its creation)."""
    have = {row[1] for row in db.execute("PRAGMA table_info(memory_notes)")}
    if "body" not in have:
        for trigger in ("memory_notes_ai", "memory_notes_ad", "memory_notes_au"):
            db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        db.execute("DROP TABLE IF EXISTS memory_notes_fts")
        db.execute("ALTER TABLE memory_notes RENAME TO memory_notes_v1")
        db.execute(_NOTES_TABLE)
        old = {row[1] for row in db.execute("PRAGMA table_info(memory_notes_v1)")}
        carried = [c.strip() for c in _NOTE_COLUMNS.split(",") if c.strip() in old]
        db.execute(
            f"INSERT INTO memory_notes (rowid, {', '.join(carried)}, valid_from) "
            f"SELECT rowid, {', '.join(carried)}, created_at FROM memory_notes_v1"
        )
        db.execute("DROP TABLE memory_notes_v1")
        for statement in _MEMORY_SCHEMA:
            db.execute(statement)
        db.execute("INSERT INTO memory_notes_fts (memory_notes_fts) VALUES ('rebuild')")
    for table, column, declaration in _MEMORY_V2_COLUMNS:
        present = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in present:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


#: Schema 23's columns (MV2, past chats): whether a connection may search the person's past
#: chats (off for every connection until they tick it), and whether models may search them
#: (`search_chats`, on until the person turns it off).
_PAST_CHATS_COLUMNS: list[tuple[str, str, str]] = [
    (
        "connections",
        "search_chats",
        "INTEGER NOT NULL DEFAULT 0 CHECK (search_chats IN (0, 1))",
    ),
    (
        "memory_prefs",
        "chats_tool",
        "INTEGER NOT NULL DEFAULT 1 CHECK (chats_tool IN (0, 1))",
    ),
]


def _past_chats(db: sqlite3.Connection) -> None:
    """Schema 23 (MV2): two columns. A person's exchanges are in their knowledge index, not
    here: the store keeps no copy of them."""
    for table, column, declaration in _PAST_CHATS_COLUMNS:
        present = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in present:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


#: Schema 24's columns (MV3, learning and the write policy): on a note, the model that wrote
#: it and the policy it was written under (null for the person's own), whether it was kept
#: with nobody approving it, and when the person first saw it (call 12); a person's own write
#: policy (null: the owner's).
_LEARNING_COLUMNS: list[tuple[str, str, str]] = [
    ("memory_notes", "model", "TEXT"),
    ("memory_notes", "policy", "TEXT"),
    ("memory_notes", "auto", "INTEGER NOT NULL DEFAULT 0 CHECK (auto IN (0, 1))"),
    ("memory_notes", "seen_at", "REAL"),
    (
        "memory_prefs",
        "write_policy",
        "TEXT CHECK (write_policy IS NULL OR write_policy IN ('strict','standard','open'))",
    ),
]


def _learning(db: sqlite3.Connection) -> None:
    """Schema 24 (MV3): the columns above, a person's choice per model (Off, Suggest, Keep
    automatically), and the install's own measurements, one per model id (the newest)."""
    for table, column, declaration in _LEARNING_COLUMNS:
        present = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in present:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    db.execute(
        "CREATE TABLE IF NOT EXISTS memory_model_choices ("
        "owner TEXT NOT NULL, model TEXT NOT NULL CHECK (length(model) BETWEEN 1 AND 256), "
        "mode TEXT NOT NULL CHECK (mode IN ('off','suggest','auto')), set_at REAL NOT NULL, "
        "PRIMARY KEY (owner, model))"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS memory_measurements ("
        "model TEXT PRIMARY KEY CHECK (length(model) BETWEEN 1 AND 256), "
        "measured_at REAL NOT NULL, owner TEXT, result TEXT NOT NULL)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS memory_notes_learned ON memory_notes (owner, auto, created_at)"
    )


def _memory_servers(db: sqlite3.Connection) -> None:
    """Schema 25 (MV5): a person's memory server, and each exchange sent to it or waiting to
    be (`send`), kept there (`sent`), or to be forgotten there since its chat was deleted
    (`forget`). Only ids are kept of an exchange, never its words."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS memory_servers ("
        "owner TEXT PRIMARY KEY, url TEXT NOT NULL, token TEXT NOT NULL DEFAULT '', "
        "name TEXT NOT NULL DEFAULT '', verbs TEXT NOT NULL DEFAULT '{}', "
        "connected_at REAL NOT NULL, last_ok_at REAL, last_error TEXT, last_error_at REAL, "
        "skipped INTEGER NOT NULL DEFAULT 0)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS memory_server_sent ("
        "document_id TEXT PRIMARY KEY, owner TEXT NOT NULL, chat_id TEXT NOT NULL, "
        "message_id TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('send','sent','forget')), "
        "attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL, created_at REAL NOT NULL)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS memory_server_sent_due ON memory_server_sent (state, next_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS memory_server_sent_by_chat ON memory_server_sent (chat_id)"
    )


def _provenance(db: sqlite3.Connection) -> None:
    """Schema 26 (MV6): the documents a note rests on, each as it was when linked; the tidy
    pass's proposals, with what the person answered (so a pair they kept is never proposed
    again); and when the pass last ran for a person. A note's links go with it."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS memory_note_documents ("
        "owner TEXT NOT NULL, note_id TEXT NOT NULL, doc_id TEXT NOT NULL, "
        "scope TEXT NOT NULL, source TEXT NOT NULL, name TEXT NOT NULL, sha256 TEXT NOT NULL, "
        "place TEXT NOT NULL DEFAULT '[]', "
        "how TEXT NOT NULL CHECK (how IN ('answer', 'person')), "
        "linked_at REAL NOT NULL, snoozed_until REAL, snoozed_sha TEXT, "
        "PRIMARY KEY (note_id, doc_id))"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS memory_note_documents_by_owner ON memory_note_documents (owner)"
    )
    db.execute(
        "CREATE TRIGGER IF NOT EXISTS memory_notes_documents_ad AFTER DELETE ON memory_notes "
        "BEGIN DELETE FROM memory_note_documents WHERE note_id = old.id; END"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS memory_tidy ("
        "id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
        "kind TEXT NOT NULL CHECK (kind IN ('duplicate', 'past')), "
        "pair TEXT NOT NULL, created_at REAL NOT NULL, "
        "settled TEXT CHECK (settled IS NULL OR settled IN ('merged', 'kept', 'removed')), "
        "settled_at REAL, UNIQUE (owner, kind, pair))"
    )
    present = {row[1] for row in db.execute("PRAGMA table_info(memory_prefs)")}
    if "tidy_at" not in present:
        db.execute("ALTER TABLE memory_prefs ADD COLUMN tidy_at REAL")


def _projects(db: sqlite3.Connection) -> None:
    """Schema 27 (MV7): a person's projects, and the project a chat or a note is in (null for
    none: a general note, a chat in no project)."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS projects ("
        "id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
        "name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80), "
        "general_memory INTEGER NOT NULL DEFAULT 1 CHECK (general_memory IN (0, 1)), "
        "general_documents INTEGER NOT NULL DEFAULT 1 CHECK (general_documents IN (0, 1)), "
        "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS projects_by_owner ON projects (owner)")
    for table in ("chats", "memory_notes"):
        present = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if "project_id" not in present:
            db.execute(f"ALTER TABLE {table} ADD COLUMN project_id TEXT")
        db.execute(f"CREATE INDEX IF NOT EXISTS {table}_by_project ON {table} (owner, project_id)")


#: The states of a removed person (row 7). `held`: removed before Eugene ever confirmed them,
#: so hidden with no delete date until the owner chooses. `hidden`: hidden, and deleted
#: `removedPeopleDays` after `clock_from`. `kept`: the owner pressed Keep. `purging`: being
#: deleted (resumed after a restart). `purged`: deleted; the row keeps the id, name and dates.
REMOVED_STATES = ("held", "hidden", "kept", "purging", "purged")
#: The states whose data is still there and hidden from every read.
HIDDEN_STATES = ("held", "hidden", "kept", "purging")


def _removal(db: sqlite3.Connection) -> None:
    """Schema 28 (row 7): when Eugene last confirmed each person, the people it no longer has,
    and the log of every download of a person's data. New tables and one column; nothing moves."""
    # `email`: the address Eugene's ID token carried, if the person has one. Kept with a removed
    # person's hidden data so a later row (7b) can give it back to a new account that proves
    # the same address, and cleared when the data is deleted.
    present = {row[1] for row in db.execute("PRAGMA table_info(people)")}
    for column, declaration in (("known_at", "REAL"), ("email", "TEXT")):
        if column not in present:
            db.execute(f"ALTER TABLE people ADD COLUMN {column} {declaration}")
    states = ", ".join(f"'{s}'" for s in REMOVED_STATES)
    # `delete_at`: when a hidden person's data is deleted, fixed when the wait began (from the
    # setting then), so changing the setting never shortens a wait already begun (Troy).
    db.execute(
        "CREATE TABLE IF NOT EXISTS removed_people ("
        "sub TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT, "
        f"state TEXT NOT NULL CHECK (state IN ({states})), "
        "found_at REAL NOT NULL, clock_from REAL, delete_at REAL, "
        "steps TEXT NOT NULL DEFAULT '{}', "
        "attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, purged_at REAL)"
    )
    present = {row[1] for row in db.execute("PRAGMA table_info(removed_people)")}
    if "delete_at" not in present:
        db.execute("ALTER TABLE removed_people ADD COLUMN delete_at REAL")
    db.execute(
        "CREATE TABLE IF NOT EXISTS exports ("
        "id TEXT PRIMARY KEY, asker TEXT NOT NULL, asker_name TEXT NOT NULL, "
        "subject TEXT NOT NULL, subject_name TEXT NOT NULL, "
        "scope TEXT NOT NULL CHECK (scope IN ('self', 'owner')), "
        "state TEXT NOT NULL CHECK (state IN ('making', 'ready', 'failed', 'expired', 'deleted')), "
        "holds TEXT NOT NULL DEFAULT '{}', size INTEGER, error TEXT, "
        "created_at REAL NOT NULL, expires_at REAL NOT NULL)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS exports_by_asker ON exports (asker, created_at)")


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
    # Interactive surfaces and durable action intent; no existing chat data changes.
    16: [*INTERACTION_SCHEMA],
    17: [*AGUI_SCHEMA],
    18: ["ALTER TABLE sessions ADD COLUMN access_token TEXT"],
    19: [
        "ALTER TABLE messages ADD COLUMN reasoning_tokens INTEGER",
        "ALTER TABLE messages ADD COLUMN reasoning_seconds REAL",
    ],
    # Memory suggestions (MR8, 2026-10-18): new columns and one new table only.
    20: [_memory_learning],
    # Connections (MR9b, 2026-10-18): one new table and two new note columns.
    21: [_connections],
    # Memory v2 (MV1, 2026-10-19): note bodies, the procedure kind, versions, tags, two
    # budgets. Every chat's frozen notes are retaken once: a v1 copy holds notes that are
    # now chosen each turn instead.
    22: [
        _memory_v2,
        "UPDATE memory_prefs SET relevant_budget_tokens = budget_tokens "
        "WHERE budget_tokens IS NOT NULL",
        "UPDATE chats SET memory_snapshot = NULL WHERE memory_snapshot IS NOT NULL",
    ],
    # Past chats (MV2, 2026-10-19): two new columns only.
    23: [_past_chats],
    # Learning and the write policy (MV3, 2026-10-19): new columns and two new tables.
    24: [_learning],
    25: [_memory_servers],
    # Provenance and tidy (MV6, 2026-10-19): two new tables, a trigger and one column.
    26: [_provenance],
    # Projects (MV7, 2026-10-19): one new table and two new columns; nothing moves.
    27: [_projects],
    # A removed person (row 7, 2026-10-21): two new tables and one new column.
    28: [_removal],
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
        for statement in [*_MEMORY_SCHEMA, *INTERACTION_SCHEMA, *AGUI_SCHEMA]:
            db.execute(statement)
        _memory_learning(db)
        _connections(db)
        _memory_v2(db)
        _past_chats(db)
        _learning(db)
        _memory_servers(db)
        _provenance(db)
        _projects(db)
        _removal(db)
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


#: The people whose data is hidden from every read (row 7): removed from Eugene, not yet deleted.
_HIDDEN = (
    "(SELECT sub FROM removed_people WHERE state IN ("
    + ", ".join(f"'{s}'" for s in HIDDEN_STATES)
    + "))"
)

#: Every table that holds a person's rows, by the column that finds them, in the order a purge
#: deletes them (row 7). `owner`, `sub` and `subject` name the person; `chat_id` is one of
#: their chats, and `artifact_id` one of their artifacts. A table that holds a person's rows
#: must be listed: `tests/test_person_removal.py` seeds every table with a person column and
#: fails on one the purge leaves. A copy kept from an older schema is purged of the tables and
#: columns it has. `memory_measurements.owner` is who measured a model for the whole install:
#: the measurement stays and the name goes (`_NULLED`).
PERSON_TABLES: tuple[tuple[str, str], ...] = (
    ("artifact_revisions", "artifact_id"),
    ("agui_events", "chat_id"),
    ("agui_runs", "chat_id"),
    ("agui_runs", "owner"),
    ("interaction_actions", "chat_id"),
    ("interaction_surfaces", "chat_id"),
    ("interaction_surfaces", "owner"),
    ("interaction_cursors", "chat_id"),
    ("messages", "chat_id"),
    ("files", "chat_id"),
    ("files", "owner"),
    ("artifacts", "chat_id"),
    ("artifacts", "owner"),
    ("memory_server_sent", "chat_id"),
    ("memory_server_sent", "owner"),
    ("memory_servers", "owner"),
    ("memory_note_documents", "owner"),
    ("memory_note_tags", "owner"),
    ("memory_events", "owner"),
    ("memory_notes", "owner"),
    ("memory_tags", "owner"),
    ("memory_prefs", "owner"),
    ("memory_dismissed", "owner"),
    ("memory_model_choices", "owner"),
    ("memory_tidy", "owner"),
    ("connections", "owner"),
    ("documents_prefs", "owner"),
    ("document_folders", "owner"),
    ("media", "owner"),
    ("projects", "owner"),
    ("folder_grants", "subject"),
    ("sessions", "sub"),
    ("chats", "owner"),
    ("people", "sub"),
)
_NULLED: tuple[tuple[str, str], ...] = (("memory_measurements", "owner"),)
#: The settings kept per person, as `<name>:<sub>` (`api.py`).
_PERSON_SETTINGS = ("installModeSeen", "runPreviews")


def _purge_rows(db: sqlite3.Connection, sub: str) -> dict[str, int]:
    """Delete every row of `sub`'s that `PERSON_TABLES` names, in an open transaction, from the
    tables this store (or copy) has. How many rows went from each table."""
    have: dict[str, set[str]] = {}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        have[name] = {row[1] for row in db.execute(f"PRAGMA table_info({name})")}
    chats: list[str] = []
    if "owner" in have.get("chats", set()):
        chats = [r[0] for r in db.execute("SELECT id FROM chats WHERE owner = ?", (sub,))]
    artifacts: list[str] = []
    if {"owner", "chat_id"} <= have.get("artifacts", set()):
        marks = ", ".join("?" for _ in chats)
        artifacts = [
            r[0]
            for r in db.execute(
                f"SELECT id FROM artifacts WHERE owner = ? OR chat_id IN ({marks})", (sub, *chats)
            )
        ]
    found = {"chat_id": chats, "artifact_id": artifacts}
    gone: dict[str, int] = {}
    for table, column in PERSON_TABLES:
        if column not in have.get(table, set()):
            continue
        values = found.get(column, [sub])
        if not values:
            continue
        marks = ", ".join("?" for _ in values)
        cursor = db.execute(f"DELETE FROM {table} WHERE {column} IN ({marks})", values)
        gone[table] = gone.get(table, 0) + max(cursor.rowcount, 0)
    for table, column in _NULLED:
        if column in have.get(table, set()):
            db.execute(f"UPDATE {table} SET {column} = NULL WHERE {column} = ?", (sub,))
    if "settings" in have:
        keys = [f"{name}:{sub}" for name in _PERSON_SETTINGS]
        db.execute(
            f"DELETE FROM settings WHERE key IN ({', '.join('?' for _ in keys)})", tuple(keys)
        )
    # The removal's own record stays (its id, name and dates), but not the email kept to give
    # the data back (row 7b): it goes here, before the file is written again, so no free page,
    # log or kept copy holds it.
    if "email" in have.get("removed_people", set()):
        db.execute("UPDATE removed_people SET email = NULL WHERE sub = ?", (sub,))
    return gone


def _rewrite(db: sqlite3.Connection) -> None:
    """Leave nothing deleted anywhere in the file: the full-text index's segments merged (a
    deleted word is otherwise kept in them), every page written again (`VACUUM`), and the
    write-ahead log folded in and emptied. `secure_delete` is on, so the pages a delete freed
    held zeros already; this takes them out of the file."""
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_notes_fts'"
    ).fetchone():
        db.execute("INSERT INTO memory_notes_fts (memory_notes_fts) VALUES ('optimize')")
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.execute("VACUUM")
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _all_copies(path: Path) -> list[Path]:
    """Every copy kept beside the store before a migration (`_keep_backup`), any schema."""
    named = re.compile(rf"{re.escape(path.name)}\.schema-\d+(?:\.\d+)?\.bak")
    if not path.parent.is_dir():
        return []
    return sorted(p for p in path.parent.iterdir() if named.fullmatch(p.name) and p.is_file())


def _purge_copy(copy: Path, sub: str) -> None:
    """The purge, on one kept copy: its own tables, then the file written again. The copy stays
    for everyone else in it; a copy is one file in rollback-journal mode, as `_keep_backup` makes
    it, and is left that way."""
    db = sqlite3.connect(copy, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA secure_delete=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            _purge_rows(db, sub)
            db.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                db.execute("ROLLBACK")
            raise
        _rewrite(db)
    finally:
        db.close()


@dataclass
class Person:
    sub: str
    name: str
    role: str
    username: str | None = None
    #: The email Eugene's ID token carried, if any (row 7: kept with a removed person's data).
    email: str | None = None
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
    access_token: str | None = None


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
    #: MV7: the project the chat is in, or None.
    project_id: str | None = None


@dataclass
class Project:
    """A person's project (MV7): a named group of chats with its own notes."""

    id: str
    owner: str
    name: str
    created_at: float
    updated_at: float
    #: Whether its chats are given the person's general notes too.
    general_memory: bool = True
    #: Whether its chats search My documents too (MV7b).
    general_documents: bool = True


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
    #: For a suggestion: the id of the note it would replace ("Keep new").
    replaces: str | None = None
    #: For a note a connection suggested (MR9b): its id, and its name at the time.
    source_connection_id: str | None = None
    source_connection: str | None = None
    #: MV1. The note's Markdown body, or None when it was not read (a list reads only
    #: its length, `body_chars`).
    body: str | None = ""
    body_chars: int = 0
    #: When this version became the current one; when it was replaced, and by which.
    valid_from: float | None = None
    superseded_at: float | None = None
    superseded_by: str | None = None
    #: The tags on the note, as the person's list writes them, in list order.
    tags: list[str] = field(default_factory=list)
    #: For a suggestion: the tags it came with, some perhaps not on the list.
    proposed_tags: list[str] = field(default_factory=list)
    #: MV3. The model that wrote it and the write policy in effect then (`memory_policy`'s
    #: JSON), both None for the person's own; whether it was kept with nobody approving it;
    #: when the person first saw it (call 12: an unseen kept note stays on the machines).
    model: str | None = None
    policy: str | None = None
    auto: bool = False
    seen_at: float | None = None
    #: MV7: the project the note is kept to, or None for a general note.
    project_id: str | None = None


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


class TagListFull(Exception):
    """The person's tag list has as many tags as it may."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"{limit} tags is the limit")
        self.limit = limit


#: Tags one person's list may hold.
MAX_TAGS = 1000
#: What a kept `search_memory` result becomes when a note it named is deleted (MV1).
FORGOTTEN_RESULT = "A note this search found was deleted since, so its result is not kept."
#: MV3b: what a kept `remember` call says once the person removed the note it kept.
REMOVED_RESULT = "The person removed this note."
#: What a kept `search_chats` result becomes when a chat it quoted is deleted (MV2).
FORGOTTEN_CHAT = "A chat this search found was deleted since, so its result is not kept."
#: The server id a kept `search_chats` call is known by (`tools.CHATS_SERVER`).
CHATS_SERVER = "past-chats"


def tag_key(tag: str) -> str:
    """What makes two tags one tag: the words, ignoring case and Unicode's forms."""
    return " ".join(unicodedata.normalize("NFKC", tag).casefold().split())


#: A note's columns without its body, which can be 20,000 characters: what a list reads.
_NOTE_LIST = (
    "id, owner, text, kind, status, pinned, source, source_chat_id, source_message_id, quote, "
    "expires_at, revisit_at, created_at, updated_at, last_used_at, use_count, replaces, "
    "source_connection_id, source_connection, length(body) AS body_chars, valid_from, "
    "superseded_at, superseded_by, proposed_tags, model, policy, auto, seen_at, project_id"
)


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
    #: What this answer's turn was sent of the person's notes, chosen for it (MV1,
    #: `memory_api.TurnMemory.record`); None when none was.
    memory_used: dict[str, Any] | None = None

    reasoning_tokens: int | None = None
    reasoning_seconds: float | None = None


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
        project_id=row["project_id"] if "project_id" in row.keys() else None,  # noqa: SIM118
    )


def _note(row: sqlite3.Row) -> MemoryNote:
    keys = row.keys()
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
        replaces=row["replaces"],
        source_connection_id=row["source_connection_id"],
        source_connection=row["source_connection"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_used_at=row["last_used_at"],
        use_count=row["use_count"],
        body=row["body"] if "body" in keys else None,
        body_chars=int(row["body_chars"]) if "body_chars" in keys else len(row["body"] or ""),
        valid_from=row["valid_from"],
        superseded_at=row["superseded_at"],
        superseded_by=row["superseded_by"],
        proposed_tags=_tag_list(row["proposed_tags"]),
        model=row["model"] if "model" in keys else None,
        policy=row["policy"] if "policy" in keys else None,
        auto=bool(row["auto"]) if "auto" in keys else False,
        seen_at=row["seen_at"] if "seen_at" in keys else None,
        project_id=row["project_id"] if "project_id" in keys else None,
    )


def _tag_list(value: str | None) -> list[str]:
    try:
        found = json.loads(value) if value else []
    except ValueError:
        return []
    return [str(t) for t in found] if isinstance(found, list) else []


@dataclass
class ConnectionRecord:
    """A connection a person made for an outside app (MR9b, `connections.py`): what it may
    do, until when, and how much it was used. The token itself is never kept."""

    id: str
    owner: str
    name: str
    read_notes: bool
    search_documents: bool
    suggest_notes: bool
    created_at: float
    expires_at: float
    last_used_at: float | None = None
    calls_day: str | None = None
    calls_today: int = 0
    #: MV2: whether it may search the person's past chats (`search_chats`).
    search_chats: bool = False


def _connection(row: sqlite3.Row) -> ConnectionRecord:
    return ConnectionRecord(
        id=row["id"],
        owner=row["owner"],
        name=row["name"],
        read_notes=bool(row["read_notes"]),
        search_documents=bool(row["search_documents"]),
        suggest_notes=bool(row["suggest_notes"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        last_used_at=row["last_used_at"],
        calls_day=row["calls_day"],
        calls_today=int(row["calls_today"]),
        search_chats=bool(row["search_chats"]),
    )


#: What a person's memory settings are until they change one. `external` None
#: is "whatever the install's default is" (it is `ask` unless the owner says).
MEMORY_PREFS_DEFAULT: dict[str, Any] = {
    "enabled": True,
    "learn": "off",
    "external": None,
    "use_mode": "relevant",
    "budget_tokens": None,
    # MR8: whether learn is the person's own choice (else it follows the install and the
    # model), whether they turned the offer of suggestions away, and when they were last
    # asked whether a project is still true.
    "learn_set": False,
    "offer_dismissed": False,
    "revisit_asked_at": None,
    # MV1: `budget_tokens` is the pinned block's ceiling, this the relevant memory's a
    # turn; and whether models may search the person's notes (`search_memory`).
    "relevant_budget_tokens": None,
    "search_tool": True,
    # MV2: whether models may search the person's past chats (`search_chats`).
    "chats_tool": True,
    # MV3: the person's write policy, stricter than the owner's or (if the owner lets them)
    # any; None is the owner's.
    "write_policy": None,
    # MV6: when the tidy pass last ran for the person.
    "tidy_at": None,
}


#: At most this many documents a note rests on (MV6).
MAX_NOTE_DOCUMENTS = 20


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
        memory_used=_snapshot(row["memory_used"]),
        reasoning_tokens=row["reasoning_tokens"],
        reasoning_seconds=row["reasoning_seconds"],
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
    "memory_used",
    "reasoning_tokens",
    "reasoning_seconds",
}
_CHAT_FIELDS = {"title", "model", "settings", "search", "updated_at", "project_id"}
_JSON_FIELDS = {
    "attachments",
    "sources",
    "settings",
    "tool_rounds",
    "search_suggestions",
    "output_cap",
    "retrieval",
    "memory_used",
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


def _drop_chat_from_answers(db: sqlite3.Connection, owner: str, chat_id: str) -> None:
    """Take a deleted chat out of what the person's other answers kept of it (MV2): a turn's
    record of the past-chat excerpts it was sent loses that chat's (counted `forgotten`), and
    a kept `search_chats` result that quoted it is replaced, so neither the page nor a later
    request carries a deleted chat's words."""
    rows = db.execute(
        "SELECT m.id, m.memory_used, m.tool_rounds FROM messages AS m "
        "JOIN chats AS c ON c.id = m.chat_id WHERE c.owner = ? AND m.chat_id != ? "
        "AND m.role = 'assistant' AND (m.memory_used LIKE ? OR m.tool_rounds LIKE ?)",
        (owner, chat_id, '%"pastChats"%', f'%"{CHATS_SERVER}"%'),
    ).fetchall()
    for row in rows:
        changes: dict[str, str] = {}
        used = _snapshot(row["memory_used"])
        past = used.get("pastChats") if used is not None else None
        if used is not None and isinstance(past, dict):
            listed = [e for e in past.get("exchanges") or [] if isinstance(e, dict)]
            kept = [e for e in listed if e.get("chatId") != chat_id]
            if len(kept) != len(listed):
                past["exchanges"] = kept
                past["forgotten"] = int(past.get("forgotten") or 0) + len(listed) - len(kept)
                changes["memory_used"] = json.dumps(used)
        try:
            rounds = json.loads(row["tool_rounds"] or "[]")
        except ValueError:
            rounds = []
        touched = False
        for turn in rounds if isinstance(rounds, list) else []:
            for call in turn.get("calls") or [] if isinstance(turn, dict) else []:
                if not isinstance(call, dict) or call.get("serverId") != CHATS_SERVER:
                    continue
                if chat_id in (call.get("chats") or []):
                    call["result"] = FORGOTTEN_CHAT
                    call["content"] = []
                    call["chats"] = [c for c in call.get("chats") or [] if c != chat_id]
                    touched = True
        if touched:
            changes["tool_rounds"] = json.dumps(rounds)
        if changes:
            columns = ", ".join(f"{k} = ?" for k in changes)
            db.execute(
                f"UPDATE messages SET {columns} WHERE id = ?", (*changes.values(), row["id"])
            )


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
    db.execute("UPDATE interaction_cursors SET value=value+1 WHERE chat_id=?", (chat_id,))
    InteractionStore.sweep_in_transaction(db, chat_id)
    return True


class Store:
    """Every read and write Workbench makes, as methods. See the module."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workbench-store")
        self._db: sqlite3.Connection | None = None
        self.interactions = InteractionStore(self)
        self.agui = AGUIStore(self)
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
            # A deleted note's words are overwritten where they lay, not left in a free page.
            db.execute("PRAGMA secure_delete=ON")
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
        """A sign-in or a refresh: Eugene vouched for the person just now (`known_at`)."""

        def go() -> None:
            now = time.time()
            self._conn().execute(
                "INSERT INTO people (sub, name, username, role, first_seen, last_seen, known_at, "
                "email) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(sub) DO UPDATE SET "
                "name = excluded.name, username = excluded.username, role = excluded.role, "
                "last_seen = excluded.last_seen, known_at = excluded.known_at, "
                "email = excluded.email",
                (
                    person.sub,
                    person.name,
                    person.username,
                    person.role,
                    now,
                    now,
                    now,
                    person.email,
                ),
            )

        await self._run(go)

    async def person(self, sub: str) -> Person | None:
        """Who `sub` is, or None: never signed in here, or removed from Eugene (row 7)."""

        def go() -> Person | None:
            row = (
                self._conn()
                .execute(f"SELECT * FROM people WHERE sub = ? AND sub NOT IN {_HIDDEN}", (sub,))
                .fetchone()
            )
            if row is None:
                return None
            return Person(
                sub=row["sub"], name=row["name"], role=row["role"], username=row["username"]
            )

        return await self._run(go)

    async def people(self) -> list[tuple[Person, int, int]]:
        """Everyone who has signed in and is not removed, with how many chats and how many
        media results each has."""

        def go() -> list[tuple[Person, int, int]]:
            rows = self._conn().execute(
                "SELECT p.*, (SELECT COUNT(*) FROM chats c WHERE c.owner = p.sub) AS chats, "
                "(SELECT COUNT(*) FROM media m WHERE m.owner = p.sub) AS media "
                f"FROM people p WHERE p.sub NOT IN {_HIDDEN} ORDER BY p.name COLLATE NOCASE"
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
                "access_expires_at, created_at, expires_at, access_token) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row.id_hash,
                    row.secret_hash,
                    row.sub,
                    row.refresh_token,
                    row.access_expires_at,
                    row.created_at,
                    row.expires_at,
                    row.access_token,
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
                access_token=r["access_token"],
            )

        return await self._run(go)

    async def refreshed(
        self,
        id_hash: str,
        refresh_token: str | None,
        expires_at: float,
        access_token: str | None = None,
    ) -> None:
        def go() -> None:
            self._conn().execute(
                "UPDATE sessions SET refresh_token = COALESCE(?, refresh_token), "
                "access_expires_at = ?, access_token = ? WHERE id_hash = ?",
                (refresh_token, expires_at, access_token, id_hash),
            )

        await self._run(go)

    async def latest_session(self, sub: str, now: float) -> SessionRow | None:
        """A background job may use only an unexpired sign-in of its owner."""

        def go() -> str | None:
            row = (
                self._conn()
                .execute(
                    "SELECT id_hash FROM sessions WHERE sub = ? AND expires_at > ? "
                    f"AND sub NOT IN {_HIDDEN} ORDER BY created_at DESC, id_hash DESC LIMIT 1",
                    (sub, now),
                )
                .fetchone()
            )
            return str(row[0]) if row else None

        ident = await self._run(go)
        return await self.session(ident) if ident else None

    async def delete_session(self, id_hash: str) -> None:
        await self._run(
            lambda: self._conn().execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
        )
        await self.interactions.sweep()

    async def sweep_sessions(self, now: float) -> None:
        await self._run(
            lambda: self._conn().execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        )

    # --- chats ----------------------------------------------------------

    async def create_chat(self, chat: Chat) -> None:
        def go() -> None:
            self._conn().execute(
                "INSERT INTO chats (id, owner, title, model, settings, search, created_at, "
                "updated_at, project_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chat.id,
                    chat.owner,
                    chat.title,
                    chat.model,
                    json.dumps(chat.settings),
                    int(chat.search),
                    chat.created_at,
                    chat.updated_at,
                    chat.project_id,
                ),
            )

        await self._run(go)

    async def chat(self, chat_id: str) -> Chat | None:
        """A chat, or None: none such, or its person was removed (row 7)."""

        def go() -> Chat | None:
            r = (
                self._conn()
                .execute(f"SELECT * FROM chats WHERE id = ? AND owner NOT IN {_HIDDEN}", (chat_id,))
                .fetchone()
            )
            return _chat(r) if r is not None else None

        return await self._run(go)

    async def chats(self, owner: str) -> list[Chat]:
        def go() -> list[Chat]:
            rows = self._conn().execute(
                f"SELECT * FROM chats WHERE owner = ? AND owner NOT IN {_HIDDEN} "
                "ORDER BY updated_at DESC",
                (owner,),
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

    # --- MV7: projects -----------------------------------------------------

    @staticmethod
    def _project(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            owner=row["owner"],
            name=row["name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            general_memory=bool(row["general_memory"]),
            general_documents=bool(row["general_documents"]),
        )

    async def projects(self, owner: str) -> list[Project]:
        """The person's projects, by name."""

        def go() -> list[Project]:
            rows = self._conn().execute(
                "SELECT * FROM projects WHERE owner = ? ORDER BY name COLLATE NOCASE, id",
                (owner,),
            )
            return [self._project(r) for r in rows]

        return await self._run(go)

    async def project(self, owner: str, project_id: str) -> Project | None:
        def go() -> Project | None:
            row = (
                self._conn()
                .execute("SELECT * FROM projects WHERE owner = ? AND id = ?", (owner, project_id))
                .fetchone()
            )
            return self._project(row) if row is not None else None

        return await self._run(go)

    async def put_project(
        self, project: Project, *, limit: int, retake: bool = False
    ) -> str | None:
        """Make or rename a project: None when done, else why not (`limit` reached, or another
        of the person's projects has that name once case and spacing are set aside). With
        `retake`, its chats' frozen pinned blocks go in the same step (the general memory
        switch changed what they are given)."""

        def go() -> str | None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                same = [
                    r["id"]
                    for r in db.execute(
                        "SELECT id, name FROM projects WHERE owner = ?", (project.owner,)
                    )
                    if note_key(r["name"]) == note_key(project.name) and r["id"] != project.id
                ]
                if same:
                    db.execute("COMMIT")
                    return "taken"
                held = db.execute(
                    "SELECT 1 FROM projects WHERE owner = ? AND id = ?", (project.owner, project.id)
                ).fetchone()
                if held is None:
                    count = db.execute(
                        "SELECT COUNT(*) AS n FROM projects WHERE owner = ?", (project.owner,)
                    ).fetchone()["n"]
                    if int(count) >= limit:
                        db.execute("COMMIT")
                        return "full"
                    db.execute(
                        "INSERT INTO projects (id, owner, name, general_memory, "
                        "general_documents, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            project.id,
                            project.owner,
                            project.name,
                            int(project.general_memory),
                            int(project.general_documents),
                            project.created_at,
                            project.updated_at,
                        ),
                    )
                else:
                    db.execute(
                        "UPDATE projects SET name = ?, general_memory = ?, general_documents = ?, "
                        "updated_at = ? WHERE owner = ? AND id = ?",
                        (
                            project.name,
                            int(project.general_memory),
                            int(project.general_documents),
                            project.updated_at,
                            project.owner,
                            project.id,
                        ),
                    )
                if retake:
                    db.execute(
                        "UPDATE chats SET memory_snapshot = NULL "
                        "WHERE owner = ? AND project_id = ?",
                        (project.owner, project.id),
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return None

        return await self._run(go)

    async def move_chat(self, owner: str, chat_id: str, project_id: str | None) -> bool:
        """Put a chat of the person's in a project, or in none. Its frozen pinned block goes:
        the next turn is given the notes of where it is now."""

        def go() -> bool:
            return bool(
                self._conn()
                .execute(
                    "UPDATE chats SET project_id = ?, memory_snapshot = NULL "
                    "WHERE owner = ? AND id = ?",
                    (project_id, owner, chat_id),
                )
                .rowcount
            )

        return await self._run(go)

    async def move_notes(self, owner: str, note_ids: Sequence[str], project_id: str | None) -> int:
        """Keep these notes of the person's to a project, or make them general. The chats that
        froze a copy of them take it again."""

        def go() -> int:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                done = 0
                for one in note_ids:
                    done += db.execute(
                        "UPDATE memory_notes SET project_id = ?, updated_at = ? "
                        "WHERE owner = ? AND id = ?",
                        (project_id, time.time(), owner, one),
                    ).rowcount
                self._drop_from_chats(db, owner, set(note_ids))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return done

        return await self._run(go)

    async def release_project(self, owner: str, project_id: str) -> tuple[int, int]:
        """Delete a project and keep what was in it: its chats and notes become general.
        `(chats, notes)` released; nothing is left under its id."""

        def go() -> tuple[int, int]:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                chats = db.execute(
                    "UPDATE chats SET project_id = NULL, memory_snapshot = NULL "
                    "WHERE owner = ? AND project_id = ?",
                    (owner, project_id),
                ).rowcount
                released = {
                    r["id"]
                    for r in db.execute(
                        "SELECT id FROM memory_notes WHERE owner = ? AND project_id = ?",
                        (owner, project_id),
                    )
                }
                notes = db.execute(
                    "UPDATE memory_notes SET project_id = NULL WHERE owner = ? AND project_id = ?",
                    (owner, project_id),
                ).rowcount
                db.execute("DELETE FROM projects WHERE owner = ? AND id = ?", (owner, project_id))
                # A chat that froze a copy of them takes it again (its project's were cleared).
                Store._drop_from_chats(db, owner, released)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return int(chats), int(notes)

        return await self._run(go)

    async def drop_project_row(self, owner: str, project_id: str) -> bool:
        """The project itself, once its chats and notes are gone. Whatever is still under its
        id (a suggestion waiting from a chat moved out of it, an earlier version, a chat made
        while it was being deleted) becomes general in the same step: nothing is left under an
        id that no longer exists."""

        def go() -> bool:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "UPDATE chats SET project_id = NULL, memory_snapshot = NULL "
                    "WHERE owner = ? AND project_id = ?",
                    (owner, project_id),
                )
                db.execute(
                    "UPDATE memory_notes SET project_id = NULL WHERE owner = ? AND project_id = ?",
                    (owner, project_id),
                )
                done = db.execute(
                    "DELETE FROM projects WHERE owner = ? AND id = ?", (owner, project_id)
                ).rowcount
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return bool(done)

        return await self._run(go)

    async def project_counts(self, owner: str) -> tuple[dict[str, int], dict[str, int]]:
        """How many chats, and how many current notes, each of the person's projects holds."""

        def go() -> tuple[dict[str, int], dict[str, int]]:
            db = self._conn()
            chats = {
                r[0]: int(r[1])
                for r in db.execute(
                    "SELECT project_id, COUNT(*) FROM chats WHERE owner = ? "
                    "AND project_id IS NOT NULL GROUP BY project_id",
                    (owner,),
                )
            }
            notes = {
                r[0]: int(r[1])
                for r in db.execute(
                    "SELECT project_id, COUNT(*) FROM memory_notes WHERE owner = ? "
                    "AND status = 'active' AND project_id IS NOT NULL GROUP BY project_id",
                    (owner,),
                )
            }
            return chats, notes

        return await self._run(go)

    async def delete_chat(self, chat_id: str) -> None:
        """Delete a chat and everything of it, and (MV2) every copy of its exchanges the
        person's other answers kept, in one transaction: a turn's record of the excerpts it
        was sent (`memory_used.pastChats`) and a kept `search_chats` result that quoted it.
        Its exchanges in the person's index go first, by the route (`exchanges_index`)."""

        def go() -> None:
            db = self._conn()
            db.execute("BEGIN")
            try:
                found = db.execute("SELECT owner FROM chats WHERE id = ?", (chat_id,)).fetchone()
                if found is not None:
                    _drop_chat_from_answers(db, found["owner"], chat_id)
                db.execute(
                    "DELETE FROM artifact_revisions WHERE artifact_id IN "
                    "(SELECT id FROM artifacts WHERE chat_id = ?)",
                    (chat_id,),
                )
                db.execute("DELETE FROM artifacts WHERE chat_id = ?", (chat_id,))
                db.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
                db.execute("DELETE FROM files WHERE chat_id = ?", (chat_id,))
                # Suggestions waiting from it go with it (their quotes are its words); notes
                # kept from it stay, as the delete dialog says.
                db.execute(
                    "DELETE FROM memory_notes WHERE source_chat_id = ? AND status = 'pending'",
                    (chat_id,),
                )
                # MV5: what was sent to the person's memory server is to be forgotten there;
                # what was waiting to be sent is not sent.
                db.execute(
                    "DELETE FROM memory_server_sent WHERE chat_id = ? AND state = 'send'",
                    (chat_id,),
                )
                # Its day to be forgotten in starts now, not when it was sent.
                now = time.time()
                db.execute(
                    "UPDATE memory_server_sent SET state = 'forget', attempts = 0, next_at = ?, "
                    "created_at = ? WHERE chat_id = ? AND state = 'sent'",
                    (now, now, chat_id),
                )
                db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

        await self._run(go)

    # --- messages -------------------------------------------------------

    async def add_message(
        self, message: Message, *, parent_id: str | None, agui_run_id: str | None = None
    ) -> Message:
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
                allow_chat_write(db, message.chat_id, agui_run_id)
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

    # --- past chats (MV2) -------------------------------------------------

    async def exchange_tree(self, chat_id: str) -> list[Message]:
        """Every row of a chat with only what its exchanges are made of (`exchanges_index`):
        no reasoning, tool rounds or records, which can be most of a chat's bytes."""

        def go() -> list[Message]:
            rows = self._conn().execute(
                "SELECT id, chat_id, seq, role, status, created_at, content, answer_from, "
                "parent_id, chosen FROM messages WHERE chat_id = ? ORDER BY seq",
                (chat_id,),
            )
            return [
                Message(
                    id=r["id"],
                    chat_id=r["chat_id"],
                    seq=r["seq"],
                    role=r["role"],
                    status=r["status"],
                    created_at=r["created_at"],
                    content=r["content"],
                    answer_from=r["answer_from"],
                    parent_id=r["parent_id"],
                    chosen=bool(r["chosen"]),
                )
                for r in rows
            ]

        return await self._run(go)

    async def chat_signatures(self, owner: str, chat_id: str | None = None) -> dict[str, str]:
        """Each of the person's chats, newest first, by id: a signature that moves whenever
        what its exchanges are could have (its title, its rows, which are chosen, which are
        running). One grouped query for every chat."""

        def go() -> dict[str, str]:
            rows = self._conn().execute(
                "SELECT c.id, c.title, COUNT(m.id) AS n, COALESCE(MAX(m.seq), 0) AS last, "
                "COALESCE(SUM(CASE WHEN m.chosen THEN m.seq ELSE 0 END), 0) AS chosen, "
                "COALESCE(SUM(m.status = 'running'), 0) AS running "
                "FROM chats AS c LEFT JOIN messages AS m ON m.chat_id = c.id "
                f"WHERE c.owner = ?{' AND c.id = ?' if chat_id is not None else ''} "
                "GROUP BY c.id ORDER BY c.updated_at DESC, c.id",
                (owner,) if chat_id is None else (owner, chat_id),
            )
            return {
                r["id"]: hashlib.sha256(
                    json.dumps(
                        [r["title"], r["n"], r["last"], r["chosen"], r["running"]],
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest()[:32]
                for r in rows
            }

        return await self._run(go)

    async def chat_people(self) -> list[str]:
        """Everyone with a chat."""

        def go() -> list[str]:
            return [
                r[0]
                for r in self._conn().execute(
                    f"SELECT DISTINCT owner FROM chats WHERE owner NOT IN {_HIDDEN} ORDER BY 1"
                )
            ]

        return await self._run(go)

    async def exchange_rows(
        self, owner: str, answer_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        """The exchanges these answers end, as the store has them now, by answer id: only
        answers that exist, in one of `owner`'s chats, after a message of the person's, and
        are not running. What past chats show or send is read here, never from the index."""
        ids = list(dict.fromkeys(answer_ids))
        if not ids:
            return {}

        def go() -> dict[str, dict[str, Any]]:
            out: dict[str, dict[str, Any]] = {}
            for start in range(0, len(ids), 500):
                part = ids[start : start + 500]
                marks = ", ".join("?" for _ in part)
                rows = self._conn().execute(
                    "SELECT a.id, a.chat_id, a.content AS answer, a.answer_from, "
                    "u.content AS question, u.created_at, c.title, u.id AS question_id, "
                    "c.project_id "
                    "FROM messages AS a JOIN messages AS u ON u.id = a.parent_id "
                    "AND u.chat_id = a.chat_id JOIN chats AS c ON c.id = a.chat_id "
                    f"WHERE a.id IN ({marks}) AND c.owner = ? AND a.role = 'assistant' "
                    "AND u.role = 'user' AND a.status != 'running' "
                    "AND a.chosen = 1 AND u.chosen = 1",
                    (*part, owner),
                )
                for r in rows:
                    out[r["id"]] = dict(r)
            return out

        return await self._run(go)

    async def choose(self, chat_id: str, message_id: str) -> bool:
        """Show this version, and the branch it remembers after it. False when
        the chat has no such message."""

        def go() -> bool:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                allow_chat_write(db, chat_id)
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
            rows = self._conn().execute(
                f"SELECT * FROM files WHERE reading IS NOT NULL AND owner NOT IN {_HIDDEN}"
            )
            return [
                r
                for r in (_file(row) for row in rows)
                if (r.reading or {}).get("state") == "reading"
            ]

        return await self._run(go)

    async def file(self, file_id: str) -> FileRecord | None:
        def go() -> FileRecord | None:
            r = (
                self._conn()
                .execute(f"SELECT * FROM files WHERE id = ? AND owner NOT IN {_HIDDEN}", (file_id,))
                .fetchone()
            )
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
            r = (
                self._conn()
                .execute(
                    f"SELECT * FROM media WHERE id = ? AND owner NOT IN {_HIDDEN}", (media_id,)
                )
                .fetchone()
            )
            return _media(r) if r is not None else None

        return await self._run(go)

    async def media_list(
        self, owner: str, door: str, *, before: float | None = None, limit: int = 30
    ) -> list[MediaRow]:
        """A person's results on one screen, newest first, a page at a time."""

        def go() -> list[MediaRow]:
            rows = self._conn().execute(
                "SELECT * FROM media WHERE owner = ? AND door = ? AND created_at < ? "
                f"AND owner NOT IN {_HIDDEN} ORDER BY created_at DESC LIMIT ?",
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
                f"AND owner NOT IN {_HIDDEN} ORDER BY created_at"
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

    @staticmethod
    def _tags_of(
        db: sqlite3.Connection, owner: str, note_ids: Sequence[str] | None = None
    ) -> dict[str, list[str]]:
        """Each of the person's notes' tags (only these notes' when given), by note id, as
        their list writes them."""
        found: dict[str, list[str]] = {}
        sql = (
            "SELECT l.note_id, t.tag FROM memory_note_tags AS l JOIN memory_tags AS t "
            "ON t.owner = l.owner AND t.key = l.key WHERE l.owner = ?"
        )
        batches: list[list[str] | None] = [None]
        if note_ids is not None:
            wanted = list(note_ids)
            batches = [wanted[i : i + 500] for i in range(0, len(wanted), 500)]
        for batch in batches:
            where, params = "", [owner]
            if batch is not None:
                where = f" AND l.note_id IN ({', '.join('?' for _ in batch)})"
                params += batch
            for row in db.execute(sql + where + " ORDER BY t.key", params):
                found.setdefault(row["note_id"], []).append(row["tag"])
        return found

    @staticmethod
    def _link_tags(
        db: sqlite3.Connection, owner: str, note_id: str, tags: Sequence[str], now: float
    ) -> None:
        """Set a note's tags to exactly these. A tag not on the person's list is added to it
        (the person wrote it); `TagListFull` when the list has no room for it."""
        db.execute("DELETE FROM memory_note_tags WHERE note_id = ? AND owner = ?", (note_id, owner))
        for tag in tags:
            key = tag_key(tag)
            listed = db.execute(
                "SELECT 1 FROM memory_tags WHERE owner = ? AND key = ?", (owner, key)
            ).fetchone()
            if listed is None:
                held = db.execute(
                    "SELECT COUNT(*) AS n FROM memory_tags WHERE owner = ?", (owner,)
                ).fetchone()["n"]
                if int(held) >= MAX_TAGS:
                    raise TagListFull(MAX_TAGS)
                db.execute(
                    "INSERT INTO memory_tags (owner, key, tag, description, created_at) "
                    "VALUES (?, ?, ?, '', ?)",
                    (owner, key, tag, now),
                )
            db.execute(
                "INSERT OR IGNORE INTO memory_note_tags (owner, note_id, key) VALUES (?, ?, ?)",
                (owner, note_id, key),
            )

    def _notes(
        self,
        db: sqlite3.Connection,
        owner: str,
        where: str,
        params: Sequence[Any],
        *,
        with_body: bool,
        order: str = " ORDER BY created_at DESC, rowid DESC",
    ) -> list[MemoryNote]:
        columns = "*" if with_body else _NOTE_LIST
        rows = db.execute(
            f"SELECT {columns} FROM memory_notes WHERE owner = ?{where}{order}", (owner, *params)
        ).fetchall()
        few = len(rows) <= 500
        tags = self._tags_of(db, owner, [r["id"] for r in rows] if few else None) if rows else {}
        notes = [_note(r) for r in rows]
        for note in notes:
            note.tags = tags.get(note.id, [])
        return notes

    @staticmethod
    def _scrub_notes(db: sqlite3.Connection) -> None:
        """After a delete, leave none of its words in the file: the titles' full-text index
        keeps a deleted word in its segments until they are merged, and the log holds the
        pages it wrote until it is folded in. Best effort: the delete is committed."""
        with contextlib.suppress(sqlite3.Error):
            db.execute("INSERT INTO memory_notes_fts (memory_notes_fts) VALUES ('optimize')")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    @staticmethod
    def _drop_from_answers(
        db: sqlite3.Connection, owner: str, titles: dict[str, str] | None
    ) -> None:
        """Take deleted notes (`titles`: id -> title; every note when None) out of what the
        person's answers kept of them (MV1): a turn's record (`memory_used`) loses them, and a
        `search_memory` result that named one is replaced, so neither the page nor a later
        request carries a deleted note's words."""
        wanted = None if titles is None else {" ".join(t.split()) for t in titles.values()}
        rows = db.execute(
            "SELECT m.id, m.memory_used, m.tool_rounds FROM messages AS m "
            "JOIN chats AS c ON c.id = m.chat_id WHERE c.owner = ? AND m.role = 'assistant' "
            "AND (m.memory_used IS NOT NULL OR m.tool_rounds LIKE '%\"memory%')",
            (owner,),
        ).fetchall()
        for row in rows:
            changes: dict[str, str | None] = {}
            used = _snapshot(row["memory_used"])
            if used is not None:
                notes = [n for n in used.get("notes") or [] if isinstance(n, dict)]
                kept = [] if titles is None else [n for n in notes if n.get("id") not in titles]
                if len(kept) != len(notes):
                    used["notes"] = kept
                    used["forgotten"] = int(used.get("forgotten") or 0) + len(notes) - len(kept)
                    changes["memory_used"] = json.dumps(used)
            try:
                rounds = json.loads(row["tool_rounds"] or "[]")
            except ValueError:
                rounds = []
            touched = False
            for turn in rounds if isinstance(rounds, list) else []:
                for call in turn.get("calls") or [] if isinstance(turn, dict) else []:
                    if (
                        isinstance(call, dict)
                        and call.get("serverId") == "memory-write"
                        and (titles is None or call.get("note") in titles)
                        and call.get("note")
                    ):
                        # MV3b: a note `remember` kept and the person removed since.
                        call["result"] = REMOVED_RESULT
                        call["removed"] = True
                        call.pop("noteText", None)
                        touched = True
                        continue
                    if not isinstance(call, dict) or call.get("serverId") != "memory":
                        continue
                    result = str(call.get("result") or "")
                    if wanted is None or any(t and t in result for t in wanted):
                        call["result"] = FORGOTTEN_RESULT
                        call["content"] = []
                        touched = True
            if touched:
                changes["tool_rounds"] = json.dumps(rounds)
            if changes:
                columns = ", ".join(f"{k} = ?" for k in changes)
                db.execute(
                    f"UPDATE messages SET {columns} WHERE id = ?", (*changes.values(), row["id"])
                )

    async def memory_notes(
        self, owner: str, *, status: str | None = "active", with_body: bool = False
    ) -> list[MemoryNote]:
        """The person's notes, newest first; every status when `status` is None. A body is
        read only with `with_body`; otherwise `body` is None and `body_chars` its length."""

        def go() -> list[MemoryNote]:
            where, params = ("", []) if status is None else (" AND status = ?", [status])
            return self._notes(self._conn(), owner, where, params, with_body=with_body)

        return await self._run(go)

    async def memory_notes_by_ids(
        self, owner: str, note_ids: Sequence[str], *, with_body: bool = False
    ) -> list[MemoryNote]:
        """These of the person's notes, whatever their status, in no order. An id that is
        not theirs is not found."""
        wanted = list(dict.fromkeys(note_ids))
        if not wanted:
            return []

        def go() -> list[MemoryNote]:
            db = self._conn()
            found: list[MemoryNote] = []
            for start in range(0, len(wanted), 500):
                part = wanted[start : start + 500]
                marks = ", ".join("?" for _ in part)
                found += self._notes(
                    db, owner, f" AND id IN ({marks})", part, with_body=with_body, order=""
                )
            return found

        return await self._run(go)

    async def memory_note(
        self, owner: str, note_id: str, *, with_body: bool = True
    ) -> MemoryNote | None:
        def go() -> MemoryNote | None:
            found = self._notes(self._conn(), owner, " AND id = ?", [note_id], with_body=with_body)
            return found[0] if found else None

        return await self._run(go)

    async def note_history(self, owner: str, note_id: str) -> list[MemoryNote]:
        """The versions a note replaced, newest first, bodies and all."""

        def go() -> list[MemoryNote]:
            db = self._conn()
            chain: list[MemoryNote] = []
            seen = {note_id}
            current = note_id
            while True:
                found = self._notes(
                    db, owner, " AND superseded_by = ?", [current], with_body=True, order=""
                )
                if not found or found[0].id in seen:
                    return chain
                chain.append(found[0])
                seen.add(found[0].id)
                current = found[0].id

        return await self._run(go)

    async def memory_owners(self) -> list[str]:
        """Everyone who has a note of any kind: whose index holds notes, or may."""

        def go() -> list[str]:
            return [
                r[0]
                for r in self._conn().execute(
                    f"SELECT DISTINCT owner FROM memory_notes WHERE owner NOT IN {_HIDDEN}"
                )
            ]

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

    @staticmethod
    def _insert_note(db: sqlite3.Connection, note: MemoryNote) -> None:
        # "Newest first" must be a fact, not a coin toss: a clock that ticks in 15 ms
        # steps gives two notes made together one moment, so a note is always later than
        # the person's latest.
        latest = db.execute(
            "SELECT MAX(created_at) AS at FROM memory_notes WHERE owner = ?", (note.owner,)
        ).fetchone()["at"]
        if latest is not None and note.created_at <= latest:
            note.created_at = note.updated_at = latest + 1e-6
        if note.valid_from is None:
            note.valid_from = note.created_at
        db.execute(
            "INSERT INTO memory_notes (id, owner, text, kind, status, pinned, source, "
            "source_chat_id, source_message_id, quote, expires_at, revisit_at, "
            "created_at, updated_at, last_used_at, use_count, replaces, body, valid_from, "
            "source_connection_id, source_connection, proposed_tags, model, policy, auto, "
            "seen_at, project_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?)",
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
                note.replaces,
                note.body or "",
                note.valid_from,
                note.source_connection_id,
                note.source_connection,
                json.dumps(note.proposed_tags) if note.proposed_tags else None,
                note.model,
                note.policy,
                int(note.auto),
                note.seen_at,
                note.project_id,
            ),
        )

    async def add_memory_note(
        self, note: MemoryNote, *, limit: int, actor: str = "person"
    ) -> tuple[MemoryNote, bool]:
        """Keep a note, with its tags (`note.tags`; one not on the list is added to it):
        `(the note, True)`. The same title and body already kept are not kept twice:
        `(the one already there, False)`. Raises `MemoryFull` at `limit` notes, and
        `TagListFull`. The count, the check and the insert are one step."""

        def go() -> tuple[MemoryNote, bool]:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                wanted = note_key(note.text)
                body = note.body or ""
                # Titles first, and a body read only where the title is the same.
                twin = next(
                    (
                        r
                        for r in db.execute(
                            "SELECT id, text FROM memory_notes "
                            "WHERE owner = ? AND status = 'active' AND project_id IS ?",
                            (note.owner, note.project_id),
                        ).fetchall()
                        if note_key(r["text"]) == wanted
                        and db.execute(
                            "SELECT body FROM memory_notes WHERE id = ?", (r["id"],)
                        ).fetchone()["body"]
                        == body
                    ),
                    None,
                )
                if twin is not None:
                    found = self._notes(
                        db, note.owner, " AND id = ?", [twin["id"]], with_body=True
                    )[0]
                    db.execute("COMMIT")
                    return found, False
                total = db.execute(
                    "SELECT COUNT(*) AS n FROM memory_notes "
                    "WHERE owner = ? AND status != 'pending'",
                    (note.owner,),
                ).fetchone()["n"]
                if total >= limit:
                    raise MemoryFull(limit)
                self._insert_note(db, note)
                self._link_tags(db, note.owner, note.id, note.tags, time.time())
                self._event(db, note.owner, note.id, "add", actor, None, note.text)
                note.tags = self._tags_of(db, note.owner).get(note.id, [])
                note.body_chars = len(body)
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
        body: str | None = None,
        kind: str | None = None,
        pinned: bool | None = None,
        tags: Sequence[str] | None = None,
        actor: str = "person",
    ) -> MemoryNote | None:
        """Change a current note in place: its words, body, kind, pin or tags (a typo, a
        better word; a fact that changed is `supersede_memory_note`). None when the person
        has no such current note. The log keeps the title before and after, until the note
        goes."""

        def go() -> MemoryNote | None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM memory_notes WHERE id = ? AND owner = ? AND status = 'active'",
                    (note_id, owner),
                ).fetchone()
                if row is None:
                    db.execute("COMMIT")
                    return None
                changes: dict[str, Any] = {}
                if text is not None and text != row["text"]:
                    changes["text"] = text
                if body is not None and body != row["body"]:
                    changes["body"] = body
                if kind is not None and kind != row["kind"]:
                    changes["kind"] = kind
                if pinned is not None and int(pinned) != row["pinned"]:
                    changes["pinned"] = int(pinned)
                now = time.time()
                retagged = False
                if tags is not None:
                    before = sorted(self._tags_of(db, owner).get(note_id, []), key=tag_key)
                    if [tag_key(t) for t in before] != sorted(tag_key(t) for t in tags):
                        self._link_tags(db, owner, note_id, tags, now)
                        retagged = True
                if changes or retagged:
                    columns = ", ".join(f"{k} = ?" for k in changes)
                    db.execute(
                        f"UPDATE memory_notes SET {columns}{', ' if columns else ''}"
                        # Editing or pinning a note a model kept makes it the person's: no
                        # longer the model's to undo or remove, and seen (call 12).
                        "updated_at = ?, auto = 0 "
                        "WHERE id = ? AND owner = ?",
                        (*changes.values(), now, note_id, owner),
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
                fresh = self._notes(db, owner, " AND id = ?", [note_id], with_body=True)[0]
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return fresh

        return await self._run(go)

    @staticmethod
    def _supersede(
        db: sqlite3.Connection, owner: str, old_id: str, new_id: str, at: float, actor: str
    ) -> bool:
        """`old_id` is replaced by `new_id`: kept as history, never sent again, and taken
        from every chat's frozen copy."""
        done = db.execute(
            "UPDATE memory_notes SET status = 'superseded', superseded_at = ?, superseded_by = ?, "
            "updated_at = ? WHERE id = ? AND owner = ? AND status = 'active'",
            (at, new_id, at, old_id, owner),
        ).rowcount
        if done:
            Store._event(db, owner, old_id, "supersede", actor, None, None)
            Store._drop_from_chats(db, owner, {old_id})
        return bool(done)

    async def supersede_memory_note(
        self,
        owner: str,
        note_id: str,
        new: MemoryNote,
        *,
        limit: int | None = None,
        actor: str = "person",
    ) -> MemoryNote | None:
        """Save a changed fact as a new version: `new` is kept (its tags as `new.tags`) and
        the current note `note_id` becomes its earlier version. None when the person has no
        such current note. Earlier versions are stored notes and count: `MemoryFull` at
        `limit`."""

        def go() -> MemoryNote | None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT id FROM memory_notes WHERE id = ? AND owner = ? AND status = 'active'",
                    (note_id, owner),
                ).fetchone()
                if row is None:
                    db.execute("COMMIT")
                    return None
                if limit is not None:
                    total = db.execute(
                        "SELECT COUNT(*) AS n FROM memory_notes "
                        "WHERE owner = ? AND status != 'pending'",
                        (owner,),
                    ).fetchone()["n"]
                    if total >= limit:
                        raise MemoryFull(limit)
                now = time.time()
                new.valid_from = None
                self._insert_note(db, new)
                self._link_tags(db, owner, new.id, new.tags, now)
                self._event(db, owner, new.id, "add", actor, None, new.text)
                self._supersede(db, owner, note_id, new.id, new.created_at, actor)
                self._carry_documents(db, owner, note_id, new.id)
                fresh = self._notes(db, owner, " AND id = ?", [new.id], with_body=True)[0]
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return fresh

        return await self._run(go)

    async def delete_memory_note(self, owner: str, note_id: str) -> list[str]:
        """Forget a note, for real: its row, its index entry and its tags go, its log keeps
        no words, and no chat keeps a copy. A current note takes its earlier versions with
        it; an earlier version goes alone, and the one it replaced now replaces the one it
        followed. Returns the ids that went (none when it is not theirs)."""

        def go() -> list[str]:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT id, status, superseded_by FROM memory_notes WHERE id = ? AND owner = ?",
                    (note_id, owner),
                ).fetchone()
                if row is None:
                    db.execute("COMMIT")
                    return []
                gone = [note_id]
                if row["status"] == "superseded":
                    db.execute(
                        "UPDATE memory_notes SET superseded_by = ? "
                        "WHERE owner = ? AND superseded_by = ?",
                        (row["superseded_by"], owner, note_id),
                    )
                else:
                    current = note_id
                    while True:
                        earlier = db.execute(
                            "SELECT id FROM memory_notes WHERE owner = ? AND superseded_by = ?",
                            (owner, current),
                        ).fetchone()
                        if earlier is None or earlier["id"] in gone:
                            break
                        gone.append(earlier["id"])
                        current = earlier["id"]
                titles = {
                    r["id"]: r["text"]
                    for one in gone
                    for r in db.execute(
                        "SELECT id, text FROM memory_notes WHERE id = ? AND owner = ?", (one, owner)
                    )
                }
                for one in gone:
                    db.execute("DELETE FROM memory_notes WHERE id = ? AND owner = ?", (one, owner))
                    self._event(db, owner, one, "delete", "person", None, None)
                self._drop_from_chats(db, owner, set(gone))
                self._drop_from_answers(db, owner, titles)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            self._scrub_notes(db)
            return gone

        return await self._run(go)

    async def delete_memory_notes(self, owner: str) -> int:
        """Forget every note the person has, as `delete_memory_note` does each. Their tag
        list stays: it is a list of words, not notes."""

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
                db.execute("DELETE FROM memory_note_tags WHERE owner = ?", (owner,))
                db.execute("DELETE FROM memory_dismissed WHERE owner = ?", (owner,))
                db.execute("DELETE FROM memory_tidy WHERE owner = ?", (owner,))
                self._drop_from_chats(db, owner, None)
                self._drop_from_answers(db, owner, None)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            self._scrub_notes(db)
            return count

        return await self._run(go)

    # --- the tag list (MV1, call 14) ----------------------------------------

    async def memory_tags(self, owner: str) -> list[dict[str, Any]]:
        """The person's tags, by name: each `{tag, key, description, notes}`, where `notes`
        counts the current and pending notes carrying it."""

        def go() -> list[dict[str, Any]]:
            rows = self._conn().execute(
                "SELECT t.tag, t.key, t.description, "
                "(SELECT COUNT(*) FROM memory_note_tags AS l JOIN memory_notes AS n "
                "ON n.id = l.note_id WHERE l.owner = t.owner AND l.key = t.key "
                "AND n.status IN ('active', 'pending')) AS notes "
                "FROM memory_tags AS t WHERE t.owner = ? ORDER BY t.key",
                (owner,),
            )
            return [
                {
                    "tag": r["tag"],
                    "key": r["key"],
                    "description": r["description"],
                    "notes": int(r["notes"]),
                }
                for r in rows
            ]

        return await self._run(go)

    async def put_memory_tag(self, owner: str, tag: str, description: str | None) -> dict[str, Any]:
        """Add a tag to the list, or change the description of one on it (None leaves it).
        Raises `TagListFull`."""

        def go() -> None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                self._put_tag(db, owner, tag, description, time.time())
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

        await self._run(go)
        found = [t for t in await self.memory_tags(owner) if t["key"] == tag_key(tag)]
        return found[0]

    @staticmethod
    def _put_tag(
        db: sqlite3.Connection, owner: str, tag: str, description: str | None, now: float
    ) -> str:
        """`added`, `updated` or `same`."""
        key = tag_key(tag)
        row = db.execute(
            "SELECT description FROM memory_tags WHERE owner = ? AND key = ?", (owner, key)
        ).fetchone()
        if row is None:
            held = db.execute(
                "SELECT COUNT(*) AS n FROM memory_tags WHERE owner = ?", (owner,)
            ).fetchone()["n"]
            if int(held) >= MAX_TAGS:
                raise TagListFull(MAX_TAGS)
            db.execute(
                "INSERT INTO memory_tags (owner, key, tag, description, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (owner, key, tag, description or "", now),
            )
            return "added"
        if description is not None and description != row["description"]:
            db.execute(
                "UPDATE memory_tags SET description = ? WHERE owner = ? AND key = ?",
                (description, owner, key),
            )
            return "updated"
        return "same"

    async def rename_memory_tag(self, owner: str, tag: str, new: str) -> int | None:
        """Rename a tag; every note follows. A new name that is already on the list merges
        the two (each note carries it once, and the old description goes unless the kept
        one has none). Returns how many notes now carry the new name; None when the person
        has no such tag."""

        def go() -> int | None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                old_key, new_key = tag_key(tag), tag_key(new)
                old = db.execute(
                    "SELECT description FROM memory_tags WHERE owner = ? AND key = ?",
                    (owner, old_key),
                ).fetchone()
                if old is None:
                    db.execute("COMMIT")
                    return None
                if new_key == old_key:
                    db.execute(
                        "UPDATE memory_tags SET tag = ? WHERE owner = ? AND key = ?",
                        (new, owner, old_key),
                    )
                else:
                    kept = db.execute(
                        "SELECT description FROM memory_tags WHERE owner = ? AND key = ?",
                        (owner, new_key),
                    ).fetchone()
                    if kept is None:
                        db.execute(
                            "UPDATE memory_tags SET tag = ?, key = ? WHERE owner = ? AND key = ?",
                            (new, new_key, owner, old_key),
                        )
                    else:
                        if not kept["description"] and old["description"]:
                            db.execute(
                                "UPDATE memory_tags SET description = ? "
                                "WHERE owner = ? AND key = ?",
                                (old["description"], owner, new_key),
                            )
                        db.execute(
                            "DELETE FROM memory_tags WHERE owner = ? AND key = ?", (owner, old_key)
                        )
                    db.execute(
                        "UPDATE OR IGNORE memory_note_tags SET key = ? WHERE owner = ? AND key = ?",
                        (new_key, owner, old_key),
                    )
                    db.execute(
                        "DELETE FROM memory_note_tags WHERE owner = ? AND key = ?", (owner, old_key)
                    )
                count = db.execute(
                    "SELECT COUNT(*) AS n FROM memory_note_tags WHERE owner = ? AND key = ?",
                    (owner, new_key),
                ).fetchone()["n"]
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return int(count)

        return await self._run(go)

    async def delete_memory_tag(self, owner: str, tag: str) -> int | None:
        """Take a tag off the list and off every note (the notes stay). Returns how many
        notes lost it; None when the person has no such tag."""

        def go() -> int | None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                key = tag_key(tag)
                gone = db.execute(
                    "DELETE FROM memory_tags WHERE owner = ? AND key = ?", (owner, key)
                ).rowcount
                if not gone:
                    db.execute("COMMIT")
                    return None
                count = db.execute(
                    "SELECT COUNT(*) AS n FROM memory_note_tags AS l JOIN memory_notes AS n "
                    "ON n.id = l.note_id WHERE l.owner = ? AND l.key = ? "
                    "AND n.status IN ('active', 'pending')",
                    (owner, key),
                ).fetchone()["n"]
                db.execute("DELETE FROM memory_note_tags WHERE owner = ? AND key = ?", (owner, key))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return int(count)

        return await self._run(go)

    async def import_memory_tags(
        self, owner: str, rows: Sequence[tuple[str, str | None]], *, replace: bool = False
    ) -> dict[str, Any]:
        """Bring in a list: each `(tag, description or None)` is added, or its description
        updated (None, or an empty one, leaves the kept description). With `replace`, every
        tag not in `rows` is deleted too, from the list and from its notes. In one step;
        raises `TagListFull` and changes nothing when the list would pass the limit.
        Returns `{added, updated, removed: [{tag, notes}]}`."""

        def go() -> dict[str, Any]:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                now = time.time()
                added = updated = 0
                wanted = {tag_key(tag) for tag, _ in rows}
                removed: list[dict[str, Any]] = []
                if replace:
                    for row in db.execute(
                        "SELECT tag, key FROM memory_tags WHERE owner = ? ORDER BY key", (owner,)
                    ).fetchall():
                        if row["key"] in wanted:
                            continue
                        notes = db.execute(
                            "DELETE FROM memory_note_tags WHERE owner = ? AND key = ?",
                            (owner, row["key"]),
                        ).rowcount
                        db.execute(
                            "DELETE FROM memory_tags WHERE owner = ? AND key = ?",
                            (owner, row["key"]),
                        )
                        removed.append({"tag": row["tag"], "notes": int(notes)})
                for tag, description in rows:
                    said = self._put_tag(db, owner, tag, description or None, now)
                    added += said == "added"
                    updated += said == "updated"
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return {"added": added, "updated": updated, "removed": removed}

        return await self._run(go)

    async def tags_removed_by(self, owner: str, keep: Sequence[str]) -> list[dict[str, Any]]:
        """What Replace my list would take away, keeping only `keep`: `[{tag, notes}]`."""
        wanted = {tag_key(t) for t in keep}
        return [
            {"tag": t["tag"], "notes": t["notes"]}
            for t in await self.memory_tags(owner)
            if t["key"] not in wanted
        ]

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
                "learn_set": bool(row["learn_set"]),
                "offer_dismissed": bool(row["offer_dismissed"]),
                "revisit_asked_at": row["revisit_asked_at"],
                "relevant_budget_tokens": row["relevant_budget_tokens"],
                "search_tool": bool(row["search_tool"]),
                "chats_tool": bool(row["chats_tool"]),
                "write_policy": row["write_policy"],
                "tidy_at": row["tidy_at"],
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

    # --- MV3: which models learn, and the install's measurements ----------

    async def memory_model_choices(self, owner: str) -> dict[str, str]:
        """The person's choice per model id: `off`, `suggest` or `auto`."""

        def go() -> dict[str, str]:
            rows = (
                self._conn()
                .execute("SELECT model, mode FROM memory_model_choices WHERE owner = ?", (owner,))
                .fetchall()
            )
            return {r["model"]: r["mode"] for r in rows}

        return await self._run(go)

    async def put_memory_model_choice(self, owner: str, model: str, mode: str | None) -> None:
        """Set the person's choice for one model; None forgets it."""

        def go() -> None:
            db = self._conn()
            if mode is None:
                db.execute(
                    "DELETE FROM memory_model_choices WHERE owner = ? AND model = ?",
                    (owner, model),
                )
            else:
                db.execute(
                    "INSERT INTO memory_model_choices (owner, model, mode, set_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT (owner, model) "
                    "DO UPDATE SET mode = excluded.mode, set_at = excluded.set_at",
                    (owner, model, mode, time.time()),
                )

        await self._run(go)

    async def memory_measurements(self) -> dict[str, dict[str, Any]]:
        """The install's own measurements, by model id: when, by whom, and the result."""

        def go() -> dict[str, dict[str, Any]]:
            rows = self._conn().execute("SELECT * FROM memory_measurements").fetchall()
            out: dict[str, dict[str, Any]] = {}
            for r in rows:
                try:
                    result = json.loads(r["result"])
                except ValueError:
                    continue
                out[r["model"]] = {
                    "measuredAt": r["measured_at"],
                    "owner": r["owner"],
                    "result": result,
                }
            return out

        return await self._run(go)

    async def put_memory_measurement(
        self, model: str, owner: str | None, result: dict[str, Any]
    ) -> None:
        """Keep a measurement of a model, in place of any earlier one of the same id."""

        def go() -> None:
            self._conn().execute(
                "INSERT INTO memory_measurements (model, measured_at, owner, result) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (model) DO UPDATE SET "
                "measured_at = excluded.measured_at, owner = excluded.owner, "
                "result = excluded.result",
                (model, time.time(), owner, json.dumps(result)),
            )

        await self._run(go)

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

    # --- memory suggestions (MR8) -------------------------------------------
    #
    # A suggestion is a note with `status = 'pending'` and `source = 'suggested'`: the
    # person keeps it (it becomes `active`) or turns it away (it is deleted, and only
    # its words' stems are kept, in `memory_dismissed`). Nothing a model proposed is
    # ever active until the person says so.

    async def memory_learning(self, chat_id: str, owner: str) -> tuple[float | None, int] | None:
        """How far this chat's messages have been read for suggestions, and how many
        it has made: `(read_at, suggested)`. None when the person has no such chat."""

        def go() -> tuple[float | None, int] | None:
            row = (
                self._conn()
                .execute(
                    "SELECT memory_read_at, memory_suggested FROM chats WHERE id = ? AND owner = ?",
                    (chat_id, owner),
                )
                .fetchone()
            )
            return (row["memory_read_at"], int(row["memory_suggested"])) if row else None

        return await self._run(go)

    async def suggested_since(self, owner: str, since: float) -> int:
        """How many suggestions the person has been offered since `since`."""

        def go() -> int:
            row = (
                self._conn()
                .execute(
                    "SELECT COUNT(*) AS n FROM memory_events "
                    "WHERE owner = ? AND event IN ('suggest', 'learn') AND ts >= ?",
                    (owner, since),
                )
                .fetchone()
            )
            return int(row["n"])

        return await self._run(go)

    async def add_suggestions(
        self,
        owner: str,
        chat_id: str,
        notes: Sequence[MemoryNote],
        *,
        read_at: float,
        limit: int,
        per_chat: int | None = None,
        per_day: int | None = None,
        since: float = 0.0,
    ) -> list[MemoryNote]:
        """Offer these notes to the person, and mark the chat read up to `read_at`. A note
        with `auto` set is kept instead (MV3b: a model the person set to Keep automatically,
        its policy allowing it): active at once, with its model and policy, the listed tags it
        came with, and, when it names one, the note it replaces kept as its earlier version.
        This is the one store method every model's note goes through.

        In one step: a note whose words the person already has (kept or offered) is
        not offered twice, no more are offered than `limit` notes in all leaves room
        for, nor than `per_chat` this chat has left or `per_day` the person has left since
        `since` (counted here, so chats read at once cannot pass them together), and the
        chat counts what it offered. Returns what was offered."""

        def go() -> list[MemoryNote]:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                if (
                    db.execute(
                        "SELECT 1 FROM chats WHERE id = ? AND owner = ?", (chat_id, owner)
                    ).fetchone()
                    is None
                ):
                    db.execute("COMMIT")
                    return []
                # The same words twice is one note within one place (MV7).
                held = {
                    (r["project_id"], note_key(r["text"]))
                    for r in db.execute(
                        "SELECT text, project_id FROM memory_notes WHERE owner = ? "
                        "AND status IN ('active', 'pending')",
                        (owner,),
                    )
                }
                total = db.execute(
                    "SELECT COUNT(*) AS n FROM memory_notes WHERE owner = ?", (owner,)
                ).fetchone()["n"]
                latest = db.execute(
                    "SELECT MAX(created_at) AS at FROM memory_notes WHERE owner = ?", (owner,)
                ).fetchone()["at"]
                room = len(notes)
                if per_chat is not None:
                    made = db.execute(
                        "SELECT memory_suggested AS n FROM chats WHERE id = ?", (chat_id,)
                    ).fetchone()["n"]
                    room = min(room, per_chat - int(made))
                if per_day is not None:
                    today = db.execute(
                        "SELECT COUNT(*) AS n FROM memory_events "
                        "WHERE owner = ? AND event IN ('suggest', 'learn') AND ts >= ?",
                        (owner, since),
                    ).fetchone()["n"]
                    room = min(room, per_day - int(today))
                offered: list[MemoryNote] = []
                for note in notes:
                    key = note_key(note.text)
                    if (note.project_id, key) in held or total >= limit or len(offered) >= room:
                        continue
                    if latest is not None and note.created_at <= latest:
                        note.created_at = note.updated_at = latest + 1e-6
                    latest = note.created_at
                    auto = bool(note.auto)
                    db.execute(
                        "INSERT INTO memory_notes (id, owner, text, kind, status, pinned, source, "
                        "source_chat_id, source_message_id, quote, expires_at, revisit_at, "
                        "created_at, updated_at, replaces, proposed_tags, model, policy, auto, "
                        "valid_from, project_id) "
                        "VALUES (?, ?, ?, ?, ?, 0, 'suggested', ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                        "?, ?, ?, ?, ?)",
                        (
                            note.id,
                            owner,
                            note.text,
                            note.kind,
                            "active" if auto else "pending",
                            chat_id,
                            note.source_message_id,
                            note.quote,
                            note.expires_at,
                            note.revisit_at,
                            note.created_at,
                            note.updated_at,
                            None if auto else note.replaces,
                            None
                            if auto or not note.proposed_tags
                            else json.dumps(note.proposed_tags),
                            note.model,
                            note.policy,
                            int(auto),
                            note.created_at if auto else None,
                            note.project_id,
                        ),
                    )
                    # The words are in the note; the log says only that it was offered or kept.
                    self._event(
                        db, owner, note.id, "learn" if auto else "suggest", "model", None, None
                    )
                    if auto:
                        self._keep_learned(db, owner, note)
                    note.status = "active" if auto else "pending"
                    note.source, note.source_chat_id = "suggested", chat_id
                    held.add((note.project_id, key))
                    total += 1
                    offered.append(note)
                db.execute(
                    "UPDATE chats SET memory_read_at = MAX(COALESCE(memory_read_at, 0), ?), "
                    "memory_suggested = memory_suggested + ? WHERE id = ? AND owner = ?",
                    (read_at, len(offered), chat_id, owner),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return offered

        return await self._run(go)

    def _keep_learned(self, db: sqlite3.Connection, owner: str, note: MemoryNote) -> None:
        """What keeping a model's note adds beside its row: the tags it came with that are on
        the person's list, and the note it replaces kept as its earlier version (with its pin,
        body and tags, as a person's Keep new does)."""
        now = note.created_at
        listed = {
            r["key"] for r in db.execute("SELECT key FROM memory_tags WHERE owner = ?", (owner,))
        }
        self._link_tags(
            db, owner, note.id, [t for t in note.proposed_tags if tag_key(t) in listed], now
        )
        if not note.replaces:
            return
        old = db.execute(
            "SELECT pinned, body FROM memory_notes "
            "WHERE id = ? AND owner = ? AND status = 'active'",
            (note.replaces, owner),
        ).fetchone()
        if old is None or not self._supersede(db, owner, note.replaces, note.id, now, "model"):
            return
        db.execute(
            "UPDATE memory_notes SET pinned = ?, body = ? WHERE id = ?",
            (old["pinned"], old["body"], note.id),
        )
        carried = self._tags_of(db, owner, [note.replaces]).get(note.replaces, [])
        self._link_tags(db, owner, note.id, carried, now)

    async def learned_notes(self, owner: str, since: float) -> list[MemoryNote]:
        """Notes a model kept automatically since `since`, current ones, newest first."""

        def go() -> list[MemoryNote]:
            return self._notes(
                self._conn(),
                owner,
                " AND auto = 1 AND status = 'active' AND created_at >= ?",
                [since],
                with_body=False,
            )

        return await self._run(go)

    async def mark_learned_seen(self, owner: str, note_ids: Sequence[str]) -> int:
        """The person has seen these notes a model kept (call 12): from now on they go where
        the person's notes go."""

        def go() -> int:
            db = self._conn()
            now = time.time()
            return sum(
                db.execute(
                    "UPDATE memory_notes SET seen_at = ? "
                    "WHERE id = ? AND owner = ? AND auto = 1 AND seen_at IS NULL",
                    (now, one, owner),
                ).rowcount
                for one in note_ids
            )

        return await self._run(go)

    async def learned_since(self, owner: str, model: str | None, since: float) -> list[str]:
        """The ids of current notes `model` (any model when None) kept since `since`."""

        def go() -> list[str]:
            rows = (
                self._conn()
                .execute(
                    "SELECT id FROM memory_notes WHERE owner = ? AND auto = 1 "
                    "AND status = 'active' AND created_at >= ? AND (? IS NULL OR model = ?) "
                    "ORDER BY created_at DESC",
                    (owner, since, model, model),
                )
                .fetchall()
            )
            return [r["id"] for r in rows]

        return await self._run(go)

    async def undo_learned(
        self, owner: str, note_ids: Sequence[str]
    ) -> tuple[list[str], list[str]]:
        """Undo notes a model kept automatically: each goes, for real (as a delete), and the
        note it replaced is current again. Returns `(the ids that went, the ids made current
        again)`. A note no model kept, or not the person's, is left alone."""

        def go() -> tuple[list[str], list[str]]:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                gone: list[str] = []
                back: list[str] = []
                titles: dict[str, str] = {}
                for one in note_ids:
                    row = db.execute(
                        "SELECT id, text FROM memory_notes "
                        "WHERE id = ? AND owner = ? AND auto = 1 AND status = 'active'",
                        (one, owner),
                    ).fetchone()
                    if row is None:
                        continue
                    for earlier in db.execute(
                        "SELECT id FROM memory_notes WHERE owner = ? AND superseded_by = ? "
                        "AND status = 'superseded'",
                        (owner, one),
                    ).fetchall():
                        db.execute(
                            "UPDATE memory_notes SET status = 'active', superseded_at = NULL, "
                            "superseded_by = NULL, updated_at = ? WHERE id = ?",
                            (time.time(), earlier["id"]),
                        )
                        self._event(db, owner, earlier["id"], "restore", "person", None, None)
                        back.append(earlier["id"])
                    titles[one] = row["text"]
                    db.execute("DELETE FROM memory_notes WHERE owner = ? AND id = ?", (owner, one))
                    self._event(db, owner, one, "undo", "person", None, None)
                    gone.append(one)
                if gone:
                    self._drop_from_chats(db, owner, set(gone) | set(back))
                    self._drop_from_answers(db, owner, titles=titles)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            if gone:
                self._scrub_notes(db)
            return gone, back

        return await self._run(go)

    async def keep_suggestion(
        self,
        owner: str,
        note_id: str,
        *,
        text: str | None = None,
        kind: str | None = None,
        replace: bool = False,
        tags: Sequence[str] | None = None,
    ) -> tuple[MemoryNote, str | None] | None:
        """Keep a suggestion as a note, with the person's own edits: `(the note, the id
        of the note it replaced, if any)`. With `replace`, the note it names becomes the
        kept one's earlier version (MV1: kept as history, never sent). `tags` are the ones
        the person kept (a tag not on their list is added to it: they chose it); None
        keeps those it came with that are on the list. None when the person has no such
        suggestion."""

        def go() -> tuple[MemoryNote, str | None] | None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM memory_notes WHERE id = ? AND owner = ? AND status = 'pending'",
                    (note_id, owner),
                ).fetchone()
                if row is None:
                    db.execute("COMMIT")
                    return None
                now = time.time()
                words = text if text is not None else row["text"]
                db.execute(
                    "UPDATE memory_notes SET status = 'active', text = ?, kind = ?, "
                    "replaces = NULL, proposed_tags = NULL, valid_from = ?, updated_at = ? "
                    "WHERE id = ?",
                    (words, kind or row["kind"], now, now, note_id),
                )
                if tags is None:
                    listed = {
                        r["key"]
                        for r in db.execute("SELECT key FROM memory_tags WHERE owner = ?", (owner,))
                    }
                    kept_tags = [t for t in _tag_list(row["proposed_tags"]) if tag_key(t) in listed]
                else:
                    kept_tags = list(tags)
                self._link_tags(db, owner, note_id, kept_tags, now)
                self._event(
                    db,
                    owner,
                    note_id,
                    "keep",
                    "person",
                    row["text"] if words != row["text"] else None,
                    words if words != row["text"] else None,
                )
                replaced = None
                old = (
                    db.execute(
                        "SELECT pinned, body FROM memory_notes "
                        "WHERE id = ? AND owner = ? AND status = 'active'",
                        (row["replaces"], owner),
                    ).fetchone()
                    if replace and row["replaces"]
                    else None
                )
                if old is not None and self._supersede(
                    db, owner, row["replaces"], note_id, now, "person"
                ):
                    replaced = row["replaces"]
                    # The new version keeps what the person gave the old one: its pin, its
                    # body and its tags (MV1 section 4); only the title is the suggestion's.
                    db.execute(
                        "UPDATE memory_notes SET pinned = ?, body = ? WHERE id = ?",
                        (old["pinned"], old["body"], note_id),
                    )
                    carried = self._tags_of(db, owner, [replaced]).get(replaced, [])
                    held = self._tags_of(db, owner, [note_id]).get(note_id, [])
                    self._link_tags(db, owner, note_id, [*held, *carried], now)
                    self._carry_documents(db, owner, replaced, note_id)
                fresh = self._notes(db, owner, " AND id = ?", [note_id], with_body=True)[0]
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return fresh, replaced

        return await self._run(go)

    async def dismiss_suggestion(self, owner: str, note_id: str, words: str) -> bool:
        """Turn a suggestion away: the note goes (its log keeps no words), and `words`,
        its stems, are kept so it is never offered again. At most 500 are kept, the
        newest. False when the person has no such suggestion."""

        def go() -> bool:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                gone = db.execute(
                    "DELETE FROM memory_notes WHERE id = ? AND owner = ? AND status = 'pending'",
                    (note_id, owner),
                ).rowcount
                if gone:
                    self._event(db, owner, note_id, "dismiss", "person", None, None)
                    db.execute(
                        "INSERT INTO memory_dismissed (owner, words, at) VALUES (?, ?, ?)",
                        (owner, words, time.time()),
                    )
                    db.execute(
                        "DELETE FROM memory_dismissed WHERE owner = ? AND rowid NOT IN ("
                        "SELECT rowid FROM memory_dismissed WHERE owner = ? "
                        "ORDER BY at DESC LIMIT 500)",
                        (owner, owner),
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return bool(gone)

        return await self._run(go)

    async def expire_suggestions(self, owner: str, before: float) -> int:
        """Forget suggestions the person has left waiting since before `before`."""

        def go() -> int:
            cursor = self._conn().execute(
                "DELETE FROM memory_notes "
                "WHERE owner = ? AND status = 'pending' AND created_at < ?",
                (owner, before),
            )
            return int(cursor.rowcount)

        return await self._run(go)

    async def dismissed_words(self, owner: str) -> list[str]:
        def go() -> list[str]:
            rows = self._conn().execute(
                "SELECT words FROM memory_dismissed WHERE owner = ? ORDER BY at DESC", (owner,)
            )
            return [r["words"] for r in rows]

        return await self._run(go)

    # --- MV6: the documents a note rests on, and the tidy pass --------------

    @staticmethod
    def _carry_documents(db: sqlite3.Connection, owner: str, from_id: str, to_id: str) -> None:
        """`to_id` rests on what `from_id` did: a new version, a kept replacement, a merge.
        A document both name keeps `to_id`'s link; past `MAX_NOTE_DOCUMENTS`, the oldest
        links of `from_id` come over first and the rest do not."""
        held = db.execute(
            "SELECT COUNT(*) AS n FROM memory_note_documents WHERE owner = ? AND note_id = ?",
            (owner, to_id),
        ).fetchone()["n"]
        room = MAX_NOTE_DOCUMENTS - int(held)
        if room <= 0:
            return
        db.execute(
            "INSERT OR IGNORE INTO memory_note_documents (owner, note_id, doc_id, scope, source, "
            "name, sha256, place, how, linked_at, snoozed_until, snoozed_sha) "
            "SELECT owner, ?, doc_id, scope, source, name, sha256, place, how, linked_at, "
            "snoozed_until, snoozed_sha FROM memory_note_documents WHERE owner = ? AND note_id = ? "
            "AND doc_id NOT IN (SELECT doc_id FROM memory_note_documents WHERE note_id = ?) "
            "ORDER BY linked_at, doc_id LIMIT ?",
            (to_id, owner, from_id, to_id, room),
        )

    async def note_documents(
        self, owner: str, note_ids: Sequence[str] | None = None, *, current: bool = False
    ) -> dict[str, list[dict[str, Any]]]:
        """The documents each note rests on, as they were when linked: every note of the
        person's that has any when `note_ids` is None; with `current`, only current notes'."""

        def go() -> dict[str, list[dict[str, Any]]]:
            db = self._conn()
            where, params = " WHERE owner = ?", [owner]
            if current:
                where += (
                    " AND note_id IN (SELECT id FROM memory_notes WHERE owner = ? "
                    "AND status = 'active')"
                )
                params.append(owner)
            if note_ids is not None:
                wanted = list(dict.fromkeys(note_ids))
                if not wanted:
                    return {}
                where += f" AND note_id IN ({', '.join('?' for _ in wanted)})"
                params += wanted
            found: dict[str, list[dict[str, Any]]] = {}
            for r in db.execute(
                f"SELECT * FROM memory_note_documents{where} ORDER BY linked_at, doc_id", params
            ):
                try:
                    place = json.loads(r["place"])
                except ValueError:
                    place = []
                found.setdefault(r["note_id"], []).append(
                    {
                        "doc_id": r["doc_id"],
                        "scope": r["scope"],
                        "source": r["source"],
                        "name": r["name"],
                        "sha256": r["sha256"],
                        "place": place if isinstance(place, list) else [],
                        "how": r["how"],
                        "linked_at": r["linked_at"],
                        "snoozed_until": r["snoozed_until"],
                        "snoozed_sha": r["snoozed_sha"],
                    }
                )
            return found

        return await self._run(go)

    async def link_note_documents(
        self, owner: str, note_id: str, links: Sequence[dict[str, Any]], how: str, limit: int
    ) -> int:
        """Name documents a current note of the person's rests on, each as it is now
        (`doc_id`, `scope`, `source`, `name`, `sha256`, `place`), at most `limit` a note; a
        document it already names is linked again as it is now. How many it names now; -1
        when it is not theirs or not current."""

        def go() -> int:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT 1 FROM memory_notes WHERE id = ? AND owner = ? AND status = 'active'",
                    (note_id, owner),
                ).fetchone()
                if row is None:
                    db.execute("COMMIT")
                    return -1
                now = time.time()
                for link in links:
                    held = db.execute(
                        "SELECT COUNT(*) AS n FROM memory_note_documents WHERE note_id = ? "
                        "AND doc_id != ?",
                        (note_id, link["doc_id"]),
                    ).fetchone()["n"]
                    if int(held) >= limit:
                        break
                    db.execute(
                        "INSERT INTO memory_note_documents (owner, note_id, doc_id, "
                        "scope, source, name, sha256, place, how, linked_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT (note_id, doc_id) DO UPDATE SET scope = excluded.scope, "
                        "source = excluded.source, name = excluded.name, "
                        "sha256 = excluded.sha256, snoozed_until = NULL, snoozed_sha = NULL",
                        (
                            owner,
                            note_id,
                            link["doc_id"],
                            link["scope"],
                            link["source"],
                            link["name"],
                            link["sha256"],
                            json.dumps(link.get("place") or []),
                            how,
                            now,
                        ),
                    )
                count = db.execute(
                    "SELECT COUNT(*) AS n FROM memory_note_documents WHERE note_id = ?",
                    (note_id,),
                ).fetchone()["n"]
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return int(count)

        return await self._run(go)

    async def unlink_note_document(self, owner: str, note_id: str, doc_id: str) -> bool:
        def go() -> bool:
            db = self._conn()
            return bool(
                db.execute(
                    "DELETE FROM memory_note_documents WHERE owner = ? AND note_id = ? "
                    "AND doc_id = ?",
                    (owner, note_id, doc_id),
                ).rowcount
            )

        return await self._run(go)

    async def settle_note_documents(
        self, owner: str, note_id: str, moved: dict[str, dict[str, Any] | None]
    ) -> None:
        """The person said a note is still true of its documents as they are now: each link
        in `moved` (by the doc id it holds) moves to the document it names, or goes when that
        is None (the document was removed), with a line in the note's history."""

        def go() -> None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                for old_id, now_doc in moved.items():
                    row = db.execute(
                        "SELECT name FROM memory_note_documents WHERE owner = ? AND note_id = ? "
                        "AND doc_id = ?",
                        (owner, note_id, old_id),
                    ).fetchone()
                    if row is None:
                        continue
                    if now_doc is None:
                        db.execute(
                            "DELETE FROM memory_note_documents WHERE owner = ? AND note_id = ? "
                            "AND doc_id = ?",
                            (owner, note_id, old_id),
                        )
                        self._event(
                            db,
                            owner,
                            note_id,
                            "unlink",
                            "person",
                            f"'{row['name']}' was removed; you said this is still true.",
                            None,
                        )
                        continue
                    if (
                        now_doc["doc_id"] != old_id
                        and db.execute(
                            "SELECT 1 FROM memory_note_documents WHERE note_id = ? AND doc_id = ?",
                            (note_id, now_doc["doc_id"]),
                        ).fetchone()
                    ):
                        # The new version is linked already, as it is now: one link is enough.
                        db.execute(
                            "DELETE FROM memory_note_documents WHERE owner = ? AND note_id = ? "
                            "AND doc_id = ?",
                            (owner, note_id, old_id),
                        )
                        continue
                    db.execute(
                        "UPDATE memory_note_documents SET doc_id = ?, scope = ?, source = ?, "
                        "name = ?, sha256 = ?, place = '[]', snoozed_until = NULL, "
                        "snoozed_sha = NULL "
                        "WHERE owner = ? AND note_id = ? AND doc_id = ?",
                        (
                            now_doc["doc_id"],
                            now_doc["scope"],
                            now_doc["source"],
                            now_doc["name"],
                            now_doc["sha256"],
                            owner,
                            note_id,
                            old_id,
                        ),
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

        await self._run(go)

    async def snooze_note_documents(
        self, owner: str, note_id: str, seen: dict[str, str], until: float
    ) -> int:
        """Put these links off until `until`, each while its document stays as it is now:
        `seen` maps the doc id a link holds to the `sha256` of the document now ('' when it
        was removed), so a second change asks again at once."""

        def go() -> int:
            db = self._conn()
            done = 0
            for doc_id, sha in seen.items():
                done += db.execute(
                    "UPDATE memory_note_documents SET snoozed_until = ?, snoozed_sha = ? "
                    "WHERE owner = ? AND note_id = ? AND doc_id = ?",
                    (until, sha, owner, note_id, doc_id),
                ).rowcount
            return done

        return await self._run(go)

    async def tidy_rows(self, owner: str) -> list[dict[str, Any]]:
        """Every proposal of the tidy pass the person has, answered or not, oldest first."""

        def go() -> list[dict[str, Any]]:
            rows = (
                self._conn()
                .execute(
                    "SELECT * FROM memory_tidy WHERE owner = ? ORDER BY created_at, id", (owner,)
                )
                .fetchall()
            )
            return [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "note_ids": json.loads(r["pair"]),
                    "created_at": r["created_at"],
                    "settled": r["settled"],
                    "settled_at": r["settled_at"],
                }
                for r in rows
            ]

        return await self._run(go)

    async def add_tidy(self, owner: str, kind: str, note_ids: Sequence[str]) -> bool:
        """Propose this, unless the same was proposed before (answered or not)."""
        pair = json.dumps(sorted(note_ids))

        def go() -> bool:
            return bool(
                self._conn()
                .execute(
                    "INSERT OR IGNORE INTO memory_tidy (id, owner, kind, pair, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (secrets.token_hex(8), owner, kind, pair, time.time()),
                )
                .rowcount
            )

        return await self._run(go)

    async def settle_tidy(self, owner: str, tidy_id: str, settled: str) -> bool:
        def go() -> bool:
            return bool(
                self._conn()
                .execute(
                    "UPDATE memory_tidy SET settled = ?, settled_at = ? "
                    "WHERE owner = ? AND id = ? AND settled IS NULL",
                    (settled, time.time(), owner, tidy_id),
                )
                .rowcount
            )

        return await self._run(go)

    async def drop_tidy(self, owner: str, tidy_ids: Sequence[str]) -> None:
        def go() -> None:
            db = self._conn()
            for one in tidy_ids:
                db.execute("DELETE FROM memory_tidy WHERE owner = ? AND id = ?", (owner, one))

        await self._run(go)

    async def merge_memory_notes(
        self, owner: str, keep_id: str, other_id: str
    ) -> MemoryNote | None:
        """Two current notes said the same thing and the person kept `keep_id`: the other
        becomes the earliest of its versions (so a history stays one line, and deleting the
        kept note takes it too), never sent again. The kept note gains the other's tags and
        documents, its pin, and its body when it had none. None when either is not a
        current note of theirs."""

        def go() -> MemoryNote | None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                rows = {
                    r["id"]: r
                    for r in db.execute(
                        "SELECT id, text, pinned, body FROM memory_notes WHERE owner = ? "
                        "AND status = 'active' AND id IN (?, ?)",
                        (owner, keep_id, other_id),
                    )
                }
                if keep_id == other_id or set(rows) != {keep_id, other_id}:
                    db.execute("COMMIT")
                    return None
                keep, other = rows[keep_id], rows[other_id]
                now = time.time()
                # The kept note's earliest version: the other goes below it.
                tail, seen = keep_id, {keep_id}
                while True:
                    earlier = db.execute(
                        "SELECT id FROM memory_notes WHERE owner = ? AND superseded_by = ?",
                        (owner, tail),
                    ).fetchone()
                    if earlier is None or earlier["id"] in seen:
                        break
                    tail = earlier["id"]
                    seen.add(tail)
                tags = [
                    *self._tags_of(db, owner, [keep_id]).get(keep_id, []),
                    *self._tags_of(db, owner, [other_id]).get(other_id, []),
                ]
                self._link_tags(db, owner, keep_id, list(dict.fromkeys(tags)), now)
                self._carry_documents(db, owner, other_id, keep_id)
                db.execute(
                    "UPDATE memory_notes SET pinned = ?, body = ?, updated_at = ? WHERE id = ?",
                    (
                        1 if keep["pinned"] or other["pinned"] else 0,
                        keep["body"] or other["body"] or "",
                        now,
                        keep_id,
                    ),
                )
                db.execute(
                    "UPDATE memory_notes SET status = 'superseded', superseded_at = ?, "
                    "superseded_by = ?, updated_at = ? WHERE id = ? AND owner = ?",
                    (now, tail, now, other_id, owner),
                )
                self._event(db, owner, other_id, "merge", "person", None, None)
                self._event(db, owner, keep_id, "merge", "person", None, None)
                self._drop_from_chats(db, owner, {other_id})
                fresh = self._notes(db, owner, " AND id = ?", [keep_id], with_body=True)[0]
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return fresh

        return await self._run(go)

    async def set_revisit(self, owner: str, note_id: str, revisit_at: str | None) -> bool:
        """When a project is next asked about ("Still true?"). False when it is not theirs."""

        def go() -> bool:
            cursor = self._conn().execute(
                "UPDATE memory_notes SET revisit_at = ? WHERE id = ? AND owner = ?",
                (revisit_at, note_id, owner),
            )
            return cursor.rowcount == 1

        return await self._run(go)

    # --- connections (MR9b) -------------------------------------------------
    #
    # A connection is found by its token's hash and nothing else; every other read and
    # write names its owner, so a person's id never reaches another person's connection.

    async def add_connection(
        self, record: ConnectionRecord, token_hash: str, *, limit: int
    ) -> ConnectionRecord | None:
        """Keep a new connection, or None when the person already has `limit`."""

        def go() -> ConnectionRecord | None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                held = db.execute(
                    "SELECT COUNT(*) AS n FROM connections WHERE owner = ?", (record.owner,)
                ).fetchone()["n"]
                if int(held) >= limit:
                    db.execute("COMMIT")
                    return None
                db.execute(
                    "INSERT INTO connections (id, owner, name, token_hash, read_notes, "
                    "search_documents, suggest_notes, created_at, expires_at, search_chats) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.id,
                        record.owner,
                        record.name,
                        token_hash,
                        int(record.read_notes),
                        int(record.search_documents),
                        int(record.suggest_notes),
                        record.created_at,
                        record.expires_at,
                        int(record.search_chats),
                    ),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return record

        return await self._run(go)

    async def connections(self, owner: str) -> list[ConnectionRecord]:
        def go() -> list[ConnectionRecord]:
            rows = self._conn().execute(
                "SELECT * FROM connections WHERE owner = ? ORDER BY created_at DESC, id",
                (owner,),
            )
            return [_connection(r) for r in rows]

        return await self._run(go)

    async def connection_by_token(self, token_hash: str) -> ConnectionRecord | None:
        """The connection whose token hashes to this, whoever holds it."""

        def go() -> ConnectionRecord | None:
            row = (
                self._conn()
                .execute(
                    f"SELECT * FROM connections WHERE token_hash = ? AND owner NOT IN {_HIDDEN}",
                    (token_hash,),
                )
                .fetchone()
            )
            return _connection(row) if row is not None else None

        return await self._run(go)

    async def connection_used(self, connection_id: str, at: float, day: str) -> None:
        """Count one call: `last_used_at`, and today's calls (a new day starts at one)."""

        def go() -> None:
            self._conn().execute(
                "UPDATE connections SET last_used_at = ?, "
                "calls_today = CASE WHEN calls_day = ? THEN calls_today + 1 ELSE 1 END, "
                "calls_day = ? WHERE id = ?",
                (at, day, day, connection_id),
            )

        await self._run(go)

    async def delete_connection(self, owner: str, connection_id: str) -> bool:
        """Revoke: the connection goes, with the suggestions of its the person has not kept.
        False when the person has no such connection."""

        def go() -> bool:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                gone = db.execute(
                    "DELETE FROM connections WHERE id = ? AND owner = ?", (connection_id, owner)
                ).rowcount
                if gone:
                    db.execute(
                        "DELETE FROM memory_notes WHERE owner = ? AND status = 'pending' "
                        "AND source_connection_id = ?",
                        (owner, connection_id),
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return bool(gone)

        return await self._run(go)

    async def add_connection_suggestion(
        self,
        owner: str,
        connection: ConnectionRecord,
        note: MemoryNote,
        *,
        limit: int,
        per_day: int,
        since: float,
    ) -> str | None:
        """Offer one note a connection suggested; None when it is offered, else why not:
        `revoked` (the connection was revoked or ran out since its call was let in),
        `duplicate` (the person has those words, kept or offered), `full` (no room under
        `limit`) or `daily` (the person's `per_day` suggestions since `since` are used up,
        counted here with every chat's, so neither can pass the cap)."""

        def go() -> str | None:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                # Checked in the write, so a call let in before Revoke leaves nothing behind.
                live = db.execute(
                    "SELECT 1 FROM connections WHERE id = ? AND owner = ? AND expires_at > ?",
                    (connection.id, owner, time.time()),
                ).fetchone()
                if live is None:
                    db.execute("COMMIT")
                    return "revoked"
                # A connection's note is general (MV7-C4): only general notes are its twins,
                # so its answer never says a project holds the words.
                held = {
                    note_key(r["text"])
                    for r in db.execute(
                        "SELECT text FROM memory_notes WHERE owner = ? "
                        "AND status IN ('active', 'pending') AND project_id IS NULL",
                        (owner,),
                    )
                }
                total = db.execute(
                    "SELECT COUNT(*) AS n FROM memory_notes WHERE owner = ?", (owner,)
                ).fetchone()["n"]
                today = db.execute(
                    "SELECT COUNT(*) AS n FROM memory_events "
                    "WHERE owner = ? AND event = 'suggest' AND ts >= ?",
                    (owner, since),
                ).fetchone()["n"]
                why = (
                    "duplicate"
                    if note_key(note.text) in held
                    else "full"
                    if int(total) >= limit
                    else "daily"
                    if int(today) >= per_day
                    else None
                )
                if why is None:
                    latest = db.execute(
                        "SELECT MAX(created_at) AS at FROM memory_notes WHERE owner = ?", (owner,)
                    ).fetchone()["at"]
                    if latest is not None and note.created_at <= latest:
                        note.created_at = note.updated_at = latest + 1e-6
                    db.execute(
                        "INSERT INTO memory_notes (id, owner, text, kind, status, pinned, source, "
                        "quote, created_at, updated_at, source_connection_id, source_connection, "
                        "proposed_tags) "
                        "VALUES (?, ?, ?, ?, 'pending', 0, 'suggested', NULL, ?, ?, ?, ?, ?)",
                        (
                            note.id,
                            owner,
                            note.text,
                            note.kind,
                            note.created_at,
                            note.updated_at,
                            connection.id,
                            connection.name,
                            json.dumps(note.proposed_tags) if note.proposed_tags else None,
                        ),
                    )
                    # The words are in the note; the log says only that it was offered.
                    self._event(db, owner, note.id, "suggest", "connection", None, None)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return why

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
        """Every folder, or one person's, oldest first; never a removed person's (row 7)."""

        def go() -> list[FolderRecord]:
            where, params = ("", ()) if owner is None else (" AND owner = ?", (owner,))
            rows = self._conn().execute(
                f"SELECT * FROM document_folders WHERE owner NOT IN {_HIDDEN}{where} "
                "ORDER BY added_at, rowid",
                params,
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

    async def project_owners(self, project_ids: Iterable[str]) -> dict[str, str]:
        """Whose each of these projects is, for the ones that exist: what the boot reconcile
        asks to find a project's documents whose project is gone (MV7b)."""
        ids = list(project_ids)
        if not ids:
            return {}

        def go() -> dict[str, str]:
            found: dict[str, str] = {}
            for start in range(0, len(ids), 500):
                part = ids[start : start + 500]
                marks = ", ".join("?" for _ in part)
                for row in self._conn().execute(
                    f"SELECT id, owner FROM projects WHERE id IN ({marks})", part
                ):
                    found[row["id"]] = row["owner"]
            return found

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

    # --- memory servers (MV5) -------------------------------------------

    async def memory_server(self, owner: str) -> dict[str, Any] | None:
        """A person's memory server, with its verbs decoded, or None."""

        def go() -> dict[str, Any] | None:
            row = (
                self._conn()
                .execute("SELECT * FROM memory_servers WHERE owner = ?", (owner,))
                .fetchone()
            )
            if row is None:
                return None
            server = dict(row)
            server["verbs"] = json.loads(server["verbs"] or "{}")
            return server

        return await self._run(go)

    async def put_memory_server(
        self, owner: str, url: str, token: str, name: str, verbs: dict[str, str]
    ) -> None:
        """Connect a person's server, replacing any they had. At another address, what was
        recorded of the old one goes (it can no longer be asked to forget anything); at the same
        address (a new token), what it is still to forget stays."""

        def go() -> None:
            db = self._conn()
            db.execute("BEGIN")
            try:
                old = db.execute(
                    "SELECT url FROM memory_servers WHERE owner = ?", (owner,)
                ).fetchone()
                if old is None or old["url"] != url:
                    db.execute("DELETE FROM memory_server_sent WHERE owner = ?", (owner,))
                db.execute(
                    "INSERT OR REPLACE INTO memory_servers (owner, url, token, name, verbs, "
                    "connected_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (owner, url, token, name, json.dumps(verbs), time.time()),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

        await self._run(go)

    async def delete_memory_server(self, owner: str) -> None:
        def go() -> None:
            db = self._conn()
            db.execute("BEGIN")
            try:
                db.execute("DELETE FROM memory_server_sent WHERE owner = ?", (owner,))
                db.execute("DELETE FROM memory_servers WHERE owner = ?", (owner,))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

        await self._run(go)

    async def memory_server_reached(self, owner: str, error: str | None) -> None:
        """Record what the last call to a person's server came to: reached, or the reason."""

        def go() -> None:
            if error is None:
                self._conn().execute(
                    "UPDATE memory_servers SET last_ok_at = ?, last_error = NULL, "
                    "last_error_at = NULL WHERE owner = ?",
                    (time.time(), owner),
                )
            else:
                self._conn().execute(
                    "UPDATE memory_servers SET last_error = ?, last_error_at = ? WHERE owner = ?",
                    (error[:300], time.time(), owner),
                )

        await self._run(go)

    async def memory_server_skipped(self, owner: str) -> None:
        """Count one turn not sent because it looked like a secret."""
        await self._run(
            lambda: self._conn().execute(
                "UPDATE memory_servers SET skipped = skipped + 1 WHERE owner = ?", (owner,)
            )
        )

    async def queue_memory_server_send(self, owner: str, chat_id: str, message_id: str) -> bool:
        """Queue an answered exchange for the person's server, when they have one. Whether
        it was queued."""

        def go() -> bool:
            now = time.time()
            cursor = self._conn().execute(
                "INSERT OR IGNORE INTO memory_server_sent (document_id, owner, chat_id, "
                "message_id, state, next_at, created_at) SELECT ?, ?, ?, ?, 'send', ?, ? "
                "WHERE EXISTS (SELECT 1 FROM memory_servers WHERE owner = ?)",
                (f"{chat_id}:{message_id}", owner, chat_id, message_id, now, now, owner),
            )
            return cursor.rowcount > 0

        return await self._run(go)

    async def memory_server_due(self, now: float, limit: int = 20) -> list[dict[str, Any]]:
        """Exchanges waiting to be sent or forgotten, oldest first."""

        def go() -> list[dict[str, Any]]:
            return [
                dict(row)
                # A removed person's are the purge's to forget (`removal.py`), not the courier's.
                for row in self._conn().execute(
                    "SELECT * FROM memory_server_sent WHERE state IN ('send','forget') "
                    f"AND next_at <= ? AND owner NOT IN {_HIDDEN} ORDER BY next_at LIMIT ?",
                    (now, limit),
                )
            ]

        return await self._run(go)

    async def memory_server_settled(self, document_id: str, state: str) -> bool:
        """A send or forget the server confirmed: a sent exchange is kept as `sent`, a
        forgotten one goes. False when the row had gone meanwhile (its chat was deleted while
        it was being sent)."""

        def go() -> bool:
            db = self._conn()
            if state == "forget":
                cursor = db.execute(
                    "DELETE FROM memory_server_sent WHERE document_id = ? AND state = 'forget'",
                    (document_id,),
                )
            else:
                cursor = db.execute(
                    "UPDATE memory_server_sent SET state = 'sent', attempts = 0 "
                    "WHERE document_id = ? AND state = 'send'",
                    (document_id,),
                )
            return cursor.rowcount > 0

        return await self._run(go)

    async def memory_server_forget_later(self, row: dict[str, Any]) -> None:
        """An exchange sent while its chat was being deleted: to be forgotten, from now."""
        now = time.time()
        await self._run(
            lambda: self._conn().execute(
                "INSERT OR REPLACE INTO memory_server_sent (document_id, owner, chat_id, "
                "message_id, state, attempts, next_at, created_at) "
                "VALUES (?, ?, ?, ?, 'forget', 0, ?, ?)",
                (row["document_id"], row["owner"], row["chat_id"], row["message_id"], now, now),
            )
        )

    async def memory_server_unsent(self, owner: str) -> None:
        """Delete all memory: what waits to be sent is not sent."""
        await self._run(
            lambda: self._conn().execute(
                "DELETE FROM memory_server_sent WHERE owner = ? AND state = 'send'", (owner,)
            )
        )

    async def memory_server_retry(self, document_id: str, next_at: float) -> bool:
        """Try this exchange again at `next_at`. False when its row had gone meanwhile (its
        chat was deleted, or its person removed, while it was being sent)."""
        cursor = await self._run(
            lambda: self._conn().execute(
                "UPDATE memory_server_sent SET attempts = attempts + 1, next_at = ? "
                "WHERE document_id = ?",
                (next_at, document_id),
            )
        )
        return bool(cursor.rowcount > 0)

    async def memory_server_drop(self, document_id: str) -> None:
        await self._run(
            lambda: self._conn().execute(
                "DELETE FROM memory_server_sent WHERE document_id = ?", (document_id,)
            )
        )

    async def memory_server_counts(self, owner: str) -> dict[str, int]:
        """How many exchanges are sent, waiting to be sent, and waiting to be forgotten."""

        def go() -> dict[str, int]:
            counts = {"send": 0, "sent": 0, "forget": 0}
            for row in self._conn().execute(
                "SELECT state, COUNT(*) AS n FROM memory_server_sent WHERE owner = ? "
                "GROUP BY state",
                (owner,),
            ):
                counts[row["state"]] = row["n"]
            return counts

        return await self._run(go)

    async def memory_server_cleared(self, owner: str) -> None:
        """After the server was cleared (Delete all memory): nothing of it is recorded."""
        await self._run(
            lambda: self._conn().execute("DELETE FROM memory_server_sent WHERE owner = ?", (owner,))
        )

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
        await self.interactions.sweep()

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

    # --- removed people (row 7) -----------------------------------------

    async def people_to_ask(self) -> list[dict[str, Any]]:
        """Whom to ask Eugene about: everyone who signed in here, but the owner (who is never
        removed) and those already removed. Each with `sub`, `name` and `known_at`."""

        def go() -> list[dict[str, Any]]:
            rows = self._conn().execute(
                "SELECT sub, name, known_at FROM people WHERE role != 'operator' "
                "AND sub != 'operator' AND sub NOT IN "
                "(SELECT sub FROM removed_people WHERE state != 'purged') "
                "ORDER BY first_seen, sub"
            )
            return [dict(r) for r in rows]

        return await self._run(go)

    async def confirm_known(self, subs: Sequence[str], at: float) -> None:
        """Eugene said it has these people, now."""
        if not subs:
            return

        def go() -> None:
            marks = ", ".join("?" for _ in subs)
            self._conn().execute(
                f"UPDATE people SET known_at = ? WHERE sub IN ({marks})", (at, *subs)
            )

        await self._run(go)

    async def back_from_removed(self, subs: Sequence[str]) -> list[str]:
        """Eugene has these people again (a root restored from a backup): their data is theirs
        again. A purge that began finishes, and a purged person's record stays. Which came
        back."""
        if not subs:
            return []

        def go() -> list[str]:
            db = self._conn()
            marks = ", ".join("?" for _ in subs)
            db.execute("BEGIN IMMEDIATE")
            try:
                back = [
                    r[0]
                    for r in db.execute(
                        f"SELECT sub FROM removed_people WHERE sub IN ({marks}) "
                        "AND state IN ('held', 'hidden', 'kept')",
                        tuple(subs),
                    )
                ]
                if back:
                    db.execute(
                        f"DELETE FROM removed_people WHERE sub IN ({', '.join('?' for _ in back)})",
                        tuple(back),
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return back

        return await self._run(go)

    async def hide_people(
        self, hidden: Sequence[tuple[str, str, str, float | None, float | None]]
    ) -> list[str]:
        """Hide each `(sub, name, state, clock_from, delete_at)`, in one transaction: their row in
        `removed_people`, and their sign-ins, their connections to other apps and what waited
        to go to their memory server, gone. Their data stays until it is deleted. Which were
        hidden now (a person already removed is left as they are)."""
        if not hidden:
            return []

        def go() -> list[str]:
            db = self._conn()
            now = time.time()
            done: list[str] = []
            db.execute("BEGIN IMMEDIATE")
            try:
                for sub, name, state, clock_from, delete_at in hidden:
                    # A person purged before, whom a restored Eugene brought back and who was
                    # removed again, is hidden again; anyone else already here is left alone.
                    cursor = db.execute(
                        "INSERT INTO removed_people (sub, name, email, state, found_at, "
                        "clock_from, delete_at) VALUES (?, ?, "
                        "(SELECT email FROM people WHERE sub = ?), ?, ?, ?, ?) "
                        "ON CONFLICT(sub) DO UPDATE SET "
                        "name = excluded.name, email = excluded.email, state = excluded.state, "
                        "found_at = excluded.found_at, clock_from = excluded.clock_from, "
                        "delete_at = excluded.delete_at, steps = '{}', attempts = 0, "
                        "last_error = NULL, purged_at = NULL "
                        "WHERE removed_people.state = 'purged'",
                        (sub, name, sub, state, now, clock_from, delete_at),
                    )
                    if cursor.rowcount <= 0:
                        continue
                    done.append(sub)
                    db.execute("DELETE FROM sessions WHERE sub = ?", (sub,))
                    db.execute("DELETE FROM connections WHERE owner = ?", (sub,))
                    db.execute(
                        "DELETE FROM memory_server_sent WHERE owner = ? AND state = 'send'", (sub,)
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return done

        return await self._run(go)

    async def removed_people(self) -> list[dict[str, Any]]:
        """Everyone removed from Eugene that Workbench knows of, newest first."""

        def go() -> list[dict[str, Any]]:
            rows = self._conn().execute(
                "SELECT * FROM removed_people ORDER BY found_at DESC, name COLLATE NOCASE"
            )
            return [{**dict(r), "steps": json.loads(r["steps"] or "{}")} for r in rows]

        return await self._run(go)

    async def removed_person(self, sub: str) -> dict[str, Any] | None:
        def go() -> dict[str, Any] | None:
            r = (
                self._conn()
                .execute("SELECT * FROM removed_people WHERE sub = ?", (sub,))
                .fetchone()
            )
            return {**dict(r), "steps": json.loads(r["steps"] or "{}")} if r is not None else None

        return await self._run(go)

    async def hidden_subs(self) -> list[str]:
        """Whose data is hidden now: removed, and not yet deleted."""
        return await self._run(lambda: [r[0] for r in self._conn().execute(_HIDDEN[1:-1])])

    async def put_removed(self, sub: str, *, only_from: Sequence[str] = (), **values: Any) -> bool:
        """Change a removed person's row: `state`, `clock_from`, `steps` (a dict), `attempts`,
        `last_error`, `purged_at`, `email`. With `only_from`, only while its state is one of
        those. Whether it changed."""
        allowed = {
            "state",
            "clock_from",
            "delete_at",
            "steps",
            "attempts",
            "last_error",
            "purged_at",
            "email",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"not removal fields: {sorted(unknown)}")
        if "steps" in values:
            values["steps"] = json.dumps(values["steps"])

        def go() -> bool:
            columns = ", ".join(f"{k} = ?" for k in values)
            where, params = "sub = ?", [sub]
            if only_from:
                where += f" AND state IN ({', '.join('?' for _ in only_from)})"
                params += list(only_from)
            cursor = self._conn().execute(
                f"UPDATE removed_people SET {columns} WHERE {where}", (*values.values(), *params)
            )
            return cursor.rowcount > 0

        return await self._run(go)

    async def person_chat_ids(self, sub: str) -> list[str]:
        """Every chat of the person's, removed or not: what to stop when they are hidden."""
        return await self._run(
            lambda: [
                r[0] for r in self._conn().execute("SELECT id FROM chats WHERE owner = ?", (sub,))
            ]
        )

    async def forgets_owed(self, sub: str) -> list[dict[str, Any]]:
        """What Workbench sent the person's memory server and has not had it forget yet."""
        return await self._run(
            lambda: [
                dict(r)
                for r in self._conn().execute(
                    "SELECT * FROM memory_server_sent WHERE owner = ? AND state IN ('sent', "
                    "'forget') ORDER BY created_at",
                    (sub,),
                )
            ]
        )

    async def purge_person(self, sub: str) -> dict[str, int]:
        """Delete every row of the person's in one transaction, then write the file again so no
        free page, full-text segment or log holds their words (`_rewrite`). How many rows went
        from each table. While it runs, nothing else reads or writes the store (one thread)."""

        def go() -> dict[str, int]:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                gone = _purge_rows(db, sub)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            _rewrite(db)
            return gone

        return await self._run(go)

    async def purge_copies(self, sub: str) -> list[Path]:
        """The purge, on every copy kept beside the store before a migration. The copies stay,
        for everyone else in them. A half-written copy (`.bak.part`, left by a stop) is removed.
        The copies purged."""

        def go() -> list[Path]:
            self._path.with_name(f"{self._path.name}.part").unlink(missing_ok=True)
            for partial in self._path.parent.glob(f"{self._path.name}.schema-*.bak.part"):
                partial.unlink(missing_ok=True)
            copies = _all_copies(self._path)
            for copy in copies:
                _purge_copy(copy, sub)
            return copies

        return await self._run(go)

    async def export_snapshot(self, sub: str) -> dict[str, Any]:
        """Everything of the person's the store holds that a download carries, whether or not
        they are removed: their chats with every version of every message, their files, media,
        artifacts with every revision, projects and connections (never a token's hash)."""

        def go() -> dict[str, Any]:
            db = self._conn()
            chats = [
                _chat(r)
                for r in db.execute(
                    "SELECT * FROM chats WHERE owner = ? ORDER BY created_at", (sub,)
                )
            ]
            trees = {
                c.id: [
                    _message(r)
                    for r in db.execute(
                        "SELECT * FROM messages WHERE chat_id = ? ORDER BY seq", (c.id,)
                    )
                ]
                for c in chats
            }
            records = [
                _file(r)
                for r in db.execute(
                    "SELECT * FROM files WHERE owner = ? ORDER BY created_at", (sub,)
                )
            ]
            media = [
                _media(r)
                for r in db.execute(
                    "SELECT * FROM media WHERE owner = ? ORDER BY created_at", (sub,)
                )
            ]
            artifacts = []
            for r in db.execute(
                f"{_ARTIFACT_SELECT} WHERE a.owner = ? GROUP BY a.id ORDER BY a.created_at", (sub,)
            ):
                artifact = _artifact(r)
                revisions = [
                    dict(x)
                    for x in db.execute(
                        "SELECT number, source, author, created_at FROM artifact_revisions "
                        "WHERE artifact_id = ? ORDER BY number",
                        (artifact.id,),
                    )
                ]
                artifacts.append((artifact, revisions))
            projects = [
                self._project(r)
                for r in db.execute("SELECT * FROM projects WHERE owner = ? ORDER BY name", (sub,))
            ]
            connections = [
                {
                    "name": r["name"],
                    "readsNotes": bool(r["read_notes"]),
                    "searchesDocuments": bool(r["search_documents"]),
                    "suggestsNotes": bool(r["suggest_notes"]),
                    "createdAt": r["created_at"],
                    "expiresAt": r["expires_at"],
                    "lastUsedAt": r["last_used_at"],
                }
                for r in db.execute(
                    "SELECT * FROM connections WHERE owner = ? ORDER BY created_at", (sub,)
                )
            ]
            return {
                "chats": chats,
                "trees": trees,
                "files": records,
                "media": media,
                "artifacts": artifacts,
                "projects": projects,
                "connections": connections,
            }

        return await self._run(go)

    # --- the export log (row 7) -----------------------------------------

    async def add_export(self, row: dict[str, Any]) -> bool:
        """Record a download being made, in the same statement that checks its subject's data
        is not being deleted (row 7): False, and nothing recorded, once their purge has begun,
        so none begins after the purge deleted their downloads."""

        def go() -> bool:
            cursor = self._conn().execute(
                "INSERT INTO exports (id, asker, asker_name, subject, subject_name, scope, state, "
                "created_at, expires_at) SELECT ?, ?, ?, ?, ?, ?, 'making', ?, ? "
                "WHERE NOT EXISTS (SELECT 1 FROM removed_people WHERE sub = ? "
                "AND state IN ('purging', 'purged'))",
                (
                    row["id"],
                    row["asker"],
                    row["asker_name"],
                    row["subject"],
                    row["subject_name"],
                    row["scope"],
                    row["created_at"],
                    row["expires_at"],
                    row["subject"],
                ),
            )
            return bool(cursor.rowcount > 0)

        return await self._run(go)

    @staticmethod
    def _export(row: sqlite3.Row) -> dict[str, Any]:
        return {**dict(row), "holds": json.loads(row["holds"] or "{}")}

    async def export_row(self, export_id: str) -> dict[str, Any] | None:
        def go() -> dict[str, Any] | None:
            r = self._conn().execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
            return self._export(r) if r is not None else None

        return await self._run(go)

    async def exports_by(self, asker: str) -> list[dict[str, Any]]:
        """The downloads `asker` asked for, newest first."""
        return await self._run(
            lambda: [
                self._export(r)
                for r in self._conn().execute(
                    "SELECT * FROM exports WHERE asker = ? ORDER BY created_at DESC LIMIT 50",
                    (asker,),
                )
            ]
        )

    async def export_log(self, limit: int = 200) -> list[dict[str, Any]]:
        """Every download the owner made of another person's data, newest first."""
        return await self._run(
            lambda: [
                self._export(r)
                for r in self._conn().execute(
                    "SELECT * FROM exports WHERE scope = 'owner' ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            ]
        )

    async def put_export(self, export_id: str, **values: Any) -> None:
        allowed = {"state", "holds", "size", "error"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"not export fields: {sorted(unknown)}")
        if "holds" in values:
            values["holds"] = json.dumps(values["holds"])

        def go() -> None:
            columns = ", ".join(f"{k} = ?" for k in values)
            self._conn().execute(
                f"UPDATE exports SET {columns} WHERE id = ?", (*values.values(), export_id)
            )

        await self._run(go)

    async def exports_holding(self, sub: str) -> list[dict[str, Any]]:
        """Downloads whose file may still hold the person's data: theirs, or of them."""
        return await self._run(
            lambda: [
                self._export(r)
                for r in self._conn().execute(
                    "SELECT * FROM exports WHERE (asker = ? OR subject = ?) "
                    "AND state IN ('making', 'ready', 'failed')",
                    (sub, sub),
                )
            ]
        )

    async def fail_unfinished_exports(self, error: str) -> list[str]:
        """Start: every download still being made was cut off by a stop. Each fails with
        `error`; their ids."""

        def go() -> list[str]:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                cut = [r[0] for r in db.execute("SELECT id FROM exports WHERE state = 'making'")]
                db.execute(
                    "UPDATE exports SET state = 'failed', error = ? WHERE state = 'making'",
                    (error,),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return cut

        return await self._run(go)

    async def exports_due(self, now: float) -> list[dict[str, Any]]:
        """Downloads kept past their day: their files go, their log rows stay."""
        return await self._run(
            lambda: [
                self._export(r)
                for r in self._conn().execute(
                    "SELECT * FROM exports WHERE state IN ('ready', 'failed', 'making') "
                    "AND expires_at <= ?",
                    (now,),
                )
            ]
        )
