#!/usr/bin/env python3
"""Smoke: normalized DB schema, furnace seed-shaped store, sequences, range report, gap warning."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook

from db.models import Database
from services import main_goal_history as mgh
from services import main_goal_persist as mgp
from services import main_goal_production_ocr as mgocr
from services import main_goal_sequences as seq


def write_seq(path: Path, section_fa: str, year: int, month: int, n: int = 3) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["ماشین", "تاندیش", "تاندیشکار", "تعداد ذوب", "مدت سکوئنس", "ISG",
               "ذوب اول سکوئنس", None, "ذوب آخر سکوئنس", None, "تعویض شرود", "تعویض نازل بیرونی"])
    ws.append([None, None, None, None, None, None, "شماره ذوب", "شروع ریخته گری", "شماره ذوب", "پایان ریخته گری", None, None])
    for i in range(n):
        ws.append([
            f"{section_fa} 1", 10 + i, 1, 5 + i, 100 + i, "1111",
            50000000 + i, f"{year}/{month:02d}/{10+i} 01:00",
            50000010 + i, f"{year}/{month:02d}/{10+i} 05:00",
            "ندارد", "دارد",
        ])
    wb.save(path)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "smoke.db"
        db = Database(str(db_path))
        user = {"bale_user_id": "1", "display_name": "smoke", "role": "owner", "active": 1}
        db.upsert_user(1, "owner", display_name="smoke")

        # furnace known values for 3 continuous months
        for month, melts, pkg, sc, bbc in [
            (4, 750, 123513760, 423, 327),
            (5, 682, 112268212, 411, 273),
            (6, 930, 150310814, 610, 320),
        ]:
            known = mgocr.from_known_furnace(
                year=1405, month=month, melts=melts,
                melt_weight_kg=pkg * 1.03, product_weight_kg=pkg,
                slab_count=sc, bloom_billet_count=bbc, melts_per_day=24,
            )
            placeholder = Path(tmp) / f"p{month}.txt"
            placeholder.write_text("seed", encoding="utf-8")
            out = mgp.store_production_from_ocr(
                db, placeholder, user=user, source="smoke",
                ocr_result=known, manual_corrected=True,
            )
            assert out.ok, out.error_fa

        # gap warning should be None for continuous 4,5,6
        months = mgp.load_history_from_db(db)
        assert len(months) == 3
        assert mgh.consecutive_month_gap_warning(months) is None

        # introduce gap by adding month 8 only via production
        known = mgocr.from_known_furnace(
            year=1405, month=8, melts=100, melt_weight_kg=1e7, product_weight_kg=9e6,
            slab_count=50, bloom_billet_count=50,
        )
        placeholder = Path(tmp) / "p8.txt"
        placeholder.write_text("x", encoding="utf-8")
        mgp.store_production_from_ocr(db, placeholder, user=user, source="smoke", ocr_result=known, manual_corrected=True)
        months = mgp.load_history_from_db(db)
        gap = mgh.consecutive_month_gap_warning(months)
        assert gap and ("مهر" in gap or "1405" in gap), gap
        print("gap ok:", gap)

        # sequences
        sp = Path(tmp) / "slab_seq.xlsx"
        write_seq(sp, "اسلب", 1405, 6, n=5)
        parsed = seq.parse_sequence_excel(sp)
        assert parsed.ok and parsed.section == "slab" and parsed.tundish_count == 5, parsed
        out = mgp.store_sequences_from_excel(db, sp, user=user, source="smoke", section="slab")
        assert out.ok, out.error_fa

        # range report last 3
        spec = mgp.range_last_n(3)
        model, result, warns = mgp.build_range_report(db, spec)
        assert result.ok and model.n_months >= 1, result.error_fa
        print("range summary:\n", result.summary[:400])

        # inventory add duplicate
        from services import main_source as ms
        # skip if no inventory — just import check
        assert hasattr(ms.add_row, "__call__")

        # OCR text parse smoke with latin digits furnace-like
        sample = "1405/06/01 تا 1405/06/31\nتعداد 930\nوزن مذاب 156058238\nوزن محصول 150310814\nبه اسلب 610\nبلوم بیلت 320\nذوب در روز 30"
        r = mgocr.parse_furnace_ocr_text(sample)
        assert r.ok and r.month == 6 and r.product_weight_kg == 150310814, (r.error_fa, r)
        assert r.melt_count == 930, (r.melt_count, r.notes)
        print("furnace text OCR parse ok", r.period_label, r.total_tons)

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
