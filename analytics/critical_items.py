"""اقلام بحرانی — forecast shortage over a lead-time horizon (shared by bot + web).

DEFINITION (user, 1405-07-14 — t206u/t207u):
  • monthly consumption (per 4-digit code) = rates × the AVERAGE monthly tundish
    counts of the last 3 months (manual «تعداد تاندیش ماهانه» entries ending at the
    selected month; fewer months → whatever exists, named in the header) — column
    «میانگین مصرف ماهانه بر اساس ۳ ماه گذشته»;
  • horizon H by origin (column «سازنده» / ``origin``): وارداتی → 6 months,
    داخلی → 3 months; missing → داخلی (3) + flagged in the notes; a code whose live
    rows mix both → 6 (conservative, flagged as «مختلط»);
  • «مصرف پیش‌بینی‌شده در افق» = monthly × H;
  • «نیاز» = max(0, horizon consumption − stock); a code is listed ONLY when
    نیاز > 0 (domestic < 90 days, imported < 180 days of cover);
  • حد تحمل (روز) = stock ÷ (monthly ÷ 30) — 30-day months (DAYS_PER_MONTH).

MODES (نوسازی): the user picks one before generating.
  • «با نوسازی» (RENO_WITH, default / previous behavior): per-tundish material
    for a type = renovation + patching (e.g. billet_renovation + billet_patching).
  • «بدون نوسازی» (RENO_WITHOUT): per-tundish material = patching only; every
    *_renovation column is ignored. Items whose need comes only from renovation
    (e.g. «بتن 86 نوسازی», patching rate 0) get need 0 and are NOT listed.
  The سطح ریخته گری (casting_floor) share is kept in BOTH modes — it is consumed
  by casting on the floor, independent of tundish renovation.
Rates stay separate in منبع اصلی (billet/bloom/slab × renovation/patching).

SPLIT (پیمانکار / شرکت): rows of منبع اصلی are split BEFORE category aggregation
by the material-id rule (``excel.id_parse.is_contractor_material_id``: 2nd group of
four chars == «0000» → پیمانکار, else شرکت) — authoritative since 1405-07-14; the
``contractor_or_company`` cell is only a fallback for ids too short to tell (still
unknown → شرکت). The primary report lists ONLY «شرکت» rows; «پیمانکار» rows form a
separate report/section. کد 1800 (اقلام مازاد) is never a consumable here.

AGGREGATION (per 4-digit کد دسته‌بندی, within one segment):
  • rows with اولویت 0 («بدون اولویت» = unused) are dropped first (blank → 1);
  • NO quantity threshold — rows with موجودی < 100 (or 0) are included;
  • موجودی (per-row column) = SUM of quantity over ALL remaining rows of the
    code, rated or not (e.g. 1450 بتن ملات: rated row 0, siblings hold stock);
  • rates (renovation/patching/casting_floor) and نقطه بحرانی are PER-CODE
    values = MAX over the same rows. The importer copies a merged rate cell into
    every row it spans (excel.processor.fill_merged_cells), so the value must be
    taken ONCE per code, never summed across rows;
  • a code is listed when its active-mode rates give need > 0; alert when
    موجودی ≤ نقطه بحرانی.

SECTION ATTRIBUTION (analytics.section_rules, before the segment split): a
  code with live rows on BOTH شرکت and پیمانکار takes BILLET rates only from
  company rows, BLOOM rates only from contractor rows, SLAB rates from
  contractor rows and from company rows whose محل استفاده contains «اسلب».

SHARED NEED (merged rate crossing several codes, column ``rate_group``):
  config.CRITICAL_SHARED_MERGE_MODE = "pooled" (default): the codes form ONE
  group. Merged columns count ONCE for the group (max); other rate columns are
  summed over member codes. Output = one group row (codes joined by «/»,
  combined stock vs shared need, days of cover, critical flag) followed by one
  row per member code with its OWN stock and «نیاز مشترک گروه» (no own need).
  "per_code": every member code gets the full rate as a normal item.
  Groups never cross segments (segment split happens first).

ASSUMPTION (casting_floor): the سطح ریخته گری rate is treated as per-tundish
material multiplied by (count_billet + count_bloom + count_slab). Refine when
daily casting-floor logs exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from analytics.tundish import (
    NO_PRIORITY,
    _priority_values,
)
from bot.jalali import PERSIAN_MONTH_NAMES
import config as _config
from analytics.section_rules import apply_section_rate_attribution
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


SHARED_POOLED = "pooled"
SHARED_PER_CODE = "per_code"
SHARED_NEED_LABEL = "نیاز مشترک گروه"
ROW_ITEM = "item"
ROW_GROUP = "group"
ROW_MEMBER = "member"
RATE_GROUP_COLUMN = "rate_group"


def shared_merge_mode(value: str | None = None) -> str:
    raw = value if value is not None else getattr(_config, "CRITICAL_SHARED_MERGE_MODE", SHARED_POOLED)
    return SHARED_PER_CODE if str(raw or "").strip().lower() == SHARED_PER_CODE else SHARED_POOLED


RATE_COLS = (
    "billet_renovation",
    "billet_patching",
    "bloom_renovation",
    "bloom_patching",
    "slab_renovation",
    "slab_patching",
    "casting_floor",
)

COL_ORIGIN = "مبدأ"
COL_HORIZON = "افق (ماه)"
COL_MONTHLY = "میانگین مصرف ماهانه بر اساس ۳ ماه گذشته"
COL_FORECAST = "مصرف پیش‌بینی‌شده در افق"
COL_NEED = "نیاز"
COL_DAYS = "حد تحمل(روز)"

REPORT_COLUMNS = [
    "کد چهاررقمی",
    "ردیف",
    "کد و شرح کالا",
    COL_ORIGIN,
    COL_HORIZON,
    "موجودی",
    "واحد",
    COL_MONTHLY,
    COL_FORECAST,
    COL_NEED,
    COL_DAYS,
]

DAYS_PER_MONTH = 30
BASIS_MONTHS = 3
ORIGIN_IMPORTED = "imported"
ORIGIN_DOMESTIC = "domestic"
ORIGIN_LABEL_FA = {ORIGIN_IMPORTED: "وارداتی", ORIGIN_DOMESTIC: "داخلی"}
HORIZON_MONTHS = {ORIGIN_IMPORTED: 6, ORIGIN_DOMESTIC: 3}
ORIGIN_MIXED_FA = "مختلط (وارداتی+داخلی)"
ORIGIN_MISSING_FA = "نامشخص (داخلی فرض شد)"


SOURCE_SEQUENCE_LOG = "sequence_log"
SOURCE_MANUAL = "manual"
SOURCE_LABEL_FA = {
    SOURCE_SEQUENCE_LOG: "لاگ توالی تاندیش",
    SOURCE_MANUAL: "ثبت دستی تعداد تاندیش",
}


@dataclass(frozen=True)
class TundishMonthCounts:
    """Monthly tundish basis. Counts may be 3-month AVERAGES (floats).

    ``basis_months`` = ((year, month, billet, bloom, slab[, source]), …) actually
    averaged; empty → a single month (jalali_year/jalali_month) as entered.
    ``report_date`` = generation date «1405/07/14» (titles / file names); when set it
    replaces the month label in titles.
    """

    jalali_year: int
    jalali_month: int
    count_billet: float
    count_bloom: float
    count_slab: float
    basis_months: tuple = ()
    report_date: str = ""

    @property
    def total(self) -> float:
        return float(self.count_billet) + float(self.count_bloom) + float(self.count_slab)

    def month_label(self) -> str:
        if self.report_date:
            return self.report_date
        name = PERSIAN_MONTH_NAMES.get(int(self.jalali_month), str(self.jalali_month))
        return f"{name} {int(self.jalali_year)}"

    def months(self) -> list[tuple]:
        """[(year, month, billet, bloom, slab, source), …] (source "" if unknown)."""
        if self.basis_months:
            return [tuple(m) + ("",) * (6 - len(m)) for m in self.basis_months]
        return [(self.jalali_year, self.jalali_month, self.count_billet, self.count_bloom,
                 self.count_slab, "")]

    def basis_label(self) -> str:
        """«تیر تا شهریور 1405» for consecutive months, else «اردیبهشت 1405 و تیر 1405»."""
        ms = [(int(y), int(m)) for y, m, *_ in self.months()]
        if not ms:
            return ""
        name = lambda y, m: f"{PERSIAN_MONTH_NAMES.get(m, str(m))} {y}"  # noqa: E731
        if len(ms) == 1:
            return name(*ms[0])
        consecutive = all(
            (b[0] * 12 + b[1]) - (a[0] * 12 + a[1]) == 1 for a, b in zip(ms, ms[1:])
        )
        if consecutive:
            (y0, m0), (y1, m1) = ms[0], ms[-1]
            first = PERSIAN_MONTH_NAMES.get(m0, str(m0)) if y0 == y1 else name(y0, m0)
            return f"{first} تا {name(y1, m1)}"
        names = [name(y, m) for y, m in ms]
        return "، ".join(names[:-1]) + " و " + names[-1]

    def sources(self) -> list[str]:
        return [m[5] for m in self.months() if m[5]]


def fmt_count(v: float) -> str:
    v = float(v)
    return str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:.1f}"


def previous_complete_months(year: int, month: int, n: int = BASIS_MONTHS) -> list[tuple[int, int]]:
    """The n complete months BEFORE (year, month), oldest first.

    Report generated on 1405/07/14 → [(1405, 4), (1405, 5), (1405, 6)].
    """
    out = []
    y, m = int(year), int(month)
    for _ in range(n):
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        out.append((y, m))
    return list(reversed(out))


def basis_from_months(
    used: list[tuple],
    *,
    report_date: str = "",
    year: int | None = None,
    month: int | None = None,
) -> TundishMonthCounts | None:
    """Average of ``used`` = [(year, month, billet, bloom, slab, source), …]."""
    if not used:
        return None
    n = float(len(used))
    ly, lm = int(used[-1][0]), int(used[-1][1])
    return TundishMonthCounts(
        jalali_year=int(year) if year is not None else ly,
        jalali_month=int(month) if month is not None else lm,
        count_billet=sum(float(u[2]) for u in used) / n,
        count_bloom=sum(float(u[3]) for u in used) / n,
        count_slab=sum(float(u[4]) for u in used) / n,
        basis_months=tuple(tuple(u) for u in used),
        report_date=report_date,
    )


def average_tundish_basis(
    year: int, month: int, rows: list[dict]
) -> TundishMonthCounts | None:
    """Average of the stored MANUAL monthly counts for the 3 months ending at (year, month).

    ``rows`` = stored monthly_tundish_counts dicts (rows flagged ``exclude_from_basis``
    are ignored). Missing months are skipped. None when none of the 3 months exist.
    """
    wanted = []
    y, m = int(year), int(month)
    for _ in range(BASIS_MONTHS):
        wanted.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    by_key = {
        (int(r["jalali_year"]), int(r["jalali_month"])): r
        for r in rows or []
        if not int(r.get("exclude_from_basis") or 0)
    }
    used = [
        (yy, mm, float(by_key[(yy, mm)]["count_billet"]), float(by_key[(yy, mm)]["count_bloom"]),
         float(by_key[(yy, mm)]["count_slab"]), SOURCE_MANUAL)
        for yy, mm in reversed(wanted)
        if (yy, mm) in by_key
    ]
    return basis_from_months(used, year=year, month=month)


def classify_origin(value: object) -> str | None:
    """وارداتی → imported, داخلی → domestic, else None (missing / unclear)."""
    text = _norm_fa(value)
    if not text:
        return None
    if "وارد" in text or "import" in text or "خارج" in text:
        return ORIGIN_IMPORTED
    if "داخل" in text or "domestic" in text or "local" in text:
        return ORIGIN_DOMESTIC
    return None


def code_origin(group: pd.DataFrame) -> tuple[str, str, str]:
    """(origin key used for H, Persian label, flag) for the live rows of one code/group.

    flag: "" | "mixed" | "missing" (missing → domestic, H=3).
    """
    vals = [classify_origin(v) for v in (group["origin"] if "origin" in group.columns else [])]
    known = {v for v in vals if v}
    if not known:
        return ORIGIN_DOMESTIC, ORIGIN_MISSING_FA, "missing"
    if len(known) > 1:
        return ORIGIN_IMPORTED, ORIGIN_MIXED_FA, "mixed"
    key = next(iter(known))
    # Blank «سازنده» on some sibling rows: the code's known origin applies.
    return key, ORIGIN_LABEL_FA[key], ""


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
    """Map a row to SEGMENT_COMPANY/CONTRACTOR.

    شناسه مواد is authoritative (chars 5–8 == «0000» → پیمانکار, else شرکت); the
    «تأمین‌کننده» (شرکت/پیمانکار) cell is only a fallback when the id is too short / blank.
    """
    flag = is_contractor_material_id(item_id)
    if flag is not None:
        return SEGMENT_CONTRACTOR if flag else SEGMENT_COMPANY
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
    """Distinct raw values of «تأمین‌کننده» (شرکت/پیمانکار) with row counts and mapped segment."""
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


def drop_priority_zero_rows(inventory_df: pd.DataFrame) -> pd.DataFrame:
    """Rows whose اولویت ≠ 0 (blank counts as 1). No quantity filter."""
    if inventory_df is None or inventory_df.empty or "priority" not in inventory_df.columns:
        return inventory_df
    prio = _priority_values(inventory_df["priority"])
    return inventory_df.loc[prio != float(NO_PRIORITY)].copy()


def drop_surplus_rows(inventory_df: pd.DataFrame) -> pd.DataFrame:
    """Drop کد دسته 1800 (اقلام مازاد) — never a consumable for critical items / main goal."""
    from config import SURPLUS_CATEGORY_CODE

    if inventory_df is None or inventory_df.empty or "category_code" not in inventory_df.columns:
        return inventory_df
    codes = inventory_df["category_code"].map(
        lambda v: str(v).strip()[:-2] if str(v).strip().endswith(".0") else str(v).strip()
    )
    return inventory_df.loc[codes != SURPLUS_CATEGORY_CODE].copy()


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
    shared_mode: str | None = None,
    include_covered: bool = False,
) -> pd.DataFrame:
    """One row per category_code with any renovation/patching/casting_floor > 0.

    ``segment`` = SEGMENT_COMPANY / SEGMENT_CONTRACTOR restricts the input rows
    (stock, rates, critical-point totals) to that «تأمین‌کننده» (شرکت/پیمانکار) side before
    aggregation. None keeps every row (legacy / tests).

    ``reno_mode`` = RENO_WITH (renovation + patching) or RENO_WITHOUT (patching
    only; renovation-only categories drop out). casting_floor counts in both.

    Printed columns = ``REPORT_COLUMNS`` (مبدأ, افق, موجودی, میانگین مصرف ماهانه بر
    اساس ۳ ماه گذشته, مصرف پیش‌بینی‌شده در افق, نیاز, حد تحمل). Only codes with
    نیاز = forecast − stock > 0 are returned. Extra columns (critical_point,
    horizon_months, origin_key/flag, row_kind …) are for callers, not printed.
    ``include_covered=True`` also returns rated codes whose stock covers the
    horizon (نیاز 0) — for the «نقطه بحرانی» fill / audits, never for the report.
    """
    empty_cols = REPORT_COLUMNS + [
        "critical_point",
        "filtered_stock",
        "below_threshold",
        "daily_need",
        "monthly_need",
        "horizon_months",
        "origin_key",
        "origin_flag",
        "row_kind",
        "group_codes",
    ]
    if inventory_df is None or inventory_df.empty or "category_code" not in inventory_df.columns:
        return pd.DataFrame(columns=empty_cols)
    # Section attribution on the FULL frame (needs both segments): codes with
    # شرکت + پیمانکار rows → billet from company, bloom from contractor, slab
    # from contractor + company rows located «اسلب» (analytics.section_rules).
    # کد 1800 = اقلام مازاد → never a consumable (rule B, 1405-07-14).
    inventory_df = drop_surplus_rows(inventory_df)
    inventory_df, _changes = apply_section_rate_attribution(inventory_df)
    inventory_df = filter_inventory_segment(inventory_df, segment)
    if inventory_df is None or inventory_df.empty:
        return pd.DataFrame(columns=empty_cols)
    # Shared-need groups come from the merge structure (all rows of the segment).
    group_source = inventory_df
    # اولویت 0 = unused → excluded from stock, rates and critical point.
    # (No quantity threshold: rows with موجودی < 100 are kept.)
    inventory_df = drop_priority_zero_rows(inventory_df)
    if inventory_df.empty:
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

    reno_mode = normalize_reno_mode(reno_mode)
    use_cols = active_rate_cols(reno_mode)
    # 30-day months for day conversions (horizon 90 / 180 days).
    days = max(1, int(days_in_month) if days_in_month is not None else DAYS_PER_MONTH)

    per_code: dict[str, dict[str, Any]] = {}
    for code, group in work.groupby("category_code", sort=True):
        per_code[str(code)] = {
            "rates": {c: float(_num_series(group, c).max()) for c in RATE_COLS},
            "cp": _critical_point_value(group),
            "stock": _real_stock(group),
            "group": group,
        }

    groups = (
        shared_need_groups(group_source, set(per_code))
        if shared_merge_mode(shared_mode) == SHARED_POOLED
        else []
    )
    grouped_codes = {code for g in groups for code in g["codes"]}

    def _metrics(
        rates: dict[str, float], stock: float, cp: float | None, origin: tuple[str, str, str]
    ) -> dict[str, Any] | None:
        if sum(rates[c] for c in use_cols) <= 0 or sum(rates.values()) <= 0:
            return None
        monthly = monthly_need_for_rates(counts=counts, reno_mode=reno_mode, **rates)
        if monthly <= 0:
            return None
        okey, olabel, oflag = origin
        horizon = HORIZON_MONTHS[okey]
        forecast = monthly * horizon
        shortage = max(0.0, forecast - stock)
        if shortage <= 1e-9 and not include_covered:
            return None  # stock covers the horizon → not critical
        daily = monthly / float(days)
        days_cover = stock / daily if daily > 0 else 0.0
        return {
            "need": shortage,
            "monthly": monthly,
            "forecast": forecast,
            "horizon": horizon,
            "origin_key": okey,
            "origin_label": olabel,
            "origin_flag": oflag,
            "daily": daily,
            "days_cover": days_cover,
            "below": bool(cp is not None and stock <= float(cp)),
        }

    def _r(v: float) -> int | float:
        v = round(float(v), 1)
        return int(v) if abs(v - round(v)) < 1e-9 else v

    def _ceil(v: float) -> int:
        import math

        return int(math.ceil(float(v) - 1e-6)) if v > 0 else 0

    def _row(code: str, desc: str, unit: str, stock: float, m: dict[str, Any], cp, kind: str) -> dict[str, Any]:
        return {
            "کد چهاررقمی": code,
            "ردیف": 0,
            "کد و شرح کالا": desc,
            COL_ORIGIN: m["origin_label"],
            COL_HORIZON: m["horizon"],
            "موجودی": stock,
            "واحد": unit,
            COL_MONTHLY: _r(m["monthly"]),
            COL_FORECAST: _r(m["forecast"]),
            # نیاز rounded UP (whole units to order; averages make it fractional)
            COL_NEED: _ceil(m["need"]),
            COL_DAYS: int(round(m["days_cover"])) if m["days_cover"] > 0 else 0,
            "critical_point": cp,
            "filtered_stock": stock,
            "below_threshold": m["below"],
            "daily_need": m["daily"],
            "monthly_need": m["monthly"],
            "horizon_months": m["horizon"],
            "origin_key": m["origin_key"],
            "origin_flag": m["origin_flag"],
            "row_kind": kind,
            "group_codes": "",
        }

    blocks: list[tuple[tuple, list[dict[str, Any]]]] = []
    for code, info in per_code.items():
        if code in grouped_codes:
            continue
        m = _metrics(info["rates"], info["stock"], info["cp"], code_origin(info["group"]))
        if m is None:
            continue
        desc, unit = _pick_description(info["group"], use_cols)
        row = _row(code, desc, unit, info["stock"], m, info["cp"], ROW_ITEM)
        blocks.append(((row[COL_DAYS], code), [row]))

    for g in groups:
        members = list(g["codes"])
        merged_cols = set(g["columns"])
        rates = {}
        for c in RATE_COLS:
            vals = [per_code[k]["rates"][c] for k in members]
            rates[c] = max(vals) if c in merged_cols else sum(vals)
        cps = [per_code[k]["cp"] for k in members if per_code[k]["cp"] is not None]
        if not cps:
            cp = None
        elif "critical_point" in merged_cols:
            cp = max(cps)
        else:
            cp = float(sum(cps))
        stock = float(sum(per_code[k]["stock"] for k in members))
        # Group horizon from all member rows (members with different origins → «مختلط», H=6).
        m = _metrics(
            rates, stock, cp, code_origin(pd.concat([per_code[k]["group"] for k in members]))
        )
        if m is None:
            continue
        descs = []
        unit = ""
        for k in members:
            d, u = _pick_description(per_code[k]["group"], use_cols)
            descs.append(d)
            unit = unit or u
        label_codes = "/".join(members)
        head = _row(label_codes, _group_label(descs), unit, stock, m, cp, ROW_GROUP)
        head["group_codes"] = label_codes
        rows_block = [head]
        for k, d in zip(members, descs):
            _d, u = _pick_description(per_code[k]["group"], use_cols)
            mo = code_origin(per_code[k]["group"])
            rows_block.append(
                {
                    "کد چهاررقمی": k,
                    "ردیف": "",
                    "کد و شرح کالا": d,
                    COL_ORIGIN: mo[1],
                    COL_HORIZON: "—",
                    "موجودی": per_code[k]["stock"],
                    "واحد": u or unit,
                    COL_MONTHLY: SHARED_NEED_LABEL,
                    COL_FORECAST: "—",
                    COL_NEED: SHARED_NEED_LABEL,
                    COL_DAYS: "—",
                    "critical_point": None,
                    "filtered_stock": per_code[k]["stock"],
                    "below_threshold": None,
                    "daily_need": None,
                    "monthly_need": None,
                    "horizon_months": None,
                    "origin_key": mo[0],
                    "origin_flag": mo[2],
                    "row_kind": ROW_MEMBER,
                    "group_codes": label_codes,
                }
            )
        blocks.append(((head[COL_DAYS], members[0]), rows_block))

    if not blocks:
        return pd.DataFrame(columns=empty_cols)
    blocks.sort(key=lambda b: b[0])
    rows: list[dict[str, Any]] = []
    n = 0
    for _key, block in blocks:
        for row in block:
            if row["row_kind"] != ROW_MEMBER:
                n += 1
                row["ردیف"] = n
            rows.append(row)
    out = pd.DataFrame(rows)
    for col in ("موجودی", COL_MONTHLY, COL_FORECAST, COL_NEED):
        out[col] = out[col].map(_pretty_num)
    return out


def critical_item_count(df: pd.DataFrame | None) -> int:
    """Listed items: plain codes + shared-need groups (member rows excluded)."""
    if df is None or df.empty:
        return 0
    if "row_kind" not in df.columns:
        return int(len(df))
    return int((df["row_kind"] != ROW_MEMBER).sum())


def group_row_indices(df: pd.DataFrame | None) -> list[int]:
    """0-based positions of shared-need group subtotal rows (for emphasis)."""
    if df is None or df.empty or "row_kind" not in df.columns:
        return []
    return [i for i, k in enumerate(df["row_kind"].tolist()) if k == ROW_GROUP]


def _group_label(descs: list[str]) -> str:
    """Common leading words of member descriptions (e.g. «نازل»), else first."""
    words = [str(d or "").split() for d in descs if str(d or "").strip()]
    if not words:
        return "گروه مشترک"
    common: list[str] = []
    for parts in zip(*words):
        if all(p == parts[0] for p in parts):
            common.append(parts[0])
        else:
            break
    base = " ".join(common) if common else words[0][0]
    return f"{base} (گروه با نیاز مشترک)"


def _parse_rate_group(value: object) -> tuple[str, tuple[str, ...]] | None:
    text = "" if value is None else str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    gid, _, cols = text.partition(":")
    return gid.strip(), tuple(c.strip() for c in cols.split(",") if c.strip())


def shared_need_groups(
    frame: pd.DataFrame | None, present_codes: set[str] | None = None
) -> list[dict[str, Any]]:
    """Code groups linked by ``rate_group`` (merged rate crossing codes).

    ``frame`` must already be segment-filtered (groups never cross segments).
    Codes sharing any rate_group id are unioned. Only codes in
    ``present_codes`` (priority ≠ 0 rows exist) are kept; groups with < 2
    such codes are dropped (the code is then a normal item).
    """
    if frame is None or frame.empty or RATE_GROUP_COLUMN not in frame.columns:
        return []
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    cols_by_root: dict[str, set[str]] = {}
    for _, row in frame.iterrows():
        parsed = _parse_rate_group(row.get(RATE_GROUP_COLUMN))
        code = _norm_code(row.get("category_code"))
        if parsed is None or not code:
            continue
        gid, cols = parsed
        a, b = find("g:" + gid), find("c:" + code)
        if a != b:
            parent[b] = a
        cols_by_root.setdefault("g:" + gid, set()).update(cols)
    comps: dict[str, dict[str, Any]] = {}
    for node in list(parent):
        root = find(node)
        comp = comps.setdefault(root, {"codes": set(), "columns": set()})
        if node.startswith("c:"):
            comp["codes"].add(node[2:])
        else:
            comp["columns"].update(cols_by_root.get(node, set()))
    out = []
    for comp in comps.values():
        codes = sorted(
            c for c in comp["codes"] if present_codes is None or c in present_codes
        )
        if len(codes) >= 2:
            out.append({"codes": codes, "columns": sorted(comp["columns"])})
    return sorted(out, key=lambda g: g["codes"])


def _norm_code(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if text in {"nan", "None"}:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


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


def basis_counts_text(counts: TundishMonthCounts) -> str:
    """«اسلب 70.7، بیلت 34، بلوم 0.3» of the (average) monthly basis."""
    return (
        f"اسلب {fmt_count(counts.count_slab)}، بیلت {fmt_count(counts.count_billet)}، "
        f"بلوم {fmt_count(counts.count_bloom)}"
    )


def basis_sources_text(counts: TundishMonthCounts) -> str:
    srcs = []
    for s in counts.sources():
        lab = SOURCE_LABEL_FA.get(s, s)
        if lab not in srcs:
            srcs.append(lab)
    return " + ".join(srcs)


def horizon_header_note(counts: TundishMonthCounts) -> str:
    """Header note on every critical-items report (PDF, xlsx, bot, web)."""
    n = len(counts.months())
    src = basis_sources_text(counts)
    src_txt = f"؛ منبع: {src}" if src else ""
    short = (
        f" — فقط {n} ماه از ۳ ماه کامل گذشته داده داشت" if n < BASIS_MONTHS else ""
    )
    date_txt = f" تاریخ گزارش: {counts.report_date}." if counts.report_date else ""
    return (
        "این جدول برای ۳ ماه آینده (اقلام داخلی) و ۶ ماه آینده (اقلام وارداتی) با توجه به "
        f"میانگین تعداد تاندیش {counts.basis_label()} ({basis_counts_text(counts)} در ماه"
        f"{src_txt}{short}) تهیه شده است.{date_txt}"
    )


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
    return f"{mode}{seg}{row_count} قلم | {horizon_header_note(counts)}"


def reno_mode_note(reno_mode: str | None = None) -> str:
    if normalize_reno_mode(reno_mode) == RENO_WITHOUT:
        return (
            "حالت محاسبه: «بدون نوسازی» — مصرف هر تاندیش فقط نرخ پچینگ "
            "(بیلت/بلوم/اسلب) است و ستون‌های نوسازی نادیده گرفته می‌شوند؛ اقلامی که "
            "مصرفشان فقط از نوسازی است (نرخ پچینگ صفر، مثل «بتن 86 نوسازی») فهرست نمی‌شوند."
        )
    return (
        "حالت محاسبه: «با نوسازی» — برای هر نوع تاندیش، مصرف هر تاندیش = "
        "نوسازی + پچینگ (تا وقتی لاگ روزانه نوسازی/پچینگ جدا شود)."
    )


def origin_audit(
    inventory_df: pd.DataFrame | None, segment: str | None = None
) -> dict[str, list[str]]:
    """Codes (priority ≠ 0, not 1800) whose «سازنده/مبدأ» is missing or mixed."""
    out: dict[str, list[str]] = {"missing": [], "mixed": []}
    if inventory_df is None or inventory_df.empty or "category_code" not in inventory_df.columns:
        return out
    work = drop_priority_zero_rows(filter_inventory_segment(drop_surplus_rows(inventory_df), segment))
    if work is None or work.empty:
        return out
    codes = work["category_code"].map(_norm_code)
    rated = pd.Series(False, index=work.index)
    for c in RATE_COLS:
        if c in work.columns:
            rated |= _num_series(work, c) > 0
    for code in sorted(set(codes[rated]) - {""}):
        _k, _l, flag = code_origin(work.loc[codes == code])
        if flag:
            out[flag].append(code)
    return out


def origin_notes(audit: dict[str, list[str]] | None) -> list[str]:
    notes = []
    if audit and audit.get("missing"):
        notes.append(
            "مبدأ (داخلی/وارداتی) برای این کدها در منبع اصلی خالی/نامشخص است و «داخلی» "
            "(افق ۳ ماه) فرض شد — لطفاً ستون «سازنده» را تکمیل کنید: "
            + "، ".join(audit["missing"])
        )
    if audit and audit.get("mixed"):
        notes.append(
            "کدهایی که ردیف داخلی و وارداتی هر دو دارند (مختلط) با افق ۶ ماه (محتاطانه) "
            "حساب شدند: " + "، ".join(audit["mixed"])
        )
    return notes


def report_footer_notes(
    counts: TundishMonthCounts,
    reno_mode: str | None = None,
    audit: dict[str, list[str]] | None = None,
) -> list[str]:
    """توضیحات footer."""
    months = "؛ ".join(
        f"{PERSIAN_MONTH_NAMES.get(int(m), str(m))} {int(y)}: اسلب {fmt_count(sl)}، "
        f"بیلت {fmt_count(b)}، بلوم {fmt_count(bl)}"
        + (f" ({SOURCE_LABEL_FA.get(src, src)})" if src else "")
        for y, m, b, bl, sl, src in counts.months()
    )
    return [
        horizon_header_note(counts),
        f"تعداد تاندیش ماه‌های مبنا (۳ ماه کامل قبل از تاریخ گزارش) — {months}.",
        (
            "لاگ توالی تاندیش ستون نوسازی/پچینگ ندارد؛ هر سکوئنس (ردیف لاگ) یک بار استفاده "
            "از تاندیش شمرده شده است: «با نوسازی» = هر تاندیش نرخ نوسازی + پچینگ، "
            "«بدون نوسازی» = فقط نرخ پچینگ."
        ),
        (
            f"«{COL_MONTHLY}» = نرخ‌های هر کد × میانگین ماهانه تعداد تاندیش ماه‌های مبنا "
            "(+ سطح ریخته‌گری × مجموع تاندیش‌ها)."
        ),
        (
            "افق: اقلام وارداتی ۶ ماه و اقلام داخلی ۳ ماه (زمان تأمین) — مبدأ از ستون "
            f"«سازنده» منبع اصلی. «{COL_FORECAST}» = میانگین مصرف ماهانه × افق."
        ),
        (
            f"«{COL_NEED}» = مصرف پیش‌بینی‌شده در افق − موجودی (اگر مثبت باشد). فقط اقلامی با "
            "نیاز مثبت فهرست می‌شوند (داخلی: پوشش کمتر از ۹۰ روز، وارداتی: کمتر از ۱۸۰ روز)."
        ),
        reno_mode_note(reno_mode),
        (
            "فرض سطح ریخته‌گری: نرخ ستون × مجموع تعداد تاندیش‌های ماه "
            "(در هر دو حالت با/بدون نوسازی منظور می‌شود)."
        ),
        (
            f"حد تحمل (روز) = موجودی واقعی ÷ (میانگین مصرف ماهانه ÷ {DAYS_PER_MONTH} روز) — "
            "ماه‌ها ۳۰ روزه حساب شده‌اند."
        ),
        *origin_notes(audit),
        (
            "تجمیع بر اساس کد ۴ رقمی (در همان بخش تأمین‌کننده شرکت/پیمانکار، فقط ردیف‌های با "
            "اولویت غیر صفر — اولویت ۰ یعنی استفاده نمی‌شود): موجودی = جمع موجودی همه "
            "ردیف‌های کد، با نرخ یا بدون نرخ و بدون هیچ حد حداقل (مثل زیر ۱۰۰)؛ نرخ‌های "
            "مصرف و سطح ریخته‌گری مقدار واحدِ هر کد هستند (بیشینه ردیف‌ها، یک بار) و بین "
            "ردیف‌ها جمع نمی‌شوند."
        ),
        (
            "خانه‌های ادغام‌شده (Merge) در فایل منبع اصلی: مقدار به همه ردیف‌های زیر "
            "آن تعلق دارد و در ورود اطلاعات به همه آن ردیف‌ها کپی می‌شود؛ فقط موجودی "
            "(مقدار هر ردیف) کپی نمی‌شود."
        ),
        shared_need_note(),
        (
            "تخصیص بخش برای کدی که هم ردیف «شرکت» و هم «پیمانکار» (با اولویت غیر صفر) "
            "دارد: مصرف بیلت فقط از ردیف‌های شرکت، مصرف بلوم فقط از ردیف‌های پیمانکار، "
            "و مصرف اسلب از ردیف‌های پیمانکار و ردیف‌های شرکت با محل استفاده «اسلب» "
            "حساب می‌شود (حتی اگر خانه ادغام‌شده نرخ را به ردیف بخش دیگر کپی کرده باشد)."
        ),
        (
            "تأمین‌کننده (شرکت/پیمانکار) از روی شناسه مواد: اگر رقم‌های ۵ تا ۸ شناسه «0000» "
            "باشد پیمانکار، وگرنه شرکت. گزارش اصلی فقط اقلام «شرکت» است و اقلام "
            "«پیمانکار» در گزارش/بخش جداگانه می‌آید؛ موجودی و نرخ هر بخش "
            "فقط از ردیف‌های همان بخش محاسبه می‌شود. اقلام کد ۱۸۰۰ (مازاد) مصرفی حساب نمی‌شوند."
        ),
    ]


def shared_need_note(mode: str | None = None) -> str:
    if shared_merge_mode(mode) == SHARED_PER_CODE:
        return (
            "نرخ ادغام‌شده روی چند کد ۴ رقمی (مثل نازل‌ها): هر کد نرخ کامل را "
            "جداگانه می‌گیرد (حالت per_code)."
        )
    return (
        "نیاز مشترک گروه: وقتی یک نرخ مصرف در فایل روی چند کد ۴ رقمی ادغام شده "
        "(مثل نازل‌های بیلت در اندازه‌های مختلف)، آن نرخ یک مصرف مشترک برای کل گروه "
        "است و فقط یک بار حساب می‌شود. ردیف گروه (کدها با «/») جمع موجودی کدها را "
        "با مصرف مشترک در افق مقایسه می‌کند (افق گروه: اگر مبدأ اعضا متفاوت باشد ۶ ماه) "
        "و نیاز و حد تحمل از همین ردیف است؛ زیر آن، هر کد با موجودی خودش و عبارت "
        "«نیاز مشترک گروه» می‌آید."
    )


def rows_for_simple_report(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Dict rows with only the sample print columns."""
    if df is None or df.empty:
        return []
    work = df.copy()
    for c in REPORT_COLUMNS:
        if c not in work.columns:
            work[c] = None
    return work[REPORT_COLUMNS].to_dict(orient="records")
