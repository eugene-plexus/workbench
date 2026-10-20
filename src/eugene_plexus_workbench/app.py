"""Workbench's process: the API, the page, and what they share."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from . import (
    a2ui_state,
    agui_api,
    api,
    artifacts_api,
    config,
    connections_api,
    content,
    documents_api,
    folder_docs_api,
    folders_api,
    interactions_api,
    job_sites_api,
    media_api,
    memory_api,
    memory_models_api,
    saved_api,
    tools_api,
    web,
)
from ._build import commit
from ._http import ssl_context
from .a2ui import A2UI
from .agui import AGUI
from .answers import Answers
from .connections import Connections
from .hub import Hub
from .interaction_store import InteractionError
from .interactions import Interactions
from .knowledge.folder_sync import FolderSync
from .knowledge.index import IndexUnavailable
from .knowledge.reader import Reader
from .knowledge.saved import SavedDocuments
from .mcp_apps import McpApps
from .mcp_ui import PROTOCOL as MCP_APPS_PROTOCOL
from .mcp_ui import VERSION as MCP_APPS_VERSION
from .media import MediaJobs
from .memory_servers import router as memory_servers_router
from .node_folders import NodeFolders
from .past_chats import router as past_chats_router
from .projects_api import router as projects_router
from .sessions import Sessions
from .settings import Settings
from .signin import Provider
from .store import Store, public_cause
from .tools import Tools

log = logging.getLogger(__name__)

DATABASE = "workbench.sqlite3"


def why_the_store_did_not_open(store: Store, exc: Exception) -> tuple[str, str]:
    """What the log says of a store that did not open, and what anyone who asks is told.

    The log's is the whole account: the observed cause, every path, the copy. The other is for
    `/healthz`, the API's answers and the page, which a visitor who is not signed in can open
    (sign-in is down while the store is). It leads with the state and names the cause, and names
    no directory: a file is named by its own name alone.
    """
    # Workbench's own errors already say what is wrong and where; SQLite's say only what.
    cause = str(exc) if isinstance(exc, RuntimeError) else f"{type(exc).__name__}: {exc}"
    kept = (
        f" A copy of your chats from before the update is kept at {store.backup}."
        if store.backup is not None
        else ""
    )
    beside = (
        f" A copy is kept beside it as {store.backup.name}." if store.backup is not None else ""
    )
    return (
        f"Workbench could not open its saved chats ({store.path}), so it is running without "
        f"them: {cause}{kept}",
        f"Workbench could not open its chat store: {public_cause(exc)}.{beside}",
    )


class _Mcp:
    """`/mcp`, answered by the running `Connections`, or a 503 while there is none (starting,
    stopping, or a store that did not open). An object, so the route hands it the raw ASGI call."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        door = getattr(scope["app"].state, "connections", None)
        if door is None:
            response = JSONResponse(
                {"error": "Workbench is not ready for connections. Try again in a moment."},
                status_code=503,
            )
            await response(scope, receive, send)
            return
        await door(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = Store(settings.data_dir / DATABASE)
        try:
            await store.open()
        except Exception as exc:
            # Degraded, not dead. A store that cannot be opened (a migration that failed, a
            # newer Workbench's, one that is not a database) used to stop the whole process,
            # and the agent restarted it into the same stop. It stays up instead and says
            # why: in its log in full, and on /healthz and on every page (`web.Degraded`)
            # with no path in it, since anyone can ask; the saved chats are not touched,
            # and starting again retries.
            full, why = why_the_store_did_not_open(store, exc)
            log.error("%s", full)
            app.state.degraded = why
            try:
                yield
            finally:
                await store.close()
            return
        interrupted = await store.mark_interrupted()
        if interrupted:
            log.info(
                "%d answer(s) were cut off by the last restart; marked interrupted", interrupted
            )
        cut = await store.mark_media_interrupted()
        if cut:
            log.info(
                "%d media request(s) were cut off by the last restart; marked interrupted", cut
            )
        # A tool's files kept for an answer that stopped before it was saved
        # with them: no answer names them, so nothing could show or delete them.
        removed = await content.remove_orphans(store, settings.data_dir)
        if removed:
            log.info("%d tool result file(s) no answer names were removed", removed)
        # Eugene's sign-in is this install's own agent: never via a proxy.
        http = httpx.AsyncClient(timeout=15.0, trust_env=False, verify=ssl_context())
        provider = Provider(
            issuer=settings.oidc_issuer,
            client_id=settings.oidc_client_id,
            secret_file=settings.oidc_secret_file,
            http=http,
            backchannel=settings.oidc_backchannel,
        )
        sessions = Sessions(store, provider)
        hub = Hub(settings.gateway_url, settings.key_file, person_token=sessions.gateway_token)
        tools = Tools(store, settings)
        tools.node_folders = NodeFolders(store, provider, http)
        answers = Answers(store, hub, tools)
        media = MediaJobs(store, hub, settings.data_dir)
        reader = Reader(store, settings.data_dir)
        # A file whose reading a stop cut short is read again, not left
        # saying "Reading" for ever.
        reading = await reader.resume()
        if reading:
            log.info("reading %d file(s) again after the restart", reading)

        async def passage_limit() -> int:
            return (await config.document_settings(store)).max_passages

        async def document_settings() -> config.DocumentSettings:
            return await config.document_settings(store)

        # The people's saved documents (MR4): put right what the last stop left (a
        # chat's own documents whose chat is gone, files no row claims, a document cut
        # off while it was read), and begin reading what is waiting. With the gateway to
        # ask, also search by meaning (MR5): the passages that still have no vector are
        # owed, and are embedded in the background.
        saved = SavedDocuments(
            settings.data_dir, reader, passage_limit, gateway=hub, settings=document_settings
        )
        reconciled = await saved.start(store.chat_owners)

        async def folder_settings() -> config.FolderSettings:
            return await config.folder_settings(store)

        # The folders people pointed their Documents at (MR7): a folder whose grant was withdrawn
        # while Workbench was stopped has its documents removed, and each is checked in turn.
        folder_sync = FolderSync(store, saved, tools.folders, document_settings, folder_settings)
        await folder_sync.start()
        if any(
            (
                reconciled.chat_documents,
                reconciled.orphan_files,
                reconciled.requeued,
                reconciled.healed,
                reconciled.finished,
                reconciled.unreadable,
            )
        ):
            log.info(
                "saved documents after the restart: %d chat document(s) of deleted chats and %d "
                "file(s) no document claims removed; %d read again, %d finished; %d index(es) "
                "could not be checked",
                reconciled.chat_documents,
                reconciled.orphan_files,
                reconciled.requeued + reconciled.healed,
                reconciled.finished,
                reconciled.unreadable,
            )
        # M11: a video job the gateway accepted is polled again, whatever
        # stopped Workbench, because the provider kept working on it.
        resumed = await media.resume()
        if resumed:
            log.info("polling %d video job(s) again after the restart", resumed)
        app.state.tools = tools
        app.state.store = store
        app.state.provider = provider
        app.state.hub = hub
        app.state.answers = answers
        app.state.media = media
        app.state.reader = reader
        app.state.saved = saved
        app.state.folder_sync = folder_sync
        # A person's notes in their index (MV1): what a stop left out of step is put right in
        # the background, and every turn chooses its notes from it.
        from . import memory_api
        from .memory_relevance import Finder
        from .notes_index import NotesIndex

        notes_index = app.state.notes_index = NotesIndex(store, saved)
        notes_index.start()
        app.state.finder = Finder(store, saved)

        async def search_notes(
            person: Any, chat_id: str, model: str | None, arguments: dict[str, Any]
        ) -> str:
            return await memory_api.run_search_tool(app.state, person, chat_id, model, arguments)

        tools.memory_search = search_notes
        # Past chats (MV2): every person's exchanges in their index, backfilled in the
        # background, and `search_chats` for models that call tools.
        from . import past_chats
        from .exchanges_index import ExchangesIndex

        exchanges = app.state.exchanges_index = ExchangesIndex(store, saved)
        exchanges.start()

        async def search_chats(
            person: Any, chat_id: str, model: str | None, arguments: dict[str, Any]
        ) -> tuple[str, list[str]]:
            return await past_chats.run_tool(app.state, person, chat_id, model, arguments)

        tools.chats_search = search_chats
        # Keep automatically (MV3b): `remember`, judged and routed under the write policy.
        from . import memory_write

        async def remember(
            person: Any, chat_id: str, model: str | None, arguments: dict[str, Any], turn: Any
        ) -> tuple[str, Any]:
            text, note = await memory_write.run_remember(
                store, person, chat_id, model, arguments, turn or memory_write.TurnContext()
            )
            if note is not None:
                await memory_api._reindex(app.state, person.sub, [note.id])
            return text, note

        tools.remember = remember
        # Memory servers (MV5): a person's own, sent their answered exchanges in the
        # background and asked every turn.
        from .memory_servers import Courier, Servers

        app.state.memory_servers = Servers(store, settings)
        courier = app.state.memory_courier = Courier(store, app.state.memory_servers)
        courier.start()

        def finished(chat_id: str) -> None:
            exchanges.touch(None, chat_id)
            courier.finished(chat_id)

        answers.on_finished = finished
        # Learn from my chats (MR8): one timer per chat that has gone quiet.
        from .memory_learn import Learner

        learner = Learner(store, hub, busy=lambda chat_id: answers.running(chat_id) is not None)
        app.state.learner = learner

        def learned(chat_id: str, notes: list[Any]) -> None:
            """Notes the pass kept automatically: into the index, and a receipt for an open
            chat (MV3b)."""
            if notes:
                owner = notes[0].owner
                task = asyncio.create_task(
                    memory_api._reindex(app.state, owner, [n.id for n in notes])
                )
                background.add(task)
                task.add_done_callback(background.discard)
            answers.publish(
                chat_id,
                {
                    "type": "memory",
                    "learned": [{"id": n.id, "text": n.text, "model": n.model} for n in notes],
                },
            )

        background: set[asyncio.Task[None]] = set()
        learner.on_learned = learned
        app.state.sessions = sessions
        app.state.agui = AGUI(store, app.state.sessions, answers)
        await store.agui.recover()
        app.state.interactions = Interactions(store, app.state.sessions)
        tools.apps = McpApps(tools, app.state.interactions)
        app.state.interactions.adapters[(MCP_APPS_PROTOCOL, MCP_APPS_VERSION)] = tools.apps
        tools.a2ui = A2UI(tools, app.state.interactions, tools.apps)
        app.state.interactions.adapters[(a2ui_state.PROTOCOL, a2ui_state.VERSION)] = tools.a2ui
        await store.interactions.sweep(restart=True)
        # Connections (MR9b): other apps reach a person's own notes and documents at `/mcp`.
        mcp_door = app.state.connections = Connections(app.state)
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
            async with mcp_door.running():
                yield
        finally:
            await app.state.agui.aclose()
            await app.state.interactions.aclose()
            await learner.aclose()
            await courier.aclose()
            measurer = getattr(app.state, "measurer", None)
            if measurer is not None:
                await measurer.aclose()
            await notes_index.aclose()
            await exchanges.aclose()
            await answers.aclose()
            await folder_sync.aclose()
            await saved.aclose()
            await reader.aclose()
            await media.aclose()
            await hub.aclose()
            await http.aclose()
            await store.close()

    app = FastAPI(title="Workbench", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.degraded = None
    # Innermost: the headers and the canonical origin wrap what a degraded Workbench says.
    from .person_context import PersonContextMiddleware

    app.add_middleware(PersonContextMiddleware)
    app.add_middleware(web.Degraded)
    app.add_middleware(interactions_api.BodyLimit)
    app.add_middleware(web.SecurityHeaders)
    if settings.public_origin:
        from .public_origin import CanonicalOrigin

        app.add_middleware(CanonicalOrigin, origin=settings.public_origin)
    app.include_router(config.router)
    app.include_router(api.router)
    app.include_router(artifacts_api.router)
    app.include_router(interactions_api.router)
    app.include_router(agui_api.router)
    app.include_router(media_api.router)
    app.include_router(memory_api.router)
    app.include_router(memory_models_api.router)
    app.include_router(past_chats_router)
    app.include_router(projects_router)
    app.include_router(memory_servers_router)
    app.include_router(documents_api.router)
    # Before `saved_api`: `/api/documents/folders` is not a document's id.
    app.include_router(folder_docs_api.router)
    app.include_router(saved_api.router)
    app.include_router(tools_api.router)
    app.include_router(folders_api.router)
    app.include_router(job_sites_api.router)
    app.include_router(connections_api.router)
    # The MCP door (MR9b): its own authentication, never the session cookie.
    app.router.routes.append(Route("/mcp", endpoint=_Mcp(), methods=["GET", "POST", "DELETE"]))
    app.add_exception_handler(job_sites_api.HeldAtTheMachine, job_sites_api.held_response)
    app.add_exception_handler(IndexUnavailable, saved_api.index_unavailable)
    app.add_exception_handler(RequestValidationError, web.invalid_request)
    app.add_exception_handler(InteractionError, interactions_api.problem)
    web.mount(app, web.static_dir(settings.static_dir))
    return app
