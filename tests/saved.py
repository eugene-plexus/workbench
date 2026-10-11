"""Helpers for the saved-documents tests: a person's browser, files to keep, and
reading what the gateway was sent. Real requests to a real Workbench; nothing here
stands in for it.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx

from eugene_plexus_workbench import files

from .conftest import ADMIN_TOKEN, MODEL, Browser, World

#: Made-up words no model knows; the passage that holds one is what a search must find.
RARE = "zzyzxquux"
SECOND = "plumbago"
THIRD = "quillwort"
TERMINAL = ("ready", "skipped", "failed")
BLOCK = re.compile(r"<documents-([0-9a-f]{8})>\n(.*?)\n</documents-\1>", re.DOTALL)
PASSAGE = re.compile(
    r'<document-([0-9a-f]{8}) n="(\d+)" name="([^"]*)"((?: [a-z]+="[^"]*")*)>\n(.*?)\n</document-\1>',
    re.DOTALL,
)


def start(world: World, person: str = "p-ada") -> Browser:
    browser = world.browser()
    browser.sign_in(person)
    return browser


def md(*paragraphs: str, title: str = "Notes") -> bytes:
    """A Markdown file: a title and these paragraphs."""
    return (f"# {title}\n\n" + "\n\n".join(paragraphs) + "\n").encode()


def survey_pdf(word: str, *, page: int = 4, pages: int = 5, topic: str = "lighthouse") -> bytes:
    """A PDF of full pages, each its own passage, with a code word on one page only."""
    sheets = []
    for n in range(1, pages + 1):
        lines = [f"Page {n} of the harbour survey records ordinary observations in plain words."]
        lines += [
            f"Observation {n}.{i} describes the weather, the tide and the gulls." for i in range(21)
        ]
        if n == page:
            lines.insert(5, f"The code word for the {topic} inspection is {word}.")
        sheets.append("\n".join(lines))
    return docs_pdf(sheets)


def docs_pdf(pages: list[str]) -> bytes:
    from . import documents as docs

    return docs.pdf(pages)


def add(person: Browser, *named: tuple[Any, ...], media: str = "application/octet-stream") -> Any:
    """Add files: each is `(name, bytes)` or `(name, bytes, the type a browser would say)`."""
    return person.post(
        "/api/documents",
        files=[("files", (n[0], n[1], n[2] if len(n) > 2 else media)) for n in named],
    )


def listing(person: Browser) -> dict[str, Any]:
    found = person.get("/api/documents")
    assert found.status_code == 200, found.text
    return dict(found.json())


def by_name(person: Browser, name: str) -> dict[str, Any]:
    return next(d for d in listing(person)["documents"] if d["name"] == name)


def settle(person: Browser, seconds: float = 90) -> dict[str, Any]:
    """The listing once every document has stopped being read (any end)."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        found = listing(person)
        if all(d["state"] in TERMINAL for d in found["documents"]):
            return found
        time.sleep(0.05)
    raise AssertionError("the documents were not read in time")


def keep(
    person: Browser, *named: tuple[Any, ...], media: str = "application/octet-stream"
) -> dict[str, Any]:
    """Add files and wait until they are read: the listing."""
    made = add(person, *named, media=media)
    assert made.status_code == 201, made.text
    return settle(person)


def settings(world: World, **values: Any) -> None:
    changed = httpx.patch(
        f"{world.workbench}/v1/config",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json=values,
        trust_env=False,
    )
    assert changed.status_code == 200 and not changed.json()["rejected"], changed.text


def chat_on(person: Browser, model: str | None = None, **more: Any) -> str:
    """A new chat with Documents turned on."""
    chat = str(person.post("/api/chats", json={"model": model or MODEL}).json()["id"])
    changed = person.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on", **more}})
    assert changed.status_code == 200, changed.text
    return chat


def send(person: Browser, chat: str, content: str, **body: Any) -> Any:
    return person.post(f"/api/chats/{chat}/messages", json={"content": content, **body})


def ask(person: Browser, chat: str, content: str, **body: Any) -> dict[str, Any]:
    sent = send(person, chat, content, **body)
    assert sent.status_code == 201, sent.text
    return person.wait_answer(chat)


def last(world: World) -> list[dict[str, Any]]:
    return list(world.gateway.requests[-1]["messages"])


def user_text(message: dict[str, Any]) -> str:
    content = message["content"]
    return content if isinstance(content, str) else str(content[0]["text"])


def block_of(world: World) -> tuple[str, str, list[tuple[Any, ...]]]:
    """The passages block in the last request's last user message: its suffix, its
    text, and each passage's (suffix, number, name, attributes, words)."""
    text = user_text(last(world)[-1])
    found = BLOCK.search(text)
    assert found is not None, f"no passages block in: {text[:300]!r}"
    return found.group(1), found.group(0), [m.groups() for m in PASSAGE.finditer(found.group(0))]


def has_block(world: World) -> bool:
    return "<documents-" in json.dumps(world.gateway.requests[-1])


def knowledge_files(world: World) -> list[Path]:
    root = world.data / "knowledge"
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def index_of(world: World, sub: str = "p-ada") -> Path:
    return world.data / "knowledge" / files.person_key(sub) / "index.sqlite3"


def raw(world: World, sub: str = "p-ada") -> sqlite3.Connection:
    return sqlite3.connect(index_of(world, sub))


def wait_for(condition: Any, seconds: float = 60.0, every: float = 0.05) -> bool:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        if condition():
            return True
        time.sleep(every)
    return bool(condition())
