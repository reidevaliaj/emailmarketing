from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONType, TZDateTime, pk_column, utcnow
from app.models.enums import ContactStatus, VerificationResult


class Contact(Base):
    """A single recipient.

    HARD RULE (Section 5): one email exists at most ONCE in the entire system.
    Enforced by a UNIQUE constraint on the normalized (lowercased, trimmed)
    email. An email may not appear in two lists, so a contact carries a single
    ``list_id`` FK rather than a many-to-many membership table (see DECISIONS).
    """

    __tablename__ = "contacts"

    id: Mapped[int] = pk_column()

    # Normalized lowercase email — GLOBALLY UNIQUE across the whole database.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)

    first_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    custom_fields: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default=ContactStatus.ACTIVE, nullable=False, index=True
    )
    verification_result: Mapped[str] = mapped_column(
        String(20), default=VerificationResult.PENDING, nullable=False
    )

    # A contact belongs to exactly one list for organizational purposes.
    list_id: Mapped[int | None] = mapped_column(
        ForeignKey("contact_lists.id", ondelete="CASCADE"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    contact_list: Mapped["ContactList | None"] = relationship(  # noqa: F821
        back_populates="contacts"
    )

    __table_args__ = (
        Index("ix_contacts_list_status", "list_id", "status"),
    )
