"""List & contact management (async, UI side).

Includes the global search-and-delete tool (Section 8): find any address across
the whole system and remove + suppress it in one action — for when a contact
converts, replies asking to stop, or complains by email.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.contact_list import ContactList
from app.models.enums import SuppressionReason
from app.models.suppression import Suppression
from app.services.normalize import normalize_email, validate_syntax
from app.services.suppression import is_suppressed_async, suppress_async


async def create_list(
    session: AsyncSession,
    name: str,
    description: str | None = None,
    source_filename: str | None = None,
    created_by: int | None = None,
) -> ContactList:
    lst = ContactList(
        name=name.strip() or "Untitled list",
        description=description,
        source_filename=source_filename,
        created_by=created_by,
    )
    session.add(lst)
    await session.flush()
    return lst


async def list_lists(session: AsyncSession) -> list[ContactList]:
    return list(
        await session.scalars(select(ContactList).order_by(ContactList.created_at.desc()))
    )


async def get_list(session: AsyncSession, list_id: int) -> ContactList | None:
    return await session.get(ContactList, list_id)


async def delete_list(session: AsyncSession, list_id: int) -> bool:
    lst = await session.get(ContactList, list_id)
    if lst is None:
        return False
    await session.delete(lst)  # cascades to contacts
    return True


async def list_contacts(
    session: AsyncSession, list_id: int, offset: int = 0, limit: int = 50
) -> tuple[list[Contact], int]:
    total = await session.scalar(
        select(func.count()).select_from(Contact).where(Contact.list_id == list_id)
    )
    rows = await session.scalars(
        select(Contact)
        .where(Contact.list_id == list_id)
        .order_by(Contact.email)
        .offset(offset)
        .limit(limit)
    )
    return list(rows), int(total or 0)


@dataclass
class AddResult:
    ok: bool
    reason: str  # added | invalid | duplicate | suppressed


async def add_contact(
    session: AsyncSession,
    list_id: int,
    email: str,
    first_name: str | None = None,
    last_name: str | None = None,
) -> AddResult:
    """Manually add a contact, honouring global-unique + suppression rules."""
    check = validate_syntax(email)
    if not check.ok or not check.normalized:
        return AddResult(False, "invalid")
    norm = check.normalized
    if await is_suppressed_async(session, norm):
        return AddResult(False, "suppressed")
    exists = await session.scalar(select(Contact.id).where(Contact.email == norm))
    if exists is not None:
        return AddResult(False, "duplicate")
    session.add(
        Contact(
            email=norm, first_name=first_name or None, last_name=last_name or None,
            list_id=list_id, status="active", verification_result="pending",
        )
    )
    await session.flush()
    lst = await session.get(ContactList, list_id)
    if lst is not None:
        lst.contact_count = int(
            await session.scalar(
                select(func.count()).select_from(Contact).where(Contact.list_id == list_id)
            ) or 0
        )
    return AddResult(True, "added")


@dataclass
class ContactSearchHit:
    email: str
    contact: Contact | None
    list_name: str | None
    suppressed: bool
    suppression_reason: str | None


async def search_contacts(session: AsyncSession, query: str, limit: int = 50) -> list[ContactSearchHit]:
    """Search contacts across ALL lists by full address, partial, or domain."""
    q = (query or "").strip().lower()
    if not q:
        return []
    like = f"%{q}%"
    rows = await session.execute(
        select(Contact, ContactList.name)
        .outerjoin(ContactList, Contact.list_id == ContactList.id)
        .where(or_(Contact.email == q, Contact.email.like(like)))
        .order_by(Contact.email)
        .limit(limit)
    )
    hits: list[ContactSearchHit] = []
    for contact, list_name in rows.all():
        supp = await session.scalar(
            select(Suppression).where(Suppression.email == contact.email)
        )
        hits.append(
            ContactSearchHit(
                email=contact.email, contact=contact, list_name=list_name,
                suppressed=supp is not None,
                suppression_reason=supp.reason if supp else None,
            )
        )
    # Also surface a suppression-only hit if the exact address is suppressed but
    # has no contact row (already removed).
    if not any(h.email == q for h in hits):
        supp = await session.scalar(select(Suppression).where(Suppression.email == q))
        if supp is not None:
            hits.insert(0, ContactSearchHit(q, None, None, True, supp.reason))
    return hits


async def remove_and_suppress(session: AsyncSession, email: str) -> bool:
    """Delete the contact row (global) AND add to suppression so it can never be
    re-imported or re-sent (Section 8 search-and-delete)."""
    norm = normalize_email(email)
    if norm is None:
        return False
    await suppress_async(session, norm, SuppressionReason.MANUAL, detail="manual removal")
    await session.execute(delete(Contact).where(Contact.email == norm))
    return True


async def suppress_only(session: AsyncSession, email: str, reason: str = "manual") -> bool:
    norm = normalize_email(email)
    if norm is None:
        return False
    try:
        reason_enum = SuppressionReason(reason)
    except ValueError:
        reason_enum = SuppressionReason.MANUAL
    await suppress_async(session, norm, reason_enum, detail="manual suppression")
    return True
