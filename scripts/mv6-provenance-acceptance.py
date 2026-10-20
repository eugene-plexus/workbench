"""Provenance, "Still true?" from changed documents, and the tidy pass (memory v2's MV6), live.

The unit checks drive Workbench in process. What only a live run shows is the whole path: a
store migrated from schema 25 by a real Workbench process, real documents read by its worker,
a real gateway and driver, and a model that cites by number. A deterministic model answers every
chat (`The code word is in the survey [1].`), so what is checked is the code's.

  0. isolated: every ambient EUGENE_PLEXUS_* variable dropped, owned ports free
  1. the stack: a model, a driver, a gateway, Workbench over a schema 25 store holding Ada's note
 1b. the store came through: schema 26, its new tables, Ada's note kept
 1c. notes for the tidy pass, before the page is first read: two that say the same thing, an
     event long past
  2. an answer cites a document, and Remember links it (not the passage it did not cite)
  3. nothing is asked while the document is as it was
  4. replacing the document asks "Still true?" with the reason; the note is still sent
  5. Still true moves the link to the new version
  6. removing the document asks again; Ask me next week puts it off; Still true drops the link
  7. Bo cannot link Ada's document, and is asked nothing of hers
  8. the tidy pass: the two notes proposed and merged into one line of versions; the old event
     proposed and removed
  9. the export carries a note's documents
 10. teardown by pid; no owned port still listening

**Safe beside a live install**: ambient EUGENE_PLEXUS_* variables dropped, ports 8540-8551,
teardown by pid.

Run with the Workbench interpreter:
  workbench/.venv/Scripts/python.exe workbench/scripts/mv6-provenance-acceptance.py
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
from datetime import date, timedelta
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
LOCAL = "mv6-local"
m.EUGENE_PORT, m.WORKBENCH, m.OUTSIDE = EUGENE_PORT, WORKBENCH, "mv6-outside"
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
            body = {"name": "mv6-node", "enrolled": False}
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


def seed_schema_25(data: Path) -> None:
    """A store as schema 25 wrote it: Ada keeps one note. Made by today's store, then put back
    to schema 25's shape (MV6's tables, trigger and column removed, its version said)."""
    sys.path.insert(0, str(m.WB / "src"))
    from eugene_plexus_workbench.store import MemoryNote, Store

    async def go() -> None:
        store = Store(data / "workbench.sqlite3")
        await store.open()
        try:
            await store.add_memory_note(
                MemoryNote(
                    id="ada-leeds",
                    owner="p-ada",
                    text="Lives in Leeds.",
                    kind="fact",
                    created_at=time.time() - 86400,
                    updated_at=time.time() - 86400,
                ),
                limit=1000,
            )
        finally:
            await store.close()

    asyncio.run(go())
    with sqlite3.connect(data / "workbench.sqlite3") as db:
        db.execute("DROP TRIGGER memory_notes_documents_ad")
        db.execute("DROP TABLE memory_note_documents")
        db.execute("DROP TABLE memory_tidy")
        db.execute("ALTER TABLE memory_prefs DROP COLUMN tidy_at")
        db.execute("UPDATE meta SET value = '25' WHERE key = 'schema'")


def run(data: Path, ada: Any, bo: Any) -> None:
    say("1b. the store came through")
    with sqlite3.connect(data / "workbench.sqlite3") as db:
        version = db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0]
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check("schema 26", version == "26", version)
    check(
        "the provenance and tidy tables are there",
        {"memory_note_documents", "memory_tidy"} <= tables,
        sorted(tables),
    )
    check(
        "a backup of the schema 25 store was kept",
        bool(list(data.glob("*.schema-25*"))),
        sorted(p.name for p in data.iterdir()),
    )

    say("1c. notes for the tidy pass, before the page is first read")
    tea = typed(ada, "Prefers green tea in the afternoon.", tags=["drinks"])
    tea_twin = typed(ada, "Prefers green tea in the afternoon!", pinned=True)
    flight = typed(ada, "Flight to Lisbon.", kind="event")
    with sqlite3.connect(data / "workbench.sqlite3") as db:
        old = (date.today() - timedelta(days=40)).isoformat()
        db.execute("UPDATE memory_notes SET expires_at = ? WHERE id = ?", (old, flight))

    say("2. an answer cites a document, and Remember links it")
    made = add(ada, "survey.md", md(f"The code word for the lighthouse is {CODE}."))
    check("the survey is kept", made.status_code == 201, made.text)
    made = add(ada, "other.md", md("Plumbago grows in the shade."))
    survey = doc_named(ada, "survey.md")
    check("both are read", survey["state"] == "ready", survey)
    chat = ada.new_chat(LOCAL, documents="on")
    answer = ask(ada, chat, "What is the code word for the lighthouse? And plumbago?")[0]
    passages = [s for s in answer["sources"] if s.get("passage")]
    cited = [s for s in passages if s.get("cited")]
    check(
        "the answer cites one passage of the survey",
        len(cited) == 1 and cited[0]["name"] == "survey.md",
        [(s["name"], s.get("cited")) for s in passages],
    )
    kept = ada.post(
        "/api/memory/notes",
        json={
            "text": f"The lighthouse code word is {CODE}.",
            "source": "button",
            "chatId": chat,
            "messageId": answer["id"],
        },
    )
    check("Remember on the answer keeps a note", kept.status_code == 201, kept.text)
    code_note = kept.json()["note"]["id"]
    linked = documents_of(ada, code_note)
    check(
        "the note rests on the survey, from the answer, with the place it cited",
        [(d["name"], d["how"]) for d in linked] == [("survey.md", "answer")]
        and bool(linked[0]["place"]),
        linked,
    )

    say("3. nothing is asked while the document is as it was")
    first = page(ada)
    check("no Still true? for the note", code_note not in {r["id"] for r in first["revisit"]})

    say("4. replacing the document asks Still true? with the reason")
    replaced = add(ada, "survey.md", md("The code word changed this year."))
    check("the survey is replaced", replaced.status_code == 201, replaced.text)
    new_survey = doc_named(ada, "survey.md")
    reasons = asked(ada).get(code_note)
    check(
        "Still true? names the survey and says it changed",
        bool(reasons) and reasons[0]["name"] == "survey.md" and reasons[0]["change"] == "changed",
        reasons,
    )
    plain = ada.new_chat(LOCAL)
    _, sent = ask(ada, plain, "What is the lighthouse code word?")
    check(
        "the note is still sent while it waits (MV6-C2)",
        CODE in m.latest_user(sent) if sent else False,
        m.latest_user(sent)[:400] if sent else sent,
    )

    say("5. Still true moves the link")
    yes = ada.post("/api/memory/revisit", json={"noteId": code_note, "answer": "yes"})
    check("Still true is taken", yes.status_code == 200, yes.text)
    check(
        "the link is at the new version",
        [d["docId"] for d in documents_of(ada, code_note)] == [new_survey["id"]],
        documents_of(ada, code_note),
    )
    check("and nothing is asked", code_note not in asked(ada))

    say("6. removing the document asks again")
    second = typed(ada, "The survey is the one for the lighthouse.")
    check(
        "Ada links the survey to a second note",
        ada.post(
            f"/api/memory/notes/{second}/documents", json={"docId": new_survey["id"]}
        ).status_code
        == 200,
    )
    check(
        "the survey is removed",
        ada.delete(f"/api/documents/{new_survey['id']}").status_code == 204,
    )
    now = asked(ada)
    check(
        "both notes are asked about at once, the survey removed",
        all(now.get(n, [{}])[0].get("change") == "removed" for n in (code_note, second)),
        now,
    )
    later = ada.post("/api/memory/revisit", json={"noteId": second, "answer": "later"})
    check("Ask me next week puts it off", later.status_code == 200 and second not in asked(ada))
    yes = ada.post("/api/memory/revisit", json={"noteId": code_note, "answer": "yes"})
    check(
        "Still true drops the link to a removed document",
        yes.status_code == 200 and documents_of(ada, code_note) == [],
        documents_of(ada, code_note),
    )

    say("7. Bo")
    other = doc_named(ada, "other.md")
    bo_note = typed(bo, "Bo's note.")
    refused = bo.post(f"/api/memory/notes/{bo_note}/documents", json={"docId": other["id"]})
    check("Bo cannot link Ada's document", refused.status_code == 404, refused.status_code)
    check("Bo is asked nothing", page(bo)["revisit"] == [])

    say("8. the tidy pass")
    items = {t["kind"]: t for t in page(ada)["tidy"]}
    duplicate = items.get("duplicate")
    check(
        "the two notes that say the same thing are proposed",
        duplicate is not None and {n["id"] for n in duplicate["notes"]} == {tea, tea_twin},
        items,
    )
    past = items.get("past")
    check(
        "the event long past is proposed",
        past is not None and [n["id"] for n in past["notes"]] == [flight],
        items,
    )
    if duplicate is not None:
        merged = ada.post(
            f"/api/memory/tidy/{duplicate['id']}", json={"action": "keep", "keepId": tea}
        )
        check("Keep this one merges them", merged.status_code == 200, merged.text)
        whole = ada.get(f"/api/memory/notes/{tea}").json()
        check(
            "the kept note has both pins and tags, and the other as its earlier version",
            whole["note"]["pinned"] is True
            and "drinks" in whole["note"]["tags"]
            and [e["id"] for e in whole["earlier"]] == [tea_twin],
            whole,
        )
    if past is not None:
        removed = ada.post(f"/api/memory/tidy/{past['id']}", json={"action": "remove"})
        gone = ada.get(f"/api/memory/notes/{flight}").status_code
        check(
            "Remove forgets the event",
            removed.status_code == 200 and gone == 404,
            (removed.text, gone),
        )
    check("nothing else waits from the pass", page(ada)["tidy"] == [])

    say("9. the export")
    check(
        "Ada links other.md to a note",
        ada.post(f"/api/memory/notes/{second}/documents", json={"docId": other["id"]}).status_code
        == 200,
    )
    exported = ada.get("/api/memory/export", params={"format": "json"}).json()
    entry = next((n for n in exported["notes"] if n["id"] == second), {})
    check(
        "the export carries the note's documents",
        "other.md" in [d["name"] for d in entry.get("documents", [])],
        entry,
    )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    procs: dict[str, subprocess.Popen[bytes]] = {}
    work = Path(tempfile.mkdtemp(prefix="ep-mv6-"))
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

        say("1. the stack, over a schema 25 store")
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
        seed_schema_25(data)
        static = work / "static"
        static.mkdir()
        (static / "index.html").write_text(
            "<!doctype html><title>Workbench</title>", encoding="utf-8"
        )
        (work / "app_key").write_text("mv6-app-key", encoding="utf-8")
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
        say("10. teardown")
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
