"""Login, logout, and forced first-login password change."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user, flash, render
from app.services.auth import authenticate, set_password, verify_password

router = APIRouter()


@router.get("/login")
async def login_form(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    user = await authenticate(session, email, password)
    if user is None:
        flash(request, "Invalid email or password.", "error")
        return RedirectResponse("/login", status_code=303)
    request.session["user_id"] = user.id
    request.session["email"] = user.email
    request.session["must_change"] = user.must_change_password
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/change-password")
async def change_password_form(request: Request):
    return render(request, "change_password.html")


@router.post("/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    if not verify_password(user.password_hash, current_password):
        flash(request, "Current password is incorrect.", "error")
        return RedirectResponse("/change-password", status_code=303)
    if new_password != confirm_password:
        flash(request, "New passwords do not match.", "error")
        return RedirectResponse("/change-password", status_code=303)
    if len(new_password) < 10:
        flash(request, "Use at least 10 characters.", "error")
        return RedirectResponse("/change-password", status_code=303)
    await set_password(session, user, new_password)
    await session.commit()
    request.session["must_change"] = False
    flash(request, "Password updated.", "success")
    return RedirectResponse("/", status_code=303)
