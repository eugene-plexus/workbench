"""Bounded text operations on held file handles, never a checked-then-opened path.

Linux uses Landlock and one-component no-link traversal on held handles. Windows
walks one name at a time relative to retained directory handles, refusing
reparse points and holding parents against rename. Only regular, singly
linked files are used. No shell and no automatic retries.

The model's tools (`operate`) name one path at a time. Saved documents (MR7) read a
whole folder, read-only: `scan` lists a folder's tree through the same handles and
`read_file` copies one file out of it, each a single call that opens the folder anew,
checks its pinned identity, and (on Linux) runs under a read-only Landlock domain. A walk
never follows a link: a listing says what an entry is, and an open that finds a link
anyway (swapped in since the listing) is refused.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

MAX_BYTES = 32_768
MAX_CHARACTERS = 16_384
MAX_ENTRIES = 200


class FolderError(Exception):
    """Safe to return to the person and model; contains no host path.

    `status` is the HTTP status Eugene answered with, when this is its refusal. `code`
    names why a file or folder was refused, for a reader that says it in its own words:
    `link` (a symlink, junction or other reparse point), `mount` (another disk is mounted
    there), `hard_links`, `special` (not an ordinary file), `not_disk`. `cause` is the
    operating system's own error when this was made from one (`Folders._io`)."""

    def __init__(
        self,
        message: str = "",
        status: int | None = None,
        *,
        code: str | None = None,
        cause: OSError | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.cause = cause


class TooLarge(FolderError):
    """A file passed the most a read may take, as it was opened or as it was read."""

    def __init__(self, size: int) -> None:
        super().__init__("This file is larger than a read may take.", code="too_large")
        self.size = size


class ChangedWhileReading(FolderError):
    """A file's size or modified time moved while it was being read: what was read may be
    two versions, so none of it is kept."""

    def __init__(self) -> None:
        super().__init__("The file changed while it was being read.", code="changed")


class WriteUncertain(FolderError):
    """A write began but its completion could not be established."""


def _utf8(text: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        raise FolderError("File names and contents must be valid UTF-8 text.") from None


def parts(path: str, *, directory: bool = False) -> list[str]:
    if directory and path == ".":
        return []
    names = path.split("/")
    if (
        not path
        or len(path) > 1024
        or len(names) > 32
        or any(
            not n
            or n in {".", ".."}
            or n.endswith((".", " "))
            or len(_utf8(n)) > 255
            or any(ord(c) < 32 or c in '\\:<>"|?*' or ord(c) == 127 for c in n)
            or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?", n)
            for n in names
        )
    ):
        raise FolderError(
            "Use a relative path with / separators, without links, .. or device names."
        )
    return names


def identity(fd: int) -> str:
    info = os.fstat(fd)
    if sys.platform == "win32":
        created = str(info.st_birthtime_ns)
    else:
        from .folder_linux import birth_time

        created = birth_time(fd)
    return f"{info.st_dev}:{info.st_ino}:{created}"


def regular(fd: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise FolderError("Only ordinary files with one hard link are supported.", code="special")
    if info.st_nlink != 1:
        raise FolderError(
            "Only ordinary files with one hard link are supported.", code="hard_links"
        )


def text_bytes(text: str) -> bytes:
    if any(ord(c) < 32 and c not in "\t\r\n" for c in text):
        raise FolderError("Only UTF-8 text files without binary control characters are supported.")
    data = _utf8(text)
    if len(data) > MAX_BYTES or len(text) > MAX_CHARACTERS:
        raise FolderError("Use a text file of at most 32 KiB and 16384 characters.")
    return data


def _read(fd: int) -> tuple[str, str]:
    regular(fd)
    if os.fstat(fd).st_size > MAX_BYTES:
        raise FolderError("Use a text file of at most 32 KiB and 16384 characters.")
    os.lseek(fd, 0, os.SEEK_SET)
    data = bytearray()
    while len(data) <= MAX_BYTES:
        chunk = os.read(fd, min(8192, MAX_BYTES + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FolderError("This is not a UTF-8 text file.") from None
    text_bytes(text)
    return text, hashlib.sha256(data).hexdigest()


@contextmanager
def root(path: str, expected: str | None = None) -> Iterator[Any]:
    if sys.platform == "win32":
        from .folder_windows import Root
    elif sys.platform.startswith("linux"):
        from .folder_linux import Root
    else:
        raise FolderError("Folder tools require Windows or Linux with Landlock support.")
    with Root(path) as folder:
        if expected is not None and identity(folder.fd) != expected:
            raise FolderError(
                "This folder was replaced. Ask the owner to remove and grant it again.",
                code="replaced",
            )
        yield folder


def inspect(path: str, protected: list[Path]) -> str:
    with root(path) as folder:
        check_root_path(folder.path, protected)
        return identity(folder.fd)


def operate(
    path: str, expected: str, tool: str, arguments: dict[str, Any], protected: list[Path]
) -> dict[str, Any]:
    if sys.platform.startswith("linux"):
        from .folder_linux import isolated

        return isolated(lambda: _operate(path, expected, tool, arguments, protected))
    return _operate(path, expected, tool, arguments, protected)


def _operate(
    path: str, expected: str, tool: str, arguments: dict[str, Any], protected: list[Path]
) -> dict[str, Any]:
    names = parts(arguments["path"], directory=tool == "list_directory")
    with root(path, expected) as folder:
        check_root_path(folder.path, protected)
        if sys.platform.startswith("linux"):
            folder.restrict(tool == "write_text")
        if tool == "list_directory":
            entries = folder.names(names, MAX_ENTRIES + 1)
            for entry in entries:
                _utf8(entry)
            return {"names": sorted(entries[:MAX_ENTRIES]), "truncated": len(entries) > MAX_ENTRIES}
        if tool == "read_text":
            with folder.file(names) as fd:
                text, digest = _read(fd)
            return {"text": text, "sha256": digest}
        if tool != "write_text":
            raise FolderError("This file tool is not supported.")
        data = text_bytes(arguments["text"])
        before = arguments["expectedSha256"]
        # Empty expected hash means create-only; a SHA-256 means edit exactly
        # the version the person approved. Never truncate while opening.
        with folder.file(names, write=True, create=before == "") as fd:
            regular(fd)
            if before:
                _, digest = _read(fd)
                if digest != before:
                    raise FolderError(
                        "The file changed. Read it again before proposing another edit."
                    )
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                pending = memoryview(data)
                while pending:
                    written = os.write(fd, pending)
                    if written == 0:
                        raise OSError("The write made no progress.")
                    pending = pending[written:]
                os.ftruncate(fd, len(data))
                os.fsync(fd)
            except OSError:
                raise WriteUncertain(
                    "The write did not finish reliably. The file may be partially "
                    "changed. Check it before trying again."
                ) from None
        return {"written": len(data), "sha256": hashlib.sha256(data).hexdigest()}


# --------------------------------------------------------------------------- #
# reading a whole folder, read-only (MR7)
# --------------------------------------------------------------------------- #

FILE, DIRECTORY, LINK, OTHER = "file", "dir", "link", "other"
#: The reparse tag of a cloud-sync placeholder (OneDrive, Dropbox); its family is this
#: with a number in the third nibble (`IO_REPARSE_TAG_CLOUD_1` and on). A symlink and a
#: junction send a path somewhere else. A placeholder is a file whose bytes are fetched
#: when it is read, so reading one would download it.
CLOUD_TAG = 0x9000001A
_REPARSE, _DIRECTORY, _HIDDEN, _SYSTEM = 0x400, 0x10, 0x2, 0x4
_OFFLINE, _RECALL_ON_OPEN, _RECALL_ON_DATA = 0x1000, 0x40000, 0x400000
_FILETIME_UNIX = 116_444_736_000_000_000
#: A copy is made a block at a time.
COPY_BLOCK = 1024 * 1024


@dataclass(frozen=True)
class Entry:
    """What a directory listing says of one name, without opening it. `kind` is `file`, `dir`,
    `link` (never entered or read) or `other` (a pipe, a device)."""

    name: str
    kind: str
    size: int = 0
    mtime_ns: int = 0
    #: Windows' hidden or system attribute. (A name with a dot in front is the caller's to judge.)
    hidden: bool = False
    #: A reparse point's tag (Windows); 0 for anything else.
    tag: int = 0

    @property
    def cloud(self) -> bool:
        """A cloud-sync placeholder: kept online, fetched when read."""
        return self.tag != 0 and (self.tag & 0xFFFF0FFF) == CLOUD_TAG


def entry_from_attributes(name: str, attributes: int, size: int, written: int, tag: int) -> Entry:
    """A Windows directory entry: its attribute bits, its size, its last-write FILETIME and, for a
    reparse point, its tag. Any reparse point is a `link` (not followed), and so is a file whose
    bytes are not on this machine yet; a directory is entered only when it is neither."""
    hidden = bool(attributes & (_HIDDEN | _SYSTEM))
    mtime = (written - _FILETIME_UNIX) * 100
    if attributes & _REPARSE:
        return Entry(name, LINK, 0, mtime, hidden, tag)
    if attributes & _DIRECTORY:
        return Entry(name, DIRECTORY, 0, mtime, hidden)
    if attributes & (_OFFLINE | _RECALL_ON_OPEN | _RECALL_ON_DATA):
        return Entry(name, LINK, 0, mtime, hidden, CLOUD_TAG)
    return Entry(name, FILE, size, mtime, hidden)


class Tree(Protocol):
    """A folder as a walk sees it: the entries of a directory (named by its path of names below
    the folder, `[]` for the folder itself) and the bytes of a small file."""

    def entries(self, names: list[str], limit: int) -> list[Entry]: ...

    def read_small(self, names: list[str], limit: int) -> bytes: ...


class _Tree:
    def __init__(self, folder: Any) -> None:
        self.folder = folder

    def entries(self, names: list[str], limit: int) -> list[Entry]:
        found: list[Entry] = self.folder.entries(names, limit)
        return found

    def read_small(self, names: list[str], limit: int) -> bytes:
        with self.folder.file(names, shared=True) as fd:
            regular(fd)
            if os.fstat(fd).st_size > limit:
                raise TooLarge(os.fstat(fd).st_size)
            data = bytearray()
            while len(data) <= limit:
                block = os.read(fd, min(65536, limit + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            if len(data) > limit:
                raise TooLarge(len(data))
            return bytes(data)


def scan[T](path: str, expected: str, protected: list[Path], walker: Callable[[Tree], T]) -> T:
    """Run `walker` over a folder's tree: one call that opens the folder, checks it is the one that
    was granted (`expected`), and holds its parents. On Linux the thread is under a Landlock domain
    that allows reading beneath the folder and nothing else, so `walker` can only read, and
    can import nothing that is not imported already. It never gets a path, only the `Tree`."""

    def run() -> T:
        with root(path, expected) as folder:
            check_root_path(folder.path, protected)
            if sys.platform.startswith("linux"):
                folder.restrict(False)
            return walker(_Tree(folder))

    if sys.platform.startswith("linux"):
        from .folder_linux import isolated

        return isolated(run)
    return run()


@dataclass(frozen=True)
class Declined:
    """A file looked at and not taken, for a reason its first bytes gave."""

    code: str
    message: str
    detail: str | None = None


@dataclass(frozen=True)
class Taken:
    """A file copied out of a folder: its size, the SHA-256 of what was copied, its modified time
    as the open file said it before and after, and its first bytes."""

    size: int
    sha256: str
    mtime_ns: int
    head: bytes


def read_file(
    path: str,
    expected: str,
    protected: list[Path],
    names: list[str],
    dest_fd: int,
    limit: int,
    head_size: int,
    head_check: Callable[[bytes, int], Declined | None],
) -> Taken | Declined:
    """Copy one file of a folder to the open file `dest_fd` (opened by the caller, for writing:
    nothing is created or written in the folder), hashing it as it goes. The first `head_size`
    bytes are shown to `head_check` before the rest is read, and a `Declined` from it ends the
    read with nothing copied. A file over `limit` bytes (by its size, or as it is read: it
    can grow) raises `TooLarge`; one that changes while it is read raises `ChangedWhileReading`;
    a link, a hard link or a special file is refused by the handle. The file is opened so that other
    programs may still write, rename and delete it."""

    def run() -> Taken | Declined:
        with root(path, expected) as folder:
            check_root_path(folder.path, protected)
            if sys.platform.startswith("linux"):
                folder.restrict(False)
            with folder.file(names, shared=True) as fd:
                regular(fd)
                before = os.fstat(fd)
                if before.st_size > limit:
                    raise TooLarge(before.st_size)
                head = b""
                while len(head) < head_size:
                    block = os.read(fd, head_size - len(head))
                    if not block:
                        break
                    head += block
                verdict = head_check(head, before.st_size)
                if verdict is not None:
                    return verdict
                digest = hashlib.sha256(head)
                _write_all(dest_fd, head)
                total = len(head)
                while True:
                    block = os.read(fd, COPY_BLOCK)
                    if not block:
                        break
                    total += len(block)
                    if total > limit:
                        raise TooLarge(total)
                    digest.update(block)
                    _write_all(dest_fd, block)
                after = os.fstat(fd)
                if (
                    after.st_size != before.st_size
                    or after.st_mtime_ns != before.st_mtime_ns
                    or total != after.st_size
                ):
                    raise ChangedWhileReading
                return Taken(total, digest.hexdigest(), before.st_mtime_ns, head)

    if sys.platform.startswith("linux"):
        from .folder_linux import isolated

        return isolated(run)
    return run()


def _write_all(fd: int, data: bytes) -> None:
    pending = memoryview(data)
    while pending:
        written = os.write(fd, pending)
        if written == 0:
            raise OSError("The copy made no progress.")
        pending = pending[written:]


def check_root_path(path: str, protected: list[Path]) -> str:
    candidate = Path(path)
    if not candidate.is_absolute() or "\x00" in path or ".." in candidate.parts:
        raise FolderError("Use the full path to an existing folder on Workbench's host.")
    # Lexical overlap is checked before opening. Native traversal rejects
    # aliases through symlinks/reparse points; identity is pinned separately.
    for private in protected:
        for boundary in (private.absolute(), private.resolve()):
            if candidate.is_relative_to(boundary) or boundary.is_relative_to(candidate):
                raise FolderError("Workbench's private files and installation cannot be granted.")
    return str(candidate)
