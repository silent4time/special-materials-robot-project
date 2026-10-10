#!/usr/bin/env python3
"""Offline smoke: گزارش تاندیش بعد از ریخته‌گری — parser, DB, bot flow, settings CRUD, web."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402  — temp DB/reports/uploads before config import

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SLAB = (
    "تاندیش ۱۰ اسلب ۱ سکونس ۸ ذوبه مارک ۳۷۱۰ . نازل هر دو خط در ذوب ۵ بعلت گرفتگی "
    "تعویض گردید شرود تا پایان سکونس نرمال سولار پودر قالب کاسپین پودر تاندیش فارس ریزان"
)
BILLET = (
    "تاندیش 8 بیلت 1 بعلت محدودیت سکونس 12 ذوبه پایان یافت / مارک ذوب 4446 / "
    "نوع ایمپکت رایان / مدت ریخته گری 835"
)


class _FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict | None]] = []

    def send_message(self, chat_id, text, reply_markup=None, **_k):
        self.sent.append((text, reply_markup))
        return {"message_id": len(self.sent)}

    def edit_message_reply_markup(self, *a, **k):
        return {}

    def answer_callback_query(self, *a, **k):
        return {}


def _buttons(markup: dict | None) -> list[str]:
    if not markup:
        return []
    out = []
    for row in markup.get("keyboard") or []:
        for b in row:
            out.append(b["text"] if isinstance(b, dict) else b)
    return out


def test_parse_and_db(db) -> None:
    from services import tundish_report as tr

    slab_items = db.list_tundish_report_items("slab")
    labels = [it["label"] for it in slab_items]
    assert labels == [
        "نازل — وضعیت",
        "نازل — ذوب تعویض",
        "نازل — علت تعویض",
        "شرود — وضعیت",
        "شرود — سازنده",
        "پودر قالب",
        "پودر تاندیش",
    ], labels
    for sec in ("billet", "bloom"):
        labs = [it["label"] for it in db.list_tundish_report_items(sec)]
        assert labs == ["علت پایان سکونس", "نوع ایمپکت", "مدت ریخته‌گری (دقیقه)"], (sec, labs)

    p = tr.parse_report_text(SLAB, "slab", tr.get_lines(db, "slab"), slab_items)
    assert p["fields"] == {
        "tundish_no": 10,
        "line": "اسلب ۱ (CCM1)",
        "sequence": 8,
        "melt_count": 8,
        "steel_grade": "3710",
    }, p["fields"]
    by = {it["label"]: p["items"].get(int(it["id"])) for it in slab_items}
    assert by["نازل — وضعیت"] == "تعویض هر دو خط"
    assert str(by["نازل — ذوب تعویض"]) == "5"
    assert by["نازل — علت تعویض"] == "گرفتگی"
    assert by["شرود — وضعیت"] == "نرمال تا پایان سکونس"
    assert by["پودر قالب"] == "کاسپین" and by["پودر تاندیش"] == "فارس ریزان"

    b_items = db.list_tundish_report_items("billet")
    pb = tr.parse_report_text(BILLET, "billet", tr.get_lines(db, "billet"), b_items)
    assert pb["fields"] == {
        "tundish_no": 8,
        "line": "بیلت ۱ (CCM4)",
        "sequence": 12,
        "melt_count": 12,
        "steel_grade": "4446",
    }, pb["fields"]
    bb = {it["label"]: pb["items"].get(int(it["id"])) for it in b_items}
    assert bb == {
        "علت پایان سکونس": "محدودیت سکونس",
        "نوع ایمپکت": "رایان",
        "مدت ریخته‌گری (دقیقه)": "835",
    }, bb
    assert tr.match_line("بیلت 2", tr.get_lines(db, "billet")) == "بیلت ۲ (CCM5)"

    user = {"bale_user_id": "777", "display_name": "تست", "role": "technician", "active": 1}
    rep, errs = tr.submit(db, user, "billet", pb["fields"], pb["items"], source="test", raw_text=BILLET)
    assert not errs and rep, errs
    assert rep["fields"]["melt_count"] == 12 and len(rep["values"]) == 3
    assert rep["created_at_tehran"] and rep["jalali_date"]
    num = [v for v in rep["values"] if v["item_type"] == "number"][0]
    assert num["value_num"] == 835.0
    # validation errors
    _rep, errs = tr.submit(db, user, "slab", {"tundish_no": "x"}, {}, source="test")
    assert errs and any("شماره تاندیش" in e for e in errs)
    print("tundish_report parse/db OK")


def test_settings_crud(db) -> None:
    from services import tundish_report as tr

    it = tr.add_item(db, "bloom", label="دمای تاندیش", item_type="number", required=False, updated_by="1")
    assert it["sort_order"] == 4
    tr.update_item(db, it["id"], item_type="choice", options="بالا، نرمال، پایین")
    got = db.get_tundish_report_item(it["id"])
    assert got["item_type"] == "choice" and got["options"] == ["بالا", "نرمال", "پایین"]
    tr.move_item(db, it["id"], 1)
    assert db.list_tundish_report_items("bloom")[0]["id"] == it["id"]
    try:
        tr.update_item(db, it["id"], options="")
        raise AssertionError("empty options accepted")
    except ValueError:
        pass
    tr.delete_item(db, it["id"])
    assert [x["sort_order"] for x in db.list_tundish_report_items("bloom")] == [1, 2, 3]
    lines = tr.set_lines(db, "bloom", "بلوم (CCM3)\nبلوم ۲")
    assert tr.get_lines(db, "bloom") == lines == ["بلوم (CCM3)", "بلوم ۲"]
    tr.set_lines(db, "bloom", ["بلوم (CCM3)"])
    print("tundish_report settings CRUD OK")


def test_bot_flow(db) -> None:
    from bot import keyboards as kb
    from bot.handlers import BotApp

    client = _FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]
    db.upsert_user("501", role="technician", display_name="تکنسین تست")
    db.upsert_user("502", role="manager", display_name="مدیر تست")

    def send(uid: str, text: str) -> tuple[str, list[str]]:
        app.handle_message({"from": {"id": int(uid), "first_name": "x"}, "chat": {"id": int(uid)}, "text": text})
        txt, mk = client.sent[-1]
        return txt, _buttons(mk)

    # technician sees the main-menu button
    assert kb.BTN_TR_MENU in _buttons(kb.main_menu({"role": "technician"}))
    txt, btns = send("501", kb.BTN_TR_MENU)
    assert kb.BTN_TR_SECTION["slab"] in btns and kb.BTN_TR_SETTINGS not in btns
    # step-by-step slab
    send("501", kb.BTN_TR_SECTION["slab"])
    txt, btns = send("501", kb.BTN_TR_MODE_STEP)
    assert "شماره تاندیش" in txt
    txt, btns = send("501", "abc")
    assert "⚠️" in txt
    txt, btns = send("501", "۱۰")
    assert "اسلب ۱ (CCM1)" in btns
    send("501", "اسلب ۱ (CCM1)")
    send("501", "8")
    send("501", "8")
    txt, btns = send("501", "3710")
    assert "تعویض هر دو خط" in btns
    send("501", "تعویض هر دو خط")
    txt, btns = send("501", "5")  # nozzle melt (optional number)
    assert kb.BTN_TR_SKIP in btns  # cause is optional
    send("501", "گرفتگی")
    txt, btns = send("501", kb.BTN_TR_PREV)  # back to cause
    assert "علت" in txt
    send("501", "گرفتگی")
    send("501", "نرمال تا پایان سکونس")
    send("501", kb.BTN_TR_SKIP)  # shroud maker skipped
    send("501", "کاسپین")
    txt, btns = send("501", "فارس ریزان")
    assert kb.BTN_TR_CONFIRM in btns and "پودر تاندیش: فارس ریزان" in txt
    # edit one field then confirm
    txt, btns = send("501", kb.BTN_TR_EDIT)
    send("501", "1")
    txt, btns = send("501", "11")
    assert "شماره تاندیش: 11" in txt and kb.BTN_TR_CONFIRM in btns
    txt, _ = send("501", kb.BTN_TR_CONFIRM)
    assert "✅ گزارش #" in txt, txt
    last = db.list_tundish_reports(limit=1)[0]
    assert last["source"] == "bot" and last["bale_user_id"] == "501" and last["tundish_no"] == "11"
    vals = {v["label"]: v["value"] for v in last["items"]}
    assert vals["شرود — سازنده"] is None and vals["نازل — ذوب تعویض"] == 5

    # compact billet from text
    send("501", kb.BTN_TR_SECTION["billet"])
    send("501", kb.BTN_TR_MODE_TEXT)
    txt, btns = send("501", BILLET)
    assert kb.BTN_TR_CONFIRM in btns, txt
    assert "نوع ایمپکت: رایان" in txt and "مدت ریخته‌گری (دقیقه): 835" in txt
    txt, _ = send("501", kb.BTN_TR_CONFIRM)
    assert "✅" in txt
    last = db.list_tundish_reports(limit=1)[0]
    assert last["section"] == "billet" and last["raw_text"] == BILLET

    # global nav abandons a draft
    send("501", kb.BTN_TR_SECTION["bloom"])
    send("501", kb.BTN_TR_MODE_STEP)
    txt, _ = send("501", kb.BTN_BACK_MAIN)
    assert "منوی اصلی" in txt and "501" not in app.tundish_report.pending

    # technician denied settings
    txt, _ = send("501", kb.BTN_TR_SETTINGS)
    assert "دسترسی ندارید" in txt or "فقط مالک یا مدیر" in txt

    # manager settings CRUD via bot
    txt, btns = send("502", kb.BTN_TR_SETTINGS)
    assert kb.BTN_TRS_SECTION["billet"] in btns
    send("502", kb.BTN_TRS_SECTION["billet"])
    send("502", kb.BTN_TRS_ADD)
    send("502", "وضعیت استاپر")
    send("502", kb.BTN_TRS_TYPE_CHOICE)
    send("502", "نرمال، گیر")
    txt, _ = send("502", kb.BTN_TRS_OPTIONAL)
    assert "اضافه شد" in txt and "وضعیت استاپر" in txt
    send("502", kb.BTN_TRS_EDIT)
    send("502", "4")
    txt, _ = send("502", kb.BTN_TRS_E_ORDER)
    txt, _ = send("502", "1")
    assert "ترتیب تغییر کرد" in txt
    assert db.list_tundish_report_items("billet")[0]["label"] == "وضعیت استاپر"
    send("502", kb.BTN_TRS_E_LABEL)
    txt, _ = send("502", "استاپر")
    assert "برچسب تغییر کرد" in txt
    send("502", kb.BTN_TRS_BACK_SECTION)
    send("502", kb.BTN_TRS_DELETE)
    send("502", "1")
    txt, _ = send("502", kb.BTN_TRS_DELETE_CONFIRM)
    assert "حذف شد" in txt
    assert [x["label"] for x in db.list_tundish_report_items("billet")] == [
        "علت پایان سکونس",
        "نوع ایمپکت",
        "مدت ریخته‌گری (دقیقه)",
    ]
    send("502", kb.BTN_TRS_LINES)
    txt, _ = send("502", "بیلت ۱ (CCM4)، بیلت ۲ (CCM5)")
    assert "خطوط ذخیره شد" in txt
    print("tundish_report bot flow OK")


def test_web(db_path: Path) -> None:
    import os

    os.environ["DATABASE_PATH"] = str(db_path)
    from fastapi.testclient import TestClient

    import web.deps as deps
    from db.models import Database
    from web.app import create_app

    deps._db = Database(db_path)
    app = create_app()
    db = deps._db
    db.upsert_user("601", role="technician", display_name="وب تکنسین")
    db.upsert_user("602", role="owner", display_name="وب مالک")

    def login_as(client: TestClient, uid: str) -> None:
        # Session cookie set through the app's own test-only helper route is not
        # available; patch current_user_optional instead.
        app.dependency_overrides[deps.current_user_optional] = lambda: db.get_user(uid)

    c = TestClient(app)
    login_as(c, "601")
    r = c.get("/tundish-report?section=billet")
    assert r.status_code == 200 and "نوع ایمپکت" in r.text, r.text[:500]
    r = c.post("/tundish-report/parse", data={"section": "billet", "raw_text": BILLET})
    assert r.status_code == 200 and "4446" in r.text and "835" in r.text
    items = db.list_tundish_report_items("billet")
    form = {
        "section": "billet",
        "tundish_no": "8",
        "line": "بیلت ۱ (CCM4)",
        "sequence": "12",
        "melt_count": "12",
        "steel_grade": "4446",
        "raw_text": BILLET,
    }
    vals = {"علت پایان سکونس": "محدودیت سکونس", "نوع ایمپکت": "رایان", "مدت ریخته‌گری (دقیقه)": "835"}
    for it in items:
        form[f"item_{it['id']}"] = vals[it["label"]]
    before = len(db.list_tundish_reports(limit=100))
    r = c.post("/tundish-report/submit", data=form)
    assert r.status_code == 200 and "ثبت شد" in r.text, r.text[:800]
    assert len(db.list_tundish_reports(limit=100)) == before + 1
    assert db.list_tundish_reports(limit=1)[0]["source"] == "web"
    r = c.post("/tundish-report/submit", data={"section": "billet"})
    assert r.status_code == 400
    r = c.get("/tundish-report/settings?section=slab")
    assert r.status_code == 403
    login_as(c, "602")
    r = c.get("/tundish-report/settings?section=slab")
    assert r.status_code == 200 and "پودر قالب" in r.text
    r = c.post(
        "/tundish-report/settings/item/add",
        data={"section": "slab", "label": "وضعیت استاپر", "item_type": "choice", "options": "نرمال\nگیر", "required": "1"},
    )
    assert r.status_code == 200 and "وضعیت استاپر" in r.text
    new = [x for x in db.list_tundish_report_items("slab") if x["label"] == "وضعیت استاپر"][0]
    r = c.post(
        f"/tundish-report/settings/item/{new['id']}/edit",
        data={"section": "slab", "label": "استاپر", "item_type": "choice", "options": "نرمال، گیر، تعویض", "position": "1"},
    )
    assert r.status_code == 200
    got = db.get_tundish_report_item(new["id"])
    assert got["label"] == "استاپر" and got["options"][-1] == "تعویض" and not got["required"]
    assert db.list_tundish_report_items("slab")[0]["id"] == new["id"]
    r = c.post(f"/tundish-report/settings/item/{new['id']}/delete", data={"section": "slab"})
    assert r.status_code == 200 and db.get_tundish_report_item(new["id"]) is None
    r = c.post("/tundish-report/settings/lines", data={"section": "slab", "lines": "اسلب ۱ (CCM1)\nاسلب ۲ (CCM2)"})
    assert r.status_code == 200
    app.dependency_overrides.clear()
    print("tundish_report web OK")


def main() -> int:
    from db.models import Database

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "tr.db"
        db = Database(path)
        test_parse_and_db(db)
        test_settings_crud(db)
        test_bot_flow(db)
        test_web(path)
    print("SMOKE_TUNDISH_REPORT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
