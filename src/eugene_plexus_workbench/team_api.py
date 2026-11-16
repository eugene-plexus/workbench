"""Team knowledge over HTTP (row 47): collections, who may use each, their documents, notes and
the notes a model proposed, and the collections a chat or a project uses.

Every route takes the person from the session and asks `team.role` before anything else: a
collection the person cannot use is the same 404 as one that does not exist, so no route says
another collection's name, size or contents to someone outside it. Only the owner of this
Workbench makes, renames, deletes and grants install collections; a shared project's own
collection is its project's (`projects_api`).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, File, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import config, memory, team
from .api import _own_chat, _person, _problem, _state
from .collection_store import Collection, Grant, Item
from .knowledge.index import LIBRARY, Document, valid_id
from .signin import GROUP_ID, SignInUnavailable
from .store import Person, Store

log = logging.getLogger(__name__)

router = APIRouter()

NO_SUCH = "There is no such collection."
FORMER = "a former member"
ONLY_OWNER = "Only the owner of this Workbench makes and shares team collections."


def _store(request: Request) -> Store:
    store: Store = _state(request).store
    return store


def _saved(request: Request) -> Any:
    return _state(request).saved


async def _names(store: Store) -> dict[str, str]:
    return {p.sub: p.name for p, _, _ in await store.people()}


def _who(names: dict[str, str], sub: str | None) -> str:
    return names.get(sub, FORMER) if sub else FORMER


async def _usable(
    request: Request, person: Person, collection_id: str, wanted: str = "read"
) -> tuple[Collection, str]:
    found = await team.usable(_store(request), person, collection_id, wanted)
    if found is None:
        # Not enough to do this, or not there: told apart only for one the person can read.
        if wanted != "read" and await team.usable(_store(request), person, collection_id):
            raise _problem(
                status.HTTP_403_FORBIDDEN,
                "You can read this collection, not change it. Ask its owner.",
            )
        raise _problem(status.HTTP_404_NOT_FOUND, NO_SUCH)
    return found


def _install_only(collection: Collection) -> None:
    if collection.belongs_to != team.INSTALL:
        raise _problem(
            status.HTTP_409_CONFLICT,
            "This collection is a shared project's. Change it on the project's page.",
        )


def _name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name:
        raise _problem(status.HTTP_422_UNPROCESSABLE_CONTENT, "A collection needs a name.")
    if memory._hidden(name):
        raise _problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "A collection's name cannot hold characters that are not seen.",
        )
    if len(name) > team.NAME_CHARS:
        raise _problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"A collection's name is at most {team.NAME_CHARS} characters.",
        )
    return name


def collection_view(
    collection: Collection,
    role: str,
    counts: tuple[int, int, int] = (0, 0, 0),
) -> dict[str, Any]:
    return {
        "id": collection.id,
        "name": collection.name,
        "kind": "project" if collection.project_id else "install",
        "projectId": collection.project_id,
        "role": role,
        "documents": counts[0],
        "notes": counts[1],
        # What waits for a contributor: never told to a reader.
        "proposed": counts[2] if team.at_least(role, "contribute") else 0,
        #: Whether its passages may go to a model outside the install's machines.
        "outsideModels": collection.outside,
    }


def item_view(item: Item, names: dict[str, str], doc: Document | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": item.id,
        "kind": item.kind,
        "state": item.state,
        "title": item.title,
        "addedBy": _who(names, item.added_by),
        "addedAt": item.added_at,
        "formerMember": item.added_by is None or item.added_by not in names,
    }
    if item.kind == "note":
        out["body"] = item.body
    if item.model:
        out["model"] = item.model
    if doc is not None:
        out["document"] = {
            "state": doc.status,
            "type": doc.kind,
            "bytes": doc.bytes,
            "passages": doc.chunks,
            "message": doc.reason,
        }
    return out


async def _grants_view(store: Store, collection_id: str, names: dict[str, str]) -> list[Any]:
    out = []
    for grant in await store.collections.grants(collection_id):
        out.append(
            {
                "kind": grant.kind,
                "id": grant.subject,
                "name": names.get(grant.subject, FORMER) if grant.kind == "person" else None,
                "role": grant.role,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# collections
# --------------------------------------------------------------------------- #


@router.get("/api/collections")
async def list_collections(request: Request) -> dict[str, Any]:
    """The collections the asker can read, by name, with their role in each."""
    person = await _person(request)
    store = _store(request)
    counts = await store.collections.counts()
    return {
        "collections": [
            collection_view(c, role, counts.get(c.id, (0, 0, 0)))
            for c, role in await team.visible(store, person)
        ],
        "canCreate": person.is_owner,
    }


class CollectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=400)


@router.post("/api/collections", status_code=status.HTTP_201_CREATED)
async def create_collection(request: Request, body: CollectionCreate) -> dict[str, Any]:
    person = await _person(request)
    if not person.is_owner:
        raise _problem(status.HTTP_403_FORBIDDEN, ONLY_OWNER)
    store = _store(request)
    if len(await store.collections.all()) >= team.MAX_COLLECTIONS:
        raise _problem(
            status.HTTP_409_CONFLICT,
            f"This Workbench has {team.MAX_COLLECTIONS} collections, the most it keeps.",
        )
    now = time.time()
    collection = Collection(team.new_id(), _name(body.name), team.INSTALL, now, now)
    if not await store.collections.add(collection):
        raise _problem(status.HTTP_409_CONFLICT, "A collection of that name is here already.")
    return collection_view(collection, "manage")


@router.get("/api/collections/{collection_id}")
async def get_collection(request: Request, collection_id: str) -> dict[str, Any]:
    """One collection: its documents and notes, who added each, and for a contributor the notes
    a model proposed; for its manager, who may use it."""
    person = await _person(request)
    store, saved = _store(request), _saved(request)
    collection, role = await _usable(request, person, collection_id)
    names = await _names(store)
    index = saved.index(team.key(collection.id), create=False)
    docs = {d.id: d for d in await index.documents(LIBRARY)} if index is not None else {}
    items = await store.collections.items(collection.id, state=None)
    counts = (await store.collections.counts()).get(collection.id, (0, 0, 0))
    out = {
        **collection_view(collection, role, counts),
        "documentList": [
            item_view(i, names, docs.get(i.id))
            for i in items
            if i.kind == "document" and i.state == "active"
        ],
        "noteList": [
            item_view(i, names) for i in items if i.kind == "note" and i.state == "active"
        ],
        "inbox": (
            [item_view(i, names) for i in items if i.state == "proposed"]
            if team.at_least(role, "contribute")
            else []
        ),
        "grants": (
            await _grants_view(store, collection.id, names)
            if role == "manage" and collection.belongs_to == team.INSTALL
            else None
        ),
    }
    if role == "manage":
        # What deleting it takes from other people: their documents, counted for the question.
        out["othersDocuments"] = sum(
            i.kind == "document" and i.state == "active" and i.added_by != person.sub for i in items
        )
    return out


class CollectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=400)
    #: Whether its passages may go to a model outside the install's machines (a cloud account).
    outsideModels: StrictBool | None = None


@router.patch("/api/collections/{collection_id}")
async def rename_collection(
    request: Request, collection_id: str, body: CollectionUpdate
) -> dict[str, Any]:
    person = await _person(request)
    collection, role = await _usable(request, person, collection_id, "manage")
    store = _store(request)
    if body.name is not None:
        _install_only(collection)
        name = _name(body.name)
        if not await store.collections.rename(collection.id, name, time.time()):
            raise _problem(status.HTTP_409_CONFLICT, "A collection of that name is here already.")
        collection.name = name
    if body.outsideModels is not None:
        # Its manager's word: a shared project's owner for its own collection.
        await store.collections.set_outside(collection.id, body.outsideModels, time.time())
        collection.outside = body.outsideModels
    return collection_view(collection, role)


@router.delete("/api/collections/{collection_id}")
async def delete_collection(request: Request, collection_id: str) -> dict[str, Any]:
    """Delete a collection and everything in it, for everyone it was given to."""
    person = await _person(request)
    collection, _ = await _usable(request, person, collection_id, "manage")
    _install_only(collection)
    store = _store(request)
    held = await store.collections.items(collection.id, state=None)
    await team.drop(store, _saved(request), collection)
    return {
        "documents": sum(i.kind == "document" for i in held),
        "notes": sum(i.kind == "note" for i in held),
    }


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["person", "group"]
    id: str = Field(min_length=1, max_length=256)
    role: Literal["read", "contribute"]


class GrantsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grants: list[GrantIn] = Field(max_length=500)


async def check_grantees(
    request: Request, person: Person, grants: list[GrantIn], *, any_group: bool
) -> list[Grant]:
    """The grants, each to a person who has signed in here, or a group Eugene lists (the owner)
    or the asker is in (anyone else). One that is not is refused by name."""
    store = _store(request)
    people = {p["sub"] for p in await shareable_people(store, person)}
    groups = {g["id"] for g in person.groups or []}
    if any_group and any(g.kind == "group" and g.id not in groups for g in grants):
        try:
            groups |= {g["id"] for g in await _state(request).provider.groups()}
        except SignInUnavailable as exc:
            raise _problem(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"Workbench could not ask Eugene for its groups: {exc}.",
            ) from None
    out: dict[tuple[str, str], Grant] = {}
    for grant in grants:
        if grant.kind == "person" and grant.id not in people:
            raise _problem(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Share only with someone in one of your groups who has signed in to Workbench."
                if not person.is_owner
                else "A person must sign in to Workbench once before something is shared with "
                "them.",
            )
        if grant.kind == "group" and (not GROUP_ID.fullmatch(grant.id) or grant.id not in groups):
            raise _problem(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Share only with a group Eugene lists for this Workbench"
                + ("." if any_group else " that you are in."),
            )
        if grant.kind == "person" and grant.id == person.sub:
            continue
        out[(grant.kind, grant.id)] = Grant(grant.kind, grant.id, grant.role)
    return list(out.values())


@router.put("/api/collections/{collection_id}/grants")
async def put_grants(request: Request, collection_id: str, body: GrantsUpdate) -> dict[str, Any]:
    """Who may use an install collection: people and groups, each `read` or `contribute`. Takes
    effect at each one's next search."""
    person = await _person(request)
    collection, _ = await _usable(request, person, collection_id, "manage")
    _install_only(collection)
    store = _store(request)
    grants = await check_grantees(request, person, body.grants, any_group=True)
    await store.collections.set_grants(collection.id, grants)
    return {"grants": await _grants_view(store, collection.id, await _names(store))}


async def shareable_people(store: Store, person: Person) -> list[dict[str, Any]]:
    """The people the asker may share with and see the names of: everyone who has signed in to
    this Workbench, for its owner; for anyone else, only the people in a group with them."""
    mine = {g["id"] for g in person.groups or []}
    out = []
    for other, _, _ in await store.people():
        if other.sub == person.sub:
            continue
        if not person.is_owner and not mine & {g["id"] for g in other.groups or []}:
            continue
        out.append({"sub": other.sub, "name": other.name, "username": other.username})
    return out


@router.get("/api/team/people")
async def team_people(request: Request) -> dict[str, Any]:
    """Who the asker may share with: the people who have signed in to this Workbench (its
    owner) or the people in a group with them (anyone else), and the groups Eugene lists for it
    (the owner) or the asker's own groups (anyone else)."""
    person = await _person(request)
    store = _store(request)
    people = await shareable_people(store, person)
    groups = list(person.groups or [])
    problem = None
    if person.is_owner:
        try:
            listed = await _state(request).provider.groups()
            known = {g["id"] for g in groups}
            groups += [g for g in listed if g["id"] not in known]
        except SignInUnavailable as exc:
            problem = f"Workbench could not ask Eugene for its groups: {exc}."
    return {"people": people, "groups": groups, "problem": problem}


# --------------------------------------------------------------------------- #
# what is in one
# --------------------------------------------------------------------------- #


async def add_to_collection(
    request: Request,
    person: Person,
    collection: Collection,
    uploads: list[UploadFile],
    role: str,
) -> dict[str, Any]:
    """Keep files in a collection, as `POST /api/documents` keeps them in My documents, each
    recorded as added by the asker. A file of the same name replaces the one there, only when
    the asker added that one or manages the collection: anyone else's is refused, by name and
    by whose it is, as taking it out would be."""
    from .saved_api import MAX_FILES, _clean_name, _refusal, keep_files

    store = _store(request)
    install = await config.document_settings(store)
    if not install.enabled:
        raise _problem(
            status.HTTP_403_FORBIDDEN, "The owner of this Workbench turned off documents."
        )
    if len(uploads) > MAX_FILES:
        raise _problem(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"That is {len(uploads):,} files at once; add at most {MAX_FILES} at a time.",
        )
    refused: list[dict[str, Any]] = []
    if role != "manage":
        names = await _names(store)
        held = {
            i.title.casefold(): i
            for i in await store.collections.items(collection.id)
            if i.kind == "document"
        }
        allowed = []
        for upload in uploads:
            name = _clean_name(upload.filename)
            there = held.get(name.casefold())
            if there is not None and there.added_by != person.sub:
                refused.append(
                    _refusal(
                        name,
                        status.HTTP_409_CONFLICT,
                        f"{collection.name} has a {name} that {_who(names, there.added_by)} "
                        "added. Give yours another name, or ask them or the collection's "
                        "owner to replace it.",
                    )
                )
            else:
                allowed.append(upload)
        uploads = allowed
    kept, more = await keep_files(
        request,
        team.key(collection.id),
        LIBRARY,
        uploads,
        install,
        holder=f"{collection.name} already keeps",
    )
    refused += more
    now = time.time()
    for view in kept:
        for gone in view.get("replaced") or []:
            await store.collections.drop_item(collection.id, gone)
        if not view.get("unchanged"):
            await store.collections.add_item(
                Item(view["id"], collection.id, "document", "active", person.sub, now, view["name"])
            )
    if await store.collections.get(collection.id) is None:
        # Deleted while its files were added: they go too.
        await team.drop(store, _saved(request), collection)
        raise _problem(status.HTTP_404_NOT_FOUND, NO_SUCH)
    if not kept and refused:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=refused[0]["status"],
            detail={"message": refused[0]["message"], "refused": refused},
        )
    return {"documents": kept, "refused": refused, "collectionId": collection.id}


@router.post("/api/collections/{collection_id}/documents", status_code=status.HTTP_201_CREATED)
async def add_documents(
    request: Request, collection_id: str, uploads: list[UploadFile] = File(..., alias="files")
) -> dict[str, Any]:
    person = await _person(request)
    collection, role = await _usable(request, person, collection_id, "contribute")
    return await add_to_collection(request, person, collection, uploads, role)


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(max_length=4000)
    body: str = Field(default="", max_length=memory.MAX_BODY_CHARS)


@router.post("/api/collections/{collection_id}/notes", status_code=status.HTTP_201_CREATED)
async def add_note(request: Request, collection_id: str, body: NoteIn) -> dict[str, Any]:
    """A contributor's own note: searched by the team's chats from now."""
    person = await _person(request)
    collection, _ = await _usable(request, person, collection_id, "contribute")
    if collection.project_id is not None:
        raise _problem(
            status.HTTP_409_CONFLICT,
            "A shared project keeps documents. Keep notes for it on the project's page.",
        )
    store = _store(request)
    try:
        item = await team.add_note(
            store, _saved(request), collection, body.note, body.body, by=person.sub
        )
    except memory.NoteRefused as exc:
        raise _problem(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.message) from None
    return item_view(item, await _names(store))


@router.post("/api/collections/{collection_id}/inbox/{item_id}/accept")
async def accept_note(request: Request, collection_id: str, item_id: str) -> dict[str, Any]:
    """A contributor accepts a note a model proposed: it is the collection's from now."""
    person = await _person(request)
    collection, _ = await _usable(request, person, collection_id, "contribute")
    store = _store(request)
    item = await store.collections.item(collection.id, item_id)
    if item is None or item.state != "proposed":
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such proposed note.")
    await team.accept(store, _saved(request), collection, item, person.sub)
    accepted = await store.collections.item(collection.id, item_id)
    assert accepted is not None
    return item_view(accepted, await _names(store))


@router.delete("/api/collections/{collection_id}/items/{item_id}")
async def remove_item(request: Request, collection_id: str, item_id: str) -> Response:
    """Take a document or a note out of a collection, or dismiss a proposed note. Its manager
    takes out anything; a contributor what they added, and any proposal."""
    person = await _person(request)
    collection, role = await _usable(request, person, collection_id, "contribute")
    store = _store(request)
    item = await store.collections.item(collection.id, item_id)
    if item is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such document or note.")
    if role != "manage" and item.state == "active" and item.added_by != person.sub:
        raise _problem(
            status.HTTP_403_FORBIDDEN,
            "Only who added it, or the collection's owner, can take it out.",
        )
    await team.remove_item(store, _saved(request), collection, item)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/collections/{collection_id}/documents/{doc_id}/passages")
async def document_passages(
    request: Request, collection_id: str, doc_id: str, ids: str
) -> dict[str, Any]:
    """The passages of a collection's document an answer was sent (its Sources)."""
    person = await _person(request)
    collection, _ = await _usable(request, person, collection_id)
    missing = _problem(status.HTTP_404_NOT_FOUND, "There is no such document.")
    if not valid_id(doc_id):
        raise missing
    index = _saved(request).index(team.key(collection.id), create=False)
    if index is None or await index.document(doc_id) is None:
        raise missing
    try:
        wanted = [int(i) for i in ids.split(",") if i.strip()][:50]
    except ValueError:
        raise _problem(status.HTTP_422_UNPROCESSABLE_CONTENT, "ids are numbers.") from None
    return {
        "passages": [
            {"id": h.chunk_id, "text": h.text, "heading": h.heading_path, "page": h.page_start}
            for h in await index.passages(doc_id, wanted)
        ]
    }


# --------------------------------------------------------------------------- #
# the collections a chat, or a project, uses
# --------------------------------------------------------------------------- #


class LinksUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(max_length=50)


async def _links_view(request: Request, person: Person, target: str) -> dict[str, Any]:
    store = _store(request)
    chosen = await team.readable(store, person, await store.collections.links(person.sub, [target]))
    return {
        "chosen": [{"id": c.id, "name": c.name} for c in chosen],
        "available": [
            {
                "id": c.id,
                "name": c.name,
                "role": role,
                "kind": "project" if c.project_id else "install",
            }
            for c, role in await team.visible(store, person)
            if c.belongs_to == team.INSTALL
        ],
    }


async def _set_links(request: Request, person: Person, target: str, ids: list[str]) -> None:
    store = _store(request)
    wanted = list(dict.fromkeys(ids))
    found = await team.readable(store, person, wanted)
    if len(found) != len(wanted) or any(c.belongs_to != team.INSTALL for c in found):
        raise _problem(status.HTTP_404_NOT_FOUND, NO_SUCH)
    await store.collections.set_links(person.sub, target, wanted)


@router.get("/api/chats/{chat_id}/collections")
async def chat_collections(request: Request, chat_id: str) -> dict[str, Any]:
    """The team collections this chat searches, and the ones the asker may choose."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    from .saved_api import _local, decide_access

    out = await _links_view(request, person, f"chat:{chat.id}")
    used = await team.for_chat(
        _store(request), person, chat, documents_on=chat.settings.get("documents") == "on"
    )
    # What its next answer is sent, never only what was chosen: a model outside the machines,
    # or documents turned off, keeps some or all of them back, and each says why.
    access = await decide_access(request, person, chat, chat.model)
    sent, held = team.split(
        used, allowed=access.library, local=_local(access), reason=access.reason
    )
    out["searched"] = [{"id": c.id, "name": c.name} for c in sent]
    out["held"] = [{"id": c.id, "name": c.name, "why": why} for c, why in held]
    return out


@router.put("/api/chats/{chat_id}/collections")
async def put_chat_collections(request: Request, chat_id: str, body: LinksUpdate) -> dict[str, Any]:
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    await _set_links(request, person, f"chat:{chat.id}", body.ids)
    return await chat_collections(request, chat_id)


@router.get("/api/projects/{project_id}/collections")
async def project_collections(request: Request, project_id: str) -> dict[str, Any]:
    """The team collections every chat in this project searches (its owner's choice)."""
    person = await _person(request)
    if await _store(request).project(person.sub, project_id) is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such project.")
    return await _links_view(request, person, f"project:{project_id}")


@router.put("/api/projects/{project_id}/collections")
async def put_project_collections(
    request: Request, project_id: str, body: LinksUpdate
) -> dict[str, Any]:
    person = await _person(request)
    if await _store(request).project(person.sub, project_id) is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such project.")
    await _set_links(request, person, f"project:{project_id}", body.ids)
    return await _links_view(request, person, f"project:{project_id}")
