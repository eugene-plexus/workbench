"""C5a: real MCP HTTP transport, browser auth, gateway loop and durable intent."""

from __future__ import annotations

import asyncio
import functools
import json
import os
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from mcp.server import MCPServer
from starlette.responses import JSONResponse

from eugene_plexus_workbench.store import Store
from eugene_plexus_workbench.tools import Calls, ToolError, validate_url

from .conftest import MODEL, Browser, ServerThread, World


@dataclass
class Remote:
    url: str = ""
    calls: list[str] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    slow: bool = False
    huge: bool = False
    status: int | None = None
    oversized: bool = False


@pytest.fixture
def remote(request: Any) -> Iterator[Remote]:
    remote = Remote()
    mcp = MCPServer("Test tools", log_level="ERROR")

    @mcp.tool()
    async def echo(text: str) -> str:
        """Echo the provided text."""
        remote.calls.append(text)
        if remote.slow:
            await asyncio.sleep(5)
        return "x" * 2_100_000 if remote.oversized else "x" * 100_000 if remote.huge else text

    app = mcp.streamable_http_app(json_response=getattr(request, "param", True))

    async def capture(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            remote.headers.append({k.decode(): v.decode() for k, v in scope["headers"]})
            if remote.status is not None:
                await JSONResponse(
                    {"echoed": "fixture-mcp-credential"},
                    status_code=remote.status,
                    headers={"Location": remote.url + "/redirect"},
                )(scope, receive, send)
                return
        await app(scope, receive, send)

    server = ServerThread(capture).start()
    remote.url = server.url + "/mcp"
    try:
        yield remote
    finally:
        server.stop()


def setup(world: World, remote: Remote) -> tuple[Browser, Browser, str, str]:
    owner = world.browser()
    assert owner.sign_in("operator").status_code == 303
    result = owner.post(
        "/api/tools/servers",
        json={"name": "Shared echo", "url": remote.url, "token": "fixture-mcp-credential"},
    )
    assert result.status_code == 201, result.text
    server = result.json()["id"]
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    assert (
        ada.patch(f"/api/chats/{chat}", json={"settings": {"toolServers": [server]}}).status_code
        == 200
    )
    world.gateway.mode = "tools"
    return owner, ada, chat, server


def pending(browser: Browser, chat: str) -> dict[str, Any]:
    deadline = time.perf_counter() + 10
    last: dict[str, Any] = {}
    while time.perf_counter() < deadline:
        last = browser.get(f"/api/chats/{chat}").json()["messages"][-1]
        if last["toolRounds"]:
            return last
        assert last["status"] == "running", last
        time.sleep(0.03)
    raise AssertionError(last)


def decide(browser: Browser, chat: str, message: dict[str, Any], allow: bool) -> Any:
    call = message["toolRounds"][0]["calls"][0]
    return browser.post(
        f"/api/chats/{chat}/messages/{message['id']}/tools/decision",
        json={"callId": call["id"], "approve": allow},
    )


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("remote", [True, False], indirect=True)
def test_discover_approve_continue_and_reuse_history(
    world: World, remote: Remote, legacy: bool, monkeypatch: Any
) -> None:
    if legacy:
        from eugene_plexus_workbench import tools

        monkeypatch.setattr(tools, "Client", functools.partial(tools.Client, mode="legacy"))
    owner, ada, chat, server = setup(world, remote)
    checked = owner.post(f"/api/tools/servers/{server}/check")
    assert checked.status_code == 200, checked.text
    assert checked.json()["tools"][0]["name"] == "echo"
    for browser in (owner, ada):
        listed = browser.get("/api/tools/servers").json()
        assert listed["servers"][0]["hasToken"] is True
        assert "fixture-mcp-credential" not in json.dumps(listed)
    assert ada.post(f"/api/chats/{chat}/messages", json={"content": "Use echo"}).status_code == 201
    message = pending(ada, chat)
    assert message["toolRounds"][0]["calls"][0]["arguments"] == {"text": "hello"}
    assert remote.calls == []
    # The install owner may not approve someone else's chat.
    assert decide(owner, chat, message, True).status_code == 404
    assert decide(ada, chat, message, True).status_code == 204
    assert decide(ada, chat, message, True).status_code == 409
    done = ada.wait_answer(chat)
    assert done["status"] == "done", done
    assert done["toolRounds"][0]["calls"][0]["status"] == "done"
    assert remote.calls == ["hello"]
    messages = world.gateway.requests[-1]["messages"]
    assert messages[-2]["tool_calls"][0]["id"] == "call-1"
    assert messages[-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": 'hello\n{"result": "hello"}',
    }
    world.gateway.mode = "answer"
    ada.post(f"/api/chats/{chat}/messages", json={"content": "What happened?"})
    assert ada.wait_answer(chat)["status"] == "done"
    history = world.gateway.requests[-1]["messages"]
    assert sum(m["role"] == "tool" for m in history) == 1
    assert all(h.get("authorization") == "Bearer fixture-mcp-credential" for h in remote.headers)
    assert "fixture-mcp-credential" not in json.dumps(world.gateway.requests)


@pytest.mark.parametrize("action", ["decline", "remove", "stop"])
def test_no_execution_without_current_approval(world: World, remote: Remote, action: str) -> None:
    owner, ada, chat, server = setup(world, remote)
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Use echo"})
    message = pending(ada, chat)
    if action == "remove":
        assert owner.delete(f"/api/tools/servers/{server}").status_code == 204
    if action == "stop":
        assert ada.post(f"/api/chats/{chat}/stop").status_code == 204
        assert decide(ada, chat, message, True).status_code == 409
    else:
        assert decide(ada, chat, message, action == "remove").status_code == 204
    done = ada.wait_answer(chat)
    assert remote.calls == []
    assert done["toolRounds"][0]["calls"][0]["status"] in {"declined", "cancelled"}


@pytest.mark.parametrize(
    "mode,arguments",
    [("tools-unknown", "{}"), ("tools-cut", "{}"), ("tools", "{bad"), ("tools", '{"text":42}')],
)
def test_malformed_calls_never_reach_server(
    world: World, remote: Remote, mode: str, arguments: str
) -> None:
    _, ada, chat, _ = setup(world, remote)
    world.gateway.mode = mode
    world.gateway.tool_arguments = arguments
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Use echo"})
    done = ada.wait_answer(chat)
    assert done["status"] == "failed"
    assert done["error"]
    assert not done["toolRounds"]
    assert remote.calls == []


def test_stop_after_dispatch_is_uncertain(world: World, remote: Remote) -> None:
    _, ada, chat, _ = setup(world, remote)
    remote.slow = True
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Use echo"})
    decide(ada, chat, pending(ada, chat), True)
    deadline = time.perf_counter() + 5
    while not remote.calls and time.perf_counter() < deadline:
        time.sleep(0.03)
    assert remote.calls == ["hello"]
    assert ada.post(f"/api/chats/{chat}/stop").status_code == 204
    done = ada.wait_answer(chat)
    assert done["status"] == "stopped"
    assert done["toolRounds"][0]["calls"][0]["status"] == "uncertain"
    assert "may have acted" in done["toolRounds"][0]["calls"][0]["result"]


def test_owner_only_connections_and_bounded_result(world: World, remote: Remote) -> None:
    _, ada, chat, server = setup(world, remote)
    assert (
        ada.post("/api/tools/servers", json={"name": "bad", "url": remote.url}).status_code == 403
    )
    assert ada.delete(f"/api/tools/servers/{server}").status_code == 403
    remote.huge = True
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Use echo"})
    decide(ada, chat, pending(ada, chat), True)
    done = ada.wait_answer(chat)
    result = done["toolRounds"][0]["calls"][0]["result"]
    assert len(result) < 66_000 and "truncated" in result


async def test_restart_keeps_uncertainty_and_cancels_pending(tmp_path: Any) -> None:
    from eugene_plexus_workbench.store import Message

    path = tmp_path / "store.sqlite3"
    store = Store(path)
    await store.open()
    await store.add_message(
        Message(id="m", chat_id="c", seq=0, role="assistant", status="running", created_at=0)
    )
    await store.update_message(
        "m", tool_rounds=[{"calls": [{"status": "running"}, {"status": "pending"}]}]
    )
    await store.close()
    store = Store(path)
    await store.open()
    assert await store.mark_interrupted() == 1
    message = await store.message("m")
    assert message is not None and message.status == "interrupted"
    assert [c["status"] for c in message.tool_rounds[0]["calls"]] == ["uncertain", "cancelled"]
    await store.close()
    with sqlite3.connect(path) as db:
        assert "uncertain" in db.execute("SELECT tool_rounds FROM messages").fetchone()[0]


@pytest.mark.parametrize("status", [401, 403, 307])
def test_refusals_and_redirects_do_not_leak_credentials(
    world: World, remote: Remote, status: int
) -> None:
    owner, _, _, server = setup(world, remote)
    remote.status = status
    result = owner.post(f"/api/tools/servers/{server}/check")
    assert result.status_code == 502
    assert "fixture-mcp-credential" not in result.text
    assert ("credential" if status in (401, 403) else "redirect") in result.text
    assert len(remote.headers) == 1, "redirects must not be followed"
    assert remote.calls == []


def test_oversized_wire_result_is_uncertain_and_never_retried(world: World, remote: Remote) -> None:
    _, ada, chat, _ = setup(world, remote)
    remote.oversized = True
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Use echo"})
    decide(ada, chat, pending(ada, chat), True)
    done = ada.wait_answer(chat)
    assert done["status"] == "failed"
    assert done["toolRounds"][0]["calls"][0]["status"] == "uncertain"
    assert remote.calls == ["hello"]


def test_expired_approval_does_not_run(world: World, remote: Remote, monkeypatch: Any) -> None:
    from eugene_plexus_workbench import answers

    monkeypatch.setattr(answers, "_APPROVAL_SECONDS", 0.05)
    _, ada, chat, _ = setup(world, remote)
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Use echo"})
    done = ada.wait_answer(chat)
    assert done["status"] == "done"
    assert done["toolRounds"][0]["calls"][0]["status"] == "declined"
    assert "expired" in done["toolRounds"][0]["calls"][0]["result"]
    assert remote.calls == []


def test_web_search_is_counted_across_mcp_rounds_without_forcing_it_again(
    world: World, remote: Remote
) -> None:
    _, ada, chat, _ = setup(world, remote)
    ada.post(
        f"/api/chats/{chat}/messages", json={"content": "Search then use echo", "search": True}
    )
    decide(ada, chat, pending(ada, chat), True)
    done = ada.wait_answer(chat)
    assert done["status"] == "done"
    assert done["searches"] == 1
    assert "web_search_options" in world.gateway.requests[0]
    assert "web_search_options" not in world.gateway.requests[1]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/mcp",
        "file:///tmp/mcp",
        "https://user:pass@example.com/mcp",
        "https://example.com/mcp?secret=x",
        "https://example.com/#token",
        "https://example.com:bad/mcp",
    ],
)
def test_unsafe_addresses_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        validate_url(url)


def test_streamed_calls_are_interleaved_and_bounded() -> None:
    calls = Calls()
    calls.take(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 1, "id": "b", "function": {"name": "two", "arguments": "{"}},
                            {"index": 0, "id": "a", "function": {"name": "one", "arguments": "{}"}},
                        ]
                    }
                }
            ]
        }
    )
    calls.take(
        {"choices": [{"delta": {"tool_calls": [{"index": 1, "function": {"arguments": "}"}}]}}]}
    )
    assert [c["id"] for c in calls.finish()] == ["a", "b"]
    with pytest.raises(ToolError):
        calls.take({"choices": [{"delta": {"tool_calls": [{"index": 16}]}}]})


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_tools_in_system_chrome(world: World, remote: Remote, tmp_path: Path) -> None:
    """npm run build first; WORKBENCH_PLAYWRIGHT names playwright-core's directory."""
    root = Path(__file__).resolve().parents[1]
    built = root / "src/eugene_plexus_workbench/static"
    assert (built / "index.html").is_file(), "Build the page first"
    assert world.settings.static_dir is not None
    shutil.copytree(built, world.settings.static_dir, dirs_exist_ok=True)
    owner = world.browser()
    owner.sign_in("operator")
    world.gateway.mode = "tools"
    cfg = tmp_path / "browser.json"
    cfg.write_text(
        json.dumps(
            {
                "url": world.workbench,
                "mcp": remote.url,
                "secret": owner.secret,
                "model": MODEL,
                "playwright": os.environ["WORKBENCH_PLAYWRIGHT"],
                "chrome": os.getenv(
                    "WORKBENCH_CHROME", "C:/Program Files/Google/Chrome/Application/chrome.exe"
                ),
                "cookies": [
                    {
                        "name": c.name,
                        "value": c.value,
                        "url": world.workbench,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                    for c in owner.http.cookies.jar
                ],
                "screenshot": str(tmp_path / "tools-phone.png"),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/tools-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert remote.calls == ["hello"]
