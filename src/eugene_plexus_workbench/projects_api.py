"""Memory v2's MV7: a person's projects, over HTTP.

`/api/projects` lists, makes, renames and deletes them; a chat is put in one with
`PATCH /api/chats/{id}` (`projectId`), and a note with `POST /api/memory/notes/{id}/move`.
Deleting a project asks one question with two answers (`keep`): its chats and notes go with
it, or they stay and become general. Either way nothing is left under its id.

**Sharing a project** (row 47): its owner shares it with people and groups (`PUT
/api/projects/{id}/sharing`), each `read` or `contribute`. They see it under Shared with me,
read its instructions and pinned notes, search its documents, and start their own chats in it.
**Chats are never shared**: each person sees only their own chats in it, and its owner a count
of everyone else's. When it is first shared, the documents its owner added move into the
project's own collection (`team.share_project`), where contributors add theirs.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import memory, memory_api, projects, team, team_api
from .api import _answers, _new_id, _person, _problem, _state, chat_view, remove_chat
from .hub import HubError
from .store import Person, Project, Store

log = logging.getLogger(__name__)

router = APIRouter()


def _store(request: Request) -> Store:
    store: Store = _state(request).store
    return store


def project_view(project: Project, chats: int = 0, notes: int = 0) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "generalMemory": project.general_memory,
        "generalDocuments": project.general_documents,
        "createdAt": project.created_at,
        "updatedAt": project.updated_at,
        "chats": chats,
        "notes": notes,
        "model": project.model,
    }


async def model_view(request: Request, project: Project) -> dict[str, Any]:
    try:
        effective, models = await projects.model_in_effect(_state(request).hub, project.model)
        return {
            "model": project.model,
            "modelInEffect": effective,
            "models": models,
            "modelProblem": None,
        }
    except HubError as exc:
        return {
            "model": project.model,
            "modelInEffect": None,
            "models": [],
            "modelProblem": f"Workbench could not check which model is served: {exc.message}",
        }


def _name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name:
        raise _problem(status.HTTP_422_UNPROCESSABLE_CONTENT, "A project needs a name.")
    if memory._hidden(name):
        raise _problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "A project's name cannot hold characters that are not seen.",
        )
    if len(name) > projects.NAME_CHARS:
        raise _problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"A project's name is at most {projects.NAME_CHARS} characters.",
        )
    return name


async def _counts(store: Store, owner: str) -> tuple[dict[str, int], dict[str, int]]:
    return await store.project_counts(owner)


async def _own(request: Request, owner: str, project_id: str) -> Project:
    project = await _store(request).project(owner, project_id)
    if project is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such project.")
    return project


async def _put(request: Request, project: Project, *, retake: bool = False) -> None:
    why = await _store(request).put_project(project, limit=projects.MAX_PROJECTS, retake=retake)
    if why == "taken":
        raise _problem(status.HTTP_409_CONFLICT, "You have a project of that name already.")
    if why == "full":
        raise _problem(
            status.HTTP_409_CONFLICT, f"You can have at most {projects.MAX_PROJECTS} projects."
        )


@router.get("/api/projects")
async def list_projects(request: Request) -> dict[str, Any]:
    """The asker's projects, by name, each with how many chats and notes it holds; and the
    projects shared with them now (row 47), with how many of their own chats each holds."""
    person = await _person(request)
    store = _store(request)
    chats, notes = await _counts(store, person.sub)
    return {
        "projects": [
            project_view(p, chats.get(p.id, 0), notes.get(p.id, 0))
            for p in await store.projects(person.sub)
        ],
        "shared": await shared_list(store, person, chats),
    }


async def shared_list(store: Store, person: Person, chats: dict[str, int]) -> list[dict[str, Any]]:
    names = {p.sub: p.name for p, _, _ in await store.people()}
    out = []
    groups = [g["id"] for g in person.groups or []]
    for project_id, role in (await store.collections.shared_with(person.sub, groups)).items():
        project = await store.shared_project(project_id)
        if project is None or project.owner == person.sub:
            continue
        out.append(
            {
                "id": project.id,
                "name": project.name,
                "owner": names.get(project.owner, "Someone"),
                "role": role,
                "chats": chats.get(project.id, 0),
            }
        )
    return sorted(out, key=lambda v: (v["name"].casefold(), v["id"]))


async def sharing_view(request: Request, project: Project) -> dict[str, Any]:
    """Who a project is shared with, its collection, and how many chats others have in it."""
    store = _store(request)
    names = {p.sub: p.name for p, _, _ in await store.people()}
    collection = await store.collections.of_project(project.id)
    return {
        "shares": [
            {
                "kind": g.kind,
                "id": g.subject,
                "name": names.get(g.subject, "a former member") if g.kind == "person" else None,
                "role": g.role,
            }
            for g in await store.collections.shares(project.id)
        ],
        "collectionId": collection.id if collection is not None else None,
        # What its owner sees of other people's chats in it: a count, never their content.
        "othersChats": await store.others_chats_in(project.id, project.owner),
        # Documents other people added, which deleting it takes too: what the question names.
        "othersDocuments": (
            sum(
                i.kind == "document" and i.added_by != project.owner
                for i in await store.collections.items(collection.id)
            )
            if collection is not None
            else 0
        ),
        "outsideModels": collection.outside if collection is not None else False,
    }


async def _shared_project(request: Request, person: Person, project_id: str) -> dict[str, Any]:
    """A project shared with the asker: its instructions and pinned notes (read only), its
    documents, and the asker's own chats in it. The same 404 as a project that is not there
    when it is not shared with them now."""
    store = _store(request)
    project = await store.shared_project(project_id)
    role = await team.project_role(store, person, project) if project is not None else None
    if project is None or role is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such project.")
    names = {p.sub: p.name for p, _, _ in await store.people()}
    mine = [c for c in await store.chats(person.sub) if c.project_id == project.id]
    pinned = await projects.shared_notes(store, project.owner, project.id)
    collection = await store.collections.of_project(project.id)
    documents = (
        sum(i.kind == "document" for i in await store.collections.items(collection.id))
        if collection is not None
        else 0
    )
    answers = _answers(request)
    return {
        **project_view(project, len(mine), 0),
        **await model_view(request, project),
        "shared": {"owner": names.get(project.owner, "Someone"), "role": role},
        "instructions": [{"id": n.id, "text": n.text, "kind": n.kind} for n in pinned],
        "documents": documents,
        "collectionId": collection.id if collection is not None else None,
        "folders": 0,
        "chatList": [chat_view(c, running=answers.running(c.id) is not None) for c in mine],
    }


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=400)


@router.post("/api/projects", status_code=status.HTTP_201_CREATED)
async def create_project(request: Request, body: ProjectCreate) -> dict[str, Any]:
    person = await _person(request)
    now = time.time()
    project = Project(
        id=_new_id(), owner=person.sub, name=_name(body.name), created_at=now, updated_at=now
    )
    await _put(request, project)
    return project_view(project)


@router.get("/api/projects/{project_id}")
async def get_project(request: Request, project_id: str) -> dict[str, Any]:
    """One project, with its chats, newest first."""
    person = await _person(request)
    store = _store(request)
    if await store.project(person.sub, project_id) is None:
        return await _shared_project(request, person, project_id)
    project = await _own(request, person.sub, project_id)
    mine = [c for c in await store.chats(person.sub) if c.project_id == project.id]
    notes = [n for n in await store.memory_notes(person.sub) if n.project_id == project.id]
    answers = _answers(request)
    saved = getattr(_state(request), "saved", None)
    index = saved.index(person.sub, create=False) if saved is not None else None
    documents = (
        len(await index.documents(f"project:{project.id}", only="upload"))
        if index is not None
        else 0
    )
    sharing = await sharing_view(request, project)
    if sharing["collectionId"] is not None:
        # Row 47: a shared project's documents are its collection's.
        documents += sum(
            i.kind == "document" for i in await store.collections.items(sharing["collectionId"])
        )
    return {
        **project_view(project, len(mine), len(notes)),
        **await model_view(request, project),
        "sharing": sharing,
        "collectionId": sharing["collectionId"],
        # MV7b: how many documents are kept to it (what Delete project names).
        "documents": documents,
        "folders": sum(f.project_id == project.id for f in await store.folder_records(person.sub)),
        "chatList": [chat_view(c, running=answers.running(c.id) is not None) for c in mine],
    }


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=400)
    #: Also use my general memory.
    generalMemory: StrictBool | None = None
    #: Also search My documents (MV7b).
    generalDocuments: StrictBool | None = None
    model: str | None = Field(default=None, min_length=1, max_length=256)


@router.patch("/api/projects/{project_id}")
async def update_project(request: Request, project_id: str, body: ProjectUpdate) -> dict[str, Any]:
    person = await _person(request)
    project = await _own(request, person.sub, project_id)
    if body.name is not None:
        project.name = _name(body.name)
        shared = await _store(request).collections.of_project(project.id)
        if shared is not None:
            await _store(request).collections.rename(shared.id, project.name, time.time())
    switched = body.generalMemory is not None and body.generalMemory != project.general_memory
    if body.generalMemory is not None:
        project.general_memory = body.generalMemory
    if body.generalDocuments is not None:
        project.general_documents = body.generalDocuments
    project.updated_at = time.time()
    # Its chats' frozen pinned blocks were taken with, or without, general notes: they go
    # with the switch, in one step.
    await _put(request, project, retake=switched)
    if "model" in body.model_fields_set:
        await _store(request).set_project_model(person.sub, project.id, body.model)
        project.model = body.model
    chats, notes = await _counts(_store(request), person.sub)
    return {
        **project_view(project, chats.get(project.id, 0), notes.get(project.id, 0)),
        **await model_view(request, project),
    }


@router.delete("/api/projects/{project_id}")
async def delete_project(request: Request, project_id: str, keep: bool = False) -> dict[str, Any]:
    """Delete a project. With `keep`, its chats and notes stay and become general; without,
    they go with it, as deleting each would (their words out of the index first). 503 when
    something could not be taken out of the index: then what went is said, and the project
    stays, holding what is left, so the person can try again."""
    person = await _person(request)
    project = await _own(request, person.sub, project_id)
    store = _store(request)
    saved = getattr(_state(request), "saved", None)
    if keep:
        failed_folders = []
        moved_folders = 0
        for folder in await store.folder_records(person.sub):
            if folder.project_id == project.id:
                try:
                    await _state(request).folder_sync.move(person.sub, folder.grant_id, None)
                    moved_folders += 1
                except Exception as exc:
                    failed_folders.append(f"{folder.name} ({type(exc).__name__}: {exc})")
        if failed_folders:
            raise _problem(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"Moved {moved_folders} folders to My documents. These folders could not be moved: "
                + "; ".join(failed_folders)
                + ". The project and its other contents were kept. Try again in a moment.",
            )
        # Its documents move to My documents first, as uploads of their names would (MV7b):
        # all or nothing, so a move that fails leaves the project whole to try again.
        moved = replaced = 0
        if saved is not None:
            try:
                moved, replaced = await saved.release_project(person.sub, project.id)
            except Exception as exc:
                log.warning("a project's documents were not moved: %s: %s", type(exc).__name__, exc)
                raise _problem(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Workbench could not move this project's documents to My documents, so "
                    f"nothing was deleted ({type(exc).__name__}). Try again in a moment.",
                ) from None
        # Row 47: a shared project's documents move to My documents too, and its shares end.
        try:
            moved += await team.release_project(store, saved, project)
        except Exception as exc:
            log.warning("a shared project's documents were not moved: %s", type(exc).__name__)
            raise _problem(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Workbench could not move this project's shared documents to My documents, so "
                f"nothing more was deleted ({type(exc).__name__}). Try again in a moment.",
            ) from None
        await team.end_project(store, saved, project.id)
        chats, notes = await store.release_project(person.sub, project.id)
        return {
            "kept": True,
            "chats": chats,
            "notes": notes,
            "documents": moved,
            "replaced": replaced,
        }
    for folder in await store.folder_records(person.sub):
        if folder.project_id == project.id:
            await _state(request).folder_sync.remove(person.sub, folder.grant_id)
    gone_chats = gone_notes = 0
    for chat in [c for c in await store.chats(person.sub) if c.project_id == project.id]:
        async with _state(request).branches.lock(chat.id):
            await remove_chat(request, person, chat)
        gone_chats += 1
    for note in [n for n in await store.memory_notes(person.sub) if n.project_id == project.id]:
        try:
            gone_notes += len(await memory_api.forget(_state(request), person.sub, note.id))
        except Exception as exc:
            log.warning("a project's note was not deleted: %s: %s", type(exc).__name__, exc)
            raise _problem(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"Workbench deleted {gone_chats} chats and {gone_notes} notes of this project, "
                "but could not remove a note's words from your search index, so the project "
                f"and its other notes were kept ({type(exc).__name__}). Try again in a moment.",
            ) from None
    gone_documents = await saved.drop_project(person.sub, project.id) if saved is not None else 0
    # Row 47: its collection, its shares and everyone's choices for it go too. Other people's
    # chats in it stay theirs, in no project.
    gone_documents += await team.end_project(store, saved, project.id)
    # Suggestions still waiting from its chats went with them (`delete_chat`).
    await store.drop_project_row(person.sub, project.id)
    return {
        "kept": False,
        "chats": gone_chats,
        "notes": gone_notes,
        "documents": gone_documents,
    }


class SharingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shares: list[team_api.GrantIn] = Field(max_length=200)


@router.put("/api/projects/{project_id}/sharing")
async def put_sharing(request: Request, project_id: str, body: SharingUpdate) -> dict[str, Any]:
    """Share a project with people and groups, each `read` or `contribute`, or with nobody.
    Its owner only. Shared the first time, the documents its owner added move into its own
    collection. Someone taken off the share loses its notes and documents at their next answer;
    their chats in it stay theirs."""
    person = await _person(request)
    project = await _own(request, person.sub, project_id)
    store = _store(request)
    shares = await team_api.check_grantees(request, person, body.shares, any_group=person.is_owner)
    if shares:
        # Every time: a first share cut short moves the rest now.
        try:
            await team.share_project(store, _state(request).saved, project)
        except team.ShareIncomplete as exc:
            raise _problem(status.HTTP_503_SERVICE_UNAVAILABLE, exc.message) from None
    await store.collections.set_shares(project.id, shares)
    return await sharing_view(request, project)


class NoteMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: The project to keep it to, or null for General.
    projectId: str | None = Field(default=None, max_length=200)


@router.post("/api/memory/notes/{note_id}/move")
async def move_note(request: Request, note_id: str, body: NoteMove) -> dict[str, Any]:
    """Keep a current note, and its earlier versions, to a project, or make it general."""
    person = await _person(request)
    store = _store(request)
    note = await store.memory_note(person.sub, note_id, with_body=False)
    if note is None or note.status != "active":
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such note.")
    if body.projectId is not None:
        await _own(request, person.sub, body.projectId)
    chain = [note_id, *[n.id for n in await store.note_history(person.sub, note_id)]]
    await store.move_notes(person.sub, chain, body.projectId)
    moved = await store.memory_note(person.sub, note_id)
    assert moved is not None
    return {"note": memory_api.note_view(moved)}
