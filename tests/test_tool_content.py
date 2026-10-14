"""Rich content RC3: a tool's pictures, sounds and files, kept as the answer's
own and shown after a refresh, through one path for every server
(workbench-rich-content-roadmap.md RC3)."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp.server import MCPServer
from mcp.types import (
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from eugene_plexus_workbench import content, files
from eugene_plexus_workbench.app import DATABASE
from eugene_plexus_workbench.content import Keeper, normalize
from eugene_plexus_workbench.store import Chat, FileRecord, Message, Store

from .conftest import ServerThread, World, png, wav
from .test_job_sites import _owner_reads, finish, offer, site_world  # noqa: F401
from .test_tools import decide, pending

PICTURE = png(3, 2)
SOUND = wav(0.1)
PAGE = b"<!doctype html><script>alert(1)</script>"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# --- the blocks ---------------------------------------------------------------------


def keep_in(tmp_path: Path, *, owner: str = "p-ada") -> tuple[Store, Keeper]:
    store = Store(tmp_path / "store.sqlite3")
    asyncio.run(store.open())
    return store, Keeper(store, tmp_path, owner, "c1", "m1")


def blocks_of(keeper: Keeper | None, blocks: Any, structured: Any = None) -> Any:
    return asyncio.run(normalize("call-1", blocks, structured, keeper))


def test_every_kind_of_block_is_kept_in_order_and_said_to_the_model(tmp_path: Path) -> None:
    store, keeper = keep_in(tmp_path)
    out, text = blocks_of(
        keeper,
        [
            {"type": "text", "text": "Here is the chart."},
            {"type": "image", "data": b64(PICTURE), "mimeType": "image/png"},
            {"type": "audio", "data": b64(SOUND), "mimeType": "audio/wav"},
            {"type": "resource_link", "uri": "https://example.com/r/report.pdf", "name": "report"},
            {"type": "resource", "resource": {"uri": "file:///notes.md", "text": "Notes."}},
            {
                "type": "resource",
                "resource": {"uri": "file:///out/data.bin", "blob": b64(b"\x00\x01")},
            },
            {"type": "hologram"},
        ],
        {"rows": 2},
    )
    assert [b["type"] for b in out] == [
        "text",
        "image",
        "audio",
        "link",
        "text",
        "file",
        "unsupported",
        "data",
    ]
    assert [b["id"] for b in out][:3] == ["call-1:0", "call-1:1", "call-1:2"]
    image, sound, link, embedded, file = out[1], out[2], out[3], out[4], out[5]
    assert image | {"fileId": "x"} == {
        "id": "call-1:1",
        "type": "image",
        "fileId": "x",
        "name": "picture.png",
        "mediaType": "image/png",
        "size": len(PICTURE),
        "width": 3,
        "height": 2,
    }
    assert sound["mediaType"] == "audio/wav"
    assert link == {
        "id": "call-1:3",
        "type": "link",
        "uri": "https://example.com/r/report.pdf",
        "name": "report",
    }
    assert embedded == {
        "id": "call-1:4",
        "type": "text",
        "text": "Notes.",
        "source": "file:///notes.md",
    }
    assert file["name"] == "data.bin" and file["mediaType"] == "application/octet-stream"
    assert out[-1] == {"id": "call-1:data", "type": "data", "data": {"rows": 2}}
    # The model reads the words and the data, and that the pictures are not its to see.
    assert text.splitlines() == [
        "Here is the chart.",
        '[The tool returned a picture, "picture.png" (image/png, '
        f"{len(PICTURE)} bytes). The person sees it; you do not.]",
        f'[The tool returned a sound, "audio.wav" (audio/wav, {files.describe_size(len(SOUND))}).'
        " The person sees it; you do not.]",
        "[The tool named report: https://example.com/r/report.pdf]",
        "Notes.",
        '[The tool returned a file, "data.bin" (application/octet-stream, 2 bytes). '
        "The person sees it; you do not.]",
        "[The tool returned a hologram result Workbench does not show.]",
        '{"rows": 2}',
    ]
    assert image["fileId"] not in text
    # Each is a file of the answer's chat, made by a tool, its bytes on disk.
    record = asyncio.run(store.file(image["fileId"]))
    assert record is not None
    assert (record.origin, record.message_id, record.chat_id) == ("tool", "m1", "c1")
    assert files.path_of(tmp_path, "p-ada", record.id).read_bytes() == PICTURE
    asyncio.run(store.close())


@pytest.mark.parametrize(
    ("data", "declared"),
    [(PAGE, "image/png"), (SVG, "image/svg+xml"), (b"GIF89a" + PICTURE, "image/gif")],
    ids=["a page that says it is a PNG", "an SVG", "a GIF"],
)
def test_a_picture_its_bytes_do_not_bear_out_is_a_file(
    tmp_path: Path, data: bytes, declared: str
) -> None:
    store, keeper = keep_in(tmp_path)
    (block,), _ = blocks_of(keeper, [{"type": "image", "data": b64(data), "mimeType": declared}])
    assert block["type"] == "file"
    assert block["mediaType"] == "application/octet-stream"
    assert block["declaredType"] == declared
    asyncio.run(store.close())


def test_what_cannot_be_kept_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, keeper = keep_in(tmp_path)
    monkeypatch.setitem(content.LIMITS, "image", 10)
    out, text = blocks_of(
        keeper,
        [
            {"type": "image", "data": "aGVsbG8=*", "mimeType": "image/png"},
            {"type": "image", "data": b64(PICTURE), "mimeType": "image/png"},
            {"type": "image", "mimeType": "image/png"},
        ],
    )
    assert [(b["type"], b["reason"]) for b in out] == [
        ("unsupported", "its bytes were not valid base64"),
        ("unsupported", "it is larger than 10 bytes"),
        ("unsupported", "it came without its bytes"),
    ]
    assert "that Workbench did not keep: it is larger than 10 bytes." in text
    (none,), _ = blocks_of(None, [{"type": "audio", "data": b64(SOUND), "mimeType": "audio/wav"}])
    assert none["reason"] == "this answer cannot keep files"
    asyncio.run(store.close())


def test_a_call_and_a_person_keep_only_so_much(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, keeper = keep_in(tmp_path)
    many = [{"type": "image", "data": b64(PICTURE), "mimeType": "image/png"}] * 10
    out, _ = blocks_of(keeper, many)
    assert sum(b["type"] == "image" for b in out) == content.MAX_FILES
    assert out[-1]["reason"] == f"one call may keep {content.MAX_FILES} files"
    monkeypatch.setattr(content, "PERSON_LIMIT", content.MAX_FILES * len(PICTURE))
    (full,), _ = blocks_of(
        Keeper(store, tmp_path, "p-ada", "c1", "m2"),
        [{"type": "image", "data": b64(PICTURE), "mimeType": "image/png"}],
    )
    assert full["reason"].startswith("your tool results already hold")
    # Another person's results are theirs to count.
    (theirs,), _ = blocks_of(
        Keeper(store, tmp_path, "p-bo", "c2", "m3"),
        [{"type": "image", "data": b64(PICTURE), "mimeType": "image/png"}],
    )
    assert theirs["type"] == "image"
    asyncio.run(store.close())


def test_structured_content_the_words_already_hold_is_said_once(tmp_path: Path) -> None:
    out, text = blocks_of(None, [{"type": "text", "text": '{"a": 1}'}], {"a": 1})
    assert text == '{"a": 1}'
    assert [b["type"] for b in out] == ["text", "data"]
    big = {"x": "y" * content.MAX_DATA}
    out, _ = blocks_of(None, [], big)
    assert out[-1]["type"] == "unsupported" and out[-1]["what"] == "structured data"


def test_a_result_of_many_blocks_keeps_so_many(tmp_path: Path) -> None:
    out, text = blocks_of(None, [{"type": "text", "text": "w"}] * (content.MAX_BLOCKS + 3))
    assert len(out) == content.MAX_BLOCKS + 1
    assert out[-1]["what"] == "3 more blocks"
    assert text.endswith("[The tool returned 3 more blocks that Workbench did not keep.]")


# --- an MCP server, through an answer ------------------------------------------------


@pytest.fixture
def mixed() -> Iterator[str]:
    """An MCP server whose `mixed` tool returns words, a picture, a sound, a
    link, an embedded file and structured content."""
    mcp = MCPServer("Mixed results", log_level="ERROR")

    @mcp.tool()
    async def mixed(text: str) -> CallToolResult:
        """Return everything at once."""
        return CallToolResult(
            content=[
                TextContent(type="text", text=f"Made for {text}."),
                ImageContent(type="image", data=b64(PICTURE), mimeType="image/png"),
                AudioContent(type="audio", data=b64(SOUND), mimeType="audio/wav"),
                ResourceLink(
                    type="resource_link", uri="https://example.com/never-fetched", name="elsewhere"
                ),
                EmbeddedResource(
                    type="resource",
                    resource=BlobResourceContents(
                        uri="file:///report.html", blob=b64(PAGE), mimeType="text/html"
                    ),
                ),
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(uri="file:///a.txt", text="Plain words."),
                ),
            ],
            structuredContent={"made": text},
        )

    server = ServerThread(mcp.streamable_http_app(json_response=True)).start()
    try:
        yield server.url + "/mcp"
    finally:
        server.stop()


def run_mixed(world: World, url: str) -> tuple[Any, Any, str, dict[str, Any]]:
    owner = world.browser()
    owner.sign_in("operator")
    added = owner.post("/api/tools/servers", json={"name": "Mixed", "url": url, "token": ""})
    assert added.status_code == 201, added.text
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    settings = {"settings": {"toolServers": [added.json()["id"]]}}
    assert ada.patch(f"/api/chats/{chat}", json=settings).status_code == 200
    world.gateway.mode = "tools"
    ada.post(f"/api/chats/{chat}/messages", json={"content": "Make me everything"})
    assert decide(ada, chat, pending(ada, chat), True).status_code == 204
    done = ada.wait_answer(chat)
    assert done["status"] == "done", done
    return owner, ada, chat, done["toolRounds"][0]["calls"][0]


def test_a_mixed_result_is_kept_shown_after_a_refresh_and_replayed_as_words(
    world: World, mixed: str
) -> None:
    owner, ada, chat, call = run_mixed(world, mixed)
    kinds = [b["type"] for b in call["content"]]
    assert kinds == ["text", "image", "audio", "link", "file", "text", "data"]
    assert call["contentVersion"] == 1
    # After a refresh: the same blocks, from the store.
    again = ada.get(f"/api/chats/{chat}").json()["messages"][-1]["toolRounds"][0]["calls"][0]
    assert again["content"] == call["content"]
    image, sound, page = call["content"][1], call["content"][2], call["content"][4]
    got = ada.get(f"/api/files/{image['fileId']}")
    assert got.status_code == 200 and got.content == PICTURE
    assert got.headers["cache-control"] == "private, no-store"
    assert got.headers["content-security-policy"] == "sandbox"
    assert ada.get(f"/api/files/{sound['fileId']}").content == SOUND
    # The page an embedded file holds is a download, never served as a page.
    served = ada.get(f"/api/files/{page['fileId']}")
    assert page["declaredType"] == "text/html"
    assert served.headers["content-type"] == "application/octet-stream"
    assert served.headers["content-disposition"].startswith("attachment")
    # Another person, and the install owner without reading on: nothing.
    bob = world.browser()
    bob.sign_in("p-bo")
    for other in (bob, owner):
        assert other.get(f"/api/files/{image['fileId']}").status_code == 404
    # The model is told what it cannot see, never where it is kept.
    replay = world.gateway.requests[-1]["messages"][-1]
    assert replay["role"] == "tool"
    assert "[The tool returned a picture" in replay["content"]
    assert "Made for hello." in replay["content"] and '{"made": "hello"}' in replay["content"]
    assert image["fileId"] not in json.dumps(world.gateway.requests)
    # Only what a person uploaded counts as an upload.
    assert ada.delete(f"/api/chats/{chat}/files/{image['fileId']}").status_code == 404
    listed = ada.get(f"/api/chats/{chat}/files").json()
    assert image["fileId"] not in json.dumps(listed), "a tool's picture is no document"


def test_deleting_the_chat_deletes_its_tools_files(
    world: World, mixed: str, tmp_path: Path
) -> None:
    _, ada, chat, call = run_mixed(world, mixed)
    record = asyncio.run(_record(world, call["content"][1]["fileId"]))
    path = files.path_of(world.settings.data_dir, record.owner, record.id)
    assert path.exists()
    assert ada.delete(f"/api/chats/{chat}").status_code == 204
    assert not path.exists()
    assert ada.get(f"/api/files/{record.id}").status_code == 404


async def _record(world: World, file_id: str) -> FileRecord:
    store = Store(world.settings.data_dir / DATABASE)
    await store.open()
    try:
        record = await store.file(file_id)
        assert record is not None
        return record
    finally:
        await store.close()


def test_a_tool_file_no_answer_names_is_removed_and_one_an_answer_names_stays(
    tmp_path: Path,
) -> None:
    store, keeper = keep_in(tmp_path)
    picture = [{"type": "image", "data": b64(PICTURE), "mimeType": "image/png"}]
    out, _ = blocks_of(keeper, picture)
    orphan = out[0]["fileId"]

    async def answer_names_one() -> str:
        # An answer saved with its call's blocks, as the checkpoint saves it.
        keeper = Keeper(store, tmp_path, "p-ada", "c1", "m9")
        named, _ = await normalize("call-2", picture, None, keeper)
        await store.create_chat(
            Chat(id="c1", owner="p-ada", title="t", model="m", created_at=1, updated_at=1)
        )
        rounds = [{"assistant": {}, "calls": [{"id": "call-2", "content": named}]}]
        message = Message(
            id="m9", chat_id="c1", seq=0, role="assistant", status="done", created_at=1
        )
        await store.add_message(message, parent_id=None)
        await store.update_message("m9", tool_rounds=rounds)
        return str(named[0]["fileId"])

    kept = asyncio.run(answer_names_one())
    assert asyncio.run(content.remove_orphans(store, tmp_path)) == 1
    assert not files.path_of(tmp_path, "p-ada", orphan).exists()
    assert asyncio.run(store.file(orphan)) is None
    assert files.path_of(tmp_path, "p-ada", kept).exists()
    assert asyncio.run(store.file(kept)) is not None
    asyncio.run(store.close())


# --- a job site ------------------------------------------------------------------------


def test_a_sites_picture_is_kept_and_hidden_from_the_install_owner_as_its_message_is(
    site_world: tuple[World, dict[str, Any]],  # noqa: F811 (the fixture, imported)
) -> None:
    world, state = site_world
    state["picture"] = PICTURE
    owner, ada = world.browser(), world.browser()
    owner.sign_in("operator")
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    chosen = ada.patch(
        f"/api/chats/{chat}", json={"settings": {"folderGrants": ["node:site-notes"]}}
    )
    assert chosen.status_code == 200, chosen.text
    world.gateway.mode = "tools"
    reading = offer(world, ada, chat, "read_text", folder="Notes", path="note.txt")
    call = finish(ada, chat, reading)
    image = next(b for b in call["content"] if b["type"] == "image")
    assert json.loads(call["result"].splitlines()[0])["text"] == "SITE-SECRET"
    assert ada.get(f"/api/files/{image['fileId']}").content == PICTURE
    _owner_reads(world)
    assert owner.get(f"/api/chats/{chat}").status_code == 200, "the owner reads the chat"
    assert owner.get(f"/api/files/{image['fileId']}").status_code == 404, "not its picture"


def test_an_upload_in_a_hidden_message_is_hidden_from_the_install_owner_too(
    site_world: tuple[World, dict[str, Any]],  # noqa: F811 (the fixture, imported)
) -> None:
    world, _ = site_world
    owner, ada = world.browser(), world.browser()
    owner.sign_in("operator")
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    early = ada.post(
        f"/api/chats/{chat}/files", files={"file": ("early.png", PICTURE, "image/png")}
    )
    assert early.status_code == 201, early.text
    ada.post(
        f"/api/chats/{chat}/messages",
        json={"content": "Before the site", "attachments": [early.json()["id"]]},
    )
    ada.wait_answer(chat)
    chosen = ada.patch(
        f"/api/chats/{chat}", json={"settings": {"folderGrants": ["node:site-notes"]}}
    )
    assert chosen.status_code == 200, chosen.text
    world.gateway.mode = "tools"
    finish(ada, chat, offer(world, ada, chat, "read_text", folder="Notes", path="note.txt"))
    world.gateway.mode = "text"
    late = ada.post(f"/api/chats/{chat}/files", files={"file": ("late.png", PICTURE, "image/png")})
    ada.post(
        f"/api/chats/{chat}/messages",
        json={"content": "After the site", "attachments": [late.json()["id"]]},
    )
    ada.wait_answer(chat)
    _owner_reads(world)
    # What came before the site's result stays readable; what follows it does not.
    assert owner.get(f"/api/files/{early.json()['id']}").status_code == 200
    assert owner.get(f"/api/files/{late.json()['id']}").status_code == 404
    assert ada.get(f"/api/files/{late.json()['id']}").status_code == 200


def test_the_words_kept_on_a_call_stop_where_the_models_do(tmp_path: Path) -> None:
    out, text = blocks_of(None, [{"type": "text", "text": "w" * (content.MAX_TEXT * 3)}])
    assert len(out[0]["text"]) == content.MAX_TEXT + 1  # and an ellipsis
    assert text.endswith(f"[Tool result truncated at {content.MAX_TEXT} characters.]")


def test_structured_content_that_is_not_json_is_not_kept() -> None:
    out, text = blocks_of(None, [], {"v": float("nan")})
    assert out == [
        {
            "id": "call-1:data",
            "type": "unsupported",
            "what": "structured data",
            "reason": "it is not JSON (it holds NaN, Infinity or what JSON cannot say)",
        }
    ]
    json.dumps(out, allow_nan=False)  # the chat still loads
    assert text == "[The tool returned structured data that is not JSON.]"


@pytest.mark.parametrize(
    ("uri", "data", "name"),
    [
        ("file:///out/chart.svg", PICTURE, "chart.png"),
        ("file:///out/..", b"\x00\x01", "file.bin"),
        ("file:///out/a%2F..%5Cb%07.txt", b"\x00\x01", "b.txt"),
    ],
    ids=["a picture named for its bytes", "no name", "no path or control character"],
)
def test_a_file_is_saved_under_a_name_that_is_only_a_name(
    tmp_path: Path, uri: str, data: bytes, name: str
) -> None:
    store, keeper = keep_in(tmp_path)
    (block,), _ = blocks_of(
        keeper, [{"type": "resource", "resource": {"uri": uri, "blob": b64(data)}}]
    )
    assert block["name"] == name
    asyncio.run(store.close())


def test_a_file_that_cannot_be_written_says_so_and_the_call_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, keeper = keep_in(tmp_path)

    def full(*_: Any, **__: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", full)
    out, _ = blocks_of(keeper, [{"type": "image", "data": b64(PICTURE), "mimeType": "image/png"}])
    assert out[0]["reason"] == "it could not be written (No space left on device)"
    assert keeper.kept == [] and not list(tmp_path.glob("files/*/*.part"))
    asyncio.run(store.close())


def test_a_file_a_stopped_call_kept_and_never_named_is_let_go_when_the_answer_ends(
    tmp_path: Path,
) -> None:
    store, keeper = keep_in(tmp_path)
    picture = [{"type": "image", "data": b64(PICTURE), "mimeType": "image/png"}]
    named, _ = blocks_of(keeper, picture)
    unnamed, _ = blocks_of(keeper, picture)
    rounds = [{"calls": [{"id": "call-1", "content": named}]}]
    assert asyncio.run(content.let_go([keeper], rounds)) == 1
    assert asyncio.run(store.file(named[0]["fileId"])) is not None
    assert asyncio.run(store.file(unnamed[0]["fileId"])) is None
    assert not files.path_of(tmp_path, "p-ada", unnamed[0]["fileId"]).exists()
    asyncio.run(store.close())
