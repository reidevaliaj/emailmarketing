"""Webhook reconciliation: any-bounce -> global suppress, idempotent dedup."""

from __future__ import annotations

from sqlalchemy import func, select

from app.db import sync_session
from app.integrations.postal.webhook import parse_event
from app.models.campaign import Campaign, CampaignRecipient, EmailEvent
from app.models.contact import Contact
from app.models.enums import RecipientStatus
from app.models.suppression import Suppression
from app.services.bounce import apply_event
from app.tasks.sending import materialize_campaign
from tests.factories import make_campaign


def _token(email):
    with sync_session() as s:
        return s.scalar(
            select(CampaignRecipient.postal_message_id).where(
                CampaignRecipient.email_snapshot == email
            )
        )


def _event(event, token, to, uuid, status=None, output=None):
    return {
        "event": event, "uuid": uuid, "timestamp": 1700000000.0,
        "payload": {"message": {"token": token, "to": to}, "status": status, "output": output},
    }


def test_delivery_marks_delivered():
    ids = make_campaign(["a@acme.com"])
    materialize_campaign(ids["campaign_id"])
    with sync_session() as s:
        apply_event(s, parse_event(_event("MessageDelivered", _token("a@acme.com"), "a@acme.com", "u1")))
    with sync_session() as s:
        r = s.scalar(select(CampaignRecipient).where(CampaignRecipient.email_snapshot == "a@acme.com"))
        assert r.status == RecipientStatus.DELIVERED.value
        assert s.get(Campaign, ids["campaign_id"]).delivered_count == 1


def test_any_bounce_suppresses_globally():
    ids = make_campaign(["bounce@acme.com"])
    materialize_campaign(ids["campaign_id"])
    tok = _token("bounce@acme.com")

    with sync_session() as s:
        outcome = apply_event(
            s, parse_event(_event("MessageDeliveryFailed", tok, "bounce@acme.com", "u2", "HardFail", "550 no user"))
        )
    assert outcome == "bounced"

    with sync_session() as s:
        # recipient bounced, contact bounced, email globally suppressed
        r = s.scalar(select(CampaignRecipient).where(CampaignRecipient.email_snapshot == "bounce@acme.com"))
        assert r.status == RecipientStatus.BOUNCED.value
        assert s.scalar(select(Suppression).where(Suppression.email == "bounce@acme.com")) is not None
        assert s.scalar(select(Contact).where(Contact.email == "bounce@acme.com")).status == "bounced"
        assert s.get(Campaign, ids["campaign_id"]).bounced_count == 1


def test_complaint_suppresses():
    ids = make_campaign(["spam@acme.com"])
    materialize_campaign(ids["campaign_id"])
    with sync_session() as s:
        out = apply_event(s, parse_event(_event("MessageSpamReport", _token("spam@acme.com"), "spam@acme.com", "u3")))
    assert out == "complaint"
    with sync_session() as s:
        assert s.scalar(select(Suppression).where(Suppression.email == "spam@acme.com")) is not None


def test_duplicate_event_is_deduped():
    ids = make_campaign(["dup@acme.com"])
    materialize_campaign(ids["campaign_id"])
    tok = _token("dup@acme.com")
    ev = _event("MessageDeliveryFailed", tok, "dup@acme.com", "same-uuid", "HardFail", "550")

    with sync_session() as s:
        assert apply_event(s, parse_event(ev)) == "bounced"
    with sync_session() as s:
        assert apply_event(s, parse_event(ev)) == "duplicate"

    with sync_session() as s:
        assert s.scalar(select(func.count()).select_from(EmailEvent)) == 1
        assert s.get(Campaign, ids["campaign_id"]).bounced_count == 1  # not double-counted


def test_unmatched_failure_still_suppresses():
    """A bounce we can't tie to a ledger row still suppresses the address."""
    with sync_session() as s:
        out = apply_event(
            s, parse_event(_event("MessageDeliveryFailed", "unknown-token", "ghost@acme.com", "u9", "HardFail"))
        )
    assert out == "unmatched"
    with sync_session() as s:
        assert s.scalar(select(Suppression).where(Suppression.email == "ghost@acme.com")) is not None
