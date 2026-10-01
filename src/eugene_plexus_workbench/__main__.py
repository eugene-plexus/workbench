"""`python -m eugene_plexus_workbench`: how the registry starts Workbench.

It binds the port in `EUGENE_PLEXUS_APP_BIND_PORT`, on
`EUGENE_PLEXUS_APP_BIND_HOST` when set and loopback otherwise, and logs to
its standard output, which the registry's launcher forwards to the agent's
log ingress under the app's key name (C1).
"""

from __future__ import annotations

import logging
import sys

import uvicorn

from .app import create_app
from .settings import Settings


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # One line per request to the gateway is noise on the Logs page, and
    # every tab polls the model list.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
