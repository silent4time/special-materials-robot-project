#!/usr/bin/env python3
"""v1.7.2 smoke: «تناژ هدف» only uses the chosen section's items (bot = web) and the
free-text question «برای ده تن تولید شمش بیلت چقدر مواد لازم است» (bot + web assistant).

Synthetic data only (temp DB / dirs via _smoke_env); never data/bot.db.
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402  — temp DB/reports/uploads before config import

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import _smoke_isolation  # noqa: E402

_smoke_isolation.isolate_uploads()

OWNER = "9701"
MANAGER = "9702"
TECH = "9703"
QUESTION = "برای ده تن تولید شمش بیلت چقدر مواد لازم است"


def _inventory():
    import pandas as pd

    zero = {
        "work_order": "", "casting_floor": 0, "billet_renovation": 0, "billet_patching": 0,
        "bloom_renovation": 0, "bloom_patching": 0, "slab_renovation": 0, "slab_patching": 0,
        "rate_group": "", "shared": "خیر", "critical_point": 10, "origin": "داخلی", "priority": 1,
        "contractor_or_company": "شرکت",
    }
    rows = [
        # شرود: «سطح ریخته گری اسلب» (as in the real منبع اصلی, code 1581) → slab only
        dict(zero, category_code="1581", id="378481807741J", product_name="LADLE SHROUD L-0102",
             keyword="شرود", usage_location="سطح ریخته گری اسلب", quantity=825, unit="NO", casting_floor=1),
        dict(zero, category_code="1203", id="378112341203A", product_name="بتن بیلت نمونه",
             keyword="بتن بیلت", usage_location="بیلت", quantity=120, unit="Kg", billet_patching=2),
        dict(zero, category_code="1207", id="378700001207C", product_name="بتن اسلب نمونه",
             keyword="بتن اسلب", usage_location="اسلب", quantity=90, unit="Kg", slab_patching=2,
             contractor_or_company="پیمانکار"),
        # shared «بلوم / بیلت» → both bloom and billet
        dict(zero, category_code="1712", id="378900001712N", product_name="نازل 18",
             keyword="نازل", usage_location="بلوم / بیلت", quantity=7, unit="NO", billet_patching=1,
             bloom_patching=1),
        dict(zero, category_code="1655", id="378800001655B", product_name="روکش بلوم نمونه",
             keyword="روکش بلوم", usage_location="بلوم", quantity=80, unit="Kg", bloom_patching=1,
             contractor_or_company="پیمانکار"),
        # blank محل استفاده + rates → section from rate columns (slab)
        dict(zero, category_code="1690", id="378800001690X", product_name="پودر قالب اسلب",
             keyword="پودر قالب", usage_location="", quantity=40, unit="Kg", slab_patching=3),
        # blank محل استفاده and no rates → unknown: stays in the section whose file recorded it
        dict(zero, category_code="1695", id="378800001695Y", product_name="الیاف",
             keyword="الیاف", usage_location="", quantity=5, unit="Kg"),
    ]
    return pd.DataFrame(rows)


def _seed(db) -> None:
    from services.main_source import persist_primary_frame

    db.upsert_user(OWNER, role="owner", display_name="مالک")
    db.upsert_user(MANAGER, role="manager", display_name="مدیر")
    db.upsert_user(TECH, role="technician", display_name="تکنسین")
    persist_primary_frame(db, _inventory(), bale_user_id=OWNER)
    for mo, f in ((4, 1.0), (5, 1.1), (6, 1.2)):
        key = f"m:1405-{mo:02d}"
        base = dict(period_key=key, period_label=f"1405/{mo:02d}", year=1405, month=mo,
                    sort_key=f"1405-{mo:02d}", bale_user_id=OWNER, actor_display_name="مالک",
                    source="smoke", created_at_tehran="2026-09-01 12:00", jalali_date="1405/06/10")
        db.upsert_main_goal_production(**base, slab_tons=1000 * f, bloom_tons=800 * f, billet_tons=1000 * f,
                                       total_tons=2800 * f, report_tab="casting", source_type="manual",
                                       source_status="recorded")

        def mats(*rows):
            return [dict(name=n, quantity=q * f, unit=u, sort_order=i) for i, (n, q, u) in enumerate(rows)]

        db.upsert_main_goal_consumption(**base, section="billet", tundish_count=10 * f, melt_count=80 * f,
                                        materials=mats(("بتن بیلت", 100, "kg"), ("نازل", 10, "عدد"),
                                                       ("الیاف", 5, "kg"),
                                                       # sequence-log pseudo line + a slab item in the billet file
                                                       ("تعویض شرود", 3, "عدد"), ("بتن اسلب", 4, "kg"),
                                                       ("تعویض نازل بیرونی", 0, "عدد")))
        db.upsert_main_goal_consumption(**base, section="slab", tundish_count=8 * f, melt_count=60 * f,
                                        materials=mats(("بتن اسلب", 200, "kg"), ("تعویض شرود", 4, "عدد"),
                                                       ("پودر قالب", 30, "kg"), ("تعویض نازل بیرونی", 0, "عدد")))
        db.upsert_main_goal_consumption(**base, section="bloom", tundish_count=6 * f, melt_count=40 * f,
                                        materials=mats(("روکش بلوم", 50, "kg"), ("نازل", 6, "عدد"),
                                                       ("تعویض شرود", 0, "عدد")))


def _need_names(res) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in res.sections[1]["rows"]:
        out.setdefault(r["بخش"], set()).add(r["ماده"])
    return out


def test_service(db) -> None:
    from analytics.frames import load_primary_inventory
    from analytics.section_rules import row_sections
    from services import main_goal_history as mgh

    user = db.get_user(OWNER)
    inv = load_primary_inventory(db, user)
    secs = dict(zip(inv["keyword"], row_sections(inv)))
    assert secs["شرود"] == ("slab",), secs
    assert secs["نازل"] == ("billet", "bloom"), secs
    assert secs["پودر قالب"] == ("slab",), secs  # blank location → rate columns
    assert secs["الیاف"] == (), secs  # unknown

    model = mgh.build_history_model(mgh.load_history(db), inv)
    # billet only
    res = mgh.scenario_target(model, {"billet": 10}, period_text="")
    assert res.ok, res.error_fa
    names = _need_names(res)
    assert set(names) == {"بیلت"}, names
    assert names["بیلت"] == {"بتن بیلت", "نازل", "الیاف"}, names
    assert res.sections[-1]["title"] == "قواعد و محدودیت‌ها"
    flat = str(res.sections[:-1])  # every table except the notes
    for bad in ("تعویض شرود", "بتن اسلب", "روکش بلوم", "پودر قالب", "تاندیش_اسلب", "تناژ_بلوم"):
        assert bad not in flat, (bad, "leaked into a billet-only report")
    notes = " ".join(r["یادداشت"] for r in res.sections[-1]["rows"])
    assert "«تعویض شرود» (متعلق به اسلب)" in notes and "«بتن اسلب» (متعلق به اسلب)" in notes, notes
    assert "«تعویض نازل بیرونی»" in notes  # zero history → listed as hidden
    need = {r["ماده"]: r for r in res.sections[1]["rows"]}
    # rate = Σqty/Σtons = 100/1000 kg per t → 10 t = 1 kg; stock of the billet row
    assert abs(float(need["بتن بیلت"]["نیاز_پیشنهادی"]) - 1.0) < 1e-6, need["بتن بیلت"]
    assert float(need["بتن بیلت"]["موجودی_منبع_اصلی"]) == 120
    assert float(need["نازل"]["موجودی_منبع_اصلی"]) == 7  # shared row
    assert need["الیاف"]["موجودی_منبع_اصلی"] in (5, 5.0), need["الیاف"]
    print("  billet-only (no شرود / slab / bloom items, shared + unknown kept) OK")

    res = mgh.scenario_target(model, {"slab": 100}, period_text="")
    names = _need_names(res)
    assert names == {"اسلب": {"بتن اسلب", "تعویض شرود", "پودر قالب"}}, names
    shroud = [r for r in res.sections[1]["rows"] if r["ماده"] == "تعویض شرود"][0]
    assert float(shroud["موجودی_منبع_اصلی"]) == 825, shroud
    assert "بتن بیلت" not in str(res.sections[:-1]) and "نازل" not in str(res.sections[1])
    print("  slab-only (شرود here, no billet items) OK")

    res = mgh.scenario_target(model, {"bloom": 50}, period_text="")
    names = _need_names(res)
    assert names == {"بلوم": {"روکش بلوم", "نازل"}}, names
    print("  bloom-only + shared nozzle OK")

    res = mgh.scenario_target(model, {"total": 2800}, period_text="")
    names = _need_names(res)
    assert set(names) == {"بیلت", "بلوم", "اسلب"} and "نازل" in names["بیلت"] and "نازل" in names["بلوم"]
    assert "تعویض شرود" in names["اسلب"] and "تعویض شرود" not in names["بیلت"] | names["بلوم"]
    fc = mgh.scenario_forecast(model, 3)
    assert fc.ok and "تعویض شرود" not in {r["ماده"] for r in fc.sections[2]["rows"] if r["بخش"] == "بیلت"}
    print("  total / forecast keep the same section rule OK")


def test_parse() -> None:
    from services import assistant_brain as ab
    from services import target_tonnage_question as tq

    cases = {
        QUESTION: {"billet": 10},
        "برای ۱۰ تن بیلت چی لازمه؟": {"billet": 10},
        "برای ١٠ تن شمش بيلت چه موادي لازم است": {"billet": 10},  # Arabic digits/yeh
        "10 tons billet": {"billet": 10},
        "۱۰تن بیلت": {"billet": 10},
        "بیست و پنج هزار تن شمش بلوم چه موادی میخواد": {"bloom": 25000},
        "۱۰٬۰۰۰ تن اسلب": {"slab": 10000},
        "10,000 تُن اسلب چقدر مواد میخواد": {"slab": 10000},
        "صد تن بیلت": {"billet": 100},
        "هزار تن اسلب": {"slab": 1000},
        "۲ هزار تن بلوم": {"bloom": 2000},
        "1.5 هزار تن بلوم": {"bloom": 1500},
        "بیلت ۱۰۰۰ تن و اسلب ۲۰۰۰ تن": {"billet": 1000, "slab": 2000},
        "برای ده هزار کیلو شمش بیلت چقدر مواد لازم است": {"billet": 10},
        "۱۰۰۰۰ کیلوگرم بلوم": {"bloom": 10},
        "10000kg اسلب": {"slab": 10},
        "۱۰ هزار کیلو گرم شمش بیلت": {"billet": 10},
        "ده هزار کیلوگرمی بیلت": {"billet": 10},
        "10000 kgs slab": {"slab": 10},
        "10000KG تولید شمش بیلت": {"billet": 10},
        "پنج هزار کیلو بلوم": {"bloom": 5},
        "۱۵۰۰ کیلوگرم بیلت": {"billet": 1.5},
        "برای ده هزار کیلو شمش بیلت چند تاندیش لازم است": {"billet": 10},
    }
    for text, want in cases.items():
        q = tq.parse(text)
        assert q is not None and q.targets == want, (text, None if q is None else (q.targets, q.tons, q.section))
        it = ab.rule_route(text)
        assert it is not None and it.name == ab.I_TARGET_TONNAGE, (text, it)
    q = tq.parse("برای تولید شمش بیلت چقدر مواد لازم است")
    assert q and q.missing == "tons" and q.section == "billet", q
    q = tq.parse("برای ده تن تولید شمش چقدر مواد لازم است")
    assert q and q.missing == "section" and q.tons == 10, q
    q = tq.parse("برای ده هزار کیلو تولید شمش چقدر مواد لازم است")
    assert q and q.missing == "section" and q.tons == 10, q
    for text in ("نیاز مواد برای ۱۰ تاندیش اسلب", "۱۰ تن آجر داریم؟", "۱۰ کیلو آجر داریم؟",
                 "ده هزار کیلو آجر داریم؟", "مصرف شهریور بیلت", "موجودی شرود",
                 "شرود چند روز کافیه", "شهریور چند تا تاندیش اسلب زدیم"):
        assert tq.parse(text) is None, text
    assert ab.rule_route("نیاز مواد برای ۱۰ تاندیش اسلب").name == ab.I_N_TUNDISH
    assert tq.parse_amount("ده") == 10 and tq.parse_amount("۲۰ هزار") == 20000 and tq.parse_amount("سلام") is None
    assert tq.parse_amount("ده هزار کیلو") == 10
    assert tq.parse_amount("10000kg") == 10
    assert tq.parse_amount("۱۵۰۰ کیلوگرم") == 1.5
    assert tq.parse_amount("۱۰ هزار کیلو گرم") == 10
    print("  free-text parsing (digits / words / تن و کیلوگرم / not hijacking other intents) OK")


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict | None]] = []
        self.docs: list[tuple[str, str, str]] = []

    def send_message(self, chat_id, text, reply_markup=None, **_k):
        self.sent.append((str(chat_id), text, reply_markup))
        return {"message_id": len(self.sent)}

    def send_document(self, chat_id, path, caption=None, **_k):
        self.docs.append((str(chat_id), str(path), caption or ""))
        return {}


def _btns(markup) -> list[str]:
    return [b["text"] if isinstance(b, dict) else str(b) for row in (markup or {}).get("keyboard", []) for b in row]


def test_bot(db) -> str:
    from bot import keyboards as kb
    from bot.handlers import BotApp
    from services import target_tonnage_question as tq
    from services import voice_assistant as va

    db.set_setting(va.SETTING_ENABLED, "0")  # assistant off: the question must still work
    client = FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]

    def send(uid: str, text: str):
        n = len(client.sent)
        app.handle_message({"from": {"id": int(uid), "first_name": "x"}, "chat": {"id": int(uid)}, "text": text})
        return [t for _c, t, _m in client.sent[n:]], _btns(client.sent[-1][2])

    texts, btns = send(MANAGER, QUESTION)
    body = "\n".join(texts)
    assert "مواد لازم برای تولید ۱۰ تن شمش بیلت" in body, body
    assert "۱۰٬۰۰۰ کیلوگرم" in body, body
    assert "بتن بیلت" in body and "نازل" in body, body
    assert "شرود" not in body and "روکش بلوم" not in body and "پودر قالب" not in body and "الیاف" not in body, body
    assert "نیاز مواد برای N تاندیش" in body and "تاندیش لازم" in body, body
    assert "│" in body, "answer must be a table"
    assert kb.BTN_OUT_PDF in btns and kb.BTN_OUT_XLSX in btns, btns
    texts_kg, btns_kg = send(MANAGER, "برای ده هزار کیلو شمش بیلت چقدر مواد لازم است")
    kg_body = "\n".join(texts_kg)
    assert "مواد لازم برای تولید ۱۰ تن شمش بیلت (۱۰٬۰۰۰ کیلوگرم)" in kg_body, kg_body
    assert "بتن بیلت" in kg_body and "نازل" in kg_body, kg_body
    assert "شرود" not in kg_body, kg_body
    assert kb.BTN_OUT_PDF in btns_kg and kb.BTN_OUT_XLSX in btns_kg, btns_kg
    nd = len(client.docs)
    texts, _ = send(MANAGER, kb.BTN_OUT_PDF)
    assert len(client.docs) == nd + 1 and client.docs[-1][1].endswith(".pdf"), client.docs[-1:]
    pdf = Path(client.docs[-1][1])
    assert pdf.stat().st_size > 2000 and "مواد_لازم" in pdf.name, pdf
    # 10,000 t ÷ 100 t/tundish = 100 tundishes; بتن بیلت patching 2 → 200 kg, stock 120 → کسری 80
    texts, _ = send(MANAGER, "۱۰ هزار تن شمش بیلت")
    big = "\n".join(texts)
    assert "۲ قلم کسری دارد" in big and "۸۰" in big and "۲۰۰" in big, big
    send(MANAGER, kb.BTN_OUT_PDF)
    print("  bot: question → table + «خروجی PDF / اکسل» (assistant off) OK")

    # missing tonnage → short question + buttons, then a plain number
    texts, btns = send(MANAGER, "برای تولید شمش بیلت چقدر مواد لازم است")
    assert "چند تن شمش بیلت" in texts[-1] and "۱۰ تن شمش بیلت" in btns, (texts, btns)
    assert "دکمه‌های منو" not in texts[-1]
    texts, btns = send(MANAGER, "ده")
    assert any("مواد لازم برای تولید ۱۰ تن شمش بیلت" in t for t in texts), texts
    send(MANAGER, kb.BTN_OUT_XLSX)
    # missing product → section buttons (complete questions)
    texts, btns = send(MANAGER, "برای ده تن تولید شمش چقدر مواد لازم است")
    assert "کدام شمش" in texts[-1] and "۱۰ تن شمش اسلب" in btns, (texts, btns)
    texts, btns = send(MANAGER, "برای ده هزار کیلو تولید شمش چقدر مواد لازم است")
    assert "کدام شمش" in texts[-1] and "۱۰٬۰۰۰ کیلوگرم" in texts[-1] and "۱۰ تن شمش بیلت" in btns, (texts, btns)
    assert "کیلوگرم" in tq.clarify_text(tq.parse("برای تولید شمش بیلت چقدر مواد لازم است")), tq.clarify_text(tq.parse("برای تولید شمش بیلت چقدر مواد لازم است"))
    texts, btns = send(MANAGER, "۱۰ تن شمش اسلب")
    body2 = "\n".join(texts)
    assert "۱۰ تن شمش اسلب" in body2 and "شرود" in body2 and "بتن بیلت" not in body2, body2
    send(MANAGER, kb.BTN_HOME)
    # pending + another menu button → leaves the question
    send(MANAGER, "برای ده تن تولید شمش چقدر مواد لازم است")
    texts, btns = send(MANAGER, kb.BTN_HOME)
    assert MANAGER not in app._tt_pending
    print("  bot: clarifying question with buttons (tonnage / product) OK")

    # permission: technician has no «هدف اصلی»
    texts, _ = send(TECH, QUESTION)
    assert "دسترسی ندارید" in texts[-1] and "بتن" not in texts[-1], texts
    print("  bot: permission denial OK")
    return body


def test_assistant_and_web(db, bot_body: str) -> None:
    from fastapi.testclient import TestClient

    import web.deps as deps
    from services import assistant_brain as ab
    from services import voice_assistant as va
    from web.app import create_app

    # assistant path (bot «از دستیار بپرس» / voice): same answer text
    ans = ab.answer(db, db.get_user(MANAGER), QUESTION, use_llm=False, log=False)
    assert ans.intent.name == ab.I_TARGET_TONNAGE and ans.ok, ans.text
    assert ans.text.split("\n\nجدول کامل")[0].strip() == bot_body.split("\n✅")[0].strip(), (ans.text, bot_body)
    assert ans.table and ans.table["sections"][0]["rows"], ans.table
    deny = ab.answer(db, db.get_user(TECH), QUESTION, use_llm=False, log=False)
    assert not deny.ok and "دسترسی" in deny.text
    print("  assistant intent: same answer + permission OK")

    deps._db = db
    app = create_app()
    app.dependency_overrides[deps.current_user_optional] = lambda: db.get_user(app.state._uid)
    app.state._uid = MANAGER
    c = TestClient(app)
    db.set_setting(va.SETTING_ENABLED, "0")
    r = c.post("/assistant/ask", data={"q": QUESTION})
    assert r.status_code == 200, r.text[:800]
    assert "بتن بیلت" in r.text and "نازل" in r.text and "روکش بلوم" not in r.text, r.text[:3000]
    assert "/assistant/file/" in r.text  # PDF / Excel download of the table
    r = c.post("/assistant/ask", data={"q": "موجودی شرود"})
    assert r.status_code == 400 and "غیرفعال" in r.text  # other questions still need the assistant
    r = c.post("/assistant/ask", data={"q": "برای ده تن تولید شمش چقدر مواد لازم است"})
    assert r.status_code == 200 and "۱۰ تن شمش بیلت" in r.text, r.text[:2000]
    app.state._uid = TECH
    r = c.post("/assistant/ask", data={"q": QUESTION})
    assert "دسترسی ندارید" in r.text and "بتن بیلت" not in r.text
    app.state._uid = MANAGER
    print("  web assistant (assistant off): answer / clarify / permission OK")

    from bot import keyboards as kb

    home = c.get("/home").text
    reports = c.get("/reports").text
    assert "پیش بینی مواد بر حسب تناژ تولید" in reports and 'href="/reports/main-goal"' in reports, reports[:2500]
    assert "🎯 هدف اصلی" not in home and "پیش بینی مواد بر حسب تناژ تولید" not in home
    assert kb.BTN_MAIN_GOAL not in kb.keyboard_texts(kb.main_menu(db.get_user(MANAGER)))
    assert kb.BTN_MAIN_GOAL in kb.keyboard_texts(kb.analytics_menu(db.get_user(MANAGER)))
    assert kb.canonical("🎯 هدف اصلی") == kb.BTN_MAIN_GOAL
    print("  menu moved into گزارش‌ها (old label still opens it) OK")

    # web «سناریو ۱ — تناژ هدف» = bot: same billet-only material rows
    import json

    r = c.post("/reports/main-goal/scenario/target", data={"period_text": "", "billet": "10"})
    assert r.status_code == 200, r.text[:500]
    rows = db.list_main_goal_reports(limit=50)
    web_row = next((x for x in rows if x.get("source") == "web"), None)
    bot_row = next((x for x in rows if x.get("source") == "bot_text" and float(x.get("target_tons") or 0) == 10
                    and "بیلت" in (x.get("summary_text") or "")), None)
    assert web_row and bot_row, [x.get("source") for x in rows]

    def need_rows(row):
        secs = json.loads(db.get_main_goal_report(int(row["id"]))["results_json"])["sections"]
        return sorted((r["بخش"], r["ماده"], r["نیاز_پیشنهادی"]) for r in secs[1]["rows"])

    assert need_rows(web_row) == need_rows(bot_row), (need_rows(web_row), need_rows(bot_row))
    assert {m for _s, m, _n in need_rows(web_row)} == {"بتن بیلت", "نازل", "الیاف"}
    print("  web scenario = bot (billet-only rows identical) OK")


def test_rate_reports_follow_usage_location() -> None:
    """N tundish + critical items: a section does not receive slab-only casting-floor items.

    شرود «سطح ریخته گری اسلب» (and the ZWNJ form) is slab only. «سطح ریخته گری بیلت»
    is billet only. «بلوم / بیلت» counts for both. A blank «محل استفاده» still applies
    to every section (the previous casting-floor rule).
    """
    import pandas as pd

    from analytics.critical_items import (
        COL_MONTHLY,
        TundishMonthCounts,
        build_critical_items_rows,
        casting_count_from_parts,
    )
    from analytics.section_rules import casting_floor_sections
    from services import n_tundish_report as nt

    zero = {
        "work_order": "", "casting_floor": 0, "billet_renovation": 0, "billet_patching": 0,
        "bloom_renovation": 0, "bloom_patching": 0, "slab_renovation": 0, "slab_patching": 0,
        "rate_group": "", "shared": "خیر", "critical_point": "", "origin": "داخلی", "priority": 1,
        "contractor_or_company": "شرکت", "unit": "NO", "quantity": 0,
    }
    rows = [
        dict(zero, category_code="1581", id="378481807741J", product_name="LADLE SHROUD",
             keyword="شرود", usage_location="سطح ریخته گری اسلب", casting_floor=1),
        dict(zero, category_code="1582", id="378481807742K", product_name="LADLE SHROUD ZWNJ",
             keyword="شرود نیم‌فاصله", usage_location="سطح ریخته‌گری اسلب", casting_floor=2),
        dict(zero, category_code="1451", id="378166661451D", product_name="بتن ۸۰ گان",
             keyword="بتن گان", usage_location="سطح ریخته گری بیلت", casting_floor=4),
        dict(zero, category_code="1712", id="378112341712N", product_name="نازل مشترک",
             keyword="نازل سطح", usage_location="بلوم / بیلت", casting_floor=1),
        dict(zero, category_code="1699", id="378112341699Z", product_name="کف بدون بخش",
             keyword="کف", usage_location="", casting_floor=3),
        dict(zero, category_code="1203", id="378112341203A", product_name="بتن بیلت",
             keyword="بتن بیلت", usage_location="بیلت", unit="Kg", billet_patching=2),
        # two codes, casting floor summed (not a merged casting-floor cell): slab + billet
        dict(zero, category_code="1710", id="378112341710A", product_name="نازل اسلب گروه",
             keyword="نازل گروه اسلب", usage_location="سطح ریخته گری اسلب", casting_floor=1,
             rate_group="G1:billet_patching"),
        dict(zero, category_code="1714", id="378112341714B", product_name="نازل بیلت گروه",
             keyword="نازل گروه بیلت", usage_location="سطح ریخته گری بیلت", casting_floor=1,
             rate_group="G1:billet_patching"),
    ]
    inv = pd.DataFrame(rows)
    secs = {row.keyword: casting_floor_sections(inv.loc[[i]]) for i, row in inv.iterrows()}
    assert secs["شرود"] == ("slab",), secs
    assert secs["شرود نیم‌فاصله"] == ("slab",), secs
    assert secs["بتن گان"] == ("billet",), secs
    assert secs["نازل سطح"] == ("billet", "bloom"), secs
    assert secs["کف"] is None, secs  # blank محل استفاده → every section
    assert secs["بتن بیلت"] == (), secs  # no casting-floor rate

    def codes_of(res) -> dict[str, float]:
        assert res.error is None, res.error
        return {str(r[nt.COL_CODE]): float(r[nt.COL_NEED]) for r in res.rows if r.get(nt.COL_ROW) not in ("", None)}

    billet = codes_of(nt.build_rows(inv, "billet", 10, "with", report_date="1405/08/19"))
    assert "1581" not in billet and "1582" not in billet, billet
    assert billet["1451"] == 40 and billet["1712"] == 10 and billet["1699"] == 30 and billet["1203"] == 20, billet
    # shared group: each member's own casting floor (1×10 billet + nothing from the slab member)
    assert billet["1710/1714"] == 10, billet
    wo = codes_of(nt.build_rows(inv, "billet", 10, "without"))
    assert "1581" not in wo and "1582" not in wo and wo["1451"] == 40, wo

    slab = codes_of(nt.build_rows(inv, "slab", 10, "with"))
    assert slab["1581"] == 10 and slab["1582"] == 20 and slab["1699"] == 30, slab
    assert "1451" not in slab and "1712" not in slab and "1203" not in slab, slab
    assert slab["1710/1714"] == 10, slab  # only the slab member

    bloom = codes_of(nt.build_rows(inv, "bloom", 10, "with"))
    assert bloom["1712"] == 10 and bloom["1699"] == 30, bloom
    assert "1581" not in bloom and "1582" not in bloom and "1451" not in bloom and "1203" not in bloom, bloom

    mix = nt.build_mix_rows(inv, {"billet": 10, "bloom": 4, "slab": 20}, "with")
    assert mix.error is None, mix.error
    by = {str(r[nt.COL_CODE]): r for r in mix.rows if r.get(nt.COL_ROW) not in ("", None)}
    assert float(by["1581"][mix.need_col]) == 20, by["1581"]  # 1 × slab, not × 34
    assert float(by["1582"][mix.need_col]) == 40, by["1582"]
    assert float(by["1451"][mix.need_col]) == 40, by["1451"]  # 4 × billet
    assert float(by["1712"][mix.need_col]) == 14, by["1712"]  # 1 × (10+4)
    assert float(by["1699"][mix.need_col]) == 3 * 34, by["1699"]  # blank location × all
    assert float(by["1203"][mix.need_col]) == 20, by["1203"]
    assert float(by["1710/1714"][mix.need_col]) == 1 * 20 + 1 * 10, by["1710/1714"]
    assert "شرود" in by["1581"][nt.COL_KEYWORD]

    counts = TundishMonthCounts(1405, 8, count_billet=10, count_bloom=4, count_slab=20)
    df = build_critical_items_rows(inv, counts, segment="company", include_covered=True)
    got = {str(r["کد چهاررقمی"]): float(r[COL_MONTHLY]) for _, r in df.iterrows() if r["row_kind"] != "member"}
    assert got["1581"] == 20 and got["1582"] == 40 and got["1451"] == 40, got
    assert got["1712"] == 14 and got["1699"] == 102 and got["1203"] == 20, got
    assert got["1710/1714"] == 30, got

    only_billet = TundishMonthCounts(1405, 8, count_billet=10, count_bloom=0, count_slab=0)
    df = build_critical_items_rows(inv, only_billet, segment="company", include_covered=True)
    names = set(df["کد چهاررقمی"].astype(str))
    assert "1581" not in names and "1582" not in names, names
    monthly = {str(r["کد چهاررقمی"]): float(r[COL_MONTHLY]) for _, r in df.iterrows() if r["row_kind"] != "member"}
    assert monthly["1451"] == 40 and monthly["1712"] == 10 and monthly["1699"] == 30 and monthly["1203"] == 20, monthly
    assert monthly["1710/1714"] == 10, monthly

    # the summed-rate adjustment: 1×20 + 1×10 with a combined rate of 2 → count 15
    cnt = TundishMonthCounts(0, 1, 10, 0, 20)
    assert casting_count_from_parts([(1.0, ("slab",)), (1.0, ("billet",))], False, cnt) == 15
    assert casting_count_from_parts([(1.0, ("slab",))], False, cnt) == 20
    assert casting_count_from_parts([(3.0, None)], False, cnt) == 30
    print("  N tundish + critical items: billet gets no slab-only casting floor OK")


def test_empty_consumption_lists_tundish_rates() -> None:
    """No stored consumption quantity must still list what one tundish uses."""
    from services.main_goal_history import _sequence_placeholder
    from services.target_tonnage_question import COL_ITEM, _rate_card_rows

    assert _sequence_placeholder("تعویض شرود", "billet")
    assert _sequence_placeholder("تعویض شرود", "bloom")
    assert not _sequence_placeholder("تعویض شرود", "slab")
    assert not _sequence_placeholder("تعویض نازل بیرونی", "billet")
    rows = _rate_card_rows(_inventory(), "billet", 1)
    names = " ".join(str(r[COL_ITEM]) for r in rows)
    assert rows and "بتن بیلت" in names and "نازل" in names, names
    assert "شرود" not in names, names
    print("  empty consumption lists tundish rates; shroud is not a billet item OK")


def main() -> None:
    import tempfile

    from db.models import Database

    tmp = Path(tempfile.mkdtemp(prefix="smoke_v172_"))
    db = Database(tmp / "v172.db")
    _seed(db)
    test_service(db)
    test_rate_reports_follow_usage_location()
    test_empty_consumption_lists_tundish_rates()
    test_parse()
    body = test_bot(db)
    print("\n--- نمونهٔ پاسخ ربات ---\n" + body + "\n---")
    # kg phrasing yields the same table; reprint a dedicated sample for the report
    from bot.handlers import BotApp
    from services import voice_assistant as va

    db.set_setting(va.SETTING_ENABLED, "0")
    client = FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]
    app.handle_message({"from": {"id": int(MANAGER), "first_name": "x"},
                        "chat": {"id": int(MANAGER)},
                        "text": "برای ده هزار کیلو شمش بیلت چقدر مواد لازم است"})
    kg_sample = "\n".join(t for _c, t, _m in client.sent)
    print("\n--- نمونهٔ پاسخ ربات برای «برای ده هزار کیلو شمش بیلت چقدر مواد لازم است» ---\n" + kg_sample + "\n---")
    test_assistant_and_web(db, body)
    print("SMOKE OK")


if __name__ == "__main__":
    main()
