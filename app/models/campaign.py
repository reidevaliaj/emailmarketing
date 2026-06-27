from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONType, TZDateTime, pk_column, utcnow
from app.models.enums import CampaignStatus, RecipientStatus


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    from_name: Mapped[str] = mapped_column(String(200), nullable=False)
    from_email: Mapped[str] = mapped_column(String(320), nullable=False)
    # Optional Reply-To — may be ANY address (e.g. info@cod-st.com). No SPF/DKIM
    # impact, so replies can route to the real business inbox while From stays on
    # the reputation-isolated sending domain.
    reply_to: Mapped[str | None] = mapped_column(String(320), nullable=True)

    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("templates.id", ondelete="RESTRICT"), nullable=True
    )
    # Legacy single list (kept for back-compat); the source of truth for a
    # campaign's lists is now the campaign_lists join (a campaign pools many).
    list_id: Mapped[int | None] = mapped_column(
        ForeignKey("contact_lists.id", ondelete="RESTRICT"), nullable=True
    )

    # --- Planner / recurring fields (4-Week Planner feature) ---
    # Local clock send time + timezone (e.g. "09:00", "Europe/Tirana"). When a
    # planner campaign fires on its day it starts at this local time.
    send_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    send_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # True for a recurring planner DEFINITION (parked, status=planner). Each
    # monthly firing clones it into a one-off run with parent_campaign_id set.
    is_planner: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    parent_campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Stored UTC; scheduling/display tz handled at the presentation layer.
    scheduled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=CampaignStatus.DRAFT, nullable=False, index=True
    )
    ip_pool: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Live counters, updated as send jobs complete and as webhooks arrive.
    total_recipients: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    queued_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delivered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bounced_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    recipients: Mapped[list["CampaignRecipient"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CampaignRecipient(Base):
    """Per-recipient ledger row — the unit that makes sending idempotent and
    reconcilable. One row per (campaign, contact), created at materialization."""

    __tablename__ = "campaign_recipients"

    id: Mapped[int] = pk_column()
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )

    # Snapshot of the email at materialization time, so later list edits never
    # corrupt an in-flight campaign.
    email_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)
    # Snapshot of merge fields so rendering is stable even if the contact changes.
    merge_snapshot: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default=RecipientStatus.PENDING, nullable=False, index=True
    )
    postal_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    ip_pool: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    campaign: Mapped["Campaign"] = relationship(back_populates="recipients")
    events: Mapped[list["EmailEvent"]] = relationship(
        back_populates="recipient",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # A contact appears at most once per campaign — prevents double
        # materialization / double sends at the database level.
        UniqueConstraint("campaign_id", "contact_id", name="uq_recipient_campaign_contact"),
        Index("ix_recipient_campaign_status", "campaign_id", "status"),
    )


class EmailEvent(Base):
    """Append-only log of webhook events from Postal."""

    __tablename__ = "email_events"

    id: Mapped[int] = pk_column()
    campaign_recipient_id: Mapped[int] = mapped_column(
        ForeignKey("campaign_recipients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Postal delivery status (e.g. Sent / SoftFail / HardFail / Bounced). Stored
    # so deferral (SoftFail) rates are queryable for warming/rate discovery.
    status: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    # Postal's own event id — used to dedupe redelivered webhooks (idempotency).
    provider_event_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    raw_payload: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    recipient: Mapped["CampaignRecipient"] = relationship(back_populates="events")
