"""G5: real Chrome, OIDC, SDK producers and AG-UI agent; counted external effects."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import test_mcp_apps
from .conftest import Browser, ServerThread, World
from .test_a2ui import offer
from .test_a2ui import producer as producer_fixture
from .test_agui import agent as agent_fixture
from .test_job_sites import _owner_reads
from .test_mcp_apps import app_server as app_fixture

producer = producer_fixture
agent = agent_fixture
app_server = app_fixture


@pytest.fixture
def network_probe() -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {"calls": 0}
    app = FastAPI()

    @app.get("/probe")
    async def probe() -> JSONResponse:
        state["calls"] += 1
        return JSONResponse({"ok": True}, headers={"Access-Control-Allow-Origin": "*"})

    server = ServerThread(app).start()
    state["url"] = server.url + "/probe"
    try:
        yield state
    finally:
        server.stop()


@pytest.fixture
def offline_app(monkeypatch: Any, request: Any) -> Any:
    html = (Path(__file__).parent / "fixtures/ui-app.html").read_text(encoding="utf-8")
    monkeypatch.setattr(test_mcp_apps, "HTML", html)
    monkeypatch.setattr(test_mcp_apps, "POLICY", {"ui": {"csp": {}, "permissions": {}}})
    return request.getfixturevalue("app_server")


def credentials(person: Browser, url: str) -> dict[str, Any]:
    return {
        "secret": person.secret,
        "cookies": [
            {"name": c.name, "value": c.value, "url": url, "httpOnly": True, "sameSite": "Lax"}
            for c in person.http.cookies.jar
        ],
    }


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_interactive_content_in_chrome(
    world: World, offline_app: Any, producer: Any, agent: Any, network_probe: Any, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    world.restart_workbench()
    owner, ada, app_chat, connection, surface = test_mcp_apps.offered(world, offline_app)
    configured = owner.get("/api/tools/servers").json()["servers"]
    assert surface["sourceName"] == next(s["name"] for s in configured if s["id"] == connection)
    caps = ada.get("/api/v1/interactions/capabilities").json()
    assert {p["protocol"] for p in caps["supportedProtocols"]} == {"mcp-apps", "a2ui", "ag-ui"}
    assert all(p["renderer"] for p in caps["backendProtocols"])
    assert ada.get("/api/v1/ag-ui/capabilities").json()["renderer"]
    _, _, form_chat, _, _ = offer(world, producer)
    world.gateway.mode = "answer"
    _owner_reads(world)
    agent.by_message = True
    cfg = {
        "url": world.workbench,
        "ada": credentials(ada, world.workbench),
        "owner": credentials(owner, world.workbench),
        "appChat": app_chat,
        "appSurface": surface["id"],
        "connection": connection,
        "formChat": form_chat,
        "agentUrl": agent.url,
        "probeUrl": network_probe["url"],
        "playwright": os.environ["WORKBENCH_PLAYWRIGHT"],
        "chrome": os.getenv(
            "WORKBENCH_CHROME", "C:/Program Files/Google/Chrome/Application/chrome.exe"
        ),
        "screenshot": str(tmp_path / "interactive-content.png"),
    }
    config = tmp_path / "browser.json"
    config.write_text(json.dumps(cfg), encoding="utf-8")
    result = subprocess.run(
        ["node", str(root / "web/scripts/ui-browser.mjs"), str(config)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
    assert offline_app.calls == ["show", "increment"], offline_app.calls
    assert len(producer.effects) == 1, producer.effects
    assert producer.effects[0]["event"]["action"]["context"] == {"name": "Grace", "ok": True}
    assert len(agent.calls) == 5, agent.calls
    assert all(h.get("authorization") == "Bearer fixture-agent-token" for h in agent.headers)
    assert "private-display-state" not in json.dumps(world.gateway.requests)
    assert network_probe["calls"] == 0, "The app reached the network"
    print(result.stdout)


def test_agent_people_picker_is_owner_only_and_has_no_chat_metadata(world: World) -> None:
    owner, ada = world.browser(), world.browser()
    owner.sign_in("operator")
    ada.sign_in("p-ada")
    assert ada.get("/api/v1/ag-ui/people").status_code == 403
    people = owner.get("/api/v1/ag-ui/people").json()
    assert {p["sub"] for p in people} == {"operator", "p-ada"}
    assert all(set(p) == {"sub", "name"} for p in people)


def test_app_proxy_policy_is_a_response_header(world: World) -> None:
    response = world.browser().get("/mcp-apps/frame")
    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    for rule in (
        "connect-src 'none'",
        "frame-src 'none'",
        "form-action 'none'",
        "sandbox allow-scripts allow-forms",
    ):
        assert rule in policy
    assert "allow-same-origin" not in policy
    assert (
        response.headers["permissions-policy"]
        == "camera=(), microphone=(), geolocation=(), clipboard-write=()"
    )
