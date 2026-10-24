"""Regression: «ماه جاری» must be the CALENDAR Jalali month, never «latest month with data».

Bug (1405/08): daily report + «ماه جاری» in Mehr returned Shahrivar. Frozen today
1405/07/29 (Mehr) and 1405/08/02 (Aban): the bot must either build the calendar month or
say it has no data and OFFER the latest month with data (inline button) — never silently
substitute it. Web daily (period=current) behaves the same.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _smoke_env  # noqa: F401,E402
import jdatetime  # noqa: E402

import bot.jalali as bj  # noqa: E402
import services.period_select as ps  # noqa: E402

_TODAY = [jdatetime.date(1405, 7, 29)]
bj.jalali_today = lambda: _TODAY[0]
ps.today = lambda: _TODAY[0]

import bot.handlers as H  # noqa: E402
from bot import keyboards as kb  # noqa: E402
from bot.handlers import BotApp  # noqa: E402
from services import data_months as dmo  # noqa: E402
from smoke_phase1 import FakeClient, _db_copy  # noqa: E402

if hasattr(H, "jalali_today"):
    H.jalali_today = lambda: _TODAY[0]


def main() -> None:
    db = _db_copy(_smoke_env.SMOKE_TMP)
    owner = next((u for u in db.list_users() if u.get("role") == "owner"), None) if hasattr(db, "list_users") else None
    uid = int((owner or {}).get("bale_user_id") or 1644670601)
    c = FakeClient()
    app = BotApp(c, db)

    def say(t: str) -> list:
        n = len(c.sent)
        app.handle_message({"message_id": 1, "chat": {"id": uid}, "from": {"id": uid, "first_name": "x"}, "text": t})
        return c.sent[n:]

    for today, name in ((jdatetime.date(1405, 7, 29), "مهر ۱۴۰۵"), (jdatetime.date(1405, 8, 2), "آبان ۱۴۰۵")):
        _TODAY[0] = today
        cur = dmo.resolve_current_month(db, None, "daily")
        assert cur["ym"] == (today.year, today.month), cur
        say(kb.BTN_DAILY)
        out = say(kb.BTN_MY_CURRENT)
        nd = len(c.docs)
        texts = " ".join(s[1] for s in out)
        if cur["has_data"]:
            out += say(kb.BTN_SEC_ALL)
            assert len(c.docs) > nd, "calendar month with data must build the report"
        else:
            assert f"ماه جاری ({name})" in texts and "هنوز داده‌ای ثبت نشده" in texts, texts
            ik = [b for s in out for row in ((s[2] or {}).get("inline_keyboard") or []) for b in row]
            assert ik and ik[0]["callback_data"].startswith(kb.CB_MY_LATEST_PREFIX), out
            assert len(c.docs) == nd, "must NOT silently build another month"
            # user taps the offer → latest month with data, labelled as such
            app.handle_callback_query({"id": "1", "data": ik[0]["callback_data"], "from": {"id": uid},
                                       "message": {"chat": {"id": uid}, "message_id": 2}})
            for t in say(kb.BTN_SEC_ALL) if not len(c.docs) > nd else []:
                pass
            assert len(c.docs) > nd, "offer button must build the latest-data month"
    print("bot OK")

    # web daily period=current
    from fastapi.testclient import TestClient

    from web.app import create_app
    from web.auth_web import set_credential
    from web.deps import get_db

    _TODAY[0] = jdatetime.date(1405, 8, 2)
    wapp = create_app()
    wapp.dependency_overrides[get_db] = lambda: db
    db.upsert_user("9901", "owner", display_name="o")
    set_credential(db, bale_user_id="9901", username="cm9901", password="secret1234")
    w = TestClient(wapp)
    assert w.post("/login", data={"username": "cm9901", "password": "secret1234"}, follow_redirects=False).status_code in (302, 303)
    r = w.get("/reports/daily?period=current&section=", follow_redirects=False)
    cur = dmo.resolve_current_month(db, None, "daily")
    if cur["has_data"]:
        assert r.status_code == 200 and r.content[:4] == b"%PDF", r.status_code
    else:
        assert r.status_code == 303 and "daily_latest" in r.headers["location"], r.headers
        page = w.get(r.headers["location"]).text
        assert "آبان" in page and "گزارش آخرین ماه دارای داده" in page, page[-500:]
        y, m = cur["latest"]
        r2 = w.get(f"/reports/daily?period=month&y={y}&m={m}&latest=1")
        assert r2.status_code == 200 and r2.content[:4] == b"%PDF", r2.status_code
    print("web OK")
    print("smoke_current_month OK")


if __name__ == "__main__":
    main()
