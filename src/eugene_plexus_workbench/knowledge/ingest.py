"""Reading saved documents in the background, one at a time (MR4, design 4.2 and 4.9).

A document a person adds is `queued`; **one worker serves every person, taking one
document from each in turn** (round-robin, oldest first), so a person who adds two
hundred files does not keep everyone else waiting behind them. A document goes
`queued` -> `reading` -> `searchable` -> `ready`:

- `reading`: its original is read in a child process (`Reader.extract`: the same
  child, 120 seconds, 2 GiB, empty directory and bare environment as a file that is
  attached), then split into passages (`chunk.py`);
- `searchable`: its passages and their entries in the full-text index are written
  in one transaction, so by the time a document says it can be found by words, it can
  (`index.commit_reading`);
- `ready`: nothing else is owed it. In MR4 that is at once; search by meaning (MR5)
  gives a document more to wait for, through `owed`.

A file that gives no text ends `skipped` (nothing to read, or nothing allowed: a scan, a
locked file, too many passages) or `failed` (it could not be read), with the reason in
the words a person reads and the cause as it was observed (`index.end_unread`).
Nothing that happens to one file stops the next: a hostile PDF, a hang, a crash and a bug
here are each one file's `failed`.

**Resumable.** The queue is the `queued` rows, so it survives a stop: at boot a document
a stop cut off while it was read is `queued` again (`Index.requeue_interrupted`), one
that could be found and was not `ready` is finished, and the worker picks the rest up
(`resume`). **Removing a document cancels it**: its reader's whole process tree is
ended, and whatever the worker had made of it is thrown away, because it commits only
if the row is still there and still the same attempt (a document read again, or
replaced, is a new attempt).

**Reconciling at boot** (`resume`) is the other half of deleting across two stores: a
chat's own documents whose chat no longer exists are removed, as is any file beside an
index that no row claims; a document that can be found but whose text is gone is read
again, because what is derived heals.

Logs name a document by id and say counts and causes, never a name or content.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from . import chunk, extract
from . import index as index_module
from .index import CHAT, FOUND, PENDING, PROJECT, Document, Index, IndexUnavailable
from .reader import Extraction, Reader, summarize

log = logging.getLogger(__name__)

#: Which chats exist, and whose: given the ids a person's chat documents name, the
#: owner of each that is still there.
ChatOwners = Callable[[set[str]], Awaitable[dict[str, str]]]
#: Whether a document is owed more work before it is `ready` (MR5: embedding).
Owed = Callable[[Index, Document], Awaitable[bool]]


@dataclass
class Reconciled:
    """What the boot reconcile did: counts, for the log."""

    people: int = 0
    chat_documents: int = 0
    orphan_files: int = 0
    requeued: int = 0
    healed: int = 0
    finished: int = 0
    unreadable: int = 0


def key_of(doc_id: str) -> str:
    """What names a document's reading to the reader that ends it."""
    return f"doc:{doc_id}"


def _write_text(path: Path, text: str) -> None:
    part = path.with_name(path.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_text(text, encoding="utf-8", newline="\n")


class Ingest:
    """The worker, and the way to wait for it. One per Workbench."""

    def __init__(
        self,
        indexes: index_module.Indexes,
        reader: Reader,
        cap: Callable[[], Awaitable[int]],
        *,
        owed: Owed | None = None,
    ) -> None:
        self.indexes = indexes
        self._reader = reader
        self._cap = cap
        self._owed = owed
        self.page_reader: (
            Callable[[str, Index, Document, Extraction], Awaitable[Extraction]] | None
        ) = None
        #: People who may have a document waiting, in the order they are served.
        self._pending: dict[str, None] = {}
        self._wake = asyncio.Event()
        self._changed = asyncio.Condition()
        self._task: asyncio.Task[None] | None = None
        #: The document being read now: (person, id).
        self.current: tuple[str, str] | None = None
        #: The order documents were taken in (the last 1,000), for the tests.
        self.served: deque[tuple[str, str]] = deque(maxlen=1000)

    # --- the queue ------------------------------------------------------

    def start(self) -> None:
        """Begin serving. Call from the running loop, once."""
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="ingest")

    def wake(self, sub: str) -> None:
        """This person has a document waiting."""
        self._pending.setdefault(sub, None)
        self._wake.set()

    async def cancel(self, sub: str, doc_ids: list[str]) -> None:
        """These documents were removed or replaced: end their readers. What a
        reader that is already past it had made is thrown away when the worker finds
        the row gone, so a document that is removed is never searchable again."""
        keys = [key_of(i) for i in doc_ids]
        await self._reader.cancel(keys)
        await self._announce()

    async def wait(self, sub: str, doc_ids: list[str], seconds: float) -> set[str]:
        """Wait for these documents to be read (searchable, or ended) and for the worker
        to be done with them, for at most `seconds`. The ids still waiting or being read
        after that. A document can be found by its words a moment before the worker has
        made it `ready` (or left it owed its meaning); a caller that waits for it and
        then looks must not see that moment."""
        index = self.indexes.get(sub, create=False)
        if index is None or not doc_ids:
            return set()
        deadline = time.perf_counter() + seconds
        while True:
            found = [await index.document(i) for i in doc_ids]
            waiting = {
                d.id
                for d in found
                if d is not None and (d.status in PENDING or self.current == (sub, d.id))
            }
            left = deadline - time.perf_counter()
            if not waiting or left <= 0:
                return waiting
            async with self._changed:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._changed.wait(), min(0.25, left))

    async def _announce(self) -> None:
        async with self._changed:
            self._changed.notify_all()

    async def aclose(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # --- the worker -----------------------------------------------------

    async def _loop(self) -> None:
        while True:
            picked = await self._pick()
            if picked is None:
                self._wake.clear()
                if self._pending:
                    continue
                await self._wake.wait()
                continue
            sub, doc = picked
            self.current = (sub, doc.id)
            try:
                await self._ingest(sub, doc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("reading saved document %s failed in Workbench itself", doc.id)
                await self._fail(sub, doc, exc)
            finally:
                self.current = None
                await self._announce()

    async def _pick(self) -> tuple[str, Document] | None:
        """The next document: the first person in turn who has one waiting, who then
        goes to the back of the turn."""
        for sub in list(self._pending):
            index = self.indexes.get(sub, create=False)
            if index is None:
                self._pending.pop(sub, None)
                continue
            try:
                doc = await index.claim()
            except IndexUnavailable as exc:
                log.warning("saved documents of one person cannot be read now: %s", exc.message)
                self._pending.pop(sub, None)
                continue
            self._pending.pop(sub, None)
            if doc is None:
                continue
            self._pending[sub] = None
            self.served.append((sub, doc.id))
            return sub, doc
        return None

    async def _fail(self, sub: str, doc: Document, exc: BaseException) -> None:
        index = self.indexes.get(sub, create=False)
        if index is None:
            return
        with contextlib.suppress(Exception):
            await index.end_unread(
                doc.id,
                doc.attempt,
                state=index_module.FAILED,
                code="internal",
                reason=(
                    f"Failed: Workbench could not read this file ({type(exc).__name__}). "
                    "See Workbench's log."
                ),
                detail=f"{type(exc).__name__}: {exc}",
            )

    async def _ingest(self, sub: str, doc: Document) -> None:
        index = self.indexes.get(sub, create=False)
        if index is None:
            return
        started = time.perf_counter()
        extraction = await self._reader.extract(
            key_of(doc.id), index.original_path(doc.id), doc.kind, doc.language
        )
        if self.page_reader is not None and not extraction.cancelled:
            extraction = await self.page_reader(sub, index, doc, extraction)
        if extraction.cancelled:
            return
        if extraction.failure is not None:
            failure = extraction.failure
            kept = await index.end_unread(
                doc.id,
                doc.attempt,
                state=str(failure["state"]),
                code=str(failure["code"]),
                reason=str(failure["message"]),
                detail=failure.get("detail"),
                pages=failure.get("pages") if isinstance(failure.get("pages"), int) else None,
            )
            if kept:
                log.warning(
                    "could not read saved document %s (%s, %s): %s",
                    doc.id,
                    failure["state"],
                    failure["code"],
                    failure.get("detail") or failure.get("reason"),
                )
            return
        extracted = extraction.extracted
        assert extracted is not None
        passages = await asyncio.to_thread(chunk.chunk, extracted)
        text = await asyncio.to_thread(extract.render, extracted.parts)
        summary = extraction.summary or summarize(extracted)
        text_path = index.text_path(doc.id)
        await asyncio.to_thread(_write_text, text_path, text)
        part = text_path.with_name(text_path.name + ".part")
        try:
            result = await index.commit_reading(
                doc.id, doc.attempt, summary, passages, cap=await self._cap()
            )
            if result == "searchable":
                # Removed between the commit and here, its text part is gone too.
                with contextlib.suppress(FileNotFoundError):
                    await asyncio.to_thread(os.replace, part, text_path)
        finally:
            await asyncio.to_thread(part.unlink, True)
        if result == "searchable":
            log.info(
                "read saved document %s as %s: %d passage(s), about %s tokens, in %.1f s",
                doc.id,
                doc.kind,
                len(passages),
                f"{summary.get('tokens') or 0:,}",
                time.perf_counter() - started,
            )
            await self._finish(index, doc.id)
        elif result == "capped":
            log.warning("saved document %s was skipped: it would pass the passage limit", doc.id)

    async def _finish(self, index: Index, doc_id: str) -> None:
        """A document that can be found by words is `ready` when it is owed nothing."""
        if self._owed is not None:
            doc = await index.document(doc_id)
            if doc is not None and await self._owed(index, doc):
                return
        await index.finish(doc_id)
        await self._announce()

    # --- boot -----------------------------------------------------------

    async def resume(
        self, chat_owners: ChatOwners, project_owners: ChatOwners | None = None
    ) -> Reconciled:
        """Boot: put what a stop left right, and start what is waiting.

        For every person who has an index: documents a stop cut off while they were
        read wait again; a chat's own documents whose chat is gone are removed; files
        beside the index that no row claims are removed; a document that can be found
        but whose text has gone is read again; and a document that can be found and is
        owed nothing is `ready`. An index that cannot be opened is left exactly as it
        is, and said, so nothing is deleted on a guess."""
        done = Reconciled()
        for directory in self.indexes.person_dirs():
            sub = self.indexes.owner_of(directory)
            if sub is None or self.indexes.dir_of(sub) != directory:
                log.warning("an index under knowledge/ cannot be read or is not its owner's")
                done.unreadable += 1
                continue
            index = self.indexes.get(sub)
            assert index is not None
            try:
                await self._reconcile(index, sub, chat_owners, done, project_owners)
            except IndexUnavailable as exc:
                log.warning("saved documents of one person were not checked: %s", exc.message)
                done.unreadable += 1
                continue
            done.people += 1
            self._pending.setdefault(sub, None)
        if self._pending:
            self._wake.set()
        return done

    async def _reconcile(
        self,
        index: Index,
        sub: str,
        chat_owners: ChatOwners,
        done: Reconciled,
        project_owners: ChatOwners | None = None,
    ) -> None:
        held = await index.scopes()
        scopes = [s for s in held if s.startswith(CHAT)]
        if scopes:
            owners = await chat_owners({s[len(CHAT) :] for s in scopes})
            for scope in scopes:
                if owners.get(scope[len(CHAT) :]) != sub:
                    done.chat_documents += len(await index.delete_where(scope))
        # MV7b: a project's documents whose project is gone, as a chat's whose chat is.
        scopes = [s for s in held if s.startswith(PROJECT)]
        if scopes and project_owners is not None:
            owners = await project_owners({s[len(PROJECT) :] for s in scopes})
            if not any(owner == sub for owner in owners.values()):
                # Not one of these projects is there: a store restored from before them, as
                # likely as a person who deleted them all. Files the person kept are not
                # deleted on a guess; they wait, unsearched, and the log says so.
                log.warning(
                    "a person's index holds %d projects' documents and the store none of those "
                    "projects; they were kept",
                    len(scopes),
                )
            else:
                for scope in scopes:
                    if owners.get(scope[len(PROJECT) :]) != sub:
                        done.chat_documents += len(await index.delete_where(scope))
        for path in await index.orphans():
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
                done.orphan_files += 1
        done.requeued += await index.requeue_interrupted()
        for doc in await index.documents():
            if doc.scope in index_module.PRIVATE:
                continue  # a note or an exchange is written from the store: no file
            if doc.status in FOUND and not index.text_path(doc.id).is_file():
                log.warning("the text of saved document %s is gone; reading it again", doc.id)
                if await index.requeue(doc.id) is not None:
                    done.healed += 1
        for doc in await index.unfinished():
            await self._finish(index, doc.id)
            now = await index.document(doc.id)
            if now is not None and now.status == index_module.READY:
                done.finished += 1
