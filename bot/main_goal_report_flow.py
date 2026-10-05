"""Bale UX for «گزارش هدف اصلی» (4 Excel uploads → period check → PDF+xlsx).

Menu: گزارش‌ها / تحلیل تاندیش → 🎯 گزارش هدف اصلی
Roles: owner / manager / responsible_officer

Shared compute lives in ``services.main_goal_report`` (also used by web).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from analytics.frames import load_primary_inventory
from bot import keyboards as kb
from bot.activity import log_activity
from bot.jalali import format_date, tehran_now
from config import REPORT_DIR, UPLOAD_DIR, ensure_dirs
from excel.simple_report import generate_simple_report_xlsx
from pdf.generator import generate_simple_report_pdf
from services import main_goal_report as mg

if TYPE_CHECKING:  # pragma: no cover
    from bot.handlers import BotApp

logger = logging.getLogger(__name__)

_OWN_NAV = {
    kb.BTN_MG_MENU,
    kb.BTN_MG_START,
    kb.BTN_MG_CANCEL,
    kb.BTN_MG_RECENT,
    kb.BTN_MG_SKIP_TARGET,
    kb.BTN_BACK_ANALYTICS,
}


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

    # ------------------------------------------------------------ dispatcher
    def handle_text(self, message: dict, text: str) -> bool:
        uid = self.app._uid(message)
        text = (text or "").strip()

        if text == kb.BTN_MG_MENU or text == kb.BTN_MG_BACK:
            self.open_menu(message)
            return True
        if text == kb.BTN_MG_START:
            self.start_upload(message)
            return True
        if text == kb.BTN_MG_RECENT:
            self.show_recent(message)
            return True

        p = self.pending.get(uid)
        if not p:
            if text in _OWN_NAV - {kb.BTN_BACK_ANALYTICS}:
                user = self._user(message)
                if user:
                    self._reply(message, "گزارش هدف اصلی فعالی در جریان نیست.", kb.main_goal_menu())
                return True
            return False

        if text == kb.BTN_MG_CANCEL:
            self.clear(uid)
            user = self.app._user_or_deny(message)
            if user:
                self._reply(message, "گزارش هدف اصلی لغو شد؛ فایل‌ها ذخیرهٔ نهایی نشدند.", kb.main_goal_menu())
            return True

        if text in self._reserved and text not in _OWN_NAV and text != kb.BTN_MG_SKIP_TARGET:
            # abandon draft for global nav
            self.clear(uid)
            return False

        if p.get("await") == "target_tons":
            return self._on_target_tons(message, p, text)

        if p.get("await") == "file":
            self._reply(
                message,
                f"در انتظار فایل Excel «{mg.FILE_KINDS[p['expect']]}» به‌صورت Document (.xlsx) هستید.\n"
                "یا «✖️ انصراف از گزارش هدف اصلی» را بزنید.",
                kb.main_goal_upload_menu(),
            )
            return True

        return True

    def handle_document(self, message: dict) -> bool:
        """Return True if this document belonged to the main-goal upload flow."""
        uid = self.app._uid(message)
        p = self.pending.get(uid)
        if not p or p.get("await") != "file":
            return False

        user = self._user(message)
        if not user:
            self.clear(uid)
            return True

        doc = message.get("document") or {}
        file_name = (doc.get("file_name") or "").strip()
        file_id = doc.get("file_id")
        if not file_id:
            self._reply(message, "فایل نامعتبر است.", kb.main_goal_upload_menu())
            return True
        if not file_name.lower().endswith(".xlsx"):
            self._reply(
                message,
                f"فقط .xlsx پذیرفته می‌شود (دریافت شد: {file_name or 'بدون‌نام'}).",
                kb.main_goal_upload_menu(),
            )
            return True

        kind = p["expect"]
        dest_dir = UPLOAD_DIR / str(user["bale_user_id"]) / "main_goal" / str(p["batch"])
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{kind}.xlsx"
        try:
            self.app.client.download_file(file_id, dest)
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal download failed")
            self._reply(message, f"دانلود فایل از بله ناموفق بود: {exc}", kb.main_goal_upload_menu())
            return True

        # Detect period early for feedback
        period, src = mg.detect_period(dest, filename=file_name)
        p.setdefault("files", {})[kind] = str(dest)
        p.setdefault("filenames", {})[kind] = file_name
        p.setdefault("periods", {})[kind] = {
            "label": period.label_fa() if period else None,
            "key": period.key() if period else None,
            "source": src,
        }

        # Advance to next missing kind
        next_kind = None
        for k in mg.FILE_KIND_ORDER:
            if k not in p["files"]:
                next_kind = k
                break

        period_note = (
            f"بازه تشخیص‌داده‌شده: {period.label_fa()} ({src})"
            if period
            else f"⚠ بازه تشخیص نشد ({src})"
        )
        if next_kind:
            p["expect"] = next_kind
            p["await"] = "file"
            self.pending[uid] = p
            got = len(p["files"])
            self._reply(
                message,
                f"✅ «{mg.FILE_KINDS[kind]}» دریافت شد.\n{period_note}\n"
                f"({got}/۴)\n\n"
                f"حالا فایل «{mg.FILE_KINDS[next_kind]}» را بفرستید:",
                kb.main_goal_upload_menu(),
            )
            return True

        # All four received — ask optional target tons then compute
        p["await"] = "target_tons"
        self.pending[uid] = p
        self._reply(
            message,
            f"✅ هر چهار فایل دریافت شد.\n{period_note}\n\n"
            "اختیاری: تناژ هدف برای پیش‌بینی نیاز را به‌عدد بفرستید "
            "(مثلاً 50000).\n"
            "یا «⏭ بدون تناژ هدف» را بزنید تا فقط نرخ‌ها و نیاز همان بازه محاسبه شود.",
            kb.main_goal_target_menu(),
        )
        return True

    def _on_target_tons(self, message: dict, p: dict, text: str) -> bool:
        uid = self.app._uid(message)
        target = None
        if text != kb.BTN_MG_SKIP_TARGET:
            raw = mg.normalize_digits(text).replace(",", "").strip()
            try:
                target = float(raw)
                if target <= 0:
                    raise ValueError
            except ValueError:
                self._reply(
                    message,
                    "یک عدد مثبت برای تناژ هدف بفرستید یا «⏭ بدون تناژ هدف» را بزنید.",
                    kb.main_goal_target_menu(),
                )
                return True
        return self._compute_and_send(message, p, target_tons=target)

    # ------------------------------------------------------------ menu
    def open_menu(self, message: dict) -> None:
        user = self._user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self.clear(uid)
        recent = self.db.list_main_goal_reports(limit=1)
        extra = ""
        if recent:
            r = recent[0]
            extra = f"\nآخرین گزارش: {r.get('period_label') or '—'} — {r.get('jalali_date') or ''}"
        self._reply(
            message,
            f"🎯 {mg.TITLE_FA}\n"
            f"{mg.SUBTITLE_FA}\n\n"
            "چهار فایل Excel برای یک بازهٔ زمانی یکسان آپلود کنید:\n"
            "۱) آمار تولید\n"
            "۲) مصرف تاندیش بیلت\n"
            "۳) مصرف تاندیش بلوم\n"
            "۴) مصرف تاندیش اسلب\n\n"
            "اگر بازه‌ها یکسان نباشند محاسبه انجام نمی‌شود."
            f"{extra}",
            kb.main_goal_menu(),
        )

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
            "periods": {},
        }
        self._reply(
            message,
            f"فایل Excel «{mg.FILE_KINDS[mg.FILE_KIND_ORDER[0]]}» را به‌صورت Document بفرستید "
            f"(.xlsx).\nبازه از نام فایل / شیت / سربرگ خوانده می‌شود.",
            kb.main_goal_upload_menu(),
        )

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

    # ------------------------------------------------------------ compute + send
    def _compute_and_send(
        self, message: dict, p: dict, *, target_tons: float | None
    ) -> bool:
        user = self._user(message)
        if not user:
            self.clear(self.app._uid(message))
            return True
        uid = str(user["bale_user_id"])
        files = {k: Path(v) for k, v in (p.get("files") or {}).items()}
        filenames = dict(p.get("filenames") or {})

        inv = None
        try:
            inv = load_primary_inventory(self.db, user)
        except Exception as exc:  # noqa: BLE001
            logger.warning("main_goal inventory load failed: %s", exc)

        try:
            result = mg.compute_main_goal(
                files, filenames=filenames, inventory=inv, target_tons=target_tons
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal compute failed")
            self.clear(uid)
            self._reply(message, f"خطا در محاسبه گزارش هدف اصلی: {exc}", kb.main_goal_menu())
            return True

        if not result.ok:
            self.clear(uid)
            self._reply(message, result.error_fa or "خطا", kb.main_goal_menu())
            return True

        # Persist uploads + results
        ensure_dirs()
        file_meta = {
            k: {
                "path": str(files[k]),
                "filename": filenames.get(k),
                "period_source": result.period_sources.get(k),
            }
            for k in mg.FILE_KIND_ORDER
            if k in files
        }
        now = tehran_now()
        try:
            self.db.insert_main_goal_report(
                period_key=result.period.key() if result.period else None,
                period_label=result.period.label_fa() if result.period else None,
                period_json=json.dumps(
                    {
                        "period": result.period.__dict__ if result.period else None,
                        "sources": result.period_sources,
                    },
                    ensure_ascii=False,
                ),
                files_json=json.dumps(file_meta, ensure_ascii=False),
                results_json=mg.persist_payload(result, file_meta),
                summary_text=result.summary_text(),
                target_tons=result.target_tons,
                source="bot",
                bale_user_id=user["bale_user_id"],
                actor_display_name=user.get("display_name"),
                created_at=mg.utc_now_iso(),
                created_at_tehran=now.isoformat(),
                jalali_date=format_date(now),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal persist failed")
            self._reply(message, f"محاسبه شد ولی ذخیره در پایگاه‌داده ناموفق بود: {exc}")

        # Empty → text only (no empty PDF/xlsx)
        empty = (not result.production or result.production.total_tons <= 0) and not result.rate_rows
        if empty or not mg.report_has_data(result.sections):
            self.clear(uid)
            self._reply(
                message,
                result.summary_text()
                + "\n\nدادهٔ جدولی کافی برای PDF/اکسل نبود — فقط خلاصه متنی.",
                kb.main_goal_menu(),
            )
            log_activity(self.db, user, "report_main_goal")
            return True

        stamp = now.strftime("%Y%m%d_%H%M%S")
        stem = f"main_goal_{stamp}"
        out_pdf = REPORT_DIR / f"{stem}.pdf"
        title = f"{mg.TITLE_FA} — {result.period.label_fa() if result.period else ''}"
        subtitle = mg.SUBTITLE_FA
        if result.warnings:
            subtitle += " | هشدار: " + "؛ ".join(result.warnings[:3])

        try:
            letterhead = self.app._letterhead_path()
            pdf_path = generate_simple_report_pdf(
                title,
                subtitle=subtitle,
                sections=result.sections,
                empty_message="داده‌ای برای گزارش هدف اصلی نیست.",
                output_path=out_pdf,
                filename_stem=stem,
                letterhead_path=letterhead,
            )
            self.app.client.send_document(
                self.app._chat_id(message),
                pdf_path,
                caption=title,
            )
            xlsx_path = pdf_path.with_suffix(".xlsx")
            generate_simple_report_xlsx(
                title,
                subtitle=subtitle,
                sections=result.sections,
                empty_message="داده‌ای برای گزارش هدف اصلی نیست.",
                output_path=xlsx_path,
                filename_stem=stem,
            )
            self.app.client.send_document(
                self.app._chat_id(message),
                xlsx_path,
                caption=f"نسخه اکسل — {mg.TITLE_FA}",
            )
            self._reply(
                message,
                result.summary_text() + "\n\nگزارش PDF و اکسل ارسال شد.",
                kb.main_goal_menu(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("main_goal report export failed")
            self._reply(
                message,
                result.summary_text() + f"\n\nخطا در تولید PDF/اکسل: {exc}",
                kb.main_goal_menu(),
            )

        self.clear(uid)
        log_activity(self.db, user, "report_main_goal")
        return True
