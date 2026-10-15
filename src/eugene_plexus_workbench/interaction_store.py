"""Store's transactional interaction records (specs/workbench-interactions-v1.md).

Only this storage helper and Store touch these tables. All operations use Store's
single executor and connection, including checks against chats and sign-ins.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import Message, Person, Store

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS interaction_cursors ("
    "chat_id TEXT PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE, value INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS interaction_surfaces ("
    "id TEXT PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE, "
    "message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE, owner TEXT NOT NULL, "
    "revision INTEGER NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS interaction_surfaces_by_chat ON interaction_surfaces(chat_id)",
    "CREATE TABLE IF NOT EXISTS interaction_actions ("
    "chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE, id TEXT NOT NULL, "
    "surface_id TEXT NOT NULL REFERENCES interaction_surfaces(id) ON DELETE CASCADE, "
    "session_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL, "
    "PRIMARY KEY(chat_id, id))",
    "CREATE INDEX IF NOT EXISTS interaction_actions_by_status ON interaction_actions(status)",
]

LIVE = ("proposed", "queued", "dispatching")
PERSON_BYTES = 25 * 1024 * 1024


class InteractionError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        self.message, self.status = message, status
        super().__init__(message)


def encoded(value: Any, limit: int) -> str:
    def depth(node: Any, level: int) -> None:
        if level > 32:
            raise InteractionError("The interaction data is nested too deeply.", 422)
        if isinstance(node, dict):
            for child in node.values():
                depth(child, level + 1)
        elif isinstance(node, list):
            for child in node:
                depth(child, level + 1)

    depth(value, 0)
    try:
        text = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        size = len(text.encode("utf-8"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise InteractionError("The interaction data is not valid JSON.", 422) from exc
    if size > limit:
        raise InteractionError("The interaction data exceeds its size limit.", 413)
    return text


def touch(db: sqlite3.Connection, chat: str) -> None:
    db.execute(
        "INSERT INTO interaction_cursors(chat_id, value) VALUES (?, 1) "
        "ON CONFLICT(chat_id) DO UPDATE SET value=value+1",
        (chat,),
    )


def selected(db: sqlite3.Connection, chat: str, via: str | None = None) -> set[str]:
    from .store import _message, path

    rows = db.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY seq", (chat,)).fetchall()
    return {m.id for m in path([_message(r) for r in rows], via)}


def surface_row(row: sqlite3.Row) -> dict[str, Any]:
    return {**json.loads(row["data"]), "revision": row["revision"], "state": row["state"]}


def action_row(row: sqlite3.Row) -> dict[str, Any]:
    return {**json.loads(row["data"]), "status": row["status"]}


def public_action(action: dict[str, Any]) -> dict[str, Any]:
    return {
        k: action[k]
        for k in (
            "id",
            "surfaceId",
            "revision",
            "name",
            "arguments",
            "status",
            "expiresAt",
            "result",
            "error",
        )
    }


def _own(db: sqlite3.Connection, chat: str, person: Person) -> None:
    row = db.execute("SELECT owner FROM chats WHERE id=?", (chat,)).fetchone()
    if row is None or row["owner"] != person.sub:
        raise InteractionError("There is no such chat.", 404)


def _surface(db: sqlite3.Connection, chat: str, surface: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM interaction_surfaces WHERE id=? AND chat_id=?", (surface, chat)
    ).fetchone()
    if row is None:
        raise InteractionError("There is no such surface.", 404)
    return surface_row(row)


def _action(db: sqlite3.Connection, chat: str, action: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT * FROM interaction_actions WHERE chat_id=? AND id=?", (chat, action)
    ).fetchone()
    return action_row(row) if row is not None else None


def _available(db: sqlite3.Connection, surface: dict[str, Any], now: float) -> bool:
    return bool(
        surface["state"] == "active"
        and surface["expires_at"] > now
        and (
            surface["connection_id"] is None
            or db.execute(
                "SELECT 1 FROM tool_servers WHERE id=?", (surface["connection_id"],)
            ).fetchone()
        )
    )


def _valid(db: sqlite3.Connection, surface: dict[str, Any], action: dict[str, Any]) -> bool:
    now = time.time()
    session = db.execute(
        "SELECT * FROM sessions WHERE id_hash=?", (action["sessionId"],)
    ).fetchone()
    return bool(
        _available(db, surface, now)
        and action["expiresAt"] > now
        and action["revision"] == surface["revision"]
        and session
        and session["sub"] == surface["owner"]
        and session["expires_at"] > now
        and session["access_expires_at"] > now
        and surface["message_id"] in selected(db, surface["chat_id"])
    )


def _save(db: sqlite3.Connection, chat: str, action: dict[str, Any]) -> None:
    db.execute(
        "UPDATE interaction_actions SET status=?, data=? WHERE chat_id=? AND id=?",
        (action["status"], encoded(action, 160 * 1024), chat, action["id"]),
    )
    touch(db, chat)


def _quota(db: sqlite3.Connection, owner: str, extra: int) -> None:
    surfaces = db.execute(
        "SELECT COALESCE(SUM(length(CAST(data AS BLOB))),0) "
        "FROM interaction_surfaces WHERE owner=?",
        (owner,),
    ).fetchone()[0]
    actions = db.execute(
        "SELECT COALESCE(SUM(length(CAST(a.data AS BLOB)) + CASE WHEN a.status IN "
        "('proposed','queued','dispatching') THEN 147456 ELSE 0 END),0) FROM interaction_actions a "
        "JOIN chats c ON c.id=a.chat_id WHERE c.owner=?",
        (owner,),
    ).fetchone()[0]
    if surfaces + actions + extra > PERSON_BYTES:
        raise InteractionError(
            "Saved interactions are full. Delete an old chat to make space.", 413
        )


class InteractionStore:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def _run[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        def transaction() -> T:
            db = self.store._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                result = fn(db)
                db.execute("COMMIT")
                return result
            except BaseException:
                db.execute("ROLLBACK")
                raise

        return await self.store._run(transaction)

    async def publish(
        self, data: dict[str, Any], base: int, state: str = "active"
    ) -> dict[str, Any]:
        packed = encoded(data, 256 * 1024)

        def write(db: sqlite3.Connection) -> dict[str, Any]:
            chat = data["chat_id"]
            message = db.execute(
                "SELECT m.role, c.owner FROM messages m JOIN chats c ON c.id=m.chat_id "
                "WHERE m.id=? AND m.chat_id=?",
                (data["message_id"], chat),
            ).fetchone()
            if (
                message is None
                or message["role"] != "assistant"
                or message["owner"] != data["owner"]
            ):
                raise InteractionError("There is no such answer.", 404)
            old = db.execute(
                "SELECT * FROM interaction_surfaces WHERE id=?", (data["id"],)
            ).fetchone()
            if old is not None:
                saved = surface_row(old)
                if any(
                    saved[k] != data[k]
                    for k in (
                        "owner",
                        "chat_id",
                        "message_id",
                        "run_id",
                        "protocol",
                        "version",
                        "connection_id",
                    )
                ):
                    raise InteractionError("A surface cannot change its origin.")
                if old["revision"] != base or old["state"] != "active":
                    raise InteractionError("This surface revision is no longer active.")
            elif base != 0:
                raise InteractionError("There is no such surface revision.")
            elif (
                db.execute(
                    "SELECT count(*) FROM interaction_surfaces WHERE chat_id=?", (chat,)
                ).fetchone()[0]
                >= 64
            ):
                raise InteractionError("This chat has reached its surface limit.", 413)
            _quota(
                db, data["owner"], len(packed.encode()) - (len(old["data"].encode()) if old else 0)
            )
            db.execute(
                "INSERT INTO interaction_surfaces VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE "
                "SET revision=excluded.revision,state=excluded.state,data=excluded.data",
                (data["id"], chat, data["message_id"], data["owner"], base + 1, state, packed),
            )
            touch(db, chat)
            self.sweep_in_transaction(db, chat)
            return _surface(db, chat, data["id"])

        return await self._run(write)

    @staticmethod
    def sweep_in_transaction(
        db: sqlite3.Connection, chat: str | None = None, restart: bool = False
    ) -> None:
        where, args = (" WHERE chat_id=?", (chat,)) if chat else ("", ())
        for row in db.execute("SELECT * FROM interaction_surfaces" + where, args).fetchall():
            surface = surface_row(row)
            if surface["state"] == "active" and not _available(db, surface, time.time()):
                db.execute(
                    "UPDATE interaction_surfaces SET state='disabled', "
                    "revision=revision+1 WHERE id=?",
                    (row["id"],),
                )
                touch(db, row["chat_id"])
        for row in db.execute(
            "SELECT * FROM interaction_actions WHERE status IN ('proposed','queued','dispatching')"
            + (" AND chat_id=?" if chat else ""),
            args,
        ).fetchall():
            action = action_row(row)
            if restart or not _valid(db, _surface(db, row["chat_id"], row["surface_id"]), action):
                action["status"] = "uncertain" if action["status"] == "dispatching" else "cancelled"
                action["error"] = (
                    "Execution was interrupted."
                    if restart
                    else "The action is no longer authorized or current."
                )
                _save(db, row["chat_id"], action)

    async def sweep(self, *, restart: bool = False) -> None:
        await self._run(lambda db: self.sweep_in_transaction(db, restart=restart))

    async def snapshot(
        self,
        chat: str,
        via: str | None,
        read_only: bool,
        hidden: Callable[[list[Message]], set[str]],
        supported: set[tuple[str, str]],
    ) -> dict[str, Any]:
        def read(db: sqlite3.Connection) -> dict[str, Any]:
            from .store import _message

            self.sweep_in_transaction(db, chat)
            tree = [
                _message(row)
                for row in db.execute(
                    "SELECT * FROM messages WHERE chat_id=? ORDER BY seq", (chat,)
                )
            ]
            visible = selected(db, chat, via) - hidden(tree)
            current = selected(db, chat)
            surfaces = []
            for row in db.execute(
                "SELECT * FROM interaction_surfaces WHERE chat_id=? ORDER BY id", (chat,)
            ):
                s = surface_row(row)
                if s["message_id"] not in visible:
                    continue
                surfaces.append(
                    {
                        "id": s["id"],
                        "messageId": s["message_id"],
                        "runId": s["run_id"],
                        "protocol": s["protocol"],
                        "version": s["version"],
                        "revision": s["revision"],
                        "state": s["state"],
                        "view": s["view"],
                        "fallback": s["fallback"],
                        "actions": s["actions"],
                        "expiresAt": s["expires_at"],
                        "actionable": not read_only
                        and via is None
                        and s["message_id"] in current
                        and _available(db, s, time.time())
                        and (s["protocol"], s["version"]) in supported,
                    }
                )
            ids = {s["id"] for s in surfaces}
            actions = [
                public_action(action_row(row))
                for row in db.execute(
                    "SELECT * FROM interaction_actions WHERE chat_id=? ORDER BY id", (chat,)
                )
                if row["surface_id"] in ids
            ]
            row = db.execute(
                "SELECT value FROM interaction_cursors WHERE chat_id=?", (chat,)
            ).fetchone()
            return {
                "version": 1,
                "cursor": row[0] if row else 0,
                "chatId": chat,
                "readOnly": read_only or via is not None,
                "surfaces": surfaces,
                "actions": actions,
            }

        return await self._run(read)

    async def surface(self, chat: str, surface: str) -> dict[str, Any]:
        return await self._run(lambda db: _surface(db, chat, surface))

    async def propose(
        self, person: Person, chat: str, surface_id: str, proposal: dict[str, Any]
    ) -> dict[str, Any]:
        encoded(proposal["arguments"], 16 * 1024)

        def write(db: sqlite3.Connection) -> dict[str, Any]:
            _own(db, chat, person)
            old = _action(db, chat, proposal["id"])
            if old:
                if old["surfaceId"] != surface_id or encoded(
                    {k: old[k] for k in proposal}, 20 * 1024
                ) != encoded(proposal, 20 * 1024):
                    raise InteractionError("That action ID already names a different request.")
                return old
            s = _surface(db, chat, surface_id)
            action = {
                **proposal,
                "surfaceId": surface_id,
                "sessionId": person.session_id,
                "expiresAt": min(s["expires_at"], time.time() + 900),
                "status": "proposed",
                "result": None,
                "modelText": None,
                "error": None,
            }
            if not _valid(db, s, action) or proposal["name"] not in s["actions"]:
                raise InteractionError("This action is no longer available at this revision.")
            if (
                db.execute(
                    "SELECT count(*) FROM interaction_actions WHERE chat_id=?", (chat,)
                ).fetchone()[0]
                >= 256
            ):
                raise InteractionError("This chat has reached its action limit.", 413)
            packed = encoded(action, 20 * 1024)
            _quota(db, person.sub, len(packed.encode()) + 144 * 1024)
            db.execute(
                "INSERT INTO interaction_actions VALUES (?,?,?,?,?,?)",
                (chat, action["id"], surface_id, person.session_id, "proposed", packed),
            )
            touch(db, chat)
            return action

        return await self._run(write)

    async def existing(self, person: Person, chat: str, action_id: str) -> dict[str, Any] | None:
        def read(db: sqlite3.Connection) -> dict[str, Any] | None:
            _own(db, chat, person)
            return _action(db, chat, action_id)

        return await self._run(read)

    async def decide(
        self, person: Person, chat: str, action_id: str, approve: bool
    ) -> dict[str, Any]:
        def write(db: sqlite3.Connection) -> dict[str, Any]:
            _own(db, chat, person)
            action = _action(db, chat, action_id)
            if action is None:
                raise InteractionError("There is no such action.", 404)
            if action["sessionId"] != person.session_id:
                raise InteractionError("Only the originating sign-in can decide this action.")
            if action["status"] != "proposed":
                return action
            if not approve or not _valid(db, _surface(db, chat, action["surfaceId"]), action):
                action["status"] = "cancelled"
            else:
                action["status"] = "queued"
            _save(db, chat, action)
            return action

        return await self._run(write)

    async def claim(
        self, chat: str, action_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        def write(db: sqlite3.Connection) -> tuple[dict[str, Any], dict[str, Any]] | None:
            action = _action(db, chat, action_id)
            if action is None or action["status"] != "queued":
                return None
            s = _surface(db, chat, action["surfaceId"])
            if not _valid(db, s, action):
                action["status"] = "cancelled"
                _save(db, chat, action)
                return None
            action["status"] = "dispatching"
            _save(db, chat, action)
            return s, action

        return await self._run(write)

    async def finish(
        self,
        chat: str,
        action_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        model_text: str | None = None,
        error: str | None = None,
    ) -> None:
        encoded(result, 64 * 1024)
        encoded(model_text, 64 * 1024)

        def write(db: sqlite3.Connection) -> None:
            action = _action(db, chat, action_id)
            if action is None or action["status"] not in LIVE:
                return
            _quota(
                db,
                _surface(db, chat, action["surfaceId"])["owner"],
                len(encoded(result, 64 * 1024).encode())
                + len(encoded(model_text, 64 * 1024).encode())
                - 144 * 1024,
            )
            action.update(status=status, result=result, modelText=model_text, error=error)
            _save(db, chat, action)

        await self._run(write)

    async def stop(self, chat: str) -> None:
        def write(db: sqlite3.Connection) -> None:
            changed = db.execute(
                "UPDATE interaction_surfaces SET state='disabled', revision=revision+1 "
                "WHERE chat_id=? AND state='active'",
                (chat,),
            ).rowcount
            if changed:
                touch(db, chat)
            self.sweep_in_transaction(db, chat)

        await self._run(write)
