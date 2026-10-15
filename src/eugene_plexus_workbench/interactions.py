"""Durable interaction coordinator for trusted backend protocol adapters.

Adapters are trusted server code, never loaded from a message, URL or browser.
Approval belongs to the host, not to an embedded app's bridge.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .interaction_store import InteractionError, encoded, public_action
from .sessions import Sessions
from .store import Person, Store


class Surface(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=80)
    owner: str
    chat_id: str
    message_id: str
    run_id: str = Field(min_length=1, max_length=80)
    protocol: str
    version: str
    connection_id: str | None = None
    view: dict[str, Any]
    fallback: str = Field(min_length=1, max_length=16000)
    model_text: str = ""
    actions: list[str] = Field(max_length=32)
    binding: dict[str, Any] = Field(default_factory=dict)
    expires_at: float


@dataclass(frozen=True)
class Update:
    surface: Surface
    base: int
    state: Literal["active", "disabled", "deleted"] = "active"


@dataclass(frozen=True)
class Result:
    ui: dict[str, Any]
    model_text: str = ""
    updates: tuple[Update, ...] = ()


class NoEffect(Exception):
    """Adapter certifies that execution failed without any external effect."""


class Adapter(Protocol):
    def validate_surface(self, surface: Surface) -> None: ...

    def validate_action(self, surface: Surface, name: str, arguments: dict[str, Any]) -> None: ...

    async def authorize(self, person: Person, surface: Surface, action: dict[str, Any]) -> None:
        """Check current source permissions and tool policy; perform no side effects."""
        ...

    async def execute(self, person: Person, surface: Surface, action: dict[str, Any]) -> Result: ...


def description(saved: dict[str, Any]) -> Surface:
    return Surface.model_validate(
        {k: v for k, v in saved.items() if k not in {"state", "revision"}}
    )


class Interactions:
    def __init__(self, store: Store, sessions: Sessions) -> None:
        self.store, self.sessions = store, sessions
        self.adapters: dict[tuple[str, str], Adapter] = {}
        self.tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self.closing = False

    def adapter(self, surface: Surface) -> Adapter:
        adapter = self.adapters.get((surface.protocol, surface.version))
        if adapter is None:
            raise InteractionError("This interaction protocol version is not supported.")
        return adapter

    async def publish(
        self,
        surface: Surface,
        *,
        base: int = 0,
        state: Literal["active", "disabled", "deleted"] = "active",
        person: Person | None = None,
    ) -> dict[str, Any]:
        encoded(surface.model_dump(), 256 * 1024)
        encoded(surface.model_text, 64 * 1024)
        self.adapter(surface).validate_surface(surface)
        return await self.store.interactions.publish(surface.model_dump(), base, state, person)

    def updates(
        self, updates: tuple[Update, ...], origin: Surface | None = None
    ) -> list[dict[str, Any]]:
        if len(updates) > 2:
            raise InteractionError("Too many atomic surface updates.", 413)
        result = []
        for update in updates:
            surface = update.surface
            if origin is not None and any(
                getattr(surface, k) != getattr(origin, k)
                for k in (
                    "owner",
                    "chat_id",
                    "message_id",
                    "run_id",
                    "protocol",
                    "version",
                    "connection_id",
                )
            ):
                raise InteractionError("An action cannot update another origin.")
            encoded(surface.model_dump(), 256 * 1024)
            self.adapter(surface).validate_surface(surface)
            result.append(
                {"data": surface.model_dump(), "base": update.base, "state": update.state}
            )
        return result

    async def publish_updates(self, updates: tuple[Update, ...]) -> None:
        await self.store.interactions.publish_batch(self.updates(updates))

    async def propose(
        self, person: Person, chat: str, surface_id: str, proposal: dict[str, Any]
    ) -> dict[str, Any]:
        encoded(proposal, 20 * 1024)
        if await self.store.interactions.existing(person, chat, proposal["id"]) is None:
            saved = await self.store.interactions.surface(chat, surface_id)
            surface = description(saved)
            self.adapter(surface).validate_action(surface, proposal["name"], proposal["arguments"])
        return public_action(
            await self.store.interactions.propose(person, chat, surface_id, proposal)
        )

    async def decide(
        self, person: Person, chat: str, action_id: str, approve: bool
    ) -> dict[str, Any]:
        if self.closing:
            raise InteractionError("Workbench is stopping.", 503)
        action = await self.store.interactions.decide(person, chat, action_id, approve)
        key = chat, action_id
        if action["status"] == "queued" and key not in self.tasks:
            if len(self.tasks) >= 8 or any(c == chat for c, _ in self.tasks):
                await self.store.interactions.finish(
                    chat,
                    action_id,
                    "cancelled",
                    error="Another action is running. Submit a new action after it finishes.",
                )
                action["status"] = "cancelled"
                action["error"] = (
                    "Another action is running. Submit a new action after it finishes."
                )
            else:
                task = asyncio.create_task(
                    self._execute(person, chat, action_id), name=f"interaction-{action_id}"
                )
                self.tasks[key] = task
                task.add_done_callback(lambda done: self.tasks.pop(key, None))
        return public_action(action)

    async def _execute(self, person: Person, chat: str, action_id: str) -> None:
        dispatched = False
        try:
            async with asyncio.timeout(120):
                action = await self.store.interactions.existing(person, chat, action_id)
                if action is None or action["status"] != "queued":
                    return
                saved = await self.store.interactions.surface(chat, action["surfaceId"])
                surface = description(saved)
                adapter = self.adapter(surface)
                current = await self.sessions.current(person.session_id, person.sub)
                await adapter.authorize(current, surface, public_action(action))
                # Authorization may await IO. The atomic claim checks the persisted
                # session, branch, source, expiry and revision again after that wait.
                claimed = await self.store.interactions.claim(chat, action_id)
                if claimed is None:
                    return
                saved, action = claimed
                dispatched = True
                result = await self._dispatch(adapter, current, description(saved), action)
                await self.store.interactions.sweep()
                await self.store.interactions.finish(
                    chat,
                    action_id,
                    "completed",
                    result.ui,
                    result.model_text,
                    updates=self.updates(result.updates, surface),
                )
        except NoEffect:
            await self.store.interactions.finish(
                chat, action_id, "failed", error="The action failed without making a change."
            )
        except (Exception, asyncio.CancelledError):
            # No exception text from a tool or auth client reaches a saved/public
            # error: it can contain credentials, paths or a remote response body.
            await self.store.interactions.finish(
                chat,
                action_id,
                "uncertain" if dispatched else "cancelled",
                error="The outcome could not be confirmed."
                if dispatched
                else "The action could not be authorized.",
            )

    async def _dispatch(
        self, adapter: Adapter, person: Person, surface: Surface, action: dict[str, Any]
    ) -> Result:
        effect = asyncio.create_task(adapter.execute(person, surface, public_action(action)))
        try:
            while True:
                await asyncio.wait({effect}, timeout=1)
                await self.store.interactions.sweep()
                saved = await self.store.interactions.existing(
                    person, surface.chat_id, action["id"]
                )
                if saved is None or saved["status"] != "dispatching":
                    # Stop, expiry or revoked access cannot undo a remote effect.
                    # The durable record is already uncertain; stop waiting on it.
                    raise asyncio.CancelledError()
                if effect.done():
                    return effect.result()
        finally:
            if not effect.done():
                effect.cancel()
            await asyncio.gather(effect, return_exceptions=True)

    async def stop(self, chat: str) -> None:
        await self.store.interactions.stop(chat)
        for (c, _), task in list(self.tasks.items()):
            if c == chat:
                task.cancel()

    async def aclose(self) -> None:
        self.closing = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks)
        await self.store.interactions.sweep(restart=True)
