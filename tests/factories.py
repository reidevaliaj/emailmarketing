"""Tiny builders for test data."""

from __future__ import annotations

from app.db import sync_session
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.contact_list import ContactList
from app.models.template import Template


def make_campaign(
    emails: list[str],
    *,
    template_body: str = "Hi {{first_name}}, stop: {{unsubscribe_url}}",
    status: str = "sending",
    subject: str = "Subject {{first_name}}",
) -> dict:
    with sync_session() as s:
        lst = ContactList(name="L", verification_status="ready")
        s.add(lst)
        s.flush()
        for e in emails:
            s.add(Contact(email=e, first_name="X", list_id=lst.id, status="active"))
        tmpl = Template(name="T", type="plain", body=template_body)
        s.add(tmpl)
        s.flush()
        camp = Campaign(
            name="C", subject=subject, from_name="X",
            from_email="news@marketing.cod-st.com",
            template_id=tmpl.id, list_id=lst.id, status=status,
        )
        s.add(camp)
        s.flush()
        return {"campaign_id": camp.id, "list_id": lst.id, "template_id": tmpl.id}
