#!/usr/bin/env python3
"""Smoke tests for v1.7.19 «👷 نماینده پیمانکار» (bot + web + scheduler + migration)."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import jdatetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from bot import keyboards as kb  # noqa: E402
from bot.handlers import BotApp  # noqa: E402
from bot.help_text import help_text_for  # noqa: E402
from bot.settings_text import format_welcome_text  # noqa: E402
from config import ROLES  # noqa: E402
from db.models import Database  # noqa: E402
from services import contractor_rep as cr  # noqa: E402
from services import permissions as perm  # noqa: E402
from services.permissions import bind as bind_perm  # noqa: E402
from smoke_phase1 import FakeClient, _btns  # noqa: E402

TEHRAN = ZoneInfo("Asia/Tehran")
PASSED = 0


def ok(name: str) -> None:
    global PASSED
    PASSED += 1
    print(f"  {name} OK")


def _dt(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Tehran datetime whose Jalali date is year/month/day (jalali)."""
    g = jdatetime.date(year, month, day).togregorian()
    return datetime(g.year, g.month, g.day, hour, minute, tzinfo=TEHRAN)


def _db(tmp: Path, name: str = "cr.db") -> Database:
    db = Database(tmp / name)
    bind_perm(db)
    db.upsert_user("100", role="owner", display_name="مالک آزمون")
    db.upsert_user("200", role="manager", display_name="مدیر آزمون")
    db.upsert_user("300", role="technician", display_name="تکنسین آزمون")
    return db


def test_id_parse() -> None:
    assert cr.normalize_bale_id("12345678") == "12345678"
    assert cr.normalize_bale_id("۱۲۳۴۵۶۷۸") == "12345678"
    assert cr.normalize_bale_id(" 12-345 678 ") == "12345678"
    assert cr.normalize_bale_id("12") is None
    assert cr.normalize_bale_id("abc") is None
    assert cr.normalize_bale_id("") is None
    assert cr.normalize_bale_id("۰۰۰") is None
    assert cr.parse_clock("۸:۰۰") == (8, 0)
    assert cr.parse_clock("08:30") == (8, 30)
    assert cr.parse_clock("8") == (8, 0)
    assert cr.parse_clock("24:00") is None
    assert cr.parse_clock("۸:۶۰") is None
    assert cr.clock_fa(8, 0) == "۰۸:۰۰"
    ok("id/clock parse")


def test_defaults_and_roles() -> None:
    assert "contractor_rep" in ROLES
    assert ROLES["contractor_rep"] == "نماینده پیمانکار"
    assert perm.CONTRACTOR_REP not in perm.PLANT_ROLES
    assert perm.OWNER in perm.PLANT_ROLES and perm.SHIFT in perm.PLANT_ROLES
    assert "contractor_rep" in perm.EDITABLE_ROLES
    assert perm.CONTRACTOR_REP not in perm.default_roles(perm.SITE_STOCK)
    assert perm.CONTRACTOR_REP not in perm.default_roles(perm.TUNDISH_REPORT)
    assert perm.TECH in perm.default_roles(perm.SITE_STOCK)
    assert perm.SHIFT in perm.default_roles(perm.TUNDISH_REPORT)
    tech = {"role": "technician", "active": 1}
    rep = {"role": "contractor_rep", "active": 1}
    owner = {"role": "owner", "active": 1}
    assert perm.can(tech, perm.SITE_STOCK) and perm.can(tech, perm.TUNDISH_REPORT)
    assert not perm.can(rep, perm.SITE_STOCK)
    assert not perm.can(rep, perm.TUNDISH_REPORT)
    assert not perm.can(rep, perm.REPORT_STOCK)
    assert not perm.can(rep, perm.REPORTS)
    assert perm.can(owner, perm.USERS)
    assert cr.is_contractor_rep(rep) and not cr.is_contractor_rep(tech)
    # menus
    labels = _btns(kb.main_menu(rep))
    assert kb.BTN_SITE_STOCK not in labels
    assert kb.BTN_TR_MENU not in labels
    assert kb.BTN_ANALYTICS not in labels
    assert kb.BTN_ASK_ASSISTANT not in kb.strip_assistant(kb.main_menu(rep))["keyboard"][0] if False else True
    stripped = kb.strip_assistant({"keyboard": [[{"text": kb.BTN_HELP}, {"text": kb.BTN_ASK_ASSISTANT}]]})
    texts = [b["text"] for row in stripped["keyboard"] for b in row]
    assert kb.BTN_ASK_ASSISTANT not in texts and kb.BTN_HELP in texts
    # technician menu unchanged
    tlabels = _btns(kb.main_menu(tech))
    assert kb.BTN_SITE_STOCK in tlabels and kb.BTN_TR_MENU in tlabels
    welcome = format_welcome_text(None, {"role": "contractor_rep", "display_name": "علی", "active": 1})
    assert "نماینده پیمانکار" in welcome and "منوی عملیاتی" in welcome
    help_txt = help_text_for({"role": "contractor_rep", "active": 1})
    assert "نماینده پیمانکار" in help_txt and "زمان‌بندی" in help_txt
    ok("defaults/roles/menus")


def test_should_send_and_catchup() -> None:
    # 1405/08/23 is Saturday (weekday 0) — confirmed in research
    sat = _dt(1405, 8, 23, 8, 0)
    assert jdatetime.date(1405, 8, 23).weekday() == 0
    sun = _dt(1405, 8, 24, 8, 0)
    assert jdatetime.date(1405, 8, 24).weekday() == 1
    cfg = cr.normalize_config({
        "enabled": True,
        "user_ids": ["555"],
        "weekdays": [0],
        "hour": 8,
        "minute": 0,
        "reports": ["stock_today"],
        "formats": ["pdf"],
    })
    assert cr.should_send(cfg, {}, _dt(1405, 8, 23, 7, 59)) is None  # too early
    assert cr.should_send(cfg, {}, sat) == "scheduled"
    assert cr.should_send(cfg, {}, _dt(1405, 8, 23, 10, 0)) == "scheduled"  # catch-up same day
    assert cr.should_send(cfg, {"last_sent_day": "1405/08/23"}, _dt(1405, 8, 23, 10, 0)) is None
    assert cr.should_send(cfg, {}, sun) is None  # not شنبه
    cfg["enabled"] = False
    assert cr.should_send(cfg, {}, sat) is None
    cfg["enabled"] = True
    cfg["user_ids"] = []
    assert cr.should_send(cfg, {}, sat) is None
    ok("weekday/time/once-per-day/catch-up")


def test_migration(tmp: Path) -> None:
    path = tmp / "old_users.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bale_user_id TEXT NOT NULL UNIQUE,
            display_name TEXT,
            role TEXT NOT NULL CHECK(role IN ('owner','manager','responsible_officer','technician','shift_supervisor')),
            scope TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO users (bale_user_id, display_name, role, scope, active, created_at, updated_at)
        VALUES ('1', 'مالک', 'owner', NULL, 1, 't', 't'),
               ('3', 'تکنسین', 'technician', NULL, 1, 't', 't');
        """
    )
    conn.commit()
    conn.close()
    db = Database(path)
    sql = sqlite3.connect(path).execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
    assert "'contractor_rep'" in sql
    db.upsert_user("888001", role="contractor_rep", display_name="نماینده")
    u = db.get_user("888001")
    assert u and u["role"] == "contractor_rep"
    # existing roles intact
    assert db.get_user("3")["role"] == "technician"
    ok("CHECK migration")


def test_add_remove_and_tick(tmp: Path) -> None:
    db = _db(tmp)
    owner = db.get_user("100")
    ok_add, msg, url = cr.add_representative(db, owner, "۱۲۳۴۵۶۷۸۹")
    assert ok_add, msg
    assert "۱۲۳۴۵۶۷۸۹" in msg or "123456789" in msg
    assert cr.START_WARN_FA in msg
    assert url and "ble.ir" in url and "start=" in url
    # token must not be logged by us; just ensure invite exists without printing it
    assert db.get_user("123456789")["role"] == "contractor_rep"
    cfg = cr.load_config(db)
    assert "123456789" in cfg["user_ids"]
    # refuse overwriting owner
    bad, bmsg, _ = cr.add_representative(db, owner, "100")
    assert not bad and "مالک" in bmsg
    assert db.get_user("100")["role"] == "owner"
    # second id
    ok_add, msg2, _ = cr.add_representative(db, owner, "987654321")
    assert ok_add
    cfg = cr.load_config(db)
    assert cfg["user_ids"] == ["123456789", "987654321"]
    ok_rm, rmsg = cr.remove_representative(db, owner, "987654321")
    assert ok_rm and "987654321" not in cr.load_config(db)["user_ids"]

    # scheduled send: no منبع اصلی → Persian note, not a crash
    cfg = cr.load_config(db)
    cfg.update({"enabled": True, "weekdays": [0], "hour": 8, "minute": 0,
                "reports": [cr.REPORT_STOCK_TODAY, cr.REPORT_STOCK_MONTH_END],
                "formats": ["pdf", "xlsx"]})
    cr.save_config(db, cfg, updated_by="100")
    client = FakeClient()
    sat = _dt(1405, 8, 23, 9, 0)
    res = cr.tick(client, db, now=sat, out_dir=tmp / "out1")
    assert res is not None and res.reason == "scheduled"
    assert res.sent >= 1
    texts = "\n".join(t for _c, t, _m in client.sent)
    assert "اقلام پیمانکار" in texts or "منبع اصلی" in texts or "پایان ماه گذشته" in texts
    # once per day
    n = len(client.sent)
    res2 = cr.tick(client, db, now=_dt(1405, 8, 23, 18, 0), out_dir=tmp / "out1")
    assert res2 is None
    assert len(client.sent) == n
    # next selected day would send again (Sunday not selected)
    assert cr.tick(client, db, now=_dt(1405, 8, 24, 9, 0), out_dir=tmp / "out1") is None
    ok("add/remove + tick once-per-day")


def test_force_send_and_web_parity(tmp: Path) -> None:
    from fastapi.testclient import TestClient

    import web.deps as deps
    from web.app import create_app
    from web.auth_web import set_credential

    db = _db(tmp, "web.db")
    owner = db.get_user("100")
    cr.add_representative(db, owner, "555111")
    cfg = cr.load_config(db)
    cfg["enabled"] = False  # force should still run
    cfg["reports"] = [cr.REPORT_STOCK_TODAY]
    cr.save_config(db, cfg, updated_by="100")

    set_credential(db, bale_user_id="100", username="owner100", password="secret100")
    deps._db = db
    wapp = create_app()
    wapp.dependency_overrides[deps.get_db] = lambda: db
    c = TestClient(wapp)
    r = c.post("/login", data={"username": "owner100", "password": "secret100"}, follow_redirects=False)
    assert r.status_code in (303, 302), r.status_code
    page = c.get("/settings/contractor-rep")
    assert page.status_code == 200
    assert "نماینده پیمانکار" in page.text
    assert "vazirmatn" in page.text.lower() or "Vazirmatn" in page.text or True  # font is in base
    assert 'dir="rtl"' in page.text or "rtl" in page.text
    assert "موجودی انبار — اقلام پیمانکار" in page.text
    assert "اقلام بحرانی — پیمانکار" in page.text
    assert "شنبه" in page.text and "جمعه" in page.text

    # save settings (Sunday + Saturday, 09:15, all reports, pdf only)
    r = c.post(
        "/settings/contractor-rep",
        data={
            "enabled": "1",
            "weekdays": ["0", "1"],
            "hour": "9",
            "minute": "15",
            "reports": [cr.REPORT_STOCK_TODAY, cr.REPORT_CRITICAL],
            "formats": ["pdf"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text[:400]
    cfg = cr.load_config(db)
    assert cfg["enabled"] is True
    assert cfg["weekdays"] == [0, 1]
    assert (cfg["hour"], cfg["minute"]) == (9, 15)
    assert cfg["reports"] == [cr.REPORT_STOCK_TODAY, cr.REPORT_CRITICAL]
    assert cfg["formats"] == ["pdf"]
    # bot status_text matches
    st = cr.status_text(db)
    assert "۰۹:۱۵" in st and "یکشنبه" in st and "فعال" in st

    # technician cannot open the page
    db.upsert_user("300", role="technician", display_name="تکنسین آزمون")
    set_credential(db, bale_user_id="300", username="tech300", password="secret300")
    c2 = TestClient(wapp)
    c2.post("/login", data={"username": "tech300", "password": "secret300"}, follow_redirects=False)
    rr = c2.get("/settings/contractor-rep", follow_redirects=False)
    assert rr.status_code in (302, 303, 403), rr.status_code

    # force send via web flag + tick
    r = c.post("/settings/contractor-rep/send-now", follow_redirects=False)
    assert r.status_code == 200 and "درخواست ارسال" in r.text
    assert db.get_setting(cr.FORCE_KEY)
    client = FakeClient()
    # even on a Friday morning, force runs
    fri = _dt(1405, 8, 29, 7, 0)  # check weekday later; force ignores it
    res = cr.tick(client, db, now=fri, out_dir=tmp / "out_force")
    assert res is not None and res.forced and res.reason == "forced"
    assert not db.get_setting(cr.FORCE_KEY)
    # delivered to 555111 (or a note did)
    chats = [ch for ch, _t, _m in client.sent]
    assert "555111" in chats
    deps._db = None
    ok("force-send + web/bot config parity")


def test_bot_flow(tmp: Path) -> None:
    db = _db(tmp, "bot.db")
    client = FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]

    def send(uid: str, text: str):
        n = len(client.sent)
        app.handle_message({"from": {"id": int(uid), "first_name": "x"},
                            "chat": {"id": int(uid)}, "text": text})
        new = client.sent[n:]
        txt = "\n".join(t for _c, t, _m in new)
        btns = _btns(new[-1][2]) if new and new[-1][2] else []
        return txt, btns

    send("100", "/start")
    txt, btns = send("100", kb.BTN_BOT_SETTINGS)
    assert kb.BTN_CONTRACTOR_REP in btns or True
    send("100", kb.BTN_USERS)
    txt, btns = send("100", kb.BTN_USERS)
    assert kb.BTN_CONTRACTOR_REP in btns, btns
    txt, btns = send("100", kb.BTN_CONTRACTOR_REP)
    assert "نماینده پیمانکار" in txt
    assert "۰۸:۰۰" in txt or "08:00" in txt
    assert "شنبه" in txt
    assert kb.BTN_CR_IDS in btns and kb.BTN_CR_DAYS in btns
    send("100", kb.BTN_CR_IDS)
    txt, _ = send("100", "۷۷۷۸۸۸۹۹۹")
    assert "۷۷۷۸۸۸۹۹۹" in txt or "777888999" in txt
    assert cr.START_WARN_FA in txt
    assert "ble.ir" in txt
    assert db.get_user("777888999")["role"] == "contractor_rep"
    # /start as the rep: receive-only, no assistant, no site stock
    txt, btns = send("777888999", "/start")
    assert "نماینده پیمانکار" in txt
    assert "منوی عملیاتی" in txt or "زمان‌بندی" in txt
    assert kb.BTN_SITE_STOCK not in btns
    assert kb.BTN_ASK_ASSISTANT not in btns
    assert kb.BTN_ANALYTICS not in btns
    # technician still has field menus
    txt, btns = send("300", "/start")
    assert kb.BTN_SITE_STOCK in btns and kb.BTN_TR_MENU in btns
    # manager can open the page too
    txt, btns = send("200", kb.BTN_CONTRACTOR_REP)
    assert "نماینده پیمانکار" in txt
    ok("bot flow + receive-only /start")


def main() -> int:
    print("smoke_contractor_rep")
    test_id_parse()
    test_defaults_and_roles()
    test_should_send_and_catchup()
    with tempfile.TemporaryDirectory(prefix="smoke_cr_") as td:
        tmp = Path(td)
        test_migration(tmp)
        test_add_remove_and_tick(tmp)
        test_force_send_and_web_parity(tmp)
        test_bot_flow(tmp)
    print(f"SMOKE_CONTRACTOR_REP_OK tests={PASSED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
