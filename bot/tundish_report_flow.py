"""Bale UX for «گزارش تاندیش بعد از ریخته‌گری» (entry + settings CRUD).

All validation / parsing / persistence lives in ``services.tundish_report`` so
the web panel (``web.routers.tundish_report``) behaves identically.

Menu paths:
- منوی اصلی → 🧾 گزارش تاندیش بعد از ریخته‌گری → 🧾 گزارش تاندیش اسلب|بلوم|بیلت
  → ✍️ ورود مرحله‌ای | 📋 ورود سریع از متن → … → ✅ تأیید و ثبت گزارش
- … → 📜 آخرین گزارش‌های تاندیش
- … → ⚙️ تنظیمات گزارش تاندیش (owner/manager; also under ⚙️ تنظیمات ربات)
  → ⚙️ اقلام گزارش اسلب|بلوم|بیلت → ➕ / ✏️ / 🗑 / 🏭 خطوط
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bot import keyboards as kb
from bot.activity import log_activity
from bot.bale_api import BaleClient
from services import tundish_report as tr

if TYPE_CHECKING:  # pragma: no cover
    from bot.handlers import BotApp

logger = logging.getLogger(__name__)

SAMPLE_TEXT = (
    "تاندیش ۱۰ اسلب ۱ سکونس ۸ ذوبه مارک ۳۷۱۰ . نازل هر دو خط در ذوب ۵ بعلت گرفتگی "
    "تعویض گردید شرود تا پایان سکونس نرمال سولار پودر قالب کاسپین پودر تاندیش فارس ریزان"
)

# Buttons owned by this module (handled here even without pending state)
_OWN_NAV = {
    kb.BTN_TR_SKIP,
    kb.BTN_TR_PREV,
    kb.BTN_TR_CANCEL,
    kb.BTN_TR_CONFIRM,
    kb.BTN_TR_EDIT,
    kb.BTN_TR_MODE_STEP,
    kb.BTN_TR_MODE_TEXT,
}
_SETTINGS_BTNS = {
    kb.BTN_TRS_ADD,
    kb.BTN_TRS_EDIT,
    kb.BTN_TRS_DELETE,
    kb.BTN_TRS_LINES,
    kb.BTN_TRS_BACK_SECTION,
    kb.BTN_TRS_E_LABEL,
    kb.BTN_TRS_E_TYPE,
    kb.BTN_TRS_E_OPTIONS,
    kb.BTN_TRS_E_REQUIRED,
    kb.BTN_TRS_E_ORDER,
    kb.BTN_TRS_DELETE_CONFIRM,
}


def _global_buttons() -> set[str]:
    vals = {
        v
        for n, v in vars(kb).items()
        if n.startswith("BTN_") and isinstance(v, str)
    }
    vals |= set(kb.PENDING_INPUT_RESERVED_TEXTS)
    return vals


class TundishReportFlow:
    def __init__(self, app: "BotApp") -> None:
        self.app = app
        self.pending: dict[str, dict[str, Any]] = {}
        self.settings_pending: dict[str, dict[str, Any]] = {}
        self._reserved = _global_buttons()

    @property
    def db(self):
        return self.app.db

    # ------------------------------------------------------------ utils
    def clear(self, uid: str) -> None:
        self.pending.pop(str(uid), None)
        self.settings_pending.pop(str(uid), None)

    def _reply(self, message: dict, text: str, markup: dict | None = None) -> None:
        self.app._reply(message, text, markup)

    def _user(self, message: dict) -> dict | None:
        user = self.app._user_or_deny(message)
        if user and not tr.can_enter(user):
            self._reply(message, "دسترسی ثبت گزارش تاندیش ندارید.", kb.main_menu(user))
            return None
        return user

    def _admin(self, message: dict) -> dict | None:
        user = self.app._user_or_deny(message)
        if not user:
            return None
        if not tr.can_configure(user):
            self.settings_pending.pop(str(user["bale_user_id"]), None)
            self._reply(
                message,
                "فقط مالک یا مدیر می‌تواند اقلام گزارش تاندیش را تنظیم کند.",
                kb.tundish_report_menu(user) if tr.can_enter(user) else kb.main_menu(user),
            )
            return None
        return user

    # ------------------------------------------------------------ dispatcher
    def handle_text(self, message: dict, text: str) -> bool:
        """Return True when the text belonged to this feature."""
        uid = self.app._uid(message)
        text = (text or "").strip()

        # 1) entry flow: choice answers first (options could look like anything)
        p = self.pending.get(uid)
        if p and p.get("await") == "step" and text not in _OWN_NAV:
            step = self._current_step(p)
            opts = self._step_options(p, step) if step else []
            if opts and tr.match_option(text, opts) is not None:
                return self._answer_step(message, text)

        # 2) menu buttons (always available)
        if text == kb.BTN_TR_MENU or text == kb.BTN_TR_BACK:
            self.open_menu(message)
            return True
        if text in kb.TR_SECTION_BUTTONS:
            self.start_section(message, kb.TR_SECTION_BUTTONS[text])
            return True
        if text == kb.BTN_TR_RECENT:
            self.show_recent(message)
            return True
        if text == kb.BTN_TR_SETTINGS or text == kb.BTN_TRS_BACK:
            self.open_settings(message)
            return True
        if text in kb.TRS_SECTION_BUTTONS:
            self.open_section_settings(message, kb.TRS_SECTION_BUTTONS[text])
            return True

        # 3) entry pending
        if p:
            if text == kb.BTN_TR_CANCEL:
                self.pending.pop(uid, None)
                user = self.app._user_or_deny(message)
                if user:
                    self._reply(message, "گزارش تاندیش لغو شد؛ چیزی ذخیره نشد.", kb.tundish_report_menu(user))
                return True
            if text in self._reserved and text not in _OWN_NAV:
                # global navigation — abandon the draft and let main dispatcher act
                self.pending.pop(uid, None)
                return False
            return self._entry_text(message, p, text)
        if text in _OWN_NAV:
            user = self.app._user_or_deny(message)
            if user:
                self._reply(message, "گزارش فعالی در جریان نیست.", kb.tundish_report_menu(user))
            return True

        # 4) settings pending
        sp = self.settings_pending.get(uid)
        if sp:
            if text in self._reserved and text not in _SETTINGS_BTNS and text not in {
                kb.BTN_TRS_TYPE_CHOICE,
                kb.BTN_TRS_TYPE_NUMBER,
                kb.BTN_TRS_TYPE_TEXT,
                kb.BTN_TRS_REQUIRED,
                kb.BTN_TRS_OPTIONAL,
            }:
                self.settings_pending.pop(uid, None)
                return False
            return self._settings_text(message, sp, text)
        if text in _SETTINGS_BTNS:
            user = self._admin(message)
            if user:
                self._reply(message, "ابتدا بخش را انتخاب کنید.", kb.tundish_report_settings_menu())
            return True
        return False

    # ------------------------------------------------------------ menu
    def open_menu(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self.clear(uid)
        recent = self.db.list_tundish_reports(limit=1)
        extra = f"\nآخرین ثبت: {tr.short_report_line(recent[0])}" if recent else ""
        self._reply(
            message,
            f"🧾 {tr.TITLE_FA}\n"
            "بخش را انتخاب کنید (اسلب / بلوم / بیلت). فیلدها مرحله‌به‌مرحله یا از روی یک متن پرسیده می‌شوند."
            f"{extra}",
            kb.tundish_report_menu(user),
        )

    def show_recent(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        rows = self.db.list_tundish_reports(limit=10)
        if not rows:
            self._reply(message, "هنوز گزارشی ثبت نشده است.", kb.tundish_report_menu(user))
            return
        lines = ["📜 ۱۰ گزارش اخیر تاندیش (زمان تهران):"]
        lines.extend(tr.short_report_line(r) for r in rows)
        last = rows[0]
        lines.append("")
        lines.append("جزئیات آخرین گزارش:")
        lines.append(last.get("summary_text") or "")
        self._reply(message, "\n".join(lines), kb.tundish_report_menu(user))

    # ------------------------------------------------------------ entry flow
    def start_section(self, message: dict, section: str) -> None:
        user = self._user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self.clear(uid)
        self.app._clear_analysis_pending(uid)
        items = self.db.list_tundish_report_items(section)
        self.pending[uid] = {
            "section": section,
            "await": "mode",
            "items_def": items,
            "steps": self._build_steps(items),
            "fields": {},
            "items": {},
            "done": [],
            "history": [],
            "edit_one": False,
            "raw_text": None,
        }
        n = len(items)
        self._reply(
            message,
            f"🧾 {tr.TITLE_FA} — {tr.section_label(section)}\n"
            f"۵ فیلد ثابت + {n} قلم تنظیم‌شده.\n"
            "• ✍️ ورود مرحله‌ای: هر فیلد جدا پرسیده می‌شود (اکثراً با دکمه).\n"
            "• 📋 ورود سریع از متن: متن گزارش را یک‌جا بفرستید؛ موارد شناخته‌شده پر و بقیه پرسیده می‌شود.",
            kb.tundish_report_mode_menu(),
        )

    @staticmethod
    def _build_steps(items: list[dict]) -> list[str]:
        return [f"f:{f['key']}" for f in tr.FIXED_FIELDS] + [f"i:{it['id']}" for it in items]

    @staticmethod
    def _item_def(p: dict, step: str) -> dict | None:
        if not step.startswith("i:"):
            return None
        iid = int(step[2:])
        for it in p["items_def"]:
            if int(it["id"]) == iid:
                return it
        return None

    def _step_label(self, p: dict, step: str) -> str:
        if step.startswith("f:"):
            return tr.FIXED_BY_KEY[step[2:]]["label"]
        it = self._item_def(p, step)
        return it["label"] if it else step

    def _step_value(self, p: dict, step: str) -> Any:
        if step.startswith("f:"):
            return p["fields"].get(step[2:])
        return p["items"].get(step[2:])

    def _step_optional(self, p: dict, step: str) -> bool:
        it = self._item_def(p, step)
        return bool(it and not it.get("required"))

    def _step_options(self, p: dict, step: str) -> list[str]:
        if step == "f:line":
            return tr.get_lines(self.db, p["section"])
        it = self._item_def(p, step)
        if it and it.get("item_type") == "choice":
            return list(it.get("options") or [])
        return []

    def _current_step(self, p: dict) -> str | None:
        return p.get("current")

    def _ask(self, message: dict, p: dict, step: str, prefix: str = "") -> None:
        p["current"] = step
        p["await"] = "step"
        label = self._step_label(p, step)
        optional = self._step_optional(p, step)
        opts = self._step_options(p, step)
        buttons = list(opts)
        hint = ""
        if step.startswith("f:"):
            kind = tr.FIXED_BY_KEY[step[2:]]["kind"]
            if kind == "int":
                hint = "یک عدد صحیح بفرستید."
            elif kind == "line":
                hint = "از دکمه‌ها انتخاب کنید."
            else:
                buttons = self.db.recent_tundish_steel_grades(p["section"])
                hint = "مارک را بفرستید" + (" یا از مارک‌های اخیر انتخاب کنید." if buttons else ".")
        else:
            it = self._item_def(p, step) or {}
            t = it.get("item_type")
            if t == "choice":
                hint = "از دکمه‌ها انتخاب کنید (یا متن کوتاه برای گزینهٔ دیگر)."
            elif t == "number":
                hint = "عدد بفرستید."
            else:
                hint = "متن کوتاه بفرستید."
        if optional:
            hint += " (اختیاری)"
        cur = self._step_value(p, step)
        cur_txt = f"\nمقدار فعلی: {tr.format_value(cur)}" if step in p["done"] else ""
        total = len(p["steps"])
        try:
            pos = p["steps"].index(step) + 1
        except ValueError:
            pos = 0
        self._reply(
            message,
            f"{prefix}({pos}/{total}) {label}\n{hint}{cur_txt}",
            kb.tundish_report_step_menu(buttons, optional=optional, can_prev=bool(p["history"])),
        )

    def _ask_next(self, message: dict, p: dict, prefix: str = "") -> None:
        for step in p["steps"]:
            if step not in p["done"]:
                self._ask(message, p, step, prefix)
                return
        self._show_confirm(message, p, prefix)

    def _summary_values(self, p: dict) -> list[dict]:
        out = []
        for it in p["items_def"]:
            out.append(
                {
                    "item_id": int(it["id"]),
                    "label": it["label"],
                    "item_type": it["item_type"],
                    "value": p["items"].get(str(it["id"])),
                }
            )
        return out

    def _show_confirm(self, message: dict, p: dict, prefix: str = "") -> None:
        p["await"] = "confirm"
        p["current"] = None
        summary = tr.build_summary(p["section"], p["fields"], self._summary_values(p))
        self._reply(
            message,
            f"{prefix}{summary}\n\nاطلاعات درست است؟",
            kb.tundish_report_confirm_menu(),
        )

    def _entry_text(self, message: dict, p: dict, text: str) -> bool:
        user = self._user(message)
        if not user:
            self.pending.pop(self.app._uid(message), None)
            return True
        aw = p.get("await")
        if aw == "mode":
            if text == kb.BTN_TR_MODE_STEP:
                p["mode"] = "step"
                self._ask_next(message, p)
            elif text == kb.BTN_TR_MODE_TEXT:
                p["mode"] = "text"
                p["await"] = "raw_text"
                self._reply(
                    message,
                    "متن گزارش را در یک پیام بفرستید. نمونه:\n" + SAMPLE_TEXT,
                    BaleClient.reply_keyboard([[kb.BTN_TR_MODE_STEP], [kb.BTN_TR_CANCEL]]),
                )
            else:
                self._reply(message, "روش ورود را از دکمه‌ها انتخاب کنید.", kb.tundish_report_mode_menu())
            return True
        if aw == "raw_text":
            if text == kb.BTN_TR_MODE_STEP:
                p["mode"] = "step"
                self._ask_next(message, p)
                return True
            self._apply_parsed(message, p, text)
            return True
        if aw == "step":
            if text == kb.BTN_TR_PREV:
                self._go_prev(message, p)
                return True
            if text == kb.BTN_TR_SKIP:
                step = self._current_step(p)
                if step and self._step_optional(p, step):
                    p["items"][step[2:]] = None
                    self._mark_done(p, step)
                    self._after_answer(message, p)
                elif step:
                    self._ask(message, p, step, "این مورد اجباری است.\n")
                return True
            if text in _OWN_NAV:
                step = self._current_step(p)
                if step:
                    self._ask(message, p, step, "لطفاً مقدار این مرحله را وارد کنید.\n")
                return True
            return self._answer_step(message, text)
        if aw == "confirm":
            if text == kb.BTN_TR_CONFIRM:
                self._submit(message, user, p)
            elif text == kb.BTN_TR_EDIT:
                self._edit_pick_prompt(message, p)
            else:
                self._show_confirm(message, p, "از دکمه‌ها استفاده کنید.\n")
            return True
        if aw == "edit_pick":
            digits = tr.normalize_digits(text).strip()
            if digits.isdigit() and 1 <= int(digits) <= len(p["steps"]):
                step = p["steps"][int(digits) - 1]
                p["edit_one"] = True
                self._ask(message, p, step)
            elif text == kb.BTN_TR_CONFIRM:
                self._submit(message, user, p)
            else:
                self._edit_pick_prompt(message, p, "شمارهٔ مورد را بفرستید.\n")
            return True
        return False

    def _mark_done(self, p: dict, step: str) -> None:
        if step not in p["done"]:
            p["done"].append(step)
        if not p.get("edit_one"):
            p["history"].append(step)

    def _after_answer(self, message: dict, p: dict) -> None:
        if p.get("edit_one"):
            p["edit_one"] = False
            self._show_confirm(message, p, "✅ اصلاح شد.\n\n")
            return
        self._ask_next(message, p)

    def _answer_step(self, message: dict, text: str) -> bool:
        uid = self.app._uid(message)
        p = self.pending.get(uid)
        if not p:
            return False
        step = self._current_step(p)
        if not step:
            return False
        try:
            if step.startswith("f:"):
                val = tr.clean_fixed(step[2:], text, lines=tr.get_lines(self.db, p["section"]))
                p["fields"][step[2:]] = val
            else:
                it = self._item_def(p, step) or {}
                val = tr.clean_item_value(it, text)
                p["items"][step[2:]] = val
        except ValueError as exc:
            self._ask(message, p, step, f"⚠️ {exc}\n")
            return True
        self._mark_done(p, step)
        self._after_answer(message, p)
        return True

    def step_back(self, message: dict) -> bool:
        """«⬅️ بازگشت» while entering a report: previous question (False = leave)."""
        p = self.pending.get(self.app._uid(message))
        if not p:
            return False
        aw = p.get("await")
        if aw == "step" and (p.get("edit_one") or p.get("history")):
            self._go_prev(message, p)
            return True
        if aw == "edit_pick":
            self._show_confirm(message, p)
            return True
        return False

    def _go_prev(self, message: dict, p: dict) -> None:
        cur = self._current_step(p)
        if p.get("edit_one"):
            p["edit_one"] = False
            self._show_confirm(message, p)
            return
        if not p["history"]:
            if cur:
                self._ask(message, p, cur, "مرحلهٔ قبلی وجود ندارد.\n")
            return
        prev = p["history"].pop()
        if prev in p["done"]:
            p["done"].remove(prev)
        self._ask(message, p, prev)

    def _apply_parsed(self, message: dict, p: dict, text: str) -> None:
        section = p["section"]
        lines = tr.get_lines(self.db, section)
        parsed = tr.parse_report_text(text, section, lines, p["items_def"])
        p["raw_text"] = text
        found: list[str] = []
        for key, raw in parsed["fields"].items():
            try:
                p["fields"][key] = tr.clean_fixed(key, raw, lines=lines)
            except ValueError:
                continue
            step = f"f:{key}"
            if step not in p["done"]:
                p["done"].append(step)
            found.append(f"{tr.FIXED_BY_KEY[key]['label']}: {p['fields'][key]}")
        for iid, raw in parsed["items"].items():
            step = f"i:{iid}"
            it = self._item_def(p, step)
            if not it:
                continue
            try:
                val = tr.clean_item_value(it, raw)
            except ValueError:
                continue
            p["items"][str(iid)] = val
            if step not in p["done"]:
                p["done"].append(step)
            found.append(f"{it['label']}: {tr.format_value(val)}")
        if found:
            prefix = "🔎 از متن شناسایی شد:\n" + "\n".join("• " + f for f in found) + "\n\n"
        else:
            prefix = "موردی از متن شناسایی نشد؛ فیلدها را مرحله‌ای وارد کنید.\n\n"
        missing = [s for s in p["steps"] if s not in p["done"]]
        if missing:
            prefix += f"{len(missing)} مورد باقی مانده است.\n"
        self._ask_next(message, p, prefix)

    def _edit_pick_prompt(self, message: dict, p: dict, prefix: str = "") -> None:
        p["await"] = "edit_pick"
        lines = [f"{prefix}کدام مورد اصلاح شود؟ شماره را بفرستید:"]
        for i, step in enumerate(p["steps"], start=1):
            lines.append(f"{i}) {self._step_label(p, step)}: {tr.format_value(self._step_value(p, step))}")
        rows: list[list[str]] = []
        row: list[str] = []
        for i in range(1, len(p["steps"]) + 1):
            row.append(str(i))
            if len(row) == 6:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([kb.BTN_TR_CONFIRM])
        rows.append([kb.BTN_TR_CANCEL])
        self._reply(message, "\n".join(lines), BaleClient.reply_keyboard(rows))

    def _submit(self, message: dict, user: dict, p: dict) -> None:
        uid = str(user["bale_user_id"])
        rep, errors = tr.submit(
            self.db,
            user,
            p["section"],
            p["fields"],
            p["items"],
            source="bot",
            raw_text=p.get("raw_text"),
        )
        if errors:
            # items may have changed in settings meanwhile — refresh and ask what's missing
            items = self.db.list_tundish_report_items(p["section"])
            p["items_def"] = items
            p["steps"] = self._build_steps(items)
            valid_ids = {str(it["id"]) for it in items}
            p["items"] = {k: v for k, v in p["items"].items() if k in valid_ids}
            p["done"] = [s for s in p["done"] if s in p["steps"]]
            p["history"] = [s for s in p["history"] if s in p["steps"]]
            p["edit_one"] = False
            self._ask_next(message, p, "⚠️ " + "\n⚠️ ".join(errors) + "\n\n")
            return
        self.pending.pop(uid, None)
        log_activity(self.db, user, "tundish_report_saved", tundish_group=p["section"])
        self._reply(
            message,
            f"✅ گزارش #{rep['id']} در {rep['created_at_tehran']} (تهران) ثبت شد.\n\n{rep.get('summary_text') or ''}",
            kb.tundish_report_menu(user),
        )

    # ------------------------------------------------------------ settings
    def open_settings(self, message: dict) -> None:
        user = self._admin(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self.clear(uid)
        counts = []
        for s in tr.SECTION_ORDER:
            counts.append(f"• {tr.section_label(s)}: {len(self.db.list_tundish_report_items(s))} قلم")
        self._reply(
            message,
            f"⚙️ تنظیمات {tr.TITLE_FA}\nبخش را برای مدیریت اقلام انتخاب کنید:\n" + "\n".join(counts),
            kb.tundish_report_settings_menu(),
        )

    def open_section_settings(self, message: dict, section: str, prefix: str = "") -> None:
        user = self._admin(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self.pending.pop(uid, None)
        self.settings_pending[uid] = {"section": section, "await": None}
        self._reply(
            message,
            prefix + tr.describe_section_settings(self.db, section),
            kb.tundish_report_section_settings_menu(),
        )

    def _pick_item(self, sp: dict, text: str) -> dict | None:
        digits = tr.normalize_digits(text).strip()
        if not digits.isdigit():
            return None
        items = self.db.list_tundish_report_items(sp["section"])
        idx = int(digits)
        if 1 <= idx <= len(items):
            return items[idx - 1]
        return None

    def _item_list_prompt(self, sp: dict, title: str) -> str:
        items = self.db.list_tundish_report_items(sp["section"])
        lines = [title]
        lines.extend(f"{i}) {it['label']}" for i, it in enumerate(items, start=1))
        return "\n".join(lines)

    def _settings_text(self, message: dict, sp: dict, text: str) -> bool:
        user = self._admin(message)
        if not user:
            return True
        uid = str(user["bale_user_id"])
        section = sp["section"]
        aw = sp.get("await")

        if text == kb.BTN_TRS_BACK_SECTION:
            self.open_section_settings(message, section)
            return True
        if text == kb.BTN_TRS_ADD:
            sp.update({"await": "add_label", "draft": {}})
            self._reply(message, "برچسب قلم جدید را بفرستید (مثلاً «نازل — وضعیت»):", kb.tundish_report_back_section_menu())
            return True
        if text == kb.BTN_TRS_EDIT:
            if not self.db.list_tundish_report_items(section):
                self.open_section_settings(message, section, "قلمی برای ویرایش نیست.\n\n")
                return True
            sp.update({"await": "edit_pick"})
            self._reply(message, self._item_list_prompt(sp, "شمارهٔ قلم برای ویرایش:"), kb.tundish_report_back_section_menu())
            return True
        if text == kb.BTN_TRS_DELETE:
            if not self.db.list_tundish_report_items(section):
                self.open_section_settings(message, section, "قلمی برای حذف نیست.\n\n")
                return True
            sp.update({"await": "delete_pick"})
            self._reply(message, self._item_list_prompt(sp, "شمارهٔ قلم برای حذف:"), kb.tundish_report_back_section_menu())
            return True
        if text == kb.BTN_TRS_LINES:
            sp.update({"await": "lines"})
            cur = tr.get_lines(self.db, section)
            self._reply(
                message,
                "خطوط / ماشین‌های فعلی:\n" + "\n".join(cur) + "\n\nفهرست جدید را بفرستید (هر خط در یک سطر یا با «،» جدا):",
                kb.tundish_report_back_section_menu(),
            )
            return True

        # edit-menu actions (item selected)
        if text in {kb.BTN_TRS_E_LABEL, kb.BTN_TRS_E_TYPE, kb.BTN_TRS_E_OPTIONS, kb.BTN_TRS_E_REQUIRED, kb.BTN_TRS_E_ORDER}:
            item = self.db.get_tundish_report_item(int(sp.get("item_id") or 0)) if sp.get("item_id") else None
            if not item:
                self.open_section_settings(message, section, "ابتدا قلم را انتخاب کنید.\n\n")
                return True
            if text == kb.BTN_TRS_E_LABEL:
                sp["await"] = "edit_label"
                self._reply(message, f"برچسب جدید برای «{item['label']}»:", kb.tundish_report_back_section_menu())
            elif text == kb.BTN_TRS_E_TYPE:
                sp["await"] = "edit_type"
                self._reply(message, "نوع جدید را انتخاب کنید:", kb.tundish_report_type_menu())
            elif text == kb.BTN_TRS_E_OPTIONS:
                sp["await"] = "edit_options"
                cur = "، ".join(item["options"]) or "—"
                self._reply(
                    message,
                    f"گزینه‌های فعلی: {cur}\nفهرست جدید گزینه‌ها را بفرستید (هر گزینه در یک سطر یا با «،» جدا):",
                    kb.tundish_report_back_section_menu(),
                )
            elif text == kb.BTN_TRS_E_REQUIRED:
                try:
                    tr.update_item(self.db, item["id"], required=not item["required"], updated_by=uid)
                except ValueError as exc:
                    self._reply(message, f"⚠️ {exc}", kb.tundish_report_item_edit_menu())
                    return True
                log_activity(self.db, user, "tundish_report_settings")
                self._show_item_edit(message, sp, "✅ وضعیت اجباری/اختیاری تغییر کرد.\n")
            else:
                n = len(self.db.list_tundish_report_items(section))
                sp["await"] = "edit_order"
                self._reply(message, f"جایگاه جدید (۱ تا {n}) را بفرستید:", kb.tundish_report_back_section_menu())
            return True

        try:
            return self._settings_await(message, user, sp, aw, text)
        except ValueError as exc:
            self._reply(message, f"⚠️ {exc}\nدوباره بفرستید یا بازگردید.", kb.tundish_report_back_section_menu())
            return True

    def _show_item_edit(self, message: dict, sp: dict, prefix: str = "") -> None:
        item = self.db.get_tundish_report_item(int(sp["item_id"]))
        if not item:
            self.open_section_settings(message, sp["section"], "قلم یافت نشد.\n\n")
            return
        sp["await"] = "edit_menu"
        items = self.db.list_tundish_report_items(sp["section"])
        idx = next((i for i, it in enumerate(items, start=1) if it["id"] == item["id"]), None)
        self._reply(message, prefix + "ویرایش قلم:\n" + tr.describe_item(item, idx), kb.tundish_report_item_edit_menu())

    def _settings_await(self, message: dict, user: dict, sp: dict, aw: str | None, text: str) -> bool:
        uid = str(user["bale_user_id"])
        section = sp["section"]
        if aw == "add_label":
            lab, _, _ = tr.validate_item_def(text, "text", None)
            sp["draft"] = {"label": lab}
            sp["await"] = "add_type"
            self._reply(message, f"نوع قلم «{lab}» را انتخاب کنید:", kb.tundish_report_type_menu())
            return True
        if aw == "add_type":
            t = tr.ITEM_TYPE_BY_LABEL.get(text)
            if not t:
                self._reply(message, "نوع را از دکمه‌ها انتخاب کنید.", kb.tundish_report_type_menu())
                return True
            sp["draft"]["item_type"] = t
            if t == "choice":
                sp["await"] = "add_options"
                self._reply(message, "گزینه‌ها را بفرستید (هر گزینه در یک سطر یا با «،» جدا):", kb.tundish_report_back_section_menu())
            else:
                sp["await"] = "add_required"
                self._reply(message, "این قلم اجباری است یا اختیاری؟", kb.tundish_report_required_menu())
            return True
        if aw == "add_options":
            _, _, opts = tr.validate_item_def(sp["draft"]["label"], "choice", text)
            sp["draft"]["options"] = opts
            sp["await"] = "add_required"
            self._reply(message, "این قلم اجباری است یا اختیاری؟", kb.tundish_report_required_menu())
            return True
        if aw == "add_required":
            if text not in {kb.BTN_TRS_REQUIRED, kb.BTN_TRS_OPTIONAL}:
                self._reply(message, "از دکمه‌ها انتخاب کنید.", kb.tundish_report_required_menu())
                return True
            d = sp["draft"]
            item = tr.add_item(
                self.db,
                section,
                label=d["label"],
                item_type=d["item_type"],
                options=d.get("options"),
                required=text == kb.BTN_TRS_REQUIRED,
                updated_by=uid,
            )
            log_activity(self.db, user, "tundish_report_settings")
            self.open_section_settings(message, section, f"✅ قلم «{item['label']}» اضافه شد.\n\n")
            return True
        if aw == "edit_pick":
            item = self._pick_item(sp, text)
            if not item:
                self._reply(message, self._item_list_prompt(sp, "شمارهٔ معتبر بفرستید:"), kb.tundish_report_back_section_menu())
                return True
            sp["item_id"] = int(item["id"])
            self._show_item_edit(message, sp)
            return True
        if aw == "edit_label":
            tr.update_item(self.db, int(sp["item_id"]), label=text, updated_by=uid)
            log_activity(self.db, user, "tundish_report_settings")
            self._show_item_edit(message, sp, "✅ برچسب تغییر کرد.\n")
            return True
        if aw == "edit_type":
            t = tr.ITEM_TYPE_BY_LABEL.get(text)
            if not t:
                self._reply(message, "نوع را از دکمه‌ها انتخاب کنید.", kb.tundish_report_type_menu())
                return True
            item = self.db.get_tundish_report_item(int(sp["item_id"]))
            if t == "choice" and not (item or {}).get("options"):
                sp["await"] = "edit_options_for_choice"
                self._reply(message, "برای نوع انتخابی، گزینه‌ها را بفرستید:", kb.tundish_report_back_section_menu())
                return True
            tr.update_item(self.db, int(sp["item_id"]), item_type=t, updated_by=uid)
            log_activity(self.db, user, "tundish_report_settings")
            self._show_item_edit(message, sp, "✅ نوع تغییر کرد.\n")
            return True
        if aw in {"edit_options", "edit_options_for_choice"}:
            kwargs: dict[str, Any] = {"options": text}
            if aw == "edit_options_for_choice":
                kwargs["item_type"] = "choice"
            item = self.db.get_tundish_report_item(int(sp["item_id"]))
            if item and item["item_type"] != "choice" and aw == "edit_options":
                self._show_item_edit(message, sp, "گزینه‌ها فقط برای قلم انتخابی معنا دارند؛ ابتدا نوع را «انتخابی» کنید.\n")
                return True
            tr.update_item(self.db, int(sp["item_id"]), updated_by=uid, **kwargs)
            log_activity(self.db, user, "tundish_report_settings")
            self._show_item_edit(message, sp, "✅ گزینه‌ها ذخیره شد.\n")
            return True
        if aw == "edit_order":
            digits = tr.normalize_digits(text).strip()
            if not digits.isdigit():
                raise ValueError("جایگاه باید عدد باشد.")
            tr.move_item(self.db, int(sp["item_id"]), int(digits))
            log_activity(self.db, user, "tundish_report_settings")
            self._show_item_edit(message, sp, "✅ ترتیب تغییر کرد.\n")
            return True
        if aw == "delete_pick":
            item = self._pick_item(sp, text)
            if not item:
                self._reply(message, self._item_list_prompt(sp, "شمارهٔ معتبر بفرستید:"), kb.tundish_report_back_section_menu())
                return True
            sp["item_id"] = int(item["id"])
            sp["await"] = "delete_confirm"
            self._reply(
                message,
                f"قلم «{item['label']}» حذف شود؟ (گزارش‌های قبلی دست نمی‌خورند.)",
                kb.tundish_report_delete_confirm_menu(),
            )
            return True
        if aw == "delete_confirm":
            if text != kb.BTN_TRS_DELETE_CONFIRM:
                self._reply(message, "برای حذف، «تأیید حذف» را بزنید یا بازگردید.", kb.tundish_report_delete_confirm_menu())
                return True
            item = self.db.get_tundish_report_item(int(sp["item_id"]))
            tr.delete_item(self.db, int(sp["item_id"]))
            log_activity(self.db, user, "tundish_report_settings")
            self.open_section_settings(message, section, f"🗑 قلم «{(item or {}).get('label', '')}» حذف شد.\n\n")
            return True
        if aw == "lines":
            vals = tr.set_lines(self.db, section, text, updated_by=uid)
            log_activity(self.db, user, "tundish_report_settings")
            self.open_section_settings(message, section, "✅ خطوط ذخیره شد: " + "، ".join(vals) + "\n\n")
            return True
        # idle in section settings
        self.open_section_settings(message, section, "از دکمه‌ها استفاده کنید.\n\n")
        return True
