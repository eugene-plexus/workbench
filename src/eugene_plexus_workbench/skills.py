"""Skills (row 32, `specs/docs/design/rows/row-32.md`).

**What a skill is.** The Agent Skills format that Claude Code, Claude's apps and Codex read: a
folder whose `SKILL.md` starts with YAML frontmatter, `name` (lowercase letters, digits and single
hyphens, at most 64 characters) and `description` (at most 1,024 characters), then Markdown
instructions. Its other files (`references/`, `scripts/`, `assets/`) are read on demand. Other
frontmatter keys are kept and shown, never acted on.

**How the model sees them.** Two tools Workbench runs itself, without asking first, as
`search_memory` (they read, and change nothing): `use_skill`, whose description lists each skill as
`name: description`, and `read_skill_file`. The list is in the tool, not the system message, so
memory's pinned block and the prompt cache do not move; it is the same bytes for as long as the
person's skills stay as they are (by name, `bounded`). At most 32 skills and 8,000 characters are
listed; the Skills page says which are not, and why.

**Scripts never run here.** There is no sandbox on the machine Workbench runs on, and a skill's
author is not the person. A script runs only where commands already run: a Job Site's
`run_command`, as the person, under the site's own rules. `use_skill` says so.

**Where they are.** A row each in `skills`, whose `owner` is the person's `sub`, or `install` for a
skill the owner gave everyone (`skill_off` says which of those a person turned off), and the files,
read-only, under `<data>/skills/<person key>/<id>/` (`install/<id>/`). A person's rows go with
their data (`store.PERSON_TABLES`), and their folder with the removal's `skills` step.

**The archive reader is where a stranger's bytes come in.** The whole archive is read and judged
in memory first, and refused, with the rule and the entry, on the first thing wrong: more than 512
entries; a link or any entry that is not a plain file or folder; an absolute path, a drive letter or
`..`; a name Windows cannot hold (a device name, a trailing dot or space, `<>:"|?*`); two names
that are one file where case or Unicode form is not told apart; nesting deeper than 8; an entry
that unpacks to more than 100 times its packed size; more than 20 MiB unpacked; no `SKILL.md` at
the root or in one top folder; frontmatter that does not parse or breaks the rules. Only then are
its files written, to a new folder inside the skills folder, and moved into place.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import io
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import stat
import time
import unicodedata
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath, PurePosixPath
from typing import TYPE_CHECKING, Any

import yaml

from . import files
from .content import MAX_TEXT
from .store import HIDDEN_STATES, Person

if TYPE_CHECKING:
    from .store import Chat, Store

log = logging.getLogger(__name__)

#: The owner of a skill the install's owner gave everyone. Eugene's people have generated ids.
INSTALL = "install"
#: The two tools (`tools.ToolSession.add_skills`).
USE_SKILL = "use_skill"
READ_SKILL_FILE = "read_skill_file"

MIB = files.MIB
MAX_ENTRIES = 512
MAX_UNPACKED = 20 * MIB
#: What an upload may be, packed: the most it unpacks to, with room for the archive's own headers.
MAX_UPLOAD = MAX_UNPACKED + MIB
MAX_RATIO = 100
#: An entry smaller than this is not judged by its ratio: a short file of repeated lines packs
#: far better than 100 to 1 and is no bomb. The 20 MiB limit holds for every entry.
RATIO_FLOOR = 64 * 1024
MAX_DEPTH = 8
MAX_PATH = 200
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_FRONTMATTER = 16 * 1024
#: What `use_skill` returns of the instructions, and `read_skill_file` of a file (the replay
#: limit, `content.MAX_TEXT`).
MAX_BODY = 32_000
MAX_FILE_TEXT = MAX_TEXT
#: What one chat lists.
MAX_LISTED = 32
MAX_LISTING = 8_000
#: What one person keeps (and the install, for the skills it gives everyone).
PER_PERSON = 200
PER_PERSON_BYTES = 50 * MIB

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SKILL_MD = "SKILL.md"
#: What macOS and Windows leave in an archive of a folder, and nobody wrote: left out.
_JUNK = frozenset({"__MACOSX", ".DS_Store", "Thumbs.db", "desktop.ini"})
#: The names Windows keeps for devices, with any extension (`CON.md` is `CON`).
_DEVICES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{kind}{n}" for kind in ("COM", "LPT") for n in "0123456789¹²³"}
)
_FORBIDDEN = frozenset('<>:"|?*')
_SCRIPT_SUFFIXES = frozenset(
    {".py", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".js", ".mjs", ".ts", ".rb", ".pl"}
)
#: Windows' attribute for a reparse point (a link), in an archive made there.
_REPARSE_POINT = 0x400
_HIDDEN = (
    "(SELECT sub FROM removed_people WHERE state IN ("
    + ", ".join(f"'{s}'" for s in HIDDEN_STATES)
    + "))"
)


class Refused(Exception):
    """A skill was not added or changed, for a reason said in words a person reads (`message`),
    with the HTTP status that says it, and, for a name already taken, the skill that has it."""

    def __init__(
        self, message: str, status: int = 400, conflict: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.conflict = conflict


def shown(name: str) -> str:
    """A name from an archive or a model, as a message may say it: no control characters, and
    not longer than a line."""
    text = "".join("?" if unicodedata.category(c)[0] == "C" else c for c in name)
    return text if len(text) <= 120 else text[:117] + "..."


def _refuse(entry: str, why: str, status: int = 400) -> Refused:
    return Refused(f"Not added: `{shown(entry)}` {why}.", status)


# --------------------------------------------------------------------------- #
# SKILL.md
# --------------------------------------------------------------------------- #


class _Loader(yaml.SafeLoader):
    """YAML's safe loader, without aliases: an alias can make a few bytes a very large value."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            raise yaml.MarkedYAMLError(
                None, None, "an alias (`*name`) is not taken here", event.start_mark
            )
        return super().compose_node(parent, index)


_FRONT = re.compile(r"---[ \t]*\n(.*?\n)?---[ \t]*(?:\n|\Z)", re.S)


@dataclass(frozen=True)
class Parsed:
    """What a `SKILL.md` says: its name and description, the instructions after the
    frontmatter, and the frontmatter as it was (other keys included)."""

    name: str
    description: str
    body: str
    meta: dict[str, Any]

    def others(self) -> list[dict[str, str]]:
        """The frontmatter's other keys, as the page shows them; Workbench acts on none."""
        out = []
        for key, value in self.meta.items():
            if key in ("name", "description"):
                continue
            text = value if isinstance(value, str) else json.dumps(value, default=str)
            out.append({"key": shown(str(key)), "value": shown(text)[:300]})
        return out


def name_problem(name: object) -> str | None:
    """Why `name` cannot be a skill's name, or None."""
    if not isinstance(name, str) or not name:
        return "has no name"
    if len(name) > MAX_NAME or not NAME.fullmatch(name):
        return (
            f"the name `{shown(name)}` breaks the rule: lowercase letters, digits and single "
            f"hyphens, at most {MAX_NAME} characters"
        )
    return None


def description_problem(description: object) -> str | None:
    if not isinstance(description, str) or not description.strip():
        return "the description is missing"
    if len(description) > MAX_DESCRIPTION:
        return f"the description is longer than {MAX_DESCRIPTION:,} characters"
    return None


def parse(data: bytes) -> Parsed:
    """Read a `SKILL.md`, or refuse it with its cause."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Refused("Not added: SKILL.md is not UTF-8 text.") from None
    text = text.replace("\r\n", "\n")
    found = _FRONT.match(text)
    if found is None:
        raise Refused(
            "Not added: SKILL.md does not start with frontmatter: a `---` line, its name and "
            "description, and another `---` line."
        )
    raw = found.group(1) or ""
    if len(raw) > MAX_FRONTMATTER:
        raise Refused(
            f"Not added: SKILL.md's frontmatter is longer than {MAX_FRONTMATTER:,} bytes."
        )
    try:
        meta = yaml.load(raw, Loader=_Loader)  # the safe loader, without aliases
    except (yaml.YAMLError, RecursionError) as exc:
        problem = getattr(exc, "problem", None) or type(exc).__name__
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 2})" if mark is not None else ""
        raise Refused(
            f"Not added: SKILL.md's frontmatter does not parse: {problem}{where}."
        ) from None
    if not isinstance(meta, dict):
        raise Refused("Not added: SKILL.md's frontmatter is not a set of keys and values.")
    for why in (name_problem(meta.get("name")), description_problem(meta.get("description"))):
        if why is not None:
            raise Refused(f"Not added: in SKILL.md, {why}.")
    return Parsed(
        name=meta["name"],
        description=meta["description"],
        body=text[found.end() :],
        meta={str(k): v for k, v in meta.items()},
    )


def compose(meta: dict[str, Any], instructions: str) -> bytes:
    """A `SKILL.md`: the frontmatter `meta` holds (name and description first), then the
    instructions."""
    ordered = {"name": meta["name"], "description": meta["description"]}
    ordered.update({k: v for k, v in meta.items() if k not in ordered})
    head = yaml.safe_dump(ordered, allow_unicode=True, sort_keys=False, width=1_000_000)
    return f"---\n{head}---\n\n{instructions.strip()}\n".encode()


# --------------------------------------------------------------------------- #
# the archive reader
# --------------------------------------------------------------------------- #


@dataclass
class Unpacked:
    """A skill judged whole and held in memory: its `SKILL.md`, and every other file by its path
    in the skill's folder."""

    parsed: Parsed
    skill_md: bytes
    members: dict[str, bytes] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.skill_md) + sum(len(d) for d in self.members.values())

    def listing(self) -> list[dict[str, Any]]:
        return [{"path": p, "size": len(d)} for p, d in sorted(self.members.items())]


def path_problem(path: str) -> str | None:
    """Why a path inside a skill cannot be written, or None. `path` uses `/` (a backslash is
    taken as one first). Every system's rules are kept, so a skill means the same file on all."""
    if path.startswith("/") or re.match(r"[A-Za-z]:", path) or ".." in path.split("/"):
        return "leaves the skill's folder"
    parts = path.split("/")
    if any(part in ("", ".") for part in parts):
        return "is not a plain path"
    if len(path) > MAX_PATH:
        return f"is a path longer than {MAX_PATH} characters"
    for part in parts:
        if part.split(".")[0].rstrip(" ").upper() in _DEVICES:
            return "is a name Windows keeps for a device"
        if part[-1] in " .":
            return "has a name that ends with a dot or a space, which Windows cannot hold"
        if any(c in _FORBIDDEN or ord(c) < 32 for c in part):
            return 'has a character Windows does not allow in a name (<>:"|?* or a control)'
        if len(part.encode("utf-8")) > 255:
            return "has a name longer than 255 bytes"
    return None


def _same(path: str) -> str:
    """What makes two names one file on Windows (case) or macOS (case and Unicode form)."""
    return unicodedata.normalize("NFC", path).casefold()


def read_archive(data: bytes) -> Unpacked:
    """Judge a skill's ZIP whole, in memory, and give its files; or refuse it with its cause."""
    if len(data) > MAX_UPLOAD:
        raise Refused(f"Not added: the file is larger than {files.describe_size(MAX_UPLOAD)}.", 413)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError):
        raise Refused("Not added: the file is not a ZIP archive Workbench can read.") from None
    if len(infos) > MAX_ENTRIES:
        raise Refused(f"Not added: the archive has more than {MAX_ENTRIES} entries.", 413)
    entries: list[tuple[str, zipfile.ZipInfo]] = []
    #: Every file and folder named so far, by what makes two names one file, with its kind.
    names: dict[str, tuple[str, bool]] = {}

    def claim(path: str, is_folder: bool) -> None:
        key = _same(path)
        if key not in names:
            names[key] = (path, is_folder)
            return
        first, was_folder = names[key]
        if first != path:
            raise Refused(
                f"Not added: `{shown(first)}` and `{shown(path)}` are the same file on Windows "
                "and macOS."
            )
        if not (was_folder and is_folder):
            raise _refuse(path, "is in the archive twice")

    declared = 0
    for info in infos:
        name = info.filename.replace("\\", "/")
        folder = name.endswith("/")
        path = name.rstrip("/") if folder else name
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if info.flag_bits & 0x1:
            raise _refuse(name, "is encrypted")
        # Deflate is read a bounded piece at a time; bzip2 and LZMA are not (`zipfile` hands
        # each piece to the decompressor whole), so a lying size could fill memory first.
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise _refuse(name, "is packed with a method other than Deflate, which Workbench reads")
        if stat.S_ISLNK(mode) or info.external_attr & _REPARSE_POINT:
            raise _refuse(name, "is a link")
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (kind == stat.S_IFDIR and not folder):
            raise _refuse(name, "is not a plain file or folder")
        why = path_problem(path)
        if why is not None:
            raise _refuse(name, why)
        parts = path.split("/")
        if len(parts) - (0 if folder else 1) > MAX_DEPTH:
            raise _refuse(name, f"is nested deeper than {MAX_DEPTH} folders")
        for depth in range(1, len(parts)):
            claim("/".join(parts[:depth]), True)
        claim(path, folder)
        if folder:
            continue
        if info.file_size > RATIO_FLOOR and info.file_size > MAX_RATIO * max(info.compress_size, 1):
            raise _refuse(name, f"unpacks to more than {MAX_RATIO} times its packed size", 413)
        declared += info.file_size
        if declared > MAX_UNPACKED:
            raise Refused(
                f"Not added: the archive unpacks to more than {MAX_UNPACKED // MIB} MiB.", 413
            )
        if not set(path.split("/")) & _JUNK:
            entries.append((path, info))
    paths = {path for path, _ in entries}
    if SKILL_MD in paths:
        prefix = ""
    else:
        tops = {path.split("/")[0] for path in paths}
        top = next(iter(tops)) if len(tops) == 1 else None
        if top is None or f"{top}/{SKILL_MD}" not in paths:
            raise Refused(
                f"Not added: there is no `{SKILL_MD}` at the archive's root or in one top folder."
            )
        prefix = f"{top}/"
    total = 0
    members: dict[str, bytes] = {}
    for path, info in entries:
        chunks: list[bytes] = []
        try:
            with archive.open(info) as handle:
                while piece := handle.read(256 * 1024):
                    total += len(piece)
                    if total > MAX_UNPACKED:
                        raise Refused(
                            f"Not added: the archive unpacks to more than {MAX_UNPACKED // MIB} "
                            "MiB.",
                            413,
                        )
                    chunks.append(piece)
        except Refused:
            raise
        except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError, ValueError) as exc:
            cause = (
                "uses a kind of compression Workbench does not read"
                if isinstance(exc, NotImplementedError)
                else "is damaged"
            )
            raise _refuse(path, cause) from None
        members[path.removeprefix(prefix)] = b"".join(chunks)
    skill_md = members.pop(SKILL_MD)
    return Unpacked(parse(skill_md), skill_md, members)


def read_upload(filename: str, data: bytes) -> Unpacked:
    """An upload: a skill's ZIP, or one `SKILL.md`."""
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06") or filename.lower().endswith(".zip"):
        return read_archive(data)
    if filename.lower().endswith(".md") or data.lstrip(b"\xef\xbb\xbf").startswith(b"---"):
        if len(data) > MAX_UNPACKED:
            raise Refused(f"Not added: the file is larger than {MAX_UNPACKED // MIB} MiB.", 413)
        return Unpacked(parse(data), data)
    raise Refused("Not added: upload a .zip of a skill's folder, or one SKILL.md.", 415)


def has_scripts(paths: Iterable[str]) -> bool:
    return any(
        p.startswith("scripts/") or PurePosixPath(p).suffix.lower() in _SCRIPT_SUFFIXES
        for p in paths
    )


# --------------------------------------------------------------------------- #
# the rows
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Skill:
    id: str
    owner: str
    name: str
    description: str
    enabled: bool
    source: str
    bytes: int
    files: tuple[tuple[str, int], ...]
    created_at: float
    updated_at: float

    @property
    def everyone(self) -> bool:
        return self.owner == INSTALL

    def paths(self) -> list[str]:
        return [p for p, _ in self.files]


def _skill(row: sqlite3.Row) -> Skill:
    return Skill(
        id=row["id"],
        owner=row["owner"],
        name=row["name"],
        description=row["description"],
        enabled=bool(row["enabled"]),
        source=row["source"],
        bytes=int(row["bytes"]),
        files=tuple((str(f["path"]), int(f["size"])) for f in json.loads(row["files"] or "[]")),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _files_json(listing: Iterable[tuple[str, int]]) -> str:
    return json.dumps([{"path": p, "size": s} for p, s in sorted(listing)])


class SkillStore:
    """The SQL of skills, run on the store's one thread, each call one transaction."""

    def __init__(self, store: Store) -> None:
        self.store = store

    async def _run[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        def transaction() -> T:
            db = self.store._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                result = fn(db)
                db.execute("COMMIT")
                return result
            except BaseException:
                db.execute("ROLLBACK")
                raise

        return await self.store._run(transaction)

    async def get(self, skill_id: str) -> Skill | None:
        def go(db: sqlite3.Connection) -> Skill | None:
            row = db.execute(
                f"SELECT * FROM skills WHERE id = ? AND owner NOT IN {_HIDDEN}", (skill_id,)
            ).fetchone()
            return _skill(row) if row is not None else None

        return await self._run(go)

    async def owned(self, owner: str, *, hidden_too: bool = False) -> list[Skill]:
        """The owner's skills, by name. A person removed from Eugene has none, unless
        `hidden_too` (the owner's download of everything Workbench holds of them, row 7)."""

        def go(db: sqlite3.Connection) -> list[Skill]:
            hidden = "" if hidden_too else f" AND owner NOT IN {_HIDDEN}"
            rows = db.execute(
                f"SELECT * FROM skills WHERE owner = ?{hidden} ORDER BY name", (owner,)
            )
            return [_skill(r) for r in rows]

        return await self._run(go)

    async def everyone(self) -> list[Skill]:
        return await self.owned(INSTALL)

    async def off(self, sub: str) -> set[str]:
        def go(db: sqlite3.Connection) -> set[str]:
            rows = db.execute("SELECT skill_id FROM skill_off WHERE sub = ?", (sub,))
            return {r[0] for r in rows}

        return await self._run(go)

    async def every(self) -> list[Skill]:
        """Every skill, a hidden person's included: what the folders are checked against."""
        return await self._run(lambda db: [_skill(r) for r in db.execute("SELECT * FROM skills")])

    async def put(self, skill: Skill, *, replacing: str | None = None) -> Refused | None:
        """Add `skill`, or put it in place of `replacing` (its id is kept). None when done, else
        why not: the owner's count or space would pass its limit, or the name is taken."""

        def go(db: sqlite3.Connection) -> Refused | None:
            taken = db.execute(
                "SELECT id, name FROM skills WHERE owner = ? AND name = ? AND id != ?",
                (skill.owner, skill.name, replacing or skill.id),
            ).fetchone()
            if taken is not None:
                return conflict(skill.owner, taken["id"], taken["name"])
            count, used = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM skills WHERE owner = ? AND id != ?",
                (skill.owner, replacing or skill.id),
            ).fetchone()
            if replacing is None and int(count) >= PER_PERSON:
                whose = "Eugene gives everyone" if skill.owner == INSTALL else "you have"
                return Refused(
                    f"Not added: {whose} {PER_PERSON} skills, the most kept. Delete one first.",
                    413,
                )
            if int(used) + skill.bytes > PER_PERSON_BYTES:
                whose = "the skills given to everyone" if skill.owner == INSTALL else "your skills"
                return Refused(
                    f"Not added: {whose} would use {files.describe_size(int(used) + skill.bytes)}; "
                    f"the limit is {PER_PERSON_BYTES // MIB} MiB.",
                    413,
                )
            values = (
                skill.owner,
                skill.name,
                skill.description,
                int(skill.enabled),
                skill.source,
                skill.bytes,
                _files_json(skill.files),
                skill.updated_at,
            )
            if replacing is not None:
                db.execute(
                    "UPDATE skills SET owner = ?, name = ?, description = ?, enabled = ?, "
                    "source = ?, bytes = ?, files = ?, updated_at = ? WHERE id = ?",
                    (*values, replacing),
                )
            else:
                db.execute(
                    "INSERT INTO skills (owner, name, description, enabled, source, bytes, files, "
                    "updated_at, id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*values, skill.id, skill.created_at),
                )
            return None

        return await self._run(go)

    async def set_enabled(self, skill_id: str, on: bool) -> None:
        await self._run(
            lambda db: (
                db.execute(
                    "UPDATE skills SET enabled = ? WHERE id = ?", (int(on), skill_id)
                ).rowcount
            )
        )

    async def set_off(self, sub: str, skill_id: str, off: bool) -> None:
        def go(db: sqlite3.Connection) -> None:
            if off:
                db.execute(
                    "INSERT OR IGNORE INTO skill_off (sub, skill_id) VALUES (?, ?)", (sub, skill_id)
                )
            else:
                db.execute("DELETE FROM skill_off WHERE sub = ? AND skill_id = ?", (sub, skill_id))

        await self._run(go)

    async def move(self, skill_id: str, owner: str, *, enabled: bool) -> Refused | None:
        """Give a skill to everyone (`owner` is `install`) or take it back (the owner's `sub`):
        whoever turned it off had it off as the install's, so those rows go."""

        def go(db: sqlite3.Connection) -> Refused | None:
            row = db.execute("SELECT name FROM skills WHERE id = ?", (skill_id,)).fetchone()
            taken = db.execute(
                "SELECT id, name FROM skills WHERE owner = ? AND name = ? AND id != ?",
                (owner, row["name"], skill_id),
            ).fetchone()
            if taken is not None:
                return conflict(owner, taken["id"], taken["name"])
            db.execute(
                "UPDATE skills SET owner = ?, enabled = ?, updated_at = ? WHERE id = ?",
                (owner, int(enabled), time.time(), skill_id),
            )
            db.execute("DELETE FROM skill_off WHERE skill_id = ?", (skill_id,))
            return None

        return await self._run(go)

    async def delete(self, skill_id: str) -> None:
        def go(db: sqlite3.Connection) -> None:
            db.execute("DELETE FROM skill_off WHERE skill_id = ?", (skill_id,))
            db.execute("DELETE FROM skills WHERE id = ?", (skill_id,))

        await self._run(go)


def conflict(owner: str, skill_id: str, name: str) -> Refused:
    whose = "Eugene already gives everyone" if owner == INSTALL else "You already have"
    return Refused(
        f"{whose} a skill named {name}. Replace it, or keep both.",
        409,
        {"id": skill_id, "name": name},
    )


# --------------------------------------------------------------------------- #
# the folders
# --------------------------------------------------------------------------- #


def owner_key(owner: str) -> str:
    """The folder a skill's owner keeps skills in: the person's own key, or `install`."""
    return INSTALL if owner == INSTALL else files.person_key(owner)


def _writable(function: Callable[..., Any], target: str, _excinfo: Any) -> None:
    os.chmod(target, stat.S_IWRITE)
    function(target)


def _remove(path: Path) -> None:
    """A folder of read-only files, gone (Windows deletes no read-only file). A link is removed
    as itself: what it points to is never changed."""
    if path.is_symlink():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, onexc=_writable)
    elif path.exists():
        with contextlib.suppress(FileNotFoundError):
            os.chmod(path, stat.S_IWRITE)
            path.unlink()


def _inside(folder: Path, path: str) -> Path:
    """Where `path` (with `/`) is in `folder`, judged on its words before anything is made
    there: no part may be a root, a drive (`C:` on Windows), `.` or `..`. A refusal names it."""
    parts = path.split("/")
    if any(PurePath(part).anchor or part in ("", ".", "..") for part in parts):
        raise Refused(f"Not added: `{shown(path)}` leaves the skill's folder.")
    target = folder.joinpath(*parts)
    if not target.is_relative_to(folder):
        raise Refused(f"Not added: `{shown(path)}` leaves the skill's folder.")
    return target


def _write(folder: Path, skill_md: bytes, members: dict[str, bytes]) -> None:
    """A new folder holding the skill's files, each written once and made read-only. Each path
    was judged before (`path_problem`); a file whose place is outside the folder after all is a
    refusal, and nothing it would have written is left."""
    # Every place is judged before the first folder is made.
    targets = [
        (_inside(folder, path), path, data)
        for path, data in [(SKILL_MD, skill_md), *sorted(members.items())]
    ]
    folder.mkdir(parents=True)
    base = folder.resolve()
    for target, path, data in targets:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.parent.resolve().is_relative_to(base) or target.parent.is_symlink():
                raise Refused(f"Not added: `{shown(path)}` leaves the skill's folder.")
            with target.open("xb") as handle:
                handle.write(data)
            os.chmod(target, stat.S_IREAD)
        except OSError as exc:
            raise Refused(
                f"Not added: Workbench could not write `{shown(path)}`: "
                f"{exc.strerror or type(exc).__name__}.",
                500,
            ) from None


def _read_skill_md(folder: Path) -> bytes:
    with (folder / SKILL_MD).open("rb") as handle:
        return handle.read(MAX_UNPACKED + 1)


def _replace_file(target: Path, data: bytes) -> None:
    """A file of a skill written again whole: beside it, then in its place, read-only."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fresh = target.with_name(f".{target.name}.{secrets.token_hex(4)}.part")
    try:
        with fresh.open("xb") as handle:
            handle.write(data)
        if target.exists():
            os.chmod(target, stat.S_IWRITE)
        os.replace(fresh, target)
        os.chmod(target, stat.S_IREAD)
    finally:
        if fresh.exists():
            _remove(fresh)


# --------------------------------------------------------------------------- #
# skills, as people and the model use them
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Listing:
    """What one person's chats may list: the skills listed, and why each other one is not."""

    listed: list[Skill]
    not_listed: dict[str, str]


def bounded(skills: list[Skill]) -> tuple[list[Skill], dict[str, str]]:
    """At most `MAX_LISTED` skills and `MAX_LISTING` characters of the listing, by name; the
    rest with why each is not listed."""
    listed: list[Skill] = []
    left: dict[str, str] = {}
    used = 0
    for skill in sorted(skills, key=lambda s: (s.name, s.id)):
        line = len(listing_line(skill)) + 1
        if len(listed) >= MAX_LISTED:
            left[skill.id] = f"Not listed in chats: more than {MAX_LISTED} skills are on."
        elif used + line > MAX_LISTING:
            left[skill.id] = (
                "Not listed in chats: the names and descriptions of the skills that are on are "
                f"over {MAX_LISTING:,} characters."
            )
        else:
            listed.append(skill)
            used += line
    return listed, left


def listing_line(skill: Skill) -> str:
    return f"- {skill.name}: {' '.join(skill.description.split())}"


def definitions(listed: list[Skill]) -> list[dict[str, Any]]:
    """The two tools, for the skills a chat lists. The same skills make the same bytes."""
    names = [s.name for s in listed]
    name = {"type": "string", "enum": names, "description": "The skill's name."}
    lines = "\n".join(listing_line(s) for s in listed)
    return [
        {
            "type": "function",
            "function": {
                "name": USE_SKILL,
                "description": (
                    "Load a skill: instructions the person added for one kind of task. When a "
                    "request fits a skill's description below, call this before you answer, then "
                    "follow what it returns. Skills:\n" + lines
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"name": name},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": READ_SKILL_FILE,
                "description": (
                    "Read one file of a skill, by a path use_skill listed (for example "
                    "references/style.md), when the skill's instructions point to it."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": name,
                        "path": {"type": "string", "maxLength": 512},
                    },
                    "required": ["name", "path"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def scripts_line(sites: list[str]) -> str:
    if not sites:
        return (
            "This skill has scripts. They run only on a Job Site that allows commands; this chat "
            "has none."
        )
    return (
        "This skill has scripts. They run only on a Job Site that allows commands: "
        f"{', '.join(sites)}. Copy a script there with that site's write_text, then run it with "
        "run_command."
    )


class Skills:
    """Every person's skills: their rows, their folders, and the two tools. One per Workbench."""

    def __init__(self, store: Store, data_dir: Path) -> None:
        self.db = SkillStore(store)
        self.root = data_dir / "skills"
        #: Skills change one at a time, so a name, a count and a folder are never raced.
        self.lock = asyncio.Lock()

    def folder(self, skill: Skill) -> Path:
        return self.root / owner_key(skill.owner) / skill.id

    def _incoming(self) -> Path:
        return self.root / f".incoming-{secrets.token_hex(8)}"

    # --- at start ------------------------------------------------------------

    async def reconcile(self) -> int:
        """What a stop left: a half-written or set-aside folder, or one no skill's row names, is
        removed; a skill whose folder a stop left under its other owner is moved back. How many
        folders were removed."""
        rows = {s.id: s for s in await self.db.every()}

        def go() -> int:
            if not self.root.is_dir():
                return 0
            gone = 0
            found: dict[str, Path] = {}
            for entry in list(self.root.iterdir()):
                if entry.name.startswith("."):
                    _remove(entry)
                    gone += 1
                    continue
                if not entry.is_dir():
                    continue
                for folder in list(entry.iterdir()):
                    if folder.name.startswith(".") or folder.name not in rows:
                        _remove(folder)
                        gone += 1
                    else:
                        found[folder.name] = folder
            for skill_id, folder in found.items():
                wanted = self.root / owner_key(rows[skill_id].owner) / skill_id
                if folder != wanted and not wanted.exists():
                    wanted.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(folder, wanted)
            return gone

        return await asyncio.to_thread(go)

    # --- what a person sees ------------------------------------------------------

    async def seen(self, person: Person) -> tuple[list[Skill], list[Skill], set[str]]:
        """The person's own skills, the install's, and which of the install's they turned off."""
        own = await self.db.owned(person.sub)
        shared = await self.db.everyone()
        return own, shared, await self.db.off(person.sub)

    async def listing(self, person: Person) -> Listing:
        """What "All my skills" lists in a chat of theirs, and why each other skill is not."""
        own, shared, off = await self.seen(person)
        mine = {s.name for s in own}
        reasons: dict[str, str] = {}
        candidates = [s for s in own if s.enabled]
        for skill in own:
            if not skill.enabled:
                reasons[skill.id] = "Off."
        for skill in shared:
            if skill.id in off:
                reasons[skill.id] = "Off."
            elif skill.name in mine:
                reasons[skill.id] = "Not listed in chats: you have your own skill with this name."
            else:
                candidates.append(skill)
        listed, left = bounded(candidates)
        return Listing(listed, {**reasons, **left})

    async def for_chat(self, person: Person, chat: Chat) -> list[Skill]:
        """The skills one chat's answer lists: all that are on, or the chat's pick of them."""
        listed = (await self.listing(person)).listed
        pick = chat.settings.get("skills")
        if isinstance(pick, list):
            chosen = set(pick)
            listed = [s for s in listed if s.id in chosen]
        return listed

    async def visible(self, person: Person, skill_id: str) -> Skill:
        """A skill the person may see: their own, or one Eugene gives everyone. Anything else is
        the 404 a skill that does not exist gets."""
        skill = await self.db.get(skill_id)
        if skill is None or skill.owner not in (person.sub, INSTALL):
            raise Refused("There is no such skill.", 404)
        return skill

    async def editable(self, person: Person, skill_id: str) -> Skill:
        skill = await self.visible(person, skill_id)
        if skill.everyone and not person.is_owner:
            raise Refused(
                "Only the owner of this install changes a skill Eugene gives everyone. You can "
                "turn it off for yourself.",
                403,
            )
        return skill

    async def read(self, skill: Skill) -> tuple[bytes, Parsed | None]:
        """A skill's `SKILL.md` as it is on the disk, and what it says (None when it no longer
        parses, or is gone)."""
        try:
            data = await asyncio.to_thread(_read_skill_md, self.folder(skill))
        except OSError:
            return b"", None
        try:
            return data, parse(data)
        except Refused:
            return data, None

    # --- adding and changing -----------------------------------------------------

    async def add(
        self,
        person: Person,
        unpacked: Unpacked,
        *,
        source: str,
        on_conflict: str | None = None,
        on: bool = False,
    ) -> Skill:
        """Add a skill a person uploaded or wrote. A name they already use is a 409, unless they
        chose to replace that skill (its id and place are kept, and it is off again: its words
        changed) or keep both (this one is named `-2`, `-3`, ...)."""
        async with self.lock:
            name = unpacked.parsed.name
            own = {s.name: s for s in await self.db.owned(person.sub)}
            replacing: Skill | None = None
            if name in own:
                if on_conflict == "replace":
                    replacing = own[name]
                elif on_conflict == "both":
                    name = _free_name(name, set(own))
                else:
                    raise conflict(person.sub, own[name].id, name)
            now = time.time()
            skill = Skill(
                id=replacing.id if replacing else secrets.token_hex(12),
                owner=person.sub,
                name=name,
                description=unpacked.parsed.description,
                enabled=on,
                source=source,
                bytes=unpacked.size,
                files=tuple((m["path"], m["size"]) for m in unpacked.listing()),
                created_at=replacing.created_at if replacing else now,
                updated_at=now,
            )
            # The quota is asked before anything is written, and again as the row goes in.
            await self._check_room(skill, replacing)
            incoming = self._incoming()
            try:
                await asyncio.to_thread(_write, incoming, unpacked.skill_md, unpacked.members)
                # The folder it replaces is kept aside until the row says the new one is it.
                aside = await asyncio.to_thread(self._swap, incoming, self.folder(skill))
                try:
                    refused = await self.db.put(
                        skill, replacing=replacing.id if replacing else None
                    )
                    if refused is not None:
                        raise refused
                except BaseException:
                    await asyncio.to_thread(self._unswap, self.folder(skill), aside)
                    raise
                if aside is not None:
                    await asyncio.to_thread(_remove, aside)
            except OSError as exc:
                raise Refused(
                    "Not added: Workbench could not put the skill's folder in place: "
                    f"{exc.strerror or type(exc).__name__}.",
                    500,
                ) from None
            finally:
                await asyncio.to_thread(_remove, incoming)
            log.info("%s added the skill %s (%s)", person.name, skill.name, source)
            return skill

    async def _check_room(self, skill: Skill, replacing: Skill | None) -> None:
        owned = await self.db.owned(skill.owner)
        others = [s for s in owned if s.id not in (skill.id, replacing.id if replacing else None)]
        if replacing is None and len(others) >= PER_PERSON:
            whose = "Eugene gives everyone" if skill.everyone else "you have"
            raise Refused(
                f"Not added: {whose} {PER_PERSON} skills, the most kept. Delete one first.", 413
            )
        used = sum(s.bytes for s in others) + skill.bytes
        if used > PER_PERSON_BYTES:
            whose = "the skills given to everyone" if skill.everyone else "your skills"
            raise Refused(
                f"Not added: {whose} would use {files.describe_size(used)}; the limit is "
                f"{PER_PERSON_BYTES // MIB} MiB.",
                413,
            )

    def _swap(self, incoming: Path, final: Path) -> Path | None:
        """Move a new folder into a skill's place. The folder that was there is set aside, not
        removed: where it is set aside is returned, for the caller to remove once the row says
        the new one is the skill's, or to put back (`_unswap`)."""
        final.parent.mkdir(parents=True, exist_ok=True)
        aside: Path | None = None
        if final.exists():
            aside = self.root / f".old-{secrets.token_hex(8)}"
            os.replace(final, aside)
        try:
            os.replace(incoming, final)
        except BaseException:
            if aside is not None:
                os.replace(aside, final)
            raise
        return aside

    def _unswap(self, final: Path, aside: Path | None) -> None:
        _remove(final)
        if aside is not None:
            os.replace(aside, final)

    async def change(self, person: Person, skill_id: str, body: dict[str, Any]) -> Skill:
        """Turn a skill on or off (an install skill, for this person only), give it to everyone
        or take it back (the owner only), or write its name, description or instructions again."""
        async with self.lock:
            skill = await self.visible(person, skill_id)
            # Whether each part may be done is asked first, so a refusal changes nothing.
            words = {k: body[k] for k in ("name", "description", "instructions") if k in body}
            if words:
                skill = await self.editable(person, skill_id)
            if "everyone" in body and not person.is_owner:
                raise Refused("Only the owner of this install gives a skill to everyone.", 403)
            if words:
                await self._rewrite(skill, words)
            if "on" in body:
                if skill.everyone:
                    await self.db.set_off(person.sub, skill.id, not body["on"])
                else:
                    await self.db.set_enabled(skill.id, bool(body["on"]))
            if "everyone" in body and body["everyone"] != skill.everyone:
                await self._give(person, await self.visible(person, skill_id), body["everyone"])
            return await self.visible(person, skill_id)

    async def _rewrite(self, skill: Skill, words: dict[str, str]) -> None:
        data, parsed = await self.read(skill)
        if parsed is None:
            raise Refused(
                "Its SKILL.md cannot be read now. Delete this skill and add it again.", 409
            )
        meta = {**parsed.meta}
        meta.update({k: words[k] for k in ("name", "description") if k in words})
        body = words.get("instructions", parsed.body)
        new = compose(meta, body)
        checked = parse(new)
        if len(new) > MAX_UNPACKED:
            raise Refused(f"Not changed: SKILL.md would be over {MAX_UNPACKED // MIB} MiB.", 413)
        changed = replace(
            skill,
            name=checked.name,
            description=checked.description,
            bytes=skill.bytes - len(data) + len(new),
            updated_at=time.time(),
        )
        refused = await self.db.put(changed, replacing=skill.id)
        if refused is not None:
            raise refused
        await asyncio.to_thread(_replace_file, self.folder(skill) / SKILL_MD, new)

    async def _give(self, person: Person, skill: Skill, everyone: bool) -> None:
        if not person.is_owner:
            raise Refused("Only the owner of this install gives a skill to everyone.", 403)
        if everyone and skill.owner != person.sub:
            raise Refused("There is no such skill.", 404)
        owner = INSTALL if everyone else person.sub
        # Taken back, it is on for the owner unless they had turned it off for themselves.
        on = True if everyone else skill.id not in await self.db.off(person.sub)
        moved = replace(skill, owner=owner)
        await self._check_room(moved, None)
        refused = await self.db.move(skill.id, owner, enabled=on)
        if refused is not None:
            raise refused
        try:
            aside = await asyncio.to_thread(self._swap, self.folder(skill), self.folder(moved))
        except BaseException:
            await self.db.move(skill.id, skill.owner, enabled=skill.enabled)
            raise
        if aside is not None:
            await asyncio.to_thread(_remove, aside)
        log.info(
            "%s %s the skill %s",
            person.name,
            "gave everyone" if everyone else "took back from everyone",
            skill.name,
        )

    async def delete(self, person: Person, skill_id: str) -> None:
        async with self.lock:
            skill = await self.editable(person, skill_id)
            await self.db.delete(skill.id)
            await asyncio.to_thread(_remove, self.folder(skill))

    async def add_file(self, person: Person, skill_id: str, path: str, data: bytes) -> Skill:
        async with self.lock:
            skill = await self.editable(person, skill_id)
            path = path.replace("\\", "/").strip()
            why = path_problem(path) if path else "is not a path"
            if why is not None:
                raise _refuse(path, why)
            if _same(path) == _same(SKILL_MD):
                raise Refused("Not added: change SKILL.md with Edit, not as a file.")
            known = {_same(p): p for p in skill.paths()}
            if _same(path) in known and known[_same(path)] != path:
                raise Refused(
                    f"Not added: `{shown(known[_same(path)])}` and `{shown(path)}` are the same "
                    "file on Windows and macOS."
                )
            if len(skill.files) >= MAX_ENTRIES - 1 and path not in known.values():
                raise Refused(f"Not added: a skill holds at most {MAX_ENTRIES} files.", 413)
            if path.count("/") > MAX_DEPTH:
                raise _refuse(path, f"is nested deeper than {MAX_DEPTH} folders")
            listing = {p: s for p, s in skill.files}
            before = listing.get(path, 0)
            if skill.bytes - before + len(data) > MAX_UNPACKED:
                raise Refused(f"Not added: the skill would be over {MAX_UNPACKED // MIB} MiB.", 413)
            listing[path] = len(data)
            changed = replace(
                skill,
                files=tuple(sorted(listing.items())),
                bytes=skill.bytes - before + len(data),
                updated_at=time.time(),
            )
            await self._check_room(changed, skill)
            folder = self.folder(skill)

            def write() -> None:
                target = _inside(folder, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.parent.resolve().is_relative_to(folder.resolve()):
                    raise _refuse(path, "leaves the skill's folder")
                _replace_file(target, data)

            try:
                await asyncio.to_thread(write)
            except OSError as exc:
                raise Refused(
                    f"Not added: Workbench could not write `{shown(path)}`: "
                    f"{exc.strerror or type(exc).__name__}.",
                    500,
                ) from None
            refused = await self.db.put(changed, replacing=skill.id)
            if refused is not None:
                raise refused
            return changed

    async def remove_file(self, person: Person, skill_id: str, path: str) -> Skill:
        async with self.lock:
            skill = await self.editable(person, skill_id)
            listing = {p: s for p, s in skill.files}
            if path not in listing:
                raise Refused(f"This skill has no file `{shown(path)}`.", 404)
            size = listing.pop(path)
            changed = replace(
                skill,
                files=tuple(sorted(listing.items())),
                bytes=skill.bytes - size,
                updated_at=time.time(),
            )
            refused = await self.db.put(changed, replacing=skill.id)
            if refused is not None:
                raise refused
            # The path is one the row lists, so it was judged when it was added.
            await asyncio.to_thread(_remove, _inside(self.folder(skill), path))
            return changed

    # --- the model's two tools ---------------------------------------------------

    async def run(
        self,
        person: Person,
        listed: list[Skill],
        tool: str,
        arguments: dict[str, Any],
        sites: list[str],
    ) -> tuple[str, str]:
        """Run `use_skill` or `read_skill_file` for the person: the call's status and what the
        model is told. A skill removed or turned off since the answer began is said, not an
        error."""
        name = str(arguments.get("name") or "")
        entry = next((s for s in listed if s.name == name), None)
        if entry is None:
            return "failed", f"There is no skill named {shown(name)} in this chat."
        skill = await self.db.get(entry.id)
        if skill is None or skill.owner not in (person.sub, INSTALL):
            return "done", f"The skill {entry.name} was removed. Answer without it."
        if (skill.everyone and skill.id in await self.db.off(person.sub)) or (
            not skill.everyone and not skill.enabled
        ):
            return "done", f"The skill {entry.name} was turned off. Answer without it."
        if tool == USE_SKILL:
            return await self._use(skill, sites)
        return await self._read_file(skill, str(arguments.get("path") or ""))

    async def _use(self, skill: Skill, sites: list[str]) -> tuple[str, str]:
        _, parsed = await self.read(skill)
        if parsed is None:
            return "failed", f"The skill {skill.name} could not be read: its SKILL.md is missing."
        body = parsed.body.strip()
        cut = len(body) > MAX_BODY
        lines = [f"The skill {skill.name}. Follow its instructions for this request.", ""]
        lines.append(body[:MAX_BODY] if body else "It has no instructions beyond its description.")
        if cut:
            lines += ["", f"[The instructions are cut at {MAX_BODY:,} characters.]"]
        lines.append("")
        if skill.files:
            lines.append("Files in this skill (read one with read_skill_file):")
            lines += [f"- {p} ({_size(s)})" for p, s in skill.files]
        else:
            lines.append("This skill has no other files.")
        if has_scripts(skill.paths()):
            lines += ["", scripts_line(sites)]
        return "done", "\n".join(lines)

    async def _read_file(self, skill: Skill, asked: str) -> tuple[str, str]:
        path = asked.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        if path.startswith("/") or re.match(r"[A-Za-z]:", path) or ".." in path.split("/"):
            return "failed", (
                f"`{shown(asked)}` is outside the skill {skill.name}. Use a path use_skill listed."
            )
        known = [SKILL_MD, *skill.paths()]
        if path not in known:
            return "failed", (
                f"The skill {skill.name} has no file `{shown(path)}`. Its files: "
                f"{', '.join(known)}."
            )
        folder = self.folder(skill)

        def go() -> tuple[bytes, bool] | None:
            try:
                target = _inside(folder, path)
            except Refused:
                return None
            if target.is_symlink() or not target.is_file():
                return None
            if not target.resolve().is_relative_to(folder.resolve()):
                return None
            with target.open("rb") as handle:
                data = handle.read(MAX_FILE_TEXT * 4 + 1)
            return data, len(data) > MAX_FILE_TEXT * 4

        try:
            found = await asyncio.to_thread(go)
        except OSError:
            found = None
        if found is None:
            return "failed", f"The file `{shown(path)}` of the skill {skill.name} is missing."
        data, more = found
        size = dict(skill.files).get(path, len(data))
        try:
            if b"\0" in data[:8192]:
                raise UnicodeDecodeError("utf-8", data, 0, 1, "a null byte")
            text = codecs.getincrementaldecoder("utf-8")().decode(data, final=not more)
        except UnicodeDecodeError:
            kind = mimetypes.guess_type(path)[0] or "a binary file"
            return "done", f"`{path}` is not text: {kind}, {_size(size)}."
        cut = more or len(text) > MAX_FILE_TEXT
        out = f"{path} of the skill {skill.name}:\n\n{text[:MAX_FILE_TEXT]}"
        if cut:
            out += f"\n\n[The file is cut at {MAX_FILE_TEXT:,} characters.]"
        return "done", out

    # --- a person's data -----------------------------------------------------------

    async def exported(self, sub: str) -> list[tuple[str, Path]]:
        """Each of the person's own skills, as a download holds it: its name and its folder. A
        removed person's too: the owner's download of them holds everything (row 7)."""
        return [(s.name, self.folder(s)) for s in await self.db.owned(sub, hidden_too=True)]


def _free_name(name: str, taken: set[str]) -> str:
    for n in range(2, PER_PERSON + 2):
        suffix = f"-{n}"
        candidate = name[: MAX_NAME - len(suffix)].rstrip("-") + suffix
        if candidate not in taken:
            return candidate
    raise Refused(f"Not added: you have {PER_PERSON} skills, the most kept.", 413)


def _size(size: int) -> str:
    return files.describe_size(size) if size >= 1024 else f"{size} bytes"


def view(skill: Skill, person: Person, listing: Listing, off: set[str]) -> dict[str, Any]:
    """A skill as the Skills page reads it: never a path on the disk."""
    mine = skill.owner == person.sub
    on = skill.enabled if mine else skill.id not in off
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "source": skill.source,
        "bytes": skill.bytes,
        "files": [{"path": p, "size": s} for p, s in skill.files],
        "createdAt": skill.created_at,
        "updatedAt": skill.updated_at,
        "on": on,
        "everyone": skill.everyone,
        "fromEugene": skill.everyone and not person.is_owner,
        "canEdit": mine or (skill.everyone and person.is_owner),
        "listed": any(s.id == skill.id for s in listing.listed),
        "notListed": listing.not_listed.get(skill.id),
        "hasScripts": has_scripts(skill.paths()),
    }
