"""Authenticated JSON status API (Section 8).

API-key protected (X-API-Key header or Bearer token). Lets external tools poll
sending status and aggregate health. The paid third-party spam-score integration
is intentionally a stub (``spam_score: null``) — a clearly-marked place to add
one later without changing the contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_api_key
from app.services.campaigns import get_campaign
from app.services.stats import campaign_progress, dashboard_stats

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
async def status(session: AsyncSession = Depends(get_db), _key=Depends(require_api_key)):
    stats = await dashboard_stats(session)
    return {
        "totals": stats.totals,
        "bounce_rate_pct": stats.bounce_rate,
        "suppression_size": stats.suppression_size,
        "per_pool_today": stats.per_pool_today,
        "recent_campaigns": stats.campaigns,
        # STUB: integrate a paid spam-score service here in future.
        "spam_score": None,
    }


@router.get("/campaigns/{campaign_id}")
async def campaign_status(
    campaign_id: int,
    session: AsyncSession = Depends(get_db),
    _key=Depends(require_api_key),
):
    campaign = await get_campaign(session, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    return campaign_progress(campaign)
