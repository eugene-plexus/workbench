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
import contextlib
import hashlib
import json
import secrets
import socket
import sqlite3
import struct
import sys
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
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from joserfc import jwt
from joserfc.jwk import RSAKey

from eugene_plexus_workbench import context, token_count
from eugene_plexus_workbench.app import create_app
from eugene_plexus_workbench.knowledge import sorting
from eugene_plexus_workbench.settings import Settings

from . import fake_embedding, fake_rerank


def _one_tls_context_for_every_test_client() -> None:
    """Each httpx client a test makes (every `httpx.get`, every `Browser`) would load certifi's
    CA bundle into an SSL context of its own: about 100 ms a client on Windows. Made once, the
    suite on four workers went from 356 s to 247 s there. The tests speak plain HTTP to loopback
    and an SSL context is safe to share. Workbench's own clients already share theirs
    (`_http.ssl_context`); this changes only clients made with httpx's default, in the tests."""
    from httpx._transports import default as transports

    made = getattr(transports, "create_ssl_context", None)
    if made is None:  # httpx moved it: the suite is slower on Windows, nothing else
        return
    shared: dict[bool, Any] = {}

    def create_ssl_context(verify: Any = True, cert: Any = None, trust_env: bool = True) -> Any:
        if verify is not True or cert is not None:
            return made(verify=verify, cert=cert, trust_env=trust_env)
        if trust_env not in shared:
            shared[trust_env] = made(verify=True, cert=None, trust_env=trust_env)
        return shared[trust_env]

    transports.create_ssl_context = create_ssl_context


_one_tls_context_for_every_test_client()


def _test_sockets_leave_no_time_wait() -> None:
    """On Windows a connection closed the ordinary way keeps its port for two minutes
    (TIME_WAIT), out of 16,384 for the whole machine. A full run on 32 workers left about 9,000
    behind in under a minute, so a second run straight after it, or two at once, ran out, and the
    tests' event loops hung making their self-pipe (`socket.socketpair` waits on an `accept` that
    never comes). Each event loop's self-pipe and each of the tests' own HTTP connections is
    closed with a reset instead, which leaves no TIME_WAIT: about 2,500 a run remain, from
    Workbench's own connections, which are not changed."""
    if sys.platform != "win32":
        return
    linger = struct.pack("hh", 1, 0)
    pair = socket.socketpair

    def socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
        left, right = pair(*args, **kwargs)
        for end in (left, right):
            end.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
        return left, right

    socket.socketpair = socketpair  # type: ignore[assignment]
    init = httpx.HTTPTransport.__init__

    def transport(self: httpx.HTTPTransport, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("socket_options") is None:
            kwargs["socket_options"] = [(socket.SOL_SOCKET, socket.SO_LINGER, linger)]
        init(self, *args, **kwargs)

    httpx.HTTPTransport.__init__ = transport  # type: ignore[method-assign]


_test_sockets_leave_no_time_wait()


def _sqlite_without_flushes_on_windows() -> None:
    """Workbench's stores are SQLite in WAL with the default `synchronous=FULL`: every commit
    waits for the disk to flush, and on a Windows CI runner that was the largest part of what
    was left of its suite's extra time (the Pytest step took 531 to 631 s with flushes and 357 s
    without; Linux took 281 s either way). No test can tell a flushed commit from one in the
    OS's cache (a process the tests kill leaves the cache behind), so in the tests, on Windows,
    each connection skips the flush. What Workbench does in use is not changed."""
    if sys.platform != "win32":
        return
    connect = sqlite3.connect

    def unflushed(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        db = connect(*args, **kwargs)
        # A file that is not a store fails where it would have, at Workbench's own first read.
        with contextlib.suppress(sqlite3.Error):
            db.execute("PRAGMA synchronous=OFF")
        return db

    sqlite3.connect = unflushed  # type: ignore[assignment]


_sqlite_without_flushes_on_windows()

CLIENT_ID = "wb-client"
CLIENT_SECRET = "wb-client-secret"
APP_KEY = "app-key-token"
ADMIN_TOKEN = "admin-token"
MODEL = "local-model"


#: The "draft" mode's text before its search; the emoji is two units in the
#: page's string length and one in Python's.
def png(width: int, height: int) -> bytes:
    """A real PNG of this size: one colour, decodable by a browser."""
    import struct
    import zlib

    def part(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + b"\xcc\x55\x22" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + part(b"IHDR", header)
        + part(b"IDAT", zlib.compress(rows))
        + part(b"IEND", b"")
    )


#: Image models as the gateway lists them since the media screens (§6.1):
#: flux makes one at a time and edits from up to four references; mini makes
#: up to ten and takes a quality; `oai-image` leaves every setting to its API.
IMAGE_MODELS: list[dict[str, Any]] = [
    {
        "id": "openrouter/flux",
        "object": "model",
        "owned_by": "openrouter",
        "x_eugene_plexus": {
            "drivers": ["openrouter"],
            "surfaces": ["image"],
            "locality": "external",
            "ready_backends": 1,
            "image_edits": True,
            "image_max_images": 1,
            "image_qualities": [],
            "image_backgrounds": [],
            "image_output_formats": ["png", "jpeg"],
            "image_min_references": 0,
            "image_max_references": 4,
        },
    },
    {
        "id": "openrouter/mini",
        "object": "model",
        "owned_by": "openrouter",
        "x_eugene_plexus": {
            "drivers": ["openrouter"],
            "surfaces": ["image"],
            "locality": "external",
            "ready_backends": 1,
            "image_edits": True,
            "image_max_images": 10,
            "image_qualities": ["auto", "low", "medium", "high"],
            "image_backgrounds": ["auto", "transparent", "opaque"],
            "image_output_formats": None,
            "image_min_references": 0,
            "image_max_references": 16,
        },
    },
    {
        "id": "oai-image",
        "object": "model",
        "owned_by": "openai",
        "x_eugene_plexus": {
            "drivers": ["oai"],
            "surfaces": ["image"],
            "locality": "external",
            "ready_backends": 1,
            "image_edits": True,
        },
    },
]


def wav(seconds: float, rate: int = 8000) -> bytes:
    """A real WAV of silence this long."""
    import struct

    body = b"\x00\x00" * int(rate * seconds)
    return (
        b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(body)) + body
    )  # fmt: skip


#: An MP4 as a video door sends one: its first box is `ftyp`.
MP4_CLIP = b"\x00\x00\x00\x18ftypisom" + bytes(range(256)) * 4

#: Video models as the gateway lists them (slice 3): grok with its durations,
#: sizes, first frame and price list (§6.4); a token-billed model lists no
#: price the gateway can read.
VIDEO_MODELS: list[dict[str, Any]] = [
    {
        "id": "x-ai/grok-imagine-video",
        "object": "model",
        "owned_by": "openrouter",
        "x_eugene_plexus": {
            "drivers": ["openrouter"],
            "surfaces": ["video"],
            "locality": "external",
            "ready_backends": 1,
            "video_durations": list(range(1, 16)),
            "video_sizes": ["1280x720", "480x854", "854x480"],
            "video_first_frame": True,
            "video_prices": [
                {"sku": "cents_per_image_input", "per": "input_image", "usd": 0.002},
                {
                    "sku": "cents_per_video_output_second_480p",
                    "per": "second",
                    "usd": 0.05,
                    "resolution": "480p",
                    "sizes": ["480x854", "854x480"],
                    "audio": None,
                    "first_frame": None,
                },
                {
                    "sku": "cents_per_video_output_second_720p",
                    "per": "second",
                    "usd": 0.07,
                    "resolution": "720p",
                    "sizes": ["1280x720"],
                },
            ],
        },
    },
    {
        "id": "bytedance/seedance",
        "object": "model",
        "owned_by": "openrouter",
        "x_eugene_plexus": {
            "drivers": ["openrouter"],
            "surfaces": ["video"],
            "locality": "external",
            "ready_backends": 1,
            "video_durations": [4, 5, 6],
            "video_sizes": None,
            "video_first_frame": False,
            "video_prices": None,
        },
    },
]


#: An MP3 as a speech model sends one: an ID3 tag, then frames.
MP3_CLIP = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + bytes(400)

#: Speech and transcription models as the gateway lists them (slice 2): kokoro
#: lists its voices; a local voice leaves the voice to its provider (null);
#: whisper-1 translates too.
AUDIO_MODELS: list[dict[str, Any]] = [
    {
        "id": "openrouter/kokoro",
        "object": "model",
        "owned_by": "openrouter",
        "x_eugene_plexus": {
            "drivers": ["openrouter"],
            "surfaces": ["speech"],
            "locality": "external",
            "ready_backends": 1,
            "voices": ["af_heart", "af_bella", "am_adam"],
            "speech_formats": ["mp3", "wav", "pcm"],
        },
    },
    {
        "id": "local-voice",
        "object": "model",
        "owned_by": "local",
        "x_eugene_plexus": {
            "drivers": ["voice"],
            "surfaces": ["speech"],
            "locality": "local",
            "ready_backends": 1,
            "voices": None,
            "speech_formats": ["mp3", "opus", "aac", "flac", "wav", "pcm"],
        },
    },
    {
        # ElevenLabs lists voices by id and names them (one is unnamed).
        "id": "eleven/eleven_flash_v2_5",
        "object": "model",
        "owned_by": "elevenlabs",
        "x_eugene_plexus": {
            "drivers": ["eleven"],
            "surfaces": ["speech"],
            "locality": "external",
            "ready_backends": 1,
            "voices": ["21m00Tcm4TlvDq8ikWAM", "EXAVITQu4vr4xnSDxMaL", "pNInz6obpgDQGcFmaJgB"],
            "voice_names": {"21m00Tcm4TlvDq8ikWAM": "Rachel", "EXAVITQu4vr4xnSDxMaL": "Sarah"},
            "speech_formats": ["mp3", "pcm"],
        },
    },
    {
        "id": "openrouter/whisper-turbo",
        "object": "model",
        "owned_by": "openrouter",
        "x_eugene_plexus": {
            "drivers": ["openrouter"],
            "surfaces": ["transcription"],
            "locality": "external",
            "ready_backends": 1,
        },
    },
    {
        "id": "oai/whisper-1",
        "object": "model",
        "owned_by": "openai",
        "x_eugene_plexus": {
            "drivers": ["oai"],
            "surfaces": ["transcription", "translation"],
            "locality": "external",
            "ready_backends": 1,
        },
    },
]

DRAFT = "A first answer 🎉, before searching."
DRAFT_REASONING = "No tools needed."


def chat_model(
    model_id: str,
    *,
    locality: str | None,
    window: int | None = 32768,
    owned_by: str | None = None,
    file_input: bool = False,
) -> dict[str, Any]:
    """A chat model as the gateway lists it: where it runs and how much it
    holds. `locality=None` leaves the key out; an outside model names its
    provider, as a cloud account's driver does; `file_input` says it takes a
    PDF as a file."""
    info: dict[str, Any] = {"surfaces": ["chat"], "ready_backends": 1}
    if file_input:
        info["file_input"] = True
    if window is not None:
        info["context_length"] = window
    if locality is not None:
        info["locality"] = locality
    if owned_by is not None:
        info["drivers"] = [owned_by]
    entry: dict[str, Any] = {"id": model_id, "object": "model", "x_eugene_plexus": info}
    if owned_by is not None:
        entry["owned_by"] = owned_by
    return entry


def search_model(
    model_id: str,
    *,
    locality: str | None = "local",
    ready: bool = True,
    owned_by: str | None = None,
) -> dict[str, Any]:
    """A search model as the gateway lists it (MR5): the `embeddings` surface, where it
    runs (`locality=None` leaves the key out, as an older gateway would) and whether a
    backend is running it."""
    info: dict[str, Any] = {"surfaces": ["embeddings"], "ready_backends": 1 if ready else 0}
    if locality is not None:
        info["locality"] = locality
    entry: dict[str, Any] = {"id": model_id, "object": "model", "x_eugene_plexus": info}
    if owned_by is not None:
        entry["owned_by"] = owned_by
    return entry


def sorter_model(
    model_id: str,
    *,
    locality: str | None = "local",
    ready: bool = True,
    owned_by: str | None = None,
) -> dict[str, Any]:
    """A result sorter as the gateway lists it (MR6b): the `rerank` surface, where it runs
    (`locality=None` leaves the key out, as an older gateway would) and whether a backend is
    running it."""
    entry = search_model(model_id, locality=locality, ready=ready, owned_by=owned_by)
    entry["x_eugene_plexus"]["surfaces"] = ["rerank"]
    return entry


# --------------------------------------------------------------------------- #
# servers
# --------------------------------------------------------------------------- #


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ServerThread:
    def __init__(self, app: Any, port: int | None = None) -> None:
        # A new server keeps the socket it was given port 0 on, so a parallel
        # test process (pytest -n) cannot take the port between asking and binding.
        sockets: list[socket.socket] | None = None
        if port is None:
            sock = socket.socket()
            if sys.platform != "win32":
                # As uvicorn's own socket does: on Linux a restart on this port
                # (restart_workbench) binds only if the old connections had it.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
            sockets = [sock]
        self.port = port
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": sockets}, daemon=True
        )

    def start(self) -> ServerThread:
        self.thread.start()
        deadline = time.perf_counter() + 15
        while not self.server.started:
            if time.perf_counter() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        stop_together(self)


def stop_together(*servers: ServerThread) -> None:
    """Stop servers at once: uvicorn's shutdown sleeps about 0.2 s whatever it has to do."""
    for server in servers:
        server.server.should_exit = True
    for server in servers:
        server.thread.join(timeout=15)


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
    #: Row 7: each `/oidc/subjects` call's ids; a person deleted from `people` is `unknown`.
    subject_calls: list[list[str]] = field(default_factory=list)
    #: Row 70c: claims each person's ID token carries beyond the standard ones
    #: (`eugene_groups`, `eugene_policies`, `eugene_groups_truncated`).
    claims: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Row 70c: what `/oidc/userinfo` says of a person beyond `sub`; their `claims` if unset.
    userinfo: dict[str, dict[str, Any]] = field(default_factory=dict)
    userinfo_calls: int = 0
    #: Row 70c: what `/oidc/groups` lists, or None for an Eugene too old to have it (404).
    groups: list[dict[str, str]] | None = field(default_factory=list)
    access: dict[str, str] = field(default_factory=dict)

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
        if "email" in person:  # row 7: only when the person has one, as Eugene sends it
            claims["email"] = person["email"]
        claims.update(self.claims.get(sub, {}))
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
                "userinfo_endpoint": f"{self.issuer}/userinfo",
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
                access = "at-" + secrets.token_urlsafe(8)
                self.access[access] = sub
                return {
                    "access_token": access,
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
                access = "at-" + secrets.token_urlsafe(8)
                self.access[access] = sub
                return {
                    "access_token": access,
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

        @app.get("/oidc/userinfo")
        async def userinfo(request: Request) -> Any:
            self.userinfo_calls += 1
            if not self.up:
                return JSONResponse({}, status_code=503)
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            sub = self.access.get(token)
            if sub is None or sub in self.disabled:
                return JSONResponse({"error": "invalid_token"}, status_code=401)
            person = self.people[sub]
            # Eugene's userinfo is never cut: a person's ID token claims, less the flag, unless
            # a test sets exactly what userinfo says (a cut answer included).
            if sub in self.userinfo:
                extra = dict(self.userinfo[sub])
            else:
                told = self.claims.get(sub, {})
                extra = {k: v for k, v in told.items() if k != "eugene_groups_truncated"}
            return {"sub": sub, "name": person["name"], "eugene_role": person["role"], **extra}

        @app.get("/oidc/groups")
        async def groups(request: Request) -> Any:
            if not self.up:
                return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
            if self.groups is None:
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            if not client_ok(request):
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            return {"groups": self.groups}

        @app.post("/oidc/subjects")
        async def subjects(request: Request) -> Any:
            if not self.up:
                return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
            if not client_ok(request):
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            asked = [str(s) for s in (await request.json())["subjects"]]
            self.subject_calls.append(asked)
            return {
                "known": [s for s in asked if s in self.people],
                "unknown": [s for s in asked if s not in self.people],
            }

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
    #: A searched answer's `url_citation`s, as the gateway sends them (rich
    #: content RC4); None sends one for example.org with no place in the text.
    citations: list[dict[str, Any]] | None = None
    #: The "draft" mode's progress marks around its search.
    draft_phases: tuple[str | None, ...] = ("started", "finished")
    release: asyncio.Event | None = None
    tool_arguments: str = '{"text":"hello"}'
    tool_name: str | None = None
    #: How many rounds of tool calls an answer makes, and how many a round.
    tool_rounds: int = 1
    tools_per_round: int = 1
    #: Row 32, mode "tools-script": each round's one call, as (tool name, arguments), in order;
    #: then the answer's words.
    tool_script: list[tuple[str, str]] = field(default_factory=list)
    repetition_modes: list[str | None] = field(default_factory=list)
    search: dict[str, Any] = field(default_factory=lambda: {"available": True, "reason": None})
    #: Where the chat model runs and how much it holds, as the gateway lists
    #: them: memory keeps a person's notes off a model that is not local and
    #: gives none to a small window. None leaves the key out, as an older
    #: gateway would.
    locality: str | None = "local"
    context_length: int | None = 32768
    #: Whether the chat model takes a PDF as a file (the gateway's `file_input`).
    file_input: bool = False
    #: Other chat models listed, whole, as `chat_model` builds them.
    more_chat_models: list[dict[str, Any]] = field(default_factory=list)
    #: End each answer with ` #N`, N its request's number, to tell versions apart.
    numbered: bool = False
    #: Search Suggestions HTML a searched answer's final chunk carries; `{n}`
    #: is the request's number (google-search-account.md GS4).
    suggestions: list[str] = field(default_factory=list)
    #: How an answer ends, and the output cap its final chunk says was sent.
    finish: str = "stop"
    output_cap: dict[str, Any] | None = None
    #: Reasoning each answer opens with, piece by piece (the reading check).
    reasoning: list[str] = field(default_factory=list)
    #: The image models listed (media screens), as the gateway lists them.
    image_models: list[dict[str, Any]] = field(default_factory=lambda: list(IMAGE_MODELS))
    audio_models: list[dict[str, Any]] = field(default_factory=lambda: list(AUDIO_MODELS))
    #: Every image request, with the door it came to: (`generations`|`edits`, body).
    image_requests: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    #: The image answered, whatever size was asked: 3x2, so asked and got differ.
    image_answer: bytes = b""
    image_delay: float = 0.0
    #: A refusal to answer every image request with: (status, message, param).
    image_refusal: tuple[int, str, str | None] | None = None
    #: Every speech request (JSON), and every transcription: (door, fields, file).
    speech_requests: list[dict[str, Any]] = field(default_factory=list)
    transcriptions: list[tuple[str, dict[str, Any], tuple[str, bytes, str]]] = field(
        default_factory=list
    )
    #: The seconds a transcription says it heard; None says tokens instead.
    heard_seconds: float | None = 3.0
    audio_delay: float = 0.0
    #: A refusal to answer every speech or transcription request with.
    audio_refusal: tuple[int, str, str | None] | None = None
    video_models: list[dict[str, Any]] = field(default_factory=lambda: list(VIDEO_MODELS))
    #: Every video submit's body, and every poll's handle, in order.
    video_requests: list[dict[str, Any]] = field(default_factory=list)
    video_polls: list[str] = field(default_factory=list)
    #: The jobs this gateway knows, by handle: how many polls each has had,
    #: and how it ends. A test may add one, as a restart finds it.
    video_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: As OpenRouter measured (P5): queued for this many polls, then done.
    video_polls_queued: int = 1
    #: How a job ends: "completed" or "failed".
    video_end: str = "completed"
    video_cost: float | None = 0.05
    video_content: bytes = MP4_CLIP
    #: A refusal to answer every video submit with: (status, message, param).
    video_refusal: tuple[int, str, str | None] | None = None
    #: Polls to answer with this (status, message) before answering again.
    video_poll_trouble: list[tuple[int, str]] = field(default_factory=list)
    #: The one model every gateway of these tests lists that only embeds, and says nothing of
    #: where it runs (so Workbench cannot place it); search by meaning's tests do without it.
    embedder_listed: bool = True
    #: How many times the list of models was asked for, and how long each answer is held back
    #: (a gateway that does not answer its list: the shared look, MR6b).
    models_asked: int = 0
    models_delay: float = 0.0
    #: Search models listed (`search_model`), whole; `/v1/embeddings` answers for each that is
    #: ready, with the stand-in meaning of `fake_embedding`.
    search_models: list[dict[str, Any]] = field(default_factory=list)
    #: Every embeddings request: its body, when it came, and how many were in the air then.
    embed_requests: list[dict[str, Any]] = field(default_factory=list)
    embed_in_flight: int = 0
    embed_peak: int = 0
    #: Requests whose caller hung up while they were held at the gate.
    embed_disconnects: int = 0
    #: A batch of passages is held until this is set (a thread's event: the test and the
    #: server are not one loop). Requests of one text are not held.
    embed_gate: threading.Event | None = None
    #: How many batches a closed gate still lets through, one each, before it holds the rest:
    #: a test that needs some passages given meaning and the others not, whatever the timing.
    embed_gate_lets: int = 0
    embed_delay: float = 0.0
    #: What an answer calls the model that was asked for, and how long its vectors are.
    embed_names: dict[str, str] = field(default_factory=dict)
    embed_dims: dict[str, int] = field(default_factory=dict)
    embed_dim: int = fake_embedding.DIM
    #: Answers for the next requests, as (status, body, headers), before it behaves.
    embed_script: list[tuple[int, dict[str, Any], dict[str, str]]] = field(default_factory=list)
    #: A passage with this text in it is refused as too long, whatever else is in its request.
    embed_refuse: str | None = None
    #: A request with this text in it answers with a vector that is not a number.
    embed_nan: str | None = None
    #: Result sorters listed (`sorter_model`), whole; `/v1/rerank` answers for each that is ready,
    #: scoring a passage by `fake_rerank.score` (0 to 1), best first.
    sorters: list[dict[str, Any]] = field(default_factory=list)
    #: Every rerank request: its body, when it came, and how many were in the air then.
    rerank_requests: list[dict[str, Any]] = field(default_factory=list)
    rerank_in_flight: int = 0
    #: Tokens a second the sorter reads, as `token_count` counts them (the passages, and the
    #: question once for each): a request takes that long. None answers at once.
    rerank_rate: float | None = None
    #: Seconds every request takes, beside what its tokens take.
    rerank_delay: float = 0.0
    #: Answers for the next requests, as (status, body, headers), before it behaves.
    rerank_script: list[tuple[int, Any, dict[str, str]]] = field(default_factory=list)
    #: A passage with this text in it is scored this, whatever the question: `{text: score}`.
    rerank_prefer: dict[str, float] = field(default_factory=dict)
    #: Memory suggestion calls (MR8): each body and the person token it came with, what each
    #: is answered (the model's content), and a refusal to answer them with instead.
    suggest_requests: list[dict[str, Any]] = field(default_factory=list)
    suggest_answer: str = '{"notes": []}'
    suggest_refusal: tuple[int, str] | None = None
    #: Row 26: report each answer's usage (its prompt counted as Workbench counts it), and the
    #: window that applied, as the gateway does on its final frame.
    usage: bool = False
    #: Refuse a prompt past this many tokens, with llama-server's words as the gateway passes
    #: them on (a 400 `invalid_request_error`, no code).
    refuse_over: int | None = None
    #: How a refusal past `refuse_over` comes: a 400 (`status`), or a 200 whose stream is an
    #: error frame (`frame`), as the gateway's streamed chat door sends it.
    overflow_as: str = "status"
    #: Say on the final frame that the backend dropped part of the prompt.
    truncated: bool = False
    #: Summary requests (not streamed, no response format): each body, and what they get.
    summary_requests: list[dict[str, Any]] = field(default_factory=list)
    summary_answer: str = "Earlier, the person and the model talked about the plan."
    summary_refusal: tuple[int, str] | None = None

    def rerank_tokens(self, body: dict[str, Any]) -> int:
        """What reading a request is worth, as Workbench counts it: each passage and the question
        once for each passage."""
        asked = token_count.estimate(body["query"])
        texts = [d if isinstance(d, str) else d["text"] for d in body["documents"]]
        return sum(token_count.estimate(t) + asked for t in texts)

    def sorts(self) -> list[dict[str, Any]]:
        """The bodies of the rerank requests that were sorts of a search, not the small probe that
        measures how fast the sorter reads (which asks about a greenhouse)."""
        return [
            r["body"] for r in self.rerank_requests if r["body"]["query"] != sorting.PROBE_QUESTION
        ]

    def probes(self) -> list[dict[str, Any]]:
        """The bodies of the small requests that measure how fast the sorter reads."""
        return [
            r["body"] for r in self.rerank_requests if r["body"]["query"] == sorting.PROBE_QUESTION
        ]

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/v1/models")
        async def models(request: Request) -> Any:
            self.models_asked += 1
            if self.models_delay:
                await asyncio.sleep(self.models_delay)
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
                            **(
                                {"context_length": self.context_length}
                                if self.context_length is not None
                                else {}
                            ),
                            "image_input": True,
                            **({"file_input": True} if self.file_input else {}),
                            "web_search": True,
                            "ready_backends": 1,
                            **({"locality": self.locality} if self.locality is not None else {}),
                        },
                    },
                    *self.more_chat_models,
                    *(
                        [
                            {
                                "id": "embedder",
                                "object": "model",
                                "x_eugene_plexus": {"surfaces": ["embeddings"]},
                            }
                        ]
                        if self.embedder_listed
                        else []
                    ),
                    *self.search_models,
                    *self.sorters,
                    *self.image_models,
                    *self.audio_models,
                    *self.video_models,
                ],
            }

        async def images(request: Request, door: str) -> Any:
            body = await request.json()
            self.image_requests.append((door, body))
            if request.headers.get("authorization") != f"Bearer {APP_KEY}":
                return JSONResponse({"error": {"message": "invalid key"}}, status_code=401)
            if self.image_delay:
                await asyncio.sleep(self.image_delay)
            if self.image_refusal is not None:
                code, message, param = self.image_refusal
                return JSONResponse(
                    {
                        "error": {
                            "message": message,
                            "type": "invalid_request_error",
                            "param": param,
                        }
                    },
                    status_code=code,
                )
            picture = base64.b64encode(self.image_answer or png(3, 2)).decode()
            return JSONResponse(
                {
                    "created": 1,
                    "data": [{"b64_json": picture} for _ in range(body.get("n") or 1)],
                    "usage": {"input_tokens": 6, "output_tokens": 1024, "total_tokens": 1030},
                    "x_eugene_plexus": {
                        "driver": "openrouter",
                        "backend": "openai_compat_http",
                        "latency_ms": 12,
                        "attempts": 1,
                        "tier": 1,
                    },
                },
                headers={"x-request-id": f"req-{len(self.image_requests)}"},
            )

        @app.post("/v1/images/generations")
        async def generations(request: Request) -> Any:
            return await images(request, "generations")

        @app.post("/v1/images/edits")
        async def edits(request: Request) -> Any:
            return await images(request, "edits")

        def audio_refused() -> Any:
            if self.audio_refusal is None:
                return None
            code, message, param = self.audio_refusal
            return JSONResponse(
                {"error": {"message": message, "type": "invalid_request_error", "param": param}},
                status_code=code,
            )

        @app.post("/v1/audio/speech")
        async def speech(request: Request) -> Any:
            body = await request.json()
            self.speech_requests.append(body)
            if self.audio_delay:
                await asyncio.sleep(self.audio_delay)
            if (refused := audio_refused()) is not None:
                return refused
            clip = wav(0.5) if body.get("response_format") == "wav" else MP3_CLIP
            return Response(
                content=clip,
                media_type="audio/wav" if clip.startswith(b"RIFF") else "audio/mpeg",
                headers={
                    "x-eugene-plexus-driver": "openrouter",
                    "x-eugene-plexus-latency-ms": "420",
                    "x-eugene-plexus-attempts": "1",
                    "x-request-id": f"speech-{len(self.speech_requests)}",
                },
            )

        async def hear(request: Request, door: str) -> Any:
            form = await request.form()
            upload = form["file"]
            assert not isinstance(upload, str)
            fields = {k: v for k, v in form.items() if k != "file"}
            sent = (upload.filename or "", await upload.read(), upload.content_type or "")
            self.transcriptions.append((door, fields, sent))
            if self.audio_delay:
                await asyncio.sleep(self.audio_delay)
            if (refused := audio_refused()) is not None:
                return refused
            usage = (
                {"type": "duration", "seconds": self.heard_seconds}
                if self.heard_seconds is not None
                else {"type": "tokens", "input_tokens": 30, "output_tokens": 11, "total_tokens": 41}
            )
            return JSONResponse(
                {"text": " The bench is ready.", "usage": usage},
                headers={"x-eugene-plexus-driver": "openrouter", "x-request-id": "hear-1"},
            )

        @app.post("/v1/audio/transcriptions")
        async def transcriptions(request: Request) -> Any:
            return await hear(request, "transcriptions")

        @app.post("/v1/audio/translations")
        async def translations(request: Request) -> Any:
            return await hear(request, "translations")

        def video_error(code: int, message: str, param: str | None = None) -> Any:
            return JSONResponse(
                {"error": {"message": message, "type": "invalid_request_error", "param": param}},
                status_code=code,
            )

        def resource(handle: str, job: dict[str, Any], state: str) -> dict[str, Any]:
            body: dict[str, Any] = {
                "id": handle,
                "object": "video",
                "model": job["model"],
                "status": state,
                "progress": 100 if state == "completed" else 0,
                "created_at": 1,
                "completed_at": 2 if state == "completed" else None,
                "expires_at": None,
                "prompt": None,
                "size": job.get("size") or "auto",
                "seconds": job.get("seconds") or "auto",
                "remixed_from_video_id": None,
                "error": {"code": "video_generation_failed", "message": job["reason"]}
                if state == "failed"
                else None,
            }
            if state in ("completed", "failed") and self.video_cost is not None:
                body["x_eugene_plexus"] = {"cost_usd": self.video_cost}
            return body

        @app.post("/v1/videos")
        async def create_video(request: Request) -> Any:
            body = await request.json()
            self.video_requests.append(body)
            if request.headers.get("authorization") != f"Bearer {APP_KEY}":
                return video_error(401, "invalid key")
            if self.video_refusal is not None:
                return video_error(*self.video_refusal)
            handle = f"video_{len(self.video_requests)}"
            self.video_jobs[handle] = {
                "model": body["model"],
                "seconds": body.get("seconds"),
                "size": body.get("size"),
                "polls": 0,
                "reason": "The provider could not make this video.",
            }
            answer = resource(handle, self.video_jobs[handle], "queued")
            answer["x_eugene_plexus"] = {"driver": "openrouter", "latency_ms": 3250, "attempts": 1}
            return JSONResponse(
                answer, headers={"x-request-id": f"video-req-{len(self.video_requests)}"}
            )

        @app.get("/v1/videos/{handle}")
        async def poll_video(handle: str) -> Any:
            self.video_polls.append(handle)
            if self.video_poll_trouble:
                return video_error(*self.video_poll_trouble.pop(0))
            job = self.video_jobs.get(handle)
            if job is None:
                return video_error(404, f"No video job '{handle}' for this key.", "video_id")
            job["polls"] += 1
            state = "queued" if job["polls"] <= self.video_polls_queued else self.video_end
            return resource(handle, job, state)

        @app.get("/v1/videos/{handle}/content")
        async def video_bytes(handle: str) -> Any:
            if handle not in self.video_jobs:
                return video_error(404, f"No video job '{handle}' for this key.", "video_id")
            return Response(content=self.video_content, media_type="video/mp4")

        @app.post("/__fake/search-models")
        async def fake_search_models(request: Request) -> Any:
            """A test's hand on the gateway while a browser drives Workbench (MR5): which search
            models it lists from now on, as `{"models": [{"id": ..., "ready": true}]}`. The
            Chrome check starts and stops a search model through it, as the owner would in
            Eugene's console."""
            body = await request.json()
            self.embedder_listed = False
            self.search_models = [
                search_model(
                    m["id"], locality=m.get("locality", "local"), ready=m.get("ready", True)
                )
                for m in body["models"]
            ]
            # `hold`: the passages' answers wait (so a screen can be seen while meaning is being
            # added), and are let go when it is false.
            if body.get("hold"):
                self.embed_gate = threading.Event()
            elif "hold" in body and self.embed_gate is not None:
                self.embed_gate.set()
                self.embed_gate = None
            return {"listed": [m["id"] for m in self.search_models]}

        @app.post("/__fake/sorters")
        async def fake_sorters(request: Request) -> Any:
            """A test's hand on the gateway while a browser drives Workbench (MR6b): which result
            sorters it lists from now on, as `{"sorters": [{"id": ..., "ready": true}]}`, and how many
            tokens a second they read (`rate`, as Workbench counts tokens; null answers at once). The
            Chrome check lists, slows and stops a sorter through it, as the owner would in Eugene's
            console."""
            body = await request.json()
            self.sorters = [
                sorter_model(
                    m["id"], locality=m.get("locality", "local"), ready=m.get("ready", True)
                )
                for m in body["sorters"]
            ]
            if "rate" in body:
                self.rerank_rate = float(body["rate"]) if body["rate"] else None
            return {"listed": [m["id"] for m in self.sorters]}

        @app.get("/__fake/sorter-calls")
        async def fake_sorter_calls() -> Any:
            """What the sorters were asked, so a browser check can say that a sorter that was too
            slow, stopped or off was asked nothing: the sorts of a question, and the small test of
            how fast one reads."""
            return {"sorts": len(self.sorts()), "probes": len(self.probes())}

        @app.post("/v1/embeddings")
        async def embeddings(request: Request) -> Any:
            body = await request.json()
            self.embed_requests.append(
                {"body": body, "at": time.perf_counter(), "in_flight": self.embed_in_flight}
            )
            if request.headers.get("authorization") != f"Bearer {APP_KEY}":
                return JSONResponse({"error": {"message": "invalid key"}}, status_code=401)
            self.embed_in_flight += 1
            self.embed_peak = max(self.embed_peak, self.embed_in_flight)
            try:
                given = body["input"] if isinstance(body["input"], list) else [body["input"]]
                # Only a batch of passages is held: the one-text requests (a question, and the
                # tiny one that asks which model answers) go through.
                while (
                    self.embed_gate is not None and len(given) > 1 and not self.embed_gate.is_set()
                ):
                    if self.embed_gate_lets > 0:
                        self.embed_gate_lets -= 1
                        break
                    if await request.is_disconnected():
                        self.embed_disconnects += 1
                        return Response(status_code=499)
                    await asyncio.sleep(0.01)
                if self.embed_delay:
                    await asyncio.sleep(self.embed_delay)
                if self.embed_script:
                    code, payload, headers = self.embed_script.pop(0)
                    return JSONResponse(payload, status_code=code, headers=headers)
                model = body["model"]
                entry = next((m for m in self.search_models if m["id"] == model), None)
                if entry is None:
                    return JSONResponse(
                        {"error": {"message": f"The model '{model}' is not served here."}},
                        status_code=404,
                    )
                if not entry["x_eugene_plexus"].get("ready_backends"):
                    return JSONResponse(
                        {"error": {"message": f"No backend for '{model}' is ready."}},
                        status_code=503,
                    )
                texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
                if self.embed_refuse and any(self.embed_refuse in t for t in texts):
                    return JSONResponse(
                        {
                            "error": {
                                "message": "input (1631 tokens) is too large to process. "
                                "increase the physical batch size (current batch size: 512)",
                                "type": "invalid_request_error",
                            }
                        },
                        status_code=400,
                    )
                dim = self.embed_dims.get(model, self.embed_dim)
                vectors = [fake_embedding.vector(t, dim) for t in texts]
                if self.embed_nan and any(self.embed_nan in t for t in texts):
                    vectors[0] = [float("nan"), *vectors[0][1:]]
                # Not JSONResponse: it refuses the NaN a broken model can answer with.
                return Response(
                    json.dumps(
                        {
                            "object": "list",
                            "data": [
                                {"object": "embedding", "index": i, "embedding": v}
                                for i, v in enumerate(vectors)
                            ],
                            "model": self.embed_names.get(model, model),
                            "usage": {
                                "prompt_tokens": sum(len(t.split()) for t in texts),
                                "total_tokens": sum(len(t.split()) for t in texts),
                            },
                        }
                    ),
                    media_type="application/json",
                    headers={"x-request-id": f"embed-{len(self.embed_requests)}"},
                )
            finally:
                self.embed_in_flight -= 1

        @app.post("/v1/rerank")
        async def rerank(request: Request) -> Any:
            body = await request.json()
            self.rerank_requests.append(
                {"body": body, "at": time.perf_counter(), "in_flight": self.rerank_in_flight}
            )
            if request.headers.get("authorization") != f"Bearer {APP_KEY}":
                return JSONResponse({"error": {"message": "invalid key"}}, status_code=401)
            self.rerank_in_flight += 1
            try:
                if self.rerank_delay:
                    await asyncio.sleep(self.rerank_delay)
                if self.rerank_rate:
                    await asyncio.sleep(self.rerank_tokens(body) / self.rerank_rate)
                if self.rerank_script:
                    code, payload, headers = self.rerank_script.pop(0)
                    if isinstance(payload, str):
                        return Response(
                            payload,
                            status_code=code,
                            media_type="text/plain",
                            headers=headers,
                        )
                    return JSONResponse(payload, status_code=code, headers=headers)
                model = body.get("model")
                entry = next((m for m in self.sorters if m["id"] == model), None)
                if entry is None:
                    return JSONResponse(
                        {"error": {"message": f"The model '{model}' is not served here."}},
                        status_code=404,
                    )
                if not entry["x_eugene_plexus"].get("ready_backends"):
                    return JSONResponse(
                        {"error": {"message": f"No backend for '{model}' is ready."}},
                        status_code=503,
                    )
                texts = [d if isinstance(d, str) else d["text"] for d in body["documents"]]
                scored = [
                    {
                        "index": i,
                        "relevance_score": fake_rerank.score(
                            body["query"], text, self.rerank_prefer
                        ),
                    }
                    for i, text in enumerate(texts)
                ]
                scored.sort(key=lambda r: (-r["relevance_score"], r["index"]))
                if isinstance(body.get("top_n"), int):
                    scored = scored[: body["top_n"]]
                tokens = self.rerank_tokens(body)
                return {
                    "object": "list",
                    "model": model,
                    "results": scored,
                    "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
                }
            finally:
                self.rerank_in_flight -= 1

        @app.post("/v1/chat/completions")
        async def chat(request: Request) -> Any:
            body = await request.json()
            if body.get("response_format") and body.get("stream") is False:
                # A memory suggestion call (MR8): not streamed, answered with `suggest_answer`,
                # kept apart so a chat's own requests stay what `requests` holds.
                self.suggest_requests.append(
                    {"body": body, "person": request.headers.get("X-Eugene-Person-Token")}
                )
                if self.suggest_refusal is not None:
                    status, message = self.suggest_refusal
                    return JSONResponse({"error": {"message": message}}, status_code=status)
                return JSONResponse(
                    {
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": self.suggest_answer},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                )
            if body.get("stream") is False:
                # A summary of earlier messages (row 26): kept apart from a chat's own requests.
                self.summary_requests.append(
                    {"body": body, "person": request.headers.get("X-Eugene-Person-Token")}
                )
                prompt = context.count_messages(body["messages"])
                if self.summary_refusal is not None:
                    status, message = self.summary_refusal
                    return JSONResponse({"error": {"message": message}}, status_code=status)
                if self.refuse_over is not None and prompt > self.refuse_over:
                    return JSONResponse(
                        {
                            "error": {
                                "message": "The backend rejected the request: request "
                                f"({prompt} tokens) exceeds the available context size "
                                f"({self.refuse_over} tokens), try increasing it",
                                "type": "invalid_request_error",
                                "code": None,
                            }
                        },
                        status_code=400,
                    )
                return JSONResponse(
                    {
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": self.summary_answer},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": prompt,
                            "completion_tokens": token_count.estimate(self.summary_answer),
                        },
                    }
                )
            self.requests.append(body)
            if self.refuse_over is not None:
                prompt = context.count_messages(body["messages"])
                if prompt > self.refuse_over and self.overflow_as == "frame":
                    # The streamed chat door: a 200, then the driver's failure in an error frame
                    # with no code, llama-server's body inside it (gateway#18).
                    words = (
                        "driver 'local' (http://127.0.0.1:1) returned 400 — Backend rejected "
                        "the request — openai_compat_http returned 400: "
                        + json.dumps(
                            {
                                "error": {
                                    "code": 400,
                                    "message": f"request ({prompt} tokens) exceeds the "
                                    f"available context size ({self.refuse_over} tokens), "
                                    "try increasing it",
                                    "type": "exceed_context_size_error",
                                    "n_prompt_tokens": prompt,
                                    "n_ctx": self.refuse_over,
                                }
                            }
                        )
                    )

                    async def failed() -> AsyncIterator[str]:
                        frame = {
                            "error": {
                                "message": words,
                                "type": "upstream_error",
                                "param": None,
                                "code": None,
                            }
                        }
                        yield "data: " + json.dumps(frame) + "\n\n"
                        yield "data: [DONE]\n\n"

                    return StreamingResponse(failed(), media_type="text/event-stream")
                if prompt > self.refuse_over:
                    return JSONResponse(
                        {
                            "error": {
                                "message": "The backend rejected the request: request "
                                f"({prompt} tokens) exceeds the available context size "
                                f"({self.refuse_over} tokens), try increasing it",
                                "type": "invalid_request_error",
                                "code": None,
                            }
                        },
                        status_code=400,
                    )
            number = len(self.requests)
            self.repetition_modes.append(request.headers.get("X-Eugene-Repetition-Mode"))
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

            def extension(searched: bool | None = None) -> dict[str, Any]:
                searched = "web_search_options" in body if searched is None else searched
                found: dict[str, Any] = {"web_searches": 1 if searched else 0}
                if self.usage and self.context_length is not None:
                    found["context_length"] = self.context_length
                if self.truncated:
                    found["prompt_truncated"] = True
                if searched and self.suggestions:
                    found["search_suggestions"] = [
                        s.replace("{n}", str(number)) for s in self.suggestions
                    ]
                if self.output_cap is not None:
                    found["output_cap"] = self.output_cap
                return found

            async def frames() -> AsyncIterator[str]:
                messages = body["messages"]
                asked_at = max(i for i, m in enumerate(messages) if m["role"] == "user")
                answered = sum(1 for m in messages[asked_at:] if m["role"] == "tool")
                wanted = self.tool_rounds * self.tools_per_round
                if self.mode == "tools-script" and answered < len(self.tool_script):
                    name, arguments = self.tool_script[answered]
                    yield chunk({"content": "I will use the tool."})
                    yield chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": f"call-{answered + 1}",
                                    "type": "function",
                                    "function": {"name": name, "arguments": arguments},
                                }
                            ]
                        }
                    )
                    yield chunk({}, finish="tool_calls", extension=extension())
                    yield "data: [DONE]\n\n"
                    return
                if (
                    self.mode.startswith("tools")
                    and self.mode != "tools-script"
                    and answered < wanted
                ):
                    name = body["tools"][0]["function"]["name"]
                    if self.tool_name:
                        name = next(
                            t["function"]["name"]
                            for t in body["tools"]
                            if f": {self.tool_name}." in t["function"]["description"]
                        )
                    if self.mode == "tools-unknown":
                        name = "not_offered"
                    yield chunk({"content": "I will use the tool."})
                    for index in range(self.tools_per_round):
                        yield chunk(
                            {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "id": f"call-{answered + index + 1}",
                                        "type": "function",
                                        "function": {
                                            "name": name,
                                            "arguments": self.tool_arguments[:5],
                                        },
                                    }
                                ]
                            }
                        )
                        yield chunk(
                            {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "function": {"arguments": self.tool_arguments[5:]},
                                    }
                                ]
                            }
                        )
                    yield chunk(
                        {},
                        finish="length" if self.mode == "tools-cut" else "tool_calls",
                        extension=extension(),
                    )
                    yield "data: [DONE]\n\n"
                    return
                yield chunk(
                    choices=False, extension={"progress": {"stage": "prompt", "prompt_tokens": 10}}
                )
                for piece in self.reasoning:
                    if self.delay:
                        await asyncio.sleep(self.delay)
                    yield chunk({"reasoning_content": piece})
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
                if self.numbered:
                    yield chunk({"content": f" #{number}"})
                if self.mode == "cut":
                    return
                if self.mode == "repetition":
                    yield 'data: {"error":{"code":"repetition_detected","message":"Stopped because the response appears to be repeating."}}\n\n'
                    yield "data: [DONE]\n\n"
                    return
                annotations = []
                if "web_search_options" in body:
                    annotations = (
                        [{"type": "url_citation", "url_citation": c} for c in self.citations]
                        if self.citations is not None
                        else [
                            {
                                "type": "url_citation",
                                "url_citation": {
                                    "url": "https://example.org/page",
                                    "title": "A page",
                                },
                            }
                        ]
                    )
                    yield chunk({"annotations": annotations})
                yield chunk({}, finish=self.finish, extension=extension(bool(annotations)))
                if self.usage:
                    written = "".join(self.words)
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "id": "c",
                                "object": "chat.completion.chunk",
                                "created": 0,
                                "model": MODEL,
                                "choices": [],
                                "usage": {
                                    "prompt_tokens": context.count_messages(body["messages"]),
                                    "completion_tokens": token_count.estimate(written),
                                    "prompt_tokens_details": {"cached_tokens": 0},
                                },
                            }
                        )
                        + "\n\n"
                    )
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

    def put(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.put(path, headers=self.headers(kw.pop("headers", None)), **kw)

    def delete(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.delete(path, headers=self.headers(kw.pop("headers", None)), **kw)

    def new_chat(self) -> str:
        return str(self.post("/api/chats", json={"model": MODEL}).json()["id"])

    def wait_read(self, chat_id: str, file_id: str, seconds: float = 30) -> dict[str, Any]:
        """The chat's file once Workbench has finished reading it (any end)."""
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            listed = self.get(f"/api/chats/{chat_id}/files").json()["files"]
            found = next(f for f in listed if f["id"] == file_id)
            if found["reading"] is None or found["reading"]["state"] != "reading":
                return dict(found)
            time.sleep(0.05)
        raise AssertionError("the file was not read in time")

    def wait_answer(self, chat_id: str, seconds: float = 60) -> dict[str, Any]:
        deadline = time.perf_counter() + seconds
        messages: list[dict[str, Any]] = []
        while time.perf_counter() < deadline:
            messages = self.get(f"/api/chats/{chat_id}").json()["messages"]
            if messages and messages[-1]["status"] != "running":
                return dict(messages[-1])
            time.sleep(0.05)
        last = messages[-1] if messages else None
        raise AssertionError(
            f"the answer did not finish within {seconds} seconds; last answer: {last}"
        )


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


@dataclass
class World:
    eugene: FakeEugene
    gateway: FakeGateway
    workbench: str
    data: Path
    settings: Settings
    server: ServerThread

    def browser(self) -> Browser:
        return Browser(self.workbench, self.eugene)

    def restart_workbench(self) -> None:
        self.server.stop()
        self.server = ServerThread(create_app(self.settings), self.server.port).start()


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
    world = World(eugene, gateway, workbench.url, tmp_path / "data", settings, workbench)
    try:
        yield world
    finally:
        world.server.stop()
        stop_together(gateway_server, eugene_server)


@pytest.fixture
def notes(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An empty folder to grant (MR7): apart from Workbench's own files, which cannot be granted."""
    return tmp_path_factory.mktemp("mr7-notes")


@pytest.fixture
def more_notes(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A second empty folder to grant, for a test with two."""
    return tmp_path_factory.mktemp("mr7-more")


@pytest.fixture
def quiet_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The folder loop looks again often, so a test that changes what it waits for is not kept
    waiting half a minute for the loop to notice. (What is asked for is checked at once; this only
    shortens its idle sleep.)"""
    from eugene_plexus_workbench.knowledge import folder_sync

    monkeypatch.setattr(folder_sync, "TICK", 0.05)


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
