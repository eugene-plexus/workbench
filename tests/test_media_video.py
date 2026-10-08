"""The Video screen's API: a video as a long job (workbench-media-screens.md
§5, M11).

Real Workbench and a fake gateway that plays OpenRouter's video API as P5
measured it: a job is `queued`, then `completed`, with progress 0 then 100
and `prompt: null`, and a finished one carries what the provider billed. The
Foreman polls each running job from the server, keeps its handle so a
restart polls it again, and keeps the MP4 the moment the job is done. Every
test here fails against Workbench before slice 3, which had no video screen.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import time
from collections.abc import Iterator
from typing import Any

import pytest

from eugene_plexus_workbench import files, media

from .conftest import MP4_CLIP, Browser, World, png

GROK = "x-ai/grok-imagine-video"


@pytest.fixture(autouse=True)
def quick_foreman(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The Foreman asks every 5 s; here, every 50 ms."""
    monkeypatch.setattr(media, "POLL_SECONDS", 0.05)
    yield


def _ada(world: World) -> Browser:
    ada = world.browser()
    ada.sign_in("p-ada")
    return ada


def _make(person: Browser, **fields: Any) -> dict[str, Any]:
    body = {"model": GROK, "prompt": "a red ball bouncing", "seconds": 1, "size": "854x480"}
    made = person.post("/api/media/video", json={**body, **fields})
    assert made.status_code == 201, made.text
    return dict(made.json())


def _wait(person: Browser, media_id: str, seconds: float = 15) -> dict[str, Any]:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        item = person.get(f"/api/media/{media_id}").json()
        if item["status"] != "running":
            return dict(item)
        time.sleep(0.05)
    raise AssertionError("the video job did not end")


def _until(check: Any, what: str, seconds: float = 10) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError(what)


def test_the_video_screen_lists_video_models_with_their_settings_and_prices(
    world: World,
) -> None:
    ada = _ada(world)
    models = {m["id"]: m for m in ada.get("/api/media/doors").json()["doors"]["video"]["models"]}
    assert set(models) == {GROK, "bytedance/seedance"}, "only models with the video surface"
    grok = models[GROK]
    assert grok["durations"] == list(range(1, 16)) and grok["firstFrame"] is True
    assert grok["sizes"] == ["1280x720", "480x854", "854x480"]
    assert (grok["provider"], grok["locality"]) == ("OpenRouter", "external")
    # The gateway's snake_case becomes the page's; unset (null) fields are absent.
    assert grok["prices"][1] == {
        "sku": "cents_per_video_output_second_480p",
        "per": "second",
        "usd": 0.05,
        "resolution": "480p",
        "sizes": ["480x854", "854x480"],
    }
    # No price listed is null, which the page says as such, never as free.
    assert models["bytedance/seedance"]["prices"] is None


def test_a_video_is_submitted_polled_and_kept_on_the_server(world: World) -> None:
    ada = _ada(world)
    started = _make(ada)
    assert started["status"] == "running" and started["door"] == "video"
    item = _wait(ada, started["id"])
    assert item["status"] == "done", item
    assert world.gateway.video_requests == [
        {"model": GROK, "prompt": "a red ball bouncing", "seconds": "1", "size": "854x480"}
    ]
    # Queued once, then completed: two polls of the one handle.
    assert world.gateway.video_polls == ["video_1", "video_1"]
    [kept] = item["files"]
    assert kept["mediaType"] == "video/mp4" and kept["size"] == len(MP4_CLIP)
    assert ada.get(f"/api/files/{kept['id']}").content == MP4_CLIP
    part = ada.get(f"/api/files/{kept['id']}", headers={"Range": "bytes=4-7"})
    assert part.status_code == 206 and part.content == b"ftyp", "a video seeks"
    # What came back, and what the provider billed (§6.4).
    assert item["units"] == {"seconds": 1, "size": "854x480", "costUsd": 0.05}
    assert item["served"]["driver"] == "openrouter"
    assert item["served"]["requestId"] == "video-req-1"
    assert item["request"] == {
        "model": GROK,
        "prompt": "a red ball bouncing",
        "seconds": 1,
        "size": "854x480",
    }


def test_a_first_frame_comes_from_the_persons_own_bin(world: World) -> None:
    ada = _ada(world)
    frame = png(4, 4)
    brought = ada.post(
        "/api/media/images/upload", files={"file": ("frame.png", frame, "image/png")}
    ).json()
    frame_id = brought["files"][0]["id"]
    item = _wait(ada, _make(ada, firstFrame=frame_id)["id"])
    assert item["status"] == "done", item
    sent = world.gateway.video_requests[-1]["input_reference"]
    assert sent == {"image_url": "data:image/png;base64," + base64.b64encode(frame).decode()}
    # Another person's image is not a frame anyone else can send.
    bo = world.browser()
    bo.sign_in("p-bo")
    refused = bo.post(
        "/api/media/video", json={"model": GROK, "prompt": "x", "firstFrame": frame_id}
    )
    assert refused.status_code == 400 and "first frame is no longer in your bins" in refused.text
    assert len(world.gateway.video_requests) == 1, "nothing was sent for Bo"


def test_a_refused_submit_is_the_gateways_words_with_its_field(world: World) -> None:
    ada = _ada(world)
    words = "seconds: no model serving 'x-ai/grok-imagine-video' makes 99 s. Nothing was sent."
    world.gateway.video_refusal = (400, words, "seconds")
    item = _wait(ada, _make(ada, seconds=99)["id"])
    assert item["status"] == "failed" and item["files"] == []
    assert item["error"] == {"message": words, "param": "seconds", "status": 400}
    assert world.gateway.video_polls == [], "a refused job has no handle to poll"


def test_a_failed_job_ends_in_the_providers_words_with_what_it_billed(world: World) -> None:
    ada = _ada(world)
    world.gateway.video_end = "failed"
    item = _wait(ada, _make(ada)["id"])
    assert item["status"] == "failed" and item["files"] == []
    assert item["error"]["message"] == "The provider could not make this video."
    assert item["units"]["costUsd"] == 0.05


def test_a_handle_the_gateway_no_longer_finds_ends_saying_it_may_have_been_billed(
    world: World,
) -> None:
    """A handle names the key that made it: after the app's key is made
    again, the gateway says it has no such job for this key (§5)."""
    ada = _ada(world)
    started = _make(ada)
    _until(lambda: world.gateway.video_polls, "the Foreman never polled")
    world.gateway.video_jobs.clear()
    item = _wait(ada, started["id"])
    assert item["status"] == "failed", item
    message = item["error"]["message"]
    assert message.startswith("No video job 'video_1' for this key.")
    assert media.MAY_HAVE_BILLED in message
    assert item["error"]["status"] == 404


def test_a_poll_that_can_be_asked_again_is_said_on_the_job_and_asked_again(
    world: World,
) -> None:
    ada = _ada(world)
    trouble = "The backend that holds this job ('openrouter') is not reachable right now."
    world.gateway.video_poll_trouble = [(503, trouble), (503, trouble)]
    world.gateway.video_polls_queued = 0
    item = _wait(ada, _make(ada)["id"])
    assert item["status"] == "done", item
    assert len(world.gateway.video_polls) == 3, "two 503s, then the answer"
    assert item["job"]["problem"] is None, "a good poll clears the trouble"


def test_the_view_carries_the_job_state_and_its_trouble(world: World) -> None:
    ada = _ada(world)
    trouble = "Workbench cannot reach Eugene's gateway right now."
    world.gateway.video_poll_trouble = [(503, trouble)] * 1000
    started = _make(ada)
    _until(lambda: len(world.gateway.video_polls) >= 2, "the Foreman stopped asking")
    item = ada.get(f"/api/media/{started['id']}").json()
    assert item["status"] == "running"
    assert item["job"]["problem"] == trouble and item["job"]["polls"] >= 2
    # The handle stays on the server: only the app's key can use it anyway.
    assert "handle" not in item["job"]
    world.gateway.video_poll_trouble.clear()
    assert _wait(ada, started["id"])["status"] == "done"


def test_stop_ends_the_job_and_the_polling(world: World) -> None:
    ada = _ada(world)
    world.gateway.video_polls_queued = 10_000
    started = _make(ada)
    _until(lambda: world.gateway.video_polls, "the Foreman never polled")
    assert ada.post(f"/api/media/{started['id']}/stop").status_code == 204
    item = ada.get(f"/api/media/{started['id']}").json()
    assert item["status"] == "stopped"
    polls = len(world.gateway.video_polls)
    time.sleep(0.3)
    assert len(world.gateway.video_polls) == polls, "nothing polls a stopped job"


def test_a_graceful_restart_keeps_the_job_running_and_polls_it_again(world: World) -> None:
    ada = _ada(world)
    world.gateway.video_polls_queued = 10_000
    started = _make(ada)
    _until(lambda: world.gateway.video_polls, "the Foreman never polled")
    world.server.stop()
    with sqlite3.connect(world.data / "workbench.sqlite3") as db:
        status, job = db.execute(
            "SELECT status, job FROM media WHERE id = ?", (started["id"],)
        ).fetchone()
    assert status == "running" and json.loads(job)["handle"] == "video_1", "not interrupted"
    world.gateway.video_polls_queued = 0
    world.restart_workbench()
    item = _wait(ada, started["id"])
    assert item["status"] == "done" and item["files"][0]["mediaType"] == "video/mp4"


def test_a_job_a_crash_left_running_is_polled_again_and_kept(world: World) -> None:
    """The check that matters most (§5, M11). A crash leaves the row
    running with its handle; the next start must poll it and keep the video.
    A graceful stop would not test this: the row is written here while
    Workbench is down, as a crash leaves it."""
    world.server.stop()
    world.gateway.video_jobs["video_resumed"] = {
        "model": GROK,
        "seconds": "1",
        "size": "854x480",
        "polls": 0,
        "reason": "",
    }
    job = {"handle": "video_resumed", "status": "queued", "progress": 0, "polls": 3}
    with sqlite3.connect(world.data / "workbench.sqlite3") as db:
        db.execute(
            "INSERT INTO media (id, owner, door, kind, status, created_at, model, request, job) "
            "VALUES ('crashed', 'p-ada', 'video', 'made', 'running', 1.0, ?, ?, ?)",
            (GROK, json.dumps({"model": GROK, "prompt": "a red ball"}), json.dumps(job)),
        )
    world.restart_workbench()
    ada = _ada(world)
    item = _wait(ada, "crashed")
    assert item["status"] == "done", item
    assert world.gateway.video_polls == ["video_resumed", "video_resumed"]
    assert world.gateway.video_requests == [], "the job is polled, never sent again"
    [kept] = item["files"]
    assert ada.get(f"/api/files/{kept['id']}").content == MP4_CLIP
    assert item["units"]["costUsd"] == 0.05


def test_bytes_that_are_not_an_mp4_fail_naming_them_and_leave_nothing(world: World) -> None:
    ada = _ada(world)
    world.gateway.video_content = b"<html>gone</html>"
    item = _wait(ada, _make(ada)["id"])
    assert item["status"] == "failed" and item["files"] == []
    assert "cannot read as MP4" in item["error"]["message"]
    assert "3c 68 74 6d" in item["error"]["message"]
    person = files.person_dir(world.data, "p-ada")
    assert not person.exists() or list(person.iterdir()) == [], "the part written is removed"


def test_a_video_is_its_owners_and_cannot_go_into_a_chat(world: World) -> None:
    ada = _ada(world)
    item = _wait(ada, _make(ada)["id"])
    [kept] = item["files"]
    bo = world.browser()
    bo.sign_in("p-bo")
    assert bo.get(f"/api/media/{item['id']}").status_code == 404
    assert bo.get(f"/api/files/{kept['id']}").status_code == 404
    sent = ada.post(f"/api/media/{item['id']}/to-chat", json={"fileId": kept["id"]})
    assert sent.status_code == 400, "the gateway carries no video in a chat (M7)"
