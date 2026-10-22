"""Recoverable branch removal and the scheduled, restartable row 7 purge."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from . import branch_index, branch_store, files
from .interaction_store import InteractionError

log = logging.getLogger(__name__)


class BranchWrites:
    """Serialize chat mutations until response headers; streamed Stop stays reachable."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        state = scope["app"].state if "app" in scope else None
        branches = getattr(state, "branches", None)
        parts = scope.get("path", "").split("/")
        if (
            scope["type"] != "http"
            or scope.get("method") in ("GET", "HEAD", "OPTIONS")
            or parts[1:3] != ["api", "chats"]
            or len(parts) < 4
            or branches is None
        ):
            await self.app(scope, receive, send)
            return
        lock = branches.lock(parts[3])
        await lock.acquire()
        held = True

        async def respond(message: Any) -> None:
            nonlocal held
            if message["type"] == "http.response.start" and held:
                held = False
                lock.release()
            await send(message)

        try:
            await self.app(scope, receive, respond)
        finally:
            if held:
                lock.release()


class Branches:
    def __init__(self, state: Any, *, clock: Callable[[], float] = time.time) -> None:
        self.state = state
        self.store = state.store
        self.clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._owners: dict[str, asyncio.Lock] = {}
        self._task: asyncio.Task[None] | None = None

    def lock(self, chat: str) -> asyncio.Lock:
        return self._locks.setdefault(chat, asyncio.Lock())

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="deleted-branches")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            try:
                await self.due()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("checking deleted branches failed")
            await asyncio.sleep(60)

    async def delete(self, owner: str, chat: str, mid: str, expected: str | None = None) -> None:
        async with (
            self._owners.setdefault(owner, asyncio.Lock()),
            self.state.notes_index.lock(owner),
        ):
            await self.state.memory_courier.drain(owner)
            prepared = False
            archived: dict[str, Any] = {}
            payload: dict[str, Any] = {}
            index = self.state.saved.index(owner, create=False)
            try:
                await self.state.learner.drain_chat(chat)
                payload = await self.store.branch_payload(chat, mid)
                if expected is None or branch_store.fingerprint(payload) != expected:
                    raise InteractionError("This version changed. Review what goes and try again.")
                records = payload["tables"]["files"]
                await self.state.reader.cancel([r["id"] for r in records])
                if index is not None:
                    file_ids = {r["id"] for r in records}
                    note_ids = {r["id"] for r in payload["tables"]["memory_notes"]}
                    archived = await branch_index.snapshot(index, file_ids, note_ids)
                    await self.state.saved._cancel(owner, [r["id"] for r in archived["documents"]])
                    archived = await branch_index.snapshot(index, file_ids, note_ids)
                await self.store.prepare_branch(chat, mid, archived, now=self.clock())
                prepared = True
                # Serializes with every index sync, so a stale sync cannot put it back.
                async with self.state.exchanges_index.lock(chat):
                    try:
                        await self.state.exchanges_index.remove_exchanges(
                            owner, chat, payload["ids"]
                        )
                        if index is not None:
                            await branch_index.remove(index, archived)
                    except Exception as exc:
                        raise InteractionError(
                            "Workbench could not remove these messages from your search index, "
                            f"so nothing was deleted ({exc}). Try again in a moment.",
                            503,
                        ) from exc
                    try:
                        await self.store.delete_branch(chat, mid, now=self.clock(), index=archived)
                    except BaseException:
                        raise
                prepared = False
                self.state.exchanges_index.touch(owner, chat)
                self.state.answers.publish(chat, {"type": "path"})
            except BaseException:
                if prepared:
                    if index is not None:
                        await branch_index.restore(index, archived)
                    await self.store.cancel_branch_preparation(mid)
                self.state.exchanges_index.touch(owner, chat)
                if payload:
                    await self._resume_readers(payload)
                raise
            finally:
                if owner not in await self.store.hidden_subs():
                    self.state.memory_courier.allow_owner(owner)

    async def _resume_readers(self, payload: dict[str, Any]) -> None:
        for record in payload["tables"].get("files", []):
            restored = await self.store.file(record["id"])
            if restored is not None and (restored.reading or {}).get("state") == "reading":
                await self.state.reader.start(restored)
        if payload.get("index"):
            self.state.saved.ingest.wake(payload["owner"])

    async def undo(self, owner: str, chat: str, mid: str) -> bool:
        async with (
            self._owners.setdefault(owner, asyncio.Lock()),
            self.state.notes_index.lock(owner),
        ):
            row = next((r for r in await self.store.deleted_branches(chat) if r["id"] == mid), None)
            if (
                row is None
                or row["state"] not in ("hidden", "restoring")
                or (row["state"] == "hidden" and row["delete_at"] <= self.clock())
            ):
                return False
            payload = await self.store.branch_undo_payload(mid)
            root = next(r for r in payload["tables"]["messages"] if r["id"] == mid)
            if (
                root["parent_id"] is not None
                and await self.store.message(root["parent_id"]) is None
            ):
                raise InteractionError("Restore the deleted version before this one first.")
            await self.store.put_branch(mid, state="restoring", problem=None)
            try:
                if payload.get("index"):
                    await branch_index.restore(self.state.saved.index(owner), payload["index"])
            except Exception as exc:
                problem = f"Workbench could not restore this version: {exc}. It will try again."
                await self.store.put_branch(mid, problem=problem)
                raise InteractionError(problem, 503) from exc
            if not await self.store.undo_branch(chat, mid, now=self.clock()):
                return False
            await self._resume_readers(payload)
            self.state.exchanges_index.touch(owner, chat)
            self.state.memory_courier.poke()
            self.state.answers.publish(chat, {"type": "path"})
            return True

    async def due(self) -> None:
        await self.store.finish_branch_rewrite()
        for row in sorted(await self.store.deleted_branches(), key=lambda r: r["delete_at"]):
            if row["state"] == "hidden" and row["delete_at"] > self.clock():
                continue
            async with self._locks.setdefault(row["chat_id"], asyncio.Lock()):
                # Undo may have won the lock after this round's read.
                current = next(
                    (r for r in await self.store.deleted_branches() if r["id"] == row["id"]), None
                )
                if current is None:
                    continue
                if current["state"] == "restoring":
                    if not any(
                        m.status == "running" for m in await self.store.tree(row["chat_id"])
                    ):
                        await self.undo(row["owner"], row["chat_id"], row["id"])
                    continue
                if current["state"] == "preparing":
                    payload = json.loads(current["payload"])
                    async with self.state.notes_index.lock(row["owner"]):
                        if payload.get("index"):
                            await branch_index.restore(
                                self.state.saved.index(row["owner"]), payload["index"]
                            )
                        await self.store.cancel_branch_preparation(row["id"])
                        await self._resume_readers(payload)
                    self.state.exchanges_index.touch(row["owner"], row["chat_id"])
                    continue
                await self._purge(current)

    async def purge_chat(self, chat: str) -> None:
        """The chat write lock is already held by the route (or the project delete)."""
        await self.store.expire_chat_branches(chat, now=self.clock())
        for row in await self.store.deleted_branches(chat):
            if not await self._purge(row, deleting_chat=True):
                current = next(
                    r for r in await self.store.deleted_branches(chat) if r["id"] == row["id"]
                )
                raise InteractionError(current["problem"], 503)

    async def _purge(self, row: dict[str, Any], *, deleting_chat: bool = False) -> bool:
        async with (
            self._owners.setdefault(row["owner"], asyncio.Lock()),
            self.state.notes_index.lock(row["owner"]),
        ):
            steps = json.loads(row["steps"])
            paused = steps.get("legacy")
            if paused and not deleting_chat and self.clock() < paused["retry_at"]:
                current = await self.store.branch_legacy_recalls(row["id"])
                if [(r["id"], r["signature"]) for r in current] == [
                    (r["id"], r["signature"]) for r in paused["recalls"]
                ]:
                    return False
            await self.store.put_branch(row["id"], state="purging", problem=None)
            payload = await self.store.branch_purge_payload(row["id"])
            if not payload:
                return True
            try:
                if recalls := await self.store.branch_legacy_recalls(
                    row["id"], deleting_chat=deleting_chat
                ):
                    raise branch_store.LegacyRecall(recalls)
                await self.state.memory_courier.drain(row["owner"])
                owed = [r for r in payload["tables"]["memory_server_sent"] if r["state"] != "send"]
                if not deleting_chat and not steps.get("forgotten"):
                    problem = await self.state.removal.forget_exchanges(row["owner"], owed)
                    if problem != "done":
                        raise RuntimeError(problem)
                    steps["forgotten"] = True
                    await self.store.put_branch(row["id"], steps=json.dumps(steps))
                await self.state.reader.cancel([r["id"] for r in payload["tables"]["files"]])
                if not steps.get("exports"):
                    await self.state.exports.drop(row["owner"], before=row["deleted_at"])
                    steps["exports"] = True
                    await self.store.put_branch(row["id"], steps=json.dumps(steps))
                async with self.state.exchanges_index.lock(row["chat_id"]):
                    await self.state.exchanges_index.remove_exchanges(
                        row["owner"], row["chat_id"], payload["ids"]
                    )
                    index = self.state.saved.index(row["owner"], create=False)
                    if index is not None:
                        await index.delete_notes(
                            [r["id"] for r in payload["tables"]["memory_notes"]]
                        )
                        await branch_index.remove(index, payload.get("index", {}))
                    for record in payload["tables"]["files"]:
                        await self.state.saved.drop_attachment(
                            row["owner"], row["chat_id"], record["id"]
                        )
                        await asyncio.to_thread(
                            files.remove_stored,
                            self.state.settings.data_dir,
                            row["owner"],
                            record["id"],
                        )
                    await self.store.purge_branch(
                        row["id"], now=self.clock(), queue_forget=deleting_chat
                    )
                return True
            except asyncio.CancelledError:
                raise
            except branch_store.LegacyRecall as exc:
                steps["legacy"] = {
                    "recalls": exc.recalls,
                    "retry_at": self.clock() + branch_store.DAY,
                }
                await self.store.put_branch(row["id"], problem=str(exc), steps=json.dumps(steps))
                return False
            except Exception as exc:
                await self.store.put_branch(
                    row["id"],
                    problem=(
                        f"Workbench could not finish deleting this version: {exc}. "
                        "It will try again."
                    ),
                )
                log.warning("deleting a branch failed: %s", exc)
                return False
            finally:
                with contextlib.suppress(Exception):
                    if row["owner"] not in await self.store.hidden_subs():
                        self.state.memory_courier.allow_owner(row["owner"])
