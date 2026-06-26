"""Postal webhook endpoint (Section 7).

Verifies the shared secret on EVERY request and ignores (401) anything that
fails verification. Verified payloads are handed to a Celery task so the handler
returns 200 immediately and reconciliation/dedup happens in one place.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response

from app.config import settings
from app.integrations.postal.webhook import verify_webhook
from app.logging import get_logger
from app.tasks.webhooks import process_webhook

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/postal")
async def postal_webhook(request: Request):
    raw = await request.body()
    if not verify_webhook(
        raw,
        settings.postal_webhook_shared_secret,
        signature_header=request.headers.get("X-Postal-Signature"),
        secret_header=request.headers.get("X-Postal-Secret"),
        query_token=request.query_params.get("token"),
    ):
        logger.warning("rejected unverified Postal webhook from %s", request.client.host if request.client else "?")
        return Response(status_code=401)

    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return Response(status_code=400)

    process_webhook.delay(body)
    return {"status": "accepted"}
