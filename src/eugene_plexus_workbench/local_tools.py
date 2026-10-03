"""Owner-selected processes, confined by the launcher's OS account.

These programs are trusted code within Workbench's account, not sandboxes
for different people's files. The MCP SDK owns stdio and process shutdown.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.client.stdio import StdioServerParameters, stdio_client

from .settings import Settings

MAX_PROCESSES = 4
_PRIVATE_HOME = {
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "TEMP",
    "TMP",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
}


class LocalToolError(Exception):
    """An observed process failure, safe to show without command secrets."""


def unavailable(settings: Settings) -> str | None:
    if settings.account_kind in {"windows_service", "systemd"}:
        return None
    return (
        "Local servers need Workbench's own OS account. Use a Windows service install "
        "or a Linux system install, update Eugene, then restart Workbench."
    )


def validate_process(command: str, args: list[str], environment: dict[str, str]) -> None:
    if not Path(command).is_absolute() or any(ord(c) < 32 for c in command):
        raise ValueError("Use the full path to the server executable.")
    if os.name == "nt" and Path(command).suffix.lower() != ".exe":
        raise ValueError("On Windows, name an .exe file; use node.exe or python.exe for scripts.")
    if len(args) > 64 or sum(len(a) for a in args) > 16_384 or any("\x00" in a for a in args):
        raise ValueError("Use at most 64 arguments, 16384 characters total, without NUL bytes.")
    if len(environment) > 64 or sum(len(k) + len(v) for k, v in environment.items()) > 32_768:
        raise ValueError("Use at most 64 environment values, 32768 characters total.")
    for key, value in environment.items():
        if (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            or key.upper().startswith("EUGENE_PLEXUS_")
            or key.upper() in _PRIVATE_HOME
            or "\x00" in value
        ):
            raise ValueError(
                "Use environment names for this server only; home and Eugene names are reserved."
            )


def environment_for(workspace: Path, command: str, extra: dict[str, str]) -> dict[str, str]:
    # The SDK merges its small default list. Replace every inherited path to
    # the app's home, and supply only a system PATH plus this executable's dir.
    system = os.environ.get("SYSTEMROOT", r"C:\Windows")
    path = os.pathsep.join(
        [str(Path(command).parent), str(Path(system) / "System32"), system]
        if os.name == "nt"
        else [str(Path(command).parent), "/usr/local/bin", "/usr/bin", "/bin"]
    )
    env = {"PATH": path, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", **extra}
    env.update({key: str(workspace) for key in _PRIVATE_HOME})
    if os.name == "nt":
        env["HOMEDRIVE"] = workspace.drive
        env["HOMEPATH"] = str(workspace)[len(workspace.drive) :]
    return env


def _parameters(settings: Settings, server: dict[str, Any]) -> StdioServerParameters:
    command = server["command"]
    args = server["args"]
    if os.name != "nt":
        # Preserve observed missing/permission errors before the guard starts.
        Path(command).stat()
        if not os.access(command, os.X_OK):
            raise PermissionError
        args = ["-I", "-u", str(Path(__file__).with_name("_stdio_guard.py")), command, *args]
        command = sys.executable
    workspace = (settings.data_dir / "tools" / server["id"]).resolve()
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    return StdioServerParameters(
        command=command,
        args=args,
        cwd=workspace,
        env=environment_for(workspace, server["command"], server["environment"]),
    )


class LocalProcesses:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.active = 0

    @asynccontextmanager
    async def connect(self, server: dict[str, Any]) -> AsyncIterator[Any]:
        if reason := unavailable(self.settings):
            raise LocalToolError(reason)
        if self.active >= MAX_PROCESSES:
            raise LocalToolError(
                "Four local servers are running. Stop an answer or wait for it to finish."
            )
        self.active += 1
        try:
            params = await asyncio.to_thread(_parameters, self.settings, server)
            # Untrusted stderr may contain environment credentials. Do not send
            # it to shared Eugene logs or an unbounded file in the app account.
            with open(os.devnull, "w", encoding="utf-8") as errors:  # noqa: ASYNC230
                async with stdio_client(params, errlog=errors) as streams:
                    yield streams
        finally:
            self.active -= 1
