#!/usr/bin/env python3
"""Smoke (QA4 bot fixes, temp DB only): main-menu text, /status keyboard, short
cancel back to the originating menu, full-replace cancel → 📦 منبع اصلی, site-stock
full-width inline rows + cancel note, material-request rounding + long-list split."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from smoke_phase1 import FakeClient, _btns

    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database
    from services import permissions as perm
    from services.units import fmt_qty, round_qty

    assert round_qty(3.2, "NO") == 4 and round_qty(2.0, "SET") == 2 and round_qty(1.234, "KG") == 1.23
    assert fmt_qty(1500.5, "Kg") == "1,500.5" and fmt_qty(7.01, "عدد") == "8"

    db = Database()
    perm.bind(db)
    db.upsert_user("9901", "owner", display_name="مالک آزمون")
    client = FakeClient()
    client.edits = []
    client.edit_message_text = lambda *a, **k: client.edits.append(a)
    app = BotApp(client, db)

    def send(text):
        n = len(client.sent)
        app.handle_message({"from": {"id": 9901, "first_name": "x"}, "chat": {"id": 9901}, "text": text})
        return client.sent[n:]

    main_btns = _btns(kb.main_menu(db.get_user("9901")))
    out = send(kb.BTN_HOME)
    assert out[-1][1] == kb.MAIN_MENU_TEXT, out[-1][1]
    out = send("/status")
    assert _btns(out[-1][2]) == main_btns, _btns(out[-1][2])

    send(kb.BTN_UPLOAD_MENU)
    send(kb.BTN_WAREHOUSE_STOCK)
    out = send(kb.BTN_CANCEL)
    assert out[-1][1].startswith("لغو شد؛ فایلی دریافت نشد") and len(out[-1][1]) < 80, out[-1][1]
    assert kb.BTN_WAREHOUSE_STOCK in _btns(out[-1][2])

    send(kb.BTN_HOME)
    send(kb.BTN_UPLOAD_MENU)
    send(kb.BTN_MAIN_SOURCE_FILE)
    send(kb.BTN_FULL_REPLACE)
    out = send(kb.BTN_CANCEL)
    assert "منبع اصلی تغییری نکرد" in out[-1][1] and kb.BTN_FULL_REPLACE in _btns(out[-1][2]), out[-1]

    # site stock inline: cancel edits the list message to a short note
    items = [{"id": "A", "name_desc": "آجر کف 40-1"}, {"id": "B", "name_desc": "فلت"}]
    app._site_stock_pending["9901"] = {"group": "bloom", "items": items, "values": {"A": 3}, "awaiting_idx": None,
                                       "walk_idx": 0, "guided": True, "chat_id": 9901, "message_id": 77}
    ik = app._site_stock_inline_markup(app._site_stock_pending["9901"])["inline_keyboard"]
    assert all(len(r) == 1 for r in ik) and ik[0][0]["text"] == "1. آجر کف 40-1 — ✅ 3", ik[0]
    app.on_site_stock_cancel({"from": {"id": 9901}, "chat": {"id": 9901}, "text": kb.BTN_CANCEL})
    assert client.edits and "لغو شد؛ چیزی ذخیره نشد" in client.edits[-1][2], client.edits

    # material request review: every line shown (split), keyboard on the last part
    lines = [{"item_name": f"قلم آزمایشی شمارهٔ {i} با نام طولانی برای تقسیم پیام", "quantity": round_qty(i + .3, "NO"),
              "unit": "NO"} for i in range(150)]
    n = len(client.sent)
    app._send_mr_review({"from": {"id": 9901}, "chat": {"id": 9901}}, 7, lines)
    parts = client.sent[n:]
    assert len(parts) >= 2 and parts[-1][2] and not parts[0][2]
    joined = "\n".join(p[1] for p in parts)
    assert "150) " in joined and "\n…" not in joined and ": 150 عدد" in joined, joined[-300:]
    rows = app._mr_lines_to_pdf_rows(lines[:3])
    assert rows[1]["پیشنهاد"] == "2" and rows[1]["واحد"] == "عدد", rows[1]
    # inbound wording: clear reason per set-aside group
    from services import inbound_report as ir
    rep = {"has_baseline": True, "n_inbound": 0, "n_rejected": 3, "n_zero_new": 2, "rejected": [
        {"کد دسته": "1605", "موجودی": 626, "دلیل": "کد ۴ رقمی در منبع اصلی نیست"},
        {"کد دسته": "1800", "موجودی": 0, "دلیل": "ردیف جدید کد 1800 (مازاد) — فقط با «افزودن رکورد»"},
        {"کد دسته": "1800", "موجودی": 5, "دلیل": "ردیف جدید کد 1800 (مازاد) — فقط با «افزودن رکورد»"}]}
    txt = ir.summary_text_fa(rep)
    assert "1 ردیف با کد ۴ رقمی ناموجود در منبع اصلی" in txt and "2 ردیف جدید کد 1800" in txt, txt
    assert "رد شده" not in txt and "2 شناسهٔ جدید با موجودی صفر" in txt, txt
    # reminders: disabled → «غیرفعال» instead of the overdue phase
    from services import mandatory_reminders as rem
    cfg = dict(rem.load_config(db), enabled=False)
    st = rem.compute_status(db, cfg)
    assert rem.status_label_fa(st, cfg).startswith("⏸ غیرفعال")
    print("SMOKE_BOT_NAV_QA4_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
