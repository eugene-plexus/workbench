"""The Skills page's routes (row 32, `skills.py`).

Every route is the asking person's: a skill that is not theirs, and not one Eugene gives everyone,
is the same 404 as one that does not exist, on every route. A skill Eugene gives everyone is read
by everyone, changed only by the install's owner, and turned off by each person for themselves.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field, StrictBool

from .skills import MAX_UNPACKED, MAX_UPLOAD, Refused, Skills, compose, read_upload, view
from .skills import parse as parse_skill_md
from .store import Person

router = APIRouter()


def _problem(refused: Refused) -> HTTPException:
    detail: dict[str, Any] = {"message": refused.message}
    if refused.conflict is not None:
        detail["conflict"] = refused.conflict
    return HTTPException(status_code=refused.status, detail=detail)


async def _person(request: Request) -> Person:
    person: Person = await request.app.state.sessions.person(request)
    return person


def _skills(request: Request) -> Skills:
    skills: Skills = request.app.state.skills
    return skills


async def _view(request: Request, person: Person, skill_id: str) -> dict[str, Any]:
    skills = _skills(request)
    skill = await skills.visible(person, skill_id)
    listing = await skills.listing(person)
    return view(skill, person, listing, await skills.db.off(person.sub))


async def _read(file: UploadFile, limit: int) -> bytes:
    """An upload, read whole up to `limit`: one byte more is a refusal, not a cut."""
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise Refused(f"Not added: the file is larger than {limit // (1024 * 1024)} MiB.", 413)
    return data


@router.get("/api/skills")
async def list_skills(request: Request) -> dict[str, Any]:
    """The person's skills and those Eugene gives everyone, by name, each with whether it is on
    and listed in their chats, and why not."""
    person = await _person(request)
    skills = _skills(request)
    own, shared, off = await skills.seen(person)
    listing = await skills.listing(person)
    everything = sorted([*own, *shared], key=lambda s: (s.name, s.everyone, s.id))
    return {"skills": [view(s, person, listing, off) for s in everything]}


@router.post("/api/skills", status_code=status.HTTP_201_CREATED)
async def upload_skill(
    request: Request,
    file: UploadFile = File(...),
    onConflict: Literal["replace", "both"] | None = Form(default=None),
) -> dict[str, Any]:
    """Add a skill from a `.zip` of its folder or one `SKILL.md`. It is added off: the page shows
    the whole `SKILL.md`, and the person turns it on once they have read it."""
    person = await _person(request)
    skills = _skills(request)
    try:
        data = await _read(file, MAX_UPLOAD)
        unpacked = read_upload(file.filename or "", data)
        skill = await skills.add(person, unpacked, source="upload", on_conflict=onConflict)
    except Refused as refused:
        raise _problem(refused) from None
    return await _view(request, person, skill.id)


class Written(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    instructions: str = Field(default="", max_length=MAX_UNPACKED)


@router.post("/api/skills/written", status_code=status.HTTP_201_CREATED)
async def write_skill(request: Request, body: Written) -> dict[str, Any]:
    """A skill the person wrote on the page: the form writes its `SKILL.md`. It is on at once:
    the person wrote every word of it."""
    from .skills import Unpacked

    person = await _person(request)
    try:
        data = compose({"name": body.name, "description": body.description}, body.instructions)
        unpacked = Unpacked(parse_skill_md(data), data)
        skill = await _skills(request).add(person, unpacked, source="written", on=True)
    except Refused as refused:
        raise _problem(refused) from None
    return await _view(request, person, skill.id)


@router.get("/api/skills/{skill_id}")
async def get_skill(request: Request, skill_id: str) -> dict[str, Any]:
    """One skill, with its whole `SKILL.md` as it is on the disk and the frontmatter's other
    keys, which Workbench keeps and shows and never acts on."""
    person = await _person(request)
    skills = _skills(request)
    try:
        shown = await _view(request, person, skill_id)
        data, parsed = await skills.read(await skills.visible(person, skill_id))
    except Refused as refused:
        raise _problem(refused) from None
    return {
        **shown,
        "skillMd": data.decode("utf-8", "replace"),
        "instructions": parsed.body.strip() if parsed else None,
        "others": parsed.others() if parsed else [],
        "problem": None if parsed else "Its SKILL.md cannot be read now.",
    }


class Change(BaseModel):
    on: StrictBool | None = None
    everyone: StrictBool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    instructions: str | None = Field(default=None, max_length=MAX_UNPACKED)


@router.patch("/api/skills/{skill_id}")
async def change_skill(request: Request, skill_id: str, body: Change) -> dict[str, Any]:
    """Turn a skill on or off (one Eugene gives everyone, for the asker only), give it to
    everyone or take it back (the owner), or change its name, description or instructions."""
    person = await _person(request)
    try:
        await _skills(request).change(person, skill_id, body.model_dump(exclude_none=True))
        return await _view(request, person, skill_id)
    except Refused as refused:
        raise _problem(refused) from None


@router.delete("/api/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(request: Request, skill_id: str) -> Response:
    person = await _person(request)
    try:
        await _skills(request).delete(person, skill_id)
    except Refused as refused:
        raise _problem(refused) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/skills/{skill_id}/files")
async def add_skill_file(
    request: Request, skill_id: str, file: UploadFile = File(...), path: str = Form(...)
) -> dict[str, Any]:
    """Add a file to a skill, or write one again, at `path` in its folder."""
    person = await _person(request)
    skills = _skills(request)
    try:
        await skills.visible(person, skill_id)
        data = await _read(file, MAX_UNPACKED)
        await skills.add_file(person, skill_id, path, data)
        return await _view(request, person, skill_id)
    except Refused as refused:
        raise _problem(refused) from None


@router.delete("/api/skills/{skill_id}/files/{path:path}")
async def remove_skill_file(request: Request, skill_id: str, path: str) -> dict[str, Any]:
    person = await _person(request)
    try:
        await _skills(request).remove_file(person, skill_id, path)
        return await _view(request, person, skill_id)
    except Refused as refused:
        raise _problem(refused) from None
