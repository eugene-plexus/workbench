"""Exact recoverable records of attachment documents in a branch (storage code)."""

from __future__ import annotations

import base64
import sqlite3
from typing import Any

from .knowledge.index import PRIVATE, Index
from .store import _rewrite


async def snapshot(index: Index, files: set[str], notes: set[str]) -> dict[str, Any]:
    def go(db: sqlite3.Connection) -> dict[str, Any]:
        docs = [
            dict(r)
            for r in db.execute("SELECT * FROM documents")
            if r["source"] in {f"attachment:{fid}" for fid in files}
            or (r["id"] in notes and r["scope"] == "notes")
        ]
        ids = {r["id"] for r in docs}
        chunks = [dict(r) for r in db.execute("SELECT * FROM chunks") if r["doc_id"] in ids]
        for chunk in chunks:
            if chunk["vec"] is not None:
                chunk["vec"] = base64.b64encode(chunk["vec"]).decode("ascii")
        disk = {}
        for doc in docs:
            if doc["scope"] in PRIVATE:
                continue
            for path in (
                index.original_path(doc["id"]),
                index.text_path(doc["id"]),
                index.meta_path(doc["id"]),
            ):
                if path.is_file():
                    disk[path.relative_to(index.dir).as_posix()] = base64.b64encode(
                        path.read_bytes()
                    ).decode("ascii")
        return {"documents": docs, "chunks": chunks, "files": disk}

    return await index._run(go)


async def remove(index: Index, payload: dict[str, Any]) -> None:
    await index.delete_notes(
        [r["id"] for r in payload.get("documents", []) if r["scope"] == "notes"]
    )
    await index.delete_many(
        [r["id"] for r in payload.get("documents", []) if r["scope"] not in PRIVATE]
    )

    def clean(db: sqlite3.Connection) -> None:
        for doc in payload.get("documents", []):
            if doc["scope"] in PRIVATE:
                continue
            for path in index._files_of(doc["id"]):
                path.unlink(missing_ok=True)
                (index.dir / "trash" / path.name).unlink(missing_ok=True)
        _rewrite(db)

    await index._run(clean)


async def restore(index: Index, payload: dict[str, Any]) -> None:
    def go(db: sqlite3.Connection) -> None:
        def body() -> None:
            restored = set()
            for table in ("documents", "chunks"):
                for original in payload.get(table, []):
                    row = dict(original)
                    if table == "documents":
                        if db.execute(
                            "SELECT 1 FROM documents WHERE id=?", (row["id"],)
                        ).fetchone():
                            continue
                        restored.add(row["id"])
                    if table == "chunks":
                        if row["doc_id"] not in restored:
                            continue
                        # Chunk ids may have been reused by a rebuilt index. References
                        # outside this document use its id and locator, never these ids.
                        if db.execute("SELECT 1 FROM chunks WHERE id=?", (row["id"],)).fetchone():
                            row.pop("id", None)
                        if row["vec"] is not None:
                            row["vec"] = base64.b64decode(row["vec"])
                    db.execute(
                        f"INSERT INTO {table} ({', '.join(row)}) "
                        f"VALUES ({', '.join('?' for _ in row)})",
                        tuple(row.values()),
                    )
            for relative, content in payload.get("files", {}).items():
                target = (index.dir / relative).resolve()
                if not target.is_relative_to(index.dir.resolve()):
                    raise ValueError("a document path left its index")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(content))

        index._transaction(db, body)

    await index._run(go)
