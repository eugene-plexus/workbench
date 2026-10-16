"""The gateway's list of models, shared only within one person's session (MR5, MR6b, U3).

Search by meaning needs to know which search models there are and whether one is running, and
sorting needs the same of result sorters. Both are read from one `GET /v1/models`, so they are one
look: kept for a few seconds, made by one asker at a time, and shared within that identity.
Owner configuration uses a separate app-catalog scope. Each kept its own copy at
first, and the Documents screen asked both, so a gateway that does not answer its list of models
held `GET /api/documents` for the 8 seconds a look may take twice, one after the other (MR6b
review). With one look, the second asker finds the first's failure kept for a moment and does
not wait.

What is kept is the gateway's answer as it was (`Look.listing`), or why there was none
(`Look.error`, a `HubError` in words a person reads); each asker picks out of it the models with
the surface it uses (`Probe`, made by `Listing.probe`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..hub import HubError
from ..person_context import catalog_only
from ..person_context import current as person_context
from . import embedders
from .embedders import SearchModel

#: How long a look is kept, a failed one, and the most a look may take.
TTL = 10.0
ERROR_TTL = 3.0
TIMEOUT = 8.0


class Gateway(Protocol):
    """What a look needs of the gateway: `hub.Hub` is one."""

    async def models(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Look:
    """One look at the gateway's list of models."""

    at: float
    #: What the gateway answered, as it answered; None when it could not be had (`error`).
    listing: dict[str, Any] | None = None
    #: Why it could not be had, in words a person reads.
    error: HubError | None = None


@dataclass(frozen=True)
class Probe:
    """A look at the gateway's list of models, as one asker uses it: the models that have the
    surface it asked for."""

    at: float
    models: tuple[SearchModel, ...] = ()
    #: Every model the gateway lists, whatever it is for (a model that is not a search model is
    #: told from one that is not there).
    listed: frozenset[str] = frozenset()
    #: Why it could not be asked, if it could not.
    error: HubError | None = None


def unreadable() -> HubError:
    """Not JSON, or not a list of models: said as what it is, and never a failure."""
    return HubError(
        "Eugene's gateway answered the list of its models with something Workbench cannot read.",
        status=502,
        kind="unusable",
    )


def probe_of(look: Look, surface: str) -> Probe:
    """The models of `look` that have `surface`, or why there are none to be had."""
    if look.listing is None:
        return Probe(look.at, error=look.error)
    try:
        models, listed = embedders.models_with(look.listing, surface)
    except (ValueError, AttributeError, TypeError):
        return Probe(look.at, error=unreadable())
    return Probe(look.at, tuple(models), frozenset(listed))


class Listing:
    """The gateway's list of models, cached for `TTL` seconds (a failed look for `ERROR_TTL`), so
    a screen that asks every second asks the gateway once in ten. One per Workbench."""

    def __init__(self, gateway: Gateway, *, clock: Callable[[], float] = time.perf_counter) -> None:
        self._gateway = gateway
        self._clock = clock
        self._last: Look | None = None
        self._lock = asyncio.Lock()
        self._identity: tuple[tuple[str, str | None] | None, bool] = (None, False)
        #: What each surface makes of the look that is kept, so that an asker that asks every
        #: second does not pick through the list every second.
        self._probes: dict[str, tuple[Look, Probe]] = {}

    @property
    def last(self) -> Look | None:
        """The look that was made last, however old it is; None before there was one."""
        return self._last if self._identity == (person_context.get(), catalog_only.get()) else None

    def forget(self) -> None:
        """What was last seen is not trusted any more (a request that used it just failed): the
        next look is a fresh one."""
        self._last = None

    def _good(self, found: Look) -> bool:
        """Whether a look is still as good as a new one: ten seconds for one that listed the
        models, three for one that could not."""
        return self._clock() - found.at < (ERROR_TTL if found.error else TTL)

    async def look(self, *, fresh: bool = False) -> Look:
        """The gateway's models: the look that is kept, or a new one (always, when `fresh`).
        Never raises: a gateway that cannot be asked, does not answer within `TIMEOUT` seconds or
        answers with something that is not a list of models is a look that says so."""
        identity = (person_context.get(), catalog_only.get())
        kept = self._last if self._identity == identity else None
        if not fresh and kept is not None and self._good(kept):
            return kept
        async with self._lock:
            current = self._last if self._identity == identity else None
            # Somebody else looked while this one waited its turn: that look is as new as one
            # made now for this same identity, so no second request is
            # made behind it.
            if current is not None and (
                (fresh and current is not kept) or (not fresh and self._good(current))
            ):
                return current
            found = await self._ask()
            self._last = found
            self._identity = identity
            return found

    async def probe(self, surface: str, *, fresh: bool = False) -> Probe:
        """The models of the look (`look`) that have `surface`."""
        look = await self.look(fresh=fresh)
        held = self._probes.get(surface)
        if held is not None and held[0] is look:
            return held[1]
        found = probe_of(look, surface)
        self._probes[surface] = (look, found)
        return found

    async def _ask(self) -> Look:
        try:
            listing = await asyncio.wait_for(self._gateway.models(), TIMEOUT)
        except HubError as exc:
            return Look(self._clock(), error=exc)
        except TimeoutError:
            return Look(
                self._clock(),
                error=HubError(
                    f"Eugene's gateway did not list its models within {TIMEOUT:.0f} seconds.",
                    status=504,
                    kind="timeout",
                ),
            )
        except (ValueError, AttributeError, TypeError):
            return Look(self._clock(), error=unreadable())
        if not isinstance(listing, dict):
            return Look(self._clock(), error=unreadable())
        return Look(self._clock(), listing)
