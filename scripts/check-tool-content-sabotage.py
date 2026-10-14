"""Prove the tool-content checks are observed (rich-content RC3).

Each case breaks one rule in the code RC3 added or changed, and its named check
must fail. Files are restored from exact bytes kept beside the run, never from
Git. Syntax and import errors do not count as a caught defect.

The browser case runs only with WORKBENCH_PLAYWRIGHT set (as for
`tests/test_rich_tool_content_browser.py`); it rebuilds the page before and after.
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
CONTENT = "src/eugene_plexus_workbench/content.py"
API = "src/eugene_plexus_workbench/api.py"
STORE = "src/eugene_plexus_workbench/store.py"
DOCUMENTS = "src/eugene_plexus_workbench/documents_api.py"
TOOLS = "src/eugene_plexus_workbench/tools.py"
VIEW = "web/src/components/ToolContent.tsx"
CLIENT = "web/src/lib/api.ts"
EXPORT = "web/src/lib/conveniences.ts"
APP = "web/src/App.tsx"
UNIT = "tests/test_tool_content.py"
SCHEMA = "tests/test_documents_store.py"
VIEW_TEST = "src/components/ToolContent.test.tsx"
BROWSER_TEST = "tests/test_rich_tool_content_browser.py"

# (label, file, before, after, kind, check): kind is pytest, vitest or browser.
CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "a picture is shown on what it says, not on its bytes",
        CONTENT,
        '    return "file", "application/octet-stream"',
        '    return (family if family in LIMITS else "file"), declared or ""',
        "pytest",
        f"{UNIT}::test_a_picture_its_bytes_do_not_bear_out_is_a_file",
    ),
    (
        "the model is not told it did not see the picture",
        CONTENT,
        '{said}. The person sees it; you do not.]"',
        '{said}.]"',
        "pytest",
        f"{UNIT}::test_every_kind_of_block_is_kept_in_order_and_said_to_the_model",
    ),
    (
        "a file's id reaches the model",
        CONTENT,
        '                said += f" ({media_type}, {_sized(len(data))})"',
        "                said += f\" ({media_type}, {_sized(len(data))}) {kept['fileId']}\"",
        "pytest",
        f"{UNIT}::test_a_mixed_result_is_kept_shown_after_a_refresh_and_replayed_as_words",
    ),
    (
        "a file of any size is kept",
        CONTENT,
        "        if len(data) > limit:",
        "        if len(data) > limit * 10**9:",
        "pytest",
        f"{UNIT}::test_what_cannot_be_kept_says_why",
    ),
    (
        "a call keeps any number of files",
        CONTENT,
        "        if self.count >= MAX_FILES:",
        "        if self.count >= 10**9:",
        "pytest",
        f"{UNIT}::test_a_call_and_a_person_keep_only_so_much",
    ),
    (
        "a person keeps any amount",
        CONTENT,
        "        if used + len(data) > PERSON_LIMIT:",
        "        if used + len(data) > PERSON_LIMIT * 10**9:",
        "pytest",
        f"{UNIT}::test_a_call_and_a_person_keep_only_so_much",
    ),
    (
        "bytes that are not base64 are read anyway",
        CONTENT,
        "base64.b64decode(encoded, validate=True)",
        "base64.b64decode(encoded, validate=False)",
        "pytest",
        f"{UNIT}::test_what_cannot_be_kept_says_why",
    ),
    (
        "structured content the words hold is said twice",
        CONTENT,
        'b["text"].strip() == encoded for b in out):',
        "False for b in out):",
        "pytest",
        f"{UNIT}::test_structured_content_the_words_already_hold_is_said_once",
    ),
    (
        "a result keeps any number of blocks",
        CONTENT,
        "enumerate(items[:MAX_BLOCKS])",
        "enumerate(items)",
        "pytest",
        f"{UNIT}::test_a_result_of_many_blocks_keeps_so_many",
    ),
    (
        "the words kept on a call are kept whole",
        CONTENT,
        '        kept = text if len(text) <= budget else text[:budget] + "…"',
        "        kept = text",
        "pytest",
        f"{UNIT}::test_the_words_kept_on_a_call_stop_where_the_models_do",
    ),
    (
        "structured content that is not JSON is kept",
        CONTENT,
        "allow_nan=False)",
        "allow_nan=True)",
        "pytest",
        f"{UNIT}::test_structured_content_that_is_not_json_is_not_kept",
    ),
    (
        "a file's name keeps its path",
        CONTENT,
        "if c.isprintable() and c not in _UNSAFE",
        "if True",
        "pytest",
        f"{UNIT}::test_a_file_is_saved_under_a_name_that_is_only_a_name",
    ),
    (
        "a picture keeps the name it gave itself",
        CONTENT,
        '    if kind == "file":',
        "    if True:",
        "pytest",
        f"{UNIT}::test_a_file_is_saved_under_a_name_that_is_only_a_name",
    ),
    (
        "a file a stopped call never named stays",
        CONTENT,
        """        if f'"{file_id}"' not in named""",
        "        if False",
        "pytest",
        f"{UNIT}::test_a_file_a_stopped_call_kept_and_never_named_is_let_go_when_the_answer_ends",
    ),
    (
        "a hidden message's tool file is served to the install owner",
        API,
        "        return record.message_id in hidden",
        "        return False",
        "pytest",
        f"{UNIT}::test_a_sites_picture_is_kept_and_hidden_from_the_install_owner_as_its_message_is",
    ),
    (
        "a hidden message's upload is served to the install owner",
        API,
        "    return any(m.id in hidden for m in tree if record.id in m.attachments)",
        "    return False",
        "pytest",
        f"{UNIT}::test_an_upload_in_a_hidden_message_is_hidden_from_the_install_owner_too",
    ),
    (
        "a tool's file is taken back as an unsent upload",
        STORE,
        "AND chat_id = ? AND origin = 'upload'\",",
        'AND chat_id = ?",',
        "pytest",
        f"{UNIT}::test_a_mixed_result_is_kept_shown_after_a_refresh_and_replayed_as_words",
    ),
    (
        "a tool's file is listed as a chat's document",
        DOCUMENTS,
        "await store.files_for_chat(chat.id, uploads=True), key=",
        "await store.files_for_chat(chat.id), key=",
        "pytest",
        f"{UNIT}::test_a_mixed_result_is_kept_shown_after_a_refresh_and_replayed_as_words",
    ),
    (
        "every tool file is taken for an orphan",
        STORE,
        """if message is None or '"' + row["id"] + '"' not in (message["tool_rounds"] or ""):""",
        "if True:",
        "pytest",
        f"{UNIT}::test_a_tool_file_no_answer_names_is_removed_and_one_an_answer_names_stays",
    ),
    (
        "an orphan is kept",
        STORE,
        """if message is None or '"' + row["id"] + '"' not in (message["tool_rounds"] or ""):""",
        "if False:",
        "pytest",
        f"{UNIT}::test_a_tool_file_no_answer_names_is_removed_and_one_an_answer_names_stays",
    ),
    (
        "a site's result is not read as blocks",
        TOOLS,
        '                    call, result.get("content"), result.get("structuredContent"), '
        "is_error, keeper",
        '                    call, None, result.get("structuredContent"), is_error, keeper',
        "pytest",
        f"{UNIT}::test_a_sites_picture_is_kept_and_hidden_from_the_install_owner_as_its_message_is",
    ),
    (
        "a new store has no origin column",
        STORE,
        "    message_id TEXT,\n    origin TEXT NOT NULL DEFAULT 'upload' "
        "CHECK (origin IN ('upload', 'tool'))\n",
        "    message_id TEXT\n",
        "pytest",
        f"{SCHEMA}::test_a_new_store_and_a_migrated_one_have_the_same_files_table",
    ),
    (
        "a sound is fetched before it is asked for",
        VIEW,
        "  const [wanted, setWanted] = useState(false);",
        "  const [wanted, setWanted] = useState(true);",
        "vitest",
        f"{VIEW_TEST}::loads a sound only when asked",
    ),
    (
        "a sound plays by itself",
        VIEW,
        '<audio src={url} controls preload="metadata" className="w-full" />',
        '<audio src={url} controls autoPlay preload="metadata" className="w-full" />',
        "vitest",
        f"{VIEW_TEST}::loads a sound only when asked",
    ),
    (
        "any address the tool names is a link",
        VIEW,
        "      const href = safeHref(block.uri);",
        "      const href = block.uri;",
        "vitest",
        f"{VIEW_TEST}::names an address without fetching it",
    ),
    (
        "a picture's address outlives it",
        VIEW,
        "      if (made) releaseFileUrl(made);\n",
        "",
        "vitest",
        f"{VIEW_TEST}::shows a picture fetched from Workbench",
    ),
    (
        "signing out keeps the files' addresses",
        CLIENT,
        "  for (const url of made) URL.revokeObjectURL(url);\n",
        "",
        "vitest",
        f"{VIEW_TEST}::lets every file's address go when the person signs out",
    ),
    (
        "an export carries Workbench's address of a file",
        EXPORT,
        "(${b.mediaType}, ${b.size} bytes)`]",
        "(${b.mediaType}, ${b.size} bytes) ${b.fileId}`]",
        "vitest",
        f"{VIEW_TEST}::exports a call's files and addresses by name",
    ),
]

BROWSER_CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "signing out does not let the files go",
        APP,
        # Named, not called: the page still builds.
        "    releaseAllFileUrls();\n",
        "    void releaseAllFileUrls;\n",
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
        return result.returncode == 0, output
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", check],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=900,
    )
    output = result.stdout + result.stderr
    if kind == "pytest" and re.search(r"\bno tests ran\b|ERROR: not found", output):
        raise SystemExit(f"{check} matched no test; update the instrument.\n{output}")
    return result.returncode == 0, output


def build() -> None:
    done = subprocess.run(
        [NPM, "run", "build"], cwd=WEB, env=ENV, capture_output=True, text=True, encoding="utf-8"
    )
    if done.returncode:
        raise SystemExit(f"The page did not build:\n{done.stdout}{done.stderr}")


def broken_build(output: str) -> bool:
    """A mutation that stops the code compiling proves nothing."""
    markers = (
        "SyntaxError",
        "Transform failed",
        "ERROR collecting",
        "error TS",
        "ImportError",
        "IndentationError",
        "NameError",
    )
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
    backup = Path(tempfile.mkdtemp(prefix="workbench-tool-content-sabotage-"))
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
