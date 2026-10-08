"""The Speech and Transcription screens in Chrome, served by the real app
(`workbench-media-screens.md` §4, slice 2)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import World, wav


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_speech_and_transcription_in_chrome(world: World, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    # What OpenRouter's whisper did to kokoro's MP3 (measured 2026-10-08).
    world.gateway.heard_seconds = 1.525
    recording = tmp_path / "note.wav"
    recording.write_bytes(wav(3.0))
    person = world.browser()
    person.sign_in("p-ada")
    cfg = tmp_path / "browser.json"
    cfg.write_text(
        json.dumps(
            {
                "url": world.workbench,
                "secret": person.secret,
                "recording": str(recording),
                "playwright": os.environ["WORKBENCH_PLAYWRIGHT"],
                "chrome": os.getenv(
                    "WORKBENCH_CHROME", "C:/Program Files/Google/Chrome/Application/chrome.exe"
                ),
                "cookies": [
                    {
                        "name": c.name,
                        "value": c.value,
                        "url": world.workbench,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                    for c in person.http.cookies.jar
                ],
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/media-audio-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
    [spoken] = world.gateway.speech_requests
    assert (spoken["voice"], spoken["response_format"]) == ("af_bella", "wav")
    [uploaded, recorded] = world.gateway.transcriptions
    assert uploaded[2][0] == "note.wav" and uploaded[2][1] == wav(3.0)
    # Chrome's own recording, as made: WebM, sent unchanged.
    assert recorded[2][0] == "recording.webm" and recorded[2][1].startswith(b"\x1a\x45\xdf\xa3")
    items = person.get("/api/media", params={"door": "transcription"}).json()["items"]
    assert sorted(i["request"]["clipSeconds"] > 0 for i in items) == [True, True]
