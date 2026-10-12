"""Prove the result-sorter checks are observed (MR6b).

Each case breaks one thing in which passages a sorter is given and as what, the order that comes
back and what follows it, which sorter is chosen, whether it is quick enough, how fast it is said
to read, what a sorter that fails does to a search, what a person is told, or the settings, and its
named checks must fail. Files are restored from exact bytes kept beside the run, never from Git. A
mutation that stops the code importing or compiling does not count as a caught defect.

  python scripts/check-sorting-sabotage.py            # every case
  python scripts/check-sorting-sabotage.py --anchors  # only check each anchor is unique
  python scripts/check-sorting-sabotage.py --labels heading,budget   # cases whose label has these words

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
SORTING = SRC + "knowledge/sorting.py"
SEARCH = SRC + "knowledge/search.py"
SAVED = SRC + "knowledge/saved.py"
SAVED_API = SRC + "saved_api.py"
HUB = SRC + "hub.py"
CONFIG = SRC + "config.py"

UNIT = "tests/test_sorting.py::"
FOUND = "tests/test_sorting_search.py::"
HUB_T = "tests/test_hub_rerank.py::"
API = "tests/test_sorting_api.py::"
CHATS = "tests/test_chats.py::test_settings_are_changed_only_with_the_agents_admin_token"

#: (label, [(file, before, after), ...], [check, ...])
Case = tuple[str, list[tuple[str, str, str]], list[str]]


def one(label: str, name: str, before: str, after: str, *checks: str) -> Case:
    return (label, [(name, before, after)], list(checks))


CASES: list[Case] = [
    # --- which passages, and as what (calls 1 and 2) -------------------------------------
    one(
        "the heading path is not sent",
        SEARCH,
        "        embedders.passage_input(embedders.PLAIN, hit.heading_path, hit.text), hit.tokens_est",
        "        embedders.passage_input(embedders.PLAIN, None, hit.text), hit.tokens_est",
        FOUND + "test_a_passage_is_given_as_its_heading_path_a_new_line_and_its_words",
        FOUND + "test_the_headings_go_to_the_sorter_through_the_retriever",
        API
        + "test_the_best_passages_are_sent_to_the_first_local_running_sorter_as_their_headings_and_words",
    ),
    one(
        "the heading path goes after the words",
        SEARCH,
        "        embedders.passage_input(embedders.PLAIN, hit.heading_path, hit.text), hit.tokens_est",
        '        hit.text + "\\n" + (hit.heading_path or ""), hit.tokens_est',
        FOUND + "test_a_passage_is_given_as_its_heading_path_a_new_line_and_its_words",
        API
        + "test_the_best_passages_are_sent_to_the_first_local_running_sorter_as_their_headings_and_words",
    ),
    one(
        "a passage is given with a search model's prefix",
        SEARCH,
        "        embedders.passage_input(embedders.PLAIN, hit.heading_path, hit.text), hit.tokens_est",
        '        "search_document: " + embedders.passage_input(embedders.PLAIN, hit.heading_path, hit.text), hit.tokens_est',
        FOUND + "test_a_passage_is_given_as_its_heading_path_a_new_line_and_its_words",
        FOUND + "test_a_passage_is_given_as_a_search_model_is_given_it_without_that_models_prefix",
    ),
    one(
        "a passage is worth nothing",
        SEARCH,
        "        embedders.passage_input(embedders.PLAIN, hit.heading_path, hit.text), hit.tokens_est",
        "        embedders.passage_input(embedders.PLAIN, hit.heading_path, hit.text), 0",
        FOUND + "test_what_a_passage_is_worth_is_its_own_count",
        FOUND + "test_a_passage_is_given_as_its_heading_path_a_new_line_and_its_words",
    ),
    one(
        "the union is the fused order's first ten",
        SEARCH,
        "    return [c for c in candidates if c.hit.chunk_id in wanted]",
        "    return list(candidates[:each])",
        FOUND + "test_the_best_ten_of_each_way_are_given_each_passage_once_and_up_to_twenty",
        FOUND + "test_the_most_a_sorter_is_given_is_twenty_when_the_ways_find_different_passages",
        FOUND + "test_the_sorter_is_given_the_union_through_the_retriever_and_the_question_asked",
    ),
    one(
        "each way gives all it found",
        SEARCH,
        "    wanted = {hit.chunk_id for hits in found.values() for hit in hits[:each]}",
        "    wanted = {hit.chunk_id for hits in found.values() for hit in hits}",
        FOUND + "test_the_best_ten_of_each_way_are_given_each_passage_once_and_up_to_twenty",
        FOUND + "test_with_one_way_running_the_sorter_is_given_its_best_ten",
    ),
    one(
        "a way gives only its best five",
        SEARCH,
        "    wanted = {hit.chunk_id for hits in found.values() for hit in hits[:each]}",
        "    wanted = {hit.chunk_id for hits in found.values() for hit in hits[: each // 2]}",
        FOUND + "test_the_best_ten_of_each_way_are_given_each_passage_once_and_up_to_twenty",
        FOUND + "test_with_one_way_running_the_sorter_is_given_its_best_ten",
    ),
    one(
        "only the words give their best",
        SEARCH,
        "    wanted = {hit.chunk_id for hits in found.values() for hit in hits[:each]}",
        '    wanted = {hit.chunk_id for name, hits in found.items() for hit in hits[:each] if name == "words"}',
        FOUND + "test_the_best_ten_of_each_way_are_given_each_passage_once_and_up_to_twenty",
        FOUND + "test_a_way_that_found_nothing_gives_nothing_and_the_other_gives_its_best_ten",
    ),
    one(
        "the passages are given in no fused order",
        SEARCH,
        "    return [c for c in candidates if c.hit.chunk_id in wanted]",
        "    return sorted((c for c in candidates if c.hit.chunk_id in wanted), key=lambda c: -c.hit.chunk_id)",
        FOUND + "test_the_passages_are_given_in_the_order_the_ways_were_fused_in",
    ),
    one(
        "the sorter is asked for the latest message alone",
        SEARCH,
        "        asked = (query.asked or question[:LATEST_CHARS]).strip()",
        "        asked = question[:LATEST_CHARS].strip()",
        FOUND + "test_a_follow_up_is_sorted_by_what_the_meaning_search_was_asked_too",
    ),
    one(
        "one passage is sent to the sorter",
        SEARCH,
        "        if len(pool) < 2 or not asked:",
        "        if not pool or not asked:",
        FOUND + "test_one_passage_has_no_order_to_be_put_in_and_the_sorter_is_not_asked",
    ),
    # --- the order that comes back, and what follows it (call 2) -------------------------------
    one(
        "the sorter's order is not applied",
        SEARCH,
        "        return sorted_by(candidates, pool, done.order), done.report",
        "        return candidates, done.report",
        FOUND + "test_the_sorters_order_is_the_order_the_passages_are_sent_in",
        FOUND + "test_each_passage_says_where_the_sorter_put_it_beside_where_each_way_did",
        API
        + "test_the_sorters_order_is_the_order_the_passages_are_sent_in_and_not_the_one_they_were_found_in",
    ),
    one(
        "the sorted come after the rest",
        SEARCH,
        "    return [*placed, *(c for c in candidates if c.hit.chunk_id not in sent)]",
        "    return [*(c for c in candidates if c.hit.chunk_id not in sent), *placed]",
        FOUND + "test_the_sorted_come_first_and_the_rest_follow_in_the_fused_order",
        FOUND + "test_the_sorters_order_is_the_order_the_passages_are_sent_in",
    ),
    one(
        "the rest are dropped",
        SEARCH,
        "    return [*placed, *(c for c in candidates if c.hit.chunk_id not in sent)]",
        "    return placed",
        FOUND + "test_the_sorted_come_first_and_the_rest_follow_in_the_fused_order",
        FOUND + "test_a_passage_the_sorter_was_not_given_has_no_place_by_it",
    ),
    one(
        "the rest are in the sorter's order too",
        SEARCH,
        "    return [*placed, *(c for c in candidates if c.hit.chunk_id not in sent)]",
        "    return [*placed, *sorted((c for c in candidates if c.hit.chunk_id not in sent), key=lambda c: -c.hit.chunk_id)]",
        FOUND + "test_the_sorted_come_first_and_the_rest_follow_in_the_fused_order",
    ),
    one(
        "the sorter's place is not kept",
        SEARCH,
        "        replace(pool[position], ranks=(*pool[position].ranks, (SORTER, place)))",
        "        replace(pool[position])",
        FOUND + "test_each_passage_says_where_the_sorter_put_it_beside_where_each_way_did",
        FOUND + "test_a_passage_the_sorter_was_not_given_has_no_place_by_it",
        API + "test_each_passage_says_where_the_sorter_put_it_beside_where_words_and_meaning_did",
    ),
    one(
        "the sorter's place counts from zero",
        SEARCH,
        "        for place, position in enumerate(order, start=1)",
        "        for place, position in enumerate(order)",
        FOUND + "test_each_passage_says_where_the_sorter_put_it_beside_where_each_way_did",
        API + "test_each_passage_says_where_the_sorter_put_it_beside_where_words_and_meaning_did",
    ),
    one(
        "the sorter changes how a passage was found",
        SEARCH,
        "        replace(pool[position], ranks=(*pool[position].ranks, (SORTER, place)))",
        "        replace(pool[position], via=(SORTER,), ranks=(*pool[position].ranks, (SORTER, place)))",
        FOUND + "test_the_ways_that_found_a_passage_are_not_the_sorters_to_change",
    ),
    one(
        "the choice after the sort ignores the sorted order",
        SEARCH,
        "        named = {c.hit.doc_id for c in candidates if names_document(c.hit.name, question)}\n        chosen = select(\n            candidates,",
        "        named = {c.hit.doc_id for c in candidates if names_document(c.hit.name, question)}\n        chosen = select(\n            sorted(candidates, key=lambda c: c.hit.chunk_id),",
        FOUND + "test_the_sorters_order_is_the_order_the_passages_are_sent_in",
        FOUND
        + "test_the_choice_after_the_sort_is_as_before_at_most_three_from_one_file_unless_named",
    ),
    # --- what it says of itself (call 6) -----------------------------------------------------
    one(
        "the mode does not say the sorter",
        SEARCH,
        '        return f"{mode}, sorted by {sort.model}"',
        "        return mode",
        FOUND + "test_when_the_passages_were_sorted_the_mode_says_by_which_sorter",
        FOUND + "test_each_mode_is_told_with_the_sorter_after_it",
        API
        + "test_the_best_passages_are_sent_to_the_first_local_running_sorter_as_their_headings_and_words",
    ),
    one(
        "the mode says a sorter that did not sort",
        SEARCH,
        "    if sort is not None and sort.sorted and sort.model:",
        "    if sort is not None and sort.model:",
        FOUND + "test_when_they_were_not_the_mode_is_as_it_was_and_the_report_says_why",
        FOUND + "test_the_mode_of_a_search_that_was_not_sorted_is_its_own",
    ),
    one(
        "the report is not kept with the result",
        SEARCH,
        "            reports=reports,\n            sort=sort,\n        )",
        "            reports=reports,\n        )",
        FOUND + "test_when_the_passages_were_sorted_the_mode_says_by_which_sorter",
        FOUND + "test_when_they_were_not_the_mode_is_as_it_was_and_the_report_says_why",
    ),
    one(
        "the sorter's report is not among the reports",
        SEARCH,
        "            reports[SORTER] = Report(\n                SORTER,\n                sort.sorted,\n                hits=sort.passages if sort.sorted else 0,\n                code=sort.code,\n                message=sort.message,\n                quiet=sort.quiet,\n            )\n",
        "",
        FOUND + "test_the_sorters_report_is_among_the_reports_beside_each_ways_and_says_the_same",
        FOUND
        + "test_a_sorter_that_raises_says_so_among_the_reports_and_one_that_is_not_there_says_nothing",
    ),
    one(
        "the sorter's report says it sorted when it did not",
        SEARCH,
        "                sort.sorted,\n                hits=sort.passages if sort.sorted else 0,",
        "                True,\n                hits=sort.passages if sort.sorted else 0,",
        FOUND + "test_the_sorters_report_is_among_the_reports_beside_each_ways_and_says_the_same",
    ),
    one(
        "the sorter's report loses why it did not sort",
        SEARCH,
        "                code=sort.code,\n                message=sort.message,\n                quiet=sort.quiet,\n            )\n",
        "                quiet=sort.quiet,\n            )\n",
        FOUND + "test_the_sorters_report_is_among_the_reports_beside_each_ways_and_says_the_same",
        FOUND
        + "test_a_sorter_that_raises_says_so_among_the_reports_and_one_that_is_not_there_says_nothing",
    ),
    # --- a sorter that fails never fails a search (call 5) ---------------------------------------
    one(
        "a sorter that raises fails the search",
        SEARCH,
        "        except Exception as exc:  # a sorter's failure is never a search's",
        "        except ZeroDivisionError as exc:  # a sorter's failure is never a search's",
        FOUND + "test_a_sorter_that_raises_is_a_report_and_the_search_is_whole",
    ),
    one(
        "a search that is ended is taken for a sorter that failed",
        SEARCH,
        "        except Exception as exc:  # a sorter's failure is never a search's",
        "        except BaseException as exc:  # a sorter's failure is never a search's",
        FOUND + "test_a_search_that_is_ended_is_not_made_to_look_like_a_sorter_that_failed",
    ),
    one(
        "an order that is not every passage once is believed",
        SEARCH,
        "        if sorted(done.order) != list(range(len(pool))):",
        "        if False:",
        FOUND + "test_an_order_that_is_not_every_passage_once_is_not_believed",
    ),
    one(
        "a sorter that did not sort has its order used",
        SEARCH,
        "        if done.order is None:\n            return candidates, done.report",
        "        if done.order is None:\n            return candidates, SortReport()",
        FOUND + "test_a_sorter_that_says_it_did_not_sort_leaves_the_order_and_its_words_are_kept",
        FOUND + "test_when_they_were_not_the_mode_is_as_it_was_and_the_report_says_why",
    ),
    one(
        "a failure of the gateway raises",
        SORTING,
        '        except (TimeoutError, HubError) as exc:\n            code, words = problem(self._error_of(exc, "the sort took too long"), model.id, budget)',
        '        except TimeoutError as exc:\n            code, words = problem(self._error_of(exc, "the sort took too long"), model.id, budget)',
        UNIT
        + "test_a_sorter_that_fails_is_a_report_in_its_own_words_and_the_passages_are_left_as_found",
        UNIT + "test_a_sorter_whose_answer_is_not_a_score_for_each_passage_is_not_believed",
        API + "test_a_sorter_that_fails_leaves_the_search_whole_and_says_what_it_said",
    ),
    one(
        "a probe that fails raises",
        SORTING,
        '        except (TimeoutError, HubError) as exc:\n            code, words = problem(self._error_of(exc, "the probe took too long"), model.id, budget)',
        '        except TimeoutError as exc:\n            code, words = problem(self._error_of(exc, "the probe took too long"), model.id, budget)',
        UNIT + "test_a_sorter_that_is_stopped_when_it_is_first_asked_is_said_and_asked_again_soon",
        UNIT + "test_the_standing_of_a_sorter_that_cannot_be_measured_names_what_was_seen",
    ),
    one(
        "a sorter that is stopped is not said to be",
        SORTING,
        '    if exc.status in (503, 404):\n        return "not_running", (',
        '    if exc.status in ():\n        return "not_running", (',
        UNIT
        + "test_a_sorter_that_fails_is_a_report_in_its_own_words_and_the_passages_are_left_as_found",
        API + "test_a_sorter_that_fails_leaves_the_search_whole_and_says_what_it_said",
    ),
    one(
        "a refusal does not say what the sorter said",
        SORTING,
        '            f"{NOT_SORTED}the sorter {model} refused the request ({engine_words(exc)}), {LEFT}."',
        '            f"{NOT_SORTED}the sorter {model} refused the request, {LEFT}."',
        UNIT
        + "test_a_sorter_that_fails_is_a_report_in_its_own_words_and_the_passages_are_left_as_found",
        API + "test_a_sorter_that_fails_leaves_the_search_whole_and_says_what_it_said",
    ),
    one(
        "a malformed answer is believed",
        HUB,
        "    if not isinstance(results, list) or len(results) != count:",
        "    if not isinstance(results, list):",
        HUB_T + "test_an_answer_that_is_not_a_score_for_each_passage_is_said_as_what_it_is",
        API + "test_a_sorter_that_fails_leaves_the_search_whole_and_says_what_it_said",
    ),
    one(
        "a score that is not a number is believed",
        HUB,
        "            or not isinstance(score, int | float)\n            or isinstance(score, bool)\n            or not math.isfinite(score)",
        "            or isinstance(score, bool)",
        HUB_T + "test_an_answer_that_is_not_a_score_for_each_passage_is_said_as_what_it_is",
        HUB_T + "test_a_score_that_is_not_a_finite_number_is_not_believed",
    ),
    one(
        "a repeated passage in the answer is believed",
        HUB,
        "            or index in seen\n            or not isinstance(score, int | float)\n",
        "            or not isinstance(score, int | float)\n",
        HUB_T + "test_an_answer_that_is_not_a_score_for_each_passage_is_said_as_what_it_is",
    ),
    one(
        "a score goes with the place it was listed at",
        HUB,
        "        scores[index] = float(score)",
        "        scores[len(seen) - 1] = float(score)",
        HUB_T
        + "test_each_score_goes_with_the_passage_it_is_for_whatever_order_the_answer_lists_them_in",
    ),
    one(
        "a tie is not kept in the order sent",
        HUB,
        "        return sorted(range(len(self.scores)), key=lambda i: (-self.scores[i], i))",
        "        return sorted(range(len(self.scores)), key=lambda i: (-self.scores[i], -i))",
        HUB_T + "test_the_order_is_best_first_and_a_tie_keeps_the_order_the_passages_were_sent_in",
        UNIT + "test_the_order_is_the_sorters_best_first_and_a_tie_keeps_the_order_sent",
    ),
    one(
        "the order is worst first",
        HUB,
        "        return sorted(range(len(self.scores)), key=lambda i: (-self.scores[i], i))",
        "        return sorted(range(len(self.scores)), key=lambda i: (self.scores[i], i))",
        HUB_T + "test_the_order_is_best_first_and_a_tie_keeps_the_order_the_passages_were_sent_in",
        API
        + "test_the_sorters_order_is_the_order_the_passages_are_sent_in_and_not_the_one_they_were_found_in",
    ),
    one(
        "the request asks for the best three only",
        HUB,
        '        body = {"model": model, "query": query, "documents": list(documents)}',
        '        body = {"model": model, "query": query, "documents": list(documents), "top_n": 3}',
        HUB_T
        + "test_a_request_is_jinas_body_with_the_model_the_question_and_the_passages_and_nothing_else",
        API
        + "test_the_best_passages_are_sent_to_the_first_local_running_sorter_as_their_headings_and_words",
    ),
    one(
        "the request is not given the seconds asked for",
        HUB,
        "                timeout=httpx.Timeout(min(10.0, seconds), read=seconds),",
        "                timeout=httpx.Timeout(10.0, read=120.0),",
        HUB_T + "test_a_request_is_given_the_seconds_asked_for_and_a_slow_answer_is_said_as_slow",
    ),
    one(
        "a sort is given the budget and not twice the budget",
        SORTING,
        "        limit = 2 * budget\n        started = self._clock()\n        self.asked.append(len(sheets))",
        "        limit = budget\n        started = self._clock()\n        self.asked.append(len(sheets))",
        UNIT
        + "test_a_sorter_that_takes_over_twice_the_budget_is_given_up_on_and_that_is_remembered",
        API
        + "test_a_sorter_that_takes_over_twice_the_budget_is_given_up_on_and_the_search_is_whole",
    ),
    one(
        "a sort that is given up on is not remembered as slow",
        SORTING,
        '            if code == "slow":',
        "            if False:",
        UNIT
        + "test_a_sorter_that_takes_over_twice_the_budget_is_given_up_on_and_that_is_remembered",
    ),
    # --- which sorter (call 3) ---------------------------------------------------------------
    one(
        "auto picks a sorter outside the machines",
        SORTING,
        "        local = [m for m in models if m.local]\n        ready = [m for m in local if m.ready]",
        "        local = list(models)\n        ready = [m for m in local if m.ready]",
        UNIT + "test_auto_never_picks_a_sorter_outside_the_machines_even_when_the_owner_allows_it",
        UNIT + "test_a_sorter_whose_place_eugene_cannot_say_is_not_one_that_stays_here",
        API + "test_auto_never_picks_a_sorter_outside_the_machines_even_when_it_is_the_only_one",
        API + "test_auto_leaves_a_stopped_sorter_alone_and_a_local_one_beside_it_is_the_one_used",
    ),
    one(
        "auto takes a sorter that is not running",
        SORTING,
        "        local = [m for m in models if m.local]\n        ready = [m for m in local if m.ready]",
        "        local = [m for m in models if m.local]\n        ready = local",
        UNIT + "test_auto_is_the_first_sorter_that_runs_on_the_machines_and_is_running",
        UNIT + "test_auto_never_picks_a_sorter_that_is_stopped_and_says_which",
        API + "test_auto_leaves_a_stopped_sorter_alone_and_a_local_one_beside_it_is_the_one_used",
    ),
    one(
        "auto takes the last sorter and not the first",
        SORTING,
        '            return Choice(ready[0], "ok", AUTO, outside=outside)',
        '            return Choice(ready[-1], "ok", AUTO, outside=outside)',
        UNIT + "test_auto_is_the_first_sorter_that_runs_on_the_machines_and_is_running",
        API
        + "test_the_best_passages_are_sent_to_the_first_local_running_sorter_as_their_headings_and_words",
    ),
    one(
        "a sorter outside the machines is used without the owner's say",
        SORTING,
        "    if not found.local and not allow_external:",
        "    if False and not found.local and not allow_external:",
        UNIT + "test_a_named_sorter_outside_the_machines_is_used_only_when_the_owner_allows_it",
        UNIT + "test_a_named_sorter_outside_the_machines_is_sent_passages_only_when_it_is_allowed",
        API
        + "test_a_named_sorter_outside_the_machines_is_sent_passages_only_when_the_owner_allows_it",
    ),
    one(
        "a named sorter that is stopped is used",
        SORTING,
        "    if not found.ready:",
        "    if False:",
        UNIT + "test_a_named_sorter_is_never_swapped_for_another_when_it_is_stopped",
    ),
    one(
        "a name that is not a sorter is taken for one",
        SORTING,
        "    found = next((m for m in models if m.id == setting), None)\n    if found is None:\n        if setting in listed:",
        "    found = next((m for m in models if m.id == setting), None)\n    if found is None:\n        if False:",
        UNIT + "test_a_name_that_is_not_a_sorter_is_told_from_one_that_is_not_there",
        API + "test_a_name_that_is_not_a_sorter_is_told_from_one_that_is_not_there",
    ),
    one(
        "off is not off",
        SORTING,
        "    if setting.casefold() == OFF:\n        return Choice(",
        "    if False:\n        return Choice(",
        UNIT + "test_off_is_off_whatever_is_running_and_is_quiet",
        API + "test_with_the_sorter_off_the_search_is_as_it_was_and_the_gateway_is_not_asked",
    ),
    one(
        "off asks the gateway",
        SORTING,
        "        if setting.casefold() == OFF:\n            return Resolved(choose([], (), setting, False))",
        "        if False:\n            return Resolved(choose([], (), setting, False))",
        UNIT + "test_a_sorter_that_is_off_asks_the_gateway_nothing_and_is_quiet",
    ),
    one(
        "a sorter that was never set up is said loudly",
        SORTING,
        'QUIET_CODES = ("off", "none_set_up")',
        "QUIET_CODES = ()",
        UNIT + "test_with_no_sorter_at_all_auto_is_quiet",
        UNIT + "test_with_no_sorter_listed_nothing_is_asked_of_one_and_it_is_quiet",
        API + "test_the_header_with_no_sorter_says_so_quietly_and_the_mode_is_as_it_was",
    ),
    # --- only when quick enough (call 4) ---------------------------------------------------------
    one(
        "the budget is ignored",
        SORTING,
        "        report = replace(report, rate=rate, expected=expected)\n        if expected > budget:",
        "        report = replace(report, rate=rate, expected=expected)\n        if False:",
        UNIT
        + "test_a_sort_that_would_take_longer_than_the_budget_is_not_made_and_says_why_with_the_numbers",
        UNIT + "test_the_same_sort_is_made_when_the_budget_allows_it_and_not_when_it_does_not",
        API
        + "test_a_sorter_too_slow_for_the_time_allowed_is_not_asked_and_the_page_is_told_why_with_the_numbers",
        API + "test_a_raised_budget_sorts_the_same_question",
    ),
    one(
        "a sort that takes exactly the budget is refused",
        SORTING,
        "        report = replace(report, rate=rate, expected=expected)\n        if expected > budget:",
        "        report = replace(report, rate=rate, expected=expected)\n        if expected >= budget:",
        UNIT + "test_a_sort_that_takes_exactly_the_budget_is_made",
    ),
    one(
        "the time is not tokens over the speed",
        SORTING,
        "        expected = tokens / rate\n        report = replace(report, rate=rate, expected=expected)",
        "        expected = tokens * rate\n        report = replace(report, rate=rate, expected=expected)",
        UNIT + "test_what_the_probe_measured_prices_the_first_sort_by_tokens_over_that_rate",
        UNIT
        + "test_a_sort_that_would_take_longer_than_the_budget_is_not_made_and_says_why_with_the_numbers",
    ),
    one(
        "the question is counted once and not for each passage",
        SORTING,
        "    return sum(s.tokens for s in sheets) + token_count.estimate(question) * len(sheets)",
        "    return sum(s.tokens for s in sheets) + token_count.estimate(question)",
        UNIT
        + "test_the_tokens_of_a_sort_are_the_passages_own_counts_and_the_question_once_for_each",
        UNIT + "test_what_the_probe_measured_prices_the_first_sort_by_tokens_over_that_rate",
    ),
    one(
        "the question is not counted",
        SORTING,
        "    return sum(s.tokens for s in sheets) + token_count.estimate(question) * len(sheets)",
        "    return sum(s.tokens for s in sheets)",
        UNIT
        + "test_the_tokens_of_a_sort_are_the_passages_own_counts_and_the_question_once_for_each",
    ),
    one(
        "the over-budget words leave out the tokens",
        SORTING,
        '        f"({tokens:,} tokens) would take about {seconds_words(expected)}, and Sort time is "',
        '        f"would take about {seconds_words(expected)}, and Sort time is "',
        UNIT
        + "test_a_sort_that_would_take_longer_than_the_budget_is_not_made_and_says_why_with_the_numbers",
        API
        + "test_a_sorter_too_slow_for_the_time_allowed_is_not_asked_and_the_page_is_told_why_with_the_numbers",
    ),
    one(
        "the over-budget words leave out where to raise it",
        SORTING,
        "RAISE_IT = \"Raise Sort time in Workbench's settings (in Eugene's console) to sort anyway.\"",
        'RAISE_IT = ""',
        UNIT
        + "test_a_sort_that_would_take_longer_than_the_budget_is_not_made_and_says_why_with_the_numbers",
        API
        + "test_a_sorter_too_slow_for_the_time_allowed_is_not_asked_and_the_page_is_told_why_with_the_numbers",
    ),
    one(
        "the probe is one passage",
        SORTING,
        "        sheets = probe_sheets()\n        limit = 2 * budget",
        "        sheets = probe_sheets()[:1]\n        limit = 2 * budget",
        UNIT
        + "test_a_sorter_that_has_never_read_is_measured_with_two_passages_when_it_is_first_asked",
    ),
    one(
        "the probe is made for every sort",
        SORTING,
        "        rate = self.rate_of(model.id)\n        if rate is not None:\n            self._refresh(model, budget)",
        "        rate = self.rate_of(model.id)\n        if False and rate is not None:\n            self._refresh(model, budget)",
        UNIT + "test_the_probe_is_not_made_again_while_the_speed_is_known_and_recent",
    ),
    one(
        "two questions at once each measure the sorter",
        SORTING,
        "        running = self._measuring.get(model.id)\n        if running is not None and not running.done():\n            return running",
        "        running = None",
        UNIT + "test_two_questions_asked_at_once_of_a_sorter_not_yet_measured_measure_it_once",
    ),
    one(
        "a sorter that was never measured is sorted with, unpriced",
        SORTING,
        "        await asyncio.wait({self._measure_soon(model, budget)})\n        rate = self.rate_of(model.id)",
        "        self._measure_soon(model, budget)\n        rate = self.rate_of(model.id)",
        UNIT
        + "test_a_sorter_that_has_never_read_is_measured_with_two_passages_when_it_is_first_asked",
        API
        + "test_a_sorter_too_slow_for_the_time_allowed_is_not_asked_and_the_page_is_told_why_with_the_numbers",
    ),
    one(
        "the speed ignores the sorts after the probe",
        SORTING,
        "        self._speeds.setdefault(model, Speed()).record(tokens, seconds, self._clock())",
        "        pass",
        UNIT + "test_the_speed_is_a_decaying_average_of_the_probe_and_the_sorts_after_it",
        UNIT + "test_a_sorter_that_turns_slow_is_noticed_and_not_asked_while_it_is_too_slow",
    ),
    one(
        "the speed does not fade",
        SORTING,
        "        self._tokens = self._tokens * self._decay + tokens",
        "        self._tokens = self._tokens + tokens",
        UNIT + "test_a_speed_is_tokens_over_seconds_with_the_older_measurements_counting_less",
        UNIT + "test_what_was_true_long_ago_fades_to_nothing",
    ),
    one(
        "the speed is an average of speeds",
        SORTING,
        "        return self._tokens / self._seconds",
        "        return self._tokens / max(self._seconds, MIN_SECONDS) / 2",
        UNIT + "test_a_speed_is_tokens_over_seconds_with_the_older_measurements_counting_less",
    ),
    one(
        "a speed that is old is never measured again",
        SORTING,
        "            speed is not None and speed.at is not None and self._clock() - speed.at > RATE_MAX_AGE",
        "            False",
        UNIT + "test_a_speed_that_is_old_is_used_as_it_stands_and_measured_again_behind_the_search",
        UNIT + "test_a_sorter_that_was_slow_for_a_while_is_not_left_off_for_ever",
    ),
    one(
        "a measurement made again does not replace the old",
        SORTING,
        "        self._speeds[model.id] = speed\n        self._failed.pop(model.id, None)",
        "        self._speeds.setdefault(model.id, speed)\n        self._failed.pop(model.id, None)",
        UNIT + "test_a_speed_that_is_old_is_used_as_it_stands_and_measured_again_behind_the_search",
    ),
    one(
        "a sorter that could not be measured is asked at once again",
        SORTING,
        "        if failed is not None and self._clock() - failed.at < MEASURE_RETRY:\n            return None, failed",
        "        if False:\n            return None, failed",
        UNIT + "test_a_sorter_that_is_stopped_when_it_is_first_asked_is_said_and_asked_again_soon",
    ),
    # --- what the screens say ----------------------------------------------------------------
    one(
        "a screen waits for the sorter to be measured",
        SORTING,
        '            self._measure_soon(model, budget)\n            return stand("measuring", None)',
        '            await asyncio.wait({self._measure_soon(model, budget)})\n            return stand("measuring", None)',
        UNIT + "test_the_standing_does_not_wait_for_a_measurement_and_says_so_until_it_is_made",
    ),
    one(
        "the header says a usual sort is quick enough when it is not",
        SORTING,
        "        expected = typical_tokens() / rate\n        if expected > budget:",
        "        expected = typical_tokens() / rate\n        if False:",
        UNIT + "test_a_sorter_too_slow_for_a_sort_of_the_usual_size_is_said_with_the_numbers",
        API + "test_the_screen_says_the_same_thing_before_any_question_is_asked",
        API + "test_the_budget_setting_says_when_the_sorter_is_too_slow_for_it",
    ),
    one(
        "a usual sort is of a different size from the one said",
        SORTING,
        "    return TYPICAL_PASSAGES * (TYPICAL_PASSAGE_TOKENS + TYPICAL_QUESTION_TOKENS)",
        "    return TYPICAL_PASSAGES * TYPICAL_PASSAGE_TOKENS",
        UNIT + "test_a_sorter_too_slow_for_a_sort_of_the_usual_size_is_said_with_the_numbers",
        API + "test_the_screen_says_the_same_thing_before_any_question_is_asked",
    ),
    one(
        "the header does not say the sorter in the mode",
        SAVED_API,
        '    if sort is not None and sort.sorting:\n        facts["mode"]',
        '    if False and sort is not None and sort.sorting:\n        facts["mode"]',
        API + "test_the_screen_says_the_same_thing_before_any_question_is_asked",
    ),
    one(
        "the header carries nothing of the sorter",
        SAVED_API,
        '    facts["sort"] = sort.view() if sort is not None else None',
        '    facts["sort"] = None',
        API + "test_the_header_names_every_field_the_documents_screen_reads_of_the_sorter",
        API + "test_the_screen_says_the_same_thing_before_any_question_is_asked",
    ),
    one(
        "a tried question says the sorter did not sort",
        SAVED_API,
        '        "used": report.sorted,',
        '        "used": False,',
        API
        + "test_the_best_passages_are_sent_to_the_first_local_running_sorter_as_their_headings_and_words",
        API + "test_a_raised_budget_sorts_the_same_question",
    ),
    one(
        "a tried question says nothing of the sorter",
        SAVED_API,
        '    out["sort"] = sort_block(result, saved.sorting is not None)',
        "    pass",
        API
        + "test_the_best_passages_are_sent_to_the_first_local_running_sorter_as_their_headings_and_words",
        API + "test_a_sorter_that_fails_leaves_the_search_whole_and_says_what_it_said",
    ),
    one(
        "an answer's record says nothing of the sorter",
        SAVED_API,
        '        "sort": sort,\n    }',
        '        "sort": None,\n    }',
        API + "test_a_chat_is_sent_the_sorted_passages_and_its_search_record_says_so",
        API
        + "test_a_chat_whose_sorter_is_too_slow_is_sent_the_passages_as_found_and_says_why_in_its_record",
    ),
    one(
        "an answer does not keep what sorting came to",
        SAVED_API,
        '        known["sort"] = sort_block(result, saved.sorting is not None)',
        "        pass",
        API + "test_a_chat_is_sent_the_sorted_passages_and_its_search_record_says_so",
    ),
    one(
        "a chat is not sent through the sorter",
        SAVED,
        "            retriever = retriever or Retriever(\n                [WordSignal(), self.meaning.signal], sorter=self.sorting\n            )",
        "            retriever = retriever or Retriever([WordSignal(), self.meaning.signal])",
        API
        + "test_the_best_passages_are_sent_to_the_first_local_running_sorter_as_their_headings_and_words",
        API + "test_a_chat_is_sent_the_sorted_passages_and_its_search_record_says_so",
    ),
    # --- the settings -----------------------------------------------------------------------
    one(
        "the budget takes any number",
        CONFIG,
        "            and MIN_SORT_SECONDS <= value <= MAX_SORT_SECONDS\n        ):\n            return None",
        "            and True\n        ):\n            return None",
        API + "test_the_settings_take_what_they_can_and_refuse_what_they_cannot",
    ),
    one(
        "the sorter setting takes anything",
        CONFIG,
        "    if key == DOCUMENTS_SORT_MODEL:\n        if isinstance(value, str) and value.strip() and len(value.strip()) <= MAX_MODEL_ID:",
        "    if key == DOCUMENTS_SORT_MODEL:\n        if True:",
        API + "test_the_settings_take_what_they_can_and_refuse_what_they_cannot",
    ),
    one(
        "the default budget is not three seconds",
        CONFIG,
        "    DOCUMENTS_SORT_SECONDS: 3.0,",
        "    DOCUMENTS_SORT_SECONDS: 30.0,",
        CHATS,
        API + "test_the_settings_take_what_they_can_and_refuse_what_they_cannot",
    ),
    one(
        "a budget saved wrongly stops a search",
        CONFIG,
        "        isinstance(value, int | float)\n        and not isinstance(value, bool)\n        and MIN_SORT_SECONDS <= value <= MAX_SORT_SECONDS\n    ):\n        return float(value)",
        "        True\n    ):\n        return float(value)",
        API + "test_a_setting_that_was_saved_wrongly_is_the_default_and_never_stops_a_search",
    ),
    one(
        "the settings do not show what they come to",
        CONFIG,
        '                f.model_copy(update={"suggestions": [AUTO, OFF, *view.ids], "status": view.model})',
        "                f",
        API
        + "test_the_two_settings_are_in_the_config_with_their_defaults_and_say_what_they_come_to",
    ),
    one(
        "the budget setting does not say what it does",
        CONFIG,
        '                else f.model_copy(update={"status": view.seconds})',
        "                else f",
        API
        + "test_the_two_settings_are_in_the_config_with_their_defaults_and_say_what_they_come_to",
        API + "test_the_budget_setting_says_when_the_sorter_is_too_slow_for_it",
    ),
    one(
        "the settings are not in the config",
        CONFIG,
        "            DOCUMENTS_SORT_SECONDS: documents.sort_seconds,\n",
        "",
        CHATS,
        API + "test_the_settings_take_what_they_can_and_refuse_what_they_cannot",
    ),
]

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
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return False, "the checks did not finish in 300 seconds"
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
    checks = sorted({check for _, _, cs in cases for check in cs})
    passed, output = run_all(checks)
    if not passed:
        print(output[-3000:])
        raise SystemExit("Baseline failed; no files changed.")
    print(f"Baseline: {len(checks)} checks pass.", flush=True)
    backup = Path(tempfile.mkdtemp(prefix="workbench-sorting-sabotage-"))
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
