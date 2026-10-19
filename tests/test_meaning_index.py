"""A person's index, with meaning (MR5, design 4.1): vectors kept beside the passages they
belong to, and which search model made them kept with the first of them, in one transaction,
so no two models' numbers are ever in one index.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from eugene_plexus_workbench.knowledge import index as index_module
from eugene_plexus_workbench.knowledge import vectors
from eugene_plexus_workbench.knowledge.embedders import identity_for
from eugene_plexus_workbench.knowledge.index import (
    LIBRARY,
    Index,
    Indexes,
    Pending,
    chat_scope,
)

from .test_knowledge_index import (  # noqa: F401
    ADA,
    RARE,
    indexes,
    new,
    passages_of,
    put,
    raw,
    summary,
    words,
)

MODEL, DIM = "model-a", 4
IDENT = identity_for(MODEL, DIM, now=1.0).to_json()


def blob(*numbers: float) -> bytes:
    return vectors.to_blob(vectors.normalised([list(numbers)])[0])


def rows(pending: list[Pending], *numbers: float) -> list[tuple[int, str, bytes]]:
    return [(p.chunk_id, p.doc_id, blob(*numbers)) for p in pending]


async def two_sections(index: Index, doc_id: str, **kw: object) -> None:
    """A document of two passages (a heading each, so they are two)."""
    kw.setdefault("name", f"{doc_id}.md")
    await put(
        index, doc_id, f"# One\n\nThe first section of {doc_id}.\n\n# Two\n\nThe second.", **kw
    )  # type: ignore[arg-type]


async def stored(index: Index, doc_id: str) -> list[tuple[int, int | None]]:
    with raw(index) as db:
        return [
            (r[0], r[1])
            for r in db.execute(
                "SELECT ord, length(vec) FROM chunks WHERE doc_id = ? ORDER BY ord", (doc_id,)
            )
        ]


async def doc(index: Index, doc_id: str) -> index_module.Document:
    found = await index.document(doc_id)
    assert found is not None
    return found


# --------------------------------------------------------------------------- #
# the schema
# --------------------------------------------------------------------------- #


async def test_an_index_made_before_meaning_gains_what_it_needs_and_keeps_its_documents(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "data" / "knowledge" / "old"
    folder.mkdir(parents=True)
    db = sqlite3.connect(folder / "index.sqlite3")
    for statement in index_module._SCHEMA:  # an index exactly as MR4 made it: version 1
        db.execute(statement)
    db.execute("INSERT INTO meta VALUES ('owner', ?)", (ADA,))
    db.execute(
        "INSERT INTO documents (id, scope, name, media_type, bytes, sha256, source, status, kind, "
        "chunks, added_at) VALUES ('doc00001', 'library', 'a.md', 'text/markdown', 3, 'x', "
        "'upload', 'ready', 'markdown', 1, 1.0)"
    )
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()
    made = Indexes(tmp_path / "data")
    try:
        found = Index(ADA, folder, made._executor)
        docs = await found.documents()
        assert [d.id for d in docs] == ["doc00001"] and docs[0].refused == []
        with raw(found) as check:
            assert check.execute("PRAGMA user_version").fetchone() == (index_module.SCHEMA_VERSION,)
            assert index_module.SCHEMA_VERSION >= 2
            assert "refused" in {r[1] for r in check.execute("PRAGMA table_info(documents)")}
        await found.close()
    finally:
        await made.aclose()


# --------------------------------------------------------------------------- #
# what is owed, and in what order
# --------------------------------------------------------------------------- #


async def test_passages_are_owed_a_chats_own_files_first_then_the_document_waiting_longest(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    await two_sections(index, "doc00002", ready=False)
    await two_sections(
        index, "doc00003", scope=chat_scope("chat-1"), ready=False, source="attachment:f1"
    )
    pending, epoch = await index.pending_meaning(16)
    order = [(p.doc_id, p.ord) for p in pending]
    assert order == [
        ("doc00003", 0), ("doc00003", 1), ("doc00001", 0), ("doc00001", 1),
        ("doc00002", 0), ("doc00002", 1),
    ], order  # fmt: skip
    assert epoch == index.epoch
    assert all(isinstance(p, Pending) and p.text and p.name for p in pending)


async def test_no_more_than_the_limit_are_owed_across_documents_and_none_that_are_ready_or_gone(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    await two_sections(index, "doc00002", ready=False)
    await two_sections(index, "doc00003", ready=True)  # ready: nothing owed (no model was in play)
    pending, _ = await index.pending_meaning(3)
    assert [(p.doc_id, p.ord) for p in pending] == [
        ("doc00001", 0),
        ("doc00001", 1),
        ("doc00002", 0),
    ]
    await index.delete("doc00001")
    pending, _ = await index.pending_meaning(16)
    assert {p.doc_id for p in pending} == {"doc00002"}


# --------------------------------------------------------------------------- #
# keeping vectors
# --------------------------------------------------------------------------- #


async def test_the_first_vectors_are_kept_with_the_search_model_that_made_them_in_one_transaction(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    assert await index.embedder() is None
    pending, epoch = await index.pending_meaning(16)
    got = await index.store_vectors(IDENT, MODEL, DIM, rows(pending, 1, 0, 0, 0), epoch=epoch)
    assert (got.written, got.refused, got.gone, got.changed, got.stale) == (2, 0, 0, None, False)
    assert got.completed == ("doc00001",)
    assert await index.embedder() == IDENT
    assert await stored(index, "doc00001") == [(0, 16), (1, 16)]
    done = await doc(index, "doc00001")
    assert (done.status, done.embedded, done.chunks) == ("ready", 2, 2)
    assert (await index.pending_meaning(16))[0] == []


async def test_a_document_is_embedding_while_some_of_its_passages_have_vectors(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    pending, _ = await index.pending_meaning(1)
    got = await index.store_vectors(IDENT, MODEL, DIM, rows(pending, 1, 0, 0, 0))
    assert got.completed == ()
    half = await doc(index, "doc00001")
    assert (half.status, half.embedded) == ("embedding", 1)
    rest, _ = await index.pending_meaning(16)
    assert [p.ord for p in rest] == [1], "what is owed is what has no vector, and only that"
    await index.store_vectors(IDENT, MODEL, DIM, rows(rest, 0, 1, 0, 0))
    assert (await doc(index, "doc00001")).status == "ready"


async def test_vectors_of_another_search_model_are_refused_and_nothing_is_stored(
    indexes: Indexes,  # noqa: F811
) -> None:
    """No mixed vector spaces: the index says which model made its vectors, and a batch from
    another is refused whole, whatever else it holds."""
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    await two_sections(index, "doc00002", ready=False)
    first, _ = await index.pending_meaning(2)
    await index.store_vectors(IDENT, MODEL, DIM, rows(first, 1, 0, 0, 0))
    rest, _ = await index.pending_meaning(16)
    other = identity_for("model-b", DIM).to_json()
    refused = await index.store_vectors(other, "model-b", DIM, rows(rest, 0, 1, 0, 0))
    assert refused.changed == (MODEL, DIM) and refused.written == 0
    assert (await index.pending_meaning(16))[0] == rest, "not one passage of them was stored"
    assert await index.embedder() == IDENT, "and the record is as it was"
    bigger = identity_for(MODEL, 6).to_json()
    wide = await index.store_vectors(bigger, MODEL, 6, rows(rest, 1, 0, 0, 0, 0, 0))
    assert wide.changed == (MODEL, DIM) and wide.written == 0, "the same model at another size"


async def test_a_batch_begun_before_a_rebuild_is_never_stored_after_it(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    pending, epoch = await index.pending_meaning(16)
    await index.clear_vectors()
    got = await index.store_vectors(IDENT, MODEL, DIM, rows(pending, 1, 0, 0, 0), epoch=epoch)
    assert got.stale and got.written == 0
    assert await index.embedder() is None and len((await index.pending_meaning(16))[0]) == 2


async def test_a_passage_that_is_gone_is_left_out_and_counted_and_the_rest_are_kept(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    await two_sections(index, "doc00002", ready=False)
    pending, epoch = await index.pending_meaning(16)
    await index.delete("doc00001")  # removed while its passages were being embedded
    got = await index.store_vectors(IDENT, MODEL, DIM, rows(pending, 1, 0, 0, 0), epoch=epoch)
    assert (got.written, got.gone) == (2, 2)
    assert got.completed == ("doc00002",)
    with raw(index) as db:
        assert db.execute("SELECT COUNT(*) FROM chunks WHERE doc_id = 'doc00001'").fetchone() == (
            0,
        )


async def test_a_passage_the_model_refused_is_kept_refused_and_the_document_is_still_ready(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    pending, _ = await index.pending_meaning(16)
    what = {"ord": 1, "page": None, "chars": 99, "why": "The search model refused one passage."}
    got = await index.store_vectors(
        IDENT, MODEL, DIM, rows(pending[:1], 1, 0, 0, 0), [(pending[1].chunk_id, "doc00001", what)]
    )
    assert (got.written, got.refused) == (1, 1) and got.completed == ("doc00001",)
    assert await stored(index, "doc00001") == [(0, 16), (1, 0)], (
        "an empty vector: refused, not owed"
    )
    done = await doc(index, "doc00001")
    assert done.status == "ready" and done.embedded == 2 and done.refused == [what]
    assert (await index.pending_meaning(16))[0] == [], "never asked for again"
    matrix = await index.load_vectors(DIM)
    assert matrix.size == 1, "and never searched"


# --------------------------------------------------------------------------- #
# rebuilding, and what is owed after
# --------------------------------------------------------------------------- #


async def test_rebuilding_clears_every_vector_and_the_record_and_leaves_the_words(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await put(index, "doc00001", f"# One\n\nThe {RARE} word.\n\n# Two\n\nMore.", ready=False)
    pending, _ = await index.pending_meaning(16)
    await index.store_vectors(IDENT, MODEL, DIM, rows(pending, 1, 0, 0, 0))
    before = (index.epoch, index.writes)
    owed = await index.clear_vectors()
    assert owed == 2 and index.epoch == before[0] + 1 and index.writes > before[1]
    assert await index.embedder() is None
    assert await stored(index, "doc00001") == [(0, None), (1, None)]
    again = await doc(index, "doc00001")
    assert (again.status, again.embedded, again.refused) == ("searchable", 0, [])
    assert await words(index, RARE) == ["doc00001"], "words are never taken away"
    assert len((await index.pending_meaning(16))[0]) == 2


async def test_rebuilding_an_index_with_nothing_in_it_owes_nothing(indexes: Indexes) -> None:  # noqa: F811
    index = indexes.get(ADA)
    assert index is not None
    assert await index.clear_vectors() == 0


async def test_documents_ready_without_meaning_are_opened_again_once_a_model_is_in_play(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=True)  # read with no search model: ready by words
    assert (await doc(index, "doc00001")).status == "ready"
    counts = await index.meaning_counts()
    assert (counts.passages, counts.embedded, counts.owed, counts.words_only) == (2, 0, 0, 2)
    assert await index.reopen_unembedded() == 1
    assert (await doc(index, "doc00001")).status == "searchable"
    assert (await index.meaning_counts()).owed == 2


async def test_opening_nothing_again_is_not_a_write_so_a_cached_matrix_is_not_made_stale(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    pending, _ = await index.pending_meaning(16)
    await index.store_vectors(IDENT, MODEL, DIM, rows(pending, 1, 0, 0, 0))
    before = index.writes
    assert await index.reopen_unembedded() == 0
    assert await index.recount_embedded() == 0
    assert index.writes == before


async def test_a_count_that_is_wrong_is_made_again_from_the_passages(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    pending, _ = await index.pending_meaning(1)
    await index.store_vectors(IDENT, MODEL, DIM, rows(pending, 1, 0, 0, 0))
    with raw(index) as db:
        db.execute("UPDATE documents SET embedded = 2 WHERE id = 'doc00001'")
        db.commit()
    assert await index.recount_embedded() == 1
    assert (await doc(index, "doc00001")).embedded == 1


async def test_counts_say_how_much_can_be_found_by_meaning_in_a_scope(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    await two_sections(index, "doc00002", ready=True)
    await two_sections(index, "doc00003", scope=chat_scope("c"), ready=False, source="attachment:x")
    pending, _ = await index.pending_meaning(16)
    first_of_mine = [p for p in pending if p.doc_id == "doc00001"][:1]
    await index.store_vectors(IDENT, MODEL, DIM, rows(first_of_mine, 1, 0, 0, 0))
    mine = await index.meaning_counts(LIBRARY)
    assert (mine.passages, mine.embedded, mine.owed, mine.words_only) == (4, 1, 1, 2)
    everything = await index.meaning_counts()
    assert (everything.passages, everything.owed) == (6, 3)
    totals = await index.totals(LIBRARY)
    assert (totals.embedded, totals.owed) == (1, 1)


async def test_the_documents_waiting_are_listed_in_the_order_they_are_served(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    await two_sections(index, "doc00002", scope=chat_scope("c"), ready=False, source="attachment:x")
    await two_sections(index, "doc00003", ready=False)
    assert await index.owed_by_document() == [("doc00002", 2), ("doc00001", 2), ("doc00003", 2)]


# --------------------------------------------------------------------------- #
# reading the vectors back
# --------------------------------------------------------------------------- #


async def test_the_vectors_are_read_as_one_matrix_with_each_passages_scope_and_the_writes(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    await two_sections(index, "doc00002", scope=chat_scope("c"), ready=False, source="attachment:x")
    pending, _ = await index.pending_meaning(16)
    await index.store_vectors(IDENT, MODEL, DIM, rows(pending, 1, 0, 0, 0))
    matrix = await index.load_vectors(DIM)
    assert matrix.size == 4 and matrix.dim == DIM and matrix.writes == index.writes
    assert sorted(matrix.scopes) == ["chat:c", "library"]
    assert np.allclose(np.linalg.norm(matrix.data, axis=1), 1.0, atol=1e-6)
    await index.delete("doc00001")
    after = await index.load_vectors(DIM)
    assert after.size == 2 and after.writes > matrix.writes, "a removed document's vectors are gone"


async def test_a_stored_vector_that_is_not_the_size_of_the_search_is_named_and_not_searched(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    pending, _ = await index.pending_meaning(16)
    await index.store_vectors(IDENT, MODEL, DIM, rows(pending, 1, 0, 0, 0))
    with raw(index) as db:
        db.execute("UPDATE chunks SET vec = ? WHERE ord = 1", (b"\x00" * 12,))
        db.commit()
    with pytest.raises(vectors.BadVector) as caught:
        await index.load_vectors(DIM)
    assert (caught.value.size, caught.value.expected) == (12, 16)


async def test_passages_found_by_meaning_come_back_as_hits_in_order_with_their_similarity(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    await two_sections(index, "doc00002", scope=chat_scope("c"), ready=False, source="attachment:x")
    pending, _ = await index.pending_meaning(16)
    ids = [p.chunk_id for p in pending]
    hits = await index.hits_by_ids(
        [ids[3], ids[0], 9999], {ids[3]: 0.9, ids[0]: 0.4, 9999: 0.1}, [LIBRARY, "chat:c"]
    )
    assert [(h.chunk_id, h.score) for h in hits] == [(ids[3], 0.9), (ids[0], 0.4)], (
        "9999 is not there"
    )
    only_mine = await index.hits_by_ids(ids, {i: 0.5 for i in ids}, [LIBRARY])
    assert {h.doc_id for h in only_mine} == {"doc00001"}, "another scope is never a hit"
    assert (
        await index.hits_by_ids(ids, {}, []) == []
        and await index.hits_by_ids([], {}, [LIBRARY]) == []
    )


# --------------------------------------------------------------------------- #
# what goes with a document
# --------------------------------------------------------------------------- #


async def test_a_replaced_documents_vectors_go_with_its_passages(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    pending, _ = await index.pending_meaning(16)
    await index.store_vectors(IDENT, MODEL, DIM, rows(pending, 1, 0, 0, 0))
    added = await index.add(
        new("doc00002", "doc00001.md", sha="different")
    )  # the same name, other bytes
    assert [d.id for d in added.replaced] == ["doc00001"]
    assert (await index.load_vectors(DIM)).size == 0, "none of the old passages' numbers is left"


async def test_reading_a_document_again_forgets_its_meaning_and_what_was_refused(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    pending, _ = await index.pending_meaning(16)
    what = {"ord": 1, "page": None, "chars": 5, "why": "refused"}
    await index.store_vectors(
        IDENT, MODEL, DIM, rows(pending[:1], 1, 0, 0, 0), [(pending[1].chunk_id, "doc00001", what)]
    )
    again = await index.requeue("doc00001")
    assert again is not None and (again.embedded, again.chunks, again.refused) == (0, 0, [])
    assert (await index.load_vectors(DIM)).size == 0


async def test_a_batch_begun_before_the_index_was_made_again_is_never_stored_in_the_new_one(
    indexes: Indexes,  # noqa: F811
) -> None:
    """The new file counts passage numbers from 1 again: a vector made for an old passage 1
    must not become the vector of a new one."""
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    pending, epoch = await index.pending_meaning(16)
    await index.close()
    index.quarantine()
    await two_sections(index, "doc00009", ready=False)
    got = await index.store_vectors(IDENT, MODEL, DIM, rows(pending, 1, 0, 0, 0), epoch=epoch)
    assert got.stale and got.written == 0
    assert (await index.load_vectors(DIM)).size == 0


async def test_every_write_is_counted_and_a_read_is_not(indexes: Indexes) -> None:  # noqa: F811
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    seen = index.writes
    await index.documents()
    await index.pending_meaning(4)
    await index.meaning_counts()
    assert index.writes == seen, "reading changes nothing a cached matrix depends on"
    pending, _ = await index.pending_meaning(16)
    await index.store_vectors(IDENT, MODEL, DIM, rows(pending, 1, 0, 0, 0))
    assert index.writes == seen + 1


def test_the_identity_that_is_kept_is_readable_as_json(indexes: Indexes) -> None:  # noqa: F811
    assert json.loads(IDENT)["model"] == MODEL and json.loads(IDENT)["dim"] == DIM
    assert passages_of("# A\n\nx") and summary([])["kind"] == "markdown"


async def test_notes_and_documents_are_owed_meaning_apart_and_the_cap_counts_documents_only(
    indexes: Indexes,  # noqa: F811
) -> None:
    index = indexes.get(ADA)
    assert index is not None
    await two_sections(index, "doc00001", ready=False)
    await two_sections(index, "note0001", scope=index_module.NOTES, ready=False)
    assert {p.doc_id for p in (await index.pending_meaning(16))[0]} == {"doc00001", "note0001"}
    assert (await index.pending_meaning(16, notes=False, documents=False))[0] == []
    only_notes = (await index.pending_meaning(16, documents=False))[0]
    assert {p.doc_id for p in only_notes} == {"note0001"}
    assert {p.doc_id for p in (await index.pending_meaning(16, notes=False))[0]} == {"doc00001"}
    assert await index.passages_total() == 2
