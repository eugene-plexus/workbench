"""Opt-in transcription; images are rendered in Reader's child, one page at a time."""

from __future__ import annotations

import asyncio
import json
import os
import re
import weakref
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .. import config
from ..hub import Hub
from ..person_context import acting_as
from ..store import Store
from .extract import Extracted, Part, Unreadable, clean, say
from .index import Document, Index, IndexUnavailable, split_source
from .reader import Extraction, Reader, failure, summarize
from .whole import Complete, answer_text

Render = Callable[[int], Awaitable[dict[str, Any]]]
PREFS = "page-reading.json"
_LOCKS: weakref.WeakKeyDictionary[Index, asyncio.Lock] = weakref.WeakKeyDictionary()


async def preferences(index: Index) -> dict[str, Any]:
    try:
        raw = await asyncio.to_thread((index.dir / PREFS).read_text, encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise IndexUnavailable(
            f"Page reading settings could not be read: {say(exc)}.", code="page_settings"
        ) from exc
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except ValueError as exc:
        raise IndexUnavailable(
            f"Page reading settings are not valid JSON: {say(exc)}.", code="page_settings"
        ) from exc


async def save_preference(index: Index, scope: str, value: dict[str, Any]) -> None:
    async with _LOCKS.setdefault(index, asyncio.Lock()):
        prefs = await preferences(index)
        prefs[scope] = value
        # Initialise the person's index and owner record, if this is their first choice.
        await index.meta("owner")

        def write() -> None:
            path = index.dir / PREFS
            temporary = path.with_suffix(".json.part")
            try:
                temporary.write_text(json.dumps(prefs), encoding="utf-8")
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

        try:
            await asyncio.to_thread(write)
        except OSError as exc:
            raise IndexUnavailable(
                f"Page reading settings were not saved: {say(exc)}.", code="page_settings"
            ) from exc


def scope(doc: Document) -> str:
    folder = split_source(doc.source)
    return f"folder:{folder[0]}" if folder else doc.scope


def picture_pages(doc: Document) -> set[int]:
    return {
        int(n)
        for note in doc.notes
        if note.startswith("Pages read from pictures:")
        for n in re.findall(r"\d+", note.partition(":")[2])
    }


async def enabled(index: Index, scope: str) -> bool:
    return (await preferences(index)).get(scope, {}).get("enabled") is True


async def ingest(
    sub: str,
    index: Index,
    doc: Document,
    result: Extraction,
    *,
    store: Store,
    hub: Hub,
    reader: Reader,
) -> Extraction:
    if doc.kind not in ("pdf", "image"):
        return result
    if result.failure and result.failure.get("code") not in ("no_text", "scanned", "unsupported"):
        return result
    if result.extracted is not None:
        text = result.extracted
        if text.truncated or (
            text.pages and len(text.parts) == text.pages and all(p.text.strip() for p in text.parts)
        ):
            return result
    prefs = await preferences(index)
    choice = prefs.get(f"document:{doc.id}", prefs.get(scope(doc), {}))
    if not choice.get("enabled"):
        return result
    install = await config.document_settings(store)
    if not install.enabled:
        return result
    model = install.page_reader
    if not model:
        return Extraction(
            failure=failure("skipped", "page_reader_off", "Off: scanned pages are skipped.")
        )
    try:
        with acting_as(sub):
            from ..memory_api import model_facts, resolve_external

            listing = await hub.models()
            if not any(m.get("id") == model for m in listing.get("data", [])):
                raise Unreadable(
                    "reader_unavailable", f"Pages not read: {model} is not served now."
                )
            facts = await model_facts(hub, model)
            outside, _ = resolve_external(
                install.external, (await store.documents_prefs(sub)).get("external")
            )
            if facts.locality != "local" and not (
                outside == "always" or (outside == "ask" and choice.get("outside") is True)
            ):
                raise Unreadable(
                    "pages_outside",
                    "Pages not read: the documents' outside-model rule "
                    "does not allow sending these page images.",
                    state="skipped",
                )

            async def check() -> None:
                current = await index.document(doc.id)
                if current is None or current.attempt != doc.attempt:
                    raise Unreadable(
                        "pages_stopped", "Pages not read: the document was removed or read again."
                    )
                current_prefs = await preferences(index)
                current_choice = current_prefs.get(
                    f"document:{doc.id}", current_prefs.get(scope(doc), {})
                )
                current_install = await config.document_settings(store)
                if not current_choice.get("enabled") or not current_install.enabled:
                    raise Unreadable(
                        "pages_stopped", "Pages not read: reading scanned pages was switched off."
                    )
                if current_install.page_reader != model:
                    raise Unreadable(
                        "pages_stopped",
                        "Pages not read: the page reader model changed. Read this document again.",
                    )
                current_outside, _ = resolve_external(
                    current_install.external, (await store.documents_prefs(sub)).get("external")
                )
                if facts.locality != "local" and not (
                    current_outside == "always"
                    or (current_outside == "ask" and current_choice.get("outside") is True)
                ):
                    raise Unreadable(
                        "pages_outside",
                        "Pages not read: the documents' outside-model rule changed.",
                    )

            extracted = await read_pages(
                index.original_path(doc.id),
                result.extracted,
                enabled=True,
                model=model,
                maximum=install.page_reader_max_pages,
                complete=hub.complete,
                render=lambda n: reader.page(index.original_path(doc.id), n),
                check=check,
            )
            if extracted is None:
                return result
            if doc.kind == "image":
                extracted.kind = "image"
            return Extraction(extracted=extracted, summary=summarize(extracted))
    except Unreadable as exc:
        return Extraction(failure=failure(exc.state, exc.code, exc.reason, pages=exc.pages))
    except Exception as exc:
        return Extraction(
            failure=failure(
                "failed",
                "pages_failed",
                f"Pages not read: {say(getattr(exc, 'message', str(exc)))}.",
            )
        )


async def read_pages(
    source: Path,
    extracted: Extracted | None,
    *,
    enabled: bool,
    model: str | None,
    maximum: int,
    complete: Complete,
    render: Render,
    check: Callable[[], Awaitable[None]] | None = None,
) -> Extracted | None:
    if not enabled or not model:
        return extracted
    if (
        extracted is not None
        and extracted.pages
        and all(p.text.strip() for p in extracted.parts)
        and len(extracted.parts) == extracted.pages
    ):
        return extracted
    first = await render(1)
    count = int(first["pageCount"])
    missing = [
        n
        for n in range(1, count + 1)
        if extracted is None or not any(p.page == n and p.text.strip() for p in extracted.parts)
    ]
    if len(missing) > maximum:
        raise Unreadable(
            "page_limit",
            f"{len(missing)} pages to read with {model}; Workbench reads at most {maximum}.",
            state="skipped",
            pages=count,
        )
    parts = {p.page: p for p in extracted.parts} if extracted else {}
    failures: list[str] = []
    read: list[int] = []
    models_read: list[str] = []
    for number in missing:
        if check is not None:
            await check()
        try:
            page = first if number == 1 else await render(number)
            response = await complete(
                {
                    "model": model,
                    "stream": False,
                    "max_tokens": 4096,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Write out all the text on this page, "
                                        "in reading order. "
                                        "Keep headings and tables as Markdown."
                                    ),
                                },
                                {"type": "image_url", "image_url": {"url": page["image"]}},
                            ],
                        }
                    ],
                }
            )
            text = answer_text(response)
            text, _ = clean(text, layout=True)
            parts[number] = Part(text, number)
            read.append(number)
            actual = response.get("model")
            actual_model = actual.strip() if isinstance(actual, str) and actual.strip() else model
            if actual_model not in models_read:
                models_read.append(actual_model)
        except Exception as exc:
            failures.append(
                f"Page {number} was not read: {say(getattr(exc, 'message', str(exc)))}."
            )
    if not any(p.text.strip() for p in parts.values()):
        raise Unreadable(
            "pages_failed", f"Pages not read: {model}. " + " ".join(failures), pages=count
        )
    return Extracted(
        kind="pdf",
        parts=[parts.get(n, Part("", n)) for n in range(1, count + 1)],
        pages=count,
        notes=[
            *(extracted.notes if extracted else []),
            *(
                [f"{len(failures)} page{'s' if len(failures) != 1 else ''} not read."]
                if failures
                else []
            ),
            *failures,
            "Pages read from pictures: " + ", ".join(str(n) for n in read) + ".",
        ],
        read_by=", ".join(models_read) or None,
    )
