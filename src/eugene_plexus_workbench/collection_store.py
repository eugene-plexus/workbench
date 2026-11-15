"""Team knowledge in the store (row 47, `specs/docs/design/rows/row-47.md`): collections, who
may read or add to each, what each holds and who added it, which chats and projects use one,
and the people and groups a project is shared with.

Only this helper and `Store` touch these tables, on the store's one thread and connection.

* `collections`: a collection's name and what it belongs to (`belongs_to`): `install`, made by
  the owner of this Workbench, or `project:<id>`, a shared project's own knowledge. Its documents
  and notes are in an index of its own (`knowledge/<key of collection:<id>>/`), never a person's.
* `collection_grants`: who may use an install collection, a person (`person`, their `sub`) or a
  permission group (`group`, its id), to `read` it (their chats search it) or `contribute` (they
  add documents and notes too). A person's grants go with them (`PERSON_TABLES`).
* `collection_items`: each document and note, who added it (`added_by`), and for a note its
  words. A note a model proposed waits as `proposed` until a contributor accepts it. When the
  person who added an item is removed from Eugene, the item stays and `added_by` becomes NULL:
  "a former member" (call R47-LEAVE). What they proposed and nobody accepted goes with them.
* `collection_links`: the collections a person chose for one of their chats (`chat:<id>`) or
  projects (`project:<id>`). The person's own choice, and theirs (`PERSON_TABLES`).
* `project_shares`: the people and groups a project is shared with, each `read` or `contribute`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import Store

#: Row 47's one migration, and the one place its number is said.
COLLECTIONS_SCHEMA = 35

ROLES = ("read", "contribute")
KINDS = ("person", "group")


def migration(db: sqlite3.Connection) -> None:
    """Schema 35 (row 47): five new tables; nothing that was there changes."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS collections ("
        "id TEXT PRIMARY KEY, "
        "name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80), "
        "belongs_to TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, "
        "outside INTEGER NOT NULL DEFAULT 0 CHECK (outside IN (0, 1)))"
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS collections_of_projects ON collections (belongs_to) "
        "WHERE belongs_to LIKE 'project:%'"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS collection_grants ("
        "collection_id TEXT NOT NULL, "
        "kind TEXT NOT NULL CHECK (kind IN ('person', 'group')), "
        "subject TEXT NOT NULL, "
        "role TEXT NOT NULL CHECK (role IN ('read', 'contribute')), "
        "PRIMARY KEY (collection_id, kind, subject))"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS collection_grants_by_subject "
        "ON collection_grants (kind, subject)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS collection_items ("
        "id TEXT PRIMARY KEY, collection_id TEXT NOT NULL, "
        "kind TEXT NOT NULL CHECK (kind IN ('document', 'note')), "
        "state TEXT NOT NULL CHECK (state IN ('active', 'proposed')), "
        "added_by TEXT, model TEXT, added_at REAL NOT NULL, "
        "title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '')"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS collection_items_by_collection "
        "ON collection_items (collection_id, state)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS collection_links ("
        "owner TEXT NOT NULL, target TEXT NOT NULL, collection_id TEXT NOT NULL, "
        "PRIMARY KEY (owner, target, collection_id))"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS collection_links_by_collection "
        "ON collection_links (collection_id)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS project_shares ("
        "project_id TEXT NOT NULL, "
        "kind TEXT NOT NULL CHECK (kind IN ('person', 'group')), "
        "subject TEXT NOT NULL, "
        "role TEXT NOT NULL CHECK (role IN ('read', 'contribute')), "
        "PRIMARY KEY (project_id, kind, subject))"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS project_shares_by_subject ON project_shares (kind, subject)"
    )


def purge_rows(db: sqlite3.Connection, sub: str, have: dict[str, set[str]]) -> None:
    """Row 7's purge, for row 47's tables, in its open transaction, before `PERSON_TABLES`
    (which deletes the person's grants, shares and links by their `subject` and `owner`):

    * what they proposed and nobody accepted goes; what they added stays, by "a former member";
    * their projects' shares, and their projects' own collections with everything in them (the
      index directories go in the removal's `collections` step, which runs first)."""
    if "collection_items" in have:
        db.execute("DELETE FROM collection_items WHERE added_by = ? AND state = 'proposed'", (sub,))
        db.execute("UPDATE collection_items SET added_by = NULL WHERE added_by = ?", (sub,))
    if "projects" not in have or "project_shares" not in have:
        return
    projects = [r[0] for r in db.execute("SELECT id FROM projects WHERE owner = ?", (sub,))]
    for start in range(0, len(projects), 400):
        part = projects[start : start + 400]
        marks = ", ".join("?" for _ in part)
        db.execute(f"DELETE FROM project_shares WHERE project_id IN ({marks})", part)
        if "memory_snapshot" in have.get("chats", set()):
            # Other people's chats in them were sent the person's instructions: not again.
            db.execute(
                f"UPDATE chats SET memory_snapshot = NULL WHERE project_id IN ({marks})", part
            )
        if "collections" in have:
            owners = [f"project:{p}" for p in part]
            ids = [
                r[0]
                for r in db.execute(
                    f"SELECT id FROM collections WHERE belongs_to IN ({marks})", owners
                )
            ]
            _drop(db, ids)


def _drop(db: sqlite3.Connection, ids: Sequence[str]) -> None:
    if not ids:
        return
    marks = ", ".join("?" for _ in ids)
    for table in ("collection_items", "collection_grants", "collection_links"):
        db.execute(f"DELETE FROM {table} WHERE collection_id IN ({marks})", list(ids))
    db.execute(f"DELETE FROM collections WHERE id IN ({marks})", list(ids))


@dataclass
class Collection:
    id: str
    name: str
    #: `install`, or `project:<id>` for a shared project's own knowledge.
    belongs_to: str
    created_at: float
    updated_at: float
    #: Whether its passages may go to a model that runs outside the install's machines (a cloud
    #: account): its manager's word, off until they say so.
    outside: bool = False

    @property
    def project_id(self) -> str | None:
        return (
            self.belongs_to[len("project:") :] if self.belongs_to.startswith("project:") else None
        )


@dataclass
class Grant:
    kind: str
    subject: str
    role: str


@dataclass
class Item:
    id: str
    collection_id: str
    kind: str
    state: str
    added_by: str | None
    added_at: float
    title: str
    body: str = ""
    model: str | None = None


def _collection(row: sqlite3.Row) -> Collection:
    return Collection(
        row["id"],
        row["name"],
        row["belongs_to"],
        row["created_at"],
        row["updated_at"],
        bool(row["outside"]),
    )


def _item(row: sqlite3.Row) -> Item:
    return Item(
        id=row["id"],
        collection_id=row["collection_id"],
        kind=row["kind"],
        state=row["state"],
        added_by=row["added_by"],
        added_at=row["added_at"],
        title=row["title"],
        body=row["body"],
        model=row["model"],
    )


def _in_groups(groups: Iterable[str]) -> tuple[str, list[str]]:
    ids = sorted(set(groups))
    return ", ".join("?" for _ in ids), ids


class CollectionStore:
    """Row 47's SQL, on the store's one thread."""

    def __init__(self, store: Store) -> None:
        self.store = store

    async def _run[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        def go() -> T:
            return fn(self.store._conn())

        return await self.store._run(go)

    async def _write[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        def go() -> T:
            db = self.store._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                out = fn(db)
                db.execute("COMMIT")
                return out
            except BaseException:
                db.execute("ROLLBACK")
                raise

        return await self.store._run(go)

    # --- collections -----------------------------------------------------------

    async def all(self) -> list[Collection]:
        return await self._run(
            lambda db: [
                _collection(r)
                for r in db.execute("SELECT * FROM collections ORDER BY name COLLATE NOCASE, id")
            ]
        )

    async def get(self, collection_id: str) -> Collection | None:
        def go(db: sqlite3.Connection) -> Collection | None:
            row = db.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
            return _collection(row) if row is not None else None

        return await self._run(go)

    async def of_project(self, project_id: str) -> Collection | None:
        def go(db: sqlite3.Connection) -> Collection | None:
            row = db.execute(
                "SELECT * FROM collections WHERE belongs_to = ?", (f"project:{project_id}",)
            ).fetchone()
            return _collection(row) if row is not None else None

        return await self._run(go)

    async def add(self, collection: Collection) -> bool:
        """Keep a new collection. False when the install has one of that name already, or the
        project has its own already."""

        def go(db: sqlite3.Connection) -> bool:
            if (
                collection.belongs_to == "install"
                and db.execute(
                    "SELECT 1 FROM collections WHERE belongs_to = 'install' AND name = ? "
                    "COLLATE NOCASE",
                    (collection.name,),
                ).fetchone()
            ):
                return False
            try:
                db.execute(
                    "INSERT INTO collections (id, name, belongs_to, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        collection.id,
                        collection.name,
                        collection.belongs_to,
                        collection.created_at,
                        collection.updated_at,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
            return True

        return await self._write(go)

    async def set_outside(self, collection_id: str, allowed: bool, now: float) -> None:
        await self._write(
            lambda db: db.execute(
                "UPDATE collections SET outside = ?, updated_at = ? WHERE id = ?",
                (int(allowed), now, collection_id),
            )
        )

    async def rename(self, collection_id: str, name: str, now: float) -> bool:
        def go(db: sqlite3.Connection) -> bool:
            row = db.execute(
                "SELECT belongs_to FROM collections WHERE id = ?", (collection_id,)
            ).fetchone()
            if row is None:
                return False
            if (
                row["belongs_to"] == "install"
                and db.execute(
                    "SELECT 1 FROM collections WHERE belongs_to = 'install' AND name = ? "
                    "COLLATE NOCASE AND id != ?",
                    (name, collection_id),
                ).fetchone()
            ):
                return False
            db.execute(
                "UPDATE collections SET name = ?, updated_at = ? WHERE id = ?",
                (name, now, collection_id),
            )
            return True

        return await self._write(go)

    async def drop(self, collection_id: str) -> None:
        """The collection's rows, all of them, in one transaction (its index goes first)."""
        await self._write(lambda db: _drop(db, [collection_id]))

    # --- grants ----------------------------------------------------------------

    async def grants(self, collection_id: str) -> list[Grant]:
        return await self._run(
            lambda db: [
                Grant(r["kind"], r["subject"], r["role"])
                for r in db.execute(
                    "SELECT kind, subject, role FROM collection_grants WHERE collection_id = ? "
                    "ORDER BY kind, subject",
                    (collection_id,),
                )
            ]
        )

    async def set_grants(self, collection_id: str, grants: Sequence[Grant]) -> None:
        """Replace who may use a collection, in one step."""

        def go(db: sqlite3.Connection) -> None:
            db.execute("DELETE FROM collection_grants WHERE collection_id = ?", (collection_id,))
            db.executemany(
                "INSERT INTO collection_grants (collection_id, kind, subject, role) "
                "VALUES (?, ?, ?, ?)",
                [(collection_id, g.kind, g.subject, g.role) for g in grants],
            )

        await self._write(go)

    async def granted(self, sub: str, groups: Iterable[str]) -> dict[str, str]:
        """Each install collection granted to this person, or to a group they are in now, with
        the most it allows (`contribute` before `read`)."""
        marks, ids = _in_groups(groups)

        def go(db: sqlite3.Connection) -> dict[str, str]:
            clause = "(kind = 'person' AND subject = ?)"
            params: list[Any] = [sub]
            if ids:
                clause += f" OR (kind = 'group' AND subject IN ({marks}))"
                params += ids
            found: dict[str, str] = {}
            for row in db.execute(
                f"SELECT collection_id, role FROM collection_grants WHERE {clause}", params
            ):
                if found.get(row["collection_id"]) != "contribute":
                    found[row["collection_id"]] = row["role"]
            return found

        return await self._run(go)

    # --- items -----------------------------------------------------------------

    async def items(self, collection_id: str, state: str | None = "active") -> list[Item]:
        def go(db: sqlite3.Connection) -> list[Item]:
            sql = "SELECT * FROM collection_items WHERE collection_id = ?"
            params: list[Any] = [collection_id]
            if state is not None:
                sql += " AND state = ?"
                params.append(state)
            return [_item(r) for r in db.execute(sql + " ORDER BY added_at DESC, id", params)]

        return await self._run(go)

    async def item(self, collection_id: str, item_id: str) -> Item | None:
        def go(db: sqlite3.Connection) -> Item | None:
            row = db.execute(
                "SELECT * FROM collection_items WHERE collection_id = ? AND id = ?",
                (collection_id, item_id),
            ).fetchone()
            return _item(row) if row is not None else None

        return await self._run(go)

    async def add_item(self, item: Item) -> None:
        await self._write(
            lambda db: db.execute(
                "INSERT OR REPLACE INTO collection_items (id, collection_id, kind, state, "
                "added_by, model, added_at, title, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.id,
                    item.collection_id,
                    item.kind,
                    item.state,
                    item.added_by,
                    item.model,
                    item.added_at,
                    item.title,
                    item.body,
                ),
            )
        )

    async def count_proposed(self, collection_id: str, by: str) -> int:
        return await self._run(
            lambda db: int(
                db.execute(
                    "SELECT COUNT(*) FROM collection_items WHERE collection_id = ? "
                    "AND state = 'proposed' AND added_by = ?",
                    (collection_id, by),
                ).fetchone()[0]
            )
        )

    async def accept(self, collection_id: str, item_id: str, by: str, now: float) -> bool:
        """A proposed note becomes a note of the collection, by the contributor who accepted it."""
        return await self._write(
            lambda db: bool(
                db.execute(
                    "UPDATE collection_items SET state = 'active', added_by = ?, added_at = ? "
                    "WHERE collection_id = ? AND id = ? AND state = 'proposed'",
                    (by, now, collection_id, item_id),
                ).rowcount
            )
        )

    async def drop_item(self, collection_id: str, item_id: str) -> bool:
        return await self._write(
            lambda db: bool(
                db.execute(
                    "DELETE FROM collection_items WHERE collection_id = ? AND id = ?",
                    (collection_id, item_id),
                ).rowcount
            )
        )

    async def counts(self) -> dict[str, tuple[int, int, int]]:
        """Each collection's `(documents, notes, proposed)`."""

        def go(db: sqlite3.Connection) -> dict[str, tuple[int, int, int]]:
            found: dict[str, list[int]] = {}
            for row in db.execute(
                "SELECT collection_id, kind, state, COUNT(*) AS n FROM collection_items "
                "GROUP BY collection_id, kind, state"
            ):
                held = found.setdefault(row["collection_id"], [0, 0, 0])
                if row["state"] == "proposed":
                    held[2] += row["n"]
                else:
                    held[0 if row["kind"] == "document" else 1] += row["n"]
            return {k: (v[0], v[1], v[2]) for k, v in found.items()}

        return await self._run(go)

    # --- links: the collections a person chose for a chat or a project ---------

    async def links(self, owner: str, targets: Sequence[str]) -> list[str]:
        """The collections this person chose for any of these chats or projects, each once."""
        if not targets:
            return []
        marks = ", ".join("?" for _ in targets)

        def go(db: sqlite3.Connection) -> list[str]:
            return list(
                dict.fromkeys(
                    r[0]
                    for r in db.execute(
                        f"SELECT collection_id FROM collection_links WHERE owner = ? AND "
                        f"target IN ({marks}) ORDER BY target, collection_id",
                        (owner, *targets),
                    )
                )
            )

        return await self._run(go)

    async def set_links(self, owner: str, target: str, collection_ids: Sequence[str]) -> None:
        def go(db: sqlite3.Connection) -> None:
            db.execute(
                "DELETE FROM collection_links WHERE owner = ? AND target = ?", (owner, target)
            )
            db.executemany(
                "INSERT INTO collection_links (owner, target, collection_id) VALUES (?, ?, ?)",
                [(owner, target, c) for c in dict.fromkeys(collection_ids)],
            )

        await self._write(go)

    async def drop_links(self, target: str) -> None:
        """A chat or project is gone: nobody's choice for it stays."""
        await self._write(
            lambda db: db.execute("DELETE FROM collection_links WHERE target = ?", (target,))
        )

    # --- a project's shares ----------------------------------------------------

    async def shares(self, project_id: str) -> list[Grant]:
        return await self._run(
            lambda db: [
                Grant(r["kind"], r["subject"], r["role"])
                for r in db.execute(
                    "SELECT kind, subject, role FROM project_shares WHERE project_id = ? "
                    "ORDER BY kind, subject",
                    (project_id,),
                )
            ]
        )

    async def set_shares(self, project_id: str, shares: Sequence[Grant]) -> None:
        def go(db: sqlite3.Connection) -> None:
            db.execute("DELETE FROM project_shares WHERE project_id = ?", (project_id,))
            db.executemany(
                "INSERT INTO project_shares (project_id, kind, subject, role) VALUES (?, ?, ?, ?)",
                [(project_id, g.kind, g.subject, g.role) for g in shares],
            )

        await self._write(go)

    async def shared_with(self, sub: str, groups: Iterable[str]) -> dict[str, str]:
        """Each project shared with this person, or a group they are in now, with the most it
        allows. Their own projects are not left out here: the caller does that."""
        marks, ids = _in_groups(groups)

        def go(db: sqlite3.Connection) -> dict[str, str]:
            clause = "(kind = 'person' AND subject = ?)"
            params: list[Any] = [sub]
            if ids:
                clause += f" OR (kind = 'group' AND subject IN ({marks}))"
                params += ids
            found: dict[str, str] = {}
            for row in db.execute(
                f"SELECT project_id, role FROM project_shares WHERE {clause}", params
            ):
                if found.get(row["project_id"]) != "contribute":
                    found[row["project_id"]] = row["role"]
            return found

        return await self._run(go)

    async def drop_shares(self, project_id: str) -> None:
        await self._write(
            lambda db: db.execute("DELETE FROM project_shares WHERE project_id = ?", (project_id,))
        )
