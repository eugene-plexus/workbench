"""Kept versions of an answer in Chrome, served by the real app
(`workbench-answer-versions.md` §7)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import World


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_answer_versions_in_chrome(world: World, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    world.gateway.numbered = True
    world.gateway.delay = 0.3
    person = world.browser()
    person.sign_in("p-ada")
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
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/versions-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
    # The last question was sent the first branch: the first message, its
    # second try, and nothing from the first try or the edit.
    assert len(world.gateway.requests) == 4
    sent = world.gateway.requests[-1]["messages"]
    assert [(m["role"], m["content"]) for m in sent] == [
        ("user", "First question"),
        ("assistant", "Hello from the model. #2"),
        ("user", "Final question"),
    ]
    # Every branch is still kept.
    (chat,) = person.get("/api/chats").json()["chats"]
    shown = person.get(f"/api/chats/{chat['id']}").json()["messages"]
    assert [m["content"] for m in shown] == [
        "First question",
        "Hello from the model. #2",
        "Final question",
        "Hello from the model. #4",
    ]
    first_try = shown[1]["versions"]["ids"][0]
    edit = shown[0]["versions"]["ids"][1]
    assert [
        m["content"]
        for m in person.get(f"/api/chats/{chat['id']}?via={first_try}").json()["messages"]
    ] == ["First question", "Hello from the model. #1"]
    assert [
        m["content"] for m in person.get(f"/api/chats/{chat['id']}?via={edit}").json()["messages"]
    ] == ["Edited question", "Hello from the model. #3"]
