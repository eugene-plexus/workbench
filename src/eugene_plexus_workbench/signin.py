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
import logging
import re
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

log = logging.getLogger(__name__)

#: Row 70c: what `eugene_groups` and `eugene_policies` may hold (the control contract's
#: `OidcGroup` and `PoliciesInEffect`): at most 64 groups, an id of 1-64 characters a path can
#: carry, a name of 1-80; a queue band of three; a percent of 50-95.
MAX_GROUPS = 64
#: The install's own list (`/oidc/groups`) has no fixed length (row 70c review): this is only
#: a bound on what Workbench will hold in memory from one answer.
MAX_CATALOG = 10_000
GROUP_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
QUEUE_BANDS = ("interactive", "background", "sheddable")
POLICIES = ("queueBand", "summaryContextPercent")


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
    access_token: str | None = None
    #: The person's email, when Eugene has one and sent it (the `email` scope).
    email: str | None = None
    #: Row 70c: the permission groups they are in and the policies those set, from
    #: `eugene_groups` and `eugene_policies`; None when Eugene said nothing of groups.
    groups: list[dict[str, str]] | None = None
    policies: dict[str, Any] | None = None


def _secret(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def https_twin(issuer: str | None) -> str | None:
    """The same issuer over https (LT2-C5): the console takes both on one port,
    so a page opened over https signs in through `https://<host>:<port>/oidc`
    and a password never goes plain from a page that had TLS. None for an
    issuer that is https already, or none."""
    if not issuer or not issuer.startswith("http://"):
        return None
    return "https://" + issuer.removeprefix("http://")


def unverified_issuer(token: str) -> str | None:
    """The `iss` a JWT claims, unverified: only to choose which provider then
    verifies it, strictly, against its own issuer."""
    import base64
    import json

    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return None
    iss = claims.get("iss") if isinstance(claims, dict) else None
    return str(iss).rstrip("/") if isinstance(iss, str) else None


class Provider:
    """Eugene's sign-in, as one client of it sees it."""

    def __init__(
        self,
        *,
        issuer: str | None,
        client_id: str | None,
        secret_file: Path | None,
        http: httpx.AsyncClient,
        backchannel: str | None = None,
    ) -> None:
        self.issuer = issuer.rstrip("/") if issuer else None
        self.client_id = client_id
        self._secret_file = secret_file
        self._http = http
        self._backchannel = backchannel
        self._metadata: dict[str, Any] | None = None
        self._metadata_at = 0.0
        self._keys: KeySet | None = None
        self._keys_at = 0.0
        self._pending: dict[str, Pending] = {}
        self._lock = asyncio.Lock()

    def transport_url(self, url: str) -> str:
        """Keep the public issuer identity while using a fixed local transport.

        Never translate a different authority, a sibling path or a traversal.
        Browser authorization URLs do not pass through this method.
        """
        if not self._backchannel:
            return url
        prefix = (self.issuer or "") + "/"
        if not url.startswith(prefix):
            raise SignInUnavailable("Eugene's sign-in endpoint is outside its configured issuer.")
        suffix = url[len(prefix) :]
        if "\\" in suffix or "%" in suffix or any(p in (".", "..") for p in suffix.split("/")):
            raise SignInUnavailable("Eugene's sign-in endpoint has an invalid path.")
        return self._backchannel + "/" + suffix

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
            response = await self._http.get(self.transport_url(url))
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
            response = await self._http.get(self.transport_url(meta["jwks_uri"]))
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
                # `email` (row 7): a removed person's email stays with their hidden data, so a
                # later row can give it back to a new account that proves the same address.
                "scope": "openid profile email",
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
        return await self._with_groups(_identity(claims, token), claims, meta)

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
        identity = await self._with_groups(_identity(claims, token), claims, meta)
        if identity.refresh_token is None:
            identity.refresh_token = refresh_token
        return identity

    async def _with_groups(
        self, identity: Identity, claims: dict[str, Any], meta: dict[str, Any]
    ) -> Identity:
        """Row 70c: the person's groups and policies. An ID token cut at Eugene's size cap
        (`eugene_groups_truncated`) lists only some groups, so every group is read from
        `/oidc/userinfo` with the access token just issued. A member must not lose a group's
        memory server, which they cannot turn off, to a list cut short: if every group cannot
        be read, the sign-in stops with the cause rather than go on with some of them."""
        if claims.get("eugene_groups_truncated") is not True:
            identity.groups, identity.policies = group_claims(claims, where="sign-in")
            return identity
        reason = "Eugene's sign-in listed only some of your groups, and the rest"
        endpoint = meta.get("userinfo_endpoint")
        if not isinstance(endpoint, str) or not identity.access_token:
            raise SignInUnavailable(f"{reason} could not be asked for.")
        try:
            response = await self._http.get(
                self.transport_url(endpoint),
                headers={"Authorization": f"Bearer {identity.access_token}"},
            )
        except httpx.HTTPError as exc:
            raise SignInUnavailable(
                f"{reason} could not be read: {str(exc) or type(exc).__name__}"
            ) from exc
        try:
            told = response.json() if response.status_code == 200 else None
        except ValueError:
            told = None
        if not isinstance(told, dict) or told.get("sub") != identity.sub:
            raise SignInUnavailable(
                f"{reason} could not be read (Eugene answered {response.status_code})."
            )
        if told.get("eugene_groups_truncated", False) is not False:
            # Userinfo is never cut: one that says it is cannot be the whole list.
            raise SignInUnavailable(f"{reason} came back cut short as well.")
        groups, policies = group_claims(told, where="userinfo")
        if groups is None:
            raise SignInUnavailable(f"{reason} came back without any groups.")
        identity.groups, identity.policies = groups, policies
        return identity

    async def groups(self) -> list[dict[str, str]]:
        """Row 70c: the permission groups that give this Workbench, from Eugene's
        `/oidc/groups` with this client's secret, as `{id, name}`; `SignInUnavailable` with
        the cause when Eugene cannot say."""
        if not self.issuer:
            raise SignInUnavailable(self.why_not() or "sign-in is not set up")
        try:
            response = await self._http.get(
                self.transport_url(f"{self.issuer}/groups"), auth=self._auth()
            )
        except httpx.HTTPError as exc:
            raise SignInUnavailable(
                f"Eugene did not answer ({str(exc) or type(exc).__name__})"
            ) from exc
        if response.status_code == 404:
            raise SignInUnavailable(
                "this Eugene cannot list its permission groups (it answered 404). Update Eugene"
            )
        if response.status_code != 200:
            raise SignInUnavailable(f"Eugene answered {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            body = None
        found = _groups_of(body.get("groups") if isinstance(body, dict) else None, MAX_CATALOG)
        if found is None:
            raise SignInUnavailable("Eugene's list of groups could not be read")
        return found

    async def revoke(self, refresh_token: str) -> None:
        """RFC 7009. Best effort: a sign-out ends Workbench's session whatever
        Eugene says, and the token is forgotten here either way."""
        with contextlib.suppress(SignInUnavailable, httpx.HTTPError, KeyError):
            meta = await self.metadata()
            await self._http.post(
                self.transport_url(meta["revocation_endpoint"]),
                data={"token": refresh_token, "token_type_hint": "refresh_token"},
                auth=self._auth(),
            )

    async def subjects(self, subs: list[str]) -> tuple[list[str], list[str]]:
        """Row 7: which of these people Eugene still has, as `(known, unknown)`.

        `POST <issuer>/subjects`, with this client's secret as `/revoke` sends it. Workbench
        hides and later deletes the data of an `unknown` person, so nothing here is guessed:
        an answer that leaves out anyone asked about, or that cannot be read, raises
        `SignInUnavailable` with the cause, as does any answer but 200."""
        if not self.issuer:
            raise SignInUnavailable(self.why_not() or "sign-in is not set up")
        try:
            response = await self._http.post(
                self.transport_url(f"{self.issuer}/subjects"),
                json={"subjects": subs},
                auth=self._auth(),
            )
        except httpx.HTTPError as exc:
            raise SignInUnavailable(
                f"Eugene did not answer ({str(exc) or type(exc).__name__})"
            ) from exc
        if response.status_code == 404:
            raise SignInUnavailable(
                "this Eugene cannot say who was removed (it answered 404). Update Eugene"
            )
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code != 200:
            said = body.get("error_description") if isinstance(body, dict) else None
            raise SignInUnavailable(
                f"Eugene answered {response.status_code}" + (f": {said}" if said else "")
            )
        known = body.get("known") if isinstance(body, dict) else None
        unknown = body.get("unknown") if isinstance(body, dict) else None
        if not (
            isinstance(known, list)
            and isinstance(unknown, list)
            and all(isinstance(s, str) for s in [*known, *unknown])
        ):
            raise SignInUnavailable("Eugene's answer could not be read")
        asked = set(subs)
        found_known, found_unknown = set(known) & asked, set(unknown) & asked
        if found_known & found_unknown or (found_known | found_unknown) != asked:
            raise SignInUnavailable("Eugene's answer did not name each person once")
        return [s for s in subs if s in found_known], [s for s in subs if s in found_unknown]

    async def _token(self, meta: dict[str, Any], form: dict[str, str]) -> dict[str, Any]:
        try:
            response = await self._http.post(
                self.transport_url(meta["token_endpoint"]), data=form, auth=self._auth()
            )
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


def _groups_of(value: Any, limit: int = MAX_GROUPS) -> list[dict[str, str]] | None:
    """`[{id, name}]` as the contract gives it, or None for anything else. `limit` is one
    person's bound (64) for their claims; the install's own list takes `MAX_CATALOG`."""
    if not isinstance(value, list) or len(value) > limit:
        return None
    groups: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "name"}:
            return None
        group_id, name = item["id"], item["name"]
        if not (isinstance(group_id, str) and GROUP_ID.fullmatch(group_id)):
            return None
        if not (isinstance(name, str) and 1 <= len(name) <= 80):
            return None
        groups.append({"id": group_id, "name": name})
    if len({g["id"] for g in groups}) != len(groups):
        return None
    return groups


def _policies_of(value: Any) -> dict[str, Any] | None:
    """`PoliciesInEffect` as the contract gives it, or None for anything else."""
    if not isinstance(value, dict) or set(value) - {*POLICIES, "setBy"}:
        return None
    band = value.get("queueBand")
    if band is not None and band not in QUEUE_BANDS:
        return None
    percent = value.get("summaryContextPercent")
    if percent is not None and (type(percent) is not int or not 50 <= percent <= 95):
        return None
    set_by = value.get("setBy", {})
    if not isinstance(set_by, dict) or set(set_by) - set(POLICIES):
        return None
    for ids in set_by.values():
        if not isinstance(ids, list) or not all(
            isinstance(i, str) and GROUP_ID.fullmatch(i) for i in ids
        ):
            return None
    return dict(value)


def group_claims(
    claims: dict[str, Any], *, where: str = "sign-in"
) -> tuple[list[dict[str, str]] | None, dict[str, Any] | None]:
    """Row 70c: `(groups, policies)` from a sign-in's claims, or `(None, None)` when Eugene said
    nothing of groups (an Eugene from before row 70).

    Claims Eugene sent out of the contract's shape are neither half-believed nor dropped: a
    member would lose a group's memory server they cannot turn off. `SignInUnavailable` says
    so, which stops a sign-in with the cause and keeps a signed-in session (a refresh answers
    503) until Eugene sends them in shape (row 70c review)."""
    if "eugene_groups" not in claims and "eugene_policies" not in claims:
        return None, None
    groups = _groups_of(claims.get("eugene_groups"))
    policies = _policies_of(claims.get("eugene_policies", {}))
    if groups is None or policies is None:
        log.warning("Eugene's group claims (%s) are not in the shape Workbench reads", where)
        part = "your groups" if groups is None else "what your groups set"
        raise SignInUnavailable(
            f"Eugene's {where} sent {part} in a shape this Workbench cannot read, so it cannot "
            "tell which of your groups' settings apply. Update Eugene, or ask its owner."
        )
    return groups, policies


def _identity(claims: dict[str, Any], token: dict[str, Any]) -> Identity:
    sub = str(claims["sub"])
    username = claims.get("preferred_username")
    name = claims.get("name") or username or sub
    role = "operator" if claims.get("eugene_role") == "operator" else "member"
    refresh = token.get("refresh_token")
    expires = token.get("expires_in")
    email = claims.get("email")
    return Identity(
        email=email if isinstance(email, str) and email else None,
        sub=sub,
        name=str(name),
        username=str(username) if username else None,
        role=role,
        refresh_token=str(refresh) if refresh else None,
        expires_in=float(expires) if isinstance(expires, int | float) and expires > 0 else 600.0,
        access_token=token.get("access_token")
        if isinstance(token.get("access_token"), str)
        else None,
    )
