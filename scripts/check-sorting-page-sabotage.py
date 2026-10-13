"""Prove the result sorter page's checks are observed (MR6b, the page side).

Each case breaks one thing in the page's code, and the test that is named for it
must fail. Files are restored from exact bytes kept beside the run, never from
Git. A mutation that stops the code compiling does not count as a caught defect,
and neither does a test name that runs nothing.

  python scripts/check-sorting-page-sabotage.py              # every unit-test case
  python scripts/check-sorting-page-sabotage.py --anchors    # only check each anchor is unique
  python scripts/check-sorting-page-sabotage.py --python     # also the cases the Python checks catch
  python scripts/check-sorting-page-sabotage.py --browser    # also the Chrome cases (needs WORKBENCH_PLAYWRIGHT)
  python scripts/check-sorting-page-sabotage.py --chrome-only    # only the Chrome cases
  python scripts/check-sorting-page-sabotage.py --label TEXT ...   # only the cases whose label has TEXT
  python scripts/check-sorting-page-sabotage.py --only PATH ...    # only the cases that break a file whose path has PATH

The Chrome cases rebuild the page for each break and run `tests/test_library_browser.py`;
the page is rebuilt from the restored source at the end. Run it with every ambient
EUGENE_PLEXUS_* variable cleared.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SRC = "web/src/"
NPM = "npm.cmd" if os.name == "nt" else "npm"
# Vitest is run with node itself: going through npx costs ten seconds a run on Windows.
VITEST = WEB / "node_modules" / "vitest" / "vitest.mjs"
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

SORTING = SRC + "lib/documentSorting.ts"
MEANING = SRC + "lib/documentMeaning.ts"
SAVED = SRC + "lib/savedDocuments.ts"
TYPES = SRC + "lib/types.ts"
SEARCH = SRC + "components/DocumentsSearch.tsx"
SORT = SRC + "components/DocumentsSort.tsx"
SCREEN = SRC + "components/Documents.tsx"
TRY = SRC + "components/TestQuestion.tsx"

CODES_TABLE = "tests/fixtures/sort_codes.json"
WORDS_TABLE = "tests/fixtures/sort_words.json"
LIBRARY_BROWSER = "tests/test_library_browser.py"
SORTING_AGREES = "tests/test_sorting_page.py"

T_SORTING = "src/lib/documentSorting.test.ts"
T_MEANING = "src/lib/documentMeaning.test.ts"
T_SAVED = "src/lib/savedDocuments.test.ts"
T_SORT = "src/components/DocumentsSort.test.tsx"
T_SEARCH = "src/components/DocumentsSearch.test.tsx"
T_SCREEN = "src/components/Documents.test.tsx"
T_TRY = "src/components/TestQuestion.test.tsx"
T_WORDS = "src/words.test.ts"

# (label, file, before, after, test file, a fragment of the test's name)
CASES: list[tuple[str, str, str, str, str, str]] = [
    # --- how each code looks, and what a person can do about it ------------------------------
    (
        "a sorter that is not running is not a warning",
        SORTING,
        '  not_running: "warn",',
        '  not_running: "info",',
        T_SORTING,
        "is a warning only where something needs somebody",
    ),
    (
        "a sorter too slow for Sort time is a warning",
        SORTING,
        '  over_budget: "info",',
        '  over_budget: "warn",',
        T_SORTING,
        "is a warning only where something needs somebody",
    ),
    (
        "a person who is not the owner is told to ask about Sort time",
        SORTING,
        '  "outside",\n];',
        '  "outside",\n  "over_budget",\n];',
        T_SORTING,
        "tells a person who is not the owner to ask them only where the owner mends it",
    ),
    (
        "the owner is sent to Backends for every code",
        SORTING,
        'return code === "not_running" ? "backends" : null;',
        'return code === "ok" ? null : "backends";',
        T_SORTING,
        "sends the owner to Backends only for a sorter that is not running",
    ),
    (
        "a sorter that was never in play is given a notice",
        SORTING,
        "  sort != null && !sort.quiet;",
        "  sort != null;",
        T_SORTING,
        "a sorter that was never in play is not worth a notice",
    ),
    (
        "a sorter that is chosen but too slow is said to be in use",
        SORTING,
        "  sort.sorting && sort.model != null && sort.label != null;",
        "  sort.model != null && sort.label != null;",
        T_SORTING,
        "is one that is sorting, and not one that is chosen but too slow",
    ),
    (
        "a sorter outside the machines is not a warning",
        SORTING,
        '  if (sorterInUse(sort) && sort.locality !== "local") return "warn";',
        "",
        T_SORTING,
        "is a warning in itself when it runs outside the person's machines",
    ),
    (
        "a sorter that is not in use is a warning for where it runs",
        SORTING,
        '  if (sorterInUse(sort) && sort.locality !== "local") return "warn";',
        '  if (sort.locality !== "local") return "warn";',
        T_SORTING,
        "is not a warning for one that is chosen and not in use",
    ),
    (
        "an outside sorter is not said to be sent the best passages and the question",
        SORTING,
        "The owner of this Workbench allowed the best passages of your documents for each question, and the question, to be sent there.",
        "The owner of this Workbench allowed your documents to be sent there.",
        T_SORTING,
        "says where it runs, and what that means for what is in the person's documents",
    ),
    (
        "the owner's choice is said to be automatic",
        SORTING,
        "  return sort.automatic\n",
        "  return !sort.automatic\n",
        T_SORTING,
        "says whose word it is: automatic, or the owner's",
    ),
    # --- a time and a speed, as the server says them -----------------------------------------
    (
        "a time has a point and a zero it does not need",
        SORTING,
        r'text.replace(/0+$/, "").replace(/\.$/, "")',
        "text",
        T_SORTING,
        "are said as the server says them",
    ),
    (
        "a time under ten seconds is said to the whole second",
        SORTING,
        "const places = seconds >= 10 ? 0 : seconds >= 1 ? 1 : 2;",
        "const places = seconds >= 10 ? 0 : 2;",
        T_SORTING,
        "have no more figures than they need",
    ),
    (
        "a speed over a hundred is not said to two figures",
        SORTING,
        "const step = rate >= 100 ? 10 ** (whole - 2) : 1;",
        "const step = 1;",
        T_SORTING,
        "go to two figures once a speed is over a hundred",
    ),
    # --- what the speed comes to, beside Sort time -------------------------------------------
    (
        "the speed does not say Sort time",
        SORTING,
        " to each answer, within Sort time (${secondsWords(sort.budgetSeconds)}).",
        " to each answer.",
        T_SORTING,
        "is how fast it reads, what a usual sort adds to an answer, and Sort time",
    ),
    (
        "the time a usual sort adds is Sort time",
        SORTING,
        "adds about ${secondsWords(typical.seconds)} to each answer",
        "adds about ${secondsWords(sort.budgetSeconds)} to each answer",
        T_SORTING,
        "is made of the numbers the server sent, and no others",
    ),
    (
        "the speed of a sorter that is not in use is said twice",
        SORTING,
        "if (!sorterInUse(sort) || sort.ratePerSecond == null || typical.seconds == null) return null;",
        "if (sort.ratePerSecond == null || typical.seconds == null) return null;",
        T_SORTING,
        "is not said for a sorter that is not in use",
    ),
    (
        "where a speed came from is said when there is no speed",
        SORTING,
        "  if (sort.ratePerSecond == null) return null;\n",
        "",
        T_SORTING,
        "does not say where a speed came from when there is none",
    ),
    (
        "a quick test is said to be the sorts",
        SORTING,
        'if (sort.rateFrom === "probe") {',
        'if (sort.rateFrom === "sorts") {',
        T_SORTING,
        "says what the speed mostly comes from: quick tests, or the sorts Workbench has made",
    ),
    # --- why passages are not sorted ---------------------------------------------------------
    (
        "a sorter that is being measured says nothing of it",
        SORTING,
        'if (sort.code === "measuring") {',
        'if (sort.code === "never") {',
        T_SORTING,
        "says what is being done while the sorter is measured",
    ),
    (
        "a reason is said for passages that are sorted",
        SORTING,
        "  if (sorterInUse(sort)) return null;\n",
        "",
        T_SORTING,
        "is nothing when the passages are sorted",
    ),
    (
        "a question that was never sorted says why, whatever the sorter was",
        SORTING,
        "  if (!run || run.used || run.quiet) return null;",
        "  if (!run || run.used) return null;",
        T_SORTING,
        "is the server's own sentence, unless the sorter was never in play",
    ),
    (
        "a question that was sorted says why it was not",
        SORTING,
        "  if (!run || run.used || run.quiet) return null;",
        "  if (!run || run.quiet) return null;",
        T_SORTING,
        "is nothing for passages that were sorted, whatever else came with them",
    ),
    (
        "a sort says how many tokens it read as passages",
        SORTING,
        '${count(run.passages, "passage")} in order',
        '${count(run.tokens ?? 0, "passage")} in order',
        T_SORTING,
        "is how many passages it put in order and how long that took, beside Sort time",
    ),
    (
        "a time that was not kept is said as no time at all",
        SORTING,
        'const took = run.seconds != null ? ` in ${secondsWords(run.seconds)}` : "";',
        "const took = ` in ${secondsWords(run.seconds ?? 0)}`;",
        T_SORTING,
        "says no more than it was told",
    ),
    # --- the headline, and where the sorter put a passage ------------------------------------
    (
        "the headline says a sorter when none sorts",
        MEANING,
        "  if (!sorter) return facts.headline;\n",
        "",
        T_MEANING,
        "is the server's own headline when nothing is sorted, for every code",
    ),
    (
        "the headline of a paused search is spliced",
        MEANING,
        "  return lead && facts.headline.startsWith(`${lead}.`)\n",
        "  return lead\n",
        T_MEANING,
        "leaves a headline that is not of the two plain ways as the server said it",
    ),
    (
        "the drawer does not say the sorter",
        MEANING,
        "  return { headline: searchHeadline(facts), more };",
        "  return { headline: facts.headline, more };",
        T_MEANING,
        "is what a chat's drawer says, so the two screens never say it differently",
    ),
    (
        "a place by the sorter is not said",
        MEANING,
        "  if (places.sorter != null) said.push(`the sorter put it ${ordinal(places.sorter)}`);\n",
        "",
        T_MEANING,
        "says where the result sorter put it beside where each way did, never a score",
    ),
    (
        "a passage the sorter was not given is not said so",
        MEANING,
        "  return sorted && places.sorter == null ? `${line} The sorter was not given it.` : line;",
        "  return line;",
        T_MEANING,
        "says a passage the sorter was not given was not, so a late place by one way is explained",
    ),
    (
        "a passage the sorter was given is said not to be",
        MEANING,
        "  return sorted && places.sorter == null ? `${line} The sorter was not given it.` : line;",
        "  return sorted ? `${line} The sorter was not given it.` : line;",
        T_MEANING,
        "says nothing of it for a passage the sorter was given, which has its place",
    ),
    (
        "passages that were not sorted are said not to be given to a sorter",
        MEANING,
        "  return sorted && places.sorter == null ? `${line} The sorter was not given it.` : line;",
        "  return places.sorter == null ? `${line} The sorter was not given it.` : line;",
        T_MEANING,
        "says nothing of a sorter when the passages were not sorted",
    ),
    # --- the notice on the Documents screen --------------------------------------------------
    (
        "the screen does not say the sorter in the headline",
        SEARCH,
        "{searchHeadline(facts)}",
        "{facts.headline}",
        T_SEARCH,
        "says the sorter in the headline while it puts the best passages in order",
    ),
    (
        "the screen has no notice for the sorter",
        SCREEN,
        "              <DocumentsSort sort={data.search.sort} me={me} />\n",
        "",
        T_SCREEN,
        "says the sorter in the headline, and under it which sorter is in use",
    ),
    (
        "the sorter's notice is not told who is reading, so the owner has no link",
        SCREEN,
        "<DocumentsSort sort={data.search.sort} me={me} />",
        "<DocumentsSort sort={data.search.sort} />",
        T_SCREEN,
        "tells the owner where a sorter that stopped is started",
    ),
    (
        "a sorter that was never in play is given a notice on the screen",
        SORT,
        "  if (!hasSortWords(sort)) return null;",
        "  if (!sort) return null;",
        T_SORT,
        "which is never in play and not worth a notice",
    ),
    (
        "a sorter that is chosen but too slow is said to be in use on the screen",
        SORT,
        "  const inUse = sorterInUse(sort);",
        "  const inUse = sort.label != null;",
        T_SORT,
        "names the sorter as in use only when it is sorting",
    ),
    (
        "the notice does not say why passages are not sorted",
        SORT,
        '        {reason && <span data-testid="documents-sort-reason">{reason}</span>}\n',
        "",
        T_SORT,
        "says why, in the server's own sentence, once",
    ),
    (
        "the notice does not say how fast the sorter reads",
        SORT,
        '        {speed && <span data-testid="documents-sort-speed">{speed}</span>}\n',
        "",
        T_SORT,
        "says its speed and what a usual sort adds to an answer, beside Sort time",
    ),
    (
        "the notice is never a warning",
        SORT,
        '  const warn = sortTone(sort) === "warn";',
        "  const warn = false;",
        T_SORT,
        "is a titled notice, a warning only where something needs somebody",
    ),
    (
        "the owner is not linked to Backends",
        SORT,
        '        {step === "backends" && consoleUrl && (',
        "        {false && (",
        T_SORT,
        "names it, and links the owner to Backends, where it is started",
    ),
    (
        "a person who is not the owner is given the link to Backends",
        SORT,
        "  const consoleUrl = me?.owner && me.consoleUrl ? me.consoleUrl : null;",
        "  const consoleUrl = me?.consoleUrl ?? null;",
        T_SORT,
        "gives no link to someone who is not the owner, even where the console's address is known",
    ),
    (
        "the link to Backends has no rel",
        SORT,
        '            rel="noopener noreferrer"\n',
        "",
        T_SORT,
        "names it, and links the owner to Backends, where it is started",
    ),
    (
        "a person who is not the owner is not told to ask the owner",
        SORT,
        "  const asksOwner = me && !me.owner ? sortOwnerWords(sort.code) : null;",
        "  const asksOwner = null;",
        T_SORT,
        "tells a person who is not the owner to ask the owner, and gives them no link",
    ),
    (
        "the notice says a word that is how it works underneath",
        SORT,
        " * said at all.\n",
        " * said at all (a reranker is not said).\n",
        T_WORDS,
        "keeps how documents are read underneath off every screen about them",
    ),
    # --- watching a sorter being measured ----------------------------------------------------
    (
        "a sorter that is being measured is not watched",
        SAVED,
        '  const measuring = page.search.sort?.code === "measuring";',
        "  const measuring = false;",
        T_SAVED,
        "is a result sorter being measured, which is asked about every few seconds",
    ),
    (
        "a sorter that is being measured is not looked at again in seconds on the screen",
        SAVED,
        '  const measuring = page.search.sort?.code === "measuring";',
        "  const measuring = false;",
        T_SCREEN,
        "looks again in seconds while the sorter is measured",
    ),
    # --- Test a question ---------------------------------------------------------------------
    (
        "a question that was not sorted does not say why",
        TRY,
        "            {notSorted && (",
        "            {false && (",
        T_TRY,
        "says why, in the server's words, with the numbers of these passages",
    ),
    (
        "a question that was sorted does not say what the sort did",
        TRY,
        '            {sortedLine && <li data-testid="documents-test-sorted">{sortedLine}</li>}\n',
        "",
        T_TRY,
        "says how many passages it put in order and how long that took, beside Sort time",
    ),
    (
        "a passage is said not to be given to a sorter when none sorted",
        TRY,
        "hit={hit} sorted={sorted} />",
        "hit={hit} sorted={true} />",
        T_TRY,
        "does not say they were sorted, and does not say a passage was not given to a sorter",
    ),
    (
        "a passage is never said not to be given to the sorter",
        TRY,
        "  const sorted = wasSearched && found.sort?.used === true;",
        "  const sorted = false;",
        T_TRY,
        "says where the sorter put each passage, beside where words and meaning put it",
    ),
    (
        "sorting is said for a question that was not searched",
        TRY,
        "  const wasSearched = found !== undefined && SEARCHED.includes(found.status);",
        "  const wasSearched = found !== undefined;",
        T_TRY,
        "says nothing of sorting for a question none of whose ways could run",
    ),
]

# (label, file, before, after, the Python test file, a fragment of the test's name)
PYTHON_CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "the page has no words for a code the server says",
        TYPES,
        '  | "refused"\n  | "unusable"\n',
        '  | "unusable"\n',
        SORTING_AGREES,
        "test_every_code_the_sorter_can_say_is_one_the_page_has_words_for",
    ),
    (
        "the page has no look for a code the server says",
        SORTING,
        '  refused: "warn",\n',
        "",
        SORTING_AGREES,
        "test_every_code_the_sorter_can_say_is_one_the_page_has_words_for",
    ),
    (
        "the page tells a person to ask the owner for a code the server never says",
        SORTING,
        '  "outside",\n];',
        '  "outside",\n  "never_said",\n];',
        SORTING_AGREES,
        "test_the_codes_that_name_the_console_are_codes_the_server_says",
    ),
    (
        "the page does not read how fast the sorter reads",
        TYPES,
        '  ratePerSecond: number | null;\n  rateFrom: "probe" | "sorts" | null;',
        '  rateFrom: "probe" | "sorts" | null;',
        SORTING_AGREES,
        "test_the_header_has_the_fields_the_page_reads_in_each_state_the_sorter_can_be_in",
    ),
    (
        "the page does not read what a usual sort is",
        TYPES,
        "  typical: { passages: number; passageTokens: number; tokens: number; seconds: number | null };\n",
        "",
        SORTING_AGREES,
        "test_the_header_has_the_fields_the_page_reads_in_each_state_the_sorter_can_be_in",
    ),
    (
        "the page does not read the sorter of the screen",
        TYPES,
        "  sort?: SortFacts | null;\n}",
        "}",
        SORTING_AGREES,
        "test_the_header_has_the_fields_the_page_reads_in_each_state_the_sorter_can_be_in",
    ),
    (
        "the page does not read where the sorter put a passage",
        TYPES,
        "  meaning?: number;\n  sorter?: number;\n}",
        "  meaning?: number;\n}",
        SORTING_AGREES,
        "test_a_question_that_was_sorted_has_the_fields_the_page_reads",
    ),
    (
        "the page does not read how long a sort took",
        TYPES,
        "  budgetSeconds: number | null;\n  seconds: number | null;\n}",
        "  budgetSeconds: number | null;\n}",
        SORTING_AGREES,
        "test_a_question_that_was_sorted_has_the_fields_the_page_reads",
    ),
    (
        "the page does not read what sorting came to for an answer",
        TYPES,
        "  sort?: SortRun | null;\n}",
        "}",
        SORTING_AGREES,
        "test_what_a_chat_keeps_of_sorting_has_the_fields_the_page_reads",
    ),
    (
        "the page does not read what sorting came to for a tried question",
        TYPES,
        "  sort?: SortRun;\n",
        "",
        SORTING_AGREES,
        "test_a_question_that_was_sorted_has_the_fields_the_page_reads",
    ),
    (
        "a sentence in the shared table is not the one the server says",
        CODES_TABLE,
        '"not_running": "Not sorted: the sorter Qwen3-Reranker-0.6B-Q8_0 is not running.',
        '"not_running": "Not sorted: the sorter Qwen3-Reranker-0.6B-Q8_0 has stopped.',
        SORTING_AGREES,
        "test_the_sentences_the_pages_tests_use_are_the_ones_the_server_says",
    ),
    (
        "a time in the shared table is not the one the server says",
        WORDS_TABLE,
        '   "6.9 s"\n',
        '   "7 s"\n',
        SORTING_AGREES,
        "test_a_time_and_a_speed_are_said_the_way_the_server_says_them",
    ),
]

# (label, file, before, after, the Chrome check that must fail)
CHROME_CASES: list[tuple[str, str, str, str, str]] = [
    # === what only a real browser sees ===================================================
    (
        "the open screen does not look again in seconds while the sorter is measured",
        SAVED,
        '  const measuring = page.search.sort?.code === "measuring";',
        "  const measuring = false;",
        LIBRARY_BROWSER,
    ),
    # The page is built for each break, so each of these still compiles: an import that is no
    # longer read, or a value that is no longer narrowed, would be a break that proves nothing.
    (
        "the headline does not say the sorter",
        SEARCH,
        "{searchHeadline(facts)}",
        "{facts.headline || searchHeadline(facts)}",
        LIBRARY_BROWSER,
    ),
    (
        "a place by the sorter is not said for a passage",
        MEANING,
        "  if (places.sorter != null) said.push(`the sorter put it ${ordinal(places.sorter)}`);\n",
        "",
        LIBRARY_BROWSER,
    ),
    (
        "the owner is not linked to where a sorter that stopped is started",
        SORT,
        '        {step === "backends" && consoleUrl && (',
        '        {step === "backends" && consoleUrl && me?.consoleUrl === "" && (',
        LIBRARY_BROWSER,
    ),
    (
        "a question that was not sorted does not say why",
        TRY,
        "            {notSorted && (",
        "            {notSorted && notSorted.length < 0 && (",
        LIBRARY_BROWSER,
    ),
    (
        "a sorter that is too slow is said to be in use",
        SORT,
        "  const inUse = sorterInUse(sort);",
        "  const inUse = sorterInUse(sort) || sort.label != null;",
        LIBRARY_BROWSER,
    ),
    (
        "the drawer does not say the sorter",
        MEANING,
        "  return { headline: searchHeadline(facts), more };",
        "  return { headline: facts.headline, more };",
        LIBRARY_BROWSER,
    ),
]

ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("EUGENE_PLEXUS_")},
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUTF8": "1",
    "FORCE_COLOR": "0",
    "NO_COLOR": "1",
}
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def vitest(test: str, name: str) -> tuple[bool, str, int]:
    """(passed, output, how many tests ran and passed) for one named test."""
    result = subprocess.run(
        ["node", str(VITEST), "run", test, "-t", name],
        cwd=WEB,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    output = ANSI.sub("", result.stdout + result.stderr)
    ran = re.search(r"Tests\s+(?:\d+ failed \| )?(\d+) passed", output)
    return result.returncode == 0, output, int(ran.group(1)) if ran else 0


def pytest(test: str, name: str) -> tuple[bool, str, int]:
    """(passed, output, how many tests ran) for the Python tests whose name has `name`."""
    result = subprocess.run(
        [str(PYTHON), "-m", "pytest", "-q", "-p", "no:cacheprovider", test, "-k", name],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    output = ANSI.sub("", result.stdout + result.stderr)
    ran = re.search(r"(\d+) passed", output)
    return result.returncode == 0, output, int(ran.group(1)) if ran else 0


def broken(output: str) -> bool:
    """A mutation that stops the code compiling or importing proves nothing."""
    markers = (
        "Transform failed",
        "SyntaxError",
        "Failed to resolve import",
        "Cannot find module",
        "Unexpected token",
        "ERROR: Expected",
    )
    return any(m in output for m in markers)


def anchors() -> None:
    every = [
        *((label, name, before) for label, name, before, _, _, _ in CASES),
        *((label, name, before) for label, name, before, _, _, _ in PYTHON_CASES),
        *((label, name, before) for label, name, before, _, _ in CHROME_CASES),
    ]
    bad = []
    for label, name, before in every:
        found = (ROOT / name).read_text(encoding="utf-8").count(before)
        if found != 1:
            bad.append(f"{name}: the anchor for '{label}' is in {found} places; fix it.")
    if bad:
        raise SystemExit("\n".join(bad))
    print(
        f"{len(CASES)} anchors, {len(PYTHON_CASES)} for Python and {len(CHROME_CASES)} for "
        f"Chrome, each in one place."
    )


def build() -> tuple[bool, str]:
    result = subprocess.run(
        [NPM, "run", "build"],
        cwd=WEB,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    return result.returncode == 0, ANSI.sub("", result.stdout + result.stderr)


def why(output: str) -> str:
    """The first line a failed Chrome run says, so the log shows what caught each case."""
    said = [
        re.sub(r"^(E\s+)?(AssertionError:\s*)?", "", line.strip()) for line in output.splitlines()
    ]
    found = [
        line[:160] for line in said if line.startswith("FAIL") and not line.startswith("FAILED")
    ]
    found += [
        line[:160]
        for line in said
        if re.search(r"Timeout \d+ms exceeded|\w+Error(?: \[\w+\])?:", line)
        and not line.startswith("FAILED")
    ]
    return " | ".join(found[:2]) if found else "(no failing line found)"


def chrome(test: str) -> tuple[bool, str]:
    result = subprocess.run(
        [str(PYTHON), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", test],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=500,
    )
    return result.returncode == 0, ANSI.sub("", result.stdout + result.stderr)


def swapped(path: Path, original: bytes, before: str, after: str) -> None:
    path.write_bytes(original.decode("utf-8").replace(before, after).encode("utf-8"))


def main() -> int:
    # What a failing test prints may not be in the console's code page.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchors", action="store_true")
    parser.add_argument("--python", action="store_true")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--chrome-only", action="store_true")
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()

    def wanted(label: str, name: str) -> bool:
        return (not args.label or any(t in label for t in args.label)) and (
            not args.only or any(t in name for t in args.only)
        )

    cases = [] if args.chrome_only else [c for c in CASES if wanted(c[0], c[1])]
    python_cases = (
        [c for c in PYTHON_CASES if wanted(c[0], c[1])]
        if args.python and not args.chrome_only
        else []
    )
    chrome_cases = [c for c in CHROME_CASES if wanted(c[0], c[1])]
    anchors()
    if args.anchors:
        return 0

    checks = sorted({(test, name) for _, _, _, _, test, name in cases})
    missing = []
    for test, name in checks:
        passed, output, ran = vitest(test, name)
        if not passed or ran == 0:
            print(output[-1500:])
            missing.append(f"{test}: {name}")
    if missing:
        raise SystemExit(
            "Baseline failed or ran nothing, so no files were changed:\n  " + "\n  ".join(missing)
        )
    py_checks = sorted({(test, name) for _, _, _, _, test, name in python_cases})
    for test, name in py_checks:
        passed, output, ran = pytest(test, name)
        if not passed or ran == 0:
            print(output[-3000:])
            raise SystemExit(f"Baseline failed or ran nothing ({test}: {name}); no files changed.")
    print(
        f"Baseline: {len(checks)} named checks and {len(py_checks)} Python checks pass and run.",
        flush=True,
    )

    backup = Path(tempfile.mkdtemp(prefix="workbench-sorting-page-sabotage-"))
    print(f"Exact backups: {backup}", flush=True)
    caught = 0
    for index, (label, name, before, after, test, check) in enumerate(cases):
        path = ROOT / name
        original = path.read_bytes()
        (backup / f"{index}-{path.name}").write_bytes(original)
        try:
            swapped(path, original, before, after)
            passed, output, _ = vitest(test, check)
            ok = not passed and not broken(output)
            print(f"{'CAUGHT' if ok else 'ESCAPED'}: {label}", flush=True)
            if not ok:
                print(output[-2500:])
            caught += ok
        finally:
            path.write_bytes(original)
            assert path.read_bytes() == original
    total = len(cases)

    for index, (label, name, before, after, test, check) in enumerate(python_cases):
        path = ROOT / name
        original = path.read_bytes()
        (backup / f"p{index}-{path.name}").write_bytes(original)
        try:
            swapped(path, original, before, after)
            passed, output, _ = pytest(test, check)
            ok = not passed
            print(f"{'CAUGHT' if ok else 'ESCAPED'} (Python): {label}", flush=True)
            if not ok:
                print(output[-2500:])
            caught += ok
        finally:
            path.write_bytes(original)
            assert path.read_bytes() == original
    total += len(python_cases)

    if args.browser or args.chrome_only:
        if not os.getenv("WORKBENCH_PLAYWRIGHT"):
            raise SystemExit("--browser needs WORKBENCH_PLAYWRIGHT.")
        # The checks must pass on the page as it is before anything is broken.
        built, output = build()
        if not built:
            print(output[-3000:])
            raise SystemExit("The page does not build as it is; no files changed.")
        for test in sorted({c[4] for c in chrome_cases}):
            passed, output = chrome(test)
            if not passed:
                print(output[-3000:])
                raise SystemExit(f"Chrome baseline failed ({test}); no files changed.")
        print(f"Chrome baseline: {len({c[4] for c in chrome_cases})} checks pass.", flush=True)
        for index, (label, name, before, after, test) in enumerate(chrome_cases):
            path = ROOT / name
            original = path.read_bytes()
            (backup / f"b{index}-{path.name}").write_bytes(original)
            try:
                swapped(path, original, before, after)
                built, output = build()
                if not built:
                    print(f"BROKEN (does not build, proves nothing): {label}", flush=True)
                    print(output[-1500:], flush=True)
                    continue
                passed, output = chrome(test)
                ok = not passed and "PASS:" not in output
                print(f"{'CAUGHT' if ok else 'ESCAPED'} (Chrome): {label}", flush=True)
                print(f"    {why(output)}" if ok else output[-2500:], flush=True)
                caught += ok
            finally:
                path.write_bytes(original)
                assert path.read_bytes() == original
        total += len(chrome_cases)
        built, output = build()
        if not built:
            print(output[-3000:])
            raise SystemExit("The restored page does not build.")

    for test, name in checks:
        passed, output, ran = vitest(test, name)
        if not passed or ran == 0:
            print(output[-3000:])
            raise SystemExit(f"Restored baseline failed ({test}: {name}).")
    print(f"{caught}/{total} caught; restored baseline passes.")
    return 0 if caught == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
