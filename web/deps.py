"""FastAPI dependencies: Database + session user (same users/roles as Bale bot)."""
from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import Depends, HTTPException, Request, status

from auth.rbac import can_request_materials, require_manager, require_owner
from config import ADMIN_ROLES, FULL_DATA_ROLES
from db.models import Database

_db: Database | None = None


class LoginRequired(Exception):
    """Raised when an unauthenticated user hits a protected page."""


class ForbiddenFa(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def reset_db_singleton() -> None:
    global _db
    _db = None


def current_user_optional(
    request: Request, db: Annotated[Database, Depends(get_db)]
) -> Optional[dict[str, Any]]:
    uid = request.session.get("bale_user_id")
    if not uid:
        return None
    user = db.get_user(uid)
    if not user or not user.get("active"):
        request.session.clear()
        return None
    return user


def current_user(
    request: Request,
    user: Annotated[Optional[dict[str, Any]], Depends(current_user_optional)],
) -> dict[str, Any]:
    if not user:
        raise LoginRequired()
    return user


def deny_technician(user: dict[str, Any] | None) -> bool:
    """Mirror BotApp._deny_technician: technicians may only use site stock."""
    return bool(user and user.get("role") == "technician")


def require_non_technician(
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> dict[str, Any]:
    if deny_technician(user):
        raise ForbiddenFa(
            "دسترسی ندارید؛ فقط ورود موجودی روزانه سایت برای نقش تکنسین فعال است."
        )
    return user


def require_materials_user(
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> dict[str, Any]:
    """Same gate as bot: can_request_materials (owner/manager/responsible_officer)."""
    if not can_request_materials(user):
        raise ForbiddenFa("دسترسی درخواست/برگشت مواد ندارید.")
    return user


def require_admin_web(
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> dict[str, Any]:
    """Owner/manager — same ADMIN_ROLES / require_manager as bot."""
    if not require_manager(user):
        raise ForbiddenFa("فقط مالک یا مدیر می‌توانند ورود وب را تنظیم کنند.")
    return user


def user_can_see_reports(user: dict[str, Any] | None) -> bool:
    return bool(user and user.get("active") and user.get("role") != "technician")


def role_sets_snapshot() -> dict[str, Any]:
    return {
        "admin_roles": sorted(ADMIN_ROLES),
        "full_data_roles": sorted(FULL_DATA_ROLES),
        "require_owner": require_owner,
        "require_manager": require_manager,
        "can_request_materials": can_request_materials,
    }
