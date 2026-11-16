"""Row 40: a person's own usage in their Settings, as Eugene's root counts it."""

from __future__ import annotations

from .conftest import World
from .test_memory_v2 import signed_in

REPORT = {
    "day": "2026-11-16",
    "month": "2026-11",
    "kept": {"days": 62, "months": 13},
    "subjects": [
        {
            "kind": "person",
            "id": "p-ada",
            "name": "Ada",
            "day": {
                "requests": 2,
                "promptTokens": 20,
                "completionTokens": 10,
                "tokens": 30,
                "costUsd": "0.000300000000",
                "unpricedRequests": 0,
                "models": [],
            },
            "month": {
                "requests": 2,
                "promptTokens": 20,
                "completionTokens": 10,
                "tokens": 30,
                "costUsd": "0.000300000000",
                "unpricedRequests": 0,
                "models": [],
            },
            "allowances": [],
        }
    ],
}


def test_a_person_sees_their_own_usage_as_eugene_counts_it(world: World) -> None:
    world.eugene.usage = {"p-ada": REPORT}
    ada = signed_in(world, "p-ada")
    page = ada.get("/api/usage")
    assert page.status_code == 200, page.text
    assert page.json() == {"known": True, "report": REPORT}
    # Each person asks with their own sign-in: Bo gets Bo's, not Ada's.
    bo = signed_in(world, "p-bo").get("/api/usage").json()
    assert bo["known"] is True and bo["report"]["subjects"] == []


def test_an_eugene_that_counts_no_usage_says_so_not_zero(world: World) -> None:
    world.eugene.usage = None
    page = signed_in(world, "p-ada").get("/api/usage").json()
    assert page == {
        "known": False,
        "why": "This Eugene does not count usage yet. Update Eugene to see yours here.",
    }


def test_usage_needs_a_sign_in(world: World) -> None:
    assert world.browser().get("/api/usage").status_code == 401
