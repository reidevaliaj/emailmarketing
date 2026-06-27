"""Editable planner / warming policy (stored as one app_settings row).

All numeric policy lives here so the owner can tune it from the dashboard without
a redeploy: warming ramp, daily cap, per-minute pacing, frequency interval, and
overflow behaviour.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting

KEY = "planner_config"

DEFAULTS: dict = {
    # Once-per-(contact, campaign) interval.
    "frequency_interval_days": 30,
    # Rising daily send cap (TOTAL across the IP pool) per warming week 1..4.
    "warming_ramp": [2000, 5000, 7000, 10000],
    # Daily cap after the ramp completes (full volume).
    "warming_full": 10000,
    # Per-minute pacing (the figure Contabo is believed to enforce; tune live).
    "send_rate_per_minute": 25,
    # "hard_stop" (send up to cap, skip remainder) or "defer" (future).
    "overflow_policy": "hard_stop",
}


def _merge(value) -> dict:
    cfg = dict(DEFAULTS)
    if isinstance(value, dict):
        cfg.update({k: v for k, v in value.items() if v is not None})
    return cfg


def get_planner_config_sync(session: Session) -> dict:
    row = session.get(AppSetting, KEY)
    return _merge(row.value if row else None)


async def get_planner_config(session: AsyncSession) -> dict:
    row = await session.get(AppSetting, KEY)
    return _merge(row.value if row else None)


async def set_planner_config(session: AsyncSession, cfg: dict) -> None:
    row = await session.get(AppSetting, KEY)
    # Keep only known keys; coerce numeric.
    clean: dict = {}
    for k in ("frequency_interval_days", "send_rate_per_minute", "warming_full"):
        if k in cfg and cfg[k] is not None:
            clean[k] = int(cfg[k])
    if "warming_ramp" in cfg and cfg["warming_ramp"]:
        clean["warming_ramp"] = [int(x) for x in cfg["warming_ramp"]][:4]
    if cfg.get("overflow_policy") in ("hard_stop", "defer"):
        clean["overflow_policy"] = cfg["overflow_policy"]
    if row is None:
        session.add(AppSetting(key=KEY, value=clean))
    else:
        row.value = {**_merge(row.value), **clean}
    await session.flush()
