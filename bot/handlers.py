"""Message/command handlers — request-driven three-file upload + RBAC."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from auth.rbac import (
    can_generate_report,
    ensure_registered,
    require_manager,
    require_owner,
    role_label,
)
from bot import keyboards as kb
from bot.bale_api import BaleClient
from config import FILE_TYPES, ROLES, UPLOAD_DIR, ensure_dirs
from db.models import Database
from excel.processor import ExcelValidationError, process_session_files
from pdf.generator import generate_report

logger = logging.getLogger(__name__)


HELP_TEXT = """راهنمای بازوی گزارش تاندیش

این بازو به‌صورت درخواست‌محور کار می‌کند:
۱) از منو نوع فایل را انتخاب کنید
۲) فایل Excel مربوطه (.xlsx) را پیوست کنید
۳) همین کار را برای هر سه نوع انجام دهید
۴) دکمه «تولید گزارش PDF» را بزنید

انواع فایل:
• مقدار مصرفی هر تاندیش
• موجودی محصولات
• مصرف ماهانه مواد

نقش‌ها:
• مدیر — همه ردیف‌ها + مدیریت کاربران
• کاردان مسئول — فقط حوزه/دامنه خودش
• تکنسین — فقط ردیف‌های تخصیص‌یافته به خودش

دستورات مدیر:
/users
/adduser <bale_id> <role> [scope] [name...]
/setrole <bale_id> <role>
/setscope <bale_id> <scope>
/reset — پاک کردن جلسه آپلود جاری
"""


class BotApp:
    def __init__(self, client: BaleClient, db: Database) -> None:
        self.client = client
        self.db = db
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
            lines.append("\nهمه فایل‌ها آماده‌اند — می‌توانید «تولید گزارش PDF» را بزنید.")
        return "\n".join(lines)

    # ---------- commands ----------
    def cmd_start(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        self.db.get_or_create_session(user["bale_user_id"])
        text = (
            "سلام! به بازوی «گزارش تاندیش» خوش آمدید.\n\n"
            f"نقش شما: {role_label(user['role'])}\n"
            f"حوزه: {user.get('scope') or '—'}\n\n"
            "از منو نوع فایل Excel را انتخاب کنید، سپس همان فایل را ارسال کنید.\n"
            "پس از دریافت هر سه فایل، گزارش PDF ترکیبی ساخته می‌شود."
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
            # if role needs scope, first optional token is scope unless it looks like a name-only
            if role == "responsible_officer":
                scope = args[2]
                name_parts = args[3:]
            else:
                # technician/manager: args[2:] may be scope then name, or just name
                if role == "technician" and len(args) >= 3:
                    # allow optional scope for technicians too
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
        self.db.reset_session(user["bale_user_id"])
        self._reply(message, "جلسه آپلود پاک شد. از منو دوباره شروع کنید.", kb.main_menu(require_manager(user)))

    # ---------- request-driven flow ----------
    def on_pick_file_type(self, message: dict, file_type: str) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
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
        session = self.db.get_or_create_session(user["bale_user_id"])
        self._reply(message, "آپلود لغو شد.\n" + self._status_text(session), kb.main_menu(require_manager(user)))

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

        # validate columns early
        try:
            from excel.processor import load_excel, validate_required_columns

            df = load_excel(dest)
            missing = validate_required_columns(df, pending)
            critical = [c for c in ("domain", "assignee_id", "assignee_name") if c not in df.columns]
            if critical:
                dest.unlink(missing_ok=True)
                self._reply(
                    message,
                    "فایل ستون‌های ضروری RBAC را ندارد: "
                    + "، ".join(critical)
                    + "\nلطفاً مطابق قالب samples اصلاح و دوباره ارسال کنید.",
                    kb.cancel_pending_menu(),
                )
                return
            warn = ""
            if missing:
                warn = "\n(هشدار: برخی ستون‌های توصیه‌شده نیست: " + "، ".join(missing) + ")"
        except ExcelValidationError as exc:
            dest.unlink(missing_ok=True)
            self._reply(message, str(exc), kb.cancel_pending_menu())
            return

        session = self.db.store_file_slot(user["bale_user_id"], pending, str(dest))
        label = FILE_TYPES[pending]["label_fa"]
        self._reply(
            message,
            f"✅ فایل «{label}» ذخیره شد.{warn}\n\n" + self._status_text(session),
            kb.main_menu(require_manager(user)),
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

        paths = {
            "tank_consumption": session.get("tank_path"),
            "product_inventory": session.get("inventory_path"),
            "monthly_consumption": session.get("monthly_path"),
        }
        self._reply(message, "در حال پردازش و ساخت PDF…")
        try:
            frames, metas = process_session_files(paths, user)
            pdf_path = generate_report(frames, metas, user)
            counts = {k: int(metas[k]["visible_rows"]) for k in metas}
            self.db.save_report(user["bale_user_id"], session["id"], str(pdf_path), counts)
            self.db.mark_session_done(session["id"])
            # start fresh collecting session for next round
            self.db.get_or_create_session(user["bale_user_id"])
            self.client.send_document(
                self._chat_id(message),
                pdf_path,
                caption="گزارش تاندیش / خلاصه داده‌های آپلود‌شده",
            )
            self._reply(
                message,
                "گزارش ارسال شد. برای گزارش بعدی دوباره از منو فایل‌ها را بارگذاری کنید.",
                kb.main_menu(require_manager(user)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("generate failed")
            self._reply(message, f"خطا در تولید گزارش: {exc}", kb.main_menu(require_manager(user)))

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
            }
            handler = mapping.get(cmd)
            if handler:
                handler()
            else:
                self._reply(message, "دستور ناشناخته. /help را ببینید.")
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
