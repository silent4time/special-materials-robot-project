"""Bale UX for «🎯 هدف اصلی» — سابقهٔ چندماهه + دو سناریو.

Menu: منوی اصلی → 🎯 هدف اصلی
Roles: owner / manager / responsible_officer (حذف ماه: owner / manager)

Flow:
  1) 📥 ثبت ورودی ماه — عکس/اکسل آمار تولید، اکسل مصرف تاندیش هر بخش، یا
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
from bot.activity import log_activity
from bot.jalali import tehran_now
from config import REPORT_DIR, UPLOAD_DIR, ensure_dirs
from services import main_goal_history as mgh
from services import main_goal_persist as mgp
from services import main_goal_production_ocr as mgocr
from services import main_goal_report as mg

if TYPE_CHECKING:  # pragma: no cover
    from bot.handlers import BotApp

logger = logging.getLogger(__name__)

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
    kb.BTN_MG_INPUT_CORRECT,
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
}
_RANGE_BTNS = {
    kb.BTN_MG_RANGE_3: 3,
    kb.BTN_MG_RANGE_6: 6,
    kb.BTN_MG_RANGE_12: 12,
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


class MainGoalReportFlow:
    def __init__(self, app: "BotApp") -> None:
        self.app = app
        self.pending: dict[str, dict[str, Any]] = {}
        self._reserved = _global_buttons()

    @property
    def db(self):
        return self.app.db

    def clear(self, uid: str) -> None:
        self.pending.pop(str(uid), None)

    def _reply(self, message: dict, text: str, markup: dict | None = None) -> None:
        # Bale messages max ~4096 chars
        if len(text) > 3900:
            text = text[:3900] + "\n…"
        self.app._reply(message, text, markup)

    def _user(self, message: dict) -> dict | None:
        user = self.app._user_or_deny(message)
        if not user:
            return None
        if self.app._deny_technician(message, user):
            return None
        if not mg.can_run(user):
            self._reply(
                message,
                "دسترسی «هدف اصلی» ندارید (مالک / مدیر / کاردان مسئول).",
                kb.main_menu(user),
            )
            return None
        return user

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
        if text == kb.BTN_MG_INPUT_PROD:
            self.start_prod_photo(message)
            return True
        if text == kb.BTN_MG_INPUT_PROD_XLSX:
            self.start_prod_xlsx(message)
            return True
        if text in _INPUT_SECTION:
            self.start_cons_xlsx(message, _INPUT_SECTION[text])
            return True
        if text == kb.BTN_MG_INPUT_CORRECT:
            self.start_prod_correct(message)
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
        if mode == "bulk":
            if text in (kb.BTN_MG_BULK_DONE, kb.BTN_MG_BULK_STOP):
                return self._bulk_done(message, p, stopped=text == kb.BTN_MG_BULK_STOP)
            self._reply(
                message,
                "فایل‌ها (Excel به‌صورت Document یا عکس آمار تولید) را بفرستید یا "
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
        if mode == "prod_photo":
            self._reply(
                message,
                "در انتظار عکس آمار تولید (screenshot) هستید — Photo بفرستید.",
                kb.main_goal_upload_menu(),
            )
            return True
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
        if mode == "prod_correct":
            return self._on_prod_correct(message, p, text)
        if mode == "range_pick":
            return self._on_range_pick(message, p, text)
        if mode == "range_custom":
            return self._on_range_custom(message, p, text)
        if mode == "range_confirm":
            return self._on_range_confirm(message, p, text)
        return True

    def cancel(self, message: dict) -> None:
        """«✖️ انصراف» while a هدف اصلی step is pending (routed by BotApp.on_cancel)."""
        uid = self.app._uid(message)
        p = self.pending.get(uid) or {}
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
        back_inputs = p.get("await") in {"prod_photo", "prod_xlsx", "cons_xlsx", "prod_correct"}
        self._reply(
            message,
            "لغو شد؛ چیزی ذخیره نشد.",
            kb.main_goal_inputs_menu() if back_inputs else kb.main_goal_menu(),
        )

    # ------------------------------------------------------------ menu / history
    def open_menu(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        self.clear(str(user["bale_user_id"]))
        months = mgh.load_history(self.db)
        hist = mgh.history_overview_text(months)
        self._reply(
            message,
            f"🎯 {mg.TITLE_FA}\n{mg.SUBTITLE_FA}\n\n"
            f"• «{kb.BTN_MG_INPUTS}»: عکس/اکسل آمار تولید، اکسل مصرف تاندیش هر بخش یا «{kb.BTN_MG_BULK}».\n"
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
        text = mgh.history_overview_text(months)
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
            mgh.history_overview_text(months) + "\n\nشمارهٔ ردیف ماهی که باید حذف شود را بفرستید:",
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
            f"🗑 ماه «{label}» از سابقه حذف شد.\n\n" + mgh.history_overview_text(months),
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
            "• عکس آمار تولید — فقط screenshot تب «ریخته‌گری» سامانهٔ otsteel (عکس تب «کوره» رد می‌شود)\n"
            "• یا اکسل آمار تولید (.xlsx)\n"
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
        if not p or p.get("await") not in {"bulk", "prod_xlsx", "cons_xlsx", "prod_photo"}:
            return False
        if p.get("await") in {"prod_xlsx", "cons_xlsx"}:
            return self._handle_input_document(message, p)
        if p.get("await") == "prod_photo":
            return self.handle_photo(message)
        user = self._user(message)
        if not user:
            self.clear(uid)
            return True
        doc = message.get("document") or {}
        file_name = (doc.get("file_name") or "").strip()
        file_id = doc.get("file_id")
        mime = str(doc.get("mime_type") or "")
        if file_id and (mime.startswith("image/") or file_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))):
            return self._on_bulk_photo(message, user, p)
        if not file_id or not file_name.lower().endswith(".xlsx"):
            self._reply(
                message,
                f"فقط .xlsx یا عکس آمار تولید پذیرفته می‌شود (دریافت شد: {file_name or 'بدون‌نام'}).",
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
            self._reply(message, f"دانلود فایل از بله ناموفق بود: {exc}", kb.main_goal_bulk_menu())
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
            self._reply(message, f"خطا در پردازش «{file_name}»: {exc}", kb.main_goal_bulk_menu())
            return True
        head = f"📄 «{file_name}»" + (f" → {kind_fa}" if kind_fa else "")
        return self._bulk_reply(message, p, head, out, act, user)

    def _on_bulk_photo(self, message: dict, user: dict, p: dict) -> bool:
        file_id = self.app._extract_image_file_id(message)
        if not file_id:
            self._reply(message, "تصویر معتبر دریافت نشد.", kb.main_goal_bulk_menu())
            return True
        dest_dir = UPLOAD_DIR / str(user["bale_user_id"]) / "main_goal" / str(p["batch"])
        dest_dir.mkdir(parents=True, exist_ok=True)
        p["seq"] = int(p.get("seq") or 0) + 1
        dest = dest_dir / f"production_{p['seq']:03d}.png"
        try:
            self.app.client.download_file(file_id, dest)
            ocr_res = mgocr.ocr_production_image(dest)
            out = mgp.store_production_from_ocr(
                self.db, dest, user=user, source="bot", filename=dest.name, ocr_result=ocr_res
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal bulk photo failed")
            self._reply(message, f"خطا در OCR عکس: {exc}", kb.main_goal_bulk_menu())
            return True
        if out.tab_rejected:
            self._reply(message, "📸 " + (out.error_fa or mgocr.ALARM_UNKNOWN_FA), kb.main_goal_bulk_menu())
            log_activity(self.db, user, "main_goal_production_photo_rejected_tab")
            return True
        if not out.ok:
            out.error_fa = (out.error_fa or "OCR ناقص بود.") + (
                f" برای اصلاح، بعد از پایان «{kb.BTN_MG_INPUT_CORRECT}» را بزنید."
            )
        return self._bulk_reply(message, p, "📸 عکس آمار تولید", out, "main_goal_store_production_ocr", user)

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
        if not result.ok:
            self._reply(message, result.error_fa or "خطا", kb.main_goal_menu())
            return True
        try:
            mgh.persist_scenario(self.db, result, model, user=user, source="bot")
        except Exception:  # noqa: BLE001
            logger.exception("main_goal scenario persist failed")
        ensure_dirs()
        stem = f"{stem_prefix}_{tehran_now().strftime('%Y%m%d_%H%M%S')}"
        try:
            pdf_path, xlsx_path = mgh.export_scenario(
                result, stem=stem, report_dir=REPORT_DIR, letterhead_path=self.app._letterhead_path()
            )
            chat = self.app._chat_id(message)
            self.app.client.send_document(chat, pdf_path, caption=result.title)
            self.app.client.send_document(chat, xlsx_path, caption=f"نسخه اکسل — {result.title}")
            self._reply(message, result.summary + "\n\nگزارش PDF و اکسل ارسال شد.", kb.main_goal_menu())
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal scenario export failed")
            self._reply(message, result.summary + f"\n\nخطا در تولید PDF/اکسل: {exc}", kb.main_goal_menu())
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
            "عکس تب «ریخته‌گری» آمار تولید → OCR | اکسل آمار تولید | اکسل مصرف تاندیش هر بخش",
            f"چند فایل/چند ماه با هم: «{kb.BTN_MG_BULK}» | راهنما: «{kb.BTN_FILE_GUIDE}»",
            "",
        ]
        if overview:
            lines.append("وضعیت ماه‌ها:")
            for o in overview[-8:]:
                status = "✅ کامل" if o["complete"] else ("⚠ ناقص: " + "، ".join(o["missing"]))
                if o.get("production_provisional"):
                    status += " (ردیف تب کوره موقت است و در محاسبه استفاده نمی‌شود)"
                elif o.get("production_note"):
                    status += f" | {o['production_note']}"
                lines.append(f"• {o['label']}: {status}")
        else:
            lines.append("هنوز ورودی‌ای در DB نیست.")
        self._reply(message, "\n".join(lines), kb.main_goal_inputs_menu())

    def start_prod_photo(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        batch = tehran_now().strftime("%Y%m%d_%H%M%S")
        self.pending[str(user["bale_user_id"])] = {"await": "prod_photo", "batch": batch}
        self.app._file_guide_kind[str(user["bale_user_id"])] = "mg_production_photo"
        self._reply(
            message,
            "📸 عکس تب «ریخته گری» صفحه آمار تولید (otsteel.ksc.ir/productionstatistics) را "
            "همراه با فیلتر تاریخ ماه بفرستید.\n"
            "⛔ عکس تب «کوره» پذیرفته نمی‌شود.\n"
            "نگاشت CCM: ۱و۲=اسلب، ۳=بلوم، ۴و۵=بیلت.\n"
            "پس از OCR می‌توانید اعداد را دستی اصلاح کنید.",
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

    def start_prod_correct(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        self.pending[str(user["bale_user_id"])] = {"await": "prod_correct"}
        self._reply(
            message,
            "✏️ اصلاح دستی تولید — یک خط به این شکل بفرستید:\n"
            "شهریور ۱۴۰۵ | اسلب 99300 | بلوم 18500 | بیلت 33600 | ذوب 920\n"
            "(بخش‌های خالی اختیاری‌اند)",
            kb.main_goal_cancel_menu(),
        )

    def handle_photo(self, message: dict) -> bool:
        """Handle production screenshot while awaiting prod_photo."""
        uid = self.app._uid(message)
        p = self.pending.get(uid)
        if not p or p.get("await") != "prod_photo":
            return False
        user = self._user(message)
        if not user:
            self.clear(uid)
            return True
        file_id = self.app._extract_image_file_id(message)
        if not file_id:
            self._reply(message, "تصویر معتبر دریافت نشد. Photo بفرستید.", kb.main_goal_upload_menu())
            return True
        dest_dir = UPLOAD_DIR / str(user["bale_user_id"]) / "main_goal" / str(p["batch"])
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "production.png"
        try:
            self.app.client.download_file(file_id, dest)
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal photo download failed")
            self._reply(message, f"دانلود عکس ناموفق: {exc}", kb.main_goal_upload_menu())
            return True
        self._reply(message, "⏳ در حال OCR عکس تولید…", kb.main_goal_upload_menu())
        try:
            ocr_res = mgocr.ocr_production_image(dest)
            out = mgp.store_production_from_ocr(
                self.db, dest, user=user, source="bot", filename="production.png", ocr_result=ocr_res
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal OCR store failed")
            self._reply(message, f"خطا در OCR/ذخیره: {exc}", kb.main_goal_menu())
            self.clear(uid)
            return True
        if out.tab_rejected:
            # Wrong tab (furnace) or undeterminable → alarm, nothing stored; wait for a new photo.
            ev = (out.ocr_result.tab_evidence if out.ocr_result else {}) or {}
            logger.info("main_goal photo rejected: tab=%s evidence=%s", ev.get("decision"), ev)
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            p["await"] = "prod_photo"
            p.pop("ocr_draft", None)
            self.pending[uid] = p
            self._reply(message, out.error_fa or mgocr.ALARM_UNKNOWN_FA, kb.main_goal_upload_menu())
            log_activity(self.db, user, "main_goal_production_photo_rejected_tab")
            return True
        if not out.ok:
            # keep pending for correction
            p["await"] = "prod_correct"
            p["ocr_draft"] = {
                "path": str(dest),
                "raw": (out.ocr_result.ocr_raw_text if out.ocr_result else ""),
                "confidence": (out.ocr_result.ocr_confidence if out.ocr_result else None),
                "year": out.ocr_result.year if out.ocr_result else None,
                "month": out.ocr_result.month if out.ocr_result else None,
                "slab": out.ocr_result.slab_tons if out.ocr_result else 0,
                "bloom": out.ocr_result.bloom_tons if out.ocr_result else 0,
                "billet": out.ocr_result.billet_tons if out.ocr_result else 0,
                "melt": out.ocr_result.melt_count if out.ocr_result else None,
                "ccm": dict(out.ocr_result.ccm_tons) if out.ocr_result else {},
            }
            self.pending[uid] = p
            self._reply(
                message,
                (out.error_fa or "OCR ناقص بود.") + "\n\n" + (out.summary or "")
                + "\n\n✏️ اصلاح دستی را به شکل "
                "«شهریور ۱۴۰۵ | اسلب 99300 | بلوم 18500 | بیلت 33600 | ذوب 920» بفرستید.",
                kb.main_goal_cancel_menu(),
            )
            return True
        self.clear(uid)
        log_activity(self.db, user, "main_goal_store_production_ocr")
        self._reply(message, out.summary + "\n" + self._remaining_line(out), kb.main_goal_inputs_menu())
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
        kind = "production" if p["await"] == "prod_xlsx" else f"{p.get('section')}_consumption"
        dest = dest_dir / f"{kind}.xlsx"
        try:
            self.app.client.download_file(file_id, dest)
        except Exception as exc:  # noqa: BLE001
            self._reply(message, f"دانلود ناموفق: {exc}", kb.main_goal_upload_menu())
            return True
        try:
            if p["await"] == "prod_xlsx":
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
            self._reply(message, f"خطا: {exc}", kb.main_goal_menu())
            self.clear(uid)
            return True
        self.clear(uid)
        if not out.ok:
            self._reply(message, out.error_fa or "خطا", kb.main_goal_inputs_menu())
            return True
        log_activity(self.db, user, act)
        self._reply(message, out.summary + "\n" + self._remaining_line(out), kb.main_goal_inputs_menu())
        return True

    def _on_prod_correct(self, message: dict, p: dict, text: str) -> bool:
        uid = self.app._uid(message)
        user = self._user(message)
        if not user:
            self.clear(uid)
            return True
        parsed = _parse_manual_production(text)
        if not parsed:
            self._reply(
                message,
                "فرمت نامعتبر. مثال:\nشهریور ۱۴۰۵ | اسلب 99300 | بلوم 18500 | بیلت 33600 | ذوب 920",
                kb.main_goal_cancel_menu(),
            )
            return True
        draft = p.get("ocr_draft") or {}
        base = mgocr.ProductionOCRResult(
            ok=False,
            year=draft.get("year"),
            month=draft.get("month"),
            period_key=None,
            slab_tons=float(draft.get("slab") or 0),
            bloom_tons=float(draft.get("bloom") or 0),
            billet_tons=float(draft.get("billet") or 0),
            melt_count=draft.get("melt"),
            ccm_tons={int(k): float(v) for k, v in (draft.get("ccm") or {}).items()},
            ocr_raw_text=str(draft.get("raw") or ""),
            ocr_confidence=draft.get("confidence"),
        )
        if base.year and base.month:
            base.period_key = f"m:{int(base.year):04d}-{int(base.month):02d}"
        fixed = mgocr.apply_manual_corrections(base, **parsed)
        if not fixed.ok:
            self._reply(message, fixed.error_fa or "اصلاح ناقص است.", kb.main_goal_cancel_menu())
            return True
        # store without requiring the image path
        path = Path(draft["path"]) if draft.get("path") else None
        if path and path.is_file():
            out = mgp.store_production_from_ocr(
                self.db,
                path,
                user=user,
                source="bot",
                filename=path.name,
                ocr_result=fixed,
                manual_corrected=True,
            )
        else:
            # create a placeholder path under uploads
            dest_dir = UPLOAD_DIR / str(user["bale_user_id"]) / "main_goal" / "manual"
            dest_dir.mkdir(parents=True, exist_ok=True)
            placeholder = dest_dir / f"{fixed.period_key}.txt"
            placeholder.write_text(fixed.ocr_raw_text or text, encoding="utf-8")
            out = mgp.store_production_from_ocr(
                self.db,
                placeholder,
                user=user,
                source="bot",
                filename=placeholder.name,
                ocr_result=fixed,
                manual_corrected=True,
            )
        self.clear(uid)
        if not out.ok:
            self._reply(message, out.error_fa or "ذخیره ناموفق", kb.main_goal_inputs_menu())
            return True
        log_activity(self.db, user, "main_goal_store_production_manual")
        self._reply(message, out.summary + "\n" + self._remaining_line(out), kb.main_goal_inputs_menu())
        return True

    # ------------------------------------------------------------ ب) درخواست گزارش بازه
    def start_range_report(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        months = self._history_gate(message)
        if months is None:
            return
        self.pending[str(user["bale_user_id"])] = {"await": "range_pick"}
        self._reply(
            message,
            "📊 درخواست گزارش از سابقهٔ DB\n"
            f"{mgh.history_count_line(len(months))}\n\n"
            "بازه را انتخاب کنید:\n"
            "• ۳ ماهه / ۶ ماهه / یکساله — آخرین N ماه ذخیره‌شده\n"
            "• بازه ورودی کاربر — مثلاً «از تیر ۱۴۰۵ تا شهریور ۱۴۰۵»",
            kb.main_goal_range_menu(),
        )

    def _on_range_pick(self, message: dict, p: dict, text: str) -> bool:
        if text == kb.BTN_MG_RANGE_CUSTOM:
            p["await"] = "range_custom"
            self._reply(
                message,
                "بازه جلالی را بنویسید (مثلاً «از تیر ۱۴۰۵ تا شهریور ۱۴۰۵» یا «شهریور ۱۴۰۵»):",
                kb.main_goal_cancel_menu(),
            )
            return True
        n = _RANGE_BTNS.get(text)
        spec = mgp.range_last_n(n) if n else mgp.parse_range_choice(text)
        if not spec:
            self._reply(message, "یکی از گزینه‌های بازه را انتخاب کنید.", kb.main_goal_range_menu())
            return True
        return self._prepare_range(message, p, spec)

    def _on_range_custom(self, message: dict, p: dict, text: str) -> bool:
        spec = mgp.parse_range_choice(text)
        if not spec or spec.kind != "custom":
            # also accept last_n phrasing
            if not spec:
                self._reply(
                    message,
                    "بازه معتبر نیست. مثال: از تیر ۱۴۰۵ تا شهریور ۱۴۰۵",
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
        months = mgh.load_history(self.db)
        selected, warns = mgp.filter_months_by_range(months, spec)
        if not selected:
            self.clear(uid)
            self._reply(
                message,
                "در این بازه هیچ ماهی در DB نیست. ابتدا ورودی‌ها را ثبت کنید.",
                kb.main_goal_menu(),
            )
            return True
        if warns:
            p["await"] = "range_confirm"
            p["range_spec"] = {
                "kind": spec.kind,
                "n_months": spec.n_months,
                "start": spec.start,
                "end": spec.end,
                "label_fa": spec.label_fa,
            }
            self.pending[uid] = p
            self._reply(
                message,
                "⚠ دادهٔ بازه ناقص است:\n"
                + "\n".join(f"• {w}" for w in warns)
                + f"\n\n{len(selected)} ماه موجود است. با دادهٔ موجود ادامه دهیم؟",
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
        raw = p.get("range_spec") or {}
        if raw.get("kind") == "custom" and raw.get("start") and raw.get("end"):
            spec = mgp.range_custom(tuple(raw["start"]), tuple(raw["end"]))
        else:
            spec = mgp.range_last_n(int(raw.get("n_months") or 3))
        self.clear(uid)
        if not user:
            return True
        return self._run_range(message, user, spec)

    def _run_range(self, message: dict, user: dict, spec) -> bool:
        model, result, _warns = mgp.build_range_report(
            self.db, spec, inventory=self._inventory(user), require_complete=False
        )
        return self._deliver(message, user, model, result, stem_prefix="main_goal_range")


def _parse_manual_production(text: str) -> dict | None:
    """Parse 'شهریور ۱۴۰۵ | اسلب 99300 | بلوم 18500 | بیلت 33600 | ذوب 920'."""
    from bot.jalali import parse_month_year_token

    s = mg.normalize_text(text)
    if not s:
        return None
    out: dict = {}
    tok = parse_month_year_token(s)
    if tok:
        out["year"], out["month"] = tok
    for label, key in (("اسلب", "slab_tons"), ("بلوم", "bloom_tons"), ("بیلت", "billet_tons"), ("ذوب", "melt_count")):
        m = __import__("re").search(rf"{label}\s*[:=]?\s*([\d٬,]+(?:\.\d+)?)", s)
        if m:
            v = mgh.parse_number(m.group(1))
            if v is not None:
                out[key] = v
    if "year" not in out or "month" not in out:
        return None
    if not any(k in out for k in ("slab_tons", "bloom_tons", "billet_tons")):
        return None
    return out
