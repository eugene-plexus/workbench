"""The page and the server agree about documents (MR2).

Workbench's own API is private and has no schema file to generate the page's types
from, so the page's TypeScript (`web/src/lib/types.ts`, `documents.ts`) is written by
hand. These read it as text and hold it to what the server really answers with, so a
field the server renames, adds or drops, or a kind of file it starts to read, is a
failure here and not a blank spot on a screen."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from eugene_plexus_workbench import context, documents_api, files
from eugene_plexus_workbench.store import FileRecord

LIB = Path(__file__).resolve().parents[1] / "web" / "src" / "lib"


def interface(name: str) -> set[str]:
    """The fields the page's TypeScript interface `name` declares (types.ts)."""
    text = (LIB / "types.ts").read_text(encoding="utf-8")
    found = re.search(rf"export interface {name}(?: extends \w+)? \{{\n(.*?)\n\}}", text, re.DOTALL)
    assert found, f"types.ts has no interface {name}"
    return set(re.findall(r"^  (\w+)\??:", found.group(1), re.MULTILINE))


def array(name: str) -> list[str]:
    """The strings of the page's `export const NAME = [...] as const` (documents.ts)."""
    text = (LIB / "documents.ts").read_text(encoding="utf-8")
    found = re.search(rf"export const {name} = \[(.*?)\] as const;", text, re.DOTALL)
    assert found, f"documents.ts has no list {name}"
    return re.findall(r'"([^"]+)"', found.group(1))


# --------------------------------------------------------------------------- #
# what the file picker offers
# --------------------------------------------------------------------------- #


def test_the_file_picker_offers_every_kind_of_text_the_server_reads_by_name() -> None:
    """Names Workbench reads as Markdown, CSV, HTML, code or data are what the
    picker is asked to show: a name the server learns and the page does not
    is a file a person cannot pick, and one the page lists and the server does
    not read would be a promise it does not keep."""
    server = {
        *files._MARKDOWN,
        *files._HTML,
        *files.CODE,
        *files.DATA,
        ".csv",
        ".tsv",
        ".tab",
    }
    page = array("KNOWN_EXTENSIONS")
    assert len(page) == len(set(page)), "an extension is listed twice"
    assert set(page) == server, (sorted(set(page) - server), sorted(server - set(page)))
    for plain in array("PLAIN_EXTENSIONS"):
        assert plain not in server


def test_the_file_picker_is_asked_for_every_type_it_was_and_for_word_and_text() -> None:
    text = (LIB / "documents.ts").read_text(encoding="utf-8")
    accept = re.search(r"export const ACCEPT = \[(.*?)\]\.join", text, re.DOTALL)
    assert accept
    listed = re.findall(r'"([^"]+)"', accept.group(1))
    for media in (*files.KINDS, "audio/x-wav"):
        assert media in listed, media
    # Word is asked for by its type (a constant) and by its extension.
    word = re.search(r'const DOCX = "([^"]+)";', text)
    assert word and word.group(1) == files.DOCX
    assert "DOCX" in accept.group(1) and ".docx" in listed and "text/*" in listed


# --------------------------------------------------------------------------- #
# what each answer is made of
# --------------------------------------------------------------------------- #

READING = {
    "state": "ready",
    "kind": "pdf",
    "pages": 12,
    "tokens": 9800,
    "chars": 29400,
    "headings": 0,
    "partTokens": [817] * 12,
    "nonce": "abcd1234",
    "notes": [],
    "truncated": False,
    "startedAt": 1.0,
    "finishedAt": 2.0,
}


def record(reading: dict[str, Any] | None = READING) -> FileRecord:
    return FileRecord(
        id="f1",
        owner="p-ada",
        chat_id="c1",
        name="report.pdf",
        media_type=files.PDF,
        size=1000,
        created_at=1.0,
        reading=reading,
    )


def decided(
    reading: dict[str, Any],
    window: int | None = 32768,
    *,
    choice: context.Choice | None = None,
    file_input: bool = False,
) -> context.Decision:
    item = context.Attached(
        id="f1",
        name="report.pdf",
        size=1000,
        media_type=files.PDF,
        current=True,
        reading=reading,
        choice=choice,
        nonce="abcd1234",
    )
    plan = context.plan(
        [item],
        model=context.Model(window=window, file_input=file_input),
        room=context.Room(history=4),
        load=None,
        render=False,
    )
    return plan.decisions[0]


def test_a_reading_has_the_fields_the_page_reads() -> None:
    view = context.reading_view(record())
    assert view is not None
    assert set(view) == interface("Reading")


def test_a_decision_has_the_fields_the_page_reads() -> None:
    # Whole, to choose (with its offers), a part, and one that is not read yet.
    whole = {**READING, "pages": 3, "tokens": 77, "partTokens": [25, 26, 26]}
    views = [
        decided(whole).view(),
        decided(READING).view(),
        decided({**READING, "state": "reading", "tokens": None}).view(),
        decided({"state": "skipped", "kind": "pdf", "message": "Skipped."}).view(),
    ]
    assert {v["kind"] for v in views} == {"whole", "choose", "pending", "unreadable"}
    page = interface("FileDecision")
    for view in views:
        # `overhead` is only there once a file has been read (it is optional on the page).
        assert set(view) <= page, set(view) - page
        assert page - {"overhead"} <= set(view), page - {"overhead"} - set(view)
    chosen = next(v for v in views if v["kind"] == "choose")
    assert [o["action"] for o in chosen["offers"]] == ["readAll", "readFirst"]
    assert set(chosen["offers"][1]) == {"action", "tokens", "where", "cut"}
    assert set(chosen["offers"][0]) == {"action", "tokens", "where"}
    assert set(chosen["offers"][1]["cut"]) == interface("Coverage")
    assert set(chosen["room"]) == {"history", "memory", "documents", "reserve", "remaining"}


def test_every_kind_of_decision_is_one_the_page_has_words_for() -> None:
    text = (LIB / "types.ts").read_text(encoding="utf-8")
    declared = re.search(r"export type DecisionKind =(.*?);", text, re.DOTALL)
    assert declared
    kinds = set(re.findall(r'"(\w+)"', declared.group(1)))
    reached = {
        decided({**READING, "pages": 3, "tokens": 77, "partTokens": [25, 26, 26]}).kind,
        decided(READING).kind,
        decided({**READING, "pages": 30, "tokens": 24524, "partTokens": [817] * 30}, 4096).kind,
        decided({**READING, "state": "reading"}).kind,
        decided({"state": "failed", "kind": "pdf", "message": "Failed."}).kind,
        decided(READING, 2048).kind,
        decided(READING, choice=context.Choice("first", 3000)).kind,
        decided(READING, file_input=True).kind,
    }
    assert reached == kinds, sorted(reached ^ kinds)


def test_a_file_in_the_list_has_the_fields_the_page_reads() -> None:
    item = documents_api._item(record(), sent=False, decision=decided(READING))
    assert set(item) == interface("ChatFile")
    unread = documents_api._item(record(None), sent=True, decision=None)
    assert set(unread) == interface("ChatFile")
    assert unread["reading"] is None and unread["decision"] is None


def test_a_source_has_the_fields_the_page_reads() -> None:
    whole = decided({**READING, "pages": 3, "tokens": 77, "partTokens": [25, 26, 26]})
    item = context.Attached(
        id="f1", name="report.pdf", size=1000, media_type=files.PDF, current=True
    )
    entry = context.source_entry(whole, item, {**READING, "pages": 3})
    assert set(entry) == interface("DocumentSource") - {"fraction"}
    # `fraction` is said only of a part.
    part = decided(READING).offers[1]["cut"]
    cut = context.Decision(
        id="f1", name="report.pdf", kind="partial", send=True, message="", cut=None
    )
    assert "fraction" not in context.source_entry(cut, item, READING)
    assert part["fraction"] < 1
    pieces = context.Cut(tokens=100, whole=False, pages=(1, 2), total_pages=12, fraction=0.2)
    cut.cut = pieces
    assert set(context.source_entry(cut, item, READING)) == interface("DocumentSource")
