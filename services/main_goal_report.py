"""«گزارش هدف اصلی» — materials needed for steel tonnage (shared bot + web).

Flow (owner / manager / responsible_officer):
  Upload 4 Excel files for the SAME period X:
    1) آمار تولید در مدت X  (CCM1/2→slab, CCM3→bloom, CCM4/5→billet)
    2) مصرف تاندیش بیلت در مدت X
    3) مصرف تاندیش بلوم در مدت X
    4) مصرف تاندیش اسلب در مدت X
  Detect periods; if any differ → Persian error (no compute).
  Else compute tonnage / tundish & melt counts / material rates / projection stub.

Assumed columns (resilient aliases — see COLUMN docs in module constants):
  Production: CCM|ماشین|خط|نوع محصول + تن|تناژ|ton|product|تولید
  Consumption: ماده|شرح|material + مقدار|quantity|kg|تعداد
               optional: تعداد تاندیش / NO. TUNDISH, تعداد ذوب / NO. HEAT
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

from bot.jalali import (
    PERSIAN_MONTH_NAME_TO_NUM,
    PERSIAN_MONTH_NAMES,
    format_month_year,
    jalali_today,
    parse_month_year_token,
    parse_user_date,
    parse_user_date_range,
    tehran_now,
)
from config import CATALOG_ADMIN_ROLES

TITLE_FA = "گزارش هدف اصلی"
SUBTITLE_FA = "مواد مورد نیاز بر حسب تناژ فولاد"

ENTRY_ROLES = frozenset(CATALOG_ADMIN_ROLES)  # owner / manager / responsible_officer

# Upload slots (order of collection in UX)
FILE_KINDS: dict[str, str] = {
    "production": "آمار تولید",
    "billet_consumption": "مصرف تاندیش بیلت",
    "bloom_consumption": "مصرف تاندیش بلوم",
    "slab_consumption": "مصرف تاندیش اسلب",
}
FILE_KIND_ORDER: tuple[str, ...] = (
    "production",
    "billet_consumption",
    "bloom_consumption",
    "slab_consumption",
)
SECTION_FOR_CONSUMPTION: dict[str, str] = {
    "billet_consumption": "billet",
    "bloom_consumption": "bloom",
    "slab_consumption": "slab",
}
SECTION_LABEL_FA = {"slab": "اسلب", "bloom": "بلوم", "billet": "بیلت"}

# CCM mapping (documented plant standard)
CCM_TO_SECTION: dict[int, str] = {
    1: "slab",
    2: "slab",
    3: "bloom",
    4: "billet",
    5: "billet",
}

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# --- period patterns ---
_MONTH_NAME_ALT = {
    # Arabic yeh / common typos → canonical Persian month name
    "فروردين": "فروردین",
    "ارديبهشت": "اردیبهشت",
    "خرداد": "خرداد",
    "تير": "تیر",
    "مرداد": "مرداد",
    "شهريور": "شهریور",
    "شهریور": "شهریور",
    "مهر": "مهر",
    "آبان": "آبان",
    "آذر": "آذر",
    "دي": "دی",
    "دی": "دی",
    "بهمن": "بهمن",
    "اسفند": "اسفند",
}
_MONTH_NAME_RE = re.compile(
    r"(" + "|".join(sorted({*_MONTH_NAME_ALT.keys(), *PERSIAN_MONTH_NAMES.values()}, key=len, reverse=True)) + r")"
    r"\s*[-_/ ]\s*"
    r"(\d{2,4})",
    re.UNICODE,
)
_YM_SLASH_RE = re.compile(r"(14\d{2}|13\d{2})\s*[/-]\s*(0?[1-9]|1[0-2])")
_RANGE_HINT_RE = re.compile(
    r"از\s*(.+?)\s*تا\s*(.+?)(?:\s|$)",
    re.UNICODE,
)

# Skip material-name rows that are totals / rates / meta
_SKIP_MATERIAL_NEEDLES = (
    "kg/ton",
    "kg /ton",
    "kg/tundish",
    "kg / tundish",
    "heat/tundish",
    "total kg",
    "ave-life",
    "ave life",
    "no. tundish",
    "no. heat",
    "no. relin",
    "no. patch",
    "product (ton)",
    "month",
    "جمع",
    "کل",
    "مجموع",
    "میانگین",
    "نرخ",
)


# ---------------------------------------------------------------- roles
def can_run(user: dict | None) -> bool:
    return bool(user and user.get("active") and user.get("role") in ENTRY_ROLES)


# ---------------------------------------------------------------- normalize
def normalize_digits(text: Any) -> str:
    return str(text if text is not None else "").translate(_DIGITS)


def normalize_text(text: Any) -> str:
    s = normalize_digits(text)
    s = s.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    s = s.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    return re.sub(r"\s+", " ", s).strip()


def fold_key(text: Any) -> str:
    return normalize_text(text).casefold().replace(" ", "").replace("_", "").replace("-", "")


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        if isinstance(val, float) and pd.isna(val):
            return None
    except Exception:  # noqa: BLE001
        pass
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    s = normalize_digits(val).strip().replace(",", "").replace("٬", "")
    if not s or s in {"-", "—", "–", "nan", "None"}:
        return None
    # strip trailing units like "Kg" attached without space
    s = re.sub(r"[^\d.\-+eE]", "", s)
    if not s or s in {".", "-", "+"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------- period
@dataclass(frozen=True)
class PeriodKey:
    """Canonical period identity for equality across the four files."""

    kind: str  # "month" | "range" | "raw"
    year: int | None = None
    month: int | None = None
    start: str | None = None  # Jalali YYYY/MM/DD
    end: str | None = None
    raw: str = ""

    def label_fa(self) -> str:
        if self.kind == "month" and self.year and self.month:
            return format_month_year(self.year, self.month, named=True)
        if self.kind == "range" and self.start and self.end:
            return f"از {self.start} تا {self.end}"
        return self.raw or "نامشخص"

    def key(self) -> str:
        if self.kind == "month" and self.year and self.month:
            return f"m:{self.year:04d}-{self.month:02d}"
        if self.kind == "range" and self.start and self.end:
            return f"r:{self.start}:{self.end}"
        return f"x:{normalize_text(self.raw).casefold()}"


def _year_from_short(y: int) -> int:
    if y < 100:
        # 05 → 1405 (current century heuristic around today)
        today = jalali_today()
        century = (today.year // 100) * 100
        cand = century + y
        if abs(cand - today.year) > 50:
            cand = century - 100 + y
        return cand
    return y


def _period_from_month_name(name: str, year_raw: int) -> PeriodKey | None:
    canon = _MONTH_NAME_ALT.get(name) or _MONTH_NAME_ALT.get(normalize_text(name)) or name
    canon = normalize_text(canon)
    month = PERSIAN_MONTH_NAME_TO_NUM.get(canon)
    if not month:
        # try without Arabic-yeh normalization miss
        for k, v in PERSIAN_MONTH_NAME_TO_NUM.items():
            if fold_key(k) == fold_key(canon):
                month = v
                break
    if not month:
        return None
    year = _year_from_short(int(year_raw))
    if not (1200 <= year <= 1500):
        return None
    return PeriodKey(kind="month", year=year, month=month, raw=f"{canon} {year}")


def parse_period_text(text: Any) -> PeriodKey | None:
    """Extract a period from a free-text blob (filename, sheet, header cell)."""
    raw = normalize_digits(text)
    if not raw or not str(raw).strip():
        return None
    s = normalize_text(raw)

    # named month: شهریور-05 / شهریور ۱۴۰۴ / شهريور-05
    m = _MONTH_NAME_RE.search(s)
    if m:
        got = _period_from_month_name(m.group(1), int(m.group(2)))
        if got:
            return got

    # «از … تا …»
    m = _RANGE_HINT_RE.search(s)
    if m:
        left, right = m.group(1).strip(), m.group(2).strip()
        # month-year range
        a = parse_month_year_token(left)
        b = parse_month_year_token(right)
        if a and b:
            if a == b:
                return PeriodKey(kind="month", year=a[0], month=a[1], raw=format_month_year(*a))
            # multi-month → treat as raw range label (still comparable)
            return PeriodKey(
                kind="raw",
                raw=f"از {format_month_year(*a)} تا {format_month_year(*b)}",
            )
        dr = parse_user_date_range(f"از {left} تا {right}")
        if dr:
            from bot.jalali import format_date

            return PeriodKey(
                kind="range",
                start=format_date(dr[0]),
                end=format_date(dr[1]),
                raw=f"از {format_date(dr[0])} تا {format_date(dr[1])}",
            )

    # 1404/06 or 1404-6
    m = _YM_SLASH_RE.search(s)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        return PeriodKey(kind="month", year=year, month=month, raw=format_month_year(year, month))

    tok = parse_month_year_token(s)
    if tok:
        return PeriodKey(kind="month", year=tok[0], month=tok[1], raw=format_month_year(*tok))

    d = parse_user_date(s)
    if d:
        from bot.jalali import format_date
        import jdatetime

        j = jdatetime.date.fromgregorian(date=d)
        return PeriodKey(
            kind="month",
            year=j.year,
            month=j.month,
            raw=format_month_year(j.year, j.month),
        )
    return None


def _iter_cell_strings(df: pd.DataFrame, max_rows: int = 40, max_cols: int = 20) -> Iterable[str]:
    rows = min(len(df), max_rows)
    cols = min(df.shape[1], max_cols) if df.shape[1] else 0
    for r in range(rows):
        for c in range(cols):
            val = df.iat[r, c]
            if val is None:
                continue
            try:
                if isinstance(val, float) and pd.isna(val):
                    continue
            except Exception:  # noqa: BLE001
                pass
            text = str(val).strip()
            if text and text.lower() != "nan":
                yield text


def detect_period(
    path: Path | str,
    *,
    filename: str | None = None,
) -> tuple[PeriodKey | None, str]:
    """Detect period from filename, sheet names, and header cells.

    Returns (period_or_None, source_description).
    """
    path = Path(path)
    name = filename or path.name
    # 1) filename
    p = parse_period_text(Path(name).stem)
    if p:
        return p, f"نام فایل «{name}»"

    try:
        xl = pd.ExcelFile(path)
    except Exception as exc:  # noqa: BLE001
        return None, f"خواندن Excel ناموفق: {exc}"

    # 2) sheet names
    for sheet in xl.sheet_names:
        p = parse_period_text(sheet)
        if p:
            return p, f"نام شیت «{sheet}»"

    # 3) header cells (first sheets)
    for sheet in xl.sheet_names[:4]:
        try:
            df = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)
        except Exception:  # noqa: BLE001
            continue
        for cell in _iter_cell_strings(df):
            p = parse_period_text(cell)
            if p:
                return p, f"سلول شیت «{sheet}»"
    return None, "بازه در نام فایل / شیت / سربرگ یافت نشد"


def compare_periods(
    detected: dict[str, tuple[PeriodKey | None, str]],
) -> str | None:
    """If any period missing or mismatched, return Persian error text; else None."""
    lines: list[str] = []
    keys: list[str] = []
    missing = False
    for kind in FILE_KIND_ORDER:
        label = FILE_KINDS[kind]
        period, src = detected.get(kind, (None, ""))
        if period is None:
            missing = True
            lines.append(f"• {label}: نامشخص ({src or 'تشخیص نشد'})")
        else:
            keys.append(period.key())
            lines.append(f"• {label}: {period.label_fa()} — از {src}")
    if missing:
        return (
            "تشخیص بازه زمانی برای همه فایل‌ها ممکن نشد:\n"
            + "\n".join(lines)
            + "\nنام فایل، نام شیت یا سربرگ را با ماه شمسی (مثل شهریور ۱۴۰۴) تکمیل کنید."
        )
    if len(set(keys)) > 1:
        return "بازه زمانی فایل‌ها یکسان نیست\n" + "\n".join(lines)
    return None


# ---------------------------------------------------------------- file kind detection
_PRODUCTION_NEEDLES = ("تولید", "production", "product", "آمارتولید")
_CONSUMPTION_NEEDLES = ("مصرف", "تاندیش", "tundish", "consumption", "مواد")
_SECTION_NEEDLES: dict[str, tuple[str, ...]] = {
    "billet": ("بیلت", "billet"),
    "bloom": ("بلوم", "bloom"),
    "slab": ("اسلب", "slab"),
}


def _kind_from_text(text: str) -> str | None:
    f = fold_key(text)
    if not f:
        return None
    sections = [sec for sec, needles in _SECTION_NEEDLES.items() if any(n in f for n in needles)]
    has_cons = any(fold_key(n) in f for n in _CONSUMPTION_NEEDLES)
    has_prod = any(fold_key(n) in f for n in _PRODUCTION_NEEDLES)
    if has_prod and not has_cons:
        return "production"
    if len(sections) == 1 and (has_cons or not has_prod):
        return f"{sections[0]}_consumption"
    if has_prod:
        return "production"
    return None


def detect_file_kind(path: Path | str, *, filename: str | None = None) -> tuple[str | None, str]:
    """Guess which of the 4 main-goal slots a file is (for bulk multi-month upload).

    Order: filename → sheet names → header cells (tundish rows ⇒ consumption,
    CCM/PRODUCT rows without tundish ⇒ production). Returns (kind|None, source).
    """
    path = Path(path)
    name = filename or path.name
    k = _kind_from_text(Path(name).stem)
    if k:
        return k, f"نام فایل «{name}»"
    try:
        xl = pd.ExcelFile(path)
    except Exception as exc:  # noqa: BLE001
        return None, f"خواندن Excel ناموفق: {exc}"
    for sheet in xl.sheet_names:
        k = _kind_from_text(sheet)
        if k:
            return k, f"نام شیت «{sheet}»"
    sec_hits = {sec: 0 for sec in _SECTION_NEEDLES}
    tundish_rows = 0
    ccm_hits = 0
    for sheet in xl.sheet_names[:3]:
        try:
            df = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)
        except Exception:  # noqa: BLE001
            continue
        for cell in _iter_cell_strings(df, max_rows=40, max_cols=20):
            f = fold_key(cell)
            if _is_tundish_count_label(cell):
                tundish_rows += 1
            if re.search(r"ccm\s*[1-5]", normalize_text(cell), re.I):
                ccm_hits += 1
            for sec, needles in _SECTION_NEEDLES.items():
                if any(n in f for n in needles):
                    sec_hits[sec] += 1
    if tundish_rows:
        best = max(sec_hits.items(), key=lambda kv: kv[1])
        others = [v for s_, v in sec_hits.items() if s_ != best[0]]
        if best[1] > 0 and all(best[1] > v for v in others):
            return f"{best[0]}_consumption", "محتوای فایل (ردیف تاندیش + نام بخش)"
        return None, "فایل مصرف تاندیش است ولی بخش (بیلت/بلوم/اسلب) تشخیص نشد"
    if ccm_hits >= 2 or sum(1 for v in sec_hits.values() if v) >= 2:
        return "production", "محتوای فایل (CCM / چند بخش)"
    return None, "نوع فایل تشخیص نشد"


# ---------------------------------------------------------------- load helpers
def _read_all_sheets(path: Path | str) -> list[tuple[str, pd.DataFrame]]:
    path = Path(path)
    xl = pd.ExcelFile(path)
    out: list[tuple[str, pd.DataFrame]] = []
    for sheet in xl.sheet_names:
        try:
            df = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)
        except Exception:  # noqa: BLE001
            continue
        if df is None or df.empty:
            continue
        out.append((sheet, df))
    return out


def _find_header_row(df: pd.DataFrame, needles: Sequence[str], scan: int = 40) -> int | None:
    want = [fold_key(n) for n in needles]
    for r in range(min(len(df), scan)):
        row_fold = " ".join(fold_key(v) for v in df.iloc[r].tolist() if v is not None)
        if any(n and n in row_fold for n in want):
            return r
    return None


def _row_label(df: pd.DataFrame, r: int) -> str:
    for c in range(min(df.shape[1], 4)):
        val = df.iat[r, c]
        if val is None:
            continue
        try:
            if isinstance(val, float) and pd.isna(val):
                continue
        except Exception:  # noqa: BLE001
            pass
        text = normalize_text(val)
        if text and not re.fullmatch(r"[\d.,]+", text):
            return text
    return ""


def _section_from_label(text: str) -> str | None:
    f = fold_key(text)
    if not f:
        return None
    # CCM first
    m = re.search(r"ccm\s*([1-5])", normalize_text(text), re.I)
    if m:
        return CCM_TO_SECTION.get(int(m.group(1)))
    m = re.search(r"(?:^|[^a-z0-9])([1-5])(?:\s*$)", f)
    # plain اسلب/بلوم/بیلت / slab/bloom/billet
    if "اسلب" in text or "slab" in f:
        return "slab"
    if "بلوم" in text or "bloom" in f:
        return "bloom"
    if "بیلت" in text or "billet" in f:
        return "billet"
    return None


def _is_tonnage_label(text: str) -> bool:
    f = fold_key(text)
    needles = (
        "product(ton)",
        "productton",
        "تناژ",
        "تولید",
        "tonnage",
        "tons",
        "ton",
        "تن",
        "وزن",
    )
    return any(n in f for n in needles)


def _is_tundish_count_label(text: str) -> bool:
    f = fold_key(text)
    return any(
        n in f
        for n in (
            "no.tundish",
            "notundish",
            "تعدادتاندیش",
                        "تعدادتانديش",
            "tundishcount",
            "counttundish",
        )
    ) or ("tundish" in f and ("no" in f or "تعداد" in text or "count" in f))


def _is_melt_count_label(text: str) -> bool:
    f = fold_key(text)
    return any(
        n in f
        for n in (
            "no.heat",
            "noheat",
            "تعدادذوب",
            "meltcount",
            "heatcount",
            "تعدادهیت",
            "تعدادheat",
        )
    ) or (("heat" in f or "ذوب" in text or "melt" in f) and ("no" in f or "تعداد" in text or "count" in f))


def _skip_as_material(name: str) -> bool:
    f = fold_key(name)
    if not f or len(f) < 2:
        return True
    for n in _SKIP_MATERIAL_NEEDLES:
        if fold_key(n) in f:
            return True
    if _is_tonnage_label(name) or _is_tundish_count_label(name) or _is_melt_count_label(name):
        return True
    if parse_period_text(name) is not None and _MONTH_NAME_RE.search(normalize_text(name)):
        # title rows like «مصرف تاندیش بیلت — شهریور ۱۴۰۵»
        return True
    if _section_from_label(name) and len(f) < 12:
        # bare "اسلب" / "CCM1" as row label — not a material
        if re.fullmatch(r"(ccm)?[1-5]", f) or f in {"اسلب", "بلوم", "بیلت", "slab", "bloom", "billet"}:
            return True
    return False


_UNIT_WORDS_RE = re.compile(r"(kg|kgs|ton|tons|pcs|عدد|تن|کیلوگرم|کیلو)", re.I)


def _cell_number(val: Any) -> float | None:
    """Numeric value of a cell; text cells with real words (titles/labels) → None."""
    if isinstance(val, str):
        letters = _UNIT_WORDS_RE.sub("", normalize_text(val))
        if re.search(r"[A-Za-z\u0600-\u06FF]{2,}", letters):
            return None
    return _to_float(val)


def _pick_numeric_from_row(df: pd.DataFrame, r: int, prefer_last: bool = True) -> float | None:
    """Pick a quantity from a row — prefer Total/last numeric, else first."""
    nums: list[float] = []
    for c in range(df.shape[1]):
        v = _cell_number(df.iat[r, c])
        if v is not None:
            nums.append(v)
    if not nums:
        return None
    return nums[-1] if prefer_last else nums[0]


def _month_column_index(df: pd.DataFrame, period: PeriodKey | None) -> int | None:
    """If a MONTH header row exists and period is a month, return that column index."""
    if not period or period.kind != "month" or not period.month:
        return None
    target = int(period.month)
    for r in range(min(len(df), 35)):
        label = fold_key(_row_label(df, r))
        if label != "month" and "ماه" not in label:
            # also accept raw header cells with 1..12
            row_vals = [_to_float(df.iat[r, c]) for c in range(df.shape[1])]
            if sum(1 for v in row_vals if v is not None and 1 <= v <= 12) >= 6:
                for c, v in enumerate(row_vals):
                    if v is not None and int(v) == target:
                        return c
            continue
        for c in range(df.shape[1]):
            v = _to_float(df.iat[r, c])
            if v is not None and int(v) == target:
                return c
    return None


def _value_at(df: pd.DataFrame, r: int, col: int | None) -> float | None:
    if col is not None and col < df.shape[1]:
        v = _cell_number(df.iat[r, col])
        if v is not None:
            return v
    return _pick_numeric_from_row(df, r, prefer_last=True)


# ---------------------------------------------------------------- production
@dataclass
class ProductionStats:
    slab_tons: float = 0.0
    bloom_tons: float = 0.0
    billet_tons: float = 0.0
    notes: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def total_tons(self) -> float:
        return float(self.slab_tons + self.bloom_tons + self.billet_tons)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slab_tons": self.slab_tons,
            "bloom_tons": self.bloom_tons,
            "billet_tons": self.billet_tons,
            "total_tons": self.total_tons,
            "notes": list(self.notes),
            "missing": list(self.missing),
        }


def parse_production(
    path: Path | str,
    *,
    period: PeriodKey | None = None,
) -> ProductionStats:
    """Parse آمار تولید — tonnage by slab/bloom/billet via CCM or labels."""
    stats = ProductionStats()
    sheets = _read_all_sheets(path)
    if not sheets:
        stats.missing.append("هیچ شیتی خوانده نشد")
        return stats

    found_any = False
    for sheet_name, df in sheets:
        month_col = _month_column_index(df, period)
        # Strategy A: row labels mention CCM / section + tonnage on same row
        for r in range(len(df)):
            label = _row_label(df, r)
            if not label:
                continue
            sec = _section_from_label(label)
            if not sec:
                continue
            # If label is tonnage+section combo, or nearby row is tonnage
            is_ton = _is_tonnage_label(label)
            val = _value_at(df, r, month_col)
            if val is None:
                continue
            if is_ton or "ccm" in fold_key(label) or sec:
                # Prefer explicit tonnage rows; for CCM-only rows accept if units look like tons (>10)
                if is_ton or "ccm" in fold_key(label) or _is_tonnage_label(label):
                    setattr(stats, f"{sec}_tons", getattr(stats, f"{sec}_tons") + float(val))
                    found_any = True
                    stats.notes.append(f"{sheet_name}: {label} → {SECTION_LABEL_FA[sec]} = {val}")
                elif val >= 10:  # heuristic: treat large numbers next to section as tons
                    setattr(stats, f"{sec}_tons", getattr(stats, f"{sec}_tons") + float(val))
                    found_any = True
                    stats.notes.append(f"{sheet_name}: {label} (تخمین تناژ) → {SECTION_LABEL_FA[sec]} = {val}")

        # Strategy B: columns headed by CCM / section, values under a tonnage row
        header_r = _find_header_row(df, ["ccm", "اسلب", "بلوم", "بیلت", "slab", "bloom", "billet", "ماشین", "خط"])
        if header_r is not None:
            col_section: dict[int, str] = {}
            for c in range(df.shape[1]):
                cell = normalize_text(df.iat[header_r, c])
                sec = _section_from_label(cell)
                if sec:
                    col_section[c] = sec
            if col_section:
                for r in range(header_r + 1, len(df)):
                    label = _row_label(df, r)
                    if label and not _is_tonnage_label(label) and fold_key(label) not in {"", "total", "جمع", "مجموع"}:
                        # only accumulate tonnage-ish rows
                        if not (_is_tonnage_label(label) or "product" in fold_key(label) or "تولید" in label):
                            continue
                    for c, sec in col_section.items():
                        v = _to_float(df.iat[r, c])
                        if v is None:
                            continue
                        if label and (_is_tonnage_label(label) or "product" in fold_key(label) or "تولید" in label or not label):
                            setattr(stats, f"{sec}_tons", getattr(stats, f"{sec}_tons") + float(v))
                            found_any = True
                            stats.notes.append(
                                f"{sheet_name}/col{c}: {label or '—'} → {SECTION_LABEL_FA[sec]} = {v}"
                            )

        # Strategy C: single PRODUCT (TON) total row without section — attribute later if only one section file context
        if not found_any:
            for r in range(len(df)):
                label = _row_label(df, r)
                if label and _is_tonnage_label(label):
                    v = _value_at(df, r, month_col)
                    if v is not None and v > 0:
                        # Distribute unknown — leave in notes; caller may still show total
                        stats.notes.append(f"{sheet_name}: تناژ کل بدون تفکیک = {v}")
                        # Put into a synthetic total by splitting equally? Better: add to all zero and store as total via billet? No.
                        # Store as slab if nothing else — actually keep a side channel:
                        if stats.total_tons == 0:
                            # stash on notes only; set a hidden attribute via billet? Use equal split later.
                            stats.notes.append("__UNSECTIONED_TOTAL__:" + str(v))

    # If we only have an unsectioned total, put it on a pseudo field via notes parse
    if stats.total_tons == 0:
        for n in stats.notes:
            if n.startswith("__UNSECTIONED_TOTAL__:"):
                try:
                    total = float(n.split(":", 1)[1])
                except ValueError:
                    continue
                # Cannot map CCM — report as total only by putting on slab with a note
                stats.slab_tons = total
                stats.notes.append(
                    "تناژ بدون تفکیک CCM/نوع یافت شد؛ موقتاً در «اسلب» نمایش داده می‌شود "
                    "(ستون CCM یا نوع محصول را اضافه کنید)."
                )
                found_any = True
                break

    if not found_any:
        stats.missing.append(
            "ستون/ردیف تناژ یا CCM (CCM1/2=اسلب، CCM3=بلوم، CCM4/5=بیلت) یافت نشد"
        )
    return stats


# ---------------------------------------------------------------- consumption
@dataclass
class MaterialLine:
    name: str
    quantity: float
    unit: str = "kg"
    item_id: str | None = None
    keyword: str | None = None


@dataclass
class ConsumptionStats:
    section: str
    tundish_count: float | None = None
    melt_count: float | None = None
    materials: list[MaterialLine] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "tundish_count": self.tundish_count,
            "melt_count": self.melt_count,
            "materials": [asdict(m) for m in self.materials],
            "notes": list(self.notes),
            "missing": list(self.missing),
        }


def _guess_unit(name: str) -> str:
    f = fold_key(name)
    if "kg" in f or "کیلو" in name or "کيلو" in name:
        return "kg"
    if "عدد" in name or "pcs" in f or "qty" in f:
        return "عدد"
    if "ton" in f or "تن" in name:
        return "ton"
    return "kg"


def _strip_unit_from_name(name: str) -> tuple[str, str]:
    unit = _guess_unit(name)
    cleaned = re.sub(
        r"\s*[\(（]?\s*(kg|کیلوگرم|کیلو|عدد|pcs|ton|تن)\s*[\)）]?\s*$",
        "",
        name,
        flags=re.I,
    ).strip()
    return cleaned or name, unit


def parse_consumption(
    path: Path | str,
    section: str,
    *,
    period: PeriodKey | None = None,
) -> ConsumptionStats:
    """Parse مصرف تاندیش for one section (billet/bloom/slab)."""
    stats = ConsumptionStats(section=section)
    sheets = _read_all_sheets(path)
    if not sheets:
        stats.missing.append("هیچ شیتی خوانده نشد")
        return stats

    materials_acc: dict[str, MaterialLine] = {}

    for sheet_name, df in sheets:
        month_col = _month_column_index(df, period)

        # Row-oriented plant report (label in col0, numbers across)
        for r in range(len(df)):
            label = _row_label(df, r)
            if not label:
                continue
            if _is_tundish_count_label(label):
                v = _value_at(df, r, month_col)
                if v is not None:
                    stats.tundish_count = (stats.tundish_count or 0) + float(v)
                    stats.notes.append(f"{sheet_name}: تعداد تاندیش = {v}")
                continue
            if _is_melt_count_label(label):
                v = _value_at(df, r, month_col)
                if v is not None:
                    stats.melt_count = (stats.melt_count or 0) + float(v)
                    stats.notes.append(f"{sheet_name}: تعداد ذوب = {v}")
                continue
            if _skip_as_material(label):
                continue
            # rate rows already skipped; take quantity rows
            v = _value_at(df, r, month_col)
            if v is None or v == 0:
                continue
            # Prefer material rows that look like consumption (have kg in name or sizable qty)
            name, unit = _strip_unit_from_name(label)
            key = fold_key(name)
            if key in materials_acc:
                materials_acc[key].quantity += float(v)
            else:
                materials_acc[key] = MaterialLine(name=name, quantity=float(v), unit=unit)

        # Table with header row (material_name / quantity)
        hdr = _find_header_row(
            df,
            [
                "material",
                "material_name",
                "ماده",
                "نام ماده",
                "شرح",
                "شرح کالا",
                "کالا",
                "مقدار",
                "quantity",
                "مصرف",
            ],
        )
        if hdr is not None:
            headers = [fold_key(df.iat[hdr, c]) for c in range(df.shape[1])]
            name_cols = [
                i
                for i, h in enumerate(headers)
                if any(n in h for n in ("material", "ماده", "شرح", "کالا", "نام", "keyword", "کلید"))
            ]
            qty_cols = [
                i
                for i, h in enumerate(headers)
                if any(n in h for n in ("quantity", "qty", "مقدار", "مصرف", "تعداد", "kg", "کیلو"))
            ]
            id_cols = [
                i
                for i, h in enumerate(headers)
                if any(n in h for n in ("id", "کد", "شناسه"))
            ]
            tundish_cols = [
                i
                for i, h in enumerate(headers)
                if "tundish" in h or "تاندیش" in normalize_text(df.iat[hdr, i])
            ]
            melt_cols = [
                i
                for i, h in enumerate(headers)
                if any(n in h for n in ("melt", "heat", "ذوب"))
            ]
            for r in range(hdr + 1, len(df)):
                # optional count columns on same table
                for tc in tundish_cols:
                    v = _to_float(df.iat[r, tc])
                    if v is not None and name_cols and _to_float(df.iat[r, name_cols[0]]) is None:
                        # likely a summary count cell — skip if also material
                        pass
                name = ""
                if name_cols:
                    name = normalize_text(df.iat[r, name_cols[0]])
                if not name or _skip_as_material(name):
                    continue
                qty = None
                for qc in qty_cols:
                    qty = _to_float(df.iat[r, qc])
                    if qty is not None:
                        break
                if qty is None:
                    qty = _pick_numeric_from_row(df, r, prefer_last=True)
                if qty is None or qty == 0:
                    continue
                item_id = None
                if id_cols:
                    raw_id = normalize_text(df.iat[r, id_cols[0]])
                    item_id = raw_id or None
                clean, unit = _strip_unit_from_name(name)
                key = fold_key(clean)
                if key in materials_acc:
                    materials_acc[key].quantity += float(qty)
                else:
                    materials_acc[key] = MaterialLine(
                        name=clean, quantity=float(qty), unit=unit, item_id=item_id
                    )

    stats.materials = sorted(materials_acc.values(), key=lambda m: -m.quantity)
    if stats.tundish_count is None:
        stats.missing.append("تعداد تاندیش (NO. TUNDISH / تعداد تاندیش) یافت نشد")
    if not stats.materials:
        stats.missing.append("ردیف مواد مصرفی (نام + مقدار) یافت نشد")
    return stats


# ---------------------------------------------------------------- join + rates
@dataclass
class RateRow:
    section: str
    material_name: str
    quantity: float
    unit: str
    item_id: str | None
    keyword: str | None
    matched_source: str | None  # id/keyword from منبع اصلی
    per_tundish: float | None
    per_ton: float | None
    per_melt: float | None
    need_for_period_tons: float | None  # = quantity (actual) / or rate×tons


def match_inventory_stock(
    mat: MaterialLine,
    inventory: pd.DataFrame | None,
) -> tuple[str | None, str | None, str | None, float | None]:
    """Like ``_match_main_source`` but also returns current stock (موجودی) if matched."""
    matched, iid, kw = _match_main_source(mat, inventory)
    if not matched or inventory is None or inventory.empty:
        return matched, iid, kw, None
    qty_col = "quantity" if "quantity" in inventory.columns else None
    if not qty_col:
        return matched, iid, kw, None
    stock: float | None = None
    if iid and "id" in inventory.columns:
        want = fold_key(iid)
        for _, row in inventory.iterrows():
            if fold_key(row.get("id")) == want:
                v = _to_float(row.get(qty_col))
                if v is not None:
                    stock = (stock or 0.0) + v
    return matched, iid, kw, stock


def _match_main_source(
    mat: MaterialLine,
    inventory: pd.DataFrame | None,
) -> tuple[str | None, str | None, str | None]:
    """Return (matched_label, item_id, keyword) from منبع اصلی if joinable."""
    if inventory is None or inventory.empty:
        return None, mat.item_id, mat.keyword
    work = inventory
    id_col = "id" if "id" in work.columns else None
    kw_col = "keyword" if "keyword" in work.columns else None
    name_col = "product_name" if "product_name" in work.columns else (
        "material_name" if "material_name" in work.columns else None
    )
    target = fold_key(mat.name)
    target_id = fold_key(mat.item_id) if mat.item_id else ""

    if id_col and target_id:
        for _, row in work.iterrows():
            if fold_key(row.get(id_col)) == target_id:
                return (
                    f"id={row.get(id_col)}",
                    str(row.get(id_col) or "") or None,
                    str(row.get(kw_col) or "") if kw_col else None,
                )

    if kw_col:
        for _, row in work.iterrows():
            kw = normalize_text(row.get(kw_col))
            if not kw:
                continue
            fk = fold_key(kw)
            if fk and (fk in target or target in fk):
                return (
                    f"keyword={kw}",
                    str(row.get(id_col) or "") if id_col else None,
                    kw,
                )

    if name_col:
        for _, row in work.iterrows():
            pn = fold_key(row.get(name_col))
            if pn and (pn in target or target in pn):
                return (
                    f"name={row.get(name_col)}",
                    str(row.get(id_col) or "") if id_col else None,
                    str(row.get(kw_col) or "") if kw_col else None,
                )
    return None, mat.item_id, mat.keyword


def build_rate_rows(
    production: ProductionStats,
    consumptions: dict[str, ConsumptionStats],
    inventory: pd.DataFrame | None = None,
) -> list[RateRow]:
    tons_by = {
        "slab": production.slab_tons,
        "bloom": production.bloom_tons,
        "billet": production.billet_tons,
    }
    rows: list[RateRow] = []
    for section, cons in consumptions.items():
        tons = float(tons_by.get(section) or 0)
        tc = cons.tundish_count
        mc = cons.melt_count
        for mat in cons.materials:
            matched, iid, kw = _match_main_source(mat, inventory)
            per_t = (mat.quantity / tc) if tc and tc > 0 else None
            per_ton = (mat.quantity / tons) if tons > 0 else None
            per_m = (mat.quantity / mc) if mc and mc > 0 else None
            rows.append(
                RateRow(
                    section=section,
                    material_name=mat.name,
                    quantity=mat.quantity,
                    unit=mat.unit,
                    item_id=iid,
                    keyword=kw,
                    matched_source=matched,
                    per_tundish=per_t,
                    per_ton=per_ton,
                    per_melt=per_m,
                    need_for_period_tons=mat.quantity,
                )
            )
    return rows


def project_need(rate_rows: list[RateRow], target_tons: float) -> list[dict[str, Any]]:
    """نیاز ≈ نرخ × تناژ — using per_ton when available; else blank."""
    out: list[dict[str, Any]] = []
    for r in rate_rows:
        need = (r.per_ton * target_tons) if r.per_ton is not None else None
        out.append(
            {
                "section": r.section,
                "section_fa": SECTION_LABEL_FA.get(r.section, r.section),
                "material_name": r.material_name,
                "unit": r.unit,
                "per_ton": r.per_ton,
                "target_tons": target_tons,
                "projected_need": need,
                "formula": "نیاز ≈ نرخ × تناژ" if r.per_ton is not None else "نرخ بر تن در دسترس نیست",
            }
        )
    return out


# ---------------------------------------------------------------- report sections for PDF/xlsx
def _fmt(n: float | None, digits: int = 3) -> str | float | None:
    if n is None:
        return None
    try:
        if abs(n) >= 100:
            return round(n, 1)
        return round(n, digits)
    except Exception:  # noqa: BLE001
        return n


def build_report_sections(
    *,
    period: PeriodKey,
    production: ProductionStats,
    consumptions: dict[str, ConsumptionStats],
    rate_rows: list[RateRow],
    target_tons: float | None = None,
) -> list[dict[str, Any]]:
    """Sections for generate_simple_report_pdf / xlsx."""
    sections: list[dict[str, Any]] = []

    # 1) tonnage
    ton_rows = [
        {"نوع": "اسلب (CCM1/2)", "تناژ_تن": _fmt(production.slab_tons, 1)},
        {"نوع": "بلوم (CCM3)", "تناژ_تن": _fmt(production.bloom_tons, 1)},
        {"نوع": "بیلت (CCM4/5)", "تناژ_تن": _fmt(production.billet_tons, 1)},
        {"نوع": "جمع", "تناژ_تن": _fmt(production.total_tons, 1)},
    ]
    sections.append(
        {
            "title": "تناژ تولید فولاد",
            "columns": ["نوع", "تناژ_تن"],
            "rows": ton_rows,
        }
    )

    # 2) tundish / melt counts
    count_rows = []
    for sec in ("slab", "bloom", "billet"):
        cons = consumptions.get(sec)
        count_rows.append(
            {
                "بخش": SECTION_LABEL_FA[sec],
                "تعداد_تاندیش": _fmt(cons.tundish_count, 1) if cons else None,
                "تعداد_ذوب": _fmt(cons.melt_count, 1) if cons else None,
                "تناژ_مرتبط": _fmt(
                    getattr(production, f"{sec}_tons"),
                    1,
                ),
            }
        )
    sections.append(
        {
            "title": "تعداد تاندیش و ذوب",
            "columns": ["بخش", "تعداد_تاندیش", "تعداد_ذوب", "تناژ_مرتبط"],
            "rows": count_rows,
        }
    )

    # 3) rates
    rate_table = []
    for r in rate_rows:
        rate_table.append(
            {
                "بخش": SECTION_LABEL_FA.get(r.section, r.section),
                "ماده": r.material_name,
                "مصرف_بازه": _fmt(r.quantity, 2),
                "واحد": r.unit,
                "نرخ_بر_تاندیش": _fmt(r.per_tundish),
                "نرخ_بر_تن": _fmt(r.per_ton),
                "نرخ_بر_ذوب": _fmt(r.per_melt),
                "اتصال_منبع_اصلی": r.matched_source or "—",
            }
        )
    sections.append(
        {
            "title": "نرخ مصرف مواد (کیلو/واحد بر تاندیش و بر تن)",
            "columns": [
                "بخش",
                "ماده",
                "مصرف_بازه",
                "واحد",
                "نرخ_بر_تاندیش",
                "نرخ_بر_تن",
                "نرخ_بر_ذوب",
                "اتصال_منبع_اصلی",
            ],
            "rows": rate_table,
        }
    )

    # 4) projection for period tons + optional target
    proj_rows = []
    period_tons = production.total_tons
    for r in rate_rows:
        need_period = r.need_for_period_tons
        need_target = (r.per_ton * target_tons) if (r.per_ton is not None and target_tons) else None
        proj_rows.append(
            {
                "بخش": SECTION_LABEL_FA.get(r.section, r.section),
                "ماده": r.material_name,
                "نرخ_بر_تن": _fmt(r.per_ton),
                "نیاز_بازه": _fmt(need_period, 2),
                "نیاز_هدف": _fmt(need_target, 2) if target_tons else "—",
                "فرمول": "نیاز ≈ نرخ × تناژ",
            }
        )
    tgt_label = f"{_fmt(target_tons, 1)} تن" if target_tons else "—"
    sections.append(
        {
            "title": (
                f"پیش‌بینی نیاز — بازه ({_fmt(period_tons, 1)} تن) "
                f"و هدف ({tgt_label})"
            ),
            "columns": ["بخش", "ماده", "نرخ_بر_تن", "نیاز_بازه", "نیاز_هدف", "فرمول"],
            "rows": proj_rows,
        }
    )

    # 5) parse notes / limitations
    note_lines: list[str] = []
    note_lines.append(f"بازه: {period.label_fa()}")
    note_lines.append(
        "نگاشت CCM: CCM1 و CCM2 → اسلب، CCM3 → بلوم، CCM4 و CCM5 → بیلت."
    )
    note_lines.append(
        "فرمول نرخ: نرخ_بر_تاندیش = مصرف ÷ تعداد تاندیش؛ "
        "نرخ_بر_تن = مصرف ÷ تناژ همان بخش؛ "
        "نرخ_بر_ذوب = مصرف ÷ تعداد ذوب (در صورت وجود)."
    )
    note_lines.append("فرمول نیاز تقریبی: نیاز ≈ نرخ × تناژ.")
    if production.missing:
        note_lines.append("تولید — کمبود: " + "؛ ".join(production.missing))
    for sec, cons in consumptions.items():
        if cons.missing:
            note_lines.append(
                f"{SECTION_LABEL_FA.get(sec, sec)} — کمبود: " + "؛ ".join(cons.missing)
            )
    note_rows = [{"یادداشت": line} for line in note_lines]
    sections.append(
        {
            "title": "قواعد و محدودیت‌ها",
            "columns": ["یادداشت"],
            "rows": note_rows,
        }
    )
    return sections


def report_has_data(sections: list[dict[str, Any]]) -> bool:
    for sec in sections:
        rows = sec.get("rows") or []
        # ignore notes-only
        if sec.get("title") == "قواعد و محدودیت‌ها":
            continue
        if rows:
            # tonnage section with all zeros still "has structure" but treat total>0 or materials
            return True
    return False


# ---------------------------------------------------------------- orchestration
@dataclass
class MainGoalResult:
    ok: bool
    error_fa: str | None
    period: PeriodKey | None
    period_sources: dict[str, str]
    production: ProductionStats | None
    consumptions: dict[str, ConsumptionStats]
    rate_rows: list[RateRow]
    sections: list[dict[str, Any]]
    target_tons: float | None
    warnings: list[str] = field(default_factory=list)

    def summary_text(self) -> str:
        if not self.ok or not self.period or not self.production:
            return self.error_fa or "خطا"
        lines = [
            f"🎯 {TITLE_FA}",
            f"بازه: {self.period.label_fa()}",
            f"تناژ: اسلب {self.production.slab_tons:.1f} | "
            f"بلوم {self.production.bloom_tons:.1f} | "
            f"بیلت {self.production.billet_tons:.1f} | "
            f"جمع {self.production.total_tons:.1f} تن",
        ]
        for sec in ("slab", "bloom", "billet"):
            c = self.consumptions.get(sec)
            if not c:
                continue
            lines.append(
                f"{SECTION_LABEL_FA[sec]}: تاندیش {c.tundish_count if c.tundish_count is not None else '—'}، "
                f"ذوب {c.melt_count if c.melt_count is not None else '—'}، "
                f"مواد {len(c.materials)}"
            )
        lines.append(f"اقلام نرخ‌دار: {len(self.rate_rows)}")
        if self.target_tons:
            lines.append(f"تناژ هدف برای پیش‌بینی: {self.target_tons}")
        if self.warnings:
            lines.append("هشدار: " + "؛ ".join(self.warnings[:5]))
        return "\n".join(lines)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error_fa": self.error_fa,
            "period": asdict(self.period) if self.period else None,
            "period_sources": self.period_sources,
            "production": self.production.as_dict() if self.production else None,
            "consumptions": {k: v.as_dict() for k, v in self.consumptions.items()},
            "rate_rows": [asdict(r) for r in self.rate_rows],
            "target_tons": self.target_tons,
            "warnings": self.warnings,
        }


def compute_main_goal(
    files: dict[str, Path | str],
    *,
    filenames: dict[str, str] | None = None,
    inventory: pd.DataFrame | None = None,
    target_tons: float | None = None,
) -> MainGoalResult:
    """Full pipeline: detect periods → compare → parse → rates → sections."""
    filenames = filenames or {}
    detected: dict[str, tuple[PeriodKey | None, str]] = {}
    period_sources: dict[str, str] = {}
    for kind in FILE_KIND_ORDER:
        if kind not in files:
            return MainGoalResult(
                ok=False,
                error_fa=f"فایل «{FILE_KINDS.get(kind, kind)}» ارسال نشده است.",
                period=None,
                period_sources={},
                production=None,
                consumptions={},
                rate_rows=[],
                sections=[],
                target_tons=target_tons,
            )
        period, src = detect_period(files[kind], filename=filenames.get(kind))
        detected[kind] = (period, src)
        period_sources[kind] = src

    err = compare_periods(detected)
    if err:
        return MainGoalResult(
            ok=False,
            error_fa=err,
            period=None,
            period_sources=period_sources,
            production=None,
            consumptions={},
            rate_rows=[],
            sections=[],
            target_tons=target_tons,
        )

    period = detected["production"][0]
    assert period is not None

    warnings: list[str] = []
    try:
        production = parse_production(files["production"], period=period)
    except Exception as exc:  # noqa: BLE001
        return MainGoalResult(
            ok=False,
            error_fa=f"خطا در خواندن آمار تولید: {exc}",
            period=period,
            period_sources=period_sources,
            production=None,
            consumptions={},
            rate_rows=[],
            sections=[],
            target_tons=target_tons,
        )
    if production.missing:
        warnings.extend(production.missing)

    consumptions: dict[str, ConsumptionStats] = {}
    for kind, section in SECTION_FOR_CONSUMPTION.items():
        try:
            consumptions[section] = parse_consumption(
                files[kind], section, period=period
            )
        except Exception as exc:  # noqa: BLE001
            return MainGoalResult(
                ok=False,
                error_fa=f"خطا در خواندن «{FILE_KINDS[kind]}»: {exc}",
                period=period,
                period_sources=period_sources,
                production=production,
                consumptions=consumptions,
                rate_rows=[],
                sections=[],
                target_tons=target_tons,
            )
        if consumptions[section].missing:
            warnings.extend(
                f"{SECTION_LABEL_FA[section]}: {m}" for m in consumptions[section].missing
            )

    # Hard fail if production has no tonnage AND no materials at all
    rate_rows = build_rate_rows(production, consumptions, inventory=inventory)
    if production.total_tons <= 0 and not rate_rows:
        detail = []
        if production.missing:
            detail.append("آمار تولید: " + "؛ ".join(production.missing))
        for sec, c in consumptions.items():
            if c.missing:
                detail.append(f"{SECTION_LABEL_FA[sec]}: " + "؛ ".join(c.missing))
        return MainGoalResult(
            ok=False,
            error_fa="دادهٔ کافی برای محاسبه یافت نشد.\n" + "\n".join(detail),
            period=period,
            period_sources=period_sources,
            production=production,
            consumptions=consumptions,
            rate_rows=[],
            sections=[],
            target_tons=target_tons,
            warnings=warnings,
        )

    # Default target = period total tons (projection table always shows period need)
    tgt = float(target_tons) if target_tons and target_tons > 0 else None
    sections = build_report_sections(
        period=period,
        production=production,
        consumptions=consumptions,
        rate_rows=rate_rows,
        target_tons=tgt,
    )
    return MainGoalResult(
        ok=True,
        error_fa=None,
        period=period,
        period_sources=period_sources,
        production=production,
        consumptions=consumptions,
        rate_rows=rate_rows,
        sections=sections,
        target_tons=tgt,
        warnings=warnings,
    )


def persist_payload(result: MainGoalResult, file_meta: dict[str, Any]) -> str:
    """JSON blob for DB results_json."""
    payload = result.to_jsonable()
    payload["files"] = file_meta
    payload["saved_at"] = tehran_now().isoformat()
    return json.dumps(payload, ensure_ascii=False)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
