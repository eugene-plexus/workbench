"""Helpers for the search-by-meaning tests (MR5): a world with a search model, files whose
meaning a test can reason about (`fake_embedding`), and reading what the gateway was asked.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from eugene_plexus_workbench.knowledge import meaning as meaning_module

from .conftest import ADMIN_TOKEN, Browser, World, search_model
from .saved import RARE, listing, md, raw, settings

EMBEDDER = "mr5-embed"
#: A passage about the ships' paperwork, and one that says it in other words: a question
#: about the boats' records has none of either's words and means both.
ARRIVALS = (
    "The harbour master keeps the vessel registry and the logbook of every ship that arrives."
)
LEDGER = "Vessels are entered in the ledger by the clerk on duty."
KITCHEN = "The pantry holds flour, tins of soup and the cook's recipes for the long winter."
FEES = "Berthing fees are paid at the quayside office each Friday."
ODD = f"The {RARE} valve is stored in the shed beside the pump."
#: Has none of the concepts of the passages above, and none of their words.
ELSEWHERE = "What is the airspeed of an unladen swallow?"


def corpus() -> list[tuple[str, bytes]]:
    """Two files: the harbour office's (four sections, each its own passage), and a file
    of one odd passage."""
    harbour = (
        "# Harbour office\n\n"
        f"## Arrivals\n\n{ARRIVALS}\n\n"
        f"## Ledger\n\n{LEDGER}\n\n"
        f"## Kitchen\n\n{KITCHEN}\n\n"
        f"## Fees\n\n{FEES}\n"
    ).encode()
    return [("harbour.md", harbour), ("odd.md", md(ODD, title="Odd jobs"))]


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Short memories, so a test that changes what the gateway lists is noticed at once."""
    monkeypatch.setattr(meaning_module, "PROBE_TTL", 0.05)
    monkeypatch.setattr(meaning_module, "PROBE_ERROR_TTL", 0.02)
    monkeypatch.setattr(meaning_module, "SEEN_TTL", 0.05)
    yield


def ready_world(world: World, *models: str, per_minute: int = 6000) -> World:
    """A world whose gateway lists these search models (ready, local), and whose worker is
    not held back by the key's requests a minute."""
    world.gateway.embedder_listed = False
    world.gateway.search_models = [search_model(m) for m in (models or (EMBEDDER,))]
    settings(world, documentsEmbedRequestsPerMinute=per_minute)
    return world


def no_models(world: World) -> World:
    """A world whose gateway lists no search model at all."""
    world.gateway.embedder_listed = False
    world.gateway.search_models = []
    return world


def filler() -> tuple[str, bytes]:
    """A file of sixty passages about ships and their records, so that a question about them
    has more than fifty passages near it, and a passage with none of their meaning is not one
    of the fifty nearest (a search by meaning takes the nearest fifty, and in a few passages
    that is all of them)."""
    body = "\n\n".join(
        f"# Entry {n}\n\nEntry {n} is about a ship, the weather, the kitchen, the law and the budget."
        for n in range(60)
    )
    return "filler.md", body.encode()


def until(condition: Callable[[], Any], seconds: float = 30.0, every: float = 0.03) -> Any:
    """The condition's value once it is true, or fail: what a wait must wait for."""
    end = time.perf_counter() + seconds
    value = condition()
    while not value and time.perf_counter() < end:
        time.sleep(every)
        value = condition()
    assert value, "the condition did not come true in time"
    return value


def search_of(person: Browser) -> dict[str, Any]:
    return dict(listing(person)["search"])


def meaning_of(person: Browser, name: str) -> dict[str, Any] | None:
    found = next(d for d in listing(person)["documents"] if d["name"] == name)
    return found["meaning"]  # type: ignore[no-any-return]


def try_question(person: Browser, question: str, **more: Any) -> dict[str, Any]:
    made = person.post("/api/documents/test", json={"question": question, **more})
    assert made.status_code == 200, made.text
    return dict(made.json())


def asked_for_passages(world: World) -> list[str]:
    """Every passage text the search model was asked to embed, in order."""
    out: list[str] = []
    for request in world.gateway.embed_requests:
        given = request["body"]["input"]
        out += given if isinstance(given, list) else [given]
    return out


def asked_for_batches(world: World) -> list[str]:
    """What the search model was asked to embed in requests of more than one text: the
    passages of documents, which are sent sixteen at a time."""
    out: list[str] = []
    for request in world.gateway.embed_requests:
        given = request["body"]["input"]
        if isinstance(given, list) and len(given) > 1:
            out += given
    return out


def asked_for_questions(world: World) -> list[str]:
    """What the search model was asked in one-text requests (questions, and the tiny one
    that asks which model answers)."""
    return [
        r["body"]["input"][0]
        for r in world.gateway.embed_requests
        if isinstance(r["body"]["input"], list) and len(r["body"]["input"]) == 1
    ]


def config(world: World, **values: Any) -> httpx.Response:
    return httpx.patch(
        f"{world.workbench}/v1/config",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json=values,
        trust_env=False,
    )


def vectors_of(world: World, sub: str = "p-ada") -> list[tuple[int, int]]:
    """`(passage id, bytes in its vector)` for every passage that has one, read straight
    from the person's index file; an empty vector is a passage the search model refused."""
    with raw(world, sub) as db:
        return [
            (r[0], r[1])
            for r in db.execute("SELECT id, length(vec) FROM chunks WHERE vec IS NOT NULL")
        ]


def all_vectors(world: World, sub: str = "p-ada") -> sqlite3.Row | None:
    with raw(world, sub) as db:
        db.row_factory = sqlite3.Row
        return db.execute("SELECT value FROM meta WHERE key = 'embedder'").fetchone()  # type: ignore[no-any-return]


def built_with(world: World, sub: str = "p-ada") -> dict[str, Any] | None:
    """What the person's index says its vectors were made by, read from the file."""
    with raw(world, sub) as db:
        row = db.execute("SELECT value FROM meta WHERE key = 'embedder'").fetchone()
    return json.loads(row[0]) if row else None  # type: ignore[no-any-return]
