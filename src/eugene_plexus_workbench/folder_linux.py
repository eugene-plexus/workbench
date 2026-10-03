"""Linux openat2 adapter. Unsupported kernels fail closed; no path fallback."""

from __future__ import annotations

import ctypes
import importlib
import os
import platform
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Self

from .folder_io import FolderError


def _flag(name: str) -> int:
    # This module is executed only on Linux; keep Windows type checking valid.
    return int(getattr(os, name))


class _How(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64), ("mode", ctypes.c_uint64), ("resolve", ctypes.c_uint64)]


def birth_time(fd: int) -> str:
    libc = ctypes.CDLL(None, use_errno=True)
    buffer = ctypes.create_string_buffer(256)  # Linux UAPI struct statx
    try:
        statx = libc.statx
    except AttributeError:
        raise FolderError(
            "This Linux host needs statx support for durable folder identity."
        ) from None
    statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
    statx.restype = ctypes.c_int
    if (
        statx(fd, b"", 0x1000, 0x0800, buffer) < 0
        or not struct.unpack_from("=I", buffer)[0] & 0x800
    ):
        raise FolderError("This filesystem does not expose a stable folder creation time.")
    seconds, nanos = struct.unpack_from("=qI", buffer, 80)
    return f"{seconds}:{nanos}"


def _open(parent: int, path: str, flags: int, *, create: bool = False, beneath: bool = True) -> int:
    if platform.machine().lower() not in {"x86_64", "aarch64", "amd64", "arm64"}:
        raise FolderError("Folder tools currently support 64-bit x86 and ARM Linux hosts.")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    # Linux UAPI: syscall 437, RESOLVE_NO_SYMLINKS, BENEATH and NO_XDEV.
    how = _How(
        flags | _flag("O_CLOEXEC") | _flag("O_NOFOLLOW"),
        0o660 if create else 0,
        0x04 | (0x08 | 0x01 if beneath else 0),
    )
    fd = libc.syscall(
        ctypes.c_long(437),
        ctypes.c_int(parent),
        os.fsencode(path),
        ctypes.byref(how),
        ctypes.sizeof(how),
    )
    if fd < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return int(fd)


class Root:
    def __init__(self, path: str) -> None:
        self.fd = _open(-100, path, os.O_RDONLY | _flag("O_DIRECTORY"), beneath=False)
        try:
            self.path = os.readlink(f"/proc/self/fd/{self.fd}")
        except BaseException:
            os.close(self.fd)
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        os.close(self.fd)

    @contextmanager
    def file(self, names: list[str], *, write: bool = False, create: bool = False) -> Iterator[int]:
        fcntl = importlib.import_module("fcntl")

        flags = (os.O_RDWR if write else os.O_RDONLY) | _flag("O_NONBLOCK")
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        fd = _open(self.fd, "/".join(names), flags, create=create)
        try:
            # Prevent concurrent Workbench/processes that honor advisory locks.
            # The hash is checked on this same descriptor after acquiring it.
            fcntl.flock(fd, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            yield fd
        finally:
            os.close(fd)

    def names(self, names: list[str], limit: int) -> list[str]:
        fd = _open(self.fd, "/".join(names) or ".", os.O_RDONLY | _flag("O_DIRECTORY"))
        try:
            with os.scandir(fd) as entries:
                out = []
                for entry in entries:
                    out.append(entry.name)
                    if len(out) >= limit:
                        break
                return out
        finally:
            os.close(fd)
