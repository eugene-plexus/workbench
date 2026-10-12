"""Reading a whole folder through held handles (MR7, `folder_io.scan` and `read_file`): real files on a
real disk. What these check is what the handles guarantee, not what the walk decides: a link is never
entered or read, a file is copied whole and hashed, a file that grows or changes while it is read is not
trusted, parents are held where the OS can hold them, other programs are never blocked from saving, and
nothing is ever written in the folder.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import folder_io
from eugene_plexus_workbench.folder_io import DIRECTORY, FILE, LINK, Declined, FolderError, Taken
from eugene_plexus_workbench.folders import Folders, GrantGone
from eugene_plexus_workbench.settings import Settings
from eugene_plexus_workbench.store import Person, Store

from .folderdocs import junction, snapshot, unlink_directory

WINDOWS = sys.platform == "win32"


@pytest.fixture
def grant(tmp_path: Path) -> Path:
    folder = tmp_path / "grant"
    (folder / "sub" / "deeper").mkdir(parents=True)
    (folder / "a.md").write_bytes(b"# A\n\nhello\n")
    (folder / "sub" / "b.txt").write_bytes(b"bee")
    return folder


def identity_of(folder: Path) -> str:
    return folder_io.inspect(str(folder), [])


def meanwhile(action: Callable[[], None]) -> Callable[[bytes, int], None]:
    """A head check that lets another program do `action` while the file is open, and waits for it.
    The other program is a thread of its own, started here and not inside the read: a thread that
    the read started would be in its sandbox too (on Linux the read runs under a Landlock domain that
    lets it read and nothing else, and a thread made from it keeps that). It is what a person's
    editor does to the file while Workbench is copying it."""
    go, finished = threading.Event(), threading.Event()

    def run() -> None:
        go.wait(10)
        try:
            action()
        finally:
            finished.set()

    threading.Thread(target=run, daemon=True).start()

    def check(head: bytes, size: int) -> None:
        go.set()
        assert finished.wait(10), "the other program did not finish"

    return check


def listing(folder: Path, names: list[str] | None = None) -> dict[str, folder_io.Entry]:
    entries = folder_io.scan(
        str(folder), identity_of(folder), [], lambda tree: tree.entries(names or [], 1000)
    )
    return {e.name: e for e in entries}


def take(
    folder: Path,
    names: list[str],
    tmp: Path,
    *,
    limit: int = 1 << 20,
    check: Any = None,
    expected: str | None = None,
) -> tuple[Taken | Declined, bytes]:
    dest = tmp / "copy.part"
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0))
    try:
        got = folder_io.read_file(
            str(folder),
            expected or identity_of(folder),
            [],
            names,
            fd,
            limit,
            8192,
            check or (lambda head, size: None),
        )
    finally:
        os.close(fd)
    return got, dest.read_bytes()


# --------------------------------------------------------------------------- #
# listing
# --------------------------------------------------------------------------- #


def test_a_listing_says_what_each_entry_is_and_its_size_and_time(grant: Path) -> None:
    found = listing(grant)
    assert set(found) == {"a.md", "sub"}
    assert found["a.md"].kind == FILE and found["a.md"].size == len(b"# A\n\nhello\n")
    assert found["a.md"].mtime_ns == (grant / "a.md").stat().st_mtime_ns
    assert found["sub"].kind == DIRECTORY and found["sub"].size == 0
    assert set(listing(grant, ["sub"])) == {"b.txt", "deeper"}


def test_a_junction_or_symlink_is_listed_as_a_link_and_never_entered(
    grant: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("private", encoding="utf-8")
    junction(grant / "jlink", outside)
    try:
        assert listing(grant)["jlink"].kind == LINK
        with pytest.raises((FolderError, OSError)) as raised:
            listing(grant, ["jlink"])
        assert getattr(raised.value, "code", "link") in ("link", None)
        with pytest.raises((FolderError, OSError)):
            take(grant, ["jlink", "secret.md"], tmp_path)
    finally:
        unlink_directory(grant / "jlink")
    assert (outside / "secret.md").read_text(encoding="utf-8") == "private"


@pytest.mark.skipif(WINDOWS, reason="a file symlink needs a privilege Windows withholds by default")
def test_a_file_symlink_is_a_link_and_is_not_read(grant: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret.md"
    outside.write_text("private", encoding="utf-8")
    (grant / "flink.md").symlink_to(outside)
    assert listing(grant)["flink.md"].kind == LINK
    with pytest.raises((FolderError, OSError)):
        take(grant, ["flink.md"], tmp_path)


@pytest.mark.skipif(WINDOWS, reason="pipes and sockets are POSIX")
def test_a_pipe_is_not_an_ordinary_file(grant: Path, tmp_path: Path) -> None:
    os.mkfifo(grant / "pipe")
    assert listing(grant)["pipe"].kind == folder_io.OTHER
    with pytest.raises(FolderError) as raised:
        take(grant, ["pipe"], tmp_path)
    assert raised.value.code == "special"


def test_a_listing_is_bounded_by_the_limit_it_is_given(grant: Path) -> None:
    for i in range(30):
        (grant / f"f{i}.md").write_bytes(b"x")
    entries = folder_io.scan(str(grant), identity_of(grant), [], lambda t: t.entries([], 7))
    assert len(entries) == 7


# --------------------------------------------------------------------------- #
# copying a file out
# --------------------------------------------------------------------------- #


def test_a_file_is_copied_whole_and_hashed_with_its_first_bytes_and_modified_time(
    grant: Path, tmp_path: Path
) -> None:
    got, copied = take(grant, ["a.md"], tmp_path)
    data = (grant / "a.md").read_bytes()
    assert isinstance(got, Taken) and copied == data
    assert got.size == len(data) and got.sha256 == hashlib.sha256(data).hexdigest()
    assert got.head == data and got.mtime_ns == (grant / "a.md").stat().st_mtime_ns


def test_a_file_below_a_folder_is_read_by_its_names(grant: Path, tmp_path: Path) -> None:
    got, copied = take(grant, ["sub", "b.txt"], tmp_path)
    assert isinstance(got, Taken) and copied == b"bee"


def test_a_file_larger_than_a_block_is_copied_in_blocks_and_hashed_across_them(
    grant: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(folder_io, "COPY_BLOCK", 1000)
    data = bytes(range(256)) * 20  # 5,120 bytes: more than the 1,000 a block is, less than the head
    (grant / "big.bin").write_bytes(data * 3)
    got, copied = take(grant, ["big.bin"], tmp_path)
    assert isinstance(got, Taken) and copied == data * 3
    assert got.sha256 == hashlib.sha256(data * 3).hexdigest() and got.size == len(data) * 3
    assert got.head == (data * 3)[:8192] and len(got.head) == 8192


def test_the_first_bytes_are_shown_before_the_rest_is_read_and_a_decline_copies_nothing(
    grant: Path, tmp_path: Path
) -> None:
    seen: list[tuple[bytes, int]] = []

    def refuse(head: bytes, size: int) -> Declined:
        seen.append((head, size))
        return Declined("unsupported", "no")

    got, copied = take(grant, ["a.md"], tmp_path, check=refuse)
    assert got == Declined("unsupported", "no") and copied == b""
    assert seen == [(b"# A\n\nhello\n", 11)]


def test_a_file_over_the_limit_is_refused_by_its_size_before_it_is_read(
    grant: Path, tmp_path: Path
) -> None:
    called: list[int] = []
    with pytest.raises(folder_io.TooLarge) as raised:
        take(grant, ["a.md"], tmp_path, limit=5, check=lambda h, s: called.append(s))
    assert raised.value.size == 11 and called == [] and raised.value.code == "too_large"


def test_a_file_that_grows_past_the_limit_while_it_is_read_is_refused_too(
    grant: Path, tmp_path: Path
) -> None:
    def grow() -> None:
        with open(grant / "a.md", "ab") as handle:
            handle.write(b"x" * 50_000)

    with pytest.raises(folder_io.TooLarge):
        take(grant, ["a.md"], tmp_path, limit=10_000, check=meanwhile(grow))


def test_a_file_that_changes_while_it_is_read_is_not_trusted(grant: Path, tmp_path: Path) -> None:
    def edit() -> None:
        with open(grant / "a.md", "ab") as handle:
            handle.write(b"more")

    with pytest.raises(folder_io.ChangedWhileReading) as raised:
        take(grant, ["a.md"], tmp_path, check=meanwhile(edit))
    assert raised.value.code == "changed"


def test_a_file_whose_time_alone_moves_while_it_is_read_is_not_trusted_either(
    grant: Path, tmp_path: Path
) -> None:
    stat = (grant / "a.md").stat()

    def touch() -> None:
        os.utime(grant / "a.md", ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    with pytest.raises(folder_io.ChangedWhileReading):
        take(grant, ["a.md"], tmp_path, check=meanwhile(touch))


def test_a_hard_link_is_refused_by_the_handle_and_the_target_is_not_read(
    grant: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("private", encoding="utf-8")
    os.link(outside, grant / "linked.txt")
    with pytest.raises(FolderError) as raised:
        take(grant, ["linked.txt"], tmp_path)
    assert raised.value.code == "hard_links"
    assert (tmp_path / "copy.part").read_bytes() == b""


def test_a_name_that_is_a_folder_is_not_read_as_a_file(grant: Path, tmp_path: Path) -> None:
    with pytest.raises((FolderError, OSError)):
        take(grant, ["sub"], tmp_path)


def test_a_file_that_is_not_there_says_so_as_the_system_does(grant: Path, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        take(grant, ["nothing.md"], tmp_path)


# --------------------------------------------------------------------------- #
# containment
# --------------------------------------------------------------------------- #


def test_a_folder_swapped_for_a_link_after_it_was_listed_is_refused_when_it_is_opened(
    grant: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "b.txt").write_text("PRIVATE", encoding="utf-8")
    identity = identity_of(grant)
    assert "sub" in listing(grant)  # as the walk saw it
    (grant / "sub" / "deeper").rmdir()
    (grant / "sub" / "b.txt").unlink()
    (grant / "sub").rmdir()
    junction(grant / "sub", outside)
    try:
        with pytest.raises((FolderError, OSError)) as raised:
            take(grant, ["sub", "b.txt"], tmp_path, expected=identity)
        assert getattr(raised.value, "code", None) in ("link", None)
    finally:
        unlink_directory(grant / "sub")
    assert (tmp_path / "copy.part").read_bytes() == b""
    assert (outside / "b.txt").read_text(encoding="utf-8") == "PRIVATE"


@pytest.mark.skipif(not WINDOWS, reason="Linux holds the path with a Landlock domain instead")
def test_a_parent_cannot_be_renamed_while_a_file_below_it_is_read(
    grant: Path, tmp_path: Path
) -> None:
    refused: list[BaseException] = []

    def try_rename() -> None:
        try:
            (grant / "sub").rename(grant / "moved")
        except OSError as exc:
            refused.append(exc)

    got, copied = take(grant, ["sub", "b.txt"], tmp_path, check=meanwhile(try_rename))
    assert isinstance(got, Taken) and copied == b"bee"
    assert len(refused) == 1 and isinstance(refused[0], PermissionError)
    assert (grant / "sub").is_dir() and not (grant / "moved").exists()


def test_a_replaced_folder_is_not_read_and_says_it_was_replaced(
    grant: Path, tmp_path: Path
) -> None:
    identity = identity_of(grant)
    grant.rename(tmp_path / "original")
    grant.mkdir()
    (grant / "a.md").write_text("replacement", encoding="utf-8")
    with pytest.raises(FolderError) as raised:
        take(grant, ["a.md"], tmp_path, expected=identity)
    assert raised.value.code == "replaced"
    with pytest.raises(FolderError) as seen:
        folder_io.scan(str(grant), identity, [], lambda tree: tree.entries([], 10))
    assert seen.value.code == "replaced"


def test_a_folder_that_holds_workbenchs_own_files_cannot_be_read(
    grant: Path, tmp_path: Path
) -> None:
    with pytest.raises(FolderError, match="private files"):
        folder_io.scan(
            str(grant), identity_of(grant), [tmp_path], lambda tree: tree.entries([], 10)
        )


def test_a_small_file_is_read_to_its_limit_and_no_further(grant: Path) -> None:
    (grant / "rules").write_bytes(b"x" * 100)
    read = lambda limit: folder_io.scan(  # noqa: E731
        str(grant), identity_of(grant), [], lambda tree: tree.read_small(["rules"], limit)
    )
    assert read(100) == b"x" * 100
    with pytest.raises(folder_io.TooLarge):
        read(99)


# --------------------------------------------------------------------------- #
# never in the way, never writing
# --------------------------------------------------------------------------- #


def test_another_program_can_write_rename_and_delete_a_file_while_it_is_read(
    grant: Path, tmp_path: Path
) -> None:
    done: list[str] = []

    def other_program() -> None:
        with open(grant / "a.md", "r+b") as handle:  # another program saves into it
            handle.seek(0, os.SEEK_END)
            handle.write(b"")
        done.append("written")
        os.replace(grant / "a.md", grant / "renamed.md")  # and renames it
        done.append("renamed")
        os.remove(grant / "renamed.md")  # and deletes it
        done.append("deleted")

    # The read is of the file as it was opened: it ends in a refusal to trust it, or in the bytes it
    # had, but it never stops the other program from saving.
    with contextlib.suppress(folder_io.ChangedWhileReading, OSError, FolderError):
        take(grant, ["a.md"], tmp_path, check=meanwhile(other_program))
    assert done == ["written", "renamed", "deleted"]
    assert not (grant / "a.md").exists()


@pytest.mark.skipif(WINDOWS, reason="advisory file locks are POSIX")
def test_a_file_another_program_has_locked_is_still_read_because_a_folder_read_takes_no_lock(
    grant: Path, tmp_path: Path
) -> None:
    import fcntl

    with open(grant / "a.md", "rb") as editor:
        fcntl.flock(editor, fcntl.LOCK_EX)  # an editor that wants the file to itself
        got, copied = take(grant, ["a.md"], tmp_path)
    assert isinstance(got, Taken) and copied == b"# A\n\nhello\n"


def test_reading_a_whole_folder_changes_nothing_in_it(grant: Path, tmp_path: Path) -> None:
    (grant / "sub" / "c.md").write_bytes(b"# C\n")
    before = snapshot(grant)
    identity = identity_of(grant)

    def walk(tree: Any) -> list[str]:
        found: list[str] = []
        queue: list[list[str]] = [[]]
        while queue:
            names = queue.pop()
            for entry in tree.entries(names, 100):
                if entry.kind == DIRECTORY:
                    queue.append([*names, entry.name])
                else:
                    found.append("/".join([*names, entry.name]))
        return found

    names = sorted(folder_io.scan(str(grant), identity, [], walk))
    for name in names:
        take(grant, name.split("/"), tmp_path)
    assert snapshot(grant) == before, "no byte, no time and no name in the folder changed"


# --------------------------------------------------------------------------- #
# what a Windows directory entry says it is
# --------------------------------------------------------------------------- #

FILETIME = 116_444_736_000_000_000 + 10_000_000  # one second after the Unix epoch
ARCHIVE, HIDDEN, SYSTEM, REPARSE, DIR = 0x20, 0x2, 0x4, 0x400, 0x10


@pytest.mark.parametrize("flag", [HIDDEN, SYSTEM, HIDDEN | SYSTEM])
def test_the_windows_hidden_and_system_attributes_each_make_an_entry_hidden(flag: int) -> None:
    hidden = folder_io.entry_from_attributes("x.md", ARCHIVE | flag, 5, FILETIME, 0)
    assert hidden.hidden and hidden.kind == FILE and hidden.size == 5
    plain = folder_io.entry_from_attributes("x.md", ARCHIVE, 5, FILETIME, 0)
    assert not plain.hidden and plain.mtime_ns == 1_000_000_000


@pytest.mark.parametrize("flag", [0x1000, 0x40000, 0x400000])
def test_a_file_that_is_not_on_this_machine_yet_is_a_cloud_link_and_never_read(flag: int) -> None:
    kept_online = folder_io.entry_from_attributes("x.docx", ARCHIVE | flag, 99, FILETIME, 0)
    assert kept_online.kind == folder_io.LINK and kept_online.cloud and kept_online.size == 0


@pytest.mark.parametrize(
    ("tag", "cloud"),
    [
        (0x9000001A, True),
        (0x9000101A, True),
        (0x9000F01A, True),
        (0xA000000C, False),  # a symlink
        (0xA0000003, False),  # a junction
        (0x80000017, False),  # a compressed file
        (0, False),
    ],
)
def test_a_reparse_point_is_a_link_and_only_a_cloud_tag_makes_it_a_cloud_one(
    tag: int, cloud: bool
) -> None:
    entry = folder_io.entry_from_attributes("x", ARCHIVE | REPARSE, 0, FILETIME, tag)
    assert entry.kind == folder_io.LINK and entry.cloud is cloud
    folder = folder_io.entry_from_attributes("d", DIR | REPARSE, 0, FILETIME, tag)
    assert folder.kind == folder_io.LINK, "a folder that is a reparse point is never entered"


def test_a_file_of_a_folder_that_holds_workbenchs_own_files_cannot_be_read_either(
    grant: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "x.part"
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0))
    try:
        with pytest.raises(FolderError, match="private files"):
            folder_io.read_file(
                str(grant),
                identity_of(grant),
                [tmp_path],
                ["a.md"],
                fd,
                1000,
                100,
                lambda head, size: None,
            )
    finally:
        os.close(fd)
    assert dest.read_bytes() == b"", "nothing was copied"


# --------------------------------------------------------------------------- #
# through the grant, under the one lock
# --------------------------------------------------------------------------- #


@pytest.fixture
async def service(tmp_path: Path) -> Any:
    store = Store(tmp_path / "private" / "workbench.sqlite3")
    await store.open()
    ada = Person("p-ada", "Ada", "member")
    await store.upsert_person(ada)
    await store.upsert_person(Person("p-bo", "Bo", "member"))
    folder = tmp_path / "granted"
    folder.mkdir()
    (folder / "a.md").write_bytes(b"hello")
    folders = Folders(
        store,
        Settings(
            data_dir=tmp_path / "private",
            account_kind="windows_service" if WINDOWS else "systemd",
        ),
    )
    await folders.add(
        {"id": "grant1", "name": "files", "path": str(folder), "subject": "p-ada", "writable": 0}
    )
    try:
        yield folders, store, folder
    finally:
        await store.close()


async def test_a_grant_is_walked_and_read_for_its_own_person_only(
    service: Any, tmp_path: Path
) -> None:
    folders, _, _ = service
    names = await folders.scan(
        "grant1", "p-ada", lambda tree: [e.name for e in tree.entries([], 10)]
    )
    assert names == ["a.md"]
    with pytest.raises(GrantGone):
        await folders.scan("grant1", "p-bo", lambda tree: tree.entries([], 10))
    with pytest.raises(GrantGone):
        await folders.scan("nothing", "p-ada", lambda tree: tree.entries([], 10))
    fd = os.open(tmp_path / "x.part", os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0))
    try:
        got = await folders.read("grant1", "p-ada", ["a.md"], fd, 1000, 100, lambda h, s: None)
        assert isinstance(got, Taken) and got.size == 5
        with pytest.raises(GrantGone):
            await folders.read("grant1", "p-bo", ["a.md"], fd, 1000, 100, lambda h, s: None)
    finally:
        os.close(fd)


async def test_a_withdrawn_grant_is_never_walked_or_read_again(
    service: Any, tmp_path: Path
) -> None:
    folders, _, _ = service
    await folders.remove("grant1")
    with pytest.raises(GrantGone):
        await folders.scan("grant1", "p-ada", lambda tree: tree.entries([], 10))
    fd = os.open(tmp_path / "y.part", os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0))
    try:
        with pytest.raises(GrantGone):
            await folders.read("grant1", "p-ada", ["a.md"], fd, 1000, 100, lambda h, s: None)
    finally:
        os.close(fd)


async def test_a_walk_holds_the_grants_lock_so_a_withdrawal_waits_for_it(service: Any) -> None:
    folders, store, _ = service
    inside, release = threading.Event(), threading.Event()

    def slow(tree: Any) -> list[Any]:
        inside.set()
        assert release.wait(10)
        return list(tree.entries([], 10))

    walking = asyncio.create_task(folders.scan("grant1", "p-ada", slow))
    try:
        assert await asyncio.to_thread(inside.wait, 5)
        withdrawal = asyncio.create_task(folders.remove("grant1"))
        await asyncio.sleep(0.1)
        assert not withdrawal.done() and len(await store.folder_grants()) == 1
        release.set()
        assert [e.name for e in await walking] == ["a.md"]
        await withdrawal
        assert await store.folder_grants() == []
    finally:
        release.set()
        await asyncio.gather(walking, return_exceptions=True)


async def test_folders_cannot_be_walked_when_workbench_has_not_its_own_account(
    service: Any,
) -> None:
    folders, _, _ = service
    folders.settings.account_kind = None
    with pytest.raises(FolderError, match="own OS account"):
        await folders.scan("grant1", "p-ada", lambda tree: tree.entries([], 10))


async def test_an_error_of_the_system_keeps_its_cause_for_whoever_must_tell_gone_from_busy(
    service: Any, tmp_path: Path
) -> None:
    folders, _, folder = service
    (folder / "a.md").unlink()
    folder.rmdir()
    with pytest.raises(FolderError) as raised:
        await folders.scan("grant1", "p-ada", lambda tree: tree.entries([], 10))
    assert isinstance(raised.value.cause, FileNotFoundError | NotADirectoryError)
