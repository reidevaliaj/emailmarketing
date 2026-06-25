"""Declarative base and shared column types.

The models are written to run on BOTH PostgreSQL (production) and SQLite
(tests / quick local runs) so the critical-path test suite needs no live DB.

* `JSONType` maps to native ``JSONB`` on PostgreSQL and to generic ``JSON`` on
  SQLite, so JSONB indexing benefits are preserved in production while tests
  stay infrastructure-free.
* Status fields are stored as plain strings validated by Python enums in the
  service layer (see ``app/models/enums.py``). This keeps Alembic migrations
  trivial and portable instead of juggling native PG enum types.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


# JSONB on Postgres, JSON elsewhere (SQLite for tests).
JSONType = JSON().with_variant(JSONB, "postgresql")


class TZDateTime(TypeDecorator):
    """Always store/return timezone-aware UTC datetimes.

    SQLite drops tzinfo, so we normalise on the way in and re-attach UTC on the
    way out. PostgreSQL keeps tz natively but we still normalise to UTC for a
    single, predictable representation everywhere.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    """Timezone-aware current UTC timestamp (all storage is UTC)."""
    return datetime.now(timezone.utc)


def pk_column():
    """Standard integer primary key."""
    from sqlalchemy import Integer

    return mapped_column(Integer, primary_key=True, autoincrement=True)
