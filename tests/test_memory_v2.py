"""Memory v2 (MV1, `workbench-memory-v2.md`): long notes, procedures, tags, versions, and
notes chosen for every turn.

The pure rules first (a body, a tag, the pinned block, a turn's block, a tag file), then the
store on a real SQLite file (the migration from schema 21, versions, the tag list), then the
claims a person would notice over HTTP: the pinned block stays the same bytes while each turn
carries the notes it asked about, `search_memory` runs and is withheld from a model notes may
not reach, and a deleted note leaves no words in the person's index.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import files, memory, memory_api, memory_suggest
from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.api import hide_notes
from eugene_plexus_workbench.store import SCHEMA_VERSION, MemoryNote, Store
from eugene_plexus_workbench.tools import MEMORY_SERVER, transcript

from .conftest import Browser, World, chat_model
from .test_memory_api import config

LIMIT = 20_000


def note(
    owner: str, text: str, *, id: str, at: float = 1.0, kind: str = "fact", **kw: Any
) -> MemoryNote:
    return MemoryNote(id=id, owner=owner, text=text, kind=kind, created_at=at, updated_at=at, **kw)


def run(path: Path, body: Callable[[Store], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        store = Store(path)
        await store.open()
        try:
            return await body(store)
        finally:
            await store.close()

    return asyncio.run(go())


# --------------------------------------------------------------------------- #
# the rules
# --------------------------------------------------------------------------- #


def test_a_body_keeps_its_lines_and_is_refused_for_a_secret_hidden_text_or_length() -> None:
    assert memory.validate_body("") == ""
    assert memory.validate_body("  \n\n ") == ""
    assert (
        memory.validate_body("# Steps\r\n\r\n1. Open\n2. Close\n") == "# Steps\n\n1. Open\n2. Close"
    )
    with pytest.raises(memory.NoteRefused) as secret:
        memory.validate_body("Steps\n\nThe password is hunter22")
    assert secret.value.reason == "secret"
    with pytest.raises(memory.NoteRefused) as hidden:
        memory.validate_body("a​b")
    assert hidden.value.reason == "hidden"
    with pytest.raises(memory.NoteRefused) as long:
        memory.validate_body("x" * 101, limit=100)
    assert long.value.reason == "too_long" and "100" in long.value.message
    with pytest.raises(memory.NoteRefused) as none:
        memory.validate_body("anything", limit=0)
    assert none.value.reason == "no_body"


def test_a_tag_is_one_short_line_without_a_mark_and_a_note_has_each_once() -> None:
    assert memory.validate_tag("  Clients  work ") == "Clients work"
    for bad in ("#work", "a,b", "x" * 41, "", "a​b"):
        with pytest.raises(memory.NoteRefused):
            memory.validate_tag(bad)
    assert memory.validate_tags(["Travel", "travel", "TRAVEL", "Home"]) == ["Travel", "Home"]
    with pytest.raises(memory.NoteRefused) as many:
        memory.validate_tags([f"t{i}" for i in range(11)])
    assert many.value.reason == "too_many_tags"
    with pytest.raises(memory.NoteRefused):
        memory.validate_description("token: sk-ant-abcdefghij1234567890abcd")


def test_the_pinned_block_holds_only_notes_always_used_and_their_bodies_while_they_fit() -> None:
    pinned = note(
        "a", "Weekly report checklist", id="p1", kind="procedure", pinned=True, body_chars=40
    )
    rule = note("a", "Answer in British English", id="i1", kind="instruction", at=2.0)
    other = note("a", "Lives in Lisbon", id="o1", at=3.0)
    snap = memory.build_snapshot(
        [pinned, rule, other],
        budget=2000,
        window=32768,
        model="m",
        now=5.0,
        bodies={"p1": "1. Gather numbers\n2. Write it"},
        nonce="abcd1234",
    )
    assert snap.ids == ["i1", "p1"]  # About / answers / work sections, pinned first in each
    assert "Lives in Lisbon" not in snap.text
    assert "How they work\n- Weekly report checklist" in snap.text
    assert '<note-abcd1234 title="Weekly report checklist">\n1. Gather numbers' in snap.text
    assert [s.body for s in snap.sent] == [False, True]
    assert memory.Snapshot.from_json(snap.to_json()) == snap
    old = {k: v for k, v in snap.to_json().items() if k != "version"}
    assert memory.Snapshot.from_json(old) is None  # a v1 copy is retaken

    tight = memory.build_snapshot(
        [pinned, rule],
        budget=260,
        window=None,
        model="m",
        now=5.0,
        bodies={"p1": "word " * 400},
        nonce="abcd1234",
    )
    assert tight.ids == ["i1", "p1"]
    assert tight.deferred == ["p1"]
    assert memory.reason_text(memory.BODY_BY_RELEVANCE, 260).startswith("Its body is too long")
    assert "word word" not in tight.text


def test_a_turns_block_is_data_in_its_own_delimiter_and_one_long_note_crowds_out_no_other() -> None:
    long = memory.Candidate(
        "n1",
        "Style guide",
        "procedure",
        ("writing",),
        "2026-10-01",
        passages=((1, "a " * 3000), (2, "b " * 3000)),
    )
    short = memory.Candidate("n2", "Lives in Lisbon", "fact", (), "2026-10-02")
    turn = memory.pack_relevant([long, short], 600, nonce="feedbeef")
    assert turn.text.startswith("<memory-feedbeef>\n" + memory.RELEVANT_INTRO)
    assert turn.text.endswith("</memory-feedbeef>")
    assert [c.id for c in turn.sent] == ["n1", "n2"]
    assert turn.sent[0].passages == ()  # cut to its title, so the next note still fits
    assert turn.tokens <= 600 and turn.did_not_fit == 0
    assert '<note-feedbeef n="1" kind="procedure" added="2026-10-01" tags="writing">' in turn.text
    record = turn.record()
    assert record["notes"][1] == {
        "id": "n2",
        "text": "Lives in Lisbon",
        "kind": "fact",
        "tags": [],
        "passages": [],
        "via": "words",
    }
    assert memory.pack_relevant([long, short], 0).text == ""
    chosen_nonce = memory.pack_relevant(
        [memory.Candidate("n3", "x", "fact", (), "")], 500
    ).text.split(">", 1)[0]
    assert chosen_nonce.startswith("<memory-")


def test_a_tag_file_is_read_as_csv_or_lines_and_names_the_lines_it_could_not_take() -> None:
    rows, problems = memory_api.read_tag_file(
        '﻿tag,description\nclients,"Work for paying customers, billed"\n'
        "Travel,\n#bad,x\na,b,c\n\nclients,Paying customers\n",
        "csv",
    )
    assert rows == [("clients", "Paying customers"), ("Travel", None)]
    assert [(p["line"], p["text"]) for p in problems] == [(4, "#bad"), (5, "a,b,c")]
    rows, problems = memory_api.read_tag_file("home\n  garden \n\nx,y\n", "txt")
    assert rows == [("home", None), ("garden", None)]
    assert [p["line"] for p in problems] == [4]


def test_a_suggestion_cannot_be_a_procedure_and_takes_tags_from_the_list() -> None:
    plain = memory_suggest.response_format()
    item = plain["json_schema"]["schema"]["properties"]["notes"]["items"]
    assert "procedure" not in item["properties"]["kind"]["enum"]
    assert "tags" not in item["properties"]
    listed = [
        memory_suggest.TagChoice("clients", "paying customers"),
        memory_suggest.TagChoice("home"),
    ]
    item = memory_suggest.response_format(listed)["json_schema"]["schema"]["properties"]["notes"][
        "items"
    ]
    assert item["properties"]["tags"]["items"] == {"type": "string", "enum": ["clients", "home"]}
    assert "tags" in item["required"]
    free = memory_suggest.response_format(listed, enum_max=1)
    free_item = free["json_schema"]["schema"]["properties"]["notes"]["items"]
    assert free_item["properties"]["tags"]["items"] == {"type": "string", "maxLength": 40}
    shown = memory_suggest.prompt(
        ["I bill clients weekly."], [], datetime.date(2026, 10, 19), "n0nce", listed
    )
    assert "- clients: paying customers" in shown and "- home" in shown


def test_a_search_memory_result_is_withheld_where_notes_may_not_go_and_hidden_from_the_owner() -> (
    None
):
    rounds = [
        {
            "assistant": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "search_memory", "arguments": '{"query":"lisbon"}'},
                    },
                ],
            },
            "calls": [
                {
                    "id": "c1",
                    "serverId": MEMORY_SERVER,
                    "result": "<memory-x>Lives in Lisbon</memory-x>",
                    "arguments": {"query": "lisbon"},
                },
            ],
        }
    ]
    assert transcript(rounds)[1]["content"].startswith("<memory-x>")
    withheld = transcript(rounds, notes=False)
    assert withheld[1]["content"] == memory.WITHHELD
    assert withheld[0]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert rounds[0]["assistant"]["tool_calls"][0]["function"]["arguments"] == '{"query":"lisbon"}'
    view = {
        "id": "m",
        "toolRounds": rounds,
        "memoryUsed": {"notes": [{"id": "n", "text": "x"}], "tokens": 9, "budget": 10},
    }
    hidden = hide_notes(view)
    assert hidden["memoryUsed"] == {"notes": 1, "tokens": 9, "budget": 10, "redacted": True}
    [call] = hidden["toolRounds"][0]["calls"]
    assert call["result"] == "Hidden: the person's notes." and call["arguments"] == {}
    assert hidden["toolRounds"][0]["assistant"]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert "Lisbon" not in json.dumps(hidden)


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #

#: `memory_notes` as schema 21 made it.
_V21_NOTES = (
    "CREATE TABLE memory_notes ("
    "id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
    "text TEXT NOT NULL CHECK (length(text) <= 240), "
    "kind TEXT NOT NULL CHECK (kind IN ('fact','preference','instruction','project','event')), "
    "status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','pending','dismissed')), "
    "pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)), "
    "source TEXT NOT NULL CHECK (source IN ('typed','said','button','suggested','imported')), "
    "source_chat_id TEXT, source_message_id TEXT, quote TEXT, "
    "expires_at TEXT, revisit_at TEXT, "
    "created_at REAL NOT NULL, updated_at REAL NOT NULL, last_used_at REAL, "
    "use_count INTEGER NOT NULL DEFAULT 0, replaces TEXT, source_connection_id TEXT, "
    "source_connection TEXT)"
)


def _as_schema_21(path: Path) -> None:
    """Turn a new store back into one schema 21 wrote: its notes table as it was, with two
    people's notes and a chat's frozen copy, and none of schema 22's columns."""
    with sqlite3.connect(path) as db:
        for trigger in ("memory_notes_ai", "memory_notes_ad", "memory_notes_au"):
            db.execute(f"DROP TRIGGER {trigger}")
        db.execute("DROP TABLE memory_notes_fts")
        db.execute("DROP TABLE memory_notes")
        db.execute("DROP TABLE memory_note_tags")
        db.execute("DROP TABLE memory_tags")
        db.execute(_V21_NOTES)
        db.execute("CREATE INDEX memory_notes_by_owner ON memory_notes (owner, status)")
        db.execute(
            "CREATE VIRTUAL TABLE memory_notes_fts USING fts5(text, content='memory_notes', "
            "tokenize='porter unicode61 remove_diacritics 2')"
        )
        for column in ("relevant_budget_tokens", "search_tool"):
            db.execute(f"ALTER TABLE memory_prefs DROP COLUMN {column}")
        db.execute("ALTER TABLE messages DROP COLUMN memory_used")
        db.executemany(
            "INSERT INTO memory_notes (id, owner, text, kind, status, pinned, source, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, 'typed', ?, ?)",
            [
                ("a1", "ada", "Cooks vegetarian food", "fact", "active", 0, 1.0, 1.0),
                ("a2", "ada", "Answer briefly", "instruction", "active", 0, 2.0, 2.0),
                ("a3", "ada", "Moved to Lisbon", "fact", "pending", 0, 3.0, 3.0),
                ("b1", "bo", "Plays the cello", "fact", "active", 1, 4.0, 4.0),
            ],
        )
        db.execute("INSERT INTO memory_notes_fts (memory_notes_fts) VALUES ('rebuild')")
        db.execute("INSERT INTO memory_prefs (owner, budget_tokens) VALUES ('ada', 700)")
        db.execute(
            "INSERT INTO chats (id, owner, title, created_at, updated_at, memory_snapshot) "
            "VALUES ('c1', 'ada', 'A chat', 1, 1, '{\"text\": \"old\"}')"
        )
        db.execute("UPDATE meta SET value = '21' WHERE key = 'schema'")


def test_a_schema_21_store_keeps_every_note_and_takes_the_v2_shape(tmp_path: Path) -> None:
    path = tmp_path / "w.sqlite3"
    run(path, lambda store: store.memory_count("ada"))
    _as_schema_21(path)

    async def body(store: Store) -> None:
        notes = {n.id: n for n in await store.memory_notes("ada", status=None, with_body=True)}
        assert {i: (n.text, n.kind, n.status) for i, n in notes.items()} == {
            "a1": ("Cooks vegetarian food", "fact", "active"),
            "a2": ("Answer briefly", "instruction", "active"),
            "a3": ("Moved to Lisbon", "fact", "pending"),
        }
        assert notes["a1"].body == "" and notes["a1"].valid_from == 1.0 and notes["a1"].tags == []
        assert [n.id for n in await store.memory_notes("bo")] == ["b1"]
        # The full-text index and its triggers came through: a search finds, a delete forgets.
        assert [n.id for n in await store.search_memory("ada", '"vegetarian"')] == ["a1"]
        assert await store.delete_memory_note("ada", "a1") == ["a1"]
        assert await store.search_memory("ada", '"vegetarian"') == []
        prefs = await store.memory_prefs("ada")
        assert (prefs["budget_tokens"], prefs["relevant_budget_tokens"]) == (700, 700)
        assert prefs["search_tool"] is True
        chat = await store.chat("c1")
        assert chat is not None and chat.memory_snapshot is None
        # The new kind and status are allowed now.
        await store.add_memory_note(
            note("ada", "Release steps", id="p1", kind="procedure", body="1. Tag\n2. Ship"),
            limit=LIMIT,
        )

    run(path, body)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key='schema'").fetchone() == (
            str(SCHEMA_VERSION),
        )
    assert SCHEMA_VERSION == 22
    assert list(tmp_path.glob("w.sqlite3.schema-21*.bak")), "backed up first"


def test_a_new_version_keeps_the_old_as_history_and_deleting_takes_the_chain(
    tmp_path: Path,
) -> None:
    async def body(store: Store) -> None:
        await store.add_memory_note(
            note("ada", "Lives in Porto", id="v1", tags=["Home"]), limit=LIMIT
        )
        v2 = await store.supersede_memory_note(
            "ada", "v1", note("ada", "Lives in Lisbon", id="v2", at=2.0, tags=["Home"])
        )
        assert v2 is not None and v2.status == "active" and v2.tags == ["Home"]
        v3 = await store.supersede_memory_note(
            "ada", "v2", note("ada", "Lives in Faro", id="v3", at=3.0)
        )
        assert v3 is not None
        assert await store.supersede_memory_note("ada", "v1", note("ada", "x", id="v9")) is None
        assert [n.id for n in await store.memory_notes("ada")] == ["v3"]
        old = await store.memory_note("ada", "v1")
        assert old is not None and (old.status, old.superseded_by) == ("superseded", "v2")
        assert [n.id for n in await store.note_history("ada", "v3")] == ["v2", "v1"]
        # Another person's delete finds nothing, and takes nothing.
        assert await store.delete_memory_note("bo", "v3") == []
        assert await store.delete_memory_note("bo", "v1") == []
        assert [n.id for n in await store.note_history("ada", "v3")] == ["v2", "v1"]
        # An earlier version goes alone, and the chain closes over it.
        assert await store.delete_memory_note("ada", "v2") == ["v2"]
        assert [n.id for n in await store.note_history("ada", "v3")] == ["v1"]
        # The current one takes what is left of its history with it.
        assert sorted(await store.delete_memory_note("ada", "v3")) == ["v1", "v3"]
        assert await store.memory_notes("ada", status=None) == []
        assert await store.delete_memory_note("bo", "v3") == []

    run(tmp_path / "w.sqlite3", body)


def test_the_tag_list_renames_merges_deletes_and_replaces_with_the_notes_following(
    tmp_path: Path,
) -> None:
    async def body(store: Store) -> None:
        await store.add_memory_note(note("ada", "One", id="n1", tags=["work", "Home"]), limit=LIMIT)
        await store.add_memory_note(note("ada", "Two", id="n2", at=2.0, tags=["job"]), limit=LIMIT)
        await store.put_memory_tag("ada", "work", "paid work")
        assert [(t["tag"], t["notes"]) for t in await store.memory_tags("ada")] == [
            ("Home", 1),
            ("job", 1),
            ("work", 1),
        ]
        # Merge: "job" onto "Work" (another case of "work"); n2 now carries "work".
        assert await store.rename_memory_tag("ada", "job", "Work") == 2
        listed = await store.memory_tags("ada")
        assert [(t["tag"], t["description"], t["notes"]) for t in listed] == [
            ("Home", "", 1),
            ("work", "paid work", 2),
        ]
        assert (await store.memory_note("ada", "n2")).tags == ["work"]  # type: ignore[union-attr]
        assert await store.rename_memory_tag("ada", "nope", "x") is None
        assert await store.delete_memory_tag("ada", "home") == 1
        assert (await store.memory_note("ada", "n1")).tags == ["work"]  # type: ignore[union-attr]
        assert await store.tags_removed_by("ada", ["garden"]) == [{"tag": "work", "notes": 2}]
        done = await store.import_memory_tags(
            "ada", [("garden", "plants"), ("Work", None)], replace=False
        )
        assert done == {"added": 1, "updated": 0, "removed": []}
        assert [t["description"] for t in await store.memory_tags("ada")] == ["plants", "paid work"]
        done = await store.import_memory_tags("ada", [("garden", None)], replace=True)
        assert done["removed"] == [{"tag": "work", "notes": 2}]
        assert (await store.memory_note("ada", "n1")).tags == []  # type: ignore[union-attr]
        # A note's delete takes its links with it.
        await store.add_memory_note(
            note("ada", "Three", id="n3", at=3.0, tags=["garden"]), limit=LIMIT
        )
        await store.delete_memory_note("ada", "n3")
        assert [t["notes"] for t in await store.memory_tags("ada")] == [0]

    run(tmp_path / "w.sqlite3", body)


# --------------------------------------------------------------------------- #
# over HTTP
# --------------------------------------------------------------------------- #


def signed_in(world: World, sub: str = "p-ada") -> Browser:
    person = world.browser()
    person.sign_in(sub)
    return person


def keep(person: Browser, text: str, **body: Any) -> dict[str, Any]:
    made = person.post("/api/memory/notes", json={"text": text, **body})
    assert made.status_code in (200, 201), made.text
    kept: dict[str, Any] = made.json()["note"]
    return kept


def ask(world: World, person: Browser, chat: str, content: str) -> dict[str, Any]:
    sent = person.post(f"/api/chats/{chat}/messages", json={"content": content})
    assert sent.status_code == 201, sent.text
    assert person.wait_answer(chat)["status"] == "done"
    request: dict[str, Any] = world.gateway.requests[-1]
    return request


def _system(request: dict[str, Any]) -> str | None:
    found = [m["content"] for m in request["messages"] if m["role"] == "system"]
    return str(found[0]) if found else None


def _users(request: dict[str, Any]) -> list[str]:
    return [m["content"] for m in request["messages"] if m["role"] == "user"]


def _index_notes(world: World, sub: str) -> dict[str, list[str]]:
    path = world.data / "knowledge" / files.person_key(sub) / "index.sqlite3"
    if not path.is_file():
        return {}
    with sqlite3.connect(path) as db:
        found: dict[str, list[str]] = {}
        for doc_id, text in db.execute(
            "SELECT d.id, c.text FROM documents d JOIN chunks c ON c.doc_id = d.id "
            "WHERE d.scope = 'notes' ORDER BY d.id, c.ord"
        ):
            found.setdefault(doc_id, []).append(text)
        return found


def test_each_turn_carries_the_notes_it_asked_about_and_the_pinned_block_stays_the_same(
    world: World,
) -> None:
    ada = signed_in(world)
    world.gateway.mode = "answer"
    keep(ada, "Answer in British English", kind="instruction")
    keep(ada, "Lives in Lisbon", tags=["Home"])
    keep(ada, "Rides a blue bicycle to work")
    keep(
        ada,
        "Weekly report checklist",
        kind="procedure",
        body="# Weekly report\n\nGather the sales numbers from the dashboard.\n\n"
        "## Sending\n\nEmail the report to the finance team on Friday.",
    )
    chat = ada.new_chat()
    first = ask(world, ada, chat, "Remind me which city I live in, Lisbon or Porto?")
    second = ask(world, ada, chat, "How do I send the weekly report to finance?")
    assert _system(first) == _system(second)
    assert "Answer in British English" in (_system(first) or "")
    assert "Lisbon" not in (_system(first) or "")
    one, two = _users(first)[-1], _users(second)[-1]
    assert one.startswith("<memory-") and one.endswith(
        "Remind me which city I live in, Lisbon or Porto?"
    )
    assert two.startswith("<memory-") and "Email the report to the finance team" in two
    # The first note in each turn's block is the one it asked about.
    assert one.index("Lives in Lisbon") < one.index("Weekly report checklist")
    assert two.index("Weekly report checklist") < two.index("Lives in Lisbon")
    # An earlier turn is replayed as the person wrote it, with no block.
    assert _users(second)[0] == "Remind me which city I live in, Lisbon or Porto?"
    answers = [
        m for m in ada.get(f"/api/chats/{chat}").json()["messages"] if m["role"] == "assistant"
    ]
    assert answers[-1]["memoryUsed"]["notes"][0]["text"] == "Weekly report checklist"
    shown = ada.get(f"/api/chats/{chat}/memory").json()
    assert shown["turn"]["messageId"] == answers[-1]["id"] and len(shown["turns"]) == 2
    assert [s["text"] for s in shown["sent"]] == ["Answer in British English"]
    first_turn = ada.get(f"/api/chats/{chat}/memory", params={"message": answers[0]["id"]}).json()
    assert first_turn["turn"]["notes"][0]["text"] == "Lives in Lisbon"
    # The model is offered search_memory (a model that calls tools; notes outside the pinned
    # block), and it is the same bytes in both requests.
    assert [t["function"]["name"] for t in first["tools"]] == ["search_memory"]
    assert first["tools"] == second["tools"]


def test_search_memory_runs_for_the_model_and_is_withheld_after_the_chat_moves_outside(
    world: World,
) -> None:
    ada = signed_in(world)
    keep(ada, "Lives in Lisbon")
    world.gateway.more_chat_models = [chat_model("cloud", locality="external", owned_by="openai")]
    world.gateway.mode = "tools"
    world.gateway.tool_arguments = '{"query": "lisbon"}'
    chat = ada.new_chat()
    ask(world, ada, chat, "Where do I live?")
    after_tool = world.gateway.requests[-1]
    [result] = [m["content"] for m in after_tool["messages"] if m["role"] == "tool"]
    assert result.startswith("<memory-") and "Lives in Lisbon" in result
    world.gateway.mode = "answer"
    ada.patch(f"/api/chats/{chat}", json={"model": "cloud"})
    moved = ask(world, ada, chat, "And what else?")
    assert moved["model"] == "cloud"
    assert _system(moved) is None and "tools" not in moved
    assert all("<memory-" not in u for u in _users(moved))
    [withheld] = [m["content"] for m in moved["messages"] if m["role"] == "tool"]
    assert withheld == memory.WITHHELD
    assert "lisbon" not in json.dumps(moved).lower(), "neither the result nor the query"


def test_a_long_note_and_its_tags_round_trip_and_a_delete_leaves_no_words_in_the_index(
    world: World,
) -> None:
    ada = signed_in(world)
    body = "# Release\n\n" + "\n\n".join(
        f"Step {i}: check the gauge numbered {i}." for i in range(60)
    )
    made = keep(ada, "Release procedure", kind="procedure", body=body, tags=["work", "Ops"])
    assert made["bodyChars"] == len(body) and sorted(made["tags"]) == ["Ops", "work"]
    whole = ada.get(f"/api/memory/notes/{made['id']}").json()
    assert whole["note"]["body"] == body and whole["earlier"] == []
    indexed = _index_notes(world, "p-ada")[made["id"]]
    assert indexed[0] == "Release procedure" and len(indexed) > 2
    exported = ada.get("/api/memory/export", params={"format": "json"}).json()
    [kept] = exported["notes"]
    assert (kept["body"], sorted(kept["tags"])) == (body, ["Ops", "work"])
    assert sorted(t["tag"] for t in exported["tags"]) == ["Ops", "work"]
    csv_text = ada.get("/api/memory/tags/export").text
    assert csv_text.splitlines()[0] == "tag,description"
    # Replace my list: said first, done only when the answer is sent back.
    asked = ada.post(
        "/api/memory/tags/import", json={"text": "work\n", "format": "txt", "replace": True}
    ).json()
    assert asked["confirmNeeded"] is True and asked["wouldRemove"] == [{"tag": "Ops", "notes": 1}]
    done = ada.post(
        "/api/memory/tags/import",
        json={"text": "work\n", "format": "txt", "replace": True, "confirm": ["Ops"]},
    ).json()
    assert done["removed"] == [{"tag": "Ops", "notes": 1}]
    assert ada.get(f"/api/memory/notes/{made['id']}").json()["note"]["tags"] == ["work"]
    # A new version is current; the old one is history, and neither is in the index twice.
    changed = ada.patch(
        f"/api/memory/notes/{made['id']}",
        json={"text": "Release procedure v2", "asNewVersion": True},
    ).json()
    assert changed["replaced"] == made["id"]
    held = _index_notes(world, "p-ada")
    assert list(held) == [changed["note"]["id"]] and held[changed["note"]["id"]][0] == (
        "Release procedure v2"
    )
    assert [
        n["id"] for n in ada.get(f"/api/memory/notes/{changed['note']['id']}").json()["earlier"]
    ] == [made["id"]]
    gone = ada.delete(f"/api/memory/notes/{changed['note']['id']}")
    assert gone.status_code == 204
    assert _index_notes(world, "p-ada") == {}
    path = world.data / "knowledge" / files.person_key("p-ada") / "index.sqlite3"
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'gauge'"
        ).fetchone() == (0,)
    assert ada.get(f"/api/memory/notes/{made['id']}").status_code == 404


def test_the_page_states_both_budgets_and_the_owner_raised_caps(world: World) -> None:
    ada = signed_in(world)
    page = ada.get("/api/memory").json()
    assert page["usage"]["pinned"] == {"tokens": 0, "budget": 2000, "notes": 0}
    assert page["usage"]["relevant"] == {"budget": 6000, "from": 0, "how": "words"}
    assert page["limits"]["maxNotes"] == 20_000 and page["limits"]["maxBodyChars"] == 20_000
    assert "procedure" in page["limits"]["kinds"]
    prefs = ada.put(
        "/api/memory/prefs", json={"relevantBudgetTokens": 900, "searchTool": False}
    ).json()
    assert prefs["effective"]["relevantBudgetTokens"] == 900 and prefs["searchTool"] is False
    assert ada.get("/api/memory").json()["usage"]["relevant"]["budget"] == 900


def test_an_index_that_lags_behind_an_edit_never_sends_the_old_words(world: World) -> None:
    ada = signed_in(world)
    made = keep(ada, "Lives in Porto", body="The flat is on Rua das Flores, number seven.")
    keeper = world.server.server.config.app.state.notes_index

    async def lagging(owner: str, note_ids: list[str]) -> None:
        return None  # the index is not told: it still holds the old version

    put = keeper.put
    keeper.put = lagging
    try:
        edited = ada.patch(
            f"/api/memory/notes/{made['id']}",
            json={"text": "Lives in Lisbon", "body": "The flat is near the river."},
        )
        assert edited.status_code == 200
        chat = ada.new_chat()
        request = ask(world, ada, chat, "Where is my flat, on Rua das Flores in Porto?")
    finally:
        keeper.put = put
    sent = json.dumps(request).replace("Where is my flat, on Rua das Flores in Porto?", "")
    assert "Flores" not in sent and "Porto" not in sent
    assert "Lives in Lisbon" in _users(request)[-1]  # the store's version, as a title


def test_deleting_a_note_takes_it_out_of_a_kept_search_and_every_answers_record(
    world: World,
) -> None:
    ada = signed_in(world)
    made = keep(ada, "Lives in Lisbon")
    keep(ada, "Plays the cello")
    world.gateway.mode = "tools"
    world.gateway.tool_arguments = '{"query": "lisbon"}'
    chat = ada.new_chat()
    ask(world, ada, chat, "Where do I live?")
    world.gateway.mode = "answer"
    assert "Lives in Lisbon" in json.dumps(ada.get(f"/api/chats/{chat}").json())
    assert ada.delete(f"/api/memory/notes/{made['id']}").status_code == 204
    after = ask(world, ada, chat, "And now?")
    [replayed] = [m["content"] for m in after["messages"] if m["role"] == "tool"]
    assert replayed == storage.FORGOTTEN_RESULT
    assert "Lisbon" not in json.dumps(after)
    view = ada.get(f"/api/chats/{chat}").json()
    assert "Lisbon" not in json.dumps(view)
    first = next(m for m in view["messages"] if m["role"] == "assistant")
    assert first["memoryUsed"]["forgotten"] == 1
    assert "Lisbon" not in json.dumps(ada.get(f"/api/chats/{chat}/memory").json())


def test_a_suggestion_is_tagged_by_code_from_the_words_it_says_and_no_model_sees_the_list() -> None:
    listed = [
        memory_suggest.TagChoice("work"),
        memory_suggest.TagChoice("home", "where I live"),
        memory_suggest.TagChoice("martial-arts"),
        memory_suggest.TagChoice("Rust"),
    ]
    assert memory_suggest.tags_for("Works from home on Rust projects", listed) == ("home", "Rust")
    assert memory_suggest.tags_for("Does martial arts after work", listed) == (
        "work",
        "martial-arts",
    )
    assert memory_suggest.tags_for("Likes tea", listed) == ()
    assert memory_suggest.TAGS_IN_REQUEST is False  # measured: the list made suggestions worse


def test_a_new_version_at_the_cap_is_refused_and_keeps_the_note_as_it_was(world: World) -> None:
    ada = signed_in(world)
    made = keep(ada, "Lives in Porto")
    keep(ada, "Plays the cello")
    assert config(world, memoryMaxNotes=2).json()["applied"] == ["memoryMaxNotes"]
    full = ada.patch(
        f"/api/memory/notes/{made['id']}", json={"text": "Lives in Lisbon", "asNewVersion": True}
    )
    assert full.status_code == 409 and full.json()["detail"]["reason"] == "full"
    kept = ada.get(f"/api/memory/notes/{made['id']}").json()
    assert (kept["note"]["text"], kept["earlier"]) == ("Lives in Porto", [])
    # Saved in place, it is still one note.
    edited = ada.patch(f"/api/memory/notes/{made['id']}", json={"text": "Lives in Lisbon"})
    assert edited.status_code == 200


def test_a_tag_cannot_start_a_formula_and_a_description_that_does_round_trips_through_csv(
    world: World,
) -> None:
    for formula in ("=SUM(A1)", "+1", "-x", "@cmd"):
        with pytest.raises(memory.NoteRefused):
            memory.validate_tag(formula)
    ada = signed_in(world)
    assert ada.post("/api/memory/tags", json={"tag": "=cmd"}).status_code == 422
    for tag, description in (("sums", "=1+1"), ("quoted", "'=2"), ("plain", "'tis the season")):
        made = ada.post("/api/memory/tags", json={"tag": tag, "description": description})
        assert made.status_code == 200, made.text
    exported = ada.get("/api/memory/tags/export").text
    lines = exported.splitlines()
    assert "sums,'=1+1" in lines and "quoted,''=2" in lines and "plain,'tis the season" in lines
    back = ada.post(
        "/api/memory/tags/import", json={"text": exported, "format": "csv", "replace": True}
    ).json()
    assert back.get("problems", []) == [], back
    described = {t["tag"]: t["description"] for t in ada.get("/api/memory/tags").json()["tags"]}
    assert described == {"plain": "'tis the season", "quoted": "'=2", "sums": "=1+1"}
