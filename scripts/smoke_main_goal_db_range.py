#!/usr/bin/env python3
"""Smoke: normalized DB schema, casting-tab store (furnace refused), sequences, range report, gap warning."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import _smoke_isolation  # noqa: E402 - real uploads/ stays untouched

_smoke_isolation.isolate_uploads()

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

        # furnace-tab values are refused (casting tab only)
        fk = mgocr.from_known_furnace(
            year=1405, month=4, melts=750, melt_weight_kg=127705938, product_weight_kg=123513760,
            slab_count=423, bloom_billet_count=327,
        )
        ph = Path(tmp) / "f4.txt"
        ph.write_text("seed", encoding="utf-8")
        out = mgp.store_production_from_ocr(db, ph, user=user, source="smoke", ocr_result=fk, manual_corrected=True)
        assert not out.ok and out.tab_rejected and db.list_main_goal_production() == []

        # casting-tab known CCM values for 3 continuous months
        for month, scale in [(4, 1.0), (5, 0.9), (6, 1.2)]:
            known = mgocr.from_known_casting(
                year=1405, month=month,
                ccm_tons={1: 40000 * scale, 2: 30000 * scale, 3: 15000 * scale, 4: 14000 * scale, 5: 13000 * scale},
                ccm_melts={1: 240, 2: 180, 3: 85, 4: 90, 5: 87},
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
        known = mgocr.from_known_casting(
            year=1405, month=8, ccm_tons={1: 5000, 3: 2000, 4: 2000}, ccm_melts={1: 30, 3: 12, 4: 12},
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

        # multi-month × multi-section log → split by start month × machine section;
        # earliest-month straddle (starts 03/31, ends 04/01) folds into Tir.
        mp = Path(tmp) / "mixed_seq.xlsx"
        wb = Workbook(); ws = wb.active
        ws.append(["ماشین", "تاندیش", "تاندیشکار", "تعداد ذوب", "مدت سکوئنس", "ISG",
                   "ذوب اول سکوئنس", None, "ذوب آخر سکوئنس", None, "تعویض شرود", "تعویض نازل بیرونی"])
        ws.append([None, None, None, None, None, None, "شماره ذوب", "شروع ریخته گری", "شماره ذوب", "پایان ریخته گری", None, None])
        mixed = [("اسلب 1", "1405/06/20 01:00", "1405/06/20 09:00", 6),
                 ("بیلت 2", "1405/06/21 01:00", "1405/06/21 09:00", 9),
                 ("اسلب 2", "1405/05/03 01:00", "1405/05/03 09:00", 7),
                 ("بلوم", "1405/04/09 01:00", "1405/04/09 05:00", 4),
                 ("اسلب 1", "1405/04/02 01:00", "1405/04/02 07:00", 5),
                 ("اسلب 1", "1405/03/31 18:18", "1405/04/01 04:49", 3)]
        for i, (mach, st, en, melts) in enumerate(mixed):
            ws.append([mach, 20 + i, 1, melts, 120, "1", 60000000 + i, st, 60000100 + i, en, "دارد", "ندارد"])
        wb.save(mp)
        out = mgp.store_sequences_from_excel(db, mp, user=user, source="smoke", section="slab")
        assert out.ok, out.error_fa
        got = {(c["period_key"], c["section"]): c["tundish_count"] for c in db.list_main_goal_consumption()}
        assert got.get(("m:1405-06", "slab")) == 1 and got.get(("m:1405-06", "billet")) == 1, got
        assert got.get(("m:1405-05", "slab")) == 1 and got.get(("m:1405-04", "bloom")) == 1, got
        assert got.get(("m:1405-04", "slab")) == 2, got  # 04/02 + folded 03/31 straddle
        assert not any(pk == "m:1405-03" for pk, _ in got), got
        assert "لبه فایل" in out.summary and "چند بخشی" in out.summary, out.summary
        print("mixed sequence log split OK:", sorted(got.items()))

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
        r = mgocr.parse_furnace_ocr_text(sample)  # audit-only parser still works
        assert r.ok and r.month == 6 and r.product_weight_kg == 150310814, (r.error_fa, r)
        assert r.melt_count == 930, (r.melt_count, r.notes)
        g = mgocr.parse_production_ocr_text(sample)  # but the production gate rejects it
        assert not g.ok and g.tab_rejected and g.report_tab == "furnace", g.tab_evidence
        print("furnace text: audit parse ok, production gate rejects", r.period_label)

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
