"""The child process that reads one file (MR2, MRC19).

    python -m eugene_plexus_workbench.knowledge.worker INPUT OUTPUT KIND [LANGUAGE]

Reads `INPUT` as a file of `KIND` (`extract.KINDS`) and writes one JSON
document to `OUTPUT`: `{"ok": true, ...}` followed by what `Extracted.to_json`
says, or `{"ok": false, "code", "reason", "state", "pages", "detail"}` for a
file that gives no text. It exits 0 whenever it wrote one of those. A file that
takes the process down (a PDF library that crashes, an out-of-memory kill) leaves
no output and a non-zero exit, and Workbench says so: that is the point of
running here and not in Workbench's own process.

The process limits itself before it opens anything: a cap on its memory where
the OS offers one (a Job Object on Windows, `RLIMIT_AS` on Linux; macOS offers
none, and the cap is then not set), no crash dialog on Windows, and no logging
(a hostile file can make a PDF library write a warning per object).
Workbench starts it with a bare environment and its own empty working
directory, so it holds none of Workbench's secrets and imports nothing the
file could have put beside it.
"""

from __future__ import annotations

import faulthandler
import json
import logging
import os
import sys
import warnings
from pathlib import Path

#: What one reader may allocate. A 50 MiB PDF of photographs needs a few
#: hundred megabytes; a file that wants more is refused by name.
MEMORY_CAP = 2 * 1024 * 1024 * 1024
#: A reader still going after this long ends itself, whether or not Python can
#: still run (the watchdog is C code in a thread of its own, so even a library
#: that never lets go of the interpreter is ended). Workbench ends a reader at its
#: own, shorter limit; this is for one whose Workbench is gone.
DEADLINE_SECONDS = 150.0


def limit_memory(limit: int) -> bool:
    """Cap this process's memory at `limit` bytes where the OS offers a way.
    True when a cap is set."""
    try:
        if sys.platform == "win32":
            return _limit_memory_windows(limit)
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        return True
    except (ImportError, OSError, ValueError, AttributeError):
        return False


def _limit_memory_windows(limit: int) -> bool:
    """A Job Object with a per-process memory limit, holding only this process.
    It also ends the process if the job is closed, which only this process can."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BasicLimits(ctypes.Structure):
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

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return False
    info = ExtendedLimits()
    process_memory, kill_on_close = 0x100, 0x2000
    info.BasicLimitInformation.LimitFlags = process_memory | kill_on_close
    info.ProcessMemoryLimit = limit
    extended = 9  # JobObjectExtendedLimitInformation
    if not kernel32.SetInformationJobObject(job, extended, ctypes.byref(info), ctypes.sizeof(info)):
        return False
    return bool(kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()))


def no_error_dialogs() -> None:
    """Windows shows a dialog, and waits for a click, when a process crashes;
    a reader that crashed must end, so the dialog is turned off."""
    if sys.platform != "win32":
        return
    import ctypes

    fail_critical, no_gp_fault, no_open_file = 0x0001, 0x0002, 0x8000
    ctypes.windll.kernel32.SetErrorMode(fail_critical | no_gp_fault | no_open_file)


def _write(output: Path, payload: dict[str, object]) -> None:
    """Whole or not at all: written beside the output, then renamed onto it."""
    part = output.with_name(output.name + ".part")
    part.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8", newline="\n")
    os.replace(part, output)


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print("usage: worker INPUT OUTPUT KIND [LANGUAGE]", file=sys.stderr)
        return 2
    source, output = Path(argv[0]), Path(argv[1])
    kind = argv[2]
    language = argv[3] if len(argv) == 4 and argv[3] != "-" else None
    no_error_dialogs()
    faulthandler.enable()
    faulthandler.dump_traceback_later(DEADLINE_SECONDS, exit=True)
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")
    limit_memory(MEMORY_CAP)
    from . import extract

    payload: dict[str, object]
    try:
        if kind == "page":
            options = json.loads(language or "{}")
            payload = {
                "ok": True,
                **extract.render_page(source, int(options["page"]), str(options.get("text") or "")),
            }
        else:
            payload = {"ok": True, **extract.read(source, kind, language).to_json()}
    except extract.Unreadable as exc:
        payload = {"ok": False, **exc.to_json()}
    except MemoryError:
        payload = {
            "ok": False,
            "code": "memory",
            "reason": (
                "Workbench could not read this file (it needs more memory than a reader is "
                f"given, {MEMORY_CAP // (1024 * 1024 * 1024)} GiB)."
            ),
            "state": "failed",
            "pages": None,
            "detail": "MemoryError",
        }
    except RecursionError as exc:
        payload = {
            "ok": False,
            "code": "damaged",
            "reason": (
                "Workbench could not read this file (it nests more deeply than a reader allows)."
            ),
            "state": "failed",
            "pages": None,
            "detail": f"RecursionError: {exc}",
        }
    except Exception as exc:
        payload = {
            "ok": False,
            "code": "damaged",
            "reason": "Workbench could not read this file "
            f"({extract.say(f'{type(exc).__name__}: {exc}')}).",
            "state": "failed",
            "pages": None,
            "detail": f"{type(exc).__name__}: {exc}",
        }
    _write(output, payload)
    faulthandler.cancel_dump_traceback_later()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
