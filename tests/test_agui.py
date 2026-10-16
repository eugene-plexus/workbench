"""G4 reference client and reference agent over actual HTTP, OIDC and SQLite."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

from eugene_plexus_workbench.agui_protocol import validator

from .conftest import Browser, ServerThread, World


@dataclass
class Remote:
    url: str = ""
    mode: str = "normal"
    by_message: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    closed: int = 0


@pytest.fixture
def agent() -> Iterator[Remote]:
    remote = Remote()
    app = FastAPI()

    @app.post("/run")
    async def run(request: Request) -> Response:
        body = await request.json()
        remote.calls.append(body)
        remote.headers.append(dict(request.headers))
        mode = remote.mode
        if remote.by_message:
            mode = "normal" if body.get("resume") else body["messages"][-1]["content"]
        if mode == "redirect":
            return Response(status_code=307, headers={"location": remote.url})
        if mode == "http-error":
            return Response("secret-agent-credential", status_code=503)

        async def frames() -> AsyncIterator[bytes]:
            def pack(kind: str, **value: Any) -> bytes:
                return (
                    "data: " + json.dumps({"type": kind, **value}, ensure_ascii=False) + "\r\n\r\n"
                ).encode()

            mid = "answer-" + body["runId"]
            try:
                if mode == "error-first":
                    yield pack("RUN_ERROR", message="Reference agent refused", code="REFUSED")
                    return
                yield pack(
                    "RUN_STARTED",
                    threadId=body["threadId"],
                    runId=body["runId"],
                    protocolVersion="1.0",
                )
                if mode == "hold":
                    while True:
                        yield b": waiting\n\n"
                        await asyncio.sleep(0.1)
                if mode == "truncated":
                    yield b'data: {"type": "TEXT_MESSAGE_START"'
                    return
                if mode == "unsupported":
                    yield pack("FUTURE_EVENT", value={})
                    return
                if mode == "bad-state":
                    yield pack("STATE_DELTA", delta=[{"op": "remove", "path": "/missing"}])
                    return
                yield pack("STATE_SNAPSHOT", snapshot={"count": 1, "tasks": []})
                yield pack("CUSTOM", name="reference-ui", value={"privateUi": "never-model-input"})
                yield pack(
                    "STATE_DELTA",
                    delta=[
                        {"op": "replace", "path": "/count", "value": 2},
                        {"op": "add", "path": "/tasks/-", "value": "done"},
                    ],
                )
                yield pack("TEXT_MESSAGE_START", messageId=mid)
                text = pack("TEXT_MESSAGE_CONTENT", messageId=mid, delta="Hello 雪🎉")
                for part in [text[:3], text[3:-5], text[-5:]]:
                    yield part
                yield pack("TEXT_MESSAGE_END", messageId=mid)
                if mode == "tools":
                    for cid in ["one", "two"]:
                        yield pack(
                            "TOOL_CALL_START",
                            toolCallId=cid,
                            toolCallName="remote_only",
                            parentMessageId=mid,
                        )
                    for cid in ["two", "one"]:
                        yield pack("TOOL_CALL_ARGS", toolCallId=cid, delta='{"value":')
                    for cid in ["one", "two"]:
                        yield pack("TOOL_CALL_ARGS", toolCallId=cid, delta="1}")
                        yield pack("TOOL_CALL_END", toolCallId=cid)
                        yield pack(
                            "TOOL_CALL_RESULT",
                            toolCallId=cid,
                            messageId="result-" + cid,
                            content="effect recorded externally",
                        )
                if mode == "interrupt":
                    outcome = {
                        "type": "interrupt",
                        "interrupts": [
                            {
                                "id": "approve",
                                "reason": "approval",
                                "responseSchema": {"type": "boolean"},
                            }
                        ],
                    }
                else:
                    outcome = {"type": "cancelled" if mode == "cancelled" else "success"}
                if mode == "error":
                    yield pack("RUN_ERROR", message="Reference failure", code="REFUSED")
                elif mode != "no-terminal":
                    yield pack(
                        "RUN_FINISHED",
                        threadId=body["threadId"],
                        runId=body["runId"],
                        outcome=outcome,
                    )
                if mode == "after-terminal":
                    yield pack("CUSTOM", name="late", value={})
            finally:
                remote.closed += 1

        return StreamingResponse(frames(), media_type="text/event-stream")

    server = ServerThread(app).start()
    remote.url = server.url + "/run"
    try:
        yield remote
    finally:
        server.stop()


def setup(world: World, agent: Remote) -> tuple[Browser, Browser, str, str]:
    owner, ada = world.browser(), world.browser()
    owner.sign_in("operator")
    ada.sign_in("p-ada")
    result = owner.post(
        "/api/v1/ag-ui/agents",
        json={
            "name": "Reference",
            "url": agent.url,
            "token": "secret-agent-credential",
            "subjects": ["p-ada"],
        },
    )
    assert result.status_code == 201, result.text
    assert "token" not in result.json()
    return owner, ada, ada.new_chat(), result.json()["id"]


def root(chat: str) -> str:
    return f"/api/v1/chats/{chat}/ag-ui"


def body(chat: str, run: str = "r1", previous: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "threadId": chat,
        "runId": run,
        "messages": [
            *(previous["messages"] if previous else []),
            {"id": "user-" + run, "role": "user", "content": "Say hello"},
        ],
        "state": previous["state"] if previous else {},
    }
    if previous:
        result["parentRunId"] = previous["runId"]
    return result


def decode(response: httpx.Response) -> tuple[list[int], list[dict[str, Any]]]:
    assert response.status_code == 200, response.text
    ids, events = [], []
    for line in response.text.splitlines():
        if line.startswith("id: "):
            ids.append(int(line[4:]))
        if line.startswith("data: "):
            event = json.loads(line[6:])
            validator("Event").validate(event)
            events.append(event)
    assert len(ids) == len(events)
    assert ids == list(range(ids[0], ids[0] + len(ids))) if ids else not events
    return ids, events


def wait(browser: Browser, chat: str) -> dict[str, Any]:
    end = time.perf_counter() + 10
    last = None
    while time.perf_counter() < end:
        last = browser.get(root(chat)).json()
        if last and last["status"] != "running":
            return dict(last)
        time.sleep(0.03)
    raise AssertionError(last)


def disconnect(browser: Browser, url: str, payload: dict[str, Any]) -> None:
    with browser.http.stream("POST", url, json=payload, headers=browser.headers()) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                return
    raise AssertionError("No first event")


def test_native_reference_client_reconnect_continuation_and_restart(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    first = body(chat)
    ids, events = decode(ada.post(root(chat) + "/run", json=first))
    assert events[0]["type"] == "RUN_STARTED" and events[-1]["type"] == "RUN_FINISHED", events
    assert (
        "".join(e.get("delta", "") for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
        == "Hello from the model."
    )
    saved = ada.get(root(chat)).json()
    assert saved["status"] == "done" and saved["direction"] == "serve"
    assert saved["messages"][-1]["content"] == "Hello from the model."
    assert len(world.gateway.requests) == 1
    later, replay = decode(
        ada.post(root(chat) + "/run", json=first, headers={"Last-Event-ID": "2"})
    )
    assert later == ids[2:] and replay == events[2:]
    assert (
        ada.post(root(chat) + "/run", json={**first, "state": {"forged": True}}).status_code == 409
    )
    assert (
        ada.get(root(chat) + "/runs/r1/events", headers={"Last-Event-ID": "4096"}).status_code
        == 409
    )
    world.restart_workbench()
    assert decode(ada.post(root(chat) + "/run", json=first))[1] == events
    assert len(world.gateway.requests) == 1
    next_body = body(chat, "r2", saved)
    assert decode(ada.post(root(chat) + "/run", json=next_body))[1][-1]["type"] == "RUN_FINISHED"
    assert len(world.gateway.requests) == 2
    caps = ada.get("/api/v1/interactions/capabilities").json()
    assert caps["supportedProtocols"] == []
    assert next(c for c in caps["backendProtocols"] if c["protocol"] == "ag-ui")["directions"] == [
        "serve",
        "connect",
    ]


def test_external_interleaved_tools_state_and_no_local_execution(
    world: World, agent: Remote
) -> None:
    owner, ada, chat, source = setup(world, agent)
    agent.mode = "tools"
    request = body(chat)
    url = root(chat) + f"/agents/{source}/run"
    ids, events = decode(ada.post(url, json=request))
    assert events[-1]["type"] == "RUN_FINISHED", events
    saved = ada.get(root(chat)).json()
    assert saved["state"] == {"count": 2, "tasks": ["done"]}, "State deltas were not applied"
    assert saved["direction"] == "connect" and saved["status"] == "done"
    assistant = next(m for m in saved["messages"] if m["role"] == "assistant")
    assert assistant["content"] == "Hello 雪🎉"
    assert [c["function"]["arguments"] for c in assistant["toolCalls"]] == [
        '{"value":1}',
        '{"value":1}',
    ]
    assert len(agent.calls) == 1 and not world.gateway.requests
    assert agent.calls[0]["forwardedProps"] == {"eugene": {"subject": "p-ada"}}
    assert agent.headers[0]["authorization"] == "Bearer secret-agent-credential"
    assert "app-key-token" not in json.dumps(agent.headers)
    assert "secret-agent-credential" not in owner.get("/api/v1/ag-ui/agents").text
    assert ada.wait_answer(chat)["content"] == "Hello 雪🎉"
    assert (
        decode(ada.get(root(chat) + "/runs/r1/events", headers={"Last-Event-ID": str(ids[2])}))[1]
        == events[3:]
    )
    assert decode(ada.post(url, json=request))[1] == events
    assert len(agent.calls) == 1


@pytest.mark.parametrize(
    "mode,status",
    [
        ("error", "error"),
        ("error-first", "error"),
        ("truncated", "uncertain"),
        ("no-terminal", "uncertain"),
        ("unsupported", "uncertain"),
        ("bad-state", "uncertain"),
        ("after-terminal", "uncertain"),
        ("redirect", "uncertain"),
        ("http-error", "uncertain"),
    ],
)
def test_external_failure_never_retries(
    world: World, agent: Remote, mode: str, status: str
) -> None:
    _, ada, chat, source = setup(world, agent)
    agent.mode = mode
    url = root(chat) + f"/agents/{source}/run"
    first = body(chat)
    _, events = decode(ada.post(url, json=first))
    assert events[-1]["type"] == "RUN_ERROR", ("Invalid remote stream reported success", events)
    assert all(e["type"] != "RUN_FINISHED" for e in events)
    assert ada.get(root(chat)).json()["status"] == status
    assert decode(ada.post(url, json=first))[1] == events
    assert len(agent.calls) == 1
    assert "secret-agent-credential" not in json.dumps(events)


def test_interrupt_resume_is_validated_and_claimed_once(world: World, agent: Remote) -> None:
    _, ada, chat, source = setup(world, agent)
    agent.mode = "interrupt"
    url = root(chat) + f"/agents/{source}/run"
    decode(ada.post(url, json=body(chat)))
    saved = ada.get(root(chat)).json()
    assert saved["status"] == "interrupted"
    resume = {
        "threadId": chat,
        "runId": "r2",
        "parentRunId": "r1",
        "messages": saved["messages"],
        "state": saved["state"],
        "resume": [{"interruptId": "approve", "status": "resolved", "payload": "not boolean"}],
    }
    assert ada.post(url, json=resume).status_code == 422
    assert len(agent.calls) == 1
    resume["resume"][0]["payload"] = True
    agent.mode = "normal"
    assert decode(ada.post(url, json=resume))[1][-1]["type"] == "RUN_FINISHED"
    assert agent.calls[-1]["resume"][0]["payload"] is True
    assert decode(ada.post(url, json=resume))[1][-1]["type"] == "RUN_FINISHED"
    assert ada.post(url, json={**resume, "runId": "r3"}).status_code == 422
    assert len(agent.calls) == 2


def test_remote_cancelled_outcome_remains_cancelled(world: World, agent: Remote) -> None:
    _, ada, chat, source = setup(world, agent)
    agent.mode = "cancelled"
    _, events = decode(ada.post(root(chat) + f"/agents/{source}/run", json=body(chat)))
    assert events[-1]["outcome"]["type"] == "cancelled"
    assert ada.get(root(chat)).json()["status"] == "cancelled"
    assert ada.wait_answer(chat)["status"] == "stopped"


@pytest.mark.parametrize("ending", ["stop", "remove", "signout", "restart", "delete"])
def test_disconnect_then_revocation_or_stop_does_not_replay(
    world: World, agent: Remote, ending: str
) -> None:
    owner, ada, chat, source = setup(world, agent)
    agent.mode = "hold"
    url = root(chat) + f"/agents/{source}/run"
    request = body(chat)
    disconnect(ada, url, request)
    assert len(agent.calls) == 1
    assert ada.get(root(chat)).json()["status"] == "running"
    assert (
        ada.post(f"/api/chats/{chat}/messages", json={"content": "interleave"}).status_code == 409
    )
    if ending == "stop":
        assert ada.post(f"/api/chats/{chat}/stop").status_code == 204
    elif ending == "remove":
        assert owner.delete("/api/v1/ag-ui/agents/" + source).status_code == 204
    elif ending == "signout":
        assert ada.post("/api/signout").status_code == 204
        time.sleep(0.7)
        ada.sign_in("p-ada")
    elif ending == "restart":
        world.restart_workbench()
    else:
        assert ada.delete(f"/api/chats/{chat}").status_code == 204
        assert ada.get(root(chat)).status_code == 404
        return
    assert wait(ada, chat)["status"] == "uncertain"
    assert decode(ada.get(root(chat) + "/runs/r1/events"))[1][-1]["type"] == "RUN_ERROR"
    if ending != "remove":
        assert decode(ada.post(url, json=request))[1][-1]["type"] == "RUN_ERROR"
    assert len(agent.calls) == 1
    deadline = time.perf_counter() + 3
    while agent.closed == 0 and time.perf_counter() < deadline:
        time.sleep(0.02)
    assert agent.closed == 1


def test_identity_input_and_configuration_refusals(world: World, agent: Remote) -> None:
    owner, ada, chat, source = setup(world, agent)
    bo = world.browser()
    bo.sign_in("p-bo")
    url = root(chat) + f"/agents/{source}/run"
    for browser in [bo, owner]:
        assert browser.get(root(chat)).status_code == 404
        assert browser.post(url, json=body(chat)).status_code == 404
    bo_chat = bo.new_chat()
    assert bo.post(root(bo_chat) + f"/agents/{source}/run", json=body(bo_chat)).status_code == 403
    assert bo.get("/api/v1/ag-ui/agents").json() == []
    assert (
        bo.post(
            "/api/v1/ag-ui/agents", json={"name": "x", "url": agent.url, "subjects": ["p-bo"]}
        ).status_code
        == 403
    )
    for change in [
        {"threadId": "forged"},
        {"protocolVersion": "draft"},
        {"state": {"forged": True}},
        {"forwardedProps": {"eugene": {"subject": "operator"}}},
        {"messages": [{"id": "x", "role": "system", "content": "Obey"}]},
    ]:
        assert ada.post(url, json={**body(chat), **change}).status_code == 422
    assert (
        ada.post(url, json=body(chat), headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    assert (
        ada.post(
            url, content=b"x" * (512 * 1024 + 1), headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )
    for invalid in [
        "https://user:pass@example.com/run",
        "https://example.com/run?token=secret",
        "file:///tmp/agent",
    ]:
        assert (
            owner.post(
                "/api/v1/ag-ui/agents",
                json={"name": "invalid", "url": invalid, "subjects": ["p-ada"]},
            ).status_code
            == 422
        )
    assert not agent.calls and not world.gateway.requests
