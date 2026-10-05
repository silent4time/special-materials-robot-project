"""اقلام بحرانی — monthly tundish-count × منبع اصلی rates.

Shared by Bale bot and web panel. Do not duplicate this logic elsewhere.

ASSUMPTION (renovation vs patching): when the monthly split between نوسازی and
پچینگ is unknown, per-tundish material for a type is the SUM of both columns
(e.g. billet_renovation + billet_patching). When daily tundish logs exist,
replace the sum with separate renovation/patching counts × rates.

ASSUMPTION (casting_floor): the سطح ریخته گری rate is treated as per-tundish
material multiplied by (count_billet + count_bloom + count_slab). Refine when
daily casting-floor logs exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from analytics.tundish import (
    CRITICAL_POINT_MIN_QTY,
    NO_PRIORITY,
    _ana_key,
    _priority_values,
    critical_point_category_totals,
)
from bot.jalali import PERSIAN_MONTH_NAMES, days_in_jalali_month

RATE_COLS = (
    "billet_renovation",
    "billet_patching",
    "bloom_renovation",
    "bloom_patching",
    "slab_renovation",
    "slab_patching",
    "casting_floor",
)

REPORT_COLUMNS = [
    "کد چهاررقمی",
    "ردیف",
    "کد و شرح کالا",
    "موجودی",
    "واحد",
    "نیاز",
    "حد تحمل(روز)",
]


@dataclass(frozen=True)
class TundishMonthCounts:
    jalali_year: int
    jalali_month: int
    count_billet: int
    count_bloom: int
    count_slab: int

    @property
    def total(self) -> int:
        return int(self.count_billet) + int(self.count_bloom) + int(self.count_slab)

    def month_label(self) -> str:
        name = PERSIAN_MONTH_NAMES.get(int(self.jalali_month), str(self.jalali_month))
        return f"{name} {int(self.jalali_year)}"


def _num_series(work: pd.DataFrame, col: str) -> pd.Series:
    if col not in work.columns:
        return pd.Series(0.0, index=work.index, dtype=float)
    return pd.to_numeric(work[col], errors="coerce").fillna(0.0)


def _short_desc(text: object, limit: int = 80) -> str:
    s = str(text or "").strip()
    s = " ".join(s.split())
    if len(s) <= limit:
        return s
    return s[: max(1, limit - 1)].rstrip() + "…"


def _pick_description(group: pd.DataFrame) -> tuple[str, str]:
    """Return (description, unit) from best keyword / product_name row."""
    work = group.copy()
    prio = (
        _priority_values(work["priority"])
        if "priority" in work.columns
        else pd.Series(1.0, index=work.index)
    )
    work["_prio"] = prio
    rate_sum = sum(_num_series(work, c) for c in RATE_COLS)
    work["_has_rate"] = rate_sum > 0
    candidates = work.loc[work["_has_rate"]].copy()
    if candidates.empty:
        candidates = work
    # Prefer priority ≥ 1; among those, lowest priority number wins; else any.
    live = candidates.loc[candidates["_prio"] != float(NO_PRIORITY)]
    pool = live if not live.empty else candidates
    pool = pool.sort_values(by=["_prio"], kind="stable")
    row = pool.iloc[0]
    keyword = str(row.get("keyword") or "").strip()
    product = str(row.get("product_name") or "").strip()
    desc = keyword if keyword else _short_desc(product)
    if not desc:
        desc = _short_desc(product) or "—"
    unit = str(row.get("unit") or "").strip()
    return desc, unit


def _real_stock(group: pd.DataFrame) -> float:
    return float(_num_series(group, "quantity").sum())


def _critical_point_value(group: pd.DataFrame) -> float | None:
    if "critical_point" not in group.columns:
        return None
    vals = pd.to_numeric(group["critical_point"], errors="coerce").dropna()
    if vals.empty:
        return None
    return float(vals.max())


def monthly_need_for_rates(
    *,
    billet_renovation: float,
    billet_patching: float,
    bloom_renovation: float,
    bloom_patching: float,
    slab_renovation: float,
    slab_patching: float,
    casting_floor: float,
    counts: TundishMonthCounts,
) -> float:
    """Monthly need from per-tundish rates × counts (+ casting_floor rule)."""
    # ASSUMPTION: renovation + patching summed until daily logs split them.
    per_billet = float(billet_renovation) + float(billet_patching)
    per_bloom = float(bloom_renovation) + float(bloom_patching)
    per_slab = float(slab_renovation) + float(slab_patching)
    need = (
        float(counts.count_billet) * per_billet
        + float(counts.count_bloom) * per_bloom
        + float(counts.count_slab) * per_slab
    )
    # ASSUMPTION: casting_floor × total tundish count for the month.
    cf = float(casting_floor)
    if cf > 0 and counts.total > 0:
        need += cf * float(counts.total)
    elif cf > 0 and counts.total == 0:
        # No tundishes entered — still surface the raw casting-floor rate as need.
        need += cf
    return need


def build_critical_items_rows(
    inventory_df: pd.DataFrame | None,
    counts: TundishMonthCounts,
    *,
    days_in_month: int | None = None,
) -> pd.DataFrame:
    """One row per category_code with any renovation/patching/casting_floor > 0.

    Columns match the sample: کد چهاررقمی | ردیف | کد و شرح کالا | موجودی |
    واحد | نیاز | حد تحمل(روز). Extra columns (critical_point, filtered_stock,
    below_threshold) help callers highlight without changing the printed set.
    """
    empty_cols = REPORT_COLUMNS + [
        "critical_point",
        "filtered_stock",
        "below_threshold",
        "daily_need",
    ]
    if inventory_df is None or inventory_df.empty or "category_code" not in inventory_df.columns:
        return pd.DataFrame(columns=empty_cols)

    work = inventory_df.copy()
    work["category_code"] = work["category_code"].map(
        lambda v: str(v).strip() if v is not None and str(v).strip() not in {"", "nan", "None"} else ""
    )
    # Normalize 4-digit-ish codes (drop trailing .0)
    work["category_code"] = work["category_code"].map(
        lambda s: s[:-2] if isinstance(s, str) and s.endswith(".0") and s[:-2].isdigit() else s
    )
    work = work.loc[work["category_code"] != ""].copy()
    if work.empty:
        return pd.DataFrame(columns=empty_cols)

    for c in RATE_COLS:
        work[c] = _num_series(work, c)

    # Categories with any positive rate on any row
    rate_any = work[list(RATE_COLS)].sum(axis=1) > 0
    work = work.loc[rate_any]
    if work.empty:
        return pd.DataFrame(columns=empty_cols)

    days = int(days_in_month) if days_in_month is not None else days_in_jalali_month(
        counts.jalali_year, counts.jalali_month
    )
    days = max(1, days)

    # Filtered stock per category (Word critical-point aggregation rule)
    filtered = critical_point_category_totals(inventory_df)
    filtered_map = {
        _ana_key(r["category_code"]): float(r["total_quantity"])
        for _, r in filtered.iterrows()
    }

    rows: list[dict[str, Any]] = []
    for code, group in work.groupby("category_code", sort=True):
        rates = {c: float(_num_series(group, c).sum()) for c in RATE_COLS}
        # Skip if aggregated rates are all zero (defensive)
        if sum(rates.values()) <= 0:
            continue
        need = monthly_need_for_rates(counts=counts, **rates)
        if need <= 0:
            # Still include zero-need only when rates exist but counts are all 0
            # and casting_floor is 0 — skip those.
            continue
        desc, unit = _pick_description(group)
        stock = _real_stock(group)
        daily = need / float(days)
        if daily > 0:
            days_cover = stock / daily
        else:
            days_cover = 0.0
        cp = _critical_point_value(group)
        fstock = filtered_map.get(_ana_key(code), 0.0)
        below = bool(cp is not None and fstock <= float(cp))
        rows.append(
            {
                "کد چهاررقمی": str(code),
                "ردیف": 0,  # filled below
                "کد و شرح کالا": desc,
                "موجودی": round(stock, 3) if abs(stock - round(stock)) > 1e-9 else int(round(stock)) if abs(stock - round(stock)) < 1e-9 else stock,
                "واحد": unit,
                "نیاز": round(need, 3) if abs(need - round(need)) > 1e-9 else int(round(need)),
                "حد تحمل(روز)": int(round(days_cover)) if days_cover > 0 else 0,
                "critical_point": cp,
                "filtered_stock": fstock,
                "below_threshold": below,
                "daily_need": daily,
            }
        )

    if not rows:
        return pd.DataFrame(columns=empty_cols)

    out = pd.DataFrame(rows)
    # Prefer categories below critical_point threshold first, then by low cover, then code
    out = out.sort_values(
        by=["below_threshold", "حد تحمل(روز)", "کد چهاررقمی"],
        ascending=[False, True, True],
        kind="stable",
    ).reset_index(drop=True)
    out["ردیف"] = range(1, len(out) + 1)
    # Normalize موجودی / نیاز display numbers
    for col in ("موجودی", "نیاز"):
        out[col] = out[col].map(_pretty_num)
    return out


def _pretty_num(val: object) -> int | float:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return val  # type: ignore[return-value]
    if abs(num - round(num)) < 1e-9:
        return int(round(num))
    return round(num, 3)


def report_title(counts: TundishMonthCounts) -> str:
    return f"لیست اقلام بحرانی نسوز تاندیش ({counts.month_label()})"


def report_subtitle(counts: TundishMonthCounts, *, row_count: int) -> str:
    return (
        f"{row_count} قلم | تعداد تاندیش — بیلت: {counts.count_billet}، "
        f"بلوم: {counts.count_bloom}، اسلب: {counts.count_slab}"
    )


def report_footer_notes(counts: TundishMonthCounts) -> list[str]:
    """توضیحات footer matching the sample sheet style."""
    return [
        (
            f"جهت ریخته‌گری بیلت تعداد {counts.count_billet} تاندیش، "
            f"بلوم {counts.count_bloom} و اسلب {counts.count_slab} "
            f"در نظر گرفته شده است."
        ),
        (
            "فرض محاسبه نیاز: برای هر نوع تاندیش، مصرف هر تاندیش = "
            "نوسازی + پچینگ (تا وقتی لاگ روزانه نوسازی/پچینگ جدا شود)."
        ),
        (
            "فرض سطح ریخته‌گری: نرخ ستون × مجموع تعداد تاندیش‌های ماه."
        ),
        (
            f"حد تحمل (روز) = موجودی واقعی ÷ (نیاز ماهانه ÷ "
            f"{days_in_jalali_month(counts.jalali_year, counts.jalali_month)} روز)."
        ),
        (
            f"نقطه بحرانی: هشدار وقتی جمع موجودی فیلترشده "
            f"(بدون موجودی < {int(CRITICAL_POINT_MIN_QTY):g} و اولویت ۰) "
            f"به حد نقطه بحرانی دسته برسد."
        ),
    ]


def rows_for_simple_report(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Dict rows with only the sample print columns."""
    if df is None or df.empty:
        return []
    work = df.copy()
    for c in REPORT_COLUMNS:
        if c not in work.columns:
            work[c] = None
    return work[REPORT_COLUMNS].to_dict(orient="records")
