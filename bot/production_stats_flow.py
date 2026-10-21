"""«📝 آمار تولید» — text-only production-stats input (photo/OCR removed).

Steps: سال/ماه → وزن محصول ccm1..ccm5 (کیلوگرم) → تعداد کل ذوب‌ها → خلاصه + تأیید →
(جایگزینی ماه موجود پس از پرسش) → پیام تأیید → بازگشت به منوی آمار تولید.
Menu: ➕ ثبت اطلاعات ماه دیگر | ✏️ ویرایش ماه‌های ثبت‌شده | 🗑 حذف ماه ثبت‌شده |
↩️ بازگشت به منوی قبل. Shared logic: ``services.production_manual`` (also web).
"""
from __future__ import annotations

from typing import Any

from bot import keyboards as kb
from bot.activity import log_activity
from services import production_manual as pm

PS_BUTTONS = {
    kb.BTN_PS_NEW,
    kb.BTN_PS_EDIT,
    kb.BTN_PS_DELETE,
    kb.BTN_PS_BACK,
    kb.BTN_PS_CONFIRM,
    kb.BTN_PS_REPLACE,
    kb.BTN_PS_DEL_CONFIRM,
    kb.BTN_PS_REENTER_ALL,
    *kb.BTN_PS_FIELDS.values(),
}
_FIELD_BY_BTN = {v: k for k, v in kb.BTN_PS_FIELDS.items()}
YM_PROMPT = "سال و ماه آمار تولید را به تقویم شمسی وارد کنید (مثلاً 1405/07):"


class ProductionStatsMixin:
    """Mixed into ``MainGoalReportFlow`` (shares ``pending`` / cancel / back routing)."""

    pending: dict[str, dict[str, Any]]

    # ---------------------------------------------------------------- entry / menu
    def start_production_stats(self, message: dict, via_files: bool = False) -> None:
        user = self._start_user(message, via_files)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self.app._file_guide_kind[uid] = "mg_production_stats"
        self.pending[uid] = {"await": "ps_ym", "via_files": via_files}
        self._reply(
            message,
            f"{kb.BTN_PRODUCTION_STATS} — ورود متنی (بدون عکس)\n"
            "ترتیب: سال/ماه ← وزن محصول ccm1 تا ccm5 به کیلوگرم (از ابتدای همان ماه) ← "
            "تعداد کل ذوب‌ها ← تأیید.\n\n" + YM_PROMPT,
            kb.production_stats_entry_menu(),
        )

    def _ps_menu(self, message: dict, p: dict, head: str = "") -> bool:
        via = p.get("via_files", False)
        p.clear()
        p.update({"await": "ps_menu", "via_files": via})
        rows = pm.list_months(self.db)
        lines = [head] if head else []
        lines.append(f"{kb.BTN_PRODUCTION_STATS} — منوی ورود اطلاعات")
        if rows:
            lines.append("ماه‌های ثبت‌شده: " + "، ".join(pm.ym_text(r["year"], r["month"]) for r in rows[:12]))
        else:
            lines.append("هنوز ماهی ثبت نشده است.")
        self._reply(message, "\n".join(lines), kb.production_stats_menu())
        return True

    def _ps_leave(self, message: dict, p: dict) -> bool:
        uid = self.app._uid(message)
        self.clear(uid)
        user = self.app._user_or_deny(message)
        if not user:
            return True
        markup = kb.upload_files_menu(user) if p.get("via_files") else kb.main_goal_inputs_menu()
        self._reply(message, "به منوی قبل برگشتید.", markup)
        return True

    def ps_cancel(self, message: dict, p: dict) -> None:
        """«✖️ انصراف» inside a step → back to the production-stats menu (nothing saved)."""
        if p.get("await") == "ps_menu":
            self._ps_leave(message, p)
            return
        self._ps_menu(message, p, "لغو شد؛ چیزی ذخیره نشد.")

    # ---------------------------------------------------------------- dispatcher
    def handle_ps_text(self, message: dict, p: dict, text: str) -> bool:
        mode = p.get("await")
        user = self._user_or_files(message, p)
        if not user:
            self.clear(self.app._uid(message))
            return True
        if mode == "ps_menu":
            if text == kb.BTN_PS_NEW:
                via = p.get("via_files", False)
                p.clear()
                p.update({"await": "ps_ym", "via_files": via})
                self._reply(message, YM_PROMPT, kb.production_stats_entry_menu())
                return True
            if text == kb.BTN_PS_EDIT:
                return self._ps_pick_month(message, p, "edit")
            if text == kb.BTN_PS_DELETE:
                return self._ps_pick_month(message, p, "delete")
            if text == kb.BTN_PS_BACK:
                return self._ps_leave(message, p)
            self._reply(message, "یکی از دکمه‌های منوی آمار تولید را بزنید.", kb.production_stats_menu())
            return True
        if mode == "ps_ym":
            ym = pm.parse_year_month(text)
            if not ym:
                self._reply(message, "❌ سال/ماه نامعتبر است. مثال: 1405/07\n" + YM_PROMPT,
                            kb.production_stats_entry_menu())
                return True
            p.update({"year": ym[0], "month": ym[1], "values": {}, "queue": list(pm.FIELD_KEYS)})
            return self._ps_ask_next(message, p)
        if mode == "ps_field":
            key = p["queue"][0]
            v = pm.parse_number(text, integer=(key == "melts"))
            if v is None:
                hint = "یک عدد صحیح" if key == "melts" else "یک عدد (کیلوگرم)"
                self._reply(
                    message,
                    f"❌ مقدار نامعتبر؛ {hint} وارد کنید (ارقام فارسی و جداکنندهٔ «,» پذیرفته است).\n"
                    + pm.field_prompt(key, p["year"], p["month"]),
                    kb.production_stats_entry_menu(),
                )
                return True
            p["values"][key] = v
            p["queue"].pop(0)
            return self._ps_ask_next(message, p)
        if mode == "ps_confirm":
            if text != kb.BTN_PS_CONFIRM:
                self._reply(message, f"برای ذخیره «{kb.BTN_PS_CONFIRM}» یا برای لغو «{kb.BTN_CANCEL}» را بزنید.",
                            kb.production_stats_confirm_menu())
                return True
            if pm.get_month(self.db, p["year"], p["month"]) and not p.get("replace_ok"):
                p["await"] = "ps_replace"
                self._reply(
                    message,
                    f"⚠ برای {pm.ym_text(p['year'], p['month'])} قبلاً آمار تولید ثبت شده است. "
                    "اطلاعات قبلی جایگزین شود؟",
                    kb.production_stats_confirm_menu(kb.BTN_PS_REPLACE),
                )
                return True
            return self._ps_save(message, p, user)
        if mode == "ps_replace":
            if text != kb.BTN_PS_REPLACE:
                self._reply(message, f"«{kb.BTN_PS_REPLACE}» یا «{kb.BTN_CANCEL}».",
                            kb.production_stats_confirm_menu(kb.BTN_PS_REPLACE))
                return True
            return self._ps_save(message, p, user)
        if mode == "ps_pick":
            ym = pm.parse_year_month(text)
            if not ym or not pm.get_month(self.db, *ym):
                self._reply(message, "ماه را از دکمه‌ها انتخاب کنید.", self._ps_months_kb())
                return True
            p.update({"year": ym[0], "month": ym[1]})
            if p.get("action") == "delete":
                p["await"] = "ps_del_confirm"
                cur = pm.from_row(pm.get_month(self.db, *ym))
                self._reply(
                    message,
                    (cur.summary_fa() if cur else "") + f"\n\n🗑 آمار تولید {pm.ym_text(*ym)} حذف شود؟",
                    kb.production_stats_confirm_menu(kb.BTN_PS_DEL_CONFIRM),
                )
                return True
            cur = pm.from_row(pm.get_month(self.db, *ym))
            p["await"] = "ps_edit_field"
            self._reply(
                message,
                (cur.summary_fa() if cur else "")
                + "\n\nکدام مقدار ویرایش شود؟ (یا «" + kb.BTN_PS_REENTER_ALL + "»)",
                kb.production_stats_field_menu(),
            )
            return True
        if mode == "ps_edit_field":
            cur = pm.from_row(pm.get_month(self.db, p["year"], p["month"]))
            if text == kb.BTN_PS_REENTER_ALL:
                p.update({"values": {}, "queue": list(pm.FIELD_KEYS), "replace_ok": True})
                return self._ps_ask_next(message, p)
            key = _FIELD_BY_BTN.get(text)
            if not key or not cur:
                self._reply(message, "یکی از دکمه‌های مقدار را بزنید.", kb.production_stats_field_menu())
                return True
            vals = dict(cur.kg)
            vals["melts"] = cur.melts
            p.update({"values": vals, "queue": [key], "replace_ok": True})
            return self._ps_ask_next(message, p)
        if mode == "ps_del_confirm":
            if text != kb.BTN_PS_DEL_CONFIRM:
                self._reply(message, f"«{kb.BTN_PS_DEL_CONFIRM}» یا «{kb.BTN_CANCEL}».",
                            kb.production_stats_confirm_menu(kb.BTN_PS_DEL_CONFIRM))
                return True
            ym = (p["year"], p["month"])
            pm.delete(self.db, *ym)
            log_activity(self.db, user, "production_stats_delete")
            return self._ps_menu(message, p, f"🗑 آمار تولید {pm.ym_text(*ym)} حذف شد.")
        return self._ps_menu(message, p)

    # ---------------------------------------------------------------- helpers
    def _user_or_files(self, message: dict, p: dict) -> dict | None:
        return self._start_user(message, bool(p.get("via_files")))

    def _ps_months_kb(self) -> dict:
        return kb.production_stats_months_menu(
            [pm.ym_text(r["year"], r["month"]) for r in pm.list_months(self.db)[:24]]
        )

    def _ps_pick_month(self, message: dict, p: dict, action: str) -> bool:
        if not pm.list_months(self.db):
            self._reply(message, "هیچ ماه ثبت‌شده‌ای وجود ندارد.", kb.production_stats_menu())
            return True
        p.update({"await": "ps_pick", "action": action})
        verb = "ویرایش" if action == "edit" else "حذف"
        self._reply(message, f"ماه مورد نظر برای {verb} را انتخاب کنید:", self._ps_months_kb())
        return True

    def _ps_ask_next(self, message: dict, p: dict) -> bool:
        if p["queue"]:
            p["await"] = "ps_field"
            self._reply(message, pm.field_prompt(p["queue"][0], p["year"], p["month"]),
                        kb.production_stats_entry_menu())
            return True
        data = self._ps_data(p)
        p["await"] = "ps_confirm"
        self._reply(message, data.summary_fa() + "\n\nاطلاعات بالا تأیید و ذخیره شود؟",
                    kb.production_stats_confirm_menu())
        return True

    @staticmethod
    def _ps_data(p: dict) -> pm.ManualProduction:
        v = p.get("values") or {}
        return pm.ManualProduction(
            int(p["year"]), int(p["month"]),
            {k: float(v.get(k) or 0) for k, _l, _s in pm.FIELDS},
            float(v.get("melts") or 0),
        )

    def _ps_save(self, message: dict, p: dict, user: dict) -> bool:
        data = self._ps_data(p)
        _row, replaced = pm.store(self.db, data, user=user, source="bot")
        log_activity(self.db, user, "production_stats_store")
        self._reply(message, pm.confirmation_fa(data, replaced) + pm.heat_note_fa(self.db, data))
        return self._ps_menu(message, p)
