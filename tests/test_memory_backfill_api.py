"""Row 27a CI acceptance using fake Eugene, gateway and a fake MCP memory server.

No agent or engine starts. ServerThread allocates its loopback ports through the OS.
The specs acceptance must additionally cover a process kill on Windows and Linux.
"""

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any

import pytest

from eugene_plexus_workbench import memory_servers as ms

from .conftest import World
from .test_memory_mv7 import chat_in, project
from .test_memory_servers import Remote, connected, until
from .test_memory_servers import remote as remote
from .test_memory_v2 import ask, signed_in


def test_backfill_count_consent_restart_live_and_forget(
    world: World, remote: Callable[..., Remote], monkeypatch: pytest.MonkeyPatch
) -> None:
    ada = signed_in(world)
    plain = ada.new_chat()
    for i in range(12):
        ask(world, ada, plain, f"I grow beans in garden bed {i}.")
    ask(world, ada, plain, "My password is hunter2")
    private = chat_in(ada, project(ada, "Private garden"))
    ask(world, ada, private, "This stays in the project.")
    off = ada.new_chat()
    ada.patch(f"/api/chats/{off}", json={"settings": {"memory": "off"}}).raise_for_status()
    ask(world, ada, off, "This chat has memory off.")
    server = remote()
    connected(world, server, ada)
    preview = ada.get("/api/memory/server/backfill").json()
    assert preview["exchanges"] == 12 and preview["chats"] == 1
    assert preview["estimateSeconds"] is None and server.retained == []
    original = ms.Servers.call

    async def slow(self: ms.Servers, *args: Any, **kwargs: Any) -> Any:
        if args[2] == "retain":
            await asyncio.sleep(0.05)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ms.Servers, "call", slow)
    ada.post("/api/memory/server/backfill", json={}).raise_for_status()
    until(lambda: len(server.retained) >= 2)
    assert len(server.retained) < 12
    world.restart_workbench()
    live = ada.new_chat()
    ask(world, ada, live, "I prefer runner beans.")
    answer = ada.wait_answer(live)
    until(lambda: f"{live}:{answer['id']}" in server.memories)
    until(lambda: ada.get("/api/memory/server/backfill").json()["backfill"]["state"] == "done")
    ids = [r["document_id"] for r in server.retained]
    # A shutdown can interrupt a remote write before its reply is recorded. Its stable id
    # replaces that write on retry; the server must still hold exactly one of each exchange.
    assert len(set(ids)) == len(server.memories) == 13
    assert all(private not in id and off not in id for id in ids)
    assert all("hunter2" not in r["content"] for r in server.retained)
    ada.delete(f"/api/chats/{plain}").raise_for_status()
    until(lambda: len(server.forgotten) == 12)
    assert list(server.memories) == [f"{live}:{answer['id']}"]


def test_backfill_requires_the_persons_own_server_and_retain(
    world: World, remote: Callable[..., Remote]
) -> None:
    ada = signed_in(world)
    assert ada.post("/api/memory/server/backfill", json={}).status_code == 400
    server = remote({"recall"})
    connected(world, server, ada)
    assert ada.post("/api/memory/server/backfill", json={}).status_code == 400
    bo = signed_in(world, "p-bo")
    assert bo.get("/api/memory/server/backfill").json()["backfill"] is None
    assert bo.post("/api/memory/server/backfill", json={}).status_code == 400


def test_stop_waits_for_an_active_retain_before_returning(
    world: World, remote: Callable[..., Remote], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop's response is the boundary after which no backfill may arrive."""
    ada = signed_in(world)
    chat = ada.new_chat()
    for i in range(3):
        ask(world, ada, chat, f"I grow beans in bed {i}.")
    server = remote()
    connected(world, server, ada)
    began, release = threading.Event(), threading.Event()
    original = ms.Servers.call

    async def held(self: ms.Servers, *args: Any, **kwargs: Any) -> Any:
        if args[2] == "retain":
            began.set()
            await asyncio.to_thread(release.wait)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ms.Servers, "call", held)
    ada.post("/api/memory/server/backfill", json={}).raise_for_status()
    assert began.wait(timeout=5), "backfill did not start a retain"
    with ThreadPoolExecutor(max_workers=1) as pool:
        stopping = pool.submit(ada.delete, "/api/memory/server/backfill")
        try:
            until(
                lambda: (
                    ada.get("/api/memory/server/backfill").json()["backfill"]["state"] == "stopped"
                )
            )
            try:
                stopping.result(timeout=0.2)
                returned_while_held = True
            except TimeoutError:
                returned_while_held = False
        finally:
            release.set()
        response = stopping.result(timeout=5)
    response.raise_for_status()
    assert not returned_while_held, "DELETE returned while a retain could still arrive"
    assert len(server.retained) == 1
    assert response.json()["backfill"]["state"] == "stopped"
    assert response.json()["backfill"]["sent"] == 1
