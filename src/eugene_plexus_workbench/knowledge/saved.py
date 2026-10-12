"""A person's saved documents, as the rest of Workbench uses them (MR4).

The parts are elsewhere: `index.py` is one person's file of documents, passages and
search index; `chunk.py` splits a file into passages; `ingest.py` reads documents in
the background; `search.py` finds passages. This is what holds them together for the
routes, and the only thing that knows all of them, so that a route never joins two
by hand: **adding** a document (its original goes in place, its row is made, a file
of the same name it replaces is cancelled and gone, the worker is woken), **removing**
one (its reader is ended first, so nothing holds its file, and then all of it goes in
one transaction), reading one **again**, and what happens to a chat's own documents
when its chat or its file goes. With a gateway it also holds search by meaning (MR5,
`meaning.py`): the worker that adds meaning to what is read, and the way of searching that
finds a passage by what it says; a document that goes ends the request that was only for it.

There are two kinds, told by their scope (`index.LIBRARY`, `index.chat_scope`): the
ones a person keeps in My documents, searched by every chat that has Documents on,
and the ones made from a file attached to one chat ("Search it instead"), searched
only there and deleted with it. A person's My documents hold the files they added and, since
MR7, the files read from the folders they pointed it at (`folder_sync.py`): the same documents in
every way but one, that their `source` names the folder and the path.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import shutil
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .. import files
from .index import (
    CHAT,
    LIBRARY,
    Added,
    DeleteFailed,
    Document,
    Held,
    Index,
    Indexes,
    NewDocument,
    chat_scope,
    folder_source,
)
from .ingest import ChatOwners, Ingest, Owed, Reconciled
from .meaning import Gateway, Meaning, SettingsReader
from .reader import Reader
from .search import Retriever, WordSignal

log = logging.getLogger(__name__)

HASH_CHUNK = 1024 * 1024


def new_id() -> str:
    """A document's id: made here, never taken from a request."""
    return secrets.token_urlsafe(12)


def hash_file(path: Path) -> tuple[int, str]:
    """`(size, sha256)` of a file, a megabyte at a time."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(HASH_CHUNK):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


class OriginalMissing(Exception):
    """A document's original is not on the disk any more, so it cannot be read again."""


@dataclass
class Rebuilt:
    """What making a person's index again from their own files came to."""

    #: Documents made again, and read again in the background.
    documents: int
    #: Files left that no document could be made of (no original, or a chat's that is gone).
    left: int


class SavedDocuments:
    """Every person's saved documents. One per Workbench."""

    def __init__(
        self,
        root: Path,
        reader: Reader,
        cap: Callable[[], Awaitable[int]],
        *,
        retriever: Retriever | None = None,
        owed: Owed | None = None,
        gateway: Gateway | None = None,
        settings: SettingsReader | None = None,
    ) -> None:
        self.root = root
        self.indexes = Indexes(root)
        #: Search by meaning: only with a gateway to ask. Without one, words only.
        self.meaning: Meaning | None = None
        if gateway is not None and settings is not None:
            self.meaning = Meaning(self.indexes, gateway, settings)
            owed = owed or self.meaning.owed
            retriever = retriever or Retriever([WordSignal(), self.meaning.signal])
        self.ingest = Ingest(self.indexes, reader, cap, owed=owed)
        self.retriever = retriever or Retriever()

    async def start(self, chat_owners: ChatOwners) -> Reconciled:
        """Boot: put right what a stop left (`Ingest.resume`), then begin reading, and
        adding meaning to what has none (`Meaning.resume`: the passages whose vector is NULL)."""
        done = await self.ingest.resume(chat_owners)
        self.ingest.start()
        if self.meaning is not None:
            await self.meaning.resume()
            self.meaning.start()
        return done

    async def aclose(self) -> None:
        await self.ingest.aclose()
        if self.meaning is not None:
            await self.meaning.aclose()
        await self.indexes.aclose()

    async def _cancel(self, sub: str, doc_ids: list[str]) -> None:
        """These documents are removed, replaced or read again: whatever is reading them,
        and whatever is adding their meaning, is ended."""
        await self.ingest.cancel(sub, doc_ids)
        if self.meaning is not None:
            await self.meaning.cancel(sub, doc_ids)

    def index(self, sub: str, *, create: bool = True) -> Index | None:
        return self.indexes.get(sub, create=create)

    # --- bringing a document in -----------------------------------------

    async def add(
        self,
        sub: str,
        *,
        doc_id: str,
        scope: str,
        name: str,
        detected: files.Detected,
        size: int,
        sha256: str,
        source: str,
        source_mtime: str | None = None,
    ) -> Added:
        """Keep a file that is whole at `index.part_path(doc_id)`: it goes in place as
        the original, the document is made `queued`, and the worker is woken. A file of
        the same name in My documents is replaced, and its reader ended; one that is
        there already, byte for byte, is left, and the copy brought now is thrown away.
        A file read from a folder is the one at the same path (`source`), whatever it is
        called, and `source_mtime` is the modified time it was read at."""
        index = self.indexes.get(sub)
        assert index is not None
        final = index.original_path(doc_id)
        await asyncio.to_thread(os.replace, index.part_path(doc_id), final)
        new = NewDocument(
            id=doc_id,
            scope=scope,
            name=name,
            media_type=detected.media_type,
            bytes=size,
            sha256=sha256,
            source=source,
            kind=detected.kind,
            language=detected.language,
            source_mtime=source_mtime,
            added_at=time.time(),
        )
        try:
            await asyncio.to_thread(index.write_sidecar, new)
            added = await index.add(new)
        except BaseException:
            final.unlink(missing_ok=True)
            index.meta_path(doc_id).unlink(missing_ok=True)
            raise
        if added.unchanged:
            final.unlink(missing_ok=True)
            index.meta_path(doc_id).unlink(missing_ok=True)
            return added
        if added.replaced:
            await self._cancel(sub, [d.id for d in added.replaced])
        self.ingest.wake(sub)
        return added

    async def search_attachment(
        self,
        sub: str,
        *,
        chat_id: str,
        file_id: str,
        name: str,
        detected: files.Detected,
        source_path: Path,
    ) -> Document:
        """ "Search it instead": a file attached to a chat becomes a document of that
        chat's own, searched there and deleted with it. Its bytes are copied in, so the
        document is as complete, and as removable, as one the person added themselves.
        The same document when it is asked for again."""
        index = self.indexes.get(sub)
        assert index is not None
        scope, source = chat_scope(chat_id), f"attachment:{file_id}"
        for existing in await index.documents(scope):
            if existing.source == source and existing.status not in ("skipped", "failed"):
                return existing
        doc_id = new_id()
        part = index.part_path(doc_id)
        part.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, source_path, part)
        size, digest = await asyncio.to_thread(hash_file, part)
        added = await self.add(
            sub,
            doc_id=doc_id,
            scope=scope,
            name=name,
            detected=detected,
            size=size,
            sha256=digest,
            source=source,
        )
        return added.document

    # --- taking one away, or reading it again ---------------------------

    async def remove(self, sub: str, doc_id: str) -> Document | None:
        """Remove a document for real: its reader is ended first (a Windows process
        holds the file it reads), then its row, passages, search entries, original and
        text go in one transaction. The document as it was; None when it is not here.
        A file a program holds a moment longer is waited for; one that stays held raises
        `DeleteFailed`, and nothing is removed."""
        index = self.indexes.get(sub, create=False)
        if index is None:
            return None
        for attempt in range(3):
            await self._cancel(sub, [doc_id])
            try:
                return await index.delete(doc_id)
            except DeleteFailed:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.4)
        return None  # unreachable

    async def remove_documents(self, sub: str, doc_ids: Sequence[str]) -> list[Document]:
        """Remove these documents as `remove` does each, in one transaction: their readers
        are ended first, then rows, passages, search entries, originals and texts go together or
        not at all (`DeleteFailed` after three tries). The documents that were there."""
        index = self.indexes.get(sub, create=False)
        if index is None or not doc_ids:
            return []
        for attempt in range(3):
            await self._cancel(sub, list(doc_ids))
            try:
                return await index.delete_many(doc_ids)
            except DeleteFailed:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.4)
        return []  # unreachable

    async def drop_folder(self, sub: str, grant_id: str) -> list[Document]:
        """Every document read from a folder is removed, as `remove_documents` does: the folder
        was withdrawn, or was removed from the person's documents, or is not there. The documents
        that were there."""
        index = self.indexes.get(sub, create=False)
        if index is None:
            return []
        for attempt in range(3):
            await self._cancel(sub, [h.doc_id for h in await index.folder_files(grant_id)])
            try:
                return await index.delete_folder(grant_id)
            except DeleteFailed:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.4)
        return []  # unreachable

    async def move_folder_document(
        self, sub: str, held: Held, grant_id: str, path: str, name: str, mtime: str | None
    ) -> Document | None:
        """A file of a folder moved, and its content is the same: its document is the one it
        was, with its passages and vectors, under the new path and name. None when it is not
        there (removed meanwhile)."""
        index = self.indexes.get(sub, create=False)
        if index is None:
            return None
        moved = await index.move_folder_document(
            held.doc_id,
            folder_source(grant_id, held.path),
            folder_source(grant_id, path),
            name,
            mtime,
        )
        if moved is not None:
            await asyncio.to_thread(
                index.write_sidecar,
                NewDocument(
                    id=moved.id,
                    scope=moved.scope,
                    name=moved.name,
                    media_type=moved.media_type,
                    bytes=moved.bytes,
                    sha256=moved.sha256,
                    source=moved.source,
                    kind=moved.kind,
                    language=moved.language,
                    source_mtime=moved.source_mtime,
                    added_at=moved.added_at,
                ),
            )
        return moved

    async def reread(self, sub: str, doc_id: str) -> Document | None:
        """Read a document again from its original: its passages go now, so nothing of
        the old reading is found, and it waits its turn. None when it is not here;
        `OriginalMissing` when there is nothing to read."""
        index = self.indexes.get(sub, create=False)
        if index is None:
            return None
        if await index.document(doc_id) is None:
            return None
        if not await asyncio.to_thread(index.original_path(doc_id).is_file):
            raise OriginalMissing
        await self._cancel(sub, [doc_id])
        again = await index.requeue(doc_id)
        if again is not None:
            self.ingest.wake(sub)
        return again

    async def drop_chat(self, sub: str, chat_id: str) -> int:
        """A chat is gone: so are the documents made from its files. How many."""
        index = self.indexes.get(sub, create=False)
        if index is None:
            return 0
        found = [d.id for d in await index.documents(chat_scope(chat_id))]
        await self._cancel(sub, found)
        return len(await index.delete_where(chat_scope(chat_id)))

    async def drop_attachment(self, sub: str, chat_id: str, file_id: str) -> int:
        """A file is no longer searched: the document made from it, if there is one, goes."""
        index = self.indexes.get(sub, create=False)
        if index is None:
            return 0
        scope, source = chat_scope(chat_id), f"attachment:{file_id}"
        found = [d.id for d in await index.documents(scope) if d.source == source]
        if not found:
            return 0
        await self._cancel(sub, found)
        return len(await index.delete_where(scope, source))

    async def rebuild(self, sub: str, chat_owners: ChatOwners) -> Rebuilt:
        """Make a person's index again from their own files ("Rebuild"): the index that
        is there, damaged or not, is thrown away, and every document that its files still
        describe (`Index.sidecars`: its name and type beside its original) is made again,
        `queued`, in the order it was added, and read again. A chat's own document whose
        chat is gone is not. The originals are never touched."""
        index = self.indexes.get(sub)
        assert index is not None
        found = await asyncio.to_thread(index.sidecars)
        await self._cancel(sub, [d.id for d in found])  # whatever is being read
        if self.meaning is not None:
            self.meaning.cache.drop(sub)
        await index.close()
        await asyncio.to_thread(index.quarantine)
        chat_ids = {d.scope[len(CHAT) :] for d in found if d.scope.startswith(CHAT)}
        owners = await chat_owners(chat_ids) if chat_ids else {}
        made = 0
        for meta in found:
            if meta.scope.startswith(CHAT) and owners.get(meta.scope[len(CHAT) :]) != sub:
                continue
            added = await index.add(meta)
            made += 0 if added.unchanged else 1
        self.ingest.wake(sub)
        return Rebuilt(documents=made, left=len(await index.orphans()))

    async def chat_documents(self, sub: str, chat_id: str) -> list[Document]:
        index = self.indexes.get(sub, create=False)
        if index is None:
            return []
        return await index.documents(chat_scope(chat_id))

    async def library(self, sub: str) -> list[Document]:
        index = self.indexes.get(sub, create=False)
        if index is None:
            return []
        return await index.documents(LIBRARY)
