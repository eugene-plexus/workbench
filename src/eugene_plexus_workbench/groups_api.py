"""A person's permission groups, the policies they set, and groups' memory servers (row 70c,
`docs/design/rows/row-70.md`).

**Who is in which group is Eugene's to say**, in each person's own sign-in claims
(`eugene_groups`, `eugene_policies`), read at sign-in and at every refresh and kept with the
person (`Person.groups`, `Person.policies`). Nothing a browser sends changes them.

**A group's memory server is Workbench's**: an address and a token, kept as any person's
server is but under the owner `group:<id>`, which is no person's id. Only this Workbench's
owner attaches or removes one, and only to a group Eugene lists for this app now
(`/oidc/groups`, asked with this Workbench's own secret). Everyone in the group sees it in
Settings as set by their group, and nobody else can change or remove it.

Sending a member's chats to their groups' servers is row 27's: this module keeps the servers
and shows them.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, status

from . import memory_servers as ms
from .signin import GROUP_ID, POLICIES, SignInUnavailable
from .store import Person, Store

log = logging.getLogger(__name__)

router = APIRouter()

#: How a group's server is kept: an owner no person's id can be.
OWNER_PREFIX = "group:"
ONLY_OWNER = "Only the owner of this Workbench sets a group's memory server."


def owner_of(group_id: str) -> str:
    return OWNER_PREFIX + group_id


def _group_names(person: Person) -> dict[str, dict[str, str]]:
    return {g["id"]: g for g in person.groups or []}


async def person_view(store: Store, person: Person) -> dict[str, Any]:
    """What a person's Settings shows of their groups: each group, each policy a group sets
    with the groups that set it, and each of their groups' memory servers (never a token)."""
    mode, why_not = await ms.mode_for(store)
    named = _group_names(person)
    policies = []
    for name in POLICIES:
        if person.policies and name in person.policies:
            ids = (person.policies.get("setBy") or {}).get(name, [])
            policies.append(
                {
                    "policy": name,
                    "value": person.policies[name],
                    "setBy": [named.get(i, {"id": i, "name": i}) for i in ids],
                }
            )
    servers = []
    for group in person.groups or []:
        server = await store.memory_server(owner_of(group["id"]))
        if server is not None:
            servers.append({"group": group, "server": ms.view(server, {})})
    return {
        "known": person.groups is not None,
        "groups": list(person.groups or []),
        "policies": policies,
        "servers": servers,
        "serversAllowed": mode != "off",
        "why": why_not,
    }


@router.get("/api/groups")
async def get_groups(request: Request) -> dict[str, Any]:
    """The signed-in person's groups and what those set for them."""
    from .api import _person, _state

    person = await _person(request)
    return await person_view(_state(request).store, person)


# --------------------------------------------------------------------------- #
# the owner's: a memory server per group
# --------------------------------------------------------------------------- #


async def _listed(request: Request) -> tuple[list[dict[str, str]], str | None]:
    """The groups Eugene lists for this app now, or none and why not."""
    from .api import _state

    try:
        return await _state(request).provider.groups(), None
    except SignInUnavailable as exc:
        return [], f"Workbench could not ask Eugene for its groups: {exc}."


async def _manage(request: Request) -> dict[str, Any]:
    from .api import _state

    store: Store = _state(request).store
    mode, why_not = await ms.mode_for(store)
    groups, problem = await _listed(request)
    kept = {s["owner"][len(OWNER_PREFIX) :]: s for s in await store.group_memory_servers()}
    listed = []
    for group in groups:
        server = kept.pop(group["id"], None)
        listed.append({**group, "server": ms.view(server, {})})
    return {
        "allowed": mode != "off",
        "mode": mode,
        "why": why_not,
        "problem": problem,
        "groups": listed,
        # Servers of groups Eugene does not list now (deleted there, or no longer giving this
        # app). Shown for the owner to remove: nothing deletes them by itself. While Eugene
        # cannot be asked, every server is here, by its group's id.
        "gone": [{"id": i, "server": ms.view(s, {})} for i, s in sorted(kept.items())],
    }


async def _owner(request: Request) -> Person:
    from .api import _person, _problem

    person = await _person(request)
    if not person.is_owner:
        raise _problem(status.HTTP_403_FORBIDDEN, ONLY_OWNER)
    return person


def _group_id(group_id: str) -> str:
    from .api import _problem

    if not GROUP_ID.fullmatch(group_id):
        raise _problem(status.HTTP_404_NOT_FOUND, "Eugene has no group with that id.")
    return group_id


@router.get("/api/groups/manage")
async def get_manage(request: Request) -> dict[str, Any]:
    """Every group Eugene lists for this app, with its memory server if it has one."""
    await _owner(request)
    return await _manage(request)


@router.put("/api/groups/{group_id}/memory-server")
async def put_group_server(
    request: Request, group_id: str, body: ms.ServerConnect
) -> dict[str, Any]:
    """Attach a memory server to a group: reached, its tools listed, and kept only when it can
    be asked (`recall`), as a person's own. The group must be one Eugene lists now."""
    from .api import _problem, _state

    await _owner(request)
    group_id = _group_id(group_id)
    store: Store = _state(request).store
    mode, why_not = await ms.mode_for(store)
    if mode == "off":
        raise _problem(status.HTTP_403_FORBIDDEN, why_not or ms.OWNER_OFF)
    groups, problem = await _listed(request)
    if problem is not None:
        raise _problem(status.HTTP_503_SERVICE_UNAVAILABLE, problem)
    if not any(g["id"] == group_id for g in groups):
        raise _problem(
            status.HTTP_404_NOT_FOUND,
            "Eugene has no group with that id that gives this Workbench. Reload the list.",
        )
    url = body.url.strip()
    if (why := ms.check_url(url)) is not None:
        raise _problem(status.HTTP_400_BAD_REQUEST, why)
    token = body.token
    owner = owner_of(group_id)
    if token is None:
        old = await store.memory_server(owner)
        token = old["token"] if old is not None and old["url"] == url else ""
    token = token.strip()
    servers: ms.Servers = _state(request).memory_servers
    try:
        name, verbs = await servers.discover(url, token, mode)
    except ms.ServerProblem as exc:
        raise _problem(status.HTTP_400_BAD_REQUEST, exc.message) from None
    await store.put_memory_server(owner, url, token, name, verbs)
    await store.memory_server_reached(owner, None)
    log.info("the owner attached a memory server to a group (%d verbs)", len(verbs))
    return await _manage(request)


@router.delete("/api/groups/{group_id}/memory-server")
async def delete_group_server(request: Request, group_id: str) -> dict[str, Any]:
    """Remove a group's memory server. Nothing on the server is deleted. Allowed for a group
    Eugene no longer lists, so a deleted group's server can be removed too."""
    from .api import _state

    await _owner(request)
    group_id = _group_id(group_id)
    await _state(request).store.delete_memory_server(owner_of(group_id))
    return await _manage(request)
