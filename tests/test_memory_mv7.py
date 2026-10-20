"""Memory v2's MV7a: chat projects and the notes kept to them.

Through a real Workbench over the fake gateway (`conftest.py`): a project's notes reach only
its chats, general ones reach them too only while Also use my general memory is on, every
writer keeps a note where it was said, and deleting a project either keeps what was in it as
general or takes it all.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import memory_servers, memory_tidy, memory_write, projects
from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.store import SCHEMA_VERSION, Chat, MemoryNote, Message, Store

from .conftest import MODEL, Browser, World
from .test_connections import _connect, call
from .test_memory_api import ask, keep, last_user, signed_in, system_of


def project(person: Browser, name: str) -> str:
    made = person.post("/api/projects", json={"name": name})
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


def chat_in(person: Browser, project_id: str | None) -> str:
    made = person.post("/api/chats", json={"projectId": project_id, "model": MODEL})
    assert made.status_code in (200, 201), made.text
    return str(made.json()["id"])


def db_of(world: World) -> sqlite3.Connection:
    return sqlite3.connect(world.data / "workbench.sqlite3")


def test_projects_are_made_named_once_renamed_and_listed_with_what_they_hold(
    world: World,
) -> None:
    ada = signed_in(world)
    garden = project(ada, "  Garden   plans ")
    assert ada.post("/api/projects", json={"name": "garden PLANS"}).status_code == 409
    assert ada.post("/api/projects", json={"name": "   "}).status_code == 422
    assert ada.post("/api/projects", json={"name": "x" * 81}).status_code == 422
    chat_in(ada, garden)
    keep(ada, "The shed key is under the mat.", projectId=garden)
    [listed] = ada.get("/api/projects").json()["projects"]
    assert listed["name"] == "Garden plans" and listed["chats"] == 1 and listed["notes"] == 1
    renamed = ada.patch(f"/api/projects/{garden}", json={"name": "Allotment"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Allotment"
    whole = ada.get(f"/api/projects/{garden}").json()
    assert [c["projectId"] for c in whole["chatList"]] == [garden]
    bo = signed_in(world, "p-bo")
    assert bo.get(f"/api/projects/{garden}").status_code == 404
    assert bo.post("/api/chats", json={"projectId": garden}).status_code == 404
    assert bo.get("/api/projects").json()["projects"] == []


def test_a_projects_notes_reach_only_its_chats_and_general_ones_as_it_says(
    world: World,
) -> None:
    ada = signed_in(world)
    garden = project(ada, "Garden")
    keep(ada, "The shed key is under the mat.", projectId=garden, pinned=True)
    keep(ada, "Prefers metric units.", pinned=True)
    outside = chat_in(ada, None)
    sent = system_of(ask(world, ada, outside)) or ""
    assert "shed key" not in sent and "metric" in sent, "a project's notes stay in it"
    inside = chat_in(ada, garden)
    sent = system_of(ask(world, ada, inside)) or ""
    assert "shed key" in sent and "metric" in sent
    off = ada.patch(f"/api/projects/{garden}", json={"generalMemory": False})
    assert off.status_code == 200 and off.json()["generalMemory"] is False
    sent = system_of(ask(world, ada, inside)) or ""
    assert "shed key" in sent and "metric" not in sent, "the switch is read again at once"


def test_relevant_memory_is_chosen_from_the_place_before_it_is_ranked(world: World) -> None:
    ada = signed_in(world)
    garden = project(ada, "Garden")
    for i in range(30):
        keep(ada, f"Garden note {i}: the garden hose is in the garage.")
    keep(ada, "Garden gate code is 4417.", projectId=garden)
    ada.patch(f"/api/projects/{garden}", json={"generalMemory": False})
    inside = chat_in(ada, garden)
    said = last_user(ask(world, ada, inside, "Where is the garden gate code?"))
    assert "4417" in said and "garden hose" not in said
    outside = chat_in(ada, None)
    said = last_user(ask(world, ada, outside, "What is the garden gate code?"))
    assert "4417" not in said


def test_a_note_belongs_where_it_was_said(world: World) -> None:
    ada = signed_in(world)
    garden = project(ada, "Garden")
    inside = chat_in(ada, garden)
    ask(world, ada, inside, "Remember that the tomatoes need staking")
    said = ada.get("/api/memory/notes").json()["notes"]
    [tomatoes] = [n for n in said if "tomatoes" in n["text"]]
    assert tomatoes["projectId"] == garden
    answer = ada.get(f"/api/chats/{inside}").json()["messages"][-1]
    pressed = ada.post(
        "/api/memory/notes",
        json={"text": "Stake them in May.", "source": "button", "chatId": inside,
              "messageId": answer["id"]},
    )  # fmt: skip
    assert pressed.status_code == 201 and pressed.json()["note"]["projectId"] == garden
    # The same words kept in General and in a project are two notes, one in each place.
    general = keep(ada, "Stake them in May.")
    assert general["projectId"] is None and general["id"] != pressed.json()["note"]["id"]


def test_a_models_note_is_kept_to_its_chats_project() -> None:
    proposal = memory_write.memory_suggest.Proposal(
        text="Grows tomatoes.", kind="fact", quote="I grow tomatoes", message=0
    )
    routed = memory_write.Routed(proposal=proposal, action="suggest", why=None)
    note = memory_write.note_of(
        "ada", "c1", "m1", routed, "m", memory_write.memory_policy.preset("standard"), [], 1.0,
        "proj-1",
    )  # fmt: skip
    assert note.project_id == "proj-1"


def test_moving_a_chat_takes_its_notes_again_from_where_it_is(world: World) -> None:
    ada = signed_in(world)
    garden = project(ada, "Garden")
    keep(ada, "The shed key is under the mat.", projectId=garden, pinned=True)
    chat = chat_in(ada, None)
    assert "shed key" not in (system_of(ask(world, ada, chat)) or "")
    moved = ada.patch(f"/api/chats/{chat}", json={"projectId": garden})
    assert moved.status_code == 200 and moved.json()["projectId"] == garden
    assert "shed key" in (system_of(ask(world, ada, chat)) or "")
    assert ada.patch(f"/api/chats/{chat}", json={"projectId": "nope"}).status_code == 404
    back = ada.patch(f"/api/chats/{chat}", json={"projectId": None})
    assert back.status_code == 200 and back.json()["projectId"] is None
    assert "shed key" not in (system_of(ask(world, ada, chat)) or "")


def test_a_note_moves_with_its_versions_and_the_where_filter_finds_it(world: World) -> None:
    ada = signed_in(world)
    garden = project(ada, "Garden")
    note = keep(ada, "Waters on Mondays.")
    newer = ada.patch(
        f"/api/memory/notes/{note['id']}",
        json={"text": "Waters on Tuesdays.", "asNewVersion": True},
    ).json()["note"]
    assert newer["projectId"] is None
    moved = ada.post(f"/api/memory/notes/{newer['id']}/move", json={"projectId": garden})
    assert moved.status_code == 200 and moved.json()["note"]["projectId"] == garden
    with db_of(world) as db:
        assert {r[0] for r in db.execute("SELECT project_id FROM memory_notes")} == {garden}
    there = ada.get("/api/memory/notes", params={"where": garden}).json()["notes"]
    assert [n["id"] for n in there] == [newer["id"]]
    assert ada.get("/api/memory/notes", params={"where": "general"}).json()["notes"] == []
    page = ada.get("/api/memory", params={"where": garden}).json()
    assert [n["id"] for n in page["sections"]["other"]] == [newer["id"]]
    assert (
        ada.post(f"/api/memory/notes/{newer['id']}/move", json={"projectId": "x"}).status_code
        == 404
    )
    again = ada.post(f"/api/memory/notes/{newer['id']}/move", json={"projectId": None})
    assert again.json()["note"]["projectId"] is None


def test_a_connection_is_given_general_notes_only(world: World) -> None:
    ada = signed_in(world)
    garden = project(ada, "Garden")
    keep(ada, "Uses metric units in the garden.", projectId=garden)
    keep(ada, "Uses metric units for every recipe.")
    made = _connect(ada, notes=True)
    found = call(world, made["token"], "search_notes", query="metric units")
    texts = [n["text"] for n in found.structured_content["notes"]]
    assert texts == ["Uses metric units for every recipe."]


def test_a_chat_in_a_project_is_not_sent_to_a_memory_server(tmp_path: Path) -> None:
    async def go() -> tuple[str | None, str | None]:
        store = Store(tmp_path / "w.sqlite3")
        await store.open()
        try:
            chat = Chat(id="c1", owner="ada", title="t", model="m", created_at=1.0,
                        updated_at=1.0, project_id="p1")  # fmt: skip
            asked = Message(id="u1", chat_id="c1", seq=0, role="user", status="done",
                            created_at=1.0, content="I grow tomatoes.")  # fmt: skip
            answer = Message(id="a1", chat_id="c1", seq=1, role="assistant", status="done",
                             created_at=2.0, content="Nice.", parent_id="u1")  # fmt: skip
            return await memory_servers.content_for(store, "ada", chat, [asked, answer], answer)
        finally:
            await store.close()

    assert asyncio.run(go()) == (None, "the chat is in a project")


def test_the_tidy_pass_stays_within_one_place() -> None:
    def n(note_id: str, text: str, project_id: str | None) -> MemoryNote:
        return MemoryNote(id=note_id, owner="ada", text=text, kind="fact",
                          created_at=float(len(note_id)), updated_at=0.0,
                          project_id=project_id)  # fmt: skip

    assert (
        memory_tidy.duplicates([n("a", "Prefers tea.", "p1"), n("bb", "Prefers tea.", None)]) == []
    )
    assert memory_tidy.duplicates(
        [n("a", "Prefers tea.", "p1"), n("bb", "prefers tea.", "p1")]
    ) == [("a", "bb")]


def test_in_place_puts_the_projects_notes_first() -> None:
    notes = [
        MemoryNote(id="g", owner="a", text="General.", kind="fact", created_at=2, updated_at=2),
        MemoryNote(id="p", owner="a", text="Mine.", kind="fact", created_at=1, updated_at=1,
                   project_id="p1"),
        MemoryNote(id="o", owner="a", text="Other.", kind="fact", created_at=1, updated_at=1,
                   project_id="p2"),
    ]  # fmt: skip
    assert [x.id for x in projects.in_place(notes, projects.Place("p1", True))] == ["p", "g"]
    assert [x.id for x in projects.in_place(notes, projects.Place("p1", False))] == ["p"]
    assert [x.id for x in projects.in_place(notes, projects.GENERAL)] == ["g"]


def test_deleting_a_project_keeps_or_takes_what_was_in_it(world: World) -> None:
    ada = signed_in(world)
    kept = project(ada, "Kept")
    gone = project(ada, "Gone")
    for where in (kept, gone):
        chat = chat_in(ada, where)
        ask(world, ada, chat, "Remember that this is in a project")
        keep(ada, f"A note of {where}.", projectId=where)
    released = ada.delete(f"/api/projects/{kept}", params={"keep": "true"})
    assert released.status_code == 200 and released.json() == {
        "kept": True, "chats": 1, "notes": 2, "documents": 0, "replaced": 0,
    }  # fmt: skip
    taken = ada.delete(f"/api/projects/{gone}")
    assert taken.status_code == 200 and taken.json()["chats"] == 1 and taken.json()["notes"] == 2
    assert ada.get("/api/projects").json()["projects"] == []
    with db_of(world) as db:
        assert db.execute("SELECT COUNT(*) FROM chats WHERE project_id IS NOT NULL").fetchone() == (
            0,
        )
        assert db.execute(
            "SELECT COUNT(*) FROM memory_notes WHERE project_id IS NOT NULL"
        ).fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM chats").fetchone() == (1,), "the kept one's"
    assert ada.delete(f"/api/projects/{gone}").status_code == 404


def test_a_schema_26_store_gains_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "workbench.sqlite3"

    async def make() -> None:
        store = Store(path)
        await store.open()
        await store.close()

    with monkeypatch.context() as m:
        m.setattr(storage, "SCHEMA_VERSION", 26)
        m.setattr(storage, "_projects", lambda db: None)
        m.setattr(storage, "_NOTE_LIST", storage._NOTE_LIST.replace(", project_id", ""))
        insert = storage.Store._insert_note

        def old_insert(db: sqlite3.Connection, note: MemoryNote) -> None:
            db.execute(
                "INSERT INTO memory_notes (id, owner, text, kind, status, pinned, source, "
                "created_at, updated_at, valid_from) VALUES (?, ?, ?, ?, 'active', 0, 'typed', "
                "1.0, 1.0, 1.0)",
                (note.id, note.owner, note.text, note.kind),
            )

        m.setattr(storage.Store, "_insert_note", staticmethod(old_insert))
        asyncio.run(make())
        assert insert is not None
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO memory_notes (id, owner, text, kind, status, pinned, source, "
            "created_at, updated_at, valid_from) VALUES ('n1', 'ada', 'Lives in Leeds.', "
            "'fact', 'active', 0, 'typed', 1.0, 1.0, 1.0)"
        )
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == "26"
        assert "project_id" not in {r[1] for r in db.execute("PRAGMA table_info(chats)")}

    async def check() -> list[Any]:
        store = Store(path)
        await store.open()
        try:
            return await store.memory_notes("ada")
        finally:
            await store.close()

    [note] = asyncio.run(check())
    assert note.text == "Lives in Leeds." and note.project_id is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == str(
            SCHEMA_VERSION
        )
        assert "project_id" in {r[1] for r in db.execute("PRAGMA table_info(chats)")}
    assert list(tmp_path.glob("*.schema-26*")), "the store's backup before the migration"


def test_past_chats_of_a_project_reach_only_its_chats(world: World) -> None:
    from .test_past_chats import _say, _users, settle

    ada = signed_in(world)
    garden = project(ada, "Garden")
    _say(world, "Your bicycle is a blue Brompton.")
    inside = chat_in(ada, garden)
    ask(world, ada, inside, "Which folding bicycle did I buy last spring?")
    settle(world, "p-ada", lambda h: len(h) == 1)
    _say(world, "Noted.")
    outside = chat_in(ada, None)
    ada.patch(f"/api/chats/{outside}", json={"settings": {"pastChats": True}})
    sent = ask(world, ada, outside, "Which folding bicycle do I have?")
    assert "blue Brompton" not in _users(sent)[-1], "a project's chats stay in it"
    other = chat_in(ada, garden)
    ada.patch(f"/api/chats/{other}", json={"settings": {"pastChats": True}})
    sent = ask(world, ada, other, "Which folding bicycle do I have?")
    assert "blue Brompton" in _users(sent)[-1], "and reach its other chats"
    # The person's own Search chats finds every chat.
    found = ada.get("/api/past-chats", params={"q": "folding bicycle"}).json()["results"]
    assert any(r["chatId"] == inside for r in found)


def test_a_projects_note_is_found_by_meaning_however_many_other_notes_outrank_it() -> None:
    """Only the pool's notes count towards the eight best by meaning (MV7): filtered after
    that cut, eight better notes elsewhere hid a project's note."""
    from types import SimpleNamespace

    from eugene_plexus_workbench.knowledge.index import Hit
    from eugene_plexus_workbench.knowledge.search import plan_query
    from eugene_plexus_workbench.memory_relevance import MEANING_TOP, Finder

    def hit(doc_id: str, score: float) -> Hit:
        return Hit(chunk_id=1, doc_id=doc_id, ord=0, text="t", heading_path=None,
                   page_start=None, page_end=None, line_start=None, line_end=None,
                   char_start=None, char_end=None, tokens_est=1, name="n", scope="notes",
                   kind="text", score=score)  # fmt: skip

    others = [hit(f"g{i}", 0.9 - i / 100) for i in range(MEANING_TOP + 4)]
    found = [*others, hit("mine", 0.5)]

    async def search(*_args: Any) -> list[Hit]:
        return found

    async def nothing(*_args: Any) -> None:
        return None

    async def resolve(_built: Any) -> Any:
        return SimpleNamespace(model=SimpleNamespace(local=True, id="m"))

    meaning = SimpleNamespace(
        _built=lambda e: e, resolve=resolve, touch=nothing, floor=nothing,
        signal=SimpleNamespace(search=search),
    )  # fmt: skip

    async def embedder() -> None:
        return None

    async def counts(_scope: str) -> Any:
        return SimpleNamespace(embedded=1)

    index = SimpleNamespace(embedder=embedder, meaning_counts=counts)
    finder = Finder(store=None, saved=SimpleNamespace(meaning=meaning))  # type: ignore[arg-type]
    query = plan_query("garden gate code")
    everywhere, _ = asyncio.run(finder._meaning(index, query))
    assert everywhere is not None and "mine" not in {h.doc_id for h in everywhere}
    pooled, _ = asyncio.run(finder._meaning(index, query, {"mine"}))
    assert pooled is not None and [h.doc_id for h in pooled] == ["mine"]


def test_forget_offers_only_the_notes_this_chat_may_be_given(world: World) -> None:
    ada = signed_in(world)
    acme = project(ada, "Acme")
    keep(ada, "The Acme merger closes in March.", projectId=acme)
    outside = chat_in(ada, None)
    sent = ada.post(f"/api/chats/{outside}/messages", json={"content": "Forget the merger"})
    assert sent.status_code == 201, sent.text
    assert sent.json()["memory"]["candidates"] == [], "a project's notes are not offered"
    inside = chat_in(ada, acme)
    sent = ada.post(f"/api/chats/{inside}/messages", json={"content": "Forget the merger"})
    assert [c["text"] for c in sent.json()["memory"]["candidates"]] == [
        "The Acme merger closes in March."
    ]


def test_a_connection_cannot_read_a_projects_note_by_its_id(world: World) -> None:
    ada = signed_in(world)
    acme = project(ada, "Acme")
    secret = keep(ada, "The Acme merger closes in March.", projectId=acme)
    made = _connect(ada, notes=True)
    found = call(world, made["token"], "read_note", note_id=secret["id"])
    assert found.is_error and "no such note" in found.content[0].text


def test_the_same_words_are_offered_in_another_place(tmp_path: Path) -> None:
    async def go() -> list[MemoryNote]:
        store = Store(tmp_path / "w.sqlite3")
        await store.open()
        try:
            await store.create_chat(
                Chat(id="c1", owner="ada", title="t", model="m", created_at=1.0, updated_at=1.0,
                     project_id="p1")
            )  # fmt: skip
            await store.add_memory_note(
                MemoryNote(id="g", owner="ada", text="Grows tomatoes.", kind="fact",
                           created_at=1.0, updated_at=1.0),
                limit=100,
            )  # fmt: skip
            offered = MemoryNote(id="s", owner="ada", text="Grows tomatoes.", kind="fact",
                                 created_at=2.0, updated_at=2.0, project_id="p1")  # fmt: skip
            return await store.add_suggestions("ada", "c1", [offered], read_at=2.0, limit=100)
        finally:
            await store.close()

    [kept] = asyncio.run(go())
    assert kept.project_id == "p1"


def test_nothing_is_left_under_a_deleted_projects_id(tmp_path: Path) -> None:
    async def go() -> tuple[Any, Any]:
        store = Store(tmp_path / "w.sqlite3")
        await store.open()
        try:
            await store.create_chat(
                Chat(id="c1", owner="ada", title="t", model="m", created_at=1.0, updated_at=1.0,
                     project_id="p1")
            )  # fmt: skip
            await store.add_memory_note(
                MemoryNote(id="n", owner="ada", text="Left behind.", kind="fact", created_at=1.0,
                           updated_at=1.0, status="pending", project_id="p1"),
                limit=100,
            )  # fmt: skip
            await store.drop_project_row("ada", "p1")
            return await store.chat("c1"), await store.memory_note("ada", "n")
        finally:
            await store.close()

    chat, note = asyncio.run(go())
    assert chat is not None and chat.project_id is None
    assert note is not None and note.project_id is None


def test_the_learning_pass_is_told_no_tag_only_another_places_notes_carry(world: World) -> None:
    from eugene_plexus_workbench.memory_learn import _tags_elsewhere

    ada = signed_in(world)
    acme = project(ada, "Acme")
    keep(ada, "The merger closes in March.", projectId=acme, tags=["merger"])
    keep(ada, "Prefers metric units.", tags=["units"])
    keep(ada, "Uses the shared drive.", projectId=acme, tags=["units"])

    async def go() -> set[str]:
        store = Store(world.data / "workbench.sqlite3")
        await store.open()
        try:
            chat = Chat(id="x", owner="p-ada", title="t", model="m", created_at=1, updated_at=1)
            return await _tags_elsewhere(store, "p-ada", chat)
        finally:
            await store.close()

    assert asyncio.run(go()) == {"merger"}


def test_the_export_says_where_each_note_is(world: World) -> None:
    ada = signed_in(world)
    acme = project(ada, "Acme")
    keep(ada, "The merger closes in March.", projectId=acme)
    keep(ada, "Prefers metric units.")
    notes = ada.get("/api/memory/export", params={"format": "json"}).json()["notes"]
    assert {n["text"]: n["project"] for n in notes} == {
        "The merger closes in March.": "Acme",
        "Prefers metric units.": None,
    }


def test_a_name_of_characters_nobody_sees_is_refused(world: World) -> None:
    ada = signed_in(world)
    refused = ada.post("/api/projects", json={"name": "​​"})
    assert refused.status_code == 422 and "not seen" in refused.text
