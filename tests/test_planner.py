"""4-Week Planner: position calc, frequency rule, warming hard-stop."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app.db import sync_session
from app.models.app_setting import AppSetting
from app.models.base import utcnow
from app.models.campaign import Campaign, CampaignRecipient
from app.models.contact import Contact
from app.models.contact_list import ContactList
from app.models.enums import CampaignStatus, RecipientStatus
from app.models.planner import CampaignContactSend, CampaignList, PlannerPlacement
from app.models.template import Template
from app.services.planner_engine import dispatch_planner, position_for_date
from app.services.warming import ramp_value
from app.tasks.sending import materialize_campaign


# --- helpers ---------------------------------------------------------------

def _recurring(emails, *, name="Svc", lists=1):
    """Create a recurring planner campaign with `lists` lists of contacts."""
    with sync_session() as s:
        tmpl = Template(name="T", type="plain", body="Hi {{first_name}} {{unsubscribe_url}}")
        s.add(tmpl); s.flush()
        camp = Campaign(
            name=name, subject="S", from_name="X", from_email="news@marketing.cod-st.com",
            template_id=tmpl.id, is_planner=True, status=CampaignStatus.PLANNER.value,
            send_time="09:00", send_timezone="Europe/Tirana",
        )
        s.add(camp); s.flush()
        per = max(1, len(emails) // lists)
        for i in range(lists):
            lst = ContactList(name=f"L{i}", verification_status="ready"); s.add(lst); s.flush()
            s.add(CampaignList(campaign_id=camp.id, list_id=lst.id))
            for e in emails[i * per:(i + 1) * per] if lists > 1 else emails:
                s.add(Contact(email=e, first_name="C", list_id=lst.id, status="active"))
        return camp.id


def _run(recurring_id):
    """Make a scheduled run of a recurring campaign and flip it to SENDING."""
    with sync_session() as s:
        rec = s.get(Campaign, recurring_id)
        run = Campaign(
            name=f"{rec.name} run", subject=rec.subject, from_name=rec.from_name,
            from_email=rec.from_email, template_id=rec.template_id,
            is_planner=False, parent_campaign_id=rec.id, status=CampaignStatus.SENDING.value,
        )
        s.add(run); s.flush()
        for lid in s.scalars(select(CampaignList.list_id).where(CampaignList.campaign_id == rec.id)):
            s.add(CampaignList(campaign_id=run.id, list_id=lid))
        return run.id


def _statuses(campaign_id):
    with sync_session() as s:
        return [
            (r.email_snapshot, r.status)
            for r in s.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id))
        ]


def _set_cfg(**kw):
    with sync_session() as s:
        row = s.get(AppSetting, "planner_config")
        val = dict(row.value) if row and isinstance(row.value, dict) else {}
        val.update(kw)
        if row:
            row.value = val
        else:
            s.add(AppSetting(key="planner_config", value=val))


# --- position calc ---------------------------------------------------------

def test_position_calc():
    assert position_for_date(date(2026, 6, 1)) == (1, 0)    # 1st Monday
    assert position_for_date(date(2026, 6, 8)) == (2, 0)    # 2nd Monday
    assert position_for_date(date(2026, 6, 26)) == (4, 4)   # 4th Friday
    assert position_for_date(date(2026, 6, 29)) is None     # 5th Monday -> idle
    assert position_for_date(date(2026, 6, 6)) is None      # Saturday -> none


# --- frequency rule (the core requirement) --------------------------------

def test_frequency_skips_recent_same_campaign():
    """A contact who got this campaign <30d ago is skipped on the next run."""
    rec = _recurring(["a@acme.com", "b@acme.com"])
    # a@ already received the recurring campaign 5 days ago.
    with sync_session() as s:
        cid_a = s.scalar(select(Contact.id).where(Contact.email == "a@acme.com"))
        s.add(CampaignContactSend(campaign_id=rec, contact_id=cid_a, last_sent_at=utcnow() - timedelta(days=5)))

    run = _run(rec)
    materialize_campaign(run)
    statuses = dict(_statuses(run))
    assert statuses["a@acme.com"] == RecipientStatus.SKIPPED_FREQUENCY.value
    assert statuses["b@acme.com"] == RecipientStatus.SENT.value  # never received it -> eligible


def test_double_placement_does_not_double_send():
    """Same campaign fired twice within the interval: 2nd firing re-sends to no one."""
    rec = _recurring(["x@acme.com"])
    run1 = _run(rec)
    materialize_campaign(run1)
    assert dict(_statuses(run1))["x@acme.com"] == RecipientStatus.SENT.value

    run2 = _run(rec)  # second firing (e.g. campaign placed in two cells)
    materialize_campaign(run2)
    assert dict(_statuses(run2))["x@acme.com"] == RecipientStatus.SKIPPED_FREQUENCY.value


def test_contact_can_receive_different_campaigns():
    """Recently receiving campaign A does NOT block campaign B."""
    rec_a = _recurring(["dentist@acme.com"], name="WebDev")
    run_a = _run(rec_a)
    materialize_campaign(run_a)
    assert dict(_statuses(run_a))["dentist@acme.com"] == RecipientStatus.SENT.value

    # Same contact, DIFFERENT campaign (different list but globally-unique email).
    with sync_session() as s:
        # move the contact's list under a second recurring campaign
        tmpl = Template(name="T2", type="plain", body="Hi {{unsubscribe_url}}"); s.add(tmpl); s.flush()
        rec_b = Campaign(name="GEO", subject="S", from_name="X",
                         from_email="news@marketing.cod-st.com", template_id=tmpl.id,
                         is_planner=True, status=CampaignStatus.PLANNER.value)
        s.add(rec_b); s.flush()
        lid = s.scalar(select(Contact.list_id).where(Contact.email == "dentist@acme.com"))
        s.add(CampaignList(campaign_id=rec_b.id, list_id=lid))
        rec_b_id = rec_b.id

    run_b = _run(rec_b_id)
    materialize_campaign(run_b)
    assert dict(_statuses(run_b))["dentist@acme.com"] == RecipientStatus.SENT.value  # eligible for B


# --- warming hard-stop -----------------------------------------------------

def test_warming_ramp_value():
    cfg = {"warming_ramp": [2000, 5000, 7000, 10000], "warming_full": 10000}
    assert ramp_value(cfg, 0) == 2000      # week 1
    assert ramp_value(cfg, 7) == 5000      # week 2
    assert ramp_value(cfg, 21) == 10000    # week 4
    assert ramp_value(cfg, 60) == 10000    # full


def test_warming_cap_hard_stop():
    """With a tiny cap, send up to it and SKIP the remainder (hard-stop)."""
    _set_cfg(warming_ramp=[2], warming_full=2)  # no IPWarmState -> cap = ramp[0] = 2
    rec = _recurring(["a@x.com", "b@x.com", "c@x.com", "d@x.com"])
    run = _run(rec)
    materialize_campaign(run)
    statuses = [st for _, st in _statuses(run)]
    assert statuses.count(RecipientStatus.SENT.value) == 2
    assert statuses.count(RecipientStatus.SKIPPED_CAP.value) == 2


# --- dispatch idempotency --------------------------------------------------

def test_dispatch_creates_run_once_per_day():
    rec = _recurring(["a@acme.com"])
    with sync_session() as s:
        s.add(PlannerPlacement(campaign_id=rec, week=1, weekday=0))  # Week1 Monday

    first_monday = date(2026, 6, 1)
    r1 = dispatch_planner(for_date=first_monday)
    assert r1["created"], "should create a run on its position day"
    r2 = dispatch_planner(for_date=first_monday)
    assert not r2["created"], "re-dispatch same day must be idempotent"

    # Not its day -> nothing.
    r3 = dispatch_planner(for_date=date(2026, 6, 8))  # 2nd Monday
    assert not r3["created"]
