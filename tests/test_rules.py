"""Project rules this repo keeps, checked as text because the defect is
which function the source names (specs CLAUDE.md)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "eugene_plexus_workbench"


def _sources() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "_generated" not in p.parts]


def test_durations_use_perf_counter_never_monotonic() -> None:
    """On the Python the installers provision, Windows `monotonic()` ticks
    every 15.6 ms."""
    offenders = [
        str(p) for p in _sources() if re.search(r"time\.monotonic\(", p.read_text("utf-8"))
    ]
    assert offenders == []


def test_every_http_client_ignores_the_environments_proxy() -> None:
    """Workbench dials only this install: its gateway and its agent."""
    for path in _sources():
        for call in ast.walk(ast.parse(path.read_text("utf-8"))):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "httpx"
                and call.func.attr in {"AsyncClient", "Client"}
            ):
                assert any(
                    kw.arg == "trust_env"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is False
                    for kw in call.keywords
                ), f"{path.name}:{call.lineno}: HTTP client must disable environment proxies"


def test_the_rules_read_the_sources() -> None:
    assert any(p.name == "hub.py" for p in _sources())
