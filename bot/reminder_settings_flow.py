"""Bale UX: ⚙️ تنظیمات ربات → 🔔 یادآور گزارش‌های الزامی (owner / manager).

Settings + status + send logic live in ``services.mandatory_reminders`` (shared
with web ``/settings/reminders``).
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from auth.rbac import role_label
from bot import keyboards as kb
from bot.activity import log_activity
from config import ROLES
from services import mandatory_reminders as rem

if TYPE_CHECKING:  # pragma: no cover
    from bot.handlers import BotApp

logger = logging.getLogger(__name__)

_OWN = {
    kb.BTN_SET_REMINDERS,
    kb.BTN_RM_STATUS,
    kb.BTN_RM_ENABLE,
    kb.BTN_RM_DISABLE,
    kb.BTN_RM_ROLES,
    kb.BTN_RM_USERS,
    kb.BTN_RM_SCHEDULE,
    kb.BTN_RM_SEND_NOW,
    kb.BTN_RM_BACK,
}
_ROLE_BTN = {
    prefix + label: key
    for key, label in ROLES.items()
    for prefix in (kb.RM_CHECK_ON, kb.RM_CHECK_OFF)
}


class ReminderSettingsFlow:
    def __init__(self, app: "BotApp") -> None:
        self.app = app
        self.pending: dict[str, dict[str, Any]] = {}

    @property
    def db(self):
        return self.app.db

    def clear(self, uid: str) -> None:
        self.pending.pop(str(uid), None)

    def _reply(self, message: dict, text: str, markup: dict | None = None) -> None:
        self.app._reply(message, text[:3900], markup)

    def _admin(self, message: dict) -> dict | None:
        user = self.app._user_or_deny(message)
        if not user:
            return None
        if self.app._deny_technician(message, user):
            return None
        if not rem.can_edit(user):
            self._reply(message, "فقط مالک یا مدیر به تنظیمات یادآور دسترسی دارد.", kb.main_menu(user))
            return None
        return user

    def _menu(self) -> dict:
        return kb.reminder_settings_menu(rem.load_config(self.db)["enabled"])

    # ------------------------------------------------------------ dispatcher
    def handle_text(self, message: dict, text: str) -> bool:
        uid = self.app._uid(message)
        text = (text or "").strip()
        p = self.pending.get(uid)

        if text in (kb.BTN_SET_REMINDERS, kb.BTN_RM_BACK, kb.BTN_RM_STATUS):
            self.open(message)
            return True
        if text in (kb.BTN_RM_ENABLE, kb.BTN_RM_DISABLE):
            self.toggle(message, text == kb.BTN_RM_ENABLE)
            return True
        if text == kb.BTN_RM_ROLES:
            self.open_roles(message)
            return True
        if text == kb.BTN_RM_USERS:
            self.open_users(message)
            return True
        if text == kb.BTN_RM_SCHEDULE:
            self.open_schedule(message)
            return True
        if text == kb.BTN_RM_SEND_NOW:
            self.send_now(message)
            return True
        if p and p.get("mode") == "roles" and text in _ROLE_BTN:
            self.toggle_role(message, _ROLE_BTN[text])
            return True
        if not p:
            return False
        if text == kb.BTN_BACK_BOT_SETTINGS or text in kb.PENDING_INPUT_RESERVED_TEXTS:
            self.clear(uid)
            return False
        if p.get("mode") == "users":
            return self._on_users_text(message, text)
        if p.get("mode") == "schedule":
            return self._on_schedule_text(message, text)
        if p.get("mode") == "roles":
            self._reply(message, "یکی از نقش‌ها را بزنید تا روشن/خاموش شود.", kb.reminder_roles_menu(rem.load_config(self.db)["roles"]))
            return True
        self.clear(uid)
        return False

    # ------------------------------------------------------------ screens
    def open(self, message: dict) -> None:
        user = self._admin(message)
        if not user:
            return
        self.pending[str(user["bale_user_id"])] = {"mode": "menu"}
        self._reply(
            message,
            rem.status_text(self.db)
            + "\n\nیادآور برای ۴ فایل ماهانهٔ «گزارش هدف اصلی» است و در صورت نبودِ ماه‌های الزامی "
            "به دریافت‌کنندگان در بله پیام می‌دهد.",
            self._menu(),
        )

    def toggle(self, message: dict, enabled: bool) -> None:
        user = self._admin(message)
        if not user:
            return
        cfg = rem.load_config(self.db)
        cfg["enabled"] = enabled
        cfg = rem.save_config(self.db, cfg, updated_by=user["bale_user_id"])
        log_activity(self.db, user, "settings_reminders")
        recips = rem.resolve_recipients(self.db, cfg)
        warn = "" if recips or not enabled else "\n⚠ هیچ دریافت‌کننده‌ای انتخاب نشده است."
        self._reply(
            message,
            ("✅ یادآور فعال شد." if enabled else "⏸ یادآور غیرفعال شد.") + warn,
            kb.reminder_settings_menu(cfg["enabled"]),
        )

    def open_roles(self, message: dict) -> None:
        user = self._admin(message)
        if not user:
            return
        self.pending[str(user["bale_user_id"])] = {"mode": "roles"}
        cfg = rem.load_config(self.db)
        self._reply(
            message,
            "👥 نقش‌های دریافت‌کنندهٔ یادآور — روی هر نقش بزنید تا روشن/خاموش شود (✅ = دریافت می‌کند):",
            kb.reminder_roles_menu(cfg["roles"]),
        )

    def toggle_role(self, message: dict, role: str) -> None:
        user = self._admin(message)
        if not user:
            return
        cfg = rem.load_config(self.db)
        roles = set(cfg["roles"])
        roles.symmetric_difference_update({role})
        cfg["roles"] = list(roles)
        cfg = rem.save_config(self.db, cfg, updated_by=user["bale_user_id"])
        log_activity(self.db, user, "settings_reminders")
        on = role in cfg["roles"]
        self._reply(
            message,
            f"{'✅' if on else '⬜'} {role_label(role)} — {'دریافت می‌کند' if on else 'دریافت نمی‌کند'}.",
            kb.reminder_roles_menu(cfg["roles"]),
        )

    def _users_text(self) -> tuple[str, list[dict]]:
        cfg = rem.load_config(self.db)
        ids = set(cfg["user_ids"])
        users = self.db.list_users(active_only=True)
        lines = ["👤 کاربران خاص دریافت‌کننده (جدا از نقش‌ها):"]
        for i, u in enumerate(users, 1):
            mark = "✅" if str(u["bale_user_id"]) in ids else "⬜"
            lines.append(
                f"{i}) {mark} {u.get('display_name') or u['bale_user_id']} — {role_label(u.get('role') or '')} ({u['bale_user_id']})"
            )
        extra = [x for x in ids if x not in {str(u["bale_user_id"]) for u in users}]
        if extra:
            lines.append("شناسه‌های ثبت‌شدهٔ غیرفعال/ناشناس: " + "، ".join(extra))
        lines.append("")
        lines.append("شمارهٔ ردیف (یا چند شماره با فاصله) یا شناسهٔ بله را بفرستید تا روشن/خاموش شود.")
        return "\n".join(lines), users

    def open_users(self, message: dict) -> None:
        user = self._admin(message)
        if not user:
            return
        self.pending[str(user["bale_user_id"])] = {"mode": "users"}
        text, _ = self._users_text()
        self._reply(message, text, kb.reminder_back_menu())

    def _on_users_text(self, message: dict, text: str) -> bool:
        user = self._admin(message)
        if not user:
            self.clear(self.app._uid(message))
            return True
        _, users = self._users_text()
        tokens = re.findall(r"\d+", text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
        if not tokens:
            self._reply(message, "شمارهٔ ردیف یا شناسهٔ بله بفرستید.", kb.reminder_back_menu())
            return True
        cfg = rem.load_config(self.db)
        ids = list(cfg["user_ids"])
        changed = []
        for tok in tokens:
            target = None
            if len(tok) <= 3 and 1 <= int(tok) <= len(users):
                target = str(users[int(tok) - 1]["bale_user_id"])
            else:
                target = tok
            if target in ids:
                ids.remove(target)
                changed.append(f"⬜ {target}")
            else:
                ids.append(target)
                changed.append(f"✅ {target}")
        cfg["user_ids"] = ids
        rem.save_config(self.db, cfg, updated_by=user["bale_user_id"])
        log_activity(self.db, user, "settings_reminders")
        text2, _ = self._users_text()
        self._reply(message, "تغییر: " + "، ".join(changed) + "\n\n" + text2, kb.reminder_back_menu())
        return True

    def open_schedule(self, message: dict) -> None:
        user = self._admin(message)
        if not user:
            return
        self.pending[str(user["bale_user_id"])] = {"mode": "schedule"}
        cfg = rem.load_config(self.db)
        order = ["due_day", "lead_days", "repeat_days", "hour", "window_months"]
        lines = ["📅 زمان‌بندی یادآور — مقادیر فعلی:"]
        for i, k in enumerate(order, 1):
            lo, hi = rem.LIMITS[k]
            lines.append(f"{i}) {rem.FIELD_LABELS_FA[k]}: {cfg[k]} (بازه {lo}–{hi})")
        cur = " ".join(str(cfg[k]) for k in order)
        lines += [
            "",
            "پنج عدد را به همین ترتیب با فاصله بفرستید. مثال (مقادیر فعلی):",
            cur,
            "",
            "یعنی: فایل‌های هر ماه تا روز «۱» ماه بعد مهلت دارند؛ «۲» روز قبل از مهلت یادآوری می‌شود؛ "
            "بعد از مهلت هر «۳» روز تکرار؛ ارسال از ساعت «۴»؛ «۵» ماه اخیر باید کامل باشند.",
        ]
        self._reply(message, "\n".join(lines), kb.reminder_back_menu())

    def _on_schedule_text(self, message: dict, text: str) -> bool:
        user = self._admin(message)
        if not user:
            self.clear(self.app._uid(message))
            return True
        nums = re.findall(r"\d+", text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
        order = ["due_day", "lead_days", "repeat_days", "hour", "window_months"]
        if not 1 <= len(nums) <= 5:
            self._reply(message, "حداکثر پنج عدد با فاصله بفرستید (مثل «5 2 3 9 3»).", kb.reminder_back_menu())
            return True
        clean, errors = rem.validate_numbers(dict(zip(order, nums)))
        if errors:
            self._reply(message, "\n".join(errors), kb.reminder_back_menu())
            return True
        cfg = rem.load_config(self.db)
        cfg.update(clean)
        cfg = rem.save_config(self.db, cfg, updated_by=user["bale_user_id"])
        log_activity(self.db, user, "settings_reminders")
        self.pending[str(user["bale_user_id"])] = {"mode": "menu"}
        self._reply(message, "✅ زمان‌بندی ذخیره شد.\n\n" + rem.status_text(self.db, cfg), kb.reminder_settings_menu(cfg["enabled"]))
        return True

    def send_now(self, message: dict) -> None:
        user = self._admin(message)
        if not user:
            return
        cfg = rem.load_config(self.db)
        recips = rem.resolve_recipients(self.db, cfg)
        if not recips:
            self._reply(message, "هیچ دریافت‌کننده‌ای تنظیم نشده است (نقش‌ها یا کاربران را انتخاب کنید).", self._menu())
            return
        res = rem.send_reminder(self.app.client, self.db, forced=True, reason="manual", cfg=cfg)
        log_activity(self.db, user, "reminder_send_now")
        fail = f"، ناموفق: {len(res.failed)} ({'، '.join(res.failed)})" if res.failed else ""
        self._reply(
            message,
            f"📨 یادآور برای {res.sent} نفر از {res.recipients} ارسال شد{fail}.\n\nمتن ارسالی:\n{res.text}",
            self._menu(),
        )
