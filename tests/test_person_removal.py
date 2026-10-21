"""Row 7: a removed person's data goes with them (`specs/docs/design/rows/row-07.md`).

When Eugene no longer has a person (`POST /oidc/subjects` says `unknown`), Workbench hides
their data at once and deletes it after `removedPeopleDays` (30), or at once when the owner
presses Delete now. Deleting is a true delete (MRC27): nothing of theirs is left in the store,
its write-ahead log, any `*.schema-N.bak` copy, their export files or their folders.

The store's purge is exercised directly on a real SQLite file, seeded with a row of each
person in every table that has a person column, so a table added later that the purge
misses fails here. The rest is a real Workbench against the fake Eugene, as a person's
browser and the owner's call it.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sqlite3
import threading
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from eugene_plexus_workbench import files
from eugene_plexus_workbench.app import create_app
from eugene_plexus_workbench.knowledge.index import IndexUnavailable
from eugene_plexus_workbench.store import Store

from . import saved
from .conftest import ADMIN_TOKEN, MODEL, Browser, ServerThread, World

#: What finds a person's row: the columns that name a person, or a chat, message, note or
#: artifact of theirs.
PERSON_COLUMNS = ("owner", "sub", "subject", "chat_id", "message_id", "note_id", "artifact_id")
#: Kept on purpose after a purge: the removal's own record (its id, name and dates) and the
#: export log (who exported whose data, and when).
RECORDS = {"removed_people", "exports"}
#: Words no model or index knows, with first letters no other word here starts with, so a
#: full-text index keeps each whole (an index stores a word after one it shares a start with
#: as only its tail).
MARK = {"a": "kqzvwobjanmark", "b": "xqzvwobjbenmark"}
SUB = {"a": "p-ann-0001", "b": "p-ben-0002"}
OWN_ID = {"chats": "chat", "messages": "msg", "memory_notes": "note", "artifacts": "art"}
DAY = 86_400.0


# --------------------------------------------------------------------------- #
# the store: every table that holds a person
# --------------------------------------------------------------------------- #


@contextlib.contextmanager
def _open(path: Path) -> Iterator[sqlite3.Connection]:
    """A connection that commits and closes, so no write-ahead log is left open beside a file."""
    db = sqlite3.connect(path)
    try:
        yield db
        db.commit()
    finally:
        db.close()


def _tables(db: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND sql NOT LIKE "
            "'CREATE VIRTUAL TABLE%' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%'"
        )
    ]


def _columns(db: sqlite3.Connection, table: str) -> list[tuple[str, str, int]]:
    """(name, declared type, primary key position) of each column."""
    return [(r[1], (r[2] or "").upper(), r[5]) for r in db.execute(f"PRAGMA table_info({table})")]


def _seed(db: sqlite3.Connection, who: str) -> None:
    """A row of this person's in every table with a person column, its text their mark."""
    sub = SUB[who]
    named = {
        "owner": sub,
        "sub": sub,
        "subject": sub,
        "chat_id": f"chat-{who}",
        "message_id": f"msg-{who}",
        "note_id": f"note-{who}",
        "artifact_id": f"art-{who}",
    }
    for table in _tables(db):
        columns = _columns(db, table)
        names = [c[0] for c in columns]
        if table in RECORDS or not (set(names) & set(PERSON_COLUMNS) or table in OWN_ID):
            continue
        values: dict[str, Any] = {}
        for name, kind, _key in columns:
            if name in named:
                values[name] = named[name]
            elif name == "id" and "INT" in kind:
                continue  # SQLite numbers it
            elif name == "id":
                values[name] = f"{OWN_ID.get(table, table)}-{who}"
            elif "INT" in kind:
                values[name] = 1 if who == "a" else 2
            elif "REAL" in kind:
                values[name] = 1.0
            elif table == "memory_measurements":
                # The install's measurement of a model, which stays: only whose it was goes.
                values[name] = f"{table}.{name}-{who}"
            else:
                values[name] = f"{MARK[who]} {table}.{name}"
        marks = ", ".join("?" for _ in values)
        db.execute(
            f"INSERT INTO {table} ({', '.join(values)}) VALUES ({marks})", tuple(values.values())
        )
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)", (f"runPreviews:{sub}", json.dumps(True))
    )


def _seeded(path: Path) -> None:
    async def make() -> None:
        store = Store(path)
        await store.open()
        await store.close()

    asyncio.run(make())
    with _open(path) as db:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("PRAGMA ignore_check_constraints = ON")
        _seed(db, "a")
        _seed(db, "b")


def _rows_of(db: sqlite3.Connection, who: str) -> dict[str, int]:
    """How many rows of this person's each table holds."""
    sub = SUB[who]
    wanted = {
        "owner": sub,
        "sub": sub,
        "subject": sub,
        "chat_id": f"chat-{who}",
        "message_id": f"msg-{who}",
        "note_id": f"note-{who}",
        "artifact_id": f"art-{who}",
    }
    found: dict[str, int] = {}
    for table in _tables(db):
        if table in RECORDS:
            continue
        names = [c[0] for c in _columns(db, table)]
        tests = [f"{c} = ?" for c in names if c in wanted]
        params = [wanted[c] for c in names if c in wanted]
        if table in OWN_ID:
            tests.append("id = ?")
            params.append(f"{OWN_ID[table]}-{who}")
        if tests:
            sql = f"SELECT COUNT(*) FROM {table} WHERE {' OR '.join(tests)}"
            found[table] = int(db.execute(sql, params).fetchone()[0])
    found["settings"] = int(
        db.execute(
            "SELECT COUNT(*) FROM settings WHERE key = ?", (f"runPreviews:{sub}",)
        ).fetchone()[0]
    )
    return found


def _holds(data: bytes, needle: bytes) -> bool:
    """Whether these bytes hold `needle`, looking inside a ZIP's members too: a download's
    contents are compressed, so its raw bytes never show a word it keeps."""
    if needle in data:
        return True
    if data[:4] != b"PK\x03\x04":
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return any(_holds(archive.read(name), needle) for name in archive.namelist())
    except zipfile.BadZipFile:
        return False


def _holding(directory: Path, mark: str) -> list[str]:
    """Every file under `directory` that holds `mark`, a ZIP's members included."""
    needle = mark.encode()
    return sorted(
        str(p.relative_to(directory))
        for p in directory.rglob("*")
        if p.is_file() and _holds(p.read_bytes(), needle)
    )


def test_the_scan_finds_a_word_inside_a_download(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "kept.zip", "w", compression=zipfile.ZIP_DEFLATED) as made:
        made.writestr("chats.json", json.dumps({"words": MARK["a"] * 20}))
    assert MARK["a"].encode() not in (tmp_path / "kept.zip").read_bytes()
    assert _holding(tmp_path, MARK["a"]) == ["kept.zip"]


def _purge(path: Path, sub: str) -> list[Path]:
    async def go() -> list[Path]:
        store = Store(path)
        await store.open()
        try:
            await store.purge_person(sub)
            return await store.purge_copies(sub)
        finally:
            await store.close()

    return asyncio.run(go())


def test_purge_leaves_nothing_of_the_person(tmp_path: Path) -> None:
    path = tmp_path / "workbench.sqlite3"
    _seeded(path)
    with _open(path) as db:
        before_a, before_b = _rows_of(db, "a"), _rows_of(db, "b")
    # Every table with a person column was seeded, so this is a test of every one.
    assert all(before_a.values()), [t for t, n in before_a.items() if not n]
    assert _holding(tmp_path, MARK["a"]), "the mark must be findable before the purge"

    _purge(path, SUB["a"])

    with _open(path) as db:
        after_a, after_b = _rows_of(db, "a"), _rows_of(db, "b")
        measured = db.execute("SELECT COUNT(*) FROM memory_measurements WHERE owner IS NULL")
        nulled = measured.fetchone()[0]
    assert {t: n for t, n in after_a.items() if n} == {}
    assert after_b == before_b
    assert nulled == 1  # the install's measurement stays; whose it was does not
    assert _holding(tmp_path, MARK["a"]) == []
    assert _holding(tmp_path, MARK["b"]) == ["workbench.sqlite3"]


def test_purge_reaches_every_copy_and_keeps_them_for_everyone_else(tmp_path: Path) -> None:
    path = tmp_path / "workbench.sqlite3"
    _seeded(path)
    copies = [
        tmp_path / "workbench.sqlite3.schema-26.bak",
        tmp_path / "workbench.sqlite3.schema-27.bak",
        tmp_path / "workbench.sqlite3.schema-27.1.bak",
    ]
    for copy in copies:
        with _open(path) as live, _open(copy) as kept:
            live.backup(kept)
    # A copy from an older schema lacks today's tables: its own tables are purged.
    with _open(copies[0]) as old:
        old.execute("DROP TABLE projects")
        old.execute("DROP TABLE memory_tidy")
    assert len(_holding(tmp_path, MARK["a"])) == 4

    purged = _purge(path, SUB["a"])

    assert sorted(purged) == sorted(copies)
    assert _holding(tmp_path, MARK["a"]) == []
    assert _holding(tmp_path, MARK["b"]) == sorted(["workbench.sqlite3", *(c.name for c in copies)])
    for copy in copies:
        with _open(copy) as db:
            # The seed ignored CHECK constraints; the file's own structure is what is checked.
            db.execute("PRAGMA ignore_check_constraints = ON")
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert db.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 1


class _Server:
    """A person's memory server that fails each forget `fails` times before it answers."""

    def __init__(self, fails: int) -> None:
        self.fails = fails
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, server: Any, mode: str, verb: str, wanted: dict[str, Any]) -> list[Any]:
        from eugene_plexus_workbench.memory_servers import ServerProblem

        self.calls.append((verb, wanted))
        if self.fails:
            self.fails -= 1
            raise ServerProblem("The memory server did not answer in time.")
        return []


@pytest.mark.parametrize(("fails", "outcome"), [(2, "done"), (3, "did not answer in time")])
def test_the_memory_server_forgets_what_workbench_sent_and_is_never_purged(
    tmp_path: Path, fails: int, outcome: str
) -> None:
    from eugene_plexus_workbench import config
    from eugene_plexus_workbench.removal import Removal

    async def go() -> tuple[str, list[tuple[str, dict[str, Any]]], list[dict[str, Any]]]:
        store = Store(tmp_path / "workbench.sqlite3")
        await store.open()
        try:
            await store.put_setting(config.MEMORY_SERVERS, "any")
            await store.put_memory_server(
                "p-ann", "http://example.org/mcp", "", "Hers", {"forget": {"tool": "x"}}
            )
            with _open(tmp_path / "workbench.sqlite3") as db:
                db.execute(
                    "INSERT INTO memory_server_sent (document_id, owner, chat_id, message_id, "
                    "state, next_at, created_at) VALUES ('c1:m1', 'p-ann', 'c1', 'm1', 'sent', 0, 0)"
                )
            server = _Server(fails)
            removal = Removal(store, None, tmp_path, servers=server)  # type: ignore[arg-type]
            done = await removal._step_memory("p-ann")
            return done, server.calls, await store.forgets_owed("p-ann")
        finally:
            await store.close()

    done, calls, owed = asyncio.run(go())
    assert outcome in done
    assert {verb for verb, _ in calls} == {"forget"}  # never purge: the server is theirs
    assert all(wanted == {"document_id": "c1:m1"} for _, wanted in calls)
    assert len(calls) == min(fails + 1, 3)
    assert (owed == []) == (done == "done")


#: A short MP3, as a gateway's speech answer starts (c3's).
SPOKEN = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + bytes(400)


class _SlowHub:
    """A gateway whose speech answer waits until it is let go."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def speech(self, body: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
        await self.release.wait()
        return SPOKEN, {}


def test_a_removed_persons_media_job_is_stopped_before_the_purge(tmp_path: Path) -> None:
    """A speech job running when its person is purged would make their folder and a file row
    again after the purge: stopping the person stops and drains it first."""
    from eugene_plexus_workbench.media import MediaJobs
    from eugene_plexus_workbench.removal import Removal
    from eugene_plexus_workbench.store import MediaRow

    async def go() -> tuple[list[Any], bool]:
        store = Store(tmp_path / "workbench.sqlite3")
        await store.open()
        hub = _SlowHub()
        jobs = MediaJobs(store, hub, tmp_path)  # type: ignore[arg-type]
        try:
            row = MediaRow(
                id="m-1",
                owner="p-ann",
                door="speech",
                kind="made",
                status="running",
                created_at=time.time(),
                request={"input": "hello"},
            )
            await jobs.start_speech(row, {"input": "hello", "model": "speaker"})
            removal = Removal(store, None, tmp_path)  # type: ignore[arg-type]
            removal.media = jobs
            await removal.stop("p-ann")
            hub.release.set()
            await asyncio.sleep(0.3)  # time for a job that was not stopped to keep its file
            return await store.files_for_media(["m-1"]), jobs.running("m-1")
        finally:
            hub.release.set()
            await jobs.aclose()
            await store.close()

    records, running = asyncio.run(go())
    assert records == [] and running is False
    assert not (tmp_path / "files" / files.person_key("p-ann")).exists()


def test_dropping_a_download_waits_for_its_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling the task that waits for a download's writer does not stop the writer's
    thread: `drop` waits for it, so no ZIP appears after the row says it was deleted."""
    from eugene_plexus_workbench import exports as exports_module
    from eugene_plexus_workbench.exports import OWNER, Exports
    from eugene_plexus_workbench.store import Person

    gate, inside = threading.Event(), threading.Event()
    readme = exports_module._readme

    def slow(*args: Any) -> str:
        inside.set()
        gate.wait(10)
        return readme(*args)

    monkeypatch.setattr(exports_module, "_readme", slow)

    async def go() -> tuple[dict[str, Any] | None, list[str]]:
        store = Store(tmp_path / "workbench.sqlite3")
        await store.open()
        try:
            made = Exports(store, tmp_path, SimpleNamespace(store=store))
            owner = Person(sub="operator", name="Owner", role="operator")
            row = await made.request(owner, "p-ann", "Ann", OWNER, full=False)
            assert await asyncio.to_thread(inside.wait, 10)
            dropping = asyncio.create_task(made.drop("p-ann"))
            await asyncio.sleep(0.2)
            gate.set()
            await dropping
            await asyncio.sleep(0.3)  # time for a writer nobody waited for
            left = sorted(p.name for p in (tmp_path / "exports").iterdir())
            return await store.export_row(row["id"]), left
        finally:
            gate.set()
            await store.close()

    row, left = asyncio.run(go())
    assert row is not None and row["state"] == "deleted"
    assert left == []


class _SlowServer:
    """A person's memory server whose `retain` waits until it is let go."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.inside = asyncio.Event()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(
        self, server: Any, mode: str, verb: str, wanted: dict[str, Any], seconds: float = 20.0
    ) -> list[Any]:
        self.calls.append((verb, dict(wanted)))
        if verb == "retain":
            self.inside.set()
            await self.release.wait()
        return []


def test_an_exchange_being_sent_when_its_person_is_removed_is_forgotten(tmp_path: Path) -> None:
    """Hiding deletes what waits to be sent, but not a send already in flight: the purge waits
    for it, and asks the server to forget it before its address and token go."""
    from eugene_plexus_workbench import config
    from eugene_plexus_workbench.memory_servers import Courier
    from eugene_plexus_workbench.removal import Removal
    from eugene_plexus_workbench.store import Chat, Message, Person

    async def go() -> list[tuple[str, dict[str, Any]]]:
        store = Store(tmp_path / "workbench.sqlite3")
        await store.open()
        server = _SlowServer()
        try:
            await store.put_setting(config.MEMORY_SERVERS, "any")
            await store.upsert_person(Person(sub="p-ann", name="Ann", role="member"))
            await store.put_memory_server(
                "p-ann",
                "http://example.org/mcp",
                "",
                "Hers",
                {"retain": {"tool": "retain"}, "forget": {"tool": "forget"}},
            )
            now = time.time()
            await store.create_chat(
                Chat(id="c1", owner="p-ann", title="t", model="m", created_at=now, updated_at=now)
            )
            await store.add_message(
                Message(
                    id="u1",
                    chat_id="c1",
                    seq=0,
                    role="user",
                    status="done",
                    created_at=now,
                    content="My favourite colour is green.",
                ),
                parent_id=None,
            )
            await store.add_message(
                Message(
                    id="a1",
                    chat_id="c1",
                    seq=0,
                    role="assistant",
                    status="done",
                    created_at=now,
                    content="Noted.",
                ),
                parent_id="u1",
            )
            assert await store.queue_memory_server_send("p-ann", "c1", "a1")
            courier = Courier(store, server)  # type: ignore[arg-type]
            sending = asyncio.create_task(courier.once())
            await asyncio.wait_for(server.inside.wait(), 10)
            removal = Removal(store, None, tmp_path, servers=server)  # type: ignore[arg-type]
            removal.courier = courier
            await removal.hide([{"sub": "p-ann", "name": "Ann", "known_at": now}])
            purging = asyncio.create_task(removal.purge("p-ann"))
            await asyncio.sleep(0.3)
            server.release.set()
            await sending
            await purging
            return server.calls
        finally:
            server.release.set()
            await store.close()

    calls = asyncio.run(go())
    assert ("forget", {"document_id": "c1:a1"}) in calls, calls


def test_a_download_cut_off_by_a_restart_says_so_and_can_be_made_again(
    world: World, ann: Browser
) -> None:
    now = time.time()
    _stop(world)
    with _open(_database(world)) as db:
        db.execute(
            "INSERT INTO exports (id, asker, asker_name, subject, subject_name, scope, state, "
            "created_at, expires_at) VALUES ('cutoff-export-01', 'p-ada', 'Ada', 'p-ada', 'Ada', "
            "'self', 'making', ?, ?)",
            (now, now + DAY),
        )
    (world.data / "exports").mkdir(exist_ok=True)
    (world.data / "exports" / "cutoff-export-01.part").write_bytes(b"PK half a file")
    _start(world)
    cut = next(
        e for e in ann.get("/api/exports").json()["exports"] if e["id"] == "cutoff-export-01"
    )
    assert cut["state"] == "failed", cut
    assert "Workbench stopped" in (cut["error"] or "")
    assert not (world.data / "exports" / "cutoff-export-01.part").exists()
    assert "chats.json" in _export(ann, "/api/me/export").namelist()


# --------------------------------------------------------------------------- #
# a real Workbench and the fake Eugene
# --------------------------------------------------------------------------- #


def _signed_in(world: World, sub: str) -> Browser:
    browser = world.browser()
    done = browser.sign_in(sub)
    assert "#signin=" in done.headers.get("location", ""), done.text
    return browser


def _owner(world: World) -> Browser:
    return _signed_in(world, "operator")


def _chat(person: Browser, words: str) -> str:
    chat = person.new_chat()
    sent = person.post(f"/api/chats/{chat}/messages", json={"content": words})
    assert sent.status_code == 201, sent.text
    person.wait_answer(chat)
    return chat


def _note(person: Browser, words: str) -> None:
    made = person.post("/api/memory/notes", json={"text": f"Remember {words}"})
    assert made.status_code == 201, made.text


def _document(person: Browser, words: str) -> None:
    saved.keep(person, ("plans.md", saved.md(f"The plan word is {words}.", title="Plans")))


def _check(owner: Browser) -> dict[str, Any]:
    checked = owner.post("/api/removed-people/check")
    assert checked.status_code == 200, checked.text
    return dict(checked.json())


def _removed(owner: Browser) -> dict[str, Any]:
    listed = owner.get("/api/removed-people")
    assert listed.status_code == 200, listed.text
    return dict(listed.json())


def _row(owner: Browser, sub: str) -> dict[str, Any] | None:
    return next((p for p in _removed(owner)["people"] if p["sub"] == sub), None)


def _until_purged(owner: Browser, sub: str, seconds: float = 60) -> dict[str, Any]:
    deadline = time.perf_counter() + seconds
    row: dict[str, Any] | None = None
    while time.perf_counter() < deadline:
        row = _row(owner, sub)
        if row is not None and row["state"] == "purged":
            return row
        time.sleep(0.1)
    raise AssertionError(f"the purge did not finish: {row}")


def _settings(world: World, **values: Any) -> None:
    changed = httpx.patch(
        f"{world.workbench}/v1/config",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json=values,
        trust_env=False,
    )
    assert changed.status_code == 200 and not changed.json()["rejected"], changed.text


def _database(world: World) -> Path:
    return world.data / "workbench.sqlite3"


def _stop(world: World) -> None:
    world.server.stop()


def _start(world: World) -> None:
    world.server = ServerThread(create_app(world.settings), world.server.port).start()


def _state_of(world: World) -> Any:
    return world.server.server.config.app.state


@pytest.fixture
def ann(world: World) -> Iterator[Browser]:
    """Ada, signed in, with a chat, a note and a document holding her mark."""
    person = _signed_in(world, "p-ada")
    _chat(person, f"My code is {MARK['a']}")
    _note(person, MARK["a"])
    _document(person, MARK["a"])
    yield person


def test_delete_now_leaves_nothing_in_the_store_or_its_copies(world: World, ann: Browser) -> None:
    bo = _signed_in(world, "p-bo")
    bo_chat = _chat(bo, f"Bo's code is {MARK['b']}")
    # A copy the last migration kept, holding Ada's chats as they were.
    _stop(world)
    database = _database(world)
    with (
        _open(database) as live,
        _open(database.with_name("workbench.sqlite3.schema-27.bak")) as kept,
    ):
        live.backup(kept)
    _start(world)
    assert len(_holding(world.data, MARK["a"])) >= 3  # the store, the copy, her index

    del world.eugene.people["p-ada"]
    owner = _owner(world)
    _check(owner)
    started = owner.post("/api/removed-people/p-ada/delete")
    assert started.status_code == 202, started.text
    row = _until_purged(owner, "p-ada")

    assert row["name"] == "Ada" and row["purgedAt"] is not None
    assert _holding(world.data, MARK["a"]) == []
    assert not (world.data / "knowledge" / files.person_key("p-ada")).exists()
    assert not (world.data / "files" / files.person_key("p-ada")).exists()
    # The copy is kept for everyone else.
    assert database.with_name("workbench.sqlite3.schema-27.bak").is_file()
    assert MARK["b"].encode() in database.with_name("workbench.sqlite3.schema-27.bak").read_bytes()
    assert bo.get(f"/api/chats/{bo_chat}").status_code == 200


def test_unknown_after_known_hides(world: World, ann: Browser) -> None:
    chat = ann.get("/api/chats").json()["chats"][0]["id"]
    owner = _owner(world)
    _settings(world, ownerReadsChats=True)
    assert owner.get(f"/api/chats/{chat}").status_code == 200

    del world.eugene.people["p-ada"]
    checked = _check(owner)
    row = next(p for p in checked["people"] if p["sub"] == "p-ada")
    assert row["state"] == "hidden" and row["name"] == "Ada"
    assert row["deleteAt"] == pytest.approx(row["removedAt"] + 30 * DAY)

    # Every read path: her own sign-in, the owner's read view, her files and documents.
    assert ann.get("/api/me").status_code == 401
    assert owner.get(f"/api/chats/{chat}").status_code == 404
    assert "p-ada" not in [p["sub"] for p in owner.get("/api/people").json()["people"]]
    assert owner.get("/api/people/p-ada/chats").json()["chats"] == []
    # It is hidden, not deleted: her data is still on the disk.
    assert (world.data / "knowledge" / files.person_key("p-ada")).is_dir()
    with _open(_database(world)) as db:
        assert db.execute("SELECT COUNT(*) FROM chats WHERE owner = 'p-ada'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM sessions WHERE sub = 'p-ada'").fetchone()[0] == 0


def _days_ago(world: World, sub: str, days: float) -> None:
    """Move a removal back in time, as if it happened `days` ago."""
    with _open(_database(world)) as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(removed_people)")}
        dated = [c for c in ("found_at", "clock_from", "delete_at") if c in columns]
        db.execute(
            f"UPDATE removed_people SET {', '.join(f'{c} = {c} - ?' for c in dated)} WHERE sub = ?",
            (*([days * DAY] * len(dated)), sub),
        )


def test_a_lower_setting_never_shortens_a_wait_already_begun(world: World, ann: Browser) -> None:
    """Troy: changing the setting never shortens the wait of people already removed. Ada was
    removed 10 days ago under 30 days; the owner lowers it to 7; she is not due, and her date
    stands. Bo, removed after the change, gets 7."""
    bo = _signed_in(world, "p-bo")
    owner = _owner(world)
    del world.eugene.people["p-ada"]
    row = next(p for p in _check(owner)["people"] if p["sub"] == "p-ada")
    _days_ago(world, "p-ada", 10)
    _settings(world, removedPeopleDays=7)
    _check(owner)  # asks Eugene and starts whatever is due
    later = _row(owner, "p-ada")
    assert later is not None and later["state"] == "hidden", later
    assert later["deleteAt"] == pytest.approx(row["deleteAt"] - 10 * DAY)
    assert later["deleteAt"] == pytest.approx(later["removedAt"] + 30 * DAY)
    assert (world.data / "knowledge" / files.person_key("p-ada")).is_dir()
    del world.eugene.people["p-bo"]
    bo_row = next(p for p in _check(owner)["people"] if p["sub"] == "p-bo")
    assert bo_row["deleteAt"] == pytest.approx(bo_row["removedAt"] + 7 * DAY)
    assert _removed(owner)["days"] == 7
    assert bo.get("/api/me").status_code == 401
    for wrong in (0, 366, True, "30"):
        refused = httpx.patch(
            f"{world.workbench}/v1/config",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"removedPeopleDays": wrong},
            trust_env=False,
        )
        assert refused.json()["rejected"], wrong


def test_after_the_wait_the_same_purge_runs(world: World, ann: Browser) -> None:
    owner = _owner(world)
    del world.eugene.people["p-ada"]
    _check(owner)
    # Not yet due: nothing is deleted.
    _check(owner)
    assert _row(owner, "p-ada")["state"] == "hidden"  # type: ignore[index]
    _days_ago(world, "p-ada", 31)
    _check(owner)
    _until_purged(owner, "p-ada")
    assert _holding(world.data, MARK["a"]) == []


def test_never_confirmed_is_held(world: World, ann: Browser) -> None:
    # A person who signed in before this update: Eugene never confirmed them.
    with _open(_database(world)) as db:
        db.execute("UPDATE people SET known_at = NULL WHERE sub = 'p-ada'")
    owner = _owner(world)
    del world.eugene.people["p-ada"]
    row = next(p for p in _check(owner)["people"] if p["sub"] == "p-ada")
    assert row["state"] == "held" and row["deleteAt"] is None
    assert ann.get("/api/me").status_code == 401
    # Nothing is deleted on a guess: the hourly job leaves it until the owner chooses.
    with _open(_database(world)) as db:
        db.execute("UPDATE removed_people SET found_at = found_at - ? WHERE sub = 'p-ada'", (DAY,))
    _check(owner)
    assert _row(owner, "p-ada")["state"] == "held"  # type: ignore[index]
    assert (world.data / "knowledge" / files.person_key("p-ada")).is_dir()


def test_three_at_once_brakes(world: World) -> None:
    subs = ["p-cy", "p-di", "p-ed"]
    for sub in subs:
        world.eugene.people[sub] = {"name": sub[2:].title(), "role": "member", "username": sub}
    people = {sub: _signed_in(world, sub) for sub in subs}
    for sub in subs:
        del world.eugene.people[sub]
    owner = _owner(world)
    checked = _check(owner)
    assert checked["people"] == []
    assert checked["brake"]["count"] == 3
    assert all(p.get("/api/me").status_code == 200 for p in people.values())

    confirmed = owner.post("/api/removed-people/brake", json={"removed": True})
    assert confirmed.status_code == 200, confirmed.text
    states = {p["sub"]: p["state"] for p in _removed(owner)["people"]}
    assert states == dict.fromkeys(subs, "hidden")
    assert _removed(owner)["brake"] is None
    assert all(p.get("/api/me").status_code == 401 for p in people.values())


def test_purge_resumes_after_restart(world: World, ann: Browser) -> None:
    owner = _owner(world)
    del world.eugene.people["p-ada"]
    _check(owner)
    # A purge a stop cut off after its first step: committed as begun, nothing more done.
    _stop(world)
    with _open(_database(world)) as db:
        db.execute(
            "UPDATE removed_people SET state = 'purging', steps = ? WHERE sub = 'p-ada'",
            (json.dumps({"memory": "done"}),),
        )
    _start(world)
    owner = _owner(world)
    _until_purged(owner, "p-ada")
    assert _holding(world.data, MARK["a"]) == []


def test_hidden_person_index_not_recreated(world: World, ann: Browser) -> None:
    owner = _owner(world)
    del world.eugene.people["p-ada"]
    _check(owner)
    indexes = _state_of(world).saved.indexes
    with pytest.raises(IndexUnavailable):
        indexes.get("p-ada", create=True)
    assert indexes.get("p-ada", create=False) is None
    owner.post("/api/removed-people/p-ada/delete")
    _until_purged(owner, "p-ada")
    with pytest.raises(IndexUnavailable):
        indexes.get("p-ada", create=True)
    assert not (world.data / "knowledge" / files.person_key("p-ada")).exists()


def test_keep_stops_the_clock_and_known_again_comes_back(world: World, ann: Browser) -> None:
    owner = _owner(world)
    del world.eugene.people["p-ada"]
    _check(owner)
    kept = owner.post("/api/removed-people/p-ada/keep")
    assert kept.status_code == 200 and kept.json()["state"] == "kept", kept.text
    assert kept.json()["deleteAt"] is None
    again = owner.post("/api/removed-people/p-ada/schedule")
    assert again.json()["state"] == "hidden" and again.json()["deleteAt"] is not None
    # Eugene has her again (a restored root): she is no longer removed.
    world.eugene.people["p-ada"] = {"name": "Ada", "role": "member", "username": "ada"}
    _check(owner)
    assert _row(owner, "p-ada") is None
    assert _signed_in(world, "p-ada").get("/api/chats").json()["chats"] != []


def test_a_recreated_person_is_a_new_person(world: World, ann: Browser) -> None:
    owner = _owner(world)
    del world.eugene.people["p-ada"]
    _check(owner)
    owner.post("/api/removed-people/p-ada/delete")
    _until_purged(owner, "p-ada")
    # Made again with the same name: Eugene gives her a new id.
    world.eugene.people["p-ada-2"] = {"name": "Ada", "role": "member", "username": "ada"}
    again = _signed_in(world, "p-ada-2")
    assert again.get("/api/chats").json()["chats"] == []
    assert again.get("/api/memory/notes").json()["notes"] == []


def _config(world: World) -> dict[str, Any]:
    got = httpx.get(
        f"{world.workbench}/v1/config",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        trust_env=False,
    )
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_retention_for_days_is_the_default(world: World, ann: Browser) -> None:
    """Kept for a number of days (30): hidden, with a delete date, and still on the disk."""
    assert _config(world)["removedPeopleRetention"] == "days"
    owner = _owner(world)
    del world.eugene.people["p-ada"]
    checked = _check(owner)
    assert checked["retention"] == "days"
    row = next(p for p in checked["people"] if p["sub"] == "p-ada")
    assert row["state"] == "hidden" and row["deleteAt"] is not None
    assert _holding(world.data, MARK["a"]) != []


def test_retention_not_at_all_deletes_at_once(world: World, ann: Browser) -> None:
    """Not at all: a removed person's data goes the moment Workbench finds out, by the same
    true delete as Delete now. People removed before the change keep their days."""
    bo = _signed_in(world, "p-bo")
    _chat(bo, f"Bo's code is {MARK['b']}")
    owner = _owner(world)
    del world.eugene.people["p-bo"]
    _check(owner)
    _settings(world, removedPeopleRetention="none")
    assert _config(world)["removedPeopleRetention"] == "none"
    del world.eugene.people["p-ada"]
    checked = _check(owner)
    assert checked["retention"] == "none"
    _until_purged(owner, "p-ada")
    assert _holding(world.data, MARK["a"]) == []
    # Bo was removed while the data was kept for 30 days: his days stand.
    bo_row = _row(owner, "p-bo")
    assert bo_row is not None and bo_row["state"] == "hidden" and bo_row["deleteAt"] is not None
    assert _holding(world.data, MARK["b"]) != []
    refused = httpx.patch(
        f"{world.workbench}/v1/config",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={"removedPeopleRetention": "forever"},
        trust_env=False,
    )
    assert refused.json()["rejected"]


def test_a_removed_persons_email_is_kept_with_their_hidden_data(world: World) -> None:
    """Row 7b links a removed person's data to a new account with the same verified email, so
    the email stays with the hidden data, and goes when the data does."""
    world.eugene.people["p-ada"]["email"] = "ada@example.com"
    _signed_in(world, "p-ada")
    owner = _owner(world)
    del world.eugene.people["p-ada"]
    _check(owner)
    with _open(_database(world)) as db:
        kept = db.execute("SELECT email FROM removed_people WHERE sub = 'p-ada'").fetchone()
    assert kept == ("ada@example.com",)
    # A copy kept while she was hidden (a later migration's) holds it too.
    database = _database(world)
    with (
        _open(database) as live,
        _open(database.with_name(f"{database.name}.schema-28.bak")) as copy,
    ):
        live.backup(copy)
    assert _holding(world.data, "ada@example.com")
    owner.post("/api/removed-people/p-ada/delete")
    _until_purged(owner, "p-ada")
    with _open(_database(world)) as db:
        gone = db.execute("SELECT email FROM removed_people WHERE sub = 'p-ada'").fetchone()
    assert gone == (None,)
    # Its bytes too: no free page, log or copy keeps the address.
    assert _holding(world.data, "ada@example.com") == []


def test_only_the_owner_manages_removed_people(world: World, ann: Browser) -> None:
    assert ann.get("/api/removed-people").status_code == 403
    assert ann.post("/api/removed-people/check").status_code == 403
    assert ann.post("/api/removed-people/p-bo/delete").status_code == 403


# --------------------------------------------------------------------------- #
# downloading data
# --------------------------------------------------------------------------- #


def _export(person: Browser, path: str) -> zipfile.ZipFile:
    made = person.post(path)
    assert made.status_code == 202, made.text
    export = made.json()["export"]
    deadline = time.perf_counter() + 60
    while export["state"] == "making" and time.perf_counter() < deadline:
        time.sleep(0.1)
        export = next(
            e for e in person.get("/api/exports").json()["exports"] if e["id"] == export["id"]
        )
    assert export["state"] == "ready", export
    got = person.get(f"/api/exports/{export['id']}")
    assert got.status_code == 200, got.text
    return zipfile.ZipFile(io.BytesIO(got.content))


def _all_text(archive: zipfile.ZipFile) -> str:
    return "\n".join(archive.read(n).decode("utf-8", "replace") for n in archive.namelist())


def test_everyone_downloads_their_own_data(world: World, ann: Browser) -> None:
    archive = _export(ann, "/api/me/export")
    names = archive.namelist()
    assert {"README.txt", "chats.json", "memory.json", "memory.md", "projects.json"} <= set(names)
    assert "connections.json" in names
    assert any(n.startswith("chats/") and n.endswith(".md") for n in names)
    assert any(n.startswith("documents/") for n in names)
    assert _all_text(archive).count(MARK["a"]) >= 3
    # Only the one who asked downloads it.
    export = ann.get("/api/exports").json()["exports"][0]
    bo = _signed_in(world, "p-bo")
    assert bo.get(f"/api/exports/{export['id']}").status_code == 404


def test_the_owners_export_of_a_person_follows_owner_reads_chats(
    world: World, ann: Browser
) -> None:
    owner = _owner(world)
    refused = owner.post("/api/people/p-ada/export")
    assert refused.status_code == 403, refused.text
    _settings(world, ownerReadsChats=True)
    archive = _export(owner, "/api/people/p-ada/export")
    names = archive.namelist()
    assert "chats.json" in names and MARK["a"] in archive.read("chats.json").decode()
    # Notes and documents are never another person's to read (memory design 4.9).
    assert "memory.json" not in names and not any(n.startswith("documents/") for n in names)


def test_the_owner_always_exports_a_removed_person_and_it_is_logged(
    world: World, ann: Browser
) -> None:
    owner = _owner(world)
    _export(ann, "/api/me/export")  # her own, before she was removed
    del world.eugene.people["p-ada"]
    _check(owner)
    archive = _export(owner, "/api/people/p-ada/export")
    assert MARK["a"] in _all_text(archive)
    log = _removed(owner)["exportLog"]
    assert log[0]["subjectName"] == "Ada" and log[0]["askerName"] == "Owner"
    assert log[0]["scope"] == "owner" and log[0]["createdAt"] > 0
    made = sorted(p.name for p in (world.data / "exports").iterdir())
    assert len(made) == 2 and all(n.endswith(".zip") for n in made), made
    # Both downloads go with her when her data is deleted, files and all; the log stays.
    owner.post("/api/removed-people/p-ada/delete")
    _until_purged(owner, "p-ada")
    assert list((world.data / "exports").iterdir()) == []
    assert _holding(world.data, MARK["a"]) == []
    assert _removed(owner)["exportLog"][0]["subjectName"] == "Ada"
    assert owner.get("/api/exports").json()["exports"][0]["state"] == "deleted"


def test_eugene_unreachable_hides_nobody_and_says_why(world: World, ann: Browser) -> None:
    owner = _owner(world)
    first = _check(owner)
    assert first["problem"] is None and first["checkedAt"] is not None
    asked = world.eugene.subject_calls[-1]
    assert "p-ada" in asked and "operator" not in asked  # the owner is never in question
    del world.eugene.people["p-ada"]
    world.eugene.up = False
    try:
        down = _check(owner)
    finally:
        world.eugene.up = True
    assert down["people"] == []
    assert down["problem"].startswith("Workbench could not ask Eugene who was removed: ")
    assert down["checkedAt"] == first["checkedAt"]  # when Eugene last answered
    assert ann.get("/api/chats").status_code == 200
    assert MODEL
