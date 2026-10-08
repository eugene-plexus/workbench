"""The page, and the headers every answer carries.

**Model output is untrusted** (W5), and a page that renders it holds a
session. So the Content Security Policy lets scripts and styles come only
from Workbench itself with nothing inline, images only from Workbench,
`data:` and `blob:` -- an image an answer names is never fetched -- and no
framing. The page itself renders an answer's images as links (`web/`); this
is the second lock on the same door.

The front end is a single-page app (W9): every path that is not the API,
signing in, or one of its own files is answered with `index.html`, and the
app reads the address itself.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "media-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

#: A working scene (workbench.md §6.1) is an SVG whose animation is its own
#: inline `<style>`. Some browsers hold an image to the policy it was served
#: with, so the page's `style-src 'self'` would freeze it. It gets its own:
#: that style and nothing else. The test gate (`web/src/sceneGate.ts`) keeps
#: script and outside references out of the file itself.
SCENE_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"

_API_PREFIXES = ("/api/", "/v1/", "/oidc/")

NO_UI = """<!doctype html><meta charset="utf-8"><title>Workbench</title>
<p>Workbench is running, but this build has no page to show. It was installed
from source without its built front end; install it from Eugene's app
catalogue, which uses the build that has one.</p>"""


def static_dir(configured: Path | None) -> Path | None:
    """The built front end: a developer's own, else the package's."""
    if configured is not None:
        return configured if (configured / "index.html").is_file() else None
    packaged = Path(str(importlib.resources.files("eugene_plexus_workbench").joinpath("static")))
    return packaged if (packaged / "index.html").is_file() else None


class SecurityHeaders:
    """Pure ASGI, so a streamed answer passes through unbuffered: a middleware
    that collects the body would hold every token until the answer ended."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        api = str(scope.get("path", "")).startswith("/api/")

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "content-security-policy" not in headers:
                    headers["Content-Security-Policy"] = CSP
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Frame-Options"] = "DENY"
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                if api and "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, with_headers)


def mount(app: FastAPI, root: Path | None) -> None:
    """Serve the page, its files, and `index.html` for every other path."""

    built = root.resolve() if root is not None else None

    # Not async: it touches the disk, and FastAPI runs a plain function in
    # its thread pool rather than on the loop every stream shares.
    @app.get("/{path:path}", include_in_schema=False)
    def page(path: str) -> Response:
        if ("/" + path).startswith(_API_PREFIXES):
            return Response(status_code=404)
        if root is None or built is None:
            return HTMLResponse(NO_UI, status_code=503)
        if path:
            candidate = (root / path).resolve()
            # A path that climbs out of the build is not one of its files.
            if candidate.is_file() and candidate.is_relative_to(built):
                cache = (
                    "public, max-age=31536000, immutable"
                    if path.startswith("assets/")
                    else "no-cache"
                )
                headers = {"Cache-Control": cache}
                if path.startswith("scenes/") and path.endswith(".svg"):
                    headers["Content-Security-Policy"] = SCENE_CSP
                return FileResponse(candidate, headers=headers)
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})
