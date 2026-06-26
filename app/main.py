"""FastAPI application entrypoint.

Server-rendered admin UI (Jinja2) + JSON endpoints for the status API, Postal
webhooks, and public unsubscribe. Auth is a signed session cookie; an auth
middleware redirects unauthenticated users to the login page and forces a
password change on first login.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from app.api import status as status_api
from app.api import unsubscribe as unsubscribe_api
from app.api import webhooks as webhooks_api
from app.config import settings
from app.logging import configure_logging, get_logger
from app.web import auth as auth_web
from app.web import campaigns as campaigns_web
from app.web import dashboard as dashboard_web
from app.web import lists as lists_web
from app.web import settings_routes as settings_web
from app.web import templates_routes as templates_web

configure_logging("DEBUG" if settings.app_debug else "INFO")
logger = get_logger(__name__)

app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None)

_PUBLIC_PREFIXES = (
    "/login", "/logout", "/static", "/u/", "/webhooks/", "/api/", "/healthz", "/favicon",
)
_MUST_CHANGE_ALLOWED = ("/change-password", "/logout", "/static")


# NOTE on ordering: add_middleware prepends, so the LAST-added middleware is the
# OUTERMOST (runs first). auth_guard is registered before SessionMiddleware so
# SessionMiddleware ends up outermost and request.session is populated by the
# time auth_guard runs.
@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    if path.startswith(_PUBLIC_PREFIXES):
        return await call_next(request)
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    if request.session.get("must_change") and not path.startswith(_MUST_CHANGE_ALLOWED):
        return RedirectResponse("/change-password", status_code=303)
    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",      # mitigates CSRF for cross-site POST navigations
    https_only=settings.app_base_url.startswith("https"),
    max_age=14 * 24 * 3600,
)


app.mount(
    "/static",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")),
    name="static",
)

app.include_router(auth_web.router)
app.include_router(dashboard_web.router)
app.include_router(lists_web.router)
app.include_router(templates_web.router)
app.include_router(campaigns_web.router)
app.include_router(settings_web.router)
app.include_router(status_api.router)
app.include_router(webhooks_api.router)
app.include_router(unsubscribe_api.router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "app": settings.app_name}


@app.on_event("startup")
async def on_startup():
    # Best-effort deliverability preflight (Section 9). Never blocks startup.
    try:
        from app.services.preflight import run_preflight

        app.state.preflight = await run_in_threadpool(run_preflight)
    except Exception as exc:  # noqa: BLE001
        logger.warning("preflight skipped: %s", exc)
        app.state.preflight = []
