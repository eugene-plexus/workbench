"""Artifacts shown and run in place, in Chrome, under the headers Workbench really
sends (workbench-rich-content-roadmap.md RC6). Opt-in, like the other browser
checks; set WORKBENCH_SHOTS to keep the pictures."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import World
from .test_job_sites import _owner_reads
from .test_rich_artifacts_browser import COUNTER, ESCAPES, LOGO, NOTES, fence

TALL = "<!doctype html>" + "".join(f"<p>Line {n}</p>" for n in range(120))
SHORT = "<!doctype html><p>Short</p>"
FIFTH = "<!doctype html><p>The fifth</p>"
CALM = "<!doctype html><p>Calm</p>"
CALM_TOO = "<!doctype html><p>Calm too</p>"
# Loops a moment after it starts, once the previews before it have run a while.
BUSY = "<!doctype html><p>Busy</p><script>setTimeout(function () { for (;;) {} }, 300)</script>"
PLAIN_WORDS = "\n\n".join(f"Paragraph {n} of words that push the page down." for n in range(80))

SHOWN = "\n\n".join(
    [
        "Here they are.",
        fence("markdown", "Notes", NOTES),
        fence("svg", "Logo", LOGO),
        fence("tsx", "Counter", COUNTER),
        fence("html", "Short", SHORT),
        fence("html", "Tall", TALL),
        fence("html", "Escapes", ESCAPES),
        fence("html", "Fifth", FIFTH),
    ]
)
# A written page that grows after it has loaded: its frame follows (the reporter's watch).
GROWS = (
    "<!doctype html><p>Grows</p><script>setTimeout(function () {"
    " for (var i = 0; i < 8; i++) { var p = document.createElement('p');"
    " p.textContent = 'More ' + i; document.body.appendChild(p); } }, 4000)</script>"
)
FAR = "\n\n".join(
    ["First, a page:", fence("html", "Far", SHORT), PLAIN_WORDS, fence("html", "Grows", GROWS)]
)
STUCK = "\n\n".join(
    [fence("html", "Calm", CALM), fence("html", "Calm too", CALM_TOO), fence("html", "Busy", BUSY)]
)


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_artifacts_shown_and_run_in_place_in_chrome(
    world: World, tmp_path: Path, quiet_loop: None
) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    ada, owner = world.browser(), world.browser()
    ada.sign_in("p-ada")
    owner.sign_in("operator")
    assert ada.get("/api/me").json()["runPreviews"] is True, "on unless turned off"
    chats = {}
    for name, words in (("shown", SHOWN), ("far", FAR), ("stuck", STUCK)):
        chat = ada.new_chat()
        world.gateway.words = [words]
        sent = ada.post(f"/api/chats/{chat}/messages", json={"content": "Make them"})
        assert sent.status_code == 201, sent.text
        ada.wait_answer(chat)
        chats[name] = chat
    _owner_reads(world)

    def cookies(who: object) -> list[dict[str, object]]:
        return [
            {
                "name": c.name,
                "value": c.value,
                "url": world.workbench,
                "httpOnly": True,
                "sameSite": "Lax",
            }
            for c in who.http.cookies.jar  # type: ignore[attr-defined]
        ]

    shots = Path(os.getenv("WORKBENCH_SHOTS") or tmp_path / "shots")
    cfg = tmp_path / "browser.json"
    cfg.write_text(
        json.dumps(
            {
                "url": world.workbench,
                "playwright": os.environ["WORKBENCH_PLAYWRIGHT"],
                "chrome": os.getenv(
                    "WORKBENCH_CHROME", "C:/Program Files/Google/Chrome/Application/chrome.exe"
                ),
                "cookies": cookies(ada),
                "secret": ada.secret,
                "owner": {"cookies": cookies(owner), "secret": owner.secret},
                "chats": chats,
                "shots": str(shots),
            }
        ),
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["node", str(root / "web/scripts/artifacts-inline-browser.mjs"), str(cfg)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=420,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(f"the browser run did not end:\n{exc.stdout}\n{exc.stderr}") from None
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
    # The switch the browser turned off and on again is kept, on.
    assert ada.get("/api/me").json()["runPreviews"] is True
