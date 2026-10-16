"""Pinned AG-UI 1.0 validation, bounded projection and SSE transport.

No event, schema reference or JSON pointer here can fetch or execute anything.
The supported subset is specified in specs/workbench-ag-ui-v1.md.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry

from .interaction_store import InteractionError, encoded

VERSION = "1.0"
REVISION = "b3f61a96fa3c755f37558ac44f4acc5b1c3828f1"
FRAME = 128 * 1024
PROJECTION = 512 * 1024
EVENTS = (
    "RUN_STARTED",
    "RUN_FINISHED",
    "RUN_ERROR",
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "TOOL_CALL_START",
    "TOOL_CALL_ARGS",
    "TOOL_CALL_END",
    "TOOL_CALL_RESULT",
    "MESSAGES_SNAPSHOT",
    "STATE_SNAPSHOT",
    "STATE_DELTA",
    "STEP_STARTED",
    "STEP_FINISHED",
    "CUSTOM",
)
TERMINAL = {"RUN_FINISHED", "RUN_ERROR"}
ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def refuse(message: str) -> None:
    raise InteractionError(message, 422)


def same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


@lru_cache(maxsize=4)
def validator(name: str) -> Draft202012Validator:
    schema = json.loads(Path(__file__).with_name("agui_schema").joinpath("schema.json").read_text())
    return Draft202012Validator({**schema, "$ref": f"#/$defs/{name}"}, registry=Registry())


def validate(value: Any, name: str) -> None:
    encoded(value, PROJECTION if name == "RunAgentInput" else FRAME)
    if not validator(name).is_valid(value):
        refuse(f"Invalid AG-UI {name} for the pinned protocol.")


def messages(value: list[dict[str, Any]]) -> None:
    ids = [m["id"] for m in value]
    if len(ids) > 256 or len(set(ids)) != len(ids):
        refuse("AG-UI messages require unique IDs and at most 256 messages.")
    for m in value:
        if len(m["id"]) > 256 or m["role"] not in {
            "user",
            "assistant",
            "tool",
            "system",
            "developer",
        }:
            refuse("Unsupported AG-UI message role or ID.")
        if not isinstance(m.get("content", ""), str):
            refuse("This AG-UI adapter supports text messages only.")


def input_value(value: Any, chat: str) -> dict[str, Any]:
    validate(value, "RunAgentInput")
    assert isinstance(value, dict)
    if value["threadId"] != chat or not ID.fullmatch(value["runId"]):
        refuse("AG-UI threadId must match the chat and runId must be a bounded identifier.")
    if value.get("protocolVersion", VERSION) != VERSION:
        refuse("Unsupported AG-UI protocol version.")
    if value.get("tools") or value.get("context") or value.get("forwardedProps"):
        refuse("Client tools, context and forwarded properties are not supported.")
    messages(value["messages"])
    return value


def response_schema(schema: Any, payload: Any) -> None:
    """Interrupt response schemas are data, with no remote reference resolver."""

    def local(node: Any) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                if key in {
                    "$id",
                    "$ref",
                    "$dynamicRef",
                    "$recursiveRef",
                    "pattern",
                    "patternProperties",
                }:
                    refuse(
                        "Interrupt response schemas cannot use references or regular expressions."
                    )
                local(val)
        elif isinstance(node, list):
            for val in node:
                local(val)

    encoded(schema, 4 * 1024)
    encoded(payload, 16 * 1024)
    local(schema)
    try:
        Draft202012Validator.check_schema(schema)
        valid = Draft202012Validator(schema, registry=Registry()).is_valid(payload)
    except Exception as exc:
        raise InteractionError("Invalid interrupt response schema.", 422) from exc
    if not valid:
        refuse("The resume payload does not satisfy the interrupt response schema.")


def pointer(value: str) -> list[str]:
    if value == "":
        return []
    if not value.startswith("/") or re.search(r"~(?![01])", value):
        refuse("Invalid JSON Pointer in state patch.")
    return [part.replace("~1", "/").replace("~0", "~") for part in value[1:].split("/")]


def index(part: str, length: int, *, add: bool = False) -> int:
    if part == "-" and add:
        return length
    if not re.fullmatch(r"0|[1-9][0-9]*", part) or len(part) > 9:
        refuse("Invalid array index in state patch.")
    n = int(part)
    if n >= length + int(add):
        refuse("State patch array index is out of bounds.")
    return n


def at(root: Any, parts: list[str]) -> Any:
    node = root
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list):
            node = node[index(part, len(node))]
        else:
            refuse("State patch path does not exist.")
    return node


def write(root: Any, parts: list[str], value: Any, op: str) -> Any:
    if not parts:
        if op == "remove":
            refuse("Removing the entire state document is unsupported; replace it with null.")
        return value
    parent, key = at(root, parts[:-1]), parts[-1]
    if isinstance(parent, dict):
        if op != "add" and key not in parent:
            refuse("State patch path does not exist.")
        if op == "remove":
            del parent[key]
        else:
            parent[key] = value
    elif isinstance(parent, list):
        n = index(key, len(parent), add=op == "add")
        if op == "add":
            parent.insert(n, value)
        elif op == "remove":
            parent.pop(n)
        else:
            parent[n] = value
    else:
        refuse("State patch parent is not an object or array.")
    return root


def patch(state: Any, operations: list[dict[str, Any]]) -> Any:
    if len(operations) > 64:
        refuse("At most 64 state patch operations are supported.")
    result = copy.deepcopy(state)
    for item in operations:
        op, dest = item["op"], pointer(item["path"])
        if op in {"move", "copy"}:
            source = pointer(item["from"])
            value = copy.deepcopy(at(result, source))
            if op == "move":
                if dest == source:
                    continue
                if dest[: len(source)] == source:
                    refuse("A state patch cannot move a value into itself.")
                result = write(result, source, None, "remove")
            result = write(result, dest, value, "add")
        elif op == "test":
            if not same(at(result, dest), item["value"]):
                refuse("State patch test failed.")
        elif op in {"add", "remove", "replace"}:
            result = write(result, dest, copy.deepcopy(item.get("value")), op)
        else:
            refuse("Unsupported state patch operation.")
        encoded(result, FRAME)
    return result


class Projection:
    def __init__(self, thread: str, run: str, history: list[dict[str, Any]], state: Any) -> None:
        self.thread, self.run = thread, run
        self.messages = copy.deepcopy(history)
        self.state = copy.deepcopy(state)
        self.started = False
        self.terminal = False
        self.outcome: dict[str, Any] | None = None
        self.open_messages: set[str] = set()
        self.calls: dict[str, dict[str, Any]] = {}
        self.open_calls: set[str] = set()
        self.results: set[str] = set()
        self.steps: set[str] = set()
        self.reindex()

    def reindex(self) -> None:
        self.calls, self.results = {}, set()
        for message in self.messages:
            for call in message.get("toolCalls", []):
                if call["id"] in self.calls:
                    refuse("Duplicate AG-UI tool call ID in a message snapshot.")
                self.calls[call["id"]] = call
            if message["role"] == "tool":
                cid = message["toolCallId"]
                if cid in self.results:
                    refuse("Duplicate AG-UI tool result in a message snapshot.")
                self.results.add(cid)

    def snapshot(self) -> dict[str, Any]:
        result = {"messages": self.messages, "state": self.state, "outcome": self.outcome}
        encoded(result, PROJECTION)
        return result

    def message(self, message_id: str) -> dict[str, Any]:
        found = next((m for m in self.messages if m["id"] == message_id), None)
        if found is None:
            refuse("AG-UI event refers to an unknown message.")
        assert found is not None
        return found

    def apply(self, event: dict[str, Any]) -> None:
        validate(event, "Event")
        kind = event["type"]
        if kind not in EVENTS or event.get("subagentRunId"):
            refuse("Unsupported AG-UI event type or subagent stream.")
        if self.terminal:
            refuse("AG-UI event followed a terminal event.")
        if not self.started and kind not in {"RUN_STARTED", "RUN_ERROR"}:
            refuse("AG-UI stream did not begin with RUN_STARTED or RUN_ERROR.")
        if kind in {"RUN_STARTED", "RUN_FINISHED"} and (
            event["threadId"] != self.thread or event["runId"] != self.run
        ):
            refuse("AG-UI run/thread identity changed within the stream.")
        if kind == "RUN_STARTED":
            if self.started or event.get("protocolVersion", VERSION) != VERSION:
                refuse("Nested run or unsupported AG-UI protocol version.")
            self.started = True
        elif kind in TERMINAL:
            if kind == "RUN_FINISHED":
                if self.open_messages or self.open_calls or self.steps:
                    refuse("AG-UI run finished with an open message, tool or step.")
                self.outcome = event.get("outcome", {"type": "success"})
                if self.outcome["type"] == "interrupt":
                    ints = self.outcome["interrupts"]
                    if len(ints) > 16 or len({i["id"] for i in ints}) != len(ints):
                        refuse("Invalid or oversized AG-UI interrupt set.")
            self.terminal = True
        elif kind == "TEXT_MESSAGE_START":
            mid = event["messageId"]
            if mid in self.open_messages or event.get("role", "assistant") != "assistant":
                refuse("Only non-overlapping assistant text messages are supported.")
            if not any(m["id"] == mid for m in self.messages):
                self.messages.append({"id": mid, "role": "assistant", "content": ""})
            elif self.message(mid)["role"] != "assistant":
                refuse("AG-UI message role changed.")
            self.open_messages.add(mid)
        elif kind in {"TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"}:
            mid = event["messageId"]
            if mid not in self.open_messages:
                refuse("AG-UI text event has no open message.")
            if kind == "TEXT_MESSAGE_CONTENT":
                m = self.message(mid)
                m["content"] = m.get("content", "") + event["delta"]
            else:
                self.open_messages.remove(mid)
        elif kind == "TOOL_CALL_START":
            cid = event["toolCallId"]
            if cid in self.calls:
                refuse("Duplicate AG-UI tool call ID.")
            mid = event.get("parentMessageId")
            if mid is None:
                mid = "tool-parent-" + cid
                if any(m["id"] == mid for m in self.messages):
                    refuse("AG-UI implicit tool parent collides with a message.")
                self.messages.append({"id": mid, "role": "assistant", "content": ""})
            parent = self.message(mid)
            if parent["role"] != "assistant":
                refuse("AG-UI tool parent is not an assistant message.")
            call = {
                "id": cid,
                "type": "function",
                "function": {"name": event["toolCallName"], "arguments": ""},
            }
            parent.setdefault("toolCalls", []).append(call)
            self.calls[cid] = call
            self.open_calls.add(cid)
        elif kind in {"TOOL_CALL_ARGS", "TOOL_CALL_END"}:
            cid = event["toolCallId"]
            if cid not in self.open_calls:
                refuse("AG-UI tool event has no open call.")
            if kind == "TOOL_CALL_ARGS":
                self.calls[cid]["function"]["arguments"] += event["delta"]
            else:
                self.open_calls.remove(cid)
        elif kind == "TOOL_CALL_RESULT":
            cid, mid = event["toolCallId"], event["messageId"]
            if cid not in self.calls or cid in self.open_calls or cid in self.results:
                refuse("AG-UI result has no completed tool call, or repeats a result.")
            if any(m["id"] == mid for m in self.messages) or not isinstance(event["content"], str):
                refuse("AG-UI tool result requires a new message ID and text content.")
            self.messages.append(
                {"id": mid, "role": "tool", "toolCallId": cid, "content": event["content"]}
            )
            self.results.add(cid)
        elif kind == "MESSAGES_SNAPSHOT":
            if self.open_messages or self.open_calls:
                refuse("AG-UI messages snapshot cannot replace an open stream.")
            messages(event["messages"])
            self.messages = copy.deepcopy(event["messages"])
            self.reindex()
        elif kind == "STATE_SNAPSHOT":
            encoded(event["snapshot"], FRAME)
            self.state = copy.deepcopy(event["snapshot"])
        elif kind == "STATE_DELTA":
            self.state = patch(self.state, event["delta"])
        elif kind == "STEP_STARTED":
            if event["stepName"] in self.steps:
                refuse("Duplicate open AG-UI step.")
            self.steps.add(event["stepName"])
        elif kind == "STEP_FINISHED":
            if event["stepName"] not in self.steps:
                refuse("AG-UI step ended without a start.")
            self.steps.remove(event["stepName"])
        messages(self.messages)
        self.snapshot()


class SSE:
    """Incremental bytes, with CR/LF, UTF-8 and multi-line data boundaries."""

    def __init__(self) -> None:
        self.pending = b""
        self.data: list[str] = []
        self.size = 0
        self.first_line = True

    def feed(self, chunk: bytes, *, final: bool = False) -> list[dict[str, Any]]:
        self.pending += chunk
        result = []
        while self.pending:
            match = re.search(b"[\r\n]", self.pending)
            if match is None:
                break
            end = match.start()
            if self.pending[end:] == b"\r" and not final:
                break
            used = end + (2 if self.pending[end : end + 2] == b"\r\n" else 1)
            raw, self.pending = self.pending[:end], self.pending[used:]
            self.size += used
            if self.size > FRAME:
                refuse("AG-UI SSE frame is too large.")
            try:
                line = raw.decode("utf-8")
                if self.first_line:
                    line = line.removeprefix("\ufeff")
                    self.first_line = False
                if not line:
                    if self.data:
                        value = json.loads("\n".join(self.data))
                        if not isinstance(value, dict):
                            refuse("AG-UI SSE data must be an event object.")
                        result.append(value)
                    self.data, self.size = [], 0
                elif line.startswith("data:"):
                    self.data.append(line[5:].removeprefix(" "))
                elif line == "data":
                    self.data.append("")
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise InteractionError("Invalid JSON or UTF-8 in AG-UI SSE data.", 422) from exc
        if self.size + len(self.pending) > FRAME:
            refuse("AG-UI SSE frame is too large.")
        if final and (self.pending or self.data):
            refuse("AG-UI SSE stream ended in an incomplete frame.")
        return result


def capability(*, renderer: bool = False) -> dict[str, Any]:
    return {
        "protocol": "ag-ui",
        "version": VERSION,
        "sourceRevision": REVISION,
        "directions": ["serve", "connect"],
        "renderer": renderer,
        "events": list(EVENTS),
        "methods": ["run", "events", "snapshot", "stop", "resume"],
    }
