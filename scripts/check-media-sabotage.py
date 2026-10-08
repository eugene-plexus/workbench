"""Prove the media screens' checks are observed (workbench-media-screens.md §9).

Each case breaks one thing in the changed code -- the store, the runner, the
API, the page -- and its named check must fail. Files are restored from
exact bytes kept beside the run, never from Git. Syntax and import errors do
not count as a caught defect.

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
APP = "src/eugene_plexus_workbench/app.py"
API = "src/eugene_plexus_workbench/api.py"
MEDIA = "src/eugene_plexus_workbench/media.py"
MEDIA_API = "src/eugene_plexus_workbench/media_api.py"
FILES = "src/eugene_plexus_workbench/files.py"
TESTS = "tests/test_media.py"
FILE_TESTS = "tests/test_media_files.py"
LIB = "web/src/lib/media.ts"
VIEW = "web/src/components/Media.tsx"
LIB_TEST = "src/lib/media.test.ts"
VIEW_TEST = "src/components/Media.test.tsx"
BROWSER_TEST = "tests/test_media_browser.py"
AUDIO_TESTS = "tests/test_media_audio.py"
AUDIO_FORMS = "web/src/components/AudioForms.tsx"
FORMS_TEST = "src/components/AudioForms.test.tsx"
AUDIO_BROWSER_TEST = "tests/test_media_audio_browser.py"

# (label, file, before, after, kind, check): kind is vitest, pytest or browser.
CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "references go to generations, not edits",
        MEDIA,
        "await self._hub.images(body, edit=bool(references))",
        "await self._hub.images(body, edit=False)",
        "pytest",
        f"{TESTS}::test_references_come_from_the_persons_own_bin",
    ),
    (
        "the size that came back is not read",
        MEDIA,
        "                width=size[0] if size else None,\n                height=size[1] if size else None,\n            )\n            path = files.path_of(self._root, row.owner, record.id)",
        "                width=None,\n                height=None,\n            )\n            path = files.path_of(self._root, row.owner, record.id)",
        "pytest",
        f"{TESTS}::test_an_image_is_kept_with_what_was_asked_and_what_came_back",
    ),
    (
        "a refusal loses the field it named",
        MEDIA,
        '"param": exc.param, "status": exc.status}',
        '"param": None, "status": exc.status}',
        "pytest",
        f"{TESTS}::test_a_refusal_is_the_gateways_words_with_its_field",
    ),
    (
        "a full disk is not named",
        MEDIA,
        "                if exc.errno == errno.ENOSPC\n",
        "                if False\n",
        "pytest",
        f"{FILE_TESTS}::test_a_full_disk_fails_the_request_and_names_the_disk",
    ),
    (
        "every tab hears everyone's results",
        MEDIA,
        "        for watch in list(self._watchers.get(owner, ())):",
        "        for watch in [w for ws in self._watchers.values() for w in ws]:",
        "pytest",
        f"{TESTS}::test_a_tab_hears_its_own_persons_results_only",
    ),
    (
        "a restart leaves a request running",
        APP,
        "        cut = await store.mark_media_interrupted()",
        "        cut = 0",
        "pytest",
        f"{TESTS}::test_a_request_a_crash_left_running_is_marked_interrupted_at_boot",
    ),
    (
        "anyone reads a result",
        MEDIA_API,
        "    if row is not None and row.owner == person.sub:\n        return row, False",
        "    if row is not None:\n        return row, False",
        "pytest",
        f"{TESTS}::test_a_result_is_its_owners_and_the_owner_reads_only_when_allowed",
    ),
    (
        "the owner reads a media file without the setting",
        API,
        "            if person.is_owner and await config.owner_reads_chats(store):\n                return",
        "            if person.is_owner:\n                return",
        "pytest",
        f"{TESTS}::test_a_result_is_its_owners_and_the_owner_reads_only_when_allowed",
    ),
    (
        "deleting leaves the bytes on disk",
        MEDIA_API,
        "            files.path_of(root, record.owner, record.id).unlink(missing_ok=True)",
        "            pass",
        "pytest",
        f"{TESTS}::test_emptying_a_bin_deletes_every_result_and_file",
    ),
    (
        "deleting a result deletes the chat's copy",
        STORE,
        'db.execute(f"DELETE FROM files WHERE media_id IN ({marks})", media_ids)',
        'db.execute(f"DELETE FROM files WHERE owner IN (SELECT owner FROM media WHERE id IN ({marks}))", media_ids)',
        "pytest",
        f"{TESTS}::test_send_to_a_chat_copies_and_each_side_deletes_alone",
    ),
    (
        "a file is not served by Range",
        API,
        "    return FileResponse(\n        path,\n",
        "    return Response(\n        content=path.read_bytes(),\n",
        "pytest",
        f"{TESTS}::test_an_image_is_kept_with_what_was_asked_and_what_came_back",
    ),
    (
        "a JPEG's size is read the wrong way round",
        FILES,
        "            return width, height\n",
        "            return height, width\n",
        "pytest",
        f"{FILE_TESTS}::test_an_images_own_size_is_read_from_its_bytes",
    ),
    (
        "a setting the backend checks is not offered",
        LIB,
        '  if (values === null) return { kind: "free" };',
        '  if (values === null) return { kind: "none" };',
        "vitest",
        f"{VIEW_TEST}::offers only what the chosen one lists",
    ),
    (
        "an external model says it runs locally",
        LIB,
        '  if (model.locality === "local") return "Runs on your own machines.";',
        '  if (model.locality !== "unknown") return "Runs on your own machines.";',
        "vitest",
        f"{LIB_TEST}::says where a model runs",
    ),
    (
        "asked and got are not shown side by side",
        LIB,
        "  if (asked && got && asked !== got) return",
        "  if (false) return",
        "vitest",
        f"{VIEW_TEST}::shows what was asked beside what came back",
    ),
    (
        "a first visit picks a model",
        VIEW,
        '    model: models.find((m) => m.id === rememberedModel(me.sub, "images"))?.id ?? "",',
        '    model: models.find((m) => m.id === rememberedModel(me.sub, "images"))?.id ?? models[0]!.id,',
        "vitest",
        f"{VIEW_TEST}::picks no model for a first visit",
    ),
    (
        "delete does not ask first",
        VIEW,
        "onClick={() => setConfirm(true)}",
        "onClick={onDelete}",
        "vitest",
        f"{VIEW_TEST}::deletes only when confirmed",
    ),
    (
        "the owner's view has actions",
        VIEW,
        '      {!readOnly && (\n        <div className="flex flex-wrap items-center gap-3 text-sm">',
        '      {(\n        <div className="flex flex-wrap items-center gap-3 text-sm">',
        "vitest",
        f"{VIEW_TEST}::read only, with no form or actions",
    ),
    # --- slice 2: speech and transcription ---------------------------------
    (
        "what the model heard is not kept",
        MEDIA,
        '"heardSeconds": usage.get("seconds") if usage.get("type") == "duration" else None,',
        '"heardSeconds": None,',
        "pytest",
        f"{AUDIO_TESTS}::test_what_the_model_heard_is_kept_beside_the_clips_length",
    ),
    (
        "translate goes to the transcriptions door",
        MEDIA_API,
        "await _jobs(request).start_transcription(row, fields, source, translate=translate)",
        "await _jobs(request).start_transcription(row, fields, source, translate=False)",
        "pytest",
        f"{AUDIO_TESTS}::test_translate_goes_to_the_translations_door_without_a_language",
    ),
    (
        "a language is sent with a translation",
        MEDIA_API,
        "    if language and not translate:\n",
        "    if language:\n",
        "pytest",
        f"{AUDIO_TESTS}::test_translate_goes_to_the_translations_door_without_a_language",
    ),
    (
        "pcm is offered to a browser",
        MEDIA_API,
        'if f != "pcm"]',
        "if f]",
        "pytest",
        f"{AUDIO_TESTS}::test_the_screens_list_speech_and_transcription_models_with_what_they_take",
    ),
    (
        "a Chrome recording is not recognised",
        FILES,
        '    if data.startswith(b"\\x1a\\x45\\xdf\\xa3"):\n        return "audio/webm"\n',
        "",
        "pytest",
        f"{AUDIO_TESTS}::test_every_recording_kind_measured_is_taken",
    ),
    (
        "a clip cannot go into a chat",
        MEDIA_API,
        'if kind not in ("image", "audio"):',
        'if kind not in ("image",):',
        "pytest",
        f"{AUDIO_TESTS}::test_send_to_a_chat_takes_mp3_and_wav_and_says_why_not_the_rest",
    ),
    (
        "a shortfall in what was heard is not said",
        LIB,
        "if (heard != null && heard < clip - 0.25) {",
        "if (heard != null && heard < clip - 5) {",
        "vitest",
        f"{VIEW_TEST}::says when the model heard less",
    ),
    (
        "a model that lists no voices gets an empty list",
        AUDIO_FORMS,
        "  const voices = chosen?.voices ?? null;",
        "  const voices = chosen?.voices ?? [];",
        "vitest",
        f"{FORMS_TEST}::a free box where it lists none",
    ),
    (
        "translation is offered on any model",
        AUDIO_FORMS,
        "        {chosen?.translates && (",
        "        {chosen && (",
        "vitest",
        f"{FORMS_TEST}::still takes an upload",
    ),
    (
        "recording is offered without HTTPS",
        AUDIO_FORMS,
        "          {recordable ? (",
        "          {recordable || true ? (",
        "vitest",
        f"{FORMS_TEST}::says recording needs HTTPS",
    ),
    # Voice names (ElevenLabs' ids say nothing), 2026-10-08.
    (
        "voice names are not passed to the page",
        MEDIA_API,
        '    named = info.get("voice_names")\n',
        "    named = None\n",
        "pytest",
        f"{AUDIO_TESTS}::test_the_screens_list_speech_and_transcription_models_with_what_they_take",
    ),
    (
        "a named voice is shown by its id",
        LIB,
        "  const name = model.voiceNames?.[id];\n",
        "  const name = model.voiceNames?.[id] && undefined;\n",
        "vitest",
        f"{LIB_TEST}::shows a voice by its name",
    ),
    (
        "two voices with one name cannot be told apart",
        LIB,
        "  return shared ? `${name} (${id})` : name;\n",
        "  return shared ? name : name;\n",
        "vitest",
        f"{LIB_TEST}::shows a voice by its name",
    ),
    (
        "the picker shows ids, not names",
        AUDIO_FORMS,
        "                      {voiceLabel(chosen, v)}\n",
        "                      {voiceLabel(chosen, v) && v}\n",
        "vitest",
        f"{FORMS_TEST}::shows ElevenLabs voices by name",
    ),
    (
        "a voice is not found by its name",
        AUDIO_FORMS,
        '      (chosen?.voiceNames?.[v] ?? "").toLowerCase().includes(wanted),\n',
        '      (chosen?.voiceNames?.[v] ?? "").toLowerCase().includes(wanted) && false,\n',
        "vitest",
        f"{FORMS_TEST}::shows ElevenLabs voices by name",
    ),
]

BROWSER_CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "Chrome: a transcript sent to a chat is not waiting there",
        "web/src/App.tsx",
        "                saveDraft(phase.me.sub, chat.id, text);\n",
        # Kept referenced, so the page still builds: the draft goes to no chat.
        '                saveDraft(phase.me.sub, "no chat", text);\n',
        "browser",
        AUDIO_BROWSER_TEST,
    ),
    (
        "Chrome: a tab does not open its screen",
        "web/src/components/Media.tsx",
        "onClick={() => d !== door && onDoor(d)}",
        # The tab reopens the screen already shown (`onDoor` stays used, so
        # the page still builds).
        "onClick={() => d !== door && onDoor(door)}",
        "browser",
        AUDIO_BROWSER_TEST,
    ),
    (
        "Chrome: Back loses the media area",
        "web/src/App.tsx",
        "      setMedia(mediaFromPath(window.location.pathname));",
        "      setMedia(null);",
        "browser",
        BROWSER_TEST,
    ),
    (
        "Chrome: the image is not waiting in the new chat",
        "web/src/App.tsx",
        "attachments={handoff?.chatId === chatId ? handoff.attachments : undefined}",
        'attachments={handoff?.chatId === "no chat" ? handoff.attachments : undefined}',
        "browser",
        BROWSER_TEST,
    ),
    (
        "Chrome: a request is dropped before it is done",
        MEDIA_API,
        "    await _jobs(request).start_images(row, gateway, references)\n",
        "    await _jobs(request).start_images(row, gateway, references)\n"
        "    await asyncio.sleep(0.2)\n"
        "    await _jobs(request).stop(row.id)\n",
        "browser",
        BROWSER_TEST,
    ),
]

if os.getenv("WORKBENCH_PLAYWRIGHT"):
    CASES += BROWSER_CASES
# `--only TEXT` runs the cases whose label holds TEXT (a rerun of a few);
# `A|B` runs those holding either.
if "--only" in sys.argv:
    wanted = sys.argv[sys.argv.index("--only") + 1].split("|")
    CASES = [c for c in CASES if any(w in c[0] for w in wanted)]

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
    backup = Path(tempfile.mkdtemp(prefix="workbench-media-sabotage-"))
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
