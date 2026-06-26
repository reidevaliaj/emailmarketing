"""API keys for the authenticated JSON status endpoint (Section 8).

Keys are high-entropy random tokens; only their SHA-256 hash is stored. The
plaintext is shown once at creation and never again.
"""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import ApiKey
from app.models.base import utcnow

_PREFIX = "emk_"  # email-marketing key


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


async def create_api_key(session: AsyncSession, name: str) -> tuple[ApiKey, str]:
    plaintext = _PREFIX + secrets.token_urlsafe(32)
    row = ApiKey(name=name.strip() or "status key", key_hash=_hash(plaintext))
    session.add(row)
    await session.flush()
    return row, plaintext


async def list_api_keys(session: AsyncSession) -> list[ApiKey]:
    return list(await session.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())))


async def revoke_api_key(session: AsyncSession, key_id: int) -> bool:
    row = await session.get(ApiKey, key_id)
    if row is None:
        return False
    row.is_active = False
    await session.flush()
    return True


async def verify_api_key(session: AsyncSession, plaintext: str | None) -> ApiKey | None:
    if not plaintext:
        return None
    row = await session.scalar(
        select(ApiKey).where(ApiKey.key_hash == _hash(plaintext), ApiKey.is_active.is_(True))
    )
    if row is not None:
        row.last_used_at = utcnow()
        await session.flush()
    return row
