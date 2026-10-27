"""MCP connections and protocol translation, through HTTP or owner-selected stdio.

Each answer owns its MCP sessions in one task (SDK cancellation scopes must
exit in the task that entered them). HTTP pools last for that session; the
TLS context is built once per Workbench, never while dispatching a tool.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx2
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.client.extension import advertise
from mcp.client.streamable_http import streamable_http_client
from referencing import Registry
from referencing.exceptions import NoSuchResource

from . import a2ui_state, files, folder_io, folders, mcp_ui, memory
from .content import CONTENT_VERSION, Keeper, normalize
from .local_tools import LocalProcesses, LocalToolError, unavailable
from .node_folders import FILES as SITE_FILES
from .node_folders import PREFIX as NODE_FOLDER_PREFIX
from .node_folders import SITE_PREFIX, Held, NodeFolders, site_server_id
from .settings import Settings
from .store import CHATS_SERVER as CHATS_SERVER_ID
from .store import Person, Store

if TYPE_CHECKING:
    from .a2ui import A2UI
    from .mcp_apps import McpApps

MAX_TOOLS = 64
#: Where a job site's tool listing says a call must be asked about (2b.3b):
#: the workspaces, for its file server; whether, for a local server's tool.
ASK_META = "eugene-plexus/ask"
#: A job site that checks the person's own signature on each call (J14b):
#: Workbench sends the call and has the person sign what the site holds,
#: instead of asking first.
SIGNED_META = "eugene-plexus/signed"
MAX_ARGUMENTS = 16_384
MAX_RESPONSE = 8 * 1024 * 1024
MAX_PICTURES = 8
MAX_PICTURE_BYTES = 10 * 1024 * 1024
#: `search_memory` (MV1): the one tool Workbench runs itself, over the asking person's own
#: notes. Its server id is what a kept call is known by, to withhold or hide its results.
MEMORY_SERVER = "memory"
MEMORY_TOOL_NAME = memory.SEARCH_TOOL
#: `search_chats` (MV2): the person's past chats, run by Workbench as `search_memory` is.
CHATS_SERVER = CHATS_SERVER_ID
CHATS_TOOL_NAME = "search_chats"
#: What a kept `search_chats` result is in a request that may not carry past chats.
CHATS_WITHHELD = "Past-chat results are not sent to this model."
#: MV3b: `remember`, the tool a model set to Keep automatically may call.
WRITE_SERVER = "memory-write"
REMEMBER_TOOL_NAME = "remember"
#: Row 32: `use_skill` and `read_skill_file` (`skills.py`), run by Workbench over the asking
#: person's skills, as `search_memory` is over their notes.
SKILLS_SERVER = "skills"
SkillRunner = Callable[
    [Person, list[Any], str, dict[str, Any], list[str]], Awaitable[tuple[str, str]]
]


class ToolError(Exception):
    """A failure safe to show without credentials or server response bodies."""


def known_problem(exc: BaseException) -> str | None:
    """Find a safe observed cause inside the SDK's task-group wrappers."""
    pending = [exc]
    seen: set[int] = set()
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        if isinstance(error, (ToolError, LocalToolError)):
            return str(error)
        if isinstance(error, FileNotFoundError):
            return "The local executable or a file it needs was not found. Check its full path."
        if isinstance(error, PermissionError):
            return "Workbench's account cannot start this executable. Check its file permissions."
        if isinstance(error, httpx2.TimeoutException):
            return "The MCP server timed out. Check that it is running and reachable."
        if isinstance(error, httpx2.ConnectError):
            return "Could not connect to the MCP server. Check its address and TLS certificate."
        if isinstance(error, httpx2.HTTPStatusError):
            return f"The MCP server answered HTTP {error.response.status_code}. Check the server."
        if isinstance(error, BaseExceptionGroup):
            pending.extend(error.exceptions)
        if error.__cause__ is not None:
            pending.append(error.__cause__)
        if error.__context__ is not None:
            pending.append(error.__context__)
    return None


def validate_url(value: str) -> str:
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        _ = url.port
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if (
            not host
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or any(c.isspace() for c in value)
            or "\\" in value
            or not (url.scheme == "https" or (url.scheme == "http" and loopback))
        ):
            raise ValueError
    except ValueError as exc:
        raise ValueError(
            "Use an HTTPS MCP URL (HTTP is allowed on loopback), "
            "without credentials, a query or a fragment."
        ) from exc
    return value


def public_server(server: dict[str, Any]) -> dict[str, Any]:
    common = {key: server[key] for key in ("id", "name")}
    if server.get("transport", "http") == "stdio":
        return common | {
            "transport": "stdio",
            "command": server["command"],
            "args": server["args"],
            "environmentKeys": sorted(server["environment"]),
            "access": "owner",
        }
    return common | {"transport": "http", "url": server["url"], "hasToken": bool(server["token"])}


def visible_server(server: dict[str, Any], person: Person | None) -> bool:
    return server.get("transport", "http") == "http" or bool(person and person.is_owner)


def _no_resource(uri: str) -> Any:
    raise NoSuchResource(ref=uri)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise ValueError("non-finite JSON number")


def transcript(
    rounds: list[dict[str, Any]],
    *,
    notes: bool = True,
    chats: bool | None = None,
    pictures: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Kept tool rounds as messages. Without `notes` (the request may not carry the person's
    notes: an outside model, memory turned off), a `search_memory` result is withheld, and so
    are the call's arguments: the query a model wrote can hold the words of the notes it had.
    Without `chats` (MV2; it follows `notes` when not given), a `search_chats` call is
    withheld the same way."""
    chats = notes if chats is None else chats
    held = ({MEMORY_SERVER} if not notes else set()) | ({CHATS_SERVER} if not chats else set())
    out: list[dict[str, Any]] = []
    for turn in rounds:
        assistant = turn["assistant"]
        if held:
            mine = {c["id"] for c in turn["calls"] if c.get("serverId") in held}
            if mine and assistant.get("tool_calls"):
                assistant = {
                    **assistant,
                    "tool_calls": [
                        {**c, "function": {**c["function"], "arguments": "{}"}}
                        if c.get("id") in mine
                        else c
                        for c in assistant["tool_calls"]
                    ],
                }
        out.append(assistant)
        for call in turn["calls"]:
            result = call["result"] or "This call did not finish. Do not assume it succeeded."
            if call.get("serverId") in held:
                result = (
                    memory.WITHHELD if call.get("serverId") == MEMORY_SERVER else CHATS_WITHHELD
                )
            elif pictures and call.get("result"):
                result = _picture_content(call, pictures)
            out.append({"role": "tool", "tool_call_id": call["id"], "content": result})
    return out


def _picture_line(block: dict[str, Any], *, attached: bool = False) -> str:
    size = files.describe_size(block["size"]) if block["size"] >= 1024 else f"{block['size']} bytes"
    state = "It is attached." if attached else "The person sees it; you do not."
    return (
        f"[The tool returned a picture, {json.dumps(block['name'], ensure_ascii=False)} "
        f"({block['mediaType']}, {size}). {state}]"
    )


def _picture_content(call: dict[str, Any], pictures: dict[str, str]) -> Any:
    result = call["result"]
    parts = []
    for block in call.get("content", []):
        if block.get("type") == "image" and (url := pictures.get(block.get("id"))):
            line = _picture_line(block)
            if line in result:
                result = result.replace(line, _picture_line(block, attached=True), 1)
                parts.append({"type": "image_url", "image_url": {"url": url}})
    return [{"type": "text", "text": result}, *parts] if parts else result


def _read_picture(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(5 * files.MIB + 1)


async def attach_pictures(
    messages: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    store: Store,
    root: Path,
    owner: str,
    chat_id: str,
    *,
    image_input: bool,
) -> list[dict[str, Any]]:
    """Read kept pictures at send time, newest first, across the whole request.

    The store and bytes must still agree. Missing files and withheld results
    keep their original line; only confirmed image input permits attachment.
    """
    if not image_input:
        return messages
    calls: dict[str, list[dict[str, Any]]] = {}
    for turn in rounds:
        for call in turn["calls"]:
            calls.setdefault(call["id"], []).append(call)
    candidates = []
    for index, message in enumerate(messages):
        if message.get("role") != "tool":
            continue
        matching = calls.get(message.get("tool_call_id", ""), [])
        if not matching:
            continue
        call = matching.pop(0)
        # Memory/privacy withholding has already been decided by transcript.
        if not call.get("result") or message.get("content") != call["result"]:
            continue
        for block in call.get("content", []):
            if block.get("type") == "image" and _picture_line(block) in call["result"]:
                candidates.append((index, call, block))
    count = total = 0
    selected: dict[int, tuple[dict[str, Any], dict[str, str]]] = {}
    for index, call, block in reversed(candidates):
        if count >= MAX_PICTURES:
            break
        record = await store.file(block["fileId"])
        if (
            record is None
            or record.owner != owner
            or record.chat_id != chat_id
            or record.origin != "tool"
            or record.media_type not in {"image/png", "image/jpeg", "image/webp"}
            or record.size > 5 * files.MIB
            or total + record.size > MAX_PICTURE_BYTES
        ):
            continue
        try:
            data = await asyncio.to_thread(_read_picture, files.path_of(root, owner, record.id))
        except OSError:
            continue
        if (
            len(data) != record.size
            or files.sniff_image(data) != record.media_type
            or total + len(data) > MAX_PICTURE_BYTES
        ):
            continue
        url = f"data:{record.media_type};base64," + base64.b64encode(data).decode("ascii")
        selected.setdefault(index, (call, {}))[1][block["id"]] = url
        count += 1
        total += len(data)
    out = list(messages)
    for index, (call, pictures) in selected.items():
        out[index] = {**out[index], "content": _picture_content(call, pictures)}
    return out


class Calls:
    """Accumulate interleaved streamed function calls without executing fragments."""

    def __init__(self) -> None:
        self.items: dict[int, dict[str, Any]] = {}

    def take(self, chunk: dict[str, Any]) -> None:
        choices = chunk.get("choices") or []
        for piece in ((choices[0].get("delta") or {}).get("tool_calls") or []) if choices else []:
            index = piece.get("index")
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 16:
                raise ToolError("The model sent an invalid tool-call index. Try another model.")
            item = self.items.setdefault(
                index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            )
            for dest, key, value in (
                (item, "id", piece.get("id")),
                (item["function"], "name", (piece.get("function") or {}).get("name")),
                (item["function"], "arguments", (piece.get("function") or {}).get("arguments")),
            ):
                if value is not None:
                    if not isinstance(value, str):
                        raise ToolError("The model sent a malformed tool call. Try another model.")
                    dest[key] += value
                    if len(dest[key]) > (MAX_ARGUMENTS if key == "arguments" else 256):
                        raise ToolError(
                            "The model's tool call is too large. Ask for a smaller task."
                        )

    def finish(self) -> list[dict[str, Any]]:
        calls = [self.items[i] for i in sorted(self.items)]
        ids = [c["id"] for c in calls]
        if any(not i for i in ids) or len(set(ids)) != len(ids):
            raise ToolError("The model sent missing or duplicate tool-call IDs. Try another model.")
        return calls


class Tools:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        #: Where a tool's pictures, sounds and files are kept (content.py).
        self.data_dir = settings.data_dir
        self.local = LocalProcesses(settings)
        self.folders = folders.Folders(store, settings)
        self.node_folders: NodeFolders | None = None
        self.tls = ssl.create_default_context()
        self.apps: McpApps | None = None
        self.a2ui: A2UI | None = None
        #: Runs one `search_memory` call for a person in a chat, with the model the answer
        #: uses (`memory_api.run_search_tool`), which asks the decision again as it runs.
        self.memory_search: (
            Callable[[Person, str, str | None, dict[str, Any]], Awaitable[str]] | None
        ) = None
        #: Runs one `search_chats` call (MV2, `past_chats.run_tool`): its text, and the chats
        #: it quoted.
        self.chats_search: (
            Callable[[Person, str, str | None, dict[str, Any]], Awaitable[tuple[str, list[str]]]]
            | None
        ) = None
        #: Runs one `remember` call (MV3b, `memory_write.run_remember`): what the model is
        #: told, and the note written (kept or offered), if any. The last argument is what the
        #: answer read so far this turn.
        self.remember: (
            Callable[[Person, str, str | None, dict[str, Any], Any], Awaitable[tuple[str, Any]]]
            | None
        ) = None
        #: Row 32: runs one `use_skill` or `read_skill_file` call (`skills.Skills.run`): the
        #: person, the skills the answer listed, the tool, its arguments and the job sites this
        #: answer may run commands on; its status and what the model is told.
        self.skills: SkillRunner | None = None

    @asynccontextmanager
    async def connect(
        self, ids: list[str], person: Person | None = None, folder_ids: list[str] | None = None
    ) -> AsyncIterator[ToolSession]:
        site_ids = [i for i in dict.fromkeys(ids) if i.startswith(SITE_PREFIX)]
        ids = [i for i in ids if not i.startswith(SITE_PREFIX)]
        servers = {s["id"]: s for s in await self.store.tool_servers()}
        if any(i not in servers for i in ids):
            raise ToolError(
                "A selected tool server was removed. Choose tools again in this chat's settings."
            )
        if any(not visible_server(servers[i], person) for i in ids):
            raise ToolError("Only the install owner can use local tool servers.")
        async with AsyncExitStack() as stack:
            session = ToolSession(self, person)
            grants = {
                g["id"]: g for g in await self.store.folder_grants() if folders.visible(g, person)
            }
            local_ids = [i for i in folder_ids or [] if not i.startswith(NODE_FOLDER_PREFIX)]
            node_ids = [
                i for i in dict.fromkeys(folder_ids or []) if i.startswith(NODE_FOLDER_PREFIX)
            ]
            if local_ids and (reason := self.folders.unavailable()):
                raise ToolError(reason)
            for grant_id in dict.fromkeys(local_ids):
                if grant_id not in grants:
                    raise ToolError("A selected folder grant was removed or is unavailable to you.")
                grant = grants[grant_id]
                server = {"id": grant_id, "name": grant["name"], "transport": "folder"}
                for name, description, schema in folders.definitions(bool(grant["writable"])):
                    session.add(server, None, name, schema, description)
            if node_ids or site_ids:
                await self._connect_sites(session, person, node_ids, site_ids)
            for server_id in dict.fromkeys(ids):
                server = servers[server_id]
                try:
                    if server["transport"] == "stdio":
                        transport = self.local.connect(server)
                    else:
                        http = await stack.enter_async_context(
                            httpx2.AsyncClient(
                                verify=self.tls,
                                trust_env=False,
                                follow_redirects=False,
                                timeout=120,
                                headers={"Authorization": f"Bearer {server['token']}"}
                                if server["token"]
                                else {},
                                event_hooks={"response": [_check_response]},
                            )
                        )
                        transport = streamable_http_client(
                            server["url"], http_client=http, max_sse_event_size=MAX_RESPONSE
                        )
                    client = await stack.enter_async_context(
                        Client(
                            transport,
                            read_timeout_seconds=30,
                            cache=None,
                            extensions=[
                                advertise(mcp_ui.EXTENSION, {"mimeTypes": [mcp_ui.MIME]}),
                                advertise(
                                    a2ui_state.EXTENSION,
                                    {
                                        "version": a2ui_state.VERSION,
                                        "catalogIds": [a2ui_state.CATALOG],
                                    },
                                ),
                            ],
                        )
                    )
                    cursor = None
                    seen: set[str] = set()
                    for _ in range(8):
                        page = await client.list_tools(cursor=cursor)
                        for tool in page.tools:
                            schema = tool.input_schema
                            Draft202012Validator.check_schema(schema)
                            if schema.get("type") != "object" or len(json.dumps(schema)) > 32_768:
                                raise ToolError(
                                    "A tool has an unsupported input schema. "
                                    "Ask the server owner to check it."
                                )
                            session.add(
                                server,
                                client,
                                tool.name,
                                schema,
                                tool.description or "",
                                meta=tool.meta or {},
                            )
                            session.descriptors[(server_id, tool.name)] = tool.model_dump(
                                mode="json", by_alias=True, exclude_none=True
                            )
                        cursor = page.next_cursor
                        if cursor is None:
                            break
                        if cursor in seen:
                            raise ToolError(
                                "The tool server repeated a listing page. "
                                "Ask its owner to check it."
                            )
                        seen.add(cursor)
                    else:
                        raise ToolError(
                            "The tool listing has too many pages. Select a smaller server."
                        )
                except ToolError:
                    raise
                except Exception as exc:
                    if reason := known_problem(exc):
                        raise ToolError(f"{server['name']}: {reason}") from None
                    if server["transport"] == "stdio":
                        raise ToolError(
                            f"{server['name']}: the local server did not complete MCP discovery "
                            f"({type(exc).__name__}). Check the program, "
                            "its arguments and dependencies."
                        ) from None
                    raise ToolError(
                        f"Could not list tools from {server['name']} ({type(exc).__name__}). "
                        "Check its address, credential and availability in Tools."
                    ) from None
            yield session

    async def _connect_sites(
        self,
        session: ToolSession,
        person: Person | None,
        node_ids: list[str],
        site_ids: list[str],
    ) -> None:
        """Each machine's servers, through Eugene (J6): one file server per
        machine, its `folder` argument narrowed to the folders this chat
        selected there (J6g), and the job sites' local servers this chat chose.
        A job site's results are hidden from the owner's reading in
        production mode (remote-nodes.md J13a)."""
        if self.node_folders is None:
            raise ToolError("Machines' tools are unavailable in this Workbench.")
        try:
            remote = await self.node_folders.servers(person)
        except folder_io.FolderError as exc:
            raise ToolError(str(exc)) from None
        folders_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
            NODE_FOLDER_PREFIX + str(f["id"]): (entry, f)
            for entry in remote
            if entry["server"] == SITE_FILES
            for f in entry["folders"]
        }
        chosen: dict[str, tuple[dict[str, Any], set[str]]] = {}
        for grant_id in node_ids:
            if grant_id not in folders_by_id:
                raise ToolError("A selected folder grant was removed or is unavailable to you.")
            entry, folder = folders_by_id[grant_id]
            chosen.setdefault(entry["site"], (entry, set()))[1].add(str(folder["name"]))
        targets: list[tuple[dict[str, Any], set[str] | None]] = list(chosen.values())
        by_id = {site_server_id(e["site"], e["server"]): e for e in remote}
        for ident in site_ids:
            if ident not in by_id or by_id[ident]["server"] == SITE_FILES:
                raise ToolError(
                    "A selected machine's server was removed or is unavailable to you. "
                    "Choose tools again."
                )
            targets.append((by_id[ident], None))
        for entry, names in targets:
            site = entry["site"]
            label = entry["label"]
            if not entry.get("available"):
                raise ToolError(f"{label}: {entry.get('reason') or 'its tools are unavailable.'}")
            files = entry["server"] == SITE_FILES
            server = {
                "id": site_server_id(site, entry["server"]),
                "name": f"{label} · {'Files' if files else entry.get('name') or entry['server']}",
                "transport": "site",
                "site": site,
                "label": label,
                "server": entry["server"],
                "jobSite": True,
            }
            try:
                listed = await self.node_folders.list_tools(person, site, entry["server"], label)
            except folder_io.FolderError as exc:
                raise ToolError(f"{label}: {exc}") from None
            for tool in listed:
                schema = tool.get("inputSchema")
                try:
                    if not isinstance(schema, dict):
                        raise ValueError
                    Draft202012Validator.check_schema(schema)
                    if schema.get("type") != "object" or len(json.dumps(schema)) > 32_768:
                        raise ValueError
                except Exception:
                    raise ToolError(
                        f"{label}: a tool has an unsupported input schema. "
                        "Ask the machine's owner to check it."
                    ) from None
                if names is not None:
                    schema = _narrowed(schema, names)
                    if schema is None:
                        continue
                session.add(
                    server,
                    None,
                    tool["name"],
                    schema,
                    str(tool.get("description") or ""),
                    ask=_ask_rule(tool),
                    signed=_signed(tool),
                )


def _ask_rule(tool: dict[str, Any]) -> frozenset[str] | bool | None:
    """Where the site says a call to `tool` must be asked about (2b.3b):
    the workspace names, or a yes or no; None when its `_meta` says neither,
    where every call is asked about."""
    meta = tool.get("_meta")
    rule = meta.get(ASK_META) if isinstance(meta, dict) else None
    if isinstance(rule, bool):
        return rule
    if isinstance(rule, list) and all(isinstance(name, str) for name in rule):
        return frozenset(rule)
    return None


def _signed(tool: dict[str, Any]) -> bool:
    """Whether the site checks the person's signature on calls to `tool`."""
    meta = tool.get("_meta")
    return isinstance(meta, dict) and meta.get(SIGNED_META) is True


def _narrowed(schema: dict[str, Any], names: set[str]) -> dict[str, Any] | None:
    """The file server's schema with its `folder` argument cut to the folders
    this chat selected; None when the tool offers none of them."""
    folder = (schema.get("properties") or {}).get("folder")
    if not isinstance(folder, dict) or not isinstance(folder.get("enum"), list):
        return None
    kept = [name for name in folder["enum"] if name in names]
    if not kept:
        return None
    return {
        **schema,
        "properties": {**schema["properties"], "folder": {**folder, "enum": kept}},
    }


async def _check_response(response: httpx2.Response) -> None:
    if response.is_redirect:
        raise ToolError("The MCP address redirected. Enter its final address in Tools.")
    if response.status_code in (401, 403):
        raise ToolError("The MCP server refused this credential. Replace the connection in Tools.")
    if "text/event-stream" not in response.headers.get("content-type", ""):
        # The SDK bounds each SSE frame. Bound JSON before decoding as well.
        response.stream = _LimitedBody(response.stream)


class _LimitedBody(httpx2.AsyncByteStream):
    def __init__(self, stream: Any) -> None:
        self.stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for part in self.stream:
            size += len(part)
            if size > MAX_RESPONSE:
                raise ToolError("The MCP response exceeded 8 MiB. Ask for a smaller result.")
            yield part

    async def aclose(self) -> None:
        await self.stream.aclose()


#: Waits for the person to sign a call a job site holds (J14b): what to send
#: again (`SiteCallApproval`), or None when they declined.
Signer = Callable[[dict[str, Any], Held], Awaitable[dict[str, Any] | None]]


class ToolSession:
    def __init__(self, tools: Tools, person: Person | None) -> None:
        self.store = tools.store
        self.local = tools.local
        self.folders = tools.folders
        self.node_folders = tools.node_folders
        self.person = person
        self.apps = tools.apps
        self.a2ui = tools.a2ui
        self.memory_search = tools.memory_search
        self.memory_calls = 0
        self.chats_search = tools.chats_search
        self.chats_calls = 0
        self.remember = tools.remember
        self.remember_calls = 0
        self.skills = tools.skills
        #: The skills this answer lists (row 32), as the answer began.
        self.skill_listing: list[Any] = []
        #: What the answer has read so far this turn (`memory_write.TurnContext`), for
        #: `remember`'s write policy.
        self.turn_context: Callable[[], Any] = lambda: None
        #: The chat and model an answer's own tools run for (`add_memory_search`).
        self.chat_id: str | None = None
        self.model: str | None = None
        self.metadata: dict[str, dict[str, Any]] = {}
        self.descriptors: dict[tuple[str, str], dict[str, Any]] = {}
        self.model_names: set[str] = set()
        self.definitions: list[dict[str, Any]] = []
        self.tools: dict[str, tuple[dict[str, Any], Any, str, dict[str, Any]]] = {}
        #: Each job-site tool's rule for asking (`_ask_rule`); absent for every
        #: other tool, which is asked about each time.
        self.asks: dict[str, frozenset[str] | bool | None] = {}
        #: The job-site tools whose site checks the person's signature (J14b).
        self.signed: set[str] = set()

    def add(
        self,
        server: dict[str, Any],
        client: Any,
        tool: str,
        schema: dict[str, Any],
        description: str,
        *,
        ask: frozenset[str] | bool | None = None,
        signed: bool = False,
        meta: dict[str, Any] | None = None,
    ) -> None:
        files = server["transport"] == "folder" or server.get("server") == SITE_FILES
        prefix = "files_" if files else "mcp_"
        name = prefix + hashlib.sha256(f"{server['id']}:{tool}".encode()).hexdigest()[:48]
        if name in self.tools or len(self.tools) >= MAX_TOOLS:
            raise ToolError(
                "The selected connections offer too many or duplicate tools. Select fewer."
            )
        self.tools[name] = (server, client, tool, schema)
        self.asks[name] = ask
        self.metadata[name] = meta or {}
        try:
            visible = mcp_ui.visibility(self.metadata[name])
        except ValueError:
            raise ToolError("The tool server supplied invalid UI visibility.") from None
        if signed:
            self.signed.add(name)
        if "model" not in visible:
            return
        self.model_names.add(name)
        self.definitions.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"{server['name']}: {tool}. {description}"[:4000],
                    "parameters": schema,
                },
            }
        )

    def add_memory_search(self, chat_id: str | None = None, model: str | None = None) -> None:
        """Offer `search_memory` (MV1): Workbench runs it over the asking person's own notes,
        without asking them first (it reads only what is theirs and changes nothing)."""
        self.chat_id, self.model = chat_id or self.chat_id, model or self.model
        if self.memory_search is None or MEMORY_TOOL_NAME in self.tools:
            return
        server = {"id": MEMORY_SERVER, "name": "Memory", "transport": "memory"}
        self.tools[MEMORY_TOOL_NAME] = (
            server,
            None,
            MEMORY_TOOL_NAME,
            memory.SEARCH_TOOL_SCHEMA,
        )
        self.asks[MEMORY_TOOL_NAME] = False
        self.metadata[MEMORY_TOOL_NAME] = {}
        self.model_names.add(MEMORY_TOOL_NAME)
        self.definitions.append(
            {
                "type": "function",
                "function": {
                    "name": MEMORY_TOOL_NAME,
                    "description": memory.SEARCH_TOOL_DESCRIPTION,
                    "parameters": memory.SEARCH_TOOL_SCHEMA,
                },
            }
        )

    def add_skills(self, listing: list[Any]) -> None:
        """Offer `use_skill` and `read_skill_file` (row 32) for the skills `listing` holds:
        Workbench runs them over the asking person's skills without asking first (they read and
        change nothing). Each skill's name and description are in `use_skill`'s description,
        never in the system message."""
        from .skills import USE_SKILL, definitions

        if self.skills is None or not listing or USE_SKILL in self.tools:
            return
        self.skill_listing = list(listing)
        server = {"id": SKILLS_SERVER, "name": "Skills", "transport": "skills"}
        for definition in definitions(self.skill_listing):
            function = definition["function"]
            self.tools[function["name"]] = (server, None, function["name"], function["parameters"])
            self.asks[function["name"]] = False
            self.metadata[function["name"]] = {}
            self.model_names.add(function["name"])
            self.definitions.append(definition)

    def _command_sites(self) -> list[str]:
        """The job sites this answer may run a command on: those whose tools offer `run_command`."""
        return sorted(
            {
                str(server.get("label") or server.get("site"))
                for server, _, tool, _ in self.tools.values()
                if server.get("transport") == "site" and tool == "run_command"
            }
        )

    def add_remember(self, chat_id: str, model: str | None, context: Callable[[], Any]) -> None:
        """Offer `remember` (MV3b): Workbench runs it, without asking the person first, and
        decides in code, under their write policy, whether the note is kept, offered to them
        or dropped. The answer shows a receipt with Undo."""
        self.chat_id, self.model = chat_id, model or self.model
        self.turn_context = context
        if self.remember is None or REMEMBER_TOOL_NAME in self.tools:
            return
        from .memory_suggest import remember_tool

        definition = remember_tool()
        schema = definition["function"]["parameters"]
        server = {"id": WRITE_SERVER, "name": "Memory", "transport": "memory-write"}
        self.tools[REMEMBER_TOOL_NAME] = (server, None, REMEMBER_TOOL_NAME, schema)
        self.asks[REMEMBER_TOOL_NAME] = False
        self.metadata[REMEMBER_TOOL_NAME] = {}
        self.model_names.add(REMEMBER_TOOL_NAME)
        self.definitions.append(definition)

    def add_chats_search(self, chat_id: str, model: str | None) -> None:
        """Offer `search_chats` (MV2): Workbench runs it over the asking person's own past
        chats (not this one), without asking them first, as `search_memory`."""
        self.chat_id, self.model = chat_id, model or self.model
        if self.chats_search is None or CHATS_TOOL_NAME in self.tools:
            return
        from .past_chats import TOOL_DESCRIPTION, TOOL_SCHEMA

        server = {"id": CHATS_SERVER, "name": "Past chats", "transport": "past-chats"}
        self.tools[CHATS_TOOL_NAME] = (server, None, CHATS_TOOL_NAME, TOOL_SCHEMA)
        self.asks[CHATS_TOOL_NAME] = False
        self.metadata[CHATS_TOOL_NAME] = {}
        self.model_names.add(CHATS_TOOL_NAME)
        self.definitions.append(
            {
                "type": "function",
                "function": {
                    "name": CHATS_TOOL_NAME,
                    "description": TOOL_DESCRIPTION,
                    "parameters": TOOL_SCHEMA,
                },
            }
        )

    def prepare(self, call: dict[str, Any]) -> dict[str, Any]:
        name = call["function"]["name"]
        if name not in self.model_names:
            raise ToolError("The model asked for a tool that was not offered. Try another model.")
        server, _, tool, schema = self.tools[name]
        try:
            arguments = json.loads(
                call["function"]["arguments"], object_pairs_hook=_object, parse_constant=_constant
            )
            Draft202012Validator(schema, registry=Registry(retrieve=_no_resource)).validate(
                arguments
            )
            if not isinstance(arguments, dict):
                raise ValueError
        except Exception:
            raise ToolError(
                f"The model sent invalid arguments for {tool}. No tool ran. Try another model."
            ) from None
        rule = self.asks.get(name)
        signed = name in self.signed
        if signed:
            # The site holds the call for the person's own signature and says
            # what to sign (J14b): nothing is asked here first.
            ask = False
        elif isinstance(rule, bool):
            ask = rule
        elif isinstance(rule, frozenset):
            ask = arguments.get("folder") in rule
        else:
            # Workbench's own folders (J75), other MCP servers, and a job-site
            # tool whose `_meta` says nothing: every call is asked about.
            ask = True
        return {
            "id": call["id"],
            "name": name,
            "serverId": server["id"],
            "serverName": server["name"],
            "tool": tool,
            "arguments": arguments,
            "status": "pending",
            "result": None,
            # Asked about here, or run under the site's `allow` (J70, J71).
            "ask": ask,
            # The site checks the person's signature itself (J14b).
            "signed": signed,
            **(
                {
                    "jobSite": True,
                    "site": server.get("site"),
                    "label": server.get("label"),
                    "mode": "production",
                }
                if server.get("jobSite")
                else {}
            ),
        }

    async def execute(
        self, call: dict[str, Any], signer: Signer | None = None, keeper: Keeper | None = None
    ) -> None:
        """Run one prepared call. A job site that holds it for the person's
        signature (J14b) is answered by `signer`, which waits for the person
        and returns what to send again, or None when they declined. A result's
        pictures, sounds and files are kept by `keeper` (content.py)."""
        server, client, tool, _ = self.tools[call["name"]]
        if server["transport"] == "memory":
            self.memory_calls += 1
            if self.memory_search is None or self.person is None:
                call.update(status="failed", result="Searching notes is unavailable.")
            elif self.memory_calls > memory.SEARCH_TOOL_CALLS:
                call.update(
                    status="failed",
                    result=(
                        f"This answer has searched the notes {memory.SEARCH_TOOL_CALLS} times, "
                        "the most it may. Answer with what was found."
                    ),
                )
            else:
                text = await self.memory_search(
                    self.person, self.chat_id or "", self.model, call["arguments"]
                )
                call.update(status="done", result=text)
            return
        if server["transport"] == "skills":
            if self.skills is None or self.person is None:
                call.update(status="failed", result="Skills are unavailable.")
            else:
                state, text = await self.skills(
                    self.person, self.skill_listing, tool, call["arguments"], self._command_sites()
                )
                call.update(status=state, result=text)
            return
        if server["transport"] == "memory-write":
            from .memory_write import REMEMBER_CALLS

            self.remember_calls += 1
            if self.remember is None or self.person is None or self.chat_id is None:
                call.update(status="failed", result="Keeping notes is unavailable.")
            elif self.remember_calls > REMEMBER_CALLS:
                call.update(
                    status="failed",
                    result=f"This answer has called remember {REMEMBER_CALLS} times, the most "
                    "it may.",
                )
            else:
                text, note = await self.remember(
                    self.person, self.chat_id, self.model, call["arguments"], self.turn_context()
                )
                call.update(status="done", result=text)
                if note is not None:
                    # The receipt: the note, and whether it was kept or offered (MV3b).
                    call.update(note=note.id, noteText=note.text, kept=bool(note.auto))
            return
        if server["transport"] == "past-chats":
            from .past_chats import TOOL_CALLS

            self.chats_calls += 1
            if self.chats_search is None or self.person is None or self.chat_id is None:
                call.update(status="failed", result="Searching past chats is unavailable.")
            elif self.chats_calls > TOOL_CALLS:
                call.update(
                    status="failed",
                    result=(
                        f"This answer has searched past chats {TOOL_CALLS} times, the most "
                        "it may. Answer with what was found."
                    ),
                )
            else:
                text, quoted = await self.chats_search(
                    self.person, self.chat_id, self.model, call["arguments"]
                )
                call.update(status="done", result=text, chats=quoted)
            return
        if server["transport"] == "site":
            try:
                if self.node_folders is None:
                    raise folder_io.FolderError("Machines' tools are unavailable.")
                approval: dict[str, Any] | None = None
                while True:
                    try:
                        result, is_error, meta = await self.node_folders.call_tool(
                            self.person,
                            server["site"],
                            server["server"],
                            tool,
                            call["arguments"],
                            # Only an approved call is ever executed when it
                            # asks (J72).
                            asked=bool(call.get("ask")),
                            approval=approval,
                        )
                        break
                    except Held as held:
                        if signer is None:
                            raise folder_io.FolderError(held.message) from None
                        approval = await signer(call, held)
                        if approval is None:
                            call.pop("held", None)
                            call.update(
                                status="declined",
                                result=call.get("result")
                                or "The person did not sign this call. It did not run.",
                            )
                            return
                call.pop("held", None)
                if meta.get("windowUntil"):
                    call["windowUntil"] = meta["windowUntil"]
                if meta["jobSite"]:
                    # Kept with the result: whether the owner may ever read it
                    # depends on the mode it was made in (J18, not retroactive).
                    call.update(
                        jobSite=True,
                        site=server.get("site"),
                        label=server.get("label"),
                        mode=meta["mode"],
                    )
                await self._settle(
                    call, result.get("content"), result.get("structuredContent"), is_error, keeper
                )
            except folder_io.WriteUncertain as exc:
                call.update(status="uncertain", result=str(exc))
            except folder_io.FolderError as exc:
                call.update(status="failed", result=str(exc))
            return
        if server["transport"] == "folder":
            try:
                if self.folders is None:
                    raise folder_io.FolderError("Folder support is unavailable.")
                file_result = await self.folders.execute(
                    server["id"], self.person, tool, call["arguments"]
                )
                call.update(status="done", result=json.dumps(file_result, ensure_ascii=False))
            except folder_io.WriteUncertain as exc:
                call.update(status="uncertain", result=str(exc))
            except folder_io.FolderError as exc:
                call.update(status="failed", result=str(exc))
            return
        if call["serverId"] not in {s["id"] for s in await self.store.tool_servers()}:
            call.update(status="cancelled", result="The server was removed. This call did not run.")
            return
        if server["transport"] == "stdio":
            person = await self.store.person(self.person.sub) if self.person else None
            reason = unavailable(self.local.settings)
            if reason or not visible_server(server, person):
                call.update(
                    status="cancelled",
                    result=reason or "Local tool access was removed. This call did not run.",
                )
                return
        try:
            # Session API sends exactly once: no elicitation or header-mismatch retry.
            async with asyncio.timeout(120):
                result = await client.session.call_tool(
                    tool, call["arguments"], read_timeout_seconds=120
                )
            blocks = [
                block.model_dump(mode="json", by_alias=True, exclude_none=True)
                for block in result.content
            ]
            await self._settle(
                call, blocks, result.structured_content, bool(result.is_error), keeper
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = known_problem(exc) or f"The connection failed ({type(exc).__name__})."
            call.update(
                status="uncertain",
                result=f"{reason} The tool call did not return a result. "
                "It may have acted. Check the server before trying again.",
            )
            return
        # UI publication cannot turn a confirmed tool result into an uncertain call.
        if self.apps is not None and keeper is not None:
            await self.apps.capture(self, call, result, keeper)
        if self.a2ui is not None and keeper is not None:
            await self.a2ui.capture(self, call, result, keeper)

    @staticmethod
    async def _settle(
        call: dict[str, Any],
        blocks: Any,
        structured: Any,
        is_error: bool,
        keeper: Keeper | None,
    ) -> None:
        """A finished call's result: its blocks (content.py, one path for every
        server) and the text the model is sent again."""
        content, text = await normalize(call["id"], blocks, structured, keeper)
        call.update(
            status="failed" if is_error else "done",
            content=content,
            contentVersion=CONTENT_VERSION,
            result=text or "The tool returned no content.",
        )
