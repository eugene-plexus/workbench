"""Folders in Chrome (MR7): the owner grants a folder of notes to a person, who adds it on the
Documents screen, sees it checked and its files read (the counts say how far, while a search model
holds its answers), reads each file's reason and what was left out and why, asks a question that a
file of the folder answers with the Sources naming it by folder and path, edits that file and chooses
Sync now, writes a pattern in the folder's rules (and is refused, in words, for one the server cannot
use), removes the folder, adds it again, and is told by the owner's withdrawal that the folder is
Withdrawn, and dismisses it; against the real app. Opt-in, like the other browser checks; set
WORKBENCH_SHOTS to keep the pictures.

The script (`web/scripts/folders-library-browser.mjs`) is what a person does. This test is what the
model was sent while they did it, and what was left on the disk: the code word that is only in the
folder's file reached the model as a passage that names its folder and file, the edited file's new
word reached it and the old one never did again, a chat after the folder was removed was sent no
passage, nothing of the folder is left in the person's own files, and the granted folder is exactly
as the person's editor left it (Workbench reads it and never writes a byte)."""

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
from .folderdocs import folders_of, grant, snapshot, write
from .meaning import EMBEDDER, no_models, quick  # noqa: F401
from .saved import ask, chat_on, listing, md, settings, start, survey_pdf
from .test_library_browser import asked, text_of

#: What the person says, in order. The script says them; this test finds the request each one made.
SAID = {
    "ask": "What is the code word for the lighthouse inspection?",
    "again": "What is the new code word for the lighthouse inspection?",
}
EDITED_WORD = "SEAGRASS-2210"
#: The Word file's and the survey's own code words: in no question, and in the folder only.
WORD_FILE = "QUILLWORT-31"
SURVEY_WORD = "LAMPREY-8801"
NAME = "Work notes"


def edited_text() -> str:
    """The file the person's editor saves over `harbour-notes.md`: a new word, and a longer file."""
    return md(
        f"The code word for the lighthouse inspection is {EDITED_WORD}.",
        "The tide table is on the wall.",
        "It was changed on the second visit.",
        title="Harbour notes",
    ).decode()


def make_folder(root: Path) -> None:
    """A folder of notes as a person keeps one: documents, a picture that is not a document, and
    what a program or an operating system leaves in it (that is left out, each for its own rule)."""
    write(
        root,
        "harbour-notes.md",
        md(
            f"The code word for the lighthouse inspection is {docs.CODE_WORD}.",
            "The tide table is on the wall.",
            title="Harbour notes",
        ),
    )
    write(
        root,
        "docs/field-notes.md",
        md(
            "The first part has nothing in it.",
            "The kettle is in the cupboard.",
            title="Field notes",
        ),
    )
    write(root, "docs/quarterly-report.docx", docs.docx(code_word=WORD_FILE))
    # Five pages: five passages, which the search model's held answers keep from being finished.
    write(root, "docs/field-survey.pdf", survey_pdf(SURVEY_WORD, page=2, topic="mooring"))
    write(root, "picture.png", PNG)
    # Left out: hidden, a program's own folders, a .gitignore's, a temporary file, a file an
    # operating system makes.
    write(root, ".env", "A=1\n")
    write(root, ".gitignore", "build/\n")
    write(root, ".git/config", "[core]\n")
    write(root, "node_modules/leftpad/index.js", "module.exports = 1;\n")
    write(root, "build/out.js", "var a = 1;\n")
    write(root, "scratch.tmp", "temporary\n")
    write(root, "Thumbs.db", b"\x00")


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_folders_in_chrome(
    world: World,
    tmp_path: Path,
    notes: Path,
    quick: None,  # noqa: F811
    quiet_loop: None,
) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    # What the model says when it has been shown the page, citing the passage by its number.
    world.gateway.words = ["The code word is ", docs.CODE_WORD, " [1]."]
    # No search model is set up to begin with; the script starts one through the gateway's hand
    # (`/__fake/search-models`) that holds its answers, so the files' reading can be seen.
    no_models(world)
    settings(world, documentsEmbedRequestsPerMinute=6000)
    make_folder(notes)

    person = start(world, "p-ada")
    owner = start(world, "operator")
    grant_id = grant(world, owner, notes, subject="p-ada", name=NAME)
    left_alone = snapshot(notes)
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
                "folder": {"path": str(notes), "name": NAME, "grantId": grant_id},
                "edited": {"text": edited_text(), "word": EDITED_WORD},
                "shots": str(shots),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/folders-library-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        timeout=900,
    )
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout

    sent = world.gateway.requests
    # 1. The question was asked of the model by the chat, with the passage that holds the code word:
    #    it names its folder and its file, like any source.
    first = asked(sent, SAID["ask"])
    assert first, "the question was asked of the model by the chat"
    block = text_of(first[0]["messages"][-1]["content"])
    assert docs.CODE_WORD in block
    assert re.search(
        rf'<document-[0-9a-f]{{8}} n="1" name="{re.escape(NAME)}/harbour-notes\.md"', block
    ), block[:600]
    assert block.rstrip().endswith(SAID["ask"]), "the person's words come last"
    # The files that are left out reached the model in no passage: nothing of a hidden file, a
    # program's folder, a temporary file or the picture was read.
    for left_out in ("A=1", "module.exports", "var a = 1", "temporary"):
        assert left_out not in json.dumps(sent), left_out
    # 2. After the edit and Sync now, the new word answers and the old one is never found again.
    again = asked(sent, SAID["again"])
    assert again, "the second question was asked of the model by the chat"
    said = text_of(again[0]["messages"][-1]["content"])
    assert EDITED_WORD in said and docs.CODE_WORD not in said
    assert f'name="{NAME}/harbour-notes.md"' in said
    # 3. The folder was removed, added again, and withdrawn by the owner, and dismissed: nothing of
    #    it is the person's any more, in the list or in a chat.
    assert folders_of(person)["folders"] == []
    assert folders_of(person)["grants"] == []
    assert listing(person)["documents"] == []
    chat = chat_on(person)
    ask(person, chat, SAID["ask"])
    after = json.dumps(world.gateway.requests[-1])
    assert docs.CODE_WORD not in after and "harbour-notes" not in after and EDITED_WORD not in after
    # Nothing of the folder is left on disk, in any file of the person's own folder.
    kept = [p for p in (world.data / "knowledge").rglob("*") if p.is_file()]
    assert kept, "the person's own folder exists"
    for word in (docs.CODE_WORD, EDITED_WORD, WORD_FILE, SURVEY_WORD):
        assert not any(word.encode() in p.read_bytes() for p in kept), word
    # 4. Workbench reads a folder and never writes in it: every file and folder is as it was left,
    #    to the byte and to the nanosecond, but for the file the person edited themselves.
    now = snapshot(notes)
    edited = "harbour-notes.md"
    assert now.pop(edited)[0] == edited_text().encode()
    left_alone.pop(edited)
    assert now == left_alone, "Workbench changed something in the granted folder"
