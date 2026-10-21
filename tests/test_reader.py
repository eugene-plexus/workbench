"""Reading attached files in a child process (MR2, MRC19): the cause of every end.

Real processes, real files, a real store. A file that hangs the reader, one that
crashes it, one that is removed while it is read, and one that is read after a
restart, each leave Workbench answering and the file in a state that is true.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import files
from eugene_plexus_workbench.knowledge import extract
from eugene_plexus_workbench.knowledge import reader as readers
from eugene_plexus_workbench.knowledge.reader import Reader
from eugene_plexus_workbench.store import Chat, FileRecord, Store

from . import documents as docs

OWNER = "p-ada"


class World:
    """A store and a reader over a data directory, as the app has them."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "data"
        self.store = Store(tmp_path / "workbench.sqlite3")
        self.reader = Reader(self.store, self.root)
        self.n = 0

    async def add(
        self, name: str, data: bytes, kind: str, *, media: str = files.PDF, state: str = "reading"
    ) -> FileRecord:
        self.n += 1
        record = FileRecord(
            id=f"file{self.n}",
            owner=OWNER,
            chat_id="c1",
            name=name,
            media_type=media,
            size=len(data),
            created_at=time.time(),
            reading={"state": state, "kind": kind, "language": None, "nonce": "7f3a9c1e"},
        )
        path = files.path_of(self.root, OWNER, record.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        await self.store.add_file(record)
        return record

    async def read(self, record: FileRecord, seconds: float = 60) -> dict[str, Any]:
        await self.reader.start(record)
        assert not await self.reader.wait([record.id], seconds), "still reading"
        found = await self.store.file(record.id)
        assert found is not None and found.reading is not None
        return found.reading


@pytest.fixture
async def world(tmp_path: Path) -> Any:
    found = World(tmp_path)
    await found.store.open()
    await found.store.create_chat(
        Chat(id="c1", owner=OWNER, title="t", model="m", created_at=1.0, updated_at=1.0)
    )
    try:
        yield found
    finally:
        await found.reader.aclose()
        await found.store.close()


def alive(pid: int) -> bool:
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


def slurp(path: Path | str) -> str:
    return Path(path).read_text(encoding="utf-8")


def exists(path: Path | str) -> bool:
    return Path(path).exists()


def same_place(one: Path | str, other: Path | str) -> bool:
    return Path(one).resolve() == Path(other).resolve()


async def until(condition: Any, seconds: float = 20.0) -> bool:
    """Wait, without holding up the loop the reader runs on, for something to be true."""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.05)
    return bool(condition())


def stand_in(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    """Replace the worker with a program of our own: `code` is given the file's
    path and the result path as `sys.argv[1]` and `sys.argv[2]`."""

    def command(
        python: str, source: Path, output: Path, kind: str, language: str | None
    ) -> list[str]:
        return [python, "-c", textwrap.dedent(code), str(source), str(output)]

    monkeypatch.setattr(readers, "worker_command", command)


HANG = """
    import os, sys, time
    # Written whole, then named: a test that sees the file reads the number.
    with open(sys.argv[2] + ".pid.part", "w") as f:
        f.write(str(os.getpid()))
    os.replace(sys.argv[2] + ".pid.part", sys.argv[2] + ".pid")
    time.sleep(600)
"""


# --------------------------------------------------------------------------- #
# what a reading comes to
# --------------------------------------------------------------------------- #


async def test_a_pdf_is_read_in_a_child_and_its_text_is_kept_beside_the_original(
    world: World,
) -> None:
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    done = await world.read(record)
    assert done["state"] == "ready" and done["pages"] == 3 and done["kind"] == "pdf"
    assert done["nonce"] == "7f3a9c1e", "what was there before is kept"
    assert done["finishedAt"] > 0 and done["message"] is None
    sidecar = files.read_path_of(world.root, OWNER, record.id)
    assert sidecar.parent == files.path_of(world.root, OWNER, record.id).parent, "beside it"
    saved = extract.Extracted.from_json(json.loads(sidecar.read_text(encoding="utf-8")))
    assert saved.pages == 3 and docs.CODE_WORD in saved.parts[2].text
    assert done["tokens"] == sum(done["partTokens"]) > 0 and len(done["partTokens"]) == 3
    assert not list((world.root / "reading").glob("*.json")), "no result file is left lying about"
    assert files.path_of(world.root, OWNER, record.id).read_bytes() == docs.three_pages()


async def test_a_text_file_is_read_with_its_language_and_lines(world: World) -> None:
    record = await world.add("a.py", docs.CODE, "code", media="text/plain")
    record.reading["language"] = "python"  # type: ignore[index]
    await world.store.merge_file_reading(record.id, {"language": "python"})
    done = await world.read(record)
    assert done["state"] == "ready" and done["language"] == "python" and done["lines"] == 5
    assert done["pages"] is None and done["partTokens"] is None


@pytest.mark.parametrize(
    ("make", "state", "code", "says"),
    [
        (docs.pdf_encrypted, "skipped", "password", "Skipped: this file is password-protected."),
        (
            lambda: docs.pdf_pictures(3),
            "skipped",
            "no_text",
            "Skipped: this PDF has no text, only pictures of pages.",
        ),
        (docs.pdf_damaged, "failed", "damaged", "Failed: Workbench could not read this file ("),
    ],
)
async def test_a_file_that_gives_no_text_ends_in_a_state_that_says_why(
    world: World, make: Any, state: str, code: str, says: str
) -> None:
    record = await world.add("r.pdf", make(), "pdf")
    done = await world.read(record)
    assert (done["state"], done["code"]) == (state, code)
    assert done["message"].startswith(says)
    assert not files.read_path_of(world.root, OWNER, record.id).exists(), "no text, none kept"
    assert done["nonce"] == "7f3a9c1e"
    if code == "no_text":
        assert done["pages"] == 3, "a scan still says how many pages it has"


async def test_a_corrupt_file_fails_that_one_file_and_the_next_is_read(world: World) -> None:
    bad = await world.add("bad.pdf", docs.pdf_damaged(), "pdf")
    good = await world.add("good.pdf", docs.three_pages(), "pdf")
    await world.reader.start(bad)
    await world.reader.start(good)
    assert not await world.reader.wait([bad.id, good.id], 60)
    assert (await world.store.file(bad.id)).reading["state"] == "failed"  # type: ignore[union-attr,index]
    assert (await world.store.file(good.id)).reading["state"] == "ready"  # type: ignore[union-attr,index]


async def test_an_empty_text_file_is_skipped(world: World) -> None:
    record = await world.add("e.txt", b"  \n", "text", media="text/plain")
    done = await world.read(record)
    assert done["state"] == "skipped" and done["message"] == "Skipped: this file has no text in it."


async def test_a_file_that_is_not_on_the_disk_fails_by_name(world: World) -> None:
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    files.path_of(world.root, OWNER, record.id).unlink()
    done = await world.read(record)
    assert done["state"] == "failed" and done["code"] == "missing"


# --------------------------------------------------------------------------- #
# a reader that hangs, and one that dies
# --------------------------------------------------------------------------- #


async def test_a_reader_that_hangs_is_ended_with_its_whole_tree_and_the_file_says_so(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readers, "TIMEOUT_SECONDS", 2.0)
    stand_in(monkeypatch, HANG)
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    started = time.perf_counter()
    done = await world.read(record, 30)
    took = time.perf_counter() - started
    assert 1.5 < took < 20, "ended after its time, not before and not never"
    assert (done["state"], done["code"]) == ("failed", "timeout")
    assert (
        done["message"]
        == "Failed: reading this file took longer than 2 seconds, so Workbench stopped."
    )
    pid_file = world.root / "reading" / f"{record.id}.json.pid"
    pid = int(pid_file.read_text(encoding="utf-8"))
    assert await until(lambda: not alive(pid), 10), "the process that was reading is gone"
    assert not world.reader.reading(record.id)


async def test_a_hung_reader_does_not_stop_another_file_being_read(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readers, "TIMEOUT_SECONDS", 4.0)
    real = readers.worker_command

    def command(
        python: str, source: Path, output: Path, kind: str, language: str | None
    ) -> list[str]:
        if source.name == "file1":
            return [python, "-c", textwrap.dedent(HANG), str(source), str(output)]
        return real(python, source, output, kind, language)

    monkeypatch.setattr(readers, "worker_command", command)
    slow = await world.add("slow.pdf", docs.three_pages(), "pdf")
    fast = await world.add("fast.pdf", docs.three_pages(), "pdf")
    await world.reader.start(slow)
    await world.reader.start(fast)
    assert not await world.reader.wait([fast.id], 30)
    assert (await world.store.file(fast.id)).reading["state"] == "ready"  # type: ignore[union-attr,index]
    assert world.reader.reading(slow.id), "while the other is still going"
    await world.reader.cancel([slow.id])


@pytest.mark.parametrize(
    ("code", "says"),
    [
        (
            "import os, sys; os._exit(0xC0000005 - 2**32 if sys.platform == 'win32' else 139)",
            None,
        ),
        (
            "import sys; print('Fatal Python error: out of luck', file=sys.stderr); sys.exit(3)",
            "exit code 3; it said: Fatal Python error: out of luck",
        ),
        (
            "import os, signal; os.kill(os.getpid(), signal.SIGKILL if sys.platform != 'win32' else 9)",
            None,
        ),
    ],
)
async def test_a_reader_that_dies_leaves_the_cause_it_died_of(
    world: World, monkeypatch: pytest.MonkeyPatch, code: str, says: str | None
) -> None:
    if "SIGKILL" in code and sys.platform == "win32":
        pytest.skip("signals are POSIX")
    stand_in(monkeypatch, "import sys\n" + code)
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    done = await world.read(record)
    assert (done["state"], done["code"]) == ("failed", "crashed")
    assert done["message"].startswith(
        "Failed: Workbench could not read this file (the reader stopped unexpectedly: "
    )
    if says is not None:
        assert says in done["message"]
    elif sys.platform == "win32" and "0xC0000005" in code:
        assert "exit code 0xC0000005, an access violation" in done["message"]
    elif "SIGKILL" in code:
        assert "ended by SIGKILL" in done["message"]


async def test_a_reader_that_really_crashes_does_not_take_workbench_with_it(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand_in(
        monkeypatch,
        """
        import sys
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.SetErrorMode(0x8003)  # no crash dialog, as the worker sets
        import faulthandler
        faulthandler.enable()
        faulthandler._sigsegv()  # a real access violation, which Python cannot catch
        """,
    )
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    done = await world.read(record)
    assert (done["state"], done["code"]) == ("failed", "crashed")
    cause = done["detail"]
    # The interpreter's own fatal-error report, or the signal it died of.
    assert "Segmentation fault" in cause or "SIGSEGV" in cause, cause
    assert "stopped unexpectedly" in done["message"]
    assert "Segmentation fault" in done["message"] or "SIGSEGV" in done["message"]
    # And the next file is read by a new reader.
    monkeypatch.undo()
    other = await world.add("ok.pdf", docs.three_pages(), "pdf")
    assert (await world.read(other))["state"] == "ready"


async def test_a_reader_that_leaves_something_that_is_not_a_result_is_a_failure_by_name(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    for text, what in (("not json at all", "no result"), ("[1, 2]", "not an object")):
        stand_in(monkeypatch, f"import sys; open(sys.argv[2], 'w').write({text!r})")
        record = await world.add("r.pdf", docs.three_pages(), "pdf")
        done = await world.read(record)
        assert (done["state"], done["code"]) == ("failed", "crashed")
        assert what in done["detail"] or "no result" in done["detail"]
    stand_in(
        monkeypatch, 'import sys; open(sys.argv[2], "w").write(\'{"ok": true, "kind": "exe"}\')'
    )
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    done = await world.read(record)
    assert (done["state"], done["code"]) == ("failed", "damaged")
    assert "gave text it could not use" in done["message"]


async def test_an_unreadable_verdict_the_worker_gives_is_kept_as_given(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    verdict = {
        "ok": False,
        "code": "too_big",
        "reason": "this file is huge.",
        "state": "skipped",
        "pages": 7,
    }
    stand_in(
        monkeypatch, f"import sys, json; open(sys.argv[2], 'w').write(json.dumps({verdict!r}))"
    )
    done = await world.read(await world.add("r.pdf", docs.three_pages(), "pdf"))
    assert (done["state"], done["code"], done["pages"]) == ("skipped", "too_big", 7)
    assert done["message"] == "Skipped: this file is huge."


# --------------------------------------------------------------------------- #
# what a reader is given, and what it is not
# --------------------------------------------------------------------------- #


async def test_a_reader_holds_none_of_workbenchs_secrets_and_works_in_a_directory_of_its_own(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EUGENE_PLEXUS_APP_ADMIN_TOKEN", "the-admin-token")
    monkeypatch.setenv("EUGENE_PLEXUS_APP_KEY_FILE", "C:/secret/key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    stand_in(
        monkeypatch,
        """
        import json, os, sys
        json.dump({"env": dict(os.environ), "cwd": os.getcwd()}, open(sys.argv[1] + ".seen", "w"))
        sys.exit(0)
        """,
    )
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    await world.read(record)
    seen = json.loads(slurp(str(files.path_of(world.root, OWNER, record.id)) + ".seen"))
    env = {k.upper() for k in seen["env"]}
    assert not any(k.startswith("EUGENE_PLEXUS_") for k in env), env
    assert not {"AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY"} & env
    # What it was given, and what the venv's launcher adds for the real interpreter.
    assert env <= {*(k.upper() for k in readers._KEEP), "PYTHONUTF8", "__PYVENV_LAUNCHER__"}, env
    assert same_place(seen["cwd"], world.root / "reading")


def test_the_environment_is_a_short_list_and_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EUGENE_PLEXUS_X", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://p")
    monkeypatch.setenv("PYTHONPATH", "/somewhere")
    env = readers.child_environment()
    assert "EUGENE_PLEXUS_X" not in env and "HTTPS_PROXY" not in env
    assert env["PYTHONPATH"] == "/somewhere" and env["PYTHONUTF8"] == "1"
    assert set(env) <= {*readers._KEEP, "PYTHONUTF8"}


def test_the_interpreter_is_the_venvs_own_under_a_windows_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Lib" / "pythonservice.exe"))
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    with pytest.raises(FileNotFoundError, match=r"python\.exe"):
        readers.worker_python()
    (tmp_path / "Scripts").mkdir()
    (tmp_path / "Scripts" / "python.exe").write_bytes(b"")
    assert readers.worker_python() == str(tmp_path / "Scripts" / "python.exe")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))
    assert readers.worker_python() == str(tmp_path / "bin" / "python"), (
        "ordinarily it is sys.executable"
    )


def test_a_crash_is_named_by_its_exit_code_or_signal_and_the_last_thing_it_said() -> None:
    assert readers.crash_cause(0xC0000005, "") == "exit code 0xC0000005, an access violation"
    assert readers.crash_cause(0xC00000FD, "") == "exit code 0xC00000FD, a stack overflow"
    assert readers.crash_cause(0xC0001234, "") == "exit code 0xC0001234, a crash"
    assert readers.crash_cause(-signal.SIGABRT, "") == "the process was ended by SIGABRT"
    assert readers.crash_cause(-250, "") == "exit code -250"
    assert readers.crash_cause(1, "") == "exit code 1"
    said = "fine\nWindows fatal exception: access violation\n  File x\n"
    assert (
        readers.crash_cause(3, said)
        == "exit code 3; it said: Windows fatal exception: access violation"
    )


def test_what_is_kept_of_a_reading_is_its_size_in_tokens_page_by_page() -> None:
    got = extract.Extracted(
        kind="pdf",
        parts=[
            extract.Part("a" * 30, page=1),
            extract.Part("", page=2),
            extract.Part("b" * 60, page=3),
        ],
        pages=3,
        notes=["Page 2 has no text (it may be a picture)."],
    )
    found = readers.summarize(got)
    assert found["state"] == "ready" and found["pages"] == 3 and found["kind"] == "pdf"
    first = (len("[page 1]\n" + "a" * 30) + 2) // 3 + 1
    blank = (len("[page 2] (no text on this page)") + 2) // 3 + 1
    third = (len("[page 3]\n" + "b" * 60) + 2) // 3
    assert found["partTokens"] == [first, blank, third] and found["tokens"] == first + blank + third
    assert found["chars"] == sum(len(extract.render_part(p)) for p in got.parts)
    assert found["notes"] == ["Page 2 has no text (it may be a picture)."]
    text = readers.summarize(
        extract.Extracted(kind="markdown", parts=[extract.Part("a" * 30)], lines=1)
    )
    assert text["partTokens"] is None and text["tokens"] == 10 and text["lines"] == 1


def test_a_failure_is_recorded_in_the_words_the_page_shows() -> None:
    got = readers.failure("skipped", "password", "this file is password-protected.", pages=3)
    assert got == {
        "state": "skipped",
        "code": "password",
        "reason": "this file is password-protected.",
        "message": "Skipped: this file is password-protected.",
        "pages": 3,
        "detail": None,
    }
    assert readers.failure("failed", "x", "y")["message"] == "Failed: y"


# --------------------------------------------------------------------------- #
# how many, and when they stop
# --------------------------------------------------------------------------- #


async def test_only_a_few_files_are_read_at_once(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand_in(
        monkeypatch,
        """
        import sys, time
        began = time.time(); time.sleep(0.7)
        open(sys.argv[1] + ".span", "w").write(f"{began} {time.time()}")
        """,
    )
    records = [await world.add(f"{n}.pdf", docs.three_pages(), "pdf") for n in range(5)]
    for record in records:
        await world.reader.start(record)
    assert not await world.reader.wait([r.id for r in records], 60)
    spans = []
    for record in records:
        began, ended = slurp(str(files.path_of(world.root, OWNER, record.id)) + ".span").split()
        spans.append((float(began), float(ended)))
    assert len(spans) == 5
    busiest = max(sum(1 for a, b in spans if a <= moment < b) for moment, _ in spans)
    assert busiest <= readers.CONCURRENCY == 2, spans


async def test_a_file_asked_to_be_read_twice_is_read_once(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand_in(
        monkeypatch,
        """
        import sys, time
        open(sys.argv[1] + ".ran", "a").write("x")
        time.sleep(0.5)
        """,
    )
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    await world.reader.start(record)
    await world.reader.start(record)
    assert not await world.reader.wait([record.id], 30)
    assert slurp(str(files.path_of(world.root, OWNER, record.id)) + ".ran") == "x"


async def test_a_wait_gives_back_what_is_still_being_read(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand_in(monkeypatch, HANG)
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    await world.reader.start(record)
    assert await world.reader.wait([record.id], 0.3) == {record.id}
    assert await world.reader.wait(["no-such-file"], 0.1) == set()
    await world.reader.cancel([record.id])
    assert not world.reader.reading(record.id)


async def test_a_file_removed_while_it_is_read_ends_its_reader_at_once_and_leaves_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readers, "TIMEOUT_SECONDS", 25.0)
    stand_in(monkeypatch, HANG)
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    await world.reader.start(record)
    pid_file = world.root / "reading" / f"{record.id}.json.pid"
    assert await until(pid_file.exists)
    pid = int(pid_file.read_text(encoding="utf-8"))
    started = time.perf_counter()
    await world.reader.cancel([record.id])
    assert time.perf_counter() - started < 15, "it did not wait for the time limit"
    assert await until(lambda: not alive(pid), 10)
    assert not files.read_path_of(world.root, OWNER, record.id).exists()


async def test_a_file_whose_row_goes_before_its_reading_ends_leaves_no_text_behind(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand_in(
        monkeypatch,
        """
        import sys, time
        time.sleep(1.0)
        from eugene_plexus_workbench.knowledge import worker
        raise SystemExit(worker.main([sys.argv[1], sys.argv[2], "pdf", "-"]))
        """,
    )
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    await world.reader.start(record)
    await world.store.delete_chat("c1")  # the chat, and its files' rows, go
    assert not await world.reader.wait([record.id], 60)
    assert await world.store.file(record.id) is None
    assert not files.read_path_of(world.root, OWNER, record.id).exists(), "no text with no file"
    assert not list((world.root / "reading").glob("*.json"))


async def test_shutting_down_ends_every_reader(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readers, "TIMEOUT_SECONDS", 25.0)
    stand_in(monkeypatch, HANG)
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    await world.reader.start(record)
    pid_file = world.root / "reading" / f"{record.id}.json.pid"
    assert await until(pid_file.exists)
    pid = int(pid_file.read_text(encoding="utf-8"))
    await world.reader.aclose()
    assert await until(lambda: not alive(pid), 10)


async def test_files_a_stop_left_part_way_are_read_again_at_boot_and_the_rest_left_alone(
    world: World,
) -> None:
    stopped = await world.add("stopped.pdf", docs.three_pages(), "pdf", state="reading")
    done = await world.add("done.pdf", docs.three_pages(), "pdf", state="ready")
    failed = await world.add("failed.pdf", docs.three_pages(), "pdf", state="failed")
    assert [r.id for r in await world.store.files_reading()] == [stopped.id]
    assert await world.reader.resume() == 1
    assert not await world.reader.wait([stopped.id], 60)
    assert (await world.store.file(stopped.id)).reading["state"] == "ready"  # type: ignore[union-attr,index]
    assert (await world.store.file(done.id)).reading["state"] == "ready"  # type: ignore[union-attr,index]
    assert not files.read_path_of(world.root, OWNER, done.id).exists(), "not read: it was only said"
    assert (await world.store.file(failed.id)).reading["state"] == "failed"  # type: ignore[union-attr,index]
    assert await world.reader.resume() == 0


async def test_a_bug_in_workbench_itself_ends_in_a_failed_file_and_not_a_chip_that_reads_for_ever(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: Reader, record: FileRecord) -> Any:
        raise RuntimeError("a bug")

    monkeypatch.setattr(Reader, "_work", broken)
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    done = await world.read(record)
    assert (done["state"], done["code"]) == ("failed", "internal")
    assert "RuntimeError" in done["message"] and "See Workbench's log" in done["message"]


async def test_the_reader_will_not_start_when_it_has_no_python_to_run(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def none() -> str:
        raise FileNotFoundError("The service's Python interpreter is missing: C:/x/python.exe")

    monkeypatch.setattr(readers, "worker_python", none)
    done = await world.read(await world.add("r.pdf", docs.three_pages(), "pdf"))
    assert (done["state"], done["code"]) == ("failed", "unavailable")
    assert "could not start its reader" in done["message"] and "python.exe" in done["message"]


async def test_what_a_stop_left_in_the_work_directory_is_cleared_at_boot(world: World) -> None:
    work = world.root / "reading"
    work.mkdir(parents=True)
    (work / "old.json").write_text("{}", encoding="utf-8")
    (work / "old.json.part").write_text("{", encoding="utf-8")
    assert await world.reader.resume() == 0
    assert not work.exists() or list(work.iterdir()) == []


async def test_a_reader_ended_in_the_middle_of_writing_leaves_no_half_a_result(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readers, "TIMEOUT_SECONDS", 1.5)
    stand_in(
        monkeypatch,
        """
        import sys, time
        open(sys.argv[2] + ".part", "w").write('{"ok": tr')
        time.sleep(600)
        """,
    )
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    done = await world.read(record, 30)
    assert done["code"] == "timeout"
    assert not list((world.root / "reading").iterdir()), "not the result, and not half of it"


async def test_a_file_removed_before_its_reader_starts_starts_no_reader(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand_in(
        monkeypatch,
        """
        import sys
        open(sys.argv[1] + ".started", "w").write("x")
        """,
    )
    record = await world.add("r.pdf", docs.three_pages(), "pdf")
    await world.reader.start(record)
    await world.reader.cancel([record.id])  # before the task has had its first turn
    assert not world.reader.reading(record.id)
    assert not exists(str(files.path_of(world.root, OWNER, record.id)) + ".started")


async def test_a_worker_whose_workbench_is_gone_ends_itself_past_its_own_deadline(
    tmp_path: Path,
) -> None:
    """Workbench ends a reader at its limit; this is the worker's own, for the day
    Workbench is not there and a library never lets go of the interpreter."""
    source = tmp_path / "hangs.pdf"
    source.write_bytes(docs.three_pages())
    program = textwrap.dedent(
        """
        import sys, time
        from eugene_plexus_workbench.knowledge import extract, worker

        worker.DEADLINE_SECONDS = 1.0
        extract.read = lambda *args, **kwargs: time.sleep(600)
        raise SystemExit(worker.main(sys.argv[1:]))
        """
    )
    started = time.perf_counter()
    ended = await asyncio.to_thread(
        subprocess.run,
        [readers.worker_python(), "-c", program, str(source), str(tmp_path / "out.json"), "pdf"],
        capture_output=True,
        text=True,
        timeout=30,
        env=readers.child_environment(),
        cwd=tmp_path,
        check=False,
    )
    assert time.perf_counter() - started > 0.9, "not before its time"
    assert ended.returncode != 0 and "Timeout" in ended.stderr, ended.stderr[-500:]
    assert not (tmp_path / "out.json").exists(), "and it left no result"


async def test_a_worker_that_finishes_in_time_is_not_ended_by_its_deadline(
    tmp_path: Path,
) -> None:
    source = tmp_path / "ok.pdf"
    source.write_bytes(docs.three_pages())
    program = textwrap.dedent(
        """
        import sys, time
        from eugene_plexus_workbench.knowledge import worker

        worker.DEADLINE_SECONDS = 2.0
        code = worker.main(sys.argv[1:])
        time.sleep(3.5)  # past the deadline: the watchdog must have been stood down
        raise SystemExit(code)
        """
    )
    ended = await asyncio.to_thread(
        subprocess.run,
        [readers.worker_python(), "-c", program, str(source), str(tmp_path / "out.json"), "pdf"],
        capture_output=True,
        text=True,
        timeout=60,
        env=readers.child_environment(),
        cwd=tmp_path,
        check=False,
    )
    assert ended.returncode == 0, ended.stderr[-500:]
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["ok"] is True
