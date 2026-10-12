"""Sorting the passages a search found, with a result sorter (MR6b, design `workbench-sorting.md`).

A result sorter (a reranker) reads a question and a passage together and says how well the
passage answers it. The search by words and by meaning finds passages by looking at each side
alone, so what a sorter adds is a second look at the best of them. Measured on MR5's 60
questions and 2,810 passages: the best 10 of each way, each sent as its headings, a new line
and its words, raised recall@5 from 0.600 to 0.750 (Qwen3-Reranker), and the same sorter given
the words alone changed nothing. This module is the sorter's side of that: which one, whether it
is quick enough, the request, and what is told of it. `search.Retriever` chooses the passages to
send and puts them in the order that comes back.

**Which sorter** (`choose`, the install's `documentsSortModel`): `auto`, `off`, or the id of one
model. `auto` is the first model the gateway lists to Workbench's own key with the `rerank` surface
that runs **on the install's own machines** and is running now. **A sorter that runs outside the
machines is never chosen by itself**: the passages of a person's documents would leave them. It is
used only when the owner names it and has allowed the text of documents to go outside (the same
allowance as an outside search model, `documentsExternalEmbedding`: MR5 call 1).

**Only when quick enough** (`documentsSortSeconds`, 3). A sort adds its time to every answer, so it
runs only when the time it is expected to take is within what the owner allows: the tokens to be
read (each passage's own count, `Hit.tokens_est`, and the question once for each passage) over how
many tokens a second this sorter reads. That rate is measured, never assumed: **a decaying average
of Workbench's own recent sorts** (`Speed`), and, before there is any, **a two-passage probe**, made
when the sorter first appears. A rate that is older than `RATE_MAX_AGE` is used as it stands and
measured again behind the search, so a sorter that was slow for a moment is not left off for ever.
Over the budget the passages stay in the order the search found them, and the report says why
with the numbers: the rate, the tokens, the seconds expected and the seconds allowed.

**A sorter that fails never fails a search.** It may be stopped, be held back by the gateway for a
moment after it failed (a 503 that asks for a wait: said as that, and not as a sorter that is not
running), refuse the request (a passage longer than it reads), take more than twice the budget,
or answer with something that is not a score for each passage. The search goes on in the order it
found, and what the sorter said is kept (`SortReport`), as a way of searching that cannot run is
(`search.SignalUnavailable`): in words, beginning "Not sorted", naming what was observed. A sort
that was given up on counts as a slow one, so the next question is not made to wait the same time
again.

What a person is told of the sorter (the header of the Documents screen, Test a question, the two
settings) is read from a live look at the gateway and at what was measured, never from what was
true when something last ran. A sorter's scores are 0 to 1, of a scale that means nothing beside
another sorter's, and are never shown: Workbench shows the place it gave a passage.

Logs say counts, timings and causes: never a document's name or its words.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .. import token_count
from .._generated.models import ConfigFieldStatus, ConfigFieldStatusLevel
from ..hub import HubError, Reranked
from . import embedders
from .embedders import SearchModel
from .meaning import Probe, engine_words
from .search import Sheet, Sorted, SortReport

log = logging.getLogger(__name__)

#: The surface a result sorter has in the gateway's list of models.
SURFACE = "rerank"
AUTO = "auto"
OFF = "off"

#: How long a look at the gateway's list of models is kept, a failed one, and the most it may take.
PROBE_TTL = 10.0
PROBE_ERROR_TTL = 3.0
PROBE_TIMEOUT = 8.0
#: How much of what was measured before is kept when another sort is measured: each older
#: measurement counts this much less than the next one. Three sorts hold two thirds of the speed.
DECAY = 0.7
#: A measurement older than this (seconds) is used as it stands and measured again.
RATE_MAX_AGE = 600.0
#: A sorter that could not be measured is asked again no sooner than this.
MEASURE_RETRY = 10.0
#: A sort that took no time at all still took this long: the speed is never infinite.
MIN_SECONDS = 0.001

#: What a sort is when the screens say how long it takes before any question is asked: the
#: passages the best ten of both ways usually come to (the median of 60 questions was 18), about
#: 300 tokens each (a passage is cut to about that), and a short question once for each.
TYPICAL_PASSAGES = 18
TYPICAL_PASSAGE_TOKENS = 300
TYPICAL_QUESTION_TOKENS = 20

NOT_SORTED = "Not sorted: "
LEFT = "so the passages are in the order the search found them"
#: Where Sort time is changed, in the words a person is told it in.
RAISE_IT = "Raise Sort time in Workbench's settings (in Eugene's console) to sort anyway."
#: Not worth saying beside every answer: the sorter was never in play.
QUIET_CODES = ("off", "none_set_up")

#: What the probe asks a sorter, in the shape of a real sort: two passages of the size of a
#: passage, each with its headings, and a question that the first answers and the second does not.
PROBE_QUESTION = "How often should the seedlings in the greenhouse be watered?"
PROBE_PASSAGES: tuple[tuple[str, str], ...] = (
    (
        "Greenhouse notes > Watering",
        "Seedlings in the greenhouse are watered every second morning, before the sun reaches "
        "the glass, so that the leaves are dry by noon and the soil is cool when the heat comes. "
        "Each tray is given about a cupful from a can with a fine rose, poured slowly at the edge "
        "so that the seeds are not washed out of their rows. In a hot week the trays by the south "
        "wall dry faster than the rest and are given a second, lighter watering in the evening. "
        "The tap water is left to stand in a barrel overnight to take the chill off it, and the "
        "barrel is scrubbed and refilled every month. A tray that stays wet for two days running "
        "is moved to the shelf by the door, where the air moves, and watered less until its "
        "surface has dried. Water left in the saucers is tipped out after an hour.",
    ),
    (
        "Greenhouse notes > Winter heating",
        "Overnight the greenhouse is kept above five degrees by a paraffin heater at the north "
        "end and a row of bricks along the back wall that hold the warmth of the day. The heater "
        "is filled each afternoon and its wick is trimmed on Sundays; a spare wick is kept in the "
        "drawer under the bench. On a night of hard frost the cold frames are covered with "
        "sacking and the doors are taped at the edges. The thermometer by the door is read at "
        "six in the morning and the lowest figure of the night is written on the clipboard with "
        "the date, so that a run of cold nights can be seen at a glance and the order for "
        "paraffin can be made in good time.",
    ),
)


class Gateway(Protocol):
    """What sorting needs of the gateway: `hub.Hub` is one."""

    async def models(self) -> dict[str, Any]: ...

    async def rerank(
        self, model: str, query: str, documents: Sequence[str], *, seconds: float = 20.0
    ) -> Reranked: ...


class Settings(Protocol):
    """What sorting needs of the install's settings (`config.DocumentSettings`)."""

    @property
    def sort_model(self) -> str: ...
    @property
    def sort_seconds(self) -> float: ...
    @property
    def external_embedding(self) -> bool: ...


SettingsReader = Callable[[], Awaitable[Settings]]


# --------------------------------------------------------------------------- #
# how things are said
# --------------------------------------------------------------------------- #


def seconds_words(seconds: float) -> str:
    """A time as a person reads it: `7 s`, `6.9 s`, `0.45 s`; never more figures than it has."""
    places = 0 if seconds >= 10 else 1 if seconds >= 1 else 2
    text = f"{seconds:.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text} s"


def rate_words(rate: float) -> str:
    """A speed as a person reads it, to two figures: `about 820 tokens a second`."""
    if rate >= 100:
        rate = round(rate, 2 - len(str(int(rate))))
    return f"about {rate:,.0f} tokens a second"


def over_budget_words(
    model: str, rate: float, passages: int, tokens: int, expected: float, budget: float
) -> str:
    """Why the passages of one search were not sorted: the sorter is not quick enough for the time
    allowed, with the numbers it was decided by."""
    return (
        f"{NOT_SORTED}{model} sorts {rate_words(rate)} here, so sorting these {passages} passages "
        f"({tokens:,} tokens) would take about {seconds_words(expected)}, and Sort time is "
        f"{seconds_words(budget)}. {RAISE_IT}"
    )


def typical_words(model: str, rate: float, expected: float, budget: float) -> str:
    """The same, said for a sort of the usual size, before any question is asked."""
    return (
        f"{NOT_SORTED}{model} sorts {rate_words(rate)} here, so sorting the {TYPICAL_PASSAGES} "
        f"passages a question usually finds (about {TYPICAL_PASSAGE_TOKENS} tokens each) would add "
        f"about {seconds_words(expected)} to each answer, and Sort time is "
        f"{seconds_words(budget)}. {RAISE_IT}"
    )


def sort_tokens(sheets: Sequence[Sheet], question: str) -> int:
    """What reading these passages is worth, in tokens: what each passage already carries (its
    own count), and the question once for each passage, which is how a sorter reads them."""
    return sum(s.tokens for s in sheets) + token_count.estimate(question) * len(sheets)


def typical_tokens() -> int:
    return TYPICAL_PASSAGES * (TYPICAL_PASSAGE_TOKENS + TYPICAL_QUESTION_TOKENS)


def probe_sheets() -> list[Sheet]:
    """The two passages the probe sends: each as a real sort sends one, worth what its words are."""
    return [
        Sheet(embedders.passage_input(embedders.PLAIN, heading, text), token_count.estimate(text))
        for heading, text in PROBE_PASSAGES
    ]


def problem(exc: HubError, model: str, budget: float) -> tuple[str, str]:
    """A failure of the sorter or of the gateway on the way to it, as `(code, words)`: what was
    observed, and what to do. Every one begins "Not sorted", which is what the passages are
    while it lasts."""
    said = exc.words or exc.message
    if exc.status == 429:
        return "limited", (
            f"{NOT_SORTED}this Workbench's key is at its limit ({said}), {LEFT}. The owner can "
            "raise the key's limits in Eugene's console."
        )
    if exc.kind == "key":
        return "key", f"{NOT_SORTED}{exc.message}"
    if exc.kind in ("unreachable", "setup"):
        return "unreachable", f"{NOT_SORTED}{exc.message}"
    if exc.kind == "timeout":
        return "slow", (
            f"{NOT_SORTED}{model} did not answer within {seconds_words(2 * budget)} (twice Sort "
            f"time, {seconds_words(budget)}), {LEFT}."
        )
    if exc.kind == "unusable":
        return (
            "unusable",
            f"{NOT_SORTED}{exc.message} The passages are in the order the search found them.",
        )
    if exc.status == 503 and exc.retry_after is not None:
        # The gateway is holding requests back for a moment and says for how long: a backend
        # that failed is cooling down, or its recovery is being tried (measured, MR6b acceptance:
        # the next second's sorts after a sorter was started again). The sorter is running or has
        # just been started, so it is not "not running", and there is nothing for a person to
        # start. Said as what was observed, in the gateway's words.
        return "error", (
            f"{NOT_SORTED}Eugene's gateway asked Workbench to wait "
            f"{seconds_words(exc.retry_after)} before asking the sorter {model} again ({said}), "
            f"{LEFT}."
        )
    if exc.status in (503, 404):
        return "not_running", (
            f"{NOT_SORTED}the sorter {model} is not running. Start it in Eugene's console "
            "under Backends."
        )
    if exc.status == 400:
        return "refused", (
            f"{NOT_SORTED}the sorter {model} refused the request ({engine_words(exc)}), {LEFT}."
        )
    return "error", f"{NOT_SORTED}the sorter {model} failed ({engine_words(exc)}), {LEFT}."


def listing_problem(exc: HubError) -> tuple[str, str]:
    """The gateway's list of models could not be had, as `(code, words)`."""
    said = exc.words or exc.message
    if exc.status == 429:
        return "limited", (
            f"{NOT_SORTED}this Workbench's key is at its limit ({said}), so Workbench could not "
            "look for a result sorter. The owner can raise the key's limits in Eugene's console."
        )
    if exc.kind in ("key", "unreachable", "setup", "timeout", "unusable"):
        code = {"timeout": "slow", "setup": "unreachable"}.get(exc.kind, exc.kind)
        return code, f"{NOT_SORTED}{exc.message}"
    return "error", f"{NOT_SORTED}Eugene's gateway could not list its models ({said})."


# --------------------------------------------------------------------------- #
# which sorter
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Choice:
    """Which sorter is used now, or why none."""

    #: The sorter to use, or None when none can be used now.
    model: SearchModel | None
    #: `ok`, or why not: `off`, `none_set_up`, `not_running`, `not_listed`, `not_sorter`,
    #: `outside` (a sorter outside the machines, not allowed), `outside_only`; or what the
    #: gateway's list could not be had as (`listing_problem`'s codes).
    code: str
    #: The setting as it is: `auto`, `off`, or the id the owner named.
    setting: str
    #: The sorter the words are about, when there is one even though it cannot be used.
    named: str | None = None
    #: Sorters that run outside the machines and were left out, by id.
    outside: tuple[str, ...] = ()
    #: In words a person reads, when `model` is None.
    message: str | None = None

    @property
    def automatic(self) -> bool:
        return self.setting == AUTO

    @property
    def quiet(self) -> bool:
        return self.code in QUIET_CODES


def choose(
    models: Sequence[SearchModel],
    listed: Collection[str],
    setting: str,
    allow_external: bool,
) -> Choice:
    """The sorter to use. `setting` is `auto`, `off` or an id; `models` are the sorters the
    gateway lists, `listed` every model it lists, and `allow_external` whether the owner lets the
    text of documents go to one outside the machines (which `auto` never picks, whatever it is)."""
    setting = (setting or AUTO).strip() or AUTO
    if setting.casefold() == OFF:
        return Choice(
            None,
            "off",
            OFF,
            message=f"{NOT_SORTED}sorting is turned off (Result sorter is set to off).",
        )
    outside = tuple(m.id for m in models if not m.local)
    if setting.casefold() == AUTO:
        local = [m for m in models if m.local]
        ready = [m for m in local if m.ready]
        if ready:
            return Choice(ready[0], "ok", AUTO, outside=outside)
        if local:
            name = local[0].id
            return Choice(
                None,
                "not_running",
                AUTO,
                name,
                outside,
                f"{NOT_SORTED}the sorter {name} is not running. Start it in Eugene's console "
                "under Backends.",
            )
        if outside:
            return Choice(
                None,
                "outside_only",
                AUTO,
                None,
                outside,
                f"{NOT_SORTED}the only result sorters Eugene lists run outside your machines "
                f"({', '.join(outside[:3])}{', ...' if len(outside) > 3 else ''}), and Workbench "
                "does not send the text of your documents there unless the owner names one in "
                "Result sorter and allows it.",
            )
        return Choice(
            None, "none_set_up", AUTO, None, outside, f"{NOT_SORTED}no result sorter is set up."
        )
    found = next((m for m in models if m.id == setting), None)
    if found is None:
        if setting in listed:
            return Choice(
                None,
                "not_sorter",
                setting,
                setting,
                outside,
                f"{NOT_SORTED}{setting} is not a result sorter. Choose one in the Result sorter "
                "setting in Eugene's console.",
            )
        return Choice(
            None,
            "not_listed",
            setting,
            setting,
            outside,
            f"{NOT_SORTED}Eugene lists no result sorter named {setting}. Check the Result sorter "
            "setting in Eugene's console.",
        )
    if not found.local and not allow_external:
        return Choice(
            None,
            "outside",
            setting,
            setting,
            outside,
            f"{NOT_SORTED}{setting} runs {embedders.where_words(found)}, outside your machines, "
            "and the owner has not allowed sending the text of your documents there.",
        )
    if not found.ready:
        return Choice(
            None,
            "not_running",
            setting,
            setting,
            outside,
            f"{NOT_SORTED}the sorter {setting} is not running. Start it in Eugene's console "
            "under Backends.",
        )
    return Choice(found, "ok", setting, outside=outside)


@dataclass(frozen=True)
class Resolved:
    """Which sorter is in use now, or why none."""

    choice: Choice
    #: The look at the gateway's list of models it was chosen from; None when the sorter is off,
    #: which asks the gateway nothing.
    probe: Probe | None = None

    @property
    def model(self) -> SearchModel | None:
        return self.choice.model


# --------------------------------------------------------------------------- #
# how fast a sorter reads
# --------------------------------------------------------------------------- #


class Speed:
    """How fast one sorter reads, in tokens a second: a decaying average of what its recent
    sorts measured. Every measurement is its tokens over its seconds, and the ones before it
    count `DECAY` as much as they did, so a big sort says more than a small one (it is tokens over
    seconds of all of them, not an average of speeds), and what was true a while ago fades."""

    def __init__(self, *, decay: float = DECAY) -> None:
        self._decay = decay
        self._tokens = 0.0
        self._seconds = 0.0
        #: When it was last measured (by the clock the caller keeps), and how many of the
        #: measurements were sorts, and not the probe.
        self.at: float | None = None
        self.sorts = 0

    def record(self, tokens: int, seconds: float, at: float, *, sort: bool = True) -> None:
        """One measurement: `tokens` read in `seconds`. A sort given up on is one too: it took at
        least as long as it was given, and the next question must not be promised better."""
        if tokens <= 0:
            return
        self._tokens = self._tokens * self._decay + tokens
        self._seconds = self._seconds * self._decay + max(seconds, MIN_SECONDS)
        self.at = at
        if sort:
            self.sorts += 1

    def per_second(self) -> float | None:
        """Tokens a second, or None until something has been measured."""
        if self._tokens <= 0 or self._seconds <= 0:
            return None
        return self._tokens / self._seconds

    @property
    def source(self) -> str | None:
        """`sorts` once a real sort has been measured, `probe` while it is only the probe's."""
        if self.at is None:
            return None
        return "sorts" if self.sorts else "probe"


@dataclass(frozen=True)
class Failure:
    """Why a sorter could not be measured, and when it was found out."""

    code: str
    message: str
    at: float


# --------------------------------------------------------------------------- #
# what is said of it
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Standing:
    """How passages are sorted right now, from a live look: which sorter, how fast it reads, and
    whether a sort of the usual size is quick enough. The Documents screen's header and the two
    settings say it."""

    choice: Choice
    #: `ok` (a sort of the usual size is quick enough), `over_budget`, `measuring` (the sorter has
    #: not been measured yet), or why no sorter is in use (`Choice.code`, or `problem`'s).
    code: str
    message: str | None
    budget: float
    allow_external: bool
    rate: float | None = None
    #: `probe` or `sorts`: what the rate was measured from.
    rate_from: str | None = None

    @property
    def sorting(self) -> bool:
        return self.code == "ok"

    @property
    def model(self) -> str | None:
        return self.choice.model.id if self.choice.model is not None else None

    @property
    def expected(self) -> float | None:
        """Seconds a sort of the usual size takes at this sorter's rate."""
        return typical_tokens() / self.rate if self.rate else None

    def view(self) -> dict[str, Any]:
        found = self.choice.model
        return {
            "setting": self.choice.setting,
            "automatic": self.choice.automatic,
            # Whether passages are sorted: a sorter is in use and a sort of the usual size is
            # quick enough. A question that finds fewer passages may be sorted when this is not.
            "sorting": self.sorting,
            "code": self.code,
            "message": self.message,
            "quiet": self.code in QUIET_CODES,
            "model": self.model,
            "named": self.choice.named,
            "label": (
                f"In use: {found.id}" + (" (automatic)" if self.choice.automatic else "")
                if found is not None
                else None
            ),
            "locality": found.locality if found is not None else None,
            "runsOn": found.who if found is not None else None,
            "allowExternal": self.allow_external,
            "outside": list(self.choice.outside),
            "budgetSeconds": self.budget,
            "ratePerSecond": round(self.rate, 1) if self.rate is not None else None,
            "rateFrom": self.rate_from,
            "typical": {
                "passages": TYPICAL_PASSAGES,
                "passageTokens": TYPICAL_PASSAGE_TOKENS,
                "tokens": typical_tokens(),
                "seconds": round(self.expected, 2) if self.expected is not None else None,
            },
        }


@dataclass(frozen=True)
class Fields:
    """What the two settings come to now, for the config page (settings never lie)."""

    #: The sorters Eugene lists, as suggestions for the Result sorter setting.
    ids: list[str]
    model: ConfigFieldStatus
    seconds: ConfigFieldStatus


# --------------------------------------------------------------------------- #
# the whole
# --------------------------------------------------------------------------- #


class Sorting:
    """Sorting for every person: choosing the sorter, measuring it, asking it. One per Workbench.
    `sort` is what `search.Retriever` asks."""

    def __init__(
        self,
        gateway: Gateway,
        settings: SettingsReader,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.gateway = gateway
        self._settings = settings
        self._clock = clock
        self._probe: Probe | None = None
        self._probe_lock = asyncio.Lock()
        #: How fast each sorter reads, and why one could not be measured, by its id.
        self._speeds: dict[str, Speed] = {}
        self._failed: dict[str, Failure] = {}
        self._measuring: dict[str, asyncio.Task[None]] = {}
        #: The sorts begun, for the tests: how many passages each was sent.
        self.asked: deque[int] = deque(maxlen=1000)

    async def aclose(self) -> None:
        tasks = list(self._measuring.values())
        self._measuring.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # --- the gateway: which sorters there are ----------------------------------

    async def probe(self) -> Probe:
        """The gateway's models, as a look at them: cached for `PROBE_TTL` seconds (a failed look
        for a few), so a screen that asks every second asks the gateway once in ten."""

        def good(found: Probe | None) -> bool:
            if found is None:
                return False
            return self._clock() - found.at < (PROBE_ERROR_TTL if found.error else PROBE_TTL)

        if self._probe is not None and good(self._probe):
            return self._probe
        async with self._probe_lock:
            if self._probe is not None and good(self._probe):
                return self._probe
            try:
                listing = await asyncio.wait_for(self.gateway.models(), PROBE_TIMEOUT)
                models, listed = embedders.models_with(listing, SURFACE)
            except HubError as exc:
                found = Probe(self._clock(), error=exc)
            except TimeoutError:
                found = Probe(
                    self._clock(),
                    error=HubError(
                        f"Eugene's gateway did not list its models within {PROBE_TIMEOUT:.0f} "
                        "seconds.",
                        status=504,
                        kind="timeout",
                    ),
                )
            except (ValueError, AttributeError, TypeError):
                # Not JSON, or not a list of models: said as what it is, and never a failure.
                found = Probe(
                    self._clock(),
                    error=HubError(
                        "Eugene's gateway answered the list of its models with something "
                        "Workbench cannot read.",
                        status=502,
                        kind="unusable",
                    ),
                )
            else:
                found = Probe(self._clock(), tuple(models), frozenset(listed))
            self._probe = found
            return found

    def forget(self) -> None:
        """The last look at the gateway is not trusted any more (a sort just failed)."""
        self._probe = None

    async def resolve(self) -> Resolved:
        """The sorter in use now (`choose`), from a live look at the gateway. A sorter that is off
        asks the gateway nothing."""
        settings = await self._settings()
        setting = (settings.sort_model or AUTO).strip() or AUTO
        if setting.casefold() == OFF:
            return Resolved(choose([], (), setting, False))
        probe = await self.probe()
        if probe.error is not None:
            code, words = listing_problem(probe.error)
            return Resolved(Choice(None, code, setting, None, (), words), probe)
        return Resolved(
            choose(probe.models, probe.listed, setting, settings.external_embedding), probe
        )

    # --- how fast it reads ---------------------------------------------------

    def rate_of(self, model: str) -> float | None:
        """Tokens a second this sorter reads at, from what was measured; None before that."""
        speed = self._speeds.get(model)
        return speed.per_second() if speed is not None else None

    def _stale(self, model: str) -> bool:
        speed = self._speeds.get(model)
        return (
            speed is not None and speed.at is not None and self._clock() - speed.at > RATE_MAX_AGE
        )

    def _refresh(self, model: SearchModel, budget: float) -> None:
        """A speed that is old is measured again behind whoever asked: not while the last try
        failed a moment ago, or a sorter that does not answer would be asked at every look."""
        failed = self._failed.get(model.id)
        if failed is not None and self._clock() - failed.at < MEASURE_RETRY:
            return
        if self._stale(model.id):
            self._measure_soon(model, budget)

    def _measure_soon(self, model: SearchModel, budget: float) -> asyncio.Task[None]:
        """Measure this sorter with the probe, one at a time: the task that is doing it, or a new
        one. A search waits for it (it has nothing to price a sort with); a screen does not."""
        running = self._measuring.get(model.id)
        if running is not None and not running.done():
            return running
        task = asyncio.create_task(self._measure(model, budget), name="measure-sorter")
        self._measuring[model.id] = task
        task.add_done_callback(functools.partial(self._measured_done, model.id))
        return task

    def _measured_done(self, key: str, done: asyncio.Task[None]) -> None:
        if self._measuring.get(key) is done:
            del self._measuring[key]
        if not done.cancelled() and (exc := done.exception()) is not None:
            log.warning("measuring a result sorter failed in Workbench itself: %r", exc)

    async def _measure(self, model: SearchModel, budget: float) -> None:
        """The probe: two passages, and how long the sorter took. What is kept is the sorter's
        own answer or its failure, never a guess. A measurement made again (the old one is stale)
        replaces what was measured, for what was measured a while ago says nothing of now."""
        sheets = probe_sheets()
        limit = 2 * budget
        started = self._clock()
        try:
            await asyncio.wait_for(
                self.gateway.rerank(
                    model.id, PROBE_QUESTION, [s.text for s in sheets], seconds=limit
                ),
                limit,
            )
        except (TimeoutError, HubError) as exc:
            code, words = problem(self._error_of(exc, "the probe took too long"), model.id, budget)
            log.warning("measuring how fast a result sorter reads failed (%s)", code)
            self._failed[model.id] = Failure(code, words, self._clock())
            self.forget()
            return
        now = self._clock()
        speed = Speed()
        speed.record(sort_tokens(sheets, PROBE_QUESTION), now - started, now, sort=False)
        self._speeds[model.id] = speed
        self._failed.pop(model.id, None)

    @staticmethod
    def _error_of(exc: TimeoutError | HubError, what: str) -> HubError:
        """A request that was given up on (ours) is a timeout, said as the gateway's own is."""
        if isinstance(exc, HubError):
            return exc
        return HubError(what, status=504, kind="timeout")

    async def _priced(
        self, model: SearchModel, budget: float
    ) -> tuple[float | None, Failure | None]:
        """The tokens a second this sorter reads at, measuring it first when it never was (a search
        has nothing to price a sort with otherwise), or the failure that stopped that."""
        rate = self.rate_of(model.id)
        if rate is not None:
            self._refresh(model, budget)  # used as it stands; measured again behind it when old
            return rate, None
        failed = self._failed.get(model.id)
        if failed is not None and self._clock() - failed.at < MEASURE_RETRY:
            return None, failed
        await asyncio.wait({self._measure_soon(model, budget)})
        rate = self.rate_of(model.id)
        if rate is not None:
            return rate, None
        return None, self._failed.get(model.id) or Failure(
            "error",
            f"{NOT_SORTED}how fast {model.id} sorts could not be measured, {LEFT}.",
            self._clock(),
        )

    # --- the sort ------------------------------------------------------------

    async def sort(self, query: str, sheets: Sequence[Sheet]) -> Sorted:
        """Put these passages in the order that answers `query` best, if there is a sorter to do
        it and it is quick enough. What was decided, and why, is the report; the order is None
        when the passages were not sorted. A sorter that fails is a report, never an exception."""
        settings = await self._settings()
        budget = float(settings.sort_seconds)
        choice = (await self.resolve()).choice
        report = SortReport(setting=choice.setting, passages=len(sheets), budget=budget)
        model = choice.model
        if model is None:
            return Sorted(
                None,
                replace(
                    report,
                    model=choice.named,
                    code=choice.code,
                    message=choice.message,
                    quiet=choice.quiet,
                ),
            )
        tokens = sort_tokens(sheets, query)
        report = replace(report, model=model.id, tokens=tokens)
        rate, failed = await self._priced(model, budget)
        if rate is None:
            assert failed is not None
            return Sorted(None, replace(report, code=failed.code, message=failed.message))
        expected = tokens / rate
        report = replace(report, rate=rate, expected=expected)
        if expected > budget:
            return Sorted(
                None,
                replace(
                    report,
                    code="over_budget",
                    message=over_budget_words(
                        model.id, rate, len(sheets), tokens, expected, budget
                    ),
                ),
            )
        limit = 2 * budget
        started = self._clock()
        self.asked.append(len(sheets))
        try:
            answer = await asyncio.wait_for(
                self.gateway.rerank(model.id, query, [s.text for s in sheets], seconds=limit),
                limit,
            )
        except (TimeoutError, HubError) as exc:
            code, words = problem(self._error_of(exc, "the sort took too long"), model.id, budget)
            log.warning("sorting the passages of a search failed (%s)", code)
            if code == "slow":
                # Given up on: it took at least this long, and the next question is not promised
                # better than that (a rate that said "quick enough" has just been wrong).
                self._record(model.id, tokens, limit)
            self.forget()
            return Sorted(None, replace(report, code=code, message=words))
        took = self._clock() - started
        self._record(model.id, tokens, took)
        return Sorted(tuple(answer.order), replace(report, sorted=True, seconds=took))

    def _record(self, model: str, tokens: int, seconds: float) -> None:
        self._speeds.setdefault(model, Speed()).record(tokens, seconds, self._clock())

    # --- what is said of it ----------------------------------------------------

    async def standing(self) -> Standing:
        """How passages are sorted now, from a live look (the gateway's models, ten seconds old at
        most) and what was measured. A sorter that has not been measured is measured behind this
        look, which never waits for it: it says `measuring` until the numbers are there."""
        settings = await self._settings()
        budget = float(settings.sort_seconds)
        choice = (await self.resolve()).choice
        allow = settings.external_embedding

        def stand(code: str, message: str | None, **more: Any) -> Standing:
            return Standing(choice, code, message, budget, allow, **more)

        model = choice.model
        if model is None:
            return stand(choice.code, choice.message)
        rate = self.rate_of(model.id)
        if rate is None:
            failed = self._failed.get(model.id)
            if failed is not None and self._clock() - failed.at < MEASURE_RETRY:
                return stand(failed.code, failed.message)
            self._measure_soon(model, budget)
            return stand("measuring", None)
        self._refresh(model, budget)
        source = self._speeds[model.id].source
        expected = typical_tokens() / rate
        if expected > budget:
            return stand(
                "over_budget",
                typical_words(model.id, rate, expected, budget),
                rate=rate,
                rate_from=source,
            )
        return stand("ok", None, rate=rate, rate_from=source)

    async def fields(self) -> Fields:
        """What the two settings come to now (settings never lie): which sorter the first is, or
        why none; and what the second does with the sorter's speed."""
        standing = await self.standing()
        probe = await self.probe() if standing.choice.code != "off" else self._probe
        ids = [m.id for m in probe.models] if probe is not None else []
        info, warning = ConfigFieldStatusLevel.info, ConfigFieldStatusLevel.warning
        choice, found = standing.choice, standing.choice.model
        if found is not None:
            tail = " (automatic)" if choice.automatic else ""
            model = ConfigFieldStatus(level=info, text=f"In use: {found.id}{tail}.")
        elif choice.code == "off":
            model = ConfigFieldStatus(
                level=info, text="Off: passages are left in the order the search found them."
            )
        else:
            model = ConfigFieldStatus(
                level=info if choice.quiet else warning,
                text=choice.message or f"{NOT_SORTED}no sorter is in use.",
            )
        if found is None:
            seconds = ConfigFieldStatus(
                level=info, text="Nothing is sorted now, so this changes nothing yet."
            )
        elif standing.code == "measuring":
            seconds = ConfigFieldStatus(
                level=info,
                text=f"Measuring how fast {found.id} sorts, to tell whether it is quick enough.",
            )
        elif standing.rate is None:
            seconds = ConfigFieldStatus(
                level=warning,
                text=standing.message or f"{NOT_SORTED}how fast {found.id} sorts is not known.",
            )
        else:
            verdict = (
                "within this, so passages are sorted"
                if standing.sorting
                else "over this, so passages are not sorted"
            )
            seconds = ConfigFieldStatus(
                level=info if standing.sorting else warning,
                text=(
                    f"{found.id} sorts {rate_words(standing.rate)} here: {TYPICAL_PASSAGES} "
                    f"passages of about {TYPICAL_PASSAGE_TOKENS} tokens take about "
                    f"{seconds_words(standing.expected or 0.0)}, {verdict}."
                ),
            )
        return Fields(ids, model, seconds)
