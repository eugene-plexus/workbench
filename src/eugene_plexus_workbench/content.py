"""A tool's result as content blocks (workbench-rich-content-roadmap.md RC3).

One path for every tool: an MCP server reached directly or as a local
process, and a job site's server reached through Eugene. Each result becomes
an ordered list of blocks, kept on its call in the answer (`content`), and a
text the model is sent again (`result`), both made here, from the same
blocks, so they cannot disagree:

- `text`: the tool's words, as they were.
- `image`, `audio`, `video`, `file`: bytes the tool returned, kept as a file of
  the answer's chat before the answer says it has them. What the bytes are is
  read from the bytes: a picture the bytes do not bear out (an SVG, HTML, or
  anything that only says it is a PNG) is a file, offered for download and
  never drawn on the page.
- `data`: the tool's structured content, as JSON.
- `link`: an address the tool named. Nothing is fetched: a URI is not
  permission to read anything.
- `unsupported`: what came back that Workbench does not keep, and why.

The model sees the words and the data, and one line for each picture, sound
or file saying it is shown to the person and not to the model: a picture the
page shows is never implied to have been seen. No file id, path or address
of Workbench's own is in that text.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from . import files
from .store import FileRecord, Store

#: The shape of `content` on a call; a page that reads another skips it.
CONTENT_VERSION = 1
#: What one call may keep: bytes for each kind, how many files, how much in all.
LIMITS = {
    "image": 5 * files.MIB,
    "audio": 10 * files.MIB,
    "video": 10 * files.MIB,
    "file": 10 * files.MIB,
}
MAX_FILES = 8
CALL_LIMIT = 20 * files.MIB
#: What one person's tool results may hold in all.
PERSON_LIMIT = 1024 * files.MIB
#: Blocks one result may hold; more are counted in one note.
MAX_BLOCKS = 64
#: The text sent to the model again, and one data block's JSON, at most.
MAX_TEXT = 65_536
MAX_DATA = 65_536
#: A link's name, title and description, and its address, at most.
MAX_LABEL = 500
MAX_URI = 2_000

_KIND_WORDS = {"image": "a picture", "audio": "a sound", "video": "a video", "file": "a file"}


def _short(value: Any, limit: int = MAX_LABEL) -> str:
    text = value if isinstance(value, str) else ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _name_from(uri: str) -> str:
    """The last part of an address's path, as a file name, if it has one."""
    try:
        name = PurePosixPath(unquote(urlsplit(uri).path)).name
    except ValueError:
        return ""
    return name[:200].strip()


#: What a file's name may not hold: a path, a device, a control character.
_UNSAFE = frozenset('/\\:*?"<>|')


def _safe_name(name: str, kind: str, media_type: str) -> str:
    """A name to save a file under: no path or control character, and a
    picture, sound or video named for what its bytes are, not for what it said."""
    cleaned = "".join(c for c in name if c.isprintable() and c not in _UNSAFE).strip(" .")[:200]
    extension = files.extension(media_type)
    if kind == "file":
        return cleaned or f"file.{extension}"
    stem = cleaned.rsplit(".", 1)[0].strip(" .") if "." in cleaned else cleaned
    return f"{stem or ('picture' if kind == 'image' else kind)}.{extension}"


def _kind_of(data: bytes, declared: str) -> tuple[str, str]:
    """(kind, media type) from the bytes: a picture, sound or video only when
    the bytes are one Workbench shows; anything else is a file."""
    family = declared.split("/", 1)[0].lower()
    if family == "image" and (found := files.sniff_image(data)):
        return "image", found
    if family == "audio" and (found := files.sniff_audio(data)):
        return "audio", found
    if family == "video" and (found := files.sniff_video(data[: files.HEAD_BYTES])):
        return "video", found
    for kind, sniff in (("image", files.sniff_image), ("audio", files.sniff_audio)):
        if family not in {"image", "audio", "video"} and (found := sniff(data)):
            return kind, found
    return "file", "application/octet-stream"


def _sized(size: int) -> str:
    return files.describe_size(size) if size >= 1024 else f"{size} bytes"


@dataclass
class Keeper:
    """Keeps a call's pictures, sounds and files as files of the answer's chat:
    the bytes first, then the row, so a file the store names has its bytes."""

    store: Store
    root: Path
    owner: str
    chat_id: str
    message_id: str
    count: int = 0
    total: int = 0
    kept: list[str] = field(default_factory=list)

    async def keep(
        self, data: bytes, media_type: str, kind: str, name: str
    ) -> dict[str, Any] | str:
        """The kept file's description, or why it was not kept."""
        limit = LIMITS[kind]
        if len(data) > limit:
            return f"it is larger than {_sized(limit)}"
        if self.count >= MAX_FILES:
            return f"one call may keep {MAX_FILES} files"
        if self.total + len(data) > CALL_LIMIT:
            return f"one call may keep {files.describe_size(CALL_LIMIT)}"
        used = await self.store.tool_bytes(self.owner)
        if used + len(data) > PERSON_LIMIT:
            return (
                f"your tool results already hold {files.describe_size(used)} of the "
                f"{files.describe_size(PERSON_LIMIT)} each person may keep"
            )
        file_id = secrets.token_urlsafe(12)
        target = files.path_of(self.root, self.owner, file_id)
        size = files.image_size(data) if kind == "image" else None

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".part")
            try:
                part.write_bytes(data)
                part.replace(target)
            except BaseException:
                part.unlink(missing_ok=True)
                raise

        async def kept() -> None:
            await asyncio.to_thread(write)
            await self.store.add_file(
                FileRecord(
                    id=file_id,
                    owner=self.owner,
                    chat_id=self.chat_id,
                    name=name,
                    media_type=media_type,
                    size=len(data),
                    created_at=time.time(),
                    width=size[0] if size else None,
                    height=size[1] if size else None,
                    message_id=self.message_id,
                    origin="tool",
                )
            )

        # Kept whole even if the answer is stopped meanwhile: a file and its row,
        # which the answer's end lets go when no call names it (answers.py).
        self.kept.append(file_id)
        try:
            await asyncio.shield(kept())
        except OSError as exc:
            self.kept.remove(file_id)
            return f"it could not be written ({exc.strerror or type(exc).__name__})"
        self.count += 1
        self.total += len(data)
        described: dict[str, Any] = {
            "fileId": file_id,
            "name": name,
            "mediaType": media_type,
            "size": len(data),
        }
        if size:
            described.update(width=size[0], height=size[1])
        return described


async def _asset(
    block_id: str,
    encoded: Any,
    declared: Any,
    name: str,
    keeper: Keeper | None,
) -> tuple[dict[str, Any], str]:
    """A picture, sound, video or file the tool sent as base64: its block and
    the line the model is sent."""
    declared_type = declared if isinstance(declared, str) else ""
    words = _KIND_WORDS.get(declared_type.split("/", 1)[0], "a file")
    if not isinstance(encoded, str):
        reason = "it came without its bytes"
    elif len(encoded) > (max(LIMITS.values()) * 4) // 3 + 4:
        reason = f"it is larger than {files.describe_size(max(LIMITS.values()))}"
    else:
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            data = None
        if data is None:
            reason = "its bytes were not valid base64"
        elif keeper is None:
            reason = "this answer cannot keep files"
        else:
            kind, media_type = _kind_of(data, declared_type)
            name = _safe_name(name, kind, media_type)
            kept = await keeper.keep(data, media_type, kind, name)
            if isinstance(kept, dict):
                said = f"{_KIND_WORDS[kind]}, {json.dumps(name, ensure_ascii=False)}"
                said += f" ({media_type}, {_sized(len(data))})"
                block = {"id": block_id, "type": kind, **kept}
                if kind == "file" and declared_type and declared_type != media_type:
                    # What it said it was, for the person: not how it is served.
                    block["declaredType"] = _short(declared_type, 100)
                return block, f"[The tool returned {said}. The person sees it; you do not.]"
            reason = kept
    block = {"id": block_id, "type": "unsupported", "what": words, "reason": reason}
    return block, f"[The tool returned {words} that Workbench did not keep: {reason}.]"


async def normalize(
    call_id: str,
    blocks: Any,
    structured: Any = None,
    keeper: Keeper | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """A result's blocks (MCP's own shapes, as JSON) and its structured content:
    the content blocks kept on the call, and the text sent to the model."""
    out: list[dict[str, Any]] = []
    lines: list[str] = []
    items = blocks if isinstance(blocks, list) else []
    # The words are kept on the call only as far as the model is sent them:
    # the call is saved and sent to the page whole, at every checkpoint.
    budget = MAX_TEXT

    def stored(text: str) -> str:
        nonlocal budget
        kept = text if len(text) <= budget else text[:budget] + "…"
        budget = max(0, budget - len(text))
        return kept

    for index, block in enumerate(items[:MAX_BLOCKS]):
        block_id = f"{call_id}:{index}"
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "text":
            text = block.get("text")
            text = text if isinstance(text, str) else ""
            out.append({"id": block_id, "type": "text", "text": stored(text)})
            lines.append(text)
        elif kind in {"image", "audio"}:
            made, line = await _asset(
                block_id, block.get("data"), block.get("mimeType"), "", keeper
            )
            out.append(made)
            lines.append(line)
        elif kind == "resource_link":
            uri = _short(block.get("uri"), MAX_URI)
            link = {
                "id": block_id,
                "type": "link",
                "uri": uri,
                "name": _short(block.get("name")) or _name_from(uri),
            }
            for key in ("title", "description", "mimeType"):
                if isinstance(block.get(key), str):
                    link[key] = _short(block[key])
            out.append(link)
            lines.append(f"[The tool named {link['name'] or 'a resource'}: {uri}]")
        elif kind == "resource" and isinstance(block.get("resource"), dict):
            resource = block["resource"]
            uri = _short(resource.get("uri"), MAX_URI)
            if isinstance(resource.get("text"), str):
                out.append(
                    {
                        "id": block_id,
                        "type": "text",
                        "text": stored(resource["text"]),
                        "source": uri,
                    }
                )
                lines.append(resource["text"])
            else:
                made, line = await _asset(
                    block_id,
                    resource.get("blob"),
                    resource.get("mimeType"),
                    _name_from(uri),
                    keeper,
                )
                out.append(made)
                lines.append(line)
        else:
            what = _short(kind, 40) if isinstance(kind, str) else "unreadable"
            out.append(
                {
                    "id": block_id,
                    "type": "unsupported",
                    "what": what,
                    "reason": "Workbench does not show this kind of result",
                }
            )
            lines.append(f"[The tool returned a {what} result Workbench does not show.]")
    if len(items) > MAX_BLOCKS:
        dropped = len(items) - MAX_BLOCKS
        out.append(
            {
                "id": f"{call_id}:{MAX_BLOCKS}",
                "type": "unsupported",
                "what": f"{dropped} more blocks",
                "reason": f"one result may hold {MAX_BLOCKS}",
            }
        )
        lines.append(f"[The tool returned {dropped} more blocks that Workbench did not keep.]")
    if structured is not None:
        try:
            # NaN and Infinity are no JSON a page can read: the chat would not load.
            encoded = json.dumps(structured, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            encoded = None
            out.append(
                {
                    "id": f"{call_id}:data",
                    "type": "unsupported",
                    "what": "structured data",
                    "reason": "it is not JSON (it holds NaN, Infinity or what JSON cannot say)",
                }
            )
            lines.append("[The tool returned structured data that is not JSON.]")
        if encoded is not None:
            # The same JSON as a text block is said once.
            if not any(b["type"] == "text" and b["text"].strip() == encoded for b in out):
                lines.append(encoded)
            if len(encoded) <= MAX_DATA:
                out.append({"id": f"{call_id}:data", "type": "data", "data": structured})
            else:
                out.append(
                    {
                        "id": f"{call_id}:data",
                        "type": "unsupported",
                        "what": "structured data",
                        "reason": f"it is longer than {MAX_DATA:,} characters",
                    }
                )
    text = "\n".join(line for line in lines if line)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f"\n[Tool result truncated at {MAX_TEXT} characters.]"
    return out, text


async def remove_orphans(store: Store, root: Path) -> int:
    """Tool files kept for an answer that stopped before it was saved with
    them: no answer names them, so nothing could show or delete them. Removed
    at start; how many."""
    orphans = await store.tool_orphans()
    for record in orphans:
        await asyncio.to_thread(files.remove_stored, root, record.owner, record.id)
    if orphans:
        await store.delete_files([r.id for r in orphans])
    return len(orphans)


async def let_go(keepers: list[Keeper], rounds: list[dict[str, Any]]) -> int:
    """The files these calls kept that no call in `rounds` names: a call
    stopped after it kept a file and before it said so. Removed, rows and
    bytes; how many."""
    named = json.dumps(rounds)
    gone = [
        (keeper, file_id)
        for keeper in keepers
        for file_id in keeper.kept
        if f'"{file_id}"' not in named
    ]
    for keeper, file_id in gone:
        await asyncio.to_thread(files.remove_stored, keeper.root, keeper.owner, file_id)
    if gone:
        await keepers[0].store.delete_files([file_id for _, file_id in gone])
    return len(gone)
