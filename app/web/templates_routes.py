"""Template CRUD UI with live preview and merge-tag/unsubscribe validation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user, flash, render
from app.services import templates as tmpl_svc

router = APIRouter()


@router.get("/templates")
async def templates_index(
    request: Request, session: AsyncSession = Depends(get_db), user=Depends(current_user)
):
    items = await tmpl_svc.list_templates(session)
    return render(request, "templates.html", {"templates": items})


@router.get("/templates/new")
async def new_template(request: Request, user=Depends(current_user)):
    return render(request, "template_form.html", {"template": None})


@router.post("/templates")
async def create_template(
    request: Request,
    name: str = Form(...),
    type: str = Form("plain"),
    subject_default: str = Form(""),
    body: str = Form(""),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    validation = tmpl_svc.validate_template(body)
    if not validation.has_unsubscribe:
        flash(request, "Template must include {{unsubscribe_url}} before it can be used.", "error")
    tmpl = await tmpl_svc.create_template(session, name, type, subject_default or None, body)
    await session.commit()
    flash(request, "Template created.", "success")
    return RedirectResponse(f"/templates/{tmpl.id}/edit", status_code=303)


@router.post("/templates/preview", response_class=HTMLResponse)
async def preview_template(
    request: Request,
    type: str = Form("plain"),
    body: str = Form(""),
    user=Depends(current_user),
):
    """Render the body with sample merge data for the live preview pane.

    Defined BEFORE /templates/{template_id} so the literal path isn't captured
    by the int path param.
    """
    html = type == "html"
    rendered = tmpl_svc.preview(body, html=html)
    if not html:
        from markupsafe import escape

        rendered = f"<pre style='white-space:pre-wrap'>{escape(rendered)}</pre>"
    return HTMLResponse(rendered)


@router.get("/templates/{template_id}/edit")
async def edit_template(
    request: Request,
    template_id: int,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    tmpl = await tmpl_svc.get_template(session, template_id)
    if tmpl is None:
        flash(request, "Template not found.", "error")
        return RedirectResponse("/templates", status_code=303)
    validation = tmpl_svc.validate_template(tmpl.body)
    return render(request, "template_form.html", {"template": tmpl, "validation": validation})


@router.post("/templates/{template_id}")
async def update_template(
    request: Request,
    template_id: int,
    name: str = Form(...),
    type: str = Form("plain"),
    subject_default: str = Form(""),
    body: str = Form(""),
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    await tmpl_svc.update_template(
        session, template_id, name=name, type=type, subject_default=subject_default or None, body=body
    )
    await session.commit()
    if not tmpl_svc.validate_template(body).has_unsubscribe:
        flash(request, "Saved — but add {{unsubscribe_url}} before sending.", "error")
    else:
        flash(request, "Template saved.", "success")
    return RedirectResponse(f"/templates/{template_id}/edit", status_code=303)


@router.post("/templates/{template_id}/delete")
async def delete_template(
    request: Request,
    template_id: int,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    await tmpl_svc.delete_template(session, template_id)
    await session.commit()
    flash(request, "Template deleted.", "success")
    return RedirectResponse("/templates", status_code=303)
