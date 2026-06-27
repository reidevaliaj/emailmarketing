"""4-Week Planner engine (Section 5).

Maps today's calendar date to a month position (Week N = the Nth occurrence of
that weekday; weekends and a 5th occurrence are idle), finds the campaigns placed
there, and clones each into a one-off scheduled RUN at its own send time/timezone.
The existing dispatch_due_campaigns + materialize + send pipeline then takes over
(frequency rule + warming cap apply automatically because the run carries
parent_campaign_id and pooled lists).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import sync_session
from app.logging import get_logger
from app.models.campaign import Campaign
from app.models.enums import CampaignStatus
from app.models.planner import CampaignList, PlannerPlacement
from app.services.campaign_lists import campaign_list_ids_sync

logger = get_logger(__name__)

WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def position_for_date(d: date) -> tuple[int, int] | None:
    """(week 1..4, weekday 0..4) for a date, or None if weekend / 5th occurrence."""
    wd = d.weekday()  # 0=Mon .. 6=Sun
    if wd > 4:
        return None
    occurrence = (d.day - 1) // 7 + 1   # Nth occurrence of THIS weekday in the month
    if occurrence > 4:
        return None                     # 5th week is idle (documented default)
    return occurrence, wd


def _app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.app_timezone)
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def today_local() -> date:
    return datetime.now(_app_tz()).date()


def _run_datetime_utc(local_date: date, send_time: str | None, send_tz: str | None) -> datetime:
    try:
        tz = ZoneInfo(send_tz) if send_tz else _app_tz()
    except Exception:  # noqa: BLE001
        tz = _app_tz()
    try:
        hh, mm = (send_time or "09:00").split(":")
        hh, mm = int(hh), int(mm)
    except (ValueError, AttributeError):
        hh, mm = 9, 0
    local_dt = datetime.combine(local_date, time(hh, mm), tzinfo=tz)
    return local_dt.astimezone(timezone.utc)


def _run_exists_for(session: Session, recurring_id: int, local_date: date) -> bool:
    """Has a run for this recurring campaign already been created for local_date?"""
    start = datetime.combine(local_date, time(0, 0), tzinfo=_app_tz()).astimezone(timezone.utc)
    candidates = session.scalars(
        select(Campaign).where(
            Campaign.parent_campaign_id == recurring_id,
            Campaign.scheduled_at >= start - timedelta(hours=18),
            Campaign.scheduled_at <= start + timedelta(hours=42),
        )
    ).all()
    app_tz = _app_tz()
    return any(
        c.scheduled_at and c.scheduled_at.astimezone(app_tz).date() == local_date
        for c in candidates
    )


def ensure_run(recurring_id: int, local_date: date) -> int | None:
    """Idempotently create today's scheduled run for a recurring campaign."""
    with sync_session() as session:
        rec = session.get(Campaign, recurring_id)
        if rec is None or not rec.is_planner:
            return None
        if _run_exists_for(session, recurring_id, local_date):
            return None
        send_dt = _run_datetime_utc(local_date, rec.send_time, rec.send_timezone)
        run = Campaign(
            name=f"{rec.name} — {local_date.isoformat()}",
            subject=rec.subject, from_name=rec.from_name, from_email=rec.from_email,
            reply_to=rec.reply_to, template_id=rec.template_id, ip_pool=rec.ip_pool,
            send_time=rec.send_time, send_timezone=rec.send_timezone,
            is_planner=False, parent_campaign_id=rec.id,
            status=CampaignStatus.SCHEDULED.value, scheduled_at=send_dt,
        )
        session.add(run)
        session.flush()
        for lid in campaign_list_ids_sync(session, rec.id):
            session.add(CampaignList(campaign_id=run.id, list_id=lid))
        run_id = run.id
    logger.info("planner created run %s for recurring campaign %s (%s)", run_id, recurring_id, local_date)
    return run_id


def dispatch_planner(for_date: date | None = None) -> dict:
    """Create today's planner runs. Safe to call repeatedly (idempotent)."""
    local_date = for_date or today_local()
    pos = position_for_date(local_date)
    if pos is None:
        return {"date": local_date.isoformat(), "position": None, "created": []}
    week, weekday = pos
    with sync_session() as session:
        placement_campaign_ids = list(
            session.scalars(
                select(PlannerPlacement.campaign_id).where(
                    PlannerPlacement.week == week, PlannerPlacement.weekday == weekday
                )
            )
        )
    created = [rid for cid in placement_campaign_ids if (rid := ensure_run(cid, local_date))]
    return {
        "date": local_date.isoformat(),
        "position": [week, weekday, WEEKDAY_LABELS[weekday]],
        "campaigns": placement_campaign_ids,
        "created": created,
    }
