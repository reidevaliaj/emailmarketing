"""Verification provider factory."""

from __future__ import annotations

from app.config import settings

from .base import EmailVerification, VerificationProvider

__all__ = ["EmailVerification", "VerificationProvider", "get_verification_provider"]


def get_verification_provider(redis=None) -> VerificationProvider:
    if settings.verify_provider == "external":
        from .external_stub import ExternalVerificationProvider

        return ExternalVerificationProvider()
    from .inhouse import InHouseVerificationProvider

    return InHouseVerificationProvider(redis=redis)
