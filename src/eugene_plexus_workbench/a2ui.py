"""Typed A2UI producer bridge; all external effects use durable host approval."""

from __future__ import annotations

import copy
import hashlib
import time
from typing import TYPE_CHECKING, Any, Literal

from . import a2ui_state as ui
from . import mcp_ui
from .content import Keeper, normalize
from .interaction_store import encoded
from .interactions import Interactions, NoEffect, Result, Surface, Update, description
from .store import Person

if TYPE_CHECKING:
    from .mcp_apps import McpApps
    from .tools import Tools, ToolSession


def declaration(meta: dict[str, Any]) -> dict[str, Any]:
    value = meta.get(ui.EXTENSION)
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "catalogId", "actionTool"}
        or value["version"] != ui.VERSION
        or value["catalogId"] != ui.CATALOG
        or not isinstance(value["actionTool"], str)
        or not 1 <= len(value["actionTool"]) <= 256
    ):
        ui.refuse("Unsupported A2UI producer declaration.")
    return value


def actions(view: dict[str, Any]) -> list[str]:
    ready, reachable = ui.graph(view["components"])
    return (
        ["a2ui/action"]
        if ready
        and any(c["id"] in reachable and "event" in c.get("action", {}) for c in view["components"])
        else []
    )


class A2UI:
    def __init__(self, tools: Tools, interactions: Interactions, apps: McpApps) -> None:
        self.tools, self.interactions, self.apps = tools, interactions, apps
        self.store = interactions.store

    def validate_surface(self, surface: Surface) -> None:
        try:
            ui.validate_view(surface.view)
            declaration({ui.EXTENSION: surface.binding["producer"]})
            if (
                not surface.connection_id
                or not surface.binding["source"]
                or not surface.binding["tool"]
                or not surface.binding["namespace"]
                or surface.binding["wire"] != surface.view["surfaceId"]
                or surface.actions != actions(surface.view)
            ):
                ui.refuse()
        except Exception:
            ui.refuse("Invalid A2UI surface or source binding.")

    def validate_action(self, surface: Surface, name: str, arguments: dict[str, Any]) -> None:
        try:
            if name != "a2ui/action":
                ui.refuse()
            ui.event(surface.view, arguments)
        except Exception:
            ui.refuse("This event does not match the current form and its checks.")

    async def plan(
        self, origin: Surface, envelope: Any, expected: tuple[str, int] | None = None
    ) -> tuple[Update, ...]:
        encoded(envelope, 64 * 1024)
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"mimeType", "messages", "fallback"}
            or envelope["mimeType"] != ui.MIME
            or not isinstance(envelope["fallback"], str)
            or not 1 <= len(envelope["fallback"]) <= 16000
            or not isinstance(envelope["messages"], list)
            or not 1 <= len(envelope["messages"]) <= 64
        ):
            ui.refuse("Invalid A2UI envelope.")
        messages = []
        for message in envelope["messages"]:
            ui.SERVER.validate(message)
            names = set(message) - {"version"}
            if len(names) != 1:
                ui.refuse()
            name = names.pop()
            payload = message[name]
            messages.append((name, payload))
        wire = ui.identifier(messages[0][1]["surfaceId"])
        if any(p["surfaceId"] != wire for _, p in messages):
            ui.refuse("Each envelope must address one surface.")
        if expected and wire != origin.view["surfaceId"]:
            ui.refuse("An action cannot update another wire surface.")
        saved = await self.store.interactions.latest_a2ui(
            origin.chat_id, origin.binding["namespace"], wire
        )
        if expected and (saved is None or (saved["id"], saved["revision"]) != expected):
            ui.refuse("The form changed during execution.")
        if saved and (
            saved["state"] == "disabled"
            or any(saved["binding"][k] != origin.binding[k] for k in ("source", "producer", "tool"))
        ):
            ui.refuse("Request a new answer for this source.")
        current = description(saved) if saved else None
        state: Literal["active", "disabled", "deleted"] = saved["state"] if saved else "deleted"
        base = saved["revision"] if saved else 0
        incarnation = saved["binding"]["incarnation"] if saved else 0
        updates: dict[str, Update] = {}
        for name, payload in messages:
            if name == "createSurface":
                if state == "active" or payload["catalogId"] != ui.CATALOG:
                    ui.refuse("The surface already exists or uses an unknown catalog.")
                incarnation += 1
                binding = {**origin.binding, "wire": wire, "incarnation": incarnation}
                ident = hashlib.sha256(
                    f"{binding['namespace']}:{wire}:{incarnation}".encode()
                ).hexdigest()[:48]
                current = origin.model_copy(
                    deep=True,
                    update={
                        "id": "a2ui_" + ident,
                        "binding": binding,
                        "view": {
                            "surfaceId": wire,
                            "catalogId": ui.CATALOG,
                            "theme": payload.get("theme", {}),
                            "sendDataModel": payload.get("sendDataModel", False),
                            "components": [],
                            "dataModel": {},
                            "ready": False,
                        },
                    },
                )
                base, state = 0, "active"
            elif current is None or state != "active":
                ui.refuse("Create the surface before updating it.")
            elif name == "updateComponents":
                new = payload["components"]
                if len({c["id"] for c in new}) != len(new):
                    ui.refuse("Duplicate component IDs in an update.")
                components = {c["id"]: c for c in current.view["components"]}
                components.update({c["id"]: copy.deepcopy(c) for c in new})
                current.view["components"] = list(components.values())
            elif name == "updateDataModel":
                current.view["dataModel"] = ui.write(
                    current.view["dataModel"],
                    payload.get("path", ""),
                    payload.get("value"),
                    "value" in payload,
                )
            elif name == "deleteSurface":
                state = "deleted"
            else:
                ui.refuse()
            assert current is not None
            current.fallback = envelope["fallback"]
            # Bound intermediate states too; a later message cannot hide a cycle
            # or allocate an unbounded temporary graph/data model.
            ui.validate_view(current.view)
            current.actions = actions(current.view)
            updates[current.id] = Update(current.model_copy(deep=True), base, state)
            if len(updates) > 2:
                ui.refuse("Too many surface incarnations in one envelope.")
        return tuple(updates.values())

    async def capture(
        self, session: ToolSession, call: dict[str, Any], result: Any, keeper: Keeper
    ) -> None:
        try:
            producer = declaration(session.metadata[call["name"]])
            raw = result.model_dump(mode="json", by_alias=True, exclude_none=True)
            envelope = raw.get("_meta", {}).get(ui.EXTENSION)
            if envelope is None:
                return
            server, _, tool, _ = session.tools[call["name"]]
            namespace = hashlib.sha256(
                f"{keeper.message_id}:{server['id']}:{tool}".encode()
            ).hexdigest()
            origin = Surface(
                id="pending",
                owner=keeper.owner,
                chat_id=keeper.chat_id,
                message_id=keeper.message_id,
                run_id=keeper.message_id,
                protocol=ui.PROTOCOL,
                version=ui.VERSION,
                connection_id=server["id"],
                view={},
                fallback="A form is available.",
                actions=[],
                binding={
                    "namespace": namespace,
                    "tool": tool,
                    "source": mcp_ui.fingerprint(server),
                    "producer": producer,
                },
                expires_at=time.time() + 86400,
            )
            await self.interactions.publish_updates(await self.plan(origin, envelope))
        except Exception:
            # UI admission never changes the outcome of a completed tool call.
            # No repair loop may repeat an effectful producer.
            return

    async def authorize(self, person: Person, surface: Surface, action: dict[str, Any]) -> None:
        self.validate_action(surface, action["name"], action["arguments"])
        await self.apps.source(person, surface)

    def origin(self, session: ToolSession, surface: Surface) -> None:
        for key, (server, _, tool, _) in session.tools.items():
            if (
                server["id"] == surface.connection_id
                and tool == surface.binding["tool"]
                and mcp_ui.fingerprint(server) == surface.binding["source"]
                and declaration(session.metadata[key]) == surface.binding["producer"]
            ):
                return
        ui.refuse("The producer declaration changed. Request a new answer.")

    async def input(
        self, person: Person, chat: str, ident: str, revision: int, component: str, value: Any
    ) -> int:
        saved = await self.store.interactions.authorized_surface(person, chat, ident, revision)
        surface = description(saved)
        if (surface.protocol, surface.version) != (ui.PROTOCOL, ui.VERSION):
            ui.refuse("This surface is not an A2UI form.")
        await self.apps.checked(person, surface, revision)
        try:
            surface.view = ui.input_value(surface.view, component, value)
        except Exception:
            ui.refuse("This component does not offer a writable value of that type.")
        result = await self.interactions.publish(surface, base=revision, person=person)
        return int(result["revision"])

    async def execute(self, person: Person, surface: Surface, action: dict[str, Any]) -> Result:
        sent = False
        try:
            self.validate_action(surface, action["name"], action["arguments"])
            await self.apps.checked(person, surface, action["revision"])
            async with self.tools.connect([surface.connection_id or ""], person) as session:
                self.origin(session, surface)
                args = {"event": action["arguments"]}
                if surface.view["sendDataModel"]:
                    args["dataModel"] = surface.view["dataModel"]
                client, tool = self.apps.tool(
                    session,
                    {
                        "name": surface.binding["producer"]["actionTool"],
                        "arguments": args,
                    },
                )
                await self.apps.checked(person, surface, action["revision"])
                current = await self.store.interactions.existing(
                    person, surface.chat_id, action["id"]
                )
                if current is None or current["status"] != "dispatching":
                    raise NoEffect()
                sent = True
                raw = await client.session.call_tool(tool, args, read_timeout_seconds=120)
                result = raw.model_dump(mode="json", by_alias=True, exclude_none=True)
                encoded(result, 60 * 1024)
                _, text = await normalize(
                    action["id"], result.get("content"), result.get("structuredContent"), None
                )
                await self.apps.checked(person, surface, action["revision"])
                updates: tuple[Update, ...] = ()
                envelope = result.get("_meta", {}).get(ui.EXTENSION)
                if envelope is not None:
                    try:
                        updates = await self.plan(
                            surface, envelope, (surface.id, action["revision"])
                        )
                        self.interactions.updates(updates, surface)
                    except Exception:
                        updates = ()
                        result = {
                            "toolResult": result,
                            "uiError": "Invalid UI update; the previous form was kept.",
                        }
                return Result(result, text, updates)
        except Exception:
            if not sent:
                raise NoEffect() from None
            raise
