"""Row 70c: a group's memory servers (`docs/design/rows/row-70.md`).

The owner attaches a memory server to a permission group, in Settings. Everyone in the
group sees it there as set by their group, and nobody but the owner can change or remove
it. Who is in a group comes only from each person's own sign-in claims; which groups exist
comes from Eugene's `/oidc/groups`, asked with this Workbench's own secret.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from .conftest import World
from .test_memory_api import config
from .test_memory_servers import TOKEN, Remote, remote  # noqa: F401 (the fixture)
from .test_memory_v2 import signed_in

NIGHT = {"id": "g-night", "name": "Night shift"}
DESK = {"id": "g-desk", "name": "Desk"}


def _member_of(world: World, sub: str, *groups: dict[str, str]) -> None:
    world.eugene.claims[sub] = {"eugene_groups": list(groups), "eugene_policies": {}}


def _attach(owner: Any, group: str, url: str, token: str = TOKEN) -> Any:
    return owner.put(f"/api/groups/{group}/memory-server", json={"url": url, "token": token})


def test_the_owner_adds_a_group_server_every_member_sees_and_cannot_remove(
    world: World,
    remote: Callable[..., Remote],  # noqa: F811
) -> None:
    world.eugene.groups = [DESK, NIGHT]
    _member_of(world, "p-ada", NIGHT)
    _member_of(world, "p-bo", NIGHT, DESK)
    assert config(world, memoryServers="any").status_code == 200
    server = remote()
    owner = signed_in(world, "operator")
    manage = owner.get("/api/groups/manage").json()
    assert [g["id"] for g in manage["groups"]] == ["g-desk", "g-night"]
    assert all(g["server"] is None for g in manage["groups"])
    done = _attach(owner, "g-night", server.url)
    assert done.status_code == 200, done.text
    shown = next(g for g in done.json()["groups"] if g["id"] == "g-night")["server"]
    assert shown["name"] == "Test memory" and shown["hasToken"] is True
    assert TOKEN not in json.dumps(done.json())
    for sub in ("p-ada", "p-bo"):
        member = signed_in(world, sub)
        page = member.get("/api/groups").json()
        servers = page["servers"]
        assert [s["group"] for s in servers] == [NIGHT], sub
        assert servers[0]["server"]["url"] == server.url and TOKEN not in json.dumps(page)
        # A member cannot remove it, change it, or add one.
        assert member.delete("/api/groups/g-night/memory-server").status_code == 403
        assert _attach(member, "g-night", server.url, "").status_code == 403
        assert _attach(member, "g-desk", server.url).status_code == 403
        assert member.get("/api/groups/manage").status_code == 403
        # Their own server is still theirs alone: the group's is not it.
        assert member.get("/api/memory/server").json()["server"] is None
    # Someone in no group sees no group's server.
    world.eugene.people["p-cy"] = {"name": "Cy", "role": "member", "username": "cy"}
    assert signed_in(world, "p-cy").get("/api/groups").json()["servers"] == []
    assert owner.get("/api/groups/manage").status_code == 200


def test_taken_out_of_the_group_a_member_no_longer_sees_its_server(
    world: World,
    remote: Callable[..., Remote],  # noqa: F811
) -> None:
    world.eugene.groups = [NIGHT]
    _member_of(world, "p-ada", NIGHT)
    assert config(world, memoryServers="any").status_code == 200
    owner = signed_in(world, "operator")
    assert _attach(owner, "g-night", remote().url).status_code == 200
    world.eugene.expires_in = 1  # every call is due for a refresh
    ada = signed_in(world, "p-ada")
    assert len(ada.get("/api/groups").json()["servers"]) == 1
    _member_of(world, "p-ada")
    time.sleep(0.05)
    assert ada.get("/api/groups").json()["servers"] == []


def test_a_group_eugene_does_not_list_gets_no_server(
    world: World,
    remote: Callable[..., Remote],  # noqa: F811
) -> None:
    """A group id from another install, or one deleted since: refused before any connection."""
    world.eugene.groups = [NIGHT]
    assert config(world, memoryServers="any").status_code == 200
    server = remote()
    owner = signed_in(world, "operator")
    refused = _attach(owner, "g-elsewhere", server.url)
    assert refused.status_code == 404
    assert "Eugene has no group" in refused.json()["detail"]["message"]
    assert server.tokens == []  # nothing was sent to it
    # An id no group of Eugene's could have, nor a path: refused by its shape.
    assert _attach(owner, "bad%20id", server.url).status_code == 404
    assert _attach(owner, "group%3Aelsewhere", server.url).status_code == 404


def test_a_server_whose_group_was_deleted_is_shown_to_the_owner_to_remove(
    world: World,
    remote: Callable[..., Remote],  # noqa: F811
) -> None:
    world.eugene.groups = [NIGHT]
    assert config(world, memoryServers="any").status_code == 200
    owner = signed_in(world, "operator")
    assert _attach(owner, "g-night", remote().url).status_code == 200
    world.eugene.groups = []
    manage = owner.get("/api/groups/manage").json()
    assert manage["groups"] == []
    assert [g["id"] for g in manage["gone"]] == ["g-night"]
    removed = owner.delete("/api/groups/g-night/memory-server")
    assert removed.status_code == 200 and removed.json()["gone"] == []


def test_the_owners_rule_for_memory_servers_holds_for_groups(
    world: World,
    remote: Callable[..., Remote],  # noqa: F811
) -> None:
    world.eugene.groups = [NIGHT]
    owner = signed_in(world, "operator")
    assert config(world, memoryServers="off").status_code == 200
    page = owner.get("/api/groups/manage").json()
    assert page["allowed"] is False and page["why"]
    assert _attach(owner, "g-night", remote().url).status_code == 403
    # Loopback, by default, as for a person's own server.
    assert config(world, memoryServers="outside").status_code == 200
    refused = _attach(owner, "g-night", remote().url)
    assert refused.status_code == 400 and "loopback" in refused.json()["detail"]["message"]


def test_when_eugene_cannot_list_groups_the_owner_is_told_why(world: World) -> None:
    world.eugene.groups = None  # an Eugene from before row 70c
    owner = signed_in(world, "operator")
    page = owner.get("/api/groups/manage")
    assert page.status_code == 200, page.text
    assert page.json()["groups"] == [] and "Update Eugene" in page.json()["problem"]
    world.eugene.groups = []
    world.eugene.up = False
    down = owner.get("/api/groups/manage").json()
    assert down["problem"] and "503" in down["problem"]


def test_a_group_server_is_not_a_persons_and_survives_a_person_purge(
    world: World,
    remote: Callable[..., Remote],  # noqa: F811
) -> None:
    """Group servers are kept as `group:<id>`: no person's id, so asking Eugene who was
    removed never names them, and removing a person leaves them."""
    world.eugene.groups = [NIGHT]
    _member_of(world, "p-ada", NIGHT)
    assert config(world, memoryServers="any").status_code == 200
    owner = signed_in(world, "operator")
    assert _attach(owner, "g-night", remote().url).status_code == 200
    signed_in(world, "p-ada")
    del world.eugene.people["p-ada"]
    checked = owner.post("/api/removed-people/check")
    assert checked.status_code in (200, 202, 204), checked.text
    asked = [s for call in world.eugene.subject_calls for s in call]
    assert asked and not any(s.startswith("group:") for s in asked)
    manage = owner.get("/api/groups/manage").json()
    assert next(g for g in manage["groups"] if g["id"] == "g-night")["server"] is not None
