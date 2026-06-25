"""Bounce / webhook reconciliation (Section 7).

OWNER POLICY (implemented exactly): ANY bounce / failure / complaint of ANY type
=> global suppression, permanently. No hard/soft classification. A cleaner list
is the explicit priority.

The suppress decision passes through ONE seam — ``classify_bounce`` — shipped as
"always suppress". A future maintainer can swap in SMTP-status-code logic
(retry 4.x.x soft, suppress 5.x.x permanent, ignore 5.7.x policy notices)
WITHOUT touching the rest of the pipeline. Do NOT build that now.

Reconciliation is idempotent: Postal may redeliver webhooks, so events are
deduped on Postal's event uuid (``email_events.provider_event_id``).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.postal.webhook import PostalWebhookEvent
from app.logging import get_logger
from app.models.campaign import CampaignRecipient, EmailEvent
from app.models.enums import RecipientStatus, SuppressionReason
from app.services.counters import bump_campaign
from app.services.suppression import suppress_sync

logger = get_logger(__name__)


def classify_bounce(event: PostalWebhookEvent) -> bool:
    """Return True if this event should globally suppress the address.

    SEAM (Section 7): default policy = suppress on ANY failure/complaint.
    Replace this body to inspect ``event.status`` (e.g. SMTP 4.x.x vs 5.x.x)
    for selective suppression — nothing else needs to change.
    """
    return True


def _is_duplicate(session: Session, event: PostalWebhookEvent) -> bool:
    if not event.event_uuid:
        return False
    existing = session.scalar(
        select(EmailEvent.id).where(EmailEvent.provider_event_id == event.event_uuid)
    )
    return existing is not None


def apply_event(session: Session, event: PostalWebhookEvent) -> str:
    """Reconcile one normalized Postal event against the recipient ledger.

    Returns a short status string for logging/tests:
    duplicate | unmatched | delivered | bounced | complaint | ignored.
    """
    if _is_duplicate(session, event):
        return "duplicate"

    cr = None
    if event.message_token:
        cr = session.scalar(
            select(CampaignRecipient).where(
                CampaignRecipient.postal_message_id == event.message_token
            )
        )

    if cr is None:
        # Can't tie to a ledger row (foreign message / missing token). We still
        # honour the policy: suppress the address on any failure/complaint so we
        # never email a known-bad address again.
        if (event.is_failure or event.is_complaint) and event.recipient_email:
            if classify_bounce(event):
                reason = (
                    SuppressionReason.COMPLAINT
                    if event.is_complaint
                    else SuppressionReason.HARD_BOUNCE
                )
                suppress_sync(session, event.recipient_email, reason, detail=event.detail)
        logger.info("webhook unmatched token=%s type=%s", event.message_token, event.event_type)
        return "unmatched"

    # Append to the append-only event log (dedup key = provider_event_id).
    session.add(
        EmailEvent(
            campaign_recipient_id=cr.id,
            type=event.event_type,
            provider_event_id=event.event_uuid,
            raw_payload=event.raw,
            occurred_at=event.occurred_at,
        )
    )
    cr.last_event_at = event.occurred_at

    if event.is_delivery:
        if cr.status != RecipientStatus.DELIVERED.value:
            cr.status = RecipientStatus.DELIVERED.value
            bump_campaign(session, cr.campaign_id, delivered_count=1)
        return "delivered"

    if event.is_failure or event.is_complaint:
        already_bounced = cr.status == RecipientStatus.BOUNCED.value
        cr.status = RecipientStatus.BOUNCED.value
        if cr.error_detail is None and event.detail:
            cr.error_detail = event.detail[:1000]
        if not already_bounced:
            bump_campaign(session, cr.campaign_id, bounced_count=1)

        # ANY failure/complaint => global, permanent suppression (owner policy).
        if classify_bounce(event):
            reason = (
                SuppressionReason.COMPLAINT
                if event.is_complaint
                else SuppressionReason.HARD_BOUNCE
            )
            suppress_sync(session, cr.email_snapshot, reason, detail=event.detail)
        return "complaint" if event.is_complaint else "bounced"

    # Other event types (held intermediate states, etc.) are logged, not acted on.
    return "ignored"
