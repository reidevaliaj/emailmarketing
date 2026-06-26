"""Shared FastAPI dependencies, template rendering, and flash messages."""

from __future__ import annotations

import os

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.app_setting import ApiKey
from app.models.user import User
from app.services.api_keys import verify_api_key

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_TEMPLATE_DIR)
templates.env.globals["app_name"] = settings.app_name


# --- flash messages (stored in the signed session cookie) ------------------

def flash(request: Request, message: str, category: str = "info") -> None:
    request.session.setdefault("_flash", []).append({"m": message, "c": category})


def _pop_flashes(request: Request) -> list[dict]:
    return request.session.pop("_flash", [])


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    ctx = {
        "request": request,
        "user_email": request.session.get("email"),
        "flashes": _pop_flashes(request),
        "settings": settings,
    }
    ctx.update(context or {})
    return templates.TemplateResponse(name, ctx, status_code=status_code)


# --- auth dependencies -----------------------------------------------------

async def current_user(request: Request, session: AsyncSession = Depends(get_db)) -> User:
    """Load the logged-in user. The auth middleware guarantees a session exists
    for protected paths; this resolves the DB row for routes that need it."""
    uid = request.session.get("user_id")
    user = await session.get(User, uid) if uid else None
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


async def require_api_key(
    request: Request, session: AsyncSession = Depends(get_db)
) -> ApiKey:
    """Authenticate the JSON status API via X-API-Key or Bearer token."""
    key = request.headers.get("X-API-Key")
    if not key:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            key = auth[7:]
    row = await verify_api_key(session, key)
    if row is None:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    return row
