"""Job sites (your machines): a person's own machines, from Workbench.

`specs/docs/design/job-sites-own-enrollment.md`; `remote-nodes.md` §3.3-§3.4. A
job site is its own enrollment, named here by its site id (J19). A person adds a
machine of their own that is already a node (J9, J21), registers its folders and
says who may use them, themselves included, and who may change files without
asking (J11, J6g);
turns its local servers on and says who may use which of their tools; lets
Eugene's owner in for dev mode or not (J6e); and reads its audit log (J8).
Eugene relays each change to the machine, whose own list is final (J6b).
Workbench holds no authority of its own here and passes the answers through.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .api import _person, _problem, _state
from .folder_io import FolderError
from .node_folders import HeldAtTheMachine

router = APIRouter()

INSTALLER_BASE = "https://raw.githubusercontent.com/eugene-plexus/specs/main/scripts"
_NODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_SITE = re.compile(r"^s-[a-z2-7]{26}$")


def _clean(value: str) -> str:
    if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Use a nonempty value without control characters.")
    return value.strip()


class Invite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, max_length=63)

    @field_validator("label")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if not _NODE.fullmatch(value.strip()):
            raise ValueError("Use letters, digits, dots, dashes and underscores.")
        return value.strip()


class ServerEnable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class FolderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    path: str = Field(min_length=1, max_length=4096)
    writable: StrictBool = False

    _clean = field_validator("name", "path")(classmethod(lambda cls, v: _clean(v)))


class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=256)
    writable: StrictBool = False

    _clean = field_validator("name")(classmethod(lambda cls, v: _clean(v)))


class People(BaseModel):
    model_config = ConfigDict(extra="forbid")
    people: list[Grant] = Field(max_length=256)


class ToolGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    standing: StrictBool = False


class PersonTools(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=256)
    tools: list[ToolGrant] = Field(max_length=64)

    _clean = field_validator("name")(classmethod(lambda cls, v: _clean(v)))


class Access(BaseModel):
    model_config = ConfigDict(extra="forbid")
    people: list[PersonTools] = Field(max_length=256)


class LinkRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person: str | None = Field(default=None, min_length=1, max_length=256)


_LINK_REFUSALS = {
    status.HTTP_403_FORBIDDEN,
    status.HTTP_404_NOT_FOUND,
    status.HTTP_409_CONFLICT,
    status.HTTP_503_SERVICE_UNAVAILABLE,
}


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ownerInDevMode: StrictBool


class AuditRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=50, ge=1, le=200)


_SERVER = re.compile(r"^[a-z][a-z0-9-]{0,39}$")


def _server(server: str) -> str:
    if not _SERVER.fullmatch(server) or server.startswith("files"):
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such server on this machine.")
    return server


def _site(site: str) -> str:
    if not _SITE.fullmatch(site):
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such job site.")
    return quote(site, safe="")


async def _call(request: Request, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
    person = await _person(request)
    remote = _state(request).tools.node_folders
    if remote is None or not person.session_id:
        raise _problem(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Job sites need Workbench to be signed in with Eugene.",
        )
    try:
        answer: dict[str, Any] | None = await remote.call(person, path, body)
        return answer
    except FolderError as exc:
        raise _problem(status.HTTP_409_CONFLICT, str(exc)) from None


def held_response(_request: Request, exc: Exception) -> JSONResponse:
    """A change a job site holds for its owner's approval at the machine."""
    message = exc.message if isinstance(exc, HeldAtTheMachine) else str(exc)
    return JSONResponse(
        {"held": True, "message": message},
        status_code=status.HTTP_202_ACCEPTED,
        headers={"Cache-Control": "no-store"},
    )


def commands(invite: dict[str, Any]) -> dict[str, str]:
    """The two install commands, run on the machine being added. They name
    no password: it is asked for at the machine (rule 1 of §3.3)."""
    url, token, key, owner = (
        str(invite["joinUrl"]),
        str(invite["token"]),
        str(invite["rootKey"]),
        str(invite["owner"]),
    )
    name = invite.get("label")
    windows = (
        f"& ([scriptblock]::Create((irm {INSTALLER_BASE}/install.ps1))) -Join {url} "
        f"-Token {token} -JobSite -Owner {_ps(owner)} -RootKey {key}"
        + (f" -NodeName {name}" if name else "")
    )
    posix = (
        f"curl -fsSL {INSTALLER_BASE}/install.sh | sh -s -- --join {url} --token {token} "
        f"--job-site --owner {_sh(owner)} --root-key {key}" + (f" --name {name}" if name else "")
    )
    return {"windows": windows, "posix": posix}


def _ps(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sh(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


@router.get("/api/job-sites")
async def sites(request: Request) -> dict[str, Any]:
    answer = await _call(request, "job-sites", {})
    return answer or {}


@router.post("/api/job-sites/invite")
async def invite(request: Request, body: Invite) -> dict[str, Any]:
    answer = await _call(request, "job-sites/invite", {"label": body.label} if body.label else {})
    if not answer or not all(k in answer for k in ("token", "joinUrl", "rootKey", "owner")):
        raise _problem(status.HTTP_502_BAD_GATEWAY, "Eugene's invitation could not be read.")
    return {
        "expiresAt": answer.get("expiresAt"),
        "label": answer.get("label"),
        "commands": commands(answer),
    }


@router.post("/api/job-sites/{site}/folders", status_code=status.HTTP_201_CREATED)
async def add_folder(request: Request, site: str, body: FolderCreate) -> dict[str, Any]:
    answer = await _call(
        request,
        f"job-sites/{_site(site)}/folders",
        {"name": body.name, "path": body.path, "writable": body.writable},
    )
    return answer or {}


@router.post("/api/job-sites/{site}/folders/{folder_id}/remove")
async def remove_folder(request: Request, site: str, folder_id: str) -> Response:
    await _call(request, f"job-sites/{_site(site)}/folders/{quote(folder_id, safe='')}/remove", {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/job-sites/{site}/folders/{folder_id}/people")
async def people(request: Request, site: str, folder_id: str, body: People) -> dict[str, Any]:
    answer = await _call(
        request,
        f"job-sites/{_site(site)}/folders/{quote(folder_id, safe='')}/people",
        {"people": [g.model_dump() for g in body.people]},
    )
    return answer or {}


@router.post("/api/job-sites/{site}/servers/{server}/access")
async def access(request: Request, site: str, server: str, body: Access) -> dict[str, Any]:
    answer = await _call(
        request,
        f"job-sites/{_site(site)}/servers/{_server(server)}/access",
        {"people": [p.model_dump() for p in body.people]},
    )
    return answer or {}


@router.post("/api/job-sites/{site}/servers/{server}/enabled")
async def server_enabled(
    request: Request, site: str, server: str, body: ServerEnable
) -> dict[str, Any]:
    answer = await _call(
        request,
        f"job-sites/{_site(site)}/servers/{_server(server)}/enabled",
        {"enabled": body.enabled},
    )
    return answer or {}


@router.post("/api/job-sites/{site}/settings")
async def settings(request: Request, site: str, body: Settings) -> dict[str, Any]:
    answer = await _call(
        request, f"job-sites/{_site(site)}/settings", {"ownerInDevMode": body.ownerInDevMode}
    )
    return answer or {}


@router.post("/api/job-sites/{site}/audit")
async def audit(request: Request, site: str, body: AuditRead) -> dict[str, Any]:
    answer = await _call(request, f"job-sites/{_site(site)}/audit", {"limit": body.limit})
    return answer or {"entries": []}


@router.post("/api/job-sites/{site}/links/remove", status_code=status.HTTP_204_NO_CONTENT)
async def remove_link(request: Request, site: str, body: LinkRemove | None = None) -> Response:
    """A person removes their own link on a machine; the machine's owner may
    name anyone's. Eugene's refusal is passed through in its own words."""
    _site(site)
    payload: dict[str, Any] = {"site": site}
    if body is not None and body.person:
        payload["person"] = body.person
    person = await _person(request)
    remote = _state(request).tools.node_folders
    if remote is None or not person.session_id:
        raise _problem(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Job sites need Workbench to be signed in with Eugene.",
        )
    try:
        await remote.call(person, "sites/link/remove", payload)
    except FolderError as exc:
        code = exc.status if exc.status in _LINK_REFUSALS else status.HTTP_409_CONFLICT
        raise _problem(code, str(exc)) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/job-sites/{site}/leave")
async def leave(request: Request, site: str) -> Response:
    await _call(request, f"job-sites/{_site(site)}/leave", {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
