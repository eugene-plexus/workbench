"""A folder's files and what to do about each (MR7, `knowledge/folder_walk.py`): the walk, with the
caps and the reasons, and the plan, which says what is new, changed, moved, gone or unchanged.
Pure: a folder here is a dictionary, and no file is read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eugene_plexus_workbench import folder_io
from eugene_plexus_workbench.folder_io import DIRECTORY, FILE, LINK, OTHER, Entry
from eugene_plexus_workbench.knowledge import folder_walk as fw
from eugene_plexus_workbench.knowledge.folder_rules import Rules
from eugene_plexus_workbench.knowledge.folder_walk import Caps, Found, Walked
from eugene_plexus_workbench.knowledge.index import Held

BIG = Caps(max_files=10_000, max_depth=12)


def f(name: str, size: int = 10, mtime: int = 1, **kw: object) -> Entry:
    return Entry(name, FILE, size, mtime, **kw)  # type: ignore[arg-type]


def d(name: str) -> Entry:
    return Entry(name, DIRECTORY)


class Fake:
    """A folder in memory: the entries of each directory by its path (`""` for the folder), the
    bytes of the small files, and the errors to raise. Records which directories were listed."""

    def __init__(
        self,
        tree: dict[str, list[Entry]],
        small: dict[str, bytes] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.tree, self.small, self.errors = tree, small or {}, errors or {}
        self.listed: list[str] = []
        self.read: list[str] = []

    def entries(self, names: list[str], limit: int) -> list[Entry]:
        path = "/".join(names)
        self.listed.append(path)
        if path in self.errors:
            raise self.errors[path]
        if path not in self.tree:
            raise FileNotFoundError(path)
        return list(self.tree[path])[:limit]

    def read_small(self, names: list[str], limit: int) -> bytes:
        path = "/".join(names)
        self.read.append(path)
        if path not in self.small:
            raise FileNotFoundError(path)
        data = self.small[path]
        if len(data) > limit:
            raise folder_io.TooLarge(len(data))
        return data


def paths(walked: Walked) -> list[str]:
    return [x.path for x in walked.files]


# --------------------------------------------------------------------------- #
# the walk: what is found, and in what order
# --------------------------------------------------------------------------- #


def test_files_are_found_level_by_level_in_name_order() -> None:
    tree = {
        "": [f("b.md"), d("z"), f("a.md"), d("m")],
        "m": [f("x.md"), d("deep")],
        "z": [f("y.md")],
        "m/deep": [f("w.md")],
    }
    got = fw.walk(Fake(tree), Rules(), BIG)
    assert paths(got) == ["a.md", "b.md", "m/x.md", "z/y.md", "m/deep/w.md"]
    assert got.listed == {"", "m", "z", "m/deep"} and got.directories == 4


def test_names_are_ordered_without_regard_to_case() -> None:
    got = fw.walk(Fake({"": [f("B.md"), f("a.md"), f("C.md")]}), Rules(), BIG)
    assert paths(got) == ["a.md", "B.md", "C.md"]


def test_a_files_size_time_and_the_total_are_taken_from_the_listing() -> None:
    got = fw.walk(Fake({"": [f("a.md", 7, 111), f("b.md", 5, 222)]}), Rules(), BIG)
    assert got.files == [Found("a.md", 7, 111), Found("b.md", 5, 222)]
    assert got.bytes == 12 and got.entries == 2


def test_a_folder_that_is_ignored_is_not_even_listed() -> None:
    fake = Fake({"": [d("node_modules"), d("src"), d(".git")], "src": [f("a.py")]})
    got = fw.walk(fake, Rules(), BIG)
    assert fake.listed == ["", "src"], "node_modules and .git were never entered"
    assert paths(got) == ["src/a.py"]
    assert got.ignored_folders == 2 and got.counts == {"ignored_default": 2}


def test_what_is_ignored_is_counted_and_named_with_its_reason() -> None:
    tree = {"": [f(".hidden.md"), f("~$x.docx"), f("keep.md"), f("a.log")]}
    got = fw.walk(Fake(tree), Rules(["*.log"]), BIG)
    assert paths(got) == ["keep.md"] and got.ignored_files == 3
    by = {x.path: (x.code, x.reason) for x in got.left}
    assert by[".hidden.md"] == ("ignored_hidden", "Ignored: a hidden file.")
    assert by["~$x.docx"][0] == "ignored_temporary"
    assert by["a.log"] == ("ignored_list", "Ignored: your ignore list (*.log).")


def test_a_windows_hidden_file_is_ignored_unless_hidden_is_on() -> None:
    tree = {"": [f("secret.md", hidden=True), f("plain.md")]}
    assert paths(fw.walk(Fake(tree), Rules(), BIG)) == ["plain.md"]
    assert paths(fw.walk(Fake(tree), Rules(include_hidden=True), BIG)) == ["plain.md", "secret.md"]


def test_a_sample_of_what_was_left_out_is_kept_and_every_one_is_counted() -> None:
    tree = {"": [f(f"t{i}.tmp") for i in range(fw.SAMPLE + 150)]}
    got = fw.walk(Fake(tree), Rules(), BIG)
    assert len(got.left) == fw.SAMPLE
    assert got.counts["ignored_temporary"] == fw.SAMPLE + 150 == got.ignored_files


# --------------------------------------------------------------------------- #
# links and what is not an ordinary file
# --------------------------------------------------------------------------- #


def test_a_link_is_never_entered_or_read_and_is_named() -> None:
    fake = Fake({"": [Entry("jlink", LINK, tag=0xA0000003), f("a.md")], "jlink": [f("secret.md")]})
    got = fw.walk(fake, Rules(), BIG)
    assert paths(got) == ["a.md"] and fake.listed == [""], "the link's target was never listed"
    assert got.links == 1
    [left] = got.left
    assert left.kind == "link" and left.code == "link" and "never follows links" in left.reason


def test_a_cloud_placeholder_is_said_to_be_online_only_and_not_a_link() -> None:
    cloud = Entry("report.docx", LINK, tag=folder_io.CLOUD_TAG | 0x2000)
    got = fw.walk(Fake({"": [cloud]}), Rules(), BIG)
    [left] = got.left
    assert left.code == "cloud" and "download" in left.reason and got.files == []


def test_a_pipe_or_device_is_not_read() -> None:
    got = fw.walk(Fake({"": [Entry("fifo", OTHER)]}), Rules(), BIG)
    [left] = got.left
    assert left.code == "special" and got.files == []


def test_a_name_with_a_control_character_is_not_opened() -> None:
    got = fw.walk(Fake({"": [f("bad\nname.md"), f("good.md")]}), Rules(), BIG)
    assert paths(got) == ["good.md"] and got.counts == {"odd_name": 1}


@pytest.mark.skipif(
    __import__("sys").platform != "win32", reason="a colon in a name is a stream on Windows only"
)
def test_a_colon_in_a_name_is_a_stream_on_windows_and_is_not_opened() -> None:
    got = fw.walk(Fake({"": [f("a:stream.md")]}), Rules(), BIG)
    assert got.files == [] and got.counts == {"odd_name": 1}


def test_a_link_with_an_ignored_name_is_ignored_not_reported_as_a_link() -> None:
    got = fw.walk(Fake({"": [Entry("~$lock.tmp", LINK)]}), Rules(), BIG)
    assert got.links == 0 and got.counts == {"ignored_temporary": 1}


# --------------------------------------------------------------------------- #
# the caps
# --------------------------------------------------------------------------- #


def test_the_files_cap_keeps_the_shallow_files_first_and_says_what_was_left() -> None:
    tree = {"": [f("r1.md"), f("r2.md"), d("sub")], "sub": [f(f"s{i}.md") for i in range(5)]}
    got = fw.walk(Fake(tree), Rules(), Caps(max_files=4, max_depth=12))
    assert paths(got) == ["r1.md", "r2.md", "sub/s0.md", "sub/s1.md"]
    assert got.over_files and got.counts == {"over_files": 3}
    assert "more than the 4 files" in got.left[0].reason


def test_exactly_the_cap_is_not_over_it() -> None:
    got = fw.walk(Fake({"": [f("a.md"), f("b.md")]}), Rules(), Caps(max_files=2, max_depth=12))
    assert len(got.files) == 2 and not got.over_files


def test_ignored_files_do_not_count_against_the_files_cap() -> None:
    tree = {"": [f("a.tmp"), f("b.tmp"), f("c.md"), f("d.md")]}
    got = fw.walk(Fake(tree), Rules(), Caps(max_files=2, max_depth=12))
    assert paths(got) == ["c.md", "d.md"] and not got.over_files


def test_a_folder_deeper_than_the_cap_is_not_entered_and_is_named() -> None:
    tree = {"": [d("a")], "a": [d("b")], "a/b": [d("c")], "a/b/c": [f("deep.md")]}
    fake = Fake(tree)
    got = fw.walk(fake, Rules(), Caps(max_files=100, max_depth=2))
    assert fake.listed == ["", "a", "a/b"] and got.files == []
    assert got.too_deep == 1 and got.counts == {"too_deep": 1}
    assert got.left[0].path == "a/b/c" and "more than 2 folders deep" in got.left[0].reason


def test_a_folder_exactly_at_the_depth_cap_is_entered() -> None:
    tree = {"": [d("a")], "a": [d("b")], "a/b": [f("ok.md")]}
    got = fw.walk(Fake(tree), Rules(), Caps(max_files=100, max_depth=2))
    assert paths(got) == ["a/b/ok.md"] and got.too_deep == 0


def test_a_walk_stops_when_it_has_looked_at_as_many_names_as_it_may() -> None:
    tree = {"": [f(f"{i:02}.md") for i in range(8)]}
    got = fw.walk(Fake(tree), Rules(), Caps(max_files=100, max_depth=12, max_entries=5))
    assert got.over_entries and got.entries == 5 and len(got.files) == 5


def test_a_second_directory_is_not_listed_once_the_names_are_used_up() -> None:
    fake = Fake({"": [f("a.md"), d("x"), d("y")], "x": [f("1.md"), f("2.md")], "y": [f("3.md")]})
    got = fw.walk(fake, Rules(), Caps(max_files=100, max_depth=12, max_entries=4))
    assert got.over_entries and fake.listed == ["", "x"]


# --------------------------------------------------------------------------- #
# a directory that cannot be listed
# --------------------------------------------------------------------------- #


def test_a_directory_that_cannot_be_listed_is_named_with_the_cause_and_the_rest_goes_on() -> None:
    tree = {"": [d("locked"), d("open")], "open": [f("a.md")], "locked": []}
    errors = {"locked": PermissionError(13, "Access is denied")}
    got = fw.walk(Fake(tree, errors=errors), Rules(), BIG)
    assert paths(got) == ["open/a.md"]
    assert got.unlisted == {"locked": "permission denied"} and "locked" not in got.listed


def test_a_refusal_on_a_directory_is_named_in_the_folders_own_words() -> None:
    errors = {"sub": folder_io.FolderError("Mounted subfolders and files are not supported.")}
    got = fw.walk(Fake({"": [d("sub")], "sub": []}, errors=errors), Rules(), BIG)
    assert got.unlisted == {"sub": "Mounted subfolders and files are not supported"}


def test_a_directory_that_is_gone_since_its_parent_was_listed_is_neither_listed_nor_unlisted() -> (
    None
):
    got = fw.walk(Fake({"": [d("vanished"), f("a.md")]}), Rules(), BIG)
    assert paths(got) == ["a.md"] and got.unlisted == {} and got.listed == {""}


@pytest.mark.parametrize("error", [PermissionError(13, "denied"), FileNotFoundError(2, "gone")])
def test_an_error_on_the_folder_itself_is_the_callers_to_say(error: Exception) -> None:
    with pytest.raises(type(error)):
        fw.walk(Fake({"": []}, errors={"": error}), Rules(), BIG)


def test_the_cause_is_the_operating_systems_own_words() -> None:
    assert fw.observed(PermissionError(13, "Access is denied")) == "permission denied"
    assert fw.observed(OSError(5, "The device is not ready.")) == "The device is not ready"
    wrapped = folder_io.FolderError("text", cause=OSError(5, "Input/output error"))
    assert fw.observed(wrapped) == "Input/output error"
    assert fw.observed(folder_io.FolderError("Links are not allowed.")) == "Links are not allowed"


# --------------------------------------------------------------------------- #
# .gitignore, read through the tree
# --------------------------------------------------------------------------- #


def test_a_gitignore_in_the_folder_is_read_and_applies() -> None:
    tree = {"": [f(".gitignore"), f("a.log"), f("a.md"), d("build")], "build": [f("x.md")]}
    fake = Fake(tree, small={".gitignore": b"*.log\nbuild/\n"})
    got = fw.walk(fake, Rules(include_hidden=True), BIG)
    assert paths(got) == [".gitignore", "a.md"] and fake.listed == [""]
    assert {x.path: x.code for x in got.left} == {
        "a.log": "ignored_gitignore",
        "build": "ignored_gitignore",
    }


def test_a_gitignore_in_a_folder_below_applies_to_that_folder_only() -> None:
    tree = {
        "": [d("docs"), d("src"), f("a.draft")],
        "docs": [f(".gitignore"), f("a.draft"), f("b.md")],
        "src": [f("a.draft")],
    }
    fake = Fake(tree, small={"docs/.gitignore": b"*.draft\n"})
    got = fw.walk(fake, Rules(include_hidden=True), BIG)
    assert sorted(paths(got)) == ["a.draft", "docs/.gitignore", "docs/b.md", "src/a.draft"]


def test_an_anchored_line_in_a_gitignore_below_is_anchored_to_that_folder() -> None:
    tree = {
        "": [d("docs"), d("other")],
        "docs": [f(".gitignore"), d("build"), f("a.md")],
        "docs/build": [f("x.md")],
        "other": [d("build"), f("b.md")],
        "other/build": [f("y.md")],
    }
    fake = Fake(tree, small={"docs/.gitignore": b"/build/\n"})
    got = fw.walk(fake, Rules(), BIG)
    assert sorted(paths(got)) == ["docs/a.md", "other/b.md", "other/build/y.md"]
    assert fake.listed == ["", "docs", "other", "other/build"], "docs/build was never entered"


def test_a_gitignore_is_not_read_when_the_person_turned_it_off() -> None:
    fake = Fake({"": [f(".gitignore"), f("a.md")]}, small={".gitignore": b"*.md\n"})
    got = fw.walk(fake, Rules(include_hidden=True, use_gitignore=False), BIG)
    assert "a.md" in paths(got) and fake.read == []


def test_a_gitignore_that_cannot_be_read_or_is_too_large_is_no_gitignore() -> None:
    big = b"x\n" * (fw.MAX_GITIGNORE_BYTES // 2 + 10)
    fake = Fake({"": [f(".gitignore"), f("a.md")]}, small={".gitignore": big})
    assert "a.md" in paths(fw.walk(fake, Rules(include_hidden=True), BIG))
    fake = Fake({"": [f(".gitignore"), f("a.md")]})  # listed, but not there to read
    assert "a.md" in paths(fw.walk(fake, Rules(include_hidden=True), BIG))


def test_a_gitignore_is_read_only_when_the_listing_shows_a_file_of_that_name() -> None:
    fake = Fake({"": [d(".gitignore"), f("a.md")]}, small={".gitignore": b"*.md\n"})
    got = fw.walk(fake, Rules(include_hidden=True), BIG)
    assert "a.md" in paths(got) and fake.read == []


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #

SHA = "a" * 64


def held(
    path: str,
    *,
    size: int = 10,
    mtime: str | None = "1",
    sha: str = SHA,
    status: str = "ready",
    code: str | None = None,
    doc_id: str | None = None,
) -> Held:
    return Held(doc_id or f"id-{path}", path, size, mtime, sha, status, code)


def walked(*found: Found) -> Walked:
    return Walked(files=list(found))


def planned(files_: list[Found], have: list[Held], **kw: bool) -> fw.Plan:
    return fw.plan(walked(*files_), have, **kw)


def test_a_file_with_no_document_is_new() -> None:
    got = planned([Found("a.md", 10, 1)], [])
    assert got.add == [Found("a.md", 10, 1)] and not (got.change or got.keep or got.remove)


def test_a_file_that_is_as_it_was_is_kept() -> None:
    got = planned([Found("a.md", 10, 1)], [held("a.md")])
    assert [h.path for h in got.keep] == ["a.md"] and not (got.add or got.change or got.remove)


def test_a_file_whose_size_or_time_moved_is_read_again() -> None:
    assert planned([Found("a.md", 11, 1)], [held("a.md")]).change
    assert planned([Found("a.md", 10, 2)], [held("a.md")]).change


def test_a_file_whose_time_could_not_be_trusted_is_read_again_whatever_it_says_now() -> None:
    assert planned([Found("a.md", 10, 1)], [held("a.md", mtime="1!")]).change
    assert planned([Found("a.md", 10, 1)], [held("a.md", mtime=None)]).change


def test_a_full_check_reads_every_file() -> None:
    got = planned(
        [Found("a.md", 10, 1), Found("b.md", 10, 1)], [held("a.md"), held("b.md")], full=True
    )
    assert [(h.path, f_.path) for h, f_ in got.change] == [("a.md", "a.md"), ("b.md", "b.md")]


def test_what_is_gone_is_removed_and_what_is_found_is_not() -> None:
    got = planned([Found("a.md", 10, 1)], [held("a.md"), held("gone.md")])
    assert [h.path for h in got.remove] == ["gone.md"] and [h.path for h in got.keep] == ["a.md"]


def test_a_file_under_a_directory_that_could_not_be_listed_is_neither_kept_nor_removed_on_a_guess() -> (
    None
):
    w = Walked(
        files=[Found("a.md", 11, 1)], unlisted={"locked": "permission denied", "x/y": "busy"}
    )
    have = [held("locked/a.md"), held("locked/deep/b.md"), held("x/y/z/c.md"), held("x/other.md")]
    got = fw.plan(w, have)
    assert sorted(h.path for h in got.unknown) == ["locked/a.md", "locked/deep/b.md", "x/y/z/c.md"]
    assert [h.path for h in got.remove] == ["x/other.md"], "x itself was listed: this one is gone"


def test_an_unlisted_directory_is_named_by_its_whole_path_not_a_prefix_of_a_name() -> None:
    w = Walked(unlisted={"doc": "busy"})
    got = fw.plan(w, [held("docs/a.md"), held("doc/a.md")])
    assert [h.path for h in got.unknown] == ["doc/a.md"] and [h.path for h in got.remove] == [
        "docs/a.md"
    ]


def test_a_file_that_is_skipped_for_a_reason_that_may_pass_is_tried_again_at_every_check() -> None:
    for code in ("busy", "changed", "unreadable"):
        have = [held("a.md", status="skipped", code=code, sha="")]
        assert planned([Found("a.md", 10, 1)], have).change, code


def test_a_file_that_is_skipped_for_what_it_is_is_not_tried_again_until_it_changes() -> None:
    for code in ("unsupported", "not_document", "special", "hard_links", "bad_zip"):
        have = [held("a.md", status="skipped", code=code, sha="")]
        assert planned([Found("a.md", 10, 1)], have, manual=True).keep, code
        assert planned([Found("a.md", 11, 1)], have).change, code


def test_a_file_that_failed_or_was_over_a_limit_is_tried_again_when_the_person_asks() -> None:
    failed = [held("a.md", status="failed", code="crashed")]
    assert planned([Found("a.md", 10, 1)], failed).keep
    assert planned([Found("a.md", 10, 1)], failed, manual=True).change
    for code in ("too_many_passages", "over_folder_size", "too_large"):
        over = [held("a.md", status="skipped", code=code)]
        assert planned([Found("a.md", 10, 1)], over).keep, code
        assert planned([Found("a.md", 10, 1)], over, manual=True).change, code


def test_a_new_file_of_the_size_and_time_of_one_that_is_gone_is_a_move() -> None:
    got = planned([Found("new/a.md", 10, 1)], [held("old/a.md")])
    assert [(h.path, f_.path) for h, f_ in got.move] == [("old/a.md", "new/a.md")]
    assert not (got.add or got.remove)


def test_a_new_file_of_another_size_or_time_is_new_and_the_other_is_gone() -> None:
    for found in (Found("new.md", 11, 1), Found("new.md", 10, 2)):
        got = planned([found], [held("old.md")])
        assert got.add == [found] and [h.path for h in got.remove] == ["old.md"] and not got.move


def test_a_document_that_kept_no_copy_cannot_be_moved() -> None:
    got = planned([Found("new.md", 10, 1)], [held("old.md", sha="", status="skipped", code="busy")])
    assert got.add and got.remove and not got.move


def test_one_document_that_is_gone_moves_to_one_new_file_only() -> None:
    got = planned([Found("a.md", 10, 1), Found("b.md", 10, 1)], [held("old.md")])
    assert len(got.move) == 1 and len(got.add) == 1 and not got.remove
    assert got.move[0][1].path == "a.md" and got.add[0].path == "b.md"


def test_every_file_and_every_document_is_in_exactly_one_place_in_the_plan() -> None:
    found = [Found(p, 10, 1) for p in ("keep.md", "new.md", "edit.md", "moved-to.md")]
    found[2] = Found("edit.md", 99, 1)
    have = [
        held("keep.md"),
        held("edit.md"),
        held("gone.md", size=3),
        held("moved-from.md"),
        held("q/u.md"),
    ]
    w = Walked(files=found, unlisted={"q": "busy"})
    got = fw.plan(w, have)
    placed = (
        [f_.path for f_ in got.add]
        + [f_.path for _, f_ in got.change]
        + [f_.path for _, f_ in got.move]
        + [h.path for h in got.keep]
    )
    assert sorted(placed) == sorted(["keep.md", "new.md", "edit.md", "moved-to.md"])
    docs = (
        [h.path for h in got.remove]
        + [h.path for h, _ in got.change]
        + [h.path for h, _ in got.move]
        + [h.path for h in got.keep]
        + [h.path for h in got.unknown]
    )
    assert sorted(docs) == sorted(h.path for h in have)
    assert {h.path for h in got.remove} == {"gone.md"}
    assert [h.path for h, _ in got.move] == ["moved-from.md"]


def test_the_time_a_file_is_kept_as_is_its_modified_time() -> None:
    assert fw.signature(Found("a.md", 1, 123456789)) == "123456789"


def test_an_entry_is_read_from_windows_attributes() -> None:
    made = folder_io.entry_from_attributes
    assert made("a", 0x20, 5, 116_444_736_000_000_000 + 10, 0) == Entry("a", FILE, 5, 1000)
    assert made("d", 0x10, 0, 116_444_736_000_000_000, 0).kind == DIRECTORY
    assert made("j", 0x410, 0, 116_444_736_000_000_000, 0xA0000003).kind == LINK, "a junction"
    assert made("l", 0x400, 0, 116_444_736_000_000_000, 0xA000000C).kind == LINK, "a symlink"
    assert made("h", 0x22, 0, 116_444_736_000_000_000, 0).hidden is True
    assert made("s", 0x24, 0, 116_444_736_000_000_000, 0).hidden is True
    gone_online = made("c", 0x1020, 0, 116_444_736_000_000_000, 0)
    assert gone_online.kind == LINK and gone_online.cloud, "a file whose bytes are not here"
    recall = made("c", 0x400000, 0, 116_444_736_000_000_000, 0)
    assert recall.kind == LINK and recall.cloud
    tagged = made("c", 0x400, 0, 116_444_736_000_000_000, folder_io.CLOUD_TAG | 0x3000)
    assert tagged.cloud and not made("m", 0x400, 0, 116_444_736_000_000_000, 0xA0000003).cloud


def test_a_walk_of_a_real_folder_matches_one_of_the_same_folder_in_memory(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "sub" / "b.md").write_text("bb", encoding="utf-8")
    (tmp_path / ".hidden").write_text("h", encoding="utf-8")
    identity = folder_io.inspect(str(tmp_path), [])
    got = folder_io.scan(str(tmp_path), identity, [], lambda tree: fw.walk(tree, Rules(), BIG))
    assert paths(got) == ["a.md", "sub/b.md"] and [x.size for x in got.files] == [1, 2]
    assert got.listed == {"", "sub"} and got.counts == {"ignored_hidden": 1}
