"""Workbench's settings: the config trio the registry expects of apps we ship.

`GET /v1/config`, `GET /v1/config/schema` and `PATCH /v1/config`, in
`common.yaml`'s shapes, which the console renders on Workbench's page and
the agent checks every answer against. The bearer is the admin token the
agent hands Workbench at each start, and nothing else: no person signed
in to Workbench can change these, the owner included -- they are changed
from Eugene's console, by its operator.

One setting today (W13): `ownerReadsChats`, whether the owner may read the
chats of the people they give Workbench to (Troy, C3 call 3: each business
decides). It takes effect at once.
"""

from __future__ import annotations

import hmac
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

FIELDS = [
    ConfigField(
        key=OWNER_READS_CHATS,
        label="The owner may read people's chats",
        description=(
            "When on, the owner of this install can read, but not change, the chats of every "
            "person given Workbench, including chats written before it was turned on, and each "
            "person sees a line above their chats saying so. When off, Workbench's chat pages "
            "refuse reads of anyone else's chats. Local servers run with Workbench's file "
            "access, so use only programs trusted with everyone's data."
        ),
        category="people",
        valueType=ConfigValueType.boolean,
        default=False,
    )
]

DEFAULTS: dict[str, Any] = {OWNER_READS_CHATS: False}


async def owner_reads_chats(store: Store) -> bool:
    value = await store.setting(OWNER_READS_CHATS)
    return bool(value) if value is not None else bool(DEFAULTS[OWNER_READS_CHATS])


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
        component="workbench", fields=FIELDS, categories={"people": "People and their chats"}
    )


@router.get("/v1/config", response_model=ConfigDocument)
async def get_config(request: Request) -> ConfigDocument:
    store: Store = request.app.state.store
    return ConfigDocument.model_validate({OWNER_READS_CHATS: await owner_reads_chats(store)})


@router.patch("/v1/config", response_model=ConfigUpdateResult)
async def patch_config(request: Request, body: ConfigUpdateRequest) -> ConfigUpdateResult:
    store: Store = request.app.state.store
    applied: list[str] = []
    rejected: list[ConfigFieldError] = []
    for key, value in (body.model_extra or {}).items():
        if key != OWNER_READS_CHATS:
            rejected.append(ConfigFieldError(key=key, message="Workbench has no such setting."))
            continue
        if value is not None and not isinstance(value, bool):
            rejected.append(ConfigFieldError(key=key, message="Must be true or false."))
            continue
        # `null` is the trio's way of saying "back to the default".
        await store.put_setting(key, value)
        applied.append(key)
    return ConfigUpdateResult(applied=applied, rejected=rejected, requiresRestart=False)
