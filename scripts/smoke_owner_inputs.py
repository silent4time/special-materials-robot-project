#!/usr/bin/env python3
"""Smoke (temp DB only) for the owner's 1405/07/20 changes as integrated:
«📤 ورود فایل‌ها» = 4 inputs (stock, monthly, production PHOTO, unified
«گزارش جامع تاندیش در سکوئنس»), ❓ file guides (text + album) for each,
owner-only 🤖 voice assistant that stays off when not installed, voice messages
never crash, web sequence upload + login-protected guide images."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _seq_xlsx(path: Path) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["ردیف", "ماشین", "تاندیش", "تاندیشکار", "تعداد ذوب", "مدت سکوئنس", "ISG",
               "شماره ذوب", "شروع ریخته گری", "شماره ذوب", "پایان ریخته گری",
               "تعویض شرود", "تعویض نازل بیرونی", "تیوب چنجر"])
    rows = [
        ("بیلت ۱", 6, "1405/06/03 08:00", "1405/06/03 14:00"),
        ("بیلت ۲", 5, "1405/06/10 08:00", "1405/06/10 13:00"),
        ("اسلب ۲", 7, "1405/06/12 09:00", "1405/06/12 18:00"),
        ("بلوم", 4, "1405/06/15 09:00", "1405/06/15 15:00"),
        ("اسلب ۱", 8, "1405/07/02 09:00", "1405/07/02 19:00"),
        ("بیلت ۱", 6, "1405/07/05 08:00", "1405/07/05 14:00"),
    ]
    for i, (m, melts, start, end) in enumerate(rows, 1):
        ws.append([i, m, str(10 + i), "1", melts, 300 + i, "1217", 30510000 + i, start, 30520000 + i, end, "", "", ""])
    wb.save(path)
    return path


def main() -> int:
    from smoke_phase1 import FakeClient, _btns

    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database
    from services import file_guides
    from services import permissions as perm
    from services import voice_assistant as va

    # broken 1-line placeholder files from the owner's paste are gone
    for name in ("voice_assistant.py", "voice_handler_complete.py", "db_settings.py"):
        assert not (ROOT / name).exists(), name

    db = Database()
    perm.bind(db)
    db.upsert_user("9901", "owner", display_name="مالک")
    db.upsert_user("9902", "manager", display_name="مدیر")
    db.upsert_user("9903", "technician", display_name="کاردان")
    client = FakeClient()
    albums: list[tuple] = []
    client.send_media_group = lambda chat_id, paths, caps=None: albums.append((chat_id, list(paths)))
    src = _seq_xlsx(Path(_smoke_env.SMOKE_TMP) / "seq.xlsx")
    client.download_file = lambda fid, dest: shutil.copy(src, dest)
    app = BotApp(client, db)

    def send(uid, text=None, **extra):
        n = len(client.sent)
        msg = {"from": {"id": int(uid), "first_name": "x"}, "chat": {"id": int(uid)}, **extra}
        if text is not None:
            msg["text"] = text
        app.handle_message(msg)
        return client.sent[n:]

    owner = db.get_user("9901")
    files_btns = _btns(kb.upload_files_menu(owner))
    for b in (kb.BTN_WAREHOUSE_STOCK, kb.BTN_MONTHLY, kb.BTN_SEQUENCE_REPORT, kb.BTN_PRODUCTION_PHOTOS):
        assert files_btns.count(b) == 1, (b, files_btns)
    assert kb.BTN_SEQUENCE_REPORT in kb.BUTTON_FEATURE and kb.BTN_PRODUCTION_PHOTOS in kb.BUTTON_FEATURE
    # mg inputs menu no longer duplicates the uploads
    mg_btns = _btns(kb.main_goal_inputs_menu())
    assert kb.BTN_MG_INPUT_BILLET not in mg_btns and kb.BTN_MG_INPUT_PROD not in mg_btns

    # unified sequence upload from «📤 ورود فایل‌ها»
    send(9901, kb.BTN_UPLOAD_MENU)
    out = send(9901, kb.BTN_SEQUENCE_REPORT)
    assert "گزارش جامع تاندیش در سکوئنس" in out[-1][1], out[-1][1]
    out = send(9901, kb.BTN_FILE_GUIDE)
    assert "ccssequence" in out[-1][1] and "خروجی Excel" in out[-1][1], out[-1][1]
    assert albums and len(albums[-1][1]) == 2, albums
    out = send(9901, document={"file_id": "F1", "file_name": "گزارش جامع.xlsx"})
    txt = "\n".join(o[1] for o in out)
    assert "بیلت" in txt and "اسلب" in txt and "بلوم" in txt, txt
    assert kb.BTN_SEQUENCE_REPORT in _btns(out[-1][2]), _btns(out[-1][2])
    with db.connect() as conn:
        secs = {(r[0], r[1]) for r in conn.execute("SELECT period_key, section FROM main_goal_tundish_sequences")}
    assert {("m:1405-06", "billet"), ("m:1405-06", "slab"), ("m:1405-06", "bloom"),
            ("m:1405-07", "slab"), ("m:1405-07", "billet")} <= secs, secs

    # production photo from files menu: cancel returns to «📤 ورود فایل‌ها»
    out = send(9901, kb.BTN_PRODUCTION_PHOTOS)
    assert "ریخته گری" in out[-1][1] or "ریخته‌گری" in out[-1][1]
    send(9901, kb.BTN_FILE_GUIDE)
    assert len(albums[-1][1]) == 2 and "mg_production_photo" in albums[-1][1][0].name
    out = send(9901, kb.BTN_CANCEL)
    assert kb.BTN_PRODUCTION_PHOTOS in _btns(out[-1][2]), out[-1]

    # stock / monthly guides
    send(9901, kb.BTN_WAREHOUSE_STOCK)
    out = send(9901, kb.BTN_FILE_GUIDE)
    assert "۱۲۰۰ تا ۱۸۰۰" in out[-1][1] and len(albums[-1][1]) == 5
    send(9901, kb.BTN_CANCEL)
    send(9901, "📥 مصرف ماهیانه")  # old label still works
    out = send(9901, kb.BTN_FILE_GUIDE)
    assert "Refractory Monthly Reports" in out[-1][1] and len(albums[-1][1]) == 3
    send(9901, kb.BTN_CANCEL)
    for k in file_guides.MAIN_INPUTS:
        assert file_guides.SYSTEM_STEPS[k] and len(file_guides.guide_images(k)) == file_guides.IMAGE_COUNTS[k]
        assert "/file-guides/" in file_guides.guide_html(k)

    # technician has no FILE_INPUTS → denied centrally
    out = send(9903, kb.BTN_SEQUENCE_REPORT)
    assert "9903" not in app.main_goal_report.pending

    # 🤖 assistant: owner only, stays off when not installed
    assert kb.BTN_ASSISTANT in _btns(kb.bot_settings_menu(owner))
    assert kb.BTN_ASSISTANT not in _btns(kb.bot_settings_menu(db.get_user("9902")))
    out = send(9902, kb.BTN_ASSISTANT)
    assert "فقط برای مالک" in out[-1][1]
    out = send(9901, kb.BTN_ASSISTANT)
    assert "دستیار هوشمند صوتی" in out[-1][1] and kb.BTN_ASSISTANT_ON in _btns(out[-1][2])
    out = send(9901, kb.BTN_BACK)
    assert kb.BTN_ASSISTANT in _btns(out[-1][2]), _btns(out[-1][2])
    avail = va.availability()
    out = send(9901, kb.BTN_ASSISTANT_ON)
    # enabling always works now (text questions use the Persian rules even without Ollama)
    assert db.get_setting(va.SETTING_ENABLED) == "1" and "فعال شد" in out[-1][1], out[-1][1]
    if not (avail["whisper"] and avail["ollama"]):
        assert "قواعد فارسی" in out[-1][1], out[-1][1]
    assert kb.BTN_ASK_ASSISTANT in _btns(kb.main_menu(owner))
    out = send(9901, kb.BTN_ASSISTANT_OFF)
    assert db.get_setting(va.SETTING_ENABLED) == "0"
    assert kb.BTN_ASK_ASSISTANT not in _btns(kb.main_menu(owner))
    voice = {"voice": {"file_id": "V1", "duration": 2}}
    out = send(9902, **voice)
    assert "پیام صوتی پشتیبانی نمی‌شود" in out[-1][1]
    db.set_setting(va.SETTING_ENABLED, "1")
    out = send(9901, **voice)
    if not avail["whisper"]:
        assert "Whisper" in out[-1][1], out[-1][1]
    db.set_setting(va.SETTING_ENABLED, "0")

    # web: sequence form + guide image route needs login
    from fastapi.testclient import TestClient

    from web.app import create_app

    tc = TestClient(create_app())
    r = tc.get("/file-guides/mg_sequence_1.jpg", follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403), r.status_code
    html = (ROOT / "web/templates/main_goal_report.html").read_text(encoding="utf-8")
    assert "/reports/main-goal/inputs/sequence" in html and 'file_guide("mg_sequence")' in html
    print("smoke_owner_inputs OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
