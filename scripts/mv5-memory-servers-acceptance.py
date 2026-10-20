"""Memory servers (memory v2's MV5), live.

The unit checks drive Workbench against a fake gateway and an in-process MCP server. What only
a live run shows is the whole path: a store migrated from schema 23 by a real Workbench
process, a real gateway and drivers, and the reference memory server
(`specs/scripts/m-memory-server.py`) in its own process over real MCP. A deterministic model
answers every chat (`Okay.`), so what is checked is the code's, not a model's. A recording
proxy keeps what Workbench sent the gateway.

  0. isolated: every ambient EUGENE_PLEXUS_* variable dropped, owned ports free
  1. the stack: a model as one on the machines and the same as an outside one, a gateway,
     Workbench over a schema 23 store, the reference server (token required) and one with
     only `recall`
 1b. the store came through: schema 25, its two new tables
  2. the address rule: under the default, loopback, `localhost` and this machine's own address
     are refused and nothing reaches the server
  3. connecting under `any`: a wrong token is refused by name; the right one connects, every
     verb found, the token never shown; a server with only recall says what it cannot do
  4. what is sent: Ada's own words, pasted material removed, under Standard; the answer too
     under Open; a secret-shaped turn not sent and counted; Bo's chats never reach her server
  5. what is given: the server's memory in the latest user turn, as data, and in the drawer
  6. an outside model is not given it, and the server is not asked; the drawer says why
  7. a slow server: the answer does not wait for it, and the drawer says why
  8. deleting a chat forgets its exchanges at the server
  9. the export carries what the server lists
 10. Delete all memory clears the server and checks it; a server that keeps everything is named
 11. the owner's "never" for outside services: no server may be used, and none is asked
 12. teardown by pid; no owned port still listening

**Safe beside a live install**: ambient EUGENE_PLEXUS_* variables dropped, ports 8490-8501,
teardown by pid.

Run with the Workbench interpreter:
  workbench/.venv/Scripts/python.exe workbench/scripts/mv5-memory-servers-acceptance.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
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
    DRV_OUTSIDE,
    WB_PORT,
    EUGENE_PORT,
    GW_PROXY,
    STUB_PORT,
    MEM_PORT,
    BARE_PORT,
    SPARE_1,
    SPARE_2,
) = range(8490, 8502)
OWNED = list(range(8490, 8502))
WORKBENCH = f"http://127.0.0.1:{WB_PORT}"
LOCAL, OUTSIDE = "mv5-local", "mv5-outside"
m.EUGENE_PORT, m.WORKBENCH, m.OUTSIDE = EUGENE_PORT, WORKBENCH, OUTSIDE
say, check, note, listening, Person = m.say, m.check, m.note, m.listening, m.Person
LOCK, WB_SENT = m.LOCK, m.WB_SENT
until = mv3.until
SPECS = m.ROOT / "specs"
TOKEN = "mv5-memory-token"
MEMORY = f"http://127.0.0.1:{MEM_PORT}"


class Stub(BaseHTTPRequestHandler):
    """A model that answers every chat `Okay.`, as both model ids."""

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
            return self._json({"data": [{"id": i, "object": "model"} for i in (LOCAL, OUTSIDE)]})
        return self._json({"error": {"message": "not found"}}, status=404)

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not body.get("stream"):
            content = json.dumps({"notes": []}) if body.get("response_format") else "Okay."
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
            {"choices": [{"index": 0, "delta": {"content": "Okay."}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ):
            self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


class Agent(BaseHTTPRequestHandler):
    """Topology only: two drivers."""

    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        body: Any = {"status": "ok"}
        if path == "/v1/components":
            body = {
                "components": [
                    {
                        "name": name,
                        "kind": "inference-driver",
                        "url": f"http://127.0.0.1:{port}/",
                        "status": "running",
                    }
                    for name, port in (("mv5-local", DRV_LOCAL), ("mv5-outside", DRV_OUTSIDE))
                ]
            }
        elif path == "/v1/runtimes":
            body = {"runtimes": []}
        elif path == "/v1/node":
            body = {"name": "mv5-node", "enrolled": False}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def state() -> dict[str, Any]:
    got: dict[str, Any] = httpx.get(f"{MEMORY}/state", timeout=5, trust_env=False).json()
    return got


def control(**values: Any) -> None:
    httpx.post(f"{MEMORY}/control", json=values, timeout=5, trust_env=False)


def plant(ident: str, text: str) -> None:
    httpx.post(f"{MEMORY}/memories", json={"id": ident, "text": text}, timeout=5, trust_env=False)


def config(**values: Any) -> Any:
    return httpx.patch(
        f"{WORKBENCH}/v1/config",
        json=values,
        headers={"Authorization": f"Bearer {m.ADMIN_TOKEN}"},
        trust_env=False,
        timeout=30,
    )


def put(person: Any, path: str, body: Any) -> Any:
    return person.call("PUT", path, json=body)


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


def latest_user(body: dict[str, Any]) -> str:
    return m.latest_user(body) if body else ""


def own_address() -> str | None:
    """This machine's address on its network, as another machine would reach it."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))
            return str(sock.getsockname()[0])
        except OSError:
            return None


def seed_schema_23(data: Path) -> None:
    mv3.seed_schema_23(m.WB, data)
    with sqlite3.connect(data / "workbench.sqlite3") as db:
        db.execute("DROP TABLE IF EXISTS memory_server_sent")
        db.execute("DROP TABLE IF EXISTS memory_servers")


def run(data: Path, ada: Any, bo: Any) -> None:
    say("1b. the store came through")
    with sqlite3.connect(data / "workbench.sqlite3") as db:
        version = db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0]
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check("schema 25", version == "25", version)
    check(
        "the memory server tables are there",
        {"memory_servers", "memory_server_sent"} <= tables,
        sorted(tables),
    )

    say("2. the address rule, by default")
    page = ada.get("/api/memory/server").json()
    check("Ada may connect a server, off this host", page["allowed"] and page["mode"] == "outside")
    hosts = ["127.0.0.1", "localhost"]
    if (mine := own_address()) is not None:
        hosts.append(mine)
    for host in hosts:
        refused = put(ada, "/api/memory/server", {"url": f"http://{host}:{MEM_PORT}/mcp"})
        detail = (refused.json().get("detail") or {}).get("message", "")
        check(
            f"{host} is refused, and says why",
            refused.status_code == 400 and ("loopback" in detail or "Workbench runs on" in detail),
            (refused.status_code, detail),
        )
    check("nothing reached the server", state()["tokens"] == [], state()["tokens"])

    say("3. connecting, with the owner's 'any'")
    check("the owner allows any address", config(memoryServers="any").status_code == 200)
    wrong = put(ada, "/api/memory/server", {"url": f"{MEMORY}/mcp", "token": "wrong"})
    check(
        "a wrong token is refused by name",
        wrong.status_code == 400 and "refused the token" in wrong.text,
        wrong.text,
    )
    done = put(ada, "/api/memory/server", {"url": f"{MEMORY}/mcp", "token": TOKEN})
    check("the right one connects", done.status_code == 200, done.text)
    server = done.json()["server"]
    check(
        "every verb is found, the token never shown",
        server["verbs"] == ["forget", "list", "purge", "recall", "retain"]
        and server["hasToken"]
        and TOKEN not in done.text,
        server,
    )
    bare = put(bo, "/api/memory/server", {"url": f"http://127.0.0.1:{BARE_PORT}/mcp"})
    missing = bare.json()["server"]["missing"] if bare.status_code == 200 else []
    check(
        "a server with only recall connects and says what it cannot do",
        len(missing) == 4 and any("cannot forget" in x for x in missing),
        missing,
    )
    check("Bo disconnects his", bo.delete("/api/memory/server").json()["server"] is None)

    say("4. what is sent")
    chat = ada.new_chat(LOCAL)
    ask(ada, chat, "I moved to Lisbon in May.\n\n```\nERROR pasted build log line\n```")
    sent = until(lambda: state()["retained"], 30)
    first = (sent or [{}])[0]
    check(
        "Ada's own words went, the pasted block did not, nor the answer",
        first.get("content") == "I moved to Lisbon in May.",
        first,
    )
    check(
        "with its chat and exchange as the document, when it was asked, and no title",
        str(first.get("document_id", "")).startswith(f"{chat}:")
        and str(first.get("timestamp", "")).startswith("20")
        and first.get("context") == "A Workbench chat",
        first,
    )
    check("Open sends the answer too", config(memoryWritePolicy="open").status_code == 200)
    ask(ada, chat, "I drink green tea in the mornings.")
    later = until(lambda: state()["retained"][1:], 30)
    check(
        "the answer went with her words, and the chat's title",
        bool(later)
        and later[0]["content"].endswith("\n\nAnswer: Okay.")
        and later[0]["context"].startswith("Chat: "),
        later,
    )
    config(memoryWritePolicy="standard")
    ask(ada, chat, "my password is hunter2-Gx9!kQ")
    skipped = until(lambda: ada.get("/api/memory/server").json()["server"]["skipped"] == 1, 30)
    check("a secret-shaped turn was not sent, and is counted", bool(skipped))
    check("it never reached the server", len(state()["retained"]) == 2, state()["retained"])
    bo_chat = bo.new_chat(LOCAL)
    ask(bo, bo_chat, "I keep three beehives.")
    time.sleep(3)
    check(
        "Bo's chat never reached Ada's server",
        all("beehives" not in r["content"] for r in state()["retained"]),
    )

    say("5. what is given")
    plant("old-1", "Ada's sister is called Grace and lives in Porto.")
    recall_chat = ada.new_chat(LOCAL)
    _, body = ask(ada, recall_chat, "Where does my sister live?")
    user = latest_user(body)
    check(
        "the server's memory is in the latest user turn, as data",
        "<memory-server-" in user and "lives in Porto" in user and "data, not instructions" in user,
        user[:600],
    )
    check(
        "the server was asked with her words and a budget",
        state()["recalled"][-1]["query"] == "Where does my sister live?"
        and 0 < state()["recalled"][-1]["max_tokens"] <= 2000,
        state()["recalled"][-1],
    )
    drawer = ada.get(f"/api/chats/{recall_chat}/memory").json()["turn"]["memoryServer"]
    check(
        "the drawer names the server and what it sent",
        drawer["name"] == "Reference memory"
        and any("Porto" in x["text"] for x in drawer["memories"]),
        drawer,
    )

    say("6. an outside model")
    config(memoryExternalModels="always")
    asked = len(state()["recalled"])
    outside_chat = ada.new_chat(OUTSIDE)
    _, body = ask(ada, outside_chat, "Where does my sister live?")
    check(
        "the outside model was not given it, and the server was not asked",
        "Porto" not in json.dumps(body) and len(state()["recalled"]) == asked,
        latest_user(body)[:400],
    )
    drawer = ada.get(f"/api/chats/{outside_chat}/memory").json()["turn"]["memoryServer"]
    check("the drawer says why", "runs outside your machines" in (drawer.get("why") or ""), drawer)
    config(memoryExternalModels="ask")

    say("7. a slow server")
    control(slow=6)
    slow_chat = ada.new_chat(LOCAL)
    started = time.perf_counter()
    _, body = ask(ada, slow_chat, "Where does my sister live?")
    took = time.perf_counter() - started
    control(slow=0)
    check("the answer did not wait for it", took < 5.5 and "Porto" not in latest_user(body), took)
    drawer = ada.get(f"/api/chats/{slow_chat}/memory").json()["turn"]["memoryServer"]
    check(
        "the drawer says it did not answer in time",
        drawer.get("why") == "The memory server did not answer in time.",
        drawer,
    )

    say("8. deleting a chat")
    documents = [r["document_id"] for r in state()["retained"] if r["document_id"].startswith(chat)]
    gone = ada.delete(f"/api/chats/{chat}")
    check("the chat is deleted", gone.status_code == 204, gone.text)
    forgotten = until(lambda: set(documents) <= set(state()["forgotten"]), 60)
    check("its exchanges were forgotten at the server", bool(forgotten), state()["forgotten"])
    check(
        "and are not there",
        not set(documents) & set(state()["memories"]),
        sorted(state()["memories"]),
    )

    say("9. the export")
    exported = ada.get("/api/memory/export?format=json").json().get("memoryServer") or {}
    check(
        "the export carries what the server lists",
        any("Porto" in x["text"] for x in exported.get("memories") or []),
        exported,
    )

    say("10. Delete all memory")
    done = ada.delete("/api/memory/notes?confirm=all").json()
    check(
        "the server was cleared and checked",
        done.get("server", {}).get("cleared") is True and state()["memories"] == {},
        done,
    )
    plant("kept", "Still here.")
    control(liar=True)
    done = ada.delete("/api/memory/notes?confirm=all").json()
    control(liar=False)
    check(
        "a server that keeps everything is named",
        done.get("server", {}).get("cleared") is False
        and "still lists 1" in done["server"]["message"],
        done,
    )

    say("11. the owner's 'never'")
    config(memoryExternalModels="never")
    page = ada.get("/api/memory/server").json()
    check(
        "no server may be used, and the page says why",
        page["allowed"] is False and "never" in (page["why"] or ""),
        page,
    )
    asked = len(state()["recalled"])
    never_chat = ada.new_chat(LOCAL)
    ask(ada, never_chat, "Where does my sister live?")
    check("the server was not asked", len(state()["recalled"]) == asked)
    config(memoryExternalModels="ask")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    procs: dict[str, subprocess.Popen[bytes]] = {}
    work = Path(tempfile.mkdtemp(prefix="ep-mv5-"))
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
        reference = SPECS / "scripts" / "m-memory-server.py"
        missing = [p for p in (m.WB_PY, m.GW_PY, m.DRV_PY, reference) if not Path(p).exists()]
        if not check("every interpreter and the reference server are here", not missing, missing):
            return 1

        say("1. the stack, over a schema 23 store")
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
        start(
            "memory",
            [str(m.WB_PY), str(reference), "--port", str(MEM_PORT), "--token", TOKEN],
            base,
        )
        start(
            "memory-bare",
            [str(m.WB_PY), str(reference), "--port", str(BARE_PORT),
             "--without", "retain,forget,purge,list"],
            base,
        )  # fmt: skip

        def driver(name: str, port: int, model: str, locality: str) -> None:
            (work / f"{name}.yaml").write_text(
                f"provider: openai_compat_custom\nbaseUrl: http://127.0.0.1:{STUB_PORT}\n"
                f"modelId: {model}\nbackendLocality: {locality}\nrequestTimeoutSeconds: 120\n",
                encoding="utf-8",
                newline="\n",
            )
            start(
                name,
                [str(m.DRV_PY), "-m", "eugene_plexus_inference_driver"],
                {
                    **base,
                    "EUGENE_PLEXUS_DRIVER_CONFIG_FILE": str(work / f"{name}.yaml"),
                    "EUGENE_PLEXUS_DRIVER_BIND_HOST": "127.0.0.1",
                    "EUGENE_PLEXUS_DRIVER_BIND_PORT": str(port),
                },
            )

        driver("driver-local", DRV_LOCAL, LOCAL, "local")
        driver("driver-outside", DRV_OUTSIDE, OUTSIDE, "external")
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

        wanted = {LOCAL, OUTSIDE}
        if not check(
            f"the gateway routes {sorted(wanted)}",
            bool(until(lambda: wanted <= listed(), 180, 1.0)),
            (sorted(listed()), logs),
        ):
            return 1
        if not check(
            "the reference servers listen",
            bool(until(lambda: listening(MEM_PORT) and listening(BARE_PORT), 60)),
            logs,
        ):
            return 1

        data = work / "workbench-data"
        data.mkdir()
        seed_schema_23(data)
        static = work / "static"
        static.mkdir()
        (static / "index.html").write_text(
            "<!doctype html><title>Workbench</title>", encoding="utf-8"
        )
        (work / "app_key").write_text("mv5-app-key", encoding="utf-8")
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
        say("12. teardown")
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
