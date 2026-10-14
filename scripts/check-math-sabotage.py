"""Prove the formula and footnote checks are observed (rich-content RC1).

Each case breaks one rule in the code RC1 added or changed, and its named check
must fail. Files are restored from exact bytes kept beside the run, never from
Git. Syntax and import errors do not count as a caught defect.

The browser cases run only with WORKBENCH_PLAYWRIGHT set (as for
`tests/test_rich_math_browser.py`); each rebuilds the page before and after.
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
PARSER = "web/src/lib/markdownMath.ts"
MARKUP = "web/src/lib/mathMarkup.ts"
CLIENT = "web/src/lib/mathClient.ts"
TYPESET = "web/src/lib/mathTypeset.ts"
FONTS = "web/src/lib/katexFonts.ts"
PLUGINS = "web/src/lib/markdownPlugins.ts"
MARKDOWN = "web/src/components/Markdown.tsx"
STYLES = "web/src/styles.css"
PARSER_TEST = "src/lib/markdownMath.test.ts"
MARKUP_TEST = "src/lib/mathMarkup.test.ts"
CLIENT_TEST = "src/lib/mathClient.test.ts"
FONTS_TEST = "src/lib/katexFonts.test.ts"
MATH_TEST = "src/components/MarkdownMath.test.tsx"
NOTES_TEST = "src/components/MarkdownFootnotes.test.tsx"
BROWSER_TEST = "tests/test_rich_math_browser.py"

# (label, file, before, after, kind, check): kind is vitest or browser.
CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "a dollar after a digit opens a formula",
        PARSER,
        "previous: (code) => code !== DOLLAR && !asciiDigit(code),",
        "previous: (code) => code !== DOLLAR,",
        "vitest",
        f"{PARSER_TEST}::money and other dollars stay text",
    ),
    (
        "a dollar before a space opens a formula",
        PARSER,
        "    if (open === 1 && markdownLineEndingOrSpace(code)) return nok(code);\n",
        "",
        "vitest",
        f"{PARSER_TEST}::money and other dollars stay text",
    ),
    (
        "a dollar after a space closes a formula",
        PARSER,
        "(open === 2 || (!markdownLineEndingOrSpace(before) && !asciiDigit(code)))",
        "(open === 2 || !asciiDigit(code))",
        "vitest",
        f"{PARSER_TEST}::money and other dollars stay text",
    ),
    (
        "a dollar before a digit closes a formula",
        PARSER,
        "(open === 2 || (!markdownLineEndingOrSpace(before) && !asciiDigit(code)))",
        "(open === 2 || !markdownLineEndingOrSpace(before))",
        "vitest",
        f"{PARSER_TEST}::money and other dollars stay text",
    ),
    (
        "an escaped dollar closes a formula",
        PARSER,
        "    return code === BACKSLASH ? escaped : data;",
        "    return data;",
        "vitest",
        f"{PARSER_TEST}::keeps an escaped dollar inside a formula",
    ),
    (
        "a close is looked for without end",
        PARSER,
        "    if (++length > (open === 1 ? DOLLAR_REACH : FORMULA_REACH)) return fail(code);",
        "    ++length;",
        "vitest",
        f"{PARSER_TEST}::does not look past its reach for a close",
    ),
    (
        "money before a formula swallows it",
        PARSER,
        "    if (open === 1) return fail(code);\n",
        "",
        "vitest",
        f"{PARSER_TEST}::money before a formula stays money",
    ),
    (
        "a parse looks for closes without a budget",
        PARSER,
        "  return search.spent > SEARCH_BUDGET || (",
        "  return false || (",
        "vitest",
        f"{PARSER_TEST}::that never closes parses quickly",
    ),
    (
        "a block ends a paragraph",
        PARSER,
        "      if (interrupting()) return nok(code);\n",
        "",
        "vitest",
        f"{PARSER_TEST}::a paragraph's own $$ pair is the formula",
    ),
    (
        "a line ending in the delimiter does not close a block",
        PARSER,
        "      if (code === fence.close[0]) {",
        "      if (code === -999) {",
        "vitest",
        f"{PARSER_TEST}::closes a block on a line that ends with its delimiter",
    ),
    (
        "an unclosed block is not marked open",
        PARSER,
        '      if ((this.data.mathFences ?? 0) < 2) code.properties.dataFence = "open";\n',
        "",
        "vitest",
        f"{PARSER_TEST}::marks a block that has not closed as open",
    ),
    (
        "the reader accepts a mismatched close",
        MARKUP,
        "open.length < 2 || top.tag !== tag) return null;",
        "open.length < 2) return null;",
        "vitest",
        f"{MARKUP_TEST}::cannot read",
    ),
    (
        "an element off the list is drawn",
        MARKUP,
        "  if (!mathml && !allowed) throw new Refused(`its layout has a <${node.tag}> element`);\n",
        "",
        "vitest",
        f"{MARKUP_TEST}::refuses",
    ),
    (
        "any attribute is kept",
        MARKUP,
        "mathml ? (MATHML_ATTRIBUTES.has(name) ? name : null) : allowed?.[name];",
        "name;",
        "vitest",
        f"{MARKUP_TEST}::drops attributes off the list",
    ),
    (
        "any class is kept",
        MARKUP,
        "const classes = value.split(/\\s+/).filter((c) => CLASS_NAME.test(c));",
        "const classes = value.split(/\\s+/);",
        "vitest",
        f"{MARKUP_TEST}::drops a class that is not a plain name",
    ),
    (
        "any style value is kept",
        MARKUP,
        "const STYLE_VALUE = /^(?:[\\w.#%+\\-*/\\s,]|calc\\(|rgba?\\(|\\))+$/i;",
        "const STYLE_VALUE = /^.+$/;",
        "vitest",
        f"{MARKUP_TEST}::drops a style that could fetch",
    ),
    (
        "any position is kept",
        MARKUP,
        '    if (name === "position" && value !== "relative" && value !== "absolute") continue;\n',
        "",
        "vitest",
        f"{MARKUP_TEST}::drops a style that could fetch",
    ),
    (
        "a formula may move text a long way",
        MARKUP,
        "const PULL_LIMIT_EM = 25;",
        "const PULL_LIMIT_EM = 2500;",
        "vitest",
        f"{MARKUP_TEST}::refuses a formula that would pull the line across the page",
    ),
    (
        "small pulls have no total",
        MARKUP,
        "    if (pulled.em > PULLS_LIMIT_EM) {",
        "    if (pulled.em > Infinity) {",
        "vitest",
        f"{MARKUP_TEST}::refuses many small pulls that add up to a long one",
    ),
    (
        "offsets up and down are held to the pull limit",
        MARKUP,
        'const PULLS = new Set(["margin", "marginLeft", "marginRight", "left"]);',
        'const PULLS = new Set(["margin", "marginLeft", "marginRight", "left", "top"]);',
        "vitest",
        f"{MARKUP_TEST}::draws what real formulas pull and raise",
    ),
    (
        "a worker's failure is final",
        CLIENT,
        '  if (formula?.state === "failed" && formula.again) {',
        '  if (formula?.state === "failed" && formula.again && !formula) {',
        "vitest",
        f"{MATH_TEST}::tries a formula again when it is shown again",
    ),
    (
        "laid-out formulas are kept by count only",
        CLIENT,
        "    if (kept.size <= KEPT && keptBytes <= KEPT_BYTES) break;",
        "    if (kept.size <= KEPT) break;",
        "vitest",
        f"{MATH_TEST}::keeps no more laid-out markup than its budget",
    ),
    (
        "a formula's macros are shared",
        TYPESET,
        "      maxExpand: 1000,\n",
        "      maxExpand: 1000,\n"
        "      macros: ((globalThis as { m?: Record<string, string> }).m ??= {}),\n",
        "vitest",
        f"{MARKUP_TEST}::keeps no macro between formulas",
    ),
    (
        "a formula of any length is laid out",
        TYPESET,
        "  if (tex.length > MAX_TEX) {",
        "  if (tex.length < 0) {",
        "vitest",
        f"{MARKUP_TEST}::says a formula is too long before laying it out",
    ),
    (
        "trust is on",
        TYPESET,
        "      trust: false,",
        "      trust: true,",
        "vitest",
        f"{MARKUP_TEST}::makes no link, picture, class, style or id of its own",
    ),
    (
        "a formula that takes too long keeps its worker",
        CLIENT,
        "    running = null;\n    end();\n    job?.resolve(",
        "    running = null;\n    job?.resolve(",
        "vitest",
        f"{CLIENT_TEST}::stops a formula that takes too long",
    ),
    (
        "a formula nobody shows is laid out",
        CLIENT,
        "    while (job?.wanted && !job.wanted()) {",
        "    while (job?.wanted && job.wanted() === null) {",
        "vitest",
        f"{CLIENT_TEST}::skips a formula nobody still shows",
    ),
    (
        "a formula already asked for is asked for again",
        CLIENT,
        "  if (kept.has(key) || asked.has(key)) return;",
        "  if (kept.has(key)) return;",
        "vitest",
        f"{MATH_TEST}::lays out a formula shown twice",
    ),
    (
        "a formula already laid out is laid out again",
        CLIENT,
        "  if (kept.has(key) || asked.has(key)) return;",
        "  if (asked.has(key)) return;",
        "vitest",
        f"{MATH_TEST}::lays out a formula shown twice",
    ),
    (
        "an open block is drawn",
        MARKDOWN,
        '  if (language === "math" && !open) {',
        '  if (language === "math") {',
        "vitest",
        f"{MATH_TEST}::shows a block that has not closed as its source",
    ),
    (
        "a formula is code",
        MARKDOWN,
        "    if (kind) return <MathFormula",
        "    if (kind && false) return <MathFormula",
        "vitest",
        f"{MATH_TEST}::draws inline and display formulas",
    ),
    (
        "KaTeX's three formats all ship",
        FONTS,
        '  return css.replace(FALLBACK, "");',
        "  return css;",
        "vitest",
        f"{FONTS_TEST}::names each face once, as woff2",
    ),
    (
        "every answer shares footnote ids",
        MARKDOWN,
        "      remarkRehypeOptions: { clobberPrefix: scope },",
        "      remarkRehypeOptions: {},",
        "vitest",
        f"{NOTES_TEST}::gives each answer its own ids",
    ),
    (
        "every answer shares the notes' heading id",
        PLUGINS,
        "    if (props.id === LABEL) props.id = prefix + LABEL;\n",
        "",
        "vitest",
        f"{NOTES_TEST}::gives each answer its own ids",
    ),
    (
        "a note cannot take focus",
        MARKDOWN,
        "tabIndex={id ? -1 : undefined}",
        "",
        "vitest",
        f"{NOTES_TEST}::moves focus to the note and back",
    ),
    (
        "a note is found anywhere on the page",
        MARKDOWN,
        '[...region.querySelectorAll<HTMLElement>("[id]")].find((el) => el.id === id);',
        "document.getElementById(id);",
        "vitest",
        f"{NOTES_TEST}::finds the note inside its own answer",
    ),
    (
        "a model's own link to an id moves focus",
        MARKDOWN,
        '    if ((isRef || isBack) && href?.startsWith("#")) {',
        '    if (href?.startsWith("#")) {',
        "vitest",
        f"{NOTES_TEST}::keeps a model's own link to an id inert",
    ),
]

BROWSER_CASES: list[tuple[str, str, str, str, str, str]] = [
    # Both overflow lines go: with overflow-y left hidden, overflow-x's
    # visible computes to auto and the box still scrolls.
    (
        "a wide display formula widens the page",
        STYLES,
        "  max-width: 100%;\n  overflow-x: auto;\n  overflow-y: hidden;\n  padding: 0.2em 0;",
        "  max-width: 100%;\n  padding: 0.2em 0;",
        "browser",
        BROWSER_TEST,
    ),
    (
        "a formula is never stopped",
        CLIENT,
        "export const FORMULA_MS = 1_500;",
        "export const FORMULA_MS = 600_000;",
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
        # vitest reads -t as a regular expression: a name's `$$` is two anchors.
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
            timeout=600,
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
    checks = sorted({(kind, check) for *_, kind, check in cases})
    if any(kind == "browser" for kind, _ in checks):
        build()
    for kind, check in checks:
        passed, output = run(kind, check)
        if not passed:
            print(output)
            raise SystemExit(f"Baseline failed ({check}); no files changed.")
    print(f"Baseline: {len(checks)} checks pass.", flush=True)
    backup = Path(tempfile.mkdtemp(prefix="workbench-math-sabotage-"))
    print(f"Exact backups: {backup}", flush=True)
    caught = 0
    for index, (label, name, before, after, kind, check) in enumerate(cases):
        path = ROOT / name
        original = path.read_bytes()
        text = original.decode("utf-8")
        if text.count(before) != 1:
            raise SystemExit(f"{name}: the anchor for '{label}' changed; update the instrument.")
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
