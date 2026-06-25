"""Database engines and session factories.

Two parallel setups share the same models:

* **Async** (asyncpg) — used by FastAPI request handlers via ``get_db``.
* **Sync** (psycopg) — used by Celery workers via ``sync_session``.

Using a blocking session inside Celery tasks avoids the well-known pitfalls of
running asyncpg across short-lived per-task event loops. The volume here
(~20 emails/min) makes the simpler, sturdier sync path the right trade-off.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# --- Async (FastAPI) -------------------------------------------------------

_async_connect_args: dict = {}
if settings.is_sqlite:
    _async_connect_args = {"check_same_thread": False}

async_engine = create_async_engine(
    settings.postgres_url,
    echo=False,
    pool_pre_ping=True,
    connect_args=_async_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async session (commit/rollback by caller)."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# --- Sync (Celery workers) -------------------------------------------------

_sync_connect_args: dict = {}
if settings.sync_database_url.startswith("sqlite"):
    _sync_connect_args = {"check_same_thread": False}

sync_engine = create_engine(
    settings.sync_database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
    connect_args=_sync_connect_args,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    expire_on_commit=False,
    autoflush=False,
    class_=Session,
)


@contextmanager
def sync_session() -> Iterator[Session]:
    """Context-managed blocking session for Celery tasks."""
    session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
