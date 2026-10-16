"""AG-UI serve/connect runs. Replay reads the ledger; only a new claim executes."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Request

from . import agui_protocol as protocol
from . import api
from ._http import ssl_context
from .answers import Answers
from .interaction_store import InteractionError
from .sessions import Sessions
from .store import Message, Person, Store

log = logging.getLogger(__name__)


@dataclass
class Active:
    run: dict[str, Any]
    person: Person
    projection: protocol.Projection
    task: asyncio.Task[None] | None = None
    reason: str = ""
    dispatched: bool = False


class AGUI:
    def __init__(self, store: Store, sessions: Sessions, answers: Answers) -> None:
        self.store, self.sessions, self.answers = store, sessions, answers
        self.runs: dict[str, Active] = {}
        self.lock = asyncio.Lock()
        self.http = httpx.AsyncClient(
            trust_env=False,
            verify=ssl_context(),
            follow_redirects=False,
            timeout=httpx.Timeout(60, connect=15),
        )

    def busy(self, chat: str, permitted: str | None = None) -> bool:
        active = self.runs.get(chat)
        return active is not None and active.run["runId"] != permitted

    async def start(
        self, request: Request, person: Person, chat: str, body: dict[str, Any], agent: str | None
    ) -> dict[str, Any]:
        async with self.lock:
            existing = await self.store.agui.get(chat, body["runId"])
            if existing is None and (self.busy(chat) or self.answers.running(chat)):
                raise InteractionError("An answer is already running in this chat.")
            run, created = await self.store.agui.claim(chat, person, body, agent)
            if created:
                active = Active(
                    run,
                    person,
                    protocol.Projection(chat, body["runId"], run["messages"], run["state"]),
                )
                self.runs[chat] = active
                active.task = asyncio.create_task(self._work(active, request, body))
            return run

    async def authorized(self, active: Active) -> dict[str, Any] | None:
        person = await self.sessions.current(active.person.session_id, active.person.sub)
        return await self.store.agui.authorize(
            active.run["threadId"], person, active.run["agentId"]
        )

    async def _monitor(self, active: Active) -> None:
        try:
            while True:
                await asyncio.sleep(0.5)
                await self.authorized(active)
        except asyncio.CancelledError:
            raise
        except Exception:
            active.reason = "revoked"
            if active.task:
                active.task.cancel()

    async def emit(self, active: Active, event: dict[str, Any], status: str = "running") -> None:
        candidate = copy.deepcopy(active.projection)
        candidate.apply(event)
        commit = asyncio.create_task(
            self.store.agui.append(
                active.run["threadId"], active.run["runId"], event, candidate.snapshot(), status
            )
        )
        try:
            await asyncio.shield(commit)
        except asyncio.CancelledError:
            # SQLite's executor may commit after its awaiter is cancelled. Finish
            # that checkpoint before constructing the cancellation terminal event.
            await commit
            active.projection = candidate
            raise
        active.projection = candidate

    async def fail(self, active: Active, message: str, *, uncertain: bool) -> None:
        await self.store.agui.append(
            active.run["threadId"],
            active.run["runId"],
            {
                "type": "RUN_ERROR",
                "code": "EUGENE_UNCERTAIN" if uncertain else "EUGENE_ERROR",
                "message": message,
            },
            None,
            "uncertain" if uncertain else "error",
            force=True,
        )

    async def _work(self, active: Active, request: Request, body: dict[str, Any]) -> None:
        chat = active.run["threadId"]
        monitor = asyncio.create_task(self._monitor(active))
        try:
            async with asyncio.timeout(3600):
                await self.authorized(active)
                if active.run["direction"] == "serve":
                    await self._native(active, request, body)
                else:
                    await self._remote(active, body)
        except asyncio.CancelledError:
            if active.run["direction"] == "serve":
                await self.answers.stop(chat)
            saved = await self.store.agui.get(chat, body["runId"])
            if saved is None or saved["status"] != "running":
                return
            if active.reason == "stop" and active.run["direction"] == "serve":
                try:
                    if not active.projection.started:
                        await self.emit(
                            active,
                            {"type": "RUN_STARTED", "threadId": chat, "runId": body["runId"]},
                        )
                    for cid in list(active.projection.open_calls):
                        await self.emit(active, {"type": "TOOL_CALL_END", "toolCallId": cid})
                    for mid in list(active.projection.open_messages):
                        await self.emit(active, {"type": "TEXT_MESSAGE_END", "messageId": mid})
                    await self.emit(
                        active,
                        {
                            "type": "RUN_FINISHED",
                            "threadId": chat,
                            "runId": body["runId"],
                            "outcome": {"type": "cancelled"},
                        },
                        "cancelled",
                    )
                except InteractionError:
                    await self.fail(
                        active,
                        "The run stopped at its event history limit.",
                        uncertain=active.dispatched,
                    )
            else:
                await self.fail(
                    active,
                    "AG-UI run stopped after access changed or execution was interrupted. "
                    "It was not replayed.",
                    uncertain=active.dispatched,
                )
        except Exception as exc:
            if active.run["direction"] == "serve":
                await self.answers.stop(chat)
            if isinstance(exc, InteractionError):
                message = exc.message
            elif isinstance(exc, httpx.HTTPStatusError):
                message = f"The AG-UI agent returned HTTP {exc.response.status_code}."
            elif isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                message = "The AG-UI run timed out. It was not replayed."
            else:
                # Transport exceptions may contain the configured URL or credentials.
                message = f"The AG-UI run failed ({type(exc).__name__}). It was not replayed."
            await self.fail(active, message, uncertain=active.dispatched)
        finally:
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor
            self.runs.pop(chat, None)
            self.answers.publish(chat, {"type": "reload"})

    async def _native(self, active: Active, request: Request, body: dict[str, Any]) -> None:
        chat, run_id = active.run["threadId"], active.run["runId"]
        await self.emit(
            active,
            {
                "type": "RUN_STARTED",
                "threadId": chat,
                "runId": run_id,
                "protocolVersion": protocol.VERSION,
            },
        )
        await self.emit(active, {"type": "MESSAGES_SNAPSHOT", "messages": body["messages"]})
        request.state.agui_run = run_id
        active.dispatched = True
        result = await api.send(request, chat, api.Send(content=body["messages"][-1]["content"]))
        answer_id = result["answer"]["id"]
        active.run["answerId"] = answer_id
        await self.store.agui.attach(chat, run_id, answer_id)
        await self.emit(
            active, {"type": "TEXT_MESSAGE_START", "messageId": answer_id, "role": "assistant"}
        )
        content = ""
        sent: set[str] = set()
        results: set[str] = set()
        last_state: Any = None
        last_refs: Any = None
        while True:
            running = self.answers.running(chat)
            message = running.message if running else await self.store.message(answer_id)
            if message is None:
                raise InteractionError("The AG-UI answer disappeared.")
            if message.content != content:
                if message.content.startswith(content):
                    delta = message.content[len(content) :]
                    # Native answers can arrive as one large chunk; frame without changing text.
                    for offset in range(0, len(delta), 16000):
                        await self.emit(
                            active,
                            {
                                "type": "TEXT_MESSAGE_CONTENT",
                                "messageId": answer_id,
                                "delta": delta[offset : offset + 16000],
                            },
                        )
                else:
                    await self.emit(active, {"type": "TEXT_MESSAGE_END", "messageId": answer_id})
                    history = copy.deepcopy(active.projection.messages)
                    next(m for m in history if m["id"] == answer_id)["content"] = message.content
                    await self.emit(active, {"type": "MESSAGES_SNAPSHOT", "messages": history})
                    await self.emit(active, {"type": "TEXT_MESSAGE_START", "messageId": answer_id})
                content = message.content
            for turn in message.tool_rounds:
                for call in turn["assistant"]["tool_calls"]:
                    # Gateway call IDs need only be unique within one answer.
                    cid = answer_id + ":" + call["id"]
                    if cid not in sent:
                        await self.emit(
                            active,
                            {
                                "type": "TOOL_CALL_START",
                                "toolCallId": cid,
                                "toolCallName": call["function"]["name"],
                                "parentMessageId": answer_id,
                            },
                        )
                        args = call["function"]["arguments"]
                        for offset in range(0, len(args), 16000):
                            await self.emit(
                                active,
                                {
                                    "type": "TOOL_CALL_ARGS",
                                    "toolCallId": cid,
                                    "delta": args[offset : offset + 16000],
                                },
                            )
                        await self.emit(active, {"type": "TOOL_CALL_END", "toolCallId": cid})
                        sent.add(cid)
                for call in turn["calls"]:
                    cid = answer_id + ":" + call["id"]
                    if (
                        call["status"] not in {"pending", "running", "signing"}
                        and cid not in results
                    ):
                        text = str(call.get("result") or "")
                        if len(text) > 16000:
                            text = (
                                text[:16000]
                                + "\n[Full tool result is saved with the Workbench answer.]"
                            )
                        await self.emit(
                            active,
                            {
                                "type": "TOOL_CALL_RESULT",
                                "toolCallId": cid,
                                "messageId": cid + ":result",
                                "content": text,
                            },
                        )
                        results.add(cid)
            state = {
                "answerId": answer_id,
                "pendingApprovals": sorted(running.approvals) if running else [],
            }
            if state != last_state:
                await self.emit(active, {"type": "STATE_SNAPSHOT", "snapshot": state})
                last_state = state
            snapshot = await self.store.interactions.snapshot(
                chat, None, False, lambda _: set(), set()
            )
            refs = [
                {k: s[k] for k in ("id", "protocol", "version", "revision", "state")}
                for s in snapshot["surfaces"]
                if s["messageId"] == answer_id
            ]
            if refs != last_refs and (refs or last_refs):
                await self.emit(
                    active,
                    {
                        "type": "CUSTOM",
                        "name": "eugene.interactions",
                        "value": {"answerId": answer_id, "surfaces": refs},
                    },
                )
            last_refs = refs
            if not running:
                await self.emit(active, {"type": "TEXT_MESSAGE_END", "messageId": answer_id})
                if message.status in {"done", "stopped"}:
                    status = "done" if message.status == "done" else "cancelled"
                    await self.emit(
                        active,
                        {
                            "type": "RUN_FINISHED",
                            "threadId": chat,
                            "runId": run_id,
                            "outcome": {"type": "success" if status == "done" else "cancelled"},
                        },
                        status,
                    )
                else:
                    await self.fail(
                        active,
                        "Workbench's answer loop failed or was interrupted. See the saved answer.",
                        uncertain=message.status == "interrupted",
                    )
                return
            await asyncio.sleep(0.05)

    async def _remote(self, active: Active, body: dict[str, Any]) -> None:
        chat, run_id = active.run["threadId"], active.run["runId"]
        shown = await self.store.messages(chat)
        parent = shown[-1].id if shown else None
        if not body.get("resume"):
            user = await self.store.add_message(
                Message(
                    id=api._new_id(),
                    chat_id=chat,
                    seq=0,
                    role="user",
                    status="done",
                    created_at=time.time(),
                    content=body["messages"][-1]["content"],
                ),
                parent_id=parent,
                agui_run_id=run_id,
            )
            parent = user.id
        answer = await self.store.add_message(
            Message(
                id=api._new_id(),
                chat_id=chat,
                seq=0,
                role="assistant",
                status="running",
                created_at=time.time(),
            ),
            parent_id=parent,
            agui_run_id=run_id,
        )
        active.run["answerId"] = answer.id
        await self.store.agui.attach(chat, run_id, answer.id)
        await self.store.update_chat(chat, updated_at=time.time())
        agent = await self.authorized(active)
        assert agent is not None
        headers = {"Accept": "text/event-stream"}
        if agent["token"]:
            headers["Authorization"] = "Bearer " + agent["token"]
        forwarded = {
            **body,
            "protocolVersion": protocol.VERSION,
            "forwardedProps": {"eugene": {"subject": active.person.sub}},
        }
        parser = protocol.SSE()
        terminal: dict[str, Any] | None = None
        active.dispatched = True
        async with self.http.stream(
            "POST", agent["url"], json=forwarded, headers=headers
        ) as response:
            response.raise_for_status()
            if (
                response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "text/event-stream"
            ):
                raise InteractionError("The AG-UI agent did not return an SSE stream.")
            async for chunk in response.aiter_bytes():
                # httpx transports may return arbitrarily large chunks; parser owns frame bounds.
                for offset in range(0, len(chunk), 16384):
                    for event in parser.feed(chunk[offset : offset + 16384]):
                        await self.authorized(active)
                        if terminal is not None:
                            raise InteractionError(
                                "The AG-UI agent sent an event after its terminal event."
                            )
                        if event.get("type") in protocol.TERMINAL:
                            candidate = copy.deepcopy(active.projection)
                            candidate.apply(event)
                            terminal = event
                        else:
                            await self.emit(active, event)
            trailing = parser.feed(b"", final=True)
            for event in trailing:
                if terminal is not None or event.get("type") not in protocol.TERMINAL:
                    raise InteractionError("Invalid trailing AG-UI event.")
                terminal = event
        if terminal is None:
            raise InteractionError("The AG-UI stream ended without a terminal event.")
        await self.authorized(active)
        status = (
            "error"
            if terminal["type"] == "RUN_ERROR"
            else {
                "success": "done",
                "interrupt": "interrupted",
                "cancelled": "cancelled",
            }[terminal.get("outcome", {"type": "success"})["type"]]
        )
        await self.emit(active, terminal, status)

    async def stop(self, chat: str, *, reason: str = "stop", run_id: str | None = None) -> None:
        active = self.runs.get(chat)
        if active and active.task and (run_id is None or active.run["runId"] == run_id):
            active.reason = reason
            active.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await active.task
            saved = await self.store.agui.get(chat, active.run["runId"])
            if saved and saved["status"] == "running":
                # Cancellation can arrive before the task enters its try/finally.
                await self.fail(
                    active, "This AG-UI run stopped before completion.", uncertain=active.dispatched
                )
            self.runs.pop(chat, None)

    async def remove_agent(self, agent_id: str) -> None:
        await self.store.agui.delete_agent(agent_id)
        for chat, active in list(self.runs.items()):
            if active.run["agentId"] == agent_id:
                await self.stop(chat, reason="revoked")

    async def aclose(self) -> None:
        for chat in list(self.runs):
            await self.stop(chat, reason="shutdown")
        await self.http.aclose()
