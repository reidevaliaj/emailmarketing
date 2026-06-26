"""Dashboard: sending health + recent campaigns + preflight (Section 8)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user, render
from app.services.stats import dashboard_stats

router = APIRouter()


@router.get("/")
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    stats = await dashboard_stats(session)
    preflight = getattr(request.app.state, "preflight", [])
    return render(request, "dashboard.html", {"stats": stats, "preflight": preflight})
