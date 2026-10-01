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
from .store import Message, Store

log = logging.getLogger(__name__)

_SAVE_EVERY = 1.0
#: A watching tab that falls this far behind is dropped; it reloads.
_QUEUE_DEPTH = 1000


def _js_length(text: str) -> int:
    """A string's length as the page counts it: UTF-16 code units, so an
    emoji is 2 there and 1 in Python, and an offset must agree with the page."""
    return len(text.encode("utf-16-le")) // 2


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

    def __init__(self, store: Store, hub: Hub) -> None:
        self._store = store
        self._hub = hub
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

    def start(self, chat_id: str, message: Message, build: Callable[[], Any]) -> None:
        """Run the answer `message` holds. `build` makes the request body
        (in a thread: it reads the attachments' bytes)."""
        if chat_id in self._running:
            raise RuntimeError(f"an answer is already being written in chat {chat_id}")
        running = Running(chat_id=chat_id, message=message)
        self._running[chat_id] = running
        running.task = asyncio.create_task(self._run(running, build), name=f"answer-{message.id}")

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
        }
        if final:
            values.update(
                status=m.status, error=m.error, finish=m.finish, finished_at=m.finished_at
            )
        await self._store.update_message(m.id, **values)
        running.saved_at = time.perf_counter()

    async def _run(self, running: Running, build: Callable[[], Any]) -> None:
        m = running.message
        chat_id = running.chat_id
        try:
            body = await asyncio.to_thread(build)
            async for chunk in self._hub.stream_chat(body):
                self._take(running, chunk)
                now = time.perf_counter()
                if now - running.saved_at >= _SAVE_EVERY:
                    await self._save(running)
            m.status = "done"
        except asyncio.CancelledError:
            m.status = "interrupted" if running.shutting_down else "stopped"
        except HubError as exc:
            m.status, m.error = "failed", exc.message
        except Exception as exc:
            log.exception("an answer in chat %s failed", chat_id)
            m.status, m.error = "failed", f"Workbench failed while writing this answer: {exc}"
        finally:
            m.finished_at = time.time()
            running.progress = None
            try:
                await asyncio.shield(self._save(running, final=True))
            finally:
                self._running.pop(chat_id, None)
                self.publish(chat_id, {"type": "done", "message": message_view(m)})

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
                self.publish(
                    running.chat_id, {"type": "progress", "id": m.id, "progress": progress}
                )
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
