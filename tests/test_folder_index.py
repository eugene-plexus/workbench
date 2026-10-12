"""What a person's index does for the files of a folder (MR7, `knowledge/index.py`): a file is the
document at its path, an edit replaces it with nothing of the old left, what is gone is removed in one
transaction, a skipped file is a document with its reason, and a move keeps the words and the vectors.
Real files in a temporary data directory.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import struct
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from eugene_plexus_workbench.knowledge import index as index_module
from eugene_plexus_workbench.knowledge.index import (
    LIBRARY,
    DeleteFailed,
    FolderSkip,
    Index,
    Indexes,
    NewDocument,
    chat_scope,
    folder_prefix,
    folder_source,
    split_source,
)

from .test_knowledge_index import ADA, RARE, new, put, raw, words

G = "grant-one"
VECTOR = struct.pack("<2f", 1.0, 0.0)


@pytest.fixture
async def indexes(tmp_path: Path) -> AsyncIterator[Indexes]:
    found = Indexes(tmp_path / "data")
    try:
        yield found
    finally:
        await found.aclose()


async def folder_file(
    index: Index,
    doc_id: str,
    path: str,
    text: str,
    *,
    grant: str = G,
    mtime: str | None = "100",
    sha: str | None = None,
    ready: bool = True,
) -> index_module.Document:
    """A file of a folder, read and searchable, with its time and a vector for each passage."""
    source = folder_source(grant, path)
    doc = await _put(index, doc_id, text, f"Work/{path}", source, mtime, sha, ready)
    return doc


async def _put(
    index: Index,
    doc_id: str,
    text: str,
    name: str,
    source: str,
    mtime: str | None,
    sha: str | None,
    ready: bool,
) -> index_module.Document:
    added = await index.add(
        new(
            doc_id,
            name,
            LIBRARY,
            sha or hashlib.sha256(text.encode()).hexdigest(),
            source=source,
            source_mtime=mtime,
        )
    )
    assert not added.unchanged
    index.original_path(doc_id).parent.mkdir(parents=True, exist_ok=True)
    index.original_path(doc_id).write_text(text, encoding="utf-8")
    index.text_path(doc_id).write_text(text, encoding="utf-8")
    claimed = await index.claim()
    while claimed is not None and claimed.id != doc_id:
        claimed = await index.claim()
    assert claimed is not None
    from .test_knowledge_index import passages_of, summary

    got = passages_of(text)
    assert await index.commit_reading(doc_id, claimed.attempt, summary(got), got, cap=10**6)
    if ready:
        assert await index.finish(doc_id)
    found = await index.document(doc_id)
    assert found is not None
    return found


def skipped(
    doc_id: str, path: str, code: str = "x", *, size: int = 10, state: str = "skipped"
) -> FolderSkip:
    """A file of the folder that was looked at and could not be read."""
    doc = dataclasses.replace(
        new(doc_id, f"Work/{path}", source=folder_source(G, path), source_mtime="9"), bytes=size
    )
    return FolderSkip(doc, code, f"Skipped: {code}.", state=state)


async def give_vectors(index: Index, doc_id: str) -> int:
    with raw(index) as db:
        db.execute("UPDATE chunks SET vec = ? WHERE doc_id = ?", (VECTOR, doc_id))
        db.commit()
        return int(db.execute("SELECT COUNT(*) FROM chunks WHERE vec IS NOT NULL").fetchone()[0])


# --------------------------------------------------------------------------- #
# sources
# --------------------------------------------------------------------------- #


def test_a_source_names_the_folder_and_the_path_below_it() -> None:
    assert folder_source("g1", "a/b.md") == "folder:g1/a/b.md"
    assert split_source("folder:g1/a/b.md") == ("g1", "a/b.md")
    assert split_source("folder:g1/b.md") == ("g1", "b.md")
    for other in ("upload", "attachment:f1", "folder:", "folder:g1", "folder:/x"):
        assert split_source(other) is None, other
    assert folder_prefix("g1") == "folder:g1/"


# --------------------------------------------------------------------------- #
# the document at a path
# --------------------------------------------------------------------------- #


async def test_a_file_is_the_document_at_its_path_whatever_it_is_called(indexes: Indexes) -> None:
    index = indexes.get(ADA)
    assert index is not None
    old = await folder_file(index, "doc00001", "a/plan.md", f"The old {RARE} words.")
    added = await index.add(
        new(
            "doc00002",
            "Work/a/plan (renamed in the listing).md",
            source=folder_source(G, "a/plan.md"),
            sha="b" * 64,
        )
    )
    assert [d.id for d in added.replaced] == [old.id] and not added.unchanged
    assert [d.id for d in await index.documents()] == ["doc00002"]


async def test_a_file_at_another_path_is_another_document_even_with_the_same_name(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a/plan.md", "First words.")
    added = await index.add(new("doc00002", "Work/a/plan.md", source=folder_source(G, "b/plan.md")))
    assert added.replaced == []
    assert {d.id for d in await index.documents()} == {"doc00001", "doc00002"}


async def test_a_file_of_a_folder_and_one_the_person_added_never_replace_each_other(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", "Uploaded words.", name="plan.md")
    folder = await index.add(new("doc00002", "plan.md", source=folder_source(G, "plan.md")))
    assert folder.replaced == [], "the folder's file did not replace the upload"
    upload = await index.add(new("doc00003", "PLAN.md", sha="c" * 64))
    assert [d.id for d in upload.replaced] == ["doc00001"], "an upload still replaces an upload"
    assert {d.id for d in await index.documents()} == {"doc00002", "doc00003"}


async def test_an_edited_file_leaves_none_of_its_old_words_or_vectors_when_it_is_replaced(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    old = await folder_file(index, "doc00001", "a.md", f"The old {RARE} words.")
    assert await give_vectors(index, "doc00001") == 1
    assert await words(index, RARE) == ["doc00001"]
    added = await index.add(
        new("doc00002", "Work/a.md", source=folder_source(G, "a.md"), sha="d" * 64)
    )
    assert [d.id for d in added.replaced] == [old.id]
    assert await words(index, RARE) == [], "the old words are not found"
    with raw(index) as db:
        assert db.execute("SELECT COUNT(*) FROM chunks").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM chunks WHERE vec IS NOT NULL").fetchone() == (0,)
        assert db.execute("SELECT id FROM documents").fetchall() == [("doc00002",)]
    assert not index.original_path("doc00001").exists()
    assert not index.text_path("doc00001").exists()


async def test_a_replacement_that_cannot_remove_the_old_files_leaves_the_old_document_whole(
    indexes: Indexes, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a.md", f"The old {RARE} words.")

    def locked(self: Index, doc_id: str) -> None:
        raise DeleteFailed("a program holds it")

    monkeypatch.setattr(Index, "_remove_files", locked)
    with pytest.raises(DeleteFailed):
        await index.add(new("doc00002", "Work/a.md", source=folder_source(G, "a.md"), sha="e" * 64))
    monkeypatch.undo()
    assert [d.id for d in await index.documents()] == ["doc00001"], "no new row, no lost old one"
    assert (
        await words(index, RARE) == ["doc00001"] and (await index.document("doc00001")).chunks == 1
    )  # type: ignore[union-attr]


async def test_the_same_words_under_a_new_time_only_refresh_the_time_and_size(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    first = await folder_file(index, "doc00001", "a.md", "Words.", mtime="100")
    again = await index.add(
        new(
            "doc00002",
            "Work/a.md",
            sha=first.sha256,
            source=folder_source(G, "a.md"),
            source_mtime="200",
        )
    )
    assert again.unchanged and again.document.id == "doc00001" and again.replaced == []
    now = await index.document("doc00001")
    assert now is not None and now.source_mtime == "200" and now.status == "ready"


async def test_a_file_that_was_skipped_is_replaced_when_the_same_file_comes_again(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    sha = hashlib.sha256(b"x").hexdigest()
    await index.put_folder_skips([skipped("skip0001", "a.md", "busy")])
    added = await index.add(new("doc00002", "Work/a.md", source=folder_source(G, "a.md"), sha=sha))
    assert [d.id for d in added.replaced] == ["skip0001"] and not added.unchanged


# --------------------------------------------------------------------------- #
# what a folder holds
# --------------------------------------------------------------------------- #


async def test_what_the_index_holds_of_a_folder_is_one_entry_for_each_path(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a/b/c.md", "Words.", mtime="123")
    await folder_file(index, "doc00002", "top.md", "More.", mtime="456!", grant="other-grant")
    await put(index, "doc00003", "Uploaded.", name="u.md")
    held = await index.folder_files(G)
    assert [(h.doc_id, h.path, h.mtime, h.status, h.copied) for h in held] == [
        ("doc00001", "a/b/c.md", "123", "ready", True)
    ]
    assert held[0].size == 10 and held[0].sha256 == hashlib.sha256(b"Words.").hexdigest()
    assert await index.folder_files("nothing") == []


async def test_a_folder_is_not_another_folder_whose_id_starts_the_same(indexes: Indexes) -> None:
    index = indexes.get(ADA)
    assert index is not None
    for n, grant in enumerate([G, G + "x", G + "-x", G + "_x", G + "0", G[:-1]]):
        await folder_file(index, f"doc0000{n}", "a.md", f"Words {n}.", grant=grant)
    assert [h.doc_id for h in await index.folder_files(G)] == ["doc00000"]
    assert [d.id for d in (await index.folder_documents(G))[0]] == ["doc00000"]
    assert (await index.folder_counts(G)).files == 1
    gone = await index.delete_folder(G)
    assert [d.id for d in gone] == ["doc00000"]
    assert len(await index.documents()) == 5, "every other folder is as it was"


async def test_the_grants_that_have_documents_are_found_from_their_sources(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a.md", "One.", grant="g-one")
    await folder_file(index, "doc00002", "b/c.md", "Two.", grant="g-two")
    await folder_file(index, "doc00003", "d.md", "Three.", grant="g-two")
    await put(index, "doc00004", "Uploaded.", name="u.md")
    assert await index.folder_grants() == {"g-one", "g-two"}


async def test_a_folder_is_counted_by_state_and_by_reason_with_the_bytes_it_copied(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a.md", "Words here.")
    await folder_file(index, "doc00002", "b.md", "More words.", ready=False)
    await index.put_folder_skips(
        [
            skipped("skip0001", "c.png", "not_document", size=500),
            skipped("skip0002", "d.exe", "unsupported", size=70),
            skipped("skip0003", "e.bin", "unsupported", size=30),
        ]
    )
    counts = await index.folder_counts(G)
    assert counts.files == 5 and counts.passages == 2
    assert counts.by_state["ready"] == 1 and counts.by_state["searchable"] == 1
    assert counts.by_state["skipped"] == 3
    assert dict(counts.by_code) == {"not_document": 1, "unsupported": 2}
    assert counts.copied == 20, "the two documents that kept a copy (the helper says 10 bytes each)"
    assert counts.bytes == 20 + 500 + 70 + 30 and counts.owed == 1


async def test_a_page_of_a_folders_documents_is_filtered_and_in_path_order(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    for n, path in enumerate(["b/two.md", "A.md", "a/one.md", "c.md"]):
        await folder_file(index, f"doc0000{n}", path, f"File {n}.")
    await index.put_folder_skips([skipped("skip0001", "z.png", "not_document")])
    docs, total = await index.folder_documents(G)
    assert total == 5 and [d.name for d in docs] == [
        "Work/A.md",
        "Work/a/one.md",
        "Work/b/two.md",
        "Work/c.md",
        "Work/z.png",
    ]
    docs, total = await index.folder_documents(G, offset=1, limit=2)
    assert total == 5 and [d.name for d in docs] == ["Work/a/one.md", "Work/b/two.md"]
    docs, total = await index.folder_documents(G, state="skipped")
    assert total == 1 and docs[0].code == "not_document"
    docs, total = await index.folder_documents(G, code="not_document")
    assert total == 1 and docs[0].name == "Work/z.png"
    docs, total = await index.folder_documents(G, match="ONE")
    assert total == 1 and docs[0].name == "Work/a/one.md"
    docs, total = await index.folder_documents(G, match=G)
    assert total == 0, "the match is on the path below the folder, not on the folder's id"


async def test_the_files_the_person_added_and_those_of_their_folders_are_listed_apart(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", "Uploaded.", name="u.md")
    await folder_file(index, "doc00002", "a.md", "In a folder.")
    await index.add(new("doc00003", "chat.md", chat_scope("c1"), source="attachment:f1"))
    assert [d.id for d in await index.documents(LIBRARY)] == ["doc00002", "doc00001"]
    assert [d.id for d in await index.documents(LIBRARY, only="upload")] == ["doc00001"]
    assert [d.id for d in await index.documents(LIBRARY, only="folder")] == ["doc00002"]
    assert (await index.totals(LIBRARY, only="upload")).documents == 1
    assert (await index.totals(LIBRARY, only="folder")).documents == 1
    assert (await index.totals(LIBRARY)).documents == 2
    assert [d.id for d in await index.documents(only="upload")] == ["doc00003", "doc00001"], (
        "every scope but the folders' files, newest first"
    )


# --------------------------------------------------------------------------- #
# taking documents out
# --------------------------------------------------------------------------- #


async def test_documents_are_removed_together_with_their_words_vectors_and_files(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a.md", f"The {RARE} one.")
    await folder_file(index, "doc00002", "b.md", "Another document.")
    await folder_file(index, "doc00003", "c.md", "A third document.")
    await give_vectors(index, "doc00001")
    gone = await index.delete_many(["doc00001", "doc00002", "nothing"])
    assert sorted(d.id for d in gone) == ["doc00001", "doc00002"]
    assert await words(index, RARE) == []
    assert [d.id for d in await index.documents()] == ["doc00003"]
    for doc_id in ("doc00001", "doc00002"):
        assert not index.original_path(doc_id).exists() and not index.text_path(doc_id).exists()
    with raw(index) as db:
        assert db.execute("SELECT COUNT(*) FROM chunks").fetchone() == (1,)


async def test_a_folder_is_removed_without_touching_uploads_chats_or_other_folders(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a.md", f"Folder {RARE} words.")
    await folder_file(index, "doc00002", "sub/b.md", "Deep words.")
    await folder_file(index, "doc00003", "a.md", "Other folder.", grant="other-grant")
    await put(index, "doc00004", "Uploaded.", name="u.md")
    await index.add(new("doc00005", "chat.md", chat_scope("c1"), source="attachment:f1"))
    gone = await index.delete_folder(G)
    assert sorted(d.id for d in gone) == ["doc00001", "doc00002"]
    assert {d.id for d in await index.documents()} == {"doc00003", "doc00004", "doc00005"}
    assert await words(index, RARE) == []
    assert await index.delete_folder(G) == [], "and again: nothing left to remove"


async def test_removing_a_folder_scrubs_its_words_out_of_the_file(indexes: Indexes) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a.md", f"The {RARE} word is here.")
    await index.delete_folder(G)
    assert RARE.encode() not in index.path.read_bytes()
    wal = index.path.with_name(index.path.name + "-wal")
    assert not wal.exists() or RARE.encode() not in wal.read_bytes()


# --------------------------------------------------------------------------- #
# files that could not be read
# --------------------------------------------------------------------------- #


async def test_a_skipped_file_is_a_document_with_its_reason_and_no_files_or_passages(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    kept = await index.put_folder_skips(
        [
            FolderSkip(
                NewDocument(
                    id="skip0001",
                    scope=LIBRARY,
                    name="Work/pic.png",
                    media_type="application/octet-stream",
                    bytes=48,
                    sha256="",
                    source=folder_source(G, "pic.png"),
                    kind="unknown",
                    source_mtime="777",
                ),
                "not_document",
                "Skipped: this file is a picture, not a document.",
                "48 bytes",
            )
        ]
    )
    assert kept == 1
    doc = await index.document("skip0001")
    assert doc is not None
    assert (doc.status, doc.code, doc.reason, doc.detail) == (
        "skipped",
        "not_document",
        "Skipped: this file is a picture, not a document.",
        "48 bytes",
    )
    assert (doc.chunks, doc.sha256, doc.source_mtime, doc.bytes) == (0, "", "777", 48)
    assert not index.original_path("skip0001").exists() and not index.text_path("skip0001").exists()
    held = (await index.folder_files(G))[0]
    assert not held.copied and held.mtime == "777" and held.code == "not_document"


async def test_a_skipped_file_takes_the_place_of_the_document_the_path_had(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a.md", f"The old {RARE} words.")
    await give_vectors(index, "doc00001")
    await index.put_folder_skips([skipped("skip0001", "a.md", "too_large")])
    assert [d.id for d in await index.documents()] == ["skip0001"]
    assert await words(index, RARE) == []
    assert not index.original_path("doc00001").exists()
    with raw(index) as db:
        assert db.execute("SELECT COUNT(*) FROM chunks").fetchone() == (0,)
    assert RARE.encode() not in index.path.read_bytes(), "and its words are scrubbed from the file"


async def test_a_batch_of_skipped_files_is_one_write_and_none_is_no_write(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await index.documents()
    before = index.writes
    assert await index.put_folder_skips([]) == 0 and index.writes == before
    rows = [skipped(f"skip{n:04}", f"f{n}.png") for n in range(50)]
    assert await index.put_folder_skips(rows) == 50
    assert index.writes == before + 1, "a write makes every cached matrix stale: one for the batch"
    assert (await index.folder_counts(G)).files == 50


async def test_a_skipped_file_failed_state_is_kept_too(indexes: Indexes) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await index.put_folder_skips([skipped("skip0001", "a.md", "crashed", state="failed")])
    assert (await index.document("skip0001")).status == "failed"  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# time, and moving
# --------------------------------------------------------------------------- #


async def test_the_time_and_size_a_file_is_at_can_be_kept_without_reading_it_again(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a.md", "Words.", mtime="1!")
    assert await index.set_signature("doc00001", "2", 99)
    doc = await index.document("doc00001")
    assert doc is not None and (doc.source_mtime, doc.bytes, doc.status) == ("2", 99, "ready")
    assert not await index.set_signature("nothing", "2", 99)


async def test_a_moved_file_keeps_its_document_its_passages_and_their_vectors(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "old/a.md", f"The {RARE} words.")
    await give_vectors(index, "doc00001")
    with raw(index) as db:
        before = db.execute("SELECT id, vec FROM chunks").fetchall()
    moved = await index.move_folder_document(
        "doc00001",
        folder_source(G, "old/a.md"),
        folder_source(G, "new/a.md"),
        "Work/new/a.md",
        "300",
    )
    assert moved is not None and moved.id == "doc00001" and moved.name == "Work/new/a.md"
    assert moved.source == folder_source(G, "new/a.md") and moved.source_mtime == "300"
    assert moved.status == "ready" and moved.chunks == 1
    with raw(index) as db:
        assert db.execute("SELECT id, vec FROM chunks").fetchall() == before
    assert await words(index, RARE) == ["doc00001"]
    assert [h.path for h in await index.folder_files(G)] == ["new/a.md"]


async def test_a_document_is_moved_only_from_where_it_is(indexes: Indexes) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a.md", "Words.")
    again = index.move_folder_document
    assert (
        await again(
            "doc00001", folder_source(G, "elsewhere.md"), folder_source(G, "b.md"), "n", "1"
        )
        is None
    )
    assert (
        await again("nothing", folder_source(G, "a.md"), folder_source(G, "b.md"), "n", "1") is None
    )
    assert [h.path for h in await index.folder_files(G)] == ["a.md"]


# --------------------------------------------------------------------------- #
# the version of the file
# --------------------------------------------------------------------------- #


async def test_a_new_index_has_the_index_on_sources_that_a_folders_range_needs(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await index.documents()
    assert index_module.SCHEMA_VERSION >= 3
    with raw(index) as db:
        names = {r[1] for r in db.execute("PRAGMA index_list(documents)")}
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM documents WHERE source >= 'folder:g/' AND source < 'folder:g0'"
        ).fetchall()
    assert "documents_by_source" in names
    assert any("documents_by_source" in str(row) for row in plan), plan


async def test_an_index_made_before_folders_gains_the_index_and_keeps_its_documents(
    indexes: Indexes, tmp_path: Path
) -> None:
    directory = indexes.dir_of(ADA)
    directory.mkdir(parents=True)
    path = directory / index_module.INDEX_FILE
    with sqlite3.connect(path) as db:
        for statement in index_module._SCHEMA:
            db.execute(statement)
        db.execute("ALTER TABLE documents ADD COLUMN refused TEXT")
        db.execute("INSERT INTO meta (key, value) VALUES ('owner', ?)", (ADA,))
        db.execute("INSERT INTO meta (key, value) VALUES ('chunker', ?)", (json.dumps({}),))
        db.execute(
            "INSERT INTO documents (id, scope, name, media_type, bytes, sha256, source, status, kind, "
            "added_at) VALUES ('doc00001', 'library', 'old.md', 'text/markdown', 3, 'x', 'upload', "
            "'ready', 'markdown', 1.0)"
        )
        db.execute("PRAGMA user_version = 2")
    index = indexes.get(ADA)
    assert index is not None
    assert [d.id for d in await index.documents()] == ["doc00001"]
    with raw(index) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (index_module.SCHEMA_VERSION,)
        assert "documents_by_source" in {r[1] for r in db.execute("PRAGMA index_list(documents)")}


async def test_files_wait_in_a_trash_beside_the_index_while_documents_go_and_none_is_left(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a.md", "Words.")
    await folder_file(index, "doc00002", "b.md", "More.")
    await index.delete_many(["doc00001", "doc00002"])
    trash = index.dir / index_module.TRASH
    assert not trash.exists() or list(trash.iterdir()) == []
    assert await index.orphans() == []


async def test_a_file_left_in_the_trash_by_a_stop_is_an_orphan_for_the_boot_to_remove(
    indexes: Indexes,
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await index.documents()
    trash = index.dir / index_module.TRASH
    trash.mkdir()
    (trash / "doc00009").write_text("left by a stop", encoding="utf-8")
    assert [p.name for p in await index.orphans()] == ["doc00009"]


async def test_a_file_that_cannot_be_moved_puts_back_every_one_that_was_and_says_so(
    indexes: Indexes, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a.md", f"The {RARE} one.")
    await folder_file(index, "doc00002", "b.md", "Another.")
    real = Path.replace

    def held(self: Path, target: Path) -> Path:
        if self.name == "doc00002":
            raise PermissionError(13, "in use")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", held)
    monkeypatch.setattr(index_module.time, "sleep", lambda s: None)
    with pytest.raises(DeleteFailed, match="Nothing was removed"):
        await index.delete_many(["doc00001", "doc00002"])
    monkeypatch.undo()
    assert index.original_path("doc00001").exists() and index.text_path("doc00001").exists()
    assert {d.id for d in await index.documents()} == {"doc00001", "doc00002"}
    assert await words(index, RARE) == ["doc00001"]


async def test_a_batch_of_skipped_files_that_cannot_remove_an_old_file_puts_back_every_one_that_was(
    indexes: Indexes, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await folder_file(index, "doc00001", "a.md", f"The {RARE} one.")
    await folder_file(index, "doc00002", "b.md", "Another.")
    real = Path.replace

    def held(self: Path, target: Path) -> Path:
        if self.name == "doc00002":
            raise PermissionError(13, "in use")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", held)
    monkeypatch.setattr(index_module.time, "sleep", lambda s: None)
    with pytest.raises(DeleteFailed, match="Nothing was removed"):
        await index.put_folder_skips([skipped("skip0001", "a.md"), skipped("skip0002", "b.md")])
    monkeypatch.undo()
    assert index.original_path("doc00001").exists() and index.text_path("doc00001").exists()
    assert {d.id for d in await index.documents()} == {"doc00001", "doc00002"}, "no row changed"
    assert await words(index, RARE) == ["doc00001"]
