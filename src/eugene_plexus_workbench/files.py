"""Attachments: kept as files, per person, by id (W7).

The database holds an attachment's name, type, size and owner; the bytes
are at `files/<person>/<id>` in the data directory, and are read when a
turn is sent. The person's directory is named by a hash of their `sub`,
which is the provider's string and may hold anything.

**What travels is limited by the gateway** (`common.yaml` `MessageContent`),
said here before an upload rather than as a refusal after one: an image at
most 5 MiB, audio or a PDF sent as a file at most 10 MiB, and the media in a
chat together at most 11 MiB, because the whole conversation travels in each
request.

**A document is read, not sent** (MR2): a PDF, Word, Markdown, text, CSV, HTML
or code file is turned into text (`knowledge/extract.py`) and the text goes in
the prompt, so any model can answer from it. Its bytes never travel, so its
limit is what Workbench will open (`DOCUMENT_LIMIT`), not the gateway's. A PDF
is also sent as the file itself, to a model that reads PDFs, when it fits what
the gateway carries.

**A file's type is what its bytes say** (`detect`), never what its name or the
browser says: a PDF is `%PDF-`, a Word file is a ZIP holding `word/document.xml`,
an image or audio file has its own first bytes, and anything else is text if it
reads as text. The name only picks among kinds of text (Markdown, CSV, HTML,
code), and a type the browser declares that the bytes do not bear out is refused.
"""

from __future__ import annotations

import codecs
import hashlib
import struct
from dataclasses import dataclass
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

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TSV = "text/tab-separated-values"
#: The type stored for each kind of text file (`knowledge.extract.KINDS`).
TEXT_TYPES = {
    "markdown": "text/markdown",
    "csv": "text/csv",
    "html": "text/html",
    "code": "text/plain",
    "text": "text/plain",
}
#: Every type Workbench reads as a document.
DOCUMENT_TYPES = frozenset({PDF, DOCX, TSV, *TEXT_TYPES.values()})
#: What Workbench opens of one document, in bytes. Its bytes never travel to a
#: model (its text does), so this is not the gateway's limit.
DOCUMENT_LIMIT = 50 * MIB
#: How much of a file its type is judged from.
HEAD_BYTES = 8192

#: Said whenever a file is refused, so the person knows what is taken.
READS = (
    "Workbench reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files, and sends "
    "PNG or JPEG images and WAV or MP3 audio to models that take them."
)

#: A type a browser may declare that its bytes must bear out. A declared type
#: that is only a kind of text (`text/plain`, `text/markdown`...) is the file
#: name's guess, and the bytes overrule it; these are claims about a format.
_CLAIMS = frozenset({*KINDS, DOCX})


class Unsupported(Exception):
    """A file Workbench neither reads nor sends. `message` says what it is, and
    what Workbench does take."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Detected:
    """What a file's bytes say it is."""

    media_type: str
    #: `image` or `audio` (carried as they are), or the kind `knowledge.extract`
    #: reads it as: `pdf`, `docx`, `markdown`, `csv`, `html`, `code` or `text`.
    kind: str
    #: A programming or data language, from the file's name (shown to a model).
    language: str | None = None
    #: A ZIP, taken to be a Word file until `zip_holds_document` says it is one.
    zipped: bool = False

    @property
    def is_document(self) -> bool:
        return self.media_type in DOCUMENT_TYPES


#: First bytes that name a format Workbench does not read: (bytes, offset, what it is).
_MAGIC: tuple[tuple[bytes, int, str], ...] = (
    (b"GIF87a", 0, "a GIF image"),
    (b"GIF89a", 0, "a GIF image"),
    (b"II*\x00", 0, "a TIFF image"),
    (b"MM\x00*", 0, "a TIFF image"),
    (b"\x1f\x8b", 0, "a gzip archive"),
    (b"7z\xbc\xaf\x27\x1c", 0, "a 7-Zip archive"),
    (b"Rar!\x1a\x07", 0, "a RAR archive"),
    (b"\xfd7zXZ\x00", 0, "an XZ archive"),
    (b"\x7fELF", 0, "a program"),
    (b"\xcf\xfa\xed\xfe", 0, "a program"),
    (b"\xca\xfe\xba\xbe", 0, "a program"),
    (b"SQLite format 3\x00", 0, "an SQLite database"),
    (b"OggS", 0, "an Ogg audio or video file"),
    (b"fLaC", 0, "a FLAC audio file"),
    (b"\x1a\x45\xdf\xa3", 0, "a WebM or Matroska video file"),
    (b"ftyp", 4, "an MP4 video or audio file"),
    (b"{\\rtf", 0, "an RTF document"),
    (b"%!PS", 0, "a PostScript file"),
    (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        0,
        "an older Microsoft Office file (.doc, .xls or .ppt) or a password-protected Office file",
    ),
)

_MARKDOWN = (".md", ".markdown", ".mdown", ".mkd")
_HTML = (".html", ".htm", ".xhtml")
#: Programming languages by file extension (the `language` a model is told).
CODE: dict[str, str] = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala", ".groovy": "groovy",
    ".gradle": "groovy", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".cs": "csharp", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".php": "php", ".swift": "swift", ".m": "objective-c", ".mm": "objective-c",
    ".dart": "dart", ".lua": "lua", ".pl": "perl", ".pm": "perl", ".r": "r", ".jl": "julia",
    ".hs": "haskell", ".ex": "elixir", ".exs": "elixir", ".erl": "erlang", ".clj": "clojure",
    ".cljs": "clojure", ".fs": "fsharp", ".fsx": "fsharp", ".vb": "vbnet", ".sh": "shell",
    ".bash": "shell", ".zsh": "shell", ".fish": "shell", ".ps1": "powershell",
    ".psm1": "powershell", ".bat": "batch", ".cmd": "batch", ".sql": "sql", ".css": "css",
    ".scss": "scss", ".sass": "sass", ".less": "less", ".vue": "vue", ".svelte": "svelte",
    ".proto": "protobuf", ".graphql": "graphql", ".tf": "terraform", ".cmake": "cmake",
    ".nim": "nim", ".zig": "zig", ".sol": "solidity", ".asm": "assembly", ".s": "assembly",
}  # fmt: skip
#: Data and configuration formats: read as text, and named to the model.
DATA: dict[str, str] = {
    ".json": "json", ".jsonl": "json", ".ndjson": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".conf": "ini", ".properties": "properties",
    ".xml": "xml", ".svg": "xml", ".xsl": "xml",
}  # fmt: skip
#: Files that are code by their whole name.
CODE_NAMES = {
    "dockerfile": "dockerfile",
    "makefile": "make",
    "cmakelists.txt": "cmake",
    "rakefile": "ruby",
    "gemfile": "ruby",
    "jenkinsfile": "groovy",
}

_CONTROLS = bytes([*range(0, 9), *range(14, 27), *range(28, 32)])


def declared_type(declared: str | None) -> str:
    """The media type a browser declared, without its parameters, lower case."""
    found = (declared or "").split(";")[0].strip().lower()
    return ALIASES.get(found, found)


def _extension(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return "." + base.rsplit(".", 1)[-1].lower() if "." in base.strip(".") else ""


def _binary(head: bytes) -> bool:
    """Whether the first bytes are not text: a NUL (but a UTF-16 byte-order
    mark was recognised before this), or more than one byte in fifty a control."""
    if b"\x00" in head:
        return True
    return bool(head) and len(head.translate(None, delete=_CONTROLS)) < len(head) * 0.98


def text_kind(name: str, head: bytes) -> Detected:
    """Which kind of text a file is: its name picks among Markdown, CSV, HTML,
    code and the rest, and a page that starts like HTML is HTML whatever it is called."""
    extension = _extension(name)
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if extension in _MARKDOWN:
        return Detected(TEXT_TYPES["markdown"], "markdown")
    if extension == ".csv":
        return Detected(TEXT_TYPES["csv"], "csv")
    if extension in (".tsv", ".tab"):
        return Detected(TSV, "csv")
    start = head.lstrip(codecs.BOM_UTF8 + b" \t\r\n")[:15].lower()
    if extension in _HTML or start.startswith((b"<!doctype html", b"<html")):
        return Detected(TEXT_TYPES["html"], "html")
    language = CODE.get(extension) or CODE_NAMES.get(base)
    if language is not None:
        return Detected(TEXT_TYPES["code"], "code", language)
    return Detected(TEXT_TYPES["text"], "text", DATA.get(extension))


def _sniff(head: bytes, name: str) -> Detected:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Detected("image/png", "image")
    if head.startswith(b"\xff\xd8\xff"):
        return Detected("image/jpeg", "image")
    if head.startswith(b"%PDF-"):
        return Detected(PDF, "pdf")
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return Detected("audio/wav", "audio")
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return Detected(DOCX, "docx", zipped=True)
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        raise Unsupported(f"This file is a WebP image. {READS}")
    for magic, offset, what in _MAGIC:
        if head[offset : offset + len(magic)] == magic:
            raise Unsupported(f"This file is {what}. {READS}")
    # A UTF-16 byte-order mark is also what an MPEG audio frame's first two
    # bytes look like, so text that has one is recognised before audio is.
    marked = head.startswith((codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))
    if not marked:
        if head.startswith(b"ID3") or (
            len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0
        ):
            return Detected("audio/mpeg", "audio")
        if _binary(head):
            ext = _extension(name)
            of = f" ({ext})" if ext else ""
            raise Unsupported(f"This file looks like a binary file, not text{of}. {READS}")
    return text_kind(name, head)


def _what(media_type: str) -> str:
    return {
        "image/png": "a PNG image",
        "image/jpeg": "a JPEG image",
        PDF: "a PDF",
        DOCX: "a Word (.docx) file",
        "audio/wav": "WAV audio",
        "audio/mpeg": "MP3 audio",
    }.get(media_type, media_type)


def _starts(head: bytes) -> str:
    """The first words of a text file, for saying what a file really is."""
    shown = head[:60].decode("utf-8", errors="replace")
    return " ".join("".join(c if c.isprintable() else " " for c in shown).split())


def detect(head: bytes, declared: str | None, name: str) -> Detected:
    """What a file is, from its first bytes (`HEAD_BYTES` of them) and its name.

    Raises `Unsupported` for what Workbench neither reads nor sends, and for a
    file whose bytes do not bear out the format the browser declared for it: a
    web page saved as `report.pdf` is refused as what it is, with how it
    starts, rather than failing later in a reader."""
    found = _sniff(head, name)
    claimed = declared_type(declared)
    if claimed in _CLAIMS and claimed != found.media_type:
        if found.zipped:
            looks = "a ZIP archive"
        elif found.is_document and found.media_type not in (PDF, DOCX):
            looks = f"text (it starts: {_starts(head)})" if head else "empty"
        else:
            looks = _what(found.media_type)
        raise Unsupported(
            f"This file says it is {_what(claimed)}, but its bytes are {looks}. {READS}"
        )
    return found


def zip_holds_document(path: Path) -> bool | None:
    """Whether a ZIP file holds a Word document (`word/document.xml`), by reading
    its central directory under hard bounds, never by opening it: at most
    4,096 entries and 4 MiB of directory are looked at. None when the file
    cannot be told this way (ZIP64, or a directory past the bounds), and the
    reader, which checks the whole package, decides."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            tail = min(size, 66_000)
            handle.seek(size - tail)
            data = handle.read(tail)
            end = data.rfind(b"PK\x05\x06")
            if end < 0 or len(data) - end < 22:
                return False
            entries, length, offset = struct.unpack_from("<HII", data, end + 10)
            if entries == 0xFFFF or length == 0xFFFFFFFF or offset == 0xFFFFFFFF:
                return None
            if length > 4 * MIB or offset + length > size:
                return None
            handle.seek(offset)
            directory = handle.read(length)
    except (OSError, struct.error):
        return None  # cannot be told this way, and the reader will say
    at = 0
    for _ in range(4096):
        if at + 46 > len(directory) or directory[at : at + 4] != b"PK\x01\x02":
            break
        name_length, extra, comment = struct.unpack_from("<HHH", directory, at + 28)
        if directory[at + 46 : at + 46 + name_length] == b"word/document.xml":
            return True
        at += 46 + name_length + extra + comment
    else:
        return None
    return False


def not_word(name: str) -> Unsupported:
    ext = _extension(name)
    shown = f" ({ext})" if ext else ""
    return Unsupported(
        f"This file is a ZIP archive{shown}, not a Word (.docx) file. Excel, PowerPoint and "
        f"other zip-based files are not read. {READS}"
    )


def upload_limit(found: Detected, documents: int = DOCUMENT_LIMIT) -> int:
    """The most bytes Workbench takes of a file like this: the gateway's limit for
    an image or audio, which travel, and `documents` for a document, which is read."""
    if found.kind in ("image", "audio"):
        return LIMITS[found.kind]
    return documents


def travels(media_type: str) -> bool:
    """Whether a file of this type is counted against what one chat carries in
    its requests (`CHAT_LIMIT`): images and audio always. A PDF counts only
    when a model takes it as a file, which is decided when it is sent."""
    return KINDS.get(media_type) in ("image", "audio")


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


def sniff_video(head: bytes) -> str | None:
    """A video's type from its first bytes, or None. Every video door here
    answers an MP4 (P5, measured), whose first box is `ftyp`."""
    return "video/mp4" if head[4:8] == b"ftyp" else None


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
        "video/mp4": "mp4",
    }.get(media_type, "bin")


def person_dir(root: Path, sub: str) -> Path:
    return root / "files" / hashlib.sha256(sub.encode("utf-8")).hexdigest()[:32]


def path_of(root: Path, sub: str, file_id: str) -> Path:
    return person_dir(root, sub) / file_id


def read_path_of(root: Path, sub: str, file_id: str) -> Path:
    """Where the text read from a document is kept: beside the original, in the
    same person's directory, so it is the same person's and goes with it."""
    return person_dir(root, sub) / f"{file_id}.read.json"


def remove_stored(root: Path, sub: str, file_id: str) -> None:
    """A file's bytes, and the text read from it, off the disk."""
    path_of(root, sub, file_id).unlink(missing_ok=True)
    read_path_of(root, sub, file_id).unlink(missing_ok=True)


def describe_limit(kind: str) -> str:
    return f"{LIMITS[kind] // MIB} MiB"


def describe_size(size: int) -> str:
    """A size as a person reads it: `312 MiB`, `4.5 MiB`, `800 KiB`."""
    if size >= MIB:
        value = size / MIB
        return f"{value:.0f} MiB" if value >= 10 or value == int(value) else f"{value:.1f} MiB"
    return f"{max(1, round(size / 1024))} KiB"
