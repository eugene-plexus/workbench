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
    # The MSP case: Ada's project uses the Handbook and is shared with Cy, who is not in G1.
    ada = signed["p-ada"]
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
    answer = ask(bo, chat, "Remember for the team that fire drills are on Fridays.")
    offered = [t for r in world.gateway.requests for t in r.get("tools") or []]
    assert any(t["function"]["name"] == "suggest_team_note" for t in offered)
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
