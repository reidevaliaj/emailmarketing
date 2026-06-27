"""A campaign's lists (many-to-many). A campaign pools + dedupes across these.

Contacts are globally unique (one row, one list), so pooling lists yields
distinct contacts automatically — no cross-list de-dup needed.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.contact_list import ContactList
from app.models.planner import CampaignList


def campaign_list_ids_sync(session: Session, campaign_id: int) -> list[int]:
    """Effective list ids for a campaign (join table, falling back to legacy list_id)."""
    ids = list(
        session.scalars(
            select(CampaignList.list_id).where(CampaignList.campaign_id == campaign_id)
        )
    )
    if ids:
        return ids
    from app.models.campaign import Campaign

    legacy = session.scalar(select(Campaign.list_id).where(Campaign.id == campaign_id))
    return [legacy] if legacy else []


async def get_campaign_list_ids(session: AsyncSession, campaign_id: int) -> list[int]:
    return list(
        await session.scalars(
            select(CampaignList.list_id).where(CampaignList.campaign_id == campaign_id)
        )
    )


async def get_campaign_lists(session: AsyncSession, campaign_id: int) -> list[ContactList]:
    return list(
        await session.scalars(
            select(ContactList)
            .join(CampaignList, CampaignList.list_id == ContactList.id)
            .where(CampaignList.campaign_id == campaign_id)
            .order_by(ContactList.name)
        )
    )


async def set_campaign_lists(
    session: AsyncSession, campaign_id: int, list_ids: list[int]
) -> None:
    """Replace a campaign's list set with ``list_ids``."""
    await session.execute(delete(CampaignList).where(CampaignList.campaign_id == campaign_id))
    seen: set[int] = set()
    for lid in list_ids:
        if lid and lid not in seen:
            session.add(CampaignList(campaign_id=campaign_id, list_id=lid))
            seen.add(lid)
    await session.flush()
