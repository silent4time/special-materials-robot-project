#!/usr/bin/env python3
"""Seed the «گزارش اقلام ورودی به انبار» baseline from past stock uploads (t221u).

Idempotent: does nothing when ``inventory_stock_uploads`` already has rows (use
--force to add anyway). Default = this deployment's history:

* extract 11 (1405/07/05, «📥 موجودی انبار») → frozen as the comparison base
  for the 07/06 report only (its stored clean snapshot = منبع اصلی then);
* extract 12 (1405/07/06 11:13, warehouse stock file) → re-extracted from its raw
  file, rejected rows by today's rule (7b786cd/1da0045, stock-update), report
  computed vs 07/05 and stored; it becomes THE baseline for the next stock upload.

Usage: .venv/bin/python scripts/seed_inbound_baseline.py [--db PATH] [--prev 11] [--current 12]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--prev", type=int, default=11)
    ap.add_argument("--current", type=int, default=12)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    import pandas as pd

    from db.models import Database
    from excel.processor import extract_and_save_clean
    from services import inbound_report as svc
    from services import main_source as ms

    db = Database(args.db) if args.db else Database()
    if db.count_stock_uploads() and not args.force:
        print("inventory_stock_uploads already seeded — nothing to do")
        return 0

    def extract(eid: int) -> dict:
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM extracted_datasets WHERE id = ?", (eid,)).fetchone()
        if not row:
            raise SystemExit(f"extract {eid} not found")
        return dict(row)

    prev, cur = extract(args.prev), extract(args.current)
    user = db.get_user(cur["bale_user_id"]) or {}
    actor = user.get("display_name") or None

    prev_df = pd.read_excel(prev["clean_path"], engine="openpyxl")
    svc.freeze_stock_snapshot(
        db,
        prev_df,
        bale_user_id=prev["bale_user_id"],
        actor_display_name=actor,
        extract_id=int(prev["id"]),
        raw_path=prev.get("raw_path"),
        note="seed t221u: آپلود موجودی ۱۴۰۵/۰۷/۰۵ — فقط پایه گزارش آپلود ۰۷/۰۶",
        created_at=prev["created_at"],
    )

    allow = db.active_category_code_set()
    with tempfile.TemporaryDirectory() as tmp:
        res = extract_and_save_clean(
            cur["raw_path"], "product_inventory", clean_dir=Path(tmp), category_allowlist=allow
        )
        uploaded = pd.read_excel(res.clean_path, engine="openpyxl")
    _kept, rejected = ms.filter_inventory_upload(prev_df, uploaded, allow_new_codes=False)
    live_after = ms.load_primary_frame(db)
    report = svc.record_stock_upload(
        db,
        uploaded=uploaded,
        live_before=prev_df,
        live_after=live_after,
        rejected=rejected,
        bale_user_id=cur["bale_user_id"],
        actor_display_name=actor,
        extract_id=int(cur["id"]),
        raw_path=cur.get("raw_path"),
        created_at=cur["created_at"],
        note="seed t221u: فایل موجودی انبار ۱۴۰۵/۰۷/۰۶ — اولین پایه",
    )
    print(svc.summary_text_fa(report))
    print(
        f"report #{report['id']}: increase={report['n_increase']} new_id={report['n_new_id']} "
        f"zero_new={report['n_zero_new']} excluded_1800={report['n_excluded_1800']} "
        f"rejected={report['n_rejected']} baseline_upload={report['baseline_upload_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
