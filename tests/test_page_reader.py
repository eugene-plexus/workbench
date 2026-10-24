"""Scanned PDFs are fixtures and the vision model is a fake."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench.knowledge.extract import Extracted, Part, Unreadable
from eugene_plexus_workbench.knowledge.index import Document, Indexes

from . import documents
from .conftest import MODEL, PNG, World
from .saved import add, keep, settings, settle, start


async def test_scanned_pdf_read_only_when_switched_on(tmp_path: Path) -> None:
    from eugene_plexus_workbench.knowledge import page_reader

    source = tmp_path / "scan.pdf"
    source.write_bytes(documents.pdf_pictures(2))
    calls: list[dict[str, Any]] = []

    async def vision(body: dict[str, Any]) -> dict[str, Any]:
        calls.append(body)
        return {
            "model": "served-vision",
            "choices": [{"message": {"content": f"Scanned page {len(calls)} has TAMARIND-6042."}}],
        }

    async def render(page: int) -> dict[str, Any]:
        return {
            "image": "data:image/png;base64,fixture",
            "pageCount": 2,
            "width": 100,
            "height": 100,
        }

    assert (
        await page_reader.read_pages(
            source,
            None,
            enabled=False,
            model="fake-vision",
            maximum=200,
            complete=vision,
            render=render,
        )
        is None
    )
    assert calls == []
    result = await page_reader.read_pages(
        source, None, enabled=True, model="fake-vision", maximum=200, complete=vision, render=render
    )
    assert result is not None and result.read_by == "served-vision"
    assert [p.page for p in result.parts] == [1, 2]
    assert len(calls) == 2 and all(c["model"] == "fake-vision" for c in calls)
    assert all("image_url" in str(c["messages"]) for c in calls)


def test_picture_page_labels_use_exact_page_numbers() -> None:
    from eugene_plexus_workbench.knowledge import page_reader

    doc = Document(
        "d",
        "library",
        "report.pdf",
        "application/pdf",
        10,
        "hash",
        "upload",
        "ready",
        "pdf",
        0,
        notes=["Pages read from pictures: 10, 12."],
        read_by="fake",
    )
    assert page_reader.picture_pages(doc) == {10, 12}


async def test_index_migration_3_to_4(tmp_path: Path) -> None:
    indexes = Indexes(tmp_path)
    index = indexes.get("p-ada")
    assert index is not None
    directory = index.dir
    await index.documents()
    await indexes.aclose()
    with sqlite3.connect(directory / "index.sqlite3") as db:
        db.execute(
            "INSERT INTO documents (id,scope,name,media_type,bytes,sha256,source,status,kind,added_at,chunks) VALUES ('legacy','library','legacy.pdf','application/pdf',10,'hash','upload','ready','pdf',1,1)"
        )
        db.execute(
            "INSERT INTO chunks (doc_id,ord,text,tokens_est) VALUES ('legacy',0,'legacy passage',4)"
        )
        columns = [r[1] for r in db.execute("PRAGMA table_info(documents)")]
        if "read_by" in columns:
            db.execute("ALTER TABLE documents DROP COLUMN read_by")
        db.execute("PRAGMA user_version = 3")
    indexes = Indexes(tmp_path)
    index = indexes.get("p-ada")
    assert index is not None
    try:
        docs = await index.documents()
        assert len(docs) == 1 and docs[0].id == "legacy" and docs[0].read_by is None
        assert docs[0].sha256 == "hash" and await index.passages_total() == 1
        with sqlite3.connect(directory / "index.sqlite3") as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 4
            assert "read_by" in [r[1] for r in db.execute("PRAGMA table_info(documents)")]
    finally:
        await indexes.aclose()


async def test_only_empty_pages_are_sent_and_a_failed_page_is_named(tmp_path: Path) -> None:
    from eugene_plexus_workbench import token_count
    from eugene_plexus_workbench.knowledge import page_reader

    called: list[Any] = []

    async def render(n: int) -> dict[str, Any]:
        return {"image": f"page-{n}", "pageCount": 3}

    async def fake(body: dict[str, Any]) -> dict[str, Any]:
        called.append(body)
        if body["messages"][0]["content"][1]["image_url"]["url"] == "page-3":
            raise RuntimeError("fixture page refused")
        return {"choices": [{"message": {"content": "The pictured words."}}]}

    result = await page_reader.read_pages(
        tmp_path / "fixture.pdf",
        Extracted("pdf", [Part("digital words", 1), Part("", 2), Part("", 3)], pages=3),
        enabled=True,
        model="fake",
        maximum=200,
        complete=fake,
        render=render,
    )
    assert result is not None and result.parts[0].text == "digital words" and len(called) == 2
    assert any("Page 3 was not read: fixture page refused" in n for n in result.notes)
    assert any("1 page" in n for n in result.notes)
    assert token_count.estimate(result.parts[1].text) > 0


async def test_page_limit_prevents_any_model_request(tmp_path: Path) -> None:
    from eugene_plexus_workbench.knowledge import page_reader

    async def render(n: int) -> dict[str, Any]:
        return {"image": "fixture", "pageCount": 201}

    async def forbidden(body: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("The limit must be checked before a model request")

    with pytest.raises(Unreadable, match=r"201 pages.*at most 200"):
        await page_reader.read_pages(
            tmp_path / "fixture.pdf",
            None,
            enabled=True,
            model="fake",
            maximum=200,
            complete=forbidden,
            render=render,
        )


async def test_stopping_a_read_prevents_the_next_page_from_being_sent(tmp_path: Path) -> None:
    from eugene_plexus_workbench.knowledge import page_reader

    calls = 0

    async def render(n: int) -> dict[str, Any]:
        return {"image": "fixture", "pageCount": 3}

    async def fake(body: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"choices": [{"message": {"content": "The pictured words."}}]}

    async def check() -> None:
        if calls:
            raise Unreadable("stopped", "The document was removed.")

    with pytest.raises(Unreadable, match="document was removed"):
        await page_reader.read_pages(
            tmp_path / "fixture.pdf",
            None,
            enabled=True,
            model="fake",
            maximum=200,
            complete=fake,
            render=render,
            check=check,
        )
    assert calls == 1


def test_scan_is_indexed_with_model_and_images_need_person_switch(
    world: World, monkeypatch: Any
) -> None:
    from eugene_plexus_workbench.hub import Hub

    calls: list[dict[str, Any]] = []

    async def vision(self: Hub, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(body)
        return {"choices": [{"message": {"content": "The scanned harbour code is TAMARIND-6042."}}]}

    monkeypatch.setattr(Hub, "complete", vision)
    world.gateway.embedder_listed = False
    settings(world, documentsPageReader=MODEL)
    ada = start(world)
    scan = keep(ada, ("scan.pdf", documents.pdf_pictures(2)))["documents"][0]
    assert scan["state"] == "skipped" and scan["readBy"] is None and not calls
    assert add(ada, ("photo.png", PNG)).status_code == 415
    plan = ada.post(f"/api/documents/{scan['id']}/read-pages/plan").json()
    assert plan["message"] == f"2 pages to read with {MODEL}"
    enabled = ada.put("/api/documents/page-reading/settings", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    result = settle(ada)["documents"][0]
    assert result["state"] == "ready" and result["readBy"] == MODEL
    assert len(calls) == 2
    assert keep(ada, ("photo.png", PNG))["documents"][0]["readBy"] == MODEL
    found = ada.post("/api/documents/test", json={"question": "TAMARIND-6042"})
    assert found.json()["passages"]


def test_page_reader_outside_rule_and_missing_model_name_cause(
    world: World, monkeypatch: Any
) -> None:
    from eugene_plexus_workbench.hub import Hub

    async def forbidden(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("No picture may be sent")

    monkeypatch.setattr(Hub, "complete", forbidden)
    settings(world, documentsPageReader=MODEL, documentsExternalModels="never")
    world.gateway.locality = "external"
    ada = start(world)
    assert (
        ada.put(
            "/api/documents/page-reading/settings", json={"enabled": True, "outside": True}
        ).status_code
        == 200
    )
    blocked = keep(ada, ("scan.pdf", documents.pdf_pictures(1)))["documents"][0]
    assert "outside-model rule" in blocked["message"]
    settings(world, documentsPageReader="missing-vision")
    assert ada.post(f"/api/documents/{blocked['id']}/read-pages", json={}).status_code == 200
    failed = settle(ada)["documents"][0]
    assert "missing-vision is not served now" in failed["message"]


def test_rebuilding_the_derived_index_keeps_the_persons_scan_choice(world: World) -> None:
    settings(world, documentsPageReader=MODEL)
    ada = start(world)
    changed = ada.put("/api/documents/page-reading/settings", json={"enabled": True})
    assert changed.status_code == 200
    rebuilt = ada.post("/api/documents/rebuild", json={})
    assert rebuilt.status_code == 200, rebuilt.text
    assert ada.get("/api/documents/page-reading/settings").json()["enabled"] is True


def test_scan_control_reports_the_value_in_effect(world: World) -> None:
    settings(world, documentsPageReader=MODEL)
    ada = start(world)
    off = ada.get("/api/documents/page-reading/settings").json()
    assert off["enabled"] is False and off["message"] == "Off: scanned pages are skipped"
    assert (
        ada.put("/api/documents/page-reading/settings", json={"enabled": True}).status_code == 200
    )
    assert ada.get("/api/documents/page-reading/settings").json()["enabled"] is True
    settings(world, documentsPageReader="")
    stopped = ada.get("/api/documents/page-reading/settings").json()
    assert stopped["enabled"] is False and stopped["message"] == "Off: scanned pages are skipped"
    assert add(ada, ("off.png", PNG)).status_code == 415


def test_text_pdf_does_not_wait_for_a_page_reader(world: World) -> None:
    settings(world, documentsPageReader="not-served")
    ada = start(world)
    assert (
        ada.put("/api/documents/page-reading/settings", json={"enabled": True}).status_code == 200
    )
    result = keep(ada, ("text.pdf", documents.three_pages()))["documents"][0]
    assert result["state"] == "ready" and result["readBy"] is None


def test_each_folder_has_its_own_scan_switch(world: World, notes: Path, monkeypatch: Any) -> None:
    from eugene_plexus_workbench.hub import Hub

    from .folderdocs import add_and_settle, by_path, grant, owner_and_ada, settle_folder, write

    calls: list[Any] = []

    async def fake(self: Hub, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(body)
        return {"choices": [{"message": {"content": "Scanned harbour notes."}}]}

    monkeypatch.setattr(Hub, "complete", fake)
    world.gateway.embedder_listed = False
    settings(world, documentsPageReader=MODEL)
    owner, ada = owner_and_ada(world)
    write(notes, "scan.pdf", documents.pdf_pictures(2))
    write(notes, "picture.png", PNG)
    folder = grant(world, owner, notes)
    first = add_and_settle(ada, folder)
    assert not calls
    scope = f"folder:{folder}"
    assert (
        ada.get("/api/documents/page-reading/settings", params={"scope": scope}).json()["enabled"]
        is False
    )
    changed = ada.put(
        "/api/documents/page-reading/settings", json={"scope": scope, "enabled": True}
    )
    assert changed.status_code == 200, changed.text
    settle_folder(ada, folder, after=first["checkedAt"])
    read = by_path(ada, folder)
    assert read["scan.pdf"]["readBy"] == read["picture.png"]["readBy"] == MODEL
    assert len(calls) == 3
    assert ada.get("/api/documents/page-reading/settings").json()["enabled"] is False
    bo = start(world, "p-bo")
    assert (
        bo.put(
            "/api/documents/page-reading/settings", json={"scope": scope, "enabled": True}
        ).status_code
        == 404
    )
