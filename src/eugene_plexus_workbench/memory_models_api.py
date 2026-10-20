"""Which models learn, and Measure this model (MV3a, design sections 29-30).

`GET /api/memory/models` lists the chat models a person can use, with what each does for them
(Off, Suggest, Keep automatically), whose word that is, Eugene's recommendation and its reason;
`PUT /api/memory/models/{model}` sets their choice. `POST /api/memory/measurements` runs the
labelled sets (`memory_measure`) against a model through the gateway, as the person who asked,
one run at a time for the install; its result is the install's, kept per model id, and shown
to everyone beside the recommendation.

The sets are made up, so a measurement sends nothing of anyone's; an outside model still
charges for it, so the person is told how much may go before it starts (`confirmOutside`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import config, memory_measure, memory_models, memory_write
from .api import _person, _problem, _state
from .hub import Hub, HubError
from .media_api import _common as served
from .person_context import acting_as
from .store import Person, Store

log = logging.getLogger(__name__)
router = APIRouter()

#: A person may start this many measurements a day, and the install this many in all.
RUNS_PER_DAY = 3
INSTALL_RUNS_PER_DAY = 12
#: A measurement stops after this long ("took too long").
RUN_SECONDS = 3_600.0
#: Each request's own limit, as the learning pass has.
REQUEST_SECONDS = 120.0
#: The most tokens one request of a measurement may take, prompt and answer: what the screen
#: says may go to an outside model.
TOKENS_PER_REQUEST = 2_000


@dataclass
class Run:
    """The measurement in progress."""

    model: str
    owner: str
    started_at: float
    total: int
    outside: bool
    by_owner: bool = False
    done: int = 0
    task: asyncio.Task[None] | None = None


@dataclass
class Measurer:
    """One per Workbench: the run in progress, how each model's last run ended, and how many
    runs each person (and the install) started today. Counted in memory: a restart forgets
    them."""

    store: Store
    hub: Hub
    current: Run | None = None
    #: How the last run of each model ended when it did not finish: `cancelled`, `too_long`,
    #: `failed` (with why, shown to its starter only), or `stopped` (Workbench stopped).
    ended: dict[str, dict[str, Any]] = field(default_factory=dict)
    started: dict[str, list[float]] = field(default_factory=dict)
    #: Held from the check that no run is going to the start of the next, so two requests at
    #: once cannot both start one.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def left_today(self, owner: str, now: float) -> int:
        recent = [t for t in self.started.get(owner, []) if now - t < 86_400.0]
        self.started[owner] = recent
        everyone = sum(1 for times in self.started.values() for t in times if now - t < 86_400.0)
        return max(0, min(RUNS_PER_DAY - len(recent), INSTALL_RUNS_PER_DAY - everyone))

    def start(self, person: Person, model: str, *, tools: bool, outside: bool) -> Run:
        now = time.time()
        self.started.setdefault(person.sub, []).append(now)
        run = Run(
            model,
            person.sub,
            now,
            memory_measure.requests_for(tools),
            outside,
            by_owner=person.is_owner,
        )
        self.current = run
        self.ended.pop(model, None)
        run.task = asyncio.create_task(self._go(run, person, tools))
        return run

    async def _go(self, run: Run, person: Person, tools: bool) -> None:
        async def complete(body: dict[str, Any]) -> dict[str, Any]:
            with acting_as(person.sub, person.session_id):
                return await self.hub.complete(body, seconds=REQUEST_SECONDS)

        def progress(done: int, total: int) -> None:
            run.done = done

        limit: asyncio.Timeout | None = None
        try:
            async with asyncio.timeout(RUN_SECONDS) as limit:
                result = await memory_measure.measure(
                    complete, run.model, tools=tools, progress=progress
                )
            # Whether it may set what the install does by default (section 30): the owner's
            # own run, never another person's, which is advice beside the choice.
            result["startedByOwner"] = run.by_owner
            await self.store.put_memory_measurement(run.model, run.owner, result)
            log.info(
                "a model was measured for memory: suggest %s, keep %s",
                result["verdict"]["suggest"],
                result["verdict"]["keep"],
            )
        except asyncio.CancelledError:
            self.ended[run.model] = {"outcome": "cancelled", "at": time.time()}
            raise
        except TimeoutError as exc:
            if limit is not None and limit.expired():
                self.ended[run.model] = {"outcome": "too_long", "at": time.time()}
            else:
                self._failed(run, exc)
        except Exception as exc:
            self._failed(run, exc)
        finally:
            if self.current is run:
                self.current = None

    def _failed(self, run: Run, exc: BaseException) -> None:
        log.warning("measuring a model failed: %s: %s", type(exc).__name__, exc)
        self.ended[run.model] = {
            "outcome": "failed",
            "at": time.time(),
            "owner": run.owner,
            "why": str(exc)[:300] or type(exc).__name__,
        }

    async def aclose(self) -> None:
        run = self.current
        if run is not None and run.task is not None:
            run.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await run.task
            self.ended[run.model] = {"outcome": "stopped", "at": time.time()}


def measurer(request: Request) -> Measurer:
    state = _state(request)
    found = getattr(state, "measurer", None)
    if found is None:
        found = state.measurer = Measurer(state.store, state.hub)
    return found


def _run_view(run: Run | None, person: Person) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "model": run.model,
        "done": run.done,
        "total": run.total,
        "startedAt": run.started_at,
        "yours": run.owner == person.sub,
        "outside": run.outside,
    }


async def _listing(hub: Hub) -> tuple[list[dict[str, Any]], str | None]:
    """The chat models the gateway lists, as this screen needs them; and why none, if it
    could not be asked."""
    try:
        listing = await hub.models()
    except HubError as exc:
        return [], exc.message
    out = []
    for model in listing.get("data") or []:
        info = model.get("x_eugene_plexus") or {}
        surfaces = info.get("surfaces")
        if surfaces is not None and "chat" not in surfaces:
            continue
        common = served(model)
        out.append(
            {
                "id": common["id"],
                "locality": common["locality"]
                if common["locality"] in ("local", "external")
                else "unknown",
                "provider": common["provider"] or common["account"],
                "tools": info.get("tool_calling") is not False,
            }
        )
    return out, None


def _ended_view(ended: dict[str, Any] | None, person: Person) -> dict[str, Any] | None:
    if ended is None:
        return None
    if ended.get("owner") not in (None, person.sub):
        # Why another person's run failed is theirs (an error may name their session).
        ended = {k: v for k, v in ended.items() if k not in ("why", "owner")}
    words = {
        "cancelled": "The last measurement was cancelled.",
        "too_long": "The last measurement took longer than an hour and was stopped.",
        "failed": "The last measurement failed: {why}.",
        "stopped": "The last measurement was stopped when Workbench stopped.",
    }
    return {
        **{k: v for k, v in ended.items() if k != "owner"},
        "text": words.get(ended["outcome"], "The last measurement did not finish.").format(
            why=ended.get("why") or "no reason given"
        ),
    }


@router.get("/api/memory/models")
async def list_models(request: Request) -> dict[str, Any]:
    """Every chat model the person can use, and any they chose or the install measured: what
    each does for them, whose word that is, and Eugene's recommendation with its reason."""
    person = await _person(request)
    state = _state(request)
    store: Store = state.store
    install = await config.memory_settings(store)
    prefs = await store.memory_prefs(person.sub)
    choices = await store.memory_model_choices(person.sub)
    policy, _ = memory_write.policy_for(install, prefs)
    keep_allowed = policy.get("keepAuto") == "on"
    local = await store.memory_measurements()
    listed, problem = await _listing(state.hub)
    seen = {m["id"] for m in listed}
    # Beside what the gateway lists for them: only models they chose themselves (a model
    # someone else measured may be one this person cannot use).
    for extra in sorted(set(choices) - seen):
        listed.append(
            {"id": extra, "locality": "unknown", "provider": None, "tools": True, "listed": False}
        )
    run = measurer(request)
    out = []
    for model in listed:
        # The recommendation shows the newest run, anyone's; the default follows Eugene's or
        # the owner's alone (section 30).
        measured = memory_models.measurement_for(model["id"], local)
        installs = memory_models.measurement_for(model["id"], local, for_default=True)
        mode, by = memory_models.effective(
            install,
            prefs,
            model["id"],
            choice=choices.get(model["id"]),
            measured=installs,
            keep_allowed=keep_allowed,
        )
        default, _ = memory_models.effective(install, prefs, model["id"], measured=installs)
        if mode == memory_models.OFF and by == memory_models.YOU and not prefs["enabled"]:
            by = "memory_off"  # their Memory switch, not their choice for this model
        out.append(
            {
                **model,
                "listed": model.get("listed", True),
                "mode": mode,
                "by": by,
                "chosen": choices.get(model["id"]),
                # What "the default" comes to for this model, if they forget their choice.
                "defaultMode": default,
                "recommendation": memory_models.recommendation(measured),
                "tagsInRequest": memory_models.tags_in_request(installs),
                "lastRun": _ended_view(run.ended.get(model["id"]), person),
            }
        )
    return {
        "models": out,
        "listingError": problem,
        "allowed": install.enabled and install.suggestions != "off",
        # Whether the write policy in effect lets a model keep notes by itself (MV3b), and if
        # not, whose policy says so.
        "keepAllowed": keep_allowed,
        "policy": policy.name,
        "measuring": _run_view(run.current, person),
        "runsLeftToday": run.left_today(person.sub, time.time()),
        "requests": memory_measure.requests_for(True),
    }


class ModelChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: `null` forgets the choice: the model then does what the person's default says.
    mode: Literal["off", "suggest", "auto"] | None


@router.put("/api/memory/models/{model:path}")
async def choose(request: Request, model: str, body: ModelChoice) -> dict[str, Any]:
    person = await _person(request)
    model = model.strip()
    if not model or len(model) > 256:
        raise _problem(422, "A model id has 1 to 256 characters.")
    state = _state(request)
    chosen = await state.store.memory_model_choices(person.sub)
    if model not in chosen:
        # A choice is for a model Eugene lists for them (or one they already chose), so the
        # list of choices cannot grow without end.
        listed, problem = await _listing(state.hub)
        if model not in {m["id"] for m in listed}:
            raise _problem(
                404 if problem is None else 502,
                problem or "Eugene does not list this model, so there is nothing to choose.",
            )
    await state.store.put_memory_model_choice(person.sub, model, body.mode)
    return await list_models(request)


class MeasureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=256)
    #: The person agreed that made-up messages go to an outside model, which charges for them.
    confirmOutside: StrictBool = False


@router.get("/api/memory/measurements")
async def measurements(request: Request) -> dict[str, Any]:
    person = await _person(request)
    run = measurer(request)
    return {
        "measuring": _run_view(run.current, person),
        "runsLeftToday": run.left_today(person.sub, time.time()),
    }


@router.post("/api/memory/measurements", status_code=status.HTTP_202_ACCEPTED)
async def measure(request: Request, body: MeasureRequest) -> Any:
    """Start measuring a model. 409 while another run is going, or until the person confirms
    an outside model (`confirm_outside`, with what may go to it); 429 past three a day."""
    person = await _person(request)
    state = _state(request)
    install = await config.memory_settings(state.store)
    if not install.enabled or install.suggestions == "off":
        raise _problem(403, "The owner of this Workbench has turned learning off.")
    run = measurer(request)
    async with run.lock:
        return await _start(request, person, body, run)


async def _start(request: Request, person: Person, body: MeasureRequest, run: Measurer) -> Any:
    state = _state(request)
    if run.current is not None:
        return JSONResponse(
            {
                "detail": {
                    "code": "busy",
                    "message": "Another model is being measured. Try again when it ends.",
                    "measuring": _run_view(run.current, person),
                }
            },
            status_code=409,
        )
    listed, problem = await _listing(state.hub)
    found = next((m for m in listed if m["id"] == body.model), None)
    if found is None:
        raise _problem(
            404 if problem is None else 502,
            problem or "Eugene does not list this model, so it cannot be measured.",
        )
    outside = found["locality"] != "local"
    requests = memory_measure.requests_for(found["tools"])
    if outside and not body.confirmOutside:
        return JSONResponse(
            {
                "detail": {
                    "code": "confirm_outside",
                    "message": (
                        f"About {requests} requests and up to {requests * TOKENS_PER_REQUEST:,} "
                        "tokens of made-up messages go to "
                        f"{found['provider'] or 'an outside service'}, which charges for them. "
                        "Nothing of yours is sent."
                    ),
                    "requests": requests,
                    "tokens": requests * TOKENS_PER_REQUEST,
                    "provider": found["provider"],
                }
            },
            status_code=409,
        )
    if run.left_today(person.sub, time.time()) <= 0:
        raise _problem(
            429,
            f"No more measurements can start today: {RUNS_PER_DAY} a person and "
            f"{INSTALL_RUNS_PER_DAY} for this Workbench. Try again tomorrow.",
        )
    started = run.start(person, body.model, tools=found["tools"], outside=outside)
    return {"measuring": _run_view(started, person)}


@router.delete("/api/memory/measurements/current", status_code=status.HTTP_204_NO_CONTENT)
async def cancel(request: Request) -> Response:
    """Stop the run in progress: its starter, or the owner."""
    person = await _person(request)
    run = measurer(request)
    current = run.current
    if current is None:
        return Response(status_code=204)
    if current.owner != person.sub and not person.is_owner:
        raise _problem(403, "Only the person who started a measurement, or the owner, can stop it.")
    if current.task is not None:
        current.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await current.task
    return Response(status_code=204)
