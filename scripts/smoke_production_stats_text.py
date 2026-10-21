#!/usr/bin/env python3
"""Smoke (temp DB only): «📝 آمار تولید» text-only input — bot dialog (year/month →
ccm1..ccm5 kg → melts → summary/confirm → one confirmation → menu), validation
(Persian digits, commas, invalid), replace-after-asking, edit one field / re-enter all,
delete with confirmation, kg→ton in DB, months without data excluded; photo refused;
web form + list + edit + delete mirror the bot."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DIALOG_OUT = Path(_smoke_env.SMOKE_TMP) / "production_stats_dialog.txt"


def main() -> int:
    from smoke_phase1 import FakeClient, _btns

    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database
    from services import main_goal_persist as mgp
    from services import permissions as perm
    from services import production_manual as pm

    assert not (ROOT / "services/main_goal_production_ocr.py").exists()
    assert pm.parse_number("۱۲٬۳۴۵") == 12345 and pm.parse_number("1,234.5") == 1234.5
    assert pm.parse_number("abc") is None and pm.parse_number("-5") is None
    assert pm.parse_year_month("۱۴۰۵/۰۷") == (1405, 7) and pm.parse_year_month("1405/13") is None

    db = Database()
    perm.bind(db)
    db.upsert_user("9901", "owner", display_name="مالک")
    client = FakeClient()
    app = BotApp(client, db)
    log: list[str] = []

    def send(text=None, **extra):
        n = len(client.sent)
        msg = {"from": {"id": 9901, "first_name": "x"}, "chat": {"id": 9901}, **extra}
        if text is not None:
            msg["text"] = text
            log.append(f"👤 {text}")
        app.handle_message(msg)
        out = client.sent[n:]
        for o in out:
            log.append("🤖 " + o[1] + (f"\n   [دکمه‌ها: {' | '.join(_btns(o[2]))}]" if o[2] else ""))
        return out

    send(kb.BTN_MAIN_GOAL)
    send(kb.BTN_MG_INPUTS)
    out = send(kb.BTN_PRODUCTION_STATS)
    assert "1405/07" in out[-1][1]
    out = send("1405/7/1")
    assert "نامعتبر" in out[-1][1]
    out = send("۱۴۰۵/۰۷")
    assert out[-1][1] == "وزن محصول ccm1 (اسلب ۱) 1405/07 را به کیلوگرم وارد کنید", out[-1][1]
    out = send("دوازده")
    assert "نامعتبر" in out[-1][1] and "ccm1" in out[-1][1]
    out = send("۴۵٬۲۳۰٬۰۰۰")
    assert out[-1][1] == "وزن محصول ccm2 (اسلب ۲) 1405/07 را به کیلوگرم وارد کنید"
    send("38,100,000")
    out = send("17000000")
    assert "ccm4 (بیلت ۱)" in out[-1][1]
    out = send("15500000")
    assert "ccm5 (بیلت ۲)" in out[-1][1]
    out = send("14200000")
    assert out[-1][1] == "تعداد کل ذوب‌های 1405/07 (مجموع ذوب‌های نرمال و با کد فرعی) را وارد کنید"
    out = send("12.5")
    assert "نامعتبر" in out[-1][1]
    out = send("۸۶۰")
    assert "تأیید و ذخیره شود" in out[-1][1] and kb.BTN_PS_CONFIRM in _btns(out[-1][2])
    out = send(kb.BTN_PS_CONFIRM)
    assert len(out) == 2 and out[0][1].startswith("✅ آمار تولید 1405/07 ثبت شد"), out
    assert "45,230,000" in out[0][1] and "860" in out[0][1]
    assert kb.BTN_PS_NEW in _btns(out[-1][2]) and kb.BTN_PS_BACK in _btns(out[-1][2])
    row = db.get_main_goal_production_by_key("m:1405-07")
    assert abs(row["slab_tons"] - 83330) < 1e-6 and abs(row["bloom_tons"] - 17000) < 1e-6
    assert abs(row["billet_tons"] - 29700) < 1e-6 and row["melt_count"] == 860 and row["source_type"] == "manual_text"

    # same month again → asks before replacing; cancel keeps old values and stays in menu
    send(kb.BTN_PS_NEW)
    send("1405/07")
    for v in ("1", "2", "3", "4", "5", "6"):
        send(v)
    out = send(kb.BTN_PS_CONFIRM)
    assert "قبلاً" in out[-1][1] and kb.BTN_PS_REPLACE in _btns(out[-1][2])
    out = send(kb.BTN_CANCEL)
    assert kb.BTN_PS_NEW in _btns(out[-1][2])
    assert db.get_main_goal_production_by_key("m:1405-07")["melt_count"] == 860

    # edit one field
    send(kb.BTN_PS_EDIT)
    out = send("1405/07")
    assert kb.BTN_PS_FIELDS["ccm3"] in _btns(out[-1][2])
    out = send(kb.BTN_PS_FIELDS["ccm3"])
    assert "ccm3 (بلوم) 1405/07" in out[-1][1]
    send("18000000")
    out = send(kb.BTN_PS_CONFIRM)
    assert "جایگزین شد" in out[0][1]
    assert abs(db.get_main_goal_production_by_key("m:1405-07")["bloom_tons"] - 18000) < 1e-6
    assert abs(db.get_main_goal_production_by_key("m:1405-07")["slab_tons"] - 83330) < 1e-6

    # second month via «ثبت اطلاعات ماه دیگر», then re-enter all for it
    send(kb.BTN_PS_NEW)
    send("1405/06")
    for v in ("1000", "1000", "0", "1000", "1000", "10"):
        send(v)
    send(kb.BTN_PS_CONFIRM)
    send(kb.BTN_PS_EDIT)
    send("1405/06")
    send(kb.BTN_PS_REENTER_ALL)
    for v in ("2000", "2000", "0", "2000", "2000", "20"):
        send(v)
    send(kb.BTN_PS_CONFIRM)
    assert db.get_main_goal_production_by_key("m:1405-06")["melt_count"] == 20

    # delete with confirmation
    send(kb.BTN_PS_DELETE)
    out = send("1405/06")
    assert kb.BTN_PS_DEL_CONFIRM in _btns(out[-1][2])
    out = send(kb.BTN_PS_DEL_CONFIRM)
    assert "حذف شد" in out[-1][1] and kb.BTN_PS_NEW in _btns(out[-1][2])
    assert db.get_main_goal_production_by_key("m:1405-06") is None

    # photos are no longer accepted
    out = send(photo=[{"file_id": "P1", "width": 10, "height": 10}])
    assert "حذف شده" in out[-1][1], out

    # leave
    out = send(kb.BTN_PS_BACK)
    assert kb.BTN_PRODUCTION_STATS in _btns(out[-1][2])
    assert "9901" not in app.main_goal_report.pending
    DIALOG_OUT.write_text("\n".join(log), encoding="utf-8")
    if __import__("os").environ.get("PS_DIALOG"):
        Path(__import__("os").environ["PS_DIALOG"]).write_text("\n".join(log), encoding="utf-8")

    # calculations: month without production is excluded (not «0 تن»)
    months = mgp.load_history_from_db(db)
    assert [m.period_key for m in months] == ["m:1405-07"], [m.period_key for m in months]
    assert abs(months[0].production.total_tons - (83330 + 18000 + 29700)) < 1e-6

    # web mirror
    from fastapi.testclient import TestClient

    from web import deps
    from web.app import create_app

    wapp = create_app()
    owner = db.get_user("9901")
    wapp.dependency_overrides[deps.get_db] = lambda: db
    wapp.dependency_overrides[deps.current_user] = lambda: owner
    tc = TestClient(wapp)
    r = tc.get("/reports/main-goal/production-stats")
    assert r.status_code == 200 and "1405/07" in r.text and "ccm1 (اسلب ۱)" in r.text, r.status_code
    form = {"ym": "۱۴۰۵/۰۸", "ccm1": "۱٬۰۰۰", "ccm2": "2,000", "ccm3": "3000", "ccm4": "4000", "ccm5": "5000", "melts": "42"}
    r = tc.post("/reports/main-goal/production-stats", data=form)
    assert r.status_code == 200 and "ثبت شد" in r.text, r.text[:400]
    assert abs(db.get_main_goal_production_by_key("m:1405-08")["total_tons"] - 15) < 1e-9
    r = tc.post("/reports/main-goal/production-stats", data={**form, "melts": "43"})
    assert "قبلاً" in r.text and 'name="replace"' in r.text
    assert db.get_main_goal_production_by_key("m:1405-08")["melt_count"] == 42
    r = tc.post("/reports/main-goal/production-stats", data={**form, "melts": "43", "replace": "1"})
    assert "جایگزین شد" in r.text and db.get_main_goal_production_by_key("m:1405-08")["melt_count"] == 43
    r = tc.post("/reports/main-goal/production-stats", data={**form, "ccm1": "x"})
    assert r.status_code == 400
    r = tc.get("/reports/main-goal/production-stats?edit=1405/08")
    assert 'value="1405/08"' in r.text and 'value="43"' in r.text
    r = tc.get("/reports/main-goal/production-stats?delete=1405/08")
    assert "بله، حذف شود" in r.text
    r = tc.post("/reports/main-goal/production-stats/delete", data={"ym": "1405/08"})
    assert r.status_code == 400 and db.get_main_goal_production_by_key("m:1405-08")
    r = tc.post("/reports/main-goal/production-stats/delete", data={"ym": "1405/08", "confirm": "1"})
    assert r.status_code == 200 and db.get_main_goal_production_by_key("m:1405-08") is None
    r = tc.post("/reports/main-goal/inputs/production-photo", data={})
    assert r.status_code in (404, 405), r.status_code
    print("dialog sample:", DIALOG_OUT)
    print("smoke_production_stats_text OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
