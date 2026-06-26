"""Layer 1/2 verification: classification, MX caching, and the list task."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.db import sync_session
from app.models.contact import Contact
from app.models.contact_list import ContactList
from app.models.enums import VerificationResult
from app.models.suppression import Suppression
from app.services.verification.inhouse import InHouseVerificationProvider
from app.tasks.verification import verify_list


@pytest.fixture
def mock_dns(monkeypatch):
    """Replace DNS with a deterministic map and count calls (to prove caching)."""
    calls = {"count": 0}

    async def fake_check(self, domain):
        calls["count"] += 1
        if "dead" in domain:
            return VerificationResult.INVALID, "no_mx_no_a"
        if "slow" in domain:
            return VerificationResult.UNKNOWN, "dns_timeout"
        return VerificationResult.VALID, "mx"

    monkeypatch.setattr(InHouseVerificationProvider, "_check_domain", fake_check)
    return calls


def test_layer1_syntax_and_layer2_classification(mock_dns):
    p = InHouseVerificationProvider()
    emails = ["ok@good.com", "bad@@syntax", "x@dead.com", "y@slow.com"]
    results = {r.email: r.result for r in asyncio.run(p.verify_batch(emails))}
    assert results["ok@good.com"] == VerificationResult.VALID
    assert results["bad@@syntax"] == VerificationResult.INVALID      # syntax (Layer 1)
    assert results["x@dead.com"] == VerificationResult.INVALID       # no MX (Layer 2)
    assert results["y@slow.com"] == VerificationResult.UNKNOWN       # DNS timeout


def test_mx_cache_resolves_each_domain_once(mock_dns):
    p = InHouseVerificationProvider()
    emails = ["a@good.com", "b@good.com", "c@good.com", "d@other.com"]
    asyncio.run(p.verify_batch(emails))
    assert mock_dns["count"] == 2  # two unique domains, not four lookups


def test_verify_list_task_suppresses_invalid(mock_dns):
    with sync_session() as s:
        lst = ContactList(name="L")
        s.add(lst)
        s.flush()
        for e in ["valid@good.com", "gone@dead.com", "maybe@slow.com"]:
            s.add(Contact(email=e, list_id=lst.id, status="active"))
        s.flush()
        list_id = lst.id

    verify_list(list_id)

    with sync_session() as s:
        by_email = {c.email: c for c in s.scalars(select(Contact).where(Contact.list_id == list_id))}
        assert by_email["valid@good.com"].verification_result == "valid"
        assert by_email["gone@dead.com"].verification_result == "invalid"
        assert by_email["maybe@slow.com"].verification_result == "unknown"
        # invalid auto-suppressed + excluded from sending
        assert by_email["gone@dead.com"].status == "bounced"
        assert s.scalar(select(Suppression).where(Suppression.email == "gone@dead.com")) is not None
        assert s.scalar(select(Suppression).where(Suppression.email == "valid@good.com")) is None

        lst = s.get(ContactList, list_id)
        assert lst.verification_status == "ready"
        assert lst.verification_summary["valid"] == 1
        assert lst.verification_summary["invalid"] == 1
        assert lst.verification_summary["unknown"] == 1
