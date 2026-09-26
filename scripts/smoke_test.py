#!/usr/bin/env python3
"""Offline smoke test: RBAC filter + PDF generation without Bale network."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.models import Database
from excel.processor import process_session_files
from pdf.generator import generate_report
from scripts.make_samples import main as make_samples


def main() -> int:
    make_samples()
    db_path = ROOT / "data" / "smoke.db"
    if db_path.exists():
        db_path.unlink()
    db = Database(db_path)
    db.upsert_user("999", role="manager", display_name="مدیر تست")
    db.upsert_user("1001", role="technician", display_name="علی رضایی", scope="خط-A")
    db.upsert_user("1002", role="responsible_officer", display_name="مریم احمدی", scope="خط-B")

    paths = {
        "tank_consumption": str(ROOT / "samples" / "01_tank_consumption.xlsx"),
        "product_inventory": str(ROOT / "samples" / "02_product_inventory.xlsx"),
        "monthly_consumption": str(ROOT / "samples" / "03_monthly_consumption.xlsx"),
    }

    for uid, label in [("999", "manager"), ("1002", "officer"), ("1001", "tech")]:
        user = db.get_user(uid)
        frames, metas = process_session_files(paths, user)
        out = ROOT / "reports" / f"smoke_{label}.pdf"
        generate_report(frames, metas, user, out)
        visible = {k: metas[k]["visible_rows"] for k in metas}
        print(label, "visible", visible, "->", out)
        assert out.exists() and out.stat().st_size > 1000

    # technician must see fewer tank rows than manager
    mgr = db.get_user("999")
    tech = db.get_user("1001")
    mf, mm = process_session_files(paths, mgr)
    tf, tm = process_session_files(paths, tech)
    assert tm["tank_consumption"]["visible_rows"] < mm["tank_consumption"]["visible_rows"]
    assert tm["tank_consumption"]["visible_rows"] == 2  # علی رضایی rows
    off = db.get_user("1002")
    of, om = process_session_files(paths, off)
    assert om["tank_consumption"]["visible_rows"] == 2  # خط-B
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
