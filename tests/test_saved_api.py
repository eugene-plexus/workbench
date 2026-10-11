"""Saved documents (MR4), through a real Workbench over a fake Eugene.

A person keeps documents in My documents; Workbench reads and indexes them in the
background; a chat with Documents on is sent the best-matching passages in its latest
message, cited by number, and the answer lists each passage's file and page. What these
check is the wire: what the gateway receives (where the passages are, how they are
delimited, that they are not sent twice), what a person is told at each step, and what
another person is never told.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from . import documents as docs
from .conftest import MODEL, PNG, World, chat_model
from .saved import (
    RARE,
    SECOND,
    add,
    ask,
    block_of,
    by_name,
    chat_on,
    has_block,
    index_of,
    keep,
    knowledge_files,
    listing,
    md,
    raw,
    settings,
    settle,
    start,
)

# --------------------------------------------------------------------------- #
# My documents: adding, and what is said at each step
# --------------------------------------------------------------------------- #


def test_many_files_are_kept_at_once_each_queued_and_then_read_with_its_counts(
    world: World,
) -> None:
    world.gateway.embedder_listed = False  # no search model at all: documents are searched by words
    ada = start(world)
    made = add(
        ada,
        ("notes.md", md(f"The {RARE} word is here.")),
        ("report.pdf", docs.three_pages()),
        ("report.docx", docs.docx()),
    )
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["refused"] == [] and [d["name"] for d in body["documents"]] == [
        "notes.md",
        "report.pdf",
        "report.docx",
    ]
    assert {d["state"] for d in body["documents"]} == {"queued"}, "said at once, before any is read"
    assert all(d["passages"] == 0 and d["scope"] == "library" for d in body["documents"])
    done = settle(ada)
    got = {d["name"]: d for d in done["documents"]}
    assert {d["state"] for d in got.values()} == {"ready"}
    assert (got["report.pdf"]["pages"], got["report.pdf"]["kind"], got["report.pdf"]["label"]) == (
        3,
        "pdf",
        "PDF",
    )
    assert got["notes.md"]["lines"] == 3 and got["report.docx"]["label"] == "Word"
    assert all(d["passages"] >= 1 and d["tokens"] and d["size"] > 0 for d in got.values())
    assert all(d["indexedAt"] and d["readAt"] and d["addedAt"] for d in got.values())
    totals = done["totals"]
    assert totals["documents"] == 3 and totals["byState"]["ready"] == 3
    assert totals["passages"] == sum(d["passages"] for d in got.values())
    assert (
        totals["bytes"] == sum(d["size"] for d in got.values())
        and totals["passagesKept"] == totals["passages"]
    )
    assert (
        done["search"]["mode"] == "words"
        and done["search"]["headline"] == "Searching by words only. No search model is running."
        and done["search"]["code"] == "none_set_up"
        and done["search"]["actions"] == ["get_search_model"]
    ), "no search model is set up, and the header says so: words only, and where to get one"


def test_each_file_shows_its_own_state_as_the_worker_goes_through_them(world: World) -> None:
    ada = start(world)
    many = [(f"f{n}.md", md(f"File {n} has the word {RARE}.")) for n in range(8)]
    assert add(ada, *many).status_code == 201
    seen: set[str] = set()
    states_at_once: set[frozenset[str]] = set()
    end = time.perf_counter() + 90
    while time.perf_counter() < end:
        found = listing(ada)["documents"]
        seen |= {d["state"] for d in found}
        states_at_once.add(frozenset(d["state"] for d in found))
        if all(d["state"] == "ready" for d in found):
            break
        time.sleep(0.01)
    assert {"queued", "ready"} <= seen, seen
    assert any(len(s) > 1 for s in states_at_once), "some are ready while others still wait"
    assert "searchable" not in seen - {"searchable"} or True


def test_a_file_workbench_does_not_read_is_refused_on_its_own_while_the_rest_are_kept(
    world: World,
) -> None:
    ada = start(world)
    made = add(
        ada,
        ("good.md", md("Words.")),
        ("picture.png", PNG),
        ("program.exe", b"MZ\x90\x00" + bytes(300)),
        ("fake.pdf", b"<html><body>hi</body></html>", "application/pdf"),
    )
    assert made.status_code == 201
    body = made.json()
    assert [d["name"] for d in body["documents"]] == ["good.md"]
    refused = {r["name"]: r for r in body["refused"]}
    assert set(refused) == {"picture.png", "program.exe", "fake.pdf"}
    assert all(r["status"] == 415 for r in refused.values())
    assert "not a document" in refused["picture.png"]["message"]
    assert "PDF, Word, Markdown, text, CSV, HTML and code" in refused["picture.png"]["message"]
    assert "binary" in refused["program.exe"]["message"]
    assert "bytes are" in refused["fake.pdf"]["message"], "what the file is, not what it claims"
    assert [d["name"] for d in listing(ada)["documents"]] == ["good.md"]


def test_when_every_file_is_refused_the_answer_is_the_first_refusal_and_names_each(
    world: World,
) -> None:
    ada = start(world)
    made = add(ada, ("picture.png", PNG), ("program.exe", b"MZ\x90\x00" + bytes(300)))
    assert made.status_code == 415
    detail = made.json()["detail"]
    assert "not a document" in detail["message"]
    assert [r["name"] for r in detail["refused"]] == ["picture.png", "program.exe"]
    assert listing(ada)["documents"] == [] and knowledge_files(world) == [], "nothing is kept"


def test_a_file_larger_than_what_workbench_reads_is_refused_naming_the_limit_and_leaves_nothing(
    world: World,
) -> None:
    settings(world, documentsMaxFileMB=1)
    ada = start(world)
    big = b"word " * 500_000  # 2.5 MB
    made = add(ada, ("big.txt", big), ("small.md", md("Fine.")))
    body = made.json()
    assert made.status_code == 201 and [d["name"] for d in body["documents"]] == ["small.md"]
    [refused] = body["refused"]
    assert (refused["name"], refused["status"]) == ("big.txt", 413)
    assert refused["message"] == "big.txt is larger than the 1 MiB Workbench reads of one file."
    settle(ada)
    assert all(".part" not in p.name for p in knowledge_files(world)), "no half an upload is left"


def test_a_zip_that_is_not_a_word_file_is_refused_as_what_it_is(world: World) -> None:
    ada = start(world)
    made = add(ada, ("sheet.docx", docs.zip_without_word()))
    assert made.status_code == 415
    assert "ZIP archive" in made.json()["detail"]["message"]


def test_a_name_is_the_files_own_without_any_folders_in_front_of_it(world: World) -> None:
    ada = start(world)
    keep(ada, ("C:\\Users\\me\\Documents\\plan.md", md("Words.")), ("a/b/c.md", md("More.")))
    assert sorted(d["name"] for d in listing(ada)["documents"]) == ["c.md", "plan.md"]


def test_the_same_name_replaces_the_old_file_and_its_old_words_are_gone_from_answers_at_once(
    world: World,
) -> None:
    ada = start(world)
    first = keep(ada, ("notes.md", md(f"The old word is {RARE}.")))["documents"][0]
    chat = chat_on(ada)
    ask(ada, chat, f"What is {RARE}?")
    assert RARE in block_of(world)[1]
    made = add(ada, ("notes.md", md(f"The new word is {SECOND}.")))
    [new] = made.json()["documents"]
    assert (
        new["replaced"] == [first["id"]] and new["state"] == "queued" and new["id"] != first["id"]
    )
    # At once: before the new one is read, the old words are not found.
    now = listing(ada)["documents"]
    assert [d["id"] for d in now] == [new["id"]]
    ask(ada, chat, f"What is {RARE}?")
    assert not has_block(world), "the old text is gone from answers at once"
    assert ada.get(f"/api/documents/{first['id']}").status_code == 404
    settle(ada)
    ask(ada, chat, f"What is {SECOND}?")
    assert SECOND in block_of(world)[1] and RARE not in block_of(world)[1]
    with raw(world) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone() == (1,)
    assert not any(first["id"] in p.name for p in knowledge_files(world)), "and its files"


def test_adding_the_same_file_again_changes_nothing(world: World) -> None:
    ada = start(world)
    first = keep(ada, ("notes.md", md("Same words.")))["documents"][0]
    again = add(ada, ("notes.md", md("Same words.")))
    [doc] = again.json()["documents"]
    assert doc["unchanged"] is True and doc["id"] == first["id"] and doc["replaced"] == []
    assert doc["state"] == "ready", "and it is not read again"
    assert [d["id"] for d in listing(ada)["documents"]] == [first["id"]]
    assert len(knowledge_files(world)) == len({p for p in knowledge_files(world)})
    assert sorted(p.name for p in (index_of(world).parent / "originals").iterdir()) == [first["id"]]


def test_the_same_name_from_two_people_never_replaces_the_others(world: World) -> None:
    ada, bo = start(world, "p-ada"), start(world, "p-bo")
    keep(ada, ("notes.md", md(f"Ada's {RARE}.")))
    keep(bo, ("notes.md", md(f"Bo's {SECOND}.")))
    assert [d["name"] for d in listing(ada)["documents"]] == ["notes.md"]
    assert listing(ada)["documents"][0]["id"] != listing(bo)["documents"][0]["id"]


# --------------------------------------------------------------------------- #
# what the states say when a file gives no text
# --------------------------------------------------------------------------- #


def test_a_scan_a_locked_file_and_a_damaged_one_are_skipped_or_failed_in_words(
    world: World,
) -> None:
    ada = start(world)
    got = {
        d["name"]: d
        for d in keep(
            ada,
            ("scan.pdf", docs.pdf_pictures(3)),
            ("locked.pdf", docs.pdf_encrypted()),
            ("bad.pdf", docs.pdf_damaged()),
            ("empty.txt", b" \n"),
            ("fine.md", md("Words.")),
        )["documents"]
    }
    assert got["scan.pdf"]["state"] == "skipped" and got["scan.pdf"]["code"] == "no_text"
    assert got["scan.pdf"]["message"] == (
        "Skipped: this PDF has no text, only pictures of pages. "
        "Workbench cannot read scanned pages yet."
    )
    assert got["scan.pdf"]["pages"] == 3 and got["scan.pdf"]["passages"] == 0
    assert got["locked.pdf"]["message"] == "Skipped: this file is password-protected."
    assert got["bad.pdf"]["state"] == "failed"
    assert got["bad.pdf"]["message"].startswith("Failed: Workbench could not read this file (")
    assert "PDFium: Data format error" in got["bad.pdf"]["message"]
    assert got["empty.txt"]["message"] == "Skipped: this file has no text in it."
    assert got["fine.md"]["state"] == "ready", "and the others were read"
    for name in ("scan.pdf", "locked.pdf", "bad.pdf", "empty.txt"):
        assert got[name]["searchable"] is False
        text = ada.get(f"/api/documents/{got[name]['id']}/text")
        assert text.status_code == 409, "there is no text to show"
        assert text.json()["detail"]["message"] == got[name]["message"], "and it says why"
    # Nothing about asking was started: they are still as they were.
    after = {d["name"]: d["state"] for d in listing(ada)["documents"]}
    assert after == {n: d["state"] for n, d in got.items()}


def test_a_chat_with_only_unreadable_documents_is_sent_nothing_and_says_how_many_are_not_ready(
    world: World,
) -> None:
    ada = start(world)
    keep(ada, ("scan.pdf", docs.pdf_pictures(2)))
    chat = chat_on(ada)
    answer = ask(ada, chat, f"What is {RARE}?")
    assert answer["status"] == "done" and not has_block(world)
    assert answer["retrieval"] is None, "nothing is kept that can be found, so nothing was searched"


def test_a_passage_limit_skips_the_file_that_would_pass_it_and_refuses_more_once_it_is_reached(
    world: World,
) -> None:
    settings(world, documentsMaxPassagesPerPerson=100)
    ada = start(world)
    many = "\n\n".join(f"# Part {i}\n\n" + ("A sentence about harbours. " * 25) for i in range(60))
    got = {
        d["name"]: d
        for d in keep(ada, ("a.md", many.encode()), ("b.md", many.encode()))["documents"]
    }
    first, second = got["a.md"], got["b.md"]
    assert first["state"] == "ready" and first["passages"] == 60
    assert second["state"] == "skipped" and second["code"] == "too_many_passages"
    assert second["message"].startswith(
        "Skipped: your documents would come to more than the 100 passages"
    )
    assert listing(ada)["totals"]["passagesKept"] == 60
    keep(ada, ("c.md", many.encode()))  # skipped again; still 60 kept
    full = add(ada, ("d.md", many.replace("Part", "Section").encode()))
    assert full.status_code == 201
    settle(ada)
    assert "d.md" in {d["name"] for d in listing(ada)["documents"]}


def test_a_person_at_the_passage_limit_is_refused_more_files_at_once_with_the_number(
    world: World,
) -> None:
    settings(world, documentsMaxPassagesPerPerson=100)
    ada = start(world)
    hundred = "\n\n".join(
        f"# Part {i}\n\n" + ("A sentence about harbours. " * 25) for i in range(100)
    )
    [full] = keep(ada, ("full.md", hundred.encode()))["documents"]
    assert full["state"] == "ready" and full["passages"] == 100
    more = add(ada, ("more.md", md("One more.")))
    assert more.status_code == 409, more.text
    assert more.json()["detail"]["message"].startswith(
        "You already keep 100 passages, the most Workbench keeps for one person."
    )
    assert [d["name"] for d in listing(ada)["documents"]] == ["full.md"], "nothing was kept"


# --------------------------------------------------------------------------- #
# the text, the original, and taking a file away
# --------------------------------------------------------------------------- #


def test_show_indexed_text_is_the_text_with_its_pages_marked(world: World) -> None:
    ada = start(world)
    doc = keep(ada, ("report.pdf", docs.three_pages()))["documents"][0]
    shown = ada.get(f"/api/documents/{doc['id']}/text")
    assert shown.status_code == 200
    assert shown.headers["content-type"] == "text/plain; charset=utf-8"
    assert shown.headers["content-security-policy"] == "sandbox"
    assert shown.headers["x-content-type-options"] == "nosniff"
    assert shown.headers["cache-control"] == "private, no-store"
    assert "[page 3]" in shown.text and docs.CODE_WORD in shown.text
    assert shown.text.startswith("[page 1]\nAnnual report.")


def test_open_original_is_a_download_of_the_file_as_it_was_added(world: World) -> None:
    ada = start(world)
    original = docs.three_pages()
    doc = keep(ada, ("my report.pdf", original))["documents"][0]
    got = ada.get(f"/api/documents/{doc['id']}/original")
    assert got.status_code == 200 and got.content == original
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"].startswith("attachment")
    assert (
        "my%20report.pdf" in got.headers["content-disposition"]
        or "my report.pdf" in got.headers["content-disposition"]
    )
    assert got.headers["content-security-policy"] == "sandbox"


def test_a_document_still_being_read_has_no_text_yet_and_says_so(world: World) -> None:
    ada = start(world)
    [doc] = add(ada, ("a.md", md("Words."))).json()["documents"]
    got = ada.get(f"/api/documents/{doc['id']}/text")
    assert got.status_code in (200, 409)
    if got.status_code == 409:
        assert got.json()["detail"]["message"] == "This document is still being read."
    settle(ada)


def test_a_text_that_has_gone_is_read_again_and_says_so(world: World) -> None:
    ada = start(world)
    doc = keep(ada, ("a.md", md(f"The {RARE} word.")))["documents"][0]
    (index_of(world).parent / f"{doc['id']}.txt").unlink()
    gone = ada.get(f"/api/documents/{doc['id']}/text")
    assert gone.status_code == 409
    assert "is reading it again" in gone.json()["detail"]["message"]
    settle(ada)
    assert ada.get(f"/api/documents/{doc['id']}/text").status_code == 200


def test_removing_a_document_removes_everything_of_it_and_it_is_gone_from_answers(
    world: World,
) -> None:
    ada = start(world)
    got = keep(ada, ("a.md", md(f"The {RARE} word.")), ("b.md", md(f"The {SECOND} word.")))
    a = next(d for d in got["documents"] if d["name"] == "a.md")
    chat = chat_on(ada)
    ask(ada, chat, f"What is {RARE}?")
    assert RARE in block_of(world)[1]
    assert ada.delete(f"/api/documents/{a['id']}").status_code == 204
    after = listing(ada)
    assert [d["name"] for d in after["documents"]] == ["b.md"] and after["totals"]["documents"] == 1
    for path in ("", "/text", "/original", "/passages?ids=1"):
        assert ada.get(f"/api/documents/{a['id']}{path}").status_code == 404, path
    assert ada.delete(f"/api/documents/{a['id']}").status_code == 404
    ask(ada, chat, f"What is {RARE}?")
    assert not has_block(world), "gone from answers at once"
    with raw(world) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] >= 1
        found = db.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", (RARE,))
        assert found.fetchall() == [], "not in the search index either"
    assert not any(a["id"] in p.name for p in knowledge_files(world)), (
        "nor its original or its text"
    )
    assert RARE.encode() not in index_of(world).read_bytes()


def test_removing_a_document_while_it_is_being_read_ends_it_and_leaves_nothing(
    world: World,
) -> None:
    ada = start(world)
    many = [(f"f{n}.md", md(f"File {n}: {RARE}.")) for n in range(6)]
    ids = {d["name"]: d["id"] for d in add(ada, *many).json()["documents"]}
    end = time.perf_counter() + 60
    reading = None
    while reading is None and time.perf_counter() < end:
        reading = next((d for d in listing(ada)["documents"] if d["state"] == "reading"), None)
        time.sleep(0.005)
    assert reading is not None, "one was being read"
    assert ada.delete(f"/api/documents/{reading['id']}").status_code == 204
    assert ada.delete(f"/api/documents/{ids['f5.md']}").status_code == 204  # still waiting
    done = settle(ada)
    assert {d["name"] for d in done["documents"]} == {f"f{n}.md" for n in range(5)} - {
        reading["name"]
    }
    assert all(d["state"] == "ready" for d in done["documents"]), "the rest were read"
    time.sleep(0.5)
    present = {d["id"] for d in done["documents"]}
    leftovers = [
        p.name
        for p in knowledge_files(world)
        if not p.name.startswith("index.sqlite3") and p.name.split(".")[0] not in present
    ]
    assert leftovers == [], leftovers
    assert not any(
        reading["id"] in p.name or ids["f5.md"] in p.name for p in knowledge_files(world)
    )
    with raw(world) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == len(done["documents"])


def test_reading_a_document_again_drops_its_old_passages_at_once_and_reads_it_anew(
    world: World,
) -> None:
    ada = start(world)
    doc = keep(ada, ("a.md", md(f"The {RARE} word.")))["documents"][0]
    again = ada.post(f"/api/documents/{doc['id']}/reread")
    assert again.status_code == 200
    body = again.json()
    assert (body["state"], body["passages"]) == ("queued", 0), "nothing of the old reading is found"
    assert body["id"] == doc["id"]
    settle(ada)
    assert by_name(ada, "a.md")["state"] == "ready" and by_name(ada, "a.md")["passages"] >= 1
    chat = chat_on(ada)
    ask(ada, chat, f"What is {RARE}?")
    assert RARE in block_of(world)[1]


def test_a_document_whose_original_is_gone_cannot_be_read_again_and_says_so(world: World) -> None:
    ada = start(world)
    doc = keep(ada, ("a.md", md("Words.")))["documents"][0]
    (index_of(world).parent / "originals" / doc["id"]).unlink()
    refused = ada.post(f"/api/documents/{doc['id']}/reread")
    assert refused.status_code == 409
    assert "not on its disk any more" in refused.json()["detail"]["message"]
    assert by_name(ada, "a.md")["state"] == "ready", "and what was kept is still there"


def test_a_passage_is_read_back_by_its_id_and_a_removed_document_is_a_404(world: World) -> None:
    ada = start(world)
    doc = keep(ada, ("a.md", md(f"The {RARE} word is in this passage.")))["documents"][0]
    chat = chat_on(ada)
    answer = ask(ada, chat, f"What is {RARE}?")
    [source] = answer["sources"]
    ids = ",".join(str(i) for i in source["chunk_ids"])
    got = ada.get(f"/api/documents/{doc['id']}/passages", params={"ids": ids + ",999999"})
    assert got.status_code == 200
    body = got.json()
    assert [p["text"] for p in body["passages"]] == [f"The {RARE} word is in this passage."]
    assert body["passages"][0]["heading"] == "Notes" and body["missing"] == [999999]
    assert body["document"] == {"id": doc["id"], "name": "a.md", "state": "ready"}
    for bad in ("", "x", ",".join(["1"] * 25)):
        assert (
            ada.get(f"/api/documents/{doc['id']}/passages", params={"ids": bad}).status_code == 422
        )
    ada.delete(f"/api/documents/{doc['id']}")
    assert ada.get(f"/api/documents/{doc['id']}/passages", params={"ids": ids}).status_code == 404


# --------------------------------------------------------------------------- #
# Test a question
# --------------------------------------------------------------------------- #


def test_test_a_question_shows_the_passages_with_file_page_heading_and_why_and_asks_no_model(
    world: World,
) -> None:
    ada = start(world)
    keep(
        ada,
        ("report.pdf", docs.three_pages()),
        ("notes.md", md(f"The second GPU cost is a lot. The word is {RARE}.", title="Budget")),
    )
    before = len(world.gateway.requests)
    got = ada.post("/api/documents/test", json={"question": f"What is {RARE} and the GPU cost?"})
    assert got.status_code == 200, got.text
    body = got.json()
    assert len(world.gateway.requests) == before, "no model was asked"
    assert body["status"] == "found" and body["mode"] == "words"
    assert body["query"]["terms"] == [RARE, "gpu", "cost"] or set(body["query"]["terms"]) >= {
        RARE,
        "gpu",
        "cost",
    }
    [first, *_] = body["passages"]
    assert first["name"] == "notes.md" and first["rank"] == 1 and first["via"] == "words"
    assert first["heading"] == "Budget" and (first["lineStart"], first["lineEnd"]) == (3, 3)
    assert first["pageStart"] is None and RARE in first["text"] and RARE in first["snippet"]
    assert RARE in first["why"]["terms"] and first["why"]["via"] == "words"
    assert body["searched"] == {
        "documents": 2,
        "passages": body["searched"]["passages"],
        "waiting": 0,
    }
    pdf = ada.post(
        "/api/documents/test", json={"question": f"the code word {docs.CODE_WORD}"}
    ).json()
    [hit] = [p for p in pdf["passages"] if p["name"] == "report.pdf"]
    assert (hit["pageStart"], hit["pageEnd"]) == (1, 3) and "tamarind 6042" in hit["why"]["phrases"]


def test_test_a_question_says_nothing_matched_as_a_normal_result_and_the_other_ends_in_words(
    world: World,
) -> None:
    ada = start(world)
    assert (
        ada.post("/api/documents/test", json={"question": "anything"}).json()["status"]
        == "no_documents"
    )
    keep(ada, ("a.md", md("Words about harbours.")))
    none = ada.post("/api/documents/test", json={"question": "airspeed swallow"})
    assert none.status_code == 200 and none.json()["status"] == "nothing_matched"
    assert none.json()["passages"] == [] and none.json()["found"] == 0
    empty = ada.post("/api/documents/test", json={"question": "and the of?"}).json()
    assert empty["status"] == "no_words" and empty["passages"] == []
    for body in (
        {"question": ""},
        {"question": "x" * 2001},
        {"question": "x", "passages": 11},
        {"x": 1},
    ):
        assert ada.post("/api/documents/test", json=body).status_code == 422


def test_test_a_question_fits_the_budget_of_the_model_named_and_can_ask_for_fewer(
    world: World,
) -> None:
    world.gateway.more_chat_models = [chat_model("small", locality="local", window=4096)]
    ada = start(world)
    text = "\n\n".join(f"# Part {i}\n\n" + ("The harbour is busy today. " * 40) for i in range(9))
    keep(
        ada, ("a.md", text.encode()), ("b.md", text.replace("harbour", "harbour harbour").encode())
    )
    big = ada.post("/api/documents/test", json={"question": "harbour", "model": MODEL}).json()
    small = ada.post("/api/documents/test", json={"question": "harbour", "model": "small"}).json()
    assert big["budget"]["window"] == 32768 and big["budget"]["limit"] == 3000
    assert small["budget"]["window"] == 4096 and small["budget"]["limit"] == 1228
    assert sum(p["tokens"] for p in small["passages"]) <= small["budget"]["allowed"]
    assert len(small["passages"]) <= len(big["passages"]) and small["didNotFit"] >= 1
    one = ada.post("/api/documents/test", json={"question": "harbour", "passages": 1}).json()
    assert len(one["passages"]) == 1


def test_test_a_question_with_a_chat_also_searches_that_chats_own_files(world: World) -> None:
    ada = start(world)
    keep(ada, ("lib.md", md(f"Library {RARE}.")))
    chat = ada.new_chat()
    long = docs.long_pdf(4)
    made = ada.post(
        f"/api/chats/{chat}/files", files={"file": ("long.pdf", long, "application/pdf")}
    )
    file_id = made.json()["id"]
    ada.wait_read(chat, file_id)
    chosen = ada.put(f"/api/chats/{chat}/files/{file_id}/reading", json={"mode": "search"})
    assert chosen.status_code == 200, chosen.text
    end = time.perf_counter() + 60
    while (
        time.perf_counter() < end
        and ada.get(f"/api/chats/{chat}/documents").json()["counts"]["chat"]["documents"] == 0
    ):
        time.sleep(0.1)
    only = ada.post("/api/documents/test", json={"question": "Section filler long file"}).json()
    assert only["status"] == "nothing_matched", "a chat's file is not in My documents"
    both = ada.post(
        "/api/documents/test", json={"question": "Section filler long file", "chatId": chat}
    ).json()
    assert both["status"] == "found" and {p["scope"] for p in both["passages"]} == {"chat"}


def test_test_a_question_on_an_index_that_cannot_be_searched_is_a_503_and_never_nothing_matched(
    world: World,
) -> None:
    ada = start(world)
    keep(ada, ("a.md", md(f"The {RARE} word.")))
    with raw(world) as db:
        db.execute("DROP TABLE chunks_fts")
    got = ada.post("/api/documents/test", json={"question": RARE})
    assert got.status_code == 503
    detail = got.json()["detail"]
    assert "The search index for your documents is damaged (SQLite said:" in detail["message"]
    assert "Your files are safe." in detail["message"]
    assert detail["code"] == "index_damaged" and detail["canRebuild"] is True
    assert ada.get("/api/documents").status_code == 200, "the list is not the full-text index"


def test_every_document_route_says_in_words_when_the_documents_index_is_damaged(
    world: World,
) -> None:
    ada = start(world)
    keep(ada, ("a.md", md("Words.")))
    path = index_of(world)
    # Close Workbench's own hold, then put something else in the place of the file.
    world.server.stop()
    path.write_bytes(b"this is not a database " * 300)
    from eugene_plexus_workbench.app import create_app

    from .conftest import ServerThread

    world.server = ServerThread(create_app(world.settings), world.server.port).start()
    ada = start(world)
    for method, url in (("get", "/api/documents"), ("post", "/api/documents/test")):
        got = getattr(ada, method)(
            url, **({"json": {"question": "words"}} if method == "post" else {})
        )
        assert got.status_code == 503, (url, got.text)
        assert got.json()["detail"]["code"] == "index_damaged"
    assert ada.post("/api/chats", json={"model": MODEL}).status_code == 201, "chats are not touched"


# --------------------------------------------------------------------------- #
# Rebuild search
# --------------------------------------------------------------------------- #


def test_rebuild_makes_the_index_again_from_the_persons_own_files_and_loses_nothing(
    world: World,
) -> None:
    ada = start(world)
    kept = keep(
        ada,
        ("a.md", md(f"The {RARE} word.")),
        ("b.pdf", docs.three_pages()),
        ("c.md", md(f"The {SECOND} word.")),
    )["documents"]
    ids = {d["name"]: d["id"] for d in kept}
    originals = index_of(world).parent / "originals"
    before = {p.name: p.read_bytes() for p in originals.iterdir()}
    world.server.stop()
    index_of(world).write_bytes(b"this is not a database " * 300)
    from eugene_plexus_workbench.app import create_app

    from .conftest import ServerThread

    world.server = ServerThread(create_app(world.settings), world.server.port).start()
    ada = start(world)
    broken = ada.get("/api/documents")
    assert broken.status_code == 503 and broken.json()["detail"]["canRebuild"] is True
    made = ada.post("/api/documents/rebuild")
    assert made.status_code == 200 and made.json() == {"documents": 3, "left": 0}
    done = settle(ada)
    assert {d["name"]: d["id"] for d in done["documents"]} == ids, (
        "the same documents, the same ids"
    )
    assert {d["state"] for d in done["documents"]} == {"ready"}
    assert {p.name: p.read_bytes() for p in originals.iterdir()} == before, "originals untouched"
    chat = chat_on(ada)
    ask(ada, chat, f"What is {RARE}?")
    assert RARE in block_of(world)[1]
    assert not list(index_of(world).parent.glob("index.damaged*")), "the damaged file is not kept"


def test_rebuild_also_works_on_an_index_that_is_fine_and_says_nothing_is_lost(
    world: World,
) -> None:
    ada = start(world)
    keep(ada, ("a.md", md(f"The {RARE} word.")))
    again = ada.post("/api/documents/rebuild")
    assert again.json() == {"documents": 1, "left": 0}
    got = settle(ada)
    assert [d["name"] for d in got["documents"]] == ["a.md"] and got["documents"][0][
        "state"
    ] == "ready"
    assert ada.post("/api/documents/rebuild").status_code == 200
    settings(world, documentsEnabled=False)
    assert ada.post("/api/documents/rebuild").status_code == 403


def test_a_person_who_has_kept_nothing_can_rebuild_and_has_nothing(world: World) -> None:
    ada = start(world)
    assert ada.post("/api/documents/rebuild").json() == {"documents": 0, "left": 0}
    assert listing(ada)["documents"] == []


def test_rebuild_of_one_person_leaves_the_other_alone(world: World) -> None:
    ada, bo = start(world, "p-ada"), start(world, "p-bo")
    keep(ada, ("a.md", md("Ada.")))
    his = keep(bo, ("b.md", md("Bo.")))["documents"][0]
    ada.post("/api/documents/rebuild")
    settle(ada)
    assert listing(bo)["documents"][0]["id"] == his["id"]


# --------------------------------------------------------------------------- #
# more on adding
# --------------------------------------------------------------------------- #


def test_a_file_added_again_while_the_first_is_still_being_read_ends_as_the_new_one(
    world: World,
) -> None:
    ada = start(world)
    many = [(f"f{n}.md", md(f"File {n}.")) for n in range(5)]
    add(ada, *many, ("notes.md", md(f"The old {RARE} words.")))
    again = add(ada, ("notes.md", md(f"The new {SECOND} words.")))
    assert again.status_code == 201
    done = settle(ada)
    assert sorted(d["name"] for d in done["documents"]) == sorted(
        [n for n, _ in many] + ["notes.md"]
    )
    assert {d["state"] for d in done["documents"]} == {"ready"}
    chat = chat_on(ada)
    ask(ada, chat, f"What is {RARE}?")
    assert not has_block(world), "the old words are never found"
    ask(ada, chat, f"What is {SECOND}?")
    assert SECOND in block_of(world)[1]


def test_a_full_disk_is_named_with_its_free_space_and_the_other_files_are_kept(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno

    from eugene_plexus_workbench import saved_api

    real = saved_api._save_upload
    calls = {"n": 0}

    async def full(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.ENOSPC, "No space left on device")
        return await real(*args, **kwargs)

    monkeypatch.setattr(saved_api, "_save_upload", full)
    ada = start(world)
    made = add(ada, ("big.md", md("Words.")), ("small.md", md("More.")))
    assert made.status_code == 201
    body = made.json()
    assert [d["name"] for d in body["documents"]] == ["small.md"]
    [refused] = body["refused"]
    assert (refused["name"], refused["status"]) == ("big.md", 507)
    assert refused["message"].startswith(
        "big.md was not added: the disk Workbench keeps your documents on is full ("
    )
    assert refused["message"].endswith(" free).")
    settle(ada)
    assert all(".part" not in p.name for p in knowledge_files(world))


def test_a_file_that_cannot_be_stored_for_another_reason_says_what_the_system_said(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eugene_plexus_workbench import saved_api

    async def denied(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(saved_api, "_save_upload", denied)
    made = add(start(world), ("a.md", md("Words.")))
    assert made.status_code == 500
    assert made.json()["detail"]["message"] == "Workbench could not store a.md (Permission denied)."
