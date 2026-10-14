"""The life of a folder (MR7), through a real Workbench: files that move, a folder that is not there or
was replaced, a grant the owner withdraws (before the owner's request returns, and while a check is
reading), a folder the person removes, a stop and a start, and checks that come by themselves.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import folder_io
from eugene_plexus_workbench.app import create_app
from eugene_plexus_workbench.knowledge import folder_sync
from eugene_plexus_workbench.knowledge.index import DeleteFailed, folder_source
from eugene_plexus_workbench.knowledge.saved import SavedDocuments
from eugene_plexus_workbench.store import FolderRecord, Person, Store

from .conftest import ServerThread as Server
from .conftest import World
from .folderdocs import (
    add_and_settle,
    add_folder,
    by_path,
    checked_at,
    files_of,
    folder_of,
    folders_of,
    grant,
    owner_and_ada,
    settle_folder,
    snapshot,
    sync_and_settle,
    sync_now,
    write,
)
from .meaning import asked_for_passages, ready_world, until, vectors_of
from .saved import RARE, SECOND, THIRD, index_of, knowledge_files, md, raw, settings

pytestmark = pytest.mark.usefixtures("quiet_loop")
OLD = 3600.0


def age(folder: Path) -> None:
    then = time.time() - OLD
    for found in folder.rglob("*"):
        if found.is_file():
            os.utime(found, (then, then))


def sync_of(world: World) -> Any:
    """The running Workbench's folder service, for a test that asks what it has begun."""
    return world.server.server.config.app.state.folder_sync


def embedded(world: World) -> list[str]:
    """The passages of these tests' files the search model was asked to embed, in order: a question
    is not one, and neither is the tiny request that asks which model answers."""
    return [t for t in asked_for_passages(world) if "ledger" in t or "Berthing" in t]


def words(person: Any, word: str) -> set[str]:
    """The documents whose own words hold this word: found by words only, so that a search model
    that is running does not add the ones whose meaning is near."""
    made = person.post(
        "/api/documents/test", json={"question": f"the {word} word", "ways": ["words"]}
    )
    assert made.status_code == 200, made.text
    return {p["name"] for p in made.json()["passages"]}


def setup(world: World, folder: Path, *, search_model: bool = False) -> tuple[Any, Any, str]:
    if search_model:
        ready_world(world)
    else:
        world.gateway.embedder_listed = False
    owner, ada = owner_and_ada(world)
    return owner, ada, grant(world, owner, folder, name="Work notes")


# --------------------------------------------------------------------------- #
# files that move
# --------------------------------------------------------------------------- #


def test_a_renamed_folder_keeps_every_document_its_passages_and_its_vectors(
    world: World, notes: Path
) -> None:
    write(notes, "old/a.md", md(f"The {RARE} harbour records are kept in the ledger.", title="A"))
    write(notes, "old/b.md", md("Berthing fees are paid at the quayside office.", title="B"))
    age(notes)
    _, ada, gid = setup(world, notes, search_model=True)
    add_and_settle(ada, gid)
    before = by_path(ada, gid)
    assert {d["state"] for d in before.values()} == {"ready"}
    vectors, asked = vectors_of(world), len(embedded(world))
    assert len(vectors) == 2 and asked == 2
    ids = {p: d["id"] for p, d in before.items()}
    (notes / "old").rename(notes / "new")
    done = sync_and_settle(ada, gid)
    after = by_path(ada, gid)
    assert set(after) == {"new/a.md", "new/b.md"}
    assert {p.removeprefix("new/"): d["id"] for p, d in after.items()} == {
        p.removeprefix("old/"): i for p, i in ids.items()
    }, "the same documents"
    assert (
        after["new/a.md"]["name"] == "Work notes/new/a.md" and after["new/a.md"]["state"] == "ready"
    )
    assert (
        done["last"]["moved"] == 2 and done["last"]["added"] == 0 and done["last"]["removed"] == 0
    )
    assert vectors_of(world) == vectors, "their vectors are the same rows"
    assert len(embedded(world)) == asked, "nothing was embedded again"
    assert words(ada, RARE) == {"Work notes/new/a.md"}
    sidecar = json.loads(
        (index_of(world).parent / f"{ids['old/a.md']}.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["source"] == folder_source(gid, "new/a.md") and sidecar["name"].endswith(
        "new/a.md"
    )


def test_a_file_moved_and_edited_is_a_new_document_and_the_old_is_gone(
    world: World, notes: Path
) -> None:
    write(notes, "old/a.md", md(f"The {RARE} words, the first version."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    before = by_path(ada, gid)["old/a.md"]
    (notes / "old" / "a.md").unlink()
    write(notes, "new/a.md", md(f"The {SECOND} words, and a longer second version of it."))
    done = sync_and_settle(ada, gid)
    after = by_path(ada, gid)
    assert set(after) == {"new/a.md"} and after["new/a.md"]["id"] != before["id"]
    assert (
        done["last"]["moved"] == 0 and done["last"]["added"] == 1 and done["last"]["removed"] == 1
    )
    assert words(ada, RARE) == set() and words(ada, SECOND) == {"Work notes/new/a.md"}


def test_a_new_file_that_looks_like_a_vanished_one_but_says_something_else_is_not_a_move(
    world: World, notes: Path
) -> None:
    first = write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    stamp, size = first.stat().st_mtime_ns, first.stat().st_size
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    before = by_path(ada, gid)["a.md"]
    first.unlink()
    second = write(notes, "b.md", md(f"The {THIRD} words."))
    assert second.stat().st_size == size
    os.utime(second, ns=(stamp, stamp))  # the same size and the same time: the hash decides
    done = sync_and_settle(ada, gid)
    after = by_path(ada, gid)
    assert set(after) == {"b.md"} and after["b.md"]["id"] != before["id"]
    assert (
        done["last"]["moved"] == 0 and done["last"]["added"] == 1 and done["last"]["removed"] == 1
    )
    assert words(ada, THIRD) == {"Work notes/b.md"} and words(ada, RARE) == set()


# --------------------------------------------------------------------------- #
# a folder that is not there, or is another
# --------------------------------------------------------------------------- #


def test_a_folder_that_is_not_there_takes_its_documents_out_and_they_come_back_with_it(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    write(notes, "b.md", md("More words."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    away = notes.with_name(notes.name + "-away")
    notes.rename(away)
    try:
        before = checked_at(ada, gid)
        assert sync_now(ada, gid).status_code == 202
        shown = settle_folder(ada, gid, after=before, ends=("unavailable",))
        assert shown["problem"]["code"] == "gone" and shown["state"] == "unavailable"
        assert shown["problem"]["message"] == (
            "This folder is not there any more. 2 documents from it were taken out of your search, "
            "and they come back if the folder does."
        )
        assert shown["headline"] == shown["problem"]["message"] and shown["problem"]["removed"] == 2
        assert by_path(ada, gid) == {} and words(ada, RARE) == set()
        assert shown["counts"]["files"] == 0
    finally:
        away.rename(notes)
    after = sync_and_settle(ada, gid)
    assert after["state"] == "synced" and after["problem"] is None
    assert set(by_path(ada, gid)) == {"a.md", "b.md"} and words(ada, RARE) == {"Work notes/a.md"}


def test_a_folder_replaced_by_another_takes_its_documents_out_and_says_to_grant_it_again(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    away = notes.with_name(notes.name + "-original")
    notes.rename(away)
    notes.mkdir()
    write(notes, "impostor.md", md(f"The {SECOND} words of the other folder."))
    try:
        before = checked_at(ada, gid)
        assert sync_now(ada, gid).status_code == 202
        shown = settle_folder(ada, gid, after=before, ends=("unavailable",))
        assert shown["problem"]["code"] == "replaced"
        assert shown["problem"]["message"] == (
            "This folder was replaced by another one. 1 document from it was taken out of your "
            "search. Ask the owner to grant it again, then add it again."
        )
        assert by_path(ada, gid) == {} and words(ada, RARE) == set()
        assert words(ada, SECOND) == set(), "nothing of the folder that took its place was read"
    finally:
        for found in notes.iterdir():
            found.unlink()
        notes.rmdir()
        away.rename(notes)
    back = sync_and_settle(ada, gid)
    assert back["state"] == "synced" and set(by_path(ada, gid)) == {"a.md"}


# --------------------------------------------------------------------------- #
# the owner withdraws the grant
# --------------------------------------------------------------------------- #


def test_a_withdrawn_grant_takes_the_documents_out_before_the_owners_request_returns(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    write(notes, "sub/b.md", md(f"The {SECOND} words."))
    age(notes)
    owner, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    assert words(ada, RARE) and words(ada, SECOND)
    chat = ada.post("/api/chats", json={"model": "local-model"}).json()["id"]
    ada.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on"}})
    files_before = snapshot(notes)
    begun = list(sync_of(world).begun)
    removed = owner.delete(f"/api/folders/{gid}")
    assert removed.status_code == 204
    # No waiting: the documents were out of the index when the request returned.
    assert words(ada, RARE) == set() and words(ada, SECOND) == set()
    shown = folder_of(ada, gid)
    assert shown["state"] == "withdrawn" and shown["granted"] is False
    assert shown["problem"]["code"] == "withdrawn" and shown["problem"]["removed"] == 2
    assert shown["problem"]["message"] == (
        "The owner took away your access to this folder. 2 documents from it were taken out of "
        "your search."
    )
    assert shown["headline"] == shown["problem"]["message"] and shown["counts"]["files"] == 0
    assert files_of(ada, gid)["total"] == 0 and snapshot(notes) == files_before
    assert ada.get(f"/api/chats/{chat}/documents").json()["counts"]["library"]["documents"] == 0
    listed = folders_of(ada)
    assert [f["state"] for f in listed["folders"]] == ["withdrawn"] and listed["grants"] == []
    assert (
        ada.patch(f"/api/documents/folders/{gid}", json={"includeHidden": True}).status_code == 409
    )
    assert sync_now(ada, gid).status_code == 409
    time.sleep(0.3)
    assert by_path(ada, gid) == {}, "and nothing came back"
    assert list(sync_of(world).begun) == begun, "no check of it was begun after the grant went"
    assert ada.delete(f"/api/documents/folders/{gid}").status_code == 204, "dismissed"
    assert ada.get(f"/api/documents/folders/{gid}").status_code == 404
    assert add_folder(ada, gid).status_code == 404, "the grant is gone: it cannot be added"


def test_a_grant_withdrawn_while_a_check_is_reading_leaves_nothing_added_after_it(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(6):
        write(notes, f"f{n}.md", md(f"File {n} has words."))
    age(notes)
    owner, ada, gid = setup(world, notes)
    reading, release = threading.Event(), threading.Event()
    real = folder_io.read_file
    calls: list[int] = []

    def slow(*args: Any) -> Any:
        calls.append(1)
        if len(calls) == 3:
            reading.set()
            assert release.wait(30)
        return real(*args)

    monkeypatch.setattr(folder_io, "read_file", slow)
    assert add_folder(ada, gid).status_code == 201
    assert reading.wait(30)
    outcome: dict[str, Any] = {}

    def withdraw() -> None:
        outcome["status"] = owner.delete(f"/api/folders/{gid}").status_code

    thread = threading.Thread(target=withdraw)
    thread.start()
    time.sleep(0.4)
    assert thread.is_alive(), (
        "the withdrawal waits for the file that is being read, as a tool's would"
    )
    release.set()
    thread.join(30)
    assert outcome == {"status": 204}
    shown = folder_of(ada, gid)
    assert shown["state"] == "withdrawn" and by_path(ada, gid) == {}
    time.sleep(0.5)
    assert by_path(ada, gid) == {} and words(ada, "File") == set(), "nothing was added after it"


def test_a_withdrawal_the_check_itself_began_is_waited_for_before_the_owners_request_returns(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(4):
        write(notes, f"f{n}.md", md(f"File {n} has words."))
    age(notes)
    owner, ada, gid = setup(world, notes)
    reading, release, purging = threading.Event(), threading.Event(), threading.Event()
    real_read = folder_io.read_file
    calls: list[int] = []

    def slow(*args: Any) -> Any:
        calls.append(1)
        if len(calls) == 2:
            reading.set()
            assert release.wait(30)
        return real_read(*args)

    monkeypatch.setattr(folder_io, "read_file", slow)
    real_purge = folder_sync.FolderSync._purge

    async def slow_purge(
        self: folder_sync.FolderSync, sub: str, grant_id: str, *, close: bool
    ) -> int:
        purging.set()
        await asyncio.sleep(0.5)  # the documents are still there for half a second
        return await real_purge(self, sub, grant_id, close=close)

    monkeypatch.setattr(folder_sync.FolderSync, "_purge", slow_purge)
    real_hook = folder_sync.FolderSync.grant_withdrawn

    async def hook_once_the_check_has_begun(self: folder_sync.FolderSync, grant_id: str) -> int:
        # The owner's request reaches Workbench's side of it after the check, finding its folder's
        # grant gone, has begun to withdraw the folder itself.
        assert await asyncio.to_thread(purging.wait, 30)
        return await real_hook(self, grant_id)

    monkeypatch.setattr(folder_sync.FolderSync, "grant_withdrawn", hook_once_the_check_has_begun)
    assert add_folder(ada, gid).status_code == 201
    assert reading.wait(30)
    outcome: dict[str, Any] = {}

    def withdraw() -> None:
        outcome["status"] = owner.delete(f"/api/folders/{gid}").status_code

    thread = threading.Thread(target=withdraw)
    thread.start()
    time.sleep(0.3)
    release.set()
    thread.join(30)
    assert outcome == {"status": 204} and purging.is_set()
    assert by_path(ada, gid) == {}, "the documents were out of the index when the request returned"
    shown = folder_of(ada, gid)
    assert shown["state"] == "withdrawn" and shown["problem"]["removed"] == 2, (
        "said once, by the withdrawal that was done, with how many documents it took out"
    )


# --------------------------------------------------------------------------- #
# the person removes the folder
# --------------------------------------------------------------------------- #


def test_removing_a_folder_takes_its_documents_out_and_never_touches_the_folder_or_the_grant(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    write(notes, "sub/b.md", md("More."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    before = snapshot(notes)
    ids = [d["id"] for d in by_path(ada, gid).values()]
    removed = ada.delete(f"/api/documents/folders/{gid}")
    assert removed.status_code == 204
    assert ada.get(f"/api/documents/folders/{gid}").status_code == 404
    assert words(ada, RARE) == set()
    for doc_id in ids:
        assert ada.get(f"/api/documents/{doc_id}").status_code == 404
    assert snapshot(notes) == before, "the folder is as it was, to the byte and the time"
    grants = ada.get("/api/folders").json()["grants"]
    assert [g["id"] for g in grants] == [gid] and grants[0]["usable"] is True, (
        "and the grant stands"
    )
    assert folders_of(ada)["folders"] == [] and folders_of(ada)["grants"][0]["added"] is False
    assert ada.delete(f"/api/documents/folders/{gid}").status_code == 404
    again = add_and_settle(ada, gid)
    assert again["counts"]["files"] == 2 and words(ada, RARE) == {"Work notes/a.md"}
    assert not [p for p in knowledge_files(world) if p.name.endswith(".part")]


def test_removing_a_folder_while_it_is_being_read_ends_the_check_and_nothing_is_added_after(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(6):
        write(notes, f"f{n}.md", md(f"File {n} has words."))
    age(notes)
    _, ada, gid = setup(world, notes)
    reading, release = threading.Event(), threading.Event()
    real = folder_io.read_file
    calls: list[int] = []

    def slow(*args: Any) -> Any:
        calls.append(1)
        if len(calls) == 3:
            reading.set()
            assert release.wait(30)
        return real(*args)

    monkeypatch.setattr(folder_io, "read_file", slow)
    assert add_folder(ada, gid).status_code == 201
    assert reading.wait(30)
    outcome: dict[str, Any] = {}

    def remove() -> None:
        outcome["status"] = ada.delete(f"/api/documents/folders/{gid}").status_code

    thread = threading.Thread(target=remove)
    thread.start()
    time.sleep(0.3)
    release.set()
    thread.join(30)
    assert outcome == {"status": 204}
    assert ada.get(f"/api/documents/folders/{gid}").status_code == 404
    time.sleep(0.5)
    assert words(ada, "File") == set(), "nothing was added after it"
    with raw(world) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone() == (0,)
    assert len(calls) <= 4, "the check was ended, not run to its end"


def test_removing_a_folder_ends_the_search_model_request_that_was_only_for_its_files(
    world: World, notes: Path
) -> None:
    big = "\n\n".join(f"# Part {n}\n\nParagraph {n} about the harbour." for n in range(40))
    write(notes, "big.md", big.encode())
    age(notes)
    _, ada, gid = setup(world, notes, search_model=True)
    world.gateway.embed_gate = threading.Event()
    assert add_folder(ada, gid).status_code == 201
    until(lambda: world.gateway.embed_in_flight == 1)
    assert ada.delete(f"/api/documents/folders/{gid}").status_code == 204
    until(lambda: world.gateway.embed_disconnects == 1, seconds=20)
    assert world.gateway.embed_in_flight == 0, "the request was ended, not merely ignored"
    world.gateway.embed_gate.set()
    assert vectors_of(world) == []


def test_a_folder_added_twice_is_refused_and_can_be_added_again_once_it_is_removed(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", md("Words."))
    _, ada, gid = setup(world, notes)
    assert add_folder(ada, gid).status_code == 201
    twice = add_folder(ada, gid)
    assert twice.status_code == 409 and twice.json()["detail"]["message"] == (
        "This folder is already in your documents."
    )
    assert ada.delete(f"/api/documents/folders/{gid}").status_code == 204
    assert add_folder(ada, gid).status_code == 201


# --------------------------------------------------------------------------- #
# a stop and a start
# --------------------------------------------------------------------------- #


def restart(world: World, edit: Any = None) -> None:
    """Workbench stops, `edit(path of its store)` is done to what it left, and it starts again."""
    world.server.stop()
    if edit is not None:
        edit(world.data / "workbench.sqlite3")
    world.server = Server(create_app(world.settings), world.server.port).start()


def test_a_restart_keeps_the_folders_and_reads_nothing_again(world: World, notes: Path) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    _, ada, gid = setup(world, notes)
    done = add_and_settle(ada, gid)
    before = by_path(ada, gid)["a.md"]
    restart(world)
    assert folder_of(ada, gid)["state"] in ("waiting", "syncing", "synced")
    after = settle_folder(ada, gid, after=done["checkedAt"])  # every folder is checked at boot
    kept = by_path(ada, gid)["a.md"]
    assert (kept["id"], kept["indexedAt"]) == (before["id"], before["indexedAt"])
    assert after["last"]["added"] == 0 and after["last"]["changed"] == 0
    assert after["last"]["unchanged"] == 1 and after["last"]["mode"] == "quick"
    assert words(ada, RARE) == {"Work notes/a.md"}


def test_what_changed_while_workbench_was_stopped_is_found_when_it_starts(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    write(notes, "gone.md", md("Deleted while Workbench was off."))
    age(notes)
    _, ada, gid = setup(world, notes)
    done = add_and_settle(ada, gid)
    world.server.stop()
    write(notes, "arrived.md", md(f"The {SECOND} words arrived."))
    (notes / "gone.md").unlink()
    world.server = Server(create_app(world.settings), world.server.port).start()
    after = settle_folder(ada, gid, after=done["checkedAt"])
    assert set(by_path(ada, gid)) == {"a.md", "arrived.md"}, "found at boot, with nobody asking"
    assert after["last"]["added"] == 1 and after["last"]["removed"] == 1
    assert words(ada, SECOND) == {"Work notes/arrived.md"} and words(ada, "Deleted") == set()


@pytest.mark.parametrize("loop_runs", [True, False], ids=["with the loop", "boot alone"])
def test_a_grant_withdrawn_while_workbench_was_stopped_is_found_when_it_starts(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch, loop_runs: bool
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)

    def drop_the_grant(path: Path) -> None:
        with sqlite3.connect(path) as db:
            db.execute("DELETE FROM folder_grants WHERE id = ?", (gid,))

    if not loop_runs:
        # The loop's own first look would do the same a moment later: this is the start alone.
        async def idle(self: folder_sync.FolderSync) -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(folder_sync.FolderSync, "_loop", idle)
    restart(world, drop_the_grant)
    shown = folder_of(ada, gid)
    assert shown["state"] == "withdrawn" and shown["problem"]["removed"] == 1
    assert by_path(ada, gid) == {} and words(ada, RARE) == set()


def test_a_folder_whose_person_is_no_longer_known_is_withdrawn_and_not_checked_again_and_again(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)

    def forget_ada(path: Path) -> None:
        with sqlite3.connect(path) as db:
            db.execute("DELETE FROM people WHERE sub = 'p-ada'")

    # The grant is still there and names her, but there is no one of that name to read for.
    restart(world, forget_ada)
    end, found = time.perf_counter() + 30, None
    while time.perf_counter() < end and found != ("withdrawn",):
        with sqlite3.connect(world.data / "workbench.sqlite3") as db:
            found = db.execute(
                "SELECT status FROM document_folders WHERE grant_id = ?", (gid,)
            ).fetchone()
        time.sleep(0.05)
    assert found == ("withdrawn",)
    with raw(world) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone() == (0,)


def test_documents_of_a_folder_the_person_no_longer_has_are_removed_when_workbench_starts(
    world: World, notes: Path
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    assert words(ada, RARE)

    def lose_the_record(path: Path) -> None:
        with sqlite3.connect(path) as db:
            db.execute("DELETE FROM document_folders WHERE grant_id = ?", (gid,))

    # what a stop in the middle of removing a folder leaves: the folder is gone, its documents are not
    restart(world, lose_the_record)
    assert folders_of(ada)["folders"] == []
    with raw(world) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone() == (0,)
    assert words(ada, RARE) == set()


# --------------------------------------------------------------------------- #
# checks that come by themselves
# --------------------------------------------------------------------------- #


def test_a_folder_is_checked_again_by_itself_every_interval(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(folder_sync, "UNIT", 0.05)
    settings(world, documentsFolderSyncMinutes=1)  # a minute of 0.05 seconds
    write(notes, "a.md", md("First."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    write(notes, "new.md", md(f"The {RARE} arrival."))
    end = time.perf_counter() + 30
    while time.perf_counter() < end and "new.md" not in by_path(ada, gid):
        time.sleep(0.05)
    assert "new.md" in by_path(ada, gid), "nobody asked: the check came by itself"
    (notes / "new.md").write_bytes(md(f"The {SECOND} arrival, edited."))
    while time.perf_counter() < end and words(ada, SECOND) == set():
        time.sleep(0.05)
    assert words(ada, SECOND) == {"Work notes/new.md"} and words(ada, RARE) == set()
    first, later = checked_at(ada, gid), 0.0
    while time.perf_counter() < end and later <= first:
        time.sleep(0.05)
        later = checked_at(ada, gid)
    assert later > first, "and again"


def test_a_folder_is_not_checked_again_before_its_interval_is_up(world: World, notes: Path) -> None:
    write(notes, "a.md", md("First."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    first, begun = checked_at(ada, gid), len(sync_of(world).begun)
    write(notes, "later.md", md(f"The {RARE} arrival."))
    time.sleep(0.8)  # sixteen looks of the loop, with the interval at ten minutes
    assert checked_at(ada, gid) == first and "later.md" not in by_path(ada, gid)
    assert len(sync_of(world).begun) == begun, "no check began"


def test_a_folder_whose_check_failed_is_left_alone_for_a_while_even_when_it_was_asked_for_again(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    entered, release = threading.Event(), threading.Event()
    calls: list[int] = []

    def failing(*args: Any) -> Any:
        calls.append(1)
        entered.set()
        assert release.wait(30)
        raise PermissionError(13, "Access is denied.")

    monkeypatch.setattr(folder_io, "scan", failing)
    assert sync_now(ada, gid).status_code == 202
    assert entered.wait(30)
    assert sync_now(ada, gid).status_code == 202, "asked again while the check is under way"
    release.set()
    until(lambda: folder_of(ada, gid)["problem"] is not None)
    time.sleep(0.5)
    assert len(calls) == 1, "the check that was asked for waits: the folder just failed"
    assert sync_now(ada, gid).status_code == 202
    until(lambda: len(calls) == 2)  # but a person who asks again is not made to wait


def test_a_file_that_cannot_be_removed_while_a_check_goes_on_is_said_as_that(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    said = (
        "Workbench could not remove a file of a document (in use). Nothing was removed; close "
        "whatever is using it and try again."
    )

    async def stuck(self: SavedDocuments, sub: str, doc_ids: Any) -> Any:
        raise DeleteFailed(said)

    monkeypatch.setattr(SavedDocuments, "remove_documents", stuck)
    (notes / "a.md").unlink()
    assert sync_now(ada, gid).status_code == 202
    until(lambda: folder_of(ada, gid)["state"] == "problem")
    shown = folder_of(ada, gid)
    assert shown["problem"]["code"] == "delete_failed" and shown["problem"]["message"] == said
    assert shown["nextAt"] is None, "a next check is promised only for a folder that is synced"
    assert set(by_path(ada, gid)) == {"a.md"}, "nothing was removed, and nothing says it was"


def test_a_folder_that_is_not_there_is_looked_for_at_every_check_and_comes_back_by_itself(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(folder_sync, "UNIT", 0.05)
    monkeypatch.setattr(folder_sync, "BACKOFF", 0.1)
    settings(world, documentsFolderSyncMinutes=1)
    write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    away = notes.with_name(notes.name + "-away")
    notes.rename(away)
    end = time.perf_counter() + 30
    while time.perf_counter() < end and folder_of(ada, gid)["state"] != "unavailable":
        time.sleep(0.05)
    assert folder_of(ada, gid)["state"] == "unavailable" and by_path(ada, gid) == {}
    away.rename(notes)
    while time.perf_counter() < end and "a.md" not in by_path(ada, gid):
        time.sleep(0.05)
    assert "a.md" in by_path(ada, gid), "nobody asked: it came back with the folder"
    assert folder_of(ada, gid)["problem"] is None


def test_checks_are_not_made_while_the_owner_has_turned_documents_off(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(folder_sync, "UNIT", 0.05)
    write(notes, "a.md", md("First."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    service = sync_of(world)
    cycle = service._cycle
    disabled_cycles: list[float] = []

    async def observed_cycle() -> float:
        delay = await cycle()
        if not (await service._documents()).enabled:
            disabled_cycles.append(time.perf_counter())
        return float(delay)

    monkeypatch.setattr(service, "_cycle", observed_cycle)
    settings(world, documentsEnabled=False, documentsFolderSyncMinutes=1)
    # Wait until the loop has observed the disabled setting and any earlier check
    # has finished. A fixed sleep can expire without the loop running under load.
    until(lambda: len(disabled_cycles) >= 1)
    write(notes, "new.md", md("Arrives while documents are off."))
    age(notes)  # This check is about the owner's switch, not a file still being saved.
    before = len(disabled_cycles)
    until(lambda: len(disabled_cycles) >= before + 2)
    assert set(by_path(ada, gid)) == {"a.md"}
    assert sync_now(ada, gid).status_code == 403
    previous = checked_at(ada, gid)
    settings(world, documentsEnabled=True)
    settle_folder(ada, gid, after=previous)
    assert "new.md" in by_path(ada, gid), "and it is looked at again once they are on"


def test_what_the_loop_takes_next_is_a_manual_check_then_a_first_check_then_a_scheduled_one(
    tmp_path: Path,
) -> None:
    async def body() -> list[str]:
        store = Store(tmp_path / "workbench.sqlite3")
        await store.open()
        await store.upsert_person(Person("p-ada", "Ada", "member"))
        for grant_id, checked in (
            ("scheduled", time.time() - 10_000),
            ("first", None),
            ("manual", time.time() - 10_000),
        ):
            await store.add_folder_record(
                FolderRecord(
                    "p-ada", grant_id, grant_id, 1.0, checked_at=checked, synced_at=checked
                )
            )
        order: list[str] = []

        class Documents:
            enabled = True

        class Folder:
            sync_minutes = 10

        async def documents() -> Any:
            return Documents()

        async def folder() -> Any:
            return Folder()

        sync = folder_sync.FolderSync(store, None, None, documents, folder)  # type: ignore[arg-type]

        async def fake_run(record: FolderRecord, want: folder_sync.Want) -> None:
            order.append(f"{record.grant_id}:{'manual' if want.manual else 'quiet'}")
            await store.update_folder_record("p-ada", record.grant_id, checked_at=time.time())

        sync._run = fake_run  # type: ignore[method-assign]

        # the store has no grants: say they are all there, so that none is withdrawn
        async def grants() -> list[dict[str, Any]]:
            return [
                {"id": g, "subject": "p-ada", "name": g, "writable": 0}
                for g in ("scheduled", "first", "manual")
            ]

        store.folder_grants = grants  # type: ignore[method-assign]
        sync.request("p-ada", "manual", manual=True)
        for _ in range(3):
            assert await sync._cycle() == 0.0
        await store.close()
        return order

    assert asyncio.run(body()) == ["manual:manual", "first:quiet", "scheduled:quiet"]


def test_two_folders_of_one_person_are_checked_apart_and_one_failing_does_not_stop_the_other(
    world: World, notes: Path, more_notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    write(more_notes, "b.md", md(f"The {SECOND} words."))
    age(notes)
    age(more_notes)
    owner, ada, one = setup(world, notes)
    two = grant(world, owner, more_notes, name="Other notes")
    real = folder_io.scan

    def scan(path: str, expected: str, protected: list[Path], walker: Any) -> Any:
        if Path(path) == more_notes:
            raise PermissionError(13, "denied")
        return real(path, expected, protected, walker)

    monkeypatch.setattr(folder_io, "scan", scan)
    first = add_and_settle(ada, one)
    assert add_folder(ada, two).status_code == 201
    broken = settle_folder(ada, two, ends=("problem",))
    assert broken["problem"]["code"] == "unreadable" and first["state"] == "synced"
    assert words(ada, RARE) == {"Work notes/a.md"}
    monkeypatch.setattr(folder_io, "scan", real)
    fixed = sync_and_settle(ada, two)
    assert fixed["state"] == "synced" and words(ada, SECOND) == {"Other notes/b.md"}


# --------------------------------------------------------------------------- #
# a file that cannot be removed
# --------------------------------------------------------------------------- #


def flaky_removal(monkeypatch: pytest.MonkeyPatch, failures: int) -> list[int]:
    """Removing a folder's documents fails this many times, as it does while a program holds a file,
    and then works. The list says how many times it was tried."""
    real = SavedDocuments.drop_folder
    tries: list[int] = []

    async def flaky(self: SavedDocuments, sub: str, grant_id: str) -> Any:
        tries.append(1)
        if len(tries) <= failures:
            raise DeleteFailed("A program is holding a file of this folder. Nothing was removed.")
        return await real(self, sub, grant_id)

    monkeypatch.setattr(SavedDocuments, "drop_folder", flaky)
    return tries


def test_a_folder_whose_files_cannot_be_removed_stays_to_be_removed_again(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    _, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    tries = flaky_removal(monkeypatch, 1)
    refused = ada.delete(f"/api/documents/folders/{gid}")
    assert refused.status_code == 500
    assert refused.json()["detail"]["message"] == (
        "A program is holding a file of this folder. Nothing was removed."
    )
    assert folder_of(ada, gid)["state"] == "synced", "it is still her folder, as it was"
    assert words(ada, RARE) == {"Work notes/a.md"}, "and its documents are all there"
    write(notes, "later.md", md(f"The {SECOND} words."))
    age(notes)
    assert sync_and_settle(ada, gid)["last"]["added"] == 1, "and it is still checked"
    assert ada.delete(f"/api/documents/folders/{gid}").status_code == 204
    assert len(tries) >= 2 and words(ada, RARE) == set()
    assert ada.get(f"/api/documents/folders/{gid}").status_code == 404


def test_a_withdrawn_grant_whose_files_cannot_be_removed_is_withdrawn_again_until_they_can(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(notes, "a.md", md(f"The {RARE} words."))
    age(notes)
    owner, ada, gid = setup(world, notes)
    add_and_settle(ada, gid)
    tries = flaky_removal(monkeypatch, 2)
    assert owner.delete(f"/api/folders/{gid}").status_code == 204, "the grant is gone, whatever"
    end = time.perf_counter() + 30
    while time.perf_counter() < end and folder_of(ada, gid)["state"] != "withdrawn":
        time.sleep(0.05)
    shown = folder_of(ada, gid)
    assert shown["state"] == "withdrawn" and shown["problem"]["removed"] == 1
    assert words(ada, RARE) == set() and by_path(ada, gid) == {}
    assert len(tries) >= 3, "tried at the request, and again by the loop, until it worked"
