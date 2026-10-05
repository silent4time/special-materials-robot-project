#!/usr/bin/env python3
"""Seed Tir/Mordad/Shahrivar 1405 furnace production + available tundish sequences into bot.db.

Copies attachments into uploads/main_goal_history (gitignored) — does not commit JPGs.
Uses known KPI ground truth (OCR may be noisy on Persian dashboards); stores OCR raw for audit.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import UPLOAD_DIR, ensure_dirs
from db.models import Database
from services import main_goal_persist as mgp
from services import main_goal_production_ocr as mgocr
from services import main_goal_sequences as seq

ATTACH = Path("/home/box/agent-data/agents/8068822b-8774-4875-8e71-86ecdb5001a4/attachments")

PROD = [
    {
        "jpg": ATTACH / "97a0ba5b628d1b077180d9b8010a49509a16145fe04be928fd5cf318f950a8ad.jpg",
        "year": 1405, "month": 4,
        "melts": 750, "melt_kg": 127705938, "product_kg": 123513760,
        "slab_c": 423, "bb_c": 327, "mpd": 24,
    },
    {
        "jpg": ATTACH / "c7bcc51239d04d96bb4065ddd29c1d561ed5d5cf2c505359eb72a8ecf8a65421.jpg",
        "year": 1405, "month": 5,
        "melts": 682, "melt_kg": 115489566, "product_kg": 112268212,
        "slab_c": 411, "bb_c": 273, "mpd": 22,
    },
    {
        "jpg": ATTACH / "1b9ae892cd8ad2cb7ec065dcf9a12f20b4cae21faa5382a317863d7234628be4.jpg",
        "year": 1405, "month": 6,
        "melts": 930, "melt_kg": 156058238, "product_kg": 150310814,
        "slab_c": 610, "bb_c": 320, "mpd": 30,
    },
]

SEQ_FILES = [
    (ATTACH / "ebca078ffd6437c5ca0ed353735990a7526cb4e4f09a505d1c248c4f92fc5601.xlsx", "slab"),
    (ATTACH / "894f832b7513acaeba98adaa542555e6a8b80785ec1c87a0210d40d934beeaf6.xlsx", "billet"),
    (ATTACH / "b395f45cab9efee787b9dc4df1c170b0133c9dfdf698e464210a886681a60744.xlsx", "bloom"),
]


def main() -> int:
    ensure_dirs()
    db = Database()
    # pick an owner-like user if present
    users = db.list_users(active_only=True)
    user = None
    for u in users:
        if u.get("role") in {"owner", "manager", "responsible_officer"}:
            user = u
            break
    if not user:
        user = {"bale_user_id": "seed", "display_name": "seed", "role": "owner", "active": 1}
        try:
            db.upsert_user("seed", "owner", display_name="seed")
            user = db.get_user("seed") or user
        except Exception:
            pass

    print("Seeding as", user.get("bale_user_id"), user.get("role"))
    for item in PROD:
        jpg = item["jpg"]
        if not jpg.is_file():
            print("MISSING", jpg)
            continue
        dest_dir = UPLOAD_DIR / "main_goal_history" / f"m_{item['year']:04d}-{item['month']:02d}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "furnace_production.jpg"
        shutil.copy2(jpg, dest)
        # OCR for audit (may be weak on Persian UI)
        try:
            ocr_res = mgocr.ocr_production_image(dest)
            raw = ocr_res.ocr_raw_text
            conf = ocr_res.ocr_confidence
            print(f"OCR {item['month']}: ok={ocr_res.ok} conf={conf} period={ocr_res.period_label}")
        except Exception as exc:
            raw, conf = f"OCR error: {exc}", None
            print("OCR failed", exc)
        known = mgocr.from_known_furnace(
            year=item["year"], month=item["month"],
            melts=item["melts"], melt_weight_kg=item["melt_kg"],
            product_weight_kg=item["product_kg"],
            slab_count=item["slab_c"], bloom_billet_count=item["bb_c"],
            melts_per_day=item["mpd"],
            ocr_raw_text=raw or "", ocr_confidence=conf,
        )
        known.notes.append("seed: ground-truth KPIs from furnace screenshot (OCR stored for audit)")
        out = mgp.store_production_from_ocr(
            db, dest, user=user, source="seed", filename=dest.name,
            ocr_result=known, manual_corrected=True,
        )
        print(out.summary)
        print("  missing:", out.missing_parts)

    for path, section in SEQ_FILES:
        if not path.is_file():
            print("MISSING seq", path)
            continue
        out = mgp.store_sequences_from_excel(
            db, path, user=user, source="seed", filename=path.name, section=section
        )
        print(out.summary if out.ok else out.error_fa)
        print("  missing:", out.missing_parts)

    months = mgp.load_history_from_db(db)
    print("History months:", len(months), [m.label for m in months])
    from services.main_goal_history import consecutive_month_gap_warning, history_count_line
    print(history_count_line(len(months), gap_warning=consecutive_month_gap_warning(months)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
