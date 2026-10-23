"""Row 27e: folder documents have exactly one place."""

import json
import sqlite3
from pathlib import Path

import pytest

from eugene_plexus_workbench.files import person_key

from .conftest import World
from .folderdocs import grant, owner_and_ada, settle_folder, write
from .saved import ask
from .test_memory_api import config
from .test_memory_mv7 import project
from .test_memory_mv7b import chat_in, sent


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
