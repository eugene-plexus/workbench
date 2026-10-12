"""What a folder leaves out, through a real Workbench (MR7): the rules that are always in force, the
person's own list for a folder, the folder's own `.gitignore`, hidden files, and Remove on one file.
Each file that is left out is named with the rule that left it out, and a rule that changes takes effect
at once: what is ignored now leaves the index, and what no longer is comes in.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from .conftest import World
from .folderdocs import (
    add_and_settle,
    add_folder,
    by_path,
    folder_of,
    grant,
    owner_and_ada,
    settle_folder,
    sync_and_settle,
    write,
)
from .saved import md

pytestmark = pytest.mark.usefixtures("quiet_loop")
OLD = 3600.0


def age(folder: Path) -> None:
    then = time.time() - OLD
    for found in folder.rglob("*"):
        if found.is_file():
            os.utime(found, (then, then))


def text(folder: Path, name: str, words: str = "Words.") -> None:
    write(folder, name, md(words, title=name))


def patch(person: Any, grant_id: str, **body: Any) -> Any:
    return person.patch(f"/api/documents/folders/{grant_id}", json=body)


def settled_patch(person: Any, grant_id: str, **body: Any) -> dict[str, Any]:
    before = float(folder_of(person, grant_id)["checkedAt"] or 0.0)
    made = patch(person, grant_id, **body)
    assert made.status_code == 200, made.text
    return settle_folder(person, grant_id, after=before)


def left(view: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """What was left out, by path: its code and its words."""
    return {x["path"]: (x["code"], x["reason"]) for x in view["left"]}


def setup(world: World, folder: Path) -> tuple[Any, Any, str]:
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    return owner, ada, grant(world, owner, folder)


# --------------------------------------------------------------------------- #
# what is always left out
# --------------------------------------------------------------------------- #


def test_the_default_rules_leave_out_other_programs_folders_hidden_and_temporary_files(
    world: World, notes: Path
) -> None:
    for name in (
        "keep.md",
        ".git/config",
        ".git/objects/ab/cd.md",
        "node_modules/pkg/readme.md",
        "src/__pycache__/mod.md",
        ".venv/lib/notes.md",
        "venv/notes.md",
        ".hidden.md",
        "sub/.private/inside.md",
        "~$report.md",
        "draft.tmp",
        "plan.md~",
        "Thumbs.db",
        "desktop.ini",
        ".DS_Store",
        "src/real.md",
    ):
        text(notes, name)
    age(notes)
    _, ada, gid = setup(world, notes)
    done = add_and_settle(ada, gid)
    assert set(by_path(ada, gid)) == {"keep.md", "src/real.md"}
    assert done["counts"]["files"] == 2
    named = left(folder_of(ada, gid))
    assert named[".git"][0] == "ignored_default" and named["node_modules"][0] == "ignored_default"
    assert (
        named["src/__pycache__"][0] == "ignored_default" and named[".venv"][0] == "ignored_default"
    )
    assert named["venv"][0] == "ignored_default"
    assert named[".hidden.md"] == ("ignored_hidden", "Ignored: a hidden file.")
    assert named["sub/.private"] == ("ignored_hidden", "Ignored: a hidden folder.")
    assert (
        named["~$report.md"][0] == "ignored_temporary"
        and named["draft.tmp"][0] == "ignored_temporary"
    )
    assert named["plan.md~"][0] == "ignored_temporary"
    assert named["Thumbs.db"][0] == "ignored_default" and named[".DS_Store"][0] == "ignored_default"
    assert folder_of(ada, gid)["last"]["ignored"] == {"files": 7, "folders": 6}
    assert ".git" in str(named) and "objects" not in str(named), "an ignored folder is not entered"


def test_what_was_ignored_is_in_the_folders_detail_and_not_in_its_listing(
    world: World, notes: Path
) -> None:
    text(notes, "keep.md")
    text(notes, ".hidden.md")
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    one = folder_of(ada, gid)
    assert one["leftCounts"] == {"ignored_hidden": 1} and one["unlisted"] == []
    listed = ada.get("/api/documents/folders").json()["folders"][0]
    assert "left" not in listed and "leftCounts" not in listed, (
        "a list polled every second is small"
    )


def test_a_hidden_file_is_read_when_the_person_turns_hidden_on_for_that_folder(
    world: World, notes: Path
) -> None:
    text(notes, "keep.md")
    text(notes, ".hidden.md", "Words of the hidden file.")
    text(notes, ".git/config.md")
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    assert set(by_path(ada, gid)) == {"keep.md"}
    shown = settled_patch(ada, gid, includeHidden=True)
    assert shown["includeHidden"] is True
    assert set(by_path(ada, gid)) == {"keep.md", ".hidden.md"}, ".git is still never read"
    back = settled_patch(ada, gid, includeHidden=False)
    assert back["includeHidden"] is False and set(by_path(ada, gid)) == {"keep.md"}


def test_hidden_can_be_chosen_when_the_folder_is_added(world: World, notes: Path) -> None:
    text(notes, ".hidden.md")
    age(notes)
    _, ada, gid = setup(world, notes)
    done = add_and_settle(ada, gid, includeHidden=True)
    assert done["includeHidden"] is True and set(by_path(ada, gid)) == {".hidden.md"}


# --------------------------------------------------------------------------- #
# the person's own list
# --------------------------------------------------------------------------- #


def test_a_folders_own_list_leaves_out_what_it_names_and_says_which_pattern_did(
    world: World, notes: Path
) -> None:
    for name in ("keep.md", "debug.log", "sub/trace.log", "drafts/idea.md", "top.md", "sub/top.md"):
        text(notes, name)
    age(notes)
    _, ada, gid = setup(world, notes)
    done = add_and_settle(ada, gid, ignore=["*.log", "drafts/", "/top.md"])
    assert done["ignore"] == ["*.log", "drafts/", "/top.md"]
    assert set(by_path(ada, gid)) == {"keep.md", "sub/top.md"}
    named = left(folder_of(ada, gid))
    assert named["debug.log"] == ("ignored_list", "Ignored: your ignore list (*.log).")
    assert named["drafts"][1] == "Ignored: your ignore list (drafts/)."
    assert (
        named["top.md"][1] == "Ignored: your ignore list (/top.md)." and "sub/top.md" not in named
    )


def test_changing_the_list_takes_files_out_at_once_and_brings_them_back_when_it_is_changed_again(
    world: World, notes: Path
) -> None:
    for name in ("keep.md", "a.log", "b.log"):
        text(notes, name, f"Words of {name}.")
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    before = set(by_path(ada, gid))
    assert before == {"keep.md", "a.log", "b.log"}, "a .log file is text, so it is read"
    gone = {d["id"] for d in by_path(ada, gid).values() if d["path"].endswith(".log")}
    shown = settled_patch(ada, gid, ignore=["*.log"])
    assert shown["ignore"] == ["*.log"] and set(by_path(ada, gid)) == {"keep.md"}
    assert shown["last"]["removed"] == 2 and shown["counts"]["files"] == 1
    for doc_id in gone:
        assert ada.get(f"/api/documents/{doc_id}").status_code == 404
    assert shown["leftCounts"] == {"ignored_list": 2}
    back = settled_patch(ada, gid, ignore=[])
    assert back["ignore"] == [] and set(by_path(ada, gid)) == before
    assert back["last"]["added"] == 2


def test_a_patch_that_does_not_name_the_list_leaves_it_as_it_is(world: World, notes: Path) -> None:
    text(notes, "keep.md")
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid, ignore=["*.log"])
    shown = settled_patch(ada, gid, useGitignore=False)
    assert shown["ignore"] == ["*.log"] and shown["useGitignore"] is False
    assert settled_patch(ada, gid, includeHidden=True)["useGitignore"] is False


@pytest.mark.parametrize(
    ("patterns", "says"),
    [
        (["!keep.md"], "only ignores"),
        (["# comment"], "comment"),
        ([""], "empty"),
        (["x" * 201], "at most 200"),
        (["v[z-a].md"], "not one Workbench understands"),
        ([f"p{n}" for n in range(101)], None),
    ],
)
def test_a_pattern_workbench_cannot_use_is_refused_in_words_and_changes_nothing(
    world: World, notes: Path, patterns: list[str], says: str | None
) -> None:
    text(notes, "keep.md")
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid, ignore=["*.log"])
    refused = patch(ada, gid, ignore=patterns)
    assert refused.status_code == 422, refused.text
    if says is not None:
        assert says in refused.text
    assert folder_of(ada, gid)["ignore"] == ["*.log"], "the list is as it was"


def test_a_bad_pattern_at_the_time_a_folder_is_added_adds_nothing(
    world: World, notes: Path
) -> None:
    _, ada, gid = setup(world, notes)
    refused = add_folder(ada, gid, ignore=["!nope"])
    assert refused.status_code == 422 and "only ignores" in refused.json()["detail"]["message"]
    assert ada.get("/api/documents/folders").json()["folders"] == []
    assert add_folder(ada, gid).status_code == 201, "and it can still be added"


def test_the_list_is_trimmed_and_the_same_pattern_is_kept_once(world: World, notes: Path) -> None:
    text(notes, "keep.md")
    _, ada, gid = setup(world, notes)
    done = add_and_settle(ada, gid, ignore=["  *.log ", "*.log", "tmp/"])
    assert done["ignore"] == ["*.log", "tmp/"]


# --------------------------------------------------------------------------- #
# .gitignore
# --------------------------------------------------------------------------- #


def test_a_folders_gitignore_is_respected_and_can_be_turned_off(world: World, notes: Path) -> None:
    write(notes, ".gitignore", "build/\n*.out.md\n!keep.out.md\n")
    for name in ("keep.md", "build/x.md", "a.out.md", "keep.out.md", "src/b.out.md"):
        text(notes, name)
    age(notes)
    _, ada, gid = setup(world, notes)
    done = add_and_settle(ada, gid)
    assert set(by_path(ada, gid)) == {"keep.md", "keep.out.md"}, ".gitignore itself is hidden too"
    named = left(folder_of(ada, gid))
    assert named["build"] == ("ignored_gitignore", "Ignored: the folder's .gitignore (build/).")
    assert (
        named["a.out.md"][0] == "ignored_gitignore"
        and named["src/b.out.md"][0] == "ignored_gitignore"
    )
    assert done["useGitignore"] is True
    off = settled_patch(ada, gid, useGitignore=False)
    assert off["useGitignore"] is False
    assert set(by_path(ada, gid)) == {
        "keep.md",
        "keep.out.md",
        "build/x.md",
        "a.out.md",
        "src/b.out.md",
    }
    on = settled_patch(ada, gid, useGitignore=True)
    assert set(by_path(ada, gid)) == {"keep.md", "keep.out.md"} and on["last"]["removed"] == 3


def test_a_gitignore_in_a_folder_below_applies_to_that_folder_only(
    world: World, notes: Path
) -> None:
    write(notes, "docs/.gitignore", "*.draft.md\n")
    for name in ("a.draft.md", "docs/a.draft.md", "docs/b.md", "docs/deep/c.draft.md"):
        text(notes, name)
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    assert set(by_path(ada, gid)) == {"a.draft.md", "docs/b.md"}


def test_a_gitignore_and_a_persons_list_both_apply_and_the_list_has_the_last_word(
    world: World, notes: Path
) -> None:
    write(notes, ".gitignore", "!secret.md\n*.tmp.md\n")
    for name in ("secret.md", "plain.md", "x.tmp.md"):
        text(notes, name)
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid, ignore=["secret.md"])
    assert set(by_path(ada, gid)) == {"plain.md"}, "a ! in .gitignore does not take back a pattern"


# --------------------------------------------------------------------------- #
# Remove on one file of a folder
# --------------------------------------------------------------------------- #


def test_removing_one_file_of_a_folder_stops_it_being_read_and_names_it_in_the_list(
    world: World, notes: Path
) -> None:
    for name in ("keep.md", "private notes.md", "sub/other.md"):
        text(notes, name)
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    target = by_path(ada, gid)["private notes.md"]
    removed = ada.delete(f"/api/documents/{target['id']}")
    assert removed.status_code == 204, removed.text
    assert ada.get(f"/api/documents/{target['id']}").status_code == 404
    assert set(by_path(ada, gid)) == {"keep.md", "sub/other.md"}
    one = folder_of(ada, gid)
    assert one["ignore"] == ["/private notes.md"], "so that it does not come back at the next check"
    after = sync_and_settle(ada, gid, deep=True)
    assert set(by_path(ada, gid)) == {"keep.md", "sub/other.md"}, "and it did not"
    assert left(after)["private notes.md"][0] == "ignored_list"
    assert settled_patch(ada, gid, ignore=[])["ignore"] == []
    assert "private notes.md" in by_path(ada, gid), "taking it off the list brings it back"


def test_a_file_with_odd_characters_in_its_name_is_removed_by_its_exact_path(
    world: World, notes: Path
) -> None:
    names = ["a [1] (draft)!.md", "#notes.md", "dollar$ and spaces .md", "plain.md"]
    for name in names:
        text(notes, name)
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid, includeHidden=True)
    assert set(by_path(ada, gid)) == set(names)
    for name in names[:-1]:
        assert ada.delete(f"/api/documents/{by_path(ada, gid)[name]['id']}").status_code == 204
    settled_patch(ada, gid, includeHidden=True)
    assert set(by_path(ada, gid)) == {"plain.md"}, "each pattern named its own file and no other"


def test_a_full_ignore_list_says_so_and_the_file_is_not_removed(world: World, notes: Path) -> None:
    text(notes, "keep.md")
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid, ignore=[f"p{n}" for n in range(100)])
    doc = by_path(ada, gid)["keep.md"]
    refused = ada.delete(f"/api/documents/{doc['id']}")
    assert (
        refused.status_code == 409 and "ignore list is full" in refused.json()["detail"]["message"]
    )
    assert "keep.md" in by_path(ada, gid), "it would come back, so it was not removed"


def test_removing_a_file_that_was_added_by_hand_is_as_it_was(world: World) -> None:
    ada = owner_and_ada(world)[1]
    made = ada.post("/api/documents", files=[("files", ("mine.md", md("Mine."), "text/markdown"))])
    assert made.status_code == 201
    doc = made.json()["documents"][0]
    assert ada.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert ada.get("/api/documents/folders").json()["folders"] == []
