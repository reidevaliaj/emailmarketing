"""Aggregate sending-health stats for the dashboard and status API (Section 8)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.campaign import Campaign
from app.models.suppression import Suppression
from app.services.rate_limit import daily_cap_usage, get_redis


def campaign_progress(c: Campaign) -> dict:
    processed = c.sent_count + c.failed_count + c.skipped_count
    pending = max(0, c.queued_count - c.sent_count - c.failed_count)
    return {
        "id": c.id,
        "name": c.name,
        "status": c.status,
        "total": c.total_recipients,
        "queued": c.queued_count,
        "sent": c.sent_count,
        "delivered": c.delivered_count,
        "bounced": c.bounced_count,
        "failed": c.failed_count,
        "skipped_suppressed": c.skipped_count,
        "pending": pending,
        "processed": processed,
        "scheduled_at": c.scheduled_at.isoformat() if c.scheduled_at else None,
    }


@dataclass
class DashboardStats:
    totals: dict
    bounce_rate: float
    suppression_size: int
    per_pool_today: list[dict]
    campaigns: list[dict]


async def _distinct_pools(session: AsyncSession) -> list[str]:
    rows = await session.scalars(select(Campaign.ip_pool).distinct())
    pools = {p for p in rows if p}
    pools.add("default")
    return sorted(pools)


async def dashboard_stats(session: AsyncSession, recent: int = 20) -> DashboardStats:
    agg = (
        await session.execute(
            select(
                func.coalesce(func.sum(Campaign.sent_count), 0),
                func.coalesce(func.sum(Campaign.delivered_count), 0),
                func.coalesce(func.sum(Campaign.bounced_count), 0),
                func.coalesce(func.sum(Campaign.failed_count), 0),
                func.coalesce(func.sum(Campaign.skipped_count), 0),
            )
        )
    ).one()
    sent, delivered, bounced, failed, skipped = (int(x) for x in agg)

    # Bounce rate over messages that reached the MTA (sent). Aggressive policy
    # means any bounce removes the address, so this is the key health metric.
    bounce_rate = round((bounced / sent) * 100, 2) if sent else 0.0

    suppression_size = int(
        await session.scalar(select(func.count()).select_from(Suppression)) or 0
    )

    redis = get_redis()
    per_pool_today = []
    for pool in await _distinct_pools(session):
        used = daily_cap_usage(redis, pool)
        per_pool_today.append(
            {"pool": pool, "sent_today": used, "cap": settings.per_ip_daily_cap}
        )

    campaigns = await session.scalars(
        select(Campaign).order_by(Campaign.created_at.desc()).limit(recent)
    )
    return DashboardStats(
        totals={
            "sent": sent, "delivered": delivered, "bounced": bounced,
            "failed": failed, "skipped_suppressed": skipped,
        },
        bounce_rate=bounce_rate,
        suppression_size=suppression_size,
        per_pool_today=per_pool_today,
        campaigns=[campaign_progress(c) for c in campaigns],
    )
