"""Role choices in Workbench's install settings, rendered by the console."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from starlette.requests import Request

from eugene_plexus_workbench import config
from eugene_plexus_workbench._generated.models import ConfigFieldStatus


async def test_the_embedder_picker_lists_the_installs_embeddings_role_first() -> None:
    app = FastAPI()
    meaning = SimpleNamespace(
        setting_view=AsyncMock(
            return_value=(
                ["mine", "role:embeddings", "other"],
                ConfigFieldStatus(level="info", text="In use: mine."),
            )
        )
    )
    app.state.saved = SimpleNamespace(meaning=meaning)
    schema = await config.get_schema(Request({"type": "http", "app": app}))
    field = next(f for f in schema.fields if f.key == config.DOCUMENTS_EMBEDDING_MODEL)
    assert field.suggestions == ["role:embeddings", "auto", "mine", "other"]
    assert field.default == "auto", "offering a role does not change the default"
    assert field.status is not None and field.status.text == "In use: mine."


async def test_role_choices_are_offered_even_when_the_gateway_is_unavailable() -> None:
    app = FastAPI()
    schema = await config.get_schema(Request({"type": "http", "app": app}))
    fields = {f.key: f for f in schema.fields}
    assert fields[config.DOCUMENTS_EMBEDDING_MODEL].suggestions == ["role:embeddings", "auto"]
    assert fields[config.DOCUMENTS_SORT_MODEL].suggestions == ["role:rerank", "auto", "off"]
