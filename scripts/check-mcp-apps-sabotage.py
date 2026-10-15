"""G2 guard sensitivity on isolated source copies, using real MCP/Workbench HTTP."""

from __future__ import annotations

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
        "model visibility",
        "tools.py",
        'if "model" not in visible:',
        'if False and "model" not in visible:',
        "test_discovery_resources_approval_restart_and_isolation[True-False]",
        "app-only tool leaked to model",
    ),
    (
        "app visibility",
        "mcp_apps.py",
        'if "app" not in mcp_ui.visibility(session.metadata[key]):',
        'if False and "app" not in mcp_ui.visibility(session.metadata[key]):',
        "test_cross_person_resource_target_visibility_schema_and_method_denied",
        "action never reached failed",
    ),
    (
        "resource binding",
        "mcp_apps.py",
        'or params["uri"] != surface.binding["uri"]:',
        "or False:",
        "test_cross_person_resource_target_visibility_schema_and_method_denied",
        "forged resource target reached server",
    ),
    (
        "post-IO resource authorization",
        "mcp_apps.py",
        "await self.checked(person, surface, revision)\n                return result",
        "return result",
        "test_resource_access_rechecked_after_io",
        "revoked resource bytes escaped",
    ),
    (
        "UI-only result isolation",
        "mcp_apps.py",
        "return Result(result, text)",
        "return Result(result, encoded(result, 64 * 1024))",
        "test_discovery_resources_approval_restart_and_isolation[True-False]",
        "UI-only result leaked into model context",
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
            *["tests/test_mcp_apps.py::" + t for t in dict.fromkeys(tests)],
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
        with tempfile.TemporaryDirectory(prefix="workbench-mcp-apps-") as directory:
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
