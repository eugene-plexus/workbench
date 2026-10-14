"""The browser instrument gives slow answers time and reports what stalled."""

from typing import Any

import httpx
import pytest

from . import conftest


class WaitingBrowser:
    def __init__(self, clock: list[float], finish: float | None) -> None:
        self.clock, self.finish = clock, finish

    def get(self, path: str) -> httpx.Response:
        self.clock[0] += 2
        done = self.finish is not None and self.clock[0] >= self.finish
        return httpx.Response(
            200,
            json={
                "messages": [
                    {
                        "id": "answer-under-test",
                        "status": "done" if done else "running",
                        "toolRounds": [{"calls": [{"status": "declined"}]}],
                    }
                ]
            },
        )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [0.0]
    monkeypatch.setattr(conftest.time, "perf_counter", lambda: now[0])
    monkeypatch.setattr(conftest.time, "sleep", lambda _: None)
    return now


def test_an_answer_can_finish_after_the_old_fifteen_second_limit(clock: list[float]) -> None:
    browser: Any = WaitingBrowser(clock, finish=18)
    assert conftest.Browser.wait_answer(browser, "chat")["status"] == "done"


def test_a_timeout_names_the_last_answer_and_tool_state(clock: list[float]) -> None:
    browser: Any = WaitingBrowser(clock, finish=None)
    with pytest.raises(AssertionError, match=r"answer-under-test.*declined"):
        conftest.Browser.wait_answer(browser, "chat", seconds=1)
