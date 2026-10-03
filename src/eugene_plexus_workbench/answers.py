"""Answers run here, not in a tab (W1).

The browser asks; this module runs the answer against the gateway, keeps
it as it arrives, and sends it to every tab watching that chat. So:

- a tab that opens mid-answer gets what has arrived so far, then the rest;
- **an answer keeps going when every tab is closed** (Troy, C3 call 2), and
  is saved as it arrives. Only Stop ends it;
- a restart of Workbench mid-answer keeps what had arrived and marks the
  answer `interrupted` (`Store.mark_interrupted`, at boot).

An answer is saved at most once a second while it streams, and at its
end. What a watching tab is sent is the change since the last thing it
was sent, never the whole answer again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .hub import Hub, HubError
from .store import Message, Store, interrupt_tools
from .tools import Calls, ToolError, Tools, transcript

log = logging.getLogger(__name__)

_SAVE_EVERY = 1.0
#: A watching tab that falls this far behind is dropped; it reloads.
_QUEUE_DEPTH = 1000
_APPROVAL_SECONDS = 1800


def _tool_problem(exc: BaseException) -> str:
    if isinstance(exc, HubError):
        return exc.message
    if isinstance(exc, ToolError):
        return str(exc)
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            if isinstance(child, (HubError, ToolError, BaseExceptionGroup)):
                return _tool_problem(child)
    return (
        f"The MCP connection failed ({type(exc).__name__}). "
        "Check the server in Tools; interrupted calls are not retried."
    )


def _js_length(text: str) -> int:
    """A string's length as the page counts it: UTF-16 code units, so an
    emoji is 2 there and 1 in Python, and an offset must agree with the page."""
    return len(text.encode("utf-16-le")) // 2


def _js_offset(text: str, index: int | None) -> int | None:
    """A Python index into `text` as the page counts it."""
    return None if index is None else _js_length(text[:index])


def answer_text(message: Message) -> str:
    """What the reply answered with: its text after its last web search.

    A model told to search can write a whole answer first and then search
    (workbench#1); that first text is a draft, not the answer. A reply with
    no mark, or with nothing after it, is its whole text.
    """
    if message.answer_from is None:
        return message.content
    after = message.content[message.answer_from :].lstrip()
    return after or message.content


def message_view(message: Message) -> dict[str, Any]:
    """A message as the page reads it."""
    return {
        "id": message.id,
        "seq": message.seq,
        "role": message.role,
        "content": message.content,
        "reasoning": message.reasoning,
        "attachments": message.attachments,
        "status": message.status,
        "error": message.error,
        "sources": message.sources,
        "searches": message.searches,
        "search": message.search,
        "model": message.model,
        "finish": message.finish,
        "createdAt": message.created_at,
        "finishedAt": message.finished_at,
        "answerFrom": _js_offset(message.content, message.answer_from),
        "reasoningFrom": _js_offset(message.reasoning, message.reasoning_from),
        "toolRounds": message.tool_rounds,
    }


@dataclass
class Running:
    chat_id: str
    message: Message
    task: asyncio.Task[None] | None = None
    progress: dict[str, Any] | None = None
    saved_at: float = 0.0
    shutting_down: bool = False
    watchers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    approvals: dict[str, asyncio.Future[bool]] = field(default_factory=dict)


class Watch:
    """One tab watching one chat."""

    def __init__(self, answers: Answers, chat_id: str) -> None:
        self._answers = answers
        self.chat_id = chat_id
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_DEPTH)

    def close(self) -> None:
        self._answers._unwatch(self)


class Answers:
    """Every answer being written, by chat."""

    def __init__(self, store: Store, hub: Hub, tools: Tools | None = None) -> None:
        self._store = store
        self._hub = hub
        self._tools = tools
        self._running: dict[str, Running] = {}
        self._watchers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    def running(self, chat_id: str) -> Running | None:
        return self._running.get(chat_id)

    # --- watching -------------------------------------------------------

    def watch(self, chat_id: str) -> tuple[Watch, dict[str, Any] | None]:
        """A new watcher, and the answer in progress as it stands now."""
        watch = Watch(self, chat_id)
        self._watchers.setdefault(chat_id, set()).add(watch.queue)
        current = self._running.get(chat_id)
        snapshot = None
        if current is not None:
            snapshot = {
                "type": "answer",
                "message": message_view(current.message),
                "progress": current.progress,
            }
        return watch, snapshot

    def _unwatch(self, watch: Watch) -> None:
        queues = self._watchers.get(watch.chat_id)
        if queues is not None:
            queues.discard(watch.queue)
            if not queues:
                self._watchers.pop(watch.chat_id, None)

    def publish(self, chat_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._watchers.get(chat_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Too far behind to catch up from deltas: tell it to reload.
                self._watchers[chat_id].discard(queue)
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait({"type": "reload"})

    # --- running --------------------------------------------------------

    def start(
        self,
        chat_id: str,
        message: Message,
        build: Callable[[], Any],
        tool_servers: list[str] | None = None,
    ) -> None:
        """Run the answer `message` holds. `build` makes the request body
        (in a thread: it reads the attachments' bytes)."""
        if chat_id in self._running:
            raise RuntimeError(f"an answer is already being written in chat {chat_id}")
        running = Running(chat_id=chat_id, message=message)
        self._running[chat_id] = running
        running.task = asyncio.create_task(
            self._run(running, build, tool_servers or []), name=f"answer-{message.id}"
        )

    def approve(self, chat_id: str, message_id: str, call_id: str, allow: bool) -> bool:
        running = self.running(chat_id)
        if running is None or running.message.id != message_id:
            return False
        future = running.approvals.get(call_id)
        if future is None or future.done():
            return False
        future.set_result(allow)
        return True

    async def stop(self, chat_id: str) -> bool:
        running = self._running.get(chat_id)
        if running is None or running.task is None:
            return False
        running.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await running.task
        return True

    async def aclose(self) -> None:
        """Shutdown: an answer cut off here is `interrupted`, not `stopped`."""
        for running in list(self._running.values()):
            running.shutting_down = True
            if running.task is not None:
                running.task.cancel()
        tasks = [r.task for r in self._running.values() if r.task is not None]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _save(self, running: Running, *, final: bool = False) -> None:
        m = running.message
        values: dict[str, Any] = {
            "content": m.content,
            "reasoning": m.reasoning,
            "sources": m.sources,
            "searches": m.searches,
            "answer_from": m.answer_from,
            "reasoning_from": m.reasoning_from,
            "tool_rounds": m.tool_rounds,
        }
        if final:
            values.update(
                status=m.status, error=m.error, finish=m.finish, finished_at=m.finished_at
            )
        await self._store.update_message(m.id, **values)
        running.saved_at = time.perf_counter()

    async def _run(
        self, running: Running, build: Callable[[], Any], tool_servers: list[str]
    ) -> None:
        m = running.message
        chat_id = running.chat_id
        try:
            body = await asyncio.to_thread(build)
            if tool_servers:
                await self._with_tools(running, body, tool_servers)
            else:
                await self._stream(running, body)
            m.status = "done"
        except asyncio.CancelledError:
            m.status = "interrupted" if running.shutting_down else "stopped"
        except HubError as exc:
            m.status, m.error = "failed", exc.message
        except ToolError as exc:
            m.status, m.error = "failed", str(exc)
        except Exception as exc:
            if tool_servers:
                # SDK task groups wrap exceptions. Never expose raw transport
                # errors, which may include a credential or server response.
                m.status, m.error = "failed", _tool_problem(exc)
            else:
                log.exception("an answer in chat %s failed", chat_id)
                m.status, m.error = "failed", f"Workbench failed while writing this answer: {exc}"
        finally:
            interrupt_tools(m.tool_rounds)
            m.finished_at = time.time()
            running.progress = None
            try:
                await asyncio.shield(self._save(running, final=True))
            finally:
                self._running.pop(chat_id, None)
                self.publish(chat_id, {"type": "done", "message": message_view(m)})

    async def _stream(self, running: Running, body: dict[str, Any]) -> list[dict[str, Any]]:
        calls = Calls()
        running.message.finish = None
        prior_searches = running.message.searches
        async for chunk in self._hub.stream_chat(body):
            extension = chunk.get("x_eugene_plexus") or {}
            if isinstance(extension.get("web_searches"), int):
                chunk = {
                    **chunk,
                    "x_eugene_plexus": {
                        **extension,
                        "web_searches": prior_searches + extension["web_searches"],
                    },
                }
            calls.take(chunk)
            self._take(running, chunk)
            if time.perf_counter() - running.saved_at >= _SAVE_EVERY:
                await self._save(running)
        result = calls.finish()
        if not result and running.message.finish == "tool_calls":
            raise ToolError("The model ended with no tool call. Try another model.")
        if result and running.message.finish != "tool_calls":
            raise ToolError(
                "The model's tool call was cut off. No tool ran. "
                "Try a shorter task or another model."
            )
        if result and not body.get("tools"):
            raise ToolError(
                "The model requested a tool but none was enabled. "
                "Choose tools in this chat's settings."
            )
        return result

    async def _checkpoint(self, running: Running) -> None:
        await self._save(running)
        self.publish(
            running.chat_id,
            {
                "type": "answer",
                "message": message_view(running.message),
                "progress": running.progress,
            },
        )

    async def _with_tools(
        self, running: Running, body: dict[str, Any], server_ids: list[str]
    ) -> None:
        if self._tools is None:
            raise ToolError("Tools are unavailable. Restart Workbench and try again.")
        m = running.message
        async with self._tools.connect(server_ids) as session:
            if not session.definitions:
                raise ToolError("The selected servers offer no tools. Check them in Tools.")
            body["tools"] = session.definitions
            count = 0
            seen_ids: set[str] = set()
            for _ in range(8):
                start = len(m.content)
                reasoning_start = len(m.reasoning)
                calls = await self._stream(running, body)
                if not calls:
                    return
                count += len(calls)
                if count > 16:
                    raise ToolError(
                        "This answer reached sixteen tool calls. Ask for a smaller task."
                    )
                if any(call["id"] in seen_ids for call in calls):
                    raise ToolError("The model reused a tool-call ID. Try another model.")
                seen_ids.update(call["id"] for call in calls)
                # Validate the whole round before offering any action.
                steps = [session.prepare(call) for call in calls]
                turn: dict[str, Any] = {
                    "assistant": {
                        "role": "assistant",
                        "content": m.content[max(start, m.answer_from or 0) :] or None,
                        "tool_calls": calls,
                    },
                    "calls": steps,
                }
                m.tool_rounds.append(turn)
                if m.reasoning[reasoning_start:]:
                    turn["assistant"]["reasoning_content"] = m.reasoning[reasoning_start:]
                m.answer_from, m.reasoning_from = len(m.content), len(m.reasoning)
                for step in steps:
                    running.approvals[step["id"]] = asyncio.get_running_loop().create_future()
                deadline = time.perf_counter() + _APPROVAL_SECONDS
                running.progress = {"stage": "tool", "tool": "mcp", "phase": "approval"}
                await self._checkpoint(running)
                for step in steps:
                    try:
                        allowed = await asyncio.wait_for(
                            running.approvals[step["id"]], max(0, deadline - time.perf_counter())
                        )
                    except TimeoutError:
                        allowed = False
                        step["result"] = "Approval expired after 30 minutes. This call did not run."
                    finally:
                        running.approvals.pop(step["id"], None)
                    if allowed:
                        step["status"] = "running"
                        running.progress = {
                            "stage": "tool",
                            "tool": step["tool"],
                            "phase": "started",
                        }
                        # Commit intent before dispatch, so a crash cannot look
                        # like a call that is safe to repeat.
                        await self._checkpoint(running)
                        await session.execute(step)
                    else:
                        step.update(
                            status="declined",
                            result=step["result"]
                            or "The person declined this call. It did not run.",
                        )
                    await self._checkpoint(running)
                    if step["status"] == "uncertain":
                        raise ToolError(step["result"])
                body["messages"].extend(transcript([turn]))
                if m.searches:
                    # The person's requested search already ran. Continuing
                    # after a client tool must not force it again each round.
                    body.pop("web_search_options", None)
                running.progress = None
            raise ToolError("This answer reached eight tool rounds. Ask for a smaller task.")

    def _take(self, running: Running, chunk: dict[str, Any]) -> None:
        """One chunk of the gateway's stream into the answer, and out to every
        tab watching."""
        m = running.message
        extension = chunk.get("x_eugene_plexus") or {}
        searches = extension.get("web_searches")
        if isinstance(searches, int):
            m.searches = searches
        choices = chunk.get("choices") or []
        if not choices:
            progress = extension.get("progress")
            if isinstance(progress, dict):
                running.progress = progress
                said: dict[str, Any] = {"type": "progress", "id": m.id, "progress": progress}
                if progress.get("stage") == "tool" and progress.get("tool") == "web_search":
                    # A search starts or finishes here: what the model wrote
                    # before it is a draft, and its answer comes after. The
                    # last search's mark wins (workbench#1).
                    m.answer_from, m.reasoning_from = len(m.content), len(m.reasoning)
                    said["answerFrom"] = _js_length(m.content)
                    said["reasoningFrom"] = _js_length(m.reasoning)
                self.publish(running.chat_id, said)
            return
        choice = choices[0] or {}
        delta = choice.get("delta") or {}
        event: dict[str, Any] = {"type": "delta", "id": m.id}
        # Each piece says where it goes, so a tab that already has it (from
        # the snapshot it opened with, or a reload) can tell, and one that
        # missed some can tell that too and reload rather than show a gap.
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            event["reasoningAt"] = _js_length(m.reasoning)
            m.reasoning += reasoning
            event["reasoning"] = reasoning
        content = delta.get("content")
        if isinstance(content, str) and content:
            event["contentAt"] = _js_length(m.content)
            m.content += content
            event["content"] = content
        for annotation in delta.get("annotations") or []:
            citation = (annotation or {}).get("url_citation") or {}
            url = citation.get("url")
            if isinstance(url, str) and all(s["url"] != url for s in m.sources):
                m.sources.append({"url": url, "title": citation.get("title") or url})
                event["sources"] = m.sources
        finish = choice.get("finish_reason")
        if isinstance(finish, str):
            m.finish = finish
        if running.progress is not None and ("content" in event or "reasoning" in event):
            running.progress = None
        if set(event) - {"type", "id"}:
            self.publish(running.chat_id, event)
