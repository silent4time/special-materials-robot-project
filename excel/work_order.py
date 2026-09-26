"""Work-order (سفارش کار) → tundish group mapping for slab / bloom / billet."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

import pandas as pd

from config import SITE_STOCK_GROUP_KEYS, TUNDISH_TYPES

# Exact 10-digit plant codes (also tolerate Excel float trailing .0 after normalize).
WORK_ORDER_TO_GROUP: dict[str, str] = {
    "1102010000": "slab",   # اسلب
    "1102020000": "bloom",  # بلوم
    "1102030000": "billet", # بیلت
}

GROUP_TO_WORK_ORDER: dict[str, str] = {v: k for k, v in WORK_ORDER_TO_GROUP.items()}

# Short Persian labels (اسلب / بلوم / بیلت)
GROUP_LABELS_FA: dict[str, str] = {
    "slab": "اسلب",
    "bloom": "بلوم",
    "billet": "بیلت",
}

# Summary section row labels
CONSUMPTION_LABELS_FA: dict[str, str] = {
    "slab": "مصرف مواد اسلب",
    "bloom": "مصرف مواد بلوم",
    "billet": "مصرف مواد بیلت",
}

UNKNOWN_GROUP = "unknown"
UNKNOWN_LABEL_FA = "مصرف مواد بدون سفارش کار شناخته‌شده"

# Marker for auto assignments derived from monthly work_order (manual wins).
WO_AUTO_ASSIGNED_BY = "system:work_order"

_PERSIAN_ARABIC_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def normalize_work_order(value: object) -> str | None:
    """Normalize سفارش کار to a 10-digit Latin string, or None if blank/invalid."""
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass

    # Integers / floats from Excel (e.g. 1102010000.0)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        if value != value:  # NaN
            return None
        as_int = int(round(value))
        if abs(value - as_int) < 1e-9:
            text = str(as_int)
        else:
            text = str(value).strip()
    else:
        text = str(value).strip()

    if not text or text.casefold() in {"nan", "none", "nat"}:
        return None

    text = text.translate(_PERSIAN_ARABIC_DIGITS)
    text = "".join(text.split())  # strip all spaces
    # tolerate trailing .0 from stringified floats
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        text = text[:-2]
    # keep digits only (drop commas etc.)
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    # plant codes are 10 digits; accept shorter if exact match after zfill? No — exact map only.
    if digits in WORK_ORDER_TO_GROUP:
        return digits
    # scientific / float residue already handled; reject unknown digit strings
    return digits if len(digits) == 10 else digits


def group_for_work_order(value: object) -> str | None:
    """Return tundish group key (slab/bloom/billet) or None if unknown/blank."""
    code = normalize_work_order(value)
    if not code:
        return None
    return WORK_ORDER_TO_GROUP.get(code)


def label_fa_for_group(group: str | None) -> str:
    if not group or group == UNKNOWN_GROUP:
        return UNKNOWN_LABEL_FA
    return CONSUMPTION_LABELS_FA.get(group) or GROUP_LABELS_FA.get(group) or str(group)


def tundish_type_label_for_work_order(value: object) -> str | None:
    """Map work_order → canonical TUNDISH_TYPES label (تاندیش اسلب/…)."""
    group = group_for_work_order(value)
    if not group:
        return None
    return TUNDISH_TYPES.get(group)


def dominant_work_order(values: Iterable[object], weights: Iterable[float] | None = None) -> object | None:
    """Pick dominant WO by absolute weight (default weight=1); first wins ties."""
    best_code: str | None = None
    best_weight = -1.0
    best_raw: object | None = None
    first_raw: object | None = None
    weight_iter = iter(weights) if weights is not None else None
    for raw in values:
        w = 1.0
        if weight_iter is not None:
            try:
                w = abs(float(next(weight_iter)))
            except (StopIteration, TypeError, ValueError):
                w = 1.0
        code = normalize_work_order(raw)
        if first_raw is None and (code or (raw is not None and str(raw).strip())):
            first_raw = raw
        if not code:
            continue
        if w > best_weight or (w == best_weight and best_code is None):
            best_weight = w
            best_code = code
            best_raw = raw
    if best_raw is not None:
        return best_raw
    return first_raw


def build_item_to_group_map(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    id_col: str = "کد کالا",
    work_order_col: str = "سفارش کار",
    weight_col: str | None = None,
) -> dict[str, str]:
    """Map item id → slab/bloom/billet using dominant work_order per item.

    Rows without a mappable work_order are omitted. Mixed WOs: largest absolute
    weight wins (weight_col or 1).
    """
    if isinstance(rows, pd.DataFrame):
        if rows.empty or id_col not in rows.columns:
            return {}
        frame = rows
    else:
        frame = pd.DataFrame(list(rows))
        if frame.empty or id_col not in frame.columns:
            return {}

    scores: dict[str, dict[str, float]] = {}
    first_group: dict[str, str] = {}
    for _, row in frame.iterrows():
        raw_id = row.get(id_col)
        if raw_id is None:
            continue
        try:
            if bool(pd.isna(raw_id)):
                continue
        except (TypeError, ValueError):
            pass
        iid = str(raw_id).strip()
        if iid.endswith(".0") and iid[:-2].replace("-", "").isalnum():
            iid = iid[:-2]
        if not iid or iid.casefold() == "nan":
            continue
        group = group_for_work_order(row.get(work_order_col))
        if not group or group not in SITE_STOCK_GROUP_KEYS:
            continue
        w = 1.0
        if weight_col and weight_col in frame.columns:
            try:
                w = abs(float(row.get(weight_col) or 0))
            except (TypeError, ValueError):
                w = 1.0
        bucket = scores.setdefault(iid, {})
        bucket[group] = bucket.get(group, 0.0) + w
        first_group.setdefault(iid, group)

    out: dict[str, str] = {}
    for iid, bucket in scores.items():
        best = max(bucket.items(), key=lambda kv: (kv[1], -list(GROUP_LABELS_FA).index(kv[0]) if kv[0] in GROUP_LABELS_FA else 0))
        out[iid] = best[0] if best[1] > 0 else first_group.get(iid, best[0])
    return out


def tundish_kg_totals_from_items(items: pd.DataFrame) -> dict[str, dict[str, float | int]]:
    """Sum مصرف_کیلوگرم on aggregated item rows by WO group (+ unknown).

    Returns dict keyed by slab/bloom/billet/unknown with keys kg, count.
    """
    result = {
        "slab": {"kg": 0.0, "count": 0},
        "bloom": {"kg": 0.0, "count": 0},
        "billet": {"kg": 0.0, "count": 0},
        UNKNOWN_GROUP: {"kg": 0.0, "count": 0},
    }
    if items is None or items.empty:
        return result
    wo_col = "سفارش کار" if "سفارش کار" in items.columns else "work_order"
    kg_col = "مصرف_کیلوگرم" if "مصرف_کیلوگرم" in items.columns else "kg"
    for _, row in items.iterrows():
        group = group_for_work_order(row.get(wo_col)) or UNKNOWN_GROUP
        try:
            kg = float(row.get(kg_col) or 0)
        except (TypeError, ValueError):
            kg = 0.0
        result[group]["kg"] = float(result[group]["kg"]) + kg
        result[group]["count"] = int(result[group]["count"]) + 1
    return result
