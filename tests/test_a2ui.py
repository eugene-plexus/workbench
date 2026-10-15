"""G3: pinned schemas and real MCP/Workbench HTTP with counted effects."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from jsonschema import ValidationError
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, TextContent

from eugene_plexus_workbench import a2ui_state as ui
from eugene_plexus_workbench.interaction_store import InteractionError

from .conftest import Browser, ServerThread, World
from .test_interactions import decision, root, wait_action
from .test_tools import decide, pending, setup


def message(kind: str, **body: Any) -> dict[str, Any]:
    return {"version": "v0.9.1", kind: {"surfaceId": "form", **body}}


def envelope(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"mimeType": ui.MIME, "messages": messages, "fallback": "Review the form."}


def components() -> list[dict[str, Any]]:
    return [
        {"id": "root", "component": "Column", "children": ["name", "ok", "submit"]},
        {
            "id": "name",
            "component": "TextField",
            "label": "Name",
            "value": {"path": "/name"},
            "checks": [
                {
                    "condition": {"call": "required", "args": {"value": {"path": "/name"}}},
                    "message": "Name is required",
                }
            ],
        },
        {"id": "ok", "component": "CheckBox", "label": "Confirmed", "value": {"path": "/ok"}},
        {
            "id": "submit",
            "component": "Button",
            "child": "label",
            "action": {
                "event": {
                    "name": "save",
                    "context": {"name": {"path": "/name"}, "ok": {"path": "/ok"}},
                }
            },
        },
        {"id": "label", "component": "Text", "text": "Save"},
    ]


def initial() -> list[dict[str, Any]]:
    return [
        message("createSurface", catalogId=ui.CATALOG, sendDataModel=True),
        message("updateComponents", components=components()),
        message("updateDataModel", value={"name": "Ada", "ok": False, "private": "ui-only-data"}),
    ]


def view() -> dict[str, Any]:
    return {
        "surfaceId": "form",
        "catalogId": ui.CATALOG,
        "theme": {},
        "sendDataModel": True,
        "components": components(),
        "dataModel": {"name": "Ada", "ok": False},
        "ready": True,
    }


def event(name: str = "Ada", ok: bool = False) -> dict[str, Any]:
    return {
        "version": "v0.9.1",
        "action": {
            "surfaceId": "form",
            "sourceComponentId": "submit",
            "name": "save",
            "timestamp": "2026-10-15T14:00:00Z",
            "context": {"name": name, "ok": ok},
        },
    }


@dataclass
class Producer:
    url: str = ""
    effects: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0
    capabilities: list[Any] = field(default_factory=list)
    meta: dict[str, Any] = field(
        default_factory=lambda: {
            ui.EXTENSION: {
                "version": ui.VERSION,
                "catalogId": ui.CATALOG,
                "actionTool": "handle_ui",
            }
        }
    )
    app_meta: dict[str, Any] = field(default_factory=lambda: {"ui": {"visibility": ["app"]}})
    initial: Any = field(default_factory=lambda: envelope(initial()))
    update: Any = field(
        default_factory=lambda: envelope([message("updateDataModel", path="/saved", value=True)])
    )
    delay: bool = False
    schema: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def producer(request: Any) -> Iterator[Producer]:
    remote = Producer()
    mcp = MCPServer("A2UI fixture", log_level="ERROR")

    @mcp.tool(meta=remote.meta)
    async def show(text: str, ctx: Context[Any, Any]) -> CallToolResult:
        remote.calls += 1
        caps = ctx.request_context.session.client_capabilities
        remote.capabilities.append(caps.extensions if caps else None)
        return CallToolResult(
            content=[TextContent(type="text", text="Readable form summary: " + text)],
            _meta={ui.EXTENSION: remote.initial},
        )

    @mcp.tool(meta=remote.app_meta)
    async def handle_ui(event: dict[str, Any], dataModel: Any = None) -> CallToolResult:
        remote.effects.append({"event": event, "dataModel": dataModel})
        if remote.delay:
            await asyncio.sleep(5)
        return CallToolResult(
            content=[TextContent(type="text", text="Saved the form.")],
            _meta={ui.EXTENSION: remote.update} if remote.update is not None else {},
        )

    remote.schema = mcp._tool_manager.get_tool("handle_ui").parameters
    remote.meta = mcp._tool_manager.get_tool("show").meta
    server = ServerThread(
        mcp.streamable_http_app(json_response=getattr(request, "param", True))
    ).start()
    remote.url = server.url + "/mcp"
    try:
        yield remote
    finally:
        server.stop()


def offer(world: World, remote: Producer) -> tuple[Browser, Browser, str, str, dict[str, Any]]:
    owner, ada, chat, connection = setup(world, remote)  # type: ignore[arg-type]
    assert ada.post(f"/api/chats/{chat}/messages", json={"content": "Show form"}).status_code == 201
    assert decide(ada, chat, pending(ada, chat), True).status_code == 204
    assert ada.wait_answer(chat)["status"] == "done"
    surfaces = ada.get(root(chat)).json()["surfaces"]
    assert len(surfaces) == 1, surfaces
    return owner, ada, chat, connection, surfaces[0]


def form_input(
    ada: Browser, chat: str, surface: dict[str, Any], component: str = "name", value: Any = "Grace"
) -> Any:
    return ada.post(
        root(chat) + f"/surfaces/{surface['id']}/a2ui/input",
        json={"revision": surface["revision"], "componentId": component, "value": value},
    )


def propose(
    ada: Browser, chat: str, surface: dict[str, Any], ident: str = "a1", payload: Any = None
) -> Any:
    return ada.post(
        root(chat) + f"/surfaces/{surface['id']}/actions",
        json={
            "id": ident,
            "revision": surface["revision"],
            "name": "a2ui/action",
            "arguments": event() if payload is None else payload,
        },
    )


def test_pinned_schema_hashes_and_original_upstream_fixtures() -> None:
    source = json.loads((ui.SCHEMAS / "SOURCE.json").read_text())
    for filename, digest in source["files"].items():
        assert hashlib.sha256((ui.SCHEMAS / filename).read_bytes()).hexdigest() == digest
    original, _, _ = ui.validators("basic_catalog.json")
    for filename in ("simple_text.json", "interactive_button.json"):
        for msg in json.loads((ui.SCHEMAS / filename).read_text())["messages"]:
            original.validate(msg)
            if "createSurface" in msg:
                msg["createSurface"]["catalogId"] = ui.CATALOG
            ui.SERVER.validate(msg)


@pytest.mark.parametrize("value", [None, "", [], {}, 0, False, "valid"])
def test_required_and_boolean_functions(value: Any) -> None:
    assert ui.resolve({"call": "required", "args": {"value": value}}, {}, [100]) is (
        value not in (None, "", [], {})
    )
    expression = {
        "call": "and",
        "args": {"values": [True, {"call": "not", "args": {"value": False}}]},
    }
    assert ui.resolve(expression, {}, [100]) is True


def test_data_pointer_replacement_deletion_and_no_sparse_allocation() -> None:
    data = {"a/b": {"~x": [1, 2]}, "original": 3}
    changed = ui.write(data, "/a~1b/~0x/2", 7)
    assert ui.read(changed, "/a~1b/~0x") == [1, 2, 7]
    assert ui.read(ui.write(changed, "/a~1b/~0x/1", None, False), "/a~1b/~0x") == [1, 7]
    assert ui.write(data, "/missing/nested", None, False) == data
    assert ui.write(data, "", [False]) == [False]
    assert ui.write(data, "/", None, False) == {}
    assert data == {"a/b": {"~x": [1, 2]}, "original": 3}
    for path in ("relative", "/~2", "/__proto__", "/a~1b/~0x/100000000"):
        with pytest.raises(InteractionError):
            ui.write(data, path, 1)


@pytest.mark.parametrize(
    "kind",
    [
        "cycle",
        "orphan-cycle",
        "expansion",
        "relative",
        "regex",
        "asset",
        "function",
        "catalog",
        "prototype",
    ],
)
def test_untrusted_graph_and_catalog_limits(kind: str) -> None:
    data = view()
    if kind == "cycle":
        data["components"][0]["children"] = ["root"]
    elif kind == "orphan-cycle":
        data["components"].append({"id": "orphan", "component": "Card", "child": "orphan"})
    elif kind == "expansion":
        data["components"] = [
            {
                "id": "root" if i == 0 else str(i),
                "component": "Column",
                "children": [str(i + 1)] * 2,
            }
            for i in range(12)
        ]
    elif kind == "relative":
        data["components"][1]["value"]["path"] = "name"
    elif kind == "regex":
        data["components"][1]["validationRegexp"] = "(a+)+$"
    elif kind == "asset":
        data["components"].append(
            {"id": "asset", "component": "Image", "url": "https://example.test/x"}
        )
    elif kind == "function":
        data["components"][1]["value"] = {"call": "fetch", "args": {"url": "https://example.test"}}
    elif kind == "catalog":
        data["catalogId"] = "https://example.test/evil.json"
    else:
        data["dataModel"] = {"nested": {"constructor": {}}}
    with pytest.raises((InteractionError, ValidationError)):
        ui.validate_view(data)


@pytest.mark.parametrize("producer", [True, False], indirect=True)
def test_real_form_approval_atomic_update_restart_and_isolation(
    world: World, producer: Producer
) -> None:
    owner, ada, chat, _, surface = offer(world, producer)
    assert producer.capabilities[0][ui.EXTENSION] == {
        "version": ui.VERSION,
        "catalogIds": [ui.CATALOG],
    }
    capabilities = ada.get("/api/v1/interactions/capabilities").json()
    assert capabilities["supportedProtocols"] == []
    assert (
        next(p for p in capabilities["backendProtocols"] if p["protocol"] == "a2ui")["renderer"]
        is False
    )
    assert "handle_ui" not in json.dumps(world.gateway.requests[0]["tools"])
    assert "ui-only-data" not in json.dumps(world.gateway.requests), (
        "UI-only description leaked to model"
    )
    assert surface["view"]["ready"] and surface["actionable"]
    assert propose(ada, chat, surface).json()["status"] == "proposed"
    assert not producer.effects, "proposal executed an effect"
    assert form_input(owner, chat, surface).status_code == 404
    assert form_input(ada, chat, surface, "name", True).status_code == 422, (
        "wrong input type accepted"
    )
    assert form_input(ada, chat, surface).json() == {"revision": 2}
    assert not producer.effects, "form input executed an effect"
    assert wait_action(ada, chat, "cancelled")["status"] == "cancelled"
    assert form_input(ada, chat, surface).status_code == 409
    surface = ada.get(root(chat)).json()["surfaces"][0]
    assert propose(ada, chat, surface, "bad-context").status_code == 422, "forged context accepted"
    assert propose(ada, chat, surface, "save", event("Grace")).status_code == 200
    assert propose(ada, chat, surface, "stale", event("Grace")).status_code == 200
    decision(ada, chat, "save")
    done = wait_action(ada, chat, "completed", "save")
    assert len(producer.effects) == 1
    assert producer.effects[0]["dataModel"]["name"] == "Grace"
    assert "Saved the form." in json.dumps(done)
    new = ada.get(root(chat)).json()["surfaces"][0]
    assert new["revision"] == 3 and new["view"]["dataModel"]["saved"] is True, (
        "action update was not committed"
    )
    wait_action(ada, chat, "cancelled", "stale")
    world.restart_workbench()
    assert decision(ada, chat, "save").json()["status"] == "completed"
    assert len(producer.effects) == 1, "restart replayed an effect"
    world.gateway.mode = "text"
    ada.post(f"/api/chats/{chat}/messages", json={"content": "What happened?"})
    ada.wait_answer(chat)
    sent = json.dumps(world.gateway.requests[-1])
    assert "Saved the form." in sent
    assert "ui-only-data" not in sent and "updateDataModel" not in sent, (
        "UI-only result leaked to model"
    )


@pytest.mark.parametrize(
    "kind", ["mime", "catalog", "cycle", "oversize", "declaration", "untyped", "partial"]
)
def test_invalid_typed_ui_preserves_completed_tool_and_never_repairs(
    world: World, producer: Producer, kind: str
) -> None:
    if kind == "mime":
        producer.initial["mimeType"] = "text/plain"
    elif kind == "catalog":
        producer.initial["messages"][0]["createSurface"]["catalogId"] = "https://example.test"
    elif kind == "cycle":
        producer.initial["messages"][1]["updateComponents"]["components"][0]["children"] = ["root"]
    elif kind == "oversize":
        producer.initial["messages"][2]["updateDataModel"]["value"] = "x" * 70000
    elif kind == "declaration":
        producer.meta[ui.EXTENSION]["version"] = "future"
    elif kind == "untyped":
        producer.meta.clear()
    else:
        producer.initial = '{"version":"v0.9.1","createSurface":'
    _, ada, chat, _ = setup(world, producer)  # type: ignore[arg-type]
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Show form"})
    decide(ada, chat, pending(ada, chat), True)
    assert ada.wait_answer(chat)["status"] == "done"
    assert ada.get(root(chat)).json()["surfaces"] == [], "invalid UI was published"
    assert "Readable form summary" in json.dumps(world.gateway.requests[-1])
    assert producer.calls == 1 and not producer.effects, "UI repair repeated a tool"


@pytest.mark.parametrize("failure", ["graph", "publication"])
def test_invalid_response_rolls_back_whole_batch_but_effect_completes(
    world: World, producer: Producer, monkeypatch: Any, failure: str
) -> None:
    _, ada, chat, _, surface = offer(world, producer)
    if failure == "graph":
        producer.update["messages"].append(
            message(
                "updateComponents",
                components=[{"id": "root", "component": "Card", "child": "root"}],
            )
        )
    else:
        from eugene_plexus_workbench.a2ui import A2UI

        original = A2UI.validate_surface

        def unavailable(self: Any, data: Any) -> None:
            original(self, data)
            if data.view["dataModel"].get("saved"):
                raise InteractionError("Simulated publication validation failure.")

        monkeypatch.setattr(A2UI, "validate_surface", unavailable)
    propose(ada, chat, surface)
    decision(ada, chat)
    done = wait_action(ada, chat, "completed")
    assert "uiError" in done["result"]
    assert ada.get(root(chat)).json()["surfaces"][0] == surface, (
        "invalid batch partially changed state"
    )
    assert len(producer.effects) == 1


def test_delete_recreate_never_reuses_durable_identity(world: World, producer: Producer) -> None:
    _, ada, chat, _, surface = offer(world, producer)
    producer.update = envelope([message("deleteSurface"), *initial()])
    propose(ada, chat, surface)
    decision(ada, chat)
    wait_action(ada, chat, "completed")
    surfaces = ada.get(root(chat)).json()["surfaces"]
    assert len(surfaces) == 2
    old = next(s for s in surfaces if s["id"] == surface["id"])
    new = next(s for s in surfaces if s["id"] != surface["id"])
    assert old["state"] == "deleted" and not old["actionable"]
    assert new["state"] == "active" and new["revision"] == 1
    assert propose(ada, chat, surface, "old").status_code == 409


@pytest.mark.parametrize("reason", ["visibility", "producer", "source", "schema"])
def test_current_source_and_action_tool_policy_rechecked(
    world: World, producer: Producer, reason: str
) -> None:
    owner, ada, chat, connection, surface = offer(world, producer)
    if reason == "visibility":
        producer.app_meta["ui"]["visibility"] = ["model"]
    elif reason == "producer":
        producer.meta[ui.EXTENSION]["actionTool"] = "other_server"
    elif reason == "source":
        owner.delete(f"/api/tools/servers/{connection}")
    else:
        producer.schema["properties"]["event"] = {"type": "string"}
    response = propose(ada, chat, surface)
    if reason == "source":
        assert response.status_code == 409
    else:
        assert response.status_code == 200
        decision(ada, chat)
        wait_action(ada, chat, "failed")
    assert not producer.effects, "revoked action target ran"


def test_incomplete_graph_and_local_function_do_not_offer_external_actions() -> None:
    data = view()
    data["components"].pop()
    ui.validate_view(data)
    assert data["ready"] is False
    with pytest.raises(InteractionError):
        ui.event(data, event())
    data = view()
    data["components"][3]["action"] = {"functionCall": {"call": "not", "args": {"value": False}}}
    ui.validate_view(data)
    with pytest.raises(InteractionError):
        ui.event(data, event())
    data = view()
    data["dataModel"]["name"] = ""
    with pytest.raises(InteractionError):
        ui.event(data, event(""))
    forged = copy.deepcopy(event())
    forged["action"]["context"]["ok"] = 0
    with pytest.raises(InteractionError):
        ui.event(view(), forged)


def test_concurrent_input_after_dispatch_records_uncertain_without_replay(
    world: World, producer: Producer
) -> None:
    _, ada, chat, _, surface = offer(world, producer)
    producer.delay = True
    propose(ada, chat, surface)
    decision(ada, chat)
    deadline = time.perf_counter() + 5
    while not producer.effects and time.perf_counter() < deadline:
        time.sleep(0.01)
    assert len(producer.effects) == 1
    assert form_input(ada, chat, surface).status_code == 200
    wait_action(ada, chat, "uncertain")
    assert decision(ada, chat).json()["status"] == "uncertain"
    assert len(producer.effects) == 1


def test_incremental_merges_deletions_and_no_data_disclosure(
    world: World, producer: Producer
) -> None:
    producer.initial["messages"][0]["createSurface"]["sendDataModel"] = False
    producer.initial["messages"].extend(
        [
            message(
                "updateComponents",
                components=[{"id": "label", "component": "Text", "text": "Submit now"}],
            ),
            message("updateDataModel", path="/list", value=[1, 2]),
            message("updateDataModel", path="/list/2", value=3),
            message("updateDataModel", path="/list/0"),
            message("updateDataModel", path="/private"),
        ]
    )
    _, ada, chat, _, surface = offer(world, producer)
    assert len(surface["view"]["components"]) == 5
    assert surface["view"]["components"][-1]["text"] == "Submit now"
    assert surface["view"]["dataModel"] == {"name": "Ada", "ok": False, "list": [2, 3]}
    propose(ada, chat, surface)
    decision(ada, chat)
    wait_action(ada, chat, "completed")
    assert producer.effects[0]["dataModel"] is None, "unrequested data model was disclosed"


def test_progressive_surface_stays_inert_until_references_resolve(
    world: World, producer: Producer
) -> None:
    producer.initial["messages"][1]["updateComponents"]["components"].pop()
    _, ada, chat, _, surface = offer(world, producer)
    assert not surface["view"]["ready"] and not surface["actions"]
    assert propose(ada, chat, surface).status_code == 422
    assert form_input(ada, chat, surface).status_code == 422
    assert not producer.effects


@pytest.mark.parametrize("reason", ["branch", "stop"])
def test_branch_and_stop_disable_input_and_actions(
    world: World, producer: Producer, reason: str
) -> None:
    _, ada, chat, _, surface = offer(world, producer)
    if reason == "branch":
        world.gateway.mode = "text"
        ada.post(f"/api/chats/{chat}/messages/{surface['messageId']}/retry", json={})
        ada.wait_answer(chat)
    else:
        assert ada.post(f"/api/chats/{chat}/stop").status_code == 204
    assert form_input(ada, chat, surface).status_code == 409
    assert propose(ada, chat, surface).status_code == 409
    assert not producer.effects


def test_action_storage_failure_rolls_back_completion_and_both_surfaces(
    world: World, producer: Producer, monkeypatch: Any
) -> None:
    from eugene_plexus_workbench.interaction_store import InteractionStore

    _, ada, chat, _, surface = offer(world, producer)
    producer.update = envelope([message("deleteSurface"), *initial()])
    original = InteractionStore.publish_in_transaction

    def fail_second(db: Any, data: Any, base: int, state: str) -> Any:
        if data["id"] != surface["id"]:
            raise InteractionError("Simulated full storage.", 413)
        return original(db, data, base, state)

    monkeypatch.setattr(InteractionStore, "publish_in_transaction", staticmethod(fail_second))
    propose(ada, chat, surface)
    decision(ada, chat)
    wait_action(ada, chat, "uncertain")
    assert ada.get(root(chat)).json()["surfaces"] == [surface], (
        "surface transaction did not roll back"
    )
    assert len(producer.effects) == 1
    assert decision(ada, chat).json()["status"] == "uncertain"
    assert len(producer.effects) == 1


def test_owner_stdio_producer_action_and_process_cleanup(world: World, tmp_path: Path) -> None:
    from .test_local_tools import assert_stopped, events

    world.settings.account_kind = "windows_service" if os.name == "nt" else "systemd"
    owner = world.browser()
    owner.sign_in("operator")
    record, payload = tmp_path / "processes.jsonl", tmp_path / "ui.json"
    payload.write_text(json.dumps(envelope(initial())), encoding="utf-8")
    created = owner.post(
        "/api/tools/servers",
        json={
            "name": "Local A2UI",
            "transport": "stdio",
            "command": sys.executable,
            "args": [
                str(Path(__file__).parent / "fixtures" / "a2ui_mcp.py"),
                str(record),
                str(payload),
            ],
            "environment": {},
        },
    )
    assert created.status_code == 201, created.text
    chat = owner.new_chat()
    owner.patch(f"/api/chats/{chat}", json={"settings": {"toolServers": [created.json()["id"]]}})
    world.gateway.mode = "tools"
    owner.post(f"/api/chats/{chat}/messages", json={"content": "Show form"})
    decide(owner, chat, pending(owner, chat), True)
    assert owner.wait_answer(chat)["status"] == "done"
    surface = owner.get(root(chat)).json()["surfaces"][0]
    assert_stopped(record)
    world.restart_workbench()
    propose(owner, chat, surface)
    decision(owner, chat)
    wait_action(owner, chat, "completed")
    assert_stopped(record)
    assert len([e for e in events(record) if e["event"] == "effect"]) == 1
