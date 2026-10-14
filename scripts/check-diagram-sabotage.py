"""Prove the diagram checks are observed (rich-content RC2).

Each case breaks one rule in the code RC2 added or changed, and its named check
must fail. Files are restored from exact bytes kept beside the run, never from
Git. Syntax and import errors do not count as a caught defect.

The browser cases run only with WORKBENCH_PLAYWRIGHT set (as for
`tests/test_rich_diagram_browser.py`); each rebuilds the page before and after.
An argument keeps only the cases whose label contains it.
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
MARKUP = "web/src/lib/diagramMarkup.ts"
RENDER = "web/src/lib/diagramRender.ts"
DOM = "web/src/lib/diagramDom.ts"
CLIENT = "web/src/lib/diagramClient.ts"
QUEUE = "web/src/lib/workerQueue.ts"
MARKDOWN = "web/src/components/Markdown.tsx"
DIAGRAM = "web/src/components/MermaidDiagram.tsx"
STYLES = "web/src/styles.css"
MARKUP_TEST = "src/lib/diagramMarkup.test.ts"
RENDER_TEST = "src/lib/diagramRender.test.ts"
DOM_TEST = "src/lib/diagramDom.test.ts"
DIAGRAM_TEST = "src/components/MarkdownDiagram.test.tsx"
BROWSER_TEST = "tests/test_rich_diagram_browser.py"

# (label, file, before, after, kind, check): kind is vitest or browser.
CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "a script element is drawn",
        MARKUP,
        '    "switch",\n',
        '    "switch",\n    "script",\n',
        "vitest",
        f"{MARKUP_TEST}::fails a drawing with a <script>",
    ),
    (
        "HTML in a drawing is kept",
        MARKUP,
        'const DROPPED = new Set(["foreignobject", "image"]);',
        'const DROPPED = new Set(["image"]);\nELEMENTS.set("foreignobject", "foreignObject");',
        "vitest",
        f"{MARKUP_TEST}::drops HTML and pictures from elsewhere",
    ),
    (
        "a picture from an address is kept",
        MARKUP,
        'const DROPPED = new Set(["foreignobject", "image"]);',
        'const DROPPED = new Set(["foreignobject"]);\nELEMENTS.set("image", "image");',
        "vitest",
        f"{MARKUP_TEST}::drops HTML and pictures from elsewhere",
    ),
    (
        "a value may point outside the drawing",
        MARKUP,
        r"const LOCAL_URL = /url\(\s*#([\w-]{1,200})\s*\)/g;",
        r"const LOCAL_URL = /url\([^)]*\)/g;",
        "vitest",
        f'{MARKUP_TEST}::a value "url(https://evil.example)" is false',
    ),
    (
        "an attribute off the list is kept",
        MARKUP,
        "    const attribute = ATTRIBUTES.get(rawName.toLowerCase());",
        "    const attribute = ATTRIBUTES.get(rawName.toLowerCase()) ?? rawName;",
        "vitest",
        f"{MARKUP_TEST}::drops handlers, outside addresses and what is not on the list",
    ),
    (
        "a use may point anywhere",
        MARKUP,
        '      if (name !== "use" || !/^#[\\w-]{1,200}$/.test(value)) continue;',
        '      if (name !== "use") continue;',
        "vitest",
        f"{MARKUP_TEST}::drops handlers, outside addresses and what is not on the list",
    ),
    (
        "a link loses its place",
        MARKUP,
        # As first built: a link unwrapped, and with it the node's transform.
        "  const name = ELEMENTS.get(",
        '  if (lower === "a") return inner();\n  const name = ELEMENTS.get(',
        "vitest",
        f"{MARKUP_TEST}::keeps a link's drawing and place, not its address",
    ),
    (
        "an xlink: prefix is written without its namespace",
        MARKUP,
        'ATTRIBUTES.set("xlink:href", "href");',
        'ATTRIBUTES.set("xlink:href", "xlink:href");',
        "vitest",
        f"{MARKUP_TEST}::drops handlers, outside addresses and what is not on the list",
    ),
    (
        "one attribute is written twice",
        MARKUP,
        "    if (!attribute || written.has(attribute)) continue;",
        "    if (!attribute) continue;",
        "vitest",
        f"{MARKUP_TEST}::writes one attribute once, however it is spelled",
    ),
    (
        "an open quote is kept",
        MARKUP,
        """    if ((value.match(/'/g)?.length ?? 0) % 2""",
        """    if ((value.match(/'/g)?.length ?? 0) % 2 && Math.random() > 2""",
        "vitest",
        f"{MARKUP_TEST}::drops a style whose quote the split left open",
    ),
    (
        "an ended worker's error fails the next job",
        QUEUE,
        "      event.preventDefault();\n      if (worker !== mine) return;\n",
        "      event.preventDefault();\n",
        "vitest",
        f"{DIAGRAM_TEST}::lets nothing an ended worker says reach the next diagram",
    ),
    (
        "text is not escaped",
        MARKUP,
        '      .replace(/</g, "&lt;")\n',
        "",
        "vitest",
        f"{MARKUP_TEST}::escapes text and values",
    ),
    (
        "every style is kept",
        MARKUP,
        "!STYLES.has(name) || ",
        "",
        "vitest",
        f"{MARKUP_TEST}::keeps only the styles a picture draws with",
    ),
    (
        "a drawing of any size is read",
        MARKUP,
        "  if (++reading.count > MAX_ELEMENTS)",
        "  if (++reading.count > Infinity)",
        "vitest",
        f"{MARKUP_TEST}::refuses a drawing with too many elements",
    ),
    (
        "a directive changes the configuration",
        RENDER,
        "  config.secure = [...keys];",
        "  config.secure = [];",
        "vitest",
        f"{RENDER_TEST}::by a directive",
    ),
    (
        "a kind not checked is drawn",
        RENDER,
        "  const kind = KINDS[type];",
        "  const kind = KINDS[type] ?? type;",
        "vitest",
        f"{RENDER_TEST}::refuses a kind of diagram it has not been checked against",
    ),
    (
        "a source of any length reaches Mermaid",
        RENDER,
        "  if (source.length > MAX_SOURCE) {",
        "  if (source.length > Infinity) {",
        "vitest",
        f"{RENDER_TEST}::refuses a source longer than its limit",
    ),
    (
        "Mermaid's stylesheet stays a <style>",
        RENDER,
        "  inlineStyles(root);\n",
        "",
        "vitest",
        f"{RENDER_TEST}::draws a flowchart",
    ),
    (
        "an own style beats an important rule",
        DOM,
        "      if (!ruled?.important || d.important)",
        "      if (true)",
        "vitest",
        f"{DOM_TEST}::is written into each element",
    ),
    (
        "text is measured without its stylesheet",
        DOM,
        "fontRules: sheet.rules.filter(",
        "fontRules: ([] as Rule[]).filter(",
        "vitest",
        f"{DOM_TEST}::measures text in the size its stylesheet gives it",
    ),
    (
        "a group is measured without its children's transforms",
        DOM,
        '          const m = parseTransform(child.getAttribute("transform"));',
        "          const m = IDENTITY;",
        "vitest",
        f"{DOM_TEST}::measures shapes, and a group through",
    ),
    (
        "a diagram is drawn on every render",
        CLIENT,
        "  if (kept.has(key) || asked.has(key)) return;",
        "  if (kept.has(key) && Math.random() > 2) return;",
        "vitest",
        f"{DIAGRAM_TEST}::draws each diagram once while the answer is written",
    ),
    (
        "a colour that is not hex reaches Mermaid",
        CLIENT,
        "    if (HEX.test(value)) palette[variable] = value;",
        "    if (value) palette[variable] = value;",
        "vitest",
        f"{DIAGRAM_TEST}::takes only the page's hex colours",
    ),
    (
        "a diagram is drawn before its block closes",
        MARKDOWN,
        '  if (language === "mermaid" && !open) return',
        '  if (language === "mermaid") return',
        "vitest",
        f"{DIAGRAM_TEST}::leaves a block that has not closed as its code",
    ),
    (
        "a job is never stopped",
        QUEUE,
        "      limits.job,\n",
        "      limits.job * 1000,\n",
        "vitest",
        f"{DIAGRAM_TEST}::stops a diagram that takes longer than 4 s",
    ),
    (
        "a PNG may be any size",
        DIAGRAM,
        "return Math.min(2, PNG_SIDE",
        "return Math.max(2, PNG_SIDE",
        "vitest",
        f"{DIAGRAM_TEST}::makes a PNG twice the diagram's size",
    ),
]

BROWSER_CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "a diagram screens away is drawn at once",
        DIAGRAM,
        '  const [near, setNear] = useState(() => typeof IntersectionObserver === "undefined");',
        "  const [near, setNear] = useState(() => true);",
        "browser",
        BROWSER_TEST,
    ),
    (
        "a diagram is never stopped",
        CLIENT,
        "export const DIAGRAM_MS = 4_000;",
        "export const DIAGRAM_MS = 600_000;",
        "browser",
        BROWSER_TEST,
    ),
    (
        "the larger view does not start at Close",
        DIAGRAM,
        "    close.current?.focus();\n",
        "",
        "browser",
        BROWSER_TEST,
    ),
    (
        "a phone's tools keep their words",
        STYLES,
        "  .diagram-tool-label {\n    position: absolute;\n    width: 1px;\n    height: 1px;\n"
        "    overflow: hidden;\n    clip-path: inset(50%);\n    white-space: nowrap;\n  }",
        "  .diagram-tool-label {\n  }",
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
        # vitest reads -t as a regular expression.
        pattern = re.sub(r"[.*+?^${}()|[\]\\]", r"\\\g<0>", name)
        command = [NPX, "vitest", "run", file, *(["-t", pattern] if name else [])]
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
            timeout=900,
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
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    cases = [case for case in CASES if only.lower() in case[0].lower()]
    for label, name, before, _after, _kind, _check in cases:
        if (ROOT / name).read_text(encoding="utf-8").count(before) != 1:
            raise SystemExit(f"{name}: the anchor for '{label}' changed; update the instrument.")
    checks = sorted({(kind, check) for *_, kind, check in cases})
    if any(kind == "browser" for kind, _ in checks):
        build()
    for kind, check in checks:
        passed, output = run(kind, check)
        if not passed:
            print(output)
            raise SystemExit(f"Baseline failed ({check}); no files changed.")
    print(f"Baseline: {len(checks)} checks pass.", flush=True)
    backup = Path(tempfile.mkdtemp(prefix="workbench-diagram-sabotage-"))
    print(f"Exact backups: {backup}", flush=True)
    caught = 0
    for index, (label, name, before, after, kind, check) in enumerate(cases):
        path = ROOT / name
        original = path.read_bytes()
        text = original.decode("utf-8")
        (backup / f"{index}-{path.name}").write_bytes(original)
        try:
            path.write_bytes(text.replace(before, after).encode("utf-8"))
            if kind == "browser":
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
            if kind == "browser":
                build()
    for kind, check in checks:
        passed, output = run(kind, check)
        if not passed:
            print(output)
            raise SystemExit(f"Restored baseline failed ({check}).")
    print(f"{caught}/{len(cases)} caught; restored baseline passes.")
    return 0 if caught == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
