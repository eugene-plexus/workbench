"""A person's index (MR4, design 4.1 and 4.9): one SQLite file each, owned, versioned,
and honest. Real files in a temporary data directory; nothing here is a stand-in.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import files
from eugene_plexus_workbench.knowledge import chunk, extract
from eugene_plexus_workbench.knowledge import index as index_module
from eugene_plexus_workbench.knowledge.index import (
    LIBRARY,
    DeleteFailed,
    Document,
    Index,
    IndexDamaged,
    Indexes,
    IndexUnavailable,
    NewDocument,
    chat_scope,
)

ADA, BO = "p-ada", "p-bo"
RARE = "zzyzxquux"


@pytest.fixture
async def indexes(tmp_path: Path) -> AsyncIterator[Indexes]:
    found = Indexes(tmp_path / "data")
    try:
        yield found
    finally:
        await found.aclose()


def passages_of(text: str) -> list[chunk.Passage]:
    return chunk.chunk(
        extract.Extracted(
            kind="markdown",
            parts=[extract.Part(text)],
            lines=extract.line_count(text),
            headings=extract.markdown_headings(text),
        )
    )


def summary(passages: list[chunk.Passage], kind: str = "markdown") -> dict[str, Any]:
    return {"kind": kind, "pages": None, "lines": 9, "tokens": 40, "notes": []}


def new(
    doc_id: str, name: str = "notes.md", scope: str = LIBRARY, sha: str | None = None, **kw: Any
) -> NewDocument:
    return NewDocument(
        id=doc_id,
        scope=scope,
        name=name,
        media_type="text/markdown",
        bytes=10,
        sha256=sha or hashlib.sha256(f"{doc_id}{name}".encode()).hexdigest(),
        source=kw.pop("source", "upload"),
        kind="markdown",
        **kw,
    )


async def put(
    index: Index,
    doc_id: str,
    text: str,
    *,
    name: str = "notes.md",
    scope: str = LIBRARY,
    ready: bool = True,
    source: str = "upload",
    sha: str | None = None,
) -> Document:
    """A document read and searchable: its original and text on the disk, its passages in."""
    added = await index.add(new(doc_id, name, scope, sha, source=source))
    assert not added.unchanged
    index.original_path(doc_id).parent.mkdir(parents=True, exist_ok=True)
    index.original_path(doc_id).write_text(text, encoding="utf-8")
    index.text_path(doc_id).write_text(text, encoding="utf-8")
    claimed = await index.claim()
    while claimed is not None and claimed.id != doc_id:  # others waiting before it
        claimed = await index.claim()
    assert claimed is not None
    got = passages_of(text)
    done = await index.commit_reading(doc_id, claimed.attempt, summary(got), got, cap=10**6)
    assert done == "searchable"
    if ready:
        assert await index.finish(doc_id)
    found = await index.document(doc_id)
    assert found is not None
    return found


def raw(index: Index) -> sqlite3.Connection:
    """A second connection to the same file, as another program would have."""
    return sqlite3.connect(index.path)


async def words(index: Index, word: str, *scopes: str) -> list[str]:
    return [h.doc_id for h in await index.search(f'"{word}"', scopes or [LIBRARY], 20)]


# --------------------------------------------------------------------------- #
# what an index is
# --------------------------------------------------------------------------- #


async def test_a_new_index_is_versioned_owned_and_records_how_its_passages_were_made(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    assert await index.documents() == []
    with raw(index) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (index_module.SCHEMA_VERSION,)
        assert db.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        meta = dict(db.execute("SELECT key, value FROM meta"))
    assert meta["owner"] == ADA
    assert '"structure-first"' in meta["chunker"] and '"version": 1' in meta["chunker"]


async def test_foreign_keys_are_on_so_a_passage_cannot_outlive_its_document(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    with pytest.raises(sqlite3.IntegrityError):
        await index._run(
            lambda db: db.execute(
                "INSERT INTO chunks (doc_id, ord, text, tokens_est) VALUES ('nope', 0, 'x', 1)"
            )
        )


async def test_a_persons_directory_is_named_by_a_hash_of_their_sub_and_nobody_elses(
    indexes: Indexes,
) -> None:
    ada, bo = indexes.get(ADA), indexes.get(BO)
    assert ada is not None and bo is not None
    key = hashlib.sha256(ADA.encode()).hexdigest()[:32]
    assert ada.dir == indexes.root / key == indexes.root / files.person_key(ADA)
    assert ada.dir != bo.dir
    assert ada.path == ada.dir / "index.sqlite3"
    assert ADA not in str(ada.dir), "the sub may hold anything; it is not in the path"


async def test_looking_at_a_persons_documents_makes_no_directory_for_them(
    indexes: Indexes,
) -> None:
    assert indexes.get(ADA, create=False) is None
    assert not indexes.root.exists()
    assert indexes.person_dirs() == []


async def test_an_index_refuses_to_open_for_anyone_but_its_owner(
    indexes: Indexes, tmp_path: Path
) -> None:
    ada = indexes.get(ADA)
    assert ada is not None
    await put(ada, "doc00001", f"The secret {RARE} is here.")
    await ada.close()
    stolen = indexes.dir_of(BO)
    shutil.copytree(ada.dir, stolen)
    thief = Index(BO, stolen, indexes._executor)
    with pytest.raises(IndexDamaged, match="belongs to someone else"):
        await thief.documents()
    with pytest.raises(IndexDamaged):
        await thief.search(f'"{RARE}"', [LIBRARY], 10)


async def test_an_index_from_a_newer_workbench_is_refused_and_left_as_it_is(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await index.documents()
    await index.close()
    with raw(index) as db:
        db.execute(f"PRAGMA user_version = {index_module.SCHEMA_VERSION + 4}")
    before = index.path.read_bytes()
    again = Index(ADA, index.dir, indexes._executor)
    with pytest.raises(IndexUnavailable, match="newer Workbench") as caught:
        await again.documents()
    assert caught.value.code == "index_newer"
    assert index.path.read_bytes() == before


async def test_a_migration_brings_an_older_index_forward_and_keeps_its_documents(
    indexes: Indexes, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", f"The {RARE} word.")
    await index.close()
    monkeypatch.setattr(index_module, "SCHEMA_VERSION", 2)
    monkeypatch.setitem(
        index_module._MIGRATIONS, 2, ["ALTER TABLE documents ADD COLUMN later TEXT"]
    )
    newer = Index(ADA, index.dir, indexes._executor)
    docs = await newer.documents()
    assert [d.id for d in docs] == ["doc00001"]
    assert await words(newer, RARE) == ["doc00001"]
    with raw(newer) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert "later" in {r[1] for r in db.execute("PRAGMA table_info(documents)")}
    await newer.close()
    # A second boot does not run it again (ALTER TABLE would fail).
    again = Index(ADA, index.dir, indexes._executor)
    assert [d.id for d in await again.documents()] == ["doc00001"]
    await again.close()


async def test_a_file_that_is_not_an_index_is_said_to_be_damaged_in_words(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    index.dir.mkdir(parents=True)
    index.path.write_bytes(b"this is not a database " * 200)
    with pytest.raises(IndexDamaged) as caught:
        await index.documents()
    assert "damaged (SQLite said:" in caught.value.message
    assert "Your files are safe." in caught.value.message
    assert caught.value.code == "index_damaged"


async def test_a_search_of_an_index_that_lost_its_full_text_table_raises_and_is_never_empty(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", f"The {RARE} word.")
    assert await words(index, RARE) == ["doc00001"]
    with raw(index) as db:
        db.execute("DROP TABLE chunks_fts")
    with pytest.raises(IndexDamaged, match="no such table"):
        await words(index, RARE)


def test_a_python_without_full_text_search_is_said_in_words() -> None:
    said = Index._explain(sqlite3.OperationalError("no such module: fts5"))
    assert isinstance(said, IndexUnavailable)
    assert said.message == index_module.NO_FTS and said.code == "fts_missing"
    assert "Reinstall Workbench" in said.message


@pytest.mark.parametrize(
    ("said", "code"),
    [
        ("database or disk is full", "disk_full"),
        ("disk I/O error", "unreachable"),
        ("database is locked", "busy"),
        ("database disk image is malformed", "index_damaged"),
    ],
)
def test_what_sqlite_says_decides_whether_an_index_is_damaged(said: str, code: str) -> None:
    got = Index._explain(sqlite3.DatabaseError(said))
    assert isinstance(got, IndexUnavailable) and got.code == code
    assert (code == "index_damaged") == isinstance(got, IndexDamaged)


# --------------------------------------------------------------------------- #
# bringing documents in
# --------------------------------------------------------------------------- #


async def test_a_document_is_queued_with_no_passages_until_it_is_read(indexes: Indexes) -> None:
    index = indexes.get(ADA)
    assert index is not None
    added = await index.add(new("doc00001"))
    doc = added.document
    assert (doc.status, doc.chunks, doc.embedded, doc.attempt) == ("queued", 0, 0, 0)
    assert doc.scope == LIBRARY and doc.added_at > 0 and doc.read_at is None
    assert not doc.searchable and added.replaced == [] and not added.unchanged


async def test_the_same_name_replaces_the_old_document_at_once_with_its_words_and_files(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    old = await put(index, "doc00001", f"The old {RARE} words.", name="Report.md")
    assert await words(index, RARE) == ["doc00001"]
    added = await index.add(new("doc00002", "report.MD"))  # case apart
    assert [d.id for d in added.replaced] == [old.id] and not added.unchanged
    assert await words(index, RARE) == [], "its old words are never found again"
    assert [d.id for d in await index.documents(LIBRARY)] == ["doc00002"]
    assert not index.original_path("doc00001").exists()
    assert not index.text_path("doc00001").exists()
    with raw(index) as db:
        assert db.execute("SELECT COUNT(*) FROM chunks").fetchone() == (0,)


async def test_the_same_bytes_under_the_same_name_change_nothing(indexes: Indexes) -> None:
    index = indexes.get(ADA)
    assert index is not None
    same = hashlib.sha256(b"same").hexdigest()
    first = await put(index, "doc00001", "Some words.", sha=same)
    again = await index.add(new("doc00002", "notes.md", sha=same))
    assert again.unchanged and again.document.id == first.id and again.replaced == []
    assert [d.id for d in await index.documents()] == ["doc00001"]
    assert (await index.document("doc00001")).status == "ready"  # type: ignore[union-attr]


async def test_a_document_that_failed_is_read_again_when_the_same_file_is_added_again(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    same = hashlib.sha256(b"same").hexdigest()
    await index.add(new("doc00001", sha=same))
    claimed = await index.claim()
    assert claimed is not None
    assert await index.end_unread(
        "doc00001", claimed.attempt, state="failed", code="damaged", reason="Failed: x"
    )
    again = await index.add(new("doc00002", sha=same))
    assert not again.unchanged and [d.id for d in again.replaced] == ["doc00001"]


async def test_a_chat_document_is_the_same_document_while_its_source_is(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    scope = chat_scope("chat1")
    first = await index.add(new("doc00001", "a.pdf", scope, source="attachment:f1"))
    again = await index.add(new("doc00002", "a.pdf", scope, source="attachment:f1", sha="x"))
    assert again.unchanged and again.document.id == first.document.id
    other = await index.add(new("doc00003", "a.pdf", scope, source="attachment:f2"))
    assert not other.unchanged
    assert {d.id for d in await index.documents(scope)} == {"doc00001", "doc00003"}
    assert await index.documents(LIBRARY) == [], "a chat's files are not in My documents"


async def test_the_document_waiting_longest_is_read_first_and_a_chats_file_before_the_library(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await index.add(new("doc00001", "one.md"))
    await asyncio.sleep(0.01)
    await index.add(new("doc00002", "two.md"))
    await asyncio.sleep(0.01)
    await index.add(new("doc00003", "chat.md", chat_scope("c1"), source="attachment:f1"))
    order = []
    while (claimed := await index.claim()) is not None:
        order.append((claimed.id, claimed.status))
    assert order == [
        ("doc00003", "reading"),
        ("doc00001", "reading"),
        ("doc00002", "reading"),
    ]
    assert await index.claim() is None


# --------------------------------------------------------------------------- #
# what each state means
# --------------------------------------------------------------------------- #


async def test_reading_a_document_writes_its_passages_and_its_state_together(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await index.add(new("doc00001"))
    claimed = await index.claim()
    assert claimed is not None and claimed.status == "reading" and claimed.read_at is not None
    assert await words(index, RARE) == []
    text = f"# Part\n\nThe {RARE} appears here.\n\n# Other\n\nSomething else.\n"
    got = passages_of(text)
    done = await index.commit_reading(
        "doc00001",
        claimed.attempt,
        {"kind": "markdown", "pages": None, "lines": 7, "tokens": 30, "notes": ["a note"]},
        got,
        cap=1000,
    )
    assert done == "searchable"
    doc = await index.document("doc00001")
    assert doc is not None
    assert (doc.status, doc.chunks, doc.lines, doc.tokens_est, doc.notes) == (
        "searchable",
        len(got),
        7,
        30,
        ["a note"],
    )
    assert doc.indexed_at is not None and doc.searchable
    assert await words(index, RARE) == ["doc00001"]
    assert await index.finish("doc00001")
    assert (await index.document("doc00001")).status == "ready"  # type: ignore[union-attr]
    assert not await index.finish("doc00001"), "ready is ready once"


async def test_a_reading_that_finds_its_document_removed_or_read_again_keeps_nothing(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await index.add(new("doc00001"))
    claimed = await index.claim()
    assert claimed is not None
    got = passages_of(f"The {RARE} word.")
    assert await index.delete("doc00001") is not None
    assert (
        await index.commit_reading("doc00001", claimed.attempt, summary(got), got, cap=9) == "gone"
    )
    await index.add(new("doc00002", "other.md"))
    again = await index.claim()
    assert again is not None
    await index.requeue("doc00002")  # read again while it is being read: a new attempt
    assert await index.commit_reading("doc00002", again.attempt, summary(got), got, cap=9) == "gone"
    assert await words(index, RARE) == []
    assert (await index.document("doc00002")).status == "queued"  # type: ignore[union-attr]


async def test_a_reading_from_an_earlier_attempt_cannot_commit_over_a_later_one(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await index.add(new("doc00001"))
    first = await index.claim()  # attempt 0
    assert first is not None and first.attempt == 0
    assert await index.requeue_interrupted() == 1  # a stop: queued again, a new attempt
    second = await index.claim()  # reading again, as attempt 1
    assert second is not None and second.attempt == 1 and second.status == "reading"
    got = passages_of(f"The {RARE} word.")
    stale = await index.commit_reading("doc00001", first.attempt, summary(got), got, cap=9)
    assert stale == "gone" and await words(index, RARE) == [], "the old reading keeps nothing"
    fresh = await index.commit_reading("doc00001", second.attempt, summary(got), got, cap=9)
    assert fresh == "searchable" and await words(index, RARE) == ["doc00001"]


async def test_a_document_in_a_state_that_cannot_be_found_has_no_passages(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    ready = await put(index, "doc00001", f"The {RARE} word.")
    assert ready.chunks > 0 and await words(index, RARE) == ["doc00001"]

    async def count() -> int:
        with raw(index) as db:
            return int(db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

    again = await index.requeue("doc00001")
    assert again is not None and again.status == "queued" and again.attempt == 1
    assert (again.chunks, again.embedded, again.reason, again.indexed_at) == (0, 0, None, None)
    assert await count() == 0 and await words(index, RARE) == [], "old reading gone at once"
    claimed = await index.claim()
    assert claimed is not None and claimed.attempt == 1
    assert await count() == 0, "reading holds none"
    assert await index.end_unread(
        "doc00001", claimed.attempt, state="skipped", code="no_text", reason="Skipped: x", pages=3
    )
    skipped = await index.document("doc00001")
    assert skipped is not None
    assert (skipped.status, skipped.code, skipped.reason, skipped.pages) == (
        "skipped",
        "no_text",
        "Skipped: x",
        3,
    )
    assert await count() == 0


async def test_the_search_looks_only_at_documents_that_can_be_found_whatever_rows_say(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", f"The {RARE} word.")
    assert await words(index, RARE) == ["doc00001"]
    for state in ("queued", "reading", "skipped", "failed"):
        await index._run(
            lambda db, s=state: db.execute("UPDATE documents SET status = ?", (s,))  # type: ignore[misc]
        )
        assert await words(index, RARE) == [], f"a {state} document is not searched"
    await index._run(lambda db: db.execute("UPDATE documents SET status = 'embedding'"))
    assert await words(index, RARE) == ["doc00001"]


async def test_only_a_document_still_being_read_can_end_unread_and_only_by_its_own_attempt(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await index.add(new("doc00001"))
    assert not await index.end_unread("doc00001", 0, state="failed", code="x", reason="Failed: x")
    claimed = await index.claim()
    assert claimed is not None
    assert not await index.end_unread("doc00001", 5, state="failed", code="x", reason="Failed: x")
    with pytest.raises(ValueError):
        await index.end_unread("doc00001", 0, state="ready", code="x", reason="x")
    assert await index.end_unread("doc00001", 0, state="failed", code="x", reason="Failed: x")


async def test_a_stop_while_a_document_was_read_puts_it_back_in_the_queue(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await index.add(new("doc00001"))
    await index.add(new("doc00002", "b.md"))
    claimed = await index.claim()
    assert claimed is not None and claimed.attempt == 0
    assert await index.requeue_interrupted() == 1
    doc = await index.document(claimed.id)
    assert doc is not None and (doc.status, doc.attempt) == ("queued", 1)
    assert await index.requeue_interrupted() == 0


async def test_the_passage_limit_skips_the_document_that_would_pass_it_and_keeps_none_of_it(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", "# A\n\nOne.\n\n# B\n\nTwo.\n")  # 2 passages
    await index.add(new("doc00002", "big.md"))
    claimed = await index.claim()
    assert claimed is not None
    got = passages_of("# A\n\nOne.\n\n# B\n\nTwo.\n\n# C\n\nThree.\n")
    done = await index.commit_reading("doc00002", claimed.attempt, summary(got), got, cap=4)
    assert done == "capped"
    doc = await index.document("doc00002")
    assert doc is not None
    assert (doc.status, doc.code, doc.chunks) == ("skipped", "too_many_passages", 0)
    assert doc.reason is not None and doc.reason.startswith("Skipped: your documents would come to")
    assert "4 passages" in doc.reason.replace(",", "") or "limit" in doc.reason
    assert await index.passages_total() == 2


async def test_counts_say_what_can_be_searched_and_what_is_still_waiting(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", "# A\n\nOne.\n\n# B\n\nTwo.\n")
    await index.add(new("doc00002", "later.md"))
    await put(index, "doc00003", "Three.", name="three.md", scope=chat_scope("c1"), source="a")
    await index.add(new("doc00004", "now.md"))
    reading = await index.claim()  # the one that has waited longest: later.md
    assert reading is not None and reading.id == "doc00002"
    # waiting is both: one is being read, and one still queued
    assert await index.count_found([LIBRARY]) == (1, 2, 2)
    assert await index.count_found([chat_scope("c1")]) == (1, 1, 0)
    assert await index.count_found([LIBRARY, chat_scope("c1")]) == (2, 3, 2)
    assert await index.count_found([]) == (0, 0, 0)
    totals = await index.totals(LIBRARY)
    assert (totals.documents, totals.passages, totals.by_state["ready"]) == (3, 2, 1)
    assert (totals.by_state["queued"], totals.by_state["reading"]) == (1, 1)


# --------------------------------------------------------------------------- #
# deleting is real
# --------------------------------------------------------------------------- #


async def test_removing_a_document_removes_its_row_passages_search_entries_and_files(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", f"# T\n\nThe {RARE} word.\n", name="a.md")
    await put(index, "doc00002", "# T\n\nAnother file entirely.\n", name="b.md")
    gone = await index.delete("doc00001")
    assert gone is not None and gone.id == "doc00001" and gone.chunks == 1
    assert await words(index, RARE) == []
    with raw(index) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM chunks").fetchone() == (1,), "the other's remain"
        # Not through the table that joins to the passages: the search index itself.
        hits = db.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", (RARE,))
        assert hits.fetchall() == []
    assert not index.original_path("doc00001").exists()
    assert not index.text_path("doc00001").exists()
    assert index.original_path("doc00002").exists() and index.text_path("doc00002").exists()
    assert await index.check() == [], "the full-text index agrees with the passages"
    assert await index.delete("doc00001") is None, "and again is nothing"


async def test_a_removed_documents_words_are_not_left_in_the_file_or_its_log(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    body = "\n\n".join(f"Ordinary filler paragraph {i} about gardens." for i in range(40))
    await put(index, "doc00001", f"{body}\n\nThe secret {RARE} is in here.\n")
    assert RARE.encode() in index.path.read_bytes() + _log(index)
    await index.delete("doc00001")
    assert RARE.encode() not in index.path.read_bytes() + _log(index)


async def test_a_replaced_documents_words_are_not_left_in_the_file_or_its_log(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    body = "\n\n".join(f"Ordinary filler paragraph {i} about gardens." for i in range(40))
    await put(index, "doc00001", f"{body}\n\nThe secret {RARE} is in here.\n", name="Report.md")
    assert RARE.encode() in index.path.read_bytes() + _log(index)
    added = await index.add(new("doc00002", "report.md"))
    assert [d.id for d in added.replaced] == ["doc00001"]
    assert RARE.encode() not in index.path.read_bytes() + _log(index)


def _log(index: Index) -> bytes:
    log = index.path.with_name(index.path.name + "-wal")
    return log.read_bytes() if log.exists() else b""


async def test_a_file_that_cannot_be_removed_rolls_the_whole_removal_back(
    indexes: Indexes, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", f"The {RARE} word.")
    held = index.text_path("doc00001")
    real = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self == held:
            raise PermissionError(13, "The process cannot access the file", str(self))
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(index_module.time, "sleep", lambda _seconds: None)
    with pytest.raises(DeleteFailed, match="Nothing was removed"):
        await index.delete("doc00001")
    monkeypatch.undo()
    doc = await index.document("doc00001")
    assert doc is not None and doc.status == "ready", "the row is still there"
    assert await words(index, RARE) == ["doc00001"], "and so are its passages"
    assert held.exists(), "and its text"
    assert await index.delete("doc00001") is not None, "and it can be tried again"
    assert not held.exists()


async def test_a_file_held_for_a_moment_is_waited_for(
    indexes: Indexes, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", "Some words.")
    held = index.original_path("doc00001")
    real, calls = Path.unlink, {"n": 0}

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self == held and calls["n"] < 2:
            calls["n"] += 1
            raise PermissionError(13, "in use", str(self))
        real(self, missing_ok=missing_ok)

    slept: list[float] = []
    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(index_module.time, "sleep", slept.append)
    assert await index.delete("doc00001") is not None
    assert calls["n"] == 2 and not held.exists()
    assert len(slept) == 2 and all(s > 0 for s in slept), "it waited between tries, not spun"


async def test_the_full_text_index_follows_an_edit_to_a_passage(indexes: Indexes) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", f"The {RARE} word.")
    await index._run(lambda db: db.execute("UPDATE chunks SET text = 'Now it says plumbago.'"))
    assert await words(index, RARE) == [] and await words(index, "plumbago") == ["doc00001"]
    assert await index.check() == []


async def test_a_chats_documents_go_with_their_scope_and_only_those(indexes: Indexes) -> None:
    index = indexes.get(ADA)
    assert index is not None
    mine, other = chat_scope("c1"), chat_scope("c2")
    await put(index, "doc00001", f"The {RARE} word.", scope=mine, source="attachment:f1")
    await put(index, "doc00002", f"The {RARE} too.", scope=mine, source="attachment:f2")
    await put(index, "doc00003", f"The {RARE} here.", scope=other, source="attachment:f3")
    await put(index, "doc00004", f"The {RARE} saved.", name="keep.md")
    gone = await index.delete_where(mine, "attachment:f1")
    assert [d.id for d in gone] == ["doc00001"]
    gone = await index.delete_where(mine)
    assert [d.id for d in gone] == ["doc00002"]
    assert {h for h in await words(index, RARE, mine, other, LIBRARY)} == {"doc00003", "doc00004"}
    assert not index.original_path("doc00002").exists()
    assert await index.delete_where(mine) == []


async def test_a_passages_id_is_never_used_again_so_a_citation_cannot_point_at_other_words(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    first = await put(index, "doc00001", "# A\n\nOne.\n\n# B\n\nTwo.\n")
    old = [h.chunk_id for h in await index.search('"one" OR "two"', [LIBRARY], 10)]
    assert first.chunks == 2 and len(old) == 2
    await index.requeue("doc00001")
    claimed = await index.claim()
    assert claimed is not None
    got = passages_of("# A\n\nOne.\n\n# B\n\nTwo.\n")
    await index.commit_reading("doc00001", claimed.attempt, summary(got), got, cap=100)
    new_ids = [h.chunk_id for h in await index.search('"one" OR "two"', [LIBRARY], 10)]
    assert set(new_ids).isdisjoint(old)
    assert await index.passages("doc00001", old) == [], "the old ids point at nothing"


async def test_passages_are_looked_up_by_document_and_never_another_documents(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", f"The {RARE} word.", name="a.md")
    await put(index, "doc00002", "Different words.", name="b.md")
    [hit] = await index.search(f'"{RARE}"', [LIBRARY], 5)
    assert [h.chunk_id for h in await index.passages("doc00001", [hit.chunk_id])] == [hit.chunk_id]
    assert await index.passages("doc00002", [hit.chunk_id]) == []
    assert await index.passages("doc00001", []) == []


# --------------------------------------------------------------------------- #
# files no row claims
# --------------------------------------------------------------------------- #


async def test_files_that_no_document_claims_are_found_and_half_written_ones_always(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", "Some words.")
    index.originals.mkdir(parents=True, exist_ok=True)
    (index.originals / "gone0001").write_text("orphan original", encoding="utf-8")
    (index.originals / "doc00001.part").write_text("half an upload", encoding="utf-8")
    (index.dir / "gone0001.txt").write_text("orphan text", encoding="utf-8")
    (index.dir / "doc00001.txt.part").write_text("half a text", encoding="utf-8")
    (index.dir / "notes-of-mine.pdf").write_text("not ours", encoding="utf-8")
    (index.originals / "x.y").write_text("not ours either", encoding="utf-8")
    found = {p.name for p in await index.orphans()}
    assert found == {"gone0001", "doc00001.part", "gone0001.txt", "doc00001.txt.part"}


# --------------------------------------------------------------------------- #
# two people
# --------------------------------------------------------------------------- #


async def test_two_people_never_see_each_others_documents_even_for_the_same_word(
    indexes: Indexes,
) -> None:
    ada, bo = indexes.get(ADA), indexes.get(BO)
    assert ada is not None and bo is not None
    await put(ada, "doc00001", f"Ada keeps the {RARE} word.")
    await put(bo, "doc00002", f"Bo keeps the {RARE} word.")
    assert await words(ada, RARE) == ["doc00001"] and await words(bo, RARE) == ["doc00002"]
    assert await ada.document("doc00002") is None and await bo.document("doc00001") is None
    await ada.delete("doc00001")
    assert await words(bo, RARE) == ["doc00002"]
    assert ada.path != bo.path and ada.originals != bo.originals
    owners = {Indexes.owner_of(d) for d in indexes.person_dirs()}
    assert owners == {ADA, BO}


async def test_many_things_at_once_do_not_corrupt_an_index(indexes: Indexes) -> None:
    index = indexes.get(ADA)
    assert index is not None

    async def one(n: int) -> None:
        await put(index, f"doc{n:05d}", f"Paragraph about harbours {n}.", name=f"f{n}.md")
        await index.search('"harbours"', [LIBRARY], 5)
        if n % 3 == 0:
            await index.delete(f"doc{n:05d}")

    await asyncio.gather(*(one(n) for n in range(1, 25)))
    assert await index.check() == []
    left = await index.documents()
    assert len(left) == 16 and all(d.status == "ready" for d in left)
    assert len(await index.search('"harbours"', [LIBRARY], 50)) == 16
