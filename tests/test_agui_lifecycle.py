"""Native approvals, concurrent claims, quotas and real process termination."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest
import uvicorn

from eugene_plexus_workbench.app import DATABASE, create_app
from eugene_plexus_workbench.settings import Settings

from .conftest import World
from .test_a2ui import producer as form_fixture
from .test_agui import (
    Remote,
    body,
    decode,
    disconnect,
    root,
    setup,
    wait,
)
from .test_agui import agent as agent_fixture
from .test_node_folders import remote_world as signed_fixture
from .test_node_folders import select
from .test_signed_calls import WRITE, signing
from .test_tools import decide
from .test_tools import remote as tool_fixture
from .test_tools import setup as setup_tools

agent = agent_fixture
mcp_remote = tool_fixture
producer = form_fixture
signed_world = signed_fixture


def test_native_signing_does_not_emit_a_premature_tool_result(signed_world: Any) -> None:
    world, remote = signed_world
    remote["signed"] = True
    ada, _, chat = select(world)
    world.gateway.tool_name = "write_text"
    world.gateway.tool_arguments = json.dumps(WRITE)
    disconnect(ada, root(chat) + "/run", body(chat))
    deadline = time.perf_counter() + 10
    while not ada.get(f"/api/chats/{chat}").json()["messages"] and time.perf_counter() < deadline:
        time.sleep(0.02)
    message = signing(ada, chat)
    # Wait until the adapter has observed the outstanding signature.
    deadline = time.perf_counter() + 10
    while time.perf_counter() < deadline:
        saved = ada.get(root(chat)).json()
        if saved["state"].get("pendingApprovals"):
            break
        time.sleep(0.02)
    assert saved["state"]["pendingApprovals"]
    assert decide(ada, chat, message, False).status_code == 204
    assert wait(ada, chat)["status"] == "done"
    results = [
        e
        for e in decode(ada.get(root(chat) + "/runs/r1/events"))[1]
        if e["type"] == "TOOL_CALL_RESULT"
    ]
    assert len(results) == 1 and "did not sign" in results[0]["content"]
    assert remote["calls"] == []


def test_native_tools_require_existing_approval_and_saved_trace(
    world: World, mcp_remote: Any
) -> None:
    _, ada, chat, _ = setup_tools(world, mcp_remote)
    first = body(chat)
    disconnect(ada, root(chat) + "/run", first)
    deadline = time.perf_counter() + 10
    state: dict[str, Any] = {}
    while time.perf_counter() < deadline:
        state = ada.get(root(chat)).json()
        if state["state"].get("pendingApprovals"):
            break
        time.sleep(0.02)
    assert state["state"]["pendingApprovals"], state
    assert mcp_remote.calls == [], "AG-UI bypassed native approval"
    message = ada.get(f"/api/chats/{chat}").json()["messages"][-1]
    assert decide(ada, chat, message, True).status_code == 204
    assert wait(ada, chat)["status"] == "done"
    _, events = decode(ada.get(root(chat) + "/runs/r1/events"))
    kinds = [event["type"] for event in events]
    assert (
        kinds.index("TOOL_CALL_START")
        < kinds.index("TOOL_CALL_ARGS")
        < kinds.index("TOOL_CALL_END")
        < kinds.index("TOOL_CALL_RESULT")
    )
    assert mcp_remote.calls == ["hello"]
    decode(ada.post(root(chat) + "/run", json=first))
    assert mcp_remote.calls == ["hello"], "Reconnection repeated an approved tool"
    assert len(world.gateway.requests) == 2


def test_native_disconnect_and_explicit_stop(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    world.gateway.mode = "slow"
    world.gateway.delay = 1
    disconnect(ada, root(chat) + "/run", body(chat))
    assert ada.get(f"/api/chats/{chat}").json()["chat"]["running"]
    assert ada.post(root(chat) + "/runs/r1/stop").status_code == 204
    assert wait(ada, chat)["status"] == "cancelled"
    _, events = decode(ada.get(root(chat) + "/runs/r1/events"))
    assert events[-1]["outcome"]["type"] == "cancelled"
    before = len(world.gateway.requests)
    decode(ada.post(root(chat) + "/run", json=body(chat)))
    assert len(world.gateway.requests) == before


def test_stop_closes_partial_native_tool_stream(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eugene_plexus_workbench.agui import AGUI, Active

    async def partial(host: AGUI, active: Active, request: Any, value: dict[str, Any]) -> None:
        await host.emit(
            active, {"type": "RUN_STARTED", "threadId": value["threadId"], "runId": value["runId"]}
        )
        await host.emit(active, {"type": "TEXT_MESSAGE_START", "messageId": "answer"})
        await host.emit(
            active,
            {
                "type": "TOOL_CALL_START",
                "toolCallId": "pending",
                "toolCallName": "held",
                "parentMessageId": "answer",
            },
        )
        await asyncio.Event().wait()

    monkeypatch.setattr(AGUI, "_native", partial)
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    disconnect(ada, root(chat) + "/run", body(chat))
    deadline = time.perf_counter() + 10
    while ada.get(root(chat)).json()["cursor"] < 3 and time.perf_counter() < deadline:
        time.sleep(0.01)
    assert ada.post(root(chat) + "/runs/r1/stop").status_code == 204
    assert wait(ada, chat)["status"] == "cancelled"
    assert [e["type"] for e in decode(ada.get(root(chat) + "/runs/r1/events"))[1]][-3:] == [
        "TOOL_CALL_END",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]


def test_native_custom_event_links_to_authorized_a2ui_surface(world: World, producer: Any) -> None:
    _, ada, chat, _ = setup_tools(world, producer)
    disconnect(ada, root(chat) + "/run", body(chat))
    deadline = time.perf_counter() + 10
    while time.perf_counter() < deadline:
        detail = ada.get(f"/api/chats/{chat}").json()
        if detail["messages"] and detail["messages"][-1]["toolRounds"]:
            break
        time.sleep(0.02)
    assert decide(ada, chat, detail["messages"][-1], True).status_code == 204
    assert wait(ada, chat)["status"] == "done"
    events = decode(ada.get(root(chat) + "/runs/r1/events"))[1]
    custom = next(e for e in events if e["type"] == "CUSTOM" and e["name"] == "eugene.interactions")
    surface = ada.get(f"/api/v1/chats/{chat}/interactions").json()["surfaces"][0]
    assert custom["value"]["surfaces"][0]["id"] == surface["id"]
    assert custom["value"]["surfaces"][0]["protocol"] == "a2ui"
    assert "view" not in custom["value"]["surfaces"][0]
    assert "ui-only-data" not in json.dumps(world.gateway.requests)
    assert producer.calls == 1 and not producer.effects


def test_simultaneous_duplicate_runs_and_consumed_continuation(world: World, agent: Remote) -> None:
    _, ada, chat, source = setup(world, agent)
    url = root(chat) + f"/agents/{source}/run"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ada.post, url, json=body(chat)) for _ in range(2)]
        streams = [decode(f.result())[1] for f in futures]
    assert streams[0] == streams[1]
    assert len(agent.calls) == 1, "Duplicate run dispatched twice"
    saved = ada.get(root(chat)).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ada.post, url, json=body(chat, run, saved)) for run in ["r2", "r3"]]
        responses = [f.result() for f in futures]
    assert sorted(r.status_code for r in responses) in [[200, 409], [200, 422]]
    assert len(agent.calls) == 2, "A continuation was dispatched twice"


def test_external_fallback_excludes_ui_state_and_branch_change_refuses_continuation(
    world: World, agent: Remote
) -> None:
    _, ada, chat, source = setup(world, agent)
    url = root(chat) + f"/agents/{source}/run"
    agent.mode = "tools"
    decode(ada.post(url, json=body(chat)))
    saved = ada.get(root(chat)).json()
    assert (
        ada.post(
            f"/api/chats/{chat}/messages", json={"content": "Continue in Workbench"}
        ).status_code
        == 201
    )
    ada.wait_answer(chat)
    sent = json.dumps(world.gateway.requests[-1])
    assert "never-model-input" not in sent and "remote_only" not in sent
    assert "Hello" in sent
    assert ada.post(url, json=body(chat, "r2", saved)).status_code == 409
    assert len(agent.calls) == 1


def test_log_and_person_quotas_preserve_terminal_and_replay(
    world: World, agent: Remote, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eugene_plexus_workbench import agui_store

    monkeypatch.setattr(agui_store, "MAX_EVENTS", 4)
    monkeypatch.setattr(agui_store, "MAX_RUNS", 1)
    _, ada, chat, source = setup(world, agent)
    url = root(chat) + f"/agents/{source}/run"
    ids, events = decode(ada.post(url, json=body(chat)))
    assert ids == [1, 2, 3, 4] and events[-1]["type"] == "RUN_ERROR"
    assert ada.get(root(chat)).json()["status"] == "uncertain"
    assert decode(ada.post(url, json=body(chat)))[1] == events
    next_chat = ada.new_chat()
    assert (
        ada.post(root(next_chat) + f"/agents/{source}/run", json=body(next_chat)).status_code == 429
    )
    assert len(agent.calls) == 1
    assert ada.delete(f"/api/chats/{chat}").status_code == 204
    assert (
        ada.post(root(next_chat) + f"/agents/{source}/run", json=body(next_chat)).status_code == 200
    )
    assert len(agent.calls) == 2


def process_server(settings: dict[str, Any], port: int) -> None:
    for key in list(os.environ):
        if key.upper().startswith("EUGENE_PLEXUS_"):
            del os.environ[key]
    uvicorn.run(
        create_app(Settings.model_validate(settings)),
        host="127.0.0.1",
        port=port,
        log_level="error",
    )


def test_process_kill_after_external_dispatch_never_replays(world: World, agent: Remote) -> None:
    _, ada, chat, source = setup(world, agent)
    world.server.stop()
    context = multiprocessing.get_context("spawn")
    child: Any = None

    def start() -> Any:
        process = context.Process(
            target=process_server, args=(world.settings.model_dump(mode="json"), world.server.port)
        )
        process.start()
        deadline = time.perf_counter() + 20
        while time.perf_counter() < deadline:
            assert process.is_alive(), process.exitcode
            try:
                if ada.get("/healthz").status_code == 200:
                    return process
            except httpx.TransportError:
                pass
            time.sleep(0.05)
        process.kill()
        process.join(10)
        raise AssertionError("Workbench process did not start")

    try:
        child = start()
        agent.mode = "hold"
        url = root(chat) + f"/agents/{source}/run"
        first = body(chat)
        disconnect(ada, url, first)
        assert len(agent.calls) == 1
        child.kill()
        child.join(10)
        assert not child.is_alive()
        with sqlite3.connect(world.data / DATABASE) as db:
            assert (
                db.execute("SELECT status FROM agui_runs WHERE run_id='r1'").fetchone()[0]
                == "running"
            )
        child = start()
        saved = ada.get(root(chat)).json()
        assert saved["status"] == "uncertain", "Restart did not mark the dispatched run uncertain"
        assert decode(ada.post(url, json=first))[1][-1]["type"] == "RUN_ERROR"
        assert len(agent.calls) == 1, "Restart replayed an external AG-UI run"
        assert ada.wait_answer(chat)["status"] == "interrupted"
    finally:
        if child is not None and child.is_alive():
            child.kill()
            child.join(10)
