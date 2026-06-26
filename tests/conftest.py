"""Test fixtures.

Critical-path tests run with NO external infrastructure:
  * SQLite (file) for the DB — the models are portable (JSONB->JSON).
  * fakeredis for the rate limiter / locks / daily cap.
  * Celery in eager mode so tasks run inline.
  * Postal mock (POSTAL_USE_MOCK=true).

Env is set BEFORE importing any app module so settings pick it up.
"""

from __future__ import annotations

import os

os.environ.update(
    {
        "POSTGRES_URL": "sqlite:////tmp/emk_test.sqlite3",
        "POSTAL_USE_MOCK": "true",
        "UNSUBSCRIBE_SECRET": "test-secret",
        "SESSION_SECRET": "test-secret",
        "POSTAL_WEBHOOK_SHARED_SECRET": "test-webhook-secret",
        # High limits so pacing never throttles the test sends.
        "RATE_GLOBAL_PER_MINUTE": "100000",
        "RATE_PER_DOMAIN_PER_MINUTE": "100000",
        "PER_IP_DAILY_CAP": "1000000",
        "SENDING_DOMAIN": "marketing.cod-st.com",
        "DEFAULT_FROM_EMAIL": "news@marketing.cod-st.com",
    }
)

import fakeredis  # noqa: E402
import pytest  # noqa: E402

import app.services.rate_limit as rate_limit  # noqa: E402
from app.celery_app import celery_app  # noqa: E402
from app.db import SyncSessionLocal, sync_engine  # noqa: E402
from app.integrations.postal.mock import get_mock_client  # noqa: E402
from app.models import Base  # noqa: E402

celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    yield
    Base.metadata.drop_all(sync_engine)


@pytest.fixture(autouse=True)
def fresh_redis():
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    rate_limit._sync_redis = fake
    yield fake
    rate_limit._sync_redis = None


@pytest.fixture(autouse=True)
def fresh_mock_postal():
    client = get_mock_client()
    client.reset()
    yield client
    client.reset()


@pytest.fixture
def db():
    """A blocking session (caller commits)."""
    session = SyncSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
