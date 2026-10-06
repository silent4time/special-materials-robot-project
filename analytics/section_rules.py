"""Which منبع اصلی rows feed which tundish section (بیلت / بلوم / اسلب).

Shared by اقلام بحرانی (analytics.critical_items) and هدف اصلی / پیش‌بینی
(services.main_goal_report, services.main_goal_history). Do not duplicate.

Business rule (user, 1405-07-14) for a 4-digit code that has BOTH «شرکت» and
«پیمانکار» rows (counting only rows with اولویت ≠ 0 — priority 0 is unused):
  1. BILLET consumption comes from the COMPANY rows; BLOOM consumption comes
     from the CONTRACTOR rows (e.g. 1637: contractor row = bloom, company row =
     billet). A billet rate sitting on a contractor row (or a bloom rate on a
     company row — e.g. copied there by a merged cell) is NOT used.
  2. Company rows whose محل استفاده contains «اسلب» (and اولویت ≠ 0) are used
     for SLAB; other company rows of such a code give no slab rate. Contractor
     rows keep their slab rate (e.g. 1203 پیمانکار اسلب).
Codes with only one side are unchanged. سطح ریخته گری is not section-specific
and is never touched. Stock is per row, so it follows the same attribution
through the شرکت / پیمانکار split.
"""
from __future__ import annotations

import pandas as pd

SECTION_BILLET = "billet"
SECTION_BLOOM = "bloom"
SECTION_SLAB = "slab"
SECTIONS = (SECTION_BILLET, SECTION_BLOOM, SECTION_SLAB)
SECTION_RATE_COLS = {
    SECTION_BILLET: ("billet_renovation", "billet_patching"),
    SECTION_BLOOM: ("bloom_renovation", "bloom_patching"),
    SECTION_SLAB: ("slab_renovation", "slab_patching"),
}
SLAB_LOCATION_TOKEN = "اسلب"


def _code_series(df: pd.DataFrame) -> pd.Series:
    def norm(v: object) -> str:
        text = "" if v is None else str(v).strip()
        if text in {"nan", "None"}:
            return ""
        if text.endswith(".0") and text[:-2].isdigit():
            text = text[:-2]
        return text

    if "category_code" not in df.columns:
        return pd.Series("", index=df.index, dtype=object)
    return df["category_code"].map(norm)


def _segments(df: pd.DataFrame) -> pd.Series:
    from analytics.critical_items import segment_series

    return segment_series(df)


def _live_mask(df: pd.DataFrame) -> pd.Series:
    from analytics.tundish import NO_PRIORITY, _priority_values

    if "priority" not in df.columns:
        return pd.Series(True, index=df.index)
    return _priority_values(df["priority"]) != float(NO_PRIORITY)


def _slab_location(df: pd.DataFrame) -> pd.Series:
    if "usage_location" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["usage_location"].map(
        lambda v: SLAB_LOCATION_TOKEN in str(v or "").replace("ي", "ی")
    )


def codes_with_both_segments(df: pd.DataFrame | None) -> set[str]:
    """4-digit codes with live (اولویت ≠ 0) rows on BOTH شرکت and پیمانکار."""
    from analytics.critical_items import SEGMENT_COMPANY, SEGMENT_CONTRACTOR

    if df is None or df.empty or "category_code" not in df.columns:
        return set()
    codes = _code_series(df)
    seg = _segments(df)
    live = _live_mask(df)
    out: set[str] = set()
    for code in set(codes[live]) - {""}:
        sides = set(seg[live & (codes == code)])
        if SEGMENT_COMPANY in sides and SEGMENT_CONTRACTOR in sides:
            out.add(code)
    return out


def section_row_mask(df: pd.DataFrame | None, section: str) -> pd.Series:
    """True for rows whose rates/stock may feed ``section`` (rule above)."""
    from analytics.critical_items import SEGMENT_COMPANY, SEGMENT_CONTRACTOR

    if df is None or df.empty:
        return pd.Series(dtype=bool)
    both = codes_with_both_segments(df)
    mask = pd.Series(True, index=df.index)
    if not both or section not in SECTIONS:
        return mask
    in_both = _code_series(df).isin(both)
    seg = _segments(df)
    if section == SECTION_BILLET:
        mask &= ~(in_both & (seg == SEGMENT_CONTRACTOR))
    elif section == SECTION_BLOOM:
        mask &= ~(in_both & (seg == SEGMENT_COMPANY))
    else:  # slab
        company_ok = _slab_location(df) & _live_mask(df)
        mask &= ~(in_both & (seg == SEGMENT_COMPANY) & ~company_ok)
    return mask


def section_inventory(df: pd.DataFrame | None, section: str | None) -> pd.DataFrame | None:
    """Rows of منبع اصلی eligible for ``section`` (None/unknown → all rows).

    کد 1800 (اقلام مازاد) never feeds a section's consumption.
    """
    if df is None or df.empty or section not in SECTIONS:
        return df
    from analytics.critical_items import drop_surplus_rows

    return drop_surplus_rows(df.loc[section_row_mask(df, section)])


def apply_section_rate_attribution(
    df: pd.DataFrame | None,
) -> tuple[pd.DataFrame | None, list[dict]]:
    """Zero section rates on rows that may not feed that section.

    Returns (frame copy, changes) — one change dict per (row, column) whose
    non-zero rate was dropped: code, segment, id, column, old value.
    """
    if df is None or df.empty:
        return df, []
    out = df.copy()
    changes: list[dict] = []
    codes = _code_series(out)
    seg = _segments(out)
    for section, cols in SECTION_RATE_COLS.items():
        drop = ~section_row_mask(out, section)
        if not drop.any():
            continue
        for col in cols:
            if col not in out.columns:
                continue
            vals = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
            for idx in out.index[drop & (vals != 0)]:
                changes.append(
                    {
                        "code": codes.at[idx],
                        "segment": seg.at[idx],
                        "id": out.at[idx, "id"] if "id" in out.columns else None,
                        "column": col,
                        "old": float(vals.at[idx]),
                    }
                )
            out.loc[drop, col] = 0
    return out, changes
