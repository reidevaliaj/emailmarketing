from __future__ import annotations

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONType, TZDateTime, pk_column, utcnow


class AppSetting(Base):
    """Simple key/value store for runtime-editable configuration.

    Used for the free-provider domain blocklist and its on/off toggle so they
    can be edited from the UI without a redeploy (Section 8).
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(JSONType, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class ApiKey(Base):
    """API key for the authenticated JSON status endpoint (Section 8).

    Only the hash is stored; the plaintext is shown once at creation time.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
