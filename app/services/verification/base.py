"""Pluggable email-verification interface (Section 7b).

The default ``InHouseVerificationProvider`` implements Layer 1 (syntax) and
Layer 2 (domain/MX). A clearly-marked external-API adapter slot
(``external_stub.py``) is left for deeper Layer-4 verification later, WITHOUT
ever running mailbox probes from our sending IPs.

Outcomes per email: ``valid`` / ``invalid`` / ``unknown`` (VerificationResult).
``invalid`` is auto-excluded from sending and globally suppressed;
``valid``/``unknown`` remain sendable (the owner prefers attempting ``unknown``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.models.enums import VerificationResult


@dataclass(slots=True)
class EmailVerification:
    email: str
    result: VerificationResult
    reason: str | None = None


@runtime_checkable
class VerificationProvider(Protocol):
    name: str

    async def verify_batch(self, emails: list[str]) -> list[EmailVerification]:
        """Verify a batch of emails, returning one result per input email."""
        ...
