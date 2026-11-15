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
TEAM = {"id": "g-team", "name": "Team"}


def together(world: World) -> None:
    """Ada and Bo in one group: a person shares only with people in a group with them."""
    for sub in ("p-ada", "p-bo"):
        world.eugene.claims[sub] = {"eugene_groups": [TEAM], "eugene_policies": {}}


def shared_project(world: World) -> tuple[Any, Any, str, str]:
    together(world)
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


def test_an_owners_deleted_or_changed_instruction_stops_reaching_members(world: World) -> None:
    """Review B2: a member's chat takes its block again when the owner's note changes or goes."""
    ada, bo, project, _ = shared_project(world)
    note = next(
        n["id"] for n in ada.get("/api/memory/notes").json()["notes"] if n["text"] == INSTRUCTION
    )
    chat = bo.post("/api/chats", json={"model": "local-model", "projectId": project}).json()["id"]
    ask(bo, chat, "Hello there.")
    assert INSTRUCTION in system_text(world)
    changed = "Always answer in the voice of a harbour pilot."
    assert ada.patch(f"/api/memory/notes/{note}", json={"text": changed}).status_code == 200
    ask(bo, chat, "Hello again.")
    sent = json.dumps(world.gateway.requests[-1])
    assert changed in sent and INSTRUCTION not in sent
    current = next(
        n["id"] for n in ada.get("/api/memory/notes").json()["notes"] if n["text"] == changed
    )
    assert ada.delete(f"/api/memory/notes/{current}").status_code in (200, 204)
    ask(bo, chat, "And once more.")
    sent = json.dumps(world.gateway.requests[-1])
    assert changed not in sent and INSTRUCTION not in sent


def test_a_note_a_model_kept_unseen_is_not_shared(world: World) -> None:
    """Review S6: only what may leave the owner's machines (call 12) reaches the members."""
    import sqlite3

    _, bo, project, _ = shared_project(world)
    db = sqlite3.connect(world.data / "workbench.sqlite3")
    with db:
        db.execute(
            "UPDATE memory_notes SET auto = 1, seen_at = NULL WHERE owner = 'p-ada' AND text = ?",
            (INSTRUCTION,),
        )
    db.close()
    assert bo.get(f"/api/projects/{project}").json()["instructions"] == []
    chat = bo.post("/api/chats", json={"model": "local-model", "projectId": project}).json()["id"]
    ask(bo, chat, "Hello there.")
    assert INSTRUCTION not in json.dumps(world.gateway.requests[-1])


def test_a_first_share_cut_short_is_finished_by_sharing_again(
    world: World, monkeypatch: Any
) -> None:
    """Review S1: each document moves whole; a full disk is said; sharing again moves the rest."""
    import shutil as real

    from eugene_plexus_workbench import team

    together(world)
    ada, bo = world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    project = ada.post("/api/projects", json={"name": "Harbour"}).json()["id"]
    for name, word in (("alpha.md", "zqalphaword"), ("beta.md", "zqbetaword")):
        added = ada.post(
            "/api/documents",
            data={"projectId": project},
            files=[("files", (name, md(f"The code is {word}."), "text/markdown"))],
        )
        assert added.status_code == 201, added.text
    settle(ada)

    class FullDisk:
        calls = 0

        def copyfile(self, src: Any, dst: Any) -> Any:
            FullDisk.calls += 1
            if FullDisk.calls >= 2:
                raise OSError(28, "No space left on device")
            return real.copyfile(src, dst)

    shares = {"shares": [{"kind": "person", "id": "p-bo", "role": "read"}]}
    monkeypatch.setattr(team, "shutil", FullDisk())
    first = ada.put(f"/api/projects/{project}/sharing", json=shares)
    assert first.status_code == 503
    assert "Moved 1 of 2" in first.text and "No space left on device" in first.text
    monkeypatch.undo()
    second = ada.put(f"/api/projects/{project}/sharing", json=shares)
    assert second.status_code == 200, second.text
    collection = second.json()["collectionId"]
    assert ada.get(f"/api/documents?projectId={project}").json()["documents"] == []
    listed = settled(ada, collection)["documentList"]
    assert sorted(d["title"] for d in listed) == ["alpha.md", "beta.md"]
    assert ada.get(f"/api/projects/{project}").json()["documents"] == 2


def test_the_owners_download_holds_the_shared_projects_documents(world: World) -> None:
    """Review S5, and default (b): the project's shared documents are in its owner's download;
    the owner is told how many of them other people added before deleting it."""
    import io
    import time
    import zipfile

    ada, bo, project, _ = shared_project(world)
    collection = ada.get(f"/api/projects/{project}").json()["collectionId"]
    ada.put(
        f"/api/projects/{project}/sharing",
        json={"shares": [{"kind": "person", "id": "p-bo", "role": "contribute"}]},
    )
    added = bo.post(
        f"/api/collections/{collection}/documents",
        files=[("files", ("bo.md", md("Bo's chart notes."), "text/markdown"))],
    )
    assert added.status_code == 201, added.text
    assert ada.get(f"/api/projects/{project}").json()["sharing"]["othersDocuments"] == 1
    made = ada.post("/api/me/export")
    assert made.status_code == 202, made.text
    export = made.json()["export"]
    deadline = time.perf_counter() + 60
    while export["state"] == "making" and time.perf_counter() < deadline:
        time.sleep(0.1)
        export = next(
            e for e in ada.get("/api/exports").json()["exports"] if e["id"] == export["id"]
        )
    archive = zipfile.ZipFile(io.BytesIO(ada.get(f"/api/exports/{export['id']}").content))
    names = archive.namelist()
    assert any(n.endswith("survey.md") and "shared project Harbour" in n for n in names), names
