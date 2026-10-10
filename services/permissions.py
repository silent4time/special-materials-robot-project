"""Single permission service for bot menus + web pages (phase-1 defaults).

Every menu button / web page asks ``can(user, FEATURE)`` instead of checking role
names inline. Phase 1 ships hard-coded defaults that reproduce current behaviour
(plus the approved cleanup: «📋 فعالیت کاربران» owner/manager only). Phase 2
(«🔐 دسترسی نقش‌ها») only has to replace :func:`role_features` with a DB-backed
lookup (defaults → overrides) — call sites stay unchanged.
"""
from __future__ import annotations

from typing import Iterable

from config import ROLES

OWNER = "owner"
MANAGER = "manager"
OFFICER = "responsible_officer"
TECH = "technician"

ALL_ROLES = frozenset(ROLES)
ADMINS = frozenset({OWNER, MANAGER})
STAFF = frozenset({OWNER, MANAGER, OFFICER})

# ---------------------------------------------------------------- feature keys
SITE_STOCK = "site_stock"
TUNDISH_REPORT = "tundish_report"
MATERIAL_REQUEST = "material_request"
WAREHOUSE_RETURN = "warehouse_return"
REPORTS = "reports"  # «📊 گزارش‌ها» menu itself
REPORT_DAILY = "report.daily"
REPORT_COMPREHENSIVE = "report.comprehensive"
REPORT_PERIOD = "report.period"
REPORT_SHORT_COVER = "report.short_cover"
REPORT_CRITICAL = "report.critical"
REPORT_N_TUNDISH = "report.n_tundish"
REPORT_SURPLUS = "report.surplus"
REPORT_INBOUND = "report.inbound"
REPORT_MONTHLY_SUMMARY = "report.monthly_summary"
MAIN_GOAL = "main_goal"
FILE_INPUTS = "file_inputs"  # «📤 ورود فایل‌ها» (stock update / monthly)
MAIN_SOURCE_EDIT = "main_source_edit"  # منبع اصلی submenu (replace/add/edit/codes)
SETTINGS = "settings"
USERS = "users"
USER_ACTIVITY = "user_activity"
ROLE_PERMISSIONS = "role_permissions"
TUNDISH_REPORT_SETTINGS = "tundish_report_settings"
REMINDERS = "reminders"
STOCK_GROUP = "stock_group"
APPEARANCE = "appearance"

FEATURE_LABEL_FA: dict[str, str] = {
    SITE_STOCK: "موجودی روزانه سایت",
    TUNDISH_REPORT: "گزارش تاندیش",
    MATERIAL_REQUEST: "درخواست مواد",
    WAREHOUSE_RETURN: "برگشت به انبار",
    REPORTS: "منوی گزارش‌ها",
    REPORT_DAILY: "گزارش مصرف روزانه",
    REPORT_COMPREHENSIVE: "گزارش جامع",
    REPORT_PERIOD: "گزارش مصرف بازه‌ای",
    REPORT_SHORT_COVER: "پوشش کوتاه‌مدت موجودی سایت",
    REPORT_CRITICAL: "اقلام بحرانی (نیاز ۳/۶ ماه)",
    REPORT_N_TUNDISH: "نیاز مواد برای N تاندیش",
    REPORT_SURPLUS: "گزارش مواد مازاد",
    REPORT_INBOUND: "گزارش اقلام ورودی به انبار",
    REPORT_MONTHLY_SUMMARY: "خلاصه مصرف ماهیانه",
    MAIN_GOAL: "هدف اصلی",
    FILE_INPUTS: "ورود فایل‌ها",
    MAIN_SOURCE_EDIT: "ویرایش منبع اصلی",
    SETTINGS: "تنظیمات",
    USERS: "کاربران",
    USER_ACTIVITY: "فعالیت کاربران",
    ROLE_PERMISSIONS: "دسترسی نقش‌ها",
    TUNDISH_REPORT_SETTINGS: "اقلام فرم گزارش تاندیش",
    REMINDERS: "یادآورها",
    STOCK_GROUP: "گروه گزارش موجودی",
    APPEARANCE: "ظاهر ربات",
}

_DEFAULTS: dict[str, frozenset[str]] = {
    SITE_STOCK: ALL_ROLES,
    TUNDISH_REPORT: ALL_ROLES,
    MATERIAL_REQUEST: STAFF,
    WAREHOUSE_RETURN: STAFF,
    REPORTS: STAFF,
    REPORT_DAILY: STAFF,
    REPORT_COMPREHENSIVE: STAFF,
    REPORT_PERIOD: STAFF,
    REPORT_SHORT_COVER: STAFF,
    REPORT_CRITICAL: STAFF,
    REPORT_N_TUNDISH: STAFF,
    REPORT_SURPLUS: STAFF,
    REPORT_INBOUND: STAFF,
    REPORT_MONTHLY_SUMMARY: STAFF,
    MAIN_GOAL: STAFF,
    FILE_INPUTS: STAFF,
    MAIN_SOURCE_EDIT: STAFF,
    SETTINGS: ADMINS,
    USERS: ADMINS,
    USER_ACTIVITY: ADMINS,
    ROLE_PERMISSIONS: ADMINS,
    TUNDISH_REPORT_SETTINGS: ADMINS,
    REMINDERS: ADMINS,
    STOCK_GROUP: ADMINS,
    APPEARANCE: ADMINS,
}

# Features that can never be removed from owner, nor granted beyond owner/manager (phase 2).
LOCKED_ADMIN_ONLY = frozenset({USERS, ROLE_PERMISSIONS})
ALL_FEATURES = tuple(_DEFAULTS)


def default_roles(feature: str) -> frozenset[str]:
    return _DEFAULTS.get(feature, frozenset())


def role_features(role: str | None) -> set[str]:
    """Features enabled for ``role``. Phase 2: merge DB overrides here."""
    if not role:
        return set()
    if role == OWNER:
        return set(ALL_FEATURES)
    return {f for f, roles in _DEFAULTS.items() if role in roles}


def can(user: dict | None, feature: str) -> bool:
    if not user or not user.get("active", 1):
        return False
    return feature in role_features(user.get("role"))


def can_any(user: dict | None, features: Iterable[str]) -> bool:
    return any(can(user, f) for f in features)


def matrix_text_fa() -> str:
    """Read-only overview of the current defaults (shown under «🔐 دسترسی نقش‌ها»)."""
    lines = []
    for f in ALL_FEATURES:
        roles = [ROLES[r] for r in ROLES if f in role_features(r)]
        lines.append(f"• {FEATURE_LABEL_FA.get(f, f)}: {'، '.join(roles) or '—'}")
    return "\n".join(lines)
