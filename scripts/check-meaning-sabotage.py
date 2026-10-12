"""Prove the search-by-meaning checks are observed (MR5).

Each case breaks one thing in the search model's choice, the prefixes, the vectors and
the scan, the index's record of what made them, the fusion of words and meaning, the
worker's limits, backing off and ending of a request, what a person is told, or the
settings, and its named checks must fail. Files are restored from exact bytes kept beside
the run, never from Git. A mutation that stops the code importing or compiling does not
count as a caught defect.

  python scripts/check-meaning-sabotage.py            # every case
  python scripts/check-meaning-sabotage.py --anchors  # only check each anchor is unique
  python scripts/check-meaning-sabotage.py --labels floor,epoch   # cases whose label has these words

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
EMBEDDERS = SRC + "knowledge/embedders.py"
VECTORS = SRC + "knowledge/vectors.py"
INDEX = SRC + "knowledge/index.py"
SEARCH = SRC + "knowledge/search.py"
MEANING = SRC + "knowledge/meaning.py"
SAVED = SRC + "knowledge/saved.py"
INGEST = SRC + "knowledge/ingest.py"
SAVED_API = SRC + "saved_api.py"
HUB = SRC + "hub.py"
CONFIG = SRC + "config.py"

EMBED_T = "tests/test_meaning_embedders.py::"
VECTOR_T = "tests/test_meaning_vectors.py::"
INDEX_T = "tests/test_meaning_index.py::"
SEARCH_T = "tests/test_meaning_search.py::"
HUB_T = "tests/test_hub_embeddings.py::"
WORKER_T = "tests/test_meaning_worker.py::"
API_T = "tests/test_meaning_api.py::"
INGEST_T = "tests/test_knowledge_ingest.py::"

#: (label, [(file, before, after), ...], [check, ...])
Case = tuple[str, list[tuple[str, str, str]], list[str]]


def one(label: str, name: str, before: str, after: str, *checks: str) -> Case:
    return (label, [(name, before, after)], list(checks))


# (label, edits, checks)
CASES: list[Case] = [
    # --- which search model, and what it is given --------------------------------------
    one(
        "qwen3's instruction is not the one in the design",
        EMBEDDERS,
        'QWEN3_TASK = "Given a question, retrieve passages from the person\'s documents that answer it"',
        'QWEN3_TASK = "Find passages that answer the question"',
        EMBED_T + "test_qwen3_embedding_is_asked_with_its_instruction_and_a_passage_has_none",
        API_T
        + "test_a_search_model_the_table_knows_is_given_its_prefixes_and_one_it_does_not_is_given_none",
    ),
    one(
        "a qwen3 passage is given a prefix",
        EMBEDDERS,
        '        f"Instruct: {QWEN3_TASK}\\nQuery:",\n        "",\n        32_768,',
        '        f"Instruct: {QWEN3_TASK}\\nQuery:",\n        "passage: ",\n        32_768,',
        EMBED_T + "test_qwen3_embedding_is_asked_with_its_instruction_and_a_passage_has_none",
        API_T
        + "test_a_search_model_the_table_knows_is_given_its_prefixes_and_one_it_does_not_is_given_none",
    ),
    one(
        "nomic's passage prefix is the question's",
        EMBEDDERS,
        '        "search_query: ",\n        "search_document: ",\n        2_048,',
        '        "search_query: ",\n        "search_query: ",\n        2_048,',
        EMBED_T + "test_nomic_has_a_prefix_for_a_question_and_another_for_a_passage",
    ),
    one(
        "the headings are not embedded with a passage",
        EMBEDDERS,
        '    head = f"{heading}\\n" if heading else ""',
        '    head = ""',
        EMBED_T + "test_qwen3_embedding_is_asked_with_its_instruction_and_a_passage_has_none",
        WORKER_T
        + "test_a_turn_embeds_sixteen_passages_and_stores_unit_vectors_with_what_the_search_was_built_with",
    ),
    one(
        "a long question is not cut",
        EMBEDDERS,
        '    return f"{identity.query_prefix}{question[:MAX_QUESTION_CHARS]}"',
        '    return f"{identity.query_prefix}{question}"',
        EMBED_T + "test_a_question_is_cut_at_fourteen_hundred_characters",
    ),
    one(
        "every model is known to the first family",
        EMBEDDERS,
        "    return next((k for k in KNOWN if k.pattern.search(model_id)), None)",
        "    return KNOWN[0]",
        EMBED_T + "test_an_unknown_model_gets_no_prefixes_and_that_is_said",
        API_T + "test_an_unknown_search_model_is_used_without_prefixes_and_the_screen_says_so",
    ),
    one(
        "auto picks a model outside the machines",
        EMBEDDERS,
        "        local = [m for m in models if m.local]\n        ready = [m for m in local if m.ready]",
        "        local = list(models)\n        ready = [m for m in local if m.ready]",
        EMBED_T
        + "test_auto_never_picks_a_model_outside_the_machines_even_when_the_owner_allows_it",
        EMBED_T + "test_auto_is_the_first_search_model_that_runs_here_and_is_ready",
        WORKER_T
        + "test_text_is_never_sent_to_a_model_outside_the_machines_unless_it_is_named_and_allowed",
    ),
    one(
        "auto takes a model that is not running",
        EMBEDDERS,
        "        local = [m for m in models if m.local]\n        ready = [m for m in local if m.ready]",
        "        local = [m for m in models if m.local]\n        ready = local",
        EMBED_T + "test_auto_is_the_first_search_model_that_runs_here_and_is_ready",
        EMBED_T + "test_auto_says_which_search_model_is_not_running_when_one_exists_and_none_is",
    ),
    one(
        "auto forgets which model a search was built with",
        EMBEDDERS,
        "            kept = next((m for m in ready if built_with and m.id == built_with), None)",
        "            kept = None",
        EMBED_T + "test_auto_keeps_the_model_a_search_was_built_with_while_it_runs",
    ),
    one(
        "a model outside the machines is used without the owner's say",
        EMBEDDERS,
        "    if not found.local and not allow_external:",
        "    if False and not found.local and not allow_external:",
        EMBED_T
        + "test_a_model_outside_the_machines_is_used_only_when_it_is_named_and_the_owner_allows_it",
        API_T
        + "test_no_text_goes_to_a_search_model_outside_the_machines_unless_it_is_named_and_allowed",
    ),
    one(
        "the floor is forgotten",
        EMBEDDERS,
        "    return known.floor if known is not None else None",
        "    return None",
        EMBED_T + "test_only_qwen3_has_a_floor_because_only_it_was_measured",
        API_T
        + "test_below_its_floor_the_best_meaning_is_not_an_answer_and_with_a_word_it_always_counts",
    ),
    # --- the vectors and the scan ---------------------------------------------------
    one(
        "a vector with a number that is not a number is stored",
        VECTORS,
        "    if not np.isfinite(matrix).all():",
        "    if False:",
        VECTOR_T + "test_numbers_that_cannot_be_searched_are_refused_and_named",
        WORKER_T + "test_numbers_that_are_not_numbers_are_never_stored_not_even_those_beside_them",
    ),
    one(
        "a vector of zeros is stored",
        VECTORS,
        "    if (norms < 1e-12).any():",
        "    if False:",
        VECTOR_T + "test_numbers_that_cannot_be_searched_are_refused_and_named",
    ),
    one(
        "a vector is stored big-endian",
        VECTORS,
        'FLOAT = "<f4"',
        'FLOAT = ">f4"',
        VECTOR_T + "test_a_vector_is_stored_as_little_endian_float32_and_comes_back_whole",
    ),
    one(
        "a stored vector of the wrong size is read",
        VECTORS,
        "    if len(blob) != dim * BYTES_PER_NUMBER:",
        "    if False:",
        VECTOR_T + "test_a_stored_vector_of_another_size_is_refused_by_name",
        INDEX_T
        + "test_a_stored_vector_that_is_not_the_size_of_the_search_is_named_and_not_searched",
    ),
    one(
        "the scan takes the farthest passages",
        VECTORS,
        "    chosen = np.argpartition(-scores, k - 1)[:k]",
        "    chosen = np.argpartition(scores, k - 1)[:k]",
        VECTOR_T + "test_the_nearest_passages_are_the_largest_dot_products_best_first",
        API_T
        + "test_a_question_with_none_of_a_passages_words_finds_it_by_meaning_where_words_find_nothing",
    ),
    one(
        "the scan ignores the scope",
        VECTORS,
        "    if len(wanted) != len(matrix.scopes):",
        "    if False:",
        VECTOR_T + "test_the_scope_is_part_of_what_ranks_so_another_scope_never_takes_a_place",
    ),
    one(
        "a stale matrix is kept",
        VECTORS,
        "        if found.writes != writes:",
        "        if False:",
        VECTOR_T + "test_a_matrix_is_kept_while_the_index_has_not_been_written_to_and_let_go_after",
        API_T + "test_a_document_added_after_a_question_is_found_by_meaning_in_the_next_one",
    ),
    one(
        "a matrix nobody uses is never let go",
        VECTORS,
        "        stale = [s for s, m in self._by_person.items() if now - m.last_used >= self.idle_seconds]",
        "        stale = [s for s, m in self._by_person.items() if False]",
        VECTOR_T + "test_a_matrix_nobody_uses_is_let_go_after_ten_minutes",
    ),
    # --- what the index records of what made its vectors ---------------------------------
    one(
        "vectors of another search model are stored",
        INDEX,
        "                    if built_model != model or built_dim != dim:",
        "                    if False:",
        INDEX_T + "test_vectors_of_another_search_model_are_refused_and_nothing_is_stored",
    ),
    one(
        "the first vectors are stored without the record of their model",
        INDEX,
        '                    db.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (EMBEDDER, identity))',
        "                    pass",
        INDEX_T
        + "test_the_first_vectors_are_kept_with_the_search_model_that_made_them_in_one_transaction",
        API_T
        + "test_a_search_model_that_changed_pauses_meaning_offers_rebuild_and_never_mixes_spaces",
    ),
    one(
        "a batch begun before a rebuild is stored after it",
        INDEX,
        "                if epoch is not None and epoch != self.epoch:",
        "                if False:",
        INDEX_T + "test_a_batch_begun_before_a_rebuild_is_never_stored_after_it",
        WORKER_T + "test_a_rebuild_while_a_request_is_in_the_air_stores_nothing_of_it",
    ),
    one(
        "a document is ready before its last passage has a vector",
        INDEX,
        '                        status = READY if have >= doc["chunks"] else EMBEDDING',
        "                        status = READY",
        INDEX_T + "test_a_document_is_embedding_while_some_of_its_passages_have_vectors",
        API_T + "test_a_document_is_found_by_words_at_once_then_meaning_catches_up_and_it_is_ready",
    ),
    one(
        "a refused passage is owed again for ever",
        INDEX,
        "                        \"UPDATE chunks SET vec = X'' WHERE id = ? AND vec IS NULL\", (chunk_id,)",
        '                        "UPDATE chunks SET vec = NULL WHERE id = ? AND vec IS NULL", (chunk_id,)',
        INDEX_T
        + "test_a_passage_the_model_refused_is_kept_refused_and_the_document_is_still_ready",
        WORKER_T
        + "test_a_passage_too_long_for_the_search_model_is_refused_alone_and_the_rest_are_kept",
    ),
    one(
        "a rebuild keeps the record of the old model",
        INDEX,
        '                db.execute("DELETE FROM meta WHERE key = ?", (EMBEDDER,))',
        "                pass",
        INDEX_T + "test_rebuilding_clears_every_vector_and_the_record_and_leaves_the_words",
        API_T
        + "test_a_search_model_that_changed_pauses_meaning_offers_rebuild_and_never_mixes_spaces",
    ),
    one(
        "a rebuild does not end the batches begun before it",
        INDEX,
        "            owed = self._transaction(db, body)\n            self.epoch += 1\n            return owed",
        "            owed = self._transaction(db, body)\n            return owed",
        INDEX_T + "test_a_batch_begun_before_a_rebuild_is_never_stored_after_it",
    ),
    one(
        "an index made again does not end the batches begun before it",
        INDEX,
        "        self.epoch += 1\n        self.writes += 1\n",
        "        self.writes += 1\n",
        INDEX_T
        + "test_a_batch_begun_before_the_index_was_made_again_is_never_stored_in_the_new_one",
    ),
    one(
        "a write is not counted",
        INDEX,
        '            db.execute("COMMIT")\n            self.writes += 1\n',
        '            db.execute("COMMIT")\n',
        INDEX_T + "test_every_write_is_counted_and_a_read_is_not",
        API_T + "test_a_document_added_after_a_question_is_found_by_meaning_in_the_next_one",
    ),
    one(
        "opening nothing again is a write",
        INDEX,
        "            if (\n                db.execute(\n                    \"SELECT 1 FROM documents WHERE status = 'ready' AND chunks > embedded LIMIT 1\"\n                ).fetchone()\n                is None\n            ):\n                return 0\n",
        "",
        INDEX_T + "test_opening_nothing_again_is_not_a_write_so_a_cached_matrix_is_not_made_stale",
    ),
    one(
        "the vectors read include the refused ones",
        INDEX,
        "            \"AND length(c.vec) > 0 AND d.status IN ('searchable', 'embedding', 'ready')\"",
        "            \"AND d.status IN ('searchable', 'embedding', 'ready')\"",
        INDEX_T
        + "test_a_passage_the_model_refused_is_kept_refused_and_the_document_is_still_ready",
    ),
    one(
        "a hit comes from another scope",
        INDEX,
        '                f"WHERE c.id IN ({marks}) AND d.scope IN ({scope_marks}) "',
        '                f"WHERE c.id IN ({marks}) "',
        INDEX_T + "test_passages_found_by_meaning_come_back_as_hits_in_order_with_their_similarity",
        API_T
        + "test_a_chat_file_searched_instead_is_embedded_and_found_by_meaning_in_its_own_chat_only",
    ),
    # --- words and meaning together ----------------------------------------------------------
    one(
        "the constant of reciprocal rank fusion is not sixty",
        SEARCH,
        "RRF_K = 60",
        "RRF_K = 1",
        SEARCH_T + "test_each_way_adds_one_over_sixty_plus_the_rank_with_equal_weights",
        SEARCH_T + "test_a_passage_found_by_both_at_modest_ranks_beats_one_found_by_one_at_the_top",
    ),
    one(
        "a passage found by one way only is dropped",
        SEARCH,
        "        Candidate(by_chunk[c], tuple(via[c]), tuple(ranks[c]), similarity.get(c)) for c in order",
        "        Candidate(by_chunk[c], tuple(via[c]), tuple(ranks[c]), similarity.get(c))\n        for c in order\n        if len(via[c]) > 1",
        SEARCH_T
        + "test_a_passage_only_the_words_find_still_appears_and_so_does_one_only_meaning_finds",
        API_T
        + "test_a_passage_only_the_words_find_and_one_only_meaning_finds_both_appear_beside_each_other",
    ),
    one(
        "a way that cannot run stops the search",
        SEARCH,
        "            except SignalUnavailable as exc:",
        "            except KeyError as exc:",
        SEARCH_T + "test_a_way_that_cannot_run_is_left_out_and_said_and_the_other_goes_on",
        API_T
        + "test_stopping_the_search_model_mid_import_names_it_and_words_search_goes_on_and_it_resumes",
    ),
    one(
        "a best meaning under its floor is still evidence",
        SEARCH,
        "                and report.best < report.floor",
        "                and report.best < -1.0",
        SEARCH_T
        + "test_when_the_words_found_nothing_and_the_best_meaning_is_under_its_floor_nothing_matched",
        API_T
        + "test_below_its_floor_the_best_meaning_is_not_an_answer_and_with_a_word_it_always_counts",
    ),
    one(
        "the floor drops meaning even when a word matched",
        SEARCH,
        "                and not others\n",
        "",
        SEARCH_T
        + "test_when_any_word_matched_every_passage_meaning_found_counts_whatever_it_scored",
        API_T
        + "test_below_its_floor_the_best_meaning_is_not_an_answer_and_with_a_word_it_always_counts",
    ),
    one(
        "a run that names its ways has the evidence rule",
        SEARCH,
        "apply_floor=ways is None)",
        "apply_floor=True)",
        SEARCH_T + "test_a_run_that_names_its_ways_shows_what_each_found_whatever_it_scored",
        API_T
        + "test_below_its_floor_the_best_meaning_is_not_an_answer_and_with_a_word_it_always_counts",
    ),
    one(
        "the question by meaning is the latest message alone",
        SEARCH,
        '        asked = f"{asked}\\n\\n{before}"',
        "        asked = asked",
        SEARCH_T
        + "test_the_question_a_search_by_meaning_reads_is_the_latest_message_then_a_little_of_the_one_before",
    ),
    # --- the door -------------------------------------------------------------------------
    one(
        "the embeddings request asks for dimensions the gateway ignores",
        HUB,
        'body = {"model": model, "input": list(inputs), "encoding_format": "float"}',
        'body = {"model": model, "input": list(inputs), "encoding_format": "float", "dimensions": 256}',
        HUB_T + "test_a_request_names_the_model_and_the_inputs_and_nothing_the_gateway_ignores",
    ),
    one(
        "a Retry-After of a day is believed",
        HUB,
        "    return max(0.0, min(seconds, RETRY_AFTER_MAX))",
        "    return max(0.0, seconds)",
        HUB_T + "test_a_refusal_at_the_keys_limit_carries_how_long_the_gateway_asked_us_to_wait",
    ),
    one(
        "an answer with the wrong number of vectors is believed",
        HUB,
        "    if not isinstance(data, list) or len(data) != count:",
        "    if not isinstance(data, list):",
        HUB_T + "test_an_answer_that_is_not_what_was_asked_for_is_said_as_what_it_is",
    ),
    one(
        "an answer that names no model is believed",
        HUB,
        '    if not isinstance(model, str) or not model:\n        raise bad("it named no model")',
        '    if False:\n        raise bad("it named no model")',
        HUB_T + "test_an_answer_that_is_not_what_was_asked_for_is_said_as_what_it_is",
    ),
    one(
        "the vectors are not put in the order of the inputs",
        HUB,
        "        ordered[index] = vector",
        "        ordered[len(seen) - 1] = vector",
        HUB_T
        + "test_the_answer_is_put_in_the_order_the_inputs_were_sent_whatever_order_it_came_in",
    ),
    one(
        "a stopped search model is said as another failure",
        MEANING,
        '    if exc.status in (503, 404):\n        return "not_running", (',
        '    if exc.status in (404,):\n        return "not_running", (',
        HUB_T + "test_every_failure_is_said_with_what_was_observed_and_begins_by_words_only",
        API_T
        + "test_stopping_the_search_model_mid_import_names_it_and_words_search_goes_on_and_it_resumes",
    ),
    one(
        "a refusal for length is waited out for ever",
        MEANING,
        '_TOO_LONG = re.compile(\n    r"too (large|long|big)',
        '_TOO_LONG = re.compile(\n    r"zzzzzzzz (large|long|big)',
        HUB_T + "test_a_refusal_for_length_is_told_from_one_that_waiting_would_mend",
        WORKER_T
        + "test_a_passage_too_long_for_the_search_model_is_refused_alone_and_the_rest_are_kept",
    ),
    one(
        "a refused passage's note gives the table's limit and not what the engine said",
        MEANING,
        '        said = engine_words(exc).rstrip(". ")',
        '        said = "its limit is 32,768 tokens"',
        WORKER_T
        + "test_a_passage_too_long_for_the_search_model_is_refused_alone_and_the_rest_are_kept",
        WORKER_T
        + "test_the_note_on_a_refused_passage_carries_the_limit_the_engine_gave_not_the_tables",
    ),
    one(
        "the engine's words are left inside the gateway's",
        MEANING,
        "    found: list[str] = _ENGINE_MESSAGE.findall(said)\n    if not found:\n        return said\n",
        "    found: list[str] = []\n    if not found:\n        return said\n",
        WORKER_T
        + "test_what_the_engine_itself_said_is_taken_out_of_the_words_the_gateway_wraps_it_in",
        WORKER_T
        + "test_the_note_on_a_refused_passage_carries_the_limit_the_engine_gave_not_the_tables",
    ),
    one(
        "the speed is measured from a dozen seconds of work, not a stretch longer than the limit's minute",
        MEANING,
        "RATE_WINDOW = 90.0\nRATE_BATCHES = 400\n",
        "RATE_WINDOW = 12.0\nRATE_BATCHES = 400\n",
        WORKER_T + "test_the_speed_is_measured_over_a_stretch_longer_than_the_limits_own_minute",
    ),
    one(
        "the time left ignores the requests a minute that are allowed",
        MEANING,
        "        return min(rate, max(1, per_minute) * BATCH / WINDOW)\n",
        "        return rate\n",
        WORKER_T + "test_the_speed_never_promises_more_than_the_requests_a_minute_let_through",
        WORKER_T + "test_the_time_left_in_the_header_is_made_with_the_speed_the_limit_allows",
    ),
    one(
        "a batch of one passage is a measurement of the speed",
        MEANING,
        "        if passages >= RATE_MIN_BATCH:\n            self._done.append(",
        "        if True:\n            self._done.append(",
        WORKER_T + "test_a_batch_too_small_to_say_how_fast_the_work_goes_is_not_a_measurement",
        WORKER_T + "test_a_small_library_does_not_promise_how_long_a_big_one_will_take",
    ),
    one(
        "waiting for a document ends the moment it is searchable, before the worker is done",
        INGEST,
        "if d is not None and (d.status in PENDING or self.current == (sub, d.id))",
        "if d is not None and d.status in PENDING",
        INGEST_T + "test_waiting_does_not_end_while_the_worker_is_still_finishing_a_document",
    ),
    # --- adding meaning: limits, backing off, ending a request --------------------------------
    one(
        "a batch is sixty-four passages",
        MEANING,
        "BATCH = 16",
        "BATCH = 64",
        WORKER_T
        + "test_a_turn_embeds_sixteen_passages_and_stores_unit_vectors_with_what_the_search_was_built_with",
    ),
    one(
        "the requests a minute are not limited",
        MEANING,
        "            if len(self._starts) < max(1, limit):",
        "            if True:",
        WORKER_T + "test_at_most_so_many_requests_are_started_in_any_minute",
        WORKER_T + "test_the_limiter_lets_the_window_start_over_and_counts_what_is_used",
    ),
    one(
        "more than one request is in the air",
        MEANING,
        "            outcome = await self._work(sub, settings)\n",
        "            asyncio.ensure_future(self._work(sub, settings))\n            outcome = Outcome.WORKED\n            await asyncio.sleep(0)\n",
        WORKER_T + "test_at_most_one_request_is_in_the_air_even_with_many_documents",
    ),
    one(
        "a Retry-After is not obeyed",
        MEANING,
        "            wait = min(LIMIT_MAX_WAIT, exc.retry_after or self._retry_in(sub, BACKOFF_SECONDS))",
        "            wait = 0.0",
        WORKER_T
        + "test_a_429_with_retry_after_waits_that_long_and_then_asks_for_the_same_passages",
        API_T + "test_the_key_at_its_limit_backs_off_as_the_gateway_asks_and_carries_on_by_itself",
    ),
    one(
        "a Retry-After of two hours is believed",
        MEANING,
        "LIMIT_MAX_WAIT = 300.0",
        "LIMIT_MAX_WAIT = 30000000.0",
        WORKER_T + "test_a_retry_after_longer_than_five_minutes_is_not_believed",
    ),
    one(
        "a person's turn is not rotated",
        MEANING,
        "                self._owing.pop(sub)\n                self._owing[sub] = None  # to the back of the turn\n",
        "                pass\n",
        WORKER_T + "test_documents_are_served_in_turn_one_batch_each_and_a_chats_own_files_first",
    ),
    one(
        "documents are added while the owner has them off",
        MEANING,
        "        if not settings.enabled:\n            return None  # the owner turned documents off: nothing is added\n",
        "",
        WORKER_T + "test_nothing_is_added_while_the_owner_has_documents_off",
    ),
    one(
        "a search model that changed is not noticed in an answer",
        MEANING,
        '            if (bad := self._mismatch(ident, seen)) is not None:\n                log.warning("the search model changed while documents were embedded: %s", bad[0])\n                return Outcome.DONE',
        "            if False:\n                return Outcome.DONE",
        WORKER_T
        + "test_a_search_model_that_changed_is_refused_and_nothing_is_stored_until_rebuild",
    ),
    one(
        "another model's answer is not told from this one's by its name",
        MEANING,
        '        if seen.model != built.model:\n            return "changed"',
        '        if False:\n            return "changed"',
        WORKER_T
        + "test_a_search_model_that_changed_is_refused_and_nothing_is_stored_until_rebuild",
        API_T
        + "test_a_search_model_that_changed_pauses_meaning_offers_rebuild_and_never_mixes_spaces",
    ),
    one(
        "another size of the same model's vectors is not noticed",
        MEANING,
        '        if seen.dim != built.dim:\n            return "dimension"',
        '        if False:\n            return "dimension"',
        WORKER_T + "test_the_same_model_at_another_size_is_refused_too",
        API_T + "test_the_same_model_at_another_size_is_said_and_refused_too",
    ),
    one(
        "a removed document's request is not ended",
        MEANING,
        "            flight.dropped = True\n            flight.task.cancel()\n\n    async def rebuild",
        "            flight.dropped = True\n\n    async def rebuild",
        WORKER_T + "test_removing_the_document_a_request_is_only_for_ends_the_request",
        API_T
        + "test_removing_a_document_ends_the_request_that_was_only_for_it_and_nothing_of_it_comes_back",
    ),
    one(
        "a request that holds another document's passages is ended too",
        MEANING,
        "            and flight.docs <= wanted\n",
        "",
        WORKER_T + "test_a_request_that_holds_another_documents_passages_too_is_left_to_finish",
    ),
    one(
        "a rebuild leaves the request in the air",
        MEANING,
        "            flight.dropped = True\n            flight.task.cancel()\n        owed = await index.clear_vectors()",
        "            flight.dropped = True\n        owed = await index.clear_vectors()",
        WORKER_T + "test_a_rebuild_while_a_request_is_in_the_air_stores_nothing_of_it",
    ),
    one(
        "a rebuild clears nothing",
        MEANING,
        "        owed = await index.clear_vectors()",
        "        owed = 0",
        WORKER_T
        + "test_rebuild_clears_what_the_old_model_made_and_adds_meaning_again_with_the_new_one",
        API_T
        + "test_a_search_model_that_changed_pauses_meaning_offers_rebuild_and_never_mixes_spaces",
    ),
    one(
        "the boot does not look for passages owed their meaning",
        MEANING,
        "            if counts.owed or counts.words_only:\n                self.wake(sub)\n                owing += 1",
        "            if False:\n                self.wake(sub)\n                owing += 1",
        WORKER_T
        + "test_a_stop_in_the_middle_is_picked_up_at_boot_from_the_passages_without_a_vector",
        API_T
        + "test_a_stop_in_the_middle_is_picked_up_at_boot_from_the_passages_that_have_no_vector",
    ),
    one(
        "a search model started later is not noticed when someone looks",
        MEANING,
        "        if counts.owed or counts.words_only:\n            self.wake(index.sub)",
        "        if False:\n            self.wake(index.sub)",
        WORKER_T
        + "test_a_search_model_started_after_the_documents_were_read_is_noticed_when_someone_looks",
        API_T
        + "test_a_search_model_started_after_the_documents_were_read_is_noticed_and_meaning_is_added",
    ),
    one(
        "a document is owed its meaning when no search model is in play",
        MEANING,
        "        if not await self.in_play(index):\n            return False\n        self.wake(index.sub)",
        "        self.wake(index.sub)",
        WORKER_T + "test_a_document_is_owed_its_meaning_only_when_a_search_model_is_in_play",
        API_T
        + "test_with_no_search_model_documents_are_ready_by_words_and_the_header_says_where_to_get_one",
    ),
    one(
        "the time left is a constant",
        MEANING,
        "        eta = queue.seconds_to_finish() if owed else None",
        "        eta = 600.0 if owed else None",
        WORKER_T + "test_the_time_left_is_not_a_number_until_a_batch_has_been_measured",
        WORKER_T
        + "test_a_documents_row_says_how_much_of_its_meaning_is_added_and_how_long_is_left",
    ),
    one(
        "the time left ignores the first batch",
        MEANING,
        "        counted = sum(p for _, p, _ in recent[1:])",
        "        counted = sum(p for _, p, _ in recent)",
        WORKER_T + "test_the_time_left_is_measured_from_the_batches_done_and_is_none_before_any",
    ),
    one(
        "the time left ignores the people waiting",
        MEANING,
        "        return ahead * max(1, self.people) / self.rate",
        "        return ahead / self.rate",
        WORKER_T
        + "test_the_time_to_finish_a_document_counts_what_is_ahead_of_it_and_the_people_waiting",
    ),
    one(
        "a person who was never offered meaning is not told so quietly",
        MEANING,
        "                quiet=built is None,",
        "                quiet=False,",
        API_T
        + "test_with_no_search_model_documents_are_ready_by_words_and_the_header_says_where_to_get_one",
    ),
    one(
        "the header does not say no search model is running",
        MEANING,
        '    if code in NO_MODEL_CODES:\n        return "Searching by words only. No search model is running."',
        '    if code in NO_MODEL_CODES:\n        return "Searching by words only."',
        WORKER_T + "test_the_header_says_why_it_is_by_words_only_for_each_reason",
        API_T
        + "test_with_no_search_model_documents_are_ready_by_words_and_the_header_says_where_to_get_one",
    ),
    one(
        "the header does not offer Rebuild",
        MEANING,
        '        if code in REBUILD_CODES:\n            actions.append("rebuild")',
        '        if False:\n            actions.append("rebuild")',
        WORKER_T
        + "test_a_search_model_that_changed_is_refused_and_nothing_is_stored_until_rebuild",
        API_T
        + "test_a_search_model_that_changed_pauses_meaning_offers_rebuild_and_never_mixes_spaces",
    ),
    # --- what a person is told, and the settings ------------------------------------------------
    one(
        "meaning that could not run is not said under an answer",
        SAVED_API,
        "        if gone is not None and not gone.ran and not gone.quiet:",
        "        if False:",
        API_T + "test_when_meaning_cannot_run_the_chat_goes_on_by_words_and_the_answer_says_so",
    ),
    one(
        "nothing matched is said plainly when meaning was off",
        SAVED_API,
        '    if block.get("used") or block.get("quiet") or not block.get("message"):\n        return ""',
        '    return ""',
        API_T + "test_nothing_matched_by_words_is_not_said_plainly_when_meaning_was_off",
    ),
    one(
        "a run that names its ways searches by all",
        SAVED_API,
        '        ways=ways,\n    )\n    out["mode"] = result.mode',
        '        ways=None,\n    )\n    out["mode"] = result.mode',
        API_T + "test_a_run_that_names_its_ways_searches_by_only_those",
    ),
    one(
        "rebuild answers without rebuilding",
        SAVED_API,
        "    owed = await saved.meaning.rebuild(person.sub)",
        "    owed = 0",
        API_T
        + "test_a_search_model_that_changed_pauses_meaning_offers_rebuild_and_never_mixes_spaces",
    ),
    one(
        "a removed document's request is ended by nothing",
        SAVED,
        "            await self._cancel(sub, [doc_id])\n            try:",
        "            await self.ingest.cancel(sub, [doc_id])\n            try:",
        API_T
        + "test_removing_a_document_ends_the_request_that_was_only_for_it_and_nothing_of_it_comes_back",
    ),
    one(
        "the requests a minute take any value",
        CONFIG,
        "            and MIN_EMBED_PER_MINUTE <= value <= MAX_EMBED_PER_MINUTE\n",
        "            and True\n",
        API_T
        + "test_the_search_model_setting_shows_what_it_comes_to_and_the_new_settings_refuse_what_they_cannot_take",
    ),
    one(
        "the search model setting takes anything",
        CONFIG,
        "        if isinstance(value, str) and value.strip() and len(value.strip()) <= MAX_MODEL_ID:",
        "        if True:",
        API_T
        + "test_the_search_model_setting_shows_what_it_comes_to_and_the_new_settings_refuse_what_they_cannot_take",
    ),
    one(
        "the setting does not show what it resolves to",
        CONFIG,
        '        f.model_copy(update={"suggestions": [AUTO, *suggestions], "status": status_})',
        "        f",
        API_T
        + "test_the_search_model_setting_shows_what_it_comes_to_and_the_new_settings_refuse_what_they_cannot_take",
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
            timeout=240,
        )
    except subprocess.TimeoutExpired:
        return False, "the checks did not finish in 240 seconds"
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
    backup = Path(tempfile.mkdtemp(prefix="workbench-meaning-sabotage-"))
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
