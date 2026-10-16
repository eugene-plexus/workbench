"""Authenticated AG-UI runs, configuration and installed renderer capabilities."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any, Self
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import agui_protocol as protocol
from .agui import AGUI
from .agui_store import public, public_agent
from .api import _new_id, _own_chat, _person, _state
from .interaction_store import InteractionError
from .sessions import SignedOut
from .store import Person

router = APIRouter(prefix="/api/v1")


def service(request: Request) -> AGUI:
    result: AGUI = _state(request).agui
    return result


class Agent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2048)
    token: str = Field(default="", max_length=4096)
    subjects: list[str] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid(self) -> Self:
        for text in [self.name, self.url, self.token, *self.subjects]:
            if any(ord(c) < 32 or ord(c) == 127 for c in text):
                raise ValueError("Control characters are not allowed.")
        parts = urlsplit(self.url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or " " in self.url
        ):
            raise ValueError("Use a fixed HTTP(S) URL without userinfo, query or fragment.")
        if len(set(self.subjects)) != len(self.subjects) or any(
            not s or len(s) > 256 for s in self.subjects
        ):
            raise ValueError("Agent access requires unique, bounded subject IDs.")
        return self


@router.get("/ag-ui/capabilities")
async def capabilities(request: Request) -> dict[str, Any]:
    await _person(request)
    return protocol.capability(renderer=bool(getattr(_state(request), "ui_renderers", False)))


@router.get("/ag-ui/agents")
async def agents(request: Request) -> list[dict[str, Any]]:
    person = await _person(request)
    return [
        public_agent(a)
        for a in await service(request).store.agui.agents()
        if person.is_owner or person.sub in a["subjects"]
    ]


@router.get("/ag-ui/people")
async def people(request: Request) -> list[dict[str, str]]:
    person = await _person(request)
    if not person.is_owner:
        raise InteractionError("Only the install owner can configure AG-UI agents.", 403)
    return [{"sub": p.sub, "name": p.name} for p, _, _ in await service(request).store.people()]


@router.post("/ag-ui/agents", status_code=201)
async def add_agent(request: Request, body: Agent) -> dict[str, Any]:
    if not (await _person(request)).is_owner:
        raise InteractionError("Only the install owner can configure AG-UI agents.", 403)
    agent = {"id": _new_id(), **body.model_dump()}
    await service(request).store.agui.add_agent(agent)
    return public_agent(agent)


@router.delete("/ag-ui/agents/{agent_id}", status_code=204)
async def remove_agent(request: Request, agent_id: str) -> Response:
    if not (await _person(request)).is_owner:
        raise InteractionError("Only the install owner can configure AG-UI agents.", 403)
    await service(request).remove_agent(agent_id)
    return Response(status_code=204)


async def owner(request: Request, chat: str) -> Person:
    person = await _person(request)
    await _own_chat(request, person, chat)
    return person


async def record(request: Request, chat: str, run: str) -> dict[str, Any]:
    await owner(request, chat)
    saved = await service(request).store.agui.get(chat, run)
    if saved is None:
        raise InteractionError("There is no such AG-UI run.", 404)
    return saved


@router.get("/chats/{chat_id}/ag-ui")
async def thread(request: Request, chat_id: str) -> dict[str, Any] | None:
    await owner(request, chat_id)
    saved = await service(request).store.agui.get(chat_id)
    return public(saved) if saved else None


@router.get("/chats/{chat_id}/ag-ui/runs/{run_id}")
async def snapshot(request: Request, chat_id: str, run_id: str) -> dict[str, Any]:
    return public(await record(request, chat_id, run_id))


def cursor(request: Request) -> int:
    raw = request.headers.get("last-event-id", "0")
    if not re.fullmatch(r"[0-9]{1,4}", raw) or int(raw) > 4096:
        raise InteractionError("Invalid AG-UI event cursor.", 422)
    return int(raw)


def stream(
    request: Request, person: Person, saved: dict[str, Any], after: int
) -> StreamingResponse:
    if after > saved["cursor"]:
        raise InteractionError("The AG-UI event cursor is ahead of the saved run.")
    host = service(request)
    chat, run = saved["threadId"], saved["runId"]

    async def events() -> AsyncIterator[str]:
        nonlocal after
        while True:
            try:
                fresh = await host.sessions.current(person.session_id, person.sub)
                await host.store.agui.authorize(chat, fresh, None)
            except (SignedOut, InteractionError):
                return
            batch = await host.store.agui.events(chat, run, after)
            for seq, event in batch:
                yield f"id: {seq}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                after = seq
            latest = await host.store.agui.get(chat, run)
            if latest is None or (latest["status"] != "running" and after >= latest["cursor"]):
                return
            if batch:
                continue
            yield ": waiting\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/chats/{chat_id}/ag-ui/runs/{run_id}/events")
async def events(request: Request, chat_id: str, run_id: str) -> StreamingResponse:
    person = await owner(request, chat_id)
    saved = await record(request, chat_id, run_id)
    return stream(request, person, saved, cursor(request))


async def start(
    request: Request, chat: str, body: dict[str, Any], agent: str | None
) -> StreamingResponse:
    person = await owner(request, chat)
    value = protocol.input_value(body, chat)
    after = cursor(request)
    existing = await service(request).store.agui.get(chat, value["runId"])
    if after > (existing["cursor"] if existing else 0):
        raise InteractionError("The AG-UI event cursor is ahead of the saved run.")
    saved = await service(request).start(request, person, chat, value, agent)
    return stream(request, person, saved, after)


@router.post("/chats/{chat_id}/ag-ui/run")
async def serve(request: Request, chat_id: str, body: dict[str, Any]) -> StreamingResponse:
    return await start(request, chat_id, body, None)


@router.post("/chats/{chat_id}/ag-ui/agents/{agent_id}/run")
async def connect(
    request: Request, chat_id: str, agent_id: str, body: dict[str, Any]
) -> StreamingResponse:
    return await start(request, chat_id, body, agent_id)


@router.post("/chats/{chat_id}/ag-ui/runs/{run_id}/stop", status_code=204)
async def stop(request: Request, chat_id: str, run_id: str) -> Response:
    saved = await record(request, chat_id, run_id)
    if saved["status"] == "running":
        await service(request).stop(chat_id, run_id=run_id)
    return Response(status_code=204)
