"""Planner UI service (async): grid, eligible counts, placement + campaign CRUD."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.contact_list import ContactList
from app.models.enums import CampaignStatus
from app.models.planner import (
    CampaignContactSend,
    CampaignList,
    PlannerPlacement,
)
from app.models.suppression import Suppression
from app.models.template import Template
from app.services.campaign_lists import set_campaign_lists
from app.services.planner_config import get_planner_config
from app.services.warming import current_daily_cap

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
WEEKDAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri"]

# Common timezones for the per-campaign send time (free text also accepted).
COMMON_TIMEZONES = [
    "Europe/Tirana", "Europe/Berlin", "Europe/London", "Europe/Madrid",
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "America/Toronto", "Australia/Sydney", "Asia/Dubai", "UTC",
]

# Deterministic per-service colour so cells are legible at a glance.
_PALETTE = [
    "#2563eb", "#16a34a", "#db2777", "#ea580c", "#7c3aed",
    "#0891b2", "#ca8a04", "#dc2626", "#4f46e5", "#0d9488",
]


def color_for(campaign_id: int) -> str:
    return _PALETTE[campaign_id % len(_PALETTE)]


@dataclass
class CardData:
    placement_id: int
    campaign_id: int
    name: str
    template_name: str | None
    list_names: list[str]
    send_time: str | None
    send_timezone: str | None
    eligible: int
    color: str


async def _eligible_count(session: AsyncSession, campaign_id: int, interval_days: int) -> int:
    """Active contacts in the campaign's lists, not suppressed, not sent within interval."""
    list_ids = list(
        await session.scalars(
            select(CampaignList.list_id).where(CampaignList.campaign_id == campaign_id)
        )
    )
    if not list_ids:
        return 0
    cutoff = utcnow() - timedelta(days=interval_days)
    recent = (
        select(CampaignContactSend.contact_id)
        .where(
            CampaignContactSend.campaign_id == campaign_id,
            CampaignContactSend.last_sent_at >= cutoff,
        )
        .scalar_subquery()
    )
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Contact)
            .outerjoin(Suppression, Suppression.email == Contact.email)
            .where(
                Contact.list_id.in_(list_ids),
                Contact.status == "active",
                Suppression.id.is_(None),
                Contact.id.not_in(recent),
            )
        )
        or 0
    )


async def _list_names(session: AsyncSession, campaign_id: int) -> list[str]:
    return list(
        await session.scalars(
            select(ContactList.name)
            .join(CampaignList, CampaignList.list_id == ContactList.id)
            .where(CampaignList.campaign_id == campaign_id)
        )
    )


async def grid(session: AsyncSession) -> dict:
    """Full 4x5 grid + day totals + the warming cap for over-cap warnings."""
    cfg = await get_planner_config(session)
    interval = int(cfg["frequency_interval_days"])
    rows = (
        await session.execute(
            select(PlannerPlacement, Campaign)
            .join(Campaign, Campaign.id == PlannerPlacement.campaign_id)
            .order_by(PlannerPlacement.week, PlannerPlacement.weekday)
        )
    ).all()

    cells: dict[tuple[int, int], list[CardData]] = {}
    tmpl_names: dict[int, str] = {}
    for placement, camp in rows:
        if camp.template_id and camp.template_id not in tmpl_names:
            tmpl_names[camp.template_id] = (
                await session.scalar(select(Template.name).where(Template.id == camp.template_id))
            ) or "—"
        card = CardData(
            placement_id=placement.id, campaign_id=camp.id, name=camp.name,
            template_name=tmpl_names.get(camp.template_id),
            list_names=await _list_names(session, camp.id),
            send_time=camp.send_time, send_timezone=camp.send_timezone,
            eligible=await _eligible_count(session, camp.id, interval),
            color=color_for(camp.id),
        )
        cells.setdefault((placement.week, placement.weekday), []).append(card)

    # current_daily_cap is sync (reads IP warm-state); a short sync read here is
    # fine for an admin page.
    from app.db import SyncSessionLocal

    with SyncSessionLocal() as sync_s:
        daily_cap = current_daily_cap(sync_s)

    matrix = []
    for week in range(1, 5):
        row = []
        for wd in range(5):
            cards = cells.get((week, wd), [])
            row.append({"week": week, "weekday": wd, "cards": cards,
                        "total": sum(c.eligible for c in cards)})
        matrix.append(row)

    day_totals = [sum(matrix[w][wd]["total"] for w in range(4)) for wd in range(5)]
    return {"matrix": matrix, "day_totals": day_totals, "daily_cap": daily_cap,
            "weekdays": WEEKDAYS_SHORT, "interval": interval}


async def recurring_campaigns(session: AsyncSession) -> list[dict]:
    """Side-panel list of recurring campaigns available to place."""
    cfg = await get_planner_config(session)
    interval = int(cfg["frequency_interval_days"])
    camps = list(
        await session.scalars(
            select(Campaign).where(Campaign.is_planner.is_(True)).order_by(Campaign.name)
        )
    )
    out = []
    for c in camps:
        out.append({
            "id": c.id, "name": c.name, "color": color_for(c.id),
            "send_time": c.send_time, "send_timezone": c.send_timezone,
            "lists": await _list_names(session, c.id),
            "eligible": await _eligible_count(session, c.id, interval),
            "placed": int(await session.scalar(
                select(func.count()).select_from(PlannerPlacement).where(
                    PlannerPlacement.campaign_id == c.id))) ,
        })
    return out


# --- placement CRUD --------------------------------------------------------

async def add_placement(session: AsyncSession, campaign_id: int, week: int, weekday: int):
    if not (1 <= week <= 4 and 0 <= weekday <= 4):
        return None
    exists = await session.scalar(
        select(PlannerPlacement.id).where(
            PlannerPlacement.campaign_id == campaign_id,
            PlannerPlacement.week == week,
            PlannerPlacement.weekday == weekday,
        )
    )
    if exists:
        return exists
    p = PlannerPlacement(campaign_id=campaign_id, week=week, weekday=weekday)
    session.add(p)
    await session.flush()
    return p.id


async def remove_placement(session: AsyncSession, placement_id: int) -> bool:
    res = await session.execute(
        delete(PlannerPlacement).where(PlannerPlacement.id == placement_id)
    )
    return res.rowcount > 0


async def move_placement(session: AsyncSession, placement_id: int, week: int, weekday: int) -> bool:
    if not (1 <= week <= 4 and 0 <= weekday <= 4):
        return False
    p = await session.get(PlannerPlacement, placement_id)
    if p is None:
        return False
    # If the campaign already sits in the target cell, just drop this placement.
    dup = await session.scalar(
        select(PlannerPlacement.id).where(
            PlannerPlacement.campaign_id == p.campaign_id,
            PlannerPlacement.week == week,
            PlannerPlacement.weekday == weekday,
            PlannerPlacement.id != placement_id,
        )
    )
    if dup:
        await session.delete(p)
        return True
    p.week = week
    p.weekday = weekday
    await session.flush()
    return True


# --- recurring campaign CRUD ----------------------------------------------

async def create_recurring(session: AsyncSession, *, list_ids: list[int], **fields) -> Campaign:
    camp = Campaign(
        name=(fields.get("name") or "Untitled service").strip(),
        subject=fields.get("subject") or "",
        from_name=fields.get("from_name") or "",
        from_email=fields.get("from_email") or "",
        reply_to=(fields.get("reply_to") or None),
        template_id=fields.get("template_id"),
        ip_pool=fields.get("ip_pool"),
        send_time=fields.get("send_time") or "09:00",
        send_timezone=fields.get("send_timezone") or "Europe/Tirana",
        is_planner=True,
        status=CampaignStatus.PLANNER.value,
    )
    session.add(camp)
    await session.flush()
    await set_campaign_lists(session, camp.id, list_ids)
    return camp


async def update_recurring(session: AsyncSession, campaign_id: int, *, list_ids=None, **fields):
    camp = await session.get(Campaign, campaign_id)
    if camp is None or not camp.is_planner:
        return None
    for k in ("name", "subject", "from_name", "from_email", "reply_to",
              "template_id", "ip_pool", "send_time", "send_timezone"):
        if k in fields and fields[k] is not None:
            setattr(camp, k, (fields[k] or None) if k == "reply_to" else fields[k])
    if list_ids is not None:
        await set_campaign_lists(session, campaign_id, list_ids)
    await session.flush()
    return camp


async def delete_recurring(session: AsyncSession, campaign_id: int) -> bool:
    camp = await session.get(Campaign, campaign_id)
    if camp is None or not camp.is_planner:
        return False
    await session.delete(camp)  # placements + lists cascade
    return True
