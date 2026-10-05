#!/usr/bin/env python3
"""Offline smoke: گزارش هدف اصلی چندماهه + سناریوها + یادآور الزامی (service, bot, web)."""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MONTHS = [("تیر", 4, 1.00), ("مرداد", 5, 1.10), ("شهریور", 6, 1.20)]


def write_month(tmp: Path, month_fa: str, scale: float, year: str = "۱۴۰۵") -> dict[str, Path]:
    from openpyxl import Workbook

    tmp.mkdir(parents=True, exist_ok=True)
    period = f"{month_fa} {year}"
    prod = tmp / f"آمار تولید {period}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "تولید"
    ws["A1"] = f"آمار تولید {period}"
    ws.append(["CCM", "CCM1", "CCM2", "CCM3", "CCM4", "CCM5"])
    ws.append(["PRODUCT (TON)", 10000 * scale, 8000 * scale, 12000 * scale, 15000 * scale, 9000 * scale])
    wb.save(prod)

    def cons(section_fa: str, tundish: float, heats: float, coat: float, cast: float) -> Path:
        path = tmp / f"مصرف تاندیش {section_fa} {period}.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "مصرف"
        ws["A1"] = f"مصرف تاندیش {section_fa} — {period}"
        ws.append(["NO. TUNDISH", tundish])
        ws.append(["NO. HEAT", heats])
        ws.append(["COATING (Kg)", coat])
        ws.append(["CASTABLE (Kg)", cast])
        wb.save(path)
        return path

    return {
        "production": prod,
        "billet_consumption": cons("بیلت", 40 * scale, 320 * scale, 8000 * scale, 1200 * scale),
        "bloom_consumption": cons("بلوم", 25 * scale, 200 * scale, 5000 * scale, 900 * scale),
        "slab_consumption": cons("اسلب", 30 * scale, 250 * scale, 6000 * scale, 1100 * scale),
    }


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict | None]] = []
        self.docs: list[tuple[str, str]] = []
        self.files: dict[str, Path] = {}

    def send_message(self, chat_id, text, reply_markup=None, **_k):
        self.sent.append((str(chat_id), text, reply_markup))
        return {"message_id": len(self.sent)}

    def send_document(self, chat_id, path, caption=None, **_k):
        self.docs.append((str(chat_id), str(path)))
        return {}

    def download_file(self, file_id, dest):
        import shutil

        shutil.copy2(self.files[file_id], dest)
        return dest


def _btns(markup) -> list[str]:
    return [b["text"] for row in (markup or {}).get("keyboard", []) for b in row]


def patch_dirs(tmp: Path) -> None:
    import bot.main_goal_report_flow as flow
    import services.main_goal_history as mgh
    import web.routers.main_goal_report as webmg

    for mod in (flow, mgh, webmg):
        if hasattr(mod, "UPLOAD_DIR"):
            mod.UPLOAD_DIR = tmp / "uploads"
        if hasattr(mod, "REPORT_DIR"):
            mod.REPORT_DIR = tmp / "reports"


def test_service(tmp: Path) -> None:
    from db.models import Database
    from services import main_goal_history as mgh
    from services import main_goal_report as mg

    db = Database(tmp / "svc.db")
    user = {"bale_user_id": "1", "display_name": "تست", "role": "owner", "active": 1}
    # kind detection
    files = write_month(tmp / "m4", "تیر", 1.0)
    for kind, path in files.items():
        got, src = mg.detect_file_kind(path, filename=path.name)
        assert got == kind, (kind, got, src)
    # content-based detection (neutral filenames)
    import shutil

    neutral = tmp / "neutral_a.xlsx"
    shutil.copy2(files["bloom_consumption"], neutral)
    assert mg.detect_file_kind(neutral, filename="a.xlsx")[0] == "bloom_consumption"
    shutil.copy2(files["production"], tmp / "neutral_b.xlsx")
    assert mg.detect_file_kind(tmp / "neutral_b.xlsx", filename="b.xlsx")[0] == "production"
    print("  kind detection OK")

    # mismatch inside a month set → error, not stored
    bad = dict(files)
    bad["slab_consumption"] = write_month(tmp / "m5", "مرداد", 1.1)["slab_consumption"]
    out = mgh.store_month_set(db, bad, filenames={k: v.name for k, v in bad.items()}, user=user, source="t")
    assert not out.ok and "یکسان نیست" in (out.error_fa or ""), out.error_fa
    assert db.list_main_goal_months() == []
    print("  per-month mismatch OK")

    # store 2 months → <3 warning; scenarios still compute
    for name, mnum, scale in MONTHS[:2]:
        f = write_month(tmp / f"s{mnum}", name, scale)
        out = mgh.store_month_set(db, f, filenames={k: v.name for k, v in f.items()}, user=user, source="t")
        assert out.ok, out.error_fa
    model = mgh.build_history_model(mgh.load_history(db))
    assert model.n_months == 2
    # Continuous 2 months → no gap alarm (old «حداقل ۳ ماه» removed)
    assert not any("وقفه" in w for w in model.warnings())
    res = mgh.scenario_forecast(model, 6)
    assert res.ok and "میانگین" in str(res.sections[0]["rows"][0]["روش"]), res.sections[0]["rows"][0]

    # third month + re-upload same month (replace, not duplicate)
    name, mnum, scale = MONTHS[2]
    f = write_month(tmp / f"s{mnum}", name, scale)
    out = mgh.store_month_set(db, f, filenames={k: v.name for k, v in f.items()}, user=user, source="t")
    assert out.ok and not out.replaced
    out = mgh.store_month_set(db, f, filenames={k: v.name for k, v in f.items()}, user=user, source="t")
    assert out.ok and out.replaced
    months = mgh.load_history(db)
    assert [m.label for m in months] == ["تیر 1405", "مرداد 1405", "شهریور 1405"], [m.label for m in months]
    model = mgh.build_history_model(months)
    assert model.n_months == 3 and not any("وقفه" in w for w in model.warnings())
    billet = model.sections["billet"]
    # billet tons = 24000×(1+1.1+1.2)=79200 ; tundish = 40×3.3=132 → 600 t/tundish
    assert abs(billet.total_tons - 79200) < 0.5, billet.total_tons
    assert abs(billet.tons_per_tundish - 600) < 0.01, billet.tons_per_tundish
    # title rows («مصرف تاندیش بیلت — تیر ۱۴۰۵») must not become materials
    assert [m.name for m in model.materials["billet"]] == ["COATING", "CASTABLE"], [m.name for m in model.materials["billet"]]
    coat = [m for m in model.materials["billet"] if "COATING" in m.name.upper()][0]
    assert abs(coat.per_tundish - 200) < 0.01 and abs(coat.per_ton - 8000 / 24000) < 1e-6

    # scenario 1: billet 30000 t → 50 tundish, coating 10000 kg
    res = mgh.scenario_target(model, {"billet": 30000}, period_text="۳ ماه")
    assert res.ok, res.error_fa
    plan = res.sections[0]["rows"][0]
    assert plan["تاندیش_لازم"] == 50, plan
    need = [r for r in res.sections[1]["rows"] if "COATING" in r["ماده"].upper()][0]
    assert abs(float(need["نیاز_پیشنهادی"]) - 10000) < 1, need
    # total split by share
    res2 = mgh.scenario_target(model, {"total": 60000, "slab": 20000}, period_text="مهر ۱۴۰۵")
    assert res2.ok and set(res2.params["resolved"]) == {"billet", "bloom", "slab"}
    assert abs(sum(res2.params["resolved"].values()) - 60000) < 1

    # scenario 2: linear trend (growing) → future billet > last month
    res3 = mgh.scenario_forecast(model, 6)
    assert res3.ok and res3.params["months_ahead"] == 6
    month_rows = res3.sections[1]["rows"]
    assert len(month_rows) == 6 and month_rows[0]["ماه"] == "مهر 1405", month_rows[0]
    assert float(month_rows[0]["تناژ_بیلت"]) > 24000 * 1.2
    assert res3.sections[0]["rows"][0]["روش"] == "روند خطی"

    pdf, xlsx = mgh.export_scenario(res3, stem="smoke_mg_forecast", report_dir=tmp / "rep")
    assert pdf.stat().st_size > 1000 and xlsx.stat().st_size > 1000
    row = mgh.persist_scenario(db, res3, model, user=user, source="t")
    assert row["id"] and "پیش‌بینی" in row["period_label"]
    print("  history + scenarios + export OK", pdf.stat().st_size, xlsx.stat().st_size)


def test_reminders(tmp: Path) -> None:
    import jdatetime

    from db.models import Database
    from services import mandatory_reminders as rem

    db = Database(tmp / "rem.db")
    db.upsert_user("10", role="owner", display_name="مالک")
    db.upsert_user("11", role="manager", display_name="مدیر")
    db.upsert_user("12", role="technician", display_name="تکنسین")
    cfg = rem.load_config(db)
    assert cfg["enabled"] is False  # default off
    client = FakeClient()
    now = datetime(2026, 10, 5, 10, 0)  # = 1405/07/13
    assert rem.tick(client, db, now=now) is None and not client.sent
    cfg.update(enabled=True, roles=["owner"], user_ids=["12"], due_day=20, lead_days=2, hour=9)
    rem.save_config(db, cfg)
    today = jdatetime.date(1405, 7, 13)
    st = rem.compute_status(db, rem.load_config(db), today=today)
    assert [(y, m) for y, m, _ in st.required] == [(1405, 4), (1405, 5), (1405, 6)]
    # older months missing → overdue immediately
    assert st.phase == "overdue", st.phase
    res = rem.tick(client, db, now=now)
    assert res and res.sent == 2 and {c for c, *_ in client.sent} == {"10", "12"}
    assert "تیر 1405" in client.sent[0][1]
    # same day → no repeat
    assert rem.tick(client, db, now=now) is None
    # mark tir+mordad present → only shahrivar missing, before lead window → upcoming
    with db.connect() as conn:
        for k in ("m:1405-04", "m:1405-05"):
            conn.execute(
                "INSERT INTO main_goal_months (period_key, period_label, sort_key, files_json, stats_json, bale_user_id, created_at, updated_at, created_at_tehran, jalali_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (k, k, k[2:], "{}", "{}", "1", "x", "x", "x", "x"),
            )
    st = rem.compute_status(db, rem.load_config(db), today=today)
    assert st.phase == "upcoming", st.phase
    st = rem.compute_status(db, rem.load_config(db), today=jdatetime.date(1405, 7, 18))
    assert st.phase == "due_soon", st.phase
    n0 = len(client.sent)
    res = rem.tick(client, db, now=datetime(2026, 10, 10, 9, 30))  # 1405/07/18
    assert res and res.reason == "due_soon_start" and len(client.sent) == n0 + 2
    assert "مهلت" in client.sent[-1][1]
    # before configured hour → nothing
    assert rem.tick(client, db, now=datetime(2026, 10, 11, 8, 0)) is None
    # force flag from web
    rem.request_force_send(db, requested_by="11")
    res = rem.tick(client, db, now=datetime(2026, 10, 11, 8, 0))
    assert res and res.reason == "forced" and db.get_setting(rem.FORCE_KEY) is None
    # due day itself (1405/07/20) → second reminder; after → overdue repeats
    res = rem.tick(client, db, now=datetime(2026, 10, 12, 9, 0))
    assert res and res.reason == "due_day", res
    res = rem.tick(client, db, now=datetime(2026, 10, 13, 9, 0))  # 21st → overdue
    assert res and res.reason == "overdue"
    assert rem.tick(client, db, now=datetime(2026, 10, 14, 9, 0)) is None  # repeat_days=3
    assert rem.tick(client, db, now=datetime(2026, 10, 16, 9, 0)).reason == "overdue"
    assert "دریافت‌کنندگان فعلی (2)" in rem.status_text(db)
    print("  reminders schedule/recipients/force OK")


def test_bot_flow(tmp: Path) -> None:
    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database

    patch_dirs(tmp)
    db = Database(tmp / "bot.db")
    db.upsert_user("701", role="manager", display_name="مدیر")
    db.upsert_user("702", role="technician", display_name="تکنسین")
    client = FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]

    def send(uid: str, text: str):
        app.handle_message({"from": {"id": int(uid), "first_name": "x"}, "chat": {"id": int(uid)}, "text": text})
        _c, txt, mk = client.sent[-1]
        return txt, _btns(mk)

    def doc(uid: str, path: Path):
        fid = f"f{len(client.files)}"
        client.files[fid] = path
        app.handle_message({
            "from": {"id": int(uid), "first_name": "x"}, "chat": {"id": int(uid)},
            "document": {"file_id": fid, "file_name": path.name},
        })
        _c, txt, mk = client.sent[-1]
        return txt, _btns(mk)

    txt, btns = send("701", kb.BTN_MAIN_GOAL)
    assert kb.BTN_MG_SCN_TARGET in btns and "هنوز ماهی" in txt
    # scenario without history → gate
    txt, _ = send("701", kb.BTN_MG_SCN_FORECAST)
    assert "هیچ ماهی" in txt

    # sequential: one month
    files = write_month(tmp / "b4", "تیر", 1.0)
    send("701", kb.BTN_MG_START)
    for k in ("production", "billet_consumption", "bloom_consumption", "slab_consumption"):
        txt, btns = doc("701", files[k])
    assert "ذخیره شد" in txt and "ماه" in txt, txt

    # bulk: two months, shuffled order + one unknown file
    f5 = write_month(tmp / "b5", "مرداد", 1.1)
    f6 = write_month(tmp / "b6", "شهریور", 1.2)
    send("701", kb.BTN_MG_BULK)
    order = [f6["slab_consumption"], f5["production"], f6["production"], f5["billet_consumption"],
             f6["billet_consumption"], f5["bloom_consumption"], f5["slab_consumption"]]
    for pth in order:
        txt, _ = doc("701", pth)
    assert "مرداد 1405" in txt and "ذخیره شد" in txt, txt
    txt, _ = doc("701", f6["bloom_consumption"])
    assert "شهریور 1405" in txt and "ماه" in txt, txt
    txt, _ = send("701", kb.BTN_MG_BULK_DONE)
    assert "پایان" in txt
    assert len(db.list_main_goal_months()) == 3

    # scenario 1 via buttons
    send("701", kb.BTN_MG_SCN_TARGET)
    txt, btns = send("701", kb.BTN_MG_P3)
    assert kb.BTN_MG_SEC_BILLET in btns
    send("701", kb.BTN_MG_SEC_BILLET)
    txt, btns = send("701", "۳۰٬۰۰۰")
    assert kb.BTN_MG_COMPUTE in btns
    send("701", kb.BTN_MG_ADD_SECTION)
    send("701", "اسلب ۲۰۰۰۰")
    nd = len(client.docs)
    txt, _ = send("701", kb.BTN_MG_COMPUTE)
    assert "سناریو ۱" in txt and "تاندیش لازم ≈ 50" in txt, txt
    assert len(client.docs) == nd + 2

    # scenario 2
    send("701", kb.BTN_MG_SCN_FORECAST)
    txt, _ = send("701", "۶ ماه آینده")
    assert "سناریو ۲" in txt and len(client.docs) == nd + 4, txt

    # history + delete (manager)
    txt, btns = send("701", kb.BTN_MG_HISTORY)
    assert kb.BTN_MG_DELETE in btns and "شهریور 1405" in txt
    send("701", kb.BTN_MG_DELETE)
    txt, _ = send("701", "1")
    assert "حذف شد" in txt and len(db.list_main_goal_months()) == 2

    # technician denied
    txt, _ = send("702", kb.BTN_MAIN_GOAL)
    assert "دسترسی" in txt

    # reminder settings
    txt, btns = send("701", kb.BTN_BOT_SETTINGS)
    assert kb.BTN_SET_REMINDERS in btns
    txt, btns = send("701", kb.BTN_SET_REMINDERS)
    assert "غیرفعال" in txt and kb.BTN_RM_ENABLE in btns
    txt, btns = send("701", kb.BTN_RM_ROLES)
    send("701", "✅ کاردان مسئول")
    txt, btns = send("701", "⬜ تکنسین")
    assert "✅ تکنسین" in btns
    send("701", kb.BTN_RM_BACK)
    send("701", kb.BTN_RM_USERS)
    txt, _ = send("701", "1")
    assert "تغییر" in txt
    send("701", kb.BTN_RM_SCHEDULE)
    txt, _ = send("701", "40 2 3 9 3")
    assert "باید بین" in txt
    txt, btns = send("701", "7 2 3 9 3")
    assert "ذخیره شد" in txt
    txt, btns = send("701", kb.BTN_RM_ENABLE)
    assert "فعال شد" in txt and kb.BTN_RM_DISABLE in btns
    from services import mandatory_reminders as rem

    cfg = rem.load_config(db)
    assert cfg["enabled"] and cfg["due_day"] == 7 and "technician" in cfg["roles"] and "responsible_officer" not in cfg["roles"]
    n0 = len(client.sent)
    txt, _ = send("701", kb.BTN_RM_SEND_NOW)
    assert "ارسال شد" in txt and len(client.sent) > n0 + 1
    txt, btns = send("701", kb.BTN_BACK_BOT_SETTINGS)
    assert kb.BTN_SET_REMINDERS in btns
    print("  bot flow (upload/bulk/scenarios/delete/reminder settings) OK")


def test_web(tmp: Path) -> None:
    from fastapi.testclient import TestClient

    import web.deps as deps
    from db.models import Database
    from web.app import create_app

    patch_dirs(tmp)
    deps._db = Database(tmp / "web.db")
    app = create_app()
    db = deps._db
    db.upsert_user("801", role="owner", display_name="مالک وب")
    db.upsert_user("802", role="responsible_officer", display_name="کاردان")
    app.dependency_overrides[deps.current_user_optional] = lambda: db.get_user(app.state._uid)
    app.state._uid = "801"
    c = TestClient(app)

    r = c.get("/reports/main-goal")
    assert r.status_code == 200 and "سناریو ۱" in r.text and "ماه‌های ذخیره‌شده" in r.text
    f4 = write_month(tmp / "w4", "تیر", 1.0)
    data = {k: (p.name, p.read_bytes(), "application/octet-stream") for k, p in f4.items()}
    r = c.post("/reports/main-goal/months/upload", files=data)
    assert r.status_code == 200 and "ذخیره شد" in r.text, r.text[:1500]
    # mismatch
    f5 = write_month(tmp / "w5", "مرداد", 1.1)
    bad = dict(data)
    bad["slab_consumption"] = (f5["slab_consumption"].name, f5["slab_consumption"].read_bytes(), "application/octet-stream")
    r = c.post("/reports/main-goal/months/upload", files=bad)
    assert r.status_code == 400 and "یکسان نیست" in r.text
    # bulk
    f6 = write_month(tmp / "w6", "شهریور", 1.2)
    multi = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in list(f5.values()) + list(f6.values())]
    r = c.post("/reports/main-goal/months/bulk", files=multi)
    assert r.status_code == 200 and "مرداد 1405" in r.text and "شهریور 1405" in r.text, r.text[:2000]
    assert len(db.list_main_goal_months()) == 3
    r = c.post("/reports/main-goal/scenario/target", data={"period_text": "۳ ماه", "billet": "30000", "total": ""})
    assert r.status_code == 200 and "download/web_main_goal_target_" in r.text, r.text[:1500]
    import re

    stem = re.search(r"download/(web_main_goal_target_[\d_]+)\.pdf", r.text).group(1)
    # download (REPORT_DIR patched) — route reads module-level REPORT_DIR
    r2 = c.get(f"/reports/main-goal/download/{stem}.pdf")
    assert r2.status_code == 200 and r2.content[:4] == b"%PDF"
    r = c.post("/reports/main-goal/scenario/forecast", data={"months_ahead": "6"})
    assert r.status_code == 200 and "download/web_main_goal_forecast_" in r.text
    r = c.post("/reports/main-goal/scenario/forecast", data={"months_ahead": "99"})
    assert r.status_code == 400
    # officer cannot delete; owner can
    app.state._uid = "802"
    mid = db.list_main_goal_months()[0]["id"]
    r = c.post(f"/reports/main-goal/months/{mid}/delete", follow_redirects=False)
    assert r.status_code == 403
    r = c.get("/settings/reminders")
    assert r.status_code == 403
    app.state._uid = "801"
    r = c.post(f"/reports/main-goal/months/{mid}/delete", follow_redirects=False)
    assert r.status_code == 303 and len(db.list_main_goal_months()) == 2

    # reminder settings
    r = c.get("/settings/reminders")
    assert r.status_code == 200 and "یادآور" in r.text
    r = c.post("/settings/reminders", data={"enabled": "1", "roles": ["owner", "manager"], "user_ids": ["802"],
                                           "due_day": "6", "lead_days": "1", "repeat_days": "2", "hour": "8", "window_months": "3"})
    assert r.status_code == 200 and "ذخیره شد" in r.text
    from services import mandatory_reminders as rem

    cfg = rem.load_config(db)
    assert cfg["enabled"] and cfg["roles"] == ["owner", "manager"] and cfg["user_ids"] == ["802"] and cfg["due_day"] == 6
    r = c.post("/settings/reminders", data={"due_day": "99"})
    assert r.status_code == 400
    r = c.post("/settings/reminders/send-now")
    assert r.status_code == 200 and db.get_setting(rem.FORCE_KEY)
    print("  web (upload/mismatch/bulk/scenarios/delete/reminders) OK")


def main() -> None:
    print("smoke main_goal_history…")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        patch_dirs(tmp)
        for sub in ("svc", "rem", "bot", "web"):
            (tmp / sub).mkdir(parents=True, exist_ok=True)
        test_service(tmp / "svc")
        test_reminders(tmp / "rem")
        test_bot_flow(tmp / "bot")
        test_web(tmp / "web")
    print("ALL OK")


if __name__ == "__main__":
    main()
