"""Signing people in with Eugene, over OpenID Connect (C2, `workbench-v1.md` W2).

Workbench is a confidential client of the control root's provider, reached
at the issuer the agent hands it: the authorization code flow with PKCE
(S256), `state` and `nonce`, the client's secret sent as HTTP Basic, and
the ID token checked here -- RS256 under a key in the provider's JWKS,
this issuer, this client, unexpired, this nonce.

**Authlib's pieces, one HTTP client.** PKCE and token generation are
Authlib's and the token checks are joserfc's (Authlib's JOSE half). The
calls themselves are three POSTs made with this module's one client,
rather than an `AsyncOAuth2Client` per sign-in: that class is an httpx
client, and building one per call is the per-call client this project
forbids (`CLAUDE.md`, one HTTP client per instance).

**What a sign-in gives Workbench is who someone is.** Their access to
models is the app's key, never a token from here (C2 D3).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from authlib.common.security import generate_token
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

#: How long a provider's discovery document and keys are believed.
_METADATA_TTL = 300.0
#: How long someone has to finish signing in on Eugene's page.
PENDING_SECONDS = 600.0
#: The ID token's clock tolerance, as every component here allows (300 s).
_LEEWAY = 300


class SignInUnavailable(Exception):
    """Eugene's provider cannot be used right now, in words a person reads."""


class SignInRefused(Exception):
    """Eugene answered and said no: a code or a refresh it will not honour."""


@dataclass
class Pending:
    """One sign-in started here and not yet finished."""

    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    binding: str
    started_at: float


@dataclass
class Identity:
    """Who signed in, from the ID token, and the refresh token to keep."""

    sub: str
    name: str
    username: str | None
    role: str
    refresh_token: str | None
    expires_in: float


def _secret(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


class Provider:
    """Eugene's sign-in, as one client of it sees it."""

    def __init__(
        self,
        *,
        issuer: str | None,
        client_id: str | None,
        secret_file: Path | None,
        http: httpx.AsyncClient,
    ) -> None:
        self.issuer = issuer.rstrip("/") if issuer else None
        self.client_id = client_id
        self._secret_file = secret_file
        self._http = http
        self._metadata: dict[str, Any] | None = None
        self._metadata_at = 0.0
        self._keys: KeySet | None = None
        self._keys_at = 0.0
        self._pending: dict[str, Pending] = {}
        self._lock = asyncio.Lock()

    # --- what is configured ----------------------------------------------

    def why_not(self) -> str | None:
        """None when sign-in can be offered, otherwise why not."""
        if not self.issuer or not self.client_id:
            return (
                "This Workbench was started without Eugene's sign-in settings. Install it from "
                "Eugene's app catalogue, which registers it to sign people in."
            )
        if _secret(self._secret_file) is None:
            return (
                "This Workbench's sign-in secret is missing. Uninstall it and install it again "
                "from Eugene's app catalogue to make a new one."
            )
        return None

    def _auth(self) -> httpx.BasicAuth:
        secret = _secret(self._secret_file)
        if not self.client_id or secret is None:
            raise SignInUnavailable(self.why_not() or "sign-in is not set up")
        return httpx.BasicAuth(self.client_id, secret)

    # --- the provider's own documents --------------------------------------

    async def metadata(self, *, fresh: bool = False) -> dict[str, Any]:
        reason = self.why_not()
        if reason is not None:
            raise SignInUnavailable(reason)
        assert self.issuer is not None
        if not fresh and self._metadata and time.time() - self._metadata_at < _METADATA_TTL:
            return self._metadata
        url = f"{self.issuer}/.well-known/openid-configuration"
        try:
            response = await self._http.get(url)
        except httpx.HTTPError as exc:
            raise SignInUnavailable(
                f"Workbench cannot reach Eugene's sign-in at {self.issuer}: "
                f"{str(exc) or type(exc).__name__}"
            ) from exc
        if response.status_code != 200:
            raise SignInUnavailable(
                f"Eugene's sign-in at {self.issuer} answered {response.status_code}."
            )
        found: dict[str, Any] = response.json()
        # OIDC Discovery 1.0 section 4.3: the issuer in the document must be
        # the one it was fetched under, or its tokens are someone else's.
        if str(found.get("issuer", "")).rstrip("/") != self.issuer:
            raise SignInUnavailable(
                f"Eugene's sign-in at {self.issuer} names a different issuer "
                f"({found.get('issuer')!r}), so Workbench will not trust its tokens."
            )
        self._metadata, self._metadata_at = found, time.time()
        return found

    async def _keyset(self, *, fresh: bool = False) -> KeySet:
        if not fresh and self._keys and time.time() - self._keys_at < _METADATA_TTL:
            return self._keys
        meta = await self.metadata()
        try:
            response = await self._http.get(meta["jwks_uri"])
            response.raise_for_status()
        except (httpx.HTTPError, KeyError) as exc:
            raise SignInUnavailable(f"Eugene's sign-in keys could not be read: {exc}") from exc
        self._keys, self._keys_at = KeySet.import_key_set(response.json()), time.time()
        return self._keys

    # --- starting and finishing ---------------------------------------------

    def _sweep(self, now: float) -> None:
        for state in [s for s, p in self._pending.items() if now - p.started_at > PENDING_SECONDS]:
            self._pending.pop(state, None)

    async def start(self, redirect_uri: str) -> tuple[str, Pending]:
        """The address to send a browser to, and what to remember about it."""
        meta = await self.metadata()
        now = time.time()
        self._sweep(now)
        pending = Pending(
            state=generate_token(32),
            nonce=generate_token(32),
            verifier=generate_token(64),
            redirect_uri=redirect_uri,
            binding=generate_token(32),
            started_at=now,
        )
        self._pending[pending.state] = pending
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "scope": "openid profile",
                "state": pending.state,
                "nonce": pending.nonce,
                "code_challenge": create_s256_code_challenge(pending.verifier),
                "code_challenge_method": "S256",
            }
        )
        return f"{meta['authorization_endpoint']}?{query}", pending

    def take(self, state: str | None) -> Pending | None:
        """The sign-in `state` names, once. A second use finds nothing."""
        if not state:
            return None
        self._sweep(time.time())
        return self._pending.pop(state, None)

    async def finish(self, pending: Pending, code: str, iss: str | None) -> Identity:
        meta = await self.metadata()
        # RFC 9207: an authorization response that names its issuer must name
        # ours, or the code came from somewhere else.
        if iss is not None and iss.rstrip("/") != self.issuer:
            raise SignInRefused("The sign-in came back from a different Eugene than this one.")
        token = await self._token(
            meta,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": pending.redirect_uri,
                "code_verifier": pending.verifier,
            },
        )
        id_token = token.get("id_token")
        if not isinstance(id_token, str):
            raise SignInRefused("Eugene's answer had no ID token.")
        claims = await self._claims(id_token, nonce=pending.nonce)
        return _identity(claims, token)

    async def refresh(self, refresh_token: str) -> Identity:
        """A new sign-in from a refresh token, or `SignInRefused` saying why.

        This is where a person turned off in Eugene is found out: the
        provider refuses their refresh (C2 D6).
        """
        meta = await self.metadata()
        token = await self._token(
            meta, {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        id_token = token.get("id_token")
        claims = await self._claims(id_token) if isinstance(id_token, str) else None
        if claims is None:
            raise SignInRefused("Eugene's refresh had no ID token.")
        identity = _identity(claims, token)
        if identity.refresh_token is None:
            identity.refresh_token = refresh_token
        return identity

    async def revoke(self, refresh_token: str) -> None:
        """RFC 7009. Best effort: a sign-out ends Workbench's session whatever
        Eugene says, and the token is forgotten here either way."""
        with contextlib.suppress(SignInUnavailable, httpx.HTTPError, KeyError):
            meta = await self.metadata()
            await self._http.post(
                meta["revocation_endpoint"],
                data={"token": refresh_token, "token_type_hint": "refresh_token"},
                auth=self._auth(),
            )

    async def _token(self, meta: dict[str, Any], form: dict[str, str]) -> dict[str, Any]:
        try:
            response = await self._http.post(meta["token_endpoint"], data=form, auth=self._auth())
        except httpx.HTTPError as exc:
            raise SignInUnavailable(
                f"Workbench cannot reach Eugene's sign-in: {str(exc) or type(exc).__name__}"
            ) from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 200 and isinstance(body, dict):
            return body
        if response.status_code in (400, 401):
            described = body.get("error_description") if isinstance(body, dict) else None
            raise SignInRefused(str(described or body.get("error") or "refused"))
        raise SignInUnavailable(f"Eugene's sign-in answered {response.status_code}.")

    async def _claims(self, id_token: str, *, nonce: str | None = None) -> dict[str, Any]:
        for fresh in (False, True):
            keys = await self._keyset(fresh=fresh)
            try:
                decoded = jwt.decode(id_token, keys, algorithms=["RS256"])
            except JoseError:
                # An unknown key id is a rotation this module has not seen
                # yet: read the keys again, once.
                if not fresh:
                    continue
                raise SignInRefused("Eugene's ID token did not verify.") from None
            # An access token (`at+jwt`) is signed by the same key and names
            # this client too; only an ID token says who someone is.
            if decoded.header.get("typ", "JWT") != "JWT":
                raise SignInRefused("Eugene sent a token that is not an ID token.")
            claims: dict[str, Any] = dict(decoded.claims)
            registry = jwt.JWTClaimsRegistry(
                leeway=_LEEWAY,
                iss={"essential": True, "value": self.issuer or ""},
                aud={"essential": True, "value": self.client_id or ""},
                exp={"essential": True},
                sub={"essential": True},
            )
            try:
                registry.validate(claims)
            except JoseError as exc:
                raise SignInRefused(f"Eugene's ID token is not for this Workbench: {exc}") from exc
            if nonce is not None and claims.get("nonce") != nonce:
                raise SignInRefused("Eugene's ID token answers a different sign-in.")
            return claims
        raise SignInRefused("Eugene's ID token did not verify.")  # pragma: no cover


def _identity(claims: dict[str, Any], token: dict[str, Any]) -> Identity:
    sub = str(claims["sub"])
    username = claims.get("preferred_username")
    name = claims.get("name") or username or sub
    role = "operator" if claims.get("eugene_role") == "operator" else "member"
    refresh = token.get("refresh_token")
    expires = token.get("expires_in")
    return Identity(
        sub=sub,
        name=str(name),
        username=str(username) if username else None,
        role=role,
        refresh_token=str(refresh) if refresh else None,
        expires_in=float(expires) if isinstance(expires, int | float) and expires > 0 else 600.0,
    )
