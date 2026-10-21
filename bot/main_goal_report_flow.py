"""Bale UX for «🎯 هدف اصلی» — سابقهٔ چندماهه + دو سناریو.

Menu: منوی اصلی → 🎯 هدف اصلی
Roles: owner / manager / responsible_officer (حذف ماه: owner / manager)

Flow:
  1) 📥 ثبت ورودی ماه — 📝 آمار تولید (ورود متنی)، اکسل مصرف تاندیش هر بخش، یا
     📦 آپلود گروهی — هر فایل جداگانه (نوع + ماه خودکار) ذخیره و باقی‌ماندهٔ ماه اعلام می‌شود
  2) 🗂 ماه‌های ذخیره‌شده (فقط در منوی هدف اصلی؛ حذف برای مالک/مدیر)
  3) 🎯 سناریو ۱: تناژ هدف (بازه + بخش) → تاندیش و مواد لازم → PDF + اکسل
     🔮 سناریو ۲: پیش‌بینی N ماه آینده با روند فعلی → PDF + اکسل

Shared compute lives in ``services.main_goal_report`` / ``services.main_goal_history``
(also used by web).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from analytics.frames import load_primary_inventory
from bot import keyboards as kb
from services import permissions as perm
from services import user_errors
from bot.activity import log_activity
from bot.jalali import tehran_now
from services.file_names import unique_dir
from config import REPORT_DIR, UPLOAD_DIR, ensure_dirs
from services import main_goal_history as mgh
from services import main_goal_persist as mgp
from services import main_goal_report as mg
from bot.production_stats_flow import PS_BUTTONS, ProductionStatsMixin

if TYPE_CHECKING:  # pragma: no cover
    from bot.handlers import BotApp

logger = logging.getLogger(__name__)

PHOTO_REMOVED_FA = (
    "ورود آمار تولید با عکس حذف شده است؛ از دکمهٔ «📝 آمار تولید» مقادیر را متنی وارد کنید."
)

_MENU_BTNS = {
    kb.BTN_MG_MENU,
    kb.BTN_MG_BULK,
    kb.BTN_MG_HISTORY,
    kb.BTN_MG_DELETE,
    kb.BTN_MG_SCN_TARGET,
    kb.BTN_MG_SCN_FORECAST,
    kb.BTN_MG_RECENT,
    kb.BTN_MG_INPUTS,
    kb.BTN_MG_INPUT_PROD,
    kb.BTN_MG_INPUT_PROD_XLSX,
    kb.BTN_MG_INPUT_BILLET,
    kb.BTN_MG_INPUT_BLOOM,
    kb.BTN_MG_INPUT_SLAB,
    kb.BTN_MG_REPORT_REQ,
}
# Buttons that only make sense inside a pending step
_STEP_BTNS = {
    kb.BTN_MG_SKIP_TARGET,
    kb.BTN_MG_BULK_DONE,
    kb.BTN_MG_BULK_STOP,
    kb.BTN_MG_ADD_SECTION,
    kb.BTN_MG_COMPUTE,
    kb.BTN_MG_P1,
    kb.BTN_MG_P3,
    kb.BTN_MG_P6,
    kb.BTN_MG_P12,
    *kb.MG_SECTION_BUTTONS.keys(),
    kb.BTN_MG_RANGE_3,
    kb.BTN_MG_RANGE_6,
    kb.BTN_MG_RANGE_12,
    kb.BTN_MG_RANGE_CUSTOM,
    kb.BTN_MG_CONFIRM_PARTIAL,
    *PS_BUTTONS,
}
_RANGE_BTNS = {
    kb.BTN_MG_RANGE_3: 3,
    kb.BTN_MG_RANGE_6: 6,
    kb.BTN_MG_RANGE_12: 12,
    **kb.MG_RANGE_LEGACY,
}
_INPUT_SECTION = {
    kb.BTN_MG_INPUT_BILLET: "billet",
    kb.BTN_MG_INPUT_BLOOM: "bloom",
    kb.BTN_MG_INPUT_SLAB: "slab",
}
_PERIOD_BTN_MONTHS = {kb.BTN_MG_P1: 1, kb.BTN_MG_P3: 3, kb.BTN_MG_P6: 6, kb.BTN_MG_P12: 12}


def _global_buttons() -> set[str]:
    vals = {
        v
        for n, v in vars(kb).items()
        if n.startswith("BTN_") and isinstance(v, str)
    }
    vals |= set(kb.PENDING_INPUT_RESERVED_TEXTS)
    return vals


class MainGoalReportFlow(ProductionStatsMixin):
    def __init__(self, app: "BotApp") -> None:
        self.app = app
        self.pending: dict[str, dict[str, Any]] = {}
        self._reserved = _global_buttons()

    @property
    def db(self):
        return self.app.db

    def clear(self, uid: str) -> None:
        self.pending.pop(str(uid), None)
        self.app._file_guide_kind.pop(str(uid), None)

    def _reply(self, message: dict, text: str, markup: dict | None = None) -> None:
        # Bale messages max ~4096 chars
        if len(text) > 3900:
            text = text[:3900] + "\n…"
        self.app._reply(message, text, markup)

    def _user(self, message: dict) -> dict | None:
        user = self.app._user_or_deny(message)
        if not user:
            return None
        p = self.pending.get(str(user["bale_user_id"])) or {}
        if p.get("via_files") and perm.can(user, perm.FILE_INPUTS):
            return user
        if self.app._deny_technician(message, user, perm.MAIN_GOAL):
            return None
        if not mg.can_run(user):
            self._reply(
                message,
                "دسترسی «هدف اصلی» ندارید (مالک / مدیر / کاردان مسئول).",
                kb.main_menu(user),
            )
            return None
        return user

    def _done_menu(self, p: dict | None, user: dict | None = None) -> dict:
        """Where an input step returns: «📤 ورود فایل‌ها» if started there, else ورودی‌ها."""
        if (p or {}).get("via_files"):
            return kb.upload_files_menu(user)
        return kb.main_goal_inputs_menu()

    def _inventory(self, user: dict):
        try:
            return load_primary_inventory(self.db, user)
        except Exception as exc:  # noqa: BLE001
            logger.warning("main_goal inventory load failed: %s", exc)
            return None

    # ------------------------------------------------------------ dispatcher
    def handle_text(self, message: dict, text: str) -> bool:
        uid = self.app._uid(message)
        text = (text or "").strip()

        if text == kb.BTN_MG_MENU:
            self.open_menu(message)
            return True
        if text == kb.BTN_MG_BULK:
            self.start_bulk(message)
            return True
        if text == kb.BTN_MG_HISTORY:
            self.show_history(message)
            return True
        if text == kb.BTN_MG_DELETE:
            self.start_delete(message)
            return True
        if text == kb.BTN_MG_SCN_TARGET:
            self.start_target(message)
            return True
        if text == kb.BTN_MG_SCN_FORECAST:
            self.start_forecast(message)
            return True
        if text == kb.BTN_MG_RECENT:
            self.show_recent(message)
            return True
        if text == kb.BTN_MG_INPUTS:
            self.open_inputs(message)
            return True
        if text == kb.BTN_PRODUCTION_STATS:
            nav = (getattr(self.app, "_nav", {}) or {}).get(uid) or {}
            here = nav.get("origin") if nav.get("key") == "flow" else nav.get("key")
            self.start_production_stats(message, via_files=(here == "files"))
            return True
        if text == kb.BTN_MG_INPUT_PROD_XLSX:
            self.start_prod_xlsx(message)
            return True
        if text in _INPUT_SECTION:
            self.start_cons_xlsx(message, _INPUT_SECTION[text])
            return True
        if text == kb.BTN_MG_REPORT_REQ:
            self.start_range_report(message)
            return True

        p = self.pending.get(uid)
        if not p:
            if text in (_STEP_BTNS - set(kb.MG_SECTION_BUTTONS) - set(_PERIOD_BTN_MONTHS)):
                user = self._user(message)
                if user:
                    self._reply(message, "گزارش هدف اصلی فعالی در جریان نیست.", kb.main_goal_menu())
                return True
            return False

        if text in self._reserved and text not in _STEP_BTNS:
            # global navigation abandons the draft
            self.clear(uid)
            return False

        mode = p.get("await")
        if str(mode or "").startswith("ps_"):
            return self.handle_ps_text(message, p, text)
        if mode == "bulk":
            if text in (kb.BTN_MG_BULK_DONE, kb.BTN_MG_BULK_STOP):
                return self._bulk_done(message, p, stopped=text == kb.BTN_MG_BULK_STOP)
            self._reply(
                message,
                "فایل‌های Excel را به‌صورت Document بفرستید یا "
                f"«{kb.BTN_MG_BULK_DONE}» را بزنید.",
                kb.main_goal_bulk_menu(),
            )
            return True
        if mode == "delete_pick":
            return self._on_delete_pick(message, p, text)
        if mode == "scn_period":
            return self._on_scn_period(message, p, text)
        if mode == "scn_section":
            return self._on_scn_section(message, p, text)
        if mode == "scn_tons":
            return self._on_scn_tons(message, p, text)
        if mode == "scn_confirm":
            return self._on_scn_confirm(message, p, text)
        if mode == "fc_months":
            return self._on_fc_months(message, p, text)
        if mode == "prod_xlsx":
            self._reply(message, "در انتظار فایل Excel آمار تولید (.xlsx) هستید.", kb.main_goal_upload_menu())
            return True
        if mode == "cons_xlsx":
            sec = p.get("section") or "billet"
            self._reply(
                message,
                f"در انتظار اکسل مصرف تاندیش {mg.SECTION_LABEL_FA[sec]} (.xlsx) هستید.",
                kb.main_goal_upload_menu(),
            )
            return True
        if mode == "seq_xlsx":
            self._reply(
                message,
                "در انتظار فایل Excel «گزارش جامع تاندیش در سکوئنس» (.xlsx) هستید.",
                kb.main_goal_upload_menu(),
            )
            return True
        if mode == "range_pick":
            return self._on_range_pick(message, p, text)
        if mode == "range_custom":
            return self._on_range_custom(message, p, text)
        if mode == "range_confirm":
            return self._on_range_confirm(message, p, text)
        return True

    def step_back(self, message: dict) -> bool:
        """«⬅️ بازگشت» inside a هدف اصلی flow: one step up (False = first step → leave)."""
        uid = self.app._uid(message)
        p = self.pending.get(uid)
        if not p:
            return False
        aw = p.get("await")
        if str(aw or "").startswith("ps_"):
            if aw == "ps_menu":
                return False
            return self._ps_menu(message, p)
        if aw == "scn_section":
            self.start_target(message)
            return True
        if aw in {"scn_tons", "scn_confirm"}:
            if aw == "scn_tons":
                p.pop("section", None)
            return self._on_scn_period(message, p, p.get("period_text") or "")
        if aw in {"range_custom", "range_confirm"}:
            self.start_range_report(message)
            return True
        return False

    def cancel(self, message: dict) -> None:
        """«✖️ انصراف» while a هدف اصلی step is pending (routed by BotApp.on_cancel)."""
        uid = self.app._uid(message)
        p = self.pending.get(uid) or {}
        if str(p.get("await") or "").startswith("ps_"):
            self.ps_cancel(message, p)
            return
        self.clear(uid)
        user = self.app._user_or_deny(message)
        if not user:
            return
        if p.get("await") == "bulk":
            self._reply(
                message,
                "📦 آپلود گروهی متوقف شد؛ ماه‌های ذخیره‌شده می‌مانند.\n" + self._bulk_status_text(p),
                kb.main_goal_inputs_menu(),
            )
            return
        back_inputs = p.get("await") in {"prod_xlsx", "cons_xlsx", "seq_xlsx"}
        if p.get("via_files"):
            markup = kb.upload_files_menu(user)
        else:
            markup = kb.main_goal_inputs_menu() if back_inputs else kb.main_goal_menu()
        self._reply(
            message,
            "لغو شد؛ چیزی ذخیره نشد.",
            markup,
        )

    # ------------------------------------------------------------ menu / history
    def open_menu(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        self.clear(str(user["bale_user_id"]))
        months = mgh.load_history(self.db)
        hist = mgh.history_overview_text(months, mgp.provisional_tons_by_key(self.db))
        self._reply(
            message,
            f"🎯 {mg.TITLE_FA}\n{mg.SUBTITLE_FA}\n\n"
            f"• «{kb.BTN_MG_INPUTS}»: «{kb.BTN_PRODUCTION_STATS}» (ورود متنی)، اکسل مصرف تاندیش هر بخش یا «{kb.BTN_MG_BULK}».\n"
            f"• «{kb.BTN_MG_REPORT_REQ}»: بازه ۳/۶/۱۲ ماهه یا بازهٔ سفارشی از سابقه.\n"
            "• سناریو ۱ (تناژ هدف) و سناریو ۲ (پیش‌بینی ماه‌های آینده).\n\n"
            f"{hist}",
            kb.main_goal_menu(),
        )

    def show_history(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        self.clear(str(user["bale_user_id"]))
        months = mgh.load_history(self.db)
        text = mgh.history_overview_text(months, mgp.provisional_tons_by_key(self.db))
        missing = [o for o in mgp.month_completeness(self.db) if o["missing"]]
        if missing:
            text += "\n\nبخش‌های باقی‌مانده:\n" + "\n".join(
                f"• {o['label']}: " + "، ".join(o["missing"]) for o in missing[-8:]
            )
        self._reply(
            message,
            text,
            kb.main_goal_history_menu(can_delete=mgh.can_delete_month(user) and bool(months)),
        )

    def start_delete(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        if not mgh.can_delete_month(user):
            self._reply(message, "حذف ماه از سابقه فقط برای مالک یا مدیر مجاز است.", kb.main_goal_history_menu())
            return
        months = mgh.load_history(self.db)
        if not months:
            self._reply(message, "ماهی برای حذف وجود ندارد.", kb.main_goal_history_menu())
            return
        # by period_key: normalized-only months have no legacy main_goal_months id
        self.pending[str(user["bale_user_id"])] = {
            "await": "delete_pick",
            "keys": [m.period_key for m in months],
            "labels": [m.label for m in months],
        }
        self._reply(
            message,
            mgh.history_overview_text(months, mgp.provisional_tons_by_key(self.db)) + "\n\nشمارهٔ ردیف ماهی که باید حذف شود را بفرستید:",
            kb.main_goal_cancel_menu(),
        )

    def _on_delete_pick(self, message: dict, p: dict, text: str) -> bool:
        uid = self.app._uid(message)
        user = self._user(message)
        if not user or not mgh.can_delete_month(user):
            self.clear(uid)
            return True
        try:
            idx = int(mg.normalize_digits(text).strip())
            if idx < 1:
                raise IndexError
            key = p["keys"][idx - 1]
            label = (p.get("labels") or [])[idx - 1] if p.get("labels") else key
        except (ValueError, IndexError):
            self._reply(message, "شمارهٔ ردیف معتبر بفرستید.", kb.main_goal_cancel_menu())
            return True
        self.db.delete_main_goal_period(key)
        self.clear(uid)
        log_activity(self.db, user, "main_goal_delete_month")
        months = mgh.load_history(self.db)
        self._reply(
            message,
            f"🗑 ماه «{label}» از سابقه حذف شد.\n\n" + mgh.history_overview_text(months, mgp.provisional_tons_by_key(self.db)),
            kb.main_goal_history_menu(can_delete=bool(months)),
        )
        return True

    def show_recent(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        rows = self.db.list_main_goal_reports(limit=8)
        if not rows:
            self._reply(message, "هنوز گزارش هدف اصلی ثبت نشده است.", kb.main_goal_menu())
            return
        lines = ["📜 آخرین گزارش‌های هدف اصلی:"]
        for r in rows:
            who = r.get("actor_display_name") or r.get("bale_user_id") or "—"
            lines.append(
                f"• {r.get('period_label') or '—'} | {r.get('jalali_date') or ''} | {who}"
            )
        self._reply(message, "\n".join(lines), kb.main_goal_menu())

    # ------------------------------------------------------------ 📦 آپلود گروهی (inside 📥 ثبت ورودی ماه)
    def start_bulk(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self.pending[uid] = {
            "await": "bulk",
            "batch": "bulk_" + tehran_now().strftime("%Y%m%d_%H%M%S"),
            "seq": 0,
            "stored": [],
        }
        self.app._file_guide_kind[uid] = "mg_bulk"
        self._reply(
            message,
            "📦 آپلود گروهی\n"
            "فایل‌های پذیرفته‌شده (به هر ترتیب، هر تعداد ماه):\n"
            "• اکسل آمار تولید (.xlsx) — آمار تولید ماهانه را می‌توانید متنی از «📝 آمار تولید» وارد کنید\n"
            "• اکسل مصرف تاندیش بیلت / بلوم / اسلب (.xlsx)\n"
            "• اکسل لاگ سکوئنس تاندیش (.xlsx)\n"
            "نوع فایل و ماه به‌طور خودکار تشخیص داده می‌شود (از نام فایل، نام شیت یا سربرگ؛ "
            "بهتر است ماه شمسی مثل «شهریور ۱۴۰۵» در نام فایل باشد).\n"
            "پس از هر فایل می‌گویم کدام ماه/بخش ذخیره شد و چه چیزی از آن ماه مانده است.\n"
            f"در پایان «{kb.BTN_MG_BULK_DONE}» را بزنید.",
            kb.main_goal_bulk_menu(),
        )

    def handle_document(self, message: dict) -> bool:
        """Return True if this document belonged to a هدف اصلی upload step."""
        uid = self.app._uid(message)
        p = self.pending.get(uid)
        if not p or p.get("await") not in {"bulk", "prod_xlsx", "cons_xlsx", "seq_xlsx"}:
            return False
        if p.get("await") in {"prod_xlsx", "cons_xlsx", "seq_xlsx"}:
            return self._handle_input_document(message, p)
        user = self._user(message)
        if not user:
            self.clear(uid)
            return True
        doc = message.get("document") or {}
        file_name = (doc.get("file_name") or "").strip()
        file_id = doc.get("file_id")
        mime = str(doc.get("mime_type") or "")
        if file_id and (mime.startswith("image/") or file_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))):
            self._reply(message, PHOTO_REMOVED_FA, kb.main_goal_bulk_menu())
            return True
        if not file_id or not file_name.lower().endswith(".xlsx"):
            self._reply(
                message,
                f"فقط فایل .xlsx پذیرفته می‌شود (دریافت شد: {file_name or 'بدون‌نام'}).",
                kb.main_goal_bulk_menu(),
            )
            return True
        dest_dir = UPLOAD_DIR / str(user["bale_user_id"]) / "main_goal" / str(p["batch"])
        dest_dir.mkdir(parents=True, exist_ok=True)
        p["seq"] = int(p.get("seq") or 0) + 1
        dest = dest_dir / f"in_{p['seq']:03d}.xlsx"
        try:
            self.app.client.download_file(file_id, dest)
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal download failed")
            self._reply(message, user_errors.error_fa("دانلود فایل از بله ناموفق بود", exc), kb.main_goal_bulk_menu())
            return True
        return self._on_bulk_file(message, user, p, dest, file_name)

    def _bulk_store_excel(self, user: dict, dest: Path, file_name: str) -> tuple[Any, str, str]:
        """Detect + store one bulk Excel via the normalized persistence functions.

        Returns (InputStoreOutcome, activity key, kind label fa).
        """
        from services import main_goal_sequences as seq

        parsed = seq.parse_sequence_excel(dest)
        if parsed.ok and (parsed.section or seq.detect_section_from_file(dest, filename=file_name)):
            out = mgp.store_sequences_from_excel(
                self.db, dest, user=user, source="bot", filename=file_name,
                section=parsed.section or seq.detect_section_from_file(dest, filename=file_name),
            )
            return out, "main_goal_store_sequences", "لاگ سکوئنس تاندیش"
        kind, ksrc = mg.detect_file_kind(dest, filename=file_name)
        if kind == "production":
            out = mgp.store_production_from_excel(self.db, dest, user=user, source="bot", filename=file_name)
            return out, "main_goal_store_production_xlsx", "اکسل آمار تولید"
        if kind and kind.endswith("_consumption"):
            section = kind.split("_", 1)[0]
            out = mgp.store_consumption_from_excel(
                self.db, dest, section, user=user, source="bot", filename=file_name,
                inventory=self._inventory(user),
            )
            return out, "main_goal_store_consumption", f"مصرف تاندیش {mg.SECTION_LABEL_FA[section]}"
        return (
            mgp.InputStoreOutcome(
                ok=False,
                error_fa=(
                    f"نوع فایل «{file_name}» تشخیص نشد ({ksrc}). نام فایل را با «آمار تولید»، "
                    "«مصرف تاندیش بیلت/بلوم/اسلب» یا «سکوئنس تاندیش …» شروع کنید."
                ),
            ),
            "",
            "",
        )

    def _bulk_reply(self, message: dict, p: dict, head: str, out: Any, act: str, user: dict) -> bool:
        if not out.ok:
            self._reply(message, f"{head}\n❌ {out.error_fa or 'ذخیره نشد'}\n\n" + self._bulk_status_text(p), kb.main_goal_bulk_menu())
            return True
        log_activity(self.db, user, act)
        label = out.period_label or out.period_key or "—"
        if label not in p["stored"]:
            p["stored"].append(label)
        self._reply(
            message,
            f"{head}\n{out.summary}\n{self._remaining_line(out)}\n\n" + self._bulk_status_text(p),
            kb.main_goal_bulk_menu(),
        )
        return True

    def _on_bulk_file(self, message: dict, user: dict, p: dict, dest: Path, file_name: str) -> bool:
        try:
            out, act, kind_fa = self._bulk_store_excel(user, dest, file_name)
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal bulk store failed")
            self._reply(message, user_errors.error_fa(f"خطا در پردازش «{file_name}»", exc), kb.main_goal_bulk_menu())
            return True
        head = f"📄 «{file_name}»" + (f" → {kind_fa}" if kind_fa else "")
        return self._bulk_reply(message, p, head, out, act, user)

    @staticmethod
    def _remaining_line(out: Any) -> str:
        miss = list(getattr(out, "missing_parts", None) or [])
        label = getattr(out, "period_label", "") or ""
        if miss:
            return f"⏳ باقی‌ماندهٔ «{label}»: " + "، ".join(miss)
        return f"✅ همهٔ ورودی‌های «{label}» کامل است."

    def _bulk_status_text(self, p: dict, *, final: bool = False) -> str:
        stored = p.get("stored") or []
        if not stored:
            return "(هنوز فایلی در این نوبت ذخیره نشده)"
        lines = ["ماه‌های دارای ورودی در این نوبت: " + "، ".join(stored)]
        incomplete = [o for o in mgp.month_completeness(self.db) if o["label"] in stored and o["missing"]]
        for o in incomplete:
            lines.append(f"⏳ {o['label']}: مانده " + "، ".join(o["missing"]))
        return "\n".join(lines)

    def _bulk_done(self, message: dict, p: dict, *, stopped: bool = False) -> bool:
        uid = self.app._uid(message)
        user = self._user(message)
        self.clear(uid)
        if not user:
            return True
        n = len(mgh.load_history(self.db))
        head = (
            "📦 آپلود گروهی متوقف شد؛ ماه‌های ذخیره‌شده می‌مانند."
            if stopped
            else "📦 آپلود گروهی پایان یافت."
        )
        self._reply(
            message,
            head + "\n" + self._bulk_status_text(p, final=True) + "\n\n" + mgh.history_count_line(n),
            kb.main_goal_inputs_menu(),
        )
        return True

    # ------------------------------------------------------------ scenario 1: target tonnage
    def _history_gate(self, message: dict) -> list | None:
        months = mgh.load_history(self.db)
        if not months:
            self._reply(
                message,
                "هنوز هیچ ماهی در سابقه ذخیره نشده است.\n"
                f"ابتدا از «{kb.BTN_MG_INPUTS}» ورودی‌های حداقل {mgh.MIN_RECOMMENDED_MONTHS} ماه اخیر را ثبت کنید.",
                kb.main_goal_menu(),
            )
            return None
        return months

    def start_target(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        months = self._history_gate(message)
        if months is None:
            return
        self.pending[str(user["bale_user_id"])] = {"await": "scn_period", "targets": {}, "period_text": ""}
        self._reply(
            message,
            "🎯 سناریو ۱ — تناژ هدف\n"
            f"{mgh.history_count_line(len(months))}\n\n"
            "بازهٔ زمانی هدف را انتخاب کنید یا بنویسید "
            "(مثلاً «مهر ۱۴۰۵»، «۳ ماه»، «از مهر ۱۴۰۵ تا آذر ۱۴۰۵»):",
            kb.main_goal_period_menu(),
        )

    def _on_scn_period(self, message: dict, p: dict, text: str) -> bool:
        if text in kb.MG_SECTION_BUTTONS or text in {kb.BTN_MG_COMPUTE, kb.BTN_MG_ADD_SECTION}:
            self._reply(message, "ابتدا بازهٔ زمانی را بفرستید.", kb.main_goal_period_menu())
            return True
        p["period_text"] = text
        p["await"] = "scn_section"
        horizon = mgh.parse_horizon_months(text)
        hz = f" (≈ {horizon:g} ماه)" if horizon else " (طول بازه تشخیص نشد؛ فقط برای مقایسه با ظرفیت لازم است)"
        self._reply(
            message,
            f"بازه: {text}{hz}\n\n"
            "بخش را انتخاب کنید (بیلت / بلوم / اسلب / کل).\n"
            "میان‌بُر: می‌توانید یک‌جا بنویسید، مثلاً «بیلت ۲۰۰۰۰ بلوم ۱۰۰۰۰ اسلب ۳۰۰۰۰» یا «کل ۶۰۰۰۰».",
            kb.main_goal_section_menu(),
        )
        return True

    def _targets_text(self, p: dict) -> str:
        t = p.get("targets") or {}
        if not t:
            return "(هنوز تناژی وارد نشده)"
        return "\n".join(
            f"• {mgh.SECTION_CHOICES_FA.get(s, s)}: {v:,.0f} تن" for s, v in t.items()
        )

    def _on_scn_section(self, message: dict, p: dict, text: str) -> bool:
        if text in kb.MG_SECTION_BUTTONS:
            p["section"] = kb.MG_SECTION_BUTTONS[text]
            p["await"] = "scn_tons"
            self._reply(
                message,
                f"تناژ هدف «{mgh.SECTION_CHOICES_FA[p['section']]}» برای بازهٔ «{p.get('period_text')}» را به تن بفرستید (مثلاً 25000):",
                kb.main_goal_cancel_menu(),
            )
            return True
        parsed = mgh.parse_targets_text(text)
        if parsed:
            p.setdefault("targets", {}).update(parsed)
            p["await"] = "scn_confirm"
            self._reply(
                message,
                "تناژهای هدف:\n" + self._targets_text(p) + "\n\n«✅ محاسبه سناریو» یا «➕ افزودن بخش دیگر»؟",
                kb.main_goal_targets_confirm_menu(),
            )
            return True
        self._reply(message, "یکی از بخش‌ها را انتخاب کنید یا «بیلت ۲۰۰۰۰ …» بنویسید.", kb.main_goal_section_menu())
        return True

    def _on_scn_tons(self, message: dict, p: dict, text: str) -> bool:
        v = mgh.parse_number(text)
        if v is None or v <= 0:
            self._reply(message, "یک عدد مثبت (تن) بفرستید.", kb.main_goal_cancel_menu())
            return True
        p.setdefault("targets", {})[p.get("section") or "total"] = v
        p["await"] = "scn_confirm"
        self._reply(
            message,
            "تناژهای هدف:\n" + self._targets_text(p) + "\n\n«✅ محاسبه سناریو» یا «➕ افزودن بخش دیگر»؟",
            kb.main_goal_targets_confirm_menu(),
        )
        return True

    def _on_scn_confirm(self, message: dict, p: dict, text: str) -> bool:
        if text == kb.BTN_MG_ADD_SECTION:
            p["await"] = "scn_section"
            self._reply(message, "بخش بعدی را انتخاب کنید:", kb.main_goal_section_menu())
            return True
        if text == kb.BTN_MG_COMPUTE:
            return self._run_target(message, p)
        parsed = mgh.parse_targets_text(text)
        if parsed:
            p.setdefault("targets", {}).update(parsed)
            self._reply(message, "تناژهای هدف:\n" + self._targets_text(p), kb.main_goal_targets_confirm_menu())
            return True
        self._reply(message, "«✅ محاسبه سناریو» یا «➕ افزودن بخش دیگر» را بزنید.", kb.main_goal_targets_confirm_menu())
        return True

    def _run_target(self, message: dict, p: dict) -> bool:
        uid = self.app._uid(message)
        user = self._user(message)
        self.clear(uid)
        if not user:
            return True
        model = mgh.build_history_model(mgh.load_history(self.db), self._inventory(user))
        result = mgh.scenario_target(model, dict(p.get("targets") or {}), period_text=p.get("period_text") or "")
        return self._deliver(message, user, model, result, stem_prefix="main_goal_target")

    # ------------------------------------------------------------ scenario 2: forecast
    def start_forecast(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        months = self._history_gate(message)
        if months is None:
            return
        self.pending[str(user["bale_user_id"])] = {"await": "fc_months"}
        self._reply(
            message,
            "🔮 سناریو ۲ — پیش‌بینی با روند فعلی\n"
            f"{mgh.history_count_line(len(months))}\n\n"
            f"چند ماه آینده؟ (۱ تا {mgh.MAX_FORECAST_MONTHS}، مثلاً «۶ ماه آینده»)",
            kb.main_goal_horizon_menu(),
        )

    def _on_fc_months(self, message: dict, p: dict, text: str) -> bool:
        n = _PERIOD_BTN_MONTHS.get(text)
        if n is None:
            h = mgh.parse_horizon_months(text)
            n = int(round(h)) if h else None
        if not n or n < 1 or n > mgh.MAX_FORECAST_MONTHS:
            self._reply(message, f"تعداد ماه بین ۱ و {mgh.MAX_FORECAST_MONTHS} بفرستید.", kb.main_goal_horizon_menu())
            return True
        uid = self.app._uid(message)
        user = self._user(message)
        self.clear(uid)
        if not user:
            return True
        model = mgh.build_history_model(mgh.load_history(self.db), self._inventory(user))
        result = mgh.scenario_forecast(model, n)
        return self._deliver(message, user, model, result, stem_prefix="main_goal_forecast")

    # ------------------------------------------------------------ export
    def _deliver(self, message: dict, user: dict, model, result, *, stem_prefix: str) -> bool:
        # 19c: PDF/XLSX building runs in the background pool
        return self.app._heavy(
            message, "گزارش هدف اصلی", True,
            lambda: self._deliver_now(message, user, model, result, stem_prefix=stem_prefix),
        )

    def _deliver_now(self, message: dict, user: dict, model, result, *, stem_prefix: str) -> bool:
        if not result.ok:
            self._reply(message, result.error_fa or "خطا", kb.main_goal_menu())
            return True
        try:
            mgh.persist_scenario(self.db, result, model, user=user, source="bot")
        except Exception:  # noqa: BLE001
            logger.exception("main_goal scenario persist failed")
        ensure_dirs()
        stem = mgh.file_stem_fa(result)
        try:
            pdf_path, xlsx_path = mgh.export_scenario(
                result, stem=stem, report_dir=unique_dir(REPORT_DIR, "main_goal"),
                letterhead_path=self.app._letterhead_path()
            )
            chat = self.app._chat_id(message)
            self.app.client.send_document(chat, pdf_path, caption=result.title)
            self.app.client.send_document(chat, xlsx_path, caption=f"نسخه اکسل — {result.title}")
            self._reply(message, result.summary + "\n\nگزارش PDF و اکسل ارسال شد.", kb.main_goal_menu())
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal scenario export failed")
            self._reply(message, result.summary + "\n\n" + user_errors.error_fa("خطا در تولید PDF/اکسل", exc), kb.main_goal_menu())
        log_activity(self.db, user, f"report_{stem_prefix}")
        return True

    # ------------------------------------------------------------ الف) ثبت ورودی
    def open_inputs(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        self.clear(str(user["bale_user_id"]))
        overview = mgp.month_completeness(self.db)
        lines = [
            f"{kb.BTN_MG_INPUTS}",
            f"آپلود ورودی‌ها از «{kb.BTN_UPLOAD_MENU}»: موجودی انبار، مصرف ماهیانه، "
            "«گزارش جامع تاندیش در سکوئنس»؛ آمار تولید متنی از «📝 آمار تولید».",
            f"چند فایل/چند ماه با هم: «{kb.BTN_MG_BULK}» | راهنما: «{kb.BTN_FILE_GUIDE}»",
            "",
        ]
        if overview:
            lines.append("وضعیت ماه‌ها:")
            for o in overview[-8:]:
                status = "✅ کامل" if o["complete"] else ("⚠ ناقص: " + "، ".join(o["missing"]))
                if o.get("production_provisional"):
                    status += " | " + mgp.provisional_text_fa(o.get("provisional_total_tons"))
                if o.get("not_produced"):
                    status += " | بدون تولید: " + "، ".join(mg.SECTION_LABEL_FA.get(x, x) for x in o["not_produced"])
                elif o.get("production_note"):
                    status += f" | {o['production_note']}"
                lines.append(f"• {o['label']}: {status}")
        else:
            lines.append("هنوز ورودی‌ای ذخیره نشده است.")
        self._reply(message, "\n".join(lines), kb.main_goal_inputs_menu())

    def _start_user(self, message: dict, via_files: bool) -> dict | None:
        if not via_files:
            return self._user(message)
        user = self.app._user_or_deny(message)
        if not user:
            return None
        if not perm.can(user, perm.FILE_INPUTS):
            self._reply(message, "دسترسی «ورود فایل‌ها» ندارید.", kb.main_menu(user))
            return None
        return user

    def start_sequence_xlsx(self, message: dict, via_files: bool = True) -> None:
        """«📤 گزارش جامع تاندیش در سکوئنس»: one aggregated log for billet/bloom/slab.

        Rows are split by the «ماشین» column (section) and by heat-start month; each
        (month, section) pair replaces only its own previous data.
        """
        user = self._start_user(message, via_files)
        if not user:
            return
        uid = str(user["bale_user_id"])
        batch = tehran_now().strftime("%Y%m%d_%H%M%S")
        self.pending[uid] = {"await": "seq_xlsx", "batch": batch, "via_files": via_files}
        self.app._file_guide_kind[uid] = "mg_sequence"
        self._reply(
            message,
            "📤 فایل Excel «گزارش جامع تاندیش در سکوئنس» (خروجی Excel صفحهٔ "
            "otsteel.ksc.ir/ccssequence) را بفرستید.\n"
            "یک فایل برای هر سه بخش کافی است: بخش از ستون «ماشین» (اسلب ۱/۲، بلوم، بیلت ۱/۲) و "
            "ماه از تاریخ شروع ریخته‌گری تشخیص داده می‌شود؛ هر ماه/بخش جداگانه جایگزین می‌شود.",
            kb.main_goal_upload_menu(),
        )

    def start_prod_xlsx(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        batch = tehran_now().strftime("%Y%m%d_%H%M%S")
        self.pending[str(user["bale_user_id"])] = {"await": "prod_xlsx", "batch": batch}
        self.app._file_guide_kind[str(user["bale_user_id"])] = "mg_production_xlsx"
        self._reply(message, "📤 فایل Excel آمار تولید (.xlsx) را بفرستید.", kb.main_goal_upload_menu())

    def start_cons_xlsx(self, message: dict, section: str) -> None:
        user = self._user(message)
        if not user:
            return
        batch = tehran_now().strftime("%Y%m%d_%H%M%S")
        self.pending[str(user["bale_user_id"])] = {
            "await": "cons_xlsx",
            "batch": batch,
            "section": section,
        }
        self.app._file_guide_kind[str(user["bale_user_id"])] = "mg_consumption"
        self._reply(
            message,
            f"📤 اکسل مصرف تاندیش {mg.SECTION_LABEL_FA[section]} را بفرستید "
            f"(نام پیشنهادی شامل ماه جلالی، مثلاً «مصرف تاندیش {mg.SECTION_LABEL_FA[section]} شهریور ۱۴۰۵.xlsx»).",
            kb.main_goal_upload_menu(),
        )

    def handle_photo(self, message: dict) -> bool:
        """Photo/OCR input was removed: production stats are text-only (📝 آمار تولید)."""
        uid = self.app._uid(message)
        p = self.pending.get(uid)
        if not p:
            return False
        markup = kb.main_goal_bulk_menu() if p.get("await") == "bulk" else None
        self._reply(message, PHOTO_REMOVED_FA, markup)
        return True

    def _handle_input_document(self, message: dict, p: dict) -> bool:
        uid = self.app._uid(message)
        user = self._user(message)
        if not user:
            self.clear(uid)
            return True
        doc = message.get("document") or {}
        file_name = (doc.get("file_name") or "").strip()
        file_id = doc.get("file_id")
        if not file_id or not file_name.lower().endswith(".xlsx"):
            self._reply(message, "فقط فایل .xlsx بفرستید.", kb.main_goal_upload_menu())
            return True
        dest_dir = UPLOAD_DIR / str(user["bale_user_id"]) / "main_goal" / str(p["batch"])
        dest_dir.mkdir(parents=True, exist_ok=True)
        kind = {"prod_xlsx": "production", "seq_xlsx": "tundish_sequence"}.get(
            p["await"], f"{p.get('section')}_consumption"
        )
        dest = dest_dir / f"{kind}.xlsx"
        try:
            self.app.client.download_file(file_id, dest)
        except Exception as exc:  # noqa: BLE001
            self._reply(message, user_errors.error_fa("دانلود ناموفق", exc), kb.main_goal_upload_menu())
            return True
        try:
            if p["await"] == "seq_xlsx":
                out = mgp.store_sequences_from_excel(
                    self.db, dest, user=user, source="bot", filename=file_name, section=None
                )
                act = "main_goal_store_sequences"
            elif p["await"] == "prod_xlsx":
                out = mgp.store_production_from_excel(
                    self.db, dest, user=user, source="bot", filename=file_name
                )
                act = "main_goal_store_production_xlsx"
            else:
                # Prefer post-cast sequence log; fall back to material-consumption sheet
                from services import main_goal_sequences as seq

                kind = seq.detect_section_from_file(dest, filename=file_name)
                parsed = seq.parse_sequence_excel(dest)
                if parsed.ok:
                    out = mgp.store_sequences_from_excel(
                        self.db,
                        dest,
                        user=user,
                        source="bot",
                        filename=file_name,
                        section=p.get("section") or kind,
                    )
                    act = "main_goal_store_sequences"
                else:
                    out = mgp.store_consumption_from_excel(
                        self.db,
                        dest,
                        p["section"],
                        user=user,
                        source="bot",
                        filename=file_name,
                        inventory=self._inventory(user),
                    )
                    act = "main_goal_store_consumption"
        except Exception as exc:  # noqa: BLE001
            logger.exception("input store failed")
            self._reply(message, user_errors.error_fa("خطا", exc), kb.main_goal_menu())
            self.clear(uid)
            return True
        self.clear(uid)
        if not out.ok:
            self._reply(message, out.error_fa or "خطا", self._done_menu(p, user))
            return True
        log_activity(self.db, user, act)
        self._reply(message, out.summary + "\n" + self._remaining_line(out), self._done_menu(p, user))
        return True

    def start_range_report(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        months = self._history_gate(message)
        if months is None:
            return
        from services import period_select as ps

        latest = ps.latest_data_date(self.db)
        self.pending[str(user["bale_user_id"])] = {"await": "range_pick"}
        self._reply(
            message,
            "📊 درخواست گزارش از سابقهٔ ذخیره‌شده\n"
            f"{mgh.history_count_line(len(months))}\n"
            f"آخرین تاریخ داده: {ps.fmt_date(latest)}\n\n"
            "بازه را انتخاب کنید:\n"
            "• ۳ ماه اخیر = ۹۰ روز / ۶ ماه = ۱۸۰ روز / یک سال = ۳۶۵ روز، منتهی به آخرین تاریخ داده\n"
            "• بازه ورودی کاربر — تعداد روز، از تاریخ تا تاریخ، یا انتخاب ماه از فهرست",
            kb.main_goal_range_menu(),
        )

    # ---- inline month picker (historical only: future months 🔒)
    def _picker_markup(self, p: dict) -> dict:
        from services import period_select as ps

        t = ps.today()
        year = int(p.get("pick_year") or t.year)
        return kb.main_goal_month_picker_inline(
            year, p.get("pick") or [], ps.year_month_states(year), max_year=t.year
        )

    def _send_picker(self, message: dict, p: dict) -> None:
        chat_id = (message.get("chat") or {}).get("id") or self.app._uid(message)
        self.app.client.send_message(
            chat_id,
            "📅 ماه‌ها را از فهرست سال جلالی انتخاب کنید (چند ماه مجاز است). "
            "ماه‌های آینده 🔒 قفل‌اند؛ ماه جاری به‌صورت ناقص (تا آخرین روز داده) منظور می‌شود.",
            reply_markup=self._picker_markup(p),
        )

    def on_month_pick_callback(self, data: str, message: dict, user: dict, answer) -> None:
        from services import period_select as ps

        uid = str(user["bale_user_id"])
        p = self.pending.get(uid)
        if not p or p.get("await") != "range_custom":
            answer("انتخاب ماه منقضی شده؛ دوباره «📊 درخواست گزارش از سابقه» را بزنید.", alert=True)
            return
        parts = data[len(kb.CB_MG_PICK_PREFIX):].split("|")
        op = parts[0]
        if op == "l":
            answer("🔒 ماه آینده در گزارش سابقه قابل انتخاب نیست (فقط پیش‌بینی/نیاز ماه آینده را می‌پذیرد).", alert=True)
            return
        pick = [tuple(x) for x in (p.get("pick") or [])]
        if op == "t":
            y, m = int(parts[1]), int(parts[2])
            if ps.is_future_month(y, m):
                answer("🔒 ماه آینده قابل انتخاب نیست.", alert=True)
                return
            if (y, m) in pick:
                pick.remove((y, m))
            else:
                pick.append((y, m))
            p["pick"] = sorted(pick)
            answer(f"{len(pick)} ماه انتخاب شد")
        elif op == "y":
            y = int(parts[1])
            if y > ps.today().year:
                answer("🔒 سال آینده قابل انتخاب نیست.", alert=True)
                return
            p["pick_year"] = y
            answer()
        elif op == "c":
            p["pick"] = []
            answer("پاک شد")
        elif op == "go":
            if not pick:
                answer("حداقل یک ماه انتخاب کنید.", alert=True)
                return
            answer()
            self.pending[uid] = p
            self._prepare_range(message, p, mgp.range_months(pick))
            return
        self.pending[uid] = p
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id is not None and message.get("message_id") is not None:
            try:
                self.app.client.edit_message_reply_markup(chat_id, int(message["message_id"]), self._picker_markup(p))
            except Exception:  # noqa: BLE001
                pass

    def _on_range_pick(self, message: dict, p: dict, text: str) -> bool:
        if text == kb.BTN_MG_RANGE_CUSTOM:
            p["await"] = "range_custom"
            p["pick"] = []
            self._reply(
                message,
                "بازه را بنویسید:\n"
                "• تعداد روز — مثلاً «۴۵» یا «۴۵ روز» (منتهی به آخرین تاریخ داده)\n"
                "• از تاریخ تا تاریخ — مثلاً «1405/05/01 تا 1405/07/29»\n"
                "• یا بازهٔ ماه — «از تیر ۱۴۰۵ تا شهریور ۱۴۰۵»\n"
                "یا از فهرست زیر ماه‌ها را انتخاب کنید.",
                kb.main_goal_cancel_menu(),
            )
            self._send_picker(message, p)
            return True
        n = _RANGE_BTNS.get(text)
        spec = mgp.range_last_n(n) if n else mgp.parse_range_choice(text)
        if not spec:
            self._reply(message, "یکی از گزینه‌های بازه را انتخاب کنید.", kb.main_goal_range_menu())
            return True
        return self._prepare_range(message, p, spec)

    def _on_range_custom(self, message: dict, p: dict, text: str) -> bool:
        spec = mgp.parse_range_choice(text)
        if not spec:
            self._reply(
                message,
                "بازه معتبر نیست. مثال: «۹۰ روز»، «1405/05/01 تا 1405/07/29» یا «از تیر ۱۴۰۵ تا شهریور ۱۴۰۵»",
                kb.main_goal_cancel_menu(),
            )
            return True
        return self._prepare_range(message, p, spec)

    def _prepare_range(self, message: dict, p: dict, spec) -> bool:
        uid = self.app._uid(message)
        user = self._user(message)
        if not user:
            self.clear(uid)
            return True
        err = mgp.future_error_fa(spec)
        if err:
            self._reply(message, err + "\nبازهٔ دیگری بنویسید یا انتخاب کنید.", kb.main_goal_cancel_menu())
            return True
        months = mgh.load_history(self.db)
        selected, warns = mgp.filter_months_by_range(months, spec, db=self.db)
        if not selected:
            self.clear(uid)
            self._reply(
                message,
                "در این بازه هیچ ماهی در سابقهٔ ذخیره‌شده نیست. ابتدا ورودی‌ها را ثبت کنید.",
                kb.main_goal_menu(),
            )
            return True
        if warns:
            p["await"] = "range_confirm"
            p["range_spec"] = mgp.spec_to_dict(spec)
            self.pending[uid] = p
            self._reply(
                message,
                "⚠ دادهٔ بازه ناقص است یا هشدار دارد:\n"
                + "\n".join(f"• {w}" for w in warns)
                + f"\n\nمبنا: {'، '.join(r.label for r in selected)}. با دادهٔ موجود ادامه دهیم؟",
                kb.main_goal_partial_confirm_menu(),
            )
            return True
        return self._run_range(message, user, spec)

    def _on_range_confirm(self, message: dict, p: dict, text: str) -> bool:
        if text != kb.BTN_MG_CONFIRM_PARTIAL:
            self._reply(message, "برای ادامه «✅ ادامه با دادهٔ موجود» را بزنید یا انصراف دهید.", kb.main_goal_partial_confirm_menu())
            return True
        uid = self.app._uid(message)
        user = self._user(message)
        spec = mgp.spec_from_dict(p.get("range_spec") or {})
        self.clear(uid)
        if not user:
            return True
        return self._run_range(message, user, spec)

    def _run_range(self, message: dict, user: dict, spec) -> bool:
        model, result, _warns = mgp.build_range_report(
            self.db, spec, inventory=self._inventory(user), require_complete=False
        )
        return self._deliver(message, user, model, result, stem_prefix="main_goal_range")
