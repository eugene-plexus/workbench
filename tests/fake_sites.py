"""A fake of Eugene's `/oidc/sites/*` routes and the site hosts behind them.

Each machine has one file server (`files`) whose tools take a `folder`
argument listing only the folders a person may use, and for `write_text`
only those they may change (Job Sites J6g); a job site may also have local
servers. Answers are `SiteMcpAnswer`s holding MCP 2026-07-28 responses.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .conftest import CLIENT_ID, CLIENT_SECRET

PATH = {"type": "string", "minLength": 1}


def file_tools(readable: list[str], writable: list[str]) -> list[dict[str, Any]]:
    tools = []
    for name, names, extra in (
        ("list_directory", readable, {}),
        ("read_text", readable, {}),
        (
            "write_text",
            writable,
            {"text": {"type": "string"}, "expectedSha256": {"type": "string"}},
        ),
    ):
        if not names:
            continue
        properties = {"folder": {"type": "string", "enum": names}, "path": PATH, **extra}
        tools.append(
            {
                "name": name,
                "description": f"{name} in a folder.",
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": name != "write_text"},
            }
        )
    return tools


def install(server: FastAPI, fake: Any, state: dict[str, Any]) -> None:
    """`state`:
    - `folders`: [{id, node, name, writable, people: {sub: may write}}];
    - `local`: [{node, server, name, people: [sub], tools: [Tool]}];
    - `available`, `reason`, `jobSite`, `mode`, `allowed`;
    - `content`: the one file's text; `calls`: every tools/call body.
    """
    state.setdefault("local", [])
    state.setdefault("calls", [])
    state.setdefault("allowed", True)
    state.setdefault("available", True)

    def client_ok(request: Request) -> bool:
        expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        return request.headers.get("authorization") == "Basic " + expected

    def who(body: dict[str, Any]) -> str | None:
        sub = fake.refresh_tokens.get(body["refreshToken"])
        if not state["allowed"] or sub in fake.disabled:
            return None
        return str(sub) if sub else None

    def mine(sub: str | None, node: str) -> list[dict[str, Any]]:
        return [f for f in state["folders"] if f["node"] == node and sub in f["people"]]

    def answer(response: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "done",
            "response": {"jsonrpc": "2.0", "id": 1, **response},
            "jobSite": bool(state.get("jobSite")),
            "installMode": state.get("mode", "production"),
        }

    @server.post("/oidc/sites/servers")
    async def servers(request: Request) -> Any:
        assert client_ok(request)
        body = await request.json()
        sub = who(body)
        listed = []
        for node in sorted({f["node"] for f in state["folders"]}):
            folders = [
                {"id": f["id"], "name": f["name"], "writable": f["people"][sub] and f["writable"]}
                for f in mine(sub, node)
            ]
            if folders:
                listed.append(
                    {
                        "node": node,
                        "server": "files",
                        "name": "Files",
                        "kind": "files",
                        "jobSite": bool(state.get("jobSite")),
                        "available": state["available"],
                        "reason": None if state["available"] else f"{node} is offline.",
                        "folders": folders,
                    }
                )
        for local in state["local"]:
            if sub in local["people"]:
                listed.append(
                    {
                        "node": local["node"],
                        "server": local["server"],
                        "name": local["name"],
                        "kind": "local",
                        "jobSite": True,
                        "available": state["available"],
                        "reason": None,
                        "folders": [],
                    }
                )
        return {"servers": listed, "installMode": {"mode": state.get("mode", "production")}}

    @server.post("/oidc/sites/cancel")
    async def cancel(request: Request) -> Any:
        return JSONResponse(None, status_code=204)

    @server.post("/oidc/sites/mcp")
    async def mcp(request: Request) -> Any:
        assert client_ok(request)
        body = await request.json()
        sub = who(body)
        meta = body["request"]["params"]["_meta"]
        assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
        method = body["request"]["method"]
        node, server_id = body["node"], body["server"]
        if server_id != "files":
            local = next(
                (
                    s
                    for s in state["local"]
                    if s["node"] == node and s["server"] == server_id and sub in s["people"]
                ),
                None,
            )
            if local is None:
                return JSONResponse({"detail": {"detail": "Not granted."}}, status_code=403)
            if method == "tools/list":
                return answer({"result": {"tools": local["tools"]}})
            state["calls"].append(body)
            name = body["request"]["params"]["name"]
            args = body["request"]["params"].get("arguments") or {}
            text = f"{name}: {json.dumps(args, sort_keys=True)}"
            return answer({"result": {"content": [{"type": "text", "text": text}]}})
        folders = mine(sub, node)
        if not folders:
            return JSONResponse(
                {"detail": {"detail": "This folder is no longer granted."}}, status_code=403
            )
        readable = [f["name"] for f in folders]
        writable = [f["name"] for f in folders if f["people"][sub] and f["writable"]]
        if method == "tools/list":
            return answer({"result": {"tools": file_tools(readable, writable)}})
        state["calls"].append(body)
        params = body["request"]["params"]
        args = dict(params.get("arguments") or {})
        folder = args.pop("folder", None)
        allowed = writable if params["name"] == "write_text" else readable
        if folder not in allowed:
            return {
                "status": "failed",
                "message": f"You have not been given a folder named {folder!r}.",
                "jobSite": bool(state.get("jobSite")),
                "installMode": state.get("mode", "production"),
            }
        if params["name"] == "write_text":
            state["content"] = args["text"]
        result = {"text": state["content"], "sha256": "a" * 64}
        return answer(
            {
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result)}],
                    "structuredContent": result,
                    "isError": False,
                }
            }
        )
