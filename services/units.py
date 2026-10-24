"""Display-only Persian unit names («NO» → «عدد»). Stored data is never changed."""
from __future__ import annotations

from typing import Any

_UNIT_FA: dict[str, str] = {
    "NO": "عدد", "NOS": "عدد", "NO.": "عدد", "NUMBER": "عدد", "PC": "عدد", "PCS": "عدد",
    "PCE": "عدد", "EA": "عدد", "EACH": "عدد", "UNIT": "عدد", "ST": "عدد",
    "KG": "کیلوگرم", "KGS": "کیلوگرم", "KILOGRAM": "کیلوگرم",
    "G": "گرم", "GR": "گرم", "GRAM": "گرم",
    "TON": "تن", "TONS": "تن", "TONNE": "تن", "T": "تن", "MT": "تن",
    "M": "متر", "MTR": "متر", "METER": "متر", "METRE": "متر",
    "M2": "متر مربع", "SQM": "متر مربع", "M3": "متر مکعب", "CBM": "متر مکعب",
    "CM": "سانتی‌متر", "MM": "میلی‌متر",
    "L": "لیتر", "LT": "لیتر", "LTR": "لیتر", "LIT": "لیتر", "LITER": "لیتر", "LITRE": "لیتر",
    "SET": "ست", "SETS": "ست",
    "BOX": "جعبه", "PACK": "بسته", "PAK": "بسته", "PKG": "بسته", "PAC": "بسته",
    "ROLL": "رول", "RL": "رول", "PAIR": "جفت", "PR": "جفت",
    "BAG": "کیسه", "DRUM": "بشکه", "BRL": "بشکه", "SHEET": "ورق", "CAN": "قوطی",
}

#: column keys / Persian headers that hold a unit (PDF + XLSX cell mapping)
UNIT_COLUMNS = frozenset({"unit", "Unit", "UNIT", "واحد", "واحد_کالا", "واحد کالا", "واحد_سنجش"})


def unit_fa(unit: Any) -> str:
    """Persian display name of a unit; unknown / already-Persian values pass through."""
    if unit is None:
        return ""
    s = str(unit).strip()
    if not s or s.lower() == "nan":
        return ""
    return _UNIT_FA.get(s.upper().replace(" ", ""), s)


def is_unit_column(col: Any, label: Any = None) -> bool:
    return str(col or "").strip() in UNIT_COLUMNS or str(label or "").strip() in UNIT_COLUMNS


# ---- request quantities: whole numbers for countable units, ≤2 decimals otherwise ----
_COUNTABLE_FA = frozenset({
    "عدد", "ست", "شاخه", "جفت", "جعبه", "بسته", "رول", "کیسه", "بشکه", "ورق", "قوطی",
    "دستگاه", "قطعه", "تخته", "حلقه", "برگ", "دست", "نفر", "پالت",
})


def is_countable(unit: Any) -> bool:
    """True for units that must be ordered in whole numbers (عدد/ست/شاخه/…)."""
    return unit_fa(unit) in _COUNTABLE_FA


def round_qty(qty: Any, unit: Any) -> float:
    """Countable → ceil to a whole number; weights/lengths → 2 decimals."""
    import math

    try:
        q = float(qty or 0)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(q):
        return 0.0
    if is_countable(unit):
        return float(math.ceil(q - 1e-9))
    return round(q, 2)


def fmt_qty(qty: Any, unit: Any = None) -> str:
    """Display text of ``round_qty`` (no trailing zeros; thousands separators)."""
    q = round_qty(qty, unit)
    if q == int(q):
        return f"{int(q):,}"
    return f"{q:,.2f}".rstrip("0").rstrip(".")


# ---- internal source codes → Persian (display only) ----
SOURCE_FA: dict[str, str] = {
    "monthly": "مصرف ماهیانه",
    "tank": "دادهٔ روزانه",
    "manual": "ثبت دستی",
    "sequence_log": "لاگ توالی تاندیش",
}
SOURCE_COLUMNS = frozenset({"source", "منبع"})


def display_code(col: Any, val: Any) -> Any:
    """Persian label for internal source codes in a «منبع» column; other values unchanged."""
    if isinstance(val, str) and str(col or "").strip() in SOURCE_COLUMNS:
        return SOURCE_FA.get(val.strip(), val)
    return val


def fmt_num(v: Any, decimals: int = 4) -> str:
    """Plain number text: no scientific notation, no 6-significant-digit cut
    (``f"{1234567.0:g}"`` → «1.23457e+06»); up to ``decimals`` decimals, zeros trimmed."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f:
        return ""
    if f in (float("inf"), float("-inf")):
        return "∞" if f > 0 else "-∞"
    if f == int(f):
        return str(int(f))
    s = f"{f:.{decimals}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s
