"""تنظیم ورود وب — owner/manager only; links to existing users rows (bot roles)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from auth.rbac import role_label
from db.models import Database
from web.auth_web import set_credential
from web.deps import get_db, require_admin_web
from web.templating import render

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/web-login")
async def web_login_settings(
    request: Request,
    user=Depends(require_admin_web),
    db: Database = Depends(get_db),
):
    return render(
        request,
        "settings_web.html",
        {
            "user": user,
            "users": db.list_users(active_only=True),
            "credentials": db.list_web_credentials(),
            "role_label": role_label,
            "message": None,
            "error": None,
        },
    )


@router.post("/web-login")
async def web_login_save(
    request: Request,
    user=Depends(require_admin_web),
    db: Database = Depends(get_db),
    bale_user_id: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
):
    error = None
    message = None
    try:
        target = db.get_user(bale_user_id)
        if not target or not target.get("active"):
            raise KeyError("کاربر بله یافت نشد.")
        set_credential(
            db,
            bale_user_id=bale_user_id,
            username=username,
            password=password,
        )
        message = (
            f"✅ ورود وب برای «{target.get('display_name') or bale_user_id}» "
            f"(نقش: {role_label(target.get('role') or '')}) با نام کاربری «{username.strip()}» تنظیم شد."
        )
    except (KeyError, ValueError) as exc:
        error = str(exc)
    return render(
        request,
        "settings_web.html",
        {
            "user": user,
            "users": db.list_users(active_only=True),
            "credentials": db.list_web_credentials(),
            "role_label": role_label,
            "message": message,
            "error": error,
        },
        status_code=400 if error else 200,
    )
