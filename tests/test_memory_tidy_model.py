"""Row 27d: model wording waits for the person, and unseen notes stay local."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from eugene_plexus_workbench import config, memory_tidy
from eugene_plexus_workbench.store import MemoryNote, Person, Store


class Hub:
    locality = "local"

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def models(self) -> dict[str, Any]:
        return {
            "data": [
                {"id": "m", "x_eugene_plexus": {"locality": self.locality, "context_length": 32768}}
            ]
        }

    async def complete(self, body: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(body)
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"pairs":[{"a":"a","b":"b","same":true,"title":"I grow red tomatoes.","body":""}]}'
                    }
                }
            ]
        }


async def test_model_merge_is_only_a_proposal(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    await store.open()
    try:
        await store.put_memory_model_choice("ada", "m", "auto")
        for id, text in [
            ("a", "I grow red garden tomatoes."),
            ("b", "I grow red garden beans peas."),
        ]:
            await store.add_memory_note(MemoryNote(id, "ada", text, "fact", 1, 1), limit=100)
        hub = Hub()
        state = SimpleNamespace(store=store, hub=hub)
        await memory_tidy.run(
            store, "ada", "2026-10-23", state=state, person=Person("ada", "Ada", "user")
        )
        [proposal] = await memory_tidy.proposals(store, "ada", "2026-10-23")
        assert proposal["model"] == "m" and proposal["merged"]["title"] == "I grow red tomatoes."
        assert all(n.status == "active" for n in await store.memory_notes("ada"))
        merged = await store.keep_model_merge(
            "ada",
            proposal["id"],
            MemoryNote("new", "ada", "I grow red tomatoes.", "fact", 2, 2),
            100,
        )
        assert merged is not None
        assert {n.id for n in await store.memory_notes("ada")} == {"new"}
        assert {n.id for n in await store.note_history("ada", "new")} == {"a", "b"}
        assert await store.note_merge_history("ada", "new") == ["merged as m proposed; you kept it"]
        hub.requests.clear()
        hub.locality = "external"
        await store.put_setting(config.MEMORY_EXTERNAL_MODELS, "always")
        for id, text in [
            ("hidden-a", "My secret project grows red garden tomatoes."),
            ("hidden-b", "My secret project grows red garden beans peas."),
        ]:
            await store.add_memory_note(
                MemoryNote(id, "ada", text, "fact", 3, 3, auto=True), limit=100
            )
        await memory_tidy.run(
            store, "ada", "2026-10-24", force=True, state=state, person=Person("ada", "Ada", "user")
        )
        assert not hub.requests
    finally:
        await store.close()


async def test_concurrent_daily_reads_ask_the_model_only_once(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    await store.open()
    try:
        await store.put_memory_model_choice("ada", "m", "auto")
        for id, text in [
            ("a", "I grow red garden tomatoes."),
            ("b", "I grow red garden beans peas."),
        ]:
            await store.add_memory_note(MemoryNote(id, "ada", text, "fact", 1, 1), limit=100)
        hub = Hub()
        state = SimpleNamespace(store=store, hub=hub)
        person = Person("ada", "Ada", "member")
        await asyncio.gather(
            *(
                memory_tidy.run(store, "ada", "2026-10-23", state=state, person=person)
                for _ in range(2)
            )
        )
        assert len(hub.requests) == 1
    finally:
        await store.close()


async def test_model_wording_is_dropped_when_a_note_was_edited(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    await store.open()
    try:
        for id, text in [
            ("a", "I grow red garden tomatoes."),
            ("b", "I grow red garden beans peas."),
        ]:
            await store.add_memory_note(MemoryNote(id, "ada", text, "fact", 1, 1), limit=100)
        await store.add_model_tidy(
            "ada", ["a", "b"], "m", {"title": "I grow vegetables.", "body": ""}
        )
        await store.update_memory_note("ada", "a", text="I stopped growing tomatoes.")
        assert await memory_tidy.proposals(store, "ada", "2026-10-23") == []
    finally:
        await store.close()
