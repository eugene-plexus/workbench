"""Media requests run here, not in a tab (workbench-media-screens.md §2.2).

A media screen asks; this module sends the request to the gateway, keeps
what comes back in the person's bins, and tells every tab that person has
open. So, as with answers (W1):

- **closing the tab loses nothing** (M2). A paid image whose tab closed is
  still kept;
- **a restart of Workbench mid-request** marks it `interrupted`
  (`Store.mark_media_interrupted`, at boot), and the page says the provider
  may have billed it, because the gateway may already have been asked.

One stream per person, not per chat: one person can have several requests
running on several screens.

**A video is a long job (M11).** The gateway accepts it and answers a
handle; the Foreman here polls the handle every `POLL_SECONDS`, keeps it in
the row's `job`, and downloads the MP4 into the bin the moment the job is
done, because the provider's retention is unknown. The provider keeps
working, and billing, whatever Workbench does, so a job with a handle is
never marked `interrupted`: a restart, graceful or not, polls it again
(`MediaJobs.resume`, at boot).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import errno
import logging
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import files
from .hub import Hub, HubError
from .store import FileRecord, MediaRow, Store

log = logging.getLogger(__name__)

#: A watching tab that falls this far behind is told to reload.
_QUEUE_DEPTH = 200

#: What a request's `x_eugene_plexus` says about what served it (§2.4).
_SERVED = ("driver", "backend", "latency_ms", "attempts", "tier", "waited_ms", "swapped_in")

#: How often the Foreman asks after a running video (§5).
POLL_SECONDS = 5.0

#: A poll the gateway refuses with one of these ends the job: asking again
#: cannot help. Anything else (its 503 *"Poll again once it is; the job is
#: not lost"*, a gateway that cannot be reached, a limit) is said on the
#: job and asked again at the next poll.
_FINAL_POLL = frozenset({400, 401, 403, 404})

#: Added to a refusal that ends a job the provider may have run anyway.
MAY_HAVE_BILLED = (
    "The provider may still make this video, and bill it: Eugene cannot cancel a video job."
)


def new_id() -> str:
    return secrets.token_urlsafe(12)


def file_view(record: FileRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "mediaType": record.media_type,
        "size": record.size,
        "width": record.width,
        "height": record.height,
    }


def media_view(row: MediaRow, records: list[FileRecord]) -> dict[str, Any]:
    """A result as the page reads it."""
    return {
        "id": row.id,
        "door": row.door,
        "kind": row.kind,
        "model": row.model,
        "request": row.request,
        "status": row.status,
        "createdAt": row.created_at,
        "finishedAt": row.finished_at,
        "served": row.served,
        "units": row.units,
        "text": row.text,
        "error": row.error,
        # A long job's state as the Foreman last saw it; its handle stays here.
        "job": {k: v for k, v in row.job.items() if k != "handle"} if row.job else None,
        "files": [file_view(r) for r in records if r.media_id == row.id],
    }


def data_url(media_type: str, data: bytes) -> str:
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def disk_full(root: Path) -> str:
    """A full disk, named with its free space (M4)."""
    try:
        free = shutil.disk_usage(root).free
        room = f"{free / files.MIB:.1f} MiB free"
    except OSError:
        room = "its free space could not be read"
    return f"The disk holding Workbench's data ({root}) is full: {room}."


@dataclass
class Running:
    row: MediaRow
    task: asyncio.Task[None] | None = None
    shutting_down: bool = False


def resource_error(answer: dict[str, Any]) -> str:
    """A failed video job's reason, in the gateway's words."""
    error = answer.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    return str(message) if message else "The provider reported the video job failed."


def cost_of(answer: dict[str, Any]) -> float | None:
    """What the provider says it billed (`x_eugene_plexus.cost_usd`, §6.4)."""
    info = answer.get("x_eugene_plexus")
    cost = info.get("cost_usd") if isinstance(info, dict) else None
    return float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None


@dataclass(eq=False)  # kept in a set, by identity
class _Watch:
    owner: str
    queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_QUEUE_DEPTH)
    )


class MediaWatch:
    """One tab watching one person's media."""

    def __init__(self, jobs: MediaJobs, owner: str) -> None:
        self._jobs = jobs
        self._watch = _Watch(owner)
        self.queue = self._watch.queue

    def close(self) -> None:
        self._jobs._unwatch(self._watch)


class MediaJobs:
    """Every media request being run, by its row's id."""

    def __init__(self, store: Store, hub: Hub, data_dir: Path) -> None:
        self._store = store
        self._hub = hub
        self._root = data_dir
        self._running: dict[str, Running] = {}
        self._watchers: dict[str, set[_Watch]] = {}

    def running(self, media_id: str) -> bool:
        return media_id in self._running

    # --- watching -------------------------------------------------------

    def watch(self, owner: str) -> MediaWatch:
        watch = MediaWatch(self, owner)
        self._watchers.setdefault(owner, set()).add(watch._watch)
        return watch

    def _unwatch(self, watch: _Watch) -> None:
        watchers = self._watchers.get(watch.owner)
        if watchers is not None:
            watchers.discard(watch)
            if not watchers:
                self._watchers.pop(watch.owner, None)

    def publish(self, owner: str, event: dict[str, Any]) -> None:
        for watch in list(self._watchers.get(owner, ())):
            try:
                watch.queue.put_nowait(event)
            except asyncio.QueueFull:
                self._watchers[owner].discard(watch)
                with contextlib.suppress(asyncio.QueueFull):
                    watch.queue.put_nowait({"type": "reload"})

    async def _changed(self, row: MediaRow) -> None:
        records = await self._store.files_for_media([row.id])
        self.publish(row.owner, {"type": "media", "item": media_view(row, records)})

    # --- running --------------------------------------------------------

    async def start_images(
        self, row: MediaRow, body: dict[str, Any], references: list[FileRecord]
    ) -> None:
        """Store `row` as running and send its image request. `body` is the
        gateway's request; `references` are bin files to edit from."""
        await self._start(row, lambda running: self._images(running, body, references))

    async def start_speech(self, row: MediaRow, body: dict[str, Any]) -> None:
        """Store `row` as running and ask for its spoken clip (slice 2)."""
        await self._start(row, lambda running: self._speech(running, body))

    async def start_transcription(
        self, row: MediaRow, fields: dict[str, str], source: FileRecord, *, translate: bool
    ) -> None:
        """Store `row` as running and send its audio (`source`, already kept
        in the bin) to be transcribed, or translated into English."""
        await self._start(
            row, lambda running: self._transcribe(running, fields, source, translate=translate)
        )

    async def start_video(
        self, row: MediaRow, body: dict[str, Any], first_frame: FileRecord | None
    ) -> None:
        """Store `row` as running, submit its video job, and have the
        Foreman poll it until it ends (slice 3, M11)."""
        await self._start(row, lambda running: self._video(running, body, first_frame))

    async def resume(self) -> int:
        """Boot: poll again every video job the gateway accepted that had not
        ended when Workbench stopped. Returns how many."""
        rows = await self._store.media_jobs_running()
        for row in rows:
            running = Running(row=row)
            self._running[row.id] = running
            running.task = asyncio.create_task(
                self._run(running, self._video(running, {}, None)), name=f"media-{row.id}"
            )
        return len(rows)

    async def _start(self, row: MediaRow, work: Any) -> None:
        if not await self._store.media(row.id):
            await self._store.add_media(row)
        running = Running(row=row)
        self._running[row.id] = running
        running.task = asyncio.create_task(
            self._run(running, work(running)), name=f"media-{row.id}"
        )
        await self._changed(row)

    async def stop(self, media_id: str) -> bool:
        running = self._running.get(media_id)
        if running is None or running.task is None:
            return False
        running.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await running.task
        return True

    async def aclose(self) -> None:
        """Shutdown: a request cut off here is `interrupted`, not `stopped`."""
        for running in list(self._running.values()):
            running.shutting_down = True
            if running.task is not None:
                running.task.cancel()
        tasks = [r.task for r in self._running.values() if r.task is not None]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, running: Running, work: Any) -> None:
        row = running.row
        kept = False
        try:
            await work
            row.status = "done"
        except asyncio.CancelledError:
            # M11: the provider keeps working, and billing, on a job it
            # accepted. At shutdown it stays running, and the next start
            # polls it again.
            kept = running.shutting_down and row.job is not None
            row.status = "interrupted" if running.shutting_down else "stopped"
        except HubError as exc:
            row.status = "failed"
            row.error = {"message": exc.message, "param": exc.param, "status": exc.status}
        except OSError as exc:
            row.status = "failed"
            message = (
                disk_full(self._root)
                if exc.errno == errno.ENOSPC
                else f"Workbench could not keep the result on disk: {exc}"
            )
            row.error = {"message": message, "param": None, "status": None}
        except Exception as exc:
            log.exception("a media request (%s) failed", row.id)
            row.status = "failed"
            row.error = {
                "message": f"Workbench failed while keeping this result: {exc}",
                "param": None,
                "status": None,
            }
        finally:
            await self._settle(row, kept=kept)

    async def _settle(self, row: MediaRow, *, kept: bool) -> None:
        """Keep how a request ended; or, for a job kept for the next start,
        only its last poll."""
        if kept:
            row.status = "running"
            try:
                await asyncio.shield(self._store.update_media(row.id, job=row.job))
            finally:
                self._running.pop(row.id, None)
            return
        row.finished_at = time.time()
        try:
            await asyncio.shield(
                self._store.update_media(
                    row.id,
                    status=row.status,
                    finished_at=row.finished_at,
                    served=row.served,
                    units=row.units,
                    text=row.text,
                    error=row.error,
                    job=row.job,
                )
            )
        finally:
            self._running.pop(row.id, None)
            await self._changed(row)

    async def _images(
        self, running: Running, body: dict[str, Any], references: list[FileRecord]
    ) -> None:
        row = running.row
        if references:

            def read() -> list[dict[str, str]]:
                return [
                    {
                        "image_url": data_url(
                            r.media_type, files.path_of(self._root, r.owner, r.id).read_bytes()
                        )
                    }
                    for r in references
                ]

            body = {**body, "images": await asyncio.to_thread(read)}
        answer, request_id = await self._hub.images(body, edit=bool(references))
        routing = answer.get("x_eugene_plexus") or {}
        row.served = {
            **{k: routing.get(k) for k in _SERVED if k in routing},
            "requestId": request_id,
        }
        images: list[tuple[bytes, str]] = []
        for index, item in enumerate(answer.get("data") or []):
            raw = item.get("b64_json") if isinstance(item, dict) else None
            if not isinstance(raw, str):
                raise HubError(
                    f"The gateway's answer has no image data in place {index + 1} "
                    "(Workbench asks for b64_json, which the gateway always answers).",
                    kind="gateway",
                )
            data = base64.b64decode(raw)
            media_type = files.sniff_image(data)
            if media_type is None:
                raise HubError(
                    f"The gateway answered image {index + 1} with bytes Workbench cannot read as "
                    f"PNG, JPEG or WebP (they start {data[:8].hex(' ')}).",
                    kind="gateway",
                )
            images.append((data, media_type))
        if not images:
            raise HubError("The gateway answered the image request with no images.", kind="gateway")
        records = []
        for index, (data, media_type) in enumerate(images, start=1):
            size = files.image_size(data)
            record = FileRecord(
                id=new_id(),
                owner=row.owner,
                chat_id=None,
                media_id=row.id,
                name=f"image-{index}.{files.extension(media_type)}",
                media_type=media_type,
                size=len(data),
                created_at=time.time(),
                width=size[0] if size else None,
                height=size[1] if size else None,
            )
            path = files.path_of(self._root, row.owner, record.id)

            def write(path: Path = path, data: bytes = data) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

            await asyncio.to_thread(write)
            await self._store.add_file(record)
            records.append(record)
        row.units = {
            "images": len(records),
            "sizes": [f"{r.width}x{r.height}" if r.width else None for r in records],
            "usage": answer.get("usage"),
        }

    async def _speech(self, running: Running, body: dict[str, Any]) -> None:
        row = running.row
        audio, headers = await self._hub.speech(body)
        row.served = served_from_headers(headers)
        media_type = files.sniff_audio(audio)
        if media_type is None:
            raise HubError(
                "The gateway answered the speech request with bytes Workbench cannot read as "
                f"audio (they start {audio[:8].hex(' ')}).",
                kind="gateway",
            )
        await self._keep(row, audio, media_type, f"speech.{files.extension(media_type)}")
        row.units = {"characters": len(str(body.get("input") or ""))}

    async def _transcribe(
        self, running: Running, fields: dict[str, str], source: FileRecord, *, translate: bool
    ) -> None:
        row = running.row
        path = files.path_of(self._root, source.owner, source.id)
        audio = await asyncio.to_thread(path.read_bytes)
        answer, headers = await self._hub.transcribe(
            fields, (source.name, audio, source.media_type), translate=translate
        )
        row.served = served_from_headers(headers)
        row.text = str(answer["text"]).strip()
        found = answer.get("usage")
        usage: dict[str, Any] = found if isinstance(found, dict) else {}
        # What the backend says it heard: seconds where its usage counts
        # them (OpenAI rounds them up), tokens where it counts those (§0).
        row.units = {
            "heardSeconds": usage.get("seconds") if usage.get("type") == "duration" else None,
            "tokens": usage.get("total_tokens") if usage.get("type") == "tokens" else None,
            "clipSeconds": row.request.get("clipSeconds"),
        }

    async def _video(
        self, running: Running, body: dict[str, Any], first_frame: FileRecord | None
    ) -> None:
        """Submit the job, unless it was submitted before a restart; then
        poll it until it ends, and keep the video the moment it is done."""
        row = running.row
        if row.job is None:
            if first_frame is not None:
                path = files.path_of(self._root, first_frame.owner, first_frame.id)
                data = await asyncio.to_thread(path.read_bytes)
                body = {
                    **body,
                    "input_reference": {"image_url": data_url(first_frame.media_type, data)},
                }
            answer, request_id = await self._hub.video(body)
            routing = answer.get("x_eugene_plexus") or {}
            row.served = {
                **{k: routing.get(k) for k in _SERVED if k in routing},
                "requestId": request_id,
            }
            row.job = {
                "handle": answer["id"],
                "status": answer.get("status") or "queued",
                "progress": answer.get("progress") or 0,
                "polls": 0,
                "polledAt": time.time(),
                "problem": None,
            }
            # The handle is what a restart polls again (M11): kept at once.
            await self._store.update_media(row.id, served=row.served, job=row.job)
            await self._changed(row)
        await self._foreman(row)

    async def _foreman(self, row: MediaRow) -> None:
        """Poll one job every `POLL_SECONDS` until it ends (§5)."""
        assert row.job is not None
        job = row.job
        while True:
            try:
                answer = await self._hub.video_job(job["handle"])
                state = str(answer.get("status") or "")
                job.update(
                    status=state,
                    progress=answer.get("progress") or 0,
                    polls=int(job.get("polls") or 0) + 1,
                    polledAt=time.time(),
                    problem=None,
                )
                if state == "failed":
                    row.units = {**(row.units or {}), "costUsd": cost_of(answer)}
                    raise HubError(resource_error(answer), kind="video")
                if state == "completed":
                    row.units = {
                        "seconds": _seconds(answer.get("seconds")),
                        "size": answer.get("size") if answer.get("size") != "auto" else None,
                        "costUsd": cost_of(answer),
                    }
                    await self._download(row, job["handle"])
                    return
            except HubError as exc:
                if exc.kind == "video":
                    raise
                if exc.status in _FINAL_POLL or exc.kind == "key":
                    raise _ended(exc) from exc
                # Said on the job, and asked again at the next poll.
                job.update(
                    polls=int(job.get("polls") or 0) + 1, polledAt=time.time(), problem=exc.message
                )
            await self._store.update_media(row.id, job=job)
            await self._changed(row)
            await asyncio.sleep(POLL_SECONDS)

    async def _download(self, row: MediaRow, handle: str) -> None:
        """The finished video, streamed into the row's bin. A part written
        before a failure is removed."""
        record = FileRecord(
            id=new_id(),
            owner=row.owner,
            chat_id=None,
            media_id=row.id,
            name="video.mp4",
            media_type="video/mp4",
            size=0,
            created_at=time.time(),
        )
        path = files.path_of(self._root, row.owner, record.id)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        out = await asyncio.to_thread(path.open, "wb")
        head = bytearray()
        try:

            async def write(chunk: bytes) -> None:
                if len(head) < 12:
                    head.extend(chunk[: 12 - len(head)])
                await asyncio.to_thread(out.write, chunk)

            record.size = await self._hub.video_content(handle, write)
            await asyncio.to_thread(out.close)
            if files.sniff_video(bytes(head)) is None:
                raise HubError(
                    "The gateway answered the finished video with bytes Workbench cannot read "
                    f"as MP4 (they start {bytes(head[:8]).hex(' ') or 'with nothing'}).",
                    kind="video",
                )
        except BaseException:
            await asyncio.to_thread(out.close)
            await asyncio.to_thread(path.unlink, True)
            raise
        await self._store.add_file(record)

    async def _keep(self, row: MediaRow, data: bytes, media_type: str, name: str) -> FileRecord:
        """Write one file into the row's bin and record it."""
        record = FileRecord(
            id=new_id(),
            owner=row.owner,
            chat_id=None,
            media_id=row.id,
            name=name,
            media_type=media_type,
            size=len(data),
            created_at=time.time(),
        )
        path = files.path_of(self._root, row.owner, record.id)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(write)
        await self._store.add_file(record)
        return record


def _seconds(raw: Any) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _ended(exc: HubError) -> HubError:
    """A poll refusal that ends a job, in the gateway's words. A job the
    gateway no longer finds for this key (a key made again, say) may have
    been made and billed all the same (§5)."""
    message = exc.message
    if exc.status == 404 or exc.kind == "key":
        message = f"{message} {MAY_HAVE_BILLED}"
    return HubError(message, status=exc.status, kind=exc.kind, param=exc.param)


def served_from_headers(headers: dict[str, str]) -> dict[str, Any]:
    """What served a clip or a transcript: the gateway's `x-eugene-plexus-*`
    headers (U4), as `x_eugene_plexus` says it in a JSON body."""
    out: dict[str, Any] = {}
    lowered = {k.lower(): v for k, v in headers.items()}
    for key in _SERVED:
        value = lowered.get("x-eugene-plexus-" + key.replace("_", "-"))
        if value is None:
            continue
        if key in ("latency_ms", "attempts", "tier", "waited_ms"):
            try:
                out[key] = int(value)
            except ValueError:
                continue
        elif key == "swapped_in":
            out[key] = value == "true"
        else:
            out[key] = value
    out["requestId"] = lowered.get("x-request-id")
    return out
