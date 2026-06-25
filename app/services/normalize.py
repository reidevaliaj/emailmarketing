"""Email normalization and Layer-1 syntax validation (Section 7b).

* ``normalize_email`` — the canonical form used everywhere as the GLOBALLY
  UNIQUE key: trimmed + fully lowercased. Lowercasing the local part is
  technically beyond RFC strictness, but for B2B dedup/suppression we want
  case-insensitive identity, which is how real mail systems behave in practice.
* ``validate_syntax`` — Layer 1: fast, in-process RFC-ish shape check. No DNS.
"""

from __future__ import annotations

from dataclasses import dataclass

from email_validator import EmailNotValidError, validate_email


def normalize_email(raw: str | None) -> str | None:
    """Trim + lowercase. Returns None if empty/obviously not an address."""
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value or "@" not in value:
        return None
    return value


@dataclass(slots=True)
class SyntaxCheck:
    ok: bool
    normalized: str | None
    error: str | None = None


def validate_syntax(raw: str | None) -> SyntaxCheck:
    """Layer 1 — validate the email's shape. No deliverability/DNS lookup here."""
    value = normalize_email(raw)
    if value is None:
        return SyntaxCheck(ok=False, normalized=None, error="empty or missing @")
    try:
        # check_deliverability=False keeps this purely syntactic and fast.
        result = validate_email(value, check_deliverability=False)
    except EmailNotValidError as exc:
        return SyntaxCheck(ok=False, normalized=value, error=str(exc))
    # Re-lower the normalized form (validator lowercases domain, keeps local case).
    return SyntaxCheck(ok=True, normalized=result.normalized.lower(), error=None)


def domain_of(email: str) -> str:
    """Return the lowercased domain part of a (normalized) email."""
    return email.rsplit("@", 1)[-1].lower()
