"""An explicit browser origin; forwarding headers never select it."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse


class SchemeByPort:
    """LT2: the agent's door sends a TLS connection, decrypted, to a second
    loopback port, so a request that arrives there came from an https page.
    Nothing a caller sends chooses it: the port is the socket's own."""

    def __init__(self, app: Any, *, tls_port: int) -> None:
        self.app = app
        self.tls_port = tls_port

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        server = scope.get("server")
        if scope["type"] in ("http", "websocket") and server and server[1] == self.tls_port:
            scheme = "https" if scope["type"] == "http" else "wss"
            scope = dict(scope, scheme=scheme)
        await self.app(scope, receive, send)


class CanonicalOrigin:
    def __init__(self, app: Any, *, origin: str) -> None:
        self.app = app
        self.authority = urlsplit(origin).netloc.encode("ascii")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            hosts = [v for k, v in scope["headers"] if k.lower() == b"host"]
            local = (
                len(hosts) == 1
                and hosts[0].split(b":")[0] == b"127.0.0.1"
                and scope.get("path") in ("/healthz", "/v1/config", "/v1/config/schema")
            )
            if hosts != [self.authority] and not local:
                await PlainTextResponse("Unrecognised host", status_code=421)(scope, receive, send)
                return
            if not local:
                scope = dict(scope, scheme="https")
        await self.app(scope, receive, send)
