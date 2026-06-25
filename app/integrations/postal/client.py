"""Real Postal HTTP API client.

Postal's send endpoint:
    POST {POSTAL_API_URL}{POSTAL_MESSAGE_PATH}        (default /api/v1/send/message)
    Header:  X-Server-API-Key: <server api key>
    Body:    JSON {to:[...], from, subject, plain_body, html_body, ...}
    200 OK:  {"status":"success","data":{"message_id":..,"messages":{addr:{id,token}}}}
    200 err: {"status":"error","data":{"code":..,"message":..}}

We classify failures so the pipeline can retry transient ones and permanently
fail terminal ones (Section 6.4). Network/timeout/5xx => transient. Postal
validation errors (bad address, missing body) => permanent.
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.logging import get_logger

from .base import (
    PostalPermanentError,
    PostalTransientError,
    SendMessage,
    SendResult,
)

logger = get_logger(__name__)

# Postal error codes that will never succeed on retry => permanent.
_PERMANENT_CODES = {
    "ValidationError",
    "NoRecipients",
    "NoContent",
    "TooManyToAddresses",
    "FromAddressMissing",
    "UnauthenticatedFromAddress",
    "AttachmentMissingName",
    "AttachmentMissingData",
}


class PostalHTTPClient:
    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        message_path: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._url = (api_url or settings.postal_api_url).rstrip("/")
        self._key = api_key or settings.postal_api_key
        self._path = message_path or settings.postal_message_path
        self._timeout = timeout

    def send_message(self, message: SendMessage) -> SendResult:
        payload: dict = {
            "to": [message.to],
            "from": message.from_full,
            "sender": message.from_email,
            "subject": message.subject,
        }
        if message.plain_body is not None:
            payload["plain_body"] = message.plain_body
        if message.html_body is not None:
            payload["html_body"] = message.html_body
        if message.reply_to:
            payload["reply_to"] = message.reply_to
        if message.headers:
            payload["headers"] = message.headers
        if message.tag:
            payload["tag"] = message.tag

        url = f"{self._url}{self._path}"
        try:
            resp = httpx.post(
                url,
                json=payload,
                headers={
                    "X-Server-API-Key": self._key,
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Network-level problem — always retryable.
            raise PostalTransientError(f"network error contacting Postal: {exc}") from exc

        # 5xx => Postal/server side, retry. 429 => throttle, retry.
        if resp.status_code >= 500 or resp.status_code == 429:
            raise PostalTransientError(f"Postal returned HTTP {resp.status_code}")
        if resp.status_code in (401, 403):
            # Auth misconfig — retrying won't help, but it's operator-fixable.
            raise PostalPermanentError(f"Postal auth failed: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise PostalPermanentError(f"Postal rejected request: HTTP {resp.status_code}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise PostalTransientError("Postal returned non-JSON response") from exc

        status = data.get("status")
        if status == "success":
            body = data.get("data", {})
            messages = body.get("messages", {}) or {}
            token = None
            for _addr, info in messages.items():
                token = info.get("token")
                break
            message_id = body.get("message_id")
            if not token:
                # Accepted but no token — treat as transient so we re-check.
                raise PostalTransientError("Postal success without a message token")
            logger.info("postal accepted to=%s token=%s", message.to, token)
            return SendResult(message_token=token, message_id=message_id, raw=data)

        # status == "error"
        err = data.get("data", {}) or {}
        code = err.get("code", "Unknown")
        detail = err.get("message", "")
        if code in _PERMANENT_CODES:
            raise PostalPermanentError(f"{code}: {detail}")
        # Unknown / rate-limit style errors => transient.
        raise PostalTransientError(f"{code}: {detail}")
