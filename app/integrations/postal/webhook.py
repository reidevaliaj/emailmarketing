"""Postal webhook verification and event normalization.

VERIFICATION (Section 7 / Section 9): every inbound webhook must be verified
against ``POSTAL_WEBHOOK_SHARED_SECRET``; unverified calls are ignored.

Postal's *native* webhook signing is RSA (header ``X-Postal-Signature``, verified
with Postal's public key). Because the brief provisions a shared secret rather
than a public key, we verify the shared secret. Supported (any one passes):

  1. HMAC header  — ``X-Postal-Signature: <hex hmac-sha256(body, secret)>``
                    (use if you front Postal with an HMAC-signing proxy)
  2. Secret header — ``X-Postal-Secret: <secret>``
  3. URL token     — ``...?token=<secret>``  (simplest with stock Postal:
                     configure the webhook URL with the token query param)

A clearly-marked seam (``verify_rsa_signature``) is left for swapping in real
RSA verification with Postal's public key later, without touching the endpoint.

NORMALIZATION: Postal emits several event shapes; ``parse_event`` flattens them
into a single ``PostalWebhookEvent`` the reconciliation service consumes.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone

# Event types that indicate the message was successfully delivered.
DELIVERY_EVENTS = {"MessageSent", "MessageDelivered"}

# Event types that indicate a delivery failure / bounce.
FAILURE_EVENTS = {"MessageDeliveryFailed", "MessageBounced", "MessageHeld"}

# Event types that indicate a spam complaint / abuse report.
COMPLAINT_EVENTS = {"MessageSpamReport", "MessageComplaint"}


@dataclass(slots=True)
class PostalWebhookEvent:
    event_type: str
    event_uuid: str | None          # Postal "uuid" — dedup key for idempotency
    message_token: str | None       # matches campaign_recipients.postal_message_id
    recipient_email: str | None
    status: str | None              # e.g. HardFail / SoftFail / Sent / Bounced
    detail: str | None              # human-readable output / details
    occurred_at: datetime
    raw: dict

    @property
    def is_delivery(self) -> bool:
        return self.event_type in DELIVERY_EVENTS

    @property
    def is_failure(self) -> bool:
        return self.event_type in FAILURE_EVENTS

    @property
    def is_complaint(self) -> bool:
        return self.event_type in COMPLAINT_EVENTS


def verify_webhook(
    raw_body: bytes,
    secret: str,
    *,
    signature_header: str | None = None,
    secret_header: str | None = None,
    query_token: str | None = None,
) -> bool:
    """Return True if any supported shared-secret check passes (constant-time)."""
    if not secret:
        return False

    if signature_header:
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature_header.strip()):
            return True

    if secret_header and hmac.compare_digest(secret_header.strip(), secret):
        return True

    if query_token and hmac.compare_digest(query_token.strip(), secret):
        return True

    return False


def verify_rsa_signature(raw_body: bytes, signature_header: str, public_key_pem: str) -> bool:
    """SEAM: native Postal RSA-SHA256 signature verification.

    Not implemented now (we use the shared secret above). A future maintainer can
    fetch Postal's public key and verify ``signature_header`` here without
    changing the webhook endpoint. Left intentionally unimplemented.
    """
    raise NotImplementedError("RSA signature verification not enabled; using shared secret")


def _first_token(*candidates: dict | None) -> str | None:
    for c in candidates:
        if isinstance(c, dict):
            tok = c.get("token")
            if tok:
                return str(tok)
    return None


def _first_to(*candidates: dict | None) -> str | None:
    for c in candidates:
        if isinstance(c, dict):
            to = c.get("to")
            if to:
                return str(to).strip().lower()
    return None


def parse_event(body: dict) -> PostalWebhookEvent:
    """Flatten a Postal webhook payload into a normalized event."""
    event_type = str(body.get("event", "")) or "Unknown"
    event_uuid = body.get("uuid")
    payload = body.get("payload", {}) or {}

    # Delivery/fail events carry payload.message; bounce events carry
    # payload.original_message (+ payload.bounce). Try both.
    message = payload.get("message")
    original = payload.get("original_message")

    token = _first_token(message, original)
    recipient = _first_to(message, original)
    status = payload.get("status")
    detail = payload.get("details") or payload.get("output") or payload.get("message")
    if isinstance(detail, dict):
        detail = detail.get("token") or str(detail)

    ts = body.get("timestamp") or payload.get("timestamp")
    try:
        occurred_at = (
            datetime.fromtimestamp(float(ts), tz=timezone.utc)
            if ts is not None
            else datetime.now(timezone.utc)
        )
    except (TypeError, ValueError, OSError):
        occurred_at = datetime.now(timezone.utc)

    return PostalWebhookEvent(
        event_type=event_type,
        event_uuid=str(event_uuid) if event_uuid else None,
        message_token=token,
        recipient_email=recipient,
        status=str(status) if status is not None else None,
        detail=str(detail) if detail is not None else None,
        occurred_at=occurred_at,
        raw=body,
    )
