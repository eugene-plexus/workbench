"""Storage helpers for recoverable branches; physical erasure belongs to row 7's purge."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .interaction_store import InteractionError

DAY = 86400


def _word_runs(text: str) -> set[tuple[str, ...]]:
    words = text.casefold().split()
    return {tuple(words[n : n + 8]) for n in range(len(words) - 7)}


def _branch_runs(payload: dict[str, Any]) -> set[tuple[str, ...]]:
    return set().union(
        *(
            _word_runs(str(message.get(field) or ""))
            for message in payload["tables"]["messages"]
            for field in ("content", "reasoning")
        )
    )


class LegacyRecall(InteractionError):
    def __init__(self, recalls: list[dict[str, str]]) -> None:
        # Retry metadata must not keep another copy of the recalled answer's words.
        self.recalls = [
            {key: recall[key] for key in ("id", "title", "signature")} for recall in recalls
        ]
        super().__init__(
            "This answer cannot be deleted for good while an older memory recall may quote it: "
            f"{recalls[0]['title']}. Remove that recall to finish."
        )


def backup_scope(db: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    """A backup recall can block only while its answer is reachable in the live store."""
    return {
        **payload,
        "live_recall_messages": {
            (r[0], r[1])
            for r in db.execute(
                "SELECT m.chat_id,m.id FROM messages m JOIN chats c ON c.id=m.chat_id "
                "WHERE c.owner=?",
                (payload["owner"],),
            )
        },
    }


def legacy_recalls(
    db: sqlite3.Connection, payload: dict[str, Any], *, overlapping: bool = True
) -> list[dict[str, str]]:
    """Only an unidentified recall sharing eight consecutive words can hold this purge."""
    payload = flatten(payload)
    ids = set(payload["ids"])

    branch_runs = _branch_runs(payload)
    if overlapping and not branch_runs:
        return []
    chats = {r["id"]: r["title"] for r in rows(db, "chats") if r["owner"] == payload["owner"]}
    copies = rows(db, "messages")
    for archive in rows(db, "deleted_branches"):
        if archive["owner"] == payload["owner"] and archive["id"] not in ids:
            copies += flatten(json.loads(archive["payload"]))["tables"].get("messages", [])
    found = []
    for message in copies:
        if message["chat_id"] not in chats or message["id"] in ids:
            continue
        live = payload.get("live_recall_messages")
        if live is not None and (message["chat_id"], message["id"]) not in live:
            continue
        if payload.get("deleting_chat") and message["chat_id"] == payload["chat"]:
            continue
        used = json.loads(message.get("memory_used") or "null") or {}
        recalled = (used.get("memoryServer") or {}).get("memories") or []
        for recall in recalled:
            if not recall.get("documentId") and (
                not overlapping or branch_runs & _word_runs(str(recall.get("text") or ""))
            ):
                found.append(
                    {
                        "id": message["id"],
                        "title": chats[message["chat_id"]],
                        "signature": fingerprint(recall),
                        "text": str(recall.get("text") or ""),
                    }
                )
    return sorted(found, key=lambda r: (r["id"], r["signature"]))


def fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def remove_recall(db: sqlite3.Connection, chat: str, mid: str, recall: dict[str, Any]) -> bool:
    def clean(message: dict[str, Any]) -> bool:
        if message["chat_id"] != chat or message["id"] != mid:
            return False
        used = json.loads(message.get("memory_used") or "null") or {}
        server = used.get("memoryServer") or {}
        memories = server.get("memories") or []
        if recall not in memories:
            return False
        server["memories"] = [m for m in memories if m != recall]
        server["forgotten"] = (
            int(server.get("forgotten") or 0) + len(memories) - len(server["memories"])
        )
        message["memory_used"] = json.dumps(used)
        return True

    changed = False
    for message in rows(db, "messages"):
        if clean(message):
            db.execute(
                "UPDATE messages SET memory_used=? WHERE id=?", (message["memory_used"], mid)
            )
            changed = True
    for archive in rows(db, "deleted_branches"):
        payload = json.loads(archive["payload"])
        updates = [clean(message) for message in payload["tables"].get("messages", [])]
        if any(updates):
            db.execute(
                "UPDATE deleted_branches SET payload=? WHERE id=?",
                (json.dumps(payload), archive["id"]),
            )
            changed = True
    return changed


def deleted_view(row: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(row["payload"])
    root = next(r for r in payload["tables"]["messages"] if r["id"] == row["id"])
    number = list(payload["chosen"]).index(row["id"]) + 1
    what = "answer" if root["role"] == "assistant" else "message"
    return {
        "id": row["id"],
        "deleteAt": row["delete_at"],
        "state": row["state"],
        "problem": row["problem"],
        "message": f"Deleted {what} {number} and the {len(payload['ids']) - 1} messages after it.",
    }


def undo_index(db: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    """A shared attachment's document may now be held by a hidden sibling."""
    sources = {f"attachment:{r['id']}" for r in payload.get("shared_files", [])}
    index = payload.get("index") or {"documents": [], "chunks": [], "files": {}}
    held = {r["id"] for r in index["documents"]}
    for archive in rows(db, "deleted_branches"):
        if archive["owner"] != payload["owner"]:
            continue
        other = json.loads(archive["payload"]).get("index", {})
        docs = [
            r for r in other.get("documents", []) if r["source"] in sources and r["id"] not in held
        ]
        wanted = {r["id"] for r in docs}
        held |= wanted
        index["documents"].extend(docs)
        index["chunks"].extend(r for r in other.get("chunks", []) if r["doc_id"] in wanted)
        for path, content in other.get("files", {}).items():
            name = path.split("/")[-1]
            if any(name in (mid, mid + ".txt", mid + ".meta.json") for mid in wanted):
                index["files"][path] = content
    return index


def migration(db: sqlite3.Connection) -> None:
    columns = {r[1] for r in db.execute("PRAGMA table_info(messages)")}
    if "branch_name" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN branch_name TEXT")
    db.execute(
        "CREATE TABLE IF NOT EXISTS deleted_branches ("
        "id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, "
        "owner TEXT NOT NULL, deleted_at REAL NOT NULL, delete_at REAL NOT NULL, "
        "state TEXT NOT NULL DEFAULT 'hidden', payload TEXT NOT NULL, "
        "problem TEXT, steps TEXT NOT NULL DEFAULT '{}')"
    )


def rows(db: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        cursor = db.execute(f"SELECT * FROM {table}")
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, r, strict=True)) for r in cursor]
    return []


def capture(db: sqlite3.Connection, chat: str, mid: str) -> dict[str, Any]:
    from .agui_store import allow_chat_write

    allow_chat_write(db, chat)
    tree = [r for r in rows(db, "messages") if r["chat_id"] == chat]
    root = next((r for r in tree if r["id"] == mid), None)
    if root is None:
        raise InteractionError("There is no such message in this chat.", 404)
    if any(r["status"] == "running" for r in tree):
        raise InteractionError("Wait for the answer, or stop it.")
    siblings = [r for r in tree if r["parent_id"] == root["parent_id"]]
    if root["parent_id"] is None and len(siblings) == 1:
        raise InteractionError("This is the whole chat. Delete the chat instead.")
    owner = db.execute("SELECT owner FROM chats WHERE id=?", (chat,)).fetchone()[0]
    archives = [
        r
        for r in rows(db, "deleted_branches")
        if r["chat_id"] == chat and r["state"] != "preparing"
    ]
    expanded = list(tree)
    for archive in archives:
        expanded += json.loads(archive["payload"])["tables"].get("messages", [])
    ids = {mid}
    while True:
        more = {r["id"] for r in expanded if r["parent_id"] in ids}
        if more <= ids:
            break
        ids |= more
    tables: dict[str, list[dict[str, Any]]] = {
        "messages": [r for r in tree if r["id"] in ids],
        "deleted_branches": [],
    }
    kept_files = {fid for r in tree if r["id"] not in ids for fid in json.loads(r["attachments"])}
    attached = {fid for r in expanded if r["id"] in ids for fid in json.loads(r["attachments"])}
    candidates = [
        r
        for r in rows(db, "files")
        if r["owner"] == owner
        and r["chat_id"] == chat
        and (r["id"] in attached or r.get("message_id") in ids)
    ]
    tables["files"] = [r for r in candidates if r["id"] not in kept_files]
    # A hidden sibling may currently hold the file row. Undo needs that record too.
    for archive in archives:
        for r in flatten(json.loads(archive["payload"]))["tables"].get("files", []):
            if r["id"] in attached and all(c["id"] != r["id"] for c in candidates):
                candidates.append(r)
    tables["artifacts"] = [
        r
        for r in rows(db, "artifacts")
        if r["chat_id"] == chat and r["owner"] == owner and r["message_id"] in ids
    ]
    arts = {r["id"] for r in tables["artifacts"]}
    tables["artifact_revisions"] = [
        r for r in rows(db, "artifact_revisions") if r["artifact_id"] in arts
    ]
    sourced_notes = [
        r
        for r in rows(db, "memory_notes")
        if r["owner"] == owner and r.get("source_message_id") in ids
    ]
    tables["memory_notes"] = [r for r in sourced_notes if r["status"] == "pending"]
    notes = {r["id"] for r in tables["memory_notes"]}
    for table in ("memory_events", "memory_note_tags", "memory_note_documents"):
        tables[table] = [r for r in rows(db, table) if r["note_id"] in notes]
    tables["memory_tidy"] = [
        r
        for r in rows(db, "memory_tidy")
        if r["owner"] == owner and set(json.loads(r["pair"])) & notes
    ]
    tables["memory_server_sent"] = [
        r
        for r in rows(db, "memory_server_sent")
        if r["owner"] == owner and r["chat_id"] == chat and r["message_id"] in ids
    ]
    tables["interaction_surfaces"] = [
        r
        for r in rows(db, "interaction_surfaces")
        if r["chat_id"] == chat and r["message_id"] in ids
    ]
    surfaces = {r["id"] for r in tables["interaction_surfaces"]}
    tables["interaction_actions"] = [
        r for r in rows(db, "interaction_actions") if r["surface_id"] in surfaces
    ]
    # A run may have written more than one version. Its projection is filtered below;
    # only runs whose produced messages all go are themselves removed.
    runs = []
    for r in rows(db, "agui_runs"):
        if r["chat_id"] != chat:
            continue
        data = json.loads(r["data"])
        made = {
            m["id"] for m in data.get("messages", []) if m.get("id") not in data.get("seedIds", [])
        }
        if data.get("answerId"):
            made.add(data["answerId"])
        if data.get("direction") == "connect" and data.get("answerId"):
            made = {data["answerId"]}
        if made and made <= ids:
            runs.append(r)
    tables["agui_runs"] = runs
    run_ids = {r["run_id"] for r in runs}
    tables["agui_events"] = [
        r for r in rows(db, "agui_events") if r["chat_id"] == chat and r["run_id"] in run_ids
    ]
    mixed = [
        r
        for r in rows(db, "agui_runs")
        if r["chat_id"] == chat
        and r["run_id"] not in run_ids
        and mentions(json.loads(r["data"]), ids)
    ]
    mixed_ids = {r["run_id"] for r in mixed}
    return {
        "owner": owner,
        "chat": chat,
        "root": mid,
        "ids": sorted(ids),
        "tables": tables,
        "chosen": {r["id"]: r["chosen"] for r in siblings},
        "index": {},
        "shared_files": candidates,
        "kept_notes": sum(r["status"] != "pending" for r in sourced_notes),
        "nested_ids": [r["id"] for r in archives if r["id"] in ids],
        "projections": {
            "agui_runs": mixed,
            "agui_events": [
                r
                for r in rows(db, "agui_events")
                if r["chat_id"] == chat and r["run_id"] in mixed_ids
            ],
        },
    }


def mentions(value: Any, ids: set[str]) -> bool:
    if isinstance(value, dict):
        return any(
            (k in ("id", "messageId", "answerId") and isinstance(v, str) and v in ids)
            or mentions(v, ids)
            for k, v in value.items()
        )
    if isinstance(value, list):
        return any(mentions(v, ids) for v in value)
    return False


def resolve(db: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    out = {**payload, "tables": dict(payload["tables"])}
    out["tables"]["deleted_branches"] = [
        {**r, "payload": json.dumps(resolve(db, json.loads(r["payload"])))}
        for r in rows(db, "deleted_branches")
        if r["id"] in payload.get("nested_ids", [])
    ]
    return flatten(out)


def flatten(payload: dict[str, Any]) -> dict[str, Any]:
    """A subtree can contain earlier hidden subtrees, each with its own Undo record."""
    out = {
        **payload,
        "tables": {t: list(rs) for t, rs in payload["tables"].items()},
        "ids": list(payload["ids"]),
        "shared_files": list(payload.get("shared_files", [])),
        "index": {
            k: (dict(v) if isinstance(v, dict) else list(v))
            for k, v in payload.get("index", {}).items()
        },
    }
    for row in payload["tables"].get("deleted_branches", []):
        nested = flatten(json.loads(row["payload"]))
        out["ids"] = sorted(set(out["ids"]) | set(nested["ids"]))
        for table, records in nested["tables"].items():
            out["tables"].setdefault(table, []).extend(records)
        out["shared_files"].extend(nested.get("shared_files", []))
        for key, value in nested.get("index", {}).items():
            if isinstance(value, dict):
                out["index"].setdefault(key, {}).update(value)
            else:
                out["index"].setdefault(key, []).extend(value)
    return out


def purge_files(db: sqlite3.Connection, payload: dict[str, Any]) -> list[dict[str, Any]]:
    payload = flatten(payload)
    ids = set(payload["ids"])
    all_messages = rows(db, "messages")
    for row in rows(db, "deleted_branches"):
        if row["id"] not in ids:
            all_messages += flatten(json.loads(row["payload"]))["tables"].get("messages", [])
    shared = {
        fid
        for r in all_messages
        if r["id"] not in ids
        for fid in json.loads(r.get("attachments") or "[]")
    }
    attached = {
        fid
        for r in [*all_messages, *payload["tables"]["messages"]]
        if r["id"] in ids
        for fid in json.loads(r.get("attachments") or "[]")
    }
    candidates = {
        r["id"]: r
        for r in [
            *payload.get("shared_files", []),
            *payload["tables"].get("files", []),
            *rows(db, "files"),
        ]
        if r["owner"] == payload["owner"]
        and r.get("chat_id") == payload["chat"]
        and (r["id"] in attached or r.get("message_id") in ids)
    }
    return [r for fid, r in candidates.items() if fid not in shared]


def summary(payload: dict[str, Any]) -> dict[str, Any]:
    tables = payload["tables"]
    messages = tables["messages"]
    return {
        "messages": len(payload["ids"]),
        "answers": sum(r["role"] == "assistant" for r in messages),
        "files": len(tables["files"]),
        "artifacts": len(tables["artifacts"]),
        "pendingNotes": sum(r["status"] == "pending" for r in tables["memory_notes"]),
        "keptNotes": payload.get("kept_notes", 0),
        "toolCalls": sum(
            len(t.get("calls", [])) for r in messages for t in json.loads(r["tool_rounds"])
        ),
        "chosen": bool(next(r for r in messages if r["id"] == payload["root"])["chosen"]),
    }


def _delete(db: sqlite3.Connection, table: str, selected: list[dict[str, Any]]) -> int:
    if not selected or not rows_exist(db, table):
        return 0
    columns = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
    keys = [r[1] for r in db.execute(f"PRAGMA table_info({table})") if r[5]]
    if not keys:
        keys = sorted(columns)
    gone = 0
    for r in selected:
        where = [k for k in keys if k in r and k in columns]
        if where:
            gone += db.execute(
                f"DELETE FROM {table} WHERE {' AND '.join(f'{k} IS ?' for k in where)}",
                [r[k] for k in where],
            ).rowcount
    return gone


def rows_exist(db: sqlite3.Connection, table: str) -> bool:
    return (
        db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        is not None
    )


def scrub_message(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, str]:
    from .store import CHATS_SERVER, FORGOTTEN_CHAT

    ids = set(payload["ids"])
    notes = {r["id"] for r in payload["tables"].get("memory_notes", [])}
    changed: dict[str, str] = {}
    used = json.loads(row.get("memory_used") or "null")
    if isinstance(used, dict):
        server = used.get("memoryServer")
        if isinstance(server, dict):
            documents = {r["document_id"] for r in payload["tables"].get("memory_server_sent", [])}
            old_memories = server.get("memories") or []
            chat_deleted = payload.get("deleting_chat") and row.get("chat_id") == payload["chat"]
            live = payload.get("live_recall_messages")
            orphaned = live is not None and (row.get("chat_id"), row.get("id")) not in live
            scrub_legacy = chat_deleted or orphaned
            branch_runs = _branch_runs(payload) if scrub_legacy else set()
            kept_memories = [
                m
                for m in old_memories
                if m.get("documentId") not in documents
                and not (
                    scrub_legacy
                    and not m.get("documentId")
                    and branch_runs & _word_runs(str(m.get("text") or ""))
                )
            ]
            if len(kept_memories) != len(old_memories):
                server["memories"] = kept_memories
                server["forgotten"] = (
                    int(server.get("forgotten") or 0) + len(old_memories) - len(kept_memories)
                )
                changed["memory_used"] = json.dumps(used)
        past = used.get("pastChats")
        if isinstance(past, dict):
            old = past.get("exchanges") or []
            kept = [e for e in old if e.get("messageId") not in ids]
            if len(old) != len(kept):
                past["exchanges"] = kept
                past["forgotten"] = int(past.get("forgotten") or 0) + len(old) - len(kept)
                changed["memory_used"] = json.dumps(used)
        old = used.get("notes") or []
        kept = [n for n in old if n.get("id") not in notes]
        if len(old) != len(kept):
            used["notes"] = kept
            used["forgotten"] = int(used.get("forgotten") or 0) + len(old) - len(kept)
            changed["memory_used"] = json.dumps(used)
    rounds = json.loads(row.get("tool_rounds") or "[]")
    for turn in rounds:
        for call in turn.get("calls", []):
            if call.get("serverId") == CHATS_SERVER and payload["chat"] in (
                call.get("chats") or []
            ):
                call["result"], call["content"] = FORGOTTEN_CHAT, []
                call["chats"] = [c for c in call["chats"] if c != payload["chat"]]
                changed["tool_rounds"] = json.dumps(rounds)
    return changed


def filter_retained(
    db: sqlite3.Connection,
    mid: str,
    values: dict[str, Any],
    forgotten: list[tuple[str, str, str, float]],
) -> dict[str, Any]:
    """An answer already in flight must never write a removed excerpt back."""
    row = db.execute(
        "SELECT m.*,c.owner FROM messages m JOIN chats c ON c.id=m.chat_id WHERE m.id=?", (mid,)
    ).fetchone()
    if row is None:
        return values
    clean = dict(values)
    used = json.loads(json.dumps(values.get("memory_used"))) if "memory_used" in values else None
    if isinstance(used, dict):
        past = used.get("pastChats")
        if isinstance(past, dict):
            previous = past.get("exchanges") or []
            kept = [
                e
                for e in previous
                if not e.get("messageId")
                or db.execute("SELECT 1 FROM messages WHERE id=?", (e["messageId"],)).fetchone()
            ]
            past["exchanges"] = kept
            if len(kept) != len(previous):
                past["forgotten"] = int(past.get("forgotten") or 0) + len(previous) - len(kept)
        notes = used.get("notes")
        if isinstance(notes, list):
            used["notes"] = [
                n
                for n in notes
                if not n.get("id")
                or db.execute(
                    "SELECT 1 FROM memory_notes WHERE id=? AND owner=?", (n["id"], row["owner"])
                ).fetchone()
            ]
        server = used.get("memoryServer")
        if isinstance(server, dict):
            kept = []
            for memory in server.get("memories") or []:
                document = str(memory.get("documentId") or "")
                chat, _, answer = document.partition(":")
                own = db.execute(
                    "SELECT 1 FROM chats WHERE id=? AND owner=?", (chat, row["owner"])
                ).fetchone()
                own = own or any(
                    owner == row["owner"] and gone_chat == chat
                    for _, owner, gone_chat, _ in forgotten
                )
                if (
                    own
                    and answer
                    and not db.execute(
                        "SELECT 1 FROM messages WHERE id=? AND chat_id=?", (answer, chat)
                    ).fetchone()
                ):
                    continue
                kept.append(memory)
            server["memories"] = kept
        clean["memory_used"] = used
    if "tool_rounds" in values:
        rounds = json.loads(json.dumps(values["tool_rounds"]))
        for _, owner, chat, at in forgotten:
            if row["owner"] != owner or row["created_at"] > at:
                continue
            change = scrub_message(
                {"tool_rounds": json.dumps(rounds)}, {"ids": [], "chat": chat, "tables": {}}
            )
            if "tool_rounds" in change:
                rounds = json.loads(change["tool_rounds"])
        clean["tool_rounds"] = rounds
    return clean


def purge_rows(
    db: sqlite3.Connection, payload: dict[str, Any], *, hiding: bool = False
) -> dict[str, int]:
    """Row 7's `_purge_rows` delegates its branch scope here, for live and old schemas."""
    from .store import Store

    payload = flatten(payload)
    owner, chat, ids = payload["owner"], payload["chat"], set(payload["ids"])
    tables = payload["tables"]
    if not hiding and (recalls := legacy_recalls(db, payload)):
        raise LegacyRecall(recalls)
    # Backups may predate a child or its records. Find dependencies in that copy too.
    local = {r["id"] for r in rows(db, "messages") if r["chat_id"] == chat and r["id"] in ids}
    actual = dict(tables)
    for table, column, wanted in (
        ("messages", "id", ids),
        ("artifacts", "message_id", ids),
        ("memory_notes", "source_message_id", ids),
        ("interaction_surfaces", "message_id", ids),
        ("memory_server_sent", "message_id", ids),
        ("deleted_branches", "id", ids),
    ):
        actual[table] = [
            r
            for r in rows(db, table)
            if r.get(column) in wanted
            and r.get("owner", owner) == owner
            and r.get("chat_id", chat) == chat
            and (table != "memory_notes" or r["status"] == "pending")
        ]
    if not hiding and rows_exist(db, "memory_notes"):
        # Kept notes are independent memory. Only their deleted message link goes,
        # including in schema backups; their words, history and index stay.
        db.executemany(
            "UPDATE memory_notes SET source_message_id=NULL WHERE id=? AND owner=?",
            [
                (r["id"], owner)
                for r in rows(db, "memory_notes")
                if r["owner"] == owner
                and r.get("source_message_id") in ids
                and r["status"] != "pending"
            ],
        )
    for table, column, target in (
        ("artifact_revisions", "artifact_id", "artifacts"),
        ("interaction_actions", "surface_id", "interaction_surfaces"),
        ("memory_events", "note_id", "memory_notes"),
        ("memory_note_tags", "note_id", "memory_notes"),
        ("memory_note_documents", "note_id", "memory_notes"),
    ):
        wanted = {r["id"] for r in actual[target]}
        actual[table] = [r for r in rows(db, table) if r.get(column) in wanted]
    notes = {r["id"] for r in actual["memory_notes"]}
    actual["memory_tidy"] = [
        r
        for r in rows(db, "memory_tidy")
        if r["owner"] == owner and set(json.loads(r["pair"])) & notes
    ]
    # Never remove a file an unaffected live or hidden message still uses.
    actual["files"] = tables.get("files", []) if hiding else purge_files(db, payload)
    if hiding:
        actual["deleted_branches"] = []
    own_chats = {r["id"] for r in rows(db, "chats") if r["owner"] == owner}
    for r in [] if hiding else rows(db, "messages"):
        if r["chat_id"] not in own_chats:
            continue
        if r["id"] in local:
            continue
        changes = scrub_message(r, payload)
        if changes:
            db.execute(
                f"UPDATE messages SET {', '.join(f'{k}=?' for k in changes)} WHERE id=?",
                (*changes.values(), r["id"]),
            )
    titles = {r["id"]: r["text"] for r in actual["memory_notes"]}
    if (
        not hiding
        and titles
        and {"memory_used", "tool_rounds"}
        <= {r[1] for r in db.execute("PRAGMA table_info(messages)")}
    ):
        Store._drop_from_answers(db, owner, titles)
        Store._drop_from_chats(db, owner, set(titles))
    scrub_runs(db, payload)
    order = (
        "artifact_revisions",
        "artifacts",
        "interaction_actions",
        "interaction_surfaces",
        "agui_events",
        "agui_runs",
        "memory_events",
        "memory_tidy",
        "memory_note_tags",
        "memory_note_documents",
        "memory_notes",
        "memory_server_sent",
        "files",
        "messages",
        "deleted_branches",
    )
    gone = {table: _delete(db, table, actual.get(table, [])) for table in order}
    for archive in rows(db, "deleted_branches"):
        if hiding or archive["owner"] != owner:
            continue
        saved = json.loads(archive["payload"])
        for message in saved["tables"].get("messages", []):
            message.update(scrub_message(message, payload))
        if not hiding:
            saved["ids"] = [mid for mid in saved["ids"] if mid not in ids]
            saved["nested_ids"] = [mid for mid in saved.get("nested_ids", []) if mid not in ids]
            saved["chosen"] = {
                mid: value for mid, value in saved["chosen"].items() if mid not in ids
            }
            removed_files = {r["id"] for r in actual["files"]}
            saved["shared_files"] = [
                r for r in saved.get("shared_files", []) if r["id"] not in removed_files
            ]
            saved["tables"]["files"] = [
                r for r in saved["tables"].get("files", []) if r["id"] not in removed_files
            ]
            affected_runs = {
                r["run_id"]
                for r in saved.get("projections", {}).get("agui_runs", [])
                if mentions(json.loads(r["data"]), ids)
            }
            for table, projections in saved.get("projections", {}).items():
                clean = []
                for projection in projections:
                    data = json.loads(projection["data"])
                    if table == "agui_events" and data.get("messageId") in ids:
                        continue
                    if table == "agui_events" and projection["run_id"] in affected_runs:
                        for key in ("snapshot", "delta", "state", "outcome", "value", "rawInput"):
                            if key in data:
                                data[key] = [] if key == "delta" else None
                    if table == "agui_runs" and mentions(data, ids):
                        data["messages"] = [
                            m for m in data.get("messages", []) if m.get("id") not in ids
                        ]
                        data["seedIds"] = [mid for mid in data.get("seedIds", []) if mid not in ids]
                        if data.get("answerId") in ids:
                            data["answerId"] = None
                        data["state"] = None
                        data["outcome"] = None
                    projection["data"] = json.dumps(data)
                    clean.append(projection)
                saved["projections"][table] = clean
        db.execute(
            "UPDATE deleted_branches SET payload=? WHERE id=?", (json.dumps(saved), archive["id"])
        )
    return gone


def scrub_runs(db: sqlite3.Connection, payload: dict[str, Any]) -> None:
    ids = set(payload["ids"])
    calls = {
        c["id"]
        for r in payload["tables"]["messages"]
        for turn in json.loads(r.get("tool_rounds") or "[]")
        for c in turn.get("calls", [])
        if c.get("id")
    }
    gone_runs = {r["run_id"] for r in payload["tables"].get("agui_runs", [])}

    def prune(value: Any) -> Any:
        if isinstance(value, list):
            return [
                prune(v)
                for v in value
                if not isinstance(v, dict)
                or (v.get("id") not in ids and v.get("messageId") not in ids)
            ]
        if isinstance(value, dict):
            return {
                k: (None if k == "answerId" and v in ids else prune(v)) for k, v in value.items()
            }
        return value

    affected = set()
    for row in rows(db, "agui_runs"):
        if row["chat_id"] != payload["chat"] or row["run_id"] in gone_runs:
            continue
        data = json.loads(row["data"])
        if not mentions(data, ids):
            continue
        affected.add(row["run_id"])
        data = prune(data)
        # State and outcomes have no message provenance. Once part of their run
        # is removed, retaining these arbitrary values could retain its words.
        data["state"] = None
        if "outcome" in data:
            data["outcome"] = None
        data["seedIds"] = [mid for mid in data.get("seedIds", []) if mid not in ids]
        db.execute(
            "UPDATE agui_runs SET data=? WHERE chat_id=? AND run_id=?",
            (json.dumps(data), row["chat_id"], row["run_id"]),
        )
    for row in rows(db, "agui_events"):
        if row["chat_id"] != payload["chat"] or row["run_id"] in gone_runs:
            continue
        data = json.loads(row["data"])
        if (
            row["run_id"] not in affected
            and data.get("messageId") not in ids
            and data.get("toolCallId") not in calls
            and not mentions(data, ids)
        ):
            continue
        if data.get("messageId") in ids or data.get("toolCallId") in calls:
            _delete(db, "agui_events", [row])
        else:
            if row["run_id"] in affected:
                for key in ("snapshot", "delta", "state", "outcome", "value", "rawInput"):
                    if key in data:
                        data[key] = [] if key == "delta" else None
            db.execute(
                "UPDATE agui_events SET data=? WHERE chat_id=? AND run_id=? AND seq=?",
                (json.dumps(prune(data)), row["chat_id"], row["run_id"], row["seq"]),
            )


def restore_projections(db: sqlite3.Connection, payload: dict[str, Any]) -> None:
    for table, records in payload.get("projections", {}).items():
        for row in records:
            keys = [r[1] for r in db.execute(f"PRAGMA table_info({table})") if r[5]]
            current = db.execute(
                f"SELECT data FROM {table} WHERE {' AND '.join(f'{k}=?' for k in keys)}",
                [row[k] for k in keys],
            ).fetchone()
            if current is None:
                db.execute(
                    f"INSERT INTO {table} ({', '.join(row)}) "
                    f"VALUES ({', '.join('?' for _ in row)})",
                    tuple(row.values()),
                )
            else:
                restored = json.loads(row["data"])
                current_data = json.loads(current[0])
                if "successor" in current_data:
                    restored["successor"] = current_data["successor"]
                db.execute(
                    f"UPDATE {table} SET data=? WHERE {' AND '.join(f'{k}=?' for k in keys)}",
                    (json.dumps(restored), *(row[k] for k in keys)),
                )


def restore_rows(db: sqlite3.Connection, tables: dict[str, list[dict[str, Any]]]) -> None:
    for table in (
        "messages",
        "files",
        "artifacts",
        "artifact_revisions",
        "memory_notes",
        "memory_events",
        "memory_tidy",
        "memory_note_tags",
        "memory_note_documents",
        "memory_server_sent",
        "interaction_surfaces",
        "interaction_actions",
        "agui_runs",
        "agui_events",
        "deleted_branches",
    ):
        for row in tables.get(table, []):
            row = dict(row)
            if table == "memory_events":
                row.pop("id", None)
            verb = "INSERT OR IGNORE" if table == "files" else "INSERT"
            db.execute(
                f"{verb} INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                tuple(row.values()),
            )
