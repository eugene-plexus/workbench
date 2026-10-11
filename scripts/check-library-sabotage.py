"""Prove the saved-documents checks are observed (MR4, documents in the prompt).

Each case breaks one thing in the chunker, the index, the reader's worker, the search,
the routes, the budget or the placement of passages, and its named check must fail.
Files are restored from exact bytes kept beside the run, never from Git. A mutation
that stops the code importing or compiling does not count as a caught defect.

  python scripts/check-library-sabotage.py            # every case
  python scripts/check-library-sabotage.py --anchors  # only check each anchor is unique
  python scripts/check-library-sabotage.py --labels fts,scope   # cases whose label has these words

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
CHUNK = SRC + "knowledge/chunk.py"
INDEX = SRC + "knowledge/index.py"
INGEST = SRC + "knowledge/ingest.py"
SAVED = SRC + "knowledge/saved.py"
SEARCH = SRC + "knowledge/search.py"
READER = SRC + "knowledge/reader.py"
SAVED_API = SRC + "saved_api.py"
DOCS_API = SRC + "documents_api.py"
CONTEXT = SRC + "context.py"
CONVERSATION = SRC + "conversation.py"
API = SRC + "api.py"
ANSWERS = SRC + "answers.py"
STORE = SRC + "store.py"
CONFIG = SRC + "config.py"
FILES = SRC + "files.py"

CHUNKS = "tests/test_knowledge_chunk.py::"
INDEXES = "tests/test_knowledge_index.py::"
SEARCHES = "tests/test_knowledge_search.py::"
INGESTS = "tests/test_knowledge_ingest.py::"
RETRIEVAL = "tests/test_retrieval_context.py::"
API_T = "tests/test_saved_api.py::"
CHATS = "tests/test_saved_chats.py::"
SCOPES = "tests/test_saved_scopes.py::"
FACADE = "tests/test_saved_facade.py::"
STORES = "tests/test_saved_store.py::"
CONTEXTS = "tests/test_context.py::"
READERS = "tests/test_reader.py::"

Case = tuple[str, str, list[tuple[str, str]], str]


def one(label: str, name: str, before: str, after: str, check: str) -> Case:
    return (label, name, [(before, after)], check)


# (label, file, [(before, after), ...], check)
CASES: list[Case] = [
    # --- cutting a file into passages -------------------------------------------------
    one(
        "a heading does not start a section",
        CHUNK,
        "            sections.append(_Section(_heading_path(stack), []))\n            continue\n",
        "            continue\n",
        CHUNKS + "test_a_passage_never_crosses_a_heading_even_when_two_sections_would_fit_in_one",
    ),
    one(
        "sibling headings nest under each other",
        CHUNK,
        "            while stack and stack[-1][0] >= found.level:\n",
        "            while False and stack and stack[-1][0] >= found.level:\n",
        CHUNKS + "test_headings_give_sections_and_every_passage_keeps_the_path_of_its_headings",
    ),
    one(
        "a passage keeps no heading path",
        CHUNK,
        '    if not stack:\n        return None\n    path = " > ".join(text for _, text in stack)\n',
        '    return None\n    path = " > ".join(text for _, text in stack)\n',
        CHUNKS + "test_headings_give_sections_and_every_passage_keeps_the_path_of_its_headings",
    ),
    one(
        "a long heading path is not cut",
        CHUNK,
        "MAX_PATH = 300\n",
        "MAX_PATH = 30000\n",
        CHUNKS + "test_a_long_heading_path_is_cut_and_says_so",
    ),
    one(
        "a setext underline is text",
        CHUNK,
        "        if number == heading_at + 1 and _UNDERLINE.match(line):\n",
        "        if False and number == heading_at + 1 and _UNDERLINE.match(line):\n",
        CHUNKS + "test_a_setext_heading_and_its_underline_are_a_heading_and_not_text",
    ),
    one(
        "a passage may grow past the maximum",
        CHUNK,
        "            if (grown > TARGET and size >= MIN) or grown > MAX:\n",
        "            if grown > TARGET and size >= MIN:\n",
        CHUNKS + "test_a_small_block_before_a_big_one_is_not_joined_to_it_past_the_maximum",
    ),
    one(
        "a passage is closed before it has the minimum",
        CHUNK,
        "            if (grown > TARGET and size >= MIN) or grown > MAX:\n",
        "            if grown > TARGET or grown > MAX:\n",
        CHUNKS + "test_a_block_under_the_minimum_is_not_left_alone_before_a_big_one_it_fits_with",
    ),
    one(
        "a tail under the minimum stays alone",
        CHUNK,
        "    if (\n        len(groups) > 1\n        and _group_weight(groups[-1]) < MIN\n",
        "    if (\n        False\n        and _group_weight(groups[-1]) < MIN\n",
        CHUNKS + "test_a_tail_under_the_minimum_joins_the_passage_before_it",
    ),
    one(
        "a block over the maximum is not cut",
        CHUNK,
        "    if weight(block.text) <= MAX:\n        return [block]\n",
        "    return [block]\n",
        CHUNKS + "test_a_paragraph_over_the_maximum_is_cut_at_sentences_and_no_sentence_is_split",
    ),
    one(
        "sentences are not where a long block is cut",
        CHUNK,
        "    parts = _SENTENCE.split(text)\n",
        "    parts = [text]\n",
        CHUNKS + "test_a_paragraph_over_the_maximum_is_cut_at_sentences_and_no_sentence_is_split",
    ),
    one(
        "a run is cut inside a word",
        CHUNK,
        "        if space > cut // 2:\n            cut = space\n",
        "        if False:\n            cut = space\n",
        CHUNKS + "test_a_run_with_no_sentence_end_is_cut_at_a_space_and_never_inside_a_word",
    ),
    one(
        "a CJK character weighs as one",
        CHUNK,
        "CJK_WEIGHT = 3\n",
        "CJK_WEIGHT = 1\n",
        CHUNKS + "test_a_cjk_character_weighs_three_so_the_same_numbers_give_about_the_same_tokens",
    ),
    one(
        "a hyphen at the end of a PDF line stays in the word",
        CHUNK,
        "            out = out + line if compound else out[:-1] + line\n",
        "            out = out + line\n",
        CHUNKS + "test_a_word_split_by_a_hyphen_at_the_end_of_a_line_is_made_whole",
    ),
    one(
        "a bullet does not start a paragraph",
        CHUNK,
        "        if current and _BULLET.match(line):\n            flush()\n",
        "        if False:\n            flush()\n",
        CHUNKS + "test_a_bullet_starts_a_new_paragraph_on_a_page",
    ),
    one(
        "a short sentence does not end a PDF paragraph",
        CHUNK,
        "        if len(stripped) < 0.6 * width and stripped.rstrip(_CLOSERS).endswith(tuple(_ENDS)):\n            flush()\n",
        "        if False:\n            flush()\n",
        CHUNKS + "test_a_pdfs_hard_wrapped_lines_are_one_paragraph_and_a_short_sentence_ends_one",
    ),
    one(
        "a passage says only its last page",
        CHUNK,
        "        page_start=min(pages) if pages else None,\n",
        "        page_start=max(pages) if pages else None,\n",
        CHUNKS + "test_a_pdf_passage_says_its_pages_and_may_run_from_one_page_to_the_next",
    ),
    one(
        "a passage's offsets are not where it is",
        CHUNK,
        "        char_start=min(b.start for b in group),\n",
        "        char_start=0,\n",
        CHUNKS + "test_offsets_locate_a_passage_in_the_text_that_show_indexed_text_shows",
    ),
    one(
        "a file's lines are not the locator of its passages",
        CHUNK,
        "        line_start=min(starts) if starts else None,\n",
        "        line_start=None,\n",
        CHUNKS + "test_lines_are_the_locator_of_a_file_that_is_read_as_its_own_lines",
    ),
    one(
        "a code fence is not kept whole",
        CHUNK,
        '    fences = extracted.kind == "markdown"\n',
        "    fences = False\n",
        CHUNKS + "test_a_blank_line_inside_a_fence_does_not_let_a_passage_end_inside_it",
    ),
    one(
        "a CSV passage forgets its column names",
        CHUNK,
        "    ) or None\n",
        "    ) and None\n",
        CHUNKS + "test_a_csv_keeps_its_column_names_with_every_passage_and_never_splits_a_row",
    ),
    one(
        "passages are numbered from one",
        CHUNK,
        "            out.append(_passage(len(out), section.path, group))\n",
        "            out.append(_passage(len(out) + 1, section.path, group))\n",
        CHUNKS + "test_passages_are_numbered_in_reading_order_from_zero",
    ),
    one(
        "tokens are not the cautious estimate",
        CHUNK,
        "        tokens_est=token_count.estimate(text),\n",
        "        tokens_est=len(text) // 4,\n",
        CHUNKS + "test_tokens_are_estimated_by_the_cautious_rule_every_budget_uses",
    ),
    # --- the words of a question ------------------------------------------------------
    one(
        "a stop word is searched for",
        SEARCH,
        "    return (len(word) >= MIN_TERM or _CJK.search(word) is not None) and word not in _STOP\n",
        "    return len(word) >= MIN_TERM or _CJK.search(word) is not None\n",
        SEARCHES + "test_stop_words_and_short_words_say_nothing_and_are_left_out_but_numbers_may",
    ),
    one(
        "a short word is searched for",
        SEARCH,
        "    return (len(word) >= MIN_TERM or _CJK.search(word) is not None) and word not in _STOP\n",
        "    return word not in _STOP\n",
        SEARCHES + "test_stop_words_and_short_words_say_nothing_and_are_left_out_but_numbers_may",
    ),
    one(
        "a query has no limit of terms",
        SEARCH,
        "        if len(terms) >= MAX_TERMS:\n            break\n",
        "        if False:\n            break\n",
        SEARCHES + "test_the_latest_message_comes_first_and_twelve_terms_are_the_most",
    ),
    one(
        "the message before comes first in a query",
        SEARCH,
        '    for word in words_of(latest) + words_of((previous or "")[:PREVIOUS_CHARS]):\n',
        '    for word in words_of((previous or "")[:PREVIOUS_CHARS]) + words_of(latest):\n',
        SEARCHES + "test_the_latest_message_comes_first_and_twelve_terms_are_the_most",
    ),
    one(
        "the latest message is not cut",
        SEARCH,
        "    latest = latest[:LATEST_CHARS]\n",
        "    latest = latest\n",
        SEARCHES
        + "test_the_latest_message_is_cut_at_a_thousand_characters_and_the_one_before_at_three_hundred",
    ),
    one(
        "the message before is not cut",
        SEARCH,
        'words_of((previous or "")[:PREVIOUS_CHARS])',
        'words_of((previous or ""))',
        SEARCHES
        + "test_the_latest_message_is_cut_at_a_thousand_characters_and_the_one_before_at_three_hundred",
    ),
    one(
        "a follow-up searches nothing of the message before",
        SEARCH,
        'words_of((previous or "")[:PREVIOUS_CHARS])',
        'words_of("")',
        SEARCHES + "test_a_follow_up_still_searches_something_by_the_message_before_it",
    ),
    one(
        "the punctuation a person typed reaches the search",
        SEARCH,
        '_WORD = re.compile(r"[^\\W_]+")\n',
        '_WORD = re.compile(r"\\S+")\n',
        SEARCHES + "test_nothing_a_person_types_is_search_syntax",
    ),
    one(
        "an identifier is not searched as a phrase",
        SEARCH,
        '    identifiers = [\n        m for m in _IDENTIFIER.findall(latest) if any(c.isdigit() or c == "-" for c in m)\n    ]\n',
        "    identifiers: list[str] = []\n",
        SEARCHES + "test_an_identifier_is_searched_as_an_exact_phrase_as_well_as_by_its_parts",
    ),
    one(
        "a quoted string is not searched as a phrase",
        SEARCH,
        "    quoted = [a or b for a, b in _QUOTED.findall(latest)]\n",
        "    quoted: list[str] = []\n",
        SEARCHES + "test_a_quoted_string_is_searched_as_an_exact_phrase",
    ),
    # --- finding, choosing, joining, fitting ------------------------------------------
    one(
        "ways of searching are not joined by reciprocal rank",
        SEARCH,
        "            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank)\n",
        "            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + rank\n",
        SEARCHES
        + "test_two_ways_of_searching_are_joined_by_reciprocal_rank_and_a_lone_find_still_appears",
    ),
    one(
        "one document crowds out the rest",
        SEARCH,
        "        if doc not in named and count.get(doc, 0) >= per_document:\n            continue\n",
        "        if False:\n            continue\n",
        SEARCHES
        + "test_at_most_three_passages_come_from_one_document_unless_the_question_names_it",
    ),
    one(
        "a file the question names is held to the cap",
        SEARCH,
        "        if doc not in named and count.get(doc, 0) >= per_document:\n",
        "        if count.get(doc, 0) >= per_document:\n",
        SEARCHES
        + "test_at_most_three_passages_come_from_one_document_unless_the_question_names_it",
    ),
    one(
        "a file is named by any one word of its name",
        SEARCH,
        "    return all(p in asked for p in parts)\n",
        "    return any(p in asked for p in parts)\n",
        SEARCHES + "test_a_question_names_a_file_when_every_word_of_its_name_is_in_it",
    ),
    one(
        "passages that are not next to each other are joined",
        SEARCH,
        "                candidate.hit.ord == last.ord + 1\n",
        "                True\n",
        SEARCHES + "test_passages_under_different_headings_or_not_next_to_each_other_stay_apart",
    ),
    one(
        "passages under different headings are joined",
        SEARCH,
        "                and candidate.hit.heading_path == last.heading_path\n",
        "                and True\n",
        SEARCHES + "test_passages_under_different_headings_or_not_next_to_each_other_stay_apart",
    ),
    one(
        "a joined passage has no length limit",
        SEARCH,
        "                and joined <= limit\n",
        "                and True\n",
        SEARCHES + "test_a_merged_passage_is_never_longer_than_the_limit",
    ),
    one(
        "passages are not fitted to the budget",
        SEARCH,
        "        if used + cost <= budget:\n",
        "        if True:\n",
        SEARCHES
        + "test_passages_are_fitted_to_the_budget_in_order_and_what_did_not_fit_is_counted",
    ),
    one(
        "fitted passages keep their old numbers",
        SEARCH,
        "    return [replace(p, rank=i) for i, p in enumerate(fit, start=1)], len(passages) - len(fit)\n",
        "    return fit, len(passages) - len(fit)\n",
        SEARCHES
        + "test_passages_are_fitted_to_the_budget_in_order_and_what_did_not_fit_is_counted",
    ),
    one(
        "no budget sends no passage",
        SEARCH,
        "    if budget is None:\n        return list(passages), 0\n",
        "    if budget is None:\n        return [], len(passages)\n",
        SEARCHES + "test_no_budget_is_no_limit_and_a_budget_too_small_for_one_passage_sends_none",
    ),
    one(
        "a snippet is not cut at the limit",
        SEARCH,
        "    end = min(len(one), start + room)\n",
        "    end = len(one)\n",
        SEARCHES + "test_a_snippet_is_at_most_240_characters_around_the_words_searched_for",
    ),
    one(
        "a snippet is not around the words searched for",
        SEARCH,
        "        if len(together) > best:\n            at, best = here, len(together)\n",
        "        if False:\n            at, best = here, len(together)\n",
        SEARCHES
        + "test_a_snippet_is_around_where_the_most_words_searched_for_are_together_as_whole_words",
    ),
    one(
        "a passage does not say which words matched",
        SEARCH,
        "        terms=tuple(t for t in query.terms if t in in_body),\n",
        "        terms=query.terms,\n",
        SEARCHES
        + "test_a_passage_says_which_of_the_words_searched_for_are_in_it_and_in_its_heading",
    ),
    one(
        "the retriever takes every candidate",
        SEARCH,
        "            candidates, k=max(1, min(k, PASSAGES_MAX)), per_document=per_document, named=named\n",
        "            candidates, k=1000, per_document=per_document, named=named\n",
        SEARCHES + "test_the_retriever_picks_chooses_joins_and_fits_in_that_order",
    ),
    one(
        "a search that breaks is nothing matched",
        SEARCH,
        "            found[signal.name] = await signal.search(index, query, scopes, CANDIDATES)\n",
        "            try:\n                found[signal.name] = await signal.search(index, query, scopes, CANDIDATES)\n"
        "            except Exception:\n                found[signal.name] = []\n",
        SEARCHES + "test_a_way_of_searching_that_breaks_raises_and_is_never_nothing_matched",
    ),
    # --- the index: its file, its search, its deletion --------------------------------
    one(
        "a removed passage stays in the search index",
        INDEX,
        '"CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN "',
        '"CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks WHEN 0 BEGIN "',
        INDEXES + "test_removing_a_document_removes_its_row_passages_search_entries_and_files",
    ),
    one(
        "an edited passage keeps its old words in the search index",
        INDEX,
        '"CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE OF text, heading_path ON chunks BEGIN "',
        '"CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE OF text, heading_path ON chunks WHEN 0 BEGIN "',
        INDEXES + "test_the_full_text_index_follows_an_edit_to_a_passage",
    ),
    one(
        "a new passage is not in the search index",
        INDEX,
        '"CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN "',
        '"CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks WHEN 0 BEGIN "',
        SEARCHES + "test_the_passage_with_more_of_the_rare_words_is_first_by_bm25",
    ),
    one(
        "foreign keys are off",
        INDEX,
        '            db.execute("PRAGMA foreign_keys=ON")\n',
        '            db.execute("PRAGMA foreign_keys=OFF")\n',
        INDEXES + "test_foreign_keys_are_on_so_a_passage_cannot_outlive_its_document",
    ),
    one(
        "deleted bytes are not overwritten",
        INDEX,
        '            db.execute("PRAGMA secure_delete=ON")\n',
        '            db.execute("PRAGMA secure_delete=OFF")\n',
        INDEXES + "test_a_removed_documents_words_are_not_left_in_the_file_or_its_log",
    ),
    one(
        "removed words stay in the full-text segments",
        INDEX,
        "            db.execute(\"INSERT INTO chunks_fts (chunks_fts) VALUES ('optimize')\")\n",
        "            pass\n",
        INDEXES + "test_a_removed_documents_words_are_not_left_in_the_file_or_its_log",
    ),
    one(
        "removed words stay in the log",
        INDEX,
        '            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")\n',
        "            pass\n",
        INDEXES + "test_a_removed_documents_words_are_not_left_in_the_file_or_its_log",
    ),
    one(
        "a removal does not scrub the file",
        INDEX,
        "            gone = self._transaction(db, body)\n            if gone is not None:\n                self._scrub(db)\n",
        "            gone = self._transaction(db, body)\n",
        INDEXES + "test_a_removed_documents_words_are_not_left_in_the_file_or_its_log",
    ),
    one(
        "a replaced file's words stay in the file",
        INDEX,
        "            if added.replaced:\n                self._scrub(db)\n",
        "            pass\n",
        INDEXES + "test_a_replaced_documents_words_are_not_left_in_the_file_or_its_log",
    ),
    one(
        "an index opens for someone it is not the owner's",
        INDEX,
        '            elif owner[0] != self.sub:\n                raise IndexDamaged("this index belongs to someone else")\n',
        '            elif False:\n                raise IndexDamaged("this index belongs to someone else")\n',
        INDEXES + "test_an_index_refuses_to_open_for_anyone_but_its_owner",
    ),
    one(
        "an index from a newer Workbench is opened",
        INDEX,
        "        if version > SCHEMA_VERSION:\n            raise IndexUnavailable(\n",
        "        if False:\n            raise IndexUnavailable(\n",
        INDEXES + "test_an_index_from_a_newer_workbench_is_refused_and_left_as_it_is",
    ),
    one(
        "an index is not versioned",
        INDEX,
        '            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")\n',
        "            pass\n",
        INDEXES + "test_a_new_index_is_versioned_owned_and_records_how_its_passages_were_made",
    ),
    one(
        "an older index is not brought forward",
        INDEX,
        "                for statement in _MIGRATIONS[target]:\n",
        "                for statement in []:\n",
        INDEXES + "test_a_migration_brings_an_older_index_forward_and_keeps_its_documents",
    ),
    one(
        "a full disk is called damage",
        INDEX,
        '_FULL = ("database or disk is full",)\n',
        "_FULL = ()\n",
        INDEXES + "test_what_sqlite_says_decides_whether_an_index_is_damaged",
    ),
    one(
        "a busy index is called damage",
        INDEX,
        '_TRANSIENT = ("database is locked", "database table is locked")\n',
        "_TRANSIENT = ()\n",
        INDEXES + "test_what_sqlite_says_decides_whether_an_index_is_damaged",
    ),
    one(
        "a replaced document is kept",
        INDEX,
        '                for old in twins:\n                    db.execute("DELETE FROM documents WHERE id = ?", (old.id,))\n                    self._remove_files(old.id)\n',
        "                for old in twins:\n                    pass\n",
        INDEXES + "test_the_same_name_replaces_the_old_document_at_once_with_its_words_and_files",
    ),
    one(
        "a name in another case is another file",
        INDEX,
        '"SELECT * FROM documents WHERE scope = ? AND name = ? COLLATE NOCASE "',
        '"SELECT * FROM documents WHERE scope = ? AND name = ? "',
        INDEXES + "test_the_same_name_replaces_the_old_document_at_once_with_its_words_and_files",
    ),
    one(
        "the same name with other bytes is the same file",
        INDEX,
        "                        and (new.scope != LIBRARY or t.sha256 == new.sha256)\n",
        "                        and True\n",
        INDEXES + "test_the_same_name_replaces_the_old_document_at_once_with_its_words_and_files",
    ),
    one(
        "a document that failed is not read again when added again",
        INDEX,
        "                        if t.status not in (SKIPPED, FAILED)\n",
        "                        if True\n",
        INDEXES + "test_a_document_that_failed_is_read_again_when_the_same_file_is_added_again",
    ),
    one(
        "a chat's file waits behind the library",
        INDEX,
        "\"ORDER BY (scope = 'library'), added_at, rowid LIMIT 1\"",
        '"ORDER BY added_at, rowid LIMIT 1"',
        INDEXES
        + "test_the_document_waiting_longest_is_read_first_and_a_chats_file_before_the_library",
    ),
    one(
        "the newest document is read first",
        INDEX,
        "\"ORDER BY (scope = 'library'), added_at, rowid LIMIT 1\"",
        "\"ORDER BY (scope = 'library'), added_at DESC, rowid LIMIT 1\"",
        INDEXES
        + "test_the_document_waiting_longest_is_read_first_and_a_chats_file_before_the_library",
    ),
    one(
        "an old reading commits over a document read again",
        INDEX,
        '                if row is None or row["status"] != READING or row["attempt"] != attempt:\n',
        '                if row is None or row["status"] != READING:\n',
        INDEXES + "test_a_reading_from_an_earlier_attempt_cannot_commit_over_a_later_one",
    ),
    one(
        "a person's passage limit is not kept",
        INDEX,
        "                if others + len(passages) > cap:\n",
        "                if False:\n",
        INDEXES
        + "test_the_passage_limit_skips_the_document_that_would_pass_it_and_keeps_none_of_it",
    ),
    one(
        "a document ends unread whatever attempt it is",
        INDEX,
        "\"WHERE id = ? AND status = 'reading' AND attempt = ?\",",
        "\"WHERE id = ? AND status = 'reading' AND ? IS NOT NULL\",",
        INDEXES
        + "test_only_a_document_still_being_read_can_end_unread_and_only_by_its_own_attempt",
    ),
    one(
        "a stop leaves a document saying reading",
        INDEX,
        "                    \"WHERE status = 'reading'\"\n                ).rowcount\n",
        "                    \"WHERE status = 'never'\"\n                ).rowcount\n",
        INDEXES + "test_a_stop_while_a_document_was_read_puts_it_back_in_the_queue",
    ),
    one(
        "reading again keeps the old passages",
        INDEX,
        '                db.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))\n                db.execute(\n                    "UPDATE documents SET status = \'queued\', attempt = attempt + 1, "',
        "                db.execute(\n                    \"UPDATE documents SET status = 'queued', attempt = attempt + 1, \"",
        INDEXES + "test_a_document_in_a_state_that_cannot_be_found_has_no_passages",
    ),
    one(
        "a search looks at documents that cannot be found",
        INDEX,
        "    \"AND d.status IN ('searchable', 'embedding', 'ready') \"\n",
        '    "AND 1 "\n',
        INDEXES + "test_the_search_looks_only_at_documents_that_can_be_found_whatever_rows_say",
    ),
    one(
        "a search is not held to its scope",
        INDEX,
        '"WHERE chunks_fts MATCH ? AND d.scope IN ({scopes}) "',
        '"WHERE chunks_fts MATCH ? AND (d.scope IN ({scopes}) OR 1) "',
        SEARCHES + "test_a_passage_of_another_scope_is_never_a_candidate",
    ),
    one(
        "a heading weighs the same as the text",
        INDEX,
        "bm25(chunks_fts, 1.0, 2.0) AS score ",
        "bm25(chunks_fts, 1.0, 1.0) AS score ",
        SEARCHES + "test_a_word_in_a_heading_outweighs_the_same_word_in_the_text",
    ),
    one(
        "an accent hides a passage",
        INDEX,
        "\"tokenize='unicode61 remove_diacritics 2')\",",
        "\"tokenize='unicode61 remove_diacritics 0')\",",
        SEARCHES + "test_accents_and_case_do_not_hide_a_passage",
    ),
    one(
        "the worst passage is first",
        INDEX,
        '"ORDER BY score, c.doc_id, c.ord LIMIT ?"',
        '"ORDER BY score DESC, c.doc_id, c.ord LIMIT ?"',
        SEARCHES + "test_the_passage_with_more_of_the_rare_words_is_first_by_bm25",
    ),
    one(
        "a removed document's text stays on the disk",
        INDEX,
        "            self.text_path(doc_id),\n            self.text_path(doc_id).with_name(",
        "            self.text_path(doc_id).with_name(",
        INDEXES + "test_removing_a_document_removes_its_row_passages_search_entries_and_files",
    ),
    one(
        "a removed document's sidecar stays on the disk",
        INDEX,
        "            self.meta_path(doc_id),\n            self.meta_path(doc_id).with_name(",
        "            self.meta_path(doc_id).with_name(",
        FACADE + "test_a_sidecar_says_what_a_documents_files_cannot",
    ),
    one(
        "a file that cannot be removed is passed over",
        INDEX,
        "                    if attempt == 5:\n                        raise DeleteFailed(\n",
        "                    if False:\n                        raise DeleteFailed(\n",
        INDEXES + "test_a_file_that_cannot_be_removed_rolls_the_whole_removal_back",
    ),
    one(
        "a file that is held for a moment is not waited for",
        INDEX,
        "                        ) from exc\n                    time.sleep(0.1)\n",
        "                        ) from exc\n                    pass\n",
        INDEXES + "test_a_file_held_for_a_moment_is_waited_for",
    ),
    one(
        "a half-written original is not found",
        INDEX,
        '                    if valid_id(stem) and (stem not in ids or path.name.endswith(".part")):\n',
        "                    if valid_id(stem) and stem not in ids:\n",
        INDEXES + "test_files_that_no_document_claims_are_found_and_half_written_ones_always",
    ),
    one(
        "every original is called an orphan",
        INDEX,
        '                    if valid_id(stem) and (stem not in ids or path.name.endswith(".part")):\n',
        '                    if valid_id(stem) and (True or path.name.endswith(".part")):\n',
        INDEXES + "test_files_that_no_document_claims_are_found_and_half_written_ones_always",
    ),
    one(
        "looking at a person's documents makes their directory",
        INDEX,
        "            if not create and not (directory / INDEX_FILE).is_file():\n                return None\n",
        "            pass\n",
        INDEXES + "test_looking_at_a_persons_documents_makes_no_directory_for_them",
    ),
    one(
        "documents being read are not counted as waiting",
        INDEX,
        "\"COALESCE(SUM(status IN ('queued','reading')), 0) AS waiting \"",
        "\"COALESCE(SUM(status IN ('queued')), 0) AS waiting \"",
        INDEXES + "test_counts_say_what_can_be_searched_and_what_is_still_waiting",
    ),
    one(
        "a passage is looked up in any document",
        INDEX,
        'f"WHERE c.doc_id = ? AND c.id IN ({marks}) ORDER BY c.ord",',
        'f"WHERE c.doc_id = ? OR 1 AND c.id IN ({marks}) ORDER BY c.ord",',
        INDEXES + "test_passages_are_looked_up_by_document_and_never_another_documents",
    ),
    one(
        "taking a chat's documents takes every scope's",
        INDEX,
        '                sql, params = "SELECT * FROM documents WHERE scope = ?", [scope]\n',
        '                sql, params = "SELECT * FROM documents WHERE scope = ? OR 1", [scope]\n',
        INDEXES + "test_a_chats_documents_go_with_their_scope_and_only_those",
    ),
    one(
        "a passage's id is used again",
        INDEX,
        '"id INTEGER PRIMARY KEY AUTOINCREMENT, "',
        '"id INTEGER PRIMARY KEY, "',
        INDEXES
        + "test_a_passages_id_is_never_used_again_so_a_citation_cannot_point_at_other_words",
    ),
    one(
        "two people share a directory",
        INDEX,
        "        return self.root / files.person_key(sub)\n",
        "        return self.root\n",
        INDEXES + "test_a_persons_directory_is_named_by_a_hash_of_their_sub_and_nobody_elses",
    ),
    one(
        "a person's directory is named by their sub",
        FILES,
        '    return hashlib.sha256(sub.encode("utf-8")).hexdigest()[:32]\n\n\ndef person_dir',
        "    return sub[:32]\n\n\ndef person_dir",
        INDEXES + "test_a_persons_directory_is_named_by_a_hash_of_their_sub_and_nobody_elses",
    ),
    # --- reading in the background ----------------------------------------------------
    one(
        "a person with many files is served first for ever",
        INGEST,
        "            self._pending.pop(sub, None)\n            if doc is None:\n                continue\n            self._pending[sub] = None\n            self.served.append((sub, doc.id))\n",
        "            if doc is None:\n                self._pending.pop(sub, None)\n                continue\n            self.served.append((sub, doc.id))\n",
        INGESTS + "test_a_person_with_many_files_does_not_keep_another_waiting",
    ),
    one(
        "a removed document's reader is left running",
        INGEST,
        "        await self._reader.cancel(keys)\n",
        "        pass\n",
        INGESTS + "test_removing_a_document_while_it_is_read_ends_its_reader_and_nothing_is_kept",
    ),
    one(
        "a cancelled reading is kept",
        INGEST,
        "        if extraction.cancelled:\n            return\n",
        "        if False:\n            return\n",
        INGESTS + "test_removing_a_document_while_it_is_read_ends_its_reader_and_nothing_is_kept",
    ),
    one(
        "a skipped file is not said to be skipped",
        INGEST,
        '                reason=str(failure["message"]),\n',
        '                reason=str(failure["reason"]),\n',
        INGESTS + "test_a_file_that_gives_no_text_ends_in_a_state_that_says_why_and_keeps_nothing",
    ),
    one(
        "a document's text is not kept",
        INGEST,
        "        await asyncio.to_thread(_write_text, text_path, text)\n",
        "        pass\n",
        INGESTS + "test_a_file_goes_from_queued_to_ready_and_can_be_found_by_its_words",
    ),
    one(
        "a document is never ready",
        INGEST,
        "        await index.finish(doc_id)\n        await self._announce()\n",
        "        await self._announce()\n",
        INGESTS + "test_a_file_goes_from_queued_to_ready_and_can_be_found_by_its_words",
    ),
    one(
        "a document that is owed work is ready",
        INGEST,
        "            if doc is not None and await self._owed(index, doc):\n                return\n",
        "            pass\n",
        INGESTS + "test_a_document_that_is_owed_more_work_is_searchable_by_words_and_not_ready",
    ),
    one(
        "a stop's readings are not resumed at boot",
        INGEST,
        "        done.requeued += await index.requeue_interrupted()\n",
        "        pass\n",
        INGESTS + "test_a_stop_in_the_middle_resumes_at_boot_and_each_document_is_read_once",
    ),
    one(
        "a deleted chat's documents stay at boot",
        INGEST,
        "                if owners.get(scope[len(CHAT) :]) != sub:\n",
        "                if False:\n",
        INGESTS + "test_the_documents_of_a_chat_that_is_gone_are_removed_at_boot_and_the_rest_kept",
    ),
    one(
        "another person's chat's documents stay at boot",
        INGEST,
        "                if owners.get(scope[len(CHAT) :]) != sub:\n",
        "                if owners.get(scope[len(CHAT) :]) is None:\n",
        INGESTS + "test_a_chat_document_whose_chat_belongs_to_someone_else_is_removed_too",
    ),
    one(
        "files no row claims stay at boot",
        INGEST,
        "            with contextlib.suppress(OSError):\n                path.unlink(missing_ok=True)\n                done.orphan_files += 1\n",
        "            pass\n",
        INGESTS + "test_files_no_row_claims_are_removed_at_boot_and_the_others_are_not",
    ),
    one(
        "a document that lost its text is not read again at boot",
        INGEST,
        "            if doc.status in FOUND and not index.text_path(doc.id).is_file():\n",
        "            if False:\n",
        INGESTS + "test_a_document_that_can_be_found_but_has_lost_its_text_is_read_again_at_boot",
    ),
    one(
        "a directory that is not its owner's is touched",
        INGEST,
        "            if sub is None or self.indexes.dir_of(sub) != directory:\n",
        "            if sub is None:\n",
        INGESTS + "test_a_directory_that_is_not_its_owners_is_not_touched",
    ),
    one(
        "a bug in one reading leaves the document reading",
        INGEST,
        "                await self._fail(sub, doc, exc)\n",
        "                pass\n",
        INGESTS + "test_a_bug_in_one_reading_fails_that_document_and_the_next_is_read",
    ),
    one(
        "the passage limit is not passed to the commit",
        INGEST,
        "                doc.id, doc.attempt, summary, passages, cap=await self._cap()\n",
        "                doc.id, doc.attempt, summary, passages, cap=10**9\n",
        INGESTS + "test_the_passage_limit_skips_the_file_that_would_pass_it",
    ),
    one(
        "an index that cannot be used stops the others",
        INGEST,
        '                log.warning("saved documents of one person cannot be read now: %s", exc.message)\n                self._pending.pop(sub, None)\n                continue\n',
        "                raise\n",
        INGESTS + "test_an_index_that_cannot_be_used_does_not_stop_the_others",
    ),
    one(
        "waiting ends at once",
        INGEST,
        "            if not waiting or left <= 0:\n                return waiting\n",
        "            if True:\n                return waiting\n",
        INGESTS + "test_waiting_ends_when_the_documents_are_read_and_not_before_its_time",
    ),
    one(
        "a reading's name is not a file name",
        READER,
        '    return re.sub(r"[^A-Za-z0-9_.-]", "-", key)\n',
        "    return key\n",
        INGESTS + "test_cancelling_a_documents_key_ends_its_real_reader_and_the_next_is_read",
    ),
    # --- the person's own documents ---------------------------------------------------
    one(
        "a replaced file's reader is left running",
        SAVED,
        "        if added.replaced:\n            await self.ingest.cancel(sub, [d.id for d in added.replaced])\n",
        "        if added.replaced:\n            pass\n",
        FACADE + "test_replacing_a_document_ends_the_reader_of_the_old_one_by_its_key",
    ),
    one(
        "a document is added and never read",
        SAVED,
        "        self.ingest.wake(sub)\n        return added\n",
        "        return added\n",
        FACADE + "test_a_document_is_added_read_and_found",
    ),
    one(
        "the same file again keeps its copy",
        SAVED,
        "        if added.unchanged:\n            final.unlink(missing_ok=True)\n            index.meta_path(doc_id).unlink(missing_ok=True)\n            return added\n",
        "        if added.unchanged:\n            return added\n",
        FACADE + "test_the_same_file_again_is_not_read_again_and_the_copy_is_thrown_away",
    ),
    one(
        "a removed document's reader is not ended first",
        SAVED,
        "            await self.ingest.cancel(sub, [doc_id])\n            try:\n                return await index.delete(doc_id)\n",
        "            try:\n                return await index.delete(doc_id)\n",
        FACADE + "test_removing_a_document_ends_its_reader_first_so_nothing_holds_its_file",
    ),
    one(
        "a file that stays held is not waited for",
        SAVED,
        "                if attempt == 2:\n                    raise\n",
        "                if True:\n                    raise\n",
        FACADE + "test_a_file_that_stays_held_is_waited_for_and_then_named_and_nothing_is_removed",
    ),
    one(
        "a document whose original is gone is read again",
        SAVED,
        "        if not await asyncio.to_thread(index.original_path(doc_id).is_file):\n            raise OriginalMissing\n",
        "        if False:\n            raise OriginalMissing\n",
        FACADE
        + "test_reading_again_drops_the_passages_now_and_a_missing_original_cannot_be_read_again",
    ),
    one(
        "a deleted chat's documents stay",
        SAVED,
        "        return len(await index.delete_where(chat_scope(chat_id)))\n",
        "        return 0\n",
        FACADE + "test_dropping_a_chat_or_a_file_removes_the_documents_made_from_them",
    ),
    one(
        "a file no longer searched leaves its document",
        SAVED,
        "        return len(await index.delete_where(scope, source))\n",
        "        return 0\n",
        FACADE + "test_dropping_a_chat_or_a_file_removes_the_documents_made_from_them",
    ),
    one(
        "a rebuild keeps the damaged index",
        SAVED,
        "        await asyncio.to_thread(index.quarantine)\n",
        "        pass\n",
        FACADE + "test_an_index_that_is_damaged_is_made_again_from_the_files_and_nothing_is_lost",
    ),
    one(
        "a rebuild brings back a deleted chat's file",
        SAVED,
        "            if meta.scope.startswith(CHAT) and owners.get(meta.scope[len(CHAT) :]) != sub:\n                continue\n",
        "            pass\n",
        FACADE
        + "test_a_rebuild_leaves_out_a_chats_file_whose_chat_is_gone_and_says_how_many_are_left",
    ),
    # --- the routes --------------------------------------------------------------------
    one(
        "a chat's document opens through My documents",
        SAVED_API,
        "    if doc is None or (library and doc.scope != LIBRARY):\n",
        "    if doc is None:\n",
        SCOPES + "test_a_chats_own_document_is_not_reachable_through_the_routes_of_my_documents",
    ),
    one(
        "My documents lists a chat's documents",
        SAVED_API,
        "        documents = await index.documents(LIBRARY)\n",
        "        documents = await index.documents()\n",
        SCOPES + "test_a_chats_own_document_is_not_reachable_through_the_routes_of_my_documents",
    ),
    one(
        "an image is kept as a document",
        SAVED_API,
        "        if not found.is_document:\n",
        "        if False:\n",
        API_T + "test_a_file_workbench_does_not_read_is_refused_on_its_own_while_the_rest_are_kept",
    ),
    one(
        "a person's passage limit is not told",
        SAVED_API,
        "        if index.path.is_file() and await index.passages_total() >= install.max_passages:\n",
        "        if False:\n",
        API_T + "test_a_person_at_the_passage_limit_is_refused_more_files_at_once_with_the_number",
    ),
    one(
        "a file over the size limit is kept",
        SAVED_API,
        "        if written is None:\n            target.unlink(missing_ok=True)\n            refused.append(\n",
        "        if False:\n            target.unlink(missing_ok=True)\n            refused.append(\n",
        API_T
        + "test_a_file_larger_than_what_workbench_reads_is_refused_naming_the_limit_and_leaves_nothing",
    ),
    one(
        "a ZIP that is not a Word file is kept",
        SAVED_API,
        "        if found.zipped and await asyncio.to_thread(files.zip_holds_document, target) is False:\n",
        "        if False:\n",
        API_T + "test_a_zip_that_is_not_a_word_file_is_refused_as_what_it_is",
    ),
    one(
        "a name keeps its folders",
        SAVED_API,
        r'    base = (raw or "").replace("\\", "/").rsplit("/", 1)[-1].strip()' + "\n",
        '    base = (raw or "").strip()\n',
        API_T + "test_a_name_is_the_files_own_without_any_folders_in_front_of_it",
    ),
    one(
        "documents are added while the owner has them off",
        SAVED_API,
        "    install = await config.document_settings(store)\n    if not install.enabled:\n        raise _problem(status.HTTP_403_FORBIDDEN, _OWNER_OFF)\n    if len(uploads) > MAX_FILES:\n",
        "    install = await config.document_settings(store)\n    if len(uploads) > MAX_FILES:\n",
        CHATS + "test_with_documents_off_for_the_install_nothing_is_added_searched_or_sent",
    ),
    one(
        "a document being read has text to show",
        SAVED_API,
        "    if doc.status in PENDING:\n",
        "    if False:\n",
        API_T + "test_a_document_still_being_read_has_no_text_yet_and_says_so",
    ),
    one(
        "a skipped document has text to show",
        SAVED_API,
        "    if doc.status not in FOUND:\n",
        "    if False:\n",
        API_T + "test_a_scan_a_locked_file_and_a_damaged_one_are_skipped_or_failed_in_words",
    ),
    one(
        "a text that has gone is not read again",
        SAVED_API,
        "        with contextlib.suppress(OriginalMissing):\n            await _saved(request).reread(person.sub, doc.id)\n",
        "        pass\n",
        API_T + "test_a_text_that_has_gone_is_read_again_and_says_so",
    ),
    one(
        "an original is shown in the page",
        SAVED_API,
        '        content_disposition_type="attachment",\n',
        '        content_disposition_type="inline",\n',
        API_T + "test_open_original_is_a_download_of_the_file_as_it_was_added",
    ),
    one(
        "another person's chat can be named to Test a question",
        SAVED_API,
        "        chat = await _own_chat(request, person, body.chatId)\n        scopes.append(chat_scope(chat.id))\n",
        "        chat = await _state(request).store.chat(body.chatId)\n        assert chat is not None\n        scopes.append(chat_scope(chat.id))\n",
        SCOPES
        + "test_another_persons_chat_gets_the_same_404_for_the_documents_routes_that_take_a_chat",
    ),
    one(
        "another person's chat's documents switch can be read",
        SAVED_API,
        "    chat, read_only = await _readable_chat(request, person, chat_id)\n    return await chat_documents_view(request, person, chat, model, read_only=read_only)\n",
        "    chat = await _state(request).store.chat(chat_id)\n    assert chat is not None\n    return await chat_documents_view(request, person, chat, model, read_only=False)\n",
        SCOPES
        + "test_another_persons_chat_gets_the_same_404_for_the_documents_routes_that_take_a_chat",
    ),
    one(
        "the owner sees a person's documents in her chat's chip",
        SAVED_API,
        "    if read_only:\n        return {\n",
        "    if False:\n        return {\n",
        SCOPES
        + "test_the_owner_cannot_read_a_persons_documents_even_where_they_may_read_her_chats",
    ),
    one(
        "a search that cannot be fixed says it can be",
        SAVED_API,
        '                "canRebuild": exc.code == "index_damaged",\n',
        '                "canRebuild": False,\n',
        API_T
        + "test_test_a_question_on_an_index_that_cannot_be_searched_is_a_503_and_never_nothing_matched",
    ),
    # --- what a chat is sent ----------------------------------------------------------
    one(
        "passages go to a model outside the machines",
        SAVED_API,
        '    if choice == "always" or (choice == "ask" and chat.settings.get("documentsExternal") is True):\n',
        "    if True:\n",
        CHATS
        + "test_passages_are_not_sent_to_a_model_outside_the_machines_unless_the_person_says_so_for_the_chat",
    ),
    one(
        "a model Eugene cannot place is trusted",
        SAVED_API,
        '    if facts.locality == "local":\n        return Access(True, facts=facts, window=facts.window)\n',
        '    if facts.locality != "external":\n        return Access(True, facts=facts, window=facts.window)\n',
        CHATS + "test_a_model_eugene_cannot_place_is_not_trusted_with_documents",
    ),
    one(
        "a window too small is searched",
        SAVED_API,
        "    if (small := window_words(facts.window)) is not None:\n",
        "    if False:\n",
        CHATS + "test_a_window_too_small_for_documents_skips_them_in_the_words_attached_files_use",
    ),
    one(
        "a search that cannot run is nothing matched",
        SAVED_API,
        '        return done("failed", code=exc.code, message=exc.message)\n',
        '        return done("nothing_matched", message="Nothing in your documents matched.")\n',
        CHATS + "test_a_search_that_cannot_run_says_so_with_its_cause_and_is_never_nothing_matched",
    ),
    one(
        "an unexpected error in a search is nothing matched",
        SAVED_API,
        '        return done(\n            "failed",\n            code="error",\n',
        '        return done(\n            "nothing_matched",\n            code="error",\n',
        CHATS + "test_an_unexpected_error_in_a_search_names_its_class_and_the_log",
    ),
    one(
        "documents only asks the model",
        SAVED_API,
        "    if strict and not sent_whole:\n",
        "    if False:\n",
        CHATS
        + "test_documents_only_answers_in_workbenchs_own_words_when_nothing_matches_and_asks_no_model",
    ),
    one(
        "ask the model anyway is not asked",
        SAVED_API,
        '    return bool(chat.settings.get("documentsOnly")) and not ask_anyway\n',
        '    return bool(chat.settings.get("documentsOnly"))\n',
        CHATS + "test_ask_the_model_anyway_asks_it_without_passages_and_says_it_was_asked_anyway",
    ),
    one(
        "passages are not fitted to the budget of the window",
        SAVED_API,
        "            budget=budget.allowed - block_cost,\n",
        "            budget=None,\n",
        CHATS
        + "test_the_passages_fit_the_lesser_of_3000_tokens_and_30_percent_of_the_models_window",
    ),
    one(
        "documents are searched while the switch is off",
        SAVED_API,
        "        if not switch and not has_chat_docs:\n            return Found()\n",
        "        pass\n",
        CHATS + "test_a_new_chat_has_documents_off_and_nothing_is_searched_or_sent",
    ),
    one(
        "the switch does not decide My documents",
        SAVED_API,
        "        library_ok = switch and access.library\n",
        "        library_ok = access.library\n",
        SCOPES
        + "test_a_chat_with_the_switch_off_searches_only_its_own_files_and_never_my_documents",
    ),
    one(
        "the working indicator is not told",
        SAVED_API,
        '        progress({"stage": "documents", "phase": "searching"})\n',
        "        pass\n",
        CHATS + "test_the_working_indicator_says_it_is_searching_the_documents",
    ),
    one(
        "the suffix is the same every time",
        SAVED_API,
        "        context.new_retrieval_nonce(texts), result.passages, strict=strict\n",
        '        "00000000", result.passages, strict=strict\n',
        CHATS + "test_the_suffix_is_random_for_each_request_and_text_cannot_end_the_block_it_is_in",
    ),
    one(
        "a message with no words is searched",
        SAVED_API,
        '        if query.empty:\n            return done(\n                "skipped",\n                code="no_words",\n',
        '        if False:\n            return done(\n                "skipped",\n                code="no_words",\n',
        CHATS + "test_a_message_with_no_words_to_search_for_says_so_and_is_not_an_error",
    ),
    one(
        "documents still being read are pretended to be searched",
        SAVED_API,
        '        if documents == 0:\n            if waiting:\n                return done("skipped", code="still_reading", message=notes[-1]["message"])\n',
        '        if documents == 0:\n            if False:\n                return done("skipped", code="still_reading", message=notes[-1]["message"])\n',
        CHATS
        + "test_when_every_document_is_still_being_read_the_answer_says_so_and_searches_nothing",
    ),
    one(
        "a follow-up is searched by its own words only",
        SAVED_API,
        "    return users[-2].content if len(users) >= 2 else None\n",
        "    return None\n",
        CHATS
        + "test_passages_are_not_replayed_on_the_next_turn_and_every_turn_is_looked_for_again",
    ),
    # --- the budget and the placement of passages -------------------------------------
    one(
        "retrieval has no ceiling",
        CONTEXT,
        "    return max(0, min(RETRIEVAL_CEILING, window * RETRIEVAL_SHARE_PERCENT // 100))\n",
        "    return max(0, window * RETRIEVAL_SHARE_PERCENT // 100)\n",
        RETRIEVAL
        + "test_found_passages_may_take_the_lesser_of_3000_tokens_and_30_percent_of_the_window",
    ),
    one(
        "retrieval has no share of the window",
        CONTEXT,
        "    return max(0, min(RETRIEVAL_CEILING, window * RETRIEVAL_SHARE_PERCENT // 100))\n",
        "    return max(0, min(RETRIEVAL_CEILING, window))\n",
        RETRIEVAL
        + "test_found_passages_may_take_the_lesser_of_3000_tokens_and_30_percent_of_the_window",
    ),
    one(
        "retrieval does not give way when the room is short",
        CONTEXT,
        "    allowed = limit if room is None else min(limit, room)\n",
        "    allowed = limit\n",
        RETRIEVAL + "test_retrieval_is_what_gives_way_when_the_room_is_short",
    ),
    one(
        "a window nobody knows has no room",
        CONTEXT,
        "    room = None if window is None else max(0, window - used - reserve)\n",
        "    room = 0 if window is None else max(0, window - used - reserve)\n",
        RETRIEVAL + "test_a_window_nobody_knows_has_the_ceiling_and_claims_no_room",
    ),
    one(
        "documents only says the same words",
        CONTEXT,
        "    inner = [RETRIEVED_STRICT if strict else RETRIEVED_INTRO]\n",
        "    inner = [RETRIEVED_INTRO]\n",
        RETRIEVAL + "test_documents_only_changes_the_one_line_to_say_the_passages_are_all_there_is",
    ),
    one(
        "the suffix may be in the text it wraps",
        CONTEXT,
        "        if not any(nonce in text for text in texts):\n            return nonce\n",
        "        if True:\n            return nonce\n",
        RETRIEVAL
        + "test_the_suffix_is_eight_random_hex_digits_for_every_request_and_never_in_what_it_wraps",
    ),
    one(
        "a name can end its attribute",
        CONTEXT,
        '    return one.replace("&", "&amp;").replace(\'"\', "&quot;").replace("<", "&lt;")\n',
        "    return one\n",
        RETRIEVAL + "test_nothing_in_a_name_or_a_heading_can_end_its_attribute_or_open_a_tag",
    ),
    one(
        "a heading has any length in its attribute",
        CONTEXT,
        '    one = " ".join("".join(c if c.isprintable() else " " for c in value).split())[:limit]\n',
        '    one = " ".join("".join(c if c.isprintable() else " " for c in value).split())\n',
        RETRIEVAL + "test_a_name_is_cut_at_120_characters_and_a_heading_at_200_in_the_tag",
    ),
    one(
        "a control character stays in a name",
        CONTEXT,
        '    one = " ".join("".join(c if c.isprintable() else " " for c in value).split())[:limit]\n',
        '    one = " ".join(value.split())[:limit]\n',
        RETRIEVAL + "test_nothing_in_a_name_or_a_heading_can_end_its_attribute_or_open_a_tag",
    ),
    one(
        "a passage's lines are not said",
        CONTEXT,
        "    elif lines is not None:\n        attributes += f' lines=\"{lines}\"'\n",
        "    elif False:\n        attributes += f' lines=\"{lines}\"'\n",
        RETRIEVAL + "test_a_passage_says_its_page_or_its_lines_and_nothing_when_it_has_neither",
    ),
    one(
        "a source's snippet is not cut",
        CONTEXT,
        '        "snippet": snippet(passage.text, passage.terms),\n',
        '        "snippet": passage.text,\n',
        RETRIEVAL + "test_a_sources_snippet_is_at_most_240_characters_and_a_chats_file_says_so",
    ),
    one(
        "a chat's file is called a library file",
        CONTEXT,
        '        "scope": "chat" if passage.scope.startswith("chat:") else "library",\n',
        '        "scope": "library",\n',
        RETRIEVAL + "test_a_sources_snippet_is_at_most_240_characters_and_a_chats_file_says_so",
    ),
    one(
        "what the wrapper costs is nothing",
        CONTEXT,
        "    block = token_count.estimate(render_passages(nonce, [], strict=True)) + 2\n",
        "    block = 0\n",
        RETRIEVAL + "test_what_the_wrapper_costs_is_never_less_than_what_it_is",
    ),
    one(
        "a file chosen to be searched is read whole",
        CONTEXT,
        '    if item.choice is not None and item.choice.mode == "search" and item.state == "ready":\n        return _decide_search(item, base)\n',
        "    if False:\n        return _decide_search(item, base)\n",
        RETRIEVAL + "test_a_file_that_can_be_searched_is_sent_nothing_of_and_is_never_too_long",
    ),
    one(
        "a file not yet searchable is sent",
        CONTEXT,
        '    if status in ("searchable", "embedding", "ready"):\n        base.kind, base.send, base.block_tokens = "search", True, 0\n',
        '    if True:\n        base.kind, base.send, base.block_tokens = "search", True, 0\n',
        RETRIEVAL + "test_a_file_whose_document_is_not_there_yet_holds_the_send_and_says_so",
    ),
    one(
        "a file that could not be made searchable is said to be pending",
        CONTEXT,
        '    elif status in ("skipped", "failed"):\n        base.kind = "unreadable"\n',
        '    elif False:\n        base.kind = "unreadable"\n',
        RETRIEVAL
        + "test_a_file_that_could_not_be_made_searchable_says_why_in_the_words_it_ended_with",
    ),
    one(
        "searching is not offered for a long file",
        CONTEXT,
        '    offers.append({"action": "search"})\n',
        "    pass\n",
        CONTEXTS
        + "test_a_document_longer_than_the_default_is_offered_all_or_the_first_part_and_not_sent",
    ),
    one(
        "a file searched instead makes the chat too full to send",
        CONTEXT,
        '        and any(d.send and not d.native and d.kind != "search" for d in out)\n',
        "        and any(d.send and not d.native for d in out)\n",
        RETRIEVAL + "test_a_file_chosen_to_be_searched_does_not_make_the_chat_too_full_to_send",
    ),
    one(
        "searching a file instead is not kept with the file",
        CONTEXT,
        '        if self.mode in ("whole", "search"):\n            return {"mode": self.mode}\n',
        '        if self.mode in ("whole",):\n            return {"mode": self.mode}\n',
        RETRIEVAL + "test_searching_a_file_instead_is_a_choice_that_is_kept_with_it",
    ),
    one(
        "passages go in every user message",
        CONVERSATION,
        "                        retrieved if message is last_user else None,\n",
        "                        retrieved,\n",
        CHATS
        + "test_passages_are_not_replayed_on_the_next_turn_and_every_turn_is_looked_for_again",
    ),
    one(
        "the question comes before the passages",
        CONVERSATION,
        "    words = [*blocks, message.content] if message.content else blocks\n",
        "    words = [message.content, *blocks] if message.content else blocks\n",
        CHATS
        + "test_the_best_passages_go_in_the_latest_message_before_the_persons_words_cited_by_number",
    ),
    one(
        "passages are dropped when nothing is attached",
        CONVERSATION,
        "    if not attached and not retrieved:\n",
        "    if not attached:\n",
        CHATS
        + "test_the_best_passages_go_in_the_latest_message_before_the_persons_words_cited_by_number",
    ),
    one(
        "the passages are not built into the request",
        API,
        '            retrieved=decided.get("retrieved"),\n',
        "            retrieved=None,\n",
        CHATS
        + "test_the_best_passages_go_in_the_latest_message_before_the_persons_words_cited_by_number",
    ),
    one(
        "the passages are not listed as sources",
        API,
        "            sources=[*prepared.sources, *found.sources],\n",
        "            sources=[*prepared.sources],\n",
        CHATS + "test_the_answer_lists_each_passages_file_page_heading_rank_and_how_it_was_found",
    ),
    one(
        "what searching came to is not kept with the answer",
        ANSWERS,
        '                if prepared.retrieval is not None:\n                    m.retrieval = prepared.retrieval\n                    told["retrieval"] = m.retrieval\n',
        '                if False:\n                    m.retrieval = prepared.retrieval\n                    told["retrieval"] = m.retrieval\n',
        CHATS + "test_what_searching_came_to_is_kept_with_the_answer",
    ),
    one(
        "a number the model made up is a source",
        ANSWERS,
        '        ranks = {s["rank"] for s in passages if isinstance(s.get("rank"), int)}\n        message.retrieval["cited"] = sorted(said & ranks)\n',
        '        message.retrieval["cited"] = sorted(said)\n',
        CHATS + "test_the_numbers_an_answer_cites_mark_which_passages_it_used",
    ),
    one(
        "a cited passage is not marked",
        ANSWERS,
        '        source["cited"] = source.get("rank") in said\n',
        '        source["cited"] = False\n',
        CHATS + "test_the_numbers_an_answer_cites_mark_which_passages_it_used",
    ),
    one(
        "Workbench's own words are a model's",
        ANSWERS,
        "                    m.model, m.finish = None, prepared.finish\n",
        "                    m.finish = prepared.finish\n",
        CHATS
        + "test_documents_only_answers_in_workbenchs_own_words_when_nothing_matches_and_asks_no_model",
    ),
    one(
        "Workbench's own words still ask the model",
        ANSWERS,
        '                if prepared.reply is not None:\n                    m.status = "done"\n                    return\n',
        '                if prepared.reply is not None:\n                    m.status = "done"\n',
        CHATS
        + "test_documents_only_answers_in_workbenchs_own_words_when_nothing_matches_and_asks_no_model",
    ),
    one(
        "the owner reads a person's passages",
        API,
        "            out.append(hide_saved_documents(view))\n            continue\n",
        "            out.append(view)\n            continue\n",
        SCOPES
        + "test_the_owner_cannot_read_a_persons_documents_even_where_they_may_read_her_chats",
    ),
    one(
        "the owner reads what was found for her",
        API,
        '        retrieval = {**retrieval, "cited": [], "searched": None, "budget": None}\n',
        "        retrieval = {**retrieval}\n",
        SCOPES
        + "test_the_owner_cannot_read_a_persons_documents_even_where_they_may_read_her_chats",
    ),
    one(
        "a deleted chat's documents stay",
        API,
        "        await _state(request).saved.drop_chat(person.sub, chat.id)\n",
        "        pass\n",
        SCOPES + "test_a_chats_documents_die_with_the_chat_and_leave_nothing_on_the_disk",
    ),
    one(
        "a removed upload's document stays",
        API,
        "        await _state(request).saved.drop_attachment(person.sub, chat.id, record.id)\n",
        "        pass\n",
        SCOPES + "test_removing_a_file_that_was_not_sent_removes_the_document_made_from_it",
    ),
    one(
        "documents are on in every new chat",
        API,
        '        settings={"documents": "on"} if prefs["new_chats"] else {},\n',
        '        settings={"documents": "on"},\n',
        CHATS + "test_a_new_chat_has_documents_off_and_nothing_is_searched_or_sent",
    ),
    one(
        "a new chat ignores what the person said about new chats",
        API,
        '        settings={"documents": "on"} if prefs["new_chats"] else {},\n',
        "        settings={},\n",
        CHATS + "test_a_new_chat_starts_with_documents_on_only_if_the_person_said_so_for_new_chats",
    ),
    one(
        "ask the model anyway is forgotten on a retry",
        API,
        '        ask_anyway=ask_anyway,\n    )\n    return {"answer": message_view(answer)}\n',
        '        ask_anyway=False,\n    )\n    return {"answer": message_view(answer)}\n',
        CHATS + "test_ask_the_model_anyway_asks_it_without_passages_and_says_it_was_asked_anyway",
    ),
    # --- a file chosen to be searched instead -----------------------------------------
    one(
        "a file chosen to be searched is not made searchable",
        DOCS_API,
        "            await _make_searchable(request, chat, record)\n        except IndexUnavailable as exc:\n            raise _problem(status.HTTP_503_SERVICE_UNAVAILABLE, exc.message) from None\n",
        "            pass\n        except IndexUnavailable as exc:\n            raise _problem(status.HTTP_503_SERVICE_UNAVAILABLE, exc.message) from None\n",
        SCOPES
        + "test_a_file_too_long_for_the_window_can_be_searched_instead_and_nothing_of_it_is_read_whole",
    ),
    one(
        "searching is chosen while the owner has documents off",
        DOCS_API,
        '        if not (await config.document_settings(store)).enabled:\n            raise _problem(\n                status.HTTP_403_FORBIDDEN, "The owner of this Workbench has turned off documents."\n            )\n',
        "        pass\n",
        SCOPES
        + "test_choosing_search_is_refused_while_the_owner_has_documents_off_and_for_a_file_not_read",
    ),
    one(
        "taking back the choice leaves the document",
        DOCS_API,
        '            await _state(request).saved.drop_attachment(person.sub, chat.id, record.id)\n        item["search"] = None\n',
        '            pass\n        item["search"] = None\n',
        SCOPES
        + "test_taking_back_the_choice_or_choosing_another_way_removes_the_document_made_from_the_file",
    ),
    one(
        "a send does not wait for the file to be searchable",
        DOCS_API,
        "            await _state(request).saved.ingest.wait(chat.owner, waiting, _wait_seconds())\n",
        "            pass\n",
        SCOPES
        + "test_a_send_waits_for_the_file_to_be_searchable_and_does_not_fail_for_being_early",
    ),
    one(
        "a file searched instead is listed as sent whole",
        DOCS_API,
        '        if decision.kind == "search":\n            continue  # not sent whole: its passages are what is sent, and they are listed\n',
        "        if False:\n            continue\n",
        SCOPES
        + "test_a_file_too_long_for_the_window_can_be_searched_instead_and_nothing_of_it_is_read_whole",
    ),
    # --- the person's settings --------------------------------------------------------
    one(
        "a person's setting is every person's",
        STORE,
        '                        f"UPDATE documents_prefs SET {columns} WHERE owner = ?", (*params, owner)\n',
        '                        f"UPDATE documents_prefs SET {columns}", (*params,)\n',
        STORES + "test_a_persons_documents_settings_are_changed_one_key_at_a_time_and_nobody_elses",
    ),
    one(
        "changing one setting resets the others",
        STORE,
        '                db.execute("INSERT OR IGNORE INTO documents_prefs (owner) VALUES (?)", (owner,))\n',
        '                db.execute("INSERT OR REPLACE INTO documents_prefs (owner) VALUES (?)", (owner,))\n',
        STORES + "test_a_persons_documents_settings_are_changed_one_key_at_a_time_and_nobody_elses",
    ),
    one(
        "what searching came to is not read back",
        STORE,
        '        retrieval=_snapshot(row["retrieval"]),\n',
        "        retrieval=None,\n",
        STORES + "test_a_messages_retrieval_is_kept_and_read_back_and_none_is_none",
    ),
    one(
        "the migration forgets the retrieval column",
        STORE,
        '        "ALTER TABLE messages ADD COLUMN retrieval TEXT",\n        "CREATE TABLE IF NOT EXISTS documents_prefs (owner TEXT PRIMARY KEY, "',
        '        "SELECT 1",\n        "CREATE TABLE IF NOT EXISTS documents_prefs (owner TEXT PRIMARY KEY, "',
        STORES + "test_a_schema_11_store_gains_retrieval_and_the_prefs_table_and_keeps_every_chat",
    ),
    one(
        "the boot is told the wrong chats exist",
        STORE,
        '                    f"SELECT id, owner FROM chats WHERE id IN ({marks})", part\n',
        '                    f"SELECT id, owner FROM chats WHERE id NOT IN ({marks})", part\n',
        STORES + "test_the_boot_can_ask_whose_each_of_these_chats_is_and_which_are_gone",
    ),
    one(
        "the passage limit takes any value",
        CONFIG,
        "            and MIN_PASSAGES_LIMIT <= value <= MAX_PASSAGES_LIMIT\n",
        "            and True\n",
        CHATS + "test_the_new_settings_say_what_they_do_and_refuse_what_they_cannot_take",
    ),
    one(
        "the owner's choice for passages is not a setting",
        CONFIG,
        "    if key in (MEMORY_EXTERNAL_MODELS, DOCUMENTS_EXTERNAL_MODELS):\n",
        "    if key in (MEMORY_EXTERNAL_MODELS,):\n",
        CHATS + "test_the_new_settings_say_what_they_do_and_refuse_what_they_cannot_take",
    ),
]

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}


def run(check: str) -> tuple[bool, str]:
    """(passed, output) for one check."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", check],
            cwd=ROOT,
            env=ENV,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=400,
        )
    except subprocess.TimeoutExpired:
        return False, "the check did not finish in 400 seconds"
    return result.returncode == 0, result.stdout + result.stderr


def run_all(checks: list[str]) -> tuple[bool, str]:
    """Every check, a few at a time in one pytest process each: the baseline before the
    cases and the baseline after them, which are one run of each check and not one
    process for each. (passed, output of the first group that failed)."""
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
    for label, name, edits, _ in CASES:
        text = (ROOT / name).read_text(encoding="utf-8")
        for before, _after in edits:
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
    checks = sorted({check for *_, check in cases})
    passed, output = run_all(checks)
    if not passed:
        print(output[-3000:])
        raise SystemExit("Baseline failed; no files changed.")
    print(f"Baseline: {len(checks)} checks pass.", flush=True)
    backup = Path(tempfile.mkdtemp(prefix="workbench-library-sabotage-"))
    print(f"Exact backups: {backup}", flush=True)
    caught = 0
    escaped: list[str] = []
    for index, (label, name, edits, check) in enumerate(cases):
        path = ROOT / name
        original = path.read_bytes()
        (backup / f"{index}-{path.name}").write_bytes(original)
        try:
            text = original.decode("utf-8")
            for before, after in edits:
                text = text.replace(before, after)
            path.write_bytes(text.encode("utf-8"))
            passed, output = run(check)
            ok = not passed and not broken_build(output)
            print(f"{'CAUGHT' if ok else 'ESCAPED'}: {label}", flush=True)
            if not ok:
                print(output[-3000:])
                escaped.append(label)
            caught += ok
        finally:
            path.write_bytes(original)
            assert path.read_bytes() == original
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
