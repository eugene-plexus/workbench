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
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import config, files
from .answers import Answers, message_view
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
from .store import Chat, FileRecord, Message, Person, Store

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
    created = await sessions.create(identity.sub, identity.refresh_token, identity.expires_in)
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


@router.get("/api/me")
async def me(request: Request) -> dict[str, Any]:
    person = await _person(request)
    reads = await config.owner_reads_chats(_state(request).store)
    mode = await install_mode(request)
    seen = await _state(request).store.setting(_mode_seen_key(person.sub))
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
        "consoleUrl": (
            str(_state(request).settings.oidc_issuer).removesuffix("/oidc")
            if person.is_owner and _state(request).settings.oidc_issuer
            else None
        ),
        # The standing line (W4) is for everyone the owner can read.
        "ownerReadsChats": reads,
    }


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
                # A gateway older than C3 does not say; offering search there
                # is the old behaviour, and its refusal still names the reason.
                "webSearch": info.get("web_search", True) is not False,
                "ready": (info.get("ready_backends") or 0) > 0,
                "onDemand": bool(info.get("on_demand")),
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
            for k in ("instructions", "toolServers", "folderGrants", "repetitionMode", *SAMPLING)
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
    return {"chats": [chat_view(c, running=answers.running(c.id) is not None) for c in chats]}


@router.post("/api/chats", status_code=status.HTTP_201_CREATED)
async def create_chat(request: Request, body: ChatCreate) -> dict[str, Any]:
    person = await _person(request)
    now = time.time()
    chat = Chat(
        id=_new_id(),
        owner=person.sub,
        title=NEW_CHAT,
        model=body.model,
        created_at=now,
        updated_at=now,
    )
    await _state(request).store.create_chat(chat)
    return chat_view(chat, running=False)


@router.get("/api/chats/{chat_id}")
async def get_chat(request: Request, chat_id: str) -> dict[str, Any]:
    person = await _person(request)
    chat, read_only = await _readable_chat(request, person, chat_id)
    store: Store = _state(request).store
    messages = await store.messages(chat.id)
    attached = {f.id: f for f in await store.files_for_chat(chat.id)}
    running = _answers(request).running(chat.id)
    views = []
    for m in messages:
        view = message_view(running.message if running and running.message.id == m.id else m)
        view["files"] = [
            {"id": i, "name": attached[i].name, "mediaType": attached[i].media_type}
            for i in m.attachments
            if i in attached
        ]
        views.append(view)
    owner = await store.person(chat.owner) if read_only else None
    if read_only:
        views = redact_for_owner(views, (await install_mode(request))["mode"])
    return {
        "chat": chat_view(chat, running=running is not None, read_only=read_only),
        "messages": views,
        "ownerName": owner.name if owner else None,
    }


def _hidden_call(call: dict[str, Any], mode: str) -> bool:
    """A job-site call the owner may not read: everything unless it was made
    in dev mode and the install is still in dev mode (J13, J18)."""
    return bool(call.get("jobSite")) and not (mode == "dev" and call.get("mode") == "dev")


def redact_for_owner(views: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """Production mode hides a chat from its first job-site result on (J13a).

    The model's later replies quote what it read, so the result alone is not
    enough; the owner sees the chat up to that point, then one line saying
    which machine's files the rest used."""
    out: list[dict[str, Any]] = []
    site: str | None = None
    for view in views:
        if site is None:
            for round_ in view.get("toolRounds") or []:
                for call in round_.get("calls") or []:
                    if _hidden_call(call, mode):
                        site = str(call.get("label") or call.get("site") or "a job site")
                        break
                if site is not None:
                    break
        if site is None:
            out.append(view)
            continue
        out.append(
            {
                "content": "",
                "reasoning": "",
                "sources": [],
                "searches": 0,
                "search": False,
                "id": view["id"],
                "seq": view["seq"],
                "role": view["role"],
                "status": view["status"],
                "createdAt": view["createdAt"],
                "finishedAt": view["finishedAt"],
                "attachments": [],
                "files": [],
                "toolRounds": [],
                "error": None,
                "model": view.get("model"),
                "finish": view.get("finish"),
                "answerFrom": None,
                "reasoningFrom": None,
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
    fresh = await _own_chat(request, person, chat_id)
    return chat_view(fresh, running=_answers(request).running(chat.id) is not None)


@router.delete("/api/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(request: Request, chat_id: str) -> Response:
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    store: Store = _state(request).store
    await _answers(request).stop(chat.id)
    records = await store.files_for_chat(chat.id)
    await store.delete_chat(chat.id)
    root = _state(request).settings.data_dir

    def unlink() -> None:
        for record in records:
            files.path_of(root, record.owner, record.id).unlink(missing_ok=True)

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
            {"sub": p.sub, "name": p.name, "chats": n}
            for p, n in await store.people()
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
            chat_view(c, running=answers.running(c.id) is not None, read_only=True)
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
    request: Request, chat: Chat, *, model: str, search: bool, records: list[FileRecord]
) -> Message:
    """Start the next answer in `chat`, whose last message is the person's."""
    store: Store = _state(request).store
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
        )
    )
    history = [m for m in await store.messages(chat.id) if m.id != answer.id]
    root = _state(request).settings.data_dir
    fresh = await store.chat(chat.id) or chat

    def build() -> dict[str, Any]:
        return request_for(fresh, history, records, root=root, model=model, search=search)

    _answers(request).start(
        chat.id,
        answer,
        build,
        list(fresh.settings.get("toolServers") or []),
        list(fresh.settings.get("folderGrants") or []),
        person=await _person(request),
    )
    return answer


def _no_running(request: Request, chat: Chat) -> None:
    if _answers(request).running(chat.id) is not None:
        raise _problem(
            status.HTTP_409_CONFLICT,
            "An answer is still being written in this chat. Wait for it, or stop it.",
        )


async def _chat_files(request: Request, chat: Chat) -> list[FileRecord]:
    records: list[FileRecord] = await _state(request).store.files_for_chat(chat.id)
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
        )
    )
    values: dict[str, Any] = {"model": model, "search": search, "updated_at": time.time()}
    if chat.title == NEW_CHAT:
        values["title"] = _title(body.content, [by_id[i] for i in body.attachments])
    await store.update_chat(chat.id, **values)
    answer = await _ask(request, chat, model=model, search=search, records=records)
    return {"user": message_view(user), "answer": message_view(answer)}


@router.post("/api/chats/{chat_id}/retry", status_code=status.HTTP_201_CREATED)
async def retry(request: Request, chat_id: str) -> dict[str, Any]:
    """Try again: the last answer is replaced (W1)."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    _no_running(request, chat)
    store: Store = _state(request).store
    messages = await store.messages(chat.id)
    if not messages or messages[-1].role != "assistant":
        raise _problem(status.HTTP_409_CONFLICT, "There is no answer to try again.")
    last = messages[-1]
    await store.delete_messages_from(chat.id, last.seq)
    model = chat.model or last.model
    if not model:
        raise _problem(status.HTTP_400_BAD_REQUEST, "Choose a model first.")
    await store.update_chat(chat.id, updated_at=time.time())
    answer = await _ask(
        request, chat, model=model, search=chat.search, records=await _chat_files(request, chat)
    )
    return {"answer": message_view(answer)}


@router.post("/api/chats/{chat_id}/messages/{message_id}/edit", status_code=status.HTTP_201_CREATED)
async def edit(request: Request, chat_id: str, message_id: str, body: Edit) -> dict[str, Any]:
    """Edit a message: it and everything after it are replaced (W1)."""
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    _no_running(request, chat)
    store: Store = _state(request).store
    message = await store.message(message_id)
    if message is None or message.chat_id != chat.id or message.role != "user":
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such message to edit.")
    if not chat.model:
        raise _problem(status.HTTP_400_BAD_REQUEST, "Choose a model first.")
    await store.delete_messages_from(chat.id, message.seq + 1)
    await store.update_message(message.id, content=body.content)
    await store.update_chat(chat.id, updated_at=time.time())
    answer = await _ask(
        request,
        chat,
        model=chat.model,
        search=chat.search,
        records=await _chat_files(request, chat),
    )
    edited = await store.message(message.id)
    assert edited is not None
    return {"user": message_view(edited), "answer": message_view(answer)}


@router.post("/api/chats/{chat_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def stop(request: Request, chat_id: str) -> Response:
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
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


@router.post("/api/chats/{chat_id}/files", status_code=status.HTTP_201_CREATED)
async def upload(request: Request, chat_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    person = await _person(request)
    chat = await _own_chat(request, person, chat_id)
    head = await file.read(16)
    media_type = files.media_type(file.content_type, head)
    if media_type is None:
        raise _problem(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Workbench can send images (PNG or JPEG), PDFs and audio (WAV or MP3). "
            "Other kinds of file are not carried to a model.",
        )
    kind = files.KINDS[media_type]
    limit = files.LIMITS[kind]
    data = head + await file.read(limit + 1 - len(head))
    if len(data) > limit:
        raise _problem(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"That file is larger than Eugene carries for a {kind} ({files.describe_limit(kind)}).",
        )
    store: Store = _state(request).store
    existing = await store.files_for_chat(chat.id)
    if sum(r.size for r in existing) + len(data) > files.CHAT_LIMIT:
        raise _problem(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "This chat's attachments would add up to more than Eugene carries in one request "
            f"({files.CHAT_LIMIT // files.MIB} MiB), because the whole chat is sent each time. "
            "Start a new chat to attach more.",
        )
    record = FileRecord(
        id=_new_id(),
        owner=person.sub,
        chat_id=chat.id,
        name=(file.filename or "attachment")[:255],
        media_type=media_type,
        size=len(data),
        created_at=time.time(),
    )
    path = files.path_of(_state(request).settings.data_dir, person.sub, record.id)

    def write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    await asyncio.to_thread(write)
    await store.add_file(record)
    return {"id": record.id, "name": record.name, "mediaType": media_type, "size": record.size}


@router.get("/api/files/{file_id}")
async def download(request: Request, file_id: str) -> Response:
    person = await _person(request)
    store: Store = _state(request).store
    record = await store.file(file_id)
    if record is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such file.")
    await _readable_chat(request, person, record.chat_id)
    path = files.path_of(_state(request).settings.data_dir, record.owner, record.id)
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except OSError as exc:
        raise _problem(status.HTTP_404_NOT_FOUND, "That file is no longer on disk.") from exc
    return Response(
        content=data,
        media_type=record.media_type,
        headers={
            "Content-Disposition": "attachment",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, no-store",
        },
    )
