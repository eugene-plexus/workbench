"""Rich content RC4: where a searched answer cites each web source, kept from
the gateway's own `url_citation` places and given to the page as the page
counts (UTF-16), and none made up (workbench-rich-content-roadmap.md RC4)."""

from __future__ import annotations

from typing import Any

from eugene_plexus_workbench.answers import cite, sources_view
from eugene_plexus_workbench.store import Message

from .conftest import World

PAGE = "https://example.org/page"
OTHER = "https://example.org/other"
WORDS = ["Read ", f"[the page]({PAGE})", " 😀 and ", OTHER, "/ too."]
TEXT = "".join(WORDS)


def js(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def span(url_text: str, url: str, title: str) -> dict[str, Any]:
    start = TEXT.index(url_text)
    return {"url": url, "title": title, "start_index": start, "end_index": start + len(url_text)}


def test_a_searched_answer_says_where_it_cites_each_page_as_the_page_counts(
    world: World,
) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    world.gateway.words = WORDS
    world.gateway.citations = [
        span(f"[the page]({PAGE})", PAGE, "The page"),
        # The gateway cites an address without its slash where the text has one.
        span(OTHER, OTHER + "/", "Another"),
        # A place that does not hold its address is no place.
        {"url": "https://example.org/x", "title": "X", "start_index": 0, "end_index": 4},
        # And one past the end of the text.
        {"url": PAGE, "title": "The page", "start_index": 5, "end_index": 999},
    ]
    sent = ada.post(f"/api/chats/{chat}/messages", json={"content": "Find it", "search": True})
    assert sent.status_code == 201, sent.text
    answer = ada.wait_answer(chat)
    assert answer["content"] == TEXT
    sources = {s["url"]: s for s in answer["sources"]}
    start = TEXT.index("[the page]")
    assert sources[PAGE]["marks"] == [
        [js(TEXT[:start]), js(TEXT[: start + len(f"[the page]({PAGE})")])]
    ]
    # After the emoji: two units on the page, one in Python.
    at = TEXT.index(OTHER)
    # The text holds the address with its slash, which is cited first, as the gateway does.
    assert sources[OTHER + "/"]["marks"] == [[js(TEXT[:at]), js(TEXT[: at + len(OTHER) + 1])]]
    assert js(TEXT[:at]) == at + 1
    assert "marks" not in sources["https://example.org/x"]
    assert all("spans" not in s for s in answer["sources"]), "Python's indexes stay inside"


def test_a_source_with_no_place_keeps_no_marks(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Find it", "search": True})
    (source,) = ada.wait_answer(chat)["sources"]
    assert source == {"kind": "web", "url": "https://example.org/page", "title": "A page"}


def test_a_place_counts_from_where_its_request_began() -> None:
    message = Message(
        id="m", chat_id="c", seq=0, role="assistant", status="done", created_at=0,
        content="Before the tools. 😀 See https://a.example/x now.",
    )  # fmt: skip
    base = message.content.index("See")
    source: dict[str, Any] = {"kind": "web", "url": "https://a.example/x", "title": "A"}
    cite(message, source, {"start_index": 4, "end_index": 23}, base)
    assert source["spans"] == [[base + 4, base + 23]]
    message.sources = [source]
    (view,) = sources_view(message)
    assert view["marks"] == [[js(message.content[: base + 4]), js(message.content[: base + 23])]]
    # An address the request's text does not hold has no place.
    gone: dict[str, Any] = {"kind": "web", "url": "https://b.example/", "title": "B"}
    cite(message, gone, {"start_index": 4, "end_index": 23}, base)
    assert "spans" not in gone


def test_a_place_after_a_draft_is_where_the_address_is_not_where_the_gateway_counted() -> None:
    """gateway#16: the gateway counts a draft and its answer joined by a blank
    line it never streams, so its index is two past the text as stored."""
    draft, answer = "Let me search.", "See [A](https://a.com/x) for details, and https://a.com/y."
    message = Message(
        id="m", chat_id="c", seq=0, role="assistant", status="done", created_at=0,
        content=draft + answer,
    )  # fmt: skip
    counted = f"{draft}\n\n{answer}"
    link: dict[str, Any] = {"kind": "web", "url": "https://a.com/x", "title": "A"}
    bare: dict[str, Any] = {"kind": "web", "url": "https://a.com/y/", "title": "Y"}
    cite(message, link, {"start_index": counted.index("[A]")}, 0)
    cite(message, bare, {"start_index": counted.index("https://a.com/y")}, 0)
    assert link["spans"] == [[len(draft) + 4, len(draft) + 4 + len("[A](https://a.com/x)")]]
    a, b = bare["spans"][0]
    assert message.content[a:b] == "https://a.com/y"


def test_an_address_written_twice_is_cited_where_the_gateway_says() -> None:
    text = "First https://a.com/z, then again https://a.com/z."
    message = Message(
        id="m", chat_id="c", seq=0, role="assistant", status="done", created_at=0, content=text
    )
    source: dict[str, Any] = {"kind": "web", "url": "https://a.com/z", "title": "Z"}
    second = text.rindex("https://a.com/z")
    cite(message, source, {"start_index": second + 2}, 0)  # off by two, as after a draft
    assert source["spans"] == [[second, second + len("https://a.com/z")]]
