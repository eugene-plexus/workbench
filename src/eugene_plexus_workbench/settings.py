"""What the agent hands Workbench at each start, and nothing else.

The registry's whole contract with an app is a short list of
`EUGENE_PLEXUS_APP_*` variables (`agent.yaml`, `AppManifest`): a port, a
data directory, a key file, the gateway's address, the admin token for the
config trio and, for an app that signs people in, the issuer, a client id
and a secret file. Everything here is one of those. The agent strips every
other `EUGENE_PLEXUS_*` variable before it starts an app, so there is
nothing else to read.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EUGENE_PLEXUS_APP_", extra="ignore")

    id: str = "workbench"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8190
    data_dir: Path = Path("data")
    key_file: Path | None = None
    admin_token: str | None = None
    gateway_url: str | None = None
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_secret_file: Path | None = None
    #: Set only by the app-account launcher, never a user-facing setting.
    account_kind: str | None = None
    #: A developer's own build of the front end; the package's otherwise.
    static_dir: Path | None = None
