"""Fixed wall-stone consumption (user, 1405-08-19).

هر تاندیش بیلت: ۱ عدد «سنگ دیواره بیلت» و ۱ عدد «سنگ دیواره بلوم».
هر تاندیش بلوم: ۲ عدد «سنگ دیواره بلوم».

These are per tundish (patching), not renovation. Applied on a copy of منبع
اصلی before the N-tundish / critical-items rate rollup, so a blank or wrong
rate in the file cannot change the count. Stock is left as recorded.
"""
from __future__ import annotations

import pandas as pd

_PATCH = (
    "billet_renovation",
    "billet_patching",
    "bloom_renovation",
    "bloom_patching",
    "slab_renovation",
    "slab_patching",
    "casting_floor",
)


def _norm(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("\u200c", "")
        .replace("ي", "ی")
        .replace("ى", "ی")
        .replace("ك", "ک")
        .strip()
    )


def wall_stone_kind(*parts: object) -> str | None:
    """``billet`` = سنگ دیواره بیلت، ``bloom`` = سنگ دیواره بلوم، else None."""
    text = " ".join(_norm(p) for p in parts)
    if "سنگ" not in text or "دیواره" not in text:
        return None
    has_billet = "بیلت" in text
    has_bloom = "بلوم" in text
    if has_bloom and not has_billet:
        return "bloom"
    if has_billet and not has_bloom:
        return "billet"
    return None


def _row_text(df: pd.DataFrame, idx) -> str:
    bits = []
    for col in ("keyword", "product_name", "usage_location"):
        if col in df.columns:
            bits.append(df.at[idx, col])
    return " ".join(_norm(b) for b in bits)


def _zero_rates(df: pd.DataFrame, idx) -> None:
    for col in _PATCH:
        if col in df.columns:
            df.at[idx, col] = 0


def apply_wall_stone_rates(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Return a copy with wall-stone patching rates set to the fixed rule.

    A code that has both شرکت and پیمانکار rows keeps the billet rate on the
    company row and the bloom rate on the contractor row, so the two sides are
    not added twice. A code that exists on only one side carries both rates
    (۱ برای بیلت و ۲ برای بلوم) on that row. If the company row is a different
    stone (سنگ دیواره بیلت) the contractor's bloom stone still counts 1 toward
    each billet tundish.
    """
    if df is None or df.empty:
        return df
    from analytics.critical_items import SEGMENT_COMPANY, segment_series
    from analytics.section_rules import _code_series, codes_with_both_segments

    out = df.copy()
    for col in _PATCH:
        if col not in out.columns:
            out[col] = 0
    codes = _code_series(out)
    seg = segment_series(out)
    both = codes_with_both_segments(out)
    kinds = {idx: wall_stone_kind(_row_text(out, idx)) for idx in out.index}
    company_bloom_codes = {
        codes.at[idx]
        for idx, kind in kinds.items()
        if kind == "bloom" and seg.at[idx] == SEGMENT_COMPANY
    }
    for idx, kind in kinds.items():
        if kind is None:
            continue
        code = codes.at[idx]
        _zero_rates(out, idx)
        if kind == "billet":
            out.at[idx, "billet_patching"] = 1
            continue
        if code in both and seg.at[idx] == SEGMENT_COMPANY:
            out.at[idx, "billet_patching"] = 1
        elif code in both:
            out.at[idx, "bloom_patching"] = 2
            if code not in company_bloom_codes:
                out.at[idx, "billet_patching"] = 1
        else:
            out.at[idx, "billet_patching"] = 1
            out.at[idx, "bloom_patching"] = 2
    return out
