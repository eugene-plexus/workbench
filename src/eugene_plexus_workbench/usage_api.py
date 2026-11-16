"""A person's own usage, for their Settings (row 40, `docs/design/rows/row-40.md`).

Eugene's control root counts what each person uses, across every gateway, and
the allowances their permission groups set. Workbench asks it for the signed-in
person's own numbers at `GET <issuer>/usage` with that person's access token,
the one Workbench already sends with their model requests, and shows them. It
keeps nothing: every read is Eugene's answer now.

An Eugene before row 40 has no such page (404): Settings says that this Eugene
does not count usage yet, rather than a zero.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request

from .signin import SignInUnavailable

log = logging.getLogger(__name__)

router = APIRouter()

NOT_COUNTED = "This Eugene does not count usage yet. Update Eugene to see yours here."
UNREACHABLE = "Workbench could not ask Eugene for your usage. Try again in a minute."


@router.get("/api/usage")
async def get_usage(request: Request) -> dict[str, Any]:
    """The signed-in person's usage today and this month, and their groups' allowances."""
    from .api import _person, _provider, _state
    from .hub import HubError

    await _person(request)
    provider = _provider(request)
    if not provider.issuer:
        return {"known": False, "why": "Workbench is not signed in to an Eugene."}
    try:
        token = await _state(request).sessions.gateway_token()
    except HubError as exc:
        return {"known": False, "why": str(exc)}
    try:
        response = await provider._http.get(
            provider.transport_url(f"{provider.issuer}/usage"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
    except (httpx.HTTPError, SignInUnavailable) as exc:
        log.info("could not read the person's usage: %s", type(exc).__name__)
        return {"known": False, "why": UNREACHABLE}
    if response.status_code == 404:
        return {"known": False, "why": NOT_COUNTED}
    if response.status_code != 200:
        return {
            "known": False,
            "why": f"Eugene did not give your usage (HTTP {response.status_code}).",
        }
    try:
        report = response.json()
    except ValueError:
        return {"known": False, "why": UNREACHABLE}
    if not isinstance(report, dict) or not isinstance(report.get("subjects"), list):
        return {"known": False, "why": UNREACHABLE}
    return {"known": True, "report": report}
