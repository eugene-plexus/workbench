"""Reading saved documents in the background (MR4): the states a document passes
through and what each says, one worker for everyone in turn, a stop and a removal,
and what the boot puts right. Real files and, where it matters, real child processes;
a stand-in reader only where a test must say exactly when a reading ends.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import textwrap
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench.knowledge import extract
from eugene_plexus_workbench.knowledge import reader as readers
from eugene_plexus_workbench.knowledge.index import (
    LIBRARY,
    Index,
    Indexes,
    NewDocument,
    chat_scope,
)
from eugene_plexus_workbench.knowledge.ingest import Ingest, key_of
from eugene_plexus_workbench.knowledge.reader import Extraction, Reader
from eugene_plexus_workbench.knowledge.search import Retriever, plan_query
from eugene_plexus_workbench.store import Store

from . import documents as docs
from .test_knowledge_index import ADA, BO, RARE, new, put

Cap = Callable[[], Awaitable[int]]


async def big_cap() -> int:
    return 100_000


@dataclass
class Rig:
    """An index directory, a reader, and the worker over them."""

    indexes: Indexes
    reader: Any
    ingest: Ingest
    root: Path

    def index(self, sub: str = ADA) -> Index:
        found = self.indexes.get(sub)
        assert found is not None
        return found

    async def add(
        self,
        doc_id: str,
        data: bytes | str,
        *,
        sub: str = ADA,
        name: str | None = None,
        kind: str = "markdown",
        scope: str = LIBRARY,
        source: str = "upload",
        language: str | None = None,
    ) -> None:
        """A file brought in: its original on the disk, its document queued, the worker woken."""
        index = self.index(sub)
        raw = data.encode("utf-8") if isinstance(data, str) else data
        index.originals.mkdir(parents=True, exist_ok=True)
        index.original_path(doc_id).write_bytes(raw)
        await index.add(
            NewDocument(
                id=doc_id,
                scope=scope,
                name=name or f"{doc_id}.md",
                media_type="text/markdown",
                bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                source=source,
                kind=kind,
                language=language,
            )
        )
        self.ingest.wake(sub)

    async def done(self, sub: str, *ids: str, seconds: float = 60) -> None:
        left = await self.ingest.wait(sub, list(ids), seconds)
        assert not left, f"still being read: {left}"

    async def status(self, doc_id: str, sub: str = ADA) -> str:
        doc = await self.index(sub).document(doc_id)
        assert doc is not None
        return doc.status


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    root = tmp_path / "data"
    indexes = Indexes(root)
    reader = Reader(Store(tmp_path / "workbench.sqlite3"), root)
    ingest = Ingest(indexes, reader, big_cap)
    ingest.start()
    try:
        yield Rig(indexes, reader, ingest, root)
    finally:
        await ingest.aclose()
        await reader.aclose()
        await indexes.aclose()


class FakeReader:
    """A reader that does what a test says, when it says: gates hold a reading until
    the test lets it end; `texts` is what a key reads as (else the file's own words)."""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.inflight = 0
        self.peak = 0
        self.gates: dict[str, asyncio.Event] = {}
        self.entered: dict[str, asyncio.Event] = {}
        self.texts: dict[str, list[str]] = {}
        self.failures: dict[str, BaseException] = {}
        self.cancels: list[str] = []
        self._cancelled: set[str] = set()

    def hold(self, key: str) -> asyncio.Event:
        self.gates[key] = asyncio.Event()
        self.entered[key] = asyncio.Event()
        return self.gates[key]

    async def extract(
        self, key: str, source: Path, kind: str, language: str | None = None
    ) -> Extraction:
        self.order.append(key)
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        self.entered.setdefault(key, asyncio.Event()).set()
        try:
            if key in self.gates:
                await self.gates[key].wait()
            if key in self._cancelled:
                return Extraction(cancelled=True)
            if key in self.failures:
                raise self.failures[key]
            queued = self.texts.get(key)
            text = queued.pop(0) if queued else await asyncio.to_thread(source.read_text, "utf-8")
            extracted = extract.Extracted(
                kind="markdown",
                parts=[extract.Part(text)],
                lines=extract.line_count(text),
                headings=extract.markdown_headings(text),
            )
            return Extraction(extracted=extracted, summary=readers.summarize(extracted))
        finally:
            self.inflight -= 1

    async def cancel(self, keys: list[str]) -> None:
        for key in keys:
            self.cancels.append(key)
            self._cancelled.add(key)
            if key in self.gates:
                self.gates[key].set()  # a killed reader returns at once


@pytest.fixture
async def fake(tmp_path: Path) -> AsyncIterator[Rig]:
    root = tmp_path / "data"
    indexes = Indexes(root)
    reader = FakeReader()
    ingest = Ingest(indexes, reader, big_cap)  # type: ignore[arg-type]
    ingest.start()
    try:
        yield Rig(indexes, reader, ingest, root)
    finally:
        await ingest.aclose()
        await indexes.aclose()


async def until(condition: Callable[[], Awaitable[bool]], seconds: float = 20.0) -> bool:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        if await condition():
            return True
        await asyncio.sleep(0.02)
    return await condition()


# --------------------------------------------------------------------------- #
# what reading comes to, with the real reader
# --------------------------------------------------------------------------- #


async def test_a_file_goes_from_queued_to_ready_and_can_be_found_by_its_words(rig: Rig) -> None:
    await rig.add("doc00001", f"# Notes\n\nThe {RARE} appears in this file.\n")
    await rig.done(ADA, "doc00001")
    doc = await rig.index().document("doc00001")
    assert doc is not None
    assert (doc.status, doc.chunks, doc.kind, doc.lines) == ("ready", 1, "markdown", 3)
    assert doc.tokens_est and doc.indexed_at and doc.read_at and doc.reason is None
    [hit] = await rig.index().search(f'"{RARE}"', [LIBRARY], 5)
    assert hit.heading_path == "Notes" and hit.doc_id == "doc00001"
    text = rig.index().text_path("doc00001").read_text(encoding="utf-8")
    assert text == f"# Notes\n\nThe {RARE} appears in this file.\n"
    assert rig.index().original_path("doc00001").read_text(encoding="utf-8") == text
    assert not list(rig.index().dir.glob("*.part")) and not list(
        rig.index().originals.glob("*.part")
    )


async def test_a_pdf_is_read_with_its_pages_and_the_code_word_is_on_page_three(rig: Rig) -> None:
    await rig.add("doc00001", docs.three_pages(), name="report.pdf", kind="pdf")
    await rig.done(ADA, "doc00001")
    doc = await rig.index().document("doc00001")
    assert doc is not None and (doc.status, doc.pages, doc.kind) == ("ready", 3, "pdf")
    [hit] = await rig.index().search(f'"{docs.CODE_WORD.split("-")[0].lower()}"', [LIBRARY], 5)
    assert hit.page_end == 3
    shown = rig.index().text_path("doc00001").read_text(encoding="utf-8")
    assert "[page 3]" in shown and docs.CODE_WORD in shown, "the text shows its page marks"


async def test_a_word_file_is_read_with_its_headings_kept_as_the_path(rig: Rig) -> None:
    await rig.add("doc00001", docs.docx(), name="r.docx", kind="docx")
    await rig.done(ADA, "doc00001")
    hits = await rig.index().search(f'"{docs.CODE_WORD.split("-")[0].lower()}"', [LIBRARY], 5)
    assert [h.heading_path for h in hits] == ["Conclusion"]


@pytest.mark.parametrize(
    ("make", "state", "code", "says"),
    [
        (
            lambda: docs.pdf_pictures(3),
            "skipped",
            "no_text",
            "Skipped: this PDF has no text, only pictures of pages. "
            "Workbench cannot read scanned pages yet.",
        ),
        (docs.pdf_encrypted, "skipped", "password", "Skipped: this file is password-protected."),
        (docs.pdf_damaged, "failed", "damaged", "Failed: Workbench could not read this file ("),
    ],
)
async def test_a_file_that_gives_no_text_ends_in_a_state_that_says_why_and_keeps_nothing(
    rig: Rig, make: Callable[[], bytes], state: str, code: str, says: str
) -> None:
    await rig.add("doc00001", make(), name="r.pdf", kind="pdf")
    await rig.done(ADA, "doc00001")
    doc = await rig.index().document("doc00001")
    assert doc is not None
    assert (doc.status, doc.code) == (state, code)
    assert doc.reason is not None and doc.reason.startswith(says)
    assert doc.chunks == 0 and not doc.searchable
    assert not rig.index().text_path("doc00001").exists(), "no text, none kept"
    assert await rig.index().search('"annual"', [LIBRARY], 5) == []
    if code == "no_text":
        assert doc.pages == 3, "a scan still says how many pages it has"
    if code == "damaged":
        assert "PDFium: Data format error" in doc.reason


async def test_an_empty_text_file_is_skipped(rig: Rig) -> None:
    await rig.add("doc00001", "  \n\n", name="e.txt", kind="text")
    await rig.done(ADA, "doc00001")
    doc = await rig.index().document("doc00001")
    assert doc is not None and doc.status == "skipped" and doc.code == "empty"
    assert doc.reason == "Skipped: this file has no text in it."


async def test_a_file_that_is_not_on_the_disk_fails_by_name_and_the_next_is_read(rig: Rig) -> None:
    await rig.add("doc00001", "Some words.")
    rig.index().original_path("doc00001").unlink()
    await rig.add("doc00002", f"The {RARE} word.")
    await rig.done(ADA, "doc00001", "doc00002")
    one = await rig.index().document("doc00001")
    assert one is not None and (one.status, one.code) == ("failed", "missing")
    assert one.reason == "Failed: Workbench's copy of this file is not on its disk any more."
    assert await rig.status("doc00002") == "ready"


async def test_a_reader_that_hangs_is_ended_and_the_document_says_so_while_the_next_is_read(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readers, "TIMEOUT_SECONDS", 2.0)
    real = readers.worker_command

    def command(
        python: str, source: Path, output: Path, kind: str, language: str | None
    ) -> list[str]:
        if source.name == "doc00001":
            hang = "import sys, time\ntime.sleep(600)\n"
            return [python, "-c", textwrap.dedent(hang), str(source), str(output)]
        return real(python, source, output, kind, language)

    monkeypatch.setattr(readers, "worker_command", command)
    await rig.add("doc00001", "Some words.")
    await rig.add("doc00002", f"The {RARE} word.")
    started = time.perf_counter()
    await rig.done(ADA, "doc00001", "doc00002", seconds=60)
    assert time.perf_counter() - started < 30
    hung = await rig.index().document("doc00001")
    assert hung is not None and (hung.status, hung.code) == ("failed", "timeout")
    assert (
        hung.reason == "Failed: reading this file took longer than 2 seconds, so Workbench stopped."
    )
    assert await rig.status("doc00002") == "ready"


async def test_cancelling_a_documents_key_ends_its_real_reader_and_the_next_is_read(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = readers.worker_command

    def command(
        python: str, source: Path, output: Path, kind: str, language: str | None
    ) -> list[str]:
        if source.name == "doc00001":
            hang = "import os, sys, time\nopen(sys.argv[2] + '.pid', 'w').write(str(os.getpid()))\ntime.sleep(600)\n"
            return [python, "-c", textwrap.dedent(hang), str(source), str(output)]
        return real(python, source, output, kind, language)

    monkeypatch.setattr(readers, "worker_command", command)
    await rig.add("doc00001", "Some words.")
    pid_file = rig.root / "reading" / "doc-doc00001.json.pid"
    assert await until(lambda: _exists(pid_file)), "the reader started"
    pid = int(pid_file.read_text(encoding="utf-8"))
    await rig.ingest.cancel(ADA, ["doc00001"])
    assert await until(lambda: _gone(pid), 20), "the process that was reading is gone"
    assert await rig.status("doc00001") == "reading", "it is only the reader that was ended"
    await rig.index().delete("doc00001")
    await rig.add("doc00002", f"The {RARE} word.")
    await rig.done(ADA, "doc00002")
    assert await rig.status("doc00002") == "ready"


async def _exists(path: Path) -> bool:
    return await asyncio.to_thread(path.exists)


async def _gone(pid: int) -> bool:
    return not alive(pid)


def alive(pid: int) -> bool:
    import os
    import subprocess
    import sys

    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


async def test_a_reader_that_dies_names_how(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    def command(
        python: str, source: Path, output: Path, kind: str, language: str | None
    ) -> list[str]:
        return [python, "-c", "import sys; sys.stderr.write('Fatal error: boom'); sys.exit(3)"]

    monkeypatch.setattr(readers, "worker_command", command)
    await rig.add("doc00001", "Some words.")
    await rig.done(ADA, "doc00001")
    doc = await rig.index().document("doc00001")
    assert doc is not None and (doc.status, doc.code) == ("failed", "crashed")
    assert doc.reason is not None and "exit code 3" in doc.reason and "boom" in doc.reason


# --------------------------------------------------------------------------- #
# one worker, everyone in turn
# --------------------------------------------------------------------------- #


async def test_one_document_is_read_at_a_time_and_each_person_has_a_turn(fake: Rig) -> None:
    reader: FakeReader = fake.reader
    gate = reader.hold(key_of("ada00000"))
    await fake.add("ada00000", "Ada 0.", sub=ADA)
    await reader.entered[key_of("ada00000")].wait()
    for n in (1, 2):
        await fake.add(f"ada0000{n}", f"Ada {n}.", sub=ADA)
    for n in range(3):
        await fake.add(f"bo00000{n}", f"Bo {n}.", sub=BO)
    gate.set()
    await fake.done(ADA, "ada00000", "ada00001", "ada00002")
    await fake.done(BO, "bo000000", "bo000001", "bo000002")
    assert reader.peak == 1, "one worker, one document at a time"
    names = [k.removeprefix("doc:") for k in reader.order]
    # Once Bo is waiting, Ada has at most one more, then Bo, then Ada, and so on.
    assert names == ["ada00000", "ada00001", "bo000000", "ada00002", "bo000001", "bo000002"]
    assert [(sub, i) for sub, i in fake.ingest.served][2] == (BO, "bo000000")


async def test_a_person_with_many_files_does_not_keep_another_waiting(fake: Rig) -> None:
    reader: FakeReader = fake.reader
    gate = reader.hold(key_of("ada00000"))
    for n in range(10):
        await fake.add(f"ada{n:05d}", f"Ada {n}.", sub=ADA)
    await reader.entered[key_of("ada00000")].wait()
    await fake.add("bo000001", "Bo's one file.", sub=BO)
    gate.set()
    await fake.done(BO, "bo000001")
    assert reader.order.index(key_of("bo000001")) <= 2, "after at most one more of Ada's"
    await fake.done(ADA, *(f"ada{n:05d}" for n in range(10)))


async def test_the_states_are_true_while_a_document_is_read(fake: Rig) -> None:
    reader: FakeReader = fake.reader
    gate = reader.hold(key_of("doc00001"))
    await fake.add("doc00001", f"The {RARE} word.")
    await reader.entered[key_of("doc00001")].wait()
    doc = await fake.index().document("doc00001")
    assert doc is not None and doc.status == "reading" and doc.chunks == 0 and not doc.searchable
    assert await fake.index().search(f'"{RARE}"', [LIBRARY], 5) == [], "not found before it says so"
    gate.set()
    await fake.done(ADA, "doc00001")
    assert await fake.status("doc00001") == "ready"
    assert len(await fake.index().search(f'"{RARE}"', [LIBRARY], 5)) == 1


async def test_a_document_that_is_owed_more_work_is_searchable_by_words_and_not_ready(
    tmp_path: Path,
) -> None:
    owed_calls: list[str] = []

    async def owed(index: Index, doc: Any) -> bool:
        owed_calls.append(doc.status)
        return True

    root = tmp_path / "data"
    indexes, reader = Indexes(root), FakeReader()
    ingest = Ingest(indexes, reader, big_cap, owed=owed)  # type: ignore[arg-type]
    ingest.start()
    try:
        rig = Rig(indexes, reader, ingest, root)
        await rig.add("doc00001", f"The {RARE} word.")
        await rig.done(ADA, "doc00001")
        assert await rig.status("doc00001") == "searchable", "words now; the rest is owed"
        assert owed_calls == ["searchable"]
        assert len(await rig.index().search(f'"{RARE}"', [LIBRARY], 5)) == 1
    finally:
        await ingest.aclose()
        await indexes.aclose()


async def test_a_bug_in_one_reading_fails_that_document_and_the_next_is_read(fake: Rig) -> None:
    reader: FakeReader = fake.reader
    reader.failures[key_of("doc00001")] = ValueError("a bug in Workbench")
    await fake.add("doc00001", "One.")
    await fake.add("doc00002", f"The {RARE} word.")
    await fake.done(ADA, "doc00001", "doc00002")
    one = await fake.index().document("doc00001")
    assert one is not None and (one.status, one.code) == ("failed", "internal")
    assert (
        one.reason
        == "Failed: Workbench could not read this file (ValueError). See Workbench's log."
    )
    assert "ValueError: a bug in Workbench" in (one.detail or "")
    assert await fake.status("doc00002") == "ready"


async def test_the_passage_limit_skips_the_file_that_would_pass_it(tmp_path: Path) -> None:
    async def small() -> int:
        return 3

    root = tmp_path / "data"
    indexes, reader = Indexes(root), FakeReader()
    ingest = Ingest(indexes, reader, small)  # type: ignore[arg-type]
    ingest.start()
    try:
        rig = Rig(indexes, reader, ingest, root)
        await rig.add("doc00001", "# A\n\nOne.\n\n# B\n\nTwo.\n")
        await rig.add("doc00002", "# A\n\nOne.\n\n# B\n\nTwo.\n\n# C\n\nThree.\n")
        await rig.done(ADA, "doc00001", "doc00002")
        assert await rig.status("doc00001") == "ready"
        two = await rig.index().document("doc00002")
        assert two is not None and (two.status, two.code, two.chunks) == (
            "skipped",
            "too_many_passages",
            0,
        )
        assert not rig.index().text_path("doc00002").exists()
    finally:
        await ingest.aclose()
        await indexes.aclose()


async def test_waiting_ends_when_the_documents_are_read_and_not_before_its_time(fake: Rig) -> None:
    reader: FakeReader = fake.reader
    gate = reader.hold(key_of("doc00001"))
    await fake.add("doc00001", "Words.")
    started = time.perf_counter()
    assert await fake.ingest.wait(ADA, ["doc00001"], 0.3) == {"doc00001"}
    assert 0.25 < time.perf_counter() - started < 5
    gate.set()
    assert await fake.ingest.wait(ADA, ["doc00001"], 20) == set()
    assert await fake.ingest.wait(ADA, ["no-such-doc"], 1) == set()
    assert await fake.ingest.wait(BO, ["doc00001"], 1) == set(), "nobody else's"


async def test_an_index_that_cannot_be_used_does_not_stop_the_others(fake: Rig) -> None:
    await fake.add("doc00001", "Ada's words.", sub=ADA)
    await fake.done(ADA, "doc00001")
    await fake.indexes.get(ADA).close()  # type: ignore[union-attr]
    fake.index(ADA).path.write_bytes(b"not a database " * 300)
    fake.ingest.wake(ADA)
    await fake.add("doc00002", f"The {RARE} word.", sub=BO)
    await fake.done(BO, "doc00002")
    assert await fake.status("doc00002", BO) == "ready"


# --------------------------------------------------------------------------- #
# a removal, a replacement, a stop
# --------------------------------------------------------------------------- #


async def test_removing_a_document_while_it_is_read_ends_its_reader_and_nothing_is_kept(
    fake: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    reader: FakeReader = fake.reader
    reader.hold(key_of("doc00001"))
    await fake.add("doc00001", f"The {RARE} word.")
    await fake.add("doc00002", "A second file.")
    await reader.entered[key_of("doc00001")].wait()
    await fake.ingest.cancel(ADA, ["doc00001"])
    assert reader.cancels == [key_of("doc00001")], "its reader is ended by its own key"
    assert await fake.index().delete("doc00001") is not None
    await fake.done(ADA, "doc00002")
    assert await fake.index().document("doc00001") is None
    assert await fake.index().search(f'"{RARE}"', [LIBRARY], 5) == []
    assert not fake.index().text_path("doc00001").exists()
    assert not list(fake.index().dir.glob("doc00001*")) and not list(
        fake.index().originals.glob("doc00001*")
    )
    assert await fake.status("doc00002") == "ready", "and the worker went on"
    assert not [r for r in caplog.records if "failed in Workbench itself" in r.getMessage()], (
        "an ended reading is a normal end, not a bug in Workbench that was caught"
    )


async def test_a_reading_that_finishes_after_its_document_was_removed_keeps_nothing(
    fake: Rig,
) -> None:
    reader: FakeReader = fake.reader
    gate = reader.hold(key_of("doc00001"))
    await fake.add("doc00001", f"The {RARE} word.")
    await reader.entered[key_of("doc00001")].wait()
    await fake.index().delete("doc00001")  # removed, and its reader is past being ended
    gate.set()
    await fake.add("doc00002", "Another file.")
    await fake.done(ADA, "doc00002")
    assert await fake.index().search(f'"{RARE}"', [LIBRARY], 5) == []
    assert not list(fake.index().dir.glob("doc00001*"))


async def test_a_document_read_again_while_it_is_read_discards_the_old_reading_and_ends_on_the_new(
    fake: Rig,
) -> None:
    reader: FakeReader = fake.reader
    reader.texts[key_of("doc00001")] = ["The old words.", "The fresh words."]
    gate = reader.hold(key_of("doc00001"))
    await fake.add("doc00001", "Placeholder.")
    await reader.entered[key_of("doc00001")].wait()
    assert await fake.index().requeue("doc00001") is not None  # "Re-read", mid-reading
    fake.ingest.wake(ADA)
    gate.set()
    await until(lambda: _is(fake, "doc00001", "ready"))
    hits = await fake.index().search('"fresh" OR "old"', [LIBRARY], 5)
    assert [h.text for h in hits] == ["The fresh words."], "the stale reading was thrown away"
    doc = await fake.index().document("doc00001")
    assert doc is not None and doc.attempt == 1


async def _is(rig: Rig, doc_id: str, state: str) -> bool:
    return await rig.status(doc_id) == state


async def test_a_stop_in_the_middle_resumes_at_boot_and_each_document_is_read_once(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    indexes = Indexes(root)
    first = Rig(indexes, FakeReader(), Ingest(indexes, FakeReader(), big_cap), root)  # type: ignore[arg-type]
    done = await put(first.index(), "doc00004", "Already searchable.", name="d.md", ready=False)
    assert done.status == "searchable", "found by words, and not yet finished"
    await first.add("doc00001", f"The {RARE} word.", name="a.md")
    await first.add("doc00002", "Another.", name="b.md")
    await first.add("doc00003", "A third.", name="c.md")
    cut = await first.index().claim()  # the stop came while this one was read
    assert cut is not None and cut.status == "reading"
    await indexes.aclose()
    # Boot, on the same files.
    again = Indexes(root)
    reader = FakeReader()
    ingest = Ingest(again, reader, big_cap)  # type: ignore[arg-type]

    async def owners(ids: set[str]) -> dict[str, str]:
        return {}

    try:
        reconciled = await ingest.resume(owners)
        ingest.start()
        rig = Rig(again, reader, ingest, root)
        await rig.done(ADA, "doc00001", "doc00002", "doc00003")
        assert reconciled.requeued == 1 and reconciled.finished == 1 and reconciled.people == 1
        assert [await rig.status(i) for i in ("doc00001", "doc00002", "doc00003", "doc00004")] == [
            "ready"
        ] * 4
        assert sorted(reader.order) == sorted(
            key_of(i) for i in ("doc00001", "doc00002", "doc00003")
        )
        assert len(await rig.index().search(f'"{RARE}"', [LIBRARY], 5)) == 1
    finally:
        await ingest.aclose()
        await again.aclose()


# --------------------------------------------------------------------------- #
# the boot reconcile
# --------------------------------------------------------------------------- #


async def boot(tmp_path: Path, chats: dict[str, str] | None = None) -> tuple[Rig, Any]:
    """Boot over `tmp_path`'s data: the chats that exist, by id and owner."""
    root = tmp_path / "data"
    indexes, reader = Indexes(root), FakeReader()
    ingest = Ingest(indexes, reader, big_cap)  # type: ignore[arg-type]

    async def owners(ids: set[str]) -> dict[str, str]:
        return {i: o for i, o in (chats or {}).items() if i in ids}

    reconciled = await ingest.resume(owners)
    ingest.start()
    return Rig(indexes, reader, ingest, root), reconciled


async def close(rig: Rig) -> None:
    await rig.ingest.aclose()
    await rig.indexes.aclose()


async def test_the_documents_of_a_chat_that_is_gone_are_removed_at_boot_and_the_rest_kept(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    seed = Indexes(root)
    index = seed.get(ADA)
    assert index is not None
    live, dead = chat_scope("live-chat"), chat_scope("dead-chat")
    await put(
        index, "doc00001", f"The {RARE} word.", scope=live, source="attachment:f1", name="a.md"
    )
    await put(
        index, "doc00002", f"The {RARE} too.", scope=dead, source="attachment:f2", name="b.md"
    )
    await put(index, "doc00003", f"The {RARE} saved.", name="c.md")
    await seed.aclose()
    rig, reconciled = await boot(tmp_path, {"live-chat": ADA})
    try:
        assert reconciled.chat_documents == 1
        assert [d.id for d in await rig.index().documents(dead)] == []
        assert {d.id for d in await rig.index().documents()} == {"doc00001", "doc00003"}
        assert not rig.index().original_path("doc00002").exists()
        assert not rig.index().text_path("doc00002").exists()
        assert await rig.index().search(f'"{RARE}"', [dead], 5) == []
    finally:
        await close(rig)


async def test_a_chat_document_whose_chat_belongs_to_someone_else_is_removed_too(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    seed = Indexes(root)
    index = seed.get(ADA)
    assert index is not None
    await put(index, "doc00001", f"The {RARE} word.", scope=chat_scope("c1"), source="a:1")
    await seed.aclose()
    rig, reconciled = await boot(tmp_path, {"c1": BO})
    try:
        assert reconciled.chat_documents == 1 and await rig.index().documents() == []
    finally:
        await close(rig)


async def test_files_no_row_claims_are_removed_at_boot_and_the_others_are_not(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    seed = Indexes(root)
    index = seed.get(ADA)
    assert index is not None
    await put(index, "doc00001", "Some words.")
    (index.originals / "gone0001").write_text("an original no row claims", encoding="utf-8")
    (index.originals / "doc00001.part").write_text("half an upload", encoding="utf-8")
    (index.dir / "gone0001.txt").write_text("a text no row claims", encoding="utf-8")
    (index.dir / "doc00001.txt.part").write_text("half a text", encoding="utf-8")
    await seed.aclose()
    rig, reconciled = await boot(tmp_path)
    try:
        assert reconciled.orphan_files == 4
        left = sorted(p.name for p in rig.index().dir.rglob("*") if p.is_file())
        assert [n for n in left if not n.startswith("index.sqlite3")] == [
            "doc00001",
            "doc00001.txt",
        ]
    finally:
        await close(rig)


async def test_a_document_that_can_be_found_but_has_lost_its_text_is_read_again_at_boot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    seed = Indexes(root)
    index = seed.get(ADA)
    assert index is not None
    await put(index, "doc00001", f"The {RARE} word.")
    index.text_path("doc00001").unlink()
    await seed.aclose()
    rig, reconciled = await boot(tmp_path)
    try:
        assert reconciled.healed == 1
        await rig.done(ADA, "doc00001")
        assert await rig.status("doc00001") == "ready"
        assert rig.index().text_path("doc00001").is_file()
        assert len(await rig.index().search(f'"{RARE}"', [LIBRARY], 5)) == 1
    finally:
        await close(rig)


async def test_an_index_that_cannot_be_read_is_left_exactly_as_it_is_and_nothing_is_deleted(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    seed = Indexes(root)
    index = seed.get(ADA)
    assert index is not None
    await put(index, "doc00001", "Some words.")
    keep = index.original_path("doc00001")
    await seed.aclose()
    index.path.write_bytes(b"not a database " * 300)
    (index.originals / "gone0001").write_text(
        "would be an orphan if the rows were readable", encoding="utf-8"
    )
    before = {p: p.read_bytes() for p in index.dir.rglob("*") if p.is_file()}
    rig, reconciled = await boot(tmp_path)
    try:
        assert reconciled.unreadable == 1 and reconciled.people == 0
        after = {p: p.read_bytes() for p in index.dir.rglob("*") if p.is_file()}
        assert after == before and keep.exists()
    finally:
        await close(rig)


async def test_a_directory_that_is_not_its_owners_is_not_touched(tmp_path: Path) -> None:
    root = tmp_path / "data"
    seed = Indexes(root)
    index = seed.get(ADA)
    assert index is not None
    await put(index, "doc00001", "Some words.", scope=chat_scope("c1"), source="a:1")
    await seed.aclose()
    elsewhere = root / "knowledge" / ("0" * 32)
    shutil.copytree(index.dir, elsewhere)
    rig, reconciled = await boot(tmp_path, {})
    try:
        assert reconciled.unreadable == 1, "named, and left alone"
        assert (elsewhere / "originals" / "doc00001").exists()
        assert reconciled.chat_documents == 1, "Ada's own was reconciled"
    finally:
        await close(rig)


async def test_an_unfinished_document_is_not_left_saying_reading_after_boot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    seed = Indexes(root)
    index = seed.get(ADA)
    assert index is not None
    await index.add(new("doc00001"))
    index.originals.mkdir(parents=True, exist_ok=True)
    index.original_path("doc00001").write_text("Words.", encoding="utf-8")
    assert (await index.claim()) is not None
    await seed.aclose()
    rig, _ = await boot(tmp_path)
    try:
        await rig.done(ADA, "doc00001")
        assert await rig.status("doc00001") == "ready"
    finally:
        await close(rig)


async def test_the_retriever_finds_what_the_worker_indexed_end_to_end(rig: Rig) -> None:
    await rig.add("doc00001", "# Plan\n\nThe second GPU cost is a lot of money.\n", name="plan.md")
    await rig.done(ADA, "doc00001")
    found = await Retriever().retrieve(
        rig.index(), plan_query("What is the GPU cost?"), [LIBRARY],
        question="What is the GPU cost?", budget=None, overhead=0,
    )  # fmt: skip
    assert [p.heading for p in found.passages] == ["Plan"]
    _ = field  # keep the import honest
