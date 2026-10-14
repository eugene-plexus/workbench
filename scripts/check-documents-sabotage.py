"""Prove the document checks are observed (MR2, documents in the prompt).

Each case breaks one thing in the reader, the rules, the routes or the store, and
its named check must fail. Files are restored from exact bytes kept beside the run,
never from Git. A mutation that stops the code importing or compiling does not
count as a caught defect.

  python scripts/check-documents-sabotage.py            # every case
  python scripts/check-documents-sabotage.py --anchors  # only check each anchor is unique
  python scripts/check-documents-sabotage.py --labels hung,cancel   # cases whose label has these words

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
EXTRACT = SRC + "knowledge/extract.py"
READER = SRC + "knowledge/reader.py"
WORKER = SRC + "knowledge/worker.py"
FILES = SRC + "files.py"
CONTEXT = SRC + "context.py"
CONVERSATION = SRC + "conversation.py"
DOCS_API = SRC + "documents_api.py"
API = SRC + "api.py"
ANSWERS = SRC + "answers.py"
STORE = SRC + "store.py"
T_EXTRACT = "tests/test_extract.py"
T_CONTEXT = "tests/test_context.py"
T_READER = "tests/test_reader.py"
T_API = "tests/test_documents_api.py"
T_STORE = "tests/test_documents_store.py"

# (label, file, [(before, after), ...], check)
CASES: list[tuple[str, str, list[tuple[str, str]], str]] = [
    # --- what a file is, by its bytes ------------------------------------------------
    (
        "a type the browser claims is believed over the bytes",
        FILES,
        [("    if claimed in _CLAIMS and claimed != found.media_type:", "    if False:")],
        f"{T_EXTRACT}::test_a_type_the_name_lies_about_is_what_the_bytes_are",
    ),
    (
        "the first bytes of a PDF are not looked at",
        FILES,
        [
            (
                '    if head.startswith(b"%PDF-"):\n        return Detected(PDF, "pdf")',
                '    if head.startswith(b"%PDF-") and False:\n        return Detected(PDF, "pdf")',
            )
        ],
        f"{T_EXTRACT}::test_a_files_type_is_what_its_bytes_say",
    ),
    (
        "UTF-16 text is taken for an MPEG frame",
        FILES,
        [
            (
                "    marked = head.startswith((codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))",
                "    marked = False",
            )
        ],
        f"{T_EXTRACT}::test_utf16_text_with_a_byte_order_mark_is_text_and_not_an_mpeg_frame",
    ),
    (
        "a file with NUL bytes is read as text",
        FILES,
        [
            (
                '    if b"\\x00" in head:\n        return True',
                '    if b"\\x00" in head:\n        return False',
            )
        ],
        f"{T_EXTRACT}::test_a_binary_file_is_named_with_its_extension",
    ),
    (
        "any ZIP is taken for a Word file",
        FILES,
        [
            (
                "    else:\n        return None\n    return False\n",
                "    else:\n        return None\n    return True\n",
            )
        ],
        f"{T_EXTRACT}::test_a_zip_is_told_from_a_word_file_by_its_directory_and_never_opened",
    ),
    (
        "a PDF is counted against what one chat carries",
        FILES,
        [
            (
                '    return KINDS.get(media_type) in ("image", "audio")',
                "    return media_type in KINDS",
            )
        ],
        f"{T_API}::test_the_limits_are_said_before_an_upload_and_a_pdf_is_not_counted_against_the_chats_carrying",
    ),
    # --- reading ---------------------------------------------------------------------
    (
        "a bidirectional override stays in the text",
        EXTRACT,
        [(r'"\U0000202a-\U0000202e\U0000feff\U0000fff9', r'"\U0000feff\U0000fff9')],
        f"{T_EXTRACT}::test_control_and_invisible_characters_are_taken_out_and_counted",
    ),
    (
        "a lone surrogate reaches the request",
        EXTRACT,
        [(r'    text = _SURROGATE.sub("\U0000fffd", text)', "    text = text")],
        f"{T_EXTRACT}::test_a_lone_surrogate_becomes_the_replacement_character",
    ),
    (
        "a PDF page is not marked",
        EXTRACT,
        [
            (
                '    return f"{marker}\\n{body}" if body else f"{marker} {NO_TEXT_ON_PAGE}"',
                "    return body",
            )
        ],
        f"{T_EXTRACT}::test_a_text_pdf_is_read_a_page_at_a_time_with_the_code_word_on_page_three",
    ),
    (
        "a PDF of pictures is read as if it had text",
        EXTRACT,
        [("    if letters == 0 or letters < MIN_CHARS_PER_PAGE * pages_read:", "    if False:")],
        f"{T_EXTRACT}::test_a_pdf_of_pictures_is_skipped_with_the_reason_and_its_page_count",
    ),
    (
        "a locked PDF is not named as locked",
        EXTRACT,
        [
            ('        if getattr(exc, "err_code", None) == _PDFIUM_PASSWORD:', "        if False:"),
            ('        if reader.is_encrypted and not reader.decrypt(""):', "        if False:"),
        ],
        f"{T_EXTRACT}::test_a_locked_pdf_is_skipped_as_password_protected",
    ),
    (
        "a damaged PDF does not say what PDFium observed",
        EXTRACT,
        [
            (
                "        raise _Refused(_pdfium_words(exc)) from exc",
                '        raise _Refused("") from exc',
            )
        ],
        f"{T_EXTRACT}::test_a_damaged_pdf_fails_with_the_cause_each_library_observed",
    ),
    (
        "a page's script is read as words",
        EXTRACT,
        [
            (
                '    {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object"}',
                '    {"style", "noscript", "template", "svg", "canvas", "iframe", "object"}',
            )
        ],
        f"{T_EXTRACT}::test_html_is_its_text_with_no_script_style_comment_or_hidden_words",
    ),
    (
        "a hidden element is read",
        EXTRACT,
        [
            (
                "        if tag in _SKIPPED or (_hidden(attrs) and tag not in _VOID):",
                "        if tag in _SKIPPED:",
            )
        ],
        f"{T_EXTRACT}::test_html_keeps_entities_preformatted_text_and_nesting",
    ),
    (
        "a heading in a code fence is a heading",
        EXTRACT,
        [
            (
                "        if fence is not None:\n            continue\n        atx = _ATX.match(line)",
                "        atx = _ATX.match(line)",
            )
        ],
        f"{T_EXTRACT}::test_markdown_is_its_own_text_with_its_headings_outside_the_fences",
    ),
    (
        "a text that is not UTF-8 is read without saying so",
        EXTRACT,
        [
            (
                '    notes.append("This file is not UTF-8, so Workbench read it as Windows-1252 text.")',
                "    pass",
            )
        ],
        f"{T_EXTRACT}::test_text_is_decoded_as_utf8_utf16_or_windows_1252_and_says_so",
    ),
    (
        "a text cut at the cap does not say so",
        EXTRACT,
        [
            (
                "    truncated = len(data) < size\n    text, notes = decode(data, complete=not truncated)\n    text, removed = clean(text, layout=False)",
                "    truncated = False\n    text, notes = decode(data, complete=not truncated)\n    text, removed = clean(text, layout=False)",
            )
        ],
        f"{T_EXTRACT}::test_a_text_file_past_the_cap_is_read_that_far_and_says_so",
    ),
    (
        "a Word file that expands a thousandfold is opened",
        EXTRACT,
        [("    if total > MAX_UNCOMPRESSED:", "    if False:")],
        f"{T_EXTRACT}::test_a_word_file_that_expands_past_what_is_opened_is_refused_unopened",
    ),
    (
        "a Word heading is read as a paragraph",
        EXTRACT,
        [('            block("#" * level + " " + text)', "            block(text)")],
        f"{T_EXTRACT}::test_a_word_file_keeps_its_headings_lists_and_table_rows",
    ),
    # --- the child process -----------------------------------------------------------
    (
        "the worker does not set its memory cap",
        WORKER,
        [("    limit_memory(MEMORY_CAP)\n", "")],
        f"{T_EXTRACT}::test_the_worker_names_a_file_that_needs_more_memory_than_a_reader_is_given",
    ),
    (
        "a reader that hangs is left running",
        READER,
        [
            (
                "                timed_out = True\n                kill_tree(child)\n",
                "                timed_out = True\n",
            )
        ],
        f"{T_READER}::test_a_reader_that_hangs_is_ended_with_its_whole_tree_and_the_file_says_so",
    ),
    (
        "a reader inherits Workbench's environment",
        READER,
        [
            (
                "    env = {key: os.environ[key] for key in _KEEP if key in os.environ}",
                "    env = dict(os.environ)",
            )
        ],
        f"{T_READER}::test_a_reader_holds_none_of_workbenchs_secrets_and_works_in_a_directory_of_its_own",
    ),
    (
        "a reader works in Workbench's own directory",
        "src/eugene_plexus_workbench/knowledge/reader.py",
        [
            (
                "            child = self._start_child(key, argv, work)",
                "            child = self._start_child(key, argv, Path.cwd())",
            )
        ],
        "tests/test_reader.py::test_a_reader_holds_none_of_workbenchs_secrets_and_works_in_a_directory_of_its_own",
    ),
    (
        "a crash is not named",
        READER,
        [
            (
                '    return f"{what}; it said: {extract.say(last, 120)}" if last else what',
                '    return ""',
            )
        ],
        f"{T_READER}::test_a_reader_that_dies_leaves_the_cause_it_died_of",
    ),
    (
        "text is left behind for a file removed while it was read",
        READER,
        [
            (
                "                files.read_path_of(self._root, record.owner, record.id).unlink(missing_ok=True)\n                return",
                "                return",
            )
        ],
        f"{T_READER}::test_a_file_whose_row_goes_before_its_reading_ends_leaves_no_text_behind",
    ),
    (
        "cancelling waits out the reader",
        READER,
        [
            (
                "            if child is not None:\n                kill_tree(child)\n        tasks = ",
                "            if child is not None:\n                pass\n        tasks = ",
            )
        ],
        f"{T_READER}::test_a_file_removed_while_it_is_read_ends_its_reader_at_once_and_leaves_nothing",
    ),
    (
        "shutting down leaves the readers running",
        READER,
        [
            (
                "        for child in list(self._children.values()):\n            kill_tree(child)\n        for task in",
                "        for task in",
            )
        ],
        f"{T_READER}::test_shutting_down_ends_every_reader",
    ),
    (
        "every file is read at once",
        READER,
        [
            (
                "        self._slots = asyncio.Semaphore(concurrency)",
                "        self._slots = asyncio.Semaphore(100)",
            )
        ],
        f"{T_READER}::test_only_a_few_files_are_read_at_once",
    ),
    (
        "files a stop left part-way are not read again",
        READER,
        [("        found = await self._store.files_reading()", "        found = []")],
        f"{T_READER}::test_files_a_stop_left_part_way_are_read_again_at_boot_and_the_rest_left_alone",
    ),
    (
        "a bug in Workbench leaves a chip reading for ever",
        READER,
        [
            (
                "        except Exception as exc:\n            # A bug here must not leave",
                "        except ZeroDivisionError as exc:\n            # A bug here must not leave",
            )
        ],
        f"{T_READER}::test_a_bug_in_workbench_itself_ends_in_a_failed_file_and_not_a_chip_that_reads_for_ever",
    ),
    # --- the budget ------------------------------------------------------------------
    (
        "a document is read whole up to half the window",
        CONTEXT,
        [("WHOLE_SHARE_PERCENT = 25", "WHOLE_SHARE_PERCENT = 50")],
        f"{T_CONTEXT}::test_a_document_is_read_whole_by_default_up_to_the_lesser_of_6000_and_a_quarter_of_the_window",
    ),
    (
        "read it all anyway may take everything that remains",
        CONTEXT,
        [("READ_ALL_SHARE_PERCENT = 70", "READ_ALL_SHARE_PERCENT = 100")],
        f"{T_CONTEXT}::test_read_it_all_anyway_may_take_seventy_percent_of_what_remains",
    ),
    (
        "the whole-document ceiling is ten times higher",
        CONTEXT,
        [("WHOLE_CEILING = 6_000", "WHOLE_CEILING = 60_000")],
        f"{T_CONTEXT}::test_a_document_is_read_whole_by_default_up_to_the_lesser_of_6000_and_a_quarter_of_the_window",
    ),
    (
        "a choice that asks for more than the room is followed",
        CONTEXT,
        [
            (
                "            if cut is not None and (read_all is None or cut.tokens + overhead <= read_all):\n                return sendable(cut",
                "            if cut is not None:\n                return sendable(cut",
            )
        ],
        f"{T_CONTEXT}::test_a_choice_that_asks_for_more_than_the_room_is_not_followed",
    ),
    (
        "a document attached earlier is asked about again",
        CONTEXT,
        [
            (
                "    if not item.current:\n        if choice is not None",
                "    if False:\n        if choice is not None",
            )
        ],
        f"{T_CONTEXT}::test_a_document_attached_earlier_is_replayed_as_it_was_and_never_asked_about_again",
    ),
    (
        "a document too long for the window is sent cut without being asked",
        CONTEXT,
        [
            (
                '    base.offers = offers\n    if fits_all:\n        base.kind = "choose"',
                '    if part is not None and not part.whole:\n        return sendable(part, "partial", "")\n    base.offers = offers\n    if fits_all:\n        base.kind = "choose"',
            )
        ],
        f"{T_CONTEXT}::test_a_document_too_long_for_the_window_is_offered_the_first_part_that_fits_the_room",
    ),
    (
        "a PDF larger than the gateway carries is still sent as the file",
        CONTEXT,
        [('        if item.size > files.LIMITS["file"]:', "        if False:")],
        f"{T_CONTEXT}::test_a_pdf_larger_than_the_gateway_carries_is_read_as_text_instead_and_says_so",
    ),
    (
        "every model gets the PDF as a file",
        CONTEXT,
        [("    if item.is_pdf and model.file_input:", "    if item.is_pdf:")],
        f"{T_CONTEXT}::test_only_a_pdf_goes_as_the_file_and_only_to_a_model_that_takes_one",
    ),
    (
        "a window of 2,048 is enough for documents",
        CONTEXT,
        [("MIN_WINDOW = 4_096", "MIN_WINDOW = 1_024")],
        f"{T_CONTEXT}::test_a_window_too_small_for_documents_says_so",
    ),
    (
        "no room is kept for the answer",
        CONTEXT,
        [("ANSWER_RESERVE = 1_024", "ANSWER_RESERVE = 0")],
        f"{T_CONTEXT}::test_a_request_keeps_1024_tokens_for_the_answer_unless_told_otherwise",
    ),
    (
        "a chat with no maximum tokens keeps no room for the answer",
        DOCS_API,
        [
            (
                "        reserve=int(wanted) if isinstance(wanted, int) and wanted > 0 else context.ANSWER_RESERVE,",
                "        reserve=int(wanted) if isinstance(wanted, int) and wanted > 0 else 0,",
            )
        ],
        f"{T_API}::test_a_chat_with_no_maximum_tokens_keeps_the_default_room_for_the_answer",
    ),
    (
        "a cut ends inside a line when a line would fit",
        CONTEXT,
        [
            (
                "            if at == 0:\n                return _cut_line(text[:end], limit)\n            break",
                "            return _cut_line(text[:end], limit)",
            )
        ],
        f"{T_CONTEXT}::test_a_cut_is_at_most_the_limit_ends_on_a_line_and_takes_every_line_that_fits",
    ),
    (
        "a cut takes a page too many",
        CONTEXT,
        [
            (
                "        if used + size > limit:\n            return index\n",
                "        if used + size > limit:\n            return index + 1\n",
            )
        ],
        f"{T_CONTEXT}::test_a_pdf_is_cut_at_a_page_and_says_which",
    ),
    (
        "a picture costs nothing in the request",
        CONTEXT,
        [
            (
                "                else:\n                    total += MEDIA_TOKENS",
                "                else:\n                    total += 0",
            )
        ],
        f"{T_CONTEXT}::test_a_requests_tokens_are_its_words_its_tool_calls_and_a_flat_cost_for_each_media_part",
    ),
    (
        "a file name is not escaped",
        CONTEXT,
        [('    return html.escape(one or "file", quote=True)', '    return one or "file"')],
        f"{T_CONTEXT}::test_a_file_name_cannot_break_out_of_its_attribute_or_its_line",
    ),
    (
        "the delimiter's suffix is always the same",
        CONTEXT,
        [("    return secrets.token_hex(4)", '    return "00000000"')],
        f"{T_CONTEXT}::test_a_suffix_is_eight_random_hex_digits_that_nothing_about_the_file_makes",
    ),
    (
        "a document's source names no pages",
        CONTEXT,
        [('        entry["page_start"], entry["page_end"] = cut.pages', "        pass")],
        f"{T_CONTEXT}::test_the_source_of_a_whole_pdf_names_the_file_and_the_pages_sent",
    ),
    # --- the request -----------------------------------------------------------------
    (
        "the person's words come before the document",
        CONVERSATION,
        [
            (
                "    words = [*blocks, message.content] if message.content else blocks",
                "    words = [message.content, *blocks] if message.content else blocks",
            )
        ],
        f"{T_API}::test_a_pdf_reaches_any_model_as_text_in_the_message_it_was_attached_to",
    ),
    (
        "the document's text is left out of the message",
        CONVERSATION,
        [
            (
                "            elif sent.text:\n                blocks.append(sent.text)",
                "            elif sent.text:\n                pass",
            )
        ],
        f"{T_API}::test_a_pdf_reaches_any_model_as_text_in_the_message_it_was_attached_to",
    ),
    (
        "a document is not replayed on later turns",
        DOCS_API,
        [
            (
                "            if record is None or not _is_document(record):\n                continue\n            out.append(",
                "            if record is None or not _is_document(record) or message is not last:\n                continue\n            out.append(",
            )
        ],
        f"{T_API}::test_the_document_is_replayed_every_turn_in_the_same_bytes",
    ),
    (
        "the delimiter's suffix is new for every request",
        CONTEXT,
        [
            (
                "            base.text = render_block(\n                item.nonce,",
                "            base.text = render_block(\n                new_nonce(),",
            )
        ],
        f"{T_API}::test_the_document_is_replayed_every_turn_in_the_same_bytes",
    ),
    (
        "a send is not refused when its documents cannot go",
        DOCS_API,
        [
            (
                "    if blocked is not None:\n        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=blocked.detail)",
                "    if blocked is not None:\n        pass",
            )
        ],
        f"{T_API}::test_a_document_too_long_for_the_window_is_read_in_part_only_when_the_person_chooses",
    ),
    (
        "a send does not wait for its files to be read",
        DOCS_API,
        [
            (
                "            await reader.wait([r.id for r in need], _wait_seconds())",
                "            pass",
            )
        ],
        f"{T_API}::test_a_send_waits_for_its_files_to_be_read_and_the_other_chats_do_not_wait",
    ),
    (
        "the person's choice is not kept",
        DOCS_API,
        [
            (
                '    await store.merge_file_reading(record.id, {"choice": stored}, chat_id=chat.id)',
                "    pass",
            )
        ],
        f"{T_API}::test_the_choice_is_kept_with_the_file_through_a_restart",
    ),
    (
        "a refused send freezes the person's notes",
        DOCS_API,
        [
            (
                "    notes = await memory_api.block_for(request, person, chat, model_id, freeze=False)\n    blocked",
                "    notes = await memory_api.block_for(request, person, chat, model_id, freeze=True)\n    blocked",
            )
        ],
        f"{T_API}::test_a_send_that_is_refused_freezes_no_notes_and_counts_none_as_used",
    ),
    # --- whose file ------------------------------------------------------------------
    (
        "another person reads the text read from a file",
        DOCS_API,
        [
            (
                "    await _may_read_file(request, person, record)\n    reading = record.reading",
                "    reading = record.reading",
            )
        ],
        f"{T_API}::test_another_person_gets_a_404_for_everything_of_a_file_that_is_not_theirs",
    ),
    (
        "a choice reaches a file that is in another chat",
        DOCS_API,
        [
            (
                "    if record is None or record.chat_id != chat.id or record.owner != person.sub:",
                "    if record is None:",
            )
        ],
        f"{T_API}::test_another_person_gets_a_404_for_everything_of_a_file_that_is_not_theirs",
    ),
    (
        "a file's owner is told it belongs to someone's chat",
        "src/eugene_plexus_workbench/api.py",
        [
            (
                '            raise _problem(status.HTTP_404_NOT_FOUND, "There is no such file.") from None\n        if chat.owner == record.owner and not (',
                "            raise\n        if chat.owner == record.owner and not (",
            )
        ],
        "tests/test_documents_api.py::test_another_person_gets_a_404_for_everything_of_a_file_that_is_not_theirs",
    ),
    # --- taking a file away ----------------------------------------------------------
    (
        "a deleted chat leaves its files' text",
        API,
        [
            (
                "            files.remove_stored(root, record.owner, record.id)",
                "            files.path_of(root, record.owner, record.id).unlink(missing_ok=True)",
            )
        ],
        f"{T_API}::test_deleting_a_chat_removes_every_file_and_every_text_it_held",
    ),
    (
        "a removed upload leaves its text",
        API,
        [
            (
                "        files.remove_stored, _state(request).settings.data_dir, record.owner, record.id",
                "        files.path_of(_state(request).settings.data_dir, record.owner, record.id).unlink, True",
            )
        ],
        f"{T_API}::test_removing_an_unsent_file_removes_its_text_too",
    ),
    # --- what an answer lists --------------------------------------------------------
    (
        "a search's citation has no kind",
        "src/eugene_plexus_workbench/answers.py",
        [
            (
                'source = {"kind": "web", "url": url, "title": citation.get("title") or url}',
                'source = {"url": url, "title": citation.get("title") or url}',
            )
        ],
        "tests/test_documents_api.py::test_documents_come_first_and_a_searchs_citations_after_with_their_kind",
    ),
    # --- the owner's switches --------------------------------------------------------
    (
        "reading is not turned off when the owner says so",
        API,
        [("    reads = found.is_document and settings.enabled", "    reads = found.is_document")],
        f"{T_API}::test_with_reading_turned_off_workbench_is_as_it_was_before_documents",
    ),
    (
        "the largest file is not the owner's setting",
        API,
        [
            (
                '    limit = files.upload_limit(found, settings.max_bytes if reads else files.LIMITS["file"])',
                '    limit = files.upload_limit(found, files.DOCUMENT_LIMIT if reads else files.LIMITS["file"])',
            )
        ],
        f"{T_API}::test_a_file_larger_than_what_workbench_reads_is_refused_naming_the_limit_and_leaves_nothing",
    ),
    # --- schema 11 -------------------------------------------------------------------
    (
        "the migration leaves a search's citation without its kind",
        STORE,
        [
            (
                '            {"kind": "web", **s} if isinstance(s, dict) and "kind" not in s else s for s in found',
                "            s for s in found",
            )
        ],
        f"{T_STORE}::test_a_schema_10_file_gains_reading_and_every_source_a_kind_and_keeps_every_chat",
    ),
    (
        "the migration does not add the reading column",
        STORE,
        [
            (
                '        "ALTER TABLE files ADD COLUMN reading TEXT",\n        _sources_get_kinds,',
                "        _sources_get_kinds,",
            )
        ],
        f"{T_STORE}::test_a_schema_10_file_gains_reading_and_every_source_a_kind_and_keeps_every_chat",
    ),
    (
        "a change to a reading reaches another chat's file",
        STORE,
        [
            (
                '                    sql, params = sql + " AND chat_id = ?", [file_id, chat_id]',
                "                    pass",
            )
        ],
        f"{T_STORE}::test_a_change_to_a_reading_reaches_only_the_file_and_the_chat_it_names",
    ),
    (
        "a text that has gone is not read again",
        DOCS_API,
        [("            await reader.start(again)", "            pass")],
        f"{T_API}::test_a_text_gone_or_damaged_beside_its_file_is_read_again_and_the_answer_goes_on",
    ),
    (
        "a text that has gone is not waited for",
        DOCS_API,
        [("        await reader.wait(ids, _wait_seconds())", "        pass")],
        f"{T_API}::test_a_text_gone_or_damaged_beside_its_file_is_read_again_and_the_answer_goes_on",
    ),
    (
        "a damaged text is not told from a missing one",
        DOCS_API,
        [
            (
                "            except (OSError, ValueError) as exc:\n"
                "                raise context.TextMissing(record.id, record.name) from exc",
                "            except OSError as exc:\n"
                "                raise context.TextMissing(record.id, record.name) from exc",
            )
        ],
        f"{T_API}::test_a_text_gone_or_damaged_beside_its_file_is_read_again_and_the_answer_goes_on",
    ),
    (
        "a text that stays missing is not named",
        DOCS_API,
        [
            (
                "        if wait and (still := next((d for d in plan.decisions if d.repair), None)):",
                "        if False and (still := next((d for d in plan.decisions if d.repair), None)):",
            )
        ],
        f"{T_API}::test_a_text_that_stays_missing_after_it_is_read_again_is_named_and_the_chat_goes_on",
    ),
    (
        "the list of files does not say a text is being read again",
        DOCS_API,
        [
            (
                "        records = [fresh.get(r.id, r) for r in records]\n",
                "",
            )
        ],
        f"{T_API}::test_the_list_of_files_says_a_file_is_being_read_again_when_its_text_has_gone",
    ),
    (
        "a text that cannot be loaded is not a decision to read again",
        CONTEXT,
        [("                repair=True,\n", "                repair=False,\n")],
        f"{T_CONTEXT}::test_a_text_that_cannot_be_loaded_is_a_decision_to_read_the_file_again_and_only_for_it",
    ),
    (
        "a text that cannot be loaded stops every document after it",
        CONTEXT,
        [
            (
                "            decision = Decision(\n                id=item.id,\n                name=item.name,\n"
                '                kind="pending",',
                "            raise\n            decision = Decision(\n                id=item.id,\n"
                '                name=item.name,\n                kind="pending",',
            )
        ],
        f"{T_CONTEXT}::test_a_text_that_cannot_be_loaded_is_a_decision_to_read_the_file_again_and_only_for_it",
    ),
    (
        "a chat's maximum tokens is not the room kept for the answer",
        DOCS_API,
        [
            (
                "        reserve=int(wanted) if isinstance(wanted, int) and wanted > 0 else context.ANSWER_RESERVE,",
                "        reserve=context.ANSWER_RESERVE,",
            )
        ],
        f"{T_API}::test_a_chats_maximum_tokens_is_the_room_kept_for_the_answer",
    ),
    (
        "the worker has no deadline of its own",
        WORKER,
        [("    faulthandler.dump_traceback_later(DEADLINE_SECONDS, exit=True)\n", "    pass\n")],
        f"{T_READER}::test_a_worker_whose_workbench_is_gone_ends_itself_past_its_own_deadline",
    ),
    (
        "the worker's deadline outlives its work",
        WORKER,
        [("    faulthandler.cancel_dump_traceback_later()\n", "")],
        f"{T_READER}::test_a_worker_that_finishes_in_time_is_not_ended_by_its_deadline",
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
    for check in checks:
        passed, output = run(check)
        if not passed:
            print(output[-3000:])
            raise SystemExit(f"Baseline failed ({check}); no files changed.")
    print(f"Baseline: {len(checks)} checks pass.", flush=True)
    backup = Path(tempfile.mkdtemp(prefix="workbench-documents-sabotage-"))
    print(f"Exact backups: {backup}", flush=True)
    caught = 0
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
            caught += ok
        finally:
            path.write_bytes(original)
            assert path.read_bytes() == original
    for check in checks:
        passed, output = run(check)
        if not passed:
            print(output[-3000:])
            raise SystemExit(f"Restored baseline failed ({check}).")
    print(f"{caught}/{len(cases)} caught; restored baseline passes.")
    return 0 if caught == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
