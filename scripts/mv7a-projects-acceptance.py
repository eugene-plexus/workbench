"""Chat projects (memory v2's MV7a), live.

The unit checks drive Workbench in process. What only a live run shows is the whole path: a
store migrated from schema 26 by a real Workbench process, a real gateway and driver, and what
each request carried. A deterministic model answers every chat.

  0. isolated: every ambient EUGENE_PLEXUS_* variable dropped, owned ports free
  1. the stack: a model, a driver, a gateway, Workbench over a schema 26 store holding Ada's
     pinned general note
 1b. the store came through: schema 27, the projects table, Ada's note general
  2. Ada makes a project and keeps a pinned note to it
  3. a chat in no project is given her general note and not the project's
  4. a chat in the project is given both; with Also use my general memory off, only its own
  5. "Remember that ..." in the project's chat keeps the note to the project
  6. a chat moved into the project is given its note at once
  7. Bo sees nothing of Ada's project and cannot make a chat in it
  8. deleting a project and keeping what was in it: its chats and notes become general;
     deleting another with everything: its chats and notes are gone
  9. teardown by pid; no owned port still listening

**Safe beside a live install**: ambient EUGENE_PLEXUS_* variables dropped, ports 8540-8551,
teardown by pid.

Run with the Workbench interpreter:
  workbench/.venv/Scripts/python.exe workbench/scripts/mv7a-projects-acceptance.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import httpx
import uvicorn

HERE = Path(__file__).resolve().parent


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mv3 = _load("mv3", "mv3-learning-acceptance.py")
m = mv3.m

(
    GW_PORT,
    AGENT_PORT,
    DRV_LOCAL,
    SPARE_0,
    WB_PORT,
    EUGENE_PORT,
    GW_PROXY,
    STUB_PORT,
    SPARE_1,
    SPARE_2,
    SPARE_3,
    SPARE_4,
) = range(8540, 8552)
OWNED = list(range(8540, 8552))
WORKBENCH = f"http://127.0.0.1:{WB_PORT}"
LOCAL = "mv7a-local"
m.EUGENE_PORT, m.WORKBENCH, m.OUTSIDE = EUGENE_PORT, WORKBENCH, "mv7a-outside"
say, check, note, listening, Person = m.say, m.check, m.note, m.listening, m.Person
LOCK, WB_SENT = m.LOCK, m.WB_SENT
until = mv3.until
CODE = "RAVEN-7731"
ANSWER = "The code word is in the survey [1]."


class Stub(BaseHTTPRequestHandler):
    """A model that answers every chat citing passage 1, and every background pass with none."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    def _json(self, body: Any, status: int = 200) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path.split("?")[0] == "/v1/models":
            return self._json({"data": [{"id": LOCAL, "object": "model"}]})
        return self._json({"error": {"message": "not found"}}, status=404)

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not body.get("stream"):
            content = json.dumps({"notes": []}) if body.get("response_format") else ANSWER
            return self._json(
                {
                    "choices": [
                        {"index": 0, "message": {"content": content}, "finish_reason": "stop"}
                    ]
                }
            )
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for frame in (
            {"choices": [{"index": 0, "delta": {"content": ANSWER}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ):
            self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


class Agent(BaseHTTPRequestHandler):
    """Topology only: one driver."""

    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        body: Any = {"status": "ok"}
        if path == "/v1/components":
            body = {
                "components": [
                    {
                        "name": LOCAL,
                        "kind": "inference-driver",
                        "url": f"http://127.0.0.1:{DRV_LOCAL}/",
                        "status": "running",
                    }
                ]
            }
        elif path == "/v1/runtimes":
            body = {"runtimes": []}
        elif path == "/v1/node":
            body = {"name": "mv7a-node", "enrolled": False}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def ask(person: Any, chat: str, content: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Send a message; the answer, and the body Workbench sent the gateway for it."""
    with LOCK:
        before = len(WB_SENT)
    sent = person.post(f"/api/chats/{chat}/messages", json={"content": content})
    assert sent.status_code == 201, sent.text
    answer = person.wait_answer(chat, 120)
    with LOCK:
        bodies = [mv3.body_of(b) for b in WB_SENT[before:] if mv3.body_of(b).get("stream")]
    return answer, (bodies[-1] if bodies else {})


def md(*paragraphs: str) -> bytes:
    return ("# Survey\n\n" + "\n\n".join(paragraphs) + "\n").encode()


def add(person: Any, name: str, data: bytes) -> Any:
    return person.post(
        "/api/documents", files=[("files", (name, data, "application/octet-stream"))]
    )


def settled(person: Any) -> list[dict[str, Any]]:
    def done() -> list[dict[str, Any]] | None:
        docs = person.get("/api/documents").json()["documents"]
        return docs if all(d["state"] in ("ready", "skipped", "failed") for d in docs) else None

    found = until(done, 120, 0.5)
    assert found is not None, "the documents were not read in time"
    return list(found)


def doc_named(person: Any, name: str) -> dict[str, Any]:
    return next(d for d in settled(person) if d["name"] == name)


def page(person: Any) -> dict[str, Any]:
    got = person.get("/api/memory")
    assert got.status_code == 200, got.text
    return dict(got.json())


def asked(person: Any) -> dict[str, list[dict[str, Any]]]:
    return {r["id"]: r["reasons"] for r in page(person)["revisit"]}


def documents_of(person: Any, note_id: str) -> list[dict[str, Any]]:
    return list(person.get(f"/api/memory/notes/{note_id}").json()["documents"])


def typed(person: Any, text: str, **more: Any) -> str:
    made = person.post("/api/memory/notes", json={"text": text, **more})
    assert made.status_code == 201, made.text
    return str(made.json()["note"]["id"])


def seed_schema_26(data: Path) -> None:
    """A store as schema 26 wrote it: Ada keeps one pinned note. Made by today's store, then
    put back to schema 26's shape (MV7's table, columns and indexes removed, its version said)."""
    sys.path.insert(0, str(m.WB / "src"))
    from eugene_plexus_workbench.store import MemoryNote, Store

    async def go() -> None:
        store = Store(data / "workbench.sqlite3")
        await store.open()
        try:
            await store.add_memory_note(
                MemoryNote(
                    id="ada-metric",
                    owner="p-ada",
                    text="Prefers metric units.",
                    kind="preference",
                    pinned=True,
                    created_at=time.time() - 86400,
                    updated_at=time.time() - 86400,
                ),
                limit=1000,
            )
        finally:
            await store.close()

    asyncio.run(go())
    with sqlite3.connect(data / "workbench.sqlite3") as db:
        db.execute("DROP INDEX chats_by_project")
        db.execute("DROP INDEX memory_notes_by_project")
        db.execute("DROP TABLE projects")
        db.execute("ALTER TABLE chats DROP COLUMN project_id")
        db.execute("ALTER TABLE memory_notes DROP COLUMN project_id")
        db.execute("UPDATE meta SET value = '26' WHERE key = 'schema'")


def system_of(body: dict[str, Any]) -> str:
    found = [x["content"] for x in body.get("messages") or [] if x.get("role") == "system"]
    return str(found[0]) if found else ""


def chat_in(person: Any, project_id: str | None) -> str:
    made = person.post("/api/chats", json={"model": LOCAL, "projectId": project_id})
    assert made.status_code in (200, 201), made.text
    return str(made.json()["id"])


def run(data: Path, ada: Any, bo: Any) -> None:
    say("1b. the store came through")
    with sqlite3.connect(data / "workbench.sqlite3") as db:
        version = db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0]
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        held = db.execute("SELECT project_id FROM memory_notes WHERE id = 'ada-metric'").fetchone()
    check("schema 27", version == "27", version)
    check("the projects table is there", "projects" in tables, sorted(tables))
    check("Ada's note is general", held == (None,), held)
    check(
        "a backup of the schema 26 store was kept",
        bool(list(data.glob("*.schema-26*"))),
        sorted(p.name for p in data.iterdir()),
    )

    say("2. Ada makes a project")
    made = ada.post("/api/projects", json={"name": "Garden"})
    check("the project is made", made.status_code == 201, made.text)
    garden = made.json()["id"]
    note = ada.post(
        "/api/memory/notes",
        json={"text": "The shed key is under the mat.", "pinned": True, "projectId": garden},
    )
    check(
        "a note is kept to it",
        note.status_code == 201 and note.json()["note"]["projectId"] == garden,
        note.text,
    )

    say("3. a chat in no project")
    outside = chat_in(ada, None)
    _, sent = ask(ada, outside, "Hello.")
    check(
        "is given the general note and not the project's",
        "metric" in system_of(sent) and "shed key" not in system_of(sent),
        system_of(sent)[:400],
    )

    say("4. a chat in the project")
    inside = chat_in(ada, garden)
    _, sent = ask(ada, inside, "Hello.")
    check(
        "is given both",
        "metric" in system_of(sent) and "shed key" in system_of(sent),
        system_of(sent)[:400],
    )
    off = ada.patch(f"/api/projects/{garden}", json={"generalMemory": False})
    check("Also use my general memory turns off", off.status_code == 200, off.text)
    _, sent = ask(ada, inside, "Hello again.")
    check(
        "with it off, only its own",
        "shed key" in system_of(sent) and "metric" not in system_of(sent),
        system_of(sent)[:400],
    )

    say("5. a note said in the project's chat")
    ask(ada, inside, "Remember that the tomatoes need staking")
    found = ada.get("/api/memory/notes", params={"where": garden}).json()["notes"]
    check(
        "is kept to the project",
        any("tomatoes" in n["text"] for n in found),
        [n["text"] for n in found],
    )
    general = ada.get("/api/memory/notes", params={"where": "general"}).json()["notes"]
    check("and not to General", not any("tomatoes" in n["text"] for n in general))

    say("6. a chat moved into the project")
    moved = ada.patch(f"/api/chats/{outside}", json={"projectId": garden})
    check("moves", moved.status_code == 200 and moved.json()["projectId"] == garden, moved.text)
    _, sent = ask(ada, outside, "And now?")
    check("is given the project's note at once", "shed key" in system_of(sent))

    say("7. Bo")
    check("sees none of Ada's projects", bo.get("/api/projects").json()["projects"] == [])
    refused = bo.post("/api/chats", json={"model": LOCAL, "projectId": garden})
    check("cannot make a chat in it", refused.status_code == 404, refused.status_code)
    check("cannot read it", bo.get(f"/api/projects/{garden}").status_code == 404)

    say("8. deleting projects")
    released = ada.delete(f"/api/projects/{garden}", params={"keep": "true"})
    check(
        "keeping what was in it",
        released.status_code == 200 and released.json()["kept"] is True,
        released.text,
    )
    with sqlite3.connect(data / "workbench.sqlite3") as db:
        left = db.execute(
            "SELECT (SELECT COUNT(*) FROM chats WHERE project_id IS NOT NULL), "
            "(SELECT COUNT(*) FROM memory_notes WHERE project_id IS NOT NULL)"
        ).fetchone()
    check("its chats and notes are general", left == (0, 0), left)
    second = ada.post("/api/projects", json={"name": "Gone"}).json()["id"]
    gone_chat = chat_in(ada, second)
    ask(ada, gone_chat, "Remember that this goes with the project")
    taken = ada.delete(f"/api/projects/{second}")
    check(
        "deleting with everything takes its chat and its note",
        taken.status_code == 200 and taken.json()["chats"] == 1 and taken.json()["notes"] == 1,
        taken.text,
    )
    check("the chat is gone", ada.get(f"/api/chats/{gone_chat}").status_code == 404)
    check("no project is left", ada.get("/api/projects").json()["projects"] == [])


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    procs: dict[str, subprocess.Popen[bytes]] = {}
    work = Path(tempfile.mkdtemp(prefix="ep-mv7a-"))
    logs = work / "logs"
    logs.mkdir()

    def start(name: str, argv: list[str], env: dict[str, str]) -> None:
        handle = open(logs / f"{name}.log", "a", encoding="utf-8")  # noqa: SIM115
        procs[name] = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, env=env
        )

    def kill(name: str) -> None:
        proc = procs.pop(name, None)
        if proc is not None:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False
            )

    server: Any = None
    try:
        say("0. isolate this run")
        base = {k: v for k, v in os.environ.items() if not k.startswith("EUGENE_PLEXUS_")}
        for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            base.pop(proxy, None)
            base.pop(proxy.lower(), None)
        check("no ambient EUGENE_PLEXUS_* variable reaches a child", True)
        taken = [p for p in OWNED if listening(p)]
        if taken:
            check(f"ports {taken} are free", False)
            return 1
        missing = [p for p in (m.WB_PY, m.GW_PY, m.DRV_PY) if not Path(p).exists()]
        if not check("every interpreter is here", not missing, missing):
            return 1

        say("1. the stack, over a schema 26 store")
        threading.Thread(target=m.serve, args=(Agent, AGENT_PORT), daemon=True).start()
        threading.Thread(target=m.serve, args=(Stub, STUB_PORT), daemon=True).start()
        threading.Thread(
            target=m.serve, args=(m.make_proxy(GW_PORT, WB_SENT), GW_PROXY), daemon=True
        ).start()
        config_ = uvicorn.Config(
            m.eugene_app(), host="127.0.0.1", port=EUGENE_PORT, log_level="error"
        )
        server = uvicorn.Server(config_)
        threading.Thread(target=server.run, daemon=True).start()
        (work / "driver.yaml").write_text(
            f"provider: openai_compat_custom\nbaseUrl: http://127.0.0.1:{STUB_PORT}\n"
            f"modelId: {LOCAL}\nbackendLocality: local\nrequestTimeoutSeconds: 120\n",
            encoding="utf-8",
            newline="\n",
        )
        start(
            "driver",
            [str(m.DRV_PY), "-m", "eugene_plexus_inference_driver"],
            {
                **base,
                "EUGENE_PLEXUS_DRIVER_CONFIG_FILE": str(work / "driver.yaml"),
                "EUGENE_PLEXUS_DRIVER_BIND_HOST": "127.0.0.1",
                "EUGENE_PLEXUS_DRIVER_BIND_PORT": str(DRV_LOCAL),
            },
        )
        (work / "gateway.yaml").write_text("routingRefreshSeconds: 3\n", encoding="utf-8")
        start(
            "gateway",
            [str(m.GW_PY), "-m", "eugene_plexus_gateway"],
            {
                **base,
                "EUGENE_PLEXUS_GATEWAY_CONFIG_FILE": str(work / "gateway.yaml"),
                "EUGENE_PLEXUS_GATEWAY_METRICS_FILE": str(work / "metrics.sqlite3"),
                "EUGENE_PLEXUS_GATEWAY_BIND_HOST": "127.0.0.1",
                "EUGENE_PLEXUS_GATEWAY_BIND_PORT": str(GW_PORT),
                "EUGENE_PLEXUS_GATEWAY_AGENT_URL": f"http://127.0.0.1:{AGENT_PORT}",
            },
        )

        def listed() -> set[str]:
            try:
                got = httpx.get(f"http://127.0.0.1:{GW_PORT}/v1/models", timeout=5, trust_env=False)
                return {
                    x["id"]
                    for x in got.json().get("data") or []
                    if (x.get("x_eugene_plexus") or {}).get("ready_backends", 1)
                }
            except (httpx.HTTPError, ValueError):
                return set()

        if not check(
            f"the gateway routes {LOCAL}",
            bool(until(lambda: LOCAL in listed(), 180, 1.0)),
            (sorted(listed()), logs),
        ):
            return 1

        data = work / "workbench-data"
        data.mkdir()
        seed_schema_26(data)
        static = work / "static"
        static.mkdir()
        (static / "index.html").write_text(
            "<!doctype html><title>Workbench</title>", encoding="utf-8"
        )
        (work / "app_key").write_text("mv7a-app-key", encoding="utf-8")
        (work / "oidc_secret").write_text(m.CLIENT_SECRET, encoding="utf-8")
        wb_env = {
            **base,
            "PYTHONPATH": str(m.WB / "src"),
            "EUGENE_PLEXUS_APP_BIND_HOST": "127.0.0.1",
            "EUGENE_PLEXUS_APP_BIND_PORT": str(WB_PORT),
            "EUGENE_PLEXUS_APP_DATA_DIR": str(data),
            "EUGENE_PLEXUS_APP_KEY_FILE": str(work / "app_key"),
            "EUGENE_PLEXUS_APP_ADMIN_TOKEN": m.ADMIN_TOKEN,
            "EUGENE_PLEXUS_APP_GATEWAY_URL": f"http://127.0.0.1:{GW_PROXY}",
            "EUGENE_PLEXUS_APP_OIDC_ISSUER": f"http://127.0.0.1:{EUGENE_PORT}/oidc",
            "EUGENE_PLEXUS_APP_OIDC_CLIENT_ID": m.CLIENT_ID,
            "EUGENE_PLEXUS_APP_OIDC_SECRET_FILE": str(work / "oidc_secret"),
            "EUGENE_PLEXUS_APP_STATIC_DIR": str(static),
        }
        start("workbench", [str(m.WB_PY), "-m", "eugene_plexus_workbench"], wb_env)
        if not check("Workbench listens", bool(until(lambda: listening(WB_PORT), 90)), logs):
            return 1
        ada, bo = Person("p-ada"), Person("p-bo")
        ada.sign_in()
        bo.sign_in()
        run(data, ada, bo)
        return 0 if m.FAILURES == 0 else 1
    except AssertionError as exc:
        check(f"the run went on ({exc})", False)
        return 1
    finally:
        say("9. teardown")
        for name in list(procs):
            kill(name)
        if server is not None:
            server.should_exit = True
        threads = (AGENT_PORT, EUGENE_PORT, GW_PROXY, STUB_PORT)
        left = until(lambda: not [p for p in OWNED if p not in threads and listening(p)], 30)
        check("no owned port still listens", bool(left))
        note(f"logs: {logs}")
        note(f"result: {m.PASSES} passed, {m.FAILURES} failed")


if __name__ == "__main__":
    sys.exit(main())
