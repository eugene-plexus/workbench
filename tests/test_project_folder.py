"""Row 27e: folder documents have exactly one place."""

import json
import sqlite3
from pathlib import Path

import pytest

from eugene_plexus_workbench.files import person_key
from eugene_plexus_workbench.knowledge.index import Index

from .conftest import World
from .folderdocs import grant, owner_and_ada, settle_folder, sync_and_settle, write
from .saved import ask
from .test_memory_api import config
from .test_memory_mv7 import project
from .test_memory_mv7b import chat_in, sent


@pytest.mark.usefixtures("quiet_loop")
def test_interrupted_folder_move_recovers_from_record_on_next_sync(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, ada = owner_and_ada(world)
    garden = project(ada, "Garden")
    write(notes, "beans.md", "The garden code is haricot.")
    granted = grant(world, owner, notes)
    ada.post("/api/documents/folders", json={"grantId": granted}).raise_for_status()
    settle_folder(ada, granted)
    original = Index.rescope

    async def interrupted(self, *args, **kwargs):
        raise OSError("disk unavailable before rescope")

    with monkeypatch.context() as patch:
        patch.setattr(Index, "rescope", interrupted)
        changed = ada.patch(f"/api/documents/folders/{granted}", json={"projectId": garden})
        assert changed.status_code == 503
        assert ada.get(f"/api/documents/folders/{granted}").json()["projectId"] == garden
    assert Index.rescope == original
    world.restart_workbench()
    sync_and_settle(ada, granted)
    directory = world.data / "knowledge" / person_key("p-ada")
    with sqlite3.connect(directory / "index.sqlite3") as db:
        [(doc_id, scope)] = db.execute(
            "SELECT id, scope FROM documents WHERE source LIKE 'folder:%'"
        ).fetchall()
    assert scope == f"project:{garden}"
    assert (
        json.loads((directory / f"{doc_id}.meta.json").read_text(encoding="utf-8"))["scope"]
        == scope
    )
    write(notes, "more.md", "Another garden fact.")
    sync_and_settle(ada, granted)
    with sqlite3.connect(directory / "index.sqlite3") as db:
        assert db.execute(
            "SELECT DISTINCT scope FROM documents WHERE source LIKE 'folder:%'"
        ).fetchall() == [(scope,)]


def test_delete_keep_reports_failed_folders_and_moves_the_rest(
    world: World, notes: Path, more_notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, ada = owner_and_ada(world)
    garden = project(ada, "Garden")
    ids = []
    for path, name in [(notes, "Blocked notes"), (more_notes, "Movable notes")]:
        write(path, "beans.md", "The garden code is haricot.")
        granted = grant(world, owner, path, name=name)
        ada.post(
            "/api/documents/folders", json={"grantId": granted, "projectId": garden}
        ).raise_for_status()
        settle_folder(ada, granted)
        ids.append(granted)
    sync = world.server.server.config.app.state.folder_sync
    original = sync.move

    async def partly(sub, grant_id, project_id):
        if grant_id == ids[0]:
            raise OSError("the file is held open")
        return await original(sub, grant_id, project_id)

    monkeypatch.setattr(sync, "move", partly)
    result = ada.delete(f"/api/projects/{garden}?keep=true")
    assert result.status_code == 503, result.text
    assert "Blocked notes" in result.text and "the file is held open" in result.text
    assert ada.get(f"/api/documents/folders/{ids[1]}").json()["projectId"] is None
    assert ada.get(f"/api/documents/folders/{ids[0]}").json()["projectId"] == garden
    assert ada.get(f"/api/projects/{garden}").status_code == 200


@pytest.mark.parametrize("refusal", ["documents-off", "withdrawn"])
def test_a_refused_folder_move_keeps_its_original_place(
    world: World, notes: Path, refusal: str
) -> None:
    owner, ada = owner_and_ada(world)
    garden = project(ada, "Garden")
    granted = grant(world, owner, notes)
    ada.post("/api/documents/folders", json={"grantId": granted}).raise_for_status()
    settle_folder(ada, granted)
    if refusal == "withdrawn":
        owner.delete(f"/api/folders/{granted}").raise_for_status()
    else:
        config(world, documentsEnabled=False).raise_for_status()
    changed = ada.patch(f"/api/documents/folders/{granted}", json={"projectId": garden})
    assert changed.status_code == (409 if refusal == "withdrawn" else 403), changed.text
    assert ada.get(f"/api/documents/folders/{granted}").json()["projectId"] is None


def test_project_folder_found_only_in_its_chats(world: World, notes: Path) -> None:
    owner, ada = owner_and_ada(world)
    garden = project(ada, "Garden")
    folder = notes
    write(folder, "beans.md", "The garden code is haricot.")
    granted = grant(world, owner, folder)
    added = ada.post("/api/documents/folders", json={"grantId": granted, "projectId": garden})
    assert added.status_code == 201, added.text
    settle_folder(ada, granted)
    listed = ada.get(f"/api/documents/folders?projectId={garden}").json()
    assert [f["id"] for f in listed["folders"]] == [granted]
    assert ada.get("/api/documents/folders").json()["folders"] == []
    inside = chat_in(ada, garden)
    ask(ada, inside, "What is the haricot garden code?")
    assert "haricot" in sent(world)
    outside = chat_in(ada, None)
    ask(ada, outside, "What is the haricot garden code?")
    assert "haricot" not in sent(world)
    kept = ada.delete(f"/api/projects/{garden}?keep=true")
    assert kept.status_code == 200, kept.text
    assert (folder / "beans.md").read_text() == "The garden code is haricot."
    assert [f["id"] for f in ada.get("/api/documents/folders").json()["folders"]] == [granted]


def test_moving_a_folder_rewrites_scope_and_sidecars_without_reading_again(
    world: World, notes: Path
) -> None:
    owner, ada = owner_and_ada(world)
    garden = project(ada, "Garden")
    write(notes, "beans.md", "The garden code is haricot.")
    granted = grant(world, owner, notes)
    ada.post("/api/documents/folders", json={"grantId": granted}).raise_for_status()
    settle_folder(ada, granted)
    before = ada.get(f"/api/documents/folders/{granted}/files").json()["files"][0]
    changed = ada.patch(f"/api/documents/folders/{granted}", json={"projectId": garden})
    assert changed.status_code == 200 and changed.json()["projectId"] == garden, changed.text
    after = ada.get(f"/api/documents/folders/{granted}/files").json()["files"][0]
    assert after["id"] == before["id"] and after["addedAt"] == before["addedAt"]
    directory = world.data / "knowledge" / person_key("p-ada")
    with sqlite3.connect(directory / "index.sqlite3") as db:
        assert db.execute(
            "SELECT scope FROM documents WHERE id = ?", (after["id"],)
        ).fetchone() == (f"project:{garden}",)
    metadata = json.loads((directory / f"{after['id']}.meta.json").read_text(encoding="utf-8"))
    assert metadata["scope"] == f"project:{garden}"
    ada.delete(f"/api/projects/{garden}").raise_for_status()
    assert (notes / "beans.md").read_text() == "The garden code is haricot."
    assert ada.get("/api/documents/folders").json()["folders"] == []
