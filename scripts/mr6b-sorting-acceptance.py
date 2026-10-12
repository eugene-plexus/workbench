"""Sorting what a search finds (MR6b), live: a result sorter, through the real stack.

The unit checks drive Workbench against a fake gateway whose sorter reads at a rate the test sets.
What only a live run shows is that a real result sorter (Qwen3-Reranker-0.6B, llama.cpp b10948 on
the processor), reached through a real inference driver and a real gateway, is sent what the design
says it is sent (the best ten passages of each way of searching, each as its headings, a new line
and its words); that the order it gives is the order a person's question gets; that the passages
are found more often, and higher, than in the order the search found them (the gate); that the
default Sort time of 3 s does not sort on this processor and says so with the numbers it was decided
by; that a sorter stopped in the middle of a question never fails the question; and that automatic
never hands the text of a person's documents to a sorter outside the machines.

The questions, the documents and the scoring are MR5's: Eugene's own 76 design documents as they
are at specs c5f725d (2,810 passages, exported from git, so the run does not depend on a copy), the
sixty questions of `mr5-meaning-questions.json` (each labelled with the section that answers it) and
MR5's recall@5, MRR@5 and paired bootstrap. The hybrid order (the search as MR5 left it) is the
same Workbench with the sorter turned off, and it must reproduce MR5's own record to the digit
before anything is compared with it.

  0. isolated: every ambient EUGENE_PLEXUS_* variable dropped, every port chosen by the OS, the
     engines on the CPU, the page that Chrome opens built from the source at HEAD
  1. the stack: a search model engine, a result sorter engine behind a runtime the stand-in agent
     keeps (so that a stopped sorter is a stopped runtime, as the agent reports it), a sorter that
     runs outside the machines (a stand-in for a cloud account, listed `external`), each behind a
     real inference driver; one real gateway; Workbench; three people
  2. Eugene's design documents into My documents, with the sorter turned off, until every passage
     has its meaning (or the index of an earlier run, with --reuse-data)
  3. off: the hybrid order (and the words and the meaning alone) reproduces MR5's record; the screen
     and Test a question say sorting is off, quietly, and the sorter is asked nothing
  4. the default Sort time: the sorter is measured by a quick test, it is too slow for 3 s, the
     passages go in the hybrid order, and the screen, Test a question and the two settings say why
     with the rate and the seconds
  5. the gate: Sort time raised, the sixty questions sorted; recall@5 and MRR@5 against hybrid with
     paired intervals; what the sorter was sent (heading, new line, words: of real passages in the
     index), what it said, and that the places the screen shows are the places its own scores give
  6. composition: a small library in which every passage is a file, so that the best ten of each
     way are exactly known: the sorter is given exactly those, each once, and no more
  7. a chat answered by the real chat model: Sources still name the files and say nothing of the
     sorter, with it sorting and with it too slow
  8. Chrome: the Documents screen's mode line and notice, Test a question's reason and its places
  9. a named sorter, one that is not a sorter, one that is not there, a second sorter (bge)
 10. the sorter stopped in the middle of a sort, then for good, then brought back
 11. outside the machines: automatic never takes it, stopped or not, allowed or not; a named one
     is used only when the owner has allowed it
 12. teardown by pid; no owned port still listening

**Safe beside a live install**: every ambient EUGENE_PLEXUS_* variable is dropped, every port is
chosen by the OS (never the live install's 8079-8083 or 8279-8283), the engines run on the CPU only
(-ngl 0), every child is in a job object that ends with this process, and teardown is by pid. Never
`pkill -f eugene_plexus_`: this box is a worker node.

Run with the Workbench interpreter (it has httpx, fastapi, uvicorn, joserfc, numpy):
  workbench/.venv/Scripts/python.exe workbench/scripts/mr6b-sorting-acceptance.py
  ... --reuse-data DIR        skip the import, using the index of an earlier run
  ... --stages budget,stopped only these stages (the stack, and the index, are always made)
  ... --questions 12          only the first twelve questions (the gate is then not judged)
  EP_WORKBENCH=DIR            Workbench from another tree (the sabotage runs use a broken copy)
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import http.client
import io
import json
import math
import os
import re
import secrets
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tarfile
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlencode, urlsplit

import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from joserfc import jwt
from joserfc.jwk import RSAKey

T0 = time.perf_counter()

ROOT = Path(os.environ.get("EP_ROOT", "D:/py/eugene-plexus"))
HERE = Path(__file__).resolve().parent.parent
#: The Workbench repo this script lives in (its page, its questions), and the source under test:
#: the same, or a copy with something broken in it.
REPO = HERE if (HERE / "src" / "eugene_plexus_workbench").is_dir() else ROOT / "workbench"
WB = Path(os.environ.get("EP_WORKBENCH", str(REPO)))
WB_PY = Path(
    os.environ.get("EP_WORKBENCH_PY", str(ROOT / "workbench" / ".venv" / "Scripts" / "python.exe"))
)
GW_PY = ROOT / "gateway" / ".venv" / "Scripts" / "python.exe"
DRV_PY = ROOT / "inference-driver" / ".venv" / "Scripts" / "python.exe"
SCRATCH = Path(
    os.environ.get(
        "EP_SCRATCH",
        "C:/Users/troyc/AppData/Local/Temp/claude/d--py-eugene-plexus-specs/"
        "8fdf4703-5901-4dea-a989-c9fe897eef3c/scratchpad",
    )
)
TEMP = Path(os.environ.get("TEMP", "C:/Users/troyc/AppData/Local/Temp"))
LLAMA = Path(
    os.environ.get(
        "EP_LLAMA_SERVER", "C:/Users/troyc/.eugene-plexus/engines/llama_cpp/b10948/llama-server.exe"
    )
)
HOME_MODELS = Path("C:/Users/troyc/.eugene-plexus/acceptance-models")
GGUF = SCRATCH / "rag" / "dl" / "gguf"


def model_file(env: str, *candidates: Path) -> Path:
    """The model a run uses: the environment's, else the first candidate that is there."""
    if os.environ.get(env):
        return Path(os.environ[env])
    return next((c for c in candidates if c.exists()), candidates[0])


CHAT_FILE = model_file(
    "EP_MODEL_FILE",
    TEMP / "ep-s9-model" / "Qwen3-0.6B-Q4_K_M.gguf",
    HOME_MODELS / "Qwen3-0.6B-Q4_K_M.gguf",
)
EMBED_FILE = model_file(
    "EP_QWEN_EMBED",
    HOME_MODELS / "Qwen3-Embedding-0.6B-Q8_0.gguf",
    GGUF / "Qwen3-Embedding-0.6B-Q8_0.gguf",
)
SORTER_FILE = model_file(
    "EP_QWEN_RERANK",
    HOME_MODELS / "qwen3-reranker-0.6b-q8_0.gguf",
    GGUF / "qwen3-reranker-0.6b-q8_0.gguf",
)
BGE_FILE = model_file(
    "EP_BGE_RERANK",
    HOME_MODELS / "bge-reranker-v2-m3-Q8_0.gguf",
    GGUF / "bge-reranker-v2-m3-Q8_0.gguf",
)
#: The 76 design documents of the run of record are the ones in specs at this commit.
SPECS_PIN = "c5f725d"
QUESTIONS = Path(
    os.environ.get("EP_QUESTIONS", str(REPO / "scripts" / "mr5-meaning-questions.json"))
)
THREADS = os.environ.get("EP_EMBED_THREADS", "12")

CHAT = "mr6b-chat"
EMBED = "Qwen3-Embedding-0.6B-Q8_0"
SORTER = "Qwen3-Reranker-0.6B-Q8_0"
BGE = "bge-reranker-v2-m3-Q8_0"
#: A sorter that runs outside the machines (a stand-in for a cloud account). Its name sorts before
#: the real sorters' in the gateway's list, so that "the first sorter listed" would be this one.
OUTSIDE = "Acme-Cloud-Reranker-v3"
assert OUTSIDE < SORTER < BGE, "the order the gateway lists models in is how automatic would choose"

CLIENT_ID, CLIENT_SECRET, ADMIN_TOKEN = "mr6b-client", "mr6b-client-secret", "mr6b-admin-token"
#: The question the inference driver asks a backend once to learn that it sorts (its own test).
DRIVER_PROBE = "What is the capital of France?"
WORDS_ONLY = "Searching by words only. No search model is running."
SORT_SECONDS = 30.0
#: How long the stand-in agent takes to see that an engine it supervises has ended.
AGENT_NOTICE_SECONDS = 2.0

#: What a question's report of the sorter holds (the page's `SortRun`): nothing like a score.
SORT_FIELDS = {
    "used", "code", "message", "quiet", "model", "setting", "passages", "tokens",
    "ratePerSecond", "expectedSeconds", "budgetSeconds", "seconds",
}  # fmt: skip

#: MR5's run of record (docs/acceptance/mr5-meaning-run.md): the hybrid order must reproduce it.
MR5_RECORD = {
    "counts": {"words": 30, "meaning": 35, "both": 36},
    "recall": {"words": 0.500, "meaning": 0.583, "both": 0.600},
    "mrr": {"words": 0.331, "meaning": 0.459, "both": 0.432},
    "passages": 2810,
    "files": 76,
}

LOCK = threading.Lock()
CHECKS: list[tuple[str, str, bool, str]] = []
NUMBERS: dict[str, Any] = {}
STAGE_SECONDS: dict[str, float] = {}


def log(text: str) -> None:
    print(f"[{time.perf_counter() - T0:7.1f}s] {text}", flush=True)


def say(text: str) -> None:
    print(f"\n== {text}", flush=True)


def check(cid: str, claim: str, passed: bool, detail: object = "") -> bool:
    """One claim of the run, with a short name the sabotage runs look for."""
    CHECKS.append((cid, claim, bool(passed), str(detail)[:900]))
    shown = f"  ({str(detail)[:900]})" if detail != "" and not passed else ""
    print(f"  {'PASS' if passed else 'FAIL'}  [{cid}] {claim}{shown}", flush=True)
    return bool(passed)


def note(text: str) -> None:
    print(f"  NOTE  {text}", flush=True)


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def free_ports(count: int) -> list[int]:
    held, ports = [], []
    for _ in range(count):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        held.append(s)
        ports.append(int(s.getsockname()[1]))
    for s in held:
        s.close()
    return ports


def until(condition: Callable[[], Any], seconds: float, every: float = 0.5) -> Any:
    """The condition's value once it is true, or None when `seconds` pass."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        value = condition()
        if value:
            return value
        time.sleep(every)
    return condition() or None


def person_key(sub: str) -> str:
    return hashlib.sha256(sub.encode("utf-8")).hexdigest()[:32]


def squash(text: str) -> str:
    return " ".join(text.split())


# --------------------------------------------------------------------------- #
# Windows: a job object that ends every child with this process, and CPU counters
# --------------------------------------------------------------------------- #

K32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
_JOBS: list[Any] = []


class _Basic(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _Io(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _Extended(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _Basic),
        ("IoInfo", _Io),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _FileTime(ctypes.Structure):
    _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]


def _ticks(ft: _FileTime) -> int:
    return (int(ft.hi) << 32) | int(ft.lo)


def children_die_with_me() -> bool:
    """Put this process in a job whose handle this process holds: when it ends, however it ends,
    the job's other members (every child it starts) are killed."""
    if K32 is None:
        return False
    try:
        K32.CreateJobObjectW.restype = wintypes.HANDLE
        K32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        K32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        K32.GetCurrentProcess.restype = wintypes.HANDLE
        K32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = K32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = _Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not K32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return False
        if not K32.AssignProcessToJobObject(job, K32.GetCurrentProcess()):
            return False
        _JOBS.append(job)
        return True
    except Exception:
        return False


def suspend_process(pid: int, suspend: bool) -> bool:
    """Freeze a process (it keeps its sockets and answers nothing) or let it go on."""
    if K32 is None:
        return False
    ntdll = ctypes.WinDLL("ntdll")
    K32.OpenProcess.restype = wintypes.HANDLE
    K32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    K32.CloseHandle.argtypes = [wintypes.HANDLE]
    call = ntdll.NtSuspendProcess if suspend else ntdll.NtResumeProcess
    call.argtypes = [wintypes.HANDLE]
    call.restype = ctypes.c_long
    handle = K32.OpenProcess(0x0800, False, pid)  # PROCESS_SUSPEND_RESUME
    if not handle:
        return False
    try:
        return int(call(handle)) == 0
    finally:
        K32.CloseHandle(handle)


def cpu_process(pid: int) -> int | None:
    """The CPU time one process has used, in 100 ns; None when it cannot be read."""
    if K32 is None:
        return None
    K32.OpenProcess.restype = wintypes.HANDLE
    K32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    K32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(_FileTime)] * 4
    K32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = K32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        made, gone, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
        ok = K32.GetProcessTimes(
            handle, ctypes.byref(made), ctypes.byref(gone), ctypes.byref(kernel), ctypes.byref(user)
        )
        return _ticks(kernel) + _ticks(user) if ok else None
    finally:
        K32.CloseHandle(handle)


def cpu_system() -> tuple[int, int]:
    """(busy, total) CPU time of the whole machine, in 100 ns, summed over its logical CPUs."""
    if K32 is None:
        return 0, 0
    idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
    K32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    total = _ticks(kernel) + _ticks(user)  # kernel time includes the idle time
    return total - _ticks(idle), total


# --------------------------------------------------------------------------- #
# Small servers: the agent's answers, a sign-in stand-in, logging pass-throughs, a cloud sorter
# --------------------------------------------------------------------------- #


class Quiet(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass  # a probe that hangs up early is not news


def serve(handler: type[BaseHTTPRequestHandler], port: int) -> None:
    Quiet(("127.0.0.1", port), handler).serve_forever()


class Tap:
    """What went through one hop, in order: the entries are filled in as requests arrive."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def mark(self) -> int:
        with LOCK:
            return len(self.entries)

    def since(self, mark: int) -> list[dict[str, Any]]:
        with LOCK:
            return list(self.entries[mark:])

    def add(self, entry: dict[str, Any]) -> dict[str, Any]:
        with LOCK:
            self.entries.append(entry)
        return entry


def _sends(
    handler: BaseHTTPRequestHandler, status: int, headers: dict[str, str], body: bytes
) -> None:
    handler.send_response(status)
    for key, value in headers.items():
        handler.send_header(key, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def rerank_proxy(upstream: int, tap: Tap) -> type[BaseHTTPRequestHandler]:
    """A pass-through to a sorter's engine that keeps every `/v1/rerank` it carries: the question
    and the passages as the engine was sent them, what the engine said (its scores), and when. An
    engine that cannot be reached is a 502 with the cause, as a stopped backend is to the driver."""

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _forward(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            entry: dict[str, Any] | None = None
            if self.command == "POST" and self.path.split("?")[0] in ("/v1/rerank", "/rerank"):
                try:
                    asked = json.loads(body)
                    entry = tap.add(
                        {
                            "keys": sorted(asked),
                            "model": asked.get("model"),
                            "query": str(asked.get("query")),
                            "docs": [
                                d if isinstance(d, str) else str(d.get("text"))
                                for d in asked.get("documents") or []
                            ],
                            "t0": time.perf_counter(),
                            "t1": None,
                            "status": None,
                            "results": None,
                            "usage": None,
                            "error": None,
                        }
                    )
                except (ValueError, AttributeError):
                    entry = None
            try:
                hop = http.client.HTTPConnection("127.0.0.1", upstream, timeout=900)
                wanted = ("content-type", "accept", "authorization", "x-request-id")
                headers = {k: v for k, v in self.headers.items() if k.lower() in wanted}
                hop.request(self.command, self.path, body=body or None, headers=headers)
                answer = hop.getresponse()
                data = answer.read()
                hop.close()
            except (OSError, http.client.HTTPException) as exc:
                if entry is not None:
                    entry["t1"] = time.perf_counter()
                    entry["status"] = 502
                    entry["error"] = f"{type(exc).__name__}: {exc}"
                _sends(
                    self,
                    502,
                    {"Content-Type": "application/json"},
                    json.dumps(
                        {"error": {"message": f"the engine did not answer ({exc})"}}
                    ).encode(),
                )
                return
            if entry is not None:
                entry["t1"] = time.perf_counter()
                entry["status"] = answer.status
                try:
                    parsed = json.loads(data)
                    entry["results"] = parsed.get("results")
                    entry["usage"] = parsed.get("usage")
                except (ValueError, AttributeError):
                    pass
            keep = {k: v for k in ("Content-Type", "Cache-Control") if (v := answer.getheader(k))}
            _sends(self, answer.status, keep, data)

        do_GET = _forward
        do_POST = _forward

    return Proxy


def chat_proxy(upstream: int, tap: Tap) -> type[BaseHTTPRequestHandler]:
    """A pass-through to the chat engine that keeps the body of every chat request."""

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _forward(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            if self.command == "POST" and self.path.split("?")[0] == "/v1/chat/completions":
                tap.add({"body": json.loads(body), "t0": time.perf_counter()})
            hop = http.client.HTTPConnection("127.0.0.1", upstream, timeout=900)
            wanted = ("content-type", "accept", "authorization")
            headers = {k: v for k, v in self.headers.items() if k.lower() in wanted}
            hop.request(self.command, self.path, body=body or None, headers=headers)
            answer = hop.getresponse()
            self.send_response(answer.status)
            for key in ("Content-Type", "Cache-Control"):
                if value := answer.getheader(key):
                    self.send_header(key, value)
            self.end_headers()
            while chunk := answer.read(4096):
                self.wfile.write(chunk)
                self.wfile.flush()
            hop.close()

        do_GET = _forward
        do_POST = _forward
        do_PATCH = _forward
        do_DELETE = _forward

    return Proxy


def passed_on(answer: http.client.HTTPResponse) -> dict[str, str]:
    """The headers of an answer a pass-through hands on: all of them (a `Retry-After` is what tells
    Workbench the gateway is holding requests back), but those that belong to one hop."""
    hop_by_hop = {
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "date",
        "server",
    }
    return {k: v for k, v in answer.getheaders() if k.lower() not in hop_by_hop}


def door_proxy(upstream: int, sorts: Tap, chats: Tap) -> type[BaseHTTPRequestHandler]:
    """The gateway's door as Workbench meets it: every `/v1/rerank` it sent (the body, the status
    the gateway answered and what it said) and every chat request, passed through unchanged."""

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _forward(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            path = self.path.split("?")[0]
            entry: dict[str, Any] | None = None
            if self.command == "POST" and path == "/v1/rerank":
                try:
                    entry = sorts.add(
                        {
                            "body": json.loads(body),
                            "t0": time.perf_counter(),
                            "t1": None,
                            "status": None,
                            "answer": None,
                        }
                    )
                except ValueError:
                    entry = None
            if self.command == "POST" and path == "/v1/chat/completions":
                chats.add({"body": json.loads(body), "t0": time.perf_counter()})
            hop = http.client.HTTPConnection("127.0.0.1", upstream, timeout=900)
            wanted = ("content-type", "accept", "authorization", "x-eugene-repetition-mode")
            headers = {k: v for k, v in self.headers.items() if k.lower() in wanted}
            hop.request(self.command, self.path, body=body or None, headers=headers)
            answer = hop.getresponse()
            if entry is not None:
                data = answer.read()
                entry["t1"] = time.perf_counter()
                entry["status"] = answer.status
                entry["retry_after"] = answer.getheader("Retry-After")
                try:
                    entry["answer"] = json.loads(data)
                except ValueError:
                    entry["answer"] = data[:300].decode("utf-8", "replace")
                _sends(self, answer.status, passed_on(answer), data)
                hop.close()
                return
            self.send_response(answer.status)
            for key, value in passed_on(answer).items():
                self.send_header(key, value)
            self.end_headers()
            while chunk := answer.read(4096):
                self.wfile.write(chunk)
                self.wfile.flush()
            hop.close()

        do_GET = _forward
        do_POST = _forward
        do_PATCH = _forward
        do_DELETE = _forward

    return Proxy


def fake_score(text: str) -> float:
    """What the sorter outside the machines says of a passage: a number made from its words, so a
    check can work out the order it gave without asking it, and Workbench's own order is not it."""
    return (int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16) % 9000 + 500) / 10000


def outside_backend(tap: Tap) -> type[BaseHTTPRequestHandler]:
    """A stand-in for a hosted sorter: a driver fronts it as a custom endpoint declared external,
    so the gateway lists it with locality `external`. It keeps everything it is asked."""

    class Outside(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _json(self, status: int, body: Any) -> None:
            _sends(self, status, {"Content-Type": "application/json"}, json.dumps(body).encode())

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            if path in ("/v1/models", "/models"):
                self._json(200, {"object": "list", "data": [{"id": OUTSIDE, "object": "model"}]})
            elif path in ("/health", "/healthz"):
                self._json(200, {"status": "ok"})
            else:
                self._json(404, {"error": {"message": "not here"}})

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            path = self.path.split("?")[0]
            if path not in ("/v1/rerank", "/rerank"):
                tap.add({"path": path, "query": None, "docs": []})
                self._json(404, {"error": {"message": "this endpoint only sorts"}})
                return
            asked = json.loads(body)
            docs = [str(d) for d in asked.get("documents") or []]
            tap.add(
                {
                    "path": path,
                    "query": str(asked.get("query")),
                    "docs": docs,
                    "keys": sorted(asked),
                    "t0": time.perf_counter(),
                }
            )
            results = [{"index": i, "relevance_score": fake_score(d)} for i, d in enumerate(docs)]
            results.sort(key=lambda r: (-r["relevance_score"], r["index"]))
            tokens = sum(max(1, len(d) // 4) for d in docs)
            self._json(
                200,
                {
                    "model": OUTSIDE,
                    "results": results,
                    "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
                },
            )

    return Outside


# --------------------------------------------------------------------------- #
# A stand-in for Eugene's sign-in (OpenID Connect, as the unit checks use)
# --------------------------------------------------------------------------- #

PEOPLE = {
    "operator": {"name": "Owner", "role": "operator", "username": "operator"},
    "p-ada": {"name": "Ada", "role": "member", "username": "ada"},
    "p-bo": {"name": "Bo", "role": "member", "username": "bo"},
}


def eugene_app(port: int) -> Any:
    key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    issuer = f"http://127.0.0.1:{port}/oidc"
    codes: dict[str, dict[str, Any]] = {}
    app = FastAPI()

    def id_token(sub: str, nonce: str | None) -> str:
        now = int(time.time())
        person = PEOPLE[sub]
        claims: dict[str, Any] = {
            "iss": issuer,
            "aud": CLIENT_ID,
            "sub": sub,
            "iat": now,
            "exp": now + 3600,
            "name": person["name"],
            "preferred_username": person["username"],
            "eugene_role": person["role"],
            "auth_time": now,
        }
        if nonce is not None:
            claims["nonce"] = nonce
        return jwt.encode({"alg": "RS256", "typ": "JWT", "kid": "k1"}, claims, key)

    @app.get("/oidc/.well-known/openid-configuration")
    async def discovery() -> Any:
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": f"{issuer}/jwks",
            "revocation_endpoint": f"{issuer}/revoke",
        }

    @app.get("/oidc/jwks")
    async def jwks() -> Any:
        return {"keys": [key.as_dict(private=False)]}

    @app.get("/oidc/authorize")
    async def authorize(request: Request) -> Any:
        q = request.query_params
        code = secrets.token_urlsafe(16)
        codes[code] = {
            "sub": q["login_as"],
            "nonce": q["nonce"],
            "challenge": q["code_challenge"],
            "redirect_uri": q["redirect_uri"],
        }
        target = (
            q["redirect_uri"] + "?" + urlencode({"code": code, "state": q["state"], "iss": issuer})
        )
        return RedirectResponse(target, status_code=302)

    @app.post("/oidc/token")
    async def token(request: Request) -> Any:
        auth = request.headers.get("authorization", "")
        expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if auth != f"Basic {expected}":
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        form = await request.form()
        if form["grant_type"] == "authorization_code":
            found = codes.pop(str(form["code"]), None)
            verifier = str(form.get("code_verifier", ""))
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if found is None or found["challenge"] != challenge:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return {
                "access_token": "at",
                "token_type": "Bearer",
                "expires_in": 3600,
                "id_token": id_token(found["sub"], found["nonce"]),
                "refresh_token": secrets.token_urlsafe(16),
            }
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    @app.post("/oidc/revoke")
    async def revoke() -> Any:
        return {}

    return app


class Person:
    """One person's browser: a cookie jar, the secret the page keeps, an origin."""

    def __init__(self, sub: str, base: str) -> None:
        self.sub = sub
        self.base = base
        self.http = httpx.Client(base_url=base, timeout=300, trust_env=False)
        self.secret: str | None = None

    def sign_in(self) -> None:
        start = self.http.get("/signin")
        assert start.status_code == 302, start.text
        at_eugene = httpx.get(start.headers["location"] + "&login_as=" + self.sub, trust_env=False)
        assert at_eugene.status_code == 302, at_eugene.text
        back = urlsplit(at_eugene.headers["location"])
        done = self.http.get(back.path + "?" + back.query)
        location = done.headers.get("location", "")
        assert "#signin=" in location, f"sign-in failed: {location}"
        self.secret = location.split("#signin=", 1)[1]

    def call(self, method: str, path: str, **kw: Any) -> Any:
        headers = {"Origin": self.base, "X-Workbench-Secret": self.secret or ""}
        return self.http.request(method, path, headers=headers, **kw)

    def get(self, path: str, **kw: Any) -> Any:
        return self.call("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.call("POST", path, **kw)

    def patch(self, path: str, **kw: Any) -> Any:
        return self.call("PATCH", path, **kw)

    def add(self, *named: tuple[str, bytes, str]) -> Any:
        return self.post("/api/documents", files=[("files", n) for n in named])

    def documents(self) -> dict[str, Any]:
        found = self.get("/api/documents")
        assert found.status_code == 200, found.text
        return dict(found.json())

    def search(self) -> dict[str, Any]:
        return dict(self.documents()["search"])

    def sort(self) -> dict[str, Any] | None:
        return cast("dict[str, Any] | None", self.search().get("sort"))

    def test_raw(self, question: str, **more: Any) -> Any:
        return self.post("/api/documents/test", json={"question": question, **more})

    def test(self, question: str, **more: Any) -> dict[str, Any]:
        made = self.test_raw(question, **more)
        assert made.status_code == 200, f"Test a question: {made.status_code} {made.text[:400]}"
        return dict(made.json())

    def wait_answer(self, chat: str, seconds: float = 400) -> dict[str, Any]:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            messages = self.get(f"/api/chats/{chat}").json()["messages"]
            if messages and messages[-1]["status"] != "running":
                return dict(messages[-1])
            time.sleep(0.4)
        raise AssertionError("the answer did not finish")


# --------------------------------------------------------------------------- #
# The questions, and what counts as finding the right section (MR5's own scoring)
# --------------------------------------------------------------------------- #


def right_section(passage: dict[str, Any], question: dict[str, Any]) -> bool:
    """A passage answers a question when it is in the labelled file and the labelled heading is
    one of the headings above it (or is its own): the section, and whatever is under it."""
    if passage["name"] != question["file"]:
        return False
    parts = [squash(p) for p in (passage.get("heading") or "").split(" > ")]
    return squash(question["heading"]) in parts


def hits_at(
    found: list[list[dict[str, Any]]], questions: list[dict[str, Any]], k: int
) -> list[int]:
    return [
        int(any(right_section(p, q) for p in passages[:k]))
        for passages, q in zip(found, questions, strict=True)
    ]


def reciprocal_ranks(
    found: list[list[dict[str, Any]]], questions: list[dict[str, Any]], k: int
) -> list[float]:
    out = []
    for passages, q in zip(found, questions, strict=True):
        place = next((i for i, p in enumerate(passages[:k], 1) if right_section(p, q)), None)
        out.append(0.0 if place is None else 1.0 / place)
    return out


def paired_bootstrap(
    a: list[float], b: list[float], rounds: int = 4000, seed: int = 7
) -> tuple[float, float, float]:
    """The mean of a - b, and its 95% interval, resampling the questions (paired): MR5's own."""
    rng = np.random.default_rng(seed)
    diff = np.array(a, dtype=float) - np.array(b, dtype=float)
    means = [float(diff[rng.integers(0, len(diff), len(diff))].mean()) for _ in range(rounds)]
    return float(diff.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(values, q)) if values else float("nan")


def order_of(found: dict[str, Any]) -> list[tuple[int, ...]]:
    """The passages a question found, as the chunks each holds, in the order they would be sent."""
    return [tuple(p["chunkIds"]) for p in found["passages"]]


# --------------------------------------------------------------------------- #
# A small library in which every passage is a file: the best ten of each way are exactly known
# --------------------------------------------------------------------------- #

#: (title, section, text). Three subjects, each with passages that share the words of its question
#: and say something else, and passages that say what the question means with none of its words.
GREENHOUSE_Q = "How often do the seedlings need a drink?"
HARBOUR_Q = "When does the early crew report for duty?"
KITCHEN_Q = "Which shelf keeps the milk cold?"
COMPOSITION_QUESTIONS = (GREENHOUSE_Q, HARBOUR_Q, KITCHEN_Q)
LIBRARY: list[tuple[str, str, str]] = [
    # --- greenhouse: the words of the question, said of other things ---
    (
        "Pub licence",
        "Opening hours",
        "The pub is often full on Fridays, when regulars drink late into the evening. The licence must be renewed each March, and the landlord says the paperwork is what they dread.",
    ),
    (
        "Seedlings catalogue",
        "Prices",
        "Seedlings of the dwarf bean are sold in trays of twelve, and customers often ask for the early variety. A tray costs the same whatever the variety and is paid for at the till.",
    ),
    (
        "Staff handbook",
        "Breaks",
        "Staff need a drink and a rest at the break, and the kettle is in the back room. Please wash your own mug, and do not leave tea bags in the sink.",
    ),
    (
        "Choir",
        "Rehearsal",
        "We often rehearse on Thursday evenings in the hall, and singers need to bring a drink of their own. The piano is tuned in autumn, and the key is kept by the caretaker.",
    ),
    (
        "Marathon",
        "Aid stations",
        "Runners need a drink every few kilometres, and volunteers often hand out the cups from long tables. Empty cups go in the green bins at the side of the road.",
    ),
    (
        "Pantry",
        "Juice",
        "A drink of juice is poured at snack time, and the youngest children often need help to carry their cups. Spilled juice is wiped up at once so the floor stays safe.",
    ),
    (
        "Nursery school",
        "Seedlings display",
        "The seedlings the children planted are on the windowsill of the front room, and parents often stop to look. The class that planted them needs to label each pot.",
    ),
    (
        "Garage",
        "Waiting room",
        "Customers often wait here with a drink from the machine, and the cars they need are brought round when ready. The machine takes coins and cards.",
    ),
    # --- greenhouse: what the question means, in other words ---
    (
        "Greenhouse",
        "Watering",
        "Young plants in the glasshouse are sprinkled with a fine rose every second morning, and the trays are never left standing in water. The tap water is left in a barrel overnight.",
    ),
    (
        "Propagation",
        "Moisture",
        "Newly sprouted plantlets are kept damp, not soaked; a light misting at dawn and again at dusk is enough in warm weather. In a cold week once a day is plenty.",
    ),
    (
        "Garden notes",
        "Irrigation",
        "The tender shoots on the bench get a gentle soak twice a week, more in a heatwave, and the compost should feel like a wrung-out sponge. A tray that stays wet is moved to the shelf by the door.",
    ),
    (
        "Cold frame",
        "Care",
        "Baby plants hardening off outdoors are watered each morning with a can, because the wind dries the tops fast. They are brought in under glass if frost is forecast.",
    ),
    (
        "Windowsill",
        "Sprouts",
        "Tiny sprouts on the sill are sprayed every day until their roots take hold, and then every other day. A sprayer with a fine mist does less harm than a can.",
    ),
    (
        "Seed trays",
        "Hydration",
        "Each seed tray is given a little water from below every few days so that the surface stays moist and the seeds are not washed away. The saucers are tipped out after an hour.",
    ),
    (
        "Polytunnel",
        "Schedule",
        "The drip lines in the tunnel run for ten minutes at six in the morning and again at six in the evening, and the timer is checked each Monday.",
    ),
    (
        "Compost",
        "Wetness",
        "Seed compost should be damp enough to hold its shape when squeezed but should not drip, and a sprinkle of water at the start of each day keeps it that way.",
    ),
    # --- harbour: the words of the question, said of other things ---
    (
        "Customs",
        "Fuel duty",
        "Fuel duty is paid by the crew chief's office each quarter, and the report of what was bought is filed on the last working day. Late reports are charged a fee.",
    ),
    (
        "Newsroom",
        "Early edition",
        "The early edition report goes to press at nine, and the crew in the print hall wait for the last page. Duty editors sign the proofs.",
    ),
    (
        "Film set",
        "Call sheet",
        "The film crew reports to the quay for the early scene on Tuesday, and the duty manager checks the permits. Catering sets up behind the lorries.",
    ),
    (
        "Library",
        "Early readers",
        "Early readers' reports are kept in the red folder, and the duty librarian files them on Fridays. Parents may borrow the folder for an evening.",
    ),
    (
        "Hospital",
        "Duty doctor",
        "The duty doctor writes a report for each early admission, and the ward crew bring the notes to the desk. Night staff hand over at seven.",
    ),
    (
        "Rowing club",
        "Early starts",
        "The crew's early outings are reported on the noticeboard, and the duty captain unlocks the boat shed. Oars are counted back in.",
    ),
    (
        "Insurance",
        "Duty of care",
        "A duty of care applies to every early inspection, and the crew leader must report any damage to the surveyor. Photographs are attached to the form.",
    ),
    (
        "Radio",
        "Early show",
        "The early show's crew report any fault in the studio to the duty engineer, and the log is read at the start of the next show.",
    ),
    # --- harbour: what the question means, in other words ---
    (
        "Harbour",
        "Shift times",
        "Dock hands clock on at six sharp, before the first ferry arrives, and the quay foreman reads out the day's berths. Latecomers lose the first hour's pay.",
    ),
    (
        "Roster",
        "Start of the day",
        "The first watch begins at dawn: sailors gather on deck for the morning muster, and the mate marks each name in the book.",
    ),
    (
        "Port office",
        "Hours",
        "Mooring staff start their day at half past five and finish at two, and a second gang takes over until the last tide.",
    ),
    (
        "Ferry terminal",
        "Morning team",
        "The team that opens the terminal arrives at a quarter to six, switches on the lights and unlocks the gangways before passengers come.",
    ),
    (
        "Pilot station",
        "Turn out",
        "Pilots on the dawn rota must be at the jetty by five, with their radios charged, in case a ship arrives with the first light.",
    ),
    (
        "Boat yard",
        "Working hours",
        "The yard hands begin at seven and stop for tea at ten; anyone on the early rota begins an hour sooner to launch the boats.",
    ),
    (
        "Lighthouse",
        "Keepers' rota",
        "The morning keeper takes over from the night keeper at six, signs the log and puts out the lamp as the sun comes up.",
    ),
    (
        "Fish market",
        "Opening",
        "Porters and salesmen are at the market by half past four to unload the catch, and the first buyers are let in at five.",
    ),
    # --- kitchen: the words of the question, said of other things ---
    (
        "Library",
        "Shelf marks",
        "Each shelf keeps its books in order by the mark on the spine, and the last shelf by the door holds the oldest ones. A cold draught from the window is why they are kept low.",
    ),
    (
        "Weather",
        "Cold front",
        "A cold front brings rain from the west, and the wind keeps up all night. Farmers who watch the sky say the milk round will be late.",
    ),
    (
        "Bakery",
        "Milk loaf",
        "A milk loaf keeps for three days in a paper bag, and the shelf behind the counter holds a fresh batch each morning. It is not kept cold.",
    ),
    (
        "Wardrobe",
        "Winter coats",
        "The top shelf keeps the winter coats, and the cold room next door is where the boots dry. Mothballs are in the drawer.",
    ),
    (
        "Cafe",
        "Cold drinks",
        "The cold drinks shelf is by the till, and a jug of milk is kept for tea. Staff top up the sugar before they open.",
    ),
    (
        "Dairy farm",
        "Milking",
        "The milk is collected from the shed each morning, and the shelf of buckets keeps them off the wet floor. A cold wind blows through the yard.",
    ),
    (
        "Museum",
        "Cold storage of maps",
        "The map shelf keeps the oldest charts flat, and the cold of the basement protects the paper. Visitors may not touch them.",
    ),
    (
        "Nursery rhyme",
        "Milk and honey",
        "The cow gives milk and the bees keep the honey on the shelf, so the rhyme goes. Children clap it in the cold of January.",
    ),
    # --- kitchen: what the question means, in other words ---
    (
        "Kitchen",
        "Refrigeration",
        "Dairy goes in the chiller on the lowest rack, where the temperature is steadiest, and never in the door where it warms each time it opens.",
    ),
    (
        "Pantry",
        "Chilled goods",
        "Cartons of cream and yoghurt are stored in the fridge, on the rack above the vegetable drawer, so they stay at four degrees.",
    ),
    (
        "Food safety",
        "Temperatures",
        "Fresh dairy must be held below five degrees, so it belongs at the back of the refrigerator and not near the warm light.",
    ),
    (
        "Larder",
        "Cooling",
        "The marble slab keeps butter and cream cool in summer, and the bottles stand in a tray of cold water beside it.",
    ),
    (
        "Dairy",
        "Storage",
        "Bottles are kept upright in the cooler at the bottom, away from strong smells, and the oldest are placed at the front to be used first.",
    ),
    (
        "Canteen",
        "Fridge layout",
        "Milk, eggs and cheese go on the middle rack of the big refrigerator, and cooked meats are kept apart on the top.",
    ),
    (
        "Camping",
        "Keeping food fresh",
        "A cool box with an ice pack keeps the milk from turning, and it is stood in the shade of the tent away from the stove.",
    ),
    (
        "Home economy",
        "The icebox",
        "Before electricity the milk was lowered into the icebox in the cellar, where blocks of ice kept the whole shelf near freezing.",
    ),
]


def library_files() -> list[tuple[str, bytes, str]]:
    """Each passage of the library as a file of its own: its title, its section, its words."""
    out = []
    for n, (title, section, words) in enumerate(LIBRARY, start=1):
        slug = re.sub(r"[^a-z0-9]+", "-", f"{title} {section}".lower()).strip("-")
        body = f"# {title}\n\n## {section}\n\n{words}\n"
        out.append((f"{n:02d}-{slug}.md", body.encode("utf-8"), "text/markdown"))
    return out


# --------------------------------------------------------------------------- #
# What an index holds, read from its own file (no part of Workbench is asked)
# --------------------------------------------------------------------------- #


class IndexFile:
    """A person's index file, read-only: each passage's headings, words and size, and how the
    sorter was sent each (`passage_input`: its headings, a new line, its words)."""

    def __init__(self, path: Path) -> None:
        db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
        try:
            self.rows = {
                int(cid): (heading, text, int(tokens))
                for cid, heading, text, tokens in db.execute(
                    "SELECT id, heading_path, text, tokens_est FROM chunks"
                )
            }
        finally:
            db.close()
        self.by_sent: dict[str, list[int]] = {}
        for cid, (heading, text, _) in self.rows.items():
            self.by_sent.setdefault(self.sent(heading, text), []).append(cid)

    @staticmethod
    def sent(heading: str | None, text: str) -> str:
        return f"{heading}\n{text}" if heading else text

    def sent_of(self, cid: int) -> str:
        heading, text, _ = self.rows[cid]
        return self.sent(heading, text)

    def chunks_of(self, sent: str) -> list[int]:
        return self.by_sent.get(sent, [])


def engine_order(entry: dict[str, Any]) -> list[int] | None:
    """The positions of the passages an engine was sent, best first by its own scores (a tie by
    position), or None when it did not answer with one score for each."""
    results = entry.get("results")
    n = len(entry["docs"])
    if not isinstance(results, list) or len(results) != n:
        return None
    scores: dict[int, float] = {}
    for item in results:
        scores[int(item["index"])] = float(item["relevance_score"])
    if sorted(scores) != list(range(n)):
        return None
    return sorted(range(n), key=lambda i: (-scores[i], i))


# --------------------------------------------------------------------------- #
# The stack
# --------------------------------------------------------------------------- #

NAMES = [
    "gateway", "door", "agent", "oidc", "workbench", "outside",
    "drv_chat", "eng_chat", "prx_chat",
    "drv_embed", "eng_embed",
    "drv_sorter", "eng_sorter", "prx_sorter",
    "drv_bge", "eng_bge", "prx_bge",
    "drv_outside",
]  # fmt: skip


class Stack:
    """Engines (a search model, a result sorter, a chat model, a second sorter), a driver for each,
    a sorter outside the machines, one gateway, a stand-in for the agent and for Eugene's sign-in,
    and Workbench. Things are started as a stage needs them."""

    def __init__(self, work: Path) -> None:
        self.work = work
        self.logs = work / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.data = work / "workbench-data"
        self.procs: list[subprocess.Popen[bytes]] = []
        self.engines: dict[str, subprocess.Popen[bytes]] = {}
        self.engine_starts: dict[str, int] = {}
        self.drivers: dict[str, subprocess.Popen[bytes]] = {}
        self.wb: subprocess.Popen[bytes] | None = None
        base = {k: v for k, v in os.environ.items() if not k.upper().startswith("EUGENE_PLEXUS_")}
        for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            base.pop(proxy, None)
            base.pop(proxy.lower(), None)
        self.base_env = base
        self.cpu_env = {**base, "CUDA_VISIBLE_DEVICES": "-1", "GGML_VK_VISIBLE_DEVICES": ""}
        self.port = dict(zip(NAMES, free_ports(len(NAMES)), strict=True))
        self.wb_url = f"http://127.0.0.1:{self.port['workbench']}"
        # What went through each hop.
        self.engine_sorts = {"sorter": Tap(), "bge": Tap()}
        self.chat_engine = Tap()
        self.door_sorts = Tap()
        self.door_chats = Tap()
        self.outside_asked = Tap()
        # What the stand-in agent says: the drivers there are, and the runtimes it supervises.
        self.components: dict[str, int] = {}
        self.runtimes: dict[str, dict[str, Any]] = {}
        self.server: Any = None

    # --- processes -----------------------------------------------------------

    def start(self, argv: list[str], log_name: str, env: dict[str, str]) -> subprocess.Popen[bytes]:
        handle = open(self.logs / log_name, "w", encoding="utf-8")  # noqa: SIM115
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(self.work),  # nothing here for `-m` to import by mistake
        )
        self.procs.append(proc)
        with open(self.work / "pids.txt", "a", encoding="utf-8") as pids:
            pids.write(f"{proc.pid} {log_name}\n")
        return proc

    @staticmethod
    def kill(proc: subprocess.Popen[bytes]) -> None:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False
        )

    def serve_all(self) -> None:
        """The small servers: the agent's answers, Eugene's sign-in, the hops that keep a log."""
        p = self.port
        threading.Thread(target=serve, args=(self.agent_handler(), p["agent"]), daemon=True).start()
        threading.Thread(
            target=serve,
            args=(rerank_proxy(p["eng_sorter"], self.engine_sorts["sorter"]), p["prx_sorter"]),
            daemon=True,
        ).start()
        threading.Thread(
            target=serve,
            args=(rerank_proxy(p["eng_bge"], self.engine_sorts["bge"]), p["prx_bge"]),
            daemon=True,
        ).start()
        threading.Thread(
            target=serve,
            args=(chat_proxy(p["eng_chat"], self.chat_engine), p["prx_chat"]),
            daemon=True,
        ).start()
        threading.Thread(
            target=serve,
            args=(door_proxy(p["gateway"], self.door_sorts, self.door_chats), p["door"]),
            daemon=True,
        ).start()
        threading.Thread(
            target=serve, args=(outside_backend(self.outside_asked), p["outside"]), daemon=True
        ).start()
        self.server = uvicorn.Server(
            uvicorn.Config(
                eugene_app(p["oidc"]), host="127.0.0.1", port=p["oidc"], log_level="error"
            )
        )
        threading.Thread(target=self.server.run, daemon=True).start()

    # --- the stand-in agent ----------------------------------------------------

    def agent_handler(self) -> type[BaseHTTPRequestHandler]:
        stack = self

        class Agent(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                status = 200
                if path == "/v1/components":
                    with LOCK:
                        body: Any = {
                            "components": [
                                {
                                    "name": name,
                                    "kind": "inference-driver",
                                    "url": f"http://127.0.0.1:{port}/",
                                    "status": "running",
                                }
                                for name, port in stack.components.items()
                            ]
                        }
                elif path == "/v1/runtimes":
                    with LOCK:
                        body = {"runtimes": [dict(r) for r in stack.runtimes.values()]}
                elif path.startswith("/v1/runtimes/"):
                    name = unquote(path.rsplit("/", 1)[1])
                    with LOCK:
                        found = stack.runtimes.get(name)
                        body = dict(found) if found is not None else {"error": "no such runtime"}
                        status = 200 if found is not None else 404
                elif path == "/v1/node":
                    body = {"name": "mr6b-node", "enrolled": False}
                else:
                    body = {"status": "ok"}
                _sends(
                    self, status, {"content-type": "application/json"}, json.dumps(body).encode()
                )

        return Agent

    def set_runtime(self, name: str, status: str, reason: str | None = None) -> None:
        with LOCK:
            runtime = self.runtimes[name]
            runtime["status"] = status
            if reason:
                runtime["stopReason"] = reason
            else:
                runtime.pop("stopReason", None)
        log(f"the agent says runtime {name} is {status}" + (f" ({reason})" if reason else ""))

    # --- engines and drivers -----------------------------------------------------

    def engine_argv(self, kind: str) -> list[str]:
        host = ["--host", "127.0.0.1", "--port", str(self.port[f"eng_{kind}"])]
        if kind == "chat":
            return [
                str(LLAMA), "-m", str(CHAT_FILE), *host, "-ngl", "0", "-t", "2", "-c", "8192",
                "-np", "1", "-b", "512", "-ub", "512",
            ]  # fmt: skip
        if kind == "embed":
            # What the agent's launch path gives a search model (MR3).
            return [
                str(LLAMA), "-m", str(EMBED_FILE), *host, "--embeddings", "-ngl", "0", "-t",
                THREADS, "-c", "4096", "-np", "4", "-b", "2048", "-ub", "2048",
            ]  # fmt: skip
        file = SORTER_FILE if kind == "sorter" else BGE_FILE
        return [
            str(LLAMA), "-m", str(file), *host, "--reranking", "-ngl", "0", "-t", THREADS,
            "--ctx-size", "4096", "--parallel", "4", "--batch-size", "2048", "--ubatch-size",
            "2048",
        ]  # fmt: skip

    def start_engine(self, kind: str) -> subprocess.Popen[bytes]:
        self.engine_starts[kind] = self.engine_starts.get(kind, 0) + 1
        proc = self.start(
            self.engine_argv(kind), f"llama-{kind}-{self.engine_starts[kind]}.log", self.cpu_env
        )
        self.engines[kind] = proc
        return proc

    def engine_ready(self, kind: str, seconds: float = 180) -> bool:
        port = self.port[f"eng_{kind}"]

        def up() -> bool:
            try:
                return (
                    httpx.get(
                        f"http://127.0.0.1:{port}/health", timeout=3, trust_env=False
                    ).status_code
                    == 200
                )
            except httpx.HTTPError:
                return False

        return bool(until(up, seconds, every=0.5))

    def driver_yaml(self, kind: str) -> str:
        p = self.port
        common = "requestTimeoutSeconds: 600\n"
        if kind == "chat":
            return (
                f"provider: openai_compat_custom\nbaseUrl: http://127.0.0.1:{p['prx_chat']}\n"
                f"modelId: {CHAT}\nbackendLocality: local\n{common}"
            )
        if kind == "embed":
            return (
                f"provider: openai_compat_custom\nbaseUrl: http://127.0.0.1:{p['eng_embed']}\n"
                f"modelId: {EMBED}\nbackendLocality: local\n{common}"
            )
        if kind == "outside":
            return (
                f"provider: openai_compat_custom\nbaseUrl: http://127.0.0.1:{p['outside']}\n"
                f"modelId: {OUTSIDE}\nbackendLocality: external\n{common}"
            )
        # A sorter the agent supervises: the driver follows the runtime by name, so that the
        # runtime being stopped is what the gateway sees, and the driver is local by being so.
        model = SORTER if kind == "sorter" else BGE
        return (
            f"provider: openai_compat_custom\nruntimeName: mr6b-{kind}\nmodelId: {model}\n{common}"
        )

    def start_driver(self, kind: str) -> None:
        p = self.port
        (self.work / f"driver-{kind}.yaml").write_text(
            self.driver_yaml(kind), encoding="utf-8", newline="\n"
        )
        with LOCK:
            self.components[f"mr6b-{kind}-driver"] = p[f"drv_{kind}"]
        self.drivers[kind] = self.start(
            [str(DRV_PY), "-m", "eugene_plexus_inference_driver"],
            f"driver-{kind}.log",
            {
                **self.base_env,
                "EUGENE_PLEXUS_DRIVER_CONFIG_FILE": str(self.work / f"driver-{kind}.yaml"),
                "EUGENE_PLEXUS_DRIVER_BIND_HOST": "127.0.0.1",
                "EUGENE_PLEXUS_DRIVER_BIND_PORT": str(p[f"drv_{kind}"]),
                "EUGENE_PLEXUS_DRIVER_AGENT_URL": f"http://127.0.0.1:{p['agent']}",
            },
        )

    def runtime_for(self, kind: str) -> None:
        """Declare the sorter's runtime to the stand-in agent, starting."""
        with LOCK:
            self.runtimes[f"mr6b-{kind}"] = {
                "name": f"mr6b-{kind}",
                "status": "starting",
                "url": f"http://127.0.0.1:{self.port[f'prx_{kind}']}",
                "modelAlias": SORTER if kind == "sorter" else BGE,
            }

    def bring_up(self, kind: str) -> bool:
        """An engine, its driver (when it has none yet) and, for a sorter, its runtime: ready when
        the engine answers. The agent says `ready` once the engine does."""
        if kind in ("sorter", "bge") and f"mr6b-{kind}" not in self.runtimes:
            self.runtime_for(kind)
        self.start_engine(kind)
        ok = self.engine_ready(kind)
        if kind in ("sorter", "bge") and ok:
            self.set_runtime(f"mr6b-{kind}", "ready")
        if kind not in self.drivers:
            self.start_driver(kind)
        return ok

    def stop_engine(self, kind: str, notice: float = AGENT_NOTICE_SECONDS) -> None:
        """The engine ends (a person or the system ends it); the agent that supervises it sees that
        a moment later and says the runtime is stopped."""
        self.kill(self.engines[kind])
        log(f"the {kind} engine was ended")
        if kind in ("sorter", "bge"):
            name = f"mr6b-{kind}"

            def noticed() -> None:
                time.sleep(notice)
                self.set_runtime(name, "stopped", "the process ended")

            threading.Thread(target=noticed, daemon=True).start()

    def freeze_engine(self, kind: str, frozen: bool) -> bool:
        """The engine stops answering without ending (as a wedged one does), or goes on."""
        done = suspend_process(self.engines[kind].pid, frozen)
        log(f"the {kind} engine was {'frozen' if frozen else 'let go on'}: {done}")
        return done

    def restart_engine(self, kind: str) -> bool:
        if kind in ("sorter", "bge"):
            self.set_runtime(f"mr6b-{kind}", "starting")
        self.start_engine(kind)
        ok = self.engine_ready(kind)
        if kind in ("sorter", "bge") and ok:
            self.set_runtime(f"mr6b-{kind}", "ready")
        return ok

    def gateway_models(self) -> dict[str, dict[str, Any]]:
        try:
            listed = httpx.get(
                f"http://127.0.0.1:{self.port['gateway']}/v1/models", timeout=5, trust_env=False
            ).json()
        except (httpx.HTTPError, ValueError):
            return {}
        return {m["id"]: m.get("x_eugene_plexus") or {} for m in listed.get("data") or []}

    def listed(self, model: str, surface: str, locality: str | None = None) -> bool:
        """Whether the gateway lists this model with this surface, running now (and where)."""
        info = self.gateway_models().get(model) or {}
        return (
            surface in (info.get("surfaces") or [])
            and (info.get("ready_backends") or 0) > 0
            and (locality is None or info.get("locality") == locality)
        )

    def not_ready(self, model: str) -> bool:
        info = self.gateway_models().get(model)
        return info is None or not (info.get("ready_backends") or 0)

    def start_gateway(self) -> None:
        p = self.port
        (self.work / "gateway.yaml").write_text(
            "routingRefreshSeconds: 3\n", encoding="utf-8", newline="\n"
        )
        self.start(
            [str(GW_PY), "-m", "eugene_plexus_gateway"],
            "gateway.log",
            {
                **self.base_env,
                "EUGENE_PLEXUS_GATEWAY_CONFIG_FILE": str(self.work / "gateway.yaml"),
                "EUGENE_PLEXUS_GATEWAY_METRICS_FILE": str(self.work / "metrics.sqlite3"),
                "EUGENE_PLEXUS_GATEWAY_BIND_HOST": "127.0.0.1",
                "EUGENE_PLEXUS_GATEWAY_BIND_PORT": str(p["gateway"]),
                "EUGENE_PLEXUS_GATEWAY_AGENT_URL": f"http://127.0.0.1:{p['agent']}",
            },
        )

    def start_workbench(self, static: Path) -> None:
        p = self.port
        (self.work / "app_key").write_text("mr6b-app-key", encoding="utf-8")
        (self.work / "oidc_secret").write_text(CLIENT_SECRET, encoding="utf-8")
        env = {
            **self.base_env,
            "PYTHONPATH": str(WB / "src"),
            "EUGENE_PLEXUS_APP_BIND_HOST": "127.0.0.1",
            "EUGENE_PLEXUS_APP_BIND_PORT": str(p["workbench"]),
            "EUGENE_PLEXUS_APP_DATA_DIR": str(self.data),
            "EUGENE_PLEXUS_APP_KEY_FILE": str(self.work / "app_key"),
            "EUGENE_PLEXUS_APP_ADMIN_TOKEN": ADMIN_TOKEN,
            "EUGENE_PLEXUS_APP_GATEWAY_URL": f"http://127.0.0.1:{p['door']}",
            "EUGENE_PLEXUS_APP_OIDC_ISSUER": f"http://127.0.0.1:{p['oidc']}/oidc",
            "EUGENE_PLEXUS_APP_OIDC_CLIENT_ID": CLIENT_ID,
            "EUGENE_PLEXUS_APP_OIDC_SECRET_FILE": str(self.work / "oidc_secret"),
            "EUGENE_PLEXUS_APP_STATIC_DIR": str(static),
        }
        where = subprocess.run(
            [str(WB_PY), "-c", "import eugene_plexus_workbench as w; print(w.__file__)"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.work),
            check=False,
        ).stdout.strip()
        wanted = os.path.normcase(os.path.normpath(str(WB / "src")))
        check(
            "iso.source",
            f"Workbench is read from {wanted}",
            os.path.normcase(os.path.normpath(where)).startswith(wanted),
            where,
        )
        self.wb = self.start([str(WB_PY), "-m", "eugene_plexus_workbench"], "workbench.log", env)

    def down(self) -> list[int]:
        if self.server is not None:
            self.server.should_exit = True
        for proc in self.procs:
            self.kill(proc)
        time.sleep(2)
        theirs = [
            port
            for name, port in self.port.items()
            if name.startswith(("drv_", "eng_")) or name in ("gateway", "workbench")
        ]
        return [port for port in theirs if listening(port)]


# --------------------------------------------------------------------------- #
# What the screens say, parsed for the numbers they were decided by
# --------------------------------------------------------------------------- #

OVER_BUDGET = re.compile(
    r"^Not sorted: (?P<model>.+?) sorts about (?P<rate>[\d,]+) tokens a second here, so sorting "
    r"these (?P<passages>\d+) passages \((?P<tokens>[\d,]+) tokens\) would take about "
    r"(?P<seconds>[\d.]+) s, and Sort time is (?P<budget>[\d.]+) s\. Raise Sort time in "
    r"Workbench's settings \(in Eugene's console\) to sort anyway\.$"
)
TYPICAL = re.compile(
    r"^Not sorted: (?P<model>.+?) sorts about (?P<rate>[\d,]+) tokens a second here, so sorting "
    r"the (?P<passages>\d+) passages a question usually finds \(about (?P<each>\d+) tokens each\) "
    r"would add about (?P<seconds>[\d.]+) s to each answer, and Sort time is (?P<budget>[\d.]+) s\. "
    r"Raise Sort time in Workbench's settings \(in Eugene's console\) to sort anyway\.$"
)
SETTING_SPEED = re.compile(
    r"^(?P<model>.+?) sorts about (?P<rate>[\d,]+) tokens a second here: (?P<passages>\d+) "
    r"passages of about (?P<each>\d+) tokens take about (?P<seconds>[\d.]+) s, "
    r"(?P<verdict>within this, so passages are sorted|over this, so passages are not sorted)\.$"
)


def num(text: str) -> float:
    return float(text.replace(",", ""))


def close(said: float, real: float, tolerance: float = 0.051) -> bool:
    """A number as a person is told it (two figures) against the number it is said of."""
    return abs(said - real) <= tolerance * abs(real) + 0.011


class Run:
    """The whole of it: the stages in order, what they found, and the checks."""

    def __init__(self, args: argparse.Namespace, work: Path) -> None:
        self.args = args
        self.stages = set(args.stages.split(",")) if args.stages else set(ALL_STAGES)
        self.work = work
        self.stack = Stack(work)
        self.questions: list[dict[str, Any]] = []
        self.total = 0
        self.design = work / "design"
        self.ada: Person
        self.bo: Person
        self.owner: Person
        self.hybrid: dict[int, dict[str, Any]] = {}
        self.sorted_: dict[int, dict[str, Any]] = {}
        self.index: IndexFile | None = None
        self.partial = False
        self.reused = False
        self.static = work / "static"

    # --- Workbench's settings, as its owner changes them -----------------------

    def settings(self, **values: Any) -> dict[str, Any]:
        made = httpx.patch(
            f"{self.stack.wb_url}/v1/config",
            json=values,
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            trust_env=False,
            timeout=60,
        )
        body = cast("dict[str, Any]", made.json())
        assert made.status_code == 200 and body.get("rejected") == [], f"{values}: {made.text}"
        return body

    def config(self) -> dict[str, Any]:
        got = httpx.get(
            f"{self.stack.wb_url}/v1/config",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            trust_env=False,
            timeout=60,
        )
        return cast("dict[str, Any]", got.json())

    def schema(self) -> dict[str, dict[str, Any]]:
        got = httpx.get(
            f"{self.stack.wb_url}/v1/config/schema",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            trust_env=False,
            timeout=60,
        )
        return {f["key"]: f for f in got.json()["fields"]}

    @contextmanager
    def sorter_off(self) -> Iterator[None]:
        """The search as it was before a sorter: for the order the passages are found in."""
        before = self.config()["documentsSortModel"]
        self.settings(documentsSortModel="off")
        try:
            yield
        finally:
            self.settings(documentsSortModel=None if before == "auto" else before)

    def hybrid_of(self, indices: list[int]) -> None:
        """The hybrid order (the sorter off) of these questions, kept."""
        missing = [i for i in indices if i not in self.hybrid]
        if not missing:
            return
        with self.sorter_off():
            for i in missing:
                self.hybrid[i] = self.ask(i)

    def ask(self, i: int, **more: Any) -> dict[str, Any]:
        """Test a question, as the question is asked of the person's documents; how long it took
        is in `wall`."""
        started = time.perf_counter()
        found = self.ada.test(self.questions[i]["question"], passages=5, **more)
        found["wall"] = time.perf_counter() - started
        return found

    def ask_with_gateway(self, i: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """A question, and what the gateway answered to the sort it made (nothing when it made none)."""
        mark = self.stack.door_sorts.mark()
        found = self.ask(i)
        asked = self.questions[i]["question"].strip()
        door = [
            e
            for e in self.stack.door_sorts.since(mark)
            if str(e["body"].get("query", "")).strip() == asked
        ]
        return found, door

    def sorting_again(self, qs: list[int], seconds: float = 120.0) -> int:
        """Ask until the sorter has sorted two questions in a row, and how many were not sorted on the
        way. After a sorter was ended the gateway tries it again before it trusts it: one question
        is let through, and another a second later; so a stage that needs the sorter waits for that."""
        end = time.perf_counter() + seconds
        run, missed, k = 0, 0, 0
        while time.perf_counter() < end and run < 2:
            found = self.ask(qs[k % len(qs)])
            k += 1
            if found["sort"]["used"]:
                run += 1
                time.sleep(1.2)
            else:
                run = 0
                missed += 1
                time.sleep(0.5)
        if run < 2:
            note("the sorter did not sort two questions in a row in time")
        return missed

    # --- the whole -----------------------------------------------------------

    def go(self) -> None:
        for key, title, stage in STAGES:
            if key not in self.stages and key not in ALWAYS:
                continue
            began = time.perf_counter()
            say(title)
            stage(self)
            STAGE_SECONDS[key] = round(time.perf_counter() - began, 1)

    def teardown(self) -> None:
        say("teardown by pid")
        still = self.stack.down()
        check("end.ports", "no port of this run is still listening", not still, still)

    # ----------------------------------------------------------------------------------------
    # 0
    # ----------------------------------------------------------------------------------------

    def stage_isolate(self) -> None:
        ambient = sorted(k for k in os.environ if k.upper().startswith("EUGENE_PLEXUS_"))
        leaked = [
            k
            for k in self.stack.base_env
            if k.upper().startswith("EUGENE_PLEXUS_")
            or k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
        ]
        check(
            "iso.env",
            "no ambient EUGENE_PLEXUS_* variable or proxy reaches a child "
            f"({len(ambient)} ambient variable(s) dropped: {', '.join(ambient) or 'none'})",
            not leaked,
            leaked,
        )
        note(f"every child dies with this process: {children_die_with_me()}")
        ports = self.stack.port
        bad = [v for v in ports.values() if 8079 <= v <= 8190 or 8279 <= v <= 8283]
        check(
            "iso.ports",
            "no port is in the live install's ranges (8079-8190, 8279-8283)",
            not bad,
            bad,
        )
        needed = [
            WB_PY, GW_PY, DRV_PY, LLAMA, EMBED_FILE, SORTER_FILE, QUESTIONS, WB / "src",
        ]  # fmt: skip
        if "chat" in self.stages or "stopped" in self.stages:
            needed.append(CHAT_FILE)
        if "named" in self.stages:
            needed.append(BGE_FILE)
        missing = [str(p) for p in needed if not p.exists()]
        if not check(
            "iso.inputs",
            "the interpreters, the engine, the models and the questions are there",
            not missing,
            missing,
        ):
            raise SystemExit(1)
        heads = {}
        for repo in ("gateway", "inference-driver", "workbench", "specs"):
            got = subprocess.run(
                ["git", "-C", str(ROOT / repo), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            dirty = subprocess.run(
                ["git", "--no-optional-locks", "-C", str(ROOT / repo), "status", "--short"],
                capture_output=True,
                text=True,
                check=False,
            )
            heads[repo] = got.stdout.strip() + (
                " (working tree changed)" if dirty.stdout.strip() else ""
            )
        NUMBERS["heads"] = heads
        note(f"heads: {heads}")
        note(f"Workbench under test: {WB}")
        # The questions, and the documents they are about.
        self.questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
        total = self.total = len(self.questions)
        if self.args.questions:
            self.questions = self.questions[: self.args.questions]
        self.partial = len(self.questions) < total
        docs = os.environ.get("EP_DESIGN_DOCS")
        if docs:
            self.design = Path(docs)
        else:
            if self.design.exists():
                shutil.rmtree(self.design)
            self.design.mkdir(parents=True)
            archive = subprocess.run(
                ["git", "-C", str(ROOT / "specs"), "archive", SPECS_PIN, "docs/design"],
                capture_output=True,
                check=True,
            ).stdout
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(self.design, filter="data")
            self.design = self.design / "docs" / "design"
        files = sorted(self.design.glob("*.md"))

        def headings_of(name: str) -> set[str]:
            found: set[str] = set()
            fenced = False
            for line in (self.design / name).read_text(encoding="utf-8").splitlines():
                if line.lstrip().startswith(("```", "~~~")):
                    fenced = not fenced
                    continue
                if not fenced and (m := re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)):
                    found.add(squash(m.group(2)))
            return found

        unlabelled = [
            q for q in self.questions if squash(q["heading"]) not in headings_of(q["file"])
        ]
        check(
            "iso.questions",
            f"{len(files)} design documents (specs {SPECS_PIN}) and {len(self.questions)} of "
            f"{total} questions, each labelled with a heading that is in its file",
            len(files) == MR5_RECORD["files"] and not unlabelled,
            (len(files), unlabelled[:2]),
        )
        # The page Chrome opens is built from the source at HEAD, not found lying about.
        if "chrome" in self.stages:
            self.build_page()
        else:
            self.static.mkdir(parents=True, exist_ok=True)
            (self.static / "index.html").write_text(
                "<!doctype html><title>Workbench</title>", encoding="utf-8"
            )

    def build_page(self) -> None:
        started = time.perf_counter()
        web = REPO / "web"
        built = subprocess.run(
            [
                str(web / "node_modules" / ".bin" / "vite.cmd"),
                "build",
                "--outDir",
                str(self.static),
                "--emptyOutDir",
            ],
            cwd=str(web),
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        index = self.static / "index.html"
        check(
            "iso.page",
            "the page Chrome opens is built from the page's source now "
            f"({time.perf_counter() - started:.0f} s)",
            built.returncode == 0 and index.exists(),
            (built.stdout + built.stderr)[-600:],
        )

    # ----------------------------------------------------------------------------------------
    # 1
    # ----------------------------------------------------------------------------------------

    def stage_stack(self) -> None:
        s = self.stack
        p = s.port
        s.serve_all()
        s.runtime_for("sorter")
        s.start_engine("embed")
        s.start_engine("sorter")
        engines_up = {k: s.engine_ready(k) for k in ("embed", "sorter")}
        if not check(
            "stack.engines",
            "the search model and the result sorter are loaded (CPU)",
            all(engines_up.values()),
            engines_up,
        ):
            raise SystemExit(1)
        s.set_runtime("mr6b-sorter", "ready")
        s.start_driver("embed")
        s.start_driver("sorter")
        s.start_driver("outside")
        s.start_gateway()
        for name in ("gateway", "drv_embed", "drv_sorter", "drv_outside", "oidc"):
            if not until(lambda n=name: listening(p[n]), 120):  # type: ignore[misc]
                check("stack.listening", f"something listens on {name}; logs in {s.logs}", False)
                raise SystemExit(1)

        def routed() -> bool:
            return (
                s.listed(EMBED, "embeddings", "local")
                and s.listed(SORTER, "rerank", "local")
                and s.listed(OUTSIDE, "rerank", "external")
            )

        got = until(routed, 240, every=1.0)
        listing = s.gateway_models()
        shown = {
            k: (v.get("surfaces"), v.get("locality"), v.get("ready_backends"))
            for k, v in listing.items()
        }
        check(
            "stack.listing",
            f"the gateway lists {EMBED} as a search model and {SORTER} as a result sorter (local), "
            f"and {OUTSIDE} as a result sorter outside the machines (external), each running",
            bool(got),
            shown,
        )
        note("the gateway lists: " + "; ".join(f"{k} {v}" for k, v in shown.items()))
        order = [m for m in listing if "rerank" in (listing[m].get("surfaces") or [])]
        check(
            "stack.order",
            f"the gateway lists {OUTSIDE} before {SORTER}, so the first sorter listed is the one "
            "outside the machines",
            order[:2] == [OUTSIDE, SORTER],
            order,
        )
        s.start_workbench(self.static)
        if not until(lambda: listening(p["workbench"]), 90):
            check("stack.workbench", f"Workbench listens; logs in {s.logs}", False)
            raise SystemExit(1)
        self.ada = Person("p-ada", s.wb_url)
        self.bo = Person("p-bo", s.wb_url)
        self.owner = Person("operator", s.wb_url)
        for person in (self.ada, self.bo, self.owner):
            person.sign_in()
        check(
            "stack.signin",
            "Workbench is up and Ada, Bo and the owner sign in with Eugene",
            all(x.secret for x in (self.ada, self.bo, self.owner)),
        )
        # The shipped defaults, before anything is changed (reading the settings asks the
        # gateway nothing).
        values = self.config()
        check(
            "defaults.values",
            "as shipped, the result sorter is automatic and Sort time is 3 s",
            values["documentsSortModel"] == "auto" and values["documentsSortSeconds"] == 3.0,
            {k: v for k, v in values.items() if "Sort" in k},
        )
        # Before the sorter is looked at: nothing may ask it. Turn it off first, so that nothing the
        # import does (the Documents screen is read all the while) asks the sorter anything.
        self.settings(documentsSortModel="off")

    # ----------------------------------------------------------------------------------------
    # 2
    # ----------------------------------------------------------------------------------------

    def stage_documents(self) -> None:
        ada = self.ada
        files = [
            (f.name, f.read_bytes(), "text/markdown") for f in sorted(self.design.glob("*.md"))
        ]
        person_dir = self.stack.data / "knowledge" / person_key("p-ada")
        if self.args.reuse_data:
            source = Path(self.args.reuse_data)
            shutil.copytree(source / "knowledge", self.stack.data / "knowledge", dirs_exist_ok=True)
            self.reused = True
            note(f"the index of an earlier run is used, from {source}")
            listed = until(lambda: len(ada.documents()["documents"]) > 0, 30)
            check(
                "docs.reused",
                "the index of an earlier run holds the design documents",
                bool(listed),
            )
        else:
            started = time.perf_counter()
            made = ada.add(*files)
            body = made.json()
            check(
                "docs.kept",
                f"{len(files)} files are kept at once and nothing is refused",
                made.status_code == 201
                and body["refused"] == []
                and len(body["documents"]) == len(files),
                made.text[:300],
            )
            last = 0.0
            deadline = time.perf_counter() + 3600
            listed = ada.documents()
            while time.perf_counter() < deadline:
                listed = ada.documents()
                totals = listed["totals"]
                if time.perf_counter() - last >= 60:
                    last = time.perf_counter()
                    log(f"  import: meaning {totals['embedded']} of {totals['passages']} passages")
                if all(d["state"] in ("ready", "skipped", "failed") for d in listed["documents"]):
                    break
                time.sleep(3.0)
            NUMBERS["import_minutes"] = round((time.perf_counter() - started) / 60, 1)
            note(f"imported in {NUMBERS['import_minutes']} minutes")
        listed = ada.documents()
        totals = listed["totals"]
        states = {d["state"] for d in listed["documents"]}
        check(
            "docs.ready",
            "every file is ready and every passage has its meaning",
            states == {"ready"}
            and totals["embedded"] == totals["passages"]
            and totals["owed"] == 0,
            (states, totals),
        )
        check(
            "docs.passages",
            f"the index holds {MR5_RECORD['passages']:,} passages in {MR5_RECORD['files']} files, as MR5's run of record did",
            totals["passages"] == MR5_RECORD["passages"]
            and len(listed["documents"]) == MR5_RECORD["files"],
            (totals, len(listed["documents"])),
        )
        NUMBERS["passages"] = totals["passages"]
        self.index = IndexFile(person_dir / "index.sqlite3")
        if not self.reused:
            # What a later run may use instead of the import: the index, written whole.
            snap = (
                Path(self.args.snapshot)
                if self.args.snapshot
                else SCRATCH / "mr6b" / "index-snapshot"
            )
            if snap.exists():
                shutil.rmtree(snap)
            target = snap / "knowledge" / person_dir.name
            shutil.copytree(
                person_dir,
                target,
                ignore=shutil.ignore_patterns("index.sqlite3*"),
            )
            src = sqlite3.connect(
                f"file:{(person_dir / 'index.sqlite3').as_posix()}?mode=ro", uri=True
            )
            dst = sqlite3.connect(target / "index.sqlite3")
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            note(f"the index is kept for the sabotage runs: {snap}")
            NUMBERS["snapshot"] = str(snap)

    # ----------------------------------------------------------------------------------------
    # 3
    # ----------------------------------------------------------------------------------------

    def stage_off(self) -> None:
        ada, s = self.ada, self.stack
        n = len(self.questions)
        # The screen and the two settings, with the sorter off.
        head = ada.search()
        sort = head.get("sort") or {}
        check(
            "off.screen",
            "with the sorter off the screen says so quietly: no sorter in use, no reason to show, "
            "and the mode does not say a sorter",
            sort.get("code") == "off"
            and sort.get("quiet") is True
            and sort.get("sorting") is False
            and sort.get("setting") == "off"
            and "sorted by" not in head["mode"],
            sort,
        )
        fields = self.schema()
        model_field, secs_field = fields["documentsSortModel"], fields["documentsSortSeconds"]
        check(
            "defaults.schema",
            "the two settings are in the config schema with their defaults and limits: Result sorter "
            "defaults to auto and offers auto and off (with the sorter off the gateway is not asked "
            "which sorters it lists); Sort time defaults to 3 s, from 0.5 to 120",
            model_field["default"] == "auto"
            and model_field["suggestions"] == ["auto", "off"]
            and secs_field["default"] == 3.0
            and secs_field["minimum"] == 0.5
            and secs_field["maximum"] == 120.0,
            (model_field.get("suggestions"), secs_field),
        )
        check(
            "off.settings-say-so",
            "the two settings say what off comes to: the sorter is off, and Sort time changes nothing",
            model_field["status"]["text"]
            == "Off: passages are left in the order the search found them."
            and secs_field["status"]["text"]
            == "Nothing is sorted now, so this changes nothing yet.",
            (model_field["status"], secs_field["status"]),
        )
        # The questions, by the words alone, by meaning alone and both: MR5's own record.
        found: dict[str, list[list[dict[str, Any]]]] = {"words": [], "meaning": [], "both": []}
        walls: list[float] = []
        reports = []
        for i in range(n):
            for label, ways in (("words", ["words"]), ("meaning", ["meaning"]), ("both", None)):
                got = self.ask(i, **({"ways": ways} if ways else {}))
                found[label].append(got["passages"])
                if label == "both":
                    self.hybrid[i] = got
                    walls.append(got["wall"])
                reports.append(got["sort"])
        hits = {k: hits_at(v, self.questions, 5) for k, v in found.items()}
        mrr_each = {k: reciprocal_ranks(v, self.questions, 5) for k, v in found.items()}
        recall = {k: sum(v) / n for k, v in hits.items()}
        mrr = {k: sum(v) / n for k, v in mrr_each.items()}
        print(
            f"\n  {n} questions over {len({q['file'] for q in self.questions})} files", flush=True
        )
        print("  the search as MR5 left it (the sorter off), k=5:", flush=True)
        print("                words (BM25)   meaning      hybrid", flush=True)
        print(
            f"  recall@5      {recall['words']:.3f} ({sum(hits['words'])})    {recall['meaning']:.3f} ({sum(hits['meaning'])})"
            f"   {recall['both']:.3f} ({sum(hits['both'])})",
            flush=True,
        )
        print(
            f"  MRR@5         {mrr['words']:.3f}          {mrr['meaning']:.3f}        {mrr['both']:.3f}",
            flush=True,
        )
        NUMBERS["hybrid_recall"] = round(recall["both"], 3)
        NUMBERS["hybrid_mrr"] = round(mrr["both"], 3)
        if not self.partial:
            same = (
                {k: sum(v) for k, v in hits.items()} == MR5_RECORD["counts"]
                and {k: round(v, 3) for k, v in recall.items()} == MR5_RECORD["recall"]
                and {k: round(v, 3) for k, v in mrr.items()} == MR5_RECORD["mrr"]
            )
            check(
                "off.mr5-record",
                "with the sorter off, the three searches reproduce MR5's record to the digit "
                "(recall@5 words 0.500, meaning 0.583, hybrid 0.600; MRR@5 0.331, 0.459, 0.432)",
                same,
                (recall, mrr),
            )
        check(
            "off.reported",
            "every question says the sorter is off, quietly, with no sorter named, and its mode "
            "does not say one",
            all(
                r["code"] == "off"
                and r["quiet"] is True
                and r["used"] is False
                and r["setting"] == "off"
                for r in reports
            )
            and all("sorted by" not in self.hybrid[i]["mode"] for i in self.hybrid),
            reports[:1],
        )
        asked_door = s.door_sorts.since(0)
        engine_real = [e for e in s.engine_sorts["sorter"].since(0) if e["query"] != DRIVER_PROBE]
        outside_real = [
            e for e in s.outside_asked.since(0) if e["query"] not in (None, DRIVER_PROBE)
        ]
        check(
            "off.asks-nothing",
            "with the sorter off nothing was asked of any sorter, by Workbench or by the screens "
            "(the import, the screens and the settings page were looked at all the while)",
            not asked_door and not engine_real and not outside_real,
            (len(asked_door), len(engine_real), len(outside_real)),
        )
        timings = {
            "hybrid_wall_median": round(statistics.median(walls), 3),
            "hybrid_wall_max": round(max(walls), 3),
        }
        NUMBERS.update(timings)
        note(
            f"a question without a sorter takes {timings['hybrid_wall_median']} s (median), at most {timings['hybrid_wall_max']} s"
        )

    # ----------------------------------------------------------------------------------------
    # 4
    # ----------------------------------------------------------------------------------------

    def stage_budget(self) -> None:
        ada, s = self.ada, self.stack
        few = list(range(min(8, len(self.questions))))
        self.hybrid_of(few)
        door_mark = s.door_sorts.mark()
        engine_mark = s.engine_sorts["sorter"].mark()
        # Automatic and the default Sort time (3 s): neither was ever set.
        self.settings(documentsSortModel=None, documentsSortSeconds=None)
        values = self.config()
        check(
            "budget.defaults",
            "back to the defaults, the settings are in effect as automatic and 3 s",
            values["documentsSortModel"] == "auto" and values["documentsSortSeconds"] == 3.0,
            values,
        )
        first = ada.sort() or {}
        first_code = first.get("code")
        note(
            f"the first look at the screen, which never waits for the measurement, saw: {first_code}"
        )
        measured = until(
            lambda: (s_ := ada.sort() or {}).get("code") not in (None, "measuring") and s_,
            90,
            every=1.0,
        )
        sort = cast("dict[str, Any]", measured or ada.sort() or {})
        rate = sort.get("ratePerSecond")
        typical = sort.get("typical") or {}
        probes = s.door_sorts.since(door_mark)
        NUMBERS["probe_rate"] = rate
        check(
            "budget.measured",
            "the sorter was measured by a quick test of two passages, once, before any question was "
            "sorted, and the screen says that is where its speed comes from",
            len(probes) == 1
            and len(probes[0]["body"]["documents"]) == 2
            and all(
                " > " in d.split("\n", 1)[0] and "\n" in d for d in probes[0]["body"]["documents"]
            )
            and set(probes[0]["body"]) == {"model", "query", "documents"}
            and sort.get("rateFrom") == "probe",
            (len(probes), sort.get("rateFrom")),
        )
        too_slow = bool(rate) and typical.get("tokens", 0) / rate > 3.0
        check(
            "budget.screen",
            "the default Sort time of 3 s does not sort on this processor, and the screen says why "
            "with the speed it measured: the sorter is named, the mode does not say it sorts, and "
            "the sentence has the rate, the seconds a usual sort would add and the 3 s",
            sort.get("code") == "over_budget"
            and sort.get("sorting") is False
            and sort.get("model") == SORTER
            and sort.get("budgetSeconds") == 3.0
            and too_slow
            and "sorted by" not in ada.search()["mode"],
            sort,
        )
        said = TYPICAL.match(sort.get("message") or "")
        check(
            "budget.screen-words",
            "the screen's sentence names the numbers it was decided by, and they are the numbers it "
            "carries: the sorter, its rate, the 18 usual passages, their seconds, and Sort time",
            said is not None
            and said["model"] == SORTER
            and close(num(said["rate"]), rate or -1)
            and int(said["passages"]) == typical.get("passages")
            and close(num(said["seconds"]), typical.get("seconds") or -1)
            and num(said["budget"]) == 3.0,
            (sort.get("message"), rate, typical),
        )
        fields = self.schema()
        status = fields["documentsSortSeconds"]["status"]
        parsed = SETTING_SPEED.match(status["text"])
        check(
            "budget.settings-say-so",
            "the two settings say what is in effect: the sorter in use (offering every sorter Eugene "
            "lists, none that is not a sorter) and, for Sort time, the rate, the seconds for 18 "
            "passages and that this is over 3 s, so nothing is sorted",
            fields["documentsSortModel"]["status"]["text"] == f"In use: {SORTER} (automatic)."
            and fields["documentsSortModel"]["suggestions"][:2] == ["auto", "off"]
            and SORTER in fields["documentsSortModel"]["suggestions"]
            and OUTSIDE in fields["documentsSortModel"]["suggestions"]
            and EMBED not in fields["documentsSortModel"]["suggestions"]
            and CHAT not in fields["documentsSortModel"]["suggestions"]
            and parsed is not None
            and parsed["verdict"] == "over this, so passages are not sorted"
            and close(num(parsed["rate"]), rate or -1)
            and status["level"] == "warning"
            and self.config()["documentsSortSeconds"] == 3.0,
            (status, fields["documentsSortModel"]["status"]),
        )
        # Test a question, for each of the first questions: the hybrid order, and why.
        rows = []
        for i in few:
            got = self.ask(i)
            rows.append(got)
        reasons = [r["sort"] for r in rows]
        parsed_rows = [OVER_BUDGET.match(r["message"] or "") for r in reasons]
        check(
            "budget.not-sorted",
            f"{len(rows)} questions at the default Sort time are answered in the hybrid order, each "
            "saying the sorter was not used and why: over budget, with its tokens, its rate, the "
            "seconds that would take and the 3 s",
            all(
                r["code"] == "over_budget"
                and r["used"] is False
                and r["model"] == SORTER
                and r["setting"] == "auto"
                and r["budgetSeconds"] == 3.0
                and (r["expectedSeconds"] or 0) > 3.0
                and r["quiet"] is False
                for r in reasons
            )
            and all(m is not None for m in parsed_rows)
            and all(
                int(m["passages"]) == r["passages"]
                and num(m["tokens"]) == r["tokens"]
                and close(num(m["rate"]), r["ratePerSecond"])
                and close(num(m["seconds"]), r["expectedSeconds"])
                and num(m["budget"]) == 3.0
                for m, r in zip(parsed_rows, reasons, strict=True)
                if m is not None
            ),
            reasons[:1],
        )
        check(
            "budget.hybrid-order",
            "and what they sent is the hybrid order: the same passages in the same order as with "
            "the sorter off, the mode does not say a sorter, and no passage has a place by one",
            all(order_of(r) == order_of(self.hybrid[i]) for i, r in zip(few, rows, strict=True))
            and all(r["mode"] == self.hybrid[i]["mode"] for i, r in zip(few, rows, strict=True))
            and all("sorter" not in p["why"]["ranks"] for r in rows for p in r["passages"]),
            [
                (i, order_of(r)[:2], order_of(self.hybrid[i])[:2])
                for i, r in zip(few, rows, strict=True)
                if order_of(r) != order_of(self.hybrid[i])
            ][:2],
        )
        check(
            "budget.same-rate",
            "the speed a question is priced at is the speed the screen says",
            all(r["ratePerSecond"] == sort["ratePerSecond"] for r in reasons),
            (reasons[0]["ratePerSecond"], sort.get("ratePerSecond")),
        )
        engine_real = [
            e for e in s.engine_sorts["sorter"].since(engine_mark) if e["query"] != DRIVER_PROBE
        ]
        door_real = s.door_sorts.since(door_mark)
        check(
            "budget.no-sort-asked",
            "no passage was sent to the sorter for a question: its one request was the quick test of "
            "two passages",
            len(engine_real) == 1
            and len(engine_real[0]["docs"]) == 2
            and len(door_real) == 1
            and len(door_real[0]["body"]["documents"]) == 2,
            (len(engine_real), len(door_real), [len(e["docs"]) for e in engine_real]),
        )
        NUMBERS["default_budget"] = {
            "rate_tokens_per_second": rate,
            "typical_seconds": typical.get("seconds"),
            "questions_checked": len(rows),
        }
        note(
            f"at the default Sort time the sorter reads {rate} tokens a second (quick test): "
            f"{typical.get('passages')} usual passages ({typical.get('tokens')} tokens) would take "
            f"{typical.get('seconds')} s"
        )

    # ----------------------------------------------------------------------------------------
    # 5
    # ----------------------------------------------------------------------------------------

    def stage_gate(self) -> None:
        ada, s = self.ada, self.stack
        n = len(self.questions)
        everything = list(range(n))
        self.hybrid_of(everything)
        index = self.index
        assert index is not None
        self.settings(documentsSortModel=None, documentsSortSeconds=SORT_SECONDS)
        head = until(
            lambda: (
                (h := ada.search()).get("sort", {}).get("code") not in (None, "measuring") and h
            ),
            90,
            every=1.0,
        )
        head = cast("dict[str, Any]", head or ada.search())
        sort = head["sort"]
        check(
            "gate.screen",
            f"Sort time raised to {SORT_SECONDS:g} s, the screen says the passages are sorted: the "
            f"mode says `, sorted by {SORTER}`, the sorter is in use and automatic, its speed is the "
            "quick test's",
            sort["code"] == "ok"
            and sort["sorting"] is True
            and sort["label"] == f"In use: {SORTER} (automatic)"
            and sort["locality"] == "local"
            and sort["budgetSeconds"] == SORT_SECONDS
            and sort["rateFrom"] == "probe"
            and head["mode"] == f"meaning and words, sorted by {SORTER}",
            (head["mode"], sort),
        )
        fields = self.schema()
        parsed = SETTING_SPEED.match(fields["documentsSortSeconds"]["status"]["text"])
        check(
            "gate.settings-say-so",
            "and the setting says it is within Sort time, so passages are sorted",
            parsed is not None
            and parsed["verdict"] == "within this, so passages are sorted"
            and fields["documentsSortSeconds"]["status"]["level"] == "info"
            and self.config()["documentsSortSeconds"] == SORT_SECONDS,
            fields["documentsSortSeconds"]["status"],
        )
        busy0, total0 = cpu_system()
        engine_pid = s.engines["sorter"].pid
        engine0 = cpu_process(engine_pid)
        rows: dict[int, dict[str, Any]] = {}
        wire: dict[int, dict[str, Any]] = {}
        began = time.perf_counter()
        for i in everything:
            d0, e0 = s.door_sorts.mark(), s.engine_sorts["sorter"].mark()
            rows[i] = self.ask(i)
            asked = self.questions[i]["question"].strip()
            wire[i] = {
                "door": [
                    e
                    for e in s.door_sorts.since(d0)
                    if str(e["body"].get("query", "")).strip() == asked
                ],
                "engine": [
                    e for e in s.engine_sorts["sorter"].since(e0) if e["query"].strip() == asked
                ],
            }
            if (i + 1) % 10 == 0:
                log(f"  sorted: {i + 1} of {n} questions asked")
        took = time.perf_counter() - began
        busy1, total1 = cpu_system()
        engine1 = cpu_process(engine_pid)
        self.sorted_ = rows
        # --- every question was sorted, and said so ---------------------------------------
        blocks = {i: rows[i]["sort"] for i in everything}
        check(
            "gate.sorted",
            f"every one of the {n} questions was sorted by {SORTER}, said so in its mode and in its "
            "report, and was found (none was left in the hybrid order)",
            all(
                b["used"] is True
                and b["code"] is None
                and b["model"] == SORTER
                and b["setting"] == "auto"
                and rows[i]["mode"] == f"meaning and words, sorted by {SORTER}"
                and rows[i]["status"] == "found"
                for i, b in blocks.items()
            ),
            [(i, b["code"], b["message"]) for i, b in blocks.items() if not b["used"]][:3],
        )
        sizes = [b["passages"] for b in blocks.values()]
        check(
            "gate.pool",
            "each sort was given between ten and twenty passages (the best ten of each way, each "
            "once), and more than ten for most questions (the union is wider than the hybrid's ten)",
            all(10 <= k <= 20 for k in sizes) and sum(k > 10 for k in sizes) >= 0.5 * n,
            (min(sizes), statistics.median(sizes), max(sizes)),
        )
        NUMBERS["sort_passages"] = {
            "min": min(sizes),
            "median": statistics.median(sizes),
            "max": max(sizes),
        }
        # --- what the sorter was sent -------------------------------------------------------
        one_each = all(len(w["door"]) == 1 and len(w["engine"]) == 1 for w in wire.values())
        check(
            "gate.one-request",
            "each question made exactly one request of the sorter, at the gateway's door and at the "
            "engine, and the two carry the same question and the same passages",
            one_each
            and all(
                w["door"][0]["body"]["documents"] == w["engine"][0]["docs"]
                and w["door"][0]["body"]["query"].strip() == w["engine"][0]["query"].strip()
                for w in wire.values()
                if len(w["door"]) == 1 and len(w["engine"]) == 1
            ),
            [
                (i, len(w["door"]), len(w["engine"]))
                for i, w in wire.items()
                if len(w["door"]) != 1 or len(w["engine"]) != 1
            ][:3],
        )
        door_ok = all(
            len(w["door"]) == 1
            and set(w["door"][0]["body"]) == {"model", "query", "documents"}
            and w["door"][0]["body"]["model"] == SORTER
            and w["door"][0]["status"] == 200
            for w in wire.values()
        )
        check(
            "gate.wire",
            "Workbench asked the gateway as Jina's body and no more (model, query, documents: no "
            "top_n), naming the sorter, and the gateway answered 200 each time",
            door_ok,
            [
                (i, w["door"][0]["status"], sorted(w["door"][0]["body"]))
                for i, w in wire.items()
                if w["door"]
                and (
                    w["door"][0]["status"] != 200
                    or set(w["door"][0]["body"]) != {"model", "query", "documents"}
                )
            ][:3],
        )
        bad_forms: list[Any] = []
        unknown = 0
        pool_chunks: dict[int, list[int]] = {}
        for i, w in wire.items():
            if len(w["engine"]) != 1:
                continue
            chunk_ids: list[int] = []
            for doc in w["engine"][0]["docs"]:
                owners = index.chunks_of(doc)
                if not owners:
                    unknown += 1
                    bad_forms.append((i, doc[:80]))
                chunk_ids.append(owners[0] if owners else -1)
            pool_chunks[i] = chunk_ids
        check(
            "gate.heading-form",
            "every passage the sorter was sent is, character for character, the headings of a "
            "passage in the person's index, a new line, and its words: the text of a real passage "
            "with its heading path, and never a snippet, a prefix or the words alone",
            unknown == 0 and bool(pool_chunks),
            (unknown, bad_forms[:2]),
        )
        with_heading = [
            d
            for w in wire.values()
            for e in w["engine"]
            for d in e["docs"]
            if "\n" in d and " > " in d.split("\n", 1)[0]
        ]
        every_doc = [d for w in wire.values() for e in w["engine"] for d in e["docs"]]
        check(
            "gate.headings-present",
            "and the headings really are there: most passages begin with a line of their heading "
            "path (`File > Section`)",
            len(with_heading) >= 0.9 * len(every_doc),
            (len(with_heading), len(every_doc)),
        )
        distinct = all(
            len(set(w["engine"][0]["docs"])) == len(w["engine"][0]["docs"])
            for w in wire.values()
            if len(w["engine"]) == 1
        )
        check(
            "gate.each-once",
            "no passage was sent twice in one request",
            distinct,
        )
        arith_bad = []
        for i, w in wire.items():
            if len(w["engine"]) != 1 or i not in pool_chunks:
                continue
            own = sum(index.rows[c][2] for c in pool_chunks[i] if c in index.rows)
            extra = blocks[i]["tokens"] - own
            per = extra / max(1, len(pool_chunks[i]))
            if extra <= 0 or abs(per - round(per)) > 1e-9 or not 1 <= per <= 80:
                arith_bad.append((i, blocks[i]["tokens"], own, len(pool_chunks[i])))
        check(
            "gate.tokens",
            "the tokens a sort is priced at are what the passages carry (their own counts, read "
            "from the index) and the question once for each passage",
            not arith_bad,
            arith_bad[:3],
        )
        # --- the places a person is shown are the places the sorter's own scores give -------
        place_bad: list[Any] = []
        first_bad: list[Any] = []
        for i, w in wire.items():
            if len(w["engine"]) != 1 or i not in pool_chunks:
                continue
            order = engine_order(w["engine"][0])
            if order is None:
                place_bad.append((i, "no score for each passage"))
                continue
            position = {pool_chunks[i][doc]: place for place, doc in enumerate(order, start=1)}
            seen_sorter = []
            for p in rows[i]["passages"]:
                ranks = p["why"]["ranks"]
                mine = [position[c] for c in p["chunkIds"] if c in position]
                if "sorter" in ranks:
                    seen_sorter.append(ranks["sorter"])
                    if not mine or ranks["sorter"] != min(mine):
                        place_bad.append((i, p["name"], ranks, mine))
                elif mine:
                    place_bad.append(
                        (i, p["name"], "a passage the sorter was sent has no place", mine)
                    )
            if seen_sorter != sorted(seen_sorter) or (seen_sorter and seen_sorter[0] != 1):
                place_bad.append((i, "not in the sorter's order", seen_sorter))
            top_doc = order[0]
            first = rows[i]["passages"][0] if rows[i]["passages"] else None
            if first is None or pool_chunks[i][top_doc] not in first["chunkIds"]:
                first_bad.append(i)
        check(
            "gate.places",
            "every passage's place by the sorter is the place the sorter's own scores give it "
            "(read from the engine's answer), in the order they are sent, and a passage the sorter "
            "was not sent has none",
            not place_bad,
            place_bad[:3],
        )
        check(
            "gate.first",
            "the first passage of every question is the one the sorter scored highest",
            not first_bad,
            first_bad[:5],
        )
        documented = {"via", "terms", "phrases", "inHeading", "ranks", "similarity"}
        score_fields = [
            i
            for i in everything
            if set(blocks[i]) != SORT_FIELDS
            or any(
                set(p["why"]) != documented
                or any(
                    not isinstance(v, int) or isinstance(v, bool)
                    for v in p["why"]["ranks"].values()
                )
                for p in rows[i]["passages"]
            )
        ]
        check(
            "gate.no-scores",
            "the sorter's scores are never shown: a question's report holds the documented fields "
            "only, and a passage's places by the sorter, the words and the meaning are whole "
            "numbers (places), never a score",
            not score_fields,
            score_fields[:3],
        )
        # --- what it took ------------------------------------------------------------
        seconds = [b["seconds"] for b in blocks.values() if b["seconds"] is not None]
        engine_seconds = [
            (w["engine"][0]["t1"] - w["engine"][0]["t0"])
            for w in wire.values()
            if len(w["engine"]) == 1 and w["engine"][0]["t1"]
        ]
        walls = [rows[i]["wall"] for i in everything]
        extra = [rows[i]["wall"] - self.hybrid[i]["wall"] for i in everything]
        said_vs_engine = [
            blocks[i]["seconds"] - (w["engine"][0]["t1"] - w["engine"][0]["t0"])
            for i, w in wire.items()
            if len(w["engine"]) == 1 and blocks[i]["seconds"] is not None
        ]
        check(
            "gate.seconds",
            "the seconds a report says a sort took are the engine's own seconds and a little more "
            "(the gateway and the driver): never less than the engine, and within two seconds of it",
            bool(said_vs_engine) and min(said_vs_engine) >= -0.05 and max(said_vs_engine) <= 2.0,
            (min(said_vs_engine, default=None), max(said_vs_engine, default=None)),
        )
        under = [
            i
            for i in everything
            if (blocks[i]["expectedSeconds"] or 0) > blocks[i]["budgetSeconds"]
        ]
        check(
            "gate.within-budget",
            f"every sort was within Sort time when it was begun ({SORT_SECONDS:g} s) and none took "
            "more than twice it",
            not under and all(x < 2 * SORT_SECONDS for x in seconds),
            (under[:3], max(seconds, default=None)),
        )
        after = ada.sort() or {}
        total_tokens = sum(b["tokens"] for b in blocks.values())
        overall = total_tokens / sum(seconds)
        check(
            "gate.rate",
            "after the sorts the screen's speed comes from them, and it is the speed they were "
            "made at: within a third of the tokens they carried over the seconds they took",
            after.get("rateFrom") == "sorts"
            and after.get("ratePerSecond") is not None
            and 0.67 * overall <= after["ratePerSecond"] <= 1.5 * overall,
            (after.get("rateFrom"), after.get("ratePerSecond"), round(overall, 1)),
        )
        predicted = [
            blocks[i]["expectedSeconds"] / blocks[i]["seconds"]
            for i in everything[5:]
            if blocks[i]["seconds"] and blocks[i]["expectedSeconds"]
        ]
        NUMBERS["sorted"] = {
            "wall_median": round(statistics.median(walls), 2),
            "wall_p90": round(percentile(walls, 90), 2),
            "wall_max": round(max(walls), 2),
            "sort_seconds_median": round(statistics.median(seconds), 2),
            "sort_seconds_p90": round(percentile(seconds, 90), 2),
            "sort_seconds_max": round(max(seconds), 2),
            "engine_seconds_median": round(statistics.median(engine_seconds), 2)
            if engine_seconds
            else None,
            "added_median": round(statistics.median(extra), 2),
            "rate_from_sorts": after.get("ratePerSecond"),
            "rate_overall": round(overall, 1),
            "predicted_over_actual_median": round(statistics.median(predicted), 2)
            if predicted
            else None,
            "all_questions_seconds": round(took, 1),
            "machine_busy_share": round((busy1 - busy0) / max(1, total1 - total0), 2),
            # What everything but the sorter's engine used of the whole machine meanwhile (other
            # people's work, and this run's own small parts), and how many cores the engine used.
            "others_share": (
                round(max(0, (busy1 - busy0) - (engine1 - engine0)) / max(1, total1 - total0), 2)
                if engine0 is not None and engine1 is not None
                else None
            ),
            "engine_cores": (
                round((engine1 - engine0) / 1e7 / took, 1)
                if engine0 is not None and engine1 is not None
                else None
            ),
        }
        note(
            f"a sorted question takes {NUMBERS['sorted']['wall_median']} s (median; p90 "
            f"{NUMBERS['sorted']['wall_p90']}, at most {NUMBERS['sorted']['wall_max']}) against "
            f"{statistics.median(self.hybrid[i]['wall'] for i in everything):.3f} s without a sorter: the sort adds "
            f"{NUMBERS['sorted']['added_median']} s; the sort itself {NUMBERS['sorted']['sort_seconds_median']} s "
            f"(engine {NUMBERS['sorted']['engine_seconds_median']} s); about {NUMBERS['sorted']['rate_overall']} tokens a second"
        )
        note(
            f"the quick test said {NUMBERS.get('probe_rate')} tokens a second; the sorts made it "
            f"{after.get('ratePerSecond')}; the price of a sort was {NUMBERS['sorted']['predicted_over_actual_median']} times what it took (median)"
        )
        # --- the gate -------------------------------------------------------------------
        self.judge_gate(everything)

    def judge_gate(self, everything: list[int]) -> None:
        n = len(everything)
        qs = self.questions
        sorted_found = [self.sorted_[i]["passages"] for i in everything]
        hybrid_found = [self.hybrid[i]["passages"] for i in everything]
        hit_s, hit_h = hits_at(sorted_found, qs, 5), hits_at(hybrid_found, qs, 5)
        rr_s, rr_h = reciprocal_ranks(sorted_found, qs, 5), reciprocal_ranks(hybrid_found, qs, 5)
        recall_s, recall_h = sum(hit_s) / n, sum(hit_h) / n
        mrr_s, mrr_h = sum(rr_s) / n, sum(rr_h) / n
        d_recall = paired_bootstrap([float(x) for x in hit_s], [float(x) for x in hit_h])
        d_mrr = paired_bootstrap(rr_s, rr_h)
        gained = [i for i in everything if hit_s[i] and not hit_h[i]]
        lost = [i for i in everything if hit_h[i] and not hit_s[i]]
        at1_s, at1_h = hits_at(sorted_found, qs, 1), hits_at(hybrid_found, qs, 1)
        at3_s, at3_h = hits_at(sorted_found, qs, 3), hits_at(hybrid_found, qs, 3)
        print(
            f"\n  {n} questions, recall@5 and MRR@5, the sorter ({SORTER}) against hybrid (MR5's order)",
            flush=True,
        )
        print("                          recall@5        MRR@5     recall@1  recall@3", flush=True)
        print(
            f"  hybrid                  {recall_h:.3f} ({sum(hit_h)})     {mrr_h:.3f}     {sum(at1_h) / n:.3f}     {sum(at3_h) / n:.3f}",
            flush=True,
        )
        print(
            f"  sorted                  {recall_s:.3f} ({sum(hit_s)})     {mrr_s:.3f}     {sum(at1_s) / n:.3f}     {sum(at3_s) / n:.3f}",
            flush=True,
        )
        print(
            f"  change [95% paired]    {d_recall[0]:+.3f} [{d_recall[1]:+.3f}, {d_recall[2]:+.3f}]"
            f"   {d_mrr[0]:+.3f} [{d_mrr[1]:+.3f}, {d_mrr[2]:+.3f}]",
            flush=True,
        )
        print(f"  gained {len(gained)} / lost {len(lost)} questions at recall@5", flush=True)
        for i in gained[:4]:
            print(
                f"    + q{i:02d} {qs[i]['question'][:70]!r} -> {qs[i]['file']} / {qs[i]['heading'][:50]}",
                flush=True,
            )
        for i in lost[:4]:
            print(
                f"    - q{i:02d} {qs[i]['question'][:70]!r} -> {qs[i]['file']} / {qs[i]['heading'][:50]}",
                flush=True,
            )
        changed = [i for i in everything if order_of(self.sorted_[i]) != order_of(self.hybrid[i])]
        did_not_fit = (
            sum(self.sorted_[i]["didNotFit"] > 0 for i in everything),
            sum(self.hybrid[i]["didNotFit"] > 0 for i in everything),
        )
        NUMBERS["gate"] = {
            "recall": {"hybrid": round(recall_h, 3), "sorted": round(recall_s, 3)},
            "recall_counts": {"hybrid": sum(hit_h), "sorted": sum(hit_s)},
            "mrr": {"hybrid": round(mrr_h, 3), "sorted": round(mrr_s, 3)},
            "recall_change": [round(x, 3) for x in d_recall],
            "mrr_change": [round(x, 3) for x in d_mrr],
            "gained": len(gained),
            "lost": len(lost),
            "order_changed": len(changed),
            "recall1": {"hybrid": round(sum(at1_h) / n, 3), "sorted": round(sum(at1_s) / n, 3)},
            "recall3": {"hybrid": round(sum(at3_h) / n, 3), "sorted": round(sum(at3_s) / n, 3)},
            "did_not_fit_questions": {"sorted": did_not_fit[0], "hybrid": did_not_fit[1]},
        }
        note(
            f"the sorter changed the order of the passages for {len(changed)} of {n} questions; passages that did not fit the budget: sorted {did_not_fit[0]} questions, hybrid {did_not_fit[1]}"
        )
        check(
            "gate.order-applied",
            "the sorter's order is what the question gets: the passages differ from the hybrid "
            "order for more than a third of the questions",
            len(changed) > n / 3,
            len(changed),
        )
        if self.partial:
            note(f"only {n} of {self.total} questions: the gate is not judged")
            return
        check(
            "gate.recall",
            "the gate: sorted is not worse than hybrid on recall@5 "
            f"({recall_s:.3f} against {recall_h:.3f}, change {d_recall[0]:+.3f} "
            f"[{d_recall[1]:+.3f}, {d_recall[2]:+.3f}])",
            recall_s >= recall_h,
            (recall_s, recall_h),
        )
        check(
            "gate.mrr",
            "the gate: sorted is better than hybrid on MRR@5 "
            f"({mrr_s:.3f} against {mrr_h:.3f}, change {d_mrr[0]:+.3f} [{d_mrr[1]:+.3f}, {d_mrr[2]:+.3f}])",
            mrr_s > mrr_h,
            (mrr_s, mrr_h),
        )
        check(
            "gate.mrr-interval",
            "and the improvement in MRR@5 is not noise: its paired 95% interval is above zero",
            d_mrr[1] > 0,
            d_mrr,
        )

    # ----------------------------------------------------------------------------------------
    # 6
    # ----------------------------------------------------------------------------------------

    def stage_composition(self) -> None:
        s, bo = self.stack, self.bo
        files = library_files()
        made = bo.add(*files)
        check(
            "comp.kept",
            f"Bo keeps a library of {len(files)} files of one passage each",
            made.status_code == 201 and made.json()["refused"] == [],
            made.text[:200],
        )
        listed = until(
            lambda: (
                (lst := bo.documents())
                and len(lst["documents"]) == len(files)
                and all(d["state"] == "ready" for d in lst["documents"])
                and lst["totals"]["embedded"] == lst["totals"]["passages"]
                and lst
            ),
            300,
            every=1.0,
        )
        check(
            "comp.ready",
            "every passage of it has its meaning, and every file is one passage",
            bool(listed) and listed["totals"]["passages"] == len(files),
            listed and listed["totals"],
        )
        index = IndexFile(s.data / "knowledge" / person_key("p-bo") / "index.sqlite3")

        def run_off(q: str) -> dict[str, dict[str, Any]]:
            with self.sorter_off():
                return {
                    "words": bo.test(q, ways=["words"], passages=10),
                    "meaning": bo.test(q, ways=["meaning"], passages=10),
                    "both": bo.test(q, passages=10),
                }

        self.settings(documentsSortModel=None, documentsSortSeconds=SORT_SECONDS)
        # The sorter has to be known to be quick enough before a question is asked.
        until(lambda: (bo.sort() or {}).get("code") in ("ok", "over_budget"), 90, every=1.0)
        setups, unions, orders, ways_one, wires = [], [], [], [], []
        for q in COMPOSITION_QUESTIONS:
            off = run_off(q)
            w_ids = [c for p in off["words"]["passages"] for c in p["chunkIds"]]
            m_ids = [c for p in off["meaning"]["passages"] for c in p["chunkIds"]]
            union = list(dict.fromkeys([*w_ids, *m_ids]))
            hybrid_ids = [c for p in off["both"]["passages"] for c in p["chunkIds"]]
            setups.append(
                {
                    "q": q,
                    "words": len(w_ids),
                    "meaning": len(m_ids),
                    "union": len(union),
                    "hybrid_ten_is_union": set(hybrid_ids) == set(union),
                }
            )
            expected = {index.sent_of(c) for c in union}
            # Both ways, sorted: the sorter is given the best ten of each way, each once.
            d0, e0 = s.door_sorts.mark(), s.engine_sorts["sorter"].mark()
            both = bo.test(q, passages=10)
            door = [e for e in s.door_sorts.since(d0) if e["body"]["query"].strip() == q]
            eng = [e for e in s.engine_sorts["sorter"].since(e0) if e["query"].strip() == q]
            sent = eng[0]["docs"] if len(eng) == 1 else []
            unions.append(
                (
                    q,
                    len(door) == 1
                    and len(eng) == 1
                    and sorted(sent) == sorted(expected)
                    and len(sent) == len(set(sent)) == len(union),
                    (
                        len(sent),
                        len(expected),
                        sorted(expected - set(sent))[:1],
                        sorted(set(sent) - expected)[:1],
                    ),
                )
            )
            wires.append(
                (
                    q,
                    len(door) == 1
                    and bool(sent)
                    and set(door[0]["body"]) == {"model", "query", "documents"}
                    and door[0]["body"]["model"] == SORTER
                    and all(
                        d.split("\n", 1)[0] == f"{index.rows[c][0]}"
                        and d.split("\n", 1)[1] == index.rows[c][1]
                        for d in sent
                        for c in index.chunks_of(d)[:1]
                    ),
                )
            )
            order = engine_order(eng[0]) if len(eng) == 1 else None
            chunk_order = (
                [(index.chunks_of(sent[k]) or [-1])[0] for k in order] if order is not None else []
            )
            got = [p["chunkIds"][0] for p in both["passages"]]
            orders.append(
                (
                    q,
                    order is not None
                    and both["sort"]["used"] is True
                    and got == chunk_order[: len(got)]
                    and len(got) == min(10, len(chunk_order))
                    and [p["why"]["ranks"].get("sorter") for p in both["passages"]]
                    == list(range(1, len(got) + 1)),
                    (got[:4], chunk_order[:4]),
                )
            )
            # One way alone: the sorter is given that way's best ten.
            one: dict[str, bool] = {}
            for way, ids in (("words", w_ids), ("meaning", m_ids)):
                e1 = s.engine_sorts["sorter"].mark()
                found = bo.test(q, ways=[way], passages=10)
                got_eng = [e for e in s.engine_sorts["sorter"].since(e1) if e["query"].strip() == q]
                sent_one = got_eng[0]["docs"] if len(got_eng) == 1 else []
                one[way] = found["sort"]["used"] is True and sorted(sent_one) == sorted(
                    index.sent_of(c) for c in ids
                )
            ways_one.append((q, one))
        NUMBERS["composition"] = setups
        discriminating = [x for x in setups if x["union"] > 10 and not x["hybrid_ten_is_union"]]
        check(
            "comp.setup",
            "the library sets the two ways against each other, so that the best ten of each way are "
            "more than ten passages and not the hybrid's ten (else this could not tell them apart)",
            len(discriminating) == len(setups),
            setups,
        )
        check(
            "comp.union",
            "the sorter was sent exactly the best ten of the words and the best ten of meaning, each "
            "passage once, and not one more or fewer (three questions, each as the two ways alone "
            "find them)",
            all(ok for _, ok, _ in unions),
            [(q, d) for q, ok, d in unions if not ok],
        )
        check(
            "comp.heading",
            "each of them was sent as its headings (`Title > Section`), a new line and its words, to "
            "the gateway as Jina's body with the sorter's name",
            all(ok for _, ok in wires),
            [q for q, ok in wires if not ok],
        )
        check(
            "comp.order",
            "the passages came back in the order of the sorter's own scores, each with its place by "
            "the sorter (1, 2, 3 ...), the first ten of them (every passage here is one chunk)",
            all(ok for _, ok, _ in orders),
            [(q, d) for q, ok, d in orders if not ok],
        )
        check(
            "comp.one-way",
            "a question asked of one way alone is still sorted, and the sorter is given that way's "
            "best ten",
            all(v for _, one in ways_one for v in one.values()),
            ways_one,
        )

    # ----------------------------------------------------------------------------------------
    # 7
    # ----------------------------------------------------------------------------------------

    def ensure_chat(self) -> bool:
        s = self.stack
        if "chat" not in s.engines:
            s.start_engine("chat")
            s.engine_ready("chat")
            s.start_driver("chat")
            until(
                lambda: (s.gateway_models().get(CHAT) or {}).get("context_length") is not None,
                120,
                every=1.0,
            )
        return (s.gateway_models().get(CHAT) or {}).get("context_length") is not None

    def chat(self, person: Person, text: str) -> dict[str, Any]:
        chat = person.post("/api/chats", json={"model": CHAT}).json()["id"]
        person.patch(f"/api/chats/{chat}", json={"settings": {"documents": "on"}})
        sent = person.post(f"/api/chats/{chat}/messages", json={"content": f"{text} /no_think"})
        assert sent.status_code == 201, sent.text
        return person.wait_answer(chat)

    def stage_chat(self) -> None:
        s, ada = self.stack, self.ada
        ok = self.ensure_chat()
        check("chat.listed", f"the gateway lists the chat model {CHAT} and its window", ok)
        if not ok:
            return
        i = next(
            (
                k
                for k in range(len(self.questions))
                if k in self.sorted_ and order_of(self.sorted_[k]) != order_of(self.hybrid[k])
            ),
            0,
        )
        question = self.questions[i]["question"]
        names = {p.name for p in self.design.glob("*.md")}
        # Too slow for Sort time: the answer is from the hybrid order, and says why.
        self.settings(documentsSortModel=None, documentsSortSeconds=None)
        until(lambda: (ada.sort() or {}).get("code") == "over_budget", 90, every=1.0)
        mark = s.chat_engine.mark()
        slow = self.chat(ada, question)
        slow_prompt = json.dumps([e["body"].get("messages") for e in s.chat_engine.since(mark)])
        # Quick enough: the passages are sorted.
        self.settings(documentsSortSeconds=SORT_SECONDS)
        until(lambda: (ada.sort() or {}).get("code") == "ok", 90, every=1.0)
        mark = s.chat_engine.mark()
        sorted_ = self.chat(ada, question)
        prompt = json.dumps([e["body"].get("messages") for e in s.chat_engine.since(mark)])
        r_slow, r_sorted = slow.get("retrieval") or {}, sorted_.get("retrieval") or {}
        src_slow = [
            x for x in slow.get("sources", []) if x.get("kind") == "document" and x.get("passage")
        ]
        src_sorted = [
            x
            for x in sorted_.get("sources", [])
            if x.get("kind") == "document" and x.get("passage")
        ]
        check(
            "chat.answered",
            "a chat with Documents on is answered by the real model, with the sorter too slow and "
            "with it sorting",
            slow.get("status") == "done" and sorted_.get("status") == "done",
            (slow.get("status"), sorted_.get("status")),
        )
        check(
            "chat.retrieval",
            f"the answer's retrieval says how it was searched: sorted by {SORTER} when it sorted, "
            "and, when it did not, that the sorter was over budget with its numbers",
            r_sorted.get("mode") == f"meaning and words, sorted by {SORTER}"
            and (r_sorted.get("sort") or {}).get("used") is True
            and r_slow.get("mode") == "meaning and words"
            and (r_slow.get("sort") or {}).get("code") == "over_budget"
            and OVER_BUDGET.match((r_slow.get("sort") or {}).get("message") or "") is not None,
            (
                r_sorted.get("mode"),
                r_sorted.get("sort"),
                r_slow.get("mode"),
                (r_slow.get("sort") or {}).get("code"),
            ),
        )
        check(
            "chat.sources",
            "under the answer, Sources still name the files: each is a passage of one of the design "
            "documents, with its heading, its place and how it was found (words, meaning or both), "
            "and its parts are exactly the parts of an answer that was not sorted",
            bool(src_sorted)
            and all(
                x["name"] in names and x["heading"] and x["via"] in ("words", "meaning", "both")
                for x in src_sorted
            )
            and [x["rank"] for x in src_sorted] == list(range(1, len(src_sorted) + 1))
            and bool(src_slow)
            and all(sorted(x) == sorted(src_slow[0]) for x in src_sorted),
            (
                sorted(src_sorted[0]) if src_sorted else None,
                sorted(src_slow[0]) if src_slow else None,
            ),
        )
        codes = [n.get("code", "") for n in r_sorted.get("notes", [])]
        check(
            "chat.no-note",
            "and no note about the sorter is added to the answer",
            not any("sort" in c for c in codes)
            and not any("sort" in json.dumps(x).lower() for x in src_sorted),
            codes,
        )
        # The passages the model was shown are the passages in the order the sorter chose.
        found = self.ada.test(f"{question} /no_think", passages=5)
        names_found = [p["chunkIds"] for p in found["passages"]]
        chat_chunks = [x["chunk_ids"] for x in src_sorted]
        positions = []
        for x in found["passages"][: len(src_sorted)]:
            at = prompt.find(json.dumps(x["text"].split("\n")[0][:60])[1:-1])
            positions.append(at)
        check(
            "chat.order-reached-model",
            "the passages reached the real engine in the order the sorter chose: the same passages "
            "that Test a question puts first for the same words, in the same order",
            bool(chat_chunks)
            and chat_chunks == names_found[: len(chat_chunks)]
            and all(a >= 0 for a in positions)
            and positions == sorted(positions)
            and "<documents-" in prompt,
            (chat_chunks[:2], names_found[:2], positions),
        )
        if src_slow:
            hybrid_found = None
            with self.sorter_off():
                hybrid_found = self.ada.test(f"{question} /no_think", passages=5)
            check(
                "chat.hybrid-order",
                "and with the sorter too slow the model was shown the hybrid order",
                [tuple(x["chunk_ids"]) for x in src_slow] == order_of(hybrid_found)[: len(src_slow)]
                and "<documents-" in slow_prompt,
                ([tuple(x["chunk_ids"]) for x in src_slow][:2], order_of(hybrid_found)[:2]),
            )
        # A follow-up in the same chat: the sorter reads what the search by meaning is asked, which
        # is the latest message and, after it, the start of the one before.
        first, second = question, self.questions[(i + 1) % len(self.questions)]["question"]
        mark = s.door_sorts.mark()
        chat_id = ada.post("/api/chats", json={"model": CHAT}).json()["id"]
        ada.patch(f"/api/chats/{chat_id}", json={"settings": {"documents": "on"}})
        for turn in (first, second):
            sent = ada.post(f"/api/chats/{chat_id}/messages", json={"content": f"{turn} /no_think"})
            assert sent.status_code == 201, sent.text
            ada.wait_answer(chat_id)
        asked = [e["body"]["query"] for e in s.door_sorts.since(mark)]
        latest, before = f"{second} /no_think".strip(), f"{first} /no_think"[:300].strip()
        check(
            "chat.follow-up",
            "in a chat, a follow-up is sorted by what the search by meaning is asked: the latest "
            "message and, after it, the start of the one before",
            asked == [f"{first} /no_think", f"{latest}\n\n{before}"],
            asked,
        )
        NUMBERS["chat"] = {"question": question, "sources": [x["name"] for x in src_sorted]}

    # ----------------------------------------------------------------------------------------
    # 8
    # ----------------------------------------------------------------------------------------

    def stage_chrome(self) -> None:
        ada, s = self.ada, self.stack
        self.settings(documentsSortModel=None, documentsSortSeconds=None)
        until(lambda: (ada.sort() or {}).get("code") == "over_budget", 90, every=1.0)
        # A question the sorter changes, if the gate found one.
        pick = next(
            (
                i
                for i in self.sorted_
                if order_of(self.sorted_[i]) != order_of(self.hybrid.get(i, self.sorted_[i]))
            ),
            0,
        )
        cfg = {
            "url": s.wb_url,
            "secret": ada.secret,
            "playwright": os.environ.get(
                "WORKBENCH_PLAYWRIGHT", "D:/py/eugene-plexus/ui/node_modules/playwright"
            ),
            "chrome": os.environ.get(
                "WORKBENCH_CHROME", "C:/Program Files/Google/Chrome/Application/chrome.exe"
            ),
            "cookies": [
                {
                    "name": c.name,
                    "value": c.value,
                    "url": s.wb_url,
                    "httpOnly": True,
                    "sameSite": "Lax",
                }
                for c in ada.http.cookies.jar
            ],
            "admin": ADMIN_TOKEN,
            "sorter": SORTER,
            "question": self.questions[pick]["question"],
            "raised": SORT_SECONDS,
            "shots": str(self.work / "shots"),
        }
        path = self.work / "browser.json"
        path.write_text(json.dumps(cfg), encoding="utf-8", newline="\n")
        started = time.perf_counter()
        ran = subprocess.run(
            ["node", str(REPO / "web" / "scripts" / "mr6b-sorting-browser.mjs"), str(path)],
            capture_output=True,
            text=True,
            timeout=900,
            cwd=str(self.work),
        )
        print(ran.stdout, flush=True)
        seen = 0
        for line in ran.stdout.splitlines():
            m = re.match(r"^CHECK (\S+) (PASS|FAIL) (.*)$", line)
            if m:
                seen += 1
                check(
                    f"chrome.{m[1]}",
                    m[3],
                    m[2] == "PASS",
                    ran.stderr[-400:] if m[2] == "FAIL" else "",
                )
        check(
            "chrome.ran",
            f"Chrome ran the page and reported {seen} checks ({time.perf_counter() - started:.0f} s)",
            ran.returncode == 0 and seen >= 6,
            ran.stderr[-600:],
        )
        NUMBERS["chrome_pictures"] = str(self.work / "shots")

    # ----------------------------------------------------------------------------------------
    # 9
    # ----------------------------------------------------------------------------------------

    def stage_named(self) -> None:
        ada, s = self.ada, self.stack
        few = list(range(min(6, len(self.questions))))
        self.hybrid_of(few)
        self.settings(documentsSortSeconds=SORT_SECONDS)
        # Named: the same sorter, by its id.
        self.settings(documentsSortModel=SORTER)
        until(lambda: (ada.sort() or {}).get("code") == "ok", 90, every=1.0)
        by_name = [self.ask(i) for i in few]
        auto_rows = self.sorted_
        check(
            "named.model",
            f"a sorter named in the setting is the one used ({SORTER}): the report says the setting "
            "is the name and the sorter sorted",
            all(
                r["sort"]["used"]
                and r["sort"]["model"] == SORTER
                and r["sort"]["setting"] == SORTER
                and r["mode"] == f"meaning and words, sorted by {SORTER}"
                for r in by_name
            ),
            by_name[0]["sort"],
        )
        if auto_rows:
            check(
                "named.same",
                "and it sorts as automatic did: the same passages in the same order",
                all(
                    order_of(r) == order_of(auto_rows[i])
                    for i, r in zip(few, by_name, strict=True)
                    if i in auto_rows
                ),
            )
        # Case: the words off and auto are the owner's, in any case.
        self.settings(documentsSortModel="AUTO")
        until(lambda: (ada.sort() or {}).get("setting") == "auto", 30)
        loud = self.ask(few[0])
        check(
            "named.case",
            "Auto is auto, whatever its case",
            loud["sort"]["used"] and loud["sort"]["model"] == SORTER,
            loud["sort"],
        )
        # A model that is not a sorter, and one that is not there.
        self.settings(documentsSortModel=EMBED)
        wrong = self.ask(few[0])
        self.settings(documentsSortModel="no-such-sorter")
        missing = self.ask(few[0])
        for label, row, code, words in (
            (
                "a model that is not a sorter",
                wrong,
                "not_sorter",
                f"Not sorted: {EMBED} is not a result sorter.",
            ),
            (
                "a name Eugene does not list",
                missing,
                "not_listed",
                "Not sorted: Eugene lists no result sorter named no-such-sorter.",
            ),
        ):
            check(
                f"named.{code}",
                f"{label} is named in the reason, and the passages go in the hybrid order",
                row["sort"]["code"] == code
                and (row["sort"]["message"] or "").startswith(words)
                and order_of(row) == order_of(self.hybrid[few[0]]),
                row["sort"],
            )
        # A second sorter, named.
        self.stack.bring_up("bge")
        up = until(lambda: s.listed(BGE, "rerank", "local"), 180, every=1.0)
        check(
            "named.second-listed",
            f"a second sorter ({BGE}) is started and the gateway lists it",
            bool(up),
        )
        if up:
            # Workbench looks at the gateway's list every ten seconds at most: a sorter that was
            # started a moment ago is seen when that look is made again.
            self.settings(documentsSortModel=BGE)
            seen_at = time.perf_counter()
            seen = until(lambda: (ada.sort() or {}).get("model") == BGE, 60, every=1.0)
            note(
                f"Workbench saw the sorter that was started after {time.perf_counter() - seen_at:.0f} s"
                if seen
                else "Workbench did not see the sorter that was started"
            )
            before_sorter = s.engine_sorts["sorter"].mark()
            before_bge = s.engine_sorts["bge"].mark()
            rows = [self.ask(i) for i in few]
            bge_sorts = [
                e for e in s.engine_sorts["bge"].since(before_bge) if e["query"] != DRIVER_PROBE
            ]
            qwen_sorts = [
                e
                for e in s.engine_sorts["sorter"].since(before_sorter)
                if e["query"] != DRIVER_PROBE
            ]
            different = sum(
                order_of(r) != order_of(auto_rows[i])
                for i, r in zip(few, rows, strict=True)
                if i in auto_rows
            )
            check(
                "named.second",
                f"the second sorter named in the setting is the one asked, and only it ({BGE}): its "
                f"engine sorted every question and {SORTER}'s was asked nothing",
                all(
                    r["sort"]["used"]
                    and r["sort"]["model"] == BGE
                    and r["mode"] == f"meaning and words, sorted by {BGE}"
                    for r in rows
                )
                and not qwen_sorts
                and len([e for e in bge_sorts if len(e["docs"]) > 2]) == len(few),
                (rows[0]["sort"], len(qwen_sorts), len(bge_sorts)),
            )
            note(f"{BGE} gave another order than {SORTER} for {different} of {len(few)} questions")
            self.settings(documentsSortModel=None)
            until(lambda: (ada.sort() or {}).get("model") == SORTER, 60)
            auto = self.ask(few[0])
            check(
                "named.auto-first",
                f"with both running, automatic uses the first the gateway lists ({SORTER})",
                auto["sort"]["model"] == SORTER and auto["sort"]["used"],
                auto["sort"],
            )
            s.stop_engine("bge", notice=0.5)
            s.kill(s.drivers["bge"])
            until(lambda: s.not_ready(BGE), 60, every=1.0)
        self.settings(documentsSortModel=None)

    # ----------------------------------------------------------------------------------------
    # 10
    # ----------------------------------------------------------------------------------------

    def stage_stopped(self) -> None:
        ada, s = self.ada, self.stack
        qs = list(range(min(10, len(self.questions))))
        self.hybrid_of(qs)
        self.settings(documentsSortModel=None, documentsSortSeconds=SORT_SECONDS)
        until(lambda: (ada.sort() or {}).get("code") == "ok", 90, every=1.0)
        before = self.ask(qs[0])
        check(
            "stop.before",
            "before it is stopped the sorter sorts",
            before["sort"]["used"] is True,
            before["sort"],
        )
        # In the middle of a sort: the engine ends while its request is in the air.
        result: list[Any] = []

        def mid() -> None:
            try:
                started = time.perf_counter()
                result.append(ada.test_raw(self.questions[qs[1]]["question"], passages=5))
                result.append(time.perf_counter() - started)
            except Exception as exc:
                result.append(exc)

        mark = s.engine_sorts["sorter"].mark()
        worker = threading.Thread(target=mid, daemon=True)
        worker.start()
        in_air = until(
            lambda: any(
                e["t1"] is None and e["query"] != DRIVER_PROBE
                for e in s.engine_sorts["sorter"].since(mark)
            ),
            30,
            every=0.05,
        )
        check(
            "stop.in-air", "a sort was in the air when the sorter's engine was ended", bool(in_air)
        )
        s.stop_engine("sorter")
        ended = time.perf_counter()
        worker.join(180)
        reply = result[0] if result else None
        answered = (
            reply is not None and not isinstance(reply, Exception) and reply.status_code == 200
        )
        body = reply.json() if answered else {}
        sort = body.get("sort") or {}
        check(
            "stop.mid-run",
            "the question whose sort was in the air when the sorter ended was still answered, from "
            "the hybrid order, and its reason is named: the sorter, and what was observed",
            answered
            and body.get("status") == "found"
            and sort.get("used") is False
            and (sort.get("message") or "").startswith("Not sorted: ")
            and SORTER in (sort.get("message") or "")
            and sort.get("code") in ("error", "not_running", "unreachable")
            and order_of(body) == order_of(self.hybrid[qs[1]])
            and "sorted by" not in body.get("mode", ""),
            (
                getattr(reply, "status_code", reply),
                reply.text[:300] if answered or hasattr(reply, "text") else reply,
            ),
        )
        note(
            f"what the gateway said of the sort that was ended: {[(e['status'], str(e['answer'])[:160]) for e in s.door_sorts.since(0)[-1:]]}"
        )
        NUMBERS["stop_mid_run"] = {"code": sort.get("code"), "message": sort.get("message")}
        # Straight after: the gateway may not know yet, and each question is still answered.
        quick = []
        for i in qs[2:4]:
            quick.append(self.ask(i))
        check(
            "stop.next",
            "the questions asked straight after are answered from the hybrid order too, each naming "
            "the sorter in its reason",
            all(
                r["status"] == "found"
                and r["sort"]["used"] is False
                and SORTER in (r["sort"]["message"] or "")
                and (r["sort"]["message"] or "").startswith("Not sorted: ")
                and order_of(r) == order_of(self.hybrid[i])
                for i, r in zip(qs[2:4], quick, strict=True)
            ),
            [r["sort"] for r in quick],
        )
        gone = until(
            lambda: (h := ada.sort() or {}).get("code") == "not_running" and h, 120, every=1.0
        )
        NUMBERS["stop_noticed_seconds"] = round(time.perf_counter() - ended, 1)
        stopped_sort = cast("dict[str, Any]", gone or ada.sort() or {})
        check(
            "stop.screen",
            "the screen then says the sorter is not running, naming it, that it is not sorting, and "
            "where to start it",
            bool(gone)
            and stopped_sort["sorting"] is False
            and stopped_sort["message"]
            == f"Not sorted: the sorter {SORTER} is not running. Start it in Eugene's console under Backends."
            and "sorted by" not in ada.search()["mode"],
            stopped_sort,
        )
        mark_door = s.door_sorts.mark()
        mark_engine = s.engine_sorts["sorter"].mark()
        rows = [self.ask(i) for i in qs[4:7]]
        check(
            "stop.report",
            "a question asked then is answered from the hybrid order and its report names the "
            "stopped sorter",
            all(
                r["status"] == "found"
                and r["sort"]["used"] is False
                and r["sort"]["code"] == "not_running"
                and r["sort"]["message"] == stopped_sort["message"]
                and order_of(r) == order_of(self.hybrid[i])
                for i, r in zip(qs[4:7], rows, strict=True)
            ),
            rows[0]["sort"],
        )
        check(
            "stop.asks-nothing",
            "and the stopped sorter is asked nothing",
            not s.door_sorts.since(mark_door)
            and not [
                e for e in s.engine_sorts["sorter"].since(mark_engine) if e["query"] != DRIVER_PROBE
            ],
            (len(s.door_sorts.since(mark_door)),),
        )
        if self.ensure_chat():
            words = self.questions[qs[7]]["question"]
            reply = self.chat(ada, words)
            retrieval = reply.get("retrieval") or {}
            sources = [
                x
                for x in reply.get("sources", [])
                if x.get("kind") == "document" and x.get("passage")
            ]
            # What the chat searched for is the question and the words that ask the model not to
            # think: its hybrid order is that of the same words with the sorter off.
            with self.sorter_off():
                hybrid_found = ada.test(f"{words} /no_think", passages=5)
            check(
                "stop.chat",
                "a chat is answered by the real model while the sorter is stopped: from the hybrid "
                "order, its Sources name the files, and its retrieval names the stopped sorter",
                reply.get("status") == "done"
                and (retrieval.get("sort") or {}).get("code") == "not_running"
                and bool(sources)
                and all(x["name"].endswith(".md") for x in sources)
                and [tuple(x["chunk_ids"]) for x in sources]
                == order_of(hybrid_found)[: len(sources)],
                (
                    reply.get("status"),
                    (retrieval.get("sort") or {}).get("code"),
                    [x["chunk_ids"] for x in sources][:2],
                    order_of(hybrid_found)[:2],
                ),
            )
        # Brought back.
        brought = time.perf_counter()
        ok = s.restart_engine("sorter")
        back = until(lambda: (h := ada.sort() or {}).get("code") == "ok" and h, 240, every=1.0)
        NUMBERS["stop_restored_seconds"] = round(time.perf_counter() - brought, 1)
        check(
            "stop.back",
            "the sorter is started again and the screen says it sorts",
            ok and bool(back) and back["sorting"] is True,
            back,
        )
        # The questions asked one straight after another: the gateway lets one through to try the
        # sorter that failed, and holds the next back for a second (a 503 that asks for a wait).
        burst = [self.ask_with_gateway(qs[k]) for k in (8, 9, 0, 1)]
        again = burst[0][0]
        check(
            "stop.sorted-again",
            f"a question asked then is sorted again by {SORTER}, its mode says so, and every passage "
            "has its place by the sorter",
            again["sort"]["used"] is True
            and again["sort"]["model"] == SORTER
            and again["mode"] == f"meaning and words, sorted by {SORTER}"
            and any("sorter" in p["why"]["ranks"] for p in again["passages"]),
            again["sort"],
        )
        held = [
            (row, door)
            for row, door in burst
            if any(e["status"] == 503 and e.get("retry_after") for e in door)
        ]
        NUMBERS["stop_held_questions"] = len(held)
        check(
            "stop.holding",
            "a question the gateway held back for a moment (a 503 that asks for a wait, while it tries "
            "the sorter again) is answered from the hybrid order, and its reason says the gateway "
            "asked Workbench to wait, in its own words: it never says the sorter, which is running, "
            "is not running, and never tells a person to start it",
            all(
                row["status"] == "found"
                and row["sort"]["used"] is False
                and row["sort"]["code"] == "error"
                and (row["sort"]["message"] or "").startswith(
                    "Not sorted: Eugene's gateway asked Workbench to wait "
                )
                and "is not running" not in (row["sort"]["message"] or "")
                and "Start it" not in (row["sort"]["message"] or "")
                and "cooling down or already probing recovery" in (row["sort"]["message"] or "")
                for row, _ in held
            ),
            [
                (row["sort"], [(e["status"], e.get("retry_after")) for e in door])
                for row, door in held
            ][:2],
        )
        if held:
            note(
                f"{len(held)} of the 3 questions asked straight after the first sorted one were held back by the gateway: "
                f"{held[0][0]['sort']['message']}"
            )
        else:
            note("the gateway held no question back this time")
        self.sorting_again(qs[:4])
        note(
            f"the sorter was noticed stopped after {NUMBERS['stop_noticed_seconds']} s and sorted again {NUMBERS['stop_restored_seconds']} s after it was started"
        )

    # ----------------------------------------------------------------------------------------
    # 11
    # ----------------------------------------------------------------------------------------

    def stage_hung(self) -> None:
        """The sorter does not end: it stops answering. A sort that takes more than twice Sort time is
        given up on, said so, and the next questions are not made to wait that long again.

        The sorter has read well for a while first (eight sorts), and Sort time is a good deal more
        than a usual sort takes, so that a give-up that is blended into that history, and not taken
        for what is known of the sorter, leaves a speed that still prices the next question inside
        Sort time: that is what makes the check tell the two apart on any processor."""
        ada, s = self.ada, self.stack
        pool = list(range(min(8, len(self.questions))))
        self.hybrid_of(pool)
        self.settings(documentsSortModel=None, documentsSortSeconds=SORT_SECONDS)
        if not s.listed(SORTER, "rerank", "local"):
            s.restart_engine("sorter")
        until(lambda: (ada.sort() or {}).get("code") == "ok", 120, every=1.0)
        self.sorting_again(pool[:3])
        history = {i: self.ask(i) for i in pool}
        sized = {i: r["sort"]["tokens"] for i, r in history.items() if r["sort"]["used"]}
        rate = (ada.sort() or {}).get("ratePerSecond") or 700.0
        # The three questions with the fewest passages to sort, smallest first: the one that
        # hangs is not the largest, so that the questions after it are not smaller than half of it.
        ranked = sorted(sized, key=lambda i: sized[i])
        picks = ranked[:3] if len(ranked) >= 3 else (ranked or pool[:3])
        while len(picks) < 3:
            picks.append(picks[-1] if picks else 0)
        # Sort time: a usual sort of these questions takes a quarter of it or less.
        usual = max(sized.get(i, 0) for i in picks) / rate
        budget = float(max(15, 5 * math.ceil(4 * usual / 5)))
        NUMBERS["hung"] = {
            "usual_sort_seconds": round(usual, 1),
            "sort_time": budget,
            "history_sorts": len(sized),
        }
        warm_q, hung_q, later_q = picks
        self.settings(documentsSortSeconds=budget)
        until(lambda: (ada.sort() or {}).get("code") == "ok", 90, every=1.0)
        warm = self.ask(warm_q)
        check(
            "hung.before",
            f"with Sort time at {budget:g} s (a usual sort of these questions takes {usual:.1f} s) the "
            "sorter sorts, and a sort is expected to take less than that",
            len(sized) == len(pool)
            and warm["sort"]["used"] is True
            and (warm["sort"]["expectedSeconds"] or 99) < budget
            and (warm["sort"]["seconds"] or 99) < budget,
            (len(sized), warm["sort"]),
        )
        s.freeze_engine("sorter", True)
        try:
            started = time.perf_counter()
            stuck = self.ask(hung_q)
            waited = time.perf_counter() - started
            said = stuck["sort"]["message"] or ""
            check(
                "hung.given-up",
                f"a sorter that stops answering is given up on after twice Sort time ({2 * budget:g} s): "
                "the question is answered from the hybrid order, and the reason says the sorter did "
                "not answer in that time, naming it and Sort time",
                stuck["status"] == "found"
                and stuck["sort"]["used"] is False
                and stuck["sort"]["code"] == "slow"
                and said.startswith("Not sorted: ")
                and SORTER in said
                and f"{2 * budget:g} s" in said
                and f"Sort time, {budget:g} s" in said
                and 2 * budget - 1 <= waited <= 2 * budget + 15
                and order_of(stuck) == order_of(self.hybrid[hung_q])
                and "sorted by" not in stuck["mode"],
                (stuck["sort"], round(waited, 1)),
            )
            NUMBERS["hung_waited_seconds"] = round(waited, 1)
            door_mark = s.door_sorts.mark()
            followers = []
            for k in (later_q, warm_q):
                started = time.perf_counter()
                found = self.ask(k)
                followers.append((k, found, time.perf_counter() - started))
            check(
                "hung.not-again",
                "and the next questions are not made to wait that long again: the sort that was given "
                "up on is what is known of the sorter now, so each is over Sort time, said with the "
                "numbers, answered at once from the hybrid order, and the sorter is not asked",
                all(
                    found["sort"]["code"] == "over_budget"
                    and found["sort"]["used"] is False
                    and OVER_BUDGET.match(found["sort"]["message"] or "") is not None
                    and took < 5
                    and order_of(found) == order_of(self.hybrid[k])
                    for k, found, took in followers
                )
                and not s.door_sorts.since(door_mark),
                [(f["sort"], round(t, 1)) for _, f, t in followers],
            )
            shown = ada.sort() or {}
            check(
                "hung.screen",
                "and the screen says the sorter is too slow for Sort time now, with the speed it has "
                "(the given-up sort's), which is less than the speed it had",
                shown.get("code") == "over_budget"
                and (shown.get("ratePerSecond") or 1e9)
                < 0.8 * (warm["sort"]["ratePerSecond"] or 0),
                (shown.get("code"), shown.get("ratePerSecond"), warm["sort"]["ratePerSecond"]),
            )
        finally:
            s.freeze_engine("sorter", False)
        until(lambda: s.engine_ready("sorter", 1), 60, every=1.0)
        # Let go on, and Sort time raised past what the sort that was given up on showed (it did not
        # read this much in twice the old Sort time), the sorter is used again.
        raised = min(120.0, 4 * budget)
        self.settings(documentsSortSeconds=raised)
        until(lambda: (ada.sort() or {}).get("code") == "ok", 90, every=1.0)
        back = self.ask(hung_q)
        check(
            "hung.back",
            "let go on, and with Sort time raised past what the sort that was given up on showed "
            f"({raised:g} s), the sorter sorts again",
            back["sort"]["used"] is True and back["sort"]["model"] == SORTER,
            back["sort"],
        )

    # ----------------------------------------------------------------------------------------
    # 12
    # ----------------------------------------------------------------------------------------

    def stage_outside(self) -> None:
        ada, s = self.ada, self.stack
        qs = list(range(min(6, len(self.questions))))
        self.hybrid_of(qs)
        self.settings(
            documentsSortModel=None,
            documentsSortSeconds=SORT_SECONDS,
            documentsExternalEmbedding=None,
        )
        if not s.listed(SORTER, "rerank", "local"):
            s.restart_engine("sorter")
        until(lambda: (ada.sort() or {}).get("code") == "ok", 120, every=1.0)
        self.sorting_again(qs[:3])

        def private() -> list[dict[str, Any]]:
            """What the sorter outside the machines was asked, but for the driver's own test."""
            return [e for e in s.outside_asked.since(0) if e["query"] not in (None, DRIVER_PROBE)]

        # Automatic, the owner allowing text outside the machines or not: never the outside one.
        for allowed in (False, True):
            self.settings(documentsExternalEmbedding=allowed)
            head = ada.sort() or {}
            rows = [self.ask(i) for i in qs]
            check(
                f"outside.auto-{'allowed' if allowed else 'not-allowed'}",
                "automatic uses the sorter on the machines, never the one outside them, even when the "
                f"owner has {'allowed' if allowed else 'not allowed'} sending the text of documents outside "
                "(and the outside one is listed first, running)",
                head.get("model") == SORTER
                and OUTSIDE in head.get("outside", [])
                and head.get("locality") == "local"
                and all(r["sort"]["used"] and r["sort"]["model"] == SORTER for r in rows)
                and not private(),
                (
                    head.get("model"),
                    head.get("outside"),
                    head.get("locality"),
                    [(r["sort"]["model"], r["sort"]["used"], r["sort"]["code"]) for r in rows],
                    len(private()),
                ),
            )
        # The sorter on the machines is stopped: automatic does not turn to the one outside.
        s.stop_engine("sorter")
        until(lambda: (ada.sort() or {}).get("code") == "not_running", 120, every=1.0)
        head = ada.sort() or {}
        rows = [self.ask(i) for i in qs[:3]]
        check(
            "outside.auto-stopped",
            "with the sorter on the machines stopped and the one outside them running, automatic "
            "still says the sorter on the machines is not running and does not turn to the one "
            "outside: the passages are in the hybrid order and the outside one was asked nothing",
            head.get("code") == "not_running"
            and head.get("model") is None
            and all(
                r["sort"]["code"] == "not_running"
                and r["sort"]["used"] is False
                and order_of(r) == order_of(self.hybrid[i])
                for i, r in zip(qs[:3], rows, strict=True)
            )
            and not private(),
            (
                head.get("code"),
                head.get("model"),
                [r["sort"]["code"] for r in rows],
                len(private()),
            ),
        )
        # Named, and not allowed: said, and nothing sent.
        self.settings(documentsSortModel=OUTSIDE, documentsExternalEmbedding=False)
        until(lambda: (ada.sort() or {}).get("code") == "outside", 60)
        refused = self.ask(qs[0])
        check(
            "outside.named-not-allowed",
            "a sorter outside the machines, named but not allowed, is not used: the reason says it "
            "runs outside and the owner has not allowed it, the passages are in the hybrid order, "
            "and nothing was sent",
            refused["sort"]["code"] == "outside"
            and refused["sort"]["used"] is False
            and "outside your machines" in (refused["sort"]["message"] or "")
            and "has not allowed" in (refused["sort"]["message"] or "")
            and order_of(refused) == order_of(self.hybrid[qs[0]])
            and not private(),
            (refused["sort"], len(private())),
        )
        # Named and allowed: used, and its order is the one the questions get.
        self.settings(documentsExternalEmbedding=True)
        until(
            lambda: (
                (h := ada.sort() or {}).get("code") in ("ok", "over_budget", "measuring")
                and h.get("model") == OUTSIDE
            ),
            60,
        )
        until(lambda: (ada.sort() or {}).get("code") == "ok", 60, every=1.0)
        d0 = s.outside_asked.mark()
        allowed = [self.ask(i) for i in qs[:3]]
        asked = [e for e in s.outside_asked.since(d0) if e["query"] not in (None, DRIVER_PROBE)]
        index = self.index
        assert index is not None
        order_ok = []
        for i, row in zip(qs[:3], allowed, strict=True):
            got = [e for e in asked if e["query"].strip() == self.questions[i]["question"].strip()]
            if len(got) != 1:
                order_ok.append(False)
                continue
            docs = got[0]["docs"]
            expected = sorted(range(len(docs)), key=lambda k: (-fake_score(docs[k]), k))
            first = index.chunks_of(docs[expected[0]])
            order_ok.append(
                bool(row["passages"]) and bool(set(first) & set(row["passages"][0]["chunkIds"]))
            )
        check(
            "outside.named-allowed",
            "named, and allowed by the owner, the sorter outside the machines is used (it is asked "
            "for each question, each passage as its headings and words), the report names it, and "
            "its own order is the order the passages come in",
            all(
                r["sort"]["used"]
                and r["sort"]["model"] == OUTSIDE
                and r["mode"] == f"meaning and words, sorted by {OUTSIDE}"
                for r in allowed
            )
            and len(asked) == 3
            and all(order_ok)
            and (ada.sort() or {}).get("locality") == "external",
            ([r["sort"] for r in allowed][:1], len(asked), order_ok),
        )
        # Back to what is shipped, and the sorter that was stopped is brought back.
        self.settings(documentsSortModel=None, documentsExternalEmbedding=None)
        s.restart_engine("sorter")
        until(
            lambda: (h := ada.sort() or {}).get("code") == "ok" and h.get("model") == SORTER,
            120,
            every=1.0,
        )
        self.sorting_again(qs[:3])
        back = self.ask(qs[0])
        check(
            "outside.back",
            "with the settings back as shipped, automatic is the sorter on the machines again",
            back["sort"]["used"] and back["sort"]["model"] == SORTER,
            back["sort"],
        )
        # Only a sorter outside the machines is left: automatic says so and sends nothing.
        s.kill(s.drivers["sorter"])
        s.set_runtime("mr6b-sorter", "stopped", "its driver ended")
        s.stop_engine("sorter", notice=0.1)
        gone = until(lambda: SORTER not in s.gateway_models(), 120, every=1.0)
        until(lambda: (h := ada.sort() or {}).get("code") == "outside_only" and h, 120, every=1.0)
        head = ada.sort() or {}
        mark = s.outside_asked.mark()
        only = self.ask(qs[0])
        check(
            "outside.only",
            "when the only sorter Eugene lists runs outside the machines, automatic does not use it: "
            "the reason names it and says the text of documents is not sent there unless the owner "
            "names it and allows it, and the passages are in the hybrid order",
            bool(gone)
            and head.get("code") == "outside_only"
            and only["sort"]["code"] == "outside_only"
            and only["sort"]["used"] is False
            and OUTSIDE in (only["sort"]["message"] or "")
            and order_of(only) == order_of(self.hybrid[qs[0]])
            and not [
                e for e in s.outside_asked.since(mark) if e["query"] not in (None, DRIVER_PROBE)
            ],
            (head.get("code"), only["sort"], len(s.outside_asked.since(mark))),
        )


ALL_STAGES = [
    "off", "budget", "gate", "composition", "chat", "chrome", "named", "stopped", "hung", "outside"
]  # fmt: skip
ALWAYS = ("isolate", "stack", "documents")
STAGES: list[tuple[str, str, Callable[[Run], None]]] = [
    ("isolate", "0. isolate this run", Run.stage_isolate),
    ("stack", "1. the stack: engines, drivers, gateway, Workbench, three people", Run.stage_stack),
    ("documents", "2. Eugene's design documents, with the sorter turned off", Run.stage_documents),
    ("off", "3. the sorter off: hybrid, as MR5 left it", Run.stage_off),
    ("budget", "4. the default Sort time of 3 s", Run.stage_budget),
    ("gate", "5. the gate: Sort time raised, the questions sorted", Run.stage_gate),
    ("composition", "6. what the sorter is given, exactly", Run.stage_composition),
    ("chat", "7. a chat answered by the real model", Run.stage_chat),
    ("chrome", "8. the page in Chrome", Run.stage_chrome),
    ("named", "9. a sorter named, not a sorter, not there, a second one", Run.stage_named),
    (
        "stopped",
        "10. the sorter stopped in a sort, then for good, then brought back",
        Run.stage_stopped,
    ),
    ("hung", "11. a sorter that stops answering is given up on", Run.stage_hung),
    ("outside", "12. a sorter outside the machines", Run.stage_outside),
]


def main() -> int:
    cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stages", default="", help=f"comma separated, of: {', '.join(ALL_STAGES)}"
    )
    parser.add_argument(
        "--reuse-data", default="", help="a data directory with the index of an earlier run"
    )
    parser.add_argument("--snapshot", default="", help="where to keep the index after the import")
    parser.add_argument("--questions", type=int, default=0, help="only the first N questions")
    parser.add_argument(
        "--work", default="", help="the work directory (default: in the scratchpad)"
    )
    args = parser.parse_args()
    work = Path(args.work) if args.work else SCRATCH / "mr6b" / "acceptance-work"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    run = Run(args, work)
    started = time.perf_counter()
    try:
        run.go()
    except SystemExit:
        pass
    except Exception as exc:
        traceback.print_exc()
        check("run.plan", f"the run went to plan ({type(exc).__name__}: {exc})", False)
    finally:
        run.teardown()
    failed = [c for c in CHECKS if not c[2]]
    print(f"\nstages (seconds): {json.dumps(STAGE_SECONDS)}", flush=True)
    print(f"numbers: {json.dumps(NUMBERS, default=str)}", flush=True)
    print(f"logs: {run.stack.logs}", flush=True)
    print(
        f"\n{len(CHECKS) - len(failed)} passed, {len(failed)} failed "
        f"({time.perf_counter() - started:.0f} s)",
        flush=True,
    )
    for cid, claim, _, detail in failed:
        print(f"  FAILED [{cid}] {claim} ({detail[:300]})", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
