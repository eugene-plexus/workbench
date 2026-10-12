"""What a folder walk leaves out (MR7, design 4): the rules, and nothing else.

Three kinds, judged for every name the walk meets and said in words wherever one is
left out:

- **Always**, whatever else is set: other programs' bookkeeping and caches (`.git`,
  `node_modules`, `__pycache__`, `.venv` ...), the recycle bin, a few files an operating system
  makes (`Thumbs.db`, `desktop.ini`, `.DS_Store`), and temporary files (`~$report.docx`, `*.tmp`,
  `*.swp`, `*~` ...). A name is matched without regard to case.
- **Hidden**: a name with a dot in front, or the Windows hidden or system attribute, unless
  the person turned *Include hidden* on for that folder.
- **Patterns**: the person's own list for the folder, and the folder's own `.gitignore`
  files unless the person turned that off. Both are written the way `.gitignore` is
  (`*`, `**`, `?`, `[ab]`, a leading `/` to anchor, a trailing `/` for folders, `\\` to
  escape); a `.gitignore` may also re-include with `!`, the person's list may not.

A `.gitignore` applies to its own directory and below, and a deeper one wins over a higher
one, and a later line over an earlier, as in git. A folder that is ignored is not entered, so
nothing in it can be re-included, as in git. Pure: nothing here reads a file.
"""

from __future__ import annotations

import fnmatch
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: Folders that are never entered.
DEFAULT_FOLDERS = (
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "site-packages",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    ".idea",
    ".vscode",
    "$RECYCLE.BIN",
    "System Volume Information",
    ".Trash",
    ".Trashes",
)
#: Files an operating system makes.
DEFAULT_FILES = ("Thumbs.db", "desktop.ini", ".DS_Store")
#: Names of temporary files, as shell patterns.
TEMPORARY = (
    "~$*",
    ".~lock.*#",
    "*.tmp",
    "*.temp",
    "*.swp",
    "*.swo",
    "*~",
    ".#*",
    "*.part",
    "*.partial",
    "*.crdownload",
)

#: What a person's list may hold.
MAX_PATTERNS = 100
MAX_PATTERN = 200
#: What one `.gitignore` may be, in bytes, and how many lines of it count.
MAX_GITIGNORE_BYTES = 65_536
MAX_GITIGNORE_LINES = 2_000

_TEMPORARY = re.compile("|".join(fnmatch.translate(p.casefold()) for p in TEMPORARY))
_FOLDERS = frozenset(n.casefold() for n in DEFAULT_FOLDERS)
_FILES = frozenset(n.casefold() for n in DEFAULT_FILES)
_IGNORE_CASE = sys.platform == "win32"


class BadPattern(ValueError):
    """A pattern in the person's list that Workbench cannot use, and why, in words."""


@dataclass(frozen=True)
class Why:
    """Why a name is left out: a code (`default`, `temporary`, `hidden`, `list`, `gitignore`)
    and the words for it."""

    code: str
    text: str

    @property
    def reason(self) -> str:
        return f"Ignored: {self.text}."


@dataclass(frozen=True)
class Pattern:
    """One line of a list, compiled: whether it re-includes (`!`), whether it names only folders
    (a trailing slash), the regular expression for a path below where it was written, and the
    line as written, for saying which one matched."""

    text: str
    negate: bool
    dir_only: bool
    regex: re.Pattern[str]

    def matches(self, path: str, is_dir: bool) -> bool:
        return (is_dir or not self.dir_only) and self.regex.fullmatch(path) is not None


def _class(pattern: str, at: int) -> tuple[str, int] | None:
    """The regular expression for the `[...]` that opens at `at`, and where it ends; None when
    it never closes (then `[` is just itself)."""
    end = at + 1
    if end < len(pattern) and pattern[end] in "!^":
        end += 1
    if end < len(pattern) and pattern[end] == "]":
        end += 1
    while end < len(pattern) and pattern[end] != "]":
        end += 1
    if end >= len(pattern):
        return None
    body = pattern[at + 1 : end]
    negated = body[:1] in ("!", "^")
    if negated:
        body = body[1:]
    body = body.replace("\\", "\\\\").replace("[", "\\[")
    if body.startswith("^"):
        body = "\\" + body
    return "[" + ("^" if negated else "") + body + "]", end + 1


def _translate(pattern: str) -> str:
    """A pattern without its leading or trailing slash, as the body of a regular expression over
    a path: `*` and `?` stay inside a name, `**` as a whole name crosses folders."""
    out: list[str] = []
    at, size = 0, len(pattern)
    while at < size:
        char = pattern[at]
        if char == "*":
            run = at
            while run < size and pattern[run] == "*":
                run += 1
            whole = (at == 0 or pattern[at - 1] == "/") and (run == size or pattern[run] == "/")
            if run - at >= 2 and whole:
                if run == size:
                    out.append(".*")  # `a/**`: everything inside
                    at = run
                else:
                    out.append("(?:.*/)?")  # `**/`: no folders, or any number
                    at = run + 1
                continue
            out.append("[^/]*")
            at = run
        elif char == "?":
            out.append("[^/]")
            at += 1
        elif char == "[":
            found = _class(pattern, at)
            if found is None:
                out.append(re.escape(char))
                at += 1
            else:
                out.append(found[0])
                at = found[1]
        elif char == "\\" and at + 1 < size:
            out.append(re.escape(pattern[at + 1]))
            at += 2
        else:
            out.append(re.escape(char))
            at += 1
    return "".join(out)


def compile_line(raw: str, *, allow_negation: bool = True) -> Pattern | None:
    """One line of a `.gitignore` (or one entry of a person's list) as a pattern; None for a line
    that holds none (blank, or a comment). Raises `BadPattern` for one that cannot be used."""
    line = raw.rstrip("\r\n")
    while line.endswith(" ") and not line.endswith("\\ "):
        line = line[:-1]
    if not line or line.startswith("#"):
        return None
    negate = line.startswith("!")
    body = line[1:] if negate else line
    if negate and not allow_negation:
        raise BadPattern("Start a pattern with a name, not with !. This list only ignores.")
    dir_only = body.endswith("/")
    body = body.rstrip("/")
    anchored = "/" in body
    body = body.lstrip("/")
    if not body:
        return None
    try:
        regex = re.compile(
            ("^" if anchored else "^(?:.*/)?") + _translate(body) + "$",
            re.IGNORECASE if _IGNORE_CASE else 0,
        )
    except re.error as exc:
        raise BadPattern(f"This pattern is not one Workbench understands ({exc}).") from None
    return Pattern(line, negate, dir_only, regex)


def parse_gitignore(text: str) -> list[Pattern]:
    """The patterns of a `.gitignore`: a line it cannot use is skipped, as git does."""
    found: list[Pattern] = []
    for raw in text.splitlines()[:MAX_GITIGNORE_LINES]:
        try:
            pattern = compile_line(raw)
        except BadPattern:
            continue
        if pattern is not None:
            found.append(pattern)
    return found


def _trim(raw: str) -> str:
    """A pattern without the spaces round it: all of those in front, and those at the end but
    one that a backslash makes part of the name (`name\\ `), as `.gitignore` reads it. What
    `exact` writes for a file whose name ends in a space must come back from a save as it was."""
    text = raw.lstrip()
    while text[-1:].isspace() and not text.endswith("\\ "):
        text = text[:-1]
    return text


def validate_list(given: Iterable[str]) -> list[str]:
    """A person's ignore list, cleaned: each pattern trimmed, none empty, none longer than
    `MAX_PATTERN`, none a comment or a re-include, each one Workbench can compile, no more than
    `MAX_PATTERNS`, the same one only once. Raises `BadPattern` with the words for the first
    that is not."""
    out: list[str] = []
    for raw in given:
        text = _trim(raw)
        if not text:
            raise BadPattern("An ignore pattern cannot be empty.")
        if len(text) > MAX_PATTERN:
            raise BadPattern(f"A pattern is at most {MAX_PATTERN} characters.")
        if any(ord(c) < 32 or ord(c) == 127 for c in text):
            raise BadPattern("A pattern cannot hold control characters.")
        if text.startswith("#"):
            raise BadPattern(
                "A pattern cannot start with #, which is a comment. Write \\# for one."
            )
        compile_line(text, allow_negation=False)
        if text not in out:
            out.append(text)
    if len(out) > MAX_PATTERNS:
        raise BadPattern(f"A folder's ignore list holds at most {MAX_PATTERNS} patterns.")
    return out


def exact(path: str) -> str:
    """The pattern that names exactly this path below the folder (and nothing else), as an
    ignore list holds it: `Remove` on one file of a folder adds this."""
    escaped = re.sub(r"([\\*?\[\]])", r"\\\1", path)
    if escaped.endswith(" "):
        escaped = escaped[:-1] + "\\ "
    return "/" + escaped


#: The `.gitignore` files that apply, outermost first: each with the directory it is in (a path
#: below the folder; `""` for the folder itself) and its patterns.
Chain = tuple[tuple[str, Sequence[Pattern]], ...]


class Rules:
    """Everything that is left out of one folder, judged name by name."""

    def __init__(
        self,
        ignore: Sequence[str] = (),
        *,
        include_hidden: bool = False,
        use_gitignore: bool = True,
    ) -> None:
        self.include_hidden = include_hidden
        self.use_gitignore = use_gitignore
        self._list: list[Pattern] = []
        for text in ignore:
            try:
                pattern = compile_line(text, allow_negation=False)
            except BadPattern:
                continue  # one that was kept when the rules were looser is not an error now
            if pattern is not None:
                self._list.append(pattern)

    def judge(
        self, path: str, name: str, is_dir: bool, *, hidden: bool = False, chain: Chain = ()
    ) -> Why | None:
        """Why this name is left out, or None when it is taken. `path` is below the folder, with
        `/` between names; `hidden` is the Windows attribute; `chain` the `.gitignore` files
        above."""
        low = name.casefold()
        if is_dir and low in _FOLDERS:
            return Why("default", f"a folder Workbench never reads ({name})")
        if not is_dir and low in _FILES:
            return Why("default", f"a file the system makes ({name})")
        if not is_dir and _TEMPORARY.fullmatch(low):
            return Why("temporary", "a temporary file")
        if not self.include_hidden and (name.startswith(".") or hidden):
            return Why("hidden", "a hidden folder" if is_dir else "a hidden file")
        for pattern in self._list:
            if pattern.matches(path, is_dir):
                return Why("list", f"your ignore list ({pattern.text})")
        if self.use_gitignore and chain:
            decided: Pattern | None = None
            for base, patterns in chain:
                if base and not path.startswith(base + "/"):
                    continue
                inside = path[len(base) + 1 :] if base else path
                for pattern in patterns:
                    if pattern.matches(inside, is_dir):
                        decided = None if pattern.negate else pattern
            if decided is not None:
                return Why("gitignore", f"the folder's .gitignore ({decided.text})")
        return None


def describe() -> dict[str, object]:
    """The rules always in force, for the screen to state (`GET /api/documents/folders`)."""
    return {
        "folders": list(DEFAULT_FOLDERS),
        "files": list(DEFAULT_FILES),
        "temporary": list(TEMPORARY),
        "hidden": "Names that start with a dot, and files and folders Windows marks hidden.",
        "gitignore": "A folder's own .gitignore files, at any depth.",
        "list": f"At most {MAX_PATTERNS} patterns, written like .gitignore lines.",
    }
