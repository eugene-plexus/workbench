"""What a folder walk leaves out (MR7, `knowledge/folder_rules.py`): every default, the hidden rule,
the pattern language of a person's list and of `.gitignore`, and the words for each. Pure: no file is
read. Each check names the case it is about, so a rule that stops working is a failed check.
"""

from __future__ import annotations

import pytest

from eugene_plexus_workbench.knowledge import folder_rules as rules
from eugene_plexus_workbench.knowledge.folder_rules import BadPattern, Rules


def judged(
    rule: Rules, path: str, *, is_dir: bool = False, hidden: bool = False, chain: tuple = ()
) -> str | None:
    why = rule.judge(path, path.rsplit("/", 1)[-1], is_dir, hidden=hidden, chain=chain)
    return why.code if why else None


# --------------------------------------------------------------------------- #
# what is always left out
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", rules.DEFAULT_FOLDERS)
def test_every_default_folder_is_never_entered_whatever_else_is_set(name: str) -> None:
    for rule in (Rules(), Rules(include_hidden=True), Rules(use_gitignore=False)):
        assert judged(rule, name, is_dir=True) == "default", name
        assert judged(rule, f"deep/er/{name}", is_dir=True) == "default"
    assert judged(Rules(), name, is_dir=False) != "default" or name.startswith("."), (
        "the folder names are for folders: a file with the same name is judged as a file"
    )


@pytest.mark.parametrize("name", rules.DEFAULT_FILES)
def test_every_default_file_is_left_out_even_with_hidden_files_on(name: str) -> None:
    assert judged(Rules(include_hidden=True), f"a/{name}") == "default"
    assert judged(Rules(), f"a/{name}") == "default"
    assert judged(Rules(), name, is_dir=True) != "default", "a folder of that name is a folder"


@pytest.mark.parametrize(
    "name",
    ["~$report.docx", ".~lock.notes.odt#", "draft.tmp", "scratch.TEMP", "x.swp", "x.swo", "plan.md~",
     ".#plan.md", "movie.part", "movie.partial", "setup.crdownload"],
)  # fmt: skip
def test_temporary_files_are_left_out_without_regard_to_case(name: str) -> None:
    assert judged(Rules(include_hidden=True), name) == "temporary", name
    assert judged(Rules(include_hidden=True), name.upper()) == "temporary", name


@pytest.mark.parametrize(
    "name", ["plan.md", "notes.txt", "report.pdf", "tmp.md", "a~b.md", "x.partial.md"]
)
def test_ordinary_files_are_taken(name: str) -> None:
    assert judged(Rules(), f"docs/{name}") is None, name


def test_default_folder_names_are_matched_without_regard_to_case() -> None:
    assert judged(Rules(), "Node_Modules", is_dir=True) == "default"
    assert (
        judged(Rules(), "src/__PYCACHE__".replace("PYCACHE", "pycache"), is_dir=True) == "default"
    )
    assert judged(Rules(), "$recycle.bin", is_dir=True) == "default"


# --------------------------------------------------------------------------- #
# hidden
# --------------------------------------------------------------------------- #


def test_a_name_with_a_dot_in_front_is_hidden_unless_the_person_turns_hidden_on() -> None:
    assert judged(Rules(), ".env.example") == "hidden"
    assert judged(Rules(), "notes/.private", is_dir=True) == "hidden"
    assert judged(Rules(include_hidden=True), ".env.example") is None
    assert judged(Rules(include_hidden=True), "notes/.private", is_dir=True) is None


def test_the_windows_hidden_or_system_attribute_is_hidden_too() -> None:
    assert judged(Rules(), "secret.md", hidden=True) == "hidden"
    assert judged(Rules(include_hidden=True), "secret.md", hidden=True) is None


def test_hidden_turned_on_still_leaves_out_what_is_always_left_out() -> None:
    on = Rules(include_hidden=True)
    assert judged(on, ".git", is_dir=True) == "default"
    assert judged(on, ".DS_Store") == "default"
    assert judged(on, ".#lock") == "temporary"


def test_each_reason_says_why_in_words() -> None:
    why = Rules().judge(".git", ".git", True)
    assert why is not None and why.reason == "Ignored: a folder Workbench never reads (.git)."
    why = Rules().judge("a/.x", ".x", False)
    assert why is not None and why.reason == "Ignored: a hidden file."
    why = Rules().judge("~$a.docx", "~$a.docx", False)
    assert why is not None and why.reason == "Ignored: a temporary file."
    why = Rules(["*.log"]).judge("a.log", "a.log", False)
    assert why is not None and why.reason == "Ignored: your ignore list (*.log)."


# --------------------------------------------------------------------------- #
# a person's list: the pattern language
# --------------------------------------------------------------------------- #


def matches(pattern: str, path: str, *, is_dir: bool = False) -> bool:
    return judged(Rules([pattern]), path, is_dir=is_dir) == "list"


def test_a_name_pattern_matches_at_any_depth_and_only_whole_names() -> None:
    assert matches("*.log", "debug.log") and matches("*.log", "a/b/debug.log")
    assert not matches("*.log", "debug.log.md") and not matches("*.log", "a/debug.logs")
    assert matches("draft.md", "a/b/draft.md") and not matches("draft.md", "a/mydraft.md")


def test_a_leading_slash_anchors_a_pattern_to_the_folder() -> None:
    assert matches("/top.md", "top.md")
    assert not matches("/top.md", "a/top.md")
    assert matches("/a/top.md", "a/top.md") and not matches("/a/top.md", "b/a/top.md")


def test_a_slash_in_the_middle_anchors_a_pattern_too() -> None:
    assert matches("docs/old", "docs/old", is_dir=True)
    assert not matches("docs/old", "x/docs/old", is_dir=True)


def test_a_trailing_slash_names_folders_only() -> None:
    assert matches("drafts/", "drafts", is_dir=True) and matches("drafts/", "a/drafts", is_dir=True)
    assert not matches("drafts/", "drafts", is_dir=False), "a file called drafts is not a folder"


def test_a_star_stays_inside_a_name_and_a_double_star_crosses_folders() -> None:
    assert matches("a/*/c.md", "a/b/c.md") and not matches("a/*/c.md", "a/b/x/c.md")
    assert matches("a/**/c.md", "a/c.md") and matches("a/**/c.md", "a/b/c.md")
    assert matches("a/**/c.md", "a/b/x/y/c.md") and not matches("a/**/c.md", "b/a/c.md")
    assert matches("**/c.md", "c.md") and matches("**/c.md", "x/y/c.md")
    assert matches("a/**", "a/b.md") and matches("a/**", "a/b/c.md")
    assert not matches("a/**", "b/a/c.md")


def test_a_question_mark_is_one_character_and_a_class_is_one_of_some() -> None:
    assert matches("v?.md", "v1.md") and not matches("v?.md", "v12.md")
    assert matches("v[12].md", "v2.md") and not matches("v[12].md", "v3.md")
    assert matches("v[a-c].md", "vb.md") and not matches("v[a-c].md", "vd.md")
    assert matches("v[!12].md", "v3.md") and not matches("v[!12].md", "v1.md")
    assert matches("v[^12].md", "v3.md") and not matches("v[^12].md", "v2.md")


def test_a_bracket_that_never_closes_is_just_a_bracket() -> None:
    assert matches("a[b.md", "a[b.md") and not matches("a[b.md", "ab.md")


def test_a_backslash_makes_the_next_character_itself() -> None:
    assert matches(r"a\*b.md", "a*b.md") and not matches(r"a\*b.md", "aXb.md")
    assert matches(r"\[x].md", "[x].md")


def test_a_pattern_is_matched_without_regard_to_case_only_where_names_are_not_case_apart() -> None:
    import sys

    folded = matches("Draft.md", "draft.md")
    assert folded is (sys.platform == "win32")


def test_dots_and_other_regex_characters_in_a_pattern_are_themselves() -> None:
    assert matches("a+b(1).md", "a+b(1).md") and not matches("a+b(1).md", "aab1.md")
    assert matches("a.md", "a.md") and not matches("a.md", "aXmd")


def test_what_an_ignore_list_leaves_out_is_said_with_the_pattern_that_did() -> None:
    rule = Rules(["*.log", "drafts/"])
    assert rule.judge("x/y.log", "y.log", False).text == "your ignore list (*.log)"  # type: ignore[union-attr]
    assert rule.judge("drafts", "drafts", True).text == "your ignore list (drafts/)"  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# what a person may write in the list
# --------------------------------------------------------------------------- #


def test_a_list_is_trimmed_and_the_same_pattern_is_kept_once() -> None:
    assert rules.validate_list(["  *.log ", "*.log", "tmp/"]) == ["*.log", "tmp/"]


@pytest.mark.parametrize(
    ("given", "says"),
    [
        (["!keep.md"], "only ignores"),
        (["# a comment"], "comment"),
        (["   "], "empty"),
        (["x" * (rules.MAX_PATTERN + 1)], "at most"),
        (["a\x00b"], "control"),
        (["v[z-a].md"], "not one Workbench understands"),
    ],
)
def test_a_pattern_that_cannot_be_used_is_refused_with_its_reason(
    given: list[str], says: str
) -> None:
    with pytest.raises(BadPattern, match=says):
        rules.validate_list(given)


def test_a_list_holds_at_most_a_hundred_patterns() -> None:
    assert (
        len(rules.validate_list([f"p{i}" for i in range(rules.MAX_PATTERNS)])) == rules.MAX_PATTERNS
    )
    with pytest.raises(BadPattern, match="at most 100"):
        rules.validate_list([f"p{i}" for i in range(rules.MAX_PATTERNS + 1)])


def test_a_backslash_hash_names_a_file_that_starts_with_a_hash() -> None:
    assert rules.validate_list([r"\#notes.md"]) == [r"\#notes.md"]
    assert matches(r"\#notes.md", "#notes.md")


@pytest.mark.parametrize(
    "path",
    [
        "plan.md",
        "a b/c d.md",
        "weird*star.md",
        "what?.md",
        "[brackets].md",
        "back\\slash.md",
        "!bang.md",
        "#hash.md",
        "ends with space .md",
        "docs/sub/file (1).md",
        "x]y.md",
    ],
)
def test_the_exact_pattern_for_a_path_matches_that_path_and_nothing_near_it(path: str) -> None:
    pattern = rules.exact(path)
    assert rules.validate_list([pattern]) == [pattern]
    rule = Rules([pattern])
    assert judged(rule, path) == "list", path
    assert judged(rule, "x/" + path) is None, "it names one path below the folder, not a name"
    assert judged(rule, path + "x") is None
    other = path.replace("a", "b") if "a" in path else path + "2"
    if other != path:
        assert judged(rule, other) is None


def test_the_exact_pattern_for_a_file_in_a_folder_is_anchored() -> None:
    rule = Rules([rules.exact("docs/plan.md")])
    assert judged(rule, "docs/plan.md") == "list"
    assert judged(rule, "other/docs/plan.md") is None and judged(rule, "plan.md") is None


# --------------------------------------------------------------------------- #
# .gitignore
# --------------------------------------------------------------------------- #


def chain_of(*files: tuple[str, str]) -> tuple:
    return tuple((base, rules.parse_gitignore(text)) for base, text in files)


def test_a_gitignore_leaves_out_what_it_names_and_not_what_it_does_not() -> None:
    chain = chain_of(("", "build/\n*.pyc\n"))
    rule = Rules()
    assert judged(rule, "build", is_dir=True, chain=chain) == "gitignore"
    assert judged(rule, "pkg/mod.pyc", chain=chain) == "gitignore"
    assert judged(rule, "pkg/mod.py", chain=chain) is None
    assert judged(rule, "build", is_dir=False, chain=chain) is None, "build/ names folders only"


def test_a_gitignore_is_not_used_when_the_person_turns_it_off() -> None:
    chain = chain_of(("", "*.md\n"))
    assert judged(Rules(), "a.md", chain=chain) == "gitignore"
    assert judged(Rules(use_gitignore=False), "a.md", chain=chain) is None


def test_comments_blank_lines_and_trailing_spaces_are_handled_as_git_does() -> None:
    chain = chain_of(("", "# a comment\n\n   \n*.log   \n\\#keep\n"))
    assert [p.text for _, ps in chain for p in ps] == ["*.log", "\\#keep"]
    assert judged(Rules(), "a.log", chain=chain) == "gitignore"
    assert judged(Rules(), "#keep", chain=chain) == "gitignore"


def test_a_later_line_wins_and_a_bang_takes_a_file_back() -> None:
    chain = chain_of(("", "*.md\n!keep.md\n"))
    assert judged(Rules(), "a.md", chain=chain) == "gitignore"
    assert judged(Rules(), "keep.md", chain=chain) is None
    assert judged(Rules(), "x/keep.md", chain=chain) is None
    again = chain_of(("", "!keep.md\n*.md\n"))
    assert judged(Rules(), "keep.md", chain=again) == "gitignore", "the last line that matches wins"


def test_a_gitignore_applies_to_its_own_folder_and_below_only() -> None:
    chain = chain_of(("docs", "*.draft\n"))
    assert judged(Rules(), "docs/a.draft", chain=chain) == "gitignore"
    assert judged(Rules(), "docs/deep/a.draft", chain=chain) == "gitignore"
    assert judged(Rules(), "a.draft", chain=chain) is None
    assert judged(Rules(), "docsx/a.draft", chain=chain) is None, "docsx is not below docs"


def test_a_deeper_gitignore_wins_over_a_higher_one() -> None:
    chain = chain_of(("", "*.md\n"), ("docs", "!*.md\n"))
    assert judged(Rules(), "a.md", chain=chain) == "gitignore"
    assert judged(Rules(), "docs/a.md", chain=chain) is None


def test_an_anchored_pattern_in_a_gitignore_is_anchored_to_its_own_folder() -> None:
    chain = chain_of(("docs", "/top.md\n"))
    assert judged(Rules(), "docs/top.md", chain=chain) == "gitignore"
    assert judged(Rules(), "docs/sub/top.md", chain=chain) is None


def test_a_line_git_cannot_use_is_skipped_and_the_rest_still_apply() -> None:
    chain = chain_of(("", "v[z-a].md\n*.tmp2\n"))
    assert [p.text for _, ps in chain for p in ps] == ["*.tmp2"]


def test_a_gitignore_is_read_to_a_bounded_number_of_lines() -> None:
    text = "\n".join(f"p{i}" for i in range(rules.MAX_GITIGNORE_LINES + 500))
    assert len(rules.parse_gitignore(text)) == rules.MAX_GITIGNORE_LINES


def test_the_person_s_list_and_the_gitignore_both_apply_and_the_list_cannot_be_undone() -> None:
    chain = chain_of(("", "!secret.md\n"))
    rule = Rules(["secret.md"])
    assert judged(rule, "secret.md", chain=chain) == "list", (
        "a ! in a .gitignore does not undo the list"
    )


def test_the_rules_the_screen_states_are_the_rules_that_apply() -> None:
    said = rules.describe()
    assert set(said["folders"]) == set(rules.DEFAULT_FOLDERS)  # type: ignore[call-overload]
    assert set(said["files"]) == set(rules.DEFAULT_FILES)  # type: ignore[call-overload]
    assert set(said["temporary"]) == set(rules.TEMPORARY)  # type: ignore[call-overload]
    for name in said["folders"]:  # type: ignore[attr-defined]
        assert judged(Rules(), name, is_dir=True) == "default"


# --------------------------------------------------------------------------- #
# edges
# --------------------------------------------------------------------------- #


def test_a_question_mark_is_never_a_slash() -> None:
    assert matches("a?b", "axb") and not matches("a?b", "a/b")
    assert matches("a/?.md", "a/b.md") and not matches("a?b.md", "a/b.md")


@pytest.mark.parametrize("name", ["backup.tmp", "~$drafts", "photos.part", "notes~", "x.swp"])
def test_a_folder_named_like_a_temporary_file_is_a_folder_and_what_is_in_it_is_taken(
    name: str,
) -> None:
    assert judged(Rules(), name, is_dir=True) is None, "the temporary names are for files"
    assert judged(Rules(), f"{name}/plan.md") is None
    assert judged(Rules(), name) == "temporary", "and the same name on a file is temporary"


def test_a_stored_pattern_that_cannot_be_used_now_is_dropped_and_the_rest_still_apply() -> None:
    # A list saved when the rules were looser (a `!` line, say) still loads: that line is not used.
    rule = Rules(["!keep.md", "*.log"])
    assert judged(rule, "a.log") == "list"
    assert judged(rule, "keep.md") is None


def test_the_exact_pattern_for_a_name_that_ends_in_a_space_keeps_the_space_when_the_list_is_saved() -> (
    None
):
    pattern = rules.exact("notes ")
    assert pattern == "/notes\\ "
    assert rules.validate_list([pattern]) == [pattern], "a trailing space that is escaped is kept"
    rule = Rules([pattern])
    assert judged(rule, "notes ") == "list" and judged(rule, "notes") is None
    assert rules.validate_list(["  *.log   "]) == ["*.log"], "any other space is trimmed"
