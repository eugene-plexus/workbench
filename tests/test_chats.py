"""Chats and their answers (W1, W4, W6, W7, W11)."""

from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .conftest import ADMIN_TOKEN, MODEL, PNG, World


def _events(world: World, browser: Any, chat_id: str, out: list[dict[str, Any]],
            until: str = "done") -> threading.Thread:
    """A tab watching a chat, in a thread, collecting events until `until`."""

    def run() -> None:
        with browser.http.stream("GET", f"/api/chats/{chat_id}/events",
                                 headers=browser.headers(), timeout=30) as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    out.append(event)
                    if event.get("type") == until:
                        return

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    time.sleep(0.3)
    return thread


def test_an_answer_streams_to_every_tab_watching(world: World) -> None:
    world.gateway.delay = 0.05
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    first: list[dict[str, Any]] = []
    second: list[dict[str, Any]] = []
    tabs = [_events(world, ada, chat, first), _events(world, ada, chat, second)]
    sent = ada.post(f"/api/chats/{chat}/messages", json={"content": "Hi there"})
    assert sent.status_code == 201, sent.text
    for tab in tabs:
        tab.join(timeout=15)
    for events in (first, second):
        text = "".join(e.get("content", "") for e in events if e["type"] == "delta")
        assert text == "Hello from the model."
        assert events[-1]["type"] == "done" and events[-1]["message"]["status"] == "done"
        assert any(e["type"] == "progress" for e in events), "the prompt's progress is passed on"


def test_an_answer_keeps_going_with_no_tab_open(world: World) -> None:
    """Troy, C3 call 2: only Stop ends an answer."""
    world.gateway.delay = 0.1
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    seen: list[dict[str, Any]] = []
    tab = _events(world, ada, chat, seen, until="delta")
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Hi"})
    tab.join(timeout=10)
    assert seen and seen[-1]["type"] == "delta", "the tab saw the start, then closed"
    answer = ada.wait_answer(chat)
    assert answer["status"] == "done" and answer["content"] == "Hello from the model."


def test_a_tab_opened_mid_answer_gets_what_has_arrived_then_the_rest(world: World) -> None:
    world.gateway.delay = 0.15
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Hi"})
    time.sleep(0.4)
    late: list[dict[str, Any]] = []
    _events(world, ada, chat, late).join(timeout=15)
    assert late[0]["type"] == "answer", "a snapshot first"
    so_far = late[0]["message"]["content"]
    rest = "".join(e.get("content", "") for e in late if e["type"] == "delta")
    assert so_far and so_far + rest == "Hello from the model."


def test_stop_ends_an_answer_and_keeps_what_arrived(world: World) -> None:
    world.gateway.delay = 0.3
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Hi"})
    time.sleep(0.5)
    assert ada.post(f"/api/chats/{chat}/stop").status_code == 204
    answer = ada.get(f"/api/chats/{chat}").json()["messages"][-1]
    assert answer["status"] == "stopped"
    assert answer["content"] and answer["content"] != "Hello from the model."


def test_a_second_question_waits_for_the_first_answer(world: World) -> None:
    world.gateway.delay = 0.2
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Hi"})
    busy = ada.post(f"/api/chats/{chat}/messages", json={"content": "Again"})
    assert busy.status_code == 409 and "still being written" in busy.json()["detail"]["message"]


def test_the_whole_conversation_and_the_instructions_are_sent(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.patch(f"/api/chats/{chat}", json={"settings": {"instructions": "Be brief.",
                                                       "temperature": 0.2}})
    ada.post(f"/api/chats/{chat}/messages", json={"content": "One"})
    ada.wait_answer(chat)
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Two"})
    ada.wait_answer(chat)
    body = world.gateway.requests[-1]
    assert body["model"] == MODEL and body["stream"] is True
    assert body["stream_options"] == {"include_usage": True, "include_progress": True}
    assert body["temperature"] == 0.2 and "top_p" not in body, "unset means the model's own"
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
    assert body["messages"][0]["content"] == "Be brief."
    assert body["messages"][2]["content"] == "Hello from the model."
    assert "web_search_options" not in body


def test_try_again_replaces_the_last_answer(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "One"})
    first = ada.wait_answer(chat)
    world.gateway.words = ["Another", " answer."]
    assert ada.post(f"/api/chats/{chat}/retry").status_code == 201
    second = ada.wait_answer(chat)
    messages = ada.get(f"/api/chats/{chat}").json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert second["id"] != first["id"] and second["content"] == "Another answer."


def test_editing_a_message_replaces_it_and_everything_after(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "One"})
    ada.wait_answer(chat)
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Two"})
    ada.wait_answer(chat)
    first = ada.get(f"/api/chats/{chat}").json()["messages"][0]
    edited = ada.post(f"/api/chats/{chat}/messages/{first['id']}/edit", json={"content": "Uno"})
    assert edited.status_code == 201, edited.text
    ada.wait_answer(chat)
    messages = ada.get(f"/api/chats/{chat}").json()["messages"]
    assert [(m["role"], m["content"]) for m in messages][0] == ("user", "Uno")
    assert len(messages) == 2
    assert [m["content"] for m in world.gateway.requests[-1]["messages"]] == ["Uno"]


def test_the_first_message_names_the_chat(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "How do bees   make honey?"})
    ada.wait_answer(chat)
    assert ada.get(f"/api/chats/{chat}").json()["chat"]["title"] == "How do bees make honey?"


def test_a_stream_that_stops_without_its_end_is_a_failed_answer(world: World) -> None:
    world.gateway.mode = "cut"
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Hi"})
    answer = ada.wait_answer(chat)
    assert answer["status"] == "failed" and "stopped part-way" in answer["error"]
    assert answer["content"] == "Hello from the model.", "what arrived is kept"


# --------------------------------------------------------------------------- #
# search (W6)
# --------------------------------------------------------------------------- #


def test_the_search_switch_asks_as_any_client_does_and_the_sources_are_kept(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "News?", "search": True})
    answer = ada.wait_answer(chat)
    assert world.gateway.requests[-1]["web_search_options"] == {}
    assert answer["sources"] == [{"url": "https://example.org/page", "title": "A page"}]
    assert answer["searches"] == 1 and answer["reasoning"] == "Searching first."
    assert ada.get(f"/api/chats/{chat}").json()["chat"]["search"] is True, "the switch is kept"


def test_whether_search_can_run_comes_from_the_gateway_in_its_words(world: World) -> None:
    reason = "no search account is set up; add one under Backends, then Add a search account"
    world.gateway.search = {"available": False, "reason": reason}
    ada = world.browser()
    ada.sign_in("p-ada")
    listing = ada.get("/api/models").json()
    assert listing["webSearch"] == {"available": False, "reason": reason}
    assert [m["id"] for m in listing["models"]] == [MODEL], "only models that chat"
    assert listing["models"][0]["webSearch"] is True and listing["models"][0]["imageInput"] is True


def test_a_refused_search_is_shown_with_the_gateways_reason(world: World) -> None:
    world.gateway.mode = "refuse_search"
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "News?", "search": True})
    answer = ada.wait_answer(chat)
    assert answer["status"] == "failed"
    assert "tool scope does not include web_search" in answer["error"]


# --------------------------------------------------------------------------- #
# the key everyone shares (W11)
# --------------------------------------------------------------------------- #


def test_a_revoked_key_says_so(world: World) -> None:
    world.gateway.mode = "refuse_key"
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Hi"})
    answer = ada.wait_answer(chat)
    assert answer["status"] == "failed"
    assert "Eugene refused the key this Workbench uses" in answer["error"]
    assert "turned off in Eugene's console" in answer["error"]


def test_every_request_carries_the_apps_key_and_no_one_elses(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Hi"})
    assert ada.wait_answer(chat)["status"] == "done", "the fake gateway accepts only the app key"


# --------------------------------------------------------------------------- #
# people's chats are their own (W4)
# --------------------------------------------------------------------------- #


def test_chats_are_their_owners(world: World) -> None:
    ada, bo = world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Mine"})
    ada.wait_answer(chat)
    assert bo.get("/api/chats").json()["chats"] == []
    for call in (
        bo.get(f"/api/chats/{chat}"),
        bo.patch(f"/api/chats/{chat}", json={"title": "ha"}),
        bo.post(f"/api/chats/{chat}/messages", json={"content": "x"}),
        bo.post(f"/api/chats/{chat}/stop"),
        bo.delete(f"/api/chats/{chat}"),
        bo.get(f"/api/chats/{chat}/events"),
    ):
        assert call.status_code == 404, call.text
    missing = bo.get("/api/chats/does-not-exist")
    assert missing.json() == bo.get(f"/api/chats/{chat}").json(), "the same answer either way"


def _owner_reads(world: World, on: bool | None) -> httpx.Response:
    return httpx.patch(f"{world.workbench}/v1/config", json={"ownerReadsChats": on},
                       headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}, trust_env=False)


def test_the_owner_cannot_read_peoples_chats_by_default(world: World) -> None:
    owner, ada = world.browser(), world.browser()
    owner.sign_in("operator")
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    assert owner.get(f"/api/chats/{chat}").status_code == 404
    listing = owner.get("/api/people")
    assert listing.status_code == 403 and "their own" in listing.json()["detail"]["message"]
    assert ada.get("/api/me").json()["ownerReadsChats"] is False


def test_when_the_business_allows_it_the_owner_reads_and_the_person_is_told(
    world: World,
) -> None:
    """Troy, C3 call 3: each business decides."""
    owner, ada = world.browser(), world.browser()
    owner.sign_in("operator")
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Quarterly numbers"})
    ada.wait_answer(chat)
    assert _owner_reads(world, True).json()["applied"] == ["ownerReadsChats"]
    assert ada.get("/api/me").json()["ownerReadsChats"] is True, "the person sees the line"
    people = owner.get("/api/people").json()["people"]
    assert [p["name"] for p in people] == ["Ada"]
    chats = owner.get(f"/api/people/{people[0]['sub']}/chats").json()["chats"]
    assert [c["id"] for c in chats] == [chat] and chats[0]["readOnly"] is True
    read = owner.get(f"/api/chats/{chat}").json()
    assert read["chat"]["readOnly"] is True and read["ownerName"] == "Ada"
    assert read["messages"][0]["content"] == "Quarterly numbers"
    # Read, never written.
    assert owner.post(f"/api/chats/{chat}/messages", json={"content": "x"}).status_code == 404
    assert owner.delete(f"/api/chats/{chat}").status_code == 404
    # A member is never an owner, whatever the setting.
    assert ada.get("/api/people").status_code == 403
    # And back to the default.
    _owner_reads(world, None)
    assert owner.get(f"/api/chats/{chat}").status_code == 404


def test_settings_are_changed_only_with_the_agents_admin_token(world: World) -> None:
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        assert httpx.get(f"{world.workbench}/v1/config", headers=headers,
                         trust_env=False).status_code == 401
    good = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    schema = httpx.get(f"{world.workbench}/v1/config/schema", headers=good, trust_env=False).json()
    assert [f["key"] for f in schema["fields"]] == ["ownerReadsChats"]
    assert schema["fields"][0]["default"] is False
    assert httpx.get(f"{world.workbench}/v1/config", headers=good,
                     trust_env=False).json() == {"ownerReadsChats": False}
    result = httpx.patch(f"{world.workbench}/v1/config", headers=good, trust_env=False,
                         json={"ownerReadsChats": "yes", "colour": "red"}).json()
    assert result["applied"] == [] and {r["key"] for r in result["rejected"]} == {
        "ownerReadsChats", "colour"}


# --------------------------------------------------------------------------- #
# attachments (W7)
# --------------------------------------------------------------------------- #


def test_an_image_is_stored_by_id_and_sent_as_a_data_url(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    up = ada.post(f"/api/chats/{chat}/files", files={"file": ("dot.png", PNG, "image/png")})
    assert up.status_code == 201, up.text
    file_id = up.json()["id"]
    ada.post(f"/api/chats/{chat}/messages", json={"content": "What is this?",
                                                  "attachments": [file_id]})
    ada.wait_answer(chat)
    parts = world.gateway.requests[-1]["messages"][-1]["content"]
    assert parts[0] == {"type": "text", "text": "What is this?"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    stored = list((world.data / "files").rglob(file_id))
    assert len(stored) == 1 and stored[0].read_bytes() == PNG
    got = ada.get(f"/api/files/{file_id}")
    assert got.content == PNG and got.headers["content-security-policy"] == "sandbox"
    other = world.browser()
    other.sign_in("p-bo")
    assert other.get(f"/api/files/{file_id}").status_code == 404


def test_a_file_that_is_not_what_it_says_is_refused(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    fake = ada.post(f"/api/chats/{chat}/files",
                    files={"file": ("x.png", b"<svg onload=alert(1)>", "image/png")})
    assert fake.status_code == 415
    pdf_called_png = ada.post(f"/api/chats/{chat}/files",
                              files={"file": ("x.png", b"%PDF-1.7 ...", "image/png")})
    assert pdf_called_png.status_code == 415


def test_the_gateways_size_limits_are_said_before_an_upload(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    big = PNG + b"\0" * (5 * 1024 * 1024)
    refused = ada.post(f"/api/chats/{chat}/files", files={"file": ("big.png", big, "image/png")})
    assert refused.status_code == 413 and "5 MiB" in refused.json()["detail"]["message"]
    pdf = b"%PDF-1.7\n" + b"0" * (4 * 1024 * 1024)
    for _ in range(2):
        assert ada.post(f"/api/chats/{chat}/files",
                        files={"file": ("a.pdf", pdf, "application/pdf")}).status_code == 201
    full = ada.post(f"/api/chats/{chat}/files", files={"file": ("c.pdf", pdf, "application/pdf")})
    assert full.status_code == 413 and "Start a new chat" in full.json()["detail"]["message"]


def test_deleting_a_chat_deletes_its_files(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    file_id = ada.post(f"/api/chats/{chat}/files",
                       files={"file": ("dot.png", PNG, "image/png")}).json()["id"]
    assert ada.delete(f"/api/chats/{chat}").status_code == 204
    assert not list(Path(world.data / "files").rglob(file_id))
    assert ada.get(f"/api/files/{file_id}").status_code == 404
