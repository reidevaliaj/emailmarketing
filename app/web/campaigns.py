"""Campaign UI: create/edit, schedule, pause/resume/cancel, detail + ledger."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import current_user, flash, render
from app.models.campaign import CampaignRecipient
from app.services import campaigns as camp_svc
from app.services import lists as lists_svc
from app.services import templates as tmpl_svc
from app.services.stats import campaign_progress
from app.tasks.sending import materialize_campaign

router = APIRouter()


def _parse_local_dt(value: str) -> datetime | None:
    """Parse an HTML datetime-local value in the app timezone -> aware UTC."""
    if not value:
        return None
    try:
        naive = datetime.fromisoformat(value)
    except ValueError:
        return None
    tz = ZoneInfo(settings.app_timezone)
    return naive.replace(tzinfo=tz).astimezone(timezone.utc)


@router.get("/campaigns")
async def campaigns_index(
    request: Request, session: AsyncSession = Depends(get_db), user=Depends(current_user)
):
    items = await camp_svc.list_campaigns(session)
    return render(request, "campaigns.html", {"campaigns": items, "progress": campaign_progress})


@router.get("/campaigns/new")
async def new_campaign(
    request: Request, session: AsyncSession = Depends(get_db), user=Depends(current_user)
):
    return render(
        request, "campaign_form.html",
        {
            "campaign": None,
            "lists": await lists_svc.list_lists(session),
            "templates": await tmpl_svc.list_templates(session),
        },
    )


@router.post("/campaigns")
async def create_campaign(
    request: Request,
    name: str = Form(...),
    subject: str = Form(""),
    from_name: str = Form(""),
    from_email: str = Form(""),
    list_id: int | None = Form(None),
    template_id: int | None = Form(None),
    ip_pool: str = Form(""),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    campaign = await camp_svc.create_campaign(
        session, name=name, subject=subject,
        from_name=from_name or settings.default_from_name,
        from_email=from_email or settings.default_from_email,
        list_id=list_id, template_id=template_id, ip_pool=ip_pool or None,
    )
    await session.commit()
    flash(request, "Campaign created as draft.", "success")
    return RedirectResponse(f"/campaigns/{campaign.id}", status_code=303)


@router.get("/campaigns/{campaign_id}")
async def campaign_detail(
    request: Request,
    campaign_id: int,
    page: int = 1,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    campaign = await camp_svc.get_campaign(session, campaign_id)
    if campaign is None:
        flash(request, "Campaign not found.", "error")
        return RedirectResponse("/campaigns", status_code=303)
    summary = await camp_svc.presend_summary(session, campaign)
    page = max(1, page)
    per_page = 50
    recipients = list(
        await session.scalars(
            select(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == campaign_id)
            .order_by(CampaignRecipient.id)
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    )
    return render(
        request, "campaign_detail.html",
        {
            "campaign": campaign,
            "progress": campaign_progress(campaign),
            "summary": summary,
            "recipients": recipients,
            "page": page,
        },
    )


@router.get("/campaigns/{campaign_id}/edit")
async def edit_campaign(
    request: Request,
    campaign_id: int,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    campaign = await camp_svc.get_campaign(session, campaign_id)
    if campaign is None:
        flash(request, "Campaign not found.", "error")
        return RedirectResponse("/campaigns", status_code=303)
    return render(
        request, "campaign_form.html",
        {
            "campaign": campaign,
            "lists": await lists_svc.list_lists(session),
            "templates": await tmpl_svc.list_templates(session),
        },
    )


@router.post("/campaigns/{campaign_id}")
async def update_campaign(
    request: Request,
    campaign_id: int,
    name: str = Form(...),
    subject: str = Form(""),
    from_name: str = Form(""),
    from_email: str = Form(""),
    list_id: int | None = Form(None),
    template_id: int | None = Form(None),
    ip_pool: str = Form(""),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    updated = await camp_svc.update_campaign(
        session, campaign_id, name=name, subject=subject, from_name=from_name,
        from_email=from_email, list_id=list_id, template_id=template_id, ip_pool=ip_pool or None,
    )
    await session.commit()
    if updated is None:
        flash(request, "Only draft/scheduled campaigns can be edited.", "error")
    else:
        flash(request, "Campaign updated.", "success")
    return RedirectResponse(f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/schedule")
async def schedule_campaign(
    request: Request,
    campaign_id: int,
    scheduled_at: str = Form(""),
    send_now: str | None = Form(None),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    campaign = await camp_svc.get_campaign(session, campaign_id)
    if campaign is None:
        flash(request, "Campaign not found.", "error")
        return RedirectResponse("/campaigns", status_code=303)
    when = datetime.now(timezone.utc) if send_now is not None else _parse_local_dt(scheduled_at)
    if when is None:
        flash(request, "Pick a valid send date/time.", "error")
        return RedirectResponse(f"/campaigns/{campaign_id}", status_code=303)
    result = await camp_svc.schedule(session, campaign, when)
    await session.commit()
    if not result.ok:
        for err in result.errors:
            flash(request, err, "error")
    else:
        flash(request, "Campaign scheduled. Dispatch runs within ~1 minute.", "success")
    return RedirectResponse(f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(
    request: Request,
    campaign_id: int,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    campaign = await camp_svc.get_campaign(session, campaign_id)
    if campaign and await camp_svc.pause(session, campaign):
        await session.commit()
        flash(request, "Campaign paused.", "success")
    else:
        flash(request, "Campaign cannot be paused in its current state.", "error")
    return RedirectResponse(f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/resume")
async def resume_campaign(
    request: Request,
    campaign_id: int,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    campaign = await camp_svc.get_campaign(session, campaign_id)
    if campaign and await camp_svc.resume(session, campaign):
        await session.commit()  # commit before enqueue so the worker sees SENDING
        materialize_campaign.delay(campaign_id)  # idempotent; re-enqueues pending
        flash(request, "Campaign resumed.", "success")
    else:
        flash(request, "Only a paused campaign can be resumed.", "error")
    return RedirectResponse(f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/cancel")
async def cancel_campaign(
    request: Request,
    campaign_id: int,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    campaign = await camp_svc.get_campaign(session, campaign_id)
    if campaign and await camp_svc.cancel(session, campaign):
        await session.commit()
        flash(request, "Campaign cancelled.", "success")
    else:
        flash(request, "Campaign cannot be cancelled.", "error")
    return RedirectResponse(f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/duplicate")
async def duplicate_campaign(
    request: Request,
    campaign_id: int,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    dup = await camp_svc.duplicate_campaign(session, campaign_id)
    await session.commit()
    if dup is None:
        flash(request, "Campaign not found.", "error")
        return RedirectResponse("/campaigns", status_code=303)
    flash(request, "Campaign duplicated as a new draft.", "success")
    return RedirectResponse(f"/campaigns/{dup.id}", status_code=303)


@router.post("/campaigns/{campaign_id}/delete")
async def delete_campaign(
    request: Request,
    campaign_id: int,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    ok = await camp_svc.delete_campaign(session, campaign_id)
    await session.commit()
    if ok:
        flash(request, "Campaign deleted.", "success")
        return RedirectResponse("/campaigns", status_code=303)
    flash(request, "Cancel the campaign before deleting it.", "error")
    return RedirectResponse(f"/campaigns/{campaign_id}", status_code=303)
