"""CSV import edge cases (Section 8)."""

from __future__ import annotations

from sqlalchemy import select

from app.db import sync_session
from app.models.contact import Contact
from app.models.contact_list import ContactList
from app.models.suppression import Suppression
from app.services.csv_import import import_into_list


def _new_list() -> int:
    with sync_session() as s:
        lst = ContactList(name="L")
        s.add(lst)
        s.flush()
        return lst.id


def _write(tmp_path, text: str) -> str:
    p = tmp_path / "leads.csv"
    p.write_text(text)
    return str(p)


def test_import_applies_all_rules(tmp_path):
    list_id = _new_list()
    # Pre-existing global contact + a suppression.
    with sync_session() as s:
        s.add(Contact(email="existing@corp.com", list_id=list_id, status="active"))
        s.add(Suppression(email="blocked@corp.com", reason="manual"))

    csv = (
        "email,first_name,company\n"
        "alice@acme.com,Alice,Acme\n"
        "ALICE@acme.com,Alice,Acme\n"        # in-file dup (case-insensitive)
        "spam@gmail.com,Spam,Consumer\n"     # free provider
        "existing@corp.com,Ex,Corp\n"        # global dup
        "blocked@corp.com,Blk,Corp\n"        # suppressed
        "not-an-email,Bad,Corp\n"            # invalid syntax
        "bob@beta.io,Bob,Beta\n"
    )
    counts = import_into_list(list_id, _write(tmp_path, csv)).to_dict()

    assert counts["imported"] == 2                  # alice, bob
    assert counts["skipped_duplicate_file"] == 1    # ALICE
    assert counts["skipped_free_provider"] == 1     # gmail
    assert counts["skipped_duplicate_global"] == 1  # existing
    assert counts["skipped_suppressed"] == 1        # blocked
    assert counts["skipped_invalid"] == 1           # not-an-email

    with sync_session() as s:
        emails = set(s.scalars(select(Contact.email).where(Contact.list_id == list_id)))
        assert "alice@acme.com" in emails and "bob@beta.io" in emails
        alice = s.scalar(select(Contact).where(Contact.email == "alice@acme.com"))
        assert alice.custom_fields == {"company": "Acme"}


def test_free_filter_can_be_disabled(tmp_path):
    list_id = _new_list()
    csv = "email\nperson@gmail.com\nbiz@acme.com\n"
    counts = import_into_list(list_id, _write(tmp_path, csv), apply_free_filter=False).to_dict()
    assert counts["imported"] == 2
    assert counts["skipped_free_provider"] == 0


def test_bad_rows_do_not_fail_whole_import(tmp_path):
    list_id = _new_list()
    # A ragged row (extra commas) shouldn't abort the import.
    csv = "email,first_name\nok@acme.com,Ok\nbroken@acme.com,Bad,extra,cols,here\nfine@acme.com,Fine\n"
    counts = import_into_list(list_id, _write(tmp_path, csv)).to_dict()
    # All three emails are still valid syntactically and imported; extra cols ignored.
    assert counts["imported"] == 3
    assert counts["error_rows"] == 0


def test_headerless_single_column(tmp_path):
    list_id = _new_list()
    csv = "solo@acme.com\nsecond@acme.com\n"
    counts = import_into_list(list_id, _write(tmp_path, csv)).to_dict()
    assert counts["imported"] == 2
