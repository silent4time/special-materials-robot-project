"""v1.3.1: critical items include every section (billet + slab + casting floor,
company + contractor) and the header lists the rated-item counts per section."""
import sys
from pathlib import Path

sys.path[:0] = [".", "scripts"]
import _smoke_env  # noqa: F401,E402
import pandas as pd  # noqa: E402

from analytics.critical_items import TundishMonthCounts  # noqa: E402
from services.critical_items_report import (  # noqa: E402
    build_critical_items_segments, section_rate_counts, section_rates_line, template_sheet)


def row(code, seg, loc, **rates):
    base = {"category_code": code, "item_name": f"قلم {code}", "description": f"قلم {code}",
            "contractor_or_company": seg, "location": loc, "stock": 0, "quantity": 0,
            "unit": "عدد", "priority": 1, "origin": "داخلی"}
    for c in ("billet_renovation", "billet_patching", "bloom_renovation", "bloom_patching",
              "slab_renovation", "slab_patching", "casting_floor"):
        base[c] = rates.get(c, 0)
    return base


inv = pd.DataFrame([
    row("1601", "شرکت", "انبار بیلت", billet_patching=2),
    row("1602", "شرکت", "انبار بیلت", billet_renovation=1, billet_patching=1),
    row("1655", "پیمانکار", "اسلب", slab_patching=3),
    row("1656", "پیمانکار", "اسلب", slab_renovation=2),
    row("1700", "پیمانکار", "سطح", casting_floor=1),
])
c = TundishMonthCounts(jalali_year=1405, jalali_month=8, count_billet=34, count_bloom=0,
                       count_slab=70, basis_months=((1405, 8, 34, 0, 70, "manual"),),
                       report_date="1405/08/02", manual_entry_date="1405/08/02")
frames = build_critical_items_segments(inv, c, "with")
codes = {s: set(map(str, (d["کد چهاررقمی"] if d is not None and len(d) else []))) for s, d in frames.items()}
allc = codes["company"] | codes["contractor"]
for need in ("1601", "1602", "1655", "1656", "1700"):
    assert need in allc, (need, codes)
assert codes["company"] and codes["contractor"], codes
rc = section_rate_counts(inv, "contractor", "with")
assert rc["slab"] == 2 and rc["casting_floor"] == 1, rc
line = section_rates_line(inv, "company", c, "with")
assert "بیلت 2 قلم" in line and "اسلب 0 قلم ⚠️بدون نرخ" in line, line
sh = template_sheet(c, "company", frames["company"], "with", rates_line=line)
assert line in sh["fixed_notes"], sh["fixed_notes"]
print("OK smoke_critical_sections", codes)
nocol = inv.drop(columns=["contractor_or_company"])
assert "ثبت نشده" in section_rates_line(nocol, "contractor", c, "with")
print("OK no-supplier-column warning")
