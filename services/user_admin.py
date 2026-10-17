"""User management rules shared by the bot («👥 کاربران») and the web (/settings/users)."""
from __future__ import annotations

from typing import Any

from auth.rbac import require_owner


def can_deactivate(db: Any, actor: dict, target_id: str) -> tuple[bool, str]:
    tid = str(target_id).strip()
    if tid == str(actor["bale_user_id"]):
        return False, "نمی‌توانید خودتان را حذف کنید."
    target = db.get_user(tid)
    if not target or not target.get("active"):
        return False, "کاربر فعال با این شناسه یافت نشد."
    if target["role"] == "owner":
        if not require_owner(actor):
            return False, "فقط مالک می‌تواند مالک دیگر را حذف کند."
        if db.count_active_owners() <= 1:
            return False, "نمی‌توان آخرین مالک را حذف کرد."
    return True, ""


def can_set_role(db: Any, actor: dict, target_id: str, role: str) -> tuple[bool, str]:
    target = db.get_user(str(target_id).strip())
    if not target or not target.get("active"):
        return False, "کاربر فعال با این شناسه یافت نشد."
    if (role == "owner" or target["role"] == "owner") and not require_owner(actor):
        return False, "فقط مالک می‌تواند نقش مالک را بدهد یا تغییر دهد."
    if target["role"] == "owner" and role != "owner" and db.count_active_owners() <= 1:
        return False, "نمی‌توان نقش آخرین مالک را تغییر داد."
    return True, ""


def invite_url(token: str, bot_username: str) -> str:
    return f"https://ble.ir/{(bot_username or '').lstrip('@')}?start={token}"
