"""Folders a person points their Documents at (MR7), through a real Workbench over a fake Eugene.

A person adds a folder the owner granted them; Workbench reads its files, read-only, into the
person's own index and checks it again; the files are documents like any other. What these check is
the wire: what a person is told at each step, what happens when a file is edited, deleted, ignored or
a link, when the grant is withdrawn, and what another person is never told.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from eugene_plexus_workbench import folder_io

from .conftest import ADMIN_TOKEN, PNG, World
from .folderdocs import (
    add_and_settle,
    add_folder,
    by_path,
    folder_of,
    folders_of,
    grant,
    owner_and_ada,
    settle_folder,
    sync_and_settle,
    write,
)
from .saved import RARE, SECOND, THIRD, ask, chat_on, index_of, listing, md, raw, settle, start

pytestmark = pytest.mark.usefixtures("quiet_loop")
OLD = 3600.0


def age(folder: Path, *, seconds: float = OLD) -> None:
    """Every file of the folder was last modified `seconds` ago, so none is too new to trust (a file
    modified within two and a half seconds of being read is read again at the next check)."""
    then = time.time() - seconds
    for found in folder.rglob("*"):
        if found.is_file():
            os.utime(found, (then, then))


def plan_file(folder: Path, name: str, *words: str, title: str = "Plan") -> Path:
    return write(folder, name, md(*words, title=title))


def no_search_model(world: World) -> None:
    """No search model is running, so documents are searched by their words: nothing waits for
    a vector."""
    world.gateway.embedder_listed = False


def tried(person: Any, question: str) -> dict[str, Any]:
    made = person.post("/api/documents/test", json={"question": question})
    assert made.status_code == 200, made.text
    return dict(made.json())


def found_in(person: Any, word: str) -> set[str]:
    """The names of the documents the words of a question find."""
    return {p["name"] for p in tried(person, f"the {word} word")["passages"]}


# --------------------------------------------------------------------------- #
# adding a folder: files become documents named by their folder and path
# --------------------------------------------------------------------------- #


def test_a_granted_folder_is_added_and_its_files_become_documents(
    world: World, notes: Path
) -> None:
    no_search_model(world)
    plan_file(notes, "plan.md", f"The harbour {RARE} plan.")
    write(notes, "sub/deep.txt", f"The {SECOND} words are deep.")
    age(notes)
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes, name="Work notes")
    made = add_folder(ada, gid)
    assert made.status_code == 201, made.text
    first = made.json()
    assert first["id"] == gid and first["name"] == "Work notes"
    assert first["state"] in ("waiting", "syncing", "synced") and first["granted"] is True
    done = settle_folder(ada, gid)
    assert done["state"] == "synced" and done["problem"] is None and done["syncing"] is None
    assert done["counts"]["files"] == 2 and done["counts"]["byState"]["ready"] == 2
    assert done["counts"]["passages"] >= 2 and done["counts"]["byCode"] == {}
    docs = by_path(ada, gid)
    assert set(docs) == {"plan.md", "sub/deep.txt"}
    plan = docs["plan.md"]
    assert plan["name"] == "Work notes/plan.md", "named by the folder and the path"
    assert (plan["folderId"], plan["path"]) == (gid, "plan.md")
    assert plan["state"] == "ready" and plan["scope"] == "library" and plan["label"] == "Markdown"
    assert docs["sub/deep.txt"]["name"] == "Work notes/sub/deep.txt"
    assert docs["sub/deep.txt"]["path"] == "sub/deep.txt"
    assert done["last"]["added"] == 2 and done["last"]["files"] == 2


def test_the_headline_says_when_it_was_checked_how_often_and_what_the_folder_holds(
    world: World, notes: Path
) -> None:
    no_search_model(world)
    plan_file(notes, "a.md", "Words.")
    write(notes, "pic.png", PNG)
    age(notes)
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    done = add_and_settle(ada, gid)
    assert done["headline"] == (
        "Checked just now, and again every 10 minutes. 2 files: 1 ready, 1 skipped."
    )
    assert done["nextAt"] is not None and done["nextAt"] > done["checkedAt"]
    assert done["nextAt"] - done["checkedAt"] == pytest.approx(600, abs=1)


def test_a_new_folder_says_so_before_its_first_check_and_how_far_it_has_read_while_it_reads(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_search_model(world)
    for n in range(6):
        plan_file(notes, f"f{n}.md", f"File {n} has words.")
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    reading, release = threading.Event(), threading.Event()
    real = folder_io.read_file
    calls: list[int] = []

    def slow(*args: Any) -> Any:
        calls.append(1)
        if len(calls) == 4:
            reading.set()
            assert release.wait(30)
        return real(*args)

    monkeypatch.setattr(folder_io, "read_file", slow)
    first = add_folder(ada, gid).json()
    assert first["state"] in ("waiting", "syncing") and first["syncedAt"] is None
    assert first["headline"] in (
        "Looking at this folder soon, for the first time.",
        "Looking at this folder.",
    )
    try:
        assert reading.wait(30)
        mid = folder_of(ada, gid)
        assert mid["state"] == "syncing" and mid["headline"] == "Reading this folder: 3 of 6 files."
        assert mid["syncing"]["phase"] == "reading"
        assert (mid["syncing"]["done"], mid["syncing"]["total"]) == (3, 6)
        assert mid["syncedAt"] is None and mid["nextAt"] is None
    finally:
        release.set()
    done = settle_folder(ada, gid)
    assert done["state"] == "synced" and done["counts"]["files"] == 6 and done["syncing"] is None


def test_the_files_of_a_folder_are_found_by_their_words_and_the_chat_names_them_in_its_sources(
    world: World, notes: Path
) -> None:
    no_search_model(world)
    plan_file(notes, "budget/plan.md", f"The harbour budget {RARE} is approved.", title="Budget")
    plan_file(notes, "other.md", "Nothing here about it.")
    age(notes)
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes, name="Work notes")
    add_and_settle(ada, gid)
    looked = tried(ada, f"What is the {RARE} budget?")
    assert looked["status"] == "found"
    top = looked["passages"][0]
    assert top["name"] == "Work notes/budget/plan.md" and RARE in top["text"]
    chat = chat_on(ada)
    answer = ask(ada, chat, f"What is the {RARE} budget?")
    assert answer["status"] == "done"
    passages = [s for s in answer["sources"] if s.get("passage")]
    assert passages and passages[0]["kind"] == "document"
    assert passages[0]["name"] == "Work notes/budget/plan.md", "the Sources name the file"
    assert RARE in passages[0]["snippet"] and passages[0]["scope"] == "library"
    assert answer["retrieval"]["status"] == "used"


def test_a_question_that_names_the_file_by_its_own_name_finds_it_not_only_by_its_folder_path(
    world: World, notes: Path
) -> None:
    no_search_model(world)
    paragraphs = [f"Paragraph {n} of the budget plan." for n in range(1, 9)]
    plan_file(notes, "deeply/nested/budget plan.md", *paragraphs, title="Budget plan")
    age(notes)
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes, name="Work notes")
    add_and_settle(ada, gid)
    looked = ada.post(
        "/api/documents/test", json={"question": "what is in the budget plan", "passages": 5}
    ).json()
    assert looked["status"] == "found"
    assert {p["name"] for p in looked["passages"]} == {"Work notes/deeply/nested/budget plan.md"}


def test_the_listing_of_a_persons_own_documents_holds_only_what_they_added_and_a_summary(
    world: World, notes: Path
) -> None:
    no_search_model(world)
    plan_file(notes, "in-folder.md", "In a folder.")
    age(notes)
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    add_and_settle(ada, gid)
    assert (
        ada.post(
            "/api/documents", files=[("files", ("mine.md", md("Mine."), "text/markdown"))]
        ).status_code
        == 201
    )
    shown = settle(ada)
    assert [d["name"] for d in shown["documents"]] == ["mine.md"]
    assert shown["totals"]["documents"] == 1 and shown["totals"]["passages"] == 1
    assert "folders" not in shown, "the shape the page reads is as it was"
    again = listing(ada)
    assert [d["name"] for d in again["documents"]] == ["mine.md"]
    chip = ada.get(f"/api/chats/{chat_on(ada)}/documents").json()
    assert chip["counts"]["library"]["documents"] == 2, "a chat searches both"


def test_the_folders_listing_is_not_taken_for_a_documents_id(world: World) -> None:
    _, ada = owner_and_ada(world)
    found = ada.get("/api/documents/folders")
    assert found.status_code == 200 and set(found.json()) >= {"folders", "grants", "limits"}
    assert found.json()["folders"] == []
    assert ada.get("/api/documents/folders/nothing-here").status_code == 404
    missing = ada.get("/api/documents/nothing-here")
    assert missing.status_code == 404 and missing.json()["detail"]["message"] == (
        "There is no such document."
    ), "a document's route still says what it always did"


def test_a_person_sees_the_granted_folders_they_may_add_and_never_a_path(
    world: World, notes: Path, more_notes: Path
) -> None:
    owner, ada = owner_and_ada(world)
    bo = start(world, "p-bo")
    mine = grant(world, owner, notes, name="Ada's notes")
    theirs = grant(world, owner, more_notes, subject="p-bo", name="Bo's notes", writable=True)
    seen = folders_of(ada)
    assert seen["available"] is True and seen["reason"] is None and seen["enabled"] is True
    assert seen["grants"] == [
        {"id": mine, "name": "Ada's notes", "writable": False, "added": False}
    ]
    assert str(notes) not in str(seen) and "path" not in seen["grants"][0]
    add_and_settle(ada, mine)
    assert folders_of(ada)["grants"][0]["added"] is True
    assert folders_of(bo)["grants"] == [
        {"id": theirs, "name": "Bo's notes", "writable": True, "added": False}
    ]
    assert folders_of(owner)["grants"] == [], "the owner is offered only what names the owner"


# --------------------------------------------------------------------------- #
# the limits are on the screen
# --------------------------------------------------------------------------- #


def test_every_limit_and_the_rules_always_in_force_are_stated(world: World) -> None:
    _, ada = owner_and_ada(world)
    limits = folders_of(ada)["limits"]
    assert limits["syncMinutes"] == 10 and limits["maxFiles"] == 10_000
    assert limits["maxMB"] == 2048 and limits["maxBytes"] == 2048 * 1024 * 1024
    assert limits["maxDepth"] == 12 and limits["maxFileMB"] == 50
    assert limits["maxPassages"] == 100_000 and limits["maxIgnore"] == 100
    assert limits["reads"].startswith("Workbench reads PDF, Word")
    rules = limits["rules"]
    assert ".git" in rules["folders"] and "node_modules" in rules["folders"]
    assert "__pycache__" in rules["folders"] and ".venv" in rules["folders"]
    assert "Thumbs.db" in rules["files"] and "*.tmp" in rules["temporary"]
    assert "dot" in rules["hidden"] and ".gitignore" in rules["gitignore"]


def test_the_limits_follow_the_installs_settings_and_a_folder_shows_how_near_it_is(
    world: World, notes: Path
) -> None:
    no_search_model(world)
    plan_file(notes, "a.md", "Words in a file.")
    age(notes)
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    set_install(
        documentsFolderMaxFiles=500,
        documentsFolderMaxMB=100,
        documentsFolderMaxDepth=3,
        documentsFolderSyncMinutes=20,
        world=world,
    )
    done = add_and_settle(ada, gid)
    limits = folders_of(ada)["limits"]
    assert (limits["maxFiles"], limits["maxMB"], limits["maxDepth"], limits["syncMinutes"]) == (
        500,
        100,
        3,
        20,
    )
    caps = done["caps"]
    assert caps["files"] == {"limit": 500, "found": 1, "reached": False}
    assert caps["bytes"]["limit"] == 100 * 1024 * 1024 and caps["bytes"]["used"] > 0
    assert caps["bytes"]["reached"] is False
    assert (
        caps["depth"] == {"limit": 3, "reached": 0} and caps["fileBytes"]["limit"] == 50 * 1024**2
    )
    assert "every 20 minutes" in done["headline"]


def set_install(world: World, **values: Any) -> None:
    changed = httpx.patch(
        f"{world.workbench}/v1/config",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json=values,
        trust_env=False,
    )
    assert changed.status_code == 200 and not changed.json()["rejected"], changed.text


# --------------------------------------------------------------------------- #
# keeping in step: new, edited and deleted files
# --------------------------------------------------------------------------- #


def test_an_edited_file_is_read_again_the_new_words_answer_and_the_old_are_gone(
    world: World, notes: Path
) -> None:
    no_search_model(world)
    plan_file(notes, "plan.md", f"The old {RARE} plan.")
    age(notes)
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes, name="Work notes")
    add_and_settle(ada, gid)
    before = by_path(ada, gid)["plan.md"]
    assert found_in(ada, RARE) == {"Work notes/plan.md"}
    plan_file(notes, "plan.md", f"The new {SECOND} plan.")
    done = sync_and_settle(ada, gid)
    after = by_path(ada, gid)["plan.md"]
    assert after["id"] != before["id"] and after["state"] == "ready"
    assert found_in(ada, SECOND) == {"Work notes/plan.md"}, "the new words answer"
    assert found_in(ada, RARE) == set(), "and the old are gone: nothing stale is left"
    assert ada.get(f"/api/documents/{before['id']}").status_code == 404
    assert (
        done["last"]["changed"] == 1 and done["last"]["added"] == 0 and done["last"]["removed"] == 0
    )
    chat = chat_on(ada)
    reply = ask(ada, chat, f"What is the {RARE} plan?")
    assert RARE not in str(reply["sources"]), "the old text is not sent to a model"
    # The person's own questions are in their index too, as their past chats (MV2): the
    # chat goes, so what is left is the documents'.
    assert ada.delete(f"/api/chats/{chat}").status_code == 204
    index = index_of(world)
    with raw(world) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone() == (1,)
        assert db.execute(
            "SELECT COUNT(*) FROM chunks WHERE text LIKE ?", (f"%{RARE}%",)
        ).fetchone() == (0,)
    assert RARE.encode() not in index.read_bytes(), "not even in the file, once it is scrubbed"


def test_a_deleted_file_is_removed_with_its_words(world: World, notes: Path) -> None:
    no_search_model(world)
    plan_file(notes, "keep.md", f"The {SECOND} file stays.")
    plan_file(notes, "doomed.md", f"The {RARE} file goes.")
    age(notes)
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    add_and_settle(ada, gid)
    doomed = by_path(ada, gid)["doomed.md"]
    assert found_in(ada, RARE)
    (notes / "doomed.md").unlink()
    done = sync_and_settle(ada, gid)
    assert set(by_path(ada, gid)) == {"keep.md"}
    assert found_in(ada, RARE) == set() and found_in(ada, SECOND)
    assert ada.get(f"/api/documents/{doomed['id']}").status_code == 404
    assert done["last"]["removed"] == 1 and done["counts"]["files"] == 1
    assert RARE.encode() not in index_of(world).read_bytes()


def test_a_new_file_is_picked_up_at_the_next_check(world: World, notes: Path) -> None:
    no_search_model(world)
    plan_file(notes, "a.md", "First.")
    age(notes)
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    add_and_settle(ada, gid)
    plan_file(notes, "fresh/new.md", f"The {THIRD} arrival.")
    done = sync_and_settle(ada, gid)
    assert set(by_path(ada, gid)) == {"a.md", "fresh/new.md"} and done["last"]["added"] == 1
    assert found_in(ada, THIRD) == {"Work notes/fresh/new.md"}


def test_a_file_that_is_touched_but_says_the_same_is_not_read_again(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_search_model(world)
    path = plan_file(notes, "plan.md", f"The {RARE} plan.")
    age(notes)
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    add_and_settle(ada, gid)
    before = by_path(ada, gid)["plan.md"]
    reads: list[list[str]] = []
    real = folder_io.read_file

    def counted(path_: str, expected: str, protected: Any, names: list[str], *rest: Any) -> Any:
        reads.append(names)
        return real(path_, expected, protected, names, *rest)

    monkeypatch.setattr(folder_io, "read_file", counted)
    later = time.time() - 60
    os.utime(path, (later, later))  # saved again, with the same words
    done = sync_and_settle(ada, gid)
    assert reads == [["plan.md"]], "it was read once, to see that it says the same"
    after = by_path(ada, gid)["plan.md"]
    assert after["id"] == before["id"] and after["indexedAt"] == before["indexedAt"]
    assert after["state"] == "ready" and done["last"]["changed"] == 0 and done["last"]["added"] == 0
    assert done["last"]["unchanged"] == 1
    # and what it was told is the new time: the next check does not look at it again
    again = sync_and_settle(ada, gid)
    assert again["last"]["unchanged"] == 1 and by_path(ada, gid)["plan.md"]["id"] == before["id"]
    assert reads == [["plan.md"]], "and not again: what is kept is the time it is at now"


def test_a_file_too_new_to_trust_is_looked_at_again_so_a_second_edit_in_the_same_tick_is_not_missed(
    world: World, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "Too new" is read within `RACY_NS` of being written; a slow runner (Windows CI) can take
    # longer than that to read it the first time, and the file is then old enough to trust.
    from eugene_plexus_workbench.knowledge import folder_sync

    monkeypatch.setattr(folder_sync, "RACY_NS", 120_000_000_000)
    no_search_model(world)
    path = plan_file(notes, "plan.md", f"The {RARE} plan, the old one.")
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    add_and_settle(ada, gid)  # the file is seconds old: its time is marked untrusted
    stamp, size = path.stat().st_mtime_ns, path.stat().st_size
    plan_file(notes, "plan.md", f"The {THIRD} plan, the new one.")
    assert path.stat().st_size == size, "the same size"
    os.utime(path, ns=(stamp, stamp))  # and the same time to the tick
    done = sync_and_settle(ada, gid)
    assert found_in(ada, THIRD) == {"Work notes/plan.md"} and found_in(ada, RARE) == set()
    assert done["last"]["changed"] == 1


def test_an_edit_that_keeps_the_size_and_the_time_of_an_old_file_is_missed_by_a_quick_check_and_found_by_a_full_one(
    world: World, notes: Path
) -> None:
    no_search_model(world)
    path = plan_file(notes, "plan.md", f"The {RARE} plan, the old one.")
    age(notes)
    stamp, size = path.stat().st_mtime_ns, path.stat().st_size
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes)
    add_and_settle(ada, gid)
    plan_file(notes, "plan.md", f"The {THIRD} plan, the new one.")
    assert path.stat().st_size == size
    os.utime(path, ns=(stamp, stamp))
    quick = sync_and_settle(ada, gid)
    assert found_in(ada, RARE) and quick["last"]["unchanged"] == 1, "size and time say as it was"
    full = sync_and_settle(ada, gid, deep=True)
    assert full["last"]["mode"] == "deep" and full["last"]["changed"] == 1
    assert found_in(ada, THIRD) and not found_in(ada, RARE), "a full check hashes every file"


# --------------------------------------------------------------------------- #
# a folder's file is a document like any other
# --------------------------------------------------------------------------- #


def test_a_folder_file_opens_as_its_own_file_shows_its_text_and_can_be_read_again(
    world: World, notes: Path
) -> None:
    no_search_model(world)
    body = md(f"The {RARE} words of the plan.", title="Plan")
    write(notes, "plans/plan.md", body)
    write(notes, "pic.png", PNG)
    age(notes)
    owner, ada = owner_and_ada(world)
    gid = grant(world, owner, notes, name="Work notes")
    add_and_settle(ada, gid)
    got = by_path(ada, gid)
    plan, pic = got["plans/plan.md"], got["pic.png"]
    original = ada.get(f"/api/documents/{plan['id']}/original")
    assert original.status_code == 200 and original.content == body
    assert 'filename="plan.md"' in original.headers["content-disposition"], "the file's own name"
    assert "Work notes" not in original.headers["content-disposition"]
    shown = ada.get(f"/api/documents/{plan['id']}/text")
    assert shown.status_code == 200 and RARE in shown.text
    assert ada.get(f"/api/documents/{plan['id']}").json()["name"] == "Work notes/plans/plan.md"
    again = ada.post(f"/api/documents/{plan['id']}/reread")
    assert again.status_code == 200 and again.json()["id"] == plan["id"]
    done = settle_folder(ada, gid)
    assert by_path(ada, gid)["plans/plan.md"]["state"] == "ready" and done["counts"]["files"] == 2
    gone = ada.post(f"/api/documents/{pic['id']}/reread")
    assert gone.status_code == 409 and gone.json()["detail"]["message"] == (
        "Workbench has no copy of this file to read again (it was not read). Use Sync now on its "
        "folder."
    )
    assert ada.get(f"/api/documents/{pic['id']}/original").status_code == 404
    refused = ada.get(f"/api/documents/{pic['id']}/text")
    assert refused.status_code == 409 and refused.json()["detail"]["message"].startswith("Skipped:")
    passages = ada.get(f"/api/documents/{plan['id']}/passages", params={"ids": "1"})
    assert passages.status_code == 200 and passages.json()["document"]["name"] == (
        "Work notes/plans/plan.md"
    )


def test_a_path_in_a_files_name_does_not_stop_a_question_from_naming_the_file() -> None:
    from eugene_plexus_workbench.knowledge.search import names_document

    name = "Work notes/deeply/nested/budget plan.md"
    assert names_document(name, "what is in the budget plan")
    assert not names_document(name, "what is in the plan")
    assert names_document("budget plan.md", "what is in the budget plan"), "an upload as it was"
    assert not names_document("Work notes/a/x.md", "what is in x"), "a name too short to mean it"
