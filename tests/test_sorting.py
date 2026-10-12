"""Which result sorter, how fast it reads, whether a sort is quick enough, and what happens when
it is not or the sorter fails (MR6b, design `workbench-sorting.md` calls 3, 4 and 5).

`Sorting` is driven with a clock the test moves and a gateway that does what it is told, so what
it measures and decides is exact and nothing here sleeps, except where a sorter that never answers
is given up on.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from eugene_plexus_workbench import token_count
from eugene_plexus_workbench.hub import HubError, Reranked
from eugene_plexus_workbench.knowledge import sorting
from eugene_plexus_workbench.knowledge.embedders import SearchModel
from eugene_plexus_workbench.knowledge.search import Sheet
from eugene_plexus_workbench.knowledge.sorting import Sorting, Speed, choose

from . import fake_rerank
from .conftest import sorter_model

SORTER = "reranker-a"
QUESTION = "what is asked?"


def local(id: str, ready: bool = True) -> SearchModel:
    return SearchModel(id, "local", ready)


def outside(id: str, ready: bool = True, who: str | None = "openai") -> SearchModel:
    return SearchModel(id, "external", ready, who)


# --------------------------------------------------------------------------- #
# which sorter (design call 3)
# --------------------------------------------------------------------------- #


def test_auto_is_the_first_sorter_that_runs_on_the_machines_and_is_running() -> None:
    got = choose([local("a", ready=False), local("b"), local("c")], {"a", "b", "c"}, "auto", False)
    assert got.model is not None and got.model.id == "b" and got.code == "ok"
    assert got.automatic and got.setting == "auto" and got.message is None


def test_auto_never_picks_a_sorter_outside_the_machines_even_when_the_owner_allows_it() -> None:
    for allowed in (False, True):
        got = choose([outside("cloud"), local("here")], {"cloud", "here"}, "auto", allowed)
        assert got.model is not None and got.model.id == "here", "the one that stays here"
        alone = choose([outside("cloud")], {"cloud"}, "auto", allowed)
        assert alone.model is None and alone.code == "outside_only"
        assert alone.outside == ("cloud",) and not alone.quiet
        assert "outside your machines (cloud)" in (alone.message or "")
        assert "does not send the text of your documents there" in (alone.message or "")


def test_a_sorter_whose_place_eugene_cannot_say_is_not_one_that_stays_here() -> None:
    unknown = SearchModel("mystery", "unknown", True)
    got = choose([unknown], {"mystery"}, "auto", True)
    assert got.model is None and got.code == "outside_only"


def test_auto_never_picks_a_sorter_that_is_stopped_and_says_which() -> None:
    got = choose([local("a", ready=False), local("b", ready=False)], {"a", "b"}, "auto", False)
    assert got.model is None and got.code == "not_running" and got.named == "a"
    assert got.message == (
        "Not sorted: the sorter a is not running. Start it in Eugene's console under Backends."
    )
    assert not got.quiet, "a sorter that was set up and is not running is worth saying"


def test_with_no_sorter_at_all_auto_is_quiet() -> None:
    got = choose([], {"chat-model"}, "auto", False)
    assert got.model is None and got.code == "none_set_up" and got.quiet
    assert got.message == "Not sorted: no result sorter is set up."
    assert choose([], (), "", False).code == "none_set_up", "an empty setting is auto"
    assert choose([], (), "  ", False).setting == "auto"


def test_off_is_off_whatever_is_running_and_is_quiet() -> None:
    for word in ("off", "OFF", " Off "):
        got = choose([local("a")], {"a"}, word, True)
        assert got.model is None and got.code == "off" and got.quiet and got.setting == "off"
        assert got.message == "Not sorted: sorting is turned off (Result sorter is set to off)."


def test_a_named_sorter_is_used_while_it_runs_here_and_is_running() -> None:
    models = [local("a"), local("b")]
    got = choose(models, {"a", "b"}, "b", False)
    assert got.model is not None and got.model.id == "b" and got.code == "ok"
    assert got.setting == "b" and not got.automatic


def test_a_named_sorter_is_never_swapped_for_another_when_it_is_stopped() -> None:
    got = choose([local("a"), local("b", ready=False)], {"a", "b"}, "b", False)
    assert got.model is None and got.code == "not_running" and got.named == "b"
    assert got.message == (
        "Not sorted: the sorter b is not running. Start it in Eugene's console under Backends."
    )


def test_a_name_that_is_not_a_sorter_is_told_from_one_that_is_not_there() -> None:
    models = [local("a")]
    chat = choose(models, {"a", "a-chat"}, "a-chat", False)
    assert chat.model is None and chat.code == "not_sorter" and chat.named == "a-chat"
    assert "a-chat is not a result sorter" in (chat.message or "")
    nowhere = choose(models, {"a"}, "ghost", False)
    assert nowhere.model is None and nowhere.code == "not_listed"
    assert "lists no result sorter named ghost" in (nowhere.message or "")


def test_a_named_sorter_outside_the_machines_is_used_only_when_the_owner_allows_it() -> None:
    models = [outside("cloud", who="openai")]
    refused = choose(models, {"cloud"}, "cloud", False)
    assert refused.model is None and refused.code == "outside" and not refused.quiet
    assert refused.message == (
        "Not sorted: cloud runs on openai, outside your machines, and the owner has not "
        "allowed sending the text of your documents there."
    )
    allowed = choose(models, {"cloud"}, "cloud", True)
    assert allowed.model is not None and allowed.model.id == "cloud" and allowed.code == "ok"
    stopped = choose([outside("cloud", ready=False)], {"cloud"}, "cloud", True)
    assert stopped.code == "not_running"


# --------------------------------------------------------------------------- #
# how fast a sorter reads: a decaying average
# --------------------------------------------------------------------------- #


def test_a_speed_is_tokens_over_seconds_with_the_older_measurements_counting_less() -> None:
    speed = Speed(decay=0.5)
    assert speed.per_second() is None and speed.source is None
    speed.record(1000, 2.0, at=10.0, sort=False)
    assert speed.per_second() == 500.0 and speed.source == "probe"
    speed.record(3000, 1.0, at=20.0)
    # (1000 * 0.5 + 3000) / (2 * 0.5 + 1): a big sort says more than a small one, and the first
    # measurement has already faded to half.
    assert speed.per_second() == pytest.approx(3500 / 2.0) and speed.source == "sorts"
    assert speed.at == 20.0 and speed.sorts == 1


def test_what_was_true_long_ago_fades_to_nothing() -> None:
    speed = Speed()
    speed.record(1000, 1.0, at=0.0, sort=False)  # 1,000 tokens a second
    for n in range(40):
        speed.record(1000, 4.0, at=float(n + 1))  # then 250 a second, again and again
    assert speed.per_second() == pytest.approx(250.0, rel=0.001)


def test_a_sort_that_took_no_time_is_not_infinitely_fast_and_one_that_read_nothing_says_nothing() -> (
    None
):
    speed = Speed()
    speed.record(0, 1.0, at=1.0)
    assert speed.per_second() is None and speed.at is None
    speed.record(500, 0.0, at=2.0)
    assert speed.per_second() == 500 / sorting.MIN_SECONDS


def test_the_tokens_of_a_sort_are_the_passages_own_counts_and_the_question_once_for_each() -> None:
    sheets = [Sheet("one", 100), Sheet("two", 250), Sheet("three", 40)]
    asked = token_count.estimate(QUESTION)
    assert asked == 5
    assert sorting.sort_tokens(sheets, QUESTION) == 100 + 250 + 40 + 3 * asked
    assert sorting.sort_tokens([], QUESTION) == 0


# --------------------------------------------------------------------------- #
# how it is said
# --------------------------------------------------------------------------- #


def test_a_time_is_said_with_no_figure_it_does_not_have() -> None:
    said = {
        3.0: "3 s",
        0.5: "0.5 s",
        6.94: "6.9 s",
        12.4: "12 s",
        0.45: "0.45 s",
        100.0: "100 s",
        1.0: "1 s",
    }
    assert {t: sorting.seconds_words(t) for t in said} == said


def test_a_speed_is_said_to_two_figures() -> None:
    assert sorting.rate_words(823.4) == "about 820 tokens a second"
    assert sorting.rate_words(1234.5) == "about 1,200 tokens a second"
    assert sorting.rate_words(87.4) == "about 87 tokens a second"
    assert sorting.rate_words(99.6) == "about 100 tokens a second"
    assert sorting.rate_words(12345.0) == "about 12,000 tokens a second"


# --------------------------------------------------------------------------- #
# stand-ins: a clock the test moves, settings, and a gateway that does what it is told
# --------------------------------------------------------------------------- #


class Clock:
    """`time.perf_counter` for a test: it moves when a stand-in sorter reads, or the test says."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Settings:
    sort_model: str = "auto"
    sort_seconds: float = 3.0
    external_embedding: bool = False


@dataclass
class Call:
    model: str
    query: str
    documents: list[str]
    #: The seconds the caller said it would wait.
    seconds: float


@dataclass
class Gateway:
    clock: Clock
    listing: list[dict[str, Any]] = field(default_factory=lambda: [sorter_model(SORTER)])
    listing_error: BaseException | None = None
    #: How long a sort takes, and how long the probe takes (the clock moves by it): each call
    #: its own, so which is asked first never matters. `takes` and `probe_takes` are the next
    #: calls', one each, before these.
    seconds: float = 0.1
    probe_seconds: float = 0.1
    takes: deque[float] = field(default_factory=deque)
    probe_takes: deque[float] = field(default_factory=deque)
    #: Raised by the next calls, one each, before it behaves.
    errors: deque[BaseException] = field(default_factory=deque)
    #: A call that never answers.
    hang: bool = False
    #: What to say instead of the scores the stand-in sorter makes (the length is not checked).
    answer: list[float] | None = None
    prefer: dict[str, float] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    models_asked: int = 0

    async def models(self) -> dict[str, Any]:
        self.models_asked += 1
        if self.listing_error is not None:
            raise self.listing_error
        return {"data": self.listing}

    async def rerank(
        self, model: str, query: str, documents: Sequence[str], *, seconds: float = 20.0
    ) -> Reranked:
        self.calls.append(Call(model, query, list(documents), seconds))
        if self.hang:
            await asyncio.sleep(3600)
        if self.errors:
            raise self.errors.popleft()
        probing = query == sorting.PROBE_QUESTION
        queue = self.probe_takes if probing else self.takes
        took = queue.popleft() if queue else (self.probe_seconds if probing else self.seconds)
        self.clock.advance(took)
        scores = self.answer or [fake_rerank.score(query, d, self.prefer) for d in documents]
        return Reranked(scores, None, took, model)

    def sorts(self) -> list[Call]:
        """The calls that were sorts of a search, not the probe."""
        return [c for c in self.calls if c.query != sorting.PROBE_QUESTION]

    def probes(self) -> list[Call]:
        return [c for c in self.calls if c.query == sorting.PROBE_QUESTION]


class Rig:
    def __init__(self) -> None:
        self.clock = Clock()
        self.gateway = Gateway(self.clock)
        self.settings = Settings()
        self.sorting = Sorting(self.gateway, self.read, clock=self.clock)

    async def read(self) -> Settings:
        return self.settings

    async def settle(self) -> None:
        """Wait for a measurement that is being made behind a screen or a search."""
        await asyncio.gather(*list(self.sorting._measuring.values()))

    @property
    def probe_tokens(self) -> int:
        return sorting.sort_tokens(sorting.probe_sheets(), sorting.PROBE_QUESTION)


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    found = Rig()
    yield found
    await found.sorting.aclose()


def sheets(count: int = 4, tokens: int = 100) -> list[Sheet]:
    return [Sheet(f"Heading {i}\npassage {i} about ships", tokens) for i in range(count)]


# --------------------------------------------------------------------------- #
# the probe (design call 4)
# --------------------------------------------------------------------------- #


async def test_a_sorter_that_has_never_read_is_measured_with_two_passages_when_it_is_first_asked(
    rig: Rig,
) -> None:
    rig.gateway.probe_seconds = 0.5
    found = await rig.sorting.sort(QUESTION, sheets())
    [probe] = rig.gateway.probes()
    assert probe.model == SORTER and probe.query == sorting.PROBE_QUESTION
    assert len(probe.documents) == 2, "two passages"
    for (heading, words), sent in zip(sorting.PROBE_PASSAGES, probe.documents, strict=True):
        assert sent == f"{heading}\n{words}", "each as a real sort sends one: headings, words"
    assert probe.seconds == 6.0, "given twice the 3 seconds Sort time allows"
    assert found.report.rate == pytest.approx(rig.probe_tokens / 0.5)
    assert found.order is not None and found.report.sorted, "and then it is sorted"
    assert len(rig.gateway.sorts()) == 1


async def test_two_questions_asked_at_once_of_a_sorter_not_yet_measured_measure_it_once(
    rig: Rig,
) -> None:
    first, second = await asyncio.gather(
        rig.sorting.sort(QUESTION, sheets()), rig.sorting.sort(QUESTION, sheets())
    )
    assert first.report.sorted and second.report.sorted
    assert len(rig.gateway.probes()) == 1 and len(rig.gateway.sorts()) == 2


async def test_the_probe_is_not_made_again_while_the_speed_is_known_and_recent(rig: Rig) -> None:
    for _ in range(4):
        await rig.sorting.sort(QUESTION, sheets())
    assert len(rig.gateway.probes()) == 1 and len(rig.gateway.sorts()) == 4
    rig.clock.advance(sorting.RATE_MAX_AGE - 5)  # not yet old
    await rig.sorting.sort(QUESTION, sheets())
    await asyncio.sleep(0)
    assert len(rig.gateway.probes()) == 1


async def test_what_the_probe_measured_prices_the_first_sort_by_tokens_over_that_rate(
    rig: Rig,
) -> None:
    rig.gateway.probe_seconds = 2.0
    rig.gateway.seconds = 0.5
    found = await rig.sorting.sort(QUESTION, sheets(4, tokens=100))
    tokens = 4 * 100 + 4 * token_count.estimate(QUESTION)
    assert found.report.tokens == tokens == 420
    assert found.report.rate == pytest.approx(rig.probe_tokens / 2.0)
    assert found.report.expected == pytest.approx(tokens / (rig.probe_tokens / 2.0))
    assert found.report.budget == 3.0 and found.report.seconds == 0.5
    assert found.report.model == SORTER and found.report.setting == "auto"
    assert found.report.passages == 4 and found.report.sorted and found.report.code is None


# --------------------------------------------------------------------------- #
# only when quick enough (design call 4), both ways
# --------------------------------------------------------------------------- #


async def test_a_sort_that_would_take_longer_than_the_budget_is_not_made_and_says_why_with_the_numbers(
    rig: Rig,
) -> None:
    rig.gateway.probe_seconds = 1.0  # about 600 tokens a second
    big = sheets(4, tokens=700)  # 2,800 tokens, and the question four times
    found = await rig.sorting.sort(QUESTION, big)
    rate = rig.probe_tokens / 1.0
    tokens = 4 * 700 + 4 * 5
    assert found.order is None and not found.report.sorted
    assert found.report.code == "over_budget" and not found.report.quiet
    assert found.report.tokens == tokens and found.report.rate == pytest.approx(rate)
    assert found.report.expected == pytest.approx(tokens / rate)
    assert found.report.expected is not None and found.report.expected > 3.0
    assert found.report.budget == 3.0 and found.report.seconds is None
    said = found.report.message or ""
    assert said.startswith("Not sorted: reranker-a sorts about ")
    assert f"here, so sorting these 4 passages ({tokens:,} tokens) would take about " in said
    assert "and Sort time is 3 s." in said
    assert "Raise Sort time in Workbench's settings" in said and said.endswith("to sort anyway.")
    assert rig.gateway.sorts() == [], "no sort was asked of the sorter"


async def test_the_same_sort_is_made_when_the_budget_allows_it_and_not_when_it_does_not(
    rig: Rig,
) -> None:
    await rig.sorting.sort(QUESTION, sheets())  # measures it
    rate = rig.sorting.rate_of(SORTER)
    assert rate is not None
    work = sheets(2, tokens=int(rate * 3.5 / 2))  # about 3.5 seconds of reading
    tokens = sorting.sort_tokens(work, QUESTION)
    assert 3.0 < tokens / rate < 4.0
    rig.settings.sort_seconds = 3.0
    refused = await rig.sorting.sort(QUESTION, work)
    assert refused.order is None and refused.report.code == "over_budget"
    rig.settings.sort_seconds = 4.0  # a setting is in effect at once
    allowed = await rig.sorting.sort(QUESTION, work)
    assert allowed.order is not None and allowed.report.sorted
    assert allowed.report.budget == 4.0


async def test_a_sort_that_takes_exactly_the_budget_is_made(rig: Rig) -> None:
    await rig.sorting.sort(QUESTION, sheets())
    rate = rig.sorting.rate_of(SORTER)
    assert rate is not None
    work = [Sheet("x", int(rate * 3.0) - token_count.estimate(QUESTION))]
    tokens = sorting.sort_tokens(work, QUESTION)
    rig.settings.sort_seconds = tokens / rate  # exactly what it takes
    found = await rig.sorting.sort(QUESTION, work)
    assert found.report.sorted, "within the budget is not under it"
    rate = rig.sorting.rate_of(SORTER)  # that sort is part of what it reads at now
    assert rate is not None
    rig.settings.sort_seconds = tokens / rate - 0.001
    assert not (await rig.sorting.sort(QUESTION, work)).report.sorted


# --------------------------------------------------------------------------- #
# the speed follows what the sorter does (a decaying average of recent sorts)
# --------------------------------------------------------------------------- #


async def test_the_speed_is_a_decaying_average_of_the_probe_and_the_sorts_after_it(
    rig: Rig,
) -> None:
    probe = rig.probe_tokens
    work = sheets(4, tokens=100)
    tokens = sorting.sort_tokens(work, QUESTION)
    rig.gateway.probe_seconds = 1.0
    rig.gateway.takes.extend([2.0, 0.5])
    await rig.sorting.sort(QUESTION, work)  # the probe, then a sort that takes two seconds
    first = (probe * sorting.DECAY + tokens) / (1.0 * sorting.DECAY + 2.0)
    assert rig.sorting.rate_of(SORTER) == pytest.approx(first)
    await rig.sorting.sort(QUESTION, work)  # then one that takes half a second
    second = (probe * sorting.DECAY**2 + tokens * sorting.DECAY + tokens) / (
        1.0 * sorting.DECAY**2 + 2.0 * sorting.DECAY + 0.5
    )
    assert rig.sorting.rate_of(SORTER) == pytest.approx(second)


async def test_a_sorter_that_turns_slow_is_noticed_and_not_asked_while_it_is_too_slow(
    rig: Rig,
) -> None:
    work = sheets(4, tokens=100)  # 420 tokens
    rig.gateway.takes.extend([0.1, 10.0])  # fast, then slow: ten seconds for 420 tokens
    assert (await rig.sorting.sort(QUESTION, work)).report.sorted
    assert (await rig.sorting.sort(QUESTION, work)).report.sorted, "it was quick enough when asked"
    slow = await rig.sorting.sort(QUESTION, work)
    assert slow.order is None and slow.report.code == "over_budget", "and is not asked again"
    assert slow.report.rate is not None and slow.report.rate < 420 / 3.0
    asked = len(rig.gateway.sorts())
    await rig.sorting.sort(QUESTION, work)
    assert len(rig.gateway.sorts()) == asked


async def test_a_speed_that_is_old_is_used_as_it_stands_and_measured_again_behind_the_search(
    rig: Rig,
) -> None:
    await rig.sorting.sort(QUESTION, sheets())
    old = rig.sorting.rate_of(SORTER)
    rig.clock.advance(sorting.RATE_MAX_AGE + 1)
    rig.gateway.probe_seconds = 4.0  # the sorter is slower now: the new probe will say so
    found = await rig.sorting.sort(QUESTION, sheets())
    assert found.report.rate == old, "this search is priced by what was measured"
    assert found.report.sorted
    await rig.settle()
    assert len(rig.gateway.probes()) == 2
    # What was measured a while ago says nothing of now: the new measurement replaces it.
    assert rig.sorting.rate_of(SORTER) == pytest.approx(rig.probe_tokens / 4.0)


async def test_an_old_speed_is_not_measured_again_at_every_look_while_the_sorter_does_not_answer(
    rig: Rig,
) -> None:
    await rig.sorting.sort(QUESTION, sheets())
    rig.clock.advance(sorting.RATE_MAX_AGE + 1)
    rig.gateway.errors.extend([HubError("x", status=503)] * 5)
    for _ in range(4):
        await rig.sorting.standing()
        await rig.settle()
    assert len(rig.gateway.probes()) == 2, "the one that was asked again, and not once a look"
    stood = await rig.sorting.standing()
    assert stood.sorting, "the speed that was measured is still the one in use"
    rig.clock.advance(sorting.MEASURE_RETRY + 1)
    await rig.sorting.standing()
    await rig.settle()
    assert len(rig.gateway.probes()) == 3, "and it is asked again after a moment"


async def test_a_sorter_that_was_slow_for_a_while_is_not_left_off_for_ever(rig: Rig) -> None:
    work = sheets(4, tokens=100)
    rig.gateway.probe_seconds = 0.1
    rig.gateway.takes.extend([0.1, 30.0, 30.0])
    for _ in range(3):
        await rig.sorting.sort(QUESTION, work)
    assert (await rig.sorting.sort(QUESTION, work)).report.code == "over_budget"
    rig.clock.advance(sorting.RATE_MAX_AGE + 1)
    still = await rig.sorting.sort(QUESTION, work)  # priced by the old rate, and measures again
    assert still.report.code == "over_budget"
    await rig.settle()
    assert (await rig.sorting.sort(QUESTION, work)).report.sorted, "it is fast again"


# --------------------------------------------------------------------------- #
# a sorter that fails never fails a search (design call 5)
# --------------------------------------------------------------------------- #


async def test_a_sorter_that_is_stopped_when_it_is_first_asked_is_said_and_asked_again_soon(
    rig: Rig,
) -> None:
    rig.gateway.errors.append(HubError("none", status=503, words="No backend is ready."))
    found = await rig.sorting.sort(QUESTION, sheets())
    assert found.order is None and found.report.code == "not_running"
    assert found.report.message == (
        "Not sorted: the sorter reranker-a is not running. Start it in Eugene's console under "
        "Backends."
    )
    assert found.report.model == SORTER and not found.report.quiet
    again = await rig.sorting.sort(QUESTION, sheets())
    assert again.report.code == "not_running" and len(rig.gateway.calls) == 1, (
        "it was just found stopped: not asked again for a moment"
    )
    rig.clock.advance(sorting.MEASURE_RETRY + 1)
    back = await rig.sorting.sort(QUESTION, sheets())
    assert back.report.sorted and len(rig.gateway.probes()) == 2, "and then it is, and it is up"


@pytest.mark.parametrize(
    ("error", "code", "words"),
    [
        (HubError("x", status=503, words="Loading model"), "not_running", "is not running"),
        (HubError("x", status=404, words="No driver serves it"), "not_running", "is not running"),
        (
            HubError("x", status=400, words="input (2301 tokens) is too large to process"),
            "refused",
            "refused the request (input (2301 tokens) is too large to process)",
        ),
        (
            HubError("x", status=502, kind="gateway", words="every backend failed"),
            "error",
            "failed (every backend failed)",
        ),
        (
            HubError(
                "The gateway's answer to the sort request was not usable: it held 3 scores for 5 "
                "passages.",
                kind="unusable",
            ),
            "unusable",
            "was not usable: it held 3 scores for 5 passages.",
        ),
        (
            HubError(
                "This Workbench's key is at its limit (60 a minute).",
                status=429,
                kind="limit",
                words="60 a minute",
            ),
            "limited",
            "key is at its limit (60 a minute)",
        ),
        (
            HubError("Eugene refused the key this Workbench uses", status=401, kind="key"),
            "key",
            "Eugene refused the key this Workbench uses",
        ),
        (
            HubError(
                "Workbench cannot reach Eugene's gateway at http://g",
                status=503,
                kind="unreachable",
            ),
            "unreachable",
            "cannot reach Eugene's gateway at http://g",
        ),
    ],
)
async def test_a_sorter_that_fails_is_a_report_in_its_own_words_and_the_passages_are_left_as_found(
    rig: Rig, error: HubError, code: str, words: str
) -> None:
    await rig.sorting.sort(QUESTION, sheets())  # it is measured, and sorts
    rig.gateway.errors.append(error)
    found = await rig.sorting.sort(QUESTION, sheets())
    assert found.order is None and not found.report.sorted
    assert found.report.code == code and not found.report.quiet
    assert (found.report.message or "").startswith("Not sorted: ")
    assert words in (found.report.message or "")
    assert found.report.model == SORTER and found.report.passages == 4
    assert (await rig.sorting.sort(QUESTION, sheets())).report.sorted, "and the next is sorted"


#: What the gateway says to a request that comes while a backend that failed is cooling down, or
#: while its recovery is being tried (measured, MR6b acceptance): a 503 that asks for a second's
#: wait. The sorter it is about is running, or has just been started again.
COOLING_DOWN = (
    "A backend serving this model is not ready yet: Eligible backends are cooling down or "
    "already probing recovery. Try a new request later.. If an engine is still loading its "
    "weights this will clear on its own."
)


async def test_a_gateway_that_holds_requests_back_for_a_moment_is_not_a_sorter_that_is_stopped(
    rig: Rig,
) -> None:
    """Measured live: after a sorter's engine was ended and started again, the gateway answered
    the next sorts with a 503 and `Retry-After: 1` while it tried the backend again, and Workbench
    told a person "the sorter is not running. Start it in Eugene's console under Backends" of a
    sorter that was running. A 503 that asks for a wait is the gateway holding requests back, said
    as that, in its own words; a 503 that does not is a sorter that is not ready, as before."""
    await rig.sorting.sort(QUESTION, sheets())  # it is measured, and sorts
    rig.gateway.errors.append(HubError("x", status=503, words=COOLING_DOWN, retry_after=1.0))
    held = await rig.sorting.sort(QUESTION, sheets())
    assert held.order is None and not held.report.sorted
    assert held.report.code == "error" and not held.report.quiet
    assert held.report.message == (
        "Not sorted: Eugene's gateway asked Workbench to wait 1 s before asking the sorter "
        f"reranker-a again ({COOLING_DOWN}), so the passages are in the order the search found "
        "them."
    )
    assert "is not running" not in (held.report.message or "")
    assert "Start it" not in (held.report.message or ""), "there is nothing for a person to start"
    assert held.report.model == SORTER and held.report.passages == 4
    # The same 503 without a wait to ask for is what it was: nothing is ready.
    rig.gateway.errors.append(HubError("x", status=503, words="No backend is ready."))
    stopped = await rig.sorting.sort(QUESTION, sheets())
    assert stopped.report.code == "not_running"
    assert "is not running. Start it in Eugene's console under Backends." in (
        stopped.report.message or ""
    )
    assert (await rig.sorting.sort(QUESTION, sheets())).report.sorted, "and the next is sorted"


async def test_a_wait_the_gateway_asks_for_is_said_in_seconds_as_a_person_reads_them(
    rig: Rig,
) -> None:
    await rig.sorting.sort(QUESTION, sheets())
    rig.gateway.errors.append(HubError("x", status=503, words=COOLING_DOWN, retry_after=12.0))
    long = await rig.sorting.sort(QUESTION, sheets())
    assert "wait 12 s before asking the sorter reranker-a again" in (long.report.message or "")
    rig.gateway.errors.append(HubError("x", status=503, words=COOLING_DOWN, retry_after=0.0))
    none = await rig.sorting.sort(QUESTION, sheets())
    assert none.report.code == "error" and "is not running" not in (none.report.message or "")


async def test_a_sorter_that_takes_over_twice_the_budget_is_given_up_on_and_that_is_remembered(
    rig: Rig,
) -> None:
    await rig.sorting.sort(QUESTION, sheets())
    before = rig.sorting.rate_of(SORTER)
    rig.settings.sort_seconds = 0.05
    rig.gateway.hang = True
    work = [Sheet("one", 1), Sheet("two", 1)]  # 12 tokens: quick enough, if it answers
    found = await rig.sorting.sort(QUESTION, work)
    assert found.order is None and found.report.code == "slow" and not found.report.quiet
    assert found.report.message == (
        "Not sorted: reranker-a did not answer within 0.1 s (twice Sort time, 0.05 s), so the "
        "passages are in the order the search found them."
    )
    ask = rig.gateway.sorts()[-1]
    assert ask.seconds == pytest.approx(0.1), "the request is given twice the budget"
    after = rig.sorting.rate_of(SORTER)
    assert before is not None and after is not None and after < before, (
        "a sort given up on says the sorter is slower than it was thought"
    )


async def test_a_sorter_that_stopped_answering_is_not_asked_again_by_the_next_questions(
    rig: Rig,
) -> None:
    """Measured live (MR6b acceptance): a sorter that had read well for a while stopped answering.
    The sort that was given up on was blended into that history, what it left still priced the
    next question inside Sort time, and that question waited twice Sort time again; the one after
    it too, until the average had fallen far enough. The sort that was given up on is what is
    known of the sorter now, so the next question is over Sort time and is not made to wait."""
    work = sheets()
    for _ in range(8):  # it has read well for a while: about 4,000 tokens a second
        assert (await rig.sorting.sort(QUESTION, work)).report.sorted
    rig.settings.sort_seconds = 0.5  # a usual sort, 0.1 s, is a fifth of it
    rig.gateway.hang = True
    gave_up = await rig.sorting.sort(QUESTION, work)  # given up on after a second
    assert gave_up.report.code == "slow"
    rig.gateway.hang = False
    asked = len(rig.gateway.calls)
    for _ in range(3):
        next_ = await rig.sorting.sort(QUESTION, work)
        assert next_.order is None and next_.report.code == "over_budget"
        assert (next_.report.expected or 0) > 0.5
    assert len(rig.gateway.calls) == asked, "a sorter that did not answer is not asked again"
    stood = await rig.sorting.standing()
    assert stood.code == "over_budget" and not stood.sorting, "and the screen says so"
    assert stood.rate == pytest.approx(work_tokens(work) / 1.0), "at the speed of that sort"


async def test_a_sorter_that_was_given_up_on_is_measured_again_when_the_speed_is_old(
    rig: Rig,
) -> None:
    """Replacing what was measured with the sort that was given up on does not leave a sorter that
    answers again off for ever: an old speed is measured again behind the next search."""
    work = sheets()
    for _ in range(4):
        await rig.sorting.sort(QUESTION, work)
    rig.settings.sort_seconds = 0.5
    rig.gateway.hang = True
    assert (await rig.sorting.sort(QUESTION, work)).report.code == "slow"
    rig.gateway.hang = False
    assert (await rig.sorting.sort(QUESTION, work)).report.code == "over_budget"
    rig.clock.advance(sorting.RATE_MAX_AGE + 1)
    await rig.sorting.sort(QUESTION, work)  # priced by the old speed, and measures again
    await rig.settle()
    assert (await rig.sorting.sort(QUESTION, work)).report.sorted, "it answers, and is used again"


def work_tokens(work: Sequence[Sheet]) -> int:
    """What a sort of these sheets is worth in tokens, as Workbench prices it."""
    return sorting.sort_tokens(work, QUESTION)


async def test_a_sorter_whose_answer_is_not_a_score_for_each_passage_is_not_believed(
    rig: Rig,
) -> None:
    await rig.sorting.sort(QUESTION, sheets())
    rig.gateway.errors.append(HubError("not usable: it held no scores", kind="unusable"))
    found = await rig.sorting.sort(QUESTION, sheets())
    assert found.order is None and found.report.code == "unusable"


async def test_a_sorter_that_is_off_asks_the_gateway_nothing_and_is_quiet(rig: Rig) -> None:
    rig.settings.sort_model = "off"
    found = await rig.sorting.sort(QUESTION, sheets())
    assert found.order is None and found.report.code == "off" and found.report.quiet
    assert found.report.setting == "off" and found.report.model is None
    assert rig.gateway.models_asked == 0 and rig.gateway.calls == []


async def test_with_no_sorter_listed_nothing_is_asked_of_one_and_it_is_quiet(rig: Rig) -> None:
    rig.gateway.listing = []
    found = await rig.sorting.sort(QUESTION, sheets())
    assert found.order is None and found.report.code == "none_set_up" and found.report.quiet
    assert rig.gateway.calls == []


async def test_a_gateway_that_cannot_list_its_models_is_a_report_and_not_a_failure(
    rig: Rig,
) -> None:
    rig.gateway.listing_error = HubError(
        "Workbench cannot reach Eugene's gateway at http://g", status=503, kind="unreachable"
    )
    found = await rig.sorting.sort(QUESTION, sheets())
    assert found.order is None and found.report.code == "unreachable"
    assert found.report.message == "Not sorted: Workbench cannot reach Eugene's gateway at http://g"
    assert rig.gateway.calls == []


@pytest.mark.parametrize(
    "broken", [ValueError("not JSON"), AttributeError("a list has no get"), TypeError("no")]
)
async def test_a_list_of_models_that_cannot_be_read_is_a_report_and_not_a_failure(
    rig: Rig, broken: BaseException
) -> None:
    rig.gateway.listing_error = broken
    found = await rig.sorting.sort(QUESTION, sheets())
    assert found.order is None and found.report.code == "unusable" and not found.report.quiet
    assert found.report.message == (
        "Not sorted: Eugene's gateway answered the list of its models with something Workbench "
        "cannot read."
    )
    assert rig.gateway.calls == []
    stood = await rig.sorting.standing()
    assert stood.code == "unusable" and not stood.sorting, "a screen says it too, and does not fail"


async def test_a_gateway_that_lists_its_models_as_something_else_is_a_report_too(
    rig: Rig,
) -> None:
    async def a_list() -> Any:
        return ["not", "an", "object"]

    rig.gateway.models = a_list  # type: ignore[method-assign]
    found = await rig.sorting.sort(QUESTION, sheets())
    assert found.order is None and found.report.code == "unusable"


async def test_what_the_gateway_lists_is_looked_at_once_in_ten_seconds_and_again_after_a_failure(
    rig: Rig,
) -> None:
    for _ in range(3):
        await rig.sorting.sort(QUESTION, sheets())
    assert rig.gateway.models_asked == 1
    rig.clock.advance(sorting.PROBE_TTL + 1)
    await rig.sorting.sort(QUESTION, sheets())
    assert rig.gateway.models_asked == 2
    rig.gateway.errors.append(HubError("x", status=503))
    await rig.sorting.sort(QUESTION, sheets())
    await rig.sorting.sort(QUESTION, sheets())
    assert rig.gateway.models_asked == 3, "a failed sort makes the next look a fresh one"


async def test_a_sorter_that_is_listed_but_stopped_is_not_asked_and_one_outside_is_not_chosen(
    rig: Rig,
) -> None:
    rig.gateway.listing = [
        sorter_model("cloud", locality="external", owned_by="openai"),
        sorter_model("stopped", ready=False),
    ]
    found = await rig.sorting.sort(QUESTION, sheets())
    assert found.order is None and found.report.code == "not_running"
    assert found.report.model == "stopped", "the words are about the one that is stopped"
    assert rig.gateway.calls == [], "and nothing was sent to the one outside"


async def test_a_named_sorter_outside_the_machines_is_sent_passages_only_when_it_is_allowed(
    rig: Rig,
) -> None:
    rig.gateway.listing = [sorter_model("cloud", locality="external", owned_by="openai")]
    rig.settings.sort_model = "cloud"
    refused = await rig.sorting.sort(QUESTION, sheets())
    assert refused.order is None and refused.report.code == "outside"
    assert rig.gateway.calls == []
    rig.settings.external_embedding = True
    allowed = await rig.sorting.sort(QUESTION, sheets())
    assert allowed.report.sorted and allowed.report.model == "cloud"
    assert rig.gateway.sorts()[0].documents[0].startswith("Heading 0\n")


# --------------------------------------------------------------------------- #
# the order that comes back
# --------------------------------------------------------------------------- #


async def test_the_order_is_the_sorters_best_first_and_a_tie_keeps_the_order_sent(rig: Rig) -> None:
    rig.gateway.answer = [0.1, 0.9, 0.5, 0.9]
    found = await rig.sorting.sort(QUESTION, sheets(4))
    assert found.order == (1, 3, 2, 0)


async def test_what_is_sent_is_each_passage_as_it_was_given_and_the_question_asked(
    rig: Rig,
) -> None:
    work = [Sheet("Notes > Budget\nThe fees are paid.", 9), Sheet("A passage with no heading.", 7)]
    await rig.sorting.sort("who pays the fees?", work)
    [sent] = rig.gateway.sorts()
    assert sent.model == SORTER and sent.query == "who pays the fees?"
    assert sent.documents == ["Notes > Budget\nThe fees are paid.", "A passage with no heading."]


# --------------------------------------------------------------------------- #
# what the screens say (settings never lie)
# --------------------------------------------------------------------------- #


async def test_the_standing_does_not_wait_for_a_measurement_and_says_so_until_it_is_made(
    rig: Rig,
) -> None:
    first = await rig.sorting.standing()
    assert first.code == "measuring" and first.model == SORTER and first.rate is None
    assert not first.sorting and first.message is None
    assert rig.gateway.probes() == [], "it did not wait for the probe to be asked"
    await rig.settle()
    done = await rig.sorting.standing()
    assert done.code == "ok" and done.sorting and done.rate_from == "probe"
    assert done.rate == pytest.approx(rig.probe_tokens / 0.1)
    assert len(rig.gateway.probes()) == 1, "once, however many looks"
    await rig.sorting.sort(QUESTION, sheets())
    assert (await rig.sorting.standing()).rate_from == "sorts"


async def test_a_sorter_too_slow_for_a_sort_of_the_usual_size_is_said_with_the_numbers(
    rig: Rig,
) -> None:
    rig.gateway.probe_seconds = 4.0  # the probe's 600-odd tokens in four seconds
    await rig.sorting.standing()
    await rig.settle()
    stood = await rig.sorting.standing()
    rate = rig.probe_tokens / 4.0
    typical = sorting.typical_tokens()
    assert stood.code == "over_budget" and not stood.sorting
    assert typical == 18 * (300 + 20) and stood.expected == pytest.approx(typical / rate)
    said = stood.message or ""
    assert said.startswith("Not sorted: reranker-a sorts about ")
    assert "so sorting the 18 passages a question usually finds (about 300 tokens each)" in said
    assert "to each answer, and Sort time is 3 s. Raise Sort time" in said
    view = stood.view()
    assert view["sorting"] is False and view["code"] == "over_budget"
    assert view["budgetSeconds"] == 3.0 and view["ratePerSecond"] == pytest.approx(rate, abs=0.05)
    assert view["typical"] == {
        "passages": 18,
        "passageTokens": 300,
        "tokens": typical,
        "seconds": pytest.approx(typical / rate, abs=0.005),
    }
    rig.settings.sort_seconds = 60.0
    assert (await rig.sorting.standing()).sorting, "a raised budget is in effect at once"


async def test_the_view_says_which_sorter_where_it_runs_and_what_is_left_out(rig: Rig) -> None:
    rig.gateway.listing = [
        sorter_model("cloud", locality="external", owned_by="openai"),
        sorter_model(SORTER),
    ]
    await rig.sorting.standing()
    await rig.settle()
    view = (await rig.sorting.standing()).view()
    assert view["setting"] == "auto" and view["automatic"] is True
    assert view["model"] == SORTER and view["label"] == "In use: reranker-a (automatic)"
    assert view["locality"] == "local" and view["outside"] == ["cloud"]
    assert view["allowExternal"] is False and view["rateFrom"] == "probe"
    assert view["code"] == "ok" and view["message"] is None and view["quiet"] is False


async def test_the_standing_of_a_sorter_that_cannot_be_measured_names_what_was_seen(
    rig: Rig,
) -> None:
    rig.gateway.errors.append(HubError("x", status=503, words="Loading model"))
    await rig.sorting.standing()
    await rig.settle()
    stood = await rig.sorting.standing()
    assert stood.code == "not_running" and stood.rate is None and not stood.sorting
    assert "reranker-a is not running" in (stood.message or "")


async def test_a_measurement_that_is_under_way_when_workbench_stops_is_ended_quietly(
    rig: Rig,
) -> None:
    rig.gateway.hang = True
    await rig.sorting.standing()
    await asyncio.sleep(0)
    assert rig.sorting._measuring, "the probe is in the air"
    await rig.sorting.aclose()
    assert not rig.sorting._measuring and rig.sorting.rate_of(SORTER) is None


async def test_the_two_settings_say_what_they_come_to_now(rig: Rig) -> None:
    rig.gateway.listing = [sorter_model(SORTER), sorter_model("second")]
    first = await rig.sorting.fields()
    assert first.ids == [SORTER, "second"]
    assert first.model.level.value == "info"
    assert first.model.text == "In use: reranker-a (automatic)."
    assert first.seconds.level.value == "info"
    assert first.seconds.text.startswith("Measuring how fast reranker-a sorts")
    await rig.settle()
    ok = await rig.sorting.fields()
    assert ok.seconds.level.value == "info"
    assert ok.seconds.text.startswith("reranker-a sorts about ")
    assert "18 passages of about 300 tokens take about " in ok.seconds.text
    assert ok.seconds.text.endswith("within this, so passages are sorted.")
    rig.settings.sort_seconds = 0.5
    over = await rig.sorting.fields()
    assert over.seconds.level.value == "warning"
    assert over.seconds.text.endswith("over this, so passages are not sorted.")
    rig.settings.sort_model = "second"
    named = await rig.sorting.fields()
    assert named.model.text == "In use: second."


async def test_the_settings_when_there_is_no_sorter_in_use_say_so_and_say_why(rig: Rig) -> None:
    rig.settings.sort_model = "off"
    off = await rig.sorting.fields()
    assert off.model.level.value == "info"
    assert off.model.text == "Off: passages are left in the order the search found them."
    assert off.seconds.text == "Nothing is sorted now, so this changes nothing yet."
    rig.settings.sort_model = "auto"
    rig.gateway.listing = []
    rig.clock.advance(sorting.PROBE_TTL + 1)
    none = await rig.sorting.fields()
    assert none.model.level.value == "info", "nothing is wrong: no sorter was ever set up"
    assert none.model.text == "Not sorted: no result sorter is set up."
    rig.gateway.listing = [sorter_model("stopped", ready=False)]
    rig.clock.advance(sorting.PROBE_TTL + 1)
    stopped = await rig.sorting.fields()
    assert stopped.model.level.value == "warning"
    assert "the sorter stopped is not running" in stopped.model.text
    assert stopped.ids == ["stopped"], "still offered as a suggestion"
    rig.settings.sort_model = "off"
    still = await rig.sorting.fields()
    assert still.ids == ["stopped"], "off still lists what there is, for a person to choose"
