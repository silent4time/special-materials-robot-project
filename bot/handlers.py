"""Message/command handlers — request-driven three-file upload + RBAC + tundish analytics."""
from __future__ import annotations

import logging
import re
import shutil
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd
from typing import Any

from analytics.frames import (
    PRIMARY_INVENTORY_LABEL,
    PRIMARY_INVENTORY_TYPE,
    completeness_status_lines,
    data_completeness,
    inventory_with_ledger,
    load_primary_inventory,
    resolve_extract_path,
    resolve_primary_inventory_path,
    resolve_remaining as shared_resolve_remaining,
    resolve_warehouse_remaining,
)
from analytics.tundish import (
    critical_materials,
    daily_rates,
    filter_by_tundish_type,
    forecast,
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
    require_owner,
    role_label,
)
from bot import keyboards as kb
from bot.activity import log_activity, resolve_display_name
from bot.jalali import (
    TEHRAN,
    format_date,
    format_datetime,
    format_month_year,
    format_month_year_range,
    month_year_to_gregorian_bounds,
    parse_month_year_range,
    resolve_month_year_preset,
    year_choices_around,
    PERSIAN_MONTH_NAME_TO_NUM,
)
from bot.bale_api import BaleAPIError, BaleClient, public_markup
from services import permissions as perm
from services import user_errors
from bot.background import BackgroundRunner, heavy
from services import comprehensive_report as comprehensive_svc
from bot.settings_text import (
    DEFAULT_INVITE_TEXT,
    PLACEHOLDER_HINT_INVITE,
    PLACEHOLDER_HINT_WELCOME,
    format_invite_text,
    format_welcome_text,
)
from config import ASSISTANT_ENABLED, BOT_ASSETS_DIR, BOT_USERNAME, CRITICAL_DAYS, FILE_TYPES, REPORT_DIR, ROLES, SITE_STOCK_GROUPS, SITE_STOCK_REPORT_GROUP_ID, SURPLUS_COVER_DAYS, SURPLUS_FORECAST_DAYS, UPLOAD_DIR, ensure_dirs
from db.models import Database
from services import main_source as main_source_svc
from services import site_stock_notify
from services import inbound_report as inbound_svc
from excel.processor import (
    ExcelValidationError,
    extract_and_save_clean,
    inventory_table_rows,
    load_excel,
    looks_like_product_inventory,
    merge_clean_frames,
    write_clean_excel,
    process_file,
    process_session_files,
)
from excel.monthly_summary import (
    SUMMARY_FILE_NAME,
    build_monthly_summary,
    summary_sections_for_pdf,
)
from pdf.generator import generate_monthly_summary_pdf, generate_report, generate_simple_report_pdf
from excel.simple_report import (
    export_dataframe_xlsx,
    generate_analytics_report_xlsx,
    generate_simple_report_xlsx,
)
from services.main_source import FIELD_LABELS_FA, INVENTORY_COLUMNS

logger = logging.getLogger(__name__)


def _utc_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()

from bot.help_text import HELP_TEXT, help_text_for  # noqa: E402  (re-exported)


class BotApp:
    def __init__(self, client: BaleClient, db: Database) -> None:
        self.client = client
        self.db = db
        perm.bind(db)  # role-permission overrides come from this DB
        self._boot_iso = _utc_iso()
        self.bg = BackgroundRunner()  # 19c: enabled by main.py (tests stay synchronous)
        self._flow_marked: set[str] = set()
        self._flow_seen: set[str] = set()
        self._stale_once: set[str] = set()
        # pending analytics: mode + await month_range|my_*|range (day advanced)
        self._analysis_pending: dict[str, dict[str, Any]] = {}
        # uid -> {"key": menu key, "origin": menu that opened a flow} (uniform «⬅️ بازگشت»)
        self._nav: dict[str, dict[str, str]] = {}
        # «📦 جایگزینی کامل منبع اصلی» waiting for confirm
        self._full_replace_confirm: set[str] = set()
        # «🧮 نیاز مواد برای N تاندیش» steps
        self._n_tundish_pending: dict[str, dict[str, Any]] = {}
        # «❓ راهنمای تهیهٔ فایل»: last file kind the user is asked for
        self._file_guide_kind: dict[str, str] = {}
        # درخواست مواد: Persian basis line of the last built list (shown in review)
        self._mr_basis: dict[str, str] = {}
        # awaiting plain text for category code entry
        self._await_category_code: set[str] = set()
        # منبع اصلی edit/add record interactive flow
        self._main_source_pending: dict[str, dict[str, Any]] = {}
        # After Excel pick/upload: "upload" | "main_source" | "main"
        self._upload_return_menu: dict[str, str] = {}
        # site stock interactive entry: uid -> {group, items, values, awaiting_idx, walk_idx, guided, chat_id, message_id}
        self._site_stock_pending: dict[str, dict[str, Any]] = {}
        # user-management interactive flows
        self._users_pending: dict[str, dict[str, Any]] = {}
        # bot settings interactive flows
        self._bot_settings_pending: dict[str, dict[str, Any]] = {}
        # material request interactive flow
        self._material_req_pending: dict[str, dict[str, Any]] = {}
        self._warehouse_ret_pending: dict[str, dict[str, Any]] = {}
        # اقلام بحرانی: year/month/counts entry
        self._critical_pending: dict[str, dict[str, Any]] = {}
        self._bot_username: str | None = None
        # گزارش تاندیش بعد از ریخته‌گری (entry + settings) — see bot/tundish_report_flow.py
        from bot.tundish_report_flow import TundishReportFlow

        self.tundish_report = TundishReportFlow(self)

        # گزارش هدف اصلی — see bot/main_goal_report_flow.py
        from bot.main_goal_report_flow import MainGoalReportFlow

        self.main_goal_report = MainGoalReportFlow(self)

        # یادآور گزارش‌های الزامی (تنظیمات ربات) — see bot/reminder_settings_flow.py
        from bot.reminder_settings_flow import ReminderSettingsFlow

        self.reminder_settings = ReminderSettingsFlow(self)
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
        markup = self._track_nav(message, markup)
        self.client.send_message(self._chat_id(message), text, reply_markup=markup)

    # ---------- uniform navigation ----------
    def _track_nav(self, message: dict, markup: dict | None) -> dict | None:
        """Record which menu the user sees (``_menu`` marker) and strip the marker."""
        if not isinstance(markup, dict) or "_menu" not in markup:
            return markup
        try:
            uid = self._uid(message)
        except (KeyError, TypeError):
            return public_markup(markup)
        key = str(markup.get("_menu"))
        cur = self._nav.get(uid) or {}
        self._mark_flow(uid, key == "flow")
        if key == "flow":
            origin = cur.get("origin") if cur.get("key") == "flow" else cur.get("key")
            self._nav[uid] = {"key": "flow", "origin": origin or "main"}
        else:
            self._nav[uid] = {"key": key}
        return public_markup(markup)

    # ---------- 19d: «operation expired after restart» ----------
    _FLOW_KEY = "flow_open:"

    def _mark_flow(self, uid: str, open_: bool) -> None:
        """Persist «user is inside a multi-step flow» so a restart can tell them."""
        try:
            if open_ and uid not in self._flow_marked:
                self.db.set_setting(self._FLOW_KEY + uid, _utc_iso())
                self._flow_marked.add(uid)
            elif not open_ and uid in self._flow_marked:
                self.db.set_setting(self._FLOW_KEY + uid, None)
                self._flow_marked.discard(uid)
        except Exception:  # noqa: BLE001
            logger.warning("flow marker write failed for %s", uid)

    def _check_stale_flow(self, uid: str) -> None:
        """First message of a user in this process: was a flow open before restart?"""
        if uid in self._flow_seen:
            return
        self._flow_seen.add(uid)
        try:
            ts = self.db.get_setting(self._FLOW_KEY + uid)
            if ts and ts < self._boot_iso:
                self.db.set_setting(self._FLOW_KEY + uid, None)
                self._stale_once.add(uid)
        except Exception:  # noqa: BLE001
            pass

    def _expired_flow_notice(self, message: dict, user: dict | None) -> bool:
        """True (and notice sent) when the user's first message after a restart was
        input for a flow that died with the old process (typed value / ✖️ انصراف)."""
        if not user:
            return False
        uid = str(user["bale_user_id"])
        if uid not in self._stale_once or self._has_pending(uid):
            return False
        self._stale_once.discard(uid)
        self._reply(message, user_errors.EXPIRED_FA, kb.main_menu(user))
        return True

    def _nav_target_back(self, uid: str) -> str:
        cur = self._nav.get(uid) or {"key": "main"}
        if cur.get("key") == "flow":
            return cur.get("origin") or "main"
        return kb.MENU_PARENT.get(cur.get("key") or "main") or "main"

    def _open_menu(self, message: dict, user: dict, key: str) -> None:
        """Open a menu by nav key (permission checks live in each opener)."""
        openers = {
            "site_stock": self.on_site_stock_menu,
            "tundish_report": self.tundish_report.open_menu,
            "reports": self.on_analytics_menu,
            "critical": self.on_critical_items_menu,
            "inbound_history": self.on_analytics_menu,
            "main_goal": self.main_goal_report.open_menu,
            "mg_inputs": self.main_goal_report.open_inputs,
            "mg_history": self.main_goal_report.show_history,
            "files": self.on_upload_menu,
            "main_source": self.on_main_source_file_menu,
            "settings": self.on_bot_settings_menu,
            "users": self.on_users_menu,
            "tr_settings": self.tundish_report.open_settings,
            "reminders": self.reminder_settings.open_menu,
            "appearance": self.on_appearance_menu,
            "appearance_item": self.on_appearance_menu,
            "letterhead": self.on_appearance_menu,
            "stock_group": self.on_bot_settings_menu,
        }
        opener = openers.get(key)
        if opener is None:
            self._reply(message, "منوی اصلی:", kb.main_menu(user))
            return
        opener(message)

    def _has_pending(self, uid: str) -> bool:
        uid = str(uid)
        session = self.db.get_or_create_session(uid)
        return any(
            (
                uid in self._analysis_pending,
                uid in self._await_category_code,
                uid in self._main_source_pending,
                uid in self._site_stock_pending,
                uid in self._users_pending,
                uid in self._bot_settings_pending,
                uid in self._material_req_pending,
                uid in self._warehouse_ret_pending,
                uid in self._critical_pending,
                uid in self._full_replace_confirm,
                uid in self._n_tundish_pending,
                uid in self.tundish_report.pending,
                uid in self.tundish_report.settings_pending,
                uid in self.main_goal_report.pending,
                self.reminder_settings.has_pending(uid),
                bool(session.get("pending_file_type")),
            )
        )

    def _clear_all_pending(self, uid: str) -> None:
        """Shared full clear: «🏠 منوی اصلی», /reset, «⬅️ بازگشت», «✖️ انصراف»."""
        uid = str(uid)
        self._clear_analysis_pending(uid)
        self._clear_site_stock_pending(uid)
        self._clear_users_pending(uid)
        self._clear_material_req_pending(uid)
        self._clear_warehouse_ret_pending(uid)
        self._clear_critical_pending(uid)
        self.tundish_report.clear(uid)
        self.main_goal_report.clear(uid)
        self.reminder_settings.clear(uid)
        self._bot_settings_pending.pop(uid, None)
        self._await_category_code.discard(uid)
        self._main_source_pending.pop(uid, None)
        self._upload_return_menu.pop(uid, None)
        self._full_replace_confirm.discard(uid)
        self._n_tundish_pending.pop(uid, None)
        self._file_guide_kind.pop(uid, None)
        try:
            self.db.set_pending_file_type(uid, None)
        except Exception:  # noqa: BLE001 - unknown user / no session
            logger.debug("clear pending file type failed for %s", uid)

    def on_nav_home(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        self._clear_all_pending(str(user["bale_user_id"]))
        self._reply(message, "منوی اصلی:", kb.main_menu(user))

    def on_nav_back(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        target = self._nav_target_back(uid)
        self._clear_all_pending(uid)
        self._open_menu(message, user, target)

    def on_cancel(self, message: dict) -> None:
        """Single «✖️ انصراف» for every multi-step flow (routes to the active one)."""
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        if uid in self.tundish_report.pending:
            self.tundish_report.pending.pop(uid, None)
            self._reply(message, "گزارش تاندیش لغو شد؛ چیزی ذخیره نشد.", kb.tundish_report_menu(user))
            return
        if uid in self.main_goal_report.pending:
            self.main_goal_report.cancel(message)
            return
        if uid in self._site_stock_pending:
            self.on_site_stock_cancel(message)
            return
        if uid in self._warehouse_ret_pending:
            self.on_warehouse_return_cancel(message)
            return
        if uid in self._material_req_pending:
            self.on_material_request_cancel(message)
            return
        session = self.db.get_or_create_session(uid)
        if (
            uid in self._main_source_pending
            or uid in self._await_category_code
            or session.get("pending_file_type")
        ):
            self.on_cancel_pending(message)
            return
        if not self._has_pending(uid):
            if self._expired_flow_notice(message, user):
                return
            target = (self._nav.get(uid) or {}).get("origin") or (self._nav.get(uid) or {}).get("key") or "main"
            self._reply(message, "عملیاتی برای انصراف نیست.")
            self._open_menu(message, user, target if target != "flow" else "main")
            return
        target = self._nav_target_back(uid)
        self._clear_all_pending(uid)
        self._reply(message, "لغو شد؛ چیزی ذخیره نشد.")
        self._open_menu(message, user, target)

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

    def _deny_technician(self, message: dict, user: dict, *features: str) -> bool:
        """Permission gate (name kept for history): deny unless ``user`` has one of
        ``features`` (services.permissions; DB overrides apply). Without features the
        old «technician-like» rule is used: deny roles with no data features."""
        if features:
            if perm.can_any(user, features):
                return False
        elif not perm.is_limited(user):
            return False
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(uid)
        self._await_category_code.discard(uid)
        self._main_source_pending.pop(uid, None)
        self._upload_return_menu.pop(uid, None)
        self._site_stock_pending.pop(uid, None)
        self._users_pending.pop(uid, None)
        self._bot_settings_pending.pop(uid, None)
        self._material_req_pending.pop(uid, None)
        self._warehouse_ret_pending.pop(uid, None)
        self._n_tundish_pending.pop(uid, None)
        self._reply(
            message,
            "دسترسی ندارید؛ این بخش برای نقش شما فعال نیست "
            "(تنظیم در «⚙️ تنظیمات» ← «🔐 دسترسی نقش‌ها» توسط مالک/مدیر).",
            kb.main_menu(user),
        )
        return True

    def _status_text(self, session: dict, user: dict | None = None) -> str:
        """DB-aware status: extracts + interactive site stock, not session slots alone."""
        lines = ["وضعیت داده‌های موجود (پایگاه + جلسه):"]
        lines.extend(completeness_status_lines(self.db, user, session=session))
        done = (
            self._effective_completeness(user, session)
            if user
            else self.db.session_completeness(session)
        )
        pending = session.get("pending_file_type")
        if pending and pending in FILE_TYPES:
            lines.append(f"\nدر انتظار آپلود: {FILE_TYPES[pending]['label_fa']}")
        else:
            lines.append("\nنوع ورود اطلاعات را از دکمه‌های زیر انتخاب کنید.")
        has_inv = bool(done.get("product_inventory"))
        has_rates = bool(done.get("tank_consumption") or done.get("monthly_consumption"))
        if has_inv and has_rates:
            lines.append(
                f"\nداده‌های لازم برای گزارش‌ها آماده‌اند — از «{kb.BTN_ANALYTICS}» استفاده کنید."
            )
        elif has_inv or done.get("site_stock") or done.get("monthly_consumption"):
            lines.append("\nبا داده‌های موجود می‌توانید بخشی از گزارش‌ها را اجرا کنید.")
        else:
            lines.append(
                "\nهنوز دادهٔ کافی نیست — منبع اصلی / مصرف ماهیانه را آپلود کنید "
                "یا موجودی روزانه سایت را تعاملی وارد کنید."
            )
        return "\n".join(lines)

    def _session_paths(self, session: dict) -> dict[str, str | None]:
        return {
            "tank_consumption": session.get("tank_path"),
            "product_inventory": session.get("inventory_path"),
            "monthly_consumption": session.get("monthly_path"),
        }

    def _resolved_file_paths(self, user: dict, session: dict) -> dict[str, str | None]:
        """Resolve on-disk paths for analytics.

        Phase 2 item 16: every type resolves to the factory-wide newest extract
        (any user); session slot / own upload are only on-disk fallbacks.
        """
        uid = str(user["bale_user_id"])
        type_to_col = {
            "tank_consumption": "tank_path",
            "product_inventory": "inventory_path",
            "monthly_consumption": "monthly_path",
        }
        resolved: dict[str, str | None] = {}
        for file_type, col in type_to_col.items():
            if file_type == PRIMARY_INVENTORY_TYPE:
                # Always latest cleaned منبع اصلی — never a divergent session path
                primary = resolve_primary_inventory_path(
                    self.db,
                    bale_user_id=uid,
                    session_inventory_path=session.get(col),
                )
                resolved[file_type] = primary
                continue
            # factory-wide latest first (same as web load_frames), then session / own
            resolved[file_type] = resolve_extract_path(
                self.db, file_type, bale_user_id=uid, session_path=session.get(col)
            )
        return resolved

    def _effective_completeness(self, user: dict, session: dict) -> dict[str, bool]:
        """DB-aware completeness (extracts + interactive site stock)."""
        return data_completeness(self.db, user, session=session)

    def _load_frames(self, user: dict, session: dict) -> tuple[dict, dict]:
        """19f: same loader as the web (analytics.frames.load_frames, factory-wide).

        A session-only upload without an extract row (legacy) is still read from disk.
        """
        frames, metas = comprehensive_svc.load(self.db, user)
        paths = self._resolved_file_paths(user, session)
        legacy = {k: v for k, v in paths.items() if v and k not in frames}
        if legacy:
            extra, extra_meta = process_session_files(legacy, user)
            frames.update(extra)
            metas.update(extra_meta)
        return frames, metas

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
        candidates: list[tuple[str, str]] = []
        primary = resolve_primary_inventory_path(
            self.db,
            bale_user_id=uid,
            session_inventory_path=session.get("inventory_path"),
        )
        if primary:
            candidates.append((primary, "آخرین استخراج منبع اصلی"))
        if session.get("inventory_path"):
            candidates.append((str(session["inventory_path"]), "منبع اصلی جلسه جاری"))

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
                        "product_name": item.get("name_desc"),
                        "keyword": "",
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
            work = pd.DataFrame(columns=["category_code", "id", "product_name", "keyword", "quantity"])
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
            {"category_code": code, "product_name": "—", "quantity": None, "_placeholder": True}
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
        suid = str(uid)
        self._analysis_pending.pop(suid, None)
        if suid.isdigit():
            self._analysis_pending.pop(int(suid), None)

    def _clear_material_req_pending(self, uid: str) -> None:
        self._material_req_pending.pop(str(uid), None)

    def _clear_warehouse_ret_pending(self, uid: str) -> None:
        self._warehouse_ret_pending.pop(str(uid), None)

    def _clear_critical_pending(self, uid: str) -> None:
        self._critical_pending.pop(str(uid), None)

    def _inventory_with_ledger(self, frame: pd.DataFrame | None) -> pd.DataFrame | None:
        """Apply inventory_ledger deltas onto a RAW warehouse frame (shared helper).

        Never call on ``load_primary_inventory`` output — it is already ledgered.
        """
        return inventory_with_ledger(self.db, frame)

    def _require_files(self, message: dict, user: dict, goal: str) -> tuple[dict, dict, dict] | None:
        if self._deny_technician(message, user, *perm.REPORT_FEATURES):
            return None
        session = self.db.get_or_create_session(user["bale_user_id"])
        completeness = self._effective_completeness(user, session)
        missing = missing_files_for_goal(goal, completeness)
        if missing:
            self._reply(
                message,
                "برای این گزارش این داده‌ها لازم است:\n• "
                + "\n• ".join(missing)
                + f"\n\nمنبع اصلی و مصرف ماهیانه را از «{kb.BTN_UPLOAD_MENU}» بفرستید؛ "
                f"موجودی روزانهٔ سایت را از «{kb.BTN_SITE_STOCK}» وارد کنید.",
                kb.analytics_menu(user),
            )
            return None
        frames, metas = self._load_frames(user, session)
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
                f"{role_label(r['role'])}"
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


    @staticmethod
    def _is_group_chat(message: dict) -> bool:
        """True for Bale/Telegram group or supergroup chats."""
        chat = (message or {}).get("chat") or {}
        ctype = str(chat.get("type") or "").lower()
        if ctype in {"group", "supergroup"}:
            return True
        cid = chat.get("id")
        try:
            return int(cid) < 0
        except (TypeError, ValueError):
            return False

    def cmd_set_stock_group(self, message: dict) -> None:
        """Owner/manager: bind current group chat_id for site-stock reports."""
        user = ensure_registered(self.db, self._uid(message), self._display_name(message))
        if not user:
            self._reply(message, "شما در سیستم ثبت نشده‌اید.")
            return
        if not perm.can(user, perm.STOCK_GROUP):
            self._reply(message, "دسترسی تنظیم گروه گزارش موجودی برای نقش شما فعال نیست.")
            return
        if not self._is_group_chat(message):
            self._reply(
                message,
                "این دستور را داخل گروهی بفرستید که ربات عضو آن است.\n"
                "ربات شناسهٔ همان گروه را به‌عنوان «گروه گزارش موجودی روزانه» ذخیره می‌کند.\n"
                f"راهنما: «{kb.BTN_BOT_SETTINGS}» ← «{kb.BTN_SET_STOCK_GROUP}».",
                kb.main_menu(user),
            )
            return
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            self._reply(message, "شناسه گروه خوانده نشد.")
            return
        self.db.set_setting(
            site_stock_notify.SETTING_KEY,
            str(chat_id),
            updated_by=user["bale_user_id"],
        )
        log_activity(self.db, user, "settings_stock_group")
        title = (chat.get("title") or "").strip()
        title_bit = f" «{title}»" if title else ""
        self._reply(
            message,
            f"✅ گروه گزارش موجودی روزانه تنظیم شد{title_bit}.\n"
            f"chat_id: `{chat_id}`\n"
            "از این پس با ثبت موفق «موجودی روزانه سایت»، خلاصه به این گروه ارسال می‌شود.",
        )

    def on_my_chat_member(self, mcm: dict) -> None:
        """Log when bot joins a group so managers can /set_stock_group."""
        chat = (mcm or {}).get("chat") or {}
        new = (mcm or {}).get("new_chat_member") or {}
        status = str(new.get("status") or "").lower()
        chat_id = chat.get("id")
        title = (chat.get("title") or "").strip()
        ctype = chat.get("type")
        if status in {"member", "administrator", "creator"} and chat_id is not None:
            logger.info(
                "Bot joined chat id=%s title=%r type=%s — owner/manager: /set_stock_group",
                chat_id,
                title,
                ctype,
            )
        elif status in {"left", "kicked"} and chat_id is not None:
            current = site_stock_notify.resolve_report_group_id(self.db)
            logger.info(
                "Bot left/removed from chat id=%s title=%r (configured_report_group=%s)",
                chat_id,
                title,
                current,
            )

    def cmd_help(self, message: dict) -> None:
        user = ensure_registered(self.db, self._uid(message), self._display_name(message))
        self._reply(
            message,
            help_text_for(user),
            kb.main_menu(user) if user else None,
        )

    def _require_manager_user(self, message: dict) -> dict | None:
        user = self._user_or_deny(message)
        if not user:
            return None
        if self._deny_technician(message, user, perm.USERS):
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
        log_activity(self.db, actor, "user_add")
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
        self._reply(
            message,
            f"لینک دعوت ساخته شد — نقش: {role_label(role)} — "
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

        if raw in (
            kb.BTN_BACK_USERS,
            kb.BTN_BACK_MAIN,
            kb.BTN_CANCEL_PENDING,
            kb.BTN_INVITE_CANCEL,
        ):
            self._clear_users_pending(uid)
            if raw == kb.BTN_BACK_MAIN:
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
            # Confirm before creating invite link (invite-link model; no target user id).
            self._users_pending[uid] = {"mode": "add_confirm", "role": role}
            self._reply(
                message,
                (
                    f"ساخت لینک دعوت با نقش «{role_label(role)}»\n"
                    "اعتبار لینک: ۷ روز\n\n"
                    f"برای ساخت لینک «{kb.BTN_INVITE_CONFIRM}» را بزنید؛ "
                    f"برای لغو «{kb.BTN_INVITE_CANCEL}»."
                ),
                kb.invite_confirm_menu(),
            )
            return True

        # --- add: confirm invite creation ---
        if mode == "add_confirm":
            role = pending.get("role")
            if not role:
                self._clear_users_pending(uid)
                self._reply(
                    message,
                    "نقش نامشخص بود. دوباره نقش را انتخاب کنید:",
                    kb.users_menu(),
                )
                return True
            if raw == kb.BTN_INVITE_CONFIRM:
                self._clear_users_pending(uid)
                self._create_and_send_invite(message, user, role, None)
                return True
            self._reply(
                message,
                (
                    f"نقش انتخاب‌شده: «{role_label(role)}» — اعتبار ۷ روز.\n"
                    f"«{kb.BTN_INVITE_CONFIRM}» یا «{kb.BTN_INVITE_CANCEL}» را بزنید."
                ),
                kb.invite_confirm_menu(),
            )
            return True

        # Legacy: if an old pending add_scope somehow remains, ask confirm first
        if mode == "add_scope":
            role = pending.get("role") or "responsible_officer"
            self._users_pending[uid] = {"mode": "add_confirm", "role": role}
            self._reply(
                message,
                (
                    f"ساخت لینک دعوت با نقش «{role_label(role)}»\n"
                    "اعتبار لینک: ۷ روز\n\n"
                    f"برای ساخت لینک «{kb.BTN_INVITE_CONFIRM}» را بزنید؛ "
                    f"برای لغو «{kb.BTN_INVITE_CANCEL}»."
                ),
                kb.invite_confirm_menu(),
            )
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
            log_activity(self.db, user, "user_edit")
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
            log_activity(self.db, user, "user_delete")
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
        if self._deny_technician(message, user, perm.USERS):
            return
        if len(args) < 2:
            self._reply(
                message,
                "فرمت:\n/adduser <bale_id> <owner|manager|responsible_officer|technician|shift_supervisor> [name...]",
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
            f"{role_label(created['role'])}",
            kb.main_menu(user),
        )

    def cmd_setrole(self, message: dict, args: list[str]) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.USERS):
            return
        if len(args) < 2 or args[1] not in ROLES:
            self._reply(message, "فرمت: /setrole <bale_id> <owner|manager|responsible_officer|technician|shift_supervisor>")
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

    def cmd_reset(self, message: dict) -> None:
        """/reset = same full clear as «🏠 منوی اصلی» (+ empty upload session)."""
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._clear_all_pending(uid)
        self.db.reset_session(user["bale_user_id"])
        self._reply(
            message,
            "همهٔ کارهای نیمه‌تمام (آپلود، گزارش‌ها، درخواست مواد، موجودی سایت، گزارش تاندیش، "
            "هدف اصلی، یادآورها) پاک شد. داده‌های ذخیره‌شده دست نخورده‌اند.",
            kb.main_menu(user),
        )

    # ---------- request-driven flow ----------
    def on_pick_file_type(
        self, message: dict, file_type: str, *, return_menu: str = "upload"
    ) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT):
            return
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(user["bale_user_id"])
        self._await_category_code.discard(uid)
        self._main_source_pending.pop(uid, None)
        self._upload_return_menu[uid] = return_menu if return_menu in {
            "upload", "main_source", "main"
        } else "upload"
        self.db.set_pending_file_type(user["bale_user_id"], file_type)
        label = FILE_TYPES[file_type]["label_fa"]
        if file_type == "product_inventory" and return_menu != "main_source":
            # «📥 به‌روزرسانی موجودی انبار» = stock update (not a full منبع اصلی replacement)
            self._reply(
                message,
                "📥 به‌روزرسانی موجودی انبار\n"
                "لطفاً فایل Excel «موجودی انبار» (خروجی سیستم انبار) را همین حالا به‌صورت Document ارسال کنید "
                "(پسوند .xlsx).\n\n"
                "• موجودی شناسه‌های موجود در منبع اصلی با این فایل به‌روز می‌شود.\n"
                "• شناسهٔ جدید فقط وقتی خودکار اضافه می‌شود که کد ۴ رقمی آن از قبل در منبع اصلی باشد "
                "و کد ۱۸۰۰ نباشد؛ بقیه رد و جداگانه اعلام می‌شوند.\n"
                "• پس از پردازش، «گزارش اقلام ورودی به انبار» (PDF و اکسل) ارسال می‌شود.\n\n"
                f"اگر منصرف شدید، «{kb.BTN_CANCEL_PENDING}» را بزنید.",
                kb.cancel_pending_menu(guide=True),
            )
            return
        self._reply(
            message,
            f"لطفاً فایل Excel مربوط به «{label}» را همین حالا به‌صورت Document ارسال کنید.\n"
            f"پسوند باید .xlsx باشد.\n"
            f"اگر منصرف شدید، «{kb.BTN_CANCEL_PENDING}» را بزنید.",
            kb.cancel_pending_menu(guide=file_type == "monthly_consumption"),
        )

    def _keyboard_for_upload_return(
        self, user: dict, *, default: str = "main", consume: bool = True
    ) -> dict:
        """Reply keyboard after Excel cancel/success based on pick context."""
        uid = str(user["bale_user_id"])
        if consume:
            dest = self._upload_return_menu.pop(uid, None) or default
        else:
            dest = self._upload_return_menu.get(uid) or default
        if dest == "main_source":
            return kb.main_source_file_menu(user)
        if dest == "upload":
            return kb.upload_files_menu(user)
        return kb.main_menu(user)

    def on_cancel_pending(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        had_main_source = (
            uid in self._main_source_pending or uid in self._await_category_code
        )
        self.db.set_pending_file_type(user["bale_user_id"], None)
        self._await_category_code.discard(uid)
        self._main_source_pending.pop(uid, None)
        session = self.db.get_or_create_session(user["bale_user_id"])
        if had_main_source:
            self._upload_return_menu.pop(uid, None)
            menu = kb.main_source_file_menu(user)
        else:
            menu = self._keyboard_for_upload_return(user, default="main")
        self._reply(message, "عملیات لغو شد.\n" + self._status_text(session, user), menu)

    def on_status(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        session = self.db.get_or_create_session(user["bale_user_id"])
        # Technicians keep their limited main menu; others get a file-entry picker
        # so status → choose type works without going through main_menu submenu.
        if not perm.can_any(user, (perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT)):
            menu = kb.main_menu(user)
        else:
            menu = kb.file_entry_menu(user)
        self._reply(message, self._status_text(session, user), menu)

    def on_document(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        session = self.db.get_or_create_session(user["bale_user_id"])
        pending = session.get("pending_file_type")
        # «ورود فایل اکسل منبع اصلی» (main-source submenu) = full-source upload; the
        # «موجودی انبار» / consumables buttons = stock update (t213u: no new codes).
        upload_origin = self._upload_return_menu.get(str(user["bale_user_id"]))
        if not perm.can_any(user, (perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT)):
            if pending:
                self.db.set_pending_file_type(user["bale_user_id"], None)
            self._deny_technician(message, user, perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT)
            return
        if not pending:
            self._reply(
                message,
                "ابتدا نوع ورود اطلاعات را از دکمه‌های زیر انتخاب کنید، سپس Excel را بفرستید.",
                kb.file_entry_menu(user),
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
            self._reply(message, user_errors.error_fa("دانلود فایل از بله ناموفق بود", exc))
            return

        # If a «مصرف ماهیانه / مواد مصرفی» upload actually matches the
        # منبع اصلی (product_inventory) cleaned format, treat it as a primary
        # inventory refresh so warehouse master data stays current.
        format_redirect_note = ""
        if pending == "monthly_consumption":
            try:
                peek = load_excel(dest, strict_tundish=False)
                if looks_like_product_inventory(peek):
                    pending = "product_inventory"
                    # Rename raw slot file to inventory name for clarity
                    inv_dest = dest_dir / "product_inventory.xlsx"
                    if dest.resolve() != inv_dest.resolve():
                        dest.replace(inv_dest)
                        dest = inv_dest
                    format_redirect_note = (
                        "\n(فایل با قالب منبع اصلی شناسایی شد — "
                        "جدول اصلی موجودی به‌روز شد.)"
                    )
                    logger.info(
                        "monthly/consumables upload redirected to product_inventory for user %s",
                        user["bale_user_id"],
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("inventory-format peek failed: %s", exc)

        allowlist = None
        if pending == "product_inventory":
            allowlist = self.db.active_category_code_set()
            if not allowlist:
                dest.unlink(missing_ok=True)
                self._reply(
                    message,
                    "لیست کدهای دسته‌بندی خالی است.\n"
                    f"ابتدا از «{kb.BTN_UPLOAD_MENU}» → «{kb.BTN_MAIN_SOURCE_FILE}» → «{kb.BTN_INV_ADD_CATEGORY}» "
                    "حداقل یک کد ۴ رقمی ثبت کنید، سپس دوباره فایل را بفرستید.",
                    self._keyboard_for_upload_return(user, default="main_source", consume=False),
                )
                return

        # Snapshot previous clean DF before extract overwrites the same clean_path.
        # For product_inventory also freeze the previous extract file so inbound
        # delta can compare against a readable pre-upload baseline later.
        slot_col = {
            "tank_consumption": "tank_path",
            "product_inventory": "inventory_path",
            "monthly_consumption": "monthly_path",
        }.get(pending)
        previous_clean = Path(session[slot_col]) if slot_col and session.get(slot_col) else None
        old_df = None
        prev_kept = 0
        if pending == "product_inventory":
            latest_inv = self.db.get_latest_extracted_any("product_inventory")
            if latest_inv and latest_inv.get("clean_path"):
                latest_path = Path(latest_inv["clean_path"])
                if latest_path.exists():
                    try:
                        snap_dir = latest_path.parent / "snapshots"
                        snap_dir.mkdir(parents=True, exist_ok=True)
                        snap_path = snap_dir / f"product_inventory_{latest_inv['id']}.xlsx"
                        if not snap_path.exists():
                            shutil.copy2(latest_path, snap_path)
                        self.db.update_extracted_clean_path(
                            int(latest_inv["id"]), str(snap_path)
                        )
                        previous_clean = snap_path
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "could not preserve inventory snapshot: %s", exc
                        )
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
            if pending == "product_inventory":
                menu = self._keyboard_for_upload_return(
                    user, default="main_source", consume=False
                )
            else:
                menu = kb.cancel_pending_menu()
            self._reply(message, str(exc), menu)
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("extract failed")
            dest.unlink(missing_ok=True)
            self._reply(message, user_errors.error_fa("استخراج داده از فایل ناموفق بود", exc), kb.cancel_pending_menu())
            return

        new_kept = int(result.kept_row_count)
        merge_note = ""
        skipped_note = ""
        # t221u: «📥 موجودی انبار» (stock update) vs authorized full منبع اصلی upload.
        # Only stock updates feed the inbound report / move its baseline.
        full_source = (
            pending == "product_inventory"
            and upload_origin == "main_source"
            and not format_redirect_note
            and perm.can(user, perm.MAIN_SOURCE_EDIT)
        )
        is_stock_upload = pending == "product_inventory" and not full_source
        uploaded_stock_df: pd.DataFrame | None = None
        inbound_rejected: list[dict] = []
        if is_stock_upload:
            try:
                uploaded_stock_df = pd.read_excel(result.clean_path, engine="openpyxl")
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not read uploaded stock frame for inbound: %s", exc)
        if old_df is not None and not old_df.empty:
            try:
                new_df = pd.read_excel(result.clean_path, engine="openpyxl")
                if pending == "product_inventory":
                    # منبع اصلی is the reference: a stock update never adds a new
                    # 4-digit code nor a NEW 1800 row; an authorized full-source
                    # upload may add both (shared rule in services.main_source).
                    new_df, skipped = main_source_svc.filter_inventory_upload(
                        old_df, new_df, allow_new_codes=full_source
                    )
                    skipped_note = main_source_svc.skipped_rows_note_fa(skipped)
                    if is_stock_upload:
                        inbound_rejected = list(skipped)
                    if skipped:
                        logger.info(
                            "inventory upload: %d rows skipped (full_source=%s)",
                            len(skipped), full_source,
                        )
                merged = merge_clean_frames(old_df, new_df, pending)
                if pending == "product_inventory":
                    from excel.id_parse import apply_id_segment_rule

                    merged, _old_mism = apply_id_segment_rule(merged)
                write_clean_excel(merged, result.clean_path, pending)
                result.kept_row_count = int(len(merged))
                merge_note = (
                    f"\nهمسان‌سازی: قبلی {prev_kept} + جدید {new_kept} → نهایی {result.kept_row_count}."
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("merge clean frames failed: %s", exc)
                merge_note = "\n(" + user_errors.error_fa("همسان‌سازی انجام نشد", exc) + ")"

        # Session slots point at CLEAN file so analytics/PDF use filtered data
        session = self.db.store_file_slot(
            user["bale_user_id"], pending, str(result.clean_path)
        )
        extract_id = self.db.save_extracted(
            bale_user_id=user["bale_user_id"],
            session_id=session["id"],
            file_type=pending,
            raw_path=str(result.raw_path),
            clean_path=str(result.clean_path),
            row_count=result.kept_row_count,
            columns=result.columns,
        )

        catalog_note = ""
        inbound_report = None
        inbound_note = ""
        if pending == "product_inventory":
            # automatic catalog sync after every منبع اصلی change (names updated too)
            from services.catalog_sync import note_fa as _cat_note, sync_after_change

            catalog_note = _cat_note(sync_after_change(self.db, result.clean_path))
            if is_stock_upload and uploaded_stock_df is not None:
                try:
                    live_after = pd.read_excel(result.clean_path, engine="openpyxl")
                    inbound_report = inbound_svc.record_stock_upload(
                        self.db,
                        uploaded=uploaded_stock_df,
                        live_before=old_df,
                        live_after=live_after,
                        rejected=inbound_rejected,
                        bale_user_id=user["bale_user_id"],
                        actor_display_name=resolve_display_name(user),
                        extract_id=extract_id,
                        raw_path=dest,
                    )
                    inbound_note = "\n\n" + inbound_svc.summary_text_fa(inbound_report)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("inbound report after stock upload failed")
                    inbound_note = "\n(" + user_errors.error_fa("گزارش اقلام ورودی ساخته نشد", exc) + ")"
            elif full_source:
                inbound_note = (
                    "\nآپلود کامل منبع اصلی — پایه «گزارش اقلام ورودی به انبار» تغییر نکرد."
                )
        elif pending == "monthly_consumption":
            try:
                monthly_clean = pd.read_excel(result.clean_path, engine="openpyxl")
                usage_sync = main_source_svc.sync_usage_from_monthly(
                    self.db,
                    monthly_clean,
                    bale_user_id=user["bale_user_id"],
                )
                if usage_sync.get("ok") and usage_sync.get("updated"):
                    catalog_note += (
                        f"\nمحل استفاده در منبع اصلی برای "
                        f"{usage_sync.get('updated', 0)} قلم به‌روز شد "
                        f"(از {usage_sync.get('mapped', 0)} نگاشت مصرف)."
                    )
                elif usage_sync.get("reason") == "no_inventory":
                    catalog_note += (
                        "\n(منبع اصلی برای پر کردن محل استفاده هنوز موجود نیست.)"
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("usage_location sync after monthly failed: %s", exc)

        label = FILE_TYPES[pending]["label_fa"]
        reasons = result.drop_reasons or {}
        if pending == "product_inventory" and reasons:
            dropped_note = (
                f"\nحذف‌شده‌ها: دسته نامجاز={reasons.get('wrong_category', 0)}، "
                f"شناسه نامعتبر={reasons.get('bad_id', 0)}، "
                f"دسته خالی={reasons.get('bad_category', 0)}، "
                f"موجودی نامعتبر={reasons.get('bad_quantity', 0)}"
            )
        elif result.dropped_row_count > 0:
            dropped_note = f"\n({result.dropped_row_count} ردیف اضافی/نامعتبر حذف شد)"
        else:
            dropped_note = ""
        if pending == "product_inventory":
            from excel.id_parse import segment_mismatch_note_fa

            dropped_note += segment_mismatch_note_fa(result.segment_mismatches)
            dropped_note += skipped_note
        extra_cols_note = ""
        if result.extra_columns_dropped:
            extra_cols_note = "\nستون‌های اضافی کنار گذاشته شد."
        reply_menu = self._keyboard_for_upload_return(user, default="upload")
        actor_line = f"ثبت‌کننده: {self._format_actor(user)}"
        if pending == "product_inventory":
            log_activity(self.db, user, "upload_product_inventory")
        elif pending == "monthly_consumption":
            log_activity(self.db, user, "upload_monthly_consumption")
        self._reply(
            message,
            (
                f"✅ فایل «{label}» دریافت شد.\n"
                f"از {result.raw_row_count} ردیف خام، {new_kept} ردیف نگه داشته شد."
                f"{merge_note}{dropped_note}{extra_cols_note}{catalog_note}{inbound_note}{format_redirect_note}\n"
                f"نسخه تمیز ذخیره و در پایگاه‌داده ثبت شد.\n"
                f"{actor_line}\n\n"
            )
            + self._status_text(session, user),
            reply_menu,
        )
        if inbound_report and inbound_report.get("id"):
            self._send_inbound_report_files(message, inbound_report)

    # ---------- 📊 گزارش جامع (merged «گزارش کلی مواد» + «PDF کامل تحلیل») ----------
    def on_comprehensive_prompt(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.REPORT_COMPREHENSIVE):
            return
        if not self._require_files(message, user, "full"):
            return
        self._ask_month_year_range(message, user, "comprehensive")

    # ---------- optional «بخش: همه/اسلب/بلوم/بیلت» step ----------
    _SECTION_STEP_MODES = frozenset({"daily", "comprehensive", "period"})

    def _ask_section_step(self, message: dict, user: dict, mode: str, **ctx: Any) -> None:
        uid = str(user["bale_user_id"])
        self._analysis_pending[uid] = {"mode": mode, "await": "section", **ctx}
        self._reply(
            message,
            "بخش (اختیاری): «همه بخش‌ها» یا یکی از اسلب / بلوم / بیلت را انتخاب کنید.",
            kb.section_step_menu(),
        )

    def on_section_step_text(self, message: dict, text: str) -> bool:
        uid = self._uid(message)
        pending = self._analysis_pending.get(uid)
        if not pending or pending.get("await") != "section":
            return False
        if text not in kb.SECTION_STEP_BUTTONS:
            self._reply(message, "یکی از گزینه‌های بخش را انتخاب کنید.", kb.section_step_menu())
            return True
        user = self._user_or_deny(message)
        if not user:
            return True
        section = kb.SECTION_STEP_BUTTONS[text]  # None = همه
        self._clear_analysis_pending(uid)
        mode = pending["mode"]
        if pending.get("start_ym"):
            self._run_month_ranged_report(
                message, user, mode, tuple(pending["start_ym"]), tuple(pending["end_ym"]),
                section=section or "",
            )
        else:
            self._run_ranged_analysis(
                message, user, mode, pending["start"], pending["end"], section=section or "",
            )
        return True

    @staticmethod
    def _section_frames(frames: dict, section: str | None) -> dict:
        return comprehensive_svc.section_frames(frames, section)

    @staticmethod
    def _section_label(section: str | None) -> str:
        return comprehensive_svc.section_label(section)

    # ---------- 🧮 نیاز مواد برای N تاندیش ----------
    def on_n_tundish_start(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.REPORT_N_TUNDISH):
            return
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(uid)
        self._n_tundish_pending[uid] = {"await": "section"}
        self._reply(
            message,
            f"{kb.BTN_N_TUNDISH}\n"
            "بخش را انتخاب کنید (اسلب / بلوم / بیلت).\n"
            "میان‌بُر: یک‌جا بنویسید، مثلاً «اسلب ۴».\n"
            "مبنا: همان نرخ‌ها و قواعد «اقلام بحرانی» از منبع اصلی؛ گزارش فقط‌خواندنی است.",
            kb.n_tundish_section_menu(),
        )

    def on_n_tundish_text(self, message: dict, text: str) -> bool:
        from services import n_tundish_report as nt

        uid = self._uid(message)
        p = self._n_tundish_pending.get(uid)
        if not p:
            return False
        step = p.get("await")
        if step == "section":
            short = nt.parse_shortcut(text)
            if short:
                p["section"], p["n"] = short
                p["await"] = "mode"
            elif text in nt.SECTION_BY_FA:
                p["section"] = nt.SECTION_BY_FA[text]
                p["await"] = "count"
                self._reply(
                    message,
                    f"تعداد تاندیش {nt.SECTIONS[p['section']]} (عدد صحیح مثبت) را انتخاب کنید یا بنویسید:",
                    kb.n_tundish_count_menu(),
                )
                return True
            else:
                self._reply(message, "یکی از بخش‌ها را انتخاب کنید یا مثلاً «اسلب ۴» بنویسید.", kb.n_tundish_section_menu())
                return True
        elif step == "count":
            short = nt.parse_shortcut(text)
            n = short[1] if short else nt.parse_count(text)
            if short:
                p["section"] = short[0]
            if n is None:
                self._reply(message, f"تعداد باید عدد صحیح مثبت (۱ تا {nt.MAX_N}) باشد.", kb.n_tundish_count_menu())
                return True
            p["n"] = n
            p["await"] = "mode"
        elif step == "mode":
            mode = {kb.BTN_NT_WITH: "with", kb.BTN_NT_WITHOUT: "without"}.get(text)
            if not mode:
                self._reply(message, f"«{kb.BTN_NT_WITH}» یا «{kb.BTN_NT_WITHOUT}» را انتخاب کنید.", kb.n_tundish_mode_menu())
                return True
            self._n_tundish_pending.pop(uid, None)
            self._run_n_tundish(message, p["section"], int(p["n"]), mode)
            return True
        if p.get("await") == "mode":
            self._reply(
                message,
                f"{p['n']} تاندیش {nt.SECTIONS[p['section']]} — حالت را انتخاب کنید:\n"
                f"• {kb.BTN_NT_WITH}: نرخ نوسازی + پچینگ\n• {kb.BTN_NT_WITHOUT}: فقط پچینگ\n"
                "(سطح ریخته‌گری × N در هر دو حالت)",
                kb.n_tundish_mode_menu(),
            )
        return True

    @heavy("گزارش نیاز N تاندیش", notice=False)
    def _run_n_tundish(self, message: dict, section: str, n: int, mode: str) -> None:
        from services import n_tundish_report as nt

        user = self._user_or_deny(message)
        if not user:
            return
        self._reply(message, "در حال ساخت گزارش…")
        try:
            res = nt.generate_files(
                self.db, user, section, n, mode, letterhead_path=self._letterhead_path()
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("n_tundish report failed")
            self._reply(message, user_errors.error_fa("خطا در ساخت گزارش", exc), kb.analytics_menu(user))
            return
        if res.error:
            self._reply(message, res.error, kb.analytics_menu(user))
            return
        chat = self._chat_id(message)
        for path, cap in ((res.pdf, res.title), (res.xlsx, f"نسخه اکسل — {res.title}")):
            try:
                self.client.send_document(chat, path, caption=cap)
            except Exception as exc:  # noqa: BLE001
                logger.warning("send n_tundish file failed: %s", exc)
        self._reply(message, res.bot_text(), kb.analytics_menu(user))
        log_activity(self.db, user, "report_n_tundish", section=section, n=n, renovation=mode)

    # ---------- 📦 جایگزینی کامل منبع اصلی (warning + confirm) ----------
    def on_full_replace_start(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.MAIN_SOURCE_EDIT):
            return
        if not perm.can(user, perm.MAIN_SOURCE_EDIT):
            self._reply(message, "جایگزینی کامل منبع اصلی برای نقش شما مجاز نیست.", kb.upload_files_menu(user))
            return
        uid = str(user["bale_user_id"])
        self._full_replace_confirm.add(uid)
        self._reply(
            message,
            "⚠️ جایگزینی کامل منبع اصلی\n"
            "فایل جدید مرجع همهٔ گزارش‌ها می‌شود: شناسه‌ها و کدهای دستهٔ جدید (و ردیف‌های ۱۸۰۰) "
            "اضافه می‌شوند و مقادیر فعلی با فایل جدید به‌روز می‌شوند.\n"
            "برای به‌روزرسانی معمول موجودی، از «" + kb.BTN_WAREHOUSE_STOCK + "» استفاده کنید.\n\n"
            f"ادامه می‌دهید؟ «{kb.BTN_FULL_REPLACE_CONFIRM}»",
            kb.full_replace_confirm_menu(),
        )

    def on_full_replace_confirm(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        if uid not in self._full_replace_confirm:
            self._reply(message, f"ابتدا «{kb.BTN_FULL_REPLACE}» را بزنید.", kb.main_source_file_menu(user))
            return
        self._full_replace_confirm.discard(uid)
        self.on_pick_file_type(message, "product_inventory", return_menu="main_source")

    # ---------- ❓ راهنمای تهیهٔ فایل ----------
    def on_file_guide(self, message: dict) -> None:
        from services import file_guides

        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        kind = self._file_guide_kind.get(uid)
        if not kind:
            pending = (self.db.get_or_create_session(uid) or {}).get("pending_file_type")
            if pending in ("product_inventory", "monthly_consumption"):
                kind = pending
        # keep the current keyboard: re-send the guide without changing nav state
        self.client.send_message(self._chat_id(message), file_guides.guide_text(kind))

    # ---------- ⚙️ تنظیمات → 🎨 ظاهر / 🔐 دسترسی نقش‌ها ----------
    def on_appearance_menu(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if not perm.can(user, perm.SETTINGS):
            self._reply(message, "این بخش فقط برای مالک یا مدیر است.", kb.main_menu(user))
            return
        self._bot_settings_pending.pop(str(user["bale_user_id"]), None)
        self._reply(
            message,
            "🎨 ظاهر — متن دعوت‌نامه، پیام خوشامد، لوگو و سربرگ PDF:",
            kb.appearance_menu(),
        )

    def on_role_permissions(self, message: dict) -> None:
        """«🔐 دسترسی نقش‌ها»: pick a role, then toggle features with inline checkboxes."""
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.ROLE_PERMISSIONS):
            return
        self._reply(
            message,
            "🔐 دسترسی نقش‌ها\n"
            "نقشی را که می‌خواهید تنظیم کنید انتخاب کنید. دسترسی‌های مالک قفل است؛ "
            "«کاربران» و «دسترسی نقش‌ها» همیشه فقط برای مالک و مدیر است.\n"
            "تغییرها فوراً در ربات و وب اعمال و در «📋 فعالیت کاربران» ثبت می‌شوند.\n\n"
            "وضعیت فعلی:\n" + perm.matrix_text_fa(),
            kb.role_perms_role_menu(),
        )

    def _role_perm_text(self, role: str) -> str:
        return (
            f"دسترسی‌های «{role_label(role)}» — روی هر مورد بزنید تا روشن/خاموش شود.\n"
            "✅ فعال | ⬜ غیرفعال | • = متفاوت با پیش‌فرض"
        )

    def on_role_perm_pick(self, message: dict, role: str) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.ROLE_PERMISSIONS):
            return
        self._reply(message, f"نقش «{role_label(role)}» انتخاب شد.", kb.role_perms_role_menu())
        self.client.send_message(
            self._chat_id(message),
            self._role_perm_text(role),
            reply_markup=kb.role_perms_inline(role, perm.matrix_rows(role)),
        )

    def _on_role_perm_callback(self, data: str, message: dict, user: dict, answer) -> None:
        parts = data[len(kb.CB_ROLE_PERM_PREFIX):].split("|", 1)
        if len(parts) != 2:
            answer()
            return
        role, feature = parts
        if feature == kb.RP_RESET:
            ok, msg = perm.reset_role(self.db, user, role)
        else:
            ok, msg = perm.toggle(self.db, user, role, feature)
        answer(msg, alert=not ok)
        if not ok:
            return
        chat = (message.get("chat") or {}).get("id")
        mid = message.get("message_id")
        if chat is not None and mid is not None:
            try:
                self.client.edit_message_reply_markup(
                    chat, int(mid), kb.role_perms_inline(role, perm.matrix_rows(role))
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("role perm keyboard refresh failed: %s", exc)

    # ---------- آپلود فایل / فایل منبع اصلی ----------
    def on_upload_menu(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT):
            return
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(user["bale_user_id"])
        self._await_category_code.discard(uid)
        self._main_source_pending.pop(uid, None)
        self._upload_return_menu.pop(uid, None)
        self.db.set_pending_file_type(user["bale_user_id"], None)
        self._reply(
            message,
            f"{kb.BTN_UPLOAD_MENU}\n"
            f"• {kb.BTN_WAREHOUSE_STOCK} — Excel انبار؛ فقط شناسه‌های موجود به‌روز می‌شوند "
            "(شناسهٔ جدید فقط با کد ۴رقمی موجود و غیر ۱۸۰۰)\n"
            f"• {kb.BTN_MONTHLY} — Excel مصرف ماهیانه\n"
            f"• {kb.BTN_MAIN_SOURCE_FILE} — جایگزینی کامل / دانلود / رکورد / کد دسته",
            kb.upload_files_menu(user),
        )

    def on_main_source_file_menu(self, message: dict) -> None:
        """Open flattened «فایل منبع اصلی» submenu (also legacy «منبع اصلی»)."""
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT):
            return
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(user["bale_user_id"])
        self._await_category_code.discard(uid)
        self._main_source_pending.pop(uid, None)
        codes = self.db.list_category_codes(active_only=True)
        hint = (
            f"تعداد کدهای فعال دسته‌بندی: {len(codes)}\n"
            "اگر لیست خالی است، قبل از آپلود Excel حداقل یک کد ۴ رقمی اضافه کنید."
        )
        self._reply(
            message,
            f"{kb.BTN_MAIN_SOURCE_FILE}\n" + hint,
            kb.main_source_file_menu(user),
        )

    def on_add_category_prompt(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.MAIN_SOURCE_EDIT):
            return
        self._await_category_code.add(str(user["bale_user_id"]))
        self._clear_analysis_pending(user["bale_user_id"])
        self.db.set_pending_file_type(user["bale_user_id"], None)
        self._reply(
            message,
            "کد دسته بندی ۴ رقمی را ارسال کنید (مثال: 1201).\n"
            f"برای انصراف «{kb.BTN_CANCEL_PENDING}» یا برای خروج «{kb.BTN_BACK}» / «{kb.BTN_HOME}» را بزنید.",
            kb.cancel_pending_menu(nav=True),
        )

    def on_category_code_text(self, message: dict, text: str) -> bool:
        """Handle pending category-code entry. Returns True if consumed."""
        uid = str(self._uid(message))
        if uid not in self._await_category_code:
            return False
        raw = kb.normalize_pending_text(text)
        # Cancel / help / back / menu — never treat as a 4-digit code
        if kb.is_pending_reserved_text(raw):
            if kb.is_pending_cancel_text(raw):
                self._await_category_code.discard(uid)
                user = self._user_or_deny(message)
                if user:
                    self.db.set_pending_file_type(user["bale_user_id"], None)
                    self._reply(
                        message,
                        "اضافه کردن کد دسته بندی لغو شد.",
                        kb.main_source_file_menu(user),
                    )
                return True
            # help / back / reset → let later keyboard handlers run
            return False
        user = self._user_or_deny(message)
        if not user:
            self._await_category_code.discard(uid)
            return True
        if self._deny_technician(message, user, perm.MAIN_SOURCE_EDIT):
            return True
        try:
            row = self.db.add_category_code(
                raw, created_by=user["bale_user_id"]
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
            kb.inventory_menu(user),
        )
        return True

    def on_list_categories(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT):
            return
        active_codes = self.db.list_category_codes(active_only=True)
        if not active_codes:
            self._reply(
                message,
                "هنوز هیچ کد دسته‌بندی فعالی ثبت نشده است.",
                kb.inventory_menu(user),
            )
            return

        table, has_extract, source = self._category_inventory_table(user)
        if has_extract:
            notice = f"منبع: {source or 'آخرین منبع اصلی تمیزشده'}"
        elif source == "کاتالوگ همگام‌شده":
            notice = (
                "استخراج فعلی منبع اصلی در دسترس نیست؛ شرح کالا از کاتالوگ است "
                "و مقدار تا آپلود منبع اصلی قابل نمایش نیست."
            )
        else:
            notice = (
                "هنوز منبع اصلی استخراج‌شده‌ای ندارید. برای جدول کامل، ابتدا فایل «منبع اصلی» "
                "را آپلود کنید."
            )
        rows = inventory_table_rows(table)
        # active codes with no item in منبع اصلی get one «—» placeholder row each, so the
        # PDF row count = items + empty codes (bug 7: 150 = 149 items + code 0922)
        empty_codes = (
            [str(c) for c in table.loc[table["_placeholder"].fillna(False).astype(bool), "category_code"]]
            if "_placeholder" in table.columns else []
        )
        n_items = len(rows) - len(empty_codes)
        if empty_codes:
            notice += (
                f"\n{n_items} قلم منبع اصلی + {len(empty_codes)} کد دستهٔ فعال بدون قلم "
                f"({'، '.join(empty_codes)}) با «—»."
            )
        if not rows:
            self._reply(
                message,
                "📋 لیست کد دسته‌بندی و موجودی\n" + notice + "\n\nلیست خالی است.",
                kb.inventory_menu(user),
            )
            return
        cols = ["کد دسته", "شرح کالا", "موجودی"]
        self._send_simple_pdf_report(
            message,
            title="لیست کد دسته‌بندی و موجودی",
            subtitle=notice,
            columns=cols,
            rows=rows,
            filename_stem="category_inventory",
            output_name="لیست_کد_دسته‌بندی_و_موجودی.pdf",
            caption=(
                f"📋 لیست کد دسته‌بندی و موجودی — {n_items} قلم"
                + (f" + {len(empty_codes)} کد دسته بدون قلم" if empty_codes else "")
            ),
            reply_ok=f"📋 جدول در PDF ارسال شد.\n{notice}",
            reply_markup=kb.inventory_menu(user),
            log_user=user,
            log_action="list_categories_pdf",
        )


    def _deny_main_source_edit(self, message: dict, user: dict) -> bool:
        """Only catalog-admin roles may edit/add/upload منبع اصلی via settings."""
        if perm.can(user, perm.MAIN_SOURCE_EDIT):
            return False
        self._main_source_pending.pop(str(user["bale_user_id"]), None)
        self._reply(
            message,
            "دسترسی ویرایش منبع اصلی برای نقش شما فعال نیست.",
            kb.inventory_menu(user),
        )
        return True

    def _clear_main_source_pending(self, uid: str) -> None:
        self._main_source_pending.pop(str(uid), None)

    def on_inv_download(self, message: dict) -> None:
        """Send current cleaned منبع اصلی (product_inventory) as Excel with Persian headers."""
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT):
            return
        menu = kb.main_source_file_menu(user)
        try:
            frame = load_primary_inventory(self.db, user)
        except Exception as exc:  # noqa: BLE001
            logger.exception("load primary inventory for download failed")
            self._reply(message, user_errors.error_fa("خطا در خواندن منبع اصلی", exc), menu)
            return
        if frame is None or getattr(frame, "empty", True):
            self._reply(
                message,
                "هنوز فایل منبع اصلی آپلود نشده است.\n"
                "ابتدا از «ورود فایل اکسل منبع اصلی» یا «موجودی انبار» فایل را بفرستید.",
                menu,
            )
            return
        try:
            out = REPORT_DIR / "منبع_اصلی.xlsx"
            export_dataframe_xlsx(
                frame,
                out,
                columns=list(INVENTORY_COLUMNS),
                header_map=FIELD_LABELS_FA,
                sheet_name="ریز اطلاعات",
            )
            self.client.send_document(
                self._chat_id(message),
                out,
                caption="فایل منبع اصلی (اکسل — نسخه تمیز فعلی)",
            )
            self._reply(
                message,
                f"✅ فایل منبع اصلی ارسال شد ({len(frame)} قلم/ردیف داده، بدون احتساب سطر عنوان).",
                menu,
            )
            log_activity(self.db, user, "download_primary_inventory")
        except Exception as exc:  # noqa: BLE001
            logger.exception("inv download failed")
            self._reply(message, user_errors.error_fa("خطا در ساخت اکسل منبع اصلی", exc), menu)


    def on_inv_edit_record_start(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.MAIN_SOURCE_EDIT) or self._deny_main_source_edit(message, user):
            return
        uid = str(user["bale_user_id"])
        frame = main_source_svc.load_primary_frame(self.db, bale_user_id=uid)
        if frame is None or frame.empty:
            self._reply(
                message,
                "منبع اصلی خالی است. ابتدا فایل را آپلود یا رکورد جدید اضافه کنید.",
                kb.inventory_edit_menu(user),
            )
            return
        preview = main_source_svc.list_ids_preview(frame, limit=25)
        self._main_source_pending[uid] = {"mode": "edit_pick_id"}
        body = "شناسه رکورد را ارسال کنید (یا یکی از موارد زیر):\n" + "\n".join(preview)
        if len(frame) > 25:
            body += f"\n… و {int(len(frame)) - 25} مورد دیگر"
        body += (
            f"\n\nبرای انصراف «{kb.BTN_CANCEL_PENDING}» را بزنید."
        )
        self._reply(message, body, kb.cancel_pending_menu())

    def on_inv_add_record_start(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.MAIN_SOURCE_EDIT) or self._deny_main_source_edit(message, user):
            return
        uid = str(user["bale_user_id"])
        fields = [c for c in main_source_svc.INVENTORY_COLUMNS]
        first = fields[0]
        self._main_source_pending[uid] = {
            "mode": "add_fields",
            "fields": fields,
            "field_idx": 0,
            "draft": {},
        }
        label = main_source_svc.FIELD_LABELS_FA.get(first, first)
        self._reply(
            message,
            (
                f"اضافه کردن رکورد جدید به منبع اصلی.\n"
                f"مقدار «{label}»{main_source_svc.FIELD_HINTS_FA.get(first, '')} را بفرستید.\n"
                f"برای رد کردن فیلدهای اختیاری «-» بفرستید.\n"
                f"برای انصراف «{kb.BTN_CANCEL_PENDING}» را بزنید."
            ),
            kb.cancel_pending_menu(),
        )

    def on_main_source_flow_text(self, message: dict, text: str) -> bool:
        """Handle edit/add record free-text. Returns True if consumed."""
        uid = str(self._uid(message))
        pending = self._main_source_pending.get(uid)
        if not pending:
            return False
        user = self._user_or_deny(message)
        if not user:
            self._clear_main_source_pending(uid)
            return True
        if self._deny_technician(message, user, perm.MAIN_SOURCE_EDIT) or self._deny_main_source_edit(message, user):
            return True
        raw_nav = kb.normalize_pending_text(text)
        if kb.is_pending_reserved_text(raw_nav):
            if kb.is_pending_cancel_text(raw_nav):
                mode = pending.get("mode") or ""
                self._clear_main_source_pending(uid)
                self.db.set_pending_file_type(user["bale_user_id"], None)
                if mode == "add_fields" or str(mode).startswith("add"):
                    msg = "اضافه کردن رکورد لغو شد."
                else:
                    msg = "ویرایش منبع اصلی لغو شد."
                self._reply(message, msg, kb.inventory_edit_menu(user))
                return True
            # help / back / reset → let later keyboard handlers run
            return False

        mode = pending.get("mode")
        if mode == "edit_pick_id":
            item_id = text.strip().split()[0]
            # allow "ID — name" paste from preview
            if "—" in item_id:
                item_id = item_id.split("—", 1)[0].strip()
            if " - " in item_id and len(item_id) > 20:
                item_id = item_id.split(" - ", 1)[0].strip()
            frame = main_source_svc.load_primary_frame(self.db, bale_user_id=uid)
            if frame is None:
                frame = pd.DataFrame()
            _idx, row = main_source_svc.find_row_by_id(frame, item_id)
            if row is None:
                self._reply(
                    message,
                    f"شناسه «{item_id}» یافت نشد. دوباره شناسه را بفرستید یا انصراف بزنید.",
                    kb.cancel_pending_menu(),
                )
                return True
            self._main_source_pending[uid] = {
                "mode": "edit_fields",
                "item_id": str(row.get("id") or item_id),
            }
            fields_hint = "، ".join(
                f"{main_source_svc.FIELD_LABELS_FA.get(c, c)}"
                for c in main_source_svc.INVENTORY_COLUMNS
                if c != "id"
            )
            self._reply(
                message,
                (
                    "رکورد فعلی:\n"
                    + main_source_svc.format_row_fa(row)
                    + "\n\nبرای ویرایش، یک یا چند خط به صورت "
                    "«نام فیلد=مقدار» بفرستید.\n"
                    f"فیلدها: {fields_hint}\n"
                    "مثال:\nموجودی=120\nمحل استفاده=اسلب، بیلت\nکلید واژه=نسوز"
                ),
                kb.cancel_pending_menu(),
            )
            return True

        if mode == "edit_fields":
            item_id = pending.get("item_id") or ""
            updates: dict[str, object] = {}
            # Accept FA labels or English keys
            label_to_key = {v: k for k, v in main_source_svc.FIELD_LABELS_FA.items()}
            for line in text.splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                left, right = line.split("=", 1)
                key = left.strip()
                key = label_to_key.get(key, key)
                if key == "id":
                    continue
                if key in main_source_svc.INVENTORY_COLUMNS:
                    updates[key] = right.strip()
            if not updates:
                self._reply(
                    message,
                    "هیچ فیلد معتبری یافت نشد. قالب: «نام فیلد=مقدار» (مثال: موجودی=120)",
                    kb.cancel_pending_menu(),
                )
                return True
            try:
                result = main_source_svc.upsert_row(
                    self.db, item_id, updates, bale_user_id=uid
                )
            except (KeyError, ValueError) as exc:
                self._reply(message, str(exc), kb.cancel_pending_menu())
                return True
            self._clear_main_source_pending(uid)
            log_activity(
                self.db, user, "edit_main_source_record",
                item_id=str(result.get("id") or item_id),
                changes_fa=main_source_svc.changes_fa(result.get("changes") or []),
            )
            self._reply(
                message,
                (
                    f"✅ رکورد «{result.get('id')}» به‌روز شد.\n"
                    + main_source_svc.format_row_fa(result.get("row") or {})
                ),
                kb.inventory_edit_menu(user),
            )
            return True

        if mode == "add_confirm_overwrite":
            ans = text.strip().casefold()
            if ans not in {"بله", "بلی", "yes", "y", "آره", "اره"}:
                self._clear_main_source_pending(uid)
                self._reply(message, "افزودن رکورد لغو شد (شناسه تکراری).", kb.inventory_edit_menu(user))
                return True
            draft = dict(pending.get("draft") or {})
            try:
                result = main_source_svc.add_row(
                    self.db, draft, bale_user_id=uid, allow_update=True
                )
            except (KeyError, ValueError) as exc:
                self._clear_main_source_pending(uid)
                self._reply(message, str(exc), kb.inventory_edit_menu(user))
                return True
            self._clear_main_source_pending(uid)
            log_activity(self.db, user, "add_main_source_record_overwrite", item_id=str(result.get("id") or ""))
            self._reply(
                message,
                "✅ رکورد جایگزین شد.\n" + main_source_svc.format_row_fa(result.get("row") or {}),
                kb.inventory_edit_menu(user),
            )
            return True

        if mode == "add_fields":
            fields = list(pending.get("fields") or main_source_svc.INVENTORY_COLUMNS)
            idx = int(pending.get("field_idx") or 0)
            draft = dict(pending.get("draft") or {})
            if idx >= len(fields):
                self._clear_main_source_pending(uid)
                self._reply(message, "وضعیت نامعتبر؛ دوباره شروع کنید.", kb.inventory_edit_menu(user))
                return True
            field = fields[idx]
            raw = text.strip()
            if raw == "-":
                raw = ""
            if field in {"id", "category_code", "product_name", "quantity"} and not raw:
                label = main_source_svc.FIELD_LABELS_FA.get(field, field)
                self._reply(
                    message,
                    f"«{label}» الزامی است. دوباره مقدار را بفرستید.",
                    kb.cancel_pending_menu(),
                )
                return True
            draft[field] = raw
            idx += 1
            if idx >= len(fields):
                try:
                    result = main_source_svc.add_row(
                        self.db, draft, bale_user_id=uid, allow_update=False
                    )
                except ValueError as exc:
                    msg = str(exc)
                    if "از قبل در منبع اصلی" in msg:
                        self._main_source_pending[uid] = {
                            "mode": "add_confirm_overwrite",
                            "draft": draft,
                            "fields": fields,
                        }
                        self._reply(
                            message,
                            msg + "\n\nبرای جایگزینی کامل رکورد «بله» بفرستید؛ وگرنه انصراف.",
                            kb.cancel_pending_menu(),
                        )
                        return True
                    self._reply(message, msg, kb.cancel_pending_menu())
                    self._main_source_pending[uid] = {
                        "mode": "add_fields",
                        "fields": fields,
                        "field_idx": 0,
                        "draft": {},
                    }
                    return True
                except KeyError as exc:
                    self._reply(message, str(exc), kb.cancel_pending_menu())
                    self._main_source_pending[uid] = {
                        "mode": "add_fields",
                        "fields": fields,
                        "field_idx": 0,
                        "draft": {},
                    }
                    return True
                self._clear_main_source_pending(uid)
                log_activity(
                    self.db, user, "add_main_source_record", item_id=str(result.get("id") or ""),
                    action_fa="به‌روزرسانی" if result.get("action") == "updated" else "رکورد جدید",
                )
                action = "به‌روز" if result.get("action") == "updated" else "اضافه"
                self._reply(
                    message,
                    (
                        f"✅ رکورد {action} شد.\n"
                        + main_source_svc.format_row_fa(result.get("row") or {})
                    ),
                    kb.inventory_edit_menu(user),
                )
                return True
            pending["draft"] = draft
            pending["field_idx"] = idx
            self._main_source_pending[uid] = pending
            nxt = fields[idx]
            label = main_source_svc.FIELD_LABELS_FA.get(nxt, nxt)
            optional = (
                ""
                if nxt in {"id", "category_code", "product_name", "quantity"}
                else " (اختیاری — با «-» رد کنید)"
            )
            self._reply(
                message,
                f"مقدار «{label}»{main_source_svc.FIELD_HINTS_FA.get(nxt, '')}{optional} را بفرستید.",
                kb.cancel_pending_menu(),
            )
            return True

        # menu mode — ignore stray text
        return False


    # ---------- analytics ----------

    def _build_analytics_bundle(
        self,
        frames: dict,
        *,
        start: date | None = None,
        end: date | None = None,
        forecast_days: float | None = None,
    ) -> dict[str, Any]:
        """Shared with the web (services.comprehensive_report.build_bundle)."""
        return comprehensive_svc.build_bundle(
            self.db, frames, start=start, end=end, forecast_days=forecast_days
        )

    def on_analytics_menu(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, *perm.REPORT_FEATURES):
            return
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(uid)
        self._clear_critical_pending(uid)
        self._n_tundish_pending.pop(uid, None)
        self.main_goal_report.clear(uid)
        session = self.db.get_or_create_session(user["bale_user_id"])
        done = self._effective_completeness(user, session)
        lines = [
            kb.BTN_ANALYTICS,
            "",
            self._status_text(session, user),
            "",
            "یک گزارش را انتخاب کنید:",
        ]
        if not any(
            done.get(k)
            for k in (
                "product_inventory",
                "monthly_consumption",
                "tank_consumption",
                "site_stock",
            )
        ):
            lines.append(
                "\nهنوز داده‌ای نیست — منبع اصلی / مصرف ماهیانه را آپلود کنید "
                "یا موجودی روزانه سایت را تعاملی وارد کنید."
            )
        self._reply(message, "\n".join(lines), kb.analytics_menu(user))

    def on_daily_report(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if not self._require_files(message, user, "daily"):
            return
        self._ask_month_year_range(message, user, "daily")


    def on_critical_items_menu(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.REPORT_CRITICAL):
            return
        uid = str(user["bale_user_id"])
        self._clear_critical_pending(uid)
        self._clear_analysis_pending(uid)
        latest = self.db.list_monthly_tundish_counts(limit=1)
        extra = ""
        if latest:
            r = latest[0]
            from bot.jalali import format_month_year

            label = format_month_year(int(r["jalali_year"]), int(r["jalali_month"]), named=True)
            extra = (
                f"\nآخرین ثبت دستی: {label} — بیلت {r['count_billet']}، "
                f"بلوم {r['count_bloom']}، اسلب {r['count_slab']}"
                + (" (در مبنا استفاده نمی‌شود)" if int(r.get("exclude_from_basis") or 0) else "")
            )
        from analytics.critical_items import horizon_header_note
        from services.critical_items_report import critical_basis

        basis = critical_basis(self.db)
        if basis is not None:
            extra += f"\nمبنای گزارش امروز: {horizon_header_note(basis)}"
        self._reply(
            message,
            f"{kb.BTN_CRITICAL_ITEMS}\n"
            "۱) (اختیاری) تعداد تاندیش بیلت/بلوم/اسلب ماه را ثبت کنید — فقط وقتی لاگ توالی تاندیش نیست\n"
            "۲) گزارش را بگیرید: PDF اصلی (فقط اقلام شرکت)، PDF جداگانه "
            "اقلام پیمانکار و اکسل با دو شیت جدا؛ حالت "
            "«با نوسازی» یا «بدون نوسازی» را انتخاب کنید\n"
            "تاریخ گزارش = امروز؛ مبنا: میانگین تعداد تاندیش ۳ ماه کامل گذشته (لاگ توالی "
            "تاندیش، در نبود آن ثبت دستی)؛ نیاز = مصرف پیش‌بینی‌شده در افق "
            "(داخلی ۳ ماه، وارداتی ۶ ماه) − موجودی؛ فقط اقلام با نیاز مثبت.\n"
            "نقش‌های مجاز: مالک، مدیر، کاردان مسئول."
            f"{extra}",
            kb.critical_items_menu(),
        )

    def on_critical_counts_start(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.REPORT_CRITICAL):
            return
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(uid)
        self._critical_pending[uid] = {
            "mode": "counts",
            "await": "year",
        }
        years = year_choices_around()
        self._reply(
            message,
            "سال شمسی مورد نظر برای تعداد تاندیش را انتخاب کنید:",
            kb.year_picker_menu(years),
        )

    def on_critical_report_start(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.REPORT_CRITICAL):
            return
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(uid)
        # No month selection: report date = today, basis = 3 complete months before it.
        self._critical_pending[uid] = {
            "mode": "report",
            "await": "reno_mode",
        }
        self._ask_critical_reno_mode(message)

    def on_critical_flow_text(self, message: dict, text: str) -> bool:
        """Consume year/month/count steps for اقلام بحرانی. Returns True if handled."""
        uid = self._uid(message)
        pending = self._critical_pending.get(uid)
        if not pending:
            return False
        await_kind = pending.get("await")
        if await_kind not in {
            "year",
            "month",
            "reno_mode",
            "count_billet",
            "count_bloom",
            "count_slab",
        }:
            return False

        user = self._user_or_deny(message)
        if not user:
            return True
        if self._deny_technician(message, user, perm.REPORT_CRITICAL):
            self._clear_critical_pending(uid)
            return True

        # Back / cancel from critical submenu
        if text in {
            kb.BTN_BACK_CRITICAL,
            kb.BTN_BACK_ANALYTICS,
            kb.BTN_BACK_MAIN,
            kb.BTN_BACK_PREV,
            kb.BTN_CANCEL_PENDING,
            kb.BTN_CRITICAL_ITEMS,
            kb.BTN_CRITICAL_COUNTS,
            kb.BTN_CRITICAL_REPORT,
        }:
            return False  # let button handlers take over

        mode = pending.get("mode")
        raw = (text or "").strip()

        if await_kind == "year":
            digits = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
            if not digits.isdigit() or len(digits) != 4:
                self._reply(
                    message,
                    "سال را از دکمه‌ها انتخاب کنید یا یک سال چهاررقمی بفرستید.",
                    kb.year_picker_menu(year_choices_around()),
                )
                return True
            pending["year"] = int(digits)
            pending["await"] = "month"
            self._critical_pending[uid] = pending
            self._reply(
                message,
                f"ماه گزارش برای سال {digits} را انتخاب کنید:",
                kb.month_picker_menu(),
            )
            return True

        if await_kind == "month":
            from bot.jalali import PERSIAN_MONTH_NAME_TO_NUM, PERSIAN_MONTH_NAMES

            month = PERSIAN_MONTH_NAME_TO_NUM.get(raw)
            if month is None:
                digits = raw.translate(
                    str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
                )
                if digits.isdigit() and 1 <= int(digits) <= 12:
                    month = int(digits)
            if month is None:
                self._reply(
                    message,
                    "ماه را از دکمه‌ها انتخاب کنید.",
                    kb.month_picker_menu(),
                )
                return True
            pending["month"] = int(month)
            year = int(pending["year"])
            if mode == "report":  # legacy pending state — reports no longer take a month
                pending["await"] = "reno_mode"
                self._critical_pending[uid] = pending
                self._ask_critical_reno_mode(message)
                return True
            # counts mode — load existing and ask billet
            existing = self.db.get_monthly_tundish_counts(year, int(month))
            hint = ""
            if existing:
                hint = (
                    f"\nمقادیر فعلی: بیلت {existing['count_billet']}، "
                    f"بلوم {existing['count_bloom']}، اسلب {existing['count_slab']}"
                )
            pending["await"] = "count_billet"
            self._critical_pending[uid] = pending
            name = PERSIAN_MONTH_NAMES.get(int(month), str(month))
            self._reply(
                message,
                f"تعداد تاندیش بیلت در {name} {year} را بفرستید (عدد صحیح ≥ ۰):{hint}",
                kb.critical_items_menu(),
            )
            return True

        if await_kind == "reno_mode":
            from analytics.critical_items import RENO_WITH, RENO_WITHOUT

            norm = raw.replace("🔧", "").replace("🩹", "").strip()
            if norm == kb.BTN_CRITICAL_RENO_WITHOUT:
                reno = RENO_WITHOUT
            elif norm == kb.BTN_CRITICAL_RENO_WITH:
                reno = RENO_WITH
            else:
                self._ask_critical_reno_mode(message, retry=True)
                return True
            self._run_critical_items_report(message, user, reno_mode=reno)
            return True

        if await_kind in {"count_billet", "count_bloom", "count_slab"}:
            digits = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
            if not digits.isdigit():
                label = {
                    "count_billet": "بیلت",
                    "count_bloom": "بلوم",
                    "count_slab": "اسلب",
                }[await_kind]
                self._reply(
                    message,
                    f"لطفاً تعداد تاندیش {label} را به‌صورت عدد صحیح (≥ ۰) بفرستید.",
                    kb.critical_items_menu(),
                )
                return True
            val = int(digits)
            pending[await_kind] = val
            if await_kind == "count_billet":
                pending["await"] = "count_bloom"
                self._critical_pending[uid] = pending
                self._reply(
                    message,
                    "تعداد تاندیش بلوم را بفرستید (عدد صحیح ≥ ۰):",
                    kb.critical_items_menu(),
                )
                return True
            if await_kind == "count_bloom":
                pending["await"] = "count_slab"
                self._critical_pending[uid] = pending
                self._reply(
                    message,
                    "تعداد تاندیش اسلب را بفرستید (عدد صحیح ≥ ۰):",
                    kb.critical_items_menu(),
                )
                return True
            # count_slab — save
            year = int(pending["year"])
            month = int(pending["month"])
            billet = int(pending["count_billet"])
            bloom = int(pending["count_bloom"])
            slab = val
            try:
                saved = self.db.upsert_monthly_tundish_counts(
                    jalali_year=year,
                    jalali_month=month,
                    count_billet=billet,
                    count_bloom=bloom,
                    count_slab=slab,
                    updated_by=user["bale_user_id"],
                )
            except ValueError as exc:
                self._clear_critical_pending(uid)
                self._reply(message, str(exc), kb.critical_items_menu())
                return True
            self._clear_critical_pending(uid)
            from bot.jalali import format_month_year

            label = format_month_year(year, month, named=True)
            log_activity(
                self.db,
                user,
                "critical_tundish_counts_saved",
                jalali_year=year,
                jalali_month=month,
            )
            self._reply(
                message,
                f"✅ تعداد تاندیش برای {label} ذخیره شد.\n"
                f"بیلت: {saved.get('count_billet', billet)} | "
                f"بلوم: {saved.get('count_bloom', bloom)} | "
                f"اسلب: {saved.get('count_slab', slab)}\n"
                "می‌توانید «تولید گزارش اقلام بحرانی» را بزنید.",
                kb.critical_items_menu(),
            )
            return True

        return False

    def _ask_critical_reno_mode(self, message: dict, *, retry: bool = False) -> None:
        """Inline «با نوسازی» / «بدون نوسازی» choice (typed label also accepted)."""
        from analytics.critical_items import horizon_header_note
        from services.critical_items_report import critical_basis

        basis = critical_basis(self.db)
        label = basis.report_date if basis is not None else "امروز"
        note = f"{horizon_header_note(basis)}\n" if basis is not None else ""
        head = "لطفاً یکی از دو دکمه را بزنید.\n" if retry else ""
        self._reply(
            message,
            f"{head}{note}حالت محاسبه نیاز اقلام بحرانی (تاریخ گزارش {label}) را انتخاب کنید:\n"
            "• با نوسازی: نرخ نوسازی + پچینگ (+ سطح ریخته‌گری)\n"
            "• بدون نوسازی: فقط پچینگ (+ سطح ریخته‌گری)؛ اقلامی که فقط نرخ نوسازی "
            "دارند فهرست نمی‌شوند.",
            kb.critical_reno_inline_keyboard(),
        )

    def _on_critical_reno_callback(self, cq_data: str, message: dict, user: dict, answer) -> None:
        from analytics.critical_items import RENO_LABEL_FA, normalize_reno_mode

        uid = str(user["bale_user_id"])
        pending = self._critical_pending.get(uid)
        if not pending or pending.get("await") != "reno_mode":
            answer("این انتخاب منقضی شده؛ دوباره «تولید گزارش اقلام بحرانی» را بزنید.", alert=True)
            return
        if self._deny_technician(message, user, perm.REPORT_CRITICAL):
            self._clear_critical_pending(uid)
            answer()
            return
        reno = normalize_reno_mode(cq_data[len(kb.CB_CRITICAL_RENO_PREFIX):])
        answer(f"حالت: {RENO_LABEL_FA[reno]}")
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id is not None and message.get("message_id") is not None:
            try:
                self.client.edit_message_reply_markup(
                    chat_id, int(message["message_id"]), {"inline_keyboard": []}
                )
            except BaleAPIError:
                pass
        self._run_critical_items_report(message, user, reno_mode=reno)

    @heavy("گزارش اقلام بحرانی")
    def _run_critical_items_report(
        self, message: dict, user: dict, *, reno_mode: str = "with"
    ) -> None:
        """Send company report, then contractor report, then the 2-sheet xlsx.

        All building is in the shared ``services.critical_items_report`` (same
        code path as the web panel; ledger applied once by load_primary_inventory).
        """
        from analytics.critical_items import RENO_LABEL_FA, normalize_reno_mode
        from services.critical_items_report import (
            SEGMENT_COMPANY,
            SEGMENT_CONTRACTOR,
            generate_critical_items_files,
        )

        reno_mode = normalize_reno_mode(reno_mode)
        reno_label = RENO_LABEL_FA[reno_mode]

        uid = str(user["bale_user_id"])
        self._clear_critical_pending(uid)
        menu = kb.critical_items_menu()
        try:
            res = generate_critical_items_files(
                self.db,
                user,
                output_dir=REPORT_DIR / "critical_items" / uid,
                file_prefix="لیست_اقلام_بحرانی",
                letterhead_path=self._letterhead_path(),
                reno_mode=reno_mode,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("critical items report failed")
            self._reply(message, user_errors.error_fa("خطا در تولید گزارش اقلام بحرانی", exc), menu)
            return
        if res.error:
            text = res.error
            if text.startswith("منبع اصلی"):
                text = "منبع اصلی یافت نشد. ابتدا فایل منبع اصلی را آپلود کنید."
            elif text.startswith("قلمی"):
                text = (
                    "قلمی با کسری (نیاز مثبت) در افق ۳ ماه (داخلی) / ۶ ماه (وارداتی) "
                    f"({reno_label}) یافت نشد.\n{res.header_note}"
                )
            self._reply(message, text, menu)
            return

        chat_id = self._chat_id(message)
        sent: list[str] = []
        try:
            for seg, tag in ((SEGMENT_COMPANY, "۱) گزارش اصلی — اقلام شرکت"),
                             (SEGMENT_CONTRACTOR, "۲) گزارش جداگانه — اقلام پیمانکار")):
                path = res.pdfs.get(seg)
                if path is None:
                    continue
                self.client.send_document(
                    chat_id, path, caption=f"{tag}\n{res.titles.get(seg, '')}"
                )
                sent.append(seg)
            if res.xlsx is not None:
                self.client.send_document(
                    chat_id,
                    res.xlsx,
                    caption=(
                        f"نسخه اکسل اقلام بحرانی ({reno_label}) — "
                        "شیت «شرکت» و شیت «پیمانکار» جدا"
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("critical items send failed")
            self._reply(message, user_errors.error_fa("خطا در ارسال گزارش اقلام بحرانی", exc), menu)
            return
        from services.critical_items_report import critical_items_bot_lines

        label = res.counts.report_date if res.counts is not None else ""
        lines = [f"گزارش اقلام بحرانی (تاریخ گزارش {label}) — حالت «{reno_label}» ارسال شد.", res.header_note]
        lines.extend(critical_items_bot_lines(res))
        self._reply(message, "\n".join(lines), menu)
        if SEGMENT_COMPANY in sent:
            log_activity(self.db, user, "report_critical_items", renovation=reno_mode)
        if SEGMENT_CONTRACTOR in sent:
            log_activity(
                self.db, user, "report_critical_items_contractor", renovation=reno_mode
            )

    def on_remaining_critical(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if not self._require_files(message, user, "remaining_critical"):
            return
        # Snapshot inventory is point-in-time; month range filters consumption rates.
        self._ask_month_year_range(message, user, "remaining")

    def on_surplus_report(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        if not self._require_files(message, user, "surplus"):
            return
        # Inventory snapshot + rates from monthly/tank filtered by selected months.
        self._ask_month_year_range(message, user, "surplus")

    def on_inbound_report(self, message: dict, report_id: int | None = None) -> None:
        """«📥 گزارش اقلام ورودی به انبار» — stored report per stock upload (t221u).

        No recompute: shows the latest stored report (or ``report_id``) as summary
        + PDF + XLSX, and a keyboard of earlier reports listed by upload date.
        """
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.REPORT_INBOUND):
            return
        history = inbound_svc.list_reports(self.db, limit=11)
        if report_id is not None:
            report = inbound_svc.get_report(self.db, int(report_id))
        else:
            report = inbound_svc.latest_report(self.db)
        if not report:
            self._reply(
                message,
                "هنوز گزارش اقلام ورودی ثبت نشده است.\n"
                f"پس از هر آپلود «{kb.BTN_WAREHOUSE_STOCK}» گزارش به‌طور خودکار ساخته و ذخیره می‌شود.",
                kb.analytics_menu(user),
            )
            return
        others = [h for h in history if int(h["id"]) != int(report["id"])][:10]
        menu = kb.inbound_history_menu(
            [inbound_svc.history_button_label(h) for h in others]
        )
        self._reply(message, inbound_svc.summary_text_fa(report), menu)
        self._send_inbound_report_files(message, report)
        log_activity(self.db, user, "report_inbound", report_id=int(report["id"]))

    def _send_inbound_report_files(self, message: dict, report: dict) -> None:
        """PDF (A4 portrait + letterhead) + XLSX of a stored inbound report."""
        try:
            pdf_path, xlsx_path = inbound_svc.build_report_files(self.db, report)
        except Exception as exc:  # noqa: BLE001
            logger.exception("inbound report files failed")
            self._reply(message, user_errors.error_fa("خطا در تولید فایل گزارش اقلام ورودی", exc))
            return
        for path, cap in (
            (pdf_path, inbound_svc.caption_fa(report)),
            (xlsx_path, inbound_svc.caption_fa(report, excel=True)),
        ):
            try:
                self.client.send_document(self._chat_id(message), path, caption=cap)
            except Exception as exc:  # noqa: BLE001
                logger.warning("send inbound file failed (%s): %s", path, exc)

    @staticmethod
    def _inbound_history_id(text: str) -> int | None:
        if not text.startswith(inbound_svc.HISTORY_PREFIX):
            return None
        m = re.search(r"\(#(\d+)\)\s*$", text)
        return int(m.group(1)) if m else None

    def _ask_month_year_range(self, message: dict, user: dict, mode: str) -> None:
        """Central prompt: every time-based report asks Jalali month+year from–to first."""
        self._analysis_pending[user["bale_user_id"]] = {
            "mode": mode,
            "await": "month_range",
        }
        hint = (
            "بازه ماه و سال گزارش را انتخاب کنید (هجری شمسی):\n"
            f"• {kb.BTN_MY_CURRENT}\n"
            f"• {kb.BTN_MY_3}\n"
            f"• {kb.BTN_MY_YTD}\n"
            f"• {kb.BTN_MY_CUSTOM} — انتخاب سال/ماه با دکمه‌ها\n"
            f"• {kb.BTN_MY_TYPED} — سپس پیام بفرستید:\n"
            "  از YYYY/MM تا YYYY/MM\n"
            "  یا: از فروردین 1405 تا شهریور 1405\n"
            "مثال: از 1405/01 تا 1405/06"
        )
        include_day = mode == "period"
        if include_day:
            hint += (
                f"\n\nبرای گزارش مصرف بازه‌ای می‌توانید «{kb.BTN_MY_DAY_ADV}» "
                "را برای بازه روزانه بزنید."
            )
        self._reply(
            message,
            hint,
            kb.month_year_range_menu(include_day_advanced=include_day),
        )

    def _ask_date_range(self, message: dict, user: dict, mode: str) -> None:
        """Day-level range (advanced option for period reports)."""
        self._analysis_pending[user["bale_user_id"]] = {"mode": mode, "await": "range"}
        hint = (
            "بازه روزانه را انتخاب کنید:\n"
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
        # rebuilt report: site-stock diffs (DB) + main-goal monthly tundish tables —
        # no technician / historical-consumption Excel is required (bug 1)
        if self._deny_technician(message, user, *perm.REPORT_FEATURES):
            return
        from services import period_consumption as pc

        av = pc.data_availability(self.db)
        if not pc.has_any_data(av):
            self._reply(
                message,
                "📅 گزارش مصرف بازه‌ای هنوز دادهٔ کافی ندارد.\n" + pc.availability_text_fa(av)
                + f"\n\nموجودی روزانه را از «{kb.BTN_SITE_STOCK}» ثبت کنید یا جدول مصرف تاندیش ماهانه را در «هدف اصلی» وارد کنید.",
                kb.analytics_menu(user),
            )
            return
        self._ask_month_year_range(message, user, "period")

    def on_user_activity_report(self, message: dict) -> None:
        """«📋 فعالیت کاربران» (⚙️ تنظیمات, owner/manager) for a Jalali month range."""
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.USER_ACTIVITY):
            return
        if not perm.can(user, perm.USER_ACTIVITY):
            self._reply(message, "فعالیت کاربران فقط برای مالک یا مدیر است.", kb.main_menu(user))
            return
        self._ask_month_year_range(message, user, "user_activity")

    def _gregorian_range_to_utc_iso(
        self, start_g: date, end_g: date
    ) -> tuple[str, str]:
        """Inclusive Tehran-local calendar days → UTC ISO bounds for created_at filter."""
        tehran = ZoneInfo("Asia/Tehran")
        start_local = datetime.combine(start_g, time.min, tzinfo=tehran)
        # inclusive end-of-day
        end_local = datetime.combine(end_g, time(23, 59, 59), tzinfo=tehran)
        return (
            start_local.astimezone(timezone.utc).isoformat(),
            end_local.astimezone(timezone.utc).isoformat(),
        )

    def _run_user_activity_report(
        self,
        message: dict,
        user: dict,
        start_ym: tuple[int, int],
        end_ym: tuple[int, int],
        range_label: str,
    ) -> None:
        start_g, end_g = month_year_to_gregorian_bounds(start_ym, end_ym)
        start_iso, end_iso = self._gregorian_range_to_utc_iso(start_g, end_g)
        rows = self.db.list_user_activities(
            start_iso=start_iso, end_iso=end_iso, newest_first=True
        )
        if not rows:
            self._empty_range_reply(message, range_label)
            return
        pdf_rows = []
        for r in rows:
            line = r.get("message_fa") or ""
            when = format_datetime(r.get("created_at"))
            pdf_rows.append({"زمان": when, "فعالیت": line})
        title = f"گزارش فعالیت کاربران — {range_label}"
        self._send_simple_pdf_report(
            message,
            title=title,
            subtitle=f"{len(pdf_rows)} فعالیت (جدیدترین ابتدا)",
            columns=["زمان", "فعالیت"],
            rows=pdf_rows,
            empty_message="در این بازه داده‌ای برای این گزارش نیست.",
            filename_stem="user_activity",
            output_name="گزارش_فعالیت_کاربران.pdf",
            caption=f"گزارش فعالیت کاربران — {range_label}",
            reply_ok="گزارش ارسال شد.",
            log_user=user,
            log_action="report_user_activity",
        )

    def on_monthly_summary(self, message: dict) -> None:
        """Prompt for month/year range, then build خلاصه مصرفی ماهیانه."""
        user = self._user_or_deny(message)
        if not user:
            return
        if self._deny_technician(message, user, perm.REPORT_MONTHLY_SUMMARY):
            return
        source, _source_label = self._resolve_monthly_source(user)
        if source is None:
            self._reply(
                message,
                "فایل مصرف ماهیانه مواد یافت نشد.\n"
                "ابتدا از منوی اصلی «مصرف ماهیانه مواد» را آپلود کنید.",
                kb.analytics_menu(user),
            )
            return
        self._ask_month_year_range(message, user, "monthly_summary")

    def on_month_year_range_choice(
        self,
        message: dict,
        *,
        preset: str | None = None,
        custom_text: str | None = None,
        picked: str | None = None,
    ) -> bool:
        """Handle month/year presets, typed range, or interactive year/month picks.

        Returns True if the message was consumed by a pending month-range flow.
        """
        # Peek pending before deny so unrelated messages are not swallowed.
        uid_msg = self._uid(message)
        pending = (
            self._analysis_pending.get(uid_msg)
            or self._analysis_pending.get(int(uid_msg) if uid_msg.isdigit() else uid_msg)
        )
        if not pending:
            return False
        await_kind = pending.get("await")
        if await_kind not in {
            "month_range",
            "my_start_year",
            "my_start_month",
            "my_end_year",
            "my_end_month",
            "my_typed",
        }:
            return False

        user = self._user_or_deny(message)
        if not user:
            return True
        if self._deny_technician(message, user, *perm.REPORT_FEATURES):
            return True
        uid = user["bale_user_id"]
        pending = self._analysis_pending.get(uid) or pending
        await_kind = pending.get("await")
        mode = pending.get("mode")
        if not mode:
            return False

        # --- typed «از … تا …» while in any month-range step ---
        if custom_text:
            parsed = parse_month_year_range(custom_text)
            if not parsed:
                return False
            if await_kind not in {
                "month_range",
                "my_start_year",
                "my_start_month",
                "my_end_year",
                "my_end_month",
                "my_typed",
            }:
                return False
            start_ym, end_ym = parsed
            self._clear_analysis_pending(uid)
            self._run_month_ranged_report(message, user, mode, start_ym, end_ym)
            return True

        # --- presets on primary month_range await ---
        if await_kind == "month_range":
            if preset == "day_advanced":
                self._ask_date_range(message, user, mode)
                return True
            if preset == "typed":
                pending["await"] = "my_typed"
                self._analysis_pending[uid] = pending
                self._reply(
                    message,
                    "بازه را با این فرمت بفرستید:\n"
                    "از YYYY/MM تا YYYY/MM\n"
                    "یا: از فروردین 1405 تا شهریور 1405",
                    kb.month_year_range_menu(include_day_advanced=(mode == "period")),
                )
                return True
            if preset == "custom":
                years = year_choices_around()
                pending["await"] = "my_start_year"
                self._analysis_pending[uid] = pending
                self._reply(
                    message,
                    "سال شروع بازه را انتخاب کنید:",
                    kb.year_picker_menu(years),
                )
                return True
            if preset in {"current", "3m", "ytd"}:
                try:
                    start_ym, end_ym = resolve_month_year_preset(preset)
                except ValueError:
                    return False
                self._clear_analysis_pending(uid)
                self._run_month_ranged_report(message, user, mode, start_ym, end_ym)
                return True
            return False

        if await_kind == "my_typed":
            # Invalid typed text while waiting for month/year typed range
            self._reply(
                message,
                "فرمت بازه نامعتبر است. نمونه صحیح:\n"
                "از 1405/01 تا 1405/06\n"
                "یا: از فروردین 1405 تا شهریور 1405",
                kb.month_year_range_menu(include_day_advanced=(mode == "period")),
            )
            return True

        # --- interactive year/month picker ---
        if await_kind == "my_start_year":
            if not picked or not str(picked).isdigit():
                return False
            year = int(picked)
            if not (1200 <= year <= 1500):
                return False
            pending["start_year"] = year
            pending["await"] = "my_start_month"
            self._analysis_pending[uid] = pending
            self._reply(message, "ماه شروع را انتخاب کنید:", kb.month_picker_menu())
            return True

        if await_kind == "my_start_month":
            month = PERSIAN_MONTH_NAME_TO_NUM.get((picked or "").strip())
            if not month:
                return False
            pending["start_month"] = month
            pending["await"] = "my_end_year"
            self._analysis_pending[uid] = pending
            years = year_choices_around()
            self._reply(message, "سال پایان بازه را انتخاب کنید:", kb.year_picker_menu(years))
            return True

        if await_kind == "my_end_year":
            if not picked or not str(picked).isdigit():
                return False
            year = int(picked)
            if not (1200 <= year <= 1500):
                return False
            pending["end_year"] = year
            pending["await"] = "my_end_month"
            self._analysis_pending[uid] = pending
            self._reply(message, "ماه پایان را انتخاب کنید:", kb.month_picker_menu())
            return True

        if await_kind == "my_end_month":
            month = PERSIAN_MONTH_NAME_TO_NUM.get((picked or "").strip())
            if not month:
                return False
            start_ym = (int(pending["start_year"]), int(pending["start_month"]))
            end_ym = (int(pending["end_year"]), month)
            if end_ym[0] * 12 + end_ym[1] < start_ym[0] * 12 + start_ym[1]:
                start_ym, end_ym = end_ym, start_ym
            self._clear_analysis_pending(uid)
            self._run_month_ranged_report(message, user, mode, start_ym, end_ym)
            return True

        return False

    def on_date_range_choice(self, message: dict, preset: str | None, custom_text: str | None = None) -> bool:
        """Handle day-level date-range keyboard or custom message. Returns True if consumed."""
        user = self._user_or_deny(message)
        if not user:
            return True
        if self._deny_technician(message, user, *perm.REPORT_FEATURES):
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

    @staticmethod
    def _df_to_row_dicts(df: pd.DataFrame | None, columns: list[str] | None = None) -> list[dict[str, Any]]:
        """Convert a DataFrame to list-of-dicts for PDF tables (safe for empty)."""
        if df is None or getattr(df, "empty", True):
            return []
        cols = list(columns) if columns else list(df.columns)
        rows: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            item: dict[str, Any] = {}
            for c in cols:
                val = row.get(c, "") if hasattr(row, "get") else (row[c] if c in df.columns else "")
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    val = ""
                item[c] = val
            rows.append(item)
        return rows

    @staticmethod
    def _report_has_rows(
        sections: list[dict[str, Any]] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> bool:
        """True when there is at least one data row to put in a PDF."""
        if sections:
            for sec in sections:
                if sec.get("rows"):
                    return True
            return False
        return bool(rows)

    def _send_simple_pdf_report(
        self,
        message: dict,
        *,
        title: str,
        subtitle: str | None = None,
        sections: list[dict[str, Any]] | None = None,
        columns: list[str] | None = None,
        rows: list[dict[str, Any]] | None = None,
        empty_message: str = "در این بازه داده‌ای برای این گزارش نیست.",
        filename_stem: str = "report",
        output_name: str | None = None,
        caption: str | None = None,
        reply_ok: str | None = None,
        reply_markup: dict | None = None,
        log_user: dict | None = None,
        log_action: str | None = None,
        also_excel: bool = True,
    ) -> Path | None:
        """Build a simple RTL PDF under REPORT_DIR and sendDocument to the user.

        After a successful PDF send, also builds and sends a sibling .xlsx with the
        same title/columns/rows (shared ``excel.simple_report``). Empty data →
        text-only (no empty PDF and no empty Excel).
        """
        markup = reply_markup if reply_markup is not None else kb.analytics_menu(log_user or self.db.get_user(self._uid(message)))
        if not self._report_has_rows(sections=sections, rows=rows):
            self._empty_range_reply(
                message,
                subtitle or title,
                text=empty_message or "در این بازه داده‌ای برای این گزارش نیست.",
            )
            return None
        try:
            out = REPORT_DIR / (output_name or f"{filename_stem}.pdf")
            pdf_path = generate_simple_report_pdf(
                title,
                subtitle=subtitle,
                sections=sections,
                columns=columns,
                rows=rows,
                empty_message=empty_message,
                output_path=out,
                filename_stem=filename_stem,
                letterhead_path=self._letterhead_path(),
            )
            self.client.send_document(
                self._chat_id(message),
                pdf_path,
                caption=caption or title,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("simple pdf report failed: %s", title)
            self._reply(message, user_errors.error_fa("خطا در تولید PDF گزارش", exc), markup)
            return None

        excel_ok = False
        if also_excel:
            try:
                xlsx_path = pdf_path.with_suffix(".xlsx")
                generate_simple_report_xlsx(
                    title,
                    subtitle=subtitle,
                    sections=sections,
                    columns=columns,
                    rows=rows,
                    empty_message=empty_message,
                    output_path=xlsx_path,
                    filename_stem=filename_stem,
                )
                self.client.send_document(
                    self._chat_id(message),
                    xlsx_path,
                    caption=f"نسخه اکسل — {title}",
                )
                excel_ok = True
            except Exception as exc:  # noqa: BLE001
                logger.exception("simple excel report failed after PDF: %s", title)
                logger.warning("excel companion failed for %s: %s", title, exc)

        if reply_ok is not None:
            ok_msg = reply_ok
        elif excel_ok:
            ok_msg = "گزارش PDF و اکسل ارسال شد."
        else:
            ok_msg = "گزارش PDF ارسال شد."
        self._reply(message, ok_msg, markup)
        if log_user and log_action:
            log_activity(self.db, log_user, log_action)
        return pdf_path

    def _empty_range_reply(
        self,
        message: dict,
        range_label: str | None = None,
        *,
        title: str | None = None,
        filename_stem: str = "empty_range",
        text: str | None = None,
    ) -> None:
        """Text-only empty state — never generate or send an empty PDF."""
        _ = title, filename_stem  # kept for call-site compatibility
        if text:
            msg = text
        elif range_label:
            msg = f"در این بازه ({range_label}) داده‌ای برای این گزارش نیست."
        else:
            msg = "در این بازه داده‌ای برای این گزارش نیست."
        self._reply(message, msg, kb.analytics_menu(self.db.get_user(self._uid(message))))


    def _run_month_ranged_report(
        self,
        message: dict,
        user: dict,
        mode: str,
        start_ym: tuple[int, int],
        end_ym: tuple[int, int],
        *,
        section: str | None = None,
    ) -> None:
        """Dispatch report generation for an inclusive Jalali month/year range.

        ``section`` None → ask the optional «بخش» step first (daily / comprehensive /
        period); "" = همه بخش‌ها.
        """
        if mode in self._SECTION_STEP_MODES and section is None:
            self._ask_section_step(message, user, mode, start_ym=list(start_ym), end_ym=list(end_ym))
            return
        range_label = format_month_year_range(start_ym, end_ym)
        start_g, end_g = month_year_to_gregorian_bounds(start_ym, end_ym)

        if mode == "monthly_summary":
            self._run_monthly_summary(message, user, start_ym, end_ym, range_label)
            return

        if mode == "user_activity":
            self._run_user_activity_report(message, user, start_ym, end_ym, range_label)
            return

        if mode in {"comprehensive", "analytics_pdf"}:
            self._run_comprehensive(message, user, start_g, end_g, range_label, section=section or "")
            return

        if mode == "period":
            self._run_ranged_analysis(
                message, user, mode, start_g, end_g, range_label=range_label, section=section or ""
            )
            return

        if mode == "daily":
            self._run_daily_report(message, user, start_g, end_g, range_label, section=section or "")
            return
        if mode == "remaining":
            self._run_remaining_critical(message, user, start_g, end_g, range_label)
            return
        if mode == "surplus":
            self._run_surplus_report(message, user, start_g, end_g, range_label)
            return

        self._reply(message, "حالت گزارش ناشناخته است.", kb.analytics_menu(user))

    def _run_daily_report(
        self,
        message: dict,
        user: dict,
        start: date,
        end: date,
        range_label: str,
        *,
        section: str = "",
    ) -> None:
        loaded = self._require_files(message, user, "daily")
        if not loaded:
            return
        _, frames, _ = loaded
        frames = self._section_frames(frames, section)
        rates = daily_rates(
            frames.get("tank_consumption"),
            frames.get("monthly_consumption"),
            start=start,
            end=end,
        )
        title = f"مصرف روزانه مواد — {range_label} — {self._section_label(section)}"
        cols = [
            "material_name",
            "tundish_type",
            "tundish_id",
            "avg_daily",
            "total_qty",
            "days_span",
            "unit",
            "source",
        ]
        if rates.empty:
            self._empty_range_reply(
                message, range_label, title=title, filename_stem="daily_rates"
            )
            return
        self._send_simple_pdf_report(
            message,
            title=title,
            subtitle=(
                f"بخش: {self._section_label(section)} | مبنا: میانگین مصرف روزانه هر ماده/تاندیش = "
                f"مصرف ثبت‌شده در {range_label} ÷ تعداد روزهای دارای داده (موجودی روزانه سایت و مصرف ماهیانه)"
            ),
            columns=cols,
            rows=self._df_to_row_dicts(rates, cols),
            empty_message="در این بازه داده‌ای برای این گزارش نیست.",
            filename_stem="daily_rates",
            output_name="مصرف_روزانه_مواد.pdf",
            caption=f"گزارش مصرف روزانه مواد — {range_label}",
            log_user=user,
            log_action="report_daily",
        )

    def _run_remaining_critical(
        self,
        message: dict,
        user: dict,
        start: date,
        end: date,
        range_label: str,
    ) -> None:
        loaded = self._require_files(message, user, "remaining_critical")
        if not loaded:
            return
        _, frames, _ = loaded
        rates = daily_rates(
            frames.get("tank_consumption"),
            frames.get("monthly_consumption"),
            start=start,
            end=end,
        )
        rem, rem_source = self._resolve_remaining(frames)
        crit = critical_materials(rates, rem, CRITICAL_DAYS)
        rem_cols = ["material_name", "remaining_qty", "unit", "location"]
        crit_cols = ["material_name", "remaining_qty", "avg_daily", "days_of_cover", "unit"]
        title = f"پوشش کوتاه‌مدت موجودی سایت — {range_label}"
        rem_rows = self._df_to_row_dicts(rem, rem_cols)
        crit_rows = self._df_to_row_dicts(crit, crit_cols)
        if not rem_rows and not crit_rows:
            self._empty_range_reply(message, range_label)
            return
        self._send_simple_pdf_report(
            message,
            title=title,
            subtitle=(
                f"مبنا: روزهای پوشش = موجودی فعلی ÷ میانگین مصرف روزانهٔ {range_label}؛ "
                f"اقلام با پوشش کمتر از {CRITICAL_DAYS} روز بحرانی‌اند | منبع موجودی: {rem_source}"
            ),
            sections=[
                {
                    "title": f"موجودی باقیمانده ({rem_source})",
                    "columns": rem_cols,
                    "rows": rem_rows,
                    "empty_message": "موجودی خالی است.",
                    "header_bg": "#2e7d32",
                },
                {
                    "title": f"مواد بحرانی (پوشش < {CRITICAL_DAYS} روز)",
                    "columns": crit_cols,
                    "rows": crit_rows,
                    "empty_message": "ماده بحرانی‌ای شناسایی نشد.",
                    "header_bg": "#c62828",
                },
            ],
            filename_stem="remaining_critical",
            output_name="پوشش_کوتاه_مدت_موجودی_سایت.pdf",
            caption=f"پوشش کوتاه‌مدت موجودی سایت — {range_label}",
            log_user=user,
            log_action="report_remaining",
        )

    def _run_surplus_report(
        self,
        message: dict,
        user: dict,
        start: date,
        end: date,
        range_label: str,
    ) -> None:
        loaded = self._require_files(message, user, "surplus")
        if not loaded:
            return
        _, frames, _ = loaded
        rates = daily_rates(
            frames.get("tank_consumption"),
            frames.get("monthly_consumption"),
            start=start,
            end=end,
        )
        rem, _rem_src = resolve_warehouse_remaining(self.db, frames)
        surplus = surplus_materials(rates, rem)
        cover_th = max(float(CRITICAL_DAYS) * 3.0, float(SURPLUS_COVER_DAYS))
        title = f"گزارش مواد مازاد — {range_label}"
        if surplus.empty:
            self._empty_range_reply(message, range_label)
            return
        cols = [
            "material_name",
            "remaining_qty",
            "surplus_qty",
            "avg_daily",
            "days_of_cover",
            "forecast_need",
            "unit",
            "surplus_reason",
        ]
        self._send_simple_pdf_report(
            message,
            title=title,
            subtitle=(
                f"نرخ مصرف بر اساس {range_label} | "
                f"تعریف: پوشش > {cover_th:g} روز، یا موجودی بیش از نیاز "
                f"{SURPLUS_FORECAST_DAYS:g} روز؛ مواد با موجودی ولی بدون مصرف = مازاد/بدون مصرف؛ "
                f"کد دسته ۱۸۰۰ به‌صورت خودکار اقلام مازاد محسوب می‌شود"
            ),
            columns=cols,
            rows=self._df_to_row_dicts(surplus, cols),
            empty_message="در این بازه داده‌ای برای این گزارش نیست.",
            filename_stem="surplus",
            output_name="گزارش_مواد_مازاد.pdf",
            caption=f"گزارش مواد مازاد — {range_label}",
            log_user=user,
            log_action="report_surplus",
        )

    @heavy(
        "گزارش مصرف بازه‌ای",
        when=lambda self, message, user, mode, start, end, **k: mode == "period" and k.get("section") is not None
        and perm.can_any(user, perm.REPORT_FEATURES),
    )
    def _run_ranged_analysis(
        self,
        message: dict,
        user: dict,
        mode: str,
        start: date,
        end: date,
        *,
        range_label: str | None = None,
        section: str | None = None,
    ) -> None:
        """Day-level / month-level «📅 گزارش مصرف بازه‌ای» (rebuilt from current data)."""
        from services import period_consumption as pc

        label = range_label or f"از {format_date(start)} تا {format_date(end)}"
        if mode != "period":
            self._reply(message, "حالت گزارش ناشناخته است.", kb.analytics_menu(user))
            return
        if section is None:
            self._ask_section_step(message, user, mode, start=start, end=end)
            return
        if self._deny_technician(message, user, *perm.REPORT_FEATURES):
            return
        try:
            res = pc.generate_files(
                self.db, start, end, range_label=label, section=section or None,
                letterhead_path=self._letterhead_path(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("period consumption failed")
            self._reply(message, user_errors.error_fa("خطا در ساخت گزارش", exc), kb.analytics_menu(user))
            return
        if res.error:
            self._reply(message, res.error, kb.analytics_menu(user))
            return
        chat = self._chat_id(message)
        for path, cap in ((res.pdf, res.title), (res.xlsx, f"نسخه اکسل — {res.title}")):
            try:
                self.client.send_document(chat, path, caption=cap)
            except Exception as exc:  # noqa: BLE001
                logger.warning("send period file failed: %s", exc)
        self._reply(message, res.bot_text(), kb.analytics_menu(user))
        log_activity(self.db, user, "report_period")

    @heavy("گزارش جامع", notice=False)
    def _run_comprehensive(
        self,
        message: dict,
        user: dict,
        start: date,
        end: date,
        range_label: str,
        *,
        section: str = "",
    ) -> None:
        """«📊 گزارش جامع» — PDF + XLSX for a Jalali month range (+ optional section)."""
        loaded = self._require_files(message, user, "full")
        if not loaded:
            return
        session, frames, metas = loaded
        self._reply(message, f"در حال ساخت گزارش جامع ({range_label} — {self._section_label(section)})…")
        try:
            res = comprehensive_svc.generate_files(
                self.db, user, start, end, range_label=range_label, section=section or None,
                letterhead_path=self._letterhead_path(), frames=frames, metas=metas,
            )
            self.db.save_report(
                user["bale_user_id"],
                session["id"],
                str(res.pdf),
                {k: int(metas[k]["visible_rows"]) for k in metas},
            )
            self.client.send_document(
                self._chat_id(message), res.pdf, caption=f"{kb.BTN_COMPREHENSIVE} — {res.range_label}"
            )
            if res.xlsx:
                self.client.send_document(
                    self._chat_id(message), res.xlsx,
                    caption=f"نسخه اکسل — {kb.BTN_COMPREHENSIVE} — {res.range_label}",
                )
            self._reply(
                message,
                "گزارش جامع (PDF و اکسل) ارسال شد." if res.xlsx else "PDF گزارش جامع ارسال شد.",
                kb.analytics_menu(user),
            )
            log_activity(self.db, user, "report_comprehensive")
        except Exception as exc:  # noqa: BLE001
            logger.exception("comprehensive report failed")
            self._reply(message, user_errors.error_fa("خطا در تولید گزارش", exc), kb.analytics_menu(user))

    def _resolve_monthly_source(self, user: dict) -> tuple[Path | None, str | None]:
        """Prefer raw monthly upload (plant detail); else cleaned session/latest."""
        uid = str(user["bale_user_id"])
        session = self.db.get_or_create_session(uid)
        latest = self.db.get_latest_extracted_any("monthly_consumption") or self.db.get_latest_extracted(
            uid, "monthly_consumption"
        )
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

    @heavy("خلاصه مصرف ماهیانه", notice=False)
    def _run_monthly_summary(
        self,
        message: dict,
        user: dict,
        start_ym: tuple[int, int],
        end_ym: tuple[int, int],
        range_label: str,
    ) -> None:
        """Build خلاصه مصرفی ماهیانه filtered to [start_ym .. end_ym]."""
        source, source_label = self._resolve_monthly_source(user)
        if source is None:
            self._reply(
                message,
                "فایل مصرف ماهیانه مواد یافت نشد.\n"
                "ابتدا از منوی اصلی «مصرف ماهیانه مواد» را آپلود کنید.",
                kb.analytics_menu(user),
            )
            return
        self._reply(message, f"در حال ساخت خلاصه مصرف ماهیانه ({range_label})…")
        try:
            from config import REPORT_DIR

            out_dir = REPORT_DIR
            excel_path = out_dir / SUMMARY_FILE_NAME
            data, excel_path = build_monthly_summary(
                source,
                excel_out=excel_path,
                start=start_ym,
                end=end_ym,
            )
            sections = summary_sections_for_pdf(data)
            pdf_title = f"خلاصه مصرفی ماهیانه — {range_label}"
            pdf_path = out_dir / "خلاصه مصرفی ماهیانه.pdf"
            generate_monthly_summary_pdf(
                sections,
                grand_kg=data.grand_kg,
                output_path=pdf_path,
                title=pdf_title,
                letterhead_path=self._letterhead_path(),
            )
            month_sum = sum(float(s.get("total_kg") or 0) for s in data.month_sections)
            caption = (
                f"{pdf_title}\n"
                f"منبع: {source_label}\n"
                f"جمع کل مصرفی: {data.grand_kg:g} کیلوگرم"
            )
            self.client.send_document(
                self._chat_id(message),
                pdf_path,
                caption=caption,
            )
            try:
                self.client.send_document(
                    self._chat_id(message),
                    excel_path,
                    caption=f"فایل اکسل «خلاصه مصرفی ماهیانه» — {range_label}",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("monthly summary excel send failed: %s", exc)
            self._reply(
                message,
                (
                    f"✅ خلاصه مصرف ماهیانه ارسال شد ({range_label}).\n"
                    f"ردیف‌های تجمیعی: {len(data.items)} | "
                    f"جمع ماه‌ها: {month_sum:g} | جمع کل: {data.grand_kg:g} کیلوگرم"
                ),
                kb.analytics_menu(user),
            )
            log_activity(self.db, user, "report_monthly_summary")
        except ValueError as exc:
            # Empty filtered set — text only, no empty PDF
            self._empty_range_reply(
                message,
                range_label,
                text=f"در این بازه ({range_label}) داده‌ای برای خلاصه مصرف نیست.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("monthly summary failed")
            self._reply(
                message,
                user_errors.error_fa("خطا در تولید خلاصه مصرف ماهیانه", exc),
                kb.analytics_menu(user),
            )


    def _resolve_remaining(self, frames: dict) -> tuple[Any, str]:
        """Site stock for on-site remaining/critical; else canonical منبع اصلی.

        Shared with web via ``analytics.frames.resolve_remaining``. Site preference
        is intentional product behavior; warehouse fallback always uses the latest
        cleaned product_inventory extract (+ ledger).
        """
        rem, rem_source = shared_resolve_remaining(self.db, frames)
        # Pretty-print site date with Jalali when applicable
        if rem_source.startswith("موجودی روزانه سایت"):
            day = self.db.get_latest_site_stock_date() or "—"
            rem_source = f"موجودی روزانه سایت — {format_date(day)}"
        return rem, rem_source

    def _clear_site_stock_pending(self, uid: str) -> None:
        """Drop pending entry and strip inline markup so the user is unlocked."""
        pending = self._site_stock_pending.pop(str(uid), None)
        if pending and pending.get("chat_id") is not None and pending.get("message_id") is not None:
            try:
                self.client.edit_message_reply_markup(
                    pending["chat_id"], int(pending["message_id"]), {"inline_keyboard": []}
                )
            except BaleAPIError:
                pass

    def _actor_full_name(self, user: dict | None = None, *, actor_display_name: str | None = None) -> str:
        """Display name only (no Bale id) for registrar stamps."""
        user = user or {}
        uid = str(user.get("bale_user_id") or "").strip()

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
        return name or "کاربر بدون نام"

    def _site_stock_stamp(self, user: dict) -> str:
        """ثبت شده توسط {name} در {jalali_date} ساعت {HH:MM} (Asia/Tehran)."""
        now = datetime.now(TEHRAN)
        return (
            f"ثبت شده توسط {self._actor_full_name(user)} "
            f"در {format_date(now)} ساعت {now.hour:02d}:{now.minute:02d}"
        )

    def _site_stock_inline_markup(self, pending: dict) -> dict:
        return kb.site_stock_inline_keyboard(
            pending["items"],
            pending.get("values") or {},
            group_key=pending["group"],
        )

    def _refresh_site_stock_keyboard(self, pending: dict) -> None:
        chat_id = pending.get("chat_id")
        message_id = pending.get("message_id")
        if chat_id is None or message_id is None:
            return
        markup = self._site_stock_inline_markup(pending)
        try:
            self.client.edit_message_reply_markup(chat_id, int(message_id), markup)
        except BaleAPIError as exc:
            logger.warning("edit site-stock markup failed: %s", exc)

    def on_site_stock_menu(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._clear_analysis_pending(uid)
        self._await_category_code.discard(uid)
        self._clear_site_stock_pending(uid)
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
            "یکی از گروه‌های زیر را انتخاب کنید؛ سپس مقادیر را یکی‌یکی وارد کنید.\n"
            f"تاریخ ورود: {format_date(day)}"
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
        # Lists come from the live منبع اصلی (name = «کلید واژه»), shared with web.
        from services.site_stock_lists import items_for_group as site_items_for_group

        items = site_items_for_group(self.db, group_key)
        if not items:
            self._reply(
                message,
                f"لیست موجودی روزانهٔ «{label}» خالی است.\n"
                "این لیست از ستون «کلید واژه» / محل مصرف منبع اصلی ساخته می‌شود"
                + (
                    f"؛ منبع اصلی را از «{kb.BTN_UPLOAD_MENU}» ← «{kb.BTN_MAIN_SOURCE_FILE}» بررسی کنید."
                    if perm.can(user, perm.MAIN_SOURCE_EDIT)
                    else "؛ با مدیر یا کاردان مسئول تماس بگیرید."
                ),
                kb.site_stock_menu(),
            )
            return
        uid = str(user["bale_user_id"])
        # Prefill today's existing quantities for this group (editable before confirm).
        day = self.db.tehran_today()
        existing = {
            e["item_id"]: float(e["quantity"])
            for e in self.db.list_site_stock_entries(entry_date=day, tundish_group=group_key)
            if e.get("item_id") is not None
        }
        values = {
            it["id"]: existing[it["id"]]
            for it in items
            if it["id"] in existing
        }
        chat_id = self._chat_id(message)
        pending = {
            "group": group_key,
            "items": items,
            "values": values,
            "awaiting_idx": None,
            "walk_idx": 0,
            "guided": True,
            "chat_id": chat_id,
            "message_id": None,
        }
        self._site_stock_pending[uid] = pending
        # Keep a reply keyboard for skip/back/cancel while the inline editor is open.
        self._reply(
            message,
            f"📋 {label}\n"
            "مقادیر را یکی‌یکی بفرستید؛ فهرست به‌صورت زنده به‌روز می‌شود.\n"
            f"پس از اتمام، یا هر زمان، با دکمه تعداد اصلاح کنید و در پایان «{kb.BTN_SITE_CONFIRM}» را بزنید.",
            kb.site_stock_entry_menu(),
        )
        try:
            sent = self.client.send_message(
                chat_id,
                f"اقلام ({len(items)}) — ورود هدایت‌شده:",
                reply_markup=self._site_stock_inline_markup(pending),
            )
            if isinstance(sent, dict) and sent.get("message_id") is not None:
                pending["message_id"] = sent["message_id"]
        except BaleAPIError as exc:
            logger.exception("send site-stock inline keyboard failed")
            self._clear_site_stock_pending(uid)
            self._reply(
                message,
                f"ارسال صفحه ورود موجودی ناموفق بود: {exc.description}",
                kb.site_stock_menu(),
            )
            return
        # Start guided walk at the first item.
        self._prompt_site_stock_qty(message, user, 0, from_walk=True)

    def _prompt_site_stock_qty(
        self,
        message: dict,
        user: dict,
        idx: int,
        *,
        from_walk: bool = False,
        prefix: str = "",
    ) -> None:
        uid = str(user["bale_user_id"])
        pending = self._site_stock_pending.get(uid)
        if not pending:
            return
        items = pending["items"]
        if idx < 0 or idx >= len(items):
            return
        pending["awaiting_idx"] = idx
        if from_walk:
            pending["walk_idx"] = idx
            pending["guided"] = True
        item = items[idx]
        name = kb.item_display_name(item)
        cur = pending.get("values", {}).get(item["id"])
        cur_note = f"\nمقدار فعلی: {float(cur):g}" if cur is not None else ""
        body = f"مقدار «{name}» را به‌صورت عدد بفرستید.{cur_note}"
        if prefix:
            body = f"{prefix.rstrip()}\n{body}"
        self._reply(
            message,
            body,
            kb.site_stock_entry_menu(),
        )

    def _site_stock_next_walk_idx(self, pending: dict, after_idx: int) -> int | None:
        """Next index in the guided pass (sequential); None when walk is done."""
        items = pending.get("items") or []
        nxt = int(after_idx) + 1
        if nxt < len(items):
            return nxt
        return None

    def _site_stock_after_value(
        self,
        message: dict,
        user: dict,
        pending: dict,
        answered_idx: int,
        *,
        confirm_line: str,
        was_walk_prompt: bool,
    ) -> None:
        """Refresh keyboard and either continue guided walk or leave editor idle."""
        self._refresh_site_stock_keyboard(pending)
        pending["awaiting_idx"] = None
        if pending.get("guided") and was_walk_prompt:
            nxt = self._site_stock_next_walk_idx(pending, answered_idx)
            if nxt is not None:
                self._prompt_site_stock_qty(
                    message,
                    user,
                    nxt,
                    from_walk=True,
                    prefix=confirm_line,
                )
                return
            pending["guided"] = False
            pending["walk_idx"] = None
            self._reply(
                message,
                f"{confirm_line}\n"
                f"همه اقلام یک‌بار پرسیده شد. در صورت نیاز با دکمه تعداد اصلاح کنید "
                f"یا «{kb.BTN_SITE_CONFIRM}» را بزنید.",
                kb.site_stock_entry_menu(),
            )
            return
        if pending.get("guided") and not was_walk_prompt:
            # Mid-walk edit of another row — resume the current walk item.
            walk_idx = pending.get("walk_idx")
            items = pending.get("items") or []
            if isinstance(walk_idx, int) and 0 <= walk_idx < len(items):
                self._prompt_site_stock_qty(
                    message,
                    user,
                    walk_idx,
                    from_walk=True,
                    prefix=confirm_line,
                )
                return
        self._reply(
            message,
            f"{confirm_line}\n"
            f"می‌توانید با دکمه تعداد اصلاح کنید یا «{kb.BTN_SITE_CONFIRM}» را بزنید.",
            kb.site_stock_entry_menu(),
        )

    def on_site_stock_quantity_text(self, message: dict, text: str) -> bool:
        """Consume numeric quantity while awaiting a guided/edit site-stock item."""
        # Never treat nav / site-stock keyboard labels as quantities — let later handlers run.
        if (text or "").strip() in kb.SITE_STOCK_RESERVED_TEXTS:
            return False
        uid = str(self._uid(message))
        pending = self._site_stock_pending.get(uid)
        if not pending:
            return False
        awaiting = pending.get("awaiting_idx")
        if awaiting is None:
            # Inline editor open but no item selected yet — don't steal unrelated numbers.
            return False
        user = self._user_or_deny(message)
        if not user:
            self._clear_site_stock_pending(uid)
            return True
        raw = (text or "").strip().replace(",", "٫").replace("٫", ".")
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
        idx = int(awaiting)
        if idx < 0 or idx >= len(items):
            pending["awaiting_idx"] = None
            return True
        item = items[idx]
        pending.setdefault("values", {})[item["id"]] = qty
        name = kb.item_display_name(item)
        was_walk = bool(pending.get("guided")) and pending.get("walk_idx") == idx
        self._site_stock_after_value(
            message,
            user,
            pending,
            idx,
            confirm_line=f"✓ {name}: {qty:g}",
            was_walk_prompt=was_walk,
        )
        return True

    def on_site_stock_skip(self, message: dict) -> None:
        """Skip current prompted item (leave empty) and advance guided walk."""
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        pending = self._site_stock_pending.get(uid)
        if not pending:
            self._reply(message, "ورود موجودی فعالی نیست.", kb.site_stock_menu())
            return
        awaiting = pending.get("awaiting_idx")
        if awaiting is None:
            self._reply(
                message,
                "قلم فعالی برای رد کردن نیست. از دکمه‌های تعداد استفاده کنید یا تأیید/انصراف را بزنید.",
                kb.site_stock_entry_menu(),
            )
            return
        items = pending["items"]
        idx = int(awaiting)
        if idx < 0 or idx >= len(items):
            pending["awaiting_idx"] = None
            self._reply(
                message,
                "از دکمه‌های تعداد استفاده کنید یا تأیید/انصراف را بزنید.",
                kb.site_stock_entry_menu(),
            )
            return
        item = items[idx]
        name = kb.item_display_name(item)
        was_walk = bool(pending.get("guided")) and pending.get("walk_idx") == idx
        # Leave value unchanged (empty stays empty; prefill kept unless user edits later).
        self._site_stock_after_value(
            message,
            user,
            pending,
            idx,
            confirm_line=f"⏭ «{name}» رد شد",
            was_walk_prompt=was_walk,
        )

    def on_site_stock_cancel(self, message: dict) -> None:
        user = self._user_or_deny(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._clear_site_stock_pending(uid)
        self._reply(message, "ورود موجودی لغو شد.", kb.site_stock_menu())

    def _finish_site_stock_entry(self, message: dict, user: dict) -> None:
        """Persist filled quantities and show summary + registrar stamp."""
        uid = str(user["bale_user_id"])
        pending = self._site_stock_pending.pop(uid, None)
        if not pending:
            self._reply(message, "ورود تمام شد.", kb.site_stock_menu())
            return
        group = pending["group"]
        label = SITE_STOCK_GROUPS.get(group, group)
        day = self.db.tehran_today()
        values = pending.get("values") or {}
        items = pending.get("items") or []
        id_to_item = {it["id"]: it for it in items}
        saved = 0
        errors: list[str] = []
        for iid, qty in values.items():
            it = id_to_item.get(iid) or {"id": iid, "name_desc": iid}
            try:
                self.db.upsert_site_stock_entry(
                    bale_user_id=user["bale_user_id"],
                    tundish_group=group,
                    item_id=iid,
                    quantity=qty,
                    item_name_snapshot=it.get("name_desc"),
                    actor_display_name=user.get("display_name"),
                )
                saved += 1
            except (ValueError, KeyError) as exc:
                errors.append(f"{it.get('name_desc') or iid}: {exc}")
        if pending.get("chat_id") is not None and pending.get("message_id") is not None:
            try:
                self.client.edit_message_text(
                    pending["chat_id"],
                    int(pending["message_id"]),
                    f"✅ ثبت «{label}» انجام شد.",
                    reply_markup={"inline_keyboard": []},
                )
            except BaleAPIError:
                try:
                    self.client.edit_message_reply_markup(
                        pending["chat_id"],
                        int(pending["message_id"]),
                        {"inline_keyboard": []},
                    )
                except BaleAPIError:
                    pass
        stamp = self._site_stock_stamp(user)
        log_activity(self.db, user, "site_stock_saved", tundish_group=group)
        header = (
            f"✅ ثبت موجودی «{label}» برای تاریخ {format_date(day)}\n"
            f"تعداد اقلام ثبت‌شده: {saved} از {len(items)}"
        )
        err_block = ""
        if errors:
            err_block = "\n\nخطاها:\n" + "\n".join(f"• {e}" for e in errors)
        attachment: Path | None = None
        # Long item lists → PDF table; short lists stay as interactive text bullets
        if values and len(values) > 8:
            pdf_rows = []
            for iid, qty in values.items():
                it = id_to_item.get(iid) or {"id": iid, "name_desc": iid}
                pdf_rows.append(
                    {
                        "شناسه": iid,
                        "شرح": kb.item_display_name(it),
                        "مقدار": f"{float(qty):g}",
                    }
                )
            attachment = self._send_simple_pdf_report(
                message,
                title=f"ثبت موجودی سایت — {label}",
                subtitle=f"تاریخ {format_date(day)} — {saved} از {len(items)} قلم",
                columns=["شناسه", "شرح", "مقدار"],
                rows=pdf_rows,
                filename_stem="site_stock_confirm",
                output_name=f"ثبت_موجودی_سایت_{group}.pdf",
                caption=f"✅ ثبت موجودی «{label}» — {saved} قلم",
                reply_ok=f"{header}{err_block}\n\nجدول اقلام در PDF.\n{stamp}",
                reply_markup=kb.site_stock_menu(),
            )
        else:
            lines = [header, ""]
            if values:
                for iid, qty in values.items():
                    name = kb.item_display_name(id_to_item.get(iid) or {"id": iid, "name_desc": iid})
                    lines.append(f"• {name}: {float(qty):g}")
            else:
                lines.append("(هیچ مقداری ثبت نشد)")
            if err_block:
                lines.append(err_block.strip())
            lines.append("")
            lines.append(stamp)
            self._reply(message, "\n".join(lines), kb.site_stock_menu())
        # Auto-post to configured Bale group (never breaks submit flow)
        self._notify_site_stock_group(
            user=user,
            group=group,
            day=day,
            saved=saved,
            items_total=len(items),
            values=values,
            id_to_item=id_to_item,
            attachment_path=attachment,
        )

    def _notify_site_stock_group(
        self,
        *,
        user: dict,
        group: str,
        day: str,
        saved: int,
        items_total: int,
        values: dict,
        id_to_item: dict,
        attachment_path: Path | None = None,
    ) -> None:
        """Best-effort post to the site-stock report group after confirm."""
        try:
            site_stock_notify.notify_site_stock_saved(
                self.client,
                self.db,
                registrar_name=self._actor_full_name(user),
                tundish_group=group,
                entry_date=day,
                saved=saved,
                items_total=items_total,
                values=values,
                id_to_item=id_to_item,
                attachment_path=attachment_path,
            )
        except Exception:  # noqa: BLE001
            logger.exception("site-stock group notify wrapper failed")

    def handle_callback_query(self, cq: dict) -> None:
        """Dispatch inline-button callbacks (site-stock editor)."""
        cq_id = str(cq.get("id") or "")
        data = (cq.get("data") or "").strip()
        from_user = cq.get("from") or {}
        msg = cq.get("message") or {}
        uid = str(from_user.get("id") or "")
        # Synthetic message for helpers that expect message["from"] / chat.
        message = {
            "message_id": msg.get("message_id"),
            "chat": msg.get("chat") or {},
            "from": from_user,
            "text": "",
        }

        def answer(text: str | None = None, alert: bool = False) -> None:
            if not cq_id:
                return
            try:
                self.client.answer_callback_query(cq_id, text=text, show_alert=alert)
            except BaleAPIError as exc:
                logger.warning("answerCallbackQuery failed: %s", exc)

        if data.startswith(kb.CB_ROLE_PERM_PREFIX):
            user = ensure_registered(self.db, uid, self._display_name(message))
            if not user:
                answer("شما در سیستم ثبت نشده‌اید.", alert=True)
                return
            self._on_role_perm_callback(data, message, user, answer)
            return

        if data.startswith(kb.CB_CRITICAL_RENO_PREFIX):
            user = ensure_registered(self.db, uid, self._display_name(message))
            if not user:
                answer("شما در سیستم ثبت نشده‌اید.", alert=True)
                return
            self._on_critical_reno_callback(data, message, user, answer)
            return

        if not data.startswith("ss|"):
            answer()
            return

        user = ensure_registered(self.db, uid, self._display_name(message))
        if not user:
            answer("شما در سیستم ثبت نشده‌اید.", alert=True)
            return

        pending = self._site_stock_pending.get(uid)

        if data == "ss|ok":
            if not pending:
                answer("ورود موجودی فعالی نیست.", alert=True)
                return
            answer("در حال ثبت…")
            # Keep message ids for cleanup inside finish.
            if msg.get("message_id") is not None:
                pending["message_id"] = msg.get("message_id")
            if (msg.get("chat") or {}).get("id") is not None:
                pending["chat_id"] = msg["chat"]["id"]
            self._finish_site_stock_entry(message, user)
            return

        if data == "ss|x":
            answer("لغو شد")
            self.on_site_stock_cancel(message)
            return

        # ss|{group}|{idx}|q  or  ss|{group}|{idx}|n
        parts = data.split("|")
        if len(parts) != 4 or parts[0] != "ss":
            answer()
            return
        _prefix, group, idx_s, action = parts
        try:
            idx = int(idx_s)
        except ValueError:
            answer()
            return
        if not pending or pending.get("group") != group:
            answer("این فهرست منقضی شده؛ دوباره گروه را انتخاب کنید.", alert=True)
            return
        items = pending.get("items") or []
        if idx < 0 or idx >= len(items):
            answer()
            return
        # Sync message identity from the callback source.
        if msg.get("message_id") is not None:
            pending["message_id"] = msg.get("message_id")
        if (msg.get("chat") or {}).get("id") is not None:
            pending["chat_id"] = msg["chat"]["id"]

        item = items[idx]
        full_name = kb.item_display_name(item)
        if action == "n":
            # Description button: toast full name (useful when truncated).
            answer(full_name[:200])
            return
        if action == "q":
            answer()
            self._prompt_site_stock_qty(message, user, idx)
            return
        answer()

    # ---------- تنظیمات اقلام سایت / تخصیص ----------
    def _letterhead_path(self) -> Path | None:
        """Return configured blank letterhead PDF path if present on disk."""
        raw = self.db.get_setting("letterhead_pdf")
        if not raw:
            return None
        path_obj = Path(str(raw))
        return path_obj if path_obj.is_file() else None

    def _clear_bot_settings_pending(self, uid: str) -> None:
        self._bot_settings_pending.pop(str(uid), None)

    def _require_bot_settings_user(self, message: dict) -> dict | None:
        """Owner + manager only (same gate as کاربران)."""
        user = self._user_or_deny(message)
        if not user:
            return None
        if self._deny_technician(message, user, *perm.SETTINGS_FEATURES):
            return None
        return user

    def _settings_item_menu(self, which: str) -> dict:
        if which == "letterhead":
            return kb.bot_settings_letterhead_menu()
        if which == "stock_group":
            return kb.bot_settings_stock_group_menu()
        return kb.bot_settings_item_menu(include_text=(which not in {"logo", "letterhead"}))

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
            f"{kb.BTN_BOT_SETTINGS} — یک بخش را انتخاب کنید:",
            kb.bot_settings_menu(user),
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
            "letterhead": "سربرگ PDF گزارش‌ها",
            "stock_group": "گروه گزارش موجودی روزانه",
        }
        hint = ""
        if which == "invite":
            hint = "\n\n" + PLACEHOLDER_HINT_INVITE
        elif which == "welcome":
            hint = "\n\n" + PLACEHOLDER_HINT_WELCOME
        elif which == "letterhead":
            hint = (
                "\n\nسربرگ اختیاری است. اگر آپلود شود، همه گزارش‌های PDF "
                "روی این سربرگ (پس‌زمینه هر صفحه) تولید می‌شوند."
            )
        elif which == "stock_group":
            current = site_stock_notify.resolve_report_group_id(self.db)
            cur_line = (
                f"✅ گروه گزارش موجودی تنظیم شده است (شناسه گروه: {current})."
                if current
                else "⚠️ گروه گزارش موجودی هنوز تنظیم نشده است."
            )
            hint = (
                "\n\n"
                + cur_line
                + "\n\n"
                "روش تنظیم:\n"
                "ربات را به گروه بله اضافه کنید، سپس داخل همان گروه دستور\n"
                "/set_stock_group\n"
                "را بفرستید (فقط مالک/مدیر). شناسه منفی گروه ذخیره می‌شود.\n"
                "اگر گروه تنظیم نشده باشد، ثبت موجودی سایت بدون خطا ادامه می‌یابد."
            )
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

    def _preview_letterhead(self, message: dict, user: dict) -> None:
        markup = self._settings_item_menu("letterhead")
        raw = self.db.get_setting("letterhead_pdf")
        if raw and Path(raw).is_file():
            try:
                self.client.send_document(
                    self._chat_id(message),
                    Path(raw),
                    caption="سربرگ PDF فعلی (برای همه گزارش‌ها):",
                )
                self._reply(message, f"✅ سربرگ تنظیم شده: {Path(raw).name}", markup)
                return
            except Exception:  # noqa: BLE001
                logger.exception("preview letterhead failed")
                self._reply(
                    message,
                    f"سربرگ ذخیره شده ولی ارسال نشد:\n{raw}",
                    markup,
                )
                return
        self._reply(
            message,
            "هنوز سربرگ PDF تنظیم نشده.\n"
            "با «آپلود سربرگ PDF» یک فایل PDF خالی/قالب بفرستید.",
            markup,
        )

    def on_bot_settings_upload_letterhead_start(self, message: dict) -> None:
        user = self._require_bot_settings_user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self._bot_settings_pending[uid] = {"mode": "await_letterhead", "which": "letterhead"}
        self._reply(
            message,
            "لطفاً فایل سربرگ را به‌صورت Document با پسوند .pdf ارسال کنید.\n"
            "این فایل به‌عنوان پس‌زمینه همه صفحات گزارش‌های PDF استفاده می‌شود.",
            self._settings_item_menu("letterhead"),
        )

    def on_bot_settings_clear_letterhead(self, message: dict) -> None:
        user = self._require_bot_settings_user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        old = self.db.get_setting("letterhead_pdf")
        self.db.clear_setting("letterhead_pdf", updated_by=uid)
        log_activity(self.db, user, "settings_letterhead")
        if old:
            try:
                Path(old).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                logger.warning("could not delete old letterhead %s", old)
        self._bot_settings_pending[uid] = {"mode": "menu", "which": "letterhead"}
        self._reply(message, "✅ سربرگ حذف شد.", self._settings_item_menu("letterhead"))

    def on_bot_settings_letterhead_document(self, message: dict) -> bool:
        """Handle inbound PDF while awaiting letterhead. Returns True if consumed."""
        uid = str(self._uid(message))
        pending = self._bot_settings_pending.get(uid)
        if not pending or pending.get("mode") != "await_letterhead":
            return False
        user = self._require_bot_settings_user(message)
        if not user:
            self._clear_bot_settings_pending(uid)
            return True
        doc = message.get("document") or {}
        file_name = (doc.get("file_name") or "").strip()
        file_id = doc.get("file_id")
        mime = str(doc.get("mime_type") or "")
        if not file_id:
            self._reply(
                message,
                "فایل نامعتبر است. یک Document با پسوند .pdf بفرستید.",
                self._settings_item_menu("letterhead"),
            )
            return True
        if not (file_name.lower().endswith(".pdf") or "pdf" in mime.lower()):
            self._reply(
                message,
                f"فقط PDF پذیرفته می‌شود. (دریافت شد: {file_name or mime or 'بدون‌نام'})",
                self._settings_item_menu("letterhead"),
            )
            return True
        try:
            BOT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
            dest = BOT_ASSETS_DIR / f"letterhead_{uid}.pdf"
            old = self.db.get_setting("letterhead_pdf")
            saved = self.client.download_file(file_id, dest)
            if old and Path(old).resolve() != Path(saved).resolve():
                try:
                    Path(old).unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
            self.db.set_setting("letterhead_pdf", str(saved), updated_by=uid)
            log_activity(self.db, user, "settings_letterhead")
        except Exception as exc:  # noqa: BLE001
            logger.exception("save letterhead failed")
            self._reply(
                message,
                user_errors.error_fa("ذخیره سربرگ ناموفق بود", exc),
                self._settings_item_menu("letterhead"),
            )
            return True
        self._bot_settings_pending[uid] = {"mode": "menu", "which": "letterhead"}
        self._reply(
            message,
            f"✅ سربرگ PDF ذخیره شد: {Path(saved).name}\n"
            "از این پس همه گزارش‌های PDF روی این سربرگ تولید می‌شوند.",
            self._settings_item_menu("letterhead"),
        )
        return True


    def _preview_stock_group(self, message: dict, user: dict) -> None:
        markup = self._settings_item_menu("stock_group")
        current = site_stock_notify.resolve_report_group_id(self.db)
        db_raw = self.db.get_setting(site_stock_notify.SETTING_KEY)
        lines = [
            "📣 گروه گزارش موجودی روزانه",
            "",
            (f"شناسه گروه فعال: {current}" if current else "⚠️ گروه گزارش موجودی هنوز تنظیم نشده است."),
            (
                "منبع: تنظیمات ربات" if db_raw
                else ("منبع: تنظیم پیش‌فرض سرور" if current else "")
            ),
            "",
            "برای تنظیم: ربات را به گروه بله اضافه کنید و داخل همان گروه (توسط مالک/مدیر) بفرستید:",
            "/set_stock_group",
            "",
            "تا وقتی گروهی تنظیم نشده، گزارش گروهی موجودی ارسال نمی‌شود (ثبت موجودی سایت بدون خطا ادامه می‌یابد).",
        ]
        self._reply(message, "\n".join(lines), markup)

    def on_bot_settings_clear_stock_group(self, message: dict) -> None:
        user = self._require_bot_settings_user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        self.db.clear_setting(site_stock_notify.SETTING_KEY, updated_by=uid)
        log_activity(self.db, user, "settings_stock_group")
        self._bot_settings_pending[uid] = {"mode": "menu", "which": "stock_group"}
        env_bit = (
            f"\nتوجه: تنظیم پیش‌فرض سرور (شناسه {SITE_STOCK_REPORT_GROUP_ID}) همچنان فعال است."
            if SITE_STOCK_REPORT_GROUP_ID
            else ""
        )
        self._reply(
            message,
            f"✅ شناسه گروه گزارش از تنظیمات حذف شد.{env_bit}",
            self._settings_item_menu("stock_group"),
        )

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
        elif which == "letterhead":
            self._preview_letterhead(message, user)
        elif which == "stock_group":
            self._preview_stock_group(message, user)
        else:
            self._reply(message, "ابتدا یک بخش تنظیمات را انتخاب کنید.", kb.bot_settings_menu(user))

    def on_bot_settings_edit_text_start(self, message: dict) -> None:
        user = self._require_bot_settings_user(message)
        if not user:
            return
        uid = str(user["bale_user_id"])
        pending = self._bot_settings_pending.get(uid) or {}
        which = pending.get("which")
        if which not in {"invite", "welcome"}:
            self._reply(message, "این بخش متن قابل ویرایش ندارد.", kb.bot_settings_menu(user))
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
            self._reply(message, "ابتدا یک بخش تنظیمات را انتخاب کنید.", kb.bot_settings_menu(user))
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
            self._reply(message, "ابتدا یک بخش تنظیمات را انتخاب کنید.", kb.bot_settings_menu(user))
            return
        old = self.db.get_setting(key)
        self.db.clear_setting(key, updated_by=uid)
        if which == "logo":
            log_activity(self.db, user, "settings_logo")
        elif which == "invite":
            log_activity(self.db, user, "settings_invite")
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
        # Activity: logo / invite image changes
        try:
            actor = self.db.get_user(uid) or {"bale_user_id": uid}
            if which == "logo":
                log_activity(self.db, actor, "settings_logo")
            elif which == "invite":
                log_activity(self.db, actor, "settings_invite")
        except Exception:  # noqa: BLE001
            pass
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
                user_errors.error_fa("ذخیره تصویر ناموفق بود", exc),
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
            kb.BTN_SET_LETTERHEAD,
            kb.BTN_SETTINGS_VIEW,
            kb.BTN_SETTINGS_EDIT_TEXT,
            kb.BTN_SETTINGS_SET_IMAGE,
            kb.BTN_SETTINGS_CLEAR_IMAGE,
            kb.BTN_SETTINGS_UPLOAD_LETTERHEAD,
            kb.BTN_SETTINGS_CLEAR_LETTERHEAD,
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
                kb.BTN_SET_LETTERHEAD,
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
            log_activity(self.db, user, "settings_invite")
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
        if self._deny_technician(message, user, perm.MATERIAL_REQUEST, perm.WAREHOUSE_RETURN):
            return None
        return user

    def _format_mr_review(self, days: float | int, lines: list[dict]) -> str:
        days_label = int(days) if float(days) == int(days) else days
        if not lines:
            return (
                f"🛒 پیشنهاد درخواست مواد برای پوشش {days_label} روز:\n\n"
                "پیشنهادی نیست — موجودی برای بازه درخواست کافی به‌نظر می‌رسد."
            )
        return (
            f"🛒 پیشنهاد درخواست مواد برای پوشش {days_label} روز — {len(lines)} قلم:\n"
            + "\n".join(
                f"{i}) {ln.get('item_name') or ln.get('item_id')}: {float(ln.get('quantity') or 0):g} {ln.get('unit') or ''}".strip()
                for i, ln in enumerate(lines[:40], 1)
            )
            + ("\n…" if len(lines) > 40 else "")
            + "\n\nتأیید همه / اصلاح / انصراف را انتخاب کنید."
        )

    def _mr_lines_to_pdf_rows(self, lines: list[dict]) -> list[dict[str, Any]]:
        from config import TUNDISH_TYPES
        from excel.work_order import GROUP_LABELS_FA

        rows: list[dict[str, Any]] = []
        for i, ln in enumerate(lines, 1):
            g = ln.get("tundish_group")
            if g:
                g_label = GROUP_LABELS_FA.get(g) or TUNDISH_TYPES.get(g) or g
            else:
                g_label = "—"
            unit = ln.get("unit") or ""
            rows.append(
                {
                    "ردیف": i,
                    "شناسه": ln.get("item_id") or "—",
                    "شرح": ln.get("item_name") or "—",
                    "گروه": g_label,
                    "موجودی": f"{float(ln.get('remaining_qty') or 0):.2f}",
                    "مصرف روز": f"{float(ln.get('avg_daily') or 0):.2f}",
                    "پیشنهاد": f"{float(ln.get('quantity') or 0):.2f}",
                    "واحد": unit,
                }
            )
        return rows

    def _send_mr_review(
        self,
        message: dict,
        days: float | int,
        lines: list[dict],
        *,
        prefix: str | None = None,
    ) -> None:
        body = self._format_mr_review(days, lines)
        basis = self._mr_basis.get(self._uid(message))
        if basis:
            body = f"{body}\n\n{basis}"
        if prefix:
            body = f"{prefix}\n\n{body}"
        if lines:
            body += f"\n\nبرای فایل پیش‌نویس (بدون ثبت) «{kb.BTN_MR_DRAFT}» را بزنید."
        self._reply(message, body[:3900], kb.material_request_review_menu())

    def on_material_request_draft(self, message: dict) -> None:
        """«📄 پیش‌نویس PDF/اکسل»: files of the CURRENT review list — no DB write/ledger."""
        uid = self._uid(message)
        pending = self._material_req_pending.get(uid)
        user = self._user_or_deny(message)
        if not user:
            return
        if not pending or not pending.get("lines"):
            self._reply(message, f"ابتدا از «{kb.BTN_MATERIAL_REQUEST}» لیست را بسازید.", kb.main_menu(user))
            return
        lines = pending.get("lines") or []
        days = pending.get("days") or 1
        days_label = int(days) if float(days) == int(days) else days
        cols = ["ردیف", "شناسه", "شرح", "گروه", "موجودی", "مصرف روز", "پیشنهاد", "واحد"]
        basis = self._mr_basis.get(uid) or ""
        self._send_simple_pdf_report(
            message,
            title=f"پیش‌نویس درخواست مواد — پوشش {days_label} روز",
            subtitle=f"{len(lines)} قلم — پیش‌نویس (ثبت نشده) | {basis}",
            columns=cols,
            rows=self._mr_lines_to_pdf_rows(lines),
            empty_message="لیست خالی است.",
            filename_stem="material_request_draft",
            output_name="پیش‌نویس_درخواست_مواد.pdf",
            caption=f"پیش‌نویس درخواست مواد — {len(lines)} قلم (ثبت نشده)",
            reply_ok="پیش‌نویس PDF و اکسل ارسال شد؛ هنوز چیزی ثبت نشده است.",
            reply_markup=kb.material_request_review_menu(),
        )


    def _build_mr_lines(
        self, user: dict, days: float | int
    ) -> tuple[list[dict], str | None]:
        """Build draft request lines from suggest_requests; error message or None."""
        session = self.db.get_or_create_session(user["bale_user_id"])
        completeness = self._effective_completeness(user, session)
        missing = missing_files_for_goal("suggest", completeness)
        if missing:
            return [], (
                "برای درخواست مواد این داده‌ها لازم است:\n• "
                + "\n• ".join(missing)
                + f"\n\nمنبع اصلی و مصرف ماهیانه را از «{kb.BTN_UPLOAD_MENU}» بفرستید."
            )
        # Never filtered by section (cleanup item 7) — the whole plant is proposed.
        frames, _metas = self._load_frames(user, session)
        tank = frames.get("tank_consumption")
        monthly = frames.get("monthly_consumption")
        inv = self._inventory_with_ledger(frames.get("product_inventory"))
        # Rate window = last 3 complete months; ``days`` = coverage horizon only.
        from services.consumption_rates import rates_last_complete_months

        use_rates, basis, _w = rates_last_complete_months(tank, monthly)
        self._mr_basis[str(user["bale_user_id"])] = basis
        rem = remaining(inv)
        # اولویت 0 items are not proposed in a material request.
        sug = suggest_requests(use_rates, rem, days, inventory_df=inv)
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

        # Resolve item_id → tundish_group (catalog assignment, else monthly WO map)
        group_by_id: dict[str, str] = {}
        for asg in self.db.list_catalog_with_assignments(active_only=True):
            if asg.get("tundish_group") and asg.get("id"):
                group_by_id[str(asg["id"]).strip()] = str(asg["tundish_group"]).strip().lower()
        try:
            from excel.work_order import build_item_to_group_map

            # Prefer session monthly frame WO when available
            if monthly is not None and not monthly.empty:
                id_col = "id" if "id" in monthly.columns else None
                wo_col = "work_order" if "work_order" in monthly.columns else None
                if id_col and wo_col:
                    m = monthly.copy()
                    qty = pd.to_numeric(m["quantity"], errors="coerce").fillna(0).abs()
                    if "coefficient" in m.columns:
                        qty = qty * pd.to_numeric(m["coefficient"], errors="coerce").fillna(1).abs()
                    m["_w"] = qty
                    for iid, g in build_item_to_group_map(
                        m, id_col=id_col, work_order_col=wo_col, weight_col="_w"
                    ).items():
                        group_by_id.setdefault(iid, g)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MR WO map failed: %s", exc)

        lines: list[dict] = []
        for _, row in positive.iterrows():
            name = str(row["material_name"]).strip()
            iid = id_by_name.get(name)
            # Try extract leading id from "CODE - desc"
            if not iid and " - " in name:
                maybe = name.split(" - ", 1)[0].strip()
                if maybe:
                    iid = maybe
            g = group_by_id.get(str(iid).strip()) if iid else None
            if not g:
                # tank/monthly rates may carry tundish_type already
                tt = row.get("tundish_type")
                if tt is not None and not (isinstance(tt, float) and pd.isna(tt)):
                    from config import TUNDISH_TYPES
                    label = str(tt).strip()
                    for key, fa in TUNDISH_TYPES.items():
                        if label == fa or label.casefold() == key:
                            g = key
                            break
            lines.append(
                {
                    "item_id": iid,
                    "item_name": name,
                    "unit": row.get("unit"),
                    "avg_daily": float(row.get("avg_daily") or 0),
                    "remaining_qty": float(row.get("remaining_qty") or 0),
                    "quantity": float(row.get("suggest_qty") or 0),
                    "tundish_group": g,
                }
            )
        # Stable order: slab, bloom, billet, then unknown
        rank = {"slab": 0, "bloom": 1, "billet": 2}
        lines.sort(key=lambda ln: (rank.get(ln.get("tundish_group") or "", 9), ln.get("item_name") or ""))
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
            "تعداد روز پوشش را وارد کنید (عدد صحیح، حداقل ۱).\n"
            "پیش‌فرض: ۱ روز — می‌توانید دکمه «۱ روز (پیش‌فرض)» را بزنید یا عدد را تایپ کنید.\n"
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
        log_activity(self.db, user, "material_request_created")
        self._send_mr_review(message, days, lines)
        return True


    def on_material_request_days_text(self, message: dict, text: str) -> bool:
        """Parse typed coverage days while awaiting days. Returns True if consumed."""
        uid = self._uid(message)
        pending = self._material_req_pending.get(uid)
        if not pending or pending.get("await") != "days":
            return False
        # leave known menu buttons to their own handlers
        if text in {
            kb.BTN_MR_DAYS_DEFAULT,
            kb.BTN_MR_HISTORY,
            kb.BTN_MR_CANCEL,
            kb.BTN_BACK_MAIN,
            kb.BTN_MATERIAL_REQUEST,
            kb.BTN_HELP,
            kb.BTN_HOME,
            kb.BTN_BACK,
        }:
            return False
        raw = (text or "").strip().translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        )
        if not raw.isdigit():
            self._reply(
                message,
                "تعداد روز باید یک عدد صحیح مثبت باشد (مثلاً ۱ یا ۵).\n"
                "یا دکمه «۱ روز (پیش‌فرض)» را بزنید.",
                kb.material_request_days_menu(),
            )
            return True
        days = int(raw)
        if days < 1:
            self._reply(
                message,
                "تعداد روز باید حداقل ۱ باشد.",
                kb.material_request_days_menu(),
            )
            return True
        if days > 366:
            self._reply(
                message,
                "حداکثر پوشش قابل قبول ۳۶۶ روز است.",
                kb.material_request_days_menu(),
            )
            return True
        return self.on_material_request_days(message, days)

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
                coverage_days=pending.get("days") or 1,
                lines=lines,
                status="confirmed",
            )
        except ValueError as exc:
            self._reply(message, str(exc), kb.material_request_review_menu())
            return
        self._clear_material_req_pending(uid)
        log_activity(self.db, user, "material_request_confirmed")
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
        out.append("منبع اصلی با ledger کسر شد و در گزارش‌های بعدی منعکس می‌شود.")
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
        log_activity(self.db, user, "material_request_cancelled")
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
        self._send_mr_review(message, pending.get("days") or 7, pending["lines"])

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
        self._send_mr_review(
            message,
            pending.get("days") or 7,
            lines,
            prefix=msg,
        )
        return True



    # ---------- warehouse return (برگشت به انبار) ----------

    def _format_wr_review(self, lines: list[dict]) -> str:
        if not lines:
            return (
                "↩️ پیشنهاد برگشت مواد مازاد به انبار:\n\n"
                "ماده مازادی برای برگشت شناسایی نشد."
            )
        return (
            f"↩️ پیشنهاد برگشت مواد مازاد به انبار — "
            f"جدول اقلام در PDF ({len(lines)} قلم).\n"
            "تأیید همه / اصلاح / انصراف را انتخاب کنید."
        )

    def _wr_lines_to_pdf_rows(self, lines: list[dict]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for i, ln in enumerate(lines, 1):
            unit = ln.get("unit") or ""
            rows.append(
                {
                    "ردیف": i,
                    "شناسه": ln.get("item_id") or "—",
                    "شرح": ln.get("item_name") or "—",
                    "موجودی سایت": f"{float(ln.get('site_qty') or 0):.2f}",
                    "مازاد": f"{float(ln.get('surplus_qty') or 0):.2f}",
                    "برگشت": f"{float(ln.get('quantity') or 0):.2f}",
                    "واحد": unit,
                    "دلیل": ln.get("surplus_reason") or "—",
                }
            )
        return rows

    def _send_wr_review(
        self,
        message: dict,
        lines: list[dict],
        *,
        prefix: str | None = None,
    ) -> None:
        body = self._format_wr_review(lines)
        if prefix:
            body = f"{prefix}\n\n{body}"
        markup = kb.warehouse_return_review_menu()
        if not lines:
            self._reply(message, body, markup)
            return
        cols = ["ردیف", "شناسه", "شرح", "موجودی سایت", "مازاد", "برگشت", "واحد", "دلیل"]
        self._send_simple_pdf_report(
            message,
            title="پیشنهاد برگشت مواد مازاد به انبار",
            subtitle=f"{len(lines)} قلم",
            columns=cols,
            rows=self._wr_lines_to_pdf_rows(lines),
            filename_stem="warehouse_return_review",
            output_name="پیشنهاد_برگشت_به_انبار.pdf",
            caption=f"پیشنهاد برگشت به انبار — {len(lines)} قلم",
            reply_ok=body,
            reply_markup=markup,
        )

    def _build_wr_lines(self, user: dict) -> tuple[list[dict], str | None]:
        """Suggest surplus lines from site stock (preferred) or warehouse remaining."""
        session = self.db.get_or_create_session(user["bale_user_id"])
        completeness = self._effective_completeness(user, session)
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
        tank = frames.get("tank_consumption")
        monthly = frames.get("monthly_consumption")
        rates = daily_rates(tank, monthly)

        if site_rows:
            rem_df = remaining(pd.DataFrame(site_rows))
            source = "موجودی روزانه سایت"
        else:
            rem_df = remaining(self._inventory_with_ledger(frames.get("product_inventory")))
            source = FILE_TYPES["product_inventory"]["label_fa"]

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
        self._send_wr_review(message, lines)

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
        log_activity(self.db, user, "warehouse_return_confirmed")
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
        out.append("منبع اصلی با ledger مثبت افزایش یافت.")
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
        self._send_wr_review(message, pending["lines"])

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
        self._send_wr_review(message, lines, prefix=msg)
        return True


    # ---------- dispatcher ----------

    def handle_message(self, message: dict) -> None:
        try:
            uid = str(((message or {}).get("from") or {}).get("id") or "")
        except Exception:  # noqa: BLE001
            uid = ""
        if uid:
            self._check_stale_flow(uid)
        try:
            self._handle_message(message)
        finally:
            if uid:
                self._stale_once.discard(uid)

    def _handle_message(self, message: dict) -> None:
        if not message:
            return
        raw_text = (message.get("text") or "").strip()
        # Old / renamed labels → current label (old keyboards stay usable)
        text = kb.canonical(raw_text)

        # Group chats: only /set_stock_group (avoid menu spam in groups)
        if self._is_group_chat(message):
            if text.startswith("/"):
                parts = text.split()
                cmd = parts[0].split("@")[0].lower()
                if cmd == "/set_stock_group":
                    self.cmd_set_stock_group(message)
            return

        if message.get("document"):
            if self.on_bot_settings_letterhead_document(message):
                return
        if message.get("photo") or message.get("document"):
            if self.on_bot_settings_photo(message):
                return
        # هدف اصلی uploads (single input / bulk) — before generic Excel slot handler
        if message.get("document"):
            if self.main_goal_report.handle_document(message):
                return
        if message.get("document"):
            self.on_document(message)
            return
        if message.get("photo"):
            self.main_goal_report.handle_photo(message)
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
                "/reset": lambda: self.cmd_reset(message),
                "/status": lambda: self.on_status(message),
                "/report": lambda: self.on_comprehensive_prompt(message),
                "/analytics": lambda: self.on_analytics_menu(message),
                "/set_stock_group": lambda: self.cmd_set_stock_group(message),
            }
            handler = mapping.get(cmd)
            # any slash command abandons unfinished drafts (/start with token too)
            uid = self._uid(message)
            if cmd != "/start" or not args:
                self._clear_all_pending(uid)
            if handler:
                handler()
            else:
                self._reply(message, "دستور ناشناخته. /help را ببینید.")
            return

        # ---- uniform navigation first (never swallowed by a pending flow) ----
        if text == kb.BTN_HOME:
            self.on_nav_home(message)
            return
        if text == kb.BTN_BACK:
            self.on_nav_back(message)
            return
        if text == kb.BTN_CANCEL:
            self.on_cancel(message)
            return
        if text == kb.BTN_HELP:
            self.cmd_help(message)
            return
        if text == kb.BTN_FILE_GUIDE:
            self.on_file_guide(message)
            return
        if raw_text in kb.REMOVED_HINTS:
            user = self._user_or_deny(message)
            if user:
                self._clear_all_pending(str(user["bale_user_id"]))
                self._reply(message, kb.REMOVED_HINTS[raw_text], kb.main_menu(user))
            return

        # central permission gate (services.permissions; DB overrides) for every
        # feature button, before any flow sees the text
        need = kb.BUTTON_FEATURE.get(text)
        if need:
            gate_user = self._user_or_deny(message)
            if not gate_user:
                return
            if self._deny_technician(message, gate_user, *need):
                return
        rp_role = kb.role_perm_button_to_role(text)
        if rp_role:
            self.on_role_perm_pick(message, rp_role)
            return

        # یادآورها — settings (owner/manager)
        if self.reminder_settings.handle_text(message, text):
            return
        # 🧾 گزارش تاندیش — menu buttons + entry/settings flows
        if self.tundish_report.handle_text(message, text):
            return
        # 🎯 هدف اصلی
        if self.main_goal_report.handle_text(message, text):
            return
        # 🧮 نیاز مواد برای N تاندیش (section / N / mode steps; «اسلب ۴» shortcut)
        if self.on_n_tundish_text(message, text):
            return
        # optional section step (مصرف روزانه / گزارش جامع / مصرف بازه‌ای)
        if self.on_section_step_text(message, text):
            return
        # «📦 جایگزینی کامل منبع اصلی» confirm
        if text == kb.BTN_FULL_REPLACE_CONFIRM:
            self.on_full_replace_confirm(message)
            return

        # month/year range typed while awaiting (از YYYY/MM تا YYYY/MM)
        if parse_month_year_range(text):
            if self.on_month_year_range_choice(message, custom_text=text):
                return
        # custom day-level date range while awaiting
        if parse_custom_range_message(text):
            if self.on_date_range_choice(message, preset=None, custom_text=text):
                return
        # interactive year / month picks while awaiting month-range wizard
        if self.on_month_year_range_choice(message, picked=text):
            return
        # منبع اصلی edit/add record free-text
        if self.on_main_source_flow_text(message, text):
            return
        # اقلام بحرانی year/month/count text
        if self.on_critical_flow_text(message, text):
            return
        # category code entry (plain 4-digit text while awaiting)
        if self.on_category_code_text(message, text):
            return

        # --- موجودی روزانه سایت (buttons BEFORE quantity parse so nav never stuck) ---
        if text == kb.BTN_SITE_STOCK:
            self.on_site_stock_menu(message)
            return
        if text in kb.SITE_GROUP_BUTTONS:
            self.on_site_stock_group(message, kb.SITE_GROUP_BUTTONS[text])
            return
        if text == kb.BTN_SITE_SKIP:
            self.on_site_stock_skip(message)
            return
        if text == kb.BTN_BACK_SITE:
            self.on_site_stock_menu(message)
            return
        if self.on_site_stock_quantity_text(message, text):
            return

        # user-management interactive flow (role/id text)
        if self.on_users_flow_text(message, text):
            return
        # bot-settings interactive text (invite/welcome templates)
        if self.on_bot_settings_flow_text(message, text):
            return
        # material-request coverage days (typed number)
        if self.on_material_request_days_text(message, text):
            return
        # material-request / warehouse-return edit text (pick item / qty)
        if self.on_material_request_edit_text(message, text):
            return
        if self.on_warehouse_return_edit_text(message, text):
            return

        handler = self._button_handlers().get(text)
        if handler is not None:
            handler(message)
            return
        inbound_hist_id = self._inbound_history_id(text)
        if inbound_hist_id is not None:
            self.on_inbound_report(message, report_id=inbound_hist_id)
            return
        if text == kb.BTN_MR_DAYS_DEFAULT:
            if self.on_material_request_days(message, 1):
                return
        presets = {
            kb.BTN_MY_CURRENT: "current",
            kb.BTN_MY_3: "3m",
            kb.BTN_MY_YTD: "ytd",
            kb.BTN_MY_CUSTOM: "custom",
            kb.BTN_MY_TYPED: "typed",
            kb.BTN_MY_DAY_ADV: "day_advanced",
        }
        if text in presets and self.on_month_year_range_choice(message, preset=presets[text]):
            return
        day_presets = {
            kb.BTN_RANGE_TODAY: "today",
            kb.BTN_RANGE_7: "7d",
            kb.BTN_RANGE_30: "30d",
            kb.BTN_RANGE_CUSTOM: "custom",
        }
        if text in day_presets and self.on_date_range_choice(message, day_presets[text]):
            return

        file_type = kb.button_to_file_type(text)
        if file_type:
            self.on_pick_file_type(message, file_type, return_menu="upload")
            return

        user = ensure_registered(self.db, self._uid(message), self._display_name(message))
        if self._expired_flow_notice(message, user):
            return
        self._reply(
            message,
            "لطفاً از دکمه‌های منو استفاده کنید یا /help را بزنید.",
            kb.main_menu(user) if user else None,
        )

    def _button_handlers(self) -> dict[str, Any]:
        """Static button → handler map (menu-walk smoke checks every keyboard label)."""
        return {
            kb.BTN_USERS: self.on_users_menu,
            kb.BTN_USERS_ADD: self.on_users_add_start,
            kb.BTN_USERS_EDIT: self.on_users_edit_start,
            kb.BTN_USERS_DELETE: self.on_users_delete_start,
            kb.BTN_USERS_LIST: self.cmd_users,
            # ⚙️ تنظیمات
            kb.BTN_BOT_SETTINGS: self.on_bot_settings_menu,
            kb.BTN_APPEARANCE: self.on_appearance_menu,
            kb.BTN_ROLE_PERMS: self.on_role_permissions,
            kb.BTN_USER_ACTIVITY: self.on_user_activity_report,
            kb.BTN_SET_INVITE: lambda m: self.on_bot_settings_section(m, "invite"),
            kb.BTN_SET_WELCOME: lambda m: self.on_bot_settings_section(m, "welcome"),
            kb.BTN_SET_LOGO: lambda m: self.on_bot_settings_section(m, "logo"),
            kb.BTN_SET_LETTERHEAD: lambda m: self.on_bot_settings_section(m, "letterhead"),
            kb.BTN_SET_STOCK_GROUP: lambda m: self.on_bot_settings_section(m, "stock_group"),
            kb.BTN_SETTINGS_CLEAR_STOCK_GROUP: self.on_bot_settings_clear_stock_group,
            kb.BTN_SETTINGS_UPLOAD_LETTERHEAD: self.on_bot_settings_upload_letterhead_start,
            kb.BTN_SETTINGS_CLEAR_LETTERHEAD: self.on_bot_settings_clear_letterhead,
            kb.BTN_SETTINGS_VIEW: self.on_bot_settings_view,
            kb.BTN_SETTINGS_EDIT_TEXT: self.on_bot_settings_edit_text_start,
            kb.BTN_SETTINGS_SET_IMAGE: self.on_bot_settings_set_image_start,
            kb.BTN_SETTINGS_CLEAR_IMAGE: self.on_bot_settings_clear_image,
            # درخواست مواد / برگشت به انبار
            kb.BTN_MATERIAL_REQUEST: self.on_material_request_start,
            kb.BTN_WAREHOUSE_RETURN: self.on_warehouse_return_start,
            kb.BTN_MR_HISTORY: self.on_material_request_history,
            kb.BTN_MR_CONFIRM_ALL: self._on_mr_wr_confirm,
            kb.BTN_MR_EDIT: self._on_mr_wr_edit,
            kb.BTN_MR_BACK_REVIEW: self._on_mr_wr_back_review,
            kb.BTN_MR_DRAFT: self.on_material_request_draft,
            kb.BTN_MR_DAYS_DEFAULT: lambda m: self.on_material_request_days(m, 1),
            # 📊 گزارش‌ها
            kb.BTN_ANALYTICS: self.on_analytics_menu,
            kb.BTN_DAILY: self.on_daily_report,
            kb.BTN_COMPREHENSIVE: self.on_comprehensive_prompt,
            kb.BTN_PERIOD: self.on_period_prompt,
            kb.BTN_REMAINING: self.on_remaining_critical,
            kb.BTN_CRITICAL_ITEMS: self.on_critical_items_menu,
            kb.BTN_CRITICAL_COUNTS: self.on_critical_counts_start,
            kb.BTN_CRITICAL_REPORT: self.on_critical_report_start,
            kb.BTN_N_TUNDISH: self.on_n_tundish_start,
            kb.BTN_SURPLUS: self.on_surplus_report,
            kb.BTN_INBOUND: self.on_inbound_report,
            kb.BTN_MONTHLY_SUMMARY: self.on_monthly_summary,
            # 📤 ورود فایل‌ها / 📦 منبع اصلی
            kb.BTN_UPLOAD_MENU: self.on_upload_menu,
            kb.BTN_MAIN_SOURCE_FILE: self.on_main_source_file_menu,
            kb.BTN_FULL_REPLACE: self.on_full_replace_start,
            kb.BTN_FULL_REPLACE_CONFIRM: self.on_full_replace_confirm,
            kb.BTN_INV_EDIT_RECORD: self.on_inv_edit_record_start,
            kb.BTN_INV_ADD_RECORD: self.on_inv_add_record_start,
            kb.BTN_INV_ADD_CATEGORY: self.on_add_category_prompt,
            kb.BTN_INV_LIST_CATEGORIES: self.on_list_categories,
            kb.BTN_INV_DOWNLOAD: self.on_inv_download,
            kb.BTN_WAREHOUSE_STOCK: lambda m: self.on_pick_file_type(m, "product_inventory", return_menu="upload"),
            kb.BTN_MONTHLY: lambda m: self.on_pick_file_type(m, "monthly_consumption", return_menu="upload"),
            # موجودی روزانه سایت
            kb.BTN_SITE_STOCK: self.on_site_stock_menu,
            kb.BTN_SITE_SKIP: self.on_site_stock_skip,
            kb.BTN_BACK_SITE: self.on_site_stock_menu,
            # nav
            kb.BTN_HOME: self.on_nav_home,
            kb.BTN_BACK: self.on_nav_back,
            kb.BTN_CANCEL: self.on_cancel,
            kb.BTN_HELP: self.cmd_help,
            kb.BTN_FILE_GUIDE: self.on_file_guide,
        }

    def _on_mr_wr_confirm(self, message: dict) -> None:
        if self._uid(message) in self._warehouse_ret_pending:
            self.on_warehouse_return_confirm(message)
        else:
            self.on_material_request_confirm(message)

    def _on_mr_wr_edit(self, message: dict) -> None:
        if self._uid(message) in self._warehouse_ret_pending:
            self.on_warehouse_return_edit_start(message)
        else:
            self.on_material_request_edit_start(message)

    def _on_mr_wr_back_review(self, message: dict) -> None:
        if self._uid(message) in self._warehouse_ret_pending:
            self.on_warehouse_return_back_review(message)
        else:
            self.on_material_request_back_review(message)

    @staticmethod
    def _update_uid(update: dict) -> str:
        src = update.get("message") or update.get("callback_query") or {}
        return str((src.get("from") or {}).get("id") or "")

    def _heavy(self, message: dict, label: str, notice: bool, job) -> bool:
        """Run ``job`` in the background pool (19c) or inline when disabled."""
        uid = ""
        try:
            uid = self._uid(message)
        except (KeyError, TypeError):
            pass

        def on_error(exc: BaseException) -> None:
            text = user_errors.error_fa(f"ساخت {label} انجام نشد", exc, log=logger)
            try:
                self.client.send_message(self._chat_id(message), text)
            except Exception:  # noqa: BLE001
                logger.warning("could not send background error notice")

        if self.bg.executor is not None and not self.bg.in_worker and uid:
            # same report type for two users never runs concurrently: several outputs
            # use fixed / per-second file names under reports/
            lock = self.bg.label_lock(label)

            def locked_job() -> None:
                with lock:
                    job()

            if notice:
                self._reply(message, f"⏳ در حال ساخت {label}… نتیجه همین‌جا ارسال می‌شود.")
            if self.bg.submit(uid, locked_job, self.handle_update, on_error):
                return True
        job()
        return True

    def handle_update(self, update: dict) -> None:
        if self.bg.try_queue(self._update_uid(update), update):
            return  # this user's heavy job is running; processed in order right after
        try:
            if "callback_query" in update:
                self.handle_callback_query(update["callback_query"])
            elif "my_chat_member" in update:
                self.on_my_chat_member(update["my_chat_member"])
            elif "message" in update:
                self.handle_message(update["message"])
        except Exception as exc:  # noqa: BLE001
            # 19d: simple message + tracking code for the user; traceback only in log
            msg = (update.get("message") or (update.get("callback_query") or {}).get("message") or {})
            who = (update.get("message") or update.get("callback_query") or {}).get("from") or {}
            chat_id = (msg.get("chat") or {}).get("id") or who.get("id")
            text = user_errors.error_fa(
                f"درخواست شما انجام نشد (update {update.get('update_id')})", exc, log=logger
            )
            if chat_id:
                try:
                    self.client.send_message(chat_id, text.replace(f" (update {update.get('update_id')})", ""))
                except Exception:  # noqa: BLE001
                    logger.warning("could not send error notice to %s", chat_id)
