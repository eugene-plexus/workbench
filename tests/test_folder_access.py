"""A refused owner grant links to Eugene; the last removal releases its access."""

from pathlib import Path

import httpx
import pytest

from eugene_plexus_workbench import folder_io
from eugene_plexus_workbench.folders import Folders
from eugene_plexus_workbench.settings import Settings
from eugene_plexus_workbench.store import Store

from .conftest import World


def test_permission_error_links_to_console(world: World, monkeypatch, tmp_path: Path) -> None:
    world.settings.account_kind = "windows_service"
    owner = world.browser()
    owner.sign_in("operator")

    def denied(*args):
        raise PermissionError("Access is denied")

    monkeypatch.setattr(folder_io, "check_root_path", denied)
    response = owner.post(
        "/api/folders",
        json={"name": "Project", "path": str(tmp_path), "subject": "operator"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert f"Workbench's account cannot open {tmp_path}." in detail["message"]
    assert "/apps/app?" in detail["giveAccessUrl"]
    assert "mode=read" in detail["giveAccessUrl"]


def test_last_grant_release_and_observed_failure(world: World, monkeypatch, tmp_path: Path) -> None:
    world.settings.account_kind = "windows_service"
    owner = world.browser()
    owner.sign_in("operator")
    monkeypatch.setattr(folder_io, "check_root_path", lambda path, _: path)
    monkeypatch.setattr(folder_io, "inspect", lambda *_: "1:2:3")
    grants = [
        owner.post(
            "/api/folders",
            json={"name": "Project", "path": str(tmp_path), "subject": "operator"},
        ).json()["id"]
        for _ in range(2)
    ]
    calls = []

    async def release(path):
        calls.append(path)
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        world.server.server.config.app.state.tools.folders, "release", release, raising=False
    )
    assert owner.delete(f"/api/folders/{grants[0]}").status_code == 204
    assert calls == []
    response = owner.delete(f"/api/folders/{grants[1]}")
    assert calls == [str(tmp_path)]
    assert response.status_code == 200
    assert "connection refused" in response.json()["warning"]
    assert owner.get("/api/folders").json()["grants"] == []


def test_an_alias_grant_keeps_os_access(world: World, monkeypatch) -> None:
    world.settings.account_kind = "windows_service"
    owner = world.browser()
    owner.sign_in("operator")
    monkeypatch.setattr(folder_io, "check_root_path", lambda path, _: path)
    monkeypatch.setattr(folder_io, "inspect", lambda *_: "same-volume:file:creation-time")
    grants = [
        owner.post(
            "/api/folders", json={"name": "Project", "path": path, "subject": "operator"}
        ).json()["id"]
        for path in ("D:\\Project", "d:\\project")
    ]
    calls = []

    async def release(path):
        calls.append(path)

    monkeypatch.setattr(world.server.server.config.app.state.tools.folders, "release", release)
    assert owner.delete(f"/api/folders/{grants[0]}").status_code == 204
    assert calls == []
    assert owner.delete(f"/api/folders/{grants[1]}").status_code == 204
    assert calls == ["d:\\project"]


def test_descendant_grant_keeps_parent_access(world: World, monkeypatch) -> None:
    world.settings.account_kind = "windows_service"
    owner = world.browser()
    owner.sign_in("operator")
    monkeypatch.setattr(folder_io, "check_root_path", lambda path, _: path)
    monkeypatch.setattr(folder_io, "inspect", lambda path, _: path.lower())
    grants = [
        owner.post(
            "/api/folders", json={"name": "Project", "path": path, "subject": "operator"}
        ).json()["id"]
        for path in ("D:\\Projects", "d:\\projects\\acme")
    ]
    calls = []

    async def release(path):
        calls.append(path)

    monkeypatch.setattr(world.server.server.config.app.state.tools.folders, "release", release)
    assert owner.delete(f"/api/folders/{grants[0]}").status_code == 204
    assert calls == []


async def test_release_looks_up_then_uses_access_id(tmp_path: Path) -> None:
    import json

    key = tmp_path / "key"
    key.write_text("app key")
    requests = []

    def answer(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "grants": [
                    {
                        "id": "access-id",
                        "path": "D:\\Projects",
                        "state": "ready" if "path" in body else "removed",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        store = Store(tmp_path / "store.sqlite3")
        await store.open()
        try:
            folders = Folders(
                store,
                Settings(
                    data_dir=tmp_path / "private", agent_url="http://agent.invalid", key_file=key
                ),
            )
            folders.http = client
            await folders.release("d:\\projects\\acme")
        finally:
            await store.close()
    assert requests == [{"path": "d:\\projects\\acme"}, {"id": "access-id"}]


async def test_release_rejects_empty_success(tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_text("app key")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"grants": []}))
    ) as client:
        folders = Folders(
            Store(tmp_path / "store.sqlite3"),
            Settings(data_dir=tmp_path / "private", agent_url="http://agent.invalid", key_file=key),
        )
        folders.http = client
        with pytest.raises(ValueError, match="no matching"):
            await folders.release("D:\\Projects")
