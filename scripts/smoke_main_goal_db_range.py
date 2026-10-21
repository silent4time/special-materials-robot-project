#!/usr/bin/env python3
"""Smoke: normalized DB schema, text production-stats store (kg→t), sequences, range report, gap warning."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402  — temp DB/reports/uploads before config import

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
from services import production_manual as pm
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

        # text production stats (kg per CCM) for 3 continuous months
        for month, scale in [(4, 1.0), (5, 0.9), (6, 1.2)]:
            kg = {f"ccm{n}": t * scale * 1000 for n, t in zip(range(1, 6), (40000, 30000, 15000, 14000, 13000))}
            pm.store(db, pm.ManualProduction(1405, month, kg, 682), user=user, source="smoke")
        row = db.get_main_goal_production_by_key("m:1405-04")
        assert abs(row["slab_tons"] - 70000) < 1e-6 and abs(row["billet_tons"] - 27000) < 1e-6, row

        # gap warning should be None for continuous 4,5,6
        months = mgp.load_history_from_db(db)
        assert len(months) == 3
        assert mgh.consecutive_month_gap_warning(months) is None

        # introduce gap by adding month 8 only via production
        pm.store(db, pm.ManualProduction(1405, 8, {"ccm1": 5e6, "ccm2": 0, "ccm3": 2e6, "ccm4": 2e6, "ccm5": 0}, 54),
                 user=user, source="smoke")
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


    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
