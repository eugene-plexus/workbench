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

**A long chat is sent with summaries** (row 26, `long_chats.py`): a run of earlier messages the
plan summarised is left out, and its summary (`Placed`) opens the first user message sent after
it, as a delimited block, the same bytes every turn. Nothing before the run changes, so the
engine's cache of the system message and the first exchange holds; no message is added, so the
roles still alternate. Only a run that follows a person's message with no answer of its own sent
(the first message alone is the head, or an answer that failed) stands in an assistant message
of its own, so that two person's messages never meet.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
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


@dataclass(frozen=True)
class Placed:
    """A summary in a request (row 26): it stands for the path's messages from `first_id` to
    `last_id`, both included, and `block` is what is sent in their place."""

    first_id: str
    last_id: str
    block: str


def _opened(content: str | list[dict[str, Any]], blocks: list[str]) -> Any:
    """A user message's content with summary blocks before everything else in it."""
    lead = "\n\n".join(blocks)
    if isinstance(content, str):
        return f"{lead}\n\n{content}" if content else lead
    parts = list(content)
    if parts and parts[0].get("type") == "text":
        text = str(parts[0].get("text") or "")
        parts[0] = {"type": "text", "text": f"{lead}\n\n{text}" if text else lead}
        return parts
    return [{"type": "text", "text": lead}, *parts]


#: What stands for a part that is not text when only its size in tokens is wanted.
_STUB = {"type": "image_url", "image_url": {"url": ""}}


def _user_content(
    message: Message,
    records: Mapping[str, FileRecord],
    root: Path,
    documents: Mapping[str, SentDocument] | None,
    lean: bool,
    retrieved: str | None = None,
) -> str | list[dict[str, Any]]:
    attached = [records[i] for i in message.attachments if i in records]
    if not attached and not retrieved:
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
    if retrieved:
        blocks.append(retrieved)
    words = [*blocks, message.content] if message.content else blocks
    text = "\n\n".join(words)
    if not media:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    return parts + media


def system_messages(chat: Chat, memory: str | None) -> list[dict[str, Any]]:
    """The request's system message, as a list of none or one: the chat's instructions and the
    person's pinned notes (`memory.system_text`)."""
    instructions = str(chat.settings.get("instructions") or "").strip()
    system = system_text(instructions, memory)
    return [{"role": "system", "content": system}] if system else []


def message_parts(
    message: Message,
    records: Mapping[str, FileRecord],
    *,
    root: Path,
    documents: Mapping[str, SentDocument] | None = None,
    lean: bool = False,
    extra: str | None = None,
    notes: bool = True,
    chats: bool = True,
) -> list[dict[str, Any]]:
    """What one message of the path is in a request: a person's message is one user message
    (with `extra`, this turn's blocks, when it is the one being answered); an answer is its tool
    rounds, with what a kept `search_memory` (`notes`) or `search_chats` (`chats`) result may
    carry, then its words. An answer that said nothing is nothing."""
    if message.role == "user":
        return [
            {
                "role": "user",
                "content": _user_content(message, records, root, documents, lean, extra or None),
            }
        ]
    if message.role != "assistant":
        return []
    out = list(transcript(message.tool_rounds, notes=notes, chats=chats))
    answer = (
        message.content[message.answer_from or 0 :].lstrip()
        if message.tool_rounds
        else answer_text(message)
    )
    if answer:
        out.append({"role": "assistant", "content": answer})
    return out


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
    retrieved: str | None = None,
    memory_turn: str | None = None,
    server_turn: str | None = None,
    memory_allowed: bool = True,
    past_turn: str | None = None,
    chats_allowed: bool | None = None,
    summaries: Sequence[Placed] | None = None,
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
    counting its tokens (`context.count_messages`).

    `retrieved` is the passages found in the person's saved documents for this
    answer (`context.render_passages`). They go in **the latest user message only**,
    after any attached document and before the person's words, and are never sent
    again: the next request has none in that message, so every earlier turn is the
    bytes it was and the engine's cache is lost only from the last turn on.

    `memory_turn` is the person's notes chosen for this turn (MV1, `memory.render_relevant`),
    placed the same way, before the passages. Without `memory_allowed` (this request may not
    carry notes), a `search_memory` result kept from an earlier turn is withheld.

    `server_turn` is what the person's own memory server returned for this turn (MV5,
    `memory_servers.render`), placed the same way, after the notes.

    `past_turn` is the person's past chats chosen for this turn (MV2, `past_chats.render`),
    after the notes. Without `chats_allowed` (this request may not carry past chats; it
    follows `memory_allowed` when not given), a kept `search_chats` result is withheld.

    `summaries` are the summaries the plan placed (row 26, `long_chats.plan`): each run they
    stand for is left out, and its block opens the next user message sent."""
    by_id = {r.id: r for r in records}
    last_user = history[-1] if history and history[-1].role == "user" else None
    messages = system_messages(chat, memory)
    starts = {p.first_id: p for p in summaries or ()}
    skip_to: str | None = None
    waiting: list[str] = []
    for message in history:
        if skip_to is not None:
            if message.id == skip_to:
                skip_to = None
            continue
        placed = starts.get(message.id)
        if placed is not None:
            if placed.last_id != message.id:
                skip_to = placed.last_id
            if not waiting and messages and messages[-1]["role"] == "user":
                messages.append({"role": "assistant", "content": placed.block})
            else:
                waiting.append(placed.block)
            continue
        sent = message_parts(
            message,
            by_id,
            root=root,
            documents=documents,
            lean=lean,
            extra="\n\n".join(p for p in (memory_turn, server_turn, past_turn, retrieved) if p)
            if message is last_user
            else None,
            notes=memory_allowed,
            chats=memory_allowed if chats_allowed is None else chats_allowed,
        )
        if waiting and sent and sent[0]["role"] == "user":
            sent[0] = {**sent[0], "content": _opened(sent[0]["content"], waiting)}
            waiting = []
        messages.extend(sent)
    if waiting:
        messages.append({"role": "user", "content": "\n\n".join(waiting)})
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
