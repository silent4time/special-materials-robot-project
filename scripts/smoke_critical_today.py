"""Smoke (v1.2.1, temp DB only): «📝 ثبت تعداد تاندیش ماهانه» asks NO year/month — only
slab → billet → bloom, dated by entry date (today); the report basis/header says
«از امروز …» + manual counts; without a today entry it falls back to the sequence-log
average of the 3 complete months. Bot + web parity."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from smoke_phase1 import FakeClient

    from analytics.critical_items import horizon_header_note
    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database
    from services import critical_items_report as cir
    from services import permissions as perm

    db = Database()
    perm.bind(db)
    db.upsert_user("9911", "owner", display_name="مالک آزمون")
    y, m, d = cir.report_date_parts(None)
    today = f"{y}/{m:02d}/{d:02d}"
    assert cir.get_today_counts(db) is None
    assert "ندارد" in cir.today_counts_line(db)

    client = FakeClient()
    app = BotApp(client, db)

    def send(text):
        n = len(client.sent)
        app.handle_message({"from": {"id": 9911, "first_name": "x"}, "chat": {"id": 9911}, "text": text})
        return client.sent[n:]

    send(kb.BTN_CRITICAL_ITEMS)
    out = send(kb.BTN_CRITICAL_COUNTS)
    txt = out[-1][1]
    assert "سال" not in txt and f"از امروز {today}" in txt and "اسلب" in txt, txt
    out = send("۷۰")
    assert "بیلت" in out[-1][1], out[-1][1]
    out = send("34")
    assert "بلوم" in out[-1][1], out[-1][1]
    out = send("0")
    assert f"از امروز {today} ذخیره شد" in out[-1][1] and "اسلب: 70" in out[-1][1], out[-1][1]
    e = cir.get_today_counts(db)
    assert e == {"billet": 34, "bloom": 0, "slab": 70, "entry_date": today, "by": "9911"}, e

    out = send(kb.BTN_CRITICAL_ITEMS)
    assert f"آخرین ثبت دستی: {today}" in out[-1][1] and "شهریور" not in out[-1][1], out[-1][1]

    b = cir.critical_basis(db)
    assert b.manual_entry_date == today and b.count_slab == 70 and b.count_billet == 34
    note = horizon_header_note(b)
    assert note.startswith(f"از امروز {today}:") and "ثبت دستی" in note and "موجودی امروز" in note, note
    assert f"ثبت‌شده در {today}" in note and f"تاریخ گزارش: {today}" in note, note
    assert "ثبت دستی" in cir.fixed_notes(b)[1]

    # an entry from another day is NOT used → sequence-log / none fallback
    import json

    db.set_setting(cir.TODAY_COUNTS_KEY, json.dumps({**e, "entry_date": "1400/01/01"}))
    b2 = cir.critical_basis(db)
    assert b2 is None or not b2.manual_entry_date
    assert "مربوط به امروز نیست" in cir.today_counts_line(db)

    # web parity: form has no year/month, POST stores today's entry
    from fastapi.testclient import TestClient

    import web.deps as deps
    from web.app import create_app
    from web.auth_web import set_credential

    set_credential(db, bale_user_id="9911", username="owner9911", password="secret99")
    deps._db = db
    wapp = create_app()
    wapp.dependency_overrides[deps.get_db] = lambda: db
    c = TestClient(wapp)
    assert c.post("/login", data={"username": "owner9911", "password": "secret99"},
                  follow_redirects=False).status_code == 303
    page = c.get("/reports").text
    form = page[page.index('action="/reports/critical-items/counts"'):]
    form = form[:form.index("</form>")]
    assert "jalali_year" not in form and "jalali_month" not in form and "count_slab" in form
    r = c.post("/reports/critical-items/counts",
               data={"count_slab": 75, "count_billet": 33, "count_bloom": 1})
    assert r.status_code == 200 and f"از امروز {today}" in r.text, r.text[-500:]
    assert cir.get_today_counts(db)["slab"] == 75
    assert f"از امروز {today}:" in r.text
    print("OK smoke_critical_today")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
