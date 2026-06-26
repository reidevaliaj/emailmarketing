"""Authentication: argon2 password hashing + user operations.

Hashing functions are pure (used by both the async app and the sync seed
script). DB operations are async for the FastAPI side.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.user import User

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (Argon2Error, ValueError):
        return False


def normalize_login(email: str) -> str:
    return email.strip().lower()


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    user = await session.scalar(select(User).where(User.email == normalize_login(email)))
    if user is None or not user.is_active:
        return None
    if not verify_password(user.password_hash, password):
        return None
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def create_user(
    session: AsyncSession,
    email: str,
    password: str,
    role: UserRole = UserRole.USER,
    must_change_password: bool = False,
) -> User:
    user = User(
        email=normalize_login(email),
        password_hash=hash_password(password),
        role=str(role),
        must_change_password=must_change_password,
    )
    session.add(user)
    await session.flush()
    return user


async def set_password(session: AsyncSession, user: User, new_password: str) -> None:
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    await session.flush()
