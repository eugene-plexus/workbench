"""Stdio A2UI producer; records process launches and counted external effects."""

import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from eugene_plexus_workbench import a2ui_state as ui

record, payload = Path(sys.argv[1]), Path(sys.argv[2])


def append(value: dict[str, Any]) -> None:
    with record.open("a", encoding="utf-8") as output:
        output.write(json.dumps(value) + "\n")


append({"event": "start", "pid": os.getpid()})
server = MCPServer("Local A2UI fixture", log_level="ERROR")


@server.tool(
    meta={ui.EXTENSION: {"version": ui.VERSION, "catalogId": ui.CATALOG, "actionTool": "handle_ui"}}
)
def show(text: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text="Local form: " + text)],
        _meta={ui.EXTENSION: json.loads(payload.read_text(encoding="utf-8"))},
    )


@server.tool(meta={"ui": {"visibility": ["app"]}})
def handle_ui(event: dict[str, Any], dataModel: Any = None) -> str:
    append({"event": "effect", "payload": event, "data": dataModel})
    return "Saved locally"


server.run()
