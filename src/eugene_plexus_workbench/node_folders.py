"""Machines' tools through Eugene, bound to the session that started this answer.

Each machine runs a site host: one file server (`files`), whose tools take a
`folder` argument, and on a job site the local servers its administrator
added (`specs/docs/design/remote-nodes.md` §3.4, J6, J6g). Workbench asks
Eugene which servers this person may use (`/oidc/sites/servers`) and sends
each one MCP request of the 2026-07-28 revision (`/oidc/sites/mcp`); the
machine's own policy decides, and on a job site it is final (J8).

A chat selects folders by id (`node:<folder id>`, unique in the install) and
site servers by id (`site:<node>:<server>`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from typing import Any

import httpx

from .folder_io import FolderError, WriteUncertain
from .signin import Provider, SignInUnavailable
from .store import Person, Store

PREFIX = "node:"
SITE_PREFIX = "site:"
PROTOCOL = "2026-07-28"
FILES = "files"

MODE_SECONDS = 10.0
PRODUCTION = "production"
MAX_ANSWER = 100_000


def rpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """One MCP request of the 2026-07-28 revision: whole, with no session."""
    return {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": method,
        "params": {
            **(params or {}),
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL,
                "io.modelcontextprotocol/clientInfo": {"name": "Workbench", "version": "1"},
            },
        },
    }


def site_server_id(node: str, server: str) -> str:
    return f"{SITE_PREFIX}{node}:{server}"


def parse_site_server_id(ident: str) -> tuple[str, str]:
    if not ident.startswith(SITE_PREFIX) or ident.count(":") < 2:
        raise FolderError("This is not a machine's server.")
    node, _, server = ident[len(SITE_PREFIX) :].rpartition(":")
    if not node or not server:
        raise FolderError("This is not a machine's server.")
    return node, server


class NodeFolders:
    def __init__(self, store: Store, provider: Provider, http: httpx.AsyncClient) -> None:
        self.store, self.provider, self.http = store, provider, http
        self._mode: tuple[float, dict[str, Any]] | None = None

    async def install_mode(self) -> dict[str, Any]:
        """Eugene's mode (J13, J18), for every person's mode line. Production
        when Eugene cannot be asked: the restricted answer is the safe one."""
        now = time.perf_counter()
        if self._mode is not None and now - self._mode[0] < MODE_SECONDS:
            return self._mode[1]
        value: dict[str, Any] = {"mode": PRODUCTION, "changedAt": None, "known": False}
        if not self.provider.why_not():
            try:
                response = await self.http.post(
                    self.provider.transport_url(f"{self.provider.issuer}/install-mode"),
                    auth=self.provider._auth(),
                    timeout=5.0,
                    follow_redirects=False,
                )
                body = response.json() if response.status_code == 200 else None
                if isinstance(body, dict) and body.get("mode") in ("production", "dev"):
                    value = {
                        "mode": body["mode"],
                        "changedAt": body.get("changedAt"),
                        "known": True,
                    }
            except (httpx.HTTPError, ValueError, SignInUnavailable):
                pass
        self._mode = (now, value)
        return value

    async def _refresh_token(self, person: Person | None, *, current: bool) -> str:
        row = await self.store.session(person.session_id) if person and person.session_id else None
        if (
            row is None
            or person is None
            or row.sub != person.sub
            or not row.refresh_token
            or (current and row.expires_at <= time.time())
        ):
            raise FolderError("This Workbench sign-in ended. Sign in again.")
        if self.provider.why_not():
            raise FolderError("Workbench is not connected to Eugene's sign-in service.")
        return row.refresh_token

    @staticmethod
    def _refusal(response: httpx.Response, fallback: str) -> str:
        try:
            value = response.json()
        except ValueError:
            value = None
        message = None
        if isinstance(value, dict):
            message = value.get("detail") or value.get("message")
            if isinstance(message, dict):
                message = message.get("detail") or message.get("message")
        return str(message or fallback)

    async def call(
        self, person: Person | None, path: str, body: dict[str, Any]
    ) -> dict[str, Any] | None:
        """A person's own Job Site call (`/oidc/job-sites/...`), with their sign-in.

        The answer, or None for a 204; Eugene's refusal, in its words, raised.
        """
        token = await self._refresh_token(person, current=False)
        try:
            response = await self.http.post(
                self.provider.transport_url(f"{self.provider.issuer}/{path}"),
                json={**body, "refreshToken": token},
                auth=self.provider._auth(),
                timeout=28.0,
                follow_redirects=False,
            )
        except (httpx.HTTPError, SignInUnavailable):
            raise FolderError("Workbench could not reach Eugene. Check its connection.") from None
        if response.status_code == 204:
            return None
        if response.status_code not in (200, 201):
            fallback = (
                "This Eugene version does not support job sites yet. Update Eugene."
                if response.status_code == 404
                else f"Eugene refused this (HTTP {response.status_code})."
            )
            raise FolderError(self._refusal(response, fallback))
        try:
            value = response.json()
        except ValueError:
            value = None
        if not isinstance(value, dict):
            raise FolderError("Eugene returned an answer Workbench could not read.")
        return value

    # --- what this person may use -----------------------------------------------

    async def servers(self, person: Person | None) -> list[dict[str, Any]]:
        """Every machine's server this person may use, as Eugene says."""
        token = await self._refresh_token(person, current=True)
        try:
            response = await self.http.post(
                self.provider.transport_url(f"{self.provider.issuer}/sites/servers"),
                json={"refreshToken": token},
                auth=self.provider._auth(),
                timeout=28.0,
                follow_redirects=False,
            )
        except (httpx.HTTPError, SignInUnavailable):
            raise FolderError(
                "Machines' tools could not reach Eugene. Check its connection."
            ) from None
        if response.status_code != 200:
            fallback = (
                "This Eugene version does not offer machines' tools yet. Update Eugene."
                if response.status_code == 404
                else f"Eugene could not list machines' tools (HTTP {response.status_code})."
            )
            raise FolderError(self._refusal(response, fallback))
        try:
            listed = response.json()["servers"]
            if not isinstance(listed, list) or any(
                not isinstance(s, dict)
                or not isinstance(s.get("node"), str)
                or not isinstance(s.get("server"), str)
                or not isinstance(s.get("folders"), list)
                for s in listed
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise FolderError("Eugene returned a list Workbench could not read.") from None
        return listed

    async def listing(self, person: Person | None) -> list[dict[str, Any]]:
        """Each folder on each machine this person may use, as a chat selects it."""
        out: list[dict[str, Any]] = []
        for server in await self.servers(person):
            if server["server"] != FILES:
                continue
            for folder in server["folders"]:
                out.append(
                    {
                        "id": PREFIX + str(folder["id"]),
                        "node": server["node"],
                        "name": str(folder["name"]),
                        "writable": bool(folder.get("writable")),
                        "usable": True,
                        "available": bool(server.get("available")),
                        "reason": server.get("reason"),
                        "jobSite": bool(server.get("jobSite")),
                        "source": "node",
                    }
                )
        return out

    async def local_servers(self, person: Person | None) -> list[dict[str, Any]]:
        """The local servers on job sites this person may use, as tools."""
        return [
            {
                "id": site_server_id(s["node"], s["server"]),
                "name": f"{s['node']} · {s.get('name') or s['server']}",
                "transport": "site",
                "node": s["node"],
                "server": s["server"],
                "available": bool(s.get("available")),
                "reason": s.get("reason"),
                "jobSite": bool(s.get("jobSite")),
            }
            for s in await self.servers(person)
            if s["server"] != FILES
        ]

    # --- one MCP request ----------------------------------------------------------

    async def mcp(
        self,
        person: Person | None,
        node: str,
        server: str,
        request: dict[str, Any],
        *,
        acting: bool = False,
    ) -> dict[str, Any]:
        """One MCP request to one server on one machine. The answer
        (`SiteMcpAnswer`), or a refusal raised: `WriteUncertain` when a call
        that may have acted was not confirmed."""
        token = await self._refresh_token(person, current=True)
        operation = uuid.uuid4().hex
        try:
            response = await self.http.post(
                self.provider.transport_url(f"{self.provider.issuer}/sites/mcp"),
                json={
                    "refreshToken": token,
                    "node": node,
                    "server": server,
                    "request": request,
                    "operationId": operation,
                },
                auth=self.provider._auth(),
                timeout=28.0,
                follow_redirects=False,
            )
        except asyncio.CancelledError:
            with contextlib.suppress(httpx.HTTPError, SignInUnavailable, asyncio.CancelledError):
                await self.http.post(
                    self.provider.transport_url(f"{self.provider.issuer}/sites/cancel"),
                    json={"refreshToken": token, "operationId": operation},
                    auth=self.provider._auth(),
                    timeout=3.0,
                    follow_redirects=False,
                )
            raise
        except (httpx.HTTPError, SignInUnavailable):
            if acting:
                raise WriteUncertain(
                    "Eugene did not confirm this call. It may have acted; check before trying "
                    "again."
                ) from None
            raise FolderError(
                "Machines' tools could not reach Eugene. Check its connection."
            ) from None
        if response.status_code != 200:
            if acting and response.status_code >= 500 and response.status_code != 503:
                raise WriteUncertain(
                    "Eugene could not confirm this call. It may have acted; check before trying "
                    "again."
                )
            fallback = (
                "This Eugene version does not offer machines' tools yet. Update Eugene."
                if response.status_code == 404
                else f"Eugene could not reach this machine's tools (HTTP {response.status_code})."
            )
            raise FolderError(self._refusal(response, fallback))
        try:
            if len(response.content) > MAX_ANSWER:
                raise ValueError
            value = response.json()
            if not isinstance(value, dict) or value.get("status") not in {
                "done",
                "failed",
                "uncertain",
            }:
                raise ValueError
            if value["status"] == "done" and not isinstance(value.get("response"), dict):
                raise ValueError
        except (ValueError, TypeError):
            error = WriteUncertain if acting else FolderError
            raise error(
                "Eugene returned an answer Workbench could not read. Check before trying again."
            ) from None
        return value

    async def list_tools(
        self, person: Person | None, node: str, server: str
    ) -> list[dict[str, Any]]:
        """A machine's server's tools for this person, as its own policy lists them."""
        answer = await self.mcp(person, node, server, rpc("tools/list"))
        if answer["status"] != "done":
            raise FolderError(answer.get("message") or f"{node} did not list its tools.")
        response = answer["response"]
        if response.get("error"):
            raise FolderError(str(response["error"].get("message") or "The tool listing failed."))
        tools = (response.get("result") or {}).get("tools")
        if not isinstance(tools, list) or any(
            not isinstance(t, dict) or not isinstance(t.get("name"), str) for t in tools
        ):
            raise FolderError(f"{node} answered a tool listing Workbench could not read.")
        return tools

    async def call_tool(
        self,
        person: Person | None,
        node: str,
        server: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> tuple[str, bool, dict[str, Any]]:
        """Run one tool: its result as text, whether it is an error, and what
        Eugene said about it (a job site's, and the install's mode, J13a)."""
        answer = await self.mcp(
            person,
            node,
            server,
            rpc("tools/call", {"name": tool, "arguments": arguments}),
            acting=True,
        )
        mode = answer.get("installMode") if answer.get("installMode") == "dev" else PRODUCTION
        meta = {"jobSite": bool(answer.get("jobSite")), "mode": mode}
        if answer["status"] == "uncertain":
            raise WriteUncertain(
                answer.get("message") or "This call may have acted. Check before trying again."
            )
        if answer["status"] != "done":
            raise FolderError(answer.get("message") or f"{node} did not run {tool}.")
        response = answer["response"]
        if response.get("error"):
            raise FolderError(str(response["error"].get("message") or f"{tool} failed."))
        result = response.get("result") or {}
        text = "\n".join(
            str(block.get("text", ""))
            if block.get("type") == "text"
            else f"[Unsupported {block.get('type')} result]"
            for block in result.get("content") or []
            if isinstance(block, dict)
        )
        structured = result.get("structuredContent")
        if structured is not None:
            encoded = json.dumps(structured, ensure_ascii=False)
            if text.strip() != encoded:
                text = (text + "\n" + encoded).strip()
        return text or "The tool returned no content.", bool(result.get("isError")), meta
