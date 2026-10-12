"""Why a file of a folder is not read, the caps, and links (MR7), through a real Workbench.

Every file that gets past the ignore rules is a document: one that cannot be read is skipped (or
failed) with the reason in the words a person reads and a code the page can act on, and the folder
says what it left out because of a cap. A link is never followed out of the folder.
"""

from __future__ import annotations

import errno
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from eugene_plexus_workbench import folder_io

from . import documents as docs
from .conftest import ADMIN_TOKEN, PNG, World, wav
from .folderdocs import (
    add_and_settle,
    by_path,
    files_of,
    folder_of,
    grant,
    junction,
    owner_and_ada,
    settle_folder,
    snapshot,
    sync_and_settle,
    unlink_directory,
    write,
)
from .saved import RARE, SECOND, md

pytestmark = pytest.mark.usefixtures("quiet_loop")
OLD = 3600.0
MIB = 1024 * 1024


def age(folder: Path) -> None:
    then = time.time() - OLD
    for found in folder.rglob("*"):
        if found.is_file():
            os.utime(found, (then, then))


def install(world: World, **values: Any) -> None:
    changed = httpx.patch(
        f"{world.workbench}/v1/config",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json=values,
        trust_env=False,
    )
    assert changed.status_code == 200 and not changed.json()["rejected"], changed.text


def setup(world: World, folder: Path) -> tuple[Any, str]:
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    return ada, grant(world, owner, folder)


def big_text(megabytes: float, word: str = "") -> bytes:
    """Text of about this size, in paragraphs of a passage each, with a word in the first."""
    paragraph = "A line of ordinary words that make up a passage of a file. " * 8
    count = max(1, int(megabytes * MIB / (len(paragraph) + 2)))
    body = ("\n\n".join([f"{word} {paragraph}".strip()] + [paragraph] * (count - 1))).encode()
    return body


# --------------------------------------------------------------------------- #
# each file that cannot be read says why
# --------------------------------------------------------------------------- #


def test_every_file_that_cannot_be_read_is_a_document_with_its_reason_and_code(
    world: World, notes: Path
) -> None:
    write(notes, "good.md", md(f"The {RARE} file is fine."))
    write(notes, "good.docx", docs.docx())
    write(notes, "program.exe", b"MZ\x90\x00" + bytes(400))
    write(notes, "picture.png", PNG)
    write(notes, "sound.wav", wav(0.2))
    write(notes, "report.xlsx", docs.zip_without_word())
    write(notes, "scan.pdf", docs.pdf_pictures(2))
    write(notes, "locked.pdf", docs.pdf_encrypted())
    write(notes, "broken.pdf", docs.pdf_damaged())
    age(notes)
    ada, gid = setup(world, notes)
    done = add_and_settle(ada, gid)
    got = by_path(ada, gid)
    assert {p: d["state"] for p, d in got.items()} == {
        "good.md": "ready",
        "good.docx": "ready",
        "program.exe": "skipped",
        "picture.png": "skipped",
        "sound.wav": "skipped",
        "report.xlsx": "skipped",
        "scan.pdf": "skipped",
        "locked.pdf": "skipped",
        "broken.pdf": "failed",
    }
    reads = "Workbench reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files."
    assert got["program.exe"]["code"] == "unsupported"
    assert got["program.exe"]["message"] == (
        f"Skipped: this file looks like a binary file, not text (.exe). {reads}"
    )
    assert got["picture.png"]["code"] == "not_document"
    assert got["picture.png"]["message"] == (
        f"Skipped: this file is a picture, not a document. {reads}"
    )
    assert got["sound.wav"]["message"] == (
        f"Skipped: this file is a sound recording, not a document. {reads}"
    )
    assert got["report.xlsx"]["code"] == "bad_zip"
    assert got["report.xlsx"]["message"].startswith(
        "Skipped: this file is a ZIP archive (.xlsx), not a Word (.docx) file."
    )
    assert "only pictures of pages" in got["scan.pdf"]["message"]
    assert "password" in got["locked.pdf"]["message"]
    assert got["broken.pdf"]["message"].startswith("Failed:")
    assert all(d["passages"] == 0 for p, d in got.items() if d["state"] != "ready")
    assert done["counts"]["byState"]["skipped"] == 6 and done["counts"]["byState"]["failed"] == 1
    assert done["counts"]["byCode"]["unsupported"] == 1 and done["counts"]["byCode"]["bad_zip"] == 1
    assert done["last"]["skipped"] == 4, "four were skipped before they were read"


def test_the_files_of_a_folder_can_be_listed_by_state_by_reason_and_by_a_word_in_the_path(
    world: World, notes: Path
) -> None:
    write(notes, "docs/good.md", md("Fine."))
    write(notes, "docs/program.exe", b"MZ\x90\x00" + bytes(400))
    write(notes, "pics/a.png", PNG)
    write(notes, "pics/b.png", PNG)
    age(notes)
    ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    skipped = files_of(ada, gid, state="skipped")
    assert skipped["total"] == 3 and {f["path"] for f in skipped["files"]} == {
        "docs/program.exe",
        "pics/a.png",
        "pics/b.png",
    }
    assert files_of(ada, gid, code="not_document")["total"] == 2
    assert files_of(ada, gid, code="unsupported")["files"][0]["path"] == "docs/program.exe"
    assert files_of(ada, gid, q="PICS/")["total"] == 2, "a word in the path, without regard to case"
    assert files_of(ada, gid, state="ready")["total"] == 1
    page = files_of(ada, gid, offset=1, limit=2)
    assert page["total"] == 4 and len(page["files"]) == 2 and page["offset"] == 1
    assert page["counts"]["files"] == 4 and page["counts"]["byCode"] == {
        "unsupported": 1,
        "not_document": 2,
    }
    assert [f["path"] for f in files_of(ada, gid)["files"]] == [
        "docs/good.md",
        "docs/program.exe",
        "pics/a.png",
        "pics/b.png",
    ], "in path order"
    bad = ada.get(f"/api/documents/folders/{gid}/files", params={"state": "nonsense"})
    assert bad.status_code == 422 and "A state is one of" in bad.json()["detail"]["message"]
    assert ada.get(f"/api/documents/folders/{gid}/files", params={"limit": 501}).status_code == 422
    assert ada.get(f"/api/documents/folders/{gid}/files", params={"offset": -1}).status_code == 422


def test_a_file_that_was_skipped_for_what_it_is_is_not_looked_at_again_until_it_changes(
    world: World, notes: Path
) -> None:
    path = write(notes, "mystery.bin", b"MZ\x90\x00" + bytes(400))
    write(notes, "keep.md", md("Fine."))
    age(notes)
    ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    first = by_path(ada, gid)["mystery.bin"]
    assert first["state"] == "skipped" and first["code"] == "unsupported"
    again = sync_and_settle(ada, gid)
    assert by_path(ada, gid)["mystery.bin"]["id"] == first["id"], (
        "the same row: it was not looked at"
    )
    assert again["last"]["unchanged"] == 2 and again["last"]["skipped"] == 0
    full = sync_and_settle(ada, gid, deep=True)
    assert by_path(ada, gid)["mystery.bin"]["state"] == "skipped" and full["last"]["skipped"] == 1
    path.write_bytes(md("It is text now."))
    os.utime(path, (time.time() - 60, time.time() - 60))
    changed = sync_and_settle(ada, gid)
    assert by_path(ada, gid)["mystery.bin"]["state"] == "ready" and changed["last"]["added"] == 1


def test_a_file_too_large_says_its_size_and_the_limit_and_is_read_once_the_limit_is_raised(
    world: World, notes: Path
) -> None:
    write(notes, "huge.md", big_text(1.3, RARE))
    write(notes, "small.md", md("Small."))
    age(notes)
    ada, gid = setup(world, notes)
    install(world, documentsMaxFileMB=1)
    done = add_and_settle(ada, gid)
    huge = by_path(ada, gid)["huge.md"]
    assert huge["state"] == "skipped" and huge["code"] == "too_large"
    assert huge["message"] == (
        "Skipped: this file is 1.3 MiB; the most Workbench reads of one file is 1 MiB."
    )
    assert done["caps"]["fileBytes"]["limit"] == MIB
    install(world, documentsMaxFileMB=5)
    after = sync_and_settle(ada, gid)
    assert by_path(ada, gid)["huge.md"]["state"] == "ready" and after["last"]["added"] == 1


def test_the_most_a_folder_copies_is_stated_and_a_file_that_would_pass_it_is_skipped(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", big_text(0.6, RARE))
    write(notes, "b.md", big_text(0.6, SECOND))
    write(notes, "c.png", PNG)
    age(notes)
    ada, gid = setup(world, notes)
    install(world, documentsFolderMaxMB=1)
    done = add_and_settle(ada, gid)
    got = by_path(ada, gid)
    assert got["a.md"]["state"] == "ready"
    assert got["b.md"]["state"] == "skipped" and got["b.md"]["code"] == "over_folder_size"
    assert got["b.md"]["message"] == (
        "Skipped: this folder's files together would pass the 1 MB Workbench keeps from one folder."
    )
    assert got["c.png"]["code"] == "not_document", "a picture is not a copy, and is not counted"
    assert done["caps"]["bytes"]["reached"] is True
    assert 0.5 * MIB < done["caps"]["bytes"]["used"] < MIB
    install(world, documentsFolderMaxMB=4)
    again = sync_and_settle(ada, gid)
    assert (
        by_path(ada, gid)["b.md"]["state"] == "ready" and again["caps"]["bytes"]["reached"] is False
    )


def test_what_a_folder_already_holds_counts_against_its_size_at_every_later_check(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", big_text(0.6, RARE))
    age(notes)
    ada, gid = setup(world, notes)
    install(world, documentsFolderMaxMB=1)
    add_and_settle(ada, gid)
    later = write(notes, "b.md", big_text(0.6, SECOND))
    os.utime(later, (time.time() - OLD, time.time() - OLD))  # only the new file: a.md is as it was
    done = sync_and_settle(ada, gid)
    got = by_path(ada, gid)
    assert got["a.md"]["state"] == "ready", "a.md is not read again, and its copy is counted"
    assert got["b.md"]["state"] == "skipped" and got["b.md"]["code"] == "over_folder_size"
    assert done["caps"]["bytes"]["reached"] is True


def test_the_most_files_a_folder_gives_keeps_the_shallow_ones_and_says_what_was_left_out(
    world: World, notes: Path
) -> None:
    for n in range(8):
        write(notes, f"top{n}.md", md(f"Top {n}."))
    for n in range(6):
        write(notes, f"sub/deep{n}.md", md(f"Deep {n}."))
    age(notes)
    ada, gid = setup(world, notes)
    install(world, documentsFolderMaxFiles=10)
    done = add_and_settle(ada, gid)
    assert set(by_path(ada, gid)) == {f"top{n}.md" for n in range(8)} | {
        "sub/deep0.md",
        "sub/deep1.md",
    }
    assert done["caps"]["files"] == {"limit": 10, "found": 10, "reached": True}
    shown = folder_of(ada, gid)
    assert shown["leftCounts"] == {"over_files": 4}
    assert shown["left"][0]["code"] == "over_files"
    assert shown["left"][0]["reason"] == (
        "Left out: the folder has more than the 10 files Workbench keeps for one folder."
    )
    install(world, documentsFolderMaxFiles=20)
    wider = sync_and_settle(ada, gid)
    assert wider["counts"]["files"] == 14 and wider["caps"]["files"]["reached"] is False
    install(world, documentsFolderMaxFiles=10)
    narrower = sync_and_settle(ada, gid)
    assert narrower["counts"]["files"] == 10 and narrower["last"]["removed"] == 4
    assert set(by_path(ada, gid)) == {f"top{n}.md" for n in range(8)} | {
        "sub/deep0.md",
        "sub/deep1.md",
    }


def test_a_folder_deeper_than_the_cap_is_not_entered_and_is_named(
    world: World, notes: Path
) -> None:
    write(notes, "a/ok.md", md("One deep."))
    write(notes, "a/b/ok.md", md("Two deep."))
    write(notes, "a/b/c/too-deep.md", md("Three deep."))
    age(notes)
    ada, gid = setup(world, notes)
    install(world, documentsFolderMaxDepth=2)
    done = add_and_settle(ada, gid)
    assert set(by_path(ada, gid)) == {"a/ok.md", "a/b/ok.md"}
    assert done["caps"]["depth"] == {"limit": 2, "reached": 1}
    shown = folder_of(ada, gid)
    assert shown["left"] == [
        {
            "path": "a/b/c",
            "kind": "folder",
            "code": "too_deep",
            "reason": "Not looked at: this folder is more than 2 folders deep.",
        }
    ]
    install(world, documentsFolderMaxDepth=3)
    deeper = sync_and_settle(ada, gid)
    assert "a/b/c/too-deep.md" in by_path(ada, gid) and deeper["caps"]["depth"]["reached"] == 0


def test_a_hard_link_is_skipped_and_the_file_it_points_at_is_never_read(
    world: World, notes: Path, more_notes: Path
) -> None:
    secret = more_notes / "secret.md"
    secret.write_bytes(md(f"The {RARE} secret.", title="Secret"))
    os.link(secret, notes / "linked.md")
    write(notes, "keep.md", md("Fine."))
    age(notes)
    ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    got = by_path(ada, gid)
    assert got["linked.md"]["state"] == "skipped" and got["linked.md"]["code"] == "hard_links"
    assert got["linked.md"]["message"] == (
        "Skipped: this file has more than one hard link, so Workbench does not read it."
    )
    found = ada.post("/api/documents/test", json={"question": f"the {RARE} secret"}).json()
    assert found["passages"] == [], "the file the link points at was never read"


# --------------------------------------------------------------------------- #
# links are never followed
# --------------------------------------------------------------------------- #


def test_a_link_inside_the_folder_is_named_and_what_it_points_to_is_never_read(
    world: World, notes: Path, more_notes: Path
) -> None:
    (more_notes / "secret.md").write_bytes(md(f"The {RARE} secret outside.", title="Secret"))
    write(notes, "keep.md", md("Fine."))
    junction(notes / "shortcut", more_notes)
    age(notes)
    age(more_notes)
    ada, gid = setup(world, notes)
    outside = snapshot(more_notes)
    try:
        done = add_and_settle(ada, gid)
        assert set(by_path(ada, gid)) == {"keep.md"}
        assert done["last"]["links"] == 1
        shown = folder_of(ada, gid)
        [link] = [x for x in shown["left"] if x["path"] == "shortcut"]
        assert link["kind"] == "link" and link["code"] == "link"
        assert link["reason"] == (
            "Not followed: this is a link or a junction. Workbench never follows links out of the "
            "folder."
        )
        assert (
            ada.post("/api/documents/test", json={"question": f"the {RARE} secret outside"}).json()[
                "passages"
            ]
            == []
        )
        assert snapshot(more_notes) == outside, "and nothing was written there"
    finally:
        unlink_directory(notes / "shortcut")


def test_a_folder_that_becomes_a_link_loses_its_documents_and_the_target_is_never_read(
    world: World, notes: Path, more_notes: Path
) -> None:
    (more_notes / "inside.md").write_bytes(md(f"The {RARE} words outside.", title="Outside"))
    write(notes, "sub/inside.md", md(f"The {SECOND} words inside."))
    write(notes, "keep.md", md("Fine."))
    age(notes)
    age(more_notes)
    ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    assert set(by_path(ada, gid)) == {"keep.md", "sub/inside.md"}
    outside = snapshot(more_notes)
    (notes / "sub" / "inside.md").unlink()
    (notes / "sub").rmdir()
    junction(notes / "sub", more_notes)  # the folder is swapped for a link to somewhere else
    try:
        done = sync_and_settle(ada, gid)
        assert set(by_path(ada, gid)) == {"keep.md"}, "what was inside it is gone from the index"
        assert done["last"]["removed"] == 1 and done["last"]["links"] == 1
        found = ada.post(
            "/api/documents/test", json={"question": f"the {RARE} words outside"}
        ).json()
        assert found["passages"] == [], "and the words of the target were never read"
        assert snapshot(more_notes) == outside
    finally:
        unlink_directory(notes / "sub")


@pytest.mark.skipif(
    sys.platform == "win32", reason="a file symlink needs a privilege Windows withholds"
)
def test_a_file_symlink_is_named_and_not_read(world: World, notes: Path, more_notes: Path) -> None:
    (more_notes / "secret.md").write_bytes(md(f"The {RARE} secret.", title="Secret"))
    (notes / "alias.md").symlink_to(more_notes / "secret.md")
    write(notes, "keep.md", md("Fine."))
    age(notes)
    ada, gid = setup(world, notes)
    done = add_and_settle(ada, gid)
    assert set(by_path(ada, gid)) == {"keep.md"} and done["last"]["links"] == 1
    assert [x["code"] for x in folder_of(ada, gid)["left"]] == ["link"]


# --------------------------------------------------------------------------- #
# what goes wrong while a file is read, said as it was observed
# --------------------------------------------------------------------------- #


def make_failing(
    monkeypatch: pytest.MonkeyPatch, error: BaseException, *, only: str = ""
) -> list[int]:
    """`folder_io.read_file` fails with this error for a file (all of them when `only` is empty),
    until the test says to stop (the list gets an item)."""
    real = folder_io.read_file
    stop: list[int] = []

    def failing(*args: Any) -> Any:
        names = args[3]
        if not stop and (not only or only in "/".join(names)):
            raise error
        return real(*args)

    monkeypatch.setattr(folder_io, "read_file", failing)
    return stop


def test_a_file_another_program_holds_is_skipped_for_now_and_read_when_it_is_free(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "held.md", md(f"The {RARE} held words."))
    write(notes, "free.md", md("Free."))
    age(notes)
    ada, gid = setup(world, notes)
    stop = make_failing(monkeypatch, BlockingIOError(errno.EAGAIN, "locked"), only="held")
    add_and_settle(ada, gid)
    held = by_path(ada, gid)["held.md"]
    assert held["state"] == "skipped" and held["code"] == "busy"
    assert held["message"] == (
        "Skipped for now: another program is using this file. Workbench looks again at the next "
        "check."
    )
    stop.append(1)  # the other program lets go
    again = sync_and_settle(ada, gid)
    assert by_path(ada, gid)["held.md"]["state"] == "ready" and again["last"]["added"] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="a sharing violation is Windows'")
def test_a_file_held_open_by_another_windows_program_is_busy_and_the_next_check_reads_it(
    world: World, notes: Path
) -> None:
    import ctypes
    from ctypes import wintypes

    path = write(notes, "held.md", md(f"The {RARE} held words."))
    age(notes)
    ada, gid = setup(world, notes)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 3, 0x80, None)  # no sharing at all
    assert handle not in (None, ctypes.c_void_p(-1).value)
    try:
        add_and_settle(ada, gid)
        held = by_path(ada, gid)["held.md"]
        assert held["state"] == "skipped" and held["code"] == "busy", held
    finally:
        kernel.CloseHandle(handle)
    again = sync_and_settle(ada, gid)
    assert by_path(ada, gid)["held.md"]["state"] == "ready" and again["last"]["added"] == 1


def test_a_file_that_changes_while_it_is_read_is_skipped_for_now_and_read_at_the_next_check(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "moving.md", md(f"The {RARE} moving words."))
    age(notes)
    ada, gid = setup(world, notes)
    stop = make_failing(monkeypatch, folder_io.ChangedWhileReading(), only="moving")
    add_and_settle(ada, gid)
    got = by_path(ada, gid)["moving.md"]
    assert got["state"] == "skipped" and got["code"] == "changed"
    assert got["message"].startswith("Skipped for now: this file changed while Workbench")
    stop.append(1)
    sync_and_settle(ada, gid)
    assert by_path(ada, gid)["moving.md"]["state"] == "ready"


def test_a_file_that_cannot_be_opened_says_what_the_system_said(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "denied.md", md("Denied."))
    write(notes, "fine.md", md("Fine."))
    age(notes)
    ada, gid = setup(world, notes)
    make_failing(monkeypatch, PermissionError(errno.EACCES, "Access is denied"), only="denied")
    add_and_settle(ada, gid)
    got = by_path(ada, gid)
    assert got["denied.md"]["state"] == "skipped" and got["denied.md"]["code"] == "unreadable"
    assert got["denied.md"]["message"] == (
        "Skipped: Workbench could not read this file (permission denied). It looks again at the "
        "next check."
    )
    assert got["fine.md"]["state"] == "ready", "one file's trouble stops no other"


def test_a_file_deleted_between_the_listing_and_the_read_is_not_there_and_has_no_document(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "doomed.md", md(f"The {RARE} words."))
    write(notes, "keep.md", md("Fine."))
    age(notes)
    ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    assert "doomed.md" in by_path(ada, gid)
    os.utime(notes / "doomed.md", (time.time() - 5, time.time() - 5))  # it looks changed
    make_failing(monkeypatch, FileNotFoundError(errno.ENOENT, "gone"), only="doomed")
    done = sync_and_settle(ada, gid)
    assert set(by_path(ada, gid)) == {"keep.md"} and done["last"]["removed"] == 1


def test_a_full_disk_stops_the_check_and_says_so_and_keeps_what_was_read(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "a.md", md("First."))
    age(notes)
    ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    write(notes, "b.md", md("Second."))
    age(notes)
    stop = make_failing(monkeypatch, OSError(errno.ENOSPC, "No space left on device"), only="b.md")
    before = folder_of(ada, gid)["checkedAt"]
    assert ada.post(f"/api/documents/folders/{gid}/sync", json={}).status_code == 202
    shown = settle_folder(ada, gid, after=before, ends=("problem",))
    assert shown["problem"]["code"] == "disk_full"
    assert shown["problem"]["message"].startswith(
        "The disk Workbench keeps your documents on is full"
    )
    assert "a.md" in by_path(ada, gid) and "b.md" not in by_path(ada, gid)
    assert shown["headline"].startswith("The disk Workbench keeps your documents on is full")
    stop.append(1)  # there is room again
    recovered = sync_and_settle(ada, gid)
    assert recovered["problem"] is None and set(by_path(ada, gid)) == {"a.md", "b.md"}


def test_a_check_that_cannot_look_at_the_folder_keeps_its_documents_and_says_why(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "keep.md", md(f"The {RARE} words."))
    age(notes)
    ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    real = folder_io.scan

    def denied(*args: Any) -> Any:
        raise PermissionError(errno.EACCES, "Access is denied")

    monkeypatch.setattr(folder_io, "scan", denied)
    before = folder_of(ada, gid)["checkedAt"]
    assert ada.post(f"/api/documents/folders/{gid}/sync", json={}).status_code == 202
    shown = settle_folder(ada, gid, after=before, ends=("problem",))
    assert shown["problem"]["code"] == "unreadable"
    assert shown["problem"]["message"] == (
        "Workbench could not look at this folder (permission denied). Its files are as they were at "
        "the last check."
    )
    assert shown["headline"].startswith("Workbench could not look at this folder")
    assert "It was last checked well" in shown["headline"]
    assert set(by_path(ada, gid)) == {"keep.md"}, (
        "a failure that is not 'it is not there' removes nothing"
    )
    monkeypatch.setattr(folder_io, "scan", real)
    ok = sync_and_settle(ada, gid)
    assert ok["problem"] is None and ok["state"] == "synced"
