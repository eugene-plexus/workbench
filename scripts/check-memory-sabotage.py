"""Prove the memory checks are observed (MR1; memory v2, MV1).

Each case breaks one thing in the store, the rules or the routes, and its
named check must fail. Files are restored from exact bytes kept beside the
run, never from Git. A mutation that stops the code importing or compiling
does not count as a caught defect.

  python scripts/check-memory-sabotage.py            # every case
  python scripts/check-memory-sabotage.py --anchors  # only check each anchor is unique
  python scripts/check-memory-sabotage.py --label TEXT ...  # only the cases whose label has TEXT

Run it with the interpreter and PYTHONPATH the tests use, and with every
ambient EUGENE_PLEXUS_* variable cleared.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = "src/eugene_plexus_workbench/"
STORE = SRC + "store.py"
MEMORY = SRC + "memory.py"
ROUTES = SRC + "memory_api.py"
API = SRC + "api.py"
CONVERSATION = SRC + "conversation.py"
RULES = "tests/test_memory.py"
STORING = "tests/test_memory_store.py"
HTTP = "tests/test_memory_api.py"
V2 = "tests/test_memory_v2.py"
TOOLS = SRC + "tools.py"
RELEVANCE = SRC + "memory_relevance.py"
MEANING = SRC + "knowledge/meaning.py"
PAST = SRC + "past_chats.py"
EXCHANGES_INDEX = SRC + "exchanges_index.py"
INDEX = SRC + "knowledge/index.py"
CONNECTIONS = SRC + "connections.py"
MV2 = "tests/test_past_chats.py"

# (label, file, before, after, check)
CASES: list[tuple[str, str, str, str, str]] = [
    # --- whose notes -------------------------------------------------------
    (
        "a note read or listed by its id ignores the owner",
        STORE,
        'f"SELECT {columns} FROM memory_notes WHERE owner = ?{where}{order}", (owner, *params)',
        'f"SELECT {columns} FROM memory_notes WHERE ? IS NOT NULL{where}{order}", (owner, *params)',
        f"{STORING}::test_no_method_reaches_a_note_that_is_not_the_askers",
    ),
    (
        "an edit finds a note without the owner",
        STORE,
        "\"SELECT * FROM memory_notes WHERE id = ? AND owner = ? AND status = 'active'\",",
        "\"SELECT * FROM memory_notes WHERE id = ? AND ? IS NOT NULL AND status = 'active'\",",
        f"{STORING}::test_no_method_reaches_a_note_that_is_not_the_askers",
    ),
    (
        "a delete finds a note without the owner",
        STORE,
        '"SELECT id, status, superseded_by FROM memory_notes WHERE id = ? AND owner = ?",',
        '"SELECT id, status, superseded_by FROM memory_notes WHERE id = ? AND ? IS NOT NULL",',
        f"{V2}::test_a_new_version_keeps_the_old_as_history_and_deleting_takes_the_chain",
    ),
    (
        "the forget search ranks other people's notes",
        STORE,
        "\"WHERE memory_notes_fts MATCH ? AND n.owner = ? AND n.status = 'active' \"",
        "\"WHERE memory_notes_fts MATCH ? AND ? IS NOT NULL AND n.status = 'active' \"",
        f"{HTTP}::test_a_note_that_is_not_yours_is_a_note_that_does_not_exist",
    ),
    (
        "another person sets a chat's copy of its notes",
        STORE,
        '"UPDATE chats SET memory_snapshot = ? WHERE id = ? AND owner = ?",',
        '"UPDATE chats SET memory_snapshot = ? WHERE id = ? AND ? IS NOT NULL",',
        f"{STORING}::test_no_method_reaches_a_note_that_is_not_the_askers",
    ),
    (
        "the owner may refresh a chat they only read",
        ROUTES,
        "    chat = await _own_chat(request, person, chat_id)\n"
        "    return await chat_memory(request, person, chat, read_only=False, refresh=True)",
        "    chat, _ = await _readable_chat(request, person, chat_id)\n"
        "    return await chat_memory(request, person, chat, read_only=False, refresh=True)",
        f"{HTTP}::test_the_owner_cannot_read_a_persons_notes_even_where_they_read_her_chats",
    ),
    (
        "the owner's read-only view carries the notes' words",
        ROUTES,
        '            "readOnly": True,\n            "frozen": saved is not None,',
        '            "readOnly": True,\n            "text": saved.text if saved else None,\n'
        '            "frozen": saved is not None,',
        f"{HTTP}::test_the_owner_cannot_read_a_persons_notes_even_where_they_read_her_chats",
    ),
    # --- outside models ----------------------------------------------------
    (
        "notes go to a model that runs outside the machines",
        ROUTES,
        '    if facts.locality == "local":\n        return Decision(True, facts=facts)\n',
        "    if True:\n        return Decision(True, facts=facts)\n",
        f"{HTTP}::test_notes_are_held_back_from_a_model_that_runs_outside_the_machines",
    ),
    (
        "ask sends without asking",
        ROUTES,
        '    if choice == "always" or (choice == "ask" and chat.settings.get("memoryExternal") is True):',
        '    if choice in ("always", "ask"):',
        f"{HTTP}::test_notes_are_held_back_from_a_model_that_runs_outside_the_machines",
    ),
    (
        "the gateway's word on where a model runs is ignored",
        ROUTES,
        '        locality=str(common["locality"]),',
        '        locality="local",',
        f"{HTTP}::test_a_model_nobody_can_place_is_not_trusted_with_notes",
    ),
    (
        "the owner's never is only a default",
        ROUTES,
        '    if install == "never":\n        return "never", "owner"\n',
        "",
        f"{HTTP}::test_the_owners_never_is_final_and_otherwise_the_persons_choice_stands",
    ),
    (
        "/api/models says every model is local",
        API,
        '                "locality": info.get("locality")\n'
        '                if info.get("locality") in ("local", "external")\n'
        '                else "unknown",',
        '                "locality": "local",',
        f"{HTTP}::test_the_models_say_where_they_run",
    ),
    # --- real deletion -----------------------------------------------------
    (
        "a delete only hides the note",
        STORE,
        'db.execute("DELETE FROM memory_notes WHERE id = ? AND owner = ?", (one, owner))',
        "db.execute(\"UPDATE memory_notes SET status = 'dismissed' WHERE id = ? AND owner = ?\", "
        "(one, owner))",
        f"{STORING}::test_deleting_a_note_is_real",
    ),
    (
        "a deleted note stays in the index",
        STORE,
        '    "CREATE TRIGGER IF NOT EXISTS memory_notes_ad AFTER DELETE ON memory_notes BEGIN "\n'
        '    "INSERT INTO memory_notes_fts (memory_notes_fts, rowid, text) "\n'
        "    \"VALUES ('delete', old.rowid, old.text); \"\n",
        '    "CREATE TRIGGER IF NOT EXISTS memory_notes_ad AFTER DELETE ON memory_notes BEGIN "\n'
        '    "SELECT 1; "\n',
        f"{STORING}::test_deleting_a_note_is_real",
    ),
    (
        "a deleted note's log keeps its words",
        STORE,
        '"UPDATE memory_events SET old_text = NULL, new_text = NULL WHERE note_id = old.id; "',
        '"UPDATE memory_events SET old_text = old_text WHERE note_id = old.id; "',
        f"{STORING}::test_deleting_a_note_is_real",
    ),
    (
        "a deleted note stays in a chat's copy",
        STORE,
        "                self._drop_from_chats(db, owner, set(gone))\n",
        "",
        f"{HTTP}::test_an_edit_waits_for_a_refresh_and_a_delete_does_not",
    ),
    (
        "deleting every note leaves the chats' copies",
        STORE,
        "                self._drop_from_chats(db, owner, None)\n",
        "",
        f"{STORING}::test_deleting_every_note_leaves_no_words_and_no_chat_copy",
    ),
    # --- what a model is shown ---------------------------------------------
    (
        "the budget is ignored",
        MEMORY,
        "            o, c = o + to, c + tc\n        if _tokens(other + o, cjk + c) <= budget:",
        "            o, c = o + to, c + tc\n        if True:",
        f"{RULES}::test_the_budget_is_spent_in_priority_order_newest_first",
    ),
    (
        "a window gives up no share of itself",
        MEMORY,
        "    return max(0, min(top, window * BUDGET_SHARE_PERCENT // 100))",
        "    return max(0, top)",
        f"{HTTP}::test_the_budget_is_kept_and_what_did_not_fit_is_named",
    ),
    (
        "no window is too small",
        MEMORY,
        "    if window is not None and window < MIN_WINDOW:",
        "    if window is not None and window < 0:",
        f"{HTTP}::test_a_window_under_4096_is_given_no_notes_and_the_drawer_says_why",
    ),
    (
        "the oldest note is taken first",
        MEMORY,
        "    return (0 if is_always(note) else 1, -note.created_at, note.id)",
        "    return (0 if is_always(note) else 1, note.created_at, note.id)",
        f"{RULES}::test_the_budget_is_spent_in_priority_order_newest_first",
    ),
    (
        "instructions are not used first",
        MEMORY,
        "    return (0 if is_always(note) else 1, -note.created_at, note.id)",
        "    return (0 if note.pinned else 1, -note.created_at, note.id)",
        f"{RULES}::test_the_budget_is_spent_in_priority_order_newest_first",
    ),
    (
        "a suggestion that is not approved is sent",
        MEMORY,
        'usable = [n for n in notes if n.status == "active" and is_always(n) and not is_past(n, today)]',
        "usable = [n for n in notes if is_always(n) and not is_past(n, today)]",
        f"{RULES}::test_only_active_notes_are_sent",
    ),
    (
        "the block is a second system message",
        CONVERSATION,
        "    system = system_text(instructions, memory)\n"
        "    if system:\n"
        '        messages.append({"role": "system", "content": system})',
        "    if instructions:\n"
        '        messages.append({"role": "system", "content": instructions})\n'
        "    if memory:\n"
        '        messages.append({"role": "system", "content": memory})',
        f"{HTTP}::test_the_notes_go_in_the_one_system_message_after_the_instructions",
    ),
    (
        "the request is built without the notes",
        "src/eugene_plexus_workbench/api.py",
        "            memory=notes,",
        "            memory=None,",
        "tests/test_memory_api.py::test_the_notes_go_in_the_one_system_message_after_the_instructions",
    ),
    # --- the first request, and every one after ----------------------------
    (
        "a chat takes new notes at every turn",
        ROUTES,
        "    if snapshot is None or snapshot.model != model_id:",
        "    if True:",
        f"{HTTP}::test_every_turn_sends_the_same_bytes_until_the_person_refreshes",
    ),
    (
        "another model is sent the first model's copy",
        ROUTES,
        "    if snapshot is None or snapshot.model != model_id:",
        "    if snapshot is None:",
        f"{HTTP}::test_another_model_takes_the_notes_again_for_its_own_window",
    ),
    (
        "a chat does not keep what it was sent",
        ROUTES,
        "        await store.set_memory_snapshot(chat.id, person.sub, snapshot.to_json())\n",
        "",
        f"{HTTP}::test_every_turn_sends_the_same_bytes_until_the_person_refreshes",
    ),
    (
        "a copy is trusted whatever its hash",
        MEMORY,
        '        if found.hash != data.get("hash") or not found.text or not found.sent:',
        "        if not found.text or not found.sent:",
        f"{RULES}::test_a_snapshot_survives_being_saved_and_a_bad_copy_is_not_trusted",
    ),
    (
        # Its order against `_ask` is not a case: the request is built in the answer's own
        # task, so a command handled just after `_ask` still lands first under the fakes.
        # The live run (mr1-memory-acceptance.py, part 2) checks the first request.
        "the command is not handled",
        API,
        "    command = await memory_api.handle_command(request, person, chat, user)\n",
        "    command = None\n",
        f"{HTTP}::test_a_command_keeps_the_persons_words_and_the_message_is_still_sent",
    ),
    # --- the switches ------------------------------------------------------
    (
        "the owner's switch is ignored",
        ROUTES,
        '    if not install.enabled:\n        return Decision(False, "install_off", _OWNER_OFF)',
        '    if False:\n        return Decision(False, "install_off", _OWNER_OFF)',
        f"{HTTP}::test_what_the_owner_turns_off_stays_off_and_the_person_can_still_leave",
    ),
    (
        "the person's switch is ignored",
        ROUTES,
        '    if not prefs["enabled"]:\n        return Decision(False, "person_off", _PERSON_OFF)',
        '    if False:\n        return Decision(False, "person_off", _PERSON_OFF)',
        f"{HTTP}::test_a_person_who_turns_memory_off_for_themselves_is_sent_none",
    ),
    (
        "the chat's switch is ignored",
        ROUTES,
        '    if chat.settings.get("memory") == "off":\n'
        '        return Decision(False, "chat_off", "Memory is off for this chat.")',
        '    if False:\n        return Decision(False, "chat_off", "Memory is off for this chat.")',
        f"{HTTP}::test_the_chat_switch_takes_the_notes_out_and_back_in_unchanged",
    ),
    # --- what may be kept --------------------------------------------------
    (
        "a secret is kept",
        MEMORY,
        '        raise NoteRefused("secret", SECRET_MESSAGE, secret)\n'
        "    if len(cleaned) > MAX_NOTE_CHARS:",
        "        pass\n    if len(cleaned) > MAX_NOTE_CHARS:",
        f"{RULES}::test_a_note_that_looks_like_a_secret_is_refused_in_plain_words",
    ),
    (
        "every long number is a card",
        MEMORY,
        "    return total % 10 == 0",
        "    return True",
        f"{RULES}::test_ordinary_notes_are_kept",
    ),
    (
        "every IBAN-shaped word is an IBAN",
        MEMORY,
        "    return int(number) % 97 == 1",
        "    return True",
        f"{RULES}::test_ordinary_notes_are_kept",
    ),
    (
        "invisible characters are kept",
        MEMORY,
        '    if _hidden(cleaned):\n        raise NoteRefused(\n            "hidden",\n'
        '            "A note is one line',
        '    if False:\n        raise NoteRefused(\n            "hidden",\n'
        '            "A note is one line',
        f"{RULES}::test_a_note_cannot_hold_a_control_or_invisible_character",
    ),
    (
        "the length is not checked",
        MEMORY,
        "    if len(cleaned) > MAX_NOTE_CHARS:",
        "    if len(cleaned) > 10_000:",
        f"{RULES}::test_a_note_is_at_most_240_characters",
    ),
    (
        "the database allows a longer note",
        STORE,
        '"text TEXT NOT NULL CHECK (length(text) <= 240), "',
        '"text TEXT NOT NULL CHECK (length(text) <= 2400), "',
        f"{STORING}::test_the_database_itself_refuses_what_a_note_may_not_be",
    ),
    (
        "the same words are kept twice",
        STORE,
        'if note_key(r["text"]) == wanted',
        "if False",
        f"{STORING}::test_the_same_words_are_not_kept_twice_and_the_limit_is_kept",
    ),
    (
        "the limit is not kept",
        STORE,
        "                if total >= limit:\n                    raise MemoryFull(limit)\n"
        "                self._insert_note(db, note)",
        "                if False:\n                    raise MemoryFull(limit)\n"
        "                self._insert_note(db, note)",
        f"{STORING}::test_the_same_words_are_not_kept_twice_and_the_limit_is_kept",
    ),
    (
        "the install's limit is not the limit",
        ROUTES,
        "        kept, created = await store.add_memory_note(note, limit=install.max_notes)",
        "        kept, created = await store.add_memory_note(note, limit=10**9)",
        f"{HTTP}::test_the_same_words_are_not_kept_twice_and_the_install_sets_the_limit",
    ),
    (
        "notes made together have no order",
        STORE,
        "        if latest is not None and note.created_at <= latest:\n"
        "            note.created_at = note.updated_at = latest + 1e-6\n"
        "        if note.valid_from is None:",
        "        if note.valid_from is None:",
        f"{STORING}::test_notes_made_in_the_same_instant_are_still_newest_first",
    ),
    # --- search and the command --------------------------------------------
    (
        "words are not stemmed",
        STORE,
        "\"text, content='memory_notes', tokenize='porter unicode61 remove_diacritics 2')\",",
        "\"text, content='memory_notes', tokenize='unicode61 remove_diacritics 2')\",",
        f"{STORING}::test_the_index_follows_every_insert_edit_and_delete",
    ),
    (
        "forget deletes on a guess",
        ROUTES,
        "            found = await store.search_memory(person.sub, expression, limit=5)\n",
        "            found = await store.search_memory(person.sub, expression, limit=5)\n"
        "            for guess in found[:1]:\n"
        "                await store.delete_memory_note(person.sub, guess.id)\n",
        f"{HTTP}::test_forget_finds_candidates_and_never_deletes_on_a_guess",
    ),
    (
        "a question is a command",
        MEMORY,
        '    return None if not text or text.endswith("?") else Command("remember", text)',
        '    return None if not text else Command("remember", text)',
        f"{RULES}::test_a_question_a_story_or_an_ordinary_sentence_is_not_a_command",
    ),
    (
        "remember to call is a note",
        MEMORY,
        "        if first is None or first.group().lower() in _NOT_A_NOTE:",
        "        if first is None:",
        f"{RULES}::test_a_question_a_story_or_an_ordinary_sentence_is_not_a_command",
    ),
    # --- schema 10 ---------------------------------------------------------
    (
        "the migration forgets the chat's copy",
        STORE,
        '        "ALTER TABLE chats ADD COLUMN memory_snapshot TEXT",\n',
        "",
        f"{STORING}::test_a_schema_9_file_gains_memory_and_keeps_every_chat",
    ),
    # --- memory v2 (MV1) ----------------------------------------------------
    (
        "notes that are not always used go in the pinned block",
        MEMORY,
        'if n.status == "active" and is_always(n) and not is_past(n, today)]',
        'if n.status == "active" and not is_past(n, today)]',
        f"{V2}::test_the_pinned_block_holds_only_notes_always_used_and_their_bodies_while_they_fit",
    ),
    (
        "a frozen copy from before MV1 is trusted",
        MEMORY,
        '                or data.get("version") != SNAPSHOT_VERSION\n',
        "",
        f"{V2}::test_the_pinned_block_holds_only_notes_always_used_and_their_bodies_while_they_fit",
    ),
    (
        "a turn's notes are not said to be data",
        MEMORY,
        "    inner = [RELEVANT_INTRO] + [",
        "    inner = [] + [",
        f"{V2}::test_a_turns_block_is_data_in_its_own_delimiter_and_one_long_note_crowds_out_no_other",
    ),
    (
        "a model notes may not reach is sent the pinned block, a turn's notes and the tool",
        ROUTES,
        "    if not decision.send:\n        return None, None",
        "    if False:\n        return None, None",
        f"{V2}::test_search_memory_runs_for_the_model_and_is_withheld_after_the_chat_moves_outside",
    ),
    (
        "a kept search_memory result is replayed where notes may not go",
        TOOLS,
        'if call.get("serverId") in held:',
        "if False:",
        f"{V2}::test_a_search_memory_result_is_withheld_where_notes_may_not_go_and_hidden_from_the_owner",
    ),
    (
        "the owner reads what a turn was sent",
        API,
        '    if isinstance(used, dict):\n        past = used.get("pastChats")\n',
        '    if False:\n        past = used.get("pastChats")\n',
        f"{V2}::test_a_search_memory_result_is_withheld_where_notes_may_not_go_and_hidden_from_the_owner",
    ),
    (
        "a deleted note's passages stay in the index",
        ROUTES,
        "    await _unindex(state, owner, chain)\n",
        "",
        f"{V2}::test_a_long_note_and_its_tags_round_trip_and_a_delete_leaves_no_words_in_the_index",
    ),
    (
        "an index that lags sends a note's old words",
        RELEVANCE,
        'if n.status == "active" and held.get(n.id) == notes_index.digest(n)',
        'if n.status == "active"',
        f"{V2}::test_an_index_that_lags_behind_an_edit_never_sends_the_old_words",
    ),
    (
        "a replaced note stays current",
        STORE,
        "\"UPDATE memory_notes SET status = 'superseded', superseded_at = ?, superseded_by = ?, \"",
        "\"UPDATE memory_notes SET status = 'active', superseded_at = ?, superseded_by = ?, \"",
        f"{V2}::test_a_new_version_keeps_the_old_as_history_and_deleting_takes_the_chain",
    ),
    (
        "the schema 22 rebuild leaves the notes behind",
        STORE,
        "f\"SELECT rowid, {', '.join(carried)}, created_at FROM memory_notes_v1\"",
        "f\"SELECT rowid, {', '.join(carried)}, created_at FROM memory_notes_v1 WHERE 0\"",
        f"{V2}::test_a_schema_21_store_keeps_every_note_and_takes_the_v2_shape",
    ),
    (
        "a note goes to an outside search model",
        MEANING,
        "BATCH, notes=model.local, documents=settings.enabled",
        "BATCH, notes=True, documents=settings.enabled",
        "tests/test_meaning_worker.py::test_a_note_is_never_sent_to_a_search_model_outside_the_machines",
    ),
    (
        "a deleted note stays in the answers' records and a kept search",
        STORE,
        "                self._drop_from_answers(db, owner, titles)\n",
        "",
        f"{V2}::test_deleting_a_note_takes_it_out_of_a_kept_search_and_every_answers_record",
    ),
    (
        "a note pinned mid-chat goes with the turn before Refresh",
        RELEVANCE,
        "            and (not memory.is_always(n) or n.id in bodies)\n",
        "",
        f"{HTTP}::test_every_turn_sends_the_same_bytes_until_the_person_refreshes",
    ),
    (
        "a withheld search keeps the query the model wrote",
        TOOLS,
        "        if held:\n            mine = ",
        "        if False:\n            mine = ",
        f"{V2}::test_a_search_memory_result_is_withheld_where_notes_may_not_go_and_hidden_from_the_owner",
    ),
    (
        "a new version is kept past the limit",
        ROUTES,
        "                person.sub, note_id, new, limit=install.max_notes\n",
        "                person.sub, note_id, new\n",
        f"{V2}::test_a_new_version_at_the_cap_is_refused_and_keeps_the_note_as_it_was",
    ),
    (
        "a description that starts a formula is written as one",
        ROUTES,
        '        writer.writerow([t["tag"], _csv_cell(t["description"])])',
        '        writer.writerow([t["tag"], t["description"]])',
        f"{V2}::test_a_tag_cannot_start_a_formula_and_a_description_that_does_round_trips_through_csv",
    ),
    # --- past chats (MV2) --------------------------------------------------
    (
        "MV2: a deleted chat's exchanges stay in the index",
        EXCHANGES_INDEX,
        "        return int(await index.delete_exchanges(chat_id=chat_id))\n",
        "        return 0\n",
        f"{MV2}::test_deleting_a_chat_leaves_no_word_in_the_index_or_in_any_answer",
    ),
    (
        "MV2: a version not shown is read",
        STORE,
        '                    "AND a.chosen = 1 AND u.chosen = 1",\n',
        '                    "",\n',
        f"{MV2}::test_a_version_not_shown_is_never_read_and_a_delete_removes_twice",
    ),
    (
        "MV2: Rebuild search leaves past chats empty",
        SRC + "saved_api.py",
        "        exchanges.refill(person.sub)\n",
        "        pass\n",
        f"{MV2}::test_rebuild_search_puts_the_past_chats_back",
    ),
    (
        "MV2: a kept search_chats result survives its chat's delete",
        STORE,
        '                    _drop_chat_from_answers(db, found["owner"], chat_id)\n',
        "                    pass\n",
        f"{MV2}::test_deleting_a_chat_takes_it_out_of_every_other_answers_record_and_kept_search",
    ),
    (
        "MV2: a withheld search_chats call replays its query",
        TOOLS,
        "({CHATS_SERVER} if not chats else set())",
        "set()",
        f"{MV2}::test_a_kept_search_chats_call_is_withheld_where_past_chats_may_not_go_and_hidden_from_owner",
    ),
    (
        "MV2: past chats go where documents may never go",
        PAST,
        '    if choice == "never":\n',
        "    if False:\n",
        f"{MV2}::test_search_chats_runs_and_is_withheld_after_the_chat_moves_outside",
    ),
    (
        "MV2: Use past chats sends this chat's own exchanges",
        PAST,
        "        found = await find(state, person.sub, latest, besides=chat.id, query=query)\n",
        "        found = await find(state, person.sub, latest, besides=None, query=query)\n",
        f"{MV2}::test_use_past_chats_is_off_by_default_and_then_carries_a_match_from_another_chat",
    ),
    (
        "MV2: Use past chats is on without the person",
        PAST,
        '    if chat.settings.get("pastChats") is not True or not others:\n',
        "    if not others:\n",
        f"{MV2}::test_use_past_chats_is_off_by_default_and_then_carries_a_match_from_another_chat",
    ),
    (
        "MV2: an exchange is owed meaning from an outside search model",
        INDEX,
        '                else f" AND scope NOT IN ({_PRIVATE_MARKS})"\n',
        "                else f\" AND scope != '{NOTES}'\"\n",
        "tests/test_meaning_index.py::test_notes_and_documents_are_owed_meaning_apart_and_the_cap_counts_documents_only",
    ),
    (
        "MV2: a connection searches past chats without the right",
        CONNECTIONS,
        '    "search_chats": "search_chats",\n}',
        '    "search_chats": "read_notes",\n}',
        f"{MV2}::test_a_connection_searches_past_chats_only_with_the_right",
    ),
    (
        "MV2: the owner's view keeps a past-chats search",
        API,
        "_PRIVATE_SERVERS = (MEMORY_SERVER, CHATS_SERVER)",
        "_PRIVATE_SERVERS = (MEMORY_SERVER,)",
        f"{MV2}::test_a_kept_search_chats_call_is_withheld_where_past_chats_may_not_go_and_hidden_from_owner",
    ),
]

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}


def run(check: str) -> tuple[bool, str]:
    """(passed, output) for one check."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", check],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=400,
    )
    return result.returncode == 0, result.stdout + result.stderr


def broken_build(output: str) -> bool:
    """A mutation that stops the code compiling or importing proves nothing."""
    markers = ("SyntaxError", "ERROR collecting", "ImportError", "IndentationError", "NameError")
    return any(m in output for m in markers)


def anchors() -> None:
    for label, name, before, _, _ in CASES:
        found = (ROOT / name).read_text(encoding="utf-8").count(before)
        if found != 1:
            raise SystemExit(f"{name}: the anchor for '{label}' is in {found} places; fix it.")
    print(f"{len(CASES)} anchors, each in exactly one place.")


def main() -> int:
    anchors()
    if "--anchors" in sys.argv:
        return 0
    wanted = [a for i, a in enumerate(sys.argv) if i > 1 and sys.argv[i - 1] == "--label"]
    cases = [c for c in CASES if not wanted or any(w in c[0] for w in wanted)]
    if not cases:
        raise SystemExit(f"No case's label has {wanted}.")
    checks = sorted({check for *_, check in cases})
    for check in checks:
        passed, output = run(check)
        if not passed:
            print(output)
            raise SystemExit(f"Baseline failed ({check}); no files changed.")
    print(f"Baseline: {len(checks)} checks pass.", flush=True)
    backup = Path(tempfile.mkdtemp(prefix="workbench-memory-sabotage-"))
    print(f"Exact backups: {backup}", flush=True)
    caught = 0
    for index, (label, name, before, after, check) in enumerate(cases):
        path = ROOT / name
        original = path.read_bytes()
        (backup / f"{index}-{path.name}").write_bytes(original)
        try:
            path.write_bytes(original.decode("utf-8").replace(before, after).encode("utf-8"))
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
            print(output)
            raise SystemExit(f"Restored baseline failed ({check}).")
    print(f"{caught}/{len(cases)} caught; restored baseline passes.")
    return 0 if caught == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
