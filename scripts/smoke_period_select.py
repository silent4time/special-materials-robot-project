#!/usr/bin/env python3
"""Smoke (temp DB only): day-based report periods + heat cross-check.

* presets 3/6/12 months = 90/180/365 days ending at the latest data date;
* partial month counts its covered days, label «مهر ۱۴۰۵ (۲۹ روز، تا ۰۷/۲۹)»;
* custom: N days / from–to date / month list; future months/dates rejected (history only);
* months without data excluded (not zero); per-day columns use real covered days;
* heats: sequence log vs production > 2% → alert (bot + web);
* stale open 1402–1404 rows dropped on sequence import;
* bot inline month picker: 🔒 future months, taps rejected with an alert; web month
  checkboxes for future months disabled.
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    import jdatetime

    from smoke_phase1 import FakeClient, _btns

    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database
    from services import main_goal_persist as mgp
    from services import main_goal_sequences as seq
    from services import period_select as ps
    from services import permissions as perm
    from services import production_manual as pm

    TODAY = jdatetime.date(1405, 7, 29)
    ps.today = lambda: TODAY  # freeze «today» for the test

    db = Database()
    perm.bind(db)
    db.upsert_user("9902", "owner", display_name="مالک")
    u = {"bale_user_id": "9902", "display_name": "مالک"}
    # production (tons from kg) Tir/Mordad/Shahrivar; Mehr only in the sequence log
    pm.store(db, pm.ManualProduction(1405, 4, {"ccm1": 43716085, "ccm2": 26292880, "ccm3": 1114630, "ccm4": 25331481, "ccm5": 28207913}, 750), user=u, source="bot")
    pm.store(db, pm.ManualProduction(1405, 5, {"ccm1": 40e6, "ccm2": 28e6, "ccm3": 0, "ccm4": 22e6, "ccm5": 23e6}, 686), user=u, source="bot")
    pm.store(db, pm.ManualProduction(1405, 6, {"ccm1": 47649419, "ccm2": 51687318, "ccm3": 0, "ccm4": 24915702, "ccm5": 27184421}, 928), user=u, source="bot")
    melts = {4: {"slab": 436, "billet": 326, "bloom": 7}, 5: {"slab": 415, "billet": 268}, 6: {"slab": 617, "billet": 325}, 7: {"slab": 700, "billet": 500}}
    for mo, secs in melts.items():
        for sec, me in secs.items():
            db.upsert_main_goal_consumption(
                period_key=f"m:1405-{mo:02d}", period_label=f"m{mo}", year=1405, month=mo,
                sort_key=f"1405-{mo:02d}", section=sec, tundish_count=me / 7, melt_count=me,
                bale_user_id="9902", source="bot", jalali_date="1405/07/29", created_at_tehran="x",
            )
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO main_goal_tundish_sequences (period_key, period_label, section, shroud_replaced,"
            " outer_nozzle_replaced, tube_changer, source, bale_user_id, created_at, jalali_date,"
            " first_cast_start, last_cast_end) VALUES ('m:1405-07','مهر','slab',0,0,0,'bot','9902','x','x',"
            " '1405/07/29 07:30', '1405/07/29 07:30')"
        )
    latest = ps.latest_data_date(db)
    assert latest == TODAY, latest
    assert ps.month_label(1405, 7, latest) == "مهر ۱۴۰۵ (۲۹ روز، تا ۰۷/۲۹)"
    assert ps.coverage(1405, 7, latest) == (29, latest) and ps.coverage(1405, 6, latest)[0] == 31
    w = ps.preset_window(latest, 3)
    assert w.days == 90 and ps.fmt_date(w.start) == "۱۴۰۵/۰۵/۰۲"
    assert ps.preset_window(latest, 6).days == 180 and ps.preset_window(latest, 12).days == 365

    # 3 months = 90 days: Mordad 30/31 + Shahrivar 31 + Mehr 29 (partial)
    model, res, warns = mgp.build_range_report(db, mgp.range_last_n(3))
    labels = [m.label for m in model.months]
    assert labels == ["مرداد ۱۴۰۵ (۳۰ از ۳۱ روز)", "شهریور ۱۴۰۵", "مهر ۱۴۰۵ (۲۹ روز، تا ۰۷/۲۹)"], labels
    assert "مبنا: ۹۰ روز دارای داده" in res.summary and "مهر ۱۴۰۵ (۲۹ روز، تا ۰۷/۲۹)" in res.summary
    total_row = res.sections[0]["rows"][-1]
    assert "تناژ_روزانه" in res.sections[0]["columns"] and "ذوب_روزانه" in res.sections[0]["columns"]
    exp_tons = (40e3 + 28e3 + 22e3 + 23e3) * 30 / 31 + (47649.419 + 51687.318 + 24915.702 + 27184.421)
    assert abs(total_row["تناژ_بازه"] - round(exp_tons, 1)) < 0.2, total_row
    assert abs(total_row["تناژ_روزانه"] - round(exp_tons / 61, 1)) < 0.2  # Mehr has no production days
    # 12 months: months without data excluded (listed), not zero
    _m, res12, w12 = mgp.build_range_report(db, mgp.range_last_n(12))
    assert any("ماه‌های بدون داده" in x and "صفر منظور نشد" in x for x in w12)
    assert "۱۲۲ روز دارای داده" in res12.summary, res12.summary.splitlines()[1]
    # heat alert: Tir 769 vs 750 (+2.5%) → alert; Shahrivar 942 vs 928 (+1.5%) → none
    assert mgp.heat_check(db, "m:1405-04")["alert"] and not mgp.heat_check(db, "m:1405-06")["alert"]
    assert any("اختلاف ذوب تیر" in x and "خروج از تاندیش" in x for x in w12)
    assert "در محدودهٔ مجاز" in pm.heat_note_fa(db, pm.from_row(pm.get_month(db, 1405, 6)))
    # custom: days / dates / months, future rejected
    assert mgp.parse_range_choice("۴۵ روز").n_days == 45 and mgp.parse_range_choice("45").kind == "days"
    sp = mgp.parse_range_choice("1405/06/15 تا 1405/07/10")
    _m, rd, _w = mgp.build_range_report(db, sp)
    assert "شهریور ۱۴۰۵ (۱۷ از ۳۱ روز)" in rd.summary and "مهر ۱۴۰۵ (۱۰ از ۲۹ روز)" in rd.summary
    assert mgp.future_error_fa(mgp.range_months([(1405, 8)])) and not mgp.future_error_fa(mgp.range_months([(1405, 7)]))
    assert mgp.future_error_fa(mgp.parse_range_choice("1405/07/01 تا 1405/08/05"))
    assert mgp.future_error_fa(mgp.range_custom((1405, 6), (1405, 9)))
    # stale open rows (no end / end == start, > 2 months before the file's last month) dropped
    R = seq.SequenceRow if hasattr(seq, "SequenceRow") else None
    if R is not None:
        import dataclasses

        f0 = {f.name: None for f in dataclasses.fields(R)}
        mk = lambda s, e, mins: R(**{**f0, "first_cast_start": s, "last_cast_end": e, "sequence_minutes": mins})  # noqa: E731
        rows = [mk("1404/02/16 00:25", "1404/02/16 00:25", 768056.0), mk("1405/07/20 01:00", "", 9305.0),
                mk("1405/07/01 01:00", "1405/07/01 09:00", 480.0)]
        keep, stale = seq.drop_stale_open_rows(rows)
        assert len(stale) == 1 and len(keep) == 2

    # ---- bot: presets + inline month picker with 🔒 future months
    client = FakeClient()
    edits: list = []
    alerts: list = []
    client.edit_message_reply_markup = lambda chat, mid, mk: edits.append(mk) or {}
    client.answer_callback_query = lambda cid, text=None, show_alert=False: alerts.append((text, show_alert)) or {}
    app = BotApp(client, db)

    def send(text):
        n = len(client.sent)
        app.handle_message({"from": {"id": 9902, "first_name": "x"}, "chat": {"id": 9902}, "text": text})
        return client.sent[n:]

    def tap(data):
        app.handle_callback_query({"id": "c1", "data": data, "from": {"id": 9902},
                                   "message": {"message_id": 5, "chat": {"id": 9902}}})

    send(kb.BTN_MAIN_GOAL)
    out = send(kb.BTN_MG_REPORT_REQ) if hasattr(kb, "BTN_MG_HISTORY_REPORT") else []
    if not out or kb.BTN_MG_RANGE_3 not in _btns(out[-1][2]):
        app.main_goal_report.start_range_report({"from": {"id": 9902, "first_name": "x"}, "chat": {"id": 9902}, "text": ""})
        out = client.sent[-1:]
    assert kb.BTN_MG_RANGE_3 in _btns(out[-1][2]) and "۹۰ روز" in kb.BTN_MG_RANGE_3
    out = send(kb.BTN_MG_RANGE_CUSTOM)
    pick = out[-1][2]["inline_keyboard"]
    flat = [b for row in pick for b in row]
    aban = next(b for b in flat if "آبان" in b["text"])
    mehr = next(b for b in flat if "مهر" in b["text"])
    assert aban["text"] == "🔒 آبان" and mehr["text"] == "مهر (جاری)", (aban, mehr)
    tap(aban["callback_data"])
    assert alerts[-1][1] is True and "🔒" in alerts[-1][0]
    tap(f"{kb.CB_MG_PICK_PREFIX}t|1405|8")  # forged future toggle → rejected too
    assert alerts[-1][1] is True
    tap(mehr["callback_data"])
    tap(f"{kb.CB_MG_PICK_PREFIX}t|1405|6")
    assert any("✅ شهریور" == b["text"] for row in edits[-1]["inline_keyboard"] for b in row)
    n = len(client.sent)
    tap(f"{kb.CB_MG_PICK_PREFIX}go")
    texts = "\n".join(o[1] for o in client.sent[n:])
    assert "مهر ۱۴۰۵ (۲۹ روز، تا ۰۷/۲۹)" in texts, texts[:500]
    # typed future date in the custom step is rejected
    send(kb.BTN_CANCEL)
    app.main_goal_report.start_range_report({"from": {"id": 9902, "first_name": "x"}, "chat": {"id": 9902}, "text": ""})
    send(kb.BTN_MG_RANGE_CUSTOM)
    out = send("1405/07/01 تا 1405/08/05")
    assert "🔒" in out[-1][1]
    send(kb.BTN_CANCEL)

    # ---- web: month checkboxes (future disabled) + days form + future rejection
    from fastapi.testclient import TestClient

    from web import deps
    from web.app import create_app

    wapp = create_app()
    owner = db.get_user("9902")
    wapp.dependency_overrides[deps.get_db] = lambda: db
    wapp.dependency_overrides[deps.current_user] = lambda: owner
    tc = TestClient(wapp)
    r = tc.get("/reports/main-goal")
    assert r.status_code == 200
    assert 'value="1405-8" disabled' in r.text and "(جاری، ناقص)" in r.text
    assert "۹۰ روز" in r.text and "اختلاف ذوب تیر" in r.text
    r = tc.post("/reports/main-goal/report/range", data={"range_kind": "months", "months": ["1405-8"]})
    assert r.status_code == 400 and "🔒" in r.text
    r = tc.post("/reports/main-goal/report/range", data={"range_kind": "days", "date_from": "1405/07/01", "date_to": "1405/08/03"})
    assert r.status_code == 400 and "🔒" in r.text
    r = tc.post("/reports/main-goal/report/range", data={"range_kind": "days", "n_days": "30", "confirm_partial": "1"})
    assert r.status_code == 200, r.text[:300]
    print("smoke_period_select: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
