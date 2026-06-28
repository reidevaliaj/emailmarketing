"""Lightweight planner monitoring (Section 8).

A weekly-glance view: per-campaign status, warming status, the deferral/temp-
failure rate (the key rate-discovery signal), and low-inventory flags.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SyncSessionLocal
from app.models.campaign import Campaign, CampaignRecipient, EmailEvent
from app.models.contact import Contact
from app.models.planner import CampaignList
from app.services.planner import _eligible_count, _list_names, color_for
from app.services.planner_config import get_planner_config
from app.services.rate_limit import get_redis, global_sent_today
from app.services.warming import warming_summary

# Postal statuses that mean "temporary deferral" (the rate-limit signal).
_DEFERRAL_STATUSES = ("SoftFail", "Held")
_LOW_INVENTORY = 200  # eligible below this => flag to add fresh lists


async def _deferrals(session: AsyncSession) -> dict:
    """Today's deferral + bounce counts, overall and per IP pool."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (
        await session.execute(
            select(CampaignRecipient.ip_pool, EmailEvent.status, func.count())
            .join(CampaignRecipient, CampaignRecipient.id == EmailEvent.campaign_recipient_id)
            .where(EmailEvent.occurred_at >= start)
            .group_by(CampaignRecipient.ip_pool, EmailEvent.status)
        )
    ).all()
    per_pool: dict[str, dict] = {}
    total_def = total_sent_evt = 0
    for pool, status, count in rows:
        pool = pool or "default"
        p = per_pool.setdefault(pool, {"deferred": 0, "sent": 0, "bounced": 0})
        if status in _DEFERRAL_STATUSES:
            p["deferred"] += count
            total_def += count
        elif status == "Sent":
            p["sent"] += count
            total_sent_evt += count
        elif status in ("HardFail", "Bounced"):
            p["bounced"] += count
    sent_today = global_sent_today(get_redis())
    rate = round((total_def / sent_today) * 100, 1) if sent_today else 0.0
    return {
        "sent_today": sent_today,
        "deferred_today": total_def,
        "deferral_rate_pct": rate,
        "per_pool": per_pool,
    }


async def monitor(session: AsyncSession) -> dict:
    cfg = await get_planner_config(session)
    interval = int(cfg["frequency_interval_days"])

    with SyncSessionLocal() as sync_s:
        warming = warming_summary(sync_s)

    recurring = list(
        await session.scalars(
            select(Campaign).where(Campaign.is_planner.is_(True)).order_by(Campaign.name)
        )
    )
    campaigns = []
    for c in recurring:
        last_run = await session.scalar(
            select(Campaign)
            .where(Campaign.parent_campaign_id == c.id)
            .order_by(Campaign.scheduled_at.desc())
            .limit(1)
        )
        list_ids = list(
            await session.scalars(select(CampaignList.list_id).where(CampaignList.campaign_id == c.id))
        )
        total_contacts = 0
        if list_ids:
            total_contacts = int(
                await session.scalar(
                    select(func.count()).select_from(Contact).where(
                        Contact.list_id.in_(list_ids), Contact.status == "active"
                    )
                ) or 0
            )
        eligible = await _eligible_count(session, c.id, interval)
        campaigns.append({
            "id": c.id, "name": c.name, "color": color_for(c.id),
            "lists": await _list_names(session, c.id),
            "total_contacts": total_contacts, "eligible": eligible,
            "last_fired": last_run.scheduled_at if last_run else None,
            "last_reached": last_run.sent_count if last_run else 0,
            "low_inventory": total_contacts == 0 or eligible < _LOW_INVENTORY,
        })

    return {
        "warming": warming,
        "deferrals": await _deferrals(session),
        "campaigns": campaigns,
        "low_inventory_count": sum(1 for c in campaigns if c["low_inventory"]),
    }
