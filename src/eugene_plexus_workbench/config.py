"""Workbench's settings: the config trio the registry expects of apps we ship.

`GET /v1/config`, `GET /v1/config/schema` and `PATCH /v1/config`, in
`common.yaml`'s shapes, which the console renders on Workbench's page and
the agent checks every answer against. The bearer is the admin token the
agent hands Workbench at each start, and nothing else: no person signed
in to Workbench can change these, the owner included -- they are changed
from Eugene's console, by its operator.

Seven settings. `ownerReadsChats` (W13): whether the owner may read the chats
of the people they give Workbench to (Troy, C3 call 3: each business
decides). Three are memory's (`memory.py`, MR1): whether people
may keep notes at all, how many each may keep, and whether notes may be sent
to a model that runs outside this install's machines. Memory is not covered
by `ownerReadsChats`: no setting here lets anyone read another person's
notes. Three are documents' (`context.py`, MR2): whether Workbench reads the
files people attach, the most it takes of one, and how long a file may be and
still be read whole by default. Every one takes effect at once, and `null` puts
one back to its default.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ._generated.models import (
    ConfigDocument,
    ConfigField,
    ConfigFieldError,
    ConfigSchema,
    ConfigUpdateRequest,
    ConfigUpdateResult,
    ConfigValueType,
)
from .store import Store

OWNER_READS_CHATS = "ownerReadsChats"
MEMORY_ENABLED = "memoryEnabled"
MEMORY_MAX_NOTES = "memoryMaxNotes"
MEMORY_EXTERNAL_MODELS = "memoryExternalModels"
DOCUMENTS_ENABLED = "documentsEnabled"
DOCUMENTS_MAX_FILE_MB = "documentsMaxFileMB"
DOCUMENTS_WHOLE_UP_TO_TOKENS = "documentsWholeUpToTokens"

#: How far a note may travel to a model outside the machines (`memory_api`'s
#: `resolve_external` says how the owner's and a person's choices meet).
EXTERNAL_CHOICES = ("never", "ask", "always")
MAX_NOTES_LIMIT = 2000
MAX_FILE_MB_LIMIT = 500
MIN_WHOLE_TOKENS, MAX_WHOLE_TOKENS = 500, 200_000

FIELDS = [
    ConfigField(
        key=OWNER_READS_CHATS,
        label="The owner may read people's chats and media",
        description=(
            "When on, the owner of this install can read, but not change, the chats and media "
            "(images and other results in their bins) of every person given Workbench, "
            "including those made before it was turned on, and each person sees a line above "
            "their chats saying so. When off, Workbench refuses reads of anyone else's chats "
            "and media. Local servers run with Workbench's file access, so use only programs "
            "trusted with everyone's data. This never covers a person's memory notes: nobody "
            "can read another person's notes, whatever this is set to."
        ),
        category="people",
        valueType=ConfigValueType.boolean,
        default=False,
    ),
    ConfigField(
        key=MEMORY_ENABLED,
        label="People can keep notes for Workbench to remember",
        description=(
            "When on (the default), each person can keep short notes about themselves on the "
            'Memory page or by writing "remember that ..." in a chat, and a chat sends them '
            "to its model so it can use them. When off, Workbench keeps no new notes and "
            "sends none to any model; people can still read, export and delete the notes they "
            "already have. Each person can also turn memory off for themselves, and for one "
            "chat at a time."
        ),
        category="memory",
        valueType=ConfigValueType.boolean,
        default=True,
    ),
    ConfigField(
        key=MEMORY_MAX_NOTES,
        label="Most notes one person can keep",
        description=(
            "How many notes each person can keep (default 300). At the limit a person is told "
            "so and keeps no new note until they delete one. Lowering it deletes nothing. "
            "Each note is at most 240 characters, and a chat sends only as many as fit its "
            "model's window (8% of it, at most 1,000 tokens), so a long list costs a chat "
            "nothing it cannot afford."
        ),
        category="memory",
        valueType=ConfigValueType.integer,
        default=300,
        minimum=1,
        maximum=MAX_NOTES_LIMIT,
    ),
    ConfigField(
        key=MEMORY_EXTERNAL_MODELS,
        label="Send notes to models that run outside your machines",
        description=(
            "What a chat does with a person's notes when its model runs on a cloud account or "
            "anywhere Eugene cannot confirm is this install's own machines. Ask (the default): "
            "the notes are held back until the person chooses to send them for that chat. "
            "Always: they are sent without asking. Each person can choose Never, Ask or Always "
            "for themselves, and this is what a person who has not chosen gets. Never is "
            "different: it is this install's rule, notes are never sent to such a model, and "
            "no person can change that."
        ),
        category="memory",
        valueType=ConfigValueType.enum,
        enumValues=list(EXTERNAL_CHOICES),
        enumLabels=["Never", "Ask for each chat", "Always"],
        default="ask",
    ),
    ConfigField(
        key=DOCUMENTS_ENABLED,
        label="Workbench reads the files people attach",
        description=(
            "When on (the default), a PDF, Word, Markdown, text, CSV, HTML or code file "
            "attached to a chat is read by Workbench, in a process of its own, and its text "
            "goes to the chat's model, so any model can answer from it. When off, Workbench "
            "reads no file: only images and audio, and PDFs to models that take them as "
            "files, are attached, as before. Turning it off does not stop a file already read "
            "from being replayed in the chat it was sent in."
        ),
        category="documents",
        valueType=ConfigValueType.boolean,
        default=True,
    ),
    ConfigField(
        key=DOCUMENTS_MAX_FILE_MB,
        label="Largest file Workbench reads (MB)",
        description=(
            "The biggest file, in mebibytes, Workbench takes to read (default 50). A bigger "
            "one is refused when it is attached, naming this limit. A file is read in a child "
            "process that is ended after two minutes and held to two gibibytes of memory, so "
            "a large or broken file fails by itself and never stops Workbench."
        ),
        category="documents",
        valueType=ConfigValueType.integer,
        default=50,
        minimum=1,
        maximum=MAX_FILE_MB_LIMIT,
    ),
    ConfigField(
        key=DOCUMENTS_WHOLE_UP_TO_TOKENS,
        label="Longest file read whole without asking (tokens)",
        description=(
            "A file is read whole, with no question, up to this many tokens (default 6,000) "
            "or a quarter of the model's window, whichever is less. A longer one is read only "
            "when the person chooses to read it all, which Workbench allows up to 70% of what "
            "remains of the window, or the first part of it. Tokens are counted as characters "
            "divided by three, deliberately high."
        ),
        category="documents",
        valueType=ConfigValueType.integer,
        default=6000,
        minimum=MIN_WHOLE_TOKENS,
        maximum=MAX_WHOLE_TOKENS,
    ),
]

DEFAULTS: dict[str, Any] = {
    OWNER_READS_CHATS: False,
    MEMORY_ENABLED: True,
    MEMORY_MAX_NOTES: 300,
    MEMORY_EXTERNAL_MODELS: "ask",
    DOCUMENTS_ENABLED: True,
    DOCUMENTS_MAX_FILE_MB: 50,
    DOCUMENTS_WHOLE_UP_TO_TOKENS: 6000,
}


async def owner_reads_chats(store: Store) -> bool:
    value = await store.setting(OWNER_READS_CHATS)
    return bool(value) if value is not None else bool(DEFAULTS[OWNER_READS_CHATS])


@dataclass(frozen=True)
class MemorySettings:
    """The install's memory settings, as they are in effect now."""

    enabled: bool
    max_notes: int
    external: str


async def memory_settings(store: Store) -> MemorySettings:
    enabled = await store.setting(MEMORY_ENABLED)
    limit = await store.setting(MEMORY_MAX_NOTES)
    external = await store.setting(MEMORY_EXTERNAL_MODELS)
    return MemorySettings(
        enabled=bool(enabled) if enabled is not None else bool(DEFAULTS[MEMORY_ENABLED]),
        max_notes=int(limit) if limit is not None else int(DEFAULTS[MEMORY_MAX_NOTES]),
        external=(
            str(external) if external in EXTERNAL_CHOICES else str(DEFAULTS[MEMORY_EXTERNAL_MODELS])
        ),
    )


@dataclass(frozen=True)
class DocumentSettings:
    """The install's document settings, as they are in effect now."""

    enabled: bool
    max_file_mb: int
    whole_up_to_tokens: int

    @property
    def max_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024


async def document_settings(store: Store) -> DocumentSettings:
    enabled = await store.setting(DOCUMENTS_ENABLED)
    largest = await store.setting(DOCUMENTS_MAX_FILE_MB)
    whole = await store.setting(DOCUMENTS_WHOLE_UP_TO_TOKENS)
    return DocumentSettings(
        enabled=bool(enabled) if enabled is not None else bool(DEFAULTS[DOCUMENTS_ENABLED]),
        max_file_mb=int(largest) if largest is not None else int(DEFAULTS[DOCUMENTS_MAX_FILE_MB]),
        whole_up_to_tokens=(
            int(whole) if whole is not None else int(DEFAULTS[DOCUMENTS_WHOLE_UP_TO_TOKENS])
        ),
    )


def _admin(request: Request) -> None:
    expected = request.app.state.settings.admin_token
    given = request.headers.get("authorization", "")
    if not expected or not hmac.compare_digest(given, f"Bearer {expected}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Workbench's settings are changed from Eugene's console.",
        )


router = APIRouter(dependencies=[Depends(_admin)])


@router.get("/v1/config/schema", response_model=ConfigSchema, response_model_exclude_none=True)
async def get_schema() -> ConfigSchema:
    return ConfigSchema(
        component="workbench",
        fields=FIELDS,
        categories={
            "people": "People and their chats",
            "memory": "Memory",
            "documents": "Documents",
        },
    )


@router.get("/v1/config", response_model=ConfigDocument)
async def get_config(request: Request) -> ConfigDocument:
    store: Store = request.app.state.store
    memory = await memory_settings(store)
    documents = await document_settings(store)
    return ConfigDocument.model_validate(
        {
            OWNER_READS_CHATS: await owner_reads_chats(store),
            MEMORY_ENABLED: memory.enabled,
            MEMORY_MAX_NOTES: memory.max_notes,
            MEMORY_EXTERNAL_MODELS: memory.external,
            DOCUMENTS_ENABLED: documents.enabled,
            DOCUMENTS_MAX_FILE_MB: documents.max_file_mb,
            DOCUMENTS_WHOLE_UP_TO_TOKENS: documents.whole_up_to_tokens,
        }
    )


def _problem_with(key: str, value: Any) -> str | None:
    """Why `value` is not one this setting takes, or None when it is. A
    `bool` is not an integer here, whatever Python says."""
    if key in (OWNER_READS_CHATS, MEMORY_ENABLED, DOCUMENTS_ENABLED):
        return None if isinstance(value, bool) else "Must be true or false."
    if key == DOCUMENTS_MAX_FILE_MB:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 1 <= value <= MAX_FILE_MB_LIMIT
        ):
            return None
        return f"Must be a whole number from 1 to {MAX_FILE_MB_LIMIT}."
    if key == DOCUMENTS_WHOLE_UP_TO_TOKENS:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and MIN_WHOLE_TOKENS <= value <= MAX_WHOLE_TOKENS
        ):
            return None
        return f"Must be a whole number from {MIN_WHOLE_TOKENS:,} to {MAX_WHOLE_TOKENS:,}."
    if key == MEMORY_MAX_NOTES:
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_NOTES_LIMIT:
            return None
        return f"Must be a whole number from 1 to {MAX_NOTES_LIMIT:,}."
    if key == MEMORY_EXTERNAL_MODELS:
        return None if value in EXTERNAL_CHOICES else "Must be never, ask or always."
    return "Workbench has no such setting."


@router.patch("/v1/config", response_model=ConfigUpdateResult)
async def patch_config(request: Request, body: ConfigUpdateRequest) -> ConfigUpdateResult:
    store: Store = request.app.state.store
    applied: list[str] = []
    rejected: list[ConfigFieldError] = []
    for key, value in (body.model_extra or {}).items():
        if key not in DEFAULTS:
            rejected.append(ConfigFieldError(key=key, message="Workbench has no such setting."))
            continue
        # `null` is the trio's way of saying "back to the default".
        problem = _problem_with(key, value) if value is not None else None
        if problem is not None:
            rejected.append(ConfigFieldError(key=key, message=problem))
            continue
        await store.put_setting(key, value)
        applied.append(key)
    return ConfigUpdateResult(applied=applied, rejected=rejected, requiresRestart=False)
