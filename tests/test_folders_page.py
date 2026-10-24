"""The page and the server agree about folders (MR7).

Workbench's own API is private and has no schema file to generate the page's types from, so the
page's TypeScript (`web/src/lib/types.ts`, `documentFolders.ts`) is written by hand. These read it
as text and hold it to what the real server answers with, as `test_saved_page.py` does for the rest
of the Documents screen: a field the server renames, adds or drops, or a state, a reason or a
kind of thing left out that it starts to say, is a failure here and not a blank spot on a screen or
a heading the page does not have.

The answers are real: a folder is granted, added, checked while a read is held (so a check is under
way), listed, opened with what it left out, its files paged, and withdrawn.
"""

from __future__ import annotations

import ast
import re
import threading
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import folder_io
from eugene_plexus_workbench.knowledge import index as index_module

from .conftest import PNG, World
from .folderdocs import (
    add_folder,
    files_of,
    folder_of,
    folders_of,
    grant,
    junction,
    owner_and_ada,
    settle_folder,
    write,
)
from .saved import md
from .test_documents_page import LIB, interface
from .test_saved_page import body, server_text, shape, union

pytestmark = pytest.mark.usefixtures("quiet_loop")
FOLDERS = (LIB / "documentFolders.ts").read_text(encoding="utf-8")


def fields(name: str) -> tuple[set[str], set[str]]:
    """The fields the page's interface `name` must have, and the ones it may have (`removed?:`)."""
    text = body(name)
    required = set(re.findall(r"^  (\w+): ", text, re.MULTILINE))
    optional = set(re.findall(r"^  (\w+)\?: ", text, re.MULTILINE))
    return required, optional


def table_keys(name: str) -> list[str]:
    """The keys of `export const NAME: Record<string, string> = { key: ..., }` in documentFolders.ts."""
    found = re.search(rf"export const {name}[^=]*= \{{(.*?)\n\}};", FOLDERS, re.DOTALL)
    assert found, f"documentFolders.ts has no table {name}"
    return re.findall(r"^  (\w+):", found.group(1), re.MULTILINE)


# --------------------------------------------------------------------------- #
# what the server answers, in each shape it has
# --------------------------------------------------------------------------- #


def folder_world(world: World, notes: Path, link_target: Path) -> tuple[Any, Any, str]:
    """A person's granted folder with a file that is read, a picture that is skipped, and things
    that are left out (a hidden file, a program's folder, a link)."""
    world.gateway.embedder_listed = False
    write(notes, "plan.md", md("The harbour budget is approved.", title="Plan"))
    write(notes, "picture.png", PNG)
    write(notes, ".hidden", "x\n")
    write(notes, "node_modules/leftpad/index.js", "module.exports = 1;\n")
    junction(notes / "shortcut", link_target)
    owner, ada = owner_and_ada(world)
    return owner, ada, grant(world, owner, notes, name="Work notes")


def test_the_pages_types_are_exactly_what_the_server_answers(
    world: World, notes: Path, more_notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, ada, gid = folder_world(world, notes, more_notes)
    # A check under way: held while it reads its second file.
    reading, release = threading.Event(), threading.Event()
    real = folder_io.read_file
    calls: list[int] = []

    def slow(*args: Any) -> Any:
        calls.append(1)
        if len(calls) == 2:
            reading.set()
            assert release.wait(30)
        return real(*args)

    monkeypatch.setattr(folder_io, "read_file", slow)
    assert add_folder(ada, gid).status_code == 201
    try:
        assert reading.wait(30)
        under_way = folder_of(ada, gid)
    finally:
        release.set()
    assert under_way["state"] == "syncing" and under_way["syncing"] is not None
    done = settle_folder(ada, gid)

    listing = folders_of(ada)
    folder = listing["folders"][0]
    detail = folder_of(ada, gid)
    page = files_of(ada, gid)

    # The list, its folders, and what each is made of.
    required, optional = fields("DocumentFolders")
    assert set(listing) == required | optional == interface("DocumentFolders")
    assert set(folder) == interface("DocumentFolder")
    assert set(listing["grants"][0]) == interface("FolderChoice")
    assert set(listing["limits"]) == interface("FolderLimits")
    assert set(listing["limits"]["rules"]) == shape("FolderLimits", "rules")
    for field in ("counts", "caps", "last"):
        assert set(folder[field]) == interface(
            {"counts": "FolderCounts", "caps": "FolderCaps", "last": "FolderLast"}[field]
        )
    assert set(folder["caps"]["files"]) == shape("FolderCaps", "files")
    assert set(folder["caps"]["bytes"]) == shape("FolderCaps", "bytes")
    assert set(folder["caps"]["depth"]) == shape("FolderCaps", "depth")
    assert set(folder["caps"]["entries"]) == shape("FolderCaps", "entries")
    assert set(folder["caps"]["fileBytes"]) == shape("FolderCaps", "fileBytes")
    assert set(folder["last"]["ignored"]) == shape("FolderLast", "ignored")
    # A check under way.
    assert set(under_way["syncing"]) == interface("FolderRun")
    # What is counted: by every state a document can be in.
    assert set(folder["counts"]["byState"]) == set(index_module.STATES)
    assert folder["counts"]["byCode"] == {"not_document": 1}

    # The folder opened: the list's fields and what was left out.
    assert set(detail) == interface("DocumentFolder") | interface("DocumentFolderDetail")
    assert set(detail["left"][0]) == interface("LeftOut")
    assert {item["kind"] for item in detail["left"]} == {"file", "folder", "link"}
    assert detail["leftCounts"] and all(isinstance(n, int) for n in detail["leftCounts"].values())
    assert done["state"] == "synced"

    # A page of the folder's files: a document, and its folder and path.
    assert set(page) == interface("FolderFiles")
    assert set(page["files"][0]) == interface("SavedDocument") | interface("FolderFile")
    assert set(page["counts"]) == interface("FolderCounts")

    # A folder the owner took the grant away from: it says so, with the count and the time.
    withdrawn = owner.delete(f"/api/folders/{gid}")
    assert withdrawn.status_code == 204, withdrawn.text
    gone = folder_of(ada, gid)
    assert gone["state"] == "withdrawn"
    problem = gone["problem"]
    required, optional = fields("FolderProblem")
    assert set(problem) == required | optional == interface("FolderProblem")
    assert required <= set(problem)


# --------------------------------------------------------------------------- #
# every state, reason and kind of thing the server can say is one the page has words for
# --------------------------------------------------------------------------- #


def parsed(name: str) -> ast.Module:
    return ast.parse(server_text(name))


def called(tree: ast.AST, name: str) -> list[ast.Call]:
    """Every call of a function or a method that is called `name`."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
    ]


def text_of(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def argument(call: ast.Call, at: int) -> str | None:
    return text_of(call.args[at]) if len(call.args) > at else None


def returned(function: str, name: str) -> set[str]:
    """The strings a function of the server's source returns, in any branch of what it returns."""
    found: set[str] = set()
    for node in ast.walk(parsed(name)):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and inner.value is not None:
                    found |= {
                        part.value
                        for part in ast.walk(inner.value)
                        if isinstance(part, ast.Constant) and isinstance(part.value, str)
                    }
    return found


def test_every_state_a_folder_can_be_in_is_one_the_page_has_words_for() -> None:
    said = returned("folder_state", "folder_docs_api.py")
    assert said == {"waiting", "syncing", "synced", "problem", "unavailable", "withdrawn"}
    assert said == union("FolderState")
    # And each has its badge and its lines.
    badge = FOLDERS.split("export function stateBadge")[1].split("export function syncingWords")[0]
    lines = FOLDERS.split("export function stateLines")[1].split("export function lastWords")[0]
    for state in said:
        assert f'case "{state}":' in badge, state
        assert f'case "{state}":' in lines, state


def skip_codes() -> set[str]:
    """Every reason a document of a folder can be skipped or can fail for, read off the server's
    own source: what the walk and the sync say, what the reader says, and the passage limit."""
    found: set[str] = set()
    sync = parsed("knowledge/folder_sync.py")
    for call in called(sync, "Declined"):
        found.add(argument(call, 0) or "")
    for call in called(sync, "_skip"):
        # `self._skip(found, held, "code", words)`
        code = argument(call, 2)
        if code:
            found.add(code)
    for node in ast.walk(sync):
        # `by_code = {"hard_links": ..., "special": ..., "link": ..., "mount": ...}`
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "by_code" for t in node.targets)
            and isinstance(node.value, ast.Dict)
        ):
            found |= {text_of(key) or "" for key in node.value.keys}
    for module in ("knowledge/extract.py", "knowledge/page_reader.py"):
        for call in called(parsed(module), "Unreadable"):
            found.add(argument(call, 0) or "")
    for call in called(parsed("knowledge/page_reader.py"), "failure"):
        found.add(argument(call, 1) or "")
    for node in ast.walk(parsed("knowledge/worker.py")):
        # `{"ok": False, "code": "memory", ...}`
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=False):
                if text_of(key) == "code" and text_of(value):
                    found.add(text_of(value) or "")
    for call in called(parsed("knowledge/reader.py"), "failure"):
        # `failure("failed", "missing", words)`: the reader's own; one passes the child's own code on.
        code = argument(call, 1)
        if code:
            found.add(code)
    for call in called(parsed("knowledge/ingest.py"), "end_unread"):
        for keyword in call.keywords:
            if keyword.arg == "code" and text_of(keyword.value):
                found.add(text_of(keyword.value) or "")
    found |= set(re.findall(r"code = '(\w+)'", server_text("knowledge/index.py")))
    found.discard("")
    return found


def test_every_reason_a_file_can_be_skipped_or_fail_for_is_one_the_page_has_words_for() -> None:
    said = skip_codes()
    # The sources really name them (a collector that finds none checks nothing).
    assert {
        "not_document",
        "unsupported",
        "busy",
        "no_text",
        "timeout",
        "too_many_passages",
    } <= said
    keys = table_keys("FILE_REASONS")
    assert len(keys) == len(set(keys)), "a reason is told twice"
    assert set(keys) == said, sorted(set(keys) ^ said)


def left_codes() -> set[str]:
    """Every kind of thing a check can leave out, read off the walk and the rules."""
    found: set[str] = set()
    walk = parsed("knowledge/folder_walk.py")
    for call in called(walk, "leave"):
        # `out.leave(path, kind, "code", words)`; an ignored name's code is `ignored_` and the rule's.
        code = argument(call, 2)
        if code:
            found.add(code)
    for node in ast.walk(walk):
        # `link_words`: `return ("cloud", ...)` and `return ("link", ...)`.
        if isinstance(node, ast.FunctionDef) and node.name == "link_words":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Tuple):
                    found.add(text_of(inner.value.elts[0]) or "")
    for call in called(parsed("knowledge/folder_rules.py"), "Why"):
        found.add(f"ignored_{argument(call, 0)}")
    found.discard("")
    return found


def test_every_kind_of_thing_a_check_can_leave_out_is_one_the_page_has_a_heading_for() -> None:
    said = left_codes()
    assert {"ignored_default", "ignored_hidden", "link", "cloud", "too_deep", "over_files"} <= said
    keys = table_keys("LEFT_HEADINGS")
    assert len(keys) == len(set(keys)), "a heading is told twice"
    assert set(keys) == said, sorted(set(keys) ^ said)


def test_the_states_the_page_counts_and_filters_by_are_the_ones_a_document_can_be_in() -> None:
    # The page counts files that are read, skipped or failed as done, and the rest as being read.
    for state in ("ready", "skipped", "failed", "queued", "reading"):
        assert state in index_module.STATES
    assert re.search(r'const ended = \["ready", "skipped", "failed"\];', FOLDERS)
    # The filters the page offers are for states the server filters by (`state=` takes one).
    for state in re.findall(
        r'value: "(\w+)", label', FOLDERS.split("export const FILE_FILTERS")[1]
    )[1:]:
        assert state in ("ready", "reading", "skipped", "failed"), state


def test_what_the_page_says_is_always_left_out_is_the_servers_own_list() -> None:
    from eugene_plexus_workbench.knowledge import folder_rules

    described = folder_rules.describe()
    assert set(described) == shape("FolderLimits", "rules")
    assert described["folders"] == list(folder_rules.DEFAULT_FOLDERS)
