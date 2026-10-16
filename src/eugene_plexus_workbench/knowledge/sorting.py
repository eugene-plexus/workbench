"""Sorting the passages a search found, with a result sorter (MR6b, design `workbench-sorting.md`).

A result sorter (a reranker) reads a question and a passage together and says how well the
passage answers it. The search by words and by meaning finds passages by looking at each side
alone, so what a sorter adds is a second look at the best of them. Measured on MR5's 60
questions and 2,810 passages (written from the headings, which favours scoring that sees them,
so the gain is in-sample, and a live run on real questions is owed): the best 10 of each way,
each sent as its headings, a new line and its words, raised recall@5 from 0.600 to 0.750
(Qwen3-Reranker), while the same sorter given the words alone gained +0.050 on the same
passages, with an interval of -0.067 to +0.167 that does not exclude no gain at all. This module
is the sorter's side of that: which one, whether it is quick enough, the request, and what is told
of it. `search.Retriever` chooses the passages to send and puts them in the order that comes back.

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
of Workbench's own recent sorts** (`Speed`), and, before there is any, **a two-passage probe**.
**A search never waits for a measurement**: with no speed known, the question is answered in the
order the search found (`measuring`) and the probe is made behind it, one at a time, with a limit
of its own (twice Sort time, and never under `PROBE_MIN_LIMIT`). A probe that fails is remembered
(`Failure`) and made again after `MEASURE_RETRY` seconds, and after twice as long each time it fails
again, up to `RATE_MAX_AGE`. Over the budget the passages stay in the order the search found them,
and the report says why with the numbers: the rate, the tokens, the seconds expected and the
seconds allowed.

**When a speed is measured again.** Only when it is older than `RATE_MAX_AGE` and either most of it
comes from the probe, or the sorter is over budget (and so is never asked a real sort that would
show it has recovered). The probe is blended in with the same decay as a sort, never put in place of
what the sorts measured, and a speed from recent real sorts is left alone. What the speed is said to
come from follows the same share (`Speed.source`): `probe` while the quick tests read more than half
of the tokens behind it, `sorts` after, so a sorter that is only ever measured again by the probe
ends by saying so.

**A sorter that fails never fails a search.** It may be stopped, be held back by the gateway for a
moment after it failed (a 503 that asks for a wait: said as that, and not as a sorter that is not
running), refuse the request (a passage longer than it reads), take more than twice the budget,
or answer with something that is not a score for each passage. The search goes on in the order it
found, and what the sorter said is kept (`SortReport`), as a way of searching that cannot run is
(`search.SignalUnavailable`): in words, beginning "Not sorted", naming what was observed.

**A sorter that did not answer in time** (a sort given up on at twice the budget, or a probe that
was) is a state of its own (`GaveUp`), apart from the speed: a sorter that did not answer never
read at any speed, so none is said, and it is told as what it is: "did not answer within N s the
last time it was asked", and when it is asked again. A sorter that does not answer is stuck, not
slow, so the size of a question says nothing about whether it would be answered: while the give-up
stands no question is asked of it, whatever its size, and none is made to wait, unless Sort time
has been raised so that twice it is longer than the wait that was given up on (`GaveUp.holds`). A
sort that was given up on is not averaged into what the sorter did while it was well. It is asked
again by the probe, behind a search, after 10 s and then after longer each time it still does not
answer; one hang is one strike, however many questions were in the air when it happened.

What a person is told of the sorter (the header of the Documents screen, Test a question, the two
settings) is read from a live look at the gateway and at what was measured, never from what was
true when something last ran, and is one judgement (`Sorting._judge`) however it is asked: the
settings and the screen cannot disagree. A sorter's scores are 0 to 1, of a scale that means
nothing beside another sorter's, and are never shown: Workbench shows the place it gave a passage.

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
from .embedders import SearchModel, duration_words, seconds_words
from .listing import Listing, Probe, probe_of
from .meaning import engine_words, too_long
from .search import Sheet, Sorted, SortReport

log = logging.getLogger(__name__)

#: The surface a result sorter has in the gateway's list of models.
SURFACE = "rerank"
AUTO = "auto"
OFF = "off"

#: How much of what was measured before is kept when another sort is measured: each older
#: measurement counts this much less than the next one. Three sorts hold two thirds of the speed.
DECAY = 0.7
#: A speed older than this (seconds) is measured again, when the probe is all it comes from or the
#: sorter is over budget; and the most a sorter that could not be measured is left before it is
#: asked again.
RATE_MAX_AGE = 600.0
#: A sorter that could not be measured, or did not answer in time, is asked again no sooner than
#: this, and no sooner than twice as long each time that fails again (never over `RATE_MAX_AGE`).
MEASURE_RETRY = 10.0
#: The probe's own limit is twice Sort time and never less than this: a sorter that was just
#: started may need seconds to answer its first request, and the probe is made behind a search, so
#: nobody waits for it.
PROBE_MIN_LIMIT = 10.0
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


def measuring_words(model: str) -> str:
    """Why a question was not sorted while the sorter's speed is not known yet: the passages are
    in the order the search found them and the speed is being measured, behind this question."""
    return f"{NOT_SORTED}how fast {model} sorts is being measured now, {LEFT}."


def again_words(retry_in: float) -> str:
    """When a sorter that did not answer is asked again: `after 8 s`, `after about 9 minutes`."""
    if retry_in <= 0:
        return "Workbench is asking it again now."
    wait = seconds_words(retry_in) if retry_in < 60 else duration_words(retry_in)
    return f"Workbench asks it again after {wait}."


def gave_up_words(model: str, seconds: float, retry_in: float) -> str:
    """A sorter that did not answer within `seconds` the last time it was asked, as what it is:
    no speed (it never read at one), what was seen, what the passages are in meanwhile, and when
    it is asked again. It does not advise raising Sort time: a sorter that is not answering is
    not helped by a longer wait for it, and each later hang would cost more."""
    return (
        f"{NOT_SORTED}{model} did not answer within {seconds_words(seconds)} the last time it "
        f"was asked, {LEFT}. {again_words(retry_in)}"
    )


def reason_of(message: str | None) -> str:
    """A reason as a clause, for a sentence that gives it: `the sorter X is not running. Start ...`
    out of `Not sorted: the sorter X is not running. Start ...`."""
    return (message or "").removeprefix(NOT_SORTED)


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
    if exc.kind == "internal":
        return "error", f"{NOT_SORTED}{exc.message}, {LEFT}. See Workbench's log."
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
    if exc.status == 503:
        return "not_running", (
            f"{NOT_SORTED}the sorter {model} is not running. Start it in Eugene's console "
            "under Backends."
        )
    if exc.status == 404:
        # The gateway does not serve this model (it listed it a moment ago, or a stale list
        # did): said in its own words. Whether it is stopped, removed or renamed is not
        # something a 404 says, so it is not said.
        return "not_listed", (
            f"{NOT_SORTED}Eugene's gateway answered that it has no sorter {model} to ask "
            f"({said}), {LEFT}."
        )
    if exc.status == 400 or too_long(exc):
        spoke = engine_words(exc)
        if too_long(exc) or spoke != said.strip():
            # The sorter's own engine refused it (a passage longer than it reads), and the
            # gateway passed its words on.
            return "refused", (
                f"{NOT_SORTED}the sorter {model} refused the request ({spoke}), {LEFT}."
            )
        # A 400 that is not the engine's is the door's own: said as the gateway's, and not as
        # the sorter's (`documents[3]: ...`, a question the door will not take).
        return "refused", f"{NOT_SORTED}Eugene's gateway refused the request ({said}), {LEFT}."
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
        #: How many of those tokens the probe read, faded the same way as the rest.
        self._probed = 0.0
        #: When it was last measured, by the clock the caller keeps.
        self.at: float | None = None

    def record(self, tokens: int, seconds: float, at: float, *, sort: bool = True) -> None:
        """One measurement: `tokens` read in `seconds`, by a real sort or (`sort=False`) by the
        probe. A sort that was given up on is not one: it read nothing in that time (`GaveUp`)."""
        if tokens <= 0:
            return
        self._tokens = self._tokens * self._decay + tokens
        self._seconds = self._seconds * self._decay + max(seconds, MIN_SECONDS)
        self._probed = self._probed * self._decay + (0 if sort else tokens)
        self.at = at

    def per_second(self) -> float | None:
        """Tokens a second, or None until something has been measured."""
        if self._tokens <= 0 or self._seconds <= 0:
            return None
        return self._tokens / self._seconds

    @property
    def source(self) -> str | None:
        """What the speed is mostly made of: `probe` while the probe read more than half of the
        tokens behind it, `sorts` after (and at exactly half). A sorter that is over budget is asked
        no sort, so it is measured again by the probe alone and the probe's share only grows: the
        speed is then the probe's, and the screen must not go on saying it is the sorts'."""
        if self.at is None:
            return None
        return "probe" if self._probed * 2 > self._tokens else "sorts"


@dataclass(frozen=True)
class GaveUp:
    """The last time this sorter was asked it did not answer within `seconds`. That is not a speed
    (it never read at one), so it is kept apart from the speed and is told as what it is. Nor does
    it depend on what was asked: a sorter that does not answer is stuck, not slow, so a smaller
    question would not be answered either, and would wait its whole limit to find that out. While
    it stands, no question is asked of the sorter, whatever its size, unless Sort time was raised
    so that the wait a question would be given, twice Sort time, is longer than the one that was
    given up on."""

    seconds: float

    def holds(self, budget: float) -> bool:
        """Whether a question is still kept from the sorter at this Sort time: the wait it would
        be given (twice `budget`) is no longer than the one that was given up on."""
        return 2 * budget <= self.seconds


@dataclass(frozen=True)
class Failure:
    """Why a sorter could not be measured, or did not answer in time, and when it was found out:
    what is said of it until it is asked again. `tries` is how many times in a row it has not
    come to anything, which makes the wait before it is asked again longer each time."""

    code: str
    message: str
    at: float
    tries: int = 1
    #: Set when it did not answer in time, and kept (and carried over to a failure that came
    #: after it) until a sort or the probe is answered.
    gave_up: GaveUp | None = None

    def retry_at(self) -> float:
        """When it is asked again: `MEASURE_RETRY` seconds after, and twice as long each time it
        fails again, never over `RATE_MAX_AGE`."""
        wait = MEASURE_RETRY * 2.0 ** min(self.tries - 1, 30)
        return self.at + min(wait, RATE_MAX_AGE)

    def words(self, model: str, now: float) -> str:
        """What is told of it, at `now`: a sorter that did not answer says when it is asked
        again, and the rest say what was seen."""
        if self.code == "slow" and self.gave_up is not None:
            return gave_up_words(model, self.gave_up.seconds, self.retry_at() - now)
        return self.message


@dataclass(frozen=True)
class Verdict:
    """What is to be done with some passages at one sorter now, from what is known of it."""

    #: `ok` (quick enough: ask it), `over_budget`, `measuring` (its speed is not known yet),
    #: or why it is not asked: `slow` (it did not answer the last time it was asked) or the
    #: code of the failure that stands (`problem`'s).
    code: str
    #: Tokens a second it reads at, from what it was measured, and what that came from (`probe`
    #: or `sorts`); None until there is a measurement, and while it is only known that it did not
    #: answer.
    rate: float | None = None
    rate_from: str | None = None
    #: The seconds these passages are expected to take at that rate.
    expected: float | None = None
    #: In words, for a state whose sentence does not depend on the passages (a failure; a sorter
    #: that did not answer).
    message: str | None = None


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
    #: not been measured yet), `slow` (it did not answer the last time it was asked), or why no
    #: sorter is in use (`Choice.code`, or `problem`'s).
    code: str
    message: str | None
    budget: float
    allow_external: bool
    #: The tokens a second it was measured at, and what that is mostly made of: `probe` while the
    #: quick tests read more than half of the tokens behind it, `sorts` after (`Speed.source`).
    #: Both are None while it is only known that it did not answer: that is not a speed.
    rate: float | None = None
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
            # Said only while it is true: a sorter that is chosen and not in use (too slow, still
            # being measured, stopped) has no "In use" sentence, in this API as on the screen.
            "label": (
                f"In use: {found.id}" + (" (automatic)" if self.choice.automatic else "")
                if found is not None and self.sorting
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
        listing: Listing | None = None,
    ) -> None:
        self.gateway = gateway
        self._settings = settings
        self._clock = clock
        #: The gateway's list of models: one look, shared with search by meaning (`listing.py`).
        self.listing = listing if listing is not None else Listing(gateway, clock=clock)
        #: How fast each sorter reads, by its id; why it could not be measured, or did not answer
        #: in time (and when it is asked again); and how many times in a row it has not come to
        #: anything, which a sort that is answered puts right.
        self._speeds: dict[str, Speed] = {}
        self._failed: dict[str, Failure] = {}
        self._strikes: dict[str, int] = {}
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
        """The gateway's models, as a look at them: the one look that is kept and shared with
        search by meaning (`listing.Listing`, ten seconds, and a failed look for a few); of it,
        the result sorters."""
        return await self.listing.probe(SURFACE)

    def forget(self) -> None:
        """The last look at the gateway is not trusted any more (a sort just failed)."""
        self.listing.forget()

    async def resolve(self, settings: Settings | None = None) -> Choice:
        """The sorter in use now (`choose`), from a live look at the gateway. A sorter that is off
        asks the gateway nothing."""
        settings = settings or await self._settings()
        setting = (settings.sort_model or AUTO).strip() or AUTO
        if setting.casefold() == OFF:
            return choose([], (), setting, False)
        probe = await self.probe()
        if probe.error is not None:
            code, words = listing_problem(probe.error)
            return Choice(None, code, setting, None, (), words)
        return choose(probe.models, probe.listed, setting, settings.external_embedding)

    # --- how fast it reads ---------------------------------------------------

    def rate_of(self, model: str) -> float | None:
        """Tokens a second this sorter reads at, from what was measured; None before that."""
        speed = self._speeds.get(model)
        return speed.per_second() if speed is not None else None

    def _judge(self, model: SearchModel, tokens: int, budget: float) -> Verdict:
        """What to do with `tokens` of passages at this sorter now: from how fast it is known to
        read, and whether it did not answer the last time it was asked. The one judgement behind
        a question, the Documents screen and the two settings, so that they cannot disagree.
        Never waits: a speed that is not known is measured behind whoever asks (`measuring`), and
        one that is old is measured again behind them."""
        from ..person_context import catalog_only

        if not catalog_only.get():
            self._measure_if_due(model, budget)
        now = self._clock()
        failed = self._failed.get(model.id)
        if failed is not None and failed.gave_up is not None and failed.gave_up.holds(budget):
            # It did not answer the last time it was asked, and Sort time has not been raised
            # past that: whatever the passages weigh, it is not asked (a sorter that does not
            # answer is stuck, not slow). No speed is said (it never read at one), and no advice
            # to raise Sort time: a longer wait for a sorter that does not answer helps nobody.
            return Verdict(failed.code, message=failed.words(model.id, now))
        rate = self.rate_of(model.id)
        if rate is None:
            if failed is not None and now < failed.retry_at():
                return Verdict(failed.code, message=failed.words(model.id, now))
            return Verdict("measuring")
        source = self._speeds[model.id].source
        expected = tokens / rate
        if expected > budget:
            return Verdict("over_budget", rate, source, expected)
        return Verdict("ok", rate, source, expected)

    def _measure_if_due(self, model: SearchModel, budget: float) -> None:
        """Measure this sorter with the probe, behind whoever is asking, when it is time to: when
        nothing is known of its speed; when it did not answer and its wait is over; or when its
        speed is old and is mostly the probe's (`Speed.source`), or the sorter is over budget, so
        that no sort that would show it has recovered is ever asked of it. Not before its turn to
        be asked again after a failure (and one at a time: `_measure_soon`). A speed from recent
        real sorts is left alone, and a sorter that is quick enough is measured by the sorts it is
        asked."""
        now = self._clock()
        failed = self._failed.get(model.id)
        if failed is not None and now < failed.retry_at():
            return
        speed = self._speeds.get(model.id)
        rate = speed.per_second() if speed is not None else None
        if speed is None or rate is None or (failed is not None and failed.gave_up is not None):
            due = True
        else:
            assert speed.at is not None  # it has a rate, so it was measured
            old = now - speed.at > RATE_MAX_AGE
            due = old and (speed.source == "probe" or typical_tokens() / rate > budget)
        if due:
            self._measure_soon(model, budget)

    def _measure_soon(self, model: SearchModel, budget: float) -> asyncio.Task[None]:
        """Measure this sorter with the probe, one at a time: the task that is doing it, or a
        new one. Nobody waits for it: a search is answered in the order it found, and a screen
        says `measuring`, until the numbers are there."""
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

    async def _measure(self, model: SearchModel, budget: float) -> None:
        """The probe: two passages, and how long the sorter took, with a limit of its own (twice
        Sort time, and never under `PROBE_MIN_LIMIT`). What is kept is the sorter's own answer,
        blended into what is known with the same decay as a sort, or why there was none: any
        failure at all is kept, and the sorter is asked again later and later, never at every
        look. A probe that was not answered in time is learned from as a sort that was given up
        on is (`GaveUp`), so that the next question is not made to wait for it."""
        sheets = probe_sheets()
        tokens = sort_tokens(sheets, PROBE_QUESTION)
        limit = max(2 * budget, PROBE_MIN_LIMIT)
        started = self._clock()
        try:
            await asyncio.wait_for(
                self.gateway.rerank(
                    model.id, PROBE_QUESTION, [s.text for s in sheets], seconds=limit
                ),
                limit,
            )
            now = self._clock()
            self._speeds.setdefault(model.id, Speed()).record(
                tokens, now - started, now, sort=False
            )
        except Exception as exc:  # whatever it was, it is kept: never asked again at every look
            error = self._error_of(exc, "measuring how fast the sorter reads")
            code, words = problem(error, model.id, budget)
            log.warning(
                "measuring how fast a result sorter reads failed (%s)",
                code,
                exc_info=(type(exc), exc, exc.__traceback__) if error.kind == "internal" else None,
            )
            self._remember(
                model.id, code, words, GaveUp(limit) if code == "slow" else None, began=started
            )
            self.forget()
            return
        self._answered(model.id, probe=True)

    @staticmethod
    def _error_of(exc: Exception, doing: str) -> HubError:
        """What went wrong, as a `HubError`: a request that was given up on (ours) is a timeout,
        said as the gateway's own is, and anything else that is not the gateway's is a failure in
        Workbench itself."""
        if isinstance(exc, HubError):
            return exc
        if isinstance(exc, TimeoutError):
            return HubError(f"{doing} took too long", status=504, kind="timeout")
        return HubError(
            f"{doing} failed in Workbench itself ({type(exc).__name__})",
            status=500,
            kind="internal",
        )

    def _remember(
        self,
        model: str,
        code: str,
        words: str,
        gave_up: GaveUp | None = None,
        *,
        began: float | None = None,
    ) -> Failure:
        """Keep that a request of this sorter came to nothing: what was seen (`problem`'s code
        and words), and so when it is asked again, which is later each time it comes to nothing
        again. A request that was given up on is a `GaveUp`; a failure that is not that leaves
        what was known of one before it.

        **One strike per event, not per question.** `began` is when the request was begun, by the
        clock. One that began before the failure that is on record was in the air with it, so it is
        the same trouble, found out a moment later, and is not another strike: a sorter that
        hangs while three people ask is still on its first strike, and is asked again after ten
        seconds, not after forty. What it saw is kept all the same."""
        before = self._failed.get(model)
        kept = gave_up if gave_up is not None else (before.gave_up if before is not None else None)
        if began is not None and before is not None and began < before.at:
            strikes = before.tries
        else:
            strikes = self._strikes.get(model, 0) + 1
            self._strikes[model] = strikes
        failure = Failure(code, words, self._clock(), strikes, kept)
        self._failed[model] = failure
        return failure

    def _answered(self, model: str, *, probe: bool) -> None:
        """The sorter answered (a sort, or the probe), so nothing is held against it. Except the
        count of times in a row it did not answer a sort: the probe is two short passages, and a
        sorter that answers it and still does not answer a sort is asked less and less, not
        again every ten seconds. A sort that is answered puts it right."""
        failed = self._failed.pop(model, None)
        if probe and failed is not None and failed.gave_up is not None:
            return
        self._strikes.pop(model, None)

    def _forget_trouble(self, model: str | None) -> None:
        """A sorter that is not running has no trouble of its own: what failed, or did not answer,
        before it stopped is not what is known of it when it is started again (a person who started
        it would be told, for the rest of that wait, that it is not running, or that it did not
        answer). What it read at is kept: that is how fast it reads, whoever stopped it."""
        if model is not None:
            self._failed.pop(model, None)
            self._strikes.pop(model, None)

    # --- the sort ------------------------------------------------------------

    async def sort(self, query: str, sheets: Sequence[Sheet]) -> Sorted:
        """Put these passages in the order that answers `query` best, if there is a sorter to do
        it and it is quick enough. What was decided, and why, is the report; the order is None
        when the passages were not sorted. A sorter that fails is a report, never an exception,
        and a search never waits for a measurement."""
        settings = await self._settings()
        budget = float(settings.sort_seconds)
        choice = await self.resolve(settings)
        report = SortReport(setting=choice.setting, passages=len(sheets), budget=budget)
        model = choice.model
        if model is None:
            self._forget_trouble(choice.named)
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
        verdict = self._judge(model, tokens, budget)
        if verdict.code == "over_budget":
            assert verdict.rate is not None and verdict.expected is not None
            return Sorted(
                None,
                replace(
                    report,
                    rate=verdict.rate,
                    expected=verdict.expected,
                    code="over_budget",
                    message=over_budget_words(
                        model.id, verdict.rate, len(sheets), tokens, verdict.expected, budget
                    ),
                ),
            )
        if verdict.code != "ok":
            message = measuring_words(model.id) if verdict.code == "measuring" else verdict.message
            return Sorted(None, replace(report, code=verdict.code, message=message))
        report = replace(report, rate=verdict.rate, expected=verdict.expected)
        limit = 2 * budget
        started = self._clock()
        self.asked.append(len(sheets))
        try:
            answer = await asyncio.wait_for(
                self.gateway.rerank(model.id, query, [s.text for s in sheets], seconds=limit),
                limit,
            )
        except (TimeoutError, HubError) as exc:
            code, words = problem(self._error_of(exc, "the sort"), model.id, budget)
            log.warning("sorting the passages of a search failed (%s)", code)
            if code == "slow":
                # Given up on: it did not answer within this many seconds, and that is all it is
                # (`GaveUp`). This question says what it met (it waited, twice Sort time); the
                # next are told what the sorter is now, and are not made to wait. Questions that
                # were in the air with this one met the same hang, and are not strikes of their
                # own (`_remember`).
                self._remember(model.id, code, words, GaveUp(limit), began=started)
            self.forget()
            return Sorted(None, replace(report, code=code, message=words))
        took = self._clock() - started
        self._record(model.id, tokens, took)
        return Sorted(tuple(answer.order), replace(report, sorted=True, seconds=took))

    def _record(self, model: str, tokens: int, seconds: float) -> None:
        self._speeds.setdefault(model, Speed()).record(tokens, seconds, self._clock())
        self._answered(model, probe=False)

    # --- what is said of it ----------------------------------------------------

    async def standing(self) -> Standing:
        """How passages are sorted now, from a live look (the gateway's models, ten seconds old at
        most) and what was measured: the same judgement a question is given (`_judge`), for a sort
        of the usual size. A sorter that has not been measured is measured behind this look,
        which never waits for it: it says `measuring` until the numbers are there.

        **Why this is read on the Documents screen's own request (`GET /api/documents`) and not
        computed in the background and kept** (MR6b review, item 7): the only call it waits on is
        the look at the gateway's models, which search by meaning's status makes in the same
        request and which is shared (`listing.Listing`: kept ten seconds, a failed one three, at
        most eight to wait), so a gateway that does not answer holds the screen once; the rest is
        arithmetic on what is in memory, and the probe, the one slow thing, is already made behind
        the look. A standing kept in the background would be as old as its last refresh, and a
        setting (Sort time, the Result sorter) is in effect at once and must be said to be, so it
        would have to be worked out again at every look anyway. Settings are read once here, where
        they were twice."""
        settings = await self._settings()
        budget = float(settings.sort_seconds)
        choice = await self.resolve(settings)
        allow = settings.external_embedding
        model = choice.model
        if model is None:
            self._forget_trouble(choice.named)
            return Standing(choice, choice.code, choice.message, budget, allow)
        verdict = self._judge(model, typical_tokens(), budget)
        message = verdict.message
        if verdict.code == "over_budget":
            assert verdict.rate is not None and verdict.expected is not None
            message = typical_words(model.id, verdict.rate, verdict.expected, budget)
        return Standing(
            choice,
            verdict.code,
            message,
            budget,
            allow,
            rate=verdict.rate,
            rate_from=verdict.rate_from,
        )

    async def fields(self) -> Fields:
        """What the two settings come to now (settings never lie): which sorter the first is and
        whether it is sorting, or why none; and what the second does with the sorter's speed. Both
        are read from the standing the Documents screen is told, so a setting and the screen say
        one thing."""
        from ..person_context import catalog_only

        standing = await self.standing()
        if standing.choice.code == OFF:
            # Off asks the gateway nothing: the models are those of the last look, if there was one.
            last = self.listing.last
            probe = probe_of(last, SURFACE) if last is not None else None
        else:
            probe = await self.probe()
        ids = [m.id for m in probe.models] if probe is not None else []
        info, warning = ConfigFieldStatusLevel.info, ConfigFieldStatusLevel.warning
        choice, found = standing.choice, standing.choice.model
        tail = " (automatic)" if choice.automatic else ""
        if found is not None and standing.sorting:
            model = ConfigFieldStatus(level=info, text=f"In use: {found.id}{tail}.")
        elif found is not None:
            # Chosen, and not in use: the setting says what the screen says, with its reason.
            said = (
                "speed will be measured when someone uses Documents while signed in."
                if standing.code == "measuring" and catalog_only.get()
                else measuring_words(found.id)
                if standing.code == "measuring"
                else standing.message
            )
            model = ConfigFieldStatus(
                level=info if standing.code in ("measuring", "over_budget") else warning,
                text=f"Chosen: {found.id}{tail}, not sorting now: {reason_of(said)}",
            )
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
                text=(
                    "Speed will be measured when someone uses Documents while signed in."
                    if catalog_only.get()
                    else f"Measuring how fast {found.id} sorts, to tell whether it is quick enough."
                ),
            )
        elif standing.rate is None:
            # No speed: it did not answer, or could not be asked. Said as that, and not as a speed.
            assert standing.message is not None
            seconds = ConfigFieldStatus(level=warning, text=standing.message)
        else:
            verdict_words = (
                "within this, so passages are sorted"
                if standing.sorting
                else "over this, so passages are not sorted"
            )
            seconds = ConfigFieldStatus(
                level=info if standing.sorting else warning,
                text=(
                    f"{found.id} sorts {rate_words(standing.rate)} here: {TYPICAL_PASSAGES} "
                    f"passages of about {TYPICAL_PASSAGE_TOKENS} tokens take about "
                    f"{seconds_words(standing.expected or 0.0)}, {verdict_words}."
                ),
            )
        return Fields(ids, model, seconds)
