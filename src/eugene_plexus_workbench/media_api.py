"""The media area's API (workbench-media-screens.md §8): images, speech,
transcription, and video as a long job (slice 3).

Every route needs a session, and every result is filtered by who is asking:
**a result that is not yours is the same 404 as one that does not exist**,
except to the owner, read-only, when `ownerReadsChats` is on (M3).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import api, config, files
from .hub import Hub, HubError
from .media import MediaJobs, media_view, new_id
from .sessions import Sessions, SignedOut
from .store import Chat, FileRecord, MediaRow, Person, Store

router = APIRouter()

#: The media screens, one tab each.
DOORS = ("images", "speech", "transcription", "video")
_PAGE = 30

#: A provider's own name for the line *Runs on <account>* (M6).
_PROVIDERS = {"openrouter": "OpenRouter", "openai": "OpenAI", "elevenlabs": "ElevenLabs"}


def _store(request: Request) -> Store:
    store: Store = request.app.state.store
    return store


def _jobs(request: Request) -> MediaJobs:
    jobs: MediaJobs = request.app.state.media
    return jobs


def _door(door: str) -> str:
    if door not in DOORS:
        raise api._problem(status.HTTP_404_NOT_FOUND, "There is no such media screen.")
    return door


# --------------------------------------------------------------------------- #
# what each screen may offer
# --------------------------------------------------------------------------- #


def _common(model: dict[str, Any]) -> dict[str, Any]:
    """What every screen's picker says of a model: who serves it, where."""
    info = model.get("x_eugene_plexus") or {}
    drivers = info.get("drivers") or []
    provider = str(model.get("owned_by") or "")
    return {
        "id": model.get("id"),
        "account": drivers[0] if len(drivers) == 1 else None,
        "provider": _PROVIDERS.get(provider, provider or None),
        "locality": info.get("locality") or "unknown",
        "ready": (info.get("ready_backends") or 0) > 0,
        "onDemand": bool(info.get("on_demand")),
    }


def speech_model(model: dict[str, Any]) -> dict[str, Any]:
    """A speech model as the Speech screen offers it (slice 2). Voices null
    is the provider's to check (a free-text box); `pcm` is left out, since a
    browser cannot play raw samples."""
    info = model.get("x_eugene_plexus") or {}
    named = info.get("voice_names")
    return {
        **_common(model),
        "voices": info.get("voices"),
        # A voice's display name by its id, where the provider names it
        # (ElevenLabs); the id is still what is sent.
        "voiceNames": {
            k: v for k, v in named.items() if isinstance(k, str) and isinstance(v, str) and v
        }
        if isinstance(named, dict)
        else {},
        "formats": [f for f in info.get("speech_formats") or ["mp3"] if f != "pcm"],
    }


def transcription_model(model: dict[str, Any]) -> dict[str, Any]:
    """A model the Transcription screen sends audio to, and whether it can
    translate into English too (only OpenAI's whisper, measured)."""
    surfaces = (model.get("x_eugene_plexus") or {}).get("surfaces") or []
    return {**_common(model), "translates": "translation" in surfaces}


def image_model(model: dict[str, Any]) -> dict[str, Any]:
    """An image model as the Images screen builds its form from it (M5).

    A setting the gateway does not list (null, or a gateway from before the
    media screens) is the backend's to check: the page gives it a free-text
    box rather than a list it would have to invent."""
    info = model.get("x_eugene_plexus") or {}
    return {
        **_common(model),
        "maxImages": info.get("image_max_images"),
        "qualities": info.get("image_qualities"),
        "backgrounds": info.get("image_backgrounds"),
        "outputFormats": info.get("image_output_formats"),
        "minReferences": info.get("image_min_references") or 0,
        "maxReferences": info.get("image_max_references"),
        "edits": bool(info.get("image_edits")),
    }


def _price_line(line: Any) -> dict[str, Any] | None:
    """One line of a video model's price list, as the page reads it."""
    usd = line.get("usd") if isinstance(line, dict) else None
    if not isinstance(usd, int | float) or isinstance(usd, bool):
        return None
    out: dict[str, Any] = {"sku": line.get("sku"), "per": line.get("per"), "usd": usd}
    for key, name in (
        ("resolution", "resolution"),
        ("sizes", "sizes"),
        ("audio", "audio"),
        ("first_frame", "firstFrame"),
    ):
        if line.get(key) is not None:
            out[name] = line[key]
    return out


def video_model(model: dict[str, Any]) -> dict[str, Any]:
    """A video model as the Video screen builds its form and its price from
    it (§5, §6.4). No price listed is null: the page says so, never free."""
    info = model.get("x_eugene_plexus") or {}
    prices = info.get("video_prices")
    lines = [p for p in (_price_line(x) for x in prices) if p] if isinstance(prices, list) else []
    return {
        **_common(model),
        "durations": info.get("video_durations"),
        "sizes": info.get("video_sizes"),
        "firstFrame": bool(info.get("video_first_frame")),
        "prices": lines or None,
    }


@router.get("/api/media/doors")
async def doors(request: Request) -> dict[str, Any]:
    """Each screen this Workbench has, with the models that serve it."""
    await api._person(request)
    hub: Hub = request.app.state.hub
    try:
        listing = await hub.models()
    except HubError as exc:
        raise api._problem(exc.status if exc.status < 500 else 502, exc.message) from exc
    data = listing.get("data") or []

    def serving(surface: str) -> list[dict[str, Any]]:
        return [
            m for m in data if surface in ((m.get("x_eugene_plexus") or {}).get("surfaces") or [])
        ]

    return {
        "doors": {
            "images": {"models": [image_model(m) for m in serving("image")]},
            "speech": {"models": [speech_model(m) for m in serving("speech")]},
            "transcription": {"models": [transcription_model(m) for m in serving("transcription")]},
            "video": {"models": [video_model(m) for m in serving("video")]},
        }
    }


# --------------------------------------------------------------------------- #
# a person's bins
# --------------------------------------------------------------------------- #


async def _views(store: Store, rows: list[MediaRow]) -> list[dict[str, Any]]:
    records = await store.files_for_media([r.id for r in rows])
    return [media_view(r, records) for r in rows]


async def _readable(request: Request, person: Person, media_id: str) -> tuple[MediaRow, bool]:
    """A result the asker may read, and whether only to read it (M3)."""
    store = _store(request)
    row = await store.media(media_id)
    if row is not None and row.owner == person.sub:
        return row, False
    if row is not None and person.is_owner and await config.owner_reads_chats(store):
        return row, True
    raise api._problem(status.HTTP_404_NOT_FOUND, "There is no such result.")


async def _own(request: Request, person: Person, media_id: str) -> MediaRow:
    row, read_only = await _readable(request, person, media_id)
    if read_only:
        raise api._problem(status.HTTP_404_NOT_FOUND, "There is no such result.")
    return row


@router.get("/api/media")
async def list_media(
    request: Request, door: str, before: float | None = None, limit: int = _PAGE
) -> dict[str, Any]:
    person = await api._person(request)
    store = _store(request)
    rows = await store.media_list(person.sub, _door(door), before=before, limit=min(limit, 100))
    return {"items": await _views(store, rows), "bytes": await store.media_bytes(person.sub)}


@router.get("/api/media/events")
async def media_events(request: Request) -> StreamingResponse:
    """Every tab a person has open gets their results as they change (§2.2).
    The session is checked again at every keepalive, as a chat's stream is."""
    person = await api._person(request)
    jobs = _jobs(request)
    sessions: Sessions = request.app.state.sessions

    async def stream() -> AsyncIterator[str]:
        watch = jobs.watch(person.sub)
        try:
            yield ": watching\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(watch.queue.get(), api._KEEPALIVE_SECONDS)
                except TimeoutError:
                    try:
                        await sessions.person(request)
                    except SignedOut as exc:
                        yield api._sse({"type": "signed-out", **exc.detail})  # type: ignore[dict-item]
                        return
                    except HTTPException:
                        pass
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "reload":
                    return
        finally:
            watch.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/api/media/{media_id}")
async def get_media(request: Request, media_id: str) -> dict[str, Any]:
    person = await api._person(request)
    row, read_only = await _readable(request, person, media_id)
    return {**(await _views(_store(request), [row]))[0], "readOnly": read_only}


async def _delete(request: Request, rows: list[str]) -> None:
    jobs = _jobs(request)
    for media_id in rows:
        await jobs.stop(media_id)
    records = await _store(request).delete_media(rows)
    root = request.app.state.settings.data_dir

    def unlink() -> None:
        for record in records:
            files.path_of(root, record.owner, record.id).unlink(missing_ok=True)

    await asyncio.to_thread(unlink)


@router.delete("/api/media/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(request: Request, media_id: str) -> Response:
    """Deletes the result and its files (M8). A running one is stopped first."""
    person = await api._person(request)
    row = await _own(request, person, media_id)
    await _delete(request, [row.id])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/media", status_code=status.HTTP_204_NO_CONTENT)
async def empty_bin(request: Request, door: str) -> Response:
    """Deletes every result on one screen (M8)."""
    person = await api._person(request)
    await _delete(request, await _store(request).media_ids(person.sub, _door(door)))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/media/{media_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def stop_media(request: Request, media_id: str) -> Response:
    person = await api._person(request)
    row = await _own(request, person, media_id)
    await _jobs(request).stop(row.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# making images
# --------------------------------------------------------------------------- #


class ImageAsk(BaseModel):
    model: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=1, max_length=32_000)
    size: str | None = Field(default=None, pattern=r"^\d{1,5}x\d{1,5}$")
    n: int | None = Field(default=None, ge=1, le=10)
    quality: str | None = Field(default=None, min_length=1, max_length=32)
    background: str | None = Field(default=None, min_length=1, max_length=32)
    outputFormat: str | None = Field(default=None, min_length=1, max_length=16)
    references: list[str] = Field(default_factory=list, max_length=16)


async def _references(
    request: Request, person: Person, ids: list[str], *, what: str = "reference image"
) -> list[FileRecord]:
    """Reference images: images in the asker's own bins, under the gateway's
    limit together, said before anything is sent."""
    store = _store(request)
    found = []
    for file_id in dict.fromkeys(ids):
        record = await store.file(file_id)
        if (
            record is None
            or record.owner != person.sub
            or record.media_id is None
            or record.media_type not in files.IMAGE_TYPES
        ):
            raise api._problem(
                status.HTTP_400_BAD_REQUEST,
                f"The {what} is no longer in your bins. Choose it again.",
            )
        found.append(record)
    if sum(r.size for r in found) > files.REFERENCES_LIMIT:
        raise api._problem(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"The reference images add up to more than Eugene carries in one request "
            f"({files.REFERENCES_LIMIT // files.MIB} MiB). Use fewer or smaller images.",
        )
    return found


@router.post("/api/media/images", status_code=status.HTTP_201_CREATED)
async def make_images(request: Request, body: ImageAsk) -> dict[str, Any]:
    """Starts an image request on the server (M2) and returns its row."""
    person = await api._person(request)
    references = await _references(request, person, body.references)
    gateway: dict[str, Any] = {"model": body.model, "prompt": body.prompt}
    for key, value in (
        ("size", body.size),
        ("n", body.n),
        ("quality", body.quality),
        ("background", body.background),
        ("output_format", body.outputFormat),
    ):
        if value is not None:
            gateway[key] = value
    row = MediaRow(
        id=new_id(),
        owner=person.sub,
        door="images",
        kind="made",
        status="running",
        created_at=time.time(),
        model=body.model,
        request=body.model_dump(exclude_none=True),
    )
    await _jobs(request).start_images(row, gateway, references)
    return (await _views(_store(request), [row]))[0]


@router.post("/api/media/images/upload", status_code=status.HTTP_201_CREATED)
async def bring_in(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    """An image brought into the bin, to use as a reference (M9)."""
    person = await api._person(request)
    data = await file.read(files.UPLOAD_LIMIT + 1)
    if len(data) > files.UPLOAD_LIMIT:
        raise api._problem(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"That image is larger than a bin takes ({files.UPLOAD_LIMIT // files.MIB} MiB).",
        )
    media_type = files.sniff_image(data)
    if media_type is None:
        raise api._problem(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Bins take PNG, JPEG and WebP images. This file is none of them.",
        )
    size = files.image_size(data)
    now = time.time()
    name = (file.filename or f"image.{files.extension(media_type)}")[:255]
    row = MediaRow(
        id=new_id(),
        owner=person.sub,
        door="images",
        kind="upload",
        status="done",
        created_at=now,
        finished_at=now,
        request={"name": name},
    )
    record = FileRecord(
        id=new_id(),
        owner=person.sub,
        chat_id=None,
        media_id=row.id,
        name=name,
        media_type=media_type,
        size=len(data),
        created_at=now,
        width=size[0] if size else None,
        height=size[1] if size else None,
    )
    path = files.path_of(request.app.state.settings.data_dir, person.sub, record.id)

    def write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    await asyncio.to_thread(write)
    store = _store(request)
    await store.add_media(row)
    await store.add_file(record)
    view = (await _views(store, [row]))[0]
    _jobs(request).publish(person.sub, {"type": "media", "item": view})
    return view


# --------------------------------------------------------------------------- #
# speech and transcription (slice 2)
# --------------------------------------------------------------------------- #


class SpeechAsk(BaseModel):
    model: str = Field(min_length=1, max_length=256)
    #: The gateway's own limit on this door (`SpeechRequest.input`).
    input: str = Field(min_length=1, max_length=4096)
    voice: str = Field(min_length=1, max_length=128)
    format: Literal["mp3", "opus", "aac", "flac", "wav"] = "mp3"


@router.post("/api/media/speech", status_code=status.HTTP_201_CREATED)
async def make_speech(request: Request, body: SpeechAsk) -> dict[str, Any]:
    """Starts a spoken clip on the server and returns its row."""
    person = await api._person(request)
    row = MediaRow(
        id=new_id(),
        owner=person.sub,
        door="speech",
        kind="made",
        status="running",
        created_at=time.time(),
        model=body.model,
        request=body.model_dump(),
    )
    await _jobs(request).start_speech(
        row,
        {
            "model": body.model,
            "input": body.input,
            "voice": body.voice,
            "response_format": body.format,
        },
    )
    return (await _views(_store(request), [row]))[0]


@router.post("/api/media/transcription", status_code=status.HTTP_201_CREATED)
async def make_transcript(
    request: Request,
    model: str = Form(min_length=1, max_length=256),
    language: str | None = Form(default=None, max_length=16),
    translate: bool = Form(default=False),
    clipSeconds: float | None = Form(default=None, ge=0, le=86_400),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Keeps the audio in the bin, then sends it to be transcribed (or
    translated into English) on the server. `clipSeconds` is the clip's own
    length as the page measured it, to set beside what the model heard."""
    person = await api._person(request)
    data = await file.read(files.TRANSCRIBE_LIMIT + 1)
    if len(data) > files.TRANSCRIBE_LIMIT:
        raise api._problem(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"That recording is larger than Eugene carries to be transcribed "
            f"({files.TRANSCRIBE_LIMIT // files.MIB} MiB). Send a shorter one.",
        )
    media_type = files.sniff_audio(data)
    if media_type is None:
        raise api._problem(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Workbench can send audio as MP3, WAV, Ogg, FLAC, AAC, WebM or MP4 (M4A). "
            "This file is none of them.",
        )
    now = time.time()
    name = (file.filename or f"recording.{files.extension(media_type)}")[:255]
    asked: dict[str, Any] = {"model": model, "name": name, "translate": translate}
    if language:
        asked["language"] = language
    if clipSeconds is not None:
        asked["clipSeconds"] = round(clipSeconds, 2)
    row = MediaRow(
        id=new_id(),
        owner=person.sub,
        door="transcription",
        kind="made",
        status="running",
        created_at=now,
        model=model,
        request=asked,
    )
    source = FileRecord(
        id=new_id(),
        owner=person.sub,
        chat_id=None,
        media_id=row.id,
        name=name,
        media_type=media_type,
        size=len(data),
        created_at=now,
    )
    path = files.path_of(request.app.state.settings.data_dir, person.sub, source.id)

    def write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    await asyncio.to_thread(write)
    store = _store(request)
    await store.add_media(row)
    await store.add_file(source)
    fields = {"model": model, "response_format": "json"}
    if language and not translate:
        fields["language"] = language
    await _jobs(request).start_transcription(row, fields, source, translate=translate)
    return (await _views(store, [row]))[0]


# --------------------------------------------------------------------------- #
# video, as a long job (slice 3, M11)
# --------------------------------------------------------------------------- #


class VideoAsk(BaseModel):
    model: str = Field(min_length=1, max_length=256)
    #: The gateway's own limit on this door (`VideoCreateRequest.prompt`).
    prompt: str = Field(min_length=1, max_length=32_000)
    seconds: int | None = Field(default=None, ge=1, le=120)
    size: str | None = Field(default=None, pattern=r"^\d{1,5}x\d{1,5}$")
    #: An image in the asker's bins, sent as the first frame.
    firstFrame: str | None = Field(default=None, min_length=1, max_length=64)


@router.post("/api/media/video", status_code=status.HTTP_201_CREATED)
async def make_video(request: Request, body: VideoAsk) -> dict[str, Any]:
    """Submits a video job on the server and returns its row. The Foreman
    polls it from here on, through restarts, and keeps the video (M11).
    The page asks before sending, with the price where one is listed."""
    person = await api._person(request)
    frames = await _references(
        request, person, [body.firstFrame] if body.firstFrame else [], what="first frame"
    )
    gateway: dict[str, Any] = {"model": body.model, "prompt": body.prompt}
    if body.seconds is not None:
        gateway["seconds"] = str(body.seconds)
    if body.size is not None:
        gateway["size"] = body.size
    row = MediaRow(
        id=new_id(),
        owner=person.sub,
        door="video",
        kind="made",
        status="running",
        created_at=time.time(),
        model=body.model,
        request=body.model_dump(exclude_none=True),
    )
    await _jobs(request).start_video(row, gateway, frames[0] if frames else None)
    return (await _views(_store(request), [row]))[0]


# --------------------------------------------------------------------------- #
# into a chat (M7)
# --------------------------------------------------------------------------- #


class ToChat(BaseModel):
    fileId: str = Field(min_length=1, max_length=64)


@router.post("/api/media/{media_id}/to-chat", status_code=status.HTTP_201_CREATED)
async def to_chat(request: Request, media_id: str, body: ToChat) -> dict[str, Any]:
    """A copy of one image or clip, attached to a new chat. The copy is the chat's:
    deleting the result keeps it, and deleting the chat keeps the result."""
    person = await api._person(request)
    row = await _own(request, person, media_id)
    store = _store(request)
    record = next((r for r in await store.files_for_media([row.id]) if r.id == body.fileId), None)
    if record is None:
        raise api._problem(status.HTTP_404_NOT_FOUND, "There is no such file in this result.")
    kind = files.KINDS.get(record.media_type)
    shown = record.media_type.split("/")[1].removeprefix("mpeg").upper() or "MP3"
    if kind not in ("image", "audio"):
        carried = (
            "PNG and JPEG images" if record.media_type.startswith("image/") else "WAV and MP3 audio"
        )
        raise api._problem(
            status.HTTP_400_BAD_REQUEST, f"A chat carries {carried}, and this one is {shown}."
        )
    if record.size > files.LIMITS[kind]:
        raise api._problem(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"A chat carries {kind} up to {files.describe_limit(kind)}, and this one is "
            f"{record.size / files.MIB:.1f} MiB.",
        )
    root = request.app.state.settings.data_dir
    now = time.time()
    said = row.request.get("prompt") or row.request.get("input") or row.request.get("name") or ""
    title = " ".join(str(said).split())
    chat = Chat(
        id=new_id(),
        owner=person.sub,
        title=(title[: api._TITLE_LENGTH] or api.NEW_CHAT),
        model=None,
        created_at=now,
        updated_at=now,
    )
    copy = FileRecord(
        id=new_id(),
        owner=person.sub,
        chat_id=chat.id,
        name=record.name,
        media_type=record.media_type,
        size=record.size,
        created_at=now,
        width=record.width,
        height=record.height,
    )

    def duplicate() -> None:
        source = files.path_of(root, record.owner, record.id)
        target = files.path_of(root, person.sub, copy.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    await asyncio.to_thread(duplicate)
    await store.create_chat(chat)
    await store.add_file(copy)
    return {
        "chatId": chat.id,
        "attachment": {
            "id": copy.id,
            "name": copy.name,
            "mediaType": copy.media_type,
            "size": copy.size,
        },
    }


# --------------------------------------------------------------------------- #
# the owner's read-only view (M3)
# --------------------------------------------------------------------------- #


@router.get("/api/people/{sub}/media")
async def person_media(
    request: Request, sub: str, door: str, before: float | None = None
) -> dict[str, Any]:
    await api.people(request)  # the same checks as people's chats
    store = _store(request)
    rows = await store.media_list(sub, _door(door), before=before, limit=_PAGE)
    return {"items": [{**v, "readOnly": True} for v in await _views(store, rows)]}
