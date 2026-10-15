"""G2 real SDK server + authenticated headless host acceptance, no browser claims."""

from __future__ import annotations

import asyncio
import base64
import functools
import json
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, TextContent

from eugene_plexus_workbench import a2ui_state, mcp_ui, tools
from eugene_plexus_workbench.interaction_store import InteractionError

from .conftest import Browser, ServerThread, World
from .test_interactions import decision, root, wait_action
from .test_tools import decide, pending, setup

URI = "ui://fixture/app.html"
HTML = "<!doctype html><html><body>Fixture app<script>/* UI-only HTML */</script></body></html>"
POLICY = {
    "ui": {
        "csp": {"connectDomains": ["https://example.test"]},
        "permissions": {"clipboardWrite": {}},
        "prefersBorder": True,
    }
}


@dataclass
class AppServer:
    url: str = ""
    calls: list[str] = field(default_factory=list)
    capabilities: list[Any] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    resources: int = 0
    slow: bool = False
    huge: bool = False
    view_huge: bool = False
    blob: bool = False
    read_slow: bool = False
    reading: bool = False
    error: bool = False
    model_meta: dict[str, Any] = field(default_factory=lambda: {"ui": {"resourceUri": URI}})
    app_meta: dict[str, Any] = field(default_factory=lambda: {"ui": {"visibility": ["app"]}})


@pytest.fixture
def app_server(request: Any) -> Iterator[AppServer]:
    remote = AppServer()
    mcp = MCPServer("MCP Apps fixture", log_level="ERROR")

    @mcp.tool(meta=remote.model_meta)
    async def show(text: str, ctx: Context[Any, Any]) -> CallToolResult:
        remote.calls.append("show")
        caps = ctx.request_context.session.client_capabilities
        remote.capabilities.append(caps.extensions if caps else None)
        return CallToolResult(
            content=[TextContent(type="text", text="Readable fallback: " + text)],
            structured_content={"value": 7},
            _meta={"uiOnly": "x" * 300000 if remote.view_huge else "private-display-state"},
        )

    @mcp.tool(meta=remote.app_meta)
    async def increment(value: int) -> CallToolResult:
        remote.calls.append("increment")
        if remote.slow:
            await asyncio.sleep(8)
        return CallToolResult(
            content=[TextContent(type="text", text="Incremented " + str(value))],
            is_error=remote.error,
            _meta={"uiOnly": "x" * 70000 if remote.huge else "app-result-secret"},
        )

    @mcp.tool(meta={"ui": {"visibility": ["model"]}})
    async def model_only(value: int) -> str:
        remote.calls.append("model_only")
        return "model only"

    @mcp.resource(URI, mime_type=mcp_ui.MIME, meta=POLICY)
    async def app_html() -> str | bytes:
        remote.resources += 1
        remote.reading = True
        if remote.read_slow:
            await asyncio.sleep(1)
        return HTML.encode() if remote.blob else HTML

    @mcp.resource("ui://other/app.html", mime_type=mcp_ui.MIME)
    async def other_html() -> str:
        remote.resources += 1
        return HTML

    app = mcp.streamable_http_app(json_response=getattr(request, "param", True))

    async def capture(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            remote.headers.append({k.decode(): v.decode() for k, v in scope["headers"]})
        await app(scope, receive, send)

    server = ServerThread(capture).start()
    remote.url = server.url + "/mcp"
    try:
        yield remote
    finally:
        server.stop()


def offered(world: World, remote: AppServer) -> tuple[Browser, Browser, str, str, dict[str, Any]]:
    owner, ada, chat, connection = setup(world, remote)  # type: ignore[arg-type]
    assert (
        ada.post(f"/api/chats/{chat}/messages", json={"content": "Show the app"}).status_code == 201
    )
    answer = pending(ada, chat)
    assert decide(ada, chat, answer, True).status_code == 204
    answer = ada.wait_answer(chat)
    assert answer["status"] == "done", answer
    snapshot = ada.get(root(chat)).json()
    assert len(snapshot["surfaces"]) == 1, snapshot
    return owner, ada, chat, connection, snapshot["surfaces"][0]


def read(
    ada: Browser,
    chat: str,
    surface: dict[str, Any],
    method: str = "resources/read",
    params: dict[str, Any] | None = None,
) -> Any:
    return ada.post(
        root(chat) + f"/surfaces/{surface['id']}/mcp-apps/read",
        json={
            "revision": surface["revision"],
            "method": method,
            "params": {"uri": URI} if params is None else params,
        },
    )


def propose(
    ada: Browser,
    chat: str,
    surface: dict[str, Any],
    ident: str = "a1",
    name: str = "tools/call",
    args: dict[str, Any] | None = None,
) -> Any:
    return ada.post(
        root(chat) + f"/surfaces/{surface['id']}/actions",
        json={
            "id": ident,
            "revision": surface["revision"],
            "name": name,
            "arguments": {"name": "increment", "arguments": {"value": 2}} if args is None else args,
        },
    )


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("app_server", [True, False], indirect=True)
def test_discovery_resources_approval_restart_and_isolation(
    world: World, app_server: AppServer, legacy: bool, monkeypatch: Any
) -> None:
    if legacy:
        monkeypatch.setattr(tools, "Client", functools.partial(tools.Client, mode="legacy"))
    owner, ada, chat, connection, surface = offered(world, app_server)
    offered_names = json.dumps(world.gateway.requests[0]["tools"])
    assert "increment" not in offered_names, "app-only tool leaked to model"
    assert "model_only" in offered_names
    assert app_server.capabilities == [
        {
            mcp_ui.EXTENSION: {"mimeTypes": [mcp_ui.MIME]},
            a2ui_state.EXTENSION: {
                "version": a2ui_state.VERSION,
                "catalogIds": [a2ui_state.CATALOG],
            },
        }
    ]
    assert surface["view"]["toolResult"]["_meta"]["uiOnly"] == "private-display-state"
    assert "private-display-state" not in json.dumps(world.gateway.requests)
    assert "resourceUri" not in json.dumps(world.gateway.requests)
    initialized = read(
        ada,
        chat,
        surface,
        "ui/initialize",
        {
            "protocolVersion": mcp_ui.VERSION,
            "appInfo": {"name": "Fixture", "version": "1"},
            "appCapabilities": {},
        },
    )
    assert initialized.json()["hostCapabilities"] == {"serverTools": {}, "serverResources": {}}
    listed = read(ada, chat, surface, "tools/list", {}).json()["tools"]
    assert {t["name"] for t in listed} == {"show", "increment"}
    response = read(ada, chat, surface)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["contents"][0]["text"] == HTML
    assert response.json()["contents"][0]["_meta"] == POLICY
    assert "fixture-mcp-credential" not in json.dumps(surface) + response.text + json.dumps(listed)
    assert all(
        h.get("authorization") == "Bearer fixture-mcp-credential" for h in app_server.headers
    )
    assert propose(ada, chat, surface).json()["status"] == "proposed"
    assert app_server.calls == ["show"]
    assert decision(ada, chat).status_code == 200
    done = wait_action(ada, chat, "completed")
    assert done["result"]["_meta"]["uiOnly"] == "app-result-secret"
    assert app_server.calls == ["show", "increment"]
    assert decision(ada, chat).json()["status"] == "completed"
    world.restart_workbench()
    assert read(ada, chat, surface).status_code == 200
    assert propose(ada, chat, surface).json()["status"] == "completed"
    assert decision(ada, chat).json()["status"] == "completed"
    assert app_server.calls == ["show", "increment"]
    world.gateway.mode = "text"
    ada.post(f"/api/chats/{chat}/messages", json={"content": "What happened?"})
    ada.wait_answer(chat)
    payload = json.dumps(world.gateway.requests[-1])
    assert "Incremented 2" in payload
    assert "private-display-state" not in payload
    assert "app-result-secret" not in payload, "UI-only result leaked into model context"
    assert HTML not in payload
    assert owner.delete(f"/api/tools/servers/{connection}").status_code == 204
    assert read(ada, chat, surface).status_code == 409


def test_cross_person_resource_target_visibility_schema_and_method_denied(
    world: World, app_server: AppServer
) -> None:
    owner, ada, chat, _, surface = offered(world, app_server)
    grace = world.browser()
    assert grace.sign_in("p-bo").status_code == 303
    assert read(grace, chat, surface).status_code == 404
    assert read(owner, chat, surface).status_code == 404
    for target in ("ui://other/app.html", "https://example.test/", "file:///secret"):
        assert read(ada, chat, surface, params={"uri": target}).status_code == 422
    assert app_server.resources == 0, "forged resource target reached server"
    assert read(ada, chat, surface, "sampling/createMessage", {}).status_code == 422
    assert (
        read(
            ada,
            chat,
            surface,
            "ui/initialize",
            {"protocolVersion": "draft", "appInfo": {}, "appCapabilities": {}},
        ).status_code
        == 422
    )
    for ident, name, args in [
        ("model", "model_only", {"value": 1}),
        ("unknown", "elsewhere", {}),
        ("schema", "increment", {"value": "no"}),
    ]:
        assert (
            propose(ada, chat, surface, ident, args={"name": name, "arguments": args}).status_code
            == 200
        )
        assert decision(ada, chat, ident).status_code == 200
        wait_action(ada, chat, "failed", ident)
    assert app_server.calls == ["show"], "forbidden app call reached server"


def test_approved_context_replaces_previous_and_message_is_deferred(
    world: World, app_server: AppServer
) -> None:
    _, ada, chat, _, surface = offered(world, app_server)
    before = len(world.gateway.requests)
    for ident, method, args in [
        ("ctx1", "ui/update-model-context", {"content": [{"type": "text", "text": "old-context"}]}),
        ("ctx2", "ui/update-model-context", {"structuredContent": {"selected": "new-context"}}),
        (
            "msg",
            "ui/message",
            {"role": "user", "content": {"type": "text", "text": "approved-app-message"}},
        ),
    ]:
        assert propose(ada, chat, surface, ident, method, args).status_code == 200
        assert decision(ada, chat, ident).status_code == 200
        wait_action(ada, chat, "completed", ident)
    assert len(world.gateway.requests) == before
    assert (
        propose(
            ada,
            chat,
            surface,
            "system",
            "ui/message",
            {"role": "system", "content": {"type": "text", "text": "no"}},
        ).status_code
        == 422
    )
    assert (
        propose(
            ada,
            chat,
            surface,
            "image",
            "ui/update-model-context",
            {"content": [{"type": "image", "data": "AA=="}]},
        ).status_code
        == 422
    )
    world.gateway.mode = "text"
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Continue"})
    ada.wait_answer(chat)
    messages = world.gateway.requests[-1]["messages"]
    text = json.dumps(messages)
    assert "new-context" in text and "approved-app-message" in text and "old-context" not in text
    assert all(
        m["role"] == "user"
        for m in messages
        if "Approved interaction context:" in str(m["content"])
    )


@pytest.mark.parametrize("reason", ["oversized", "restart"])
def test_uncertain_effect_is_never_replayed(
    world: World, app_server: AppServer, reason: str
) -> None:
    _, ada, chat, _, surface = offered(world, app_server)
    app_server.huge = reason == "oversized"
    app_server.slow = reason == "restart"
    propose(ada, chat, surface)
    decision(ada, chat)
    if reason == "restart":
        deadline = time.perf_counter() + 5
        while "increment" not in app_server.calls and time.perf_counter() < deadline:
            time.sleep(0.02)
        assert "increment" in app_server.calls
        world.restart_workbench()
    wait_action(ada, chat, "uncertain")
    assert propose(ada, chat, surface).json()["status"] == "uncertain"
    assert decision(ada, chat).json()["status"] == "uncertain"
    assert app_server.calls == ["show", "increment"]


def test_changed_linkage_visibility_and_selection_rechecked(
    world: World, app_server: AppServer
) -> None:
    _, ada, chat, _, surface = offered(world, app_server)
    app_server.model_meta["ui"]["resourceUri"] = "ui://changed/app"
    assert read(ada, chat, surface).status_code in (409, 422)
    propose(ada, chat, surface)
    decision(ada, chat)
    wait_action(ada, chat, "failed")
    app_server.model_meta["ui"]["resourceUri"] = URI
    app_server.app_meta["ui"]["visibility"] = ["model"]
    propose(ada, chat, surface, "visibility")
    decision(ada, chat, "visibility")
    wait_action(ada, chat, "failed", "visibility")
    ada.patch(f"/api/chats/{chat}", json={"settings": {"toolServers": []}})
    assert read(ada, chat, surface).status_code == 409
    assert app_server.calls == ["show"]


def test_resource_access_rechecked_after_io(world: World, app_server: AppServer) -> None:
    from concurrent.futures import ThreadPoolExecutor

    owner, ada, chat, connection, surface = offered(world, app_server)
    app_server.read_slow = True
    with ThreadPoolExecutor() as pool:
        response = pool.submit(read, ada, chat, surface)
        deadline = time.perf_counter() + 5
        while not app_server.reading and time.perf_counter() < deadline:
            time.sleep(0.02)
        assert app_server.reading
        owner.delete(f"/api/tools/servers/{connection}")
        assert response.result().status_code == 409, "revoked resource bytes escaped"


def test_blob_resources_and_error_results(world: World, app_server: AppServer) -> None:
    _, ada, chat, _, surface = offered(world, app_server)
    app_server.blob = True
    response = read(ada, chat, surface)
    assert response.status_code == 200, response.text
    assert base64.b64decode(response.json()["contents"][0]["blob"]).decode() == HTML
    app_server.error = True
    propose(ada, chat, surface)
    decision(ada, chat)
    assert wait_action(ada, chat, "completed")["result"]["isError"] is True


@pytest.mark.parametrize("change", ["mime", "uri", "blob", "size", "html", "policy", "permissions"])
def test_invalid_resource_rejected(change: str) -> None:
    item: dict[str, Any] = {"uri": URI, "mimeType": mcp_ui.MIME, "text": HTML}
    if change == "mime":
        item["mimeType"] = "text/html"
    if change == "uri":
        item["uri"] = "ui://other/app"
    if change == "blob":
        item.pop("text")
        item["blob"] = "???"
    if change == "size":
        item["text"] += "x" * (256 * 1024)
    if change == "html":
        item["text"] = "<script>no document</script>"
    if change == "policy":
        item["_meta"] = {"ui": {"csp": {"unknown": []}}}
    if change == "permissions":
        item["_meta"] = {"ui": {"permissions": {"camera": True}}}
    with pytest.raises((ValueError, InteractionError)):
        mcp_ui.resource({"contents": [item]}, URI)


def test_model_cannot_guess_an_app_only_tool(world: World) -> None:
    session = tools.ToolSession(tools.Tools(None, world.settings), None)  # type: ignore[arg-type]
    session.add(
        {"id": "s", "name": "Fixture", "transport": "http"},
        None,
        "hidden",
        {"type": "object"},
        "",
        meta={"ui": {"visibility": ["app"]}},
    )
    assert session.definitions == []
    with pytest.raises(tools.ToolError, match="not offered"):
        session.prepare(
            {"id": "guessed", "function": {"name": next(iter(session.tools)), "arguments": "{}"}}
        )


def test_other_configured_server_is_not_an_app_target(world: World, app_server: AppServer) -> None:
    owner, ada, chat, connection, surface = offered(world, app_server)
    effects: list[int] = []
    other = MCPServer("Other server", log_level="ERROR")

    @other.tool(meta={"ui": {"visibility": ["app"]}})
    async def foreign(value: int) -> str:
        effects.append(value)
        return "foreign effect"

    server = ServerThread(other.streamable_http_app(json_response=True)).start()
    try:
        created = owner.post(
            "/api/tools/servers", json={"name": "Other", "url": server.url + "/mcp", "token": ""}
        )
        assert created.status_code == 201
        ada.patch(
            f"/api/chats/{chat}",
            json={"settings": {"toolServers": [connection, created.json()["id"]]}},
        )
        assert "foreign" not in read(ada, chat, surface, "tools/list", {}).text
        propose(ada, chat, surface, args={"name": "foreign", "arguments": {"value": 1}})
        decision(ada, chat)
        wait_action(ada, chat, "failed")
        assert effects == [], "cross-server tool ran"
    finally:
        server.stop()


def test_context_completion_order_and_historical_branch(
    world: World, app_server: AppServer
) -> None:
    _, ada, chat, _, surface = offered(world, app_server)
    for ident, text in (("first", "latest-approved"), ("second", "earlier-approved")):
        propose(
            ada,
            chat,
            surface,
            ident,
            "ui/update-model-context",
            {"content": [{"type": "text", "text": text}]},
        )
    for ident in ("second", "first"):
        decision(ada, chat, ident)
        wait_action(ada, chat, "completed", ident)
    world.gateway.mode = "text"
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Continue"})
    ada.wait_answer(chat)
    text = json.dumps(world.gateway.requests[-1]["messages"])
    assert "latest-approved" in text and "earlier-approved" not in text
    # Retry the app's original answer; its context cannot cross to a sibling.
    assert (
        ada.post(f"/api/chats/{chat}/messages/{surface['messageId']}/retry", json={}).status_code
        == 201
    )
    ada.wait_answer(chat)
    assert "latest-approved" not in json.dumps(world.gateway.requests[-1]["messages"])
    assert read(ada, chat, surface).status_code == 409


@pytest.mark.parametrize("reason", ["uri", "size"])
def test_invalid_ui_link_and_oversized_view_preserve_text(
    world: World, app_server: AppServer, reason: str
) -> None:
    if reason == "uri":
        app_server.model_meta["ui"]["resourceUri"] = "https://example.test/not-ui"
    else:
        app_server.view_huge = True
    _, ada, chat, _ = setup(world, app_server)  # type: ignore[arg-type]
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Show"})
    decide(ada, chat, pending(ada, chat), True)
    assert ada.wait_answer(chat)["status"] == "done"
    assert ada.get(root(chat)).json()["surfaces"] == []
    assert "Readable fallback" in json.dumps(world.gateway.requests[-1])


def test_owner_stdio_reconnect_and_process_cleanup(world: World, tmp_path: Path) -> None:
    from .test_local_tools import assert_stopped, events

    world.settings.account_kind = "windows_service" if os.name == "nt" else "systemd"
    owner = world.browser()
    owner.sign_in("operator")
    record = tmp_path / "app-processes.jsonl"
    created = owner.post(
        "/api/tools/servers",
        json={
            "name": "Local app",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(Path(__file__).parent / "fixtures" / "mcp_app.py"), str(record)],
            "environment": {},
        },
    )
    assert created.status_code == 201, created.text
    chat = owner.new_chat()
    owner.patch(f"/api/chats/{chat}", json={"settings": {"toolServers": [created.json()["id"]]}})
    world.gateway.mode = "tools"
    owner.post(f"/api/chats/{chat}/messages", json={"content": "Show local app"})
    decide(owner, chat, pending(owner, chat), True)
    assert owner.wait_answer(chat)["status"] == "done"
    surface = owner.get(root(chat)).json()["surfaces"][0]
    assert_stopped(record)
    world.restart_workbench()
    assert read(owner, chat, surface).status_code == 200
    assert_stopped(record)
    propose(owner, chat, surface)
    decision(owner, chat)
    wait_action(owner, chat, "completed")
    assert_stopped(record)
    assert [e for e in events(record) if e["event"] == "effect"] == [
        {"event": "effect", "value": 2}
    ]
