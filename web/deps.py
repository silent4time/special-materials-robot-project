"""FastAPI dependencies: Database + session user (same users/roles as Bale bot)."""
from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import Depends, HTTPException, Request, status

from auth.rbac import can_request_materials, require_manager, require_owner
from config import ADMIN_ROLES, FULL_DATA_ROLES
from db.models import Database
from services import permissions as perm

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
    if perm.bound_db() is not _db:
        perm.bind(_db)
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


DENIED_FA = (
    "دسترسی ندارید؛ این بخش برای نقش شما فعال نیست "
    "(تنظیم در «⚙️ تنظیمات ← 🔐 دسترسی نقش‌ها» توسط مالک/مدیر)."
)


def deny_technician(user: dict[str, Any] | None) -> bool:
    """«Field» profile (technician / shift supervisor by default): no data features."""
    return bool(user and perm.is_limited(user))


def require_feature(*features: str):
    """FastAPI dependency: user must have one of ``features`` (services.permissions)."""

    def _dep(user: Annotated[dict[str, Any], Depends(current_user)]) -> dict[str, Any]:
        if not perm.can_any(user, features):
            raise ForbiddenFa(DENIED_FA)
        return user

    return _dep


def require_non_technician(
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> dict[str, Any]:
    """Any report feature (reports page itself)."""
    if not perm.can_any(user, perm.REPORT_FEATURES):
        raise ForbiddenFa(DENIED_FA)
    return user


def require_materials_user(
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> dict[str, Any]:
    if not perm.can_any(user, (perm.MATERIAL_REQUEST, perm.WAREHOUSE_RETURN)):
        raise ForbiddenFa(DENIED_FA)
    return user


def require_catalog_admin(
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> dict[str, Any]:
    """منبع اصلی page: file inputs or main-source edit."""
    if not perm.can_any(user, (perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT)):
        raise ForbiddenFa(DENIED_FA)
    return user


def require_admin_web(
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> dict[str, Any]:
    """Owner/manager — same ADMIN_ROLES / require_manager as bot."""
    if not require_manager(user):
        raise ForbiddenFa("فقط مالک یا مدیر می‌توانند ورود وب را تنظیم کنند.")
    return user


def user_can_see_reports(user: dict[str, Any] | None) -> bool:
    return perm.can_any(user, perm.REPORT_FEATURES)


def role_sets_snapshot() -> dict[str, Any]:
    return {
        "admin_roles": sorted(ADMIN_ROLES),
        "full_data_roles": sorted(FULL_DATA_ROLES),
        "require_owner": require_owner,
        "require_manager": require_manager,
        "can_request_materials": can_request_materials,
    }
