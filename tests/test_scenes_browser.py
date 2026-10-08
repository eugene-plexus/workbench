"""The working scenes in Chrome, served by the real app (workbench.md §6.1)."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import World


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_working_scenes_in_chrome(world: World, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    scenes = sorted(p.name for p in (root / "web/public/scenes").glob("*.svg"))
    assert scenes and all((world.settings.static_dir / "scenes" / s).is_file() for s in scenes), (
        "build the page first (npm run build): the scenes are not in the build"
    )
    listed = (root / "web/src/lib/scenes.ts").read_text(encoding="utf-8")
    phrases = re.findall(r'phrase: "([^"]+)"', listed)
    delay = re.search(r"SCENE_DELAY_MS = (\d+)", listed)
    assert phrases and delay
    # The first words wait long enough for Eugene to show and be looked at.
    world.gateway.words = ["Hello", " there."]
    world.gateway.delay = 2.5
    person = world.browser()
    person.sign_in("p-ada")
    shots = Path(os.getenv("WORKBENCH_SHOTS", str(tmp_path / "shots")))
    shots.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "browser.json"
    cfg.write_text(
        json.dumps(
            {
                "url": world.workbench,
                "secret": person.secret,
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
                "scenes": scenes,
                "phrases": phrases,
                "delayMs": int(delay[1]),
                "shots": str(shots),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/scenes-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
