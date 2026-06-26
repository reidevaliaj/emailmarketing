"""Idempotent sending: a retry or duplicate enqueue never double-sends."""

from __future__ import annotations

from sqlalchemy import select

from app.db import sync_session
from app.models.campaign import Campaign, CampaignRecipient
from app.models.enums import RecipientStatus
from app.tasks.sending import materialize_campaign, send_one
from tests.factories import make_campaign


def _rid(email):
    with sync_session() as s:
        return s.scalar(
            select(CampaignRecipient.id).where(CampaignRecipient.email_snapshot == email)
        )


def test_send_once_then_idempotent(fresh_mock_postal):
    ids = make_campaign(["a@acme.com"])
    materialize_campaign(ids["campaign_id"])  # sends via eager send_one

    rid = _rid("a@acme.com")
    assert _status(rid) == RecipientStatus.SENT.value
    assert len(fresh_mock_postal.sent) == 1

    # Re-running the same recipient must NOT send again.
    result = send_one(rid)
    assert result == "already-processed"
    assert len(fresh_mock_postal.sent) == 1

    with sync_session() as s:
        camp = s.get(Campaign, ids["campaign_id"])
        assert camp.sent_count == 1  # counter not double-incremented
        assert camp.status == "completed"


def test_permanent_failure_marks_failed_not_bounced(fresh_mock_postal):
    ids = make_campaign(["nope@permanent.test"])
    materialize_campaign(ids["campaign_id"])

    rid = _rid("nope@permanent.test")
    assert _status(rid) == RecipientStatus.FAILED.value
    assert fresh_mock_postal.sent == []
    with sync_session() as s:
        camp = s.get(Campaign, ids["campaign_id"])
        assert camp.failed_count == 1
        assert camp.bounced_count == 0  # API failure is NOT a bounce


def test_paused_campaign_does_not_send(fresh_mock_postal):
    ids = make_campaign(["b@acme.com"], status="paused")
    # Manually create a pending recipient and try to send while paused.
    with sync_session() as s:
        s.add(
            CampaignRecipient(
                campaign_id=ids["campaign_id"], email_snapshot="b@acme.com",
                merge_snapshot={"first_name": "B"}, status=RecipientStatus.PENDING.value,
            )
        )
        s.flush()
    rid = _rid("b@acme.com")
    assert send_one(rid) == "paused"
    assert _status(rid) == RecipientStatus.PENDING.value  # left for resume
    assert fresh_mock_postal.sent == []


def _status(rid):
    with sync_session() as s:
        return s.get(CampaignRecipient, rid).status
