"""Scheduler tasks driven by Celery Beat.

* ``dispatch_due_campaigns`` (every minute, Section 6.1) — find SCHEDULED
  campaigns whose ``scheduled_at <= now``, flip them to SENDING, and kick off
  materialization.
* ``finalize_and_repump`` (every couple of minutes) — mark SENDING campaigns
  with no PENDING recipients as COMPLETED, and conservatively re-enqueue work for
  any campaign that has genuinely stalled (e.g. after a worker crash). Normal
  paced sending never triggers a re-pump; the guard prevents enqueue storms.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.celery_app import celery_app
from app.db import sync_session
from app.logging import get_logger
from app.models.base import utcnow
from app.models.campaign import Campaign, CampaignRecipient
from app.models.enums import CampaignStatus, RecipientStatus
from app.services.rate_limit import get_redis
from app.tasks.sending import (
    _pending_count,
    enqueue_pending_for_campaign,
    materialize_campaign,
)

logger = get_logger(__name__)

_STALL_AFTER = timedelta(minutes=15)


@celery_app.task(name="app.tasks.scheduler.dispatch_due_campaigns")
def dispatch_due_campaigns() -> dict:
    now = utcnow()
    with sync_session() as session:
        due = session.scalars(
            select(Campaign).where(
                Campaign.status == CampaignStatus.SCHEDULED.value,
                Campaign.scheduled_at.is_not(None),
                Campaign.scheduled_at <= now,
            )
        ).all()
        ids = [c.id for c in due]
        for c in due:
            c.status = CampaignStatus.SENDING.value

    for cid in ids:
        materialize_campaign.delay(cid)
    if ids:
        logger.info("dispatched due campaigns: %s", ids)
    return {"dispatched": ids}


@celery_app.task(name="app.tasks.scheduler.finalize_and_repump")
def finalize_and_repump() -> dict:
    now = utcnow()
    with sync_session() as session:
        sending_ids = session.scalars(
            select(Campaign.id).where(Campaign.status == CampaignStatus.SENDING.value)
        ).all()

    finalized: list[int] = []
    repumped: list[int] = []
    redis = get_redis()
    for cid in sending_ids:
        with sync_session() as session:
            pending = _pending_count(session, cid)
            if pending == 0:
                c = session.get(Campaign, cid)
                if c and c.status == CampaignStatus.SENDING.value:
                    c.status = CampaignStatus.COMPLETED.value
                    finalized.append(cid)
                continue
            last_activity = session.scalar(
                select(func.max(CampaignRecipient.updated_at)).where(
                    CampaignRecipient.campaign_id == cid
                )
            )

        # Stalled? No recipient activity for a while but work remains.
        stalled = last_activity is None or last_activity < now - _STALL_AFTER
        if stalled:
            # At most one re-pump per campaign per 15 min — prevents storms.
            if redis.set(f"repump:{cid}", "1", nx=True, ex=900):
                enqueue_pending_for_campaign(cid)
                repumped.append(cid)

    if finalized or repumped:
        logger.info("finalize_and_repump finalized=%s repumped=%s", finalized, repumped)
    return {"finalized": finalized, "repumped": repumped}
