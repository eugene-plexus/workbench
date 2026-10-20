"""A project's documents (memory v2's MV7b), live.

What only a live run shows: real documents read by a real Workbench's worker into a project's
own scope, and which of them each request was sent, through a real gateway and driver. A
deterministic model answers every chat.

  0. isolated: every ambient EUGENE_PLEXUS_* variable dropped, owned ports free
  1. the stack: a model, a driver, a gateway, Workbench over a schema 26 store
  2. Ada keeps a document in My documents and one in a project
  3. a chat in the project, with Documents on, is sent both; with Also search My documents
     off, only the project's
  4. a chat in no project is sent My documents' and never the project's
  5. a file of the same name kept to the project replaces only the project's
  6. deleting the project and keeping what was in it moves its documents to My documents,
     replacing one of the same name; deleting another with everything takes its documents
  7. teardown by pid; no owned port still listening

**Safe beside a live install**: ambient EUGENE_PLEXUS_* variables dropped, ports 8540-8551,
teardown by pid.

Run with the Workbench interpreter:
  workbench/.venv/Scripts/python.exe workbench/scripts/mv7b-project-documents-acceptance.py
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
LOCAL = "mv7b-local"
m.EUGENE_PORT, m.WORKBENCH, m.OUTSIDE = EUGENE_PORT, WORKBENCH, "mv7b-outside"
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
            body = {"name": "mv7b-node", "enrolled": False}
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


def block_of(sent: dict[str, Any]) -> str:
    """The documents block of the latest message: what was sent of the documents, without
    the question (which names the words too)."""
    said = m.latest_user(sent) if sent else ""
    start = said.find("<documents-")
    end = said.find("</documents-")
    return said[start:end] if start >= 0 and end > start else ""


def upload_to(person: Any, name: str, data: bytes, project_id: str | None) -> Any:
    return person.post(
        "/api/documents",
        files=[("files", (name, data, "application/octet-stream"))],
        data={"projectId": project_id} if project_id else None,
    )


def read_all(person: Any, project_id: str | None = None) -> list[dict[str, Any]]:
    params = {"projectId": project_id} if project_id else None

    def done() -> list[dict[str, Any]] | None:
        docs = person.get("/api/documents", params=params).json()["documents"]
        return docs if all(d["state"] in ("ready", "skipped", "failed") for d in docs) else None

    found = until(done, 120, 0.5)
    assert found is not None, "the documents were not read in time"
    return list(found)


def docs_chat(person: Any, project_id: str | None) -> str:
    chat = chat_in(person, project_id)
    changed = person.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on"}})
    assert changed.status_code == 200, changed.text
    return chat


def run(data: Path, ada: Any, bo: Any) -> None:
    say("2. Ada keeps documents")
    garden = ada.post("/api/projects", json={"name": "Garden"}).json()["id"]
    made = upload_to(ada, "plan.md", md("The garden plan word is HELIOTROPE."), garden)
    check("a document is kept to the project", made.status_code == 201, made.text)
    made = upload_to(ada, "general.md", md("The general word is PLUMBAGO."), None)
    check("and one to My documents", made.status_code == 201, made.text)
    mine = read_all(ada, garden)
    check(
        "the project lists its own",
        [d["name"] for d in mine] == ["plan.md"] and mine[0]["state"] == "ready",
        mine,
    )
    check(
        "My documents lists only its own",
        [d["name"] for d in read_all(ada)] == ["general.md"],
    )

    say("3. a chat in the project")
    inside = docs_chat(ada, garden)
    _, sent = ask(ada, inside, "What are the words, HELIOTROPE and PLUMBAGO?")
    said = block_of(sent)
    check("is sent both documents", "HELIOTROPE" in said and "PLUMBAGO" in said, said[:400])
    off = ada.patch(f"/api/projects/{garden}", json={"generalDocuments": False})
    check("Also search My documents turns off", off.status_code == 200, off.text)
    _, sent = ask(ada, inside, "And the words again, HELIOTROPE and PLUMBAGO?")
    said = block_of(sent)
    check(
        "with it off, only the project's",
        "HELIOTROPE" in said and "PLUMBAGO" not in said,
        said[:400],
    )

    say("4. a chat in no project")
    outside = docs_chat(ada, None)
    _, sent = ask(ada, outside, "What are the words, HELIOTROPE and PLUMBAGO?")
    said = block_of(sent)
    check(
        "is sent My documents' and never the project's",
        "PLUMBAGO" in said and "HELIOTROPE" not in said,
        said[:400],
    )

    say("5. a file of the same name")
    upload_to(ada, "general.md", md("A project file named like My documents' one."), garden)
    read_all(ada, garden)
    check(
        "kept to the project leaves My documents' own",
        [d["name"] for d in read_all(ada)] == ["general.md"]
        and sorted(d["name"] for d in read_all(ada, garden)) == ["general.md", "plan.md"],
    )

    say("6. deleting projects")
    released = ada.delete(f"/api/projects/{garden}", params={"keep": "true"})
    body = released.json() if released.status_code == 200 else {}
    check(
        "keeping what was in it moves both documents, replacing one",
        body.get("documents") == 2 and body.get("replaced") == 1,
        released.text,
    )
    names = sorted(d["name"] for d in read_all(ada))
    check("My documents holds them now", names == ["general.md", "plan.md"], names)
    gone = ada.post("/api/projects", json={"name": "Gone"}).json()["id"]
    upload_to(ada, "gone.md", md("This goes with its project."), gone)
    read_all(ada, gone)
    taken = ada.delete(f"/api/projects/{gone}")
    check(
        "deleting with everything takes its document",
        taken.status_code == 200 and taken.json().get("documents") == 1,
        taken.text,
    )
    check("and it is nowhere", "gone.md" not in [d["name"] for d in read_all(ada)])


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    procs: dict[str, subprocess.Popen[bytes]] = {}
    work = Path(tempfile.mkdtemp(prefix="ep-mv7b-"))
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
        (work / "app_key").write_text("mv7b-app-key", encoding="utf-8")
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
        say("7. teardown")
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
