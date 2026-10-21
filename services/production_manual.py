"""Text-only «آمار تولید» input (bot + web share this module).

Monthly production is entered by hand: Jalali year/month, then product weight of each
caster in **kg** from the start of that month (CCM1=اسلب ۱, CCM2=اسلب ۲, CCM3=بلوم,
CCM4=بیلت ۱, CCM5=بیلت ۲) and the month's total melt count (normal + sub-code).

Stored in ``main_goal_production`` in **tons** (kg / 1000) so every existing
calculation (slab = CCM1+2, bloom = CCM3, billet = CCM4+5) consumes it unchanged.
Months with no row are simply absent (excluded from averages, never «0 تن»).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from bot.jalali import format_date, format_month_year, tehran_now
from services import main_goal_persist as mgp

SOURCE_TYPE = "manual_text"
REPORT_TAB = "manual"

# (key, short label, section)
FIELDS: list[tuple[str, str, str]] = [
    ("ccm1", "ccm1 (اسلب ۱)", "slab"),
    ("ccm2", "ccm2 (اسلب ۲)", "slab"),
    ("ccm3", "ccm3 (بلوم)", "bloom"),
    ("ccm4", "ccm4 (بیلت ۱)", "billet"),
    ("ccm5", "ccm5 (بیلت ۲)", "billet"),
]
FIELD_KEYS = [f[0] for f in FIELDS] + ["melts"]
FIELD_LABEL = {k: lbl for k, lbl, _ in FIELDS}
FIELD_LABEL["melts"] = "تعداد کل ذوب‌ها"

_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def fa_num(s: Any) -> str:
    return str(s).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def normalize_digits(text: Any) -> str:
    return str(text or "").translate(_FA_DIGITS).strip()


def parse_number(text: Any, *, integer: bool = False) -> float | None:
    """«۱۲٬۳۴۵»، «12,345.5»، «12345» → float (None when invalid or negative)."""
    t = normalize_digits(text)
    t = t.replace("٬", "").replace(",", "").replace("،", "").replace(" ", "").replace("\u200c", "")
    t = t.replace("٫", ".")
    if t.lower().endswith("kg"):
        t = t[:-2]
    if not re.fullmatch(r"\d+(\.\d+)?", t or ""):
        return None
    v = float(t)
    if integer and v != int(v):
        return None
    return v


def parse_year_month(text: Any) -> tuple[int, int] | None:
    """«1405/07»، «۱۴۰۵/۷»، «1405-07» → (1405, 7)."""
    t = normalize_digits(text).replace(" ", "")
    m = re.fullmatch(r"(\d{4})[/\-.](\d{1,2})", t)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    if not (1350 <= y <= 1500 and 1 <= mo <= 12):
        return None
    return y, mo


def ym_text(year: int, month: int) -> str:
    return f"{int(year):04d}/{int(month):02d}"


def period_key(year: int, month: int) -> str:
    return f"m:{int(year):04d}-{int(month):02d}"


def field_prompt(key: str, year: int, month: int) -> str:
    ym = ym_text(year, month)
    if key == "melts":
        return f"تعداد کل ذوب‌های {ym} (مجموع ذوب‌های نرمال و با کد فرعی) را وارد کنید"
    return f"وزن محصول {FIELD_LABEL[key]} {ym} را به کیلوگرم وارد کنید"


def fmt_kg(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{f:,.0f}" if f == int(f) else f"{f:,.3f}".rstrip("0").rstrip(".")


@dataclass
class ManualProduction:
    year: int
    month: int
    kg: dict[str, float]
    melts: float

    @property
    def key(self) -> str:
        return period_key(self.year, self.month)

    @property
    def label(self) -> str:
        return format_month_year(self.year, self.month, named=True)

    def section_tons(self) -> dict[str, float]:
        out = {"slab": 0.0, "bloom": 0.0, "billet": 0.0}
        for k, _lbl, sec in FIELDS:
            out[sec] += float(self.kg.get(k) or 0) / 1000.0
        return out

    def summary_fa(self) -> str:
        tons = self.section_tons()
        lines = [f"📝 آمار تولید {ym_text(self.year, self.month)} ({self.label})"]
        for k, lbl, _ in FIELDS:
            lines.append(f"• وزن محصول {lbl}: {fmt_kg(self.kg.get(k))} کیلوگرم")
        lines.append(f"• تعداد کل ذوب‌ها: {fmt_kg(self.melts)}")
        lines.append(
            f"جمع (تن): اسلب {tons['slab']:,.1f} | بلوم {tons['bloom']:,.1f} | "
            f"بیلت {tons['billet']:,.1f} | کل {sum(tons.values()):,.1f}"
        )
        return "\n".join(lines)


def from_row(row: dict) -> ManualProduction | None:
    """Rebuild entered kg values from a stored production row."""
    if not row or not row.get("year") or not row.get("month"):
        return None
    kg = {}
    for i, (k, _l, _s) in enumerate(FIELDS, start=1):
        t = row.get(f"ccm{i}_tons")
        kg[k] = float(t) * 1000.0 if t is not None else 0.0
    return ManualProduction(int(row["year"]), int(row["month"]), kg, float(row.get("melt_count") or 0))


def get_month(db: Any, year: int, month: int) -> dict | None:
    return db.get_main_goal_production_by_key(period_key(year, month))


def list_months(db: Any) -> list[dict]:
    """Stored production months (newest first) for edit/delete pickers."""
    rows = [r for r in db.list_main_goal_production() if r.get("year") and r.get("month")]
    rows.sort(key=lambda r: (int(r["year"]), int(r["month"])), reverse=True)
    return rows


def store(db: Any, data: ManualProduction, *, user: dict, source: str) -> tuple[dict, bool]:
    tons = data.section_tons()
    now = tehran_now()
    kg_total = sum(float(data.kg.get(k) or 0) for k, _l, _s in FIELDS)
    row, replaced = db.upsert_main_goal_production(
        period_key=data.key,
        period_label=data.label,
        year=data.year,
        month=data.month,
        sort_key=mgp.period_sort_key(data.year, data.month, data.key),
        slab_tons=tons["slab"],
        bloom_tons=tons["bloom"],
        billet_tons=tons["billet"],
        total_tons=sum(tons.values()),
        melt_count=float(data.melts),
        melt_weight_kg=None,
        product_weight_kg=kg_total,
        slab_count=None,
        bloom_billet_count=None,
        melts_per_day=None,
        report_tab=REPORT_TAB,
        source_status=mgp.STATUS_VALID,
        slab_melt_count=None,
        bloom_melt_count=None,
        billet_melt_count=None,
        ccm1_tons=float(data.kg.get("ccm1") or 0) / 1000.0,
        ccm2_tons=float(data.kg.get("ccm2") or 0) / 1000.0,
        ccm3_tons=float(data.kg.get("ccm3") or 0) / 1000.0,
        ccm4_tons=float(data.kg.get("ccm4") or 0) / 1000.0,
        ccm5_tons=float(data.kg.get("ccm5") or 0) / 1000.0,
        source_type=SOURCE_TYPE,
        source_path=None,
        source_filename=None,
        ocr_raw_text=None,
        ocr_confidence=None,
        ocr_fields_json=json.dumps({"kg": data.kg, "melts": data.melts}, ensure_ascii=False),
        manual_corrected=1,
        notes_json=json.dumps(["ورود متنی آمار تولید (کیلوگرم → تن)"], ensure_ascii=False),
        missing_json="[]",
        bale_user_id=user["bale_user_id"],
        actor_display_name=user.get("display_name"),
        source=source,
        created_at_tehran=now.isoformat(),
        jalali_date=format_date(now),
    )
    # legacy aggregate is derived: drop it and re-sync when all inputs exist
    db.delete_main_goal_month_by_key(data.key)
    mgp.sync_month_aggregate(db, data.key, user=user, source=source)
    return row, replaced


def delete(db: Any, year: int, month: int) -> bool:
    key = period_key(year, month)
    ok = db.delete_main_goal_production_by_key(key)
    db.delete_main_goal_month_by_key(key)
    return ok


def confirmation_fa(data: ManualProduction, replaced: bool) -> str:
    verb = "جایگزین شد" if replaced else "ثبت شد"
    return f"✅ آمار تولید {ym_text(data.year, data.month)} {verb}.\n\n" + data.summary_fa()
