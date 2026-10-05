"""Bale UX for «گزارش هدف اصلی» — سابقهٔ چندماهه + دو سناریو.

Menu: گزارش‌ها / تحلیل تاندیش → 🎯 گزارش هدف اصلی
Roles: owner / manager / responsible_officer (حذف ماه: owner / manager)

Flow:
  1) 📤 آپلود ۴ فایل یک ماه  — ترتیبی؛ بازهٔ ۴ فایل باید یکسان باشد (وگرنه خطا)
     📦 آپلود گروهی چند ماه   — فایل‌ها به هر ترتیب؛ نوع + ماه خودکار تشخیص؛
                                  هر ماهی که ۴ فایلش کامل شد ذخیره می‌شود
  2) 🗂 ماه‌های ذخیره‌شده (حداقل ۳ ماه توصیه می‌شود)
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
from services import main_goal_report as mg

if TYPE_CHECKING:  # pragma: no cover
    from bot.handlers import BotApp

logger = logging.getLogger(__name__)

_MENU_BTNS = {
    kb.BTN_MG_MENU,
    kb.BTN_MG_BACK,
    kb.BTN_MG_START,
    kb.BTN_MG_START_LEGACY,
    kb.BTN_MG_BULK,
    kb.BTN_MG_HISTORY,
    kb.BTN_MG_DELETE,
    kb.BTN_MG_SCN_TARGET,
    kb.BTN_MG_SCN_FORECAST,
    kb.BTN_MG_RECENT,
}
# Buttons that only make sense inside a pending step
_STEP_BTNS = {
    kb.BTN_MG_CANCEL,
    kb.BTN_MG_SKIP_TARGET,
    kb.BTN_MG_BULK_DONE,
    kb.BTN_MG_ADD_SECTION,
    kb.BTN_MG_COMPUTE,
    kb.BTN_MG_P1,
    kb.BTN_MG_P3,
    kb.BTN_MG_P6,
    kb.BTN_MG_P12,
    *kb.MG_SECTION_BUTTONS.keys(),
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
                "دسترسی گزارش هدف اصلی ندارید (مالک / مدیر / کاردان مسئول).",
                kb.analytics_menu(),
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

        if text in (kb.BTN_MG_MENU, kb.BTN_MG_BACK):
            self.open_menu(message)
            return True
        if text in (kb.BTN_MG_START, kb.BTN_MG_START_LEGACY):
            self.start_upload(message)
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

        p = self.pending.get(uid)
        if not p:
            if text in (_STEP_BTNS - set(kb.MG_SECTION_BUTTONS) - set(_PERIOD_BTN_MONTHS)):
                user = self._user(message)
                if user:
                    self._reply(message, "گزارش هدف اصلی فعالی در جریان نیست.", kb.main_goal_menu())
                return True
            return False

        if text == kb.BTN_MG_CANCEL:
            self.clear(uid)
            user = self.app._user_or_deny(message)
            if user:
                extra = ""
                if p.get("await") == "bulk":
                    extra = "\n" + self._bulk_status_text(p, final=True)
                self._reply(message, "گزارش هدف اصلی لغو شد." + extra, kb.main_goal_menu())
            return True

        if text in self._reserved and text not in _STEP_BTNS:
            # global navigation abandons the draft
            self.clear(uid)
            return False

        mode = p.get("await")
        if mode == "file":
            self._reply(
                message,
                f"در انتظار فایل Excel «{mg.FILE_KINDS[p['expect']]}» به‌صورت Document (.xlsx) هستید.\n"
                "یا «✖️ انصراف از گزارش هدف اصلی» را بزنید.",
                kb.main_goal_upload_menu(),
            )
            return True
        if mode == "bulk":
            if text == kb.BTN_MG_BULK_DONE:
                return self._bulk_done(message, p)
            self._reply(
                message,
                "فایل‌های Excel را به‌صورت Document بفرستید یا «✅ پایان آپلود گروهی» را بزنید.",
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
        return True

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
            "مرحله ۱ — سابقه: برای دقت، فایل‌های ۴گانهٔ حداقل ۳ ماه اخیر را آپلود کنید "
            "(هر ماه: آمار تولید، مصرف تاندیش بیلت، بلوم، اسلب — هر ۴ فایل یک ماه باید بازهٔ یکسان داشته باشند).\n"
            "مرحله ۲ — سناریو:\n"
            "  🎯 سناریو ۱: تناژ هدف در یک بازه و بخش → تاندیش و مواد لازم\n"
            "  🔮 سناریو ۲: پیش‌بینی N ماه آینده با روند فعلی → تناژ و مواد هر بخش\n\n"
            f"{hist}",
            kb.main_goal_menu(),
        )

    def show_history(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        self.clear(str(user["bale_user_id"]))
        months = mgh.load_history(self.db)
        self._reply(
            message,
            mgh.history_overview_text(months),
            kb.main_goal_history_menu(can_delete=mgh.can_delete_month(user) and bool(months)),
        )

    def start_delete(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        if not mgh.can_delete_month(user):
            self._reply(message, "حذف ماه از سابقه فقط برای مالک یا مدیر مجاز است.", kb.main_goal_menu())
            return
        months = mgh.load_history(self.db)
        if not months:
            self._reply(message, "ماهی برای حذف وجود ندارد.", kb.main_goal_menu())
            return
        self.pending[str(user["bale_user_id"])] = {
            "await": "delete_pick",
            "ids": [m.id for m in months],
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
            mid = p["ids"][idx - 1]
            if idx < 1:
                raise IndexError
        except (ValueError, IndexError):
            self._reply(message, "شمارهٔ ردیف معتبر بفرستید.", kb.main_goal_cancel_menu())
            return True
        row = self.db.get_main_goal_month(mid)
        self.db.delete_main_goal_month(mid)
        self.clear(uid)
        log_activity(self.db, user, "main_goal_delete_month")
        months = mgh.load_history(self.db)
        self._reply(
            message,
            f"🗑 ماه «{(row or {}).get('period_label') or mid}» از سابقه حذف شد.\n\n"
            + mgh.history_overview_text(months),
            kb.main_goal_menu(),
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

    # ------------------------------------------------------------ sequential upload (one month)
    def start_upload(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        batch = tehran_now().strftime("%Y%m%d_%H%M%S")
        self.pending[uid] = {
            "await": "file",
            "expect": mg.FILE_KIND_ORDER[0],
            "batch": batch,
            "files": {},
            "filenames": {},
        }
        n = len(self.db.list_main_goal_months())
        self._reply(
            message,
            "آپلود ۴ فایل یک ماه (هر ۴ فایل باید بازهٔ یکسان داشته باشند).\n"
            f"{mgh.history_count_line(n)}\n\n"
            f"فایل Excel «{mg.FILE_KINDS[mg.FILE_KIND_ORDER[0]]}» را به‌صورت Document بفرستید (.xlsx).\n"
            "بازه از نام فایل / شیت / سربرگ خوانده می‌شود (مثل «شهریور ۱۴۰۵»).",
            kb.main_goal_upload_menu(),
        )

    def start_bulk(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self.pending[uid] = {
            "await": "bulk",
            "batch": "bulk_" + tehran_now().strftime("%Y%m%d_%H%M%S"),
            "seq": 0,
            "buckets": {},
            "stored": [],
        }
        self._reply(
            message,
            "📦 آپلود گروهی چند ماه\n"
            "همهٔ فایل‌های چند ماه (مثلاً ۳ ماه × ۴ فایل = ۱۲ فایل) را به هر ترتیبی بفرستید.\n"
            "نوع فایل (آمار تولید / مصرف تاندیش بیلت / بلوم / اسلب) و ماه از نام فایل، نام شیت یا سربرگ "
            "تشخیص داده می‌شود؛ هر ماهی که ۴ فایلش کامل شود بلافاصله اعتبارسنجی و ذخیره می‌شود.\n"
            "پیشنهاد نام فایل: «آمار تولید شهریور ۱۴۰۵.xlsx»، «مصرف تاندیش بیلت شهریور ۱۴۰۵.xlsx».\n"
            "در پایان «✅ پایان آپلود گروهی» را بزنید.",
            kb.main_goal_bulk_menu(),
        )

    def handle_document(self, message: dict) -> bool:
        """Return True if this document belonged to the main-goal upload flow."""
        uid = self.app._uid(message)
        p = self.pending.get(uid)
        if not p or p.get("await") not in {"file", "bulk"}:
            return False

        user = self._user(message)
        if not user:
            self.clear(uid)
            return True

        doc = message.get("document") or {}
        file_name = (doc.get("file_name") or "").strip()
        file_id = doc.get("file_id")
        menu = kb.main_goal_bulk_menu() if p["await"] == "bulk" else kb.main_goal_upload_menu()
        if not file_id:
            self._reply(message, "فایل نامعتبر است.", menu)
            return True
        if not file_name.lower().endswith(".xlsx"):
            self._reply(
                message,
                f"فقط .xlsx پذیرفته می‌شود (دریافت شد: {file_name or 'بدون‌نام'}).",
                menu,
            )
            return True

        dest_dir = UPLOAD_DIR / str(user["bale_user_id"]) / "main_goal" / str(p["batch"])
        dest_dir.mkdir(parents=True, exist_ok=True)
        if p["await"] == "bulk":
            p["seq"] = int(p.get("seq") or 0) + 1
            dest = dest_dir / f"in_{p['seq']:03d}.xlsx"
        else:
            dest = dest_dir / f"{p['expect']}.xlsx"
        try:
            self.app.client.download_file(file_id, dest)
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal download failed")
            self._reply(message, f"دانلود فایل از بله ناموفق بود: {exc}", menu)
            return True

        if p["await"] == "bulk":
            return self._on_bulk_file(message, user, p, dest, file_name)
        return self._on_sequential_file(message, user, p, dest, file_name)

    def _on_sequential_file(self, message: dict, user: dict, p: dict, dest: Path, file_name: str) -> bool:
        uid = str(user["bale_user_id"])
        kind = p["expect"]
        period, src = mg.detect_period(dest, filename=file_name)
        p.setdefault("files", {})[kind] = str(dest)
        p.setdefault("filenames", {})[kind] = file_name
        next_kind = next((k for k in mg.FILE_KIND_ORDER if k not in p["files"]), None)
        period_note = (
            f"بازه تشخیص‌داده‌شده: {period.label_fa()} ({src})"
            if period
            else f"⚠ بازه تشخیص نشد ({src})"
        )
        if next_kind:
            p["expect"] = next_kind
            self.pending[uid] = p
            self._reply(
                message,
                f"✅ «{mg.FILE_KINDS[kind]}» دریافت شد.\n{period_note}\n"
                f"({len(p['files'])}/۴)\n\n"
                f"حالا فایل «{mg.FILE_KINDS[next_kind]}» را بفرستید:",
                kb.main_goal_upload_menu(),
            )
            return True

        # all four → validate same period + store as one month
        self.clear(uid)
        try:
            out = mgh.store_month_set(
                self.db,
                {k: Path(v) for k, v in p["files"].items()},
                filenames=p.get("filenames") or {},
                user=user,
                source="bot",
                inventory=self._inventory(user),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal store failed")
            self._reply(message, f"خطا در پردازش فایل‌ها: {exc}", kb.main_goal_menu())
            return True
        if not out.ok:
            self._reply(
                message,
                (out.error_fa or "خطا") + "\n\nاین ماه ذخیره نشد؛ فایل‌های اصلاح‌شده را دوباره آپلود کنید.",
                kb.main_goal_menu(),
            )
            return True
        log_activity(self.db, user, "main_goal_store_month")
        n = len(self.db.list_main_goal_months())
        self._reply(
            message,
            out.text_fa(history_count=n)
            + "\n\nبرای ماه بعدی «📤 آپلود ۴ فایل یک ماه» یا برای محاسبه یکی از سناریوها را بزنید.",
            kb.main_goal_menu(),
        )
        return True

    def _on_bulk_file(self, message: dict, user: dict, p: dict, dest: Path, file_name: str) -> bool:
        kind, ksrc = mg.detect_file_kind(dest, filename=file_name)
        period, psrc = mg.detect_period(dest, filename=file_name)
        if not kind:
            self._reply(
                message,
                f"⚠ نوع فایل «{file_name}» تشخیص نشد ({ksrc}).\n"
                "نام فایل را با «آمار تولید» یا «مصرف تاندیش بیلت/بلوم/اسلب» شروع کنید و دوباره بفرستید.",
                kb.main_goal_bulk_menu(),
            )
            return True
        if not period:
            self._reply(
                message,
                f"⚠ ماه/بازهٔ فایل «{file_name}» تشخیص نشد ({psrc}).\n"
                "ماه شمسی (مثل «شهریور ۱۴۰۵») را در نام فایل بنویسید و دوباره بفرستید.",
                kb.main_goal_bulk_menu(),
            )
            return True
        key = period.key()
        bucket = p["buckets"].setdefault(key, {"label": period.label_fa(), "files": {}, "filenames": {}})
        replaced = kind in bucket["files"]
        bucket["files"][kind] = str(dest)
        bucket["filenames"][kind] = file_name
        head = (
            f"✅ «{file_name}» → {mg.FILE_KINDS[kind]} | {period.label_fa()}"
            + (" (جایگزین فایل قبلی همین نوع)" if replaced else "")
        )
        if len(bucket["files"]) < len(mg.FILE_KIND_ORDER):
            self._reply(message, head + "\n" + self._bulk_status_text(p), kb.main_goal_bulk_menu())
            return True

        # bucket complete → validate + store
        try:
            out = mgh.store_month_set(
                self.db,
                {k: Path(v) for k, v in bucket["files"].items()},
                filenames=bucket["filenames"],
                user=user,
                source="bot",
                inventory=self._inventory(user),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal bulk store failed")
            self._reply(message, head + f"\nخطا در پردازش ماه {bucket['label']}: {exc}", kb.main_goal_bulk_menu())
            return True
        p["buckets"].pop(key, None)
        if not out.ok:
            self._reply(
                message,
                head + f"\n❌ ماه {bucket['label']} ذخیره نشد:\n{out.error_fa}\n\n" + self._bulk_status_text(p),
                kb.main_goal_bulk_menu(),
            )
            return True
        p["stored"].append(bucket["label"])
        log_activity(self.db, user, "main_goal_store_month")
        n = len(self.db.list_main_goal_months())
        self._reply(
            message,
            head + "\n\n" + out.text_fa(history_count=n) + "\n\n" + self._bulk_status_text(p),
            kb.main_goal_bulk_menu(),
        )
        return True

    def _bulk_status_text(self, p: dict, *, final: bool = False) -> str:
        lines = []
        if p.get("stored"):
            lines.append("ذخیره‌شده در این نوبت: " + "، ".join(p["stored"]))
        for _key, b in (p.get("buckets") or {}).items():
            missing = [mg.FILE_KINDS[k] for k in mg.FILE_KIND_ORDER if k not in b["files"]]
            if missing:
                lines.append(f"⏳ {b['label']}: {len(b['files'])}/۴ — مانده: " + "، ".join(missing))
        if final and any((p.get("buckets") or {}).values()):
            lines.append("ماه‌های ناقص ذخیره نشدند.")
        return "\n".join(lines) if lines else "(هنوز ماه کاملی نیست)"

    def _bulk_done(self, message: dict, p: dict) -> bool:
        uid = self.app._uid(message)
        user = self._user(message)
        self.clear(uid)
        if not user:
            return True
        n = len(self.db.list_main_goal_months())
        self._reply(
            message,
            "📦 آپلود گروهی پایان یافت.\n"
            + self._bulk_status_text(p, final=True)
            + "\n\n"
            + mgh.history_count_line(n),
            kb.main_goal_menu(),
        )
        return True

    # ------------------------------------------------------------ scenario 1: target tonnage
    def _history_gate(self, message: dict) -> list | None:
        months = mgh.load_history(self.db)
        if not months:
            self._reply(
                message,
                "هنوز هیچ ماهی در سابقه ذخیره نشده است.\n"
                f"ابتدا فایل‌های ۴گانهٔ حداقل {mgh.MIN_RECOMMENDED_MONTHS} ماه اخیر را آپلود کنید.",
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
