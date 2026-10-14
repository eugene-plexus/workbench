"""Prove the citation, chart and table checks are observed (rich-content RC4).

Each case breaks one rule in the code RC4 added or changed, and its named check
must fail. Files are restored from exact bytes kept beside the run, never from
Git. Syntax and import errors do not count as a caught defect.

The browser case runs only with WORKBENCH_PLAYWRIGHT set (as for
`tests/test_rich_structured_browser.py`); it rebuilds the page before and after.
An argument keeps only the cases whose label contains it.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The runner is RC3's, unchanged: one instrument, its cases here.
_spec = importlib.util.spec_from_file_location(
    "tool_content_sabotage", ROOT / "scripts" / "check-tool-content-sabotage.py"
)
assert _spec is not None and _spec.loader is not None
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

ANSWERS = "src/eugene_plexus_workbench/answers.py"
CITATIONS = "web/src/lib/citations.ts"
MARKDOWN = "web/src/components/Markdown.tsx"
SPEC = "web/src/lib/chartSpec.ts"
CSV = "web/src/lib/csv.ts"
TABLE = "web/src/components/DataTable.tsx"
VIEW = "web/src/components/ToolContent.tsx"
STYLES = "web/src/styles.css"
BACKEND = "tests/test_citations.py"
CITED = "src/components/MarkdownCitations.test.tsx"
SPEC_TEST = "src/lib/chartSpec.test.ts"
DRAWN = "src/components/Structured.test.tsx"
BROWSER_TEST = "tests/test_rich_structured_browser.py"

CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "a place is the address's first, not the one the gateway named",
        ANSWERS,
        "            if best is None or abs(at - want) < abs(best[0] - want):",
        "            if best is None:",
        "pytest",
        f"{BACKEND}::test_an_address_written_twice_is_cited_where_the_gateway_says",
    ),
    (
        "a place is made up where the text does not hold the address",
        ANSWERS,
        "    if best is None:\n        return\n",
        "    if best is None:\n        best = (want, url)\n",
        "pytest",
        f"{BACKEND}::test_a_searched_answer_says_where_it_cites_each_page_as_the_page_counts",
    ),
    (
        "a place is given to the page in Python's count",
        ANSWERS,
        "[_js_length(message.content[:a]), _js_length(message.content[:b])] for a, b in spans",
        "[a, b] for a, b in spans",
        "pytest",
        f"{BACKEND}::test_a_searched_answer_says_where_it_cites_each_page_as_the_page_counts",
    ),
    (
        "a source's number goes inside the link it follows",
        CITATIONS,
        '              out.push(marker("web", cited.n));',
        '              child.children.push(marker("web", cited.n) as never);',
        "vitest",
        f"{CITED}::puts a web source's number after the link it cites",
    ),
    (
        "a number inside a link becomes a passage's",
        CITATIONS,
        'if (child.type === "text" && !inLink && passages.size)',
        'if (child.type === "text" && passages.size)',
        "vitest",
        f"{CITED}::links a passage's number only for a passage that was sent",
    ),
    (
        "a number no passage has becomes a link",
        CITATIONS,
        "            if (!passages.has(n)) continue;\n",
        "",
        "vitest",
        f"{CITED}::links a passage's number only for a passage that was sent",
    ),
    (
        "a passage's number does not open it",
        CITATIONS,
        "  if (details) details.open = true;\n",
        "",
        "vitest",
        f"{CITED}::links a passage's number only for a passage that was sent",
    ),
    (
        "a citation's number changes the address",
        MARKDOWN,
        "              event.preventDefault();\n              followCitation(",
        "              followCitation(",
        "vitest",
        f"{CITED}::puts a web source's number after the link it cites",
    ),
    (
        "a point's y need not be a number",
        SPEC,
        '      if (typeof py !== "number" || !Number.isFinite(py)) {',
        "      if (false) {",
        "vitest",
        f"{SPEC_TEST}::refuses, saying why",
    ),
    (
        "a CSV writes a spreadsheet's formulas",
        CSV,
        r"const FORMULA = /^\s*[=+\-@]|^[\t\r]/;",
        "const FORMULA = /^$/;",
        "vitest",
        f"{SPEC_TEST}::writes CSV as data",
    ),
    (
        "a table shows every row",
        TABLE,
        "  const shown = sorted.slice(0, SHOWN);",
        "  const shown = sorted;",
        "vitest",
        f"{DRAWN}::sorts a table by a column at a click",
    ),
    (
        "any data of a tool is tried as a chart",
        VIEW,
        "      if (asksToBeDrawn(block.data)) return",
        "      if (true) return",
        "vitest",
        f"{DRAWN}::draws a tool's chart, and leaves other data as the data it is",
    ),
]

BROWSER_CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "two series share one colour",
        STYLES,
        ".chart-s1 {\n  --series: var(--accent-right);",
        ".chart-s1 {\n  --series: var(--accent-left);",
        "browser",
        BROWSER_TEST,
    ),
]

if os.getenv("WORKBENCH_PLAYWRIGHT"):
    CASES += BROWSER_CASES

if __name__ == "__main__":
    runner.CASES = CASES
    sys.exit(runner.main())
