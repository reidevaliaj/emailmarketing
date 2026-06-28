"""4-Week Planner UI routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user, flash, render
from app.services import planner as planner_svc
from app.services import templates as tmpl_svc
from app.services.campaign_lists import get_campaign_list_ids
from app.services.lists import list_lists
from app.services.planner import COMMON_TIMEZONES

router = APIRouter()


@router.get("/planner")
async def planner_page(
    request: Request, session: AsyncSession = Depends(get_db), user=Depends(current_user)
):
    return render(
        request, "planner.html",
        {
            "grid": await planner_svc.grid(session),
            "campaigns": await planner_svc.recurring_campaigns(session),
        },
    )


@router.get("/planner/grid")
async def planner_grid_fragment(
    request: Request, session: AsyncSession = Depends(get_db), user=Depends(current_user)
):
    """Just the grid partial — swapped in after a drag/drop without a full reload."""
    return render(request, "_planner_grid.html", {"grid": await planner_svc.grid(session)})


@router.get("/planner/status")
async def planner_status(
    request: Request, session: AsyncSession = Depends(get_db), user=Depends(current_user)
):
    from app.services.planner_config import get_planner_config
    from app.services.planner_monitor import monitor

    return render(
        request, "planner_status.html",
        {"monitor": await monitor(session), "cfg": await get_planner_config(session),
         "timezones": COMMON_TIMEZONES},
    )


@router.post("/planner/config")
async def update_planner_config(
    request: Request,
    ramp1: int = Form(2000), ramp2: int = Form(5000), ramp3: int = Form(7000), ramp4: int = Form(10000),
    warming_full: int = Form(10000),
    send_rate_per_minute: int = Form(25),
    frequency_interval_days: int = Form(30),
    overflow_policy: str = Form("hard_stop"),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    from app.services.planner_config import set_planner_config

    await set_planner_config(session, {
        "warming_ramp": [ramp1, ramp2, ramp3, ramp4],
        "warming_full": warming_full,
        "send_rate_per_minute": send_rate_per_minute,
        "frequency_interval_days": frequency_interval_days,
        "overflow_policy": overflow_policy,
    })
    await session.commit()
    flash(request, "Planner & warming settings saved.", "success")
    return RedirectResponse("/planner/status", status_code=303)


@router.post("/planner/warming/ip")
async def set_warming_ip(
    request: Request,
    ip: str = Form(...),
    pool: str = Form(""),
    warmed_since: str = Form(""),
    user=Depends(current_user),
):
    from datetime import datetime, timezone

    from app.db import sync_session
    from app.services.warming import ensure_ip_warm_state

    since = None
    if warmed_since:
        try:
            since = datetime.fromisoformat(warmed_since).replace(tzinfo=timezone.utc)
        except ValueError:
            since = None
    with sync_session() as s:
        state = ensure_ip_warm_state(s, ip.strip(), pool.strip() or None, since)
        if since is not None:
            state.warmed_since = since
    flash(request, f"Warming start set for {ip}.", "success")
    return RedirectResponse("/planner/status", status_code=303)


@router.post("/planner/warming/ip/{ip}/delete")
async def delete_warming_ip(request: Request, ip: str, user=Depends(current_user)):
    from sqlalchemy import delete as sa_delete

    from app.db import sync_session
    from app.models.planner import IPWarmState

    with sync_session() as s:
        s.execute(sa_delete(IPWarmState).where(IPWarmState.ip == ip))
    flash(request, f"Removed warming state for {ip}.", "success")
    return RedirectResponse("/planner/status", status_code=303)


@router.post("/planner/placements")
async def add_placement(
    request: Request,
    campaign_id: int = Form(...),
    week: int = Form(...),
    weekday: int = Form(...),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    pid = await planner_svc.add_placement(session, campaign_id, week, weekday)
    await session.commit()
    return JSONResponse({"ok": pid is not None, "placement_id": pid})


@router.post("/planner/placements/{placement_id}/move")
async def move_placement(
    placement_id: int,
    week: int = Form(...),
    weekday: int = Form(...),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    ok = await planner_svc.move_placement(session, placement_id, week, weekday)
    await session.commit()
    return JSONResponse({"ok": ok})


@router.post("/planner/placements/{placement_id}/delete")
async def delete_placement(
    placement_id: int,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    ok = await planner_svc.remove_placement(session, placement_id)
    await session.commit()
    return JSONResponse({"ok": ok})


# --- recurring campaign (service) CRUD ------------------------------------

async def _form_context(session: AsyncSession, campaign=None) -> dict:
    return {
        "campaign": campaign,
        "templates": await tmpl_svc.list_templates(session),
        "lists": await list_lists(session),
        "timezones": COMMON_TIMEZONES,
        "selected_lists": (
            set(await get_campaign_list_ids(session, campaign.id)) if campaign else set()
        ),
    }


@router.get("/planner/campaigns/new")
async def new_recurring(
    request: Request, session: AsyncSession = Depends(get_db), user=Depends(current_user)
):
    return render(request, "planner_campaign_form.html", await _form_context(session))


@router.post("/planner/campaigns")
async def create_recurring(
    request: Request,
    name: str = Form(...),
    subject: str = Form(""),
    from_name: str = Form(""),
    from_email: str = Form(""),
    reply_to: str = Form(""),
    template_id: int | None = Form(None),
    list_ids: list[int] = Form(default=[]),
    send_time: str = Form("09:00"),
    send_timezone: str = Form("Europe/Tirana"),
    ip_pool: str = Form(""),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    camp = await planner_svc.create_recurring(
        session, list_ids=list_ids, name=name, subject=subject, from_name=from_name,
        from_email=from_email, reply_to=reply_to, template_id=template_id,
        send_time=send_time, send_timezone=send_timezone, ip_pool=ip_pool or None,
    )
    await session.commit()
    flash(request, f"Service campaign '{camp.name}' created — drag it onto the planner.", "success")
    return RedirectResponse("/planner", status_code=303)


@router.get("/planner/campaigns/{campaign_id}/edit")
async def edit_recurring(
    request: Request,
    campaign_id: int,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    from app.services.campaigns import get_campaign

    camp = await get_campaign(session, campaign_id)
    if camp is None or not camp.is_planner:
        flash(request, "Service campaign not found.", "error")
        return RedirectResponse("/planner", status_code=303)
    return render(request, "planner_campaign_form.html", await _form_context(session, camp))


@router.post("/planner/campaigns/{campaign_id}")
async def update_recurring(
    request: Request,
    campaign_id: int,
    name: str = Form(...),
    subject: str = Form(""),
    from_name: str = Form(""),
    from_email: str = Form(""),
    reply_to: str = Form(""),
    template_id: int | None = Form(None),
    list_ids: list[int] = Form(default=[]),
    send_time: str = Form("09:00"),
    send_timezone: str = Form("Europe/Tirana"),
    ip_pool: str = Form(""),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    await planner_svc.update_recurring(
        session, campaign_id, list_ids=list_ids, name=name, subject=subject,
        from_name=from_name, from_email=from_email, reply_to=reply_to,
        template_id=template_id, send_time=send_time, send_timezone=send_timezone,
        ip_pool=ip_pool or None,
    )
    await session.commit()
    flash(request, "Service campaign updated.", "success")
    return RedirectResponse("/planner", status_code=303)


@router.post("/planner/campaigns/{campaign_id}/delete")
async def delete_recurring(
    request: Request,
    campaign_id: int,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    await planner_svc.delete_recurring(session, campaign_id)
    await session.commit()
    flash(request, "Service campaign deleted.", "success")
    return RedirectResponse("/planner", status_code=303)
