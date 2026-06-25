"""Global suppression service — THE core correctness rule.

No campaign may ever send to an email present in ``suppressions``. Suppression
is global and permanent (one contact row per email by construction).

Enforced at TWO points (Section 9): (1) at recipient materialization, and
(2) again at per-job send time. Both call into this module.

Functions come in sync (Celery tasks) and async (FastAPI) flavours because the
app honours the brief's "FastAPI async + SQLAlchemy async" while Celery workers
use a plain blocking session. The SQL is identical; only the await differs.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.contact import Contact
from app.models.enums import ContactStatus, SuppressionReason
from app.models.suppression import Suppression
from app.services.normalize import normalize_email

# Map a suppression reason to the contact status it implies.
_REASON_TO_CONTACT_STATUS = {
    SuppressionReason.HARD_BOUNCE: ContactStatus.BOUNCED,
    SuppressionReason.COMPLAINT: ContactStatus.COMPLAINED,
    SuppressionReason.UNSUBSCRIBE: ContactStatus.UNSUBSCRIBED,
    SuppressionReason.INVALID: ContactStatus.BOUNCED,
    SuppressionReason.MANUAL: ContactStatus.UNSUBSCRIBED,
}


# --- Sync (Celery tasks) ---------------------------------------------------

def is_suppressed_sync(session: Session, email: str) -> bool:
    norm = normalize_email(email)
    if norm is None:
        return True  # un-normalizable => treat as un-sendable
    return session.scalar(select(Suppression.id).where(Suppression.email == norm)) is not None


def suppress_sync(
    session: Session,
    email: str,
    reason: SuppressionReason | str,
    detail: str | None = None,
    *,
    update_contact: bool = True,
) -> bool:
    """Insert into global suppression (idempotent). Returns True if newly added.

    Also flips the matching contact's status when ``update_contact`` is set.
    """
    norm = normalize_email(email)
    if norm is None:
        return False
    reason = SuppressionReason(reason) if not isinstance(reason, SuppressionReason) else reason

    newly_added = False
    if session.scalar(select(Suppression.id).where(Suppression.email == norm)) is None:
        session.add(Suppression(email=norm, reason=str(reason), detail=detail))
        try:
            session.flush()
            newly_added = True
        except IntegrityError:
            session.rollback()  # lost a race; already suppressed

    if update_contact:
        contact = session.scalar(select(Contact).where(Contact.email == norm))
        if contact is not None:
            contact.status = str(_REASON_TO_CONTACT_STATUS.get(reason, ContactStatus.UNSUBSCRIBED))
            session.flush()
    return newly_added


# --- Async (FastAPI) -------------------------------------------------------

async def is_suppressed_async(session: AsyncSession, email: str) -> bool:
    norm = normalize_email(email)
    if norm is None:
        return True
    return (
        await session.scalar(select(Suppression.id).where(Suppression.email == norm))
    ) is not None


async def suppress_async(
    session: AsyncSession,
    email: str,
    reason: SuppressionReason | str,
    detail: str | None = None,
    *,
    update_contact: bool = True,
) -> bool:
    norm = normalize_email(email)
    if norm is None:
        return False
    reason = SuppressionReason(reason) if not isinstance(reason, SuppressionReason) else reason

    newly_added = False
    if (await session.scalar(select(Suppression.id).where(Suppression.email == norm))) is None:
        session.add(Suppression(email=norm, reason=str(reason), detail=detail))
        try:
            await session.flush()
            newly_added = True
        except IntegrityError:
            await session.rollback()

    if update_contact:
        contact = await session.scalar(select(Contact).where(Contact.email == norm))
        if contact is not None:
            contact.status = str(_REASON_TO_CONTACT_STATUS.get(reason, ContactStatus.UNSUBSCRIBED))
            await session.flush()
    return newly_added
