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


#: What an image in a bin may be (workbench-media-screens.md §2.8): what a
#: chat carries, plus WebP, which a model makes when its listing offers it.
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")
#: A file brought into a bin to use as a reference image.
UPLOAD_LIMIT = 10 * MIB
#: Reference images in one request, together. The gateway takes 25 MiB of
#: JSON on its edits door, and base64 makes 3 bytes 4.
REFERENCES_LIMIT = 18 * MIB


def sniff_image(data: bytes) -> str | None:
    """An image's type from its first bytes, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """An image's own width and height, read from its bytes; None when they
    cannot be read. What came back is shown beside what was asked (§2.4)."""
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data.startswith(b"\xff\xd8"):
            return _jpeg_size(data)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return _webp_size(data)
    except (IndexError, ValueError):
        return None
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    at = 2
    while at + 9 < len(data):
        if data[at] != 0xFF:
            at += 1
            continue
        marker = data[at + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            at += 2
            continue
        length = int.from_bytes(data[at + 2 : at + 4], "big")
        # Start of frame, every kind but the DHT/JPG/DAC markers among them.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[at + 5 : at + 7], "big")
            width = int.from_bytes(data[at + 7 : at + 9], "big")
            return width, height
        at += 2 + length
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8X":
        return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    if chunk == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 ":
        return (
            int.from_bytes(data[26:28], "little") & 0x3FFF,
            int.from_bytes(data[28:30], "little") & 0x3FFF,
        )
    return None


#: What audio in a bin may be (§2.8, slice 2): what speech models make
#: (mp3, opus in Ogg, aac, flac, wav) and what a recording or an upload to
#: transcribe is (Chrome records WebM/Opus; phones record MP4/AAC).
AUDIO_TYPES = (
    "audio/mpeg", "audio/wav", "audio/ogg", "audio/flac", "audio/aac", "audio/webm", "audio/mp4",
)  # fmt: skip
#: Audio sent to be transcribed: the gateway's own limit on that door.
TRANSCRIBE_LIMIT = 25 * MIB


def sniff_audio(data: bytes) -> str | None:
    """An audio file's type from its first bytes, or None."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data.startswith(b"fLaC"):
        return "audio/flac"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm"
    if data[4:8] == b"ftyp":
        return "audio/mp4"
    if data.startswith(b"ID3"):
        return "audio/mpeg"
    if len(data) > 1 and data[0] == 0xFF:
        # ADTS (AAC) has layer bits 00; an MPEG audio frame never does.
        if data[1] & 0xF6 == 0xF0:
            return "audio/aac"
        if data[1] & 0xE0 == 0xE0 and (data[1] >> 1) & 0x03:
            return "audio/mpeg"
    return None


def extension(media_type: str) -> str:
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
        "audio/ogg": "ogg",
        "audio/flac": "flac",
        "audio/aac": "aac",
        "audio/webm": "webm",
        "audio/mp4": "m4a",
    }.get(media_type, "bin")


def person_dir(root: Path, sub: str) -> Path:
    return root / "files" / hashlib.sha256(sub.encode("utf-8")).hexdigest()[:32]


def path_of(root: Path, sub: str, file_id: str) -> Path:
    return person_dir(root, sub) / file_id


def describe_limit(kind: str) -> str:
    return f"{LIMITS[kind] // MIB} MiB"
