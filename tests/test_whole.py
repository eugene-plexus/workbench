"""Whole-document reads use a fake model, never an engine."""

import asyncio
import threading
from typing import Any

import pytest

from eugene_plexus_workbench.knowledge import whole

from .conftest import World
from .saved import ask, chat_on, keep, md, send, settings, start


async def test_read_all_reads_every_part_once() -> None:
    parts = [
        whole.Part(
            "report", "Annual report.pdf", f"unique-part-{i}", f"pages {i * 10 + 1}-{i * 10 + 10}"
        )
        for i in range(12)
    ]
    calls: list[dict[str, Any]] = []
    active = peak = 0

    async def complete(body: dict[str, Any]) -> dict[str, Any]:
        nonlocal active, peak
        calls.append(body)
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0)
            text = body["messages"][-1]["content"]
            if text.endswith("unique-part-6"):
                raise RuntimeError("fixture connection closed")
            return {
                "choices": [
                    {
                        "message": {
                            "content": "summary" if "unique-part-" in text else "reduced answer"
                        }
                    }
                ]
            }
        finally:
            active -= 1

    progress: list[Any] = []
    result = await whole.read_all(
        parts, "summarize the report", "fake", 8192, complete, progress.append
    )
    assert len(calls) == 13
    assert peak == 2
    for i in range(12):
        assert sum(b["messages"][-1]["content"].endswith(f"unique-part-{i}") for b in calls) == 1
    assert "Part 7 (pages 61-70) was not read: fixture connection closed" in result
    assert "leaves it out" in result
    assert "pages 1-10" in calls[-1]["messages"][-1]["content"]
    assert "Reading part 3 of 12 of Annual report.pdf" in str(progress)


async def test_stop_cancels_maps_and_does_not_reduce() -> None:
    calls = 0
    entered = asyncio.Event()

    async def complete(body: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        entered.set()
        await asyncio.Event().wait()
        return {}

    task = asyncio.create_task(
        whole.read_all(
            [whole.Part("a", "a.txt", str(i), "text") for i in range(12)],
            "overview",
            "fake",
            8192,
            complete,
            lambda _: None,
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 2


def test_parts_cover_text_once_and_caps_name_observed_count() -> None:
    text = "A sentence about the harbour.\n" * 2000
    parts = whole.split("a", "a.txt", text, "overview", 2048)
    assert "".join(p.text for p in parts) == text
    with pytest.raises(ValueError, match="at most 40"):
        whole.check_limits(parts * 40)


def test_whole_answer_versions_respect_setting_and_outside_rule(
    world: World, monkeypatch: Any
) -> None:
    from eugene_plexus_workbench.hub import Hub

    calls: list[dict[str, Any]] = []

    async def fake(self: Hub, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(body)
        return {"choices": [{"message": {"content": "Harbour summary."}}]}

    monkeypatch.setattr(Hub, "complete", fake)
    world.gateway.embedder_listed = False
    settings(world, documentsWholeByParts="ask")
    ada = start(world)
    doc = keep(
        ada,
        (
            "report.md",
            md(
                *[
                    "The harbour report lists every ship and records its visit. " * 30
                    for _ in range(40)
                ]
            ),
        ),
    )["documents"][0]
    chat = chat_on(ada)
    first = ask(ada, chat, "Summarize the harbour report")
    offer = first["retrieval"]["whole"]
    assert offer["state"] == "offered" and "tokens" in offer["cost"] and not calls
    version = ada.post(offer["action"], json={"documents": [doc["id"]], "cost": offer["cost"]})
    assert version.status_code == 201, version.text
    answer = ada.wait_answer(chat)
    assert answer["finish"] == "whole_document" and answer["model"]
    assert answer["sources"][0]["wholeParts"] >= 1 and len(calls) >= 2
    calls.clear()
    settings(world, documentsWholeByParts="always")
    automatic = ask(ada, chat_on(ada), "Summarize the harbour report")
    assert automatic["finish"] == "whole_document" and calls
    calls.clear()
    world.gateway.locality = "external"
    settings(world, documentsExternalModels="never")
    outside = ask(ada, chat_on(ada), "Summarize the harbour report")
    assert not calls and outside["retrieval"]["status"] == "skipped"


def test_whole_question_can_name_a_file_without_matching_its_contents(
    world: World, monkeypatch: Any
) -> None:
    from eugene_plexus_workbench.hub import Hub

    calls: list[dict[str, Any]] = []

    async def fake(self: Hub, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(body)
        return {"choices": [{"message": {"content": "All parts answered."}}]}

    monkeypatch.setattr(Hub, "complete", fake)
    world.gateway.embedder_listed = False
    ada = start(world)
    keep(ada, ("AnnualZebra.md", ("Harbour traffic is recorded here.\n" * 2000).encode()))
    reply = ask(ada, chat_on(ada), "Summarize AnnualZebra.md")
    assert reply["finish"] == "whole_document" and calls


def test_whole_question_reads_a_large_attached_file_in_parts(
    world: World, monkeypatch: Any
) -> None:
    from eugene_plexus_workbench.hub import Hub

    from .test_documents_api import attach

    calls: list[Any] = []

    async def fake(self: Hub, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(body)
        return {"choices": [{"message": {"content": "All attached parts answered."}}]}

    monkeypatch.setattr(Hub, "complete", fake)
    world.gateway.embedder_listed = False
    ada = start(world)
    chat = ada.new_chat()
    file_id = attach(
        ada, chat, "AnnualZebra.md", ("Harbour traffic is recorded here.\n" * 2000).encode()
    )
    reply = ask(ada, chat, "Summarize AnnualZebra.md", attachments=[file_id])
    assert reply["finish"] == "whole_document" and calls


def test_outside_rule_is_checked_before_each_part(world: World, monkeypatch: Any) -> None:
    from eugene_plexus_workbench.hub import Hub

    entered, release = threading.Event(), threading.Event()
    calls: list[Any] = []

    async def fake(self: Hub, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(body)
        entered.set()
        await asyncio.to_thread(release.wait, 10)
        return {"choices": [{"message": {"content": "A short note."}}]}

    monkeypatch.setattr(Hub, "complete", fake)
    world.gateway.embedder_listed = False
    world.gateway.locality = "external"
    world.gateway.context_length = 8192
    settings(world, documentsExternalModels="always")
    ada = start(world)
    keep(ada, ("AnnualZebra.md", ("Harbour traffic is recorded here.\n" * 3000).encode()))
    chat = chat_on(ada)
    assert send(ada, chat, "Summarize AnnualZebra.md").status_code == 201
    assert entered.wait(10)
    settings(world, documentsExternalModels="never")
    release.set()
    answer = ada.wait_answer(chat)
    assert len(calls) <= 2, "no further part or reduce may leave the machines"
    assert answer["status"] == "failed" and "outside" in answer["error"]
