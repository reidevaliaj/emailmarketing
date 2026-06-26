"""Suppression enforcement — the single most important correctness rule.

Enforced at TWO points (Section 9): recipient materialization AND per-job send.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db import sync_session
from app.models.campaign import CampaignRecipient
from app.models.enums import RecipientStatus, SuppressionReason
from app.models.suppression import Suppression
from app.services.suppression import suppress_sync
from app.tasks.sending import materialize_campaign, send_one
from tests.factories import make_campaign


def _recipient(email):
    with sync_session() as s:
        return s.scalar(
            select(CampaignRecipient).where(CampaignRecipient.email_snapshot == email)
        )


def test_materialization_skips_suppressed():
    ids = make_campaign(["keep@acme.com", "block@acme.com"])
    # Suppress the email WITHOUT flipping the contact's active status, so the
    # materialization suppression branch (not the status filter) is exercised —
    # this is the safety net for an active contact whose email is suppressed.
    with sync_session() as s:
        suppress_sync(s, "block@acme.com", SuppressionReason.MANUAL, update_contact=False)

    materialize_campaign(ids["campaign_id"])

    assert _recipient("block@acme.com").status == RecipientStatus.SKIPPED_SUPPRESSED.value
    assert _recipient("keep@acme.com").status == RecipientStatus.SENT.value  # sent via mock


def test_send_time_recheck_suppresses_race(fresh_mock_postal):
    """A contact suppressed AFTER materialization but BEFORE send must NOT be
    sent — proves the second enforcement point."""
    ids = make_campaign(["late@acme.com"])

    # Materialize only (don't auto-send): build the pending recipient row, then
    # suppress, then run send_one directly.
    with sync_session() as s:
        from app.models.campaign import Campaign

        camp = s.get(Campaign, ids["campaign_id"])
        s.add(
            CampaignRecipient(
                campaign_id=camp.id,
                email_snapshot="late@acme.com",
                merge_snapshot={"first_name": "L"},
                status=RecipientStatus.PENDING.value,
            )
        )
        s.flush()
        rid = s.scalar(select(CampaignRecipient.id))

    with sync_session() as s:
        suppress_sync(s, "late@acme.com", SuppressionReason.MANUAL)

    send_one(rid)

    assert _recipient("late@acme.com").status == RecipientStatus.SKIPPED_SUPPRESSED.value
    assert fresh_mock_postal.sent == []  # nothing was actually sent


def test_suppression_is_idempotent():
    with sync_session() as s:
        assert suppress_sync(s, "x@acme.com", SuppressionReason.HARD_BOUNCE) is True
    with sync_session() as s:
        assert suppress_sync(s, "x@acme.com", SuppressionReason.HARD_BOUNCE) is False
        count = s.scalar(select(Suppression.id).where(Suppression.email == "x@acme.com"))
        assert count is not None
