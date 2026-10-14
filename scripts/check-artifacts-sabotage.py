"""Prove the artifact and preview checks are observed (rich-content RC5).

Each case breaks one rule in the code RC5 added or changed, and its named check
must fail. Files are restored from exact bytes kept beside the run, never from
Git. Syntax and import errors do not count as a caught defect.

The browser case runs only with WORKBENCH_PLAYWRIGHT set (as for
`tests/test_rich_artifacts_browser.py`); it rebuilds the page before and after.
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

API = "src/eugene_plexus_workbench/artifacts_api.py"
STORE = "src/eugene_plexus_workbench/store.py"
KEPT = "src/eugene_plexus_workbench/artifacts.py"
WEB = "src/eugene_plexus_workbench/web.py"
PREVIEW = "web/src/components/ArtifactPreview.tsx"
MARKDOWN = "web/src/components/Markdown.tsx"
SVG = "web/src/lib/svgArtifact.ts"
DIGEST = "web/src/lib/artifacts.ts"
RUNTIME = "web/src/preview/runtime.ts"
APP = "src/eugene_plexus_workbench/app.py"
DIALOG = "web/src/components/ArtifactDialog.tsx"
BACKEND = "tests/test_artifacts.py"
SHOWN = "src/components/Artifact.test.tsx"
LIB = "src/lib/artifacts.test.ts"
FRAME = "src/preview/runtime.test.ts"
BROWSER_TEST = "tests/test_rich_artifacts_browser.py"
# Rich content RC6: shown and run in place.
RUNNER = "web/src/lib/previewRunner.ts"
CARD = "web/src/components/Artifact.tsx"
ME = "src/eugene_plexus_workbench/api.py"
RUNS = "src/lib/previewRunner.test.ts"
INLINE = "src/components/ArtifactInline.test.tsx"
INLINE_BROWSER = "tests/test_rich_artifacts_inline_browser.py"
# After RC6 landed: footnotes that scrolled the page, a proxy's script in a preview.
CHAT = "web/src/components/ChatView.tsx"
STYLES = "web/src/styles.css"
CHAT_TEST = "src/components/ChatView.test.tsx"
SHEET = "src/stylesheet.test.ts"

CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "an edit begun before the last revision is put after it",
        STORE,
        '    if base is not None and last is not None and last["number"] != base:',
        "    if False:",
        "pytest",
        f"{BACKEND}::test_keeping_the_same_block_again_adds_to_it_and_nothing_is_written_over",
    ),
    (
        "lines of the answer found apart are its block",
        KEPT,
        "    return any(held[at : at + len(want)] == want "
        "for at in range(len(held) - len(want) + 1))",
        "    return all(line in held for line in want)",
        "pytest",
        f"{BACKEND}::test_an_answer_holds_its_block_even_set_in_by_a_list",
    ),
    (
        "a request with a character that is no text is an error of the server's",
        APP,
        "    app.add_exception_handler(RequestValidationError, web.invalid_request)\n",
        "",
        "pytest",
        f"{BACKEND}::test_a_block_that_is_not_text_is_refused_and_nothing_is_kept",
    ),
    (
        "reading the source stops a running preview",
        DIALOG,
        "        {preview}",
        '        {tab === "preview" && preview}',
        "vitest",
        f"{SHOWN}::keeps a running preview while its source is read",
    ),
    (
        "the owner reading a chat keeps an artifact in it",
        API,
        "    chat = await _own_chat(request, person, chat_id)\n"
        "    store: Store = _state(request).store\n"
        "    message = await store.message(message_id)",
        "    chat = (await _readable_chat(request, person, chat_id))[0]\n"
        "    store: Store = _state(request).store\n"
        "    message = await store.message(message_id)",
        "pytest",
        f"{BACKEND}::test_another_person_finds_no_artifact_and_the_owner_only_reads",
    ),
    (
        "the owner reading a chat adds a revision",
        API,
        "    chat = await _own_chat(request, person, chat_id)\n"
        "    store: Store = _state(request).store\n"
        "    record = await store.artifact(artifact_id)",
        "    chat = (await _readable_chat(request, person, chat_id))[0]\n"
        "    store: Store = _state(request).store\n"
        "    record = await store.artifact(artifact_id)",
        "pytest",
        f"{BACKEND}::test_another_person_finds_no_artifact_and_the_owner_only_reads",
    ),
    (
        "a hidden message's artifact is the owner's to read",
        API,
        "        if record.message_id in hidden:",
        "        if False:",
        "pytest",
        f"{BACKEND}::test_an_artifact_of_a_hidden_message_is_hidden_from_the_install_owner",
    ),
    (
        "an answer still being written keeps an artifact",
        API,
        '    if message.status == "running" or (running is not None and '
        "running.message.id == message.id):",
        "    if False:",
        "pytest",
        f"{BACKEND}::test_an_answer_being_written_keeps_nothing_yet",
    ),
    (
        "a revision is numbered as the last one",
        STORE,
        '    number = (last["number"] if last is not None else 0) + 1',
        '    number = last["number"] if last is not None else 1',
        "pytest",
        f"{BACKEND}::test_keeping_the_same_block_again_adds_to_it_and_nothing_is_written_over",
    ),
    (
        "an artifact keeps any number of revisions",
        STORE,
        "    if number > most:",
        "    if False:",
        "pytest",
        f"{BACKEND}::test_an_artifact_keeps_only_so_much",
    ),
    (
        "a deleted chat's artifacts stay",
        STORE,
        '                db.execute("DELETE FROM artifacts WHERE chat_id = ?", (chat_id,))\n',
        "",
        "pytest",
        f"{BACKEND}::test_deleting_the_chat_deletes_its_artifacts",
    ),
    (
        "the server counts a block's words another way",
        KEPT,
        "        h1 = ((h1 ^ unit) * 2654435761) & _M",
        "        h1 = ((h1 ^ unit) * 2654435769) & _M",
        "pytest",
        f"{BACKEND}::test_a_block_s_number_is_the_one_the_page_computes",
    ),
    (
        "any page may frame the preview",
        WEB,
        "        \"frame-ancestors 'self'\",",
        '        "frame-ancestors *",',
        "pytest",
        f"{BACKEND}::test_the_preview_is_a_page_of_its_own_and_the_chat_s_policy_is_unchanged",
    ),
    (
        "the preview keeps an origin",
        WEB,
        '        "sandbox allow-scripts allow-forms",',
        '        "sandbox allow-scripts allow-forms allow-same-origin",',
        "pytest",
        f"{BACKEND}::test_the_preview_is_a_page_of_its_own_and_the_chat_s_policy_is_unchanged",
    ),
    (
        "the preview is served under the chat's policy",
        WEB,
        "                if framed:\n"
        '                    headers["Content-Security-Policy"] = PREVIEW_CSP',
        "                if False:\n"
        '                    headers["Content-Security-Policy"] = PREVIEW_CSP',
        "pytest",
        f"{BACKEND}::test_the_preview_is_a_page_of_its_own_and_the_chat_s_policy_is_unchanged",
    ),
    (
        "any page may frame the chat",
        WEB,
        '                headers["X-Frame-Options"] = "SAMEORIGIN" if framed else "DENY"',
        '                headers["X-Frame-Options"] = "SAMEORIGIN"',
        "pytest",
        f"{BACKEND}::test_the_preview_is_a_page_of_its_own_and_the_chat_s_policy_is_unchanged",
    ),
    (
        "a message from any window is heard",
        PREVIEW,
        "      if (!own || event.source !== own) return;",
        "      if (!own) return;",
        "vitest",
        f"{SHOWN}::sends the source only to its own frame",
    ),
    (
        "any nonce starts a preview",
        PREVIEW,
        "          if (said.nonce === nonce) {",
        "          if (said.nonce) {",
        "vitest",
        f"{SHOWN}::sends the source only to its own frame",
    ),
    (
        "the frame keeps this page's origin",
        PREVIEW,
        '          sandbox="allow-scripts allow-forms"\n',
        '          sandbox="allow-scripts allow-forms allow-same-origin"\n',
        "vitest",
        f"{SHOWN}::runs nothing until Run",
    ),
    (
        "an artifact runs when it is opened",
        PREVIEW,
        "  const [run, setRun] = useState<Running | null>(null);",
        '  const [run, setRun] = useState<Running | null>({ id: 0, tries: 1, state: "starting" });',
        "vitest",
        f"{SHOWN}::runs nothing until Run",
    ),
    (
        "a frame that never says it is ready is waited on",
        PREVIEW,
        "      if (tries < TRIES) {",
        "      if (false) {",
        "vitest",
        f"{SHOWN}::puts a frame that never says it is ready in a new one",
    ),
    (
        "a block the answer asked to be an artifact is code",
        MARKDOWN,
        "  if (kind && asked !== null) {",
        "  if (false) {",
        "vitest",
        f"{SHOWN}::shows a block the answer asked to be an artifact as its card",
    ),
    (
        "a fence still being written is a card",
        MARKDOWN,
        "  const asked = kind && !open && scope ?",
        "  const asked = kind && scope ?",
        "vitest",
        f"{SHOWN}::keeps it code while its fence is open",
    ),
    (
        "an SVG that is not well-formed is drawn anyway",
        SVG,
        '  if (parsed.getElementsByTagName("parsererror").length) {',
        "  if (false) {",
        "vitest",
        f"{LIB}::is not shown, saying why",
    ),
    (
        "the page counts a block's words another way",
        DIGEST,
        "    h1 = Math.imul(h1 ^ unit, 2654435761);",
        "    h1 = Math.imul(h1 ^ unit, 2654435769);",
        "vitest",
        f"{LIB}::is found by the number the server computes",
    ),
    (
        "a component's form is sent",
        RUNTIME,
        '  HTMLFormElement.prototype.submit = () => post({ type: "refused", what: FORM });\n',
        "",
        "vitest",
        f"{FRAME}::is never sent from a preview",
    ),
    (
        "a component may import anything",
        RUNTIME,
        "  if (found === undefined) {",
        "  if (found === undefined && false) {",
        "vitest",
        f"{FRAME}::imports React and nothing else",
    ),
]

RC6_CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "previews start together, none waiting for the one before",
        RUNNER,
        "    if (running().some((e) => e.readyAt === null || "
        "t - e.readyAt < PROBATION_MS)) return;",
        "",
        "vitest",
        f"{RUNS}::start one at a time",
    ),
    (
        "any number run at once",
        RUNNER,
        "    if (others.length < MAX_RUNNING) return true;",
        "    return true;",
        "vitest",
        f"{RUNS}::make room by stopping the one longest out of view",
    ),
    (
        "a frame that never got its source can be named the one that loops",
        RUNNER,
        "        given.find((e) => e.slot.focused()) ??\n"
        "        [...given].sort((a, b) => (b.readyAt ?? 0) - (a.readyAt ?? 0))[0];",
        "        live.find((e) => e.slot.focused()) ??\n"
        "        [...live].sort((a, b) => b.startedAt - a.startedAt)[0];",
        "vitest",
        f"{RUNS}::never name one whose frame never got its source",
    ),
    (
        "the one that looped runs by itself again",
        RUNNER,
        "          stuck.add(e.slot.key());\n",
        "",
        "vitest",
        f"{RUNS}::name the one the person was in",
    ),
    (
        "the others are not started again",
        RUNNER,
        "          e.wanted = e.visible;",
        "          e.wanted = false;",
        "vitest",
        f"{RUNS}::name the one the person was in",
    ),
    (
        "the owner's view runs someone's previews by itself",
        CARD,
        "            auto={runPreviews && !scope.readOnly && !scope.writing}",
        "            auto={runPreviews && !scope.writing}",
        "vitest",
        f"{INLINE}::waits for Run when the person turned that off",
    ),
    (
        "the person's switch is not obeyed",
        CARD,
        "            auto={runPreviews && !scope.readOnly && !scope.writing}",
        "            auto={!scope.readOnly && !scope.writing}",
        "vitest",
        f"{INLINE}::waits for Run when the person turned that off",
    ),
    (
        "an answer still being written runs its previews",
        CARD,
        "            auto={runPreviews && !scope.readOnly && !scope.writing}",
        "            auto={runPreviews && !scope.readOnly}",
        "vitest",
        f"{INLINE}::runs nothing by itself in an answer still being written",
    ),
    (
        "silence across a stall of the page's own is a loop",
        RUNNER,
        "    if (hidden() || gap > 2 * WATCH_MS) {",
        "    if (false) {",
        "vitest",
        f"{RUNS}::do not call a group stuck across a gap",
    ),
    (
        "a frame that floods the page is read in full",
        PREVIEW,
        "      if (++counted > MESSAGES_PER_SECOND) return;\n",
        "",
        "vitest",
        f"{INLINE}::reads a frame that floods the page only so far",
    ),
    (
        "a frame's height moves at every message",
        PREVIEW,
        "          if (sizing !== undefined) return;\n",
        "",
        "vitest",
        f"{INLINE}::moves its height at most every so often",
    ),
    (
        "a Markdown artifact's own fences are artifacts that run",
        "web/src/components/ArtifactBody.tsx",
        "        <ArtifactScope.Provider value={null}>\n"
        "          <Markdown text={latest.source} />\n"
        "        </ArtifactScope.Provider>",
        "        <Markdown text={latest.source} />",
        "vitest",
        f"{INLINE}::holds no artifacts of its own",
    ),
    (
        "a frame is as tall as its page, however tall",
        PREVIEW,
        "  return Math.round(Math.max(LEAST_HEIGHT, Math.min(page, windowHeight * MOST_HEIGHT)));",
        "  return Math.round(page);",
        "vitest",
        f"{INLINE}::takes its page's height",
    ),
    (
        "a preview does not say it still answers",
        RUNTIME,
        '    own.__previewAlive = window.setInterval(() => post({ type: "alive" }), ALIVE_MS);',
        "    own.__previewAlive = 0;",
        "vitest",
        f"{FRAME}::says it still answers",
    ),
    (
        "previews run by themselves only for one who turned it on",
        ME,
        '        "runPreviews": run_previews is not False,',
        '        "runPreviews": run_previews is True,',
        "pytest",
        f"{BACKEND}::test_previews_run_when_shown_until_the_person_turns_it_off",
    ),
    (
        "the switch takes words for yes",
        ME,
        "    on: StrictBool",
        "    on: bool",
        "pytest",
        f"{BACKEND}::test_previews_run_when_shown_until_the_person_turns_it_off",
    ),
    (
        "words hidden deep in the thread reach below it",
        CHAT,
        'className="relative min-h-0 flex-1 overflow-y-auto px-4',
        'className="min-h-0 flex-1 overflow-y-auto px-4',
        "vitest",
        f"{CHAT_TEST}::holds what is hidden in it for a screen reader",
    ),
    (
        "a Markdown artifact's hidden words reach out of its box",
        STYLES,
        ".artifact-doc {\n  position: relative;\n",
        ".artifact-doc {\n",
        "vitest",
        f"{SHEET}::a Markdown artifact's box holds what its Markdown hides",
    ),
    (
        "a proxy in front may rewrite the pages",
        WEB,
        'NO_CACHE = "no-cache, no-transform"',
        'NO_CACHE = "no-cache"',
        "pytest",
        f"{BACKEND}::test_a_proxy_in_front_is_told_to_leave_the_pages_as_written",
    ),
]

CASES += RC6_CASES

BROWSER_CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "a written page's form is sent by its button",
        RUNTIME,
        '  addEventListener("submit", function (e) { if (!e.defaultPrevented) '
        '{ e.preventDefault(); post({ type: "refused", what: FORM }); } });\n',
        "",
        "browser",
        BROWSER_TEST,
    ),
]

BROWSER_CASES.append(
    (
        "a written page's frame does not follow its page's height",
        RUNTIME,
        "  if (window.ResizeObserver) "
        "new ResizeObserver(size).observe(document.documentElement);\n",
        "",
        "browser",
        INLINE_BROWSER,
    )
)

if os.getenv("WORKBENCH_PLAYWRIGHT"):
    CASES += BROWSER_CASES

if __name__ == "__main__":
    runner.CASES = CASES
    sys.exit(runner.main())
