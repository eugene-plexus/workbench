"""The Video screen in Chrome, served by the real app
(`workbench-media-screens.md` §5, slice 3)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from eugene_plexus_workbench import media

from .conftest import World, png

#: A real 1 s H.264 clip, 160x90, so Chrome plays it and reads its size.
CLIP = Path(__file__).parent / "fixtures" / "clip-160x90.mp4"


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_video_in_chrome(world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    # Long enough running for the time so far and the working scene to show.
    monkeypatch.setattr(media, "POLL_SECONDS", 0.5)
    world.gateway.video_polls_queued = 6
    world.gateway.video_content = CLIP.read_bytes()
    frame = tmp_path / "frame.png"
    frame.write_bytes(png(8, 8))
    person = world.browser()
    person.sign_in("p-ada")
    cfg = tmp_path / "browser.json"
    cfg.write_text(
        json.dumps(
            {
                "url": world.workbench,
                "secret": person.secret,
                "frame": str(frame),
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
        ["node", str(root / "web/scripts/media-video-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
    [first, second] = world.gateway.video_requests
    assert first == {
        "model": "x-ai/grok-imagine-video",
        "prompt": "a red ball bouncing",
        "seconds": "1",
        "size": "854x480",
    }
    # The second starts from the image brought in.
    assert second["prompt"] == "the ball from this frame"
    assert second["input_reference"]["image_url"].startswith("data:image/png;base64,")
