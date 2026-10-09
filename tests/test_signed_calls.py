"""J14b in Workbench: a job site that checks the person's own signature holds
each call that needs it, and Workbench shows what the site says to sign,
waits, and sends the call again until the site runs it or the person
declines (`person-held-keys.md` §13)."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from eugene_plexus_workbench import answers

from . import test_node_folders
from .conftest import World
from .test_node_folders import select
from .test_tools import decide

#: The fake site of `test_node_folders`, as a fixture of this module too.
remote_world = test_node_folders.remote_world

WRITE = {"folder": "Notes", "path": "note.txt", "text": "Changed", "expectedSha256": "a" * 64}


def signing(browser: Any, chat: str) -> dict[str, Any]:
    deadline = time.perf_counter() + 10
    last: dict[str, Any] = {}
    while time.perf_counter() < deadline:
        last = browser.get(f"/api/chats/{chat}").json()["messages"][-1]
        calls = [c for r in last.get("toolRounds") or [] for c in r["calls"]]
        if calls and calls[0]["status"] == "signing":
            return last
        assert last["status"] == "running", last
        time.sleep(0.03)
    raise AssertionError(last)


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(answers, "_SIGN_POLL_SECONDS", 0.2)


def start(world: World, remote: dict[str, Any]) -> tuple[Any, str, dict[str, Any]]:
    remote["signed"] = True
    ada, _, chat = select(world)
    world.gateway.tool_name = "write_text"
    world.gateway.tool_arguments = json.dumps(WRITE)
    assert ada.post(f"/api/chats/{chat}/messages", json={"content": "Write."}).status_code == 201
    return ada, chat, signing(ada, chat)


def test_a_held_call_shows_the_sites_words_and_runs_once_signed_at_the_machine(
    remote_world: tuple[World, dict[str, Any]], quick: None
) -> None:
    world, remote = remote_world
    ada, chat, message = start(world, remote)
    call = message["toolRounds"][0]["calls"][0]
    # Nothing was asked here first: the site holds it and says what to sign.
    assert call["ask"] is False and call["signed"] is True
    assert call["held"]["words"][0].startswith("Write the file note.txt")
    assert call["held"]["approvePage"] == "http://127.0.0.1:8079/link/approve"
    assert remote["calls"] == [] and remote["content"] == "Desktop notes"
    # Asked again while the person signs, naming what the site holds.
    time.sleep(0.5)
    assert any((t.get("approval") or {}).get("held") == call["held"]["id"] for t in remote["tries"])
    remote["signed_ids"].add(call["held"]["id"])
    answer = ada.wait_answer(chat)
    assert answer["status"] == "done", answer
    ran = answer["toolRounds"][0]["calls"][0]
    assert ran["status"] == "done" and "held" not in ran
    assert remote["content"] == "Changed"


def test_saying_it_was_signed_sends_it_again_at_once(
    remote_world: tuple[World, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(answers, "_SIGN_POLL_SECONDS", 30.0)
    world, remote = remote_world
    ada, chat, message = start(world, remote)
    remote["signed_ids"].add(message["toolRounds"][0]["calls"][0]["held"]["id"])
    started = time.perf_counter()
    assert decide(ada, chat, message, True).status_code == 204
    answer = ada.wait_answer(chat)
    assert answer["status"] == "done" and time.perf_counter() - started < 10


def test_declining_a_held_call_runs_nothing(
    remote_world: tuple[World, dict[str, Any]], quick: None
) -> None:
    world, remote = remote_world
    ada, chat, message = start(world, remote)
    assert decide(ada, chat, message, False).status_code == 204
    answer = ada.wait_answer(chat)
    declined = answer["toolRounds"][0]["calls"][0]
    assert declined["status"] == "declined" and "did not sign" in declined["result"]
    assert remote["calls"] == [] and remote["content"] == "Desktop notes"


def test_signing_expires_after_its_time(
    remote_world: tuple[World, dict[str, Any]], quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(answers, "_APPROVAL_SECONDS", 1.0)
    world, remote = remote_world
    ada, chat, _ = start(world, remote)
    answer = ada.wait_answer(chat)
    expired = answer["toolRounds"][0]["calls"][0]
    assert expired["status"] == "declined" and "expired" in expired["result"]
    assert remote["calls"] == []
