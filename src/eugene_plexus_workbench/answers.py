"""Answers run here, not in a tab (W1).

The browser asks; this module runs the answer against the gateway, keeps
it as it arrives, and sends it to every tab watching that chat. So:

- a tab that opens mid-answer gets what has arrived so far, then the rest;
- **an answer keeps going when every tab is closed** (Troy, C3 call 2), and
  is saved as it arrives. Stop, a gateway safeguard or a failure can end it;
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
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .content import Keeper, let_go
from .hub import Hub, HubError
from .node_folders import Held
from .store import Message, Person, Store, interrupt_tools
from .tools import Calls, Signer, ToolError, Tools, attach_pictures, transcript

log = logging.getLogger(__name__)

_SAVE_EVERY = 1.0
#: A watching tab that falls this far behind is dropped; it reloads.
_QUEUE_DEPTH = 1000
_APPROVAL_SECONDS = 1800
#: How often a call a job site holds for the person's signature is sent again
#: while they sign at the machine (J14b).
_SIGN_POLL_SECONDS = 4.0
#: An answer's limits (J71): calls the rules allow do not count against the
#: calls a person is asked about, and Stop ends an answer at any time.
MAX_CALLS = 200
MAX_ROUNDS = 50
MAX_ASKED = 16
_MAX_REASONING_TOKENS = 2**53 - 1


class Refused(Exception):
    """A request that was stopped before it was sent, for a reason said in words
    a person reads (`message`): the answer fails with that, and nothing reached
    the model. Documents are the case (`documents_api.Blocked`)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


#: What `prepare` is given to say what is going on while it waits: it sets the
#: answer's progress (`None` clears it) and every tab watching is told.
Progress = Callable[[dict[str, Any] | None], None]


@dataclass
class Preparation:
    """What the step before an answer decided: the sources it starts with, what
    searching the person's saved documents came to (`Message.retrieval`), and, for a
    chat set to answer only from them, Workbench's own words in place of a model's
    when there is nothing to answer from (`reply`: the model is not asked)."""

    sources: list[dict[str, Any]] = field(default_factory=list)
    retrieval: dict[str, Any] | None = None
    reply: str | None = None
    #: What this turn was sent of the person's notes (`Message.memory_used`), or None.
    memory_used: dict[str, Any] | None = None
    #: Whether the model is offered `search_memory` (MV1): the answer then runs with tools.
    memory_tool: bool = False
    #: Whether the model is offered `search_chats` (MV2), likewise.
    chats_tool: bool = False
    #: Whether the model is offered `remember` (MV3b): set to Keep automatically, under a
    #: write policy that allows it.
    remember_tool: bool = False
    #: How a `reply` ends, as a message's `finish` says it.
    finish: str = "documents_only"


def turn_context(message: Message, interactions: bool = False) -> Any:
    """What an answer has read so far this turn, for `remember`'s write policy (MV3b);
    `interactions`: an app's surface put text in the chat's context."""
    from .memory_write import TurnContext

    used = message.memory_used or {}
    return TurnContext(
        web=bool(message.searches) or any(s.get("kind") == "web" for s in message.sources),
        documents=bool(message.retrieval)
        or any(s.get("kind") == "document" for s in message.sources),
        tools=any(
            call.get("serverId") not in ("memory", "memory-write")
            for turn in message.tool_rounds
            for call in turn.get("calls") or []
        ),
        # A memory server's results (MV5) are memory from elsewhere, read as past chats are.
        past_chats=bool((used.get("pastChats") or {}).get("exchanges"))
        or bool((used.get("memoryServer") or {}).get("memories")),
        interactions=interactions,
    )


def with_remember_hint(body: dict[str, Any]) -> None:
    """Say in the system message that `remember` is there (MV3b): measured, models seldom call
    a memory tool nothing mentions. Guidance, not a rule: the rules are code."""
    from .memory_suggest import REMEMBER_HINT

    messages = body.get("messages") or []
    if (
        messages
        and messages[0].get("role") == "system"
        and isinstance(messages[0].get("content"), str)
    ):
        messages[0] = {**messages[0], "content": f"{messages[0]['content']}\n\n{REMEMBER_HINT}"}
    else:
        messages.insert(0, {"role": "system", "content": REMEMBER_HINT})
        body["messages"] = messages


#: A step run before the request is built: it may wait (for a file to be read, a
#: search of the person's documents), raise `Refused`, and returns what the answer
#: starts with.
Prepare = Callable[[Progress], Awaitable[Preparation]]


_CITATION = re.compile(r"\[(\d{1,3})\]")


def mark_cited(message: Message) -> None:
    """Which of the passages an answer was sent are numbered in it: a passage's source
    says `cited`, and `retrieval.cited` lists the numbers. Only the markers are read;
    the answer is never rewritten, and a number the model made up is not a source."""
    passages = [s for s in message.sources if s.get("kind") == "document" and s.get("passage")]
    if not passages:
        return
    said = {int(n) for n in _CITATION.findall(answer_text(message))}
    for source in passages:
        source["cited"] = source.get("rank") in said
    if message.retrieval is not None:
        ranks = {s["rank"] for s in passages if isinstance(s.get("rank"), int)}
        message.retrieval["cited"] = sorted(said & ranks)


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


def _only_repetition(exc: BaseException) -> bool:
    if isinstance(exc, HubError):
        return exc.kind == "repetition"
    if isinstance(exc, BaseExceptionGroup):
        return all(_only_repetition(child) for child in exc.exceptions)
    return False


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


def sources_view(message: Message) -> list[dict[str, Any]]:
    """A message's sources as the page reads them: where the answer cites a web
    source (`spans`, Python indexes into `content`) becomes `marks`, the same
    places as the page counts them (UTF-16)."""
    out: list[dict[str, Any]] = []
    for source in message.sources:
        spans = source.get("spans")
        if not spans:
            out.append(source)
            continue
        view = {k: v for k, v in source.items() if k != "spans"}
        view["marks"] = [
            [_js_length(message.content[:a]), _js_length(message.content[:b])] for a, b in spans
        ]
        out.append(view)
    return out


def cite(message: Message, source: dict[str, Any], citation: dict[str, Any], base: int) -> None:
    """Where the answer cites `source`, from a `url_citation` annotation.

    Its `start_index` is a Python index into the text of the one request that
    wrote it (the gateway's `server_tools.citations`), so it counts from
    `base`, where that request's text began. But the gateway counts its rounds'
    text joined with a blank line it never streams (gateway#16, its
    `_final`), so an answer written after a draft is off by two a round. So
    the place is where the text holds the address, at the occurrence nearest
    the gateway's index; a Markdown link around it is cited whole, as the
    gateway cites it. Where the text does not hold the address, there is no
    place: none is made up."""
    start = citation.get("start_index")
    url = str(source.get("url") or "")
    if not isinstance(start, int) or start < 0 or not url:
        return
    text = message.content
    want = base + start
    best: tuple[int, str] | None = None
    for spelled in dict.fromkeys((url, url.rstrip("/"))):
        if not spelled:
            continue
        at = text.find(spelled, base)
        while at >= 0:
            if best is None or abs(at - want) < abs(best[0] - want):
                best = (at, spelled)
            at = text.find(spelled, at + 1)
    if best is None:
        return
    a, b = best[0], best[0] + len(best[1])
    if text[max(base, a - 2) : a] == "](":
        opening = text.rfind("[", base, a - 2)
        if opening >= 0 and text[b : b + 1] == ")":
            a, b = opening, b + 1
    spans = source.setdefault("spans", [])
    if [a, b] not in spans and len(spans) < 20:
        spans.append([a, b])


def message_view(message: Message) -> dict[str, Any]:
    """A message as the page reads it."""
    return {
        "id": message.id,
        "seq": message.seq,
        "role": message.role,
        "content": message.content,
        "reasoning": message.reasoning,
        "reasoningTokens": message.reasoning_tokens,
        "reasoningSeconds": message.reasoning_seconds,
        "attachments": message.attachments,
        "status": message.status,
        "error": message.error,
        "sources": sources_view(message),
        "searches": message.searches,
        "searchSuggestions": message.search_suggestions,
        "search": message.search,
        "model": message.model,
        "finish": message.finish,
        "createdAt": message.created_at,
        "finishedAt": message.finished_at,
        "answerFrom": _js_offset(message.content, message.answer_from),
        "reasoningFrom": _js_offset(message.reasoning, message.reasoning_from),
        "toolRounds": message.tool_rounds,
        "outputCap": message.output_cap,
        "retrieval": message.retrieval,
        "memoryUsed": message.memory_used,
        "branchName": message.branch_name,
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
    person: Person | None = None
    #: What each call kept of what its tool returned (content.py).
    keepers: list[Keeper] = field(default_factory=list)
    #: Where the text of the request being streamed begins in the message.
    request_from: int = 0
    reasoning_at: float | None = None
    request_reasoning_tokens: int | None = None
    prior_reasoning_tokens: int = 0
    reasoning_tokens_complete: bool = True


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
        #: Told the chat's id when an answer ends, whatever its outcome (MV2: its
        #: exchange goes in the person's index). Never in an answer's way.
        self.on_finished: Callable[[str], None] | None = None

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
        folder_grants: list[str] | None = None,
        person: Person | None = None,
        prepare: Prepare | None = None,
    ) -> None:
        """Run the answer `message` holds. `build` makes the request body
        (in a thread: it reads the attachments' bytes). `prepare`, if given, runs
        first, in the answer's own task: what it waits for does not hold up the
        request that started the answer, and what it refuses fails the answer."""
        if chat_id in self._running:
            raise RuntimeError(f"an answer is already being written in chat {chat_id}")
        running = Running(chat_id=chat_id, message=message, person=person)
        self._running[chat_id] = running
        running.task = asyncio.create_task(
            self._run(running, build, tool_servers or [], folder_grants or [], prepare),
            name=f"answer-{message.id}",
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
        self._reasoning_tick(running, end=final)
        m = running.message
        values: dict[str, Any] = {
            "content": m.content,
            "reasoning": m.reasoning,
            "reasoning_tokens": m.reasoning_tokens,
            "reasoning_seconds": m.reasoning_seconds,
            "sources": m.sources,
            "searches": m.searches,
            "search_suggestions": m.search_suggestions,
            "answer_from": m.answer_from,
            "reasoning_from": m.reasoning_from,
            "tool_rounds": m.tool_rounds,
            "retrieval": m.retrieval,
            "memory_used": m.memory_used,
            "model": m.model,
        }
        if final:
            values.update(
                status=m.status,
                error=m.error,
                finish=m.finish,
                finished_at=m.finished_at,
                output_cap=m.output_cap,
            )
        clean = await self._store.filter_retained(
            m.id, memory_used=m.memory_used, tool_rounds=m.tool_rounds
        )
        m.memory_used = clean["memory_used"]
        # Keep live call objects: tool tasks still hold them while an approval waits.
        for before, after in zip(m.tool_rounds, clean["tool_rounds"], strict=True):
            for call, filtered in zip(before.get("calls", []), after.get("calls", []), strict=True):
                if call != filtered:
                    call.clear()
                    call.update(filtered)
        values.update(memory_used=m.memory_used, tool_rounds=m.tool_rounds)
        await self._store.update_message(m.id, **values)
        running.saved_at = time.perf_counter()

    async def _run(
        self,
        running: Running,
        build: Callable[[], Any],
        tool_servers: list[str],
        folder_grants: list[str],
        prepare: Prepare | None = None,
    ) -> None:
        from .person_context import acting_as

        person = running.person
        chat = await self._store.chat(running.chat_id) if person is None else None
        with acting_as(
            person.sub if person else (chat.owner if chat else ""),
            person.session_id if person else None,
        ):
            await self._run_answer(running, build, tool_servers, folder_grants, prepare)

    async def _run_answer(
        self,
        running: Running,
        build: Callable[[], Any],
        tool_servers: list[str],
        folder_grants: list[str],
        prepare: Prepare | None = None,
    ) -> None:
        m = running.message
        chat_id = running.chat_id

        def progress(value: dict[str, Any] | None) -> None:
            running.progress = value
            if value is not None:
                self.publish(chat_id, {"type": "progress", "id": m.id, "progress": value})

        memory_tool = chats_tool = remember_tool = False
        try:
            if prepare is not None:
                prepared = await prepare(progress)
                memory_tool = prepared.memory_tool
                chats_tool = prepared.chats_tool
                remember_tool = prepared.remember_tool
                running.progress = None
                told: dict[str, Any] = {}
                if prepared.sources:
                    m.sources = [*prepared.sources, *m.sources]
                    told["sources"] = m.sources
                if prepared.retrieval is not None:
                    m.retrieval = prepared.retrieval
                    told["retrieval"] = m.retrieval
                if prepared.memory_used is not None:
                    m.memory_used = prepared.memory_used
                    told["memoryUsed"] = m.memory_used
                if prepared.reply is not None:
                    # Nothing to answer from, and the chat answers only from the
                    # person's documents: these are Workbench's words, not a model's.
                    m.model, m.finish = None, prepared.finish
                    told["contentAt"] = 0
                    told["content"] = m.content = prepared.reply
                if told:
                    self.publish(chat_id, {"type": "delta", "id": m.id, **told})
                    await self._save(running)
                if prepared.reply is not None:
                    m.status = "done"
                    return
            body = await asyncio.to_thread(build)
            context = await self._store.interactions.model_context(chat_id)
            if context:
                # Ordinary user context, before the current explicit request.
                body["messages"][-1:-1] = context
            if tool_servers or folder_grants or memory_tool or chats_tool or remember_tool:
                await self._with_tools(
                    running,
                    body,
                    tool_servers,
                    folder_grants,
                    memory_tool=memory_tool,
                    chats_tool=chats_tool,
                    remember_tool=remember_tool,
                    interactions=bool(context),
                )
            else:
                await self._stream(running, body)
            m.status = "done"
        except asyncio.CancelledError:
            m.status = "interrupted" if running.shutting_down else "stopped"
        except HubError as exc:
            m.status, m.error = "stopped" if exc.kind == "repetition" else "failed", exc.message
            if exc.kind == "repetition":
                m.finish = "repetition_detected"
        except Refused as exc:
            m.status, m.error = "failed", exc.message
        except ToolError as exc:
            m.status, m.error = "failed", str(exc)
        except Exception as exc:
            if _only_repetition(exc):
                # MCP task groups can wrap the gateway's intentional stop.
                # A mixed group with a real tool/transport failure stays failed.
                m.status, m.error, m.finish = "stopped", _tool_problem(exc), "repetition_detected"
            elif tool_servers or folder_grants:
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
            with contextlib.suppress(Exception):
                mark_cited(m)  # never in the way of saving what was written
            try:
                await asyncio.shield(self._save(running, final=True))
                # A file a stopped call kept and never named: no answer shows it.
                with contextlib.suppress(Exception):
                    await asyncio.shield(let_go(running.keepers, m.tool_rounds))
            finally:
                self._running.pop(chat_id, None)
                self.publish(chat_id, {"type": "done", "message": message_view(m)})
                if self.on_finished is not None:
                    with contextlib.suppress(Exception):
                        self.on_finished(chat_id)

    async def _stream(self, running: Running, body: dict[str, Any]) -> list[dict[str, Any]]:
        body = {
            **body,
            "stream_options": {**body.get("stream_options", {}), "include_usage": True},
        }
        if (
            any(message.get("role") == "tool" for message in body.get("messages", []))
            and self._tools is not None
        ):
            history = await self._store.messages(running.chat_id, via=running.message.id)
            rounds = [
                turn
                for message in history
                for turn in (
                    running.message.tool_rounds
                    if message.id == running.message.id
                    else message.tool_rounds
                )
            ]
            if any(
                block.get("type") == "image"
                for turn in rounds
                for call in turn["calls"]
                for block in call.get("content", [])
            ):
                try:
                    listing = await self._hub.models()
                except HubError:
                    listing = {}
                vision = any(
                    model.get("id") == body["model"]
                    and (model.get("x_eugene_plexus") or {}).get("image_input") is True
                    for model in listing.get("data", [])
                )
                chat = await self._store.chat(running.chat_id)
                if chat is not None:
                    body["messages"] = await attach_pictures(
                        body["messages"],
                        rounds,
                        self._store,
                        self._tools.data_dir,
                        chat.owner,
                        chat.id,
                        image_input=vision,
                    )
        calls = Calls()
        running.message.finish = None
        prior_searches = running.message.searches
        # Where this request's text begins: its citations count from here.
        running.request_from = len(running.message.content)
        running.prior_reasoning_tokens = running.message.reasoning_tokens or 0
        running.message.reasoning_tokens = None
        running.request_reasoning_tokens = None
        try:
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
        finally:
            self._reasoning_tick(running, end=True)
            if running.request_reasoning_tokens is None:
                running.reasoning_tokens_complete = False
                running.message.reasoning_tokens = None
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
        self,
        running: Running,
        body: dict[str, Any],
        server_ids: list[str],
        folder_ids: list[str],
        *,
        memory_tool: bool = False,
        chats_tool: bool = False,
        remember_tool: bool = False,
        interactions: bool = False,
    ) -> None:
        if self._tools is None:
            raise ToolError("Tools are unavailable. Restart Workbench and try again.")
        m = running.message
        chat = await self._store.chat(running.chat_id)
        person = running.person or (await self._store.person(chat.owner) if chat else None)
        async with self._tools.connect(server_ids, person, folder_ids) as session:
            if memory_tool and person is not None:
                session.add_memory_search(running.chat_id, body.get("model"))
            if chats_tool and person is not None:
                session.add_chats_search(running.chat_id, body.get("model"))
            if remember_tool and person is not None:
                session.add_remember(
                    running.chat_id,
                    body.get("model"),
                    lambda: turn_context(running.message, interactions),
                )
                with_remember_hint(body)
            if not session.definitions:
                raise ToolError("The selected servers offer no tools. Check them in Tools.")
            body["tools"] = session.definitions
            count = asked = 0
            seen_ids: set[str] = set()
            for _ in range(MAX_ROUNDS):
                start = len(m.content)
                reasoning_start = len(m.reasoning)
                calls = await self._stream(running, body)
                if not calls:
                    return
                count += len(calls)
                if count > MAX_CALLS:
                    raise ToolError(
                        f"This answer reached {MAX_CALLS} tool calls. Ask for a smaller task."
                    )
                if any(call["id"] in seen_ids for call in calls):
                    raise ToolError("The model reused a tool-call ID. Try another model.")
                seen_ids.update(call["id"] for call in calls)
                # Validate the whole round before offering any action.
                steps = [session.prepare(call) for call in calls]
                asked += sum(1 for step in steps if step["ask"])
                if asked > MAX_ASKED:
                    raise ToolError(
                        f"This answer reached {MAX_ASKED} calls that need your approval. Ask "
                        "for a smaller task."
                    )
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
                    if step["ask"]:
                        running.approvals[step["id"]] = asyncio.get_running_loop().create_future()
                deadline = time.perf_counter() + _APPROVAL_SECONDS
                if running.approvals:
                    running.progress = {"stage": "tool", "tool": "mcp", "phase": "approval"}
                await self._checkpoint(running)
                for step in steps:
                    if not step["ask"]:
                        # The site's rules allow it: it runs without asking (J70).
                        allowed = True
                    else:
                        try:
                            allowed = await asyncio.wait_for(
                                running.approvals[step["id"]],
                                max(0, deadline - time.perf_counter()),
                            )
                        except TimeoutError:
                            allowed = False
                            step["result"] = (
                                "Approval expired after 30 minutes. This call did not run."
                            )
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
                        # What it returns is kept as the chat's files before the
                        # checkpoint below says the answer has them.
                        keeper = (
                            Keeper(
                                self._store,
                                self._tools.data_dir,
                                chat.owner,
                                running.chat_id,
                                m.id,
                            )
                            if chat is not None
                            else None
                        )
                        if keeper is not None:
                            running.keepers.append(keeper)
                        await session.execute(step, self._signer(running), keeper)
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
            raise ToolError(
                f"This answer reached {MAX_ROUNDS} tool rounds. Ask for a smaller task."
            )

    def _signer(self, running: Running) -> Signer:
        """What waits for the person when a job site holds a call for their
        own signature (J14b, J86). The site is the authority: the person signs
        on the machine's own page or with a passkey here, and the call is sent
        again every few seconds, or at once when the page says it signed, until
        the site runs it. Declining, or 30 minutes, ends the wait; nothing ran."""
        started: dict[str, float] = {}

        async def sign(step: dict[str, Any], held: Held) -> dict[str, Any] | None:
            ident = str(held.held["id"])
            shown = {
                key: held.held.get(key)
                for key in ("id", "kind", "minutes", "words", "expiresAt", "approvePage")
            }
            shown["message"] = held.message
            future = running.approvals.get(step["id"])
            # A decision can arrive while the site is being polled. Keep it for
            # the same request, but never reuse approval for changed content.
            # A refusal cancels the tool call even if its held request changed.
            declined = future is not None and future.done() and not future.result()
            if future is None or (step.get("held") != shown and not declined):
                step["held"] = shown
                step["status"] = "signing"
                started.setdefault(step["id"], time.perf_counter())
                future = asyncio.get_running_loop().create_future()
                running.approvals[step["id"]] = future
                running.progress = {"stage": "tool", "tool": step["tool"], "phase": "approval"}
                await self._checkpoint(running)
            left = _APPROVAL_SECONDS - (time.perf_counter() - started[step["id"]])
            if left <= 0:
                running.approvals.pop(step["id"], None)
                step["result"] = "Signing expired after 30 minutes. This call did not run."
                return None
            try:
                signed = await asyncio.wait_for(
                    asyncio.shield(future), min(_SIGN_POLL_SECONDS, left)
                )
            except TimeoutError:
                # Not signed here yet: ask the site again, which knows whether
                # it was signed at the machine.
                return {"held": ident}
            running.approvals.pop(step["id"], None)
            if not signed:
                return None
            step["status"] = "running"
            running.progress = {"stage": "tool", "tool": step["tool"], "phase": "started"}
            await self._checkpoint(running)
            return {"held": ident}

        return sign

    def _reasoning_tick(self, running: Running, *, end: bool = False) -> None:
        if running.reasoning_at is None:
            return
        now = time.perf_counter()
        running.message.reasoning_seconds = (running.message.reasoning_seconds or 0) + max(
            0, now - running.reasoning_at
        )
        running.reasoning_at = None if end else now

    def _take(self, running: Running, chunk: dict[str, Any]) -> None:
        """One chunk of the gateway's stream into the answer, and out to every
        tab watching."""
        m = running.message
        self._reasoning_tick(running)
        usage = chunk.get("usage")
        details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
        tokens = details.get("reasoning_tokens") if isinstance(details, dict) else None
        if (
            type(tokens) is int
            and 0 <= tokens <= _MAX_REASONING_TOKENS - running.prior_reasoning_tokens
        ):
            running.request_reasoning_tokens = tokens
            if running.reasoning_tokens_complete:
                m.reasoning_tokens = running.prior_reasoning_tokens + tokens
        elif isinstance(details, dict) and "reasoning_tokens" in details:
            running.request_reasoning_tokens = None
            m.reasoning_tokens = None
        extension = chunk.get("x_eugene_plexus") or {}
        searches = extension.get("web_searches")
        if isinstance(searches, int):
            m.searches = searches
        suggestions = extension.get("search_suggestions")
        if isinstance(suggestions, list):
            # The final chunk of each request carries that request's own list;
            # a tool round makes another request, so these add up (GS4).
            added = [s for s in suggestions if isinstance(s, str) and s]
            if added:
                m.search_suggestions = [*m.search_suggestions, *added]
                self.publish(
                    running.chat_id,
                    {"type": "delta", "id": m.id, "searchSuggestions": m.search_suggestions},
                )
        choices = chunk.get("choices") or []
        if not choices:
            progress = extension.get("progress")
            if isinstance(progress, dict):
                if progress.get("stage") in ("tool", "prompt"):
                    self._reasoning_tick(running, end=True)
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
            if m.reasoning_seconds is not None:
                self.publish(
                    running.chat_id,
                    {
                        "type": "delta",
                        "id": m.id,
                        "reasoningTokens": m.reasoning_tokens,
                        "reasoningSeconds": m.reasoning_seconds,
                    },
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
            if running.reasoning_at is None:
                running.reasoning_at = time.perf_counter()
                m.reasoning_seconds = m.reasoning_seconds or 0.0
            event["reasoningAt"] = _js_length(m.reasoning)
            m.reasoning += reasoning
            event["reasoning"] = reasoning
        content = delta.get("content")
        if content or delta.get("tool_calls") or choice.get("finish_reason"):
            self._reasoning_tick(running, end=True)
        if isinstance(content, str) and content:
            event["contentAt"] = _js_length(m.content)
            m.content += content
            event["content"] = content
        for annotation in delta.get("annotations") or []:
            citation = (annotation or {}).get("url_citation") or {}
            url = citation.get("url")
            if not isinstance(url, str):
                continue
            source = next((s for s in m.sources if s.get("url") == url), None)
            if source is None:
                source = {"kind": "web", "url": url, "title": citation.get("title") or url}
                m.sources.append(source)
            cite(m, source, citation, running.request_from)
            event["sources"] = sources_view(m)
        finish = choice.get("finish_reason")
        if isinstance(finish, str):
            m.finish = finish
            # What capped this request, from its final frame: each tool round
            # is a request of its own, and the last one's is the answer's.
            cap = extension.get("output_cap")
            m.output_cap = (
                {"tokens": cap["tokens"], "source": cap["source"]}
                if isinstance(cap, dict)
                and isinstance(cap.get("tokens"), int)
                and cap.get("source") in ("request", "profile", "install")
                else None
            )
        if (
            running.progress is not None
            and running.progress.get("stage") != "generating"
            and ("content" in event or "reasoning" in event)
        ):
            running.progress = None
        if set(event) - {"type", "id"}:
            event["reasoningTokens"] = m.reasoning_tokens
            event["reasoningSeconds"] = m.reasoning_seconds
            self.publish(running.chat_id, event)
