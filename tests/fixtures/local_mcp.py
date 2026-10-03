"""A real stdio MCP server. Records observable starts, arguments and calls."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp.server import MCPServer

record = Path(sys.argv[1])
mode = sys.argv[2] if len(sys.argv) > 2 else "normal"
with record.open("a", encoding="utf-8") as handle:
    handle.write(
        json.dumps(
            {
                "event": "start",
                "pid": os.getpid(),
                "env": dict(os.environ),
                "cwd": os.getcwd(),
                "args": sys.argv[3:],
            }
        )
        + "\n"
    )

server = MCPServer("Local fixture", log_level="ERROR")

if mode == "child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    with record.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "child", "pid": child.pid}) + "\n")


@server.tool()
async def echo(text: str) -> str:
    def save() -> None:
        with record.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "call", "text": text}) + "\n")

    await asyncio.to_thread(save)
    if mode == "slow":
        await asyncio.sleep(120)
    return text


if mode == "exit":
    print("fixture-private-error", file=sys.stderr)
    raise SystemExit(7)
if mode == "hang":
    import time

    time.sleep(120)
else:
    server.run()
