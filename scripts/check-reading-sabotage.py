"""Prove the reading check's looks are observed (workbench-reading.md 13.1).

Each case breaks one rule of the reading stylesheet, rebuilds the page and
runs `tests/test_reading_browser.py` in Chrome; the run must fail, and on the
named check. The stylesheet is restored from exact bytes kept beside the run,
never from Git, and the page is rebuilt from it at the end.

Needs WORKBENCH_PLAYWRIGHT (as the browser checks do).
Usage: python scripts/check-reading-sabotage.py [--label TEXT ...]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
STYLES = WEB / "src" / "styles.css"
NPM = "npm.cmd" if os.name == "nt" else "npm"
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

# (label, before, after, the check that must fail)
CASES: list[tuple[str, str, str, str]] = [
    (
        "an answer back at 16 px",
        "  font-size: var(--text-read);\n  line-height: var(--text-read--line-height);\n"
        "  color: var(--foreground-read);",
        "  font-size: 1rem;\n  line-height: var(--text-read--line-height);\n"
        "  color: var(--foreground-read);",
        "an answer reads at 17 px",
    ),
    (
        "lines as long as the frame",
        "  --measure: 33.88em;",
        "  --measure: 60em;",
        "60 to 85 characters a line",
    ),
    (
        "an h2 at the body's size",
        ".answer h2 {\n  font-size: 1.25em;",
        ".answer h2 {\n  font-size: 1em;",
        "an h2 is at least 1.2 times the text",
    ),
    (
        "headings in the body's colour",
        "  --heading: #e3c2a8;",
        "  --heading: var(--foreground);",
        "headings carry their own colour",
    ),
    (
        "bold in the body's colour",
        ".answer :is(strong, b) {\n  color: var(--foreground-strong);",
        ".answer :is(strong, b) {\n  color: inherit;",
        "bold is brighter than the text",
    ),
    (
        "inline code in the body's colour",
        "  color: var(--accent-right-text);\n  font-size: 0.88em;",
        "  color: inherit;\n  font-size: 0.88em;",
        "inline code is a warm mark",
    ),
    (
        "bullets in the body's colour",
        ".answer > ul > li::marker {\n  color: var(--accent-right);",
        ".answer > ul > li::marker {\n  color: inherit;",
        "first-level bullets are marked in colour",
    ),
    (
        "a rule drawn in the text colour",
        "  border: 0;\n  border-top: 1px solid var(--rule);",
        "  border: 0;\n  border-top: 1px solid currentColor;",
        "a rule is a hairline, not a bright line",
    ),
    (
        "the Copy bar floats above its code again",
        ".answer pre {\n  margin: 0;",
        ".answer pre {\n  margin: 0.6em 0 0;",
        "the Copy bar is joined to its code",
    ),
    (
        "a code block as wide as its longest line",
        ".answer .code-block {\n  overflow: hidden;",
        ".answer .code-block {\n  width: max-content;\n  overflow: hidden;",
        "nothing is wider than the screen",
    ),
    (
        "muted text too faint to read",
        "  --muted: #b3b9bf;",
        "  --muted: #5c6066;",
        "every text on the page is at least 4.5:1",
    ),
    (
        "an answer in the old page's colours (body and links only)",
        ".answer > ul > li::marker {\n  color: var(--accent-right);",
        ".answer > ul > li::marker {\n  color: inherit;\n}\n"
        ".answer *:not(a, pre, pre *) {\n  color: inherit !important;",
        "an answer's prose carries at least five colours",
    ),
]


def build() -> None:
    done = subprocess.run(
        [NPM, "run", "build"],
        cwd=WEB,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if done.returncode != 0:
        raise SystemExit(f"the build failed, so this case proves nothing:\n{done.stdout[-1500:]}")


def reading() -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("EUGENE_PLEXUS_")}
    done = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "pytest",
            "-q",
            "-s",
            "-p",
            "no:cacheprovider",
            "tests/test_reading_browser.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        env=env,
    )
    return done.returncode, done.stdout + done.stderr


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    if not os.getenv("WORKBENCH_PLAYWRIGHT"):
        print("set WORKBENCH_PLAYWRIGHT to the playwright package, as for the browser checks")
        return 2
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", action="append", help="run only cases whose label has this")
    args = parser.parse_args()
    chosen = [c for c in CASES if not args.label or any(part in c[0] for part in args.label)]
    original = STYLES.read_bytes()
    text = original.decode("utf-8")
    missing = [label for label, before, _after, _check in chosen if text.count(before) != 1]
    if missing:
        for label in missing:
            print(f"[ANCHOR ] {label}: its text is not in styles.css exactly once")
        return 1
    copy = Path(tempfile.mkdtemp(prefix="reading-sabotage-")) / "styles.css"
    shutil.copyfile(STYLES, copy)
    build()
    code, out = reading()
    print(f"[baseline] {'PASS' if code == 0 else 'FAIL'}", flush=True)
    if code != 0:
        print(out[-3000:])
        return 1
    caught = escaped = 0
    try:
        for label, before, after, check in chosen:
            STYLES.write_bytes(text.replace(before, after, 1).encode("utf-8"))
            try:
                build()
                code, out = reading()
            finally:
                shutil.copyfile(copy, STYLES)  # restore FROM THE COPY
            named = any(line.startswith("FAIL") and check in line for line in out.splitlines())
            if code != 0 and named:
                print(f"[caught ] {label}  ({check})", flush=True)
                caught += 1
            else:
                verdict = "failed" if named else "did not fail"
                print(f"[ESCAPED] {label}  (exit {code}; '{check}' {verdict})", flush=True)
                escaped += 1
    finally:
        shutil.copyfile(copy, STYLES)
        assert STYLES.read_bytes() == original, "styles.css differs from its copy"
        build()
    code, out = reading()
    print(f"\n{caught} caught, {escaped} escaped, {len(chosen)} sabotages")
    print(f"[restored] {'PASS' if code == 0 else 'FAIL'}")
    return 0 if escaped == 0 and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
