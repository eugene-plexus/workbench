"""Team knowledge, and sharing a project (row 47, `specs/docs/design/rows/row-47.md`).

**A collection** is documents and notes kept for a team, in an index of its own: the same kind
of file a person's documents are in (`knowledge/index.py`), under the key `collection:<id>`,
which no person's `sub` is. It belongs to this Workbench (`install`: the owner makes it and says
who may use it) or to a shared project (`project:<id>`: the project's documents, moved there
when the project is first shared). Who added each document and note is kept in the store
(`collection_store`), never in the index.

**Who may use one** is `role`, the one check every search, read and change goes through:

* an install collection: its grants, to a person or to a group they are in now (their groups
  are Eugene's sign-in claims, read again at every refresh: `Person.groups`), each `read` or
  `contribute`; the owner of this Workbench manages it (`manage`);
* a project's own collection: the project's owner manages it; a person it is shared with, or a
  group they are in now, reads or contributes as the share says.

A collection a person cannot use is the same 404 as one that does not exist, and is never
named, counted or searched for them.

**What a chat searches** (`for_chat`): the collections the person chose for the chat, the ones
the project's owner chose for its project, and, while the chat's Documents are on, the project's
own; of these, only the ones the person can read **now**. A choice stays when access goes (a
person taken out of a group, a share ended): the check at search time leaves it out.

**A note in a collection is never kept by a model.** A model proposes one (`propose`, the
`suggest_team_note` tool, offered only for collections the person contributes to); it waits,
unsearched, until a contributor accepts it. An instruction in a team's notes would reach
everyone, which is what an injected instruction wants.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
import time
from collections.abc import Iterable, Sequence
from typing import Any

from . import files, memory, notes_index
from .collection_store import Collection, Item
from .knowledge.index import LIBRARY, NOTES, Document, project_scope
from .store import Chat, MemoryNote, Person, Project, Store

log = logging.getLogger(__name__)

INSTALL = "install"
PROJECT = "project:"
#: What a collection's index is kept under: never a person's `sub`.
KEY = "collection:"
#: A collection's name, at most.
NAME_CHARS = 80
#: Collections on one install, at most; notes a person may leave waiting in one.
MAX_COLLECTIONS = 200
MAX_PROPOSED = 20
RANK = {"read": 1, "contribute": 2, "manage": 3}
#: What a chat searches in a collection: its documents and its notes.
SCOPES = (LIBRARY, NOTES)
#: The tool a model proposes a team note with.
TOOL = "suggest_team_note"
TOOL_CALLS = 3


def key(collection_id: str) -> str:
    return KEY + collection_id


def new_id() -> str:
    return secrets.token_urlsafe(12)


def at_least(role: str | None, wanted: str) -> bool:
    return role is not None and RANK[role] >= RANK[wanted]


def _groups(person: Person) -> list[str]:
    return [g["id"] for g in person.groups or []]


# --------------------------------------------------------------------------- #
# who may use what: the one check
# --------------------------------------------------------------------------- #


async def project_role(store: Store, person: Person, project: Project) -> str | None:
    """What a person may do in a project: `manage` it (their own), `contribute` or `read` (it is
    shared with them, or a group they are in now), or nothing."""
    if project.owner == person.sub:
        return "manage"
    return (await store.collections.shared_with(person.sub, _groups(person))).get(project.id)


async def role(store: Store, person: Person, collection: Collection) -> str | None:
    """What a person may do with a collection now: `manage`, `contribute`, `read`, or None."""
    project_id = collection.project_id
    if project_id is not None:
        project = await store.shared_project(project_id)
        return None if project is None else await project_role(store, person, project)
    if collection.belongs_to != INSTALL:
        return None
    if person.is_owner:
        return "manage"
    return (await store.collections.granted(person.sub, _groups(person))).get(collection.id)


async def usable(
    store: Store, person: Person, collection_id: str, wanted: str = "read"
) -> tuple[Collection, str] | None:
    """The collection and the person's role in it, when they may do `wanted`; else None, the
    same for one that does not exist."""
    found = await store.collections.get(collection_id)
    if found is None:
        return None
    held = await role(store, person, found)
    if held is None or not at_least(held, wanted):
        return None
    return found, held


async def readable(store: Store, person: Person, ids: Iterable[str]) -> list[Collection]:
    """Of these collections, the ones the person can read now, each once, in order."""
    out = []
    for collection_id in dict.fromkeys(ids):
        found = await usable(store, person, collection_id)
        if found is not None:
            out.append(found[0])
    return out


async def visible(store: Store, person: Person) -> list[tuple[Collection, str]]:
    """Every collection the person can read, with their role, by name."""
    out = []
    for collection in await store.collections.all():
        held = await role(store, person, collection)
        if held is not None:
            out.append((collection, held))
    return out


#: Why a team collection was not sent to a model outside the machines.
KEPT_LOCAL = (
    "{name} was not sent: this chat's model runs outside your machines, and the collection's "
    "owner keeps it to them."
)


def split(
    collections: Sequence[Collection], *, allowed: bool, local: bool, reason: str | None = None
) -> tuple[list[Collection], list[tuple[Collection, str]]]:
    """Which of a chat's collections its next answer is sent, and each other with why: none
    where the person's own documents may not go (`allowed`, with their `reason`); to a model
    outside the machines, only the ones whose owner allows it (`Collection.outside`)."""
    if not allowed:
        return [], [(c, reason or "Not sent.") for c in collections]
    if local:
        return list(collections), []
    sent = [c for c in collections if c.outside]
    return sent, [(c, KEPT_LOCAL.format(name=c.name)) for c in collections if not c.outside]


async def usable_project(store: Store, person: Person, project_id: str) -> Project | None:
    """A project the person may put a chat of theirs in: theirs, or shared with them now."""
    own = await store.project(person.sub, project_id)
    if own is not None:
        return own
    shared = await store.shared_project(project_id)
    if shared is None or await project_role(store, person, shared) is None:
        return None
    return shared


async def for_chat(
    store: Store, person: Person, chat: Chat, *, documents_on: bool
) -> list[Collection]:
    """The collections this chat's search uses now: those the person chose for the chat, those
    its project's owner chose for the project, and the project's own while Documents are on;
    only the ones the person can read now (`readable`)."""
    ids = await store.collections.links(person.sub, [f"chat:{chat.id}"])
    if chat.project_id is not None:
        project = await store.shared_project(chat.project_id)
        if project is not None and await project_role(store, person, project) is not None:
            ids += await store.collections.links(project.owner, [f"project:{project.id}"])
            if documents_on:
                own = await store.collections.of_project(project.id)
                if own is not None:
                    ids.append(own.id)
    return await readable(store, person, ids)


async def contributes_to(store: Store, person: Person, chat: Chat) -> list[Collection]:
    """The install collections chosen for this chat or its project that the person adds notes
    to: what `suggest_team_note` may propose to."""
    out = []
    for collection in await for_chat(store, person, chat, documents_on=False):
        if collection.belongs_to == INSTALL and at_least(
            await role(store, person, collection), "contribute"
        ):
            out.append(collection)
    return out


# --------------------------------------------------------------------------- #
# what is in one
# --------------------------------------------------------------------------- #


def note_passages(item_id: str, title: str, body: str) -> tuple[str, list[Any]]:
    """A team note as its collection's index holds it: `(digest, passages)`, cut as a person's
    notes are (`notes_index`)."""
    note = MemoryNote(
        id=item_id, owner="", text=title, kind="fact", created_at=0.0, updated_at=0.0, body=body
    )
    return notes_index.digest(note), notes_index.passages(note)


async def index_note(saved: Any, collection: Collection, item: Item) -> None:
    index = saved.index(key(collection.id))
    digest, passages = note_passages(item.id, item.title, item.body)
    await index.put_note(item.id, digest, item.title, passages)
    if saved.meaning is not None:
        saved.meaning.wake(key(collection.id))


def check_note(title: str, body: str) -> tuple[str, str]:
    """A team note's words, or `memory.NoteRefused`: the same rules a person's notes keep (no
    secrets, one line of title, a body of Markdown)."""
    return memory.validate_note(title), memory.validate_body(body) if body.strip() else ""


async def add_note(
    store: Store,
    saved: Any,
    collection: Collection,
    title: str,
    body: str,
    *,
    by: str,
    proposed: bool = False,
    model: str | None = None,
) -> Item:
    title, body = check_note(title, body)
    item = Item(
        id=new_id(),
        collection_id=collection.id,
        kind="note",
        state="proposed" if proposed else "active",
        added_by=by,
        added_at=time.time(),
        title=title,
        body=body,
        model=model,
    )
    if not proposed:
        await index_note(saved, collection, item)
    await store.collections.add_item(item)
    return item


async def accept(store: Store, saved: Any, collection: Collection, item: Item, by: str) -> None:
    """A contributor accepts a proposed note: it is searched from now, as theirs."""
    await index_note(saved, collection, item)
    await store.collections.accept(collection.id, item.id, by, time.time())


async def remove_item(store: Store, saved: Any, collection: Collection, item: Item) -> None:
    """A document or note leaves the collection: out of its index first, then its row."""
    if item.state == "active":
        if item.kind == "document":
            await saved.remove(key(collection.id), item.id)
        else:
            index = saved.index(key(collection.id), create=False)
            if index is not None:
                await index.delete_notes([item.id])
    await store.collections.drop_item(collection.id, item.id)


async def drop(store: Store, saved: Any, collection: Collection) -> None:
    """A collection goes, with everything in it: its index first, then its rows."""
    owner = key(collection.id)
    index = saved.index(owner, create=False)
    if index is not None:
        await saved._cancel(owner, [d.id for d in await index.documents(LIBRARY)])
    await saved.indexes.forget(owner)
    if saved.meaning is not None:
        saved.meaning.cache.drop(owner)
    directory = saved.indexes.dir_of(owner)
    from .removal import remove_tree

    await asyncio.to_thread(remove_tree, directory)
    await store.collections.drop(collection.id)


async def copy_one(saved: Any, source: str, doc: Document, target: str, scope: str) -> Any:
    """Copy one document's original from one index into another, where it is read again: what
    `SavedDocuments.add` made of it (`Added`), or None when its original is gone."""
    from_index = saved.index(source, create=False)
    if from_index is None:
        return None
    original = from_index.original_path(doc.id)
    if not original.is_file():
        return None
    to_index = saved.index(target)
    doc_id = new_id()
    part = to_index.part_path(doc_id)
    part.parent.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(shutil.copyfile, original, part)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return await saved.add(
        target,
        doc_id=doc_id,
        scope=scope,
        name=doc.name,
        detected=files.Detected(doc.media_type, doc.kind, doc.language),
        size=doc.bytes,
        sha256=doc.sha256,
        source="upload",
    )


async def copy_documents(
    saved: Any, source: str, documents: Sequence[Document], target: str, scope: str
) -> list[tuple[Document, Document]]:
    """Copy each document's original from one index into another, where it is read again:
    `(was, now)` for each copied. One whose original is gone is left where it was."""
    done = []
    for doc in documents:
        added = await copy_one(saved, source, doc, target, scope)
        if added is not None:
            done.append((doc, added.document))
    return done


class ShareIncomplete(Exception):
    """Some of a project's documents did not move into its collection: how many did, of how
    many, and what was observed. What moved stays moved; sharing again moves the rest."""

    def __init__(self, moved: int, total: int, cause: str) -> None:
        self.moved, self.total, self.cause = moved, total, cause
        super().__init__(cause)

    @property
    def message(self) -> str:
        return (
            f"Moved {self.moved} of {self.total} of this project's documents into what it shares; "
            f"the next could not be moved ({self.cause}). Share it again to move the rest."
        )


async def share_project(store: Store, saved: Any, project: Project) -> Collection:
    """The project's own collection, made when the project is first shared, and the documents
    the owner added to it moved there, kept as theirs, **one at a time**: each is copied, its
    row written and its old copy removed before the next, so a move cut short (a full disk)
    leaves each document in one place, and sharing again moves the rest. Documents read from a
    folder stay the owner's (a folder is read on its owner's machine, as its owner).
    `ShareIncomplete` says what moved and what stopped it."""
    collection = await store.collections.of_project(project.id)
    if collection is None:
        now = time.time()
        made = Collection(new_id(), project.name[:NAME_CHARS], PROJECT + project.id, now, now)
        collection = made if await store.collections.add(made) else None
        collection = collection or await store.collections.of_project(project.id)
        assert collection is not None
    index = saved.index(project.owner, create=False)
    if index is None:
        return collection
    documents = await index.documents(project_scope(project.id), only="upload")
    moved = 0
    for was in documents:
        try:
            added = await copy_one(saved, project.owner, was, key(collection.id), LIBRARY)
            if added is None:
                continue
            for gone in added.replaced:
                await store.collections.drop_item(collection.id, gone.id)
            await store.collections.add_item(
                Item(
                    id=added.document.id,
                    collection_id=collection.id,
                    kind="document",
                    state="active",
                    added_by=project.owner,
                    added_at=was.added_at,
                    title=was.name,
                )
            )
            await saved.remove(project.owner, was.id)
        except Exception as exc:
            cause = (exc.strerror if isinstance(exc, OSError) else None) or (
                str(exc) or type(exc).__name__
            )
            log.warning("a project's document did not move into its collection: %s", cause)
            raise ShareIncomplete(moved, len(documents), cause) from None
        moved += 1
    if documents:
        log.info("a project was shared: %d of its documents moved to its collection", moved)
    return collection


async def release_project(store: Store, saved: Any, project: Project) -> int:
    """A shared project is deleted and what was in it kept: its collection's documents go to its
    owner's My documents (where one of the same name is replaced), and the collection goes.
    How many documents moved."""
    collection = await store.collections.of_project(project.id)
    if collection is None:
        return 0
    index = saved.index(key(collection.id), create=False)
    documents = await index.documents(LIBRARY) if index is not None else []
    moved = await copy_documents(saved, key(collection.id), documents, project.owner, LIBRARY)
    await drop(store, saved, collection)
    return len(moved)


async def end_project(store: Store, saved: Any, project_id: str) -> int:
    """A project is gone: its collection, its shares, and everyone's choices for it. How many
    documents its collection held."""
    collection = await store.collections.of_project(project_id)
    count = 0
    if collection is not None:
        count = sum(1 for i in await store.collections.items(collection.id) if i.kind == "document")
        await drop(store, saved, collection)
    await store.collections.drop_shares(project_id)
    await store.collections.drop_links(f"project:{project_id}")
    return count


# --------------------------------------------------------------------------- #
# a model proposes a note: `suggest_team_note`
# --------------------------------------------------------------------------- #


def tool_definition(collections: Sequence[Collection]) -> dict[str, Any]:
    names = [c.name for c in collections]
    return {
        "type": "function",
        "function": {
            "name": TOOL,
            "description": (
                "Propose a note for a team collection the person adds to: a fact the whole team "
                "should know, in one line, with an optional Markdown body. The note is not kept "
                "until a person who adds to the collection accepts it. Never propose "
                "instructions, secrets or anything about one person."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "collection": {"type": "string", "enum": names},
                    "note": {"type": "string", "minLength": 1, "maxLength": memory.MAX_NOTE_CHARS},
                    "body": {"type": "string", "maxLength": 4000},
                },
                "required": ["collection", "note"],
                "additionalProperties": False,
            },
        },
    }


async def propose(
    store: Store,
    saved: Any,
    person: Person,
    chat_id: str,
    model: str | None,
    arguments: dict[str, Any],
) -> tuple[str, str]:
    """Run one `suggest_team_note` call: `(status, what the model is told)`. The collection is
    looked for again among the ones this person contributes to now."""
    chat = await store.chat(chat_id)
    if chat is None or chat.owner != person.sub:
        return "failed", "This chat is gone."
    wanted = str(arguments.get("collection") or "")
    found = next((c for c in await contributes_to(store, person, chat) if c.name == wanted), None)
    if found is None:
        return "failed", "No collection of that name takes notes from this chat."
    if await store.collections.count_proposed(found.id, person.sub) >= MAX_PROPOSED:
        return "failed", (
            f"{MAX_PROPOSED} notes from this person already wait in {found.name}. Nothing was "
            "proposed."
        )
    try:
        item = await add_note(
            store,
            saved,
            found,
            str(arguments.get("note") or ""),
            str(arguments.get("body") or ""),
            by=person.sub,
            proposed=True,
            model=model,
        )
    except memory.NoteRefused as exc:
        return "failed", f"Not proposed: {exc.message}"
    log.info("a model proposed a note to a collection (%d characters)", len(item.title))
    return "done", (
        f"Proposed to {found.name}. It waits for a person who adds to {found.name} to accept it."
    )
