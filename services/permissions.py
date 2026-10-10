"""Single permission service for bot menus/handlers + web pages/routes.

Every menu button, handler gate and web route asks ``can(user, FEATURE)`` instead
of checking role names. Effective permissions = built-in defaults (reproduce the
pre-phase-2 behaviour) overlaid with per-role overrides stored in the DB table
``role_permissions`` (edited from «🔐 دسترسی نقش‌ها» in the bot or
/settings/permissions on the web).

Rules:
* owner always has every feature (locked);
* ``USERS`` and ``ROLE_PERMISSIONS`` are owner/manager only and cannot be toggled;
* ``REPORTS`` and ``SETTINGS`` are derived menus (any child feature enabled).
The bot and web processes each bind their Database with :func:`bind`; overrides are
cached for ``_TTL`` seconds so a change made in one process applies in the other
within a couple of seconds (and immediately in the process that made it).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Iterable

from config import ROLES

OWNER = "owner"
MANAGER = "manager"
OFFICER = "responsible_officer"
TECH = "technician"
SHIFT = "shift_supervisor"

ALL_ROLES = frozenset(ROLES)
ADMINS = frozenset({OWNER, MANAGER})
STAFF = frozenset({OWNER, MANAGER, OFFICER})
FIELD = frozenset({TECH, SHIFT})  # post-casting tundish report + daily site stock

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
MAIN_GOAL_DELETE = "main_goal_delete"  # delete a stored month
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
    MAIN_GOAL_DELETE: "حذف ماه ذخیره‌شدهٔ هدف اصلی",
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
    MAIN_GOAL_DELETE: ADMINS,
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

REPORT_FEATURES = (
    REPORT_DAILY, REPORT_COMPREHENSIVE, REPORT_PERIOD, REPORT_SHORT_COVER, REPORT_CRITICAL,
    REPORT_N_TUNDISH, REPORT_SURPLUS, REPORT_INBOUND, REPORT_MONTHLY_SUMMARY,
)
SETTINGS_FEATURES = (
    USERS, USER_ACTIVITY, ROLE_PERMISSIONS, TUNDISH_REPORT_SETTINGS, REMINDERS, STOCK_GROUP, APPEARANCE,
)
DERIVED = {REPORTS: REPORT_FEATURES, SETTINGS: SETTINGS_FEATURES}
# owner/manager only, never toggled (spec: users & role-permission pages)
LOCKED_ADMIN_ONLY = frozenset({USERS, ROLE_PERMISSIONS})
LOCKED_ADMIN_ONLY_NOTE_FA = "فقط مالک و مدیر (قفل)"
ALL_FEATURES = tuple(_DEFAULTS)
# Order shown in the bot inline checklist and on the web page.
TOGGLABLE_FEATURES: tuple[str, ...] = tuple(
    f for f in ALL_FEATURES if f not in DERIVED and f not in LOCKED_ADMIN_ONLY
)
EDITABLE_ROLES: tuple[str, ...] = tuple(r for r in ROLES if r != OWNER)


def default_roles(feature: str) -> frozenset[str]:
    return _DEFAULTS.get(feature, frozenset())


def default_allowed(role: str, feature: str) -> bool:
    return role == OWNER or role in _DEFAULTS.get(feature, frozenset())


# ---------------------------------------------------------------- DB overrides
_TTL = 2.0
_lock = threading.Lock()
_db: Any = None
_cache: dict[str, dict[str, bool]] | None = None
_cache_at = 0.0


def bind(db: Any) -> None:
    """Use ``db`` (a db.models.Database) for overrides in this process."""
    global _db
    with _lock:
        _db = db
    invalidate()


def bound_db() -> Any:
    return _db


def invalidate() -> None:
    global _cache, _cache_at
    with _lock:
        _cache = None
        _cache_at = 0.0


def _overrides() -> dict[str, dict[str, bool]]:
    global _cache, _cache_at
    now = time.monotonic()
    with _lock:
        if _cache is not None and now - _cache_at < _TTL:
            return _cache
        db = _db
    data: dict[str, dict[str, bool]] = {}
    if db is not None:
        try:
            for row in db.list_role_permission_overrides():
                data.setdefault(str(row["role"]), {})[str(row["feature"])] = bool(row["allowed"])
        except Exception:  # noqa: BLE001 — table missing on a very old DB → defaults
            data = {}
    with _lock:
        _cache, _cache_at = data, now
    return data


def role_features(role: str | None) -> set[str]:
    """Effective features for ``role`` (defaults + DB overrides, derived menus)."""
    if not role:
        return set()
    if role == OWNER:
        return set(ALL_FEATURES)
    ov = _overrides().get(role, {})
    out: set[str] = set()
    for f in ALL_FEATURES:
        if f in DERIVED:
            continue
        if f in LOCKED_ADMIN_ONLY:
            if role in ADMINS:
                out.add(f)
            continue
        allowed = ov.get(f, role in _DEFAULTS.get(f, frozenset()))
        if allowed:
            out.add(f)
    for menu, children in DERIVED.items():
        if out.intersection(children):
            out.add(menu)
    return out


def can(user: dict | None, feature: str) -> bool:
    if not user or not user.get("active", 1):
        return False
    return feature in role_features(user.get("role"))


def can_any(user: dict | None, features: Iterable[str]) -> bool:
    feats = role_features((user or {}).get("role")) if user and user.get("active", 1) else set()
    return any(f in feats for f in features)


def can_edit_permissions(user: dict | None) -> bool:
    return can(user, ROLE_PERMISSIONS)


def is_limited(user: dict | None) -> bool:
    """«Field» profile: no reports / file inputs / requests (technician-like)."""
    return not can_any(
        user, (*REPORT_FEATURES, FILE_INPUTS, MAIN_SOURCE_EDIT, MATERIAL_REQUEST, WAREHOUSE_RETURN, MAIN_GOAL)
    )


def set_permission(db: Any, actor: dict, role: str, feature: str, allowed: bool) -> tuple[bool, str]:
    """Validate + store one toggle; log activity. Returns (ok, Persian message)."""
    if not can_edit_permissions(actor):
        return False, "فقط مالک یا مدیر می‌تواند دسترسی نقش‌ها را تغییر دهد."
    if role not in EDITABLE_ROLES:
        return False, "دسترسی‌های مالک قابل تغییر نیست." if role == OWNER else "نقش نامعتبر است."
    if feature not in TOGGLABLE_FEATURES:
        return False, "این مورد قابل تغییر نیست."
    before = feature in role_features(role)
    db.set_role_permission(role, feature, bool(allowed), updated_by=str(actor.get("bale_user_id") or ""))
    invalidate()
    if before != bool(allowed):
        _log(db, actor, role, feature, bool(allowed))
    state = "فعال" if allowed else "غیرفعال"
    return True, f"«{FEATURE_LABEL_FA.get(feature, feature)}» برای «{ROLES.get(role, role)}» {state} شد."


def toggle(db: Any, actor: dict, role: str, feature: str) -> tuple[bool, str]:
    return set_permission(db, actor, role, feature, feature not in role_features(role))


def reset_role(db: Any, actor: dict, role: str) -> tuple[bool, str]:
    if not can_edit_permissions(actor):
        return False, "فقط مالک یا مدیر می‌تواند دسترسی نقش‌ها را تغییر دهد."
    if role not in EDITABLE_ROLES:
        return False, "دسترسی‌های مالک قابل تغییر نیست."
    db.reset_role_permissions(role)
    invalidate()
    try:
        from bot.activity import log_activity

        log_activity(db, actor, "role_permissions_reset", role_fa=ROLES.get(role, role))
    except Exception:  # noqa: BLE001
        pass
    return True, f"دسترسی‌های «{ROLES.get(role, role)}» به پیش‌فرض برگشت."


def _log(db: Any, actor: dict, role: str, feature: str, allowed: bool) -> None:
    try:
        from bot.activity import log_activity

        log_activity(
            db, actor, "role_permission_changed",
            role_fa=ROLES.get(role, role), feature_fa=FEATURE_LABEL_FA.get(feature, feature),
            state_fa="فعال" if allowed else "غیرفعال", role=role, feature=feature,
        )
    except Exception:  # noqa: BLE001
        pass


def matrix_rows(role: str) -> list[dict[str, Any]]:
    """[{feature, label, allowed, default, locked}] for one role (bot + web)."""
    feats = role_features(role)
    rows = []
    for f in TOGGLABLE_FEATURES:
        rows.append({
            "feature": f,
            "label": FEATURE_LABEL_FA.get(f, f),
            "allowed": f in feats,
            "default": default_allowed(role, f),
            "locked": role == OWNER,
        })
    return rows


def matrix_text_fa() -> str:
    """Overview of effective permissions per feature."""
    lines = []
    for f in ALL_FEATURES:
        roles = [ROLES[r] for r in ROLES if f in role_features(r)]
        extra = f" ({LOCKED_ADMIN_ONLY_NOTE_FA})" if f in LOCKED_ADMIN_ONLY else ""
        lines.append(f"• {FEATURE_LABEL_FA.get(f, f)}: {'، '.join(roles) or '—'}{extra}")
    return "\n".join(lines)
