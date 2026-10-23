"""Row 26: long chats that outgrow the context (`long_chats.py`, `summaries.py`).

A chat whose next request would pass 80% of its model's window is sent with a summary of a run of
its earlier messages in their place. The system message and the first exchange stay the same
bytes, the newest exchanges go as they are, and between summaries every request is the one before
it plus new messages. The messages themselves never change.
"""

from __future__ import annotations

import ast
import asyncio
import itertools
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from eugene_plexus_workbench import context, conversation, long_chats, summaries
from eugene_plexus_workbench import store as storage
from eugene_plexus_workbench.store import Chat, Message, Person, Store, SummaryRow

from .conftest import Browser, World
from .test_documents_api import attach
from .test_memory_api import config

BLOCK = re.compile(r'<earlier-turns-([0-9a-f]{8}) from="(\d+)" to="(\d+)" date="[0-9-]+">')
#: About 200 tokens each, as Workbench counts (600 characters).
FILLER = ("The garden plan has more beds, wider paths, a shed and a pond. " * 10)[:600]
ANSWER = ("Noted: beds, paths, the shed and the pond stay in the plan as said. " * 10)[:600]


def _start(world: World, window: int | None = 8192, **gateway: Any) -> tuple[Browser, str]:
    world.gateway.context_length = window
    world.gateway.usage = True
    world.gateway.words = [ANSWER]
    for key, value in gateway.items():
        setattr(world.gateway, key, value)
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    made = ada.patch(f"/api/chats/{chat}", json={"settings": {"instructions": "Be brief."}})
    assert made.status_code == 200, made.text
    return ada, chat


def _turn(person: Browser, chat: str, number: int, extra: str = "") -> dict[str, Any]:
    sent = person.post(
        f"/api/chats/{chat}/messages",
        json={"content": f"Turn {number}: {FILLER}{extra}"},
    )
    assert sent.status_code == 201, sent.text
    return person.wait_answer(chat)


def _talk(person: Browser, chat: str, turns: range) -> None:
    for number in turns:
        answer = _turn(person, chat, number)
        assert answer["status"] == "done", answer


def _blocks(body: dict[str, Any]) -> list[tuple[str, int, int]]:
    found = []
    for message in body["messages"]:
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        found += [(n, int(a), int(b)) for n, a, b in BLOCK.findall(text)]
    return found


def _store(world: World) -> Path:
    return world.data / "workbench.sqlite3"


def _summary_rows(world: World, chat: str) -> list[sqlite3.Row]:
    with sqlite3.connect(_store(world)) as db:
        db.row_factory = sqlite3.Row
        return db.execute("SELECT * FROM chat_summaries WHERE chat_id = ?", (chat,)).fetchall()


# --------------------------------------------------------------------------- #
# the request a long chat sends
# --------------------------------------------------------------------------- #


def test_a_chat_past_the_trigger_is_sent_with_a_summary(world: World) -> None:
    ada, chat = _start(world)
    _talk(ada, chat, range(1, 31))
    previous = world.gateway.requests[-1]
    answer = _turn(ada, chat, 31)
    assert answer["status"] == "done", answer
    sent = world.gateway.requests[-1]
    # (a) the system message and the first exchange are the same bytes as the request before
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][:3] == previous["messages"][:3]
    assert sent["messages"][1]["content"].startswith("Turn 1: ")
    # (b) the first user message after the head opens with the summary block
    assert sent["messages"][3]["role"] == "user"
    assert BLOCK.match(sent["messages"][3]["content"]), sent["messages"][3]["content"][:200]
    # (c) the newest exchanges go as they are
    assert sent["messages"][-1] == {"role": "user", "content": f"Turn 31: {FILLER}"}
    assert sent["messages"][-3] == {"role": "user", "content": f"Turn 30: {FILLER}"}
    assert sent["messages"][-2] == {"role": "assistant", "content": ANSWER}
    # (d) the request and the answer's room are within 80% of the window
    assert context.count_messages(sent["messages"]) + context.ANSWER_RESERVE <= 8192 * 80 // 100
    # (e) the store holds every message, unchanged
    shown = ada.get(f"/api/chats/{chat}").json()["messages"]
    assert len(shown) == 62
    for number in range(1, 32):
        assert shown[2 * (number - 1)]["content"] == f"Turn {number}: {FILLER}"
    assert all(m["content"] == ANSWER for m in shown[1::2])
    # The answer says what it was sent, and the chat folds the run.
    assert answer["summaries"] and answer["promptTokens"] and answer["contextLength"] == 8192
    detail = ada.get(f"/api/chats/{chat}").json()
    folded = detail["summaries"]
    assert folded and folded[0]["from"] == 3 and folded[0]["text"]
    assert [f["from"] for f in folded] == sorted(f["from"] for f in folded)


def test_summary_request_is_the_previous_prompt_plus_one_message(world: World) -> None:
    ada, chat = _start(world)
    _talk(ada, chat, range(1, 20))
    first = next(i for i, body in enumerate(world.gateway.requests) if _blocks(body))
    previous = world.gateway.requests[first - 1]
    asked = world.gateway.summary_requests[0]
    body = asked["body"]
    replayed = body["messages"][:-1]
    assert replayed == previous["messages"][: len(replayed)], "byte for byte, the same prefix"
    assert body["model"] == previous["model"]
    assert "tools" not in body and "web_search_options" not in body
    assert body["stream"] is False
    question = body["messages"][-1]
    assert question["role"] == "user" and question["content"].startswith("Summarise turns 3 to ")
    assert asked["person"], "the summary request carries the person's token, as answers do"
    # The run summarised ends with an answer, and what follows it in the next request is the
    # summary block opening a person's message.
    _, start, _ = _blocks(world.gateway.requests[first])[0]
    assert start == 3 and replayed[-1]["role"] == "assistant"


def test_turns_after_a_summary_only_append(world: World) -> None:
    ada, chat = _start(world)
    _talk(ada, chat, range(1, 26))
    pairs = 0
    bodies = world.gateway.requests
    for one, two in itertools.pairwise(bodies):
        if not _blocks(one) or _blocks(one) != _blocks(two):
            continue
        assert two["messages"][: len(one["messages"])] == one["messages"]
        assert len(two["messages"]) == len(one["messages"]) + 2
        pairs += 1
    assert pairs >= 3, "several turns went between summaries"


@pytest.mark.parametrize("overflow_as", ["status", "frame"])
def test_overflow_refusal_summarises_and_sends_again_once(world: World, overflow_as: str) -> None:
    # The window is not known: nothing is planned, and only the backend's refusal summarises.
    ada, chat = _start(world, window=None, refuse_over=12_000, overflow_as=overflow_as)
    number = 0
    while True:
        number += 1
        before = len(world.gateway.requests)
        answer = _turn(ada, chat, number)
        assert answer["status"] == "done", answer
        if len(world.gateway.requests) - before > 1:
            break
        assert number < 60, "the backend never refused"
    refused, again = world.gateway.requests[-2:]
    assert context.count_messages(refused["messages"]) > 12_000
    assert not _blocks(refused) and _blocks(again), "sent again once, with a summary"
    assert context.count_messages(again["messages"]) <= 12_000
    rows = _summary_rows(world, chat)
    assert rows and {r["how"] for r in rows} == {"overflow"}
    assert answer["summaries"]


def test_a_document_turn_is_never_summarised(world: World) -> None:
    ada, chat = _start(world)
    _talk(ada, chat, range(1, 3))
    report = attach(
        ada, chat, "report.md", b"# Report\n\nThe budget is 4,100 euros.\n", "text/markdown"
    )
    sent = ada.post(
        f"/api/chats/{chat}/messages",
        json={"content": f"Turn 3: {FILLER}", "attachments": [report]},
    )
    assert sent.status_code == 201, sent.text
    assert ada.wait_answer(chat)["status"] == "done"
    _talk(ada, chat, range(4, 26))
    last = world.gateway.requests[-1]
    assert _blocks(last), "the chat was summarised"
    with_document = [
        m for m in last["messages"] if m["role"] == "user" and "<document-" in str(m["content"])
    ]
    assert len(with_document) == 1 and f"Turn 3: {FILLER}" in with_document[0]["content"]
    for _, start, end in _blocks(last):
        assert not start <= 5 <= end, "the document's message is in no summary"


def test_a_version_switch_inside_the_range_leaves_the_summary_unused(world: World) -> None:
    ada, chat = _start(world)
    _talk(ada, chat, range(1, 20))
    shown = ada.get(f"/api/chats/{chat}").json()["messages"]
    rows = _summary_rows(world, chat)
    assert rows
    # Another version of message 5, inside the run: the run is off the path.
    fifth = shown[4]
    edited = ada.post(f"/api/chats/{chat}/messages/{fifth['id']}/edit", json={"content": "Again"})
    assert edited.status_code == 201, edited.text
    ada.wait_answer(chat)
    assert not _blocks(world.gateway.requests[-1])
    assert len(_summary_rows(world, chat)) == len(rows), "kept"
    # Back to the first version: the run is on the path again, and its summary is used.
    chose = ada.post(f"/api/chats/{chat}/messages/{fifth['id']}/choose")
    assert chose.status_code == 200, chose.text
    answer = _turn(ada, chat, 20)
    assert answer["status"] == "done"
    nonces = {n for n, _, _ in _blocks(world.gateway.requests[-1])}
    assert nonces & {r["nonce"] for r in rows}


def test_off_refuses_before_anything_is_kept(world: World) -> None:
    ada, chat = _start(world)
    _talk(ada, chat, range(1, 26))
    off = ada.patch(f"/api/chats/{chat}", json={"settings": {"summarise": False}})
    assert off.status_code == 200 and off.json()["settings"]["summarise"] is False
    count = len(ada.get(f"/api/chats/{chat}").json()["messages"])
    asked = len(world.gateway.requests)
    refused = ada.post(f"/api/chats/{chat}/messages", json={"content": f"Turn 26: {FILLER}"})
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "long_chat"
    assert detail["message"].startswith("This chat is about ")
    assert "Turn on summarising for this chat" in detail["message"]
    assert len(ada.get(f"/api/chats/{chat}").json()["messages"]) == count, "nothing was kept"
    assert len(world.gateway.requests) == asked, "nothing was sent"


def test_an_outside_model_summarises_at_its_ceiling(world: World) -> None:
    assert config(world, longChatsOutsideCeilingTokens=16_000).status_code == 200
    ada, chat = _start(world, window=1_000_000, locality="external")
    long = FILLER * 3
    for number in range(1, 30):
        sent = ada.post(f"/api/chats/{chat}/messages", json={"content": f"Turn {number}: {long}"})
        assert sent.status_code == 201, sent.text
        assert ada.wait_answer(chat)["status"] == "done"
        if _blocks(world.gateway.requests[-1]):
            break
    last = world.gateway.requests[-1]
    assert _blocks(last), "summarised under a window of a million, at the ceiling"
    assert context.count_messages(last["messages"]) <= 16_000
    view = ada.get(f"/api/chats/{chat}/long").json()
    assert view["window"] == 1_000_000 and view["trigger"] == 16_000
    assert view["ceiling"] == 16_000


def test_the_drawer_says_how_full_the_chat_is(world: World) -> None:
    ada, chat = _start(world)
    _talk(ada, chat, range(1, 4))
    view = ada.get(f"/api/chats/{chat}/long").json()
    assert view["window"] == 8192 and view["trigger"] == 8192 * 80 // 100
    assert view["percent"] == 80 and view["percentSetBy"] == "install"
    assert view["measured"] is True and view["about"] > 0
    assert view["on"] is True and view["summaries"] == 0
    assert config(world, longChatsSummariseAtPercent=60).status_code == 200
    assert ada.get(f"/api/chats/{chat}/long").json()["trigger"] == 8192 * 60 // 100


def test_a_groups_percent_comes_before_the_installs(world: World) -> None:
    world.eugene.claims["p-ada"] = {
        "eugene_groups": [{"id": "staff", "name": "Staff"}],
        "eugene_policies": {
            "summaryContextPercent": 55,
            "setBy": {"summaryContextPercent": ["staff"]},
        },
    }
    ada, chat = _start(world)
    view = ada.get(f"/api/chats/{chat}/long").json()
    assert view["percent"] == 55 and view["percentSetBy"] == "group"
    assert view["trigger"] == 8192 * 55 // 100


def test_deleting_a_chat_deletes_its_summaries(world: World) -> None:
    ada, chat = _start(world)
    _talk(ada, chat, range(1, 18))
    assert _summary_rows(world, chat)
    assert ada.delete(f"/api/chats/{chat}").status_code == 204
    assert not _summary_rows(world, chat)


def test_prompt_truncated_is_said(world: World) -> None:
    ada, chat = _start(world, window=16_384)
    _talk(ada, chat, range(1, 20))
    assert not _blocks(world.gateway.requests[-1]), "under the trigger"
    world.gateway.truncated = True
    answer = _turn(ada, chat, 20)
    assert answer["promptTruncated"] is True
    world.gateway.truncated = False
    _turn(ada, chat, 21)
    # Far under the trigger, the next request is summarised all the same.
    assert _blocks(world.gateway.requests[-1])


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #


async def _seed(store: Store, turns: int = 6) -> list[Message]:
    await store.upsert_person(Person("p", "Person", "member"))
    await store.create_chat(Chat("c", "p", "Chat", "m", 0, 0))
    out: list[Message] = []
    parent = None
    for n in range(turns):
        for role in ("user", "assistant"):
            m = Message(f"{role[0]}{n}", "c", 0, role, "done", float(n), content=f"{role} {n}")
            await store.add_message(m, parent_id=parent)
            parent = m.id
            out.append(m)
    return out


def _row(first: str, last: str, **values: Any) -> SummaryRow:
    return SummaryRow(
        id=values.pop("id", "s1"),
        chat_id="c",
        owner="p",
        first_id=first,
        last_id=last,
        digest=values.pop("digest", "d"),
        text="A summary.",
        nonce="00000000",
        tokens=50,
        source_tokens=400,
        model="m",
        made_at=0.0,
        **values,
    )


def test_migration_adds_usage_and_summaries(tmp_path: Path) -> None:
    assert storage.SCHEMA_VERSION == storage.LONG_CHATS_SCHEMA == 33
    assert set(storage._MIGRATIONS) == set(
        range(storage.OLDEST_SCHEMA + 1, storage.SCHEMA_VERSION + 1)
    )

    async def go() -> None:
        store = Store(tmp_path / "wb.sqlite3")
        await store.open()
        await _seed(store, 1)
        await store.close()
        with sqlite3.connect(store.path) as db:
            db.execute("DROP TABLE chat_summaries")
            for column in (
                "prompt_tokens",
                "completion_tokens",
                "cached_tokens",
                "context_length",
                "prompt_truncated",
                "summaries",
            ):
                db.execute(f"ALTER TABLE messages DROP COLUMN {column}")
            db.execute("UPDATE meta SET value = '30' WHERE key = 'schema'")
        store = Store(store.path)
        await store.open()
        try:
            kept = await store.message("a0")
            assert kept is not None and kept.content == "assistant 0" and kept.prompt_tokens is None
            await store.update_message("a0", prompt_tokens=10, context_length=8192, summaries=[])
            again = await store.message("a0")
            assert again is not None and again.prompt_tokens == 10 and again.summaries == []
            assert await store.add_summary(_row("u0", "a0"))
            assert [r.id for r in await store.chat_summaries("c")] == ["s1"]
            assert store.backup is not None and store.backup.name.endswith(".schema-30.bak")
        finally:
            await store.close()
        with sqlite3.connect(store.path) as db:
            assert db.execute("SELECT value FROM meta WHERE key='schema'").fetchone() == ("33",)

    asyncio.run(go())


@pytest.mark.asyncio
async def test_a_branch_delete_deletes_the_summaries_of_its_messages(tmp_path: Path) -> None:
    store = Store(tmp_path / "wb.sqlite3")
    await store.open()
    try:
        await _seed(store)
        assert await store.add_summary(_row("u1", "a2", id="covers"))
        assert await store.add_summary(_row("u1", "a1", id="before"))
        # Hidden for 30 days: kept, so Undo brings the summary back with its messages.
        await store.delete_branch("c", "u2", now=0)
        assert {r.id for r in await store.chat_summaries("c")} == {"covers", "before"}
        await store.purge_branch("u2", now=31 * 86400)
        assert {r.id for r in await store.chat_summaries("c")} == {"before"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_a_summary_is_not_kept_for_a_chat_that_is_gone(tmp_path: Path) -> None:
    store = Store(tmp_path / "wb.sqlite3")
    await store.open()
    try:
        await _seed(store, 2)
        await store.delete_chat("c")
        assert not await store.add_summary(_row("u0", "a0"))
    finally:
        await store.close()


# --------------------------------------------------------------------------- #
# what a summary may carry
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_summary_carrying_notes_is_not_sent_to_an_outside_model(tmp_path: Path) -> None:
    store = Store(tmp_path / "wb.sqlite3")
    await store.open()
    try:
        path = await _seed(store, 4)
        # The second answer used `search_memory`: what it found is the person's notes.
        rounds = [
            {
                "assistant": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "search_memory", "arguments": '{"q":"x"}'},
                        }
                    ],
                },
                "calls": [
                    {
                        "id": "call-1",
                        "serverId": "memory",
                        "tool": "search_memory",
                        "status": "done",
                        "result": "A note: the code word is ORCHID-42.",
                    }
                ],
            }
        ]
        await store.update_message("a1", tool_rounds=rounds)
        path = await store.messages("c")
        chat = await store.chat("c")
        assert chat is not None

        def inputs(notes: bool) -> summaries.Inputs:
            return summaries.Inputs(
                store=store,
                hub=None,  # type: ignore[arg-type]
                root=tmp_path,
                person=Person("p", "Person", "member"),
                chat=chat,
                history=path,
                records={},
                model="m",
                notes=None,
                allow_notes=notes,
                allow_chats=False,
            )

        local = inputs(True)
        run = path[2:4]
        carries = summaries._carries(local, run)
        assert carries == {"notes": True, "chats": False}
        digest = summaries._digest(local, run, carries)
        assert await store.add_summary(_row("u1", "a1", digest=digest, carries=carries))
        assert [v.row.id for v in await summaries._valid(local)] == ["s1"]
        assert await summaries._valid(inputs(False)) == [], "never to a model notes may not reach"
        # A run that carries nothing may go anywhere.
        plain = summaries._carries(inputs(False), path[4:6])
        assert plain == {"notes": False, "chats": False}
    finally:
        await store.close()


def test_a_summary_with_a_changed_run_is_not_used(tmp_path: Path) -> None:
    async def go() -> None:
        store = Store(tmp_path / "wb.sqlite3")
        await store.open()
        try:
            path = await _seed(store, 3)
            chat = await store.chat("c")
            assert chat is not None
            inputs = summaries.Inputs(
                store, None, tmp_path, Person("p", "P", "member"), chat, path, {}, "m", None,  # type: ignore[arg-type]
                False, False,
            )  # fmt: skip
            run = path[2:4]
            digest = summaries._digest(inputs, run, {})
            assert await store.add_summary(_row("u1", "a1", digest=digest))
            assert [v.row.id for v in await summaries._valid(inputs)] == ["s1"]
            await store.update_message("a1", content="changed")
            inputs.history = await store.messages("c")
            assert await summaries._valid(inputs) == []
        finally:
            await store.close()

    asyncio.run(go())


# --------------------------------------------------------------------------- #
# the rules
# --------------------------------------------------------------------------- #


def _items(sizes: list[int], pinned: set[int] | None = None) -> list[long_chats.Item]:
    return [
        long_chats.Item(
            f"m{i}", "user" if i % 2 == 0 else "assistant", size, i in (pinned or set())
        )
        for i, size in enumerate(sizes)
    ]


def _limits(window: int | None = 8192, **values: Any) -> long_chats.Limits:
    return long_chats.limits(
        window,
        percent=values.get("percent", 80),
        outside=values.get("outside", False),
        ceiling=values.get("ceiling", 128_000),
    )


def test_a_chat_under_the_trigger_is_sent_whole() -> None:
    plan = long_chats.plan(_items([200] * 20), system=10, limits=_limits(), reserve=1024)
    assert not plan.summarised and plan.refuse is None and plan.tokens == 4010


def test_a_chat_over_the_trigger_keeps_its_head_and_tail() -> None:
    items = _items([200] * 40)
    plan = long_chats.plan(items, system=10, limits=_limits(), reserve=1024)
    assert plan.make and not plan.use
    first, last = plan.make[0]
    assert first == 2, "the first exchange is the head"
    assert items[first].role == "user" and items[last].role == "assistant"
    assert last < 40 - 2048 // 400 * 2, "the newest exchanges are the tail"
    assert (
        plan.tokens + 1024 <= long_chats.limits(8192, percent=80, outside=False, ceiling=0).trigger
    )  # type: ignore[operator]


def test_a_large_first_exchange_keeps_only_its_first_message() -> None:
    items = _items([300, 2400] + [200] * 40)
    plan = long_chats.plan(items, system=10, limits=_limits(), reserve=1024)
    assert plan.make[0][0] == 1, "the head's answer joins the run"


def test_a_documents_exchange_splits_the_runs() -> None:
    items = _items([200] * 40, pinned={10})
    plan = long_chats.plan(items, system=10, limits=_limits(), reserve=1024)
    for first, last in plan.make:
        assert not first <= 10 <= last and not first <= 11 <= last


def test_a_summary_the_last_request_used_is_used_again() -> None:
    items = _items([200] * 30)
    kept = [long_chats.Kept("s", 2, 15, 300, sticky=True)]
    plan = long_chats.plan(items, system=10, limits=_limits(), reserve=1024, kept=kept)
    assert [k.id for k in plan.use] == ["s"] and not plan.make


def test_a_valid_summary_is_used_before_a_new_one_is_made() -> None:
    items = _items([200] * 30)
    kept = [long_chats.Kept("s", 2, 15, 300)]
    plan = long_chats.plan(items, system=10, limits=_limits(), reserve=1024, kept=kept)
    assert [k.id for k in plan.use] == ["s"] and not plan.make


def test_a_merge_is_used_before_its_parts() -> None:
    items = _items([200] * 40)
    kept = [
        long_chats.Kept("a", 2, 9, 300),
        long_chats.Kept("b", 10, 19, 300),
        long_chats.Kept("ab", 2, 19, 400),
    ]
    plan = long_chats.plan(items, system=10, limits=_limits(), reserve=1024, kept=kept)
    assert [k.id for k in plan.use] == ["ab"]


def test_off_and_too_small_refuse_only_when_the_window_is_passed() -> None:
    items = _items([250] * 40)
    off = long_chats.plan(items, system=10, limits=_limits(), reserve=1024, enabled=False)
    assert off.refuse == "off" and not off.summarised
    under = long_chats.plan(
        _items([200] * 30), system=10, limits=_limits(), reserve=1024, enabled=False
    )
    assert under.refuse is None, "over the trigger but within the window: sent as it is"
    # Before a backend has counted the chat, the estimate is taken as high as it is meant to be.
    unmeasured = long_chats.plan(
        _items([250] * 36), system=10, limits=_limits(), reserve=1024, enabled=False, floor=0.75
    )
    assert unmeasured.whole > 8192 and unmeasured.refuse is None
    small = long_chats.plan(_items([200] * 40), system=10, limits=_limits(4096), reserve=512)
    assert small.refuse == "too_small"


def test_no_room_when_the_head_and_documents_fill_the_window() -> None:
    items = _items([200, 200, 7500, 200, 200, 200], pinned={2})
    plan = long_chats.plan(items, system=10, limits=_limits(), reserve=1024)
    assert plan.refuse == "no_room" and plan.documents
    words = long_chats.no_room_words("qwen3-8b", plan.fixed, 8192, ["report.pdf"])
    assert words.startswith("The first message and attached documents fill about ")
    assert "Search it instead for report.pdf" in words


def test_force_summarises_under_the_trigger() -> None:
    items = _items([200] * 26)
    plain = long_chats.plan(items, system=10, limits=_limits(), reserve=1024)
    assert not plain.summarised
    forced = long_chats.plan(items, system=10, limits=_limits(), reserve=1024, force=True)
    assert forced.make


def test_a_run_too_long_for_one_request_is_summarised_in_pieces() -> None:
    items = _items([200, 200] + [3000] * 20 + [200] * 4)
    plan = long_chats.plan(items, system=10, limits=_limits(32_768), reserve=1024)
    assert len(plan.make) > 1
    limit = 32_768 * long_chats.REQUEST_SHARE_PERCENT // 100
    for first, last in plan.make:
        assert sum(i.tokens for i in items[first : last + 1]) < limit


def test_limits_take_the_ceiling_for_a_model_outside() -> None:
    inside = _limits(1_000_000)
    assert inside.trigger == 800_000 and inside.ceiling is None
    outside = _limits(1_000_000, outside=True)
    assert outside.trigger == 128_000 and outside.ceiling == 128_000
    assert outside.span == 160_000
    unknown = _limits(None, outside=True)
    assert unknown.trigger == 128_000
    assert _limits(None).trigger is None
    assert _limits(1_000_000, outside=True, ceiling=0).trigger == 800_000


def test_the_scale_is_bounded() -> None:
    assert long_chats.scale_of(None, 100) == 1.0
    assert long_chats.scale_of(75, 100) == 0.75
    assert long_chats.scale_of(10, 100) == long_chats.SCALE_LOW
    assert long_chats.scale_of(1000, 100) == long_chats.SCALE_HIGH


def test_merging_takes_the_oldest_two_next_to_each_other() -> None:
    used = [
        long_chats.Kept("a", 2, 9, 700),
        long_chats.Kept("b", 10, 19, 700),
        long_chats.Kept("c", 20, 29, 700),
    ]
    assert long_chats.merge_pair(used, 8192) == (0, 1)
    assert long_chats.merge_pair(used[:2], 8192) is None, "within a quarter of the window"
    apart = [long_chats.Kept("a", 2, 9, 1500), long_chats.Kept("c", 12, 29, 1500)]
    assert long_chats.merge_pair(apart, 8192) is None


def test_a_summary_block_is_the_same_bytes_every_turn() -> None:
    one = long_chats.render_block("abcd1234", 3, 18, 1_792_540_800, "qwen3-8b", "Text.", False)
    assert one == long_chats.render_block(
        "abcd1234", 3, 18, 1_792_540_800, "qwen3-8b", "Text.", False
    )
    assert one.startswith('<earlier-turns-abcd1234 from="3" to="18" date="2026-10-21">\n')
    assert "A summary of turns 3 to 18 of this conversation, written by qwen3-8b." in one
    assert one.endswith("</earlier-turns-abcd1234>")
    assert "cut at its limit" in long_chats.render_block("n", 1, 2, 0, "m", "T", True)


def test_a_thinking_models_preamble_is_not_the_summary() -> None:
    assert long_chats.clean_text("<think>hmm</think>\n\nThe summary.") == "The summary."


def test_placement_opens_the_next_persons_message() -> None:
    chat = Chat("c", "p", "Chat", "m", 0, 0)
    path = [
        Message(f"{r[0]}{n}", "c", 0, r, "done", 0, content=f"{r} {n}")
        for n in range(4)
        for r in ("user", "assistant")
    ]
    placed = [conversation.Placed("u1", "a2", "BLOCK")]
    body = conversation.request_for(
        chat, path, [], root=Path("."), model="m", search=False, summaries=placed
    )
    assert [m["content"] for m in body["messages"]] == [
        "user 0", "assistant 0", "BLOCK\n\nuser 3", "assistant 3",
    ]  # fmt: skip
    # After a person's message with no answer sent, the block stands in an answer's place.
    head_only = [conversation.Placed("a0", "a1", "BLOCK")]
    body = conversation.request_for(
        chat, path, [], root=Path("."), model="m", search=False, summaries=head_only
    )
    assert [m["role"] for m in body["messages"]][:3] == ["user", "assistant", "user"]
    assert body["messages"][1]["content"] == "BLOCK"


def test_overflow_words_are_read() -> None:
    words = (
        "The backend rejected the request: request (9120 tokens) exceeds the available context "
        "size (8192 tokens), try increasing it"
    )
    assert long_chats.is_overflow(words) and long_chats.window_said(words) == 8192
    assert long_chats.window_said("maximum context length is 4096 tokens. However") == 4096
    assert not long_chats.is_overflow("This key was revoked.")


def _gateway_words() -> tuple[str, ...]:
    """The gateway's own list, from a checkout beside this one, else from GitHub."""
    local = Path(os.environ.get("EP_ROOT", Path(__file__).resolve().parents[2])) / "gateway"
    source = local / "src" / "eugene_plexus_gateway" / "responses.py"
    if source.is_file():
        text = source.read_text(encoding="utf-8")
    else:
        url = (
            "https://raw.githubusercontent.com/eugene-plexus/gateway/main/"
            "src/eugene_plexus_gateway/responses.py"
        )
        text = httpx.get(url, timeout=30, follow_redirects=True).raise_for_status().text
    found = re.search(r"_CONTEXT_WORDS = (\([^)]*\))", text)
    assert found, "the gateway names its overflow words _CONTEXT_WORDS"
    words: tuple[str, ...] = ast.literal_eval(found.group(1))
    return words


def test_overflow_words_match_the_gateways() -> None:
    assert _gateway_words() == long_chats.OVERFLOW_WORDS
