"""Campaign management (async, UI side).

State transitions set DB state only; the calling route is responsible for
committing and THEN enqueuing any Celery work (commit-before-enqueue avoids a
worker racing ahead of an uncommitted status change). Actual dispatch of
SCHEDULED campaigns is done by Celery Beat (Section 6.1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.base import utcnow
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.enums import CampaignStatus
from app.models.suppression import Suppression
from app.models.template import Template
from app.services.merge import has_unsubscribe
from app.services.normalize import domain_of


async def list_campaigns(session: AsyncSession) -> list[Campaign]:
    return list(await session.scalars(select(Campaign).order_by(Campaign.created_at.desc())))


async def get_campaign(session: AsyncSession, campaign_id: int) -> Campaign | None:
    return await session.get(Campaign, campaign_id)


async def create_campaign(session: AsyncSession, **fields) -> Campaign:
    campaign = Campaign(
        name=(fields.get("name") or "Untitled campaign").strip(),
        subject=fields.get("subject") or "",
        from_name=fields.get("from_name") or settings.default_from_name,
        from_email=fields.get("from_email") or settings.default_from_email,
        template_id=fields.get("template_id"),
        list_id=fields.get("list_id"),
        ip_pool=fields.get("ip_pool"),
        status=CampaignStatus.DRAFT.value,
    )
    session.add(campaign)
    await session.flush()
    return campaign


async def update_campaign(session: AsyncSession, campaign_id: int, **fields) -> Campaign | None:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None or campaign.status not in (
        CampaignStatus.DRAFT.value, CampaignStatus.SCHEDULED.value
    ):
        return None  # only editable while draft/scheduled
    for key in ("name", "subject", "from_name", "from_email", "template_id", "list_id", "ip_pool"):
        if key in fields and fields[key] is not None:
            setattr(campaign, key, fields[key])
    await session.flush()
    return campaign


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


async def validate_for_send(session: AsyncSession, campaign: Campaign) -> ValidationResult:
    res = ValidationResult()
    if campaign.list_id is None:
        res.errors.append("Campaign has no contact list.")
    if campaign.template_id is None:
        res.errors.append("Campaign has no template.")
    if not campaign.subject.strip():
        res.errors.append("Subject is empty.")
    if not campaign.from_email:
        res.errors.append("From address is empty.")
    elif domain_of(campaign.from_email) != settings.sending_domain.lower():
        res.errors.append(
            f"From address must be on the sending domain ({settings.sending_domain})."
        )
    if campaign.template_id is not None:
        template = await session.get(Template, campaign.template_id)
        if template is None:
            res.errors.append("Template not found.")
        elif not has_unsubscribe(template.body):
            # Compliance: refuse to send a template lacking {{unsubscribe_url}}.
            res.errors.append("Template must include an {{unsubscribe_url}} link.")
    return res


@dataclass
class PreSendSummary:
    recipients: int
    suppressed_excluded: int
    estimated_minutes: int
    errors: list[str]


async def presend_summary(session: AsyncSession, campaign: Campaign) -> PreSendSummary:
    validation = await validate_for_send(session, campaign)
    recipients = suppressed = 0
    if campaign.list_id is not None:
        active = await session.scalar(
            select(func.count()).select_from(Contact).where(
                Contact.list_id == campaign.list_id, Contact.status == "active"
            )
        ) or 0
        # Active contacts whose email is in the global suppression list.
        suppressed = await session.scalar(
            select(func.count())
            .select_from(Contact)
            .join(Suppression, Suppression.email == Contact.email)
            .where(Contact.list_id == campaign.list_id, Contact.status == "active")
        ) or 0
        recipients = active - suppressed
    rate = max(1, settings.rate_global_per_minute)
    return PreSendSummary(
        recipients=recipients,
        suppressed_excluded=suppressed,
        estimated_minutes=math.ceil(recipients / rate) if recipients else 0,
        errors=validation.errors,
    )


async def schedule(
    session: AsyncSession, campaign: Campaign, scheduled_at: datetime
) -> ValidationResult:
    """Validate then mark SCHEDULED. Beat dispatches once scheduled_at passes
    (set scheduled_at <= now for "send now" — dispatched within ~1 minute)."""
    res = await validate_for_send(session, campaign)
    if not res.ok:
        return res
    campaign.scheduled_at = scheduled_at
    campaign.status = CampaignStatus.SCHEDULED.value
    await session.flush()
    return res


async def pause(session: AsyncSession, campaign: Campaign) -> bool:
    if campaign.status not in (CampaignStatus.SENDING.value, CampaignStatus.SCHEDULED.value):
        return False
    campaign.status = CampaignStatus.PAUSED.value
    await session.flush()
    return True


async def resume(session: AsyncSession, campaign: Campaign) -> bool:
    """Mark SENDING again. Route must commit then enqueue materialize (idempotent,
    re-enqueues remaining PENDING recipients)."""
    if campaign.status != CampaignStatus.PAUSED.value:
        return False
    campaign.status = CampaignStatus.SENDING.value
    await session.flush()
    return True


async def cancel(session: AsyncSession, campaign: Campaign) -> bool:
    if campaign.status in (
        CampaignStatus.COMPLETED.value, CampaignStatus.CANCELLED.value
    ):
        return False
    campaign.status = CampaignStatus.CANCELLED.value
    await session.flush()
    return True
