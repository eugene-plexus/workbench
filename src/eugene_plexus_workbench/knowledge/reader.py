"""Reading attached files in the background, in a child process (MR2, MRC19).

A file is read when it is attached, not when it is sent: `Reader.start` begins
it, the chip shows "Reading ...", and a send waits for it (`Reader.wait`). The
reading is done by `worker.py` in a short-lived child, started with a bare
environment and an empty working directory of its own, given 120 seconds, and
ended with its whole process tree when they pass. So a hostile or broken file
fails one file with its cause and never takes Workbench, or anyone's chat, down:
a timeout, a crash (the exit code or signal is named) and a refusal are each
a state of one file, and Workbench keeps answering.

What comes back is kept beside the original (`files.read_path_of`) and its
summary -- its state, how many tokens its text is, the page tokens a partial
read is cut from -- goes in the file's row (`Store.merge_file_reading`), so
"ready" is only ever said of a file whose text is on disk. A file removed while
it is read is noticed when the reading ends, and what was written is removed.

At boot a file still `reading` (Workbench was stopped part-way) is read again.
Logs name the file by id and the cause by what was observed, never the content.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import files, token_count
from ..store import FileRecord, Store
from . import extract

log = logging.getLogger(__name__)

#: Seconds a reader may take on one file before it is ended.
TIMEOUT_SECONDS = 120.0
#: How long to wait for a reader's process tree to go once it is ended.
_REAP_SECONDS = 15.0
WORKER_MODULE = "eugene_plexus_workbench.knowledge.worker"
#: Files read at once. Each reader is a process of its own, and a person who
#: drops twenty files should not start twenty.
CONCURRENCY = 2
#: What a reader inherits of Workbench's environment: where programs are, and
#: what Python needs to start. Nothing else, so no token or key goes with it.
_KEEP = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
)
_NT_CRASHES = {
    0xC0000005: "an access violation",
    0xC00000FD: "a stack overflow",
    0xC000001D: "an illegal instruction",
    0xC0000409: "a fatal runtime error",
    0xC0000142: "a library that would not start",
}


# --------------------------------------------------------------------------- #
# the child
# --------------------------------------------------------------------------- #


def worker_python() -> str:
    """The interpreter that can run `-m` commands for Workbench: `sys.executable`,
    or the venv's own `python.exe` under a Windows service, where `sys.executable`
    is pywin32's `pythonservice.exe`, which cannot (the Strata lesson). Never an
    interpreter from PATH."""
    if Path(sys.executable).name.lower() != "pythonservice.exe":
        return sys.executable
    python = Path(sys.prefix) / "Scripts" / "python.exe"
    if not python.is_file():
        raise FileNotFoundError(f"The service's Python interpreter is missing: {python}")
    return str(python)


def worker_command(
    python: str, source: Path, output: Path, kind: str, language: str | None
) -> list[str]:
    return [python, "-m", WORKER_MODULE, str(source), str(output), kind, language or "-"]


def child_environment() -> dict[str, str]:
    env = {key: os.environ[key] for key in _KEEP if key in os.environ}
    env["PYTHONUTF8"] = "1"
    return env


def kill_tree(process: subprocess.Popen[bytes]) -> None:
    """End a reader and everything it started. On Windows the interpreter in a
    venv is a launcher that starts the real one, so the launcher alone is not it."""
    with contextlib.suppress(Exception):
        if sys.platform == "win32":
            taskkill = (
                Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe"
            )
            subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=_REAP_SECONDS,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()


def crash_cause(code: int, stderr: str) -> str:
    """What stopped a reader that left no result, as observed: the exit code
    or signal, and the last thing it said if it said one."""
    if code < 0:
        try:
            what = f"the process was ended by {signal.Signals(-code).name}"
        except ValueError:
            what = f"exit code {code}"
    elif code >= 0x80000000:
        what = f"exit code 0x{code:08X}, {_NT_CRASHES.get(code, 'a crash')}"
    else:
        what = f"exit code {code}"
    last = next(
        (
            line.strip()
            for line in reversed(stderr.splitlines())
            if any(word in line for word in ("Fatal", "fatal", "Error", "error"))
        ),
        "",
    )
    return f"{what}; it said: {extract.say(last, 120)}" if last else what


# --------------------------------------------------------------------------- #
# what a reading comes to
# --------------------------------------------------------------------------- #


def summarize(extracted: extract.Extracted) -> dict[str, Any]:
    """The part of a reading a request and a chip are decided from, small enough
    to keep in a file's row: how large the text is in tokens (a PDF page by page,
    so a partial read can be cut at a page), and what was noticed in it."""
    texts = [extract.render_part(p) for p in extracted.parts]
    last = len(texts) - 1
    # A blank line joins one part to the next, and is a token of its own.
    per_part = [token_count.estimate(t) + (1 if i < last else 0) for i, t in enumerate(texts)]
    return {
        "state": "ready",
        "kind": extracted.kind,
        "language": extracted.language,
        "pages": extracted.pages,
        "lines": extracted.lines,
        "headings": len(extracted.headings),
        "chars": sum(len(t) for t in texts),
        "tokens": sum(per_part),
        "partTokens": per_part if extracted.pages is not None else None,
        "notes": extracted.notes,
        "truncated": extracted.truncated,
        "code": None,
        "reason": None,
        "message": None,
        "detail": None,
    }


def failure(
    state: str, code: str, reason: str, *, pages: int | None = None, detail: str | None = None
) -> dict[str, Any]:
    """A file that gave no text, as its reading records it."""
    return {
        "state": state,
        "code": code,
        "reason": reason,
        "message": f"{'Skipped' if state == 'skipped' else 'Failed'}: {reason}",
        "pages": pages,
        "detail": detail,
    }


@dataclass
class Outcome:
    """How one reading ended: `changes` is what to set in the file's reading,
    and `cancelled` says the file was removed meanwhile. `extracted` is the text
    itself, kept only for a reading that is not of an attached file (`Reader.extract`)."""

    changes: dict[str, Any]
    cancelled: bool = False
    extracted: extract.Extracted | None = None


@dataclass
class Extraction:
    """What `Reader.extract` came to: the text read from a file (and `summary`, how
    large it is and what was noticed in it), or why there is none (`failure`, as
    `failure()` says it), or that it was removed meanwhile (`cancelled`)."""

    extracted: extract.Extracted | None = None
    summary: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    cancelled: bool = False


def _safe(key: str) -> str:
    """A key as a file name: Windows allows no colon in one."""
    return re.sub(r"[^A-Za-z0-9_.-]", "-", key)


class Reader:
    """Every file being read, by id. One per Workbench."""

    def __init__(self, store: Store, root: Path, *, concurrency: int = CONCURRENCY) -> None:
        self._store = store
        self._root = root
        self._slots = asyncio.Semaphore(concurrency)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._children: dict[str, subprocess.Popen[bytes]] = {}
        self._cancelled: set[str] = set()

    # --- starting and waiting -------------------------------------------

    def reading(self, file_id: str) -> bool:
        return file_id in self._tasks

    async def start(self, record: FileRecord) -> None:
        """Begin reading `record` (a document whose reading is `reading`). A file
        already being read is left to it."""
        if record.id in self._tasks:
            return
        self._cancelled.discard(record.id)
        task = asyncio.create_task(self._read(record), name=f"reading-{record.id}")
        self._tasks[record.id] = task

        def forget(_task: asyncio.Task[None], file_id: str = record.id) -> None:
            self._tasks.pop(file_id, None)

        task.add_done_callback(forget)

    async def wait(self, file_ids: Iterable[str], seconds: float) -> set[str]:
        """Wait for these files to be read, for at most `seconds`. The ids still
        being read after that."""
        ids = list(file_ids)
        tasks = {self._tasks[i] for i in ids if i in self._tasks}
        if tasks:
            await asyncio.wait(tasks, timeout=seconds)
        return {i for i in ids if i in self._tasks}

    async def resume(self) -> int:
        """Boot: read again the files a stop left part-way. How many. Nothing is
        being read yet, so what the work directory holds was left by the stop."""
        await asyncio.to_thread(shutil.rmtree, self._root / "reading", True)
        found = await self._store.files_reading()
        for record in found:
            await self.start(record)
        return len(found)

    async def cancel(self, file_ids: Iterable[str]) -> None:
        """These files were removed: end their readers, and forget them."""
        ids = [i for i in file_ids if i in self._tasks or i in self._children]
        self._cancelled.update(ids)
        for file_id in ids:
            child = self._children.get(file_id)
            if child is not None:
                kill_tree(child)
        tasks = [self._tasks[i] for i in ids if i in self._tasks]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def aclose(self) -> None:
        """Shutdown: end every reader and wait for its task."""
        self._cancelled.update(self._tasks)
        for child in list(self._children.values()):
            kill_tree(child)
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    # --- reading --------------------------------------------------------

    async def _read(self, record: FileRecord) -> None:
        try:
            async with self._slots:
                if record.id in self._cancelled:
                    return
                started = time.perf_counter()
                outcome = await asyncio.to_thread(self._work, record)
            if outcome.cancelled:
                return
            outcome.changes["finishedAt"] = time.time()
            kept = await self._store.merge_file_reading(record.id, outcome.changes)
            if kept is None:
                # Removed while it was read: what was written goes too.
                files.read_path_of(self._root, record.owner, record.id).unlink(missing_ok=True)
                return
            state = outcome.changes["state"]
            if state == "ready":
                log.info(
                    "read file %s as %s: %s page(s), about %s tokens, in %.1f s",
                    record.id,
                    outcome.changes["kind"],
                    outcome.changes["pages"] or "no",
                    f"{outcome.changes['tokens']:,}",
                    time.perf_counter() - started,
                )
            else:
                log.warning(
                    "could not read file %s (%s, %s): %s",
                    record.id,
                    state,
                    outcome.changes["code"],
                    outcome.changes["detail"] or outcome.changes["reason"],
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A bug here must not leave a chip saying "Reading" for ever.
            log.exception("reading file %s failed in Workbench itself", record.id)
            with contextlib.suppress(Exception):
                await self._store.merge_file_reading(
                    record.id,
                    {
                        **failure(
                            "failed",
                            "internal",
                            f"Workbench could not read this file ({type(exc).__name__}). "
                            "See Workbench's log.",
                            detail=f"{type(exc).__name__}: {exc}",
                        ),
                        "finishedAt": time.time(),
                    },
                )

    def _work(self, record: FileRecord) -> Outcome:
        """One file, start to end, in a thread: the child, its result, the text
        kept beside the original."""
        reading = record.reading or {}
        return self._run_file(
            record.id,
            files.path_of(self._root, record.owner, record.id),
            str(reading.get("kind") or "text"),
            reading.get("language"),
            record,
        )

    def _run_file(
        self, key: str, source: Path, kind: str, language: str | None, record: FileRecord | None
    ) -> Outcome:
        """The child that reads `source`, and what it left. For an attached file
        (`record`) the text is moved beside it; without one it is handed back in the
        outcome."""
        if not source.is_file():
            return Outcome(
                failure(
                    "failed",
                    "missing",
                    "Workbench's copy of this file is not on its disk any more.",
                    detail="the stored file is missing",
                )
            )
        work = self._root / "reading"
        work.mkdir(parents=True, exist_ok=True)
        output = work / f"{_safe(key)}.json"
        try:
            return self._read_in_child(key, source, kind, language, work, output, record)
        finally:
            # Whatever ended it, no result file or half of one is left lying about.
            output.unlink(missing_ok=True)
            output.with_name(output.name + ".part").unlink(missing_ok=True)

    async def extract(
        self, key: str, source: Path, kind: str, language: str | None = None
    ) -> Extraction:
        """Read one file in a child and hand the text back, for a reader that keeps it
        in its own way (saved documents: `ingest.py`). `key` names the reading, so
        `cancel([key])` can end it; it must not be a file id (`doc:` and the like).
        The same child, limits and words as a file that is attached, sharing its
        slots, so a person who adds twenty files does not start twenty readers."""
        self._cancelled.discard(key)
        async with self._slots:
            outcome = await asyncio.to_thread(self._run_file, key, source, kind, language, None)
        if outcome.cancelled:
            return Extraction(cancelled=True)
        if outcome.extracted is None:
            return Extraction(failure=outcome.changes)
        return Extraction(extracted=outcome.extracted, summary=outcome.changes)

    def _read_in_child(
        self,
        key: str,
        source: Path,
        kind: str,
        language: str | None,
        work: Path,
        output: Path,
        record: FileRecord | None,
    ) -> Outcome:
        output.unlink(missing_ok=True)
        try:
            argv = worker_command(worker_python(), source, output, kind, language)
            child = self._start_child(key, argv, work)
        except (OSError, FileNotFoundError) as exc:
            return Outcome(
                failure(
                    "failed",
                    "unavailable",
                    f"Workbench could not start its reader ({extract.say(str(exc))}).",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
        if key in self._cancelled:
            kill_tree(child)  # removed between its being started and its child's
        timed_out = False
        try:
            try:
                _, stderr = child.communicate(timeout=TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_tree(child)
                try:
                    _, stderr = child.communicate(timeout=_REAP_SECONDS)
                except subprocess.TimeoutExpired:
                    stderr = b""
        finally:
            self._children.pop(key, None)
        if key in self._cancelled:
            return Outcome({}, cancelled=True)
        if timed_out:
            return Outcome(
                failure(
                    "failed",
                    "timeout",
                    f"reading this file took longer than {TIMEOUT_SECONDS:g} seconds, "
                    "so Workbench stopped.",
                    detail=f"ended after {TIMEOUT_SECONDS:g} s",
                )
            )
        return self._result(
            record, child.returncode, stderr.decode("utf-8", errors="replace"), output
        )

    def _start_child(self, file_id: str, argv: list[str], cwd: Path) -> subprocess.Popen[bytes]:
        if sys.platform == "win32":
            child = subprocess.Popen(
                argv,
                cwd=cwd,
                env=child_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            child = subprocess.Popen(
                argv,
                cwd=cwd,
                env=child_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        self._children[file_id] = child
        return child

    def _result(
        self, record: FileRecord | None, code: int | None, stderr: str, output: Path
    ) -> Outcome:
        """What the child left: its result file, or the cause it died."""
        try:
            data = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            cause = crash_cause(code if code is not None else -1, stderr)
            output.unlink(missing_ok=True)
            return Outcome(
                failure(
                    "failed",
                    "crashed",
                    f"Workbench could not read this file (the reader stopped unexpectedly: "
                    f"{cause}).",
                    detail=f"{cause}; no result ({type(exc).__name__})",
                )
            )
        if not isinstance(data, dict):
            output.unlink(missing_ok=True)
            return Outcome(
                failure(
                    "failed",
                    "crashed",
                    "Workbench could not read this file (its reader gave no result).",
                    detail="the result was not an object",
                )
            )
        if not data.get("ok"):
            output.unlink(missing_ok=True)
            return Outcome(
                failure(
                    "skipped" if data.get("state") == "skipped" else "failed",
                    str(data.get("code") or "damaged"),
                    str(data.get("reason") or "Workbench could not read this file."),
                    pages=data.get("pages") if isinstance(data.get("pages"), int) else None,
                    detail=data.get("detail"),
                )
            )
        try:
            extracted = extract.Extracted.from_json(data)
        except ValueError as exc:
            output.unlink(missing_ok=True)
            return Outcome(
                failure(
                    "failed",
                    "damaged",
                    "Workbench could not read this file (its reader gave text it could not use).",
                    detail=str(exc),
                )
            )
        if record is None:
            return Outcome({**summarize(extracted), "state": "ready"}, extracted=extracted)
        os.replace(output, files.read_path_of(self._root, record.owner, record.id))
        return Outcome({**summarize(extracted), "state": "ready"})
