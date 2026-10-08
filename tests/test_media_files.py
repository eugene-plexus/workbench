"""What came back is read from the bytes (workbench-media-screens.md §2.4),
and a full disk names itself (M4)."""

from __future__ import annotations

import errno
import pathlib
import struct
import time
from typing import Any

import pytest

from eugene_plexus_workbench import files

from .conftest import World, png


def _jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def _webp_vp8x(width: int, height: int) -> bytes:
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00"
    body += (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def _webp_vp8l(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    body = b"VP8L" + struct.pack("<I", 5) + b"\x2f" + struct.pack("<I", bits)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


@pytest.mark.parametrize(
    ("data", "kind", "size"),
    [
        (png(1024, 768), "image/png", (1024, 768)),
        (_jpeg(1536, 1024), "image/jpeg", (1536, 1024)),
        (_webp_vp8x(1280, 720), "image/webp", (1280, 720)),
        (_webp_vp8l(333, 333), "image/webp", (333, 333)),
    ],
)
def test_an_images_own_size_is_read_from_its_bytes(
    data: bytes, kind: str, size: tuple[int, int]
) -> None:
    assert files.sniff_image(data) == kind
    assert files.image_size(data) == size


def test_bytes_that_are_no_image_have_no_size() -> None:
    assert files.sniff_image(b"%PDF-1.7") is None
    assert files.image_size(b"\xff\xd8\xff") is None
    assert files.image_size(b"") is None


def test_a_full_disk_fails_the_request_and_names_the_disk(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = pathlib.Path.write_bytes

    def full(self: pathlib.Path, data: Any) -> int:
        if "files" in self.parts:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, data)

    monkeypatch.setattr(pathlib.Path, "write_bytes", full)
    ada = world.browser()
    ada.sign_in("p-ada")
    made = ada.post("/api/media/images", json={"model": "openrouter/flux", "prompt": "x"}).json()
    deadline = time.perf_counter() + 10
    item = made
    while item["status"] == "running" and time.perf_counter() < deadline:
        time.sleep(0.05)
        item = ada.get(f"/api/media/{made['id']}").json()
    assert item["status"] == "failed", item
    message = item["error"]["message"]
    assert "is full" in message and "MiB free" in message and str(world.data) in message
