"""Documents in the prompt (MR2), live: a real model answers from a person's files.

The unit checks drive Workbench against a fake gateway. What only a live run shows
is the whole path a file takes: a real Workbench process reading a file in a child
process of its own, a real gateway, a real inference-driver, a real `llama-server`
answering from a real small model that cannot read a PDF (nobody's local model
can). Two recording proxies keep what each hop was sent: what Workbench sent the
gateway, and what reached the engine -- with the engine's own account of how much of
the prompt it had already, which is the cache check. The people are real sign-ins
against a stand-in for Eugene's sign-in (OpenID Connect, as the unit checks use).

  0. isolated: every ambient EUGENE_PLEXUS_* variable dropped, owned ports free
  1. the stack: engine (CPU, Qwen3 0.6B, a window of 8,192), a driver that says it
     is local, a gateway, Workbench; two people and the owner sign in
  2. a Markdown file, a text PDF and a Word file, each in a chat of its own, each
     with a made-up code word the model cannot know: it is read in the background,
     the chip says so, the request carries it as text in the message it was
     attached to (at both hops), the real model answers with the code word, and the
     answer lists the file and, for the PDF, page 1 to 3
  3. a file too long for the window: the numbers, a refusal to send until the person
     chooses, the explicit partial read, and what was and was not sent
  4. a corrupt PDF fails that one file with its cause, a scan and a locked file are
     skipped in words, and Workbench keeps answering, in that chat and in another
  5. two people: the second is told nothing of the first's files, their text or
     their choices, and the owner reads them only where they may read her chats
  6. the cache: turn two's request is turn one's, byte for byte, up to the new
     turn, and the engine says it already held that much of the prompt
  7. teardown by pid; no owned port still listening

**Safe beside a live install**: every ambient EUGENE_PLEXUS_* variable is dropped,
ports are 8460-8469, teardown is by pid. Never `pkill -f eugene_plexus_`: this box
is a worker node.

Run with the Workbench interpreter (it has httpx, fastapi, uvicorn, joserfc, pypdf):
  workbench/.venv/Scripts/python.exe workbench-mr/scripts/mr2-documents-acceptance.py
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.client
import json
import os
import re
import secrets
import socket
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

sys.path.insert(0, str(HERE))
from tests import documents as docs  # noqa: E402  (the fixtures the unit checks use)

(
    GW_PORT,
    AGENT_PORT,
    DRV_LOCAL,
    ENGINE,
    ENGINE_PROXY,
    WB_PORT,
    EUGENE_PORT,
    GW_PROXY,
) = range(8460, 8468)
OWNED = [GW_PORT, AGENT_PORT, DRV_LOCAL, ENGINE, ENGINE_PROXY, WB_PORT, EUGENE_PORT, GW_PROXY]
#: Ports a thread of this script serves; the rest belong to child processes.
OURS = (AGENT_PORT, ENGINE_PROXY, EUGENE_PORT, GW_PROXY)
WORKBENCH = f"http://127.0.0.1:{WB_PORT}"
LOCAL = "mr2-local"

CLIENT_ID, CLIENT_SECRET, ADMIN_TOKEN = "mr2-client", "mr2-client-secret", "mr2-admin-token"
MD_WORD, DOCX_WORD, LONG_WORD = "HERON-1180", "QUOKKA-7359", "OSPREY-2255"
PDF_WORD = docs.CODE_WORD  # TAMARIND-6042, on page three
QUESTION = "What is the code word? Reply with only the code word. /no_think"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

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
# What each hop was sent, and what the engine said of its own cache
# --------------------------------------------------------------------------- #

#: Every chat request Workbench sent the gateway, and every one the engine
#: received, with the engine's `timings` for it (None when it sent none).
WB_SENT: list[dict[str, Any]] = []
ENGINE_BODIES: list[dict[str, Any]] = []
ENGINE_TIMINGS: list[dict[str, Any] | None] = []
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
                        "name": "mr2-local-driver",
                        "kind": "inference-driver",
                        "url": f"http://127.0.0.1:{DRV_LOCAL}/",
                        "status": "running",
                    }
                ]
            }
        elif path == "/v1/runtimes":
            body = {"runtimes": []}
        elif path == "/v1/node":
            body = {"name": "mr2-node", "enrolled": False}
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
        self.http = httpx.Client(base_url=WORKBENCH, timeout=240, trust_env=False)
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

    def put(self, path: str, **kw: Any) -> Any:
        return self.call("PUT", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.call("DELETE", path, **kw)

    def new_chat(self, model: str, **settings: Any) -> str:
        chat = self.post("/api/chats", json={"model": model}).json()["id"]
        if settings:
            changed = self.call("PATCH", f"/api/chats/{chat}", json={"settings": settings})
            assert changed.status_code == 200, changed.text
        return str(chat)

    def upload(self, chat: str, name: str, data: bytes, media: str) -> Any:
        return self.post(f"/api/chats/{chat}/files", files={"file": (name, data, media)})

    def files(self, chat: str, **params: Any) -> dict[str, Any]:
        found = self.get(f"/api/chats/{chat}/files", params=params or None)
        assert found.status_code == 200, found.text
        return dict(found.json())

    def wait_read(self, chat: str, file_id: str, seconds: float = 120) -> dict[str, Any]:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            found = next(f for f in self.files(chat)["files"] if f["id"] == file_id)
            if found["reading"] is None or found["reading"]["state"] != "reading":
                return found
            time.sleep(0.3)
        raise AssertionError("the file was not read in time")

    def wait_answer(self, chat: str, seconds: float = 300) -> dict[str, Any]:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            messages = self.get(f"/api/chats/{chat}").json()["messages"]
            if messages and messages[-1]["status"] != "running":
                return dict(messages[-1])
            time.sleep(0.5)
        raise AssertionError("the answer did not finish")

    def say(self, chat: str, content: str, **body: Any) -> Any:
        return self.post(f"/api/chats/{chat}/messages", json={"content": content, **body})


def newest(sink: list[dict[str, Any]], count: int, what: str) -> dict[str, Any]:
    """The newest request in `sink`, once it holds more than `count`."""
    end = time.time() + 60
    while time.time() < end:
        with LOCK:
            if len(sink) > count:
                return sink[-1]
        time.sleep(0.2)
    raise AssertionError(f"{what} was sent no request")


def blocks_of(content: Any) -> list[tuple[str, ...]]:
    text = content if isinstance(content, str) else content[0]["text"]
    pattern = (
        r'<document-([0-9a-f]{8}) name="([^"]*)" type="([a-z]+)"([^>]*)>\n(.*?)\n</document-\1>'
    )
    return [m.groups() for m in re.finditer(pattern, text, re.DOTALL)]


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    procs: list[subprocess.Popen[bytes]] = []
    work = Path(tempfile.mkdtemp(prefix="ep-mr2-"))
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

        say("1. the stack: engine, driver, gateway, Workbench, and three people")
        threading.Thread(target=serve, args=(Agent, AGENT_PORT), daemon=True).start()
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
        (work / "driver-local.yaml").write_text(
            f"provider: openai_compat_custom\nbaseUrl: http://127.0.0.1:{ENGINE_PROXY}\n"
            f"modelId: {LOCAL}\nbackendLocality: local\nrequestTimeoutSeconds: 600\n",
            encoding="utf-8",
            newline="\n",
        )
        start(
            [str(DRV_PY), "-m", "eugene_plexus_inference_driver"],
            "driver-local.log",
            {
                **base,
                "EUGENE_PLEXUS_DRIVER_CONFIG_FILE": str(work / "driver-local.yaml"),
                "EUGENE_PLEXUS_DRIVER_BIND_HOST": "127.0.0.1",
                "EUGENE_PLEXUS_DRIVER_BIND_PORT": str(DRV_LOCAL),
            },
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
        for port in (ENGINE, DRV_LOCAL, GW_PORT, EUGENE_PORT):
            if not wait_for(port, 180):
                check(f"something listens on {port}; logs in {logs}", False)
                return 1
        ids: set[str] = set()
        known = False
        end = time.time() + 240
        while time.time() < end and not known:
            try:
                listed = httpx.get(
                    f"http://127.0.0.1:{GW_PORT}/v1/models", timeout=5, trust_env=False
                ).json()
                ids = {m["id"] for m in listed.get("data") or []}
                entry = next((m for m in listed.get("data") or [] if m["id"] == LOCAL), None)
                # The driver learns the engine's window once the model has loaded.
                known = (
                    entry is not None
                    and (entry.get("x_eugene_plexus") or {}).get("context_length") is not None
                )
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(1)
        if not check(f"the gateway routes {LOCAL} and knows its window", known, sorted(ids)):
            return 1

        data = work / "workbench-data"
        static = work / "static"
        static.mkdir()
        (static / "index.html").write_text(
            "<!doctype html><title>Workbench</title>", encoding="utf-8"
        )
        (work / "app_key").write_text("mr2-app-key", encoding="utf-8")
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
        window = listing[LOCAL]["contextLength"]
        note(f"{LOCAL}: window {window} tokens; takes PDFs as files: {listing[LOCAL]['fileInput']}")
        if not check(
            "the model is a local one that cannot read a PDF, with a window of 8,192",
            window == 8192
            and listing[LOCAL]["fileInput"] is False
            and listing[LOCAL]["locality"] == "local",
            listing[LOCAL],
        ):
            return 1

        def settings(**values: Any) -> Any:
            return httpx.patch(
                f"{WORKBENCH}/v1/config",
                json=values,
                headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
                trust_env=False,
            )

        # ------------------------------------------------------------------ #
        say("2. a Markdown file, a text PDF and a Word file, each answered by a real model")
        md = (
            b"# Field notes\n\nSome opening words about the garden.\n\n"
            + f"## The secret\n\nThe code word is {MD_WORD}.\n".encode()
        )
        files_to_read = [
            ("notes.md", md, "text/markdown", MD_WORD, "markdown"),
            ("report.pdf", docs.three_pages(), "application/pdf", PDF_WORD, "pdf"),
            ("report.docx", docs.docx(DOCX_WORD), DOCX_TYPE, DOCX_WORD, "docx"),
        ]
        first_pdf_chat: tuple[str, str] | None = None
        for name, content, media, word, kind in files_to_read:
            stem = word.split("-")[0]
            chat = ada.new_chat(LOCAL, temperature=0, maxTokens=300)
            uploaded = ada.upload(chat, name, content, media)
            body = uploaded.json()
            check(
                f"{name}: attached, and the answer says it is being read or already read",
                uploaded.status_code == 201
                and body["kind"] == "document"
                and body["reading"]["state"] in ("reading", "ready")
                and body["reading"]["kind"] == kind,
                body,
            )
            done = ada.wait_read(chat, body["id"])
            reading, decision = done["reading"], done["decision"]
            check(
                f"{name}: read in the background; its size is said, and it will be read whole",
                reading["state"] == "ready"
                and reading["tokens"] > 0
                and decision["kind"] == "whole"
                and decision["send"] is True
                and (reading["pages"] == 3) == (kind == "pdf"),
                done,
            )
            note(f"{name}: {decision['message']}")
            before_sent, before_engine = counts()
            reply_to = ada.say(chat, QUESTION, attachments=[body["id"]])
            check(f"{name}: sent", reply_to.status_code == 201, reply_to.text)
            reply = ada.wait_answer(chat)
            request = newest(WB_SENT, before_sent, "the gateway")
            tries = 1
            while stem not in reply["content"].upper() and tries < 3:
                tries += 1
                ada.post(f"/api/chats/{chat}/retry")
                reply = ada.wait_answer(chat)
            content_sent = request["messages"][-1]["content"]
            found = blocks_of(content_sent)
            check(
                f"{name}: Workbench's request is ONE user message, the file's text in a delimited "
                "block before the person's words",
                [m["role"] for m in request["messages"]] == ["user"]
                and isinstance(content_sent, str)
                and len(found) == 1
                and found[0][1] == name
                and word in found[0][4]
                and content_sent.endswith("\n\n" + QUESTION),
                [m["role"] for m in request["messages"]],
            )
            reached = newest(ENGINE_BODIES, before_engine, "the engine")
            check(
                f"{name}: and the same text reached the real engine, through the gateway "
                "and the driver",
                word in json.dumps(reached["messages"]) and "file_data" not in json.dumps(reached),
            )
            check(
                f"{name}: the real model answers with the code word it was given",
                stem in reply["content"].upper(),
                f"{tries} tries: {reply['content'][:120]!r}",
            )
            note(f"answer: {reply['content'][:100]!r}")
            [source] = [s for s in reply["sources"] if s["kind"] == "document"]
            if kind == "pdf":
                check(
                    "report.pdf: the answer lists the file and pages 1 to 3 of 3, kind document",
                    source["name"] == name
                    and (source["page_start"], source["page_end"], source["pages_total"])
                    == (1, 3, 3)
                    and source["partial"] is False,
                    source,
                )
                first_pdf_chat = (chat, body["id"])
            else:
                check(
                    f"{name}: the answer lists the file, kind document, as sent whole",
                    source["name"] == name
                    and source["partial"] is False
                    and source["type"] == kind,
                    source,
                )
            text_view = ada.get(f"/api/files/{body['id']}/text")
            check(
                f"{name}: the text Workbench read can be shown, and has the code word in it",
                text_view.status_code == 200 and word in text_view.text,
            )

        # ------------------------------------------------------------------ #
        say("3. a file too long for the window: the numbers, and a part only when chosen")
        pages = [
            "\n".join(
                [
                    f"Page {n} of the long file. "
                    + (f"The code word is {LONG_WORD}." if n == 1 else "")
                ]
                + [
                    f"Line {i} of page {n} carries ordinary words and nothing more."
                    for i in range(40)
                ]
            )
            for n in range(1, 31)
        ]
        long_chat = ada.new_chat(LOCAL, temperature=0, maxTokens=300)
        uploaded = ada.upload(long_chat, "long.pdf", docs.pdf(pages), "application/pdf").json()
        done = ada.wait_read(long_chat, uploaded["id"])
        decision = done["decision"]
        note(
            f"long.pdf: {done['reading']['pages']} pages, about {decision['tokens']:,} tokens; "
            f"window {decision['window']:,}; read whole by default up to "
            f"{decision['wholeLimit']:,}; room to read up to {decision['readAllLimit']:,}"
        )
        offer = (decision["offers"] or [{}])[0]
        check(
            "long.pdf: too long for this model's window; the numbers are given, and the offer is "
            "the first part, by pages",
            decision["kind"] == "too_long"
            and decision["send"] is False
            and decision["window"] == 8192
            and decision["tokens"] > 20_000
            and offer.get("action") == "readFirst"
            and offer["cut"]["pages"][0] == 1
            and 1 <= offer["cut"]["pages"][1] < 30
            and offer["tokens"] + decision["overhead"] <= decision["readAllLimit"],
            decision,
        )
        note(f"the offer: {offer.get('where')} ({offer.get('tokens'):,} tokens)")
        sent_before = counts()[0]
        refused = ada.say(long_chat, "Summarise it. /no_think", attachments=[uploaded["id"]])
        check(
            "sending it without choosing is refused (409), names the file and the numbers, "
            "and nothing is stored or sent",
            refused.status_code == 409
            and refused.json()["detail"]["code"] == "documents"
            and "too long for this model's window of 8,192 tokens"
            in refused.json()["detail"]["message"]
            and ada.get(f"/api/chats/{long_chat}").json()["messages"] == []
            and counts()[0] == sent_before,
            refused.text[:300],
        )
        chosen = ada.put(
            f"/api/chats/{long_chat}/files/{uploaded['id']}/reading",
            json={"mode": "first", "tokens": offer["tokens"]},
        )
        last_page = offer["cut"]["pages"][1]
        check(
            "choosing the first part is explicit, kept with the file, and says how much "
            "will be read",
            chosen.status_code == 200
            and chosen.json()["decision"]["kind"] == "partial"
            and chosen.json()["reading"]["chosen"] == {"mode": "first", "tokens": offer["tokens"]},
            chosen.text[:300],
        )
        before_sent, before_engine = counts()
        ada.say(
            long_chat,
            "What is the code word on page one? Reply with only the code word. /no_think",
            attachments=[uploaded["id"]],
        )
        reply = ada.wait_answer(long_chat)
        request = newest(WB_SENT, before_sent, "the gateway")
        content_sent = request["messages"][-1]["content"]
        [found_block] = blocks_of(content_sent)
        check(
            f"the request carries pages 1 to {last_page} only, says it is the first part, and "
            f"page {last_page + 1} is not in it",
            f"[page {last_page}]" in found_block[4]
            and f"[page {last_page + 1}]" not in found_block[4]
            and found_block[3] == f' pages="1 to {last_page} of 30" read="first part"'
            and "The rest of the file was not given to you" in found_block[4],
            found_block[3],
        )
        [source] = [s for s in reply["sources"] if s["kind"] == "document"]
        check(
            "the answer lists the part that was sent, and that it is a part",
            source["partial"] is True
            and (source["page_start"], source["page_end"], source["pages_total"])
            == (1, last_page, 30),
            source,
        )
        check(
            "the real model, given page one, answers from it",
            LONG_WORD.split("-")[0] in reply["content"].upper(),
            reply["content"][:120],
        )
        note(f"answer: {reply['content'][:100]!r}")
        engine_prompt = newest(ENGINE_BODIES, before_engine, "the engine")
        check(
            "and the engine was not sent the pages that were not chosen",
            f"[page {last_page + 1}]" not in json.dumps(engine_prompt),
        )

        # ------------------------------------------------------------------ #
        say("4. a corrupt PDF, a scan and a locked file: one file each, and Workbench answers")
        mix_chat = ada.new_chat(LOCAL, temperature=0, maxTokens=200)
        bad = ada.upload(mix_chat, "bad.pdf", docs.pdf_damaged(), "application/pdf").json()
        scan = ada.upload(mix_chat, "scan.pdf", docs.pdf_pictures(3), "application/pdf").json()
        locked = ada.upload(mix_chat, "locked.pdf", docs.pdf_encrypted(), "application/pdf").json()
        bad_done, scan_done, locked_done = (
            ada.wait_read(mix_chat, one["id"]) for one in (bad, scan, locked)
        )
        check(
            "the corrupt PDF fails with the cause the libraries observed",
            bad_done["reading"]["state"] == "failed"
            and bad_done["reading"]["message"].startswith(
                "Failed: Workbench could not read this file ("
            )
            and "PDFium: Data format error" in bad_done["reading"]["message"],
            bad_done["reading"],
        )
        note(bad_done["reading"]["message"])
        check(
            "the scan is skipped: no text, only pictures of pages",
            scan_done["reading"]["state"] == "skipped"
            and scan_done["reading"]["message"]
            == "Skipped: this PDF has no text, only pictures of pages. "
            "Workbench cannot read scanned pages yet.",
            scan_done["reading"],
        )
        check(
            "the locked file is skipped as password-protected",
            locked_done["reading"]["message"] == "Skipped: this file is password-protected.",
            locked_done["reading"],
        )
        refused = ada.say(mix_chat, "What is in them?", attachments=[bad["id"]])
        check(
            "sending the corrupt file is refused naming it and why, and keeps nothing",
            refused.status_code == 409
            and refused.json()["detail"]["message"].startswith(
                "bad.pdf: Failed: Workbench could not read"
            )
            and ada.get(f"/api/chats/{mix_chat}").json()["messages"] == [],
            refused.text[:300],
        )
        quick = ada.say(mix_chat, "Say hello in one word. /no_think")
        reply = ada.wait_answer(mix_chat)
        other = ada.new_chat(LOCAL, temperature=0, maxTokens=100)
        ada.say(other, "Say yes in one word. /no_think")
        other_reply = ada.wait_answer(other)
        check(
            "Workbench keeps answering with the real model, in that chat and in another",
            quick.status_code == 201
            and reply["status"] == "done"
            and other_reply["status"] == "done",
            (reply["status"], other_reply["status"]),
        )
        for gone in (bad, scan, locked):
            ada.delete(f"/api/chats/{mix_chat}/files/{gone['id']}")
        listed = ada.files(mix_chat)["files"]
        check("the three files can be removed, and none is left", listed == [], listed)

        # ------------------------------------------------------------------ #
        say("5. two people: nothing of Ada's files is Bo's, and the owner reads only where allowed")
        assert first_pdf_chat is not None
        pdf_chat, pdf_id = first_pdf_chat
        refused = [
            bo.get(f"/api/files/{pdf_id}"),
            bo.get(f"/api/files/{pdf_id}/text"),
            bo.get(f"/api/chats/{pdf_chat}/files"),
            bo.put(f"/api/chats/{pdf_chat}/files/{pdf_id}/reading", json={"mode": "whole"}),
            bo.delete(f"/api/chats/{pdf_chat}/files/{pdf_id}"),
            bo.upload(pdf_chat, "x.md", b"# x\n", "text/markdown"),
        ]
        check(
            "Bo gets the same 404 for her file, its text, her chat's files, a choice and a removal",
            all(r.status_code == 404 for r in refused),
            [r.status_code for r in refused],
        )
        mine = bo.upload(bo.new_chat(LOCAL), "mine.md", b"# mine\nwords\n", "text/markdown").json()[
            "id"
        ]
        check(
            "and his own file is his: it downloads for him, and she cannot read it",
            bo.get(f"/api/files/{mine}").status_code == 200
            and ada.get(f"/api/files/{mine}").status_code == 404
            and ada.get(f"/api/files/{mine}/text").status_code == 404,
        )
        check(
            "the owner cannot read her file while the owner's switch is off",
            owner.get(f"/api/files/{pdf_id}").status_code == 404
            and owner.get(f"/api/files/{pdf_id}/text").status_code == 404,
        )
        settings(ownerReadsChats=True)
        try:
            check(
                "and can read it, and the text read from it, once the switch is on",
                owner.get(f"/api/files/{pdf_id}").content == docs.three_pages()
                and PDF_WORD in owner.get(f"/api/files/{pdf_id}/text").text
                and owner.get(f"/api/chats/{pdf_chat}/files").status_code == 404,
            )
        finally:
            settings(ownerReadsChats=None)

        # ------------------------------------------------------------------ #
        say("6. the cache: turn two is turn one, byte for byte, up to the new turn")
        cache_chat = ada.new_chat(LOCAL, temperature=0, maxTokens=60)
        cached = ada.upload(cache_chat, "report.pdf", docs.three_pages(), "application/pdf").json()
        ada.wait_read(cache_chat, cached["id"])
        before_sent, before_engine = counts()
        ada.say(
            cache_chat,
            "What is on page two? One short sentence. /no_think",
            attachments=[cached["id"]],
        )
        ada.wait_answer(cache_chat)
        one = newest(WB_SENT, before_sent, "the gateway")
        one_engine = newest(ENGINE_BODIES, before_engine, "the engine")
        engine_at = before_engine
        before_sent, before_engine = counts()
        ada.say(cache_chat, "And page one? One short sentence. /no_think")
        ada.wait_answer(cache_chat)
        two = newest(WB_SENT, before_sent, "the gateway")
        two_engine = newest(ENGINE_BODIES, before_engine, "the engine")
        n = len(one["messages"])
        check(
            "Workbench's second request begins with its first, byte for byte (the document once, "
            "in the same bytes)",
            json.dumps(two["messages"][:n]) == json.dumps(one["messages"])
            and [m["role"] for m in two["messages"]] == ["user", "assistant", "user"]
            and json.dumps(two["messages"]).count("<document-") == 1,  # its one opening tag
            [m["role"] for m in two["messages"]],
        )
        m = len(one_engine["messages"])
        check(
            "and so does the request the engine received",
            json.dumps(two_engine["messages"][:m]) == json.dumps(one_engine["messages"]),
        )
        with LOCK:
            first_timings, second_timings = ENGINE_TIMINGS[engine_at], ENGINE_TIMINGS[before_engine]
        if first_timings and second_timings:
            note(
                f"engine, turn one: read {first_timings.get('prompt_n')} tokens, held "
                f"{first_timings.get('cache_n')}; turn two: read {second_timings.get('prompt_n')}, "
                f"held {second_timings.get('cache_n')}"
            )
            check(
                "the engine already held at least 90% of turn one's prompt when turn two came",
                second_timings.get("cache_n", 0) >= 0.9 * first_timings.get("prompt_n", 1)
                and second_timings.get("prompt_n", 10**9) < first_timings.get("prompt_n", 0),
                (first_timings, second_timings),
            )
        else:
            check(
                "the engine reported its cache (timings in the stream)",
                False,
                (first_timings, second_timings),
            )

    except AssertionError as exc:
        check(f"the run went to plan ({exc})", False)
    finally:
        say("7. teardown by pid")
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
