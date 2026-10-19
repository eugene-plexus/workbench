"""A person's notes in their knowledge index (MV1, `workbench-memory-v2.md` section 1).

The store (`store.py`) holds the notes; the person's knowledge index (`knowledge/index.py`)
holds a copy of each current note to search, in the scope `index.NOTES`, one document a note
by the note's id: its title as passage 0 and its body's passages after it, cut by the same
Markdown rule documents are. Every passage's heading is `Tags: a, b · <title> > <headings>`,
so the tags and the title weigh twice the text by words and go with every passage a search
model embeds. What the copy holds is named by a hash (`digest`) of the title, the body and
the tags, kept as the document's `sha256`.

**The store is the truth; the index is kept in step, and checked.** The two are separate
SQLite files and one transaction cannot hold both, so:

- a note that is kept, edited, given new tags or a new version is put again (`put`), and a
  failure to is logged and put right by `reconcile`;
- a note that is deleted leaves the index **first** (`remove`), so a failure to remove its
  words fails the delete and nothing is deleted; a note row that then fails to go is still a
  note, and `reconcile` puts it back;
- every search that uses a passage checks it against the store (`memory_relevance`): a
  passage whose note is not the person's, not current, or not at this digest is never used;
- `reconcile` (at start-up for every person with notes, and whenever a put failed) makes the
  index hold exactly the person's current notes at their digests. It is also how the notes
  that existed before MV1 are first indexed.

Logs carry counts and causes, never a note's words.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from collections.abc import Sequence
from typing import Any

from .knowledge import chunk, extract
from .knowledge.index import NOTES, IndexUnavailable
from .store import MemoryNote, Store, tag_key

log = logging.getLogger(__name__)

#: Between the tags and the title in a passage's heading.
SEPARATOR = " · "


def digest(note: MemoryNote) -> str:
    """What names a version of a note in the index: its title, body and tags."""
    shape = [note.text, note.body or "", sorted(tag_key(t) for t in note.tags)]
    return hashlib.sha256(json.dumps(shape, ensure_ascii=False).encode("utf-8")).hexdigest()


def _head(note: MemoryNote) -> str | None:
    return f"Tags: {', '.join(note.tags)}" if note.tags else None


def passages(note: MemoryNote) -> list[chunk.Passage]:
    """The note as the index holds it: the title, then the body's passages."""
    head = _head(note)
    title = " ".join(note.text.split())
    out = [
        chunk.Passage(
            ord=0,
            text=title,
            heading_path=head,
            page_start=None,
            page_end=None,
            line_start=None,
            line_end=None,
            char_start=0,
            char_end=0,
            tokens_est=max(1, len(title) // 3),
        )
    ]
    body = note.body or ""
    if body.strip():
        extracted = extract.Extracted(
            kind="markdown",
            parts=[extract.Part(body)],
            lines=extract.line_count(body),
            headings=extract.markdown_headings(body),
        )
        top = f"{head}{SEPARATOR}{title}" if head else title
        for piece in chunk.chunk(extracted):
            path = f"{top} > {piece.heading_path}" if piece.heading_path else top
            out.append(
                chunk.Passage(
                    ord=len(out),
                    text=piece.text,
                    heading_path=path[: chunk.MAX_PATH * 2],
                    page_start=None,
                    page_end=None,
                    line_start=piece.line_start,
                    line_end=piece.line_end,
                    char_start=piece.char_start,
                    char_end=piece.char_end,
                    tokens_est=piece.tokens_est,
                )
            )
    return out


class NotesIndex:
    """Keeps every person's notes in their index. One per Workbench; `saved` is the
    people's `SavedDocuments`, whose indexes and meaning worker it uses."""

    def __init__(self, store: Store, saved: Any) -> None:
        self.store = store
        self.saved = saved
        #: People whose index may not hold their notes as they are (a put failed).
        self.dirty: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._retrying: asyncio.Task[None] | None = None

    def _index(self, owner: str) -> Any:
        return self.saved.index(owner, create=True)

    def _wake(self, owner: str) -> None:
        meaning = getattr(self.saved, "meaning", None)
        if meaning is not None:
            meaning.wake(owner)

    async def put(self, owner: str, note_ids: Sequence[str]) -> None:
        """Make the index hold these notes as they are now: a current note is put, any
        other (gone, superseded, pending) is taken out. Never raises: a failure is logged,
        and the person's notes are reconciled later."""
        if not note_ids:
            return
        try:
            notes = {
                n.id: n
                for n in await self.store.memory_notes_by_ids(owner, note_ids, with_body=True)
            }
            index = self._index(owner)
            gone: list[str] = []
            for note_id in dict.fromkeys(note_ids):
                note = notes.get(note_id)
                if note is None or note.status != "active":
                    gone.append(note_id)
                    continue
                await index.put_note(note.id, digest(note), note.text, passages(note))
            if gone:
                await index.delete_notes(gone)
            self._wake(owner)
        except Exception as exc:
            self.dirty.add(owner)
            log.warning(
                "%d note(s) could not be put in a person's index: %s: %s",
                len(note_ids),
                type(exc).__name__,
                exc,
            )
            self._later()

    async def remove(self, owner: str, note_ids: Sequence[str]) -> None:
        """Take these notes out of the person's index, words and all. Raises
        `IndexUnavailable` (or what the index raises) when it cannot: the delete that asked
        must then not go on."""
        index = self.saved.index(owner, create=False)
        if index is None or not note_ids:
            return
        await index.delete_notes(list(note_ids))

    async def reconcile(self, owner: str) -> tuple[int, int]:
        """Make the person's index hold exactly their current notes, each at its digest:
        `(put, removed)`. Raises what the index raises."""
        listed = [n.id for n in await self.store.memory_notes(owner)]
        index = self.saved.index(owner, create=bool(listed))
        if index is None:
            return 0, 0
        held = await index.note_digests()
        put = 0
        # In batches: a person's notes with their bodies may be hundreds of megabytes.
        for start in range(0, len(listed), 500):
            batch = await self.store.memory_notes_by_ids(
                owner, listed[start : start + 500], with_body=True
            )
            for note in batch:
                if note.status == "active" and held.get(note.id) != digest(note):
                    await index.put_note(note.id, digest(note), note.text, passages(note))
                    put += 1
        current = set(listed)
        removed = await index.delete_notes([i for i in held if i not in current])
        if put:
            self._wake(owner)
        self.dirty.discard(owner)
        return put, removed

    async def reconcile_all(self) -> None:
        """Start-up: every person with notes, one after another. Never raises."""
        owners = await self.store.memory_owners()
        put = removed = failed = 0
        for owner in owners:
            try:
                p, r = await self.reconcile(owner)
                put, removed = put + p, removed + r
            except Exception as exc:
                failed += 1
                self.dirty.add(owner)
                log.warning(
                    "one person's notes could not be checked against their index: %s: %s",
                    type(exc).__name__,
                    exc,
                )
        if put or removed or failed:
            log.info(
                "notes in people's indexes after the restart: %d put, %d removed, %d "
                "person(s) not checked",
                put,
                removed,
                failed,
            )

    def start(self) -> None:
        """Reconcile everyone in the background, so a large store does not hold up boot."""
        self._task = asyncio.create_task(self.reconcile_all(), name="notes-index")

    def _later(self) -> None:
        if self._retrying is None or self._retrying.done():
            self._retrying = asyncio.create_task(self._retry(), name="notes-index-retry")

    async def _retry(self) -> None:
        """Every 30 seconds, until every person's index is in step again."""
        while self.dirty:
            await asyncio.sleep(30)
            await self._retry_once()

    async def _retry_once(self) -> None:
        for owner in list(self.dirty):
            try:
                await self.reconcile(owner)
            except Exception as exc:
                log.warning(
                    "one person's notes are still not in step with their index: %s: %s",
                    type(exc).__name__,
                    exc,
                )

    async def aclose(self) -> None:
        for task in (self._task, self._retrying):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


__all__ = ["NOTES", "IndexUnavailable", "NotesIndex", "digest", "passages"]
