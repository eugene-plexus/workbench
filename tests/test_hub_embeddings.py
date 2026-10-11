"""The gateway's embeddings door, as Workbench asks it (MR5): what is sent, what is checked
in the answer before it is believed, what a refusal is said as, and `Retry-After`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from eugene_plexus_workbench.hub import Hub, HubError
from eugene_plexus_workbench.knowledge.meaning import problem, too_long

Handler = Callable[[httpx.Request], httpx.Response]


def hub_over(tmp_path: Path, handler: Handler) -> Hub:
    key = tmp_path / "key"
    key.write_text("app-key", encoding="utf-8")
    hub = Hub("http://gateway.test", key)
    hub._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    return hub


def good(count: int = 2, model: str = "m", dim: int = 3) -> dict[str, Any]:
    return {
        "object": "list",
        "model": model,
        "data": [
            {"object": "embedding", "index": i, "embedding": [float(i + 1)] * dim}
            for i in range(count)
        ],
        "usage": {"prompt_tokens": 7, "total_tokens": 7},
    }


async def test_a_request_names_the_model_and_the_inputs_and_nothing_the_gateway_ignores(
    tmp_path: Path,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=good(), headers={"x-request-id": "r-9"})

    hub = hub_over(tmp_path, handler)
    got = await hub.embeddings("m", ["one", "two"])
    [request] = seen
    assert request.method == "POST" and request.url.path == "/v1/embeddings"
    assert request.headers["authorization"] == "Bearer app-key"
    assert json.loads(request.content) == {
        "model": "m",
        "input": ["one", "two"],
        "encoding_format": "float",
    }, "no dimensions and no user: the gateway ignores both and says so"
    assert (got.model, got.tokens, got.request_id) == ("m", 7, "r-9")
    assert got.vectors == [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]] and got.seconds >= 0
    await hub.aclose()


async def test_the_answer_is_put_in_the_order_the_inputs_were_sent_whatever_order_it_came_in(
    tmp_path: Path,
) -> None:
    body = good(3)
    body["data"].reverse()
    hub = hub_over(tmp_path, lambda r: httpx.Response(200, json=body))
    got = await hub.embeddings("m", ["a", "b", "c"])
    assert [v[0] for v in got.vectors] == [1.0, 2.0, 3.0]
    await hub.aclose()


async def test_the_model_the_gateway_says_answered_is_what_comes_back_not_the_name_asked(
    tmp_path: Path,
) -> None:
    hub = hub_over(tmp_path, lambda r: httpx.Response(200, json=good(1, model="the-first-target")))
    got = await hub.embeddings("an-alias", ["x"])
    assert got.model == "the-first-target"
    await hub.aclose()


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("not json", "it was not JSON"),
        ([1, 2], "it was not an object"),
        ({**good(), "model": ""}, "it named no model"),
        ({k: v for k, v in good().items() if k != "model"}, "it named no model"),
        (good(1), "it held 1 vectors for 2"),
        ({**good(), "data": "none"}, "it held no vectors for 2"),
        (
            {
                **good(),
                "data": [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [1.0]}],
            },
            "missing, repeated or not a list",
        ),
        (
            {
                **good(),
                "data": [{"index": 0, "embedding": [1.0]}, {"index": 5, "embedding": [1.0]}],
            },
            "missing, repeated or not a list",
        ),
        (
            {
                **good(),
                "data": [{"index": 0, "embedding": "AAAA"}, {"index": 1, "embedding": [1.0]}],
            },
            "missing, repeated or not a list",
        ),
        (
            {
                **good(),
                "data": [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1.0, 2.0]}],
            },
            "not all the same length",
        ),
    ],
)
async def test_an_answer_that_is_not_what_was_asked_for_is_said_as_what_it_is(
    tmp_path: Path, body: Any, why: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(200, text=body, headers={"content-type": "text/plain"})
        return httpx.Response(200, json=body)

    hub = hub_over(tmp_path, handler)
    with pytest.raises(HubError, match="not usable") as caught:
        await hub.embeddings("m", ["a", "b"])
    assert why in caught.value.message and caught.value.kind == "gateway"
    await hub.aclose()


@pytest.mark.parametrize(
    ("header", "seconds"),
    [("7", 7.0), ("2.5", 2.5), ("0", 0.0), ("999999", 3600.0), ("soon", None), ("", None)],
)
async def test_a_refusal_at_the_keys_limit_carries_how_long_the_gateway_asked_us_to_wait(
    tmp_path: Path, header: str, seconds: float | None
) -> None:
    headers = {"retry-after": header} if header else {}
    hub = hub_over(
        tmp_path,
        lambda r: httpx.Response(
            429, json={"error": {"message": "rate limit: 60 requests per minute"}}, headers=headers
        ),
    )
    with pytest.raises(HubError) as caught:
        await hub.embeddings("m", ["a"])
    error = caught.value
    assert (error.status, error.kind, error.retry_after) == (429, "limit", seconds)
    assert error.words == "rate limit: 60 requests per minute"
    assert "at its limit" in error.message and "everyone" in error.message.lower()
    await hub.aclose()


async def test_retry_after_may_be_a_date_and_a_date_in_the_past_is_no_wait(tmp_path: Path) -> None:
    soon = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    past = format_datetime(datetime.now(UTC) - timedelta(seconds=30), usegmt=True)
    waits = []
    for date in (soon, past):
        hub = hub_over(
            tmp_path,
            lambda r, d=date: httpx.Response(
                429, json={"error": {"message": "x"}}, headers={"retry-after": d}
            ),
        )
        with pytest.raises(HubError) as caught:
            await hub.embeddings("m", ["a"])
        waits.append(caught.value.retry_after)
        await hub.aclose()
    assert waits[0] is not None and 20 < waits[0] <= 30
    assert waits[1] == 0.0


async def test_a_search_model_that_is_not_running_is_a_503_with_the_gateways_words(
    tmp_path: Path,
) -> None:
    hub = hub_over(
        tmp_path,
        lambda r: httpx.Response(503, json={"error": {"message": "No backend for 'm' is ready."}}),
    )
    with pytest.raises(HubError) as caught:
        await hub.embeddings("m", ["a"])
    assert (caught.value.status, caught.value.kind) == (503, "gateway")
    assert caught.value.message == "No backend for 'm' is ready."
    await hub.aclose()


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_key_is_said_as_a_key_that_was_refused(tmp_path: Path, status: int) -> None:
    hub = hub_over(
        tmp_path, lambda r: httpx.Response(status, json={"error": {"message": "revoked"}})
    )
    with pytest.raises(HubError) as caught:
        await hub.embeddings("m", ["a"])
    assert caught.value.kind == "key" and "refused the key" in caught.value.message
    await hub.aclose()


async def test_a_gateway_that_does_not_answer_in_time_and_one_that_is_not_there_are_said_apart(
    tmp_path: Path,
) -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("took too long", request=request)

    def gone(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    hub = hub_over(tmp_path, slow)
    with pytest.raises(HubError) as caught:
        await hub.embeddings("m", ["a"], seconds=20)
    assert (caught.value.kind, caught.value.status) == ("timeout", 504)
    assert "within 20 seconds" in caught.value.message
    await hub.aclose()
    hub = hub_over(tmp_path, gone)
    with pytest.raises(HubError) as caught:
        await hub.embeddings("m", ["a"])
    assert caught.value.kind == "unreachable" and "cannot reach" in caught.value.message
    await hub.aclose()


async def test_with_no_key_or_no_gateway_it_says_so_before_it_asks(tmp_path: Path) -> None:
    hub = Hub(None, None)
    with pytest.raises(HubError) as caught:
        await hub.embeddings("m", ["a"])
    assert caught.value.kind == "setup" and caught.value.status == 503
    await hub.aclose()


# --------------------------------------------------------------------------- #
# what a refusal is
# --------------------------------------------------------------------------- #


def refusal(status: int, words: str, kind: str = "gateway") -> HubError:
    return HubError(words, status=status, kind=kind, words=words)


@pytest.mark.parametrize(
    ("error", "long"),
    [
        (
            refusal(
                400, "input (1631 tokens) is too large to process. increase the physical batch size"
            ),
            True,
        ),
        (refusal(500, "input (1631 tokens) is too large to process"), True),
        (refusal(502, "upstream said: request exceeds the available context size"), True),
        (refusal(400, "the input is longer than the model's maximum context length"), True),
        (refusal(413, "payload"), True),
        (refusal(422, "unprocessable"), True),
        (refusal(400, "the model does not take token arrays"), False),
        (refusal(503, "the input is too long, and it is not ready"), False),
        (refusal(429, "too many requests"), False),
        (refusal(500, "something broke"), False),
    ],
)
def test_a_refusal_for_length_is_told_from_one_that_waiting_would_mend(
    error: HubError, long: bool
) -> None:
    assert too_long(error) is long


@pytest.mark.parametrize(
    ("error", "code", "said"),
    [
        (
            HubError("busy", status=429, kind="limit", words="rate limit", retry_after=3),
            "limited",
            "Searching by words only: this Workbench's key is at its limit (rate limit), so a "
            "question could not be searched by meaning. The owner can raise the key's limits in "
            "Eugene's console.",
        ),
        (
            HubError("Eugene refused the key.", status=401, kind="key"),
            "key",
            "Searching by words only: Eugene refused the key.",
        ),
        (
            HubError(
                "Workbench cannot reach Eugene's gateway at http://x: refused",
                status=503,
                kind="unreachable",
            ),
            "unreachable",
            "Searching by words only: Workbench cannot reach Eugene's gateway at http://x: refused",
        ),
        (
            refusal(503, "No backend is ready."),
            "not_running",
            "Searching by words only: the search model m is not running. "
            "Start it in Eugene's console under Backends.",
        ),
        (
            refusal(404, "no such model"),
            "not_running",
            "Searching by words only: the search model m is not running. "
            "Start it in Eugene's console under Backends.",
        ),
        (
            HubError("did not answer within 20 seconds", status=504, kind="timeout"),
            "slow",
            "Searching by words only: did not answer within 20 seconds",
        ),
        (
            refusal(400, "model serves chat"),
            "refused",
            "Searching by words only: the search model m refused the request (model serves chat).",
        ),
        (
            refusal(500, "boom"),
            "error",
            "Searching by words only: the search model m failed (boom).",
        ),
    ],
)
def test_every_failure_is_said_with_what_was_observed_and_begins_by_words_only(
    error: HubError, code: str, said: str
) -> None:
    assert problem(error, "m") == (code, said)
