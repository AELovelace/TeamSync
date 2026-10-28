"""Runtime settings. Every variable is prefixed ``TEAMSYNC_`` and may come from
the environment or a ``.env`` file in the working directory."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TEAMSYNC_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./storage")
    bind_host: str = "127.0.0.1"
    port: int = 8000
    # Seconds between scheduled scans of ``watch_dir`` when it is set. 0 disables.
    sync_interval_seconds: int = 900
    # Optional directory the server itself scans (useful when Box Drive runs on the server,
    # or for example-data in development). The push script is the normal path.
    watch_dir: Path | None = None
    # Password for the first admin user, used only when the users table is empty.
    # Leave unset to have one generated and written to <data_dir>/initial-admin-password.txt.
    admin_password: str | None = None
    # Set true behind an HTTPS reverse proxy so the session cookie carries the Secure flag.
    cookie_secure: bool = False
    # Largest single upload accepted, in bytes. JE exports are a few KB.
    max_upload_bytes: int = 25 * 1024 * 1024
    log_level: str = "INFO"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "teamsync.sqlite3"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    return Settings()
