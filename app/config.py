"""Application configuration.

All settings are read from environment variables / a `.env` file via
pydantic-settings. Nothing here is hardcoded with a real secret; the defaults
are safe placeholders so the app can boot in `POSTAL_USE_MOCK=true` mode for
development and tests.

Each field maps 1:1 to a variable documented in `.env.example`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App / runtime ----
    app_name: str = "Email Marketing"
    app_env: str = "production"
    app_base_url: str = "http://localhost:8000"
    app_debug: bool = False
    app_timezone: str = "UTC"

    session_secret: str = "dev-insecure-session-secret"
    jwt_secret: str = "dev-insecure-jwt-secret"
    unsubscribe_secret: str = "dev-insecure-unsubscribe-secret"

    # ---- Initial admin ----
    app_admin_email: str = "admin@example.com"
    app_admin_initial_password: str = "changeme"

    # ---- Database ----
    # Async URL used by FastAPI. The sync URL (Celery) is derived from it.
    postgres_url: str = "sqlite+aiosqlite:///./dev.sqlite3"

    # ---- Redis ----
    redis_url: str = "redis://localhost:6379/0"

    # ---- Postal ----
    postal_use_mock: bool = True
    postal_api_url: str = "https://postal.example.com"
    postal_api_key: str = "CHANGE_ME"
    postal_message_path: str = "/api/v1/send/message"
    postal_webhook_shared_secret: str = "CHANGE_ME"

    # ---- Sending identity ----
    sending_domain: str = "marketing.cod-st.com"
    default_from_name: str = "COD-ST"
    default_from_email: str = "news@marketing.cod-st.com"
    dkim_selector: str = "postal"
    dmarc_report_email: str = "dmarc@cod-st.com"

    # ---- Rate pacing & warming ----
    rate_global_per_minute: int = 20
    rate_per_domain_per_minute: int = 6
    per_ip_daily_cap: int = 2000
    send_max_retries: int = 5
    send_retry_backoff_seconds: int = 30

    # ---- Worker ----
    celery_worker_concurrency: int = 4

    # ---- Verification ----
    verify_dns_timeout_seconds: float = 3.0
    verify_dns_concurrency: int = 80
    verify_provider: str = "inhouse"

    # ---- Free-provider filter ----
    free_provider_filter_default: bool = True

    @field_validator("postgres_url")
    @classmethod
    def _normalize_async_url(cls, v: str) -> str:
        """Ensure the URL uses an async driver for the FastAPI side."""
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("sqlite://"):
            return v.replace("sqlite://", "sqlite+aiosqlite://", 1)
        return v

    @property
    def sync_database_url(self) -> str:
        """Synchronous SQLAlchemy URL used by Celery workers.

        Celery tasks use a plain (blocking) DB session — far simpler and more
        robust than driving asyncpg across per-task event loops. We derive the
        sync URL from the async one so there is a single source of truth.
        """
        url = self.postgres_url
        url = url.replace("+asyncpg", "+psycopg")
        url = url.replace("+aiosqlite", "")
        return url

    @property
    def is_sqlite(self) -> bool:
        return self.postgres_url.startswith("sqlite")

    @property
    def app_base_url_clean(self) -> str:
        return self.app_base_url.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()


settings = get_settings()
