"""Workbench's process: the API, the page, and what they share."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from . import api, config, folders_api, tools_api, web
from ._build import commit
from .answers import Answers
from .hub import Hub
from .node_folders import NodeFolders
from .sessions import Sessions
from .settings import Settings
from .signin import Provider
from .store import Store
from .tools import Tools

log = logging.getLogger(__name__)

DATABASE = "workbench.sqlite3"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = Store(settings.data_dir / DATABASE)
        await store.open()
        interrupted = await store.mark_interrupted()
        if interrupted:
            log.info(
                "%d answer(s) were cut off by the last restart; marked interrupted", interrupted
            )
        # Eugene's sign-in is this install's own agent: never via a proxy.
        http = httpx.AsyncClient(timeout=15.0, trust_env=False)
        provider = Provider(
            issuer=settings.oidc_issuer,
            client_id=settings.oidc_client_id,
            secret_file=settings.oidc_secret_file,
            http=http,
        )
        hub = Hub(settings.gateway_url, settings.key_file)
        tools = Tools(store, settings)
        tools.node_folders = NodeFolders(store, provider, http)
        answers = Answers(store, hub, tools)
        app.state.tools = tools
        app.state.store = store
        app.state.provider = provider
        app.state.hub = hub
        app.state.answers = answers
        app.state.sessions = Sessions(store, provider)
        for problem in (provider.why_not(), hub.why_not()):
            if problem:
                log.warning("%s", problem)
        log.info(
            "Workbench %s on port %d; gateway %s; sign-in %s",
            commit() or "(development)",
            settings.bind_port,
            settings.gateway_url or "(none)",
            settings.oidc_issuer or "(none)",
        )
        try:
            yield
        finally:
            await answers.aclose()
            await hub.aclose()
            await http.aclose()
            await store.close()

    app = FastAPI(title="Workbench", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.add_middleware(web.SecurityHeaders)
    app.include_router(config.router)
    app.include_router(api.router)
    app.include_router(tools_api.router)
    app.include_router(folders_api.router)
    web.mount(app, web.static_dir(settings.static_dir))
    return app
