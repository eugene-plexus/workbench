"""Attachments: kept as files, per person, by id (W7).

The database holds an attachment's name, type, size and owner; the bytes
are at `files/<person>/<id>` in the data directory, and are read when a
turn is sent. The person's directory is named by a hash of their `sub`,
which is the provider's string and may hold anything.

**The limits are the gateway's** (`common.yaml` `MessageContent`), said
here before an upload rather than as a refusal after one: an image at
most 5 MiB, audio or a PDF at most 10 MiB, and every attachment in a chat
together at most 11 MiB, because the whole conversation travels in each
request.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

MIB = 1024 * 1024
#: What the gateway carries (P2a), by the type Workbench stores.
KINDS: dict[str, str] = {
    "image/png": "image",
    "image/jpeg": "image",
    "application/pdf": "file",
    "audio/wav": "audio",
    "audio/mpeg": "audio",
}
#: Browsers name these differently; they are the same files.
ALIASES = {"audio/x-wav": "audio/wav", "audio/wave": "audio/wav", "audio/mp3": "audio/mpeg"}
LIMITS = {"image": 5 * MIB, "file": 10 * MIB, "audio": 10 * MIB}
CHAT_LIMIT = 11 * MIB


def media_type(declared: str | None, head: bytes) -> str | None:
    """The type to store, checked against the file's first bytes, or None.

    A browser's word for a type is the file name's; the gateway checks the
    bytes, so this does too, and says so before an upload rather than
    after a send.
    """
    declared = (declared or "").split(";")[0].strip().lower()
    declared = ALIASES.get(declared, declared)
    sniffed: str | None = None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        sniffed = "image/png"
    elif head.startswith(b"\xff\xd8\xff"):
        sniffed = "image/jpeg"
    elif head.startswith(b"%PDF-"):
        sniffed = "application/pdf"
    elif head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        sniffed = "audio/wav"
    elif head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        sniffed = "audio/mpeg"
    if sniffed is None:
        return None
    if declared and declared in KINDS and declared != sniffed:
        return None
    return sniffed


def person_dir(root: Path, sub: str) -> Path:
    return root / "files" / hashlib.sha256(sub.encode("utf-8")).hexdigest()[:32]


def path_of(root: Path, sub: str, file_id: str) -> Path:
    return person_dir(root, sub) / file_id


def describe_limit(kind: str) -> str:
    return f"{LIMITS[kind] // MIB} MiB"
