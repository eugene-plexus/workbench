"""Row 27c: evidence is a hash and a location; its words are never copied."""

import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

from eugene_plexus_workbench import memory_provenance as provenance
from eugene_plexus_workbench.store import MemoryNote, Message, Store


async def test_remember_links_cited_page_not_its_words(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    await store.open()
    try:
        note = MemoryNote("n", "ada", "I grow beans.", "fact", 1, 1)
        await store.add_memory_note(note, limit=100)
        message = Message(
            "a",
            "c",
            1,
            "assistant",
            "done",
            1,
            content="See [1].",
            sources=[
                {
                    "kind": "web",
                    "url": "https://example.com",
                    "title": "Example",
                    "text": "Page words",
                    "cited": True,
                },
            ],
        )
        await provenance.link_answer(SimpleNamespace(store=store), "ada", "n", message)
        [link] = (await store.note_documents("ada", ["n"]))["n"]
        assert link["scope"] == "web" and link["source"] == "https://example.com"
        assert link["sha256"] == hashlib.sha256(b"Page words").hexdigest()
        assert "Page words" not in str(link)
        assert await provenance.check(SimpleNamespace(store=store), "ada", {"n": [link]}) == {}
    finally:
        await store.close()


async def test_tool_result_is_linked_only_when_ticked_and_errors_are_not_offered(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "db")
    await store.open()
    try:
        for id in ("n1", "n2"):
            await store.add_memory_note(
                MemoryNote(id, "ada", "I grow beans " + id, "fact", 1, 1), limit=100
            )
        answer = Message(
            "a",
            "c",
            1,
            "assistant",
            "done",
            1,
            tool_rounds=[
                {
                    "calls": [
                        {
                            "id": "ok",
                            "serverId": "s",
                            "serverName": "Weather",
                            "tool": "read",
                            "status": "done",
                            "result": "Tool's private words",
                        },
                        {
                            "id": "bad",
                            "serverId": "s",
                            "tool": "read",
                            "status": "failed",
                            "result": "failed",
                        },
                    ]
                }
            ],
        )
        assert [e["key"] for e in provenance.evidence(answer)] == ["tool:ok"]
        await provenance.link_answer(SimpleNamespace(store=store), "ada", "n1", answer)
        assert not await store.note_documents("ada", ["n1"])
        await provenance.link_answer(SimpleNamespace(store=store), "ada", "n2", answer, ["tool:ok"])
        [link] = (await store.note_documents("ada", ["n2"]))["n2"]
        assert link["scope"] == "tool" and link["source"] == "s/read"
        assert "Tool's private words" not in str(link)
    finally:
        await store.close()


async def test_check_again_fetches_only_on_request_and_person_can_accept_new_hash(
    tmp_path: Path,
) -> None:
    class Session:
        tools: ClassVar[dict[str, Any]] = {
            "fetch": (
                {"id": "s", "name": "Web"},
                None,
                "fetch",
                {"properties": {"url": {"type": "string"}}},
            )
        }
        fetched: ClassVar[list[dict[str, Any]]] = []

        def prepare(self, call: dict[str, Any]) -> dict[str, Any]:
            self.fetched.append(call)
            return call

        async def execute(self, call: dict[str, Any]) -> None:
            call.update(status="done", result="New page words")

    class Tools:
        @asynccontextmanager
        async def connect(self, ids: Any, person: Any):
            assert ids == ["s"] and person.sub == "ada"
            yield Session()

    store = Store(tmp_path / "db")
    await store.open()
    try:
        await store.add_memory_note(
            MemoryNote("n", "ada", "I grow beans.", "fact", 1, 1), limit=100
        )
        link = {
            "doc_id": "web",
            "name": "Example",
            "scope": "web",
            "source": "https://example.com",
            "sha256": hashlib.sha256(b"Old page words").hexdigest(),
            "place": [],
        }
        await store.link_note_documents("ada", "n", [link], "answer", 20)
        state = SimpleNamespace(store=store, tools=Tools())
        assert await provenance.check(state, "ada", {"n": [link]}) == {} and not Session.fetched
        result = await provenance.check_web(state, SimpleNamespace(sub="ada"), link, "s", "fetch")
        assert result["changed"] and len(Session.fetched) == 1
        assert (await store.note_documents("ada", ["n"]))["n"][0]["sha256"] == link["sha256"]
        await store.accept_web_hash("ada", "n", "web", result["sha256"])
        assert (await store.note_documents("ada", ["n"]))["n"][0]["sha256"] == result["sha256"]
    finally:
        await store.close()
