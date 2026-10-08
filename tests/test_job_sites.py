"""Job sites in Workbench (`specs/docs/design/remote-nodes.md` §3.3, J9, J13, J18).

A person's own machines, managed with their own sign-in; every person told the
install's mode; and the owner's reading of other people's chats stopping at the
first job-site result in production mode (J13a), with results made in
production staying hidden after a switch to dev.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import fake_sites
from .conftest import ADMIN_TOKEN, CLIENT_ID, CLIENT_SECRET, FakeEugene, World
from .test_folders import finish, offer

DESK = fake_sites.DESK
TOOL = f"site:{DESK}:notes-tool"
CHANGED = "2026-10-05T12:00:00+00:00"


@pytest.fixture
def site_world(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> tuple[World, dict[str, Any]]:
    original = FakeEugene.app
    state: dict[str, Any] = {
        "mode": "production",
        "changedAt": None,
        "calls": [],
        "sites": [],
        "managed": [],
        "links_removed": [],
        "content": "SITE-SECRET",
        "folders": [
            {
                "id": "site-notes",
                "site": DESK,
                "label": "desk",
                "name": "Notes",
                "writable": False,
                "people": {"p-ada": False},
            }
        ],
    }

    def app(fake: FakeEugene) -> FastAPI:
        server = original(fake)

        def client_ok(request: Request) -> bool:
            expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            return request.headers.get("authorization") == "Basic " + expected

        @server.post("/oidc/install-mode")
        async def mode(request: Request) -> Any:
            if not client_ok(request):
                return JSONResponse({}, status_code=401)
            return {"mode": state["mode"], "changedAt": state["changedAt"]}

        fake_sites.install(server, fake, state)

        @server.post("/oidc/sites/link/remove")
        async def link_remove(request: Request) -> Any:
            assert client_ok(request)
            body = await request.json()
            state["links_removed"].append(body)
            refusal = state.get("link_refusal")
            if refusal:
                return JSONResponse({"detail": {"detail": refusal[1]}}, status_code=refusal[0])
            return JSONResponse(None, status_code=204)

        @server.post("/oidc/job-sites/{site}/{what}/{ident}/{action}")
        async def relayed(site: str, what: str, ident: str, action: str, request: Request) -> Any:
            body = await request.json()
            if fake.refresh_tokens.get(body["refreshToken"]) != "p-ada":
                return JSONResponse({"detail": {"detail": "No such job site."}}, status_code=404)
            state["managed"].append((what, ident, action, body))
            if state.get("held"):
                return JSONResponse({"held": True, "message": state["held"]}, status_code=202)
            return {"server": {"id": ident}, "people": body.get("people", [])}

        @server.post("/oidc/job-sites/{site}/{action}")
        async def site_action(site: str, action: str, request: Request) -> Any:
            body = await request.json()
            if action == "leave":
                return JSONResponse(None, status_code=204)
            if fake.refresh_tokens.get(body["refreshToken"]) != "p-ada":
                return JSONResponse({"detail": {"detail": "No such job site."}}, status_code=404)
            state["managed"].append((action, body))
            if action == "audit":
                return {"entries": [{"at": CHANGED, "subject": "p-bo", "decision": "refused"}]}
            return {"site": site, "ownerInDevMode": body.get("ownerInDevMode")}

        @server.post("/oidc/job-sites")
        async def mine(request: Request) -> Any:
            body = await request.json()
            assert client_ok(request) and fake.refresh_tokens.get(body["refreshToken"])
            return {
                "sites": state["sites"],
                "installMode": {"mode": state["mode"]},
                "canInvite": True,
            }

        @server.post("/oidc/job-sites/invite")
        async def invite(request: Request) -> Any:
            body = await request.json()
            if fake.refresh_tokens.get(body["refreshToken"]) != "p-ada":
                return JSONResponse(
                    {"detail": {"detail": "Job sites belong to people."}}, status_code=403
                )
            return {
                "token": "JOIN-TOKEN",
                "expiresAt": "2026-10-05T12:15:00+00:00",
                "label": body.get("label"),
                "joinUrl": "https://nodes.example.test:8443",
                "rootKey": "ROOTKEY",
                "owner": "ada o'neil",
            }

        return server

    monkeypatch.setattr(FakeEugene, "app", app)
    world: World = request.getfixturevalue("world")
    return world, state


def _owner_reads(world: World) -> None:
    answer = httpx.patch(
        f"{world.workbench}/v1/config",
        json={"ownerReadsChats": True},
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        trust_env=False,
    )
    assert answer.status_code == 200


def _chat_with_a_site_read(world: World) -> tuple[Any, Any, str]:
    owner, ada = world.browser(), world.browser()
    owner.sign_in("operator")
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Before the files"})
    ada.wait_answer(chat)
    chosen = ada.patch(
        f"/api/chats/{chat}", json={"settings": {"folderGrants": ["node:site-notes"]}}
    )
    assert chosen.status_code == 200, chosen.text
    world.gateway.mode = "tools"
    reading = offer(world, ada, chat, "read_text", folder="Notes", path="note.txt")
    call = finish(ada, chat, reading)
    assert json.loads(call["result"])["text"] == "SITE-SECRET"
    world.gateway.mode = "text"
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Summarise it"})
    ada.wait_answer(chat)
    _owner_reads(world)
    return owner, ada, chat


def test_production_hides_the_chat_from_the_first_site_result_on(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    world, _ = site_world
    owner, ada, chat = _chat_with_a_site_read(world)
    mine = ada.get(f"/api/chats/{chat}").text
    assert "SITE-SECRET" in mine and '"redacted"' not in mine
    read = owner.get(f"/api/chats/{chat}")
    assert read.status_code == 200
    assert "SITE-SECRET" not in read.text and "Summarise it" not in read.text
    messages = read.json()["messages"]
    assert messages[0]["content"] == "Before the files" and "redacted" not in messages[0]
    hidden = [m for m in messages if m.get("redacted")]
    assert hidden and all(m["redacted"] == {"site": "desk"} for m in hidden)
    first = messages.index(hidden[0])
    assert all(m.get("redacted") for m in messages[first:])


def test_dev_mode_shows_dev_results_and_production_results_stay_hidden(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    world, state = site_world
    owner, _, chat = _chat_with_a_site_read(world)  # made in production
    state.update(mode="dev", changedAt=CHANGED)
    _wait_mode(owner, "dev")
    assert "SITE-SECRET" not in owner.get(f"/api/chats/{chat}").text, "J18: not retroactive"
    state["mode"] = "dev"
    dev_owner, _, dev_chat = _chat_with_a_site_read(world)  # made in dev
    assert "SITE-SECRET" in dev_owner.get(f"/api/chats/{dev_chat}").text
    state.update(mode="production", changedAt="2026-10-05T13:00:00+00:00")
    _wait_mode(owner, "production")
    assert "SITE-SECRET" not in dev_owner.get(f"/api/chats/{dev_chat}").text


def _wait_mode(browser: Any, mode: str) -> None:
    import time

    deadline = time.perf_counter() + 15
    while time.perf_counter() < deadline:
        if browser.get("/api/me").json()["installMode"] == mode:
            return
        time.sleep(0.2)
    pytest.fail(f"Workbench never saw {mode} mode")


def test_every_person_sees_the_mode_and_is_told_once_when_it_changes(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    world, state = site_world
    ada = world.browser()
    ada.sign_in("p-ada")
    me = ada.get("/api/me").json()
    assert me["installMode"] == "production" and me["installModeNotice"] is False
    state.update(mode="dev", changedAt=CHANGED)
    _wait_mode(ada, "dev")
    assert ada.get("/api/me").json()["installModeNotice"] is True
    assert ada.post("/api/me/mode-seen").status_code == 204
    assert ada.get("/api/me").json()["installModeNotice"] is False


def test_a_person_adds_their_own_machine_and_no_password_is_in_the_command(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    world, _ = site_world
    ada, owner = world.browser(), world.browser()
    ada.sign_in("p-ada")
    owner.sign_in("operator")
    assert ada.get("/api/job-sites").json()["canInvite"] is True
    invited = ada.post("/api/job-sites/invite", json={"label": "laptop"})
    assert invited.status_code == 200, invited.text
    commands = invited.json()["commands"]
    assert "-JobSite -Owner 'ada o''neil' -RootKey ROOTKEY -NodeName laptop" in commands["windows"]
    assert (
        "--job-site --owner 'ada o'\\''neil' --root-key ROOTKEY --name laptop" in commands["posix"]
    )
    assert "https://nodes.example.test:8443 -Token JOIN-TOKEN" in commands["windows"]
    assert "JOIN-TOKEN" in commands["windows"] and "password" not in json.dumps(commands).lower()
    refused = owner.post("/api/job-sites/invite", json={})
    assert refused.status_code == 409 and "belong to people" in refused.text
    assert ada.post("/api/job-sites/invite", json={"label": "bad name"}).status_code == 422
    assert ada.post(f"/api/job-sites/{DESK}/leave").status_code == 204


def test_the_owner_manages_a_sites_servers_settings_and_reads_its_audit(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    """Each change is relayed to the site, which keeps the list (J6b)."""
    world, state = site_world
    ada, bo = world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    access = ada.post(
        f"/api/job-sites/{DESK}/servers/notes-tool/access",
        json={"people": [{"name": "bo", "tools": [{"name": "search"}]}]},
    )
    assert access.status_code == 200, access.text
    assert state["managed"][-1][:3] == ("servers", "notes-tool", "access")
    assert state["managed"][-1][3]["people"] == [
        {"name": "bo", "tools": [{"name": "search", "standing": False}]}
    ]
    assert (
        ada.post(f"/api/job-sites/{DESK}/servers/notes-tool/enabled", json={"enabled": True})
    ).status_code == 200
    assert (
        ada.post(
            f"/api/job-sites/{DESK}/servers/files/enabled", json={"enabled": False}
        ).status_code
        == 404
    )
    opted = ada.post(f"/api/job-sites/{DESK}/settings", json={"ownerInDevMode": True})
    assert opted.status_code == 200 and state["managed"][-1] == (
        "settings",
        {"ownerInDevMode": True, "refreshToken": state["managed"][-1][1]["refreshToken"]},
    )
    log = ada.post(f"/api/job-sites/{DESK}/audit", json={"limit": 5})
    assert log.status_code == 200 and log.json()["entries"][0]["decision"] == "refused"
    assert bo.post(f"/api/job-sites/{DESK}/audit", json={}).status_code == 409


def test_a_sites_local_server_is_a_tool_like_any_other(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    """A fifth tool, added at the machine, reaches a chat with no change to
    Workbench: it is listed, chosen and run through Eugene like the files."""
    world, state = site_world
    state["local"] = [
        {
            "site": DESK,
            "label": "desk",
            "server": "notes-tool",
            "name": "Notes tool",
            "people": ["p-ada"],
            "tools": [
                {
                    "name": "search",
                    "description": "Search notes.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                        "required": ["q"],
                    },
                }
            ],
        }
    ]
    ada, bo = world.browser(), world.browser()
    ada.sign_in("p-ada")
    bo.sign_in("p-bo")
    listed = ada.get("/api/tools/servers").json()["servers"]
    assert {"id": TOOL, "name": "desk · Notes tool"}.items() <= next(
        s for s in listed if s["id"] == TOOL
    ).items()
    assert all(s["id"] != TOOL for s in bo.get("/api/tools/servers").json()["servers"])
    chat = ada.new_chat()
    chosen = ada.patch(f"/api/chats/{chat}", json={"settings": {"toolServers": [TOOL]}})
    assert chosen.status_code == 200, chosen.text
    bo_chat = bo.new_chat()
    refused = bo.patch(f"/api/chats/{bo_chat}", json={"settings": {"toolServers": [TOOL]}})
    assert refused.status_code == 400
    world.gateway.mode = "tools"
    searching = offer(world, ada, chat, "search", q="plans")
    call = finish(ada, chat, searching)
    assert call["status"] == "done" and call["result"] == 'search: {"q": "plans"}'
    assert call["jobSite"] is True and call["site"] == DESK
    assert state["calls"][-1]["server"] == "notes-tool"


def test_a_person_removes_their_own_link_and_the_owner_names_anyones(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    world, state = site_world
    ada = world.browser()
    ada.sign_in("p-ada")
    assert ada.post(f"/api/job-sites/{DESK}/links/remove", json={}).status_code == 204
    named = ada.post(f"/api/job-sites/{DESK}/links/remove", json={"person": "p-bo"})
    assert named.status_code == 204
    assert [(c["site"], c.get("person")) for c in state["links_removed"]] == [
        (DESK, None),
        (DESK, "p-bo"),
    ]
    assert all(c["refreshToken"] for c in state["links_removed"])
    assert ada.post("/api/job-sites/not-a-site/links/remove", json={}).status_code == 404


@pytest.mark.parametrize("code", [403, 404, 409, 503])
def test_a_refused_link_removal_keeps_eugenes_words_and_status(
    site_world: tuple[World, dict[str, Any]], code: int
) -> None:
    world, state = site_world
    state["link_refusal"] = (code, f"Eugene said {code} in its own words.")
    ada = world.browser()
    ada.sign_in("p-ada")
    refused = ada.post(f"/api/job-sites/{DESK}/links/remove", json={})
    assert refused.status_code == code, refused.text
    assert f"Eugene said {code} in its own words." in refused.text


def test_what_runs_as_whom_reaches_the_folders_and_tools_a_person_sees(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    world, state = site_world
    owner_account = "DESK-owner"
    state["linking"] = {"linked": False, "account": owner_account, "linkPage": "http://h/link"}
    state["local"] = [
        {
            "site": DESK,
            "label": "desk",
            "server": "notes-tool",
            "name": "Notes tool",
            "people": ["p-ada"],
            "tools": [],
        }
    ]
    ada = world.browser()
    ada.sign_in("p-ada")
    expected = (False, owner_account, "http://h/link")

    def node_grant() -> dict[str, Any]:
        grants = ada.get("/api/folders").json()["grants"]
        return next(g for g in grants if g.get("source") == "node")

    grant = node_grant()
    assert (grant["linked"], grant["account"], grant["linkPage"]) == expected
    servers = ada.get("/api/tools/servers").json()["servers"]
    tool = next(s for s in servers if s["id"] == TOOL)
    assert (tool["linked"], tool["account"], tool["linkPage"]) == expected
    state["linking"] = {}  # a machine that predates linking says nothing
    grant = node_grant()
    assert "linked" not in grant and "linkPage" not in grant


def test_a_change_the_machine_holds_reaches_the_page_as_202_with_its_words(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    """J14a: Eugene answers 202 when the job site holds a change for its
    owner's key at the machine (J50). Workbench passes it on as that, not as
    a refusal, with the machine's own words."""
    world, state = site_world
    words = "Waiting for your approval on desk, at http://127.0.0.1:8079/link/approve."
    state["held"] = words
    ada = world.browser()
    ada.sign_in("p-ada")
    held = ada.post(
        f"/api/job-sites/{DESK}/folders/site-notes/people",
        json={"people": [{"name": "bo", "writable": False}]},
    )
    assert held.status_code == 202, held.text
    assert held.json() == {"held": True, "message": words}
    assert held.headers["cache-control"] == "no-store"
    assert state["managed"][-1][:3] == ("folders", "site-notes", "people")


# --- passkeys (J14a.3) --------------------------------------------------------------------

PAIR = {
    "credentialId": "Y3JlZA",
    "publicKey": "cHVibGlj",
    "alg": -7,
    "rpId": "workbench.example",
    "label": "Passkey from Workbench",
    "mac": "m" * 43,
}
APPROVAL = {
    "envelope": "{}x",
    "key": "c" * 32,
    "credentialId": "Y3JlZA",
    "authenticatorData": "YXV0aA",
    "clientDataJSON": "Y2xpZW50",
    "signature": "c2ln",
}


def test_without_an_https_address_there_is_no_relying_party(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    """A plain-HTTP Workbench can hold no passkey: the list says so, and a
    pairing is refused before anything reaches Eugene."""
    world, state = site_world
    ada = world.browser()
    ada.sign_in("p-ada")
    listed = ada.get("/api/job-sites").json()
    assert listed["passkeys"] == {
        "rpId": None,
        "person": "p-ada",
        "name": listed["passkeys"]["name"],
    }
    refused = ada.post(f"/api/job-sites/{DESK}/passkeys", json=PAIR)
    assert refused.status_code == 409 and "https address" in refused.text
    assert state["managed"] == []


def test_a_passkey_and_its_approvals_are_carried_and_the_code_never_is(
    site_world: tuple[World, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from eugene_plexus_workbench import job_sites_api

    monkeypatch.setattr(job_sites_api, "_relying_party", lambda _request: "workbench.example")
    world, state = site_world
    ada = world.browser()
    ada.sign_in("p-ada")
    assert ada.get("/api/job-sites").json()["passkeys"]["rpId"] == "workbench.example"
    other_rp = ada.post(f"/api/job-sites/{DESK}/passkeys", json={**PAIR, "rpId": "evil.example"})
    assert other_rp.status_code == 409
    with_code = ada.post(f"/api/job-sites/{DESK}/passkeys", json={**PAIR, "code": "ABCDE-FGHJK"})
    assert with_code.status_code == 422
    paired = ada.post(f"/api/job-sites/{DESK}/passkeys", json=PAIR)
    assert paired.status_code == 201, paired.text
    action, body = state["managed"][-1]
    assert action == "passkeys" and {k: body[k] for k in PAIR} == PAIR
    assert set(body) == {*PAIR, "refreshToken"}
    assert ada.post(f"/api/job-sites/{DESK}/held", json={"key": "c" * 32}).status_code == 200
    assert state["managed"][-1] == ("held", {"key": "c" * 32, "refreshToken": body["refreshToken"]})
    approved = ada.post(f"/api/job-sites/{DESK}/held/rules/approve", json=APPROVAL)
    assert approved.status_code == 200, approved.text
    what, ident, act, sent = state["managed"][-1]
    assert (what, ident, act) == ("held", "rules", "approve")
    assert {k: sent[k] for k in APPROVAL} == APPROVAL
    rejected = ada.post(f"/api/job-sites/{DESK}/held/abc123/reject")
    assert rejected.status_code == 204
    assert state["managed"][-1][:3] == ("held", "abc123", "reject")
    bad = ada.post(f"/api/job-sites/{DESK}/held/NOT-AN-ID/reject")
    assert bad.status_code == 404
    removed = ada.post(f"/api/job-sites/{DESK}/passkeys/{'c' * 32}/remove")
    assert removed.status_code == 204
    assert state["managed"][-1][:3] == ("passkeys", "c" * 32, "remove")
    assert ada.post(f"/api/job-sites/{DESK}/passkeys/abc123/remove").status_code == 404


def test_the_owner_reaches_every_branch_and_each_is_redacted_along_its_own_path(
    site_world: tuple[World, dict[str, Any]],
) -> None:
    """V5: a person cannot hide a branch from oversight by switching away
    from it, and a branch that never touched a job site stays readable."""
    world, _ = site_world
    owner, ada, chat = _chat_with_a_site_read(world)
    shown = ada.get(f"/api/chats/{chat}").json()["messages"]
    site_answer = next(
        m for m in shown if any(c.get("jobSite") for r in m["toolRounds"] for c in r["calls"])
    )
    world.gateway.words = ["A", " fresh", " try."]
    tried = ada.post(f"/api/chats/{chat}/messages/{site_answer['id']}/retry")
    assert tried.status_code == 201, tried.text
    ada.wait_answer(chat)

    # The owner opens on the person's path: the new try, never at a job site.
    read = owner.get(f"/api/chats/{chat}")
    assert read.status_code == 200 and "SITE-SECRET" not in read.text
    current = read.json()["messages"]
    assert not any(m.get("redacted") for m in current)
    assert current[-1]["content"] == "A fresh try."
    assert current[-1]["versions"]["ids"] == [site_answer["id"], tried.json()["answer"]["id"]]

    # Stepping to the other branch: hidden from the site result on, by its path.
    branch = owner.get(f"/api/chats/{chat}?via={site_answer['id']}")
    assert branch.status_code == 200
    assert "SITE-SECRET" not in branch.text and "Summarise it" not in branch.text
    messages = branch.json()["messages"]
    at = next(i for i, m in enumerate(messages) if m["id"] == site_answer["id"])
    assert not any(m.get("redacted") for m in messages[:at])
    assert len(messages) > at + 1 and all(m.get("redacted") for m in messages[at:])
    assert messages[at]["versions"]["count"] == 2, "the arrows still reach the other branch"
    # Looking moved nothing: the person's path is as they left it.
    assert ada.get(f"/api/chats/{chat}").json()["messages"][-1]["content"] == "A fresh try."
    assert owner.post(f"/api/chats/{chat}/messages/{site_answer['id']}/choose").status_code == 404
