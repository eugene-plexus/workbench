"""Row 32: skills (`specs/docs/design/rows/row-32.md`).

A person adds a skill (a folder with `SKILL.md`, as a ZIP or one file) or writes one; the model
sees each skill's name and description in the `use_skill` tool, never in the system message, and
loads the instructions and files only when a request needs them. The owner can give a skill to
everyone, and each person may turn it off. The ZIP reader refuses a hostile archive with its
cause and writes nothing. A removed person's skills go with their data, and a download of a
person's data holds their skills.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import sqlite3
import stat
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from eugene_plexus_workbench import files
from eugene_plexus_workbench.store import SCHEMA_VERSION, Store

from .conftest import ADMIN_TOKEN, Browser, World

STYLE = "Write every heading in sentence case. kqzvstylemark"
BODY = "# Filling PDF forms\n\nRead references/style.md first, then fill each field. kqzvbodymark"


def _skill_md(
    name: str = "pdf-forms",
    description: str = "Fill in PDF forms.",
    body: str = BODY,
    extra: str = "",
) -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n{extra}---\n\n{body}\n".encode()


def _archive(
    *entries: tuple[str | zipfile.ZipInfo, bytes], compression: int = zipfile.ZIP_DEFLATED
) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=compression) as made:
        for name, data in entries:
            made.writestr(name, data)
    return out.getvalue()


def _good_zip(top: str = "pdf-forms/", name: str = "pdf-forms") -> bytes:
    return _archive(
        (f"{top}SKILL.md", _skill_md(name)),
        (f"{top}references/style.md", STYLE.encode()),
        (f"{top}scripts/fill.py", b"print('filled')\n"),
    )


def _signed_in(world: World, sub: str) -> Browser:
    browser = world.browser()
    done = browser.sign_in(sub)
    assert "#signin=" in done.headers.get("location", ""), done.text
    return browser


def _upload(person: Browser, data: bytes, name: str = "skill.zip", **form: str) -> Any:
    return person.post(
        "/api/skills", files={"file": (name, data, "application/octet-stream")}, data=form
    )


def _added(
    person: Browser, data: bytes, name: str = "skill.zip", *, on: bool = True
) -> dict[str, Any]:
    made = _upload(person, data, name)
    assert made.status_code == 201, made.text
    skill = dict(made.json())
    if on:
        turned = person.patch(f"/api/skills/{skill['id']}", json={"on": True})
        assert turned.status_code == 200, turned.text
        skill = dict(turned.json())
    return skill


def _ask(world: World, person: Browser, chat: str, words: str) -> dict[str, Any]:
    sent = person.post(f"/api/chats/{chat}/messages", json={"content": words})
    assert sent.status_code == 201, sent.text
    answer = person.wait_answer(chat)
    assert answer["status"] == "done", answer
    return dict(world.gateway.requests[-1])


def _system(request: dict[str, Any]) -> str | None:
    found = [m["content"] for m in request["messages"] if m["role"] == "system"]
    return str(found[0]) if found else None


def _tool(request: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next(
        (t["function"] for t in request.get("tools") or [] if t["function"]["name"] == name), None
    )


def _results(request: dict[str, Any]) -> list[str]:
    return [str(m["content"]) for m in request["messages"] if m["role"] == "tool"]


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")) if root.exists() else []


def _skills_dir(world: World, sub: str) -> Path:
    return world.data / "skills" / files.person_key(sub)


def _folder(owner_dir: Path, skill_id: str) -> Path:
    """A skill's one folder under its owner's: `<id>`, or `<id>.<revision>`."""
    found = [p for p in owner_dir.glob(f"{skill_id}*") if p.name.split(".")[0] == skill_id]
    assert len(found) == 1, found
    return found[0]


# --------------------------------------------------------------------------- #
# adding a skill
# --------------------------------------------------------------------------- #


def test_a_skill_zip_is_unpacked_and_listed(world: World) -> None:
    ada = _signed_in(world, "p-ada")
    made = _upload(ada, _good_zip())
    assert made.status_code == 201, made.text
    skill = made.json()
    # An upload is a stranger's words: it waits off until the person has read it.
    assert (skill["name"], skill["on"], skill["source"]) == ("pdf-forms", False, "upload")
    assert sorted(f["path"] for f in skill["files"]) == ["references/style.md", "scripts/fill.py"]
    folder = _folder(_skills_dir(world, "p-ada"), skill["id"])
    assert (folder / "SKILL.md").read_bytes() == _skill_md()
    assert (folder / "references" / "style.md").read_text(encoding="utf-8") == STYLE
    # Unpacked read-only, and nothing is left in the incoming folder.
    assert not (folder / "SKILL.md").stat().st_mode & stat.S_IWRITE
    assert [p.name for p in (world.data / "skills").iterdir()] == [files.person_key("p-ada")]
    # The page shows the whole SKILL.md before it is turned on.
    whole = ada.get(f"/api/skills/{skill['id']}").json()
    assert whole["skillMd"] == _skill_md().decode() and whole["hasScripts"] is True
    listed = ada.get("/api/skills").json()["skills"]
    assert [(s["name"], s["on"], s["listed"]) for s in listed] == [("pdf-forms", False, False)]
    ada.patch(f"/api/skills/{skill['id']}", json={"on": True}).raise_for_status()
    listed = ada.get("/api/skills").json()["skills"]
    assert [(s["name"], s["on"], s["listed"]) for s in listed] == [("pdf-forms", True, True)]
    request = _ask(world, ada, ada.new_chat(), "Fill my form.")
    tool = _tool(request, "use_skill")
    assert tool is not None and "- pdf-forms: Fill in PDF forms." in tool["description"]


def test_an_archive_made_on_a_mac_leaves_its_junk_out(world: World) -> None:
    ada = _signed_in(world, "p-ada")
    made = _upload(
        ada,
        _archive(
            ("pdf-forms/", b""),
            ("pdf-forms/SKILL.md", _skill_md()),
            ("pdf-forms/.DS_Store", b"\0\0\0\1Bud1"),
            ("__MACOSX/pdf-forms/._SKILL.md", b"\0\5\26\7"),
        ),
    )
    assert made.status_code == 201, made.text
    assert made.json()["files"] == []
    folder = _folder(_skills_dir(world, "p-ada"), made.json()["id"])
    assert [p.name for p in folder.iterdir()] == ["SKILL.md"]


def test_a_single_skill_md_is_a_skill(world: World) -> None:
    ada = _signed_in(world, "p-ada")
    skill = _added(ada, _skill_md("meeting-notes", "Write meeting notes."), "SKILL.md")
    assert (skill["name"], skill["files"], skill["on"]) == ("meeting-notes", [], True)
    # The same name again asks: Replace, or Keep both (the new one gets -2).
    again = _upload(ada, _skill_md("meeting-notes", "Newer notes."), "SKILL.md")
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["conflict"]["id"] == skill["id"]
    both = _upload(ada, _skill_md("meeting-notes", "Newer notes."), "SKILL.md", onConflict="both")
    assert both.status_code == 201 and both.json()["name"] == "meeting-notes-2", both.text
    replaced = _upload(
        ada, _skill_md("meeting-notes", "Replaced."), "SKILL.md", onConflict="replace"
    )
    assert replaced.status_code == 201 and replaced.json()["id"] == skill["id"], replaced.text
    assert replaced.json()["description"] == "Replaced." and replaced.json()["on"] is False
    # Written on the page: the form writes SKILL.md, and a file can be added.
    written = ada.post(
        "/api/skills/written",
        json={"name": "tidy-code", "description": "Tidy code.", "instructions": "Sort imports."},
    )
    assert written.status_code == 201, written.text
    assert written.json()["source"] == "written" and written.json()["on"] is True
    text = ada.get(f"/api/skills/{written.json()['id']}").json()["skillMd"]
    assert "name: tidy-code" in text and text.rstrip().endswith("Sort imports.")
    added = ada.post(
        f"/api/skills/{written.json()['id']}/files",
        files={"file": ("rules.md", b"No wildcard imports.", "text/markdown")},
        data={"path": "references/rules.md"},
    )
    assert added.status_code == 200, added.text
    assert [f["path"] for f in added.json()["files"]] == ["references/rules.md"]
    edited = ada.patch(
        f"/api/skills/{written.json()['id']}", json={"instructions": "Sort imports, then lint."}
    )
    assert edited.status_code == 200, edited.text
    assert (
        ada.get(f"/api/skills/{written.json()['id']}")
        .json()["skillMd"]
        .rstrip()
        .endswith("Sort imports, then lint.")
    )
    names = sorted(s["name"] for s in ada.get("/api/skills").json()["skills"])
    assert names == ["meeting-notes", "meeting-notes-2", "tidy-code"]


def _link(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


def _tenfold() -> bytes:
    """6 MiB that packs about ten to one: under the ratio rule, so the size rule is what refuses."""
    return b"".join(os.urandom(1024) + bytes(9 * 1024) for _ in range(615))


def _hostile() -> list[tuple[str, bytes, list[str]]]:
    """Each hostile archive, what it is, and the words its refusal must hold."""
    return [
        (
            "traversal",
            _archive(("SKILL.md", _skill_md()), ("../x.sh", b"rm -rf /")),
            ["`../x.sh`", "leaves the skill's folder"],
        ),
        (
            "backslash traversal",
            _archive(("SKILL.md", _skill_md()), ("scripts\\..\\..\\x.sh", b"x")),
            ["`scripts/../../x.sh`", "leaves the skill's folder"],
        ),
        (
            "absolute path",
            _archive(("SKILL.md", _skill_md()), ("/etc/cron.d/x", b"x")),
            ["`/etc/cron.d/x`", "leaves the skill's folder"],
        ),
        (
            "drive letter",
            _archive(("SKILL.md", _skill_md()), ("C:/Windows/x.dll", b"x")),
            ["`C:/Windows/x.dll`", "leaves the skill's folder"],
        ),
        (
            "link",
            _archive(("SKILL.md", _skill_md()), (_link("references/notes.md"), b"../../../secret")),
            ["`references/notes.md`", "is a link"],
        ),
        (
            "513 entries",
            _archive(("SKILL.md", _skill_md()), *((f"r/{i}.md", b"x") for i in range(512))),
            ["more than 512"],
        ),
        (
            "a 1,000-to-1 entry",
            _archive(("SKILL.md", _skill_md()), ("assets/zeros.bin", b"\0" * (1 << 20))),
            ["`assets/zeros.bin`", "more than 100 times"],
        ),
        (
            "over 20 MiB unpacked",
            _archive(
                ("SKILL.md", _skill_md()), *((f"assets/{i}.bin", _tenfold()) for i in range(4))
            ),
            ["more than 20 MiB"],
        ),
        (
            "no SKILL.md",
            _archive(("a/README.md", b"x"), ("b/SKILL.md", _skill_md())),
            ["no `SKILL.md`"],
        ),
        ("bad name", _archive(("SKILL.md", _skill_md("Bad Name"))), ["`Bad Name`", "lowercase"]),
        (
            "a Windows device name",
            _archive(("SKILL.md", _skill_md()), ("references/CON.md", b"x")),
            ["`references/CON.md`", "Windows"],
        ),
        (
            "a trailing dot",
            _archive(("SKILL.md", _skill_md()), ("references./a.md", b"x")),
            ["`references./a.md`", "dot or a space"],
        ),
        (
            "a name only case tells apart",
            _archive(("SKILL.md", _skill_md()), ("Notes.md", b"a"), ("notes.md", b"b")),
            ["`Notes.md`", "`notes.md`", "same file"],
        ),
        (
            "a stream name",
            _archive(("SKILL.md", _skill_md()), ("SKILL.md:hidden", b"x")),
            ["`SKILL.md:hidden`", "Windows"],
        ),
        (
            "too deep",
            _archive(("SKILL.md", _skill_md()), ("/".join("abcdefghij") + "/x.md", b"x")),
            ["deeper than 8"],
        ),
        (
            "frontmatter that does not parse",
            _archive(("SKILL.md", b"---\nname: [unclosed\n---\nBody\n")),
            ["frontmatter does not parse"],
        ),
        ("not a ZIP", b"PK\x03\x04 this is not an archive", ["not a ZIP"]),
        (
            "bzip2, whose pieces unpack unbounded",
            _archive(("SKILL.md", _skill_md()), compression=zipfile.ZIP_BZIP2),
            ["`SKILL.md`", "Deflate"],
        ),
    ]


def test_hostile_archives_are_refused_with_their_cause(world: World) -> None:
    ada = _signed_in(world, "p-ada")
    before = _tree(world.data)
    for label, data, words in _hostile():
        refused = _upload(ada, data)
        assert refused.status_code in (400, 413, 415), (label, refused.status_code, refused.text)
        message = refused.json()["detail"]["message"]
        assert message.startswith("Not added: "), (label, message)
        for word in words:
            assert word in message, (label, word, message)
    # Nothing was written, in the skills folder or outside it.
    after = [p for p in _tree(world.data) if not p.startswith("workbench.sqlite3")]
    assert after == [p for p in before if not p.startswith("workbench.sqlite3")]
    assert not any((world.data / "skills").rglob("*")) if (world.data / "skills").exists() else True
    assert ada.get("/api/skills").json()["skills"] == []
    # The ZIP's own Unix bits never make a link on the disk, even when it says so.
    assert not any(p.is_symlink() for p in world.data.rglob("*"))


def test_the_writer_judges_every_place_before_it_makes_a_folder(tmp_path: Path) -> None:
    """The second wall: were the reader ever to let a path through, the writer refuses it before
    it makes any folder, a Windows drive (`C:`) included."""
    from eugene_plexus_workbench import skills

    outside = ["../x.sh", "a/../../x.sh", "/abs.sh", "a//b.sh", "./x.sh"]
    if os.name == "nt":
        outside += ["C:x.sh", "C:/x.sh", "a/C:/x.sh"]
    for bad in outside:
        with pytest.raises(skills.Refused, match="leaves the skill's folder"):
            skills._write(tmp_path / "incoming", b"---\n", {bad: b"x"})
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# the model's two tools
# --------------------------------------------------------------------------- #


def test_use_skill_returns_the_body_and_file_list(world: World) -> None:
    ada = _signed_in(world, "p-ada")
    _added(ada, _good_zip())
    world.gateway.mode = "tools-script"
    world.gateway.tool_script = [
        ("use_skill", json.dumps({"name": "pdf-forms"})),
        ("read_skill_file", json.dumps({"name": "pdf-forms", "path": "references/style.md"})),
    ]
    request = _ask(world, ada, ada.new_chat(), "Fill my form.")
    used, read = _results(request)
    assert "kqzvbodymark" in used and "name: pdf-forms" not in used
    assert "references/style.md" in used and "scripts/fill.py" in used
    assert (
        "This skill has scripts. They run only on a Job Site that allows commands; this chat "
        "has none." in used
    )
    assert STYLE in read
    answer = ada.get(f"/api/chats/{ada.get('/api/chats').json()['chats'][0]['id']}").json()
    calls = [c for t in answer["messages"][-1]["toolRounds"] for c in t["calls"]]
    # Read-only, run without asking, as search_memory is.
    assert [(c["tool"], c["status"], c["ask"]) for c in calls] == [
        ("use_skill", "done", False),
        ("read_skill_file", "done", False),
    ]


def test_read_skill_file_stays_inside_the_skill(world: World) -> None:
    ada = _signed_in(world, "p-ada")
    _added(ada, _good_zip())
    (world.data / "outside.txt").write_text("kqzvoutsidemark", encoding="utf-8")
    world.gateway.mode = "tools-script"
    attempts = [
        "../outside.txt",
        "../../../outside.txt",
        "references/../../outside.txt",
        "..\\..\\..\\outside.txt",
        str(world.data / "outside.txt"),
        "/etc/passwd",
        "C:/Windows/win.ini",
        "references/missing.md",
    ]
    world.gateway.tool_script = [
        ("read_skill_file", json.dumps({"name": "pdf-forms", "path": path})) for path in attempts
    ]
    request = _ask(world, ada, ada.new_chat(), "Read around.")
    results = _results(request)
    assert len(results) == len(attempts)
    for path, result in zip(attempts, results, strict=True):
        assert "kqzvoutsidemark" not in result and "root:" not in result, (path, result)
        assert "outside the skill" in result or "has no file" in result, (path, result)
    assert "has no file `references/missing.md`" in results[-1]


def test_the_listing_is_in_the_tool_not_the_system_message(world: World) -> None:
    ada = _signed_in(world, "p-ada")
    chat = ada.new_chat()
    ada.patch(f"/api/chats/{chat}", json={"settings": {"instructions": "Be brief."}})
    without = _ask(world, ada, chat, "First.")
    assert _tool(without, "use_skill") is None
    _added(ada, _good_zip())
    with_skills = _ask(world, ada, chat, "Second.")
    tool = _tool(with_skills, "use_skill")
    assert tool is not None and "Fill in PDF forms." in tool["description"]
    assert tool["parameters"]["properties"]["name"]["enum"] == ["pdf-forms"]
    assert _tool(with_skills, "read_skill_file") is not None
    # The system message is the same bytes with and without skills, and names none.
    assert _system(with_skills) == _system(without)
    assert "pdf-forms" not in json.dumps(with_skills["messages"])
    # The same tools, byte for byte, while the skills stay as they are (the prompt cache).
    again = _ask(world, ada, chat, "Third.")
    assert again["tools"] == with_skills["tools"]
    # A chat set to none of them is offered none.
    ada.patch(f"/api/chats/{chat}", json={"settings": {"skills": []}}).raise_for_status()
    assert _tool(_ask(world, ada, chat, "Fourth."), "use_skill") is None


def test_another_persons_skill_is_invisible(world: World) -> None:
    ada = _signed_in(world, "p-ada")
    skill = _added(ada, _good_zip())
    bo = _signed_in(world, "p-bo")
    assert bo.get("/api/skills").json()["skills"] == []
    path = f"/api/skills/{skill['id']}"
    for answer in (
        bo.get(path),
        bo.patch(path, json={"on": False}),
        bo.patch(path, json={"description": "Mine now."}),
        bo.delete(path),
        bo.post(
            f"{path}/files", files={"file": ("a.md", b"x", "text/markdown")}, data={"path": "a.md"}
        ),
        bo.delete(f"{path}/files/references/style.md"),
    ):
        assert answer.status_code == 404, answer.text
        assert answer.json()["detail"]["message"] == "There is no such skill."
    # Nor can Bo pick it for a chat, or reach it through a chat's tools.
    chat = bo.new_chat()
    picked = bo.patch(f"/api/chats/{chat}", json={"settings": {"skills": [skill["id"]]}})
    assert picked.status_code == 400, picked.text
    assert _tool(_ask(world, bo, chat, "Hello."), "use_skill") is None
    assert ada.get(path).json()["description"] == "Fill in PDF forms."


def test_an_owners_skill_reaches_everyone_and_can_be_turned_off(world: World) -> None:
    owner = _signed_in(world, "operator")
    made = owner.post(
        "/api/skills/written",
        json={"name": "house-style", "description": "The house style.", "instructions": "Be kind."},
    )
    assert made.status_code == 201, made.text
    given = owner.patch(f"/api/skills/{made.json()['id']}", json={"everyone": True})
    assert given.status_code == 200 and given.json()["everyone"] is True, given.text
    skill_id = given.json()["id"]
    assert (_folder(world.data / "skills" / "install", skill_id) / "SKILL.md").is_file()
    ada, bo = _signed_in(world, "p-ada"), _signed_in(world, "p-bo")
    seen = ada.get("/api/skills").json()["skills"]
    assert [(s["name"], s["fromEugene"], s["on"], s["canEdit"]) for s in seen] == [
        ("house-style", True, True, False)
    ]
    # Read-only to them.
    assert (
        ada.patch(f"/api/skills/{skill_id}", json={"instructions": "Be rude."}).status_code == 403
    )
    assert ada.delete(f"/api/skills/{skill_id}").status_code == 403
    for person in (ada, bo):
        tool = _tool(_ask(world, person, person.new_chat(), "Hi."), "use_skill")
        assert tool is not None and "house-style" in tool["description"]
    # Ada turns it off for herself: it leaves her next chat, and only hers.
    off = ada.patch(f"/api/skills/{skill_id}", json={"on": False})
    assert off.status_code == 200 and off.json()["on"] is False, off.text
    assert _tool(_ask(world, ada, ada.new_chat(), "Hi."), "use_skill") is None
    assert _tool(_ask(world, bo, bo.new_chat(), "Hi."), "use_skill") is not None
    assert owner.get(f"/api/skills/{skill_id}").json()["on"] is True
    # Taken back from everyone: it is the owner's own again, and nobody else sees it.
    owner.patch(f"/api/skills/{skill_id}", json={"everyone": False}).raise_for_status()
    assert bo.get("/api/skills").json()["skills"] == []


# --------------------------------------------------------------------------- #
# a person's data
# --------------------------------------------------------------------------- #


def _until_purged(owner: Browser, sub: str, seconds: float = 60) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        listed = owner.get("/api/removed-people").json()["people"]
        if any(p["sub"] == sub and p["state"] == "purged" for p in listed):
            return
        time.sleep(0.1)
    raise AssertionError("the purge did not finish")


def test_removing_a_person_deletes_their_skills(world: World) -> None:
    owner = _signed_in(world, "operator")
    shared = owner.post(
        "/api/skills/written",
        json={"name": "house-style", "description": "The house style.", "instructions": "Be kind."},
    ).json()
    owner.patch(f"/api/skills/{shared['id']}", json={"everyone": True}).raise_for_status()
    ada = _signed_in(world, "p-ada")
    skill = _added(ada, _good_zip())
    ada.patch(f"/api/skills/{shared['id']}", json={"on": False}).raise_for_status()
    folder = _skills_dir(world, "p-ada")
    assert (_folder(folder, skill["id"]) / "SKILL.md").is_file()

    del world.eugene.people["p-ada"]
    owner.post("/api/removed-people/check").raise_for_status()
    # Hidden for the wait: her skills are still on the disk until the purge.
    assert _folder(folder, skill["id"]).is_dir()
    started = owner.post("/api/removed-people/p-ada/delete")
    assert started.status_code == 202, started.text
    _until_purged(owner, "p-ada")

    assert not folder.exists()
    with contextlib.closing(sqlite3.connect(world.data / "workbench.sqlite3")) as db:
        assert db.execute("SELECT COUNT(*) FROM skills WHERE owner = 'p-ada'").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM skill_off WHERE sub = 'p-ada'").fetchone() == (0,)
        # The install's skill stays for everyone else.
        assert db.execute("SELECT owner FROM skills").fetchall() == [("install",)]
    assert (_folder(world.data / "skills" / "install", shared["id"]) / "SKILL.md").is_file()


def _export(person: Browser, path: str = "/api/me/export") -> zipfile.ZipFile:
    made = person.post(path)
    assert made.status_code == 202, made.text
    export = made.json()["export"]
    deadline = time.perf_counter() + 60
    while export["state"] == "making" and time.perf_counter() < deadline:
        time.sleep(0.1)
        export = next(
            e for e in person.get("/api/exports").json()["exports"] if e["id"] == export["id"]
        )
    assert export["state"] == "ready", export
    return zipfile.ZipFile(io.BytesIO(person.get(f"/api/exports/{export['id']}").content))


def test_export_includes_skills(world: World) -> None:
    ada = _signed_in(world, "p-ada")
    _added(ada, _good_zip())
    archive = _export(ada)
    names = archive.namelist()
    assert archive.read("skills/pdf-forms/SKILL.md") == _skill_md()
    assert archive.read("skills/pdf-forms/references/style.md").decode() == STYLE
    assert "skills/pdf-forms/scripts/fill.py" in names
    assert "skills/" in archive.read("README.txt").decode()
    # The owner's download of a person who uses Workbench holds no skills, as it holds no notes.
    owner = _signed_in(world, "operator")
    changed = httpx.patch(
        f"{world.workbench}/v1/config",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={"ownerReadsChats": True},
        trust_env=False,
    )
    assert changed.status_code == 200, changed.text
    assert not any(
        n.startswith("skills/") for n in _export(owner, "/api/people/p-ada/export").namelist()
    )
    # Of a person removed from Eugene, it holds everything Workbench keeps of them (row 7).
    del world.eugene.people["p-ada"]
    owner.post("/api/removed-people/check").raise_for_status()
    removed = _export(owner, "/api/people/p-ada/export")
    assert removed.read("skills/pdf-forms/SKILL.md") == _skill_md()


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #


def test_a_schema_33_store_gains_skills_and_keeps_a_copy(tmp_path: Path) -> None:
    path = tmp_path / "workbench.sqlite3"

    async def make() -> None:
        store = Store(path)
        await store.open()
        await store.close()

    asyncio.run(make())
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE skills")
        db.execute("DROP TABLE skill_off")
        db.execute("UPDATE meta SET value = '33' WHERE key = 'schema'")
        db.execute(
            "INSERT INTO chats (id, owner, title, created_at, updated_at) "
            "VALUES ('c1', 'p-ada', 'Kept', 1, 1)"
        )
    db.close()
    asyncio.run(make())
    assert SCHEMA_VERSION == 34
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone() == ("34",)
        assert db.execute("SELECT title FROM chats").fetchall() == [("Kept",)]
        columns = {r[1] for r in db.execute("PRAGMA table_info(skills)")}
        assert {"id", "owner", "name", "description", "enabled", "source", "bytes"} <= columns
        assert {r[1] for r in db.execute("PRAGMA table_info(skill_off)")} == {"sub", "skill_id"}
    db.close()
    copy = path.with_name("workbench.sqlite3.schema-33.bak")
    assert copy.is_file()
    with sqlite3.connect(copy) as kept:
        assert kept.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone() == ("33",)
    kept.close()


def test_the_admin_token_is_not_a_way_in(world: World) -> None:
    """Skills are a person's: the install's admin token reaches no skill route."""

    answer = httpx.get(
        f"{world.workbench}/api/skills",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        trust_env=False,
    )
    assert answer.status_code == 401
