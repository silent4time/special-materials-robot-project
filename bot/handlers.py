"""Message/command handlers — request-driven three-file upload + RBAC + tundish analytics."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from analytics.tundish import (
    critical_materials,
    daily_rates,
    filter_by_tundish_type,
    forecast,
    format_suggest_list_fa,
    format_surplus_list_fa,
    missing_files_for_goal,
    parse_custom_range_message,
    period_consumption,
    range_day_count,
    remaining,
    resolve_preset_range,
    suggest_requests,
    surplus_materials,
)
from auth.rbac import (
    can_generate_report,
    ensure_registered,
    require_manager,
    require_owner,
    role_label,
)
from bot import keyboards as kb
from bot.bale_api import BaleClient
from config import CRITICAL_DAYS, FILE_TYPES, ROLES, SURPLUS_COVER_DAYS, SURPLUS_FORECAST_DAYS, UPLOAD_DIR, ensure_dirs
from db.models import Database
from excel.processor import ExcelValidationError, extract_and_save_clean, process_session_files
from pdf.generator import generate_report

logger = logging.getLogger(__name__)

HELP_TEXT = """راهنمای بازوی گزارش مواد / تاندیش

این بازو به‌صورت درخواست‌محور کار می‌کند:
۱) از منو نوع فایل را انتخاب کنید
۲) فایل Excel مربوطه (.xlsx) را پیوست کنید
۳) همین کار را برای هر سه نوع انجام دهید
۴) دکمه «تولید گزارش PDF» را بزنید
۵) از «گزارش‌ها / تحلیل تاندیش» برای مصرف روزانه، پیشنهاد درخواست، گزارش بازه‌ای، مواد بحرانی، مواد مازاد و پیش‌بینی استفاده کنید

انواع فایل:
• موجودی انبار — ۳ ستون: کد دسته بندی، کد و شرح کالا، موجودی
  (شناسه از «کد و شرح کالا» استخراج می‌شود؛ فقط دسته‌های مجاز در پایگاه‌داده نگه داشته می‌شوند؛ اولویت ۰ حذف می‌شود)
• مصرف ماهیانه مواد
• موجودی روزانه سایت (فیلدهای تاندیش برای تحلیل حفظ شده‌اند)

منوی موجودی انبار:
• ورود فایل اکسل
• اضافه کردن کد دسته بندی (دقیقاً ۴ رقم)

تحلیل (نیاز به فایل مرتبط):
• مصرف روزانه — موجودی روزانه سایت (ماهانه اختیاری)
• پیشنهاد درخواست = max(0, نیاز پیش‌بینی − موجودی)
• گزارش بازه‌ای — امروز / ۷ روز / ۳۰ روز یا «از YYYY-MM-DD تا YYYY-MM-DD»
• مواد بحرانی — پوشش کمتر از CRITICAL_DAYS={critical} روز
• مواد مازاد — پوشش > max(CRITICAL_DAYS×3، {surplus_cover}) روز یا موجودی بیش از نیاز {surplus_forecast} روز؛ بدون مصرف = مازاد/بدون مصرف
• پیش‌بینی = میانگین روزانه × تعداد روز بازه

نقش‌ها:
• مالک / مدیر — همه ردیف‌ها + مدیریت کاربران
• کاردان مسئول / تکنسین — فیلتر حوزه/تخصیص روی فایل‌های دارای domain؛ موجودی انبار بدون domain برای همه کاربران مجاز قابل مشاهده است

دستورات مدیر:
/users
/adduser <bale_id> <role> [scope] [name...]
/setrole <bale_id> <role>
/setscope <bale_id> <scope>
/reset — پاک کردن جلسه آپلود جاری
""".format(
    critical=int(CRITICAL_DAYS) if CRITICAL_DAYS == int(CRITICAL_DAYS) else CRITICAL_DAYS,
    surplus_cover=int(SURPLUS_COVER_DAYS) if SURPLUS_COVER_DAYS == int(SURPLUS_COVER_DAYS) else SURPLUS_COVER_DAYS,
    surplus_forecast=int(SURPLUS_FORECAST_DAYS) if SURPLUS_FORECAST_DAYS == int(SURPLUS_FORECAST_DAYS) else SURPLUS_FORECAST_DAYS,
)


class BotApp:
    def __init__(self, client: BaleClient, db: Database) -> None:
        self.client = client
        self.db = db
        # pending analytics interaction per user: {"mode": "period"|"forecast"|"suggest", "await": "range"|"days"}
        self._analysis_pending: dict[str, dict[str, Any]] = {}
        self._analysis_tundish_filter: dict[str, str | None] = {}
        # awaiting plain text for category code entry
        self._await_category_code: set[str] = set()
        ensure_dirs()

    # ---------- helpers ----------
    def _uid(self, message: dict) -> str:
        return str(message["from"]["id"])

    def _chat_id(self, message: dict) -> int | str:
        return message["chat"]["id"]

    def _display_name(self, message: dict) -> str:
        u = message.get("from") or {}
        parts = [u.get("first_name") or "", u.get("last_name") or ""]
        name = " ".join(p for p in parts if p).strip()
        return name or u.get("username") or str(u.get("id"))

    def _reply(self, message: dict, text: str, markup: dict | None = None) -> None:
        self.client.send_message(self._chat_id(message), text, reply_markup=markup)

    def _user_or_deny(self, message: dict) -> dict | None:
        user = ensure_registered(self.db, self._uid(message), self._display_name(message))
        if not user:
            self._reply(
                message,
                "شما در سیستم ثبت نشده‌اید. با مدیر تماس بگیرید تا با /adduser شما را اضافه کند.",
            )
            return None
        return user

    def _status_text(self, session: dict) -> str:
        done = self.db.session_completeness(session)
        lines = ["وضعیت فایل‌های جلسه جاری:"]
        marks = {True: "✅", False: "⏳"}
        for key, meta in FILE_TYPES.items():
            lines.append(f"{marks[done[key]]} {meta['label_fa']}")
        pending = session.get("pending_file_type")
        if pending and pending in FILE_TYPES:
            lines.append(f"\nدر انتظار آپلود: {FILE_TYPES[pending]['label_fa']}")
        else:
            lines.append("\nبرای آپلود، ابتدا نوع فایل را از منو انتخاب کنید.")
        if all(done.values()):
            lines.append("\nهمه فایل‌ها آماده‌اند — می‌توانید «تولید گزارش PDF» یا «گزارش‌ها / تحلیل تاندیش» را بزنید.")
        elif done.get("tank_consumption") or done.get("product_inventory"):
            lines.append("\nبا فایل‌های موجود می‌توانید بخشی از تحلیل تاندیش را اجرا کنید.")
        return "\n".join(lines)

    def _session_paths(self, session: dict) -> dict[str, str | None]:
        return {
            "tank_consumption": session.get("tank_path"),
            "product_inventory": session.get("inventory_path"),
            "monthly_consumption": session.get("monthly_path"),
        }

    def _load_frames(self, user: dict, session: dict) -> tuple[dict, dict]:
        paths = self._session_paths(session)
        # only process present paths
        present = {k: v for k, v in paths.items() if v}
        if not present:
            return {}, {}
        return process_session_files(present, user)

    def _clear_analysis_pending(self, uid: str) -> None:
        self._analysis_pending.pop(uid, None)

    def _apply_tundish_filter(self, frames: dict, uid: str) -> dict:
        selected = self._analysis_tundish_filter.get(uid)
        if not selected:
            return frames
        return {
            key: filter_by_tundish_type(frame, selected) if frame is not None else frame
            for key, frame in frames.items()
        }

    def _selected_tundish_label(self, uid: str) -> str:
        return self._analysis_tundish_filter.get(uid) or kb.BTN_ALL_TUNDISHES

    def _require_files(self, message: dict, user: dict, goal: str) -> tuple[dict, dict, dict] | None:
        session = self.db.get_or_create_session(user["bale_user_id"])
        completeness = self.db.session_completeness(session)
        missing = missing_files_for_goal(goal, completeness)
        if missing:
            self._reply(
                message,
                "برای این گزارش این فایل(ها) لازم است:\n• "
                + "\n• ".join(missing)
                + "\n\nابتدا از منوی اصلی نوع فایل را انتخاب و Excel را ارسال کنید.",
                kb.analytics_menu(),
            )
            return None
        frames, metas = self._load_frames(user, session)
        frames = self._apply_tundish_filter(frames, str(user["bale_user_id"]))
        return session, frames, metas

    # ---------- commands ----------
    def cmd_start(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        self.db.get_or_create_session(user["bale_user_id"])
        text = (
            "سلام! به بازوی «گزارش مواد / تاندیش» خوش آمدید.\n\n"
            f"نقش شما: {role_label(user['role'])}\n"
            f"حوزه: {user.get('scope') or '—'}\n\n"
            "از منو: موجودی انبار / مصرف ماهیانه / موجودی روزانه سایت را انتخاب کنید.\n"
            "برای موجودی انبار ابتدا کدهای دسته بندی ۴ رقمی را اضافه کنید، سپس Excel بفرستید.\n"
            "از «گزارش‌ها / تحلیل تاندیش» برای تحلیل‌ها و گزارش مواد مازاد استفاده کنید."
        )
        self._reply(message, text, kb.main_menu(require_manager(user)))

    def cmd_help(self, message: dict) -> None:
        user = ensure_registered(self.db, self._uid(message), self._display_name(message))
        self._reply(
            message,
            HELP_TEXT,
            kb.main_menu(require_manager(user)) if user else None,
        )

    def cmd_users(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user or not require_manager(user):
            self._reply(message, "فقط مدیر می‌تواند لیست کاربران را ببیند.")
            return
        rows = self.db.list_users()
        if not rows:
            self._reply(message, "هیچ کاربری ثبت نشده.", kb.main_menu(True))
            return
        lines = ["لیست کاربران:"]
        for r in rows:
            flag = "🟢" if r["active"] else "🔴"
            lines.append(
                f"{flag} {r['bale_user_id']} | {r.get('display_name')} | "
                f"{role_label(r['role'])} | حوزه={r.get('scope') or '—'}"
            )
        self._reply(message, "\n".join(lines), kb.main_menu(True))

    def cmd_adduser(self, message: dict, args: list[str]) -> None:
        user = self._user_or_deny(message)
        if not user or not require_manager(user):
            self._reply(message, "فقط مالک یا مدیر می‌تواند کاربر اضافه کند.")
            return
        if len(args) < 2:
            self._reply(
                message,
                "فرمت:\n/adduser <bale_id> <owner|manager|responsible_officer|technician> [scope] [name...]",
            )
            return
        target_id, role = args[0], args[1]
        if role not in ROLES:
            self._reply(message, "نقش نامعتبر. یکی از: " + "، ".join(ROLES))
            return
        if role == "owner" and not require_owner(user):
            self._reply(message, "فقط مالک می‌تواند نقش مالک بدهد.")
            return
        scope = None
        name_parts: list[str] = []
        if len(args) >= 3:
            if role == "responsible_officer":
                scope = args[2]
                name_parts = args[3:]
            else:
                if role == "technician" and len(args) >= 3:
                    maybe_scope = args[2]
                    if len(args) >= 4:
                        scope = maybe_scope
                        name_parts = args[3:]
                    else:
                        name_parts = [maybe_scope]
                else:
                    name_parts = args[2:]
        display = " ".join(name_parts).strip() or target_id
        created = self.db.upsert_user(target_id, role=role, display_name=display, scope=scope)
        self._reply(
            message,
            f"کاربر ذخیره شد:\n{created['bale_user_id']} | {created['display_name']} | "
            f"{role_label(created['role'])} | حوزه={created.get('scope') or '—'}",
            kb.main_menu(True),
        )

    def cmd_setrole(self, message: dict, args: list[str]) -> None:
        user = self._user_or_deny(message)
        if not user or not require_manager(user):
            self._reply(message, "فقط مالک یا مدیر.")
            return
        if len(args) < 2 or args[1] not in ROLES:
            self._reply(message, "فرمت: /setrole <bale_id> <owner|manager|responsible_officer|technician>")
            return
        if args[1] == "owner" and not require_owner(user):
            self._reply(message, "فقط مالک می‌تواند نقش مالک بدهد.")
            return
        try:
            updated = self.db.set_role(args[0], args[1])
        except KeyError:
            self._reply(message, "کاربر یافت نشد. اول /adduser استفاده کنید.")
            return
        self._reply(
            message,
            f"نقش به‌روز شد: {updated['bale_user_id']} → {role_label(updated['role'])}",
            kb.main_menu(True),
        )

    def cmd_setscope(self, message: dict, args: list[str]) -> None:
        user = self._user_or_deny(message)
        if not user or not require_manager(user):
            self._reply(message, "فقط مالک یا مدیر.")
            return
        if len(args) < 2:
            self._reply(message, "فرمت: /setscope <bale_id> <scope>")
            return
        try:
            updated = self.db.set_scope(args[0], args[1])
        except KeyError:
            self._reply(message, "کاربر یافت نشد.")
            return
        self._reply(
            message,
            f"حوزه به‌روز شد: {updated['bale_user_id']} → {updated.get('scope')}",
            kb.main_menu(True),
        )

    def cmd_reset(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        self._clear_analysis_pending(user["bale_user_id"])
        self._analysis_tundish_filter.pop(str(user["bale_user_id"]), None)
        self._await_category_code.discard(str(user["bale_user_id"]))
        self.db.reset_session(user["bale_user_id"])
        self._reply(message, "جلسه آپلود پاک شد. از منو دوباره شروع کنید.", kb.main_menu(require_manager(user)))

    # ---------- request-driven flow ----------
    def on_pick_file_type(self, message: dict, file_type: str) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        self._clear_analysis_pending(user["bale_user_id"])
        self._await_category_code.discard(str(user["bale_user_id"]))
        self.db.set_pending_file_type(user["bale_user_id"], file_type)
        label = FILE_TYPES[file_type]["label_fa"]
        self._reply(
            message,
            f"لطفاً فایل Excel مربوط به «{label}» را همین حالا به‌صورت Document ارسال کنید.\n"
            f"پسوند باید .xlsx باشد.\n"
            f"اگر منصرف شدید، «{kb.BTN_CANCEL_PENDING}» را بزنید.",
            kb.cancel_pending_menu(),
        )

    def on_cancel_pending(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        self.db.set_pending_file_type(user["bale_user_id"], None)
        self._await_category_code.discard(str(user["bale_user_id"]))
        session = self.db.get_or_create_session(user["bale_user_id"])
        self._reply(message, "عملیات لغو شد.\n" + self._status_text(session), kb.main_menu(require_manager(user)))

    def on_status(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        session = self.db.get_or_create_session(user["bale_user_id"])
        self._reply(message, self._status_text(session), kb.main_menu(require_manager(user)))

    def on_document(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        session = self.db.get_or_create_session(user["bale_user_id"])
        pending = session.get("pending_file_type")
        if not pending:
            self._reply(
                message,
                "ابتدا از منو نوع فایل را انتخاب کنید، سپس Excel را بفرستید.",
                kb.main_menu(require_manager(user)),
            )
            return

        doc = message.get("document") or {}
        file_name = (doc.get("file_name") or "").strip()
        file_id = doc.get("file_id")
        if not file_id:
            self._reply(message, "فایل نامعتبر است.")
            return
        if not file_name.lower().endswith(".xlsx"):
            self._reply(
                message,
                f"فقط .xlsx پذیرفته می‌شود. (دریافت شد: {file_name or 'بدون‌نام'})\n"
                f"هنوز در انتظار «{FILE_TYPES[pending]['label_fa']}» هستید.",
                kb.cancel_pending_menu(),
            )
            return

        dest_dir = UPLOAD_DIR / str(user["bale_user_id"]) / str(session["id"])
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{pending}.xlsx"
        try:
            self.client.download_file(file_id, dest)
        except Exception as exc:  # noqa: BLE001
            logger.exception("download failed")
            self._reply(message, f"دانلود فایل از بله ناموفق بود: {exc}")
            return

        allowlist = None
        if pending == "product_inventory":
            allowlist = self.db.active_category_code_set()
            if not allowlist:
                dest.unlink(missing_ok=True)
                self._reply(
                    message,
                    "لیست کدهای دسته‌بندی خالی است.\n"
                    "ابتدا از منوی «موجودی انبار» → «اضافه کردن کد دسته بندی» "
                    "حداقل یک کد ۴ رقمی ثبت کنید، سپس دوباره فایل را بفرستید.",
                    kb.inventory_menu(),
                )
                return

        try:
            result = extract_and_save_clean(
                dest, pending, category_allowlist=allowlist
            )
        except ExcelValidationError as exc:
            dest.unlink(missing_ok=True)
            menu = kb.inventory_menu() if pending == "product_inventory" else kb.cancel_pending_menu()
            self._reply(message, str(exc), menu)
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("extract failed")
            dest.unlink(missing_ok=True)
            self._reply(message, f"استخراج داده از فایل ناموفق بود: {exc}", kb.cancel_pending_menu())
            return

        # Session slots point at CLEAN file so analytics/PDF use filtered data
        session = self.db.store_file_slot(
            user["bale_user_id"], pending, str(result.clean_path)
        )
        self.db.save_extracted(
            bale_user_id=user["bale_user_id"],
            session_id=session["id"],
            file_type=pending,
            raw_path=str(result.raw_path),
            clean_path=str(result.clean_path),
            row_count=result.kept_row_count,
            columns=result.columns,
        )

        label = FILE_TYPES[pending]["label_fa"]
        reasons = result.drop_reasons or {}
        if pending == "product_inventory" and reasons:
            dropped_note = (
                f"\nحذف‌شده‌ها: دسته نامجاز={reasons.get('wrong_category', 0)}، "
                f"اولویت ۰={reasons.get('priority_0', 0)}، "
                f"شناسه نامعتبر={reasons.get('bad_id', 0)}، "
                f"دسته خالی={reasons.get('bad_category', 0)}، "
                f"موجودی نامعتبر={reasons.get('bad_quantity', 0)}"
            )
        elif result.dropped_row_count > 0:
            dropped_note = f"\n({result.dropped_row_count} ردیف اضافی/نامعتبر حذف شد)"
        else:
            dropped_note = ""
        extra_cols_note = ""
        if result.extra_columns_dropped:
            extra_cols_note = "\nستون‌های اضافی کنار گذاشته شد."
        reply_menu = (
            kb.inventory_menu()
            if pending == "product_inventory"
            else kb.main_menu(require_manager(user))
        )
        self._reply(
            message,
            (
                f"✅ فایل «{label}» دریافت شد.\n"
                f"از {result.raw_row_count} ردیف خام، {result.kept_row_count} ردیف نگه داشته شد."
                f"{dropped_note}{extra_cols_note}\n"
                f"نسخه تمیز ذخیره و در پایگاه‌داده ثبت شد.\n\n"
            )
            + self._status_text(session),
            reply_menu,
        )

    def on_generate(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        session = self.db.get_or_create_session(user["bale_user_id"])
        completeness = self.db.session_completeness(session)
        ok, err = can_generate_report(user, session, completeness)
        if not ok:
            self._reply(message, err + "\n\n" + self._status_text(session), kb.main_menu(require_manager(user)))
            return

        paths = self._session_paths(session)
        self._reply(message, "در حال پردازش و ساخت PDF…")
        try:
            frames, metas = process_session_files(paths, user)
            analytics = self._build_analytics_bundle(frames)
            pdf_path = generate_report(frames, metas, user, analytics=analytics)
            counts = {k: int(metas[k]["visible_rows"]) for k in metas}
            self.db.save_report(user["bale_user_id"], session["id"], str(pdf_path), counts)
            # keep paths available for further analytics: copy into fresh collecting session
            saved_paths = {
                "tank_consumption": session.get("tank_path"),
                "product_inventory": session.get("inventory_path"),
                "monthly_consumption": session.get("monthly_path"),
            }
            self.db.mark_session_done(session["id"])
            new_session = self.db.get_or_create_session(user["bale_user_id"])
            for ftype, path in saved_paths.items():
                if path:
                    self.db.store_file_slot(user["bale_user_id"], ftype, path)
            self.client.send_document(
                self._chat_id(message),
                pdf_path,
                caption="گزارش تاندیش / خلاصه داده‌های آپلود‌شده",
            )
            self._reply(
                message,
                "گزارش ارسال شد. فایل‌های جلسه برای تحلیل بعدی نگه داشته شدند.\n"
                "از «گزارش‌ها / تحلیل تاندیش» استفاده کنید یا با /reset جلسه را پاک کنید.",
                kb.main_menu(require_manager(user)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("generate failed")
            self._reply(message, f"خطا در تولید گزارش: {exc}", kb.main_menu(require_manager(user)))

    # ---------- موجودی انبار submenu ----------
    def on_inventory_menu(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        self._clear_analysis_pending(user["bale_user_id"])
        self._await_category_code.discard(str(user["bale_user_id"]))
        codes = self.db.list_category_codes(active_only=True)
        hint = (
            f"تعداد کدهای فعال دسته‌بندی: {len(codes)}\n"
            "اگر لیست خالی است، قبل از آپلود Excel حداقل یک کد ۴ رقمی اضافه کنید."
        )
        self._reply(
            message,
            "منوی موجودی انبار\n" + hint,
            kb.inventory_menu(),
        )

    def on_add_category_prompt(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        self._await_category_code.add(str(user["bale_user_id"]))
        self._clear_analysis_pending(user["bale_user_id"])
        self.db.set_pending_file_type(user["bale_user_id"], None)
        self._reply(
            message,
            "کد دسته بندی ۴ رقمی را ارسال کنید (مثال: 1201).\n"
            f"برای انصراف «{kb.BTN_CANCEL_PENDING}» یا بازگشت به منو را بزنید.",
            kb.cancel_pending_menu(),
        )

    def on_category_code_text(self, message: dict, text: str) -> bool:
        """Handle pending category-code entry. Returns True if consumed."""
        uid = str(self._uid(message))
        if uid not in self._await_category_code:
            return False
        user = self._user_or_deny(message)
        if not user:
            self._await_category_code.discard(uid)
            return True
        try:
            row = self.db.add_category_code(
                text.strip(), created_by=user["bale_user_id"]
            )
        except ValueError as exc:
            self._reply(message, str(exc), kb.cancel_pending_menu())
            return True
        self._await_category_code.discard(uid)
        self._reply(
            message,
            f"✅ کد دسته بندی «{row['code']}» ذخیره شد"
            + (f" ({row.get('label')})" if row.get("label") else "")
            + f".\nتعداد کدهای فعال: {len(self.db.list_category_codes(active_only=True))}",
            kb.inventory_menu(),
        )
        return True

    def on_list_categories(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        rows = self.db.list_category_codes(active_only=False)
        if not rows:
            self._reply(
                message,
                "هنوز هیچ کد دسته‌بندی ثبت نشده است.",
                kb.inventory_menu(),
            )
            return
        lines = ["کدهای دسته بندی:"]
        for r in rows:
            flag = "🟢" if r.get("active") else "🔴"
            label = f" — {r['label']}" if r.get("label") else ""
            lines.append(f"{flag} {r['code']}{label}")
        self._reply(message, "\n".join(lines), kb.inventory_menu())

    # ---------- analytics ----------

    def _build_analytics_bundle(
        self,
        frames: dict,
        *,
        start: date | None = None,
        end: date | None = None,
        forecast_days: float | None = None,
    ) -> dict[str, Any]:
        tank = frames.get("tank_consumption")
        inv = frames.get("product_inventory")
        monthly = frames.get("monthly_consumption")
        if start is None or end is None:
            end = end or date.today()
            start = start or (end - timedelta(days=29))
        days = forecast_days if forecast_days is not None else float(range_day_count(start, end))
        rates = daily_rates(tank, monthly)
        rates_in_range = daily_rates(tank, monthly, start=start, end=end)
        rem = remaining(inv)
        period = period_consumption(tank, start, end)
        crit = critical_materials(rates, rem, CRITICAL_DAYS)
        fc = forecast(rates_in_range if not rates_in_range.empty else rates, days)
        sug = suggest_requests(rates_in_range if not rates_in_range.empty else rates, rem, days)
        return {
            "start": start,
            "end": end,
            "days": days,
            "critical_days": CRITICAL_DAYS,
            "daily_rates": rates,
            "period_consumption": period,
            "remaining": rem,
            "critical": crit,
            "forecast": fc,
            "suggest": sug,
        }

    def on_tundish_filter_menu(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        self._reply(
            message,
            "فیلتر نوع تاندیش را برای تحلیل‌ها و PDF انتخاب کنید:\n"
            f"فیلتر فعلی: {self._selected_tundish_label(str(user['bale_user_id']))}",
            kb.tundish_filter_menu(),
        )

    def on_tundish_filter_choice(self, message: dict, label: str | None) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._analysis_tundish_filter[uid] = label
        self._clear_analysis_pending(uid)
        selected = label or kb.BTN_ALL_TUNDISHES
        self._reply(message, f"فیلتر تحلیل روی «{selected}» تنظیم شد.", kb.analytics_menu())

    def on_analytics_menu(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        self._clear_analysis_pending(user["bale_user_id"])
        session = self.db.get_or_create_session(user["bale_user_id"])
        done = self.db.session_completeness(session)
        lines = [
            "منوی گزارش‌ها / تحلیل تاندیش",
            f"آستانه بحرانی: {CRITICAL_DAYS} روز پوشش موجودی",
            "",
            self._status_text(session),
            "",
            f"فیلتر نوع تاندیش: {self._selected_tundish_label(str(user['bale_user_id']))}",
            "یک گزینه را انتخاب کنید:",
        ]
        if not any(done.values()):
            lines.append("\nهنوز فایلی بارگذاری نشده — ابتدا Excelها را از منوی اصلی بفرستید.")
        self._reply(message, "\n".join(lines), kb.analytics_menu())

    def on_daily_report(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        loaded = self._require_files(message, user, "daily")
        if not loaded:
            return
        _, frames, _ = loaded
        rates = daily_rates(frames.get("tank_consumption"), frames.get("monthly_consumption"))
        if rates.empty:
            self._reply(message, "داده‌ای برای محاسبه مصرف روزانه یافت نشد.", kb.analytics_menu())
            return
        lines = ["📈 میانگین مصرف روزانه (ماده / تاندیش):", ""]
        for _, row in rates.head(30).iterrows():
            tundish_type = row.get("tundish_type")
            type_s = f" | {tundish_type}" if tundish_type not in (None, "") else ""
            tid = row.get("tundish_id")
            tid_s = f" | تاندیش {tid}" if tid not in (None, "") else ""
            unit = row.get("unit") or ""
            lines.append(
                f"• {row['material_name']}{type_s}{tid_s}: {float(row['avg_daily']):.2f} {unit}/روز "
                f"(مجموع {float(row['total_qty']):.1f} در {int(row['days_span'])} روز)"
            )
        self._reply(message, "\n".join(lines), kb.analytics_menu())

    def on_remaining_critical(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        loaded = self._require_files(message, user, "remaining_critical")
        if not loaded:
            return
        _, frames, _ = loaded
        rates = daily_rates(frames.get("tank_consumption"), frames.get("monthly_consumption"))
        rem = remaining(frames.get("product_inventory"))
        crit = critical_materials(rates, rem, CRITICAL_DAYS)
        lines = ["📦 موجودی باقیمانده:", ""]
        if rem.empty:
            lines.append("موجودی خالی است.")
        else:
            for _, row in rem.head(30).iterrows():
                loc = f" @ {row['location']}" if row.get("location") else ""
                unit = row.get("unit") or ""
                lines.append(f"• {row['material_name']}: {float(row['remaining_qty']):.2f} {unit}{loc}")
        lines.append("")
        lines.append(f"⚠️ مواد بحرانی (پوشش < {CRITICAL_DAYS} روز):")
        if crit.empty:
            lines.append("ماده بحرانی‌ای شناسایی نشد.")
        else:
            for _, row in crit.iterrows():
                cover = row["days_of_cover"]
                cover_s = "∞" if cover == float("inf") else f"{float(cover):.1f}"
                unit = row.get("unit") or ""
                lines.append(
                    f"• {row['material_name']}: باقیمانده {float(row['remaining_qty']):.2f} {unit} | "
                    f"مصرف روز {float(row['avg_daily']):.2f} | پوشش ≈ {cover_s} روز"
                )
        self._reply(message, "\n".join(lines), kb.analytics_menu())

    def on_surplus_report(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        loaded = self._require_files(message, user, "surplus")
        if not loaded:
            return
        _, frames, _ = loaded
        rates = daily_rates(frames.get("tank_consumption"), frames.get("monthly_consumption"))
        rem = remaining(frames.get("product_inventory"))
        surplus = surplus_materials(rates, rem)
        cover_th = max(float(CRITICAL_DAYS) * 3.0, float(SURPLUS_COVER_DAYS))
        lines = [
            "📦 گزارش مواد مازاد",
            f"تعریف: پوشش > {cover_th:g} روز، یا موجودی بیش از نیاز {SURPLUS_FORECAST_DAYS:g} روز؛",
            "مواد با موجودی ولی بدون مصرف ثبت‌شده = «مازاد/بدون مصرف».",
            "",
            format_surplus_list_fa(surplus),
        ]
        self._reply(message, "\n".join(lines), kb.analytics_menu())

    def _ask_date_range(self, message: dict, user: dict, mode: str) -> None:

        self._analysis_pending[user["bale_user_id"]] = {"mode": mode, "await": "range"}
        hint = (
            "بازه زمانی را انتخاب کنید:\n"
            f"• {kb.BTN_RANGE_TODAY}\n"
            f"• {kb.BTN_RANGE_7}\n"
            f"• {kb.BTN_RANGE_30}\n"
            f"• {kb.BTN_RANGE_CUSTOM} — سپس پیام بفرستید:\n"
            "  از YYYY-MM-DD تا YYYY-MM-DD\n"
            "مثال: از 2026-09-01 تا 2026-09-15"
        )
        self._reply(message, hint, kb.date_range_menu())

    def on_period_prompt(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if not self._require_files(message, user, "period"):
            return
        self._ask_date_range(message, user, "period")

    def on_forecast_prompt(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if not self._require_files(message, user, "forecast"):
            return
        self._ask_date_range(message, user, "forecast")

    def on_suggest_prompt(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if not self._require_files(message, user, "suggest"):
            return
        self._ask_date_range(message, user, "suggest")

    def on_date_range_choice(self, message: dict, preset: str | None, custom_text: str | None = None) -> bool:
        """Handle date-range keyboard or custom message. Returns True if consumed."""
        user = self._user_or_deny(message)
        if not user:
            return True
        uid = user["bale_user_id"]
        pending = self._analysis_pending.get(uid)
        if not pending or pending.get("await") != "range":
            return False

        start: date | None = None
        end: date | None = None
        if preset == "custom":
            self._reply(
                message,
                "لطفاً بازه را با این فرمت بفرستید:\nاز YYYY-MM-DD تا YYYY-MM-DD",
                kb.date_range_menu(),
            )
            return True
        if custom_text:
            parsed = parse_custom_range_message(custom_text)
            if not parsed:
                return False
            start, end = parsed
        elif preset:
            try:
                start, end = resolve_preset_range(preset)
            except ValueError:
                return False
        else:
            return False

        mode = pending.get("mode")
        self._clear_analysis_pending(uid)
        self._run_ranged_analysis(message, user, mode, start, end)
        return True

    def _run_ranged_analysis(
        self, message: dict, user: dict, mode: str, start: date, end: date
    ) -> None:
        goal = {"period": "period", "forecast": "forecast", "suggest": "suggest"}.get(mode, "period")
        loaded = self._require_files(message, user, goal)
        if not loaded:
            return
        _, frames, _ = loaded
        days = range_day_count(start, end)
        tank = frames.get("tank_consumption")
        monthly = frames.get("monthly_consumption")
        inv = frames.get("product_inventory")

        if mode == "period":
            period = period_consumption(tank, start, end)
            lines = [
                f"📅 مصرف مواد از {start.isoformat()} تا {end.isoformat()} ({days} روز):",
                "",
            ]
            if period.empty:
                lines.append("در این بازه مصرفی ثبت نشده است.")
            else:
                for _, row in period.head(40).iterrows():
                    tundish_type = row.get("tundish_type")
                    type_s = f" | {tundish_type}" if tundish_type not in (None, "") else ""
                    tid = row.get("tundish_id")
                    tid_s = f" | تاندیش {tid}" if tid not in (None, "") else ""
                    unit = row.get("unit") or ""
                    lines.append(
                        f"• {row['material_name']}{type_s}{tid_s}: {float(row['quantity']):.2f} {unit}"
                    )
            self._reply(message, "\n".join(lines), kb.analytics_menu())
            return

        rates = daily_rates(tank, monthly)
        rates_r = daily_rates(tank, monthly, start=start, end=end)
        use_rates = rates_r if not rates_r.empty else rates

        if mode == "forecast":
            fc = forecast(use_rates, days)
            lines = [
                f"🔮 پیش‌بینی نیاز تاندیش برای {days} روز "
                f"({start.isoformat()} تا {end.isoformat()}):",
                "",
            ]
            if fc.empty:
                lines.append("داده‌ای برای پیش‌بینی نیست.")
            else:
                for _, row in fc.head(40).iterrows():
                    tundish_type = row.get("tundish_type")
                    type_s = f" | {tundish_type}" if tundish_type not in (None, "") else ""
                    tid = row.get("tundish_id")
                    tid_s = f" | تاندیش {tid}" if tid not in (None, "") else ""
                    unit = row.get("unit") or ""
                    lines.append(
                        f"• {row['material_name']}{type_s}{tid_s}: "
                        f"{float(row['forecast_need']):.2f} {unit} "
                        f"(روزانه {float(row['avg_daily']):.2f} × {days})"
                    )
            self._reply(message, "\n".join(lines), kb.analytics_menu())
            return

        if mode == "suggest":
            sug = suggest_requests(use_rates, remaining(inv), days)
            lines = [
                f"🛒 پیشنهاد درخواست مواد برای {days} روز "
                f"({start.isoformat()} تا {end.isoformat()}):",
                "",
                format_suggest_list_fa(sug),
                "",
                "فرمول: max(0, پیش‌بینی نیاز − موجودی باقیمانده)",
            ]
            self._reply(message, "\n".join(lines), kb.analytics_menu())

    def on_analytics_pdf(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        loaded = self._require_files(message, user, "full")
        if not loaded:
            return
        session, frames, metas = loaded
        # monthly optional — still include if present
        self._reply(message, "در حال ساخت PDF تحلیل تاندیش…")
        try:
            end = date.today()
            start = end - timedelta(days=29)
            analytics = self._build_analytics_bundle(frames, start=start, end=end)
            pdf_path = generate_report(frames, metas, user, analytics=analytics)
            self.db.save_report(
                user["bale_user_id"],
                session["id"],
                str(pdf_path),
                {k: int(metas[k]["visible_rows"]) for k in metas},
            )
            self.client.send_document(
                self._chat_id(message),
                pdf_path,
                caption="گزارش تحلیل تاندیش",
            )
            self._reply(message, "PDF تحلیل ارسال شد.", kb.analytics_menu())
        except Exception as exc:  # noqa: BLE001
            logger.exception("analytics pdf failed")
            self._reply(message, f"خطا در تولید PDF: {exc}", kb.analytics_menu())

    # ---------- dispatcher ----------
    def handle_message(self, message: dict) -> None:
        if not message:
            return
        text = (message.get("text") or "").strip()
        if message.get("document"):
            self.on_document(message)
            return

        if not text:
            return

        if text.startswith("/"):
            parts = text.split()
            cmd = parts[0].split("@")[0].lower()
            args = parts[1:]
            mapping = {
                "/start": lambda: self.cmd_start(message),
                "/help": lambda: self.cmd_help(message),
                "/users": lambda: self.cmd_users(message),
                "/adduser": lambda: self.cmd_adduser(message, args),
                "/setrole": lambda: self.cmd_setrole(message, args),
                "/setscope": lambda: self.cmd_setscope(message, args),
                "/reset": lambda: self.cmd_reset(message),
                "/status": lambda: self.on_status(message),
                "/report": lambda: self.on_generate(message),
                "/analytics": lambda: self.on_analytics_menu(message),
            }
            handler = mapping.get(cmd)
            if handler:
                handler()
            else:
                self._reply(message, "دستور ناشناخته. /help را ببینید.")
            return

        # custom date range while awaiting
        if parse_custom_range_message(text):
            if self.on_date_range_choice(message, preset=None, custom_text=text):
                return

        # category code entry (plain 4-digit text while awaiting)
        if self.on_category_code_text(message, text):
            return

        # keyboard buttons
        if text == kb.BTN_HELP:
            self.cmd_help(message)
            return
        if text == kb.BTN_STATUS:
            self.on_status(message)
            return
        if text == kb.BTN_GENERATE:
            self.on_generate(message)
            return
        if text == kb.BTN_RESET:
            self.cmd_reset(message)
            return
        if text == kb.BTN_CANCEL_PENDING:
            self.on_cancel_pending(message)
            return
        if text == kb.BTN_USERS:
            self.cmd_users(message)
            return
        if text == kb.BTN_ANALYTICS:
            self.on_analytics_menu(message)
            return
        if text == kb.BTN_TUNDISH_FILTER:
            self.on_tundish_filter_menu(message)
            return
        if text == kb.BTN_ALL_TUNDISHES:
            self.on_tundish_filter_choice(message, None)
            return
        if text == kb.BTN_TUNDISH_SLAB:
            self.on_tundish_filter_choice(message, kb.BTN_TUNDISH_SLAB)
            return
        if text == kb.BTN_TUNDISH_BLOOM:
            self.on_tundish_filter_choice(message, kb.BTN_TUNDISH_BLOOM)
            return
        if text == kb.BTN_TUNDISH_BILLET:
            self.on_tundish_filter_choice(message, kb.BTN_TUNDISH_BILLET)
            return
        if text == kb.BTN_BACK_MAIN:
            user = self._user_or_deny(message)
            if user:
                self._clear_analysis_pending(user["bale_user_id"])
                self._reply(message, "منوی اصلی:", kb.main_menu(require_manager(user)))
            return
        if text == kb.BTN_BACK_ANALYTICS:
            self.on_analytics_menu(message)
            return
        if text == kb.BTN_DAILY:
            self.on_daily_report(message)
            return
        if text == kb.BTN_REMAINING:
            self.on_remaining_critical(message)
            return
        if text == kb.BTN_SURPLUS:
            self.on_surplus_report(message)
            return
        if text == kb.BTN_PERIOD:
            self.on_period_prompt(message)
            return
        if text == kb.BTN_FORECAST:
            self.on_forecast_prompt(message)
            return
        if text == kb.BTN_SUGGEST:
            self.on_suggest_prompt(message)
            return
        if text == kb.BTN_ANALYTICS_PDF:
            self.on_analytics_pdf(message)
            return
        if text == kb.BTN_RANGE_TODAY:
            if self.on_date_range_choice(message, "today"):
                return
        if text == kb.BTN_RANGE_7:
            if self.on_date_range_choice(message, "7d"):
                return
        if text == kb.BTN_RANGE_30:
            if self.on_date_range_choice(message, "30d"):
                return
        if text == kb.BTN_RANGE_CUSTOM:
            if self.on_date_range_choice(message, "custom"):
                return

        if text == kb.BTN_INV_MENU or text == kb.BTN_INV:
            self.on_inventory_menu(message)
            return
        if text == kb.BTN_INV_ADD_CATEGORY:
            self.on_add_category_prompt(message)
            return
        if text == kb.BTN_INV_LIST_CATEGORIES:
            self.on_list_categories(message)
            return

        file_type = kb.button_to_file_type(text)
        if file_type:
            self.on_pick_file_type(message, file_type)
            return

        user = ensure_registered(self.db, self._uid(message), self._display_name(message))
        self._reply(
            message,
            "لطفاً از دکمه‌های منو استفاده کنید یا /help را بزنید.",
            kb.main_menu(require_manager(user)) if user else None,
        )

    def handle_update(self, update: dict) -> None:
        try:
            if "message" in update:
                self.handle_message(update["message"])
        except Exception:  # noqa: BLE001
            logger.exception("update failed: %s", update.get("update_id"))
