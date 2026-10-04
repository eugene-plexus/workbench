"""A chat, as the request the gateway is sent for its next answer.

The whole conversation travels in each request, as with any OpenAI
client: the chat's instructions as a `system` message, then every turn.
A user turn with attachments becomes content parts in OpenAI's shapes
(`common.yaml` `MessageContentPart`): an image as a `data:` URL, a PDF as a
file part, audio as `input_audio`. An assistant turn is its answer only:
text it wrote before its last web search is a draft and is not sent back
(workbench#1). An answer that failed before it said anything is left out,
because the model never said it.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from . import files
from .answers import answer_text
from .store import Chat, FileRecord, Message
from .tools import transcript

#: What a chat's settings may hold, and the gateway's name for each.
SAMPLING = {"temperature": "temperature", "topP": "top_p", "maxTokens": "max_tokens"}


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


def _user_content(
    message: Message, records: dict[str, FileRecord], root: Path
) -> str | list[dict[str, Any]]:
    attached = [records[i] for i in message.attachments if i in records]
    if not attached:
        return message.content
    parts: list[dict[str, Any]] = []
    if message.content:
        parts.append({"type": "text", "text": message.content})
    for record in attached:
        data = files.path_of(root, record.owner, record.id).read_bytes()
        parts.append(_part(record, data))
    return parts


def request_for(
    chat: Chat,
    history: list[Message],
    records: list[FileRecord],
    *,
    root: Path,
    model: str,
    search: bool,
) -> dict[str, Any]:
    """The body of `POST /v1/chat/completions` for the next answer."""
    by_id = {r.id: r for r in records}
    messages: list[dict[str, Any]] = []
    instructions = str(chat.settings.get("instructions") or "").strip()
    if instructions:
        messages.append({"role": "system", "content": instructions})
    for message in history:
        if message.role == "user":
            messages.append({"role": "user", "content": _user_content(message, by_id, root)})
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
