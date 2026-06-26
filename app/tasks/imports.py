"""CSV import task — runs the streaming import, then chains verification.

Kept out of the upload HTTP request so large files don't block the UI: the
endpoint stores the file + creates the list, then enqueues this.
"""

from __future__ import annotations

import os

from app.celery_app import celery_app
from app.logging import get_logger
from app.services.csv_import import import_into_list
from app.tasks.verification import verify_list

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.imports.import_csv")
def import_csv(
    list_id: int,
    file_path: str,
    mapping: dict | None = None,
    apply_free_filter: bool | None = None,
    cleanup: bool = True,
) -> dict:
    counts = import_into_list(list_id, file_path, mapping, apply_free_filter)
    # Chain Layer 1+2 verification once contacts are stored.
    verify_list.delay(list_id)
    if cleanup:
        try:
            os.remove(file_path)
        except OSError:
            pass
    return counts.to_dict()
