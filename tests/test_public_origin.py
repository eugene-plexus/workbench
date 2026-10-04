"""HTTPS behind the configured entry point must preserve origin isolation."""

from __future__ import annotations

from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from eugene_plexus_workbench.app import create_app
from eugene_plexus_workbench.settings import Settings
from eugene_plexus_workbench.signin import Identity, Provider, SignInUnavailable

ORIGIN = "https://workbench.home.arpa:8443"
ISSUER = "https://eugene.home.arpa:8443/oidc"


def test_https_cookies_callbacks_and_sibling_origin_csrf(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, public_origin=ORIGIN))
    with TestClient(app, base_url=ORIGIN) as client:
        provider = app.state.provider
        provider.metadata = AsyncMock(
            return_value={"authorization_endpoint": ISSUER + "/authorize"}
        )
        provider.finish = AsyncMock(return_value=Identity("ada", "Ada", "ada", "member", None, 600))
        start = client.get(
            "/signin",
            follow_redirects=False,
            headers={
                "X-Forwarded-Host": "attacker.example",
                "X-Forwarded-Proto": "http",
            },
        )
        assert start.status_code == 302
        query = parse_qs(urlsplit(start.headers["location"]).query)
        assert query["redirect_uri"] == [ORIGIN + "/oidc/callback"]
        cookie = start.headers["set-cookie"]
        assert cookie.startswith("__Host-workbench_signin=")
        assert "Secure" in cookie and "HttpOnly" in cookie and "Path=/;" in cookie
        assert "Domain=" not in cookie
        done = client.get(
            "/oidc/callback",
            params={"state": query["state"][0], "code": "code"},
            follow_redirects=False,
        )
        assert done.status_code == 303
        secret = done.headers["location"].split("#signin=")[1]
        cookies = done.headers.get_list("set-cookie")
        assert all("Secure" in c and "HttpOnly" in c and "Domain=" not in c for c in cookies)
        assert any(c.startswith("__Host-workbench_session=") for c in cookies)
        assert client.get("/api/me").status_code == 401  # Cookie alone remains insufficient.
        headers = {"X-Workbench-Secret": secret, "Origin": ORIGIN}
        assert client.get("/api/me", headers=headers).status_code == 200
        assert (
            client.post(
                "/api/chats",
                json={},
                headers={**headers, "Origin": "https://eugene.home.arpa:8443"},
            ).status_code
            == 403
        )
        assert (
            client.post("/api/chats", json={}, headers={"X-Workbench-Secret": secret}).status_code
            == 403
        )
        assert client.post("/api/chats", json={}, headers=headers).status_code == 201
        assert client.get("/signin", headers={"Host": "attacker.example"}).status_code == 421
        signed_out = client.post("/api/signout", headers=headers)
        assert signed_out.status_code == 204
        assert "__Host-workbench_session=" in signed_out.headers["set-cookie"]
        assert "Secure" in signed_out.headers["set-cookie"]


async def test_only_the_canonical_issuer_can_use_the_local_backchannel(tmp_path):
    http = httpx.AsyncClient(trust_env=False)
    provider = Provider(
        issuer=ISSUER,
        client_id="client",
        secret_file=None,
        http=http,
        backchannel="http://127.0.0.1:8079/oidc",
    )
    assert provider.transport_url(ISSUER + "/token") == "http://127.0.0.1:8079/oidc/token"
    assert (
        provider.transport_url(ISSUER + "/node-helpers/folders")
        == "http://127.0.0.1:8079/oidc/node-helpers/folders"
    )
    for url in (
        "https://attacker.example/token",
        ISSUER + "-other/token",
        ISSUER + "/../v1/config",
        ISSUER + "/%2e%2e/v1/config",
    ):
        with pytest.raises(SignInUnavailable):
            provider.transport_url(url)
    await http.aclose()


@pytest.mark.parametrize(
    "address",
    [
        "http://remote:8079/oidc",
        "http://127.0.0.1:8079/v1",
        "http://user@127.0.0.1:8079/oidc",
        "https://127.0.0.1:8079/oidc",
        "http://127.0.0.1:8079/oidc?x=y",
    ],
)
def test_backchannel_is_only_an_explicit_local_agent(address):
    with pytest.raises(ValidationError):
        Settings(oidc_backchannel=address)


async def test_discovery_still_verifies_public_issuer_through_local_transport(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("client-secret")
    seen = []

    def respond(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"issuer": "http://127.0.0.1:8079/oidc"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = Provider(
            issuer=ISSUER,
            client_id="client",
            secret_file=secret,
            http=http,
            backchannel="http://127.0.0.1:8079/oidc",
        )
        with pytest.raises(SignInUnavailable, match="different issuer"):
            await provider.metadata()
    assert seen == ["http://127.0.0.1:8079/oidc/.well-known/openid-configuration"]
