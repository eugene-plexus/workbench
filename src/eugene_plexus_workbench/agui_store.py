"""Durable AG-UI intents and event projections on Store's single connection."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import agui_protocol as protocol
from .interaction_store import InteractionError, _own, encoded

if TYPE_CHECKING:
    from .store import Person, Store

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS agui_agents (id TEXT PRIMARY KEY, data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS agui_runs ("
    "chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE, run_id TEXT NOT NULL, "
    "owner TEXT NOT NULL, status TEXT NOT NULL, cursor INTEGER NOT NULL DEFAULT 0, "
    "size INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL, PRIMARY KEY(chat_id, run_id))",
    "CREATE INDEX IF NOT EXISTS agui_runs_owner ON agui_runs(owner)",
    "CREATE TABLE IF NOT EXISTS agui_events (chat_id TEXT NOT NULL, run_id TEXT NOT NULL, "
    "seq INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(chat_id, run_id, seq), "
    "FOREIGN KEY(chat_id,run_id) REFERENCES agui_runs(chat_id,run_id) ON DELETE CASCADE)",
]

MAX_EVENTS = 4096
MAX_RUNS = 64


def allow_chat_write(db: sqlite3.Connection, chat: str, run_id: str | None = None) -> None:
    row = db.execute(
        "SELECT run_id FROM agui_runs WHERE chat_id=? AND status='running'", (chat,)
    ).fetchone()
    if row is not None and row[0] != run_id:
        raise InteractionError("An AG-UI run is active in this chat.")


def public(run: dict[str, Any]) -> dict[str, Any]:
    return {
        k: run[k]
        for k in (
            "threadId",
            "runId",
            "direction",
            "agentId",
            "status",
            "cursor",
            "messages",
            "state",
            "answerId",
            "outcome",
        )
    }


def public_agent(agent: dict[str, Any]) -> dict[str, Any]:
    return {k: agent[k] for k in ("id", "name", "url", "subjects")}


def read(row: sqlite3.Row) -> dict[str, Any]:
    return {**json.loads(row["data"]), "status": row["status"], "cursor": row["cursor"]}


def current(db: sqlite3.Connection, person: Person, chat: str) -> None:
    _own(db, chat, person)
    session = db.execute("SELECT * FROM sessions WHERE id_hash=?", (person.session_id,)).fetchone()
    if (
        session is None
        or session["sub"] != person.sub
        or min(session["expires_at"], session["access_expires_at"]) <= time.time()
    ):
        raise InteractionError("This sign-in is no longer valid.", 401)


def agent_in(db: sqlite3.Connection, agent_id: str, person: Person) -> dict[str, Any]:
    row = db.execute("SELECT data FROM agui_agents WHERE id=?", (agent_id,)).fetchone()
    if row is None:
        raise InteractionError("There is no such AG-UI agent.", 404)
    result: dict[str, Any] = json.loads(row[0])
    if person.sub not in result["subjects"]:
        raise InteractionError("This person is not allowed to run that AG-UI agent.", 403)
    return result


def continuation(body: dict[str, Any], previous: dict[str, Any] | None, direction: str) -> None:
    history = previous["messages"] if previous else []
    state = previous["state"] if previous else {}
    supplied = body.get("state", None if state is None else {})
    if not protocol.same(supplied, state):
        protocol.refuse("Continue with the authoritative saved AG-UI state.")
    if body.get("parentRunId") != (previous["runId"] if previous else None):
        protocol.refuse("parentRunId must identify the latest AG-UI run.")
    incoming = body["messages"]
    resume = body.get("resume", [])
    outcome = previous.get("outcome") if previous else None
    if outcome and outcome["type"] == "interrupt":
        if direction != "connect" or incoming != history:
            protocol.refuse("Resume an external interrupt with unchanged saved messages.")
        interrupts = outcome["interrupts"]
        if len(resume) != len(interrupts) or {r["interruptId"] for r in resume} != {
            i["id"] for i in interrupts
        }:
            protocol.refuse("Resume must answer each outstanding interrupt exactly once.")
        for interrupt in interrupts:
            expiry = interrupt.get("expiresAt")
            if expiry:
                try:
                    parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                    if parsed.tzinfo is None or parsed.timestamp() <= time.time():
                        protocol.refuse("The AG-UI interrupt has expired or has no timezone.")
                except ValueError:
                    protocol.refuse("Invalid AG-UI interrupt expiry.")
            answer = next(r for r in resume if r["interruptId"] == interrupt["id"])
            if answer["status"] == "resolved" and "responseSchema" in interrupt:
                protocol.response_schema(interrupt["responseSchema"], answer.get("payload"))
    else:
        if resume or len(incoming) != len(history) + 1 or incoming[:-1] != history:
            protocol.refuse("Continue with saved messages followed by one new user text message.")
        user = incoming[-1]
        if (
            set(user) != {"id", "role", "content"}
            or user["role"] != "user"
            or not user["content"].strip()
        ):
            protocol.refuse("A new AG-UI run requires one plain user text message.")


class AGUIStore:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def agents(self) -> list[dict[str, Any]]:
        return await self.store.interactions._run(
            lambda db: [
                json.loads(r[0]) for r in db.execute("SELECT data FROM agui_agents ORDER BY id")
            ]
        )

    async def add_agent(self, agent: dict[str, Any]) -> None:
        def write(db: sqlite3.Connection) -> None:
            if db.execute("SELECT count(*) FROM agui_agents").fetchone()[0] >= 64:
                raise InteractionError("AG-UI agent limit reached.", 429)
            db.execute(
                "INSERT INTO agui_agents VALUES (?,?)", (agent["id"], encoded(agent, 32 * 1024))
            )

        await self.store.interactions._run(write)

    async def delete_agent(self, agent_id: str) -> None:
        await self.store.interactions._run(
            lambda db: db.execute("DELETE FROM agui_agents WHERE id=?", (agent_id,))
        )

    async def authorize(
        self, chat: str, person: Person, agent_id: str | None
    ) -> dict[str, Any] | None:
        def check(db: sqlite3.Connection) -> dict[str, Any] | None:
            current(db, person, chat)
            return agent_in(db, agent_id, person) if agent_id else None

        return await self.store.interactions._run(check)

    async def get(self, chat: str, run: str | None = None) -> dict[str, Any] | None:
        def query(db: sqlite3.Connection) -> dict[str, Any] | None:
            row = db.execute(
                "SELECT * FROM agui_runs WHERE chat_id=? "
                + ("AND run_id=?" if run else "ORDER BY rowid DESC LIMIT 1"),
                (chat, run) if run else (chat,),
            ).fetchone()
            return read(row) if row else None

        return await self.store.interactions._run(query)

    async def claim(
        self, chat: str, person: Person, body: dict[str, Any], agent_id: str | None
    ) -> tuple[dict[str, Any], bool]:
        direction = "connect" if agent_id else "serve"
        digest = hashlib.sha256(encoded(body, protocol.PROJECTION).encode()).hexdigest()

        def claim(db: sqlite3.Connection) -> tuple[dict[str, Any], bool]:
            from .store import _message, path

            current(db, person, chat)
            if agent_id:
                agent_in(db, agent_id, person)
            old = db.execute(
                "SELECT * FROM agui_runs WHERE chat_id=? AND run_id=?", (chat, body["runId"])
            ).fetchone()
            if old:
                run = read(old)
                if run["requestHash"] != digest or run["agentId"] != agent_id:
                    raise InteractionError("The run ID was already used with different input.")
                return run, False
            previous_row = db.execute(
                "SELECT * FROM agui_runs WHERE chat_id=? ORDER BY rowid DESC LIMIT 1", (chat,)
            ).fetchone()
            previous = read(previous_row) if previous_row else None
            shown = path(
                [
                    _message(r)
                    for r in db.execute(
                        "SELECT * FROM messages WHERE chat_id=? ORDER BY seq", (chat,)
                    )
                ]
            )
            if any(m.status == "running" for m in shown):
                raise InteractionError("An answer is already running in this chat.")
            if previous:
                if (
                    previous["status"] == "running"
                    or previous["direction"] != direction
                    or previous["agentId"] != agent_id
                ):
                    raise InteractionError(
                        "This AG-UI thread is busy or belongs to a different agent."
                    )
                if previous.get("successor") or not shown or shown[-1].id != previous["answerId"]:
                    raise InteractionError(
                        "This AG-UI continuation was consumed or the chat branch changed."
                    )
            elif shown:
                raise InteractionError("Start AG-UI in a new empty chat.")
            continuation(body, previous, direction)
            counts = db.execute(
                "SELECT count(*), sum(chat_id=?) FROM agui_runs WHERE owner=?", (chat, person.sub)
            ).fetchone()
            if counts[0] >= MAX_RUNS or (counts[1] or 0) >= 32:
                raise InteractionError(
                    "Saved AG-UI run limit reached; delete an old chat to release space.", 429
                )
            if (
                db.execute("SELECT count(*) FROM agui_runs WHERE status='running'").fetchone()[0]
                >= 8
            ):
                raise InteractionError("Too many AG-UI runs are active.", 429)
            run = {
                "threadId": chat,
                "runId": body["runId"],
                "direction": direction,
                "agentId": agent_id,
                "owner": person.sub,
                "sessionId": person.session_id,
                "requestHash": digest,
                "status": "running",
                "cursor": 0,
                "answerId": None,
                "outcome": None,
                "messages": body["messages"],
                "state": previous["state"] if previous else {},
                "createdAt": time.time(),
                "seedIds": [m["id"] for m in body["messages"]],
            }
            if previous:
                previous["successor"] = body["runId"]
                db.execute(
                    "UPDATE agui_runs SET data=? WHERE chat_id=? AND run_id=?",
                    (encoded(previous, 2 * protocol.PROJECTION), chat, previous["runId"]),
                )
            db.execute(
                "INSERT INTO agui_runs(chat_id,run_id,owner,status,data) VALUES (?,?,?,?,?)",
                (chat, body["runId"], person.sub, "running", encoded(run, 2 * protocol.PROJECTION)),
            )
            return run, True

        return await self.store.interactions._run(claim)

    async def attach(self, chat: str, run_id: str, answer: str) -> None:
        def write(db: sqlite3.Connection) -> None:
            row = db.execute(
                "SELECT * FROM agui_runs WHERE chat_id=? AND run_id=?", (chat, run_id)
            ).fetchone()
            run = read(row)
            run["answerId"] = answer
            db.execute(
                "UPDATE agui_runs SET data=? WHERE chat_id=? AND run_id=?",
                (encoded(run, 2 * protocol.PROJECTION), chat, run_id),
            )

        await self.store.interactions._run(write)

    async def append(
        self,
        chat: str,
        run_id: str,
        event: dict[str, Any],
        snapshot: dict[str, Any] | None,
        status: str = "running",
        *,
        force: bool = False,
    ) -> None:
        data = encoded(event, protocol.FRAME)

        def write(db: sqlite3.Connection) -> None:
            row = db.execute(
                "SELECT * FROM agui_runs WHERE chat_id=? AND run_id=?", (chat, run_id)
            ).fetchone()
            if row is None or row["status"] != "running":
                raise InteractionError("This AG-UI run is no longer active.")
            if not force and (
                row["cursor"] >= MAX_EVENTS - 1
                or row["size"] + len(data.encode()) > 4 * 1024 * 1024 - 1024
            ):
                raise InteractionError("AG-UI event history limit reached.", 413)
            run = read(row)
            if snapshot is not None:
                run.update(snapshot)
            cursor = row["cursor"] + 1
            db.execute("INSERT INTO agui_events VALUES (?,?,?,?)", (chat, run_id, cursor, data))
            db.execute(
                "UPDATE agui_runs SET status=?,cursor=?,size=size+?,data=? "
                "WHERE chat_id=? AND run_id=?",
                (
                    status,
                    cursor,
                    len(data.encode()),
                    encoded(run, 2 * protocol.PROJECTION),
                    chat,
                    run_id,
                ),
            )
            if run["direction"] == "connect" and run["answerId"]:
                text = "\n\n".join(
                    m.get("content", "")
                    for m in run["messages"]
                    if m["role"] == "assistant" and m["id"] not in run["seedIds"]
                )
                message_status = (
                    "running"
                    if status == "running"
                    else "done"
                    if status in {"done", "interrupted"}
                    else "interrupted"
                    if status == "uncertain"
                    else "stopped"
                    if status == "cancelled"
                    else "failed"
                )
                db.execute(
                    "UPDATE messages SET content=?,status=?,error=?,finished_at=? WHERE id=?",
                    (
                        text,
                        message_status,
                        event.get("message") if status in {"error", "uncertain"} else None,
                        None if status == "running" else time.time(),
                        run["answerId"],
                    ),
                )

        await self.store.interactions._run(write)

    async def events(self, chat: str, run: str, after: int) -> list[tuple[int, dict[str, Any]]]:
        return await self.store.interactions._run(
            lambda db: [
                (r[0], json.loads(r[1]))
                for r in db.execute(
                    "SELECT seq,data FROM agui_events WHERE chat_id=? AND run_id=? AND seq>? "
                    "ORDER BY seq LIMIT 64",
                    (chat, run, after),
                )
            ]
        )

    async def recover(self) -> None:
        runs = await self.store.interactions._run(
            lambda db: [
                read(r) for r in db.execute("SELECT * FROM agui_runs WHERE status='running'")
            ]
        )
        for run in runs:
            await self.append(
                run["threadId"],
                run["runId"],
                {
                    "type": "RUN_ERROR",
                    "code": "EUGENE_UNCERTAIN",
                    "message": "Workbench restarted before this run completed. "
                    "It was not replayed.",
                },
                None,
                "uncertain",
                force=True,
            )
