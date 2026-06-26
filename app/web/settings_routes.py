"""Settings UI: free-provider blocklist (Section 8) + status-API keys."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user, flash, render
from app.services import api_keys as keys_svc
from app.services.config_store import get_free_provider_config, set_free_provider_config

router = APIRouter()


@router.get("/settings")
async def settings_page(
    request: Request, session: AsyncSession = Depends(get_db), user=Depends(current_user)
):
    enabled, domains = await get_free_provider_config(session)
    keys = await keys_svc.list_api_keys(session)
    return render(
        request, "settings.html",
        {"free_enabled": enabled, "free_domains": "\n".join(domains), "api_keys": keys},
    )


@router.post("/settings/free-provider")
async def update_free_provider(
    request: Request,
    enabled: str | None = Form(None),
    domains: str = Form(""),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    domain_list = [d.strip() for d in domains.replace(",", "\n").splitlines() if d.strip()]
    await set_free_provider_config(session, enabled is not None, domain_list)
    await session.commit()
    flash(request, "Free-provider filter updated.", "success")
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/api-keys")
async def create_api_key(
    request: Request,
    name: str = Form(...),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    _row, plaintext = await keys_svc.create_api_key(session, name)
    await session.commit()
    # Shown once — never recoverable afterwards.
    flash(request, f"API key created. Copy it now — it won't be shown again: {plaintext}", "success")
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/api-keys/{key_id}/revoke")
async def revoke_api_key(
    request: Request,
    key_id: int,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    await keys_svc.revoke_api_key(session, key_id)
    await session.commit()
    flash(request, "API key revoked.", "success")
    return RedirectResponse("/settings", status_code=303)
