"""Identity remains private and follows foreground/background model work."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from eugene_plexus_workbench.hub import Hub, HubError
from eugene_plexus_workbench.knowledge.listing import Listing
from eugene_plexus_workbench.person_context import acting_as, current
from eugene_plexus_workbench.sessions import Sessions, digest
from eugene_plexus_workbench.signin import Identity, SignInRefused
from eugene_plexus_workbench.store import Person, Store


async def test_two_people_and_background_work_never_share_an_identity(tmp_path):
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    sessions = Sessions(store, SimpleNamespace(refresh=AsyncMock()))
    try:
        ada = await sessions.create("ada", "refresh-ada", 600, "proof-ada")
        bob = await sessions.create("bob", "refresh-bob", 600, "proof-bob")

        async def ask(sub, session):
            with acting_as(sub, session):
                await asyncio.sleep(0)
                return await sessions.gateway_token()

        assert await asyncio.gather(
            ask("ada", digest(ada.cookie)), ask("bob", digest(bob.cookie)), ask("ada", None)
        ) == ["proof-ada", "proof-bob", "proof-ada"]
        assert current.get() is None
        with pytest.raises(HubError):
            await sessions.gateway_token()
        with acting_as("ada", digest(bob.cookie)), pytest.raises(HubError):
            await sessions.gateway_token()
        await store.delete_session(digest(ada.cookie))
        with acting_as("ada"), pytest.raises(HubError):
            await sessions.gateway_token()
        with acting_as("bob"):
            assert await sessions.gateway_token() == "proof-bob"
    finally:
        await store.close()


async def test_legacy_session_refreshes_once_and_logout_never_falls_back(tmp_path):
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    identity = Identity("ada", "Ada", "Ada", "member", "refresh", 600, "proof")
    provider = SimpleNamespace(refresh=AsyncMock(return_value=identity))
    sessions = Sessions(store, provider)
    try:
        await store.upsert_person(Person("ada", "Ada", "member"))
        created = await sessions.create("ada", "refresh", 600)
        with acting_as("ada", digest(created.cookie)):
            assert await sessions.gateway_token() == "proof"
            assert await sessions.gateway_token() == "proof"
            provider.refresh.assert_awaited_once()
            await store.refreshed(digest(created.cookie), "refresh", time.time() - 1, "proof")
            provider.refresh.side_effect = SignInRefused("revoked")
            with pytest.raises(HubError) as refused:
                await sessions.gateway_token()
            assert refused.value.status == 401
        assert await store.session(digest(created.cookie)) is None
    finally:
        await store.close()


async def test_hub_sends_proof_for_chat_search_sorting_and_listing(tmp_path):
    key = tmp_path / "key"
    key.write_text("app-key")
    tokens = AsyncMock(return_value="signed-person-proof")
    hub = Hub("http://gateway.test", key, person_token=tokens)
    seen = []

    def handle(request):
        seen.append(request.url.path)
        assert request.headers["authorization"] == "Bearer app-key"
        assert request.headers["x-eugene-person-token"] == "signed-person-proof"
        if request.url.path.endswith("embeddings"):
            return httpx.Response(
                200,
                json={
                    "model": "embed",
                    "data": [{"index": 0, "embedding": [1.0, 2.0]}],
                    "usage": {"prompt_tokens": 1},
                },
            )
        if request.url.path.endswith("rerank"):
            return httpx.Response(
                200, json={"model": "sort", "results": [{"index": 0, "relevance_score": 1.0}]}
            )
        if request.url.path.endswith("completions"):
            return httpx.Response(
                200,
                content='data: {"choices": []}\n\ndata: [DONE]\n\n',
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json={"data": []})

    await hub._http.aclose()
    hub._http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        await hub.models()
        await hub.embeddings("embed", ["private text"])
        await hub.rerank("sort", "question", ["document"])
        async for _ in hub.stream_chat({"model": "chat", "messages": []}):
            pass
        assert len(seen) == 4
        tokens.side_effect = HubError("sign-in ended", status=401)
        with pytest.raises(HubError):
            await hub.models()
        assert len(seen) == 4  # no retry with app-only authority
    finally:
        await hub.aclose()


async def test_model_listing_cache_does_not_cross_people_or_sessions():
    gateway = SimpleNamespace(models=AsyncMock(return_value={"data": [{"id": "ada-model"}]}))
    listing = Listing(gateway)
    with acting_as("ada", "one"):
        ada = await listing.look()
        assert await listing.look() is ada
    gateway.models.return_value = {"data": [{"id": "bob-model"}]}
    with acting_as("bob", "two"):
        assert listing.last is None
        assert (await listing.look()).listing == gateway.models.return_value
    with acting_as("ada", "new-session"):
        await listing.look()
    assert gateway.models.await_count == 3
