"""Atomic campaign counter updates.

Shared by the send pipeline and webhook reconciliation. Uses a single UPDATE so
concurrent workers never clobber each other's increments (no read-modify-write).
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.campaign import Campaign


def bump_campaign(session: Session, campaign_id: int, **deltas: int) -> None:
    if not deltas:
        return
    values = {k: getattr(Campaign, k) + v for k, v in deltas.items()}
    session.execute(update(Campaign).where(Campaign.id == campaign_id).values(**values))
