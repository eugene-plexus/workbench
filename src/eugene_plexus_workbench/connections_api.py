"""The Memory page's Connections (MR9b): make one, see yours, revoke one.

Each is the signed-in person's own, read with their session as every page API is
(`Sessions.person`: the cookie, the page's secret, and Workbench's own origin for a change).
The token is shown once, in the answer that makes it, and never again: what is kept is its
hash (`connections.py`). No route here names another person, and the owner's sessions are
treated as anyone's: the owner sees only their own connections.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import config, connections
from .api import _person, _problem, _state
from .store import ConnectionRecord, Store

log = logging.getLogger(__name__)

router = APIRouter()


class Rights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: StrictBool = False
    documents: StrictBool = False
    suggest: StrictBool = False


class NewConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=connections.MAX_NAME)
    rights: Rights
    days: int = connections.EXPIRY_DAYS[0]


def connection_view(record: ConnectionRecord, now: float) -> dict[str, Any]:
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    return {
        "id": record.id,
        "name": record.name,
        "rights": {
            "notes": record.read_notes,
            "documents": record.search_documents,
            "suggest": record.suggest_notes,
        },
        "createdAt": record.created_at,
        "expiresAt": record.expires_at,
        "expired": now >= record.expires_at,
        "lastUsedAt": record.last_used_at,
        "callsToday": record.calls_today if record.calls_day == today else 0,
    }


async def _settings(store: Store) -> dict[str, Any]:
    memory = await config.memory_settings(store)
    return {
        "enabled": await config.connections_enabled(store),
        "suggestionsAllowed": memory.enabled and memory.suggestions != "off",
        "notesAllowed": memory.enabled,
        "documentsAllowed": (await config.document_settings(store)).enabled,
    }


@router.get("/api/connections")
async def list_connections(request: Request) -> dict[str, Any]:
    """The person's connections, what the install allows, and the address apps use."""
    person = await _person(request)
    store: Store = _state(request).store
    now = time.time()
    return {
        **await _settings(store),
        "address": connections.mcp_address(request),
        "limit": connections.MAX_CONNECTIONS,
        "days": list(connections.EXPIRY_DAYS),
        "connections": [connection_view(c, now) for c in await store.connections(person.sub)],
    }


@router.post("/api/connections", status_code=status.HTTP_201_CREATED)
async def make_connection(request: Request, body: NewConnection) -> dict[str, Any]:
    """A new connection and its token, which this answer is the only one to hold."""
    person = await _person(request)
    store: Store = _state(request).store
    allowed = await _settings(store)
    if not allowed["enabled"]:
        raise _problem(status.HTTP_403_FORBIDDEN, connections.TURNED_OFF)
    name = " ".join(body.name.split())
    if not name:
        raise _problem(status.HTTP_422_UNPROCESSABLE_ENTITY, "Give the connection a name.")
    if body.days not in connections.EXPIRY_DAYS:
        raise _problem(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A connection lasts 30, 90 or 365 days.",
        )
    rights = body.rights
    if not (rights.notes or rights.documents or rights.suggest):
        raise _problem(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose at least one thing it may do.")
    if rights.suggest and not allowed["suggestionsAllowed"]:
        raise _problem(
            status.HTTP_403_FORBIDDEN,
            "The owner of this Workbench has turned suggested notes off.",
        )
    now = time.time()
    token = connections.new_token()
    record = ConnectionRecord(
        id=secrets.token_urlsafe(9),
        owner=person.sub,
        name=name,
        read_notes=rights.notes,
        search_documents=rights.documents,
        suggest_notes=rights.suggest,
        created_at=now,
        expires_at=now + body.days * connections.DAY,
    )
    kept = await store.add_connection(
        record, connections.token_hash(token), limit=connections.MAX_CONNECTIONS
    )
    if kept is None:
        raise _problem(
            status.HTTP_409_CONFLICT,
            f"You have {connections.MAX_CONNECTIONS} connections. Revoke one to make another.",
        )
    log.info("a connection was made (%s)", record.id)
    address = connections.mcp_address(request)
    return {
        "connection": connection_view(record, now),
        "token": token,
        "address": address,
        "setups": connections.client_setups(address, token),
    }


@router.delete("/api/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_connection(request: Request, connection_id: str) -> Response:
    """Revoke: the next call with its token is refused, and its unkept suggestions go."""
    person = await _person(request)
    store: Store = _state(request).store
    if not await store.delete_connection(person.sub, connection_id):
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such connection.")
    log.info("a connection was revoked (%s)", connection_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
