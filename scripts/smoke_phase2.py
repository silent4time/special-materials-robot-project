"""Phase-2 smoke (1405-07-18 spec items 16–18). Temp DBs only — never the live DB.

* 16: one factory-wide منبع اصلی (edit by user B is what user A sees at once)
* 17: role shift_supervisor (DB, invites, menus, reminders, web login, help)
* 18: editable role permissions (DB overrides, owner locked, bot inline + web page,
      activity log, enforcement in menus + handlers + routes), menu walk for
      shift_supervisor and for a role with custom toggled permissions.
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402  — temp DB/reports/uploads before config import

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_GUARD_DIR = Path(tempfile.mkdtemp(prefix="smoke_p2_guard_"))
os.environ["DATABASE_PATH"] = str(_GUARD_DIR / "guard.db")

import pandas as pd  # noqa: E402


def _inv_frame(qty: float = 5.0) -> pd.DataFrame:
    from services.main_source import INVENTORY_COLUMNS

    rows = []
    for i, (code, name) in enumerate([("1601", "آجر"), ("1710", "نازل"), ("1800", "مازاد")]):
        r = {c: "" for c in INVENTORY_COLUMNS}
        r.update(
            category_code=code, id=f"3784{code}00{i}0001A", product_name=name,
            quantity=qty, priority=1, unit="عدد",
        )
        rows.append(r)
    return pd.DataFrame(rows)


def test_shared_source(tmp: Path) -> None:
    import services.main_source as ms
    from analytics.frames import load_primary_inventory, resolve_primary_inventory_path
    from db.models import Database

    ms.UPLOAD_DIR = tmp / "uploads"
    db = Database(tmp / "src.db")
    db.upsert_user("21", role="owner", display_name="A")
    db.upsert_user("22", role="responsible_officer", display_name="B")
    db.upsert_user("23", role="manager", display_name="C")
    ms.persist_primary_frame(db, _inv_frame(5.0), bale_user_id="21")
    pa = resolve_primary_inventory_path(db, bale_user_id="21")
    # officer edits a row → everyone (incl. A, who uploaded) sees it immediately
    item = _inv_frame().iloc[0]["id"]
    ms.upsert_row(db, item, {"quantity": 42}, bale_user_id="22")
    for uid in ("21", "22", "23", "999"):
        p = resolve_primary_inventory_path(db, bale_user_id=uid)
        assert p != pa and "/22/" in p, (uid, p)
        df = load_primary_inventory(db, {"bale_user_id": uid, "role": "owner", "active": 1})
        q = float(df.loc[df["id"].astype(str) == item, "quantity"].iloc[0])
        assert q == 42, (uid, q)
    # a later full upload by A wins again for everybody
    ms.persist_primary_frame(db, _inv_frame(7.0), bale_user_id="21")
    assert "/21/" in resolve_primary_inventory_path(db, bale_user_id="22")
    # stock-upload filter: new id w/ existing code passes, 1800 / unknown code rejected
    old = _inv_frame()
    new = pd.concat([old, _inv_frame().assign(id=lambda d: d["id"] + "N")], ignore_index=True)
    extra = new.iloc[[0]].copy()
    extra["id"], extra["category_code"] = "37849999000001Z", "9999"
    new = pd.concat([new, extra], ignore_index=True)
    kept, skipped = ms.filter_inventory_upload(old, new, allow_new_codes=False)
    kept_ids = set(kept["id"].astype(str))
    assert old.iloc[0]["id"] + "N" in kept_ids and old.iloc[1]["id"] + "N" in kept_ids
    assert old.iloc[2]["id"] + "N" not in kept_ids and "37849999000001Z" not in kept_ids
    assert len(skipped) == 2, skipped
    print("  16 shared منبع اصلی OK (edits immediate for all users; stock auto-add rules)")


def _helpers():
    sys.path.insert(0, str(ROOT / "scripts"))
    import smoke_phase1 as p1  # FakeClient / ErrCatcher / walk_role / _db_copy

    return p1


def test_shift_role(tmp: Path) -> None:
    """Item 17 on a file-level copy of the live DB (migration runs on the copy)."""
    import sqlite3

    from bot import keyboards as kb
    from bot.handlers import BotApp
    from bot.help_text import help_text_for
    from services import permissions as perm

    p1 = _helpers()
    db = p1._db_copy(tmp)
    sql = sqlite3.connect(db.path).execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
    assert "'shift_supervisor'" in sql
    owner = next(u for u in db.list_users() if u.get("role") == "owner" and u.get("active"))
    oid = str(owner["bale_user_id"])
    client = p1.FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]

    def send(uid: str, text: str, args_text: str | None = None):
        n = len(client.sent)
        app.handle_message({"from": {"id": int(uid), "first_name": "x"}, "chat": {"id": int(uid)}, "text": args_text or text})
        new = client.sent[n:]
        return "\n".join(t for _c, t, _m in new), p1._btns(new[-1][2]) if new else []

    # invite flow offers + creates a shift-supervisor invite
    send(oid, "/reset")
    send(oid, kb.BTN_BOT_SETTINGS)
    send(oid, kb.BTN_USERS)
    _t, btns = send(oid, kb.BTN_USERS_ADD)
    assert kb.BTN_ROLE_SHIFT in btns, btns
    txt, _ = send(oid, kb.BTN_ROLE_SHIFT)
    assert "مسئول شیفت" in txt, txt
    txt, _ = send(oid, kb.BTN_INVITE_CONFIRM)
    import sqlite3 as _sq

    tok = _sq.connect(db.path).execute("SELECT token FROM invites ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    inv = db.get_invite(tok)
    assert inv and inv["role"] == "shift_supervisor" and "مسئول شیفت" in txt, inv
    # redeem → new user with role shift_supervisor and the field menu
    txt, btns = send("7701", "/start", f"/start {tok}")
    nu = db.get_user("7701")
    assert nu and nu["role"] == "shift_supervisor", (nu, txt)
    assert kb.BTN_SITE_STOCK in btns and kb.BTN_TR_MENU in btns, btns
    assert kb.BTN_ANALYTICS not in btns and kb.BTN_UPLOAD_MENU not in btns and kb.BTN_BOT_SETTINGS not in btns
    # role edit via /setrole and back
    send(oid, "/setrole", "/setrole 7701 technician")
    assert db.get_user("7701")["role"] == "technician"
    send(oid, "/setrole", "/setrole 7701 shift_supervisor")
    assert db.get_user("7701")["role"] == "shift_supervisor"
    # user list shows the Persian label
    send(oid, "/reset")
    send(oid, kb.BTN_BOT_SETTINGS)
    send(oid, kb.BTN_USERS)
    txt, _ = send(oid, kb.BTN_USERS_LIST)
    assert "مسئول شیفت" in txt, txt[:300]
    # reminders recipients include the role
    send(oid, "/reset")
    send(oid, kb.BTN_BOT_SETTINGS)
    send(oid, kb.BTN_SET_REMINDERS)
    _t, btns = send(oid, kb.BTN_RM_ROLES)
    assert any("مسئول شیفت" in b for b in btns), btns
    # denied features for the shift supervisor (handler gate, not only menu)
    for b in (kb.BTN_ANALYTICS, kb.BTN_DAILY, kb.BTN_UPLOAD_MENU, kb.BTN_INV_ADD_RECORD, kb.BTN_MATERIAL_REQUEST, kb.BTN_USERS):
        txt, _ = send("7701", b)
        assert "دسترسی ندارید" in txt, (b, txt)
    # help text
    h = help_text_for(nu)
    assert "مسئول شیفت" in h and "گزارش تاندیش" in h, h
    assert perm.can(nu, perm.SITE_STOCK) and perm.can(nu, perm.TUNDISH_REPORT) and perm.is_limited(nu)

    # web login with a real credential (username/password) → home shows only field links
    from fastapi.testclient import TestClient

    import web.deps as deps
    from web.app import create_app
    from web.auth_web import set_credential

    set_credential(db, bale_user_id="7701", username="shift7701", password="secret77")
    deps._db = db
    wapp = create_app()
    wapp.dependency_overrides[deps.get_db] = lambda: db
    c = TestClient(wapp)
    r = c.post("/login", data={"username": "shift7701", "password": "secret77"}, follow_redirects=False)
    assert r.status_code == 303, r.status_code
    r = c.get("/home")
    assert r.status_code == 200 and "مسئول شیفت" in r.text and 'href="/stock"' in r.text
    assert 'href="/reports"' not in r.text and 'href="/settings/permissions"' not in r.text
    assert c.get("/stock").status_code == 200
    assert c.get("/tundish-report").status_code == 200
    for url in ("/reports", "/reports/surplus", "/materials/request", "/settings/main-source", "/settings/permissions"):
        rr = c.get(url, follow_redirects=False)
        assert rr.status_code in (302, 303, 403), (url, rr.status_code)
    deps._db = None
    print("  17 shift_supervisor OK (invite, redeem, setrole, list, reminders, gates, help, web login)")


def test_custom_permissions(tmp: Path) -> None:
    """Item 18: toggles via bot inline callbacks + web page; enforcement + menu walk."""
    import logging

    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database
    from services import permissions as perm

    p1 = _helpers()
    db = Database(tmp / "perm.db")
    roles = {
        "951": "owner", "952": "manager", "953": "responsible_officer",
        "954": "technician", "955": "shift_supervisor",
    }
    for u, r in roles.items():
        db.upsert_user(u, role=r, display_name=r)
    client = p1.FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]
    edits: list = []
    answers: list = []
    client.edit_message_reply_markup = lambda chat, mid, markup=None: edits.append(markup) or {}
    client.answer_callback_query = lambda cid, text=None, show_alert=False: answers.append((text, show_alert)) or True

    def send(uid: str, text: str):
        n = len(client.sent)
        app.handle_message({"from": {"id": int(uid), "first_name": "x"}, "chat": {"id": int(uid)}, "text": text})
        new = client.sent[n:]
        return "\n".join(t for _c, t, _m in new), new

    def tap(uid: str, data: str):
        app.handle_callback_query({
            "id": "cb1", "data": data, "from": {"id": int(uid), "first_name": "x"},
            "message": {"message_id": 5, "chat": {"id": int(uid)}},
        })
        return answers[-1]

    # bot UI: manager opens «🔐 دسترسی نقش‌ها», picks technician → inline checklist
    send("952", "/reset")
    send("952", kb.BTN_BOT_SETTINGS)
    txt, new = send("952", kb.BTN_ROLE_PERMS)
    assert "دسترسی نقش‌ها" in txt and kb.role_perm_button("technician") in p1._btns(new[-1][2])
    assert kb.role_perm_button("owner") not in p1._btns(new[-1][2])
    txt, new = send("952", kb.role_perm_button("technician"))
    inline = new[-1][2]["inline_keyboard"]
    labels = [row[0]["text"] for row in inline]
    assert any(t.startswith(kb.RP_ON) and "موجودی روزانه سایت" in t for t in labels), labels
    assert any(t.startswith(kb.RP_OFF) and "گزارش مصرف روزانه" in t for t in labels), labels
    assert not any("کاربران" == t[2:] for t in labels)  # users is locked → not togglable

    # toggles (manager): technician += daily report + material request; officer -= edit/surplus
    assert tap("952", "rp|technician|report.daily")[1] is False
    assert tap("952", "rp|technician|material_request")[1] is False
    assert tap("952", "rp|responsible_officer|main_source_edit")[1] is False
    assert tap("952", "rp|responsible_officer|report.surplus")[1] is False
    assert tap("951", "rp|shift_supervisor|tundish_report")[1] is False  # owner may edit too
    assert edits and any("✅ گزارش مصرف روزانه" in r[0]["text"] for r in edits[0]["inline_keyboard"] if r)
    # locks
    text, alert = tap("952", "rp|owner|site_stock")
    assert alert and "مالک" in text
    text, alert = tap("952", "rp|manager|users")
    assert alert, text
    text, alert = tap("953", "rp|technician|reports")  # officer may not edit permissions
    assert alert, text
    assert "report.daily" in perm.role_features("technician")
    # activity log
    import sqlite3

    msgs = [r[0] for r in sqlite3.connect(db.path).execute("SELECT message_fa FROM user_activity").fetchall()]
    assert sum("دسترسی «" in m for m in msgs) >= 5, msgs[:6]

    # enforcement: menus show only permitted buttons, handlers check too
    tech = db.get_user("954"); off = db.get_user("953"); sh = db.get_user("955")
    m = p1._btns(kb.main_menu(tech))
    assert kb.BTN_ANALYTICS in m and kb.BTN_MATERIAL_REQUEST in m and kb.BTN_WAREHOUSE_RETURN not in m, m
    assert p1._btns(kb.analytics_menu(tech))[:1] == [kb.BTN_DAILY], p1._btns(kb.analytics_menu(tech))
    m = p1._btns(kb.main_source_file_menu(off))
    assert kb.BTN_INV_ADD_RECORD not in m and kb.BTN_INV_DOWNLOAD in m, m
    assert kb.BTN_SURPLUS not in p1._btns(kb.analytics_menu(off))
    assert kb.BTN_TR_MENU not in p1._btns(kb.main_menu(sh))
    for uid, b in (("953", kb.BTN_SURPLUS), ("953", kb.BTN_INV_ADD_RECORD), ("953", kb.BTN_FULL_REPLACE),
                   ("955", kb.BTN_TR_MENU), ("954", kb.BTN_SURPLUS), ("954", kb.BTN_WAREHOUSE_RETURN)):
        send(uid, "/reset")
        txt, _ = send(uid, b)
        assert "دسترسی ندارید" in txt, (uid, b, txt)
    send("954", "/reset")
    txt, _ = send("954", kb.BTN_ANALYTICS)
    assert "دسترسی ندارید" not in txt

    # menu walk with custom permissions (+ shift supervisor)
    catcher = p1.ErrCatcher()
    logging.getLogger().addHandler(catcher)
    for uid in ("953", "954", "955", "952"):
        p1.walk_role(app, client, catcher, uid, roles[uid] + "(custom)")
    logging.getLogger().removeHandler(catcher)

    # web: same overrides, page + save + reset, routes enforce
    from fastapi.testclient import TestClient

    import web.deps as deps
    from web.app import create_app

    deps._db = db
    wapp = create_app()
    wapp.dependency_overrides[deps.get_db] = lambda: db
    who = {"u": db.get_user("952")}
    wapp.dependency_overrides[deps.current_user_optional] = lambda: who["u"]
    c = TestClient(wapp)
    r = c.get("/settings/permissions?role=technician")
    assert r.status_code == 200 and "گزارش مصرف روزانه" in r.text and 'value="report.daily" checked' in r.text
    assert 'value="owner"' not in r.text
    # web save: technician keeps site stock + tundish, gets surplus report, loses daily/material request
    r = c.post("/settings/permissions", data={"role": "technician", "feature": ["site_stock", "tundish_report", "report.surplus"]})
    assert r.status_code == 200 and "ذخیره شد" in r.text, r.text[:400]
    feats = perm.role_features("technician")
    assert "report.surplus" in feats and "report.daily" not in feats and "material_request" not in feats
    r = c.post("/settings/permissions", data={"role": "owner", "feature": []})
    assert r.status_code == 400
    who["u"] = db.get_user("954")
    assert c.get("/reports").status_code == 200  # has a report now
    assert c.get("/reports/remaining-critical", follow_redirects=False).status_code in (302, 303, 403)
    assert c.get("/settings/permissions", follow_redirects=False).status_code in (302, 303, 403)
    who["u"] = db.get_user("953")
    assert c.post("/settings/main-source/add", data={}, follow_redirects=False).status_code in (302, 303, 403, 422)
    assert c.get("/reports/surplus", follow_redirects=False).status_code in (302, 303, 403)
    who["u"] = db.get_user("952")
    r = c.post("/settings/permissions/reset", data={"role": "technician"})
    assert r.status_code == 200 and "پیش‌فرض" in r.text
    assert perm.role_features("technician") == {"site_stock", "tundish_report"}, perm.role_features("technician")
    # bot reset via inline as well
    tap("952", "rp|responsible_officer|__reset__")
    assert "main_source_edit" in perm.role_features("responsible_officer")
    deps._db = None
    print("  18 role permissions OK (bot inline + web page, locks, activity log, menus + handlers + routes)")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke_p2_"))
    import config

    config.REPORT_DIR = tmp / "reports"  # type: ignore[attr-defined]
    print("smoke phase2…")
    try:
        test_shared_source(tmp)
        test_shift_role(tmp)
        test_custom_permissions(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(_GUARD_DIR, ignore_errors=True)
    print("SMOKE_PHASE2_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
