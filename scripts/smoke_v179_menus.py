#!/usr/bin/env python3
"""v1.7.9 — every reply keyboard has «⬅️ بازگشت» (except the main menu) and,
when the voice assistant is on, «🤖 از دستیار بپرس». Back returns to the parent
menu and drops pending input. Temp DB only.
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def _texts(markup: dict | None) -> list[str]:
    from bot import keyboards as kb

    return kb.keyboard_texts(markup)


def _rows(markup: dict) -> list[list[str]]:
    out = []
    for row in markup.get("keyboard") or []:
        out.append([b.get("text") if isinstance(b, dict) else str(b) for b in row])
    return out


def test_keyboard_shapes() -> None:
    from bot import keyboards as kb
    from bot.bale_api import BaleClient

    owner = {"role": "owner", "active": 1, "bale_user_id": "1"}
    builders: list[tuple[str, object]] = [
        ("main_menu", lambda: kb.main_menu(owner)),
        ("upload_files_menu", lambda: kb.upload_files_menu(owner)),
        ("main_source_file_menu", lambda: kb.main_source_file_menu(owner)),
        ("full_replace_confirm_menu", kb.full_replace_confirm_menu),
        ("cancel_pending_menu", kb.cancel_pending_menu),
        ("analytics_menu", lambda: kb.analytics_menu(owner)),
        ("critical_items_menu", kb.critical_items_menu),
        ("section_step_menu", kb.section_step_menu),
        ("n_tundish_section_menu", kb.n_tundish_section_menu),
        ("n_tundish_count_menu", kb.n_tundish_count_menu),
        ("n_tundish_mode_menu", kb.n_tundish_mode_menu),
        ("main_goal_menu", kb.main_goal_menu),
        ("main_goal_inputs_menu", kb.main_goal_inputs_menu),
        ("main_goal_range_menu", kb.main_goal_range_menu),
        ("main_goal_partial_confirm_menu", kb.main_goal_partial_confirm_menu),
        ("main_goal_upload_menu", kb.main_goal_upload_menu),
        ("main_goal_bulk_menu", kb.main_goal_bulk_menu),
        ("main_goal_history_menu", lambda: kb.main_goal_history_menu(can_delete=True)),
        ("main_goal_period_menu", kb.main_goal_period_menu),
        ("main_goal_section_menu", kb.main_goal_section_menu),
        ("main_goal_targets_confirm_menu", kb.main_goal_targets_confirm_menu),
        ("main_goal_horizon_menu", kb.main_goal_horizon_menu),
        ("main_goal_cancel_menu", kb.main_goal_cancel_menu),
        ("main_goal_target_menu", kb.main_goal_target_menu),
        ("date_range_menu", kb.date_range_menu),
        ("month_year_range_menu", kb.month_year_range_menu),
        ("year_picker_menu", lambda: kb.year_picker_menu([1404, 1405])),
        ("month_picker_menu", kb.month_picker_menu),
        ("material_request_days_menu", kb.material_request_days_menu),
        ("material_request_review_menu", kb.material_request_review_menu),
        ("material_request_edit_menu", kb.material_request_edit_menu),
        ("warehouse_return_review_menu", kb.warehouse_return_review_menu),
        ("site_stock_menu", kb.site_stock_menu),
        ("site_stock_entry_menu", kb.site_stock_entry_menu),
        ("bot_settings_menu", lambda: kb.bot_settings_menu(owner)),
        ("appearance_menu", kb.appearance_menu),
        ("bot_settings_letterhead_menu", kb.bot_settings_letterhead_menu),
        ("bot_settings_stock_group_menu", kb.bot_settings_stock_group_menu),
        ("bot_settings_item_menu", kb.bot_settings_item_menu),
        ("users_menu", kb.users_menu),
        ("guide_roles_menu", kb.guide_roles_menu),
        ("guide_doc_menu", kb.guide_doc_menu),
        ("invite_confirm_menu", kb.invite_confirm_menu),
        ("role_menu", lambda: kb.role_menu(True)),
        ("reminder_settings_menu", lambda: kb.reminder_settings_menu(True)),
        ("reminder_roles_menu", lambda: kb.reminder_roles_menu(["owner"])),
        ("reminder_back_menu", kb.reminder_back_menu),
        ("tundish_report_menu", lambda: kb.tundish_report_menu(owner)),
        ("tundish_report_mode_menu", kb.tundish_report_mode_menu),
        ("tundish_report_step_menu", lambda: kb.tundish_report_step_menu(["کوتاه", "یک گزینهٔ بلند برای تمام‌عرض"], optional=True)),
        ("tundish_report_confirm_menu", kb.tundish_report_confirm_menu),
        ("tundish_report_settings_menu", kb.tundish_report_settings_menu),
        ("tundish_report_section_settings_menu", kb.tundish_report_section_settings_menu),
        ("tundish_report_item_edit_menu", kb.tundish_report_item_edit_menu),
        ("tundish_report_type_menu", kb.tundish_report_type_menu),
        ("tundish_report_required_menu", kb.tundish_report_required_menu),
        ("tundish_report_back_section_menu", kb.tundish_report_back_section_menu),
        ("tundish_report_delete_confirm_menu", kb.tundish_report_delete_confirm_menu),
        ("inbound_history_menu", lambda: kb.inbound_history_menu(["گزارش ۱۴۰۵"])),
        ("edit_field_menu", kb.edit_field_menu),
        ("output_format_menu", kb.output_format_menu),
        ("stock_report_mode_menu", kb.stock_report_mode_menu),
        ("stock_report_segment_menu", kb.stock_report_segment_menu),
        ("production_stats_menu", kb.production_stats_menu),
        ("production_stats_entry_menu", kb.production_stats_entry_menu),
        ("production_stats_confirm_menu", kb.production_stats_confirm_menu),
        ("production_stats_months_menu", lambda: kb.production_stats_months_menu(["1405/06", "1405/07", "1405/08"])),
        ("production_stats_field_menu", kb.production_stats_field_menu),
        ("target_tonnage_section_menu", lambda: kb.target_tonnage_section_menu("۱۰")),
        ("target_tonnage_tons_menu", lambda: kb.target_tonnage_tons_menu("بیلت")),
        ("target_tonnage_examples_menu", kb.target_tonnage_examples_menu),
        ("update_menu", lambda: kb.update_menu(True)),
        ("backup_menu", lambda: kb.backup_menu(owner)),
        ("assistant_settings_menu", kb.assistant_settings_menu),
        ("assistant_answer_menu", lambda: kb.assistant_answer_menu(["نمونه"], misfire=True)),
        ("rate_loss_menu", kb.rate_loss_menu),
        ("role_perms_role_menu", kb.role_perms_role_menu),
    ]
    kb.set_assistant_menu(False)
    for name, fn in builders:
        markup = fn()
        rows = _rows(markup)
        texts = [t for row in rows for t in row]
        for row in rows:
            assert 1 <= len(row) <= 2, (name, row)
        if name == "main_menu":
            assert kb.BTN_BACK not in texts and kb.BTN_HOME not in texts, texts
        else:
            assert kb.BTN_BACK in texts, (name, texts)
            assert kb.BTN_HOME in texts, (name, texts)
        assert kb.BTN_ASK_ASSISTANT not in texts, (name, texts)
        assert markup.get("_menu") != "main" or name == "main_menu"
    kb.set_assistant_menu(True)
    for name, fn in builders:
        texts = _texts(fn())
        assert texts.count(kb.BTN_ASK_ASSISTANT) == 1, (name, texts)
    assert kb.BTN_ASK_ASSISTANT not in _texts(BaleClient.inline_keyboard([]))
    inline = BaleClient.inline_keyboard([[{"text": "نمونه", "callback_data": "x"}]])
    assert inline["inline_keyboard"][-1][0]["callback_data"] == kb.CB_ASK_ASSISTANT
    kb.set_assistant_menu(False)
    bare = BaleClient.inline_keyboard([[{"text": "نمونه", "callback_data": "x"}]])
    assert all(b.get("callback_data") != kb.CB_ASK_ASSISTANT for row in bare["inline_keyboard"] for b in row)
    print(f"  {len(builders)} reply keyboards OK")


def test_back_and_assistant_return() -> None:
    import os

    import bot.handlers as handlers_mod
    import bot.keyboards as kb
    from db.models import Database
    from services import permissions as perm
    from services import voice_assistant as va

    db = Database(Path(os.environ["DATABASE_PATH"]).with_name("v179menus.db"))
    perm.bind(db)
    db.upsert_user("1", role="owner", display_name="مالک")

    class FakeClient:
        def __init__(self) -> None:
            self.sent: list[tuple[str, str, dict | None]] = []

        def send_message(self, chat_id, text, reply_markup=None, **_k):
            self.sent.append((str(chat_id), text, reply_markup))
            return {"ok": True, "message_id": len(self.sent)}

        def __getattr__(self, name):
            return lambda *a, **k: {"ok": True}

    client = FakeClient()
    app = handlers_mod.BotApp(client, db)

    def send(text: str) -> tuple[str, list[str]]:
        app.handle_message({"from": {"id": 1, "first_name": "م"}, "chat": {"id": 1}, "text": text})
        _c, txt, mk = client.sent[-1]
        return txt, _texts(mk)

    send(kb.BTN_HOME)
    send(kb.BTN_ANALYTICS)
    send(kb.BTN_CRITICAL_ITEMS)
    _t, btns = send(kb.BTN_BACK)
    assert kb.BTN_DAILY in btns and kb.BTN_CRITICAL_ITEMS in btns, btns
    assert kb.BTN_SITE_STOCK not in btns, btns
    _t, btns = send(kb.BTN_BACK)
    assert kb.BTN_ANALYTICS in btns and kb.BTN_DAILY not in btns, btns

    send(kb.BTN_UPLOAD_MENU)
    send(kb.BTN_MAIN_SOURCE_FILE)
    _t, btns = send(kb.BTN_BACK)
    assert kb.BTN_WAREHOUSE_STOCK in btns and kb.BTN_INV_EDIT_RECORD not in btns, btns

    send(kb.BTN_BOT_SETTINGS)
    send(kb.BTN_USERS)
    _t, btns = send(kb.BTN_BACK)
    assert kb.BTN_USERS in btns and kb.BTN_USERS_ADD not in btns, btns
    send(kb.BTN_BACKUP)
    _t, btns = send(kb.BTN_BACK)
    assert kb.BTN_BACKUP in btns and kb.BTN_BACKUP_CREATE not in btns, btns

    db.set_setting(va.SETTING_ENABLED, "1")
    app._sync_assistant_menu()
    send(kb.BTN_ANALYTICS)
    _t, btns = send(kb.BTN_ANALYTICS)
    assert kb.BTN_ASK_ASSISTANT in btns, btns
    txt, btns = send(kb.BTN_ASK_ASSISTANT)
    assert "سؤال" in txt and kb.BTN_BACK in btns, (txt, btns)
    _t, btns = send(kb.BTN_BACK)
    assert kb.BTN_DAILY in btns and kb.BTN_ASK_ASSISTANT in btns, btns
    assert not app._has_pending("1")

    db.set_setting(va.SETTING_ENABLED, "0")
    app._sync_assistant_menu()
    _t, btns = send(kb.BTN_HOME)
    assert kb.BTN_ASK_ASSISTANT not in btns and kb.BTN_BACK not in btns, btns
    print("  back routing + assistant return OK")


def main() -> int:
    test_keyboard_shapes()
    test_back_and_assistant_return()
    print("SMOKE_V179_MENUS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
