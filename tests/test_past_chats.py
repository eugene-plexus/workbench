"""Past chats (MV2, `workbench-memory-v2.md` sections 14-25).

The pure rules first (what an exchange is, the block a model is sent, the replay and the
owner's view), then the store on a real SQLite file (the migration from schema 22, a deleted
chat leaving other answers), then the claims a person would notice over HTTP: their chats are
found on Search chats and never anyone else's, Use past chats carries a matching exchange and
is off by default, `search_chats` runs and is withheld where past chats may not go, a deleted
chat leaves no word in the index or in any answer, and a connection searches them only with
the right.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from eugene_plexus_workbench import exchanges_index, files, past_chats
from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.api import hide_notes
from eugene_plexus_workbench.store import SCHEMA_VERSION, Message, Store
from eugene_plexus_workbench.tools import CHATS_SERVER, CHATS_WITHHELD, transcript

from .conftest import Browser, World, chat_model
from .test_connections import _connect, call

# --------------------------------------------------------------------------- #
# the rules
# --------------------------------------------------------------------------- #


def _m(id: str, role: str, content: str, parent: str | None, **kw: Any) -> Message:
    return Message(
        id=id,
        chat_id="c1",
        seq=kw.pop("seq", 0),
        role=role,
        status=kw.pop("status", "done"),
        created_at=kw.pop("at", 86_400.0),
        content=content,
        parent_id=parent,
        **kw,
    )


def test_an_exchange_is_a_question_and_the_shown_answers_opening_on_the_path() -> None:
    long = "First part. " * 40 + "\n\n" + "Second part. " * 200
    tree = [
        _m("u1", "user", "Where should we eat in Lisbon?", None, seq=1),
        _m("a1", "assistant", "Try the old version.", "u1", seq=2, chosen=False),
        _m("a2", "assistant", long, "u1", seq=3),
        _m("u2", "user", "   ", "a2", seq=4),
        _m("a3", "assistant", "Nothing asked.", "u2", seq=5),
        _m("u3", "user", "And after?", "a3", seq=6),
        _m("a4", "assistant", "Still writing", "u3", seq=7, status="running"),
    ]
    found = exchanges_index.exchanges_of("c1", "Trip", tree)
    assert [e.id for e in found] == ["a2"]  # a version not shown, an empty question, running
    [one] = found
    assert one.question == "Where should we eat in Lisbon?" and one.date == "1970-01-02"
    assert one.answer.endswith("…") and len(one.answer) <= exchanges_index.OPENING_CHARS
    assert one.answer.startswith("First part.") and "Second part" in one.answer
    passages = exchanges_index.passages(one)
    assert passages[0].heading_path == "Trip · 1970-01-02"
    assert passages[-1].heading_path == "Trip · 1970-01-02 > Answer"
    # After a web search, the opening is of the text written after it.
    searched = _m("a5", "assistant", "Draft before.Final words.", "u1", answer_from=13)
    assert exchanges_index.final_text(searched.content, searched.answer_from) == "Final words."
    renamed = exchanges_index.exchanges_of("c1", "Lisbon trip", tree)[0]
    assert exchanges_index.digest(renamed) != exchanges_index.digest(one)


def test_the_block_is_data_in_its_own_delimiter_and_keeps_to_its_budget() -> None:
    results = [
        past_chats.Result(
            f"a{i}", f"c{i}", 'Trip "x" <y>', "2026-10-01", "Q" * 3000, "A " * 3000, "words"
        )
        for i in range(8)
    ]
    packed = past_chats.pack(results, 2000, 5)
    assert packed.sent and packed.tokens <= 2000 and len(packed.sent) < 8
    text = packed.text
    nonce = text.split("\n", 1)[0].removeprefix("<past-chats-").removesuffix(">")
    assert len(nonce) == 8 and text.endswith(f"</past-chats-{nonce}>")
    assert past_chats.INTRO in text and 'chat="Trip &quot;x&quot; &lt;y>"' in text
    assert "Q" * 1300 not in text  # an excerpt is cut
    assert past_chats.pack(results, 0, 5).sent == []
    assert len(past_chats.pack(results, 100_000, 3).sent) == 3


def test_a_kept_search_chats_call_is_withheld_where_past_chats_may_not_go_and_hidden_from_owner() -> (
    None
):
    rounds = [
        {
            "assistant": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "t1",
                        "type": "function",
                        "function": {"name": "search_chats", "arguments": '{"query":"lisbon"}'},
                    }
                ],
            },
            "calls": [
                {"id": "t1", "serverId": CHATS_SERVER, "result": "Lisbon trip", "chats": ["c9"]}
            ],
        }
    ]
    sent = transcript(rounds, notes=True, chats=True)
    assert "lisbon" in json.dumps(sent).lower()
    held = transcript(rounds, notes=True, chats=False)
    assert "lisbon" not in json.dumps(held).lower() and held[-1]["content"] == CHATS_WITHHELD
    # Without its own flag it follows the notes'.
    assert transcript(rounds, notes=False)[-1]["content"] == CHATS_WITHHELD
    view = hide_notes(
        {
            "toolRounds": rounds,
            "memoryUsed": {
                "notes": [],
                "pastChats": {"exchanges": [{"chatId": "c9", "title": "Lisbon trip"}]},
            },
        }
    )
    assert "lisbon" not in json.dumps(view).lower()
    assert view["memoryUsed"]["pastChats"] == {"exchanges": 1, "redacted": True}


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #


def run(path: Path, body: Callable[[Store], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        store = Store(path)
        await store.open()
        try:
            return await body(store)
        finally:
            await store.close()

    return asyncio.run(go())


def test_a_schema_22_store_gains_the_two_columns_and_keeps_its_connections(tmp_path: Path) -> None:
    path = tmp_path / "workbench.sqlite3"
    run(path, lambda store: asyncio.sleep(0))
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE connections DROP COLUMN search_chats")
        db.execute("ALTER TABLE memory_prefs DROP COLUMN chats_tool")
        db.execute(
            "INSERT INTO connections (id, owner, name, token_hash, read_notes, search_documents, "
            "suggest_notes, created_at, expires_at) VALUES ('k1', 'ada', 'Laptop', 'h', 1, 0, 0, "
            "1, 9e9)"
        )
        db.execute("INSERT INTO memory_prefs (owner, search_tool) VALUES ('ada', 0)")
        db.execute("UPDATE meta SET value = '22' WHERE key = 'schema'")

    async def check(store: Store) -> tuple[Any, Any]:
        return await store.connections("ada"), await store.memory_prefs("ada")

    [kept], prefs = run(path, check)
    assert kept.read_notes and not kept.search_chats  # off for every connection already made
    assert prefs["chats_tool"] is True and prefs["search_tool"] is False
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == str(
            SCHEMA_VERSION
        )
    assert list(tmp_path.glob("*.schema-22*")), "the store's backup before the migration"


def test_deleting_a_chat_takes_it_out_of_every_other_answers_record_and_kept_search(
    tmp_path: Path,
) -> None:
    async def go(store: Store) -> tuple[Any, Any]:
        for chat in ("gone", "kept", "theirs"):
            owner = "bo" if chat == "theirs" else "ada"
            await store.create_chat(storage.Chat(chat, owner, chat.title(), "m", 1.0, 1.0))
        used = {
            "notes": [],
            "pastChats": {
                "exchanges": [
                    {"chatId": "gone", "messageId": "a", "title": "Gone"},
                    {"chatId": "other", "messageId": "b", "title": "Other"},
                ]
            },
        }
        rounds = [
            {
                "assistant": {"role": "assistant", "content": None, "tool_calls": []},
                "calls": [
                    {"id": "t", "serverId": CHATS_SERVER, "result": "Gone words", "chats": ["gone"]}
                ],
            }
        ]
        for chat, mid in (("kept", "m1"), ("theirs", "m2")):
            await store.add_message(Message(mid, chat, 0, "assistant", "done", 1.0), parent_id=None)
            await store.update_message(mid, memory_used=used, tool_rounds=rounds)
        await store.delete_chat("gone")
        return await store.message("m1"), await store.message("m2")

    mine, theirs = run(tmp_path / "w.sqlite3", go)
    assert mine.memory_used["pastChats"]["exchanges"] == [
        {"chatId": "other", "messageId": "b", "title": "Other"}
    ]
    assert mine.memory_used["pastChats"]["forgotten"] == 1
    assert mine.tool_rounds[0]["calls"][0]["result"] == storage.FORGOTTEN_CHAT
    assert "Gone words" not in json.dumps(mine.tool_rounds)
    # Another person's answers are not theirs to change (and could not have quoted it).
    assert theirs.tool_rounds[0]["calls"][0]["result"] == "Gone words"


# --------------------------------------------------------------------------- #
# over HTTP
# --------------------------------------------------------------------------- #


def signed_in(world: World, sub: str = "p-ada") -> Browser:
    person = world.browser()
    person.sign_in(sub)
    return person


def ask(world: World, person: Browser, chat: str, content: str) -> dict[str, Any]:
    sent = person.post(f"/api/chats/{chat}/messages", json={"content": content})
    assert sent.status_code == 201, sent.text
    assert person.wait_answer(chat)["status"] == "done"
    request: dict[str, Any] = world.gateway.requests[-1]
    return request


def _index(world: World, sub: str) -> dict[str, str]:
    """Each exchange the person's index holds: its words, joined."""
    path = world.data / "knowledge" / files.person_key(sub) / "index.sqlite3"
    if not path.is_file():
        return {}
    found: dict[str, str] = {}
    db = sqlite3.connect(path)
    try:
        rows = db.execute(
            "SELECT d.id, c.text FROM documents d JOIN chunks c ON c.doc_id = d.id "
            "WHERE d.scope = 'exchanges' ORDER BY d.id, c.ord"
        ).fetchall()
    except sqlite3.OperationalError:
        # The file exists a moment before its tables do (seen on Windows CI).
        return {}
    finally:
        db.close()
    for doc_id, text in rows:
        found[doc_id] = found.get(doc_id, "") + " " + text
    return found


def settle(world: World, sub: str, check: Callable[[dict[str, str]], bool]) -> dict[str, str]:
    deadline = time.perf_counter() + 30
    while True:
        held = _index(world, sub)
        if check(held) or time.perf_counter() > deadline:
            return held
        time.sleep(0.05)


def _users(request: dict[str, Any]) -> list[str]:
    return [m["content"] for m in request["messages"] if m["role"] == "user"]


def _say(world: World, *words: str) -> None:
    world.gateway.words = list(words)


def test_a_chat_is_found_by_its_words_and_follows_its_shown_version_and_title(
    world: World,
) -> None:
    ada, bo = signed_in(world), signed_in(world, "p-bo")
    _say(world, "Pastéis de nata at Manteigaria.")
    trip = ada.new_chat()
    ask(world, ada, trip, "Where do I find the best custard tarts in Lisbon?")
    held = settle(world, "p-ada", lambda h: len(h) == 1)
    [(first_id, words)] = held.items()
    assert "custard tarts" in words and "Manteigaria" in words
    found = ada.get("/api/past-chats", params={"q": "custard tarts"}).json()
    [hit] = found["results"]
    assert hit["chatId"] == trip and hit["messageId"] == first_id
    assert hit["question"].startswith("Where do I find") and "Manteigaria" in hit["answer"]
    # Never another person's, and an empty search lists nothing.
    assert bo.get("/api/past-chats", params={"q": "custard tarts"}).json()["results"] == []
    assert ada.get("/api/past-chats", params={"q": ""}).json()["results"] == []
    # Try again: the shown version's exchange replaces the old one.
    _say(world, "Try Pastelaria Alcôa instead.")
    again = ada.post(f"/api/chats/{trip}/retry")
    assert again.status_code == 201, again.text
    ada.wait_answer(trip)
    held = settle(world, "p-ada", lambda h: first_id not in h and len(h) == 1)
    assert first_id not in held and "Alcôa" in next(iter(held.values()))
    # Choosing the first version back brings its exchange back.
    assert ada.post(f"/api/chats/{trip}/messages/{first_id}/choose").status_code == 200
    held = settle(world, "p-ada", lambda h: list(h) == [first_id])
    assert list(held) == [first_id]
    # A rename is put again, with the title in the heading the words weigh.
    ada.patch(f"/api/chats/{trip}", json={"title": "Bakery crawl"})
    settle(world, "p-ada", lambda h: True)
    deadline = time.perf_counter() + 30
    while not ada.get("/api/past-chats", params={"q": "bakery crawl"}).json()["results"]:
        assert time.perf_counter() < deadline, "the renamed chat is found by its title"
        time.sleep(0.05)


def test_use_past_chats_is_off_by_default_and_then_carries_a_match_from_another_chat(
    world: World,
) -> None:
    ada = signed_in(world)
    _say(world, "Your bicycle is a blue Brompton.")
    old = ada.new_chat()
    ask(world, ada, old, "Which folding bicycle did I buy last spring?")
    settle(world, "p-ada", lambda h: len(h) == 1)
    _say(world, "Noted.")
    chat = ada.new_chat()
    off = ask(world, ada, chat, "Remind me about my folding bicycle.")
    assert all("<past-chats-" not in u for u in _users(off))
    assert ada.get(f"/api/chats/{chat}/memory").json()["pastChats"]["on"] is False
    ada.patch(f"/api/chats/{chat}", json={"settings": {"pastChats": True}})
    on = ask(world, ada, chat, "Which folding bicycle do I have?")
    latest = _users(on)[-1]
    assert latest.startswith("<past-chats-") and "blue Brompton" in latest
    assert latest.endswith("Which folding bicycle do I have?")
    assert "Remind me about my folding bicycle" not in latest  # never this chat's own
    # Earlier turns are replayed as the person wrote them.
    assert _users(on)[0] == "Remind me about my folding bicycle."
    answers = [
        m for m in ada.get(f"/api/chats/{chat}").json()["messages"] if m["role"] == "assistant"
    ]
    [sent] = answers[-1]["memoryUsed"]["pastChats"]["exchanges"]
    assert sent["chatId"] == old and "Brompton" not in json.dumps(sent)
    drawer = ada.get(f"/api/chats/{chat}/memory").json()
    assert drawer["pastChats"]["on"] is True and drawer["turn"]["pastChats"]["exchanges"]


def test_search_chats_runs_and_is_withheld_after_the_chat_moves_outside(world: World) -> None:
    ada = signed_in(world)
    _say(world, "The cabin is at Lake Tahoe.")
    old = ada.new_chat()
    ask(world, ada, old, "Where is the cabin we rented in July?")
    settle(world, "p-ada", lambda h: len(h) == 1)
    world.gateway.more_chat_models = [chat_model("cloud", locality="external", owned_by="openai")]
    world.gateway.mode = "tools"
    world.gateway.tool_arguments = '{"query": "cabin"}'
    _say(world, "Let me look.")  # this chat's own answers name nothing
    chat = ada.new_chat()
    ask(world, ada, chat, "Where was that cabin?")
    first = next(r for r in world.gateway.requests if r.get("tools"))
    assert [t["function"]["name"] for t in first["tools"]] == ["search_chats"]
    after_tool = world.gateway.requests[-1]
    [result] = [m["content"] for m in after_tool["messages"] if m["role"] == "tool"]
    assert result.startswith("<past-chats-") and "Lake Tahoe" in result
    world.gateway.mode = "answer"
    ada.put("/api/memory/prefs", json={"external": "always"})
    ada.put("/api/documents/prefs", json={"external": "never"})
    ada.patch(f"/api/chats/{chat}", json={"model": "cloud"})
    moved = ask(world, ada, chat, "And when?")
    # Notes may go to it (always), documents never may: past chats are held back (MV2-C1).
    assert "tools" not in moved or all(
        t["function"]["name"] != "search_chats" for t in moved["tools"]
    )
    [withheld] = [m["content"] for m in moved["messages"] if m["role"] == "tool"]
    assert withheld == CHATS_WITHHELD
    assert "tahoe" not in json.dumps(moved).lower() and "cabin\\" not in json.dumps(moved)
    drawer = ada.get(f"/api/chats/{chat}/memory").json()["pastChats"]
    assert drawer["allowed"] is False and drawer["reasonCode"] == "documents_never"


def test_deleting_a_chat_leaves_no_word_in_the_index_or_in_any_answer(world: World) -> None:
    ada = signed_in(world)
    _say(world, "Your locker code is at the gym desk.")
    old = ada.new_chat()
    ask(world, ada, old, "Where did I leave the swimming locker key?")
    settle(world, "p-ada", lambda h: len(h) == 1)
    world.gateway.mode = "tools"
    world.gateway.tool_arguments = '{"query": "locker"}'
    _say(world, "Let me look.")
    chat = ada.new_chat()
    ada.patch(f"/api/chats/{chat}", json={"settings": {"pastChats": True}})
    ask(world, ada, chat, "Where is my swimming locker key?")
    world.gateway.mode = "answer"
    assert "gym desk" in json.dumps(world.gateway.requests[-1])
    # An index that cannot let go: nothing is deleted.
    keeper = world.server.server.config.app.state.exchanges_index

    async def refuse(owner: str, chat_id: str) -> int:
        raise OSError("disk")

    remove = keeper.remove_chat
    keeper.remove_chat = refuse
    try:
        assert ada.delete(f"/api/chats/{old}").status_code == 503
    finally:
        keeper.remove_chat = remove
    assert ada.get(f"/api/chats/{old}").status_code == 200
    assert ada.delete(f"/api/chats/{old}").status_code == 204
    held = _index(world, "p-ada")
    assert all("locker key" not in words or "Where is my" in words for words in held.values())
    assert all("gym desk" not in words for words in held.values())
    after = ask(world, ada, chat, "And again?")
    assert "gym desk" not in json.dumps(after)
    [replayed] = [m["content"] for m in after["messages"] if m["role"] == "tool"]
    assert replayed == storage.FORGOTTEN_CHAT
    view = ada.get(f"/api/chats/{chat}").json()
    assert "gym desk" not in json.dumps(view)
    first = next(m for m in view["messages"] if m["role"] == "assistant")
    assert first["memoryUsed"]["pastChats"]["forgotten"] == 1
    assert ada.get("/api/past-chats", params={"q": "gym desk"}).json()["results"] == []


def test_an_exchange_the_index_still_holds_is_never_shown_once_its_chat_is_gone(
    world: World,
) -> None:
    ada = signed_in(world)
    _say(world, "The spare key is under the blue pot.")
    old = ada.new_chat()
    ask(world, ada, old, "Where is the spare house key?")
    settle(world, "p-ada", lambda h: len(h) == 1)
    keeper = world.server.server.config.app.state.exchanges_index

    async def lagging(owner: str, chat_id: str) -> int:
        return 0  # the index is not told: it still holds the exchange

    remove = keeper.remove_chat
    keeper.remove_chat = lagging
    try:
        assert ada.delete(f"/api/chats/{old}").status_code == 204
    finally:
        keeper.remove_chat = remove
    assert len(_index(world, "p-ada")) == 1  # still in the index...
    found = ada.get("/api/past-chats", params={"q": "spare house key"}).json()
    assert found["results"] == []  # ...and never shown: the store is read
    # The backfill takes it out.
    world.restart_workbench()
    assert settle(world, "p-ada", lambda h: not h) == {}


def test_the_backfill_indexes_chats_made_before_and_resumes_by_signature(world: World) -> None:
    ada = signed_in(world)
    _say(world, "Basil needs six hours of sun.")
    chats = []
    for crop in ("basil", "tomatoes", "mint"):
        chat = ada.new_chat()
        ask(world, ada, chat, f"How much sun do my {crop} need?")
        chats.append(chat)
    settle(world, "p-ada", lambda h: len(h) == 3)
    path = world.data / "knowledge" / files.person_key("p-ada") / "index.sqlite3"
    with sqlite3.connect(path) as db:
        # As if the index were new: no exchanges, and one chat's signature already kept.
        db.execute("DELETE FROM documents WHERE scope = 'exchanges'")
        db.execute(
            "DELETE FROM meta WHERE key LIKE 'exchanges:%' AND key != ?", (f"exchanges:{chats[0]}",)
        )
    world.restart_workbench()
    held = settle(world, "p-ada", lambda h: len(h) == 2)
    assert len(held) == 2, "the chat whose signature had not moved was skipped"
    assert all("basil" not in w for w in held.values())
    status = ada.get("/api/past-chats/status").json()
    assert status["exchanges"] == 2 and status["backfill"] == {"running": False}


def test_a_connection_searches_past_chats_only_with_the_right(world: World) -> None:
    ada = signed_in(world)
    _say(world, "The dentist is on Tuesday at nine.")
    chat = ada.new_chat()
    ask(world, ada, chat, "When is my dentist appointment?")
    settle(world, "p-ada", lambda h: len(h) == 1)
    without = _connect(ada, notes=True)
    assert call(world, without["token"], "search_chats", query="dentist").is_error
    made = _connect(ada, chats=True)
    assert made["connection"]["rights"]["chats"] is True
    answer = call(world, made["token"], "search_chats", query="dentist")
    assert not answer.is_error, answer
    [exchange] = answer.structured_content["exchanges"]
    assert exchange["chatId"] == chat and "Tuesday at nine" in exchange["answer"]
    bo = signed_in(world, "p-bo")
    theirs = _connect(bo, chats=True)
    found = call(world, theirs["token"], "search_chats", query="dentist")
    assert found.structured_content["exchanges"] == []


def test_rebuild_search_puts_the_past_chats_back(world: World) -> None:
    ada = signed_in(world)
    _say(world, "Plant garlic in October.")
    chat = ada.new_chat()
    ask(world, ada, chat, "When should I plant garlic cloves?")
    settle(world, "p-ada", lambda h: len(h) == 1)
    assert ada.post("/api/documents/rebuild").status_code == 200
    held = settle(world, "p-ada", lambda h: len(h) == 1)
    assert len(held) == 1 and "garlic" in next(iter(held.values()))
    assert ada.get("/api/past-chats", params={"q": "garlic"}).json()["results"]


def test_a_version_not_shown_is_never_read_and_a_delete_removes_twice(
    world: World, tmp_path: Path
) -> None:
    async def go(store: Store) -> dict[str, Any]:
        await store.create_chat(storage.Chat("c", "ada", "T", "m", 1.0, 1.0))
        await store.add_message(
            Message("u", "c", 0, "user", "done", 1.0, content="Q"), parent_id=None
        )
        for a in ("a1", "a2"):
            await store.add_message(
                Message(a, "c", 0, "assistant", "done", 1.0, content=a), parent_id="u"
            )
        return await store.exchange_rows("ada", ["a1", "a2"])

    assert set(run(tmp_path / "s.sqlite3", go)) == {"a2"}  # a1 is a version not shown
    ada = signed_in(world)
    chat = ada.new_chat()
    keeper = world.server.server.config.app.state.exchanges_index
    calls: list[str] = []
    remove = keeper.remove_chat

    async def counted(owner: str, chat_id: str) -> int:
        calls.append(chat_id)
        return int(await remove(owner, chat_id))

    keeper.remove_chat = counted
    try:
        assert ada.delete(f"/api/chats/{chat}").status_code == 204
    finally:
        keeper.remove_chat = remove
    # Before the store delete, and again after it (a sync that read the chat first).
    assert calls == [chat, chat]


def test_the_status_counts_every_passage_without_meaning_and_says_when_none_can_run(
    world: World,
) -> None:
    ada = signed_in(world)
    chat = ada.new_chat()
    ask(world, ada, chat, "How deep should tulip bulbs go?")
    settle(world, "p-ada", lambda h: len(h) == 1)
    path = world.data / "knowledge" / files.person_key("p-ada") / "index.sqlite3"
    with sqlite3.connect(path) as db:
        # As a restart leaves them before a search model is seen: found by words only.
        db.execute("UPDATE documents SET status = 'ready' WHERE scope = 'exchanges'")
        passages = db.execute(
            "SELECT SUM(chunks) FROM documents WHERE scope = 'exchanges'"
        ).fetchone()[0]
    status = ada.get("/api/past-chats/status").json()
    assert status["owed"] == passages > 0
    assert status["meaning"] == {"available": False, "why": past_chats.NO_MEANING}


def test_a_look_at_past_chats_wakes_meaning_left_owed_by_a_restart(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from eugene_plexus_workbench.knowledge import chunk
    from eugene_plexus_workbench.knowledge.index import Indexes

    async def go() -> list[str]:
        indexes = Indexes(tmp_path / "k")
        index = indexes.get("p-ada")
        assert index is not None
        passage = chunk.Passage(0, "a question", "T", None, None, None, None, 0, 10, 4)
        await index.put_exchange("ans00001", "chat0001", "sha", "T", [passage])
        await index.finish("ans00001")  # as a restart leaves it: ready, words only
        touched: list[str] = []

        async def resolve(*_: Any) -> Any:
            return SimpleNamespace(model=SimpleNamespace(local=True, id="m"))

        async def touch(found: Any) -> None:
            touched.append(found.sub)

        meaning = SimpleNamespace(resolve=resolve, touch=touch)
        saved = SimpleNamespace(index=indexes.get, meaning=meaning)
        status = await past_chats.status_view(SimpleNamespace(saved=saved), "p-ada")
        assert status["owed"] == 1 and status["meaning"]["available"] is True
        await indexes.aclose()
        return touched

    assert asyncio.run(go()) == ["p-ada"]
