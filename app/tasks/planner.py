"""Planner beat task — creates today's runs from the 4-week grid.

Runs hourly (and shortly after midnight). It's idempotent: a recurring campaign
gets at most one run per calendar day regardless of how often this fires, and
the actual send time is honoured because each run is scheduled at its own
send_time/timezone and picked up by dispatch_due_campaigns.
"""

from __future__ import annotations

from app.celery_app import celery_app
from app.logging import get_logger
from app.services.planner_engine import dispatch_planner

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.planner.run_planner")
def run_planner() -> dict:
    result = dispatch_planner()
    if result.get("created"):
        logger.info("planner dispatch: %s", result)
    return result
