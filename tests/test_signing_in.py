"""Signing in with Eugene, and what a session needs (W2, W3)."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

from eugene_plexus_workbench.signin import Provider, SignInRefused

from .conftest import CLIENT_ID, Browser, World, query_of


def test_the_owner_signs_in_with_eugene_and_is_the_owner(world: World) -> None:
    browser = world.browser()
    done = browser.sign_in("operator")
    assert done.status_code == 303
    assert done.headers["location"].startswith("/#signin=")
    me = browser.get("/api/me")
    assert me.status_code == 200, me.text
    assert me.json()["name"] == "Owner" and me.json()["owner"] is True
    assert me.json()["accountUrl"] is None


def test_member_has_a_link_to_eugenes_shared_sign_in_controls(world: World) -> None:
    browser = world.browser()
    browser.sign_in("p-ada")
    me = browser.get("/api/me").json()
    assert me["accountUrl"] == world.eugene.issuer.rstrip("/") + "/account"


def test_the_request_asks_for_the_code_flow_with_pkce_state_and_nonce(world: World) -> None:
    start = world.browser().http.get("/signin")
    asked = query_of(start.headers["location"])
    assert asked["response_type"] == "code" and asked["client_id"] == CLIENT_ID
    assert asked["code_challenge_method"] == "S256" and len(asked["code_challenge"]) == 43
    assert len(asked["state"]) >= 32 and len(asked["nonce"]) >= 32
    assert asked["redirect_uri"] == f"{world.workbench}/oidc/callback"
    assert "openid" in asked["scope"].split()


def test_a_cookie_alone_is_refused_and_so_is_the_secret_alone(world: World) -> None:
    """W3: a browser sends Workbench's cookie to every port on the host, so
    another service there holding it must get nothing."""
    browser = world.browser()
    browser.sign_in("p-ada")
    cookie_only = browser.http.get("/api/me")
    assert cookie_only.status_code == 401
    secret_only = httpx.get(
        f"{world.workbench}/api/me",
        headers={"X-Workbench-Secret": browser.secret or ""},
        trust_env=False,
    )
    assert secret_only.status_code == 401
    wrong = browser.http.get("/api/me", headers={"X-Workbench-Secret": "not-it"})
    assert wrong.status_code == 401
    assert browser.get("/api/me").status_code == 200


def test_a_changing_call_from_another_origin_is_refused(world: World) -> None:
    browser = world.browser()
    browser.sign_in("p-ada")
    elsewhere = browser.post("/api/chats", json={}, headers={"Origin": "http://evil.example"})
    assert elsewhere.status_code == 403
    no_origin = browser.http.post(
        "/api/chats", json={}, headers={"X-Workbench-Secret": browser.secret or ""}
    )
    assert no_origin.status_code == 403
    assert browser.post("/api/chats", json={}).status_code == 201


def test_a_sign_in_finished_in_another_browser_is_refused(world: World) -> None:
    """Login CSRF: a callback is bound to the browser that started it."""
    mine = world.browser()
    start = mine.http.get("/signin")
    at_eugene = httpx.get(start.headers["location"] + "&login_as=p-ada", trust_env=False)
    callback = at_eugene.headers["location"].replace(world.workbench, "")
    victim = world.browser()
    done = victim.http.get(callback)
    assert done.status_code == 303
    assert "#signin-error=" in done.headers["location"]
    assert "another browser" in unquote(done.headers["location"])
    assert victim.http.cookies.get("workbench_session") is None


def test_a_sign_in_state_is_good_once(world: World) -> None:
    """Refused by Workbench, before Eugene is asked: even someone who kept
    the browser binding cannot finish the same sign-in twice. (Eugene's
    single-use code would refuse it later; that is Eugene's guard, not this.)"""
    browser = world.browser()
    start = browser.http.get("/signin")
    binding = browser.http.cookies.get("workbench_signin")
    at_eugene = httpx.get(start.headers["location"] + "&login_as=p-ada", trust_env=False)
    callback = at_eugene.headers["location"].replace(world.workbench, "")
    assert "#signin=" in browser.http.get(callback).headers["location"]
    asked = world.eugene.token_calls
    browser.http.cookies.set("workbench_signin", binding or "", domain="127.0.0.1", path="/oidc")
    again = browser.http.get(callback)
    assert "has expired" in unquote(again.headers["location"])
    assert world.eugene.token_calls == asked, "Eugene was asked to trade the code again"


def test_a_person_turned_off_is_signed_out_at_the_next_refresh_and_nobody_else(
    world: World,
) -> None:
    world.eugene.expires_in = 1  # every call is due for a refresh
    ada, bo = world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    time.sleep(0.05)
    assert ada.get("/api/me").status_code == 200
    world.eugene.disabled.add("p-ada")
    refused = ada.get("/api/me")
    assert refused.status_code == 401
    assert "no longer accepts this sign-in" in refused.json()["detail"]["message"]
    assert bo.get("/api/me").status_code == 200
    # And the session is gone, not merely refused once.
    world.eugene.disabled.clear()
    assert ada.get("/api/me").status_code == 401


def test_eugene_unreachable_at_a_refresh_is_not_a_sign_out(world: World) -> None:
    world.eugene.expires_in = 1
    ada = world.browser()
    ada.sign_in("p-ada")
    world.eugene.up = False
    unreachable = ada.get("/api/me")
    assert unreachable.status_code == 503, unreachable.text
    assert "could not check your sign-in" in unreachable.json()["detail"]["message"]
    world.eugene.up = True
    assert ada.get("/api/me").status_code == 200


def test_sign_out_revokes_the_refresh_token_at_eugene(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    assert ada.post("/api/signout").status_code == 204
    assert world.eugene.revoked, "the refresh token was handed back to Eugene"
    assert ada.get("/api/me").status_code == 401


def test_status_says_when_sign_in_is_not_set_up(world: World, tmp_path) -> None:
    status = httpx.get(f"{world.workbench}/api/status", trust_env=False).json()
    assert status["signIn"] == {"available": True, "reason": None}
    provider = Provider(issuer=None, client_id=None, secret_file=None, http=httpx.AsyncClient())
    assert "without Eugene's sign-in settings" in (provider.why_not() or "")
    empty = tmp_path / "empty"
    empty.write_text("", encoding="utf-8")
    provider = Provider(
        issuer="http://x/oidc", client_id="c", secret_file=empty, http=httpx.AsyncClient()
    )
    assert "secret is missing" in (provider.why_not() or "")


# --------------------------------------------------------------------------- #
# the ID token's checks
# --------------------------------------------------------------------------- #


@pytest.fixture
def provider(world: World, tmp_path) -> Provider:
    secret = tmp_path / "s"
    secret.write_text("x", encoding="utf-8")
    return Provider(
        issuer=world.eugene.issuer,
        client_id=CLIENT_ID,
        secret_file=secret,
        http=httpx.AsyncClient(trust_env=False),
    )


async def test_an_id_token_for_another_app_is_refused(world: World, provider: Provider) -> None:
    with pytest.raises(SignInRefused, match="not for this Workbench"):
        await provider._claims(world.eugene.id_token("p-ada", aud="someone-else"))


async def test_an_id_token_from_another_issuer_is_refused(world: World, provider: Provider) -> None:
    with pytest.raises(SignInRefused, match="not for this Workbench"):
        await provider._claims(world.eugene.id_token("p-ada", issuer="http://other/oidc"))


async def test_an_id_token_for_another_sign_in_is_refused(world: World, provider: Provider) -> None:
    with pytest.raises(SignInRefused, match="different sign-in"):
        await provider._claims(world.eugene.id_token("p-ada", nonce="a"), nonce="b")


async def test_an_access_token_is_not_an_id_token(world: World, provider: Provider) -> None:
    """C2 D12: every sign-in token is signed by one key; only the typ tells
    an ID token from an access token that also names this client."""
    with pytest.raises(SignInRefused, match="not an ID token"):
        await provider._claims(world.eugene.id_token("p-ada", typ="at+jwt"))


async def test_a_token_signed_by_another_key_is_refused(world: World, provider: Provider) -> None:
    from joserfc import jwt as jose
    from joserfc.jwk import RSAKey

    other = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    forged = jose.encode(
        {"alg": "RS256", "typ": "JWT", "kid": "k1"},
        {
            "iss": world.eugene.issuer,
            "aud": CLIENT_ID,
            "sub": "operator",
            "exp": int(time.time()) + 60,
        },
        other,
    )
    with pytest.raises(SignInRefused, match="did not verify"):
        await provider._claims(forged)


def test_browser_helper_keeps_cookies_per_host(world: World) -> None:
    """The fixture's own premise: a cookie set at sign-in is sent back."""
    browser = Browser(world.workbench, world.eugene)
    browser.sign_in("operator")
    assert browser.http.cookies.get("workbench_session")


# --------------------------------------------------------------------------- #
# row 70c: a person's groups and the policies they set, from the claims
# --------------------------------------------------------------------------- #

NIGHT = {"id": "g-night", "name": "Night shift"}
DESK = {"id": "g-desk", "name": "Desk"}


def _claims(*groups: dict[str, str], **policies: Any) -> dict[str, Any]:
    set_by = {name: [g["id"] for g in groups] for name in policies}
    return {
        "eugene_groups": list(groups),
        "eugene_policies": {**policies, **({"setBy": set_by} if set_by else {})},
    }


def test_groups_and_policies_come_from_the_claims(world: World) -> None:
    world.eugene.claims["p-ada"] = _claims(NIGHT, queueBand="background", summaryContextPercent=70)
    world.eugene.expires_in = 1  # every call is due for a refresh
    ada = world.browser()
    ada.sign_in("p-ada")
    page = ada.get("/api/groups")
    assert page.status_code == 200, page.text
    shown = page.json()
    assert shown["known"] is True
    assert shown["groups"] == [NIGHT]
    assert shown["policies"] == [
        {"policy": "queueBand", "value": "background", "setBy": [NIGHT]},
        {"policy": "summaryContextPercent", "value": 70, "setBy": [NIGHT]},
    ]
    # Moved to another group at Eugene: the next refresh says so.
    world.eugene.claims["p-ada"] = _claims(DESK, summaryContextPercent=85)
    time.sleep(0.05)
    moved = ada.get("/api/groups").json()
    assert moved["groups"] == [DESK]
    assert moved["policies"] == [{"policy": "summaryContextPercent", "value": 85, "setBy": [DESK]}]
    # Another person's groups are theirs alone.
    bo = world.browser()
    bo.sign_in("p-bo")
    assert bo.get("/api/groups").json()["groups"] == []


def test_a_cut_id_token_reads_every_group_from_userinfo(world: World) -> None:
    every = [{"id": f"g{n}", "name": f"Team {n}"} for n in range(3)]
    world.eugene.userinfo["p-ada"] = _claims(*every, queueBand="interactive")
    world.eugene.claims["p-ada"] = {
        **_claims(every[0], queueBand="interactive"),
        "eugene_groups_truncated": True,
    }
    ada = world.browser()
    ada.sign_in("p-ada")
    shown = ada.get("/api/groups").json()
    assert shown["groups"] == every
    assert shown["policies"][0]["setBy"] == every
    assert world.eugene.userinfo_calls == 1


def test_a_cut_id_token_with_no_userinfo_is_not_a_partial_sign_in(world: World) -> None:
    """A member must not lose a group's server because a list was cut: if every group cannot
    be read, the sign-in stops and says why."""
    world.eugene.claims["p-ada"] = {**_claims(NIGHT), "eugene_groups_truncated": True}
    world.eugene.userinfo["p-ada"] = {"eugene_groups": "not a list"}
    done = world.browser().sign_in("p-ada")
    assert "#signin-error=" in done.headers["location"]
    assert "groups" in unquote(done.headers["location"])


def test_an_older_eugene_sends_no_group_claims(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    shown = ada.get("/api/groups").json()
    assert shown == {
        "known": False,
        "groups": [],
        "policies": [],
        "servers": [],
        "serversAllowed": shown["serversAllowed"],
        "why": shown["why"],
    }


@pytest.mark.parametrize(
    "claims",
    [
        {"eugene_groups": [{"id": "", "name": "x"}]},
        {"eugene_groups": [{"id": "g1"}]},
        {"eugene_groups": [{"id": "g1", "name": "x" * 81}]},
        {"eugene_groups": [{"id": "g1", "name": "Ok"}] * 65},
        {"eugene_groups": [], "eugene_policies": {"queueBand": "urgent"}},
        {"eugene_groups": [], "eugene_policies": {"summaryContextPercent": 99}},
        {"eugene_groups": [], "eugene_policies": {"summaryContextPercent": True}},
        # Review should-fix 3: one valid group beside a policy out of shape.
        {
            "eugene_groups": [{"id": "g1", "name": "Night shift"}],
            "eugene_policies": {"summaryContextPercent": 99},
        },
    ],
)
def test_a_group_claim_out_of_shape_stops_the_sign_in_and_says_why(
    world: World, claims: dict[str, Any]
) -> None:
    """A claim Eugene sent out of the contract's shape is not half-believed, nor dropped: a
    member must not lose a group's server they cannot turn off. The sign-in stops and says
    why. An Eugene that sends no group claims at all is an older one, and signs in."""
    world.eugene.claims["p-ada"] = claims
    done = world.browser().sign_in("p-ada")
    assert "#signin-error=" in done.headers["location"]
    assert "groups" in unquote(done.headers["location"])


def test_a_group_claim_out_of_shape_at_a_refresh_is_not_a_sign_out(world: World) -> None:
    world.eugene.claims["p-ada"] = {"eugene_groups": [NIGHT], "eugene_policies": {}}
    world.eugene.expires_in = 1  # every call is due for a refresh
    ada = world.browser()
    ada.sign_in("p-ada")
    world.eugene.claims["p-ada"] = {"eugene_groups": [NIGHT], "eugene_policies": {"x": 1}}
    time.sleep(0.05)
    refused = ada.get("/api/groups")
    assert refused.status_code == 503, refused.text
    assert "groups" in refused.json()["detail"]["message"]
    world.eugene.claims["p-ada"] = {"eugene_groups": [NIGHT], "eugene_policies": {}}
    assert ada.get("/api/groups").json()["groups"] == [NIGHT]


def test_userinfo_that_says_it_is_cut_is_not_taken_for_every_group(world: World) -> None:
    """Review should-fix 3: userinfo is never cut; one that says it is cannot be the whole list."""
    world.eugene.claims["p-ada"] = {**_claims(NIGHT), "eugene_groups_truncated": True}
    world.eugene.userinfo["p-ada"] = {**_claims(NIGHT, DESK), "eugene_groups_truncated": True}
    done = world.browser().sign_in("p-ada")
    assert "#signin-error=" in done.headers["location"]
    assert "groups" in unquote(done.headers["location"])
