"""G1 acceptance sensitivity: mutate copies only, require named HTTP-test failures."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE = Path("src/eugene_plexus_workbench/interaction_store.py")
TEST = "tests/test_interactions.py::"
CASES = [
    (
        "terminal action replay",
        'if action["status"] != "proposed":',
        'if False and action["status"] != "proposed":',
        "test_a_terminal_decision_never_dispatches_again",
        "A terminal action was dispatched again",
    ),
    (
        "revision check removed",
        'and action["revision"] == surface["revision"]',
        "and True",
        "test_revision_binding_deletion_and_unknown_version",
        "action never reached cancelled",
    ),
    (
        "post-authorization session check removed",
        'and session["access_expires_at"] > now',
        "and True",
        "test_authorization_is_checked_after_wait_before_effect[lease]",
        "lease did not prevent external dispatch",
    ),
    (
        "owner redaction removed",
        "visible = selected(db, chat, via) - hidden(tree)",
        "visible = selected(db, chat, via)",
        "test_owner_cannot_read_surfaces_or_results_of_hidden_job_site_answers",
        'assert view["surfaces"] == view["actions"] == []',
    ),
]


def fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted((ROOT / "src").rglob("*.py")):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run(copy: Path, tests: list[str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("EUGENE_PLEXUS_")}
    env["PYTHONPATH"] = str(copy / "src")
    verify = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pathlib,eugene_plexus_workbench as p; "
            "assert pathlib.Path(p.__file__).resolve().is_relative_to("
            "pathlib.Path('src').resolve())",
        ],
        cwd=copy,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if verify.returncode:
        raise RuntimeError("The isolated source copy did not import: " + verify.stderr)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", *[TEST + t for t in tests]],
        cwd=copy,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def main() -> None:
    before = fingerprint()
    try:
        with tempfile.TemporaryDirectory(prefix="workbench-interactions-") as directory:
            copy = Path(directory)
            shutil.copytree(
                ROOT / "src", copy / "src", ignore=shutil.ignore_patterns("__pycache__", "static")
            )
            shutil.copytree(
                ROOT / "tests", copy / "tests", ignore=shutil.ignore_patterns("__pycache__")
            )
            shutil.copy2(ROOT / "pyproject.toml", copy / "pyproject.toml")
            control = run(copy, [case[3] for case in CASES])
            if control.returncode:
                raise RuntimeError("Unmodified control failed:\n" + control.stdout + control.stderr)
            print("PASS unmodified control", flush=True)
            path = copy / MODULE
            original = path.read_text(encoding="utf-8")
            for label, anchor, replacement, test, failure in CASES:
                if original.count(anchor) != 1:
                    raise RuntimeError(f"Ambiguous sabotage anchor: {label}")
                path.write_text(original.replace(anchor, replacement), encoding="utf-8")
                broken = run(copy, [test])
                if (
                    broken.returncode != 1
                    or failure not in broken.stdout
                    or "ERROR at setup" in broken.stdout
                ):
                    raise RuntimeError(
                        f"Sabotage was not caught as intended: {label}\n"
                        f"{broken.stdout}{broken.stderr}"
                    )
                print(f"PASS caught {label}", flush=True)
    finally:
        if fingerprint() != before:
            raise RuntimeError("The working source changed during the sabotage run")


if __name__ == "__main__":
    main()
