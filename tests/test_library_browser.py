"""Saved documents in Chrome (MR4), and search by meaning (MR5): adding files in Documents,
watching them be read, trying a question without a model, turning Documents on in a chat, and
the Sources under the answer, against the real app; then a search model starts, as the owner
would start one in Eugene's console, and the same screens say it: meaning and words, each
document given its meaning, a passage found by what it says, and a search model that stops,
named. Opt-in, like the other browser checks; set WORKBENCH_SHOTS to keep the pictures.

The script (`web/scripts/library-browser.mjs`) is what a person does. This test is what
the model was sent while they did it: the code word that is only on page 4 of the PDF
reached the model as a passage that names its file and page, Test a question asked no
model at all, a chat that answers only from the documents asked none when nothing
matched, a file that was removed reached nobody afterwards, and what a question found by
meaning is what the model was shown, beside the search model being asked for the passages
of the files and for the question, and for nothing else."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from . import documents as docs
from .conftest import MODEL, PNG, World
from .meaning import (
    ARRIVALS,
    EMBEDDER,
    asked_for_passages,
    asked_for_questions,
    no_models,
    quick,  # noqa: F401
)
from .saved import ask, listing, md, settings, survey_pdf

#: What the person says, in order. The script says them; this test finds the request each
#: one made by its words.
SAID = {
    "ask": "What is the code word for the lighthouse inspection?",
    "nothing": "Tell me about the zebra giraffe okapi.",
    # None of its words is in any file, and it means what a passage of the harbour office says.
    "meaning": "Where is the paperwork for the ferry filed?",
}


def text_of(content: Any) -> str:
    """A message's text, whether it is a string or a list of parts."""
    if isinstance(content, str):
        return content
    return "\n".join(str(part.get("text", "")) for part in content if part.get("type") == "text")


def asked(requests: list[dict[str, Any]], words: str) -> list[dict[str, Any]]:
    """The requests whose newest message ends with these words."""
    return [
        body
        for body in requests
        if body["messages"][-1]["role"] == "user"
        and text_of(body["messages"][-1]["content"]).rstrip().endswith(words)
    ]


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_saved_documents_in_chrome(
    world: World,
    tmp_path: Path,
    quick: None,  # noqa: F811
) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    # What the model says when it has been shown the page, citing the passage by its number.
    world.gateway.words = ["The code word is ", docs.CODE_WORD, " [1]."]
    # No search model is set up to begin with: documents are searched by words, and the screen says
    # so. The script starts one through the gateway's hand (`/__fake/search-models`) half way.
    no_models(world)
    settings(world, documentsEmbedRequestsPerMinute=6000)

    files = tmp_path / "files"
    files.mkdir()
    # The code word is only on page 4 of five.
    (files / "harbour-survey.pdf").write_bytes(survey_pdf(docs.CODE_WORD, page=4))
    (files / "field-notes.md").write_bytes(
        md(
            "The first part has nothing in it.",
            "The tide table is on the wall.",
            title="Field notes",
        )
    )
    (files / "quarterly-report.docx").write_bytes(docs.docx(code_word="PLUMBAGO-77"))
    (files / "picture.png").write_bytes(PNG)
    # A passage that says what the question asks, and has none of its words.
    (files / "harbour-office.md").write_bytes(md(ARRIVALS, title="Harbour office"))

    person = world.browser()
    person.sign_in("p-ada")
    owner = world.browser()
    owner.sign_in("operator")
    shots = Path(os.getenv("WORKBENCH_SHOTS") or tmp_path / "shots")
    cfg = tmp_path / "browser.json"

    def cookies(who: Any) -> list[dict[str, Any]]:
        return [
            {
                "name": c.name,
                "value": c.value,
                "url": world.workbench,
                "httpOnly": True,
                "sameSite": "Lax",
            }
            for c in who.http.cookies.jar
        ]

    cfg.write_text(
        json.dumps(
            {
                "url": world.workbench,
                "secret": person.secret,
                "playwright": os.environ["WORKBENCH_PLAYWRIGHT"],
                "chrome": os.getenv(
                    "WORKBENCH_CHROME", "C:/Program Files/Google/Chrome/Application/chrome.exe"
                ),
                "cookies": cookies(person),
                "owner": {"secret": owner.secret, "cookies": cookies(owner)},
                "gateway": world.gateway.url,
                "searchModel": EMBEDDER,
                "word": docs.CODE_WORD,
                "said": SAID,
                "model": MODEL,
                "files": {path.name: str(path) for path in files.iterdir()},
                "shots": str(shots),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/library-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout

    sent = world.gateway.requests
    # 1. Test a question asked no model: the first request the gateway ever got is the
    #    chat's own question, and it is the only one that carried the passages.
    first = asked(sent, SAID["ask"])
    assert first, "the question was asked of the model by the chat"
    assert sent[0] is first[0], "nothing was asked of a model before the chat's question"
    block = text_of(first[0]["messages"][-1]["content"])
    # 2. The passage that holds the code word is the one that went, and it names its page.
    assert docs.CODE_WORD in block
    assert re.search(r'<document-[0-9a-f]{8} n="1" name="harbour-survey\.pdf" pages="4"', block)
    assert block.rstrip().endswith(SAID["ask"]), "the person's words come last"
    # 3. A chat that answers only from the documents asked no model when nothing matched;
    #    "Ask the model anyway" asked it, with no passages and the person's words alone.
    nothing = asked(sent, SAID["nothing"])
    assert len(nothing) == 1, "the model was asked about the zebra once: by Ask anyway"
    assert "<documents-" not in json.dumps(nothing[0])
    # 4. MR5: a question with none of a passage's words reached the model with that passage,
    #    found by what it says, and named like any other: its file and its heading.
    meaning = asked(sent, SAID["meaning"])
    assert len(meaning) == 1, "the question was asked of the model once, by the chat"
    said = text_of(meaning[0]["messages"][-1]["content"])
    assert ARRIVALS in said and 'name="harbour-office.md"' in said
    # 5. The search model was asked for the passages of the files (sixteen at a time at most)
    #    and for the questions, and for nothing else: not a word a chat said to a model.
    assert any(ARRIVALS in text for text in asked_for_passages(world)), "its passage was embedded"
    questions = asked_for_questions(world)
    assert SAID["meaning"] in questions and any(SAID["ask"] in q for q in questions)
    assert all(len(r["body"]["input"]) <= 16 for r in world.gateway.embed_requests)
    # 6. The PDF was removed: it is gone from the list, and the same question no longer
    #    finds it; the chat sends no passages for it.
    assert sorted(d["name"] for d in listing(person)["documents"]) == [
        "field-notes.md",
        "quarterly-report.docx",
    ]
    chat = str(person.post("/api/chats", json={"model": MODEL}).json()["id"])
    on = person.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on"}})
    assert on.status_code == 200, on.text
    ask(person, chat, SAID["ask"])
    again = json.dumps(world.gateway.requests[-1])
    # The Word file also has a code word, and its passage can match: the survey's cannot.
    assert docs.CODE_WORD not in again and "harbour-survey" not in again
    # Nothing of the removed file is left on disk, in any file of the person's folder.
    kept = [p for p in (world.data / "knowledge").rglob("*") if p.is_file()]
    assert kept and not any(docs.CODE_WORD.encode() in p.read_bytes() for p in kept)
