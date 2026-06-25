"""Send pipeline tasks (Section 6 — the core).

Queue-driven, never a loop in a web request:

  dispatch_due_campaigns (beat)
        -> materialize_campaign(campaign_id)        # snapshot recipient set
              -> send_one(campaign_recipient_id)     # one message, idempotent
                    -> Postal HTTP API

Idempotency: ``send_one`` is keyed on the recipient ledger row. A Redis NX lock
plus a DB status guard guarantee a retry or duplicate enqueue never double-sends.

Rate pacing: a blocked rate-limit/daily-cap does NOT consume the transient-retry
budget — it re-enqueues a fresh task with a countdown. Only real Postal transient
failures use Celery's retry (exponential backoff). Postal permanent failures mark
the recipient ``failed``. A failed API call is NOT a bounce (bounces arrive via
webhook, Section 7).
"""

from __future__ import annotations

import math

from sqlalchemy import func, select, update

from app.celery_app import celery_app
from app.config import settings
from app.db import sync_session
from app.integrations.postal import SendMessage, get_postal_client
from app.integrations.postal.base import PostalPermanentError, PostalTransientError
from app.logging import get_logger
from app.models.campaign import Campaign, CampaignRecipient
from app.models.contact import Contact
from app.models.enums import (
    TERMINAL_RECIPIENT_STATUSES,
    CampaignStatus,
    ContactStatus,
    RecipientStatus,
)
from app.models.template import Template
from app.services.merge import build_context, render
from app.services.normalize import domain_of
from app.services.rate_limit import (
    build_send_buckets,
    get_rate_limiter,
    get_redis,
    is_under_daily_cap,
    record_daily_send,
)
from app.services.suppression import is_suppressed_sync
from app.services.tokens import unsubscribe_url

logger = get_logger(__name__)

_TERMINAL_VALUES = [s.value for s in TERMINAL_RECIPIENT_STATUSES]


# --- counter helpers -------------------------------------------------------

def _bump(session, campaign_id: int, **deltas: int) -> None:
    """Atomic counter increments so concurrent workers don't clobber each other."""
    if not deltas:
        return
    values = {k: getattr(Campaign, k) + v for k, v in deltas.items()}
    session.execute(update(Campaign).where(Campaign.id == campaign_id).values(**values))


def _pending_count(session, campaign_id: int) -> int:
    return session.scalar(
        select(func.count())
        .select_from(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.status == RecipientStatus.PENDING.value,
        )
    ) or 0


def _maybe_finalize(session, campaign_id: int) -> None:
    """Mark a campaign completed once no recipients remain PENDING."""
    if _pending_count(session, campaign_id) == 0:
        campaign = session.get(Campaign, campaign_id)
        if campaign and campaign.status == CampaignStatus.SENDING.value:
            campaign.status = CampaignStatus.COMPLETED.value
            logger.info("campaign %s completed", campaign_id)


# --- enqueue helpers -------------------------------------------------------

def enqueue_pending_for_campaign(campaign_id: int) -> int:
    """Enqueue send_one for every PENDING recipient. Used by materialize & resume."""
    with sync_session() as session:
        ids = session.scalars(
            select(CampaignRecipient.id).where(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.status == RecipientStatus.PENDING.value,
            )
        ).all()
    for cr_id in ids:
        send_one.delay(cr_id)
    logger.info("enqueued %d send tasks for campaign %s", len(ids), campaign_id)
    return len(ids)


# --- materialization (Section 6.1) ----------------------------------------

@celery_app.task(name="app.tasks.sending.materialize_campaign")
def materialize_campaign(campaign_id: int) -> dict:
    """Create the per-recipient ledger for a campaign that just entered SENDING.

    For every ACTIVE contact in the campaign's list, create a campaign_recipient,
    EXCLUDING (marking skipped_suppressed) any email in the global suppression
    list. Snapshotting here means later list edits can't corrupt an in-flight
    campaign. Idempotent: re-running skips contacts already materialized.
    """
    with sync_session() as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            return {"error": "campaign not found"}
        if campaign.status != CampaignStatus.SENDING.value:
            return {"skipped": "campaign not in sending state", "status": campaign.status}
        if campaign.list_id is None:
            campaign.status = CampaignStatus.FAILED.value
            return {"error": "campaign has no list"}

        # Already-materialized contact ids (idempotent re-run).
        existing_ids = set(
            session.scalars(
                select(CampaignRecipient.contact_id).where(
                    CampaignRecipient.campaign_id == campaign_id
                )
            ).all()
        )

        # One join classifies suppressed vs sendable in bulk (no N+1 lookups).
        from app.models.suppression import Suppression

        rows = session.execute(
            select(
                Contact.id,
                Contact.email,
                Contact.first_name,
                Contact.last_name,
                Contact.custom_fields,
                Suppression.id,
            )
            .outerjoin(Suppression, Suppression.email == Contact.email)
            .where(
                Contact.list_id == campaign.list_id,
                Contact.status == ContactStatus.ACTIVE.value,
            )
        ).all()

        created = skipped = 0
        batch = 0
        for contact_id, email, first, last, custom, supp_id in rows:
            if contact_id in existing_ids:
                continue
            merge_snapshot = {
                "first_name": first,
                "last_name": last,
                "custom_fields": custom or {},
            }
            if supp_id is not None:
                session.add(
                    CampaignRecipient(
                        campaign_id=campaign_id,
                        contact_id=contact_id,
                        email_snapshot=email,
                        merge_snapshot=merge_snapshot,
                        ip_pool=campaign.ip_pool,
                        status=RecipientStatus.SKIPPED_SUPPRESSED.value,
                    )
                )
                skipped += 1
            else:
                session.add(
                    CampaignRecipient(
                        campaign_id=campaign_id,
                        contact_id=contact_id,
                        email_snapshot=email,
                        merge_snapshot=merge_snapshot,
                        ip_pool=campaign.ip_pool,
                        status=RecipientStatus.PENDING.value,
                    )
                )
                created += 1
            batch += 1
            if batch % 1000 == 0:
                session.flush()

        # Counters (recomputed from the full ledger for accuracy on re-run).
        total = session.scalar(
            select(func.count()).select_from(CampaignRecipient).where(
                CampaignRecipient.campaign_id == campaign_id
            )
        )
        total_skipped = session.scalar(
            select(func.count()).select_from(CampaignRecipient).where(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.status == RecipientStatus.SKIPPED_SUPPRESSED.value,
            )
        )
        campaign.total_recipients = total or 0
        campaign.skipped_count = total_skipped or 0
        campaign.queued_count = (total or 0) - (total_skipped or 0)

    enqueue_pending_for_campaign(campaign_id)
    logger.info(
        "materialized campaign %s: created=%d skipped_suppressed=%d", campaign_id, created, skipped
    )
    return {"created": created, "skipped_suppressed": skipped}


# --- per-recipient send (Sections 6.2-6.4) --------------------------------

@celery_app.task(
    bind=True,
    name="app.tasks.sending.send_one",
    max_retries=settings.send_max_retries,
    acks_late=True,
)
def send_one(self, campaign_recipient_id: int) -> str:
    redis = get_redis()
    lock_key = f"lock:send:{campaign_recipient_id}"
    # NX lock: only one worker processes a given recipient at a time.
    if not redis.set(lock_key, "1", nx=True, ex=180):
        return "locked"

    try:
        # --- Phase 1: validate + suppression re-check (point #2) ----------
        with sync_session() as session:
            cr = session.get(CampaignRecipient, campaign_recipient_id)
            if cr is None:
                return "missing"
            if cr.status != RecipientStatus.PENDING.value:
                return "already-processed"  # idempotent guard

            campaign = session.get(Campaign, cr.campaign_id)
            if campaign is None:
                return "no-campaign"
            cstatus = campaign.status
            if cstatus == CampaignStatus.PAUSED.value:
                return "paused"  # leave PENDING; resume re-enqueues
            if cstatus != CampaignStatus.SENDING.value:
                return f"campaign-{cstatus}"  # cancelled/completed/failed/draft

            email = cr.email_snapshot
            if is_suppressed_sync(session, email):
                cr.status = RecipientStatus.SKIPPED_SUPPRESSED.value
                _bump(session, campaign.id, skipped_count=1)
                _maybe_finalize(session, campaign.id)
                return "suppressed"

            # Snapshot everything needed to send, then release the DB txn before
            # the network call (never hold a transaction across HTTP I/O).
            campaign_id = campaign.id
            domain = domain_of(email)
            pool = cr.ip_pool or campaign.ip_pool or "default"
            subject_tpl = campaign.subject
            from_name = campaign.from_name
            from_email = campaign.from_email
            merge = dict(cr.merge_snapshot or {})
            template = (
                session.get(Template, campaign.template_id) if campaign.template_id else None
            )
            tmpl_type = template.type if template else "plain"
            tmpl_body = template.body if template else ""
            if template is None:
                cr.status = RecipientStatus.FAILED.value
                cr.error_detail = "campaign has no template"
                _bump(session, campaign_id, failed_count=1)
                _maybe_finalize(session, campaign_id)
                return "no-template"

        # --- Phase 2: pacing (does NOT consume retry budget) --------------
        rate = get_rate_limiter().acquire(build_send_buckets(domain))
        if not rate.allowed:
            countdown = min(max(1, math.ceil(rate.retry_after)), 60)
            send_one.apply_async((campaign_recipient_id,), countdown=countdown)
            return "rate-limited"

        cap = is_under_daily_cap(redis, pool)
        if not cap.allowed:
            countdown = min(max(60, math.ceil(cap.retry_after)), 3600)
            send_one.apply_async((campaign_recipient_id,), countdown=countdown)
            logger.info("daily cap reached for pool=%s; deferring recipient %s", pool, campaign_recipient_id)
            return "daily-cap"

        # --- Phase 3: render + send --------------------------------------
        unsub = unsubscribe_url(email)
        ctx = build_context(
            email=email,
            first_name=merge.get("first_name"),
            last_name=merge.get("last_name"),
            unsubscribe_url=unsub,
            custom_fields=merge.get("custom_fields"),
        )
        is_html = tmpl_type == "html"
        rendered_body = render(tmpl_body, ctx, html=is_html)
        rendered_subject = render(subject_tpl, ctx, html=False)
        message = SendMessage(
            to=email,
            from_email=from_email,
            from_name=from_name,
            subject=rendered_subject,
            plain_body=None if is_html else rendered_body,
            html_body=rendered_body if is_html else None,
            headers={
                "List-Unsubscribe": f"<{unsub}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
            tag=f"campaign-{campaign_id}:{pool}",
        )

        try:
            result = get_postal_client().send_message(message)
        except PostalTransientError as exc:
            # Temporary — retry with exponential backoff (does not "send").
            backoff = settings.send_retry_backoff_seconds * (2 ** self.request.retries)
            logger.warning(
                "transient send failure recipient=%s attempt=%d: %s",
                campaign_recipient_id, self.request.retries, exc,
            )
            raise self.retry(exc=exc, countdown=min(backoff, 3600))
        except PostalPermanentError as exc:
            with sync_session() as session:
                cr = session.get(CampaignRecipient, campaign_recipient_id)
                if cr and cr.status == RecipientStatus.PENDING.value:
                    cr.status = RecipientStatus.FAILED.value
                    cr.error_detail = f"permanent: {exc}"
                    _bump(session, campaign_id, failed_count=1)
                    _maybe_finalize(session, campaign_id)
            logger.error("permanent send failure recipient=%s: %s", campaign_recipient_id, exc)
            return "failed"

        # --- Phase 4: persist success ------------------------------------
        with sync_session() as session:
            cr = session.get(CampaignRecipient, campaign_recipient_id)
            if cr is None:
                return "missing-after-send"
            # Guard against a concurrent transition (e.g. cancelled mid-flight).
            if cr.status == RecipientStatus.PENDING.value:
                cr.status = RecipientStatus.SENT.value
                cr.postal_message_id = result.message_token
                cr.ip_pool = pool
                _bump(session, campaign_id, sent_count=1)
                _maybe_finalize(session, campaign_id)
        record_daily_send(redis, pool)
        return "sent"
    finally:
        redis.delete(lock_key)
