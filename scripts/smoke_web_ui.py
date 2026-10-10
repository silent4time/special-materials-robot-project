#!/usr/bin/env python3
"""Smoke: web UI polish (1405-07-19) — temp DB only, never the live DB.

Vazirmatn everywhere (no monospace), Persian file picker, visible «خروج», /stock
Jalali date + category column (no raw SS: ids), Jalali dates on /reports, internal
tags stripped from notes, inline delete in tundish settings, materials columns from
منبع اصلی, no developer notes / legacy 4-file upload on main goal, reminders grid.
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402  — temp DB/reports/uploads before config import

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def visible(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def main() -> int:
    from fastapi.testclient import TestClient

    import web.deps as deps
    from db.models import Database
    from web.app import clean_note, create_app, jdt
    from web.auth_web import set_credential

    # filters
    note = "نمونه شهریور 1405 — مبنا = لاگ توالی تاندیش (t211u، 1405/07/14)"
    assert clean_note(note).endswith("(1405/07/14)") and "t211u" not in clean_note(note), clean_note(note)
    assert jdt("2026-10-05T07:52:54+00:00") == "1405/07/13 11:22", jdt("2026-10-05T07:52:54+00:00")

    css = (ROOT / "web" / "static" / "style.css").read_text(encoding="utf-8")
    assert "monospace" not in css.replace("monospace breaks", "") and "@font-face" in css and "Vazirmatn" in css
    assert re.search(r"pre, code, kbd, samp", css) and ".filepick" in css and ".kv-grid" in css

    deps.reset_db_singleton()
    db = Database()
    db.upsert_user("8801", "owner", display_name="مالک آزمون")
    set_credential(db, bale_user_id="8801", username="owner8801", password="secret88")
    deps._db = db
    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: db
    c = TestClient(app)
    assert c.get("/fonts/Vazirmatn-Regular.ttf").status_code == 200
    r = c.post("/login", data={"username": "owner8801", "password": "secret88"}, follow_redirects=False)
    assert r.status_code == 303, r.status_code

    home = c.get("/home").text
    chip = home[home.index('class="user-chip"'):]
    assert 'action="/logout"' in chip[:600] and "خروج" in chip[:600], "logout not in header chip"
    assert "انتخاب فایل" in home and "فایلی انتخاب نشده" in home  # picker script in base

    st = c.get("/stock", params={"group": "bloom", "entry_date": "1405/07/10"})
    assert st.status_code == 200 and 'value="1405/07/10"' in st.text and 'type="date"' not in st.text, st.text[:400]
    assert "تاریخ موجودی (شمسی)" in st.text
    bad = c.get("/stock", params={"entry_date": "abc"})
    assert bad.status_code == 200 and "تاریخ نامعتبر" in bad.text
    legacy = c.get("/stock", params={"entry_date": "2026-10-02"})
    assert 'value="1405/07/10"' in legacy.text

    rep = c.get("/reports")
    assert rep.status_code == 200
    assert not re.search(r"20\d\d-\d\d-\d\d", visible(rep.text)), "Gregorian date on /reports"

    mg = c.get("/reports/main-goal").text
    assert "اعتبارسنجی نشده" not in mg and "۴ فایل" not in mg and "CCM1/2" not in mg

    rem = c.get("/settings/reminders").text
    assert 'class="kv-grid"' in rem

    ts = c.get("/tundish-report/settings").text
    assert "colspan=\"6\" style=\"text-align:left\"" not in ts
    if "/delete" in ts:
        assert 'class="actions"' in ts

    for path in ("/materials/request", "/materials/return"):
        t = c.get(path).text
        assert "محل استفاده" in t and "کلید واژه" in t and "<th>گروه</th>" not in t, path

    for path in ("/home", "/stock", "/reports", "/reports/main-goal", "/tundish-report",
                 "/tundish-report/settings", "/materials/request", "/materials/return",
                 "/settings/reminders", "/settings/main-source"):
        t = c.get(path).text
        assert "monospace" not in t, path
        assert not re.search(r"\bSS:[a-z]+:\d{4}:", visible(t)), path
    print("SMOKE_WEB_UI_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
