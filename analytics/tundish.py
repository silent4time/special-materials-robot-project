"""Pure analytics for tundish (تاندیش) material consumption, inventory, and forecasts."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from config import (
    CRITICAL_DAYS,
    SURPLUS_CATEGORY_CODE,
    SURPLUS_COVER_DAYS,
    SURPLUS_FORECAST_DAYS,
    TUNDISH_TYPES,
    TUNDISH_TYPE_LABELS,
)

CUSTOM_RANGE_RE = re.compile(
    r"از\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})\s*تا\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})",
    re.UNICODE,
)


def parse_custom_range_message(text: str) -> tuple[date, date] | None:
    """Parse «از … تا …» in Jalali or Gregorian; return Gregorian (start, end)."""
    try:
        from bot.jalali import parse_user_date_range
    except Exception:  # noqa: BLE001
        parse_user_date_range = None  # type: ignore[assignment]
    if parse_user_date_range is not None:
        parsed = parse_user_date_range(text)
        if parsed:
            return parsed
    m = CUSTOM_RANGE_RE.search((text or "").strip())
    if not m:
        return None
    try:
        start = date.fromisoformat(m.group(1).replace("/", "-"))
        end = date.fromisoformat(m.group(2).replace("/", "-"))
    except ValueError:
        return None
    if end < start:
        start, end = end, start
    return start, end


def resolve_preset_range(preset: str, today: date | None = None) -> tuple[date, date]:
    """
    preset: today | 7d | 30d
    Returns inclusive (start, end).
    """
    today = today or date.today()
    key = (preset or "").strip().lower()
    if key in {"today", "امروز", "1d", "1"}:
        return today, today
    if key in {"7d", "7", "هفته", "۷ روز", "7 روز"}:
        return today - timedelta(days=6), today
    if key in {"30d", "30", "ماه", "۳۰ روز", "30 روز"}:
        return today - timedelta(days=29), today
    raise ValueError(f"بازه از پیش‌تعریف‌شده نامعتبر: {preset}")


def range_day_count(start: date, end: date) -> int:
    return max(1, (end - start).days + 1)


def _to_dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.date


def _qty(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def filter_by_tundish_type(
    df: pd.DataFrame | None, tundish_type_label_or_key: str | None
) -> pd.DataFrame:
    """Filter rows by a tundish key/label; values are compared canonically."""
    if df is None:
        return pd.DataFrame()
    work = df.copy()
    requested = (tundish_type_label_or_key or "").strip().casefold()
    if not requested or requested in {"all", "همه", "همه تاندیش‌ها", "همه تاندیش ها"}:
        return work
    accepted = {
        **{str(key).casefold(): label for key, label in TUNDISH_TYPES.items()},
        **{str(label).casefold(): label for label in TUNDISH_TYPE_LABELS},
    }
    canonical = accepted.get(requested)
    if canonical is None:
        allowed = "، ".join(TUNDISH_TYPE_LABELS)
        raise ValueError(f"نوع تاندیش نامعتبر است. انواع مجاز: {allowed}")
    if "tundish_type" not in work.columns:
        return work
    values = work["tundish_type"].astype("string").str.strip().str.casefold()
    values = values.map(lambda value: accepted.get(value, value)).fillna("")
    return work.loc[values == canonical].reset_index(drop=True)


def filter_by_date_range(
    df: pd.DataFrame,
    start: date | datetime | str | None,
    end: date | datetime | str | None,
    date_col: str = "date",
) -> pd.DataFrame:
    """Filter rows whose date is within [start, end] inclusive. Empty/missing dates drop out."""
    if df is None or df.empty or date_col not in df.columns:
        return df.copy() if df is not None else pd.DataFrame()
    work = df.copy()
    dates = _to_dates(work[date_col])
    if start is not None:
        if isinstance(start, str):
            start = date.fromisoformat(start)
        elif isinstance(start, datetime):
            start = start.date()
        work = work.loc[dates.notna() & (dates >= start)]
        dates = _to_dates(work[date_col])
    if end is not None:
        if isinstance(end, str):
            end = date.fromisoformat(end)
        elif isinstance(end, datetime):
            end = end.date()
        work = work.loc[dates.notna() & (dates <= end)]
    return work.reset_index(drop=True)



def _jalali_ym_from_row(date_val: object, month_val: object) -> tuple[int, int] | None:
    """Best-effort Jalali (year, month) from plant monthly date/month cells."""
    month_i = None
    if month_val is not None and str(month_val).strip() not in {"", "nan", "None"}:
        try:
            month_i = int(float(str(month_val).strip()))
        except (TypeError, ValueError):
            month_i = None
    year_i = None
    if date_val is not None and str(date_val).strip() not in {"", "nan", "None"}:
        digits = "".join(ch for ch in str(date_val) if ch.isdigit())
        if len(digits) >= 4:
            try:
                year_i = int(digits[:4])
            except ValueError:
                year_i = None
        # YYYYMMDD plant stamp → also recover month when missing
        if month_i is None and len(digits) >= 6:
            try:
                month_i = int(digits[4:6])
            except ValueError:
                month_i = None
    if year_i is None or month_i is None:
        return None
    if not (1200 <= year_i <= 1500 and 1 <= month_i <= 12):
        return None
    return year_i, month_i


def filter_monthly_by_gregorian_range(
    df: pd.DataFrame,
    start: date | None,
    end: date | None,
) -> pd.DataFrame:
    """Filter monthly rows to months overlapping [start, end] (Gregorian inclusive).

    Prefer Jalali year+month from plant ``date``/``month`` columns; fall back to
    ``filter_by_date_range`` when dates look Gregorian.
    """
    if df is None or df.empty or (start is None and end is None):
        return df.copy() if df is not None else pd.DataFrame()
    work = df.copy()
    # Try Jalali month filtering when month or plant-style date present
    has_month = "month" in work.columns
    has_date = "date" in work.columns
    if has_month or has_date:
        try:
            import jdatetime
        except Exception:  # noqa: BLE001
            jdatetime = None  # type: ignore[assignment]
        if jdatetime is not None:
            def _to_j(d: date | None) -> tuple[int, int] | None:
                if d is None:
                    return None
                j = jdatetime.date.fromgregorian(date=d)
                return j.year, j.month

            start_ym = _to_j(start) or (0, 1)
            end_ym = _to_j(end) or (9999, 12)
            lo = start_ym[0] * 12 + start_ym[1]
            hi = end_ym[0] * 12 + end_ym[1]
            mask = []
            for _, row in work.iterrows():
                ym = _jalali_ym_from_row(row.get("date") if has_date else None, row.get("month") if has_month else None)
                if ym is None:
                    mask.append(False)
                else:
                    mask.append(lo <= (ym[0] * 12 + ym[1]) <= hi)
            filtered = work.loc[mask]
            if not filtered.empty or has_month:
                return filtered.reset_index(drop=True)
    return filter_by_date_range(work, start, end)


def daily_rates(
    tank_df: pd.DataFrame | None,
    monthly_df: pd.DataFrame | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """
    Average daily consumption per material (and tundish when present).

    Primary source: tank/tundish consumption (quantity / distinct calendar days in span).
    Optional monthly: quantity / 30 added as supplemental rows when material absent from tank.
    """
    rows: list[dict[str, Any]] = []

    if tank_df is not None and not tank_df.empty and "quantity" in tank_df.columns:
        work = tank_df.copy()
        if start is not None or end is not None:
            work = filter_by_date_range(work, start, end)
        if not work.empty and "material_name" in work.columns:
            work["_qty"] = _qty(work["quantity"])
            has_date = "date" in work.columns
            if has_date:
                work["_d"] = _to_dates(work["date"])
            group_cols = ["material_name"]
            if "tundish_type" in work.columns:
                group_cols.append("tundish_type")
            if "tundish_id" in work.columns:
                group_cols.append("tundish_id")
            if "unit" in work.columns:
                group_cols.append("unit")

            for keys, g in work.groupby(group_cols, dropna=False):
                if not isinstance(keys, tuple):
                    keys = (keys,)
                payload = dict(zip(group_cols, keys))
                total = float(g["_qty"].sum())
                if has_date and g["_d"].notna().any():
                    dmin = g["_d"].min()
                    dmax = g["_d"].max()
                    span = max(1, (dmax - dmin).days + 1) if dmin and dmax else 1
                else:
                    span = 1
                payload.update(
                    {
                        "total_qty": total,
                        "days_span": int(span),
                        "avg_daily": total / span,
                        "source": "tank",
                    }
                )
                rows.append(payload)

    if monthly_df is not None and not monthly_df.empty and "quantity" in monthly_df.columns:
        work = monthly_df.copy()
        if start is not None or end is not None:
            work = filter_monthly_by_gregorian_range(work, start, end)
        if "material_name" not in work.columns:
            pass
        else:
            work["_qty"] = _qty(work["quantity"])
            existing = {
                (r.get("material_name"), r.get("tundish_type"), r.get("tundish_id"), r.get("unit"))
                for r in rows
            }
            group_cols = ["material_name"]
            if "tundish_type" in work.columns:
                group_cols.append("tundish_type")
            if "unit" in work.columns:
                group_cols.append("unit")
            for keys, g in work.groupby(group_cols, dropna=False):
                if not isinstance(keys, tuple):
                    keys = (keys,)
                payload = dict(zip(group_cols, keys))
                key = (payload.get("material_name"), payload.get("tundish_type"), None, payload.get("unit"))
                if key in existing or any(
                    r.get("material_name") == payload.get("material_name")
                    and r.get("tundish_id") is None
                    for r in rows
                ):
                    # prefer tank-derived rates for same material without tundish split
                    if any(r.get("material_name") == payload.get("material_name") for r in rows):
                        continue
                total = float(g["_qty"].sum())
                payload.update(
                    {
                        "tundish_id": None,
                        "total_qty": total,
                        "days_span": 30,
                        "avg_daily": total / 30.0,
                        "source": "monthly",
                    }
                )
                rows.append(payload)

    if not rows:
        return pd.DataFrame(
            columns=[
                "material_name",
                "tundish_type",
                "tundish_id",
                "unit",
                "total_qty",
                "days_span",
                "avg_daily",
                "source",
            ]
        )
    out = pd.DataFrame(rows)
    for col in ("material_name", "tundish_type", "tundish_id", "unit", "source"):
        if col not in out.columns:
            out[col] = None
    return out.sort_values(
        by=["material_name", "tundish_type", "tundish_id"], kind="stable"
    ).reset_index(drop=True)


def period_consumption(
    tank_df: pd.DataFrame | None,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Sum consumption in date range, grouped by material and tundish."""
    if tank_df is None or tank_df.empty:
        return pd.DataFrame(
            columns=["material_name", "tundish_type", "tundish_id", "unit", "quantity", "start", "end"]
        )
    work = filter_by_date_range(tank_df, start, end)
    if work.empty or "material_name" not in work.columns or "quantity" not in work.columns:
        return pd.DataFrame(
            columns=["material_name", "tundish_type", "tundish_id", "unit", "quantity", "start", "end"]
        )
    work = work.copy()
    work["_qty"] = _qty(work["quantity"])
    group_cols = ["material_name"]
    if "tundish_type" in work.columns:
        group_cols.append("tundish_type")
    if "tundish_id" in work.columns:
        group_cols.append("tundish_id")
    if "unit" in work.columns:
        group_cols.append("unit")
    agg = (
        work.groupby(group_cols, dropna=False)["_qty"]
        .sum()
        .reset_index()
        .rename(columns={"_qty": "quantity"})
    )
    agg["start"] = start.isoformat()
    agg["end"] = end.isoformat()
    return agg.sort_values(by=[c for c in ["material_name", "tundish_type", "tundish_id"] if c in agg.columns], kind="stable").reset_index(
        drop=True
    )



def _normalize_category_code_value(value: object) -> str | None:
    """Normalize category to a 4-digit string (Excel floats like 1800.0 → 1800)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit() and len(text) <= 4:
        text = text.zfill(4)
    if len(text) == 4 and text.isdigit():
        return text
    return None


def _pick_category_code(series: pd.Series) -> str | None:
    """Prefer SURPLUS_CATEGORY_CODE when a material has multiple category codes."""
    codes: list[str] = []
    for value in series:
        norm = _normalize_category_code_value(value)
        if norm and norm not in codes:
            codes.append(norm)
    if SURPLUS_CATEGORY_CODE in codes:
        return SURPLUS_CATEGORY_CODE
    return codes[0] if codes else None


def remaining(inventory_df: pd.DataFrame | None) -> pd.DataFrame:
    """
    Remaining stock from warehouse / product inventory.
    Prefers product_name, then legacy item_code_desc, then material_name as join key
    (matched to consumption material_name).

    When ``category_code`` is present on the inventory frame it is preserved
    (groupby: prefer SURPLUS_CATEGORY_CODE if multiple codes share a material).
    """
    empty_cols = ["material_name", "remaining_qty", "unit", "location", "category_code"]
    if inventory_df is None or inventory_df.empty:
        return pd.DataFrame(columns=empty_cols)
    work = inventory_df.copy()
    name_col = None
    for candidate in ("product_name", "item_code_desc", "material_name"):
        if candidate in work.columns:
            name_col = candidate
            break
    if not name_col or "quantity" not in work.columns:
        return pd.DataFrame(columns=empty_cols)
    work["_qty"] = _qty(work["quantity"])
    group_cols = [name_col]
    if "unit" in work.columns:
        group_cols.append("unit")
    if "location" in work.columns:
        group_cols.append("location")
    has_cat = "category_code" in work.columns
    if has_cat:
        agg = (
            work.groupby(group_cols, dropna=False)
            .agg(
                remaining_qty=("_qty", "sum"),
                category_code=("category_code", _pick_category_code),
            )
            .reset_index()
            .rename(columns={name_col: "material_name"})
        )
    else:
        agg = (
            work.groupby(group_cols, dropna=False)["_qty"]
            .sum()
            .reset_index()
            .rename(columns={"_qty": "remaining_qty", name_col: "material_name"})
        )
        agg["category_code"] = None
    if "location" not in agg.columns:
        agg["location"] = None
    if "unit" not in agg.columns:
        agg["unit"] = None
    # also roll up per material (sum locations) for cover calculations
    return agg.sort_values(by=["material_name"], kind="stable").reset_index(drop=True)



def apply_inventory_ledger(
    inventory_df: pd.DataFrame | None,
    ledger_by_id: dict[str, float] | None = None,
    ledger_by_name: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    Apply inventory_ledger deltas to a warehouse inventory DataFrame (immutable base).

    Matching preference: ``id`` column first, then product_name / legacy item_code_desc /
    material_name. Delta is added to quantity (issues are stored as negative).
    """
    if inventory_df is None or inventory_df.empty:
        return inventory_df.copy() if inventory_df is not None else pd.DataFrame()
    by_id = {str(k).strip(): float(v) for k, v in (ledger_by_id or {}).items() if str(k).strip()}
    by_name = {
        str(k).strip().casefold(): float(v)
        for k, v in (ledger_by_name or {}).items()
        if str(k).strip()
    }
    if not by_id and not by_name:
        return inventory_df.copy()
    work = inventory_df.copy()
    if "quantity" not in work.columns:
        return work

    name_col = None
    for candidate in ("product_name", "item_code_desc", "material_name"):
        if candidate in work.columns:
            name_col = candidate
            break

    def row_delta(row: pd.Series) -> float:
        if "id" in work.columns:
            iid = row.get("id")
            if iid is not None and not (isinstance(iid, float) and pd.isna(iid)):
                key = str(iid).strip()
                if key and key.casefold() != "nan" and key in by_id:
                    return by_id[key]
        if name_col:
            name = row.get(name_col)
            if name is not None and not (isinstance(name, float) and pd.isna(name)):
                nkey = str(name).strip().casefold()
                if nkey and nkey in by_name:
                    return by_name[nkey]
        return 0.0

    deltas = work.apply(row_delta, axis=1)
    qty = pd.to_numeric(work["quantity"], errors="coerce").fillna(0.0) + deltas
    work["quantity"] = qty
    return work


def _material_daily_avg(rates_df: pd.DataFrame) -> pd.DataFrame:
    """Collapse tundish-level rates to per-material avg_daily + unit."""
    if rates_df is None or rates_df.empty:
        return pd.DataFrame(columns=["material_name", "avg_daily", "unit"])
    work = rates_df.copy()
    work["avg_daily"] = pd.to_numeric(work["avg_daily"], errors="coerce").fillna(0.0)
    group_cols = ["material_name"]
    if "unit" in work.columns:
        # take first non-null unit per material after sum
        summed = (
            work.groupby("material_name", dropna=False)
            .agg(avg_daily=("avg_daily", "sum"), unit=("unit", "first"))
            .reset_index()
        )
        return summed
    return (
        work.groupby(group_cols, dropna=False)["avg_daily"]
        .sum()
        .reset_index()
    )


def _material_remaining(remaining_df: pd.DataFrame) -> pd.DataFrame:
    empty_cols = ["material_name", "remaining_qty", "unit", "category_code"]
    if remaining_df is None or remaining_df.empty:
        return pd.DataFrame(columns=empty_cols)
    work = remaining_df.copy()
    work["remaining_qty"] = pd.to_numeric(work["remaining_qty"], errors="coerce").fillna(0.0)
    agg_map: dict[str, tuple[str, Any]] = {
        "remaining_qty": ("remaining_qty", "sum"),
        "unit": ("unit", "first"),
    }
    if "category_code" in work.columns:
        agg_map["category_code"] = ("category_code", _pick_category_code)
    out = work.groupby("material_name", dropna=False).agg(**agg_map).reset_index()
    if "category_code" not in out.columns:
        out["category_code"] = None
    return out


def critical_materials(
    rates_df: pd.DataFrame | None,
    remaining_df: pd.DataFrame | None,
    critical_days: float | int = CRITICAL_DAYS,
) -> pd.DataFrame:
    """
    Materials where days_of_cover = remaining / avg_daily < critical_days.
    Zero/negative avg_daily → not critical (unknown consumption).
    """
    rates_m = _material_daily_avg(rates_df if rates_df is not None else pd.DataFrame())
    rem_m = _material_remaining(remaining_df if remaining_df is not None else pd.DataFrame())
    if rem_m.empty:
        return pd.DataFrame(
            columns=[
                "material_name",
                "remaining_qty",
                "avg_daily",
                "days_of_cover",
                "unit",
                "critical",
            ]
        )
    merged = rem_m.merge(rates_m, on="material_name", how="left", suffixes=("", "_rate"))
    if "unit" not in merged.columns or merged["unit"].isna().all():
        if "unit_rate" in merged.columns:
            merged["unit"] = merged["unit_rate"]
    merged["avg_daily"] = pd.to_numeric(merged.get("avg_daily"), errors="coerce").fillna(0.0)
    merged["remaining_qty"] = pd.to_numeric(merged["remaining_qty"], errors="coerce").fillna(0.0)

    def cover(row: pd.Series) -> float:
        avg = float(row["avg_daily"])
        if avg <= 0:
            return float("inf")
        return float(row["remaining_qty"]) / avg

    merged["days_of_cover"] = merged.apply(cover, axis=1)
    merged["critical"] = merged.apply(
        lambda r: bool(r["avg_daily"] > 0 and r["days_of_cover"] < float(critical_days)),
        axis=1,
    )
    crit = merged.loc[merged["critical"]].copy()
    crit = crit.sort_values(by=["days_of_cover", "material_name"], kind="stable")
    cols = ["material_name", "remaining_qty", "avg_daily", "days_of_cover", "unit", "critical"]
    for c in cols:
        if c not in crit.columns:
            crit[c] = None
    return crit[cols].reset_index(drop=True)


def forecast(
    rates_df: pd.DataFrame | None,
    days: int | float,
) -> pd.DataFrame:
    """forecast_need = avg_daily_consumption * requested_days, per material/tundish."""
    days = max(0.0, float(days))
    if rates_df is None or rates_df.empty:
        return pd.DataFrame(
            columns=["material_name", "tundish_type", "tundish_id", "unit", "avg_daily", "days", "forecast_need"]
        )
    work = rates_df.copy()
    work["avg_daily"] = pd.to_numeric(work["avg_daily"], errors="coerce").fillna(0.0)
    work["days"] = days
    work["forecast_need"] = work["avg_daily"] * days
    cols = ["material_name"]
    if "tundish_type" in work.columns:
        cols.append("tundish_type")
    cols += ["tundish_id", "unit", "avg_daily", "days", "forecast_need"]
    for c in cols:
        if c not in work.columns:
            work[c] = None
    return work[cols].sort_values(
        by=[c for c in ["material_name", "tundish_type", "tundish_id"] if c in work.columns], kind="stable"
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Priority (اولویت) in منبع اصلی
# ---------------------------------------------------------------------------
# 0 = «بدون اولویت»: the item is NOT used in request suggestions / material
# requests (it is not the highest priority). 1 is highest, then 2, 3, ...
# A blank cell is stored as 1 on upload.
NO_PRIORITY = 0
# Critical-point aggregation threshold (see critical_point_category_totals).
CRITICAL_POINT_MIN_QTY = 100.0


def _ana_key(value: object) -> str:
    """Comparison key: yeh/kaf, collapsed spaces, casefold, drop trailing .0."""
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.casefold() in {"nan", "none", "nat"}:
        return ""
    text = text.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    text = " ".join(text.split())
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        text = text[:-2]
    return text.casefold()


def _priority_values(series: pd.Series) -> pd.Series:
    """Numeric priority; blank → 1 (same default as upload)."""
    return pd.to_numeric(series, errors="coerce").fillna(1)


def is_no_priority(value: object) -> bool:
    """True when اولویت is 0 (excluded from suggestions)."""
    num = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return bool(pd.notna(num) and float(num) == float(NO_PRIORITY))


def priority_excluded_keys(inventory_df: pd.DataFrame | None) -> set[str]:
    """Normalized ids / product names whose every inventory row has priority 0.

    An id (or name) that also appears on a row with priority ≥ 1 stays
    eligible. Keys are compared with ``_ana_key``.
    """
    if (
        inventory_df is None
        or not isinstance(inventory_df, pd.DataFrame)
        or inventory_df.empty
        or "priority" not in inventory_df.columns
    ):
        return set()
    prio = _priority_values(inventory_df["priority"])
    zero_mask = prio == float(NO_PRIORITY)
    out: set[str] = set()
    for col in ("id", "product_name", "item_code_desc", "material_name"):
        if col not in inventory_df.columns:
            continue
        keys = inventory_df[col].map(_ana_key)
        zero_keys = set(keys[zero_mask]) - {""}
        live_keys = set(keys[~zero_mask]) - {""}
        out |= zero_keys - live_keys
    return out


def _material_is_excluded(material_name: object, excluded: set[str]) -> bool:
    """Match «id - name» consumption labels against priority-0 keys."""
    if not excluded:
        return False
    key = _ana_key(material_name)
    if not key:
        return False
    if key in excluded:
        return True
    if " - " in key:
        head, tail = key.split(" - ", 1)
        if head.strip() in excluded or tail.strip() in excluded:
            return True
    return False


def drop_priority_zero_materials(
    df: pd.DataFrame | None,
    inventory_df: pd.DataFrame | None,
    *,
    name_col: str = "material_name",
) -> pd.DataFrame | None:
    """Remove rows whose material maps to a priority-0 inventory item."""
    if df is None or df.empty or name_col not in df.columns:
        return df
    excluded = priority_excluded_keys(inventory_df)
    if not excluded:
        return df
    mask = df[name_col].map(lambda v: _material_is_excluded(v, excluded))
    return df.loc[~mask].reset_index(drop=True)


def critical_point_category_totals(inventory_df: pd.DataFrame | None) -> pd.DataFrame:
    """Per category_code stock total for critical-point / اقلام بحرانی.

    Rule (spec): sum quantity over rows with the same کد دسته بندی, EXCLUDING
    rows with quantity < 100 OR priority 0. Returns category_code,
    total_quantity, row_count.

    Used by ``analytics.critical_items`` for threshold highlighting; display
    stock in the critical-items report is the unfiltered real sum.
    """
    cols = ["category_code", "total_quantity", "row_count"]
    if (
        inventory_df is None
        or inventory_df.empty
        or "category_code" not in inventory_df.columns
        or "quantity" not in inventory_df.columns
    ):
        return pd.DataFrame(columns=cols)
    work = inventory_df.copy()
    work["_qty"] = pd.to_numeric(work["quantity"], errors="coerce")
    prio = (
        _priority_values(work["priority"])
        if "priority" in work.columns
        else pd.Series(1, index=work.index)
    )
    keep = work["_qty"].notna() & (work["_qty"] >= CRITICAL_POINT_MIN_QTY) & (
        prio != float(NO_PRIORITY)
    )
    work = work.loc[keep]
    work["category_code"] = work["category_code"].map(_ana_key)
    work = work.loc[work["category_code"] != ""]
    if work.empty:
        return pd.DataFrame(columns=cols)
    out = (
        work.groupby("category_code")
        .agg(total_quantity=("_qty", "sum"), row_count=("_qty", "size"))
        .reset_index()
        .sort_values("category_code")
    )
    return out[cols].reset_index(drop=True)


def suggest_requests(
    rates_df: pd.DataFrame | None,
    remaining_df: pd.DataFrame | None,
    days: int | float,
    *,
    inventory_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    suggest_qty = max(0, forecast_need - remaining_inventory) at material level.
    forecast_need uses sum of per-tundish avg_daily * days.

    When ``inventory_df`` (منبع اصلی) is given, materials whose inventory
    rows all have اولویت 0 are skipped (0 = not used in suggestions).
    """
    days = max(0.0, float(days))
    if inventory_df is not None:
        rates_df = drop_priority_zero_materials(rates_df, inventory_df)
    rates_m = _material_daily_avg(rates_df if rates_df is not None else pd.DataFrame())
    rem_m = _material_remaining(remaining_df if remaining_df is not None else pd.DataFrame())
    if rates_m.empty:
        return pd.DataFrame(
            columns=[
                "material_name",
                "unit",
                "avg_daily",
                "days",
                "forecast_need",
                "remaining_qty",
                "suggest_qty",
            ]
        )
    merged = rates_m.merge(rem_m, on="material_name", how="left", suffixes=("", "_inv"))
    if "unit_inv" in merged.columns:
        merged["unit"] = merged["unit"].fillna(merged["unit_inv"])
    merged["remaining_qty"] = pd.to_numeric(
        merged.get("remaining_qty"), errors="coerce"
    ).fillna(0.0)
    merged["avg_daily"] = pd.to_numeric(merged["avg_daily"], errors="coerce").fillna(0.0)
    merged["days"] = days
    merged["forecast_need"] = merged["avg_daily"] * days
    merged["suggest_qty"] = (merged["forecast_need"] - merged["remaining_qty"]).clip(lower=0.0)
    cols = [
        "material_name",
        "unit",
        "avg_daily",
        "days",
        "forecast_need",
        "remaining_qty",
        "suggest_qty",
    ]
    out = merged[cols].sort_values(by=["suggest_qty", "material_name"], ascending=[False, True])
    return out.reset_index(drop=True)


def format_suggest_list_fa(suggest_df: pd.DataFrame, limit: int = 20) -> str:
    """Short Persian bullet list for chat replies."""
    if suggest_df is None or suggest_df.empty:
        return "پیشنهادی نیست — موجودی برای بازه درخواست کافی به‌نظر می‌رسد."
    lines: list[str] = []
    shown = suggest_df.loc[suggest_df["suggest_qty"] > 0].head(limit)
    if shown.empty:
        return "پیشنهادی نیست — موجودی برای بازه درخواست کافی به‌نظر می‌رسد."
    for _, row in shown.iterrows():
        unit = row.get("unit") or ""
        lines.append(
            f"• {row['material_name']}: {float(row['suggest_qty']):.2f} {unit}".strip()
        )
    return "\n".join(lines)


def missing_files_for_goal(goal: str, completeness: dict[str, bool]) -> list[str]:
    """Which data sources are required for a given analytics goal.

    ``completeness`` keys: FILE_TYPES plus optional ``site_stock`` (interactive
    daily site inventory). See ``analytics.frames.data_completeness``.

    Rates (daily / forecast / suggest / remaining critical) accept tank Excel
    **or** monthly (÷ 30). Remaining prefers interactive site stock, else
    product_inventory / tank Excel. Suggest / surplus / full plant facts need
    ``product_inventory`` (منبع اصلی). Period series needs tank Excel rows.
    goal: daily | period | remaining_critical | forecast | suggest | surplus | full
    """
    from config import FILE_TYPES

    def label(k: str) -> str:
        return FILE_TYPES[k]["label_fa"]

    def has(k: str) -> bool:
        return bool(completeness.get(k))

    has_tank_excel = has("tank_consumption")
    has_site = has("site_stock")
    has_inv = has("product_inventory")
    has_monthly = has("monthly_consumption")
    has_rates = has_tank_excel or has_monthly
    has_remaining = has_site or has_tank_excel or has_inv

    missing: list[str] = []

    if goal == "daily":
        if not has_rates:
            missing.append(f"{label('tank_consumption')} یا {label('monthly_consumption')}")
    elif goal == "period":
        # Dated period series needs tank Excel; monthly/site snapshot insufficient.
        if not has_tank_excel:
            missing.append(f"{label('tank_consumption')} (Excel تاریخی مصرف)")
    elif goal == "forecast":
        if not has_rates:
            missing.append(f"{label('tank_consumption')} یا {label('monthly_consumption')}")
    elif goal == "remaining_critical":
        if not has_remaining:
            missing.append(
                f"{label('tank_consumption')} یا {label('product_inventory')}"
            )
        if not has_rates:
            missing.append(
                f"{label('tank_consumption')} یا {label('monthly_consumption')}"
            )
    elif goal == "suggest":
        # Material request: canonical warehouse + consumption rates.
        # Do NOT demand tank Excel when monthly rates exist.
        if not has_inv:
            missing.append(label("product_inventory"))
        if not has_rates:
            missing.append(
                f"{label('tank_consumption')} یا {label('monthly_consumption')}"
            )
    elif goal == "surplus":
        if not has_inv:
            missing.append(label("product_inventory"))
    elif goal == "full":
        if not has_inv:
            missing.append(label("product_inventory"))
        if not has_rates:
            missing.append(
                f"{label('tank_consumption')} یا {label('monthly_consumption')}"
            )
    else:
        required = ["tank_consumption"]
        missing = [label(k) for k in required if not has(k) and k in FILE_TYPES]
    return missing



def surplus_materials(
    rates_df: pd.DataFrame | None,
    remaining_df: pd.DataFrame | None,
    *,
    critical_days: float | int = CRITICAL_DAYS,
    surplus_cover_days: float | int | None = None,
    forecast_days: float | int | None = None,
) -> pd.DataFrame:
    """Identify surplus (مازاد) materials.

    A material is surplus when ANY of:
      1) category_code == SURPLUS_CATEGORY_CODE (1800) → «کد دسته ۱۸۰۰ — اقلام مازاد»
      2) days_of_cover > max(CRITICAL_DAYS * 3, SURPLUS_COVER_DAYS) and avg_daily > 0
      3) remaining_qty > forecast_need for SURPLUS_FORECAST_DAYS (default 30)
      4) has stock but no matching consumption (avg_daily == 0) → flag «مازاد/بدون مصرف»

    Rows with rem_q <= 0 are skipped. Category-1800 reason is primary when present.
    Documented in Persian UI as «گزارش مواد مازاد».
    """
    cover_threshold = float(
        surplus_cover_days
        if surplus_cover_days is not None
        else max(float(critical_days) * 3.0, float(SURPLUS_COVER_DAYS))
    )
    days = float(forecast_days if forecast_days is not None else SURPLUS_FORECAST_DAYS)

    rates_m = _material_daily_avg(rates_df if rates_df is not None else pd.DataFrame())
    rem_m = _material_remaining(remaining_df if remaining_df is not None else pd.DataFrame())
    cols = [
        "material_name",
        "remaining_qty",
        "avg_daily",
        "days_of_cover",
        "forecast_need",
        "surplus_qty",
        "unit",
        "surplus_reason",
    ]
    if rem_m.empty:
        return pd.DataFrame(columns=cols)

    merged = rem_m.merge(rates_m, on="material_name", how="left", suffixes=("", "_rate"))
    if "unit" not in merged.columns or merged["unit"].isna().all():
        if "unit_rate" in merged.columns:
            merged["unit"] = merged["unit_rate"]
    merged["avg_daily"] = pd.to_numeric(merged.get("avg_daily"), errors="coerce").fillna(0.0)
    merged["remaining_qty"] = pd.to_numeric(merged["remaining_qty"], errors="coerce").fillna(0.0)
    merged["forecast_need"] = merged["avg_daily"] * days

    def cover(row: pd.Series) -> float:
        avg = float(row["avg_daily"])
        if avg <= 0:
            return float("inf")
        return float(row["remaining_qty"]) / avg

    merged["days_of_cover"] = merged.apply(cover, axis=1)

    reasons: list[str] = []
    keep: list[bool] = []
    surplus_qty: list[float] = []
    has_cat_col = "category_code" in merged.columns
    for _, row in merged.iterrows():
        rem_q = float(row["remaining_qty"])
        avg = float(row["avg_daily"])
        cover_d = float(row["days_of_cover"])
        need = float(row["forecast_need"])
        flags: list[str] = []
        if rem_q <= 0:
            keep.append(False)
            reasons.append("")
            surplus_qty.append(0.0)
            continue
        cat = (
            _normalize_category_code_value(row.get("category_code"))
            if has_cat_col
            else None
        )
        is_surplus_cat = cat == SURPLUS_CATEGORY_CODE
        # Category 1800 = اقلام مازاد — always surplus (primary reason).
        if is_surplus_cat:
            flags.append("کد دسته ۱۸۰۰ — اقلام مازاد")
        if avg <= 0:
            if not is_surplus_cat:
                flags.append("مازاد/بدون مصرف")
            elif "مازاد/بدون مصرف" not in flags:
                # Keep category reason primary; skip redundant no-consumption flag.
                pass
        else:
            if cover_d > cover_threshold:
                flags.append(f"پوشش بالا (> {cover_threshold:g} روز)")
            if rem_q > need:
                flags.append(f"بیش از نیاز {days:g} روز")
        if flags:
            keep.append(True)
            reasons.append("؛ ".join(flags))
            surplus_qty.append(max(0.0, rem_q - need) if avg > 0 else rem_q)
        else:
            keep.append(False)
            reasons.append("")
            surplus_qty.append(0.0)

    merged["surplus_reason"] = reasons
    merged["surplus_qty"] = surplus_qty
    out = merged.loc[keep].copy()
    out = out.sort_values(by=["surplus_qty", "material_name"], ascending=[False, True], kind="stable")
    for c in cols:
        if c not in out.columns:
            out[c] = None
    return out[cols].reset_index(drop=True)


def format_surplus_list_fa(surplus_df: pd.DataFrame, limit: int = 30) -> str:
    """Persian bullet list for surplus materials chat reply."""
    if surplus_df is None or surplus_df.empty:
        return "ماده مازادی شناسایی نشد."
    lines: list[str] = []
    for _, row in surplus_df.head(limit).iterrows():
        unit = row.get("unit") or ""
        cover = row.get("days_of_cover")
        if cover == float("inf"):
            cover_s = "∞"
        else:
            cover_s = f"{float(cover):.1f}"
        reason = row.get("surplus_reason") or ""
        lines.append(
            f"• {row['material_name']}: موجودی {float(row['remaining_qty']):.2f} {unit} | "
            f"مازاد≈ {float(row['surplus_qty']):.2f} | پوشش≈ {cover_s} روز"
            + (f" | {reason}" if reason else "")
        )
    return "\n".join(lines)
