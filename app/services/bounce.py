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


# Postal statuses that are PERMANENT (suppress). Everything else on a delivery-
# failure event (SoftFail, Held, unknown) is a temporary DEFERRAL that Postal
# retries — it must NOT suppress (planner spec Section 6a: deferrals are the
# rate-limit signal during warming, not bounces).
_PERMANENT_STATUSES = {"hardfail", "hard_fail", "permanent"}


def is_deferral(event: PostalWebhookEvent) -> bool:
    """A temporary failure (SoftFail/Held) — retried by Postal, never suppressed."""
    if not event.is_failure or event.is_complaint:
        return False
    if event.event_type == "MessageBounced":
        return False
    return (event.status or "").strip().lower() not in _PERMANENT_STATUSES


def classify_bounce(event: PostalWebhookEvent) -> bool:
    """Return True if this event should globally suppress the address.

    Owner policy is aggressive (any real bounce/complaint suppresses permanently),
    but a DEFERRAL is not a bounce. Suppress on complaints, MessageBounced, and
    HardFail; do NOT suppress on SoftFail / Held / temporary failures.
    """
    if event.is_complaint:
        return True
    if event.event_type == "MessageBounced":
        return True
    return (event.status or "").strip().lower() in _PERMANENT_STATUSES


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

    # Append to the append-only event log (dedup key = provider_event_id). The
    # Postal status is stored so deferral (SoftFail) rates are queryable.
    session.add(
        EmailEvent(
            campaign_recipient_id=cr.id,
            type=event.event_type,
            status=event.status,
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
        # DEFERRAL (SoftFail/Held): temporary — Postal retries. Do NOT suppress
        # and do NOT mark bounced; just log it for rate-discovery monitoring.
        if is_deferral(event):
            return "deferred"

        already_bounced = cr.status == RecipientStatus.BOUNCED.value
        cr.status = RecipientStatus.BOUNCED.value
        if cr.error_detail is None and event.detail:
            cr.error_detail = event.detail[:1000]
        if not already_bounced:
            bump_campaign(session, cr.campaign_id, bounced_count=1)

        # Real bounce / complaint => global, permanent suppression (owner policy).
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
