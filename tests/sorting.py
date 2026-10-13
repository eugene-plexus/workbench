"""Helpers for the result-sorter tests (MR6b): a world with a search model and a sorter, documents
whose sorted order a test can reason about, and reading what the gateway was asked.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from eugene_plexus_workbench.knowledge import listing, sorting

from .conftest import World, sorter_model
from .meaning import ARRIVALS, FEES, KITCHEN, LEDGER, ODD, config, ready_world, try_question

SORTER = "mr6b-sorter"
#: A question the words find some of the harbour office's passages for, and meaning the rest of.
QUESTION = "who keeps the vessel registry, the ledger and the berthing fees?"
#: The harbour corpus's five passages, by their texts.
PASSAGES = (ARRIVALS, LEDGER, KITCHEN, FEES, ODD)


@pytest.fixture
def quick_sorting(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Short memories, so a test that changes what the gateway lists, or what failed, is
    noticed at once."""
    monkeypatch.setattr(listing, "TTL", 0.05)
    monkeypatch.setattr(listing, "ERROR_TTL", 0.02)
    monkeypatch.setattr(sorting, "MEASURE_RETRY", 0.05)
    yield


def sorter_world(world: World, *models: str) -> World:
    """A world whose gateway lists a search model (ready, local; not held back by the key's
    requests a minute) and these result sorters (ready, local)."""
    ready_world(world)
    world.gateway.sorters = [sorter_model(m) for m in (models or (SORTER,))]
    return world


def sort_of(found: dict[str, Any]) -> dict[str, Any]:
    """The `sort` block of a tried question or an answer's search."""
    return dict(found["sort"])


def texts(found: dict[str, Any]) -> list[str]:
    """The words of the passages a tried question found, in the order they would be sent."""
    return [p["text"] for p in found["passages"]]


def places(found: dict[str, Any]) -> list[dict[str, int]]:
    """Where each of the passages was put by each way and by the sorter, in the order found."""
    return [p["why"]["ranks"] for p in found["passages"]]


def unsorted(person: Any, world: World, question: str = QUESTION, **more: Any) -> dict[str, Any]:
    """What the question finds when the sorter is turned off: the order the search found."""
    assert config(world, documentsSortModel="off").status_code == 200
    found = try_question(person, question, **more)
    assert config(world, documentsSortModel=None).status_code == 200
    return found
