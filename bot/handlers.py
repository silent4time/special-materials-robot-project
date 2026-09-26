"""Message/command handlers — request-driven three-file upload + RBAC + tundish analytics."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
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
    apply_inventory_ledger,
    remaining,
    resolve_preset_range,
    suggest_requests,
    surplus_materials,
)
from auth.rbac import (
    can_configure_catalog,
    can_request_materials,
    can_generate_report,
    ensure_registered,
    require_manager,
    require_owner,
    role_label,
)
from bot import keyboards as kb
from bot.jalali import format_date, format_datetime
from bot.bale_api import BaleClient
from bot.settings_text import (
    DEFAULT_INVITE_TEXT,
    PLACEHOLDER_HINT_INVITE,
    PLACEHOLDER_HINT_WELCOME,
    format_invite_text,
    format_welcome_text,
)
from config import BOT_ASSETS_DIR, BOT_USERNAME, CRITICAL_DAYS, FILE_TYPES, ROLES, SITE_STOCK_GROUPS, SURPLUS_COVER_DAYS, SURPLUS_FORECAST_DAYS, UPLOAD_DIR, ensure_dirs
from db.models import Database
from excel.processor import (
    ExcelValidationError,
    extract_and_save_clean,
    format_inventory_table_fa,
    merge_clean_frames,
    process_file,
    process_session_files,
)
from excel.monthly_summary import (
    SUMMARY_FILE_NAME,
    build_monthly_summary,
    summary_sections_for_pdf,
)
from pdf.generator import generate_monthly_summary_pdf, generate_report

logger = logging.getLogger(__name__)

HELP_TEXT = """راهنمای بازوی گزارش مواد / تاندیش

جریان اصلی:
۱) موجودی انبار و مصرف ماهیانه را از منو با Excel (.xlsx) بفرستید
۲) «موجودی روزانه سایت» را به‌صورت تعاملی وارد کنید (نه Excel تکنسین)
۳) دکمه «تولید گزارش PDF» یا «گزارش‌ها / تحلیل تاندیش» را بزنید

موجودی روزانه سایت (ورود تعاملی در SQLite):
• سه بخش: موجودی مواد اسلب / بلوم / بیلت
• ربات اقلام تخصیص‌یافته به هر گروه را نشان می‌دهد؛ مقدار را یکی‌یکی بفرستید
• داده در جدول site_stock_entries ذخیره می‌شود (upsert روزانه)

تنظیمات اقلام سایت / تخصیص به گروه (مالک، مدیر، کاردان مسئول — نه تکنسین):
• همگام‌سازی اقلام از آخرین استخراج موجودی انبار
• تخصیص هر قلم به اسلب یا بلوم یا بیلت

انواع فایل Excel:
• موجودی انبار — ۳ ستون: کد دسته بندی، کد و شرح کالا، موجودی
• مصرف ماهیانه مواد

تحلیل:
• مصرف روزانه / بازه‌ای / پیشنهاد / بحرانی / مازاد / پیش‌بینی
• مواد بحرانی — پوشش < CRITICAL_DAYS={critical} روز
• اگر موجودی روزانه سایت ثبت شده باشد، برای «موجودی و مواد بحرانی» به‌عنوان منبع باقیمانده سایت استفاده می‌شود

درخواست مواد (مالک / مدیر / کاردان مسئول):
• دکمه «🛒 درخواست مواد» در منوی اصلی
• انتخاب پوشش روز (۷ / ۱۴ / ۳۰)، بررسی پیشنهاد، تأیید یا اصلاح مقدار
• پس از تأیید، از موجودی انبار (ledger) کسر می‌شود

برگشت به انبار (مالک / مدیر / کاردان مسئول):
• دکمه «↩️ برگشت به انبار» — پیشنهاد مواد مازاد سایت
• پس از تأیید، به موجودی انبار (ledger مثبت) افزوده می‌شود

نقش‌ها:
• مالک / مدیر — همه ردیف‌ها + مدیریت کاربران + تنظیمات اقلام
• کاردان مسئول — مثل بقیه نقش‌های عملیاتی کار می‌کند؛ همه ردیف‌ها + تنظیمات اقلام سایت
• تکنسین — فقط ورود موجودی روزانه سایت (سه گروه)؛ بدون تنظیمات/گزارش

شناسایی افراد با شناسه اکانت بله (bale_user_id) انجام می‌شود.
هر ورودی داده با ثبت‌کننده = نام فرد از اکانت بله (همراه شناسه بله) ذخیره می‌شود.

مدیریت کاربران (مالک/مدیر):
• از منوی «کاربران»: اضافه / اصلاح نقش / حذف / لیست + لینک دعوت
• دستورات اختیاری:
  /users
  /adduser <bale_id> <role> [name...]
  /setrole <bale_id> <role>
  /setscope <bale_id> <scope>  (ابزار قدیمی؛ معمولاً لازم نیست)

تنظیمات ربات (مالک/مدیر):
• متن دعوت‌نامه کاربران (قالب + تصویر اختیاری)
• پیام خوشامدگویی (قالب + تصویر اختیاری)
• لوگوی ربات (تصویر برندینگ؛ در خوشامدگویی نمایش داده می‌شود)

/reset — پاک کردن جلسه آپلود و وضعیت ورود جاری
""".format(
    critical=int(CRITICAL_DAYS) if CRITICAL_DAYS == int(CRITICAL_DAYS) else CRITICAL_DAYS,
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
        # site stock interactive entry: uid -> {group, items, index, values}
        self._site_stock_pending: dict[str, dict[str, Any]] = {}
        # catalog assignment: uid -> {item_id} while choosing group
        self._catalog_assign_pending: dict[str, dict[str, Any]] = {}
        # user-management interactive flows
        self._users_pending: dict[str, dict[str, Any]] = {}
        # bot settings interactive flows
        self._bot_settings_pending: dict[str, dict[str, Any]] = {}
        # material request interactive flow
        self._material_req_pending: dict[str, dict[str, Any]] = {}
        self._warehouse_ret_pending: dict[str, dict[str, Any]] = {}
        self._bot_username: str | None = None
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

    def _format_actor(
        self,
        user: dict | None = None,
        *,
        bale_user_id: str | int | None = None,
        actor_display_name: str | None = None,
    ) -> str:
        """Format an actor as the display name followed by the Bale user id."""
        user = user or {}
        uid = str(user.get("bale_user_id") or bale_user_id or "").strip()

        def meaningful(value: object) -> str:
            value = str(value or "").strip()
            return value if value and value != uid else ""

        name = meaningful(user.get("display_name"))
        if not name:
            name = meaningful(actor_display_name)
        if not name and uid:
            stored = self.db.get_user(uid)
            name = meaningful((stored or {}).get("display_name"))
        if not name:
            name = meaningful(user.get("username"))

        if name and uid:
            return f"{name} ({uid})"
        if name:
            return name
        return f"کاربر بدون نام ({uid})" if uid else "کاربر بدون نام"

    def _reply(self, message: dict, text: str, markup: dict | None = None) -> None:
        self.client.send_message(self._chat_id(message), text, reply_markup=markup)

    def _user_or_deny(self, message: dict) -> dict | None:
        user = ensure_registered(self.db, self._uid(message), self._display_name(message))
        if not user:
            self._reply(
                message,
                "شما در سیستم ثبت نشده‌اید.\n"
                "از مدیر بخواهید از منوی «کاربران → اضافه کردن کاربر» لینک دعوت برایتان بفرستد.",
            )
            return None
        return user

    def _deny_technician(self, message: dict, user: dict) -> bool:
        """Deny non-upload features and restore the technician-only menu."""
        if user.get("role") != "technician":
            return False
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(uid)
        self._await_category_code.discard(uid)
        self._site_stock_pending.pop(uid, None)
        self._catalog_assign_pending.pop(uid, None)
        self._users_pending.pop(uid, None)
        self._bot_settings_pending.pop(uid, None)
        self._material_req_pending.pop(uid, None)
        self._warehouse_ret_pending.pop(uid, None)
        self._reply(
            message,
            "دسترسی ندارید؛ فقط ورود موجودی روزانه سایت برای نقش تکنسین فعال است.",
            kb.main_menu(user),
        )
        return True

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

    def _load_latest_inventory_frame(
        self, user: dict, *, include_catalog_fallback: bool = True
    ) -> tuple[pd.DataFrame | None, bool, str | None]:
        """Load the newest usable cleaned inventory and its provenance.

        Returns (frame, has_inventory_extract, source). A catalog fallback keeps
        category/name visible when an old extract file was removed, but quantity
        remains an em dash because catalog_items does not store warehouse qty.
        """
        uid = str(user["bale_user_id"])
        session = self.db.get_or_create_session(uid)
        latest = self.db.get_latest_extracted(uid, "product_inventory")
        candidates: list[tuple[str, str]] = []
        if latest and latest.get("clean_path"):
            candidates.append((str(latest["clean_path"]), "آخرین استخراج موجودی انبار"))
        if session.get("inventory_path"):
            candidates.append((str(session["inventory_path"]), "موجودی انبار جلسه جاری"))

        seen: set[str] = set()
        for raw_path, source in candidates:
            if raw_path in seen:
                continue
            seen.add(raw_path)
            try:
                frame, _ = process_file(raw_path, "product_inventory", user)
                frame = self._inventory_with_ledger(frame)
                return frame, True, source
            except Exception as exc:  # noqa: BLE001
                logger.warning("inventory list load failed for %s: %s", raw_path, exc)

        if include_catalog_fallback:
            catalog = self.db.list_catalog_items(active_only=True)
            if catalog:
                rows = [
                    {
                        "category_code": item.get("category_code"),
                        "id": item.get("id"),
                        "item_code_desc": item.get("name_desc"),
                        "product_name": item.get("name_desc"),
                        "quantity": None,
                    }
                    for item in catalog
                ]
                return pd.DataFrame(rows), False, "کاتالوگ همگام‌شده"
        return None, False, None

    @staticmethod
    def _normalise_inventory_category(value: object) -> str | None:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        text = str(value).strip()
        if not text or text.casefold() == "nan":
            return None
        if text.endswith(".0") and text[:-2].isdigit():
            text = text[:-2]
        if text.isdigit() and len(text) <= 4:
            text = text.zfill(4)
        return text if text.isdigit() and len(text) == 4 else None

    def _category_inventory_table(self, user: dict) -> tuple[pd.DataFrame, bool, str | None]:
        """Build rows for active allowlisted categories, including empty codes."""
        active_rows = self.db.list_category_codes(active_only=True)
        active_codes = [str(row["code"]) for row in active_rows]
        frame, has_extract, source = self._load_latest_inventory_frame(user)
        if frame is None:
            work = pd.DataFrame(columns=["category_code", "id", "item_code_desc", "quantity"])
        else:
            work = frame.copy()
            if "category_code" in work.columns:
                work["category_code"] = work["category_code"].map(
                    self._normalise_inventory_category
                )
                work = work[work["category_code"].isin(active_codes)].copy()
            else:
                work = work.iloc[0:0].copy()

        present = set(work.get("category_code", pd.Series(dtype=str)).dropna().astype(str))
        missing = [
            {"category_code": code, "item_code_desc": "—", "quantity": None}
            for code in active_codes
            if code not in present
        ]
        if missing:
            work = pd.concat([work, pd.DataFrame(missing)], ignore_index=True)
        if not work.empty:
            order = {code: i for i, code in enumerate(active_codes)}
            work["_category_order"] = work["category_code"].map(order).fillna(len(order))
            work = work.sort_values(["_category_order"], kind="stable").drop(
                columns=["_category_order"]
            )
        return work.reset_index(drop=True), has_extract, source

    @staticmethod
    def _inventory_cell(value: object, fallback: str = "—") -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return fallback
        text = str(value).strip()
        return text if text and text.casefold() not in {"nan", "none"} else fallback

    def _clear_analysis_pending(self, uid: str) -> None:
        self._analysis_pending.pop(uid, None)

    def _clear_material_req_pending(self, uid: str) -> None:
        self._material_req_pending.pop(str(uid), None)

    def _clear_warehouse_ret_pending(self, uid: str) -> None:
        self._warehouse_ret_pending.pop(str(uid), None)

    def _inventory_with_ledger(self, frame: pd.DataFrame | None) -> pd.DataFrame | None:
        """Apply inventory_ledger deltas onto a warehouse inventory frame."""
        if frame is None:
            return None
        sums = self.db.inventory_ledger_sums()
        return apply_inventory_ledger(frame, sums.get("by_id"), sums.get("by_name"))

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
        if self._deny_technician(message, user):
            return None
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
    # ---------- bot username / invite links ----------
    def _bot_username_cached(self) -> str:
        if self._bot_username:
            return self._bot_username
        try:
            me = self.client.get_me()
            uname = (me.get("username") or "").strip().lstrip("@")
            if uname:
                self._bot_username = uname
                return uname
        except Exception:  # noqa: BLE001
            logger.warning("getMe for username failed; using BOT_USERNAME=%s", BOT_USERNAME)
        self._bot_username = BOT_USERNAME or "nasoz_bot"
        return self._bot_username

    def _invite_url(self, token: str) -> str:
        return f"https://ble.ir/{self._bot_username_cached()}?start={token}"

    def _clear_users_pending(self, uid: str) -> None:
        self._users_pending.pop(str(uid), None)

    def _format_users_list(self, active_only: bool = True) -> str:
        rows = self.db.list_users(active_only=active_only)
        if not rows:
            return "هیچ کاربری ثبت نشده."
        lines = ["لیست کاربران:"]
        for i, r in enumerate(rows, 1):
            flag = "🟢" if r["active"] else "🔴"
            lines.append(
                f"{i}) {flag} شناسه={r['bale_user_id']} | {r.get('display_name')} | "
                f"{role_label(r['role'])} | حوزه={r.get('scope') or '—'}"
            )
        return "\n".join(lines)

    def _welcome_text(self, user: dict) -> str:
        template = self.db.get_setting("welcome_text")
        return format_welcome_text(template, user)

    def _branding_photo_path(self, *, prefer_welcome: bool = False) -> Path | None:
        """Return an existing branding image path, or None."""
        keys = ("welcome_image_path", "logo_path") if prefer_welcome else ("logo_path",)
        for key in keys:
            raw = self.db.get_setting(key)
            if raw:
                path = Path(raw)
                if path.is_file():
                    return path
        return None

    def _send_welcome(self, message: dict, user: dict, prefix: str = "") -> None:
        """Send welcome: one photo (welcome_image else logo) + caption, or text only."""
        text = (prefix + self._welcome_text(user)).strip()
        markup = kb.main_menu(user)
        photo = self._branding_photo_path(prefer_welcome=True)
        if photo is not None:
            try:
                self.client.send_photo(
                    self._chat_id(message),
                    photo,
                    caption=text,
                    reply_markup=markup,
                )
                return
            except Exception:  # noqa: BLE001
                logger.exception("send_photo welcome failed; falling back to text")
        self._reply(message, text, markup)

    def _redeem_invite(self, message: dict, token: str) -> None:
        """Redeem deep-link invite BEFORE any registered-user gate."""
        uid = self._uid(message)
        name = self._display_name(message)
        try:
            user = self.db.consume_invite(token, uid, name)
        except ValueError as exc:
            self._reply(message, str(exc))
            return
        self.db.get_or_create_session(user["bale_user_id"])
        self._send_welcome(message, user, prefix="✅ دعوت پذیرفته شد.\n\n")

    # ---------- commands ----------
    def cmd_start(self, message: dict, args: list[str] | None = None) -> None:
        args = args or []
        token = (args[0] if args else "").strip()
        # Invite redeem must work for users not yet in DB — no _user_or_deny first.
        if token:
            self._redeem_invite(message, token)
            return
        uid = self._uid(message)
        name = self._display_name(message)
        user = ensure_registered(self.db, uid, name)
        if user:
            self.db.get_or_create_session(user["bale_user_id"])
            self._send_welcome(message, user)
            return
        # Bare /start with zero active owners → first user claims owner (race-safe).
        claimed = self.db.try_claim_first_owner(uid, name)
        if claimed:
            self.db.get_or_create_session(claimed["bale_user_id"])
            self._send_welcome(
                message,
                claimed,
                prefix="شما به‌عنوان اولین کاربر، مالک سیستم شدید.\n\n",
            )
            return
        self._reply(
            message,
            "شما در سیستم ثبت نشده‌اید.\n"
            "از مدیر بخواهید از منوی «کاربران → اضافه کردن کاربر» لینک دعوت برایتان بفرستد.",
        )

    def cmd_help(self, message: dict) -> None:
        user = ensure_registered(self.db, self._uid(message), self._display_name(message))
        self._reply(
            message,
            HELP_TEXT,
            kb.main_menu(user) if user else None,
        )

    def _require_manager_user(self, message: dict) -> dict | None:
        user = self._user_or_deny(message)
        if not user:
            return None
        if self._deny_technician(message, user):
            return None
        if not require_manager(user):
            self._reply(message, "فقط مالک یا مدیر به بخش کاربران دسترسی دارد.", kb.main_menu(user))
            return None
        return user

    def on_users_menu(self, message: dict) -> None:
        user = self._require_manager_user(message)
        if not user:
            return
        self._clear_users_pending(str(user["bale_user_id"]))
        self._reply(message, "مدیریت کاربران — یک گزینه را انتخاب کنید:", kb.users_menu())

    def cmd_users(self, message: dict) -> None:
        """List users (also used from submenu «لیست کاربران»)."""
        user = self._require_manager_user(message)
        if not user:
            return
        self._reply(message, self._format_users_list(active_only=False), kb.users_menu())

    def on_users_add_start(self, message: dict) -> None:
        user = self._require_manager_user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._users_pending[uid] = {"mode": "add_role"}
        self._reply(
            message,
            "نقش کاربر جدید را انتخاب کنید:",
            kb.role_menu(include_owner=require_owner(user)),
        )

    def on_users_edit_start(self, message: dict) -> None:
        user = self._require_manager_user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._users_pending[uid] = {"mode": "edit_pick_user"}
        self._reply(
            message,
            self._format_users_list(active_only=True)
            + "\n\nشناسه کاربر را بفرستید:",
            kb.users_menu(),
        )

    def on_users_delete_start(self, message: dict) -> None:
        user = self._require_manager_user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._users_pending[uid] = {"mode": "delete_pick_user"}
        self._reply(
            message,
            self._format_users_list(active_only=True)
            + "\n\nشناسه کاربری که باید حذف (غیرفعال) شود را بفرستید:",
            kb.users_menu(),
        )

    def _invite_message_text(self, role: str, scope: str | None = None) -> str:
        """Formal Persian invitation body for the invitee (no bot username / raw URL)."""
        _ = scope  # kept for backward compat
        template = self.db.get_setting("invite_text")
        return format_invite_text(template, role)

    def _create_and_send_invite(
        self, message: dict, actor: dict, role: str, scope: str | None
    ) -> None:
        invite = self.db.create_invite(
            role=role,
            scope=scope,
            created_by=str(actor["bale_user_id"]),
            expires_days=7,
        )
        url = self._invite_url(invite["token"])
        body = self._invite_message_text(role, scope)
        markup = kb.invite_url_button(url)
        img_raw = self.db.get_setting("invite_image_path")
        sent_photo = False
        if img_raw:
            img = Path(img_raw)
            if img.is_file():
                try:
                    self.client.send_photo(
                        self._chat_id(message),
                        img,
                        caption=body,
                        reply_markup=markup,
                    )
                    sent_photo = True
                except Exception:  # noqa: BLE001
                    logger.exception("send_photo invite failed; falling back to text")
        if not sent_photo:
            # Forwardable invite: formal body + inline URL button only (no reply keyboard).
            self._reply(message, body, markup)
        scope_note = f" — حوزه: {scope}" if scope else ""
        self._reply(
            message,
            f"لینک دعوت ساخته شد — نقش: {role_label(role)}{scope_note} — "
            "این پیام بالا را برای فرد بفرستید.\n"
            "برای ادامه مدیریت کاربران از منو استفاده کنید",
            kb.users_menu(),
        )

    def on_users_flow_text(self, message: dict, text: str) -> bool:
        """Handle pending user-management text/role picks. Returns True if consumed."""
        uid = str(self._uid(message))
        pending = self._users_pending.get(uid)
        if not pending:
            return False
        user = self._require_manager_user(message)
        if not user:
            self._clear_users_pending(uid)
            return True

        mode = pending.get("mode")
        raw = (text or "").strip()

        # Let top-level users menu buttons re-start their own handlers
        nav_buttons = {
            kb.BTN_USERS,
            kb.BTN_USERS_ADD,
            kb.BTN_USERS_EDIT,
            kb.BTN_USERS_DELETE,
            kb.BTN_USERS_LIST,
        }
        if raw in nav_buttons:
            self._clear_users_pending(uid)
            return False

        if raw in (kb.BTN_BACK_USERS, kb.BTN_BACK_MAIN, kb.BTN_CANCEL_PENDING):
            self._clear_users_pending(uid)
            if raw == kb.BTN_BACK_MAIN or raw == kb.BTN_BACK_USERS:
                self._reply(message, "منوی اصلی:", kb.main_menu(user))
            else:
                self._reply(message, "مدیریت کاربران:", kb.users_menu())
            return True

        # --- add: pick role ---
        if mode == "add_role":
            role = kb.ROLE_BUTTON_TO_KEY.get(raw)
            if not role:
                self._reply(
                    message,
                    "لطفاً نقش را از دکمه‌ها انتخاب کنید.",
                    kb.role_menu(include_owner=require_owner(user)),
                )
                return True
            if role == "owner" and not require_owner(user):
                self._reply(message, "فقط مالک می‌تواند نقش مالک بدهد.", kb.users_menu())
                self._clear_users_pending(uid)
                return True
            # responsible_officer: no scope text — same invite path as other roles
            self._clear_users_pending(uid)
            self._create_and_send_invite(message, user, role, None)
            return True

        # Legacy: if an old pending add_scope somehow remains, invite without scope
        if mode == "add_scope":
            role = pending.get("role") or "responsible_officer"
            self._clear_users_pending(uid)
            self._create_and_send_invite(message, user, role, None)
            return True

        # --- edit: pick user id ---
        if mode == "edit_pick_user":
            target = self.db.get_user(raw)
            if not target or not target.get("active"):
                self._reply(
                    message,
                    "کاربر فعال با این شناسه یافت نشد. شناسه را دوباره بفرستید:",
                    kb.users_menu(),
                )
                return True
            if target["role"] == "owner" and not require_owner(user):
                self._reply(
                    message,
                    "فقط مالک می‌تواند نقش مالک را تغییر دهد.",
                    kb.users_menu(),
                )
                self._clear_users_pending(uid)
                return True
            self._users_pending[uid] = {
                "mode": "edit_pick_role",
                "target_id": str(target["bale_user_id"]),
            }
            self._reply(
                message,
                f"نقش جدید برای {target.get('display_name')} ({target['bale_user_id']}) را انتخاب کنید:",
                kb.role_menu(include_owner=require_owner(user)),
            )
            return True

        # --- edit: pick role ---
        if mode == "edit_pick_role":
            role = kb.ROLE_BUTTON_TO_KEY.get(raw)
            if not role:
                self._reply(
                    message,
                    "لطفاً نقش را از دکمه‌ها انتخاب کنید.",
                    kb.role_menu(include_owner=require_owner(user)),
                )
                return True
            if role == "owner" and not require_owner(user):
                self._reply(message, "فقط مالک می‌تواند نقش مالک بدهد.", kb.users_menu())
                self._clear_users_pending(uid)
                return True
            target_id = pending.get("target_id")
            target = self.db.get_user(target_id) if target_id else None
            if not target:
                self._clear_users_pending(uid)
                self._reply(message, "کاربر یافت نشد.", kb.users_menu())
                return True
            if (
                target["role"] == "owner"
                and role != "owner"
                and self.db.count_active_owners() <= 1
            ):
                self._clear_users_pending(uid)
                self._reply(message, "نمی‌توان نقش آخرین مالک را تغییر داد.", kb.users_menu())
                return True
            updated = self.db.set_role(target_id, role)
            self._clear_users_pending(uid)
            self._reply(
                message,
                f"✅ نقش به‌روز شد: {updated['bale_user_id']} → {role_label(updated['role'])}",
                kb.users_menu(),
            )
            return True

        # --- delete: pick user id ---
        if mode == "delete_pick_user":
            target_id = raw
            ok, err = self._can_deactivate_user(user, target_id)
            if not ok:
                self._reply(message, err, kb.users_menu())
                # stay in mode so they can retry unless fatal self/last-owner
                if "خودتان" in err or "آخرین مالک" in err:
                    self._clear_users_pending(uid)
                return True
            self.db.deactivate_user(target_id)
            self._clear_users_pending(uid)
            self._reply(
                message,
                f"✅ کاربر {target_id} غیرفعال شد.",
                kb.users_menu(),
            )
            return True

        self._clear_users_pending(uid)
        return False

    def _can_deactivate_user(self, actor: dict, target_id: str) -> tuple[bool, str]:
        tid = str(target_id).strip()
        if tid == str(actor["bale_user_id"]):
            return False, "نمی‌توانید خودتان را حذف کنید."
        target = self.db.get_user(tid)
        if not target or not target.get("active"):
            return False, "کاربر فعال با این شناسه یافت نشد."
        if target["role"] == "owner":
            if not require_owner(actor):
                return False, "فقط مالک می‌تواند مالک دیگر را حذف کند."
            if self.db.count_active_owners() <= 1:
                return False, "نمی‌توان آخرین مالک را حذف کرد."
        return True, ""

    def cmd_adduser(self, message: dict, args: list[str]) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user) or not require_manager(user):
            if not require_manager(user) and user.get("role") != "technician":
                self._reply(message, "فقط مالک یا مدیر می‌تواند کاربر اضافه کند.")
            return
        if len(args) < 2:
            self._reply(
                message,
                "فرمت:\n/adduser <bale_id> <owner|manager|responsible_officer|technician> [name...]",
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
        name_parts: list[str] = args[2:] if len(args) >= 3 else []
        display = " ".join(name_parts).strip() or target_id
        created = self.db.upsert_user(target_id, role=role, display_name=display, scope=scope)
        self._reply(
            message,
            f"کاربر ذخیره شد:\n{created['bale_user_id']} | {created['display_name']} | "
            f"{role_label(created['role'])} | حوزه={created.get('scope') or '—'}",
            kb.main_menu(user),
        )

    def cmd_setrole(self, message: dict, args: list[str]) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user) or not require_manager(user):
            if not require_manager(user) and user.get("role") != "technician":
                self._reply(message, "فقط مالک یا مدیر.")
            return
        if len(args) < 2 or args[1] not in ROLES:
            self._reply(message, "فرمت: /setrole <bale_id> <owner|manager|responsible_officer|technician>")
            return
        if args[1] == "owner" and not require_owner(user):
            self._reply(message, "فقط مالک می‌تواند نقش مالک بدهد.")
            return
        target = self.db.get_user(args[0])
        if not target:
            self._reply(message, "کاربر یافت نشد. اول /adduser استفاده کنید یا از لینک دعوت استفاده شود.")
            return
        if target["role"] == "owner" and not require_owner(user):
            self._reply(message, "فقط مالک می‌تواند نقش مالک را تغییر دهد.")
            return
        if (
            target["role"] == "owner"
            and args[1] != "owner"
            and self.db.count_active_owners() <= 1
        ):
            self._reply(message, "نمی‌توان نقش آخرین مالک را تغییر داد.")
            return
        try:
            updated = self.db.set_role(args[0], args[1])
        except KeyError:
            self._reply(message, "کاربر یافت نشد. اول /adduser استفاده کنید.")
            return
        self._reply(
            message,
            f"نقش به‌روز شد: {updated['bale_user_id']} → {role_label(updated['role'])}",
            kb.main_menu(user),
        )

    def cmd_setscope(self, message: dict, args: list[str]) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user) or not require_manager(user):
            if not require_manager(user) and user.get("role") != "technician":
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
            (
                f"حوزه به‌روز شد: {updated['bale_user_id']} → {updated.get('scope')}\n"
                "(توجه: حوزه برای کاردان مسئول دیگر در فیلتر داده استفاده نمی‌شود؛ ابزار قدیمی.)"
            ),
            kb.main_menu(user),
        )

    def cmd_reset(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(uid)
        self._analysis_tundish_filter.pop(uid, None)
        self._await_category_code.discard(uid)
        self._site_stock_pending.pop(uid, None)
        self._catalog_assign_pending.pop(uid, None)
        self._users_pending.pop(uid, None)
        self._bot_settings_pending.pop(uid, None)
        self._material_req_pending.pop(uid, None)
        self._warehouse_ret_pending.pop(uid, None)
        self.db.reset_session(user["bale_user_id"])
        self._reply(message, "جلسه آپلود و وضعیت ورود جاری پاک شد. از منو دوباره شروع کنید.", kb.main_menu(user))

    # ---------- request-driven flow ----------
    def on_pick_file_type(self, message: dict, file_type: str) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if user.get("role") == "technician":
            self._deny_technician(message, user)
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
        self._reply(message, "عملیات لغو شد.\n" + self._status_text(session), kb.main_menu(user))

    def on_status(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        session = self.db.get_or_create_session(user["bale_user_id"])
        self._reply(message, self._status_text(session), kb.main_menu(user))

    def on_document(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        session = self.db.get_or_create_session(user["bale_user_id"])
        pending = session.get("pending_file_type")
        if user.get("role") == "technician":
            if pending:
                self.db.set_pending_file_type(user["bale_user_id"], None)
            self._deny_technician(message, user)
            return
        if not pending:
            self._reply(
                message,
                "ابتدا از منو نوع فایل را انتخاب کنید، سپس Excel را بفرستید.",
                kb.main_menu(user),
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

        # Snapshot previous clean DF before extract overwrites the same clean_path
        slot_col = {
            "tank_consumption": "tank_path",
            "product_inventory": "inventory_path",
            "monthly_consumption": "monthly_path",
        }.get(pending)
        previous_clean = Path(session[slot_col]) if slot_col and session.get(slot_col) else None
        old_df = None
        prev_kept = 0
        if previous_clean is not None and previous_clean.exists():
            try:
                old_df = pd.read_excel(previous_clean, engine="openpyxl")
                prev_kept = int(len(old_df))
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not load previous clean for merge: %s", exc)
                old_df = None
                prev_kept = 0

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

        new_kept = int(result.kept_row_count)
        merge_note = ""
        if old_df is not None and not old_df.empty:
            try:
                new_df = pd.read_excel(result.clean_path, engine="openpyxl")
                merged = merge_clean_frames(old_df, new_df, pending)
                merged.to_excel(result.clean_path, index=False, engine="openpyxl")
                result.kept_row_count = int(len(merged))
                merge_note = (
                    f"\nهمسان‌سازی: قبلی {prev_kept} + جدید {new_kept} → نهایی {result.kept_row_count}."
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("merge clean frames failed: %s", exc)
                merge_note = f"\n(همسان‌سازی انجام نشد: {exc})"

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

        catalog_note = ""
        if pending == "product_inventory":
            try:
                counts = self.db.seed_catalog_from_inventory_extract(
                    result.clean_path, only_missing=True
                )
                catalog_note = (
                    f"\nکاتالوگ اقلام سایت: +{counts.get('inserted', 0)} قلم جدید "
                    f"(ردشده/موجود={counts.get('skipped', 0)})."
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("catalog seed after inventory failed: %s", exc)

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
            else kb.main_menu(user)
        )
        actor_line = f"ثبت‌کننده: {self._format_actor(user)}"
        self._reply(
            message,
            (
                f"✅ فایل «{label}» دریافت شد.\n"
                f"از {result.raw_row_count} ردیف خام، {new_kept} ردیف نگه داشته شد."
                f"{merge_note}{dropped_note}{extra_cols_note}{catalog_note}\n"
                f"نسخه تمیز ذخیره و در پایگاه‌داده ثبت شد.\n"
                f"{actor_line}\n\n"
            )
            + self._status_text(session),
            reply_menu,
        )

    def on_generate(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user):
            return
        session = self.db.get_or_create_session(user["bale_user_id"])
        completeness = self.db.session_completeness(session)
        ok, err = can_generate_report(user, session, completeness)
        if not ok:
            self._reply(message, err + "\n\n" + self._status_text(session), kb.main_menu(user))
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
                kb.main_menu(user),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("generate failed")
            self._reply(message, f"خطا در تولید گزارش: {exc}", kb.main_menu(user))

    # ---------- موجودی انبار submenu ----------
    def on_inventory_menu(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user):
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
        if self._deny_technician(message, user):
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
        if self._deny_technician(message, user):
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
        if self._deny_technician(message, user):
            return
        active_codes = self.db.list_category_codes(active_only=True)
        if not active_codes:
            self._reply(
                message,
                "هنوز هیچ کد دسته‌بندی فعالی ثبت نشده است.",
                kb.inventory_menu(),
            )
            return

        table, has_extract, source = self._category_inventory_table(user)
        if has_extract:
            notice = f"منبع: {source or 'آخرین موجودی انبار تمیزشده'}"
        elif source == "کاتالوگ همگام‌شده":
            notice = (
                "استخراج فعلی موجودی انبار در دسترس نیست؛ شرح کالا از کاتالوگ است "
                "و مقدار تا آپلود موجودی انبار قابل نمایش نیست."
            )
        else:
            notice = (
                "هنوز استخراج موجودی انبار ندارید. برای جدول کامل، ابتدا فایل «موجودی انبار» "
                "را آپلود کنید."
            )
        chunks = format_inventory_table_fa(table)
        prefix = "📋 لیست کد دسته‌بندی و موجودی\n" + notice + "\n\n"
        for index, chunk in enumerate(chunks):
            is_last = index == len(chunks) - 1
            self._reply(message, (prefix if index == 0 else "") + chunk,
                        kb.inventory_menu() if is_last else None)

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
        rem = remaining(self._inventory_with_ledger(inv))
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
        if self._deny_technician(message, user):
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
        if self._deny_technician(message, user):
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
        if self._deny_technician(message, user):
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
        rem, rem_source = self._resolve_remaining(frames)
        crit = critical_materials(rates, rem, CRITICAL_DAYS)
        lines = [f"📦 موجودی باقیمانده ({rem_source}):", ""]
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
        rem = remaining(self._inventory_with_ledger(frames.get("product_inventory")))
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
            "  از YYYY/MM/DD تا YYYY/MM/DD (هجری شمسی)\n"
            "مثال: از 1403/07/01 تا 1403/07/15"
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
        if self._deny_technician(message, user):
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
                "لطفاً بازه را با این فرمت بفرستید (هجری شمسی):\nاز YYYY/MM/DD تا YYYY/MM/DD",
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
                f"📅 مصرف مواد از {format_date(start)} تا {format_date(end)} ({days} روز):",
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
                f"({format_date(start)} تا {format_date(end)}):",
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
            sug = suggest_requests(use_rates, remaining(self._inventory_with_ledger(inv)), days)
            lines = [
                f"🛒 پیشنهاد درخواست مواد برای {days} روز "
                f"({format_date(start)} تا {format_date(end)}):",
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
        if self._deny_technician(message, user):
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



    def _resolve_monthly_source(self, user: dict) -> tuple[Path | None, str | None]:
        """Prefer raw monthly upload (plant detail); else cleaned session/latest."""
        uid = str(user["bale_user_id"])
        session = self.db.get_or_create_session(uid)
        latest = self.db.get_latest_extracted(uid, "monthly_consumption")
        candidates: list[tuple[Path, str]] = []
        if latest:
            raw = latest.get("raw_path")
            clean = latest.get("clean_path")
            if raw:
                candidates.append((Path(str(raw)), "خام آخرین آپلود مصرف ماهیانه"))
            if clean:
                candidates.append((Path(str(clean)), "نسخه تمیز آخرین استخراج"))
        if session.get("monthly_path"):
            candidates.append((Path(str(session["monthly_path"])), "مصرف ماهیانه جلسه جاری"))
        seen: set[str] = set()
        for path_obj, label in candidates:
            key = str(path_obj.resolve()) if path_obj.exists() else str(path_obj)
            if key in seen:
                continue
            seen.add(key)
            if path_obj.exists():
                return path_obj, label
        return None, None

    def on_monthly_summary(self, message: dict) -> None:
        """Build خلاصه مصرفی ماهیانه as PDF (+ Excel) and send document."""
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user):
            return
        source, source_label = self._resolve_monthly_source(user)
        if source is None:
            self._reply(
                message,
                "فایل مصرف ماهیانه مواد یافت نشد.\n"
                "ابتدا از منوی اصلی «مصرف ماهیانه مواد» را آپلود کنید.",
                kb.analytics_menu(),
            )
            return
        self._reply(message, "در حال ساخت خلاصه مصرف ماهیانه (PDF)…")
        try:
            from config import REPORT_DIR

            out_dir = REPORT_DIR
            excel_path = out_dir / SUMMARY_FILE_NAME
            data, excel_path = build_monthly_summary(source, excel_out=excel_path)
            sections = summary_sections_for_pdf(data)
            pdf_path = out_dir / "خلاصه مصرفی ماهیانه.pdf"
            generate_monthly_summary_pdf(
                sections,
                grand_kg=data.grand_kg,
                output_path=pdf_path,
            )
            month_sum = sum(float(s.get("total_kg") or 0) for s in data.month_sections)
            caption = (
                f"خلاصه مصرفی ماهیانه\n"
                f"منبع: {source_label}\n"
                f"جمع کل مصرفی: {data.grand_kg:g} کیلوگرم"
            )
            self.client.send_document(
                self._chat_id(message),
                pdf_path,
                caption=caption,
            )
            # Also send Excel companion when available
            try:
                self.client.send_document(
                    self._chat_id(message),
                    excel_path,
                    caption="فایل اکسل «خلاصه مصرفی ماهیانه»",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("monthly summary excel send failed: %s", exc)
            self._reply(
                message,
                (
                    f"✅ خلاصه مصرف ماهیانه ارسال شد.\n"
                    f"ردیف‌های تجمیعی: {len(data.items)} | "
                    f"جمع ماه‌ها: {month_sum:g} | جمع کل: {data.grand_kg:g} کیلوگرم"
                ),
                kb.analytics_menu(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("monthly summary failed")
            self._reply(
                message,
                f"خطا در تولید خلاصه مصرف ماهیانه: {exc}",
                kb.analytics_menu(),
            )

    def _resolve_remaining(self, frames: dict) -> tuple[Any, str]:
        """Prefer today's (or latest) site_stock_entries; else warehouse inventory."""
        import pandas as pd

        rows = self.db.site_stock_as_remaining_rows()
        if rows:
            day = self.db.get_latest_site_stock_date() or "—"
            rem = remaining(pd.DataFrame(rows))
            return rem, f"موجودی روزانه سایت — {format_date(day)}"
        rem = remaining(self._inventory_with_ledger(frames.get("product_inventory")))
        return rem, "موجودی انبار"

    def _clear_site_stock_pending(self, uid: str) -> None:
        self._site_stock_pending.pop(str(uid), None)

    def _clear_catalog_pending(self, uid: str) -> None:
        self._catalog_assign_pending.pop(str(uid), None)

    # ---------- موجودی روزانه سایت (interactive) ----------
    def on_site_stock_menu(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(uid)
        self._await_category_code.discard(uid)
        self._clear_site_stock_pending(uid)
        self._clear_catalog_pending(uid)
        self.db.set_pending_file_type(user["bale_user_id"], None)
        day = self.db.tehran_today()
        actor_note = ""
        today_entries = self.db.list_site_stock_entries(entry_date=day)
        if today_entries:
            last = max(today_entries, key=lambda e: e.get("created_at") or "")
            actor_note = (
                "\nآخرین ثبت‌کننده امروز: "
                + self._format_actor(
                    None,
                    bale_user_id=last.get("bale_user_id"),
                    actor_display_name=last.get("actor_display_name"),
                )
            )
        self._reply(
            message,
            "موجودی روزانه سایت\n"
            "یکی از گروه‌های زیر را انتخاب کنید؛ سپس مقادیر اقلام را یکی‌یکی بفرستید.\n"
            f"تاریخ ورود (تهران): {format_date(day)}"
            f"{actor_note}",
            kb.site_stock_menu(),
        )

    def on_site_stock_group(self, message: dict, group_key: str) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        group_key = (group_key or "").strip().lower()
        label = SITE_STOCK_GROUPS.get(group_key)
        if not label:
            self._reply(message, "گروه نامعتبر.", kb.site_stock_menu())
            return
        items = self.db.list_items_for_group(group_key, active_only=True)
        if not items:
            self._reply(
                message,
                f"هیچ قلمی به «{label}» تخصیص داده نشده است.\n"
                + (
                    "از منوی «تنظیمات اقلام سایت / تخصیص به گروه» اقلام را تخصیص دهید."
                    if can_configure_catalog(user)
                    else "با مدیر یا کاردان مسئول برای تخصیص اقلام تماس بگیرید."
                ),
                kb.site_stock_menu(),
            )
            return
        uid = str(user["bale_user_id"])
        self._site_stock_pending[uid] = {
            "group": group_key,
            "items": items,
            "index": 0,
            "values": {},  # item_id -> quantity
        }
        lines = [f"📋 {label} — اقلام تخصیص‌یافته ({len(items)}):", ""]
        lines.append("نام و شرح کالا")
        lines.append("─" * 12)
        for i, it in enumerate(items, 1):
            lines.append(f"{i}. {it.get('name_desc') or it['id']}")
        lines.append("")
        lines.append("حالا مقدار هر قلم را به‌صورت عدد بفرستید.")
        self._reply(message, "\n".join(lines), kb.site_stock_entry_menu())
        self._prompt_site_stock_item(message, user)

    def _prompt_site_stock_item(self, message: dict, user: dict) -> None:
        uid = str(user["bale_user_id"])
        pending = self._site_stock_pending.get(uid)
        if not pending:
            return
        items = pending["items"]
        idx = pending["index"]
        if idx >= len(items):
            self._finish_site_stock_entry(message, user)
            return
        item = items[idx]
        label = SITE_STOCK_GROUPS.get(pending["group"], pending["group"])
        self._reply(
            message,
            f"قلم {idx + 1} از {len(items)} — {label}\n"
            f"نام: {item.get('name_desc') or item['id']}\n"
            f"شناسه: {item['id']}\n"
            "مقدار عددی را بفرستید "
            f"(یا «{kb.BTN_SITE_SKIP}» برای رد کردن).",
            kb.site_stock_entry_menu(),
        )

    def on_site_stock_quantity_text(self, message: dict, text: str) -> bool:
        """Consume numeric quantity while site-stock entry is pending."""
        uid = str(self._uid(message))
        pending = self._site_stock_pending.get(uid)
        if not pending:
            return False
        user = self._user_or_deny(message)
        if not user:
            self._clear_site_stock_pending(uid)
            return True
        raw = (text or "").strip().replace(",", "٫").replace("٫", ".")
        # Persian digits → English
        trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        raw = raw.translate(trans)
        try:
            qty = float(raw)
        except ValueError:
            self._reply(
                message,
                "لطفاً فقط یک عدد بفرستید (مثال: 12 یا 3.5).",
                kb.site_stock_entry_menu(),
            )
            return True
        items = pending["items"]
        idx = pending["index"]
        if idx >= len(items):
            self._finish_site_stock_entry(message, user)
            return True
        item = items[idx]
        try:
            self.db.upsert_site_stock_entry(
                bale_user_id=user["bale_user_id"],
                tundish_group=pending["group"],
                item_id=item["id"],
                quantity=qty,
                item_name_snapshot=item.get("name_desc"),
                actor_display_name=user.get("display_name"),
            )
        except (ValueError, KeyError) as exc:
            self._reply(message, f"خطا در ذخیره: {exc}", kb.site_stock_entry_menu())
            return True
        pending["values"][item["id"]] = qty
        pending["index"] = idx + 1
        if pending["index"] >= len(items):
            self._finish_site_stock_entry(message, user)
        else:
            self._prompt_site_stock_item(message, user)
        return True

    def on_site_stock_skip(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        pending = self._site_stock_pending.get(uid)
        if not pending:
            self._reply(message, "ورود موجودی فعالی نیست.", kb.site_stock_menu())
            return
        pending["index"] = pending["index"] + 1
        if pending["index"] >= len(pending["items"]):
            self._finish_site_stock_entry(message, user)
        else:
            self._prompt_site_stock_item(message, user)

    def on_site_stock_cancel(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        self._clear_site_stock_pending(str(user["bale_user_id"]))
        self._reply(message, "ورود موجودی لغو شد.", kb.site_stock_menu())

    def _finish_site_stock_entry(self, message: dict, user: dict) -> None:
        uid = str(user["bale_user_id"])
        pending = self._site_stock_pending.pop(uid, None)
        if not pending:
            self._reply(message, "ورود تمام شد.", kb.site_stock_menu())
            return
        group = pending["group"]
        label = SITE_STOCK_GROUPS.get(group, group)
        day = self.db.tehran_today()
        values = pending.get("values") or {}
        lines = [
            f"✅ ثبت موجودی «{label}» برای تاریخ {format_date(day)}",
            f"تعداد اقلام ثبت‌شده: {len(values)} از {len(pending['items'])}",
            "",
        ]
        if values:
            id_to_name = {it["id"]: it.get("name_desc") or it["id"] for it in pending["items"]}
            for iid, qty in values.items():
                lines.append(f"• {id_to_name.get(iid, iid)}: {qty:g}")
        else:
            lines.append("(هیچ مقداری ثبت نشد)")
        lines.append("")
        lines.append(f"ثبت‌کننده: {self._format_actor(user)}")
        lines.append("داده‌ها در پایگاه‌داده ذخیره شدند.")
        self._reply(message, "\n".join(lines), kb.site_stock_menu())

    # ---------- تنظیمات اقلام سایت / تخصیص ----------
    def _deny_catalog_settings(self, message: dict, user: dict) -> bool:
        if can_configure_catalog(user):
            return False
        if user.get("role") == "technician":
            self._deny_technician(message, user)
        else:
            self._reply(
                message,
                "دسترسی تنظیمات اقلام سایت فقط برای مالک، مدیر و کاردان مسئول است.",
                kb.main_menu(user),
            )
        return True

    def on_catalog_settings_menu(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_catalog_settings(message, user):
            return
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(uid)
        self._clear_site_stock_pending(uid)
        self._clear_catalog_pending(uid)
        self._await_category_code.discard(uid)
        total = len(self.db.list_catalog_items(active_only=True))
        unassigned = len(self.db.list_unassigned_catalog_items(active_only=True))
        self._reply(
            message,
            "⚙️ تنظیمات اقلام سایت / تخصیص به گروه\n"
            f"اقلام فعال کاتالوگ: {total} | بدون گروه: {unassigned}\n"
            "ابتدا در صورت نیاز از موجودی انبار همگام‌سازی کنید، سپس اقلام را به اسلب/بلوم/بیلت تخصیص دهید.",
            kb.catalog_settings_menu(),
        )

    def on_catalog_seed(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_catalog_settings(message, user):
            return
        result = self.db.seed_catalog_from_latest_warehouse()
        if not result.get("ok"):
            self._reply(
                message,
                result.get("error")
                or "همگام‌سازی ناموفق. ابتدا فایل موجودی انبار را آپلود کنید.",
                kb.catalog_settings_menu(),
            )
            return
        counts = result["counts"]
        extract = result["extract"]
        self._reply(
            message,
            "✅ همگام‌سازی کاتالوگ از آخرین موجودی انبار انجام شد.\n"
            f"ردیف‌های فایل: {counts.get('total_rows', 0)}\n"
            f"افزوده: {counts.get('inserted', 0)} | به‌روز: {counts.get('updated', 0)} | "
            f"ردشده/موجود: {counts.get('skipped', 0)}\n"
            f"منبع: extract#{extract.get('id')} ({extract.get('row_count')} ردیف تمیز)",
            kb.catalog_settings_menu(),
        )

    def on_catalog_list(self, message: dict, unassigned_only: bool = False) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_catalog_settings(message, user):
            return
        if unassigned_only:
            rows = self.db.list_unassigned_catalog_items(active_only=True)
            title = "📭 اقلام بدون گروه"
        else:
            rows = self.db.list_catalog_with_assignments(active_only=True)
            title = "📋 لیست اقلام و تخصیص‌ها"
        if not rows:
            self._reply(
                message,
                title + "\nلیست خالی است. ابتدا همگام‌سازی از موجودی انبار را بزنید.",
                kb.catalog_settings_menu(),
            )
            return
        # Show up to 40; instruct to pick by id or number
        uid = str(user["bale_user_id"])
        self._catalog_assign_pending[uid] = {
            "mode": "pick",
            "rows": rows[:80],
            "unassigned_only": unassigned_only,
        }
        inventory_frame, _, _ = self._load_latest_inventory_frame(
            user, include_catalog_fallback=False
        )
        inventory_by_id: dict[str, dict] = {}
        if inventory_frame is not None and not inventory_frame.empty:
            for _, inv_row in inventory_frame.iterrows():
                item_id = self._inventory_cell(inv_row.get("id"), "")
                if item_id:
                    inventory_by_id[item_id] = inv_row.to_dict()
        lines = [title, "برای تخصیص، شماره یا شناسه قلم را بفرستید.", ""]
        lines.append("کد دسته | شرح کالا | موجودی | گروه")
        lines.append("───────── | ───────────── | ─────── | ────")
        for i, r in enumerate(rows[:40], 1):
            group = r.get("tundish_group")
            g_label = SITE_STOCK_GROUPS.get(group, "—") if group else "—"
            inv = inventory_by_id.get(str(r["id"]).strip(), {})
            category = self._inventory_cell(
                inv.get("category_code"), self._inventory_cell(r.get("category_code"))
            )
            quantity = self._inventory_cell(inv.get("quantity"))
            desc = self._inventory_cell(r.get("name_desc"), str(r["id"]))
            lines.append(f"{i}. [{r['id']}] {category} | {desc} | {quantity} | {g_label}")
        if len(rows) > 40:
            lines.append(f"\n… و {len(rows) - 40} قلم دیگر (با شناسه دقیق بفرستید).")
        self._reply(message, "\n".join(lines), kb.catalog_settings_menu())

    def on_catalog_pick_text(self, message: dict, text: str) -> bool:
        """While awaiting item pick for assignment."""
        uid = str(self._uid(message))
        pending = self._catalog_assign_pending.get(uid)
        if not pending or pending.get("mode") != "pick":
            return False
        # Ignore menu buttons — let dispatcher handle them
        menu_buttons = {
            kb.BTN_CATALOG_SETTINGS,
            kb.BTN_CATALOG_LIST,
            kb.BTN_CATALOG_UNASSIGNED,
            kb.BTN_CATALOG_SEED,
            kb.BTN_BACK_MAIN,
            kb.BTN_BACK_CATALOG,
            kb.BTN_SITE_STOCK,
            kb.BTN_HELP,
            kb.BTN_CANCEL_PENDING,
        }
        if text in menu_buttons or text in kb.SITE_GROUP_BUTTONS or text in kb.ASSIGN_GROUP_BUTTONS:
            return False
        user = self._user_or_deny(message)
        if not user:
            self._clear_catalog_pending(uid)
            return True
        if self._deny_catalog_settings(message, user):
            return True
        rows = pending.get("rows") or []
        raw = (text or "").strip()
        trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        raw_n = raw.translate(trans)
        chosen = None
        if raw_n.isdigit():
            n = int(raw_n)
            if 1 <= n <= len(rows):
                chosen = rows[n - 1]
        if chosen is None:
            for r in rows:
                if str(r["id"]).strip() == raw:
                    chosen = r
                    break
        if chosen is None:
            # try full catalog by id
            item = self.db.get_catalog_item(raw)
            if item and item.get("active"):
                chosen = item
        if chosen is None:
            self._reply(
                message,
                "قلم یافت نشد. شماره لیست یا شناسه دقیق را بفرستید.",
                kb.catalog_settings_menu(),
            )
            return True
        item_id = chosen["id"]
        self._catalog_assign_pending[uid] = {"mode": "assign", "item_id": item_id}
        current = self.db.get_item_assignment(item_id)
        cur_label = (
            SITE_STOCK_GROUPS.get(current["tundish_group"], current["tundish_group"])
            if current
            else "—"
        )
        name = chosen.get("name_desc") or item_id
        self._reply(
            message,
            f"قلم انتخاب شد:\n[{item_id}] {name}\nتخصیص فعلی: {cur_label}\n"
            "گروه مقصد را انتخاب کنید:",
            kb.catalog_assign_menu(),
        )
        return True

    def on_catalog_assign_group(self, message: dict, group_key: str | None) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_catalog_settings(message, user):
            return
        uid = str(user["bale_user_id"])
        pending = self._catalog_assign_pending.get(uid)
        if not pending or pending.get("mode") != "assign":
            self._reply(
                message,
                "ابتدا از لیست یک قلم را انتخاب کنید.",
                kb.catalog_settings_menu(),
            )
            return
        item_id = pending["item_id"]
        try:
            if group_key is None:
                self.db.unassign_item(item_id)
                msg = f"تخصیص قلم [{item_id}] حذف شد."
            else:
                row = self.db.assign_item_to_group(
                    item_id, group_key, assigned_by=user["bale_user_id"]
                )
                label = SITE_STOCK_GROUPS.get(row["tundish_group"], row["tundish_group"])
                msg = f"✅ قلم [{item_id}] به «{label}» تخصیص داده شد."
        except (ValueError, KeyError) as exc:
            self._reply(message, str(exc), kb.catalog_settings_menu())
            self._clear_catalog_pending(uid)
            return
        self._clear_catalog_pending(uid)
        self._reply(message, msg, kb.catalog_settings_menu())


    # ---------- bot settings (owner/manager) ----------
    def _clear_bot_settings_pending(self, uid: str) -> None:
        self._bot_settings_pending.pop(str(uid), None)

    def _require_bot_settings_user(self, message: dict) -> dict | None:
        """Owner + manager only (same gate as کاربران)."""
        user = self._user_or_deny(message)
        if not user:
            return None
        if self._deny_technician(message, user):
            return None
        if not require_manager(user):
            self._reply(
                message,
                "فقط مالک یا مدیر به بخش تنظیمات ربات دسترسی دارد.",
                kb.main_menu(user),
            )
            return None
        return user

    def _settings_item_menu(self, which: str) -> dict:
        return kb.bot_settings_item_menu(include_text=(which != "logo"))

    def _image_status_line(self, key: str) -> str:
        raw = self.db.get_setting(key)
        if raw and Path(raw).is_file():
            return f"تصویر: ✅ تنظیم شده ({Path(raw).name})"
        return "تصویر: ❌ تنظیم نشده"

    def on_bot_settings_menu(self, message: dict) -> None:
        user = self._require_bot_settings_user(message)
        if not user:
            return
        self._clear_bot_settings_pending(str(user["bale_user_id"]))
        self._reply(
            message,
            "تنظیمات ربات — یک بخش را انتخاب کنید:",
            kb.bot_settings_menu(),
        )

    def on_bot_settings_section(self, message: dict, which: str) -> None:
        user = self._require_bot_settings_user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._bot_settings_pending[uid] = {"mode": "menu", "which": which}
        titles = {
            "invite": "متن دعوت‌نامه کاربران",
            "welcome": "پیام خوشامدگویی",
            "logo": "لوگوی ربات",
        }
        hint = ""
        if which == "invite":
            hint = "\n\n" + PLACEHOLDER_HINT_INVITE
        elif which == "welcome":
            hint = "\n\n" + PLACEHOLDER_HINT_WELCOME
        self._reply(
            message,
            f"بخش «{titles.get(which, which)}» — یک گزینه را انتخاب کنید:{hint}",
            self._settings_item_menu(which),
        )

    def _preview_invite(self, message: dict, user: dict) -> None:
        sample_role = "technician"
        text = self._invite_message_text(sample_role)
        img = self.db.get_setting("invite_image_path")
        status = self._image_status_line("invite_image_path")
        header = (
            "پیش‌نمایش دعوت‌نامه (نمونه نقش تکنسین):\n"
            f"{status}\n"
            "────────\n"
        )
        markup = self._settings_item_menu("invite")
        body = header + text
        if img and Path(img).is_file():
            try:
                self.client.send_photo(
                    self._chat_id(message), Path(img), caption=body, reply_markup=markup
                )
                return
            except Exception:  # noqa: BLE001
                logger.exception("preview invite photo failed")
        self._reply(message, body, markup)

    def _preview_welcome(self, message: dict, user: dict) -> None:
        text = self._welcome_text(user)
        status_w = self._image_status_line("welcome_image_path")
        status_l = self._image_status_line("logo_path")
        header = (
            "پیش‌نمایش خوشامدگویی (با نقش شما):\n"
            f"{status_w}\n"
            f"لوگو: {status_l.split(':', 1)[-1].strip()}\n"
            "────────\n"
        )
        markup = self._settings_item_menu("welcome")
        body = header + text
        photo = self._branding_photo_path(prefer_welcome=True)
        if photo is not None:
            try:
                self.client.send_photo(
                    self._chat_id(message), photo, caption=body, reply_markup=markup
                )
                return
            except Exception:  # noqa: BLE001
                logger.exception("preview welcome photo failed")
        self._reply(message, body, markup)

    def _preview_logo(self, message: dict, user: dict) -> None:
        markup = self._settings_item_menu("logo")
        raw = self.db.get_setting("logo_path")
        if raw and Path(raw).is_file():
            try:
                self.client.send_photo(
                    self._chat_id(message),
                    Path(raw),
                    caption="لوگوی فعلی ربات:",
                    reply_markup=markup,
                )
                return
            except Exception:  # noqa: BLE001
                logger.exception("preview logo failed")
                self._reply(message, f"لوگو ذخیره شده ولی ارسال نشد:\n{raw}", markup)
                return
        self._reply(message, "هنوز لوگو تنظیم نشده.", markup)

    def on_bot_settings_view(self, message: dict) -> None:
        user = self._require_bot_settings_user(message)
        if not user:
            return
        pending = self._bot_settings_pending.get(str(user["bale_user_id"])) or {}
        which = pending.get("which")
        if which == "invite":
            self._preview_invite(message, user)
        elif which == "welcome":
            self._preview_welcome(message, user)
        elif which == "logo":
            self._preview_logo(message, user)
        else:
            self._reply(message, "ابتدا یک بخش تنظیمات را انتخاب کنید.", kb.bot_settings_menu())

    def on_bot_settings_edit_text_start(self, message: dict) -> None:
        user = self._require_bot_settings_user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        pending = self._bot_settings_pending.get(uid) or {}
        which = pending.get("which")
        if which not in {"invite", "welcome"}:
            self._reply(message, "این بخش متن قابل ویرایش ندارد.", kb.bot_settings_menu())
            return
        self._bot_settings_pending[uid] = {"mode": "await_text", "which": which}
        if which == "invite":
            current = self.db.get_setting("invite_text") or DEFAULT_INVITE_TEXT
            hint = PLACEHOLDER_HINT_INVITE
        else:
            current = self.db.get_setting("welcome_text") or "(پیش‌فرض داخلی نقش‌محور)"
            hint = PLACEHOLDER_HINT_WELCOME
        self._reply(
            message,
            f"{hint}\n\nمتن فعلی:\n────────\n{current}\n────────\n"
            "متن جدید را بفرستید (یا برای بازگشت از منو استفاده کنید):",
            self._settings_item_menu(which),
        )

    def on_bot_settings_set_image_start(self, message: dict) -> None:
        user = self._require_bot_settings_user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        pending = self._bot_settings_pending.get(uid) or {}
        which = pending.get("which")
        if which not in {"invite", "welcome", "logo"}:
            self._reply(message, "ابتدا یک بخش تنظیمات را انتخاب کنید.", kb.bot_settings_menu())
            return
        self._bot_settings_pending[uid] = {"mode": "await_image", "which": which}
        self._reply(
            message,
            "لطفاً تصویر را همین حالا به‌صورت Photo ارسال کنید.\n"
            "(ارسال به‌صورت Document تصویر هم پذیرفته می‌شود.)",
            self._settings_item_menu(which),
        )

    def on_bot_settings_clear_image(self, message: dict) -> None:
        user = self._require_bot_settings_user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        pending = self._bot_settings_pending.get(uid) or {}
        which = pending.get("which")
        key_map = {
            "invite": "invite_image_path",
            "welcome": "welcome_image_path",
            "logo": "logo_path",
        }
        key = key_map.get(which or "")
        if not key:
            self._reply(message, "ابتدا یک بخش تنظیمات را انتخاب کنید.", kb.bot_settings_menu())
            return
        old = self.db.get_setting(key)
        self.db.clear_setting(key, updated_by=uid)
        if old:
            try:
                Path(old).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                logger.warning("could not delete old asset %s", old)
        self._bot_settings_pending[uid] = {"mode": "menu", "which": which}
        self._reply(message, "✅ تصویر حذف شد.", self._settings_item_menu(which))

    def _save_bot_asset(self, file_id: str, which: str, uid: str) -> Path:
        BOT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        # Prefer jpeg extension; Bale may serve jpeg regardless of original
        dest = BOT_ASSETS_DIR / f"{which}_{uid}.jpg"
        # rotate previous path if different
        key_map = {
            "invite": "invite_image_path",
            "welcome": "welcome_image_path",
            "logo": "logo_path",
        }
        old = self.db.get_setting(key_map[which])
        saved = self.client.download_file(file_id, dest)
        if old and Path(old).resolve() != Path(saved).resolve():
            try:
                Path(old).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        self.db.set_setting(key_map[which], str(saved), updated_by=uid)
        return Path(saved)

    @staticmethod
    def _extract_image_file_id(message: dict) -> str | None:
        photos = message.get("photo") or []
        if isinstance(photos, list) and photos:
            best = photos[-1] if isinstance(photos[-1], dict) else None
            if best and best.get("file_id"):
                return str(best["file_id"])
        doc = message.get("document") or {}
        mime = str(doc.get("mime_type") or "")
        if mime.startswith("image/") and doc.get("file_id"):
            return str(doc["file_id"])
        return None

    def on_bot_settings_photo(self, message: dict) -> bool:
        """Handle inbound photo while awaiting image. Returns True if consumed."""
        uid = str(self._uid(message))
        pending = self._bot_settings_pending.get(uid)
        if not pending or pending.get("mode") != "await_image":
            return False
        user = self._require_bot_settings_user(message)
        if not user:
            self._clear_bot_settings_pending(uid)
            return True
        which = pending.get("which")
        if which not in {"invite", "welcome", "logo"}:
            self._clear_bot_settings_pending(uid)
            return True
        file_id = self._extract_image_file_id(message)
        if not file_id:
            self._reply(
                message,
                "تصویر معتبر دریافت نشد. یک Photo بفرستید.",
                self._settings_item_menu(which),
            )
            return True
        try:
            path = self._save_bot_asset(file_id, which, uid)
        except Exception as exc:  # noqa: BLE001
            logger.exception("save bot asset failed")
            self._reply(
                message,
                f"ذخیره تصویر ناموفق بود: {exc}",
                self._settings_item_menu(which),
            )
            return True
        extra = ""
        if which == "logo":
            ok, detail = self.client.try_set_my_photo(path)
            if ok:
                extra = "\nعکس پروفایل بازو هم به‌روز شد."
            else:
                extra = (
                    "\n(API عکس پروفایل بازو در بله پشتیبانی نشد؛ "
                    "لوگو به‌عنوان تصویر برندینگ ذخیره شد و در خوشامدگویی نمایش داده می‌شود.)"
                )
                logger.info("set bot profile photo fallback: %s", detail)
        self._bot_settings_pending[uid] = {"mode": "menu", "which": which}
        self._reply(
            message,
            f"✅ تصویر ذخیره شد: {path.name}{extra}",
            self._settings_item_menu(which),
        )
        return True

    def on_bot_settings_flow_text(self, message: dict, text: str) -> bool:
        """Handle pending bot-settings text. Returns True if consumed."""
        uid = str(self._uid(message))
        pending = self._bot_settings_pending.get(uid)
        if not pending:
            return False
        user = self._require_bot_settings_user(message)
        if not user:
            self._clear_bot_settings_pending(uid)
            return True

        raw = (text or "").strip()
        nav = {
            kb.BTN_BOT_SETTINGS,
            kb.BTN_SET_INVITE,
            kb.BTN_SET_WELCOME,
            kb.BTN_SET_LOGO,
            kb.BTN_SETTINGS_VIEW,
            kb.BTN_SETTINGS_EDIT_TEXT,
            kb.BTN_SETTINGS_SET_IMAGE,
            kb.BTN_SETTINGS_CLEAR_IMAGE,
            kb.BTN_BACK_BOT_SETTINGS,
            kb.BTN_BACK_MAIN,
            kb.BTN_USERS,
            kb.BTN_HELP,
        }
        if raw in nav:
            # Let dispatcher handle navigation buttons; clear await_text if leaving.
            if pending.get("mode") == "await_text" and raw not in {
                kb.BTN_SETTINGS_VIEW,
                kb.BTN_SETTINGS_EDIT_TEXT,
                kb.BTN_SETTINGS_SET_IMAGE,
                kb.BTN_SETTINGS_CLEAR_IMAGE,
                kb.BTN_BACK_BOT_SETTINGS,
            }:
                # keep which for item buttons; clear only on leaving section
                pass
            if raw in {
                kb.BTN_BOT_SETTINGS,
                kb.BTN_SET_INVITE,
                kb.BTN_SET_WELCOME,
                kb.BTN_SET_LOGO,
                kb.BTN_BACK_BOT_SETTINGS,
                kb.BTN_BACK_MAIN,
                kb.BTN_USERS,
                kb.BTN_HELP,
            }:
                # navigation handled by dispatcher; don't consume await for item actions
                if raw in {kb.BTN_BACK_MAIN, kb.BTN_BOT_SETTINGS, kb.BTN_BACK_BOT_SETTINGS}:
                    return False
                return False
            return False

        if pending.get("mode") != "await_text":
            return False

        which = pending.get("which")
        if which == "invite":
            self.db.set_setting("invite_text", raw, updated_by=uid)
            self._bot_settings_pending[uid] = {"mode": "menu", "which": "invite"}
            self._reply(message, "✅ متن دعوت‌نامه ذخیره شد.", self._settings_item_menu("invite"))
            return True
        if which == "welcome":
            self.db.set_setting("welcome_text", raw, updated_by=uid)
            self._bot_settings_pending[uid] = {"mode": "menu", "which": "welcome"}
            self._reply(message, "✅ متن خوشامدگویی ذخیره شد.", self._settings_item_menu("welcome"))
            return True

        self._clear_bot_settings_pending(uid)
        return False



    # ---------- material request (درخواست مواد) ----------

    def _require_material_request_access(self, message: dict) -> dict | None:
        user = self._user_or_deny(message)
        if not user:
            return None
        if self._deny_technician(message, user):
            return None
        if not can_request_materials(user):
            self._reply(
                message,
                "دسترسی ندارید؛ درخواست مواد فقط برای مالک، مدیر و کاردان مسئول است.",
                kb.main_menu(user),
            )
            return None
        return user

    def _format_mr_review(self, days: float | int, lines: list[dict]) -> str:
        header = [
            f"🛒 پیشنهاد درخواست مواد برای پوشش {int(days) if float(days) == int(days) else days} روز:",
            "",
        ]
        if not lines:
            header.append("پیشنهادی نیست — موجودی برای بازه درخواست کافی به‌نظر می‌رسد.")
            return "\n".join(header)
        for i, ln in enumerate(lines, 1):
            unit = ln.get("unit") or ""
            iid = ln.get("item_id") or "—"
            header.append(
                f"{i}) {ln.get('item_name')} (شناسه: {iid})\n"
                f"   موجودی: {float(ln.get('remaining_qty') or 0):.2f} {unit} | "
                f"مصرف روز: {float(ln.get('avg_daily') or 0):.2f} | "
                f"پیشنهاد: {float(ln.get('quantity') or 0):.2f} {unit}".rstrip()
            )
        header.append("")
        header.append("تأیید همه / اصلاح / انصراف را انتخاب کنید.")
        return "\n".join(header)

    def _build_mr_lines(
        self, user: dict, days: float | int
    ) -> tuple[list[dict], str | None]:
        """Build draft request lines from suggest_requests; error message or None."""
        session = self.db.get_or_create_session(user["bale_user_id"])
        completeness = self.db.session_completeness(session)
        missing = missing_files_for_goal("suggest", completeness)
        if missing:
            return [], (
                "برای درخواست مواد این فایل(ها) لازم است:\n• "
                + "\n• ".join(missing)
                + "\n\nابتدا از منوی اصلی نوع فایل را انتخاب و Excel را ارسال کنید."
            )
        frames, _metas = self._load_frames(user, session)
        frames = self._apply_tundish_filter(frames, str(user["bale_user_id"]))
        tank = frames.get("tank_consumption")
        monthly = frames.get("monthly_consumption")
        inv = self._inventory_with_ledger(frames.get("product_inventory"))
        end = date.today()
        start = end - timedelta(days=max(0, int(days) - 1))
        rates = daily_rates(tank, monthly)
        rates_r = daily_rates(tank, monthly, start=start, end=end)
        use_rates = rates_r if rates_r is not None and not rates_r.empty else rates
        rem = remaining(inv)
        sug = suggest_requests(use_rates, rem, days)
        if sug is None or sug.empty:
            return [], None
        positive = sug.loc[sug["suggest_qty"] > 0].copy()
        if positive.empty:
            return [], None

        # Map material_name → id from inventory
        id_by_name: dict[str, str] = {}
        if inv is not None and not inv.empty:
            name_col = None
            for candidate in ("product_name", "item_code_desc", "material_name"):
                if candidate in inv.columns:
                    name_col = candidate
                    break
            if name_col and "id" in inv.columns:
                for _, row in inv.iterrows():
                    nm = row.get(name_col)
                    iid = row.get("id")
                    if nm is None or iid is None:
                        continue
                    if isinstance(nm, float) and pd.isna(nm):
                        continue
                    if isinstance(iid, float) and pd.isna(iid):
                        continue
                    key = str(nm).strip()
                    if key and key not in id_by_name:
                        id_by_name[key] = str(iid).strip()

        lines: list[dict] = []
        for _, row in positive.iterrows():
            name = str(row["material_name"]).strip()
            lines.append(
                {
                    "item_id": id_by_name.get(name),
                    "item_name": name,
                    "unit": row.get("unit"),
                    "avg_daily": float(row.get("avg_daily") or 0),
                    "remaining_qty": float(row.get("remaining_qty") or 0),
                    "quantity": float(row.get("suggest_qty") or 0),
                }
            )
        return lines, None

    def on_material_request_start(self, message: dict) -> None:
        user = self._require_material_request_access(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(uid)
        self._clear_material_req_pending(uid)
        self._material_req_pending[uid] = {"await": "days"}
        self._reply(
            message,
            "🛒 درخواست مواد\n"
            "تعداد روز پوشش را انتخاب کنید (پیش‌فرض: ۷ روز).\n"
            "پیشنهاد بر اساس مصرف روزانه و موجودی باقیمانده محاسبه می‌شود.",
            kb.material_request_days_menu(),
        )

    def on_material_request_history(self, message: dict) -> None:
        user = self._require_material_request_access(message)
        if not user:
            return
        rows = self.db.list_material_requests(limit=10)
        if not rows:
            self._reply(
                message,
                "هنوز درخواست مواد ثبت‌شده‌ای نیست.",
                kb.material_request_days_menu(),
            )
            return
        lines = ["📜 تاریخچه درخواست‌ها (۱۰ مورد اخیر):", ""]
        for r in rows:
            actor = self._format_actor(
                bale_user_id=r.get("bale_user_id"),
                actor_display_name=r.get("actor_display_name"),
            )
            days = r.get("coverage_days")
            days_s = int(days) if days is not None and float(days) == int(float(days)) else days
            lines.append(
                f"#{r['id']} | {format_datetime(r.get('created_at'))} | "
                f"{days_s} روز | {int(r.get('line_count') or 0)} قلم | "
                f"ثبت‌کننده: {actor}"
            )
        uid = str(user["bale_user_id"])
        pending = self._material_req_pending.get(uid)
        menu = kb.material_request_days_menu() if pending else kb.main_menu(user)
        self._reply(message, "\n".join(lines), menu)

    def on_material_request_days(self, message: dict, days: int) -> bool:
        """Handle coverage-days choice. Returns True if consumed."""
        uid = self._uid(message)
        pending = self._material_req_pending.get(uid)
        if not pending or pending.get("await") != "days":
            return False
        user = self._require_material_request_access(message)
        if not user:
            self._clear_material_req_pending(uid)
            return True
        lines, err = self._build_mr_lines(user, days)
        if err:
            self._clear_material_req_pending(uid)
            self._reply(message, err, kb.main_menu(user))
            return True
        if not lines:
            self._clear_material_req_pending(uid)
            self._reply(
                message,
                "پیشنهادی نیست — موجودی برای بازه درخواست کافی به‌نظر می‌رسد.\n"
                "درخواست خالی باز نمی‌شود.",
                kb.main_menu(user),
            )
            return True
        self._material_req_pending[uid] = {
            "await": "review",
            "days": float(days),
            "lines": lines,
        }
        self._reply(
            message,
            self._format_mr_review(days, lines),
            kb.material_request_review_menu(),
        )
        return True

    def on_material_request_confirm(self, message: dict) -> None:
        user = self._require_material_request_access(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        pending = self._material_req_pending.get(uid)
        if not pending or pending.get("await") not in {"review", "edit_pick", "edit_qty"}:
            self._reply(message, "درخواست فعالی برای تأیید نیست.", kb.main_menu(user))
            return
        lines = [ln for ln in pending.get("lines") or [] if float(ln.get("quantity") or 0) > 0]
        if not lines:
            self._clear_material_req_pending(uid)
            self._reply(
                message,
                "هیچ قلمی با مقدار مثبت باقی نمانده — درخواست ثبت نشد.",
                kb.main_menu(user),
            )
            return
        display = user.get("display_name") or self._display_name(message)
        try:
            req = self.db.create_material_request(
                uid,
                actor_display_name=display,
                coverage_days=pending.get("days") or 7,
                lines=lines,
                status="confirmed",
            )
        except ValueError as exc:
            self._reply(message, str(exc), kb.material_request_review_menu())
            return
        self._clear_material_req_pending(uid)
        actor = self._format_actor(user)
        out = [
            f"✅ درخواست مواد #{req['id']} ثبت شد.",
            f"پوشش: {req.get('coverage_days')} روز",
            f"ثبت‌کننده: {actor}",
            "",
            "اقلام تأییدشده:",
        ]
        for ln in req.get("lines") or []:
            unit = ln.get("unit") or ""
            iid = ln.get("item_id") or "—"
            out.append(
                f"• {ln.get('item_name')} (شناسه: {iid}): "
                f"{float(ln.get('quantity') or 0):.2f} {unit}".rstrip()
            )
        out.append("")
        out.append("موجودی انبار با ledger کسر شد و در گزارش‌های بعدی منعکس می‌شود.")
        self._reply(message, "\n".join(out), kb.main_menu(user))

    def on_material_request_edit_start(self, message: dict) -> None:
        user = self._require_material_request_access(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        pending = self._material_req_pending.get(uid)
        if not pending or not pending.get("lines"):
            self._reply(message, "درخواستی برای اصلاح نیست.", kb.main_menu(user))
            return
        pending["await"] = "edit_pick"
        self._material_req_pending[uid] = pending
        lines = pending["lines"]
        body = ["✏️ اصلاح — شماره یا نام قلم را بفرستید (۰ برای حذف بعد از انتخاب مقدار):", ""]
        for i, ln in enumerate(lines, 1):
            unit = ln.get("unit") or ""
            body.append(
                f"{i}) {ln.get('item_name')}: {float(ln.get('quantity') or 0):.2f} {unit}".rstrip()
            )
        body.append("")
        body.append("سپس مقدار جدید را بفرستید. برای حذف، مقدار ۰ بفرستید.")
        self._reply(message, "\n".join(body), kb.material_request_edit_menu())

    def on_material_request_cancel(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._clear_material_req_pending(uid)
        self._reply(message, "درخواست مواد لغو شد.", kb.main_menu(user))

    def on_material_request_back_review(self, message: dict) -> None:
        user = self._require_material_request_access(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        pending = self._material_req_pending.get(uid)
        if not pending or not pending.get("lines"):
            self._clear_material_req_pending(uid)
            self._reply(message, "پیش‌نویسی نیست.", kb.main_menu(user))
            return
        pending["await"] = "review"
        pending.pop("edit_index", None)
        self._material_req_pending[uid] = pending
        self._reply(
            message,
            self._format_mr_review(pending.get("days") or 7, pending["lines"]),
            kb.material_request_review_menu(),
        )

    def on_material_request_edit_text(self, message: dict, text: str) -> bool:
        """Consume pick / qty text while editing. Returns True if handled."""
        uid = self._uid(message)
        pending = self._material_req_pending.get(uid)
        if not pending:
            return False
        await_mode = pending.get("await")
        if await_mode not in {"edit_pick", "edit_qty"}:
            return False
        user = self._require_material_request_access(message)
        if not user:
            self._clear_material_req_pending(uid)
            return True
        raw = (text or "").strip()
        # navigation buttons are handled by dispatcher; don't consume them here
        if raw in {
            kb.BTN_MR_BACK_REVIEW,
            kb.BTN_MR_CANCEL,
            kb.BTN_MR_CONFIRM_ALL,
            kb.BTN_MR_EDIT,
            kb.BTN_BACK_MAIN,
            kb.BTN_MATERIAL_REQUEST,
        }:
            return False

        lines: list[dict] = pending.get("lines") or []
        if await_mode == "edit_pick":
            idx = None
            if raw.isdigit():
                n = int(raw)
                if 1 <= n <= len(lines):
                    idx = n - 1
            if idx is None:
                needle = raw.casefold()
                for i, ln in enumerate(lines):
                    name = str(ln.get("item_name") or "").casefold()
                    iid = str(ln.get("item_id") or "").casefold()
                    if needle and (needle == name or needle == iid or needle in name):
                        idx = i
                        break
            if idx is None:
                self._reply(
                    message,
                    "قلم یافت نشد. شماره یا نام را دوباره بفرستید.",
                    kb.material_request_edit_menu(),
                )
                return True
            pending["await"] = "edit_qty"
            pending["edit_index"] = idx
            self._material_req_pending[uid] = pending
            ln = lines[idx]
            unit = ln.get("unit") or ""
            self._reply(
                message,
                f"مقدار جدید برای «{ln.get('item_name')}» را بفرستید "
                f"(فعلی: {float(ln.get('quantity') or 0):.2f} {unit}).\n"
                "۰ = حذف از لیست.",
                kb.material_request_edit_menu(),
            )
            return True

        # edit_qty
        try:
            qty = float(raw.replace(",", "."))
        except ValueError:
            self._reply(
                message,
                "مقدار نامعتبر است. یک عدد بفرستید (مثلاً ۱۲.۵ یا ۰).",
                kb.material_request_edit_menu(),
            )
            return True
        if qty < 0:
            self._reply(message, "مقدار نمی‌تواند منفی باشد.", kb.material_request_edit_menu())
            return True
        idx = int(pending.get("edit_index", -1))
        if idx < 0 or idx >= len(lines):
            pending["await"] = "edit_pick"
            pending.pop("edit_index", None)
            self._material_req_pending[uid] = pending
            self._reply(message, "انتخاب قلم نامعتبر بود. دوباره شماره را بفرستید.", kb.material_request_edit_menu())
            return True
        if qty == 0:
            removed = lines.pop(idx)
            msg = f"«{removed.get('item_name')}» حذف شد."
        else:
            lines[idx]["quantity"] = qty
            msg = f"مقدار «{lines[idx].get('item_name')}» به {qty:.2f} به‌روز شد."
        pending["lines"] = lines
        pending["await"] = "review"
        pending.pop("edit_index", None)
        self._material_req_pending[uid] = pending
        if not lines:
            self._clear_material_req_pending(uid)
            self._reply(
                message,
                msg + "\nلیست خالی شد — درخواست لغو گردید.",
                kb.main_menu(user),
            )
            return True
        self._reply(
            message,
            msg + "\n\n" + self._format_mr_review(pending.get("days") or 7, lines),
            kb.material_request_review_menu(),
        )
        return True



    # ---------- warehouse return (برگشت به انبار) ----------

    def _format_wr_review(self, lines: list[dict]) -> str:
        header = ["↩️ پیشنهاد برگشت مواد مازاد به انبار:", ""]
        if not lines:
            header.append("ماده مازادی برای برگشت شناسایی نشد.")
            return "\n".join(header)
        for i, ln in enumerate(lines, 1):
            unit = ln.get("unit") or ""
            iid = ln.get("item_id") or "—"
            reason = ln.get("surplus_reason") or ""
            header.append(
                f"{i}) {ln.get('item_name')} (شناسه: {iid})\n"
                f"   موجودی سایت: {float(ln.get('site_qty') or 0):.2f} {unit} | "
                f"مازاد≈ {float(ln.get('surplus_qty') or 0):.2f} | "
                f"برگشت: {float(ln.get('quantity') or 0):.2f} {unit}".rstrip()
                + (f"\n   دلیل: {reason}" if reason else "")
            )
        header.append("")
        header.append("تأیید همه / اصلاح / انصراف را انتخاب کنید.")
        return "\n".join(header)

    def _build_wr_lines(self, user: dict) -> tuple[list[dict], str | None]:
        """Suggest surplus lines from site stock (preferred) or warehouse remaining."""
        session = self.db.get_or_create_session(user["bale_user_id"])
        completeness = self.db.session_completeness(session)
        # Need consumption rates + some remaining source
        missing = missing_files_for_goal("surplus", completeness)
        # surplus needs tank or monthly + inventory; but site stock can replace inventory
        site_rows = self.db.site_stock_as_remaining_rows()
        if missing and not site_rows:
            return [], (
                "برای برگشت به انبار این فایل(ها) لازم است:\n• "
                + "\n• ".join(missing)
                + "\n\nیا ابتدا موجودی روزانه سایت را وارد کنید."
            )
        frames, _ = self._load_frames(user, session) if any(completeness.values()) else ({}, {})
        frames = self._apply_tundish_filter(frames, str(user["bale_user_id"]))
        tank = frames.get("tank_consumption")
        monthly = frames.get("monthly_consumption")
        rates = daily_rates(tank, monthly)

        if site_rows:
            rem_df = remaining(pd.DataFrame(site_rows))
            source = "موجودی روزانه سایت"
        else:
            rem_df = remaining(self._inventory_with_ledger(frames.get("product_inventory")))
            source = "موجودی انبار"

        surplus = surplus_materials(rates, rem_df)
        if surplus is None or surplus.empty:
            return [], None
        positive = surplus.loc[surplus["surplus_qty"] > 0].copy()
        if positive.empty:
            return [], None

        id_by_name: dict[str, str] = {}
        # Prefer ids from site stock rows, else warehouse inventory
        for row in site_rows or []:
            nm = str(row.get("product_name") or "").strip()
            iid = row.get("id")
            if nm and iid is not None and str(iid).strip():
                id_by_name.setdefault(nm, str(iid).strip())
        inv = frames.get("product_inventory")
        if inv is not None and not inv.empty:
            name_col = None
            for candidate in ("product_name", "item_code_desc", "material_name"):
                if candidate in inv.columns:
                    name_col = candidate
                    break
            if name_col and "id" in inv.columns:
                for _, row in inv.iterrows():
                    nm = row.get(name_col)
                    iid = row.get("id")
                    if nm is None or iid is None:
                        continue
                    if isinstance(nm, float) and pd.isna(nm):
                        continue
                    if isinstance(iid, float) and pd.isna(iid):
                        continue
                    id_by_name.setdefault(str(nm).strip(), str(iid).strip())

        lines: list[dict] = []
        for _, row in positive.iterrows():
            name = str(row["material_name"]).strip()
            site_q = float(row.get("remaining_qty") or 0)
            sur_q = float(row.get("surplus_qty") or 0)
            qty = min(site_q, sur_q) if site_q > 0 else sur_q
            if qty <= 0:
                continue
            lines.append(
                {
                    "item_id": id_by_name.get(name),
                    "item_name": name,
                    "unit": row.get("unit"),
                    "site_qty": site_q,
                    "surplus_qty": sur_q,
                    "quantity": qty,
                    "surplus_reason": row.get("surplus_reason"),
                    "_source": source,
                }
            )
        return lines, None

    def on_warehouse_return_start(self, message: dict) -> None:
        user = self._require_material_request_access(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(uid)
        self._clear_material_req_pending(uid)
        self._clear_warehouse_ret_pending(uid)
        lines, err = self._build_wr_lines(user)
        if err:
            self._reply(message, err, kb.main_menu(user))
            return
        if not lines:
            self._reply(
                message,
                "ماده مازادی برای برگشت شناسایی نشد — درخواست خالی باز نمی‌شود.",
                kb.main_menu(user),
            )
            return
        self._warehouse_ret_pending[uid] = {"await": "review", "lines": lines}
        self._reply(
            message,
            self._format_wr_review(lines),
            kb.warehouse_return_review_menu(),
        )

    def on_warehouse_return_confirm(self, message: dict) -> None:
        user = self._require_material_request_access(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        pending = self._warehouse_ret_pending.get(uid)
        if not pending or pending.get("await") not in {"review", "edit_pick", "edit_qty"}:
            self._reply(message, "برگشت فعالی برای تأیید نیست.", kb.main_menu(user))
            return
        lines = [ln for ln in pending.get("lines") or [] if float(ln.get("quantity") or 0) > 0]
        if not lines:
            self._clear_warehouse_ret_pending(uid)
            self._reply(
                message,
                "هیچ قلمی با مقدار مثبت باقی نمانده — برگشت ثبت نشد.",
                kb.main_menu(user),
            )
            return
        display = user.get("display_name") or self._display_name(message)
        try:
            ret = self.db.create_warehouse_return(
                uid,
                actor_display_name=display,
                lines=lines,
                status="confirmed",
            )
        except ValueError as exc:
            self._reply(message, str(exc), kb.warehouse_return_review_menu())
            return
        self._clear_warehouse_ret_pending(uid)
        actor = self._format_actor(user)
        out = [
            f"✅ برگشت به انبار #{ret['id']} ثبت شد.",
            f"ثبت‌کننده: {actor}",
            "",
            "اقلام برگشتی:",
        ]
        for ln in ret.get("lines") or []:
            unit = ln.get("unit") or ""
            iid = ln.get("item_id") or "—"
            out.append(
                f"• {ln.get('item_name')} (شناسه: {iid}): "
                f"{float(ln.get('quantity') or 0):.2f} {unit}".rstrip()
            )
        out.append("")
        out.append("موجودی انبار با ledger مثبت افزایش یافت.")
        self._reply(message, "\n".join(out), kb.main_menu(user))

    def on_warehouse_return_edit_start(self, message: dict) -> None:
        user = self._require_material_request_access(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        pending = self._warehouse_ret_pending.get(uid)
        if not pending or not pending.get("lines"):
            self._reply(message, "برگشتی برای اصلاح نیست.", kb.main_menu(user))
            return
        pending["await"] = "edit_pick"
        self._warehouse_ret_pending[uid] = pending
        lines = pending["lines"]
        body = ["✏️ اصلاح برگشت — شماره یا نام قلم را بفرستید:", ""]
        for i, ln in enumerate(lines, 1):
            unit = ln.get("unit") or ""
            body.append(
                f"{i}) {ln.get('item_name')}: {float(ln.get('quantity') or 0):.2f} {unit}".rstrip()
            )
        body.append("")
        body.append("سپس مقدار جدید را بفرستید. ۰ = حذف از لیست.")
        self._reply(message, "\n".join(body), kb.warehouse_return_edit_menu())

    def on_warehouse_return_cancel(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._clear_warehouse_ret_pending(uid)
        self._reply(message, "برگشت به انبار لغو شد.", kb.main_menu(user))

    def on_warehouse_return_back_review(self, message: dict) -> None:
        user = self._require_material_request_access(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        pending = self._warehouse_ret_pending.get(uid)
        if not pending or not pending.get("lines"):
            self._clear_warehouse_ret_pending(uid)
            self._reply(message, "پیش‌نویسی نیست.", kb.main_menu(user))
            return
        pending["await"] = "review"
        pending.pop("edit_index", None)
        self._warehouse_ret_pending[uid] = pending
        self._reply(
            message,
            self._format_wr_review(pending["lines"]),
            kb.warehouse_return_review_menu(),
        )

    def on_warehouse_return_edit_text(self, message: dict, text: str) -> bool:
        uid = self._uid(message)
        pending = self._warehouse_ret_pending.get(uid)
        if not pending:
            return False
        await_mode = pending.get("await")
        if await_mode not in {"edit_pick", "edit_qty"}:
            return False
        user = self._require_material_request_access(message)
        if not user:
            self._clear_warehouse_ret_pending(uid)
            return True
        raw = (text or "").strip()
        if raw in {
            kb.BTN_MR_BACK_REVIEW,
            kb.BTN_MR_CANCEL,
            kb.BTN_MR_CONFIRM_ALL,
            kb.BTN_MR_EDIT,
            kb.BTN_BACK_MAIN,
            kb.BTN_WAREHOUSE_RETURN,
            kb.BTN_MATERIAL_REQUEST,
        }:
            return False
        lines: list[dict] = pending.get("lines") or []
        if await_mode == "edit_pick":
            idx = None
            if raw.isdigit():
                n = int(raw)
                if 1 <= n <= len(lines):
                    idx = n - 1
            if idx is None:
                needle = raw.casefold()
                for i, ln in enumerate(lines):
                    name = str(ln.get("item_name") or "").casefold()
                    iid = str(ln.get("item_id") or "").casefold()
                    if needle and (needle == name or needle == iid or needle in name):
                        idx = i
                        break
            if idx is None:
                self._reply(
                    message,
                    "قلم یافت نشد. شماره یا نام را دوباره بفرستید.",
                    kb.warehouse_return_edit_menu(),
                )
                return True
            pending["await"] = "edit_qty"
            pending["edit_index"] = idx
            self._warehouse_ret_pending[uid] = pending
            ln = lines[idx]
            unit = ln.get("unit") or ""
            self._reply(
                message,
                f"مقدار برگشت برای «{ln.get('item_name')}» را بفرستید "
                f"(فعلی: {float(ln.get('quantity') or 0):.2f} {unit}).\n"
                "۰ = حذف از لیست.",
                kb.warehouse_return_edit_menu(),
            )
            return True
        try:
            qty = float(raw.replace(",", "."))
        except ValueError:
            self._reply(
                message,
                "مقدار نامعتبر است. یک عدد بفرستید.",
                kb.warehouse_return_edit_menu(),
            )
            return True
        if qty < 0:
            self._reply(message, "مقدار نمی‌تواند منفی باشد.", kb.warehouse_return_edit_menu())
            return True
        idx = int(pending.get("edit_index", -1))
        if idx < 0 or idx >= len(lines):
            pending["await"] = "edit_pick"
            pending.pop("edit_index", None)
            self._warehouse_ret_pending[uid] = pending
            self._reply(message, "انتخاب قلم نامعتبر بود.", kb.warehouse_return_edit_menu())
            return True
        if qty == 0:
            removed = lines.pop(idx)
            msg = f"«{removed.get('item_name')}» حذف شد."
        else:
            lines[idx]["quantity"] = qty
            msg = f"مقدار «{lines[idx].get('item_name')}» به {qty:.2f} به‌روز شد."
        pending["lines"] = lines
        pending["await"] = "review"
        pending.pop("edit_index", None)
        self._warehouse_ret_pending[uid] = pending
        if not lines:
            self._clear_warehouse_ret_pending(uid)
            self._reply(
                message,
                msg + "\nلیست خالی شد — برگشت لغو گردید.",
                kb.main_menu(user),
            )
            return True
        self._reply(
            message,
            msg + "\n\n" + self._format_wr_review(lines),
            kb.warehouse_return_review_menu(),
        )
        return True


    # ---------- dispatcher ----------
    def handle_message(self, message: dict) -> None:
        if not message:
            return
        text = (message.get("text") or "").strip()

        # Bot-settings image wait (photo or image document) — before generic document handler
        if message.get("photo") or message.get("document"):
            if self.on_bot_settings_photo(message):
                return

        if message.get("document"):
            self.on_document(message)
            return

        if message.get("photo"):
            # Photo outside settings flow — ignore politely if registered
            return

        if not text:
            return

        if text.startswith("/"):
            parts = text.split()
            cmd = parts[0].split("@")[0].lower()
            args = parts[1:]
            mapping = {
                "/start": lambda: self.cmd_start(message, args),
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

        # site stock quantity entry (number while awaiting items)
        if self.on_site_stock_quantity_text(message, text):
            return

        # catalog item pick (number/id while awaiting)
        if self.on_catalog_pick_text(message, text):
            return

        # user-management interactive flow (role/id text)
        if self.on_users_flow_text(message, text):
            return

        # bot-settings interactive text (invite/welcome templates)
        if self.on_bot_settings_flow_text(message, text):
            return

        # material-request / warehouse-return edit text (pick item / qty)
        if self.on_material_request_edit_text(message, text):
            return
        if self.on_warehouse_return_edit_text(message, text):
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
            self.on_users_menu(message)
            return
        if text == kb.BTN_USERS_ADD:
            self.on_users_add_start(message)
            return
        if text == kb.BTN_USERS_EDIT:
            self.on_users_edit_start(message)
            return
        if text == kb.BTN_USERS_DELETE:
            self.on_users_delete_start(message)
            return
        if text == kb.BTN_USERS_LIST:
            self.cmd_users(message)
            return
        if text == kb.BTN_BACK_USERS:
            user = self._require_manager_user(message)
            if user:
                self._clear_users_pending(str(user["bale_user_id"]))
                self._reply(message, "منوی اصلی:", kb.main_menu(user))
            return

        # --- تنظیمات ربات ---
        if text == kb.BTN_BOT_SETTINGS:
            self.on_bot_settings_menu(message)
            return
        if text == kb.BTN_SET_INVITE:
            self.on_bot_settings_section(message, "invite")
            return
        if text == kb.BTN_SET_WELCOME:
            self.on_bot_settings_section(message, "welcome")
            return
        if text == kb.BTN_SET_LOGO:
            self.on_bot_settings_section(message, "logo")
            return
        if text == kb.BTN_SETTINGS_VIEW:
            self.on_bot_settings_view(message)
            return
        if text == kb.BTN_SETTINGS_EDIT_TEXT:
            self.on_bot_settings_edit_text_start(message)
            return
        if text == kb.BTN_SETTINGS_SET_IMAGE:
            self.on_bot_settings_set_image_start(message)
            return
        if text == kb.BTN_SETTINGS_CLEAR_IMAGE:
            self.on_bot_settings_clear_image(message)
            return
        if text == kb.BTN_BACK_BOT_SETTINGS:
            self.on_bot_settings_menu(message)
            return


        # --- درخواست مواد / برگشت به انبار ---
        if text == kb.BTN_MATERIAL_REQUEST:
            self.on_material_request_start(message)
            return
        if text == kb.BTN_WAREHOUSE_RETURN:
            self.on_warehouse_return_start(message)
            return
        if text == kb.BTN_MR_HISTORY:
            self.on_material_request_history(message)
            return
        if text in {kb.BTN_MR_DAYS_7, kb.BTN_MR_DAYS_14, kb.BTN_MR_DAYS_30}:
            days_map = {
                kb.BTN_MR_DAYS_7: 7,
                kb.BTN_MR_DAYS_14: 14,
                kb.BTN_MR_DAYS_30: 30,
            }
            if self.on_material_request_days(message, days_map[text]):
                return
        if text == kb.BTN_MR_CONFIRM_ALL:
            uid = self._uid(message)
            if uid in self._warehouse_ret_pending:
                self.on_warehouse_return_confirm(message)
            else:
                self.on_material_request_confirm(message)
            return
        if text == kb.BTN_MR_EDIT:
            uid = self._uid(message)
            if uid in self._warehouse_ret_pending:
                self.on_warehouse_return_edit_start(message)
            else:
                self.on_material_request_edit_start(message)
            return
        if text == kb.BTN_MR_CANCEL:
            uid = self._uid(message)
            if uid in self._warehouse_ret_pending:
                self.on_warehouse_return_cancel(message)
            elif uid in self._material_req_pending:
                self.on_material_request_cancel(message)
            else:
                user = self._user_or_deny(message)
                if user:
                    self._reply(message, "عملیاتی برای انصراف نیست.", kb.main_menu(user))
            return
        if text == kb.BTN_MR_BACK_REVIEW:
            uid = self._uid(message)
            if uid in self._warehouse_ret_pending:
                self.on_warehouse_return_back_review(message)
            else:
                self.on_material_request_back_review(message)
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
                uid = str(user["bale_user_id"])
                self._clear_analysis_pending(uid)
                self._clear_site_stock_pending(uid)
                self._clear_catalog_pending(uid)
                self._clear_users_pending(uid)
                self._clear_material_req_pending(uid)
                self._clear_warehouse_ret_pending(uid)
                self._bot_settings_pending.pop(uid, None)
                self._await_category_code.discard(uid)
                self._reply(message, "منوی اصلی:", kb.main_menu(user))
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
        if text == kb.BTN_MONTHLY_SUMMARY:
            self.on_monthly_summary(message)
            return
        if text == kb.BTN_RANGE_TODAY:
            if self.on_date_range_choice(message, "today"):
                return
        if text == kb.BTN_RANGE_7:
            if self.on_material_request_days(message, 7):
                return
            if self.on_date_range_choice(message, "7d"):
                return
        if text == kb.BTN_RANGE_30:
            if self.on_material_request_days(message, 30):
                return
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

        # --- موجودی روزانه سایت ---
        if text == kb.BTN_SITE_STOCK or text == kb.BTN_TANK:
            self.on_site_stock_menu(message)
            return
        if text in kb.SITE_GROUP_BUTTONS:
            self.on_site_stock_group(message, kb.SITE_GROUP_BUTTONS[text])
            return
        if text == kb.BTN_SITE_SKIP:
            self.on_site_stock_skip(message)
            return
        if text == kb.BTN_SITE_CANCEL:
            self.on_site_stock_cancel(message)
            return
        if text == kb.BTN_BACK_SITE:
            self.on_site_stock_menu(message)
            return

        # --- تنظیمات اقلام سایت ---
        if text == kb.BTN_CATALOG_SETTINGS:
            self.on_catalog_settings_menu(message)
            return
        if text == kb.BTN_CATALOG_LIST:
            self.on_catalog_list(message, unassigned_only=False)
            return
        if text == kb.BTN_CATALOG_UNASSIGNED:
            self.on_catalog_list(message, unassigned_only=True)
            return
        if text == kb.BTN_CATALOG_SEED:
            self.on_catalog_seed(message)
            return
        if text == kb.BTN_BACK_CATALOG:
            self.on_catalog_settings_menu(message)
            return
        if text in kb.ASSIGN_GROUP_BUTTONS:
            self.on_catalog_assign_group(message, kb.ASSIGN_GROUP_BUTTONS[text])
            return
        if text == kb.BTN_CATALOG_UNASSIGN:
            self.on_catalog_assign_group(message, None)
            return

        file_type = kb.button_to_file_type(text)
        if file_type:
            self.on_pick_file_type(message, file_type)
            return

        user = ensure_registered(self.db, self._uid(message), self._display_name(message))
        self._reply(
            message,
            "لطفاً از دکمه‌های منو استفاده کنید یا /help را بزنید.",
            kb.main_menu(user) if user else None,
        )

    def handle_update(self, update: dict) -> None:
        try:
            if "message" in update:
                self.handle_message(update["message"])
        except Exception:  # noqa: BLE001
            logger.exception("update failed: %s", update.get("update_id"))
