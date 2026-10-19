"""Search by meaning, and adding meaning to documents (MR5, design 4.1, 4.2, 4.4, 4.9, 5.3, 5.7).

Words find a passage that has the words of the question. Meaning finds one that says what
the question asks, in other words. Both are used together, and a person is told which found
what (`via`: `words`, `meaning` or `both`). **The words search never depends on this**: a
search model that is stopped, changed, at its limit or never set up costs a person the meaning
half and nothing else, and they are told, in words, that this answer was searched by words only.

**Adding meaning** (`Meaning`, one worker for every person, started with Workbench): a document
becomes searchable by words as soon as it is read, and its passages are embedded in the background
(`queued` -> `reading` -> `searchable` -> `embedding` -> `ready`). One request is in flight at a
time, at most `documentsEmbedRequestsPerMinute` (30) a minute, 16 passages each: half of what a
new key allows, so a chat never waits behind an import. A `Retry-After` is obeyed. A person's
documents are served in turn with everyone else's, a chat's own files first. It resumes at boot
from the passages that still have no vector, and removing a document ends the request that was
only for it. How long it will take is said from what the first batches measured, never a constant.

**No two search models' numbers are ever in one index** (`embedders.Identity`). The model the
gateway says answered, and the size of its vectors, are written with the first vectors and checked
against every answer after, for the question as for the passages. When they differ the search
by meaning is *paused* for that person: nothing is stored, nothing is searched by meaning, the
words search goes on, and `Rebuild` (`Meaning.rebuild`) clears the vectors and adds meaning
again with the model that is running, while words are never taken away.

**What is asked of the gateway**: `GET /v1/models` (cached ten seconds) for which search models
there are, where they run and whether they are ready, and `POST /v1/embeddings` with the app's
key. A model that runs outside the machines is used only if the owner allowed it and named it
(`embedders.choose`), and the same rule holds for the question as for the passages.

Logs say counts, timings and causes: never a document's name or its words.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from .._generated.models import ConfigFieldStatus, ConfigFieldStatusLevel
from ..hub import Embedded, HubError
from . import embedders, vectors
from .embedders import Choice, Identity, SearchModel
from .index import (
    LIBRARY,
    READY,
    Document,
    Hit,
    Index,
    Indexes,
    IndexUnavailable,
    MeaningCounts,
    Pending,
)
from .listing import Listing, Probe
from .search import Query, SignalUnavailable

log = logging.getLogger(__name__)

#: How long what a search model answered to is kept before a tiny request asks again. Every
#: real answer renews it, so while documents are being embedded it never needs to ask.
SEEN_TTL = 60.0
IDENTIFY_TEXT = "ping"
#: Passages in one request, and the seconds a request may take.
BATCH = 16
BATCH_TIMEOUT = 120.0
QUESTION_TIMEOUT = 20.0
#: A question that is refused for a moment (`Retry-After` this short) is asked again once.
QUESTION_RETRY_MAX = 3.0
#: The window the requests a minute are counted over.
WINDOW = 60.0
#: How long to wait before looking again when the search model is not there, growing; and
#: when a request fails some other way.
RETRY_SECONDS = (5.0, 10.0, 20.0, 30.0)
BACKOFF_SECONDS = (5.0, 10.0, 20.0, 40.0, 60.0)
LIMIT_MAX_WAIT = 300.0
#: How often the matrices that nobody is using are let go of.
SWEEP_SECONDS = 60.0
#: The time left is measured from the batches of the last `RATE_WINDOW` seconds of work (at most
#: `RATE_BATCHES` of them), and only while the last of them is no older than `RATE_MAX_AGE`. The
#: window is longer than the limit's own minute: a limit that lets a burst start at once and
#: then holds the rest back would, over a few batches, promise the speed of the burst (a live run
#: said 147 seconds with 257 left, and 48 with 80 left, from the last twelve batches).
RATE_WINDOW = 90.0
RATE_BATCHES = 400
RATE_MAX_AGE = 300.0
#: A batch of fewer passages than this says nothing of the speed: its time is mostly the cost of
#: one request, and a few of them would promise thousands of passages in seconds (a live run
#: said 36 seconds for a rebuild that took 5.6 minutes, from three batches of one or two).
RATE_MIN_BATCH = 8

#: What a search model says when a passage is too long for it (llama-server: "input (1631
#: tokens) is too large to process"; "exceeds the available context size"), on any status.
_TOO_LONG = re.compile(
    r"too (large|long|big)|exceed|longer than|batch size|context (size|length|window)|"
    r"maximum (context|input|sequence)|max(imum)? (input )?(tokens|length)",
    re.IGNORECASE,
)

#: The search model's own `message`, when the gateway passed its JSON error on inside its words.
_ENGINE_MESSAGE = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"')

WORDS_ONLY = "Searching by words only: "


class Gateway(Protocol):
    """What search by meaning needs of the gateway: `hub.Hub` is one."""

    async def models(self) -> dict[str, Any]: ...

    async def embeddings(
        self, model: str, inputs: Sequence[str], *, seconds: float = 120.0
    ) -> Embedded: ...


class Settings(Protocol):
    """What search by meaning needs of the install's settings (`config.DocumentSettings`)."""

    @property
    def enabled(self) -> bool: ...
    @property
    def embedding_model(self) -> str: ...
    @property
    def external_embedding(self) -> bool: ...
    @property
    def embed_per_minute(self) -> int: ...


SettingsReader = Callable[[], Awaitable[Settings]]


# --------------------------------------------------------------------------- #
# how long things take
# --------------------------------------------------------------------------- #


class RateLimiter:
    """At most `limit` requests in any `window` seconds: `wait` returns when one may start,
    and counts it. The limit is given each time, so a changed setting is in effect at once."""

    def __init__(
        self,
        window: float = WINDOW,
        *,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.window = window
        self._clock = clock
        self._sleep = sleep
        self._starts: deque[float] = deque()

    def _trim(self, now: float) -> None:
        while self._starts and now - self._starts[0] >= self.window:
            self._starts.popleft()

    async def wait(self, limit: int) -> float:
        """Wait until a request may start, count it, and return the seconds waited."""
        waited = 0.0
        while True:
            now = self._clock()
            self._trim(now)
            if len(self._starts) < max(1, limit):
                self._starts.append(now)
                return waited
            pause = self.window - (now - self._starts[0])
            await self._sleep(pause)
            waited += pause

    def used(self) -> int:
        """Requests started in the last window."""
        self._trim(self._clock())
        return len(self._starts)


class Throughput:
    """How fast passages are being embedded, measured from the batches just done: the
    passages of every batch after the first, over the time from the first to the last, which
    counts every wait the limits made; or, from one batch, its own passages over its own time."""

    def __init__(self, *, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._done: deque[tuple[float, int, float]] = deque(maxlen=RATE_BATCHES)

    def record(self, passages: int, seconds: float) -> None:
        """A batch done. One too small to say how fast the work goes (`RATE_MIN_BATCH`) is
        not a measurement."""
        if passages >= RATE_MIN_BATCH:
            self._done.append((self._clock(), passages, seconds))

    def per_second(self) -> float | None:
        """Passages a second over the last `RATE_WINDOW` seconds of work, or None until a batch
        has been measured (or when the last was long ago: whatever was measured then says
        nothing about now)."""
        now = self._clock()
        fresh = [d for d in self._done if now - d[0] <= RATE_MAX_AGE]
        if not fresh:
            return None
        recent = [d for d in fresh if fresh[-1][0] - d[0] <= RATE_WINDOW]
        if len(recent) == 1:
            _, passages, seconds = recent[0]
            return passages / seconds if seconds > 0 else None
        span = recent[-1][0] - recent[0][0]
        counted = sum(p for _, p, _ in recent[1:])
        return counted / span if span > 0 else None


# --------------------------------------------------------------------------- #
# what the gateway says about the search models, and what a person is told
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Resolved:
    """Which search model is in use now, or why none."""

    choice: Choice
    probe: Probe

    @property
    def model(self) -> SearchModel | None:
        return self.choice.model

    @property
    def code(self) -> str:
        return self.choice.code

    @property
    def message(self) -> str | None:
        return self.choice.message


@dataclass(frozen=True)
class Seen:
    """What a search model answered to the last time it was asked: the model the gateway
    named, and how many numbers each of its vectors has."""

    model: str
    dim: int
    at: float


class Outcome(StrEnum):
    WORKED = "worked"
    DONE = "done"
    BLOCKED = "blocked"


@dataclass
class Block:
    """Why adding meaning to one person's documents is waiting, in words."""

    code: str
    message: str
    #: `waiting` (the search model is not there) or `limited` (the key is at its limit).
    state: str
    until: float
    #: Set because the gateway lists no search model that can be used, as against a request that
    #: failed: the first ends when a model is listed, and the second only when it is tried again.
    no_model: bool = False


def too_long(exc: HubError) -> bool:
    """Whether the gateway refused a request for being too long: a passage too long for the
    search model, which no waiting will change."""
    if exc.status in (413, 422):
        return True
    said = " ".join(filter(None, (exc.words, exc.message)))
    return exc.status in (400, 500, 502) and _TOO_LONG.search(said) is not None


def engine_words(exc: HubError) -> str:
    """What the search model itself said, when the gateway passed its error on whole
    (`... returned 400 for embeddings: {"error": {"message": "request (1072 tokens) exceeds the
    available context size (1024 tokens)"}}`): its message. Otherwise the gateway's words."""
    said = (exc.words or exc.message or "").strip()
    found: list[str] = _ENGINE_MESSAGE.findall(said)
    if not found:
        return said
    try:
        return str(json.loads(f'"{found[-1]}"')).strip()
    except ValueError:
        return str(found[-1]).strip()


def problem(exc: HubError, model: str) -> tuple[str, str]:
    """A refusal from the gateway, as `(code, words)`: said as what it was, naming what was
    observed, and what to do. Every one begins "Searching by words only", which is what
    a person gets while it lasts."""
    said = exc.words or exc.message
    if exc.status == 429:
        return "limited", (
            f"{WORDS_ONLY}this Workbench's key is at its limit ({said}), so a question could "
            "not be searched by meaning. The owner can raise the key's limits in Eugene's console."
        )
    if exc.kind == "key":
        return "key", f"{WORDS_ONLY}{exc.message}"
    if exc.kind in ("unreachable", "setup"):
        return "unreachable", f"{WORDS_ONLY}{exc.message}"
    if exc.status == 503 and exc.retry_after is not None:
        # The gateway is holding requests back for a moment and says for how long (a backend
        # that failed is cooling down, or its recovery is being tried: measured live for a
        # result sorter, MR6b acceptance, within a second of the engine being started again).
        # The search model is running or has just been started, so it is not "not running", and
        # there is nothing for a person to start. Said as what was observed, in the gateway's
        # words.
        return "error", (
            f"{WORDS_ONLY}Eugene's gateway asked Workbench to wait "
            f"{embedders.seconds_words(exc.retry_after)} before asking the search model {model} "
            f"again ({said}), so a question could not be searched by meaning."
        )
    if exc.status in (503, 404):
        return "not_running", (
            f"{WORDS_ONLY}the search model {model} is not running. "
            "Start it in Eugene's console under Backends."
        )
    if exc.kind == "timeout":
        return "slow", f"{WORDS_ONLY}{exc.message}"
    if exc.status == 400:
        return "refused", f"{WORDS_ONLY}the search model {model} refused the request ({said})."
    return "error", f"{WORDS_ONLY}the search model {model} failed ({said})."


LIMITED_ADDING = (
    "Adding meaning to your documents is slow right now: this Workbench's key is at its limit "
    "({said}) and chats use it too. It will carry on by itself. The owner can raise the key's "
    "limits in Eugene's console."
)
PAUSED_HINT = (
    "Your documents are searched by words until you choose Rebuild, which adds meaning again "
    "with the model that is running."
)
DAMAGED = (
    "Search by meaning is off: what this search was built with is unreadable ({why}). "
    "Your files are safe. Rebuild adds meaning again."
)
#: Why meaning cannot be searched, and a person should choose Rebuild.
REBUILD_CODES = ("changed", "dimension", "damaged")
#: The reasons that the headline says "no search model is running" for.
NO_MODEL_CODES = (
    "none_set_up",
    "not_running",
    "not_listed",
    "not_search_model",
    "outside",
    "outside_only",
)


def paused_headline(built: Identity, running: str) -> str:
    return (
        f"Search by meaning is paused: this search was built with {built.model} and "
        f"{running} is running now."
    )


def dimension_headline(model: str, got: int, built: Identity) -> str:
    return (
        f"Search by meaning is paused: {model} now gives {got:,} numbers for each passage, "
        f"and this search was built with {built.dim:,}."
    )


def headline_for(code: str, detail: str | None) -> str:
    """The header sentence, from what is true now (design 5.3)."""
    if code == "ok":
        return "Searching by meaning and words."
    if code in ("changed", "dimension", "damaged"):
        return detail or "Search by meaning is paused."
    if code in NO_MODEL_CODES:
        return "Searching by words only. No search model is running."
    return "Searching by words only."


def reason_of(detail: str | None) -> str:
    """A reason as a clause: `the search model X is not running. Start it ...`."""
    return (detail or "").removeprefix(WORDS_ONLY)


# --------------------------------------------------------------------------- #
# the whole
# --------------------------------------------------------------------------- #


@dataclass
class Flight:
    """The request in the air: whose documents it is for, and the task that can end it."""

    sub: str
    docs: set[str]
    task: asyncio.Future[Embedded] | None = None
    dropped: bool = False


@dataclass(frozen=True)
class Status:
    """How a person's documents are searched right now, and how adding meaning is going:
    everything the Documents screen's header says, from a live look (design 5.3)."""

    #: `ok` (meaning and words), or why not: `none_set_up`, `not_running`, `not_listed`,
    #: `not_search_model`, `outside`, `outside_only`, `unreachable`, `key`, `limited`, `slow`,
    #: `refused`, `error`, `changed`, `dimension`, `damaged`.
    code: str
    meaning: bool
    headline: str
    detail: str | None
    actions: tuple[str, ...]
    model: dict[str, Any]
    adding: dict[str, Any]
    rebuild: dict[str, Any] | None
    #: What is waiting for its meaning, and how fast it is going: the rows' times are made of it.
    queue: Queue = field(default_factory=lambda: Queue())

    @property
    def mode(self) -> str:
        return "meaning and words" if self.meaning else "words"

    def view(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "words": True,
            "meaning": self.meaning,
            "headline": self.headline,
            "code": self.code,
            "detail": self.detail,
            "actions": list(self.actions),
            "model": self.model,
            "adding": self.adding,
            "rebuild": self.rebuild,
        }


@dataclass
class Queue:
    """What is waiting for its meaning, and how fast it is being done: what the time left is
    made from."""

    order: list[tuple[str, int]] = field(default_factory=list)
    rate: float | None = None
    people: int = 1

    def __post_init__(self) -> None:
        #: For each document, the passages owed it and every document before it: made once,
        #: so a list of thousands of documents is not searched for each of them.
        self._through: dict[str, int] = {}
        running = 0
        for ident, owed in self.order:
            running += owed
            self._through[ident] = running
        self._all = running

    @property
    def owed(self) -> int:
        return self._all

    def seconds_to_finish(self, doc_id: str | None = None) -> float | None:
        """The seconds until every document ahead of this one, and this one, is done; or
        until all are when none is named. None when nothing has been measured yet. A person's
        documents are served in turn with everyone else's, so their share is the rate over the
        number of people waiting."""
        if not self.rate or self.rate <= 0:
            return None
        ahead = self._all if doc_id is None else self._through.get(doc_id)
        if ahead is None:
            return None
        return ahead * max(1, self.people) / self.rate


class Meaning:
    """Search by meaning for every person: choosing the search model, asking it, the matrices
    of vectors, and the worker that adds meaning. One per Workbench."""

    def __init__(
        self,
        indexes: Indexes,
        gateway: Gateway,
        settings: SettingsReader,
        *,
        clock: Callable[[], float] = time.perf_counter,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        listing: Listing | None = None,
    ) -> None:
        self.indexes = indexes
        self.gateway = gateway
        self._settings = settings
        self._clock = clock
        self._wall = wall
        self._sleep = sleep
        self.cache = vectors.MatrixCache(clock=clock)
        self.limiter = RateLimiter(clock=clock, sleep=sleep)
        self.signal = MeaningSignal(self)
        #: The gateway's list of models: one look, shared with sorting (`listing.py`).
        self.listing = listing if listing is not None else Listing(gateway, clock=clock)
        self._seen: dict[str, Seen] = {}
        self._rates: dict[str, Throughput] = {}
        #: People who may have passages owed their meaning, in the order they are served.
        self._owing: dict[str, None] = {}
        self._blocked: dict[str, Block] = {}
        self._until: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        #: The key is at its limit (every person waits): until when, and what was said.
        self._limit_until = 0.0
        self._limit: Block | None = None
        self._flight: Flight | None = None
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._sweeper: asyncio.Task[None] | None = None
        #: The batches begun, for the tests: (person, passages in it).
        self.served: deque[tuple[str, int]] = deque(maxlen=1000)

    # --- starting and stopping ---------------------------------------------

    def start(self) -> None:
        """Begin adding meaning. Call from the running loop, once."""
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="meaning")
            self._sweeper = asyncio.create_task(self._sweep(), name="meaning-sweep")

    async def aclose(self) -> None:
        tasks = [t for t in (self._task, self._sweeper) if t is not None]
        self._task = self._sweeper = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.cache.clear()

    def wake(self, sub: str) -> None:
        """This person may have passages owed their meaning."""
        self._owing.setdefault(sub, None)
        self._wake.set()

    async def resume(self) -> int:
        """Boot: the passages that still have no vector are owed (`chunks WHERE vec IS
        NULL`), so the people who have any are served, and a document that had counted its
        passages wrongly counts them again. How many people have something owed."""
        owing = 0
        for directory in self.indexes.person_dirs():
            sub = self.indexes.owner_of(directory)
            if sub is None or self.indexes.dir_of(sub) != directory:
                continue
            index = self.indexes.get(sub)
            if index is None:
                continue
            try:
                await index.recount_embedded()
                counts = await index.meaning_counts()
            except IndexUnavailable as exc:
                log.warning("one person's passages were not checked for meaning: %s", exc.message)
                continue
            if counts.owed or counts.words_only:
                self.wake(sub)
                owing += 1
        return owing

    # --- the gateway: which search models there are --------------------------

    async def probe(self, *, fresh: bool = False) -> Probe:
        """The gateway's models, as a look at them: the one look that is kept and shared with
        sorting (`listing.Listing`, ten seconds, and a failed look for a few), so a screen that
        asks every second asks the gateway once in ten; of it, the search models."""
        return await self.listing.probe(embedders.SURFACE, fresh=fresh)

    async def resolve(self, built: Identity | None = None) -> Resolved:
        """The search model in use now (`embedders.choose`), from a live look at the gateway.
        `built` is the model this person's search was built with, which `auto` keeps."""
        settings = await self._settings()
        probe = await self.probe()
        setting = settings.embedding_model or embedders.AUTO
        if probe.error is not None:
            code, words = problem(probe.error, setting)
            return Resolved(Choice(None, code, setting, None, (), words), probe)
        choice = embedders.choose(
            probe.models,
            probe.listed,
            setting,
            settings.external_embedding,
            built.model if built is not None else None,
        )
        return Resolved(choice, probe)

    async def identify(self, request_id: str) -> Seen:
        """Which model answers to this name, and how long its vectors are: from the last
        answer it gave (renewed by every one), else a tiny request. Raises `HubError`."""
        cached = self._seen.get(request_id)
        if cached is not None and self._clock() - cached.at < SEEN_TTL:
            return cached
        answer = await self.gateway.embeddings(
            request_id, [IDENTIFY_TEXT], seconds=QUESTION_TIMEOUT
        )
        return self._noted(request_id, answer)

    def _noted(self, request_id: str, answer: Embedded) -> Seen:
        seen = Seen(answer.model, len(answer.vectors[0]), self._clock())
        self._seen[request_id] = seen
        return seen

    def forget(self, request_id: str) -> None:
        """What the search model answered is not trusted any more (it just failed), and
        neither is the last look at the gateway."""
        self._seen.pop(request_id, None)
        self.listing.forget()

    def rate_of(self, model: str | None, per_minute: int | None = None) -> float | None:
        """Passages a second this search model is adding, from what it just did; and never more
        than the requests a minute that are allowed (`per_minute`, of sixteen passages each) let
        through, which is what a burst before the limit bites would not show."""
        found = self._rates.get(model) if model is not None else None
        rate = found.per_second() if found is not None else None
        if rate is None or per_minute is None:
            return rate
        return min(rate, max(1, per_minute) * BATCH / WINDOW)

    # --- the question -----------------------------------------------------

    @staticmethod
    def _built(raw: str | None) -> Identity | None:
        try:
            return Identity.from_json(raw)
        except ValueError as exc:
            raise SignalUnavailable("damaged", DAMAGED.format(why=exc)) from exc

    @staticmethod
    def _mismatch(built: Identity, seen: Seen) -> tuple[str, str] | None:
        """`(code, sentence)` when what answered is not what this search was built with."""
        if seen.model != built.model:
            return "changed", paused_headline(built, seen.model)
        if seen.dim != built.dim:
            return "dimension", dimension_headline(seen.model, seen.dim, built)
        return None

    async def search(
        self, index: Index, query: Query, scopes: Sequence[str], limit: int
    ) -> list[Hit]:
        """The passages nearest the question by meaning, best first, as hits whose `score` is
        the similarity. Raises `SignalUnavailable` (the search goes on by words) when there is
        no search model to ask, it is not the one this search was built with, or it fails."""
        built = self._built(await index.embedder())
        resolved = await self.resolve(built)
        model = resolved.model
        if model is None:
            raise SignalUnavailable(
                resolved.code,
                resolved.message or WORDS_ONLY.strip(),
                quiet=built is None,
            )
        await self.touch(index)
        if built is None:
            return []  # a search model is in play and nothing is embedded yet
        known = self._seen.get(model.id)
        if (
            known is not None
            and self._clock() - known.at < SEEN_TTL
            and (bad := self._mismatch(built, known)) is not None
        ):
            raise SignalUnavailable(*bad)  # it answered as another model a moment ago
        text = embedders.question_input(built, query.asked or " ".join(query.terms))
        try:
            answer = await self._ask(model.id, text)
        except HubError as exc:
            self.forget(model.id)
            raise SignalUnavailable(*problem(exc, model.id)) from exc
        seen = self._noted(model.id, answer)
        if (bad := self._mismatch(built, seen)) is not None:
            raise SignalUnavailable(*bad)
        try:
            question = vectors.normalised(answer.vectors)[0]
        except vectors.UnusableVectors as exc:
            raise SignalUnavailable(
                "unusable",
                f"{WORDS_ONLY}the search model {model.id} gave numbers Workbench cannot "
                f"use ({exc}).",
            ) from exc
        matrix = await self._matrix(index, built)
        top = await asyncio.to_thread(vectors.scan, matrix, question, scopes, limit)
        if not top:
            return []
        return await index.hits_by_ids([i for i, _ in top], dict(top), scopes)

    async def _ask(self, model: str, text: str) -> Embedded:
        """One question, embedded. A refusal that asks for only a moment is waited out once."""
        try:
            return await self.gateway.embeddings(model, [text], seconds=QUESTION_TIMEOUT)
        except HubError as exc:
            if exc.status == 429 and (exc.retry_after or 1.0) <= QUESTION_RETRY_MAX:
                await self._sleep(max(0.05, exc.retry_after or 1.0))
                return await self.gateway.embeddings(model, [text], seconds=QUESTION_TIMEOUT)
            raise

    async def _matrix(self, index: Index, built: Identity) -> vectors.Matrix:
        """The person's vectors in memory: kept while nothing is written to their index."""
        found = self.cache.get(index.sub, index.writes)
        if found is not None and found.dim == built.dim:
            return found
        try:
            matrix = await index.load_vectors(built.dim, self._clock)
        except vectors.BadVector as exc:
            log.warning("a stored vector is not the size this search was built with: %s", exc)
            raise SignalUnavailable(
                "damaged",
                "Search by meaning is off: a stored vector is not the size this search was "
                f"built with ({exc.size:,} bytes where {exc.expected:,} were expected). Your "
                "files are safe. Rebuild adds meaning again.",
            ) from exc
        self.cache.put(index.sub, matrix)
        return matrix

    async def floor(self, index: Index) -> float | None:
        """The best similarity that is not, by itself, an answer: for the search model this
        person's search was built with, when it is one that was measured."""
        try:
            built = Identity.from_json(await index.embedder())
        except ValueError:
            return None
        return embedders.floor_for(built.model) if built is not None else None

    # --- who is owed what ---------------------------------------------------

    async def in_play(self, index: Index) -> bool:
        """Whether search by meaning is being used for this person: their search was built
        with a search model, or one is running now to build it with."""
        if await index.embedder() is not None:
            return True
        from ..person_context import acting_as, current

        inherited = current.get()
        session_id = inherited[1] if inherited and inherited[0] == index.sub else None
        with acting_as(index.sub, session_id):
            return (await self.resolve(None)).model is not None

    async def owed(self, index: Index, doc: Document) -> bool:
        """Whether a document that can be found by words is owed its meaning before it is
        `ready` (the `Ingest.owed` hook): when search by meaning is in play and it has
        passages. The debt is registered: the worker is woken."""
        if doc.chunks == 0:
            return False
        if not await self.in_play(index):
            return False
        self.wake(index.sub)
        return True

    async def touch(self, index: Index) -> None:
        """Somebody looked at this person's documents: if meaning can be added to them and has
        not been, the worker is woken, so a search model that was started after their
        documents were read is noticed the first time they come back."""
        if index.sub in self._owing:
            if index.sub in self._blocked:
                self._wake.set()  # waiting out a block: a look is a reason to see if it is over
            return
        try:
            counts = await index.meaning_counts()
        except IndexUnavailable:
            return
        if counts.owed or counts.words_only:
            self.wake(index.sub)

    async def cancel(self, sub: str, doc_ids: Sequence[str]) -> None:
        """These documents were removed or read again: a request that was only for them is
        ended (so the key's slot is free for a chat). A request that also holds other documents'
        passages is left to finish, and what it made for these is thrown away, because their
        passages are gone."""
        flight = self._flight
        wanted = set(doc_ids)
        if (
            flight is not None
            and flight.sub == sub
            and flight.docs
            and flight.docs <= wanted
            and flight.task is not None
            and not flight.task.done()
        ):
            flight.dropped = True
            flight.task.cancel()

    async def rebuild(self, sub: str) -> int:
        """Add meaning again with the search model that is running ("Rebuild"): the request
        in the air is ended, every vector goes with the record of the model that made them, in
        one transaction, and each document is owed all of its meaning. Words are never touched.
        How many passages are owed."""
        index = self.indexes.get(sub, create=False)
        if index is None:
            return 0
        flight = self._flight
        if flight is not None and flight.sub == sub and flight.task is not None:
            flight.dropped = True
            flight.task.cancel()
        owed = await index.clear_vectors()
        self.cache.drop(sub)
        for table in (self._blocked, self._until, self._failures):
            table.pop(sub, None)
        if owed:
            self.wake(sub)
        return owed

    # --- the worker -------------------------------------------------------

    async def _sweep(self) -> None:
        while True:
            await self._sleep(SWEEP_SECONDS)
            self.cache.evict_idle()

    async def _loop(self) -> None:
        while True:
            try:
                pause = await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("adding meaning to documents failed in Workbench itself")
                pause = 5.0
            if pause is None:
                await self._wake.wait()
            elif pause > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), pause)
            self._wake.clear()

    async def _cycle(self) -> float | None:
        """One turn: a batch for the first person who has one owed and is not waiting, who
        then goes to the back. Seconds to wait before the next turn (0: go on), or None to
        wait until somebody has something owed."""
        settings = await self._settings()
        # With documents turned off by the owner, a person's notes still get their meaning
        # (MV1): `_work` asks the index for notes' passages only.
        now = self._clock()
        if now < self._limit_until:
            return self._limit_until - now
        self._limit = None
        soonest: float | None = None
        for sub in list(self._owing):
            wait = self._until.get(sub, 0.0) - now
            from ..person_context import acting_as

            with acting_as(sub):
                back = wait > 0 and await self._model_is_back(sub)
            if back:
                wait = 0.0
            if wait > 0:
                soonest = wait if soonest is None else min(soonest, wait)
                continue
            with acting_as(sub):
                outcome = await self._work(sub, settings)
            if sub in self._owing:
                self._owing.pop(sub)
                self._owing[sub] = None  # to the back of the turn
            if outcome is Outcome.DONE:
                self._owing.pop(sub, None)
                self._blocked.pop(sub, None)
                self._until.pop(sub, None)
                continue
            if outcome is Outcome.WORKED:
                return 0.0
            if self._clock() < self._limit_until:
                return self._limit_until - self._clock()  # the key's limit holds everyone back
            wait = max(0.05, self._until.get(sub, 0.0) - self._clock())
            soonest = wait if soonest is None else min(soonest, wait)
        if soonest is not None:
            return soonest
        return None if not self._owing else 0.0

    async def _model_is_back(self, sub: str) -> bool:
        """Whether a wait that was set because there was no search model to use is over, because
        there is one now. A block does not outlive what it waits for: a document that says "no
        search model is set up" while one is listed is a lie for as long as the wait has left (5
        seconds, and then longer each time). The wait is ended, and what it had counted is not held
        against the model that is back. MR6b round two found it in the flaky test of issue #6: a
        pass that met a document that was not yet decided as words only (`searchable`, for a
        moment) set a 5 s wait, and the model that came a moment later was not used for 5 s."""
        block = self._blocked.get(sub)
        if block is None or not block.no_model:
            return False
        if (await self.resolve(None)).model is None:
            return False
        self._until.pop(sub, None)
        self._failures.pop(sub, None)
        return True

    def _wait(
        self,
        sub: str,
        code: str,
        message: str,
        seconds: float,
        state: str = "waiting",
        *,
        no_model: bool = False,
    ) -> Outcome:
        """This person's documents wait `seconds`, and say why."""
        until = self._clock() + seconds
        self._blocked[sub] = Block(code, message, state, until, no_model)
        self._until[sub] = until
        return Outcome.BLOCKED

    def _retry_in(self, sub: str, table: Sequence[float]) -> float:
        n = self._failures.get(sub, 0)
        self._failures[sub] = n + 1
        return table[min(n, len(table) - 1)]

    def _succeeded(self, sub: str) -> None:
        self._failures.pop(sub, None)
        self._blocked.pop(sub, None)
        self._until.pop(sub, None)

    async def _work(self, sub: str, settings: Settings) -> Outcome:
        index = self.indexes.get(sub, create=False)
        if index is None:
            return Outcome.DONE
        try:
            if not settings.enabled and not (await index.pending_meaning(1, documents=False))[0]:
                return Outcome.DONE  # documents are off, and none of this person's notes is owed
            built = Identity.from_json(await index.embedder())
            if sub in self._blocked:
                await self.probe(fresh=True)  # it was waiting for the search model: look again
            resolved = await self.resolve(built)
            model = resolved.model
            if model is None:
                if built is None and not (await index.meaning_counts()).owed:
                    return Outcome.DONE  # no search model was ever in play: nothing is owed
                return self._wait(
                    sub,
                    resolved.code,
                    resolved.message or WORDS_ONLY.strip(),
                    self._retry_in(sub, RETRY_SECONDS),
                    no_model=True,
                )
            try:
                seen = await self.identify(model.id)
            except HubError as exc:
                return self._failed(sub, exc, model.id)
            # The model is there and answers: whatever this person's documents waited for is not
            # what they wait for now, and a request that is in the air is work, not waiting. (The
            # block was kept until a batch was stored, so a batch that was held up, or was slow,
            # left every document saying "Meaning is waiting: no search model is set up" beside a
            # search model that was answering.) A request that fails sets its own block.
            self._blocked.pop(sub, None)
            if built is not None and (bad := self._mismatch(built, seen)) is not None:
                # Paused: nothing is added with another model's numbers. Whoever looks at the
                # documents next (`touch`) brings the worker back to see if that has changed.
                log.info("adding meaning is paused for one person: %s", bad[0])
                return Outcome.DONE
            await index.reopen_unembedded()
            ident = built or embedders.identity_for(seen.model, seen.dim, self._wall())
            await self.limiter.wait(settings.embed_per_minute)
            # A person's notes are a profile of them: embedded only by a search model on the
            # install's own machines (MV1), never by one the owner allowed for documents.
            pending, epoch = await index.pending_meaning(
                BATCH, notes=model.local, documents=settings.enabled
            )
            if not pending:
                await self._finish_counted(index)
                return Outcome.DONE
            return await self._embed(sub, index, model, ident, pending, epoch)
        except IndexUnavailable as exc:
            log.warning("meaning could not be added to one person's documents: %s", exc.message)
            return Outcome.DONE
        except ValueError as exc:  # Identity.from_json: what the index says it was built with
            log.warning("the search model an index was built with is unreadable: %s", exc)
            return Outcome.DONE

    async def _finish_counted(self, index: Index) -> None:
        """Documents that wait for nothing (all their passages have a vector) are `ready`."""
        for doc in await index.unfinished():
            if doc.embedded >= doc.chunks:
                await index.finish(doc.id)

    def _failed(self, sub: str, exc: HubError, model: str) -> Outcome:
        """A request failed: wait, and say why (`problem`). The key at its limit holds
        everyone back for as long as the gateway asked."""
        code, words = problem(exc, model)
        self.forget(model)
        if exc.status == 429:
            wait = min(LIMIT_MAX_WAIT, exc.retry_after or self._retry_in(sub, BACKOFF_SECONDS))
            self._limit_until = self._clock() + max(1.0, wait)
            self._limit = Block(
                "limited",
                LIMITED_ADDING.format(said=exc.words or exc.message),
                "limited",
                self._limit_until,
            )
            log.info("the key is at its limit; adding meaning waits %.0f s", wait)
            return Outcome.BLOCKED
        slow = exc.status in (503, 404) or exc.kind == "unreachable"
        log.warning("adding meaning is waiting (%s): %s", code, exc.words or exc.message)
        return self._wait(
            sub, code, words, self._retry_in(sub, RETRY_SECONDS if slow else BACKOFF_SECONDS)
        )

    async def _request(self, flight: Flight, model: str, inputs: list[str]) -> Embedded | None:
        """One request, that can be ended from outside (`cancel`). None when it was."""
        task = asyncio.ensure_future(self.gateway.embeddings(model, inputs, seconds=BATCH_TIMEOUT))
        flight.task = task
        try:
            await asyncio.wait({task})
            if task.cancelled():
                return None
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    @staticmethod
    def _refusal(passage: Pending, exc: HubError) -> dict[str, Any]:
        """What is kept of a passage the search model refused: in the words of design 5.7."""
        chars = len(passage.text)
        # What the engine said, never the table's limit for the model: the engine was started
        # with a context of its own, and a note that says 32,768 beside "exceeds 1,024" lies.
        said = engine_words(exc).rstrip(". ")
        where = f", page {passage.page_start}" if passage.page_start else ""
        return {
            "ord": passage.ord,
            "page": passage.page_start,
            "chars": chars,
            "why": (
                f"The search model refused one passage ({chars:,} characters; it said: {said}): "
                f"skipped {passage.name}{where}."
            ),
        }

    async def _embed(
        self,
        sub: str,
        index: Index,
        model: SearchModel,
        ident: Identity,
        pending: list[Pending],
        epoch: int,
    ) -> Outcome:
        """One batch: embed it, check what answered is what this search was built with, store
        the vectors in one transaction. A passage the search model calls too long is found by
        halving the batch, and is kept refused, so it is neither retried for ever nor lost."""
        flight = Flight(sub, {p.doc_id for p in pending})
        self._flight = flight
        self.served.append((sub, len(pending)))
        started = self._clock()
        try:
            try:
                answer = await self._request(
                    flight,
                    model.id,
                    [embedders.passage_input(ident, p.heading_path, p.text) for p in pending],
                )
            except HubError as exc:
                if too_long(exc):
                    return await self._isolate(sub, index, model, ident, pending, epoch, exc)
                return self._failed(sub, exc, model.id)
            if answer is None:
                return Outcome.WORKED  # its documents went; the next turn looks again
            seen = self._noted(model.id, answer)
            if (bad := self._mismatch(ident, seen)) is not None:
                log.warning("the search model changed while documents were embedded: %s", bad[0])
                return Outcome.DONE
            return await self._store(
                sub, index, model, ident, [(pending, answer)], [], epoch, self._clock() - started
            )
        finally:
            self._flight = None

    async def _store(
        self,
        sub: str,
        index: Index,
        model: SearchModel,
        ident: Identity,
        answers: Sequence[tuple[Sequence[Pending], Embedded]],
        refused: Sequence[tuple[int, str, dict[str, Any]]],
        epoch: int,
        seconds: float,
    ) -> Outcome:
        """Keep what the search model answered (and what it refused) in one transaction."""
        rows: list[tuple[int, str, bytes]] = []
        try:
            for part, answer in answers:
                unit = vectors.normalised(answer.vectors)
                rows += [
                    (p.chunk_id, p.doc_id, vectors.to_blob(unit[i])) for i, p in enumerate(part)
                ]
        except vectors.UnusableVectors as exc:
            log.warning("the search model gave vectors that cannot be searched: %s", exc)
            return self._wait(
                sub,
                "unusable",
                f"{WORDS_ONLY}the search model {model.id} gave numbers Workbench cannot "
                f"use ({exc}).",
                self._retry_in(sub, BACKOFF_SECONDS),
            )
        stored = await index.store_vectors(
            ident.to_json(), ident.model, ident.dim, rows, refused, epoch
        )
        if stored.stale:
            return Outcome.WORKED  # Rebuild came while the request was in the air
        if stored.changed is not None:
            log.warning("the index was built with another search model than the one running")
            return Outcome.DONE
        self._rates.setdefault(ident.model, Throughput(clock=self._clock)).record(
            stored.written + stored.refused, seconds
        )
        self._succeeded(sub)
        log.info(
            "added meaning to %d passage(s) (%d refused, %d gone) in %.1f s; %d document(s) done",
            stored.written,
            stored.refused,
            stored.gone,
            seconds,
            len(stored.completed),
        )
        return Outcome.WORKED

    async def _isolate(
        self,
        sub: str,
        index: Index,
        model: SearchModel,
        ident: Identity,
        pending: list[Pending],
        epoch: int,
        first: HubError,
    ) -> Outcome:
        """A batch was refused as too long: halve it until the passage that is too long is
        alone, and keep it refused. What was not too long is stored. Another kind of failure
        while doing it ends it: what was stored stays stored, and the rest waits its turn."""
        answers: list[tuple[Sequence[Pending], Embedded]] = []
        refused: list[tuple[int, str, dict[str, Any]]] = []
        failures: list[HubError] = []
        started = self._clock()

        async def piece(part: list[Pending], error: HubError | None) -> None:
            if error is None:
                await self.limiter.wait((await self._settings()).embed_per_minute)
                try:
                    answer = await self.gateway.embeddings(
                        model.id,
                        [embedders.passage_input(ident, p.heading_path, p.text) for p in part],
                        seconds=BATCH_TIMEOUT,
                    )
                except HubError as exc:
                    error = exc
                else:
                    if self._mismatch(ident, self._noted(model.id, answer)) is not None:
                        failures.append(HubError("another search model answered", kind="changed"))
                        return
                    answers.append((part, answer))
                    return
            if not too_long(error):
                failures.append(error)
                return
            if len(part) == 1:
                log.warning(
                    "the search model refused a passage of %d characters as too long: %s",
                    len(part[0].text),
                    engine_words(error),
                )
                refused.append((part[0].chunk_id, part[0].doc_id, self._refusal(part[0], error)))
                return
            middle = len(part) // 2
            await piece(part[:middle], None)
            if not failures:
                await piece(part[middle:], None)

        await piece(pending, first)
        outcome = Outcome.WORKED
        if answers or refused:
            outcome = await self._store(
                sub, index, model, ident, answers, refused, epoch, self._clock() - started
            )
        if failures:
            if failures[0].kind == "changed":
                return Outcome.DONE
            if outcome is Outcome.WORKED:
                return self._failed(sub, failures[0], model.id)
        return outcome

    # --- what is said of it ---------------------------------------------------

    async def queue_of(self, index: Index, model: str | None) -> Queue:
        """What is waiting for its meaning in this person's index, and how fast it is going."""
        limit = (await self._settings()).embed_per_minute
        return Queue(
            await index.owed_by_document(), self.rate_of(model, limit), max(1, len(self._owing))
        )

    async def setting_view(self) -> tuple[list[str], ConfigFieldStatus]:
        """For the config page: the search models Eugene lists (ids, for suggestions), and
        what the Search model setting comes to now, as `ConfigFieldStatus` says it."""
        resolved = await self.resolve(None)
        ids = [m.id for m in resolved.probe.models]
        if resolved.model is not None:
            text = f"In use: {resolved.model.id}"
            if resolved.choice.automatic:
                text += " (automatic)"
            return ids, ConfigFieldStatus(level=ConfigFieldStatusLevel.info, text=f"{text}.")
        return ids, ConfigFieldStatus(
            level=ConfigFieldStatusLevel.warning,
            text=resolved.message or "No search model is in use.",
        )

    async def status(self, index: Index | None, sub: str) -> Status:
        """How this person's documents are searched now, and how adding meaning is going: a
        live look (the gateway's models, ten seconds old at most, and a tiny request when no
        answer is a minute fresh), never what was true when the last document was added."""
        settings = await self._settings()
        built: Identity | None = None
        damaged = False
        if index is not None:
            await self.touch(index)
            try:
                built = Identity.from_json(await index.embedder())
            except ValueError as exc:
                damaged = True
                why = str(exc)
        resolved = await self.resolve(built)
        model = resolved.model
        code, detail = resolved.code, resolved.message
        running: str | None = None
        dim: int | None = None
        if damaged:
            code, detail = "damaged", DAMAGED.format(why=why)
        elif model is not None:
            running = model.id
            if built is not None:
                try:
                    seen = await self.identify(model.id)
                except HubError as exc:
                    code, detail = problem(exc, model.id)
                    self.forget(model.id)
                else:
                    running, dim = seen.model, seen.dim
                    if (bad := self._mismatch(built, seen)) is not None:
                        code, detail = bad
        meaning = code == "ok"
        headline = headline_for(code, detail)
        if meaning:
            detail = None
        elif code in ("changed", "dimension"):
            detail = PAUSED_HINT
        elif code == "damaged":
            detail = None
        actions: list[str] = []
        if code in REBUILD_CODES:
            actions.append("rebuild")
        if code == "none_set_up":
            actions.append("get_search_model")
        counts = await index.meaning_counts(LIBRARY) if index is not None else None
        queue = (
            await self.queue_of(index, built.model if built is not None else running)
            if index is not None
            else Queue()
        )
        adding = self._adding_view(
            sub, counts, queue, code, headline, detail, meaning, settings.enabled
        )
        rebuild: dict[str, Any] | None = None
        if "rebuild" in actions and counts is not None:
            rate = self.rate_of(running, settings.embed_per_minute)
            rebuild = {
                "passages": counts.passages,
                "seconds": counts.passages / rate if rate and rate > 0 else None,
                "model": running,
            }
        identity = built or (embedders.identity_for(running, dim or 0) if running else None)
        known = embedders.lookup(identity.model) if identity is not None else None
        view = {
            "setting": settings.embedding_model or embedders.AUTO,
            "automatic": resolved.choice.automatic,
            "resolved": model.id if model is not None else None,
            "label": (
                f"In use: {model.id}" + (" (automatic)" if resolved.choice.automatic else "")
                if model is not None
                else None
            ),
            "locality": model.locality if model is not None else None,
            "runsOn": model.who if model is not None else None,
            "allowExternal": settings.external_embedding,
            "outside": list(resolved.choice.outside),
            "builtWith": built.model if built is not None else None,
            "dimension": built.dim if built is not None else dim,
            "prefixes": {
                "known": identity.known if identity is not None else None,
                "family": known.family if known is not None else None,
                "query": identity.query_prefix if identity is not None else None,
                "document": identity.document_prefix if identity is not None else None,
                "note": embedders.unknown_prefixes(identity) if identity is not None else None,
            },
            "floor": embedders.floor_for(built.model) if built is not None else None,
        }
        return Status(code, meaning, headline, detail, tuple(actions), view, adding, rebuild, queue)

    def _adding_view(
        self,
        sub: str,
        counts: MeaningCounts | None,
        queue: Queue,
        code: str,
        headline: str,
        detail: str | None,
        meaning: bool,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """How adding meaning is going, for the header: counts, and what is true of the
        worker: working, waiting for the search model, or held back by the key's limit."""
        passages = counts.passages if counts else 0
        embedded = counts.embedded if counts else 0
        owed = counts.owed if counts else 0
        words_only = counts.words_only if counts else 0
        limit = self._limit if self._clock() < self._limit_until else None
        eta = queue.seconds_to_finish() if owed else None
        state, message = "idle", None
        if owed:
            if not enabled:
                state = "waiting"
                message = (
                    "Meaning is waiting: the owner of this Workbench has turned off documents."
                )
            elif limit is not None:
                state, message = "limited", limit.message
            elif not meaning:
                state = "waiting"
                if code in REBUILD_CODES:
                    message = "Meaning is paused until you choose Rebuild."
                else:
                    message = f"Meaning is waiting: {reason_of(detail or headline)}"
            elif (block := self._blocked.get(sub)) is not None and not block.no_model:
                # (A wait for a search model that is over, while one is running, is not said.)
                state, message = "waiting", f"Meaning is waiting: {reason_of(block.message)}"
            else:
                state = "working"
                tail = f", {embedders.duration_words(eta)} left." if eta is not None else "."
                message = f"Adding meaning: {embedded:,} of {passages:,} passages{tail}"
        return {
            "state": state,
            "message": message,
            "passages": passages,
            "embedded": embedded,
            "owed": owed,
            "wordsOnly": words_only,
            "etaSeconds": eta,
            "ratePerSecond": queue.rate,
        }

    @staticmethod
    def document_view(doc: Document, queue: Queue, status: Status | None) -> dict[str, Any] | None:
        """A document's meaning, for its row: `ready` (all of its passages), `words_only`
        (read while no search model was in play), `adding` (`Ready for words. Adding meaning:
        1,240 of 3,000 passages, about 14 minutes left.`), `waiting` (owed it, and the search
        model is not there or is not the one this search was built with) or `limited` (the key
        is at its limit). None for a document that is not read yet, or could not be."""
        if doc.status not in ("searchable", "embedding", READY):
            return None
        base: dict[str, Any] = {
            "embedded": doc.embedded,
            "total": doc.chunks,
            "etaSeconds": None,
            "refused": len(doc.refused),
            "notes": [str(r["why"]) for r in doc.refused if r.get("why")],
        }
        if doc.status == READY:
            done = doc.embedded >= doc.chunks
            return {**base, "state": "ready" if done else "words_only", "message": None}
        adding = status.adding if status is not None else {}
        state = str(adding.get("state") or "working")
        if state in ("working", "idle"):
            eta = queue.seconds_to_finish(doc.id)
            tail = f", {embedders.duration_words(eta)} left." if eta is not None else "."
            return {
                **base,
                "state": "adding",
                "etaSeconds": eta,
                "message": (
                    f"Ready for words. Adding meaning: {doc.embedded:,} of {doc.chunks:,} "
                    f"passages{tail}"
                ),
            }
        return {**base, "state": state, "message": f"Ready for words. {adding.get('message')}"}


class MeaningSignal:
    """Meaning as a way of searching (`search.Signal`): the passages whose vectors are nearest
    the question's. Its hits' scores are similarities."""

    name = "meaning"
    similarity = True

    def __init__(self, meaning: Meaning) -> None:
        self._meaning = meaning

    async def search(
        self, index: Index, query: Query, scopes: Sequence[str], limit: int
    ) -> list[Hit]:
        return await self._meaning.search(index, query, scopes, limit)

    async def floor(self, index: Index) -> float | None:
        return await self._meaning.floor(index)
