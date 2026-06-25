from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TZDateTime, pk_column, utcnow


class Suppression(Base):
    """Global, permanent do-not-send list.

    THE single most important correctness rule in the app: no campaign may ever
    send to an email present here, regardless of list. Suppression is global by
    construction because there is only ever one contact row per email.

    Populated by: any bounce/complaint/failure webhook, failed pre-send
    verification, unsubscribe, and manual removal.
    """

    __tablename__ = "suppressions"

    id: Mapped[int] = pk_column()
    # Normalized lowercase email — UNIQUE so suppression is idempotent.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    # Optional free-text context (e.g. "campaign 42 hard bounce", SMTP detail).
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
