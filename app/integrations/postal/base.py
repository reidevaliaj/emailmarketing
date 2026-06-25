"""Postal client contract and shared types.

The web app talks to Postal ONLY through this interface (HTTP API for sending,
webhooks for events). Postal owns SMTP delivery, DKIM, IP pools, suppression at
the MTA level, and bounce capture — we never reimplement those.

Two implementations exist:
  * ``PostalHTTPClient`` (client.py)  — real HTTP calls.
  * ``MockPostalClient``  (mock.py)   — deterministic, no network; lets the whole
                                         app be built and tested without Postal.

The crucial distinction the pipeline relies on: a failed *API call* to Postal is
NOT a bounce. Transient API problems are retried; permanent ones mark the
recipient ``failed``. Bounces arrive asynchronously via webhooks (Section 7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class SendMessage:
    """One outbound message (we send one recipient per call)."""

    to: str
    from_email: str
    from_name: str
    subject: str
    plain_body: str | None = None
    html_body: str | None = None
    reply_to: str | None = None
    # Extra headers, e.g. List-Unsubscribe / List-Unsubscribe-Post.
    headers: dict[str, str] = field(default_factory=dict)
    # Postal "tag" — we use it to label the IP pool / campaign for visibility.
    tag: str | None = None

    @property
    def from_full(self) -> str:
        return f"{self.from_name} <{self.from_email}>" if self.from_name else self.from_email


@dataclass(slots=True)
class SendResult:
    """Outcome of a successful Postal API submission (accepted for delivery)."""

    # Postal message *token* — the stable id carried by delivery/bounce webhooks.
    # Stored on campaign_recipients.postal_message_id for reconciliation.
    message_token: str
    message_id: str | None = None
    raw: dict | None = None


class PostalError(Exception):
    """Base class for Postal integration errors."""


class PostalTransientError(PostalError):
    """Temporary failure (network, timeout, 5xx, throttle). Safe to RETRY."""


class PostalPermanentError(PostalError):
    """Permanent failure (invalid address, malformed request). Do NOT retry —
    mark the recipient ``failed``. This is distinct from a bounce."""


@runtime_checkable
class PostalClient(Protocol):
    def send_message(self, message: SendMessage) -> SendResult:
        """Submit one message to Postal for delivery.

        Returns a ``SendResult`` on acceptance. Raises ``PostalTransientError``
        for retryable problems or ``PostalPermanentError`` for terminal ones.
        """
        ...
