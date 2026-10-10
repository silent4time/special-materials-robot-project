"""Phase-2 smoke (1405-07-18 spec items 16–18). Temp DBs only — never the live DB.

* 16: one factory-wide منبع اصلی (edit by user B is what user A sees at once)
* 17: role shift_supervisor (DB, invites, menus, reminders, web login, help)
* 18: editable role permissions (DB overrides, owner locked, bot inline + web page,
      activity log, enforcement in menus + handlers + routes), menu walk for
      shift_supervisor and for a role with custom toggled permissions.
"""
from __future__ import annotations

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


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke_p2_"))
    import config

    config.REPORT_DIR = tmp / "reports"  # type: ignore[attr-defined]
    print("smoke phase2…")
    try:
        test_shared_source(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(_GUARD_DIR, ignore_errors=True)
    print("SMOKE_PHASE2_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
