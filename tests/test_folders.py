"""C6: real OS file handles and authenticated, approved model calls."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import folder_io
from eugene_plexus_workbench.folders import Folders
from eugene_plexus_workbench.settings import Settings
from eugene_plexus_workbench.store import Person, Store

from .conftest import MODEL, Browser, World
from .test_tools import decide, pending


@pytest.fixture
def shared_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("c6-shared")


def setup(world: World, path: Path, *, writable: bool = True) -> tuple[Browser, Browser, str, str]:
    world.settings.account_kind = "windows_service" if os.name == "nt" else "systemd"
    owner, ada = world.browser(), world.browser()
    owner.sign_in("operator")
    ada.sign_in("p-ada")
    created = owner.post(
        "/api/folders",
        json={"name": "Ada's notes", "path": str(path), "subject": "p-ada", "writable": writable},
    )
    assert created.status_code == 201, created.text
    grant = created.json()["id"]
    chat = ada.new_chat()
    chosen = ada.patch(f"/api/chats/{chat}", json={"settings": {"folderGrants": [grant]}})
    assert chosen.status_code == 200, chosen.text
    world.gateway.mode = "tools"
    return owner, ada, chat, grant


def offer(world: World, browser: Browser, chat: str, tool: str, **args: Any) -> dict[str, Any]:
    world.gateway.tool_name = tool
    world.gateway.tool_arguments = json.dumps(args)
    sent = browser.post(f"/api/chats/{chat}/messages", json={"content": "Use this folder."})
    assert sent.status_code == 201, sent.text
    return pending(browser, chat)


def finish(
    browser: Browser, chat: str, message: dict[str, Any], approve: bool = True
) -> dict[str, Any]:
    assert decide(browser, chat, message, approve).status_code == 204
    answer = browser.wait_answer(chat)
    assert answer["status"] == "done", answer
    return answer["toolRounds"][0]["calls"][0]


def test_approved_read_edit_and_create_survive_reopen(world: World, shared_dir: Path) -> None:
    folder = shared_dir / "notes"
    folder.mkdir()
    file = folder / "one.txt"
    file.write_text("Original", encoding="utf-8")
    owner, ada, chat, grant = setup(world, folder)
    reading = offer(world, ada, chat, "read_text", path="one.txt")
    assert "Original" not in json.dumps(world.gateway.requests)
    assert decide(owner, chat, reading, True).status_code == 404
    got = finish(ada, chat, reading)
    assert got["status"] == "done"
    result = json.loads(got["result"])
    assert result["text"] == "Original"
    assert str(folder) not in json.dumps(world.gateway.requests)
    writing = offer(
        world,
        ada,
        chat,
        "write_text",
        path="one.txt",
        text="Revised",
        expectedSha256=result["sha256"],
    )
    assert file.read_text() == "Original"
    assert finish(ada, chat, writing)["status"] == "done"
    assert file.read_text() == "Revised"
    create = offer(world, ada, chat, "write_text", path="new.txt", text="New", expectedSha256="")
    assert not (folder / "new.txt").exists()
    assert finish(ada, chat, create)["status"] == "done"
    assert (folder / "new.txt").read_text() == "New"
    assert owner.get("/api/folders").json()["grants"][0]["path"] == str(folder)
    assert "path" not in ada.get("/api/folders").json()["grants"][0]
    world.restart_workbench()
    assert ada.get("/api/folders").json()["grants"][0]["id"] == grant
    saved = ada.get(f"/api/chats/{chat}").json()["messages"][-1]
    assert saved["toolRounds"][0]["calls"][0]["status"] == "done"


@pytest.mark.parametrize("action", ["decline", "remove", "stop", "account-lost", "changed"])
def test_pending_writes_cannot_outlive_permission_or_version(
    world: World, shared_dir: Path, action: str
) -> None:
    file = shared_dir / "one.txt"
    file.write_text("Original", encoding="utf-8")
    owner, ada, chat, grant = setup(world, shared_dir)
    digest = json.loads(
        finish(ada, chat, offer(world, ada, chat, "read_text", path="one.txt"))["result"]
    )["sha256"]
    message = offer(
        world, ada, chat, "write_text", path="one.txt", text="Unexpected", expectedSha256=digest
    )
    if action == "remove":
        assert owner.delete(f"/api/folders/{grant}").status_code == 204
    if action == "account-lost":
        world.settings.account_kind = None
    if action == "changed":
        file.write_text("Someone else's edit", encoding="utf-8")
    if action == "stop":
        assert ada.post(f"/api/chats/{chat}/stop").status_code == 204
    else:
        call = finish(ada, chat, message, approve=action != "decline")
        assert call["status"] in {"failed", "declined"}, call
    assert file.read_text() == ("Someone else's edit" if action == "changed" else "Original")


def test_grants_belong_to_the_named_person_and_read_only_is_enforced(
    world: World, shared_dir: Path
) -> None:
    owner, ada, chat, grant = setup(world, shared_dir, writable=False)
    bo = world.browser()
    bo.sign_in("p-bo")
    assert bo.get("/api/folders").json()["grants"] == []
    assert bo.get("/api/folders/people").status_code == 403
    for browser in (owner, bo):
        other = browser.new_chat()
        assert (
            browser.patch(
                f"/api/chats/{other}", json={"settings": {"folderGrants": [grant]}}
            ).status_code
            == 400
        )
    assert (
        ada.post(
            "/api/folders", json={"name": "bad", "path": str(shared_dir), "subject": "p-ada"}
        ).status_code
        == 403
    )
    assert ada.delete(f"/api/folders/{grant}").status_code == 403
    message = offer(world, ada, chat, "list_directory", path=".")
    descriptions = [t["function"]["description"] for t in world.gateway.requests[-1]["tools"]]
    assert all(": write_text." not in d for d in descriptions)
    assert finish(ada, chat, message)["status"] == "done"


def test_account_and_private_roots_cannot_be_granted(world: World, shared_dir: Path) -> None:
    owner = world.browser()
    owner.sign_in("operator")
    body = {"name": "files", "path": str(shared_dir), "subject": "operator"}
    assert owner.post("/api/folders", json=body).status_code == 400
    world.settings.account_kind = "windows_service" if os.name == "nt" else "systemd"
    (world.data / "nested").mkdir()
    for private in (world.data, world.data.parent, world.data / "nested"):
        refused = owner.post("/api/folders", json={**body, "path": str(private)})
        assert refused.status_code == 400, refused.text
    assert owner.post("/api/folders", json={**body, "subject": "not-signed-in"}).status_code == 400


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/secret",
        "a/../../secret",
        "C:/secret",
        "a\\secret",
        "file:stream",
        "NUL",
        "COM1.txt",
        "a/./file",
        "a//file",
        "file.",
        "file ",
        "a\x00b",
    ],
)
def test_relative_paths_cannot_escape(tmp_path: Path, path: str) -> None:
    with pytest.raises(folder_io.FolderError):
        folder_io.operate(
            str(tmp_path), folder_io.inspect(str(tmp_path), []), "read_text", {"path": path}, []
        )


def test_hard_links_and_create_over_existing_are_refused(tmp_path: Path) -> None:
    grant = tmp_path / "grant"
    grant.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("private", encoding="utf-8")
    os.link(outside, grant / "linked.txt")
    identity = folder_io.inspect(str(grant), [])
    for tool, args in (
        ("read_text", {"path": "linked.txt"}),
        ("write_text", {"path": "linked.txt", "text": "bad", "expectedSha256": "a" * 64}),
    ):
        with pytest.raises(folder_io.FolderError, match="hard link"):
            folder_io.operate(str(grant), identity, tool, args, [])
    with pytest.raises(FileExistsError):
        folder_io.operate(
            str(grant),
            identity,
            "write_text",
            {"path": "linked.txt", "text": "bad", "expectedSha256": ""},
            [],
        )
    assert outside.read_text() == "private"


def test_directory_links_and_replaced_roots_are_refused(tmp_path: Path) -> None:
    folder, outside = tmp_path / "grant", tmp_path / "outside"
    folder.mkdir()
    outside.mkdir()
    (outside / "secret").write_text("private", encoding="utf-8")
    link = folder / "link"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        identity = folder_io.inspect(str(folder), [])
        with pytest.raises((OSError, folder_io.FolderError)):
            folder_io.operate(str(folder), identity, "read_text", {"path": "link/secret"}, [])
        with pytest.raises((OSError, folder_io.FolderError)):
            folder_io.inspect(str(link), [])
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()
    folder.rename(tmp_path / "original")
    folder.mkdir()
    (folder / "secret").write_text("replacement", encoding="utf-8")
    with pytest.raises(folder_io.FolderError, match="replaced"):
        folder_io.operate(str(folder), identity, "read_text", {"path": "secret"}, [])


def test_large_binary_and_directory_results_are_bounded(tmp_path: Path) -> None:
    identity = folder_io.inspect(str(tmp_path), [])
    for content in (b"x" * 32769, b"x\x00y", b"\xff"):
        (tmp_path / "bad").write_bytes(content)
        with pytest.raises(folder_io.FolderError):
            folder_io.operate(str(tmp_path), identity, "read_text", {"path": "bad"}, [])
    for i in range(205):
        (tmp_path / f"entry{i}").touch()
    got = folder_io.operate(str(tmp_path), identity, "list_directory", {"path": "."}, [])
    assert len(got["names"]) == 200 and got["truncated"] is True


def test_path_replacement_cannot_change_the_opened_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "grant"
    folder.mkdir()
    file = folder / "read.txt"
    file.write_text("Granted", encoding="utf-8")
    outside = tmp_path / "private.txt"
    outside.write_text("Private", encoding="utf-8")
    original = folder_io._read

    def replace_after_open(fd: int) -> tuple[str, str]:
        if os.name == "nt":
            # Held handles refuse rename/delete while a file call is in flight.
            with pytest.raises(PermissionError):
                file.rename(folder / "old.txt")
        else:
            file.rename(folder / "old.txt")
            file.symlink_to(outside)
        return original(fd)

    monkeypatch.setattr(folder_io, "_read", replace_after_open)
    got = folder_io.operate(
        str(folder), folder_io.inspect(str(folder), []), "read_text", {"path": "read.txt"}, []
    )
    assert got["text"] == "Granted"
    assert outside.read_text() == "Private"


def test_flush_failure_is_uncertain_and_does_not_continue_the_model(
    world: World, shared_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ada, chat, _ = setup(world, shared_dir)
    message = offer(
        world, ada, chat, "write_text", path="new.txt", text="Written", expectedSha256=""
    )
    before = len(world.gateway.requests)

    def failed_flush(fd: int) -> None:
        raise OSError("Injected flush failure")

    monkeypatch.setattr(folder_io.os, "fsync", failed_flush)
    assert decide(ada, chat, message, True).status_code == 204
    answer = ada.wait_answer(chat)
    assert answer["toolRounds"][0]["calls"][0]["status"] == "uncertain"
    assert (shared_dir / "new.txt").read_text() == "Written"
    assert len(world.gateway.requests) == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX special files and byte names")
def test_fifo_and_non_utf8_names_fail_safely(tmp_path: Path) -> None:
    identity = folder_io.inspect(str(tmp_path), [])
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(folder_io.FolderError):
        folder_io.operate(str(tmp_path), identity, "read_text", {"path": "pipe"}, [])
    bad_name = os.fsencode(tmp_path) + b"/bad-\xff"
    fd = os.open(bad_name, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    with pytest.raises(folder_io.FolderError, match="UTF-8"):
        folder_io.operate(str(tmp_path), identity, "list_directory", {"path": "."}, [])


async def test_repeated_stop_holds_revocation_until_the_file_worker_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "private/workbench.sqlite3")
    await store.open()
    person = Person("ada", "Ada", "member")
    await store.upsert_person(person)
    path = tmp_path / "granted"
    path.mkdir()
    folders = Folders(store, Settings(data_dir=tmp_path / "private", account_kind="systemd"))
    await folders.add(
        {"id": "grant", "name": "files", "path": str(path), "subject": person.sub, "writable": True}
    )
    entered, release = threading.Event(), threading.Event()
    original = folder_io.operate

    def slow(*args: Any) -> Any:
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(folder_io, "operate", slow)
    running = asyncio.create_task(
        folders.execute(
            "grant",
            person,
            "write_text",
            {"path": "saved.txt", "text": "one write", "expectedSha256": ""},
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        running.cancel()
        await asyncio.sleep(0)
        running.cancel()
        revoke = asyncio.create_task(folders.remove("grant"))
        await asyncio.sleep(0.05)
        assert not revoke.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await running
        await revoke
        assert (path / "saved.txt").read_text() == "one write"
        assert await store.folder_grants() == []
    finally:
        release.set()
        await asyncio.gather(running, return_exceptions=True)
        await store.close()


@pytest.mark.skipif(
    not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="Set WORKBENCH_PLAYWRIGHT for real Chrome"
)
def test_folders_in_system_chrome(world: World, shared_dir: Path, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    world.settings.account_kind = "windows_service" if os.name == "nt" else "systemd"
    owner = world.browser()
    owner.sign_in("operator")
    world.gateway.mode = "tools"
    world.gateway.tool_name = "write_text"
    world.gateway.tool_arguments = json.dumps(
        {"path": "browser.txt", "text": "Approved in Chrome", "expectedSha256": ""}
    )
    cfg = tmp_path / "folder-browser.json"
    cfg.write_text(
        json.dumps(
            {
                "url": world.workbench,
                "folder": str(shared_dir),
                "secret": owner.secret,
                "model": MODEL,
                "playwright": os.environ["WORKBENCH_PLAYWRIGHT"],
                "chrome": os.getenv(
                    "WORKBENCH_CHROME", "C:/Program Files/Google/Chrome/Application/chrome.exe"
                ),
                "cookies": [
                    {
                        "name": c.name,
                        "value": c.value,
                        "url": world.workbench,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                    for c in owner.http.cookies.jar
                ],
                "screenshot": str(tmp_path / "tools-phone.png"),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/tools-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (shared_dir / "browser.txt").read_text() == "Approved in Chrome"
