"""G3 sensitivity: break one guard in a source copy; require a named test failure."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = "test_real_form_approval_atomic_update_restart_and_isolation[True]"
CASES = [
    (
        "event context",
        "a2ui_state.py",
        'if encoded(context, 16 * 1024) != encoded(action["context"], 16 * 1024):',
        "if False:",
        MAIN,
        "forged context accepted",
    ),
    (
        "form input type",
        "a2ui_state.py",
        "or not valid_type",
        "or False",
        MAIN,
        "wrong input type accepted",
    ),
    (
        "completion updates",
        "interaction_store.py",
        "for update in updates or []:",
        "for update in []:",
        MAIN,
        "action update was not committed",
    ),
    (
        "data model opt-in",
        "a2ui.py",
        'if surface.view["sendDataModel"]:',
        "if True:",
        "test_incremental_merges_deletions_and_no_data_disclosure",
        "unrequested data model was disclosed",
    ),
    (
        "model projection",
        "a2ui.py",
        "return Result(result, text, updates)",
        "return Result(result, encoded(result, 64 * 1024), updates)",
        MAIN,
        "UI-only result leaked to model",
    ),
    (
        "discard invalid update plan",
        "a2ui.py",
        "except Exception:\n                        updates = ()",
        "except Exception:\n                        pass",
        "test_invalid_response_rolls_back_whole_batch_but_effect_completes[publication]",
        "action never reached completed",
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
    verified = subprocess.run(
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
    if verified.returncode:
        raise RuntimeError("Copy import failed: " + verified.stderr)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            *["tests/test_a2ui.py::" + t for t in dict.fromkeys(tests)],
        ],
        cwd=copy,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def main() -> None:
    before = fingerprint()
    try:
        with tempfile.TemporaryDirectory(prefix="workbench-a2ui-") as directory:
            copy = Path(directory)
            for folder in ("src", "tests"):
                shutil.copytree(
                    ROOT / folder,
                    copy / folder,
                    ignore=shutil.ignore_patterns("__pycache__", "static"),
                )
            shutil.copy2(ROOT / "pyproject.toml", copy / "pyproject.toml")
            control = run(copy, [case[4] for case in CASES])
            if control.returncode:
                raise RuntimeError("Control failed:\n" + control.stdout + control.stderr)
            print("PASS unmodified control", flush=True)
            for label, module, anchor, replacement, test, failure in CASES:
                path = copy / "src" / "eugene_plexus_workbench" / module
                original = path.read_text(encoding="utf-8")
                if original.count(anchor) != 1:
                    raise RuntimeError("Ambiguous anchor: " + label)
                try:
                    path.write_text(original.replace(anchor, replacement), encoding="utf-8")
                    broken = run(copy, [test])
                    if (
                        broken.returncode != 1
                        or failure not in broken.stdout
                        or "ERROR at setup" in broken.stdout
                    ):
                        raise RuntimeError(f"Missed {label}:\n{broken.stdout}{broken.stderr}")
                    print("PASS caught " + label, flush=True)
                finally:
                    path.write_text(original, encoding="utf-8")
    finally:
        if before != fingerprint():
            raise RuntimeError("Working source changed during sabotage")


if __name__ == "__main__":
    main()
