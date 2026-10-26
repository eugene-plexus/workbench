"""G1 reference host over real HTTP, OIDC, SQLite and counted external effects.

The fixture adapter is deliberately not an implementation of any UI protocol.
Only tests register it. No production endpoint can register adapters or surfaces.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import sqlite3
import threading
import time
from collections.abc import Coroutine, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from eugene_plexus_workbench.app import DATABASE, create_app
from eugene_plexus_workbench.interaction_store import InteractionError
from eugene_plexus_workbench.interactions import Interactions, NoEffect, Result, Surface
from eugene_plexus_workbench.sessions import Sessions
from eugene_plexus_workbench.settings import Settings
from eugene_plexus_workbench.store import Person, Store

from .conftest import Browser, ServerThread, World
from .test_artifacts import answered
from .test_job_sites import _owner_reads


class Reference:
    def __init__(self) -> None:
        self.effects: list[dict[str, Any]] = []
        self.hold_authorization = False
        self.hold_result = False
        self.denied = False
        self.fail = ""
        self.entered = threading.Event()
        self.service: Interactions
        self.loop: asyncio.AbstractEventLoop
        self.url = ""

    def run[T](self, coro: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=15)

    def validate_surface(self, surface: Surface) -> None:
        if surface.view.get("catalog") != "reference-only":
            raise InteractionError("Unknown reference catalog.")

    def validate_action(self, surface: Surface, name: str, arguments: dict[str, Any]) -> None:
        if (
            name != "increment"
            or set(arguments) != {"amount"}
            or type(arguments["amount"]) is not int
        ):
            raise InteractionError("Invalid reference arguments.", 422)

    async def authorize(self, person: Person, surface: Surface, action: dict[str, Any]) -> None:
        self.entered.set()
        while self.hold_authorization:  # noqa: ASYNC110 -- controlled by another OS thread
            await asyncio.sleep(0.01)
        if self.denied:
            raise InteractionError("Access removed.")

    async def execute(self, person: Person, surface: Surface, action: dict[str, Any]) -> Result:
        if self.fail == "safe":
            raise NoEffect()
        # The effect happens on a separate HTTP server, not in the coordinator.
        response = await asyncio.to_thread(
            httpx.post, self.url + "/effect", json=action, trust_env=False
        )
        response.raise_for_status()
        while self.hold_result:  # noqa: ASYNC110 -- controlled by another OS thread
            await asyncio.sleep(0.01)
        if self.fail == "unknown":
            raise RuntimeError("secret-from-tool-must-not-leak")
        return Result(
            {"privateUi": "UI-only-result", "count": response.json()["count"]}, "Added one."
        )


@pytest.fixture
def reference(monkeypatch: pytest.MonkeyPatch) -> Iterator[Reference]:
    ref = Reference()
    app = FastAPI()

    @app.post("/effect")
    def effect(body: dict[str, Any]) -> dict[str, int]:
        ref.effects.append(body)
        return {"count": len(ref.effects)}

    server = ServerThread(app).start()
    ref.url = server.url
    original = Interactions.__init__

    def init(self: Interactions, store: Store, sessions: Sessions) -> None:
        original(self, store, sessions)
        self.adapters[("reference", "1")] = ref
        ref.service, ref.loop = self, asyncio.get_running_loop()

    monkeypatch.setattr(Interactions, "__init__", init)
    yield ref
    server.stop()


@pytest.fixture
def setup(reference: Reference, world: World) -> tuple[Reference, World, Browser, str, Surface]:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat, message = answered(world, ada, "The saved answer stays plain text.")
    surface = Surface(
        id="surface-1",
        owner="p-ada",
        chat_id=chat,
        message_id=message["id"],
        run_id="run-1",
        protocol="reference",
        version="1",
        view={"catalog": "reference-only", "privateUi": "UI-only-description"},
        fallback="A counter",
        model_text="A counter was offered.",
        actions=["increment"],
        binding={"target": "server-only-target"},
        expires_at=time.time() + 3600,
    )
    reference.run(reference.service.publish(surface))
    return reference, world, ada, chat, surface


def root(chat: str) -> str:
    return f"/api/v1/chats/{chat}/interactions"


def proposal(
    ada: Browser, chat: str, action: str = "a1", surface: str = "surface-1", **changes: Any
) -> httpx.Response:
    return ada.post(
        root(chat) + f"/surfaces/{surface}/actions",
        json={
            "id": action,
            "revision": 1,
            "name": "increment",
            "arguments": {"amount": 1},
            **changes,
        },
    )


def decision(ada: Browser, chat: str, action: str = "a1", approve: bool = True) -> httpx.Response:
    return ada.post(root(chat) + f"/actions/{action}/decision", json={"approve": approve})


def wait_action(ada: Browser, chat: str, state: str, action: str = "a1") -> dict[str, Any]:
    deadline = time.perf_counter() + 10
    while time.perf_counter() < deadline:
        snapshot = ada.get(root(chat)).json()
        found = next(a for a in snapshot["actions"] if a["id"] == action)
        if found["status"] == state:
            return found
        time.sleep(0.02)
    raise AssertionError(f"action never reached {state}: {snapshot}")


def test_production_advertises_no_protocol_and_has_no_publisher(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    response = ada.get("/api/v1/interactions/capabilities")
    assert response.json()["supportedProtocols"] == []
    assert response.json()["backendProtocols"][0]["renderer"] is False
    assert ada.post(root(ada.new_chat()) + "/surfaces", json={}).status_code in (404, 405)


def test_approval_duplicate_concurrency_restart_and_model_isolation(setup: Any) -> None:
    ref, world, ada, chat, _surface = setup
    first = ada.get(root(chat)).json()
    assert first["surfaces"][0]["actionable"]
    assert "server-only-target" not in json.dumps(first)
    assert proposal(ada, chat).json()["status"] == "proposed"
    assert ref.effects == [], "proposing an action must not execute it"
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: decision(ada, chat), range(12)))
    assert all(r.status_code == 200 for r in results)
    done = wait_action(ada, chat, "completed")
    assert done["result"]["count"] == 1 and len(ref.effects) == 1, (
        "duplicate decisions executed twice"
    )
    assert proposal(ada, chat).json() == done
    assert proposal(ada, chat, arguments={"amount": 2}).status_code == 409
    assert proposal(ada, chat, arguments={"amount": True}).status_code == 409
    assert "UI-only" not in ada.get(f"/api/chats/{chat}").text
    with sqlite3.connect(world.data / DATABASE) as db:
        saved = json.loads(
            db.execute("SELECT data FROM interaction_actions WHERE id='a1'").fetchone()[0]
        )
    assert saved["modelText"] == "Added one." and saved["result"]["privateUi"] == "UI-only-result"
    before = ada.get(root(chat)).json()
    world.restart_workbench()
    assert ada.get(root(chat)).json() == before
    assert decision(ada, chat).json()["status"] == "completed"
    assert len(ref.effects) == 1

    assert ada.post(f"/api/chats/{chat}/messages", json={"content": "Continue"}).status_code == 201
    ada.wait_answer(chat)
    assert "UI-only" not in json.dumps(world.gateway.requests[-1])


def test_people_and_owner_read_only(setup: Any) -> None:
    ref, world, ada, chat, _surface = setup
    bo, owner = world.browser(), world.browser()
    bo.sign_in("p-bo")
    owner.sign_in("operator")
    assert bo.get(root(chat)).status_code == 404
    assert proposal(bo, chat).status_code == 404
    assert owner.get(root(chat)).status_code == 404
    _owner_reads(world)
    view = owner.get(root(chat)).json()
    assert view["readOnly"] and not view["surfaces"][0]["actionable"]
    assert proposal(owner, chat).status_code == 404
    assert proposal(ada, chat).status_code == 200
    assert decision(owner, chat).status_code == 404
    another = world.browser()
    another.sign_in("p-ada")
    assert decision(another, chat).status_code == 409
    assert ref.effects == []


def test_revision_binding_deletion_and_unknown_version(setup: Any) -> None:
    ref, _world, ada, chat, surface = setup
    assert proposal(ada, chat).status_code == 200
    ref.run(ref.service.publish(surface.model_copy(update={"fallback": "Updated counter"}), base=1))
    assert wait_action(ada, chat, "cancelled")
    assert proposal(ada, chat, "a2").status_code == 409
    assert proposal(ada, chat, "a2", revision=2).status_code == 200
    with pytest.raises(InteractionError, match="origin"):
        ref.run(ref.service.publish(surface.model_copy(update={"run_id": "forged"}), base=2))
    with pytest.raises(InteractionError, match="not supported"):
        ref.run(ref.service.publish(surface.model_copy(update={"version": "future"}), base=2))
    ref.run(ref.service.publish(surface, base=2, state="deleted"))
    assert not ada.get(root(chat)).json()["surfaces"][0]["actionable"]
    assert decision(ada, chat, "a2").json()["status"] == "cancelled"
    with pytest.raises(InteractionError, match="no longer active"):
        ref.run(ref.service.publish(surface, base=3))
    assert ref.effects == []


def test_answer_branches_keep_their_own_surfaces_and_cancel_pending(setup: Any) -> None:
    ref, _world, ada, chat, surface = setup
    assert proposal(ada, chat).status_code == 200
    retried = ada.post(f"/api/chats/{chat}/retry", json={})
    assert retried.status_code == 201, retried.text
    other = ada.wait_answer(chat)
    ref.run(
        ref.service.publish(
            surface.model_copy(
                update={"id": "surface-2", "message_id": other["id"], "run_id": "run-2"}
            )
        )
    )
    view = ada.get(root(chat)).json()
    assert [s["id"] for s in view["surfaces"]] == ["surface-2"]
    assert view["actions"] == []
    old = ada.get(root(chat), params={"via": surface.message_id}).json()
    assert old["surfaces"][0]["id"] == surface.id and old["readOnly"]
    assert old["actions"][0]["status"] == "cancelled"
    assert proposal(ada, chat, "a2").status_code == 409
    assert ada.post(f"/api/chats/{chat}/messages/{surface.message_id}/choose").status_code == 200
    assert ada.get(root(chat)).json()["surfaces"][0]["id"] == surface.id
    assert ref.effects == []


@pytest.mark.parametrize(
    "change", ["signout", "source", "branch", "revision", "expiry", "denied", "lease"]
)
def test_authorization_is_checked_after_wait_before_effect(setup: Any, change: str) -> None:
    ref, world, ada, chat, surface = setup
    owner = world.browser()
    owner.sign_in("operator")
    if change == "source":
        server = owner.post("/api/tools/servers", json={"name": "Reference", "url": ref.url}).json()
        surface = surface.model_copy(update={"id": "source-surface", "connection_id": server["id"]})
        ref.run(ref.service.publish(surface))
    ref.hold_authorization = True
    assert proposal(ada, chat, surface=surface.id).status_code == 200
    assert decision(ada, chat).status_code == 200
    assert ref.entered.wait(5)
    if change == "signout":
        assert ada.post("/api/signout").status_code == 204
    elif change == "source":
        assert owner.delete(f"/api/tools/servers/{server['id']}").status_code == 204
    elif change == "branch":
        assert ada.post(f"/api/chats/{chat}/retry", json={}).status_code == 201
        ada.wait_answer(chat)
    elif change in ("revision", "expiry"):
        update = {"expires_at": time.time() - 1} if change == "expiry" else {"fallback": "Changed"}
        ref.run(ref.service.publish(surface.model_copy(update=update), base=1))
    elif change == "lease":
        # The lease ended and Eugene's next renewal is over as soon as it is
        # given. Any renewal before the claim (a request by the person, or
        # background work that carries their token) must not bring them back.
        world.eugene.expires_in = 0.001
        with sqlite3.connect(world.data / DATABASE) as db:
            db.execute(
                "UPDATE sessions SET access_expires_at=? WHERE sub='p-ada'", (time.time() - 1,)
            )
        # Force the renewal. CI once renewed the lease in this window and the
        # action dispatched (candidate-workbench, specs run 40097628340).
        assert ada.get("/api/chats").status_code == 200
    else:
        ref.denied = True
    ref.hold_authorization = False
    deadline = time.perf_counter() + 5
    while ref.service.tasks and time.perf_counter() < deadline:
        time.sleep(0.02)
    assert ref.effects == [], f"{change} did not prevent external dispatch"
    with sqlite3.connect(world.data / DATABASE) as db:
        assert (
            db.execute("SELECT status FROM interaction_actions WHERE id='a1'").fetchone()[0]
            == "cancelled"
        )


@pytest.mark.parametrize("ending", ["stop", "restart", "unknown", "safe"])
def test_uncertain_calls_never_replay(setup: Any, ending: str) -> None:
    ref, world, ada, chat, _surface = setup
    ref.hold_result = ending in ("stop", "restart")
    ref.fail = ending
    assert proposal(ada, chat).status_code == 200
    assert decision(ada, chat).status_code == 200
    if ending in ("stop", "restart"):
        deadline = time.perf_counter() + 5
        while not ref.effects and time.perf_counter() < deadline:
            time.sleep(0.02)
        assert len(ref.effects) == 1
        if ending == "stop":
            assert ada.post(f"/api/chats/{chat}/stop").status_code == 204
        else:
            world.restart_workbench()
    expected = "failed" if ending == "safe" else "uncertain"
    done = wait_action(ada, chat, expected)
    assert "secret-from-tool" not in json.dumps(done)
    assert proposal(ada, chat).json()["status"] == expected
    assert decision(ada, chat).json()["status"] == expected
    assert len(ref.effects) == (0 if ending == "safe" else 1)


def test_restart_cancels_unapproved_and_abandoned_dispatch_records(setup: Any) -> None:
    ref, world, ada, chat, _surface = setup
    assert proposal(ada, chat, "pending").status_code == 200
    assert proposal(ada, chat, "abandoned").status_code == 200
    # Simulate disk state left by a hard kill after intent committed. There is
    # deliberately no in-memory task to clean it up in lifespan shutdown.
    with sqlite3.connect(world.data / DATABASE) as db:
        db.execute("UPDATE interaction_actions SET status='dispatching' WHERE id='abandoned'")
    world.restart_workbench()
    assert wait_action(ada, chat, "cancelled", "pending")
    assert wait_action(ada, chat, "uncertain", "abandoned")
    assert ref.effects == []


def test_sse_reconnect_is_snapshot_only_and_signout_closes_stream(setup: Any) -> None:
    ref, _world, ada, chat, _surface = setup
    assert proposal(ada, chat).status_code == 200
    assert decision(ada, chat).status_code == 200
    wait_action(ada, chat, "completed")
    for last in ("0", "99999999"):
        with ada.http.stream(
            "GET", root(chat) + "/events", headers=ada.headers({"Last-Event-ID": last})
        ) as response:
            assert response.status_code == 200
            lines = response.iter_lines()
            event_id = next(lines)
            assert next(lines) == "event: snapshot"
            view = json.loads(next(lines).removeprefix("data: "))
            assert event_id == f"id: {view['cursor']}"
            assert view["actions"][0]["status"] == "completed"
    assert len(ref.effects) == 1
    with ada.http.stream("GET", root(chat) + "/events", headers=ada.headers()) as response:
        lines = response.iter_lines()
        assert next(lines).startswith("id:")
        assert ada.post("/api/signout").status_code == 204
        rest = list(lines)
        assert sum(line.startswith("event:") for line in rest) <= 1


def test_invalid_and_oversized_payloads_are_refused_before_storage(setup: Any) -> None:
    ref, _world, ada, chat, surface = setup
    assert proposal(ada, chat, extra="forged").status_code == 422
    assert proposal(ada, chat, revision=True).status_code == 422
    assert proposal(ada, chat, arguments={"amount": "x" * 25000}).status_code == 413
    nested: Any = 0
    for _ in range(40):
        nested = [nested]
    assert proposal(ada, chat, arguments={"amount": nested}).status_code == 422
    assert ada.get(root(chat)).json()["actions"] == []
    with pytest.raises(InteractionError) as exc:
        ref.run(
            ref.service.publish(
                surface.model_copy(
                    update={"view": {"catalog": "reference-only", "big": "x" * 300000}}
                ),
                base=1,
            )
        )
    assert exc.value.status == 413
    assert ref.effects == []


def test_chat_delete_removes_surfaces_actions_and_cursor(setup: Any) -> None:
    ref, world, ada, chat, _surface = setup
    assert proposal(ada, chat).status_code == 200
    assert ada.delete(f"/api/chats/{chat}").status_code == 204
    with sqlite3.connect(world.data / DATABASE) as db:
        for table in ("interaction_surfaces", "interaction_actions", "interaction_cursors"):
            assert (
                db.execute(f"SELECT count(*) FROM {table} WHERE chat_id=?", (chat,)).fetchone()[0]
                == 0
            )
    assert ref.effects == []


def test_owner_cannot_read_surfaces_or_results_of_hidden_job_site_answers(setup: Any) -> None:
    ref, world, ada, chat, surface = setup
    _owner_reads(world)
    owner = world.browser()
    owner.sign_in("operator")
    assert proposal(ada, chat).status_code == 200
    ref.run(
        ref.service.store.update_message(
            surface.message_id,
            tool_rounds=[
                {"calls": [{"jobSite": "Private site", "mode": "production", "serverName": "Site"}]}
            ],
        )
    )
    view = owner.get(root(chat)).json()
    assert view["surfaces"] == view["actions"] == []
    assert ada.get(root(chat)).json()["surfaces"]


def test_action_quota_keeps_old_ids_and_refuses_new_work(setup: Any) -> None:
    ref, _world, ada, chat, _surface = setup
    for number in range(200):
        response = proposal(ada, chat, f"a{number}")
        if response.status_code == 413:
            break
        assert response.status_code == 200
    else:
        pytest.fail("Outstanding actions did not reserve bounded result space")
    assert proposal(ada, chat, "a0").status_code == 200
    assert ref.effects == []


def test_a_terminal_decision_never_dispatches_again(setup: Any) -> None:
    ref, _world, ada, chat, _surface = setup
    assert proposal(ada, chat).status_code == 200
    assert decision(ada, chat).status_code == 200
    wait_action(ada, chat, "completed")
    assert decision(ada, chat).status_code == 200
    wait_action(ada, chat, "completed")
    assert len(ref.effects) == 1, "A terminal action was dispatched again"


def test_oidc_revocation_is_refreshed_before_dispatch(setup: Any) -> None:
    ref, world, ada, chat, _surface = setup
    assert proposal(ada, chat).status_code == 200
    world.eugene.disabled.add("p-ada")
    with sqlite3.connect(world.data / DATABASE) as db:
        db.execute("UPDATE sessions SET access_expires_at=? WHERE sub='p-ada'", (time.time() - 1,))
    assert decision(ada, chat).status_code == 401
    with sqlite3.connect(world.data / DATABASE) as db:
        assert (
            db.execute("SELECT status FROM interaction_actions WHERE id='a1'").fetchone()[0]
            == "cancelled"
        )
    assert ref.effects == []


def _process_server(settings: dict[str, Any], port: int, effect_url: str) -> None:
    for key in list(os.environ):
        if key.upper().startswith("EUGENE_PLEXUS_"):
            del os.environ[key]
    app = create_app(Settings.model_validate(settings))
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        async with original(app):
            adapter = Reference()
            adapter.url = effect_url
            adapter.hold_result = True
            app.state.interactions.adapters[("reference", "1")] = adapter
            yield

    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


def test_real_process_kill_after_external_effect_does_not_replay(setup: Any) -> None:
    ref, world, ada, chat, _surface = setup
    world.server.stop()
    context = multiprocessing.get_context("spawn")
    process: Any = None

    def start() -> Any:
        child = context.Process(
            target=_process_server,
            args=(
                world.settings.model_dump(mode="json"),
                world.server.port,
                ref.url,
            ),
        )
        child.start()
        deadline = time.perf_counter() + 20
        while time.perf_counter() < deadline:
            if not child.is_alive():
                raise AssertionError(f"Workbench child exited: {child.exitcode}")
            try:
                if ada.get("/healthz").status_code == 200:
                    return child
            except httpx.TransportError:
                pass
            time.sleep(0.05)
        child.kill()
        child.join(10)
        raise AssertionError("Workbench child did not start")

    try:
        process = start()
        assert proposal(ada, chat).status_code == 200
        assert decision(ada, chat).status_code == 200
        deadline = time.perf_counter() + 10
        while len(ref.effects) != 1 and time.perf_counter() < deadline:
            time.sleep(0.02)
        assert len(ref.effects) == 1
        process.kill()
        process.join(10)
        assert not process.is_alive()
        with sqlite3.connect(world.data / DATABASE) as db:
            assert (
                db.execute("SELECT status FROM interaction_actions WHERE id='a1'").fetchone()[0]
                == "dispatching"
            )
        process = start()
        assert wait_action(ada, chat, "uncertain")
        assert proposal(ada, chat).json()["status"] == "uncertain"
        assert decision(ada, chat).json()["status"] == "uncertain"
        assert len(ref.effects) == 1, "Restart replayed a crashed action"
    finally:
        if process is not None and process.is_alive():
            process.kill()
            process.join(10)


def test_signout_interrupts_inflight_wait_without_repeating_effect(setup: Any) -> None:
    ref, world, ada, chat, _surface = setup
    ref.hold_result = True
    assert proposal(ada, chat).status_code == 200
    assert decision(ada, chat).status_code == 200
    deadline = time.perf_counter() + 5
    while not ref.effects and time.perf_counter() < deadline:
        time.sleep(0.02)
    assert len(ref.effects) == 1
    assert ada.post("/api/signout").status_code == 204
    while ref.service.tasks and time.perf_counter() < deadline:
        time.sleep(0.02)
    assert not ref.service.tasks
    another = world.browser()
    another.sign_in("p-ada")
    assert wait_action(another, chat, "uncertain")
    assert len(ref.effects) == 1


def test_stream_disconnect_does_not_cancel_an_approved_action(setup: Any) -> None:
    ref, _world, ada, chat, _surface = setup
    ref.hold_result = True
    assert proposal(ada, chat).status_code == 200
    assert decision(ada, chat).status_code == 200
    with ada.http.stream("GET", root(chat) + "/events", headers=ada.headers()) as response:
        assert next(response.iter_lines()).startswith("id:")
    ref.hold_result = False
    assert wait_action(ada, chat, "completed")
    assert len(ref.effects) == 1
