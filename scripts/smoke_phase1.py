"""Phase-1 cleanup smoke (1405-07-18 spec).

* N-tundish report (service + bot flow incl. «اسلب ۴» shortcut + web)
* merged «📊 گزارش جامع» (aliases of the old labels) + section step
* rebuilt «📅 گزارش مصرف بازه‌ای» (site-stock diffs + main-goal months)
* removed buttons → one-line hint; legacy aliases → existing buttons
* menu walk: every button of every keyboard reachable from the main menu has a
  handler (never the generic fallback, never an exception) for
  owner / manager / responsible_officer / technician.

Runs on a temp copy of data/bot.db (never the live DB) + a fresh temp DB.
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402  — temp DB/reports/uploads before config import

import logging
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

import os

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# guard: anything that falls back to the default DB path gets a throwaway file
_GUARD_DIR = Path(tempfile.mkdtemp(prefix="smoke_p1_guard_"))
os.environ["DATABASE_PATH"] = str(_GUARD_DIR / "guard.db")

FALLBACK = "لطفاً از دکمه‌های منو استفاده کنید"


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict | None]] = []
        self.docs: list[tuple[str, str]] = []

    def send_message(self, chat_id, text, reply_markup=None, **_k):
        self.sent.append((str(chat_id), text, reply_markup))
        return {"message_id": len(self.sent)}

    def send_document(self, chat_id, path, caption=None, **_k):
        self.docs.append((str(chat_id), str(path)))
        return {}

    def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **_k):
        self.sent.append((str(chat_id), caption or "", reply_markup))
        return {}

    def __getattr__(self, name):  # any other API call → no-op
        return lambda *a, **k: {}


class ErrCatcher(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.records: list[str] = []

    def emit(self, record):
        self.records.append(self.format(record))


def _btns(markup) -> list[str]:
    return [b["text"] for row in (markup or {}).get("keyboard", []) for b in row]


def _db_copy(tmp: Path):
    from db.models import Database

    # file-level copy (db + wal + shm) — the live DB is never opened
    live = ROOT / "data" / "bot.db"
    n = len(list(tmp.glob("bot_copy*.db")))
    dst = tmp / f"bot_copy{n}.db"
    for suf in ("", "-wal", "-shm"):
        src = Path(str(live) + suf)
        if src.exists():
            shutil.copy2(src, Path(str(dst) + suf))
    return Database(dst)


def test_services(tmp: Path) -> None:
    from analytics.frames import load_primary_inventory
    from services import n_tundish_report as nt
    from services import period_consumption as pc

    db = _db_copy(tmp)
    # item 9 migration on the copy: work_order auto-assignments gone, site lists intact
    from services.site_stock_lists import items_for_group

    with db.connect() as conn:
        n_wo = conn.execute(
            "SELECT COUNT(*) FROM catalog_group_assignments WHERE assigned_by='system:work_order'"
        ).fetchone()[0]
        n_manual = conn.execute(
            "SELECT COUNT(*) FROM catalog_group_assignments WHERE assigned_by NOT LIKE 'system:%'"
        ).fetchone()[0]
    assert n_wo == 0, n_wo
    sizes = {g: len(items_for_group(db, g)) for g in ("slab", "bloom", "billet")}
    assert all(sizes.values()), sizes
    print(f"  migration OK work_order=0 manual={n_manual} site lists={sizes}")
    assert nt.parse_shortcut("اسلب ۴") == ("slab", 4)
    assert nt.parse_shortcut("بیلت 12") == ("billet", 12)
    assert nt.parse_count("۰") is None and nt.parse_count("abc") is None
    owner = {"bale_user_id": "1", "role": "owner", "active": 1, "display_name": "x"}
    inv = load_primary_inventory(db, owner)
    if inv is not None and not inv.empty:
        for sec in ("slab", "bloom", "billet"):
            for mode in ("with", "without"):
                r = nt.build_rows(inv, sec, 4, mode, report_date="1405/07/18")
                assert not r.error, r.error
                assert r.item_count() > 0, (sec, mode)
                assert "۴" in r.title or "4" in r.title, r.title
        r = nt.generate_files(db, owner, "slab", 4, "with", output_dir=tmp / "nt")
        assert not r.error and r.pdf.stat().st_size > 1000 and r.xlsx.stat().st_size > 500
        print(f"  n-tundish OK items={r.item_count()} short={r.shortage_count()}")
    else:
        print("  n-tundish: no main source in copy — service skipped")
    # period consumption
    assert pc.full_months_in_range(date(2026, 6, 22), date(2026, 8, 22)) == [(1405, 4), (1405, 5)]
    assert pc.full_months_in_range(date(2026, 6, 23), date(2026, 7, 10)) == []
    res = pc.generate_files(
        db, date(2026, 3, 21), date(2026, 10, 10), range_label="1405/01/01 تا 1405/07/18",
        output_dir=tmp / "pc",
    )
    assert res.pdf.stat().st_size > 1000 and res.xlsx.stat().st_size > 500
    assert res.source_line().startswith("منبع داده"), res.source_line()
    res_s = pc.build(db, date(2026, 3, 21), date(2026, 10, 10), range_label="x", section="slab")
    assert all(str(r.get("بخش")).startswith("اسلب") for r in res_s.site_rows + res_s.mg_rows), res_s.site_rows[:2]
    # synthetic site-stock diffs on the copy: 1.5 (base) → 1.0 → 3.0 → 2.5
    ent = db.list_site_stock_entries()
    if ent:
        e0 = ent[0]
        with db.connect() as conn:
            for d, q in (("2026-09-28", 1.0), ("2026-09-30", 3.0), ("2026-10-02", 2.5)):
                conn.execute(
                    "INSERT INTO site_stock_entries (bale_user_id, tundish_group, item_id, item_name_snapshot,"
                    " quantity, entry_date, created_at) VALUES (?,?,?,?,?,?,?)",
                    ("1", e0["tundish_group"], e0["item_id"], e0["item_name_snapshot"], q, d, "x"),
                )
        r2 = pc.build(db, date(2026, 9, 28), date(2026, 10, 5), range_label="x")
        row = next(r for r in r2.site_rows if r["شناسه کالا"] == e0["item_id"])
        assert row["مصرف (کاهش موجودی)"] == 1 and row["افزایش (ورودی)"] == 2, row
        assert row["تعداد ثبت در بازه"] == 3
        print("  period site-diff OK", row["مصرف (کاهش موجودی)"], row["افزایش (ورودی)"])
    print(f"  period OK site_rows={len(res.site_rows)} mg_rows={len(res.mg_rows)}")


def test_bot_reports(tmp: Path) -> None:
    from bot import keyboards as kb
    from bot.handlers import BotApp

    db = _db_copy(tmp)
    owner = next((u for u in db.list_users() if u.get("role") == "owner" and u.get("active")), None)
    assert owner, "no owner in live copy"
    uid = str(owner["bale_user_id"])
    client = FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]

    def send(text: str):
        app.handle_message({"from": {"id": int(uid), "first_name": "x"}, "chat": {"id": int(uid)}, "text": text})
        _c, txt, mk = client.sent[-1]
        return txt, _btns(mk)

    # N-tundish via shortcut
    txt, btns = send(kb.BTN_ANALYTICS)
    assert kb.BTN_N_TUNDISH in btns and kb.BTN_COMPREHENSIVE in btns
    assert "📄 PDF کامل تحلیل" not in btns and "📊 گزارش کلی مواد" not in btns
    send(kb.BTN_N_TUNDISH)
    txt, btns = send("اسلب ۴")
    assert kb.BTN_NT_WITH in btns, txt
    nd = len(client.docs)
    send(kb.BTN_NT_WITH)
    assert len(client.docs) == nd + 2, client.sent[-3:]
    # stepwise path
    send(kb.BTN_N_TUNDISH)
    send("بلوم")
    txt, _ = send("۰")
    assert "عدد صحیح" in txt
    send("۳")
    nd = len(client.docs)
    send(kb.BTN_NT_WITHOUT)
    assert len(client.docs) == nd + 2
    print("  bot n-tundish OK")

    # merged comprehensive: both old labels open the same prompt
    t1, b1 = send("📄 PDF کامل تحلیل")
    send(kb.BTN_HOME)
    t2, b2 = send("📊 گزارش کلی مواد")
    send(kb.BTN_HOME)
    t3, b3 = send(kb.BTN_COMPREHENSIVE)
    assert t1 == t2 == t3 and b1 == b3, (t1[:80], t3[:80])
    if kb.BTN_MY_CURRENT in b3:
        txt, btns = send(kb.BTN_MY_CURRENT)
        if kb.SECTION_STEP_BUTTONS and set(kb.SECTION_STEP_BUTTONS) & set(btns):
            nd = len(client.docs)
            send(next(iter(kb.SECTION_STEP_BUTTONS)))
            assert len(client.docs) > nd, client.sent[-2:]
    send(kb.BTN_HOME)
    print("  bot comprehensive (merged) OK")

    # period report (day range → section → files)
    txt, btns = send(kb.BTN_ANALYTICS)
    txt, btns = send(kb.BTN_PERIOD)
    assert btns, txt
    send(kb.BTN_HOME)
    print("  bot period prompt OK")


def test_aliases_and_removed(tmp: Path) -> None:
    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database

    db = Database(tmp / "alias.db")
    db.upsert_user("801", role="owner", display_name="مالک")
    client = FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]

    def send(text: str):
        app.handle_message({"from": {"id": 801, "first_name": "x"}, "chat": {"id": 801}, "text": text})
        return client.sent[-1][1]

    all_btn_values = {v for k, v in vars(kb).items() if k.startswith("BTN_") and isinstance(v, str)}
    for old, new in kb.LEGACY_ALIASES.items():
        assert new in all_btn_values, (old, new)
        assert kb.canonical(old) == new
    for old, hint in kb.REMOVED_HINTS.items():
        send(kb.BTN_HOME)
        txt = send(old)
        assert FALLBACK not in txt and txt, (old, txt)
        assert len(txt) < 400, (old, txt)
    # removed commands
    for cmd in ("/setscope x", "/assistant"):
        txt = send(cmd)
        assert "ناشناخته" in txt, txt
    print(f"  aliases {len(kb.LEGACY_ALIASES)} + removed hints {len(kb.REMOVED_HINTS)} OK")


def test_nav(tmp: Path) -> None:
    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database

    db = Database(tmp / "nav.db")
    db.upsert_user("811", role="owner", display_name="مالک")
    db.upsert_user("812", role="technician", display_name="ت")
    client = FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]

    def send(text: str, uid: int = 811):
        app.handle_message({"from": {"id": uid, "first_name": "x"}, "chat": {"id": uid}, "text": text})
        _c, txt, mk = client.sent[-1]
        return txt, _btns(mk)

    owner = db.get_user("811")
    settings = _btns(kb.bot_settings_menu(owner))
    main = _btns(kb.main_menu(owner))
    reports = _btns(kb.analytics_menu(owner))
    # main menu layout per spec (2 columns, fixed order)
    rows = [[b["text"] for b in r] for r in kb.main_menu(owner)["keyboard"]]
    assert rows[0] == [kb.BTN_SITE_STOCK, kb.BTN_TR_MENU], rows
    assert rows[-1] == [kb.BTN_BOT_SETTINGS, kb.BTN_HELP], rows
    # item 11: tundish-report settings only under settings, back → settings
    _t, trb = send(kb.BTN_TR_MENU)
    assert kb.BTN_TR_SETTINGS not in trb, trb
    send(kb.BTN_HOME)
    send(kb.BTN_BOT_SETTINGS)
    _t, b = send(kb.BTN_TR_SETTINGS)
    assert kb.BTN_BACK in b
    _t, b = send(kb.BTN_BACK)
    assert b == settings, b
    _t, b = send(kb.BTN_BACK)
    assert b == main, b
    # reports → critical → back → reports → back → main
    send(kb.BTN_ANALYTICS)
    send(kb.BTN_CRITICAL_ITEMS)
    _t, b = send(kb.BTN_BACK)
    assert b == reports, b
    # /reset == 🏠 full clear (n-tundish pending, analysis pending…)
    send(kb.BTN_ANALYTICS)
    send(kb.BTN_N_TUNDISH)
    assert app._n_tundish_pending
    txt, b = send("/reset")
    assert not app._n_tundish_pending and b == main, (txt, b)
    assert not app._has_pending("811")
    # ✖️ انصراف in a flow
    send(kb.BTN_N_TUNDISH)
    txt, b = send(kb.BTN_CANCEL)
    assert not app._n_tundish_pending
    # removed 🔄 شروع مجدد is not on any menu; alias → home
    assert "🔄 شروع مجدد" not in main and kb.canonical("🔄 شروع مجدد") == kb.BTN_HOME
    # technician: N-tundish denied
    txt, _ = send(kb.BTN_N_TUNDISH, uid=812)
    assert "دسترسی" in txt or "تکنسین" in txt, txt
    print("  nav (back one level, home, /reset, cancel, tr settings) OK")


DENIED_MARK = "دسترسی ندارید"


def walk_role(app, client, catcher, uid: str, role: str, max_depth: int = 4) -> int:
    """BFS over every keyboard reachable for ``uid``; every visible button must have a
    handler (no fallback, no logged error, no «دسترسی ندارید» for a shown button) and
    every sub-keyboard must offer back/home/cancel. Returns number of presses."""
    from bot import keyboards as kb

    def send(text: str):
        n = len(client.sent)
        app.handle_message({"from": {"id": int(uid), "first_name": "x"}, "chat": {"id": int(uid)}, "text": text})
        new = client.sent[n:]
        assert new, (role, text, "no reply")
        return [t for _c, t, _m in new], _btns(new[-1][2])

    def replay(path: tuple[str, ...]):
        app.handle_message({"from": {"id": int(uid), "first_name": "x"}, "chat": {"id": int(uid)}, "text": "/reset"})
        out = None
        for p in path:
            out = send(p)
        return out

    nav_bad: set = set()
    _t, root = send("/start")
    seen_kb: set[tuple[str, ...]] = set()
    queue: list[tuple[tuple[str, ...], list[str]]] = [((), root)]
    pressed = 0
    while queue:
        path, btns = queue.pop(0)
        key = tuple(btns)
        if key in seen_kb or len(path) > max_depth:
            continue
        seen_kb.add(key)
        for b in btns:
            if b in (kb.BTN_HOME,) and path:
                continue
            replay(path)
            n_err = len(catcher.records)
            texts, nb = send(b)
            pressed += 1
            assert all(FALLBACK not in t for t in texts), (role, path, b, texts)
            assert all(DENIED_MARK not in t for t in texts), (role, path, b, "shown but denied", texts)
            assert len(catcher.records) == n_err, (role, path, b, catcher.records[n_err:])
            if nb and nb != root:
                if not (nb[-1] in (kb.BTN_HOME, kb.BTN_CANCEL) or kb.BTN_CANCEL in nb or kb.BTN_BACK in nb):
                    nav_bad.add((b, tuple(nb[-2:])))
            if nb and tuple(nb) not in seen_kb:
                queue.append((path + (b,), nb))
    assert pressed > 3, (role, pressed)
    assert not nav_bad, (role, sorted(nav_bad))
    print(f"  menu walk {role}: {len(seen_kb)} keyboards, {pressed} presses OK")
    return pressed


def test_menu_walk(tmp: Path) -> None:
    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database

    db = Database(tmp / "walk.db")
    roles = {
        "901": "owner", "902": "manager", "903": "responsible_officer",
        "904": "technician", "906": "shift_supervisor",
    }
    for u, r in roles.items():
        db.upsert_user(u, role=r, display_name=r)
    client = FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]
    catcher = ErrCatcher()
    logging.getLogger().addHandler(catcher)
    for uid, role in roles.items():
        walk_role(app, client, catcher, uid, role)
    logging.getLogger().removeHandler(catcher)

    # role gates on main menu
    tech = _btns(kb.main_menu(db.get_user("904")))
    assert kb.BTN_ANALYTICS not in tech and kb.BTN_UPLOAD_MENU not in tech and kb.BTN_MAIN_GOAL not in tech
    assert kb.BTN_SITE_STOCK in tech
    off = _btns(kb.main_menu(db.get_user("903")))
    assert kb.BTN_BOT_SETTINGS not in off
    # every main-menu row ≤ 2 columns
    for u in roles:
        for row in kb.main_menu(db.get_user(u))["keyboard"]:
            assert len(row) <= 2, row


def test_web(tmp: Path) -> None:
    from fastapi.testclient import TestClient

    import web.deps as deps

    db = _db_copy(tmp)
    owner = next(u for u in db.list_users() if u.get("role") == "owner" and u.get("active"))
    db.upsert_user("905", role="technician", display_name="تکنسین")
    deps._db = db
    from web.app import create_app

    wapp = create_app()
    wapp.dependency_overrides[deps.get_db] = lambda: db
    who = {"u": owner}
    wapp.dependency_overrides[deps.current_user_optional] = lambda: who["u"]
    wapp.dependency_overrides[deps.current_user] = lambda: who["u"]
    c = TestClient(wapp)
    r = c.get("/reports")
    assert r.status_code == 200 and "نیاز مواد برای N تاندیش" in r.text and "گزارش مصرف بازه‌ای" in r.text
    assert "پوشش کوتاه‌مدت موجودی سایت" in r.text
    r = c.get("/reports/n-tundish?section=slab&n=4&renovation=with")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf"), r.status_code
    r = c.get("/reports/n-tundish.xlsx?section=bloom&n=2&renovation=without")
    assert r.status_code == 200 and "spreadsheet" in r.headers["content-type"]
    r = c.get("/reports/n-tundish?section=slab&n=0", follow_redirects=False)
    assert r.status_code == 303
    r = c.get("/reports/period?from_year=1405&from_month=4&to_year=1405&to_month=6&section=slab")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf")
    r = c.get("/reports/period.xlsx?from_year=1405&from_month=1&to_year=1405&to_month=7")
    assert r.status_code == 200
    who["u"] = db.get_user("905")
    r = c.get("/reports/n-tundish?section=slab&n=4", follow_redirects=False)
    assert r.status_code in (302, 303, 403), r.status_code
    print("  web n-tundish + period + reports page OK")


def test_qa_fixes(tmp: Path) -> None:
    """Live-QA pass 1405-07-19: period report runs end-to-end, Persian prompts/labels."""
    import re as _re

    from bot import keyboards as kb
    from bot.handlers import BotApp
    from services import inbound_report as inbound_svc, main_goal_history as mgh

    db = _db_copy(tmp)
    owner = next(u for u in db.list_users() if u.get("role") == "owner" and u.get("active"))
    uid = str(owner["bale_user_id"])
    client = FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]

    def send(text: str):
        n = len(client.sent)
        app.handle_message({"from": {"id": int(uid), "first_name": "x"}, "chat": {"id": int(uid)}, "text": text})
        new = client.sent[n:]
        return "\n".join(t for _c, t, _m in new), _btns(new[-1][2]) if new else []

    # 1: period report — section step must produce files or a Persian «what is missing» message
    for preset in (kb.BTN_MY_3, kb.BTN_MY_CURRENT):
        send("/reset")
        send(kb.BTN_ANALYTICS)
        txt, btns = send(kb.BTN_PERIOD)
        assert "Excel تاریخی" not in txt and kb.BTN_MY_3 in btns, txt
        send(preset)
        nd = len(client.docs)
        txt, _ = send(kb.BTN_SEC_ALL)
        assert "انجام نشد" not in txt and "کد پیگیری" not in txt, txt
        assert len(client.docs) == nd + 2 or "دادهٔ موجود" in txt, txt
    # 2: stock update prompt asks for the warehouse stock file (not منبع اصلی)
    send("/reset")
    send(kb.BTN_UPLOAD_MENU)
    txt, btns = send(kb.BTN_WAREHOUSE_STOCK)
    assert "موجودی انبار" in txt and "۱۸۰۰" in txt and "گزارش اقلام ورودی" in txt, txt
    assert "مربوط به «منبع اصلی»" not in txt and kb.BTN_FILE_GUIDE in btns
    send(kb.BTN_CANCEL)
    # 3: stock-group settings — no env names
    send("/reset")
    send(kb.BTN_BOT_SETTINGS)
    txt, _ = send(kb.BTN_SET_STOCK_GROUP)
    txt2, _ = send(kb.BTN_SETTINGS_VIEW)
    for t in (txt, txt2):
        assert "env" not in t and "SITE_STOCK_REPORT_GROUP_ID" not in t and " DB" not in t, t
    # 4/5: add-record prompt Persian label; add-category screen has nav row
    send("/reset")
    send(kb.BTN_UPLOAD_MENU)
    send(kb.BTN_MAIN_SOURCE_FILE)
    txt, _ = send(kb.BTN_INV_ADD_RECORD)
    assert "(category_code)" not in txt and "کد دسته ۴ رقمی" in txt, txt
    assert not _re.search(r"\([a-z_]+\)", txt), txt
    send(kb.BTN_CANCEL)
    txt, btns = send(kb.BTN_INV_ADD_CATEGORY)
    assert kb.BTN_BACK in btns and kb.BTN_HOME in btns and "بازگشت به منوی اصلی" not in txt, (txt, btns)
    send(kb.BTN_BACK)
    # 7: category list caption separates items from empty codes
    txt, _ = send(kb.BTN_INV_LIST_CATEGORIES)
    assert "قلم" in txt, txt
    # 6: inbound report — both dates labelled + Persian file names
    rep = inbound_svc.latest_report(db)
    if rep:
        summ = inbound_svc.summary_text_fa(rep)
        assert "تاریخ آپلود فعلی" in summ and ("تاریخ پایهٔ مقایسه" in summ or not rep.get("has_baseline")), summ
        pdf, xlsx = inbound_svc.build_report_files(db, rep, out_dir=tmp / "inb")
        assert pdf.name.startswith("گزارش_اقلام_ورودی_") and xlsx.suffix == ".xlsx", pdf.name
    # 8: warehouse-return review keyboard has standard nav
    btns = _btns(kb.warehouse_return_review_menu())
    assert kb.BTN_BACK in btns and kb.BTN_HOME in btns, btns
    # 9: never «— 0 تن» for a month without tonnage
    hist = mgh.history_overview_text(mgh.load_history(db))
    assert "— 0 تن" not in hist, hist
    # 10: role-permission callbacks bypass the per-user heavy-job queue
    app.bg.active[uid] = __import__("collections").deque()
    try:
        assert not app.bg.try_queue("", {})  # sanity
        n = len(app.bg.active[uid])
        app.handle_update({"update_id": 1, "callback_query": {
            "id": "1", "data": kb.CB_ROLE_PERM_PREFIX + "manager|bogus", "from": {"id": int(uid)},
            "message": {"message_id": 1, "chat": {"id": int(uid)}}}})
        assert len(app.bg.active[uid]) == n, "role-perm toggle was queued behind a heavy job"
    finally:
        app.bg.active.pop(uid, None)
    print("  QA fixes (period/stock prompt/env text/labels/nav/inbound/0 تن/perm queue) OK")


def test_qa2_fixes(tmp: Path) -> None:
    """Live-QA pass 2: forecast skips untonned months, settings nav, one-step back,
    Persian file names, unit display, reports header, stock-group wording."""
    from bot import keyboards as kb
    from bot.handlers import BotApp
    from services import main_goal_history as mgh
    from services.file_names import fa_stem
    from services.units import unit_fa

    db = _db_copy(tmp)
    mgr = next(
        (u for u in db.list_users() if u.get("role") in ("manager", "owner") and u.get("active")), None
    )
    assert mgr
    uid = str(mgr["bale_user_id"])
    client = FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]

    def send(text: str):
        n = len(client.sent)
        app.handle_message({"from": {"id": int(uid), "first_name": "x"}, "chat": {"id": int(uid)}, "text": text})
        new = client.sent[n:]
        return "\n".join(t for _c, t, _m in new), _btns(new[-1][2]) if new else []

    # BUG 1: scenario 2 averages only months that have tonnage and says which were used
    model = mgh.build_history_model(mgh.load_history(db), None)
    if model.tonnage_months:
        res = mgh.scenario_forecast(model, 3)
        assert res.ok and "ماه‌های استفاده‌شده برای تناژ" in res.summary, res.summary
        tot = sum(float(r["تناژ_پیش‌بینی_کل"]) for r in res.sections[0]["rows"][:-1])
        assert tot > 0, res.summary
        if model.skipped_tonnage_months:
            assert "کنار گذاشته (تناژ ریخته‌گری ثبت نشده)" in res.summary and "ماه با تناژ ریخته‌گری)" in res.summary
        assert mgh.file_stem_fa(res).startswith("هدف_اصلی_سناریو_۲_پیش‌بینی_۳_ماه")
    # BUG 2: user activity report keeps the ⚙️ تنظیمات keyboard
    send("/reset")
    send(kb.BTN_BOT_SETTINGS)
    send(kb.BTN_USER_ACTIVITY)
    _txt, btns = send(kb.BTN_MY_CURRENT)
    assert kb.BTN_USER_ACTIVITY in btns and kb.BTN_DAILY not in btns, btns
    # (a) one-step back: section step → range prompt; N-tundish count → section
    send("/reset")
    send(kb.BTN_ANALYTICS)
    send(kb.BTN_COMPREHENSIVE)
    send(kb.BTN_MY_3)
    _txt, btns = send(kb.BTN_BACK)
    assert kb.BTN_MY_3 in btns, btns
    send("/reset")
    send(kb.BTN_MAIN_GOAL)
    send(kb.BTN_MG_SCN_TARGET)
    send(kb.BTN_MG_P3)
    _txt, btns = send(kb.BTN_BACK)
    assert kb.BTN_MG_P3 in btns and kb.BTN_MG_SEC_BILLET not in btns, btns
    # (b) upload prompts carry back/home + help
    send("/reset")
    send(kb.BTN_UPLOAD_MENU)
    for b in (kb.BTN_WAREHOUSE_STOCK, kb.BTN_MONTHLY):
        _txt, btns = send(b)
        assert {kb.BTN_BACK, kb.BTN_HOME, kb.BTN_HELP, kb.BTN_CANCEL} <= set(btns), btns
        _txt, btns = send(kb.BTN_BACK)
        assert kb.BTN_WAREHOUSE_STOCK in btns, btns
    # (c) Persian names, (d) units
    assert fa_stem("گزارش جامع", "مهر 1405").startswith("گزارش_جامع_مهر_1405_")
    assert unit_fa("NO") == "عدد" and unit_fa("kg") == "کیلوگرم" and unit_fa("عدد") == "عدد"
    # (e) reports header fits the reports menu
    send("/reset")
    txt, _ = send(kb.BTN_ANALYTICS)
    assert "نوع ورود اطلاعات" not in txt and "استفاده کنید" not in txt, txt
    # (f) stock-group wording
    send("/reset")
    send(kb.BTN_BOT_SETTINGS)
    txt, _ = send(kb.BTN_SET_STOCK_GROUP)
    assert "شناسه منفی" not in txt, txt
    print("  QA-2 fixes (forecast basis/settings nav/step back/upload nav/names/units/header) OK")


def test_qa3_text(tmp: Path) -> None:
    """Guide-prep pass 1405-07-19: no internal names in web pages, units in bot text."""
    import re as _re

    import pandas as pd

    from services import n_tundish_report as ntr
    from services.units import unit_fa

    tpl = Path(__file__).resolve().parent.parent / "web" / "templates"
    for p in tpl.glob("*.html"):
        body = p.read_text(encoding="utf-8")
        for bad in ("Database.", "site_stock_entries", "users.role", "analytics.", "services.", "screenshot"):
            assert bad not in body, (p.name, bad)
    assert "labels.get(c, c)" in (tpl / "settings_main_source.html").read_text(encoding="utf-8")
    res = ntr.NTundishResult(section="bloom", n=2, reno_mode="with", report_date="1405/07/19")
    res.rows = [{ntr.COL_ROW: 1, ntr.COL_CODE: "1473", ntr.COL_KEYWORD: "فلت", ntr.COL_SUPPLIER: "پیمانکار",
                 ntr.COL_NEED: 300, ntr.COL_STOCK: 125, ntr.COL_SHORT: 175, ntr.COL_UNIT: "No"}]
    res.highlight_rows = [0]
    txt = res.bot_text()
    assert "175 عدد" in txt and not _re.search(r"\bNo\b", txt), txt
    assert unit_fa("Kg") == "کیلوگرم"
    print("  qa3 web text + N-tundish/critical units OK")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke_p1_"))
    import config

    config.REPORT_DIR = tmp / "reports"  # type: ignore[attr-defined]
    print("smoke phase1…")
    try:
        test_services(tmp)
        test_bot_reports(tmp)
        test_web(tmp)
        test_qa_fixes(tmp)
        test_qa2_fixes(tmp)
        test_qa3_text(tmp)
        test_aliases_and_removed(tmp)
        test_nav(tmp)
        test_menu_walk(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(_GUARD_DIR, ignore_errors=True)
    print("SMOKE_PHASE1_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
