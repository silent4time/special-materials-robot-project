"""FastAPI app factory for the Persian RTL materials dashboard.

Architecture: reuses Database, auth.rbac, analytics, pdf, excel, bot.activity.
Does NOT embed BotApp FSM / bale_api / keyboards. Roles === bot roles (users.role).
Deploy bot + web together when shared modules change.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from auth.rbac import can_configure_catalog, can_request_materials, require_manager, role_label
from config import ROLES, SITE_STOCK_GROUPS, WEB_SECRET_KEY
from web.deps import (
    ForbiddenFa,
    LoginRequired,
    current_user_optional,
    deny_technician,
    get_db,
    user_can_see_reports,
)
from web.routers import auth as auth_router
from web.routers import home as home_router
from web.routers import materials as materials_router
from web.routers import reports as reports_router
from web.routers import settings as settings_router
from web.routers import stock as stock_router
from web.routers import tundish_report as tundish_report_router

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def create_app() -> FastAPI:
    app = FastAPI(title="داشبورد مواد تاندیش", docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=WEB_SECRET_KEY,
        session_cookie="nasoz_web_session",
        same_site="lax",
        https_only=False,
        max_age=60 * 60 * 12,
    )
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    app.state.templates = TEMPLATES

    @app.middleware("http")
    async def attach_template_globals(request: Request, call_next):
        request.state.templates = TEMPLATES
        return await call_next(request)

    @app.exception_handler(LoginRequired)
    async def _login_required(_request: Request, _exc: LoginRequired):
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(ForbiddenFa)
    async def _forbidden(request: Request, exc: ForbiddenFa):
        templates = getattr(request.state, "templates", TEMPLATES)
        user = None
        try:
            user = current_user_optional(request, get_db())
        except Exception:
            pass
        name = "home.html" if user else "login.html"
        return templates.TemplateResponse(
            request,
            name,
            {
                "user": user,
                "role_fa": role_label(user.get("role") or "") if user else "",
                "show_stock": True,
                "show_materials": can_request_materials(user) if user else False,
                "show_reports": user_can_see_reports(user),
                "show_web_settings": require_manager(user) if user else False,
                "is_technician": deny_technician(user),
                "error": exc.detail,
            },
            status_code=403,
        )

    app.include_router(auth_router.router)
    app.include_router(home_router.router)
    app.include_router(stock_router.router)
    app.include_router(materials_router.router)
    app.include_router(reports_router.router)
    app.include_router(settings_router.router)
    app.include_router(tundish_report_router.router)

    @app.get("/")
    async def root(request: Request):
        user = current_user_optional(request, get_db())
        if not user:
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse("/home", status_code=303)

    TEMPLATES.env.globals.update(
        {
            "role_label": role_label,
            "ROLES": ROLES,
            "SITE_STOCK_GROUPS": SITE_STOCK_GROUPS,
            "can_request_materials": can_request_materials,
            "can_configure_catalog": can_configure_catalog,
            "require_manager": require_manager,
            "deny_technician": deny_technician,
            "user_can_see_reports": user_can_see_reports,
        }
    )
    get_db()  # schema + WAL on startup
    return app


app = create_app()
