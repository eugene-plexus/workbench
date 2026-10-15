"""Real stdio MCP app fixture; each launch and effect is recorded outside the host."""

import json
import os
import sys
from pathlib import Path

from mcp.server import MCPServer

record = Path(sys.argv[1])


def event(value: dict[str, object]) -> None:
    with record.open("a", encoding="utf-8") as output:
        output.write(json.dumps(value) + "\n")


event({"event": "start", "pid": os.getpid()})
server = MCPServer("Local app fixture", log_level="ERROR")


@server.tool(meta={"ui": {"resourceUri": "ui://fixture/app.html"}})
def show(text: str) -> str:
    return "Local app fallback: " + text


@server.tool(meta={"ui": {"visibility": ["app"]}})
def increment(value: int) -> str:
    event({"event": "effect", "value": value})
    return "Local incremented"


@server.resource("ui://fixture/app.html", mime_type="text/html;profile=mcp-app")
def document() -> str:
    return "<!doctype html><html><body>Local app</body></html>"


server.run()
