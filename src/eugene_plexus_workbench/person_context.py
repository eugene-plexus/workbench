"""Request-local identity, explicitly replaced when background work changes owner."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send

current: ContextVar[tuple[str, str | None] | None] = ContextVar("workbench_person", default=None)
catalog_only: ContextVar[bool] = ContextVar("app_catalog_only", default=False)


@contextmanager
def acting_as(sub: str, session_id: str | None = None) -> Iterator[None]:
    previous = current.set((sub, session_id))
    try:
        yield
    finally:
        current.reset(previous)


class PersonContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        previous = current.set(None)
        try:
            await self.app(scope, receive, send)
        finally:
            current.reset(previous)
