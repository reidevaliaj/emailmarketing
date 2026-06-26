"""The raw MIME builder must never produce multipart/mixed (phantom attachment)."""

from __future__ import annotations

from email import message_from_bytes

from app.integrations.postal.base import SendMessage
from app.integrations.postal.client import build_raw_message


def _ct(message: SendMessage) -> str:
    return message_from_bytes(build_raw_message(message)).get_content_type()


def test_plain_template_is_single_text_plain():
    long_url = "<https://dashboard.cod-st.com/u/ImhlbGxvQHJlaS1hbGlhai5jb20i.o7p-QiYoLmEdFEn07H9n_D0cuq4>"
    m = SendMessage(
        to="x@corp.com", from_email="news@marketing.cod-st.com", from_name="COD-ST",
        subject="Hi", plain_body="Hello there\nLine 2",
        headers={"List-Unsubscribe": long_url, "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
    )
    raw = build_raw_message(m)
    assert b"multipart/mixed" not in raw
    assert _ct(m) == "text/plain"
    # List-Unsubscribe must stay a literal URL (NOT RFC2047-encoded) or one-click breaks.
    assert f"List-Unsubscribe: {long_url}".encode() in raw
    assert b"=?utf-8?q?=3Chttps" not in raw


def test_html_only_is_single_text_html():
    m = SendMessage(
        to="x@corp.com", from_email="news@marketing.cod-st.com", from_name="X",
        subject="S", html_body="<p>Hi {{x}}</p>",
    )
    assert b"multipart/mixed" not in build_raw_message(m)
    assert _ct(m) == "text/html"


def test_plain_and_html_is_alternative_not_mixed():
    m = SendMessage(
        to="x@corp.com", from_email="news@marketing.cod-st.com", from_name="X",
        subject="S", plain_body="plain", html_body="<p>html</p>",
    )
    assert b"multipart/mixed" not in build_raw_message(m)
    assert _ct(m) == "multipart/alternative"


def test_required_headers_present():
    m = SendMessage(
        to="x@corp.com", from_email="news@marketing.cod-st.com", from_name="X",
        subject="Subj", plain_body="b",
    )
    raw = build_raw_message(m).decode()
    for header in ("From:", "To:", "Subject:", "Date:", "Message-ID:"):
        assert header in raw
