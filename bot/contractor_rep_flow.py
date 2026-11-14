"""Bale UX: ⚙️ تنظیمات / 👥 کاربران → 👷 نماینده پیمانکار (owner / manager).

Settings + send logic live in ``services.contractor_rep`` (shared with web
``/settings/contractor-rep``).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bot import keyboards as kb
from bot.activity import log_activity
from services import contractor_rep as cr
from services import permissions as perm

if TYPE_CHECKING:  # pragma: no cover
    from bot.handlers import BotApp

logger = logging.getLogger(__name__)

_OWN = {
    kb.BTN_CONTRACTOR_REP,
    kb.BTN_CR_ENABLE,
    kb.BTN_CR_DISABLE,
    kb.BTN_CR_IDS,
    kb.BTN_CR_DAYS,
    kb.BTN_CR_TIME,
    kb.BTN_CR_REPORTS,
    kb.BTN_CR_FORMATS,
    kb.BTN_CR_SEND_NOW,
    kb.BTN_CR_BACK,
}

_WEEKDAY_BTN = {}
for i, name in enumerate(cr.WEEKDAY_FA):
    _WEEKDAY_BTN[kb.CR_CHECK_ON + name] = i
    _WEEKDAY_BTN[kb.CR_CHECK_OFF + name] = i

_REPORT_BTN = {}
for key, label in cr.REPORT_LABEL_FA.items():
    _REPORT_BTN[kb.CR_CHECK_ON + label] = key
    _REPORT_BTN[kb.CR_CHECK_OFF + label] = key

_FORMAT_BTN = {}
for key, label in cr.FORMAT_LABEL_FA.items():
    _FORMAT_BTN[kb.CR_CHECK_ON + label] = key
    _FORMAT_BTN[kb.CR_CHECK_OFF + label] = key


class ContractorRepFlow:
    def __init__(self, app: "BotApp") -> None:
        self.app = app
        self.pending: dict[str, dict[str, Any]] = {}

    @property
    def db(self):
        return self.app.db

    def clear(self, uid: str) -> None:
        self.pending.pop(str(uid), None)

    def has_pending(self, uid: str) -> bool:
        return (self.pending.get(str(uid)) or {}).get("mode") in {"ids", "time"}

    def open_menu(self, message: dict) -> None:
        self.open(message)

    def _reply(self, message: dict, text: str, markup: dict | None = None) -> None:
        self.app._reply(message, text[:3900], markup)

    def _admin(self, message: dict) -> dict | None:
        user = self.app._user_or_deny(message)
        if not user:
            return None
        if self.app._deny_technician(message, user, perm.USERS):
            return None
        if not cr.can_edit(user):
            self._reply(message, "دسترسی تنظیمات نماینده پیمانکار برای نقش شما فعال نیست.", kb.main_menu(user))
            return None
        return user

    def _menu(self) -> dict:
        return kb.contractor_rep_menu(cr.load_config(self.db)["enabled"])

    def handle_text(self, message: dict, text: str) -> bool:
        uid = self.app._uid(message)
        text = (text or "").strip()
        p = self.pending.get(uid)

        if text in (kb.BTN_CONTRACTOR_REP, kb.BTN_CR_BACK):
            self.open(message)
            return True
        if text == kb.BTN_CR_ENABLE:
            self.toggle(message, True)
            return True
        if text == kb.BTN_CR_DISABLE:
            self.toggle(message, False)
            return True
        if text == kb.BTN_CR_IDS:
            self.open_ids(message)
            return True
        if text == kb.BTN_CR_DAYS:
            self.open_days(message)
            return True
        if text == kb.BTN_CR_TIME:
            self.open_time(message)
            return True
        if text == kb.BTN_CR_REPORTS:
            self.open_reports(message)
            return True
        if text == kb.BTN_CR_FORMATS:
            self.open_formats(message)
            return True
        if text == kb.BTN_CR_SEND_NOW:
            self.send_now(message)
            return True
        if p and p.get("mode") == "days" and text in _WEEKDAY_BTN:
            self.toggle_day(message, _WEEKDAY_BTN[text])
            return True
        if p and p.get("mode") == "reports" and text in _REPORT_BTN:
            self.toggle_report(message, _REPORT_BTN[text])
            return True
        if p and p.get("mode") == "formats" and text in _FORMAT_BTN:
            self.toggle_format(message, _FORMAT_BTN[text])
            return True
        if not p:
            return False
        if kb.is_pending_reserved_text(text):
            self.clear(uid)
            return False
        if p.get("mode") == "ids":
            return self._on_ids_text(message, text)
        if p.get("mode") == "time":
            return self._on_time_text(message, text)
        if p.get("mode") in {"days", "reports", "formats"}:
            self._reply(message, "یکی از گزینه‌ها را از منو بزنید.", self._mode_menu(p["mode"]))
            return True
        self.clear(uid)
        return False

    def _mode_menu(self, mode: str) -> dict:
        cfg = cr.load_config(self.db)
        if mode == "days":
            return kb.contractor_rep_weekday_menu(cfg["weekdays"])
        if mode == "reports":
            return kb.contractor_rep_report_menu(cfg["reports"])
        if mode == "formats":
            return kb.contractor_rep_format_menu(cfg["formats"])
        return self._menu()

    def open(self, message: dict) -> None:
        user = self._admin(message)
        if not user:
            return
        self.pending[str(user["bale_user_id"])] = {"mode": "menu"}
        self._reply(message, cr.status_text(self.db), self._menu())

    def toggle(self, message: dict, enabled: bool) -> None:
        user = self._admin(message)
        if not user:
            return
        cfg = cr.load_config(self.db)
        cfg["enabled"] = enabled
        cfg = cr.save_config(self.db, cfg, updated_by=user["bale_user_id"])
        log_activity(self.db, user, "settings_contractor_rep")
        warn = ""
        if enabled and not cfg["user_ids"]:
            warn = "\n⚠ هنوز شناسه‌ای در فهرست نیست؛ ارسال انجام نمی‌شود تا نماینده اضافه شود."
        self._reply(
            message,
            ("✅ ارسال خودکار فعال شد." if enabled else "⏸ ارسال خودکار غیرفعال شد.") + warn
            + "\n\n" + cr.status_text(self.db),
            kb.contractor_rep_menu(cfg["enabled"]),
        )

    def open_ids(self, message: dict) -> None:
        user = self._admin(message)
        if not user:
            return
        self.pending[str(user["bale_user_id"])] = {"mode": "ids"}
        cfg = cr.load_config(self.db)
        lines = [
            "👤 شناسه‌های نماینده پیمانکار",
            "شناسهٔ عددی بله را بفرستید تا به فهرست اضافه شود.",
            "برای حذف، بنویسید: حذف ۱۲۳۴۵۶۷۸",
            "",
        ]
        if cfg["user_ids"]:
            recips = {u["bale_user_id"]: u for u in cr.resolve_recipients(self.db, cfg)}
            for uid in cfg["user_ids"]:
                u = recips.get(uid) or self.db.get_user(uid)
                name = (u or {}).get("display_name") or "—"
                flag = "✅" if u and u.get("active") else "⚠ غیرفعال/نیست"
                lines.append(f"• {cr.fa_digits(uid)} — {name} {flag}")
        else:
            lines.append("فهرست خالی است.")
        lines.append("")
        lines.append(cr.START_WARN_FA)
        self._reply(message, "\n".join(lines), kb.contractor_rep_back_menu())

    def _on_ids_text(self, message: dict, text: str) -> bool:
        user = self._admin(message)
        if not user:
            return True
        raw = text.strip()
        # «حذف 123» / «حذف ۱۲۳»
        parts = raw.replace("\u200c", " ").split(None, 1)
        if parts and parts[0] in {"حذف", "del", "remove", "پاک"} and len(parts) == 2:
            ok, msg = cr.remove_representative(self.db, user, parts[1])
            if ok:
                log_activity(self.db, user, "contractor_rep_remove")
            self._reply(message, msg, kb.contractor_rep_back_menu())
            self.open_ids(message)
            return True
        ok, msg, _url = cr.add_representative(self.db, user, raw)
        if ok:
            log_activity(self.db, user, "contractor_rep_add")
        self._reply(message, msg, kb.contractor_rep_back_menu())
        # stay in ids mode
        self.pending[str(user["bale_user_id"])] = {"mode": "ids"}
        return True

    def open_days(self, message: dict) -> None:
        user = self._admin(message)
        if not user:
            return
        self.pending[str(user["bale_user_id"])] = {"mode": "days"}
        cfg = cr.load_config(self.db)
        self._reply(
            message,
            "📅 روزهای ارسال — روی هر روز بزنید تا روشن/خاموش شود (✅ = ارسال می‌شود).\n"
            f"فعلی: {cr.weekdays_fa(cfg)}",
            kb.contractor_rep_weekday_menu(cfg["weekdays"]),
        )

    def toggle_day(self, message: dict, day: int) -> None:
        user = self._admin(message)
        if not user:
            return
        cfg = cr.load_config(self.db)
        days = list(cfg["weekdays"])
        if day in days:
            if len(days) == 1:
                self._reply(
                    message,
                    "دست‌کم یک روز باید انتخاب شده باشد.",
                    kb.contractor_rep_weekday_menu(days),
                )
                return
            days = [d for d in days if d != day]
        else:
            days.append(day)
            days.sort()
        cfg["weekdays"] = days
        cfg = cr.save_config(self.db, cfg, updated_by=user["bale_user_id"])
        log_activity(self.db, user, "settings_contractor_rep")
        on = day in cfg["weekdays"]
        self._reply(
            message,
            f"{'✅' if on else '⬜'} {cr.weekday_fa(day)} — {'ارسال می‌شود' if on else 'ارسال نمی‌شود'}.\n"
            f"روزهای فعلی: {cr.weekdays_fa(cfg)}",
            kb.contractor_rep_weekday_menu(cfg["weekdays"]),
        )

    def open_time(self, message: dict) -> None:
        user = self._admin(message)
        if not user:
            return
        self.pending[str(user["bale_user_id"])] = {"mode": "time"}
        cfg = cr.load_config(self.db)
        self._reply(
            message,
            "🕐 ساعت ارسال به وقت تهران را بفرستید؛ مثلاً ۸:۰۰ یا 08:30 یا ۸.\n"
            f"ساعت فعلی: {cr.clock_fa(cfg['hour'], cfg['minute'])}",
            kb.contractor_rep_back_menu(),
        )

    def _on_time_text(self, message: dict, text: str) -> bool:
        user = self._admin(message)
        if not user:
            return True
        parsed = cr.parse_clock(text)
        if not parsed:
            self._reply(
                message,
                "ساعت نامعتبر است. به شکل ۸:۰۰ یا 08:30 یا فقط ۸ بفرستید (۰ تا ۲۳).",
                kb.contractor_rep_back_menu(),
            )
            return True
        hour, minute = parsed
        cfg = cr.load_config(self.db)
        cfg["hour"], cfg["minute"] = hour, minute
        cr.save_config(self.db, cfg, updated_by=user["bale_user_id"])
        log_activity(self.db, user, "settings_contractor_rep")
        self.clear(str(user["bale_user_id"]))
        self._reply(
            message,
            f"✅ ساعت ارسال {cr.clock_fa(hour, minute)} به وقت تهران ذخیره شد.\n\n" + cr.status_text(self.db),
            self._menu(),
        )
        return True

    def open_reports(self, message: dict) -> None:
        user = self._admin(message)
        if not user:
            return
        self.pending[str(user["bale_user_id"])] = {"mode": "reports"}
        cfg = cr.load_config(self.db)
        self._reply(
            message,
            "📄 نوع گزارش‌ها — روی هر مورد بزنید تا تیک بخورد یا برداشته شود.\n"
            f"فعلی: {cr.reports_fa(cfg)}",
            kb.contractor_rep_report_menu(cfg["reports"]),
        )

    def toggle_report(self, message: dict, key: str) -> None:
        user = self._admin(message)
        if not user:
            return
        cfg = cr.load_config(self.db)
        reps = list(cfg["reports"])
        if key in reps:
            if len(reps) == 1:
                self._reply(
                    message,
                    "دست‌کم یک نوع گزارش باید انتخاب شده باشد.",
                    kb.contractor_rep_report_menu(reps),
                )
                return
            reps = [k for k in reps if k != key]
        else:
            reps.append(key)
            reps = [k for k in cr.REPORT_KEYS if k in reps]
        cfg["reports"] = reps
        cfg = cr.save_config(self.db, cfg, updated_by=user["bale_user_id"])
        log_activity(self.db, user, "settings_contractor_rep")
        self._reply(
            message,
            f"گزارش‌های انتخاب‌شده: {cr.reports_fa(cfg)}",
            kb.contractor_rep_report_menu(cfg["reports"]),
        )

    def open_formats(self, message: dict) -> None:
        user = self._admin(message)
        if not user:
            return
        self.pending[str(user["bale_user_id"])] = {"mode": "formats"}
        cfg = cr.load_config(self.db)
        self._reply(
            message,
            "📁 قالب خروجی — PDF و/یا اکسل.\n"
            f"فعلی: {cr.formats_fa(cfg)}",
            kb.contractor_rep_format_menu(cfg["formats"]),
        )

    def toggle_format(self, message: dict, key: str) -> None:
        user = self._admin(message)
        if not user:
            return
        cfg = cr.load_config(self.db)
        fmts = list(cfg["formats"])
        if key in fmts:
            if len(fmts) == 1:
                self._reply(
                    message,
                    "دست‌کم یک قالب باید انتخاب شده باشد.",
                    kb.contractor_rep_format_menu(fmts),
                )
                return
            fmts = [k for k in fmts if k != key]
        else:
            fmts.append(key)
            fmts = [k for k in cr.FORMATS if k in fmts]
        cfg["formats"] = fmts
        cfg = cr.save_config(self.db, cfg, updated_by=user["bale_user_id"])
        log_activity(self.db, user, "settings_contractor_rep")
        self._reply(
            message,
            f"قالب‌های انتخاب‌شده: {cr.formats_fa(cfg)}",
            kb.contractor_rep_format_menu(cfg["formats"]),
        )

    def send_now(self, message: dict) -> None:
        user = self._admin(message)
        if not user:
            return
        cfg = cr.load_config(self.db)
        if not cfg["user_ids"]:
            self._reply(
                message,
                "هیچ شناسه‌ای در فهرست نیست؛ ابتدا نماینده را اضافه کنید.",
                self._menu(),
            )
            return
        cr.request_force_send(self.db, requested_by=user["bale_user_id"])
        log_activity(self.db, user, "contractor_rep_send_now")
        self._reply(
            message,
            "📨 درخواست ارسال آزمایشی ثبت شد؛ ربات بله حداکثر تا حدود یک دقیقه دیگر گزارش را می‌فرستد.\n"
            + cr.START_WARN_FA,
            self._menu(),
        )
