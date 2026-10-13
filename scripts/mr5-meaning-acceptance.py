"""Search by meaning (MR5), live: documents found by what they say, with real models.

The unit checks drive Workbench against a fake gateway and a stand-in for meaning. What only a
live run shows is that a real embedding model, reached through a real gateway and a real
inference driver, turns a person's real documents into something a question that shares none of
their words can find; that the numbers it makes are checked against the model that made them; and
that the words search never waits for any of it. The people are real sign-ins against a stand-in
for Eugene's sign-in (OpenID Connect, as the unit checks use).

  0. isolated: every ambient EUGENE_PLEXUS_* variable dropped, owned ports free
  1. the stack: a chat engine (Qwen3-0.6B), a search model engine (Qwen3-Embedding-0.6B, started
     with --embeddings, CPU) and one for another (nomic), each behind its own driver, one gateway,
     Workbench; three people
  2. Eugene's own design documents into My documents: searchable by words first, then "Adding
     meaning: n of N passages, about X left", then ready; one request in the air at a time, and
     never more than 30 started in a minute
  3. the search model is stopped in the middle of the import: the screen names it, a question
     is still answered from the words, a chat is too, and the import resumes when it is back
  4. a question phrased with none of a passage's words finds it by meaning, and the passage says
     so (`via: meaning`); the order when both ways find a passage
  5. the quality gate: sixty questions written by someone who had not read the documents, each
     labelled with the section that answers it; hybrid must beat the words alone and meaning
     alone on recall@5 (recall@5, MRR@5 and paired-bootstrap intervals are printed); then the
     floor under which the nearest passage is no evidence, measured on questions the documents
     answer, on unrelated ones (twelve of them held out), and on ones about the same subject
  6. a real model answers a chat from what meaning found
  7. another search model is started and the first stopped (its engine and its driver, so the
     gateway stops listing it as running): meaning is paused for each person, nothing of the new
     model is stored in their index, words go on; Rebuild adds meaning again with the new model,
     and the old numbers are gone; the time left a large rebuild shows is checked against the
     time that was left
  8. teardown by pid; no owned port still listening

**Safe beside a live install**: every ambient EUGENE_PLEXUS_* variable is dropped, ports are
8500-8515 (outside 8440-8487, and the live install's 8279-8283 and 8079-8083), the engines run on
the CPU only, and teardown is by pid. Never `pkill -f eugene_plexus_`: this box is a worker node.

Run with the Workbench interpreter (it has httpx, fastapi, uvicorn, joserfc, numpy, pypdf):
  workbench/.venv/Scripts/python.exe workbench-mr/scripts/mr5-meaning-acceptance.py
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import itertools
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
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urlsplit

import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from joserfc import jwt
from joserfc.jwk import RSAKey

HERE = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("EP_ROOT", str(HERE.parent)))
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
#: The acceptance models live in one folder (EP_MODELS_DIR; default ~/.eugene-plexus/acceptance-models).
MODELS = Path(
    os.environ.get("EP_MODELS_DIR", str(Path.home() / ".eugene-plexus" / "acceptance-models"))
)
LLAMA = Path(
    os.environ.get(
        "EP_LLAMA_SERVER",
        str(
            Path.home() / ".eugene-plexus" / "engines" / "llama_cpp" / "b10948" / "llama-server.exe"
        ),
    )
)
CHAT_FILE = Path(os.environ.get("EP_MODEL_FILE", str(MODELS / "Qwen3-0.6B-Q4_K_M.gguf")))
#: The search model of the run of record, Qwen3-Embedding-0.6B at Q8_0, beside the other
#: acceptance models.
QWEN_FILE = Path(os.environ.get("EP_QWEN_EMBED", str(MODELS / "Qwen3-Embedding-0.6B-Q8_0.gguf")))
NOMIC_FILE = Path(os.environ.get("EP_NOMIC_EMBED", str(MODELS / "nomic-embed-text-v1.5.Q8_0.gguf")))
#: Eugene's own design documents are the corpus. The run of record used a copy of the 76 files as
#: they are at specs c5f725d (set EP_DESIGN_DOCS to a checkout of that commit's docs/design to
#: repeat it): the questions' labels are headings in them, and a check says so before anything
#: starts, so a later edit that renames a heading is told, not hidden.
DESIGN = Path(os.environ.get("EP_DESIGN_DOCS", str(ROOT / "specs" / "docs" / "design")))
#: Sixty questions, each labelled with the file and the section that answers it, written from the
#: headings alone by someone who had not read the documents (design 6.2, MR5).
QUESTIONS = Path(
    os.environ.get("EP_QUESTIONS", str(HERE / "scripts" / "mr5-meaning-questions.json"))
)
#: Threads for the engines that embed: the import is as fast as they are.
EMBED_THREADS = os.environ.get("EP_EMBED_THREADS", "12")

sys.path.insert(0, str(HERE))

(
    GW_PORT,
    AGENT_PORT,
    DRV_CHAT,
    ENGINE_CHAT,
    ENGINE_CHAT_PROXY,
    DRV_QWEN,
    ENGINE_QWEN,
    QWEN_PROXY,
    DRV_NOMIC,
    ENGINE_NOMIC,
    NOMIC_PROXY,
    WB_PORT,
    EUGENE_PORT,
    GW_PROXY,
) = range(8500, 8514)
OWNED = [
    GW_PORT, AGENT_PORT, DRV_CHAT, ENGINE_CHAT, ENGINE_CHAT_PROXY, DRV_QWEN, ENGINE_QWEN,
    QWEN_PROXY, DRV_NOMIC, ENGINE_NOMIC, NOMIC_PROXY, WB_PORT, EUGENE_PORT, GW_PROXY,
]  # fmt: skip
#: Ports a thread of this script serves; the rest belong to child processes.
OURS = (AGENT_PORT, ENGINE_CHAT_PROXY, QWEN_PROXY, NOMIC_PROXY, EUGENE_PORT, GW_PROXY)
WORKBENCH = f"http://127.0.0.1:{WB_PORT}"
CHAT = "mr5-chat"
QWEN = "Qwen3-Embedding-0.6B-Q8_0"
NOMIC = "nomic-embed-text-v1.5.Q8_0"

CLIENT_ID, CLIENT_SECRET, ADMIN_TOKEN = "mr5-client", "mr5-client-secret", "mr5-admin-token"
WORDS_ONLY = "Searching by words only. No search model is running."

FAILURES = 0
PASSES = 0
NUMBERS: dict[str, Any] = {}


def say(text: str) -> None:
    print(f"\n== {text}", flush=True)


def check(claim: str, passed: bool, detail: object = "") -> bool:
    global FAILURES, PASSES
    if passed:
        PASSES += 1
    else:
        FAILURES += 1
    shown = f"  ({str(detail)[:600]})" if detail != "" and not passed else ""
    print(f"  {'PASS' if passed else 'FAIL'}  {claim}{shown}", flush=True)
    return passed


def note(text: str) -> None:
    print(f"  NOTE  {text}", flush=True)


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def person_key(sub: str) -> str:
    return hashlib.sha256(sub.encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------- #
# What each hop was sent
# --------------------------------------------------------------------------- #

#: Every chat request Workbench sent the gateway, and every one the chat engine received.
WB_SENT: list[dict[str, Any]] = []
ENGINE_BODIES: list[dict[str, Any]] = []
#: Every embeddings request that reached each search model engine: what it was asked, when it
#: started and ended, and how many were in the air when it began.
EMBED_LOG: dict[str, list[dict[str, Any]]] = {"qwen": [], "nomic": []}
INFLIGHT = {"qwen": 0, "nomic": 0}
LOCK = threading.Lock()


class Quiet(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass  # a probe that hangs up early is not news


def make_proxy(upstream: int, sink: list[dict[str, Any]]) -> type[BaseHTTPRequestHandler]:
    """A pass-through to `127.0.0.1:upstream` that keeps the body of every chat request."""

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
                if value := answer.getheader(key):
                    self.send_header(key, value)
            self.end_headers()
            while chunk := answer.read(4096):
                self.wfile.write(chunk)
                self.wfile.flush()
            hop.close()

        do_GET = _forward
        do_POST = _forward
        do_PATCH = _forward
        do_DELETE = _forward

    return Proxy


def embed_proxy(upstream: int, name: str) -> type[BaseHTTPRequestHandler]:
    """A pass-through that keeps, for every embeddings request, what it was asked (how many
    texts, the first of them), when it began and ended, and how many were in the air then."""

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _forward(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            entry: dict[str, Any] | None = None
            if self.command == "POST" and self.path.split("?")[0] == "/v1/embeddings":
                asked = json.loads(body).get("input")
                texts = asked if isinstance(asked, list) else [asked]
                with LOCK:
                    INFLIGHT[name] += 1
                    entry = {
                        "texts": [str(t) for t in texts],
                        "n": len(texts),
                        "t0": time.perf_counter(),
                        "t1": None,
                        "status": None,
                        "inflight": INFLIGHT[name],
                    }
                    EMBED_LOG[name].append(entry)
            try:
                hop = http.client.HTTPConnection("127.0.0.1", upstream, timeout=900)
                wanted = ("content-type", "accept", "authorization")
                headers = {k: v for k, v in self.headers.items() if k.lower() in wanted}
                hop.request(self.command, self.path, body=body or None, headers=headers)
                answer = hop.getresponse()
                data = answer.read()
                if entry is not None:
                    entry["status"] = answer.status
                self.send_response(answer.status)
                for key in ("Content-Type", "Cache-Control"):
                    if value := answer.getheader(key):
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                hop.close()
            finally:
                if entry is not None:
                    with LOCK:
                        entry["t1"] = time.perf_counter()
                        INFLIGHT[name] -= 1

        do_GET = _forward
        do_POST = _forward

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
                        "name": name,
                        "kind": "inference-driver",
                        "url": f"http://127.0.0.1:{port}/",
                        "status": "running",
                    }
                    for name, port in (
                        ("mr5-chat-driver", DRV_CHAT),
                        ("mr5-qwen-driver", DRV_QWEN),
                        ("mr5-nomic-driver", DRV_NOMIC),
                    )
                ]
            }
        elif path == "/v1/runtimes":
            body = {"runtimes": []}
        elif path == "/v1/node":
            body = {"name": "mr5-node", "enrolled": False}
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

    def patch(self, path: str, **kw: Any) -> Any:
        return self.call("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.call("DELETE", path, **kw)

    def add(self, *named: tuple[str, bytes, str]) -> Any:
        return self.post("/api/documents", files=[("files", n) for n in named])

    def documents(self) -> dict[str, Any]:
        found = self.get("/api/documents")
        assert found.status_code == 200, found.text
        return dict(found.json())

    def search(self) -> dict[str, Any]:
        return dict(self.documents()["search"])

    def test(self, question: str, **more: Any) -> dict[str, Any]:
        made = self.post("/api/documents/test", json={"question": question, **more})
        assert made.status_code == 200, made.text
        return dict(made.json())

    def wait_answer(self, chat: str, seconds: float = 300) -> dict[str, Any]:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            messages = self.get(f"/api/chats/{chat}").json()["messages"]
            if messages and messages[-1]["status"] != "running":
                return dict(messages[-1])
            time.sleep(0.3)
        raise AssertionError("the answer did not finish")


def until(condition: Any, seconds: float, every: float = 0.5) -> Any:
    """The condition's value once it is true, or None when `seconds` pass."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        value = condition()
        if value:
            return value
        time.sleep(every)
    return condition() or None


# --------------------------------------------------------------------------- #
# The questions, and what counts as finding the right section
# --------------------------------------------------------------------------- #


def squash(text: str) -> str:
    return " ".join(text.split())


def right_section(passage: dict[str, Any], question: dict[str, Any]) -> bool:
    """A passage answers a question when it is in the labelled file and the labelled heading is
    one of the headings above it (or is its own): the section, and whatever is under it."""
    if passage["name"] != question["file"]:
        return False
    parts = [squash(p) for p in (passage.get("heading") or "").split(" > ")]
    return squash(question["heading"]) in parts


def recall_at(
    found: list[list[dict[str, Any]]], questions: list[dict[str, Any]], k: int
) -> list[int]:
    return [
        int(any(right_section(p, q) for p in passages[:k]))
        for passages, q in zip(found, questions, strict=True)
    ]


def reciprocal_rank(
    found: list[list[dict[str, Any]]], questions: list[dict[str, Any]], k: int
) -> float:
    total = 0.0
    for passages, q in zip(found, questions, strict=True):
        for place, p in enumerate(passages[:k], start=1):
            if right_section(p, q):
                total += 1.0 / place
                break
    return total / len(questions)


def paired_bootstrap(
    a: list[int], b: list[int], rounds: int = 4000, seed: int = 7
) -> tuple[float, float, float]:
    """The mean of a - b, and its 95% interval, resampling the questions (paired)."""
    rng = np.random.default_rng(seed)
    diff = np.array(a) - np.array(b)
    means = [float(diff[rng.integers(0, len(diff), len(diff))].mean()) for _ in range(rounds)]
    return float(diff.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


def main() -> int:
    cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
    procs: list[subprocess.Popen[bytes]] = []
    work = Path(tempfile.mkdtemp(prefix="ep-mr5-"))
    logs = work / "logs"
    logs.mkdir()
    data = work / "workbench-data"
    wb_log_count = [0]
    engine_log_count = {"qwen": 0, "nomic": 0}

    def start(argv: list[str], log: str, env: dict[str, str]) -> subprocess.Popen[bytes]:
        handle = open(logs / log, "w", encoding="utf-8")  # noqa: SIM115
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, env=env
        )
        procs.append(proc)
        return proc

    def wait_for(port: int, seconds: float = 120) -> bool:
        return bool(until(lambda: listening(port), seconds))

    def kill(proc: subprocess.Popen[bytes]) -> None:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False
        )

    def index_path(sub: str) -> Path:
        return data / "knowledge" / person_key(sub) / "index.sqlite3"

    def vector_sizes(sub: str) -> dict[int, int]:
        """How many passages have a vector of each size in bytes, read from the person's own
        index file (an empty vector is a passage the search model refused)."""
        db = sqlite3.connect(index_path(sub), timeout=10)
        try:
            return {
                int(size): int(n)
                for size, n in db.execute(
                    "SELECT length(vec), COUNT(*) FROM chunks WHERE vec IS NOT NULL GROUP BY 1"
                )
            }
        finally:
            db.close()

    def built_with(sub: str) -> dict[str, Any] | None:
        db = sqlite3.connect(index_path(sub), timeout=10)
        try:
            row = db.execute("SELECT value FROM meta WHERE key = 'embedder'").fetchone()
        finally:
            db.close()
        return json.loads(row[0]) if row else None

    server: Any = None
    wb: list[subprocess.Popen[bytes]] = []
    engines: dict[str, subprocess.Popen[bytes]] = {}
    drivers: dict[str, subprocess.Popen[bytes]] = {}
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
            CHAT_FILE,
            QWEN_FILE,
            NOMIC_FILE,
            QUESTIONS,
            DESIGN,
        ):
            if not path.exists():
                check(f"{path} exists", False)
                return 1
        questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
        check(
            f"ports {OWNED[0]}-{OWNED[-1]} free; Workbench from {WB}; llama-server {LLAMA.parent.name}; "
            f"{len(questions)} questions from {QUESTIONS.name}",
            True,
        )

        def headings_of(name: str) -> set[str]:
            found_: set[str] = set()
            fenced = False
            for line in (DESIGN / name).read_text(encoding="utf-8").splitlines():
                if line.lstrip().startswith(("```", "~~~")):
                    fenced = not fenced
                    continue
                if not fenced and (m := re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)):
                    found_.add(squash(m.group(2)))
            return found_

        unlabelled = [q for q in questions if squash(q["heading"]) not in headings_of(q["file"])]
        check(
            f"every one of the {len(questions)} questions is labelled with a heading that is in its "
            f"file of {DESIGN.name}/",
            not unlabelled,
            unlabelled[:2],
        )

        say(
            "1. the stack: three engines (CPU), their drivers, the gateway, Workbench, three people"
        )
        threading.Thread(target=serve, args=(Agent, AGENT_PORT), daemon=True).start()
        threading.Thread(
            target=serve,
            args=(make_proxy(ENGINE_CHAT, ENGINE_BODIES), ENGINE_CHAT_PROXY),
            daemon=True,
        ).start()
        threading.Thread(
            target=serve, args=(embed_proxy(ENGINE_QWEN, "qwen"), QWEN_PROXY), daemon=True
        ).start()
        threading.Thread(
            target=serve, args=(embed_proxy(ENGINE_NOMIC, "nomic"), NOMIC_PROXY), daemon=True
        ).start()
        threading.Thread(
            target=serve, args=(make_proxy(GW_PORT, WB_SENT), GW_PROXY), daemon=True
        ).start()
        server = uvicorn.Server(
            uvicorn.Config(eugene_app(), host="127.0.0.1", port=EUGENE_PORT, log_level="error")
        )
        threading.Thread(target=server.run, daemon=True).start()
        cpu_only = {**base, "CUDA_VISIBLE_DEVICES": "-1", "GGML_VK_VISIBLE_DEVICES": ""}

        def engine_argv(kind: str) -> list[str]:
            if kind == "chat":
                return [
                    str(LLAMA), "-m", str(CHAT_FILE), "--host", "127.0.0.1", "--port", str(ENGINE_CHAT),
                    "-ngl", "0", "-t", "2", "-c", "8192", "-np", "1", "-b", "512", "-ub", "512",
                ]  # fmt: skip
            model, port = (QWEN_FILE, ENGINE_QWEN) if kind == "qwen" else (NOMIC_FILE, ENGINE_NOMIC)
            # What the agent's launch path gives a search model (MR3): --embeddings, four slots,
            # a physical batch as big as the logical one; the pooling is read from the file.
            return [
                str(LLAMA), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
                "--embeddings", "-ngl", "0", "-t", EMBED_THREADS,
                "-c", "8192" if kind == "nomic" else "4096", "-np", "4", "-b", "2048", "-ub", "2048",
            ]  # fmt: skip

        def start_engine(kind: str) -> subprocess.Popen[bytes]:
            if kind != "chat":
                engine_log_count[kind] += 1
            name = (
                "llama-chat.log" if kind == "chat" else f"llama-{kind}-{engine_log_count[kind]}.log"
            )
            proc = start(engine_argv(kind), name, cpu_only)
            engines[kind] = proc
            return proc

        def engine_ready(port: int, seconds: float = 180) -> bool:
            def up() -> bool:
                try:
                    return (
                        httpx.get(
                            f"http://127.0.0.1:{port}/health", timeout=3, trust_env=False
                        ).status_code
                        == 200
                    )
                except httpx.HTTPError:
                    return False

            return bool(until(up, seconds, every=1.0))

        start_engine("chat")
        start_engine("qwen")
        for name, drv_port, base_url, model_id in (
            ("chat", DRV_CHAT, f"http://127.0.0.1:{ENGINE_CHAT_PROXY}", CHAT),
            ("qwen", DRV_QWEN, f"http://127.0.0.1:{QWEN_PROXY}", QWEN),
            ("nomic", DRV_NOMIC, f"http://127.0.0.1:{NOMIC_PROXY}", NOMIC),
        ):
            (work / f"driver-{name}.yaml").write_text(
                f"provider: openai_compat_custom\nbaseUrl: {base_url}\nmodelId: {model_id}\n"
                "backendLocality: local\nrequestTimeoutSeconds: 600\n",
                encoding="utf-8",
                newline="\n",
            )
            drivers[name] = start(
                [str(DRV_PY), "-m", "eugene_plexus_inference_driver"],
                f"driver-{name}.log",
                {
                    **base,
                    "EUGENE_PLEXUS_DRIVER_CONFIG_FILE": str(work / f"driver-{name}.yaml"),
                    "EUGENE_PLEXUS_DRIVER_BIND_HOST": "127.0.0.1",
                    "EUGENE_PLEXUS_DRIVER_BIND_PORT": str(drv_port),
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
        for port in (DRV_CHAT, DRV_QWEN, DRV_NOMIC, GW_PORT, EUGENE_PORT):
            if not wait_for(port, 180):
                check(f"something listens on {port}; logs in {logs}", False)
                return 1
        if not check(
            "the chat engine and the search model engine are loaded",
            engine_ready(ENGINE_CHAT) and engine_ready(ENGINE_QWEN),
            logs,
        ):
            return 1

        def gateway_models() -> dict[str, dict[str, Any]]:
            try:
                listed = httpx.get(
                    f"http://127.0.0.1:{GW_PORT}/v1/models", timeout=5, trust_env=False
                ).json()
            except (httpx.HTTPError, ValueError):
                return {}
            return {m["id"]: m.get("x_eugene_plexus") or {} for m in listed.get("data") or []}

        def search_model_ready(model_id: str) -> bool:
            info = gateway_models().get(model_id) or {}
            return (
                "embeddings" in (info.get("surfaces") or [])
                and (info.get("ready_backends") or 0) > 0
            )

        known = until(
            lambda: (
                (gateway_models().get(CHAT) or {}).get("context_length") is not None
                and search_model_ready(QWEN)
            ),
            300,
            every=1.0,
        )
        listed_now = gateway_models()
        if not check(
            f"the gateway routes {CHAT} (and knows its window) and lists {QWEN} as a search model, "
            "running here",
            bool(known),
            {
                k: (v.get("surfaces"), v.get("locality"), v.get("ready_backends"))
                for k, v in listed_now.items()
            },
        ):
            return 1
        note(
            "the gateway lists: "
            + "; ".join(
                f"{k} surfaces={v.get('surfaces')} locality={v.get('locality')} "
                f"ready={v.get('ready_backends')}"
                for k, v in listed_now.items()
            )
        )

        static = work / "static"
        static.mkdir()
        (static / "index.html").write_text(
            "<!doctype html><title>Workbench</title>", encoding="utf-8"
        )
        (work / "app_key").write_text("mr5-app-key", encoding="utf-8")
        (work / "oidc_secret").write_text(CLIENT_SECRET, encoding="utf-8")

        def start_workbench() -> subprocess.Popen[bytes]:
            wb_log_count[0] += 1
            return start(
                [str(WB_PY), "-m", "eugene_plexus_workbench"],
                f"workbench-{wb_log_count[0]}.log",
                {
                    **base,
                    "PYTHONPATH": str(WB / "src"),
                    "EUGENE_PLEXUS_APP_BIND_HOST": "127.0.0.1",
                    "EUGENE_PLEXUS_APP_BIND_PORT": str(WB_PORT),
                    "EUGENE_PLEXUS_APP_DATA_DIR": str(data),
                    "EUGENE_PLEXUS_APP_KEY_FILE": str(work / "app_key"),
                    "EUGENE_PLEXUS_APP_ADMIN_TOKEN": ADMIN_TOKEN,
                    "EUGENE_PLEXUS_APP_GATEWAY_URL": f"http://127.0.0.1:{GW_PROXY}",
                    "EUGENE_PLEXUS_APP_OIDC_ISSUER": f"http://127.0.0.1:{EUGENE_PORT}/oidc",
                    "EUGENE_PLEXUS_APP_OIDC_CLIENT_ID": CLIENT_ID,
                    "EUGENE_PLEXUS_APP_OIDC_SECRET_FILE": str(work / "oidc_secret"),
                    "EUGENE_PLEXUS_APP_STATIC_DIR": str(static),
                },
            )

        wb.append(start_workbench())
        if not wait_for(WB_PORT, 60):
            check(f"Workbench listens on {WB_PORT}; logs in {logs}", False)
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

        schema = {
            f["key"]: f
            for f in httpx.get(
                f"{WORKBENCH}/v1/config/schema",
                headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
                trust_env=False,
            ).json()["fields"]
        }
        model_field = schema["documentsEmbeddingModel"]
        check(
            "the owner's Search model setting shows what it comes to: automatic, and the one in "
            "use, with the models Eugene lists as suggestions",
            model_field["default"] == "auto"
            and QWEN in model_field["suggestions"]
            and model_field["status"] == {"level": "info", "text": f"In use: {QWEN} (automatic)."},
            model_field,
        )
        for field_ in ("documentsExternalEmbedding", "documentsEmbedRequestsPerMinute"):
            check(
                f"{field_} is a setting with its default shown",
                field_ in schema and schema[field_]["default"] in (False, 30),
                schema.get(field_),
            )
        head0 = ada.search()
        check(
            "before any document is kept the header already says meaning is on (the search model is "
            "running, nothing is built yet)",
            head0["headline"] == "Searching by meaning and words."
            and head0["model"]["resolved"] == QWEN
            and head0["model"]["label"] == f"In use: {QWEN} (automatic)"
            and head0["model"]["builtWith"] is None,
            head0,
        )

        # ------------------------------------------------------------------ #
        say("2. Eugene's own design documents: words first, then meaning catches up, then ready")
        files = [(p.name, p.read_bytes(), "text/markdown") for p in sorted(DESIGN.glob("*.md"))]
        made = ada.add(*files)
        body = made.json()
        check(
            f"{len(files)} files are kept at once, each queued, and nothing is refused",
            made.status_code == 201
            and body["refused"] == []
            and len(body["documents"]) == len(files),
            made.text[:300],
        )
        started = time.perf_counter()
        timeline: list[dict[str, Any]] = []
        seen_states: dict[str, list[str]] = {}
        first_words_ready: dict[str, Any] | None = None
        first_embedding: dict[str, Any] | None = None
        eta_seen: list[float] = []
        messages_seen: set[str] = set()
        words_while_adding: dict[str, Any] | None = None
        stop_at = 350  # passages with meaning when the search model is stopped
        stopped_listing: dict[str, Any] | None = None

        def poll() -> dict[str, Any]:
            nonlocal first_words_ready, first_embedding, words_while_adding
            listed = ada.documents()
            counts: dict[str, int] = {}
            for d in listed["documents"]:
                counts[d["state"]] = counts.get(d["state"], 0) + 1
                trail = seen_states.setdefault(d["name"], [])
                if not trail or trail[-1] != d["state"]:
                    trail.append(d["state"])
            adding = listed["search"]["adding"]
            sample = {
                "t": round(time.perf_counter() - started, 1),
                "counts": counts,
                "embedded": listed["totals"]["embedded"],
                "passages": listed["totals"]["passages"],
                "adding": adding["state"],
            }
            timeline.append(sample)
            if adding["etaSeconds"] is not None:
                eta_seen.append(float(adding["etaSeconds"]))
            if adding["message"]:
                messages_seen.add(re.sub(r"[\d,]+", "N", str(adding["message"])))
            read_by_words = sum(counts.get(s, 0) for s in ("searchable", "embedding", "ready"))
            if (
                first_words_ready is None
                and read_by_words == len(files)
                and listed["totals"]["embedded"] < listed["totals"]["passages"]
            ):
                first_words_ready = sample
            if first_embedding is None and counts.get("embedding"):
                first_embedding = sample
            if (
                words_while_adding is None
                and first_words_ready is not None
                and listed["totals"]["embedded"] > 0
            ):
                words_while_adding = ada.test(
                    "device mapper transient failure retry", ways=["words"]
                )
            return listed

        listed = poll()
        while time.perf_counter() - started < 2400:
            listed = poll()
            if listed["totals"]["embedded"] >= stop_at or all(
                d["state"] in ("ready", "skipped", "failed") for d in listed["documents"]
            ):
                break
            time.sleep(1.0)
        check(
            "all the files could be found by words while meaning had barely begun: "
            f"every file read by t={first_words_ready['t'] if first_words_ready else '?'} s, with "
            f"{first_words_ready['embedded'] if first_words_ready else '?'} of "
            f"{first_words_ready['passages'] if first_words_ready else '?'} passages with meaning",
            first_words_ready is not None,
            timeline[-3:],
        )
        check(
            "a document was seen mid-way (embedding), with 'n of N passages' and a time left that "
            "was measured and never a constant",
            first_embedding is not None
            and any(
                re.fullmatch(r"Adding meaning: N of N passages(, about [\w .]+ left)?\.", m)
                for m in messages_seen
            )
            and len(set(round(e) for e in eta_seen)) > 3,
            (sorted(messages_seen)[:3], sorted(set(round(e) for e in eta_seen))[:8]),
        )
        note(
            f"{len(eta_seen)} estimates were shown; the first {eta_seen[:1]} s, the last {eta_seen[-1:]} s"
        )
        words_found = words_while_adding or {}
        check(
            "a question was answered from the words while meaning was being added (found, by words "
            "or both, with the words it matched shown)",
            words_found.get("status") in ("found", "nothing_matched")
            and {p["via"] for p in words_found.get("passages", [])} <= {"words", "both"},
            words_found.get("status"),
        )
        say("3. the search model is stopped in the middle of the import")
        embedded_at_stop = listed["totals"]["embedded"]
        passages_total = listed["totals"]["passages"]
        if embedded_at_stop < passages_total:
            kill(engines["qwen"])
            gone_head = until(
                lambda: (
                    (h := ada.search())["code"] in ("not_running", "none_set_up", "unreachable")
                    and h
                ),
                120,
                every=1.0,
            )
            check(
                "the screen says the search model stopped, naming it, and that documents are searched by words",
                bool(gone_head)
                and gone_head["code"] == "not_running"
                and gone_head["headline"] == WORDS_ONLY
                and gone_head["detail"]
                == f"Searching by words only: the search model {QWEN} is not running. "
                "Start it in Eugene's console under Backends.",
                gone_head,
            )
            frozen = ada.documents()["totals"]["embedded"]
            time.sleep(8)
            now_embedded = ada.documents()
            check(
                "no meaning is added while it is stopped, and the header says it is waiting",
                now_embedded["totals"]["embedded"] == frozen
                and now_embedded["search"]["adding"]["state"] == "waiting"
                and "is not running" in (now_embedded["search"]["adding"]["message"] or ""),
                (frozen, now_embedded["totals"]["embedded"], now_embedded["search"]["adding"]),
            )
            stopped_listing = now_embedded
            wordy = ada.test("how does the gateway retry a failed request on another backend")
            check(
                "a question is still answered from the words, and says meaning did not run and why",
                wordy["status"] == "found"
                and wordy["mode"] == "words"
                and {p["via"] for p in wordy["passages"]} == {"words"}
                and wordy["meaning"]["used"] is False
                and wordy["meaning"]["code"] == "not_running"
                and "is not running" in (wordy["meaning"]["message"] or ""),
                {k: wordy[k] for k in ("status", "mode", "meaning")},
            )
            chat_id = ada.post("/api/chats", json={"model": CHAT}).json()["id"]
            ada.patch(f"/api/chats/{chat_id}", json={"settings": {"documents": "on"}})
            sent = ada.post(
                f"/api/chats/{chat_id}/messages",
                json={
                    "content": "how does the gateway retry a failed request on another backend? /no_think"
                },
            )
            reply = ada.wait_answer(chat_id) if sent.status_code == 201 else {}
            retrieval = reply.get("retrieval") or {}
            check(
                "a chat with Documents on is answered by the real model from the words, and the answer "
                "says it was searched by words only",
                reply.get("status") == "done"
                and retrieval.get("status") == "used"
                and retrieval.get("mode") == "words"
                and retrieval["meaning"]["used"] is False
                and any(n["code"] == "meaning_not_running" for n in retrieval.get("notes", [])),
                {k: retrieval.get(k) for k in ("status", "mode", "meaning", "notes")},
            )
            say("3b. the search model is back: the import resumes where it stopped")
            start_engine("qwen")
            back = until(
                lambda: engine_ready(ENGINE_QWEN, 1) and search_model_ready(QWEN), 240, every=1.0
            )
            resumed = until(
                lambda: (
                    ada.documents()["totals"]["embedded"] > frozen and ada.search()["code"] == "ok"
                ),
                240,
                every=1.0,
            )
            check(
                "the engine is back, the header says meaning and words again, and meaning is being "
                "added from where it stopped",
                bool(back) and bool(resumed),
                ada.search(),
            )
        else:
            note("the import was already complete before the stop point: the stop was not run")

        say("2b. ... and on to ready: every file, every passage")
        end = time.perf_counter() + 3000
        while time.perf_counter() < end:
            listed = poll()
            if all(d["state"] in ("ready", "skipped", "failed") for d in listed["documents"]):
                break
            time.sleep(2.0)
        totals = listed["totals"]
        took = time.perf_counter() - started
        states_final = {d["state"] for d in listed["documents"]}
        check(
            "every file is ready, and every passage has its meaning",
            states_final == {"ready"}
            and totals["embedded"] == totals["passages"]
            and totals["owed"] == 0,
            (states_final, totals),
        )
        # A poll sees a state only if it lasts longer than the poll, and a file is read in a
        # fraction of a second: so the claim is that no file ever moved backwards, and that each
        # of the states a person sees was seen in some file.
        pipeline = {"queued": 0, "reading": 1, "searchable": 2, "embedding": 3, "ready": 4}
        went_back = {
            name: trail
            for name, trail in seen_states.items()
            if any(pipeline[a] > pipeline[b] for a, b in itertools.pairwise(trail))
        }
        seen_anywhere = {s for trail in seen_states.values() for s in trail}
        check(
            "every file only moved forward (queued, reading, searchable by words, adding meaning, "
            "ready), none went back, and each of those states was seen in some file",
            not went_back
            and {"queued", "searchable", "embedding", "ready"} <= seen_anywhere
            and all(trail[-1] == "ready" for trail in seen_states.values()),
            (went_back, sorted(seen_anywhere)),
        )
        note(
            f"{len(files)} files, {totals['passages']:,} passages embedded in {took / 60:.1f} minutes "
            f"on the CPU ({totals['passages'] / took:.1f} passages a second)"
        )
        NUMBERS["passages"] = totals["passages"]
        NUMBERS["import_minutes"] = round(took / 60, 1)
        sizes = vector_sizes("p-ada")
        built = built_with("p-ada") or {}
        # A passage the engine refused as too long has an empty vector, and the screen counts it.
        refused_rows = [d for d in listed["documents"] if (d.get("meaning") or {}).get("refused")]
        refused_passages = sum(int(d["meaning"]["refused"]) for d in refused_rows)
        refusal_notes = [n for d in refused_rows for n in d["meaning"]["notes"]]
        check(
            "every vector is 1,024 numbers of float32 (4,096 bytes), made by the model the index records, "
            "with its prefixes known; a passage the engine refused has none, and the screen counts "
            f"it ({refused_passages})",
            set(sizes) <= {0, 4096}
            and sizes.get(0, 0) == refused_passages
            and sizes.get(4096, 0) == totals["passages"] - refused_passages
            and built.get("model") == QWEN
            and built.get("dim") == 1024
            and built.get("known") is True
            and built.get("query_prefix", "").startswith(
                "Instruct: Given a question, retrieve passages"
            ),
            (sizes, built, refused_passages),
        )
        NUMBERS["passages_refused_by_the_engine"] = refused_passages
        if refused_passages:
            for line in refusal_notes:
                note(f"a refused passage, as its file's row says it: {line}")
            check(
                "the note on a refused passage gives what the engine itself said about its limit "
                "(its own 1,024 tokens a slot), and never the 32,768 the table holds for the model",
                all(
                    "it said: request (" in line
                    and "tokens) exceeds the available context size (1024 tokens)" in line
                    and "32,768" not in line
                    for line in refusal_notes
                )
                and len(refusal_notes) == refused_passages,
                refusal_notes,
            )
        else:
            note("the engine refused no passage this time (none was longer than its 1,024 tokens)")
        # What was sent to the search model, and how: every request that carried passages (not
        # the one-word ping Workbench asks which model answers with, not the one-character
        # question the inference driver asks once to learn that the backend embeds, and not a
        # question).
        passage_requests = [
            e
            for e in EMBED_LOG["qwen"]
            if all(t not in ("ping", "1") and not t.startswith("Instruct:") for t in e["texts"])
        ]
        accepted = [e for e in passage_requests if e["status"] == 200]
        too_long_400 = [e for e in passage_requests if e["status"] == 400]
        other_bad = [e for e in passage_requests if e["status"] not in (200, 400, None)]
        starts = sorted(e["t0"] for e in passage_requests)
        window_max = max((sum(1 for t in starts if t0 <= t < t0 + 60) for t0 in starts), default=0)
        # The most requests that were in the air at once: a sweep over their start and end.
        edges = sorted(
            [(e["t0"], 1) for e in passage_requests]
            + [(e["t1"] or e["t0"], -1) for e in passage_requests]
        )
        peak = running = 0
        for _, step in edges:
            running += step
            peak = max(peak, running)
        inputs_sent = sum(e["n"] for e in accepted)
        check(
            "passages went in requests of at most sixteen, one request in the air at a time, and never "
            f"more than 30 started in a minute (most seen: {window_max}; peak in the air: {peak})",
            max(e["n"] for e in passage_requests) <= 16 and peak == 1 and window_max <= 30,
            (max(e["n"] for e in passage_requests), peak, window_max),
        )
        check(
            "no passage text carries the question's instruction, and each is its headings, a new line, "
            "its words",
            sum(1 for e in accepted for t in e["texts"] if "\n" in t) > 0.5 * inputs_sent,
            accepted[0]["texts"][0][:200],
        )
        check(
            "after the stop and the restart every passage was embedded once and none twice: what the "
            "engine accepted is every passage but the refused ones "
            f"({inputs_sent:,} accepted for {totals['passages']:,} passages, {refused_passages} refused)",
            inputs_sent == totals["passages"] - refused_passages,
            (inputs_sent, totals["passages"], refused_passages),
        )
        check(
            "the only requests the engine turned away were for the too-long passage, found by halving "
            f"the batch it was in: {len(too_long_400)} refusals (at most 5 for a batch of 16) for "
            f"{refused_passages} passage(s), and no other error but the one the stop caused",
            not other_bad and len(too_long_400) <= 5 * max(refused_passages, 1),
            [(e["n"], e["status"]) for e in too_long_400 + other_bad],
        )
        if stopped_listing is not None:
            NUMBERS["stopped_at_embedded"] = embedded_at_stop

        # ------------------------------------------------------------------ #
        say("4/5. the quality gate: questions written by someone who had not read the documents")
        found: dict[str, list[list[dict[str, Any]]]] = {"words": [], "meaning": [], "both": []}
        best_similarity: list[float | None] = []
        for q in questions:
            for label, ways in (("words", ["words"]), ("meaning", ["meaning"]), ("both", None)):
                got = ada.test(q["question"], passages=5, **({"ways": ways} if ways else {}))
                found[label].append(got["passages"])
                if label == "meaning":
                    best_similarity.append(got["meaning"]["best"])
        hits = {k: recall_at(v, questions, 5) for k, v in found.items()}
        recall = {k: sum(v) / len(v) for k, v in hits.items()}
        mrr = {k: reciprocal_rank(v, questions, 5) for k, v in found.items()}
        n = len(questions)
        files_used = len({q["file"] for q in questions})
        print(f"\n  {n} questions over {files_used} files, from {QUESTIONS.name}", flush=True)
        print("  recall@5   words (BM25)  meaning    both (hybrid)", flush=True)
        print(
            f"             {recall['words']:.3f}          {recall['meaning']:.3f}      {recall['both']:.3f}"
            f"      ({sum(hits['words'])}, {sum(hits['meaning'])}, {sum(hits['both'])} of {n})",
            flush=True,
        )
        print(
            f"  MRR@5      {mrr['words']:.3f}          {mrr['meaning']:.3f}      {mrr['both']:.3f}",
            flush=True,
        )
        vs_words = paired_bootstrap(hits["both"], hits["words"])
        vs_meaning = paired_bootstrap(hits["both"], hits["meaning"])
        print(
            f"  hybrid minus words   {vs_words[0]:+.3f}  (95% interval {vs_words[1]:+.3f} to {vs_words[2]:+.3f})",
            flush=True,
        )
        print(
            f"  hybrid minus meaning {vs_meaning[0]:+.3f}  (95% interval {vs_meaning[1]:+.3f} to {vs_meaning[2]:+.3f})",
            flush=True,
        )
        only_both = sum(
            1
            for a, b, c in zip(hits["words"], hits["meaning"], hits["both"], strict=True)
            if c and not a and not b
        )
        only_meaning = sum(
            1 for a, b in zip(hits["words"], hits["meaning"], strict=True) if b and not a
        )
        only_words = sum(
            1 for a, b in zip(hits["words"], hits["meaning"], strict=True) if a and not b
        )
        note(
            f"words found {only_words} that meaning missed; meaning found {only_meaning} that words missed; "
            f"{only_both} only the two together found"
        )
        NUMBERS["recall"] = {k: round(v, 3) for k, v in recall.items()}
        NUMBERS["recall_counts"] = {k: sum(v) for k, v in hits.items()}
        NUMBERS["questions"] = n
        NUMBERS["files"] = files_used
        NUMBERS["mrr"] = {k: round(v, 3) for k, v in mrr.items()}
        check(
            "the gate: hybrid beats the words alone on recall@5 "
            f"({recall['both']:.3f} against {recall['words']:.3f})",
            recall["both"] > recall["words"],
            recall,
        )
        check(
            "the gate: hybrid beats meaning alone on recall@5 "
            f"({recall['both']:.3f} against {recall['meaning']:.3f})",
            recall["both"] > recall["meaning"],
            recall,
        )

        # The floor (design 4.4, item 7): how near is the nearest passage to a question the
        # documents do not answer, against one they do? Twelve unrelated questions the floor was
        # first set from, twelve more written after those numbers were seen and never used to set
        # it (held out), and eight about the same subject as the documents that they do not
        # answer, which is the case a floor cannot be expected to stop.
        unrelated = [
            "What is the airspeed velocity of an unladen swallow?",
            "How do I bake a loaf of sourdough bread?",
            "Who won the 1998 football World Cup?",
            "What is the capital city of Australia?",
            "How tall is Mount Everest?",
            "What is the boiling point of liquid nitrogen?",
            "Can you recommend a good pasta recipe?",
            "Who wrote the novel Pride and Prejudice?",
            "How many moons does Jupiter have?",
            "What happens at the end of the play Hamlet?",
            "How do vaccines train the immune system?",
            "Which planet is closest to the sun?",
        ]
        held_out = [
            "What is the tallest building in the world?",
            "How do I change a flat tyre on a bicycle?",
            "Who painted the Mona Lisa?",
            "What is the recommended daily amount of vitamin C?",
            "How long does light take to travel from the sun to the earth?",
            "What are the rules for castling in chess?",
            "When did the Roman Empire fall?",
            "How do I knit a scarf?",
            "What is the population of Canada?",
            "Why is the sky blue?",
            "What is the best way to train for a marathon?",
            "How many players are on a basketball team?",
        ]
        near_subject = [
            "How do I set up a Kubernetes cluster with Helm charts?",
            "What is the best graphics card for playing video games at 4K?",
            "How do I configure a printer on Windows?",
            "Which programming language should a beginner learn first?",
            "How do I set up a WordPress blog?",
            "What is the difference between TCP and UDP?",
            "How do I write a SQL query that joins two tables?",
            "How do I install Docker on Ubuntu?",
        ]

        def nearest(question: str) -> tuple[float | None, dict[str, Any]]:
            """The nearest passage's similarity (meaning alone), and the answer as asked."""
            alone = ada.test(question, ways=["meaning"])
            return alone["meaning"]["best"], ada.test(question)

        measured: dict[str, list[tuple[float | None, dict[str, Any]]]] = {
            "unrelated": [nearest(q_) for q_ in unrelated],
            "held out": [nearest(q_) for q_ in held_out],
            "near the subject": [nearest(q_) for q_ in near_subject],
        }
        on_best = [b for b, hit in zip(best_similarity, hits["meaning"], strict=True) if hit and b]
        missed_best = [
            b for b, hit in zip(best_similarity, hits["meaning"], strict=True) if not hit and b
        ]
        answerable_best = [b for b in best_similarity if b is not None]

        def spread(values: list[float]) -> str:
            if not values:
                return "none"
            ordered = sorted(values)
            return (
                f"min {ordered[0]:.3f}, 5th percentile {ordered[int(0.05 * len(ordered))]:.3f}, "
                f"median {ordered[len(ordered) // 2]:.3f}, max {ordered[-1]:.3f}"
            )

        qwen_floor = ada.search()["model"]["floor"]
        print("\n  the nearest passage's similarity (meaning only, top one):", flush=True)
        print(
            f"    questions the documents answer, found ({len(on_best)}): {spread(on_best)}",
            flush=True,
        )
        print(
            f"    questions the documents answer, missed ({len(missed_best)}): {spread(missed_best)}",
            flush=True,
        )
        for label, rows_ in measured.items():
            values = [b for b, _ in rows_ if b is not None]
            seen = {
                s: sum(1 for _, a in rows_ if a["status"] == s)
                for s in ("found", "nothing_matched")
            }
            print(
                f"    {label} ({len(values)}): {spread(values)}; under the floor "
                f"{sum(1 for b in values if b < qwen_floor)}; asked as a person asks: {seen}",
                flush=True,
            )
        all_unrelated = [
            b for b, _ in measured["unrelated"] + measured["held out"] if b is not None
        ]
        under = sum(1 for b in all_unrelated if b < qwen_floor)
        answered_under = sum(1 for b in answerable_best if b < qwen_floor)
        NUMBERS["floor"] = qwen_floor
        NUMBERS["similarity_answerable"] = [
            round(min(answerable_best), 3),
            round(max(answerable_best), 3),
        ]
        NUMBERS["similarity_unrelated"] = [
            round(min(all_unrelated), 3),
            round(max(all_unrelated), 3),
        ]
        NUMBERS["similarity_unrelated_held_out"] = [
            round(min(b for b, _ in measured["held out"] if b is not None), 3),
            round(max(b for b, _ in measured["held out"] if b is not None), 3),
        ]
        near = [b for b, _ in measured["near the subject"] if b is not None]
        NUMBERS["similarity_near_subject"] = [round(min(near), 3), round(max(near), 3)]
        NUMBERS["floor_unrelated_under"] = f"{under}/{len(all_unrelated)}"
        NUMBERS["floor_answerable_under"] = f"{answered_under}/{len(answerable_best)}"
        NUMBERS["floor_near_subject_under"] = (
            f"{sum(1 for b in near if b < qwen_floor)}/{len(near)}"
        )
        note(
            f"floor in use: {qwen_floor}; the nearest passage is under it for {under} of "
            f"{len(all_unrelated)} unrelated questions (12 the floor was set from, 12 held out), "
            f"{answered_under} of {len(answerable_best)} questions the documents answer, and "
            f"{sum(1 for b in near if b < qwen_floor)} of {len(near)} about the same subject"
        )
        check(
            "the floor sits above the nearest passage of every unrelated question, the twelve held "
            "out included, and below the nearest passage of every question the documents answer",
            under == len(all_unrelated) and answered_under == 0,
            (qwen_floor, spread(answerable_best), spread(all_unrelated)),
        )
        shown_nothing = [
            q_
            for q_, (b, a) in zip(
                unrelated + held_out, measured["unrelated"] + measured["held out"], strict=True
            )
            if a["status"] == "nothing_matched"
        ]
        check(
            "asked as a person asks, an unrelated question the words found nothing for is told "
            f"'nothing matched' and sent no passage ({len(shown_nothing)} of {len(all_unrelated)})",
            bool(shown_nothing)
            and all(
                not a["passages"]
                for _, a in measured["unrelated"] + measured["held out"]
                if a["status"] == "nothing_matched"
            ),
            shown_nothing[:3],
        )

        # A question where the words found nothing and meaning did: the paraphrase.
        demo_index = next(
            (
                i
                for i in range(n)
                if not hits["words"][i] and hits["meaning"][i] and hits["both"][i]
            ),
            None,
        )
        if demo_index is None:
            check("some question was found by meaning that the words missed", False, hits)
        else:
            q = questions[demo_index]
            by_words = ada.test(q["question"], ways=["words"])
            by_meaning = ada.test(q["question"], ways=["meaning"])
            both = ada.test(q["question"])
            right = [p for p in both["passages"] if right_section(p, q)]
            terms = set(by_words["query"]["terms"])
            print(f"\n  question: {q['question']}", flush=True)
            print(f"  answered under: {q['file']} / {q['heading']}", flush=True)
            print(f"  words searched for: {sorted(terms)}", flush=True)
            for label, got in (
                ("words only", by_words),
                ("meaning only", by_meaning),
                ("both", both),
            ):
                top = [f"{p['name']} / {p['heading']} [{p['via']}]" for p in got["passages"][:3]]
                print(f"    {label:13s}: {top}", flush=True)
            check(
                "a question phrased in other words finds its section by meaning, where the words alone "
                "do not, and the passage says so (via meaning, its place and how near it was)",
                not any(right_section(p, q) for p in by_words["passages"])
                and any(right_section(p, q) for p in by_meaning["passages"])
                and bool(right)
                and right[0]["via"] in ("meaning", "both")
                and 0 < (right[0]["why"]["similarity"] or 0) <= 1
                and "meaning" in right[0]["why"]["ranks"],
                right[:1],
            )
        both_seen = [p for res in found["both"] for p in res if p["via"] == "both"]
        by_via_rank: dict[str, list[int]] = {"both": [], "words": [], "meaning": []}
        for res in found["both"]:
            for place, p in enumerate(res, start=1):
                by_via_rank[p["via"]].append(place)
        mean_place = {k: (sum(v) / len(v) if v else None) for k, v in by_via_rank.items()}
        note(
            f"mean place in the top five by how found: {mean_place} ({len(both_seen)} passages found by both)"
        )
        check(
            "a passage both ways found is placed ahead of those only one way found (the hybrid's "
            "order: found by both comes first on average)",
            mean_place["both"] is not None
            and all(
                mean_place["both"] < v
                for k, v in mean_place.items()
                if k != "both" and v is not None
            ),
            mean_place,
        )

        # ------------------------------------------------------------------ #
        say("6. a real model answers a chat from what meaning found")
        paraphrase = (
            questions[demo_index]["question"]
            if demo_index is not None
            else questions[0]["question"]
        )
        chat_two = ada.post("/api/chats", json={"model": CHAT}).json()["id"]
        ada.patch(f"/api/chats/{chat_two}", json={"settings": {"documents": "on"}})
        before_sent = len(WB_SENT)
        before_engine = len(ENGINE_BODIES)
        sent_two = ada.post(
            f"/api/chats/{chat_two}/messages", json={"content": f"{paraphrase} /no_think"}
        )
        reply_two = ada.wait_answer(chat_two) if sent_two.status_code == 201 else {}
        two = reply_two.get("retrieval") or {}
        reached = ENGINE_BODIES[before_engine:][-1] if len(ENGINE_BODIES) > before_engine else {}
        engine_text = json.dumps(reached.get("messages", []))
        vias = [s["via"] for s in reply_two.get("sources", []) if s.get("passage")]
        check(
            "the chat's answer says it searched by meaning and words, names the search model, lists "
            "each passage with how it was found, and the passages reached the real engine",
            reply_two.get("status") == "done"
            and two.get("mode") == "meaning and words"
            and two["meaning"]["used"] is True
            and two["meaning"]["model"] == QWEN
            and bool(vias)
            and set(vias) <= {"words", "meaning", "both"}
            and "<documents-" in engine_text
            and len(WB_SENT) > before_sent,
            (two.get("mode"), two.get("meaning"), vias),
        )
        note(f"the model said: {str(reply_two.get('content'))[:160]!r}")

        # ------------------------------------------------------------------ #
        say(
            "7. another search model is started and the first stopped: meaning is paused, never mixed"
        )
        bo_files = [
            (
                "harbour.md",
                b"# Harbour office\n\n## Arrivals\n\nThe harbour master keeps the vessel registry and the logbook of every ship that arrives.\n\n## Kitchen\n\nThe pantry holds flour, tins of soup and the cook's recipes for the long winter.\n",
                "text/markdown",
            ),
            (
                "lamps.md",
                b"# Lamp room\n\nThe lamp room is cleaned on Sundays, and the lantern glass is polished before the autumn storms.\n",
                "text/markdown",
            ),
        ]
        bo.add(*bo_files)
        until(
            lambda: all(d["state"] == "ready" for d in bo.documents()["documents"]), 300, every=1.0
        )
        bo_sizes_before = vector_sizes("p-bo")
        ada_sizes_before = vector_sizes("p-ada")
        check(
            "Bo's small library is ready with the same search model's numbers (1,024 each)",
            set(bo_sizes_before) == {4096} and (built_with("p-bo") or {}).get("model") == QWEN,
            bo_sizes_before,
        )
        note(
            "stopping the first search model (its engine and its driver, so that the gateway stops "
            "listing it as running, as it does for a runtime that is stopped) and starting "
            "nomic-embed-text"
        )
        kill(engines["qwen"])
        kill(drivers["qwen"])
        start_engine("nomic")
        up = until(
            lambda: engine_ready(ENGINE_NOMIC, 1) and search_model_ready(NOMIC), 240, every=1.0
        )
        gone = until(
            lambda: not (gateway_models().get(QWEN) or {}).get("ready_backends"), 120, every=1.0
        )
        check(
            "the gateway no longer lists the first search model as running",
            bool(gone),
            gateway_models().get(QWEN),
        )
        check(
            "nomic's engine is loaded and the gateway lists it as a search model, running here",
            bool(up),
            gateway_models(),
        )
        paused = until(lambda: (h := bo.search())["code"] == "changed" and h, 120, every=1.0)
        headline = f"Search by meaning is paused: this search was built with {QWEN} and {NOMIC} is running now."
        check(
            "meaning is paused for Bo: the header says which model built the search and which is "
            "running, offers Rebuild, and says documents are searched by words until then",
            bool(paused)
            and paused["headline"] == headline
            and paused["meaning"] is False
            and paused["actions"] == ["rebuild"]
            and paused["model"]["builtWith"] == QWEN
            and paused["model"]["resolved"] == NOMIC
            and "searched by words until you choose Rebuild" in paused["detail"],
            paused,
        )
        ada_paused = until(lambda: (h := ada.search())["code"] == "changed" and h, 60, every=1.0)
        check(
            "and for Ada, whose library is large: the same, with the number of passages a rebuild would redo",
            bool(ada_paused)
            and ada_paused["actions"] == ["rebuild"]
            and ada_paused["rebuild"]["passages"] == NUMBERS["passages"],
            ada_paused and ada_paused.get("rebuild"),
        )
        words_bo = bo.test("pantry flour soup")
        check(
            "words go on: a question is answered by words for Bo, and says meaning is paused",
            words_bo["status"] == "found"
            and {p["via"] for p in words_bo["passages"]} == {"words"}
            and words_bo["meaning"]["used"] is False
            and words_bo["meaning"]["code"] == "changed",
            words_bo["meaning"],
        )
        bo.add(
            (
                "late.md",
                b"# Late note\n\nThe fog horn is tested on the first Monday of each month.\n",
                "text/markdown",
            )
        )
        until(
            lambda: any(
                d["name"] == "late.md" and d["state"] == "searchable"
                for d in bo.documents()["documents"]
            ),
            60,
            every=0.5,
        )
        time.sleep(15)
        late = next(d for d in bo.documents()["documents"] if d["name"] == "late.md")
        check(
            "a file Bo adds while meaning is paused is found by words and gets no number from the new model",
            late["state"] == "searchable"
            and late["embedded"] == 0
            and late["meaning"]["state"] == "waiting"
            and late["meaning"]["message"]
            == "Ready for words. Meaning is paused until you choose Rebuild."
            and bo.test("fog horn tested monday")["status"] == "found",
            late,
        )
        check(
            "never mixed: in both indexes every vector is still the first model's size, the same "
            "number of them, and the record still names the first model",
            vector_sizes("p-bo") == bo_sizes_before
            and vector_sizes("p-ada") == ada_sizes_before
            and (built_with("p-bo") or {}).get("model") == QWEN
            and (built_with("p-ada") or {}).get("model") == QWEN,
            (vector_sizes("p-bo"), vector_sizes("p-ada")),
        )
        rebuild_started = time.perf_counter()
        rebuilt = bo.post("/api/documents/meaning/rebuild")
        check(
            "Rebuild answers at once with how many passages are owed, and the header",
            rebuilt.status_code == 200 and rebuilt.json()["passages"] > 0,
            rebuilt.text[:200],
        )
        bo_ready = until(
            lambda: all(d["state"] == "ready" for d in bo.documents()["documents"]), 300, every=1.0
        )
        bo_rebuild_seconds = time.perf_counter() - rebuild_started
        sizes_after = vector_sizes("p-bo")
        built_after = built_with("p-bo") or {}
        check(
            "after Rebuild every one of Bo's vectors is nomic's (768 numbers, 3,072 bytes), the first "
            "model's are gone, and the record names nomic with its prefixes",
            bool(bo_ready)
            and set(sizes_after) == {3072}
            and built_after.get("model") == NOMIC
            and built_after.get("dim") == 768
            and built_after.get("query_prefix") == "search_query: "
            and built_after.get("document_prefix") == "search_document: ",
            (sizes_after, built_after),
        )
        nomic_texts = [t for e in EMBED_LOG["nomic"] if e["n"] > 1 for t in e["texts"]]
        check(
            "nomic was given its prefixes: every passage began 'search_document: '",
            bool(nomic_texts) and all(t.startswith("search_document: ") for t in nomic_texts),
            nomic_texts[:2],
        )
        meaning_bo = bo.test("who records the boats arriving?", ways=["meaning"])
        both_bo = bo.test("who records the boats arriving?")
        nomic_questions = [e["texts"][0] for e in EMBED_LOG["nomic"] if e["n"] == 1]
        check(
            "with nomic, a question with none of the passage's words finds it by meaning; the question "
            "was sent with nomic's prefix",
            meaning_bo["status"] == "found"
            and any("vessel registry" in p["text"] for p in meaning_bo["passages"])
            and any(
                t.startswith("search_query: who records the boats arriving?")
                for t in nomic_questions
            )
            and bo.search()["code"] == "ok"
            and both_bo["meaning"]["model"] == NOMIC,
            (meaning_bo["status"], nomic_questions[-2:]),
        )
        check(
            "Ada was never touched: still paused, her vectors exactly as before",
            ada.search()["code"] == "changed" and vector_sizes("p-ada") == ada_sizes_before,
        )
        NUMBERS["bo_rebuild_seconds"] = round(bo_rebuild_seconds, 1)
        estimate = ada.search().get("rebuild") or {}
        note(
            f"Ada's rebuild offer, with nomic measured only on Bo's few short passages: {estimate}"
        )
        check(
            "Rebuild offers no time while the new model has been measured only on a few short "
            "passages (three tiny batches would have promised seconds for thousands of passages), "
            "and still says how many passages it would redo",
            estimate.get("passages") == NUMBERS["passages"]
            and estimate.get("seconds") is None
            and estimate.get("model") == NOMIC,
            estimate,
        )
        rebuilt_ada = ada.post("/api/documents/meaning/rebuild")
        ada_started = time.perf_counter()
        samples: list[tuple[float, int, float]] = []

        def ada_done() -> bool:
            listed_ = ada.documents()
            adding_ = listed_["search"]["adding"]
            if adding_["etaSeconds"] is not None:
                samples.append(
                    (
                        time.perf_counter() - ada_started,
                        int(listed_["totals"]["embedded"]),
                        float(adding_["etaSeconds"]),
                    )
                )
            return all(d["state"] == "ready" for d in listed_["documents"])

        ada_ready = until(ada_done, 2400, every=2.0)
        actual = time.perf_counter() - ada_started
        NUMBERS["ada_rebuild_actual_min"] = round(actual / 60, 1)
        # What the header said was left, once a quarter, half and three quarters of her passages
        # had their new numbers, against what was left.
        shown: dict[float, tuple[int, int, int]] = {}
        for share in (0.25, 0.5, 0.75):
            crossed = [s for s in samples if s[1] >= share * int(NUMBERS["passages"])]
            if crossed:
                at, embedded_, eta_ = crossed[0]
                shown[share] = (round(eta_), round(actual - at), embedded_)
        for share, (eta_s, left_s, embedded_) in shown.items():
            note(
                f"Ada's rebuild at {share:.0%} ({embedded_:,} passages): the header said {eta_s} s "
                f"left; {left_s} s were left"
            )
        note(f"Ada's rebuild took {actual / 60:.1f} minutes in all")
        NUMBERS["ada_rebuild_header_vs_left_seconds"] = {
            f"{round(k * 100)}%": [v[0], v[1]] for k, v in shown.items()
        }
        check(
            "while Ada's rebuild ran the time left was shown, and it was within a third of what was "
            "left once a quarter, half and three quarters of her passages had their new numbers",
            len(shown) == 3 and all(abs(e_ - l_) <= l_ / 3 for e_, l_, _ in shown.values()),
            shown,
        )
        check(
            "Ada's Rebuild re-embedded every passage with nomic, 768 numbers each, from the files "
            "she kept; words found her documents throughout",
            rebuilt_ada.status_code == 200
            and bool(ada_ready)
            and set(vector_sizes("p-ada")) == {3072}
            and (built_with("p-ada") or {}).get("model") == NOMIC
            and ada.search()["code"] == "ok",
            (rebuilt_ada.status_code, vector_sizes("p-ada"), built_with("p-ada")),
        )

        # ------------------------------------------------------------------ #
        say("6b. the owner, and a person's documents")
        check(
            "the owner cannot read Bo's or Ada's documents (404), as in MR4",
            owner.get(f"/api/documents/{bo.documents()['documents'][0]['id']}").status_code == 404,
        )

    except Exception as exc:
        traceback.print_exc()
        check(f"the run went to plan ({type(exc).__name__}: {exc})", False)
    finally:
        say("8. teardown by pid")
        if server is not None:
            server.should_exit = True
        for proc in procs:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False
            )
        time.sleep(2)
        still = [p for p in OWNED if listening(p) and p not in OURS]
        check("no owned process port is still listening", not still, still)
        print(f"\nnumbers: {json.dumps(NUMBERS)}", flush=True)
        print(f"logs: {logs}\n{PASSES} passed, {FAILURES} failed", flush=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
