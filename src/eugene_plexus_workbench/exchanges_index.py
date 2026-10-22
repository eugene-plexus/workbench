"""A person's past chats in their knowledge index (MV2, `workbench-memory-v2.md` sections 14-15).

An **exchange** is a person's message and the answer that follows it on the chat's path (what
the chat shows): what they asked, whole up to `QUESTION_CHARS`, and the answer's opening, up
to `OPENING_CHARS`, with the day it was asked. Each is one document in the person's index, in
the scope `index.EXCHANGES`, by the answer's id, its `source` the chat's id. Reasoning, tool
rounds and attachments are not part of it.

**The store is the truth; the index is kept in step, and checked**, as for notes
(`notes_index`):

- an answer that ends, a version chosen and a chat renamed `touch` the chat; a background
  task syncs each touched chat once: its path's exchanges are put at their digests, and the
  ones the index holds that are no longer on the path are taken out;
- a chat that is deleted leaves the index **first** (`remove_chat`), so a failure to remove
  its words fails the delete and nothing is deleted;
- the backfill is `reconcile`, at start-up for everyone with chats, newest chat first. Each
  chat's **signature** (`Store.chat_signatures`) is kept in the index's `meta` when it is
  synced, so a chat that has not moved is skipped without reading it, and a restart resumes;
- nothing shown or sent is read from the index: `past_chats` takes the text of every exchange
  it finds from the store at that moment.

Logs carry counts and causes, never a chat's words.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import itertools
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .knowledge import chunk, extract
from .knowledge.index import EXCHANGES
from .store import Message, Store, path

log = logging.getLogger(__name__)

#: What of an exchange is indexed: the person's message, and the answer's opening, cut at
#: the last paragraph or sentence end after `OPENING_MIN` characters when there is one.
QUESTION_CHARS = 4000
OPENING_CHARS = 1500
OPENING_MIN = 600
#: Between a chat's title and the date in a passage's heading.
SEPARATOR = " · "
ELLIPSIS = "…"
RETRY_SECONDS = 30.0

_END = re.compile(r"(?:\n\s*\n|[.!?\u3002\uff01\uff1f][\"')\]]?\s)")


@dataclass(frozen=True)
class Exchange:
    """One exchange as it is indexed: by the answer's id."""

    id: str
    chat_id: str
    question_id: str
    title: str
    date: str
    question: str
    answer: str


def day(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).strftime("%Y-%m-%d")


def cut(text: str, limit: int) -> str:
    """`text` at most `limit` characters, `…` where it was cut."""
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + ELLIPSIS


def opening(text: str, limit: int = OPENING_CHARS, floor: int = OPENING_MIN) -> str:
    """The first `limit` characters of an answer, ended at its last paragraph or sentence
    end after `floor` when it has one there."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    ends = [m.end() for m in _END.finditer(head) if m.end() >= floor]
    return (head[: ends[-1]].rstrip() if ends else head[: limit - 1].rstrip()) + ELLIPSIS


def final_text(content: str, answer_from: int | None) -> str:
    """An answer's final text: after its last web search, when one ran."""
    if answer_from is not None and 0 <= answer_from <= len(content):
        tail = content[answer_from:]
        if tail.strip():
            return tail
    return content


def exchanges_of(chat_id: str, title: str, tree: list[Message]) -> list[Exchange]:
    """The exchanges of a chat's path: each answer that is not running, after a message of
    the person's that has some text."""
    shown = path(tree)
    out: list[Exchange] = []
    for before, answer in itertools.pairwise(shown):
        if (
            answer.role != "assistant"
            or answer.status == "running"
            or before.role != "user"
            or answer.parent_id != before.id
            or not before.content.strip()
        ):
            continue
        out.append(
            Exchange(
                id=answer.id,
                chat_id=chat_id,
                question_id=before.id,
                title=title,
                date=day(before.created_at),
                question=cut(before.content, QUESTION_CHARS),
                answer=opening(final_text(answer.content, answer.answer_from)),
            )
        )
    return out


def digest(exchange: Exchange) -> str:
    """What names a version of an exchange in the index."""
    shape = [exchange.title, exchange.date, exchange.question, exchange.answer]
    return hashlib.sha256(json.dumps(shape, ensure_ascii=False).encode("utf-8")).hexdigest()


def heading(exchange: Exchange) -> str:
    return f"{' '.join(exchange.title.split())}{SEPARATOR}{exchange.date}"[: chunk.MAX_PATH]


def passages(exchange: Exchange) -> list[chunk.Passage]:
    """The exchange as the index holds it: the person's message, cut as notes' bodies are,
    then the answer's opening as one passage."""
    head = heading(exchange)
    question = exchange.question
    extracted = extract.Extracted(
        kind="markdown",
        parts=[extract.Part(question)],
        lines=extract.line_count(question),
        headings=extract.markdown_headings(question),
    )
    out: list[chunk.Passage] = []
    for piece in chunk.chunk(extracted) or []:
        out.append(
            chunk.Passage(
                ord=len(out),
                text=piece.text,
                heading_path=head,
                page_start=None,
                page_end=None,
                line_start=piece.line_start,
                line_end=piece.line_end,
                char_start=piece.char_start,
                char_end=piece.char_end,
                tokens_est=piece.tokens_est,
            )
        )
    if not out:
        out.append(_whole(0, question, head))
    if exchange.answer.strip():
        out.append(_whole(len(out), exchange.answer, f"{head} > Answer"))
    return out


def _whole(ordinal: int, text: str, head: str) -> chunk.Passage:
    return chunk.Passage(
        ord=ordinal,
        text=text,
        heading_path=head,
        page_start=None,
        page_end=None,
        line_start=None,
        line_end=None,
        char_start=0,
        char_end=len(text),
        tokens_est=max(1, len(text) // 3),
    )


@dataclass
class Progress:
    """How far a person's backfill has come."""

    total: int = 0
    done: int = 0
    running: bool = False


class ExchangesIndex:
    """Keeps every person's past chats in their index. One per Workbench; `saved` is the
    people's `SavedDocuments`, whose indexes and meaning worker it uses."""

    def __init__(self, store: Store, saved: Any) -> None:
        self.store = store
        self.saved = saved
        #: Chats to sync, `(owner, chat id)`, in the order they were touched; an owner of
        #: None is the chat's own, read when it is synced.
        self._touched: dict[tuple[str | None, str], None] = {}
        self._failed: dict[tuple[str | None, str], None] = {}
        self._wake = asyncio.Event()
        self.progress: dict[str, Progress] = {}
        self._worker: asyncio.Task[None] | None = None
        self._backfill: asyncio.Task[None] | None = None
        self._refills: set[asyncio.Task[None]] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, chat_id: str) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    def _meaning(self, owner: str) -> None:
        meaning = getattr(self.saved, "meaning", None)
        if meaning is not None:
            meaning.wake(owner)

    # --- keeping one chat in step ------------------------------------------

    def touch(self, owner: str | None, chat_id: str) -> None:
        """This chat's exchanges may have changed: sync it soon. Never raises, never waits."""
        self._touched[(owner, chat_id)] = None
        self._wake.set()
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            with contextlib.suppress(RuntimeError):  # no loop: a test without one
                self._worker = asyncio.get_running_loop().create_task(
                    self._work(), name="exchanges-index"
                )

    async def sync_chat(
        self, owner: str, chat_id: str, signature: str | None = None
    ) -> tuple[int, int]:
        async with self.lock(chat_id):
            return await self._sync_chat(owner, chat_id, signature)

    async def _sync_chat(
        self, owner: str, chat_id: str, signature: str | None = None
    ) -> tuple[int, int]:
        """Make the person's index hold exactly this chat's exchanges as the store has them:
        `(put, removed)`. `signature` is the chat's as read before its rows (read here when
        not given). Raises what the index raises."""
        if signature is None:
            signature = (await self.store.chat_signatures(owner, chat_id)).get(chat_id)
        chat = await self.store.chat(chat_id)
        mine = chat is not None and chat.owner == owner
        index = self.saved.index(owner, create=mine)
        if index is None:
            return 0, 0
        if not mine or signature is None:
            return 0, await index.delete_exchanges(chat_id=chat_id)
        assert chat is not None
        wanted = exchanges_of(chat.id, chat.title, await self.store.exchange_tree(chat.id))
        held = await index.exchange_digests(chat.id)
        put = 0
        for exchange in wanted:
            sha = digest(exchange)
            if held.get(exchange.id, ("", ""))[1] != sha:
                await index.put_exchange(
                    exchange.id, chat.id, sha, exchange.title, passages(exchange)
                )
                put += 1
        keep = {e.id for e in wanted}
        removed = await index.delete_exchanges(
            chat_id=chat.id, ids=[i for i in held if i not in keep], signature=signature
        )
        if await self.store.chat(chat.id) is None:
            # Deleted while it was being put: what was put goes too.
            removed += await index.delete_exchanges(chat_id=chat.id)
        if put:
            self._meaning(owner)
        return put, removed

    async def remove_chat(self, owner: str, chat_id: str) -> int:
        """Take a chat's exchanges out of the person's index, words and all, before the chat
        goes. Raises when it cannot: the delete that asked must then not go on."""
        for key in ((owner, chat_id), (None, chat_id)):
            self._touched.pop(key, None)
            self._failed.pop(key, None)
        index = self.saved.index(owner, create=False)
        if index is None:
            return 0
        return int(await index.delete_exchanges(chat_id=chat_id))

    async def remove_exchanges(self, owner: str, chat_id: str, ids: list[str]) -> int:
        """Remove only this branch's exchanges; caller holds the chat's sync lock."""
        index = self.saved.index(owner, create=False)
        if index is None:
            return 0
        return int(await index.delete_exchanges(ids=ids))

    async def _work(self) -> None:
        """Sync each touched chat once; retry the ones that failed every `RETRY_SECONDS`."""
        while True:
            if not self._touched:
                timeout = RETRY_SECONDS if self._failed else None
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout)
                self._wake.clear()
                if not self._touched and self._failed:
                    self._touched.update(self._failed)
                    self._failed.clear()
                continue
            key = next(iter(self._touched))
            self._touched.pop(key)
            try:
                owner, chat_id = key
                if owner is None:
                    chat = await self.store.chat(chat_id)
                    if chat is None:
                        continue  # deleted: its exchanges went before it did
                    owner = chat.owner
                await self.sync_chat(owner, chat_id)
            except Exception as exc:
                self._failed[key] = None
                log.warning(
                    "a chat's exchanges could not be put in its person's index: %s: %s",
                    type(exc).__name__,
                    exc,
                )

    # --- the backfill ---------------------------------------------------------

    async def reconcile(self, owner: str) -> tuple[int, int]:
        """Make the person's index hold exactly the exchanges of their chats, newest chat
        first, skipping a chat whose signature has not moved: `(put, removed)`. It is the
        backfill, and resumes where a restart stopped it. Raises what the index raises."""
        signatures = await self.store.chat_signatures(owner)
        index = self.saved.index(owner, create=bool(signatures))
        if index is None:
            return 0, 0
        kept = await index.signatures()
        held = await index.exchange_digests()
        todo = [c for c, s in signatures.items() if kept.get(c) != s]
        progress = self.progress[owner] = Progress(
            total=len(signatures), done=len(signatures) - len(todo), running=bool(todo)
        )
        put = removed = 0
        try:
            for chat_id in todo:
                try:
                    p, r = await self.sync_chat(owner, chat_id, signatures[chat_id])
                except Exception as exc:
                    # One chat that cannot be put does not hold up the rest: it is
                    # retried with the touched ones.
                    self._failed[(owner, chat_id)] = None
                    self._wake.set()
                    log.warning(
                        "a chat's exchanges could not be put in its person's index: %s: %s",
                        type(exc).__name__,
                        exc,
                    )
                    p = r = 0
                put, removed = put + p, removed + r
                progress.done += 1
                await asyncio.sleep(0)
            if self._failed:
                self._ensure_worker()
            gone = ({c for c, _ in held.values()} | set(kept)) - set(signatures)
            for chat_id in gone:
                if await self.store.chat(chat_id) is None:
                    removed += await index.delete_exchanges(chat_id=chat_id)
        finally:
            progress.running = False
        return put, removed

    async def reconcile_all(self) -> None:
        """Start-up: everyone with chats, one after another. Never raises."""
        owners = set(await self.store.chat_people())
        # Someone whose last chat went while Workbench was stopped still has exchanges.
        for directory in getattr(getattr(self.saved, "indexes", None), "person_dirs", list)():
            owner = self.saved.indexes.owner_of(directory)
            if owner is not None:
                owners.add(owner)
        put = removed = failed = 0
        started = time.perf_counter()
        for owner in sorted(owners):
            try:
                p, r = await self.reconcile(owner)
                put, removed = put + p, removed + r
            except Exception as exc:
                failed += 1
                log.warning(
                    "one person's past chats could not be checked against their index: %s: %s",
                    type(exc).__name__,
                    exc,
                )
        if put or removed or failed:
            log.info(
                "past chats in people's indexes after the restart: %d exchange(s) put, %d "
                "removed, %d person(s) not checked, in %.1f s",
                put,
                removed,
                failed,
                time.perf_counter() - started,
            )

    def refill(self, owner: str) -> None:
        """The person's index was made again (Rebuild search): put every chat back, in
        the background. Never raises."""

        async def go() -> None:
            try:
                await self.reconcile(owner)
            except Exception as exc:
                log.warning(
                    "one person's past chats could not be put in their new index: %s: %s",
                    type(exc).__name__,
                    exc,
                )

        with contextlib.suppress(RuntimeError):
            self._refills.add(asyncio.get_running_loop().create_task(go(), name="exchanges-refill"))
        self._refills = {t for t in self._refills if not t.done()}

    def status(self, owner: str) -> dict[str, Any]:
        progress = self.progress.get(owner)
        if progress is None or not progress.running:
            return {"running": False}
        return {"running": True, "chats": progress.total, "done": progress.done}

    def start(self) -> None:
        """Backfill everyone in the background, so a large store does not hold up boot."""
        self._backfill = asyncio.create_task(self.reconcile_all(), name="exchanges-backfill")

    async def aclose(self) -> None:
        for task in (self._backfill, self._worker, *self._refills):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


__all__ = ["EXCHANGES", "Exchange", "ExchangesIndex", "digest", "exchanges_of", "passages"]
