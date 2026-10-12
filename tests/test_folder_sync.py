"""The parts of the folder service that need no folder (MR7, `knowledge/folder_sync.py` and
`folder_docs_api.py`): the words a file that is not read is told, what a folder says of itself in each
state, and the few races a real Workbench is put through on purpose (a rule that changes while the loop
is deciding, a Sync now while a check is reading, a link that appears between a listing and a read).
"""

from __future__ import annotations

import errno
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import files, folder_io
from eugene_plexus_workbench.folder_docs_api import (
    count_words,
    describe_when,
    folder_state,
    headline,
)
from eugene_plexus_workbench.knowledge import folder_sync as fs
from eugene_plexus_workbench.knowledge.index import STATES, FolderCounts
from eugene_plexus_workbench.store import FolderRecord

from .conftest import World
from .folderdocs import (
    add_and_settle,
    add_folder,
    by_path,
    folder_of,
    grant,
    owner_and_ada,
    settle_folder,
    sync_now,
    write,
)
from .saved import RARE, md

pytestmark = pytest.mark.usefixtures("quiet_loop")
OLD = 3600.0
READS = "Workbench reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files."


def age(folder: Path) -> None:
    then = time.time() - OLD
    for found in folder.rglob("*"):
        if found.is_file():
            os.utime(found, (then, then))


# --------------------------------------------------------------------------- #
# the words a file that is not read is told
# --------------------------------------------------------------------------- #


def test_what_workbench_does_not_read_is_said_as_the_first_sentence_and_what_it_does() -> None:
    said = files.Unsupported(f"This file is a GIF image. {files.READS}").message
    assert fs.unsupported_words(said) == f"Skipped: this file is a GIF image. {READS}"
    binary = f"This file looks like a binary file, not text (.exe). {files.READS}"
    assert fs.unsupported_words(binary) == (
        f"Skipped: this file looks like a binary file, not text (.exe). {READS}"
    )
    assert (
        fs.not_a_document_words("image")
        == f"Skipped: this file is a picture, not a document. {READS}"
    )
    assert fs.not_a_document_words("audio") == (
        f"Skipped: this file is a sound recording, not a document. {READS}"
    )
    assert fs.not_word_words("a.xlsx").startswith(
        "Skipped: this file is a ZIP archive (.xlsx), not a Word (.docx) file."
    )
    assert fs.not_word_words("a.xlsx").endswith(READS)


def test_a_file_too_large_or_over_the_folders_size_says_the_numbers() -> None:
    assert fs.too_large_words(312 * 1024 * 1024, 50 * 1024 * 1024) == (
        "Skipped: this file is 312 MiB; the most Workbench reads of one file is 50 MiB."
    )
    assert fs.over_folder_words(2048) == (
        "Skipped: this folder's files together would pass the 2,048 MB Workbench keeps from one folder."
    )


def test_a_busy_file_is_told_from_a_denied_one_and_a_link_from_a_missing_file() -> None:
    class Sharing(PermissionError):
        winerror = 32

    class Locked(PermissionError):
        winerror = 33

    assert fs.is_busy(BlockingIOError(errno.EAGAIN, "locked")) and fs.is_busy(Sharing(13, "x"))
    assert fs.is_busy(Locked(13, "x"))
    assert not fs.is_busy(PermissionError(13, "denied")) and not fs.is_busy(None)
    assert not fs.is_busy(FileNotFoundError(2, "gone"))
    assert fs.is_link(OSError(getattr(errno, "ELOOP", 40), "too many levels"))
    assert not fs.is_link(FileNotFoundError(errno.ENOENT, "gone")) and not fs.is_link(None)


def test_plurals_and_how_long_ago_are_said_as_a_person_says_them() -> None:
    assert fs.plural(1, "document") == "1 document" and fs.plural(2, "document") == "2 documents"
    assert fs.plural(1_204, "file") == "1,204 files" and fs.plural(0, "file") == "0 files"
    assert fs.plural(1, "box", "boxes") == "1 box" and fs.plural(2, "box", "boxes") == "2 boxes"
    for seconds, said in (
        (3, "just now"),
        (50, "a minute ago"),
        (600, "10 minutes ago"),
        (3600 * 5, "5 hours ago"),
        (86400 * 3, "3 days ago"),
    ):
        assert describe_when(seconds) == said, seconds


# --------------------------------------------------------------------------- #
# what a folder says of itself
# --------------------------------------------------------------------------- #


def record(**kw: Any) -> FolderRecord:
    return FolderRecord("p-ada", "g1", "Work notes", 1.0, **kw)


def counts(**by: int) -> FolderCounts:
    states = dict.fromkeys(STATES, 0) | by
    return FolderCounts(sum(states.values()), 0, 0, 0, 0, 0, states, {})


def test_a_folders_state_comes_from_its_record_what_is_running_and_what_is_waiting() -> None:
    run = fs.Run(1.0)
    assert folder_state(record(status="withdrawn"), None, False) == "withdrawn"
    assert folder_state(record(status="withdrawn"), run, True) == "withdrawn"
    for code in ("gone", "replaced"):
        assert (
            folder_state(record(problem={"code": code, "message": "m"}), run, False)
            == "unavailable"
        )
    assert folder_state(record(checked_at=1.0), run, False) == "syncing"
    assert folder_state(record(checked_at=1.0), None, True) == "waiting"
    assert folder_state(record(), None, False) == "waiting", "never checked"
    assert (
        folder_state(record(checked_at=1.0, problem={"code": "x", "message": "m"}), None, False)
        == "problem"
    )
    assert folder_state(record(checked_at=1.0), None, False) == "synced"


def test_what_a_folder_holds_is_said_in_a_line() -> None:
    assert count_words(counts()) == "0 files"
    assert count_words(counts(ready=380, reading=12, queued=3, skipped=9)) == (
        "404 files: 380 ready, 15 being read, 9 skipped"
    )
    assert count_words(counts(ready=1, failed=1)) == "2 files: 1 ready, 1 failed"
    assert count_words(counts(searchable=2, embedding=1)) == "3 files: 3 being read"


def test_the_headline_is_one_sentence_for_each_state_made_of_what_is_true() -> None:
    now = 1_000_000.0
    held = counts(ready=3)
    lost = record(problem={"code": "gone", "message": "It is not there."}, checked_at=now)
    assert headline(lost, "unavailable", None, held, 10, now) == "It is not there."
    away = record(status="withdrawn", problem={"code": "withdrawn", "message": "Taken away."})
    assert headline(away, "withdrawn", None, held, 10, now) == "Taken away."
    looking = fs.Run(now)
    assert headline(record(), "syncing", looking, held, 10, now) == "Looking at this folder."
    reading = fs.Run(now, phase="reading", done=4, total=90)
    assert (
        headline(record(), "syncing", reading, held, 10, now)
        == "Reading this folder: 4 of 90 files."
    )
    assert headline(record(), "waiting", None, held, 10, now) == (
        "Looking at this folder soon, for the first time."
    )
    assert headline(record(checked_at=now), "waiting", None, held, 10, now) == (
        "This folder will be checked in a moment."
    )
    broken = record(
        problem={"code": "x", "message": "Could not look."}, synced_at=now - 7200, checked_at=now
    )
    assert headline(broken, "problem", None, held, 10, now) == (
        "Could not look. It was last checked well 2 hours ago."
    )
    never = record(problem={"code": "x", "message": "Could not look."}, checked_at=now)
    assert headline(never, "problem", None, held, 10, now) == "Could not look."
    done = record(synced_at=now - 180, checked_at=now - 180)
    assert headline(done, "synced", None, held, 10, now) == (
        "Checked 3 minutes ago, and again every 10 minutes. 3 files: 3 ready."
    )
    assert headline(done, "synced", None, held, 1, now).startswith(
        "Checked 3 minutes ago, and again every 1 minute."
    )


# --------------------------------------------------------------------------- #
# races a real Workbench is put through on purpose
# --------------------------------------------------------------------------- #


def test_a_rule_the_person_changes_while_the_loop_is_deciding_is_the_rule_the_check_uses(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "keep.md", md("Fine."))
    write(notes, ".hidden.md", md(f"The {RARE} hidden words."))
    age(notes)
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    real = fs.FolderSync._run

    async def run_after_the_person_changed_it(
        self: fs.FolderSync, found: FolderRecord, want: fs.Want
    ) -> None:
        # The loop looked at the record, and then the person turned hidden files on.
        await self.store.update_folder_record(found.owner, found.grant_id, include_hidden=True)
        await real(self, found, want)

    monkeypatch.setattr(fs.FolderSync, "_run", run_after_the_person_changed_it)
    assert add_folder(ada, gid).status_code == 201
    done = settle_folder(ada, gid)
    assert set(by_path(ada, gid)) == {"keep.md", ".hidden.md"}, (
        "the check used the rules as they are"
    )
    assert done["includeHidden"] is True


def test_a_check_that_begins_after_the_folder_was_withdrawn_reads_and_adds_nothing(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    real = fs.FolderSync._run
    ran = threading.Event()

    async def run_after_it_was_withdrawn(
        self: fs.FolderSync, found: FolderRecord, want: fs.Want
    ) -> None:
        # The loop looked at the record, and then the folder was withdrawn.
        await self.store.update_folder_record(found.owner, found.grant_id, status="withdrawn")
        await real(self, found, want)
        ran.set()

    monkeypatch.setattr(fs.FolderSync, "_run", run_after_it_was_withdrawn)
    assert add_folder(ada, gid).status_code == 201
    assert ran.wait(30)
    assert by_path(ada, gid) == {}, "nothing was read for a folder that is not the person's now"


def test_a_folder_is_granted_only_while_its_grant_is_there_and_names_its_person() -> None:
    grants = {"g1": {"id": "g1", "subject": "p-ada"}}
    assert fs._granted(grants, record()) is True
    assert fs._granted(grants, FolderRecord("p-bo", "g1", "Work notes", 1.0)) is False
    assert fs._granted({}, record()) is False


def test_a_sync_now_while_a_check_is_reading_is_followed_by_another_check(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(4):
        write(notes, f"f{n}.md", md(f"File {n}."))
    age(notes)
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    reading, release = threading.Event(), threading.Event()
    real = folder_io.read_file
    calls: list[int] = []

    def slow(*args: Any) -> Any:
        calls.append(1)
        if len(calls) == 2:
            reading.set()
            assert release.wait(30)
        return real(*args)

    monkeypatch.setattr(folder_io, "read_file", slow)
    assert add_folder(ada, gid).status_code == 201
    assert reading.wait(30)
    write(notes, "late.md", md("Arrived while it was reading."))
    age(notes)
    assert sync_now(ada, gid).status_code == 202, "asked while the first check is still under way"
    release.set()
    deadline = time.perf_counter() + 60
    seen = folder_of(ada, gid)
    while time.perf_counter() < deadline and "late.md" not in by_path(ada, gid):
        time.sleep(0.05)
        seen = folder_of(ada, gid)
    assert "late.md" in by_path(ada, gid), "a second check followed the first"
    done = settle_folder(ada, gid)
    assert done["state"] == "synced" and seen["problem"] is None


@pytest.mark.parametrize(
    ("error", "code", "words"),
    [
        (folder_io.FolderError("x", code="link"), "link", fs.LINKED),
        (folder_io.FolderError("x", code="mount"), "mount", fs.MOUNTED),
        (folder_io.FolderError("x", code="special"), "special", fs.SPECIAL),
        (folder_io.FolderError("x", code="hard_links"), "hard_links", fs.HARD_LINKS),
        (
            OSError(getattr(errno, "ELOOP", 40), "Too many levels of symbolic links"),
            "link",
            fs.LINKED,
        ),
    ],
)
def test_a_link_that_appears_between_the_listing_and_the_read_is_skipped_in_its_words(
    world: World,
    notes: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    code: str,
    words: str,
) -> None:
    write(notes, "swapped.md", md("Words."))
    write(notes, "fine.md", md("Fine."))
    age(notes)
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    real = folder_io.read_file

    def swapped(*args: Any) -> Any:
        if args[3] == ["swapped.md"]:
            raise error
        return real(*args)

    monkeypatch.setattr(folder_io, "read_file", swapped)
    add_and_settle(ada, gid)
    got = by_path(ada, gid)
    assert got["swapped.md"]["state"] == "skipped" and got["swapped.md"]["code"] == code
    assert got["swapped.md"]["message"] == words and got["fine.md"]["state"] == "ready"


def test_a_failure_in_workbench_itself_is_said_as_that_and_is_not_a_crash(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "a.md", md("Words."))
    age(notes)
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)

    def broken(*args: Any) -> Any:
        raise RuntimeError("a bug")

    monkeypatch.setattr(folder_io, "scan", broken)
    assert add_folder(ada, gid).status_code == 201
    shown = settle_folder(ada, gid, ends=("problem",))
    assert shown["problem"]["code"] == "internal"
    assert shown["problem"]["message"] == (
        "Workbench's check of this folder failed (RuntimeError). See Workbench's log."
    )
    assert ada.get("/api/documents/folders").status_code == 200, "and Workbench keeps answering"
