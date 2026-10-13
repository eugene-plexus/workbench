"""Adding meaning to documents (MR5, design 4.2): one batch at a time, at most so many a
minute, `Retry-After` obeyed, a stopped search model named and waited for, a changed one
refused (never two models' numbers in one index), a passage that is too long refused alone,
a removal that ends its request, and what a stop leaves picked up again at boot.

The worker is driven one turn at a time (`Meaning._cycle`) with a clock the test moves, so what
it does is exact and nothing here sleeps; one test runs the real loop to watch it.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench.hub import Embedded, HubError
from eugene_plexus_workbench.knowledge import embedders
from eugene_plexus_workbench.knowledge import meaning as meaning_module
from eugene_plexus_workbench.knowledge.index import (
    LIBRARY,
    Index,
    Indexes,
    chat_scope,
)
from eugene_plexus_workbench.knowledge.meaning import (
    Meaning,
    Queue,
    RateLimiter,
    Throughput,
)
from eugene_plexus_workbench.knowledge.search import SignalUnavailable, plan_query

from . import fake_embedding
from .conftest import search_model
from .test_knowledge_index import ADA, BO, indexes, put  # noqa: F401

EMBED = "embed-model"


# --------------------------------------------------------------------------- #
# stand-ins: a clock the test moves, settings, and a gateway that does what it is told
# --------------------------------------------------------------------------- #


class Clock:
    """`time.perf_counter` for a test: it moves when something sleeps, or the test says."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Settings:
    enabled: bool = True
    embedding_model: str = "auto"
    external_embedding: bool = False
    embed_per_minute: int = 30


@dataclass
class Call:
    model: str
    inputs: list[str]
    at: float
    in_flight: int


@dataclass
class Gateway:
    clock: Clock
    listing: list[dict[str, Any]] = field(default_factory=lambda: [search_model(EMBED)])
    listing_error: HubError | None = None
    calls: list[Call] = field(default_factory=list)
    #: Raised by the next requests, one each, before it behaves.
    errors: deque[BaseException] = field(default_factory=deque)
    names: dict[str, str] = field(default_factory=dict)
    dims: dict[str, int] = field(default_factory=dict)
    gate: asyncio.Event | None = None
    delay: float = 0.0
    refuse: str | None = None
    refuse_status: int = 400
    #: What the gateway says of a refusal, when a test wants it to say more than the engine did.
    refuse_words: str | None = None
    nan: str | None = None
    in_flight: int = 0
    peak: int = 0
    cancelled: int = 0
    models_asked: int = 0

    async def models(self) -> dict[str, Any]:
        self.models_asked += 1
        if self.listing_error is not None:
            raise self.listing_error
        return {"data": self.listing}

    async def embeddings(
        self, model: str, inputs: Sequence[str], *, seconds: float = 120.0
    ) -> Embedded:
        self.calls.append(Call(model, list(inputs), self.clock(), self.in_flight))
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if self.gate is not None:
                try:
                    await self.gate.wait()
                except asyncio.CancelledError:
                    self.cancelled += 1
                    raise
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.errors:
                raise self.errors.popleft()
            if self.refuse and any(self.refuse in t for t in inputs):
                raise HubError(
                    "input (1631 tokens) is too large to process. increase the physical batch size",
                    status=self.refuse_status,
                    words=self.refuse_words or "input (1631 tokens) is too large to process",
                )
            dim = self.dims.get(model, 4)
            rows = [fake_embedding.vector(t, dim) for t in inputs]
            if self.nan and any(self.nan in t for t in inputs):
                rows[0] = [float("nan"), *rows[0][1:]]
            return Embedded(self.names.get(model, model), rows, len(inputs), 0.01)
        finally:
            self.in_flight -= 1

    def passages(self) -> list[str]:
        """What the search model was asked to embed, not counting the tiny question that
        asks which model answers."""
        return [t for c in self.calls for t in c.inputs if t != meaning_module.IDENTIFY_TEXT]


class World:
    def __init__(self, tmp: Path) -> None:
        self.clock = Clock()
        self.indexes = Indexes(tmp / "data")
        self.gateway = Gateway(self.clock)
        self.settings = Settings()
        self.meaning = Meaning(
            self.indexes, self.gateway, self.read, clock=self.clock, sleep=self.clock.sleep
        )

    async def read(self) -> Settings:
        return self.settings

    def index(self, sub: str = ADA) -> Index:
        found = self.indexes.get(sub)
        assert found is not None
        return found

    async def add(
        self,
        doc_id: str,
        passages: int = 3,
        *,
        sub: str = ADA,
        scope: str = LIBRARY,
        text: str | None = None,
    ) -> None:
        """A document read and searchable by words, owed its meaning."""
        body = text or "\n\n".join(f"# H{n}\n\nParagraph {n} of {doc_id}." for n in range(passages))
        extra = {"source": f"attachment:{doc_id}"} if scope != LIBRARY else {}
        await put(
            self.index(sub), doc_id, body, name=f"{doc_id}.md", scope=scope, ready=False, **extra
        )
        self.meaning.wake(sub)

    async def warm(self) -> None:
        """Ask the search model which model it is now, so that the next request a test holds up
        or fails is a batch of passages and not that tiny first question."""
        await self.meaning.identify(EMBED)

    async def run(self, limit: int = 100) -> list[float | None]:
        """Turns until the worker has nothing to do (or has to wait): what each returned."""
        out: list[float | None] = []
        for _ in range(limit):
            pause = await self.meaning._cycle()
            out.append(pause)
            if pause != 0.0:
                break
        return out

    async def status(self, doc_id: str, sub: str = ADA) -> str:
        found = await self.index(sub).document(doc_id)
        assert found is not None
        return found.status

    async def embedded(self, sub: str = ADA) -> int:
        return (await self.index(sub).meaning_counts()).embedded

    async def built(self, sub: str = ADA) -> embedders.Identity | None:
        return embedders.Identity.from_json(await self.index(sub).embedder())


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    made = World(tmp_path)
    try:
        yield made
    finally:
        await made.meaning.aclose()
        await made.indexes.aclose()


# --------------------------------------------------------------------------- #
# one batch at a time
# --------------------------------------------------------------------------- #


async def test_a_turn_embeds_sixteen_passages_and_stores_unit_vectors_with_what_the_search_was_built_with(
    world: World,
) -> None:
    world.gateway.listing = [search_model("Qwen3-Embedding-0.6B-Q8_0")]
    await world.add("doc00001", 40)
    turns = await world.run()
    assert turns == [0.0, 0.0, 0.0, None], "three batches, and then nothing owed"
    assert [len(c.inputs) for c in world.gateway.calls if c.inputs != ["ping"]] == [16, 16, 8]
    assert list(world.meaning.served) == [(ADA, 16), (ADA, 16), (ADA, 8)]
    assert await world.status("doc00001") == "ready" and await world.embedded() == 40
    matrix = await world.index().load_vectors(4)
    assert matrix.size == 40 and abs(float((matrix.data**2).sum(axis=1).min()) - 1.0) < 1e-5
    built = await world.built()
    assert built is not None and built.model == "Qwen3-Embedding-0.6B-Q8_0" and built.dim == 4
    assert built.known and built.query_prefix.startswith("Instruct: Given a question")
    # A passage is embedded with the headings above it, and no prefix (Qwen3 has none for passages).
    assert world.gateway.passages()[0] == "H0\nParagraph 0 of doc00001."


async def test_the_first_batch_asks_which_model_answers_and_never_again_while_answers_keep_coming(
    world: World,
) -> None:
    await world.add("doc00001", 40)
    await world.run()
    pings = [c for c in world.gateway.calls if c.inputs == [meaning_module.IDENTIFY_TEXT]]
    assert len(pings) == 1, "every real answer says which model it was"


async def test_at_most_one_request_is_in_the_air_even_with_many_documents(
    tmp_path: Path,
) -> None:
    """The real loop, the real clock: one request at a time, whatever is owed."""
    found = Indexes(tmp_path / "data")
    gateway = Gateway(Clock())
    gateway.clock = lambda: time.perf_counter()  # type: ignore[assignment]
    gateway.delay = 0.01
    settings = Settings(embed_per_minute=6000)

    async def read() -> Settings:
        return settings

    meaning = Meaning(found, gateway, read)
    try:
        index = found.get(ADA)
        assert index is not None
        for n in range(6):
            await put(index, f"doc0000{n}", "\n\n".join(f"# H{i}\n\nBody {i}." for i in range(20)),
                      name=f"d{n}.md", ready=False)  # fmt: skip
        meaning.wake(ADA)
        meaning.start()
        for _ in range(1500):
            if not (await index.meaning_counts()).owed:
                break
            await asyncio.sleep(0.02)
        assert (await index.meaning_counts()).owed == 0
        assert gateway.peak == 1 and len(gateway.passages()) == 120
    finally:
        await meaning.aclose()
        await found.aclose()


async def test_at_most_so_many_requests_are_started_in_any_minute(world: World) -> None:
    world.settings.embed_per_minute = 3
    await world.add("doc00001", 16 * 7)  # seven batches
    await world.run(limit=200)
    starts = [c.at for c in world.gateway.calls if c.inputs != ["ping"]]
    assert len(starts) == 7
    for first in starts:
        inside = [t for t in starts if first <= t < first + 60]
        assert len(inside) <= 3, "never more than three in a minute"
    assert starts[3] >= starts[0] + 60, "the fourth waited for the first to be a minute old"
    assert max(world.clock.slept) <= 60.0


async def test_a_changed_limit_is_in_effect_at_once(world: World) -> None:
    world.settings.embed_per_minute = 1
    await world.add("doc00001", 32)
    await world.run(limit=1)
    world.settings.embed_per_minute = 10
    before = len(world.clock.slept)
    await world.run(limit=1)
    assert len(world.clock.slept) == before, "the owner raised it, and the next batch did not wait"


# --------------------------------------------------------------------------- #
# the rate limiter and what time is measured from
# --------------------------------------------------------------------------- #


async def test_the_limiter_lets_the_window_start_over_and_counts_what_is_used() -> None:
    clock = Clock()
    limiter = RateLimiter(60.0, clock=clock, sleep=clock.sleep)
    assert [await limiter.wait(2), await limiter.wait(2)] == [0.0, 0.0] and limiter.used() == 2
    waited = await limiter.wait(2)
    assert waited == 60.0 and clock.now == 1060.0
    clock.advance(61)
    assert limiter.used() == 0, "a minute later none is counted"


async def test_a_limit_of_nothing_is_taken_as_one() -> None:
    clock = Clock()
    limiter = RateLimiter(60.0, clock=clock, sleep=clock.sleep)
    assert await limiter.wait(0) == 0.0


def test_the_time_left_is_measured_from_the_batches_done_and_is_none_before_any() -> None:
    clock = Clock()
    rate = Throughput(clock=clock)
    assert rate.per_second() is None, "nothing measured is no number"
    rate.record(16, 2.0)
    assert rate.per_second() == 8.0, "one batch: its own passages over its own time"
    clock.advance(4)
    rate.record(16, 2.0)
    clock.advance(4)
    rate.record(16, 2.0)
    assert rate.per_second() == 4.0, (
        "32 passages in the 8 s from the first to the last: waits count"
    )
    clock.advance(1000)
    assert rate.per_second() is None, "what was measured long ago says nothing about now"


def test_a_batch_too_small_to_say_how_fast_the_work_goes_is_not_a_measurement() -> None:
    clock = Clock()
    rate = Throughput(clock=clock)
    rate.record(3, 0.1)
    rate.record(1, 0.1)
    assert rate.per_second() is None, "a few passages promise nothing of thousands"
    rate.record(8, 2.0)
    assert rate.per_second() == 4.0, "eight is enough"


def test_the_speed_is_measured_over_a_stretch_longer_than_the_limits_own_minute() -> None:
    """The limit lets a burst start at once and then holds the rest back, in turn. The last
    dozen batches are all of a burst and say sixteen a second; the whole stretch says less."""
    clock = Clock()
    rate = Throughput(clock=clock)
    for _ in range(30):
        rate.record(16, 1.0)
        clock.advance(1)
    clock.advance(30)
    for _ in range(30):
        rate.record(16, 1.0)
        clock.advance(1)
    assert rate.per_second() == pytest.approx(59 * 16 / 89)
    assert rate.per_second() < 12, "not the sixteen a second of the last dozen batches"


async def test_the_speed_never_promises_more_than_the_requests_a_minute_let_through(
    world: World,
) -> None:
    rate = world.meaning._rates.setdefault(EMBED, Throughput(clock=world.clock))
    for _ in range(12):
        rate.record(16, 1.0)
        world.clock.advance(1)
    assert world.meaning.rate_of(EMBED) == 16.0
    assert world.meaning.rate_of(EMBED, 30) == 8.0, "thirty requests of sixteen in a minute"
    assert world.meaning.rate_of(EMBED, 6000) == 16.0, "a limit that does not bite changes nothing"
    assert world.meaning.rate_of(EMBED, 0) == pytest.approx(16 / 60), "nothing is taken as one"
    assert world.meaning.rate_of("another model", 30) is None, "nothing measured is no number"


async def test_the_time_left_in_the_header_is_made_with_the_speed_the_limit_allows(
    world: World,
) -> None:
    """A live run: nomic did sixteen passages a second for the thirty requests the limit let
    through at once, and the header said 147 seconds left when 257 were."""
    await world.add("doc00001", 200)
    for _ in range(3):
        await world.run(limit=1)
        world.clock.advance(1)
    status = await world.meaning.status(world.index(), ADA)
    assert status.adding["embedded"] == 48
    assert status.adding["etaSeconds"] == pytest.approx(152 / 8), (
        "152 passages left at the 8 a second that 30 requests of 16 a minute allow, not 16"
    )


async def test_a_small_library_does_not_promise_how_long_a_big_one_will_take(world: World) -> None:
    """A live run: a rebuild of 2,810 passages was said to take 36 seconds (it took 5.6
    minutes) because the speed came from three batches of one to four short passages."""
    await world.add("doc00001", 3)
    await world.run(limit=1)
    world.clock.advance(1)
    await world.add("doc00002", 2)
    await world.run(limit=1)
    assert world.meaning.rate_of(EMBED) is None, "five passages in two small batches"
    await world.add("doc00003", 48)
    await world.run(limit=1)
    world.clock.advance(4)
    await world.run(limit=1)
    assert world.meaning.rate_of(EMBED) == 4.0, "two full batches, sixteen in the four seconds"


def test_the_time_to_finish_a_document_counts_what_is_ahead_of_it_and_the_people_waiting() -> None:
    queue = Queue([("a", 100), ("b", 50), ("c", 10)], rate=10.0, people=1)
    assert queue.owed == 160
    assert queue.seconds_to_finish("a") == 10.0
    assert queue.seconds_to_finish("c") == 16.0
    assert queue.seconds_to_finish() == 16.0
    assert queue.seconds_to_finish("missing") is None
    assert Queue(queue.order, 10.0, 3).seconds_to_finish("a") == 30.0, (
        "served in turn with two others"
    )
    assert Queue(queue.order, None, 1).seconds_to_finish("a") is None


# --------------------------------------------------------------------------- #
# the key's limit, a stopped search model, and other failures
# --------------------------------------------------------------------------- #


async def test_a_429_with_retry_after_waits_that_long_and_then_asks_for_the_same_passages(
    world: World,
) -> None:
    await world.add("doc00001", 4)
    await world.warm()
    world.gateway.errors.append(
        HubError("busy", status=429, kind="limit", words="rate limit", retry_after=7.0)
    )
    first = await world.meaning._cycle()
    assert first is not None and 6.5 < first <= 7.0
    assert await world.embedded() == 0 and len(world.gateway.passages()) == 4
    view = (await world.meaning.status(world.index(), ADA)).adding
    assert view["state"] == "limited"
    assert (
        "at its limit (rate limit)" in view["message"] and "carry on by itself" in view["message"]
    )
    assert "raise the key's limits in Eugene's console" in view["message"]
    assert await world.meaning._cycle() is not None, "still inside the wait: it does not ask"
    batches = [c for c in world.gateway.calls if c.inputs != [meaning_module.IDENTIFY_TEXT]]
    assert len(batches) == 1
    world.clock.advance(7.1)
    assert await world.meaning._cycle() == 0.0
    batches = [c for c in world.gateway.calls if c.inputs != [meaning_module.IDENTIFY_TEXT]]
    assert len(batches) == 2 and batches[1].inputs == batches[0].inputs, "the same batch"
    assert await world.status("doc00001") == "ready"
    after = (await world.meaning.status(world.index(), ADA)).adding
    assert after["state"] == "idle" and after["message"] is None


async def test_a_429_without_retry_after_backs_off_and_starts_over_after_a_success(
    world: World,
) -> None:
    await world.add("doc00001", 4)
    await world.warm()
    waits = []
    for _ in range(3):
        world.gateway.errors.append(HubError("busy", status=429, kind="limit", words="x"))
        waits.append(await world.meaning._cycle())
        world.clock.advance(61)
    assert [round(w or 0) for w in waits] == [5, 10, 20]
    assert await world.meaning._cycle() == 0.0, "then it goes through"
    await world.add("doc00002", 4)
    world.gateway.errors.append(HubError("busy", status=429, kind="limit", words="x"))
    assert round(await world.meaning._cycle() or 0) == 5, "a success starts the back-off over"


async def test_a_retry_after_longer_than_five_minutes_is_not_believed(world: World) -> None:
    await world.add("doc00001", 4)
    await world.warm()
    world.gateway.errors.append(
        HubError("busy", status=429, kind="limit", words="x", retry_after=7200)
    )
    wait = await world.meaning._cycle()
    assert wait is not None and wait <= 300.0, "five minutes at most, whatever the gateway asks"


async def test_a_stopped_search_model_is_named_and_waited_for_and_nothing_is_lost(
    world: World,
) -> None:
    await world.add("doc00001", 20)
    await world.run(limit=1)  # one batch goes through, and the model is built with
    world.gateway.errors.append(HubError("No backend for 'embed-model' is ready.", status=503))
    pause = await world.meaning._cycle()
    assert pause is not None and 0 < pause <= meaning_module.RETRY_SECONDS[0]
    said = (await world.meaning.status(world.index(), ADA)).adding
    assert said["state"] == "waiting" and said["owed"] == 4
    assert said["message"] == (
        "Meaning is waiting: the search model embed-model is not running. "
        "Start it in Eugene's console under Backends."
    )
    assert await world.status("doc00001") == "embedding" and await world.embedded() == 16
    world.clock.advance(60)
    assert await world.meaning._cycle() == 0.0
    assert await world.status("doc00001") == "ready" and await world.embedded() == 20


async def test_a_search_model_that_is_listed_but_not_running_gets_no_requests_and_is_waited_for(
    world: World,
) -> None:
    world.gateway.listing = [search_model(EMBED, ready=False)]
    await world.add("doc00001", 4)
    await world.meaning.resume()  # nothing yet: no vector, and nothing was ever in play
    pause = await world.meaning._cycle()
    assert world.gateway.calls == [] and pause is not None and pause > 0
    block = world.meaning._blocked[ADA]
    assert block.code == "not_running" and "embed-model is not running" in block.message
    world.gateway.listing = [search_model(EMBED)]
    world.clock.advance(60)
    assert await world.meaning._cycle() == 0.0, "it looked again, and it is there"


async def test_a_wait_for_a_search_model_ends_when_one_is_listed_and_does_not_outlive_it(
    world: World,
) -> None:
    """The flaky test of issue #6 (found in MR6b round two, with a trace of a failing run): a pass
    met a document that was not yet decided as words only (it was `searchable` for a moment) and
    set a 5 s wait because no search model was there; the model came a moment later and was not
    used for 5 seconds, and what the screen said in the meantime was "no search model is set up"
    beside a model that was listed. A wait for a search model ends when one is there."""
    world.gateway.listing = []
    await world.add("doc00001", 4)  # searchable: counted as owed, and no model in play
    pause = await world.meaning._cycle()
    block = world.meaning._blocked[ADA]
    assert pause is not None and 0 < pause <= 5.0 and block.code == "none_set_up" and block.no_model
    # The model is listed. Somebody looks at the screen: no clock has moved.
    world.gateway.listing = [search_model(EMBED)]
    world.meaning.listing.forget()
    world.meaning._wake.clear()
    await world.meaning.touch(world.index())
    assert world.meaning._wake.is_set(), "a look wakes a worker that is waiting out a block"
    assert await world.meaning._cycle() == 0.0, "and it goes on at once, not when the 5 s are up"
    assert ADA not in world.meaning._blocked and await world.embedded() == 4
    assert world.meaning._failures.get(ADA) is None, "a model that was away is not a failure"


async def test_a_wait_that_a_failed_request_set_is_not_ended_by_the_model_being_listed(
    world: World,
) -> None:
    """The gateway lists the model, and its request failed: that is not the same thing as no model
    being there, so a look does not end the wait (the backoff is what stops a worker that hammers
    a model that fails), and the screen still says what failed."""
    await world.add("doc00001", 4)
    await world.warm()
    world.gateway.errors.append(HubError("No backend for 'embed-model' is ready.", status=503))
    pause = await world.meaning._cycle()
    block = world.meaning._blocked[ADA]
    assert pause and block.code == "not_running" and block.no_model is False
    world.meaning._wake.clear()
    await world.meaning.touch(world.index())
    assert world.meaning._wake.is_set()
    again = await world.meaning._cycle()
    assert again is not None and again > 0, "still waiting: the model is listed, and it failed"
    said = (await world.meaning.status(world.index(), ADA)).adding
    assert said["state"] == "waiting" and "is not running" in said["message"]


async def test_a_request_that_is_held_up_is_work_and_not_a_wait_for_the_search_model(
    world: World,
) -> None:
    """The block was kept until a batch was stored, so a batch that was slow, or held, left every
    document saying "Meaning is waiting: no search model is set up" beside a model that answered."""
    world.gateway.listing = []
    await world.add("doc00001", 4)
    await world.meaning._cycle()  # no model: blocked
    assert world.meaning._blocked[ADA].no_model
    world.gateway.listing = [search_model(EMBED)]
    world.meaning.listing.forget()
    world.clock.advance(60)  # its wait is over: nothing here is the lift of a look
    await world.warm()
    world.gateway.gate = asyncio.Event()  # and the batch is held up
    turn = asyncio.create_task(world.meaning._cycle())
    for _ in range(200):
        if world.gateway.in_flight:
            break
        await asyncio.sleep(0.01)
    assert world.gateway.in_flight == 1
    assert ADA not in world.meaning._blocked, "a request in the air is work, not a wait"
    status = await world.meaning.status(world.index(), ADA)
    assert status.adding["state"] == "working" and status.code == "ok"
    doc = await world.index().document("doc00001")
    assert doc is not None
    row = world.meaning.document_view(doc, status.queue, status)
    assert row is not None and row["state"] == "adding"
    world.gateway.gate.set()
    assert await asyncio.wait_for(turn, 15) == 0.0


async def test_the_header_does_not_say_a_search_model_is_not_there_when_one_is_running(
    world: World,
) -> None:
    await world.add("doc00001", 4)
    await world.warm()
    world.meaning._blocked[ADA] = meaning_module.Block(
        "none_set_up",
        "Searching by words only: no search model is set up.",
        "waiting",
        9999.0,
        True,
    )
    status = await world.meaning.status(world.index(), ADA)
    assert status.code == "ok" and status.adding["state"] == "working", "the model is listed"
    assert "no search model is set up" not in (status.adding["message"] or "")


async def test_each_other_failure_waits_and_says_what_was_observed(world: World) -> None:
    await world.add("doc00001", 4)
    await world.warm()
    world.gateway.errors.append(HubError("boom", status=500, words="boom"))
    assert round(await world.meaning._cycle() or 0) == 5
    said = world.meaning._blocked[ADA]
    assert (
        said.code == "error"
        and said.message == "Searching by words only: the search model embed-model failed (boom)."
    )


# --------------------------------------------------------------------------- #
# no two search models' numbers in one index
# --------------------------------------------------------------------------- #


async def test_a_search_model_that_changed_is_refused_and_nothing_is_stored_until_rebuild(
    world: World,
) -> None:
    await world.add("doc00001", 4)
    await world.run()
    before = await world.embedded()
    assert before == 4 and (await world.built()) is not None
    world.gateway.names[EMBED] = "another-model"
    await world.add("doc00002", 4)
    assert await world.meaning._cycle() is None, (
        "refused, so this person is paused: nothing owed to wait for"
    )
    assert len(world.gateway.passages()) == 8, "one batch of new passages was asked, and refused"
    # Not one of the new document's passages has a vector, and the index still says what it
    # was built with.
    assert await world.embedded() == before
    built = await world.built()
    assert built is not None and built.model == EMBED
    # What a person is told, once the memory of the last answer has passed:
    world.clock.advance(meaning_module.SEEN_TTL + 1)
    status = await world.meaning.status(world.index(), ADA)
    assert status.code == "changed" and not status.meaning and status.actions == ("rebuild",)
    assert status.headline == (
        "Search by meaning is paused: this search was built with embed-model and "
        "another-model is running now."
    )
    assert "searched by words until you choose Rebuild" in (status.detail or "")
    assert (
        status.adding["state"] == "waiting"
        and status.adding["message"] == "Meaning is paused until you choose Rebuild."
    )
    # The search goes on by words, and says why meaning did not run.
    with pytest.raises(SignalUnavailable) as caught:
        await world.meaning.signal.search(
            world.index(), plan_query("anything at all"), [LIBRARY], 5
        )
    assert caught.value.code == "changed" and not caught.value.quiet


async def test_rebuild_clears_what_the_old_model_made_and_adds_meaning_again_with_the_new_one(
    world: World,
) -> None:
    await world.add("doc00001", 4)
    await world.run()
    world.gateway.names[EMBED] = "another-model"
    await world.add("doc00002", 4)
    world.clock.advance(meaning_module.SEEN_TTL + 1)
    assert (await world.meaning.status(world.index(), ADA)).code == "changed"
    owed = await world.meaning.rebuild(ADA)
    assert owed == 8 and await world.embedded() == 0 and (await world.built()) is None
    assert (
        await world.status("doc00001") == "searchable"
        and await world.status("doc00002") == "searchable"
    )
    await world.run()
    built = await world.built()
    assert built is not None and built.model == "another-model"
    matrix = await world.index().load_vectors(built.dim)
    assert matrix.size == 8, "every passage again, all from the model that is running"
    status = await world.meaning.status(world.index(), ADA)
    assert (
        status.code == "ok"
        and status.meaning
        and status.headline == "Searching by meaning and words."
    )


async def test_the_same_model_at_another_size_is_refused_too(world: World) -> None:
    await world.add("doc00001", 4)
    await world.run()
    world.gateway.dims[EMBED] = 6
    await world.add("doc00002", 4)
    await world.meaning._cycle()
    assert await world.embedded() == 4
    world.clock.advance(meaning_module.SEEN_TTL + 1)
    status = await world.meaning.status(world.index(), ADA)
    assert status.code == "dimension" and status.headline == (
        "Search by meaning is paused: embed-model now gives 6 numbers for each passage, "
        "and this search was built with 4."
    )


async def test_a_rebuild_while_a_request_is_in_the_air_stores_nothing_of_it(world: World) -> None:
    await world.add("doc00001", 4)
    await world.warm()
    world.gateway.gate = asyncio.Event()
    turn = asyncio.create_task(world.meaning._cycle())
    for _ in range(200):
        if world.gateway.in_flight:
            break
        await asyncio.sleep(0.01)
    assert world.gateway.in_flight == 1
    await world.meaning.rebuild(ADA)  # ends the request in the air
    assert await asyncio.wait_for(turn, 15) == 0.0 and world.gateway.cancelled == 1
    assert await world.embedded() == 0


# --------------------------------------------------------------------------- #
# vectors that cannot be searched, and passages that are too long
# --------------------------------------------------------------------------- #


async def test_numbers_that_are_not_numbers_are_never_stored_not_even_those_beside_them(
    world: World,
) -> None:
    await world.add("doc00001", 20)
    world.gateway.nan = "Paragraph 3 "
    pause = await world.meaning._cycle()
    assert pause is not None and pause > 0 and await world.embedded() == 0
    said = world.meaning._blocked[ADA]
    assert (
        said.code == "unusable"
        and "gave numbers Workbench cannot use (a vector holds a number that is not a number)"
        in said.message
    )


async def test_a_passage_too_long_for_the_search_model_is_refused_alone_and_the_rest_are_kept(
    world: World,
) -> None:
    world.gateway.listing = [search_model("Qwen3-Embedding-0.6B-Q8_0")]
    world.gateway.refuse = "TOOLONG"
    sections = [f"# H{n}\n\nParagraph {n}." for n in range(16)]
    sections[5] = "# H5\n\nTOOLONG " + "word " * 40
    await world.add("doc00001", text="\n\n".join(sections))
    await world.run()
    found = await world.index().document("doc00001")
    assert found is not None and found.status == "ready" and found.embedded == 16
    assert len(found.refused) == 1 and found.refused[0]["ord"] == 5
    sentence = found.refused[0]["why"]
    assert sentence.startswith("The search model refused one passage (")
    assert "it said: input (1631 tokens) is too large to process" in sentence
    assert "32,768" not in sentence, "what the engine said, never the table's limit for the model"
    assert sentence.endswith("skipped doc00001.md.")
    matrix = await world.index().load_vectors(4)
    assert matrix.size == 15, "15 are searched by meaning, and the refused one by words only"
    asked = len(world.gateway.calls)
    assert asked <= 12, "found by halving the batch, not one at a time"
    await world.meaning._cycle()
    assert len(world.gateway.calls) == asked, "a refused passage is never asked for again"


async def test_a_refusal_for_length_that_comes_as_a_500_is_found_the_same_way(world: World) -> None:
    sections = [f"# H{n}\n\nParagraph {n}." for n in range(4)]
    sections[2] = "# H2\n\nTOOLONG"
    world.gateway.refuse = "TOOLONG"
    world.gateway.refuse_status = 500
    await world.add("doc00001", text="\n\n".join(sections))
    await world.run()
    found = await world.index().document("doc00001")
    assert found is not None and found.status == "ready" and len(found.refused) == 1
    assert "it said: input (1631 tokens) is too large to process" in found.refused[0]["why"], (
        "an unknown model: what it said"
    )


def test_what_the_engine_itself_said_is_taken_out_of_the_words_the_gateway_wraps_it_in() -> None:
    # Word for word what a real llama-server said through the gateway and a driver.
    live = HubError(
        "Eugene's gateway refused the request.",
        status=400,
        words=(
            "The backend rejected the request: openai_compat_http returned 400 for embeddings: "
            '{"error":{"code":400,"message":"request (1072 tokens) exceeds the available context '
            'size (1024 tokens), try increasing it","type":"exceed_context_size_error",'
            '"n_prompt_tokens":1072,"n_ctx":1024}}'
        ),
    )
    assert meaning_module.engine_words(live) == (
        "request (1072 tokens) exceeds the available context size (1024 tokens), try increasing it"
    )
    escaped = HubError("x", words='failed: {"error":{"message":"a \\"quoted\\" word"}}')
    assert meaning_module.engine_words(escaped) == 'a "quoted" word'
    plain = HubError("Eugene's gateway said no.", status=400)
    assert meaning_module.engine_words(plain) == "Eugene's gateway said no."
    assert meaning_module.engine_words(HubError("x", words="  spaced out  ")) == "spaced out"


async def test_the_note_on_a_refused_passage_carries_the_limit_the_engine_gave_not_the_tables(
    world: World,
) -> None:
    """A real engine is started with a context of its own (1,024 tokens a slot, here) that is
    not the model's own limit in the table (32,768): the note says what was observed."""
    world.gateway.listing = [search_model("Qwen3-Embedding-0.6B-Q8_0")]
    world.gateway.refuse = "TOOLONG"
    world.gateway.refuse_words = (
        'The backend rejected the request: openai_compat_http returned 400 for embeddings: {"error":'
        '{"code":400,"message":"request (1072 tokens) exceeds the available context size '
        '(1024 tokens), try increasing it","type":"exceed_context_size_error"}}'
    )
    await world.add("doc00001", text="# H0\n\nShort.\n\n# H1\n\nTOOLONG and more.")
    await world.run()
    found = await world.index().document("doc00001")
    assert found is not None and len(found.refused) == 1
    assert found.refused[0]["why"] == (
        "The search model refused one passage (17 characters; it said: request (1072 tokens) "
        "exceeds the available context size (1024 tokens), try increasing it): skipped "
        "doc00001.md."
    ), found.refused[0]["why"]


async def test_a_failure_while_looking_for_the_long_passage_keeps_what_was_found_and_waits(
    world: World,
) -> None:
    sections = [f"# H{n}\n\nParagraph {n}." for n in range(4)]
    sections[3] = "# H3\n\nTOOLONG"
    await world.add("doc00001", text="\n\n".join(sections))
    world.gateway.refuse = "TOOLONG"
    await world.warm()
    original = world.gateway.embeddings
    calls = {"n": 0}

    async def flaky(model: str, inputs: Sequence[str], *, seconds: float = 120.0) -> Embedded:
        calls["n"] += 1
        if (
            calls["n"] == 3
        ):  # the batch is refused (1), its first half goes (2), its second fails (3)
            raise HubError("down", status=503)
        return await original(model, inputs, seconds=seconds)

    world.gateway.embeddings = flaky  # type: ignore[method-assign]
    await world.meaning._cycle()
    assert await world.embedded() >= 2, "the half that was embedded is kept"
    assert world.meaning._blocked[ADA].code == "not_running"


#: What the gateway says to a request that comes while a backend that failed is cooling down: a 503
#: that asks for a second's wait (measured for a result sorter, MR6b acceptance).
COOLING_DOWN = (
    "A backend serving this model is not ready yet: Eligible backends are cooling down or "
    "already probing recovery. Try a new request later.. If an engine is still loading its "
    "weights this will clear on its own."
)


def test_a_503_that_asks_for_a_wait_is_the_gateway_holding_requests_back_and_not_a_stopped_model() -> (
    None
):
    """Measured live for a result sorter, and the same reading was in this module: after a model's
    engine was started again, the gateway held the next requests back for a second with a 503 and a
    `Retry-After`, and Workbench said the model was not running and to start it, of a model that
    was. A 503 that asks for a wait is the gateway's wait, said in its own words; one that does not
    is a model that is not ready, as ever."""
    held = HubError("x", status=503, words=COOLING_DOWN, retry_after=1.0)
    code, words = meaning_module.problem(held, EMBED)
    assert code == "error"
    assert words == (
        "Searching by words only: Eugene's gateway asked Workbench to wait 1 s before asking the "
        f"search model embed-model again ({COOLING_DOWN}), so a question could not be searched by "
        "meaning."
    )
    assert "is not running" not in words and "Start it" not in words
    assert meaning_module.problem(
        HubError("x", status=503, words=COOLING_DOWN, retry_after=12.0), EMBED
    )[1].startswith("Searching by words only: Eugene's gateway asked Workbench to wait 12 s ")
    for plain in (HubError("x", status=503), HubError("x", status=404, retry_after=3.0)):
        again, said = meaning_module.problem(plain, EMBED)
        assert again == "not_running" and "the search model embed-model is not running" in said


async def test_a_worker_held_back_by_the_gateway_says_so_and_does_not_say_the_model_is_stopped(
    world: World,
) -> None:
    await world.add("doc00001", 4)
    await world.warm()
    world.gateway.errors.append(HubError("x", status=503, words=COOLING_DOWN, retry_after=1.0))
    await world.meaning._cycle()
    blocked = world.meaning._blocked[ADA]
    assert blocked.code == "error"
    assert "asked Workbench to wait 1 s" in blocked.message
    assert "is not running" not in blocked.message


# --------------------------------------------------------------------------- #
# a removal, a stop, and who is served
# --------------------------------------------------------------------------- #


async def test_removing_the_document_a_request_is_only_for_ends_the_request(world: World) -> None:
    await world.add("doc00001", 4)
    await world.warm()
    world.gateway.gate = asyncio.Event()
    turn = asyncio.create_task(world.meaning._cycle())
    for _ in range(200):
        if world.gateway.in_flight:
            break
        await asyncio.sleep(0.01)
    assert world.gateway.in_flight == 1
    await world.meaning.cancel(ADA, ["doc00001"])
    await world.index().delete("doc00001")
    assert await asyncio.wait_for(turn, 15) == 0.0
    assert world.gateway.cancelled == 1 and world.gateway.in_flight == 0, "the key's slot is free"
    assert await world.index().owed_by_document() == []
    await world.add("doc00002", 4)
    world.gateway.gate = None
    await world.run()
    assert await world.status("doc00002") == "ready", "and the worker went on"


async def test_a_request_that_holds_another_documents_passages_too_is_left_to_finish(
    world: World,
) -> None:
    await world.add("doc00001", 2)
    await world.add("doc00002", 2)
    await world.warm()
    world.gateway.gate = asyncio.Event()
    turn = asyncio.create_task(world.meaning._cycle())
    for _ in range(200):
        if world.gateway.in_flight:
            break
        await asyncio.sleep(0.01)
    await world.meaning.cancel(ADA, ["doc00001"])
    assert world.gateway.cancelled == 0, "it is also for doc00002"
    await world.index().delete("doc00001")
    world.gateway.gate.set()
    assert await asyncio.wait_for(turn, 15) == 0.0
    assert await world.status("doc00002") == "ready" and await world.embedded() == 2
    assert await world.index().document("doc00001") is None


async def test_a_stop_in_the_middle_is_picked_up_at_boot_from_the_passages_without_a_vector(
    tmp_path: Path,
) -> None:
    made = World(tmp_path)
    try:
        await made.add("doc00001", 40)
        await made.run(limit=1)  # one batch of sixteen, and then Workbench stops
        assert await made.embedded() == 16 and await made.status("doc00001") == "embedding"
    finally:
        await made.meaning.aclose()
        await made.indexes.aclose()
    again = World(tmp_path)
    try:
        assert await again.meaning.resume() == 1, "this person has passages owed"
        await again.run()
        assert await again.status("doc00001") == "ready" and await again.embedded() == 40
        asked = again.gateway.passages()
        assert len(asked) == 24, "only the 24 that had no vector: none is embedded twice"
        assert not any("Paragraph 0 of" in t for t in asked)
    finally:
        await again.meaning.aclose()
        await again.indexes.aclose()


async def test_the_boot_counts_a_documents_passages_again_if_it_had_them_wrong(
    tmp_path: Path,
) -> None:
    made = World(tmp_path)
    try:
        await made.add("doc00001", 4)
        await made.run()
        from .test_knowledge_index import raw

        with raw(made.index()) as db:
            db.execute("UPDATE documents SET embedded = 1")
            db.commit()
        assert await made.meaning.resume() == 0
        assert await made.embedded() == 4
    finally:
        await made.meaning.aclose()
        await made.indexes.aclose()


async def test_documents_are_served_in_turn_one_batch_each_and_a_chats_own_files_first(
    world: World,
) -> None:
    await world.add("doc00001", 32, sub=ADA)
    await world.add("doc00002", 32, sub=BO)
    await world.add("doc00003", 16, sub=ADA, scope=chat_scope("chat-1"))
    await world.run()
    served = list(world.meaning.served)
    assert [s for s, _ in served] == [ADA, BO, ADA, BO, ADA], (
        "her 48 and his 32 are 3 and 2 batches"
    )
    assert world.gateway.passages()[0].startswith("H0\nParagraph 0 of doc00003"), (
        "her chat's file first"
    )
    assert (
        await world.status("doc00003") == "ready" and await world.status("doc00002", BO) == "ready"
    )


async def test_nothing_is_added_while_the_owner_has_documents_off(world: World) -> None:
    await world.add("doc00001", 4)
    world.settings.enabled = False
    assert await world.meaning._cycle() is None and world.gateway.calls == []
    view = (await world.meaning.status(world.index(), ADA)).adding
    assert view["state"] == "waiting" and "turned off documents" in view["message"]
    world.settings.enabled = True
    assert await world.meaning._cycle() == 0.0


# --------------------------------------------------------------------------- #
# who is allowed to be asked
# --------------------------------------------------------------------------- #


async def test_text_is_never_sent_to_a_model_outside_the_machines_unless_it_is_named_and_allowed(
    world: World,
) -> None:
    world.gateway.listing = [search_model("cloud-embed", locality="external", owned_by="openai")]
    await world.add("doc00001", 4)
    await world.meaning._cycle()
    assert world.gateway.calls == [], "auto never picks it, allowed or not"
    world.settings.external_embedding = True
    world.clock.advance(60)
    await world.meaning._cycle()
    assert world.gateway.calls == []
    world.settings.embedding_model = "cloud-embed"
    world.settings.external_embedding = False
    world.clock.advance(60)
    await world.meaning._cycle()
    assert world.gateway.calls == [], "named, but the owner has not allowed it"
    world.settings.external_embedding = True
    world.clock.advance(60)
    assert await world.meaning._cycle() == 0.0
    assert {c.model for c in world.gateway.calls} == {"cloud-embed"} and await world.embedded() == 4


async def test_with_no_search_model_ever_in_play_nobody_is_polled(world: World) -> None:
    world.gateway.listing = []
    await world.add("doc00001", 4)
    await world.index().finish("doc00001")  # read with no search model: ready by words
    world.meaning.wake(ADA)
    assert await world.meaning._cycle() is None, "nothing is owed, so there is nothing to wait for"
    assert world.gateway.calls == [] and ADA not in world.meaning._owing


async def test_a_document_is_owed_its_meaning_only_when_a_search_model_is_in_play(
    world: World,
) -> None:
    await world.add("doc00001", 4)
    doc = await world.index().document("doc00001")
    assert doc is not None
    assert await world.meaning.owed(world.index(), doc) is True and ADA in world.meaning._owing
    world.gateway.listing = []
    world.meaning.listing.forget()
    world.meaning._owing.clear()
    assert await world.meaning.owed(world.index(), doc) is False and not world.meaning._owing
    empty = doc.__class__(**{**doc.__dict__, "chunks": 0})
    world.gateway.listing = [search_model(EMBED)]
    world.meaning.listing.forget()
    assert await world.meaning.owed(world.index(), empty) is False, "no passages, nothing owed"


async def test_a_search_model_started_after_the_documents_were_read_is_noticed_when_someone_looks(
    world: World,
) -> None:
    world.gateway.listing = []
    await world.add("doc00001", 4)
    await world.index().finish("doc00001")  # ready by words: no model then
    world.meaning._owing.clear()
    world.gateway.listing = [search_model(EMBED)]
    world.meaning.listing.forget()
    status = await world.meaning.status(world.index(), ADA)  # the person opens the screen
    assert status.code == "ok" and ADA in world.meaning._owing, "so the worker is woken"
    await world.run()
    assert await world.status("doc00001") == "ready" and await world.embedded() == 4


# --------------------------------------------------------------------------- #
# what the header says, for each reason
# --------------------------------------------------------------------------- #

WORDS = "Searching by words only. No search model is running."


@pytest.mark.parametrize(
    ("setup", "code", "headline", "detail", "actions"),
    [
        (
            lambda w: setattr(w.gateway, "listing", []),
            "none_set_up",
            WORDS,
            "Searching by words only: no search model is set up.",
            ["get_search_model"],
        ),
        (
            lambda w: setattr(w.gateway, "listing", [search_model(EMBED, ready=False)]),
            "not_running",
            WORDS,
            "Searching by words only: the search model embed-model is not running. "
            "Start it in Eugene's console under Backends.",
            [],
        ),
        (
            lambda w: setattr(w.settings, "embedding_model", "no-such-model"),
            "not_listed",
            WORDS,
            "Searching by words only: Eugene lists no search model named no-such-model. "
            "Check the Search model setting in Eugene's console.",
            [],
        ),
        (
            lambda w: setattr(
                w.gateway,
                "listing",
                [search_model("cloud", locality="external", owned_by="openai")],
            ),
            "outside_only",
            WORDS,
            None,
            [],
        ),
        (
            lambda w: setattr(
                w.gateway,
                "listing_error",
                HubError(
                    "Workbench cannot reach Eugene's gateway at http://x: refused",
                    status=503,
                    kind="unreachable",
                ),
            ),
            "unreachable",
            "Searching by words only.",
            "Searching by words only: Workbench cannot reach Eugene's gateway at http://x: refused",
            [],
        ),
        (
            lambda w: setattr(
                w.gateway,
                "listing_error",
                HubError("Eugene refused the key this Workbench uses.", status=401, kind="key"),
            ),
            "key",
            "Searching by words only.",
            "Searching by words only: Eugene refused the key this Workbench uses.",
            [],
        ),
    ],
)
async def test_the_header_says_why_it_is_by_words_only_for_each_reason(
    world: World,
    setup: Callable[[World], None],
    code: str,
    headline: str,
    detail: str | None,
    actions: list[str],
) -> None:
    await world.add("doc00001", 2)
    setup(world)
    status = await world.meaning.status(world.index(), ADA)
    assert (status.code, status.headline, status.actions) == (code, headline, tuple(actions))
    assert not status.meaning and status.mode == "words"
    if detail is not None:
        assert status.detail == detail
    else:
        assert "the owner has not allowed" in (status.detail or "")


async def test_the_header_is_by_meaning_and_words_when_the_search_model_answers(
    world: World,
) -> None:
    await world.add("doc00001", 2)
    status = await world.meaning.status(world.index(), ADA)
    assert status.code == "ok" and status.meaning and status.mode == "meaning and words"
    assert status.headline == "Searching by meaning and words." and status.detail is None
    assert (
        status.model["label"] == "In use: embed-model (automatic)"
        and status.model["resolved"] == EMBED
    )
    named = Settings(embedding_model=EMBED)
    world.settings = named
    again = await world.meaning.status(world.index(), ADA)
    assert again.model["label"] == "In use: embed-model", "named, so not 'automatic'"


async def test_the_key_at_its_limit_when_the_header_asks_is_said_and_search_is_by_words(
    world: World,
) -> None:
    await world.add("doc00001", 2)
    await world.run()
    world.gateway.errors.append(
        HubError("busy", status=429, kind="limit", words="60 requests per minute")
    )
    world.clock.advance(meaning_module.SEEN_TTL + 1)
    status = await world.meaning.status(world.index(), ADA)
    assert status.code == "limited" and not status.meaning
    assert "this Workbench's key is at its limit (60 requests per minute)" in (status.detail or "")


async def test_an_unknown_search_model_is_used_without_prefixes_and_the_header_says_so(
    world: World,
) -> None:
    world.gateway.listing = [search_model("bge-m3-q8_0")]
    await world.add("doc00001", 2)
    await world.run()
    status = await world.meaning.status(world.index(), ADA)
    prefixes = status.model["prefixes"]
    assert prefixes["known"] is False and prefixes["query"] == "" and prefixes["document"] == ""
    assert "does not know which prefixes bge-m3-q8_0 wants" in prefixes["note"]
    assert world.gateway.passages()[0] == "H0\nParagraph 0 of doc00001."


async def test_a_known_search_model_says_which_family_and_what_prefixes_it_gets(
    world: World,
) -> None:
    world.gateway.listing = [search_model("nomic-embed-text-v1.5.Q8_0")]
    await world.add("doc00001", 2)
    await world.run()
    prefixes = (await world.meaning.status(world.index(), ADA)).model["prefixes"]
    assert (
        prefixes["known"] and prefixes["family"] == "nomic-embed-text" and prefixes["note"] is None
    )
    assert (prefixes["query"], prefixes["document"]) == ("search_query: ", "search_document: ")
    assert world.gateway.passages()[0] == "search_document: H0\nParagraph 0 of doc00001."


# --------------------------------------------------------------------------- #
# a document's row
# --------------------------------------------------------------------------- #


async def test_a_documents_row_says_how_much_of_its_meaning_is_added_and_how_long_is_left(
    world: World,
) -> None:
    await world.add("doc00001", 48)
    await world.run(limit=1)
    world.clock.advance(4)
    await world.run(limit=1)
    status = await world.meaning.status(world.index(), ADA)
    doc = await world.index().document("doc00001")
    assert doc is not None
    row = world.meaning.document_view(doc, status.queue, status)
    assert row is not None
    assert (row["state"], row["embedded"], row["total"]) == ("adding", 32, 48)
    assert row["etaSeconds"] is not None and row["etaSeconds"] > 0
    assert row["message"].startswith("Ready for words. Adding meaning: 32 of 48 passages, ")
    assert row["message"].endswith(" left.")
    assert status.adding["etaSeconds"] == row["etaSeconds"]


async def test_the_time_left_is_not_a_number_until_a_batch_has_been_measured(world: World) -> None:
    await world.add("doc00001", 48)
    status = await world.meaning.status(world.index(), ADA)
    doc = await world.index().document("doc00001")
    assert doc is not None
    row = world.meaning.document_view(doc, status.queue, status)
    assert row is not None and row["etaSeconds"] is None
    assert row["message"] == "Ready for words. Adding meaning: 0 of 48 passages."


async def test_a_finished_documents_row_says_ready_and_one_read_without_meaning_says_words_only(
    world: World,
) -> None:
    await world.add("doc00001", 2)
    await world.run()
    await world.add("doc00002", 2)
    await world.index().finish("doc00002")  # ready, but no vector: read while no model was in play
    status = await world.meaning.status(world.index(), ADA)
    done = await world.index().document("doc00001")
    plain = await world.index().document("doc00002")
    assert done is not None and plain is not None
    assert world.meaning.document_view(done, status.queue, status)["state"] == "ready"  # type: ignore[index]
    assert world.meaning.document_view(plain, status.queue, status)["state"] == "words_only"  # type: ignore[index]


async def test_a_document_that_is_not_read_has_no_meaning_to_say_anything_of(world: World) -> None:
    from eugene_plexus_workbench.knowledge.index import Document

    queued = Document(
        "d", "library", "x.md", "text/markdown", 1, "s", "upload", "queued", "markdown", 1.0
    )
    assert world.meaning.document_view(queued, Queue(), None) is None


async def test_a_waiting_documents_row_says_why_it_waits(world: World) -> None:
    await world.add("doc00001", 4)
    world.gateway.listing = [search_model(EMBED, ready=False)]
    world.meaning.listing.forget()
    status = await world.meaning.status(world.index(), ADA)
    doc = await world.index().document("doc00001")
    assert doc is not None
    row = world.meaning.document_view(doc, status.queue, status)
    assert row is not None and row["state"] == "waiting"
    assert row["message"] == (
        "Ready for words. Meaning is waiting: the search model embed-model is not running. "
        "Start it in Eugene's console under Backends."
    )
