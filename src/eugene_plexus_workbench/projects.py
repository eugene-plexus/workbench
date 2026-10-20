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


GENERAL = Place()


async def place_of(store: Store, chat: Chat | None) -> Place:
    """The place a chat is in. A chat whose project has just gone is in none."""
    if chat is None or chat.project_id is None:
        return GENERAL
    project = await store.project(chat.owner, chat.project_id)
    if project is None:
        return GENERAL
    return Place(project.id, project.general_memory)


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
    """The person's notes a chat may be given."""
    return in_place(await store.memory_notes(owner, status=status), await place_of(store, chat))
