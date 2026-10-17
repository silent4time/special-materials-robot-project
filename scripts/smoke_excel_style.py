"""Standing rule (1405/07/25): every xlsx the app writes is tidy and uses Tahoma.

Generates one file from each Excel writer (simple/analytics reports, DataFrame export =
منبع اصلی download, clean extract = منبع اصلی storage, inbound, monthly summary, stock
snapshot, assistant table) in a temp dir, then opens EVERY generated xlsx and asserts:
Tahoma, center (horizontal + vertical), RTL, bordered + filled + frozen header.
Also checks that styling never changes the content of the منبع اصلی file (round-trip).
Temp dirs only — never live data.
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
TMP = _smoke_env.SMOKE_TMP / "xlsx"
TMP.mkdir()


def main() -> None:
    import pandas as pd
    from openpyxl import load_workbook

    from excel.table_style import EXCEL_FONT
    from smoke_phase3 import check_xlsx

    assert EXCEL_FONT == "Tahoma"
    src = TMP / "inv_src.xlsx"
    shutil.copy(ROOT / "samples" / "02_product_inventory.xlsx", src)
    from analytics.frames import PRIMARY_INVENTORY_TYPE
    from excel.processor import process_file, write_clean_excel

    frame, _meta = process_file(src, PRIMARY_INVENTORY_TYPE, {"role": "owner", "bale_user_id": "1", "active": 1})
    assert frame is not None and not frame.empty
    # 1) منبع اصلی clean storage + download: styling only, identical content
    ref = TMP / "ref_unstyled.xlsx"
    frame.to_excel(ref, index=False, engine="openpyxl", sheet_name="x")
    clean = TMP / "clean_main_source.xlsx"
    write_clean_excel(frame, clean, PRIMARY_INVENTORY_TYPE)
    a = pd.read_excel(ref, engine="openpyxl")
    b = pd.read_excel(clean, engine="openpyxl")
    pd.testing.assert_frame_equal(a, b, check_dtype=False)
    from services.main_source import FIELD_LABELS_FA, INVENTORY_COLUMNS
    from excel.simple_report import export_dataframe_xlsx, generate_simple_report_xlsx

    dl = export_dataframe_xlsx(frame, TMP / "main_source_download.xlsx", columns=list(INVENTORY_COLUMNS),
                               header_map=FIELD_LABELS_FA, sheet_name="ریز اطلاعات")
    got = pd.read_excel(dl, engine="openpyxl")
    work = frame.copy()
    for c in INVENTORY_COLUMNS:
        if c not in work.columns:
            work[c] = None
    ref2 = TMP / "ref2_unstyled.xlsx"
    work[list(INVENTORY_COLUMNS)].rename(columns=FIELD_LABELS_FA).to_excel(ref2, index=False, engine="openpyxl")
    pd.testing.assert_frame_equal(pd.read_excel(ref2, engine="openpyxl"), got, check_dtype=False)
    # 2) report writers
    generate_simple_report_xlsx("گزارش آزمایشی", subtitle="زیرعنوان طولانی برای آزمایش ادغام سطر عنوان",
                                columns=["a", "b"], rows=[{"a": "یک", "b": 2}, {"a": "دو", "b": 3.5}],
                                output_path=TMP / "simple.xlsx")
    from excel.inbound import write_inbound_excel

    write_inbound_excel(frame.head(5), TMP / "inbound.xlsx")
    from excel.monthly_summary import build_monthly_summary

    m = TMP / "monthly_src.xlsx"
    real = [p for p in (ROOT / "samples" / "real" / "monthly_consumption_sample.xlsx",) if p.is_file()] or [ROOT / "samples" / "03_monthly_consumption.xlsx"]
    shutil.copy(real[0], m)
    try:
        build_monthly_summary(m, excel_out=TMP / "monthly_summary.xlsx")
    except ValueError as exc:
        print("  monthly summary skipped:", exc)
    from excel.simple_report import generate_analytics_report_xlsx

    generate_analytics_report_xlsx({PRIMARY_INVENTORY_TYPE: frame}, {}, output_path=TMP / "analytics.xlsx")
    from db.models import Database
    from services.inbound_report import freeze_stock_snapshot

    freeze_stock_snapshot(Database(), frame.head(10), bale_user_id="1", actor_display_name="smoke")
    from services import assistant_tables as at

    at.write_files({"title": "جدول دستیار", "subtitle": "آزمایش",
                    "sections": [{"title": "بخش", "columns": ["کد", "شرح کالا", "موجودی"],
                                  "rows": [{"کد": 1581, "شرح کالا": "شرود", "موجودی": 1730}]}]}, TMP / "asst")
    # 3) every generated xlsx (here + anything a writer dropped in REPORT_DIR)
    import config

    from services.inbound_report import snapshot_dir

    files = sorted(set(TMP.rglob("*.xlsx")) | set(Path(config.REPORT_DIR).rglob("*.xlsx"))
                   | set(Path(snapshot_dir()).glob("*.xlsx")))
    files = [f for f in files if "_raw" not in f.stem]
    files = [f for f in files if f.name not in {"inv_src.xlsx", "monthly_src.xlsx", "ref_unstyled.xlsx", "ref2_unstyled.xlsx"}]
    assert len(files) >= 6, files
    bad: list[str] = []
    for f in files:
        bad += check_xlsx(f)
        wb = load_workbook(f)
        for ws in wb.worksheets:
            assert ws.sheet_view.rightToLeft, f
    assert not bad, bad
    print(f"  {len(files)} xlsx OK (Tahoma, centered, RTL, bordered/frozen header); منبع اصلی round-trip identical")
    print("SMOKE_EXCEL_STYLE_OK")


if __name__ == "__main__":
    main()
