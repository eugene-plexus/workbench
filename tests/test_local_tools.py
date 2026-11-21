"""C5b crosses the HTTP, sign-in and real process boundaries."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from .conftest import Browser, World
from .test_tools import decide, pending

FIXTURE = Path(__file__).parent / "fixtures" / "local_mcp.py"


def alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = wintypes.DWORD()
        try:
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        # Linux orphan zombies are dead even before the host's init reaps them.
        stat = Path(f"/proc/{pid}/stat")
        return not (stat.exists() and stat.read_text().split(")", 1)[1].split()[0] == "Z")
    except ProcessLookupError:
        return False


#: How long the product itself may take to reap a hung local server, from `mcp` 2.3.0's
#: `mcp/client/stdio.py` (`_stop_server_process`), which `local_tools.connect` uses: it lets
#: the writer flush for 0.5 s (`_WRITER_FLUSH_TIMEOUT`), closes stdin and waits 2.0 s for the
#: server to exit by itself (`PROCESS_TERMINATION_TIMEOUT`), sends SIGTERM to the group and
#: waits 2.0 s before SIGKILL (`FORCE_KILL_TIMEOUT`), then waits 2.0 s for the death to be seen
#: (`_KILL_REAP_TIMEOUT`): 0.5 + 2.0 + 2.0 + 2.0 = 6.5 s. Windows kills hard at once. The check
#: request waits for that shutdown before it answers (a hung server was seen dead 6 ms after
#: the 502), so on a green run the wait below ends at once; the bound only decides how long a
#: failing run waits. Twice the product's worst case, 13 s, leaves room for a loaded runner.
SHUTDOWN_WORST_CASE = 0.5 + 2.0 + 2.0 + 2.0
REAPED_WITHIN = 2 * SHUTDOWN_WORST_CASE


def assert_stopped(record: Path, bound: float = REAPED_WITHIN) -> None:
    pids = [row["pid"] for row in events(record) if "pid" in row]
    deadline = time.perf_counter() + bound
    while any(alive(pid) for pid in pids) and time.perf_counter() < deadline:
        time.sleep(0.03)
    survivors = [pid for pid in pids if alive(pid)]
    # Clean up only fixture processes whose PIDs this test recorded, even on failure. One that
    # exits between the check and the kill is gone, which is what the cleanup wants: Linux
    # raises ProcessLookupError, Windows PermissionError or OSError for an exited pid. The
    # assertion below still names every process that outlived the bound.
    for pid in survivors:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
    assert not survivors, (
        f"Local fixture processes survived shutdown, {bound:.1f} s after it ended: {survivors}"
    )


@pytest.mark.parametrize("gone", [ProcessLookupError, PermissionError, OSError])
def test_cleanup_kill_of_a_process_that_just_exited_still_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gone: type[OSError]
) -> None:
    """`assert_stopped` saw a pid alive, and it exited before the kill: the kill error must
    not hide the assertion that names the pid (weekly rehearsal, 2026-11-16)."""
    record = tmp_path / "events.jsonl"
    record.write_text(json.dumps({"event": "start", "pid": 4242}) + "\n", encoding="utf-8")

    def exited(pid: int, sig: int) -> None:
        raise gone(3, "No such process")

    monkeypatch.setattr(sys.modules[__name__], "alive", lambda pid: True)
    monkeypatch.setattr(os, "kill", exited)
    with pytest.raises(AssertionError, match="4242"):
        assert_stopped(record, bound=0.1)


def test_a_process_that_is_already_gone_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tmp_path / "events.jsonl"
    record.write_text(json.dumps({"event": "start", "pid": 4242}) + "\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "alive", lambda pid: False)
    assert_stopped(record, bound=0.1)


def configuration(record: Path, mode: str = "normal") -> dict[str, Any]:
    return {
        "name": "Local echo",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(FIXTURE.resolve()), str(record), mode, "literal spaces & $()"],
        "environment": {"FIXTURE_SECRET": "local-private-value"},
    }


def events(record: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]


def setup(world: World, record: Path, mode: str = "normal") -> tuple[Browser, str, str]:
    world.settings.account_kind = "windows_service" if os.name == "nt" else "systemd"
    owner = world.browser()
    owner.sign_in("operator")
    created = owner.post("/api/tools/servers", json=configuration(record, mode))
    assert created.status_code == 201, created.text
    server = created.json()["id"]
    chat = owner.new_chat()
    selected = owner.patch(f"/api/chats/{chat}", json={"settings": {"toolServers": [server]}})
    assert selected.status_code == 200, selected.text
    world.gateway.mode = "tools"
    return owner, chat, server


def test_local_servers_require_launcher_account(world: World, tmp_path: Path) -> None:
    owner = world.browser()
    owner.sign_in("operator")
    record = tmp_path / "events.jsonl"
    result = owner.post("/api/tools/servers", json=configuration(record))
    assert result.status_code == 409, result.text
    assert "account" in result.text
    assert not record.exists()
    listed = owner.get("/api/tools/servers").json()
    assert listed["localProcesses"]["available"] is False
    assert listed["localProcesses"]["reason"]


def test_local_discovery_approval_and_environment(
    world: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EUGENE_PLEXUS_APP_ADMIN_TOKEN", "ambient-eugene-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-provider-secret")
    monkeypatch.setenv("PYTHONPATH", "ambient-python-path")
    record = tmp_path / "events.jsonl"
    owner, chat, server = setup(world, record)
    listed = owner.get("/api/tools/servers").json()
    assert "local-private-value" not in json.dumps(listed)
    assert listed["servers"][0]["environmentKeys"] == ["FIXTURE_SECRET"]
    assert not record.exists()  # Saving a connection does not start code.
    checked = owner.post(f"/api/tools/servers/{server}/check")
    assert checked.status_code == 200, checked.text
    assert checked.json()["tools"][0]["name"] == "echo"
    assert_stopped(record)
    owner.post(f"/api/chats/{chat}/messages", json={"content": "Use local echo"})
    message = pending(owner, chat)
    before = events(record)
    assert all(row["event"] == "start" for row in before)
    for row in before:
        assert row["args"] == ["literal spaces & $()"]
        assert row["env"]["FIXTURE_SECRET"] == "local-private-value"
        assert not any(key.startswith("EUGENE_PLEXUS_") for key in row["env"])
        assert "OPENAI_API_KEY" not in row["env"]
        assert "PYTHONPATH" not in row["env"]
        assert Path(row["cwd"]).is_relative_to(world.data / "tools")
        assert Path(row["env"]["HOME"]) == Path(row["cwd"])
    assert decide(owner, chat, message, True).status_code == 204
    done = owner.wait_answer(chat)
    assert done["status"] == "done", done
    assert done["toolRounds"][0]["calls"][0]["status"] == "done"
    assert [row for row in events(record) if row["event"] == "call"] == [
        {"event": "call", "text": "hello"}
    ]
    assert "local-private-value" not in json.dumps(world.gateway.requests)
    assert_stopped(record)


def test_members_cannot_list_select_or_launch_local_servers(world: World, tmp_path: Path) -> None:
    record = tmp_path / "events.jsonl"
    owner, _, server = setup(world, record)
    ada = world.browser()
    ada.sign_in("p-ada")
    assert ada.get("/api/tools/servers").json()["servers"] == []
    assert ada.post("/api/tools/servers", json=configuration(record)).status_code == 403
    assert ada.post(f"/api/tools/servers/{server}/check").status_code == 403
    chat = ada.new_chat()
    result = ada.patch(f"/api/chats/{chat}", json={"settings": {"toolServers": [server]}})
    assert result.status_code == 400, result.text
    assert not record.exists()
    assert owner.delete(f"/api/tools/servers/{server}").status_code == 204


@pytest.mark.parametrize("action", ["decline", "remove", "stop", "account-lost"])
def test_local_call_does_not_run_without_permission(
    world: World,
    tmp_path: Path,
    action: str,
) -> None:
    record = tmp_path / "events.jsonl"
    owner, chat, server = setup(world, record)
    owner.post(f"/api/chats/{chat}/messages", json={"content": "Use local echo"})
    message = pending(owner, chat)
    if action == "remove":
        owner.delete(f"/api/tools/servers/{server}")
    if action == "account-lost":
        world.settings.account_kind = None
    if action == "stop":
        assert owner.post(f"/api/chats/{chat}/stop").status_code == 204
    else:
        assert decide(owner, chat, message, action != "decline").status_code == 204
    done = owner.wait_answer(chat)
    assert all(row["event"] == "start" for row in events(record))
    assert done["toolRounds"][0]["calls"][0]["status"] in {"declined", "cancelled"}
    assert_stopped(record)


def test_stop_during_local_call_keeps_uncertainty(world: World, tmp_path: Path) -> None:
    record = tmp_path / "events.jsonl"
    owner, chat, _ = setup(world, record, "slow")
    owner.post(f"/api/chats/{chat}/messages", json={"content": "Use local echo"})
    message = pending(owner, chat)
    decide(owner, chat, message, True)
    deadline = time.perf_counter() + 10
    while not any(row["event"] == "call" for row in events(record)):
        assert time.perf_counter() < deadline
        time.sleep(0.03)
    assert owner.post(f"/api/chats/{chat}/stop").status_code == 204
    done = owner.wait_answer(chat)
    assert done["status"] == "stopped", done
    assert done["toolRounds"][0]["calls"][0]["status"] == "uncertain"
    assert_stopped(record)


@pytest.mark.parametrize("mode", ["exit", "missing"])
def test_failed_start_is_explained_without_private_stderr(
    world: World,
    tmp_path: Path,
    mode: str,
) -> None:
    world.settings.account_kind = "systemd"
    owner = world.browser()
    owner.sign_in("operator")
    record = tmp_path / "events.jsonl"
    body = configuration(record, "exit")
    if mode == "missing":
        body["command"] = str(tmp_path / "missing.exe")
    created = owner.post("/api/tools/servers", json=body)
    assert created.status_code == 201, created.text
    server = created.json()["id"]
    checked = owner.post(f"/api/tools/servers/{server}/check")
    assert checked.status_code == 502, checked.text
    assert "fixture-private-error" not in checked.text
    assert "Check" in checked.text
    if mode == "missing":
        assert "not found" in checked.text
    else:
        assert_stopped(record)


def test_process_limit_recovers_after_stopping_an_answer(world: World, tmp_path: Path) -> None:
    record = tmp_path / "events.jsonl"
    owner, first, server = setup(world, record)
    chats = [first]
    try:
        for index in range(4):
            if index:
                chat = owner.new_chat()
                owner.patch(f"/api/chats/{chat}", json={"settings": {"toolServers": [server]}})
                chats.append(chat)
            owner.post(f"/api/chats/{chats[-1]}/messages", json={"content": "Wait for approval"})
            pending(owner, chats[-1])
        refused = owner.post(f"/api/tools/servers/{server}/check")
        assert refused.status_code == 502 and "Four local servers" in refused.text
        assert owner.post(f"/api/chats/{first}/stop").status_code == 204
        assert owner.post(f"/api/tools/servers/{server}/check").status_code == 200
    finally:
        for chat in chats:
            owner.post(f"/api/chats/{chat}/stop")
    assert_stopped(record)


def test_hung_discovery_is_bounded_and_process_is_reaped(
    world: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from eugene_plexus_workbench import tools

    original = tools.Client

    def client(*args: Any, **kwargs: Any) -> Any:
        kwargs["read_timeout_seconds"] = 0.5
        return original(*args, **kwargs)

    monkeypatch.setattr(tools, "Client", client)
    record = tmp_path / "events.jsonl"
    owner, _, server = setup(world, record, "hang")
    started = time.perf_counter()
    response = owner.post(f"/api/tools/servers/{server}/check")
    assert response.status_code == 502, response.text
    # Modern discovery has its own ten-second probe before legacy fallback.
    assert time.perf_counter() - started < 20
    assert_stopped(record)


def test_graceful_server_exit_leaves_no_child_processes(world: World, tmp_path: Path) -> None:
    record = tmp_path / "events.jsonl"
    owner, _, server = setup(world, record, "child")
    checked = owner.post(f"/api/tools/servers/{server}/check")
    assert checked.status_code == 200, checked.text
    assert any(row["event"] == "child" for row in events(record))
    assert_stopped(record)


@pytest.mark.parametrize(
    "field,value",
    [
        ("command", "python"),
        ("command", "relative/server.exe"),
        ("args", ["contains\x00nul"]),
        ("environment", {"EUGENE_PLEXUS_APP_ACCOUNT_KIND": "systemd"}),
    ],
)
def test_invalid_process_configuration(
    world: World, tmp_path: Path, field: str, value: Any
) -> None:
    world.settings.account_kind = "systemd"
    owner = world.browser()
    owner.sign_in("operator")
    body = configuration(tmp_path / "events.jsonl")
    body[field] = value
    result = owner.post("/api/tools/servers", json=body)
    assert result.status_code == 422, result.text
