"""A tool's pictures, sounds and files in Chrome, under the page's own headers
(workbench-rich-content-roadmap.md RC3). Opt-in, like the other browser
checks; set WORKBENCH_SHOTS to keep the pictures."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from eugene_plexus_workbench.web import CSP

from .conftest import World
from .test_tool_content import PAGE, PICTURE, mixed, run_mixed  # noqa: F401


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_a_tools_pictures_sounds_and_files_in_chrome(
    world: World,
    mixed: str,  # noqa: F811 (the fixture, imported)
    tmp_path: Path,
    quiet_loop: None,
) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    _, ada, chat, call = run_mixed(world, mixed)
    picture = next(b for b in call["content"] if b["type"] == "image")
    sound = next(b for b in call["content"] if b["type"] == "audio")
    page = next(b for b in call["content"] if b["type"] == "file")
    shots = Path(os.getenv("WORKBENCH_SHOTS") or tmp_path / "shots")
    cfg = tmp_path / "browser.json"
    cfg.write_text(
        json.dumps(
            {
                "url": world.workbench,
                "csp": CSP,
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
                    for c in ada.http.cookies.jar
                ],
                "secret": ada.secret,
                "chat": chat,
                "picture": {"id": picture["fileId"], "name": picture["name"], "size": len(PICTURE)},
                "sound": {"id": sound["fileId"], "name": sound["name"]},
                "page": {"id": page["fileId"], "name": page["name"], "bytes": PAGE.decode()},
                "shots": str(shots),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/tool-content-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
