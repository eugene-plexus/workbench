"""Memory v2's MV6: provenance, "Still true?" from changed documents, and the tidy pass.

The provenance checks run through a real Workbench over the fake gateway (`conftest.py`): a
document is kept, an answer cites it by number, Remember links it, and replacing or removing
the document asks "Still true?" with the reason. The tidy pass's rules are pure and tested
directly; its proposals and answers go over HTTP.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import memory_provenance, memory_tidy
from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.store import SCHEMA_VERSION, MemoryNote, Message, Store

from .conftest import Browser, World
from .saved import SECOND, add, ask, by_name, chat_on, keep, md, settle, start, survey_pdf
from .test_saved_chats import CODE, QUESTION


def db_of(world: World) -> sqlite3.Connection:
    return sqlite3.connect(world.data / "workbench.sqlite3")


def remember(person: Browser, chat: str, message_id: str, text: str) -> str:
    made = person.post(
        "/api/memory/notes",
        json={"text": text, "source": "button", "chatId": chat, "messageId": message_id},
    )
    assert made.status_code == 201, made.text
    return str(made.json()["note"]["id"])


def documents_of(person: Browser, note_id: str) -> list[dict[str, Any]]:
    got = person.get(f"/api/memory/notes/{note_id}")
    assert got.status_code == 200, got.text
    return list(got.json()["documents"])


def page(person: Browser) -> dict[str, Any]:
    got = person.get("/api/memory")
    assert got.status_code == 200, got.text
    return dict(got.json())


def asked(person: Browser) -> dict[str, list[dict[str, Any]]]:
    return {r["id"]: r["reasons"] for r in page(person)["revisit"]}


def typed(person: Browser, text: str, **more: Any) -> str:
    made = person.post("/api/memory/notes", json={"text": text, **more})
    assert made.status_code == 201, made.text
    return str(made.json()["note"]["id"])


def link(person: Browser, note_id: str, doc_id: str) -> Any:
    return person.post(f"/api/memory/notes/{note_id}/documents", json={"docId": doc_id})


def doc_id(person: Browser, name: str) -> str:
    return str(by_name(person, name)["id"])


# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #


def test_remember_on_an_answer_links_what_it_cited_and_nothing_else(world: World) -> None:
    ada = start(world)
    keep(ada, ("survey.pdf", survey_pdf(CODE)), ("notes.md", md(f"The {SECOND} word.")))
    chat = chat_on(ada)
    world.gateway.words = ["The code word is ", CODE, " [1]."]
    answer = ask(ada, chat, f"{QUESTION} And {SECOND}?")
    passages = [s for s in answer["sources"] if s.get("passage")]
    assert len(passages) == 2 and [s["cited"] for s in passages] == [True, False]
    cited = passages[0]
    note = remember(ada, chat, answer["id"], f"The code word is {CODE}.")
    [linked] = documents_of(ada, note)
    assert linked["docId"] == cited["doc_id"] and linked["name"] == cited["name"]
    assert linked["how"] == "answer" and linked["scope"] == "library"
    assert linked["place"] and linked["place"][0]["page_start"] == cited["page_start"]
    # Linking it again by hand keeps how and where it was first linked.
    assert link(ada, note, cited["doc_id"]).status_code == 200
    [again] = documents_of(ada, note)
    assert again["how"] == "answer" and again["place"] == linked["place"]
    # The person's own message rests on no document, and nor does an answer citing none.
    question = next(
        m for m in ada.get(f"/api/chats/{chat}").json()["messages"] if m["role"] == "user"
    )
    assert documents_of(ada, remember(ada, chat, question["id"], "I asked about it.")) == []
    world.gateway.words = ["No numbers here."]
    quiet = ask(ada, chat, QUESTION)
    assert documents_of(ada, remember(ada, chat, quiet["id"], "Nothing cited.")) == []


def test_answer_documents_reads_cited_passages_and_whole_documents_only() -> None:
    message = Message(
        id="m", chat_id="c", seq=1, role="assistant", status="done", created_at=0, content="x",
        sources=[
            {"kind": "document", "passage": True, "doc_id": "a", "cited": True, "page_start": 3,
             "heading": "H" * 500, "snippet": "s"},
            {"kind": "document", "passage": True, "doc_id": "a", "cited": True, "line_start": 9},
            {"kind": "document", "passage": True, "doc_id": "b", "cited": False},
            {"kind": "document", "doc_id": "c", "name": "whole.md"},
            {"kind": "web", "url": "https://example.org"},
        ],
    )  # fmt: skip
    found = memory_provenance.answer_documents(message)
    assert set(found) == {"a", "c"}
    # Where, never what: a removed document's words must not live on in a note's link.
    assert found["a"][0] == {"page_start": 3}
    assert found["a"][1] == {"line_start": 9} and found["c"] == []


def test_a_person_with_no_index_is_asked_nothing() -> None:
    class Saved:
        def index(self, sub: str, *, create: bool = True) -> None:
            return None

    state = type("State", (), {"saved": Saved()})()
    links = {"n1": [{"doc_id": "d", "scope": "library", "source": "upload", "name": "a.md",
                     "sha256": "x", "snoozed_until": None, "snoozed_sha": None}]}  # fmt: skip
    assert asyncio.run(memory_provenance.check(state, "ada", links)) == {}


def test_a_person_links_only_their_own_documents_and_unlinks(world: World) -> None:
    ada = start(world)
    keep(ada, ("notes.md", md(f"The {SECOND} word.")))
    mine = doc_id(ada, "notes.md")
    bo = start(world, "p-bo")
    keep(bo, ("bo.md", md("Bo's own words.")))
    note = typed(ada, f"The word is {SECOND}.")
    assert link(ada, note, doc_id(bo, "bo.md")).status_code == 404
    assert link(ada, note, "nope").status_code == 404
    done = link(ada, note, mine)
    assert done.status_code == 200, done.text
    assert [d["docId"] for d in done.json()["documents"]] == [mine]
    assert [d["how"] for d in documents_of(ada, note)] == ["person"]
    assert link(bo, note, doc_id(bo, "bo.md")).status_code == 404, "not Bo's note"
    assert ada.delete(f"/api/memory/notes/{note}/documents/{mine}").status_code == 204
    assert ada.delete(f"/api/memory/notes/{note}/documents/{mine}").status_code == 404
    assert documents_of(ada, note) == []


def test_replacing_a_document_asks_still_true_and_yes_moves_the_link(world: World) -> None:
    ada = start(world)
    keep(ada, ("notes.md", md(f"The old word is {SECOND}.")))
    first = doc_id(ada, "notes.md")
    note = typed(ada, f"The word is {SECOND}.")
    assert link(ada, note, first).status_code == 200
    assert note not in asked(ada), "a current document asks nothing"
    # Read again with the same words: still nothing.
    assert ada.post(f"/api/documents/{first}/reread").status_code in (200, 202)
    settle(ada)
    assert note not in asked(ada)
    made = add(ada, ("notes.md", md("The new word is different.")))
    assert made.status_code == 201, made.text
    settle(ada)
    second = doc_id(ada, "notes.md")
    assert second != first
    [reason] = asked(ada)[note]
    assert reason["name"] == "notes.md" and reason["change"] == "changed" and reason["at"]
    # It is still sent to models while it waits (MV6-C2): it is a current note.
    assert any(n["id"] == note for n in page(ada)["sections"]["other"])
    said = ada.post("/api/memory/revisit", json={"noteId": note, "answer": "yes"})
    assert said.status_code == 200, said.text
    assert [d["docId"] for d in documents_of(ada, note)] == [second]
    assert note not in asked(ada)


def test_a_removed_document_asks_later_puts_it_off_and_yes_drops_the_link(
    world: World,
) -> None:
    ada = start(world)
    keep(ada, ("notes.md", md(f"The word is {SECOND}.")))
    gone = doc_id(ada, "notes.md")
    note = typed(ada, f"The word is {SECOND}.")
    other = typed(ada, "A second note on it.")
    assert link(ada, note, gone).status_code == 200
    assert link(ada, other, gone).status_code == 200
    assert ada.delete(f"/api/documents/{gone}").status_code == 204
    now = asked(ada)
    assert now[note] == [{"docId": gone, "name": "notes.md", "change": "removed", "at": None}]
    assert other in now, "every note with a changed document, at once (MV6-C3)"
    assert (
        ada.post("/api/memory/revisit", json={"noteId": note, "answer": "later"}).status_code == 200
    )
    assert note not in asked(ada) and other in asked(ada)
    with db_of(world) as db:
        db.execute("UPDATE memory_note_documents SET snoozed_until = ?", (time.time() - 1,))
    assert note in asked(ada), "back after the week"
    assert (
        ada.post("/api/memory/revisit", json={"noteId": note, "answer": "yes"}).status_code == 200
    )
    assert documents_of(ada, note) == [] and note not in asked(ada)
    assert (
        ada.post("/api/memory/revisit", json={"noteId": other, "answer": "no"}).status_code == 200
    )
    assert ada.get(f"/api/memory/notes/{other}").status_code == 404
    with db_of(world) as db:
        asked_at = db.execute(
            "SELECT revisit_asked_at FROM memory_prefs WHERE owner = 'p-ada'"
        ).fetchone()
    assert asked_at in (None, (None,)), "a document's question spends no project's week"


def test_put_off_holds_only_while_the_document_stays_as_it_was(world: World) -> None:
    ada = start(world)
    keep(ada, ("notes.md", md(f"The word is {SECOND}.")))
    note = typed(ada, f"The word is {SECOND}.")
    assert link(ada, note, doc_id(ada, "notes.md")).status_code == 200
    add(ada, ("notes.md", md("The word changed once.")))
    settle(ada)
    assert note in asked(ada)
    assert (
        ada.post("/api/memory/revisit", json={"noteId": note, "answer": "later"}).status_code == 200
    )
    assert note not in asked(ada)
    add(ada, ("notes.md", md("The word changed twice.")))
    settle(ada)
    assert note in asked(ada), "a second change asks again at once"


def test_a_new_version_carries_its_documents_as_they_are_now(world: World) -> None:
    ada = start(world)
    keep(ada, ("notes.md", md(f"The word is {SECOND}.")))
    first = doc_id(ada, "notes.md")
    note = typed(ada, f"The word is {SECOND}.")
    assert link(ada, note, first).status_code == 200
    add(ada, ("notes.md", md("The word changed.")))
    settle(ada)
    assert note in asked(ada)
    saved = ada.patch(
        f"/api/memory/notes/{note}", json={"text": "The word changed.", "asNewVersion": True}
    )
    assert saved.status_code == 200, saved.text
    new = saved.json()["note"]["id"]
    assert [d["docId"] for d in documents_of(ada, new)] == [doc_id(ada, "notes.md")]
    assert new not in asked(ada)
    # Deleting the note takes its links with it.
    assert ada.delete(f"/api/memory/notes/{new}").status_code == 204
    with db_of(world) as db:
        assert db.execute("SELECT COUNT(*) FROM memory_note_documents").fetchone() == (0,)


def test_the_export_carries_each_notes_documents(world: World) -> None:
    ada = start(world)
    keep(ada, ("notes.md", md(f"The word is {SECOND}.")))
    note = typed(ada, f"The word is {SECOND}.")
    link(ada, note, doc_id(ada, "notes.md"))
    exported = ada.get("/api/memory/export", params={"format": "json"}).json()
    [entry] = [n for n in exported["notes"] if n["id"] == note]
    assert [d["name"] for d in entry["documents"]] == ["notes.md"]
    assert entry["documents"][0]["how"] == "person"


# --------------------------------------------------------------------------- #
# the tidy pass
# --------------------------------------------------------------------------- #


def n(note_id: str, text: str, kind: str = "fact", **more: Any) -> MemoryNote:
    return MemoryNote(
        id=note_id, owner="ada", text=text, kind=kind, created_at=float(len(note_id)),
        updated_at=0.0, **more,
    )  # fmt: skip


def test_duplicates_are_the_same_words_or_most_of_them_within_one_kind() -> None:
    found = memory_tidy.duplicates(
        [
            n("a", "Lives in Leeds."),
            n("bb", "lives in  LEEDS."),
            n("ccc", "Lives in Leeds with two cats."),
            n("dddd", "Lives in Leeds.", kind="preference"),
            n("eeeee", "Prefers short answers in bullet points."),
            n("ffffff", "Keeps bees in the garden."),
            n("ggggggg", "Lives in Leeds.", status="superseded"),
        ]
    )
    assert ("a", "bb") in found
    assert ("a", "ccc") not in found, "two of five words is not most of them"
    assert not any("dddd" in p for p in found), "never across kinds"
    assert not any("ggggggg" in p for p in found), "only current notes"
    assert memory_tidy.duplicates(
        [
            n("a", "Prefers short answers in bullet points."),
            n("bb", "Prefers short answers in bullets."),
        ]
    ) == [("a", "bb")]


def test_an_event_is_proposed_thirty_days_after_its_day() -> None:
    today = date(2026, 10, 19)

    def ago(days: int) -> str:
        return (today - timedelta(days=days)).isoformat()

    notes = [
        n("a", "Flight to Lisbon.", "event", expires_at=ago(31)),
        n("bb", "Dentist.", "event", expires_at=ago(29)),
        n("ccc", "A fact.", expires_at=ago(90)),
    ]
    assert memory_tidy.past_events(notes, today.isoformat()) == ["a"]


def test_a_branching_chain_is_found() -> None:
    assert memory_tidy.branching(
        [n("a", "x", superseded_by="c"), n("bb", "y", superseded_by="c"), n("c", "z")]
    ) == ["c"]


def force_tidy(world: World, owner: str = "p-ada") -> None:
    async def go() -> None:
        store = Store(world.data / "workbench.sqlite3")
        await store.open()
        try:
            await memory_tidy.run(store, owner, date.today().isoformat(), force=True)
        finally:
            await store.close()

    asyncio.run(go())


def test_merging_keeps_one_note_with_both_notes_pins_tags_and_history(world: World) -> None:
    ada = start(world)
    older = typed(ada, "Lives in Leeds.", tags=["home"], body="Moved in 2020.")
    newer = typed(ada, "lives in leeds.", pinned=True, tags=["city"])
    [item] = [t for t in page(ada)["tidy"] if t["kind"] == "duplicate"]
    assert {x["id"] for x in item["notes"]} == {older, newer}
    assert ada.post(f"/api/memory/tidy/{item['id']}", json={"action": "keep"}).status_code == 400
    done = ada.post(f"/api/memory/tidy/{item['id']}", json={"action": "keep", "keepId": newer})
    assert done.status_code == 200, done.text
    kept = done.json()["note"]
    assert kept["id"] == newer and kept["pinned"] is True
    assert set(kept["tags"]) == {"home", "city"}
    whole = ada.get(f"/api/memory/notes/{newer}").json()
    assert whole["note"]["body"] == "Moved in 2020.", "a body comes over when the kept has none"
    assert [e["id"] for e in whole["earlier"]] == [older]
    assert page(ada)["tidy"] == []
    with db_of(world) as db:
        logged = db.execute(
            "SELECT old_text, new_text FROM memory_events WHERE event = 'merge'"
        ).fetchall()
    assert logged and all(row == (None, None) for row in logged), "a merge logs no words"
    # Deleting the kept note takes the merged one with it: nothing is left behind.
    assert ada.delete(f"/api/memory/notes/{newer}").status_code == 204
    with db_of(world) as db:
        assert db.execute("SELECT COUNT(*) FROM memory_notes WHERE owner = 'p-ada'").fetchone() == (
            0,
        )


def test_merging_below_a_history_keeps_one_line_of_versions(world: World) -> None:
    ada = start(world)
    first = typed(ada, "Works as a teacher.")
    current = ada.patch(
        f"/api/memory/notes/{first}", json={"text": "Works as a tester.", "asNewVersion": True}
    ).json()["note"]["id"]
    twin = typed(ada, "Works as a tester!")
    [item] = page(ada)["tidy"]
    done = ada.post(f"/api/memory/tidy/{item['id']}", json={"action": "keep", "keepId": current})
    assert done.status_code == 200, done.text
    earlier = ada.get(f"/api/memory/notes/{current}").json()["earlier"]
    assert [e["id"] for e in earlier] == [first, twin]
    assert ada.delete(f"/api/memory/notes/{current}").status_code == 204
    with db_of(world) as db:
        assert db.execute("SELECT COUNT(*) FROM memory_notes").fetchone() == (0,)


def test_keep_both_is_never_proposed_again_and_an_old_event_is_removed(world: World) -> None:
    ada = start(world)
    one = typed(ada, "Prefers tea.")
    two = typed(ada, "Prefers tea!")
    event = typed(ada, "Flight to Lisbon.", kind="event")
    with db_of(world) as db:
        old = (date.today() - timedelta(days=40)).isoformat()
        db.execute("UPDATE memory_notes SET expires_at = ? WHERE id = ?", (old, event))
    items = {t["kind"]: t for t in page(ada)["tidy"]}
    assert {x["id"] for x in items["duplicate"]["notes"]} == {one, two}
    assert [x["id"] for x in items["past"]["notes"]] == [event]
    both = ada.post(f"/api/memory/tidy/{items['duplicate']['id']}", json={"action": "keep_both"})
    assert both.status_code == 200
    removed = ada.post(f"/api/memory/tidy/{items['past']['id']}", json={"action": "remove"})
    assert removed.status_code == 200, removed.text
    assert ada.get(f"/api/memory/notes/{event}").status_code == 404
    force_tidy(world)
    assert page(ada)["tidy"] == [], "a pair kept is not proposed again"
    assert (
        ada.post(f"/api/memory/tidy/{items['past']['id']}", json={"action": "remove"}).status_code
        == 404
    )


def test_a_proposal_whose_notes_changed_is_dropped(world: World) -> None:
    ada = start(world)
    one = typed(ada, "Prefers tea.")
    typed(ada, "Prefers tea!")
    [item] = page(ada)["tidy"]
    assert ada.delete(f"/api/memory/notes/{one}").status_code == 204
    assert page(ada)["tidy"] == []
    gone = ada.post(f"/api/memory/tidy/{item['id']}", json={"action": "keep_both"})
    assert gone.status_code == 404


def test_the_pass_runs_once_a_day(world: World) -> None:
    ada = start(world)
    typed(ada, "Prefers tea.")
    assert page(ada)["tidy"] == []
    typed(ada, "Prefers tea!")
    assert page(ada)["tidy"] == [], "not again within the day"
    force_tidy(world)
    assert len(page(ada)["tidy"]) == 1


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #


def test_a_schema_25_store_gains_links_tidy_and_its_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "workbench.sqlite3"

    async def make() -> None:
        store = Store(path)
        await store.open()
        await store.add_memory_note(
            MemoryNote(
                id="n1", owner="ada", text="Lives in Leeds.", kind="fact",
                created_at=1.0, updated_at=1.0,
            ),
            limit=100,
        )  # fmt: skip
        await store.close()

    with monkeypatch.context() as m:
        m.setattr(storage, "SCHEMA_VERSION", 25)
        m.setattr(storage, "_provenance", lambda db: None)
        m.setattr(storage, "_memory_extras", lambda db: None)
        m.setattr(storage, "MEMORY_PREFS_DEFAULT", {
            k: v for k, v in storage.MEMORY_PREFS_DEFAULT.items() if k != "tidy_at"
        })  # fmt: skip
        asyncio.run(make())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == "25"
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
        assert "memory_note_documents" not in tables

    async def check() -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
        store = Store(path)
        await store.open()
        try:
            count = await store.link_note_documents(
                "ada", "n1",
                [{"doc_id": "d", "scope": "library", "source": "upload", "name": "a.md",
                  "sha256": "x", "place": []}],
                "person", 20,
            )  # fmt: skip
            await store.add_tidy("ada", "past", ["n1"])
            return count, await store.tidy_rows("ada"), await store.memory_prefs("ada")
        finally:
            await store.close()

    count, rows, prefs = asyncio.run(check())
    assert count == 1 and rows[0]["note_ids"] == ["n1"] and prefs["tidy_at"] is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == str(
            SCHEMA_VERSION
        )
    assert list(tmp_path.glob("*.schema-25*")), "the store's backup before the migration"


def test_at_most_twenty_documents_a_note(tmp_path: Path) -> None:
    async def go() -> int:
        store = Store(tmp_path / "w.sqlite3")
        await store.open()
        try:
            await store.add_memory_note(n("n1", "A note."), limit=100)
            links = [
                {"doc_id": f"d{i}", "scope": "library", "source": "upload", "name": f"{i}.md",
                 "sha256": "x", "place": []}
                for i in range(25)
            ]  # fmt: skip
            return await store.link_note_documents("ada", "n1", links, "answer", 20)
        finally:
            await store.close()

    assert asyncio.run(go()) == 20


def test_notes_that_contradict_each_other_are_never_proposed() -> None:
    pairs = [
        ("Prefers tabs over spaces.", "Prefers spaces over tabs."),
        ("Has 2 kids.", "Has 3 kids."),
        ("Is allergic to peanuts.", "Is not allergic to peanuts."),
        ("Likes coffee in the morning with milk.", "Dislikes coffee in the morning with milk."),
    ]
    for a, b in pairs:
        assert memory_tidy.duplicates([n("a", a), n("bb", b)]) == [], (a, b)
    # Two events on different days are two events.
    assert (
        memory_tidy.duplicates(
            [
                n("a", "Dentist appointment.", "event", expires_at="2026-11-02"),
                n("bb", "Dentist appointment.", "event", expires_at="2026-12-07"),
            ]
        )
        == []
    )


def test_long_notes_that_share_most_words_are_found_whatever_their_rarest() -> None:
    shared = "apple bread cider dough eagle fable grape house igloo jelly kites lemon mango night ocean pearl"
    filler = [n(f"x{i}", f"Other note {i} about apple and bread.") for i in range(30)]
    found = memory_tidy.duplicates(
        [
            n("a", f"{shared} zebra yacht xenon vivid."),
            n("bb", f"{shared} quilt risky sugar tiger."),
            *filler,
        ]
    )
    assert ("a", "bb") in found


def test_carrying_documents_keeps_to_the_cap(tmp_path: Path) -> None:
    def links(prefix: str) -> list[dict[str, Any]]:
        return [
            {"doc_id": f"{prefix}{i}", "scope": "library", "source": "upload",
             "name": f"{prefix}{i}.md", "sha256": "x", "place": []}
            for i in range(15)
        ]  # fmt: skip

    async def go() -> int:
        store = Store(tmp_path / "w.sqlite3")
        await store.open()
        try:
            await store.add_memory_note(n("k", "A note."), limit=100)
            await store.add_memory_note(n("oo", "The same note."), limit=100)
            await store.link_note_documents("ada", "k", links("a"), "person", 20)
            await store.link_note_documents("ada", "oo", links("b"), "person", 20)
            assert await store.merge_memory_notes("ada", "k", "oo") is not None
            return len((await store.note_documents("ada", ["k"]))["k"])
        finally:
            await store.close()

    assert asyncio.run(go()) == 20
