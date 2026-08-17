"""Application settings, loaded once from the environment / `.env`.

Everything downstream (engine, redis, bots) reads its configuration from the
single :data:`settings` instance created at import time.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application configuration.

    Values are sourced (in priority order) from real environment variables and
    then from a local ``.env`` file. Secrets use :class:`~pydantic.SecretStr`
    so they are never accidentally logged.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Controller bot -----------------------------------------------------
    main_bot_token: SecretStr = Field(..., description="Token of the Main/Controller bot.")

    # --- Public webhook -----------------------------------------------------
    webhook_base_url: str = Field(..., description="Public HTTPS origin Telegram calls back on.")
    webhook_secret: SecretStr = Field(..., description="Secret verified on every incoming update.")

    # --- Datastores ---------------------------------------------------------
    database_url: str = Field(..., description="SQLAlchemy async URL (asyncpg).")
    redis_url: str = Field("redis://localhost:6379/0", description="Redis connection URL.")
    config_cache_ttl: int = Field(3600, ge=1, description="Bot-config cache TTL in seconds.")

    # --- Runtime ------------------------------------------------------------
    log_level: str = Field("INFO", description="Root log level.")
    api_host: str = Field("0.0.0.0", description="Bind host for the FastAPI app.")
    api_port: int = Field(8000, ge=1, le=65535, description="Bind port for the FastAPI app.")

    def webhook_url_for(self, bot_token: str) -> str:
        """Return the full public webhook URL for a given bot token."""
        return f"{self.webhook_base_url.rstrip('/')}/webhook/{bot_token}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` singleton.

    Wrapped in ``lru_cache`` so the ``.env`` file is parsed exactly once and so
    tests can override via ``get_settings.cache_clear()``.
    """
    return Settings()  # type: ignore[call-arg]  # values come from env/.env


settings = get_settings()
