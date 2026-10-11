"""Saved documents as the rest of Workbench uses them (`knowledge/saved.py`): adding,
replacing, removing and reading again, a chat's own documents, and making an index
again from a person's own files. A real reader and a real worker over real files, with
no server in front.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import files
from eugene_plexus_workbench.knowledge.index import (
    LIBRARY,
    DeleteFailed,
    Index,
    chat_scope,
)
from eugene_plexus_workbench.knowledge.reader import Reader
from eugene_plexus_workbench.knowledge.saved import (
    OriginalMissing,
    SavedDocuments,
    hash_file,
    new_id,
)
from eugene_plexus_workbench.store import Store

from . import documents as docs
from .test_knowledge_index import ADA, BO, RARE

MARKDOWN = files.Detected("text/markdown", "markdown")
PDF = files.Detected("application/pdf", "pdf")


async def big() -> int:
    return 100_000


@pytest.fixture
async def saved(tmp_path: Path) -> AsyncIterator[SavedDocuments]:
    root = tmp_path / "data"
    reader = Reader(Store(tmp_path / "workbench.sqlite3"), root)
    found = SavedDocuments(root, reader, big)

    async def nobody(ids: set[str]) -> dict[str, str]:
        return {}

    await found.start(nobody)
    try:
        yield found
    finally:
        await found.aclose()
        await reader.aclose()


async def bring(
    saved: SavedDocuments,
    sub: str,
    name: str,
    data: bytes | str,
    *,
    detected: files.Detected = MARKDOWN,
    scope: str = LIBRARY,
    source: str = "upload",
) -> Any:
    """What the upload route does: write the file whole as a part, then add it."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    index = saved.index(sub)
    assert index is not None
    doc_id = new_id()
    part = index.part_path(doc_id)
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(raw)
    size, digest = hash_file(part)
    return await saved.add(
        sub,
        doc_id=doc_id,
        scope=scope,
        name=name,
        detected=detected,
        size=size,
        sha256=digest,
        source=source,
    )


async def ready(saved: SavedDocuments, sub: str, *ids: str, seconds: float = 60) -> None:
    left = await saved.ingest.wait(sub, list(ids), seconds)
    assert not left, f"still being read: {left}"


def listing(saved: SavedDocuments, sub: str) -> list[str]:
    index = saved.index(sub, create=False)
    assert index is not None
    return sorted(
        p.name for p in index.dir.rglob("*") if p.is_file() and "index.sqlite3" not in p.name
    )


# --------------------------------------------------------------------------- #
# adding
# --------------------------------------------------------------------------- #


async def test_a_document_is_added_read_and_found(saved: SavedDocuments) -> None:
    added = await bring(saved, ADA, "notes.md", f"# Notes\n\nThe {RARE} word.\n")
    assert added.document.status == "queued" and not added.unchanged and added.replaced == []
    await ready(saved, ADA, added.document.id)
    index = saved.index(ADA)
    assert index is not None
    found = await saved.retriever.retrieve(
        index, __import__("eugene_plexus_workbench.knowledge.search", fromlist=["x"]).plan_query(RARE),
        [LIBRARY], question=RARE, budget=None, overhead=0,
    )  # fmt: skip
    assert [p.name for p in found.passages] == ["notes.md"]
    doc_id = added.document.id
    assert sorted(listing(saved, ADA)) == sorted(
        ["originals/" + doc_id if False else doc_id, f"{doc_id}.meta.json", f"{doc_id}.txt"]
    )


async def test_a_file_of_the_same_name_replaces_the_one_being_read_and_ends_the_reader(
    saved: SavedDocuments,
) -> None:
    first = await bring(saved, ADA, "notes.md", f"The old {RARE} words.")
    second = await bring(saved, ADA, "notes.md", "The new words.")
    assert [d.id for d in second.replaced] == [first.document.id]
    await ready(saved, ADA, second.document.id)
    index = saved.index(ADA)
    assert index is not None
    assert [d.id for d in await index.documents()] == [second.document.id]
    assert await index.search(f'"{RARE}"', [LIBRARY], 5) == []
    assert not [n for n in listing(saved, ADA) if first.document.id in n], "its files are gone"


async def test_replacing_a_document_ends_the_reader_of_the_old_one_by_its_key(
    tmp_path: Path,
) -> None:
    from .test_knowledge_ingest import FakeReader

    reader = FakeReader()
    found = SavedDocuments(tmp_path / "data", reader, big)  # type: ignore[arg-type]

    async def nobody(ids: set[str]) -> dict[str, str]:
        return {}

    await found.start(nobody)
    try:
        first = await bring(found, ADA, "notes.md", f"The old {RARE} words.")
        key = f"doc:{first.document.id}"
        reader.hold(key)
        await reader.entered[key].wait()
        second = await bring(found, ADA, "notes.md", "The new words.")
        assert [d.id for d in second.replaced] == [first.document.id]
        assert reader.cancels == [key], "the old document's reading is ended, by its own key"
    finally:
        await found.aclose()


async def test_the_same_file_again_is_not_read_again_and_the_copy_is_thrown_away(
    saved: SavedDocuments,
) -> None:
    one = await bring(saved, ADA, "notes.md", "Same words.")
    await ready(saved, ADA, one.document.id)
    again = await bring(saved, ADA, "notes.md", "Same words.")
    assert again.unchanged and again.document.id == one.document.id
    assert len(listing(saved, ADA)) == 3, "its original, its text and its sidecar: no more"
    index = saved.index(ADA)
    assert index is not None
    assert [d.status for d in await index.documents()] == ["ready"]


async def test_a_sidecar_says_what_a_documents_files_cannot(saved: SavedDocuments) -> None:
    added = await bring(saved, ADA, "My Report.md", "Words.", source="upload")
    index = saved.index(ADA)
    assert index is not None
    said = json.loads(index.meta_path(added.document.id).read_text(encoding="utf-8"))
    assert said == {
        "id": added.document.id,
        "scope": "library",
        "name": "My Report.md",
        "mediaType": "text/markdown",
        "bytes": 6,
        "sha256": hashlib.sha256(b"Words.").hexdigest(),
        "source": "upload",
        "kind": "markdown",
        "language": None,
        "sourceMtime": None,
        "addedAt": added.document.added_at,
    }
    await ready(saved, ADA, added.document.id)
    await saved.remove(ADA, added.document.id)
    assert listing(saved, ADA) == [], "removing a document removes its sidecar too"


# --------------------------------------------------------------------------- #
# removing, and reading again
# --------------------------------------------------------------------------- #


async def test_removing_a_document_removes_it_from_the_index_and_the_disk(
    saved: SavedDocuments,
) -> None:
    added = await bring(saved, ADA, "notes.md", f"The {RARE} word.")
    await ready(saved, ADA, added.document.id)
    gone = await saved.remove(ADA, added.document.id)
    assert gone is not None and gone.name == "notes.md"
    assert listing(saved, ADA) == []
    assert await saved.remove(ADA, added.document.id) is None
    assert await saved.remove(BO, added.document.id) is None, "nobody else's"


async def test_removing_a_document_ends_its_reader_first_so_nothing_holds_its_file(
    tmp_path: Path,
) -> None:
    from .test_knowledge_ingest import FakeReader

    reader = FakeReader()
    found = SavedDocuments(tmp_path / "data", reader, big)  # type: ignore[arg-type]

    async def nobody(ids: set[str]) -> dict[str, str]:
        return {}

    await found.start(nobody)
    try:
        added = await bring(found, ADA, "notes.md", f"The {RARE} word.")
        key = f"doc:{added.document.id}"
        reader.hold(key)
        await reader.entered[key].wait()
        gone = await found.remove(ADA, added.document.id)
        assert gone is not None and gone.name == "notes.md"
        assert reader.cancels == [key], "ended by its own key, before its files went"
        assert listing(found, ADA) == []
    finally:
        await found.aclose()


async def test_a_file_that_stays_held_is_waited_for_and_then_named_and_nothing_is_removed(
    saved: SavedDocuments, monkeypatch: pytest.MonkeyPatch
) -> None:
    added = await bring(saved, ADA, "notes.md", f"The {RARE} word.")
    await ready(saved, ADA, added.document.id)
    index = saved.index(ADA)
    assert index is not None
    held = index.original_path(added.document.id)
    real = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self == held:
            raise PermissionError(13, "in use", str(self))
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    from eugene_plexus_workbench.knowledge import index as index_module

    monkeypatch.setattr(index_module.time, "sleep", lambda _s: None)
    sleeps: list[float] = []

    async def no_wait(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    with pytest.raises(DeleteFailed, match="Nothing was removed"):
        await saved.remove(ADA, added.document.id)
    assert len(sleeps) == 2, "tried three times"
    monkeypatch.undo()
    assert await index.document(added.document.id) is not None
    assert held.exists() and await saved.remove(ADA, added.document.id) is not None


async def test_reading_again_drops_the_passages_now_and_a_missing_original_cannot_be_read_again(
    saved: SavedDocuments,
) -> None:
    added = await bring(saved, ADA, "notes.md", f"The {RARE} word.")
    await ready(saved, ADA, added.document.id)
    again = await saved.reread(ADA, added.document.id)
    assert again is not None and (again.status, again.chunks) == ("queued", 0)
    await ready(saved, ADA, added.document.id)
    index = saved.index(ADA)
    assert index is not None
    assert (await index.document(added.document.id)).status == "ready"  # type: ignore[union-attr]
    index.original_path(added.document.id).unlink()
    with pytest.raises(OriginalMissing):
        await saved.reread(ADA, added.document.id)
    assert await saved.reread(ADA, "no-such-doc-1") is None
    assert await saved.reread("nobody", added.document.id) is None


# --------------------------------------------------------------------------- #
# a chat's own documents
# --------------------------------------------------------------------------- #


async def test_a_file_chosen_to_be_searched_becomes_a_document_of_its_chat_with_its_own_copy(
    saved: SavedDocuments, tmp_path: Path
) -> None:
    attachment = tmp_path / "attached.pdf"
    attachment.write_bytes(docs.three_pages())
    doc = await saved.search_attachment(
        ADA, chat_id="c1", file_id="f1", name="report.pdf", detected=PDF, source_path=attachment
    )
    assert (
        doc.scope == chat_scope("c1") and doc.source == "attachment:f1" and doc.name == "report.pdf"
    )
    await ready(saved, ADA, doc.id)
    index = saved.index(ADA)
    assert index is not None
    assert index.original_path(doc.id).read_bytes() == docs.three_pages()
    attachment.unlink()  # the chat's own file may go; the document has its own copy
    assert index.original_path(doc.id).is_file()
    again = await saved.search_attachment(
        ADA, chat_id="c1", file_id="f1", name="report.pdf", detected=PDF, source_path=tmp_path / "x"
    )
    assert again.id == doc.id, "asked again, it is the same document (and nothing is copied)"
    assert [d.id for d in await saved.chat_documents(ADA, "c1")] == [doc.id]
    assert await saved.chat_documents(ADA, "c2") == [] and await saved.library(ADA) == []


async def test_dropping_a_chat_or_a_file_removes_the_documents_made_from_them(
    saved: SavedDocuments, tmp_path: Path
) -> None:
    source = tmp_path / "attached.md"
    source.write_text(f"The {RARE} word.", encoding="utf-8")
    for chat, file_id in (("c1", "f1"), ("c1", "f2"), ("c2", "f3")):
        await saved.search_attachment(
            ADA,
            chat_id=chat,
            file_id=file_id,
            name=f"{file_id}.md",
            detected=MARKDOWN,
            source_path=source,
        )
    await ready(
        saved, ADA, *[d.id for c in ("c1", "c2") for d in await saved.chat_documents(ADA, c)]
    )
    assert await saved.drop_attachment(ADA, "c1", "f1") == 1
    assert [d.name for d in await saved.chat_documents(ADA, "c1")] == ["f2.md"]
    assert await saved.drop_attachment(ADA, "c1", "nope") == 0
    assert await saved.drop_chat(ADA, "c1") == 1 and await saved.chat_documents(ADA, "c1") == []
    assert [d.name for d in await saved.chat_documents(ADA, "c2")] == ["f3.md"]
    assert await saved.drop_chat(BO, "c2") == 0 and await saved.drop_chat("nobody", "c2") == 0
    assert len(listing(saved, ADA)) == 3, "what is left is one document's three files"


# --------------------------------------------------------------------------- #
# making an index again from a person's own files
# --------------------------------------------------------------------------- #


async def test_an_index_that_is_damaged_is_made_again_from_the_files_and_nothing_is_lost(
    saved: SavedDocuments, tmp_path: Path
) -> None:
    kept = {}
    for name, text in (("a.md", f"The {RARE} word."), ("b.md", "Second file."), ("c.md", "Third.")):
        added = await bring(saved, ADA, name, text)
        kept[name] = added.document.id
        await asyncio.sleep(0.01)
    await ready(saved, ADA, *kept.values())
    index = saved.index(ADA)
    assert index is not None
    originals = {n: index.original_path(i).read_bytes() for n, i in kept.items()}
    await index.close()
    index.path.write_bytes(b"this is not a database " * 300)

    async def owners(ids: set[str]) -> dict[str, str]:
        return {}

    made = await saved.rebuild(ADA, owners)
    assert (made.documents, made.left) == (3, 0)
    await ready(saved, ADA, *kept.values())
    documents = await index.documents()
    assert sorted((d.name, d.id, d.status) for d in documents) == sorted(
        (n, i, "ready") for n, i in kept.items()
    ), "the same documents, the same ids, read again"
    assert [d.name for d in documents] == ["c.md", "b.md", "a.md"], "in the order they were added"
    assert {n: index.original_path(i).read_bytes() for n, i in kept.items()} == originals
    assert len(await index.search(f'"{RARE}"', [LIBRARY], 5)) == 1
    assert await index.check() == []


async def test_a_rebuild_leaves_out_a_chats_file_whose_chat_is_gone_and_says_how_many_are_left(
    saved: SavedDocuments, tmp_path: Path
) -> None:
    source = tmp_path / "attached.md"
    source.write_text("Words in a chat's file.", encoding="utf-8")
    keep = await bring(saved, ADA, "a.md", "Library words.")
    live = await saved.search_attachment(
        ADA, chat_id="live", file_id="f1", name="live.md", detected=MARKDOWN, source_path=source
    )
    dead = await saved.search_attachment(
        ADA, chat_id="dead", file_id="f2", name="dead.md", detected=MARKDOWN, source_path=source
    )
    await ready(saved, ADA, keep.document.id, live.id, dead.id)
    index = saved.index(ADA)
    assert index is not None
    (index.dir / "orphan01.txt").write_text("a stray text", encoding="utf-8")
    index.original_path(keep.document.id).with_name("not-a-document.bin").write_bytes(b"x")

    async def owners(ids: set[str]) -> dict[str, str]:
        return {i: ADA for i in ids if i == "live"}

    made = await saved.rebuild(ADA, owners)
    assert made.documents == 2 and made.left >= 1
    await ready(saved, ADA, keep.document.id, live.id)
    assert {d.name for d in await index.documents()} == {"a.md", "live.md"}


async def test_a_rebuild_makes_a_missing_index_from_the_files_too_and_skips_a_lost_original(
    saved: SavedDocuments,
) -> None:
    one = await bring(saved, ADA, "a.md", "First file.")
    two = await bring(saved, ADA, "b.md", "Second file.")
    await ready(saved, ADA, one.document.id, two.document.id)
    index = saved.index(ADA)
    assert index is not None
    await index.close()
    index.path.unlink()
    for suffix in ("-wal", "-shm"):
        index.path.with_name(index.path.name + suffix).unlink(missing_ok=True)
    index.original_path(two.document.id).unlink()

    async def owners(ids: set[str]) -> dict[str, str]:
        return {}

    made = await saved.rebuild(ADA, owners)
    assert made.documents == 1
    await ready(saved, ADA, one.document.id)
    assert [d.name for d in await index.documents()] == ["a.md"]


async def test_a_rebuild_of_one_person_touches_nobody_elses(saved: SavedDocuments) -> None:
    mine = await bring(saved, ADA, "a.md", "Ada's words.")
    his = await bring(saved, BO, "b.md", "Bo's words.")
    await ready(saved, ADA, mine.document.id)
    await ready(saved, BO, his.document.id)
    bo_index = saved.index(BO)
    assert bo_index is not None
    before = bo_index.path.stat().st_mtime_ns

    async def owners(ids: set[str]) -> dict[str, str]:
        return {}

    await saved.rebuild(ADA, owners)
    await ready(saved, ADA, mine.document.id)
    assert bo_index.path.stat().st_mtime_ns == before
    assert [d.name for d in await bo_index.documents()] == ["b.md"]


def test_files_that_are_not_documents_are_not_mistaken_for_sidecars(tmp_path: Path) -> None:
    index = Index(ADA, tmp_path / "p", None)  # type: ignore[arg-type]
    index.dir.mkdir()
    (index.dir / "notes.meta.json").write_text("{}", encoding="utf-8")  # not an id
    (index.dir / "abcdef12.meta.json").write_text("{}", encoding="utf-8")  # no original
    index.originals.mkdir()
    (index.originals / "abcdef12").write_bytes(b"x")  # an original, a sidecar that says nothing
    assert index.sidecars() == []
    (index.dir / "abcdef12.meta.json").write_text("not json", encoding="utf-8")
    assert index.sidecars() == []
    assert time is not None
