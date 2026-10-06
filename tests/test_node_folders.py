"""A central Workbench keeps each remote operation bound to its initiating sign-in."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from eugene_plexus_workbench.folder_io import FolderError, WriteUncertain
from eugene_plexus_workbench.node_folders import NodeFolders
from eugene_plexus_workbench.signin import Provider
from eugene_plexus_workbench.store import Person, SessionRow, Store

from . import fake_sites
from .conftest import FakeEugene, World
from .test_folders import finish, offer
from .test_tools import decide


@pytest.fixture
def remote_world(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> tuple[World, dict[str, Any]]:
    original = FakeEugene.app
    state: dict[str, Any] = {
        "folders": [
            {
                "id": "remote-notes",
                "site": fake_sites.DESK,
                "label": "Ada's desktop",
                "name": "Notes",
                "writable": True,
                "people": {"p-ada": True},
            }
        ],
        "content": "Desktop notes",
    }

    def app(fake: FakeEugene) -> FastAPI:
        server = original(fake)
        fake_sites.install(server, fake, state)
        return server

    monkeypatch.setattr(FakeEugene, "app", app)
    world: World = request.getfixturevalue("world")
    return world, state


def select(world: World) -> tuple[Any, Any, str]:
    ada, bo = world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    chat = ada.new_chat()
    chosen = ada.patch(
        f"/api/chats/{chat}", json={"settings": {"folderGrants": ["node:remote-notes"]}}
    )
    assert chosen.status_code == 200, chosen.text
    world.gateway.mode = "tools"
    return ada, bo, chat


def test_central_host_needs_no_local_file_account_and_each_call_requires_approval(
    remote_world: tuple[World, dict[str, Any]],
) -> None:
    world, remote = remote_world
    assert world.settings.account_kind is None
    ada, bo, chat = select(world)
    folders = ada.get("/api/folders").json()
    assert folders["localAvailable"] is False and folders["available"] is True
    assert folders["grants"][0]["source"] == "node"
    assert bo.get("/api/folders").json()["grants"] == []
    bo_chat = bo.new_chat()
    assert (
        bo.patch(
            f"/api/chats/{bo_chat}", json={"settings": {"folderGrants": ["node:remote-notes"]}}
        ).status_code
        == 400
    )
    reading = offer(world, ada, chat, "read_text", folder="Notes", path="note.txt")
    assert remote["calls"] == []
    assert decide(bo, chat, reading, True).status_code == 404
    result = finish(ada, chat, reading)
    assert json.loads(result["result"])["text"] == "Desktop notes"
    writing = offer(
        world,
        ada,
        chat,
        "write_text",
        folder="Notes",
        path="note.txt",
        text="Changed",
        expectedSha256="a" * 64,
    )
    assert remote["content"] == "Desktop notes"
    assert finish(ada, chat, writing)["status"] == "done"
    assert remote["content"] == "Changed"
    saved = ada.get(f"/api/chats/{chat}").text
    tokens = [call["refreshToken"] for call in remote["calls"]]
    assert [c["request"]["params"]["arguments"]["folder"] for c in remote["calls"]] == [
        "Notes",
        "Notes",
    ]
    assert all(
        token not in saved and token not in json.dumps(world.gateway.requests) for token in tokens
    )
    assert tokens[0] == tokens[1]


@pytest.mark.parametrize("reason", ["declined", "grant-removed", "person-disabled"])
def test_remote_write_cannot_outlive_approval_or_access(
    remote_world: tuple[World, dict[str, Any]], reason: str
) -> None:
    world, remote = remote_world
    ada, _, chat = select(world)
    writing = offer(
        world,
        ada,
        chat,
        "write_text",
        folder="Notes",
        path="note.txt",
        text="Wrong",
        expectedSha256="a" * 64,
    )
    if reason == "grant-removed":
        remote["allowed"] = False
    if reason == "person-disabled":
        world.eugene.disabled.add("p-ada")
    result = finish(ada, chat, writing, approve=reason != "declined")
    assert result["status"] in {"declined", "failed"}
    assert remote["calls"] == [] and remote["content"] == "Desktop notes"


def test_offline_folder_stays_visible_without_authorizing_work(
    remote_world: tuple[World, dict[str, Any]],
) -> None:
    world, remote = remote_world
    ada, _, chat = select(world)
    remote["available"] = False
    grant = ada.get("/api/folders").json()["grants"][0]
    assert grant["available"] is False and "offline" in grant["reason"]
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Read note.txt"})
    answer = ada.wait_answer(chat)
    assert answer["status"] == "failed"
    assert not remote["calls"]


@pytest.mark.parametrize(
    "scenario",
    ["missing-session", "different-person", "expired", "cancel", "timeout", "bad-answer"],
)
async def test_session_binding_and_uncertain_write_handling(tmp_path: Path, scenario: str) -> None:
    store = Store(tmp_path / "db.sqlite")
    await store.open()
    now = time.time()
    await store.put_session(
        SessionRow("sid", "secret", "ada", "ada-refresh", now + 60, now, now + 600)
    )
    secret = tmp_path / "oidc-secret"
    secret.write_text("app-only-secret")
    calls: list[httpx.Request] = []
    started = asyncio.Event()

    async def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("cancel"):
            return httpx.Response(204)
        if scenario == "cancel":
            started.set()
            await asyncio.Event().wait()
        if scenario == "timeout":
            raise httpx.ReadTimeout("lost reply", request=request)
        return httpx.Response(200, json=[])

    person = Person("ada", "Ada", "member", session_id="sid")
    if scenario == "missing-session":
        person.session_id = None
    if scenario == "different-person":
        person.sub = "bo"
    if scenario == "expired":
        row = await store.session("sid")
        assert row
        row.expires_at = now - 1
        await store.delete_session("sid")
        await store.put_session(row)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            provider = Provider(
                issuer="http://eugene/oidc", client_id="wb", secret_file=secret, http=http
            )
            remote = NodeFolders(store, provider, http)
            run = remote.call_tool(
                person, fake_sites.DESK, "files", "write_text", {"folder": "Notes", "path": "n.txt"}
            )
            if scenario == "cancel":
                task = asyncio.create_task(run)
                await asyncio.wait_for(started.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert len(calls) == 2 and calls[1].url.path.endswith("cancel")
                assert (
                    json.loads(calls[0].content)["operationId"]
                    == json.loads(calls[1].content)["operationId"]
                )
            elif scenario in {"timeout", "bad-answer"}:
                with pytest.raises(WriteUncertain):
                    await run
                assert len(calls) == 1  # Never retry a write.
            else:
                with pytest.raises(FolderError, match="sign-in ended"):
                    await run
                assert not calls
    finally:
        await store.close()


def test_one_server_per_machine_narrowed_to_the_chats_folders(
    remote_world: tuple[World, dict[str, Any]],
) -> None:
    """J6g: a machine's file server is one, its `folder` argument listing only
    the folders this chat selected there; a call cannot name another."""
    world, remote = remote_world
    remote["folders"].append(
        {
            "id": "remote-private",
            "site": fake_sites.DESK,
            "label": "Ada's desktop",
            "name": "Private",
            "writable": True,
            "people": {"p-ada": True},
        }
    )
    ada, _, chat = select(world)
    listed = [g["name"] for g in ada.get("/api/folders").json()["grants"]]
    assert listed == ["Notes", "Private"]
    reading = offer(world, ada, chat, "read_text", folder="Notes", path="note.txt")
    tools = world.gateway.requests[-1]["tools"]
    enums = {
        t["function"]["description"].split(": ")[1].split(".")[0]: t["function"]["parameters"][
            "properties"
        ]["folder"]["enum"]
        for t in tools
    }
    assert enums == {
        "list_directory": ["Notes"],
        "read_text": ["Notes"],
        "write_text": ["Notes"],
    }
    assert all("Ada's desktop · Files" in t["function"]["description"] for t in tools)
    assert finish(ada, chat, reading)["status"] == "done"
    world.gateway.tool_name = "read_text"
    world.gateway.tool_arguments = json.dumps({"folder": "Private", "path": "note.txt"})
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Read the private one."})
    sneaking = ada.wait_answer(chat)
    assert sneaking["status"] == "failed" and not sneaking["toolRounds"]
    assert all(c["request"]["params"]["arguments"]["folder"] == "Notes" for c in remote["calls"])
