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

**Each person's own** (2b.3b, `job-sites-own-enrollment.md` §3.3). A person
linked to a machine they do not own keeps workspaces of their own there: they
add one by its path on the machine, set their rules in it (allow, ask or deny
for reading and for changing files, and paths to hide), and approve each
change with their own key. The machine's owner shares their own workspaces,
each person with rules of their own there. Paths are read live from the
machine; Eugene keeps none (J76).

**Passkeys** (J14a.3, `person-held-keys.md` §4.2, §12.5). At its HTTPS address
Workbench is a WebAuthn relying party, and a person may pair a passkey with
their machine and approve what it holds from here. The browser makes the
passkey and computes the pairing MAC from the code the person typed; this
server never sees the code, and only carries the passkey's public half, the
MAC and each approval to Eugene, which carries them to the machine. Without an
HTTPS address there is no relying party, so no passkey: the list says so.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlsplit

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
    #: `allow` runs without asking, `ask` asks the person each time (J78).
    decision: Literal["allow", "ask"] | None = None
    standing: StrictBool = False


class PersonTools(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=256)
    tools: list[ToolGrant] = Field(max_length=64)

    _clean = field_validator("name")(classmethod(lambda cls, v: _clean(v)))


class Access(BaseModel):
    model_config = ConfigDict(extra="forbid")
    people: list[PersonTools] = Field(max_length=256)


Decision = Literal["allow", "ask", "deny"]


class Rules(BaseModel):
    """`SiteRules` (J70): reading and searching, and changing files; since
    2b.4, running commands (J88), signed each time or never."""

    model_config = ConfigDict(extra="forbid")
    read: Decision
    change: Decision
    command: Literal["ask", "deny"] | None = None


#: `.gitignore`'s syntax without `!` (`SiteDenyPattern`).
DenyPattern = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[^!\x00-\x1f]")]


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    path: str = Field(min_length=1, max_length=4096)
    writable: StrictBool = True
    rules: Rules | None = None
    deny: list[DenyPattern] = Field(default_factory=list, max_length=64)

    _clean = field_validator("name", "path")(classmethod(lambda cls, v: _clean(v)))


class RulesSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: Rules
    deny: list[DenyPattern] = Field(max_length=64)


class Share(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=256)
    read: Decision
    change: Decision

    _clean = field_validator("name")(classmethod(lambda cls, v: _clean(v)))


class Shares(BaseModel):
    model_config = ConfigDict(extra="forbid")
    people: list[Share] = Field(max_length=256)


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
_B64URL = r"^[A-Za-z0-9_-]+$"
_KEY = r"^[a-f0-9]{32}$"
_HELD = re.compile(r"^[a-z0-9]{1,32}$")
_PASSKEY = re.compile(r"^[a-f0-9]{32}$")
_WORKSPACE = re.compile(r"^[a-f0-9]{32}$")


class PasskeyPair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credentialId: str = Field(min_length=1, max_length=1366, pattern=_B64URL)
    publicKey: str = Field(min_length=1, max_length=1100)
    alg: Literal[-8, -7, -257]
    rpId: str = Field(min_length=1, max_length=253)
    label: str | None = Field(default=None, max_length=128)
    mac: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class HeldList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str | None = Field(default=None, pattern=_KEY)


class PasskeyApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    envelope: str = Field(min_length=2, max_length=262144)
    key: str = Field(pattern=_KEY)
    credentialId: str = Field(min_length=1, max_length=1366, pattern=_B64URL)
    authenticatorData: str = Field(min_length=1, max_length=4096, pattern=_B64URL)
    clientDataJSON: str = Field(min_length=1, max_length=8192, pattern=_B64URL)
    signature: str = Field(min_length=1, max_length=1024, pattern=_B64URL)


def _relying_party(request: Request) -> str | None:
    """Workbench's WebAuthn RP ID: the host of its HTTPS address, or None
    where it has none (a plain-HTTP address can hold no passkey)."""
    origin = _state(request).settings.public_origin
    return urlsplit(origin).hostname if origin else None


def _held(ident: str) -> str:
    if not _HELD.fullmatch(ident):
        raise _problem(status.HTTP_404_NOT_FOUND, "Nothing is waiting under that name.")
    return ident


def _passkey(ident: str) -> str:
    if not _PASSKEY.fullmatch(ident):
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such passkey.")
    return ident


def _workspace(ident: str) -> str:
    if not _WORKSPACE.fullmatch(ident):
        raise _problem(status.HTTP_404_NOT_FOUND, "There is no such workspace.")
    return ident


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
    person = await _person(request)
    # What the browser needs to make a passkey here (J14a.3).
    passkeys = {"rpId": _relying_party(request), "person": person.sub, "name": person.username}
    return {**(answer or {}), "passkeys": passkeys}


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


@router.post("/api/job-sites/{site}/workspaces", status_code=status.HTTP_201_CREATED)
async def add_workspace(request: Request, site: str, body: WorkspaceCreate) -> dict[str, Any]:
    """A workspace of your own: your own account on the machine opens it.
    The machine holds it until you approve it with your own key (J68)."""
    payload: dict[str, Any] = {"name": body.name, "path": body.path, "writable": body.writable}
    if body.rules is not None:
        # Without `command` when not given: an older Eugene refuses the field.
        payload["rules"] = body.rules.model_dump(exclude_none=True)
    if body.deny:
        payload["deny"] = body.deny
    answer = await _call(request, f"job-sites/{_site(site)}/workspaces", payload)
    return answer or {}


@router.post("/api/job-sites/{site}/workspaces/list")
async def list_workspaces(request: Request, site: str) -> dict[str, Any]:
    """Your workspaces there with their paths, read live (J76)."""
    answer = await _call(request, f"job-sites/{_site(site)}/workspaces/list", {})
    return answer or {"workspaces": []}


@router.post("/api/job-sites/{site}/workspaces/{ident}/remove")
async def remove_workspace(request: Request, site: str, ident: str) -> Response:
    await _call(request, f"job-sites/{_site(site)}/workspaces/{_workspace(ident)}/remove", {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/job-sites/{site}/workspaces/{ident}/rules")
async def workspace_rules(
    request: Request, site: str, ident: str, body: RulesSet
) -> dict[str, Any]:
    answer = await _call(
        request,
        f"job-sites/{_site(site)}/workspaces/{_workspace(ident)}/rules",
        {"rules": body.rules.model_dump(exclude_none=True), "deny": body.deny},
    )
    return answer or {}


@router.post("/api/job-sites/{site}/workspaces/{ident}/people")
async def share_workspace(request: Request, site: str, ident: str, body: Shares) -> dict[str, Any]:
    """The machine's owner shares one of their workspaces (J69)."""
    answer = await _call(
        request,
        f"job-sites/{_site(site)}/workspaces/{_workspace(ident)}/people",
        {"people": [p.model_dump() for p in body.people]},
    )
    return answer or {}


@router.post("/api/job-sites/{site}/servers/{server}/access")
async def access(request: Request, site: str, server: str, body: Access) -> dict[str, Any]:
    answer = await _call(
        request,
        f"job-sites/{_site(site)}/servers/{_server(server)}/access",
        {"people": [p.model_dump(exclude_none=True) for p in body.people]},
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


@router.post("/api/job-sites/{site}/passkeys", status_code=status.HTTP_201_CREATED)
async def pair_passkey(request: Request, site: str, body: PasskeyPair) -> dict[str, Any]:
    """A passkey this page made, with the MAC its browser computed from the
    code shown at the machine. The code is not here: only the MAC is."""
    rp = _relying_party(request)
    if rp is None or body.rpId != rp:
        raise _problem(
            status.HTTP_409_CONFLICT,
            "A passkey is made at Workbench's https address. Open Workbench there.",
        )
    answer = await _call(request, f"job-sites/{_site(site)}/passkeys", body.model_dump())
    return answer or {}


@router.post("/api/job-sites/{site}/passkeys/{ident}/remove")
async def remove_passkey(request: Request, site: str, ident: str) -> Response:
    """A lost phone (J60): removing a key only takes it away, so it needs no
    passkey, no https and no visit to the machine."""
    await _call(request, f"job-sites/{_site(site)}/passkeys/{_passkey(ident)}/remove", {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/job-sites/{site}/held")
async def held(request: Request, site: str, body: HeldList) -> dict[str, Any]:
    answer = await _call(
        request, f"job-sites/{_site(site)}/held", {"key": body.key} if body.key else {}
    )
    return answer or {}


@router.post("/api/job-sites/{site}/held/{ident}/approve")
async def approve_held(
    request: Request, site: str, ident: str, body: PasskeyApproval
) -> dict[str, Any]:
    answer = await _call(
        request, f"job-sites/{_site(site)}/held/{_held(ident)}/approve", body.model_dump()
    )
    return answer or {}


@router.post("/api/job-sites/{site}/held/{ident}/reject")
async def reject_held(request: Request, site: str, ident: str) -> Response:
    await _call(request, f"job-sites/{_site(site)}/held/{_held(ident)}/reject", {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/job-sites/{site}/window/close")
async def close_window(request: Request, site: str) -> Response:
    """Close the person's window on a machine now (J14b, J90): it only takes
    access away, so it needs no passkey."""
    await _call(request, f"job-sites/{_site(site)}/window/close", {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/job-sites/{site}/commands/withdraw")
async def withdraw_commands(request: Request, site: str) -> Response:
    """The machine's owner turns commands off there (J30, J89). Turning them
    on again is done at the machine, by an administrator."""
    await _call(request, f"job-sites/{_site(site)}/commands/withdraw", {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/job-sites/{site}/leave")
async def leave(request: Request, site: str) -> Response:
    await _call(request, f"job-sites/{_site(site)}/leave", {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
