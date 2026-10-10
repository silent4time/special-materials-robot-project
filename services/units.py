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
