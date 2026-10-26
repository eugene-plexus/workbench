"""Row 27b: the project's choice survives an unavailable model."""

from eugene_plexus_workbench import projects

from .conftest import MODEL, World
from .test_memory_api import config, signed_in
from .test_memory_mv7 import project


def test_new_chat_in_project_starts_on_its_model(world: World) -> None:
    ada = signed_in(world)
    garden = project(ada, "Garden")
    result = ada.patch(f"/api/projects/{garden}", json={"model": MODEL})
    assert result.status_code == 200, result.text
    made = ada.post("/api/chats", json={"projectId": garden}).json()
    assert made["model"] == MODEL
    ada.patch(f"/api/projects/{garden}", json={"model": "not-served"}).raise_for_status()
    view = ada.get(f"/api/projects/{garden}").json()
    assert view["model"] == "not-served" and view["modelInEffect"] == MODEL
    assert ada.post("/api/chats", json={"projectId": garden}).json()["model"] == MODEL


def test_owner_view_exposes_only_project_names_and_chat_membership(world: World) -> None:
    ada = signed_in(world)
    garden = project(ada, "Garden")
    made = ada.post("/api/chats", json={"projectId": garden}).json()
    project(ada, "Private project with no chats")
    owner = signed_in(world, "operator")
    assert owner.get("/api/people/p-ada/chats").status_code == 403
    config(world, ownerReadsChats=True).raise_for_status()
    found = owner.get("/api/people/p-ada/chats").json()
    assert found["projects"] == [{"id": garden, "name": "Garden"}]
    assert found["chats"][0]["id"] == made["id"] and found["chats"][0]["projectId"] == garden
    assert owner.get(f"/api/projects/{garden}").status_code == 404
    ada.delete(f"/api/chats/{made['id']}").raise_for_status()
    assert owner.get("/api/people/p-ada/chats").json()["projects"] == []


async def test_project_fallback_uses_only_chat_models() -> None:
    class Hub:
        async def models(self):
            return {
                "data": [
                    {"id": "not-a-chat", "x_eugene_plexus": {"surfaces": []}},
                    {"id": "chat", "x_eugene_plexus": None},
                ]
            }

    assert await projects.model_in_effect(Hub(), "not-served") == ("chat", ["chat"])
