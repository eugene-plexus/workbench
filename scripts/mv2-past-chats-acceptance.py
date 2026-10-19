"""Past chats (memory v2's MV2), live: a person's chats are found, used and forgotten, and
nobody else's are.

The unit checks drive Workbench against a fake gateway. What only a live run shows is the
whole path an exchange takes: a store migrated from schema 22 and backfilled into each
person's own index by a real Workbench process, a real search model adding meaning to it, a
real gateway and drivers, a real `llama-server` answering from a small model, and a stub
hosted API that calls `search_chats` as a model that calls tools would. Recording proxies keep
what Workbench sent the gateway and what reached the engine.

  0. isolated: every ambient EUGENE_PLEXUS_* variable dropped, owned ports free
  1. the stack: a chat engine and a search-model engine (CPU), a driver for each and one for
     the hosted stub (external), a gateway, Workbench **over a schema 22 store** holding three
     people's chats, one chat with two answer versions and one person with 400 chats
 1b. the store came through (schema 23, the schema 22 file kept); the backfill puts each
     shown exchange in its person's own index, never another's; stopped part way and started
     again, it resumes where it stopped; its rate is recorded
  2. Search chats: found by words and by meaning; a version chosen changes what is found; a
     result names the exchange to open at
  3. Use past chats: off by default; on, the turn carries the matching excerpt to the engine,
     never this chat's own, and the answer records which exchanges went
  4. `search_chats` from a model on an outside account (after "Send for this chat"); with
     documents set never to leave, it is not offered and the kept result and query are withheld
  5. the owner, even where they may read a person's chats, searches none of them and reads no
     excerpt; another person finds nothing of hers
  6. a connection searches past chats only with the right
  7. Delete: no exchange, word or signature of the chat is left in the index; the answers
     that used it keep no excerpt; a kept `search_chats` result is replaced; a fresh chat
     with Use past chats on is not sent it
  8. teardown by pid; no owned port still listening

**Safe beside a live install**: ambient EUGENE_PLEXUS_* variables dropped, ports 8470-8481,
teardown by pid.

Run with the Workbench interpreter:
  workbench/.venv/Scripts/python.exe workbench/scripts/mv2-past-chats-acceptance.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
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
import httpx2
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("mr1", HERE / "mr1-memory-acceptance.py")
assert _spec is not None and _spec.loader is not None
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

(
    GW_PORT,
    AGENT_PORT,
    DRV_LOCAL,
    ENGINE,
    ENGINE_PROXY,
    DRV_HOSTED,
    HOSTED,
    WB_PORT,
    EUGENE_PORT,
    GW_PROXY,
    EMBED,
    DRV_EMBED,
) = range(8470, 8482)
OWNED = list(range(8470, 8482))
OURS = (AGENT_PORT, ENGINE_PROXY, HOSTED, EUGENE_PORT, GW_PROXY)
WORKBENCH = f"http://127.0.0.1:{WB_PORT}"
LOCAL, OUTSIDE, SEARCH = "mv2-local", "mv2-outside", "mv2-search"
ADMIN_TOKEN = m.ADMIN_TOKEN
EMBED_FILE = Path(
    os.environ.get(
        "EP_EMBED_FILE",
        str(
            Path.home() / ".eugene-plexus" / "acceptance-models" / "Qwen3-Embedding-0.6B-Q8_0.gguf"
        ),
    )
)
#: The helpers of the MV1 run read these at call time.
m.EUGENE_PORT, m.WORKBENCH, m.OUTSIDE = EUGENE_PORT, WORKBENCH, OUTSIDE
m.PEOPLE["p-cy"] = {"name": "Cy", "role": "member", "username": "cy"}
say, check, note, listening, Person = m.say, m.check, m.note, m.listening, m.Person
LOCK, WB_SENT, ENGINE_BODIES, HOSTED_BODIES = m.LOCK, m.WB_SENT, m.ENGINE_BODIES, m.HOSTED_BODIES
users_of, latest_user, tool_names = m.users_of, m.latest_user, m.tool_names

BULK_CHATS, BULK_TURNS = 400, 5
FORGOTTEN_CHAT = "A chat this search found was deleted since, so its result is not kept."
WITHHELD = "Past-chat results are not sent to this model."

#: What the hosted stub calls when `search_chats` is offered (once a turn), or None.
HOSTED_TOOL: dict[str, str | None] = {"query": None}


class Hosted(m.Hosted):  # type: ignore[name-defined,misc]
    """The MV1 stub, calling `search_chats` instead of `search_memory`."""

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) or b"{}"
        body = json.loads(raw)
        if self.path.split("?")[0] != "/v1/chat/completions" or not body.get("stream"):
            self.rfile = __import__("io").BytesIO(raw)
            self.headers.replace_header("content-length", str(len(raw)))
            return super().do_POST()
        with LOCK:
            HOSTED_BODIES.append(body)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        messages = body.get("messages") or []
        last_user = max((i for i, x in enumerate(messages) if x.get("role") == "user"), default=0)
        answered = any(x.get("role") == "tool" for x in messages[last_user:])
        offered = "search_chats" in tool_names(body)
        if HOSTED_TOOL["query"] and offered and not answered:
            call = {
                "index": 0,
                "id": "call-chats-1",
                "type": "function",
                "function": {
                    "name": "search_chats",
                    "arguments": json.dumps({"query": HOSTED_TOOL["query"]}),
                },
            }
            frames = [
                {"choices": [{"index": 0, "delta": {"tool_calls": [call]}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            ]
        else:
            frames = [
                {"choices": [{"index": 0, "delta": {"content": "Hosted answer"}}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
        for frame in frames:
            self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


class Agent(BaseHTTPRequestHandler):
    """Topology only: three drivers."""

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
                    for name, port in (
                        ("mv2-local-driver", DRV_LOCAL),
                        ("mv2-outside-driver", DRV_HOSTED),
                        ("mv2-search-driver", DRV_EMBED),
                    )
                ]
            }
        elif path == "/v1/runtimes":
            body = {"runtimes": []}
        elif path == "/v1/node":
            body = {"name": "mv2-node", "enrolled": False}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def until(condition: Any, seconds: float, every: float = 0.5) -> Any:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        value = condition()
        if value:
            return value
        time.sleep(every)
    return condition() or None


# --------------------------------------------------------------------------- #
# The store, as schema 22 left it
# --------------------------------------------------------------------------- #

TRIP_Q = "Where can I find the best custard tarts in Lisbon?"
TRIP_A1 = "Manteigaria on Rua do Loreto bakes them fresh every hour."
TRIP_A2 = "Pasteis de Belem, near the monastery, has made them since 1837."
BIKE_Q = "Which folding bicycle did I buy last spring?"
BIKE_A = "You bought a Brompton C Line in racing green."
CELLO_Q = "What is my cello teacher's name?"
CELLO_A = "Your cello teacher is Ms. Okafor."


def seed_schema_22(wb: Path, data: Path) -> dict[str, str]:
    """A store as schema 22 wrote it: Ada's trip chat with two answer versions (the second
    shown), her bicycle chat, Bo's cello chat, and Cy's 400 chats of five exchanges."""
    sys.path.insert(0, str(wb / "src"))
    from eugene_plexus_workbench.store import Chat, Message, Store

    ids: dict[str, str] = {}

    async def go() -> None:
        store = Store(data / "workbench.sqlite3")
        await store.open()
        try:
            now = time.time() - 30 * 86400

            async def chat(cid: str, owner: str, title: str) -> None:
                await store.create_chat(Chat(cid, owner, title, LOCAL, now, now))

            async def turn(cid: str, mid: str, q: str, a: str, parent: str | None) -> str:
                await store.add_message(
                    Message(f"{mid}-u", cid, 0, "user", "done", now, content=q), parent_id=parent
                )
                await store.add_message(
                    Message(f"{mid}-a", cid, 0, "assistant", "done", now, content=a),
                    parent_id=f"{mid}-u",
                )
                return f"{mid}-a"

            await chat("ada-trip", "p-ada", "Lisbon trip")
            ids["trip_a1"] = await turn("ada-trip", "trip1", TRIP_Q, TRIP_A1, None)
            await store.add_message(
                Message("trip1-b", "ada-trip", 0, "assistant", "done", now, content=TRIP_A2),
                parent_id="trip1-u",
            )
            ids["trip_a2"] = "trip1-b"
            await chat("ada-bike", "p-ada", "Bicycles")
            ids["bike"] = await turn("ada-bike", "bike1", BIKE_Q, BIKE_A, None)
            await chat("bo-cello", "p-bo", "Cello")
            ids["cello"] = await turn("bo-cello", "cello1", CELLO_Q, CELLO_A, None)
        finally:
            await store.close()

    asyncio.run(go())
    with sqlite3.connect(data / "workbench.sqlite3") as db:
        rows_chats, rows_messages = [], []
        base = time.time() - 60 * 86400
        for c in range(BULK_CHATS):
            cid = f"cy-{c:04d}"
            rows_chats.append((cid, "p-cy", f"Garden notes {c}", LOCAL, base + c, base + c))
            parent = None
            for t in range(BULK_TURNS):
                u, a = f"{cid}-u{t}", f"{cid}-a{t}"
                rows_messages.append(
                    (u, cid, 2 * t + 1, "user", f"How often should I water plant {c}-{t} in "
                     "the summer heat?", "done", base + c, parent)
                )  # fmt: skip
                rows_messages.append(
                    (a, cid, 2 * t + 2, "assistant", f"Water plant {c}-{t} every second "
                     "morning, deeply, and mulch it.", "done", base + c, u)
                )  # fmt: skip
                parent = a
        db.executemany(
            "INSERT INTO chats (id, owner, title, model, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows_chats,
        )
        db.executemany(
            "INSERT INTO messages (id, chat_id, seq, role, content, status, created_at, "
            "parent_id, chosen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
            rows_messages,
        )
        db.execute("ALTER TABLE connections DROP COLUMN search_chats")
        db.execute("ALTER TABLE memory_prefs DROP COLUMN chats_tool")
        db.execute("UPDATE meta SET value = '22' WHERE key = 'schema'")
    return ids


def index_db(data: Path, sub: str) -> Path:
    sys.path.insert(0, str(WB / "src"))
    from eugene_plexus_workbench import files

    return data / "knowledge" / files.person_key(sub) / "index.sqlite3"


def exchanges(data: Path, sub: str) -> dict[str, str]:
    """Each exchange the person's index holds, by id: its chat."""
    path = index_db(data, sub)
    if not path.is_file():
        return {}
    db = sqlite3.connect(path)
    try:
        return dict(db.execute("SELECT id, source FROM documents WHERE scope = 'exchanges'"))
    except sqlite3.Error:
        return {}
    finally:
        db.close()


def matches(data: Path, sub: str, word: str) -> int:
    db = sqlite3.connect(index_db(data, sub))
    try:
        return int(
            db.execute(
                "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (word,)
            ).fetchone()[0]
        )
    finally:
        db.close()


def signature_kept(data: Path, sub: str, chat: str) -> bool:
    db = sqlite3.connect(index_db(data, sub))
    try:
        return (
            db.execute("SELECT 1 FROM meta WHERE key = ?", (f"exchanges:{chat}",)).fetchone()
            is not None
        )
    finally:
        db.close()


async def _mcp(token: str, work: Any) -> Any:
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx2.AsyncClient(trust_env=False, timeout=120, headers=headers) as http,
        Client(streamable_http_client(f"{WORKBENCH}/mcp", http_client=http), cache=None) as client,
    ):
        return await work(client)


def mcp_tools(token: str) -> list[str]:
    async def go(client: Any) -> list[str]:
        return [t.name for t in (await client.list_tools()).tools]

    return list(asyncio.run(_mcp(token, go)))


def mcp_call(token: str, name: str, **arguments: Any) -> Any:
    async def go(client: Any) -> Any:
        return await client.call_tool(name, arguments)

    return asyncio.run(_mcp(token, go))


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #

WB: Path = m.WB


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    procs: dict[str, subprocess.Popen[bytes]] = {}
    work = Path(tempfile.mkdtemp(prefix="ep-mv2-"))
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

    def sent_since(count: int) -> dict[str, Any]:
        return m.newest(WB_SENT, count, "the gateway")

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
        missing = [
            p
            for p in (m.WB_PY, m.GW_PY, m.DRV_PY, m.LLAMA, m.MODEL_FILE, EMBED_FILE)
            if not Path(p).exists()
        ]
        if not check(
            "the engine, both models and every interpreter are here", not missing, missing
        ):
            return 1
        check(f"ports {OWNED[0]}-{OWNED[-1]} free; Workbench from {WB}", True)

        say("1. the stack, over a schema 22 store")
        threading.Thread(target=m.serve, args=(Agent, AGENT_PORT), daemon=True).start()
        threading.Thread(target=m.serve, args=(Hosted, HOSTED), daemon=True).start()
        threading.Thread(
            target=m.serve,
            args=(m.make_proxy(ENGINE, ENGINE_BODIES, m.ENGINE_TIMINGS), ENGINE_PROXY),
            daemon=True,
        ).start()
        threading.Thread(
            target=m.serve, args=(m.make_proxy(GW_PORT, WB_SENT), GW_PROXY), daemon=True
        ).start()
        config = uvicorn.Config(
            m.eugene_app(), host="127.0.0.1", port=EUGENE_PORT, log_level="error"
        )
        server = uvicorn.Server(config)
        threading.Thread(target=server.run, daemon=True).start()
        cpu = {**base, "CUDA_VISIBLE_DEVICES": "-1", "GGML_VK_VISIBLE_DEVICES": ""}
        start(
            "llama",
            [str(m.LLAMA), "-m", str(m.MODEL_FILE), "--host", "127.0.0.1", "--port", str(ENGINE),
             "-ngl", "0", "-t", "4", "-c", "8192", "-np", "1", "-b", "512", "-ub", "512"],
            cpu,
        )  # fmt: skip
        start(
            "embed",
            [str(m.LLAMA), "-m", str(EMBED_FILE), "--host", "127.0.0.1", "--port", str(EMBED),
             "--embeddings", "-ngl", "0", "-t", "4", "-c", "4096", "-np", "4",
             "-b", "2048", "-ub", "2048"],
            cpu,
        )  # fmt: skip

        def driver(name: str, port: int, upstream: int, model: str, locality: str) -> None:
            (work / f"{name}.yaml").write_text(
                f"provider: openai_compat_custom\nbaseUrl: http://127.0.0.1:{upstream}\n"
                f"modelId: {model}\nbackendLocality: {locality}\nrequestTimeoutSeconds: 600\n",
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

        driver("driver-local", DRV_LOCAL, ENGINE_PROXY, LOCAL, "local")
        driver("driver-outside", DRV_HOSTED, HOSTED, OUTSIDE, "external")
        driver("driver-search", DRV_EMBED, EMBED, SEARCH, "local")
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

        wanted = {LOCAL, OUTSIDE, SEARCH}
        if not check(
            f"the gateway routes {sorted(wanted)}",
            bool(until(lambda: wanted <= listed(), 300, 1.0)),
            (sorted(listed()), logs),
        ):
            return 1

        data = work / "workbench-data"
        data.mkdir()
        ids = seed_schema_22(WB, data)
        with sqlite3.connect(data / "workbench.sqlite3") as db:
            seeded = db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0]
            total_bulk = db.execute(
                "SELECT count(*) FROM messages m JOIN chats c ON c.id = m.chat_id "
                "WHERE c.owner = 'p-cy' AND m.role = 'assistant'"
            ).fetchone()[0]
        check(
            f"a schema 22 store: three people's chats, Cy's {total_bulk:,} exchanges",
            seeded == "22" and total_bulk == BULK_CHATS * BULK_TURNS,
        )
        static = work / "static"
        static.mkdir()
        (static / "index.html").write_text(
            "<!doctype html><title>Workbench</title>", encoding="utf-8"
        )
        (work / "app_key").write_text("mv2-app-key", encoding="utf-8")
        (work / "oidc_secret").write_text(m.CLIENT_SECRET, encoding="utf-8")
        wb_env = {
            **base,
            "PYTHONPATH": str(WB / "src"),
            "EUGENE_PLEXUS_APP_BIND_HOST": "127.0.0.1",
            "EUGENE_PLEXUS_APP_BIND_PORT": str(WB_PORT),
            "EUGENE_PLEXUS_APP_DATA_DIR": str(data),
            "EUGENE_PLEXUS_APP_KEY_FILE": str(work / "app_key"),
            "EUGENE_PLEXUS_APP_ADMIN_TOKEN": ADMIN_TOKEN,
            "EUGENE_PLEXUS_APP_GATEWAY_URL": f"http://127.0.0.1:{GW_PROXY}",
            "EUGENE_PLEXUS_APP_OIDC_ISSUER": f"http://127.0.0.1:{EUGENE_PORT}/oidc",
            "EUGENE_PLEXUS_APP_OIDC_CLIENT_ID": m.CLIENT_ID,
            "EUGENE_PLEXUS_APP_OIDC_SECRET_FILE": str(work / "oidc_secret"),
            "EUGENE_PLEXUS_APP_STATIC_DIR": str(static),
        }

        say("1b. the backfill: each shown exchange in its own person's index, resumed after a stop")
        started = time.perf_counter()
        start("workbench", [str(m.WB_PY), "-m", "eugene_plexus_workbench"], wb_env)
        if not check("Workbench listens", bool(until(lambda: listening(WB_PORT), 90)), logs):
            return 1
        part = until(lambda: len(exchanges(data, "p-cy")) >= 200, 120, 0.05)
        first_part = len(exchanges(data, "p-cy"))
        kill("workbench")
        stopped_at = time.perf_counter() - started
        note(f"stopped Workbench with {first_part:,} of Cy's exchanges in, at {stopped_at:.1f} s")
        check(
            "the stop came part way through the backfill",
            bool(part) and first_part < total_bulk,
            first_part,
        )
        until(lambda: not listening(WB_PORT), 20)
        log_before = (logs / "workbench.log").read_text(encoding="utf-8", errors="replace")
        start("workbench", [str(m.WB_PY), "-m", "eugene_plexus_workbench"], wb_env)
        if not check("Workbench listens again", bool(until(lambda: listening(WB_PORT), 90)), logs):
            return 1
        complete = until(lambda: len(exchanges(data, "p-cy")) == total_bulk, 300, 0.2)
        log_after = (logs / "workbench.log").read_text(encoding="utf-8", errors="replace")[
            len(log_before) :
        ]
        line = re.search(
            r"past chats in people's indexes after the restart: (\d+) exchange\(s\) put, "
            r"(\d+) removed, (\d+) person\(s\) not checked, in ([\d.]+) s",
            log_after,
        )
        put = int(line.group(1)) if line else -1
        seconds = float(line.group(4)) if line else 0.0
        check(
            "after the restart every exchange is in, and only the ones not put before were put",
            bool(complete) and line is not None and put < total_bulk + 3 and put > 0,
            (len(exchanges(data, "p-cy")), line.group(0) if line else log_after[-800:]),
        )
        if line and seconds:
            note(f"backfill rate: {put:,} exchanges in {seconds:.1f} s = {put / seconds:,.0f}/s")
        with sqlite3.connect(data / "workbench.sqlite3") as db:
            version = db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0]
        backups = sorted(p.name for p in data.glob("workbench.sqlite3.schema-22*.bak"))
        check(
            "the store is at schema 23, the schema 22 file kept beside it",
            version == "23" and backups == ["workbench.sqlite3.schema-22.bak"],
            (version, backups),
        )
        ada_held = until(
            lambda: exchanges(data, "p-ada") if len(exchanges(data, "p-ada")) == 2 else None, 30
        )
        check(
            "Ada's index holds her two shown exchanges (the trip's second version, not its first)",
            ada_held == {ids["trip_a2"]: "ada-trip", ids["bike"]: "ada-bike"},
            exchanges(data, "p-ada"),
        )
        check(
            "Bo's holds his one, and nobody's holds another's",
            exchanges(data, "p-bo") == {ids["cello"]: "bo-cello"}
            and not set(exchanges(data, "p-cy")) & {ids["trip_a2"], ids["bike"], ids["cello"]},
            exchanges(data, "p-bo"),
        )

        ada, bo, owner = Person("p-ada"), Person("p-bo"), Person("operator")
        for person in (ada, bo, owner):
            person.sign_in()
        check("Ada, Bo and the owner sign in with Eugene", all(p.secret for p in (ada, bo, owner)))

        def settings(**values: Any) -> Any:
            return httpx.patch(
                f"{WORKBENCH}/v1/config",
                json=values,
                headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
                trust_env=False,
            )

        say("2. Search chats: by words, by meaning, and the version shown")

        def search(person: Any, q: str) -> dict[str, Any]:
            return dict(person.get("/api/past-chats", params={"q": q}).json())

        def meaning_done() -> bool:
            status = ada.get("/api/past-chats/status").json()
            return bool(status.get("exchanges")) and status.get("owed") == 0

        check(
            "meaning is added to her past chats by the search model on this machine",
            bool(until(meaning_done, 300, 1.0)),
            ada.get("/api/past-chats/status").json(),
        )
        words = search(ada, "custard tarts")
        check(
            "a search by words finds the trip, at the shown answer, read from the store",
            [r["messageId"] for r in words["results"]][:1] == [ids["trip_a2"]]
            and "Belem" in words["results"][0]["answer"],
            words,
        )
        by_meaning = search(ada, "pastry shop recommendations")
        trip_hit = next((r for r in by_meaning["results"] if r["chatId"] == "ada-trip"), None)
        check(
            "a search sharing no word with it finds it by meaning",
            by_meaning["how"] == "meaning and words"
            and trip_hit is not None
            and trip_hit["via"] in ("meaning", "both"),
            by_meaning,
        )
        chose = ada.post(f"/api/chats/ada-trip/messages/{ids['trip_a1']}/choose")
        swapped = until(
            lambda: (
                (r := search(ada, "Manteigaria"))["results"]
                and r["results"][0]["messageId"] == ids["trip_a1"]
            ),
            30,
        )
        check(
            "choosing the first version puts its exchange in and takes the second out",
            chose.status_code == 200
            and bool(swapped)
            and all(
                r["messageId"] != ids["trip_a2"] for r in search(ada, "Belem monastery")["results"]
            )
            and ids["trip_a2"] not in exchanges(data, "p-ada"),
            exchanges(data, "p-ada"),
        )
        detail = ada.get("/api/chats/ada-trip").json()
        check(
            "the exchange a result names is in the chat as it is shown",
            ids["trip_a1"] in [x["id"] for x in detail["messages"]],
        )

        say("3. Use past chats: off by default, then the matching excerpt reaches the engine")
        plain = ada.new_chat(LOCAL, temperature=0, maxTokens=120)
        count = len(WB_SENT)
        ada.say(plain, "Which folding bicycle do I own?")
        ada.wait_answer(plain)
        body = sent_since(count)
        check(
            "a chat that did not turn it on is sent nothing from her other chats",
            "<past-chats-" not in json.dumps(body) and "Brompton" not in json.dumps(body),
        )
        drawer = ada.get(f"/api/chats/{plain}/memory").json()["pastChats"]
        check("its drawer says Use past chats is off", drawer["on"] is False, drawer)
        used = ada.new_chat(LOCAL, temperature=0, maxTokens=120, pastChats=True)
        count, engine_count = len(WB_SENT), len(ENGINE_BODIES)
        ada.say(used, "Which folding bicycle do I own?")
        answer = ada.wait_answer(used)
        body = sent_since(count)
        engine = m.newest(ENGINE_BODIES, engine_count, "the engine")
        last = latest_user(body)
        check(
            "with it on, her message carries the bicycle exchange as data, before her words",
            last.startswith("<past-chats-")
            and "Brompton C Line" in last
            and last.endswith("Which folding bicycle do I own?"),
            last[:400],
        )
        check(
            "and that is what reached the engine",
            "Brompton C Line" in latest_user(engine) and "<past-chats-" in latest_user(engine),
        )
        record = (answer.get("memoryUsed") or {}).get("pastChats") or {}
        check(
            "the answer records which exchanges went, by chat (the bicycle chat among them, never "
            "this one), and none of their words",
            "ada-bike" in [x["chatId"] for x in record.get("exchanges") or []]
            and all(x["chatId"] != used for x in record.get("exchanges") or [])
            and "Brompton" not in json.dumps(record),
            record,
        )
        note(f"the model answered: {answer.get('content', '')[:160]!r}")
        count = len(WB_SENT)
        ada.say(used, "And remind me about custard tarts in Lisbon, and my folding bicycle.")
        ada.wait_answer(used)
        body = sent_since(count)
        last = latest_user(body)
        again = ada.wait_answer(used)
        went = [
            x["chatId"]
            for x in ((again.get("memoryUsed") or {}).get("pastChats") or {}).get("exchanges") or []
        ]
        check(
            "the next turn is chosen again for what it asks (the trip now), never this chat's own",
            "Manteigaria" in last and "ada-trip" in went and used not in went,
            (went, last[:300]),
        )

        say("4. search_chats from a model outside her machines, and MV2-C1")
        HOSTED_TOOL["query"] = "custard tarts"
        outside = ada.new_chat(OUTSIDE, memoryExternal=True)
        hosted_before = len(HOSTED_BODIES)
        ada.say(outside, "What did I ask about pastries before?")
        ada.wait_answer(outside)
        with LOCK:
            hosted = HOSTED_BODIES[hosted_before:]
        first_req = hosted[0] if hosted else {}
        tool_msgs = [x["content"] for b in hosted for x in b["messages"] if x["role"] == "tool"]
        check(
            "the outside model, sent notes for this chat, is offered search_chats",
            "search_chats" in tool_names(first_req),
            tool_names(first_req),
        )
        check(
            "Workbench ran it and sent the excerpt back as data",
            any(t.startswith("<past-chats-") and "Manteigaria" in t for t in tool_msgs),
            tool_msgs[:1],
        )
        HOSTED_TOOL["query"] = None
        ada.call("PUT", "/api/documents/prefs", json={"external": "never"})
        hosted_before = len(HOSTED_BODIES)
        ada.say(outside, "Anything else?")
        ada.wait_answer(outside)
        with LOCK:
            later = HOSTED_BODIES[hosted_before:]
        sent = json.dumps(later)
        check(
            "with documents set never to leave, search_chats is not offered and the kept result "
            "and its query are withheld",
            later
            and "search_chats" not in tool_names(later[-1])
            and WITHHELD in sent
            and "Manteigaria" not in sent
            and "custard" not in sent.lower(),
            sent[:600],
        )
        drawer = ada.get(f"/api/chats/{outside}/memory").json()["pastChats"]
        check(
            "the drawer says why",
            drawer["allowed"] is False and drawer["reasonCode"] == "documents_never",
            drawer,
        )
        ada.call("PUT", "/api/documents/prefs", json={"external": None})

        say("5. the owner and another person")
        settings(ownerReadsChats=True)
        try:
            mine = owner.get("/api/past-chats", params={"q": "bicycle"}).json()
            read = owner.get(f"/api/chats/{outside}")
            read_used = owner.get(f"/api/chats/{used}")
            check(
                "the owner, who may read her chats, searches none of them",
                mine["results"] == [],
                mine,
            )
            # The chat's own words are hers to let the owner read; what past chats gave
            # it (a turn's record, a kept search_chats call) is not.
            private = json.dumps(
                [
                    {"used": x.get("memoryUsed"), "rounds": x.get("toolRounds")}
                    for r in (read, read_used)
                    for x in r.json().get("messages") or []
                ]
            )
            leaked = [
                w for w in ("Manteigaria", "custard", "Brompton", "racing green") if w in private
            ]
            records = [
                (x.get("memoryUsed") or {}).get("pastChats")
                for x in read_used.json().get("messages") or []
                if (x.get("memoryUsed") or {}).get("pastChats") is not None
            ]
            check(
                "and reads her chats with no excerpt, query or search result of past chats: "
                "counts only",
                read.status_code == 200
                and read_used.status_code == 200
                and not leaked
                and records
                and all(set(r) == {"exchanges", "redacted"} for r in records)
                and "Hidden: the person's past chats." in read.text,
                (leaked, records),
            )
        finally:
            settings(ownerReadsChats=None)
        check(
            "Bo finds nothing of hers, and his own",
            search(bo, "bicycle")["results"] == []
            and [r["chatId"] for r in search(bo, "cello teacher")["results"]] == ["bo-cello"],
        )

        say("6. a connection searches past chats only with the right")
        no_right = ada.post(
            "/api/connections",
            json={
                "name": "Laptop",
                "rights": {"notes": True, "documents": False, "suggest": False},
            },
        ).json()
        with_right = ada.post(
            "/api/connections",
            json={
                "name": "Laptop 2",
                "rights": {"notes": False, "documents": False, "suggest": False, "chats": True},
            },
        ).json()
        tools_without = mcp_tools(no_right["token"])
        refused = mcp_call(no_right["token"], "search_chats", query="bicycle")
        found = mcp_call(with_right["token"], "search_chats", query="folding bicycle")
        got = (found.structured_content or {}).get("exchanges") or []
        check(
            "without the right it is not listed and is refused; with it, her exchange comes back",
            "search_chats" not in tools_without
            and refused.is_error
            and not found.is_error
            and any(x["chatId"] == "ada-bike" and "Brompton" in x["answer"] for x in got),
            (tools_without, got[:1]),
        )

        say("7. Delete: the trip chat goes, and every copy of it")
        gone = ada.delete("/api/chats/ada-trip")
        held = exchanges(data, "p-ada")
        check(
            "no exchange, word or signature of it is left in her index",
            gone.status_code == 204
            and "ada-trip" not in held.values()
            and matches(data, "p-ada", "Manteigaria") == 0
            and matches(data, "p-ada", "Belem") == 0
            and not signature_kept(data, "p-ada", "ada-trip"),
            held,
        )
        outside_view = ada.get(f"/api/chats/{outside}").text
        used_view = ada.get(f"/api/chats/{used}").json()
        last_answer = [x for x in used_view["messages"] if x["role"] == "assistant"][-1]
        trip_record = (last_answer.get("memoryUsed") or {}).get("pastChats") or {}
        check(
            "the kept search_chats result is replaced, and the answer that used it keeps no "
            "trace of it but a count",
            FORGOTTEN_CHAT in outside_view
            and "Manteigaria" not in outside_view
            and all(x["chatId"] != "ada-trip" for x in trip_record.get("exchanges") or [])
            and trip_record.get("forgotten") == 1,
            trip_record,
        )
        fresh = ada.new_chat(LOCAL, temperature=0, maxTokens=60, pastChats=True)
        count = len(WB_SENT)
        ada.say(fresh, "Where can I find the best custard tarts in Lisbon?")
        ada.wait_answer(fresh)
        body = json.dumps(sent_since(count))
        later = search(ada, "custard tarts")["results"]
        check(
            "a fresh chat with Use past chats on is not sent it, and Search chats no longer "
            "finds it (only her other chats that asked about it)",
            'chat=\\"Lisbon trip\\"' not in body
            and "Lisbon trip" not in body
            and all(r["chatId"] != "ada-trip" for r in later),
            ([r["chatId"] for r in later], body[-600:]),
        )

    except AssertionError as exc:
        check(f"the run went to plan ({exc})", False)
    finally:
        say("8. teardown by pid")
        if server is not None:
            server.should_exit = True
        for name in list(procs):
            kill(name)
        time.sleep(2)
        still = [p for p in OWNED if listening(p) and p not in OURS]
        check("no owned process port is still listening", not still, still)
        print(f"\nlogs: {logs}\n{m.PASSES} passed, {m.FAILURES} failed", flush=True)
    return 1 if m.FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
