"""A fake Eugene (its sign-in and its gateway) and a real Workbench, each a
real server on a loopback port.

Real servers rather than in-process transports because the claims under
test are about streams: an answer that keeps going when no tab is
watching, two tabs fed one answer, Stop mid-answer. `httpx.ASGITransport`
and Starlette's test client both collect a response before handing it
over, which would make every one of those claims untestable.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from joserfc import jwt
from joserfc.jwk import RSAKey

from eugene_plexus_workbench.app import create_app
from eugene_plexus_workbench.settings import Settings

CLIENT_ID = "wb-client"
CLIENT_SECRET = "wb-client-secret"
APP_KEY = "app-key-token"
ADMIN_TOKEN = "admin-token"
MODEL = "local-model"
#: The "draft" mode's text before its search; the emoji is two units in the
#: page's string length and one in Python's.
DRAFT = "A first answer 🎉, before searching."
DRAFT_REASONING = "No tools needed."


# --------------------------------------------------------------------------- #
# servers
# --------------------------------------------------------------------------- #


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ServerThread:
    def __init__(self, app: Any, port: int | None = None) -> None:
        self.port = port or free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> ServerThread:
        self.thread.start()
        deadline = time.perf_counter() + 15
        while not self.server.started:
            if time.perf_counter() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)


# --------------------------------------------------------------------------- #
# a fake Eugene sign-in (OpenID Connect, C2's shape)
# --------------------------------------------------------------------------- #


@dataclass
class FakeEugene:
    url: str = ""
    key: RSAKey = field(default_factory=lambda: RSAKey.generate_key(2048, parameters={"kid": "k1"}))
    people: dict[str, dict[str, str]] = field(
        default_factory=lambda: {
            "operator": {"name": "Owner", "role": "operator", "username": "operator"},
            "p-ada": {"name": "Ada", "role": "member", "username": "ada"},
            "p-bo": {"name": "Bo", "role": "member", "username": "bo"},
        }
    )
    disabled: set[str] = field(default_factory=set)
    expires_in: int = 600
    up: bool = True
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    refresh_tokens: dict[str, str] = field(default_factory=dict)
    revoked: list[str] = field(default_factory=list)
    refreshes: int = 0
    token_calls: int = 0

    @property
    def issuer(self) -> str:
        return f"{self.url}/oidc"

    def id_token(
        self,
        sub: str,
        *,
        nonce: str | None = None,
        aud: str = CLIENT_ID,
        typ: str = "JWT",
        issuer: str | None = None,
    ) -> str:
        person = self.people[sub]
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": issuer or self.issuer,
            "aud": aud,
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
        return jwt.encode({"alg": "RS256", "typ": typ, "kid": "k1"}, claims, self.key)

    def app(self) -> FastAPI:
        app = FastAPI()

        def client_ok(request: Request) -> bool:
            auth = request.headers.get("authorization", "")
            expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            return auth == f"Basic {expected}"

        @app.get("/oidc/.well-known/openid-configuration")
        async def discovery() -> Any:
            if not self.up:
                return JSONResponse({}, status_code=503)
            return {
                "issuer": self.issuer,
                "authorization_endpoint": f"{self.issuer}/authorize",
                "token_endpoint": f"{self.issuer}/token",
                "jwks_uri": f"{self.issuer}/jwks",
                "revocation_endpoint": f"{self.issuer}/revoke",
            }

        @app.get("/oidc/jwks")
        async def jwks() -> Any:
            return {"keys": [self.key.as_dict(private=False)]}

        @app.get("/oidc/authorize")
        async def authorize(request: Request) -> Any:
            q = request.query_params
            assert q["client_id"] == CLIENT_ID and q["code_challenge_method"] == "S256"
            sub = q["login_as"]
            if sub in self.disabled:
                return JSONResponse({"message": "turned off"}, status_code=403)
            code = secrets.token_urlsafe(16)
            self.codes[code] = {
                "sub": sub,
                "nonce": q["nonce"],
                "challenge": q["code_challenge"],
                "redirect_uri": q["redirect_uri"],
            }
            target = (
                q["redirect_uri"]
                + "?"
                + urlencode({"code": code, "state": q["state"], "iss": self.issuer})
            )
            return RedirectResponse(target, status_code=302)

        @app.post("/oidc/token")
        async def token(request: Request) -> Any:
            if not self.up:
                return JSONResponse({}, status_code=503)
            if not client_ok(request):
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            self.token_calls += 1
            form = await request.form()
            if form["grant_type"] == "authorization_code":
                found = self.codes.pop(str(form["code"]), None)
                verifier = str(form.get("code_verifier", ""))
                challenge = (
                    base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                    .rstrip(b"=")
                    .decode()
                )
                if (
                    found is None
                    or found["challenge"] != challenge
                    or found["redirect_uri"] != form.get("redirect_uri")
                ):
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
                sub = found["sub"]
                refresh = secrets.token_urlsafe(16)
                self.refresh_tokens[refresh] = sub
                return {
                    "access_token": "at",
                    "token_type": "Bearer",
                    "expires_in": self.expires_in,
                    "id_token": self.id_token(sub, nonce=found["nonce"]),
                    "refresh_token": refresh,
                }
            if form["grant_type"] == "refresh_token":
                self.refreshes += 1
                sub = self.refresh_tokens.get(str(form["refresh_token"]))
                if sub is None or sub in self.disabled:
                    return JSONResponse(
                        {
                            "error": "invalid_grant",
                            "error_description": "This sign-in is no longer good.",
                        },
                        status_code=400,
                    )
                return {
                    "access_token": "at",
                    "token_type": "Bearer",
                    "expires_in": self.expires_in,
                    "id_token": self.id_token(sub),
                }
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

        @app.post("/oidc/revoke")
        async def revoke(request: Request) -> Any:
            form = await request.form()
            token = str(form["token"])
            self.revoked.append(token)
            self.refresh_tokens.pop(token, None)
            return {}

        return app


# --------------------------------------------------------------------------- #
# a fake gateway
# --------------------------------------------------------------------------- #


def chunk(
    delta: dict[str, Any] | None = None,
    *,
    finish: str | None = None,
    extension: dict[str, Any] | None = None,
    choices: bool = True,
) -> str:
    body: dict[str, Any] = {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL,
        "choices": [],
    }
    if choices:
        body["choices"] = [{"index": 0, "delta": delta or {}, "finish_reason": finish}]
    if extension is not None:
        body["x_eugene_plexus"] = extension
    return "data: " + json.dumps(body) + "\n\n"


@dataclass
class FakeGateway:
    url: str = ""
    requests: list[dict[str, Any]] = field(default_factory=list)
    #: "answer", "slow", "refuse_key", "refuse_search", "cut", "draft"
    mode: str = "answer"
    words: list[str] = field(default_factory=lambda: ["Hello", " from", " the", " model."])
    delay: float = 0.0
    #: The "draft" mode's progress marks around its search.
    draft_phases: tuple[str | None, ...] = ("started", "finished")
    release: asyncio.Event | None = None
    search: dict[str, Any] = field(default_factory=lambda: {"available": True, "reason": None})

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/v1/models")
        async def models(request: Request) -> Any:
            if request.headers.get("authorization") != f"Bearer {APP_KEY}":
                return JSONResponse({"error": {"message": "invalid key"}}, status_code=401)
            return {
                "object": "list",
                "x_eugene_plexus": {"web_search": self.search},
                "data": [
                    {
                        "id": MODEL,
                        "object": "model",
                        "x_eugene_plexus": {
                            "surfaces": ["chat"],
                            "context_length": 32768,
                            "image_input": True,
                            "web_search": True,
                            "ready_backends": 1,
                        },
                    },
                    {
                        "id": "embedder",
                        "object": "model",
                        "x_eugene_plexus": {"surfaces": ["embeddings"]},
                    },
                ],
            }

        @app.post("/v1/chat/completions")
        async def chat(request: Request) -> Any:
            body = await request.json()
            self.requests.append(body)
            if (
                request.headers.get("authorization") != f"Bearer {APP_KEY}"
                or self.mode == "refuse_key"
            ):
                return JSONResponse(
                    {"error": {"message": "This key was revoked."}}, status_code=401
                )
            if self.mode == "refuse_search" and "web_search_options" in body:
                return JSONResponse(
                    {
                        "error": {
                            "message": "web_search_options: this key's tool "
                            "scope does not include web_search"
                        }
                    },
                    status_code=400,
                )

            async def frames() -> AsyncIterator[str]:
                yield chunk(
                    choices=False, extension={"progress": {"stage": "prompt", "prompt_tokens": 10}}
                )
                if self.mode == "draft" and "web_search_options" in body:
                    # What a local model does under a forced first search
                    # (gateway#4): a whole answer, then the search, then
                    # another. The gateway marks the search's start and end.
                    yield chunk({"reasoning_content": DRAFT_REASONING})
                    yield chunk({"content": DRAFT})
                    for phase in self.draft_phases:
                        tool = {"stage": "tool", "tool": "web_search"}
                        if phase:  # a gateway before alpha.6 sends no phase
                            tool["phase"] = phase
                        yield chunk(choices=False, extension={"progress": tool})
                    yield chunk({"reasoning_content": "Now with results."})
                    yield chunk({"content": "\n\n"})
                elif "web_search_options" in body:
                    yield chunk({"reasoning_content": "Searching first."})
                for word in self.words:
                    if self.delay:
                        await asyncio.sleep(self.delay)
                    yield chunk({"content": word})
                if self.mode == "cut":
                    return
                annotations = []
                if "web_search_options" in body:
                    annotations = [
                        {
                            "type": "url_citation",
                            "url_citation": {"url": "https://example.org/page", "title": "A page"},
                        }
                    ]
                    yield chunk({"annotations": annotations})
                yield chunk({}, finish="stop", extension={"web_searches": 1 if annotations else 0})
                yield "data: [DONE]\n\n"

            return StreamingResponse(frames(), media_type="text/event-stream")

        return app


# --------------------------------------------------------------------------- #
# a browser
# --------------------------------------------------------------------------- #


class Browser:
    """One person's browser: cookies, the page's stored secret, its origin."""

    def __init__(self, workbench: str, eugene: FakeEugene) -> None:
        self.base = workbench
        self.eugene = eugene
        self.http = httpx.Client(base_url=workbench, timeout=20, trust_env=False)
        self.secret: str | None = None

    def sign_in(self, sub: str) -> httpx.Response:
        start = self.http.get("/signin")
        assert start.status_code == 302, start.text
        to_eugene = start.headers["location"] + "&login_as=" + sub
        at_eugene = httpx.get(to_eugene, trust_env=False)
        if at_eugene.status_code != 302:
            return at_eugene
        back = urlsplit(at_eugene.headers["location"])
        done = self.http.get(back.path + "?" + back.query)
        location = done.headers.get("location", "")
        if "#signin=" in location:
            self.secret = location.split("#signin=", 1)[1]
        return done

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        out = {"Origin": self.base}
        if self.secret:
            out["X-Workbench-Secret"] = self.secret
        out.update(extra or {})
        return out

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.get(path, headers=self.headers(kw.pop("headers", None)), **kw)

    def post(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.post(path, headers=self.headers(kw.pop("headers", None)), **kw)

    def patch(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.patch(path, headers=self.headers(kw.pop("headers", None)), **kw)

    def delete(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.delete(path, headers=self.headers(kw.pop("headers", None)), **kw)

    def new_chat(self) -> str:
        return str(self.post("/api/chats", json={"model": MODEL}).json()["id"])

    def wait_answer(self, chat_id: str, seconds: float = 15) -> dict[str, Any]:
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            messages = self.get(f"/api/chats/{chat_id}").json()["messages"]
            if messages and messages[-1]["status"] != "running":
                return dict(messages[-1])
            time.sleep(0.05)
        raise AssertionError("the answer did not finish")


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


@dataclass
class World:
    eugene: FakeEugene
    gateway: FakeGateway
    workbench: str
    data: Path
    settings: Settings

    def browser(self) -> Browser:
        return Browser(self.workbench, self.eugene)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    eugene = FakeEugene()
    eugene_server = ServerThread(eugene.app()).start()
    eugene.url = eugene_server.url
    gateway = FakeGateway()
    gateway_server = ServerThread(gateway.app()).start()
    gateway.url = gateway_server.url
    (tmp_path / "client_key").write_text(APP_KEY, encoding="utf-8")
    (tmp_path / "oidc_secret").write_text(CLIENT_SECRET, encoding="utf-8")
    settings = Settings(
        data_dir=tmp_path / "data",
        key_file=tmp_path / "client_key",
        admin_token=ADMIN_TOKEN,
        gateway_url=gateway.url,
        oidc_issuer=eugene.issuer,
        oidc_client_id=CLIENT_ID,
        oidc_secret_file=tmp_path / "oidc_secret",
        static_dir=_static(tmp_path),
    )
    workbench = ServerThread(create_app(settings)).start()
    try:
        yield World(eugene, gateway, workbench.url, tmp_path / "data", settings)
    finally:
        workbench.stop()
        gateway_server.stop()
        eugene_server.stop()


def _static(tmp_path: Path) -> Path:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(
        "<!doctype html><title>Workbench</title><div id=root>", encoding="utf-8"
    )
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return static


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x04\x00\x09\xfb\x03\xfd\xe3U\xf2\x9c"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
