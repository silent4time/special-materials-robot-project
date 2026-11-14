"""ارسال زمان‌بندی‌شدهٔ گزارش‌های اقلام پیمانکار برای «نماینده پیمانکار».

Shared by the bot («⚙️ تنظیمات / 👥 کاربران → 👷 نماینده پیمانکار») and the web
(``/settings/contractor-rep``). Config and last-send state live in ``bot_settings``.
The web panel never talks to Bale: «ارسال آزمایشی» sets a force flag that the bot
tick in ``main.py`` picks up (≤ ~1 minute), same pattern as ``mandatory_reminders``.

Weekdays use ``jdatetime.date.weekday()`` (شنبه=0 … جمعه=6), Asia/Tehran.
Contractor items = existing id rule (chars 5–8 == «0000», ``SEGMENT_CONTRACTOR``).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import jdatetime

from analytics.critical_items import SEGMENT_CONTRACTOR
from auth.rbac import role_label
from bot.jalali import TEHRAN, tehran_now
from config import ADMIN_ROLES, BOT_USERNAME, FULL_DATA_ROLES
from services import permissions as perm
from services import user_admin
from services.backup import BALE_MAX_UPLOAD, human_size

logger = logging.getLogger(__name__)

CONTRACTOR_ROLE = "contractor_rep"

SETTING_KEY = "contractor_rep_config"
STATE_KEY = "contractor_rep_state"
FORCE_KEY = "contractor_rep_force"

WEEKDAY_FA: tuple[str, ...] = (
    "شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه",
)
REPORT_STOCK_TODAY = "stock_today"
REPORT_STOCK_MONTH_END = "stock_month_end"
REPORT_CRITICAL = "critical"
REPORT_KEYS: tuple[str, ...] = (REPORT_STOCK_TODAY, REPORT_STOCK_MONTH_END, REPORT_CRITICAL)
REPORT_LABEL_FA: dict[str, str] = {
    REPORT_STOCK_TODAY: "موجودی انبار — اقلام پیمانکار (گزارش روز)",
    REPORT_STOCK_MONTH_END: "موجودی پایان ماه گذشته — پیمانکار",
    REPORT_CRITICAL: "اقلام بحرانی — پیمانکار",
}
FORMATS: tuple[str, ...] = ("pdf", "xlsx")
FORMAT_LABEL_FA: dict[str, str] = {"pdf": "PDF", "xlsx": "اکسل"}

_FA_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_EN_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "user_ids": [],
    "weekdays": [0],  # شنبه
    "hour": 8,
    "minute": 0,
    "reports": [REPORT_STOCK_TODAY],
    "formats": ["pdf", "xlsx"],
}

RECEIVE_ONLY_FA = (
    "نقش شما «نماینده پیمانکار» است.\n"
    "به‌صورت پیش‌فرض فقط گزارش‌های زمان‌بندی‌شدهٔ موجودی اقلام پیمانکار را "
    "دریافت می‌کنید و منوی عملیاتی ندارید.\n"
    "برای توضیح کوتاه، «❓ راهنما» را بزنید."
)

START_WARN_FA = (
    "⚠ تا وقتی نماینده ربات را در بله «استارت» نکرده باشد، پیام و فایل به دستش نمی‌رسد."
)

NOTE_NO_SOURCE_FA = "منبع اصلی بارگذاری نشده است؛ گزارش موجودی اقلام پیمانکار ساخته نشد."
NOTE_NO_SNAPSHOT_FA = (
    "برای موجودی پایان ماه گذشته هیچ ثبت موجودی انبار ذخیره نشده است؛ این بخش ارسال نشد."
)
NOTE_NO_CRITICAL_FA = "گزارش اقلام بحرانی پیمانکار در حال حاضر قابل تهیه نیست."


def fa_digits(value: Any) -> str:
    return str(value).translate(_EN_TO_FA)


def can_edit(user: dict | None) -> bool:
    """Owner/manager only (locked «کاربران» feature)."""
    return perm.can(user, perm.USERS)


def is_contractor_rep(user: dict | None) -> bool:
    return bool(user and user.get("active", 1) and user.get("role") == CONTRACTOR_ROLE)


def normalize_bale_id(raw: object) -> str | None:
    """Return a digits-only Bale user id, or None if invalid."""
    text = str(raw or "").strip().translate(_FA_TO_EN)
    text = re.sub(r"[\s\u200c\u200f\u200e\-_,.]", "", text)
    if not text.isdigit() or not (3 <= len(text) <= 20):
        return None
    if set(text) == {"0"}:
        return None
    return text


def parse_clock(raw: object) -> tuple[int, int] | None:
    """Parse «۸:۰۰» / «8:00» / «8» into (hour, minute), or None."""
    text = str(raw or "").strip().translate(_FA_TO_EN)
    text = text.replace("٫", ":").replace(".", ":").replace(" ", "")
    if re.fullmatch(r"\d{1,2}", text):
        hour = int(text)
        minute = 0
    else:
        m = re.fullmatch(r"(\d{1,2}):(\d{1,2})", text)
        if not m:
            return None
        hour, minute = int(m.group(1)), int(m.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def clock_fa(hour: int, minute: int) -> str:
    return fa_digits(f"{int(hour):02d}:{int(minute):02d}")


def weekday_fa(idx: int) -> str:
    if 0 <= int(idx) < len(WEEKDAY_FA):
        return WEEKDAY_FA[int(idx)]
    return str(idx)


def normalize_config(raw: dict | None) -> dict[str, Any]:
    src = dict(DEFAULTS)
    raw = raw or {}
    src["enabled"] = bool(raw.get("enabled", DEFAULTS["enabled"]))
    ids: list[str] = []
    for v in raw.get("user_ids") or []:
        uid = normalize_bale_id(v)
        if uid and uid not in ids:
            ids.append(uid)
    src["user_ids"] = ids
    days: list[int] = []
    for v in raw.get("weekdays") if raw.get("weekdays") is not None else DEFAULTS["weekdays"]:
        try:
            d = int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= d <= 6 and d not in days:
            days.append(d)
    src["weekdays"] = days or list(DEFAULTS["weekdays"])
    try:
        hour = int(raw.get("hour", DEFAULTS["hour"]))
    except (TypeError, ValueError):
        hour = int(DEFAULTS["hour"])
    try:
        minute = int(raw.get("minute", DEFAULTS["minute"]))
    except (TypeError, ValueError):
        minute = int(DEFAULTS["minute"])
    src["hour"] = max(0, min(23, hour))
    src["minute"] = max(0, min(59, minute))
    reps: list[str] = []
    for k in raw.get("reports") if raw.get("reports") is not None else DEFAULTS["reports"]:
        if k in REPORT_KEYS and k not in reps:
            reps.append(str(k))
    src["reports"] = reps or list(DEFAULTS["reports"])
    fmts: list[str] = []
    for k in raw.get("formats") if raw.get("formats") is not None else DEFAULTS["formats"]:
        if k in FORMATS and k not in fmts:
            fmts.append(str(k))
    src["formats"] = fmts or list(DEFAULTS["formats"])
    return src


def load_config(db: Any) -> dict[str, Any]:
    try:
        raw = db.get_setting(SETTING_KEY)
        return normalize_config(json.loads(raw) if raw else None)
    except Exception:  # noqa: BLE001
        logger.exception("contractor_rep config load failed")
        return normalize_config(None)


def save_config(db: Any, cfg: dict[str, Any], *, updated_by: str | int | None = None) -> dict[str, Any]:
    clean = normalize_config(cfg)
    db.set_setting(SETTING_KEY, json.dumps(clean, ensure_ascii=False), updated_by=updated_by)
    return clean


def load_state(db: Any) -> dict[str, Any]:
    try:
        raw = db.get_setting(STATE_KEY)
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        logger.exception("contractor_rep state load failed")
        return {}


def save_state(db: Any, state: dict[str, Any], *, updated_by: str | int | None = "scheduler") -> None:
    db.set_setting(STATE_KEY, json.dumps(state, ensure_ascii=False), updated_by=updated_by)


def request_force_send(db: Any, *, requested_by: str | int | None = None) -> None:
    db.set_setting(FORCE_KEY, json.dumps({"by": str(requested_by or ""), "at": tehran_now().isoformat()},
                                         ensure_ascii=False), updated_by=requested_by)


def clear_force(db: Any) -> None:
    db.clear_setting(FORCE_KEY, updated_by="scheduler")


def jalali_day_key(now: datetime | None = None) -> str:
    now = now or tehran_now()
    today = jdatetime.date.fromgregorian(date=now.date())
    return f"{today.year:04d}/{today.month:02d}/{today.day:02d}"


def weekdays_fa(cfg: dict[str, Any]) -> str:
    days = [weekday_fa(d) for d in cfg.get("weekdays") or []]
    return "، ".join(days) if days else "—"


def reports_fa(cfg: dict[str, Any]) -> str:
    labs = [REPORT_LABEL_FA[k] for k in cfg.get("reports") or [] if k in REPORT_LABEL_FA]
    return "، ".join(labs) if labs else "—"


def formats_fa(cfg: dict[str, Any]) -> str:
    labs = [FORMAT_LABEL_FA[k] for k in cfg.get("formats") or [] if k in FORMAT_LABEL_FA]
    return " و ".join(labs) if labs else "—"


def resolve_recipients(db: Any, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = cfg or load_config(db)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for uid in cfg.get("user_ids") or []:
        if uid in seen:
            continue
        seen.add(uid)
        user = db.get_user(uid)
        if user and user.get("active"):
            out.append(user)
    return out


def missing_ids(db: Any, cfg: dict[str, Any] | None = None) -> list[str]:
    """IDs in the send list that are missing or inactive (will not receive)."""
    cfg = cfg or load_config(db)
    missing: list[str] = []
    for uid in cfg.get("user_ids") or []:
        user = db.get_user(uid)
        if not user or not user.get("active"):
            missing.append(uid)
    return missing


def full_data_actor(db: Any) -> dict[str, Any]:
    """Build reports with a plant-wide actor, never the representative's scope."""
    for u in db.list_users(active_only=True):
        if u.get("role") in FULL_DATA_ROLES:
            return u
    return {"role": "owner", "active": 1, "bale_user_id": "system", "display_name": "سامانه"}


def admin_recipients(db: Any) -> list[dict[str, Any]]:
    return [u for u in db.list_users(active_only=True) if u.get("role") in ADMIN_ROLES]


def add_representative(db: Any, actor: dict, raw_id: object) -> tuple[bool, str, str | None]:
    """Validate ID, upsert as contractor_rep (if safe), append to the send list, make a 7-day invite.

    Returns (ok, persian_message, invite_url_or_none). The invite token is never logged.
    """
    uid = normalize_bale_id(raw_id)
    if not uid:
        return False, "شناسهٔ بله نامعتبر است. فقط رقم وارد کنید (دست‌کم ۳ رقم).", None
    existing = db.get_user(uid)
    if existing and existing.get("active") and existing.get("role") not in {CONTRACTOR_ROLE, None}:
        if existing["role"] != CONTRACTOR_ROLE:
            return (
                False,
                f"این شناسه از قبل با نقش «{role_label(existing['role'])}» ثبت شده است؛ "
                "نقش را از «✏️ اصلاح نقش کاربر» تغییر دهید یا شناسهٔ دیگری وارد کنید.",
                None,
            )
    if existing and existing.get("role") != CONTRACTOR_ROLE and existing.get("active"):
        return (
            False,
            f"این شناسه از قبل با نقش «{role_label(existing['role'])}» ثبت شده است.",
            None,
        )
    db.upsert_user(
        uid,
        role=CONTRACTOR_ROLE,
        display_name=(existing or {}).get("display_name") or uid,
        scope=None,
        active=True,
    )
    cfg = load_config(db)
    if uid not in cfg["user_ids"]:
        cfg["user_ids"].append(uid)
        save_config(db, cfg, updated_by=actor.get("bale_user_id"))
    invite_url = None
    try:
        invite = db.create_invite(
            role=CONTRACTOR_ROLE,
            scope=None,
            created_by=str(actor.get("bale_user_id") or ""),
            expires_days=7,
        )
        invite_url = user_admin.invite_url(invite["token"], BOT_USERNAME)
    except Exception:  # noqa: BLE001
        logger.exception("contractor_rep invite create failed")
    already = "در فهرست ارسال بود و به‌روز شد" if existing else "ثبت شد"
    lines = [
        f"✅ شناسهٔ {fa_digits(uid)} به‌عنوان «نماینده پیمانکار» {already}.",
        START_WARN_FA,
    ]
    if invite_url:
        lines.append("لینک دعوت ۷ روزه (برای استارت ربات برایشان بفرستید):")
        lines.append(invite_url)
    else:
        lines.append("اگر قبلاً ربات را استارت کرده‌اند، همین کافی است؛ در غیر این صورت از «➕ اضافه کردن کاربر» لینک دعوت بسازید.")
    return True, "\n".join(lines), invite_url


def remove_representative(db: Any, actor: dict, raw_id: object, *, deactivate: bool = False) -> tuple[bool, str]:
    uid = normalize_bale_id(raw_id)
    if not uid:
        return False, "شناسهٔ بله نامعتبر است."
    cfg = load_config(db)
    if uid not in cfg["user_ids"]:
        return False, f"شناسهٔ {fa_digits(uid)} در فهرست ارسال نیست."
    cfg["user_ids"] = [i for i in cfg["user_ids"] if i != uid]
    save_config(db, cfg, updated_by=actor.get("bale_user_id"))
    extra = ""
    if deactivate:
        user = db.get_user(uid)
        if user and user.get("role") == CONTRACTOR_ROLE and user.get("active"):
            db.upsert_user(uid, role=CONTRACTOR_ROLE, display_name=user.get("display_name"),
                           scope=user.get("scope"), active=False)
            extra = " و کاربر غیرفعال شد"
    return True, f"✅ شناسهٔ {fa_digits(uid)} از فهرست ارسال حذف شد{extra}."


def last_result_fa(state: dict[str, Any] | None) -> str:
    last = (state or {}).get("last_result") or {}
    if not last:
        return "هنوز ارسالی انجام نشده است."
    at = last.get("at_fa") or last.get("at") or "—"
    sent = last.get("sent", 0)
    failed = last.get("failed") or []
    kind = "آزمایشی" if last.get("forced") else "زمان‌بندی‌شده"
    line = f"آخرین ارسال ({kind}): {at} — موفق {fa_digits(sent)}، ناموفق {fa_digits(len(failed))}"
    if failed:
        line += " (شناسه: " + "، ".join(fa_digits(i) for i in failed) + ")"
    notes = last.get("notes") or []
    if notes:
        line += "\n" + "\n".join(f"• {n}" for n in notes)
    return line


def status_text(db: Any) -> str:
    """Clear summary for the bot screen and as the web intro's source of truth."""
    cfg = load_config(db)
    state = load_state(db)
    recips = resolve_recipients(db, cfg)
    missing = missing_ids(db, cfg)
    en = "✅ فعال" if cfg["enabled"] else "⏸ غیرفعال — ارسال خودکار انجام نمی‌شود"
    lines = [
        "👷 نماینده پیمانکار — تنظیمات ارسال خودکار",
        "",
        f"وضعیت: {en}",
        f"ساعت ارسال (تهران): {clock_fa(cfg['hour'], cfg['minute'])}",
        f"روزهای ارسال: {weekdays_fa(cfg)}",
        f"گزارش‌ها: {reports_fa(cfg)}",
        f"قالب: {formats_fa(cfg)}",
        f"نمایندگان در فهرست: {fa_digits(len(cfg['user_ids']))}",
    ]
    if recips:
        for u in recips:
            lines.append(f"  • {fa_digits(u['bale_user_id'])} — {u.get('display_name') or 'بدون نام'}")
    elif cfg["user_ids"]:
        lines.append("  (هیچ نمایندهٔ فعال در فهرست نیست)")
    if missing:
        lines.append("شناسه‌های بدون کاربر فعال: " + "، ".join(fa_digits(i) for i in missing))
    lines.append(last_result_fa(state))
    if db.get_setting(FORCE_KEY):
        lines.append("⏳ یک درخواست «ارسال آزمایشی همین حالا» در صف ربات است.")
    lines.append("")
    lines.append(START_WARN_FA)
    return "\n".join(lines)


def should_send(cfg: dict[str, Any], state: dict[str, Any], now: datetime) -> str | None:
    """Return 'scheduled' when an automatic send is due, else None."""
    if not cfg.get("enabled"):
        return None
    if not cfg.get("user_ids"):
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=TEHRAN)
    now_local = now.astimezone(TEHRAN)
    today = jdatetime.date.fromgregorian(date=now_local.date())
    if int(today.weekday()) not in set(int(d) for d in cfg.get("weekdays") or []):
        return None
    if (now_local.hour, now_local.minute) < (int(cfg["hour"]), int(cfg["minute"])):
        return None
    if state.get("last_sent_day") == jalali_day_key(now_local):
        return None
    return "scheduled"


# ---------------------------------------------------------------- report build + send
def _letterhead(db: Any) -> Path | None:
    from services.inbound_report import letterhead_path

    return letterhead_path(db)


def _stock_files(db: Any, actor: dict, *, mode: str, formats: list[str], out_dir: Path) -> tuple[list[Path], str | None]:
    from services.warehouse_stock_report import MODE_MONTH_END, MODE_TODAY, write_file, build

    mode = MODE_MONTH_END if mode == REPORT_STOCK_MONTH_END else MODE_TODAY
    rep = build(db, actor, mode, SEGMENT_CONTRACTOR)
    if rep.error:
        err = rep.error
        if mode == MODE_MONTH_END:
            return [], NOTE_NO_SNAPSHOT_FA if "موجودی پایان ماه گذشته" in err or "ثبت موجودی" in err else err
        if "منبع اصلی" in err:
            return [], NOTE_NO_SOURCE_FA
        return [], err
    paths: list[Path] = []
    for fmt in formats:
        paths.append(write_file(rep, fmt, letterhead_path=_letterhead(db), output_dir=out_dir))
    return paths, None


def _critical_files(db: Any, actor: dict, *, formats: list[str], out_dir: Path) -> tuple[list[Path], str | None]:
    from services.critical_items_report import generate_critical_items_files
    from excel.critical_template import write_critical_items_xlsx

    res = generate_critical_items_files(db, actor, output_dir=out_dir, letterhead_path=_letterhead(db))
    if res.error and not res.pdfs and not res.sheets:
        return [], res.error or NOTE_NO_CRITICAL_FA
    paths: list[Path] = []
    if "pdf" in formats and res.contractor_pdf and Path(res.contractor_pdf).is_file():
        paths.append(Path(res.contractor_pdf))
    if "xlsx" in formats:
        sheets = [s for s in (res.sheets or []) if s.get("segment") == SEGMENT_CONTRACTOR]
        if sheets and (sheets[0].get("rows")):
            xlsx = out_dir / "اقلام_بحرانی_پیمانکار.xlsx"
            write_critical_items_xlsx(sheets, xlsx)
            paths.append(Path(xlsx))
    if not paths:
        return [], res.error or NOTE_NO_CRITICAL_FA
    return paths, None


def build_payload(db: Any, cfg: dict[str, Any], *, out_dir: Path | None = None) -> tuple[list[Path], list[str]]:
    """Return (files to send, persian notes). Never raises."""
    from config import REPORT_DIR

    actor = full_data_actor(db)
    out = Path(out_dir) if out_dir else Path(REPORT_DIR) / "contractor_rep"
    out.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    notes: list[str] = []
    formats = [f for f in (cfg.get("formats") or []) if f in FORMATS] or list(FORMATS)
    for key in cfg.get("reports") or []:
        try:
            if key == REPORT_STOCK_TODAY:
                paths, note = _stock_files(db, actor, mode=REPORT_STOCK_TODAY, formats=formats, out_dir=out)
            elif key == REPORT_STOCK_MONTH_END:
                paths, note = _stock_files(db, actor, mode=REPORT_STOCK_MONTH_END, formats=formats, out_dir=out)
            elif key == REPORT_CRITICAL:
                paths, note = _critical_files(db, actor, formats=formats, out_dir=out)
            else:
                continue
            files.extend(paths)
            if note:
                notes.append(note)
        except Exception as exc:  # noqa: BLE001
            logger.exception("contractor_rep build %s failed", key)
            notes.append(f"{REPORT_LABEL_FA.get(key, key)}: خطا در تهیهٔ گزارش ({exc}).")
    return files, notes


def caption_fa(cfg: dict[str, Any], *, now: datetime | None = None, forced: bool = False) -> str:
    now = now or tehran_now()
    today = jdatetime.date.fromgregorian(date=now.date())
    kind = "ارسال آزمایشی" if forced else "ارسال خودکار"
    return (
        f"📦 {kind} — موجودی اقلام پیمانکار\n"
        f"تاریخ: {fa_digits(today.strftime('%Y/%m/%d'))}  ساعت: {clock_fa(now.hour, now.minute)} (تهران)\n"
        f"گزارش‌ها: {reports_fa(cfg)}\n"
        f"قالب: {formats_fa(cfg)}"
    )


def _send_file(client: Any, chat_id: Any, path: Path, *, caption: str) -> bool:
    if not path.is_file():
        return False
    if path.stat().st_size > BALE_MAX_UPLOAD:
        client.send_message(
            chat_id,
            f"{caption}\n⚠ حجم فایل «{path.name}» ({human_size(path.stat().st_size)}) "
            "بیش از سقف ارسال بله است؛ از پنل وب دانلود کنید.",
        )
        return False
    try:
        client.send_document(chat_id, path, caption=caption)
        return True
    except TypeError:
        # some clients accept (chat_id, path) only
        client.send_document(chat_id, path)
        if caption:
            client.send_message(chat_id, caption)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("contractor_rep send_document failed: %s", exc)
        try:
            client.send_message(
                chat_id,
                f"{caption}\n⚠ ارسال فایل «{path.name}» در بله ناموفق بود.",
            )
        except Exception:  # noqa: BLE001
            pass
        return False


@dataclass
class SendResult:
    sent: int
    failed: list[str]
    recipients: int
    files: int
    notes: list[str] = field(default_factory=list)
    forced: bool = False
    reason: str | None = None
    text: str = ""


def send_reports(
    client: Any,
    db: Any,
    *,
    forced: bool = False,
    reason: str | None = None,
    cfg: dict[str, Any] | None = None,
    now: datetime | None = None,
    out_dir: Path | None = None,
) -> SendResult:
    cfg = cfg or load_config(db)
    now = now or tehran_now()
    recips = resolve_recipients(db, cfg)
    files, notes = build_payload(db, cfg, out_dir=out_dir)
    caption = caption_fa(cfg, now=now, forced=forced)
    body_notes = "\n".join(notes)
    text = caption + (("\n\n" + body_notes) if body_notes else "")
    sent = 0
    failed: list[str] = []
    if not recips:
        failed.extend(missing_ids(db, cfg) or ["(فهرست خالی)"])
    for u in recips:
        uid = str(u["bale_user_id"])
        try:
            client.send_message(uid, text)
            for path in files:
                _send_file(client, uid, path, caption=path.name)
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("contractor_rep send failed to %s: %s", uid, exc)
            failed.append(uid)
    # IDs listed but not active also count as failures for the admin notice
    for mid in missing_ids(db, cfg):
        if mid not in failed:
            failed.append(mid)
    today_s = jalali_day_key(now)
    state = load_state(db)
    if not forced:
        state["last_sent_day"] = today_s
    state["last_result"] = {
        "at": now.isoformat(),
        "at_fa": f"{today_s} {clock_fa(now.hour, now.minute)}",
        "sent": sent,
        "failed": failed,
        "recipients": len(recips),
        "files": len(files),
        "notes": notes,
        "reason": reason,
        "forced": forced,
    }
    save_state(db, state)
    if failed:
        _notify_admins_of_failures(client, db, failed, now=now, forced=forced)
    logger.info("contractor_rep sent=%s failed=%s files=%s reason=%s forced=%s",
                sent, len(failed), len(files), reason, forced)
    return SendResult(sent=sent, failed=failed, recipients=len(recips), files=len(files),
                      notes=notes, forced=forced, reason=reason, text=text)


def _notify_admins_of_failures(client: Any, db: Any, failed: list[str], *, now: datetime, forced: bool) -> None:
    kind = "آزمایشی" if forced else "زمان‌بندی‌شده"
    body = (
        f"⚠ ارسال {kind} گزارش نماینده پیمانکار برای "
        f"{fa_digits(len(failed))} شناسه ناموفق بود:\n"
        + "\n".join(f"• {fa_digits(i)}" for i in failed)
        + f"\n{START_WARN_FA}"
    )
    for admin in admin_recipients(db):
        try:
            client.send_message(admin["bale_user_id"], body)
        except Exception:  # noqa: BLE001
            logger.warning("contractor_rep admin notice failed to %s", admin.get("bale_user_id"))


def tick(client: Any, db: Any, *, now: datetime | None = None, out_dir: Path | None = None) -> SendResult | None:
    """Called every minute from the bot loop. Never raises. Catch-up: if the bot
    starts later the same selected weekday after the configured time, it still sends
    once (``last_sent_day`` not yet today)."""
    try:
        now = now or tehran_now()
        cfg = load_config(db)
        if db.get_setting(FORCE_KEY):
            clear_force(db)
            return send_reports(client, db, forced=True, reason="forced", cfg=cfg, now=now, out_dir=out_dir)
        state = load_state(db)
        reason = should_send(cfg, state, now)
        if not reason:
            return None
        return send_reports(client, db, reason=reason, cfg=cfg, now=now, out_dir=out_dir)
    except Exception:  # noqa: BLE001
        logger.exception("contractor_rep tick failed")
        return None

