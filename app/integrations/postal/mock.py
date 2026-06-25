"""Mock Postal client.

Deterministic, no network. Used when ``POSTAL_USE_MOCK=true`` (default) so the
whole app can be built, run, and tested before Postal/IPs/DNS are live.

Failure paths are triggerable by recipient domain so tests can exercise the
pipeline's retry/permanent-fail logic:
    * ``*@transient.test``  -> raises PostalTransientError (retryable)
    * ``*@permanent.test``  -> raises PostalPermanentError (terminal)
Everything else is accepted and assigned a synthetic message token.
"""

from __future__ import annotations

import threading
import uuid

from app.logging import get_logger

from .base import (
    PostalPermanentError,
    PostalTransientError,
    SendMessage,
    SendResult,
)

logger = get_logger(__name__)


class MockPostalClient:
    def __init__(self) -> None:
        self.sent: list[SendMessage] = []
        self._lock = threading.Lock()

    def send_message(self, message: SendMessage) -> SendResult:
        domain = message.to.rsplit("@", 1)[-1].lower()
        if domain == "transient.test":
            raise PostalTransientError("mock transient failure")
        if domain == "permanent.test":
            raise PostalPermanentError("mock permanent failure")

        token = f"mock-{uuid.uuid4().hex}"
        with self._lock:
            self.sent.append(message)
        logger.info("MOCK postal accepted to=%s token=%s", message.to, token)
        return SendResult(
            message_token=token,
            message_id=f"{token}@mock.local",
            raw={"status": "success", "mock": True},
        )

    def reset(self) -> None:
        with self._lock:
            self.sent.clear()


# Process-wide singleton so tests can inspect what was "sent".
_mock_singleton = MockPostalClient()


def get_mock_client() -> MockPostalClient:
    return _mock_singleton
