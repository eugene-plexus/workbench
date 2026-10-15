"""G4: remove guards in temporary copies and require specific assertion failures."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = [
    (
        "request binding",
        "agui_store.py",
        'if run["requestHash"] != digest or run["agentId"] != agent_id:',
        "if False:",
        "tests/test_agui.py::test_native_reference_client_reconnect_continuation_and_restart",
        "409",
    ),
    (
        "chat ownership",
        "agui_api.py",
        "await _own_chat(request, person, chat)",
        "pass",
        "tests/test_agui.py::test_identity_input_and_configuration_refusals",
        "404",
    ),
    (
        "agent access grant",
        "agui_store.py",
        'if person.sub not in result["subjects"]:',
        "if False:",
        "tests/test_agui.py::test_identity_input_and_configuration_refusals",
        "403",
    ),
    (
        "state delta",
        "agui_protocol.py",
        'self.state = patch(self.state, event["delta"])',
        "pass",
        "tests/test_agui.py::test_external_interleaved_tools_state_and_no_local_execution",
        "State deltas were not applied",
    ),
    (
        "terminal ordering",
        "agui.py",
        "if terminal is not None:",
        "if False:",
        "tests/test_agui.py::test_external_failure_never_retries[after-terminal-uncertain]",
        "Invalid remote stream reported success",
    ),
    (
        "checkpoint cancellation",
        "agui.py",
        "await asyncio.shield(commit)",
        "await commit",
        "tests/test_agui_protocol.py::test_cancellation_finishes_the_committed_projection",
        "Committed event was lost from the projection",
    ),
    (
        "restart recovery",
        "app.py",
        "await store.agui.recover()",
        "pass",
        "tests/test_agui_lifecycle.py::test_process_kill_after_external_dispatch_never_replays",
        "Restart did not mark the dispatched run uncertain",
    ),
]


def fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted((ROOT / "src").rglob("*.py")):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run(directory: Path, tests: list[str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("EUGENE_PLEXUS_")}
    env["PYTHONPATH"] = str(directory / "src")
    # Never mistake the editable install for the sabotaged source.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import pathlib,eugene_plexus_workbench as p; "
            "assert pathlib.Path(p.__file__).resolve().is_relative_to("
            "pathlib.Path('src').resolve())",
        ],
        cwd=directory,
        env=env,
        check=True,
        capture_output=True,
        timeout=30,
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", *dict.fromkeys(tests)],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", nargs="+", choices=[case[0] for case in CASES])
    args = parser.parse_args()
    cases = [case for case in CASES if not args.labels or case[0] in args.labels]
    before = fingerprint()
    try:
        with tempfile.TemporaryDirectory(prefix="workbench-agui-") as directory:
            target = Path(directory)
            for folder in ("src", "tests"):
                shutil.copytree(
                    ROOT / folder,
                    target / folder,
                    ignore=shutil.ignore_patterns("__pycache__", "static"),
                )
            shutil.copy2(ROOT / "pyproject.toml", target / "pyproject.toml")
            control = run(target, [case[4] for case in cases])
            if control.returncode:
                raise RuntimeError("Unmodified control failed:\n" + control.stdout + control.stderr)
            print("PASS unmodified control", flush=True)
            for label, module, anchor, replacement, test, failure in cases:
                path = target / "src" / "eugene_plexus_workbench" / module
                original = path.read_text(encoding="utf-8")
                if original.count(anchor) != 1:
                    raise RuntimeError("Ambiguous sabotage anchor: " + label)
                try:
                    path.write_text(
                        original.replace(anchor, replacement), encoding="utf-8", newline="\n"
                    )
                    broken = run(target, [test])
                    if (
                        broken.returncode != 1
                        or failure not in broken.stdout
                        or "ERROR at setup" in broken.stdout
                    ):
                        raise RuntimeError(f"Missed {label}:\n{broken.stdout}{broken.stderr}")
                    print("PASS caught " + label, flush=True)
                finally:
                    path.write_text(original, encoding="utf-8", newline="\n")
    finally:
        if fingerprint() != before:
            raise RuntimeError("Working source changed during sabotage")


if __name__ == "__main__":
    main()
