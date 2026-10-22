"""Artifacts (workbench-rich-content-roadmap.md RC5): a block of an answer kept
when the person first changes it, every save a new revision, each version of an
answer with its own, readable as its chat is, and gone with it."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import artifacts
from eugene_plexus_workbench.app import DATABASE
from eugene_plexus_workbench.content import normalize
from eugene_plexus_workbench.store import Message, Store
from eugene_plexus_workbench.web import CSP, PREVIEW_CSP

from .conftest import World
from .test_job_sites import _owner_reads, finish, offer, site_world  # noqa: F401

PAGE = "<!doctype html>\n<h1>Prices</h1>\n<script>document.title = 'x'</script>"
ANSWER = f'Here it is:\n\n```html artifact title="Prices"\n{PAGE}\n```\n\nAnd a list:\n\n- one'
# A fence inside a list is indented: the block's lines are the answer's, set in.
LISTED = "- First:\n\n  ```svg\n  <svg/>\n  <g/>\n  ```\n"


def answered(world: World, ada: Any, words: str) -> tuple[str, dict[str, Any]]:
    chat = ada.new_chat()
    world.gateway.words = [words]
    sent = ada.post(f"/api/chats/{chat}/messages", json={"content": "Make it"})
    assert sent.status_code == 201, sent.text
    return chat, ada.wait_answer(chat)


def keep(ada: Any, chat: str, message: str, **body: Any) -> Any:
    return ada.post(
        f"/api/chats/{chat}/messages/{message}/artifacts",
        json={"kind": "html", "title": "Prices", "original": PAGE, **body},
    )


# --- the number that finds a block again -----------------------------------------


@pytest.mark.parametrize(
    ("words", "number"),
    [
        # The same values web/src/lib/artifacts.test.ts expects of the page's `digest`.
        ("", "0-bdcb81aee8d83"),
        ("<svg/>", "6-1afcc7b26c5050"),
        ("é 😀\n", "5-13aaf5a05f19cb"),
    ],
)
def test_a_block_s_number_is_the_one_the_page_computes(words: str, number: str) -> None:
    assert artifacts.digest(words) == number


def test_an_answer_holds_its_block_even_set_in_by_a_list() -> None:
    def message(content: str) -> Message:
        return Message(
            id="a",
            chat_id="c",
            seq=1,
            role="assistant",
            status="done",
            created_at=0,
            content=content,
        )

    assert artifacts.found_in(PAGE, message(ANSWER)) == {"origin": "answer"}
    assert artifacts.found_in("<svg/>\n<g/>", message(LISTED))
    assert not artifacts.found_in("<svg>made up</svg>", message(ANSWER))
    # Its lines, but apart or in another order, are not the answer's block; nor is nothing.
    assert not artifacts.found_in("- one\n<h1>Prices</h1>", message(ANSWER))
    assert not artifacts.found_in("<g/>\n<svg/>", message(LISTED))
    assert not artifacts.found_in("", message(ANSWER))
    assert artifacts.found_in(PAGE.replace("\n", "\r\n"), message(ANSWER))


def test_a_block_that_is_not_text_is_refused_and_nothing_is_kept(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat, message = answered(world, ada, ANSWER)
    # A lone surrogate, as JSON may carry one.
    broken = ada.post(
        f"/api/chats/{chat}/messages/{message['id']}/artifacts",
        content='{"kind": "html", "original": "x", "source": "a\\ud800b"}',
        headers={"Content-Type": "application/json"},
    )
    # Refused as not the request's shape: never an error of the server's.
    assert broken.status_code == 422, broken.text
    empty = keep(ada, chat, message["id"], original="", source="x")
    assert empty.status_code == 422
    assert ada.get(f"/api/chats/{chat}").json()["messages"][-1]["artifacts"] == []


def test_a_title_is_one_short_line_and_never_empty() -> None:
    assert artifacts.title_of("  Two\n lines  ", "html") == "Two lines"
    assert artifacts.title_of("", "svg") == "Picture"
    assert len(artifacts.title_of("x" * 500, "react")) == artifacts.MAX_TITLE


# --- keeping one -----------------------------------------------------------------


def test_an_edit_keeps_the_block_as_revision_one_and_itself_as_two(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat, message = answered(world, ada, ANSWER)
    assert message["artifacts"] == [], "nothing is kept before it is changed"
    made = keep(ada, chat, message["id"], source=PAGE + "\n<p>Mine</p>")
    assert made.status_code == 201, made.text
    view = made.json()
    assert view["kind"] == "html" and view["title"] == "Prices"
    assert view["digest"] == artifacts.digest(PAGE)
    assert [(r["number"], r["author"]) for r in view["revisionList"]] == [
        (1, "answer"),
        (2, "person"),
    ]
    one = ada.get(f"/api/chats/{chat}/artifacts/{view['id']}/revisions/1").json()
    two = ada.get(f"/api/chats/{chat}/artifacts/{view['id']}/revisions/2").json()
    assert one["source"] == PAGE and two["source"] == PAGE + "\n<p>Mine</p>"
    # The chat says so under its message, for the page to find the block again.
    shown = ada.get(f"/api/chats/{chat}").json()["messages"][-1]["artifacts"]
    assert [(a["id"], a["revisions"], a["digest"]) for a in shown] == [
        (view["id"], 2, artifacts.digest(PAGE))
    ]


def test_keeping_the_same_block_again_adds_to_it_and_nothing_is_written_over(
    world: World,
) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat, message = answered(world, ada, ANSWER)
    first = keep(ada, chat, message["id"], source="A")
    # Kept again from a page that knew only the answer's block: its edit began before
    # revision 2, so it is refused, saying so, and nothing is written over.
    stale = keep(ada, chat, message["id"], source="B")
    assert stale.status_code == 409 and "Revision 2 was saved" in stale.json()["detail"]["message"]
    again = keep(ada, chat, message["id"], source="B", base=2)
    assert first.status_code == 201 and again.status_code == 200, again.text
    assert again.json()["id"] == first.json()["id"]
    path = f"/api/chats/{chat}/artifacts/{first.json()['id']}/revisions"
    same = ada.post(path, json={"source": "B"})
    assert same.status_code == 201 and same.json()["revisions"] == 3, "the same words, no new one"
    behind = ada.post(path, json={"source": "C", "base": 2})
    assert behind.status_code == 409, behind.text
    newer = ada.post(path, json={"source": "C", "base": 3})
    assert [r["number"] for r in newer.json()["revisionList"]] == [1, 2, 3, 4]
    sources = [ada.get(f"{path}/{n}").json()["source"] for n in (1, 2, 3, 4)]
    assert sources == [PAGE, "A", "B", "C"]


def test_a_block_the_answer_does_not_hold_is_refused(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat, message = answered(world, ada, ANSWER)
    made = keep(ada, chat, message["id"], original="<p>never written</p>", source="<p>x</p>")
    assert made.status_code == 422, made.text
    assert (
        made.json()["detail"]["message"] == "This block is not in the answer or its tools' results"
    )
    assert ada.get(f"/api/chats/{chat}").json()["messages"][-1]["artifacts"] == []


@pytest.mark.parametrize("where", ["result", "text", "resource"])
def test_block_from_tool_result_is_kept_with_origin_tool(world: World, where: str) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat, message = answered(world, ada, "The tool made a page.")
    call: dict[str, Any] = {
        "id": "fetch",
        "serverId": "pages",
        "serverName": "Pages",
        "tool": "fetch_page",
        "arguments": {"invented": "<p>not a result</p>"},
        "status": "done",
        "result": "Page made.",
    }
    if where == "result":
        call["result"] = PAGE
    elif where == "text":
        call["content"] = [
            {"id": "fetch:0", "type": "text", "text": PAGE, "source": "file:///page.html"}
        ]
        call["contentVersion"] = 1
    else:
        call["content"] = [
            {"type": "resource", "resource": {"text": PAGE, "mimeType": "text/html"}}
        ]

    async def returned() -> None:
        store = Store(world.settings.data_dir / DATABASE)
        await store.open()
        try:
            await store.update_message(message["id"], tool_rounds=[{"calls": [call]}])
        finally:
            await store.close()

    asyncio.run(returned())
    made = keep(ada, chat, message["id"], source=PAGE + "\n<p>Edited</p>")
    assert made.status_code == 201, made.text
    view = made.json()
    assert (view["origin"], view["tool"]) == ("tool", "pages/fetch_page")
    assert view["revisionList"][0]["author"] == "answer"
    path = f"/api/chats/{chat}/artifacts/{view['id']}"
    reread = ada.get(path).json()
    assert (reread["origin"], reread["tool"]) == ("tool", "pages/fetch_page")
    first = ada.get(f"{path}/revisions/1").json()
    assert first["source"] == PAGE and first["author"] == "answer"
    assert (first["origin"], first["tool"]) == ("tool", "pages/fetch_page")
    assert ada.get(f"{path}/revisions/2").json()["author"] == "person"
    # Arguments are not results, and a block in neither place must be refused.
    for absent in ("<p>not a result</p>", "<p>in neither place</p>"):
        refused = keep(ada, chat, message["id"], original=absent)
        assert refused.status_code == 422, refused.text
        assert (
            refused.json()["detail"]["message"]
            == "This block is not in the answer or its tools' results"
        )
    assert len(ada.get(f"/api/chats/{chat}").json()["messages"][-1]["artifacts"]) == 1


@pytest.mark.parametrize("where", ["text", "resource"])
@pytest.mark.parametrize("mime", ["text/html", "image/svg+xml", "text/markdown; charset=utf-8"])
def test_tool_text_keeps_mime_type_for_artifacts(where: str, mime: str) -> None:
    # Plain Markdown has no syntax to sniff, and a resource need not have a file extension.
    text = "Words from the tool.\n\n"
    raw: dict[str, Any] = {"type": "text", "text": text, "mimeType": mime}
    if where == "resource":
        raw = {"type": "resource", "resource": {"uri": "tool:///report", **raw}}
    blocks, result = asyncio.run(normalize("fetch", [raw]))
    assert blocks[0]["mimeType"] == mime
    assert blocks[0]["text"] == result == text


def test_each_version_of_an_answer_keeps_its_own(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat, first = answered(world, ada, ANSWER)
    keep(ada, chat, first["id"], source="first's")
    retried = ada.post(f"/api/chats/{chat}/retry")
    assert retried.status_code == 201, retried.text
    second = ada.wait_answer(chat)
    assert second["id"] != first["id"] and second["artifacts"] == []
    keep(ada, chat, second["id"], source="second's")
    by = {
        m["id"]: m["artifacts"]
        for via in (first["id"], second["id"])
        for m in ada.get(f"/api/chats/{chat}?via={via}").json()["messages"]
        if m["role"] == "assistant"
    }
    assert len(by[first["id"]]) == 1 and len(by[second["id"]]) == 1
    assert by[first["id"]][0]["id"] != by[second["id"]][0]["id"]


def test_an_answer_being_written_keeps_nothing_yet(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    world.gateway.words = [ANSWER] + ["more "] * 200
    world.gateway.delay = 0.02
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Make it"})
    running = ada.get(f"/api/chats/{chat}").json()["messages"][-1]
    assert running["status"] == "running"
    refused = keep(ada, chat, running["id"], source="early")
    assert refused.status_code == 409
    assert "still being written" in refused.json()["detail"]["message"]
    world.gateway.delay = 0
    ada.post(f"/api/chats/{chat}/stop")


def test_an_artifact_keeps_only_so_much(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat, message = answered(world, ada, ANSWER + "\n<p>another</p>\n<p>third</p>")
    big = ada.post(
        f"/api/chats/{chat}/messages/{message['id']}/artifacts",
        json={"kind": "html", "original": PAGE, "source": "é" * (artifacts.MAX_SOURCE // 2 + 1)},
    )
    assert big.status_code == 413 and "KB" in big.json()["detail"]["message"]
    monkeypatch.setattr(artifacts, "MAX_REVISIONS", 3)
    view = keep(ada, chat, message["id"], source="two").json()
    path = f"/api/chats/{chat}/artifacts/{view['id']}/revisions"
    assert ada.post(path, json={"source": "three"}).status_code == 201
    over = ada.post(path, json={"source": "four"})
    assert over.status_code == 409 and "3 revisions" in over.json()["detail"]["message"]
    monkeypatch.setattr(artifacts, "MAX_PER_MESSAGE", 1)
    other = keep(ada, chat, message["id"], original="<p>another</p>", source="x")
    assert other.status_code == 409 and "artifacts already" in other.json()["detail"]["message"]
    monkeypatch.setattr(artifacts, "MAX_PERSON_BYTES", 10)
    full = ada.post(f"/api/chats/{chat}/artifacts/{view['id']}/revisions", json={"source": "y"})
    assert full.status_code == 409, full.text
    # A first save whose edit is past a limit keeps nothing, not the block alone.
    monkeypatch.setattr(artifacts, "MAX_PER_MESSAGE", 20)
    monkeypatch.setattr(artifacts, "MAX_PERSON_BYTES", artifacts.MAX_SOURCE * 4)
    monkeypatch.setattr(artifacts, "MAX_REVISIONS", 1)
    whole = keep(ada, chat, message["id"], original="<p>third</p>", source="edit")
    assert whole.status_code == 409, whole.text
    assert len(ada.get(f"/api/chats/{chat}").json()["messages"][-1]["artifacts"]) == 1


# --- whose it is -------------------------------------------------------------------


def test_another_person_finds_no_artifact_and_the_owner_only_reads(world: World) -> None:
    owner, ada, bo = world.browser(), world.browser(), world.browser()
    owner.sign_in("operator")
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    chat, message = answered(world, ada, ANSWER)
    view = keep(ada, chat, message["id"], source="mine").json()
    one = f"/api/chats/{chat}/artifacts/{view['id']}"
    for path in (one, f"{one}/revisions/2"):
        assert bo.get(path).status_code == 404
    assert bo.post(f"{one}/revisions", json={"source": "bo's"}).status_code == 404
    assert keep(bo, chat, message["id"], source="bo's").status_code == 404
    assert owner.get(one).status_code == 404, "not until the owner may read chats"
    _owner_reads(world)
    assert owner.get(f"{one}/revisions/2").json()["source"] == "mine"
    assert owner.post(f"{one}/revisions", json={"source": "owner's"}).status_code == 404
    assert keep(owner, chat, message["id"], source="owner's").status_code == 404


def test_an_artifact_of_a_hidden_message_is_hidden_from_the_install_owner(
    site_world: tuple[World, dict[str, Any]],  # noqa: F811 (the fixture, imported)
) -> None:
    world, _ = site_world
    owner, ada = world.browser(), world.browser()
    owner.sign_in("operator")
    ada.sign_in("p-ada")
    chat, before = answered(world, ada, ANSWER)
    early = keep(ada, chat, before["id"], source="before the site").json()
    chosen = ada.patch(
        f"/api/chats/{chat}", json={"settings": {"folderGrants": ["node:site-notes"]}}
    )
    assert chosen.status_code == 200, chosen.text
    world.gateway.mode = "tools"
    world.gateway.words = [ANSWER]
    finish(ada, chat, offer(world, ada, chat, "read_text", folder="Notes", path="note.txt"))
    after = ada.get(f"/api/chats/{chat}").json()["messages"][-1]
    late = keep(ada, chat, after["id"], source="after the site")
    assert late.status_code == 201, late.text
    _owner_reads(world)
    assert owner.get(f"/api/chats/{chat}/artifacts/{early['id']}").status_code == 200
    assert owner.get(f"/api/chats/{chat}/artifacts/{late.json()['id']}").status_code == 404
    shown = owner.get(f"/api/chats/{chat}").json()["messages"][-1]
    assert shown["redacted"] and shown["artifacts"] == []


def test_deleting_the_chat_deletes_its_artifacts(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat, message = answered(world, ada, ANSWER)
    view = keep(ada, chat, message["id"], source="gone soon").json()
    assert ada.delete(f"/api/chats/{chat}").status_code == 204
    assert ada.get(f"/api/chats/{chat}/artifacts/{view['id']}").status_code == 404
    assert asyncio.run(_kept(world, view["id"])) == (None, [])


async def _kept(world: World, artifact_id: str) -> tuple[Any, list[Any]]:
    store = Store(world.settings.data_dir / DATABASE)
    await store.open()
    try:
        return await store.artifact(artifact_id), await store.revisions(artifact_id)
    finally:
        await store.close()


# --- the preview's page ------------------------------------------------------------


def test_the_preview_is_a_page_of_its_own_and_the_chat_s_policy_is_unchanged(
    world: World, tmp_path: Path
) -> None:
    ada = world.browser()
    frame = ada.get("/preview/frame")
    assert frame.status_code == 200
    assert frame.headers["content-security-policy"] == PREVIEW_CSP
    assert "sandbox allow-scripts allow-forms" in PREVIEW_CSP
    assert "allow-same-origin" not in PREVIEW_CSP and "allow-top-navigation" not in PREVIEW_CSP
    # A form's script runs; its sending is refused here if the preview's script missed it.
    assert "form-action 'none'" in PREVIEW_CSP
    assert "connect-src 'none'" in PREVIEW_CSP and "frame-ancestors 'self'" in PREVIEW_CSP
    assert frame.headers["x-frame-options"] == "SAMEORIGIN"
    assert '<script src="/preview/runtime.js"></script>' in frame.text
    page = ada.get("/chats/x")
    assert page.headers["content-security-policy"] == CSP
    assert page.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in CSP and "script-src 'self';" in CSP + ";"


def test_a_proxy_in_front_is_told_to_leave_the_pages_as_written(world: World) -> None:
    # Cloudflare adds its analytics script to a page it may transform: refused by the
    # page's policy, and in a preview reported as the preview's own load.
    assert world.settings.static_dir is not None
    world.settings.static_dir.mkdir(parents=True, exist_ok=True)
    (world.settings.static_dir / "index.html").write_text("<!doctype html>", encoding="utf-8")
    ada = world.browser()
    for path in ("/preview/frame", "/chats/x", "/"):
        response = ada.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-cache, no-transform", path


def test_previews_run_when_shown_until_the_person_turns_it_off(world: World) -> None:
    ada, bo = world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    assert ada.get("/api/me").json()["runPreviews"] is True, "on unless turned off"
    off = ada.put("/api/me/run-previews", json={"on": False})
    assert off.status_code == 200 and off.json() == {"runPreviews": False}
    assert ada.get("/api/me").json()["runPreviews"] is False
    assert bo.get("/api/me").json()["runPreviews"] is True, "a person's own"
    assert ada.put("/api/me/run-previews", json={"on": "yes"}).status_code == 422
    ada.put("/api/me/run-previews", json={"on": True})
    assert ada.get("/api/me").json()["runPreviews"] is True
