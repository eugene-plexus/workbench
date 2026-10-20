"""Workbench's own API, for its own page, and signing in.

Every `/api` route but `/api/status` needs a session (`sessions.py`: the
cookie and the request secret), and every chat, message and file is
filtered by who is asking. **A chat that is not yours is the same 404 as
one that does not exist**, so nothing here says whose chats exist --
except to the owner, read-only, when the business has turned
`ownerReadsChats` on (W4).
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO, Literal
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field, StrictBool

from . import artifacts, config, files, memory_write
from .answers import Answers, Preparation, Progress, message_view
from .context import reading_view
from .conversation import SAMPLING, request_for
from .hub import Hub, HubError
from .sessions import (
    SESSION_COOKIE,
    SESSION_SECONDS,
    SIGNIN_COOKIE,
    Sessions,
    SignedOut,
    cookie_name,
    own_origin,
    signin_path,
)
from .signin import PENDING_SECONDS, Provider, SignInRefused, SignInUnavailable
from .store import (
    AttachmentGone,
    Chat,
    FileInUse,
    FileRecord,
    Message,
    Person,
    Store,
    path,
    versions,
)
from .tools import (
    CHATS_SERVER,
    CHATS_TOOL_NAME,
    MEMORY_SERVER,
    MEMORY_TOOL_NAME,
    REMEMBER_TOOL_NAME,
    WRITE_SERVER,
)

log = logging.getLogger(__name__)

router = APIRouter()

NEW_CHAT = "New chat"
_TITLE_LENGTH = 60
_KEEPALIVE_SECONDS = 15.0


def _problem(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"message": message})


def _state(request: Request) -> Any:
    return request.app.state


async def _person(request: Request) -> Person:
    sessions: Sessions = _state(request).sessions
    return await sessions.person(request)


def _new_id() -> str:
    return secrets.token_urlsafe(12)


# --------------------------------------------------------------------------- #
# health and status
# --------------------------------------------------------------------------- #


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, Any]:
    origin = _state(request).settings.public_origin
    return {"status": "ok", **({"publicOrigin": origin, "originIsolation": 1} if origin else {})}


@router.get("/api/status")
async def get_status(request: Request) -> dict[str, Any]:
    """What the page needs before anyone has signed in."""
    provider: Provider = _state(request).provider
    reason = provider.why_not()
    return {"signIn": {"available": reason is None, "reason": reason}}


# --------------------------------------------------------------------------- #
# signing in
# --------------------------------------------------------------------------- #


def _to_page(fragment: str) -> RedirectResponse:
    response = RedirectResponse(f"/#{fragment}", status_code=status.HTTP_303_SEE_OTHER)
    response.headers["Cache-Control"] = "no-store"
    return response


def _sign_in_failed(request: Request, message: str) -> RedirectResponse:
    response = _to_page("signin-error=" + quote(message, safe=""))
    response.delete_cookie(
        cookie_name(request, SIGNIN_COOKIE),
        path=signin_path(request),
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/signin")
async def sign_in(request: Request) -> Response:
    provider: Provider = _state(request).provider
    try:
        url, pending = await provider.start(f"{own_origin(request)}/oidc/callback")
    except SignInUnavailable as exc:
        return _sign_in_failed(request, str(exc))
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    # Ties the callback to the browser that started it, so a code from
    # someone else's sign-in cannot be finished in this one (login CSRF).
    response.set_cookie(
        cookie_name(request, SIGNIN_COOKIE),
        pending.binding,
        max_age=int(PENDING_SECONDS),
        path=signin_path(request),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/oidc/callback")
async def sign_in_callback(request: Request) -> Response:
    provider: Provider = _state(request).provider
    sessions: Sessions = _state(request).sessions
    store: Store = _state(request).store
    query = request.query_params
    pending = provider.take(query.get("state"))
    if pending is None or not secrets.compare_digest(
        request.cookies.get(cookie_name(request, SIGNIN_COOKIE), ""), pending.binding
    ):
        return _sign_in_failed(
            request, "This sign-in was started in another browser or has expired. Sign in again."
        )
    if query.get("error"):
        return _sign_in_failed(
            request, str(query.get("error_description") or query.get("error") or "Eugene said no.")
        )
    code = query.get("code")
    if not code:
        return _sign_in_failed(request, "Eugene sent no sign-in code back. Sign in again.")
    try:
        identity = await provider.finish(pending, code, query.get("iss"))
    except (SignInRefused, SignInUnavailable) as exc:
        return _sign_in_failed(request, str(exc))
    await store.upsert_person(
        Person(sub=identity.sub, name=identity.name, role=identity.role, username=identity.username)
    )
    created = await sessions.create(
        identity.sub, identity.refresh_token, identity.expires_in, identity.access_token
    )
    log.info("%s signed in", identity.name)
    # The secret goes in the fragment, which no server is ever sent (W3).
    response = _to_page("signin=" + created.secret)
    response.delete_cookie(
        cookie_name(request, SIGNIN_COOKIE),
        path=signin_path(request),
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        cookie_name(request, SESSION_COOKIE),
        created.cookie,
        max_age=SESSION_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.post("/api/signout", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(request: Request) -> Response:
    sessions: Sessions = _state(request).sessions
    await _person(request)
    await sessions.end(request)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        cookie_name(request, SESSION_COOKIE),
        path="/",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )
    return response


async def install_mode(request: Request) -> dict[str, Any]:
    remote = _state(request).tools.node_folders
    if remote is None:
        return {"mode": "production", "changedAt": None, "known": False}
    value: dict[str, Any] = await remote.install_mode()
    return value


def _mode_seen_key(sub: str) -> str:
    return f"installModeSeen:{sub}"


def _run_previews_key(sub: str) -> str:
    """Whether a person's artifact previews run when shown (rich content RC6); on unless
    they turned it off."""
    return f"runPreviews:{sub}"


class RunPreviews(BaseModel):
    on: StrictBool


@router.get("/api/me")
async def me(request: Request) -> dict[str, Any]:
    person = await _person(request)
    reads = await config.owner_reads_chats(_state(request).store)
    mode = await install_mode(request)
    seen = await _state(request).store.setting(_mode_seen_key(person.sub))
    run_previews = await _state(request).store.setting(_run_previews_key(person.sub))
    return {
        # Every person sees the install's mode, and is told when it changed
        # since they last acknowledged it (J18).
        "installMode": mode["mode"],
        "installModeChangedAt": mode["changedAt"],
        "installModeNotice": bool(mode["changedAt"] and mode["changedAt"] != seen),
        "sub": person.sub,
        "name": person.name,
        "username": person.username,
        "owner": person.is_owner,
        "accountUrl": (
            str(_state(request).settings.oidc_issuer).rstrip("/") + "/account"
            if not person.is_owner and _state(request).settings.oidc_issuer
            else None
        ),
        "consoleUrl": (
            str(_state(request).settings.oidc_issuer).removesuffix("/oidc")
            if person.is_owner and _state(request).settings.oidc_issuer
            else None
        ),
        # The standing line (W4) is for everyone the owner can read.
        "ownerReadsChats": reads,
        "runPreviews": run_previews is not False,
    }


@router.put("/api/me/run-previews")
async def put_run_previews(request: Request, body: RunPreviews) -> dict[str, Any]:
    """Whether the person's artifact previews run when shown, or wait for Run."""
    person = await _person(request)
    await _state(request).store.put_setting(_run_previews_key(person.sub), body.on)
    return {"runPreviews": body.on}


@router.post("/api/me/mode-seen", status_code=status.HTTP_204_NO_CONTENT)
async def mode_seen(request: Request) -> Response:
    """The person read the notice that the mode changed."""
    person = await _person(request)
    mode = await install_mode(request)
    if mode["changedAt"]:
        await _state(request).store.put_setting(_mode_seen_key(person.sub), mode["changedAt"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #


@router.get("/api/models")
async def models(request: Request) -> dict[str, Any]:
    await _person(request)
    hub: Hub = _state(request).hub
    try:
        listing = await hub.models()
    except HubError as exc:
        raise _problem(exc.status if exc.status < 500 else 502, exc.message) from exc
    out = []
    for model in listing.get("data") or []:
        info = model.get("x_eugene_plexus") or {}
        surfaces = info.get("surfaces")
        if surfaces is not None and "chat" not in surfaces:
            continue
        out.append(
            {
                "id": model.get("id"),
                "contextLength": info.get("context_length"),
                "imageInput": bool(info.get("image_input")),
                "audioInput": bool(info.get("audio_input")),
                "fileInput": bool(info.get("file_input")),
                "webSearch": bool(info.get("web_search")),
                "ready": (info.get("ready_backends") or 0) > 0,
                "onDemand": bool(info.get("on_demand")),
                # Where it runs, as the gateway says: `local` only when every
                # backend serving it is on this install's machines. A model
                # the gateway does not place is `unknown`, which is not
                # trusted with a person's notes (memory).
                "locality": info.get("locality")
                if info.get("locality") in ("local", "external")
                else "unknown",
            }
        )
    search = (listing.get("x_eugene_plexus") or {}).get("web_search") or {
        "available": True,
        "reason": None,
    }
    return {
        "models": out,
        "webSearch": {"available": bool(search.get("available")), "reason": search.get("reason")},
    }


# --------------------------------------------------------------------------- #
# chats
# --------------------------------------------------------------------------- #


class ChatCreate(BaseModel):
    model: str | None = Field(default=None, max_length=256)


class ChatSettings(BaseModel):
    toolServers: list[str] = Field(default_factory=list, max_length=8)
    folderGrants: list[str] = Field(default_factory=list, max_length=8)
    instructions: str | None = Field(default=None, max_length=20000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    topP: float | None = Field(default=None, gt=0, le=1)
    maxTokens: int | None = Field(default=None, ge=1, le=1_000_000)
    repetitionMode: Literal["off", "observe", "stop"] | None = None
    #: Memory in this chat: `off` sends no notes to its model; unset follows the
    #: person's own setting.
    memory: Literal["on", "off"] | None = None
    #: The person chose to send their notes to this chat's model even though it
    #: runs outside their machines ("Send for this chat").
    memoryExternal: bool | None = None
    #: Documents in this chat (MR4): `on` searches the person's saved documents for each
    #: question and sends the best passages; unset is off. The chat's own files the
    #: person chose to search instead are searched whatever this says.
    documents: Literal["on", "off"] | None = None
    #: "Answer only from my documents": when nothing is found Workbench says so itself
    #: and does not ask the model.
    documentsOnly: bool | None = None
    #: The person chose to send passages of their saved documents to this chat's model
    #: even though it runs outside their machines ("Send for this chat").
    documentsExternal: bool | None = None
    #: Use past chats (MV2): each turn carries the person's earlier exchanges that match
    #: what they just asked. Unset is off.
    pastChats: bool | None = None


class ChatUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=256)
    search: bool | None = None
    settings: ChatSettings | None = None


class Send(BaseModel):
    content: str = Field(default="", max_length=200_000)
    attachments: list[str] = Field(default_factory=list, max_length=32)
    model: str | None = Field(default=None, max_length=256)
    search: bool | None = None


class Edit(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)


def chat_view(chat: Chat, *, running: bool, read_only: bool = False) -> dict[str, Any]:
    return {
        "id": chat.id,
        "title": chat.title,
        "model": chat.model,
        "search": chat.search,
        "settings": {
            k: chat.settings.get(k)
            for k in (
                "instructions",
                "toolServers",
                "folderGrants",
                "repetitionMode",
                "memory",
                "memoryExternal",
                "documents",
                "documentsOnly",
                "documentsExternal",
                "pastChats",
                *SAMPLING,
            )
        },
        "createdAt": chat.created_at,
        "updatedAt": chat.updated_at,
        "running": running,
        "readOnly": read_only,
    }


async def _own_chat(request: Request, person: Person, chat_id: str) -> Chat:
    """The asker's chat, or the 404 a chat that does not exist gets."""
    store: Store = _state(request).store
    chat = await store.chat(chat_id)
    if chat is None or chat.owner != person.sub:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such chat.")
    return chat


async def _readable_chat(request: Request, person: Person, chat_id: str) -> tuple[Chat, bool]:
    """A chat the asker may read, and whether only to read it (W4)."""
    store: Store = _state(request).store
    chat = await store.chat(chat_id)
    if chat is not None and chat.owner == person.sub:
        return chat, False
    if chat is not None and person.is_owner and await config.owner_reads_chats(store):
        return chat, True
    raise _problem(status.HTTP_404_NOT_FOUND, "There is no such chat.")


def _answers(request: Request) -> Answers:
    answers: Answers = _state(request).answers
    return answers


@router.get("/api/chats")
async def list_chats(request: Request) -> dict[str, Any]:
    person = await _person(request)
    answers = _answers(request)
    chats = await _state(request).store.chats(person.sub)
    return {
        "chats": [
            chat_view(
                c, running=answers.running(c.id) is not None or _state(request).agui.busy(c.id)
            )
            for c in chats
        ]
    }


@router.post("/api/chats", status_code=status.HTTP_201_CREATED)
async def create_chat(request: Request, body: ChatCreate) -> dict[str, Any]:
    person = await _person(request)
    now = time.time()
    store: Store = _state(request).store
    # Documents are off in a new chat unless the person has said otherwise for new chats.
    prefs = await store.documents_prefs(person.sub)
    chat = Chat(
        id=_new_id(),
        owner=person.sub,
        title=NEW_CHAT,
        model=body.model,
        created_at=now,
        updated_at=now,
        settings={"documents": "on"} if prefs["new_chats"] else {},
    )
    await store.create_chat(chat)
    return chat_view(chat, running=False)


@router.get("/api/chats/{chat_id}")
async def get_chat(request: Request, chat_id: str, via: str | None = None) -> dict[str, Any]:
    """The chat's path, each message with its place among its versions.

    `via` is the path through that message instead, and saves nothing: the
    owner's read-only view steps through every branch with it (V5)."""
    person = await _person(request)
    chat, read_only = await _readable_chat(request, person, chat_id)
    return await _chat_detail(request, chat, read_only=read_only, via=via)


async def _chat_detail(
    request: Request, chat: Chat, *, read_only: bool, via: str | None = None
) -> dict[str, Any]:
    store: Store = _state(request).store
    running = _answers(request).running(chat.id)
    tree = await store.tree(chat.id)
    if running is not None:
        # The answer being written, as it stands now rather than as last saved.
        tree = [
            replace(running.message, parent_id=m.parent_id, chosen=m.chosen)
            if m.id == running.message.id
            else m
            for m in tree
        ]
    if via is not None and all(m.id != via for m in tree):
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such message in this chat.")
    places = versions(tree)
    attached = {f.id: f for f in await store.files_for_chat(chat.id, uploads=True)}
    kept: dict[str, list[dict[str, Any]]] = {}
    for record in await store.artifacts_for_chat(chat.id):
        kept.setdefault(record.message_id, []).append(artifacts.summary(record))
    views = []
    for m in path(tree, via):
        view = message_view(m)
        # What each block the person changed keeps (rich content RC5), for the page
        # to find by its block's words.
        view["artifacts"] = kept.get(m.id, [])
        view["files"] = [
            {
                "id": i,
                "name": attached[i].name,
                "mediaType": attached[i].media_type,
                "reading": reading_view(attached[i]),
            }
            for i in m.attachments
            if i in attached
        ]
        view["versions"] = places[m.id]
        views.append(view)
    owner = await store.person(chat.owner) if read_only else None
    if read_only:
        views = redact_for_owner(
            views, hidden_from_owner(tree, (await install_mode(request))["mode"])
        )
    return {
        "chat": chat_view(
            chat,
            running=running is not None or _state(request).agui.busy(chat.id),
            read_only=read_only,
        ),
        "messages": views,
        "ownerName": owner.name if owner else None,
    }


def _hidden_call(call: dict[str, Any], mode: str) -> bool:
    """A job-site call the owner may not read: everything unless it was made
    in dev mode and the install is still in dev mode (J13, J18)."""
    return bool(call.get("jobSite")) and not (mode == "dev" and call.get("mode") == "dev")


def _hidden_site(message: Message, mode: str) -> str | None:
    for round_ in message.tool_rounds:
        for call in round_.get("calls") or []:
            if _hidden_call(call, mode):
                return str(call.get("label") or call.get("site") or "a job site")
    return None


def hidden_from_owner(tree: list[Message], mode: str) -> dict[str, str]:
    """What production mode hides from the owner, each with the machine it
    names: a chat from its first job-site result on (J13a), along each path.

    The model's later replies quote what it read, so the result alone is not
    enough. A message is hidden when it holds such a result, or when the
    message it follows is hidden. Rows come in `seq` order, a parent before
    its versions, so this is one walk; a branch that never touched a job
    site stays visible."""
    hidden: dict[str, str] = {}
    for message in tree:
        site = hidden.get(message.parent_id) if message.parent_id is not None else None
        site = site or _hidden_site(message, mode)
        if site is not None:
            hidden[message.id] = site
    return hidden


#: The tools Workbench runs over a person's own memory (MV1, MV2): never the owner's to read.
_PRIVATE_SERVERS = (MEMORY_SERVER, CHATS_SERVER, WRITE_SERVER)


def _touch_exchanges(request: Request, owner: str, chat_id: str) -> None:
    """The chat's exchanges may have changed (MV2): its index copy is synced soon."""
    keeper = getattr(_state(request), "exchanges_index", None)
    if keeper is not None:
        keeper.touch(owner, chat_id)


def hide_notes(view: dict[str, Any]) -> dict[str, Any]:
    """A message as the install's owner may read it, with the person's notes hidden (MV1):
    what its turn was sent of them is counts only, and a `search_memory` call keeps neither
    what was asked nor what it found. A person's notes are never the owner's to read."""
    used = view.get("memoryUsed")
    rounds = view.get("toolRounds") or []
    searched = any(
        call.get("serverId") in _PRIVATE_SERVERS
        for turn in rounds
        for call in turn.get("calls") or []
    )
    if not used and not searched:
        return view
    out = dict(view)
    if isinstance(used, dict):
        past = used.get("pastChats")
        out["memoryUsed"] = {
            "notes": len(used.get("notes") or []),
            "tokens": used.get("tokens"),
            "budget": used.get("budget"),
            "redacted": True,
            # MV2: the past chats a turn was sent are the person's, as their notes are.
            **(
                {"pastChats": {"exchanges": len(past.get("exchanges") or []), "redacted": True}}
                if isinstance(past, dict)
                else {}
            ),
        }
    if searched:
        out["toolRounds"] = [
            {
                **turn,
                "assistant": _hide_memory_calls(turn.get("assistant") or {}),
                "calls": [
                    {
                        **call,
                        "arguments": {},
                        "result": "Hidden: the person's past chats."
                        if call.get("serverId") == CHATS_SERVER
                        else "Hidden: the person's notes.",
                        "content": [],
                        "chats": [],
                        "note": None,
                        "noteText": None,
                        "redacted": True,
                    }
                    if call.get("serverId") in _PRIVATE_SERVERS
                    else call
                    for call in turn.get("calls") or []
                ],
            }
            for turn in rounds
        ]
    return out


def _hide_memory_calls(assistant: dict[str, Any]) -> dict[str, Any]:
    calls = assistant.get("tool_calls") or []
    if not calls:
        return assistant
    return {
        **assistant,
        "tool_calls": [
            {**c, "function": {**(c.get("function") or {}), "arguments": "{}"}}
            if (c.get("function") or {}).get("name")
            in (MEMORY_TOOL_NAME, CHATS_TOOL_NAME, REMEMBER_TOOL_NAME)
            else c
            for c in calls
        ],
    }


def hide_saved_documents(view: dict[str, Any]) -> dict[str, Any]:
    """A message as the install's owner may read it, with the passages of a person's
    saved documents hidden: "a document", and no name, no words, no headings, no page
    (MR4). Saved documents are never the owner's to read, even where the person's chats
    are; a file the person attached to the chat is in the chat and stays readable."""
    view = hide_notes(view)
    sources = view.get("sources") or []
    if not any(s.get("kind") == "document" and s.get("passage") for s in sources):
        return view
    hidden = [
        {
            "kind": "document",
            "passage": True,
            "name": "a document",
            "scope": s.get("scope"),
            "rank": s.get("rank"),
            "via": s.get("via"),
            "redacted": True,
        }
        if s.get("kind") == "document" and s.get("passage")
        else s
        for s in sources
    ]
    retrieval = view.get("retrieval")
    if isinstance(retrieval, dict):
        # What was searched for is the person's own question, which the owner reads in
        # the chat where they may; what the person keeps, and what was found, is not.
        retrieval = {**retrieval, "cited": [], "searched": None, "budget": None}
    return {**view, "sources": hidden, "retrieval": retrieval}


def redact_for_owner(views: list[dict[str, Any]], hidden: dict[str, str]) -> list[dict[str, Any]]:
    """The owner sees each hidden message as one line saying which machine's
    files it used. Its place among its versions stays, so every branch can
    still be reached (V5). The passages of saved documents are hidden in every
    message (`hide_saved_documents`)."""
    out: list[dict[str, Any]] = []
    for view in views:
        site = hidden.get(view["id"])
        if site is None:
            out.append(hide_saved_documents(view))
            continue
        out.append(
            {
                "content": "",
                "reasoning": "",
                "sources": [],
                "searches": 0,
                "searchSuggestions": [],
                "search": False,
                "id": view["id"],
                "seq": view["seq"],
                "role": view["role"],
                "status": view["status"],
                "createdAt": view["createdAt"],
                "finishedAt": view["finishedAt"],
                "attachments": [],
                "files": [],
                "artifacts": [],
                "toolRounds": [],
                "error": None,
                "model": view.get("model"),
                "finish": view.get("finish"),
                "answerFrom": None,
                "reasoningFrom": None,
                "versions": view.get("versions"),
                "redacted": {"site": site},
            }
        )
    return out


@router.patch("/api/chats/{chat_id}")
async def update_chat(request: Request, chat_id: str, body: ChatUpdate) -> dict[str, Any]:
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    values: dict[str, Any] = {}
    if body.title is not None:
        values["title"] = body.title.strip() or NEW_CHAT
    if "model" in body.model_fields_set:
        values["model"] = body.model
    if body.search is not None:
        values["search"] = body.search
    if body.settings is not None:
        if "folderGrants" in body.settings.model_fields_set:
            from .folders import visible

            grants = {
                g["id"] for g in await _state(request).store.folder_grants() if visible(g, person)
            }
            if any(i.startswith("node:") for i in body.settings.folderGrants):
                from .folder_io import FolderError

                remote = _state(request).tools.node_folders
                if remote is None:
                    raise _problem(503, "Machines' folders are unavailable in this Workbench.")
                try:
                    grants.update(g["id"] for g in await remote.listing(person))
                except FolderError as exc:
                    raise _problem(503, str(exc)) from None
            if any(i not in grants for i in body.settings.folderGrants):
                raise _problem(400, "A selected folder grant was removed or is unavailable to you.")
        if "toolServers" in body.settings.model_fields_set:
            from .tools import visible_server

            servers = {
                s["id"]
                for s in await _state(request).store.tool_servers()
                if visible_server(s, person)
            }
            if any(i.startswith("site:") for i in body.settings.toolServers):
                from .folder_io import FolderError

                remote = _state(request).tools.node_folders
                if remote is None:
                    raise _problem(503, "Machines' tools are unavailable in this Workbench.")
                try:
                    servers.update(s["id"] for s in await remote.local_servers(person))
                except FolderError as exc:
                    raise _problem(503, str(exc)) from None
            if any(i not in servers for i in body.settings.toolServers):
                raise _problem(
                    400,
                    "A selected tool server was removed or is unavailable to you. "
                    "Choose tools again.",
                )
        # Only the fields sent change; a `null` returns one to the model's own.
        merged = dict(chat.settings)
        merged.update(body.settings.model_dump(exclude_unset=True))
        values["settings"] = {k: v for k, v in merged.items() if v is not None}
    values["updated_at"] = time.time()
    await _state(request).store.update_chat(chat.id, **values)
    if "title" in values:
        _touch_exchanges(request, person.sub, chat.id)
    fresh = await _own_chat(request, person, chat_id)
    return chat_view(
        fresh,
        running=_answers(request).running(chat.id) is not None
        or _state(request).agui.busy(chat.id),
    )


@router.delete("/api/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(request: Request, chat_id: str) -> Response:
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    store: Store = _state(request).store
    await _state(request).agui.stop(chat.id)
    await _answers(request).stop(chat.id)
    records = await store.files_for_chat(chat.id)
    # Its exchanges leave the person's index first (MV2): if they cannot, nothing is
    # deleted, so no word of a deleted chat stays searchable.
    keeper = getattr(_state(request), "exchanges_index", None)
    if keeper is not None:
        try:
            await keeper.remove_chat(person.sub, chat.id)
        except Exception as exc:
            log.warning("a chat was not deleted: its index said %s: %s", type(exc).__name__, exc)
            raise _problem(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Workbench could not remove this chat's words from your search index, so "
                f"nothing was deleted ({type(exc).__name__}). Try again in a moment.",
            ) from None
    try:
        await store.delete_chat(chat.id)
    except BaseException:
        # The chat is still there with no exchanges in the index: put them back.
        _touch_exchanges(request, person.sub, chat.id)
        raise
    if keeper is not None:
        # A sync that had read the chat before it went may have put an exchange back
        # after the first removal (stopping its answer touches it): it goes now.
        try:
            await keeper.remove_chat(person.sub, chat.id)
        except Exception as exc:
            log.warning(
                "a deleted chat's exchanges were not all removed (%s: %s); the next start will",
                type(exc).__name__,
                exc,
            )
    await _state(request).reader.cancel([r.id for r in records])
    # The documents made from this chat's files go with it: they are in the person's own
    # index, another store, so a failure here is said in the log and the boot reconcile
    # removes what is left. It never keeps a chat from being deleted.
    try:
        await _state(request).saved.drop_chat(person.sub, chat.id)
    except Exception as exc:
        log.warning(
            "a deleted chat's documents were not all removed (%s: %s); the next start will",
            type(exc).__name__,
            exc,
        )
    root = _state(request).settings.data_dir

    def unlink() -> None:
        for record in records:
            files.remove_stored(root, record.owner, record.id)

    await asyncio.to_thread(unlink)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/people")
async def people(request: Request) -> dict[str, Any]:
    """The owner's read-only view of everyone's chats, when allowed (W4)."""
    person = await _person(request)
    store: Store = _state(request).store
    if not person.is_owner:
        raise _problem(status.HTTP_403_FORBIDDEN, "Only the owner of this install sees this.")
    if not await config.owner_reads_chats(store):
        raise _problem(
            status.HTTP_403_FORBIDDEN,
            "People's chats are their own on this Workbench. The owner can change that in "
            "Workbench's settings on Eugene's console.",
        )
    return {
        "people": [
            {"sub": p.sub, "name": p.name, "chats": n, "media": m}
            for p, n, m in await store.people()
            if p.sub != person.sub
        ]
    }


@router.get("/api/people/{sub}/chats")
async def person_chats(request: Request, sub: str) -> dict[str, Any]:
    await people(request)  # the same checks
    store: Store = _state(request).store
    answers = _answers(request)
    return {
        "chats": [
            chat_view(
                c,
                running=answers.running(c.id) is not None or _state(request).agui.busy(c.id),
                read_only=True,
            )
            for c in await store.chats(sub)
        ]
    }


# --------------------------------------------------------------------------- #
# asking
# --------------------------------------------------------------------------- #


def _title(content: str, records: list[FileRecord]) -> str:
    text = " ".join(content.split())
    if not text and records:
        text = records[0].name
    if len(text) > _TITLE_LENGTH:
        text = text[: _TITLE_LENGTH - 1].rstrip() + "…"
    return text or NEW_CHAT


async def _ask(
    request: Request,
    chat: Chat,
    *,
    parent_id: str | None,
    model: str,
    search: bool,
    records: list[FileRecord],
    version: bool = False,
    ask_anyway: bool = False,
) -> Message:
    """Start an answer to the person's message `parent_id`.

    The model is sent the path through it and nothing else. With `version`,
    the answer is another version on a branch of its own (Try again, or an
    edit), and every tab watching is told the path changed. With `ask_anyway`, a
    chat that answers only from the person's documents asks the model all the same
    (the "Ask the model anyway" of an answer that said it found nothing)."""
    store: Store = _state(request).store
    person = await _person(request)
    answer = await store.add_message(
        Message(
            id=_new_id(),
            chat_id=chat.id,
            seq=0,
            role="assistant",
            status="running",
            created_at=time.time(),
            search=search,
            model=model,
        ),
        parent_id=parent_id,
        agui_run_id=getattr(request.state, "agui_run", None),
    )
    history = (await store.messages(chat.id, via=answer.id))[:-1]
    root = _state(request).settings.data_dir
    fresh = await store.chat(chat.id) or chat
    # The person's notes, as this chat was first sent them (or as they are now,
    # at its first request that carries any). Never raises; None is none.
    from . import documents_api, memory_api, past_chats, saved_api

    notes = await memory_api.block_for(request, person, fresh, model)
    by_id = {r.id: r for r in records}
    # How each document goes is decided in the answer's own task, once its files
    # have been read (`documents_api.prepare`), and the request is built from it. Then
    # the person's saved documents are searched for what they just asked, and the
    # passages found go in their latest message only (`saved_api.retrieve`).
    decided: dict[str, Any] = {}

    async def prepare(progress: Progress) -> Preparation:
        prepared = await documents_api.prepare(
            request, fresh, history, by_id, model, notes, progress
        )
        decided["documents"] = prepared.sent
        # Past chats that bear on what the person just asked, when they turned them on for
        # this chat (MV2), in up to a third of the relevant budget; then their notes (MV1)
        # in the rest, in the room the chat leaves; then the documents' passages, in what
        # is left after them.
        past = await past_chats.turn_for(
            _state(request),
            person,
            fresh,
            history,
            model,
            used=prepared.used,
            reserve=saved_api._reserve(fresh),
        )
        turn = await memory_api.turn_for(
            request,
            person,
            fresh,
            history,
            model,
            used=prepared.used + past.tokens,
            reserve=saved_api._reserve(fresh),
            spent=past.tokens,
        )
        decided["memory_turn"] = turn.block
        # Call 12 (review, MV3b): a kept `search_memory` result may name a note a model kept
        # that the person has not seen; while one exists, none is replayed to an outside model.
        decided["memory_allowed"] = turn.allowed and not (
            turn.facts is not None
            and turn.facts.locality != "local"
            and await memory_api.unseen_withheld(_state(request).store, person.sub)
        )
        decided["past_turn"] = past.block
        decided["chats_allowed"] = past.allowed
        record = turn.record
        if past.record is not None:
            record = {**(record or {}), "pastChats": past.record}
        found = await saved_api.retrieve(
            request,
            person,
            fresh,
            history,
            model,
            used=prepared.used + past.tokens + turn.tokens,
            sent_whole=prepared.sent_whole,
            progress=progress,
            ask_anyway=ask_anyway,
        )
        decided["retrieved"] = found.block
        return Preparation(
            sources=[*prepared.sources, *found.sources],
            retrieval=found.retrieval,
            reply=found.reply,
            memory_used=record,
            memory_tool=turn.tool,
            chats_tool=past.tool,
            # MV3b: tools are assumed unless the gateway says the model refuses them.
            remember_tool=(turn.facts is None or turn.facts.tools)
            and await memory_write.remember_allowed(
                _state(request).store, person.sub, fresh.settings, model
            ),
        )

    def build() -> dict[str, Any]:
        return request_for(
            fresh,
            history,
            records,
            root=root,
            model=model,
            search=search,
            memory=notes,
            documents=decided.get("documents"),
            retrieved=decided.get("retrieved"),
            memory_turn=decided.get("memory_turn"),
            memory_allowed=bool(decided.get("memory_allowed", notes is not None)),
            past_turn=decided.get("past_turn"),
            chats_allowed=bool(decided.get("chats_allowed", False)),
        )

    answers = _answers(request)
    if version:
        # Before the answer's first piece, so a tab reloads into it.
        answers.publish(chat.id, {"type": "path"})
    answers.start(
        chat.id,
        answer,
        build,
        list(fresh.settings.get("toolServers") or []),
        list(fresh.settings.get("folderGrants") or []),
        person=person,
        prepare=prepare,
    )
    return answer


def _no_running(request: Request, chat: Chat) -> None:
    if _answers(request).running(chat.id) is not None or _state(request).agui.busy(
        chat.id, getattr(request.state, "agui_run", None)
    ):
        raise _problem(
            status.HTTP_409_CONFLICT,
            "An answer is still being written in this chat. Wait for it, or stop it.",
        )


async def _chat_files(request: Request, chat: Chat) -> list[FileRecord]:
    records: list[FileRecord] = await _state(request).store.files_for_chat(chat.id, uploads=True)
    return records


@router.post("/api/chats/{chat_id}/messages", status_code=status.HTTP_201_CREATED)
async def send(request: Request, chat_id: str, body: Send) -> dict[str, Any]:
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    _no_running(request, chat)
    model = body.model or chat.model
    if not model:
        raise _problem(status.HTTP_400_BAD_REQUEST, "Choose a model first.")
    records = await _chat_files(request, chat)
    by_id = {r.id: r for r in records}
    unknown = [i for i in body.attachments if i not in by_id]
    if unknown:
        raise _problem(status.HTTP_400_BAD_REQUEST, "An attachment is not in this chat.")
    if not body.content.strip() and not body.attachments:
        raise _problem(status.HTTP_400_BAD_REQUEST, "Write something, or attach a file.")
    search = chat.search if body.search is None else body.search
    store: Store = _state(request).store
    shown = await store.messages(chat.id)
    # Files are read when they are attached, and a message does not go before
    # they are: this waits, and refuses (409, nothing stored) what cannot go.
    from . import documents_api

    await documents_api.check_send(
        request, person, chat, shown, body.content, body.attachments, by_id, model
    )
    # Waiting for a file to be read can take a while; an answer may have started.
    _no_running(request, chat)
    try:
        user = await store.add_message(
            Message(
                id=_new_id(),
                chat_id=chat.id,
                seq=0,
                role="user",
                status="done",
                created_at=time.time(),
                content=body.content,
                attachments=list(body.attachments),
            ),
            # Sending continues from the end of the path shown.
            parent_id=shown[-1].id if shown else None,
            agui_run_id=getattr(request.state, "agui_run", None),
        )
    except AttachmentGone:
        # Removed in another tab while this send was being checked.
        raise _problem(status.HTTP_400_BAD_REQUEST, "An attachment is not in this chat.") from None
    values: dict[str, Any] = {"model": model, "search": search, "updated_at": time.time()}
    if chat.title == NEW_CHAT:
        values["title"] = _title(body.content, [by_id[i] for i in body.attachments])
    await store.update_chat(chat.id, **values)
    # "Remember that ..." keeps a note before the request is built, so the
    # first request of a chat already carries it; "forget ..." only finds
    # candidates. The message goes to the model either way.
    from . import memory_api

    command = await memory_api.handle_command(request, person, chat, user)
    # Learn from my chats (MR8): read the person's new words once the chat goes quiet.
    learner = getattr(_state(request), "learner", None)
    if learner is not None:
        learner.poke(person, chat.id)
    answer = await _ask(
        request, chat, parent_id=user.id, model=model, search=search, records=records
    )
    sent: dict[str, Any] = {"user": message_view(user), "answer": message_view(answer)}
    if command is not None:
        sent["memory"] = command
    return sent


class Retry(BaseModel):
    #: Ask the model even though this chat answers only from the person's documents
    #: and found nothing in them: "Ask the model anyway".
    askAnyway: bool = False


async def _try_again(
    request: Request, chat: Chat, message_id: str, ask_anyway: bool = False
) -> dict[str, Any]:
    """Another version of an answer, after the same message (V6). The old
    one and everything after it stay. An answer still being written is
    stopped first and kept as a version (V2). Its passages are looked for again, so
    an edited or removed document is reflected as it is now."""
    store: Store = _state(request).store
    target = await store.message(message_id)
    if target is None or target.chat_id != chat.id or target.role != "assistant":
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such answer to try again.")
    model = chat.model or target.model
    if not model:
        raise _problem(status.HTTP_400_BAD_REQUEST, "Choose a model first.")
    await _answers(request).stop(chat.id)
    await store.update_chat(chat.id, updated_at=time.time())
    answer = await _ask(
        request,
        chat,
        parent_id=target.parent_id,
        model=model,
        search=chat.search,
        records=await _chat_files(request, chat),
        version=True,
        ask_anyway=ask_anyway,
    )
    return {"answer": message_view(answer)}


@router.post("/api/chats/{chat_id}/retry", status_code=status.HTTP_201_CREATED)
async def retry(request: Request, chat_id: str, body: Retry | None = None) -> dict[str, Any]:
    """Try again on the last answer shown (W1). With `askAnyway`, a chat that answers only
    from the person's documents asks the model although nothing in them matched."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    messages = await _state(request).store.messages(chat.id)
    if not messages or messages[-1].role != "assistant":
        raise _problem(status.HTTP_409_CONFLICT, "There is no answer to try again.")
    return await _try_again(request, chat, messages[-1].id, bool(body and body.askAnyway))


@router.post(
    "/api/chats/{chat_id}/messages/{message_id}/retry", status_code=status.HTTP_201_CREATED
)
async def retry_message(
    request: Request, chat_id: str, message_id: str, body: Retry | None = None
) -> dict[str, Any]:
    """Try again on any answer (V6)."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    return await _try_again(request, chat, message_id, bool(body and body.askAnyway))


@router.post("/api/chats/{chat_id}/messages/{message_id}/edit", status_code=status.HTTP_201_CREATED)
async def edit(request: Request, chat_id: str, message_id: str, body: Edit) -> dict[str, Any]:
    """Edit a message (V3): the edit is a new version of it, with the same
    attachments, and the old message and its whole branch stay."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    _no_running(request, chat)
    store: Store = _state(request).store
    message = await store.message(message_id)
    if message is None or message.chat_id != chat.id or message.role != "user":
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such message to edit.")
    if not chat.model:
        raise _problem(status.HTTP_400_BAD_REQUEST, "Choose a model first.")
    edited = await store.add_message(
        Message(
            id=_new_id(),
            chat_id=chat.id,
            seq=0,
            role="user",
            status="done",
            created_at=time.time(),
            content=body.content,
            attachments=list(message.attachments),
        ),
        parent_id=message.parent_id,
    )
    await store.update_chat(chat.id, updated_at=time.time())
    answer = await _ask(
        request,
        chat,
        parent_id=edited.id,
        model=chat.model,
        search=chat.search,
        records=await _chat_files(request, chat),
        version=True,
    )
    return {"user": message_view(edited), "answer": message_view(answer)}


@router.post("/api/chats/{chat_id}/messages/{message_id}/choose")
async def choose(request: Request, chat_id: str, message_id: str) -> dict[str, Any]:
    """Show another version of a message, and the branch it remembers.

    Refused while an answer runs, so the running answer, its Stop and any
    call waiting for approval stay on the path shown (§1)."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    _no_running(request, chat)
    answers = _answers(request)
    if not await _state(request).store.choose(chat.id, message_id):
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such message in this chat.")
    answers.publish(chat.id, {"type": "path"})
    _touch_exchanges(request, person.sub, chat.id)
    return await _chat_detail(request, chat, read_only=False)


@router.post("/api/chats/{chat_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def stop(request: Request, chat_id: str) -> Response:
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    await _state(request).agui.stop(chat.id)
    await _state(request).interactions.stop(chat.id)
    await _answers(request).stop(chat.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def _settle(queue: asyncio.Queue[Any], quiet: float = 1.0) -> None:
    """Drain events until none has arrived for `quiet` seconds."""
    while True:
        try:
            await asyncio.wait_for(queue.get(), quiet)
        except TimeoutError:
            return


@router.get("/api/chats/{chat_id}/events")
async def events(request: Request, chat_id: str) -> StreamingResponse:
    """Every tab watching a chat gets its answers as they arrive (W1).

    The session is checked again at every keepalive, so a person turned
    off in Eugene stops receiving within one refresh even on a tab left
    open.
    """
    person = await _person(request)
    chat, read_only = await _readable_chat(request, person, chat_id)
    answers = _answers(request)
    sessions: Sessions = _state(request).sessions

    async def stream() -> AsyncIterator[str]:
        watch, snapshot = answers.watch(chat.id)
        try:
            yield ": watching\n\n"
            if snapshot is not None and not read_only:
                yield _sse(snapshot)
            while True:
                try:
                    event = await asyncio.wait_for(watch.queue.get(), _KEEPALIVE_SECONDS)
                    if read_only:
                        # The owner reading someone's chat is told only that it
                        # changed, so the page re-reads it through the
                        # redaction: a live answer's content never reaches them
                        # before production mode could hide it (J13a).
                        await _settle(watch.queue)
                        yield _sse({"type": "reload"})
                        continue
                except TimeoutError:
                    try:
                        await sessions.person(request)
                    except SignedOut as exc:
                        yield _sse({"type": "signed-out", **exc.detail})  # type: ignore[dict-item]
                        return
                    except HTTPException:
                        pass
                    yield ": keepalive\n\n"
                    continue
                yield _sse(event)
                if event.get("type") == "reload":
                    return
        finally:
            watch.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------- #
# attachments
# --------------------------------------------------------------------------- #


UPLOAD_CHUNK = 1024 * 1024


def _open_for_write(target: Path) -> BinaryIO:
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.open("wb")


def _discard(target: Path) -> None:
    """Take a half-written upload off the disk: a refusal or a lost connection."""
    target.unlink(missing_ok=True)


async def _save_upload(file: UploadFile, head: bytes, target: Path, limit: int) -> int | None:
    """Write an upload to `target` a megabyte at a time, so a large document is
    never held in memory. The bytes written, or None when it is larger than `limit`
    (what was written is left for the caller to remove)."""
    handle = await asyncio.to_thread(_open_for_write, target)
    written = 0
    try:
        chunk = head
        while chunk:
            written += len(chunk)
            if written > limit:
                return None
            await asyncio.to_thread(handle.write, chunk)
            chunk = await file.read(UPLOAD_CHUNK)
    finally:
        await asyncio.to_thread(handle.close)
    return written


def _too_large(found: files.Detected, limit: int, reads: bool) -> str:
    """Why a file is refused for its size, in the limit that applies to it."""
    if found.kind in ("image", "audio"):
        return (
            f"That file is larger than Eugene carries for a {found.kind} "
            f"({files.describe_limit(found.kind)})."
        )
    if reads:
        return (
            f"That file is larger than the {files.describe_size(limit)} Workbench reads "
            "of one file."
        )
    return f"That file is larger than Eugene carries for a file ({files.describe_limit('file')})."


@router.post("/api/chats/{chat_id}/files", status_code=status.HTTP_201_CREATED)
async def upload(
    request: Request, chat_id: str, file: UploadFile = File(...), model: str | None = None
) -> dict[str, Any]:
    """Attach a file to the chat. Its type is what its bytes say (`files.detect`),
    and what Workbench neither reads nor sends is refused with a 415 that names
    what it is and what is taken. An image or audio clip is kept as it is; a
    document is read in the background (`knowledge/reader.py`), and the answer says
    so: `reading.state` is `reading` until it ends, and `decision` is what a request
    to `model` (the chat's own when not said) would do with it."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    store: Store = _state(request).store
    head = await file.read(files.HEAD_BYTES)
    try:
        found = files.detect(head, file.content_type, file.filename or "")
    except files.Unsupported as exc:
        raise _problem(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, exc.message) from None
    settings = await config.document_settings(store)
    # With reading off, Workbench is as it was before documents: a PDF is carried
    # as a file to a model that takes one, and nothing else is read.
    reads = found.is_document and settings.enabled
    legacy_pdf = found.is_document and not settings.enabled and found.media_type == files.PDF
    if found.is_document and not reads and not legacy_pdf:
        raise _problem(
            status.HTTP_403_FORBIDDEN,
            "The owner of this Workbench has turned off reading files. It still sends "
            "images and audio, and PDFs to models that take them as files.",
        )
    limit = files.upload_limit(found, settings.max_bytes if reads else files.LIMITS["file"])
    root = _state(request).settings.data_dir
    record_id = _new_id()
    target = files.path_of(root, person.sub, record_id)
    try:
        size = await _save_upload(file, head, target, limit)
    except BaseException:
        _discard(target)  # a connection that was lost part-way leaves nothing
        raise
    if size is None:
        await asyncio.to_thread(_discard, target)
        raise _problem(status.HTTP_413_CONTENT_TOO_LARGE, _too_large(found, limit, reads))
    if found.zipped:
        holds = await asyncio.to_thread(files.zip_holds_document, target)
        if holds is False:
            await asyncio.to_thread(_discard, target)
            raise _problem(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, files.not_word(file.filename or "").message
            )

    def counts(media_type: str) -> bool:
        return files.travels(media_type) or (legacy_pdf and media_type == files.PDF)

    existing = await store.files_for_chat(chat.id, uploads=True)
    if counts(found.media_type) and (
        sum(r.size for r in existing if counts(r.media_type)) + size > files.CHAT_LIMIT
    ):
        await asyncio.to_thread(_discard, target)
        raise _problem(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "This chat's attachments would add up to more than one request can carry "
            f"({files.CHAT_LIMIT // files.MIB} MiB). Remove a file not yet sent, or start a "
            "new chat.",
        )
    from . import documents_api
    from .context import new_nonce

    record = FileRecord(
        id=record_id,
        owner=person.sub,
        chat_id=chat.id,
        name=(file.filename or "attachment")[:255],
        media_type=found.media_type,
        size=size,
        created_at=time.time(),
        reading=(
            {
                "state": "reading",
                "kind": found.kind,
                "language": found.language,
                "nonce": new_nonce(),
                "choice": None,
                "startedAt": time.time(),
            }
            if reads
            else None
        ),
    )
    await store.add_file(record)
    if reads:
        await _state(request).reader.start(record)
    out: dict[str, Any] = {
        "id": record.id,
        "name": record.name,
        "mediaType": record.media_type,
        "size": record.size,
        "kind": documents_api.kind_of(record),
        "reading": reading_view(record),
        "decision": None,
    }
    if reads:
        try:
            view = await documents_api.files_view(request, chat, model or chat.model or None)
            out["decision"] = next(i for i in view["files"] if i["id"] == record.id)["decision"]
        except Exception as exc:  # the file is kept; the page asks again for its decision
            log.warning("could not decide a new upload: %s: %s", type(exc).__name__, exc)
    return out


@router.delete("/api/chats/{chat_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_upload(request: Request, chat_id: str, file_id: str) -> Response:
    """The composer's Remove: an upload not yet sent is deleted, row and
    bytes, so it no longer counts against the chat's limit (workbench#3).
    A sent file stays with its message."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    try:
        record = await _state(request).store.delete_unsent_file(chat.id, file_id)
    except FileInUse:
        raise _problem(
            status.HTTP_409_CONFLICT,
            "This file was sent with a message, so it stays with the chat. "
            "Deleting the chat deletes it.",
        ) from None
    if record is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such file in this chat.")
    await _state(request).reader.cancel([record.id])
    try:
        await _state(request).saved.drop_attachment(person.sub, chat.id, record.id)
    except Exception as exc:
        log.warning(
            "the document made from a removed file was not removed (%s: %s); the next start will",
            type(exc).__name__,
            exc,
        )
    await asyncio.to_thread(
        files.remove_stored, _state(request).settings.data_dir, record.owner, record.id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _hidden_file(request: Request, chat: Chat, record: FileRecord) -> bool:
    """Whether the message a file belongs to is hidden from the install owner
    (J13a): a tool's result made for it, or an upload it carries. The file is
    hidden as its message is, through its address as on the page."""
    tree = await _state(request).store.tree(chat.id)
    hidden = hidden_from_owner(tree, (await install_mode(request))["mode"])
    if record.message_id is not None:
        return record.message_id in hidden
    return any(m.id in hidden for m in tree if record.id in m.attachments)


async def _may_read_file(request: Request, person: Person, record: FileRecord) -> None:
    """A file is readable by whoever may read what it belongs to: its chat,
    or its media row (workbench-media-screens.md §2.7). The file's own
    owner is checked too, so an id is never the only thing guarding it."""
    store: Store = _state(request).store
    if record.chat_id is not None:
        try:
            chat, read_only = await _readable_chat(request, person, record.chat_id)
        except HTTPException:
            # Not "no such chat": the same words as for a file that is not there.
            raise _problem(status.HTTP_404_NOT_FOUND, "There is no such file.") from None
        if chat.owner == record.owner and not (
            read_only and await _hidden_file(request, chat, record)
        ):
            return
    elif record.media_id is not None:
        row = await store.media(record.media_id)
        if row is not None and row.owner == record.owner:
            if row.owner == person.sub:
                return
            if person.is_owner and await config.owner_reads_chats(store):
                return
    raise _problem(status.HTTP_404_NOT_FOUND, "There is no such file.")


@router.get("/api/files/{file_id}")
async def download(request: Request, file_id: str) -> Response:
    """The file, streamed from disk, with `Range` answered so audio and video
    can seek (§2.8)."""
    person = await _person(request)
    store: Store = _state(request).store
    record = await store.file(file_id)
    if record is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such file.")
    await _may_read_file(request, person, record)
    path = files.path_of(_state(request).settings.data_dir, record.owner, record.id)
    if not await asyncio.to_thread(path.is_file):
        raise _problem(status.HTTP_404_NOT_FOUND, "That file is no longer on disk.")
    return FileResponse(
        path,
        media_type=record.media_type,
        headers={
            "Content-Disposition": "attachment",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, no-store",
        },
    )
