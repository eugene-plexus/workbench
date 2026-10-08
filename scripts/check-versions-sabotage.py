"""Prove the answer-version checks are observed (workbench-answer-versions.md §7).

Each case breaks one thing in the store, the API or the page, and its named
check must fail. Files are restored from exact bytes kept beside the run,
never from Git. Syntax and import errors do not count as a caught defect.

The browser cases run only with WORKBENCH_PLAYWRIGHT set (as for
`tests/test_versions_browser.py`); a page case rebuilds before and after.
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
STORE = "src/eugene_plexus_workbench/store.py"
API = "src/eugene_plexus_workbench/api.py"
VERSIONS = "tests/test_versions.py"
VIEW_TEST = "src/components/MessageView.test.tsx"
HOOK_TEST = "src/lib/useChat.test.ts"
BROWSER_TEST = "tests/test_versions_browser.py"

# (label, file, before, after, kind, check): kind is vitest, pytest or browser.
CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "messages() returns every row",
        STORE,
        "        return path(await self.tree(chat_id), via)",
        "        return await self.tree(chat_id)",
        "pytest",
        f"{VERSIONS}::test_the_path_never_holds_a_row_off_it_and_via_only_looks",
    ),
    (
        "a new version is not chosen",
        STORE,
        "                _choose(db, message.chat_id, message.id)\n",
        "",
        "pytest",
        f"{VERSIONS}::test_each_group_of_versions_has_exactly_one_chosen",
    ),
    (
        "choose ignores a running answer",
        API,
        "    _no_running(request, chat)\n    answers = _answers(request)\n",
        "    answers = _answers(request)\n",
        "pytest",
        f"{VERSIONS}::test_choose_is_refused_while_an_answer_runs",
    ),
    (
        "Try again deletes the old branch",
        API,
        "    await _answers(request).stop(chat.id)\n    await store.update_chat(",
        "    await _answers(request).stop(chat.id)\n"
        "    await store._run(lambda: store._conn().execute("
        '"DELETE FROM messages WHERE chat_id = ? AND seq >= ?", (chat.id, target.seq)))\n'
        "    await store.update_chat(",
        "pytest",
        f"{VERSIONS}::test_try_again_on_an_earlier_answer_starts_a_branch_and_keeps_the_old_one",
    ),
    (
        "an edit deletes the old branch",
        API,
        "    edited = await store.add_message(\n",
        "    await store._run(lambda: store._conn().execute("
        '"DELETE FROM messages WHERE chat_id = ? AND seq > ?", (chat.id, message.seq)))\n'
        "    edited = await store.add_message(\n",
        "pytest",
        f"{VERSIONS}::test_an_edit_keeps_the_old_message_and_its_branch",
    ),
    (
        "?via= saves",
        API,
        "    places = versions(tree)\n",
        "    if via is not None:\n        await store.choose(chat.id, via)\n"
        "    places = versions(tree)\n",
        "pytest",
        f"{VERSIONS}::test_via_only_looks_and_choose_saves",
    ),
    (
        "redaction checks only the message itself, not its path",
        API,
        "        site = hidden.get(message.parent_id) if message.parent_id is not None else None\n",
        "        site = None\n",
        "pytest",
        "tests/test_job_sites.py::"
        "test_the_owner_reaches_every_branch_and_each_is_redacted_along_its_own_path",
    ),
    (
        "the migration leaves a parent missing",
        STORE,
        '"WHERE p.chat_id = messages.chat_id AND p.seq < messages.seq "',
        '"WHERE p.chat_id = messages.chat_id AND p.seq < messages.seq - 1 "',
        "pytest",
        f"{VERSIONS}::test_a_schema_5_chat_opens_as_the_same_conversation",
    ),
    (
        "the arrows move while an answer runs",
        "web/src/components/MessageView.tsx",
        "  const held = busy && !readOnly;",
        "  const held = busy && false;",
        "vitest",
        f"{VIEW_TEST}::waits while an answer runs",
    ),
    (
        "the owner's arrows choose",
        "web/src/components/MessageView.tsx",
        "    if (readOnly) {\n      onLook?.(id);\n      return;\n    }\n",
        "",
        "vitest",
        f"{VIEW_TEST}::only looks for the owner",
    ),
    (
        "Try again is only the chat's last answer",
        "web/src/components/MessageView.tsx",
        "await post(`/api/chats/${chatId}/messages/${message.id}/retry`);",
        "await post(`/api/chats/${chatId}/retry`);",
        "vitest",
        f"{VIEW_TEST}::tries again on any answer",
    ),
    (
        "the edit note says the old one is replaced",
        "web/src/lib/words.ts",
        '  "Your earlier version and what followed it are kept. Use the arrows to go back.";',
        '  "Asking again replaces everything after this message.";',
        "vitest",
        f"{VIEW_TEST}::says an edit keeps the earlier version",
    ),
    (
        "a tab ignores a path event",
        "web/src/lib/useChat.ts",
        'if (event.type === "reload" || event.type === "path") {',
        'if (event.type === "reload") {',
        "vitest",
        f"{HOOK_TEST}::reloads every tab",
    ),
    (
        "a finished answer drops its place among its versions",
        "web/src/lib/useChat.ts",
        "      versions: message.versions ?? shown?.versions,",
        "      versions: message.versions,",
        "vitest",
        f"{HOOK_TEST}::keeps a message's place",
    ),
    (
        "export says nothing of other versions",
        "web/src/lib/conveniences.ts",
        "(m.versions?.count ?? 1) > 1",
        "(m.versions?.count ?? 1) > 99",
        "vitest",
        "src/components/Conveniences.test.tsx::says when other versions exist",
    ),
]

BROWSER_CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "Chrome: the model is sent every branch",
        STORE,
        "        return path(await self.tree(chat_id), via)",
        "        return await self.tree(chat_id)",
        "browser",
        BROWSER_TEST,
    ),
    (
        "Chrome: a second tab ignores a path event",
        "web/src/lib/useChat.ts",
        'if (event.type === "reload" || event.type === "path") {',
        'if (event.type === "reload") {',
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
    backup = Path(tempfile.mkdtemp(prefix="workbench-versions-sabotage-"))
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
