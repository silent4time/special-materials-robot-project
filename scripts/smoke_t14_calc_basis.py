#!/usr/bin/env python3
"""Smoke (T14, temp DB only): calculation basis = months that ACTUALLY have data.

* monthly daily_rates: net (برگشتی −) ÷ days of the months present in the window;
  a month without an uploaded file is excluded (not a zero month); a month with
  data but not this item is a real zero; all tundish types of an item count.
* rates_last_complete_months: same rule + actual-basis label + fallback.
* basis_label_fa wording; stay-in-section after an action (task C).
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    import jdatetime
    import pandas as pd

    from analytics.tundish import basis_label_fa, daily_rates, rates_basis_label
    from services.consumption_rates import rates_last_complete_months

    def row(name, ym, q, rr="درخواستی", tt="slab"):
        return {"material_name": name, "tundish_type": tt, "unit": "NO", "quantity": q,
                "request_return": rr, "month": ym[1], "date": f"{ym[0]}{ym[1]:02d}05"}

    # only تیر/مرداد/شهریور 1405 uploaded; B is absent in مرداد (real zero there)
    df = pd.DataFrame([
        row("A", (1405, 4), 31), row("A", (1405, 5), 31), row("A", (1405, 6), 40),
        row("A", (1405, 6), 9, "برگشتی"), row("A", (1405, 6), 31, tt="billet"),
        row("B", (1405, 4), 93),
    ])
    g = lambda y, m, d: jdatetime.date(y, m, d).togregorian()  # noqa: E731
    # requested 12 months → only the 3 present months (93 days)
    r = daily_rates(None, df, start=g(1404, 7, 1), end=g(1405, 6, 31))
    a = r[r.material_name == "A"]
    assert abs(a["total_qty"].sum() - 124) < 1e-9, a  # 31+31+40−9+31 (billet row kept)
    assert set(a["days_span"]) == {93}, a
    assert abs(a["avg_daily"].sum() - 124 / 93) < 1e-9
    b = r[r.material_name == "B"]
    assert abs(float(b["avg_daily"].iloc[0]) - 1.0) < 1e-9  # 93 ÷ 93: مرداد/شهریور real zeros
    lab = rates_basis_label(r, "۱۲ ماه اخیر")
    assert lab.startswith("مبنا: ۳ ماه تیر تا شهریور ۱۴۰۵ (۹۳ روز"), lab
    assert "درخواست: ۱۲ ماه اخیر" in lab
    # no window → still months present only (never ÷30 for the whole file)
    r0 = daily_rates(None, df)
    assert set(r0["days_span"]) == {93}
    # gap months are named, not counted
    assert "تیر ۱۴۰۵، شهریور ۱۴۰۵" in basis_label_fa([(1405, 4), (1405, 6)])

    # request suggestions on 1405/08/10: window مرداد–مهر, مهر has no file → 62 days
    out, basis, w = rates_last_complete_months(None, df, today=jdatetime.date(1405, 8, 10))
    a = out[out.material_name == "A"]
    assert abs(a["avg_daily"].sum() - (31 + 40 - 9 + 31) / 62) < 1e-9, a
    assert "۲ ماه مرداد تا شهریور ۱۴۰۵" in basis, basis
    # 1405/11/05: window مهر–دی has no data → fallback to latest months with data
    out, basis, w = rates_last_complete_months(None, df, today=jdatetime.date(1405, 11, 5))
    assert not out.empty and "آخرین ماه‌های دارای داده" in basis, basis

    # ---- task C: stay in the section menu after an action ----
    from smoke_phase1 import FakeClient

    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database
    from services import permissions as perm

    db = Database()
    perm.bind(db)
    db.upsert_user("9914", "owner", display_name="مالک آزمون")
    client = FakeClient()
    app = BotApp(client, db)
    msg = {"from": {"id": 9914, "first_name": "x"}, "chat": {"id": 9914}, "text": "x"}
    user = db.get_user("9914")
    app.on_analytics_menu(msg)
    sec_btns = [b["text"] for rw in client.sent[-1][2]["keyboard"] for b in rw]
    app._last_in = {"9914": "some input"}
    app._reply(msg, "done", kb.main_menu(user))
    got = [b["text"] for rw in client.sent[-1][2]["keyboard"] for b in rw]
    assert got == sec_btns, "after an action the section menu must stay"
    app._last_in = {"9914": kb.BTN_HOME}
    app._reply(msg, kb.MAIN_MENU_TEXT, kb.main_menu(user))
    got = [b["text"] for rw in client.sent[-1][2]["keyboard"] for b in rw]
    assert got != sec_btns, "«منوی اصلی» must open the main menu"
    print("smoke_t14_calc_basis OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
