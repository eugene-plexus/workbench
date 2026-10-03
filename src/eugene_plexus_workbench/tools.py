"""MCP connections and protocol translation. No hub credentials or subprocesses.

Each answer owns its MCP sessions in one task (SDK cancellation scopes must
exit in the task that entered them). HTTP pools last for that session; the
TLS context is built once per Workbench, never while dispatching a tool.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import ssl
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx2
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from referencing import Registry
from referencing.exceptions import NoSuchResource

from .store import Store

MAX_TOOLS = 64
MAX_ARGUMENTS = 16_384
MAX_RESULT = 65_536
MAX_RESPONSE = 2_097_152


class ToolError(Exception):
    """A failure safe to show without credentials or server response bodies."""


def known_problem(exc: BaseException) -> str | None:
    """Find a safe observed cause inside the SDK's task-group wrappers."""
    pending = [exc]
    seen: set[int] = set()
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        if isinstance(error, ToolError):
            return str(error)
        if isinstance(error, httpx2.TimeoutException):
            return "The MCP server timed out. Check that it is running and reachable."
        if isinstance(error, httpx2.ConnectError):
            return "Could not connect to the MCP server. Check its address and TLS certificate."
        if isinstance(error, httpx2.HTTPStatusError):
            return f"The MCP server answered HTTP {error.response.status_code}. Check the server."
        if isinstance(error, BaseExceptionGroup):
            pending.extend(error.exceptions)
        if error.__cause__ is not None:
            pending.append(error.__cause__)
        if error.__context__ is not None:
            pending.append(error.__context__)
    return None


def validate_url(value: str) -> str:
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        _ = url.port
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if (
            not host
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or any(c.isspace() for c in value)
            or "\\" in value
            or not (url.scheme == "https" or (url.scheme == "http" and loopback))
        ):
            raise ValueError
    except ValueError as exc:
        raise ValueError(
            "Use an HTTPS MCP URL (HTTP is allowed on loopback), "
            "without credentials, a query or a fragment."
        ) from exc
    return value


def public_server(server: dict[str, str]) -> dict[str, Any]:
    return {key: server[key] for key in ("id", "name", "url")} | {"hasToken": bool(server["token"])}


def _no_resource(uri: str) -> Any:
    raise NoSuchResource(ref=uri)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise ValueError("non-finite JSON number")


def transcript(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for turn in rounds:
        out.append(turn["assistant"])
        for call in turn["calls"]:
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": call["result"]
                    or "This call did not finish. Do not assume it succeeded.",
                }
            )
    return out


class Calls:
    """Accumulate interleaved streamed function calls without executing fragments."""

    def __init__(self) -> None:
        self.items: dict[int, dict[str, Any]] = {}

    def take(self, chunk: dict[str, Any]) -> None:
        choices = chunk.get("choices") or []
        for piece in ((choices[0].get("delta") or {}).get("tool_calls") or []) if choices else []:
            index = piece.get("index")
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 16:
                raise ToolError("The model sent an invalid tool-call index. Try another model.")
            item = self.items.setdefault(
                index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            )
            for dest, key, value in (
                (item, "id", piece.get("id")),
                (item["function"], "name", (piece.get("function") or {}).get("name")),
                (item["function"], "arguments", (piece.get("function") or {}).get("arguments")),
            ):
                if value is not None:
                    if not isinstance(value, str):
                        raise ToolError("The model sent a malformed tool call. Try another model.")
                    dest[key] += value
                    if len(dest[key]) > (MAX_ARGUMENTS if key == "arguments" else 256):
                        raise ToolError(
                            "The model's tool call is too large. Ask for a smaller task."
                        )

    def finish(self) -> list[dict[str, Any]]:
        calls = [self.items[i] for i in sorted(self.items)]
        ids = [c["id"] for c in calls]
        if any(not i for i in ids) or len(set(ids)) != len(ids):
            raise ToolError("The model sent missing or duplicate tool-call IDs. Try another model.")
        return calls


class Tools:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.tls = ssl.create_default_context()

    @asynccontextmanager
    async def connect(self, ids: list[str]) -> AsyncIterator[ToolSession]:
        servers = {s["id"]: s for s in await self.store.tool_servers()}
        if any(i not in servers for i in ids):
            raise ToolError(
                "A selected tool server was removed. Choose tools again in this chat's settings."
            )
        async with AsyncExitStack() as stack:
            session = ToolSession(self.store)
            for server_id in dict.fromkeys(ids):
                server = servers[server_id]
                try:
                    http = await stack.enter_async_context(
                        httpx2.AsyncClient(
                            verify=self.tls,
                            trust_env=False,
                            follow_redirects=False,
                            timeout=120,
                            headers={"Authorization": f"Bearer {server['token']}"}
                            if server["token"]
                            else {},
                            event_hooks={"response": [_check_response]},
                        )
                    )
                    client = await stack.enter_async_context(
                        Client(
                            streamable_http_client(
                                server["url"], http_client=http, max_sse_event_size=1_048_576
                            ),
                            read_timeout_seconds=30,
                            cache=None,
                        )
                    )
                    cursor = None
                    seen: set[str] = set()
                    for _ in range(8):
                        page = await client.list_tools(cursor=cursor)
                        for tool in page.tools:
                            schema = tool.input_schema
                            Draft202012Validator.check_schema(schema)
                            if schema.get("type") != "object" or len(json.dumps(schema)) > 32_768:
                                raise ToolError(
                                    "A tool has an unsupported input schema. "
                                    "Ask the server owner to check it."
                                )
                            name = (
                                "mcp_"
                                + hashlib.sha256(f"{server_id}:{tool.name}".encode()).hexdigest()[
                                    :48
                                ]
                            )
                            if name in session.tools or len(session.tools) >= MAX_TOOLS:
                                raise ToolError(
                                    "The selected servers list too many or duplicate tools. "
                                    "Select fewer servers."
                                )
                            session.tools[name] = (server, client, tool.name, schema)
                            description = f"{server['name']}: {tool.name}. {tool.description or ''}"
                            session.definitions.append(
                                {
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "description": description[:4000],
                                        "parameters": schema,
                                    },
                                }
                            )
                        cursor = page.next_cursor
                        if cursor is None:
                            break
                        if cursor in seen:
                            raise ToolError(
                                "The tool server repeated a listing page. "
                                "Ask its owner to check it."
                            )
                        seen.add(cursor)
                    else:
                        raise ToolError(
                            "The tool listing has too many pages. Select a smaller server."
                        )
                except ToolError:
                    raise
                except Exception as exc:
                    if reason := known_problem(exc):
                        raise ToolError(f"{server['name']}: {reason}") from None
                    raise ToolError(
                        f"Could not list tools from {server['name']} ({type(exc).__name__}). "
                        "Check its address, credential and availability in Tools."
                    ) from None
            yield session


async def _check_response(response: httpx2.Response) -> None:
    if response.is_redirect:
        raise ToolError("The MCP address redirected. Enter its final address in Tools.")
    if response.status_code in (401, 403):
        raise ToolError("The MCP server refused this credential. Replace the connection in Tools.")
    if "text/event-stream" not in response.headers.get("content-type", ""):
        # The SDK bounds each SSE frame. Bound JSON before decoding as well.
        response.stream = _LimitedBody(response.stream)


class _LimitedBody(httpx2.AsyncByteStream):
    def __init__(self, stream: Any) -> None:
        self.stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for part in self.stream:
            size += len(part)
            if size > MAX_RESPONSE:
                raise ToolError("The MCP response exceeded 2 MiB. Ask for a smaller result.")
            yield part

    async def aclose(self) -> None:
        await self.stream.aclose()


class ToolSession:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.definitions: list[dict[str, Any]] = []
        self.tools: dict[str, tuple[dict[str, str], Any, str, dict[str, Any]]] = {}

    def prepare(self, call: dict[str, Any]) -> dict[str, Any]:
        name = call["function"]["name"]
        if name not in self.tools:
            raise ToolError("The model asked for a tool that was not offered. Try another model.")
        server, _, tool, schema = self.tools[name]
        try:
            arguments = json.loads(
                call["function"]["arguments"], object_pairs_hook=_object, parse_constant=_constant
            )
            Draft202012Validator(schema, registry=Registry(retrieve=_no_resource)).validate(
                arguments
            )
            if not isinstance(arguments, dict):
                raise ValueError
        except Exception:
            raise ToolError(
                f"The model sent invalid arguments for {tool}. No tool ran. Try another model."
            ) from None
        return {
            "id": call["id"],
            "name": name,
            "serverId": server["id"],
            "serverName": server["name"],
            "tool": tool,
            "arguments": arguments,
            "status": "pending",
            "result": None,
        }

    async def execute(self, call: dict[str, Any]) -> None:
        if call["serverId"] not in {s["id"] for s in await self.store.tool_servers()}:
            call.update(status="cancelled", result="The server was removed. This call did not run.")
            return
        _, client, tool, _ = self.tools[call["name"]]
        try:
            # Session API sends exactly once: no elicitation or header-mismatch retry.
            async with asyncio.timeout(120):
                result = await client.session.call_tool(
                    tool, call["arguments"], read_timeout_seconds=120
                )
            text = "\n".join(
                block.text if block.type == "text" else f"[Unsupported {block.type} result]"
                for block in result.content
            )
            if result.structured_content is not None:
                text += "\n" + json.dumps(result.structured_content, ensure_ascii=False)
            if len(text) > MAX_RESULT:
                text = text[:MAX_RESULT] + "\n[Tool result truncated at 65536 characters.]"
            call.update(
                status="failed" if result.is_error else "done",
                result=text or "The tool returned no content.",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = known_problem(exc) or f"The connection failed ({type(exc).__name__})."
            call.update(
                status="uncertain",
                result=f"{reason} The tool call did not return a result. "
                "It may have acted. Check the server before trying again.",
            )
