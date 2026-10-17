#!/usr/bin/env python3
"""Smoke: دستیار هوشمند (services.assistant_brain).

بدون آرگومان: مسیر قواعد (دقت تشخیص نیت روی ~۴۵ عبارت فارسی)، نرمال‌سازی، بازگشت
مدل زبانی با پاسخ شبیه‌سازی‌شده (JSON)، Ollama خاموش، و کنترل اعداد بازنویسی.

با ``--db PATH``: همان پایگاه‌داده را با sqlite backup در پوشهٔ موقت کپی می‌کند (هرگز
روی فایل اصلی نمی‌نویسد) و پاسخ‌ها را با فراخوانی مستقیم همان سرویس‌ها مقایسه می‌کند
(اعداد پاسخ = اعداد سرویس). ``--ollama-model qwen2.5:1.5b`` بازگشت واقعی مدل را روی
Ollama محلی هم می‌سنجد. ``--show`` نمونهٔ پرسش→پاسخ چاپ می‌کند.
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402  — temp DB/reports/uploads before config import

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ap = argparse.ArgumentParser()
ap.add_argument("--db", help="copy of this DB is used (never written)")
ap.add_argument("--ollama-model", default="", help="also test real Ollama fallback with this model")
ap.add_argument("--show", action="store_true")
ARGS = ap.parse_args()

if ARGS.db:
    src = Path(ARGS.db).resolve()
    dst = _smoke_env.SMOKE_TMP / "copy.db"
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as s, sqlite3.connect(dst) as d:
        s.backup(d)
    os.environ["DATABASE_PATH"] = str(dst)

from services import assistant_brain as ab  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILS.append(msg)
        print("  ✗", msg)


# (phrase, intent, expected slots subset)
CASES: list[tuple[str, str, dict]] = [
    # stock
    ("از آجر ۱۶۰۳ چقدر داریم", ab.I_STOCK, {"code": "1603"}),
    ("موجودی ۱۶۷۲ چنده؟", ab.I_STOCK, {"code": "1672"}),
    ("موجودی استوپر", ab.I_STOCK, {"query": "استوپر"}),
    ("از نازل بیرونی چندتا مونده؟", ab.I_STOCK, {"query": "نازل بیرونی"}),
    ("378121641302R موجودی", ab.I_STOCK, {"item_id": "378121641302R"}),
    ("بتن میکس داریم؟", ab.I_STOCK, {"query": "بتن میکس"}),
    ("کد ١٦٥٦ چقدر هست", ab.I_STOCK, {"code": "1656"}),
    ("موجودي روكش اسلب", ab.I_STOCK, {"query": "روکش اسلب"}),
    ("چقدر نازل 18 تو انبار هست", ab.I_STOCK, {"query": "نازل 18"}),
    # critical
    ("چی کم داریم", ab.I_CRITICAL, {}),
    ("اقلام بحرانی کدوما هستن", ab.I_CRITICAL, {}),
    ("چه چیزهایی رو به اتمامه؟", ab.I_CRITICAL, {}),
    ("کمبود‌ها بدون نوسازی", ab.I_CRITICAL, {"reno": "without"}),
    ("چیا باید سفارش بدیم", ab.I_CRITICAL, {}),
    # n tundish
    ("برای ۱۰ تاندیش چی لازمه", ab.I_N_TUNDISH, {"n": 10}),
    ("برای ۱۰ تاندیش اسلب چی لازمه؟", ab.I_N_TUNDISH, {"n": 10, "section": "slab"}),
    ("نیاز مواد ۲۰ تاندیش بیلت", ab.I_N_TUNDISH, {"n": 20, "section": "billet"}),
    ("اسلب ۴", ab.I_N_TUNDISH, {"n": 4, "section": "slab"}),
    ("پنج تا تاندیش بلوم چقدر مواد می‌خواد", ab.I_N_TUNDISH, {"n": 5, "section": "bloom"}),
    # coverage
    ("شرود چند روز کافیه", ab.I_COVERAGE, {"query": "شرود"}),
    ("حد تحمل ۱۶۷۲", ab.I_COVERAGE, {"code": "1672"}),
    ("نازل بیرونی تا کی جواب میده", ab.I_COVERAGE, {"query": "نازل بیرونی"}),
    ("پوشش بتن ۸۶ نوسازی چند روزه", ab.I_COVERAGE, {"query": "بتن 86 نوسازی"}),
    # consumption
    ("مصرف ۳۰ روز اخیر", ab.I_CONSUMPTION, {}),
    ("مصرف شهریور", ab.I_CONSUMPTION, {}),
    ("ماه گذشته چقدر مصرف شد", ab.I_CONSUMPTION, {}),
    ("مصرف ۳ ماه اخیر اسلب", ab.I_CONSUMPTION, {"section": "slab"}),
    ("مصرف شرود در شهریور", ab.I_CONSUMPTION, {"query": "شرود"}),
    # last upload
    ("آخرین بار موجودی کی آپلود شد", ab.I_LAST_UPLOAD, {}),
    ("موجودی انبار کی به‌روز شده؟", ab.I_LAST_UPLOAD, {}),
    ("تاریخ آخرین بارگذاری فایل", ab.I_LAST_UPLOAD, {}),
    ("فایل انبار رو آخرین بار کی گذاشتن", ab.I_LAST_UPLOAD, {}),
    # pending approvals
    ("درخواست‌های در انتظار تأیید", ab.I_PENDING, {}),
    ("چیزی منتظر تایید من هست؟", ab.I_PENDING, {}),
    # tundish stats
    ("شهریور چند تا تاندیش اسلب زدیم", ab.I_TUNDISH_STATS, {"section": "slab"}),
    ("آمار توالی تاندیش مرداد", ab.I_TUNDISH_STATS, {}),
    ("تعداد ذوب ماه گذشته", ab.I_TUNDISH_STATS, {}),
    ("۳ ماه اخیر چند تاندیش بیلت داشتیم", ab.I_TUNDISH_STATS, {"section": "billet"}),
    # open report
    ("گزارش مازاد را بفرست", ab.I_OPEN_REPORT, {"report": "BTN_SURPLUS"}),
    ("گزارش جامع", ab.I_OPEN_REPORT, {"report": "BTN_COMPREHENSIVE"}),
    ("منوی هدف اصلی رو باز کن", ab.I_OPEN_REPORT, {"report": "BTN_MAIN_GOAL"}),
    ("pdf اقلام ورودی می‌خوام", ab.I_OPEN_REPORT, {"report": "BTN_INBOUND"}),
    # help
    ("چه کارهایی می‌تونی بکنی؟", ab.I_HELP, {}),
    ("راهنما", ab.I_HELP, {}),
    ("سلام", ab.I_HELP, {}),
    ("دستیار چی بلده", ab.I_HELP, {}),
    # 1405/07/25 — the owner's real question (typo «داربم») + colloquial stock/coverage phrasings
    ("چندتا شرود داربم؟", ab.I_STOCK, {"query": "شرود"}),
    ("چندتا شرود داریم؟", ab.I_STOCK, {"query": "شرود"}),
    ("چند تا شرود داریم", ab.I_STOCK, {"query": "شرود"}),
    ("شرود چندتا داریم", ab.I_STOCK, {"query": "شرود"}),
    ("شرود چند عدد داریم", ab.I_STOCK, {"query": "شرود"}),
    ("چند عدد شرود موجوده", ab.I_STOCK, {"query": "شرود"}),
    ("شرود داریم؟", ab.I_STOCK, {"query": "شرود"}),
    ("شرود هست؟", ab.I_STOCK, {"query": "شرود"}),
    ("شرود چقد مونده", ab.I_STOCK, {"query": "شرود"}),
    ("چه قدر شرود باقی مونده", ab.I_STOCK, {"query": "شرود"}),
    ("چه مقدار شراود داریم", ab.I_STOCK, {"query": "شرود"}),
    ("shroud موجودی", ab.I_STOCK, {"query": "شرود"}),
    ("شرود ته کشیده؟", ab.I_STOCK, {"query": "شرود"}),
    ("استاپر چند تا داریم", ab.I_STOCK, {"query": "استوپر"}),
    ("از استوپر چی مونده", ab.I_STOCK, {"query": "استوپر"}),
    ("استوپر موجوده؟", ab.I_STOCK, {"query": "استوپر"}),
    ("چند تا استوپر تو انبار هست", ab.I_STOCK, {"query": "استوپر"}),
    ("نازل بیرونی چندتا مونده", ab.I_STOCK, {"query": "نازل بیرونی"}),
    ("از نازل بیرونی چقد داریم", ab.I_STOCK, {"query": "نازل بیرونی"}),
    ("آجر کف چندتا داریم", ab.I_STOCK, {"query": "اجر کف"}),
    ("آجر ۱۶۰۳ چند تا مونده", ab.I_STOCK, {"code": "1603"}),
    ("بتن پلی ۶۵ چه مقدار داریم", ab.I_STOCK, {"query": "بتن پلی 65"}),
    ("کد ۱۶۷۲ چندتا داریم", ab.I_STOCK, {"code": "1672"}),
    ("۱۵۸۱ موجودی", ab.I_STOCK, {"code": "1581"}),
    ("1603 چقد هست", ab.I_STOCK, {"code": "1603"}),
    ("شرود چند روز دیگه جواب میده", ab.I_COVERAGE, {"query": "شرود"}),
    ("استوپر تا کی کافیه", ab.I_COVERAGE, {"query": "استوپر"}),
    ("نازل بیرونی چند روز دوام داره", ab.I_COVERAGE, {"query": "نازل بیرونی"}),
    ("حد تحمل آجر ۱۶۰۳", ab.I_COVERAGE, {"code": "1603"}),
    ("پوشش شرود چند روزه", ab.I_COVERAGE, {"query": "شرود"}),
    ("چیا ته کشیده", ab.I_CRITICAL, {}),
    ("چی تموم شده", ab.I_CRITICAL, {}),
]

# held-out stock/coverage phrasings (written 1405/07/25 after the fix, never tuned on)
HELDOUT_STOCK: list[tuple[str, str, str]] = [  # (phrase, intent, item word that must be in the slots)
    ("شرودا چندتان", ab.I_STOCK, "شرود"), ("هنوز شرود تو انبار داریم؟", ab.I_STOCK, "شرود"),
    ("موجودی شراود رو بگو", ab.I_STOCK, "شرود"), ("استاپرا چقدر مونده", ab.I_STOCK, "استوپر"),
    ("یه دونه نازل ثابت هم داریم؟", ab.I_STOCK, "نازل ثابت"), ("بتن پلی ۶۰ چند کیسه هست", ab.I_STOCK, "بتن پلی 60"),
    ("آجر کف موجوده یا تموم شده", ab.I_STOCK, "اجر کف"), ("کد ۱۶۵۵ چه خبر", ab.I_STOCK, "1655"),
    ("روکش اسلب تا چند روز کافیه", ab.I_COVERAGE, "روکش"), ("استوپر چه مدت دیگه بسه", ab.I_COVERAGE, "استوپر"),
    ("از شرود چی داریم الان", ab.I_STOCK, "شرود"), ("نازل بیرونی موجودیش چنده", ab.I_STOCK, "نازل بیرونی"),
]

# held-out phrasings (written after the rules, never tuned on): rules + LLM fallback
HELDOUT: list[tuple[str, str]] = [
    ("چند تا شرود اسلب داریم", ab.I_STOCK), ("سنگ دیواره بلوم موجودیش چقدره", ab.I_STOCK),
    ("1745 چی داریم", ab.I_STOCK), ("بتن پلی ۶۵ چقدر مونده", ab.I_STOCK),
    ("وضعیت انبار توربو استاپ بیلت", ab.I_STOCK), ("کدوم اقلام زیر نقطه سفارشن", ab.I_CRITICAL),
    ("لیست کسری ها رو بده", ab.I_CRITICAL), ("چیزی داره تموم میشه؟", ab.I_CRITICAL),
    ("واسه ۱۵ تاندیش بلوم چه موادی لازم داریم", ab.I_N_TUNDISH), ("نیاز ۸ تاندیش", ab.I_N_TUNDISH),
    ("بیلت ۱۲", ab.I_N_TUNDISH), ("استوپر چند روز دیگه تموم میشه", ab.I_COVERAGE),
    ("روکش بلوم چند روز جواب میده", ab.I_COVERAGE), ("مصرف هفته گذشته", ab.I_CONSUMPTION),
    ("مصرف امسال بلوم", ab.I_CONSUMPTION), ("مصرف نازل بیرونی تو مرداد چقدر بود", ab.I_CONSUMPTION),
    ("موجودی رو آخرین بار کی آپدیت کردن", ab.I_LAST_UPLOAD), ("آخرین آپلود فایل موجودی", ab.I_LAST_UPLOAD),
    ("درخواستی برای تایید دارم؟", ab.I_PENDING), ("کارتابل تایید", ab.I_PENDING),
    ("مرداد چند تا تاندیش بیلت مصرف شد", ab.I_TUNDISH_STATS), ("آمار ریخته گری شهریور", ab.I_TUNDISH_STATS),
    ("گزارش خلاصه مصرف ماهیانه بده", ab.I_OPEN_REPORT), ("میخوام موجودی سایت رو ثبت کنم", ab.I_OPEN_REPORT),
    ("کمکم کن", ab.I_HELP), ("چه سوالایی میتونم بپرسم", ab.I_HELP),
]


def run_heldout_stock(codes, inv, model: str = "") -> None:
    print("== held-out stock/coverage phrasings ==")
    ok = 0
    for q, want, item in HELDOUT_STOCK:
        it = ab.route(q, use_llm=bool(model), model=model or None, known_codes=codes, inventory=inv)
        vals = " ".join(str(v) for v in it.slots.values())
        good = it.name == want and item in ab.normalize(vals)
        ok += good
        if not good:
            print(f"  miss «{q}» → {it.name} {it.slots} ({it.source})")
    print(f"  held-out stock/coverage: {ok}/{len(HELDOUT_STOCK)}" + (" (rules + LLM)" if model else " (rules)"))
    check(ok >= len(HELDOUT_STOCK) - 2, "held-out stock/coverage accuracy too low")


def run_heldout(codes, inv, model: str = "") -> None:
    print("== held-out phrasings ==")
    ok_rules = ok_all = 0
    for q, want in HELDOUT:
        it = ab.route(q, use_llm=False, known_codes=codes, inventory=inv)
        ok_rules += it.name == want
        if it.name == ab.I_UNKNOWN and model:
            it = ab.route(q, model=model, known_codes=codes, inventory=inv)
            print(f"  LLM fallback «{q}» → {it.name}")
        ok_all += it.name == want
        if it.name != want:
            print(f"  miss «{q}» → {it.name}")
    print(f"  rules only: {ok_rules}/{len(HELDOUT)}" + (f" | rules + LLM fallback: {ok_all}/{len(HELDOUT)}" if model else ""))
    check(ok_rules >= len(HELDOUT) - 4, "held-out rules accuracy too low")


# questions the rules deliberately miss → LLM fallback (mocked / real)
LLM_CASES: list[tuple[str, str, dict]] = [
    ("استاک بتن ملات رو بگو ببینم", ab.I_STOCK, {}),  # rules: «استاک» → stock already; kept as control
    ("یه لیست از چیزای در خطر بده", ab.I_CRITICAL, {}),
    ("دیتای جدید کی اومد", ab.I_LAST_UPLOAD, {}),
    ("کسی منتظر جواب منه", ab.I_PENDING, {}),
]


def run_rules(codes: set[str] | None) -> tuple[int, int]:
    print("== rules routing ==")
    ok = 0
    for q, want, slots in CASES:
        it = ab.route(q, use_llm=False, known_codes=codes)
        good = it.name == want and all(it.slots.get(k) == v for k, v in slots.items())
        ok += good
        if not good:
            check(False, f"route «{q}» → {it.name} {it.slots} (want {want} {slots})")
    print(f"  intent+slots accuracy: {ok}/{len(CASES)}")
    return ok, len(CASES)


def run_units() -> None:
    print("== normalize / numbers ==")
    check(ab.normalize("موجودي كد ۱۶۰۳\u200cها") == "موجودی کد 1603 ها", "normalize ی/ک/digits/ZWNJ")
    check(ab.normalize("٠١٢٣٤٥٦٧٨٩") == "0123456789", "arabic-indic digits")
    check(ab.numbers_match("موجودی 1,200 عدد و 3.5 روز", "۱۲۰۰ عدد و 3.5 روز موجود است"), "numbers ok")
    check(not ab.numbers_match("موجودی 1,200 عدد", "حدود 1300 عدد"), "changed number rejected")
    check(not ab.numbers_match("موجودی 1,200 عدد", "1200 عدد برای 3 روز"), "new number rejected")
    p = ab.parse_period("ماه گذشته")
    check(bool(p) and len(p["months"]) == 1, "period ماه گذشته")
    p = ab.parse_period("3 ماه اخیر")
    check(bool(p) and len(p["months"]) == 3, "period 3 ماه")


def run_llm_mock(codes: set[str] | None) -> None:
    print("== LLM fallback (mocked) ==")
    replies = {
        "یه لیست از چیزای در خطر بده": '{"intent":"critical","code":"","query":"","n":0,"section":"","period":"","report":""}',
        "دیتای جدید کی اومد": '{"intent":"last_upload","code":"","query":"","n":0,"section":"","period":"","report":""}',
        "کسی منتظر جواب منه": '```json {"intent":"pending_approvals"} ```',
    }

    def chat(msgs, **kw):
        assert kw.get("json_mode") is True
        return replies.get(msgs[-1]["content"], "")

    for q, want, _ in LLM_CASES[1:]:
        check(ab.rule_route(q, known_codes=codes) is None, f"rules should miss «{q}»")
        it = ab.route(q, model="mock", chat=chat, known_codes=codes)
        check(it.name == want and it.source == "llm", f"mock LLM «{q}» → {it.name}/{it.source}")
    # hallucinated slots are dropped (n not in text / code not in text / bad intent)
    bad = ab.llm_route("واسه چند تا تاندیش چی بخریم", model="m",
                       chat=lambda m, **k: '{"intent":"n_tundish","n":12}')
    check(bad is None, "LLM n not grounded in text → rejected")
    bad = ab.llm_route("یه چیزی بگو", model="m", chat=lambda m, **k: '{"intent":"launch_rockets"}')
    check(bad is None, "unknown LLM intent rejected")
    g = ab.llm_route("از اون آجره چقدر هست", model="m",
                     chat=lambda m, **k: '{"intent":"stock","code":"1603","query":"آجر"}')
    check(g is not None and "code" not in g.slots and g.slots.get("query") == "اجر", "LLM code not in text dropped")
    # guard: a named item never becomes critical/pending from the LLM (unless critical words are there)
    crit = lambda m, **k: '{"intent":"critical"}'  # noqa: E731
    g = ab.guard_llm_intent("وضعیت ذخیره شرود رو بگو", ab.llm_route("وضعیت ذخیره شرود رو بگو", model="m", chat=crit),
                            "شرود")
    check(g.name == ab.I_COVERAGE and g.slots.get("query") == "شرود", f"guard: item + LLM critical → coverage ({g})")
    g = ab.guard_llm_intent("شرود رو چک کن", ab.Intent(ab.I_PENDING, source="llm"), "شرود")
    check(g.name == ab.I_STOCK and g.slots.get("query") == "شرود", f"guard: item + LLM pending → stock ({g})")
    g = ab.guard_llm_intent("کدوم شرودها بحرانی شدن", ab.Intent(ab.I_CRITICAL, source="llm"), "شرود")
    check(g.name == ab.I_CRITICAL, "guard keeps critical when «بحرانی» is in the text")
    g = ab.guard_llm_intent("ذخیره شرود", ab.Intent(ab.I_COVERAGE, {}, source="llm"), "شرود")
    check(g.slots.get("query") == "شرود", "guard fills the item the model dropped")
    check(ab.item_mention("استاپرا چطوره") == "استوپر" and ab.item_mention("shroud") == "شرود", "item aliases")
    check(ab.understood_fa(ab.Intent(ab.I_STOCK, {"query": "شرود"})) == "برداشت: موجودی شرود", "understood footer")
    for c in ab.intent_choices("شرود", approver=True):
        check(ab.rule_route(c) is not None, f"misfire choice «{c}» routes by rules")
    # Ollama down → rules still answer, unknown → None
    import services.assistant_brain as m

    old = os.environ.get("OLLAMA_URL")
    os.environ["OLLAMA_URL"] = "http://127.0.0.1:9"  # closed port
    try:
        t0 = time.time()
        it = ab.route("چی کم داریم", model="qwen2.5:1.5b")
        check(it.name == ab.I_CRITICAL, "rules work with Ollama down")
        it = ab.route("یه لیست از چیزای در خطر بده", model="qwen2.5:1.5b")
        check(it.name == ab.I_UNKNOWN and time.time() - t0 < 10, "Ollama down → unknown quickly")
        check(m.rephrase("موجودی 5", model="x") is None, "rephrase with Ollama down → template kept")
    finally:
        if old is None:
            os.environ.pop("OLLAMA_URL", None)
        else:
            os.environ["OLLAMA_URL"] = old
    # verified rephrase
    good = ab.rephrase("موجودی 1,200 عدد", model="m", chat=lambda m, **k: "الان ۱۲۰۰ عدد داریم.")
    check(good == "الان ۱۲۰۰ عدد داریم.", "rephrase kept when numbers match")
    check(ab.rephrase("موجودی 1,200 عدد", model="m", chat=lambda m, **k: "حدود 1250 عدد") is None,
          "rephrase rejected when a number changes")
    check(ab.rephrase("موجودی 1,200 عدد", model="m", chat=lambda m, **k: "库存 1200 个") is None,
          "rephrase rejected when the model switches language")


def run_real_ollama(model: str, codes: set[str] | None) -> None:
    print(f"== LLM fallback (real Ollama, {model}) ==")
    ok = 0
    t0 = time.time()
    for q, want, _ in LLM_CASES[1:]:
        it = ab.llm_route(q, model=model, known_codes=codes)
        got = it.name if it else None
        ok += got == want
        print(f"  «{q}» → {got}")
    dt = time.time() - t0
    print(f"  real LLM accuracy: {ok}/{len(LLM_CASES) - 1} ({dt:.1f}s)")
    # broader: every rules case through the LLM alone (no rules) — informative
    ok2 = 0
    for q, want, _ in CASES:
        it = ab.llm_route(q, model=model, known_codes=codes)
        ok2 += bool(it and it.name == want)
    print(f"  LLM-only intent accuracy on rules set: {ok2}/{len(CASES)}")
    check(ok >= len(LLM_CASES) - 2, "real LLM fallback accuracy too low")


def run_data() -> None:
    """Answers vs direct service calls on the DB copy."""
    import pandas as pd

    from analytics.critical_items import COL_DAYS, build_critical_items_rows
    from analytics.frames import load_primary_inventory
    from bot.jalali import format_datetime
    from db.models import Database
    from services import main_source_approvals as msa
    from services import n_tundish_report as nt
    from services.critical_items_report import SEGMENTS, build_critical_items_segments, critical_basis, critical_item_count
    from services.units import fmt_qty

    print("== data answers (DB copy) ==")
    db = Database()
    users = db.list_users()
    owner = next((u for u in users if u.get("role") == "owner"), None)
    if owner is None:
        print("  (no owner in DB — skipped)")
        return
    inv = load_primary_inventory(db, owner)
    codes = ab.known_codes(inv)
    run_rules(codes)
    run_heldout(codes, inv, ARGS.ollama_model)
    run_heldout_stock(codes, inv, ARGS.ollama_model)
    # the owner's real question → shroud stock from the latest stock data, with a table + PDF/Excel
    a = ab.answer(db, owner, "چندتا شرود داربم؟", use_llm=False, log=False, rephrase_answer=False)
    rows = inv.loc[inv["category_code"].map(ab._code_of) == "1581"]
    if not rows.empty:
        tot = float(pd.to_numeric(rows["quantity"], errors="coerce").fillna(0).sum())
        check(a.intent.name == ab.I_STOCK and a.facts.get("code") == "1581" and abs(a.facts["total"] - tot) < 1e-6,
              f"«چندتا شرود داربم؟» → shroud stock ({a.intent.name} {a.facts.get('code')})")
        check(a.understood.startswith("برداشت: موجودی شرود"), f"footer {a.understood}")
    from services import assistant_tables as at

    crit = ab.answer(db, owner, "چی کم داریم", use_llm=False, log=False, rephrase_answer=False)
    if at.has_table(crit.table):
        import tempfile

        from smoke_phase3 import check_pdf, check_xlsx

        with tempfile.TemporaryDirectory() as td:
            files = at.write_files(crit.table, td)
            check(set(files) == {"pdf", "xlsx"}, f"assistant table files {files}")
            check(not check_xlsx(files["xlsx"]), f"assistant xlsx style {check_xlsx(files['xlsx'])}")
            check(not check_pdf(files["pdf"]), f"assistant pdf fonts {check_pdf(files['pdf'])}")
        check(at.SEP in crit.text, "critical answer shows a text table")

    def ask(q: str, user=owner):
        a = ab.answer(db, user, q, use_llm=False, log=False, rephrase_answer=False)
        if ARGS.show:
            print(f"\n❓ {q}\n{a.text}\n[{', '.join(a.buttons)}]")
        return a

    def total(code: str) -> float:
        rows = inv.loc[inv["category_code"].map(ab._code_of) == code]
        return float(pd.to_numeric(rows["quantity"], errors="coerce").fillna(0).sum())

    n_num = 0
    # stock by code / name / id
    for q, code in (("از آجر ۱۶۰۳ چقدر داریم", "1603"), ("موجودی استوپر", "1672"),
                    ("378121641302R موجودی", "1203"), ("از نازل بیرونی چندتا مونده؟", "1674")):
        a = ask(q)
        if code not in codes:
            continue
        unit = a.facts.get("unit")
        check(a.facts.get("code") == code, f"stock «{q}» code {a.facts.get('code')} != {code}")
        check(fmt_qty(total(code), unit) in a.text, f"stock «{q}» total {fmt_qty(total(code), unit)} missing")
        n_num += 1
    a = ask("نازل پایینی چنجر")
    check(len(a.buttons) >= 2, "ambiguous name → buttons")
    # critical counts
    counts = critical_basis(db)
    if counts is not None:
        frames = build_critical_items_segments(inv, counts, "with")
        a = ask("چی کم داریم")
        for seg in SEGMENTS:
            n = critical_item_count(frames[seg])
            check(a.facts["counts"].get(seg) == n and f": {n} قلم" in a.text, f"critical {seg} count {n}")
            n_num += 1
        # coverage of the first critical code
        rows = build_critical_items_rows(inv, counts, segment=SEGMENTS[0], reno_mode="with", include_covered=True)
        plain = rows.loc[~rows["کد چهاررقمی"].astype(str).str.contains("/") & (rows["row_kind"] != "member")]
        if not plain.empty:
            r = plain.iloc[0]
            a = ask(f"حد تحمل {r['کد چهاررقمی']}")
            check(f"حد تحمل {r[COL_DAYS]} روز" in a.text, f"coverage days {r[COL_DAYS]} for {r['کد چهاررقمی']}")
            n_num += 1
    # N tundish
    for sec in nt.SECTIONS:
        res = nt.build_rows(inv, sec, 10, "with")
        a = ask(f"برای ۱۰ تاندیش {nt.SECTIONS[sec]} چی لازمه")
        if res.error:
            continue
        check(f"{res.item_count()} قلم با نیاز مثبت؛ {res.shortage_count()} قلم کسری" in a.text, f"n_tundish {sec}")
        n_num += 1
    a = ask("برای ۱۰ تاندیش چی لازمه")
    check(len(a.buttons) == 3, "n_tundish without section → 3 section buttons")
    # pending approvals
    a = ask("درخواست‌های در انتظار تأیید")
    n = len(msa.list_pending(db))
    check(a.facts.get("count") == n, f"pending count {n}")
    n_num += 1
    # last upload
    up = db.latest_stock_upload()
    a = ask("آخرین بار موجودی کی آپلود شد")
    if up:
        check(format_datetime(up["created_at"]) in a.text, "last upload datetime")
        n_num += 1
    # tundish stats: شهریور اسلب
    agg = db.aggregate_sequences("m:1405-06", "slab")
    if agg.get("tundish_count"):
        a = ask("شهریور ۱۴۰۵ چند تا تاندیش اسلب زدیم")
        check(f"اسلب: {agg['tundish_count']} تاندیش" in a.text, "tundish stats شهریور اسلب")
        n_num += 1
    # consumption
    a = ask("مصرف شهریور")
    print(f"  numeric cross-checks run: {n_num}")
    # permissions
    tech = {"bale_user_id": "999", "role": "technician", "active": 1, "display_name": "t"}
    a = ask("از آجر ۱۶۰۳ چقدر داریم", tech)
    check(not a.ok and "⛔" in a.text, "technician denied stock (same as menus)")
    a = ask("چی کم داریم", tech)
    check(not a.ok and "⛔" in a.text, "technician denied critical")
    ro = next((u for u in users if u.get("role") == "responsible_officer"), None)
    if ro:
        a = ask("درخواست‌های در انتظار تأیید", ro)
        check(not a.ok and "⛔" in a.text, "responsible officer denied pending approvals")
    a = ask("گزارش مازاد را بفرست", tech)
    check(not a.ok, "technician denied surplus report button")
    a = ask("راهنما", tech)
    check("موجودی قلم" not in a.text, "help hides intents the role cannot use")
    for q in ("گزارش جامع", "سلام", "هر چی"):
        ask(q)


def run_wiring() -> None:
    """Bot handler + web page wiring (fake Bale client, temp/copied DB)."""
    print("== bot / web wiring ==")
    from bot import keyboards as kb
    from bot.handlers import BotApp as BotHandlers
    from db.models import Database
    from services import voice_assistant as va

    class FakeClient:
        def __init__(self) -> None:
            self.sent: list[tuple[str, str, dict | None]] = []

        def send_message(self, chat_id, text, reply_markup=None, **_k):
            self.sent.append((str(chat_id), text, reply_markup))
            return {"message_id": len(self.sent)}

        def __getattr__(self, name):
            return lambda *a, **k: {}

    def btns(markup) -> list[str]:
        return [b["text"] for row in (markup or {}).get("keyboard", []) for b in row]

    db = Database()
    db.upsert_user("7701", "owner", display_name="مالک آزمون")
    db.upsert_user("7702", "technician", display_name="تکنسین آزمون")
    db.upsert_user("7703", "manager", display_name="مدیر آزمون")
    before = db.get_setting(va.SETTING_ENABLED)
    client = FakeClient()
    app = BotHandlers(client, db)

    def send(uid, text=None, **extra):
        n = len(client.sent)
        msg = {"from": {"id": int(uid), "first_name": "x"}, "chat": {"id": int(uid), "type": "private"}, **extra}
        if text is not None:
            msg["text"] = text
        app.handle_message(msg)
        return client.sent[n:]

    db.set_setting(va.SETTING_ENABLED, "0")
    app._sync_assistant_menu()
    out = send(7701, "چی کم داریم")
    check("لطفاً از دکمه‌های منو" in out[-1][1], "assistant off → old fallback text")
    check(kb.BTN_ASK_ASSISTANT not in btns(kb.main_menu(db.get_user("7701"))), "no ask button while off")
    send(7701, kb.BTN_BOT_SETTINGS)
    send(7701, kb.BTN_ASSISTANT)
    out = send(7701, kb.BTN_ASSISTANT_ON)
    check(db.get_setting(va.SETTING_ENABLED) == "1", "owner enables assistant (rules work without Ollama)")
    check(kb.BTN_ASK_ASSISTANT in btns(kb.main_menu(db.get_user("7702"))), "ask button in main menu when on")
    out = send(7702, kb.BTN_ASK_ASSISTANT)
    check("سؤالتان را بنویسید" in out[-1][1], "ask button prompt")
    out = send(7701, "از آجر ۱۶۰۳ چقدر داریم")
    check(bool(out) and ("موجودی کد 1603" in out[-1][1] or "منبع اصلی" in out[-1][1]), f"free text → brain: {out[-1][1][:80] if out else ''}")
    out = send(7702, "از آجر ۱۶۰۳ چقدر داریم")
    check(bool(out) and "⛔" in out[-1][1], "technician free text → permission denied")
    out = send(7701, "دستیار چی بلده")
    check(bool(out) and "دستیار هوشمند" in out[-1][1], "help via brain")
    out = send(7701, "/help")
    check(kb.BTN_ASK_ASSISTANT in out[-1][1], "/help mentions the assistant when on")
    out = send(7701, kb.BTN_ASSISTANT_REPHRASE_ON)
    check(db.get_setting("assistant_rephrase") == "1", "rephrase toggle on")
    send(7701, kb.BTN_ASSISTANT_REPHRASE_OFF)
    # menu buttons still win over the assistant
    out = send(7701, kb.BTN_HOME)
    check(kb.BTN_ASK_ASSISTANT in btns(out[-1][2]), "home menu shows ask button")
    # «🔎 برداشت: …» footer + «❌ منظورم این نبود» → logged misfire + intent choices
    out = send(7701, "چی کم داریم")
    check("🔎 برداشت: اقلام بحرانی" in out[-1][1], f"answer footer: {out[-1][1][-80:]}")
    check(kb.BTN_ASSISTANT_MISFIRE in btns(out[-1][2]), "misfire button under the answer")
    out = send(7701, kb.BTN_ASSISTANT_MISFIRE)
    choices = btns(out[-1][2])
    check("اقلام بحرانی کدام‌اند" in choices and "راهنمای دستیار" in choices, f"misfire choices {choices}")
    with db.connect() as conn:
        acts = conn.execute("SELECT details_json FROM user_activity WHERE action_key='report_assistant_misfire' "
                            "ORDER BY id DESC LIMIT 1").fetchall()
    check(bool(acts) and "چی کم داریم" in str(acts[0][0]), "misfire logged with the question")
    out = send(7701, "اقلام بحرانی کدام‌اند")
    check("اقلام بحرانی" in out[-1][1] and "🔎" in out[-1][1], "a misfire choice re-asks the assistant")
    # web parity
    from fastapi.testclient import TestClient

    from web.app import create_app
    from web.auth_web import set_credential
    from web.deps import reset_db_singleton

    reset_db_singleton()
    set_credential(db, bale_user_id="7701", username="own7701", password="secret77")
    set_credential(db, bale_user_id="7702", username="tech7702", password="secret77")
    webapp = create_app()
    own, tech = TestClient(webapp), TestClient(webapp)
    own.post("/login", data={"username": "own7701", "password": "secret77"})
    tech.post("/login", data={"username": "tech7702", "password": "secret77"})
    r = own.get("/assistant")
    check(r.status_code == 200 and "از دستیار بپرس" in r.text, "web /assistant page")
    r = own.post("/assistant/ask", data={"q": "درخواست‌های در انتظار تأیید"})
    check(r.status_code == 200 and ("در انتظار تأیید" in r.text), "web ask (owner)")
    # voice upload: request.form() returns starlette's UploadFile (regression: fastapi's class never matched)
    _tr, _av = va.transcribe, va.availability
    va.transcribe = lambda p, *a, **k: "درخواست‌های در انتظار تأیید" if Path(p).read_bytes() == b"RIFFfake" else ""
    va.availability = lambda: {**_av(), "whisper": True}
    try:
        r = own.post("/assistant/ask", files={"audio": ("q.wav", b"RIFFfake", "audio/wav")})
    finally:
        va.transcribe, va.availability = _tr, _av
    check(r.status_code == 200 and "🗣" in r.text and "در انتظار تأیید" in r.text, "web ask by voice upload")
    r = tech.post("/assistant/ask", data={"q": "درخواست‌های در انتظار تأیید"})
    check(r.status_code == 200 and "⛔" in r.text, "web ask denied for technician (same gate)")
    check("/assistant" in own.get("/home").text, "nav link when on")
    send(7701, kb.BTN_ASSISTANT_OFF)
    r = own.post("/assistant/ask", data={"q": "چی کم داریم"})
    check(r.status_code == 400 and "غیرفعال" in r.text, "web ask refused while off")
    db.set_setting(va.SETTING_ENABLED, before or "0")
    kb.set_assistant_provider(None)
    kb.set_assistant_menu(False)


if __name__ == "__main__":
    run_units()
    if ARGS.db:
        run_data()
    else:
        run_rules(None)
        run_heldout(None, None, ARGS.ollama_model)
        run_heldout_stock(None, None, ARGS.ollama_model)
    run_llm_mock(None)
    run_wiring()
    if ARGS.ollama_model:
        run_real_ollama(ARGS.ollama_model, None)
    if FAILS:
        print(f"\nFAILED: {len(FAILS)}")
        sys.exit(1)
    print("\nOK smoke_assistant")
