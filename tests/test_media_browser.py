"""The Images screen in Chrome, served by the real app
(`workbench-media-screens.md` §9)."""

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from eugene_plexus_workbench import files

from .conftest import World, png


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_the_images_screen_in_chrome(world: World, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    world.gateway.image_delay = 2.0
    person = world.browser()
    person.sign_in("p-ada")
    square = tmp_path / "square.png"
    square.write_bytes(png(4, 4))
    cfg = tmp_path / "browser.json"
    cfg.write_text(
        json.dumps(
            {
                "url": world.workbench,
                "secret": person.secret,
                "attach": str(square),
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
        ["node", str(root / "web/scripts/media-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
    # One image was asked for, by the server, at the default shape.
    assert [(door, body.get("size")) for door, body in world.gateway.image_requests] == [
        ("generations", "1024x1024")
    ]
    # The chat was sent the file attached in place of the removed copy, as an
    # image the model can see.
    [asked] = world.gateway.requests
    parts = asked["messages"][-1]["content"]
    images = [p["image_url"]["url"] for p in parts if p.get("type") == "image_url"]
    assert images == ["data:image/png;base64," + base64.b64encode(png(4, 4)).decode()], parts
    # The result and the removed copy are gone from disk; the attached file stays.
    [chat] = person.get("/api/chats").json()["chats"]
    [message] = [
        m for m in person.get(f"/api/chats/{chat['id']}").json()["messages"] if m["role"] == "user"
    ]
    [attached] = message["attachments"]
    kept = [p.name for p in files.person_dir(world.data, "p-ada").iterdir() if p.is_file()]
    assert kept == [attached], kept
    assert person.get("/api/media", params={"door": "images"}).json()["items"] == []
