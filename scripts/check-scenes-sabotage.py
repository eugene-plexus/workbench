"""Prove the working scenes' checks are observed (workbench.md §6.1).

Each case breaks one thing in the scene gate, the working scene, a scene
file, or the server's scene policy, and its named check must fail. Files are
restored from exact bytes kept beside the run, never from Git. Syntax and
import errors do not count as a caught defect.

The browser cases run only with WORKBENCH_PLAYWRIGHT set (as for
`tests/test_scenes_browser.py`); each rebuilds the page before and after.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
NPX = "npx.cmd" if os.name == "nt" else "npx"
NPM = "npm.cmd" if os.name == "nt" else "npm"
SCENE_TEST = "src/components/WorkingScene.test.tsx"
GATE_TEST = "src/scenes.test.ts"
POLICY_TEST = "tests/test_page.py::test_a_working_scene_may_run_its_own_style_and_nothing_else"
BROWSER_TEST = "tests/test_scenes_browser.py"

# (label, file, before, after, kind, check): kind is vitest, pytest or browser.
CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "Eugene shows at once, flashing on a fast answer",
        "web/src/components/WorkingScene.tsx",
        "setTimeout(() => setShown(true), SCENE_DELAY_MS)",
        "setTimeout(() => setShown(true), SCENE_DELAY_MS * 0)",
        "vitest",
        f"{SCENE_TEST}::shows only the plain line",
    ),
    (
        "Eugene works while the person is asked to approve",
        "web/src/components/MessageView.tsx",
        '<WorkingScene active={!(progress?.stage === "tool" && progress.phase === "approval")}>',
        "<WorkingScene active={true}>",
        "vitest",
        f"{SCENE_TEST}::steps away while the person approves",
    ),
    (
        "the live progress line is replaced when Eugene steps away",
        "web/src/components/WorkingScene.tsx",
        "  const scene = active && shown ? SCENES[index] : undefined;\n",
        "  const scene = active && shown ? SCENES[index] : undefined;\n"
        "  if (!active) return <>{children}</>;\n",
        "vitest",
        f"{SCENE_TEST}::steps away while the person approves",
    ),
    (
        "reduced motion is ignored",
        "web/src/components/WorkingScene.tsx",
        "  const reduced = useReducedMotion();",
        "  const reduced = useReducedMotion() && false;",
        "vitest",
        f"{SCENE_TEST}::still working pose",
    ),
    (
        "a screen reader hears the scene",
        "web/src/components/WorkingScene.tsx",
        '<div aria-hidden="true" className="scene-in shrink-0" data-testid="working-scene">',
        '<div className="scene-in shrink-0" data-testid="working-scene">',
        "vitest",
        f"{SCENE_TEST}::shows only the plain line",
    ),
    (
        "a long wait never moves to the next scene",
        "web/src/components/WorkingScene.tsx",
        "setIndex((i) => (i + 1) % SCENES.length)",
        "setIndex((i) => i)",
        "vitest",
        f"{SCENE_TEST}::moves to the next scene",
    ),
    (
        "the gate lets a jumping loop through",
        "web/src/sceneGate.ts",
        "if (show(first) !== show(last)) {",
        "if (false) {",
        "vitest",
        f"{GATE_TEST}::0% and 100% that differ",
    ),
    (
        "the gate misses a script the parser cannot see",
        "web/src/sceneGate.ts",
        r'  if (/<\s*script\b/i.test(text)) problems.push("it has a <script>");',
        "",
        "vitest",
        f"{GATE_TEST}::a script hidden from the parser",
    ),
    (
        "the gate accepts a reduced-motion rule an id outranks",
        "web/src/sceneGate.ts",
        r"if (/^none\s*!important$/.test(",
        "if (/^none/.test(",
        "vitest",
        f"{GATE_TEST}::a reduced-motion rule an id outranks",
    ),
    (
        "the gate lets a file over its budget through",
        "web/src/sceneGate.ts",
        "  if (bytes > SCENE_BUDGET_BYTES) {",
        "  if (false) {",
        "vitest",
        f"{GATE_TEST}::over the budget",
    ),
    (
        "the gate misses a transform attribute the animation would replace",
        "web/src/sceneGate.ts",
        '} else if (element.hasAttribute("transform")) {',
        "} else if (false) {",
        "vitest",
        f"{GATE_TEST}::transform attribute",
    ),
    (
        "the gate lets a link to another file through",
        "web/src/sceneGate.ts",
        'if (attr.localName === "href" && !attr.value.startsWith("#")) {',
        "if (false) {",
        "vitest",
        f"{GATE_TEST}::a link to another file",
    ),
    (
        "a scene ships without its reduced-motion rule",
        "web/public/scenes/eugene-measuring.svg",
        "    @media (prefers-reduced-motion: reduce) { * { animation: none !important; } }\n",
        "",
        "vitest",
        f"{GATE_TEST}::eugene-measuring.svg passes the gate",
    ),
    (
        "a scene file is not in the list",
        "web/src/lib/scenes.ts",
        '  { file: "eugene-measuring.svg", phrase: "Measuring twice…" },\n',
        "",
        "vitest",
        f"{GATE_TEST}::are exactly the listed scenes",
    ),
    (
        "a scene is served with the page's policy",
        "src/eugene_plexus_workbench/web.py",
        '                    headers["Content-Security-Policy"] = SCENE_CSP',
        "                    pass",
        "pytest",
        POLICY_TEST,
    ),
    (
        "every SVG gets the scene policy",
        "src/eugene_plexus_workbench/web.py",
        'if path.startswith("scenes/") and path.endswith(".svg"):',
        'if path.endswith(".svg"):',
        "pytest",
        POLICY_TEST,
    ),
]

BROWSER_CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "Chrome: a scene served with the page's policy stands still",
        "src/eugene_plexus_workbench/web.py",
        '                    headers["Content-Security-Policy"] = SCENE_CSP',
        "                    pass",
        "browser",
        BROWSER_TEST,
    ),
    (
        "Chrome: a scene with a painted background",
        "web/public/scenes/eugene-measuring.svg",
        "  </style>\n",
        '  </style>\n  <rect width="320" height="320" fill="#141619"/>\n',
        "browser",
        BROWSER_TEST,
    ),
    (
        "Chrome: Eugene shows at once",
        "web/src/components/WorkingScene.tsx",
        "setTimeout(() => setShown(true), SCENE_DELAY_MS)",
        "setTimeout(() => setShown(true), SCENE_DELAY_MS * 0)",
        "browser",
        BROWSER_TEST,
    ),
    (
        "Chrome: reduced motion is ignored in the page",
        "web/src/components/WorkingScene.tsx",
        "  const reduced = useReducedMotion();",
        "  const reduced = useReducedMotion() && false;",
        "browser",
        BROWSER_TEST,
    ),
]

if os.getenv("WORKBENCH_PLAYWRIGHT"):
    CASES += BROWSER_CASES

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}


def run(kind: str, check: str) -> tuple[bool, str]:
    """(passed, output) for one check."""
    if kind == "vitest":
        file, _, name = check.partition("::")
        command = [NPX, "vitest", "run", file, *(["-t", name] if name else [])]
        result = subprocess.run(
            command, cwd=WEB, env=ENV, capture_output=True, text=True, encoding="utf-8"
        )
        output = result.stdout + result.stderr
        if not re.search(r"Tests\s+(\d+ failed \| )?[1-9]\d* (passed|failed)", output):
            raise SystemExit(f"{check} matched no test; update the instrument.\n{output}")
    else:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", check],
            cwd=ROOT,
            env=ENV,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=400,
        )
    return result.returncode == 0, result.stdout + result.stderr


def build() -> None:
    done = subprocess.run(
        [NPM, "run", "build"], cwd=WEB, env=ENV, capture_output=True, text=True, encoding="utf-8"
    )
    if done.returncode:
        raise SystemExit(f"The page did not build:\n{done.stdout}{done.stderr}")


def broken_build(output: str) -> bool:
    """A mutation that stops the code compiling proves nothing."""
    markers = ("SyntaxError", "Transform failed", "ERROR collecting", "error TS", "ImportError")
    return any(m in output for m in markers)


def main() -> int:
    checks = sorted({(kind, check) for *_, kind, check in CASES})
    if any(kind == "browser" for kind, _ in checks):
        build()
    for kind, check in checks:
        passed, output = run(kind, check)
        if not passed:
            print(output)
            raise SystemExit(f"Baseline failed ({check}); no files changed.")
    print(f"Baseline: {len(checks)} checks pass.", flush=True)
    backup = Path(tempfile.mkdtemp(prefix="workbench-scenes-sabotage-"))
    print(f"Exact backups: {backup}", flush=True)
    caught = 0
    for index, (label, name, before, after, kind, check) in enumerate(CASES):
        path = ROOT / name
        original = path.read_bytes()
        text = original.decode("utf-8")
        if text.count(before) != 1:
            raise SystemExit(f"{name}: the anchor for '{label}' changed; update the instrument.")
        (backup / f"{index}-{path.name}").write_bytes(original)
        try:
            path.write_bytes(text.replace(before, after).encode("utf-8"))
            if kind == "browser" and name.startswith("web/"):
                build()
            passed, output = run(kind, check)
            ok = not passed and not broken_build(output)
            print(f"{'CAUGHT' if ok else 'ESCAPED'}: {label}", flush=True)
            if not ok:
                print(output[-3000:])
            caught += ok
        finally:
            path.write_bytes(original)
            assert path.read_bytes() == original
            if kind == "browser" and name.startswith("web/"):
                build()
    for kind, check in checks:
        passed, output = run(kind, check)
        if not passed:
            print(output)
            raise SystemExit(f"Restored baseline failed ({check}).")
    print(f"{caught}/{len(CASES)} caught; restored baseline passes.")
    return 0 if caught == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
