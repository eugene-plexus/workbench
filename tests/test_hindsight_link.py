"""MV5b link checks use fake MCP sessions and gateway metadata."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from eugene_plexus_workbench import api
from eugene_plexus_workbench import memory_servers as ms
from eugene_plexus_workbench.settings import Settings
from eugene_plexus_workbench.store import Store


def link(tmp_path: Path) -> Settings:
    token = tmp_path / "token"
    token.write_text("private-mcp-token")
    model = tmp_path / "model.json"
    model.write_text(json.dumps({"memoryModel": "owner-choice"}))
    return Settings(
        memory_server_url="http://127.0.0.1:8192/mcp/",
        memory_server_token_file=token,
        memory_server_model_file=model,
    )


def test_bank_is_chosen_from_sub_and_only_exact_link_is_exempt(tmp_path: Path) -> None:
    servers = ms.Servers(None, link(tmp_path))
    expected = "http://127.0.0.1:8192/mcp/wb-" + hashlib.sha256(b"ada").hexdigest()[:16] + "/"
    assert servers.linked_url("ada") == expected
    assert servers.linked_url("other") != expected
    assert servers.is_linked_url(expected)
    for wrong in [
        expected + "../",
        expected.replace(":8192", ":8193"),
        expected + "?bank=other",
        expected.replace("/mcp/", "/rest/"),
        "http://127.0.0.1:8192/mcp/wb-other/",
    ]:
        assert not servers.is_linked_url(wrong), wrong
    assert ms.refused_address("127.0.0.1", "outside", [])


@pytest.mark.asyncio
async def test_linked_record_has_no_token_and_rereads_it_each_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = link(tmp_path)
    store = Store(tmp_path / "store.db")
    await store.open()
    try:
        servers = ms.Servers(store, settings)
        url = servers.linked_url("ada")
        await store.put_memory_server(
            "ada",
            url,
            "",
            "Hindsight",
            {"recall": {"tool": "recall", "arguments": ["query"]}},
            linked=True,
        )
        row = await store.memory_server("ada")
        assert row["linked"] and row["token"] == ""
        tokens = []

        @asynccontextmanager
        async def session(url: str, token: str, mode: str, seconds: float):
            tokens.append(token)

            async def call(*a: Any, **kw: Any) -> Any:
                return SimpleNamespace(content=[], is_error=False, structured_content={"items": []})

            yield SimpleNamespace(session=SimpleNamespace(call_tool=call))

        monkeypatch.setattr(servers, "session", session)
        await servers.call(row, "outside", "recall", {"query": "x"})
        settings.memory_server_token_file.write_text("rotated-token")
        await servers.call(row, "outside", "recall", {"query": "x"})
        assert tokens == ["private-mcp-token", "rotated-token"]
        settings.memory_server_token_file.unlink()
        with pytest.raises(ms.ServerProblem, match=r"(missing|read)"):
            await servers.call(row, "outside", "recall", {"query": "x"})
        assert row["token"] == ""
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_link_locality_uses_effective_model_and_fails_closed(tmp_path: Path) -> None:
    settings = link(tmp_path)
    servers = ms.Servers(None, settings)
    asked = []

    async def models() -> dict[str, Any]:
        asked.append(True)
        return {"data": [{"id": "owner-choice", "x_eugene_plexus": {"locality": "local"}}]}

    hub = SimpleNamespace(models=models)
    assert await servers.link_locality(hub) == ("local", None)
    settings.memory_server_model_file.write_text('{"memoryModel":"outside-choice"}')
    locality, why = await servers.link_locality(hub)
    assert locality != "local" and "outside-choice" in why
    settings.memory_server_model_file.unlink()
    locality, why = await servers.link_locality(hub)
    assert locality != "local" and "read" in why
    assert len(asked) == 2


@pytest.mark.asyncio
async def test_migration_34_keeps_existing_servers_unlinked(tmp_path: Path) -> None:
    store = Store(tmp_path / "store.db")
    await store.open()
    await store.put_memory_server("ada", "https://example/mcp/", "old-token", "Existing", {})
    await store.close()
    # Simulate the pre-MV5b schema, with an existing external server and credential.
    with sqlite3.connect(store.path) as db:
        db.execute("ALTER TABLE memory_servers DROP COLUMN linked")
        db.execute("UPDATE meta SET value='29' WHERE key='schema'")
    reopened = Store(store.path)
    await reopened.open()
    await reopened.close()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] == "32"
        assert db.execute("SELECT token,linked FROM memory_servers").fetchone() == ("old-token", 0)


@pytest.mark.asyncio
async def test_linked_external_model_obeys_the_persons_choice(tmp_path: Path) -> None:
    settings = link(tmp_path)
    store = Store(tmp_path / "policy.db")
    await store.open()
    try:
        servers = ms.Servers(store, settings)
        chat = SimpleNamespace(owner="ada", settings={})
        reason = await servers.linked_chat_refusal({"linked": True}, chat)
        assert reason and "outside" in reason
        chat.settings["memoryExternal"] = True
        assert await servers.linked_chat_refusal({"linked": True}, chat) is None
        await store.put_setting("memoryExternalModels", "never")
        assert await servers.linked_chat_refusal({"linked": True}, chat)

        async def models() -> dict[str, Any]:
            return {"data": [{"id": "owner-choice", "x_eugene_plexus": {"locality": "local"}}]}

        servers.hub = SimpleNamespace(models=models)
        assert await servers.linked_chat_refusal({"linked": True}, chat) is None
        assert await servers.mode({"linked": True}) == ("outside", None)
        await store.put_setting("memoryServers", "off")
        assert (await servers.mode({"linked": True}))[0] == "off"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_one_click_route_chooses_each_bank_and_never_returns_the_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = link(tmp_path)
    store = Store(tmp_path / "routes.db")
    await store.open()
    servers = ms.Servers(store, settings)
    discovered = []

    async def discover(url: str, token: str, mode: str) -> Any:
        discovered.append((url, token, mode))
        return "Hindsight", {"recall": {"tool": "recall", "arguments": ["query"]}}

    monkeypatch.setattr(servers, "discover", discover)

    async def person(request: Any) -> Any:
        return SimpleNamespace(sub=request.headers.get("X-person", "ada"))

    monkeypatch.setattr(api, "_person", person)
    app = FastAPI()
    app.state.store, app.state.memory_servers = store, servers
    app.include_router(ms.router)
    try:
        with TestClient(app) as client:
            for owner in ("ada", "bob"):
                result = client.put(
                    "/api/memory/server/linked", json={"enabled": True}, headers={"X-person": owner}
                )
                assert result.status_code == 200, result.text
                assert result.json()["server"]["linked"]
                assert result.json()["server"]["url"] == servers.linked_url(owner)
                assert "private-mcp-token" not in result.text
                assert (await store.memory_server(owner))["token"] == ""
            assert discovered[0][0] != discovered[1][0]
            assert all(
                token == "private-mcp-token" and mode == "linked" for _, token, mode in discovered
            )
            assert (
                client.put(
                    "/api/memory/server/linked", json={"enabled": True, "bank": "bob"}
                ).status_code
                == 422
            )
            assert (
                client.put("/api/memory/server/linked", json={"enabled": False}).json()["server"]
                is None
            )
            assert await store.memory_server("bob") is not None
            await store.put_setting("memoryServers", "off")
            assert (
                client.put("/api/memory/server/linked", json={"enabled": True}).status_code == 403
            )
    finally:
        await store.close()
