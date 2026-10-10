"""یادآور گزارش‌های ورودی الزامی (۴ فایل ماهانهٔ «گزارش هدف اصلی»).

Shared by bot (settings UI + scheduler tick in ``main.py``) and web
(``/settings/reminders``). Settings live in ``bot_settings`` as JSON so both
processes read the same values; the web panel never calls Bale directly — its
«ارسال همین حالا» sets a flag that the bot's tick picks up (≤ ~1 دقیقه).

Rules (Jalali calendar, Asia/Tehran):
  • ماه‌های الزامی = ``window_months`` ماهِ کامل قبل از ماه جاری
    (مثلاً امروز ۱۳ مهر ۱۴۰۵ و window=3 → تیر، مرداد، شهریور ۱۴۰۵).
  • مهلت فایل‌های ماه قبل = روز ``due_day`` ماه جاری.
  • due_soon: از ``lead_days`` روز قبل از مهلت تا روز مهلت → یک بار در شروع پنجره
    + یک بار در روز مهلت.
  • overdue: بعد از مهلت (یا ماه‌های قدیمی‌ترِ ناقص) → هر ``repeat_days`` روز یک بار.
  • ارسال فقط از ساعت ``hour`` به بعد و حداکثر یک بار در روز (به‌جز «ارسال همین حالا»).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import jdatetime

from bot.jalali import days_in_jalali_month, format_month_year, tehran_now
from config import ADMIN_ROLES, ROLES

logger = logging.getLogger(__name__)

SETTING_KEY = "mandatory_reminder_config"
STATE_KEY = "mandatory_reminder_state"
FORCE_KEY = "mandatory_reminder_force"

REPORT_LABEL_FA = "۴ فایل ماهانهٔ گزارش هدف اصلی"
FILES_FA = "آمار تولید، مصرف تاندیش بیلت، مصرف تاندیش بلوم، مصرف تاندیش اسلب"

DEFAULTS: dict[str, Any] = {
    # Disabled until owner/manager chooses recipients (no surprise messages on deploy)
    "enabled": False,
    "roles": ["owner", "manager", "responsible_officer"],
    "user_ids": [],
    "due_day": 5,
    "lead_days": 2,
    "repeat_days": 3,
    "hour": 9,
    "window_months": 3,
}
LIMITS: dict[str, tuple[int, int]] = {
    "due_day": (1, 29),
    "lead_days": (0, 15),
    "repeat_days": (1, 30),
    "hour": (0, 23),
    "window_months": (1, 12),
}
FIELD_LABELS_FA = {
    "due_day": "روز مهلت در ماه (برای فایل‌های ماه قبل)",
    "lead_days": "یادآوری چند روز قبل از مهلت",
    "repeat_days": "تکرار یادآوری عقب‌افتاده (هر چند روز)",
    "hour": "ساعت ارسال (تهران)",
    "window_months": "تعداد ماه‌های الزامی اخیر",
}


def can_edit(user: dict | None) -> bool:
    return bool(user and user.get("active") and user.get("role") in ADMIN_ROLES)


# ---------------------------------------------------------------- config
def normalize_config(raw: dict | None) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    raw = raw or {}
    cfg["enabled"] = bool(raw.get("enabled", DEFAULTS["enabled"]))
    roles = raw.get("roles", DEFAULTS["roles"])
    cfg["roles"] = [r for r in ROLES if r in set(roles or [])]
    ids = []
    for v in raw.get("user_ids") or []:
        sv = str(v).strip()
        if sv and sv not in ids:
            ids.append(sv)
    cfg["user_ids"] = ids
    for key, (lo, hi) in LIMITS.items():
        try:
            val = int(raw.get(key, DEFAULTS[key]))
        except (TypeError, ValueError):
            val = int(DEFAULTS[key])
        cfg[key] = max(lo, min(hi, val))
    return cfg


def validate_numbers(values: dict[str, Any]) -> tuple[dict[str, int], list[str]]:
    """Strict validation for user input; returns (clean, errors_fa)."""
    clean: dict[str, int] = {}
    errors: list[str] = []
    for key, (lo, hi) in LIMITS.items():
        if key not in values:
            continue
        try:
            val = int(str(values[key]).strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
        except (TypeError, ValueError):
            errors.append(f"{FIELD_LABELS_FA[key]}: عدد صحیح وارد کنید.")
            continue
        if not lo <= val <= hi:
            errors.append(f"{FIELD_LABELS_FA[key]}: باید بین {lo} و {hi} باشد.")
            continue
        clean[key] = val
    return clean, errors


def load_config(db: Any) -> dict[str, Any]:
    try:
        raw = db.get_setting(SETTING_KEY)
        return normalize_config(json.loads(raw) if raw else None)
    except Exception:  # noqa: BLE001
        logger.exception("reminder config load failed")
        return normalize_config(None)


def save_config(db: Any, cfg: dict[str, Any], *, updated_by: Any = None) -> dict[str, Any]:
    clean = normalize_config(cfg)
    db.set_setting(SETTING_KEY, json.dumps(clean, ensure_ascii=False), updated_by=updated_by)
    return clean


def load_state(db: Any) -> dict[str, Any]:
    try:
        raw = db.get_setting(STATE_KEY)
        return json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        return {}


def save_state(db: Any, state: dict[str, Any]) -> None:
    db.set_setting(STATE_KEY, json.dumps(state, ensure_ascii=False), updated_by="scheduler")


def request_force_send(db: Any, *, requested_by: Any) -> None:
    db.set_setting(
        FORCE_KEY,
        json.dumps({"by": str(requested_by), "at": tehran_now().isoformat()}),
        updated_by=requested_by,
    )


# ---------------------------------------------------------------- recipients
def resolve_recipients(db: Any, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    roles = set(cfg.get("roles") or [])
    ids = {str(x) for x in cfg.get("user_ids") or []}
    out = []
    for u in db.list_users(active_only=True):
        if u.get("role") in roles or str(u.get("bale_user_id")) in ids:
            out.append(u)
    return out


# ---------------------------------------------------------------- status
def _prev_month(y: int, m: int, k: int = 1) -> tuple[int, int]:
    idx = y * 12 + (m - 1) - k
    return idx // 12, idx % 12 + 1


def month_key(y: int, m: int) -> str:
    """Same as ``PeriodKey.key()`` for month periods."""
    return f"m:{y:04d}-{m:02d}"


@dataclass
class ReminderStatus:
    today: jdatetime.date
    required: list[tuple[int, int, bool]]  # (y, m, present) oldest→newest
    prev_month: tuple[int, int]
    prev_present: bool
    due_date: jdatetime.date
    days_to_due: int
    phase: str  # ok | upcoming | due_soon | overdue
    missing: list[tuple[int, int]] = field(default_factory=list)

    @property
    def missing_labels(self) -> list[str]:
        return [format_month_year(y, m) for y, m in self.missing]

    def phase_fa(self) -> str:
        return {
            "ok": "✅ همه ماه‌های الزامی ثبت شده‌اند",
            "upcoming": "⏳ هنوز در مهلت (پیش از پنجرهٔ یادآوری)",
            "due_soon": "⏰ مهلت نزدیک است",
            "overdue": "🚨 عقب‌افتاده",
        }.get(self.phase, self.phase)


def compute_status(db: Any, cfg: dict[str, Any], *, today: jdatetime.date | None = None) -> ReminderStatus:
    today = today or jdatetime.date.fromgregorian(date=tehran_now().date())
    present_keys = set(db.main_goal_month_keys())
    window = int(cfg.get("window_months") or DEFAULTS["window_months"])
    required: list[tuple[int, int, bool]] = []
    for k in range(window, 0, -1):
        y, m = _prev_month(today.year, today.month, k)
        required.append((y, m, month_key(y, m) in present_keys))
    prev = _prev_month(today.year, today.month, 1)
    prev_present = month_key(*prev) in present_keys
    due_day = min(int(cfg.get("due_day") or 5), days_in_jalali_month(today.year, today.month))
    due = jdatetime.date(today.year, today.month, due_day)
    days_to_due = (due.togregorian() - today.togregorian()).days
    missing = [(y, m) for y, m, ok in required if not ok]
    older_missing = [ym for ym in missing if ym != prev]
    lead = int(cfg.get("lead_days") or 0)
    if not missing:
        phase = "ok"
    elif older_missing or (not prev_present and days_to_due < 0):
        phase = "overdue"
    elif not prev_present and days_to_due <= lead:
        phase = "due_soon"
    else:
        phase = "upcoming"
    return ReminderStatus(
        today=today,
        required=required,
        prev_month=prev,
        prev_present=prev_present,
        due_date=due,
        days_to_due=days_to_due,
        phase=phase,
        missing=missing,
    )


def build_reminder_text(status: ReminderStatus, *, forced: bool = False) -> str:
    due_s = f"{status.due_date.year:04d}/{status.due_date.month:02d}/{status.due_date.day:02d}"
    lines = ["🔔 یادآوری گزارش‌های ورودی الزامی", f"({REPORT_LABEL_FA})", ""]
    lines.append(f"وضعیت: {status.phase_fa()}")
    if not status.prev_present:
        prev_label = format_month_year(*status.prev_month)
        if status.days_to_due > 0:
            lines.append(f"مهلت ارسال فایل‌های «{prev_label}»: {due_s} ({status.days_to_due} روز مانده)")
        elif status.days_to_due == 0:
            lines.append(f"مهلت ارسال فایل‌های «{prev_label}»: امروز ({due_s})")
        else:
            lines.append(f"مهلت ارسال فایل‌های «{prev_label}» ({due_s}) {-status.days_to_due} روز گذشته است.")
    if status.missing:
        lines.append("")
        lines.append("ماه‌های ثبت‌نشده:")
        lines.extend(f"• {label}" for label in status.missing_labels)
        lines.append("")
        lines.append(f"ورودی‌های هر ماه: {FILES_FA}.")
        lines.append("مسیر ربات: 🎯 هدف اصلی → 📥 ثبت ورودی ماه (تکی یا 📦 آپلود گروهی)")
        lines.append("یا پنل وب: /reports/main-goal")
    else:
        lines.append("نیازی به اقدام نیست.")
    if forced:
        lines.append("")
        lines.append("(ارسال دستی از تنظیمات یادآور)")
    return "\n".join(lines)


def status_text(db: Any, cfg: dict[str, Any] | None = None) -> str:
    """Settings-screen summary (config + recipients + current status)."""
    cfg = cfg or load_config(db)
    st = compute_status(db, cfg)
    recips = resolve_recipients(db, cfg)
    state = load_state(db)
    roles_fa = "، ".join(ROLES.get(r, r) for r in cfg["roles"]) or "—"
    lines = [
        "🔔 یادآور گزارش‌های الزامی",
        f"وضعیت یادآور: {'✅ فعال' if cfg['enabled'] else '⏸ غیرفعال'}",
        f"نقش‌های دریافت‌کننده: {roles_fa}",
        f"کاربران خاص: {len(cfg['user_ids'])} نفر",
        f"دریافت‌کنندگان فعلی ({len(recips)}): "
        + ("، ".join((u.get('display_name') or str(u['bale_user_id'])) for u in recips) or "—"),
        "",
        f"• {FIELD_LABELS_FA['due_day']}: {cfg['due_day']}",
        f"• {FIELD_LABELS_FA['lead_days']}: {cfg['lead_days']}",
        f"• {FIELD_LABELS_FA['repeat_days']}: {cfg['repeat_days']}",
        f"• {FIELD_LABELS_FA['hour']}: {cfg['hour']}",
        f"• {FIELD_LABELS_FA['window_months']}: {cfg['window_months']}",
        "",
        f"ماه‌های الزامی: "
        + "، ".join(f"{format_month_year(y, m)} {'✅' if ok else '❌'}" for y, m, ok in st.required),
        f"وضعیت فعلی: {st.phase_fa()}",
    ]
    last = state.get("last_result")
    if last:
        lines.append(
            f"آخرین ارسال: {last.get('at_fa') or last.get('at')} — موفق {last.get('sent', 0)}، "
            f"ناموفق {len(last.get('failed') or [])}{' (دستی)' if last.get('forced') else ''}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------- scheduling
def should_send(status: ReminderStatus, state: dict[str, Any], cfg: dict[str, Any], now: datetime) -> str | None:
    """Return a reason key if an automatic reminder is due now, else None."""
    if not cfg.get("enabled"):
        return None
    if status.phase not in {"due_soon", "overdue"}:
        return None
    if now.hour < int(cfg.get("hour") or 0):
        return None
    today_s = f"{status.today.year:04d}/{status.today.month:02d}/{status.today.day:02d}"
    if state.get("last_sent_day") == today_s:
        return None
    prev_k = month_key(*status.prev_month)
    if status.phase == "due_soon":
        if state.get("due_soon_for") != prev_k:
            return "due_soon_start"
        if status.days_to_due == 0 and state.get("due_day_for") != prev_k:
            return "due_day"
        return None
    # overdue
    last = state.get("last_overdue_greg")
    if not last:
        return "overdue"
    try:
        last_d = datetime.fromisoformat(last).date()
    except ValueError:
        return "overdue"
    if (now.date() - last_d).days >= int(cfg.get("repeat_days") or 1):
        return "overdue"
    return None


@dataclass
class SendResult:
    sent: int
    failed: list[str]
    recipients: int
    text: str
    reason: str | None


def send_reminder(
    client: Any,
    db: Any,
    *,
    forced: bool = False,
    reason: str | None = None,
    cfg: dict[str, Any] | None = None,
    status: ReminderStatus | None = None,
    now: datetime | None = None,
) -> SendResult:
    cfg = cfg or load_config(db)
    status = status or compute_status(db, cfg)
    now = now or tehran_now()
    text = build_reminder_text(status, forced=forced)
    recips = resolve_recipients(db, cfg)
    sent = 0
    failed: list[str] = []
    for u in recips:
        uid = str(u["bale_user_id"])
        try:
            client.send_message(uid, text)
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("reminder send failed to %s: %s", uid, exc)
            failed.append(uid)
    state = load_state(db)
    today_s = f"{status.today.year:04d}/{status.today.month:02d}/{status.today.day:02d}"
    if not forced:
        state["last_sent_day"] = today_s
        prev_k = month_key(*status.prev_month)
        if reason == "due_soon_start":
            state["due_soon_for"] = prev_k
        elif reason == "due_day":
            state["due_day_for"] = prev_k
            state["due_soon_for"] = prev_k
        elif reason == "overdue":
            state["last_overdue_greg"] = now.date().isoformat()
    state["last_result"] = {
        "at": now.isoformat(),
        "at_fa": f"{today_s} {now.hour:02d}:{now.minute:02d}",
        "sent": sent,
        "failed": failed,
        "recipients": len(recips),
        "phase": status.phase,
        "reason": reason,
        "forced": forced,
    }
    save_state(db, state)
    logger.info("reminder sent=%s failed=%s reason=%s forced=%s", sent, len(failed), reason, forced)
    return SendResult(sent=sent, failed=failed, recipients=len(recips), text=text, reason=reason)


def tick(client: Any, db: Any, *, now: datetime | None = None) -> SendResult | None:
    """Called periodically by the bot process. Never raises."""
    try:
        now = now or tehran_now()
        cfg = load_config(db)
        force_raw = db.get_setting(FORCE_KEY)
        if force_raw:
            db.clear_setting(FORCE_KEY, updated_by="scheduler")
            return send_reminder(client, db, forced=True, reason="forced", cfg=cfg, now=now)
        today = jdatetime.date.fromgregorian(date=now.date())
        status = compute_status(db, cfg, today=today)
        state = load_state(db)
        reason = should_send(status, state, cfg, now)
        if not reason:
            return None
        return send_reminder(client, db, reason=reason, cfg=cfg, status=status, now=now)
    except Exception:  # noqa: BLE001
        logger.exception("mandatory reminder tick failed")
        return None
