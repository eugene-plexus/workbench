"""The page, its headers, and a restart mid-answer (W1, W5, W9)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from eugene_plexus_workbench import api
from eugene_plexus_workbench.store import Chat, Message, Store
from eugene_plexus_workbench.web import CSP, SCENE_CSP

from .conftest import World


def test_the_page_answers_every_address_the_app_reads_itself(world: World) -> None:
    for path in ("/", "/chats/abc123", "/anything/else"):
        page = httpx.get(world.workbench + path, trust_env=False)
        assert page.status_code == 200 and "<div id=root>" in page.text, path
    asset = httpx.get(world.workbench + "/assets/app.js", trust_env=False)
    assert asset.text == "console.log(1)" and "immutable" in asset.headers["cache-control"]


def test_an_api_address_that_does_not_exist_is_not_the_page(world: World) -> None:
    for path in ("/api/nothing", "/v1/nothing", "/oidc/nothing"):
        response = httpx.get(world.workbench + path, trust_env=False)
        assert response.status_code in (401, 404) and "<div id=root>" not in response.text, path


def test_a_path_that_climbs_out_of_the_build_is_the_page(world: World) -> None:
    secret = world.data.parent / "client_key"
    response = httpx.get(world.workbench + "/..%2Fclient_key", trust_env=False)
    assert secret.read_text(encoding="utf-8") not in response.text


def test_every_answer_carries_the_policy(world: World) -> None:
    """W5: scripts and styles from Workbench only, nothing inline, images only
    from Workbench, data: and blob:, and no framing."""
    for path in ("/", "/api/status", "/healthz"):
        headers = httpx.get(world.workbench + path, trust_env=False).headers
        assert headers["content-security-policy"] == CSP, path
        assert headers["x-frame-options"] == "DENY" and headers["referrer-policy"] == "no-referrer"
        assert headers["x-content-type-options"] == "nosniff"
    assert "img-src 'self' data: blob:" in CSP and "frame-ancestors 'none'" in CSP
    assert "script-src 'self'" in CSP and "'unsafe-inline'" not in CSP
    assert (
        httpx.get(world.workbench + "/api/status", trust_env=False).headers["cache-control"]
        == "no-store"
    )


def test_a_working_scene_may_run_its_own_style_and_nothing_else(world: World) -> None:
    """§6.1: a scene's animation is its inline style, which the page's policy
    would block in a browser that applies it to images."""
    assert world.settings.static_dir is not None
    scenes = world.settings.static_dir / "scenes"
    scenes.mkdir()
    (scenes / "eugene-test.svg").write_text("<svg/>", encoding="utf-8")
    (scenes / "notes.txt").write_text("x", encoding="utf-8")
    (world.settings.static_dir / "eugene-face.svg").write_text("<svg/>", encoding="utf-8")
    scene = httpx.get(world.workbench + "/scenes/eugene-test.svg", trust_env=False)
    assert scene.status_code == 200 and scene.headers["content-type"] == "image/svg+xml"
    assert scene.headers["content-security-policy"] == SCENE_CSP
    assert "default-src 'none'" in SCENE_CSP and "script-src" not in SCENE_CSP
    assert scene.headers["x-content-type-options"] == "nosniff"
    for path in ("/scenes/notes.txt", "/eugene-face.svg", "/"):
        headers = httpx.get(world.workbench + path, trust_env=False).headers
        assert headers["content-security-policy"] == CSP, path


def test_a_page_built_without_its_front_end_says_so(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from eugene_plexus_workbench.app import create_app
    from eugene_plexus_workbench.settings import Settings

    app = create_app(Settings(data_dir=tmp_path, static_dir=tmp_path / "nothing-here"))
    with TestClient(app) as client:
        page = client.get("/")
    assert page.status_code == 503 and "without its built front end" in page.text


async def test_a_restart_marks_an_answer_in_progress_interrupted(tmp_path: Path) -> None:
    store = Store(tmp_path / "wb.sqlite3")
    await store.open()
    now = time.time()
    await store.create_chat(
        Chat(id="c", owner="p", title="t", model="m", created_at=now, updated_at=now)
    )
    await store.add_message(
        Message(
            id="m1",
            chat_id="c",
            seq=0,
            role="assistant",
            status="running",
            created_at=now,
            content="Half an",
        ),
        parent_id=None,
    )
    await store.close()
    reopened = Store(tmp_path / "wb.sqlite3")
    await reopened.open()
    assert await reopened.mark_interrupted() == 1
    (kept,) = await reopened.messages("c")
    assert kept.status == "interrupted" and kept.content == "Half an"
    await reopened.close()


async def test_a_database_from_a_newer_workbench_is_refused(tmp_path: Path) -> None:
    store = Store(tmp_path / "wb.sqlite3")
    await store.open()
    await store._run(lambda: store._conn().execute("UPDATE meta SET value = '99'"))
    await store.close()
    newer = Store(tmp_path / "wb.sqlite3")
    with pytest.raises(RuntimeError, match="newer Workbench"):
        await newer.open()


def test_a_tab_left_open_is_signed_out_when_its_person_is(world: World, monkeypatch) -> None:
    """The session is checked again at every keepalive, so a person turned
    off in Eugene stops receiving even on a tab they left open."""
    monkeypatch.setattr(api, "_KEEPALIVE_SECONDS", 0.3)
    world.eugene.expires_in = 1
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    events = []
    with ada.http.stream(
        "GET", f"/api/chats/{chat}/events", headers=ada.headers(), timeout=10
    ) as response:
        assert response.status_code == 200
        lines = response.iter_lines()
        assert next(lines) == ": watching"
        # Turned off while the tab is open.
        world.eugene.disabled.add("p-ada")
        # A stream that is never ended is the defect, not a slow test.
        deadline = time.perf_counter() + 10
        for line in lines:
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
            if time.perf_counter() > deadline:
                break
    assert events and events[-1]["type"] == "signed-out"
    assert "no longer accepts" in events[-1]["message"]


def test_shutdown_marks_a_running_answer_interrupted_not_stopped(tmp_path: Path) -> None:
    from eugene_plexus_workbench.answers import Answers
    from eugene_plexus_workbench.hub import Hub

    class Slow(Hub):
        async def stream_chat(self, body):  # type: ignore[override]
            yield {"choices": [{"index": 0, "delta": {"content": "Part"}}]}
            await asyncio.sleep(30)

    async def run() -> str:
        store = Store(tmp_path / "wb.sqlite3")
        await store.open()
        now = time.time()
        await store.create_chat(
            Chat(id="c", owner="p", title="t", model="m", created_at=now, updated_at=now)
        )
        message = await store.add_message(
            Message(id="a", chat_id="c", seq=0, role="assistant", status="running", created_at=now),
            parent_id=None,
        )
        answers = Answers(store, Slow(None, None))
        answers.start("c", message, lambda: {})
        await asyncio.sleep(0.2)
        await answers.aclose()
        (saved,) = await store.messages("c")
        await store.close()
        return f"{saved.status}:{saved.content}"

    assert asyncio.run(run()) == "interrupted:Part"
