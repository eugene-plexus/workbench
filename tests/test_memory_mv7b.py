"""Memory v2's MV7b: a project's documents.

Through a real Workbench over the fake gateway (`conftest.py`): documents kept to a project are
searched by its chats only, and My documents too while Also search My documents is on; a
file of the same name replaces one within its own place; deleting a project moves its
documents to My documents or takes them; and a project gone at boot takes its documents.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from .conftest import MODEL, Browser, World
from .saved import (
    RARE,
    SECOND,
    TERMINAL,
    ask,
    has_block,
    last,
    listing,
    md,
    start,
    user_text,
)


def project(person: Browser, name: str) -> str:
    made = person.post("/api/projects", json={"name": name})
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


def chat_in(person: Browser, project_id: str | None) -> str:
    made = person.post("/api/chats", json={"projectId": project_id, "model": MODEL})
    assert made.status_code in (200, 201), made.text
    chat = str(made.json()["id"])
    changed = person.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on"}})
    assert changed.status_code == 200, changed.text
    return chat


def upload(person: Browser, name: str, data: bytes, project_id: str | None = None) -> Any:
    return person.post(
        "/api/documents",
        files=[("files", (name, data, "application/octet-stream"))],
        data={"projectId": project_id} if project_id else None,
    )


def kept(person: Browser, project_id: str | None = None) -> list[dict[str, Any]]:
    """The documents of My documents, or of a project, once every one has been read."""
    params = {"projectId": project_id} if project_id else None
    deadline = time.perf_counter() + 90
    while True:
        got = person.get("/api/documents", params=params)
        assert got.status_code == 200, got.text
        docs = list(got.json()["documents"])
        if all(d["state"] in TERMINAL for d in docs) or time.perf_counter() > deadline:
            return docs
        time.sleep(0.05)


def sent(world: World) -> str:
    return user_text(last(world)[-1]) if has_block(world) else ""


def test_a_projects_documents_are_kept_to_it_and_searched_by_its_chats(world: World) -> None:
    ada = start(world)
    garden = project(ada, "Garden")
    made = upload(ada, "plan.md", md(f"The plan word is {RARE}."), garden)
    assert made.status_code == 201, made.text
    assert [d["name"] for d in kept(ada, garden)] == ["plan.md"]
    assert listing(ada)["documents"] == [], "not in My documents"
    inside = chat_in(ada, garden)
    ask(ada, inside, f"What is {RARE}?")
    assert RARE in sent(world)
    outside = chat_in(ada, None)
    ask(ada, outside, f"What is {RARE}?")
    assert RARE not in sent(world), "a project's documents stay in it"


def test_my_documents_reach_a_project_only_while_it_says(world: World) -> None:
    ada = start(world)
    garden = project(ada, "Garden")
    upload(ada, "mine.md", md(f"My word is {SECOND}."))
    kept(ada)
    inside = chat_in(ada, garden)
    ask(ada, inside, f"What is {SECOND}?")
    assert SECOND in sent(world)
    off = ada.patch(f"/api/projects/{garden}", json={"generalDocuments": False})
    assert off.status_code == 200 and off.json()["generalDocuments"] is False
    ask(ada, inside, f"What is {SECOND}?")
    assert SECOND not in sent(world)
    counts = ada.get(f"/api/chats/{inside}/documents").json()["counts"]["library"]
    assert counts["documents"] == 0, "the drawer counts what is searched"


def test_the_same_name_replaces_a_file_within_its_own_place_only(world: World) -> None:
    ada = start(world)
    garden = project(ada, "Garden")
    upload(ada, "notes.md", md("General notes."))
    upload(ada, "notes.md", md("Project notes, first."), garden)
    kept(ada)
    upload(ada, "notes.md", md("Project notes, second."), garden)
    [mine] = kept(ada, garden)
    assert mine["name"] == "notes.md"
    assert [d["name"] for d in kept(ada)] == ["notes.md"], "My documents' file stays"
    with sqlite3.connect(world.data / "knowledge" / _key("p-ada") / "index.sqlite3") as db:
        scopes = sorted(
            r[0] for r in db.execute("SELECT scope FROM documents WHERE name = 'notes.md'")
        )
    assert scopes == ["library", f"project:{garden}"]


def test_another_persons_project_takes_no_upload(world: World) -> None:
    ada = start(world)
    garden = project(ada, "Garden")
    bo = start(world, "p-bo")
    refused = upload(bo, "x.md", md("Bo's."), garden)
    assert refused.status_code == 404
    assert bo.get("/api/documents", params={"projectId": garden}).status_code == 404


def test_deleting_a_project_moves_its_documents_or_takes_them(world: World) -> None:
    ada = start(world)
    keep_it = project(ada, "Kept")
    upload(ada, "plan.md", md("Old general plan."))
    upload(ada, "plan.md", md(f"The project plan is {RARE}."), keep_it)
    upload(ada, "other.md", md("Another."), keep_it)
    kept(ada)
    assert ada.get(f"/api/projects/{keep_it}").json()["documents"] == 2
    released = ada.delete(f"/api/projects/{keep_it}", params={"keep": "true"})
    assert released.status_code == 200, released.text
    assert released.json()["documents"] == 2 and released.json()["replaced"] == 1
    names = sorted(d["name"] for d in kept(ada))
    assert names == ["other.md", "plan.md"]
    outside = chat_in(ada, None)
    ask(ada, outside, f"What is {RARE}?")
    assert RARE in sent(world), "the project's plan replaced the old one in My documents"
    gone = project(ada, "Gone")
    upload(ada, "gone.md", md("Goes with it."), gone)
    kept(ada)
    taken = ada.delete(f"/api/projects/{gone}")
    assert taken.status_code == 200 and taken.json()["documents"] == 1
    assert "gone.md" not in [d["name"] for d in kept(ada)]


def test_a_project_gone_at_boot_takes_its_documents_unless_every_project_is(
    world: World,
) -> None:
    ada = start(world)
    garden = project(ada, "Garden")
    other = project(ada, "Other")
    upload(ada, "plan.md", md("A plan."), garden)
    upload(ada, "keep.md", md("Kept."), other)
    kept(ada, garden)
    kept(ada, other)
    with sqlite3.connect(world.data / "workbench.sqlite3") as db:
        db.execute("DELETE FROM projects WHERE id = ?", (garden,))
    world.restart_workbench()
    path = world.data / "knowledge" / _key("p-ada") / "index.sqlite3"
    with sqlite3.connect(path) as db:
        left = dict(db.execute("SELECT scope, COUNT(*) FROM documents GROUP BY scope").fetchall())
    assert f"project:{garden}" not in left and left.get(f"project:{other}") == 1
    # Every project gone looks like a store restored from before them: nothing is deleted.
    with sqlite3.connect(world.data / "workbench.sqlite3") as db:
        db.execute("DELETE FROM projects")
    world.restart_workbench()
    with sqlite3.connect(path) as db:
        left = dict(db.execute("SELECT scope, COUNT(*) FROM documents GROUP BY scope").fetchall())
    assert left.get(f"project:{other}") == 1


def test_a_projects_document_is_opened_read_again_and_removed(world: World) -> None:
    ada = start(world)
    garden = project(ada, "Garden")
    upload(ada, "plan.md", md("A plan."), garden)
    [doc] = kept(ada, garden)
    assert ada.get(f"/api/documents/{doc['id']}").status_code == 200
    assert ada.get(f"/api/documents/{doc['id']}/original").status_code == 200
    assert ada.post(f"/api/documents/{doc['id']}/reread").status_code in (200, 202)
    kept(ada, garden)
    assert ada.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert kept(ada, garden) == []
    bo = start(world, "p-bo")
    assert bo.get(f"/api/documents/{doc['id']}").status_code == 404


def test_a_kept_projects_documents_are_rebuilt_in_my_documents(world: World) -> None:
    ada = start(world)
    garden = project(ada, "Garden")
    upload(ada, "plan.md", md("A plan."), garden)
    kept(ada, garden)
    assert ada.delete(f"/api/projects/{garden}", params={"keep": "true"}).status_code == 200
    assert [d["name"] for d in kept(ada)] == ["plan.md"]
    assert ada.post("/api/documents/rebuild").status_code == 200
    assert [d["name"] for d in kept(ada)] == ["plan.md"], "its own files say where it is now"


def test_a_failed_move_keeps_the_whole_project(world: World) -> None:
    ada = start(world)
    garden = project(ada, "Garden")
    upload(ada, "plan.md", md("A plan."), garden)
    kept(ada, garden)
    saved = world.server.server.config.app.state.saved

    async def broken(sub: str, project_id: str) -> tuple[int, int]:
        raise OSError("the file is held open")

    moving = saved.release_project
    saved.release_project = broken
    try:
        refused = ada.delete(f"/api/projects/{garden}", params={"keep": "true"})
    finally:
        saved.release_project = moving
    assert refused.status_code == 503 and "nothing was deleted" in refused.text
    assert ada.get(f"/api/projects/{garden}").status_code == 200
    assert [d["name"] for d in kept(ada, garden)] == ["plan.md"]


def test_a_projects_document_is_linked_only_to_its_notes(world: World) -> None:
    ada = start(world)
    garden = project(ada, "Garden")
    upload(ada, "plan.md", md("A plan."), garden)
    [doc] = kept(ada, garden)
    mine = ada.post("/api/memory/notes", json={"text": "Rests on the plan.", "projectId": garden})
    general = ada.post("/api/memory/notes", json={"text": "A general note."})
    linked = ada.post(
        f"/api/memory/notes/{mine.json()['note']['id']}/documents", json={"docId": doc["id"]}
    )
    assert linked.status_code == 200, linked.text
    refused = ada.post(
        f"/api/memory/notes/{general.json()['note']['id']}/documents", json={"docId": doc["id"]}
    )
    assert refused.status_code == 404


def _key(sub: str) -> str:
    from eugene_plexus_workbench import files

    return files.person_key(sub)
