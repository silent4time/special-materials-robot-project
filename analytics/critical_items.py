"""اقلام بحرانی — monthly tundish-count × منبع اصلی rates.

Shared by Bale bot and web panel. Do not duplicate this logic elsewhere.

MODES (نوسازی): the user picks one before generating.
  • «با نوسازی» (RENO_WITH, default / previous behavior): per-tundish material
    for a type = renovation + patching (e.g. billet_renovation + billet_patching).
  • «بدون نوسازی» (RENO_WITHOUT): per-tundish material = patching only; every
    *_renovation column is ignored. Items whose need comes only from renovation
    (e.g. «بتن 86 نوسازی», patching rate 0) get need 0 and are NOT listed.
  The سطح ریخته گری (casting_floor) share is kept in BOTH modes — it is consumed
  by casting on the floor, independent of tundish renovation.
Rates stay separate in منبع اصلی (billet/bloom/slab × renovation/patching).

SPLIT (پیمانکار / شرکت): rows of منبع اصلی are split by the
``contractor_or_company`` column («پیمانکار / شرکت» in sheet «ریز اطلاعات»)
BEFORE category aggregation. The primary report lists ONLY «شرکت» rows; «پیمانکار»
rows form a separate report/section. Blank or unrecognized values fall back to
the material-id rule (``excel.id_parse.is_contractor_material_id``: 2nd group of
four chars == «0000» → پیمانکار); still unknown → شرکت.

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
from excel.id_parse import is_contractor_material_id

SEGMENT_COMPANY = "company"
SEGMENT_CONTRACTOR = "contractor"
SEGMENTS = (SEGMENT_COMPANY, SEGMENT_CONTRACTOR)
SEGMENT_LABEL_FA = {SEGMENT_COMPANY: "شرکت", SEGMENT_CONTRACTOR: "پیمانکار"}
CONTRACTOR_COLUMN = "contractor_or_company"

RENO_WITH = "with"
RENO_WITHOUT = "without"
RENO_MODES = (RENO_WITH, RENO_WITHOUT)
RENO_LABEL_FA = {RENO_WITH: "با نوسازی", RENO_WITHOUT: "بدون نوسازی"}
RENOVATION_COLS = ("billet_renovation", "bloom_renovation", "slab_renovation")


def normalize_reno_mode(value: object) -> str:
    """Accept with/without, 1/0, true/false or the Persian labels; default RENO_WITH."""
    text = "".join(str(value or "").split()).replace("\u200c", "").lower()
    if text in {"without", "without_renovation", "0", "false", "no", "بدوننوسازی", "بدون"}:
        return RENO_WITHOUT
    return RENO_WITH


def active_rate_cols(reno_mode: str = RENO_WITH) -> tuple[str, ...]:
    """Rate columns that contribute to need in the given mode."""
    if normalize_reno_mode(reno_mode) == RENO_WITHOUT:
        return tuple(c for c in RATE_COLS if c not in RENOVATION_COLS)
    return RATE_COLS


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


def _norm_fa(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.strip().lower() in {"", "nan", "none"}:
        return ""
    text = (
        text.replace("ي", "ی")
        .replace("ى", "ی")
        .replace("ك", "ک")
        .replace("\u200c", "")
        .replace("\u200f", "")
        .replace("\u200e", "")
    )
    return "".join(text.split()).lower()


_CONTRACTOR_TOKENS = ("پیمانکار", "contractor")
_COMPANY_TOKENS = ("شرکت", "company")


def classify_contractor_or_company(value: object, item_id: object = None) -> str:
    """Map a «پیمانکار / شرکت» cell (+ id fallback) to SEGMENT_COMPANY/CONTRACTOR."""
    text = _norm_fa(value)
    if text:
        if any(tok in text for tok in _CONTRACTOR_TOKENS):
            return SEGMENT_CONTRACTOR
        if any(tok in text for tok in _COMPANY_TOKENS):
            return SEGMENT_COMPANY
    flag = is_contractor_material_id(item_id)
    if flag is True:
        return SEGMENT_CONTRACTOR
    return SEGMENT_COMPANY


def segment_series(inventory_df: pd.DataFrame) -> pd.Series:
    """Per-row segment (company / contractor) for an inventory frame."""
    if inventory_df is None or inventory_df.empty:
        return pd.Series(dtype=object)
    vals = (
        inventory_df[CONTRACTOR_COLUMN]
        if CONTRACTOR_COLUMN in inventory_df.columns
        else pd.Series([None] * len(inventory_df), index=inventory_df.index)
    )
    ids = (
        inventory_df["id"]
        if "id" in inventory_df.columns
        else pd.Series([None] * len(inventory_df), index=inventory_df.index)
    )
    return pd.Series(
        [classify_contractor_or_company(v, i) for v, i in zip(vals, ids)],
        index=inventory_df.index,
        dtype=object,
    )


def filter_inventory_segment(
    inventory_df: pd.DataFrame | None, segment: str | None
) -> pd.DataFrame | None:
    """Rows of one segment; ``segment=None`` returns the frame unchanged."""
    if inventory_df is None or segment is None or inventory_df.empty:
        return inventory_df
    if segment not in SEGMENTS:
        raise ValueError(f"unknown segment: {segment}")
    seg = segment_series(inventory_df)
    return inventory_df.loc[seg == segment].copy()


def contractor_column_summary(inventory_df: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Distinct raw values of «پیمانکار / شرکت» with row counts and mapped segment."""
    if inventory_df is None or inventory_df.empty:
        return []
    raw = (
        inventory_df[CONTRACTOR_COLUMN]
        if CONTRACTOR_COLUMN in inventory_df.columns
        else pd.Series([""] * len(inventory_df), index=inventory_df.index)
    )
    seg = segment_series(inventory_df)
    work = pd.DataFrame({"value": raw.map(lambda v: "" if _norm_fa(v) == "" else str(v).strip()), "segment": seg})
    out = []
    for (value, segment), grp in work.groupby(["value", "segment"], sort=True):
        out.append({"value": value, "segment": segment, "rows": int(len(grp))})
    return out


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


def _pick_description(
    group: pd.DataFrame, rate_cols: tuple[str, ...] | None = None
) -> tuple[str, str]:
    """Return (description, unit) from best keyword / product_name row."""
    work = group.copy()
    prio = (
        _priority_values(work["priority"])
        if "priority" in work.columns
        else pd.Series(1.0, index=work.index)
    )
    work["_prio"] = prio
    rate_sum = sum(_num_series(work, c) for c in (rate_cols or RATE_COLS))
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
    reno_mode: str = RENO_WITH,
) -> float:
    """Monthly need from per-tundish rates × counts (+ casting_floor rule).

    «با نوسازی»: per tundish = renovation + patching. «بدون نوسازی»: patching
    only. casting_floor is applied in both modes.
    """
    reno = 1.0 if normalize_reno_mode(reno_mode) == RENO_WITH else 0.0
    per_billet = reno * float(billet_renovation) + float(billet_patching)
    per_bloom = reno * float(bloom_renovation) + float(bloom_patching)
    per_slab = reno * float(slab_renovation) + float(slab_patching)
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
    segment: str | None = None,
    reno_mode: str = RENO_WITH,
) -> pd.DataFrame:
    """One row per category_code with any renovation/patching/casting_floor > 0.

    ``segment`` = SEGMENT_COMPANY / SEGMENT_CONTRACTOR restricts the input rows
    (stock, rates, critical-point totals) to that «پیمانکار / شرکت» side before
    aggregation. None keeps every row (legacy / tests).

    ``reno_mode`` = RENO_WITH (renovation + patching) or RENO_WITHOUT (patching
    only; renovation-only categories drop out). casting_floor counts in both.

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
    inventory_df = filter_inventory_segment(inventory_df, segment)
    if inventory_df is None or inventory_df.empty:
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
    reno_mode = normalize_reno_mode(reno_mode)
    use_cols = active_rate_cols(reno_mode)
    rate_any = work[list(use_cols)].sum(axis=1) > 0
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
        if sum(rates[c] for c in use_cols) <= 0:
            continue
        # Skip if aggregated rates are all zero (defensive)
        if sum(rates.values()) <= 0:
            continue
        need = monthly_need_for_rates(counts=counts, reno_mode=reno_mode, **rates)
        if need <= 0:
            # Still include zero-need only when rates exist but counts are all 0
            # and casting_floor is 0 — skip those.
            continue
        desc, unit = _pick_description(group, use_cols)
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


def report_title(
    counts: TundishMonthCounts,
    segment: str | None = None,
    reno_mode: str | None = None,
) -> str:
    seg = f" — {SEGMENT_LABEL_FA[segment]}" if segment in SEGMENT_LABEL_FA else ""
    mode = (
        f" — {RENO_LABEL_FA[normalize_reno_mode(reno_mode)]}" if reno_mode is not None else ""
    )
    return f"لیست اقلام بحرانی نسوز تاندیش{seg}{mode} ({counts.month_label()})"


def report_subtitle(
    counts: TundishMonthCounts,
    *,
    row_count: int,
    segment: str | None = None,
    reno_mode: str | None = None,
) -> str:
    seg = f"اقلام {SEGMENT_LABEL_FA[segment]}: " if segment in SEGMENT_LABEL_FA else ""
    mode = (
        f"حالت: {RENO_LABEL_FA[normalize_reno_mode(reno_mode)]} | "
        if reno_mode is not None
        else ""
    )
    return (
        f"{mode}{seg}{row_count} قلم | تعداد تاندیش — بیلت: {counts.count_billet}، "
        f"بلوم: {counts.count_bloom}، اسلب: {counts.count_slab}"
    )


def reno_mode_note(reno_mode: str | None = None) -> str:
    if normalize_reno_mode(reno_mode) == RENO_WITHOUT:
        return (
            "حالت محاسبه: «بدون نوسازی» — مصرف هر تاندیش فقط نرخ پچینگ "
            "(بیلت/بلوم/اسلب) است و ستون‌های نوسازی نادیده گرفته می‌شوند؛ اقلامی که "
            "نیازشان فقط از نوسازی است (نرخ پچینگ صفر، مثل «بتن 86 نوسازی») فهرست نمی‌شوند."
        )
    return (
        "حالت محاسبه: «با نوسازی» — برای هر نوع تاندیش، مصرف هر تاندیش = "
        "نوسازی + پچینگ (تا وقتی لاگ روزانه نوسازی/پچینگ جدا شود)."
    )


def report_footer_notes(
    counts: TundishMonthCounts, reno_mode: str | None = None
) -> list[str]:
    """توضیحات footer matching the sample sheet style."""
    return [
        (
            f"جهت ریخته‌گری بیلت تعداد {counts.count_billet} تاندیش، "
            f"بلوم {counts.count_bloom} و اسلب {counts.count_slab} "
            f"در نظر گرفته شده است."
        ),
        reno_mode_note(reno_mode),
        (
            "فرض سطح ریخته‌گری: نرخ ستون × مجموع تعداد تاندیش‌های ماه "
            "(در هر دو حالت با/بدون نوسازی منظور می‌شود)."
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
        (
            "تفکیک بر اساس ستون «پیمانکار / شرکت» منبع اصلی: گزارش اصلی فقط اقلام "
            "«شرکت» است و اقلام «پیمانکار» در گزارش/بخش جداگانه می‌آید؛ موجودی، نرخ "
            "و نقطه بحرانی هر بخش فقط از ردیف‌های همان بخش محاسبه می‌شود "
            "(اگر خانه خالی باشد، قاعده شناسه «0000» ملاک است)."
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
