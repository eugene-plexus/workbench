"""Row 47: sharing a project (`specs/docs/design/rows/row-47.md`).

Ada shares her project with Bo. Bo sees its instructions and documents and starts his own chat
in it; nobody sees anyone else's chats; Ada sees how many Bo has, never what is in them. When
Ada stops sharing it, Bo's next answer has nothing of the project, and his chat stays his.
"""

from __future__ import annotations

import json
from typing import Any

from .conftest import World
from .saved import RARE, ask, block_of, chat_on, has_block, md, settle
from .test_collections import settled

INSTRUCTION = "Always answer in the voice of a lighthouse keeper."


def shared_project(world: World) -> tuple[Any, Any, str, str]:
    ada, bo = world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    project = ada.post("/api/projects", json={"name": "Harbour"}).json()["id"]
    kept = ada.post(
        "/api/memory/notes",
        json={"text": INSTRUCTION, "kind": "instruction", "projectId": project},
    )
    assert kept.status_code in (200, 201), kept.text
    added = ada.post(
        "/api/documents",
        data={"projectId": project},
        files=[("files", ("survey.md", md(f"The tide gauge code is {RARE}."), "text/markdown"))],
    )
    assert added.status_code == 201, added.text
    settle(ada)
    own_chat = chat_on(ada)
    moved = ada.patch(f"/api/chats/{own_chat}", json={"projectId": project})
    assert moved.status_code == 200, moved.text
    ask(ada, own_chat, "A private question about my garden.")
    shared = ada.put(
        f"/api/projects/{project}/sharing",
        json={"shares": [{"kind": "person", "id": "p-bo", "role": "read"}]},
    )
    assert shared.status_code == 200, shared.text
    collection = shared.json()["collectionId"]
    moved_in = settled(ada, collection)["documentList"]
    assert [(d["title"], d["addedBy"]) for d in moved_in] == [("survey.md", "Ada")]
    # Moved, not copied: the project's own folder of documents is empty now.
    assert ada.get(f"/api/documents?projectId={project}").json()["documents"] == []
    return ada, bo, project, own_chat


def system_text(world: World) -> str:
    first = world.gateway.requests[-1]["messages"][0]
    return str(first["content"]) if first["role"] == "system" else ""


def test_a_shared_project_shares_knowledge_not_chats(world: World) -> None:
    ada, bo, project, ada_chat = shared_project(world)
    listed = bo.get("/api/projects").json()
    assert listed["projects"] == []
    assert [(p["name"], p["owner"], p["role"]) for p in listed["shared"]] == [
        ("Harbour", "Ada", "read")
    ]
    page = bo.get(f"/api/projects/{project}").json()
    assert [n["text"] for n in page["instructions"]] == [INSTRUCTION]
    assert page["documents"] == 1 and page["chatList"] == []
    # Bo starts his own chat in it: it is given the instruction and searches its documents.
    chat = bo.post("/api/chats", json={"model": "local-model", "projectId": project})
    assert chat.status_code == 201, chat.text
    bo_chat = chat.json()["id"]
    assert (
        bo.patch(f"/api/chats/{bo_chat}", json={"settings": {"documents": "on"}}).status_code == 200
    )
    ask(bo, bo_chat, f"What is the tide gauge code {RARE}?")
    assert INSTRUCTION in system_text(world)
    _, block, passages = block_of(world)
    assert RARE in block and passages[0][2] == "Harbour: survey.md"
    # Chats are never shared: not Ada's to Bo, nor Bo's to Ada; Ada sees a count.
    assert bo.get(f"/api/chats/{ada_chat}").status_code == 404
    assert ada.get(f"/api/chats/{bo_chat}").status_code == 404
    mine = ada.get(f"/api/projects/{project}").json()
    assert [c["id"] for c in mine["chatList"]] == [ada_chat]
    assert mine["sharing"]["othersChats"] == 1
    assert "tide gauge" not in json.dumps(mine)
    # Bo reads; he cannot share it, add to it, or change it.
    assert bo.put(f"/api/projects/{project}/sharing", json={"shares": []}).status_code == 404
    assert bo.patch(f"/api/projects/{project}", json={"name": "Mine"}).status_code == 404
    collection = mine["collectionId"]
    refused = bo.post(
        f"/api/collections/{collection}/documents",
        files=[("files", ("x.md", md("Bo's words."), "text/markdown"))],
    )
    assert refused.status_code == 403
    # Ada's own chat in it still searches its documents.
    ask(ada, ada_chat, f"What is the tide gauge code {RARE}?")
    assert has_block(world) and RARE in block_of(world)[1]


def test_leaving_detaches_knowledge_and_keeps_own_chats(world: World) -> None:
    ada, bo, project, _ = shared_project(world)
    bo_chat = bo.post("/api/chats", json={"model": "local-model", "projectId": project}).json()[
        "id"
    ]
    bo.patch(f"/api/chats/{bo_chat}", json={"settings": {"documents": "on"}})
    ask(bo, bo_chat, f"What is the tide gauge code {RARE}?")
    assert INSTRUCTION in system_text(world) and has_block(world)
    ended = ada.put(f"/api/projects/{project}/sharing", json={"shares": []})
    assert ended.status_code == 200 and ended.json()["shares"] == []
    # His next answer has nothing of the project: not its instruction, not its documents.
    ask(bo, bo_chat, f"What is the tide gauge code {RARE}?")
    assert INSTRUCTION not in json.dumps(world.gateway.requests[-1])
    assert not has_block(world)
    # His chat stays his, with what was said in it.
    kept = bo.get(f"/api/chats/{bo_chat}")
    assert kept.status_code == 200 and len(kept.json()["messages"]) == 4
    assert bo.get(f"/api/projects/{project}").status_code == 404
    assert bo.get("/api/projects").json()["shared"] == []
    # And no new chat goes in it.
    assert (
        bo.post("/api/chats", json={"model": "local-model", "projectId": project}).status_code
        == 404
    )
