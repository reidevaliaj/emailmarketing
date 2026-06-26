"""Lists & contacts UI: upload, view, manual add, global search-and-delete."""

from __future__ import annotations

import os
import shutil
import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import current_user, flash, render
from app.services import lists as lists_svc
from app.tasks.imports import import_csv

router = APIRouter()


@router.get("/lists")
async def lists_index(
    request: Request, session: AsyncSession = Depends(get_db), user=Depends(current_user)
):
    all_lists = await lists_svc.list_lists(session)
    return render(request, "lists.html", {"lists": all_lists})


@router.post("/lists/upload")
async def upload_list(
    request: Request,
    name: str = Form(...),
    apply_free_filter: str | None = Form(None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    # Persist the upload to the shared upload dir, then process in a background
    # task so large files never block the request (Section 8 / 7b).
    os.makedirs(settings.upload_dir, exist_ok=True)
    dest = os.path.join(settings.upload_dir, f"{uuid.uuid4().hex}.csv")
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)

    lst = await lists_svc.create_list(
        session, name=name, source_filename=file.filename, created_by=user.id
    )
    await session.commit()  # commit before enqueue so the worker sees the list

    apply_free = apply_free_filter is not None  # checkbox present => on
    import_csv.delay(lst.id, dest, None, apply_free)
    flash(request, "List uploaded — importing and verifying in the background.", "success")
    return RedirectResponse(f"/lists/{lst.id}", status_code=303)


@router.get("/lists/{list_id}")
async def list_detail(
    request: Request,
    list_id: int,
    page: int = 1,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    lst = await lists_svc.get_list(session, list_id)
    if lst is None:
        flash(request, "List not found.", "error")
        return RedirectResponse("/lists", status_code=303)
    page = max(1, page)
    per_page = 50
    contacts, total = await lists_svc.list_contacts(
        session, list_id, offset=(page - 1) * per_page, limit=per_page
    )
    pages = max(1, (total + per_page - 1) // per_page)
    return render(
        request, "list_detail.html",
        {"lst": lst, "contacts": contacts, "total": total, "page": page, "pages": pages},
    )


@router.post("/lists/{list_id}/contacts")
async def add_contact(
    request: Request,
    list_id: int,
    email: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    result = await lists_svc.add_contact(session, list_id, email, first_name, last_name)
    await session.commit()
    messages = {
        "added": ("Contact added.", "success"),
        "invalid": ("Invalid email address.", "error"),
        "duplicate": ("That email already exists in the system (not added).", "error"),
        "suppressed": ("That email is suppressed and cannot be added.", "error"),
    }
    msg, cat = messages.get(result.reason, ("Done.", "info"))
    flash(request, msg, cat)
    return RedirectResponse(f"/lists/{list_id}", status_code=303)


@router.post("/lists/{list_id}/delete")
async def delete_list(
    request: Request,
    list_id: int,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    await lists_svc.delete_list(session, list_id)
    await session.commit()
    flash(request, "List deleted.", "success")
    return RedirectResponse("/lists", status_code=303)


# --- Global search-and-delete (Section 8) ---------------------------------

@router.get("/search")
async def search_page(
    request: Request,
    q: str = "",
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    hits = await lists_svc.search_contacts(session, q) if q else []
    return render(request, "search.html", {"q": q, "hits": hits})


@router.post("/search/remove")
async def search_remove(
    request: Request,
    email: str = Form(...),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    await lists_svc.remove_and_suppress(session, email)
    await session.commit()
    flash(request, f"{email} removed and suppressed globally.", "success")
    return RedirectResponse(f"/search?q={email}", status_code=303)


@router.post("/suppress/add")
async def suppress_add(
    request: Request,
    email: str = Form(...),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    await lists_svc.suppress_only(session, email)
    await session.commit()
    flash(request, f"{email} added to global suppression.", "success")
    return RedirectResponse(f"/search?q={email}", status_code=303)
