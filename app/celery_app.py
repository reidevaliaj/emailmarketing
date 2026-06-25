"""Celery application (queue/worker + beat scheduler).

Redis is the broker and result backend. Key settings for correct, paced sending:

* ``worker_prefetch_multiplier=1`` + ``task_acks_late=True`` — fair dispatch for
  rate-limited tasks and at-least-once delivery; our DB ledger guard makes the
  per-recipient send idempotent so at-least-once never double-sends.
* Beat runs ``dispatch_due_campaigns`` every minute (Section 6.1) and a periodic
  finalize/sweep to complete campaigns and re-pump any stragglers.
"""

from __future__ import annotations

from celery import Celery

from app.config import settings
from app.logging import configure_logging

configure_logging()

celery_app = Celery(
    "emailmarketing",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.sending",
        "app.tasks.scheduler",
        "app.tasks.verification",
        "app.tasks.webhooks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=500,
    result_expires=3600,
    broker_transport_options={"visibility_timeout": 3600},
    task_default_queue="default",
    beat_schedule={
        "dispatch-due-campaigns": {
            "task": "app.tasks.scheduler.dispatch_due_campaigns",
            "schedule": 60.0,  # every minute
        },
        "finalize-campaigns": {
            "task": "app.tasks.scheduler.finalize_and_repump",
            "schedule": 120.0,  # every 2 minutes
        },
    },
)
