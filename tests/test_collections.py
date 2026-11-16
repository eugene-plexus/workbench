"""Row 47: team knowledge (`specs/docs/design/rows/row-47.md`).

A real Workbench against the fake Eugene and gateway, as each person's browser calls it. The
owner makes a collection, gives it to a group, and adds a document; a chat of someone in the
group searches it; nobody else finds it, or learns its name; a person taken out of the group
loses it at their next refresh; and a note a model proposes waits for a contributor.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .conftest import World
from .meaning import quick, ready_world  # noqa: F401
from .saved import RARE, ask, block_of, has_block, md

G1 = {"id": "g-one", "name": "Group one"}
G2 = {"id": "g-two", "name": "Group two"}
DOC = md(f"New starters get a desk on day one. The front door code word is {RARE}.", title="Rules")
NO_SUCH = {"detail": {"message": "There is no such collection."}}


def member_of(world: World, sub: str, *groups: dict[str, str]) -> None:
    world.eugene.claims[sub] = {"eugene_groups": list(groups), "eugene_policies": {}}


def people(world: World) -> dict[str, Any]:
    world.eugene.groups = [G1, G2]
    world.eugene.people["p-cy"] = {"name": "Cy", "role": "member", "username": "cy"}
    member_of(world, "p-ada", G1)
    member_of(world, "p-bo", G1)
    member_of(world, "p-cy", G2)
    signed = {}
    for sub in ("operator", "p-ada", "p-bo", "p-cy"):
        browser = world.browser()
        browser.sign_in(sub)
        assert browser.secret, sub
        signed[sub] = browser
    return signed


def settled(person: Any, collection_id: str, seconds: float = 60) -> dict[str, Any]:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        seen = person.get(f"/api/collections/{collection_id}").json()
        if seen["documentList"] and all(
            d["document"]["state"] in ("ready", "searchable", "skipped", "failed")
            for d in seen["documentList"]
        ):
            return dict(seen)
        time.sleep(0.05)
    raise AssertionError("the collection's documents were not read in time")


def handbook(world: World, owner: Any, grants: list[dict[str, str]]) -> str:
    made = owner.post("/api/collections", json={"name": "Handbook"})
    assert made.status_code == 201, made.text
    hid = str(made.json()["id"])
    added = owner.post(
        f"/api/collections/{hid}/documents", files=[("files", ("rules.md", DOC, "text/markdown"))]
    )
    assert added.status_code == 201, added.text
    granted = owner.put(f"/api/collections/{hid}/grants", json={"grants": grants})
    assert granted.status_code == 200, granted.text
    seen = settled(owner, hid)
    assert seen["documentList"][0]["addedBy"] == "Owner"
    return hid


def chat_with(person: Any, collection_id: str | None = None, **body: Any) -> str:
    chat = person.post("/api/chats", json={"model": "local-model", **body})
    assert chat.status_code == 201, chat.text
    chat_id = str(chat.json()["id"])
    if collection_id is not None:
        linked = person.put(f"/api/chats/{chat_id}/collections", json={"ids": [collection_id]})
        assert linked.status_code == 200, linked.text
    return chat_id


def test_a_read_grant_lets_a_chat_search_the_collection(world: World) -> None:
    signed = people(world)
    hid = handbook(world, signed["operator"], [{"kind": "group", "id": G1["id"], "role": "read"}])
    ada = signed["p-ada"]
    listed = ada.get("/api/collections").json()["collections"]
    assert [(c["name"], c["role"]) for c in listed] == [("Handbook", "read")]
    chat = chat_with(ada, hid)
    answer = ask(ada, chat, f"What is the code word {RARE}?")
    _, block, passages = block_of(world)
    assert RARE in block
    assert passages[0][2] == "Handbook: rules.md"
    team = [s for s in answer["sources"] if s.get("scope") == "collection"]
    assert team and team[0]["collection"] == {"id": hid, "name": "Handbook"}
    # A reader cannot add to it, or see what waits in it.
    refused = ada.post(f"/api/collections/{hid}/notes", json={"note": "Lunch is at noon."})
    assert refused.status_code == 403
    assert ada.get(f"/api/collections/{hid}").json()["inbox"] == []


def test_no_grant_finds_nothing_and_says_nothing(world: World) -> None:
    signed = people(world)
    hid = handbook(world, signed["operator"], [{"kind": "group", "id": G1["id"], "role": "read"}])
    cy = signed["p-cy"]
    assert cy.get("/api/collections").json()["collections"] == []
    # The same 404, word for word, as a collection that is not there.
    assert cy.get(f"/api/collections/{hid}").json() == NO_SUCH
    assert cy.get("/api/collections/not-there").json() == NO_SUCH
    chat = chat_with(cy)
    tried = cy.put(f"/api/chats/{chat}/collections", json={"ids": [hid]})
    assert tried.status_code == 404 and tried.json() == NO_SUCH
    # A choice kept from before (or made in a project shared with them) is left out at search.
    db = sqlite3.connect(world.data / "workbench.sqlite3")
    with db:
        db.execute(
            "INSERT INTO collection_links (owner, target, collection_id) VALUES (?, ?, ?)",
            ("p-cy", f"chat:{chat}", hid),
        )
    db.close()
    answer = ask(cy, chat, f"What is the code word {RARE}?")
    assert not has_block(world)
    told = json.dumps(answer) + json.dumps(cy.get(f"/api/chats/{chat}/collections").json())
    assert "Handbook" not in told, told
    assert "front door" not in json.dumps(world.gateway.requests[-1])
    assert answer["retrieval"] is None
    # The MSP case: the owner's project uses the Handbook and is shared with Cy, not in G1.
    ada = signed["operator"]
    project = ada.post("/api/projects", json={"name": "Client"}).json()["id"]
    assert ada.put(f"/api/projects/{project}/collections", json={"ids": [hid]}).status_code == 200
    shared = ada.put(
        f"/api/projects/{project}/sharing",
        json={"shares": [{"kind": "person", "id": "p-cy", "role": "read"}]},
    )
    assert shared.status_code == 200, shared.text
    in_project = chat_with(cy, projectId=project)
    answer = ask(cy, in_project, f"What is the code word {RARE}?")
    assert not has_block(world) and "Handbook" not in json.dumps(answer)
    assert "front door" not in json.dumps(world.gateway.requests[-1])


def test_a_group_grant_follows_membership_after_refresh(world: World) -> None:
    world.eugene.expires_in = 1  # every call is due for a refresh
    signed = people(world)
    hid = handbook(world, signed["operator"], [{"kind": "group", "id": G1["id"], "role": "read"}])
    bo = signed["p-bo"]
    chat = chat_with(bo, hid)
    ask(bo, chat, f"What is the code word {RARE}?")
    assert has_block(world)
    member_of(world, "p-bo", G2)  # taken out of G1 at the root
    time.sleep(1.1)
    assert bo.get("/api/groups").json()["groups"] == [G2]  # the refresh read it
    answer = ask(bo, chat, f"What is the code word {RARE}?")
    assert not has_block(world) and "Handbook" not in json.dumps(answer)
    assert bo.get(f"/api/collections/{hid}").status_code == 404


def waiting_call(person: Any, chat: str) -> dict[str, Any]:
    """The answer once it waits on the person's decision about a tool call."""
    deadline = time.perf_counter() + 20
    last: dict[str, Any] = {}
    while time.perf_counter() < deadline:
        last = person.get(f"/api/chats/{chat}").json()["messages"][-1]
        if last["toolRounds"]:
            return last
        time.sleep(0.03)
    raise AssertionError(last)


def test_a_proposal_the_person_declines_goes_nowhere(world: World) -> None:
    signed = people(world)
    hid = handbook(
        world, signed["operator"], [{"kind": "group", "id": G1["id"], "role": "contribute"}]
    )
    bo = signed["p-bo"]
    chat = chat_with(bo, hid)
    world.gateway.mode = "tools-script"
    world.gateway.tool_script = [
        ("suggest_team_note", json.dumps({"collection": "Handbook", "note": "Bo's salary is X."}))
    ]
    bo.post(f"/api/chats/{chat}/messages", json={"content": "Summarise the handbook."})
    asking = waiting_call(bo, chat)
    call = asking["toolRounds"][0]["calls"][0]
    bo.post(
        f"/api/chats/{chat}/messages/{asking['id']}/tools/decision",
        json={"callId": call["id"], "approve": False},
    )
    bo.wait_answer(chat)
    assert signed["operator"].get(f"/api/collections/{hid}").json()["inbox"] == []


def test_a_model_proposed_note_waits_for_a_contributor(world: World) -> None:
    signed = people(world)
    hid = handbook(
        world, signed["operator"], [{"kind": "group", "id": G1["id"], "role": "contribute"}]
    )
    ada, bo = signed["p-ada"], signed["p-bo"]
    chat = chat_with(bo, hid)
    world.gateway.mode = "tools-script"
    world.gateway.tool_script = [
        (
            "suggest_team_note",
            json.dumps({"collection": "Handbook", "note": "Fire drills are on Fridays quillwort."}),
        )
    ]
    sent = bo.post(
        f"/api/chats/{chat}/messages",
        json={"content": "Remember for the team that fire drills are on Fridays."},
    )
    assert sent.status_code == 201, sent.text
    # Bo is asked first, with the words, before anything leaves the chat (review B3).
    asking = waiting_call(bo, chat)
    offered = [t for r in world.gateway.requests for t in r.get("tools") or []]
    assert any(t["function"]["name"] == "suggest_team_note" for t in offered)
    call = asking["toolRounds"][0]["calls"][0]
    assert call.get("ask") is True and call["status"] != "done"
    assert "Fire drills" in json.dumps(call["arguments"])
    assert ada.get(f"/api/collections/{hid}").json()["inbox"] == []
    decided = bo.post(
        f"/api/chats/{chat}/messages/{asking['id']}/tools/decision",
        json={"callId": call["id"], "approve": True},
    )
    assert decided.status_code == 204, decided.text
    answer = bo.wait_answer(chat)
    calls = [c for r in answer["toolRounds"] for c in r["calls"]]
    assert calls and calls[0]["status"] == "done", calls
    waiting = ada.get(f"/api/collections/{hid}").json()
    assert [i["title"] for i in waiting["inbox"]] == ["Fire drills are on Fridays quillwort."]
    assert waiting["noteList"] == []
    # Not searched while it waits.
    world.gateway.mode = "answer"
    ask(bo, chat_with(bo, hid), "When are the quillwort drills?")
    assert not has_block(world)
    # A contributor accepts it, and from then it is found, as theirs.
    item = waiting["inbox"][0]["id"]
    accepted = ada.post(f"/api/collections/{hid}/inbox/{item}/accept")
    assert accepted.status_code == 200 and accepted.json()["addedBy"] == "Ada"
    ask(bo, chat_with(bo, hid), "When are the quillwort drills?")
    assert has_block(world) and "Fridays quillwort" in block_of(world)[1]
    # A reader is never offered the tool.
    reader = signed["operator"].put(
        f"/api/collections/{hid}/grants",
        json={"grants": [{"kind": "group", "id": G1["id"], "role": "read"}]},
    )
    assert reader.status_code == 200
    world.gateway.requests.clear()
    ask(bo, chat_with(bo, hid), "Hello")
    assert not any(
        t["function"]["name"] == "suggest_team_note"
        for t in world.gateway.requests[-1].get("tools") or []
    )


def test_a_contributor_cannot_replace_someone_elses_document(world: World) -> None:
    """Review B1: a file of the same name replaces only the uploader's own, or for its manager."""
    signed = people(world)
    owner, ada = signed["operator"], signed["p-ada"]
    hid = handbook(world, owner, [{"kind": "person", "id": "p-ada", "role": "contribute"}])
    first = owner.get(f"/api/collections/{hid}").json()["documentList"][0]
    put = ada.post(
        f"/api/collections/{hid}/documents",
        files=[("files", ("rules.md", md("Ada's own rules."), "text/markdown"))],
    )
    assert put.status_code == 409, put.text
    assert "rules.md" in put.json()["detail"]["message"]
    assert "Owner added" in put.json()["detail"]["message"]
    kept = owner.get(f"/api/collections/{hid}").json()["documentList"]
    assert [(d["id"], d["addedBy"]) for d in kept] == [(first["id"], "Owner")]
    # Her own file of another name is kept, and she may replace that one herself.
    for words in ("Ada's notes, first.", "Ada's notes, second."):
        mine = ada.post(
            f"/api/collections/{hid}/documents",
            files=[("files", ("ada.md", md(words), "text/markdown"))],
        )
        assert mine.status_code == 201, mine.text
    titles = sorted(d["title"] for d in settled(owner, hid)["documentList"])
    assert titles == ["ada.md", "rules.md"]
    # Its manager replaces anyone's.
    again = owner.post(
        f"/api/collections/{hid}/documents",
        files=[("files", ("ada.md", md("The owner's version."), "text/markdown"))],
    )
    assert again.status_code == 201, again.text
    held = {d["title"]: d["addedBy"] for d in settled(owner, hid)["documentList"]}
    assert held == {"ada.md": "Owner", "rules.md": "Owner"}


def test_a_shared_projects_collection_takes_no_notes(world: World) -> None:
    """Review S7: a project's collection is its documents; notes for it are kept in Memory."""
    signed = people(world)
    ada = signed["p-ada"]
    project = ada.post("/api/projects", json={"name": "Harbour"}).json()["id"]
    shared = ada.put(
        f"/api/projects/{project}/sharing",
        json={"shares": [{"kind": "person", "id": "p-bo", "role": "contribute"}]},
    )
    cid = shared.json()["collectionId"]
    refused = signed["p-bo"].post(f"/api/collections/{cid}/notes", json={"note": "Tides at six."})
    assert refused.status_code == 409, refused.text
    assert ada.get(f"/api/collections/{cid}").json()["noteList"] == []


def test_a_cloud_model_gets_a_collection_only_when_its_owner_allows(world: World) -> None:
    """Default (a): a team's passages go to a model outside the machines only when the
    collection's manager says they may; the chip and the settings say which is sent, and why
    not (review S3)."""
    signed = people(world)
    owner, ada = signed["operator"], signed["p-ada"]
    hid = handbook(world, owner, [{"kind": "group", "id": G1["id"], "role": "read"}])
    world.gateway.locality = "external"
    chat = chat_with(ada, hid)
    assert (
        ada.patch(f"/api/chats/{chat}", json={"settings": {"documentsExternal": True}}).status_code
        == 200
    )
    ask(ada, chat, f"What is the code word {RARE}?")
    assert not has_block(world)
    chip = ada.get(f"/api/chats/{chat}/documents").json()
    assert chip["searching"]["team"] is False and chip["team"]["sent"] == []
    assert "keeps it to them" in chip["team"]["held"][0]["why"]
    settings = ada.get(f"/api/chats/{chat}/collections").json()
    assert settings["searched"] == [] and settings["held"][0]["name"] == "Handbook"
    # Only its manager says so.
    assert ada.patch(f"/api/collections/{hid}", json={"outsideModels": True}).status_code == 403
    allowed = owner.patch(f"/api/collections/{hid}", json={"outsideModels": True})
    assert allowed.status_code == 200 and allowed.json()["outsideModels"] is True
    ask(ada, chat, f"What is the code word {RARE}?")
    assert has_block(world) and "front door" in block_of(world)[1]
    assert ada.get(f"/api/chats/{chat}/collections").json()["held"] == []


def test_the_chip_says_a_team_collection_is_searched_with_documents_off(world: World) -> None:
    """Review S3: with the chat's own Documents off, a chosen collection is still sent, and the
    chip says so rather than "off"."""
    signed = people(world)
    hid = handbook(world, signed["operator"], [{"kind": "group", "id": G1["id"], "role": "read"}])
    ada = signed["p-ada"]
    chat = chat_with(ada, hid)
    ask(ada, chat, f"What is the code word {RARE}?")
    assert has_block(world)
    chip = ada.get(f"/api/chats/{chat}/documents").json()
    assert chip["switch"] == "off" and chip["reasonCode"] != "chat_off"
    assert chip["sending"] is True and chip["searching"]["team"] is True
    assert [c["name"] for c in chip["team"]["sent"]] == ["Handbook"]


def test_only_people_in_a_group_with_you_are_listed(world: World) -> None:
    """Review S4: the owner sees everyone who signed in; anyone else, only the people in a group
    with them, and shares only with those."""
    signed = people(world)
    everyone = signed["operator"].get("/api/team/people").json()["people"]
    assert {p["sub"] for p in everyone} == {"p-ada", "p-bo", "p-cy"}
    ada = signed["p-ada"]
    assert [p["sub"] for p in ada.get("/api/team/people").json()["people"]] == ["p-bo"]
    project = ada.post("/api/projects", json={"name": "Harbour"}).json()["id"]
    refused = ada.put(
        f"/api/projects/{project}/sharing",
        json={"shares": [{"kind": "person", "id": "p-cy", "role": "read"}]},
    )
    assert refused.status_code == 422, refused.text


def test_a_collection_gets_search_by_meaning(world: World, quick: None) -> None:  # noqa: F811
    """Review S2: a collection's passages are given meaning, with its owner's sign-in."""
    ready_world(world)
    owner = world.browser()
    owner.sign_in("operator")
    hid = owner.post("/api/collections", json={"name": "Handbook"}).json()["id"]
    added = owner.post(
        f"/api/collections/{hid}/documents",
        files=[("files", ("rules.md", md(f"Team words {RARE}."), "text/markdown"))],
    )
    assert added.status_code == 201, added.text
    deadline = time.perf_counter() + 30
    state = None
    while time.perf_counter() < deadline:
        listed = owner.get(f"/api/collections/{hid}").json()["documentList"]
        state = listed[0]["document"]["state"] if listed else None
        if state == "ready":
            break
        time.sleep(0.1)
    assert state == "ready"
    assert any(f"Team words {RARE}" in json.dumps(r) for r in world.gateway.embed_requests), (
        "the collection's passage was never sent to the search model"
    )
