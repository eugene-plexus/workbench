"""Prove the C5/C6 guards are observed; restore exact working bytes, never Git.

Run alone, after the ordinary test suite. Each mutation must make its
behavioral test fail; syntax/import errors do not count as a caught defect.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "eugene_plexus_workbench"
CASES = [
    (
        "a folder grant leaks to another person",
        "folders.py",
        '    return person is not None and grant["subject"] == person.sub',
        "    return True",
        "tests/test_folders.py::test_grants_belong_to_the_named_person_and_read_only_is_enforced",
    ),
    (
        "revoking a folder does nothing",
        "folders.py",
        "            await self.store.delete_folder_grant(grant_id)",
        "            return",
        "tests/test_folders.py::test_pending_writes_cannot_outlive_permission_or_version[remove]",
    ),
    (
        "an edit overwrites changed content",
        "folder_io.py",
        "                if digest != before:",
        "                if False:",
        "tests/test_folders.py::test_pending_writes_cannot_outlive_permission_or_version[changed]",
    ),
    (
        "a hard link exposes an outside file",
        "folder_io.py",
        "    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:",
        "    if not stat.S_ISREG(info.st_mode):",
        "tests/test_folders.py::test_hard_links_and_create_over_existing_are_refused",
    ),
    (
        "a replaced folder inherits the old grant",
        "folder_io.py",
        "        if expected is not None and identity(folder.fd) != expected:",
        "        if False:",
        "tests/test_folders.py::test_directory_links_and_replaced_roots_are_refused",
    ),
    (
        "another person approves",
        "tools_api.py",
        "    await _own_chat(request, person, chat_id)",
        "    # sabotage: no ownership check",
        "test_discover_approve_continue_and_reuse_history",
    ),
    (
        "declined call runs",
        "answers.py",
        "                    if allowed:",
        "                    if True:",
        "test_no_execution_without_current_approval[decline]",
    ),
    (
        "removed connection runs",
        "tools.py",
        '        if call["serverId"] not in {s["id"] for s in await self.store.tool_servers()}:',
        "        if False:",
        "test_no_execution_without_current_approval[remove]",
    ),
    (
        "interrupted action looks successful",
        "store.py",
        '                    status="uncertain",',
        '                    status="done",',
        "test_restart_keeps_uncertainty_and_cancels_pending",
    ),
    (
        "local process starts without an app account",
        "local_tools.py",
        '    if settings.account_kind in {"windows_service", "systemd"}:',
        "    if True:",
        "tests/test_local_tools.py::test_local_servers_require_launcher_account",
    ),
    (
        "members gain access to local processes",
        "tools.py",
        '    return server.get("transport", "http") == "http" or bool(person and person.is_owner)',
        "    return True",
        "tests/test_local_tools.py::test_members_cannot_list_select_or_launch_local_servers",
    ),
    (
        "a local process inherits app credentials",
        "local_tools.py",
        '    env = {"PATH": path, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", **extra}',
        '    env = {**os.environ, "PATH": path, "PYTHONUNBUFFERED": "1", '
        '"PYTHONUTF8": "1", **extra}',
        "tests/test_local_tools.py::test_local_discovery_approval_and_environment",
    ),
]

if sys.platform.startswith("linux"):
    CASES.append(
        (
            "a moved directory escapes the file boundary",
            "folder_linux.py",
            "        _restrict(self.fd, writable)",
            "        return",
            "tests/test_folders.py::test_moved_parent_cannot_read_or_create_outside_the_grant",
        )
    )


def node(test: str) -> str:
    return test if test.startswith("tests/") else f"tests/test_tools.py::{test}"


def run(tests: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *tests],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def main() -> int:
    selected = [node(case[4]) for case in CASES]
    baseline = run(selected)
    if baseline.returncode:
        print(baseline.stdout + baseline.stderr)
        raise SystemExit("Baseline failed; no files changed.")
    print("Baseline: all selected checks pass.", flush=True)
    backup = Path(tempfile.mkdtemp(prefix="workbench-c5-sabotage-"))
    print(f"Exact backups: {backup}", flush=True)
    caught = 0
    for label, filename, before, after, test in CASES:
        path = SOURCE / filename
        original = path.read_bytes()
        text = original.decode("utf-8")
        if text.count(before) != 1:
            raise SystemExit(f"{filename}: mutation anchor changed; update the instrument.")
        (backup / filename).write_bytes(original)
        try:
            path.write_bytes(text.replace(before, after).encode("utf-8"))
            result = run([node(test)])
            output = result.stdout + result.stderr
            passed = result.returncode == 1 and "FAILED tests/" in output.replace("\\", "/")
            passed = passed and "ERROR collecting" not in output
            print(f"{'CAUGHT' if passed else 'ESCAPED'}: {label}", flush=True)
            if not passed:
                print(output)
            caught += passed
        finally:
            path.write_bytes(original)
            assert path.read_bytes() == original
    restored = run(selected)
    if restored.returncode:
        print(restored.stdout + restored.stderr)
        raise SystemExit("Restored baseline failed.")
    print(f"{caught}/{len(CASES)} caught; restored baseline passes.")
    return 0 if caught == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
