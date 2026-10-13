"""Memory v1 (MR1), live: a person's notes reach a real model, and nobody else's do.

The unit checks drive Workbench against a fake gateway. What only a live run
shows is the whole path a note takes: a real Workbench process, a real
gateway, a real inference-driver, a real `llama-server` answering from a real
small model. Two recording proxies keep what each hop was sent: what
Workbench sent the gateway (the request the drawer must describe exactly) and
what reached the engine. The people are real sign-ins against a stand-in for
Eugene's sign-in (OpenID Connect, as the unit checks use).

  0. isolated: every ambient EUGENE_PLEXUS_* variable dropped, owned ports free
  1. the stack: engine (CPU, Qwen3 0.6B), a driver that declares it local, a
     stub hosted API behind a driver that declares it external, a gateway over
     both, Workbench over the gateway; two people and the owner sign in;
     /api/models says where each model runs, as the gateway does
  2. a note by command ("Remember that my code word is ...", and the message
     is still answered) and by API (a preference, and a pinned instruction)
  3. a new chat's request carries the notes in its ONE system message, after
     the chat's instructions, and reaches the engine so; the model answers a
     question only the note answers; a second person's chat, asked the same,
     is sent none and cannot answer
  4. the chat's drawer lists exactly the text Workbench sent, and what it was
     and was not sent
  5. the next turn sends the same system message byte for byte, a note added
     meanwhile waits, and Refresh takes it
  6. the chat's switch takes the notes out of the next request, and back
  7. a model on an outside account is sent none, until "Send for this chat"
  8. Delete: the note is in no later request, of that chat or a new one, and
     no row, index entry, log text or chat copy of it is left
  9. the second person sees nothing of the first's notes, on any route, and
     the owner, where they can read a person's chats, reads counts and no note
 10. teardown by pid; no owned port still listening

**Safe beside a live install**: every ambient EUGENE_PLEXUS_* variable is
dropped, ports are +350, teardown is by pid. Never `pkill -f eugene_plexus_`:
this box is a worker node.

Run with the Workbench interpreter (it has httpx, fastapi, uvicorn, joserfc):
  workbench/.venv/Scripts/python.exe workbench-mr/scripts/mr1-memory-acceptance.py
"""

from __future__ import annotations

import base64
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
HOSTED_BODIES: list[dict[str, Any]] = []
LOCK = threading.Lock()


class Quiet(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A probe that hangs up early is not news.
        pass


def make_proxy(upstream: int, sink: list[dict[str, Any]]) -> type[BaseHTTPRequestHandler]:
    """A pass-through to `127.0.0.1:upstream` that keeps the body of every chat
    request in `sink`: exactly what the next hop was sent."""

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _forward(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            if self.command == "POST" and self.path.split("?")[0] == "/v1/chat/completions":
                with LOCK:
                    sink.append(json.loads(body))
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
            while chunk := answer.read(4096):
                self.wfile.write(chunk)
                self.wfile.flush()
            hop.close()

        do_GET = _forward
        do_POST = _forward
        do_PATCH = _forward
        do_DELETE = _forward

    return Proxy


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

        say("1. the stack: engine, drivers, gateway, Workbench, and three people")
        threading.Thread(target=serve, args=(Agent, AGENT_PORT), daemon=True).start()
        threading.Thread(target=serve, args=(Hosted, HOSTED), daemon=True).start()
        threading.Thread(
            target=serve, args=(make_proxy(ENGINE, ENGINE_BODIES), ENGINE_PROXY), daemon=True
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
        note(f"answer: {answer['content'][:100]!r}")
        check(
            "the very first request already carried the note, in one system message",
            f"- my code word is {CODE_WORD}" in (system_of(sent(before_sent)) or "")
            and f"- my code word is {CODE_WORD}" in (system_of(reached(before_engine)) or ""),
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
            "a preference and a pinned instruction by API: three notes, one used first",
            fruit.status_code == 201
            and brief.status_code == 201
            and listed["usage"]["notes"] == 3
            and [n["text"] for n in listed["sections"]["always"]]
            == ["Answer in one short sentence"],
            listed["usage"],
        )

        say("3. a new chat is sent the notes; a second person's is sent none")
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
            "Workbench's request has ONE system message: the chat's instructions, then the notes",
            system.startswith("You are Ada's assistant.\n\nWhat you know about this person")
            and [m["role"] for m in request["messages"]] == ["system", "user"],
            [m["role"] for m in request["messages"]],
        )
        check(
            "all three notes are in it, and the app's own words about remembering",
            all(
                line in system
                for line in (
                    f"- my code word is {CODE_WORD}",
                    "- My favourite fruit is the quince",
                    "- Answer in one short sentence",
                    "The app saves notes when the person asks you to remember something",
                )
            ),
        )
        check(
            "and the same message reached the engine, through the gateway and the driver",
            (system_of(reached(before_engine)) or "").endswith(system.split("\n\n", 1)[1]),
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
            "asked the same, a second person's chat is sent no notes, at either hop",
            system_of(bo_request) is None
            and CODE_WORD not in json.dumps(bo_request)
            and CODE_WORD not in json.dumps(reached(before_engine)),
        )
        check(
            "and the model cannot say a word it was never told",
            STEM not in bo_reply["content"].upper(),
            bo_reply["content"][:120],
        )
        note(f"Bo's answer: {bo_reply['content'][:100]!r}")

        say("4. the drawer lists exactly what Workbench sent")
        view = ada.get(f"/api/chats/{second}/memory").json()
        check(
            "its text is Workbench's system message after the chat's instructions, to the byte",
            system == "You are Ada's assistant.\n\n" + str(view["text"]),
        )
        expected_budget = min(1000, window * 8 // 100)
        check(
            "it names the three notes sent, none left out, with the budget for this window",
            sorted(s["text"] for s in view["sent"])
            == sorted(
                [
                    f"my code word is {CODE_WORD}",
                    "My favourite fruit is the quince",
                    "Answer in one short sentence",
                ]
            )
            and view["notSent"] == []
            and view["budget"] == expected_budget
            and view["tokens"] <= view["budget"]
            and view["frozen"] is True
            and view["sending"] is True,
            {k: view[k] for k in ("budget", "tokens", "frozen", "sending", "counts")},
        )
        note(f"{view['tokens']} of {view['budget']} tokens")

        say("5. the next turn sends the same bytes; a note added meanwhile waits")
        added = ada.post("/api/memory/notes", json={"text": "I keep bees"}).json()["note"]
        before_sent, _ = counts()
        ada.say(second, "And my favourite fruit? One word. /no_think")
        ada.wait_answer(second)
        check(
            "the system message is the same bytes as the first turn's",
            system_of(sent(before_sent)) == system,
        )
        view = ada.get(f"/api/chats/{second}/memory").json()
        check(
            "the drawer says the notes changed, and that the new one was not sent",
            view["changed"] is True
            and [n["id"] for n in view["notSent"]] == [added["id"]]
            and view["notSent"][0]["reason"] == "added_since",
        )
        refreshed = ada.post(f"/api/chats/{second}/memory/refresh").json()
        before_sent, _ = counts()
        ada.say(second, "Thanks.")
        ada.wait_answer(second)
        after = system_of(sent(before_sent)) or ""
        check(
            "Refresh memory takes the notes again: the next request carries the new one",
            "- I keep bees" in after
            and after == "You are Ada's assistant.\n\n" + str(refreshed["text"]),
        )

        say("6. the chat's switch takes the notes out, and back")
        ada.patch(f"/api/chats/{second}", json={"settings": {"memory": "off"}})
        before_sent, before_engine = counts()
        ada.say(second, "Anything?")
        ada.wait_answer(second)
        off = sent(before_sent)
        check(
            "with the switch off the system message is the chat's instructions alone, at both hops",
            system_of(off) == "You are Ada's assistant."
            and system_of(reached(before_engine)) == "You are Ada's assistant.",
            (system_of(off), system_of(reached(before_engine))),
        )
        view = ada.get(f"/api/chats/{second}/memory").json()
        check(
            "and the drawer says why",
            view["sending"] is False and view["reasonCode"] == "chat_off" and view["text"] is None,
        )
        ada.patch(f"/api/chats/{second}", json={"settings": {"memory": "on"}})
        before_sent, _ = counts()
        ada.say(second, "Again?")
        ada.wait_answer(second)
        check(
            "switched on again, the same notes are sent again",
            system_of(sent(before_sent)) == after,
        )

        say("7. a model on an outside account is sent none, until the person says so")
        outside = ada.new_chat(OUTSIDE)
        before_hosted = len(HOSTED_BODIES)
        ada.say(outside, "Hello there")
        ada.wait_answer(outside)
        held = newest(HOSTED_BODIES, before_hosted, "the hosted API")
        view = ada.get(f"/api/chats/{outside}/memory").json()
        check(
            "the hosted API was sent no system message and none of the notes",
            system_of(held) is None and "quince" not in json.dumps(held),
            held,
        )
        check(
            "the drawer says the notes were not sent, and why, and offers Send for this chat",
            view["sending"] is False
            and view["reasonCode"] == "outside_model"
            and view["outside"]["canSendForThisChat"] is True
            and "outside your machines" in view["reason"],
            {k: view[k] for k in ("reason", "reasonCode", "outside")},
        )
        ada.patch(f"/api/chats/{outside}", json={"settings": {"memoryExternal": True}})
        before_hosted = len(HOSTED_BODIES)
        ada.say(outside, "And now?")
        ada.wait_answer(outside)
        check(
            'after "Send for this chat" the hosted API is sent the notes',
            "- My favourite fruit is the quince"
            in (system_of(newest(HOSTED_BODIES, before_hosted, "the hosted API")) or ""),
        )

        say("8. Delete is real")
        word = next(
            n for n in ada.get("/api/memory").json()["sections"]["other"] if CODE_WORD in n["text"]
        )
        deleted = ada.delete(f"/api/memory/notes/{word['id']}")
        before_sent, before_engine = counts()
        ada.say(second, "What was my code word? /no_think")
        ada.wait_answer(second)
        gone = sent(before_sent)
        check(
            "the chat that had been sent it is sent a new copy without it, at both hops",
            deleted.status_code == 204
            and CODE_WORD not in (system_of(gone) or "")
            and CODE_WORD not in (system_of(reached(before_engine)) or "")
            and "- My favourite fruit is the quince" in (system_of(gone) or ""),
        )
        fresh = ada.new_chat(LOCAL, temperature=0, maxTokens=100)
        before_sent, _ = counts()
        ada.say(fresh, "Hello. /no_think")
        ada.wait_answer(fresh)
        check("and so is a new chat", CODE_WORD not in json.dumps(sent(before_sent)))
        db = sqlite3.connect(data / "workbench.sqlite3")
        try:
            in_notes = db.execute(
                "SELECT count(*) FROM memory_notes WHERE text LIKE ?", (f"%{STEM}%",)
            ).fetchone()[0]
            in_index = db.execute(
                "SELECT count(*) FROM memory_notes_fts WHERE memory_notes_fts MATCH 'zebrafish'"
            ).fetchone()[0]
            events = db.execute(
                "SELECT event, old_text, new_text FROM memory_events WHERE note_id = ?",
                (word["id"],),
            ).fetchall()
            in_chats = db.execute(
                "SELECT count(*) FROM chats WHERE memory_snapshot LIKE ?", (f"%{STEM}%",)
            ).fetchone()[0]
        finally:
            db.close()
        check(
            "no row, no index entry, no log text and no chat's copy of it is left",
            (in_notes, in_index, in_chats) == (0, 0, 0)
            and [e[0] for e in events] == ["add", "delete"]
            and all(e[1] is None and e[2] is None for e in events),
            (in_notes, in_index, in_chats, events),
        )

        say("9. nobody else sees them, and the owner reads counts and no note")
        quince = next(
            n for n in ada.get("/api/memory").json()["sections"]["other"] if "quince" in n["text"]
        )["id"]
        refused = [
            bo.patch(f"/api/memory/notes/{quince}", json={"text": "x"}),
            bo.delete(f"/api/memory/notes/{quince}"),
            bo.get(f"/api/chats/{second}/memory"),
            bo.post(f"/api/chats/{second}/memory/refresh"),
            bo.post("/api/memory/notes", json={"text": "y", "chatId": second}),
        ]
        check(
            "Bo's page, export and every call for Ada's note or chat answer nothing of hers",
            bo.get("/api/memory").json()["sections"] == {"always": [], "other": []}
            and all(r.status_code == 404 for r in refused)
            and "quince" not in bo.get("/api/memory/export?format=md").text
            and "quince" not in bo.get("/api/memory/export?format=json").text
            and [r.json()["detail"]["message"] for r in refused[:2]]
            == ["There is no such note."] * 2,
            [r.status_code for r in refused],
        )
        bo_forget = bo.say(bo.new_chat(LOCAL, maxTokens=20), "Forget my favourite fruit")
        check(
            'a "forget ..." from Bo finds none of her notes',
            (bo_forget.get("memory") or {}).get("candidates") == [],
        )
        settings(ownerReadsChats=True)
        try:
            read = owner.get(f"/api/chats/{second}/memory")
            body = read.json()
            check(
                "the owner, who may read her chat, is told counts and not one word of a note",
                read.status_code == 200
                and body["readOnly"] is True
                and body["counts"]["sent"] >= 1
                and "quince" not in read.text
                and "bees" not in read.text
                and owner.post(f"/api/chats/{second}/memory/refresh").status_code == 404
                and owner.patch(f"/api/memory/notes/{quince}", json={"text": "x"}).status_code
                == 404,
                body,
            )
            check(
                "and the owner's own Memory page is the owner's",
                owner.get("/api/memory").json()["sections"] == {"always": [], "other": []},
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
        say("10. teardown by pid")
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
