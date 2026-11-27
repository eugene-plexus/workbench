"""Issue 25: an abandoned session leaves Eugene's tokens in the store for ever.

`Store.sweep_sessions` had no caller, so a session was removed only when someone presented
it. The hourly loop that already asks Eugene who was removed (`Removal._run`) now sweeps
the expired sessions too, soon after the start and then each hour.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from eugene_plexus_workbench.removal import Removal
from eugene_plexus_workbench.store import SessionRow, Store

DEADLINE = 10.0


def _session(name: str, expires_at: float) -> SessionRow:
    return SessionRow(
        id_hash=f"id-{name}",
        secret_hash=f"secret-{name}",
        sub="p-ann",
        refresh_token=f"refresh-token-{name}",
        access_expires_at=expires_at,
        created_at=expires_at - 100.0,
        expires_at=expires_at,
        access_token=f"access-token-{name}",
    )


async def _until(check: object, what: str) -> None:
    """Wait for the thing itself to happen (the row to go), never a bare sleep."""
    end = time.perf_counter() + DEADLINE
    while not await check():  # type: ignore[operator]
        if time.perf_counter() > end:
            raise AssertionError(f"{what} did not happen within {DEADLINE} s")
        await asyncio.sleep(0.02)


def test_the_loop_removes_an_expired_session_and_its_tokens_and_keeps_a_live_one(
    tmp_path: Path,
) -> None:
    async def go() -> tuple[str | None, str | None]:
        store = Store(tmp_path / "workbench.sqlite3")
        await store.open()
        removal = Removal(store, None, tmp_path)  # type: ignore[arg-type]
        try:
            now = time.time()
            await store.put_session(_session("old", now - 60.0))
            await store.put_session(_session("live", now + 3600.0))
            removal.start()

            async def gone() -> bool:
                return await store.session("id-old") is None

            await _until(gone, "the expired session's removal")
            kept = await store.session("id-live")
            return kept.access_token if kept else None, kept.refresh_token if kept else None
        finally:
            await removal.aclose()
            await store.close()

    access, refresh = asyncio.run(go())
    assert (access, refresh) == ("access-token-live", "refresh-token-live")
    with sqlite3.connect(tmp_path / "workbench.sqlite3") as db:
        rows = db.execute("SELECT id_hash, refresh_token, access_token FROM sessions").fetchall()
    assert rows == [("id-live", "refresh-token-live", "access-token-live")]


def test_a_wake_that_arrives_during_a_tick_is_not_lost(tmp_path: Path) -> None:
    """A wake during the sweep must request another tick, not wait an hour."""

    async def go() -> int:
        store = Store(tmp_path / "workbench.sqlite3")
        await store.open()
        removal = Removal(store, None, tmp_path)  # type: ignore[arg-type]
        calls = 0

        async def sweep(now: float) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                removal._wake.set()

        store.sweep_sessions = sweep  # type: ignore[method-assign]
        try:
            removal.start()

            async def ticked_again() -> bool:
                return calls >= 2

            await _until(ticked_again, "the tick the wake asked for")
            return calls
        finally:
            await removal.aclose()
            await store.close()

    assert asyncio.run(go()) >= 2


def test_one_failed_sweep_is_logged_and_the_next_tick_tries_again(
    tmp_path: Path, caplog: object
) -> None:
    async def go() -> None:
        store = Store(tmp_path / "workbench.sqlite3")
        await store.open()
        removal = Removal(store, None, tmp_path)  # type: ignore[arg-type]
        real = store.sweep_sessions
        calls = 0

        async def flaky(now: float) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("database is locked")
            await real(now)

        store.sweep_sessions = flaky  # type: ignore[method-assign]
        try:
            await store.put_session(_session("old", time.time() - 60.0))
            removal.start()

            async def failed_once() -> bool:
                return calls >= 1

            await _until(failed_once, "the first sweep")
            assert await store.session("id-old") is not None
            removal._wake.set()  # the next tick, without waiting an hour

            async def gone() -> bool:
                return await store.session("id-old") is None

            await _until(gone, "the removal on the next tick")
        finally:
            await removal.aclose()
            await store.close()

    asyncio.run(go())
    assert "database is locked" in caplog.text  # type: ignore[attr-defined]
