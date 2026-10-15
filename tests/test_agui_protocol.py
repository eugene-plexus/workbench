"""Pinned upstream conformance plus adversarial projection and framing checks."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from eugene_plexus_workbench import agui_protocol as p
from eugene_plexus_workbench.interaction_store import InteractionError

ROOT = Path(p.__file__).with_name("agui_schema")


def test_pin_and_hashes() -> None:
    source = json.loads((ROOT / "SOURCE.json").read_text())
    assert source["upstreamCommit"] == p.REVISION and source["protocolVersion"] == p.VERSION
    for name, digest in source["files"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("fixture", sorted((ROOT / "streams").glob("*.json")), ids=lambda f: f.stem)
def test_upstream_stream(fixture: Path) -> None:
    data = json.loads(fixture.read_text())
    events = data["stream"]
    run = p.Projection(events[0]["threadId"], events[0]["runId"], [], {})
    if fixture.stem.endswith("-fatal"):
        with pytest.raises(InteractionError):
            for event in events:
                run.apply(event)
        return
    for event in events:
        run.apply(event)
    expected = data["expect"]
    assert run.terminal
    if "messages" in expected:
        assert len(run.messages) == len(expected["messages"])
        for actual, wanted in zip(run.messages, expected["messages"], strict=True):
            assert {key: actual[key] for key in wanted} == wanted
    if "state" in expected:
        assert run.state == expected["state"]
    if "messageCount" in expected:
        assert len(run.messages) == expected["messageCount"]
    if events[-1]["type"] == "RUN_FINISHED" and "outcome" in events[-1]:
        assert run.outcome == events[-1]["outcome"]


def test_json_patch_all_operations_and_atomic_failure() -> None:
    initial = {"a/b": {"~": [1, 2]}, "": "empty", "bool": True}
    changed = p.patch(
        initial,
        [
            {"op": "test", "path": "/a~1b/~0/0", "value": 1},
            {"op": "add", "path": "/a~1b/~0/-", "value": 3},
            {"op": "copy", "from": "/a~1b/~0", "path": "/copy"},
            {"op": "move", "from": "/copy/0", "path": "/copy/2"},
            {"op": "remove", "path": "/a~1b/~0/1"},
            {"op": "replace", "path": "/", "value": "key"},
        ],
    )
    assert changed == {"a/b": {"~": [1, 3]}, "copy": [2, 3, 1], "": "key", "bool": True}
    before = copy.deepcopy(initial)
    with pytest.raises(InteractionError):
        p.patch(
            initial,
            [{"op": "remove", "path": "/bool"}, {"op": "test", "path": "/", "value": "wrong"}],
        )
    assert initial == before
    with pytest.raises(InteractionError):
        p.patch(initial, [{"op": "test", "path": "/bool", "value": 1}])
    assert p.patch(initial, [{"op": "replace", "path": "", "value": None}]) is None


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "add", "path": "/a/01", "value": 1},
        {"op": "remove", "path": "/a/-"},
        {"op": "replace", "path": "/a/2", "value": 1},
        {"op": "add", "path": "/missing/child", "value": 1},
        {"op": "add", "path": "/bad~2escape", "value": 1},
        {"op": "move", "from": "/a", "path": "/a/0/child"},
    ],
)
def test_patch_refuses_invalid_paths(operation: dict[str, Any]) -> None:
    with pytest.raises(InteractionError):
        p.patch({"a": [0]}, [operation])


def test_sse_bytes_multiline_crlf_and_limits() -> None:
    event = {"type": "CUSTOM", "name": "hello", "value": "雪🎉"}
    wire = (
        "id: ignored\r\n: comment\r\nevent: message\r\ndata: "
        + json.dumps(event, ensure_ascii=False, indent=2).replace("\n", "\r\ndata: ")
        + "\r\n\r\n"
    ).encode()
    for size in [1, 2, 7, 10000]:
        decoder = p.SSE()
        result = []
        for i in range(0, len(wire), size):
            result.extend(decoder.feed(wire[i : i + size]))
        result.extend(decoder.feed(b"", final=True))
        assert result == [event]
    for wire in [
        b'data: {"type": "CUSTOM"}',
        b"data: {}\n",
        b"data: \xff\n\n",
        b"data: [DONE]\n\n",
        b":" + b"x" * p.FRAME,
    ]:
        with pytest.raises(InteractionError):
            p.SSE().feed(wire, final=True)


@pytest.mark.parametrize("kind", ["RAW", "TEXT_MESSAGE_CHUNK", "REASONING_START", "FUTURE_EVENT"])
def test_unsupported_events_are_not_success(kind: str) -> None:
    run = p.Projection("thread", "run", [], {})
    run.apply({"type": "RUN_STARTED", "threadId": "thread", "runId": "run"})
    with pytest.raises(InteractionError):
        run.apply({"type": kind})


def test_patch_bound_and_projection_bound() -> None:
    with pytest.raises(InteractionError):
        p.patch({}, [{"op": "add", "path": "/a", "value": 1}] * 65)
    with pytest.raises(InteractionError):
        p.patch({}, [{"op": "add", "path": "/a", "value": "x" * p.FRAME}])
    with pytest.raises(InteractionError):
        p.input_value(
            {"threadId": "t", "runId": "r", "messages": [], "state": {"bad": float("nan")}}, "t"
        )


@pytest.mark.asyncio
async def test_cancellation_finishes_the_committed_projection() -> None:
    from eugene_plexus_workbench.agui import AGUI, Active
    from eugene_plexus_workbench.store import Person

    written, release = asyncio.Event(), asyncio.Event()

    async def append(*args: Any) -> None:
        written.set()
        await release.wait()

    host = object.__new__(AGUI)
    host.store = SimpleNamespace(agui=SimpleNamespace(append=append))  # type: ignore[assignment]
    active = Active(
        {"threadId": "t", "runId": "r"},
        Person("p", "Person", "member"),
        p.Projection("t", "r", [], {}),
    )
    task = asyncio.create_task(
        host.emit(active, {"type": "RUN_STARTED", "threadId": "t", "runId": "r"})
    )
    await written.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active.projection.started, "Committed event was lost from the projection"


def test_interrupt_schema_has_no_network_or_regex_execution() -> None:
    for schema in [{"$ref": "http://127.0.0.1:1/schema"}, {"$ref": "#"}, {"pattern": "(a+)+$"}]:
        with pytest.raises(InteractionError):
            p.response_schema(schema, "a")
    p.response_schema(
        {"type": "object", "required": ["ok"], "properties": {"ok": {"const": True}}}, {"ok": True}
    )
