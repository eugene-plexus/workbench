"""Workbench's settings: the config trio the registry expects of apps we ship.

`GET /v1/config`, `GET /v1/config/schema` and `PATCH /v1/config`, in
`common.yaml`'s shapes, which the console renders on Workbench's page and
the agent checks every answer against. The bearer is the admin token the
agent hands Workbench at each start, and nothing else: no person signed
in to Workbench can change these, the owner included -- they are changed
from Eugene's console, by its operator.

Four settings. `ownerReadsChats` (W13): whether the owner may read the chats
of the people they give Workbench to (Troy, C3 call 3: each business
decides). The other three are memory's (`memory.py`, MR1): whether people
may keep notes at all, how many each may keep, and whether notes may be sent
to a model that runs outside this install's machines. Memory is not covered
by `ownerReadsChats`: no setting here lets anyone read another person's
notes. Every one takes effect at once, and `null` puts one back to its
default.
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

#: How far a note may travel to a model outside the machines (`memory_api`'s
#: `resolve_external` says how the owner's and a person's choices meet).
EXTERNAL_CHOICES = ("never", "ask", "always")
MAX_NOTES_LIMIT = 2000

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
]

DEFAULTS: dict[str, Any] = {
    OWNER_READS_CHATS: False,
    MEMORY_ENABLED: True,
    MEMORY_MAX_NOTES: 300,
    MEMORY_EXTERNAL_MODELS: "ask",
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
        categories={"people": "People and their chats", "memory": "Memory"},
    )


@router.get("/v1/config", response_model=ConfigDocument)
async def get_config(request: Request) -> ConfigDocument:
    store: Store = request.app.state.store
    memory = await memory_settings(store)
    return ConfigDocument.model_validate(
        {
            OWNER_READS_CHATS: await owner_reads_chats(store),
            MEMORY_ENABLED: memory.enabled,
            MEMORY_MAX_NOTES: memory.max_notes,
            MEMORY_EXTERNAL_MODELS: memory.external,
        }
    )


def _problem_with(key: str, value: Any) -> str | None:
    """Why `value` is not one this setting takes, or None when it is. A
    `bool` is not an integer here, whatever Python says."""
    if key in (OWNER_READS_CHATS, MEMORY_ENABLED):
        return None if isinstance(value, bool) else "Must be true or false."
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
