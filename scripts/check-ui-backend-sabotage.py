"""G5 regressions and actual Chrome CSP fail when guards are removed in copies."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    (
        "signature decision race",
        "answers.py",
        'if future is None or (step.get("held") != shown and not declined):',
        'if step.get("held") != shown or future is None or future.done():',
        "tests/test_signed_calls.py::test_decision_during_site_poll_is_consumed_not_replaced",
        "AssertionError",
    ),
    (
        "signature HTTP poll race",
        "answers.py",
        'if future is None or (step.get("held") != shown and not declined):',
        'if step.get("held") != shown or future is None or future.done():',
        "tests/test_signed_calls.py::test_decision_while_http_poll_is_in_flight[decline]",
        "the answer did not finish",
    ),
    (
        "changed signing request",
        "answers.py",
        'if future is None or (step.get("held") != shown and not declined):',
        'if future is None or (step.get("held") != shown and not future.done()):',
        "tests/test_signed_calls.py::test_changed_held_request_needs_its_own_approval[approve-id]",
        "signing",
    ),
    (
        "pending signature projection",
        "agui.py",
        '{"pending", "running", "signing"}',
        '{"pending", "running"}',
        "tests/test_agui_lifecycle.py::test_native_signing_does_not_emit_a_premature_tool_result",
        "did not sign",
    ),
]
BROWSER = (
    "browser network CSP",
    "mcp_sandbox.py",
    "\"connect-src 'none'\"",
    '"connect-src *"',
    "tests/test_ui_browser.py::test_interactive_content_in_chrome",
    "fetch: false",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--labels", nargs="+", help="Run only the named mutations.")
    args = parser.parse_args()
    cases = [BROWSER] if args.browser else CASES
    if args.labels:
        unknown = set(args.labels) - {case[0] for case in cases}
        if unknown:
            parser.error("Unknown mutation labels: " + ", ".join(sorted(unknown)))
        cases = [case for case in cases if case[0] in args.labels]
    if args.browser and not os.getenv("WORKBENCH_PLAYWRIGHT"):
        raise SystemExit("Set WORKBENCH_PLAYWRIGHT for Chrome.")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "src").rglob("*.py")}
    try:
        with tempfile.TemporaryDirectory(prefix="workbench-ui-backend-") as directory:
            target = Path(directory)
            for folder in ("src", "tests", "web/scripts"):
                shutil.copytree(
                    ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__")
                )
            shutil.copy2(ROOT / "pyproject.toml", target / "pyproject.toml")
            env = {
                k: v for k, v in os.environ.items() if not k.upper().startswith("EUGENE_PLEXUS_")
            }
            env["PYTHONPATH"] = str(target / "src")
            env["PYTHONIOENCODING"] = "utf-8"
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import pathlib,eugene_plexus_workbench as p; "
                    "assert pathlib.Path(p.__file__).resolve().is_relative_to("
                    "pathlib.Path('src').resolve())",
                ],
                cwd=target,
                env=env,
                check=True,
                capture_output=True,
            )
            for label, module, anchor, replacement, test, expected in cases:
                path = target / "src/eugene_plexus_workbench" / module
                original = path.read_text(encoding="utf-8")
                if original.count(anchor) != 1:
                    raise RuntimeError("Ambiguous anchor: " + label)
                try:
                    path.write_text(
                        original.replace(anchor, replacement), encoding="utf-8", newline="\n"
                    )
                    result = subprocess.run(
                        [sys.executable, "-m", "pytest", "-q", "--tb=short", test],
                        cwd=target,
                        env=env,
                        text=True,
                        encoding="utf-8",
                        capture_output=True,
                        timeout=180,
                    )
                    output = result.stdout + result.stderr
                    if (
                        result.returncode != 1
                        or expected not in output
                        or "ERROR at setup" in output
                    ):
                        raise RuntimeError(f"Missed {label}:\n{output}")
                    print("PASS caught " + label, flush=True)
                finally:
                    path.write_text(original, encoding="utf-8", newline="\n")
    finally:
        assert all(
            hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in before.items()
        ), "Working source changed"


if __name__ == "__main__":
    main()
