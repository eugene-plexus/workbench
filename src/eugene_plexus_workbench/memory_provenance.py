"""Memory v2's MV6: the documents a note rests on, and "Still true?" when one changes.

A note kept from an answer names the documents the answer **cited** (`answers.mark_cited`) and
those it was sent whole; a person may link one of their documents to a note themselves. Each
link records the document as it was (`doc_id`, `sha256`, where it came from).

Whether a document changed is **checked when the Memory page is read**, not recorded when it
changes: the notes and the documents are two SQLite files, so no transaction spans both, and a
check on read cannot miss a change a crash would have lost. A link is

* current: the document is there, with the same `sha256`;
* changed: it is there with other content, or gone and another stands where it was (an edited
  folder file, an upload of the same name: `Index.at_origin`);
* removed: gone, and nothing stands where it was.

A note with a changed or removed link asks "Still true?" with the reason, at once (MV6-C3), and
is still sent to models until the person answers (MV6-C2).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .store import MAX_NOTE_DOCUMENTS, Message, Store

log = logging.getLogger(__name__)

#: At most this many documents a note.
MAX_LINKS = MAX_NOTE_DOCUMENTS
#: "Ask me next week".
SNOOZE_SECONDS = 7 * 86_400.0


def _place(source: dict[str, Any]) -> dict[str, Any]:
    """Where a cited passage is: its pages or lines, never its words. A document's words go
    when it is replaced or removed (`Index.add`); a note's link must not keep them."""
    return {
        key: source[key]
        for key in ("page_start", "page_end", "line_start", "line_end")
        if isinstance(source.get(key), int)
    }


def answer_documents(message: Message) -> dict[str, list[dict[str, Any]]]:
    """The documents an answer rests on, by doc id, each with the places it cited: every
    document a passage of which it cited, and every document it was sent whole. A passage it
    was sent and did not cite is not one: the answer did not rest on it."""
    found: dict[str, list[dict[str, Any]]] = {}
    for source in message.sources or []:
        if not isinstance(source, dict) or source.get("kind") != "document":
            continue
        doc_id = source.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id:
            continue
        if source.get("passage"):
            if source.get("cited") is True:
                found.setdefault(doc_id, []).append(_place(source))
        else:
            found.setdefault(doc_id, [])
    return found


def _now_of(doc: Any) -> dict[str, Any]:
    return {
        "doc_id": doc.id,
        "scope": doc.scope,
        "source": doc.source,
        "name": doc.name,
        "sha256": doc.sha256,
    }


def evidence(message: Message) -> list[dict[str, Any]]:
    """Locations and hashes of answer evidence. No words go in a link."""
    out: list[dict[str, Any]] = []
    for i, source in enumerate(message.sources or []):
        url = source.get("url")
        if source.get("kind") != "web" or not isinstance(url, str):
            continue
        if urlsplit(url).scheme not in ("http", "https"):
            continue
        text = source.get("text", source.get("snippet"))
        digest = source.get("sha256") or (
            hashlib.sha256(text.encode()).hexdigest() if isinstance(text, str) else ""
        )
        out.append(
            {
                "key": f"web:{i}",
                "selected": source.get("cited") is not False,
                "doc_id": hashlib.sha256(url.encode()).hexdigest(),
                "scope": "web",
                "source": url,
                "name": source.get("title") or url,
                "sha256": digest,
                "place": [],
            }
        )
    for turn in message.tool_rounds:
        for call in turn.get("calls", []):
            if call.get("status") != "done" or not isinstance(call.get("result"), str):
                continue
            origin = f"{call.get('serverId', '')}/{call.get('tool', '')}"
            out.append(
                {
                    "key": f"tool:{call['id']}",
                    "selected": False,
                    "doc_id": hashlib.sha256(origin.encode()).hexdigest(),
                    "scope": "tool",
                    "source": origin,
                    "name": f"{call.get('serverName', '')}: {call.get('tool', '')}",
                    "sha256": hashlib.sha256(call["result"].encode()).hexdigest(),
                    "place": [],
                }
            )
    return out


async def link_answer(
    state: Any, owner: str, note_id: str, message: Message, selected: list[str] | None = None
) -> int:
    """Name the documents `message`, an answer, rests on, on the person's note. How many
    were linked; 0 when there were none, or the person's index cannot be read (then the note
    is kept without them, and that is logged)."""
    wanted = answer_documents(message)
    external = [
        e
        for e in evidence(message)
        if (e["selected"] if selected is None else e["key"] in selected)
    ]
    store: Store = state.store
    if external:
        await store.link_note_documents(owner, note_id, external, "answer", MAX_LINKS)
    if not wanted:
        return len(external)
    saved = getattr(state, "saved", None)
    if saved is None:
        return 0
    links = []
    try:
        index = saved.index(owner, create=False)
        if index is None:
            return 0
        for doc_id, places in wanted.items():
            doc = await index.document(doc_id)
            if doc is None:
                continue  # gone since the answer: nothing to rest on
            links.append({**_now_of(doc), "place": places})
    except Exception as exc:
        log.warning("a note's documents were not linked: %s: %s", type(exc).__name__, exc)
        return 0
    if not links:
        return 0
    count = await store.link_note_documents(owner, note_id, links, "answer", MAX_LINKS)
    return max(count, 0)


@dataclass
class Change:
    """One link that is no longer current."""

    doc_id: str
    name: str
    #: `changed` or `removed`.
    change: str
    #: When the document as it is now was added (`changed`), else None.
    at: float | None
    #: The document as it is now (`changed`), else None: where Still true moves the link.
    now: dict[str, Any] | None
    snoozed: bool

    def view(self) -> dict[str, Any]:
        return {"docId": self.doc_id, "name": self.name, "change": self.change, "at": self.at}


async def check(
    state: Any, owner: str, links: dict[str, list[dict[str, Any]]]
) -> dict[str, list[Change]]:
    """Each note's links that are no longer current, by note id; a note whose links are all
    current is not listed. A person whose index cannot be read is asked nothing: a document
    is never called removed on a guess."""
    saved = getattr(state, "saved", None)
    if not links or saved is None:
        return {}
    found: dict[str, list[Change]] = {}
    now = time.time()
    seen: dict[str, Any] = {}
    there: dict[tuple[str, str, str], Any] = {}
    try:
        index = saved.index(owner, create=False)
        if index is None:
            # No index where one was: a store restored without its documents, not a person
            # who removed them all. Nothing is called removed on that.
            log.warning("a person's notes name documents but their index is not there")
            return {}
        for note_id, held in links.items():
            for link in held:
                if link["scope"] in ("web", "tool"):
                    continue
                doc_id = link["doc_id"]
                if doc_id not in seen:
                    seen[doc_id] = await index.document(doc_id)
                doc = seen[doc_id]
                if doc is not None and doc.sha256 == link["sha256"]:
                    continue
                if doc is None:
                    origin = (link["scope"], link["source"], link["name"])
                    if origin not in there:
                        there[origin] = await index.at_origin(*origin)
                    doc = there[origin]
                    if doc is not None and doc.sha256 == link["sha256"]:
                        continue  # the same words where they were: nothing changed
                # Put off only while the document stays as it was when put off.
                snoozed = bool(
                    link.get("snoozed_until") and link["snoozed_until"] > now
                ) and link.get("snoozed_sha") == ("" if doc is None else doc.sha256)
                found.setdefault(note_id, []).append(
                    Change(
                        doc_id=doc_id,
                        name=link["name"],
                        change="removed" if doc is None else "changed",
                        at=None if doc is None else doc.added_at,
                        now=None if doc is None else _now_of(doc),
                        snoozed=snoozed,
                    )
                )
    except Exception as exc:
        log.warning("notes' documents were not checked: %s: %s", type(exc).__name__, exc)
        return {}
    return found


async def due(state: Any, owner: str) -> dict[str, list[Change]]:
    """The notes to ask "Still true?" about now because of their documents: each with every
    link that changed, unless the person put all of them off ("Ask me next week")."""
    store: Store = state.store
    changes = await check(state, owner, await store.note_documents(owner, current=True))
    return {k: v for k, v in changes.items() if not all(c.snoozed for c in v)}


async def answer(state: Any, owner: str, note_id: str, said: str) -> bool:
    """The person's answer about a note asked about for its documents: `yes` moves each link
    to the document as it is now (or drops it, the document gone); `later` puts them off a
    week. False when the note has no such link (nothing was asked)."""
    store: Store = state.store
    links = await store.note_documents(owner, [note_id])
    changes = (await check(state, owner, links)).get(note_id, [])
    if not changes:
        return False
    if said == "yes":
        await store.settle_note_documents(owner, note_id, {c.doc_id: c.now for c in changes})
    elif said == "later":
        seen = {c.doc_id: c.now["sha256"] if c.now is not None else "" for c in changes}
        await store.snooze_note_documents(owner, note_id, seen, time.time() + SNOOZE_SECONDS)
    return True


async def settle_all(state: Any, owner: str, note_id: str) -> None:
    """A new version was saved: it rests on its documents as they are now (it answers the
    question). Never raises."""
    try:
        await answer(state, owner, note_id, "yes")
    except Exception as exc:
        log.warning("a new version's documents were not settled: %s: %s", type(exc).__name__, exc)


def link_view(link: dict[str, Any]) -> dict[str, Any]:
    return {
        "docId": link["doc_id"],
        "name": link["name"],
        "scope": link["scope"]
        if link["scope"] in ("web", "tool")
        else "chat"
        if str(link["scope"]).startswith("chat:")
        else "library",
        "source": link["source"] if link["scope"] in ("web", "tool") else None,
        "canCompare": bool(link["sha256"]),
        "how": link["how"],
        "place": link["place"],
        "linkedAt": link["linked_at"],
    }


async def check_web(
    state: Any, person: Any, link: dict[str, Any], server_id: str, tool_name: str
) -> dict[str, Any]:
    """Only the person's explicit action fetches a page, through their selected web tool."""
    async with state.tools.connect([server_id], person) as session:
        match = next(
            (
                (name, schema)
                for name, (server, _, tool, schema) in session.tools.items()
                if server["id"] == server_id and tool == tool_name
            ),
            None,
        )
        if match is None:
            raise ValueError("That web tool is not available to your account.")
        name, schema = match
        if "url" not in schema.get("properties", {}):
            raise ValueError("This tool has no URL input. Choose a web page tool.")
        call = session.prepare(
            {
                "id": "check-page",
                "function": {"name": name, "arguments": json.dumps({"url": link["source"]})},
            }
        )
        await session.execute(call)
        if call["status"] != "done":
            raise ValueError(str(call.get("result") or "The web tool returned no page."))
        digest = hashlib.sha256(call["result"].encode()).hexdigest()
    return {
        "changed": bool(link["sha256"]) and digest != link["sha256"],
        "canCompare": bool(link["sha256"]),
        "sha256": digest,
    }
