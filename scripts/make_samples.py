#!/usr/bin/env python3
"""Create three sample Excel templates under samples/."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook

SAMPLES = ROOT / "samples"


def write_sheet(path: Path, headers: list[str], rows: list[list]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(headers)
    for row in rows:
        ws.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    print("wrote", path)


def main() -> None:
    write_sheet(
        SAMPLES / "01_tank_consumption.xlsx",
        [
            "domain",
            "assignee_id",
            "assignee_name",
            "tank_id",
            "material_name",
            "quantity",
            "unit",
            "date",
            "notes",
        ],
        [
            ["خط-A", "1001", "علی رضایی", "T-01", "اسید سولفوریک", 12.5, "لیتر", "2026-09-01", "نوبت صبح"],
            ["خط-A", "1001", "علی رضایی", "T-02", "سود سوزآور", 8.0, "کیلو", "2026-09-01", ""],
            ["خط-B", "1002", "مریم احمدی", "T-05", "آب اکسیژنه", 20.0, "لیتر", "2026-09-02", ""],
            ["خط-B", "1003", "حسین کریمی", "T-06", "کلر", 5.5, "کیلو", "2026-09-02", "اضطراری"],
            ["انبار", "1002", "مریم احمدی", "T-10", "روغن صنعتی", 3.0, "لیتر", "2026-09-03", ""],
        ],
    )
    write_sheet(
        SAMPLES / "02_product_inventory.xlsx",
        [
            "domain",
            "assignee_id",
            "assignee_name",
            "product_name",
            "quantity",
            "unit",
            "location",
            "date",
            "notes",
        ],
        [
            ["خط-A", "1001", "علی رضایی", "محصول آلفا", 120, "عدد", "قفسه ۱", "2026-09-01", ""],
            ["خط-A", "1001", "علی رضایی", "محصول بتا", 45, "بسته", "قفسه ۲", "2026-09-01", "کمبود"],
            ["خط-B", "1002", "مریم احمدی", "محصول گاما", 80, "عدد", "سالن B", "2026-09-02", ""],
            ["انبار", "1003", "حسین کریمی", "محصول دلتا", 200, "کیلو", "انبار مرکزی", "2026-09-03", ""],
        ],
    )
    write_sheet(
        SAMPLES / "03_monthly_consumption.xlsx",
        [
            "domain",
            "assignee_id",
            "assignee_name",
            "material_name",
            "month",
            "quantity",
            "unit",
            "status",
            "notes",
        ],
        [
            ["خط-A", "1001", "علی رضایی", "اسید سولفوریک", "1404-06", 140, "لیتر", "تأیید", ""],
            ["خط-A", "1001", "علی رضایی", "سود سوزآور", "1404-06", 90, "کیلو", "در انتظار", ""],
            ["خط-B", "1002", "مریم احمدی", "آب اکسیژنه", "1404-06", 210, "لیتر", "تأیید", ""],
            ["خط-B", "1003", "حسین کریمی", "کلر", "1404-06", 60, "کیلو", "تأیید", ""],
            ["انبار", "1002", "مریم احمدی", "روغن صنعتی", "1404-06", 40, "لیتر", "بررسی", ""],
        ],
    )


if __name__ == "__main__":
    main()
