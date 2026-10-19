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

from .person_context import current as person_context
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
    configured = getattr(request.app.state.settings, "public_origin", None)
    if configured:
        return str(configured)
    return f"{request.url.scheme}://{request.headers.get('host', '')}"


def cookie_name(request: Request, name: str) -> str:
    return "__Host-" + name if request.url.scheme == "https" else name


def signin_path(request: Request) -> str:
    return "/" if request.url.scheme == "https" else "/oidc"


class Sessions:
    """Creates, checks, refreshes and ends sessions."""

    def __init__(self, store: Store, provider: Provider) -> None:
        self._store = store
        self._provider = provider
        self._locks: dict[str, asyncio.Lock] = {}

    async def create(
        self,
        sub: str,
        refresh_token: str | None,
        expires_in: float,
        access_token: str | None = None,
    ) -> NewSession:
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
                access_token=access_token,
            )
        )
        return NewSession(cookie=cookie, secret=secret)

    async def end(self, request: Request) -> None:
        cookie = request.cookies.get(cookie_name(request, SESSION_COOKIE))
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
        cookie = request.cookies.get(cookie_name(request, SESSION_COOKIE))
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
        person_context.set((person.sub, row.id_hash))
        return replace(person, session_id=row.id_hash)

    async def _refresh(self, row: SessionRow, *, need_access: bool = False) -> None:
        lock = self._locks.setdefault(row.id_hash, asyncio.Lock())
        async with lock:
            current = await self._store.session(row.id_hash)
            if current is None:
                raise SignedOut("revoked", _REVOKED)
            if time.time() < current.access_expires_at - _EARLY and (
                not need_access or current.access_token
            ):
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
                row.id_hash,
                identity.refresh_token,
                time.time() + identity.expires_in,
                identity.access_token,
            )

    async def gateway_token(self) -> str:
        """Only a current owner's sign-in, never a process-wide service fallback."""
        from .hub import HubError

        identity = person_context.get()
        if identity is None:
            raise HubError(
                "Sign in to use models. Background work waits for its owner's sign-in.", status=401
            )
        sub, session_id = identity
        row = (
            await self._store.session(session_id)
            if session_id
            else await self._store.latest_session(sub, time.time())
        )
        if row is None or row.sub != sub or time.time() >= row.expires_at:
            raise HubError("Your sign-in has ended. Sign in again to use models.", status=401)
        try:
            if not row.access_token or time.time() >= row.access_expires_at - _EARLY:
                await self._refresh(row, need_access=True)
                row = await self._store.session(row.id_hash)
        except HTTPException as exc:
            raise HubError(
                "Workbench could not renew your model access. Sign in again if your session ended.",
                status=exc.status_code,
            ) from None
        if row is None or not row.access_token:
            raise HubError(
                "Eugene did not provide a person token. Update Eugene and sign in again.",
                status=401,
            )
        return row.access_token

    async def for_connection(self, sub: str) -> Person:
        """The person a connection acts for, through their newest live sign-in (MR9b).

        A connection holds no sign-in of its own: it works while its person has one here,
        and that sign-in is checked with Eugene as a page's is (refreshed once its access is
        ten minutes old), so a person Eugene turned off, whose password changed or whose
        sign-ins were ended, loses every connection within those minutes. `SignedOut` when
        there is none; a 503 when Eugene cannot be asked."""
        row = await self._store.latest_session(sub, time.time())
        if row is None:
            raise SignedOut(
                "none", "Sign in to Workbench again to use your connections to other apps."
            )
        if time.time() >= row.access_expires_at - _EARLY:
            await self._refresh(row)
        person = await self._store.person(sub)
        if person is None:
            raise SignedOut("none", "Sign in to Workbench again to use your connections.")
        person_context.set((person.sub, row.id_hash))
        return replace(person, session_id=row.id_hash)

    async def current(self, session_id: str | None, sub: str) -> Person:
        """Recheck an already authenticated action's sign-in before dispatch."""
        row = await self._store.session(session_id) if session_id else None
        if row is None or row.sub != sub or time.time() >= row.expires_at:
            raise SignedOut("revoked", _REVOKED)
        if time.time() >= row.access_expires_at - _EARLY:
            await self._refresh(row)
        person = await self._store.person(sub)
        if person is None:
            raise SignedOut("revoked", _REVOKED)
        person_context.set((person.sub, row.id_hash))
        return replace(person, session_id=row.id_hash)


_REVOKED = (
    "Eugene no longer accepts this sign-in. The owner may have turned your account off or "
    "given it a new password. Sign in again, or ask the owner."
)
