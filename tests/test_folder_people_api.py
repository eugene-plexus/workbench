"""Whose a folder is, and what is refused (MR7): two people against every route, the owner, the
grants that are not yours, folders on other machines, Workbench without its own account, documents
turned off, and the install's settings. A folder that is not yours is the same 404 as one that does
not exist; nobody reads another person's documents, the owner included.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from .conftest import ADMIN_TOKEN, World
from .folderdocs import (
    add_and_settle,
    add_folder,
    allow_folders,
    by_path,
    files_of,
    folder_of,
    folders_of,
    grant,
    owner_and_ada,
    settle_folder,
    sync_and_settle,
    sync_now,
    write,
)
from .saved import RARE, ask, chat_on, index_of, listing, md, settings, start

pytestmark = pytest.mark.usefixtures("quiet_loop")
OLD = 3600.0


def age(folder: Path) -> None:
    then = time.time() - OLD
    for found in folder.rglob("*"):
        if found.is_file():
            os.utime(found, (then, then))


def routes(gid: str) -> list[tuple[str, str, dict[str, Any]]]:
    """Every folder route, with a body that is valid, so that what is refused is the caller."""
    base = f"/api/documents/folders/{gid}"
    return [
        ("get", "/api/documents/folders", {}),
        ("post", "/api/documents/folders", {"json": {"grantId": gid}}),
        ("get", base, {}),
        ("patch", base, {"json": {"includeHidden": True}}),
        ("post", f"{base}/sync", {"json": {}}),
        ("get", f"{base}/files", {}),
        ("delete", base, {}),
    ]


# --------------------------------------------------------------------------- #
# every route needs a session, and the changing ones the page's own origin
# --------------------------------------------------------------------------- #


def test_every_folder_route_needs_a_session_and_the_changing_ones_the_pages_own_origin(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", md("Words."))
    age(notes)
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    add_and_settle(ada, gid)
    stranger = httpx.Client(base_url=world.workbench, timeout=20, trust_env=False)
    for method, path, body in routes(gid):
        got = stranger.request(method.upper(), path, headers={"Origin": world.workbench}, **body)
        assert got.status_code == 401, (method, path, got.status_code)
        assert got.json()["detail"]["signedIn"] is False
    for method, path, body in routes(gid):
        if method == "get":
            continue
        bare = ada.http.request(
            method.upper(), path, headers={"X-Workbench-Secret": ada.secret or ""}, **body
        )
        assert bare.status_code == 403, (method, path)
    assert folder_of(ada, gid)["state"] == "synced", "and nothing changed"
    assert set(by_path(ada, gid)) == {"a.md"}


# --------------------------------------------------------------------------- #
# two people against every route
# --------------------------------------------------------------------------- #


def test_a_folder_that_is_not_yours_is_the_same_404_as_one_that_does_not_exist(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", md(f"The {RARE} secret."))
    age(notes)
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    bo = start(world, "p-bo")
    gid = grant(world, owner, notes)
    add_and_settle(ada, gid)
    for method, path, body in routes(gid)[2:]:
        theirs = getattr(bo, method)(path, **body)
        nothing = getattr(bo, method)(path.replace(gid, "no-such-folder-id"), **body)
        assert theirs.status_code == nothing.status_code == 404, (method, path)
        assert (
            theirs.json() == nothing.json() == {"detail": {"message": "There is no such folder."}}
        )
    after = folder_of(ada, gid)
    assert after["includeHidden"] is False and after["state"] == "synced", (
        "nothing of Ada's changed"
    )
    assert set(by_path(ada, gid)) == {"a.md"}
    for other in (bo, owner):
        theirs = folders_of(other)
        assert theirs["folders"] == [] and theirs["grants"] == [], "nor is it in their list"
    assert [x["id"] for x in folders_of(ada)["folders"]] == [gid]


def test_a_grant_that_is_not_yours_cannot_be_added_and_is_the_same_404_as_none(
    world: World, notes: Path
) -> None:
    owner, ada = owner_and_ada(world)
    bo = start(world, "p-bo")
    gid = grant(world, owner, notes, subject="p-ada")
    for who in (bo, owner):
        theirs = add_folder(who, gid)
        none = add_folder(who, "no-such-grant")
        assert theirs.status_code == none.status_code == 404
        assert theirs.json() == none.json() == {"detail": {"message": "There is no such folder."}}
        assert folders_of(who)["folders"] == [] and folders_of(who)["grants"] == []
    assert add_folder(ada, gid).status_code == 201, "and it is Ada's to add"


def test_the_owner_is_offered_only_the_grants_that_name_the_owner(
    world: World, notes: Path, more_notes: Path
) -> None:
    world.gateway.embedder_listed = False
    write(more_notes, "mine.md", md(f"The {RARE} owner's words."))
    age(more_notes)
    owner, ada = owner_and_ada(world)
    for_ada = grant(world, owner, notes, subject="p-ada", name="Ada's")
    for_owner = grant(world, owner, more_notes, subject="operator", name="Owner's")
    assert [g["id"] for g in folders_of(owner)["grants"]] == [for_owner]
    assert [g["id"] for g in folders_of(ada)["grants"]] == [for_ada]
    shown = owner.get("/api/folders").json()["grants"]
    assert {g["path"] for g in shown} == {str(notes), str(more_notes)}, (
        "the owner sees the paths of every grant, in the folders list that has always shown them"
    )
    done = add_and_settle(owner, for_owner)
    assert done["counts"]["files"] == 1 and set(by_path(owner, for_owner)) == {"mine.md"}
    assert str(more_notes) not in str(folders_of(owner)), "never a path in the folders' own routes"


def test_nobody_finds_or_reads_another_persons_folder_documents_the_owner_included(
    world: World, notes: Path
) -> None:
    write(notes, "secret.md", md(f"The {RARE} secret of Ada's folder."))
    age(notes)
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    bo = start(world, "p-bo")
    gid = grant(world, owner, notes)
    add_and_settle(ada, gid)
    doc = by_path(ada, gid)["secret.md"]
    settings(world, ownerReadsChats=True)
    try:
        for stranger in (bo, owner):
            assert (
                listing(stranger)["documents"] == []
                and listing(stranger)["totals"]["documents"] == 0
            )
            found = stranger.post("/api/documents/test", json={"question": f"the {RARE} secret"})
            assert found.json()["status"] == "no_documents" and found.json()["passages"] == []
            for path in ("", "/text", "/original", "/passages?ids=1"):
                assert stranger.get(f"/api/documents/{doc['id']}{path}").status_code == 404, path
            assert stranger.delete(f"/api/documents/{doc['id']}").status_code == 404
            assert stranger.post(f"/api/documents/{doc['id']}/reread").status_code == 404
            chat = chat_on(stranger)
            assert (
                stranger.get(f"/api/chats/{chat}/documents").json()["counts"]["library"][
                    "documents"
                ]
                == 0
            )
        # Ada's chat quotes the folder's file; the owner may read her chats, and sees "a document".
        chat = chat_on(ada)
        answer = ask(ada, chat, f"What is the {RARE} secret?")
        passages = [s for s in answer["sources"] if s.get("passage")]
        assert passages and passages[0]["name"] == "Work notes/secret.md"
        seen = owner.get(f"/api/chats/{chat}")
        assert (
            seen.status_code == 200
            and "secret.md" not in seen.text
            and "Work notes" not in seen.text
        )
        hidden = [s for s in seen.json()["messages"][-1]["sources"] if s.get("passage")]
        assert hidden and all(s["name"] == "a document" and s["redacted"] is True for s in hidden)
    finally:
        settings(world, ownerReadsChats=None)


def test_each_persons_folder_documents_are_in_their_own_index_and_nobody_elses(
    world: World, notes: Path, more_notes: Path
) -> None:
    write(notes, "ada.md", md(f"The {RARE} words of Ada."))
    write(more_notes, "bo.md", md("The plumbago words of Bo."))
    age(notes)
    age(more_notes)
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    bo = start(world, "p-bo")
    one = grant(world, owner, notes, subject="p-ada", name="Ada's notes")
    two = grant(world, owner, more_notes, subject="p-bo", name="Bo's notes")
    add_and_settle(ada, one)
    add_and_settle(bo, two)
    assert index_of(world, "p-ada") != index_of(world, "p-bo")
    assert set(by_path(ada, one)) == {"ada.md"} and set(by_path(bo, two)) == {"bo.md"}
    assert b"plumbago" not in index_of(world, "p-ada").read_bytes()
    assert RARE.encode() not in index_of(world, "p-bo").read_bytes()
    found = bo.post("/api/documents/test", json={"question": RARE}).json()
    assert found["status"] == "nothing_matched" and found["passages"] == []
    assert ada.post("/api/documents/test", json={"question": RARE}).json()["status"] == "found"
    removed = bo.delete(f"/api/documents/folders/{two}")
    assert removed.status_code == 204
    assert set(by_path(ada, one)) == {"ada.md"}, "Bo removing his folder leaves Ada's"
    assert ada.delete(f"/api/documents/folders/{two}").status_code == 404


# --------------------------------------------------------------------------- #
# what is refused
# --------------------------------------------------------------------------- #


def test_a_folder_on_another_machine_is_refused_in_words_and_nothing_is_kept(world: World) -> None:
    _, ada = owner_and_ada(world)
    allow_folders(world)
    refused = add_folder(ada, "node:abc123")
    assert refused.status_code == 400
    assert refused.json()["detail"]["message"] == (
        "Folders on other machines (Job Sites) cannot be added to your documents yet. Only a folder "
        "on this machine can."
    )
    assert folders_of(ada)["folders"] == []


def test_without_workbenchs_own_account_folders_are_unavailable_and_say_why(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", md("Words."))
    age(notes)
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    add_and_settle(ada, gid)
    world.settings.account_kind = None
    listed = folders_of(ada)
    assert listed["available"] is False
    assert "Workbench's own OS account" in listed["reason"]
    refused = add_folder(ada, gid)
    assert refused.status_code == 503 and refused.json()["detail"]["code"] == "folders_unavailable"
    assert "Workbench's own OS account" in refused.json()["detail"]["message"]
    before = folder_of(ada, gid)["checkedAt"]
    assert sync_now(ada, gid).status_code == 202
    shown = settle_folder(ada, gid, after=before, ends=("problem",))
    assert shown["problem"]["code"] == "unreadable"
    assert "Workbench's own OS account" in shown["problem"]["message"]
    assert set(by_path(ada, gid)) == {"a.md"}, "its documents are as they were"
    allow_folders(world)
    assert sync_and_settle(ada, gid)["state"] == "synced"


def test_while_the_owner_has_documents_off_a_folder_cannot_be_added_or_changed_but_can_be_read_and_removed(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    add_and_settle(ada, gid)
    settings(world, documentsEnabled=False)
    off = "The owner of this Workbench has turned off documents."
    for response in (
        add_folder(ada, gid),
        ada.patch(f"/api/documents/folders/{gid}", json={"includeHidden": True}),
        sync_now(ada, gid),
    ):
        assert response.status_code == 403 and response.json()["detail"]["message"] == off
    assert folder_of(ada, gid)["state"] == "synced" and files_of(ada, gid)["total"] == 1
    assert folders_of(ada)["enabled"] is False
    assert ada.delete(f"/api/documents/folders/{gid}").status_code == 204


@pytest.mark.parametrize(
    ("method", "body"),
    [
        ("post", {"grantId": "g", "unknown": 1}),
        ("post", {"grantId": ""}),
        ("post", {}),
        ("post", {"grantId": "g", "includeHidden": "yes"}),
        ("post", {"grantId": "g", "useGitignore": 1}),
        ("post", {"grantId": "g", "ignore": "*.log"}),
        ("post", {"grantId": "g", "ignore": [f"p{n}" for n in range(102)]}),
        ("patch", {"unknown": 1}),
        ("patch", {"includeHidden": "yes"}),
        ("patch", {"ignore": [1]}),
        ("sync", {"deep": "yes"}),
        ("sync", {"unknown": 1}),
    ],
)
def test_a_body_that_is_not_what_a_route_takes_is_a_422_before_anything_happens(
    world: World, notes: Path, method: str, body: dict[str, Any]
) -> None:
    write(notes, "a.md", md("Words."))
    age(notes)
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    if method == "post":
        body = {k: (gid if v == "g" else v) for k, v in body.items()}
        assert ada.post("/api/documents/folders", json=body).status_code == 422
        assert folders_of(ada)["folders"] == []
        return
    add_and_settle(ada, gid)
    before = folder_of(ada, gid)
    target = f"/api/documents/folders/{gid}" + ("/sync" if method == "sync" else "")
    made = ada.post(target, json=body) if method == "sync" else ada.patch(target, json=body)
    assert made.status_code == 422, made.text
    assert folder_of(ada, gid) == before, "nothing changed"


def test_a_folder_is_read_only_whatever_the_grant_allows(world: World, notes: Path) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes, writable=True)
    done = add_and_settle(ada, gid)
    assert done["writable"] is True, (
        "the grant lets a model write, with approval; indexing never does"
    )
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in notes.rglob("*") if p.is_file()}
    sync_and_settle(ada, gid, deep=True)
    sync_and_settle(ada, gid)
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in notes.rglob("*") if p.is_file()}
    assert after == before and sorted(p.name for p in notes.iterdir()) == ["a.md"]


# --------------------------------------------------------------------------- #
# the install's settings
# --------------------------------------------------------------------------- #

KEYS = (
    ("documentsFolderSyncMinutes", 10, 1, 1440),
    ("documentsFolderMaxFiles", 10_000, 10, 100_000),
    ("documentsFolderMaxMB", 2048, 1, 102_400),
    ("documentsFolderMaxDepth", 12, 1, 31),
)


def test_the_installs_folder_settings_are_in_the_console_with_what_unset_means(
    world: World,
) -> None:
    good = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    schema = httpx.get(f"{world.workbench}/v1/config/schema", headers=good, trust_env=False).json()
    by_key = {f["key"]: f for f in schema["fields"]}
    now = httpx.get(f"{world.workbench}/v1/config", headers=good, trust_env=False).json()
    for key, default, low, high in KEYS:
        field = by_key[key]
        assert field["category"] == "documents" and field["valueType"] == "integer"
        assert (field["default"], field["minimum"], field["maximum"]) == (default, low, high)
        # The default is the schema's own field, asserted above; the help does not repeat it.
        assert field["label"] and field["description"]
        assert now[key] == default, "the value in effect is shown, not a stand-in"
    assert "Documents" in schema["categories"].values()


@pytest.mark.parametrize(("key", "default", "low", "high"), KEYS)
def test_each_folder_setting_takes_whole_numbers_in_its_range_and_nothing_else(
    world: World, key: str, default: int, low: int, high: int
) -> None:
    def patch(value: Any) -> dict[str, Any]:
        result = httpx.patch(
            f"{world.workbench}/v1/config",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={key: value},
            trust_env=False,
        )
        assert result.status_code == 200
        return dict(result.json())

    def current() -> Any:
        found = httpx.get(
            f"{world.workbench}/v1/config",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            trust_env=False,
        )
        return found.json()[key]

    for bad in (low - 1, high + 1, True, "5", 2.5):
        refused = patch(bad)
        assert refused["applied"] == [] and refused["rejected"][0]["key"] == key, bad
        assert f"{low:,} to {high:,}" in refused["rejected"][0]["message"]
        assert current() == default, "a refused value changes nothing"
    for fine in (low, high, (low + high) // 2):
        assert patch(fine)["applied"] == [key] and current() == fine
    assert patch(None)["applied"] == [key] and current() == default, "null is the default again"
