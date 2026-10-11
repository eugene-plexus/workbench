"""Documents in Chrome (MR2): attaching a file, watching it be read, choosing how
much of a long one to read, and the files named under an answer, against the real
app. Opt-in, like the other browser checks; set WORKBENCH_SHOTS to keep the
pictures.

The script (`web/scripts/documents-browser.mjs`) is what a person does. This test
is what the model was sent while they did it: the code word that is only on page
3 of the PDF reached the model, a part chosen was the only part sent, and a file
that was refused or never attached reached nobody."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from . import documents as docs
from .conftest import MODEL, World, chat_model

SMALL = "small-window-model"

#: What the person says, in order. The script says them; this test finds the
#: request each one made by its words.
SAID = {
    "ask": "What is the code word?",
    "summarize": "Summarize the long file.",
}


def text_of(content: Any) -> str:
    """A message's text, whether it is a string or a list of parts."""
    if isinstance(content, str):
        return content
    return "\n".join(str(part.get("text", "")) for part in content if part.get("type") == "text")


def asked(requests: list[dict[str, Any]], words: str) -> dict[str, Any]:
    """The one request whose newest message ends with these words."""
    found = [
        body
        for body in requests
        if body["messages"][-1]["role"] == "user"
        and text_of(body["messages"][-1]["content"]).rstrip().endswith(words)
    ]
    assert len(found) == 1, (words, len(found))
    return found[0]


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_documents_in_chrome(world: World, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    # A second chat model with a small window, so a long file does not fit it.
    world.gateway.more_chat_models = [chat_model(SMALL, locality="local", window=4096)]
    # What the model says when it has read the third page of the PDF.
    world.gateway.words = ["The code word is ", docs.CODE_WORD, "."]

    files = tmp_path / "files"
    files.mkdir()
    (files / "report.pdf").write_bytes(docs.three_pages())
    # About 9,800 tokens: longer than the default, and it fits the 32,768 window.
    (files / "long-12-pages.pdf").write_bytes(docs.long_pdf(12))
    # About 24,500 tokens: more than the 32,768 window leaves room for.
    (files / "long-30-pages.pdf").write_bytes(docs.long_pdf(30))
    (files / "scan.pdf").write_bytes(docs.pdf_pictures(2))
    (files / "field-notes.md").write_bytes(docs.MARKDOWN)
    (files / "quarterly-report.docx").write_bytes(docs.docx())
    (files / "prices.csv").write_bytes(docs.CSV)

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
                "word": docs.CODE_WORD,
                "said": SAID,
                "model": MODEL,
                "small": SMALL,
                "files": {path.name: str(path) for path in files.iterdir()},
                "shots": str(shots),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/documents-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        timeout=420,
    )
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout

    sent = world.gateway.requests
    # 1. The code word is only on page 3 of the PDF, and the model was given that page.
    first = text_of(asked(sent, SAID["ask"])["messages"][-1]["content"])
    assert docs.CODE_WORD in first
    assert "[page 3]" in first
    assert re.search(r'<document-[0-9a-f]{8} name="report.pdf" type="pdf"', first)
    # 2. The file is sent as text to a model that cannot read a PDF itself: no file part.
    body = asked(sent, SAID["ask"])
    assert isinstance(body["messages"][-1]["content"], (str, list))
    assert "application/pdf" not in json.dumps(body)
    # 3. The part that was chosen is the only part of the long file that was sent.
    second = text_of(asked(sent, SAID["summarize"])["messages"][-1]["content"])
    assert 'name="long-12-pages.pdf"' in second
    assert 'read="first part"' in second
    assert "Section 1." in second
    assert "Section 12." not in second
    # 4. What the first turn carried is carried again, the same bytes.
    earlier = [text_of(m["content"]) for m in asked(sent, SAID["summarize"])["messages"][:-1]]
    assert first in earlier
    # 5. A file that was refused, or never sent, reached no model at all.
    everything = json.dumps(sent)
    assert "scan.pdf" not in everything
    assert "long-30-pages.pdf" not in everything
