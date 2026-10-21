#!/usr/bin/env python3
"""Smoke (T14b, temp DB only): fresh install → upload «مصرف ماهیانه» (تیر–شهریور 1405)
through the real bot upload handler → «📅 گزارش مصرف بازه‌ای» + «۳ ماه اخیر» +
«همه بخش‌ها» must use the latest 3 months WITH data (not the calendar window that
includes an empty current month), read the uploaded monthly file, and state the basis.
Also: custom range with an empty month names it; presets resolve on data months."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    import jdatetime
    import pandas as pd

    from _smoke_env import SMOKE_TMP
    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database
    from services import data_months as dmo
    from services import permissions as perm

    src = SMOKE_TMP / "monthly_upload.xlsx"
    rows = []
    for m, q in ((4, 31), (5, 62), (6, 93)):
        rows.append({"domain": "خط-A", "tundish_type": "تاندیش اسلب", "assignee_id": 1001,
                     "assignee_name": "علی رضایی", "material_name": "M1 - جرم آزمون", "month": f"1405-{m:02d}",
                     "quantity": q, "unit": "KG", "status": "تأیید", "notes": None})
    pd.DataFrame(rows).to_excel(src, index=False)

    class C:
        def __init__(self):
            self.sent = []

        def send_message(self, chat, text, reply_markup=None, **_k):
            self.sent.append(("msg", text))
            return {"message_id": len(self.sent)}

        def send_document(self, chat, path, caption=None, **_k):
            self.sent.append(("doc", caption))
            return {}

        def download_file(self, file_id, dest):
            shutil.copy(src, dest)
            return dest

        def __getattr__(self, name):
            return lambda *a, **k: {}

    db = Database()
    perm.bind(db)
    db.upsert_user("5101", "owner", display_name="مالک آزمون")
    c = C()
    app = BotApp(c, db)
    base = {"from": {"id": 5101, "first_name": "x"}, "chat": {"id": 5101}}

    def send(text=None, doc=False):
        n = len(c.sent)
        m = dict(base)
        if doc:
            m["document"] = {"file_id": "f1", "file_name": "monthly.xlsx"}
        else:
            m["text"] = text
        app.handle_message(m)
        return c.sent[n:]

    send(kb.BTN_MONTHLY)
    send(doc=True)
    assert dmo.report_months(db, None, "period") == [(1405, 4), (1405, 5), (1405, 6)], dmo.report_months(db, None, "period")
    send(kb.BTN_PERIOD)
    send(kb.BTN_MY_3)
    out = send(kb.BTN_SEC_ALL)
    texts = "\n".join(str(t) for _k, t in out)
    assert any(k == "doc" for k, _t in out), texts
    assert "داده‌ای برای این گزارش نیست" not in texts and "دادهٔ کافی" not in texts, texts
    assert "مبنا: ۳ ماه تیر تا شهریور ۱۴۰۵" in texts, texts
    assert "(ج) فایل «مصرف ماهیانه»" in texts and "جرم آزمون: 186" in texts, texts

    # presets on data months (today 1405/10/05: مهر–آذر empty → still تیر–شهریور)
    t = jdatetime.date(1405, 10, 5)
    months = [(1405, 4), (1405, 5), (1405, 6)]
    assert dmo.resolve_preset_by_data("3m", months, t)[:2] == ((1405, 4), (1405, 6))
    assert dmo.resolve_preset_by_data("current", months, t)[:2] == ((1405, 6), (1405, 6))
    assert dmo.resolve_preset_by_data("ytd", months, t)[2] == months
    assert dmo.resolve_preset_by_data("3m", [], t) is None

    # custom range مرداد–مهر: مهر named as skipped, data still reported
    from services import period_consumption as pc

    s, e = jdatetime.date(1405, 5, 1).togregorian(), jdatetime.date(1405, 7, 30).togregorian()
    res = pc.build(db, s, e, range_label="مرداد تا مهر")
    assert not res.error and res.mon_months == [(1405, 5), (1405, 6)], res.error
    assert "بدون فایل مصرف ماهیانه" in res.source_line() and "مهر 1405" in res.source_line()
    print("smoke_t14b_period_presets OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
