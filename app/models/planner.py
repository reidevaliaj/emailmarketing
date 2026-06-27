"""4-Week Planner models.

* ``CampaignList``        — many-to-many: a campaign pools one or more lists.
* ``PlannerPlacement``    — a campaign placed on a month position (Week 1-4 x Mon-Fri).
* ``CampaignContactSend`` — per (campaign, contact) last-sent log for the
                            once-per-interval frequency rule.
* ``IPWarmState``         — per-IP warming start, so a newly added IP can be
                            ramped without resetting the others.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TZDateTime, pk_column, utcnow


class CampaignList(Base):
    """A list attached to a campaign. A campaign pools + dedupes across these."""

    __tablename__ = "campaign_lists"

    id: Mapped[int] = pk_column()
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    list_id: Mapped[int] = mapped_column(
        ForeignKey("contact_lists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("campaign_id", "list_id", name="uq_campaign_list"),
    )


class PlannerPlacement(Base):
    """A recurring campaign placed on a month position.

    ``week`` in 1..4 (1 = first occurrence of the weekday in the month).
    ``weekday`` in 0..4 (Mon..Fri). The block repeats every month.
    A campaign may occupy multiple cells; a cell may hold multiple campaigns.
    """

    __tablename__ = "planner_placements"

    id: Mapped[int] = pk_column()
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    week: Mapped[int] = mapped_column(Integer, nullable=False)      # 1..4
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)   # 0=Mon..4=Fri
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("campaign_id", "week", "weekday", name="uq_placement_cell"),
        Index("ix_placement_cell", "week", "weekday"),
    )


class CampaignContactSend(Base):
    """Last time a given contact received a given campaign.

    Enforces the once-per-interval frequency rule PER (campaign, contact). A
    contact can still receive other campaigns freely — that is the whole point.
    """

    __tablename__ = "campaign_contact_sends"

    id: Mapped[int] = pk_column()
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    last_sent_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("campaign_id", "contact_id", name="uq_contact_send"),
    )


class IPWarmState(Base):
    """Warming start for a sending IP. Total daily cap = sum over IPs of each
    IP's ramp value for its current age, so a new IP ramps independently."""

    __tablename__ = "ip_warm_states"

    id: Mapped[int] = pk_column()
    ip: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    pool: Mapped[str | None] = mapped_column(String(100), nullable=True)
    warmed_since: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
