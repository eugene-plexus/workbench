"""Owner-managed folder grants; recipients opt in per chat."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .api import _new_id, _person, _problem, _state
from .folder_io import FolderError
from .folders import public, visible

router = APIRouter()


class GrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    path: str = Field(min_length=1, max_length=4096)
    subject: str = Field(min_length=1, max_length=256)
    writable: StrictBool = False

    @field_validator("name", "path", "subject")
    @classmethod
    def clean(cls, value: str) -> str:
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Use a nonempty value without control characters.")
        return value.strip()


@router.get("/api/folders")
async def listing(request: Request) -> dict[str, Any]:
    person = await _person(request)
    folders = _state(request).tools.folders
    grants = await folders.store.folder_grants()
    reason = folders.unavailable()
    local_grants = [
        dict(public(g, person), source="local", available=reason is None, reason=reason)
        for g in grants
        if person.is_owner or visible(g, person)
    ]
    remote = _state(request).tools.node_folders
    node_grants: list[dict[str, Any]] = []
    node_reason = None
    if remote is not None and person.session_id:
        try:
            node_grants = await remote.listing(person)
        except FolderError as exc:
            node_reason = str(exc)
    return {
        "grants": [*local_grants, *node_grants],
        "available": reason is None or bool(node_grants),
        "localAvailable": reason is None,
        "reason": reason,
        "nodeReason": node_reason,
        "manageUrl": (
            _state(request).provider.issuer.removesuffix("/oidc") + "/people#node-files"
            if person.is_owner and _state(request).provider.issuer
            else None
        ),
    }


@router.get("/api/folders/people")
async def recipients(request: Request) -> dict[str, Any]:
    if not (await _person(request)).is_owner:
        raise _problem(403, "Only the owner can assign folder access.")
    return {
        "people": [
            {"sub": p.sub, "name": p.name, "username": p.username}
            for p, *_ in await _state(request).store.people()
        ]
    }


@router.post("/api/folders", status_code=201)
async def add(request: Request, body: GrantCreate) -> dict[str, Any]:
    person = await _person(request)
    if not person.is_owner:
        raise _problem(403, "Only the owner can grant folder access.")
    try:
        grant = await _state(request).tools.folders.add({"id": _new_id(), **body.model_dump()})
    except FolderError as exc:
        if isinstance(exc.cause, PermissionError):
            raise HTTPException(
                400,
                detail={
                    "message": str(exc),
                    "giveAccessUrl": _state(request).tools.folders.give_access_url(
                        body.path, body.writable
                    ),
                },
            ) from None
        raise _problem(400, str(exc)) from None
    return public(grant, person)


@router.delete("/api/folders/{grant_id}", status_code=204)
async def remove(request: Request, grant_id: str) -> Response:
    if not (await _person(request)).is_owner:
        raise _problem(403, "Only the owner can remove folder access.")
    warning = await _state(request).tools.folders.remove(grant_id)
    # The grant is gone: what was read from the folder leaves the person's documents before this
    # returns (`knowledge/folder_sync.py`).
    await _state(request).folder_sync.grant_withdrawn(grant_id)
    if warning:
        return JSONResponse(
            {
                "warning": warning,
                "giveAccessUrl": _state(request).tools.folders.give_access_url(""),
            },
            status_code=200,
        )
    return Response(status_code=204)
