"""Authenticated host API for durable, reviewed browser interactions, version 1."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import a2ui_state, agui_protocol, mcp_ui
from .a2ui import A2UI
from .api import _own_chat, _person, _readable_chat, _state, hidden_from_owner, install_mode
from .interaction_store import InteractionError, encoded
from .interactions import Interactions
from .mcp_apps import McpApps

router = APIRouter(prefix="/api/v1")


class BodyLimit:
    """Bound host action requests before FastAPI parses JSON or validates models."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        agui = scope["type"] == "http" and "ag-ui" in scope.get("path", "").split("/")
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].startswith("/api/v1/")
            or ("/interactions/" not in scope["path"] and not agui)
        ):
            await self.app(scope, receive, send)
            return
        data = bytearray()
        try:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                data.extend(message.get("body", b""))
                if len(data) > (512 * 1024 if agui else 24 * 1024):
                    raise InteractionError("The action request is too large.", 413)
                if not message.get("more_body", False):
                    break
            if data:
                encoded(json.loads(data), 512 * 1024 if agui else 20 * 1024)
        except (ValueError, RecursionError, UnicodeError, InteractionError) as exc:
            code = exc.status if isinstance(exc, InteractionError) else 422
            response = JSONResponse(
                {"detail": {"message": "Invalid or oversized interaction request."}},
                status_code=code,
            )
            await response(scope, receive, send)
            return

        delivered = False

        async def limited() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(data), "more_body": False}
            return await receive()

        await self.app(scope, limited, send)


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    approve: bool


class AppRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=1)
    method: str = Field(max_length=80)
    params: dict[str, Any]


class FormInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=1)
    componentId: str = Field(min_length=1, max_length=80)
    value: str | bool


async def problem(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, InteractionError)
    return JSONResponse(status_code=exc.status, content={"detail": {"message": exc.message}})


def service(request: Request) -> Interactions:
    result: Interactions = _state(request).interactions
    return result


@router.get("/interactions/capabilities")
async def capabilities(request: Request) -> dict[str, Any]:
    await _person(request)
    renderer = bool(getattr(_state(request), "ui_renderers", False))
    return {
        "version": 1,
        "supportedProtocols": [
            {"protocol": protocol, "version": version}
            for protocol, version in sorted(service(request).adapters)
            if renderer or protocol not in {mcp_ui.PROTOCOL, a2ui_state.PROTOCOL}
        ]
        + ([{"protocol": "ag-ui", "version": agui_protocol.VERSION}] if renderer else []),
        "backendProtocols": (
            [
                {
                    "protocol": mcp_ui.PROTOCOL,
                    "version": mcp_ui.VERSION,
                    "renderer": renderer,
                    "rendererProfile": "opaque-offline",
                    "browserConformance": "constrained",
                    "limits": ["no-network", "no-devices", "opaque-proxy-origin"],
                    "methods": [*mcp_ui.READS, *mcp_ui.ACTIONS],
                }
            ]
            if (mcp_ui.PROTOCOL, mcp_ui.VERSION) in service(request).adapters
            else []
        )
        + (
            [
                {
                    "protocol": a2ui_state.PROTOCOL,
                    "version": a2ui_state.VERSION,
                    "renderer": renderer,
                    "catalogIds": [a2ui_state.CATALOG],
                    "methods": ["a2ui/input", "a2ui/action"],
                }
            ]
            if (a2ui_state.PROTOCOL, a2ui_state.VERSION) in service(request).adapters
            else []
        )
        + [agui_protocol.capability(renderer=renderer)],
    }


@router.get("/chats/{chat_id}/interactions")
async def snapshot(request: Request, chat_id: str, via: str | None = None) -> dict[str, Any]:
    person = await _person(request)
    chat, read_only = await _readable_chat(request, person, chat_id)
    store = service(request).store
    mode = (await install_mode(request))["mode"] if read_only else None
    return await store.interactions.snapshot(
        chat.id,
        via,
        read_only,
        lambda tree: set(hidden_from_owner(tree, mode)) if mode is not None else set(),
        set(service(request).adapters),
    )


@router.get("/chats/{chat_id}/interactions/events")
async def events(request: Request, chat_id: str, via: str | None = None) -> StreamingResponse:
    first = await snapshot(request, chat_id, via)

    async def stream() -> AsyncIterator[str]:
        current = first
        while True:
            # Also compare contents: owner visibility and adapter availability can
            # change without a write to this chat's interaction cursor.
            payload = json.dumps(current, ensure_ascii=False)
            yield f"id: {current['cursor']}\nevent: snapshot\ndata: {payload}\n\n"
            while True:
                await asyncio.sleep(1)
                if await request.is_disconnected():
                    return
                try:
                    following = await snapshot(request, chat_id, via)
                except (HTTPException, InteractionError):
                    return
                if following != current:
                    current = following
                    break
                yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/chats/{chat_id}/interactions/surfaces/{surface_id}/actions")
async def propose(
    request: Request, chat_id: str, surface_id: str, body: Proposal
) -> dict[str, Any]:
    person = await _person(request)
    await _own_chat(request, person, chat_id)
    return await service(request).propose(person, chat_id, surface_id, body.model_dump())


@router.post("/chats/{chat_id}/interactions/surfaces/{surface_id}/mcp-apps/read")
async def app_read(request: Request, chat_id: str, surface_id: str, body: AppRead) -> JSONResponse:
    person = await _person(request)
    await _own_chat(request, person, chat_id)
    adapter = service(request).adapters.get((mcp_ui.PROTOCOL, mcp_ui.VERSION))
    if not isinstance(adapter, McpApps):
        raise InteractionError("MCP Apps backend is unavailable.", 503)
    result = await adapter.read(
        person, chat_id, surface_id, body.revision, body.method, body.params
    )
    return JSONResponse(
        result,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        },
    )


@router.post("/chats/{chat_id}/interactions/actions/{action_id}/decision")
async def decide(request: Request, chat_id: str, action_id: str, body: Decision) -> dict[str, Any]:
    person = await _person(request)
    await _own_chat(request, person, chat_id)
    return await service(request).decide(person, chat_id, action_id, body.approve)


@router.post("/chats/{chat_id}/interactions/surfaces/{surface_id}/a2ui/input")
async def form_input(
    request: Request, chat_id: str, surface_id: str, body: FormInput
) -> dict[str, int]:
    person = await _person(request)
    await _own_chat(request, person, chat_id)
    adapter = service(request).adapters.get((a2ui_state.PROTOCOL, a2ui_state.VERSION))
    if not isinstance(adapter, A2UI):
        raise InteractionError("A2UI backend is unavailable.", 503)
    return {
        "revision": await adapter.input(
            person, chat_id, surface_id, body.revision, body.componentId, body.value
        )
    }
