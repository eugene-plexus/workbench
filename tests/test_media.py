"""The media area's API (workbench-media-screens.md, slice 1: images).

Real Workbench, fake Eugene and a fake gateway that lists image models the
way the gateway does since §6.1 and answers its two image doors. Every test
here fails against Workbench before the media screens, which had no
`/api/media` at all.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

import httpx

from eugene_plexus_workbench import files

from .conftest import ADMIN_TOKEN, Browser, World, png


def _owner_reads(world: World, on: bool) -> None:
    httpx.patch(
        f"{world.workbench}/v1/config",
        json={"ownerReadsChats": on},
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        trust_env=False,
    ).raise_for_status()


def _make(person: Browser, **fields: Any) -> dict[str, Any]:
    body = {"model": "openrouter/flux", "prompt": "a red barn", **fields}
    made = person.post("/api/media/images", json=body)
    assert made.status_code == 201, made.text
    return dict(made.json())


def _wait(person: Browser, media_id: str, seconds: float = 15) -> dict[str, Any]:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        item = person.get(f"/api/media/{media_id}").json()
        if item["status"] != "running":
            return dict(item)
        time.sleep(0.05)
    raise AssertionError("the image request did not finish")


def _on_disk(world: World, owner: str, file_id: str) -> bool:
    return files.path_of(world.data, owner, file_id).is_file()


def test_the_images_screen_lists_only_image_models_with_their_settings(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    models = {m["id"]: m for m in ada.get("/api/media/doors").json()["doors"]["images"]["models"]}
    assert set(models) == {"openrouter/flux", "openrouter/mini", "oai-image"}, "no chat model"
    flux, mini, oai = models["openrouter/flux"], models["openrouter/mini"], models["oai-image"]
    assert (flux["maxImages"], flux["qualities"], flux["maxReferences"]) == (1, [], 4)
    assert (mini["maxImages"], mini["qualities"]) == (10, ["auto", "low", "medium", "high"])
    assert (flux["provider"], flux["account"], flux["locality"]) == (
        "OpenRouter",
        "openrouter",
        "external",
    )
    # Not listed is the backend's to check: null, never an invented list.
    assert oai["maxImages"] is None and oai["qualities"] is None and oai["outputFormats"] is None
    # The chat picker is unchanged by image models.
    assert [m["id"] for m in ada.get("/api/models").json()["models"]] == ["local-model"]


def test_an_image_is_kept_with_what_was_asked_and_what_came_back(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    item = _wait(ada, _make(ada, size="512x512")["id"])
    assert item["status"] == "done", item
    [door, sent] = world.gateway.image_requests[-1]
    assert door == "generations" and sent == {
        "model": "openrouter/flux",
        "prompt": "a red barn",
        "size": "512x512",
    }
    [made] = item["files"]
    assert (made["width"], made["height"], made["mediaType"]) == (3, 2, "image/png")
    assert item["request"]["size"] == "512x512" and item["units"]["sizes"] == ["3x2"]
    assert item["served"]["driver"] == "openrouter" and item["served"]["requestId"] == "req-1"
    whole = ada.get(f"/api/files/{made['id']}")
    assert whole.status_code == 200 and whole.content == png(3, 2)
    part = ada.get(f"/api/files/{made['id']}", headers={"Range": "bytes=0-7"})
    assert part.status_code == 206 and part.content == b"\x89PNG\r\n\x1a\n", "Range is answered"
    listed = ada.get("/api/media", params={"door": "images"}).json()
    assert [i["id"] for i in listed["items"]] == [item["id"]]
    assert listed["bytes"] == len(png(3, 2))


def test_several_images_are_one_result(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    item = _wait(ada, _make(ada, model="openrouter/mini", n=3, quality="low")["id"])
    assert len(item["files"]) == 3 and item["units"]["images"] == 3
    assert world.gateway.image_requests[-1][1]["quality"] == "low"


def test_a_refusal_is_the_gateways_words_with_its_field(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    words = (
        "n: no model serving 'openrouter/flux' takes this request (makes at most 1 per request)."
    )
    world.gateway.image_refusal = (400, words, "n")
    item = _wait(ada, _make(ada, n=2)["id"])
    assert item["status"] == "failed" and item["files"] == []
    assert item["error"] == {"message": words, "param": "n", "status": 400}


def test_a_request_runs_on_the_server_and_a_restart_marks_it_interrupted(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    world.gateway.image_delay = 30
    started = _make(ada)
    assert started["status"] == "running"
    deadline = time.perf_counter() + 5
    while not world.gateway.image_requests and time.perf_counter() < deadline:
        time.sleep(0.05)
    assert world.gateway.image_requests, "the server sent it without any page watching"
    world.restart_workbench()
    item = ada.get(f"/api/media/{started['id']}").json()
    assert item["status"] == "interrupted", item


def test_a_request_a_crash_left_running_is_marked_interrupted_at_boot(world: World) -> None:
    """A clean shutdown marks its own requests; a crash cannot, so the next
    start must (the boot sweep), or the result says *Making it* forever."""
    world.server.stop()
    with sqlite3.connect(world.data / "workbench.sqlite3") as db:
        db.execute(
            "INSERT INTO media (id, owner, door, kind, status, created_at, request) "
            "VALUES ('crashed', 'p-ada', 'images', 'made', 'running', 1.0, '{}')"
        )
    world.restart_workbench()
    ada = world.browser()
    ada.sign_in("p-ada")
    item = ada.get("/api/media/crashed").json()
    assert item["status"] == "interrupted" and item["finishedAt"], item


def test_stop_ends_a_running_request(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    world.gateway.image_delay = 30
    started = _make(ada)
    assert ada.post(f"/api/media/{started['id']}/stop").status_code == 204
    assert ada.get(f"/api/media/{started['id']}").json()["status"] == "stopped"


def test_a_result_is_its_owners_and_the_owner_reads_only_when_allowed(world: World) -> None:
    owner, ada, bo = world.browser(), world.browser(), world.browser()
    owner.sign_in("operator")
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    item = _wait(ada, _make(ada)["id"])
    file_id = item["files"][0]["id"]
    for who in (bo, owner):
        assert who.get(f"/api/media/{item['id']}").status_code == 404
        assert who.get(f"/api/files/{file_id}").status_code == 404
    assert bo.get("/api/media", params={"door": "images"}).json()["items"] == []
    assert owner.get("/api/people/p-ada/media", params={"door": "images"}).status_code == 403
    _owner_reads(world, True)
    read = owner.get(f"/api/media/{item['id']}").json()
    assert read["readOnly"] is True
    assert owner.get(f"/api/files/{file_id}").status_code == 200
    listed = owner.get("/api/people/p-ada/media", params={"door": "images"}).json()["items"]
    assert [i["id"] for i in listed] == [item["id"]]
    people = {p["sub"]: p for p in owner.get("/api/people").json()["people"]}
    assert people["p-ada"]["media"] == 1
    # Read-only: the owner cannot delete, stop or copy it.
    assert owner.delete(f"/api/media/{item['id']}").status_code == 404
    assert bo.get(f"/api/media/{item['id']}").status_code == 404, "a member still cannot"
    assert _on_disk(world, "p-ada", file_id)


def test_references_come_from_the_persons_own_bin(world: World) -> None:
    ada, bo = world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    brought = ada.post(
        "/api/media/images/upload", files={"file": ("photo.png", png(4, 4), "image/png")}
    )
    assert brought.status_code == 201, brought.text
    upload = brought.json()
    assert upload["kind"] == "upload" and upload["files"][0]["width"] == 4
    ref = upload["files"][0]["id"]
    item = _wait(ada, _make(ada, prompt="make it blue", references=[ref])["id"])
    assert item["status"] == "done"
    door, sent = world.gateway.image_requests[-1]
    assert door == "edits"
    assert sent["images"][0]["image_url"].startswith("data:image/png;base64,")
    refused = bo.post(
        "/api/media/images", json={"model": "openrouter/flux", "prompt": "x", "references": [ref]}
    )
    assert refused.status_code == 400 and "no longer in your bins" in refused.text
    not_an_image = ada.post(
        "/api/media/images/upload", files={"file": ("a.txt", b"plain words", "text/plain")}
    )
    assert not_an_image.status_code == 415


def test_send_to_a_chat_copies_and_each_side_deletes_alone(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    item = _wait(ada, _make(ada, prompt="a red barn at dusk")["id"])
    source = item["files"][0]["id"]
    sent = ada.post(f"/api/media/{item['id']}/to-chat", json={"fileId": source})
    assert sent.status_code == 201, sent.text
    chat, copy = sent.json()["chatId"], sent.json()["attachment"]
    assert copy["id"] != source and copy["mediaType"] == "image/png"
    assert ada.get(f"/api/chats/{chat}").json()["chat"]["title"] == "a red barn at dusk"
    assert ada.delete(f"/api/media/{item['id']}").status_code == 204
    assert not _on_disk(world, "p-ada", source), "deleting the result removes its file"
    assert ada.get(f"/api/files/{copy['id']}").content == png(3, 2), "the chat keeps its copy"
    again = _wait(ada, _make(ada)["id"])
    sent = ada.post(f"/api/media/{again['id']}/to-chat", json={"fileId": again["files"][0]["id"]})
    assert ada.delete(f"/api/chats/{sent.json()['chatId']}").status_code == 204
    assert ada.get(f"/api/files/{again['files'][0]['id']}").status_code == 200, "the result stays"


def test_a_webp_cannot_go_into_a_chat_and_says_why(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    webp = b"RIFF\x1a\x00\x00\x00WEBPVP8L\x0d\x00\x00\x00\x2f\x01\x40\x00\x00" + bytes(8)
    world.gateway.image_answer = webp
    item = _wait(ada, _make(ada)["id"])
    assert item["files"][0]["mediaType"] == "image/webp"
    refused = ada.post(f"/api/media/{item['id']}/to-chat", json={"fileId": item["files"][0]["id"]})
    assert refused.status_code == 400 and "WEBP" in refused.text


def test_emptying_a_bin_deletes_every_result_and_file(world: World) -> None:
    ada, bo = world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    mine = [_wait(ada, _make(ada)["id"]) for _ in range(2)]
    theirs = _wait(bo, _make(bo)["id"])
    assert ada.delete("/api/media", params={"door": "images"}).status_code == 204
    assert ada.get("/api/media", params={"door": "images"}).json()["items"] == []
    for item in mine:
        assert not _on_disk(world, "p-ada", item["files"][0]["id"])
    assert bo.get(f"/api/media/{theirs['id']}").status_code == 200, "only my own bin"
    with sqlite3.connect(world.data / "workbench.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM files WHERE owner = 'p-ada'").fetchone()[0] == 0


def test_a_tab_hears_its_own_persons_results_only(world: World) -> None:
    ada, bo = world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    seen: list[str] = []
    with ada.http.stream("GET", "/api/media/events", headers=ada.headers(), timeout=10) as stream:
        lines = stream.iter_lines()
        assert next(lines) == ": watching"
        _make(bo)
        mine = _make(ada)
        for line in lines:
            if line.startswith("data: "):
                seen.append(line)
                if '"status": "done"' in line:
                    break
    assert seen and all(mine["id"] in line for line in seen), seen
