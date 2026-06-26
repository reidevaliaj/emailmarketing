"""Public unsubscribe endpoint (Section 9).

GET shows a confirmation page; POST performs the unsubscribe (also used by
one-click List-Unsubscribe-Post / RFC 8058). The token is a signed email, so no
auth is needed and ids are never exposed. Adds the address to global suppression.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import render
from app.models.enums import SuppressionReason
from app.services.suppression import suppress_async
from app.services.tokens import read_unsubscribe_token

router = APIRouter(tags=["unsubscribe"])


@router.get("/u/{token}")
async def unsubscribe_confirm(request: Request, token: str):
    email = read_unsubscribe_token(token)
    return render(request, "unsubscribe.html", {"token": token, "email": email})


@router.post("/u/{token}")
async def unsubscribe(
    request: Request, token: str, session: AsyncSession = Depends(get_db)
):
    email = read_unsubscribe_token(token)
    if email is None:
        return render(request, "unsubscribed.html", {"ok": False, "email": None})
    await suppress_async(session, email, SuppressionReason.UNSUBSCRIBE, detail="unsubscribe link")
    await session.commit()
    return render(request, "unsubscribed.html", {"ok": True, "email": email})
