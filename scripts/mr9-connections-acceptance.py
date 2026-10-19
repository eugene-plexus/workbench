"""Connections (MR9b), live: an outside app reaches one person's own notes and documents, and
nobody else's.

The unit checks drive Workbench against a fake gateway. What only a live run shows is the whole
path: a real Workbench process, a real gateway and inference-driver, a real `llama-server` serving
a real search model (Qwen3-Embedding 0.6B, on the processor), recording proxies (what Workbench
sent the gateway, with whose proof, and what reached the engine), a stand-in for Eugene's sign-in
that can turn a person off, two people (Ada and Bo) and the install's owner, and the official MCP
SDK's own Streamable HTTP client, as Claude Code connects.

  0. isolated: every ambient EUGENE_PLEXUS_* variable dropped, owned ports free
  1. the stack: search model engine, driver, gateway, Workbench, three people signed in
  2. Ada and Bo keep notes and documents; meaning is built for both
  3. connections: each tool offered only with its right, and refused when called without it
  4. Ada's search finds her notes and passages by meaning, through the gateway with her proof
     and never Bo's; Bo's finds none of hers; her document's id is missing to him
  5. a suggestion waits in her inbox, named by its connection; a secret is refused
  6. the owner, reading people's chats, sees and revokes none of her connections
  7. the door: no token in the address, no foreign page, no cookie, one answer for every bad token
  8. revoking stops the next call and takes the unkept suggestions with it
  9. Eugene turning Ada off stops her connections; the install's switch stops everyone's
 10. no token in the database; Workbench's log has no query or note words
 11. teardown by pid; no owned port still listening

**Safe beside a live install**: every ambient EUGENE_PLEXUS_* variable is dropped, ports are
8470-8478, teardown is by pid.

Run with the Workbench interpreter:
  EP_LLAMA_SERVER=.../llama-server.exe EP_EMBED_FILE=.../Qwen3-Embedding-0.6B-Q8_0.gguf \\
    workbench/.venv/Scripts/python.exe workbench/scripts/mr9-connections-acceptance.py
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.client as httpclient
import json
import os
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
import httpx2
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from joserfc import jwt
from joserfc.jwk import RSAKey
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(os.environ.get("EP_ROOT", "D:/py/eugene-plexus"))
HERE = Path(__file__).resolve().parent.parent
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
LLAMA = Path(
    os.environ.get(
        "EP_LLAMA_SERVER",
        str(
            Path.home() / ".eugene-plexus" / "engines" / "llama_cpp" / "b10948" / "llama-server.exe"
        ),
    )
)
EMBED_FILE = Path(
    os.environ.get(
        "EP_EMBED_FILE",
        str(
            Path.home() / ".eugene-plexus" / "acceptance-models" / "Qwen3-Embedding-0.6B-Q8_0.gguf"
        ),
    )
)
(GW_PORT, AGENT_PORT, DRV_PORT, ENGINE, ENGINE_PROXY, WB_PORT, EUGENE_PORT, GW_PROXY, SPARE) = (
    range(8470, 8479)
)
OWNED = [GW_PORT, AGENT_PORT, DRV_PORT, ENGINE, ENGINE_PROXY, WB_PORT, EUGENE_PORT, GW_PROXY]
WORKBENCH = f"http://127.0.0.1:{WB_PORT}"
MCP = f"{WORKBENCH}/mcp"
SEARCH = "mr9-search"
CLIENT_ID, CLIENT_SECRET, ADMIN_TOKEN = "mr9-client", "mr9-client-secret", "mr9-admin-token"

ADA_NOTE = "Measures everything in metric units, kilograms and centimetres."
BO_NOTE = "Keeps the workshop lathe oiled every Friday afternoon."
ADA_WORD, BO_WORD = "zzyzxquux", "plumbagowort"
ADA_DOC = (
    "# Allotment\n\nThe rhubarb bed sits behind the greenhouse. "
    f"The gate code for the allotment shed is {ADA_WORD}.\n\n"
    "Tomatoes are watered each evening in July.\n"
)
BO_DOC = (
    "# Boat\n\nThe dinghy is kept on the slipway trailer. "
    f"The padlock combination for the boathouse is {BO_WORD}.\n"
)
SECRET = "Tr0ub4dor&3xK9"
ADA_QUERY = "how heavy things are, which units"
DOC_QUERY = "what opens the shed at the vegetable garden"

FAILURES = 0
PASSES = 0


def say(text: str) -> None:
    print(f"\n== {text}", flush=True)


def check(claim: str, passed: bool, detail: object = "") -> bool:
    global FAILURES, PASSES
    if passed:
        PASSES += 1
        print(f"PASS  {claim}", flush=True)
    else:
        FAILURES += 1
        print(f"FAIL  {claim}  -- {detail}", flush=True)
    return passed


def note(text: str) -> None:
    print(f"      {text}", flush=True)


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def until(condition: Any, seconds: float, every: float = 0.5) -> Any:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        value = condition()
        if value:
            return value
        time.sleep(every)
    return condition() or None


# --------------------------------------------------------------------------- #
# What each hop was sent
# --------------------------------------------------------------------------- #

#: Every embeddings request Workbench sent the gateway: the person token it carried.
GW_EMBEDS: list[str | None] = []
#: How many embeddings requests reached the engine.
ENGINE_EMBEDS: list[int] = []
LOCK = threading.Lock()


class Quiet(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass


def make_proxy(upstream: int, gateway_side: bool) -> type[BaseHTTPRequestHandler]:
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _forward(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            if self.command == "POST" and self.path.split("?")[0] == "/v1/embeddings":
                with LOCK:
                    if gateway_side:
                        GW_EMBEDS.append(self.headers.get("x-eugene-person-token"))
                    else:
                        ENGINE_EMBEDS.append(1)
            hop = httpclient.HTTPConnection("127.0.0.1", upstream, timeout=600)
            skip = ("host", "content-length", "connection", "transfer-encoding")
            if gateway_side:
                # Recorded above, then dropped, as MR8's proxy does: this stack's gateway runs
                # standalone (no root to check a proof against), and a standalone gateway
                # refuses a person token that comes without an app key. U3's own acceptance
                # covers the root's check of it.
                skip = (*skip, "x-eugene-person-token")
            headers = {k: v for k, v in self.headers.items() if k.lower() not in skip}
            hop.request(self.command, self.path, body=body or None, headers=headers)
            answer = hop.getresponse()
            data = answer.read()
            self.send_response(answer.status)
            for key, value in answer.getheaders():
                if key.lower() not in ("transfer-encoding", "content-length", "connection"):
                    self.send_header(key, value)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            hop.close()

        do_GET = do_POST = do_PATCH = do_DELETE = _forward

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
                        "name": "mr9-search-driver",
                        "kind": "inference-driver",
                        "url": f"http://127.0.0.1:{DRV_PORT}/",
                        "status": "running",
                    }
                ]
            }
        elif path == "/v1/runtimes":
            body = {"runtimes": []}
        elif path == "/v1/node":
            body = {"name": "mr9-node", "enrolled": False}
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
# A stand-in for Eugene's sign-in, which can turn a person off at a refresh
# --------------------------------------------------------------------------- #

PEOPLE = {
    "operator": {"name": "Owner", "role": "operator", "username": "operator"},
    "p-ada": {"name": "Ada", "role": "member", "username": "ada"},
    "p-bo": {"name": "Bo", "role": "member", "username": "bo"},
}
EUGENE: dict[str, Any] = {"ttl": 3600, "disabled": set(), "refreshes": {}, "issued": 0}


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

    def tokens(sub: str, nonce: str | None) -> dict[str, Any]:
        EUGENE["issued"] += 1
        refresh = secrets.token_urlsafe(16)
        EUGENE["refreshes"][refresh] = sub
        return {
            # Whose proof it is, readable at the gateway's door.
            "access_token": f"at-{sub}-{EUGENE['issued']}",
            "token_type": "Bearer",
            "expires_in": EUGENE["ttl"],
            "id_token": id_token(sub, nonce),
            "refresh_token": refresh,
        }

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
            return tokens(found["sub"], found["nonce"])
        if form["grant_type"] == "refresh_token":
            sub = EUGENE["refreshes"].get(str(form.get("refresh_token", "")))
            if sub is None or sub in EUGENE["disabled"]:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return tokens(sub, None)
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    @app.post("/oidc/revoke")
    async def revoke() -> Any:
        return {}

    return app


# --------------------------------------------------------------------------- #
# A person's browser, and an app's MCP client
# --------------------------------------------------------------------------- #


class Person:
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
        headers.update(kw.pop("headers", {}))
        return self.http.request(method, path, headers=headers, **kw)

    def get(self, path: str, **kw: Any) -> Any:
        return self.call("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.call("POST", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.call("DELETE", path, **kw)

    def connect(self, name: str, **rights: bool) -> dict[str, Any]:
        made = self.post(
            "/api/connections",
            json={
                "name": name,
                "rights": {"notes": False, "documents": False, "suggest": False, **rights},
            },
        )
        assert made.status_code == 201, made.text
        return dict(made.json())


async def _with_client(token: str, work: Any) -> Any:
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx2.AsyncClient(trust_env=False, timeout=120, headers=headers) as http,
        Client(streamable_http_client(MCP, http_client=http), cache=None) as client,
    ):
        return await work(client)


def tools_of(token: str) -> list[str]:
    async def go(client: Any) -> list[str]:
        return sorted(t.name for t in (await client.list_tools()).tools)

    return list(asyncio.run(_with_client(token, go)))


def use(token: str, name: str, **arguments: Any) -> Any:
    async def go(client: Any) -> Any:
        return await client.call_tool(name, arguments)

    return asyncio.run(_with_client(token, go))


def raw(token: str | None, *, query: str = "", headers: dict[str, str] | None = None) -> Any:
    """One plain JSON-RPC request to the door, as anything on the network could send."""
    sent = {"Accept": "application/json, text/event-stream", **(headers or {})}
    if token is not None:
        sent["Authorization"] = f"Bearer {token}"
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mr9-probe", "version": "0"},
        },
    }
    return httpx.post(MCP + query, json=body, headers=sent, trust_env=False, timeout=30)


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    base = {k: v for k, v in os.environ.items() if not k.startswith("EUGENE_PLEXUS_")}
    work = Path(tempfile.mkdtemp(prefix="mr9-connections-"))
    logs = work / "logs"
    logs.mkdir()
    procs: list[subprocess.Popen[bytes]] = []

    def start(argv: list[str], log: str, env: dict[str, str]) -> subprocess.Popen[bytes]:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=open(logs / log, "wb"),  # noqa: SIM115 - closed with the process
            stderr=subprocess.STDOUT,
            env=env,
        )
        procs.append(proc)
        return proc

    say("0. isolated")
    missing = [p for p in (LLAMA, EMBED_FILE, GW_PY, DRV_PY, WB_PY) if not p.exists()]
    if not check(
        "the engine, the search model and every interpreter are here", not missing, missing
    ):
        return 1
    taken = [p for p in OWNED if listening(p)]
    if not check(f"ports {OWNED[0]}-{OWNED[-1]} are free", not taken, taken):
        return 1
    check(
        "no ambient EUGENE_PLEXUS_* variable reaches anything this run starts",
        not any(k.startswith("EUGENE_PLEXUS_") for k in base),
    )
    try:
        return run(start, base, work, logs)
    finally:
        say("11. teardown")
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        children = [p for p in (GW_PORT, DRV_PORT, ENGINE, WB_PORT) if listening(p)]
        check("no port a child process held is still listening", not children, children)
        print(f"\n{PASSES} PASS, {FAILURES} FAIL  (logs in {logs})", flush=True)


def run(start: Any, base: dict[str, str], work: Path, logs: Path) -> int:
    say("1. the stack")
    threading.Thread(target=serve, args=(Agent, AGENT_PORT), daemon=True).start()
    threading.Thread(
        target=serve, args=(make_proxy(ENGINE, False), ENGINE_PROXY), daemon=True
    ).start()
    threading.Thread(target=serve, args=(make_proxy(GW_PORT, True), GW_PROXY), daemon=True).start()
    config = uvicorn.Config(eugene_app(), host="127.0.0.1", port=EUGENE_PORT, log_level="error")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()
    cpu = {**base, "CUDA_VISIBLE_DEVICES": "-1", "GGML_VK_VISIBLE_DEVICES": ""}
    start(
        [str(LLAMA), "-m", str(EMBED_FILE), "--host", "127.0.0.1", "--port", str(ENGINE),
         "--embeddings", "-ngl", "0", "-t", "8", "-c", "4096", "-np", "4",
         "-b", "2048", "-ub", "2048"],
        "llama.log",
        cpu,
    )  # fmt: skip
    (work / "driver.yaml").write_text(
        f"provider: openai_compat_custom\nbaseUrl: http://127.0.0.1:{ENGINE_PROXY}\n"
        f"modelId: {SEARCH}\nbackendLocality: local\nrequestTimeoutSeconds: 600\n",
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
            "EUGENE_PLEXUS_DRIVER_BIND_PORT": str(DRV_PORT),
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

    def search_model_listed() -> bool:
        try:
            listed = httpx.get(f"http://127.0.0.1:{GW_PORT}/v1/models", timeout=5, trust_env=False)
            data = {m["id"]: m.get("x_eugene_plexus") or {} for m in listed.json()["data"]}
        except (httpx.HTTPError, ValueError, KeyError):
            return False
        info = data.get(SEARCH) or {}
        return (
            "embeddings" in (info.get("surfaces") or []) and (info.get("ready_backends") or 0) > 0
        )

    if not check(
        "the gateway lists the search model, running here",
        bool(until(search_model_listed, 300, 1.0)),
        logs,
    ):
        return 1
    static = work / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>Workbench</title>", encoding="utf-8")
    (work / "app_key").write_text("mr9-app-key", encoding="utf-8")
    (work / "oidc_secret").write_text(CLIENT_SECRET, encoding="utf-8")
    data_dir = work / "data"
    start(
        [str(WB_PY), "-m", "eugene_plexus_workbench"],
        "workbench.log",
        {
            **base,
            "PYTHONPATH": str(WB / "src"),
            "EUGENE_PLEXUS_APP_BIND_HOST": "127.0.0.1",
            "EUGENE_PLEXUS_APP_BIND_PORT": str(WB_PORT),
            "EUGENE_PLEXUS_APP_DATA_DIR": str(data_dir),
            "EUGENE_PLEXUS_APP_KEY_FILE": str(work / "app_key"),
            "EUGENE_PLEXUS_APP_ADMIN_TOKEN": ADMIN_TOKEN,
            "EUGENE_PLEXUS_APP_GATEWAY_URL": f"http://127.0.0.1:{GW_PROXY}",
            "EUGENE_PLEXUS_APP_OIDC_ISSUER": f"http://127.0.0.1:{EUGENE_PORT}/oidc",
            "EUGENE_PLEXUS_APP_OIDC_CLIENT_ID": CLIENT_ID,
            "EUGENE_PLEXUS_APP_OIDC_SECRET_FILE": str(work / "oidc_secret"),
            "EUGENE_PLEXUS_APP_STATIC_DIR": str(static),
        },
    )
    if not check("Workbench listens", bool(until(lambda: listening(WB_PORT), 90)), logs):
        return 1
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

    check(
        "the owner reads people's chats (W13 on)", settings(ownerReadsChats=True).status_code == 200
    )

    say("2. notes and documents")
    for person, text in ((ada, ADA_NOTE), (bo, BO_NOTE)):
        made = person.post("/api/memory/notes", json={"text": text})
        check(f"{person.sub} keeps a note", made.status_code in (200, 201), made.text)
    pinned = ada.post(
        "/api/memory/notes", json={"text": "Answer in short paragraphs.", "kind": "instruction"}
    )
    check("Ada keeps an instruction (always used)", pinned.status_code in (200, 201), pinned.text)
    for person, name, text in ((ada, "allotment.md", ADA_DOC), (bo, "boat.md", BO_DOC)):
        kept = person.post(
            "/api/documents", files=[("files", (name, text.encode(), "text/markdown"))]
        )
        check(f"{person.sub} keeps {name}", kept.status_code == 201, kept.text)

    def meaning_ready(person: Person, question: str) -> Any:
        out = person.post("/api/documents/test", json={"question": question}).json()
        return out if "meaning" in str(out.get("mode")) and out.get("passages") else None

    ada_ready = until(lambda: meaning_ready(ada, DOC_QUERY), 300, 2.0)
    bo_ready = until(lambda: meaning_ready(bo, "where is the dinghy kept"), 300, 2.0)
    check(
        "both people's documents are searched by meaning and words",
        bool(ada_ready) and bool(bo_ready),
        (ada_ready or {}).get("mode"),
    )

    say("3. connections, and what each may do")
    a_all = ada.connect("Claude Code on Ada's laptop", notes=True, documents=True, suggest=True)
    a_notes = ada.connect("Notes only", notes=True)
    b_all = bo.connect("Bo's Codex", notes=True, documents=True, suggest=True)
    check(
        "a connection's token is shown once, as wbc_ with Workbench's address",
        a_all["token"].startswith("wbc_") and a_all["address"] == MCP,
        a_all["address"],
    )
    listed = tools_of(a_all["token"])
    check(
        "a connection with every right is offered the four tools",
        listed == ["read_document", "search_documents", "search_notes", "suggest_note"],
        listed,
    )
    check(
        "a notes-only connection is offered search_notes alone",
        tools_of(a_notes["token"]) == ["search_notes"],
    )
    refused = use(a_notes["token"], "search_documents", query=DOC_QUERY)
    check(
        "and calling search_documents anyway is refused",
        refused.is_error and "no tool named" in refused.content[0].text,
        refused,
    )

    say("4. her own notes and passages, by meaning, with her proof")
    before = len(GW_EMBEDS)
    found = use(a_all["token"], "search_notes", query=ADA_QUERY)
    notes_found = [n["text"] for n in (found.structured_content or {}).get("notes", [])]
    check(
        "Ada's connection finds her note by meaning (no word of the query is in it), "
        "with her instruction first",
        not found.is_error
        and ADA_NOTE in notes_found
        and notes_found[0] == "Answer in short paragraphs."
        and BO_NOTE not in notes_found
        and found.structured_content.get("how") == "meaning and words",
        found.structured_content,
    )
    passages = use(a_all["token"], "search_documents", query=DOC_QUERY)
    got = (passages.structured_content or {}).get("passages", [])
    check(
        "her documents search finds the passage with her code word, by meaning",
        any(ADA_WORD in p["text"] for p in got)
        and "meaning" in str(passages.structured_content.get("mode"))
        and not any(BO_WORD in p["text"] for p in got),
        passages.structured_content,
    )
    with LOCK:
        proofs = GW_EMBEDS[before:]
    check(
        "her queries were embedded through the gateway with her own proof, never another's",
        bool(proofs) and all(p is not None and p.startswith("at-p-ada-") for p in proofs),
        proofs,
    )
    check("and the search model engine answered them", len(ENGINE_EMBEDS) > 0, len(ENGINE_EMBEDS))
    ada_doc = next((p["documentId"] for p in got if ADA_WORD in p["text"]), "")
    read = use(a_all["token"], "read_document", document_id=ada_doc)
    check(
        "read_document gives her the document's text",
        not read.is_error and ADA_WORD in read.structured_content["text"],
        read,
    )
    theirs = use(b_all["token"], "search_documents", query=f"gate code allotment shed {ADA_WORD}")
    check(
        "Bo's connection finds none of her passages",
        not any(ADA_WORD in p["text"] for p in theirs.structured_content.get("passages", [])),
        theirs.structured_content,
    )
    bo_notes = use(b_all["token"], "search_notes", query="metric units kilograms")
    check(
        "nor her notes",
        ADA_NOTE not in [n["text"] for n in bo_notes.structured_content.get("notes", [])],
        bo_notes.structured_content,
    )
    hers = use(b_all["token"], "read_document", document_id=ada_doc)
    made_up = use(b_all["token"], "read_document", document_id="0" * 32)
    check(
        "her document's id answers Bo as a document that does not exist",
        hers.is_error and made_up.is_error and hers.content[0].text == made_up.content[0].text,
        (hers.content[0].text, made_up.content[0].text),
    )

    say("5. a suggestion waits for her")
    offered = use(a_all["token"], "suggest_note", text="Grows rhubarb at the allotment.")
    memory = ada.get("/api/memory").json()
    waiting = [s for s in memory["suggestions"] if s.get("sourceConnection")]
    check(
        "suggest_note lands in her inbox, named by its connection, and is not kept",
        not offered.is_error
        and [s["text"] for s in waiting] == ["Grows rhubarb at the allotment."]
        and waiting[0]["sourceConnection"] == "Claude Code on Ada's laptop"
        and all(
            n["text"] != "Grows rhubarb at the allotment."
            for n in memory["sections"]["always"] + memory["sections"]["other"]
        ),
        waiting,
    )
    secret = use(a_all["token"], "suggest_note", text=f"My password is {SECRET}")
    check("a secret is refused", secret.is_error, secret)
    check(
        "Bo's inbox has nothing of hers",
        all(
            s.get("sourceConnection") != "Claude Code on Ada's laptop"
            for s in bo.get("/api/memory").json()["suggestions"]
        ),
    )

    say("6. the owner")
    owners = owner.get("/api/connections").json()["connections"]
    check("the owner, reading people's chats, lists none of her connections", owners == [], owners)
    check(
        "nor can revoke one",
        owner.delete(f"/api/connections/{a_all['connection']['id']}").status_code == 404,
    )
    own_memory = owner.get("/api/memory").json()
    texts = [n["text"] for n in own_memory["sections"]["always"] + own_memory["sections"]["other"]]
    check("nor reads her notes", ADA_NOTE not in texts, texts)

    say("7. the door")
    check("the door answers a good token", raw(b_all["token"]).status_code == 200)
    in_address = raw(None, query=f"?token={b_all['token']}")
    check("a token in the address is refused (400)", in_address.status_code == 400, in_address.text)
    foreign = raw(b_all["token"], headers={"Origin": "https://evil.example"})
    check("a page from another site is refused (403)", foreign.status_code == 403, foreign.text)
    cookie = ada.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={"Accept": "application/json, text/event-stream"},
    )
    check(
        "her cookie and page secret, without a token, open nothing (401)", cookie.status_code == 401
    )
    bad = [raw("wbc_" + "Z" * 43), raw("nonsense"), raw(None)]
    check(
        "every bad token gets the same 401 and the same words",
        {r.status_code for r in bad} == {401} and len({r.text for r in bad}) == 1,
        [r.text for r in bad],
    )

    say("8. revoking")
    gone = ada.delete(f"/api/connections/{a_notes['connection']['id']}")
    after = raw(a_notes["token"])
    check(
        "a revoked connection's next call is refused, in the words a made-up token gets",
        gone.status_code == 204 and after.status_code == 401 and after.text == bad[0].text,
        after.text,
    )
    ada.delete(f"/api/connections/{a_all['connection']['id']}")
    left = [s for s in ada.get("/api/memory").json()["suggestions"] if s.get("sourceConnection")]
    check("revoking takes its unkept suggestions with it", left == [], left)

    say("9. Eugene's word, and the owner's switch")
    EUGENE["ttl"] = 1
    ada2 = Person("p-ada")
    ada2.sign_in()
    a_new = ada2.connect("Ada's second app", notes=True)
    time.sleep(1.5)
    check(
        "a fresh connection of hers works while Eugene accepts her",
        raw(a_new["token"]).status_code == 200,
    )
    EUGENE["disabled"].add("p-ada")
    time.sleep(1.5)
    stopped = raw(a_new["token"])
    check(
        "once Eugene turns her off, her connection stops at its next check, with words",
        stopped.status_code == 401 and ("Sign in" in stopped.text or "Eugene" in stopped.text),
        stopped.text,
    )
    EUGENE["disabled"].clear()
    EUGENE["ttl"] = 3600
    check("the owner turns connections off", settings(connections=False).status_code == 200)
    off = raw(b_all["token"])
    check(
        "every connection stops (403, with words)",
        off.status_code == 403 and "turned" in off.text,
        off.text,
    )
    settings(connections=None)
    check("and works again once they are on", raw(b_all["token"]).status_code == 200)

    say("10. what is kept, and what is logged")
    stored = b"".join(p.read_bytes() for p in data_dir.glob("workbench.sqlite3*"))
    check(
        "the database holds no connection's token",
        all(
            t.encode() not in stored
            for t in (a_all["token"], a_notes["token"], b_all["token"], a_new["token"])
        ),
    )
    log_text = (logs / "workbench.log").read_text(encoding="utf-8", errors="replace")
    leaked = [
        w for w in (ADA_QUERY, DOC_QUERY, ADA_WORD, "rhubarb", SECRET, ADA_NOTE) if w in log_text
    ]
    check("Workbench's log has no query, note or document words", not leaked, leaked)
    check("and no token", "wbc_" not in log_text)
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
