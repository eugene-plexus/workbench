"""Citations, charts and tables in Chrome, under the page's own headers
(workbench-rich-content-roadmap.md RC4). Opt-in, like the other browser
checks; set WORKBENCH_SHOTS to keep the pictures."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench.web import CSP

from .conftest import World
from .saved import ask, chat_on, keep, md, start
from .test_tool_content import mixed, run_mixed  # noqa: F401

PAGE = "https://example.org/page"
OTHER = "https://example.org/other"
UNPLACED = "https://example.org/unplaced"
THIRD = "https://example.org/third"
# The first version: a link, an emoji, then an address, each cited in place.
CITED = ["Read ", f"[the page]({PAGE})", " 😀 and ", OTHER, "/ too."]
# The version tried again: one address of its own.
RETRIED = ["Only ", THIRD, " here."]


def place(words: list[str], cited: str, url: str, title: str) -> dict[str, Any]:
    text = "".join(words)
    start = text.index(cited)
    return {"url": url, "title": title, "start_index": start, "end_index": start + len(cited)}


def fence(language: str, body: Any) -> str:
    return f"```{language}\n{json.dumps(body)}\n```"


BAR = {
    "type": "bar",
    "title": "Tokens a second",
    "x": {"label": "Model"},
    "y": {"label": "tok/s"},
    "series": [
        {"name": "Before", "points": [["=cmd()", 40], ["small", 80], ["large", 30]]},
        {"name": "After", "points": [["=cmd()", 55], ["small", 95], ["large", 41]]},
    ],
}
TABLE = {"columns": ["Run", "Seconds"], "rows": [["b", 2.5], ["a", 9], ["c", 1]]}
DRAWN = (
    "A chart:\n\n"
    + fence("chart", BAR)
    + "\n\nA table:\n\n"
    + fence("chart", TABLE)
    + "\n\nOne that cannot be drawn:\n\n"
    + fence("chart", {"type": "pie", "series": []})
)


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_citations_charts_and_tables_in_chrome(
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
    # A tool's chart, through an answer (RC3's server, its `plot` tool).
    _, ada, plotted, call = run_mixed(world, mixed, "plot")
    assert any(b["type"] == "data" for b in call["content"]), call

    # A searched answer citing two pages in place and one with no place; tried again.
    world.gateway.mode = "answer"
    world.gateway.tool_name = None
    cited = ada.new_chat()
    world.gateway.words = CITED
    world.gateway.citations = [
        place(CITED, f"[the page]({PAGE})", PAGE, "The page"),
        place(CITED, OTHER, OTHER + "/", "Another page"),
        {"url": UNPLACED, "title": "A page with no place"},
    ]
    sent = ada.post(f"/api/chats/{cited}/messages", json={"content": "Find it", "search": True})
    assert sent.status_code == 201, sent.text
    first = ada.wait_answer(cited)
    assert {s["url"] for s in first["sources"] if s.get("marks")} == {PAGE, OTHER + "/"}
    world.gateway.words = RETRIED
    world.gateway.citations = [place(RETRIED, THIRD, THIRD, "A third page")]
    again = ada.post(f"/api/chats/{cited}/retry")
    assert again.status_code == 201, again.text
    ada.wait_answer(cited)
    world.gateway.citations = None

    # An answer citing a passage of a saved document, and a number no passage has.
    person = start(world)
    keep(person, ("tides.md", md("The tide at the harbour rises twice a day.", title="Tides")))
    passages = chat_on(person)
    world.gateway.words = ["It rises twice a day [1]; ", "the almanac [9] agrees."]
    answer = ask(person, passages, "How often does the tide rise at the harbour?")
    assert any(s.get("rank") == 1 for s in answer["sources"]), answer["sources"]

    # Charts and tables an answer asked for.
    drawn = ada.new_chat()
    world.gateway.words = [DRAWN]
    ada.post(f"/api/chats/{drawn}/messages", json={"content": "Draw it"})
    ada.wait_answer(drawn)

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
                "chats": {
                    "cited": cited,
                    "passages": passages,
                    "drawn": drawn,
                    "plotted": plotted,
                },
                "shots": str(shots),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/structured-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
