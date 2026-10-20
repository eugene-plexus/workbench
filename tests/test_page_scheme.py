"""LT2: behind the agent's door, a page's scheme is the loopback port a
connection arrived at, and sign-in follows the page (LT2-C5)."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any

import httpx

from eugene_plexus_workbench import api
from eugene_plexus_workbench.public_origin import SchemeByPort
from eugene_plexus_workbench.sessions import Sessions
from eugene_plexus_workbench.signin import Provider, https_twin, unverified_issuer


def _provider(issuer: str) -> Provider:
    return Provider(
        issuer=issuer, client_id="c", secret_file=None, http=httpx.AsyncClient(trust_env=False)
    )


def _jwt(claims: dict[str, Any]) -> str:
    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'EdDSA'})}.{part(claims)}.sig"


def test_the_https_twin_is_the_same_issuer_over_https() -> None:
    assert https_twin("http://192.168.1.10:8079/oidc") == "https://192.168.1.10:8079/oidc"
    assert https_twin("https://eugene.example.com/oidc") is None
    assert https_twin(None) is None


def test_the_issuer_a_token_claims_is_read_only_to_choose() -> None:
    assert unverified_issuer(_jwt({"iss": "https://h:8079/oidc/"})) == "https://h:8079/oidc"
    assert unverified_issuer("not a token") is None


async def test_a_request_on_the_tls_port_is_https_and_nothing_else_is() -> None:
    seen: list[str] = []

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen.append(scope["scheme"])

    middleware = SchemeByPort(app, tls_port=40001)
    for port in (40001, 40000):
        await middleware(
            {"type": "http", "scheme": "http", "server": ("127.0.0.1", port)}, None, None
        )
    await middleware(
        {"type": "websocket", "scheme": "ws", "server": ("127.0.0.1", 40001)}, None, None
    )
    assert seen == ["https", "http", "wss"]


def test_an_https_page_signs_in_through_the_https_issuer() -> None:
    plain, twin = _provider("http://h:8079/oidc"), _provider("https://h:8079/oidc")
    state = SimpleNamespace(provider=plain, provider_https=twin)

    def request(scheme: str) -> Any:
        return SimpleNamespace(app=SimpleNamespace(state=state), url=SimpleNamespace(scheme=scheme))

    assert api._provider(request("https")) is twin
    assert api._provider(request("http")) is plain
    state.provider_https = None
    assert api._provider(request("https")) is plain


def test_a_refresh_goes_to_the_issuer_that_issued_the_token() -> None:
    plain, twin = _provider("http://h:8079/oidc"), _provider("https://h:8079/oidc")
    sessions = Sessions(None, plain, twin)  # type: ignore[arg-type]
    assert sessions._provider_for(_jwt({"iss": "https://h:8079/oidc"})) is twin
    assert sessions._provider_for(_jwt({"iss": "http://h:8079/oidc"})) is plain
    assert sessions._provider_for(_jwt({"iss": "https://elsewhere/oidc"})) is plain
    assert sessions._provider_for("opaque") is plain
