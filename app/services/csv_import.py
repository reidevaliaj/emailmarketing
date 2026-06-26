"""CSV import (Section 8).

Streaming, batched, and tolerant of bad rows. Applies, in order, per row:
  1. normalize + Layer-1 syntax  -> skipped_invalid
  2. in-file de-duplication      -> skipped_duplicate_file
  3. free-provider filter (B2B)  -> skipped_free_provider
  4. global-unique check         -> skipped_duplicate_global  (never duplicate a row)
  5. global suppression check    -> skipped_suppressed
Survivors are inserted as ACTIVE / pending verification.

DB work is done in batches with set-based IN lookups (2 queries per 1000 rows,
not 2 per row) and committed per batch, so hundreds of thousands of rows stream
without a giant transaction or loading everything into memory.
"""

from __future__ import annotations

import csv
import itertools
from dataclasses import dataclass, field

from sqlalchemy import func, select

from app.db import sync_session
from app.logging import get_logger
from app.models.contact import Contact
from app.models.contact_list import ContactList
from app.models.suppression import Suppression
from app.services.config_store import get_free_provider_config_sync, is_free_provider
from app.services.normalize import domain_of, validate_syntax

logger = get_logger(__name__)

_BATCH = 1000

_EMAIL_HEADERS = {"email", "e-mail", "email address", "emailaddress", "email_address", "mail"}
_FIRST_HEADERS = {"first_name", "first name", "firstname", "first", "fname", "given_name", "given name"}
_LAST_HEADERS = {"last_name", "last name", "lastname", "last", "lname", "surname", "family_name", "family name"}


@dataclass
class ImportCounts:
    total_rows: int = 0
    imported: int = 0
    skipped_invalid: int = 0
    skipped_duplicate_file: int = 0
    skipped_duplicate_global: int = 0
    skipped_suppressed: int = 0
    skipped_free_provider: int = 0
    error_rows: int = 0
    error_samples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_rows": self.total_rows,
            "imported": self.imported,
            "skipped_invalid": self.skipped_invalid,
            "skipped_duplicate": self.skipped_duplicate_file + self.skipped_duplicate_global,
            "skipped_duplicate_file": self.skipped_duplicate_file,
            "skipped_duplicate_global": self.skipped_duplicate_global,
            "skipped_suppressed": self.skipped_suppressed,
            "skipped_free_provider": self.skipped_free_provider,
            "error_rows": self.error_rows,
            "error_samples": self.error_samples,
        }


def detect_columns(header: list[str]) -> dict:
    """Map email/first_name/last_name from a header row (case-insensitive)."""
    mapping: dict = {"email": None, "first_name": None, "last_name": None}
    for name in header:
        low = (name or "").strip().lower()
        if mapping["email"] is None and low in _EMAIL_HEADERS:
            mapping["email"] = name
        elif mapping["first_name"] is None and low in _FIRST_HEADERS:
            mapping["first_name"] = name
        elif mapping["last_name"] is None and low in _LAST_HEADERS:
            mapping["last_name"] = name
    if mapping["email"] is None and len(header) == 1:
        mapping["email"] = header[0]
    return mapping


def _resolve_header(first_row: list[str], override: dict | None):
    """Return (has_header, colnames, mapping). Falls back to headerless detection."""
    has_header = not any("@" in (c or "") for c in first_row)
    if has_header:
        colnames = [c.strip() for c in first_row]
        mapping = detect_columns(colnames)
        if override:
            for k in ("email", "first_name", "last_name"):
                if override.get(k):
                    mapping[k] = override[k]
        return True, colnames, mapping

    # Headerless: synthesize names, pick the email column by '@' presence.
    colnames = [f"column_{i}" for i in range(len(first_row))]
    email_col = next(
        (colnames[i] for i, c in enumerate(first_row) if "@" in (c or "")), colnames[0]
    )
    return False, colnames, {"email": email_col, "first_name": None, "last_name": None}


def _flush_batch(list_id: int, pending: list[dict], counts: ImportCounts) -> None:
    """Insert a batch, applying global-unique + suppression set lookups."""
    if not pending:
        return
    emails = [p["email"] for p in pending]
    with sync_session() as session:
        existing = set(
            session.scalars(select(Contact.email).where(Contact.email.in_(emails))).all()
        )
        suppressed = set(
            session.scalars(select(Suppression.email).where(Suppression.email.in_(emails))).all()
        )
        for p in pending:
            email = p["email"]
            if email in suppressed:
                counts.skipped_suppressed += 1
                continue
            if email in existing:
                counts.skipped_duplicate_global += 1  # never create a duplicate row
                continue
            session.add(
                Contact(
                    email=email,
                    first_name=p["first_name"],
                    last_name=p["last_name"],
                    custom_fields=p["custom"],
                    list_id=list_id,
                    status="active",
                    verification_result="pending",
                )
            )
            existing.add(email)
            counts.imported += 1


def import_into_list(
    list_id: int,
    file_path: str,
    mapping: dict | None = None,
    apply_free_filter: bool | None = None,
) -> ImportCounts:
    counts = ImportCounts()

    with sync_session() as session:
        enabled, patterns = get_free_provider_config_sync(session)
    apply_free = enabled if apply_free_filter is None else apply_free_filter

    seen: set[str] = set()
    pending: list[dict] = []

    with open(file_path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.reader(fh)
        try:
            first_row = next(reader)
        except StopIteration:
            first_row = None
        if first_row is None:
            _finalize(list_id, counts, apply_free)
            return counts

        has_header, colnames, colmap = _resolve_header(first_row, mapping)
        email_key = colmap["email"]
        mapped_keys = {v for v in colmap.values() if v}
        # If headerless, the first row was data — include it.
        rows = reader if has_header else itertools.chain([first_row], reader)

        for raw in rows:
            counts.total_rows += 1
            try:
                row = dict(zip(colnames, raw))
                check = validate_syntax(row.get(email_key))
                if not check.ok or not check.normalized:
                    counts.skipped_invalid += 1
                    continue
                email = check.normalized
                if email in seen:
                    counts.skipped_duplicate_file += 1
                    continue
                seen.add(email)
                if apply_free and is_free_provider(domain_of(email), patterns):
                    counts.skipped_free_provider += 1
                    continue
                custom = {
                    k: v for k, v in row.items()
                    if k not in mapped_keys and v not in (None, "")
                }
                pending.append(
                    {
                        "email": email,
                        "first_name": (row.get(colmap["first_name"]) or None) if colmap["first_name"] else None,
                        "last_name": (row.get(colmap["last_name"]) or None) if colmap["last_name"] else None,
                        "custom": custom,
                    }
                )
                if len(pending) >= _BATCH:
                    _flush_batch(list_id, pending, counts)
                    pending = []
            except Exception as exc:  # noqa: BLE001 - never fail whole upload on one row
                counts.error_rows += 1
                if len(counts.error_samples) < 10:
                    counts.error_samples.append(f"row {counts.total_rows}: {exc}")

        _flush_batch(list_id, pending, counts)

    _finalize(list_id, counts, apply_free)
    logger.info("imported list %s: %s", list_id, counts.to_dict())
    return counts


def _finalize(list_id: int, counts: ImportCounts, apply_free: bool) -> None:
    with sync_session() as session:
        lst = session.get(ContactList, list_id)
        if lst is None:
            return
        lst.contact_count = (
            session.scalar(
                select(func.count()).select_from(Contact).where(Contact.list_id == list_id)
            )
            or 0
        )
        lst.import_summary = counts.to_dict()
        lst.free_provider_filter_applied = apply_free
