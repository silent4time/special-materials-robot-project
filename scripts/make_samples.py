#!/usr/bin/env python3
"""Create three sample Excel templates under samples/ (tundish-focused)."""
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
    # Multi-day tank/tundish consumption so daily averages are meaningful.
    # Keep exactly 4 rows for علی رضایی (tech 1001) and 4 for خط-B (officer scope).
    tank_rows = [
        # خط-A / علی رضایی — 4 rows across dates
        ["خط-A", "1001", "علی رضایی", "T-01", "اسید سولفوریک", 12.5, "لیتر", "2026-09-01", "نوبت صبح"],
        ["خط-A", "1001", "علی رضایی", "T-01", "اسید سولفوریک", 11.0, "لیتر", "2026-09-02", ""],
        ["خط-A", "1001", "علی رضایی", "T-02", "سود سوزآور", 8.0, "کیلو", "2026-09-01", ""],
        ["خط-A", "1001", "علی رضایی", "T-02", "سود سوزآور", 7.5, "کیلو", "2026-09-03", ""],
        # خط-B — 4 rows (مریم + حسین) for officer scope tests
        ["خط-B", "1002", "مریم احمدی", "T-05", "آب اکسیژنه", 20.0, "لیتر", "2026-09-02", ""],
        ["خط-B", "1002", "مریم احمدی", "T-05", "آب اکسیژنه", 18.0, "لیتر", "2026-09-04", ""],
        ["خط-B", "1003", "حسین کریمی", "T-06", "کلر", 5.5, "کیلو", "2026-09-02", "اضطراری"],
        ["خط-B", "1003", "حسین کریمی", "T-06", "کلر", 6.0, "کیلو", "2026-09-05", ""],
        # انبار
        ["انبار", "1002", "مریم احمدی", "T-10", "روغن صنعتی", 3.0, "لیتر", "2026-09-03", ""],
        ["انبار", "1002", "مریم احمدی", "T-10", "روغن صنعتی", 2.5, "لیتر", "2026-09-06", ""],
    ]
    write_sheet(
        SAMPLES / "01_tank_consumption.xlsx",
        [
            "domain",
            "assignee_id",
            "assignee_name",
            "tundish_id",
            "material_name",
            "quantity",
            "unit",
            "date",
            "notes",
        ],
        tank_rows,
    )

    # Inventory product_name matches consumption material_name for analytics join.
    # Low stock on اسید / کلر so they appear critical (CRITICAL_DAYS=3).
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
            ["خط-A", "1001", "علی رضایی", "اسید سولفوریک", 20, "لیتر", "قفسه ۱", "2026-09-06", "کم"],
            ["خط-A", "1001", "علی رضایی", "سود سوزآور", 80, "کیلو", "قفسه ۲", "2026-09-06", ""],
            ["خط-B", "1002", "مریم احمدی", "آب اکسیژنه", 100, "لیتر", "سالن B", "2026-09-06", ""],
            ["خط-B", "1003", "حسین کریمی", "کلر", 8, "کیلو", "سالن B", "2026-09-06", "بحرانی"],
            ["انبار", "1002", "مریم احمدی", "روغن صنعتی", 50, "لیتر", "انبار مرکزی", "2026-09-06", ""],
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
            ["خط-A", "1001", "علی رضایی", "اسید سولفوریک", "1404-06", 350, "لیتر", "تأیید", ""],
            ["خط-A", "1001", "علی رضایی", "سود سوزآور", "1404-06", 220, "کیلو", "در انتظار", ""],
            ["خط-B", "1002", "مریم احمدی", "آب اکسیژنه", "1404-06", 500, "لیتر", "تأیید", ""],
            ["خط-B", "1003", "حسین کریمی", "کلر", "1404-06", 150, "کیلو", "تأیید", ""],
            ["انبار", "1002", "مریم احمدی", "روغن صنعتی", "1404-06", 80, "لیتر", "بررسی", ""],
        ],
    )


if __name__ == "__main__":
    main()
