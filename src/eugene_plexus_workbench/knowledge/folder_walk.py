"""A folder's files, and what to do about each (MR7, design 3 to 7): a walk, and a plan.

**The walk** (`walk`) lists a folder's tree through a `Tree` (`folder_io.Tree`: the entries of a
directory, and the bytes of a small file) and judges every name by the folder's rules
(`folder_rules`), the caps and what the entry is. It goes level by level, in name order, so the
files that are left out when a cap is reached are the deepest and last, the same ones every time.
It never enters a link (a symlink, a junction, a cloud-sync placeholder), never lists a folder
past the depth cap, and stops when it has looked at `max_entries` names or `max_files` files
are taken. What it leaves out is counted by reason, with the first few named (`SAMPLE`), and a
directory it could not list (permission, a busy disk: anything but "not found") is named with
the cause and **not** treated as empty.

**The plan** (`plan`) sets what the walk found against what the index holds for the folder
(`index.Held`: one document each, by path) and says, for each, whether it is new, must be
read again, is unchanged, was moved, or is gone. It is pure, so every case is a unit test with
no files.
A file's size and modified time say whether it is worth reading; **its content hash** (taken when
it is read, in `folder_sync`) says whether it changed. A document under a directory that
could not be listed is left as it is: only what is known to be gone is removed.
"""

from __future__ import annotations

import sys
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..folder_io import DIRECTORY, FILE, LINK, OTHER, Entry, FolderError, Tree
from .folder_rules import MAX_GITIGNORE_BYTES, Chain, Rules, parse_gitignore
from .index import Held

#: The names of what was left out that a report keeps; the rest are only counted.
SAMPLE = 200
#: The most names one walk looks at, ignored or not.
ENTRIES_LIMIT = 200_000
#: The `.gitignore` of a directory, by its name.
GITIGNORE = ".gitignore"

#: A skipped file that is read again at every check: the reason may pass by itself.
RETRY_ALWAYS = frozenset({"busy", "changed", "unreadable"})
#: ... and one that is read again when the person asks (Sync now): a limit that may have moved
#: (the owner can raise any of them), or a failure that may not have been the file's.
RETRY_MANUAL = frozenset({"too_many_passages", "over_folder_size", "too_large"})


@dataclass(frozen=True)
class Caps:
    """What one walk may take: files, folders deep (below the one granted), names looked at."""

    max_files: int
    max_depth: int
    max_entries: int = ENTRIES_LIMIT


@dataclass(frozen=True)
class Found:
    """A file that passed the rules and the caps, as the listing described it."""

    path: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class Left:
    """Something the walk did not take, with a code and the words for it. `kind` is `file`,
    `folder`, `link` or `other`."""

    path: str
    kind: str
    code: str
    reason: str


@dataclass
class Walked:
    """What a walk came to."""

    files: list[Found] = field(default_factory=list)
    #: The first `SAMPLE` things left out, and how many of each code there were in all.
    left: list[Left] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    #: The directories listed (`""` is the folder), and the ones that could not be, with the
    #: cause as it was observed.
    listed: set[str] = field(default_factory=set)
    unlisted: dict[str, str] = field(default_factory=dict)
    entries: int = 0
    bytes: int = 0
    ignored_files: int = 0
    ignored_folders: int = 0
    links: int = 0
    #: Folders past the depth cap that were not entered.
    too_deep: int = 0
    #: More files than the cap, and more names than a walk looks at.
    over_files: bool = False
    over_entries: bool = False

    @property
    def directories(self) -> int:
        return len(self.listed)

    def leave(self, path: str, kind: str, code: str, reason: str) -> None:
        self.counts[code] = self.counts.get(code, 0) + 1
        if len(self.left) < SAMPLE:
            self.left.append(Left(path, kind, code, reason))


def observed(exc: BaseException) -> str:
    """What was seen to go wrong, as a short phrase: the operating system's own words, or a
    refusal's."""
    cause = getattr(exc, "cause", None) or exc
    if isinstance(cause, PermissionError):
        return "permission denied"
    if isinstance(cause, OSError):
        return (cause.strerror or type(cause).__name__).rstrip(".")
    return str(exc).rstrip(".") or type(exc).__name__


def odd_name(name: str) -> bool:
    """A name Workbench will not open: control characters, text that cannot be written down
    (a name with bytes that are not text), or a colon on Windows (a stream, not a file)."""
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return True
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return ":" in name and sys.platform == "win32"


def link_words(entry: Entry) -> tuple[str, str]:
    """The code and the words for a link that is not followed."""
    if entry.cloud:
        return (
            "cloud",
            "Not read: this file is kept online by a cloud-sync program (OneDrive, Dropbox ...), "
            "so reading it would download it.",
        )
    return (
        "link",
        "Not followed: this is a link or a junction. Workbench never follows links out of the "
        "folder.",
    )


def _under(path: str, directories: dict[str, str]) -> bool:
    """Whether a file at `path` is in, or below, a directory in `directories`."""
    parent = path.rpartition("/")[0]
    while True:
        if parent in directories:
            return True
        if not parent:
            return False
        parent = parent.rpartition("/")[0]


def _gitignore(
    tree: Tree, rules: Rules, names: tuple[str, ...], base: str, listing: list[Entry], chain: Chain
) -> Chain:
    """`chain` with this directory's `.gitignore`, when it has one that holds a pattern."""
    if not rules.use_gitignore or not any(e.name == GITIGNORE and e.kind == FILE for e in listing):
        return chain
    try:
        data = tree.read_small([*names, GITIGNORE], MAX_GITIGNORE_BYTES)
    except (OSError, FolderError):
        return chain
    patterns = parse_gitignore(data.decode("utf-8", errors="replace"))
    return (*chain, (base, patterns)) if patterns else chain


def walk(tree: Tree, rules: Rules, caps: Caps) -> Walked:
    """Every file of the folder that is taken, and why anything is not. An error listing the
    folder itself is the caller's (it says the folder is gone, or cannot be read); an error in a
    directory below it is that directory's, named in `unlisted`."""
    out = Walked()
    queue: deque[tuple[tuple[str, ...], Chain]] = deque([((), ())])
    while queue and not out.over_entries:
        names, chain = queue.popleft()
        base = "/".join(names)
        budget = caps.max_entries - out.entries
        try:
            listing = tree.entries(list(names), budget + 1)
        except (FileNotFoundError, NotADirectoryError):
            if not names:
                raise
            continue  # gone since its parent was listed: nothing there to be looked at
        except (OSError, FolderError) as exc:
            if not names:
                raise
            out.unlisted[base] = observed(exc)
            continue
        out.listed.add(base)
        if len(listing) > budget:
            listing = listing[:budget]
            out.over_entries = True
        out.entries += len(listing)
        listing.sort(key=lambda e: (e.name.casefold(), e.name))
        chain = _gitignore(tree, rules, names, base, listing, chain)
        for entry in listing:
            path = f"{base}/{entry.name}" if base else entry.name
            is_dir = entry.kind == DIRECTORY
            why = rules.judge(path, entry.name, is_dir, hidden=entry.hidden, chain=chain)
            if why is not None:
                if is_dir:
                    out.ignored_folders += 1
                else:
                    out.ignored_files += 1
                out.leave(path, "folder" if is_dir else "file", f"ignored_{why.code}", why.reason)
            elif entry.kind == LINK:
                out.links += 1
                code, words = link_words(entry)
                out.leave(path, "link", code, words)
            elif entry.kind == OTHER:
                out.leave(
                    path,
                    "other",
                    "special",
                    "Skipped: this is not an ordinary file (a pipe, a device), so Workbench "
                    "does not read it.",
                )
            elif odd_name(entry.name):
                out.leave(
                    path,
                    "folder" if is_dir else "file",
                    "odd_name",
                    "Skipped: Workbench cannot open a file or folder whose name has characters "
                    "like these.",
                )
            elif is_dir:
                if len(names) + 1 > caps.max_depth:
                    out.too_deep += 1
                    out.leave(
                        path,
                        "folder",
                        "too_deep",
                        f"Not looked at: this folder is more than {caps.max_depth} folders deep.",
                    )
                else:
                    queue.append(((*names, entry.name), chain))
            elif len(out.files) >= caps.max_files:
                out.over_files = True
                out.leave(
                    path,
                    "file",
                    "over_files",
                    f"Left out: the folder has more than the {caps.max_files:,} files Workbench "
                    "keeps for one folder.",
                )
            else:
                out.files.append(Found(path, entry.size, entry.mtime_ns))
                out.bytes += entry.size
    return out


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #


@dataclass
class Plan:
    """What a check has to do. `add`: no document yet. `change`: read again (its size or time
    differ, it was too new to trust, a retry is due, or this is a full check). `move`: a file
    that is the same size and time as a document whose path is gone, to be moved if its hash is
    the same. `remove`: known to be gone. `keep`: unchanged. `unknown`: under a directory that
    could not be listed, so neither kept nor removed on a guess."""

    add: list[Found] = field(default_factory=list)
    change: list[tuple[Held, Found]] = field(default_factory=list)
    move: list[tuple[Held, Found]] = field(default_factory=list)
    remove: list[Held] = field(default_factory=list)
    keep: list[Held] = field(default_factory=list)
    unknown: list[Held] = field(default_factory=list)


def signature(found: Found) -> str:
    """What a file's modified time is kept as."""
    return str(found.mtime_ns)


def needs_reading(held: Held, found: Found, *, full: bool, manual: bool) -> bool:
    """Whether a file that has a document is read again: always in a full check; when the size or
    time differ (a time that could not be trusted was kept with a `!` after it, so it is never the
    time the file has now); and for a file that was skipped or failed, when the reason may have
    passed (`RETRY_ALWAYS` at every check, the rest when the person asks)."""
    if full or held.mtime is None:
        return True
    if held.size != found.size or held.mtime != signature(found):
        return True
    if held.status in ("skipped", "failed"):
        code = held.code or ""
        if code in RETRY_ALWAYS:
            return True
        if manual and (held.status == "failed" or code in RETRY_MANUAL):
            return True
    return False


def plan(walked: Walked, held: Sequence[Held], *, full: bool = False, manual: bool = False) -> Plan:
    """The work a check has to do, from what the walk found and what the index holds."""
    out = Plan()
    by_path = {h.path: h for h in held}
    seen: set[str] = set()
    for found in walked.files:
        have = by_path.get(found.path)
        if have is None:
            out.add.append(found)
            continue
        seen.add(found.path)
        if needs_reading(have, found, full=full, manual=manual):
            out.change.append((have, found))
        else:
            out.keep.append(have)
    gone: list[Held] = []
    for have in held:
        if have.path in seen:
            continue
        (out.unknown if _under(have.path, walked.unlisted) else gone).append(have)
    # A file that is new and a document that is gone, of the same size and time, are one file
    # that moved (a renamed folder is every file moved): its hash is checked before it is believed.
    waiting: dict[tuple[int, str | None], list[Held]] = {}
    for have in gone:
        if have.copied:
            waiting.setdefault((have.size, have.mtime), []).append(have)
    unmoved: list[Found] = []
    taken: set[str] = set()
    for found in out.add:
        options = waiting.get((found.size, signature(found)))
        if options:
            moved = options.pop(0)
            taken.add(moved.doc_id)
            out.move.append((moved, found))
        else:
            unmoved.append(found)
    out.add = unmoved
    out.remove = [h for h in gone if h.doc_id not in taken]
    return out
