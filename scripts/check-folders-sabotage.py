"""Prove the folder checks are observed (MR7).

Each case breaks one thing in how a person's folder is read into their Documents and the
checks named for it must fail: the rules that leave names out, the walk and the plan, the
reading of one file on held handles, who may add and see a folder, what a check does about
an edit, a delete, a move, a vanished folder and a withdrawn grant, the staged removal of
documents and their files, the words and numbers a person is shown, and the settings. Files
are restored from exact bytes kept beside the run, never from Git. A mutation that stops the
code importing or compiling does not count as a caught defect.

  python scripts/check-folders-sabotage.py            # every case
  python scripts/check-folders-sabotage.py --anchors  # only check each anchor is unique
  python scripts/check-folders-sabotage.py --labels gitignore,depth   # cases whose label has these words
  python scripts/check-folders-sabotage.py --shard 2/4   # every fourth case, from the second

Run it with the interpreter and PYTHONPATH the tests use, and with every ambient
EUGENE_PLEXUS_* variable cleared.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = "src/eugene_plexus_workbench/"
RULES = SRC + "knowledge/folder_rules.py"
WALK = SRC + "knowledge/folder_walk.py"
SYNC = SRC + "knowledge/folder_sync.py"
INDEX = SRC + "knowledge/index.py"
SAVED = SRC + "knowledge/saved.py"
SEARCH = SRC + "knowledge/search.py"
FOLDER_IO = SRC + "folder_io.py"
WINDOWS = SRC + "folder_windows.py"
FOLDERS = SRC + "folders.py"
FOLDERS_API = SRC + "folders_api.py"
DOCS_API = SRC + "folder_docs_api.py"
SAVED_API = SRC + "saved_api.py"
STORE = SRC + "store.py"
CONFIG = SRC + "config.py"
LINUX = SRC + "folder_linux.py"

RULES_T = "tests/test_folder_rules.py"
WALK_T = "tests/test_folder_walk.py"
IO_T = "tests/test_folder_io.py"
INDEX_T = "tests/test_folder_index.py"
SYNC_T = "tests/test_folder_sync.py::"
API_T = "tests/test_folder_api.py::"
IGN_T = "tests/test_folder_ignore_api.py::"
REASON_T = "tests/test_folder_reasons_api.py::"
LIFE_T = "tests/test_folder_lifecycle_api.py::"
PEOPLE_T = "tests/test_folder_people_api.py::"

# The checks that more than one case names.
T_EDIT = API_T + "test_an_edited_file_is_read_again_the_new_words_answer_and_the_old_are_gone"
T_DELETE = API_T + "test_a_deleted_file_is_removed_with_its_words"
T_TOUCHED = API_T + "test_a_file_that_is_touched_but_says_the_same_is_not_read_again"
T_RACY = (
    API_T
    + "test_a_file_too_new_to_trust_is_looked_at_again_so_a_second_edit_in_the_same_tick_is_not_missed"
)
T_SAME_STAT = (
    API_T
    + "test_an_edit_that_keeps_the_size_and_the_time_of_an_old_file_is_missed_by_a_quick_check_and_found_by_a_full_one"
)
T_GONE = (
    LIFE_T + "test_a_folder_that_is_not_there_takes_its_documents_out_and_they_come_back_with_it"
)
T_REPLACED = (
    LIFE_T + "test_a_folder_replaced_by_another_takes_its_documents_out_and_says_to_grant_it_again"
)
T_WITHDRAWN = (
    LIFE_T + "test_a_withdrawn_grant_takes_the_documents_out_before_the_owners_request_returns"
)
T_WITHDRAWN_MID = (
    LIFE_T + "test_a_grant_withdrawn_while_a_check_is_reading_leaves_nothing_added_after_it"
)
T_REMOVED_MID = (
    LIFE_T
    + "test_removing_a_folder_while_it_is_being_read_ends_the_check_and_nothing_is_added_after"
)
T_STUCK_REMOVE = LIFE_T + "test_a_folder_whose_files_cannot_be_removed_stays_to_be_removed_again"
T_STUCK_WITHDRAW = (
    LIFE_T
    + "test_a_withdrawn_grant_whose_files_cannot_be_removed_is_withdrawn_again_until_they_can"
)
T_BOOT_WITHDRAWN = (
    LIFE_T + "test_a_grant_withdrawn_while_workbench_was_stopped_is_found_when_it_starts"
)
T_BOOT_ORPHANS = (
    LIFE_T + "test_documents_of_a_folder_the_person_no_longer_has_are_removed_when_workbench_starts"
)
T_BOOT_CHECK = LIFE_T + "test_what_changed_while_workbench_was_stopped_is_found_when_it_starts"
T_ORDER = (
    LIFE_T
    + "test_what_the_loop_takes_next_is_a_manual_check_then_a_first_check_then_a_scheduled_one"
)
T_INTERVAL = LIFE_T + "test_a_folder_is_checked_again_by_itself_every_interval"
T_OFF = LIFE_T + "test_checks_are_not_made_while_the_owner_has_turned_documents_off"
T_APART = (
    LIFE_T
    + "test_two_folders_of_one_person_are_checked_apart_and_one_failing_does_not_stop_the_other"
)
T_TWICE = LIFE_T + "test_a_folder_added_twice_is_refused_and_can_be_added_again_once_it_is_removed"
T_NOT_A_MOVE = (
    LIFE_T + "test_a_new_file_that_looks_like_a_vanished_one_but_says_something_else_is_not_a_move"
)
T_UNREADABLE = (
    REASON_T + "test_a_check_that_cannot_look_at_the_folder_keeps_its_documents_and_says_why"
)
T_DISK_FULL = REASON_T + "test_a_full_disk_stops_the_check_and_says_so_and_keeps_what_was_read"
T_REASONS = REASON_T + "test_every_file_that_cannot_be_read_is_a_document_with_its_reason_and_code"
T_BYTES_CAP = (
    REASON_T + "test_the_most_a_folder_copies_is_stated_and_a_file_that_would_pass_it_is_skipped"
)
T_BUSY = REASON_T + "test_a_file_another_program_holds_is_skipped_for_now_and_read_when_it_is_free"
T_CHANGING = (
    REASON_T
    + "test_a_file_that_changes_while_it_is_read_is_skipped_for_now_and_read_at_the_next_check"
)
T_VANISHED_READ = (
    REASON_T
    + "test_a_file_deleted_between_the_listing_and_the_read_is_not_there_and_has_no_document"
)
T_SWAPPED = (
    SYNC_T + "test_a_link_that_appears_between_the_listing_and_the_read_is_skipped_in_its_words"
)
T_NOT_YOURS = (
    PEOPLE_T + "test_a_folder_that_is_not_yours_is_the_same_404_as_one_that_does_not_exist"
)
T_GRANT_NOT_YOURS = (
    PEOPLE_T + "test_a_grant_that_is_not_yours_cannot_be_added_and_is_the_same_404_as_none"
)
T_OWNER_OFFERED = PEOPLE_T + "test_the_owner_is_offered_only_the_grants_that_name_the_owner"
T_SETTINGS = PEOPLE_T + "test_each_folder_setting_takes_whole_numbers_in_its_range_and_nothing_else"
T_LIMITS = API_T + "test_the_limits_follow_the_installs_settings_and_a_folder_shows_how_near_it_is"
T_STATES = SYNC_T + "test_a_folders_state_comes_from_its_record_what_is_running_and_what_is_waiting"
T_HEADLINE = SYNC_T + "test_the_headline_is_one_sentence_for_each_state_made_of_what_is_true"
T_LISTING = (
    API_T + "test_the_listing_of_a_persons_own_documents_holds_only_what_they_added_and_a_summary"
)
T_REMOVED_ENDS_REQUEST = (
    LIFE_T + "test_removing_a_folder_ends_the_search_model_request_that_was_only_for_its_files"
)
T_NOT_BEFORE_INTERVAL = LIFE_T + "test_a_folder_is_not_checked_again_before_its_interval_is_up"
T_LEFT_ALONE = (
    LIFE_T
    + "test_a_folder_whose_check_failed_is_left_alone_for_a_while_even_when_it_was_asked_for_again"
)
T_STUCK_IN_CHECK = (
    LIFE_T + "test_a_file_that_cannot_be_removed_while_a_check_goes_on_is_said_as_that"
)
T_WITHDRAW_RACE = (
    LIFE_T
    + "test_a_withdrawal_the_check_itself_began_is_waited_for_before_the_owners_request_returns"
)
T_WITHDRAWN_BEFORE_CHECK = (
    SYNC_T + "test_a_check_that_begins_after_the_folder_was_withdrawn_reads_and_adds_nothing"
)
T_GRANTED = SYNC_T + "test_a_folder_is_granted_only_while_its_grant_is_there_and_names_its_person"
T_BYTES_LATER = (
    REASON_T + "test_what_a_folder_already_holds_counts_against_its_size_at_every_later_check"
)
T_PERSON_GONE = (
    LIFE_T
    + "test_a_folder_whose_person_is_no_longer_known_is_withdrawn_and_not_checked_again_and_again"
)
STORE_T = "tests/test_folder_store.py"

#: (label, [(file, before, after), ...], [check, ...])
Case = tuple[str, list[tuple[str, str, str]], list[str]]


def one(label: str, name: str, before: str, after: str, *checks: str) -> Case:
    return (label, [(name, before, after)], list(checks))


def lines(*parts: str) -> str:
    """Several lines of source, joined as the file has them."""
    return "\n".join(parts)


# (label, edits, checks)
CASES: list[Case] = [
    # --- the rules: what is left out -----------------------------------------------------
    one(
        "a default folder is entered",
        RULES,
        "        if is_dir and low in _FOLDERS:",
        "        if False and is_dir and low in _FOLDERS:",
        RULES_T,
        WALK_T,
    ),
    one(
        "a file the system makes is taken",
        RULES,
        "        if not is_dir and low in _FILES:",
        "        if False and not is_dir and low in _FILES:",
        RULES_T,
    ),
    one(
        "a temporary file is taken",
        RULES,
        "        if not is_dir and _TEMPORARY.fullmatch(low):",
        "        if False and not is_dir and _TEMPORARY.fullmatch(low):",
        RULES_T,
    ),
    one(
        "a folder named like a temporary file is left out",
        RULES,
        "        if not is_dir and _TEMPORARY.fullmatch(low):",
        "        if _TEMPORARY.fullmatch(low):",
        RULES_T,
    ),
    one(
        "a default name is matched with its case",
        RULES,
        "        low = name.casefold()",
        "        low = name",
        RULES_T,
    ),
    one(
        "hidden is left out when Include hidden is on",
        RULES,
        '        if not self.include_hidden and (name.startswith(".") or hidden):',
        '        if name.startswith(".") or hidden:',
        RULES_T,
        IGN_T + "test_a_hidden_file_is_read_when_the_person_turns_hidden_on_for_that_folder",
    ),
    one(
        "the Windows hidden attribute is not read",
        RULES,
        '        if not self.include_hidden and (name.startswith(".") or hidden):',
        '        if not self.include_hidden and (name.startswith(".")):',
        RULES_T,
    ),
    one(
        "a name with a dot in front is not hidden",
        RULES,
        '        if not self.include_hidden and (name.startswith(".") or hidden):',
        "        if not self.include_hidden and (hidden):",
        RULES_T,
        IGN_T
        + "test_the_default_rules_leave_out_other_programs_folders_hidden_and_temporary_files",
    ),
    one(
        "the person's own list is not read",
        RULES,
        "        for pattern in self._list:",
        "        for pattern in []:",
        RULES_T,
        IGN_T + "test_a_folders_own_list_leaves_out_what_it_names_and_says_which_pattern_did",
    ),
    one(
        "a .gitignore applies when it is turned off",
        RULES,
        "        if self.use_gitignore and chain:",
        "        if chain:",
        RULES_T,
        IGN_T + "test_a_folders_gitignore_is_respected_and_can_be_turned_off",
    ),
    one(
        "a .gitignore re-include ignores",
        RULES,
        "                        decided = None if pattern.negate else pattern",
        "                        decided = pattern",
        RULES_T,
    ),
    one(
        "the first .gitignore line wins, not the last",
        RULES,
        lines(
            "                    if pattern.matches(inside, is_dir):",
            "                        decided = None if pattern.negate else pattern",
        ),
        lines(
            "                    if pattern.matches(inside, is_dir) and decided is None:",
            "                        decided = None if pattern.negate else pattern",
        ),
        RULES_T,
    ),
    one(
        "a .gitignore reaches beside its own folder",
        RULES,
        '                if base and not path.startswith(base + "/"):',
        '                if False and base and not path.startswith(base + "/"):',
        RULES_T,
        IGN_T + "test_a_gitignore_in_a_folder_below_applies_to_that_folder_only",
    ),
    one(
        "a pattern with a slash in the middle is not anchored",
        RULES,
        lines('    anchored = "/" in body', '    body = body.lstrip("/")'),
        lines("    anchored = False", '    body = body.lstrip("/")'),
        RULES_T,
    ),
    one(
        "a leading slash does not anchor",
        RULES,
        lines('    anchored = "/" in body', '    body = body.lstrip("/")'),
        lines('    anchored = "/" in body.lstrip("/")', '    body = body.lstrip("/")'),
        RULES_T,
    ),
    one(
        "a trailing slash does not mean folders only",
        RULES,
        '    dir_only = body.endswith("/")',
        "    dir_only = False",
        RULES_T,
    ),
    one(
        "** needs at least one folder",
        RULES,
        'out.append("(?:.*/)?")',
        'out.append("(?:.*/)")',
        RULES_T,
    ),
    one(
        "a/** is not everything below",
        RULES,
        'out.append(".*")',
        'out.append("[^/]*")',
        RULES_T,
    ),
    one(
        "a single star crosses folders",
        RULES,
        'out.append("[^/]*")',
        'out.append(".*")',
        RULES_T,
    ),
    one(
        "a question mark matches a slash",
        RULES,
        'out.append("[^/]")',
        'out.append(".")',
        RULES_T,
    ),
    one(
        "a backslash does not make the next character literal",
        RULES,
        "out.append(re.escape(pattern[at + 1]))",
        "out.append(pattern[at + 1])",
        RULES_T,
    ),
    one(
        "a negated class is not negated",
        RULES,
        '    negated = body[:1] in ("!", "^")',
        "    negated = False",
        RULES_T,
    ),
    one(
        "names are matched with their case on Windows",
        RULES,
        '_IGNORE_CASE = sys.platform == "win32"',
        "_IGNORE_CASE = False",
        RULES_T,
    ),
    one(
        "a list may hold any number of patterns",
        RULES,
        "    if len(out) > MAX_PATTERNS:",
        "    if len(out) > MAX_PATTERNS * 100:",
        RULES_T,
        IGN_T + "test_a_pattern_workbench_cannot_use_is_refused_in_words_and_changes_nothing",
    ),
    one(
        "a pattern may be any length",
        RULES,
        "        if len(text) > MAX_PATTERN:",
        "        if len(text) > MAX_PATTERN * 100:",
        RULES_T,
        IGN_T + "test_a_pattern_workbench_cannot_use_is_refused_in_words_and_changes_nothing",
    ),
    one(
        "a person's list may re-include",
        RULES,
        "    if negate and not allow_negation:",
        "    if False and negate and not allow_negation:",
        RULES_T,
        IGN_T + "test_a_pattern_workbench_cannot_use_is_refused_in_words_and_changes_nothing",
    ),
    one(
        "a comment may be in the person's list",
        RULES,
        '        if text.startswith("#"):',
        '        if False and text.startswith("#"):',
        RULES_T,
        IGN_T + "test_a_pattern_workbench_cannot_use_is_refused_in_words_and_changes_nothing",
    ),
    one(
        "an empty pattern is kept quietly",
        RULES,
        lines(
            "        if not text:",
            '            raise BadPattern("An ignore pattern cannot be empty.")',
        ),
        lines("        if not text:", "            continue"),
        RULES_T,
        IGN_T + "test_a_pattern_workbench_cannot_use_is_refused_in_words_and_changes_nothing",
    ),
    one(
        "a control character may be in a pattern",
        RULES,
        "        if any(ord(c) < 32 or ord(c) == 127 for c in text):",
        "        if False:",
        RULES_T,
    ),
    one(
        "the same pattern is kept twice",
        RULES,
        "        if text not in out:",
        "        if True:",
        RULES_T,
        IGN_T + "test_the_list_is_trimmed_and_the_same_pattern_is_kept_once",
    ),
    one(
        "trailing spaces are part of a pattern",
        RULES,
        r'    while line.endswith(" ") and not line.endswith("\\ "):',
        "    while False:",
        RULES_T,
    ),
    one(
        "an exact pattern is not escaped",
        RULES,
        r'    escaped = re.sub(r"([\\*?\[\]])", r"\\\1", path)',
        "    escaped = path",
        RULES_T,
        IGN_T + "test_a_file_with_odd_characters_in_its_name_is_removed_by_its_exact_path",
    ),
    one(
        "an exact pattern loses a trailing space",
        RULES,
        '    if escaped.endswith(" "):',
        "    if False:",
        RULES_T,
    ),
    one(
        "an exact pattern is not anchored",
        RULES,
        '    return "/" + escaped',
        "    return escaped",
        RULES_T,
        IGN_T + "test_removing_one_file_of_a_folder_stops_it_being_read_and_names_it_in_the_list",
    ),
    one(
        "a .gitignore has no cap on its lines",
        RULES,
        "    for raw in text.splitlines()[:MAX_GITIGNORE_LINES]:",
        "    for raw in text.splitlines():",
        RULES_T,
    ),
    one(
        "a bad .gitignore line is an error",
        RULES,
        lines(
            "        except BadPattern:",
            "            continue",
            "        if pattern is not None:",
        ),
        lines(
            "        except BadPattern:",
            "            raise",
            "        if pattern is not None:",
        ),
        RULES_T,
    ),
    one(
        "a stored pattern that is bad now is an error",
        RULES,
        lines(
            "            except BadPattern:",
            "                continue  # one that was kept when the rules were looser is not an error now",
        ),
        lines("            except BadPattern:", "                raise"),
        RULES_T,
    ),
    # --- the walk and the plan -----------------------------------------------------------
    one(
        "the depth cap is one too deep",
        WALK,
        "                if len(names) + 1 > caps.max_depth:",
        "                if len(names) + 1 >= caps.max_depth:",
        WALK_T,
        REASON_T + "test_a_folder_deeper_than_the_cap_is_not_entered_and_is_named",
    ),
    one(
        "the depth cap is not applied",
        WALK,
        "                if len(names) + 1 > caps.max_depth:",
        "                if False and len(names) + 1 > caps.max_depth:",
        WALK_T,
        REASON_T + "test_a_folder_deeper_than_the_cap_is_not_entered_and_is_named",
    ),
    one(
        "the file cap lets one more in",
        WALK,
        "            elif len(out.files) >= caps.max_files:",
        "            elif len(out.files) > caps.max_files:",
        WALK_T,
        REASON_T
        + "test_the_most_files_a_folder_gives_keeps_the_shallow_ones_and_says_what_was_left_out",
    ),
    one(
        "a link is taken for a file",
        WALK,
        "            elif entry.kind == LINK:",
        "            elif False and entry.kind == LINK:",
        WALK_T,
        REASON_T + "test_a_link_inside_the_folder_is_named_and_what_it_points_to_is_never_read",
    ),
    one(
        "a pipe or a device is taken for a file",
        WALK,
        "            elif entry.kind == OTHER:",
        "            elif False and entry.kind == OTHER:",
        WALK_T,
    ),
    one(
        "the listing is read in whatever order it comes",
        WALK,
        "        listing.sort(key=lambda e: (e.name.casefold(), e.name))",
        "        pass",
        WALK_T,
    ),
    one(
        "a directory that could not be listed is taken for empty",
        WALK,
        lines("            out.unlisted[base] = observed(exc)", "            continue"),
        "            continue",
        WALK_T,
        T_UNREADABLE,
    ),
    one(
        "a directory that cannot be listed at the top is not an error",
        WALK,
        lines(
            "            if not names:",
            "                raise",
            "            out.unlisted[base] = observed(exc)",
        ),
        "            out.unlisted[base] = observed(exc)",
        WALK_T,
        T_GONE,
    ),
    one(
        "a directory that vanished while it was walked is an error",
        WALK,
        lines(
            "        except (FileNotFoundError, NotADirectoryError):",
            "            if not names:",
            "                raise",
            "            continue",
        ),
        lines(
            "        except (FileNotFoundError, NotADirectoryError):",
            "            raise",
            "            continue",
        ),
        WALK_T,
    ),
    one(
        "the cap on names looked at is not applied",
        WALK,
        "        if len(listing) > budget:",
        "        if False and len(listing) > budget:",
        WALK_T,
    ),
    one(
        "an ignored folder is entered",
        WALK,
        '                out.leave(path, "folder" if is_dir else "file", f"ignored_{why.code}", why.reason)',
        lines(
            '                out.leave(path, "folder" if is_dir else "file", f"ignored_{why.code}", why.reason)',
            "                if is_dir:",
            "                    queue.append(((*names, entry.name), chain))",
        ),
        WALK_T,
        IGN_T
        + "test_the_default_rules_leave_out_other_programs_folders_hidden_and_temporary_files",
    ),
    one(
        "a .gitignore is read when it is turned off",
        WALK,
        "    if not rules.use_gitignore or not any(e.name == GITIGNORE and e.kind == FILE for e in listing):",
        "    if not any(e.name == GITIGNORE and e.kind == FILE for e in listing):",
        WALK_T,
        IGN_T + "test_a_folders_gitignore_is_respected_and_can_be_turned_off",
    ),
    one(
        "a .gitignore below applies to the whole folder",
        WALK,
        "    return (*chain, (base, patterns)) if patterns else chain",
        '    return (*chain, ("", patterns)) if patterns else chain',
        WALK_T,
        IGN_T + "test_a_gitignore_in_a_folder_below_applies_to_that_folder_only",
    ),
    one(
        "a name with characters Workbench cannot open is taken",
        WALK,
        "    if any(ord(c) < 32 or ord(c) == 127 for c in name):",
        "    if False:",
        WALK_T,
    ),
    one(
        "a permission error is not called that",
        WALK,
        lines("    if isinstance(cause, PermissionError):", '        return "permission denied"'),
        lines("    if False:", '        return "permission denied"'),
        WALK_T,
    ),
    one(
        "a file kept online is a link to be not followed",
        WALK,
        "    if entry.cloud:",
        "    if False:",
        WALK_T,
    ),
    one(
        "a document under an unlisted directory two folders up is removed",
        WALK,
        lines(
            '    parent = path.rpartition("/")[0]',
            "    while True:",
            "        if parent in directories:",
            "            return True",
            "        if not parent:",
            "            return False",
            '        parent = parent.rpartition("/")[0]',
        ),
        '    return path.rpartition("/")[0] in directories',
        WALK_T,
    ),
    one(
        "a changed size alone is not noticed",
        WALK,
        "    if held.size != found.size or held.mtime != signature(found):",
        "    if held.mtime != signature(found):",
        WALK_T,
    ),
    one(
        "a changed time alone is not noticed",
        WALK,
        "    if held.size != found.size or held.mtime != signature(found):",
        "    if held.size != found.size:",
        WALK_T,
        T_TOUCHED,
    ),
    one(
        "a full check reads only what moved",
        WALK,
        "    if full or held.mtime is None:",
        "    if held.mtime is None:",
        WALK_T,
        T_SAME_STAT,
    ),
    one(
        "a busy file is never tried again",
        WALK,
        "        if code in RETRY_ALWAYS:",
        "        if False and code in RETRY_ALWAYS:",
        WALK_T,
        T_BUSY,
    ),
    one(
        "Sync now does not try again what may have passed",
        WALK,
        '        if manual and (held.status == "failed" or code in RETRY_MANUAL):',
        '        if False and manual and (held.status == "failed" or code in RETRY_MANUAL):',
        WALK_T,
        REASON_T
        + "test_a_file_too_large_says_its_size_and_the_limit_and_is_read_once_the_limit_is_raised",
    ),
    one(
        "Sync now does not try a failed file again",
        WALK,
        '        if manual and (held.status == "failed" or code in RETRY_MANUAL):',
        "        if manual and code in RETRY_MANUAL:",
        WALK_T,
    ),
    one(
        "a move is paired by size alone",
        WALK,
        "        options = waiting.get((found.size, signature(found)))",
        "        options = next((v for (s, _t), v in waiting.items() if s == found.size and v), None)",
        WALK_T,
    ),
    one(
        "a document that kept no copy can be moved",
        WALK,
        "        if have.copied:",
        "        if True:",
        WALK_T,
    ),
    one(
        "a moved document is also removed",
        WALK,
        "    out.remove = [h for h in gone if h.doc_id not in taken]",
        "    out.remove = list(gone)",
        WALK_T,
        LIFE_T + "test_a_renamed_folder_keeps_every_document_its_passages_and_its_vectors",
    ),
    one(
        "documents under an unlisted directory are removed",
        WALK,
        "        (out.unknown if _under(have.path, walked.unlisted) else gone).append(have)",
        "        gone.append(have)",
        WALK_T,
    ),
    # --- reading one file on held handles ------------------------------------------------
    one(
        "a file that changed while it was read is trusted",
        FOLDER_IO,
        "                    raise ChangedWhileReading",
        "                    pass",
        IO_T,
        T_CHANGING,
    ),
    one(
        "a file that grows while it is read is not stopped",
        FOLDER_IO,
        lines(
            "                    if total > limit:", "                        raise TooLarge(total)"
        ),
        lines("                    if False:", "                        raise TooLarge(total)"),
        IO_T,
    ),
    one(
        "a file over the limit is read",
        FOLDER_IO,
        lines(
            "                if before.st_size > limit:",
            "                    raise TooLarge(before.st_size)",
        ),
        lines("                if False:", "                    raise TooLarge(before.st_size)"),
        IO_T,
        REASON_T
        + "test_a_file_too_large_says_its_size_and_the_limit_and_is_read_once_the_limit_is_raised",
    ),
    one(
        "what the first bytes say is ignored",
        FOLDER_IO,
        lines("                if verdict is not None:", "                    return verdict"),
        lines("                if False:", "                    return verdict"),
        IO_T,
        T_REASONS,
    ),
    one(
        "the hash leaves out the first bytes",
        FOLDER_IO,
        "                digest = hashlib.sha256(head)",
        "                digest = hashlib.sha256()",
        IO_T,
    ),
    one(
        "the first bytes are not copied",
        FOLDER_IO,
        "                _write_all(dest_fd, head)",
        "                pass",
        IO_T,
    ),
    one(
        "a file with two hard links is read",
        FOLDER_IO,
        "    if info.st_nlink != 1:",
        "    if False:",
        IO_T,
        REASON_T + "test_a_hard_link_is_skipped_and_the_file_it_points_at_is_never_read",
    ),
    one(
        "a folder that was replaced is read",
        FOLDER_IO,
        "        if expected is not None and identity(folder.fd) != expected:",
        "        if False:",
        IO_T,
        T_REPLACED,
    ),
    one(
        "a scan lets Workbench's own folders be read",
        FOLDER_IO,
        lines(
            "            check_root_path(folder.path, protected)",
            '            if sys.platform.startswith("linux"):',
            "                folder.restrict(False)",
            "            return walker(_Tree(folder))",
        ),
        lines(
            '            if sys.platform.startswith("linux"):',
            "                folder.restrict(False)",
            "            return walker(_Tree(folder))",
        ),
        IO_T,
    ),
    one(
        "a read lets Workbench's own folders be read",
        FOLDER_IO,
        lines(
            "            check_root_path(folder.path, protected)",
            '            if sys.platform.startswith("linux"):',
            "                folder.restrict(False)",
            "            with folder.file(names, shared=True) as fd:",
        ),
        lines(
            '            if sys.platform.startswith("linux"):',
            "                folder.restrict(False)",
            "            with folder.file(names, shared=True) as fd:",
        ),
        IO_T,
    ),
    (
        "a small file has no limit",
        [
            (
                FOLDER_IO,
                lines(
                    "            if os.fstat(fd).st_size > limit:",
                    "                raise TooLarge(os.fstat(fd).st_size)",
                ),
                lines("            if False:", "                raise TooLarge(0)"),
            ),
            (
                FOLDER_IO,
                lines(
                    "            if len(data) > limit:", "                raise TooLarge(len(data))"
                ),
                lines("            if False:", "                raise TooLarge(len(data))"),
            ),
        ],
        [IO_T, WALK_T],
    ),
    one(
        "a file kept online is a file",
        FOLDER_IO,
        "        return self.tag != 0 and (self.tag & 0xFFFF0FFF) == CLOUD_TAG",
        "        return False",
        IO_T,
    ),
    one(
        "a reparse point is a file or a folder",
        FOLDER_IO,
        lines(
            "    if attributes & _REPARSE:",
            "        return Entry(name, LINK, 0, mtime, hidden, tag)",
        ),
        lines("    if False:", "        return Entry(name, LINK, 0, mtime, hidden, tag)"),
        IO_T,
        REASON_T + "test_a_link_inside_the_folder_is_named_and_what_it_points_to_is_never_read",
    ),
    one(
        "a file that is not on this machine is read",
        FOLDER_IO,
        "    if attributes & (_OFFLINE | _RECALL_ON_OPEN | _RECALL_ON_DATA):",
        "    if False:",
        IO_T,
    ),
    one(
        "the system attribute does not hide",
        FOLDER_IO,
        "    hidden = bool(attributes & (_HIDDEN | _SYSTEM))",
        "    hidden = bool(attributes & _HIDDEN)",
        IO_T,
    ),
    one(
        "a modified time is read as the system keeps it",
        FOLDER_IO,
        "    mtime = (written - _FILETIME_UNIX) * 100",
        "    mtime = written * 100",
        IO_T,
    ),
    one(
        "a file is opened so that another program cannot save it",
        WINDOWS,
        "                    share=7 if shared else None,",
        "                    share=None,",
        IO_T,
    ),
    one(
        "a link is opened as the file it points to",
        WINDOWS,
        "            if os.fstat(fd).st_file_attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT",
        "            if False:  # FILE_ATTRIBUTE_REPARSE_POINT",
        IO_T,
        REASON_T
        + "test_a_folder_that_becomes_a_link_loses_its_documents_and_the_target_is_never_read",
    ),
    # --- the grant and the folder lock ---------------------------------------------------
    one(
        "another person's grant is read",
        FOLDERS,
        '        if grant is None or grant["subject"] != sub or await self.store.person(sub) is None:',
        "        if grant is None or await self.store.person(sub) is None:",
        T_NOT_YOURS,
        T_GRANT_NOT_YOURS,
        IO_T,
        INDEX_T,
        LIFE_T + "test_a_grant_withdrawn_while_workbench_was_stopped_is_found_when_it_starts",
    ),
    one(
        "a scan runs where folders cannot be used",
        FOLDERS,
        lines(
            "                raise folder_io.FolderError(reason)",
            "            grant = await self._grant_of(grant_id, sub)",
            "            return await self._io(",
            "                folder_io.scan,",
        ),
        lines(
            "                pass",
            "            grant = await self._grant_of(grant_id, sub)",
            "            return await self._io(",
            "                folder_io.scan,",
        ),
        T_UNREADABLE,
        PEOPLE_T + "test_without_workbenchs_own_account_folders_are_unavailable_and_say_why",
    ),
    one(
        "a scan does not wait for the other file calls",
        FOLDERS,
        lines(
            "        async with self.lock:",
            "            if reason := self.unavailable():",
            "                raise folder_io.FolderError(reason)",
            "            grant = await self._grant_of(grant_id, sub)",
            "            return await self._io(",
            "                folder_io.scan,",
        ),
        lines(
            "        async with asyncio.Lock():",
            "            if reason := self.unavailable():",
            "                raise folder_io.FolderError(reason)",
            "            grant = await self._grant_of(grant_id, sub)",
            "            return await self._io(",
            "                folder_io.scan,",
        ),
        T_WITHDRAWN_MID,
        PEOPLE_T + "test_a_folder_is_read_only_whatever_the_grant_allows",
        IO_T,
    ),
    one(
        "taking away the grant does not take the documents out",
        FOLDERS_API,
        "    await _state(request).folder_sync.grant_withdrawn(grant_id)",
        "    pass",
        T_WITHDRAWN,
    ),
    # --- the index: a folder's documents ---------------------------------------------------
    one(
        "an upload replaces a folder's file of the same name",
        INDEX,
        "                            \"AND source NOT LIKE 'folder:%' ORDER BY added_at DESC, rowid DESC\",",
        '                            "ORDER BY added_at DESC, rowid DESC",',
        INDEX_T,
        T_LISTING,
    ),
    one(
        "a file of a folder is not the document at its path",
        INDEX,
        lines(
            "                if new.source.startswith(FOLDER):",
            "                    # A file of a folder is the one at its path, whatever it is called: an edited",
        ),
        lines(
            "                if False and new.source.startswith(FOLDER):",
            "                    # A file of a folder is the one at its path, whatever it is called: an edited",
        ),
        INDEX_T,
        T_EDIT,
    ),
    one(
        "a touched file's time is not kept",
        INDEX,
        "                    if new.source.startswith(FOLDER) and (",
        "                    if False and new.source.startswith(FOLDER) and (",
        INDEX_T,
        T_TOUCHED,
    ),
    one(
        "a folder's range takes the next folder's documents",
        INDEX,
        '    return folder_prefix(grant_id), f"{FOLDER}{grant_id}0"',
        '    return folder_prefix(grant_id), f"{FOLDER}{grant_id}~"',
        INDEX_T,
    ),
    one(
        "the person's own documents hold a folder's files",
        INDEX,
        "        return \" AND source NOT LIKE 'folder:%'\"",
        '        return ""',
        INDEX_T,
        T_LISTING,
    ),
    one(
        "a folder's files hold the person's own",
        INDEX,
        "        return \" AND source LIKE 'folder:%'\"",
        '        return ""',
        INDEX_T,
    ),
    one(
        "a removal that fails leaves files moved",
        INDEX,
        lines(
            "        except BaseException:",
            "            self._unstage(staged)",
            "            raise",
            "        return staged",
        ),
        lines("        except BaseException:", "            raise", "        return staged"),
        INDEX_T,
        T_STUCK_REMOVE,
    ),
    one(
        "the files of a removed document are never deleted",
        INDEX,
        "                target.unlink(missing_ok=True)",
        "                pass",
        INDEX_T,
        T_WITHDRAWN,
    ),
    one(
        "a removal that fails does not put the files back",
        INDEX,
        "                target.replace(path)",
        "                pass",
        INDEX_T,
        T_STUCK_REMOVE,
    ),
    one(
        "a file already gone stops a removal",
        INDEX,
        lines(
            "                        except FileNotFoundError:",
            "                            break  # not there: nothing of it to remove",
        ),
        lines(
            "                        except FileNotFoundError:", "                            raise"
        ),
        INDEX_T,
        T_DELETE,
    ),
    one(
        "removing one folder removes another's documents",
        INDEX,
        '"SELECT * FROM documents WHERE source >= ? AND source < ?", (low, high)',
        '"SELECT * FROM documents WHERE source >= ? AND source < ?", ("folder:", "folder;")',
        INDEX_T,
        PEOPLE_T + "test_each_persons_folder_documents_are_in_their_own_index_and_nobody_elses",
    ),
    one(
        "a file's path is cut wrongly from its source",
        INDEX,
        '                    r["source"][cut:],',
        '                    r["source"][cut + 1 :],',
        INDEX_T,
    ),
    one(
        "the bytes a folder holds count the files that were not copied",
        INDEX,
        "COALESCE(SUM(CASE WHEN sha256 != '' THEN bytes ELSE 0 END), 0) AS k",
        "COALESCE(SUM(bytes), 0) AS k",
        INDEX_T,
        T_BYTES_CAP,
    ),
    one(
        "a folder's passages owed count the ready ones",
        INDEX,
        lines(
            '                copied += row["k"]',
            '                embedded += row["e"]',
            '                if row["status"] in (SEARCHABLE, EMBEDDING):',
        ),
        lines(
            '                copied += row["k"]',
            '                embedded += row["e"]',
            '                if row["status"] in (SEARCHABLE, EMBEDDING, READY):',
        ),
        INDEX_T,
        API_T + "test_a_granted_folder_is_added_and_its_files_become_documents",
    ),
    one(
        "the files of a folder are not filtered by state",
        INDEX,
        '                where, params = where + " AND status = ?", [*params, state]',
        "                pass",
        INDEX_T,
        REASON_T
        + "test_the_files_of_a_folder_can_be_listed_by_state_by_reason_and_by_a_word_in_the_path",
    ),
    one(
        "the files of a folder are not filtered by reason",
        INDEX,
        '                where, params = where + " AND code = ?", [*params, code]',
        "                pass",
        INDEX_T,
        REASON_T
        + "test_the_files_of_a_folder_can_be_listed_by_state_by_reason_and_by_a_word_in_the_path",
    ),
    one(
        "a word in the path is matched with its case",
        INDEX,
        "                params = [*params, len(low) + 1, match.lower()]",
        "                params = [*params, len(low) + 1, match]",
        INDEX_T,
        REASON_T
        + "test_the_files_of_a_folder_can_be_listed_by_state_by_reason_and_by_a_word_in_the_path",
    ),
    one(
        "a word is looked for in the folder's id too",
        INDEX,
        "                params = [*params, len(low) + 1, match.lower()]",
        "                params = [*params, 1, match.lower()]",
        INDEX_T,
        REASON_T
        + "test_the_files_of_a_folder_can_be_listed_by_state_by_reason_and_by_a_word_in_the_path",
    ),
    one(
        "the files of a folder are not in path order",
        INDEX,
        '                "ORDER BY lower(source), source LIMIT ? OFFSET ?",',
        '                "ORDER BY rowid LIMIT ? OFFSET ?",',
        INDEX_T,
        REASON_T
        + "test_the_files_of_a_folder_can_be_listed_by_state_by_reason_and_by_a_word_in_the_path",
    ),
    one(
        "a skipped file does not replace the document it was",
        INDEX,
        '                        db.execute("DELETE FROM documents WHERE id = ?", (old["id"],))',
        "                        pass",
        INDEX_T,
        T_REASONS,
    ),
    one(
        "a batch of skipped files that fails leaves files moved",
        INDEX,
        lines(
            "            except BaseException:",
            "                self._unstage(staged)",
            "                raise",
            "            self._discard(staged)",
            "            if removed:",
        ),
        lines(
            "            except BaseException:",
            "                raise",
            "            self._discard(staged)",
            "            if removed:",
        ),
        INDEX_T,
    ),
    one(
        "the time a read file is at is not kept",
        INDEX,
        "                        (mtime, size, doc_id),",
        "                        (None, size, doc_id),",
        INDEX_T,
        T_TOUCHED,
    ),
    one(
        "a move does not look where the document was",
        INDEX,
        '                    "WHERE id = ? AND source = ?",',
        '                    "WHERE id = ? AND (source = ? OR 1)",',
        INDEX_T,
    ),
    one(
        "boot finds no folder's documents",
        INDEX,
        "\"FROM documents WHERE source >= 'folder:' AND source < 'folder;'\"",
        "\"FROM documents WHERE source >= 'folder:' AND source < 'folder:'\"",
        INDEX_T,
        T_BOOT_ORPHANS,
    ),
    one(
        "files that were being removed when a stop came are not found",
        INDEX,
        "                found.extend(trash.iterdir())  # files that were being removed when a stop came",
        "                pass",
        INDEX_T,
    ),
    one(
        "removing a folder does not end what reads its files",
        SAVED,
        "            await self._cancel(sub, [h.doc_id for h in await index.folder_files(grant_id)])",
        "            pass",
        T_REMOVED_MID,
        T_WITHDRAWN_MID,
        T_REMOVED_ENDS_REQUEST,
    ),
    one(
        "a moved document's sidecar is not rewritten",
        SAVED,
        lines(
            "        if moved is not None:",
            "            await asyncio.to_thread(",
            "                index.write_sidecar,",
        ),
        lines(
            "        if False:",
            "            await asyncio.to_thread(",
            "                index.write_sidecar,",
        ),
        INDEX_T,
        LIFE_T + "test_a_renamed_folder_keeps_every_document_its_passages_and_its_vectors",
    ),
    # --- a check ---------------------------------------------------------------------------
    one(
        "a check uses the rules the loop saw, not the rules now",
        SYNC,
        lines("        record = current", "        began = time.time()"),
        "        began = time.time()",
        SYNC_T
        + "test_a_rule_the_person_changes_while_the_loop_is_deciding_is_the_rule_the_check_uses",
    ),
    one(
        "a check of a folder that was withdrawn goes on",
        SYNC,
        lines(
            '        if current is None or current.status != "active":',
            "            raise _Stopped",
        ),
        lines("        if current is None:", "            raise _Stopped"),
        T_WITHDRAWN_MID,
        T_WITHDRAWN,
        T_WITHDRAWN_BEFORE_CHECK,
    ),
    one(
        "a file that is gone is not removed",
        SYNC,
        lines(
            "        if plan.remove:",
            "            await self._remove(sub, state, [h.doc_id for h in plan.remove])",
        ),
        lines(
            "        if False:",
            "            await self._remove(sub, state, [h.doc_id for h in plan.remove])",
        ),
        T_DELETE,
    ),
    one(
        "a folder that cannot be looked at loses its documents",
        SYNC,
        "        if not (gone or replaced):",
        "        if False:",
        T_UNREADABLE,
    ),
    one(
        "a folder that is not there keeps its documents",
        SYNC,
        "        gone = isinstance(exc.cause, FileNotFoundError | NotADirectoryError)",
        "        gone = False",
        T_GONE,
    ),
    one(
        "a folder that was replaced keeps its documents",
        SYNC,
        '        replaced = exc.code == "replaced"',
        "        replaced = False",
        T_REPLACED,
    ),
    one(
        "a folder that is not there cannot come back",
        SYNC,
        "        removed = await self._purge(sub, grant_id, close=False)",
        "        removed = await self._purge(sub, grant_id, close=True)",
        LIFE_T
        + "test_a_folder_that_is_not_there_is_looked_for_at_every_check_and_comes_back_by_itself",
    ),
    one(
        "the loop does not withdraw a grant that is gone",
        SYNC,
        lines(
            "            if not _granted(grants, record):",
            "                await self._withdraw(record)",
            "                records.remove(record)",
        ),
        lines(
            "            if False:",
            "                await self._withdraw(record)",
            "                records.remove(record)",
        ),
        LIFE_T[:-2],
    ),
    one(
        "checks go on while documents are off",
        SYNC,
        lines("        if not (await self._documents()).enabled:", "            return TICK"),
        lines("        if False:", "            return TICK"),
        T_OFF,
    ),
    one(
        "a scheduled check goes before a manual one",
        SYNC,
        "        due.sort(key=lambda item: item[:2])",
        "        due.sort(key=lambda item: item[1:2])",
        T_ORDER,
    ),
    one(
        "a folder is checked at every look",
        SYNC,
        "            elif now >= record.checked_at + interval:",
        "            elif now >= record.checked_at:",
        T_INTERVAL,
        T_LIMITS,
        T_NOT_BEFORE_INTERVAL,
    ),
    one(
        "a grant gone under a check does not withdraw the folder",
        SYNC,
        lines(
            '            if current is not None and current.status == "active":',
            "                await self._withdraw(current)",
        ),
        "            pass",
        T_WITHDRAWN_MID,
        T_PERSON_GONE,
    ),
    one(
        "a file that cannot be deleted is said to be an internal failure",
        SYNC,
        lines(
            "        except DeleteFailed as exc:",
            '            await self._problem(record, "delete_failed", exc.message)',
        ),
        lines(
            "        except ZeroDivisionError as exc:",
            '            await self._problem(record, "delete_failed", exc.message)',
        ),
        T_STUCK_REMOVE,
        T_STUCK_WITHDRAW,
        T_STUCK_IN_CHECK,
    ),
    one(
        "a full disk is said to be an internal failure",
        SYNC,
        '                "disk_full",',
        '                "internal",',
        T_DISK_FULL,
    ),
    one(
        "a folder that could not be checked is checked again at once",
        SYNC,
        "        self._rest[(record.owner, record.grant_id)] = time.perf_counter() + BACKOFF",
        "        pass",
        T_APART,
        T_UNREADABLE,
        T_DISK_FULL,
        T_LEFT_ALONE,
    ),
    one(
        "a check that ended badly is not dated",
        SYNC,
        lines(
            "            checked_at=now,",
            "        )",
            '        log.warning("a folder could not be checked (%s)", code)',
        ),
        lines("        )", '        log.warning("a folder could not be checked (%s)", code)'),
        T_APART,
        T_UNREADABLE,
        T_DISK_FULL,
    ),
    one(
        "the files read again are not counted as unchanged",
        SYNC,
        '            "unchanged": len(plan.keep) + len(plan.unknown) + tally["rechecked"],',
        '            "unchanged": len(plan.keep) + len(plan.unknown),',
        T_TOUCHED,
    ),
    one(
        "the files kept count nothing against the folder's size",
        SYNC,
        "            used=sum(h.size for h in [*plan.keep, *plan.unknown] if h.copied),",
        "            used=0,",
        T_BYTES_CAP,
        T_BYTES_LATER,
    ),
    one(
        "a picture is read as a document",
        SYNC,
        "            if not got.is_document:",
        "            if False:",
        T_REASONS,
    ),
    one(
        "a file that would pass the folder's size is read",
        SYNC,
        "            if size > room:",
        "            if False:",
        T_BYTES_CAP,
    ),
    one(
        "a file that changed while it was read is said to be unreadable",
        SYNC,
        '                await self._skip(found, held, "changed", CHANGED)',
        '                await self._skip(found, held, "unreadable", CHANGED)',
        T_CHANGING,
    ),
    one(
        "a full disk while reading is an unreadable file",
        SYNC,
        "        if isinstance(cause, OSError) and cause.errno == errno.ENOSPC:",
        "        if False:",
        T_DISK_FULL,
    ),
    one(
        "a link swapped in is said to be unreadable",
        SYNC,
        lines('            "link": LINKED,', '            "mount": MOUNTED,'),
        '            "mount": MOUNTED,',
        T_SWAPPED,
    ),
    one(
        "a hard link is said to be unreadable",
        SYNC,
        lines('            "hard_links": HARD_LINKS,', '            "special": SPECIAL,'),
        '            "special": SPECIAL,',
        T_SWAPPED,
    ),
    one(
        "a file deleted while it was read is said to be unreadable",
        SYNC,
        "        elif isinstance(cause, FileNotFoundError):",
        "        elif False:",
        T_VANISHED_READ,
    ),
    one(
        "a file another program holds is said to be unreadable",
        SYNC,
        "        elif is_busy(cause):",
        "        elif False:",
        T_BUSY,
        SYNC_T + "test_a_busy_file_is_told_from_a_denied_one_and_a_link_from_a_missing_file",
    ),
    one(
        "a file just saved is trusted",
        SYNC,
        "        racy = 0 <= time.time_ns() - taken.mtime_ns < RACY_NS",
        "        racy = False",
        T_RACY,
    ),
    one(
        "an edited file is taken for the same",
        SYNC,
        "        if held is not None and held.copied and held.sha256 == taken.sha256:",
        "        if held is not None and held.copied:",
        T_EDIT,
    ),
    one(
        "a file that looks like a moved one but says something else is a move",
        SYNC,
        '        elif how == "move" and held is not None:',
        "        elif False:",
        T_NOT_A_MOVE,
    ),
    one(
        "a file read again to the same words is read again at every check",
        SYNC,
        "                await self.index.set_signature(held.doc_id, signature, taken.size)",
        "                pass",
        T_TOUCHED,
    ),
    one(
        "a zip that holds no document is read",
        SYNC,
        "        if detected.zipped and await asyncio.to_thread(files.zip_holds_document, part) is False:",
        "        if False:",
        T_REASONS,
    ),
    one(
        "an edited file is counted as added",
        SYNC,
        '        self.tally["added" if held is None or not held.copied else "changed"] += 1',
        '        self.tally["added"] += 1',
        T_EDIT,
    ),
    # --- what people do ----------------------------------------------------------------------
    one(
        "the ignore list may be filled past its limit",
        SYNC,
        "            if len(record.ignore) >= folder_rules.MAX_PATTERNS:",
        "            if False:",
        IGN_T + "test_a_full_ignore_list_says_so_and_the_file_is_not_removed",
    ),
    one(
        "a grant that is not yours can be added",
        SYNC,
        '                if g["id"] == grant_id and visible(g, person)',
        '                if g["id"] == grant_id',
        T_GRANT_NOT_YOURS,
        T_OWNER_OFFERED,
    ),
    one(
        "a folder can be added twice",
        SYNC,
        "        if not await self.store.add_folder_record(record):",
        "        if False:",
        T_TWICE,
    ),
    one(
        "changing a rule does not check the folder",
        SYNC,
        lines(
            '        if record is not None and record.status == "active":',
            "            self.request(sub, grant_id, manual=False)",
            "        return record",
        ),
        "        return record",
        IGN_T
        + "test_changing_the_list_takes_files_out_at_once_and_brings_them_back_when_it_is_changed_again",
        IGN_T + "test_a_hidden_file_is_read_when_the_person_turns_hidden_on_for_that_folder",
    ),
    one(
        "Sync now is not a manual check",
        SYNC,
        "        want.manual = want.manual or manual",
        "        want.manual = False",
        REASON_T[:-2],
    ),
    one(
        "a deep Sync now is not deep",
        SYNC,
        "        want.full = want.full or full",
        "        want.full = False",
        T_SAME_STAT,
    ),
    one(
        "Sync now waits out the rest after a problem",
        SYNC,
        lines("        self._rest.pop((sub, grant_id), None)", "        self._wake.set()"),
        "        self._wake.set()",
        LIFE_T[:-2],
        REASON_T[:-2],
        T_LEFT_ALONE,
    ),
    one(
        "a folder that could not be removed stays closed",
        SYNC,
        "            self.state(sub, grant_id).closed = False  # still the person's folder: checked as before",
        "            pass",
        T_STUCK_REMOVE,
    ),
    one(
        "a withdrawn folder with stuck files fails the owner's request",
        SYNC,
        lines(
            "                except DeleteFailed as exc:",
            "                    # The grant is gone and nothing more will be added; a file that cannot be",
        ),
        lines(
            "                except ZeroDivisionError as exc:",
            "                    # The grant is gone and nothing more will be added; a file that cannot be",
        ),
        T_STUCK_WITHDRAW,
    ),
    one(
        "a withdrawn folder does not say how many documents left",
        SYNC,
        lines(
            '                    "removed": removed,',
            "                },",
            "            )",
            '            log.info("a folder was withdrawn: %d document(s) removed", removed)',
        ),
        lines(
            '                    "removed": 0,',
            "                },",
            "            )",
            '            log.info("a folder was withdrawn: %d document(s) removed", removed)',
        ),
        T_WITHDRAWN,
        T_WITHDRAW_RACE,
    ),
    one(
        "a removal does not close the folder to adds",
        SYNC,
        lines("            if close:", "                state.closed = True"),
        lines("            if close:", "                pass"),
        T_REMOVED_MID,
        T_WITHDRAWN_MID,
    ),
    one(
        "boot keeps documents of folders the person no longer has",
        SYNC,
        "                    if (sub, grant_id) not in kept:",
        "                    if False:",
        T_BOOT_ORPHANS,
    ),
    one(
        "boot does not withdraw a folder whose grant is gone",
        SYNC,
        '            if record.status == "active" and not _granted(grants, record):',
        "            if False:",
        T_BOOT_WITHDRAWN,
    ),
    one(
        "a grant of another person keeps the folder",
        SYNC,
        '    return grant is not None and grant["subject"] == record.owner',
        "    return grant is not None",
        T_BOOT_WITHDRAWN,
        LIFE_T[:-2],
        T_GRANTED,
    ),
    one(
        "boot does not check the folders",
        SYNC,
        lines(
            "        for record in await self.store.folder_records():",
            '            if record.status == "active":',
            "                self.request(record.owner, record.grant_id, manual=False)",
        ),
        lines(
            "        for record in await self.store.folder_records():",
            "            if False:",
            "                self.request(record.owner, record.grant_id, manual=False)",
        ),
        T_BOOT_CHECK,
    ),
    # --- the routes --------------------------------------------------------------------------
    one(
        "a folder on another machine is taken",
        DOCS_API,
        '    if body.grantId.startswith("node:"):',
        '    if False and body.grantId.startswith("node:"):',
        PEOPLE_T + "test_a_folder_on_another_machine_is_refused_in_words_and_nothing_is_kept",
    ),
    one(
        "a folder is added where folders cannot be used",
        DOCS_API,
        lines(
            "    if reason := state.tools.folders.unavailable():",
            "        raise _refusal(status.HTTP_503_SERVICE_UNAVAILABLE, reason, _UNAVAILABLE)",
        ),
        lines(
            "    if False:",
            '        raise _refusal(status.HTTP_503_SERVICE_UNAVAILABLE, "", _UNAVAILABLE)',
        ),
        PEOPLE_T + "test_without_workbenchs_own_account_folders_are_unavailable_and_say_why",
    ),
    one(
        "a folder is added while documents are off",
        DOCS_API,
        "    if not (await config.document_settings(_state(request).store)).enabled:",
        "    if False:",
        PEOPLE_T
        + "test_while_the_owner_has_documents_off_a_folder_cannot_be_added_or_changed_but_can_be_read_and_removed",
    ),
    one(
        "a folder that is not there is not called unavailable",
        DOCS_API,
        '    if code in ("gone", "replaced"):',
        "    if False:",
        T_STATES,
        T_GONE,
    ),
    one(
        "a withdrawn folder is not called withdrawn",
        DOCS_API,
        lines('    if record.status == "withdrawn":', '        return "withdrawn"'),
        lines("    if False:", '        return "withdrawn"'),
        T_STATES,
        T_WITHDRAWN,
    ),
    one(
        "a folder under a check is not called syncing",
        DOCS_API,
        lines("    if run is not None:", '        return "syncing"'),
        lines("    if False:", '        return "syncing"'),
        T_STATES,
        API_T
        + "test_a_new_folder_says_so_before_its_first_check_and_how_far_it_has_read_while_it_reads",
    ),
    one(
        "the headline does not say how often a folder is checked",
        DOCS_API,
        "        f\"Checked {when}, and again every {plural(sync_minutes, 'minute')}. {count_words(counts)}.\"",
        '        f"Checked {when}. {count_words(counts)}."',
        T_HEADLINE,
        API_T + "test_the_headline_says_when_it_was_checked_how_often_and_what_the_folder_holds",
    ),
    one(
        "the bytes used count the files that were not copied",
        DOCS_API,
        '            "used": counts.copied,',
        '            "used": counts.bytes,',
        T_LIMITS,
        T_BYTES_CAP,
    ),
    one(
        "a cap that was reached is not said",
        DOCS_API,
        '            "reached": bool(caps.get("files")),',
        '            "reached": False,',
        T_LIMITS,
        REASON_T
        + "test_the_most_files_a_folder_gives_keeps_the_shallow_ones_and_says_what_was_left_out",
    ),
    one(
        "the next check is shown for a folder that is not synced",
        DOCS_API,
        '            if state == "synced" and record.checked_at is not None',
        "            if record.checked_at is not None",
        T_LIMITS,
        T_STATES,
        API_T
        + "test_a_new_folder_says_so_before_its_first_check_and_how_far_it_has_read_while_it_reads",
        T_STUCK_IN_CHECK,
    ),
    one(
        "a withdrawn folder is called granted",
        DOCS_API,
        '        "granted": grant is not None and record.status == "active",',
        '        "granted": True,',
        T_WITHDRAWN,
    ),
    one(
        "the grants offered are not the person's",
        DOCS_API,
        lines("            if visible(g, person)", "        ],", '        "limits"'),
        lines("            if True", "        ],", '        "limits"'),
        T_OWNER_OFFERED,
        API_T + "test_a_person_sees_the_granted_folders_they_may_add_and_never_a_path",
    ),
    one(
        "everyone's folders are listed to everyone",
        DOCS_API,
        "    records = await state.store.folder_records(person.sub)",
        "    records = await state.store.folder_records()",
        T_NOT_YOURS,
        PEOPLE_T + "test_nobody_finds_or_reads_another_persons_folder_documents_the_owner_included",
    ),
    one(
        "a granted folder is not marked added",
        DOCS_API,
        '                "added": g["id"] in added,',
        '                "added": False,',
        API_T + "test_a_person_sees_the_granted_folders_they_may_add_and_never_a_path",
        T_TWICE,
    ),
    one(
        "folders are available whatever the install",
        DOCS_API,
        '        "available": reason is None,',
        '        "available": True,',
        PEOPLE_T + "test_without_workbenchs_own_account_folders_are_unavailable_and_say_why",
    ),
    one(
        "a withdrawn folder can be changed",
        DOCS_API,
        lines(
            "    await _enabled(request)",
            '    if record.status != "active":',
            '        raise _problem(status.HTTP_409_CONFLICT, "The owner took away your access to this folder.")',
            "    try:",
        ),
        lines("    await _enabled(request)", "    try:"),
        T_WITHDRAWN,
    ),
    one(
        "a withdrawn folder can be synced",
        DOCS_API,
        lines(
            '    if record.status != "active":',
            '        raise _problem(status.HTTP_409_CONFLICT, "The owner took away your access to this folder.")',
            "    _sync(request).request(",
        ),
        "    _sync(request).request(",
        T_WITHDRAWN,
    ),
    one(
        "Sync now from the page is not manual",
        DOCS_API,
        "    _sync(request).request(person.sub, grant_id, manual=True, full=bool(body and body.deep))",
        "    _sync(request).request(person.sub, grant_id, manual=False, full=bool(body and body.deep))",
        REASON_T[:-2],
        API_T[:-2],
    ),
    one(
        "a deep Sync now from the page is not deep",
        DOCS_API,
        "    _sync(request).request(person.sub, grant_id, manual=True, full=bool(body and body.deep))",
        "    _sync(request).request(person.sub, grant_id, manual=True, full=False)",
        T_SAME_STAT,
    ),
    one(
        "a state that is not one is taken",
        DOCS_API,
        "    if state is not None and state not in STATES:",
        "    if False:",
        REASON_T
        + "test_the_files_of_a_folder_can_be_listed_by_state_by_reason_and_by_a_word_in_the_path",
    ),
    one(
        "a page of files may be any size",
        DOCS_API,
        "    limit: int = Query(default=200, ge=1, le=500),",
        "    limit: int = Query(default=200, ge=1, le=5000),",
        REASON_T
        + "test_the_files_of_a_folder_can_be_listed_by_state_by_reason_and_by_a_word_in_the_path",
    ),
    one(
        "a folder's detail leaves out what was left out",
        DOCS_API,
        "    return await folder_view(request, person, await _folder(request, person, grant_id), detail=True)",
        "    return await folder_view(request, person, await _folder(request, person, grant_id), detail=False)",
        IGN_T + "test_what_was_ignored_is_in_the_folders_detail_and_not_in_its_listing",
        REASON_T + "test_a_link_inside_the_folder_is_named_and_what_it_points_to_is_never_read",
    ),
    # --- the Documents routes that a folder's files share ----------------------------------------
    one(
        "the person's own documents hold a folder's files in the list",
        SAVED_API,
        '        documents = await index.documents(LIBRARY, only="upload")',
        "        documents = await index.documents(LIBRARY)",
        T_LISTING,
    ),
    one(
        "the person's own totals count a folder's files",
        SAVED_API,
        '        totals = await index.totals(LIBRARY, only="upload")',
        "        totals = await index.totals(LIBRARY)",
        T_LISTING,
    ),
    one(
        "removing a file of a folder lets it come back",
        SAVED_API,
        lines(
            "    split = index_module.split_source(doc.source)",
            "    if split is not None:",
            "        # A file of a folder would come back at the next check: removing it is telling Workbench",
        ),
        lines(
            "    split = index_module.split_source(doc.source)",
            "    if False:",
            "        # A file of a folder would come back at the next check: removing it is telling Workbench",
        ),
        IGN_T + "test_removing_one_file_of_a_folder_stops_it_being_read_and_names_it_in_the_list",
    ),
    one(
        "a full ignore list is a server error",
        SAVED_API,
        lines(
            "        except IgnoreListFull:",
            "            raise _problem(",
            "                status.HTTP_409_CONFLICT,",
        ),
        lines(
            "        except ZeroDivisionError:",
            "            raise _problem(",
            "                status.HTTP_409_CONFLICT,",
        ),
        IGN_T + "test_a_full_ignore_list_says_so_and_the_file_is_not_removed",
    ),
    one(
        "a file of a folder that was not read is told to be added again",
        SAVED_API,
        lines(
            "        if index_module.split_source(doc.source) is not None:",
            "            raise _problem(",
        ),
        lines("        if False:", "            raise _problem("),
        API_T + "test_a_folder_file_opens_as_its_own_file_shows_its_text_and_can_be_read_again",
        REASON_T + "test_every_file_that_cannot_be_read_is_a_document_with_its_reason_and_code",
    ),
    one(
        "a folder file is downloaded under its path",
        SAVED_API,
        '        filename=doc.name.rsplit("/", 1)[-1],',
        "        filename=doc.name,",
        API_T + "test_a_folder_file_opens_as_its_own_file_shows_its_text_and_can_be_read_again",
    ),
    one(
        "a question cannot name a file of a folder by its own name",
        SEARCH,
        '    name = name.rsplit("/", 1)[-1]',
        "    name = name",
        API_T
        + "test_a_question_that_names_the_file_by_its_own_name_finds_it_not_only_by_its_folder_path",
        API_T + "test_a_path_in_a_files_name_does_not_stop_a_question_from_naming_the_file",
    ),
    # --- the store and the settings ----------------------------------------------------------------
    one(
        "a folder is found by anyone who knows its id",
        STORE,
        lines(
            '                    "SELECT * FROM document_folders WHERE owner = ? AND grant_id = ?",',
            "                    (owner, grant_id),",
            "                )",
            "                .fetchone()",
        ),
        lines(
            '                    "SELECT * FROM document_folders WHERE owner = ? OR grant_id = ?",',
            "                    (owner, grant_id),",
            "                )",
            "                .fetchone()",
        ),
        T_NOT_YOURS,
        STORE_T,
    ),
    one(
        "a folder is changed by anyone who knows its id",
        STORE,
        '                    f"UPDATE document_folders SET {columns} WHERE owner = ? AND grant_id = ?",',
        '                    f"UPDATE document_folders SET {columns} WHERE owner = ? OR grant_id = ?",',
        T_NOT_YOURS,
        SYNC_T[:-2],
        LIFE_T[:-2],
        IGN_T[:-2],
        STORE_T,
    ),
    one(
        "a folder is removed by anyone who knows its id",
        STORE,
        '                "DELETE FROM document_folders WHERE owner = ? AND grant_id = ?", (owner, grant_id)',
        '                "DELETE FROM document_folders WHERE owner = ? OR grant_id = ?", (owner, grant_id)',
        T_NOT_YOURS,
        LIFE_T[:-2],
        STORE_T,
    ),
    one(
        "everyone's folders are one person's",
        STORE,
        lines(
            '        """Every folder, or one person\'s, oldest first."""',
            "",
            "        def go() -> list[FolderRecord]:",
            '            where, params = ("", ()) if owner is None else (" WHERE owner = ?", (owner,))',
        ),
        lines(
            '        """Every folder, or one person\'s, oldest first."""',
            "",
            "        def go() -> list[FolderRecord]:",
            '            where, params = ("", ())',
        ),
        T_NOT_YOURS,
        PEOPLE_T + "test_nobody_finds_or_reads_another_persons_folder_documents_the_owner_included",
        STORE_T,
    ),
    one(
        "adding a folder twice replaces it",
        STORE,
        '"INSERT OR IGNORE INTO document_folders (owner, grant_id, name, ignore, "',
        '"INSERT OR REPLACE INTO document_folders (owner, grant_id, name, ignore, "',
        T_TWICE,
        STORE_T,
    ),
    one(
        "a field that is not a folder's is written",
        STORE,
        lines(
            "        if unknown:",
            '            raise ValueError(f"not folder fields: {sorted(unknown)}")',
        ),
        lines(
            "        if False:",
            '            raise ValueError(f"not folder fields: {sorted(unknown)}")',
        ),
        LIFE_T[:-2],
        SYNC_T[:-2],
        STORE_T,
    ),
    one(
        "a setting takes a boolean for a number",
        CONFIG,
        "        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:",
        "        if isinstance(value, int) and low <= value <= high:",
        T_SETTINGS,
    ),
    one(
        "a setting takes a number outside its range",
        CONFIG,
        "        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:",
        "        if isinstance(value, int) and not isinstance(value, bool) and True:",
        T_SETTINGS,
    ),
    one(
        "the depth setting takes a thousand",
        CONFIG,
        "        DOCUMENTS_FOLDER_MAX_DEPTH: (MIN_FOLDER_DEPTH, MAX_FOLDER_DEPTH),",
        "        DOCUMENTS_FOLDER_MAX_DEPTH: (MIN_FOLDER_DEPTH, 1_000),",
        T_SETTINGS,
    ),
    one(
        "the interval setting is not what is used",
        CONFIG,
        "        sync_minutes=await number(DOCUMENTS_FOLDER_SYNC_MINUTES),",
        "        sync_minutes=10,",
        T_LIMITS,
        PEOPLE_T + "test_the_installs_folder_settings_are_in_the_console_with_what_unset_means",
    ),
    one(
        "a megabyte is a million bytes",
        CONFIG,
        "        return self.max_mb * 1024 * 1024",
        "        return self.max_mb * 1000 * 1000",
        T_LIMITS,
        T_BYTES_CAP,
    ),
    one(
        "the console shows the default file cap",
        CONFIG,
        "            DOCUMENTS_FOLDER_MAX_FILES: folders.max_files,",
        "            DOCUMENTS_FOLDER_MAX_FILES: 10_000,",
        PEOPLE_T + "test_the_installs_folder_settings_are_in_the_console_with_what_unset_means",
        T_SETTINGS,
    ),
    one(
        "a check does not use the file cap setting",
        SYNC,
        "        caps = Caps(max_files=settings.max_files, max_depth=settings.max_depth)",
        "        caps = Caps(max_files=10_000, max_depth=settings.max_depth)",
        REASON_T
        + "test_the_most_files_a_folder_gives_keeps_the_shallow_ones_and_says_what_was_left_out",
    ),
    one(
        "a check does not use the depth cap setting",
        SYNC,
        "        caps = Caps(max_files=settings.max_files, max_depth=settings.max_depth)",
        "        caps = Caps(max_files=settings.max_files, max_depth=12)",
        REASON_T + "test_a_folder_deeper_than_the_cap_is_not_entered_and_is_named",
    ),
    one(
        "a check does not use the size cap setting",
        SYNC,
        "        room = self.settings.max_bytes - self.used",
        "        room = 2_048 * 1024 * 1024 - self.used",
        T_BYTES_CAP,
    ),
    # --- found by the first pass, each with the check that now catches it -------------------------
    one(
        "a withdrawal is done by whoever comes, not once",
        SYNC,
        "        async with state.withdrawal:",
        "        async with asyncio.Lock():",
        T_WITHDRAW_RACE,
        T_WITHDRAWN,
    ),
    one(
        "a folder that is withdrawn already is withdrawn again",
        SYNC,
        lines(
            '            if current is None or current.status != "active":',
            "                return 0",
        ),
        lines("            if current is None:", "                return 0"),
        T_WITHDRAW_RACE,
    ),
    one(
        "a saved list loses a space that was part of a name",
        RULES,
        r'    while text[-1:].isspace() and not text.endswith("\\ "):',
        "    while text[-1:].isspace():",
        RULES_T,
    ),
    one(
        "a saved list keeps the spaces in front of a pattern",
        RULES,
        "    text = raw.lstrip()",
        "    text = raw",
        RULES_T,
    ),
]


#: Cases for the code that only Linux runs (`folder_linux.py`): added to the list where Linux is the
#: platform, so that run them there (Ubuntu under WSL, or a Linux box) with `--labels Linux`.
LINUX_CASES: list[Case] = [
    one(
        "a link in a Linux listing is a file",
        LINUX,
        lines("                    if entry.is_symlink():", "                        kind = LINK"),
        lines("                    if False:", "                        kind = LINK"),
        IO_T,
    ),
    one(
        "a folder in a Linux listing is a file",
        LINUX,
        lines(
            "                    elif entry.is_dir(follow_symlinks=False):",
            "                        kind = DIRECTORY",
        ),
        lines(
            "                    elif entry.is_dir(follow_symlinks=False):",
            "                        kind = FILE",
        ),
        IO_T,
    ),
    one(
        "a pipe in a Linux listing is a file",
        LINUX,
        lines("                    else:", "                        kind = OTHER"),
        lines("                    else:", "                        kind = FILE"),
        IO_T,
    ),
    one(
        "a Linux listing gives a link the size of what it points to",
        LINUX,
        "                size = info.st_size if kind == FILE else 0",
        "                size = info.st_size",
        IO_T,
    ),
    one(
        "a Linux read takes the lock an editor wants",
        LINUX,
        "                if not shared:",
        "                if True:",
        IO_T,
    ),
]
if sys.platform == "linux":
    CASES += LINUX_CASES

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
for key in [k for k in ENV if k.startswith("EUGENE_PLEXUS_")]:
    del ENV[key]


def run(checks: list[str]) -> tuple[bool, str]:
    """(passed, output) for these checks."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *checks],
            cwd=ROOT,
            env=ENV,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return False, "the checks did not finish in 600 seconds"
    return result.returncode == 0, result.stdout + result.stderr


def run_all(checks: list[str]) -> tuple[bool, str]:
    """Every check, a few at a time in one pytest process each: the baseline before the
    cases and the baseline after them. (passed, output of the first group that failed)."""
    batch = 24
    for start in range(0, len(checks), batch):
        group = checks[start : start + batch]
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *group],
                cwd=ROOT,
                env=ENV,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=1800,
            )
        except subprocess.TimeoutExpired:
            return False, "a group of checks did not finish in 30 minutes"
        if result.returncode != 0:
            return False, result.stdout + result.stderr
    return True, ""


def broken_build(output: str) -> bool:
    """A mutation that stops the code compiling or importing proves nothing."""
    markers = ("SyntaxError", "ERROR collecting", "ImportError", "IndentationError", "NameError")
    return any(m in output for m in markers)


def anchors() -> None:
    for label, edits, _ in CASES:
        for name, before, _after in edits:
            text = (ROOT / name).read_text(encoding="utf-8")
            found = text.count(before)
            if found != 1:
                raise SystemExit(f"{name}: the anchor for '{label}' is in {found} places; fix it.")
    print(f"{len(CASES)} cases, each anchor in exactly one place.")


def main() -> int:
    anchors()
    if "--anchors" in sys.argv:
        return 0
    cases = CASES
    if "--labels" in sys.argv:
        words = sys.argv[sys.argv.index("--labels") + 1].split(",")
        cases = [c for c in CASES if any(w in c[0] for w in words)]
        print(f"{len(cases)} cases chosen.")
    if "--shard" in sys.argv:
        # `--shard 2/4`: every fourth case from the second, so that copies of the tree can run
        # the cases side by side (each mutates only its own copy).
        which, _, of = sys.argv[sys.argv.index("--shard") + 1].partition("/")
        cases = cases[int(which) - 1 :: int(of)]
        print(f"{len(cases)} cases in this shard.")
    checks = sorted({check for _, _, cs in cases for check in cs})
    passed, output = run_all(checks)
    if not passed:
        print(output[-3000:])
        raise SystemExit("Baseline failed; no files changed.")
    print(f"Baseline: {len(checks)} checks pass.", flush=True)
    backup = Path(tempfile.mkdtemp(prefix="workbench-folders-sabotage-"))
    print(f"Exact backups: {backup}", flush=True)
    caught = 0
    escaped: list[str] = []
    for index, (label, edits, case_checks) in enumerate(cases):
        originals: dict[str, bytes] = {}
        try:
            for name, before, after in edits:
                path = ROOT / name
                if name not in originals:
                    originals[name] = path.read_bytes()
                    (backup / f"{index}-{path.name}").write_bytes(originals[name])
                text = path.read_bytes().decode("utf-8")
                path.write_bytes(text.replace(before, after).encode("utf-8"))
            passed, output = run(case_checks)
            ok = not passed and not broken_build(output)
            print(f"{'CAUGHT' if ok else 'ESCAPED'}: {label}", flush=True)
            if not ok:
                print(output[-3000:])
                escaped.append(label)
            caught += ok
        finally:
            for name, original in originals.items():
                (ROOT / name).write_bytes(original)
                assert (ROOT / name).read_bytes() == original
    passed, output = run_all(checks)
    if not passed:
        print(output[-3000:])
        raise SystemExit("Restored baseline failed.")
    print(f"{caught}/{len(cases)} caught; restored baseline passes.")
    for label in escaped:
        print(f"  escaped: {label}")
    return 0 if caught == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
