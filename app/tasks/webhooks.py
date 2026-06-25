"""Webhook processing task.

The FastAPI endpoint verifies the shared secret, then enqueues the raw payload
here so reconciliation logic lives in ONE place (sync) and the HTTP handler can
return 200 immediately. Idempotency/dedup is handled in ``apply_event``.
"""

from __future__ import annotations

from app.celery_app import celery_app
from app.db import sync_session
from app.integrations.postal.webhook import parse_event
from app.logging import get_logger
from app.services.bounce import apply_event

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.webhooks.process_webhook")
def process_webhook(body: dict) -> str:
    event = parse_event(body)
    with sync_session() as session:
        outcome = apply_event(session, event)
    logger.info("webhook %s -> %s", event.event_type, outcome)
    return outcome
