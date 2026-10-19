"""A chat's own documents ("Search it instead"), what a restart puts right, and
whose documents are whose (MR4), through a real Workbench over a fake Eugene.

A file attached to a chat can be searched for each question instead of read whole; it
is searched in that chat whatever the switch says, and goes with the chat. Two people
never see each other's documents on any route, and the install's owner sees no
person's documents even where they may read that person's chats.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

import httpx

from eugene_plexus_workbench import files

from .conftest import MODEL, PNG, Browser, World, chat_model
from .saved import (
    RARE,
    SECOND,
    add,
    ask,
    block_of,
    has_block,
    index_of,
    keep,
    knowledge_files,
    last,
    listing,
    md,
    raw,
    send,
    settings,
    settle,
    start,
    survey_pdf,
    wait_for,
)

CODE = "RAVEN-7731"
QUESTION = "What is the code word for the lighthouse inspection?"


def attach(
    person: Browser, chat: str, name: str, data: bytes, media: str = "application/pdf"
) -> str:
    made = person.post(f"/api/chats/{chat}/files", files={"file": (name, data, media)})
    assert made.status_code == 201, made.text
    file_id = str(made.json()["id"])
    found = person.wait_read(chat, file_id)
    assert found["reading"]["state"] == "ready", found
    return file_id


def search_state(person: Browser, chat: str, file_id: str) -> dict[str, Any] | None:
    listed = person.get(f"/api/chats/{chat}/files").json()["files"]
    found = next(f for f in listed if f["id"] == file_id)
    return found["search"]  # type: ignore[no-any-return]


def choose_search(person: Browser, chat: str, file_id: str) -> Any:
    return person.put(f"/api/chats/{chat}/files/{file_id}/reading", json={"mode": "search"})


def wait_searchable(
    person: Browser, chat: str, file_id: str, seconds: float = 90
) -> dict[str, Any]:
    wait_for(
        lambda: (
            (search_state(person, chat, file_id) or {}).get("status")
            in ("searchable", "ready", "skipped", "failed")
        ),
        seconds,
    )
    state = search_state(person, chat, file_id)
    assert state is not None
    return state


def long_survey() -> bytes:
    return survey_pdf(CODE, pages=12, page=9)


def window(world: World, tokens: int = 8192) -> None:
    world.gateway.context_length = tokens


def chat_documents(world: World, sub: str = "p-ada") -> list[tuple[str, str, str]]:
    """Every document in a person's index: (scope, name, state); not their past chats'
    exchanges (MV2), which are the person's memory, not documents."""
    if not index_of(world, sub).exists():
        return []
    with raw(world, sub) as db:
        return [
            tuple(r)
            for r in db.execute(
                "SELECT scope, name, status FROM documents WHERE scope != 'exchanges'"
            )
        ]


# --------------------------------------------------------------------------- #
# Search it instead
# --------------------------------------------------------------------------- #


def test_a_file_too_long_for_the_window_can_be_searched_instead_and_nothing_of_it_is_read_whole(
    world: World,
) -> None:
    window(world)
    ada = start(world)
    chat = ada.new_chat()
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    item = next(
        f for f in ada.get(f"/api/chats/{chat}/files").json()["files"] if f["id"] == file_id
    )
    assert item["decision"]["kind"] == "too_long" and item["search"] is None
    actions = [o["action"] for o in item["decision"]["offers"]]
    assert actions[-1] == "search" and "readFirst" in actions, "searching instead is always offered"
    refused = send(ada, chat, QUESTION, attachments=[file_id])
    assert refused.status_code == 409, "not sent until the person chooses"
    chosen = choose_search(ada, chat, file_id)
    assert chosen.status_code == 200, chosen.text
    first = chosen.json()
    assert first["search"]["status"] in ("queued", "reading", "searchable", "ready")
    assert first["search"]["docId"] and first["reading"]["chosen"] == {"mode": "search"}
    state = wait_searchable(ada, chat, file_id)
    assert state["status"] in ("searchable", "ready") and state["passages"] >= 12
    item = next(
        f for f in ada.get(f"/api/chats/{chat}/files").json()["files"] if f["id"] == file_id
    )
    decision = item["decision"]
    assert (decision["kind"], decision["send"], decision["blockTokens"]) == ("search", True, 0)
    assert decision["message"] == "Will be searched for each question, not read whole."
    answer = ask(ada, chat, QUESTION, attachments=[file_id])
    assert answer["status"] == "done"
    text = last(world)[-1]["content"]
    assert 'type="pdf"' not in text and "[page 1]" not in text, "the file itself was not sent"
    [(_, _, name, attributes, words), *_] = block_of(world)[2]
    assert name == "survey.pdf" and ' pages="9"' in attributes and CODE in words
    assert text.endswith("\n\n" + QUESTION)
    passages = [s for s in answer["sources"] if s.get("passage")]
    assert [s["scope"] for s in passages] == ["chat"] * len(passages)
    assert not [s for s in answer["sources"] if not s.get("passage")], "no whole-file source"
    assert answer["retrieval"]["scope"] == {"library": False, "chat": True}


def test_a_chats_file_is_searched_in_that_chat_whatever_the_switch_says_and_in_no_other(
    world: World,
) -> None:
    window(world)
    ada = start(world)
    chat = ada.new_chat()  # Documents off
    assert ada.get(f"/api/chats/{chat}").json()["chat"]["settings"]["documents"] is None
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    choose_search(ada, chat, file_id)
    wait_searchable(ada, chat, file_id)
    state = ada.get(f"/api/chats/{chat}/documents").json()
    assert (state["switch"], state["sending"]) == ("off", True)
    assert state["searching"] == {"library": False, "chat": True} and state["reason"] is None
    assert [d["name"] for d in state["chatDocuments"]] == ["survey.pdf"]
    ask(ada, chat, QUESTION, attachments=[file_id])
    assert CODE in block_of(world)[1]
    other = ada.post("/api/chats", json={"model": MODEL}).json()["id"]
    ada.patch(f"/api/chats/{other}", json={"settings": {"documents": "on"}})
    ask(ada, other, QUESTION)
    assert not has_block(world), "another chat of the same person does not see it"
    listed = listing(ada)
    assert listed["documents"] == [], "and it is not in My documents"
    assert (
        ada.post("/api/documents/test", json={"question": QUESTION}).json()["status"]
        == "no_documents"
    )
    follow = ask(ada, chat, "And what about the weather and the tide?")
    assert follow["status"] == "done" and follow["retrieval"]["scope"]["chat"] is True


def test_a_chat_with_the_switch_off_searches_only_its_own_files_and_never_my_documents(
    world: World,
) -> None:
    window(world)
    ada = start(world)
    keep(ada, ("keys.md", md(f"The lighthouse inspection code word is {SECOND}.", title="Keys")))
    chat = ada.new_chat()  # Documents off
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    choose_search(ada, chat, file_id)
    wait_searchable(ada, chat, file_id)
    answer = ask(ada, chat, QUESTION, attachments=[file_id])
    assert {s["name"] for s in answer["sources"] if s.get("passage")} == {"survey.pdf"}
    assert SECOND not in last(world)[-1]["content"], "nothing of My documents went with it"
    assert answer["retrieval"]["scope"] == {"library": False, "chat": True}


def test_a_chats_own_documents_are_searched_beside_my_documents_when_the_switch_is_on(
    world: World,
) -> None:
    window(world)
    ada = start(world)
    keep(ada, ("notes.md", md(f"The kitchen code is {SECOND}.", title="Kitchen")))
    chat = ada.new_chat()
    ada.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on"}})
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    choose_search(ada, chat, file_id)
    wait_searchable(ada, chat, file_id)
    answer = ask(
        ada, chat, f"{QUESTION} And what is the kitchen code {SECOND}?", attachments=[file_id]
    )
    scopes = {s["name"]: s["scope"] for s in answer["sources"] if s.get("passage")}
    assert scopes == {"survey.pdf": "chat", "notes.md": "library"}
    assert answer["retrieval"]["scope"] == {"library": True, "chat": True}


def test_a_chats_documents_die_with_the_chat_and_leave_nothing_on_the_disk(world: World) -> None:
    window(world)
    ada = start(world)
    keep(ada, ("notes.md", md(f"The {RARE} word.")))
    chat = ada.new_chat()
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    choose_search(ada, chat, file_id)
    wait_searchable(ada, chat, file_id)
    assert {s for s, _, _ in chat_documents(world)} == {"library", f"chat:{chat}"}
    docs_dir = index_of(world).parent
    assert len(list((docs_dir / "originals").iterdir())) == 2
    assert ada.delete(f"/api/chats/{chat}").status_code == 204
    assert chat_documents(world) == [("library", "notes.md", "ready")], "only My documents is left"
    assert len(list((docs_dir / "originals").iterdir())) == 1
    assert len(list(docs_dir.glob("*.txt"))) == 1
    with raw(world) as db:
        assert db.execute("SELECT COUNT(*) FROM chunks WHERE text LIKE '%RAVEN%'").fetchone() == (
            0,
        )
        found = db.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'lighthouse'")
        assert found.fetchall() == []
    assert ada.get(f"/api/chats/{chat}").status_code == 404
    assert [d["name"] for d in listing(ada)["documents"]] == ["notes.md"], (
        "My documents is untouched"
    )


def test_taking_back_the_choice_or_choosing_another_way_removes_the_document_made_from_the_file(
    world: World,
) -> None:
    window(world)
    ada = start(world)
    chat = ada.new_chat()
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    choose_search(ada, chat, file_id)
    wait_searchable(ada, chat, file_id)
    assert len(chat_documents(world)) == 1
    cleared = ada.put(f"/api/chats/{chat}/files/{file_id}/reading", json={"mode": None})
    assert cleared.status_code == 200
    assert cleared.json()["search"] is None and cleared.json()["decision"]["kind"] == "too_long"
    assert chat_documents(world) == [], "the document went with the choice"
    assert len(list((index_of(world).parent / "originals").iterdir())) == 0
    again = choose_search(ada, chat, file_id)
    assert again.status_code == 200 and again.json()["search"]["docId"]
    wait_searchable(ada, chat, file_id)
    first = ada.put(
        f"/api/chats/{chat}/files/{file_id}/reading", json={"mode": "first", "tokens": 1500}
    )
    assert first.status_code == 200 and first.json()["search"] is None
    assert chat_documents(world) == []
    assert first.json()["decision"]["kind"] == "partial"


def test_a_chats_own_document_is_not_reachable_through_the_routes_of_my_documents(
    world: World,
) -> None:
    window(world)
    ada = start(world)
    chat = ada.new_chat()
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    doc_id = choose_search(ada, chat, file_id).json()["search"]["docId"]
    wait_searchable(ada, chat, file_id)
    for method, path in (
        ("get", f"/api/documents/{doc_id}"),
        ("delete", f"/api/documents/{doc_id}"),
        ("post", f"/api/documents/{doc_id}/reread"),
        ("get", f"/api/documents/{doc_id}/text"),
        ("get", f"/api/documents/{doc_id}/original"),
    ):
        assert getattr(ada, method)(path).status_code == 404, path
    assert listing(ada)["documents"] == []
    # What its sources open is a passage, and that is read by id from the chat's own document.
    answer = ask(ada, chat, QUESTION, attachments=[file_id])
    source = next(s for s in answer["sources"] if s.get("passage"))
    got = ada.get(f"/api/documents/{doc_id}/passages", params={"ids": str(source["chunk_ids"][0])})
    assert got.status_code == 200 and CODE in got.json()["passages"][0]["text"]
    assert len(chat_documents(world)) == 1, "none of that touched it"


def test_choosing_search_twice_is_the_same_document_and_not_two(world: World) -> None:
    window(world)
    ada = start(world)
    chat = ada.new_chat()
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    one = choose_search(ada, chat, file_id).json()["search"]["docId"]
    two = choose_search(ada, chat, file_id).json()["search"]["docId"]
    wait_searchable(ada, chat, file_id)
    assert one == two and len(chat_documents(world)) == 1


def test_removing_a_file_that_was_not_sent_removes_the_document_made_from_it(world: World) -> None:
    window(world)
    ada = start(world)
    chat = ada.new_chat()
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    choose_search(ada, chat, file_id)
    wait_searchable(ada, chat, file_id)
    assert ada.delete(f"/api/chats/{chat}/files/{file_id}").status_code == 204
    assert chat_documents(world) == []
    assert knowledge_files(world) and not [
        p for p in knowledge_files(world) if p.parent.name == "originals"
    ]


def test_a_send_waits_for_the_file_to_be_searchable_and_does_not_fail_for_being_early(
    world: World,
) -> None:
    window(world)
    ada = start(world)
    chat = ada.new_chat()
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    choose_search(ada, chat, file_id)
    sent = send(ada, chat, QUESTION, attachments=[file_id])  # no waiting: the file may be queued
    assert sent.status_code == 201, sent.text
    answer = ada.wait_answer(chat)
    assert answer["status"] == "done" and CODE in block_of(world)[1]


def test_a_file_that_could_not_be_made_searchable_stops_the_send_and_says_why(
    world: World,
) -> None:
    window(world)
    settings(world, documentsMaxPassagesPerPerson=100)
    ada = start(world)
    many = "\n\n".join(f"# Part {i}\n\n" + ("A sentence about harbours. " * 25) for i in range(95))
    assert keep(ada, ("big.md", many.encode()))["totals"]["passages"] == 95
    chat = ada.new_chat()
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    choose_search(ada, chat, file_id)
    state = wait_searchable(ada, chat, file_id)
    assert state["status"] == "skipped"
    assert state["message"].startswith(
        "Skipped: your documents would come to more than the 100 passages"
    )
    item = next(
        f for f in ada.get(f"/api/chats/{chat}/files").json()["files"] if f["id"] == file_id
    )
    assert (item["decision"]["kind"], item["decision"]["send"]) == ("unreadable", False)
    refused = send(ada, chat, QUESTION, attachments=[file_id])
    assert refused.status_code == 409
    assert refused.json()["detail"]["message"].startswith(
        "survey.pdf: Skipped: your documents would come"
    )
    assert ada.get(f"/api/chats/{chat}").json()["messages"] == [], "nothing was kept"


def test_a_search_instead_file_is_not_held_back_from_a_model_outside_the_machines(
    world: World,
) -> None:
    window(world)
    world.gateway.more_chat_models = [chat_model("cloud", locality="external", owned_by="openai")]
    ada = start(world)
    keep(ada, ("notes.md", md(f"The library {RARE} word.")))
    chat = ada.post("/api/chats", json={"model": "cloud"}).json()["id"]
    ada.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on"}})
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    choose_search(ada, chat, file_id)
    wait_searchable(ada, chat, file_id)
    answer = ask(ada, chat, f"{QUESTION} And {RARE}?", model="cloud", attachments=[file_id])
    block = block_of(world)[1]
    assert CODE in block, "the person's own act, for this chat"
    assert RARE not in block, "but My documents is held back from a model outside"
    assert answer["retrieval"]["scope"] == {"library": False, "chat": True}
    assert answer["retrieval"]["notes"][0]["code"] == "outside_model"
    assert {s["scope"] for s in answer["sources"] if s.get("passage")} == {"chat"}


def test_choosing_search_is_refused_while_the_owner_has_documents_off_and_for_a_file_not_read(
    world: World,
) -> None:
    window(world)
    ada = start(world)
    chat = ada.new_chat()
    file_id = attach(ada, chat, "survey.pdf", long_survey())
    settings(world, documentsEnabled=False)
    off = choose_search(ada, chat, file_id)
    assert off.status_code == 403
    assert (
        off.json()["detail"]["message"] == "The owner of this Workbench has turned off documents."
    )
    settings(world, documentsEnabled=None)
    picture = ada.post(
        f"/api/chats/{chat}/files", files={"file": ("dot.png", PNG, "image/png")}
    ).json()["id"]
    refused = choose_search(ada, chat, picture)
    assert (
        refused.status_code == 409
        and "does not read this file" in refused.json()["detail"]["message"]
    )
    scan = ada.post(
        f"/api/chats/{chat}/files",
        files={
            "file": (
                "scan.pdf",
                __import__("tests.documents", fromlist=["x"]).pdf_pictures(2),
                "application/pdf",
            )
        },
    ).json()["id"]
    ada.wait_read(chat, scan)
    assert choose_search(ada, chat, scan).status_code == 409


# --------------------------------------------------------------------------- #
# a restart
# --------------------------------------------------------------------------- #


def test_documents_survive_a_restart_and_a_chat_that_is_gone_takes_its_documents_at_boot(
    world: World,
) -> None:
    window(world)
    ada = start(world)
    keep(ada, ("notes.md", md(f"The {RARE} word.")))
    kept_chat, gone_chat = ada.new_chat(), ada.new_chat()
    for chat in (kept_chat, gone_chat):
        file_id = attach(ada, chat, "survey.pdf", long_survey())
        choose_search(ada, chat, file_id)
        wait_searchable(ada, chat, file_id)
    assert len(chat_documents(world)) == 3
    docs_dir = index_of(world).parent
    world.server.stop()
    # Behind Workbench's back: one chat is deleted from the chats' store, and a stop
    # left an original no row claims and a half-written upload.
    db = sqlite3.connect(world.data / "workbench.sqlite3")
    db.execute("DELETE FROM chats WHERE id = ?", (gone_chat,))
    db.execute("DELETE FROM files WHERE chat_id = ?", (gone_chat,))
    db.commit()
    db.close()
    (docs_dir / "originals" / "orphan-original-1").write_text("no row claims me", encoding="utf-8")
    (docs_dir / "originals" / "halfwayupload.part").write_text("half an upload", encoding="utf-8")
    (docs_dir / "orphan-original-1.txt").write_text("no row claims my text", encoding="utf-8")
    world.restart_workbench()
    ada = start(world)
    assert [d["name"] for d in listing(ada)["documents"]] == ["notes.md"]
    scopes = sorted(s for s, _, _ in chat_documents(world))
    assert scopes == sorted(["library", f"chat:{kept_chat}"]), (
        "the gone chat's document went at boot"
    )
    names = {p.name for p in (docs_dir / "originals").iterdir()}
    assert "orphan-original-1" not in names and "halfwayupload.part" not in names
    assert not (docs_dir / "orphan-original-1.txt").exists()
    chat = ada.post("/api/chats", json={"model": MODEL}).json()["id"]
    ada.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on"}})
    ask(ada, chat, f"What is {RARE}?")
    assert RARE in block_of(world)[1], "and it still answers from what was kept"


def test_a_stop_in_the_middle_of_reading_resumes_at_boot_and_every_file_is_read_once(
    world: World,
) -> None:
    ada = start(world)
    many = [(f"f{n:02d}.md", md(f"File {n} says {RARE}.", title=f"File {n}")) for n in range(14)]
    assert add(ada, *many).status_code == 201
    assert wait_for(lambda: any(d["state"] == "ready" for d in listing(ada)["documents"]), 60)
    mid = listing(ada)["documents"]
    assert any(d["state"] != "ready" for d in mid), "stopped in the middle of reading"
    world.restart_workbench()
    ada = start(world)
    done = settle(ada)
    assert sorted(d["name"] for d in done["documents"]) == [n for n, _ in many]
    assert {d["state"] for d in done["documents"]} == {"ready"}
    assert done["totals"]["passages"] == 14, "each file once"
    leftovers = [p.name for p in knowledge_files(world) if p.name.endswith(".part")]
    assert leftovers == []
    chat = ada.post("/api/chats", json={"model": MODEL}).json()["id"]
    ada.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on"}})
    ask(ada, chat, f"What is {RARE}?")
    assert has_block(world)


# --------------------------------------------------------------------------- #
# whose documents are whose
# --------------------------------------------------------------------------- #


def test_another_person_gets_the_same_404_on_every_route_for_a_document_that_is_not_theirs(
    world: World,
) -> None:
    ada, bo = start(world, "p-ada"), start(world, "p-bo")
    doc = keep(ada, ("secret.md", md(f"Ada's {RARE} word.")))["documents"][0]
    keep(bo, ("mine.md", md(f"Bo's {SECOND} word.")))
    mine = listing(bo)["documents"][0]
    missing = "doesnotexist1"
    routes = [
        ("get", "/api/documents/{id}", None),
        ("delete", "/api/documents/{id}", None),
        ("post", "/api/documents/{id}/reread", None),
        ("get", "/api/documents/{id}/text", None),
        ("get", "/api/documents/{id}/original", None),
        ("get", "/api/documents/{id}/passages?ids=1,2", None),
    ]
    for method, path, body in routes:
        theirs = getattr(bo, method)(path.format(id=doc["id"]), **({"json": body} if body else {}))
        nothing = getattr(bo, method)(path.format(id=missing), **({"json": body} if body else {}))
        assert theirs.status_code == nothing.status_code == 404, (method, path)
        assert (
            theirs.json() == nothing.json() == {"detail": {"message": "There is no such document."}}
        )
    assert [d["name"] for d in listing(bo)["documents"]] == ["mine.md"]
    assert ada.get(f"/api/documents/{doc['id']}").status_code == 200, "and hers is untouched"
    assert ada.get(f"/api/documents/{mine['id']}").status_code == 404
    shared = bo.post("/api/documents/test", json={"question": f"{RARE} {SECOND}"}).json()
    assert [p["name"] for p in shared["passages"]] == ["mine.md"], "ranked among his own only"
    assert all(RARE not in p["text"] + p["snippet"] for p in shared["passages"])


def test_another_persons_chat_gets_the_same_404_for_the_documents_routes_that_take_a_chat(
    world: World,
) -> None:
    ada, bo = start(world, "p-ada"), start(world, "p-bo")
    keep(ada, ("secret.md", md(f"Ada's {RARE} word.")))
    chat = chat_id = ada.new_chat()
    gone = "no-such-chat-1"
    for path in ("/api/chats/{id}/documents",):
        theirs = bo.get(path.format(id=chat_id))
        nothing = bo.get(path.format(id=gone))
        assert theirs.status_code == nothing.status_code == 404
        assert theirs.json() == nothing.json()
    test = bo.post("/api/documents/test", json={"question": RARE, "chatId": chat})
    nothing = bo.post("/api/documents/test", json={"question": RARE, "chatId": gone})
    assert test.status_code == nothing.status_code == 404 and test.json() == nothing.json()
    patch = bo.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on"}})
    assert patch.status_code == 404
    assert chat == chat_id


def test_a_chat_with_documents_on_is_sent_only_its_own_persons_documents(world: World) -> None:
    ada, bo = start(world, "p-ada"), start(world, "p-bo")
    keep(ada, ("ada.md", md(f"Ada's {RARE} word.", title="Ada's")))
    keep(bo, ("bo.md", md(f"Bo's {RARE} word.", title="Bo's")))
    ada_chat = ada.post("/api/chats", json={"model": MODEL}).json()["id"]
    bo_chat = bo.post("/api/chats", json={"model": MODEL}).json()["id"]
    for person, chat in ((ada, ada_chat), (bo, bo_chat)):
        person.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on"}})
    ask(ada, ada_chat, f"What is {RARE}?")
    assert "Ada's" in block_of(world)[1] and "Bo's" not in block_of(world)[1]
    ask(bo, bo_chat, f"What is {RARE}?")
    assert "Bo's" in block_of(world)[1] and "Ada's" not in block_of(world)[1]
    third = start(world, "operator")
    chat = third.post("/api/chats", json={"model": MODEL}).json()["id"]
    third.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on"}})
    ask(third, chat, f"What is {RARE}?")
    assert not has_block(world), "the owner has no documents of their own and is sent none"


def test_each_person_has_a_directory_of_their_own_and_removing_one_leaves_the_other(
    world: World,
) -> None:
    ada, bo = start(world, "p-ada"), start(world, "p-bo")
    keep(ada, ("a.md", md(f"{RARE}.")))
    keep(bo, ("b.md", md(f"{RARE}.")))
    assert index_of(world, "p-ada") != index_of(world, "p-bo")
    assert index_of(world, "p-ada").parent.name == files.person_key("p-ada")
    for person in ("p-ada", "p-bo"):
        assert (index_of(world, person).parent / "originals").is_dir()
    ada_doc = listing(ada)["documents"][0]
    assert ada.delete(f"/api/documents/{ada_doc['id']}").status_code == 204
    assert [d["name"] for d in listing(bo)["documents"]] == ["b.md"]
    assert chat_documents(world, "p-bo") == [("library", "b.md", "ready")]


def test_the_owner_cannot_read_a_persons_documents_even_where_they_may_read_her_chats(
    world: World,
) -> None:
    window(world)
    ada, owner = start(world, "p-ada"), start(world, "operator")
    doc = keep(ada, ("secret.pdf", survey_pdf(CODE)))["documents"][0]
    chat = ada.post("/api/chats", json={"model": MODEL}).json()["id"]
    ada.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on"}})
    memo = attach(ada, chat, "memo.md", md("A short memo."), "text/markdown")
    answer = ask(ada, chat, QUESTION, attachments=[memo])
    assert any(s.get("passage") and s["name"] == "secret.pdf" for s in answer["sources"])
    assert owner.get(f"/api/chats/{chat}").status_code == 404, "nothing yet: the switch is off"
    settings(world, ownerReadsChats=True)
    try:
        for path in ("", "/text", "/original", "/passages?ids=1"):
            assert owner.get(f"/api/documents/{doc['id']}{path}").status_code == 404, path
        assert owner.delete(f"/api/documents/{doc['id']}").status_code == 404
        assert listing(owner)["documents"] == []
        assert (
            owner.post("/api/documents/test", json={"question": QUESTION}).json()["status"]
            == "no_documents"
        )
        assert listing(owner)["ownerReadsChats"] is True, "so Documents can say it, in one line"
        seen = owner.get(f"/api/chats/{chat}")
        assert seen.status_code == 200 and seen.json()["chat"]["readOnly"] is True
        last_message = seen.json()["messages"][-1]
        hidden = [s for s in last_message["sources"] if s.get("passage")]
        assert hidden and all(s["name"] == "a document" and s["redacted"] is True for s in hidden)
        for s in hidden:
            assert not (
                {"snippet", "heading", "page_start", "doc_id", "chunk_ids", "terms"} & set(s)
            )
        assert "secret.pdf" not in seen.text and CODE not in seen.text
        assert (
            last_message["retrieval"]["searched"] is None
            and last_message["retrieval"]["cited"] == []
        )
        attached = [s for s in last_message["sources"] if not s.get("passage")]
        assert [s["name"] for s in attached] == ["memo.md"], (
            "what she attached to the chat is in the chat"
        )
        state = owner.get(f"/api/chats/{chat}/documents")
        assert state.status_code == 200 and state.json() == {
            "chatId": chat,
            "readOnly": True,
            "switch": "on",
            "documentsOnly": False,
        }
    finally:
        settings(world, ownerReadsChats=None)
    assert owner.get(f"/api/chats/{chat}").status_code == 404


def test_every_route_needs_a_session_and_the_changing_ones_the_pages_own_origin(
    world: World,
) -> None:
    ada = start(world)
    doc = keep(ada, ("a.md", md("Words.")))["documents"][0]
    chat = ada.new_chat()
    routes = [
        ("get", "/api/documents"),
        ("post", "/api/documents"),
        ("get", "/api/documents/prefs"),
        ("put", "/api/documents/prefs"),
        ("post", "/api/documents/test"),
        ("get", f"/api/documents/{doc['id']}"),
        ("delete", f"/api/documents/{doc['id']}"),
        ("post", f"/api/documents/{doc['id']}/reread"),
        ("get", f"/api/documents/{doc['id']}/text"),
        ("get", f"/api/documents/{doc['id']}/original"),
        ("get", f"/api/documents/{doc['id']}/passages?ids=1"),
        ("get", f"/api/chats/{chat}/documents"),
    ]
    stranger = httpx.Client(base_url=world.workbench, timeout=20, trust_env=False)

    def body(method: str, path: str) -> dict[str, Any]:
        """A valid request body, so what is refused is the caller and not the shape."""
        if path == "/api/documents" and method == "post":
            return {"files": [("files", ("x.md", b"# x\n", "text/markdown"))]}
        if path == "/api/documents/test":
            return {"json": {"question": "words"}}
        if path == "/api/documents/prefs" and method == "put":
            return {"json": {}}
        return {}

    for method, path in routes:
        got = stranger.request(
            method.upper(), path, headers={"Origin": world.workbench}, **body(method, path)
        )
        assert got.status_code == 401, (method, path, got.status_code)
        assert got.json()["detail"]["signedIn"] is False
    for method, path in routes:
        if method == "get":
            continue
        bare = ada.http.request(
            method.upper(),
            path,
            headers={"X-Workbench-Secret": ada.secret or ""},
            **body(method, path),
        )
        assert bare.status_code == 403, (method, path)
        assert (
            bare.json()["detail"]["message"]
            == "This request did not come from Workbench's own page."
        )
    assert listing(ada)["documents"][0]["id"] == doc["id"], "and nothing changed"


def test_the_time_to_say_something_is_never_a_guess_at_a_time_the_list_is_quick(
    world: World,
) -> None:
    ada = start(world)
    keep(ada, *[(f"f{n}.md", md(f"File {n}.")) for n in range(12)])
    started = time.perf_counter()
    assert len(listing(ada)["documents"]) == 12
    assert time.perf_counter() - started < 2
