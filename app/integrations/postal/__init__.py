"""Postal integration package.

``get_postal_client()`` returns the mock or the real HTTP client based on
``POSTAL_USE_MOCK``. The rest of the app depends only on the ``PostalClient``
protocol, so switching to live Postal is a single env-var change.
"""

from __future__ import annotations

from app.config import settings

from .base import (
    PostalClient,
    PostalError,
    PostalPermanentError,
    PostalTransientError,
    SendMessage,
    SendResult,
)
from .webhook import PostalWebhookEvent, parse_event, verify_webhook

__all__ = [
    "PostalClient",
    "PostalError",
    "PostalTransientError",
    "PostalPermanentError",
    "SendMessage",
    "SendResult",
    "PostalWebhookEvent",
    "parse_event",
    "verify_webhook",
    "get_postal_client",
]


def get_postal_client() -> PostalClient:
    if settings.postal_use_mock:
        from .mock import get_mock_client

        return get_mock_client()

    from .client import PostalHTTPClient

    return PostalHTTPClient()
