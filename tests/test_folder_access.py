"""A refused owner grant links to Eugene; the last removal releases its access."""

from pathlib import Path

import httpx

from eugene_plexus_workbench import folder_io

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
