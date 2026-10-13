"""The gateway's rerank door, as Workbench asks it (MR6b): what is sent, what is checked in the
answer before it is believed, the order a sort comes to, what a refusal is said as, and the time a
request is given.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from eugene_plexus_workbench.hub import Hub, HubError, Reranked

Handler = Callable[[httpx.Request], httpx.Response]


def hub_over(tmp_path: Path, handler: Handler) -> Hub:
    key = tmp_path / "key"
    key.write_text("app-key", encoding="utf-8")
    hub = Hub("http://gateway.test", key)
    hub._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    return hub


def good(scores: list[float], model: str = "sorter") -> dict[str, Any]:
    """An answer as the gateway makes it: best first by score, then by index."""
    ranked = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    return {
        "object": "list",
        "model": model,
        "results": [{"index": i, "relevance_score": scores[i]} for i in ranked],
        "usage": {"prompt_tokens": 90, "total_tokens": 90},
    }


async def test_a_request_is_jinas_body_with_the_model_the_question_and_the_passages_and_nothing_else(
    tmp_path: Path,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=good([0.2, 0.9]), headers={"x-request-id": "r-4"})

    hub = hub_over(tmp_path, handler)
    got = await hub.rerank("sorter", "what is asked?", ["first passage", "second passage"])
    [request] = seen
    assert request.method == "POST" and request.url.path == "/v1/rerank"
    assert request.headers["authorization"] == "Bearer app-key"
    assert json.loads(request.content) == {
        "model": "sorter",
        "query": "what is asked?",
        "documents": ["first passage", "second passage"],
    }, "no top_n (every passage is scored) and no return_documents (the caller has the text)"
    assert got.scores == [0.2, 0.9] and got.tokens == 90 and got.model == "sorter"
    assert got.request_id == "r-4" and got.seconds >= 0
    await hub.aclose()


async def test_the_order_is_best_first_and_a_tie_keeps_the_order_the_passages_were_sent_in() -> (
    None
):
    assert Reranked([0.2, 0.9, 0.5], None, 0.0).order == [1, 2, 0]
    assert Reranked([0.5, 0.5, 0.9, 0.5], None, 0.0).order == [2, 0, 1, 3]
    assert Reranked([], None, 0.0).order == []


async def test_each_score_goes_with_the_passage_it_is_for_whatever_order_the_answer_lists_them_in(
    tmp_path: Path,
) -> None:
    body = good([0.1, 0.7, 0.4])
    body["results"].reverse()
    hub = hub_over(tmp_path, lambda r: httpx.Response(200, json=body))
    got = await hub.rerank("sorter", "q", ["a", "b", "c"])
    assert got.scores == [0.1, 0.7, 0.4] and got.order == [1, 2, 0]
    await hub.aclose()


async def test_an_answer_that_names_no_model_or_no_usage_is_still_good(tmp_path: Path) -> None:
    body = {"results": [{"index": 0, "relevance_score": 1}, {"index": 1, "relevance_score": 0}]}
    hub = hub_over(tmp_path, lambda r: httpx.Response(200, json=body))
    got = await hub.rerank("sorter", "q", ["a", "b"])
    assert got.scores == [1.0, 0.0] and got.model is None and got.tokens is None
    await hub.aclose()


def results(*items: tuple[Any, Any]) -> dict[str, Any]:
    return {"results": [{"index": i, "relevance_score": s} for i, s in items]}


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("not json", "it was not JSON"),
        ([1, 2], "it was not an object"),
        ({"object": "list"}, "it held no scores for 2 passages"),
        ({"results": "none"}, "it held no scores for 2 passages"),
        (results((0, 0.5)), "it held 1 scores for 2 passages"),
        (results((0, 0.5), (1, 0.5), (1, 0.4)), "it held 3 scores for 2 passages"),
        (results((0, 0.5), (0, 0.4)), "missing, repeated or not a number"),
        (results((0, 0.5), (5, 0.4)), "missing, repeated or not a number"),
        (results((0, 0.5), (-1, 0.4)), "missing, repeated or not a number"),
        (results((0, 0.5), ("1", 0.4)), "missing, repeated or not a number"),
        (results((0, 0.5), (True, 0.4)), "missing, repeated or not a number"),
        (results((0, 0.5), (1, "high")), "missing, repeated or not a number"),
        (results((0, 0.5), (1, None)), "missing, repeated or not a number"),
        (results((0, 0.5), (1, True)), "missing, repeated or not a number"),
        ({"results": [{"index": 0, "relevance_score": 0.5}, "nope"]}, "missing, repeated"),
        ({"results": [{"index": 0}, {"index": 1}]}, "missing, repeated or not a number"),
    ],
)
async def test_an_answer_that_is_not_a_score_for_each_passage_is_said_as_what_it_is(
    tmp_path: Path, body: Any, why: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(200, text=body, headers={"content-type": "text/plain"})
        return httpx.Response(200, json=body)

    hub = hub_over(tmp_path, handler)
    with pytest.raises(HubError, match="not usable") as caught:
        await hub.rerank("sorter", "q", ["a", "b"])
    assert why in caught.value.message and caught.value.kind == "unusable"
    await hub.aclose()


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
async def test_a_score_that_is_not_a_finite_number_is_not_believed(
    tmp_path: Path, literal: str
) -> None:
    content = f'{{"results": [{{"index": 0, "relevance_score": {literal}}}]}}'.encode()
    hub = hub_over(
        tmp_path,
        lambda r: httpx.Response(
            200, content=content, headers={"content-type": "application/json"}
        ),
    )
    with pytest.raises(HubError, match="not a number"):
        await hub.rerank("sorter", "q", ["a"])
    await hub.aclose()


@pytest.mark.parametrize(
    "literal",
    [
        "1" + "0" * 400,  # an integer too large to be a float: `math.isfinite` raised OverflowError
        "-" + "9" * 400,
        "1e999",  # JSON lets it through, and Python reads it as infinity
        "-1e999",
    ],
)
async def test_a_score_too_large_to_be_a_number_is_an_unusable_answer_and_not_a_failure_in_workbench(
    tmp_path: Path, literal: str
) -> None:
    """A score of four hundred digits is not a `HubError` when it reaches `math.isfinite`, it is an
    `OverflowError`: a failure in Workbench itself that was neither said as the gateway's answer
    being unusable nor kept as a reason, and which a sorter's probe did not catch at all."""
    content = f'{{"results": [{{"index": 0, "relevance_score": {literal}}}]}}'.encode()
    hub = hub_over(
        tmp_path,
        lambda r: httpx.Response(
            200, content=content, headers={"content-type": "application/json"}
        ),
    )
    with pytest.raises(HubError, match="not a number") as caught:
        await hub.rerank("sorter", "q", ["a"])
    assert caught.value.kind == "unusable"
    await hub.aclose()


@pytest.mark.parametrize(
    "usage", [{"prompt_tokens": "many"}, {"prompt_tokens": True}, "none", None, {}]
)
async def test_a_tokens_count_that_is_not_a_number_is_left_out_not_believed(
    tmp_path: Path, usage: Any
) -> None:
    body = {**good([0.3]), "usage": usage}
    hub = hub_over(tmp_path, lambda r: httpx.Response(200, json=body))
    got = await hub.rerank("sorter", "q", ["a"])
    assert got.tokens is None and got.scores == [0.3]
    await hub.aclose()


async def test_a_refusal_is_said_with_what_the_gateway_said_and_a_retry_after_is_kept(
    tmp_path: Path,
) -> None:
    def refuse(status: int, message: str, **headers: str) -> Hub:
        return hub_over(
            tmp_path,
            lambda r: httpx.Response(status, json={"error": {"message": message}}, headers=headers),
        )

    key = refuse(401, "invalid key")
    with pytest.raises(HubError) as caught:
        await key.rerank("sorter", "q", ["a"])
    assert caught.value.kind == "key" and caught.value.status == 401
    limited = refuse(429, "slow down", **{"retry-after": "7"})
    with pytest.raises(HubError) as caught:
        await limited.rerank("sorter", "q", ["a"])
    assert caught.value.status == 429 and caught.value.retry_after == 7.0
    assert caught.value.kind == "limit" and caught.value.words == "slow down"
    long = refuse(
        400, "input (2301 tokens) is too large to process. The sorter reads at most 2048 tokens"
    )
    with pytest.raises(HubError) as caught:
        await long.rerank("sorter", "q", ["a"])
    assert caught.value.status == 400 and "2048" in (caught.value.words or "")
    for status in (404, 503, 502, 504):
        gone = refuse(status, f"it said {status}")
        with pytest.raises(HubError) as caught:
            await gone.rerank("sorter", "q", ["a"])
        assert caught.value.status == status and caught.value.words == f"it said {status}"
    for hub in (key, limited, long):
        await hub.aclose()


async def test_a_request_is_given_the_seconds_asked_for_and_a_slow_answer_is_said_as_slow(
    tmp_path: Path,
) -> None:
    given: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        given.append(request.extensions["timeout"])
        raise httpx.ReadTimeout("took too long", request=request)

    hub = hub_over(tmp_path, handler)
    with pytest.raises(
        HubError, match=r"did not answer the sort request within 6 seconds"
    ) as caught:
        await hub.rerank("sorter", "q", ["a"], seconds=6.0)
    assert caught.value.kind == "timeout" and caught.value.status == 504
    assert given[0]["read"] == 6.0
    assert given[0]["connect"] == 6.0, "the whole request is given no more than it was told"
    await hub.aclose()


async def test_a_gateway_that_cannot_be_reached_is_said_as_that(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    hub = hub_over(tmp_path, handler)
    with pytest.raises(HubError, match="cannot reach Eugene's gateway") as caught:
        await hub.rerank("sorter", "q", ["a"])
    assert caught.value.kind == "unreachable" and caught.value.status == 503
    await hub.aclose()


async def test_a_503_that_asks_for_a_wait_keeps_the_seconds_and_what_the_gateway_said(
    tmp_path: Path,
) -> None:
    """A gateway that holds requests back while it tries a backend again says so as a 503 with a
    `Retry-After`: the seconds are kept, so that a sorter that is running is not told to a person
    as one that is stopped (a 503 with none is nothing ready)."""
    said = "A backend serving this model is not ready yet: Eligible backends are cooling down."
    held = hub_over(
        tmp_path,
        lambda r: httpx.Response(
            503, json={"error": {"message": said}}, headers={"retry-after": "1"}
        ),
    )
    with pytest.raises(HubError) as caught:
        await held.rerank("sorter", "q", ["a"])
    assert caught.value.status == 503 and caught.value.retry_after == 1.0
    assert caught.value.words == said and caught.value.kind == "gateway"
    nothing = hub_over(
        tmp_path, lambda r: httpx.Response(503, json={"error": {"message": "No backend is ready."}})
    )
    with pytest.raises(HubError) as caught:
        await nothing.rerank("sorter", "q", ["a"])
    assert caught.value.status == 503 and caught.value.retry_after is None
    await held.aclose()
    await nothing.aclose()
