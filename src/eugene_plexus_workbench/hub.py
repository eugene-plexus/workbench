"""Eugene's gateway, as Workbench reaches it: an ordinary OpenAI client.

**The same doors as everyone else** (`workbench.md` call 6). `GET
/v1/models`, `POST /v1/chat/completions`, for search by meaning (MR5)
`POST /v1/embeddings` and, for sorting what a search found (MR6b),
`POST /v1/rerank`, with the app's client key as the bearer. The key is
minted by the registry at install and read from the file it names; nothing
else the hub has is reachable from here.

**A refusal is said in plain words** (W11): the key turned off, the key at
its limit, the gateway unreachable -- each names what happened and what to
do, because every person using this Workbench shares the one key and none
of them can see Eugene's console.
"""

from __future__ import annotations

import email.utils
import json
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from . import long_chats
from ._http import ssl_context

#: The longest a gateway's `Retry-After` is believed: a person waits minutes, not days.
RETRY_AFTER_MAX = 3600.0


class HubError(Exception):
    """The gateway could not give an answer, in words a person reads."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 502,
        kind: str = "hub",
        param: str | None = None,
        retry_after: float | None = None,
        words: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.kind = kind
        #: The request field the gateway named, so a form can mark it (§2.5).
        self.param = param
        #: Seconds the gateway asked us to wait (`Retry-After`), when it said.
        self.retry_after = retry_after
        #: What the gateway itself said, when it refused (`message` is ours, around it).
        self.words = words


def _retry_after(response: httpx.Response) -> float | None:
    """The seconds a refusal's `Retry-After` asks for: a number, or an HTTP date."""
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    if seconds != seconds:  # NaN
        return None
    return max(0.0, min(seconds, RETRY_AFTER_MAX))


@dataclass(frozen=True)
class Embedded:
    """What the gateway answered to one embeddings request."""

    #: The model the gateway says answered: the one its vectors belong to, which is not
    #: always the name that was asked for (an alias answers with its first target's).
    model: str
    #: One vector for each input, in the order the inputs were sent.
    vectors: list[list[float]]
    tokens: int | None
    #: How long the request took, from sending it to having the whole answer.
    seconds: float
    request_id: str | None = None


@dataclass(frozen=True)
class Reranked:
    """What the gateway answered to one rerank request (MR6b): how well each passage answers
    the question, which is all Workbench asks of a result sorter."""

    #: `scores[i]` is for the `i`-th passage that was sent, 0 to 1 (the gateway puts every
    #: sorter on that scale). They are put in order here, never compared with another sorter's.
    scores: list[float]
    #: What the sorter says it read, when it says.
    tokens: int | None
    #: How long the request took, from sending it to having the whole answer.
    seconds: float
    model: str | None = None
    request_id: str | None = None

    @property
    def order(self) -> list[int]:
        """The positions of the passages, best first. A tie keeps the order the passages were
        sent in, so the order does not depend on the order the answer happened to list them in."""
        return sorted(range(len(self.scores)), key=lambda i: (-self.scores[i], i))


def _gateway_words(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:400] or f"HTTP {response.status_code}"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    if isinstance(body, dict) and body.get("detail"):
        detail = body["detail"]
        return str(detail.get("detail") if isinstance(detail, dict) else detail)
    return f"HTTP {response.status_code}"


def _gateway_param(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    param = error.get("param") if isinstance(error, dict) else None
    return str(param) if param else None


def _video_resource(response: httpx.Response, what: str) -> dict[str, Any]:
    try:
        answer = response.json()
    except ValueError as exc:
        raise HubError(
            f"The gateway answered {what} with something that is not JSON "
            f"({response.headers.get('content-type') or 'no type'}).",
            kind="gateway",
        ) from exc
    if not isinstance(answer, dict) or not isinstance(answer.get("id"), str):
        raise HubError(f"The gateway's answer to {what} named no job.", kind="gateway")
    return answer


def _embedded(response: httpx.Response, count: int, seconds: float) -> Embedded:
    """The gateway's answer to an embeddings request, checked: one vector for each input
    in the order they were sent, all of one length, and a model named. A reply that is
    not that is said as what it is, and never stored."""

    def bad(why: str) -> HubError:
        return HubError(
            f"The gateway's answer to the search request was not usable: {why}.", kind="gateway"
        )

    try:
        answer = response.json()
    except ValueError as exc:
        raise bad(f"it was not JSON ({response.headers.get('content-type') or 'no type'})") from exc
    if not isinstance(answer, dict):
        raise bad("it was not an object")
    model = answer.get("model")
    if not isinstance(model, str) or not model:
        raise bad("it named no model")
    data = answer.get("data")
    if not isinstance(data, list) or len(data) != count:
        raise bad(f"it held {len(data) if isinstance(data, list) else 'no'} vectors for {count}")
    ordered: list[list[float]] = [[] for _ in range(count)]
    seen: set[int] = set()
    for item in data:
        index = item.get("index") if isinstance(item, dict) else None
        vector = item.get("embedding") if isinstance(item, dict) else None
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < count
            or index in seen
            or not isinstance(vector, list)
            or not vector
        ):
            raise bad("a vector was missing, repeated or not a list of numbers")
        seen.add(index)
        ordered[index] = vector
    width = len(ordered[0])
    if any(len(v) != width for v in ordered):
        raise bad("its vectors were not all the same length")
    usage = answer.get("usage")
    tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    return Embedded(
        model=model,
        vectors=ordered,
        tokens=tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
        seconds=seconds,
        request_id=response.headers.get("x-request-id"),
    )


def _score(value: object) -> float | None:
    """A relevance score as a number a sorter's order can be made from, or None when it is not:
    anything but an integer or a real number, a boolean, one that is not finite (`NaN`, `Infinity`,
    or `1e999`, which JSON lets through), and an integer too large to be a float at all (a score of
    four hundred digits makes `math.isfinite` raise `OverflowError`, which is not a `HubError` and
    so was not an unusable answer but a failure in Workbench itself)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _reranked(response: httpx.Response, count: int, seconds: float) -> Reranked:
    """The gateway's answer to a rerank request, checked: a score for each of the `count`
    passages sent, each once, each a finite number. A reply that is not that is said as what
    it is (`kind` is `unusable`), and the passages are left in the order they were found in."""

    def bad(why: str) -> HubError:
        return HubError(
            f"The gateway's answer to the sort request was not usable: {why}.", kind="unusable"
        )

    try:
        answer = response.json()
    except ValueError as exc:
        raise bad(f"it was not JSON ({response.headers.get('content-type') or 'no type'})") from exc
    if not isinstance(answer, dict):
        raise bad("it was not an object")
    results = answer.get("results")
    if not isinstance(results, list) or len(results) != count:
        raise bad(
            f"it held {len(results) if isinstance(results, list) else 'no'} scores for "
            f"{count} passages"
        )
    scores = [0.0] * count
    seen: set[int] = set()
    for item in results:
        index = item.get("index") if isinstance(item, dict) else None
        score = _score(item.get("relevance_score")) if isinstance(item, dict) else None
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < count
            or index in seen
            or score is None
        ):
            raise bad("a score was missing, repeated or not a number")
        seen.add(index)
        scores[index] = score
    usage = answer.get("usage")
    tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    model = answer.get("model")
    return Reranked(
        scores=scores,
        tokens=tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
        seconds=seconds,
        model=model if isinstance(model, str) and model else None,
        request_id=response.headers.get("x-request-id"),
    )


def refusal(
    status: int, words: str, param: str | None = None, retry_after: float | None = None
) -> HubError:
    """The gateway's refusal, said for a person who cannot see the console."""
    if status in (401, 403):
        return HubError(
            "Eugene refused the key this Workbench uses, so it cannot reach any model "
            f"({words}). If the key was turned off in Eugene's console, the owner can "
            "uninstall and install Workbench again to make a new one.",
            status=status,
            kind="key",
            words=words,
        )
    if status == 429:
        return HubError(
            f"This Workbench's key is at its limit ({words}). Everyone using this Workbench "
            "shares that key; the owner can raise its limits on the key in Eugene's console.",
            status=429,
            kind="limit",
            retry_after=retry_after,
            words=words,
        )
    # Row 26: the backend refused the request as longer than the model's window. The gateway's
    # chat door gives no code for it yet (gateway#18), so its words are read, as the gateway
    # itself reads them.
    kind = "context" if status == 400 and long_chats.is_overflow(words) else "gateway"
    return HubError(
        words, status=status, kind=kind, param=param, retry_after=retry_after, words=words
    )


class Hub:
    """The gateway, with the app's key. One HTTP client for the process."""

    def __init__(
        self,
        gateway_url: str | None,
        key_file: Path | None,
        person_token: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/") if gateway_url else None
        self._key_file = key_file
        self._person_token = person_token
        self._key: tuple[float, str] | None = None
        # The gateway is this install's, on loopback or the LAN: never
        # through a proxy the person's environment names. A stream may go
        # quiet while a model reads a long prompt, but not for 15 minutes:
        # past that the gateway's own deadline (600 s) has long fired.
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=900.0), trust_env=False, verify=ssl_context()
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def why_not(self) -> str | None:
        if not self.gateway_url:
            return (
                "Eugene's agent found no gateway for this Workbench when it started it. Start "
                "the gateway, then restart Workbench from its page in Eugene's console."
            )
        if self._key_file is None:
            return (
                "This Workbench was started without a key. Install it from Eugene's app catalogue."
            )
        return None

    def _token(self) -> str:
        reason = self.why_not()
        if reason is not None:
            raise HubError(reason, status=503, kind="setup")
        assert self._key_file is not None
        try:
            mtime = self._key_file.stat().st_mtime
            if self._key is None or self._key[0] != mtime:
                self._key = (mtime, self._key_file.read_text(encoding="utf-8").strip())
        except OSError as exc:
            raise HubError(
                f"This Workbench's key file cannot be read ({exc}). Uninstall and install "
                "Workbench again from Eugene's console.",
                status=503,
                kind="setup",
            ) from exc
        return self._key[1]

    async def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token()}"}
        if self._person_token is not None:
            headers["X-Eugene-Person-Token"] = await self._person_token()
        return headers

    def _unreachable(self, exc: Exception) -> HubError:
        return HubError(
            f"Workbench cannot reach Eugene's gateway at {self.gateway_url}: "
            f"{str(exc) or type(exc).__name__}",
            status=503,
            kind="unreachable",
        )

    async def models(self) -> dict[str, Any]:
        from .person_context import catalog_only

        headers = (
            {"Authorization": f"Bearer {self._token()}", "X-Eugene-App-Operation": "catalog"}
            if catalog_only.get()
            else await self._headers()
        )
        try:
            response = await self._http.get(f"{self.gateway_url}/v1/models", headers=headers)
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if response.status_code != 200:
            raise refusal(response.status_code, _gateway_words(response))
        body: dict[str, Any] = response.json()
        return body

    async def embeddings(
        self, model: str, inputs: Sequence[str], *, seconds: float = 120.0
    ) -> Embedded:
        """One embeddings request (MR5): a vector for each input, from `model`, and the
        model the gateway says answered. Floats, not base64, and nothing else in the body:
        the gateway ignores `dimensions` and `user`, and says so. An alias embeds with its
        first target only, and a stopped model is not woken (503): both are the gateway's.
        Raises `HubError`, with `retry_after` when the gateway asked us to wait."""
        headers = await self._headers()
        body = {"model": model, "input": list(inputs), "encoding_format": "float"}
        started = time.perf_counter()
        try:
            response = await self._http.post(
                f"{self.gateway_url}/v1/embeddings",
                json=body,
                headers=headers,
                timeout=httpx.Timeout(10.0, read=seconds),
            )
        except httpx.TimeoutException as exc:
            raise HubError(
                f"Eugene's gateway did not answer the search request within {seconds:.0f} "
                f"seconds ({type(exc).__name__}).",
                status=504,
                kind="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if response.status_code != 200:
            raise refusal(
                response.status_code,
                _gateway_words(response),
                _gateway_param(response),
                _retry_after(response),
            )
        return _embedded(response, len(inputs), time.perf_counter() - started)

    async def rerank(
        self, model: str, query: str, documents: Sequence[str], *, seconds: float = 20.0
    ) -> Reranked:
        """One sort (MR6b): how well each of `documents` answers `query`, by `model`, which
        is a result sorter the gateway lists with the `rerank` surface. Jina's body, with
        nothing in it the gateway does not honour: no `top_n` (every passage is scored; the
        caller puts them in order), and no `return_documents` (the caller has the text). A
        passage longer than the sorter reads is the gateway's 400, not cut. The request is
        given up on after `seconds`. Raises `HubError`, with `retry_after` when the gateway
        asked us to wait."""
        headers = await self._headers()
        body = {"model": model, "query": query, "documents": list(documents)}
        started = time.perf_counter()
        try:
            response = await self._http.post(
                f"{self.gateway_url}/v1/rerank",
                json=body,
                headers=headers,
                timeout=httpx.Timeout(min(10.0, seconds), read=seconds),
            )
        except httpx.TimeoutException as exc:
            raise HubError(
                f"Eugene's gateway did not answer the sort request within {seconds:g} "
                f"seconds ({type(exc).__name__}).",
                status=504,
                kind="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if response.status_code != 200:
            raise refusal(
                response.status_code,
                _gateway_words(response),
                _gateway_param(response),
                _retry_after(response),
            )
        return _reranked(response, len(documents), time.perf_counter() - started)

    async def images(
        self, body: dict[str, Any], *, edit: bool
    ) -> tuple[dict[str, Any], str | None]:
        """One image request (P4), and the gateway's request id. A request
        with reference images goes to `/edits` as JSON with `data:` URLs,
        the shape the gateway takes from a server. Raises `HubError`."""
        headers = await self._headers()
        url = f"{self.gateway_url}/v1/images/{'edits' if edit else 'generations'}"
        try:
            response = await self._http.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if response.status_code != 200:
            raise refusal(response.status_code, _gateway_words(response), _gateway_param(response))
        try:
            answer = response.json()
        except ValueError as exc:
            raise HubError(
                "The gateway answered the image request with something that is not JSON "
                f"({response.headers.get('content-type') or 'no type'}).",
                kind="gateway",
            ) from exc
        if not isinstance(answer, dict):
            raise HubError("The gateway's answer to the image request was not an object.")
        return answer, response.headers.get("x-request-id")

    async def speech(self, body: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
        """One spoken clip (P3a): its bytes, and the response's headers, which
        carry what served it (`x-eugene-plexus-*`). Raises `HubError`."""
        headers = await self._headers()
        try:
            response = await self._http.post(
                f"{self.gateway_url}/v1/audio/speech", json=body, headers=headers
            )
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if response.status_code != 200:
            raise refusal(response.status_code, _gateway_words(response), _gateway_param(response))
        return response.content, dict(response.headers)

    async def transcribe(
        self,
        fields: dict[str, str],
        file: tuple[str, bytes, str],
        *,
        translate: bool = False,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """One transcription (P3b), or translation into English (P3-4), as
        the OpenAI SDK sends it: a multipart form. Raises `HubError`."""
        headers = await self._headers()
        door = "translations" if translate else "transcriptions"
        try:
            response = await self._http.post(
                f"{self.gateway_url}/v1/audio/{door}",
                data=fields,
                files={"file": file},
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if response.status_code != 200:
            raise refusal(response.status_code, _gateway_words(response), _gateway_param(response))
        try:
            answer = response.json()
        except ValueError as exc:
            raise HubError(
                "The gateway answered the transcription with something that is not JSON "
                f"({response.headers.get('content-type') or 'no type'}).",
                kind="gateway",
            ) from exc
        if not isinstance(answer, dict) or not isinstance(answer.get("text"), str):
            raise HubError("The gateway's transcription had no text in it.", kind="gateway")
        return answer, dict(response.headers)

    async def video(self, body: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        """Submit one video job (P5): the gateway's `VideoResource`, whose
        `id` is the handle to poll, and the request id. Raises `HubError`."""
        headers = await self._headers()
        try:
            response = await self._http.post(
                f"{self.gateway_url}/v1/videos", json=body, headers=headers
            )
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if response.status_code != 200:
            raise refusal(response.status_code, _gateway_words(response), _gateway_param(response))
        answer = _video_resource(response, "the video request")
        return answer, response.headers.get("x-request-id")

    async def video_job(self, handle: str) -> dict[str, Any]:
        """One poll of a video job. Raises `HubError`, whose `status` says
        whether polling again can help (a 503 says it can)."""
        headers = await self._headers()
        try:
            response = await self._http.get(
                f"{self.gateway_url}/v1/videos/{handle}", headers=headers
            )
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if response.status_code != 200:
            raise refusal(response.status_code, _gateway_words(response), _gateway_param(response))
        return _video_resource(response, "a poll of the video job")

    async def video_content(self, handle: str, write: Any) -> int:
        """A finished job's MP4, handed to `write` a chunk at a time as it
        arrives (a long video is not held in memory). Returns the bytes
        written. Raises `HubError`."""
        headers = await self._headers()
        url = f"{self.gateway_url}/v1/videos/{handle}/content"
        written = 0
        try:
            async with self._http.stream("GET", url, headers=headers) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise refusal(
                        response.status_code, _gateway_words(response), _gateway_param(response)
                    )
                async for chunk in response.aiter_bytes():
                    await write(chunk)
                    written += len(chunk)
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        return written

    async def complete(self, body: dict[str, Any], *, seconds: float = 120.0) -> dict[str, Any]:
        """One chat completion, not streamed (MR8's memory suggestions): the gateway's
        answer as it sent it. Raises `HubError`."""
        headers = await self._headers()
        try:
            response = await self._http.post(
                f"{self.gateway_url}/v1/chat/completions",
                json={**body, "stream": False},
                headers=headers,
                timeout=httpx.Timeout(10.0, read=seconds),
            )
        except httpx.TimeoutException as exc:
            raise HubError(
                f"Eugene's gateway did not answer within {seconds:.0f} seconds "
                f"({type(exc).__name__}).",
                status=504,
                kind="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if response.status_code != 200:
            raise refusal(response.status_code, _gateway_words(response))
        try:
            answer: dict[str, Any] = response.json()
        except ValueError as exc:
            raise HubError(
                "Eugene's gateway answered with something that is not JSON.", kind="stream"
            ) from exc
        return answer

    async def stream_chat(self, body: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """The chunks of one streamed answer, parsed. Raises `HubError`."""
        headers = await self._headers()
        body = dict(body)
        repetition_mode = body.pop("_repetition_mode", None)
        if repetition_mode is not None:
            headers["X-Eugene-Repetition-Mode"] = repetition_mode
        url = f"{self.gateway_url}/v1/chat/completions"
        try:
            async with self._http.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise refusal(response.status_code, _gateway_words(response))
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(chunk, dict) and isinstance(chunk.get("error"), dict):
                        error = chunk["error"]
                        words = str(error.get("message") or "the answer stopped part-way")
                        # Row 26: a streamed request the backend refused as longer than the
                        # model's window comes as an `upstream_error` frame with no code, the
                        # backend's words inside (gateway#18), before any token.
                        raise HubError(
                            words,
                            kind="repetition"
                            if error.get("code") == "repetition_detected"
                            else "context"
                            if long_chats.is_overflow(words)
                            else "stream",
                            words=words,
                        )
                    if isinstance(chunk, dict):
                        yield chunk
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        # A stream that ends with neither [DONE] nor an error is a backend
        # that stopped mid-answer, not one that finished (M10's lesson).
        raise HubError("The answer stopped part-way: the gateway closed the stream.", kind="stream")
