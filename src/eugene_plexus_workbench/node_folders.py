"""Node folders through Eugene, bound to the session that started this answer."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any

import httpx

from .folder_io import FolderError, WriteUncertain
from .signin import Provider, SignInUnavailable
from .store import Person, Store

PREFIX = "node:"


class NodeFolders:
    def __init__(self, store: Store, provider: Provider, http: httpx.AsyncClient) -> None:
        self.store, self.provider, self.http = store, provider, http

    async def request(
        self, person: Person | None, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        row = await self.store.session(person.session_id) if person and person.session_id else None
        if (
            row is None
            or person is None
            or row.sub != person.sub
            or not row.refresh_token
            or row.expires_at <= time.time()
        ):
            raise FolderError(
                "This Workbench sign-in ended. Sign in again before using node folders."
            )
        if self.provider.why_not():
            raise FolderError("Workbench is not connected to Eugene's sign-in service.")
        try:
            response = await self.http.post(
                self.provider.transport_url(f"{self.provider.issuer}/node-helpers/{path}"),
                json={**body, "refreshToken": row.refresh_token},
                auth=self.provider._auth(),
                timeout=28.0,
                follow_redirects=False,
            )
        except asyncio.CancelledError:
            if path == "execute" and body.get("operationId"):
                with contextlib.suppress(
                    httpx.HTTPError, SignInUnavailable, asyncio.CancelledError
                ):
                    await self.http.post(
                        self.provider.transport_url(f"{self.provider.issuer}/node-helpers/cancel"),
                        json={
                            "refreshToken": row.refresh_token,
                            "operationId": body["operationId"],
                        },
                        auth=self.provider._auth(),
                        timeout=3.0,
                        follow_redirects=False,
                    )
            raise
        except (httpx.HTTPError, SignInUnavailable):
            if body.get("tool") == "write_text":
                raise WriteUncertain(
                    "Eugene did not confirm this write. Check the file before trying again."
                ) from None
            raise FolderError(
                "Node folders could not reach Eugene. Check its connection."
            ) from None
        if response.status_code != 200:
            if body.get("tool") == "write_text" and response.status_code >= 500:
                raise WriteUncertain(
                    "Eugene could not confirm this write. Check the file before trying again."
                )
            try:
                detail = response.json()
                message = detail.get("detail") or detail.get("message")
                if isinstance(message, dict):
                    message = message.get("detail") or message.get("message")
            except (ValueError, AttributeError):
                message = None
            if response.status_code == 404:
                message = "This Eugene version does not support node folders yet. Update Eugene."
            raise FolderError(
                str(message or f"Eugene could not use node folders (HTTP {response.status_code}).")
            )
        try:
            if len(response.content) > 100_000:
                raise ValueError
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError
            if path == "execute" and (
                value.get("status") not in {"done", "failed", "uncertain"}
                or (value["status"] == "done" and not isinstance(value.get("result"), dict))
            ):
                raise ValueError
            if path == "folders" and (
                not isinstance(value.get("grants"), list)
                or any(
                    not isinstance(g, dict) or not isinstance(g.get("id"), str)
                    for g in value["grants"]
                )
            ):
                raise ValueError
        except (ValueError, TypeError):
            error = WriteUncertain if body.get("tool") == "write_text" else FolderError
            raise error(
                "Eugene returned an invalid file result. Check the file before retrying."
            ) from None
        return value

    async def listing(self, person: Person | None) -> list[dict[str, Any]]:
        data = await self.request(person, "folders", {})
        return [{**g, "id": PREFIX + g["id"], "source": "node"} for g in data["grants"]]

    async def execute(
        self, ident: str, person: Person | None, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if not ident.startswith(PREFIX):
            raise FolderError("This is not a node folder.")
        answer = await self.request(
            person,
            "execute",
            {
                "folderId": ident[len(PREFIX) :],
                "tool": tool,
                "arguments": arguments,
                "operationId": uuid.uuid4().hex,
            },
        )
        if answer.get("status") == "uncertain":
            raise WriteUncertain(
                answer.get("message") or "Check this file before retrying the write."
            )
        if answer.get("status") != "done":
            raise FolderError(
                answer.get("message") or "The node could not complete this file operation."
            )
        result: dict[str, Any] = answer["result"]
        return result
