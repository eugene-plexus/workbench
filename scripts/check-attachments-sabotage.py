"""Prove the checks for workbench#2 and #3 are observed.

#2: the edit box and the composer each have their own name. #3: the
composer's Remove deletes an unsent upload, row and bytes, so it no longer
counts against the chat's limit; a sent file stays.

Each case breaks one thing in the store, the API or the page, and its named
check must fail. Files are restored from exact bytes kept beside the run,
never from Git. Syntax and import errors do not count as a caught defect.

The browser cases run only with WORKBENCH_PLAYWRIGHT set (as for
`tests/test_media_browser.py`); a page case rebuilds before and after.
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
COMPOSER = "web/src/components/Composer.tsx"
CHATS = "tests/test_chats.py"
COMPOSER_TEST = "src/components/Composer.test.tsx"
REMOVED = f"{CHATS}::test_removing_an_unsent_upload_deletes_it_and_frees_the_chats_room"
OWNER_ONLY = f"{CHATS}::test_only_the_chats_owner_removes_its_uploads"

# (label, file, before, after, kind, check): kind is vitest, pytest or browser.
CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "no Remove route (the bug as filed)",
        API,
        '@router.delete("/api/chats/{chat_id}/files/{file_id}", '
        "status_code=status.HTTP_204_NO_CONTENT)\n",
        "",
        "pytest",
        REMOVED,
    ),
    (
        "Remove keeps the row",
        API,
        "        record = await _state(request).store.delete_unsent_file(chat.id, file_id)\n",
        "        record = await _state(request).store.file(file_id)\n",
        "pytest",
        REMOVED,
    ),
    (
        "Remove keeps the bytes",
        API,
        "    await asyncio.to_thread(stored.unlink, True)\n",
        "    await asyncio.to_thread(stored.exists)\n",
        "pytest",
        REMOVED,
    ),
    (
        "a sent file is deleted",
        STORE,
        "                            raise FileInUse\n",
        "                            pass\n",
        "pytest",
        f"{CHATS}::test_a_sent_file_stays_with_its_message",
    ),
    (
        "the owner's read-only view removes",
        API,
        "    chat = await _own_chat(request, person, chat_id)\n    try:\n"
        "        record = await _state(request).store.delete_unsent_file(",
        "    chat, _ = await _readable_chat(request, person, chat_id)\n    try:\n"
        "        record = await _state(request).store.delete_unsent_file(",
        "pytest",
        OWNER_ONLY,
    ),
    (
        "a file of another chat is removed",
        STORE,
        '"SELECT * FROM files WHERE id = ? AND chat_id = ?", (file_id, chat_id)',
        '"SELECT * FROM files WHERE id = ?", (file_id,)',
        "pytest",
        OWNER_ONLY,
    ),
    (
        "a bin's original is removed through a chat",
        STORE,
        '"SELECT * FROM files WHERE id = ? AND chat_id = ?", (file_id, chat_id)',
        '"SELECT * FROM files WHERE id = ?", (file_id,)',
        "pytest",
        "tests/test_media.py::test_removing_a_sent_to_chat_copy_deletes_only_the_copy",
    ),
    (
        "the store keeps a message naming a lost file",
        STORE,
        "                        raise AttachmentGone\n",
        "                        pass\n",
        "pytest",
        f"{CHATS}::test_a_send_and_a_remove_meet_one_at_a_time_in_the_store",
    ),
    (
        "a send that lost its file is a 500",
        API,
        "    except AttachmentGone:\n",
        "    except LookupError:\n",
        "pytest",
        f"{CHATS}::test_a_file_removed_while_its_send_is_checked_is_refused_by_name",
    ),
    (
        "Remove only drops the chip",
        COMPOSER,
        "      await del(`/api/chats/${chat.id}/files/${encodeURIComponent(file.id)}`);\n",
        "      void del;\n",
        "vitest",
        f"{COMPOSER_TEST}::deletes the upload, then drops the chip",
    ),
    (
        "a refused Remove drops the chip anyway",
        COMPOSER,
        "    } catch (error) {\n      setProblem(\n        `${file.name} was not removed:",
        "    } catch (error) {\n"
        "      setPending((current) => current.filter((p) => p.id !== file.id));\n"
        "      setProblem(\n        `${file.name} was not removed:",
        "vitest",
        f"{COMPOSER_TEST}::keeps the chip and says why when the delete is refused",
    ),
    (
        "Send is not held while a Remove is under way",
        COMPOSER,
        "                removing !== null ||\n",
        "",
        "vitest",
        f"{COMPOSER_TEST}::holds Send while the delete is under way",
    ),
    (
        "the edit box has the composer's name (the bug as filed)",
        "web/src/components/MessageView.tsx",
        'aria-label="Edit your message"',
        'aria-label="Your message"',
        "vitest",
        f"{COMPOSER_TEST}::names the edit box apart from the composer",
    ),
]

BROWSER_CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "Chrome: Remove only drops the chip",
        COMPOSER,
        "      await del(`/api/chats/${chat.id}/files/${encodeURIComponent(file.id)}`);\n",
        "      void del;\n",
        "browser",
        "tests/test_media_browser.py",
    ),
    (
        "Chrome: the edit box has the composer's name",
        "web/src/components/MessageView.tsx",
        'aria-label="Edit your message"',
        'aria-label="Your message"',
        "browser",
        "tests/test_versions_browser.py",
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
    backup = Path(tempfile.mkdtemp(prefix="workbench-attachments-sabotage-"))
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
