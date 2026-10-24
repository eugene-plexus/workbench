"""Row 26: long chats that outgrow the context, live (`docs/design/rows/row-26.md` §5).

The unit checks drive Workbench in process against a fake gateway. What only a live run shows is
the whole path: a real Workbench, a real gateway and inference driver, and a recording stand-in
engine that says its window on `/props` as llama-server does, reports usage, and refuses a prompt
past its window with llama-server's own error body. It checks the request shapes only. A stand-in
cannot prove that the engine's cache is reused; the run of record with a real engine does.

  0. isolated: every ambient EUGENE_PLEXUS_* variable dropped; every port the OS assigns, and
     none of 8079-8083
  1. the stack: the stand-in engine, a driver, a gateway, Workbench behind a recording proxy
  2. the window: the gateway's /v1/models gives the engine's 16,384, and so does Workbench
  3. a long chat: no answer fails and no request passes the window; the system message and the
     first exchange are the same bytes in every request; between summaries every request is the
     one before plus two messages; the summary request is the request before it plus one message,
     the same model and no tools; the chat folds the run and Read the summary gives its words;
     the store keeps every message as it was written
  4. the engine refuses a request as too long: the answer summarises once and is sent once more
  5. summarising off: a chat past the window is refused before anything is kept or sent
  6. deleting the chat deletes its summaries
  7. teardown by pid; no port of this run still listening

**Safe beside a live install**: ambient EUGENE_PLEXUS_* variables dropped, ports the OS assigns
(never 8079-8083), teardown by pid.

Run with the Workbench interpreter, the gateway and the driver in their own checkouts beside it
(or name their interpreters with R26_GATEWAY_PY and R26_DRIVER_PY):
  workbench/.venv/Scripts/python.exe workbench/scripts/row26-long-chats-acceptance.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import pairwise
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlencode, urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from joserfc import jwt
from joserfc.jwk import RSAKey

HERE = Path(__file__).resolve().parent
WB = HERE.parent
sys.path.insert(0, str(WB / "src"))

from eugene_plexus_workbench import context  # noqa: E402

ROOT = Path(os.environ.get("EP_ROOT", str(WB.parent)))


def _python_of(repo: str, variable: str) -> Path:
    named = os.environ.get(variable)
    if named:
        return Path(named)
    for candidate in (
        ROOT / repo / ".venv" / "Scripts" / "python.exe",
        ROOT / repo / ".venv" / "bin" / "python",
    ):
        if candidate.exists():
            return candidate
    return ROOT / repo / ".venv" / "bin" / "python"


GW_PY = _python_of("gateway", "R26_GATEWAY_PY")
DRV_PY = _python_of("inference-driver", "R26_DRIVER_PY")
WB_PY = Path(sys.executable)

MODEL = "r26-local"
WINDOW = 16_384
CLIENT_ID, CLIENT_SECRET, ADMIN_TOKEN = "r26-client", "r26-client-secret", "r26-admin-token"
CODE_WORD = "ORCHID-42"
#: About 400 tokens each as Workbench counts (1,200 characters): an exchange is about 800.
FILLER = ("The plan for the garden keeps the beds, the paths, the shed and the pond. " * 20)[:1200]
ANSWER = ("Noted. The beds, the paths, the shed and the pond stay in the plan as said. " * 20)[
    :1200
]
SUMMARY = f"The person plans a garden: beds, paths, a shed, a pond. The code word is {CODE_WORD}."
BLOCK = re.compile(r'<earlier-turns-([0-9a-f]{8}) from="(\d+)" to="(\d+)"')
#: Ports a person's install may hold on this machine: never used here.
LIVE_PORTS = range(8079, 8084)

FAILURES = 0
PASSES = 0
LOCK = threading.Lock()


def say(text: str) -> None:
    print(f"\n== {text}", flush=True)


def check(claim: str, passed: bool, detail: object = "") -> bool:
    global FAILURES, PASSES
    if passed:
        PASSES += 1
        print(f"  PASS  {claim}", flush=True)
    else:
        FAILURES += 1
        print(f"  FAIL  {claim}  {detail}", flush=True)
    return passed


def note(text: str) -> None:
    print(f"  NOTE  {text}", flush=True)


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def free_port() -> int:
    """A port the OS assigns, never one a live install may hold."""
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = int(s.getsockname()[1])
        if port not in LIVE_PORTS:
            return port


class Quiet(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass


def serve(handler: type[BaseHTTPRequestHandler]) -> Quiet:
    server = Quiet(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# --------------------------------------------------------------------------- #
# the stand-in engine
# --------------------------------------------------------------------------- #


class Engine:
    """What the stand-in says and does: its window on `/props`, the prompt past which it refuses,
    and every chat request it received (`refused` marks the ones it refused)."""

    window = WINDOW
    refuse_over = WINDOW
    bodies: ClassVar[list[dict[str, Any]]] = []
    refused: ClassVar[list[int]] = []


def _tokens(messages: list[dict[str, Any]]) -> int:
    """A prompt's tokens as Workbench counts them, so what is refused here is what it plans for."""
    return context.count_messages(messages)


class EngineHandler(BaseHTTPRequestHandler):
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
        path = self.path.split("?")[0]
        if path == "/props":
            return self._json(
                {
                    "default_generation_settings": {"n_ctx": Engine.window},
                    "total_slots": 1,
                    "build_info": "b0000-r26-stand-in",
                }
            )
        if path == "/health":
            return self._json({"status": "ok"})
        if path == "/v1/models":
            return self._json({"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        return self._json({"error": {"message": "not found"}}, status=404)

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.split("?")[0] != "/v1/chat/completions":
            return self._json({"error": {"message": "not found"}}, status=404)
        messages = body.get("messages") or []
        prompt = _tokens(messages)
        with LOCK:
            Engine.bodies.append(body)
            place = len(Engine.bodies) - 1
        if prompt > Engine.refuse_over:
            with LOCK:
                Engine.refused.append(place)
            # llama-server's own refusal (b11375).
            return self._json(
                {
                    "error": {
                        "code": 400,
                        "message": f"request ({prompt} tokens) exceeds the available context "
                        f"size ({Engine.refuse_over} tokens), try increasing it",
                        "type": "exceed_context_size_error",
                        "n_prompt_tokens": prompt,
                        "n_ctx": Engine.refuse_over,
                    }
                },
                status=400,
            )
        last = str((messages[-1] if messages else {}).get("content") or "")
        text = SUMMARY if last.startswith("Summarise turns") else ANSWER
        usage = {
            "prompt_tokens": prompt,
            "completion_tokens": context.count_messages([{"content": text}]),
            "total_tokens": prompt + context.count_messages([{"content": text}]),
            "prompt_tokens_details": {"cached_tokens": 0},
        }
        if not body.get("stream"):
            return self._json(
                {
                    "id": "r26",
                    "object": "chat.completion",
                    "model": MODEL,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                }
            )
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()

        def frame(data: dict[str, Any]) -> None:
            self.wfile.write(b"data: " + json.dumps(data).encode() + b"\n\n")

        base = {"id": "r26", "object": "chat.completion.chunk", "model": MODEL}
        frame({**base, "choices": [{"index": 0, "delta": {"role": "assistant"}}]})
        for at in range(0, len(text), 300):
            piece = {"index": 0, "delta": {"content": text[at : at + 300]}, "finish_reason": None}
            frame({**base, "choices": [piece]})
        frame({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        frame({**base, "choices": [], "usage": usage})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


# --------------------------------------------------------------------------- #
# what Workbench sent the gateway
# --------------------------------------------------------------------------- #

#: Chat requests (streamed) and summary requests (not), as Workbench sent them.
CHATS: list[dict[str, Any]] = []
ASKED: list[dict[str, Any]] = []


def make_proxy(upstream: int) -> type[BaseHTTPRequestHandler]:
    import http.client

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _forward(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            if self.command == "POST" and self.path.split("?")[0] == "/v1/chat/completions":
                sent = json.loads(body)
                # Whether it came with the person's token, as an answer's request does; the token
                # stays here, for this gateway has no Eugene to check it against.
                sent["_person"] = bool(self.headers.get("x-eugene-person-token"))
                with LOCK:
                    (CHATS if sent.get("stream") else ASKED).append(sent)
            hop = http.client.HTTPConnection("127.0.0.1", upstream, timeout=900)
            wanted = (
                "content-type",
                "accept",
                "authorization",
                "x-eugene-repetition-mode",
                "x-eugene-app-operation",
            )
            headers = {k: v for k, v in self.headers.items() if k.lower() in wanted}
            hop.request(self.command, self.path, body=body or None, headers=headers)
            answer = hop.getresponse()
            self.send_response(answer.status)
            for key in ("Content-Type", "Cache-Control", "Retry-After"):
                if answer.getheader(key):
                    self.send_header(key, answer.getheader(key))
            self.end_headers()
            while chunk := answer.read(4096):
                self.wfile.write(chunk)
                self.wfile.flush()
            hop.close()

        do_GET = _forward
        do_POST = _forward

    return Proxy


def make_agent(driver_port: int) -> type[BaseHTTPRequestHandler]:
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
                            "name": "r26-driver",
                            "kind": "inference-driver",
                            "url": f"http://127.0.0.1:{driver_port}/",
                            "status": "running",
                        }
                    ]
                }
            elif path == "/v1/runtimes":
                body = {"runtimes": []}
            elif path == "/v1/node":
                body = {"name": "r26-node", "enrolled": False}
            else:
                body = {"status": "ok"}
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return Agent


# --------------------------------------------------------------------------- #
# a stand-in for Eugene's sign-in
# --------------------------------------------------------------------------- #


def eugene_app(port: int) -> Any:
    key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    issuer = f"http://127.0.0.1:{port}/oidc"
    codes: dict[str, dict[str, Any]] = {}
    app = FastAPI()

    def id_token(sub: str, nonce: str | None) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": issuer,
            "aud": CLIENT_ID,
            "sub": sub,
            "iat": now,
            "exp": now + 600,
            "name": "Ada",
            "preferred_username": "ada",
            "eugene_role": "member",
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
        codes[code] = {"sub": q["login_as"], "nonce": q["nonce"], "challenge": q["code_challenge"]}
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
        if form["grant_type"] != "authorization_code":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
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

    @app.post("/oidc/revoke")
    async def revoke() -> Any:
        return {}

    return app


class Person:
    """One person's browser: a cookie jar, the secret the page keeps, an origin."""

    def __init__(self, base: str, sub: str) -> None:
        self.base = base
        self.sub = sub
        self.http = httpx.Client(base_url=base, timeout=120, trust_env=False)
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
        headers = {"Origin": self.base, "X-Workbench-Secret": self.secret or ""}
        return self.http.request(method, path, headers=headers, **kw)

    def get(self, path: str, **kw: Any) -> Any:
        return self.call("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.call("POST", path, **kw)

    def patch(self, path: str, **kw: Any) -> Any:
        return self.call("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.call("DELETE", path, **kw)

    def wait_answer(self, chat: str, seconds: float = 300) -> dict[str, Any]:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            messages = self.get(f"/api/chats/{chat}").json()["messages"]
            if messages and messages[-1]["status"] != "running":
                return dict(messages[-1])
            time.sleep(0.2)
        raise AssertionError("the answer did not finish")


def blocks(body: dict[str, Any]) -> list[tuple[str, int, int]]:
    found = []
    for message in body["messages"]:
        content = message.get("content")
        if isinstance(content, str):
            found += [(n, int(a), int(b)) for n, a, b in BLOCK.findall(content)]
    return found


def turn_text(number: int) -> str:
    said = f" The code word is {CODE_WORD}." if number == 3 else ""
    return f"Turn {number}: {FILLER}{said}"


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    procs: list[subprocess.Popen[bytes]] = []
    servers: list[Quiet] = []
    work = Path(tempfile.mkdtemp(prefix="ep-r26-"))
    logs = work / "logs"
    logs.mkdir()
    owned: list[int] = []

    def start(argv: list[str], log: str, env: dict[str, str]) -> None:
        handle = open(logs / log, "w", encoding="utf-8")  # noqa: SIM115
        procs.append(
            subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, env=env
            )
        )

    def wait_for(port: int, seconds: float = 120) -> bool:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            if listening(port):
                return True
            time.sleep(0.3)
        return False

    eugene: Any = None
    try:
        say("0. isolate this run")
        base = {k: v for k, v in os.environ.items() if not k.startswith("EUGENE_PLEXUS_")}
        for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            base.pop(proxy, None)
            base.pop(proxy.lower(), None)
        check("no ambient EUGENE_PLEXUS_* variable reaches a child", True)
        for path in (GW_PY, DRV_PY, WB_PY):
            if not path.exists():
                check(f"{path} exists", False)
                return 1

        say("1. the stack: a stand-in engine, a driver, a gateway, Workbench")
        engine = serve(EngineHandler)
        servers.append(engine)
        engine_port = engine.server_address[1]
        driver_port, gateway_port, workbench_port, eugene_port = (free_port() for _ in range(4))
        agent = serve(make_agent(driver_port))
        servers.append(agent)
        proxy = serve(make_proxy(gateway_port))
        servers.append(proxy)
        owned = [engine_port, driver_port, gateway_port, workbench_port, eugene_port]
        owned += [agent.server_address[1], proxy.server_address[1]]
        check(
            f"every port is the OS's and none is 8079-8083: {sorted(owned)}",
            not any(p in LIVE_PORTS for p in owned),
        )
        eugene = uvicorn.Server(
            uvicorn.Config(
                eugene_app(eugene_port), host="127.0.0.1", port=eugene_port, log_level="error"
            )
        )
        threading.Thread(target=eugene.run, daemon=True).start()
        (work / "driver.yaml").write_text(
            f"provider: openai_compat_custom\nbaseUrl: http://127.0.0.1:{engine_port}\n"
            f"modelId: {MODEL}\nbackendLocality: local\nrequestTimeoutSeconds: 600\n",
            encoding="utf-8",
            newline="\n",
        )
        start(
            [str(DRV_PY), "-m", "eugene_plexus_inference_driver"],
            "driver.log",
            {
                **base,
                "EUGENE_PLEXUS_DRIVER_CONFIG_FILE": str(work / "driver.yaml"),
                "EUGENE_PLEXUS_DRIVER_BIND_HOST": "127.0.0.1",
                "EUGENE_PLEXUS_DRIVER_BIND_PORT": str(driver_port),
            },
        )
        (work / "gateway.yaml").write_text("routingRefreshSeconds: 2\n", encoding="utf-8")
        start(
            [str(GW_PY), "-m", "eugene_plexus_gateway"],
            "gateway.log",
            {
                **base,
                "EUGENE_PLEXUS_GATEWAY_CONFIG_FILE": str(work / "gateway.yaml"),
                "EUGENE_PLEXUS_GATEWAY_METRICS_FILE": str(work / "metrics.sqlite3"),
                "EUGENE_PLEXUS_GATEWAY_BIND_HOST": "127.0.0.1",
                "EUGENE_PLEXUS_GATEWAY_BIND_PORT": str(gateway_port),
                "EUGENE_PLEXUS_GATEWAY_AGENT_URL": f"http://127.0.0.1:{agent.server_address[1]}",
            },
        )
        for port in (driver_port, gateway_port, eugene_port):
            if not wait_for(port, 180):
                check(f"something listens on {port}; logs in {logs}", False)
                return 1

        say("2. the window, from the engine to the gateway to Workbench")
        listed: dict[str, Any] = {}
        end = time.perf_counter() + 120
        while time.perf_counter() < end:
            try:
                found = httpx.get(
                    f"http://127.0.0.1:{gateway_port}/v1/models", timeout=5, trust_env=False
                ).json()
                listed = next((m for m in found.get("data") or [] if m["id"] == MODEL), {})
                if (listed.get("x_eugene_plexus") or {}).get("context_length"):
                    break
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(1)
        window = (listed.get("x_eugene_plexus") or {}).get("context_length")
        if not check(
            "the gateway's /v1/models gives the engine's window", window == WINDOW, window
        ):
            return 1
        data = work / "workbench-data"
        data.mkdir()
        static = work / "static"
        static.mkdir()
        (static / "index.html").write_text("<!doctype html><title>Workbench</title>", "utf-8")
        (work / "app_key").write_text("r26-app-key", encoding="utf-8")
        (work / "oidc_secret").write_text(CLIENT_SECRET, encoding="utf-8")
        workbench = f"http://127.0.0.1:{workbench_port}"
        start(
            [str(WB_PY), "-m", "eugene_plexus_workbench"],
            "workbench.log",
            {
                **base,
                "PYTHONPATH": str(WB / "src"),
                "EUGENE_PLEXUS_APP_BIND_HOST": "127.0.0.1",
                "EUGENE_PLEXUS_APP_BIND_PORT": str(workbench_port),
                "EUGENE_PLEXUS_APP_DATA_DIR": str(data),
                "EUGENE_PLEXUS_APP_KEY_FILE": str(work / "app_key"),
                "EUGENE_PLEXUS_APP_ADMIN_TOKEN": ADMIN_TOKEN,
                "EUGENE_PLEXUS_APP_GATEWAY_URL": f"http://127.0.0.1:{proxy.server_address[1]}",
                "EUGENE_PLEXUS_APP_OIDC_ISSUER": f"http://127.0.0.1:{eugene_port}/oidc",
                "EUGENE_PLEXUS_APP_OIDC_CLIENT_ID": CLIENT_ID,
                "EUGENE_PLEXUS_APP_OIDC_SECRET_FILE": str(work / "oidc_secret"),
                "EUGENE_PLEXUS_APP_STATIC_DIR": str(static),
            },
        )
        if not wait_for(workbench_port, 90):
            check(f"Workbench listens on {workbench_port}; logs in {logs}", False)
            return 1
        ada = Person(workbench, "p-ada")
        ada.sign_in()
        listing = ada.get("/api/models")
        if not check("Workbench lists the models", listing.status_code == 200, listing.text[:300]):
            return 1
        models = {m["id"]: m for m in listing.json()["models"]}
        check(
            "Workbench says the same window",
            (models.get(MODEL) or {}).get("contextLength") == WINDOW,
            models.get(MODEL),
        )

        say("3. a long chat")
        chat = str(ada.post("/api/chats", json={"model": MODEL}).json()["id"])
        made = ada.patch(f"/api/chats/{chat}", json={"settings": {"instructions": "Be brief."}})
        check(
            "the chat has instructions, so its requests open with a system message", made.is_success
        )
        answers: list[dict[str, Any]] = []
        turns = 0
        while turns < 40 and len(ASKED) < 2:
            turns += 1
            sent = ada.post(f"/api/chats/{chat}/messages", json={"content": turn_text(turns)})
            if sent.status_code != 201:
                check(f"turn {turns} is sent", False, sent.text)
                return 1
            answers.append(ada.wait_answer(chat))
        note(f"{turns} turns, {len(ASKED)} summaries written, {len(CHATS)} chat requests")
        check(
            "no answer fails",
            all(a["status"] == "done" for a in answers),
            [a.get("error") for a in answers if a["status"] != "done"],
        )
        check("a summary was written", bool(ASKED))
        check(
            "no request reached the engine past its window, and none was refused",
            not Engine.refused and all(_tokens(b["messages"]) <= WINDOW for b in Engine.bodies),
            Engine.refused,
        )
        head = CHATS[2]["messages"][:3]
        check(
            "the system message and the first exchange are the same bytes in every request",
            head[0]["role"] == "system" and all(body["messages"][:3] == head for body in CHATS[2:]),
        )
        pairs = [(a, b) for a, b in pairwise(CHATS) if blocks(a) and blocks(a) == blocks(b)]
        check(
            "between summaries every request is the one before plus two messages",
            len(pairs) >= 2
            and all(
                b["messages"][: len(a["messages"])] == a["messages"]
                and len(b["messages"]) == len(a["messages"]) + 2
                for a, b in pairs
            ),
            len(pairs),
        )
        first = next(i for i, body in enumerate(CHATS) if blocks(body))
        asked = ASKED[0]
        replayed = asked["messages"][:-1]
        check(
            "the summary request is the request before it plus one message",
            replayed == CHATS[first - 1]["messages"][: len(replayed)]
            and asked["messages"][-1]["content"].startswith("Summarise turns 3 to "),
        )
        check(
            "the summary request goes to the chat's own model, with no tools and no web search",
            asked["model"] == MODEL and "tools" not in asked and "web_search_options" not in asked,
        )
        check(
            "the summary request carries the person's token, as the answer's request does",
            asked["_person"] and CHATS[first - 1]["_person"],
        )
        check(
            "the summary opens the first person's message after the head",
            BLOCK.match(CHATS[first]["messages"][3]["content"]) is not None,
        )
        detail = ada.get(f"/api/chats/{chat}").json()
        folded = detail.get("summaries") or []
        check("the chat folds the run", bool(folded) and folded[0]["from"] == 3, folded[:1])
        if folded:
            read = ada.get(f"/api/chats/{chat}/summaries/{folded[0]['id']}").json()
            check("Read the summary gives its words", read.get("text") == SUMMARY, read)
        shown = detail["messages"]
        check(
            "the store keeps every message as it was written",
            len(shown) == 2 * turns
            and all(shown[2 * i]["content"] == turn_text(i + 1) for i in range(turns))
            and all(m["content"] == ANSWER for m in shown[1::2]),
        )
        view = ada.get(f"/api/chats/{chat}/long").json()
        check(
            "the drawer's numbers are the window and the trigger in effect",
            view.get("window") == WINDOW and view.get("trigger") == WINDOW * 80 // 100,
            view,
        )

        say("4. the engine refuses a request as too long")
        Engine.refuse_over = 9_000
        recovered = None
        for _ in range(12):
            turns += 1
            before_chats, before_refused = len(CHATS), len(Engine.refused)
            sent = ada.post(f"/api/chats/{chat}/messages", json={"content": turn_text(turns)})
            if sent.status_code != 201:
                check(f"turn {turns} is sent", False, sent.text)
                return 1
            answer = ada.wait_answer(chat)
            if len(Engine.refused) > before_refused:
                recovered = (answer, CHATS[before_chats:])
                break
        Engine.refuse_over = WINDOW
        if not check("the engine refused a request", recovered is not None):
            return 1
        assert recovered is not None
        answer, bodies = recovered
        check("the answer is written all the same", answer["status"] == "done", answer.get("error"))
        check(
            "it was sent twice: refused, then summarised and sent once more",
            len(bodies) == 2
            and len(blocks(bodies[1])) >= 1
            and blocks(bodies[1]) != blocks(bodies[0]),
            [blocks(b) for b in bodies],
        )
        with sqlite3.connect(data / "workbench.sqlite3") as db:
            hows = [
                r[0]
                for r in db.execute("SELECT how FROM chat_summaries WHERE chat_id = ?", (chat,))
            ]
        check("the summary written after the refusal says so", "overflow" in hows, hows)

        say("5. summarising off: refused before anything is kept")
        off = ada.patch(f"/api/chats/{chat}", json={"settings": {"summarise": False}})
        check("summarising is turned off for the chat", off.is_success)
        count, asked_before = len(ada.get(f"/api/chats/{chat}").json()["messages"]), len(CHATS)
        refused = ada.post(f"/api/chats/{chat}/messages", json={"content": turn_text(turns + 1)})
        detail_ = refused.json().get("detail") if refused.status_code == 409 else None
        check(
            "the send is refused with the cause",
            isinstance(detail_, dict)
            and detail_.get("code") == "long_chat"
            and "Turn on summarising for this chat" in str(detail_.get("message")),
            refused.text[:300],
        )
        check(
            "nothing was kept or sent",
            len(ada.get(f"/api/chats/{chat}").json()["messages"]) == count
            and len(CHATS) == asked_before,
        )

        say("6. deleting the chat deletes its summaries")
        gone = ada.delete(f"/api/chats/{chat}")
        with sqlite3.connect(data / "workbench.sqlite3") as db:
            left = db.execute(
                "SELECT COUNT(*) FROM chat_summaries WHERE chat_id = ?", (chat,)
            ).fetchone()[0]
        check("the chat and its summaries are gone", gone.status_code == 204 and left == 0, left)
    finally:
        say("7. teardown")
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        if eugene is not None:
            eugene.should_exit = True
        for server in servers:
            server.shutdown()
            server.server_close()
        time.sleep(1.0)
        still = [p for p in owned if listening(p)]
        check("no port of this run is still listening", not still, still)
    print(f"\n{PASSES} passed, {FAILURES} failed. Logs: {logs}", flush=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
