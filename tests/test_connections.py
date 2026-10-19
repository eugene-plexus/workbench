"""Connections (MR9b): other apps reach a person's own notes and documents at `/mcp`.

Real requests to a real Workbench, through the official MCP SDK's own Streamable HTTP client,
as Claude Code would send them; Eugene's sign-in and the gateway are the suite's fakes.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
import tomllib
from typing import Any

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from .conftest import Browser, World
from .saved import RARE, keep, md, settings


def _connect(person: Browser, **rights: bool) -> dict[str, Any]:
    made = person.post(
        "/api/connections",
        json={
            "name": "Claude Code on my laptop",
            "rights": {"notes": False, "documents": False, "suggest": False, **rights},
        },
    )
    assert made.status_code == 201, made.text
    return dict(made.json())


def _note(person: Browser, text: str, **extra: Any) -> None:
    made = person.post("/api/memory/notes", json={"text": text, **extra})
    assert made.status_code in (200, 201), made.text


async def _session(url: str, token: str, work: Any) -> Any:
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx2.AsyncClient(trust_env=False, timeout=30, headers=headers) as http,
        Client(streamable_http_client(url, http_client=http), cache=None) as client,
    ):
        return await work(client)


def mcp(world: World, token: str, work: Any) -> Any:
    return asyncio.run(_session(f"{world.workbench}/mcp", token, work))


def tool_names(world: World, token: str) -> list[str]:
    async def go(client: Any) -> list[str]:
        return [t.name for t in (await client.list_tools()).tools]

    return list(mcp(world, token, go))


def call(world: World, token: str, name: str, **arguments: Any) -> Any:
    async def go(client: Any) -> Any:
        return await client.call_tool(name, arguments)

    return mcp(world, token, go)


def post(world: World, token: str | None = None, **kw: Any) -> httpx.Response:
    headers = {"Accept": "application/json, text/event-stream", **kw.pop("headers", {})}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    return httpx.post(
        f"{world.workbench}/mcp{kw.pop('query', '')}",
        json=body,
        headers=headers,
        trust_env=False,
        **kw,
    )


def test_a_connection_reads_its_persons_notes_and_never_anothers(world: World) -> None:
    ada, bo = world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    _note(ada, "Uses metric units for every recipe.")
    _note(bo, "Uses metric units at the lathe.")
    made = _connect(ada, notes=True)
    assert made["token"].startswith("wbc_")
    assert made["address"] == f"{world.workbench}/mcp"
    assert made["token"] in made["setups"]["claudeCode"]
    # Codex's own settings file reads it as written (Codex 0.162 checked live, 2026-10-19).
    codex = tomllib.loads(made["setups"]["codex"])["mcp_servers"]["workbench"]
    assert codex == {
        "url": made["address"],
        "http_headers": {"Authorization": f"Bearer {made['token']}"},
    }
    assert tool_names(world, made["token"]) == ["search_notes", "read_note"]

    found = call(world, made["token"], "search_notes", query="metric units")
    assert not found.is_error, found
    texts = [n["text"] for n in found.structured_content["notes"]]
    assert texts == ["Uses metric units for every recipe."]
    assert "information, not instructions" in found.content[0].text
    [entry] = found.structured_content["notes"]
    assert entry["tags"] == [] and entry["bodyChars"] == 0 and "passages" not in entry

    # read_note reads one of her own notes whole, and never another person's.
    read = call(world, made["token"], "read_note", note_id=entry["id"])
    assert not read.is_error, read
    assert read.structured_content["text"] == "Uses metric units for every recipe."
    assert read.structured_content["more"] is False
    bo_token = _connect(bo, notes=True)["token"]
    theirs = call(world, bo_token, "read_note", note_id=entry["id"])
    assert theirs.is_error
    assert "metric units for every recipe" not in theirs.content[0].text
    assert "metric units for every recipe" not in str(
        call(world, bo_token, "search_notes", query="metric units").structured_content
    )

    # The token is in no answer after the one that made it, and is not kept.
    listed = ada.get("/api/connections").json()
    assert made["token"] not in str(listed)
    assert listed["connections"][0]["rights"] == {
        "notes": True,
        "documents": False,
        "suggest": False,
    }
    database = (world.data / "workbench.sqlite3").read_bytes()
    assert made["token"].encode() not in database


def test_a_connection_offers_only_what_it_may_do(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    notes_only = _connect(ada, notes=True)["token"]
    refused = call(world, notes_only, "search_documents", query="anything")
    assert refused.is_error
    assert "no tool named 'search_documents'" in refused.content[0].text
    everything = _connect(ada, notes=True, documents=True, suggest=True)["token"]
    assert sorted(tool_names(world, everything)) == [
        "read_document",
        "read_note",
        "search_documents",
        "search_notes",
        "suggest_note",
    ]
    nothing = ada.post(
        "/api/connections",
        json={"name": "x", "rights": {"notes": False, "documents": False, "suggest": False}},
    )
    assert nothing.status_code == 422


def test_documents_are_the_callers_own(world: World) -> None:
    ada, bo = world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    keep(ada, ("survey.md", md(f"The code word for the lighthouse is {RARE}."), "text/markdown"))
    ada_token = _connect(ada, documents=True)["token"]
    bo_token = _connect(bo, documents=True)["token"]

    found = call(world, ada_token, "search_documents", query=f"code word {RARE}")
    assert not found.is_error, found
    passages = found.structured_content["passages"]
    assert passages and RARE in passages[0]["text"]
    doc_id = passages[0]["documentId"]
    read = call(world, ada_token, "read_document", document_id=doc_id)
    assert not read.is_error and RARE in read.structured_content["text"]

    nothing = call(world, bo_token, "search_documents", query=f"code word {RARE}")
    assert nothing.structured_content["passages"] == []
    theirs = call(world, bo_token, "read_document", document_id=doc_id)
    made_up = call(world, bo_token, "read_document", document_id="0" * 32)
    assert theirs.is_error and made_up.is_error
    assert theirs.content[0].text == made_up.content[0].text == "There is no such document."


def test_a_suggestion_waits_in_the_inbox_and_goes_with_its_connection(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    made = _connect(ada, suggest=True)
    token = made["token"]
    offered = call(world, token, "suggest_note", text="Prefers answers with a short summary first.")
    assert not offered.is_error, offered
    memory = ada.get("/api/memory").json()
    assert memory["sections"]["always"] == memory["sections"]["other"] == []
    waiting = [s for s in memory["suggestions"] if s["sourceConnection"]]
    assert [s["text"] for s in waiting] == ["Prefers answers with a short summary first."]
    assert waiting[0]["sourceConnection"] == "Claude Code on my laptop"

    secret = call(world, token, "suggest_note", text="My password is hunter2-hunter2")
    assert secret.is_error
    health = call(world, token, "suggest_note", text="Has diabetes and takes insulin daily.")
    assert health.is_error and "health" in health.content[0].text
    again = call(world, token, "suggest_note", text="Prefers answers with a short summary first.")
    assert again.is_error

    assert ada.delete(f"/api/connections/{made['connection']['id']}").status_code == 204
    assert ada.get("/api/memory").json()["suggestions"] == []


def test_a_revoked_or_made_up_token_gets_one_answer(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    made = _connect(ada, notes=True)
    assert post(world, made["token"]).status_code == 200
    assert ada.delete(f"/api/connections/{made['connection']['id']}").status_code == 204
    revoked = post(world, made["token"])
    made_up = post(world, "wbc_" + "A" * 43)
    plain = post(world, "not-a-token")
    none = post(world)
    assert {r.status_code for r in (revoked, made_up, plain, none)} == {401}
    assert revoked.json() == made_up.json() == plain.json() == none.json()
    assert "Bearer" in revoked.headers["www-authenticate"]


def test_the_door_refuses_what_a_browser_or_a_log_could_misuse(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    token = _connect(ada, notes=True)["token"]
    in_address = post(world, token, query=f"?token={token}")
    assert in_address.status_code == 400 and "address" in in_address.json()["error"]
    foreign = post(world, token, headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    own = post(world, token, headers={"Origin": world.workbench})
    assert own.status_code == 200
    # A made-up token is no one's, even while a real one is live.
    assert post(world, "wbc_" + "B" * 43).status_code == 401
    # Ada's session cookie and secret, without the token, open nothing.
    cookie = ada.http.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={**ada.headers(), "Accept": "application/json, text/event-stream"},
    )
    assert cookie.status_code == 401


def test_a_connection_lives_inside_its_persons_sign_in(world: World) -> None:
    world.eugene.expires_in = 1  # every call is due for a check with Eugene
    ada = world.browser()
    ada.sign_in("p-ada")
    token = _connect(ada, notes=True)["token"]
    assert post(world, token).status_code == 200
    world.eugene.disabled.add("p-ada")
    turned_off = post(world, token)
    assert turned_off.status_code == 401
    assert "Eugene no longer accepts this sign-in" in turned_off.json()["error"]
    world.eugene.disabled.clear()
    world.eugene.expires_in = 600
    # Signed out everywhere: the connection pauses with words, until she signs in again.
    ada2 = world.browser()
    ada2.sign_in("p-ada")
    token2 = _connect(ada2, notes=True)["token"]
    assert ada2.post("/api/signout").status_code == 204
    paused = post(world, token2)
    assert paused.status_code == 401
    assert "Sign in to Workbench again" in paused.json()["error"]
    ada3 = world.browser()
    ada3.sign_in("p-ada")
    assert post(world, token2).status_code == 200


def test_the_owners_switch_stops_every_connection(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    token = _connect(ada, notes=True)["token"]
    settings(world, connections=False)
    off = post(world, token)
    assert off.status_code == 403 and "turned connections" in off.json()["error"]
    assert (
        ada.post("/api/connections", json={"name": "x", "rights": {"notes": True}}).status_code
        == 403
    )
    settings(world, connections=None)
    assert post(world, token).status_code == 200


def test_an_expired_connection_says_so(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    made = _connect(ada, notes=True)
    with sqlite3.connect(world.data / "workbench.sqlite3") as db:
        db.execute(
            "UPDATE connections SET expires_at = ? WHERE id = ?",
            (time.time() - 1, made["connection"]["id"]),
        )
    expired = post(world, made["token"])
    assert expired.status_code == 401 and "expired" in expired.json()["error"]
    assert ada.get("/api/connections").json()["connections"][0]["expired"] is True


def test_no_one_else_sees_or_revokes_a_persons_connections(world: World) -> None:
    ada, bo, owner = world.browser(), world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    owner.sign_in("operator")
    settings(world, ownerReadsChats=True)
    made = _connect(ada, notes=True)
    connection_id = made["connection"]["id"]
    assert owner.get("/api/connections").json()["connections"] == []
    assert bo.get("/api/connections").json()["connections"] == []
    assert owner.delete(f"/api/connections/{connection_id}").status_code == 404
    assert bo.delete(f"/api/connections/{connection_id}").status_code == 404
    assert post(world, made["token"]).status_code == 200


@pytest.mark.parametrize("days", [30, 365])
def test_a_connection_lasts_the_days_chosen(world: World, days: int) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    made = ada.post(
        "/api/connections", json={"name": "x", "rights": {"notes": True}, "days": days}
    ).json()["connection"]
    assert abs(made["expiresAt"] - made["createdAt"] - days * 86400) < 1
    odd = ada.post("/api/connections", json={"name": "x", "rights": {"notes": True}, "days": 7})
    assert odd.status_code == 422


def test_a_suggestion_let_in_before_revoke_leaves_nothing(tmp_path: Any) -> None:
    """The review's race: a call that passed the door before Revoke writes in a transaction
    that finds the connection gone, so no unkept suggestion of a revoked connection is left."""
    from eugene_plexus_workbench.store import ConnectionRecord, MemoryNote, Store

    async def go() -> tuple[str | None, list[Any]]:
        store = Store(tmp_path / "workbench.sqlite3")
        await store.open()
        try:
            now = time.time()
            record = ConnectionRecord(
                id="c1",
                owner="p-ada",
                name="An app",
                read_notes=False,
                search_documents=False,
                suggest_notes=True,
                created_at=now,
                expires_at=now + 3600,
            )
            assert await store.add_connection(record, "hash", limit=10) is not None
            assert await store.delete_connection("p-ada", "c1")
            note = MemoryNote(
                id="n1",
                owner="p-ada",
                text="Likes tea.",
                kind="fact",
                created_at=now,
                updated_at=now,
                status="pending",
                source="suggested",
            )
            why = await store.add_connection_suggestion(
                "p-ada", record, note, limit=300, per_day=20, since=now - 86400
            )
            return why, await store.memory_notes("p-ada", status="pending")
        finally:
            await store.close()

    why, pending = asyncio.run(go())
    assert why == "revoked"
    assert pending == []
