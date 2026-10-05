"""Job sites in Workbench (`specs/docs/design/remote-nodes.md` §3.3, J9, J13, J18).

A person's own machines, managed with their own sign-in; every person told the
install's mode; and the owner's reading of other people's chats stopping at the
first job-site result in production mode (J13a), with results made in
production staying hidden after a switch to dev.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .conftest import ADMIN_TOKEN, CLIENT_ID, CLIENT_SECRET, FakeEugene, World
from .test_folders import finish, offer

CHANGED = "2026-10-05T12:00:00+00:00"


@pytest.fixture
def site_world(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> tuple[World, dict[str, Any]]:
    original = FakeEugene.app
    state: dict[str, Any] = {"mode": "production", "changedAt": None, "calls": [], "sites": []}

    def app(fake: FakeEugene) -> FastAPI:
        server = original(fake)

        def client_ok(request: Request) -> bool:
            expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            return request.headers.get("authorization") == "Basic " + expected

        @server.post("/oidc/install-mode")
        async def mode(request: Request) -> Any:
            if not client_ok(request):
                return JSONResponse({}, status_code=401)
            return {"mode": state["mode"], "changedAt": state["changedAt"]}

        @server.post("/oidc/node-helpers/{action}")
        async def helper(action: str, request: Request) -> Any:
            body = await request.json()
            sub = fake.refresh_tokens.get(body["refreshToken"])
            if action == "folders":
                return {
                    "grants": [
                        {
                            "id": "site-notes",
                            "node": "desk",
                            "name": "Notes",
                            "subject": sub,
                            "writable": False,
                            "usable": True,
                            "available": True,
                            "reason": None,
                            "jobSite": True,
                        }
                    ]
                    if sub == "p-ada"
                    else [],
                    "installMode": {"mode": state["mode"], "changedAt": state["changedAt"]},
                }
            state["calls"].append(body)
            return {
                "status": "done",
                "result": {"text": "SITE-SECRET"},
                "jobSite": True,
                "installMode": state["mode"],
            }

        @server.post("/oidc/job-sites")
        async def mine(request: Request) -> Any:
            body = await request.json()
            assert client_ok(request) and fake.refresh_tokens.get(body["refreshToken"])
            return {
                "sites": state["sites"],
                "installMode": {"mode": state["mode"]},
                "canInvite": True,
            }

        @server.post("/oidc/job-sites/invite")
        async def invite(request: Request) -> Any:
            body = await request.json()
            if fake.refresh_tokens.get(body["refreshToken"]) != "p-ada":
                return JSONResponse(
                    {"detail": {"detail": "Job sites belong to people."}}, status_code=403
                )
            return {
                "token": "JOIN-TOKEN",
                "expiresAt": "2026-10-05T12:15:00+00:00",
                "nodeName": body.get("nodeName"),
                "nodesUrl": "https://nodes.example.test:8443",
                "rootKey": "ROOTKEY",
                "owner": "ada o'neil",
            }

        @server.post("/oidc/job-sites/{node}/leave")
        async def leave(node: str) -> Any:
            return JSONResponse(None, status_code=204)

        return server

    monkeypatch.setattr(FakeEugene, "app", app)
    world: World = request.getfixturevalue("world")
    return world, state


def _owner_reads(world: World) -> None:
    answer = httpx.patch(
        f"{world.workbench}/v1/config",
        json={"ownerReadsChats": True},
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        trust_env=False,
    )
    assert answer.status_code == 200


def _chat_with_a_site_read(world: World) -> tuple[Any, Any, str]:
    owner, ada = world.browser(), world.browser()
    owner.sign_in("operator")
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Before the files"})
    ada.wait_answer(chat)
    chosen = ada.patch(
        f"/api/chats/{chat}", json={"settings": {"folderGrants": ["node:site-notes"]}}
    )
    assert chosen.status_code == 200, chosen.text
    world.gateway.mode = "tools"
    reading = offer(world, ada, chat, "read_text", path="note.txt")
    call = finish(ada, chat, reading)
    assert json.loads(call["result"])["text"] == "SITE-SECRET"
    world.gateway.mode = "text"
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Summarise it"})
    ada.wait_answer(chat)
    _owner_reads(world)
    return owner, ada, chat


def test_production_hides_the_chat_from_the_first_site_result_on(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    world, _ = site_world
    owner, ada, chat = _chat_with_a_site_read(world)
    mine = ada.get(f"/api/chats/{chat}").text
    assert "SITE-SECRET" in mine and '"redacted"' not in mine
    read = owner.get(f"/api/chats/{chat}")
    assert read.status_code == 200
    assert "SITE-SECRET" not in read.text and "Summarise it" not in read.text
    messages = read.json()["messages"]
    assert messages[0]["content"] == "Before the files" and "redacted" not in messages[0]
    hidden = [m for m in messages if m.get("redacted")]
    assert hidden and all(m["redacted"] == {"site": "desk"} for m in hidden)
    first = messages.index(hidden[0])
    assert all(m.get("redacted") for m in messages[first:])


def test_dev_mode_shows_dev_results_and_production_results_stay_hidden(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    world, state = site_world
    owner, _, chat = _chat_with_a_site_read(world)  # made in production
    state.update(mode="dev", changedAt=CHANGED)
    _wait_mode(owner, "dev")
    assert "SITE-SECRET" not in owner.get(f"/api/chats/{chat}").text, "J18: not retroactive"
    state["mode"] = "dev"
    dev_owner, _, dev_chat = _chat_with_a_site_read(world)  # made in dev
    assert "SITE-SECRET" in dev_owner.get(f"/api/chats/{dev_chat}").text
    state.update(mode="production", changedAt="2026-10-05T13:00:00+00:00")
    _wait_mode(owner, "production")
    assert "SITE-SECRET" not in dev_owner.get(f"/api/chats/{dev_chat}").text


def _wait_mode(browser: Any, mode: str) -> None:
    import time

    deadline = time.perf_counter() + 15
    while time.perf_counter() < deadline:
        if browser.get("/api/me").json()["installMode"] == mode:
            return
        time.sleep(0.2)
    pytest.fail(f"Workbench never saw {mode} mode")


def test_every_person_sees_the_mode_and_is_told_once_when_it_changes(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    world, state = site_world
    ada = world.browser()
    ada.sign_in("p-ada")
    me = ada.get("/api/me").json()
    assert me["installMode"] == "production" and me["installModeNotice"] is False
    state.update(mode="dev", changedAt=CHANGED)
    _wait_mode(ada, "dev")
    assert ada.get("/api/me").json()["installModeNotice"] is True
    assert ada.post("/api/me/mode-seen").status_code == 204
    assert ada.get("/api/me").json()["installModeNotice"] is False


def test_a_person_adds_their_own_machine_and_no_password_is_in_the_command(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    world, _ = site_world
    ada, owner = world.browser(), world.browser()
    ada.sign_in("p-ada")
    owner.sign_in("operator")
    assert ada.get("/api/job-sites").json()["canInvite"] is True
    invited = ada.post("/api/job-sites/invite", json={"nodeName": "laptop"})
    assert invited.status_code == 200, invited.text
    commands = invited.json()["commands"]
    assert "-JobSite -Owner 'ada o''neil' -RootKey ROOTKEY -NodeName laptop" in commands["windows"]
    assert (
        "--job-site --owner 'ada o'\\''neil' --root-key ROOTKEY --name laptop" in commands["posix"]
    )
    assert "JOIN-TOKEN" in commands["windows"] and "password" not in json.dumps(commands).lower()
    refused = owner.post("/api/job-sites/invite", json={})
    assert refused.status_code == 409 and "belong to people" in refused.text
    assert ada.post("/api/job-sites/invite", json={"nodeName": "bad name"}).status_code == 422
    assert ada.post("/api/job-sites/laptop/leave").status_code == 204
