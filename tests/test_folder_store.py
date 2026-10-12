"""The folders a person's documents are read from, as the main store keeps them (MR7, schema 13): one
row for each person and grant, filtered by the person in every query, with the rules the person chose
and what the last check found. The files read from a folder are not here: they are documents in the
person's own index.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from eugene_plexus_workbench.store import FolderRecord, Store


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    found = Store(tmp_path / "workbench.sqlite3")
    await found.open()
    try:
        yield found
    finally:
        await found.close()


def record(
    owner: str = "p-ada", grant: str = "g1", added: float = 1.0, **more: object
) -> FolderRecord:
    return FolderRecord(owner, grant, "Work notes", added, **more)  # type: ignore[arg-type]


async def test_a_folder_is_kept_with_its_rules_and_what_the_last_check_found(store: Store) -> None:
    kept = record(
        ignore=["*.log", "/build/"],
        include_hidden=True,
        use_gitignore=False,
        report={"files": 3, "left": [{"path": "a.log"}]},
        problem={"code": "gone", "message": "It is not there.", "at": 5.0},
        synced_at=7.0,
        checked_at=8.0,
    )
    assert await store.add_folder_record(kept) is True
    assert await store.folder_record("p-ada", "g1") == kept


async def test_the_same_person_cannot_have_a_folder_twice_and_the_first_is_kept(
    store: Store,
) -> None:
    assert await store.add_folder_record(record(ignore=["*.log"])) is True
    assert await store.add_folder_record(record(ignore=["*.tmp"], include_hidden=True)) is False
    found = await store.folder_record("p-ada", "g1")
    assert found is not None and found.ignore == ["*.log"] and found.include_hidden is False


async def test_two_people_can_have_the_same_grant_each_their_own_folder(store: Store) -> None:
    assert await store.add_folder_record(record("p-ada", ignore=["*.log"])) is True
    assert await store.add_folder_record(record("p-bo", ignore=["*.tmp"])) is True
    ada, bo = await store.folder_record("p-ada", "g1"), await store.folder_record("p-bo", "g1")
    assert ada is not None and ada.ignore == ["*.log"]
    assert bo is not None and bo.ignore == ["*.tmp"]


async def test_a_folder_is_found_changed_and_removed_by_the_person_it_is_of_only(
    store: Store,
) -> None:
    await store.add_folder_record(record("p-ada"))
    await store.add_folder_record(record("p-bo", "g2"))
    assert await store.folder_record("p-bo", "g1") is None
    assert await store.update_folder_record("p-bo", "g1", include_hidden=True) is None
    assert await store.delete_folder_record("p-bo", "g1") is False
    ada = await store.folder_record("p-ada", "g1")
    assert ada is not None and ada.include_hidden is False, "Bo's change reached nothing of Ada's"
    assert await store.delete_folder_record("p-ada", "g1") is True
    assert await store.folder_record("p-ada", "g1") is None
    assert await store.folder_record("p-bo", "g2") is not None, "and Bo's own is as it was"


async def test_only_the_fields_of_a_folder_can_be_changed_and_only_those_are(
    store: Store,
) -> None:
    await store.add_folder_record(record(ignore=["*.log"]))
    with pytest.raises(ValueError, match="not folder fields"):
        await store.update_folder_record("p-ada", "g1", added_at=5.0)
    with pytest.raises(ValueError, match="not folder fields"):
        await store.update_folder_record("p-ada", "g1", bogus=1)
    changed = await store.update_folder_record(
        "p-ada", "g1", status="withdrawn", problem={"code": "withdrawn", "message": "m", "at": 2.0}
    )
    assert changed is not None and changed.status == "withdrawn"
    assert changed.problem == {"code": "withdrawn", "message": "m", "at": 2.0}
    assert changed.ignore == ["*.log"], "what was not named is as it was"
    cleared = await store.update_folder_record("p-ada", "g1", problem=None, report=None)
    assert cleared is not None and cleared.problem is None and cleared.report is None


async def test_folders_are_listed_oldest_first_for_one_person_or_for_everyone(
    store: Store,
) -> None:
    await store.add_folder_record(record("p-ada", "g2", added=2.0))
    await store.add_folder_record(record("p-bo", "g3", added=3.0))
    await store.add_folder_record(record("p-ada", "g1", added=1.0))
    assert [r.grant_id for r in await store.folder_records("p-ada")] == ["g1", "g2"]
    assert [r.grant_id for r in await store.folder_records("p-bo")] == ["g3"]
    assert [(r.owner, r.grant_id) for r in await store.folder_records()] == [
        ("p-ada", "g1"),
        ("p-ada", "g2"),
        ("p-bo", "g3"),
    ]
    assert await store.folder_records("nobody") == []
