from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONType, TZDateTime, pk_column, utcnow
from app.models.enums import ListVerificationStatus


class ContactList(Base):
    __tablename__ = "contact_lists"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Denormalized, kept accurate by the import/verification services.
    contact_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    verification_status: Mapped[str] = mapped_column(
        String(30), default=ListVerificationStatus.PENDING, nullable=False
    )
    # JSONB: {"valid": n, "invalid": n, "unknown": n, "failed_domains": [...], ...}
    verification_summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # JSONB: CSV import report (imported / skipped-duplicate / skipped-suppressed /
    # skipped-free-provider / skipped-invalid / error_rows / progress).
    import_summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    # Records whether the consumer/free-provider filter was applied on import.
    free_provider_filter_applied: Mapped[bool] = mapped_column(default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    contacts: Mapped[list["Contact"]] = relationship(  # noqa: F821
        back_populates="contact_list",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
