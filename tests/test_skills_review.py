"""Row 32's second-model review: each finding, as a test that failed before its fix.

1. A purge waits for a skill write in flight, and every write refuses a person whose removal has
   begun, so nothing of theirs is made after the purge says it is done.
2. Content and the switch go together: replacing an enabled skill never shows its new words while
   it is on, a stop before the row is written leaves the skill as it was, and an edit that cannot
   write its file changes nothing.
3. A name with a NUL or a DEL in the archive is refused, not cut short.
4. Local and central headers that disagree, and a broken Deflate stream, are refused per entry.
5. "Only these" picks first and bounds after.
6. A skill deleted while a file of it is read says it was removed, as the design says.
7. Reading is anchored to the storage root: a skill folder that is a link leads nowhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import struct
import sys
import time
import zipfile
import zlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from eugene_plexus_workbench import files
from eugene_plexus_workbench import skills as skills_module
from eugene_plexus_workbench.removal import Removal
from eugene_plexus_workbench.skills import (
    READ_SKILL_FILE,
    USE_SKILL,
    Refused,
    Skills,
    Unpacked,
    compose,
    parse,
    read_archive,
    read_upload,
)
from eugene_plexus_workbench.store import Chat, Person, Store

ADA = Person(sub="p-ada", name="Ada", role="member")
OWNER = Person(sub="operator", name="Owner", role="operator")


def _md(name: str = "pdf-forms", body: str = "Fill the form. kqzvone") -> bytes:
    return f"---\nname: {name}\ndescription: Fill in PDF forms.\n---\n\n{body}\n".encode()


def _zip(*entries: tuple[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=compression) as made:
        for name, data in entries:
            made.writestr(name, data)
    return out.getvalue()


def _written(name: str, body: str = "Do it.") -> Unpacked:
    data = compose({"name": name, "description": f"The {name} skill."}, body)
    return Unpacked(parse(data), data)


def _chat(settings: dict[str, Any] | None = None) -> Chat:
    return Chat(
        id="c1",
        owner=ADA.sub,
        title="t",
        model=None,
        created_at=0,
        updated_at=0,
        settings=settings or {},
    )


async def _open(tmp_path: Path) -> tuple[Store, Skills]:
    store = Store(tmp_path / "workbench.sqlite3")
    await store.open()
    await store.upsert_person(ADA)
    await store.upsert_person(OWNER)
    return store, Skills(store, tmp_path)


def _run(test: Any, tmp_path: Path) -> None:
    async def go() -> None:
        store, skills = await _open(tmp_path)
        try:
            await test(store, skills)
        finally:
            await store.close()

    asyncio.run(go())


async def _hide(store: Store, sub: str = ADA.sub) -> None:
    now = time.time()
    assert await store.hide_people([(sub, "Ada", "hidden", now, now)]) == [sub]


# --------------------------------------------------------------------------- #
# 1. removal and writes in flight
# --------------------------------------------------------------------------- #


def test_a_purge_waits_for_an_upload_in_flight_and_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def test(store: Store, skills: Skills) -> None:
        entered, gate = asyncio.Event(), asyncio.Event()
        checked = skills._check_room

        async def held(*args: Any, **kwargs: Any) -> None:
            await checked(*args, **kwargs)
            entered.set()
            await gate.wait()

        monkeypatch.setattr(skills, "_check_room", held)
        upload = asyncio.create_task(
            skills.add(ADA, read_upload("SKILL.md", _md()), source="upload")
        )
        await asyncio.wait_for(entered.wait(), 5)
        await _hide(store)
        assert await store.put_removed(ADA.sub, only_from=("hidden",), state="purging")
        removal = Removal(store, None, tmp_path)  # type: ignore[arg-type]
        removal.skills = skills
        purge = asyncio.create_task(removal.purge(ADA.sub))
        # Let the purge go as far as it can while the upload waits after its room check.
        await asyncio.wait({purge}, timeout=2)
        gate.set()
        with contextlib.suppress(Refused):
            await asyncio.wait_for(upload, 10)
        await asyncio.wait_for(purge, 10)
        row = await store.removed_person(ADA.sub)
        assert row is not None and row["state"] == "purged", row
        assert not (tmp_path / "skills" / files.person_key(ADA.sub)).exists()
        assert await skills.db.every() == []

    _run(test, tmp_path)


def test_every_skill_write_refuses_a_person_whose_removal_has_begun(tmp_path: Path) -> None:
    async def test(store: Store, skills: Skills) -> None:
        own = await skills.add(ADA, _written("mine"), source="written", on=True)
        shared = await skills.add(OWNER, _written("house-style"), source="written", on=True)
        await skills.change(OWNER, shared.id, {"everyone": True})
        await _hide(store)
        with pytest.raises(Refused) as added:
            await skills.add(ADA, _written("another"), source="written", on=True)
        assert added.value.status == 410
        with pytest.raises(Refused) as turned:
            await skills.change(ADA, shared.id, {"on": False})
        assert turned.value.status == 410
        # Should a write reach the store all the same, the store refuses it too.
        assert await skills.db.set_off(ADA.sub, shared.id, True) is not None
        assert await skills.db.put(replace(own, id="other0000", name="other")) is not None
        assert await skills.db.off(ADA.sub) == set()

    _run(test, tmp_path)


# --------------------------------------------------------------------------- #
# 2. content and the switch go together
# --------------------------------------------------------------------------- #


def test_replacing_an_enabled_skill_never_shows_its_new_words_while_it_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def test(store: Store, skills: Skills) -> None:
        first = await skills.add(ADA, read_upload("SKILL.md", _md()), source="upload", on=True)
        listed = await skills.for_chat(ADA, _chat())
        entered, gate = asyncio.Event(), asyncio.Event()
        put = skills.db.put

        async def held(*args: Any, **kwargs: Any) -> Any:
            entered.set()
            await gate.wait()
            return await put(*args, **kwargs)

        monkeypatch.setattr(skills.db, "put", held)
        replacing = asyncio.create_task(
            skills.add(
                ADA,
                read_upload("SKILL.md", _md(body="Unread words. kqzvtwo")),
                source="upload",
                on_conflict="replace",
            )
        )
        await asyncio.wait_for(entered.wait(), 5)
        _, text = await skills.run(ADA, listed, USE_SKILL, {"name": "pdf-forms"}, [])
        assert "kqzvone" in text and "kqzvtwo" not in text, text
        gate.set()
        replaced = await asyncio.wait_for(replacing, 10)
        assert replaced.id == first.id and not replaced.enabled
        state, text = await skills.run(ADA, listed, USE_SKILL, {"name": "pdf-forms"}, [])
        assert (state, "kqzvtwo" in text) == ("done", False), text
        assert "turned off" in text

    _run(test, tmp_path)


def test_a_stop_before_the_row_is_written_leaves_the_skill_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def test(store: Store, skills: Skills) -> None:
        await skills.add(ADA, read_upload("SKILL.md", _md()), source="upload", on=True)

        async def stopped(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("Workbench stopped here")

        # A stop: the row is never written, and nothing is tidied up after it.
        monkeypatch.setattr(skills.db, "put", stopped)
        monkeypatch.setattr(skills_module, "_remove", lambda _path: None)
        with pytest.raises((RuntimeError, Refused)):
            await skills.add(
                ADA,
                read_upload("SKILL.md", _md(body="Unread words. kqzvtwo")),
                source="upload",
                on_conflict="replace",
            )
        monkeypatch.undo()
        restarted = Skills(store, tmp_path)
        await restarted.reconcile()
        listed = await restarted.for_chat(ADA, _chat())
        state, text = await restarted.run(ADA, listed, USE_SKILL, {"name": "pdf-forms"}, [])
        assert state == "done" and "kqzvone" in text and "kqzvtwo" not in text, text
        assert len(list((tmp_path / "skills" / files.person_key(ADA.sub)).iterdir())) == 1

    _run(test, tmp_path)


def test_an_edit_that_cannot_write_its_file_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def test(store: Store, skills: Skills) -> None:
        made = await skills.add(ADA, _written("notes", "Keep notes."), source="written", on=True)
        before, _ = await skills.read(made)

        def full(*args: Any, **kwargs: Any) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(skills_module, "_write", full)
        monkeypatch.setattr(skills_module, "_replace_file", full, raising=False)
        with pytest.raises((Refused, OSError)):
            await skills.change(ADA, made.id, {"description": "New words."})
        monkeypatch.undo()
        after = await skills.db.get(made.id)
        assert after is not None and after.description == "The notes skill."
        assert (await skills.read(after))[0] == before

    _run(test, tmp_path)


# --------------------------------------------------------------------------- #
# 3. and 4. what the archive says of itself
# --------------------------------------------------------------------------- #


def test_a_name_with_a_nul_or_a_del_is_refused_not_cut_short() -> None:
    nul = _zip(("SKILL.md", _md()), ("safe@../outside", b"x")).replace(
        b"safe@../outside", b"safe\x00../outside"
    )
    assert zipfile.ZipFile(io.BytesIO(nul)).namelist()[1] == "safe"  # what zipfile makes of it
    with pytest.raises(Refused) as refused:
        read_archive(nul)
    assert "`safe?../outside`" in refused.value.message
    with pytest.raises(Refused) as deleted:
        read_archive(_zip(("SKILL.md", _md()), ("notes\x7f.md", b"x")))
    assert "`notes?.md`" in deleted.value.message and "control" in deleted.value.message


def _central(data: bytearray, name: bytes) -> int:
    """Where the central directory entry of `name` starts."""
    at = data.find(b"PK\x01\x02")
    while at >= 0:
        length = struct.unpack_from("<H", data, at + 28)[0]
        if bytes(data[at + 46 : at + 46 + length]) == name:
            return at
        at = data.find(b"PK\x01\x02", at + 4)
    raise AssertionError(name)


def test_headers_that_disagree_and_broken_deflate_are_refused_by_entry() -> None:
    stored = bytearray(
        _zip(("SKILL.md", _md()), ("notes.md", b"X" + b"Y" * 99), compression=zipfile.ZIP_STORED)
    )
    at = _central(stored, b"notes.md")
    struct.pack_into("<III", stored, at + 16, zlib.crc32(b"X"), 1, 1)
    # Python's reader goes by the central directory, and finds one byte.
    assert zipfile.ZipFile(io.BytesIO(bytes(stored))).read("notes.md") == b"X"
    with pytest.raises(Refused) as disagree:
        read_archive(bytes(stored))
    assert "`notes.md`" in disagree.value.message, disagree.value.message

    deflated = bytearray(_zip(("SKILL.md", _md()), ("notes.md", b"Some notes. " * 40)))
    at = _central(deflated, b"notes.md")
    local = struct.unpack_from("<I", deflated, at + 42)[0]
    name_len, extra_len = struct.unpack_from("<HH", deflated, local + 26)
    deflated[local + 30 + name_len + extra_len] = 0x07  # a final block of the reserved type
    with pytest.raises(Refused) as broken:
        read_archive(bytes(deflated))
    assert "`notes.md`" in broken.value.message and "damaged" in broken.value.message


def test_archives_that_tools_really_make_still_read() -> None:
    """The header checks keep what zip tools make: Python's, and a streamed archive whose local
    headers defer their sizes to a data descriptor (bit 3), as Java's and macOS's do."""
    plain = read_archive(_zip(("SKILL.md", _md()), ("references/a.md", b"A" * 300)))
    assert plain.members == {"references/a.md": b"A" * 300}
    out = io.BytesIO()
    with zipfile.ZipFile(_Unseekable(out), "w", compression=zipfile.ZIP_DEFLATED) as made:
        made.writestr("SKILL.md", _md())
        with made.open("references/a.md", "w") as handle:
            handle.write(b"B" * 300)
    streamed = out.getvalue()
    assert zipfile.ZipFile(io.BytesIO(streamed)).getinfo("references/a.md").flag_bits & 0x08
    assert read_archive(streamed).members == {"references/a.md": b"B" * 300}


class _Unseekable(io.RawIOBase):
    """A file `zipfile` cannot seek in, so it writes data descriptors, as a stream does."""

    def __init__(self, target: io.BytesIO) -> None:
        self.target = target

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        return self.target.write(data)

    def flush(self) -> None:
        self.target.flush()


# --------------------------------------------------------------------------- #
# 5. to 7. what a chat lists and reads
# --------------------------------------------------------------------------- #


def test_only_these_picks_first_and_bounds_after(tmp_path: Path) -> None:
    async def test(store: Store, skills: Skills) -> None:
        made = [
            await skills.add(ADA, _written(f"skill-{n:02d}"), source="written", on=True)
            for n in range(33)
        ]
        last = max(made, key=lambda s: s.name)
        assert last.id not in {s.id for s in await skills.for_chat(ADA, _chat())}
        picked = await skills.for_chat(ADA, _chat({"skills": [last.id]}))
        assert [s.name for s in picked] == ["skill-32"]

    _run(test, tmp_path)


def test_a_skill_deleted_while_a_file_is_read_says_it_was_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def test(store: Store, skills: Skills) -> None:
        upload = _zip(("SKILL.md", _md()), ("references/style.md", b"Sentence case."))
        await skills.add(ADA, read_upload("s.zip", upload), source="upload", on=True)
        listed = await skills.for_chat(ADA, _chat())
        get = skills.db.get
        calls = 0

        async def then_deleted(skill_id: str) -> Any:
            nonlocal calls
            calls += 1
            found = await get(skill_id)
            if calls == 1 and found is not None:
                # The person deletes it between the row being read and its file.
                await skills.db.delete(skill_id)
                skills_module._remove(skills.folder(found))
            return found

        monkeypatch.setattr(skills.db, "get", then_deleted)
        state, text = await skills.run(
            ADA, listed, READ_SKILL_FILE, {"name": "pdf-forms", "path": "references/style.md"}, []
        )
        assert (state, text) == ("done", "The skill pdf-forms was removed. Answer without it.")

    _run(test, tmp_path)


def _link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_reading_is_anchored_to_the_storage_root(tmp_path: Path) -> None:
    async def test(store: Store, skills: Skills) -> None:
        upload = _zip(("SKILL.md", _md()), ("references/style.md", b"Sentence case."))
        made = await skills.add(ADA, read_upload("s.zip", upload), source="upload", on=True)
        listed = await skills.for_chat(ADA, _chat())
        outside = tmp_path / "elsewhere"
        (outside / "references").mkdir(parents=True)
        (outside / "SKILL.md").write_bytes(_md(body="kqzvoutside"))
        (outside / "references" / "style.md").write_bytes(b"kqzvoutside")
        folder = skills.folder(made)
        skills_module._remove(folder)
        _link(folder, outside)
        for tool, arguments in (
            (USE_SKILL, {"name": "pdf-forms"}),
            (READ_SKILL_FILE, {"name": "pdf-forms", "path": "references/style.md"}),
        ):
            _, text = await skills.run(ADA, listed, tool, arguments, [])
            assert "kqzvoutside" not in text, (tool, text)
        if sys.platform == "win32":
            os.rmdir(folder)  # a junction, gone without touching what it points to
        assert (outside / "SKILL.md").is_file()

    _run(test, tmp_path)
