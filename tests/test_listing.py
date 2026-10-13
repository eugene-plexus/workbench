"""The gateway's list of models, looked at once for everyone who asks (MR6b review, item 7).

Search by meaning and sorting each kept their own copy of the look at `GET /v1/models`, so the
Documents screen, which asks both, was held for the 8 seconds a look may take twice by a gateway
that does not answer its list. `Listing` is the one look both read.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from eugene_plexus_workbench.hub import HubError
from eugene_plexus_workbench.knowledge import embedders, listing
from eugene_plexus_workbench.knowledge.listing import Listing

from .conftest import search_model, sorter_model


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Gateway:
    """A gateway that lists what a test says, or fails as a test says, and counts its looks."""

    def __init__(self) -> None:
        self.asked = 0
        self.listing: Any = {
            "data": [search_model("embed"), sorter_model("sorter"), {"id": "chat-model"}]
        }
        self.error: BaseException | None = None
        self.delay = 0.0

    async def models(self) -> Any:
        self.asked += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.listing


def make() -> tuple[Listing, Gateway, Clock]:
    clock, gateway = Clock(), Gateway()
    return Listing(gateway, clock=clock), gateway, clock


async def test_a_look_is_kept_for_ten_seconds_and_a_failed_one_for_three() -> None:
    found, gateway, clock = make()
    first = await found.look()
    assert first.error is None and first.listing is not None and gateway.asked == 1
    clock.now += listing.TTL - 0.1
    assert await found.look() is first and gateway.asked == 1
    clock.now += 0.2
    second = await found.look()
    assert second is not first and gateway.asked == 2
    gateway.error = HubError("down", status=503, kind="unreachable")
    clock.now += listing.TTL + 1
    failed = await found.look()
    assert (
        failed.listing is None and failed.error is not None and failed.error.kind == "unreachable"
    )
    clock.now += listing.ERROR_TTL - 0.1
    assert await found.look() is failed and gateway.asked == 3
    clock.now += 0.2
    assert await found.look() is not failed and gateway.asked == 4


async def test_a_look_that_is_asked_for_fresh_is_made_again_and_forgotten_is_made_again() -> None:
    found, gateway, _ = make()
    await found.look()
    await found.look(fresh=True)
    assert gateway.asked == 2
    await found.look()
    assert gateway.asked == 2, "the fresh look is the one that is kept"
    found.forget()
    assert found.last is None
    await found.look()
    assert gateway.asked == 3


async def test_askers_that_come_together_make_one_look_between_them() -> None:
    found, gateway, _ = make()
    gateway.delay = 0.05
    looks = await asyncio.gather(*(found.look() for _ in range(5)))
    assert gateway.asked == 1 and all(look is looks[0] for look in looks)
    # Fresh askers that were waiting behind a fresh look use it, and do not make another.
    gateway.delay = 0.05
    fresh = await asyncio.gather(*(found.look(fresh=True) for _ in range(4)))
    assert gateway.asked == 2 and all(look is fresh[0] for look in fresh)


async def test_a_gateway_that_does_not_answer_is_a_look_that_says_so_after_the_time_a_look_may_take(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 8 seconds a look may take (shortened here): the branch the review found no test ran."""
    monkeypatch.setattr(listing, "TIMEOUT", 0.05)
    found, gateway, _ = make()
    gateway.delay = 5.0
    look = await found.look()
    assert look.listing is None and look.error is not None
    assert look.error.kind == "timeout" and look.error.status == 504
    assert "did not list its models within" in look.error.message


async def test_the_time_a_look_may_take_is_said_in_whole_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(listing, "TIMEOUT", 8.0)
    found, gateway, _ = make()
    gateway.error = TimeoutError()
    look = await found.look()
    assert look.error is not None
    assert look.error.message == "Eugene's gateway did not list its models within 8 seconds."


@pytest.mark.parametrize(
    "broken",
    [ValueError("not JSON"), AttributeError("no get"), TypeError("no"), ["a", "list"], None],
)
async def test_an_answer_that_is_not_a_list_of_models_is_a_look_that_says_so(broken: Any) -> None:
    found, gateway, _ = make()
    if isinstance(broken, BaseException):
        gateway.error = broken
    else:
        gateway.listing = broken
    look = await found.look()
    assert look.listing is None and look.error is not None and look.error.kind == "unusable"
    assert look.error.message == (
        "Eugene's gateway answered the list of its models with something Workbench cannot read."
    )


async def test_an_error_the_gateway_gave_is_kept_as_it_was_said() -> None:
    found, gateway, _ = make()
    gateway.error = HubError("refused", status=401, kind="key")
    look = await found.look()
    assert look.error is gateway.error and look.listing is None


async def test_each_asker_picks_out_the_models_with_the_surface_it_uses() -> None:
    found, _, _ = make()
    searching = await found.probe(embedders.SURFACE)
    sorting = await found.probe("rerank")
    assert [m.id for m in searching.models] == ["embed"]
    assert [m.id for m in sorting.models] == ["sorter"]
    assert searching.listed == sorting.listed == {"embed", "sorter", "chat-model"}
    assert searching.at == sorting.at
    # What each makes of the look that is kept is made once, and again for a look that is new.
    assert await found.probe("rerank") is sorting
    found.forget()
    assert await found.probe("rerank") is not sorting


async def test_a_probe_of_a_look_that_failed_carries_why() -> None:
    found, gateway, _ = make()
    gateway.error = HubError("down", status=503, kind="unreachable")
    probe = await found.probe("rerank")
    assert probe.models == () and probe.error is gateway.error


async def test_a_list_with_an_entry_that_cannot_be_read_is_a_probe_that_says_so() -> None:
    found, gateway, _ = make()
    gateway.listing = {"data": [{"id": "odd", "x_eugene_plexus": ["not", "an", "object"]}]}
    probe = await found.probe("rerank")
    assert probe.error is not None and probe.error.kind == "unusable"
    assert probe.models == ()
