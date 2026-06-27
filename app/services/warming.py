"""IP warming: a rising daily send cap (Section 6).

The TOTAL daily cap across the pool = sum over active IPs of each IP's ramp value
for its current age. A newly-added cold IP therefore ramps on its own clock
without resetting the warm ones. The engine enforces this aggregate cap; Postal's
IP pool handles the actual per-IP assignment.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base import utcnow
from app.models.planner import IPWarmState
from app.services.planner_config import get_planner_config_sync


def ramp_value(cfg: dict, age_days: int) -> int:
    """Daily cap for one IP that has been warming ``age_days`` days."""
    ramp = cfg.get("warming_ramp") or [2000, 5000, 7000, 10000]
    full = int(cfg.get("warming_full") or ramp[-1])
    week = max(0, age_days) // 7  # 0-based week
    if week < len(ramp):
        return int(ramp[week])
    return full


def current_daily_cap(session: Session) -> int:
    """Total warming cap today = sum of each active IP's ramp value for its age.

    With no recorded warm-state, assume a single cold IP (conservative).
    """
    cfg = get_planner_config_sync(session)
    states = list(session.scalars(select(IPWarmState).where(IPWarmState.is_active.is_(True))))
    if not states:
        return ramp_value(cfg, 0)
    now = utcnow()
    total = 0
    for s in states:
        since = s.warmed_since
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        age_days = (now - since).days
        total += ramp_value(cfg, age_days)
    return total


def warming_summary(session: Session) -> dict:
    cfg = get_planner_config_sync(session)
    states = list(session.scalars(select(IPWarmState)))
    now = utcnow()
    ips = []
    for s in states:
        since = s.warmed_since
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        age = (now - since).days
        ips.append({
            "ip": s.ip, "pool": s.pool, "active": s.is_active,
            "age_days": age, "week": age // 7 + 1, "daily_cap": ramp_value(cfg, age),
        })
    return {"total_daily_cap": current_daily_cap(session), "ips": ips, "ramp": cfg.get("warming_ramp")}


def ensure_ip_warm_state(
    session: Session, ip: str, pool: str | None = None, warmed_since: datetime | None = None
) -> IPWarmState:
    state = session.scalar(select(IPWarmState).where(IPWarmState.ip == ip))
    if state is None:
        state = IPWarmState(ip=ip, pool=pool, warmed_since=warmed_since or utcnow())
        session.add(state)
        session.flush()
    return state
