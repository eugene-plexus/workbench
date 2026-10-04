"""A Workbench session: an `HttpOnly` cookie and a request secret (W3).

A browser sends a cookie for a host to **every port on it**, and on a home
network's plain `http` there is no `__Host-` prefix or `Secure` flag to
narrow that. So Workbench's cookie reaches every other service on the
machine -- the console, and Open WebUI once C4 sits beside Workbench, which
runs its admin's tools in its own process. A cookie alone must therefore be
worth nothing.

So every call needs **both**: the cookie, which no script can read, and a
secret the page keeps in its own storage, which a browser keeps per origin
-- port included -- and sends as a header. The secret is handed over once,
in the address fragment of the redirect that finishes a sign-in; a fragment
is never sent to a server, so no other port's server ever sees it. The
header is also the CSRF defence, and a changing call must carry this
origin's `Origin` as well.

Only hashes are stored. A copy of the database opens no session.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, replace

from fastapi import HTTPException, Request, status

from .signin import Provider, SignInRefused, SignInUnavailable
from .store import Person, SessionRow, Store

SESSION_COOKIE = "workbench_session"
SIGNIN_COOKIE = "workbench_signin"
SECRET_HEADER = "x-workbench-secret"
#: As long as Eugene's refresh token lives (C2 D6), then sign in again.
SESSION_SECONDS = 30 * 24 * 3600
#: Refresh this long before the access token's own expiry.
_EARLY = 30.0
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class NewSession:
    cookie: str
    secret: str


class SignedOut(HTTPException):
    """401 with the reason, which the page shows on its sign-in screen."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"signedIn": False, "reason": reason, "message": message},
        )


def own_origin(request: Request) -> str:
    return f"{request.url.scheme}://{request.headers.get('host', '')}"


class Sessions:
    """Creates, checks, refreshes and ends sessions."""

    def __init__(self, store: Store, provider: Provider) -> None:
        self._store = store
        self._provider = provider
        self._locks: dict[str, asyncio.Lock] = {}

    async def create(self, sub: str, refresh_token: str | None, expires_in: float) -> NewSession:
        cookie, secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.time()
        await self._store.put_session(
            SessionRow(
                id_hash=digest(cookie),
                secret_hash=digest(secret),
                sub=sub,
                refresh_token=refresh_token,
                access_expires_at=now + expires_in,
                created_at=now,
                expires_at=now + SESSION_SECONDS,
            )
        )
        return NewSession(cookie=cookie, secret=secret)

    async def end(self, request: Request) -> None:
        cookie = request.cookies.get(SESSION_COOKIE)
        if not cookie:
            return
        row = await self._store.session(digest(cookie))
        if row is None:
            return
        await self._store.delete_session(row.id_hash)
        if row.refresh_token:
            await self._provider.revoke(row.refresh_token)

    async def person(self, request: Request) -> Person:
        """Who is asking, or 401 saying why not. Refreshes when due."""
        if request.method in _UNSAFE and request.headers.get("origin") != own_origin(request):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"message": "This request did not come from Workbench's own page."},
            )
        cookie = request.cookies.get(SESSION_COOKIE)
        secret = request.headers.get(SECRET_HEADER)
        if not cookie or not secret:
            raise SignedOut("none", "Sign in with Eugene to use Workbench.")
        row = await self._store.session(digest(cookie))
        if row is None or not hmac.compare_digest(row.secret_hash, digest(secret)):
            raise SignedOut("none", "Sign in with Eugene to use Workbench.")
        now = time.time()
        if now >= row.expires_at:
            await self._store.delete_session(row.id_hash)
            raise SignedOut("expired", "Your Workbench sign-in is a month old. Sign in again.")
        if now >= row.access_expires_at - _EARLY:
            await self._refresh(row)
        person = await self._store.person(row.sub)
        if person is None:
            raise SignedOut("none", "Sign in with Eugene to use Workbench.")
        return replace(person, session_id=row.id_hash)

    async def _refresh(self, row: SessionRow) -> None:
        lock = self._locks.setdefault(row.id_hash, asyncio.Lock())
        async with lock:
            current = await self._store.session(row.id_hash)
            if current is None:
                raise SignedOut("revoked", _REVOKED)
            if time.time() < current.access_expires_at - _EARLY:
                return  # another request refreshed it while this one waited
            if not current.refresh_token:
                await self._store.delete_session(row.id_hash)
                raise SignedOut("revoked", _REVOKED)
            try:
                identity = await self._provider.refresh(current.refresh_token)
            except SignInRefused:
                await self._store.delete_session(row.id_hash)
                raise SignedOut("revoked", _REVOKED) from None
            except SignInUnavailable as exc:
                # Not signed out: nobody can be revoked while Eugene cannot
                # be asked either, and the session works again once it can.
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "message": "Workbench could not check your sign-in with Eugene: "
                        f"{exc}. Try again in a moment."
                    },
                ) from exc
            if identity.sub != current.sub:
                await self._store.delete_session(row.id_hash)
                raise SignedOut("revoked", _REVOKED)
            await self._store.upsert_person(
                Person(
                    sub=identity.sub,
                    name=identity.name,
                    role=identity.role,
                    username=identity.username,
                )
            )
            await self._store.refreshed(
                row.id_hash, identity.refresh_token, time.time() + identity.expires_in
            )


_REVOKED = (
    "Eugene no longer accepts this sign-in. The owner may have turned your account off or "
    "given it a new password. Sign in again, or ask the owner."
)
