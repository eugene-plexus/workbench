"""Job sites (your machines): a person's own machines, from Workbench.

`specs/docs/design/remote-nodes.md` §3.2-§3.4. A person adds a machine of their
own (J9), turns its file support on, registers its folders and says who may use
them, themselves included, and who may change files without asking (J11, J6g);
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
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .api import _person, _problem, _state
from .folder_io import FolderError

router = APIRouter()

INSTALLER_BASE = "https://raw.githubusercontent.com/eugene-plexus/specs/main/scripts"
_NODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")


def _clean(value: str) -> str:
    if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Use a nonempty value without control characters.")
    return value.strip()


class Invite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodeName: str | None = Field(default=None, max_length=63)

    @field_validator("nodeName")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if not _NODE.fullmatch(value.strip()):
            raise ValueError("Use letters, digits, dots, dashes and underscores.")
        return value.strip()


class Enable(BaseModel):
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


def _node(node: str) -> str:
    if not _NODE.fullmatch(node):
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such job site.")
    return quote(node, safe="")


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


def commands(invite: dict[str, Any]) -> dict[str, str]:
    """The two install commands, run on the machine being added. They name
    no password: it is asked for at the machine (rule 1 of §3.3)."""
    url, token, key, owner = (
        str(invite["nodesUrl"]),
        str(invite["token"]),
        str(invite["rootKey"]),
        str(invite["owner"]),
    )
    name = invite.get("nodeName")
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
    answer = await _call(
        request, "job-sites/invite", {"nodeName": body.nodeName} if body.nodeName else {}
    )
    if not answer or not all(k in answer for k in ("token", "nodesUrl", "rootKey", "owner")):
        raise _problem(status.HTTP_502_BAD_GATEWAY, "Eugene's invitation could not be read.")
    return {
        "expiresAt": answer.get("expiresAt"),
        "nodeName": answer.get("nodeName"),
        "commands": commands(answer),
    }


@router.post("/api/job-sites/{node}/enabled")
async def enabled(request: Request, node: str, body: Enable) -> dict[str, Any]:
    answer = await _call(request, f"job-sites/{_node(node)}/enabled", {"enabled": body.enabled})
    return answer or {}


@router.post("/api/job-sites/{node}/folders", status_code=status.HTTP_201_CREATED)
async def add_folder(request: Request, node: str, body: FolderCreate) -> dict[str, Any]:
    answer = await _call(
        request,
        f"job-sites/{_node(node)}/folders",
        {"name": body.name, "path": body.path, "writable": body.writable},
    )
    return answer or {}


@router.post("/api/job-sites/{node}/folders/{folder_id}/remove")
async def remove_folder(request: Request, node: str, folder_id: str) -> Response:
    await _call(request, f"job-sites/{_node(node)}/folders/{quote(folder_id, safe='')}/remove", {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/job-sites/{node}/folders/{folder_id}/people")
async def people(request: Request, node: str, folder_id: str, body: People) -> dict[str, Any]:
    answer = await _call(
        request,
        f"job-sites/{_node(node)}/folders/{quote(folder_id, safe='')}/people",
        {"people": [g.model_dump() for g in body.people]},
    )
    return answer or {}


@router.post("/api/job-sites/{node}/servers/{server}/access")
async def access(request: Request, node: str, server: str, body: Access) -> dict[str, Any]:
    answer = await _call(
        request,
        f"job-sites/{_node(node)}/servers/{_server(server)}/access",
        {"people": [p.model_dump() for p in body.people]},
    )
    return answer or {}


@router.post("/api/job-sites/{node}/servers/{server}/enabled")
async def server_enabled(request: Request, node: str, server: str, body: Enable) -> dict[str, Any]:
    answer = await _call(
        request,
        f"job-sites/{_node(node)}/servers/{_server(server)}/enabled",
        {"enabled": body.enabled},
    )
    return answer or {}


@router.post("/api/job-sites/{node}/settings")
async def settings(request: Request, node: str, body: Settings) -> dict[str, Any]:
    answer = await _call(
        request, f"job-sites/{_node(node)}/settings", {"ownerInDevMode": body.ownerInDevMode}
    )
    return answer or {}


@router.post("/api/job-sites/{node}/audit")
async def audit(request: Request, node: str, body: AuditRead) -> dict[str, Any]:
    answer = await _call(request, f"job-sites/{_node(node)}/audit", {"limit": body.limit})
    return answer or {"entries": []}


@router.post("/api/job-sites/{node}/leave")
async def leave(request: Request, node: str) -> Response:
    await _call(request, f"job-sites/{_node(node)}/leave", {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
