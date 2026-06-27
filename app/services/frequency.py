"""Per-(contact, campaign) frequency rule (Section 3 of the planner spec).

A contact receives a given CAMPAIGN at most once per interval (default 30 days),
but may receive DIFFERENT campaigns freely. The rule is keyed on the recurring
campaign id (the planner definition / a run's parent), so a dentist can get the
website offer, the GEO offer and the AI-receptionist offer in the same month —
they are different campaigns.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base import utcnow
from app.models.planner import CampaignContactSend


def ineligible_contact_ids(
    session: Session, frequency_campaign_id: int, interval_days: int
) -> set[int]:
    """Contacts that received this campaign within ``interval_days`` (skip them)."""
    cutoff = utcnow() - timedelta(days=interval_days)
    return set(
        session.scalars(
            select(CampaignContactSend.contact_id).where(
                CampaignContactSend.campaign_id == frequency_campaign_id,
                CampaignContactSend.last_sent_at >= cutoff,
            )
        ).all()
    )


def record_send(
    session: Session,
    frequency_campaign_id: int,
    contact_id: int,
    when: datetime | None = None,
) -> None:
    """Upsert the last-sent timestamp for (campaign, contact)."""
    when = when or utcnow()
    existing = session.scalar(
        select(CampaignContactSend).where(
            CampaignContactSend.campaign_id == frequency_campaign_id,
            CampaignContactSend.contact_id == contact_id,
        )
    )
    if existing is not None:
        existing.last_sent_at = when
    else:
        session.add(
            CampaignContactSend(
                campaign_id=frequency_campaign_id,
                contact_id=contact_id,
                last_sent_at=when,
            )
        )
