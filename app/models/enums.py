"""String-valued enums for status columns.

Stored as plain strings in the DB (portable, migration-friendly) but defined
here so the service layer validates against a single source of truth.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class ListVerificationStatus(StrEnum):
    PENDING = "pending_verification"
    VERIFYING = "verifying"
    READY = "ready"
    FAILED = "failed"


class ContactStatus(StrEnum):
    ACTIVE = "active"
    BOUNCED = "bounced"
    UNSUBSCRIBED = "unsubscribed"
    COMPLAINED = "complained"


class VerificationResult(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"
    PENDING = "pending"


class SuppressionReason(StrEnum):
    HARD_BOUNCE = "hard_bounce"
    COMPLAINT = "complaint"
    UNSUBSCRIBE = "unsubscribe"
    MANUAL = "manual"
    INVALID = "invalid"          # failed pre-send verification (no MX / bad syntax)


class TemplateType(StrEnum):
    PLAIN = "plain"
    HTML = "html"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    SENDING = "sending"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RecipientStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    FAILED = "failed"
    SKIPPED_SUPPRESSED = "skipped_suppressed"


# Terminal recipient states — a campaign is complete when every recipient is in
# one of these. Used by the pipeline to decide completion and by resume to find
# remaining work.
TERMINAL_RECIPIENT_STATUSES = {
    RecipientStatus.SENT,
    RecipientStatus.DELIVERED,
    RecipientStatus.BOUNCED,
    RecipientStatus.FAILED,
    RecipientStatus.SKIPPED_SUPPRESSED,
}
