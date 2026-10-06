#!/usr/bin/env python3
"""Smoke: production-photo tab gate (casting only) + casting CCM parser + furnace provisional rows.

Real furnace-tab screenshots (not committed — JPGs stay out of git) are read from
MG_FURNACE_TEST_IMAGES (os.pathsep-separated) or the default attachment paths below;
missing files are reported and skipped. Every furnace image MUST be rejected with the alarm.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import _smoke_isolation  # noqa: E402 - real uploads/ stays untouched

_smoke_isolation.isolate_uploads()

_ATT = Path("/home/box/agent-data/agents/8068822b-8774-4875-8e71-86ecdb5001a4/attachments")
DEFAULT_FURNACE = [
    _ATT / "97a0ba5b628d1b077180d9b8010a49509a16145fe04be928fd5cf318f950a8ad.jpg",  # Tir 1405
    _ATT / "c7bcc51239d04d96bb4065ddd29c1d561ed5d5cf2c505359eb72a8ecf8a65421.jpg",  # Mordad 1405
    _ATT / "1b9ae892cd8ad2cb7ec065dcf9a12f20b4cae21faa5382a317863d7234628be4.jpg",  # Shahrivar 1405
]
# Real casting-tab («ریخته گری») phone photos of the monitor (1405-07-14): Tir, Mordad, stale-Mordad
DEFAULT_CASTING_REAL = [
    _ATT / "2764c911797a6fdf8864ce355cad11b4b22c6fd916eac88ecbe9143e5bdb1f98.jpg",
    _ATT / "d406d891c1916c92d705c4118827c690d8571416bf7febbf3b7ed2ffcd218912.jpg",
    _ATT / "e72eda7702fc8c526c92d39062598d9211bbc9ad29b63e0e022acc1e91d65aa3.jpg",
]
CASTING_FIXTURE = ROOT / "samples" / "main_goal" / "shahrivar_1405_production_fixture.png"


def furnace_images() -> list[Path]:
    env = os.environ.get("MG_FURNACE_TEST_IMAGES")
    paths = [Path(p) for p in env.split(os.pathsep) if p] if env else DEFAULT_FURNACE
    found = [p for p in paths if p.is_file()]
    for p in paths:
        if not p.is_file():
            print(f"  ⚠ furnace test image missing (skipped): {p}")
    return found


# OCR snippets observed on real furnace-tab screenshots (noisy Persian OCR)
FURNACE_TEXTS = [
    "کوره پاتیلی | ريخته گری\nشماره کوره وزن مذاب وزن محصول تعداد به اسلب تعداد به بلوم بیلت\n"
    "ازتاریخ: 1305/4/1\nتولید وزن مذاب به تفکیک کوره ها (ماهیانه)",
    "‏[ | کوره پائپلی | ربخنه گری\nشماره تور وزن ملاب وزن محصول اراول ماه\n"
    "1405/05/01 تا 1405/05/31\nشماره کوره | تعداد به بلوم بپلت جرلیات\nتولبد وزن مذاب به تفکیک کوره ها (سالبانه)",
    "1405/06/01 تا 1405/06/31\nتعداد 930\nوزن مذاب 156058238\nوزن محصول 150310814\nتعداد به اسلب 610\n"
    "تعداد به بلوم بیلت 320\nذوب در روز 30",
]

CASTING_ROWS_FA = """کوره | کوره پاتیلی | ریخته گری
از تاریخ: ۱۴۰۵/۰۶/۰۱ تا تاریخ: ۱۴۰۵/۰۶/۳۱
ماشین | تعداد ذوب | وزن تولید | متوسط وزن | از اول سال
اسلب ۱ | ۳۰۵ | ۵۰,۲۰۰,۰۰۰ | ۱۶۴,۵۹۰ | ۳۰۰,۰۰۰,۰۰۰
اسلب ۲ | ۳۰۰ | ۴۹,۱۰۰,۰۰۰ | ۱۶۳,۶۶۶ | ۲۹۰,۰۰۰,۰۰۰
بلوم | ۱۰۵ | ۱۸,۵۰۰,۰۰۰ | ۱۷۶,۱۹۰ | ۱۰۰,۰۰۰,۰۰۰
بیلت ۱ | ۱۱۰ | ۱۷,۸۰۰,۰۰۰ | ۱۶۱,۸۱۸ | ۹۰,۰۰۰,۰۰۰
بیلت ۲ | ۱۰۰ | ۱۵,۸۰۰,۰۰۰ | ۱۵۸,۰۰۰ | ۸۵,۰۰۰,۰۰۰
جمع | ۹۲۰ | ۱۵۱,۴۰۰,۰۰۰ | ۱۶۴,۵۶۵ | ۸۶۵,۰۰۰,۰۰۰
"""

# RTL-ordered OCR (machine name last) + CCM labels, tons units
CASTING_ROWS_RTL = """1405/04/01 1405/04/31
290000 164 50200 305 CCM1
280000 163 49100 300 CCM2
95000 176 18500 105 CCM3
88000 161 17800 110 CCM4
80000 158 15800 100 CCM5
"""

# Noisy OCR snippets from the real casting screenshots: subtotal rows «مجموع اسلب ها» /
# «مجموع بلوم بیلت ها» + chart titles; header shows TODAY («سه شنبه ۱۴ مهر ۱۴۰۵»).
CASTING_REAL_TEXTS = [
    "برنامه ریزی و کنترل تولید فولادسازی - نمابش آماری اطلاعات تولید سه شنبه ۱۴ مهر ۱۳۰۵ &- ۵ 0\n"
    "|| کوره پنیلی EER ۳ ی\nشماره CCM تعداد ذوب she x متوسط وزن محصول\n"
    "مجموع اسلب ها FAR Ale VER ۷۰۸۵ ۰ ۱\nمجموع بلوم بیلت ها ۳۳۳ ۳ ۵۵ ۱۸۶ ۱۲ ۵ و ۹۷\n"
    "وزن محصول به تفکیک ريخته گری (سالیانه) 133,736,625",
    "6 2 ۵ ۸۵ ۲ & ده هن ۱۴ مهرا۱۳۰۵ | gs lol بنمهریزی و کنترل تولید فولاازی\n[ caves |] کوره ||\n"
    "در 44 من را ۴ Fey مجموع اسلب ها || ۳\n| وزن محصول به تفکیک ريخته گری (سالیانه) EMH وزن محصول به تقکیک ريخته گری (ماهیانه)\n"
    "203.234,623 ۱۳ 3 28,523,195",
]

UNKNOWN_TEXT = "گزارش روزانه انبار\n1405/06/10\nموجودی 1200 کیلوگرم\nکوره پاتیلی | ریخته گری"


def expect_sections(r, slab, bloom, billet, tol=1.0):
    assert r.ok, (r.error_fa, r.missing, r.notes, r.ccm_tons)
    assert abs(r.slab_tons - slab) < tol and abs(r.bloom_tons - bloom) < tol and abs(r.billet_tons - billet) < tol, (
        r.slab_tons, r.bloom_tons, r.billet_tons, r.ccm_tons)


def test_text() -> None:
    from services import main_goal_production_ocr as o

    for t in FURNACE_TEXTS:
        r = o.parse_production_ocr_text(t)
        assert not r.ok and r.tab_rejected and r.report_tab == "furnace", r.tab_evidence
        assert r.error_fa == o.ALARM_FURNACE_FA
        assert r.total_tons == 0
    r = o.parse_production_ocr_text(UNKNOWN_TEXT)
    assert not r.ok and r.tab_rejected and r.report_tab == "unknown", r.tab_evidence
    assert "قابل تشخیص نبود" in r.error_fa and "ریخته گری" in r.error_fa

    r = o.parse_production_ocr_text(CASTING_ROWS_FA)
    expect_sections(r, 99300, 18500, 33600)
    assert r.report_tab == "casting" and r.needs_validation and r.period_key == "m:1405-06"
    assert r.section_melts() == {"slab": 605.0, "bloom": 105.0, "billet": 210.0}, r.section_melts()
    assert r.melt_count == 920

    r = o.parse_production_ocr_text(CASTING_ROWS_RTL)
    expect_sections(r, 99300, 18500, 33600)
    assert r.period_key == "m:1405-04" and r.ccm_melts.get(3) == 105, (r.period_key, r.ccm_melts)

    for t in CASTING_REAL_TEXTS:
        tab, ev = o.detect_report_tab(t)
        assert tab == "casting" and not ev["furnace"], ev
        r = o.parse_production_ocr_text(t)
        assert r.report_tab == "casting" and not r.tab_rejected and not r.ok, (r.report_tab, r.tab_evidence)
        assert r.period_key is None, f"header date (today, مهر) must not become the period: {r.period_key}"
    assert o._detect_month("سه شنبه ۱۴ مهر ۱۴۰۵\nگزارش مهر ۱۴۰۵")[:2] == (1405, 7)

    # manual values never become «furnace»
    m = o.apply_manual_corrections(None, year=1405, month=6, slab_tons=1, bloom_tons=1, billet_tons=1)
    assert m.ok and m.report_tab == "manual"
    print("  text: furnace rejected, unknown rejected, casting rows (fa/RTL) parsed OK")


def test_images(tmp: Path) -> None:
    from db.models import Database
    from services import main_goal_persist as mgp
    from services import main_goal_production_ocr as o

    db = Database(str(tmp / "img.db"))
    user = {"bale_user_id": "1", "display_name": "smoke", "role": "owner", "active": 1}
    imgs = furnace_images()
    for p in imgs:
        r = o.ocr_production_image(p)
        assert not r.ok and r.tab_rejected and r.report_tab == "furnace", (p.name, r.tab_evidence)
        assert r.error_fa == o.ALARM_FURNACE_FA, r.error_fa
        out = mgp.store_production_from_ocr(db, p, user=user, source="smoke", ocr_result=r)
        assert not out.ok and out.tab_rejected and out.error_fa == o.ALARM_FURNACE_FA
        # even a hand-built furnace result is refused by the store
        out2 = mgp.store_production_from_ocr(
            db, p, user=user, source="smoke",
            ocr_result=o.from_known_furnace(year=1405, month=6, melts=930, melt_weight_kg=1.5e8,
                                            product_weight_kg=1.5e8, slab_count=610, bloom_billet_count=320),
        )
        assert not out2.ok and out2.tab_rejected
        print(f"  furnace image REJECTED: {p.name[:12]}… ({r.period_label or 'ماه؟'}) evidence={r.tab_evidence.get('furnace')}")
    assert db.list_main_goal_production() == [], "rejected images must not be stored"

    for p in DEFAULT_CASTING_REAL:
        if not p.is_file():
            print(f"  ⚠ real casting image missing (skipped): {p.name[:12]}…")
            continue
        r = o.ocr_production_image(p)
        assert r.report_tab == "casting" and not r.tab_rejected, (p.name, r.tab_evidence)
        out = mgp.store_production_from_ocr(db, p, user=user, source="smoke", ocr_result=r)
        if not out.ok:  # digits unreadable on moiré monitor photos → manual-correction path, nothing stored
            assert not out.tab_rejected and out.needs_confirm, out
        print(f"  real casting photo accepted as casting tab: {p.name[:12]}… ok={out.ok}")
    with db.connect() as c:
        c.execute("DELETE FROM main_goal_production")

    r = o.ocr_production_image(CASTING_FIXTURE)
    expect_sections(r, 99300, 18500, 33600)
    assert r.report_tab == "casting" and r.period_key == "m:1405-06"
    out = mgp.store_production_from_ocr(db, CASTING_FIXTURE, user=user, source="smoke", ocr_result=r)
    assert out.ok, out.error_fa
    row = db.get_main_goal_production_by_key("m:1405-06")
    assert row["report_tab"] == "casting" and row["source_status"] == mgp.STATUS_CASTING_UNVALIDATED
    assert row["slab_melt_count"] == 605 and row["bloom_melt_count"] == 105 and row["billet_melt_count"] == 210
    assert row["ccm3_tons"] == 18500
    print(f"  casting fixture image stored: {len(imgs)} furnace images rejected")


def test_provisional(tmp: Path) -> None:
    import json

    from db.models import Database
    from services import main_goal_persist as mgp
    from services import main_goal_production_ocr as o

    dbp = tmp / "prov.db"
    db = Database(str(dbp))
    # legacy furnace-tab row written directly (pre-gate seed shape)
    known = o.from_known_furnace(year=1405, month=5, melts=682, melt_weight_kg=115489566,
                                 product_weight_kg=112268212, slab_count=411, bloom_billet_count=273)
    db.upsert_main_goal_production(
        period_key=known.period_key, period_label=known.period_label, year=1405, month=5,
        sort_key="1405-05", slab_tons=known.slab_tons, bloom_tons=known.bloom_tons, billet_tons=0,
        total_tons=known.total_tons, report_tab="furnace", source_type="manual",
        notes_json=json.dumps([]), missing_json="[]", bale_user_id="seed", source="seed",
        created_at_tehran="x", jalali_date="x",
    )
    db = Database(str(dbp))  # re-open → migration flags it
    row = db.get_main_goal_production_by_key("m:1405-05")
    assert row and row["source_status"] == mgp.STATUS_FURNACE_PROVISIONAL, row and row["source_status"]
    comp = {c["period_key"]: c for c in mgp.month_completeness(db)}
    c = comp["m:1405-05"]
    assert c["production_provisional"] and not c["has_production"] and c["provisional_total_tons"] > 0
    assert any(mgp.NEEDS_CASTING_NOTE_FA in m for m in c["missing"])
    assert mgp.usable_production(db, "m:1405-05") is None
    assert mgp.load_history_from_db(db) == []  # furnace-only month → no section tonnage
    # casting photo for the same month replaces the provisional row
    cast = o.from_known_casting(year=1405, month=5, ccm_tons={1: 40000, 2: 30000, 3: 15000, 4: 14000, 5: 13000},
                                ccm_melts={1: 240, 2: 180, 3: 85, 4: 90, 5: 87})
    placeholder = tmp / "c5.txt"
    placeholder.write_text("x", encoding="utf-8")
    out = mgp.store_production_from_ocr(db, placeholder, user={"bale_user_id": "1"}, source="smoke", ocr_result=cast)
    assert out.ok and out.replaced
    row = db.get_main_goal_production_by_key("m:1405-05")
    assert row["source_status"] == mgp.STATUS_VALID and row["report_tab"] == "casting"
    months = mgp.load_history_from_db(db)
    assert len(months) == 1 and abs(months[0].production.billet_tons - 27000) < 0.1
    print("  furnace rows → provisional, excluded; casting replaces OK")


def test_bot_and_web(tmp: Path) -> None:
    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database
    from services import main_goal_production_ocr as o

    import bot.main_goal_report_flow as flow
    import services.main_goal_history as mgh_mod
    import services.main_goal_persist as mgp
    import web.routers.main_goal_report as webmg

    for mod in (flow, mgh_mod, webmg, mgp):
        if hasattr(mod, "UPLOAD_DIR"):
            mod.UPLOAD_DIR = tmp / "uploads"
        if hasattr(mod, "REPORT_DIR"):
            mod.REPORT_DIR = tmp / "reports"

    imgs = furnace_images()
    furnace = imgs[0] if imgs else None

    class FakeClient:
        def __init__(self):
            self.sent, self.files = [], {}

        def send_message(self, chat_id, text, reply_markup=None, **_k):
            self.sent.append((str(chat_id), text, reply_markup))
            return {"message_id": len(self.sent)}

        def send_document(self, *a, **k):
            return {}

        def download_file(self, file_id, dest):
            import shutil

            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.files[file_id], dest)
            return dest

    db = Database(str(tmp / "bot.db"))
    db.upsert_user("901", role="owner", display_name="مالک")
    client = FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]

    def send(text):
        app.handle_message({"from": {"id": 901, "first_name": "x"}, "chat": {"id": 901}, "text": text})
        return client.sent[-1][1]

    def photo(path: Path):
        fid = f"p{len(client.files)}"
        client.files[fid] = path
        app.handle_message({"from": {"id": 901, "first_name": "x"}, "chat": {"id": 901},
                            "photo": [{"file_id": fid, "width": 100, "height": 100}]})
        return client.sent[-1][1]

    send(kb.BTN_MAIN_GOAL)
    send(kb.BTN_MG_INPUTS)
    txt = send(kb.BTN_MG_INPUT_PROD)
    assert "ریخته گری" in txt and "کوره" in txt, txt
    if furnace:
        txt = photo(furnace)
        assert txt == o.ALARM_FURNACE_FA, txt
        assert db.list_main_goal_production() == []
        assert app.main_goal_report.pending.get("901", {}).get("await") == "prod_photo"
        print("  bot: furnace photo → alarm, not stored, still awaiting casting photo")
    txt = photo(CASTING_FIXTURE)
    assert "ذخیره شد" in txt and "تب ریخته‌گری" in txt, txt
    assert db.get_main_goal_production_by_key("m:1405-06")["report_tab"] == "casting"
    print("  bot: casting fixture photo stored")

    # ---- web
    from fastapi.testclient import TestClient

    import web.deps as deps
    from web.app import create_app

    deps._db = Database(str(tmp / "web.db"))
    wdb = deps._db
    wdb.upsert_user("902", role="owner", display_name="مالک وب")
    wapp = create_app()
    wapp.dependency_overrides[deps.current_user_optional] = lambda: wdb.get_user("902")
    c = TestClient(wapp)
    r = c.get("/reports/main-goal")
    assert r.status_code == 200 and "فقط تب «ریخته گری»" in r.text
    if furnace:
        r = c.post("/reports/main-goal/inputs/production-photo",
                   files={"photo": (furnace.name, furnace.read_bytes(), "image/jpeg")})
        assert r.status_code == 400 and "این تصویر از تب کوره است" in r.text, r.text[:800]
        assert wdb.list_main_goal_production() == []
        print("  web: furnace photo → 400 + alarm, not stored")
    r = c.post("/reports/main-goal/inputs/production-photo",
               files={"photo": ("cast.png", CASTING_FIXTURE.read_bytes(), "image/png")})
    assert r.status_code == 200 and "ذخیره شد" in r.text, r.text[:800]
    # provisional note on page
    known = o.from_known_furnace(year=1405, month=4, melts=750, melt_weight_kg=127705938,
                                 product_weight_kg=123513760, slab_count=423, bloom_billet_count=327)
    wdb.upsert_main_goal_production(
        period_key="m:1405-04", period_label="تیر 1405", year=1405, month=4, sort_key="1405-04",
        slab_tons=known.slab_tons, bloom_tons=known.bloom_tons, billet_tons=0, total_tons=known.total_tons,
        report_tab="furnace", source_status=mgp.STATUS_FURNACE_PROVISIONAL, source_type="manual",
        bale_user_id="seed", source="seed", created_at_tehran="x", jalali_date="x",
    )
    r = c.get("/reports/main-goal")
    assert r.status_code == 200 and "نیاز به عکس تب ریخته‌گری" in r.text
    print("  web: casting stored; provisional furnace note shown")


def main() -> int:
    test_text()
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        test_images(t)
        test_provisional(t)
        test_bot_and_web(t)
    print("SMOKE PRODUCTION TAB OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
