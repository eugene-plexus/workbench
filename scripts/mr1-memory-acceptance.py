"""Memory (MR1, and memory v2's MV1), live: a person's notes reach a real model, and nobody
else's do.

The unit checks drive Workbench against a fake gateway. What only a live run
shows is the whole path a note takes: a real Workbench process, a real
gateway, a real inference-driver, a real `llama-server` answering from a real
small model. Two recording proxies keep what each hop was sent: what
Workbench sent the gateway (the request the drawer must describe exactly) and
what reached the engine, with the engine's own count of what it read and what it
already held. The people are real sign-ins against a stand-in for Eugene's sign-in
(OpenID Connect, as the unit checks use).

  0. isolated: every ambient EUGENE_PLEXUS_* variable dropped, owned ports free
  1. the stack: engine (CPU, Qwen3 0.6B), a driver that declares it local, a
     stub hosted API behind a driver that declares it external, a gateway over
     both, Workbench over the gateway, **starting on a schema 21 store** with two
     people's notes and a chat's frozen copy; two people and the owner sign in
 1b. the store came through: schema 22, the schema 21 file kept beside it, every
     note each person's own, the frozen copy retaken, the notes in each person's
     own index
  2. a note by command and by API; the very first request carries it in the
     person's message as data (MV1), not in the system message
  3. a new chat: ONE system message with the chat's instructions and the pinned
     notes only; the turn's notes in the latest message; the model answers from
     them; a second person's chat carries none
  4. the drawer lists exactly the pinned text Workbench sent, and the turn's notes
  5. the next turn: the pinned block byte-identical, the turn's notes chosen again
     for what it asks, an earlier turn replayed without its block, the engine's
     cache holding the system message (turn two's prefill much smaller); a new
     note is chosen at once, a new pinned one waits for Refresh
  6. the chat's switch takes every note out (block, turn, tool), and back
  7. a model on an outside account: nothing, until "Send for this chat"; then
     `search_memory`, which Workbench runs; taken back, the kept result is withheld
  8. a long note with tags: export, the tag list as CSV into another person's list,
     Replace my list asks first, a turn gets the passage that answers, a new version
     leaves the old one as history and out of the index
  9. Delete: no row, index entry (store or knowledge index), log text, tag link or
     chat copy of it is left, and no later request carries it
 10. the second person sees nothing of the first's notes, on any route, and the
     owner, where they can read a person's chats, reads counts and no note
 11. teardown by pid; no owned port still listening

**Safe beside a live install**: every ambient EUGENE_PLEXUS_* variable is
dropped, ports are +350, teardown is by pid. Never `pkill -f eugene_plexus_`:
this box is a worker node.

Run with the Workbench interpreter (it has httpx, fastapi, uvicorn, joserfc):
  workbench/.venv/Scripts/python.exe workbench/scripts/mr1-memory-acceptance.py
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import http.client
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from joserfc import jwt
from joserfc.jwk import RSAKey

ROOT = Path(os.environ.get("EP_ROOT", "D:/py/eugene-plexus"))
HERE = Path(__file__).resolve().parent.parent
#: The Workbench tree under test: the one this script sits in, else the main one.
WB = Path(
    os.environ.get(
        "EP_WORKBENCH",
        str(HERE if (HERE / "src" / "eugene_plexus_workbench").is_dir() else ROOT / "workbench"),
    )
)
WB_PY = Path(
    os.environ.get("EP_WORKBENCH_PY", str(ROOT / "workbench" / ".venv" / "Scripts" / "python.exe"))
)
GW_PY = ROOT / "gateway" / ".venv" / "Scripts" / "python.exe"
DRV_PY = ROOT / "inference-driver" / ".venv" / "Scripts" / "python.exe"
TEMP = Path(os.environ.get("TEMP", tempfile.gettempdir()))
LLAMA = Path(
    os.environ.get(
        "EP_LLAMA_SERVER",
        str(
            Path.home() / ".eugene-plexus" / "engines" / "llama_cpp" / "b10948" / "llama-server.exe"
        ),
    )
)
MODEL_FILE = Path(
    os.environ.get("EP_MODEL_FILE", str(TEMP / "ep-s9-model" / "Qwen3-0.6B-Q4_K_M.gguf"))
)

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
) = range(8440, 8450)
OWNED = [
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
]
#: Ports a thread of this script serves; the rest belong to child processes.
OURS = (AGENT_PORT, ENGINE_PROXY, HOSTED, EUGENE_PORT, GW_PROXY)
WORKBENCH = f"http://127.0.0.1:{WB_PORT}"
LOCAL, OUTSIDE = "mr1-local", "mr1-outside"

CLIENT_ID, CLIENT_SECRET, ADMIN_TOKEN = "mr1-client", "mr1-client-secret", "mr1-admin-token"
CODE_WORD = "ZEBRAFISH-4417"
STEM = CODE_WORD.split("-")[0]

FAILURES = 0
PASSES = 0


def say(text: str) -> None:
    print(f"\n== {text}", flush=True)


def check(claim: str, passed: bool, detail: object = "") -> bool:
    global FAILURES, PASSES
    if passed:
        PASSES += 1
    else:
        FAILURES += 1
    shown = f"  ({str(detail)[:300]})" if detail != "" and not passed else ""
    print(f"  {'PASS' if passed else 'FAIL'}  {claim}{shown}", flush=True)
    return passed


def note(text: str) -> None:
    print(f"  NOTE  {text}", flush=True)


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


# --------------------------------------------------------------------------- #
# What each hop was sent
# --------------------------------------------------------------------------- #

#: Every chat request Workbench sent the gateway; every one the engine received;
#: every one the hosted API received.
WB_SENT: list[dict[str, Any]] = []
ENGINE_BODIES: list[dict[str, Any]] = []
ENGINE_TIMINGS: list[dict[str, Any] | None] = []
HOSTED_BODIES: list[dict[str, Any]] = []
LOCK = threading.Lock()


class Quiet(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A probe that hangs up early is not news.
        pass


def timings_of(stream: bytes) -> dict[str, Any] | None:
    """The last `timings` object in a streamed answer: how many tokens the engine
    already held (`cache_n`) and how many it had to read (`prompt_n`)."""
    found = None
    for line in stream.decode("utf-8", errors="replace").splitlines():
        if line.startswith("data:") and '"timings"' in line:
            with contextlib.suppress(ValueError):
                found = json.loads(line[5:]).get("timings") or found
    return found


def make_proxy(
    upstream: int, sink: list[dict[str, Any]], timings: list[dict[str, Any] | None] | None = None
) -> type[BaseHTTPRequestHandler]:
    """A pass-through to `127.0.0.1:upstream` that keeps the body of every chat
    request in `sink`: exactly what the next hop was sent. With `timings`, it
    also keeps the engine's account of that request, once it has been answered."""

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _forward(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            chat = self.command == "POST" and self.path.split("?")[0] == "/v1/chat/completions"
            place = 0
            if chat:
                with LOCK:
                    sink.append(json.loads(body))
                    if timings is not None:
                        timings.append(None)
                    place = len(sink) - 1
            hop = http.client.HTTPConnection("127.0.0.1", upstream, timeout=900)
            wanted = ("content-type", "accept", "authorization", "x-eugene-repetition-mode")
            headers = {k: v for k, v in self.headers.items() if k.lower() in wanted}
            hop.request(self.command, self.path, body=body or None, headers=headers)
            answer = hop.getresponse()
            self.send_response(answer.status)
            for key in ("Content-Type", "Cache-Control"):
                if answer.getheader(key):
                    self.send_header(key, answer.getheader(key))
            self.end_headers()  # HTTP/1.0: the body ends when the connection does
            kept = bytearray()
            while chunk := answer.read(4096):
                if chat and timings is not None and len(kept) < 8_000_000:
                    kept += chunk
                self.wfile.write(chunk)
                self.wfile.flush()
            hop.close()
            if chat and timings is not None:
                with LOCK:
                    timings[place] = timings_of(bytes(kept))

        do_GET = _forward
        do_POST = _forward
        do_PATCH = _forward
        do_DELETE = _forward

    return Proxy


#: What the stub hosted API does with a request offering `search_memory`: when `query` is
#: set, it calls the tool once with it (as a model that calls tools would), then answers.
HOSTED_TOOL: dict[str, str | None] = {"query": None}


class Hosted(BaseHTTPRequestHandler):
    """A stub for a hosted API: one model, answering a fixed sentence."""

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
            return self._json({"data": [{"id": OUTSIDE, "object": "model"}]})
        return self._json({"error": {"message": "not found"}}, status=404)

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.split("?")[0] != "/v1/chat/completions":
            return self._json({"error": {"message": "not found"}}, status=404)
        with LOCK:
            HOSTED_BODIES.append(body)
        if not body.get("stream"):
            return self._json(
                {"choices": [{"index": 0, "message": {"content": "ok"}, "finish_reason": "stop"}]}
            )
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        messages = body.get("messages") or []
        last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=0)
        answered = any(m.get("role") == "tool" for m in messages[last_user:])
        offered = any(
            (t.get("function") or {}).get("name") == "search_memory"
            for t in body.get("tools") or []
        )
        if HOSTED_TOOL["query"] and offered and not answered:
            call = {
                "index": 0,
                "id": "call-memory-1",
                "type": "function",
                "function": {
                    "name": "search_memory",
                    "arguments": json.dumps({"query": HOSTED_TOOL["query"]}),
                },
            }
            frame = {
                "choices": [{"index": 0, "delta": {"tool_calls": [call]}, "finish_reason": None}]
            }
            self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
            done = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
            self.wfile.write(b"data: " + json.dumps(done).encode() + b"\n\ndata: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True
            return
        for piece in ({"content": "Hosted "}, {"content": "answer"}):
            frame = {"choices": [{"index": 0, "delta": piece, "finish_reason": None}]}
            self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
        done = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        self.wfile.write(b"data: " + json.dumps(done).encode() + b"\n\ndata: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


class Agent(BaseHTTPRequestHandler):
    """Topology only: the gateway asks its agent which drivers there are."""

    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/v1/components":
            body: Any = {
                "components": [
                    {
                        "name": name,
                        "kind": "inference-driver",
                        "url": f"http://127.0.0.1:{port}/",
                        "status": "running",
                    }
                    for name, port in (
                        ("mr1-local-driver", DRV_LOCAL),
                        ("mr1-outside-driver", DRV_HOSTED),
                    )
                ]
            }
        elif path == "/v1/runtimes":
            body = {"runtimes": []}
        elif path == "/v1/node":
            body = {"name": "mr1-node", "enrolled": False}
        else:
            body = {"status": "ok"}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def serve(handler: type[BaseHTTPRequestHandler], port: int) -> None:
    Quiet(("127.0.0.1", port), handler).serve_forever()


# --------------------------------------------------------------------------- #
# A stand-in for Eugene's sign-in (OpenID Connect, as the unit checks use)
# --------------------------------------------------------------------------- #

PEOPLE = {
    "operator": {"name": "Owner", "role": "operator", "username": "operator"},
    "p-ada": {"name": "Ada", "role": "member", "username": "ada"},
    "p-bo": {"name": "Bo", "role": "member", "username": "bo"},
}


def eugene_app() -> Any:
    key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    issuer = f"http://127.0.0.1:{EUGENE_PORT}/oidc"
    codes: dict[str, dict[str, Any]] = {}
    app = FastAPI()

    def id_token(sub: str, nonce: str | None) -> str:
        now = int(time.time())
        person = PEOPLE[sub]
        claims: dict[str, Any] = {
            "iss": issuer,
            "aud": CLIENT_ID,
            "sub": sub,
            "iat": now,
            "exp": now + 600,
            "name": person["name"],
            "preferred_username": person["username"],
            "eugene_role": person["role"],
            "auth_time": now,
        }
        if nonce is not None:
            claims["nonce"] = nonce
        return jwt.encode({"alg": "RS256", "typ": "JWT", "kid": "k1"}, claims, key)

    @app.get("/oidc/.well-known/openid-configuration")
    async def discovery() -> Any:
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": f"{issuer}/jwks",
            "revocation_endpoint": f"{issuer}/revoke",
        }

    @app.get("/oidc/jwks")
    async def jwks() -> Any:
        return {"keys": [key.as_dict(private=False)]}

    @app.get("/oidc/authorize")
    async def authorize(request: Request) -> Any:
        q = request.query_params
        code = secrets.token_urlsafe(16)
        codes[code] = {
            "sub": q["login_as"],
            "nonce": q["nonce"],
            "challenge": q["code_challenge"],
            "redirect_uri": q["redirect_uri"],
        }
        target = (
            q["redirect_uri"] + "?" + urlencode({"code": code, "state": q["state"], "iss": issuer})
        )
        return RedirectResponse(target, status_code=302)

    @app.post("/oidc/token")
    async def token(request: Request) -> Any:
        auth = request.headers.get("authorization", "")
        expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if auth != f"Basic {expected}":
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        form = await request.form()
        if form["grant_type"] == "authorization_code":
            found = codes.pop(str(form["code"]), None)
            verifier = str(form.get("code_verifier", ""))
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if found is None or found["challenge"] != challenge:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return {
                "access_token": "at",
                "token_type": "Bearer",
                "expires_in": 3600,
                "id_token": id_token(found["sub"], found["nonce"]),
                "refresh_token": secrets.token_urlsafe(16),
            }
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    @app.post("/oidc/revoke")
    async def revoke() -> Any:
        return {}

    return app


# --------------------------------------------------------------------------- #
# A person's browser
# --------------------------------------------------------------------------- #


class Person:
    """One person's browser: a cookie jar, the secret the page keeps, an origin."""

    def __init__(self, sub: str) -> None:
        self.sub = sub
        self.http = httpx.Client(base_url=WORKBENCH, timeout=60, trust_env=False)
        self.secret: str | None = None

    def sign_in(self) -> None:
        start = self.http.get("/signin")
        assert start.status_code == 302, start.text
        at_eugene = httpx.get(start.headers["location"] + "&login_as=" + self.sub, trust_env=False)
        assert at_eugene.status_code == 302, at_eugene.text
        back = urlsplit(at_eugene.headers["location"])
        done = self.http.get(back.path + "?" + back.query)
        location = done.headers.get("location", "")
        assert "#signin=" in location, f"sign-in failed: {location}"
        self.secret = location.split("#signin=", 1)[1]

    def call(self, method: str, path: str, **kw: Any) -> Any:
        headers = {"Origin": WORKBENCH, "X-Workbench-Secret": self.secret or ""}
        return self.http.request(method, path, headers=headers, **kw)

    def get(self, path: str, **kw: Any) -> Any:
        return self.call("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.call("POST", path, **kw)

    def patch(self, path: str, **kw: Any) -> Any:
        return self.call("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.call("DELETE", path, **kw)

    def new_chat(self, model: str, **settings: Any) -> str:
        chat = self.post("/api/chats", json={"model": model}).json()["id"]
        if settings:
            changed = self.patch(f"/api/chats/{chat}", json={"settings": settings})
            assert changed.status_code == 200, changed.text
        return str(chat)

    def wait_answer(self, chat: str, seconds: float = 300) -> dict[str, Any]:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            messages = self.get(f"/api/chats/{chat}").json()["messages"]
            if messages and messages[-1]["status"] != "running":
                return dict(messages[-1])
            time.sleep(0.5)
        raise AssertionError("the answer did not finish")

    def say(self, chat: str, content: str, **body: Any) -> Any:
        sent = self.post(f"/api/chats/{chat}/messages", json={"content": content, **body})
        assert sent.status_code == 201, sent.text
        return sent.json()


def system_of(body: dict[str, Any]) -> str | None:
    found = [m["content"] for m in body["messages"] if m["role"] == "system"]
    assert len(found) <= 1, "a second system message"
    return str(found[0]) if found else None


def newest(sink: list[dict[str, Any]], count: int, what: str) -> dict[str, Any]:
    """The newest request in `sink`, once it holds more than `count`."""
    end = time.time() + 60
    while time.time() < end:
        with LOCK:
            if len(sink) > count:
                return sink[-1]
        time.sleep(0.2)
    raise AssertionError(f"{what} was sent no request")


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


def seed_schema_21(data: Path) -> None:
    """A store as schema 21 left it, before Workbench starts: two people's notes, one of them
    pinned, and a chat holding a frozen copy of its notes (the v1 shape). Made with this
    Workbench's own store and then put back into schema 21's shape, as the unit check does."""
    sys.path.insert(0, str(WB / "src"))
    from eugene_plexus_workbench.store import Chat, MemoryNote, Store

    async def make() -> None:
        store = Store(data / "workbench.sqlite3")
        await store.open()
        try:
            for owner, ident, text, kind, pinned, at in SEEDED:
                await store.add_memory_note(
                    MemoryNote(
                        id=ident,
                        owner=owner,
                        text=text,
                        kind=kind,
                        pinned=pinned,
                        created_at=at,
                        updated_at=at,
                    ),
                    limit=300,
                )
            await store.create_chat(
                Chat(
                    id=SEEDED_CHAT,
                    owner="p-ada",
                    title="Before",
                    model=LOCAL,
                    created_at=1.0,
                    updated_at=1.0,
                )
            )
        finally:
            await store.close()

    asyncio.run(make())
    with sqlite3.connect(data / "workbench.sqlite3") as db:
        for trigger in ("memory_notes_ai", "memory_notes_ad", "memory_notes_au"):
            db.execute(f"DROP TRIGGER {trigger}")
        db.execute("DROP TABLE memory_notes_fts")
        db.execute("ALTER TABLE memory_notes RENAME TO kept")
        db.execute("DROP TABLE memory_note_tags")
        db.execute("DROP TABLE memory_tags")
        db.execute(V21_NOTES)
        db.execute(
            "INSERT INTO memory_notes (id, owner, text, kind, status, pinned, source, created_at, "
            "updated_at) SELECT id, owner, text, kind, status, pinned, source, created_at, "
            "updated_at FROM kept"
        )
        db.execute("DROP TABLE kept")
        db.execute("CREATE INDEX memory_notes_by_owner ON memory_notes (owner, status)")
        db.execute(
            "CREATE VIRTUAL TABLE memory_notes_fts USING fts5(text, content='memory_notes', "
            "tokenize='porter unicode61 remove_diacritics 2')"
        )
        db.execute("INSERT INTO memory_notes_fts (memory_notes_fts) VALUES ('rebuild')")
        for column in ("relevant_budget_tokens", "search_tool"):
            db.execute(f"ALTER TABLE memory_prefs DROP COLUMN {column}")
        db.execute("ALTER TABLE messages DROP COLUMN memory_used")
        db.execute(
            "UPDATE chats SET memory_snapshot = ? WHERE id = ?",
            (
                json.dumps({"text": "What you know about this person: - Grows tomatoes"}),
                SEEDED_CHAT,
            ),
        )
        db.execute("UPDATE meta SET value = '21' WHERE key = 'schema'")


#: `memory_notes` as schema 21 made it.
V21_NOTES = (
    "CREATE TABLE memory_notes ("
    "id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
    "text TEXT NOT NULL CHECK (length(text) <= 240), "
    "kind TEXT NOT NULL CHECK (kind IN ('fact','preference','instruction','project','event')), "
    "status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','pending','dismissed')), "
    "pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)), "
    "source TEXT NOT NULL CHECK (source IN ('typed','said','button','suggested','imported')), "
    "source_chat_id TEXT, source_message_id TEXT, quote TEXT, "
    "expires_at TEXT, revisit_at TEXT, "
    "created_at REAL NOT NULL, updated_at REAL NOT NULL, last_used_at REAL, "
    "use_count INTEGER NOT NULL DEFAULT 0, replaces TEXT, source_connection_id TEXT, "
    "source_connection TEXT)"
)
SEEDED = [
    ("p-ada", "seed-a1", "Grows tomatoes on the balcony", "fact", False, 1.0),
    ("p-ada", "seed-a2", "Prefers metric units", "preference", True, 2.0),
    ("p-bo", "seed-b1", "Plays the cello", "fact", False, 3.0),
]
SEEDED_CHAT = "seed-chat-ada"


def users_of(body: dict[str, Any]) -> list[str]:
    """The text of each user message, whatever its shape."""
    out: list[str] = []
    for m in body["messages"]:
        if m["role"] != "user":
            continue
        content = m["content"]
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        out.append(str(content))
    return out


def latest_user(body: dict[str, Any]) -> str:
    return users_of(body)[-1]


def tool_names(body: dict[str, Any]) -> list[str]:
    return [t["function"]["name"] for t in body.get("tools") or []]


def index_notes(data: Path, sub: str) -> dict[str, list[str]]:
    """The person's notes as their knowledge index holds them: id -> passages."""
    sys.path.insert(0, str(WB / "src"))
    from eugene_plexus_workbench import files

    path = data / "knowledge" / files.person_key(sub) / "index.sqlite3"
    if not path.is_file():
        return {}
    db = sqlite3.connect(path)
    try:
        found: dict[str, list[str]] = {}
        for doc_id, text in db.execute(
            "SELECT d.id, c.text FROM documents d JOIN chunks c ON c.doc_id = d.id "
            "WHERE d.scope = 'notes' ORDER BY d.id, c.ord"
        ):
            found.setdefault(doc_id, []).append(text)
        return found
    finally:
        db.close()


def index_matches(data: Path, sub: str, word: str) -> int:
    sys.path.insert(0, str(WB / "src"))
    from eugene_plexus_workbench import files

    path = data / "knowledge" / files.person_key(sub) / "index.sqlite3"
    db = sqlite3.connect(path)
    try:
        return int(
            db.execute(
                "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (word,)
            ).fetchone()[0]
        )
    finally:
        db.close()


#: A chat's own instructions long enough that the pinned system message is most of a turn's
#: prompt, so what the engine's cache holds from one turn to the next is plain in its timings.
LONG_INSTRUCTIONS = "You are Ada's assistant. " + " ".join(
    f"Rule {i}: keep answers short, plain and correct, and say so when you do not know."
    for i in range(1, 70)
)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    procs: list[subprocess.Popen[bytes]] = []
    work = Path(tempfile.mkdtemp(prefix="ep-mr1-"))
    logs = work / "logs"
    logs.mkdir()

    def start(argv: list[str], log: str, env: dict[str, str]) -> None:
        # The child owns the handle for as long as it runs; teardown kills it.
        handle = open(logs / log, "w", encoding="utf-8")  # noqa: SIM115
        procs.append(
            subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, env=env
            )
        )

    def wait_for(port: int, seconds: float = 120) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            if listening(port):
                return True
            time.sleep(0.5)
        return False

    def sent(count: int) -> dict[str, Any]:
        return newest(WB_SENT, count, "the gateway")

    def reached(count: int) -> dict[str, Any]:
        return newest(ENGINE_BODIES, count, "the engine")

    def counts() -> tuple[int, int]:
        with LOCK:
            return len(WB_SENT), len(ENGINE_BODIES)

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
        for path in (
            WB_PY,
            GW_PY,
            DRV_PY,
            LLAMA,
            MODEL_FILE,
            WB / "src" / "eugene_plexus_workbench",
        ):
            if not path.exists():
                check(f"{path} exists", False)
                return 1
        check(
            f"ports {OWNED[0]}-{OWNED[-1]} free; Workbench from {WB}; llama-server "
            f"{LLAMA.parent.name}; {MODEL_FILE.name}",
            True,
        )

        say("1. the stack: engine, drivers, gateway, Workbench over a schema 21 store")
        threading.Thread(target=serve, args=(Agent, AGENT_PORT), daemon=True).start()
        threading.Thread(target=serve, args=(Hosted, HOSTED), daemon=True).start()
        threading.Thread(
            target=serve,
            args=(make_proxy(ENGINE, ENGINE_BODIES, ENGINE_TIMINGS), ENGINE_PROXY),
            daemon=True,
        ).start()
        threading.Thread(
            target=serve, args=(make_proxy(GW_PORT, WB_SENT), GW_PROXY), daemon=True
        ).start()
        config = uvicorn.Config(eugene_app(), host="127.0.0.1", port=EUGENE_PORT, log_level="error")
        server = uvicorn.Server(config)
        threading.Thread(target=server.run, daemon=True).start()
        # CPU only, one slot (several would divide the window), a window of 8,192.
        engine = [str(LLAMA), "-m", str(MODEL_FILE), "--host", "127.0.0.1", "--port", str(ENGINE)]
        engine += ["-ngl", "0", "-t", "4", "-c", "8192", "-np", "1", "-b", "512", "-ub", "512"]
        start(
            engine,
            "llama.log",
            {**base, "CUDA_VISIBLE_DEVICES": "-1", "GGML_VK_VISIBLE_DEVICES": ""},
        )

        def driver(name: str, port: int, yaml: str) -> None:
            (work / f"{name}.yaml").write_text(yaml, encoding="utf-8", newline="\n")
            start(
                [str(DRV_PY), "-m", "eugene_plexus_inference_driver"],
                f"{name}.log",
                {
                    **base,
                    "EUGENE_PLEXUS_DRIVER_CONFIG_FILE": str(work / f"{name}.yaml"),
                    "EUGENE_PLEXUS_DRIVER_BIND_HOST": "127.0.0.1",
                    "EUGENE_PLEXUS_DRIVER_BIND_PORT": str(port),
                },
            )

        driver(
            "driver-local",
            DRV_LOCAL,
            f"provider: openai_compat_custom\nbaseUrl: http://127.0.0.1:{ENGINE_PROXY}\n"
            f"modelId: {LOCAL}\nbackendLocality: local\nrequestTimeoutSeconds: 600\n",
        )
        driver(
            "driver-outside",
            DRV_HOSTED,
            f"provider: openai_compat_custom\nbaseUrl: http://127.0.0.1:{HOSTED}\n"
            f"modelId: {OUTSIDE}\nbackendLocality: external\nrequestTimeoutSeconds: 120\n",
        )
        (work / "gateway.yaml").write_text("routingRefreshSeconds: 3\n", encoding="utf-8")
        start(
            [str(GW_PY), "-m", "eugene_plexus_gateway"],
            "gateway.log",
            {
                **base,
                "EUGENE_PLEXUS_GATEWAY_CONFIG_FILE": str(work / "gateway.yaml"),
                "EUGENE_PLEXUS_GATEWAY_METRICS_FILE": str(work / "metrics.sqlite3"),
                "EUGENE_PLEXUS_GATEWAY_BIND_HOST": "127.0.0.1",
                "EUGENE_PLEXUS_GATEWAY_BIND_PORT": str(GW_PORT),
                "EUGENE_PLEXUS_GATEWAY_AGENT_URL": f"http://127.0.0.1:{AGENT_PORT}",
            },
        )
        for port in (ENGINE, DRV_LOCAL, DRV_HOSTED, GW_PORT, EUGENE_PORT):
            if not wait_for(port, 180):
                check(f"something listens on {port}; logs in {logs}", False)
                return 1
        wanted = {LOCAL, OUTSIDE}
        ids: set[str] = set()
        end = time.time() + 120
        while time.time() < end and not wanted <= ids:
            try:
                listed = httpx.get(
                    f"http://127.0.0.1:{GW_PORT}/v1/models", timeout=5, trust_env=False
                ).json()
                ids = {m["id"] for m in listed.get("data") or []}
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(1)
        if not check(f"the gateway routes {sorted(wanted)}", wanted <= ids, sorted(ids)):
            return 1

        data = work / "workbench-data"
        data.mkdir()
        seed_schema_21(data)
        with sqlite3.connect(data / "workbench.sqlite3") as db:
            seeded_at = db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0]
        check("a schema 21 store with two people's notes and a frozen chat", seeded_at == "21")
        static = work / "static"
        static.mkdir()
        (static / "index.html").write_text(
            "<!doctype html><title>Workbench</title>", encoding="utf-8"
        )
        (work / "app_key").write_text("mr1-app-key", encoding="utf-8")
        (work / "oidc_secret").write_text(CLIENT_SECRET, encoding="utf-8")
        start(
            [str(WB_PY), "-m", "eugene_plexus_workbench"],
            "workbench.log",
            {
                **base,
                "PYTHONPATH": str(WB / "src"),
                "EUGENE_PLEXUS_APP_BIND_HOST": "127.0.0.1",
                "EUGENE_PLEXUS_APP_BIND_PORT": str(WB_PORT),
                "EUGENE_PLEXUS_APP_DATA_DIR": str(data),
                "EUGENE_PLEXUS_APP_KEY_FILE": str(work / "app_key"),
                "EUGENE_PLEXUS_APP_ADMIN_TOKEN": ADMIN_TOKEN,
                # Through the recording proxy: it is what Workbench sends the gateway.
                "EUGENE_PLEXUS_APP_GATEWAY_URL": f"http://127.0.0.1:{GW_PROXY}",
                "EUGENE_PLEXUS_APP_OIDC_ISSUER": f"http://127.0.0.1:{EUGENE_PORT}/oidc",
                "EUGENE_PLEXUS_APP_OIDC_CLIENT_ID": CLIENT_ID,
                "EUGENE_PLEXUS_APP_OIDC_SECRET_FILE": str(work / "oidc_secret"),
                "EUGENE_PLEXUS_APP_STATIC_DIR": str(static),
            },
        )
        if not wait_for(WB_PORT, 60):
            check(f"Workbench listens on {WB_PORT}; logs in {logs}", False)
            return 1
        ada, bo, owner = Person("p-ada"), Person("p-bo"), Person("operator")
        for person in (ada, bo, owner):
            person.sign_in()
        check("Ada, Bo and the owner sign in with Eugene", all(p.secret for p in (ada, bo, owner)))
        listing = {m["id"]: m for m in ada.get("/api/models").json()["models"]}
        check(
            "Workbench says where each model runs, as the gateway does",
            listing[LOCAL]["locality"] == "local" and listing[OUTSIDE]["locality"] == "external",
            {k: v["locality"] for k, v in listing.items()},
        )
        window = listing[LOCAL]["contextLength"]
        note(f"{LOCAL}: window {window} tokens")
        if not check(
            "the engine's window leaves room for notes (4,096 or more)", (window or 0) >= 4096
        ):
            return 1

        def settings(**values: Any) -> Any:
            return httpx.patch(
                f"{WORKBENCH}/v1/config",
                json=values,
                headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
                trust_env=False,
            )

        say(
            "1b. the schema 21 store came through: every note, each person's own, snapshots retaken"
        )
        with sqlite3.connect(data / "workbench.sqlite3") as db:
            version = db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0]
        backups = sorted(p.name for p in data.glob("workbench.sqlite3.schema-21*.bak"))
        ada_page, bo_page = ada.get("/api/memory").json(), bo.get("/api/memory").json()
        check(
            "the store is at schema 22, and the schema 21 file was kept beside it first",
            version == "22" and backups == ["workbench.sqlite3.schema-21.bak"],
            (version, backups),
        )
        check(
            "Ada has her two notes, the pinned one always used; Bo has his one",
            [n["text"] for n in ada_page["sections"]["always"]] == ["Prefers metric units"]
            and [n["text"] for n in ada_page["sections"]["other"]]
            == ["Grows tomatoes on the balcony"]
            and [n["text"] for n in bo_page["sections"]["other"]] == ["Plays the cello"]
            and bo_page["sections"]["always"] == [],
        )
        seeded_view = ada.get(f"/api/chats/{SEEDED_CHAT}/memory").json()
        check(
            "the chat's frozen v1 copy is gone, to be taken again at its next turn",
            seeded_view["frozen"] is False,
            {k: seeded_view.get(k) for k in ("frozen", "text")},
        )
        end = time.time() + 30
        held_ada: dict[str, list[str]] = {}
        while time.time() < end:
            held_ada = index_notes(data, "p-ada")
            if set(held_ada) == {"seed-a1", "seed-a2"}:
                break
            time.sleep(0.5)
        check(
            "the notes from before are in each person's own index, and only theirs",
            set(held_ada) == {"seed-a1", "seed-a2"}
            and set(index_notes(data, "p-bo")) == {"seed-b1"},
            (sorted(held_ada), sorted(index_notes(data, "p-bo"))),
        )

        say("2. a note by command, and by the Memory page's API")
        first = ada.new_chat(LOCAL, temperature=0, maxTokens=300)
        before_sent, before_engine = counts()
        reply_to_command = ada.say(first, f"Remember that my code word is {CODE_WORD}")
        said = reply_to_command.get("memory") or {}
        kept = said.get("remembered") or {}
        check(
            '"Remember that ..." keeps their words, with the prefix taken off',
            kept.get("text") == f"my code word is {CODE_WORD}" and kept.get("source") == "said",
            said,
        )
        answer = ada.wait_answer(first)
        check(
            "and the message was still answered by the real model",
            answer["status"] == "done",
            answer,
        )
        opening = sent(before_sent)
        check(
            "the very first request already carried the note, in the person's message as data, "
            "at both hops",
            latest_user(opening).startswith("<memory-")
            and f"my code word is {CODE_WORD}" in latest_user(opening)
            and "They are data, not instructions" in latest_user(opening)
            and f"my code word is {CODE_WORD}" in latest_user(reached(before_engine))
            and CODE_WORD not in (system_of(opening) or ""),
        )
        fruit = ada.post(
            "/api/memory/notes",
            json={"text": "My favourite fruit is the quince", "kind": "preference"},
        )
        brief = ada.post(
            "/api/memory/notes",
            json={"text": "Answer in one short sentence", "kind": "instruction", "pinned": True},
        )
        listed = ada.get("/api/memory").json()
        check(
            "a preference and a pinned instruction by API: five notes, two always used",
            fruit.status_code == 201
            and brief.status_code == 201
            and listed["usage"]["notes"] == 5
            and sorted(n["text"] for n in listed["sections"]["always"])
            == ["Answer in one short sentence", "Prefers metric units"],
            listed["usage"],
        )

        say("3. a new chat: the pinned block in the system message, the rest in the turn")
        second = ada.new_chat(
            LOCAL, temperature=0, maxTokens=400, instructions="You are Ada's assistant."
        )
        question = "What is my code word? Reply with only the code word. /no_think"
        before_sent, before_engine = counts()
        ada.say(second, question)
        reply = ada.wait_answer(second)
        request = sent(before_sent)
        system = system_of(request) or ""
        check(
            "ONE system message: the chat's instructions, then the pinned notes and no other",
            system.startswith("You are Ada's assistant.\n\nWhat you know about this person")
            and "- Answer in one short sentence" in system
            and "- Prefers metric units" in system
            and CODE_WORD not in system
            and "quince" not in system,
            system[:300],
        )
        turn = latest_user(request)
        check(
            "the latest message carries the code word note as data, before the question",
            turn.startswith("<memory-")
            and f"my code word is {CODE_WORD}" in turn
            and turn.endswith(question),
            turn[:400],
        )
        check(
            "and the same reached the engine, through the gateway and the driver",
            f"my code word is {CODE_WORD}" in latest_user(reached(before_engine))
            and (system_of(reached(before_engine)) or "").endswith(system.split("\n\n", 1)[1]),
        )
        tries = 1
        while STEM not in reply["content"].upper() and tries < 3:
            tries += 1
            ada.post(f"/api/chats/{second}/retry")
            reply = ada.wait_answer(second)
        check(
            "the model answers a question only the note answers",
            STEM in reply["content"].upper(),
            f"{tries} tries: {reply['content'][:120]!r}",
        )
        note(f"answer: {reply['content'][:100]!r}")
        bo_chat = bo.new_chat(LOCAL, temperature=0, maxTokens=400)
        before_sent, before_engine = counts()
        bo.say(bo_chat, question)
        bo_reply = bo.wait_answer(bo_chat)
        bo_request = sent(before_sent)
        check(
            "asked the same, a second person's chat carries none of Ada's notes, at either hop",
            CODE_WORD not in json.dumps(bo_request)
            and "quince" not in json.dumps(bo_request)
            and CODE_WORD not in json.dumps(reached(before_engine)),
        )
        check(
            "and the model cannot say a word it was never told",
            STEM not in bo_reply["content"].upper(),
            bo_reply["content"][:120],
        )

        say("4. the drawer lists exactly what Workbench sent")
        view = ada.get(f"/api/chats/{second}/memory").json()
        check(
            "its pinned text is Workbench's system message after the instructions, to the byte",
            system == "You are Ada's assistant.\n\n" + str(view["text"]),
        )
        check(
            "it names the two pinned notes, with the pinned budget for this window",
            sorted(s["text"] for s in view["sent"])
            == ["Answer in one short sentence", "Prefers metric units"]
            and view["budget"] == min(2000, window * 4 // 100)
            and view["frozen"] is True
            and view["sending"] is True,
            {k: view[k] for k in ("budget", "tokens", "frozen", "sending", "counts")},
        )
        turn_view = view["turn"] or {}
        check(
            "and what this turn was sent: the code word note, in its relevant budget",
            any(CODE_WORD in n["text"] for n in turn_view.get("notes") or [])
            and turn_view.get("budget") == min(6000, window * 10 // 100)
            and 0 < turn_view.get("tokens", 0) <= turn_view.get("budget", 0),
            turn_view,
        )

        say("5. the next turn: the same pinned bytes, notes chosen again, the engine's cache")
        cached = ada.new_chat(LOCAL, temperature=0, maxTokens=60, instructions=LONG_INSTRUCTIONS)
        before_sent, before_engine = counts()
        ada.say(cached, "Which fruit do I like best? One word. /no_think")
        ada.wait_answer(cached)
        one, one_at = sent(before_sent), before_engine
        before_sent, before_engine = counts()
        ada.say(cached, "Do I use metric or imperial units? And what do I grow? /no_think")
        ada.wait_answer(cached)
        two, two_at = sent(before_sent), before_engine
        check(
            "the system message is the same bytes in both turns",
            system_of(one) == system_of(two) and system_of(one) is not None,
        )
        check(
            "each turn's block leads with what that turn asked about",
            latest_user(one).index("quince") < latest_user(one).index("tomatoes")
            and latest_user(two).index("tomatoes") < latest_user(two).index("quince"),
            (latest_user(one)[:300], latest_user(two)[:300]),
        )
        check(
            "an earlier turn is replayed as the person wrote it, with no block",
            users_of(two)[0] == "Which fruit do I like best? One word. /no_think",
            users_of(two)[0][:200],
        )
        with LOCK:
            t1 = ENGINE_TIMINGS[one_at] if one_at < len(ENGINE_TIMINGS) else None
            t2 = ENGINE_TIMINGS[two_at] if two_at < len(ENGINE_TIMINGS) else None
        if t1 and t2:
            note(
                f"engine, turn one: read {t1.get('prompt_n')} tokens, held {t1.get('cache_n')}; "
                f"turn two: read {t2.get('prompt_n')}, held {t2.get('cache_n')}"
            )
            check(
                "turn two's prefill is much smaller than turn one's: the pinned system message "
                "came from the engine's cache",
                t2.get("prompt_n", 10**9) < 0.5 * t1.get("prompt_n", 0)
                and t2.get("cache_n", 0) > 0.5 * t1.get("prompt_n", 0),
                (t1, t2),
            )
        else:
            check("the engine said how much of each prompt it read (timings)", False, (t1, t2))
        bees = ada.post("/api/memory/notes", json={"text": "I keep bees"}).json()["note"]
        pinned_new = ada.post(
            "/api/memory/notes", json={"text": "Uses a Linux laptop", "pinned": True}
        ).json()["note"]
        before_sent, _ = counts()
        ada.say(cached, "Tell me about my bees and my laptop. /no_think")
        ada.wait_answer(cached)
        three = sent(before_sent)
        check(
            "a note added meanwhile is chosen at once; a new pinned one waits for Refresh, the "
            "pinned bytes unchanged",
            "I keep bees" in latest_user(three)
            and system_of(three) == system_of(one)
            and "Uses a Linux laptop" not in json.dumps(three),
        )
        view = ada.get(f"/api/chats/{cached}/memory").json()
        check(
            "the drawer says the pinned notes changed, and names the new pinned one not sent",
            view["changed"] is True
            and [n["id"] for n in view["notSent"]] == [pinned_new["id"]]
            and view["notSent"][0]["reason"] == "added_since",
            {k: view.get(k) for k in ("changed", "notSent")},
        )
        refreshed = ada.post(f"/api/chats/{cached}/memory/refresh").json()
        before_sent, _ = counts()
        ada.say(cached, "Thanks. /no_think")
        ada.wait_answer(cached)
        after = system_of(sent(before_sent)) or ""
        check(
            "Refresh memory takes the pinned notes again: the next request carries the new one",
            "- Uses a Linux laptop" in after
            and after == LONG_INSTRUCTIONS + "\n\n" + str(refreshed["text"]),
        )
        del bees

        say("6. the chat's switch takes every note out, and back")
        ada.patch(f"/api/chats/{second}", json={"settings": {"memory": "off"}})
        before_sent, before_engine = counts()
        ada.say(second, "Anything about quince? /no_think")
        ada.wait_answer(second)
        off = sent(before_sent)
        check(
            "with the switch off the request holds no note, block or memory tool, at both hops",
            system_of(off) == "You are Ada's assistant."
            and "<memory-" not in json.dumps(off)
            and "quince is" not in json.dumps(off).lower()
            and "search_memory" not in tool_names(off)
            and "<memory-" not in json.dumps(reached(before_engine)),
            (system_of(off), tool_names(off)),
        )
        view = ada.get(f"/api/chats/{second}/memory").json()
        check(
            "and the drawer says why",
            view["sending"] is False and view["reasonCode"] == "chat_off" and view["text"] is None,
        )
        ada.patch(f"/api/chats/{second}", json={"settings": {"memory": "on"}})
        before_sent, _ = counts()
        ada.say(second, "Again? /no_think")
        ada.wait_answer(second)
        check(
            "switched on again, the pinned block and a turn's notes are sent again",
            system_of(sent(before_sent)) == system
            and latest_user(sent(before_sent)).startswith("<memory-"),
        )

        say("7. a model on an outside account: nothing, until the person says so; search_memory")
        outside = ada.new_chat(OUTSIDE)
        before_hosted = len(HOSTED_BODIES)
        ada.say(outside, "Hello there")
        ada.wait_answer(outside)
        held = newest(HOSTED_BODIES, before_hosted, "the hosted API")
        view = ada.get(f"/api/chats/{outside}/memory").json()
        check(
            "the hosted API was sent no system message, no note, no block and no memory tool",
            system_of(held) is None
            and "quince" not in json.dumps(held)
            and "<memory-" not in json.dumps(held)
            and "search_memory" not in tool_names(held),
            held,
        )
        check(
            "the drawer says the notes were not sent, and why, and offers Send for this chat",
            view["sending"] is False
            and view["reasonCode"] == "outside_model"
            and view["outside"]["canSendForThisChat"] is True,
            {k: view[k] for k in ("reason", "reasonCode", "outside")},
        )
        ada.patch(f"/api/chats/{outside}", json={"settings": {"memoryExternal": True}})
        HOSTED_TOOL["query"] = "quince"
        before_hosted = len(HOSTED_BODIES)
        ada.say(outside, "Look up what fruit I like.")
        ada.wait_answer(outside)
        HOSTED_TOOL["query"] = None
        with LOCK:
            asked = HOSTED_BODIES[before_hosted:]
        tool_turn = asked[-1] if asked else {}
        results = [m["content"] for m in tool_turn.get("messages") or [] if m["role"] == "tool"]
        check(
            'after "Send for this chat": the pinned block, the turn\'s notes and search_memory, '
            "which Workbench ran for the model",
            len(asked) >= 2
            and "- Prefers metric units" in (system_of(asked[0]) or "")
            and latest_user(asked[0]).startswith("<memory-")
            and tool_names(asked[0]) == ["search_memory"]
            and len(results) == 1
            and results[0].startswith("<memory-")
            and "quince" in results[0],
            (len(asked), tool_names(asked[0]) if asked else None, results[:1]),
        )
        ada.patch(f"/api/chats/{outside}", json={"settings": {"memoryExternal": None}})
        before_hosted = len(HOSTED_BODIES)
        ada.say(outside, "And again?")
        ada.wait_answer(outside)
        withheld = newest(HOSTED_BODIES, before_hosted, "the hosted API")
        replayed = [m["content"] for m in withheld["messages"] if m["role"] == "tool"]
        check(
            "taken back: the kept search_memory result is withheld from the outside model, and "
            "no note, block or tool goes",
            replayed == ["Memory results are not sent to this model."]
            and "quince" not in json.dumps(withheld)
            and system_of(withheld) is None
            and "search_memory" not in tool_names(withheld)
            and "<memory-" not in json.dumps(withheld),
            (replayed, tool_names(withheld)),
        )

        say("8. a long note with tags, export and the tag list, a new version")
        body = "# Release\n\n" + "\n\n".join(
            "Step 41: unlock the walnut cabinet with the brass key."
            if i == 41
            else f"Step {i}: check gauge number {i} and write it down."
            for i in range(1, 80)
        )
        long_note = ada.post(
            "/api/memory/notes",
            json={
                "text": "Release procedure",
                "kind": "procedure",
                "body": body,
                "tags": ["work", "Ops"],
            },
        ).json()["note"]
        ada.post("/api/memory/tags", json={"tag": "work", "description": "paid work"})
        exported = ada.get("/api/memory/export", params={"format": "json"}).json()
        kept_long = next(n for n in exported["notes"] if n["id"] == long_note["id"])
        check(
            "the export carries the body and the tags whole, and the tag list",
            kept_long["body"] == body
            and sorted(kept_long["tags"]) == ["Ops", "work"]
            and {t["tag"]: t["description"] for t in exported["tags"]}
            == {"Ops": "", "work": "paid work"},
        )
        csv_text = ada.get("/api/memory/tags/export").text
        brought = bo.post(
            "/api/memory/tags/import", json={"text": csv_text, "format": "csv"}
        ).json()
        check(
            "the tag list round-trips as CSV into another person's list",
            brought.get("added") == 2
            and {t["tag"]: t["description"] for t in bo.get("/api/memory/tags").json()["tags"]}
            == {"Ops": "", "work": "paid work"},
            brought,
        )
        asked_replace = bo.post(
            "/api/memory/tags/import", json={"text": "work\n", "format": "txt", "replace": True}
        ).json()
        check(
            "Replace my list names what it would remove before it removes anything",
            asked_replace.get("confirmNeeded") is True
            and asked_replace.get("wouldRemove") == [{"tag": "Ops", "notes": 0}]
            and len(bo.get("/api/memory/tags").json()["tags"]) == 2,
            asked_replace,
        )
        check(
            "the long note is in Ada's index as its title and many passages",
            len(index_notes(data, "p-ada").get(long_note["id"]) or []) > 3,
        )
        before_sent, _ = counts()
        ada.say(second, "Where is the brass key for the walnut cabinet? /no_think")
        ada.wait_answer(second)
        found = latest_user(sent(before_sent))
        check(
            "a turn that asks about it is sent the passage that answers, not the whole body",
            "walnut cabinet with the brass key" in found
            and "Step 2:" not in found
            and len(found) < len(body),
            found[:300],
        )
        newer = ada.patch(
            f"/api/memory/notes/{long_note['id']}",
            json={"text": "Release procedure (2026)", "asNewVersion": True},
        ).json()
        history = ada.get(f"/api/memory/notes/{newer['note']['id']}").json()
        check(
            "Save as a new version: the old one is history, never sent, out of the index",
            newer.get("replaced") == long_note["id"]
            and [n["id"] for n in history["earlier"]] == [long_note["id"]]
            and long_note["id"] not in index_notes(data, "p-ada")
            and newer["note"]["id"] in index_notes(data, "p-ada"),
        )

        say("9. Delete is real: rows, index, tags, log and every chat's copy")
        word = next(
            n for n in ada.get("/api/memory").json()["sections"]["other"] if CODE_WORD in n["text"]
        )
        deleted = ada.delete(f"/api/memory/notes/{word['id']}")
        gone_long = ada.delete(f"/api/memory/notes/{newer['note']['id']}")
        # The chat it was used in still holds the model's own answers that said it (the
        # person's chat, not the note): there, the note is in neither the system message
        # nor the new turn. A new chat holds it nowhere.
        before_sent, before_engine = counts()
        ada.say(second, "What was my code word? /no_think")
        ada.wait_answer(second)
        old_chat, old_engine = sent(before_sent), reached(before_engine)
        fresh = ada.new_chat(LOCAL, temperature=0, maxTokens=200)
        before_sent, before_engine = counts()
        ada.say(fresh, "What was my code word? /no_think")
        ada.wait_answer(fresh)
        gone = sent(before_sent)
        check(
            "no later request carries it, at either hop",
            deleted.status_code == 204
            and gone_long.status_code == 204
            and CODE_WORD not in (system_of(old_chat) or "") + latest_user(old_chat)
            and CODE_WORD not in (system_of(old_engine) or "") + latest_user(old_engine)
            and CODE_WORD not in json.dumps(gone)
            and CODE_WORD not in json.dumps(reached(before_engine)),
            (deleted.status_code, gone_long.status_code),
        )
        db = sqlite3.connect(data / "workbench.sqlite3")
        try:
            in_notes = db.execute(
                "SELECT count(*) FROM memory_notes WHERE text LIKE ?", (f"%{STEM}%",)
            ).fetchone()[0]
            in_fts = db.execute(
                "SELECT count(*) FROM memory_notes_fts WHERE memory_notes_fts MATCH 'zebrafish'"
            ).fetchone()[0]
            events = db.execute(
                "SELECT event, old_text, new_text FROM memory_events WHERE note_id = ?",
                (word["id"],),
            ).fetchall()
            in_chats = db.execute(
                "SELECT count(*) FROM chats WHERE memory_snapshot LIKE ?", (f"%{STEM}%",)
            ).fetchone()[0]
            links = db.execute(
                "SELECT count(*) FROM memory_note_tags WHERE note_id IN (?, ?)",
                (long_note["id"], newer["note"]["id"]),
            ).fetchone()[0]
        finally:
            db.close()
        check(
            "no row, store index entry, log text, chat copy or tag link of them is left",
            (in_notes, in_fts, in_chats, links) == (0, 0, 0, 0)
            and [e[0] for e in events] == ["add", "delete"]
            and all(e[1] is None and e[2] is None for e in events),
            (in_notes, in_fts, in_chats, links, events),
        )
        held_now = index_notes(data, "p-ada")
        check(
            "and no passage of either is left in Ada's knowledge index, words included",
            gone_long.status_code == 204
            and word["id"] not in held_now
            and newer["note"]["id"] not in held_now
            and index_matches(data, "p-ada", "zebrafish") == 0
            and index_matches(data, "p-ada", "gauge") == 0,
            sorted(held_now),
        )

        say("10. nobody else sees them, and the owner reads counts and no note")
        quince = next(
            n for n in ada.get("/api/memory").json()["sections"]["other"] if "quince" in n["text"]
        )["id"]
        refused = [
            bo.patch(f"/api/memory/notes/{quince}", json={"text": "x"}),
            bo.delete(f"/api/memory/notes/{quince}"),
            bo.get(f"/api/memory/notes/{quince}"),
            bo.get(f"/api/chats/{second}/memory"),
            bo.post(f"/api/chats/{second}/memory/refresh"),
            bo.post("/api/memory/notes", json={"text": "y", "chatId": second}),
        ]
        bo_search = bo.get("/api/memory/notes", params={"query": "quince"}).json()
        check(
            "Bo's page, search, export and every call for her note or chat answer nothing of hers",
            [n["text"] for n in bo.get("/api/memory").json()["sections"]["other"]]
            == ["Plays the cello"]
            and bo_search["notes"] == []
            and all(r.status_code == 404 for r in refused)
            and "quince" not in bo.get("/api/memory/export?format=md").text
            and "quince" not in bo.get("/api/memory/export?format=json").text,
            [r.status_code for r in refused],
        )
        settings(ownerReadsChats=True)
        try:
            read = owner.get(f"/api/chats/{outside}/memory")
            chat_read = owner.get(f"/api/chats/{outside}")
            check(
                "the owner, who may read her chat, is told counts, and not one word of a note in "
                "the drawer, the answers' records or the search_memory call",
                read.status_code == 200
                and read.json()["readOnly"] is True
                and chat_read.status_code == 200
                and "quince" not in read.text
                and "quince" not in chat_read.text
                and "metric" not in chat_read.text
                and owner.post(f"/api/chats/{outside}/memory/refresh").status_code == 404,
                read.json(),
            )
        finally:
            settings(ownerReadsChats=None)
        check(
            "with that setting off the owner cannot even open her chat's memory",
            owner.get(f"/api/chats/{second}/memory").status_code == 404,
        )

    except AssertionError as exc:
        check(f"the run went to plan ({exc})", False)
    finally:
        say("11. teardown by pid")
        if server is not None:
            server.should_exit = True
        for proc in procs:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False
            )
        time.sleep(2)
        still = [p for p in OWNED if listening(p) and p not in OURS]
        check("no owned process port is still listening", not still, still)
        print(f"\nlogs: {logs}\n{PASSES} passed, {FAILURES} failed", flush=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
