"""Keeping a person's folders in step with their documents (MR7, design 3, 7 and 8).

A person points their Documents at a folder the owner granted them (`add`). From then on Workbench
**checks** it: now, then every `documentsFolderSyncMinutes`, and when the person asks (`request`).
A check is a walk of the folder (`folder_walk.walk`, read-only, on held handles), a plan of what
changed (`folder_walk.plan`), and the work of the plan:

- a file with no document is **read**: its first bytes say what it is, and a document is copied
  into the person's own `originals/` (hashed as it goes) and queued like an upload; anything
  else is kept as a `skipped` document with its reason, so a person can see why a file is not found;
- a file whose size or modified time moved is read again, and **its hash decides**: the same
  words only refresh what is kept of its time; different words are a new document, and the old
  document, its passages, vectors and search entries go in the same transaction (`Index.add`);
- a file that is gone, or that is ignored now, is removed, and one that only **moved** keeps its
  document, passages and vectors (the same size, time and hash under a new path);
- a directory that could not be listed leaves its documents as they are, named, and a failure
  that is not "it is not there" leaves them all: only what is known to be gone is removed.

**A folder that is not there**, or was replaced by another, takes its documents out of the index
and says so; a **grant withdrawn** by the owner does the same before the owner's request returns
(`grant_withdrawn`), and the folder stays in the person's list as `withdrawn` until they dismiss
it. What a check found is kept with the folder (`FolderRecord.report`).

**One check at a time, for everyone**, a manual one before a scheduled one. A file is read
through `Folders.read` (the same lock and the same handles as the model's tools), so a grant
withdrawn while a check waits is never read, and each file is added under a lock of its own folder,
which a removal takes before it deletes: nothing is added after it. Logs carry counts and causes,
never a name or a path.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import files, folder_io
from ..folders import Folders, GrantGone, visible
from ..store import FolderRecord, Person, Store
from . import folder_rules, folder_walk
from .folder_walk import Caps, Found
from .index import (
    LIBRARY,
    DeleteFailed,
    FolderSkip,
    Held,
    Index,
    IndexUnavailable,
    NewDocument,
    folder_source,
)
from .saved import SavedDocuments, new_id

if TYPE_CHECKING:
    from ..config import DocumentSettings, FolderSettings

log = logging.getLogger(__name__)

#: Seconds in a minute of the check interval. A test makes it short.
UNIT = 60.0
#: The loop looks at the folders at least this often, in seconds, whatever the interval is.
TICK = 30.0
#: How long a folder is left alone after a check that ended badly, in seconds.
BACKOFF = 60.0
#: A file whose modified time is this recent when it is read may be saved again within one tick of
#: its clock, and so unseen: it is read again at the next check (nanoseconds).
RACY_NS = 2_500_000_000
#: A batch of skipped files is written to the index at once.
SKIP_BATCH = 200

READS = "Workbench reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files."


class NoSuchGrant(Exception):
    """The grant is not there, or does not name this person."""


class AlreadyAdded(Exception):
    """The person has already pointed their Documents at this folder."""


class IgnoreListFull(Exception):
    """The folder's ignore list holds as many patterns as it may."""


class _DiskFull(Exception):
    """The disk Workbench keeps its documents on has no room."""

    def __init__(self, free: int | None) -> None:
        super().__init__("disk full")
        self.free = free


class _Stopped(Exception):
    """The check was ended on purpose: the folder was removed or withdrawn."""


@dataclass
class Want:
    """A check that was asked for: `manual` is Sync now (skipped files are tried again), `full`
    reads and hashes every file."""

    manual: bool = False
    full: bool = False


@dataclass
class Run:
    """A check that is under way, as the page reads it."""

    started: float
    phase: str = "looking"
    done: int = 0
    total: int = 0
    manual: bool = False
    full: bool = False


class _State:
    """What one folder of one person has in memory: the lock every add takes (and a removal takes
    before it deletes), whether it was closed (removed or withdrawn), the lock a withdrawal is done
    under, and the check under way."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.closed = False
        self.withdrawal = asyncio.Lock()
        self.run: Run | None = None
        self.task: asyncio.Task[None] | None = None


def plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count:,} {one if count == 1 else many or one + 's'}"


# --------------------------------------------------------------------------- #
# what a file that is not taken is told
# --------------------------------------------------------------------------- #


def _lower(text: str) -> str:
    return f"{text[:1].lower()}{text[1:]}"


def unsupported_words(message: str) -> str:
    """`files.Unsupported`'s message (`This file is a GIF image. <what is read>`) as the reason a
    folder's file is skipped for."""
    what = message.removesuffix(" " + files.READS).strip()
    return f"Skipped: {_lower(what)} {READS}"


def not_a_document_words(kind: str) -> str:
    what = "a picture" if kind == "image" else "a sound recording"
    return f"Skipped: this file is {what}, not a document. {READS}"


def not_word_words(name: str) -> str:
    what = files.not_word(name).message.removesuffix(" " + files.READS).strip()
    return f"Skipped: {_lower(what)} {READS}"


def too_large_words(size: int, limit: int) -> str:
    return (
        f"Skipped: this file is {files.describe_size(size)}; the most Workbench reads of one "
        f"file is {files.describe_size(limit)}."
    )


def over_folder_words(max_mb: int) -> str:
    return (
        f"Skipped: this folder's files together would pass the {max_mb:,} MB Workbench keeps "
        "from one folder."
    )


BUSY = (
    "Skipped for now: another program is using this file. Workbench looks again at the next check."
)
CHANGED = (
    "Skipped for now: this file changed while Workbench was reading it. It is read again at "
    "the next check."
)
HARD_LINKS = "Skipped: this file has more than one hard link, so Workbench does not read it."
SPECIAL = "Skipped: this is not an ordinary file, so Workbench does not read it."
LINKED = (
    "Not followed: this became a link while Workbench was looking. Workbench never follows "
    "links out of the folder."
)
MOUNTED = "Skipped: another disk is mounted here, and Workbench does not read across disks."


def unreadable_words(cause: str) -> str:
    return (
        f"Skipped: Workbench could not read this file ({cause}). It looks again at the next check."
    )


def is_busy(cause: BaseException | None) -> bool:
    """A file another program holds: a sharing or lock violation (Windows), or a lock (POSIX)."""
    return isinstance(cause, BlockingIOError) or getattr(cause, "winerror", None) in (32, 33)


def is_link(cause: BaseException | None) -> bool:
    """A name that is a link where a file was expected: `O_NOFOLLOW` on one is `ELOOP` (POSIX)."""
    return isinstance(cause, OSError) and cause.errno == getattr(errno, "ELOOP", -1)


class FolderSync:
    """Every person's folders, checked in turn. One per Workbench."""

    def __init__(
        self,
        store: Store,
        saved: SavedDocuments,
        folders: Folders,
        documents: Callable[[], Awaitable[DocumentSettings]],
        folder_settings: Callable[[], Awaitable[FolderSettings]],
    ) -> None:
        self.store = store
        self.saved = saved
        self.folders = folders
        self._documents = documents
        self._settings = folder_settings
        self._states: dict[tuple[str, str], _State] = {}
        self._wanted: dict[tuple[str, str], Want] = {}
        #: When a folder may be looked at again after a check that ended badly (`perf_counter`).
        self._rest: dict[tuple[str, str], float] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        #: The order checks were begun in (the last 200), for the tests.
        self.begun: list[tuple[str, str]] = []

    # --- what a page reads ---------------------------------------------------

    def state(self, sub: str, grant_id: str) -> _State:
        return self._states.setdefault((sub, grant_id), _State())

    def progress(self, sub: str, grant_id: str) -> Run | None:
        found = self._states.get((sub, grant_id))
        return found.run if found is not None else None

    def waiting(self, sub: str, grant_id: str) -> bool:
        return (sub, grant_id) in self._wanted

    # --- starting and stopping ------------------------------------------------

    async def start(self) -> None:
        """Boot: put right what a stop left (`reconcile`), and begin checking. Every folder is
        checked once at once: a check a stop cut off is finished, and what changed while Workbench
        was not running is found (a check of a folder that has not changed reads nothing)."""
        await self.reconcile()
        for record in await self.store.folder_records():
            if record.status == "active":
                self.request(record.owner, record.grant_id, manual=False)
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="folder-sync")

    async def stop_person(self, sub: str) -> None:
        """The person was removed from Eugene (row 7): nothing of theirs waits, and a check of
        one of their folders running now ends. Their folders are no longer listed
        (`Store.folder_records`), so none is checked again."""
        for key in [k for k in self._wanted if k[0] == sub]:
            self._wanted.pop(key, None)
        running = [s.task for k, s in self._states.items() if k[0] == sub and s.task is not None]
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)

    async def aclose(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for found in list(self._states.values()):
            if found.task is not None:
                found.task.cancel()
                await asyncio.gather(found.task, return_exceptions=True)

    async def reconcile(self) -> int:
        """A folder whose grant is gone is withdrawn; a document read from a folder the person
        no longer has, or that was withdrawn, is removed (what a stop between the two halves of
        removing a folder leaves). How many documents were removed."""
        removed = 0
        grants = {g["id"]: g for g in await self.store.folder_grants()}
        for record in await self.store.folder_records():
            if record.status == "active" and not _granted(grants, record):
                await self._withdraw(record)
        kept = {
            (r.owner, r.grant_id) for r in await self.store.folder_records() if r.status == "active"
        }
        for directory in self.saved.indexes.person_dirs():
            sub = self.saved.indexes.owner_of(directory)
            if sub is None or self.saved.indexes.dir_of(sub) != directory:
                continue
            index = self.saved.index(sub)
            assert index is not None
            try:
                for grant_id in await index.folder_grants():
                    if (sub, grant_id) not in kept:
                        removed += len(await self.saved.drop_folder(sub, grant_id))
            except IndexUnavailable as exc:
                log.warning("a person's folders were not checked at boot: %s", exc.message)
        if removed:
            log.info("removed %d document(s) read from folders that are no longer kept", removed)
        return removed

    # --- what people do --------------------------------------------------------

    async def add(
        self,
        person: Person,
        grant_id: str,
        *,
        ignore: list[str] | None = None,
        include_hidden: bool = False,
        use_gitignore: bool = True,
        project_id: str | None = None,
    ) -> FolderRecord:
        """Point a person's Documents at a folder that was granted them. `NoSuchGrant` when it is
        not theirs (the same answer as one that does not exist); `AlreadyAdded` when it is there;
        `folder_rules.BadPattern` for a pattern that cannot be used. Its first check starts at
        once."""
        grant = next(
            (
                g
                for g in await self.store.folder_grants()
                if g["id"] == grant_id and visible(g, person)
            ),
            None,
        )
        if grant is None:
            raise NoSuchGrant
        record = FolderRecord(
            owner=person.sub,
            grant_id=grant_id,
            name=str(grant["name"]),
            added_at=time.time(),
            ignore=folder_rules.validate_list(ignore or []),
            include_hidden=include_hidden,
            use_gitignore=use_gitignore,
            project_id=project_id,
        )
        if not await self.store.add_scoped_folder_record(record):
            raise AlreadyAdded
        self.request(person.sub, grant_id, manual=False)
        return record

    async def update(
        self,
        sub: str,
        grant_id: str,
        *,
        ignore: list[str] | None = None,
        include_hidden: bool | None = None,
        use_gitignore: bool | None = None,
    ) -> FolderRecord | None:
        """Change a folder's own rules; it is checked again at once, so what is ignored now leaves
        the index and what is not comes in. None when the person has no such folder."""
        changes: dict[str, Any] = {}
        if ignore is not None:
            changes["ignore"] = folder_rules.validate_list(ignore)
        if include_hidden is not None:
            changes["include_hidden"] = include_hidden
        if use_gitignore is not None:
            changes["use_gitignore"] = use_gitignore
        record = await self.store.update_folder_record(sub, grant_id, **changes)
        if record is not None and record.status == "active":
            self.request(sub, grant_id, manual=False)
        return record

    async def ignore_file(self, sub: str, grant_id: str, path: str) -> str | None:
        """Add one file of a folder to its ignore list (what Remove on a file of a folder does,
        so that it does not come back at the next check). The pattern that was added; None when
        the person has no such folder; `IgnoreListFull` when the list is."""
        record = await self.store.folder_record(sub, grant_id)
        if record is None:
            return None
        pattern = folder_rules.exact(path)
        if pattern not in record.ignore:
            if len(record.ignore) >= folder_rules.MAX_PATTERNS:
                raise IgnoreListFull
            await self.store.update_folder_record(sub, grant_id, ignore=[*record.ignore, pattern])
        return pattern

    def request(self, sub: str, grant_id: str, *, manual: bool = True, full: bool = False) -> None:
        """Sync now: this folder is checked as soon as it is its turn (before any scheduled
        check), and a check that is under way is followed by another, since it may have begun before
        what the person changed."""
        want = self._wanted.setdefault((sub, grant_id), Want())
        want.manual = want.manual or manual
        want.full = want.full or full
        self._rest.pop((sub, grant_id), None)
        self._wake.set()

    async def remove(self, sub: str, grant_id: str) -> int | None:
        """Take a folder out of a person's Documents: its documents leave the index (and any check
        of it is ended), never touching the folder or the grant. The documents go first and then
        the folder, so that a file that cannot be removed (`DeleteFailed`) leaves the folder where
        it was, to try again, and a stop in between leaves a folder with no documents, which is
        checked again, not documents with no folder. How many documents were removed; None when
        the person has no such folder."""
        record = await self.store.folder_record(sub, grant_id)
        if record is None:
            return None
        self._wanted.pop((sub, grant_id), None)
        try:
            removed = await self._purge(sub, grant_id, close=True)
        except BaseException:
            self.state(sub, grant_id).closed = False  # still the person's folder: checked as before
            raise
        await self.store.delete_folder_record(sub, grant_id)
        self._states.pop((sub, grant_id), None)
        self._rest.pop((sub, grant_id), None)
        return removed

    async def move(self, sub: str, grant_id: str, project_id: str | None) -> FolderRecord | None:
        """Move the indexed words and sidecars, without reading the folder again.
        Close and drain the old sync first so it cannot put a file back in the old scope."""
        record = await self.store.folder_record(sub, grant_id)
        if record is None:
            return None
        held = self.state(sub, grant_id)
        held.closed = True
        if held.task is not None:
            held.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await held.task
        async with held.lock:
            try:
                # Commit the destination first. A stopped process or a failed index move is
                # repaired from this record before the next sync can write any documents.
                record = await self.store.update_folder_record(sub, grant_id, project_id=project_id)
                if record is None:
                    return None
                index = self.saved.index(sub, create=False)
                if index is not None:
                    await self._rescope(record, index)
                return record
            finally:
                held.closed = False

    async def _rescope(self, record: FolderRecord, index: Index) -> None:
        new = f"project:{record.project_id}" if record.project_id else LIBRARY
        old_scopes = await index.folder_scopes(record.grant_id) - {new}
        if old_scopes:
            await self.saved._cancel(
                record.owner, [h.doc_id for h in await index.folder_files(record.grant_id)]
            )
            for old in sorted(old_scopes):
                await index.rescope(old, new, source_prefix=f"folder:{record.grant_id}/")

    async def grant_withdrawn(self, grant_id: str) -> int:
        """The owner took this grant away: the folder's documents leave the index before the
        owner's request returns, and the folder stays in the person's list as `withdrawn`. How
        many documents were removed."""
        removed = 0
        for record in await self.store.folder_records():
            if record.grant_id == grant_id and record.status == "active":
                try:
                    removed += await self._withdraw(record)
                except DeleteFailed as exc:
                    # The grant is gone and nothing more will be added; a file that cannot be
                    # removed now is tried again by the loop, which sees the grant is not there.
                    log.warning("a withdrawn folder's documents could not all be removed: %s", exc)
        return removed

    # --- taking documents out ----------------------------------------------------

    async def _purge(self, sub: str, grant_id: str, *, close: bool) -> int:
        """Every document read from a folder is removed, and anything adding to them is ended.
        With `close`, nothing is added to this folder again by a check that was under way (it
        was removed or withdrawn); without, the folder may come back and be read again."""
        state = self.state(sub, grant_id)
        async with state.lock:  # an add under way finishes first
            if close:
                state.closed = True
        task = state.task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with state.lock:
            gone = await self.saved.drop_folder(sub, grant_id)
        return len(gone)

    async def _withdraw(self, record: FolderRecord) -> int:
        """The grant is gone: the documents go (nothing of it stays searchable), and then the
        folder says why. It is done once: whoever asks while it is being done (the owner's request,
        or a check that found its grant gone) waits for it, and finds it done, so that the owner's
        request never returns before the documents are out. A file that cannot be removed raises
        `DeleteFailed` and leaves the folder as it was, so that it is withdrawn again at the next
        look."""
        state = self.state(record.owner, record.grant_id)
        async with state.withdrawal:
            current = await self.store.folder_record(record.owner, record.grant_id)
            if current is None or current.status != "active":
                return 0
            self._wanted.pop((record.owner, record.grant_id), None)
            removed = await self._purge(record.owner, record.grant_id, close=True)
            verb = "was" if removed == 1 else "were"
            await self.store.update_folder_record(
                record.owner,
                record.grant_id,
                status="withdrawn",
                problem={
                    "code": "withdrawn",
                    "message": (
                        "The owner took away your access to this folder. "
                        f"{plural(removed, 'document')} from it {verb} taken out of your search."
                    ),
                    "at": time.time(),
                    "removed": removed,
                },
            )
            log.info("a folder was withdrawn: %d document(s) removed", removed)
            return removed

    # --- the loop -----------------------------------------------------------------

    async def _loop(self) -> None:
        while True:
            try:
                wait = await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("checking people's folders failed in Workbench itself")
                wait = TICK
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=wait)
            self._wake.clear()

    async def _cycle(self) -> float:
        """Withdraw what was withdrawn and check the folder whose turn it is. How long to wait
        before looking again: 0 when there may be another."""
        records = [r for r in await self.store.folder_records() if r.status == "active"]
        grants = {g["id"]: g for g in await self.store.folder_grants()}
        for record in list(records):
            if not _granted(grants, record):
                await self._withdraw(record)
                records.remove(record)
        if not (await self._documents()).enabled:
            return TICK
        interval = (await self._settings()).sync_minutes * UNIT
        now, rested = time.time(), time.perf_counter()
        due: list[tuple[int, float, FolderRecord, Want]] = []
        soonest = TICK
        for record in records:
            key = (record.owner, record.grant_id)
            if self._rest.get(key, 0.0) > rested:
                soonest = min(soonest, self._rest[key] - rested)
                continue
            want = self._wanted.get(key)
            if want is not None:
                due.append((0, record.added_at, record, want))
            elif record.checked_at is None:
                due.append((1, record.added_at, record, Want()))
            elif now >= record.checked_at + interval:
                due.append((2, record.checked_at, record, Want()))
            else:
                soonest = min(soonest, record.checked_at + interval - now)
        if not due:
            return max(soonest, 0.02)
        due.sort(key=lambda item: item[:2])
        _, _, record, want = due[0]
        self._wanted.pop((record.owner, record.grant_id), None)
        await self._run(record, want)
        return 0.0

    async def _run(self, record: FolderRecord, want: Want) -> None:
        """One check, as a task the folder can be taken from under (`_purge` ends it)."""
        key = (record.owner, record.grant_id)
        state = self.state(*key)
        if state.closed:
            return
        run = Run(time.time(), manual=want.manual, full=want.full)
        state.run = run
        self.begun.append(key)
        del self.begun[:-200]
        task = state.task = asyncio.create_task(self._guarded(record, state, run), name="check")
        try:
            await task
        except asyncio.CancelledError:
            me = asyncio.current_task()
            if me is not None and me.cancelling():
                raise  # this loop is being stopped; a check ended from under it is not news
        finally:
            state.run = None
            state.task = None

    async def _guarded(self, record: FolderRecord, state: _State, run: Run) -> None:
        sub, grant_id = record.owner, record.grant_id
        try:
            await self._check(record, state, run)
        except _Stopped:
            return
        except GrantGone:
            current = await self.store.folder_record(sub, grant_id)
            if current is not None and current.status == "active":
                await self._withdraw(current)
        except _DiskFull as exc:
            free = f" ({files.describe_size(exc.free)} free)" if exc.free is not None else ""
            await self._problem(
                record,
                "disk_full",
                f"The disk Workbench keeps your documents on is full{free}. Reading this folder "
                "stopped; what was read stays, and it carries on at the next check.",
            )
        except IndexUnavailable as exc:
            await self._problem(record, exc.code, exc.message)
        except DeleteFailed as exc:
            await self._problem(record, "delete_failed", exc.message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("checking one person's folder failed in Workbench itself")
            await self._problem(
                record,
                "internal",
                f"Workbench's check of this folder failed ({type(exc).__name__}). "
                "See Workbench's log.",
            )

    async def _problem(self, record: FolderRecord, code: str, message: str) -> None:
        """A check ended without finishing: what is wrong, kept with the folder, and the folder left
        alone for a minute so that it is not hammered."""
        now = time.time()
        self._rest[(record.owner, record.grant_id)] = time.perf_counter() + BACKOFF
        await self.store.update_folder_record(
            record.owner,
            record.grant_id,
            problem={"code": code, "message": message, "at": now},
            checked_at=now,
        )
        log.warning("a folder could not be checked (%s)", code)

    # --- one check ------------------------------------------------------------------

    async def _check(self, record: FolderRecord, state: _State, run: Run) -> None:
        sub, grant_id = record.owner, record.grant_id
        # The rules as they are now: the person may have changed them since the loop looked.
        current = await self.store.folder_record(sub, grant_id)
        if current is None or current.status != "active":
            raise _Stopped
        record = current
        began = time.time()
        install = await self._documents()
        settings = await self._settings()
        index = self.saved.index(sub)
        assert index is not None
        async with state.lock:
            if state.closed:
                raise _Stopped
            await self._rescope(record, index)
        rules = folder_rules.Rules(
            record.ignore, include_hidden=record.include_hidden, use_gitignore=record.use_gitignore
        )
        caps = Caps(max_files=settings.max_files, max_depth=settings.max_depth)
        try:
            walked = await self.folders.scan(
                grant_id, sub, partial(folder_walk.walk, rules=rules, caps=caps)
            )
        except GrantGone:
            raise
        except folder_io.FolderError as exc:
            await self._unreadable(record, exc)
            return
        plan = folder_walk.plan(
            walked, await index.folder_files(grant_id), full=run.full, manual=run.manual
        )
        if state.closed:
            raise _Stopped
        tally = {"added": 0, "changed": 0, "moved": 0, "removed": 0, "skipped": 0, "rechecked": 0}
        # What is gone goes first: nothing stale is left to be found while the rest is read.
        if plan.remove:
            await self._remove(sub, state, [h.doc_id for h in plan.remove])
            tally["removed"] += len(plan.remove)
        run.phase = "reading"
        run.total = len(plan.change) + len(plan.move) + len(plan.add)
        index.originals.mkdir(parents=True, exist_ok=True)
        work = _Work(
            self,
            record,
            state,
            index,
            install,
            settings,
            used=sum(h.size for h in [*plan.keep, *plan.unknown] if h.copied),
            tally=tally,
        )
        for have, found in plan.change:
            await work.take(found, have, "change")
            run.done += 1
        for have, found in plan.move:
            await work.take(found, have, "move")
            run.done += 1
        for found in plan.add:
            await work.take(found, None, "add")
            run.done += 1
        await work.flush()
        now = time.time()
        report: dict[str, Any] = {
            "startedAt": began,
            "finishedAt": now,
            "seconds": round(now - began, 2),
            "mode": "deep" if run.full else ("now" if run.manual else "quick"),
            "directories": walked.directories,
            "entries": walked.entries,
            "files": len(walked.files),
            "bytes": walked.bytes,
            "added": tally["added"],
            "changed": tally["changed"],
            "moved": tally["moved"],
            "removed": tally["removed"],
            "skipped": tally["skipped"],
            "unchanged": len(plan.keep) + len(plan.unknown) + tally["rechecked"],
            "ignored": {"files": walked.ignored_files, "folders": walked.ignored_folders},
            "links": walked.links,
            "caps": {
                "files": walked.over_files,
                "entries": walked.over_entries,
                "bytes": work.over_size,
                "depth": walked.too_deep,
            },
            "left": [asdict(item) for item in walked.left],
            "leftCounts": walked.counts,
            "unlisted": [
                {"path": path, "cause": cause}
                for path, cause in list(walked.unlisted.items())[: folder_walk.SAMPLE]
            ],
            "unlistedTotal": len(walked.unlisted),
        }
        await self.store.update_folder_record(
            sub, grant_id, report=report, problem=None, synced_at=now, checked_at=now
        )
        log.info(
            "checked a folder: %d file(s), %d added, %d changed, %d moved, %d removed, %d skipped",
            len(walked.files),
            tally["added"],
            tally["changed"],
            tally["moved"],
            tally["removed"],
            tally["skipped"],
        )

    async def _remove(self, sub: str, state: _State, doc_ids: list[str]) -> None:
        """Documents of files that are gone, in transactions of at most 500, each under the
        folder's lock (a folder that was removed meanwhile adds and removes nothing more)."""
        for start in range(0, len(doc_ids), 500):
            async with state.lock:
                if state.closed:
                    raise _Stopped
                await self.saved.remove_documents(sub, doc_ids[start : start + 500])

    async def _unreadable(self, record: FolderRecord, exc: folder_io.FolderError) -> None:
        """The folder could not be walked. Not there, or another folder in its place: its documents
        are taken out of the index, and the folder is looked at again at every check. Anything else
        (permission, a busy disk, an unsupported host): its documents stay as they were."""
        sub, grant_id = record.owner, record.grant_id
        gone = isinstance(exc.cause, FileNotFoundError | NotADirectoryError)
        replaced = exc.code == "replaced"
        if not (gone or replaced):
            await self._problem(
                record,
                "unreadable",
                f"Workbench could not look at this folder ({folder_walk.observed(exc)}). Its files "
                "are as they were at the last check.",
            )
            return
        removed = await self._purge(sub, grant_id, close=False)
        verb = "was" if removed == 1 else "were"
        if replaced:
            code = "replaced"
            message = (
                f"This folder was replaced by another one. {plural(removed, 'document')} from it "
                f"{verb} taken out of your search. Ask the owner to grant it again, then add it "
                "again."
            )
        else:
            code = "gone"
            message = (
                f"This folder is not there any more. {plural(removed, 'document')} from it {verb} "
                "taken out of your search, and they come back if the folder does."
            )
        now = time.time()
        self._rest[(sub, grant_id)] = time.perf_counter() + BACKOFF
        await self.store.update_folder_record(
            sub,
            grant_id,
            problem={"code": code, "message": message, "at": now, "removed": removed},
            checked_at=now,
        )
        log.warning("a folder is %s: %d document(s) removed", code, removed)


def _granted(grants: dict[str, dict[str, Any]], record: FolderRecord) -> bool:
    grant = grants.get(record.grant_id)
    return grant is not None and grant["subject"] == record.owner


def _free(root: Path) -> int | None:
    try:
        return shutil.disk_usage(root).free
    except OSError:
        return None


class _Work:
    """The reading of one check: each file taken in turn, what it came to, and the skipped files
    kept for a batch."""

    def __init__(
        self,
        owner: FolderSync,
        record: FolderRecord,
        state: _State,
        index: Index,
        install: DocumentSettings,
        settings: FolderSettings,
        *,
        used: int,
        tally: dict[str, int],
    ) -> None:
        self.owner = owner
        self.record = record
        self.state = state
        self.index = index
        self.install = install
        self.settings = settings
        #: Bytes of the folder's files that are kept as copies, and so count against its limit.
        self.used = used
        self.tally = tally
        self.skips: list[FolderSkip] = []
        #: Whether a file was refused for the folder's size.
        self.over_size = False

    @property
    def sub(self) -> str:
        return self.record.owner

    @property
    def grant_id(self) -> str:
        return self.record.grant_id

    def _named(self, found: Found) -> str:
        return f"{self.record.name}/{found.path}"

    async def flush(self) -> None:
        if not self.skips:
            return
        rows, self.skips = self.skips, []
        async with self.state.lock:
            if self.state.closed:
                raise _Stopped
            await self.index.put_folder_skips(rows)

    async def _skip(
        self, found: Found, held: Held | None, code: str, reason: str, detail: str | None = None
    ) -> None:
        """A file looked at and not taken, kept with why. The document it was, if it kept a copy,
        goes now: what it says is not what the file says."""
        if held is not None and held.copied:
            await self.owner._remove(self.sub, self.state, [held.doc_id])
        self.tally["skipped"] += 1
        self.skips.append(
            FolderSkip(
                NewDocument(
                    id=new_id(),
                    scope=f"project:{self.record.project_id}"
                    if self.record.project_id
                    else LIBRARY,
                    name=self._named(found),
                    media_type="application/octet-stream",
                    bytes=found.size,
                    sha256="",
                    source=folder_source(self.grant_id, found.path),
                    kind="unknown",
                    source_mtime=folder_walk.signature(found),
                ),
                code,
                reason,
                detail,
            )
        )
        if len(self.skips) >= SKIP_BATCH:
            await self.flush()

    async def take(self, found: Found, held: Held | None, how: str) -> None:
        """Read one file of the folder: copy it into the person's own files if it is a document,
        and make it a document (new, changed or moved), or keep it as skipped with its reason."""
        if self.state.closed:
            raise _Stopped
        install, names = self.install, found.path.split("/")
        base = names[-1]
        part = self.index.part_path(new_id())
        verdict: dict[str, files.Detected] = {}
        room = self.settings.max_bytes - self.used

        def check(head: bytes, size: int) -> folder_io.Declined | None:
            try:
                got = files.detect(head, None, base)
            except files.Unsupported as exc:
                return folder_io.Declined(
                    "unsupported", unsupported_words(exc.message), exc.message
                )
            if not got.is_document:
                return folder_io.Declined("not_document", not_a_document_words(got.kind))
            if size > room:
                return folder_io.Declined(
                    "over_folder_size", over_folder_words(self.settings.max_mb), f"{size} > {room}"
                )
            verdict["found"] = got
            return None

        taken: folder_io.Taken | folder_io.Declined | None = None
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0))
        try:
            try:
                taken = await self.owner.folders.read(
                    self.grant_id, self.sub, names, fd, install.max_bytes, files.HEAD_BYTES, check
                )
            except GrantGone:
                raise
            except folder_io.TooLarge as exc:
                await self._skip(
                    found,
                    held,
                    "too_large",
                    too_large_words(exc.size, install.max_bytes),
                    f"{exc.size} bytes",
                )
                return
            except folder_io.ChangedWhileReading:
                await self._skip(found, held, "changed", CHANGED)
                return
            except folder_io.FolderError as exc:
                await self._refused(found, held, exc)
                return
        finally:
            os.close(fd)
            if not isinstance(taken, folder_io.Taken):
                part.unlink(missing_ok=True)
        if isinstance(taken, folder_io.Declined):
            if taken.code == "over_folder_size":
                self.over_size = True
            await self._skip(found, held, taken.code, taken.message, taken.detail)
            return
        try:
            await self._keep(found, held, how, taken, verdict["found"], part)
        finally:
            part.unlink(missing_ok=True)

    async def _refused(self, found: Found, held: Held | None, exc: folder_io.FolderError) -> None:
        """A file that could not be opened or read, said as it was observed."""
        cause = exc.cause
        if isinstance(cause, OSError) and cause.errno == errno.ENOSPC:
            raise _DiskFull(_free(self.owner.saved.root))
        by_code = {
            "hard_links": HARD_LINKS,
            "special": SPECIAL,
            "link": LINKED,
            "mount": MOUNTED,
        }
        if exc.code in by_code:
            await self._skip(found, held, str(exc.code), by_code[str(exc.code)])
        elif is_link(cause):
            await self._skip(found, held, "link", LINKED)
        elif isinstance(cause, FileNotFoundError):
            # Deleted since it was listed: not there, so no document of it either.
            if held is not None:
                await self.owner._remove(self.sub, self.state, [held.doc_id])
                self.tally["removed"] += 1
        elif is_busy(cause):
            await self._skip(found, held, "busy", BUSY)
        else:
            seen = folder_walk.observed(exc)
            await self._skip(found, held, "unreadable", unreadable_words(seen), seen)

    async def _keep(
        self,
        found: Found,
        held: Held | None,
        how: str,
        taken: folder_io.Taken,
        detected: files.Detected,
        part: Path,
    ) -> None:
        """A file that was copied: the same words as the document it has, a file that moved, or a
        document that is new."""
        owner = self.owner
        racy = 0 <= time.time_ns() - taken.mtime_ns < RACY_NS
        signature = f"{taken.mtime_ns}{'!' if racy else ''}"
        name = self._named(found)
        if held is not None and held.copied and held.sha256 == taken.sha256:
            if how == "move":
                async with self.state.lock:
                    if self.state.closed:
                        raise _Stopped
                    moved = await owner.saved.move_folder_document(
                        self.sub, held, self.grant_id, found.path, name, signature
                    )
                if moved is not None:
                    self.used += taken.size
                    self.tally["moved"] += 1
                    return
            elif held.status not in ("skipped", "failed"):
                # The same words: only the time and size it is at now are kept.
                await self.index.set_signature(held.doc_id, signature, taken.size)
                self.used += taken.size
                self.tally["rechecked"] += 1
                return
        elif how == "move" and held is not None:
            # Not the file that was there: that one is gone, and this one is new.
            await owner._remove(self.sub, self.state, [held.doc_id])
            self.tally["removed"] += 1
            held = None
        if detected.zipped and await asyncio.to_thread(files.zip_holds_document, part) is False:
            await self._skip(found, held, "bad_zip", not_word_words(found.path.rsplit("/", 1)[-1]))
            return
        async with self.state.lock:
            if self.state.closed:
                raise _Stopped
            await owner.saved.add(
                self.sub,
                doc_id=part.name.removesuffix(".part"),
                scope=f"project:{self.record.project_id}" if self.record.project_id else LIBRARY,
                name=name,
                detected=detected,
                size=taken.size,
                sha256=taken.sha256,
                source=folder_source(self.grant_id, found.path),
                source_mtime=signature,
            )
        self.used += taken.size
        self.tally["added" if held is None or not held.copied else "changed"] += 1
