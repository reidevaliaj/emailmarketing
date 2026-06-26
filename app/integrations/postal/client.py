"""Real Postal HTTP API client.

We submit a **raw** RFC822 message to Postal's ``/api/v1/send/raw`` endpoint
rather than ``/send/message``. Postal's message-building API always wraps the
body in ``multipart/mixed`` (it uses ``mail.part`` internally), which makes many
mail clients show a phantom paperclip/attachment even when there is none. By
building the MIME ourselves we send a clean ``text/plain`` (or
``multipart/alternative``) message. Postal still DKIM-signs at delivery and
manages the return-path, so deliverability is unchanged.

Failures are classified so the pipeline can retry transient ones and permanently
fail terminal ones (Section 6.4). Network/timeout/5xx => transient. Postal
validation errors => permanent.
"""

from __future__ import annotations

import base64
import email.policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

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


# SMTP policy => CRLF line endings (Postal splits the raw on CRLFCRLF) and a
# high max_line_length so the long List-Unsubscribe URL stays a literal
# <https://...> rather than getting RFC2047-encoded (which breaks one-click
# unsubscribe in Gmail/Yahoo).
_MAIL_POLICY = email.policy.SMTP.clone(max_line_length=900)


def build_raw_message(message: SendMessage) -> bytes:
    """Build a clean RFC822 message (no multipart/mixed wrapper)."""
    msg = EmailMessage(policy=_MAIL_POLICY)
    msg["From"] = message.from_full
    msg["To"] = message.to
    msg["Subject"] = message.subject
    if message.reply_to:
        msg["Reply-To"] = message.reply_to
    msg["Date"] = formatdate(localtime=False)
    domain = message.from_email.rsplit("@", 1)[-1]
    msg["Message-ID"] = make_msgid(domain=domain)
    for key, value in (message.headers or {}).items():
        msg[key] = value

    if message.html_body is not None:
        if message.plain_body:
            # Proper multipart/alternative (plain + html), no mixed wrapper.
            msg.set_content(message.plain_body)
            msg.add_alternative(message.html_body, subtype="html")
        else:
            # Single text/html part — clean, no phantom attachment.
            msg.set_content(message.html_body, subtype="html")
    else:
        # Single text/plain part.
        msg.set_content(message.plain_body or "")
    return msg.as_bytes()


class PostalHTTPClient:
    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        raw_path: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._url = (api_url or settings.postal_api_url).rstrip("/")
        self._key = api_key or settings.postal_api_key
        self._path = raw_path or settings.postal_raw_path
        self._timeout = timeout

    def send_message(self, message: SendMessage) -> SendResult:
        raw = build_raw_message(message)
        payload = {
            "rcpt_to": [message.to],
            "mail_from": message.from_email,
            "data": base64.b64encode(raw).decode("ascii"),
        }
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
            raise PostalTransientError(f"network error contacting Postal: {exc}") from exc

        if resp.status_code >= 500 or resp.status_code == 429:
            raise PostalTransientError(f"Postal returned HTTP {resp.status_code}")
        if resp.status_code in (401, 403):
            raise PostalPermanentError(f"Postal auth failed: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise PostalPermanentError(f"Postal rejected request: HTTP {resp.status_code}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise PostalTransientError("Postal returned non-JSON response") from exc

        if data.get("status") == "success":
            body = data.get("data", {})
            messages = body.get("messages", {}) or {}
            token = next((info.get("token") for info in messages.values()), None)
            if not token:
                raise PostalTransientError("Postal success without a message token")
            logger.info("postal accepted to=%s token=%s", message.to, token)
            return SendResult(message_token=token, message_id=body.get("message_id"), raw=data)

        err = data.get("data", {}) or {}
        code = err.get("code", "Unknown")
        detail = err.get("message", "")
        if code in _PERMANENT_CODES:
            raise PostalPermanentError(f"{code}: {detail}")
        raise PostalTransientError(f"{code}: {detail}")
