"""Application settings loaded from environment variables and .env files."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
WORKER_ENV_VARS = ("WEB_CONCURRENCY", "UVICORN_WORKERS")


class Settings(BaseSettings):
    """Runtime configuration.

    Values are read case-insensitively from environment variables (e.g.
    ``HOST`` -> ``host``). A ``.env`` file in the server directory or at the
    repository root is also honoured. Never commit real credentials.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AI Interior Layout Workbench"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    host: str = "127.0.0.1"
    port: int = 8000

    data_dir: Path = Path("./data")
    cors_origins: list[str] = Field(default_factory=lambda: list(DEFAULT_CORS_ORIGINS))

    # LLM / VLM credentials stay server-side only (PRD §8.3).
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    # Optional JSONL script for local demos/tests; bypasses the network model.
    llm_script_path: str = ""
    vlm_base_url: str = ""
    vlm_api_key: SecretStr = SecretStr("")
    vlm_model: str = ""
    vlm_enabled: bool = True
    vlm_timeout_seconds: float = 30.0
    vlm_max_rounds: int = 2
    # Optional JSON array of scripted VLM outputs (tests and offline demos).
    vlm_script_path: str = ""

    # M4 solver / capture behaviour.
    solver_process_pool: bool = True
    capture_timeout_seconds: float = 5.0
    screenshot_retention_days: int = 7

    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL for the SQLite scene store."""
        return f"sqlite+aiosqlite:///{(self.data_dir / 'app.db').as_posix()}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


def ensure_single_worker() -> None:
    """Refuse to boot with multiple workers.

    SQLite uses a single writer and the turn manager relies on per-scene
    in-process asyncio locks (PRD D12, §5.5), so more than one worker would
    silently break correctness. Development scripts and Docker pin one worker;
    this guard turns accidental multi-worker launches into a hard failure.
    """
    for env_name in WORKER_ENV_VARS:
        raw_value = os.environ.get(env_name)
        if raw_value is None or not raw_value.isdigit():
            continue
        if int(raw_value) > 1:
            raise RuntimeError(
                f"{env_name}={raw_value} is not supported: the server must run with "
                "a single worker (SQLite single-writer + in-process asyncio locks)."
            )
