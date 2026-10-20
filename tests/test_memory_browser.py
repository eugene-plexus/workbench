"""Memory in Chrome (MR1): the Memory page, the chip and its drawer, the toast
and Remember this, against the real app. Opt-in, like the other browser
checks; set WORKBENCH_SHOTS to keep the pictures.

The script (`web/scripts/memory-browser.mjs`) is what a person does. This test
is what the model was sent while they did it: the same words the drawer showed,
in the one system message and the same bytes every turn, and nothing when
memory was off, when the words were refused, or when the model runs outside the
person's machines."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from .conftest import MODEL, World, chat_model

WORD = "HELIOTROPE"
OUTSIDE = "outside/gpt"

#: What the person says, in order. The script says them; this test finds the
#: request each one made by its words.
SAID = {
    "remember": f"Remember that my code word is {WORD}",
    "card": "Remember that my card number is 4242 4242 4242 4242",
    "cat": "Remember that my cat is called Biscuit",
    "cat2": "Remember that my other cat is called Marmalade",
    "forget": "Forget my cat",
    "forget2": "Forget the cat",
    "ask": "What is my code word?",
    "french": "And in French?",
    "long": "I like to be told the reasons as well as the answers, " * 6 + "and nothing else.",
    "outside": "Say my code word.",
    "pictures": "What do you know about me?",
}

#: What the pictures of the page show: every kind, a pinned note, a note from
#: each of two chats, one written on the page.
NOTES: list[dict[str, Any]] = [
    {"text": "I use metric units", "kind": "fact", "pinned": True},
    {"text": "Keep answers short, and give the number first", "kind": "instruction"},
    {"text": "I live in Lisbon", "kind": "fact", "pinned": True},
    {"text": "I am building a birdhouse out of cedar", "kind": "project", "chat": "older"},
    {"text": "I prefer Python to JavaScript", "kind": "preference", "chat": "newer"},
    {"text": "My flight to Denver leaves on March 3", "kind": "event"},
]


def system_of(body: dict[str, Any]) -> str:
    """The one system message of a request, or "" when it has none."""
    first = body["messages"][0]
    return str(first["content"]) if first["role"] == "system" else ""


def said_in(body: dict[str, Any]) -> str:
    """The text of a request's newest message: relevant memory (MV1) comes first in it,
    then the person's words."""
    content = body["messages"][-1]["content"]
    return content if isinstance(content, str) else json.dumps(content)


def relevant_of(body: dict[str, Any]) -> str:
    """The relevant-memory block the newest message carries (`<memory-XXXXXXXX>...`), or ""."""
    text = said_in(body)
    start = text.find("<memory-")
    end = text.find("</memory-")
    return text[start : text.index(">", end) + 1] if start >= 0 and end > start else ""


def asked(requests: list[dict[str, Any]], words: str) -> dict[str, Any]:
    """The one request whose newest message ends in these words."""
    found = [
        body
        for body in requests
        if body["messages"][-1]["role"] == "user" and said_in(body).endswith(words)
    ]
    assert len(found) == 1, (words, len(found))
    return found[0]


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_memory_in_chrome(world: World, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    # A second chat model, which runs outside the person's machines.
    world.gateway.more_chat_models = [
        chat_model(OUTSIDE, locality="external", owned_by="openrouter")
    ]
    person = world.browser()
    person.sign_in("p-ada")
    shots = Path(os.getenv("WORKBENCH_SHOTS") or tmp_path / "shots")
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
                "word": WORD,
                "said": SAID,
                "outside": OUTSIDE,
                "notes": NOTES,
                "shots": str(shots),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/memory-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout

    # What the browser said the model was given is what the model was given.
    shown = next(
        json.loads(line.removeprefix("DRAWER_TEXT:"))
        for line in result.stdout.splitlines()
        if line.startswith("DRAWER_TEXT:")
    )
    sent = world.gateway.requests
    first = asked(sent, SAID["remember"])
    # 1. "Remember that ..." kept the note before the request was built: the note was not
    # pinned, so it went as relevant memory in that turn's own message (MV1), not in the
    # system message (design section 6).
    assert system_of(first) == ""
    assert WORD in relevant_of(first)
    # 2. The chat began with no pinned notes, so every turn of it has the same (empty) system
    # message; a note kept later reaches a turn only as relevant memory, in that turn.
    for later in ("card", "cat", "cat2", "forget", "forget2"):
        assert system_of(asked(sent, SAID[later])) == "", later
    assert all("Biscuit" not in system_of(body) for body in sent)
    assert all("Marmalade" not in system_of(body) for body in sent)
    # 3. Words that were refused (a card number) are in no system message and no memory block.
    assert all("4242" not in system_of(body) for body in sent)
    assert all("4242" not in relevant_of(body) for body in sent)
    # 4. The new chat, after the note was pinned, sent it once in the system message, and the
    # drawer showed those very words; a pinned note is not also sent as relevant memory.
    chat2 = asked(sent, SAID["ask"])
    assert shown in system_of(chat2)
    assert chat2["model"] == MODEL
    assert "Biscuit" not in system_of(chat2)
    assert WORD not in relevant_of(chat2)
    # 5. Memory off for the chat: no notes, no system message, and no memory block at all.
    for off in ("french", "long"):
        body = asked(sent, SAID[off])
        assert system_of(body) == "", off
        assert relevant_of(body) == "", off
        assert WORD not in json.dumps(body), off
    # 6. The outside model was sent the note once, after "Send for this chat".
    outside = [body for body in sent if body["model"] == OUTSIDE]
    assert len(outside) == 1
    assert outside[0] is asked(sent, SAID["outside"])
    assert WORD in system_of(outside[0])
    # 7. Every note the pictures show went to the chat they were made in: the pinned ones and the
    # instruction in its system message, the others as relevant memory in the turn.
    body = asked(sent, SAID["pictures"])
    pictured = system_of(body) + relevant_of(body)
    for note in NOTES:
        assert note["text"] in pictured, note["text"]
        # An instruction is always used, as a pinned note is.
        always = bool(note.get("pinned")) or note["kind"] == "instruction"
        assert (note["text"] in system_of(body)) == always, note["text"]
