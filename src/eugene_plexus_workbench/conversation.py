"""A chat, as the request the gateway is sent for its next answer.

The whole conversation travels in each request, as with any OpenAI
client: the chat's instructions as a `system` message (with the person's
notes after them, when the chat uses memory: `memory.py`), then every turn.
A user turn with attachments becomes content parts in OpenAI's shapes
(`common.yaml` `MessageContentPart`): an image as a `data:` URL, a PDF a model
reads as a file part, audio as `input_audio`. An assistant turn is its answer only:
text it wrote before its last web search is a draft and is not sent back
(workbench#1). An answer that failed before it said anything is left out,
because the model never said it.

**A document is text in its message** (MR2, `context.py`): a PDF, Word, Markdown,
text, CSV, HTML or code file the person attached is put in the user message it
was attached to, as a delimited block before the person's own words, and is
replayed there every turn, byte for byte, so the engine's prompt cache holds.
The stored message is only the person's words.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import files
from .answers import answer_text
from .memory import system_text
from .store import Chat, FileRecord, Message
from .tools import transcript

#: What a chat's settings may hold, and the gateway's name for each.
SAMPLING = {"temperature": "temperature", "topP": "top_p", "maxTokens": "max_tokens"}


@dataclass(frozen=True)
class SentDocument:
    """How one attached document goes in a request (`context.Plan`): its text as
    a delimited block, or, for a model that reads PDFs, the file itself."""

    id: str
    text: str | None
    native: bool = False


def _part(record: FileRecord, data: bytes) -> dict[str, Any]:
    encoded = base64.b64encode(data).decode("ascii")
    kind = files.KINDS[record.media_type]
    if kind == "image":
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{record.media_type};base64,{encoded}"},
        }
    if kind == "audio":
        fmt = "wav" if record.media_type == "audio/wav" else "mp3"
        return {"type": "input_audio", "input_audio": {"data": encoded, "format": fmt}}
    return {
        "type": "file",
        "file": {"filename": record.name, "file_data": f"data:application/pdf;base64,{encoded}"},
    }


#: What stands for a part that is not text when only its size in tokens is wanted.
_STUB = {"type": "image_url", "image_url": {"url": ""}}


def _user_content(
    message: Message,
    records: dict[str, FileRecord],
    root: Path,
    documents: Mapping[str, SentDocument] | None,
    lean: bool,
) -> str | list[dict[str, Any]]:
    attached = [records[i] for i in message.attachments if i in records]
    if not attached:
        return message.content
    blocks: list[str] = []
    media: list[dict[str, Any]] = []
    for record in attached:
        if record.media_type in files.DOCUMENT_TYPES:
            if lean:
                continue
            sent = (documents or {}).get(record.id)
            if sent is None:
                # Every document of a request is decided before it is built
                # (`context.plan`); one that was not must not be left out unsaid.
                raise LookupError(f"the document {record.id} was not planned for this request")
            if sent.native:
                data = files.path_of(root, record.owner, record.id).read_bytes()
                media.append(_part(record, data))
            elif sent.text:
                blocks.append(sent.text)
            continue
        if lean:
            media.append(dict(_STUB))
        else:
            data = files.path_of(root, record.owner, record.id).read_bytes()
            media.append(_part(record, data))
    words = [*blocks, message.content] if message.content else blocks
    text = "\n\n".join(words)
    if not media:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    return parts + media


def request_for(
    chat: Chat,
    history: list[Message],
    records: list[FileRecord],
    *,
    root: Path,
    model: str,
    search: bool,
    memory: str | None = None,
    documents: Mapping[str, SentDocument] | None = None,
    lean: bool = False,
) -> dict[str, Any]:
    """The body of `POST /v1/chat/completions` for the next answer.

    `memory` is the block of the person's notes (`memory.py`). It goes in the
    one system message, after the chat's instructions and never in a message
    of its own: Qwen templates refuse a second system message, and one that
    changed each turn would start every turn's prompt differently and lose
    the engine's cache.

    `documents` says how each attached document goes (by file id): its block of
    text, or the PDF itself. With `lean`, no file is read and no document is
    included: a part that is not text is a stub, and the request is only good for
    counting its tokens (`context.count_messages`)."""
    by_id = {r.id: r for r in records}
    messages: list[dict[str, Any]] = []
    instructions = str(chat.settings.get("instructions") or "").strip()
    system = system_text(instructions, memory)
    if system:
        messages.append({"role": "system", "content": system})
    for message in history:
        if message.role == "user":
            messages.append(
                {
                    "role": "user",
                    "content": _user_content(message, by_id, root, documents, lean),
                }
            )
        elif message.role == "assistant":
            messages.extend(transcript(message.tool_rounds))
            answer = (
                message.content[message.answer_from or 0 :].lstrip()
                if message.tool_rounds
                else answer_text(message)
            )
            if answer:
                messages.append({"role": "assistant", "content": answer})
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True, "include_progress": True},
    }
    for ours, theirs in SAMPLING.items():
        value = chat.settings.get(ours)
        if value is not None:
            body[theirs] = value
    if search:
        body["web_search_options"] = {}
    if chat.settings.get("repetitionMode") is not None:
        # Hub turns this internal option into the public request header.
        body["_repetition_mode"] = chat.settings["repetitionMode"]
    return body
