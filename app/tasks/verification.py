"""Background list verification task (Section 7b).

Runs OUTSIDE the upload request: upload stores contacts as pending_verification
and enqueues this task. It runs Layers 1-2, writes per-contact results, auto-
suppresses invalids, records a progress/summary on the list, and flips the list
to ``ready``. Progress is written to ``verification_summary`` so the UI can poll.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.celery_app import celery_app
from app.db import sync_session
from app.logging import get_logger
from app.models.contact import Contact
from app.models.contact_list import ContactList
from app.models.enums import (
    ListVerificationStatus,
    SuppressionReason,
    VerificationResult,
)
from app.services.normalize import domain_of
from app.services.rate_limit import get_redis
from app.services.suppression import suppress_sync
from app.services.verification import get_verification_provider

logger = get_logger(__name__)

_BATCH = 1000


@celery_app.task(name="app.tasks.verification.verify_list")
def verify_list(list_id: int) -> dict:
    redis = get_redis()
    provider = get_verification_provider(redis=redis)

    with sync_session() as session:
        lst = session.get(ContactList, list_id)
        if lst is None:
            return {"error": "list not found"}
        rows = session.execute(
            select(Contact.id, Contact.email).where(Contact.list_id == list_id)
        ).all()
        total = len(rows)
        lst.verification_status = ListVerificationStatus.VERIFYING.value
        lst.verification_summary = {
            "total": total, "processed": 0, "valid": 0, "invalid": 0, "unknown": 0,
        }

    counts = {"valid": 0, "invalid": 0, "unknown": 0}
    invalid_domains: set[str] = set()

    try:
        for start in range(0, total, _BATCH):
            batch = rows[start : start + _BATCH]
            emails = [email for _cid, email in batch]
            # DNS-heavy, pure I/O — run the concurrent gather in its own loop.
            results = asyncio.run(provider.verify_batch(emails))
            resmap = {r.email: r for r in results}

            with sync_session() as session:
                for cid, email in batch:
                    r = resmap.get(email)
                    if r is None:
                        continue
                    contact = session.get(Contact, cid)
                    if contact is None:
                        continue
                    contact.verification_result = r.result.value
                    counts[r.result.value] += 1
                    if r.result == VerificationResult.INVALID:
                        # Auto-exclude + globally suppress (Section 7b).
                        suppress_sync(
                            session, email, SuppressionReason.INVALID,
                            detail=f"verification:{r.reason}", update_contact=True,
                        )
                        if r.reason and not r.reason.startswith("syntax"):
                            invalid_domains.add(domain_of(email))

                lst = session.get(ContactList, list_id)
                if lst is not None:
                    lst.verification_summary = {
                        "total": total,
                        "processed": min(start + _BATCH, total),
                        **counts,
                    }

        with sync_session() as session:
            lst = session.get(ContactList, list_id)
            if lst is not None:
                lst.verification_summary = {
                    "total": total, "processed": total, **counts,
                    "failed_domains": sorted(invalid_domains)[:200],
                }
                lst.verification_status = ListVerificationStatus.READY.value
        logger.info("verified list %s: %s", list_id, counts)
        return {"list_id": list_id, **counts, "total": total}

    except Exception as exc:  # noqa: BLE001 - surface failure on the list
        logger.exception("verification failed for list %s", list_id)
        with sync_session() as session:
            lst = session.get(ContactList, list_id)
            if lst is not None:
                lst.verification_status = ListVerificationStatus.FAILED.value
                summary = dict(lst.verification_summary or {})
                summary["error"] = str(exc)
                lst.verification_summary = summary
        return {"error": str(exc)}
