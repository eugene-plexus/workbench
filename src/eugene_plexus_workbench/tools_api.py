"""Workbench's authenticated tools surface; never an operator API on the hub."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, StrictBool, field_validator

from .api import _answers, _new_id, _own_chat, _person, _problem, _state
from .tools import public_server, validate_url

router = APIRouter()


class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)
    token: str = Field(default="", max_length=4096)

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return validate_url(value.strip())

    @field_validator("name", "token")
    @classmethod
    def no_controls(cls, value: str) -> str:
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Control characters are not allowed.")
        return value.strip()


class Decision(BaseModel):
    callId: str = Field(min_length=1, max_length=256)
    approve: StrictBool


async def _owner(request: Request) -> None:
    if not (await _person(request)).is_owner:
        raise _problem(403, "Only the owner can add or remove shared tool servers.")


@router.get("/api/tools/servers")
async def servers(request: Request) -> dict[str, Any]:
    await _person(request)
    return {"servers": [public_server(s) for s in await _state(request).store.tool_servers()]}


@router.post("/api/tools/servers", status_code=201)
async def add(request: Request, body: ServerCreate) -> dict[str, Any]:
    await _owner(request)
    store = _state(request).store
    if not body.name:
        raise _problem(400, "Give this server a name.")
    if len(await store.tool_servers()) >= 32:
        raise _problem(400, "This Workbench already has 32 tool servers. Remove one first.")
    server = {"id": _new_id(), **body.model_dump()}
    await store.add_tool_server(server)
    return public_server(server)


@router.delete("/api/tools/servers/{server_id}", status_code=204)
async def remove(request: Request, server_id: str) -> Response:
    await _owner(request)
    await _state(request).store.delete_tool_server(server_id)
    return Response(status_code=204)


@router.post("/api/tools/servers/{server_id}/check")
async def check(request: Request, server_id: str) -> dict[str, Any]:
    await _person(request)
    # The answer runner uses this same discovery path.
    from .answers import _tool_problem

    try:
        async with _state(request).tools.connect([server_id]) as session:
            return {
                "tools": [
                    {"name": t[2], "description": d["function"]["description"]}
                    for t, d in zip(session.tools.values(), session.definitions, strict=True)
                ]
            }
    except Exception as exc:
        raise _problem(502, _tool_problem(exc)) from None


@router.post("/api/chats/{chat_id}/messages/{message_id}/tools/decision", status_code=204)
async def decide(request: Request, chat_id: str, message_id: str, body: Decision) -> Response:
    person = await _person(request)
    await _own_chat(request, person, chat_id)
    if not _answers(request).approve(chat_id, message_id, body.callId, body.approve):
        raise _problem(409, "This call is no longer waiting for approval. Refresh the chat.")
    return Response(status_code=204)
