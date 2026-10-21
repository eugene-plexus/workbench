"""Artifacts (workbench-rich-content-roadmap.md RC5): a document, a picture, a
page or a small React component an answer wrote, opened beside the chat to be
read, run, edited and kept in revisions.

**An answer asks for one in so many words**: a fence whose info string carries
the word `artifact` (```` ```html artifact title="Pricing table" ````) is shown
as an artifact's card; any other closed fence of these languages can be opened
as one by the person (web/src/components/Artifact.tsx). Its language names its
kind: Markdown, SVG, HTML or a React component. Pages and components run when
shown unless the person or chat turns that off, in a frame of their own
(web.py `PREVIEW_CSP`). Tool results use the same frame.

**An artifact is kept when it is first changed.** Until then it is the answer's
own block, revision 1, and nothing is written. Saving an edit keeps the block
as revision 1 (by the answer, when the answer holds it) and the edit as
revision 2 (by the person); each later save is a new revision, and none is
overwritten. Each belongs to the message whose block it was, so another
version of an answer has its own, and an artifact goes with its chat.

**A block is found again by its words**: `digest` of revision 1, the same
number the page computes for each block (web/src/lib/artifacts.ts), so the
page shows a block's saved revisions wherever it is in the answer.
"""

from __future__ import annotations

import struct
from typing import Any

from .store import ArtifactRecord, Message

#: An artifact's kinds, by the fence languages that name them.
KINDS = {
    "markdown": "markdown",
    "md": "markdown",
    "svg": "svg",
    "html": "html",
    "jsx": "react",
    "tsx": "react",
    "react": "react",
}
KIND_NAMES = {"markdown": "Markdown", "svg": "SVG", "html": "HTML", "react": "React"}

#: One revision's source, in UTF-8 bytes.
MAX_SOURCE = 512 * 1024
#: Revisions one artifact keeps.
MAX_REVISIONS = 100
#: Artifacts kept for one message.
MAX_PER_MESSAGE = 20
#: The source a person's artifacts keep, every revision counted.
MAX_PERSON_BYTES = 256 * 1024 * 1024
MAX_TITLE = 120

_M = 0xFFFFFFFF


def digest(source: str) -> str:
    """The number the page computes for a block's words: its length in UTF-16
    code units and cyrb53 over them (web/src/lib/artifacts.ts `digest`)."""
    data = source.encode("utf-16-le", "surrogatepass")
    units = struct.unpack(f"<{len(data) // 2}H", data)
    h1, h2 = 0xDEADBEEF, 0x41C6CE57
    for unit in units:
        h1 = ((h1 ^ unit) * 2654435761) & _M
        h2 = ((h2 ^ unit) * 1597334677) & _M
    h1 = ((h1 ^ (h1 >> 16)) * 2246822507) & _M
    h1 ^= ((h2 ^ (h2 >> 13)) * 3266489909) & _M
    h2 = ((h2 ^ (h2 >> 16)) * 2246822507) & _M
    h2 ^= ((h1 ^ (h1 >> 13)) * 3266489909) & _M
    return f"{len(units)}-{(2097151 & h2) * 4294967296 + h1:x}"


def _holds(original: str, content: str) -> bool:
    """Whether text holds `original`: as written, or as a run of its lines
    in their order once each line's indent is set aside (a fence inside a list
    is indented). Lines found apart, or in another order, are not the answer's."""
    if not original.strip():
        return False
    original = original.replace("\r\n", "\n")
    content = content.replace("\r\n", "\n")
    if original in content:
        return True
    want = [line.strip() for line in original.split("\n")]
    held = [line.strip() for line in content.split("\n")]
    return any(held[at : at + len(want)] == want for at in range(len(held) - len(want) + 1))


def found_in(original: str, message: Message) -> dict[str, str] | None:
    """Find a block in the answer first, then its tools' results, with its origin.

    Only result text counts: never a call's arguments, resource addresses or
    arbitrary structured data. Text resources may be stored as text blocks.
    """
    if _holds(original, message.content):
        return {"origin": "answer"}
    for round_ in message.tool_rounds:
        for call in round_.get("calls") or []:
            texts = [call.get("result")]
            for block in call.get("content") or []:
                if block.get("type") == "text":
                    texts.append(block.get("text"))
                elif block.get("type") == "resource":
                    resource = block.get("resource")
                    if isinstance(resource, dict):
                        texts.append(resource.get("text"))
            if any(isinstance(text, str) and _holds(original, text) for text in texts):
                return {
                    "origin": "tool",
                    "tool": f"{call.get('serverId', '')}/{call.get('tool', '')}",
                }
    return None


def summary(record: ArtifactRecord) -> dict[str, Any]:
    """What a message's view says of an artifact kept for one of its blocks."""
    return {
        "id": record.id,
        "kind": record.kind,
        "title": record.title,
        "digest": record.digest,
        "revisions": record.revisions,
        "createdAt": record.created_at,
        "updatedAt": record.updated_at,
    }


def title_of(title: str, kind: str) -> str:
    """A title as kept: one line, at most `MAX_TITLE` characters, never empty."""
    words = " ".join(title.split())[:MAX_TITLE]
    return words or {"markdown": "Document", "svg": "Picture", "html": "Page"}.get(
        kind, "Component"
    )
