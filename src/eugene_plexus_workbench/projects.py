"""Memory v2's MV7: chat projects, and which notes a chat is given.

A project is a named group of a person's chats with its own notes (`memory_notes.project_id`).
A note is in one place: a project, or General (null). What a chat is given:

* a chat in no project: general notes;
* a chat in a project: the project's notes, and general notes too while the project's
  **Also use my general memory** is on (`general_memory`).

A project's notes never reach a chat outside it (MV7-C1). The filter is applied to the pool
before anything ranks it (`Finder.choose`), never after, so a project's notes are not crowded
out of a search's top results by general ones.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from . import memory
from .store import Chat, MemoryNote, Store

#: At most this many projects a person.
MAX_PROJECTS = 100
#: A project's name, at most.
NAME_CHARS = 80


@dataclass(frozen=True)
class Place:
    """Where a chat is: its project (None for none), and whether general notes come too."""

    project_id: str | None = None
    general: bool = True
    #: Row 47: the owner of a project shared with the chat's person, whose instructions and
    #: pinned notes in it the chat is given, read only. None for a project of their own.
    shared_from: str | None = None


GENERAL = Place()


async def model_in_effect(hub: Any, choice: str | None) -> tuple[str | None, list[str]]:
    """Use the project's served choice, otherwise Workbench's first chat model."""
    listing = await hub.models()
    models = []
    for entry in listing.get("data") or []:
        surfaces = (entry.get("x_eugene_plexus") or {}).get("surfaces")
        if entry.get("id") and (surfaces is None or "chat" in surfaces):
            models.append(entry["id"])
    return (choice if choice in models else next(iter(models), None)), models


async def place_of(store: Store, chat: Chat | None) -> Place:
    """The place a chat is in. A chat whose project has just gone is in none; so is one in a
    project that is no longer shared with its person (row 47), which keeps their own chat and
    loses the project's notes at once."""
    if chat is None or chat.project_id is None:
        return GENERAL
    project = await store.project(chat.owner, chat.project_id)
    if project is not None:
        return Place(project.id, project.general_memory)
    shared = await store.shared_project(chat.project_id)
    person = await store.person(chat.owner) if shared is not None else None
    if shared is None or person is None:
        return GENERAL
    from .team import project_role

    if await project_role(store, person, shared) is None:
        return GENERAL
    # Their own general notes come as in any chat of theirs: the project's switch is its owner's.
    return Place(shared.id, True, shared.owner)


def in_place(notes: Iterable[MemoryNote], place: Place) -> list[MemoryNote]:
    """The notes a chat in `place` may be given: the project's first, then general ones."""
    held = list(notes)
    if place.project_id is None:
        return [n for n in held if n.project_id is None]
    mine = [n for n in held if n.project_id == place.project_id]
    if not place.general:
        return mine
    return mine + [n for n in held if n.project_id is None]


async def chat_notes(
    store: Store, owner: str, chat: Chat | None, *, status: str | None = "active"
) -> list[MemoryNote]:
    """The person's notes a chat may be given; in a project shared with them (row 47), its
    owner's instructions and pinned notes in it too, read only."""
    place = await place_of(store, chat)
    mine = in_place(await store.memory_notes(owner, status=status), place)
    if place.shared_from is None or status != "active":
        return mine
    return mine + await shared_notes(store, place.shared_from, place.project_id)


async def shared_notes(store: Store, owner: str, project_id: str | None) -> list[MemoryNote]:
    """What a shared project gives the people it is shared with of its owner's notes: its
    instructions and pinned notes, but none a model kept by itself that its owner has not seen
    yet (call 12: `memory.shareable`)."""
    return memory.shareable(
        [
            n
            for n in await store.memory_notes(owner, status="active")
            if n.project_id == project_id and memory.is_always(n)
        ]
    )
