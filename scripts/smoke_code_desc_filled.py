"""Smoke (v1.3.0, no DB writes outside temp): code + description never blank in material reports.

• inbound: stock row matched only by ID (blank «کد دسته») takes the code from منبع اصلی;
  «شرح دسته» falls back to the code's keywords (new and older stored reports); no «nan»;
• critical: _pick_description never returns «nan» / blank when any group row has a name;
• stock merge fills a blank code / name / unit of an existing ID (never overwrites);
• rates import: an unknown ID without code is NOT matched by rid[:4];
• monthly summary (real sample): subtotal rows carry the group's code / ID."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    import pandas as pd

    from analytics.critical_items import _pick_description
    from services import inbound_report as ir
    from services import main_source as ms
    from services import rates_import as ri

    live = pd.DataFrame([
        {"id": "378121641252L", "category_code": "1203", "quantity": 10, "product_name": "بتن 85 درصد",
         "keyword": "بتن 85bt", "unit": "Kg"},
        {"id": "378700009002G", "category_code": "1203", "quantity": 5, "product_name": "بتن پلی",
         "keyword": "بتن پلی 85", "unit": "Kg"},
    ])
    base = live[["id", "category_code", "quantity"]].copy()
    up = pd.DataFrame([
        {"id": "378121641252L", "category_code": "1203", "quantity": 30},
        {"id": "378700009002G", "category_code": None, "quantity": 9},   # merged/blank code
    ])
    res = ir.compute_inbound(base, up, live_before=live, live_after=live, category_labels={"1203": None})
    assert len(res.lines) == 2, res.lines
    for ln in res.lines:
        assert ln[ir.COL_CODE] == "1203", ln
        assert ln[ir.COL_KEYWORD] not in ("", "—", "nan"), ln
        assert "nan" not in str(ln.values()), ln
    assert res.summary and res.summary[0]["شرح دسته"] == "بتن 85bt، بتن پلی 85", res.summary
    old = {"lines": res.lines, "summary": [dict(res.summary[0], **{"شرح دسته": ""})]}
    assert ir._summary_rows_with_ids(old)[0]["شرح دسته"] not in ("", "—")

    g = pd.DataFrame([
        {"keyword": float("nan"), "product_name": float("nan"), "unit": float("nan"), "priority": 1, "billet_rate": 1},
        {"keyword": "فلت", "product_name": "فلت تاندیش", "unit": "NO", "priority": 2, "billet_rate": 0},
    ])
    desc, unit = _pick_description(g)
    assert desc == "فلت" and unit == "NO", (desc, unit)

    o = pd.DataFrame([{"id": "X1", "category_code": "", "quantity": 1, "product_name": "", "unit": ""}])
    n = pd.DataFrame([{"id": "X1", "category_code": "1473", "quantity": 4, "product_name": "فلت", "unit": "NO"}])
    merged, _ = ms.merge_stock_update(ms.ensure_inventory_columns(o), ms.ensure_inventory_columns(n))
    r = merged.iloc[0]
    assert str(r["category_code"]) == "1473" and r["product_name"] == "فلت" and r["unit"] == "NO", r.to_dict()

    src = ms.ensure_inventory_columns(pd.DataFrame([{"id": "378121641252L", "category_code": "3781", "quantity": 1}]))
    rate_col = ri.RATE_COLS[0]
    inc = pd.DataFrame([{"id": "378199999999Z", "category_code": None, rate_col: 2.5}])
    plan = ri.build_plan(src, inc)
    assert not getattr(plan, "changes", None) and plan.unmatched, vars(plan)

    from excel import monthly_summary as msum
    sample = ROOT / "samples/real/monthly_consumption_sample.xlsx"
    if sample.exists():
        data = msum.aggregate_monthly_detail(msum.load_monthly_detail(sample))
        subs = [r for r in msum.build_summary_sheet_rows(data) if r.kind == "subtotal"]
        assert subs, "no subtotal rows"
        blank = [r.values for r in subs if not r.values[0] or not r.values[1]]
        assert not blank, blank[:3]
    print("smoke_code_desc_filled: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
