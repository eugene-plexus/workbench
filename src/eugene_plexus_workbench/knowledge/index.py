"""A person's saved documents, in one SQLite file of their own (MR4, design 4.1 and 4.9).

`<data dir>/knowledge/<person key>/index.sqlite3` holds that person's documents,
their passages and a full-text index over the passages (FTS5, BM25), and beside
it are the originals (`originals/<doc id>`) and the text read from each
(`<doc id>.txt`). **It is one file per person, and that is what makes it theirs**:
no query here takes a person, a path or a store from a request, there is no table
that holds two people's words, and the directory is named by a hash of the
person's `sub` (`files.person_key`), the same derivation attachments use. The
index also records whose it is (`meta.owner`) and refuses to open for anyone else,
so a path that was wrong would be refused, not served.

**Deletion is real.** Removing a document deletes its row, which takes its
passages with it (a foreign key, `ON DELETE CASCADE`) and their entries in the
full-text index (a trigger), and its original and its text, **in one
transaction**: the files go before the commit, so a file that cannot be removed
(a Windows program holding it) rolls everything back and says so, never leaving a
document that is half gone. `secure_delete` is on, so a page that held its words
is overwritten, the full-text index's segments are merged (a deleted word is
otherwise kept in them), and the log is folded into the file and emptied.

**A document in a state that cannot be searched has no passages**: `queued`,
`reading`, `skipped` and `failed` hold none, and the search only looks at
`searchable`, `embedding` and `ready`. So what a state says is what is true of it
(a state is never claimed ahead of the work), and nothing stale outlives an edit.

**Meaning** (MR5): a passage's vector is `chunks.vec` (NULL while it is owed, an empty
BLOB where the search model refused the passage), and which search model made the vectors
is `meta.embedder` (`embedders.Identity`), written in the same transaction as the first
vectors and checked in every one after, so two search models' numbers are never in one
index. `Index.writes` counts the transactions, which is what says a cached matrix of the
vectors is stale (`vectors.MatrixCache`).

One connection per index, used by one thread at a time (a lock), on a few
threads shared by every index and apart from the chats' store. Every method here
is `async` and returns data, never a cursor. The schema is versioned with
`PRAGMA user_version`: `_MIGRATIONS` is the history, an index written by a newer
Workbench is refused rather than guessed at, and a file that is not a database or
whose full-text index is gone says so in words (`IndexDamaged`) and never as an
empty result.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .. import files
from . import chunk, vectors

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3
INDEX_FILE = "index.sqlite3"
#: Where files wait, beside the index, while documents are removed (`Index._stage_files`).
TRASH = "trash"
#: Where a person's saved documents are: the scope of one is `library`, or
#: `chat:<id>` for one searched only in the chat it was attached to.
LIBRARY = "library"
CHAT = "chat:"
#: A project's documents (MV7b): `project:<id>`, searched by its chats, and kept like My
#: documents (a file of the same name replaces one already there).
PROJECT = "project:"
#: A person's notes (MV1, `workbench-memory-v2.md`): one document a note, by the note's id,
#: written by `Index.put_note` from the main store and never read from a file. Not a document
#: the person keeps: the Documents screen, its counts and its cap leave this scope out.
NOTES = "notes"
#: A person's past chats (MV2): one document an exchange (their message and the answer's
#: opening), by the answer's id, its `source` the chat's id, written by `Index.put_exchange`
#: from the main store. Not a document either.
EXCHANGES = "exchanges"
#: The scopes that are the person's memory rather than their documents: kept out of the
#: Documents screen, its counts, its cap and its order, and given meaning only by a search
#: model on the install's own machines.
PRIVATE: tuple[str, ...] = (NOTES, EXCHANGES)
#: What a chat's signature is kept under in `meta` (`exchanges_index`).
SIGNATURE = "exchanges:"
_PRIVATE_MARKS = ", ".join("?" for _ in PRIVATE)
#: What a document's `source` starts with when it was read from a folder the person was granted
#: (MR7): `folder:<grant id>/<path below the folder>`. A document is the same document while
#: its source is, so an edited file replaces the one it was and a deleted one is found by its path.
FOLDER = "folder:"
UPLOAD = "upload"


def folder_source(grant_id: str, path: str) -> str:
    return f"{FOLDER}{grant_id}/{path}"


def folder_prefix(grant_id: str) -> str:
    return f"{FOLDER}{grant_id}/"


def split_source(source: str) -> tuple[str, str] | None:
    """`(grant id, path)` of a document read from a folder; None for any other."""
    if not source.startswith(FOLDER):
        return None
    grant, _, path = source[len(FOLDER) :].partition("/")
    return (grant, path) if grant and path else None


def _only(source: str | None) -> str:
    """The SQL that keeps only the documents a person added (`upload`) or only those read from a
    folder (`folder`), as a clause to add to a `WHERE`; nothing for either."""
    if source == "upload":
        return " AND source NOT LIKE 'folder:%'"
    if source == "folder":
        return " AND source LIKE 'folder:%'"
    return ""


def _bounds(grant_id: str) -> tuple[str, str]:
    """The least and greatest `source` of a folder's documents: `/` is followed by `0`, so a range
    on the indexed column holds exactly the sources that start with `folder:<grant id>/`."""
    return folder_prefix(grant_id), f"{FOLDER}{grant_id}0"


QUEUED, READING, SEARCHABLE, EMBEDDING, READY, SKIPPED, FAILED = (
    "queued",
    "reading",
    "searchable",
    "embedding",
    "ready",
    "skipped",
    "failed",
)
STATES = (QUEUED, READING, SEARCHABLE, EMBEDDING, READY, SKIPPED, FAILED)
#: The states whose passages are searched, and the only ones that have any.
FOUND = (SEARCHABLE, EMBEDDING, READY)
#: A document still on its way to being searchable.
PENDING = (QUEUED, READING)

#: A document's id is made by Workbench; one in a URL is looked for, never joined
#: to a path before it is known to be one.
_ID = re.compile(r"[A-Za-z0-9_-]{6,64}")

#: The words a person reads when the index cannot be used.
NO_FTS = (
    "Workbench cannot search documents: this Python was built without SQLite full-text "
    "search. Reinstall Workbench from Eugene's console."
)


#: What a removed person's index says to a job that asks for it (row 7).
REMOVED = "This person was removed from Eugene, so Workbench keeps no documents for them."


class IndexUnavailable(Exception):
    """The index cannot be used, and why, in words a person reads."""

    code = "unavailable"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class IndexDamaged(IndexUnavailable):
    """The file is not an index, or has lost part of itself. Never "no results"."""

    code = "index_damaged"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"The search index for your documents is damaged (SQLite said: {reason}). "
            "Your files are safe."
        )
        self.reason = reason


class DeleteFailed(Exception):
    """A document's files could not be removed, so nothing was: it is all still there."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def valid_id(value: str) -> bool:
    return _ID.fullmatch(value) is not None


def chat_scope(chat_id: str) -> str:
    return f"{CHAT}{chat_id}"


def project_scope(project_id: str) -> str:
    return f"{PROJECT}{project_id}"


def named_scope(scope: str) -> bool:
    """A scope whose files are told apart by their names (My documents, a project's)."""
    return scope == LIBRARY or scope.startswith(PROJECT)


# --------------------------------------------------------------------------- #
# the schema
# --------------------------------------------------------------------------- #

#: Version 1, one statement each (a trigger holds semicolons). Times are epoch
#: seconds, as everywhere in Workbench. `chunks.vec` is for the search by meaning
#: that follows (MR5): NULL until embedded. The full-text index is an *external
#: content* table over `chunks` kept by triggers, so the words are stored once; its
#: second column is the heading path, which a search weighs twice as much as the
#: text. Beyond the sketch in the design: a document's `kind`, `language`, `code`,
#: `detail`, `notes`, `lines` and `attempt`, and a passage's `line_start` and
#: `line_end` (the locator for a file read as its own lines).
_SCHEMA: list[str] = [
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS documents ("
    "id TEXT PRIMARY KEY, scope TEXT NOT NULL, name TEXT NOT NULL, "
    "media_type TEXT NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, "
    "source TEXT NOT NULL, "
    "status TEXT NOT NULL CHECK (status IN "
    "('queued','reading','searchable','embedding','ready','skipped','failed')), "
    "reason TEXT, code TEXT, detail TEXT, "
    "kind TEXT NOT NULL, language TEXT, pages INTEGER, lines INTEGER, tokens_est INTEGER, "
    "chunks INTEGER NOT NULL DEFAULT 0, embedded INTEGER NOT NULL DEFAULT 0, "
    "notes TEXT NOT NULL DEFAULT '[]', attempt INTEGER NOT NULL DEFAULT 0, "
    "added_at REAL NOT NULL, read_at REAL, indexed_at REAL, source_mtime TEXT)",
    "CREATE INDEX IF NOT EXISTS documents_by_scope ON documents (scope, status)",
    "CREATE INDEX IF NOT EXISTS documents_by_name ON documents (scope, name COLLATE NOCASE)",
    "CREATE TABLE IF NOT EXISTS chunks ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "doc_id TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE, ord INTEGER NOT NULL, "
    "heading_path TEXT, page_start INTEGER, page_end INTEGER, line_start INTEGER, "
    "line_end INTEGER, char_start INTEGER, char_end INTEGER, "
    "text TEXT NOT NULL, tokens_est INTEGER NOT NULL, vec BLOB)",
    "CREATE INDEX IF NOT EXISTS chunks_by_doc ON chunks (doc_id, ord)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
    "text, heading_path, content='chunks', content_rowid='id', "
    "tokenize='unicode61 remove_diacritics 2')",
    "CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN "
    "INSERT INTO chunks_fts (rowid, text, heading_path) "
    "VALUES (new.id, new.text, COALESCE(new.heading_path, '')); END",
    "CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN "
    "INSERT INTO chunks_fts (chunks_fts, rowid, text, heading_path) "
    "VALUES ('delete', old.id, old.text, COALESCE(old.heading_path, '')); END",
    "CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE OF text, heading_path ON chunks BEGIN "
    "INSERT INTO chunks_fts (chunks_fts, rowid, text, heading_path) "
    "VALUES ('delete', old.id, old.text, COALESCE(old.heading_path, '')); "
    "INSERT INTO chunks_fts (rowid, text, heading_path) "
    "VALUES (new.id, new.text, COALESCE(new.heading_path, '')); END",
]

#: What brings an index at version N-1 to N (`_SCHEMA` is version 1 whole). Version 2
#: (MR5) keeps, for a document, the passages the search model refused (a JSON list), so
#: the Documents screen can say which, in words, without reading every passage. Version 3
#: (MR7) indexes a document's `source`, which names a folder's files by path.
_MIGRATIONS: dict[int, list[str]] = {
    2: ["ALTER TABLE documents ADD COLUMN refused TEXT"],
    3: ["CREATE INDEX IF NOT EXISTS documents_by_source ON documents (source)"],
}

#: The `meta` key that holds the search model an index was built with.
EMBEDDER = "embedder"


def chunker_meta() -> dict[str, Any]:
    """What this Workbench splits files with, as `meta` records it."""
    return {
        "name": chunk.CHUNKER,
        "version": chunk.CHUNKER_VERSION,
        "target": chunk.TARGET,
        "max": chunk.MAX,
        "min": chunk.MIN,
    }


# --------------------------------------------------------------------------- #
# what an index says
# --------------------------------------------------------------------------- #


@dataclass
class Document:
    id: str
    scope: str
    name: str
    media_type: str
    bytes: int
    sha256: str
    source: str
    status: str
    kind: str
    added_at: float
    reason: str | None = None
    code: str | None = None
    detail: str | None = None
    language: str | None = None
    pages: int | None = None
    lines: int | None = None
    tokens_est: int | None = None
    chunks: int = 0
    embedded: int = 0
    notes: list[str] = field(default_factory=list)
    attempt: int = 0
    read_at: float | None = None
    indexed_at: float | None = None
    source_mtime: str | None = None
    #: Passages the search model refused (too long for it): each `{ord, page, chars, why}`.
    refused: list[dict[str, Any]] = field(default_factory=list)

    @property
    def searchable(self) -> bool:
        return self.status in FOUND


@dataclass(frozen=True)
class NewDocument:
    """A file just brought in, before it is read: the person's own words for it."""

    id: str
    scope: str
    name: str
    media_type: str
    bytes: int
    sha256: str
    source: str
    kind: str
    language: str | None = None
    source_mtime: str | None = None
    #: When it was added; None is now. A document made again from what its own
    #: files say (`Index.sidecars`) keeps the place it had in the list.
    added_at: float | None = None


@dataclass
class Added:
    """What adding a document came to: the document, the ones it replaced (the same
    name, brought in again), and whether it was already there, byte for byte."""

    document: Document
    replaced: list[Document] = field(default_factory=list)
    unchanged: bool = False


@dataclass(frozen=True)
class Hit:
    """A passage a search found, with the file it is in."""

    chunk_id: int
    doc_id: str
    ord: int
    text: str
    heading_path: str | None
    page_start: int | None
    page_end: int | None
    line_start: int | None
    line_end: int | None
    char_start: int | None
    char_end: int | None
    tokens_est: int
    name: str
    scope: str
    kind: str
    #: What the way of searching scored it, on its own scale, which means nothing beside
    #: another's: BM25 as SQLite gives it (lower is better) for words, and for meaning
    #: the similarity of its vector to the question's (cosine, higher is better).
    score: float


@dataclass(frozen=True)
class Totals:
    documents: int
    passages: int
    bytes: int
    by_state: Mapping[str, int]
    #: Passages that have their meaning (or were refused it), and passages still owed it.
    embedded: int = 0
    owed: int = 0


@dataclass(frozen=True)
class Pending:
    """A passage that is owed its meaning, with what it is embedded from."""

    chunk_id: int
    doc_id: str
    ord: int
    heading_path: str | None
    page_start: int | None
    text: str
    name: str
    scope: str


@dataclass(frozen=True)
class Stored:
    """What storing a batch of vectors came to."""

    #: Passages that now have their vector, and passages marked refused.
    written: int = 0
    refused: int = 0
    #: Passages that were not there any more (their document was removed or read again).
    gone: int = 0
    #: Documents that are `ready` now: nothing is owed them.
    completed: tuple[str, ...] = ()
    #: Set when nothing was stored because the index was built with another search model:
    #: `(model, dim)` it was built with.
    changed: tuple[str, int] | None = None
    #: Set when nothing was stored because the vectors were cleared (Rebuild) after the
    #: batch was begun: its numbers are from the search model that was just let go.
    stale: bool = False


@dataclass(frozen=True)
class MeaningCounts:
    """How much of what can be found by words can also be found by meaning."""

    passages: int
    #: Passages that have a vector, or were refused one.
    embedded: int
    #: Passages of documents that are waiting for their meaning.
    owed: int
    #: Passages of documents that are `ready` without it (no search model was in play).
    words_only: int


@dataclass(frozen=True)
class Held:
    """What the index holds of one file of a granted folder (MR7): its document, the size and the
    modified time it was read at (nanoseconds as text, with a `!` after it when the file was too
    new to trust that it would not change again unseen), and the hash of what was read. A
    document with no hash was never read: skipped before it was, with no copy kept."""

    doc_id: str
    path: str
    size: int
    mtime: str | None
    sha256: str
    status: str
    code: str | None

    @property
    def copied(self) -> bool:
        return bool(self.sha256)


@dataclass(frozen=True)
class FolderCounts:
    """A folder's documents, counted: by state, and by reason for the ones that were skipped or
    failed, with their passages, the meaning added to them and the bytes of their files."""

    files: int
    passages: int
    embedded: int
    owed: int
    #: The bytes of the folder's files that were looked at, and of those that were copied.
    bytes: int
    copied: int
    by_state: Mapping[str, int]
    by_code: Mapping[str, int]


@dataclass(frozen=True)
class FolderSkip:
    """A file of a folder that was looked at and could not be read, to be kept as a `skipped` (or
    `failed`) document: its reason in the words a person reads, and a code the page can act on. No
    copy of it is kept, so it has no hash."""

    new: NewDocument
    code: str
    reason: str
    detail: str | None = None
    state: str = SKIPPED


def _document(row: sqlite3.Row) -> Document:
    try:
        notes = json.loads(row["notes"] or "[]")
    except ValueError:
        notes = []
    try:
        refused = json.loads(row["refused"] or "[]")
    except ValueError:
        refused = []
    return Document(
        id=row["id"],
        scope=row["scope"],
        name=row["name"],
        media_type=row["media_type"],
        bytes=row["bytes"],
        sha256=row["sha256"],
        source=row["source"],
        status=row["status"],
        kind=row["kind"],
        added_at=row["added_at"],
        reason=row["reason"],
        code=row["code"],
        detail=row["detail"],
        language=row["language"],
        pages=row["pages"],
        lines=row["lines"],
        tokens_est=row["tokens_est"],
        chunks=row["chunks"],
        embedded=row["embedded"],
        notes=[str(n) for n in notes] if isinstance(notes, list) else [],
        attempt=row["attempt"],
        read_at=row["read_at"],
        indexed_at=row["indexed_at"],
        source_mtime=row["source_mtime"],
        refused=[r for r in refused if isinstance(r, dict)] if isinstance(refused, list) else [],
    )


def _hit(row: sqlite3.Row) -> Hit:
    return Hit(
        chunk_id=row["chunk_id"],
        doc_id=row["doc_id"],
        ord=row["ord"],
        text=row["text"],
        heading_path=row["heading_path"],
        page_start=row["page_start"],
        page_end=row["page_end"],
        line_start=row["line_start"],
        line_end=row["line_end"],
        char_start=row["char_start"],
        char_end=row["char_end"],
        tokens_est=row["tokens_est"],
        name=row["name"],
        scope=row["scope"],
        kind=row["kind"],
        score=float(row["score"]),
    )


# --------------------------------------------------------------------------- #
# one person's index
# --------------------------------------------------------------------------- #

_SEARCH = (
    "SELECT c.id AS chunk_id, c.doc_id, c.ord, c.text, c.heading_path, c.page_start, "
    "c.page_end, c.line_start, c.line_end, c.char_start, c.char_end, c.tokens_est, "
    "d.name, d.scope, d.kind, bm25(chunks_fts, 1.0, 2.0) AS score "
    "FROM chunks_fts JOIN chunks AS c ON c.id = chunks_fts.rowid "
    "JOIN documents AS d ON d.id = c.doc_id "
    "WHERE chunks_fts MATCH ? AND d.scope IN ({scopes}) "
    "AND d.status IN ('searchable', 'embedding', 'ready') "
    "ORDER BY score, c.doc_id, c.ord LIMIT ?"
)
_INSERT_CHUNK = (
    "INSERT INTO chunks (doc_id, ord, heading_path, page_start, page_end, line_start, "
    "line_end, char_start, char_end, text, tokens_est) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
#: A damaged file is told from a busy, a full or an unreachable one by what SQLite says.
_TRANSIENT = ("database is locked", "database table is locked")
_FULL = ("database or disk is full",)
_UNREACHABLE = ("disk i/o error", "unable to open database file", "readonly database")


class Index:
    """One person's documents. Use `Indexes.get`; the person is whose `sub` made it."""

    def __init__(self, sub: str, directory: Path, executor: ThreadPoolExecutor) -> None:
        self.sub = sub
        self.dir = directory
        self.path = directory / INDEX_FILE
        self.originals = directory / "originals"
        self._executor = executor
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        #: How many transactions have changed this index since it was opened. A cached
        #: matrix of the vectors is good only while this is what it was when it was read.
        self.writes = 0
        #: How many times the vectors have been cleared (Rebuild). A batch of vectors made
        #: before one is never stored after it (`store_vectors`).
        self.epoch = 0
        #: Row 7: the person was removed from Eugene. A job still holding this index cannot
        #: open it again, so none makes their folder again after it is deleted.
        self.retired = False

    # --- where things are ----------------------------------------------

    def original_path(self, doc_id: str) -> Path:
        if not valid_id(doc_id):
            raise ValueError("not a document id")
        return self.originals / doc_id

    def text_path(self, doc_id: str) -> Path:
        if not valid_id(doc_id):
            raise ValueError("not a document id")
        return self.dir / f"{doc_id}.txt"

    def part_path(self, doc_id: str) -> Path:
        """Where an original is written before it is whole."""
        return self.original_path(doc_id).with_name(f"{doc_id}.part")

    def meta_path(self, doc_id: str) -> Path:
        """What a document's own files say of it: its name, type and size (the
        `.meta.json` beside its text), so that an index that is lost can be made again
        from them (`Index.sidecars`) and the person's files are never only a pile of ids."""
        if not valid_id(doc_id):
            raise ValueError("not a document id")
        return self.dir / f"{doc_id}.meta.json"

    def write_sidecar(self, new: NewDocument) -> None:
        """Keep what a document's files say of it, whole or not at all."""
        said = {
            "id": new.id,
            "scope": new.scope,
            "name": new.name,
            "mediaType": new.media_type,
            "bytes": new.bytes,
            "sha256": new.sha256,
            "source": new.source,
            "kind": new.kind,
            "language": new.language,
            "sourceMtime": new.source_mtime,
            "addedAt": new.added_at if new.added_at is not None else time.time(),
        }
        target = self.meta_path(new.id)
        part = target.with_name(target.name + ".part")
        part.write_text(json.dumps(said, ensure_ascii=False), encoding="utf-8", newline="\n")
        part.replace(target)

    def sidecars(self) -> list[NewDocument]:
        """Every document the files beside the index describe whose original is still
        there, oldest first. A sidecar that cannot be read, or whose original is gone, is
        not one: it is left for the reconcile to remove with the rest that no row claims."""
        found: list[NewDocument] = []
        if not self.dir.is_dir():
            return found
        for path in self.dir.glob("*.meta.json"):
            doc_id = path.name.removesuffix(".meta.json")
            if not valid_id(doc_id) or not self.original_path(doc_id).is_file():
                continue
            try:
                said = json.loads(path.read_text(encoding="utf-8"))
                found.append(
                    NewDocument(
                        id=doc_id,
                        scope=str(said["scope"]),
                        name=str(said["name"]),
                        media_type=str(said["mediaType"]),
                        bytes=int(said["bytes"]),
                        sha256=str(said["sha256"]),
                        source=str(said["source"]),
                        kind=str(said["kind"]),
                        language=said.get("language"),
                        source_mtime=said.get("sourceMtime"),
                        added_at=float(said["addedAt"]),
                    )
                )
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(found, key=lambda n: (n.added_at or 0.0, n.id))

    def quarantine(self) -> None:
        """Take the index file out of the way, with its log, so a new one is made in its
        place. Close it first (`close`); nothing is read from it again. A batch of vectors
        begun before this is never stored after it: its passage numbers are the old file's,
        and the new one counts from 1 again."""
        self.epoch += 1
        self.writes += 1
        for suffix in ("", "-wal", "-shm"):
            path = self.path.with_name(self.path.name + suffix)
            for attempt in range(6):
                try:
                    path.unlink(missing_ok=True)
                    break
                except OSError:
                    if attempt == 5:
                        raise
                    time.sleep(0.1)

    # --- the connection -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self.retired:
            raise IndexUnavailable(REMOVED, code="removed")
        self.dir.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=5.0)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            self._initialize(db)
        except sqlite3.DatabaseError as exc:
            db.close()
            raise self._explain(exc) from exc
        except BaseException:
            db.close()
            raise
        return db

    def _initialize(self, db: sqlite3.Connection) -> None:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise IndexUnavailable(
                f"{self.path} was written by a newer Workbench (index version {version}; this "
                f"Workbench knows up to {SCHEMA_VERSION}). Restore the matching Workbench.",
                code="index_newer",
            )
        db.execute("BEGIN IMMEDIATE")
        try:
            if version == 0:
                for statement in _SCHEMA:
                    db.execute(statement)
            for target in range(max(version, 1) + 1, SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS[target]:
                    db.execute(statement)
            owner = db.execute("SELECT value FROM meta WHERE key = 'owner'").fetchone()
            if owner is None:
                db.execute("INSERT INTO meta (key, value) VALUES ('owner', ?)", (self.sub,))
                db.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES ('chunker', ?)",
                    (json.dumps(chunker_meta()),),
                )
                db.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES ('created_at', ?)",
                    (str(time.time()),),
                )
            elif owner[0] != self.sub:
                raise IndexDamaged("this index belongs to someone else")
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            db.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                db.execute("ROLLBACK")
            raise

    @staticmethod
    def _explain(exc: sqlite3.DatabaseError) -> Exception:
        """What a person is told of an error SQLite raised in the index."""
        said = str(exc)
        low = said.lower()
        if "no such module: fts5" in low:
            return IndexUnavailable(NO_FTS, code="fts_missing")
        if any(word in low for word in _FULL):
            return IndexUnavailable(
                f"Workbench could not write to your documents' index: the disk is full ({said}).",
                code="disk_full",
            )
        if any(word in low for word in _UNREACHABLE):
            return IndexUnavailable(
                f"Workbench could not use your documents' index ({said}).", code="unreachable"
            )
        if any(word in low for word in _TRANSIENT):
            return IndexUnavailable(
                f"Your documents' index is busy ({said}). Try again in a moment.", code="busy"
            )
        return IndexDamaged(said)

    async def _run[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        def locked() -> T:
            with self._lock:
                if self._db is None:
                    self._db = self._connect()
                try:
                    return fn(self._db)
                except sqlite3.IntegrityError:
                    raise
                except sqlite3.DatabaseError as exc:
                    raise self._explain(exc) from exc

        return await asyncio.get_running_loop().run_in_executor(self._executor, locked)

    async def close(self) -> None:
        def shut() -> None:
            with self._lock:
                if self._db is not None:
                    with contextlib.suppress(sqlite3.Error):
                        self._db.close()
                    self._db = None

        await asyncio.get_running_loop().run_in_executor(self._executor, shut)

    async def discard(self) -> None:
        """Close the index and take its file out of the way (`quarantine`) as one step, under
        the lock every read takes: a worker that asks between the two would open the file again,
        and Windows does not remove a file that is open."""

        def go() -> None:
            with self._lock:
                if self._db is not None:
                    with contextlib.suppress(sqlite3.Error):
                        self._db.close()
                    self._db = None
                self.quarantine()

        await asyncio.get_running_loop().run_in_executor(self._executor, go)

    def _transaction[T](self, db: sqlite3.Connection, body: Callable[[], T]) -> T:
        """`body` as one transaction: all of it, or none."""
        db.execute("BEGIN IMMEDIATE")
        try:
            result = body()
            db.execute("COMMIT")
            self.writes += 1
            return result
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                db.execute("ROLLBACK")
            raise

    # --- what is there --------------------------------------------------

    async def documents(
        self, scope: str | None = None, *, only: str | None = None
    ) -> list[Document]:
        """The documents, newest first: those of one scope, or every one. With `only`, just the
        ones the person added themselves (`upload`), or just the files of their folders
        (`folder`): a folder's files are listed under it, so that a folder of ten thousand
        files is not part of every poll."""

        def go(db: sqlite3.Connection) -> list[Document]:
            where, params = ("", ()) if scope is None else (" WHERE scope = ?", (scope,))
            if only is not None:
                where = (where or " WHERE 1") + _only(only)
            rows = db.execute(
                f"SELECT * FROM documents{where} ORDER BY added_at DESC, rowid DESC", params
            )
            return [_document(r) for r in rows]

        return await self._run(go)

    async def at_origin(self, scope: str, source: str, name: str) -> Document | None:
        """The document now where one was (MV6): a folder's file by its path, a file of My
        documents by its name (one of the same name replaces it, `add`), a chat's by its
        `source`. The newest, or None. The same rule `add` uses to find what it replaces."""

        def go(db: sqlite3.Connection) -> Document | None:
            if source.startswith(FOLDER):
                row = db.execute(
                    "SELECT * FROM documents WHERE source = ? ORDER BY added_at DESC, rowid DESC",
                    (source,),
                ).fetchone()
            elif named_scope(scope):
                row = db.execute(
                    "SELECT * FROM documents WHERE scope = ? AND name = ? COLLATE NOCASE "
                    "AND source NOT LIKE 'folder:%' ORDER BY added_at DESC, rowid DESC",
                    (scope, name),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM documents WHERE scope = ? AND source = ? "
                    "ORDER BY added_at DESC, rowid DESC",
                    (scope, source),
                ).fetchone()
            return _document(row) if row is not None else None

        return await self._run(go)

    async def document(self, doc_id: str) -> Document | None:
        def go(db: sqlite3.Connection) -> Document | None:
            row = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            return _document(row) if row is not None else None

        return await self._run(go)

    async def totals(self, scope: str = LIBRARY, *, only: str | None = None) -> Totals:
        """What a scope holds, counted; with `only`, just the files the person added themselves
        (`upload`) or just those read from their folders (`folder`)."""

        def go(db: sqlite3.Connection) -> Totals:
            by_state = dict.fromkeys(STATES, 0)
            documents = passages = size = embedded = owed = 0
            for row in db.execute(
                "SELECT status, COUNT(*) AS n, COALESCE(SUM(chunks), 0) AS c, "
                "COALESCE(SUM(bytes), 0) AS b, COALESCE(SUM(embedded), 0) AS e "
                f"FROM documents WHERE scope = ?{_only(only)} GROUP BY status",
                (scope,),
            ):
                by_state[row["status"]] = row["n"]
                documents += row["n"]
                passages += row["c"]
                size += row["b"]
                embedded += row["e"]
                if row["status"] in (SEARCHABLE, EMBEDDING):
                    owed += row["c"] - row["e"]
            return Totals(documents, passages, size, by_state, embedded, owed)

        return await self._run(go)

    async def passages_total(self) -> int:
        """Every passage of the person's documents, in every scope but their notes': what
        the person's cap counts."""

        def go(db: sqlite3.Connection) -> int:
            return int(
                db.execute(
                    "SELECT COALESCE(SUM(chunks), 0) FROM documents WHERE scope NOT IN "
                    f"({_PRIVATE_MARKS})",
                    PRIVATE,
                ).fetchone()[0]
            )

        return await self._run(go)

    async def scopes(self) -> list[str]:
        def go(db: sqlite3.Connection) -> list[str]:
            return [r[0] for r in db.execute("SELECT DISTINCT scope FROM documents")]

        return await self._run(go)

    async def count_found(self, scopes: Sequence[str]) -> tuple[int, int, int]:
        """`(documents, passages, still on their way)` in these scopes: what a
        search of them can look at, and what it cannot yet."""
        if not scopes:
            return 0, 0, 0

        def go(db: sqlite3.Connection) -> tuple[int, int, int]:
            marks = ", ".join("?" for _ in scopes)
            row = db.execute(
                "SELECT "
                "COALESCE(SUM(status IN ('searchable','embedding','ready')), 0) AS found, "
                "COALESCE(SUM(CASE WHEN status IN ('searchable','embedding','ready') "
                "THEN chunks ELSE 0 END), 0) AS passages, "
                "COALESCE(SUM(status IN ('queued','reading')), 0) AS waiting "
                f"FROM documents WHERE scope IN ({marks})",
                tuple(scopes),
            ).fetchone()
            return int(row["found"]), int(row["passages"]), int(row["waiting"])

        return await self._run(go)

    async def meta(self, key: str) -> str | None:
        def go(db: sqlite3.Connection) -> str | None:
            row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return str(row[0]) if row is not None else None

        return await self._run(go)

    async def set_meta(self, key: str, value: str) -> None:
        def go(db: sqlite3.Connection) -> None:
            db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

        await self._run(go)

    # --- bringing a document in -----------------------------------------

    async def add(self, new: NewDocument) -> Added:
        """Keep a new document, `queued`. In My documents, a file of the same name
        (case apart) is **replaced**: the old one goes at once, with its passages and
        files, so its old words are never found again; unless its bytes are the same
        and it is still good, when nothing changes. A document for one chat is the
        same one while its `source` is."""

        def go(db: sqlite3.Connection) -> Added:
            def body() -> Added:
                if new.source.startswith(FOLDER):
                    # A file of a folder is the one at its path, whatever it is called: an edited
                    # file replaces the document it was, and nothing else does.
                    twins = [
                        _document(r)
                        for r in db.execute(
                            "SELECT * FROM documents WHERE source = ? "
                            "ORDER BY added_at DESC, rowid DESC",
                            (new.source,),
                        )
                    ]
                elif named_scope(new.scope):
                    twins = [
                        _document(r)
                        for r in db.execute(
                            "SELECT * FROM documents WHERE scope = ? AND name = ? COLLATE NOCASE "
                            "AND source NOT LIKE 'folder:%' ORDER BY added_at DESC, rowid DESC",
                            (new.scope, new.name),
                        )
                    ]
                else:
                    twins = [
                        _document(r)
                        for r in db.execute(
                            "SELECT * FROM documents WHERE scope = ? AND source = ?",
                            (new.scope, new.source),
                        )
                    ]
                same = next(
                    (
                        t
                        for t in twins
                        if t.status not in (SKIPPED, FAILED)
                        and (not named_scope(new.scope) or t.sha256 == new.sha256)
                    ),
                    None,
                )
                if same is not None:
                    if new.source.startswith(FOLDER) and (
                        same.source_mtime != new.source_mtime or same.bytes != new.bytes
                    ):
                        # The same words under a new modified time: the file was touched, or
                        # saved again unchanged. What is kept is the time it is now at.
                        db.execute(
                            "UPDATE documents SET source_mtime = ?, bytes = ? WHERE id = ?",
                            (new.source_mtime, new.bytes, same.id),
                        )
                        same.source_mtime, same.bytes = new.source_mtime, new.bytes
                    return Added(same, unchanged=True)
                for old in twins:
                    db.execute("DELETE FROM documents WHERE id = ?", (old.id,))
                    self._remove_files(old.id)
                db.execute(
                    "INSERT INTO documents (id, scope, name, media_type, bytes, sha256, source, "
                    "status, kind, language, added_at, source_mtime) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                    (
                        new.id,
                        new.scope,
                        new.name,
                        new.media_type,
                        new.bytes,
                        new.sha256,
                        new.source,
                        new.kind,
                        new.language,
                        new.added_at if new.added_at is not None else time.time(),
                        new.source_mtime,
                    ),
                )
                row = db.execute("SELECT * FROM documents WHERE id = ?", (new.id,)).fetchone()
                return Added(_document(row), replaced=twins)

            added = self._transaction(db, body)
            if added.replaced:
                self._scrub(db)
            return added

        return await self._run(go)

    async def requeue(self, doc_id: str) -> Document | None:
        """Read a document again ("Re-read"): its passages go now, so nothing of
        the old reading is found, and it waits its turn. None when it is not here."""

        def go(db: sqlite3.Connection) -> Document | None:
            def body() -> Document | None:
                found = db.execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone()
                if found is None:
                    return None
                db.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
                db.execute(
                    "UPDATE documents SET status = 'queued', attempt = attempt + 1, "
                    "reason = NULL, code = NULL, detail = NULL, chunks = 0, embedded = 0, "
                    "refused = NULL, indexed_at = NULL WHERE id = ?",
                    (doc_id,),
                )
                row = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
                return _document(row)

            return self._transaction(db, body)

        return await self._run(go)

    async def requeue_interrupted(self) -> int:
        """Boot: documents a stop cut off while they were read wait their turn again."""

        def go(db: sqlite3.Connection) -> int:
            def body() -> int:
                return db.execute(
                    "UPDATE documents SET status = 'queued', attempt = attempt + 1 "
                    "WHERE status = 'reading'"
                ).rowcount

            return self._transaction(db, body)

        return await self._run(go)

    async def claim(self) -> Document | None:
        """The document that has waited longest, now `reading`; None when none waits.
        A document made from a file a person is waiting on in a chat comes before the
        ones they are keeping in My documents: it is a send that waits for it."""

        def go(db: sqlite3.Connection) -> Document | None:
            def body() -> Document | None:
                row = db.execute(
                    "SELECT * FROM documents WHERE status = 'queued' "
                    "ORDER BY (scope = 'library' OR scope LIKE 'project:%'), added_at, rowid "
                    "LIMIT 1"
                ).fetchone()
                if row is None:
                    return None
                db.execute(
                    "UPDATE documents SET status = 'reading', read_at = ? WHERE id = ?",
                    (time.time(), row["id"]),
                )
                fresh = db.execute("SELECT * FROM documents WHERE id = ?", (row["id"],)).fetchone()
                return _document(fresh)

            return self._transaction(db, body)

        return await self._run(go)

    async def commit_reading(
        self,
        doc_id: str,
        attempt: int,
        summary: Mapping[str, Any],
        passages: Sequence[chunk.Passage],
        *,
        cap: int,
    ) -> str:
        """What reading a document came to: its passages and their full-text entries,
        and its state, `searchable`, **in one transaction**. Returns `searchable`;
        `capped` when the person would pass `cap` passages (nothing is kept, and the
        document is skipped, saying so); `gone` when the document was removed, or read
        again, meanwhile (nothing is kept, and its caller removes what it wrote)."""

        def go(db: sqlite3.Connection) -> str:
            def body() -> str:
                row = db.execute(
                    "SELECT status, attempt FROM documents WHERE id = ?", (doc_id,)
                ).fetchone()
                if row is None or row["status"] != READING or row["attempt"] != attempt:
                    return "gone"
                others = int(
                    db.execute(
                        "SELECT COALESCE(SUM(chunks), 0) FROM documents WHERE id != ? "
                        f"AND scope NOT IN ({_PRIVATE_MARKS})",
                        (doc_id, *PRIVATE),
                    ).fetchone()[0]
                )
                if others + len(passages) > cap:
                    reason = (
                        f"your documents would come to more than the {cap:,} passages Workbench "
                        f"keeps for one person ({others:,} already, and this file is "
                        f"{len(passages):,} more). Remove a document, or ask the owner to raise "
                        "the limit in Eugene's console."
                    )
                    db.execute(
                        "UPDATE documents SET status = 'skipped', code = 'too_many_passages', "
                        "reason = ?, detail = ?, chunks = 0 WHERE id = ?",
                        (
                            f"Skipped: {reason}",
                            f"{others} + {len(passages)} > {cap}",
                            doc_id,
                        ),
                    )
                    return "capped"
                db.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
                db.executemany(
                    _INSERT_CHUNK,
                    [
                        (
                            doc_id,
                            p.ord,
                            p.heading_path,
                            p.page_start,
                            p.page_end,
                            p.line_start,
                            p.line_end,
                            p.char_start,
                            p.char_end,
                            p.text,
                            p.tokens_est,
                        )
                        for p in passages
                    ],
                )
                now = time.time()
                db.execute(
                    "UPDATE documents SET status = 'searchable', reason = NULL, code = NULL, "
                    "detail = NULL, kind = ?, language = ?, pages = ?, lines = ?, tokens_est = ?, "
                    "chunks = ?, embedded = 0, refused = NULL, notes = ?, indexed_at = ? "
                    "WHERE id = ?",
                    (
                        summary["kind"],
                        summary.get("language"),
                        summary.get("pages"),
                        summary.get("lines"),
                        summary.get("tokens"),
                        len(passages),
                        json.dumps(list(summary.get("notes") or [])),
                        now,
                        doc_id,
                    ),
                )
                return "searchable"

            return self._transaction(db, body)

        return await self._run(go)

    async def end_unread(
        self,
        doc_id: str,
        attempt: int,
        *,
        state: str,
        code: str,
        reason: str,
        detail: str | None = None,
        pages: int | None = None,
    ) -> bool:
        """A document that gave no text: `skipped` or `failed`, with why, in the
        words a person reads. False when it was removed or read again meanwhile."""
        if state not in (SKIPPED, FAILED):
            raise ValueError(f"not an end: {state}")

        def go(db: sqlite3.Connection) -> bool:
            def body() -> bool:
                return (
                    db.execute(
                        "UPDATE documents SET status = ?, code = ?, reason = ?, detail = ?, "
                        "pages = COALESCE(?, pages), chunks = 0 "
                        "WHERE id = ? AND status = 'reading' AND attempt = ?",
                        (state, code, reason, detail, pages, doc_id, attempt),
                    ).rowcount
                    == 1
                )

            return self._transaction(db, body)

        return await self._run(go)

    async def finish(self, doc_id: str) -> bool:
        """Everything a document is owed is done: `searchable` is `ready`. False
        when it is not `searchable` (or `embedding`) any more."""

        def go(db: sqlite3.Connection) -> bool:
            def body() -> bool:
                return (
                    db.execute(
                        "UPDATE documents SET status = 'ready' "
                        "WHERE id = ? AND status IN ('searchable', 'embedding')",
                        (doc_id,),
                    ).rowcount
                    == 1
                )

            return self._transaction(db, body)

        return await self._run(go)

    async def unfinished(self) -> list[Document]:
        """Documents that can be found by words and are not `ready`: a stop cut
        off what they were still owed."""

        def go(db: sqlite3.Connection) -> list[Document]:
            rows = db.execute(
                "SELECT * FROM documents WHERE status IN ('searchable', 'embedding') "
                "ORDER BY added_at, rowid"
            )
            return [_document(r) for r in rows]

        return await self._run(go)

    # --- meaning (MR5) ----------------------------------------------------

    async def embedder(self) -> str | None:
        """The search model the vectors were made by, as `embedders.Identity` JSON, or
        None while there are none: `meta.embedder`."""
        return await self.meta(EMBEDDER)

    async def pending_meaning(
        self, limit: int, *, notes: bool = True, documents: bool = True
    ) -> tuple[list[Pending], int]:
        """The next `limit` passages owed their meaning, in the order they are served:
        a chat's own files before the ones in My documents, then the document that has
        waited longest, in reading order; and the `epoch` the index is at, read with them,
        for `store_vectors`. Documents are chosen first (a small table), then their
        passages, so this never reads the passages of every document."""

        def go(db: sqlite3.Connection) -> tuple[list[Pending], int]:
            owed: list[Pending] = []
            if not notes and not documents:
                return owed, self.epoch
            # A person's notes go to a search model on their own machines only, and are
            # added whether or not the owner has documents on (`Meaning._work` says which).
            # Past chats (MV2) are the person's memory too, and come after their notes.
            only = (
                ""
                if notes and documents
                else f" AND scope IN ({_PRIVATE_MARKS})"
                if notes
                else f" AND scope NOT IN ({_PRIVATE_MARKS})"
            )
            docs = db.execute(
                "SELECT id, name, scope FROM documents WHERE status IN ('searchable', 'embedding') "
                f"AND chunks > embedded{only} ORDER BY CASE WHEN scope = 'library' THEN 1 "
                f"WHEN scope = '{NOTES}' THEN 2 WHEN scope = '{EXCHANGES}' THEN 3 ELSE 0 END, "
                "added_at, rowid",
                () if notes and documents else PRIVATE,
            ).fetchall()
            for doc in docs:
                rows = db.execute(
                    "SELECT id, ord, heading_path, page_start, text FROM chunks "
                    "WHERE doc_id = ? AND vec IS NULL ORDER BY ord LIMIT ?",
                    (doc["id"], limit - len(owed)),
                )
                owed.extend(
                    Pending(
                        r["id"],
                        doc["id"],
                        r["ord"],
                        r["heading_path"],
                        r["page_start"],
                        r["text"],
                        doc["name"],
                        doc["scope"],
                    )
                    for r in rows
                )
                if len(owed) >= limit:
                    break
            return owed, self.epoch

        return await self._run(go)

    async def store_vectors(
        self,
        identity: str,
        model: str,
        dim: int,
        vectors_: Sequence[tuple[int, str, bytes]],
        refused: Sequence[tuple[int, str, dict[str, Any]]] = (),
        epoch: int | None = None,
    ) -> Stored:
        """Keep a batch of vectors, `(passage id, document id, stored vector)`, and the
        passages the search model refused, `(passage id, document id, what)`, **in one
        transaction** with the identity of the search model that made them: the first batch
        writes it, and every one after is checked against it, and **nothing is stored** (and
        `changed` says what the index was built with) when it is not this model at this size.
        A passage that is not there any more is left out. A document with nothing owed
        any more is `ready`. `epoch` is `Index.epoch` as it was when the batch was begun:
        if the vectors were cleared since, nothing is stored (`stale`)."""

        def go(db: sqlite3.Connection) -> Stored:
            def body() -> Stored:
                if epoch is not None and epoch != self.epoch:
                    return Stored(stale=True)
                row = db.execute("SELECT value FROM meta WHERE key = ?", (EMBEDDER,)).fetchone()
                if row is None:
                    db.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (EMBEDDER, identity))
                else:
                    try:
                        built = json.loads(row[0])
                        built_model, built_dim = str(built["model"]), int(built["dim"])
                    except (ValueError, KeyError, TypeError) as exc:
                        raise IndexDamaged(
                            f"the search model this index was built with is unreadable ({exc})"
                        ) from exc
                    if built_model != model or built_dim != dim:
                        return Stored(changed=(built_model, built_dim))
                written = skipped = gone = 0
                touched: set[str] = set()
                for chunk_id, doc_id, blob in vectors_:
                    changed = db.execute(
                        "UPDATE chunks SET vec = ? WHERE id = ? AND vec IS NULL", (blob, chunk_id)
                    ).rowcount
                    written += changed
                    gone += 1 - changed
                    if changed:
                        touched.add(doc_id)
                refusals: dict[str, list[dict[str, Any]]] = {}
                for chunk_id, doc_id, what in refused:
                    changed = db.execute(
                        "UPDATE chunks SET vec = X'' WHERE id = ? AND vec IS NULL", (chunk_id,)
                    ).rowcount
                    skipped += changed
                    gone += 1 - changed
                    if changed:
                        touched.add(doc_id)
                        refusals.setdefault(doc_id, []).append(what)
                completed: list[str] = []
                for doc_id in touched:
                    doc = db.execute(
                        "SELECT chunks, status, refused FROM documents WHERE id = ?", (doc_id,)
                    ).fetchone()
                    if doc is None:
                        continue
                    have = int(
                        db.execute(
                            "SELECT COUNT(*) FROM chunks WHERE doc_id = ? AND vec IS NOT NULL",
                            (doc_id,),
                        ).fetchone()[0]
                    )
                    status = doc["status"]
                    if status in (SEARCHABLE, EMBEDDING):
                        status = READY if have >= doc["chunks"] else EMBEDDING
                    kept = json.loads(doc["refused"] or "[]") if doc_id in refusals else None
                    db.execute(
                        "UPDATE documents SET embedded = ?, status = ?, "
                        "refused = CASE WHEN ? THEN ? ELSE refused END WHERE id = ?",
                        (
                            have,
                            status,
                            kept is not None,
                            json.dumps([*(kept or []), *refusals.get(doc_id, [])]),
                            doc_id,
                        ),
                    )
                    if status == READY and doc["status"] != READY:
                        completed.append(doc_id)
                return Stored(written, skipped, gone, tuple(sorted(completed)))

            return self._transaction(db, body)

        return await self._run(go)

    async def clear_vectors(self) -> int:
        """Rebuild the meaning: every vector goes, and the record of which search model made
        them, **in one transaction**, so no vector of the old model is ever beside one of a
        new one; a document that was `ready` or adding its meaning is `searchable` again,
        owed all of it. Words are not touched. How many passages are owed now."""

        def go(db: sqlite3.Connection) -> int:
            def body() -> int:
                db.execute("UPDATE chunks SET vec = NULL WHERE vec IS NOT NULL")
                db.execute("DELETE FROM meta WHERE key = ?", (EMBEDDER,))
                db.execute(
                    "UPDATE documents SET embedded = 0, refused = NULL, "
                    "status = CASE WHEN status IN ('ready', 'embedding') AND chunks > 0 "
                    "THEN 'searchable' ELSE status END "
                    "WHERE status IN ('searchable', 'embedding', 'ready')"
                )
                return int(
                    db.execute(
                        "SELECT COALESCE(SUM(chunks), 0) FROM documents WHERE status = 'searchable'"
                    ).fetchone()[0]
                )

            owed = self._transaction(db, body)
            self.epoch += 1
            return owed

        return await self._run(go)

    async def reopen_unembedded(self) -> int:
        """Documents that are `ready` with passages that have no meaning (no search model
        was in play when they were read) are `searchable` again, owed it. How many."""

        def go(db: sqlite3.Connection) -> int:
            # Nothing to do is no write: a write makes every cached matrix stale.
            if (
                db.execute(
                    "SELECT 1 FROM documents WHERE status = 'ready' AND chunks > embedded LIMIT 1"
                ).fetchone()
                is None
            ):
                return 0

            def body() -> int:
                return db.execute(
                    "UPDATE documents SET status = 'searchable' "
                    "WHERE status = 'ready' AND chunks > embedded"
                ).rowcount

            return self._transaction(db, body)

        return await self._run(go)

    async def recount_embedded(self) -> int:
        """Boot: each document's count of passages that have their meaning, made again from
        the passages themselves (what is derived heals). How many were wrong."""

        def go(db: sqlite3.Connection) -> int:
            wrong = (
                "SELECT 1 FROM documents WHERE status IN ('searchable', 'embedding', 'ready') "
                "AND embedded != (SELECT COUNT(*) FROM chunks WHERE chunks.doc_id = documents.id "
                "AND chunks.vec IS NOT NULL) LIMIT 1"
            )
            if db.execute(wrong).fetchone() is None:
                return 0

            def body() -> int:
                return db.execute(
                    "UPDATE documents SET embedded = (SELECT COUNT(*) FROM chunks "
                    "WHERE chunks.doc_id = documents.id AND chunks.vec IS NOT NULL) "
                    "WHERE status IN ('searchable', 'embedding', 'ready') AND embedded != "
                    "(SELECT COUNT(*) FROM chunks WHERE chunks.doc_id = documents.id "
                    "AND chunks.vec IS NOT NULL)"
                ).rowcount

            return self._transaction(db, body)

        return await self._run(go)

    async def meaning_counts(self, scope: str | None = None) -> MeaningCounts:
        """How much can be found by meaning too: in one scope (My documents), or in all."""

        def go(db: sqlite3.Connection) -> MeaningCounts:
            where, params = ("", ()) if scope is None else (" AND scope = ?", (scope,))
            row = db.execute(
                "SELECT COALESCE(SUM(chunks), 0) AS passages, COALESCE(SUM(embedded), 0) AS e, "
                "COALESCE(SUM(CASE WHEN status IN ('searchable', 'embedding') "
                "THEN chunks - embedded ELSE 0 END), 0) AS owed, "
                "COALESCE(SUM(CASE WHEN status = 'ready' AND chunks > embedded "
                "THEN chunks - embedded ELSE 0 END), 0) AS words_only "
                f"FROM documents WHERE status IN ('searchable', 'embedding', 'ready'){where}",
                params,
            ).fetchone()
            return MeaningCounts(row["passages"], row["e"], row["owed"], row["words_only"])

        return await self._run(go)

    async def owed_by_document(self) -> list[tuple[str, int]]:
        """Documents waiting for their meaning, in the order they are served, with how many
        passages each is owed: what an estimate of the time left is made from."""

        def go(db: sqlite3.Connection) -> list[tuple[str, int]]:
            rows = db.execute(
                "SELECT id, chunks - embedded AS owed FROM documents "
                "WHERE status IN ('searchable', 'embedding') AND chunks > embedded "
                f"AND scope NOT IN ({_PRIVATE_MARKS}) ORDER BY (scope = 'library'), "
                "added_at, rowid",
                PRIVATE,
            )
            return [(r["id"], r["owed"]) for r in rows]

        return await self._run(go)

    async def load_vectors(
        self, dim: int, clock: Callable[[], float] = time.perf_counter
    ) -> vectors.Matrix:
        """Every vector of a document that can be found, as one matrix (`vectors.assemble`),
        stamped with the writes this index has had, so a later write makes it stale. Raises
        `vectors.BadVector` for a stored vector that is not `dim` numbers."""
        found = (
            "FROM chunks AS c JOIN documents AS d ON d.id = c.doc_id WHERE c.vec IS NOT NULL "
            "AND length(c.vec) > 0 AND d.status IN ('searchable', 'embedding', 'ready')"
        )

        def go(db: sqlite3.Connection) -> vectors.Matrix:
            count = int(db.execute(f"SELECT COUNT(*) {found}").fetchone()[0])
            # No ORDER BY: it would sort every passage with its 4 KB vector in it, and the scan
            # does not need an order (it breaks ties by passage id itself).
            rows = ((r[0], r[1], r[2]) for r in db.execute(f"SELECT c.id, c.vec, d.scope {found}"))
            return vectors.assemble(dim, count, rows, self.writes, clock)

        return await self._run(go)

    async def hits_by_ids(
        self, ids: Sequence[int], scores: Mapping[int, float], scopes: Sequence[str]
    ) -> list[Hit]:
        """These passages, as hits with the similarity each was found with, in the order
        of `ids`, among the documents of these scopes that can be found. A passage that is
        gone (removed since the matrix was read) is left out."""
        if not ids or not scopes:
            return []

        def go(db: sqlite3.Connection) -> list[Hit]:
            marks = ", ".join("?" for _ in ids)
            scope_marks = ", ".join("?" for _ in scopes)
            rows = db.execute(
                "SELECT c.id AS chunk_id, c.doc_id, c.ord, c.text, c.heading_path, c.page_start, "
                "c.page_end, c.line_start, c.line_end, c.char_start, c.char_end, c.tokens_est, "
                "d.name, d.scope, d.kind, 0.0 AS score "
                "FROM chunks AS c JOIN documents AS d ON d.id = c.doc_id "
                f"WHERE c.id IN ({marks}) AND d.scope IN ({scope_marks}) "
                "AND d.status IN ('searchable', 'embedding', 'ready')",
                (*ids, *scopes),
            )
            by_id = {r["chunk_id"]: _hit(r) for r in rows}
            return [replace(by_id[i], score=scores[i]) for i in ids if i in by_id]

        return await self._run(go)

    # --- taking one away ------------------------------------------------

    def _files_of(self, doc_id: str) -> list[Path]:
        """Every file a document can have beside the index: its original, its text, what the
        index says of it, and the halves of each that were being written."""
        return [
            self.original_path(doc_id),
            self.part_path(doc_id),
            self.text_path(doc_id),
            self.text_path(doc_id).with_name(f"{doc_id}.txt.part"),
            self.meta_path(doc_id),
            self.meta_path(doc_id).with_name(f"{doc_id}.meta.json.part"),
        ]

    def _stage_files(self, doc_ids: Sequence[str]) -> list[tuple[Path, Path]]:
        """Take these documents' files out of the way, into `trash/` beside the index, **as
        renames**: quick, and undone by renaming back, so that removing many documents is all or
        nothing, files included (`_unstage` puts them back; `_discard` deletes them once the rows
        are gone). A file that cannot be moved (a Windows program holds it, and is waited for
        briefly) puts back every one that was, and raises `DeleteFailed`."""
        trash = self.dir / TRASH
        staged: list[tuple[Path, Path]] = []
        try:
            for doc_id in doc_ids:
                for path in self._files_of(doc_id):
                    target = trash / path.name
                    for attempt in range(6):
                        try:
                            trash.mkdir(parents=True, exist_ok=True)
                            path.replace(target)
                            staged.append((path, target))
                            break
                        except FileNotFoundError:
                            break  # not there: nothing of it to remove
                        except OSError as exc:
                            if attempt == 5:
                                raise DeleteFailed(
                                    f"Workbench could not remove a file of a document ({exc}). "
                                    "Nothing was removed; close whatever is using it and try again."
                                ) from exc
                            time.sleep(0.1)
        except BaseException:
            self._unstage(staged)
            raise
        return staged

    @staticmethod
    def _unstage(staged: list[tuple[Path, Path]]) -> None:
        """Put staged files back where they were."""
        for path, target in reversed(staged):
            with contextlib.suppress(OSError):
                target.replace(path)

    @staticmethod
    def _discard(staged: list[tuple[Path, Path]]) -> None:
        """Delete staged files for good. Best effort: the documents are already gone, and a file
        that cannot be deleted now is one nothing claims, which the next boot removes."""
        for _, target in staged:
            with contextlib.suppress(OSError):
                target.unlink(missing_ok=True)

    def _remove_files(self, doc_id: str) -> None:
        """A document's files off the disk, inside the transaction that removes its
        rows. A file that is already gone is fine; one that cannot be removed
        raises `DeleteFailed`, which rolls the whole removal back. A program that
        holds a file for a moment (a scanner, a download) is waited out briefly."""
        for path in self._files_of(doc_id):
            for attempt in range(6):
                try:
                    path.unlink(missing_ok=True)
                    break
                except OSError as exc:
                    if attempt == 5:
                        raise DeleteFailed(
                            f"Workbench could not remove a file of this document ({exc}). "
                            "Nothing was removed; close whatever is using it and try again."
                        ) from exc
                    time.sleep(0.1)

    @staticmethod
    def _scrub(db: sqlite3.Connection) -> None:
        """After a removal, leave none of its words in the file. The full-text index
        keeps a word in its segments after the rows that held it are deleted, until
        the segments are merged, so they are (about a second for 100,000 passages);
        then the log is folded into the file and emptied, so the words are not lying
        in it either. Best effort: the removal is already complete and committed."""
        with contextlib.suppress(sqlite3.Error):
            db.execute("INSERT INTO chunks_fts (chunks_fts) VALUES ('optimize')")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    async def delete(self, doc_id: str) -> Document | None:
        """Remove a document for real: its row, its passages, their full-text
        entries, its original and its text, in one transaction. The document as it
        was, or None when it is not here."""

        def go(db: sqlite3.Connection) -> Document | None:
            def body() -> Document | None:
                row = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
                if row is None:
                    return None
                db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                self._remove_files(doc_id)
                return _document(row)

            gone = self._transaction(db, body)
            if gone is not None:
                self._scrub(db)
            return gone

        return await self._run(go)

    async def rescope(
        self, old: str, new: str, *, source_prefix: str | None = None
    ) -> tuple[list[Document], list[Document]]:
        """Move every document of scope `old` to `new` (MV7b: a deleted project's documents to
        My documents), its sidecar too, so a Rebuild makes it again where it is now. Where `new`
        names its files, one of the same name there is replaced, as an upload of that name would
        replace it. All or nothing, files included (`delete_many`'s staging). `(moved,
        replaced)`, as they were."""

        def go(db: sqlite3.Connection) -> tuple[list[Document], list[Document]]:
            staged: list[tuple[Path, Path]] = []
            rewritten: list[tuple[Path, str]] = []

            def body() -> tuple[list[Document], list[Document]]:
                moving = [
                    _document(r)
                    for r in db.execute("SELECT * FROM documents WHERE scope = ?", (old,))
                    if source_prefix is None or str(r["source"]).startswith(source_prefix)
                ]
                replaced: list[Document] = []
                for doc in moving:
                    if named_scope(new) and not doc.source.startswith(FOLDER):
                        twins = [
                            _document(r)
                            for r in db.execute(
                                "SELECT * FROM documents WHERE scope = ? AND name = ? "
                                "COLLATE NOCASE AND source NOT LIKE 'folder:%' AND id != ?",
                                (new, doc.name, doc.id),
                            ).fetchall()
                        ]
                        staged.extend(self._stage_files([t.id for t in twins]))
                        for twin in twins:
                            db.execute("DELETE FROM documents WHERE id = ?", (twin.id,))
                            replaced.append(twin)
                    db.execute("UPDATE documents SET scope = ? WHERE id = ?", (new, doc.id))
                    path = self.meta_path(doc.id)
                    if path.is_file():
                        before = path.read_text(encoding="utf-8")
                        said = json.loads(before)
                        said["scope"] = new
                        part = path.with_name(path.name + ".part")
                        part.write_text(
                            json.dumps(said, ensure_ascii=False), encoding="utf-8", newline="\n"
                        )
                        part.replace(path)
                        rewritten.append((path, before))
                return moving, replaced

            try:
                moved = self._transaction(db, body)
            except BaseException:
                self._unstage(staged)
                for path, before in reversed(rewritten):
                    with contextlib.suppress(OSError):
                        path.write_text(before, encoding="utf-8", newline="\n")
                raise
            self._discard(staged)
            if moved[1]:
                self._scrub(db)
            return moved

        return await self._run(go)

    async def delete_where(self, scope: str, source: str | None = None) -> list[Document]:
        """Remove every document of a scope (a chat's), or the one made from `source`,
        as `delete` does each. The documents as they were."""

        def go(db: sqlite3.Connection) -> list[Document]:
            def body() -> list[Document]:
                sql, params = "SELECT * FROM documents WHERE scope = ?", [scope]
                if source is not None:
                    sql, params = sql + " AND source = ?", [scope, source]
                gone = [_document(r) for r in db.execute(sql, params).fetchall()]
                for doc in gone:
                    db.execute("DELETE FROM documents WHERE id = ?", (doc.id,))
                    self._remove_files(doc.id)
                return gone

            found = self._transaction(db, body)
            if found:
                self._scrub(db)
            return found

        return await self._run(go)

    # --- a person's notes (MV1) -----------------------------------------

    async def put_note(
        self, note_id: str, sha256: str, title: str, passages: Sequence[chunk.Passage]
    ) -> bool:
        """Make the index hold this version of a note: one document in `NOTES`, its passages
        searchable by words at once and owed their meaning. Whatever it held of the note is
        replaced in the same transaction. False when it already holds this version."""

        def go(db: sqlite3.Connection) -> bool:
            def body() -> bool:
                row = db.execute(
                    "SELECT sha256, scope FROM documents WHERE id = ?", (note_id,)
                ).fetchone()
                if row is not None and row["sha256"] == sha256 and row["scope"] == NOTES:
                    return False
                if row is not None and row["scope"] != NOTES:
                    raise IndexDamaged("a note's id is a document's")
                db.execute("DELETE FROM documents WHERE id = ?", (note_id,))
                now = time.time()
                db.execute(
                    "INSERT INTO documents (id, scope, name, media_type, bytes, sha256, source, "
                    "status, kind, added_at, read_at, indexed_at, chunks, tokens_est) "
                    "VALUES (?, ?, ?, 'text/markdown', ?, ?, 'note', 'searchable', 'note', "
                    "?, ?, ?, ?, ?)",
                    (
                        note_id,
                        NOTES,
                        title[:240],
                        sum(len(p.text) for p in passages),
                        sha256,
                        now,
                        now,
                        now,
                        len(passages),
                        sum(p.tokens_est for p in passages),
                    ),
                )
                db.executemany(
                    _INSERT_CHUNK,
                    [
                        (
                            note_id,
                            p.ord,
                            p.heading_path,
                            p.page_start,
                            p.page_end,
                            p.line_start,
                            p.line_end,
                            p.char_start,
                            p.char_end,
                            p.text,
                            p.tokens_est,
                        )
                        for p in passages
                    ],
                )
                return True

            return self._transaction(db, body)

        return await self._run(go)

    async def note_digests(self) -> dict[str, str]:
        """Each note the index holds, by id: the `sha256` of the version it holds."""

        def go(db: sqlite3.Connection) -> dict[str, str]:
            return {
                r["id"]: r["sha256"]
                for r in db.execute("SELECT id, sha256 FROM documents WHERE scope = ?", (NOTES,))
            }

        return await self._run(go)

    async def delete_notes(self, note_ids: Sequence[str]) -> int:
        """Take these notes out of the index for real, in one transaction: their passages,
        those passages' entries in the words index (merged, so no deleted word is kept in a
        segment) and their vectors. How many it held."""
        ids = list(dict.fromkeys(note_ids))
        if not ids:
            return 0

        def go(db: sqlite3.Connection) -> int:
            def body() -> int:
                gone = 0
                for start in range(0, len(ids), 500):
                    part = ids[start : start + 500]
                    marks = ", ".join("?" for _ in part)
                    gone += db.execute(
                        f"DELETE FROM documents WHERE scope = ? AND id IN ({marks})",
                        (NOTES, *part),
                    ).rowcount
                return gone

            count = self._transaction(db, body)
            if count:
                self._scrub(db)
            return int(count)

        return await self._run(go)

    # --- past chats (MV2) ---------------------------------------------------

    async def put_exchange(
        self,
        exchange_id: str,
        chat_id: str,
        sha256: str,
        title: str,
        passages: Sequence[chunk.Passage],
    ) -> bool:
        """Make the index hold this version of an exchange: one document in `EXCHANGES` by
        the answer's id, its `source` the chat's id, its passages searchable by words at once
        and owed their meaning. False when it already holds this version."""

        def go(db: sqlite3.Connection) -> bool:
            def body() -> bool:
                row = db.execute(
                    "SELECT sha256, scope, source FROM documents WHERE id = ?", (exchange_id,)
                ).fetchone()
                if row is not None and row["scope"] != EXCHANGES:
                    raise IndexDamaged("an exchange's id is a document's")
                if row is not None and row["sha256"] == sha256 and row["source"] == chat_id:
                    return False
                db.execute("DELETE FROM documents WHERE id = ?", (exchange_id,))
                now = time.time()
                db.execute(
                    "INSERT INTO documents (id, scope, name, media_type, bytes, sha256, source, "
                    "status, kind, added_at, read_at, indexed_at, chunks, tokens_est) "
                    "VALUES (?, ?, ?, 'text/markdown', ?, ?, ?, 'searchable', 'exchange', "
                    "?, ?, ?, ?, ?)",
                    (
                        exchange_id,
                        EXCHANGES,
                        title[:240],
                        sum(len(p.text) for p in passages),
                        sha256,
                        chat_id,
                        now,
                        now,
                        now,
                        len(passages),
                        sum(p.tokens_est for p in passages),
                    ),
                )
                db.executemany(
                    _INSERT_CHUNK,
                    [
                        (
                            exchange_id,
                            p.ord,
                            p.heading_path,
                            p.page_start,
                            p.page_end,
                            p.line_start,
                            p.line_end,
                            p.char_start,
                            p.char_end,
                            p.text,
                            p.tokens_est,
                        )
                        for p in passages
                    ],
                )
                return True

            return self._transaction(db, body)

        return await self._run(go)

    async def exchange_digests(self, chat_id: str | None = None) -> dict[str, tuple[str, str]]:
        """Each exchange the index holds (of one chat, or every one), by id: `(chat id,
        sha256)`."""

        def go(db: sqlite3.Connection) -> dict[str, tuple[str, str]]:
            sql, params = "SELECT id, source, sha256 FROM documents WHERE scope = ?", [EXCHANGES]
            if chat_id is not None:
                sql, params = sql + " AND source = ?", [EXCHANGES, chat_id]
            return {r["id"]: (r["source"], r["sha256"]) for r in db.execute(sql, params)}

        return await self._run(go)

    async def exchange_count(self, *, besides: str | None = None) -> int:
        """How many exchanges the index holds, leaving out one chat's."""

        def go(db: sqlite3.Connection) -> int:
            return int(
                db.execute(
                    "SELECT COUNT(*) FROM documents WHERE scope = ? AND source != ?",
                    (EXCHANGES, besides or ""),
                ).fetchone()[0]
            )

        return await self._run(go)

    async def signatures(self) -> dict[str, str]:
        """Each chat's signature as it was when its exchanges were last put, by chat id."""

        def go(db: sqlite3.Connection) -> dict[str, str]:
            return {
                r["key"][len(SIGNATURE) :]: r["value"]
                for r in db.execute(
                    "SELECT key, value FROM meta WHERE substr(key, 1, ?) = ?",
                    (len(SIGNATURE), SIGNATURE),
                )
            }

        return await self._run(go)

    async def delete_exchanges(
        self,
        *,
        chat_id: str | None = None,
        ids: Sequence[str] = (),
        signature: str | None = None,
    ) -> int:
        """Take exchanges out of the index for real, in one transaction: every one of a chat
        (and its signature), or these. With `chat_id` and `signature`, the chat's signature
        is kept as that in the same transaction (a sync); with `chat_id` alone, it goes. How
        many it held."""
        wanted = list(dict.fromkeys(ids))

        def go(db: sqlite3.Connection) -> int:
            def body() -> int:
                gone = 0
                if chat_id is not None and signature is None:
                    gone += db.execute(
                        "DELETE FROM documents WHERE scope = ? AND source = ?",
                        (EXCHANGES, chat_id),
                    ).rowcount
                for start in range(0, len(wanted), 500):
                    part = wanted[start : start + 500]
                    marks = ", ".join("?" for _ in part)
                    gone += db.execute(
                        f"DELETE FROM documents WHERE scope = ? AND id IN ({marks})",
                        (EXCHANGES, *part),
                    ).rowcount
                if chat_id is not None:
                    if signature is None:
                        db.execute("DELETE FROM meta WHERE key = ?", (SIGNATURE + chat_id,))
                    else:
                        db.execute(
                            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                            (SIGNATURE + chat_id, signature),
                        )
                return gone

            count = self._transaction(db, body)
            if count:
                self._scrub(db)
            return int(count)

        return await self._run(go)

    # --- a granted folder's files (MR7) -----------------------------------

    async def delete_many(self, doc_ids: Sequence[str]) -> list[Document]:
        """Remove these documents as `delete` does each, **in one transaction**: rows, passages,
        search entries, originals and texts, so a file that cannot be removed rolls all of it
        back. The documents as they were (those that were there)."""
        ids = list(dict.fromkeys(doc_ids))
        if not ids:
            return []

        def go(db: sqlite3.Connection) -> list[Document]:
            staged: list[tuple[Path, Path]] = []

            def body() -> list[Document]:
                gone: list[Document] = []
                for start in range(0, len(ids), 500):
                    part = ids[start : start + 500]
                    marks = ", ".join("?" for _ in part)
                    for row in db.execute(
                        f"SELECT * FROM documents WHERE id IN ({marks})", part
                    ).fetchall():
                        gone.append(_document(row))
                staged.extend(self._stage_files([d.id for d in gone]))
                for doc in gone:
                    db.execute("DELETE FROM documents WHERE id = ?", (doc.id,))
                return gone

            try:
                found = self._transaction(db, body)
            except BaseException:
                self._unstage(staged)
                raise
            self._discard(staged)
            if any(d.chunks for d in found):
                self._scrub(db)
            return found

        return await self._run(go)

    async def delete_folder(self, grant_id: str) -> list[Document]:
        """Remove every document read from a folder, as `delete_many` does. The documents as they
        were."""
        low, high = _bounds(grant_id)

        def go(db: sqlite3.Connection) -> list[Document]:
            staged: list[tuple[Path, Path]] = []

            def body() -> list[Document]:
                gone = [
                    _document(r)
                    for r in db.execute(
                        "SELECT * FROM documents WHERE source >= ? AND source < ?", (low, high)
                    ).fetchall()
                ]
                staged.extend(self._stage_files([d.id for d in gone]))
                for doc in gone:
                    db.execute("DELETE FROM documents WHERE id = ?", (doc.id,))
                return gone

            try:
                found = self._transaction(db, body)
            except BaseException:
                self._unstage(staged)
                raise
            self._discard(staged)
            if any(d.chunks for d in found):
                self._scrub(db)
            return found

        return await self._run(go)

    async def folder_files(self, grant_id: str) -> list[Held]:
        """What the index holds of a folder, one entry for each of its files, by path."""
        low, high = _bounds(grant_id)

        def go(db: sqlite3.Connection) -> list[Held]:
            cut = len(low)
            rows = db.execute(
                "SELECT id, source, bytes, source_mtime, sha256, status, code FROM documents "
                "WHERE source >= ? AND source < ?",
                (low, high),
            )
            return [
                Held(
                    r["id"],
                    r["source"][cut:],
                    r["bytes"],
                    r["source_mtime"],
                    r["sha256"],
                    r["status"],
                    r["code"],
                )
                for r in rows
            ]

        return await self._run(go)

    async def folder_scopes(self, grant_id: str) -> set[str]:
        """The actual scopes of a folder, to reconcile an interrupted move from its record."""
        low, high = _bounds(grant_id)
        return await self._run(
            lambda db: {
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT scope FROM documents WHERE source >= ? AND source < ?",
                    (low, high),
                )
            }
        )

    async def folder_grants(self) -> set[str]:
        """The ids of the grants any document was read from: what the boot reconcile checks against
        the folders people have."""

        def go(db: sqlite3.Connection) -> set[str]:
            rows = db.execute(
                "SELECT DISTINCT substr(source, 8, instr(substr(source, 8), '/') - 1) "
                "FROM documents WHERE source >= 'folder:' AND source < 'folder;'"
            )
            return {r[0] for r in rows if r[0]}

        return await self._run(go)

    async def folder_counts(self, grant_id: str) -> FolderCounts:
        low, high = _bounds(grant_id)

        def go(db: sqlite3.Connection) -> FolderCounts:
            by_state = dict.fromkeys(STATES, 0)
            by_code: dict[str, int] = {}
            files = passages = size = copied = embedded = owed = 0
            for row in db.execute(
                "SELECT status, code, COUNT(*) AS n, COALESCE(SUM(chunks), 0) AS c, "
                "COALESCE(SUM(bytes), 0) AS b, COALESCE(SUM(embedded), 0) AS e, "
                "COALESCE(SUM(CASE WHEN sha256 != '' THEN bytes ELSE 0 END), 0) AS k "
                "FROM documents WHERE source >= ? AND source < ? GROUP BY status, code",
                (low, high),
            ):
                by_state[row["status"]] += row["n"]
                if row["status"] in (SKIPPED, FAILED) and row["code"]:
                    by_code[row["code"]] = by_code.get(row["code"], 0) + row["n"]
                files += row["n"]
                passages += row["c"]
                size += row["b"]
                copied += row["k"]
                embedded += row["e"]
                if row["status"] in (SEARCHABLE, EMBEDDING):
                    owed += row["c"] - row["e"]
            return FolderCounts(files, passages, embedded, owed, size, copied, by_state, by_code)

        return await self._run(go)

    async def folder_documents(
        self,
        grant_id: str,
        *,
        state: str | None = None,
        code: str | None = None,
        match: str | None = None,
        offset: int = 0,
        limit: int = 200,
    ) -> tuple[list[Document], int]:
        """A page of a folder's documents in path order, and how many there are in all (that fit
        the filters): by state, by the reason they were skipped or failed, and by a word in their
        path (without regard to case)."""
        low, high = _bounds(grant_id)

        def go(db: sqlite3.Connection) -> tuple[list[Document], int]:
            where = "source >= ? AND source < ?"
            params: list[str | int] = [low, high]
            if state is not None:
                where, params = where + " AND status = ?", [*params, state]
            if code is not None:
                where, params = where + " AND code = ?", [*params, code]
            if match:
                where += " AND instr(lower(substr(source, ?)), ?) > 0"
                params = [*params, len(low) + 1, match.lower()]
            total = int(
                db.execute(f"SELECT COUNT(*) FROM documents WHERE {where}", params).fetchone()[0]
            )
            rows = db.execute(
                f"SELECT * FROM documents WHERE {where} "
                "ORDER BY lower(source), source LIMIT ? OFFSET ?",
                [*params, limit, offset],
            )
            return [_document(r) for r in rows], total

        return await self._run(go)

    async def put_folder_skips(self, rows: Sequence[FolderSkip]) -> int:
        """Keep these files of a folder as `skipped` (or `failed`) documents with their reasons,
        **in one transaction**, each in place of whatever document its path had (and its
        passages and files). A write makes every cached matrix of the vectors stale, so a
        batch is one write. How many were kept."""
        if not rows:
            return 0

        def go(db: sqlite3.Connection) -> int:
            staged: list[tuple[Path, Path]] = []

            def body() -> int:
                removed_passages = 0
                for skip in rows:
                    new = skip.new
                    for old in db.execute(
                        "SELECT id, chunks FROM documents WHERE source = ?", (new.source,)
                    ).fetchall():
                        removed_passages += old["chunks"]
                        staged.extend(self._stage_files([old["id"]]))
                        db.execute("DELETE FROM documents WHERE id = ?", (old["id"],))
                    db.execute(
                        "INSERT INTO documents (id, scope, name, media_type, bytes, sha256, "
                        "source, status, reason, code, detail, kind, language, added_at, "
                        "source_mtime) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            new.id,
                            new.scope,
                            new.name,
                            new.media_type,
                            new.bytes,
                            new.source,
                            skip.state,
                            skip.reason,
                            skip.code,
                            skip.detail,
                            new.kind,
                            new.language,
                            new.added_at if new.added_at is not None else time.time(),
                            new.source_mtime,
                        ),
                    )
                return removed_passages

            try:
                removed = self._transaction(db, body)
            except BaseException:
                self._unstage(staged)
                raise
            self._discard(staged)
            if removed:
                self._scrub(db)
            return len(rows)

        return await self._run(go)

    async def set_signature(self, doc_id: str, mtime: str | None, size: int) -> bool:
        """A file read again came to the same words: keep the time and size it is at now, so it is
        not read again until it changes. False when the document is not there."""

        def go(db: sqlite3.Connection) -> bool:
            def body() -> bool:
                return (
                    db.execute(
                        "UPDATE documents SET source_mtime = ?, bytes = ? WHERE id = ?",
                        (mtime, size, doc_id),
                    ).rowcount
                    == 1
                )

            return self._transaction(db, body)

        return await self._run(go)

    async def move_folder_document(
        self, doc_id: str, old_source: str, source: str, name: str, mtime: str | None
    ) -> Document | None:
        """A file of a folder moved: its document is the same one, with its passages and their
        vectors, under its new path. None when it is not there, or is not at `old_source` any
        more."""

        def go(db: sqlite3.Connection) -> Document | None:
            def body() -> Document | None:
                changed = db.execute(
                    "UPDATE documents SET source = ?, name = ?, source_mtime = ? "
                    "WHERE id = ? AND source = ?",
                    (source, name, mtime, doc_id, old_source),
                ).rowcount
                if changed != 1:
                    return None
                return _document(
                    db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
                )

            return self._transaction(db, body)

        return await self._run(go)

    # --- finding ---------------------------------------------------------

    async def search(self, match: str, scopes: Sequence[str], limit: int) -> list[Hit]:
        """The passages, best first by BM25, that match an FTS5 expression, among the
        documents of these scopes that can be found. **The scope is part of the query
        that ranks**, never a filter on its result: a passage of another chat's file is
        never a candidate, so it cannot take a place a passage of this chat's file
        should have had. An empty list is a search that found nothing; a search that
        could not run raises, and is never that."""
        if not scopes:
            return []

        def go(db: sqlite3.Connection) -> list[Hit]:
            marks = ", ".join("?" for _ in scopes)
            rows = db.execute(_SEARCH.format(scopes=marks), (match, *scopes, limit))
            return [_hit(r) for r in rows]

        return await self._run(go)

    async def passages(self, doc_id: str, ids: Sequence[int]) -> list[Hit]:
        """These passages of one document, in reading order, as stored. A passage
        that is not there (the document was read again) is not returned."""
        if not ids:
            return []

        def go(db: sqlite3.Connection) -> list[Hit]:
            marks = ", ".join("?" for _ in ids)
            rows = db.execute(
                "SELECT c.id AS chunk_id, c.doc_id, c.ord, c.text, c.heading_path, c.page_start, "
                "c.page_end, c.line_start, c.line_end, c.char_start, c.char_end, c.tokens_est, "
                "d.name, d.scope, d.kind, 0.0 AS score "
                "FROM chunks AS c JOIN documents AS d ON d.id = c.doc_id "
                f"WHERE c.doc_id = ? AND c.id IN ({marks}) ORDER BY c.ord",
                (doc_id, *ids),
            )
            return [_hit(r) for r in rows]

        return await self._run(go)

    async def check(self) -> list[str]:
        """What is wrong with the index, if anything: SQLite's own integrity check,
        and that the full-text index agrees with the passages. Empty when sound."""

        def go(db: sqlite3.Connection) -> list[str]:
            problems = [str(r[0]) for r in db.execute("PRAGMA integrity_check") if r[0] != "ok"]
            try:
                db.execute(
                    "INSERT INTO chunks_fts (chunks_fts, rank) VALUES ('integrity-check', 1)"
                )
            except sqlite3.DatabaseError as exc:
                problems.append(f"full-text index: {exc}")
            return problems

        return await self._run(go)

    # --- the files beside the index --------------------------------------

    async def known_ids(self) -> set[str]:
        def go(db: sqlite3.Connection) -> set[str]:
            return {r[0] for r in db.execute("SELECT id FROM documents")}

        return await self._run(go)

    async def orphans(self) -> list[Path]:
        """Files beside the index that no document row claims: an original or a text
        whose document is gone, and any half-written file. What a stop between the
        two steps of an upload, or a removal that could not finish, leaves behind."""
        ids = await self.known_ids()

        def scan() -> list[Path]:
            found: list[Path] = []
            trash = self.dir / TRASH
            if trash.is_dir():
                found.extend(trash.iterdir())  # files that were being removed when a stop came
            if self.originals.is_dir():
                for path in self.originals.iterdir():
                    stem = path.name.removesuffix(".part")
                    if valid_id(stem) and (stem not in ids or path.name.endswith(".part")):
                        found.append(path)
            if self.dir.is_dir():
                for path in self.dir.iterdir():
                    name = path.name
                    if not name.endswith((".txt", ".txt.part", ".meta.json", ".meta.json.part")):
                        continue
                    stem = (
                        name.removesuffix(".part").removesuffix(".txt").removesuffix(".meta.json")
                    )
                    if valid_id(stem) and (stem not in ids or name.endswith(".part")):
                        found.append(path)
            return found

        return await asyncio.to_thread(scan)


# --------------------------------------------------------------------------- #
# every person's index
# --------------------------------------------------------------------------- #


class Indexes:
    """The indexes under a data directory, one per person, opened when first used."""

    def __init__(self, root: Path, *, workers: int = 4) -> None:
        self.root = root / "knowledge"
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wb-index")
        self._open: dict[str, Index] = {}
        #: Row 7: people removed from Eugene, by `sub` and by their directory's name. Their
        #: index is not opened, made, or walked by a job that visits every person's.
        self._refused: set[str] = set()
        self._refused_keys: set[str] = set()

    def dir_of(self, sub: str) -> Path:
        return self.root / files.person_key(sub)

    def refuse(self, sub: str) -> None:
        """The person was removed from Eugene: their index is not opened or made again."""
        self._refused.add(sub)
        self._refused_keys.add(files.person_key(sub))

    def allow(self, sub: str) -> None:
        """Eugene has the person again: their index is theirs again."""
        self._refused.discard(sub)
        self._refused_keys.discard(files.person_key(sub))

    def refused(self, sub: str) -> bool:
        return sub in self._refused

    async def forget(self, sub: str) -> None:
        """Close the person's index for good and let it go, so their directory can be removed
        (Windows does not remove a file that is open). A job that still holds it cannot open it
        again (`Index.retired`)."""
        found = self._open.pop(sub, None)
        if found is not None:
            found.retired = True
            await found.close()

    def get(self, sub: str, *, create: bool = True) -> Index | None:
        """The person's index. With `create=False`, None when they have none yet:
        reading a person's documents never makes a directory for them. A removed person's
        (row 7) is None to read and refused to make."""
        if sub in self._refused:
            if not create:
                return None
            raise IndexUnavailable(REMOVED, code="removed")
        found = self._open.get(sub)
        if found is None:
            directory = self.dir_of(sub)
            if not create and not (directory / INDEX_FILE).is_file():
                return None
            found = self._open[sub] = Index(sub, directory, self._executor)
        elif not create and not found.path.is_file():
            return None  # asked for by a request that may not make it
        return found

    def person_dirs(self) -> list[Path]:
        """Every person's directory that holds an index, but a removed person's."""
        if not self.root.is_dir():
            return []
        return sorted(
            p
            for p in self.root.iterdir()
            if p.name not in self._refused_keys and (p / INDEX_FILE).is_file()
        )

    @staticmethod
    def owner_of(directory: Path) -> str | None:
        """Whose index a directory holds, read straight from it. None when it
        cannot be read (damaged, or not one): the caller leaves such a directory alone."""
        try:
            db = sqlite3.connect(f"file:{directory / INDEX_FILE}?mode=ro", uri=True, timeout=5.0)
        except sqlite3.Error:
            return None
        try:
            row = db.execute("SELECT value FROM meta WHERE key = 'owner'").fetchone()
        except sqlite3.Error:
            return None
        finally:
            db.close()
        return str(row[0]) if row is not None else None

    async def aclose(self) -> None:
        for index in list(self._open.values()):
            await index.close()
        self._open.clear()
        self._executor.shutdown(wait=True)
