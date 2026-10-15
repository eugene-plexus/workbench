"""MCP Apps headless host. All effects use G1 approval and durable dispatch."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry

from . import mcp_ui
from .content import Keeper, normalize
from .interaction_store import InteractionError, encoded
from .interactions import Interactions, NoEffect, Result, Surface, description
from .store import Person
from .tools import Tools, ToolSession, _no_resource, visible_server


class McpApps:
    def __init__(self, tools: Tools, interactions: Interactions) -> None:
        self.tools, self.interactions = tools, interactions
        self.store = interactions.store
        self.read_slots = asyncio.Semaphore(8)

    def validate_surface(self, surface: Surface) -> None:
        if (
            not surface.connection_id
            or set(surface.actions) != set(mcp_ui.ACTIONS)
            or surface.view["resourceUri"] != mcp_ui.uri(surface.binding["uri"])
            or not surface.binding["tool"]
            or not surface.binding["source"]
        ):
            raise InteractionError("Invalid MCP app binding.", 422)

    def validate_action(self, surface: Surface, name: str, arguments: dict[str, Any]) -> None:
        try:
            mcp_ui.action(name, arguments)
        except ValueError:
            raise InteractionError("Unsupported or invalid MCP app request.", 422) from None

    async def capture(
        self, session: ToolSession, call: dict[str, Any], result: Any, keeper: Keeper
    ) -> None:
        resource_uri = mcp_ui.resource_uri(session.metadata[call["name"]])
        if resource_uri is None:
            return
        server, _, tool, _ = session.tools[call["name"]]
        try:
            await self.interactions.publish(
                Surface(
                    id="mcp_"
                    + hashlib.sha256(f"{keeper.message_id}:{call['id']}".encode()).hexdigest()[:48],
                    owner=keeper.owner,
                    chat_id=keeper.chat_id,
                    message_id=keeper.message_id,
                    run_id=keeper.message_id,
                    protocol=mcp_ui.PROTOCOL,
                    version=mcp_ui.VERSION,
                    connection_id=server["id"],
                    view={
                        "resourceUri": resource_uri,
                        "tool": session.descriptors[(server["id"], tool)],
                        "toolInput": {"arguments": call["arguments"]},
                        "toolResult": result.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        ),
                    },
                    fallback=(call["result"] or "The tool returned an app.")[:16000],
                    actions=list(mcp_ui.ACTIONS),
                    binding={
                        "uri": resource_uri,
                        "tool": tool,
                        "source": mcp_ui.fingerprint(server),
                    },
                    expires_at=time.time() + 86400,
                )
            )
        except (ValueError, InteractionError):
            # Preserve the completed ordinary tool result, including its fallback.
            return

    async def source(self, person: Person, surface: Surface) -> dict[str, Any]:
        person = await self.interactions.sessions.current(person.session_id, person.sub)
        chat = await self.store.chat(surface.chat_id)
        for server in await self.store.tool_servers():
            if (
                server["id"] == surface.connection_id
                and mcp_ui.fingerprint(server) == surface.binding["source"]
                and visible_server(server, person)
                and chat is not None
                and chat.owner == person.sub == surface.owner
                and server["id"] in chat.settings.get("toolServers", [])
            ):
                return server
        raise InteractionError("The app's source is no longer available.")

    @staticmethod
    def origin(session: ToolSession, surface: Surface) -> None:
        for key, (server, _, tool, _) in session.tools.items():
            if tool == surface.binding["tool"]:
                if (
                    server["id"] == surface.connection_id
                    and mcp_ui.fingerprint(server) == surface.binding["source"]
                    and mcp_ui.resource_uri(session.metadata[key]) == surface.binding["uri"]
                ):
                    return
                break
        raise InteractionError("The tool's app linkage changed. Request a new answer.")

    @staticmethod
    def tool(session: ToolSession, arguments: dict[str, Any]) -> tuple[Any, str]:
        for key, (_, client, tool, schema) in session.tools.items():
            if tool == arguments["name"]:
                if "app" not in mcp_ui.visibility(session.metadata[key]):
                    break
                Draft202012Validator(schema, registry=Registry(retrieve=_no_resource)).validate(
                    arguments["arguments"]
                )
                return client, tool
        raise InteractionError("This tool is not available to the app.")

    async def authorize(self, person: Person, surface: Surface, action: dict[str, Any]) -> None:
        self.validate_action(surface, action["name"], action["arguments"])
        await self.source(person, surface)

    async def checked(self, person: Person, surface: Surface, revision: int) -> None:
        await self.source(person, surface)
        await self.store.interactions.authorized_surface(
            person, surface.chat_id, surface.id, revision
        )

    async def read(
        self,
        person: Person,
        chat: str,
        surface_id: str,
        revision: int,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        saved = await self.store.interactions.authorized_surface(person, chat, surface_id, revision)
        surface = description(saved)
        if (surface.protocol, surface.version) != (mcp_ui.PROTOCOL, mcp_ui.VERSION):
            raise InteractionError("This surface is not a supported MCP app.", 422)
        try:
            self.validate_read(surface, method, params)
            await self.checked(person, surface, revision)
            async with (
                asyncio.timeout(45),
                self.read_slots,
                self.tools.connect([surface.connection_id or ""], person) as session,
            ):
                self.origin(session, surface)
                if method == "resources/read":
                    client = next(iter(session.tools.values()))[1]
                    raw = await client.session.read_resource(params["uri"])
                    result = mcp_ui.resource(
                        raw.model_dump(mode="json", by_alias=True, exclude_none=True),
                        surface.binding["uri"],
                    )
                elif method == "tools/list":
                    result = {
                        "tools": [
                            session.descriptors[(server["id"], tool)]
                            for key, (server, _, tool, _) in session.tools.items()
                            if "app" in mcp_ui.visibility(session.metadata[key])
                        ]
                    }
                elif method == "ui/initialize":
                    result = {
                        "protocolVersion": mcp_ui.VERSION,
                        "hostInfo": {"name": "Eugene Workbench backend", "version": "1"},
                        "hostCapabilities": {"serverTools": {}, "serverResources": {}},
                    }
                else:
                    result = {}
                encoded(result, 256 * 1024)
                await self.checked(person, surface, revision)
                return result
        except InteractionError:
            raise
        except Exception as exc:
            # SDK transport task groups may wrap our post-IO access rejection.
            while isinstance(exc, ExceptionGroup):
                selected = exc.subgroup(InteractionError)
                if selected is None:
                    break
                exc = selected.exceptions[0]
            if isinstance(exc, InteractionError):
                raise exc from None
            raise InteractionError("The app request could not be completed safely.", 422) from None

    @staticmethod
    def validate_read(surface: Surface, method: str, params: dict[str, Any]) -> None:
        if method == "resources/read":
            if set(params) != {"uri"} or params["uri"] != surface.binding["uri"]:
                raise InteractionError("Only this app's bound resource may be read.", 422)
        elif method == "ui/initialize":
            if (
                set(params) != {"protocolVersion", "appInfo", "appCapabilities"}
                or params["protocolVersion"] != mcp_ui.VERSION
                or not isinstance(params["appInfo"], dict)
                or not isinstance(params["appCapabilities"], dict)
                or not all(isinstance(params["appInfo"].get(k), str) for k in ("name", "version"))
            ):
                raise InteractionError("Unsupported app initialization.", 422)
        elif method not in ("ping", "tools/list") or params:
            raise InteractionError("Unsupported MCP app read method.", 422)

    async def execute(self, person: Person, surface: Surface, action: dict[str, Any]) -> Result:
        sent = False
        try:
            await self.checked(person, surface, action["revision"])
            async with self.tools.connect([surface.connection_id or ""], person) as session:
                self.origin(session, surface)
                name, args = action["name"], action["arguments"]
                if name == "tools/call":
                    client, tool = self.tool(session, args)
                    await self.checked(person, surface, action["revision"])
                    current = await self.store.interactions.existing(
                        person, surface.chat_id, action["id"]
                    )
                    if current is None or current["status"] != "dispatching":
                        raise NoEffect()
                    sent = True
                    # Exactly once, bypass the SDK convenience retry/elicitation loop.
                    raw = await client.session.call_tool(
                        tool, args["arguments"], read_timeout_seconds=120
                    )
                    result = raw.model_dump(mode="json", by_alias=True, exclude_none=True)
                    encoded(result, 64 * 1024)
                    _, text = await normalize(
                        action["id"], result.get("content"), result.get("structuredContent"), None
                    )
                    await self.checked(person, surface, action["revision"])
                    return Result(result, text)
                await self.checked(person, surface, action["revision"])
                text = args["content"]["text"] if name == "ui/message" else encoded(args, 16 * 1024)
                return Result({}, text)
        except Exception:
            if not sent:
                raise NoEffect() from None
            raise
