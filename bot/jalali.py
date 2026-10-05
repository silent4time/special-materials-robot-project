"""Jalali (هجری شمسی) display helpers for user-facing dates/times.

Internal storage stays Gregorian ISO where convenient; every message/PDF
shown to the user should go through these helpers.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import jdatetime

TEHRAN = ZoneInfo("Asia/Tehran")

# Jalali or Gregorian: YYYY/MM/DD or YYYY-MM-DD
_DATE_TOKEN = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})")
_CUSTOM_RANGE_RE = re.compile(
    r"از\s*(\d{4}[/-]\d{1,2}[/-]\d{1,2})\s*تا\s*(\d{4}[/-]\d{1,2}[/-]\d{1,2})",
    re.UNICODE,
)


def _to_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(TEHRAN).date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # datetime ISO with time
    if "T" in text or " " in text:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(TEHRAN).date()
        except ValueError:
            pass
    # date only
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    parsed = parse_user_date(text)
    return parsed


def format_date(d: date | datetime | str | None) -> str:
    """Format as Jalali ``1403/07/04`` (empty string if missing/unparseable)."""
    g = _to_date(d)
    if g is None:
        return ""
    j = jdatetime.date.fromgregorian(date=g)
    return f"{j.year:04d}/{j.month:02d}/{j.day:02d}"


def format_datetime(
    dt: datetime | str | None,
    *,
    tz: ZoneInfo | str | None = TEHRAN,
) -> str:
    """Format as Jalali ``1403/07/04 13:40`` in Asia/Tehran by default."""
    if dt is None:
        return ""
    zone = TEHRAN if tz is None else (ZoneInfo(tz) if isinstance(tz, str) else tz)
    if isinstance(dt, str):
        text = dt.strip()
        if not text:
            return ""
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            # date-only → midnight Tehran
            g = _to_date(text)
            if g is None:
                return ""
            parsed = datetime(g.year, g.month, g.day, tzinfo=zone)
        dt = parsed
    if dt.tzinfo is None:
        # naive: treat as UTC if looks like DB stamp, else as Tehran local
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(zone)
    j = jdatetime.datetime.fromgregorian(datetime=local)
    return f"{j.year:04d}/{j.month:02d}/{j.day:02d} {j.hour:02d}:{j.minute:02d}"


def parse_user_date(text: str) -> Optional[date]:
    """Parse a single user date.

    Accepts Jalali ``YYYY/MM/DD`` or ``YYYY-MM-DD`` (years typically 1300–1500)
    and Gregorian ``YYYY-MM-DD`` / ``YYYY/MM/DD`` (years typically 1900–2100).
    """
    m = _DATE_TOKEN.search((text or "").strip())
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        if 1200 <= year <= 1500:
            return jdatetime.date(year, month, day).togregorian()
        if 1900 <= year <= 2100:
            return date(year, month, day)
    except ValueError:
        return None
    return None


def parse_user_date_range(text: str) -> Optional[tuple[date, date]]:
    """Parse «از YYYY/MM/DD تا YYYY/MM/DD» (Jalali or Gregorian tokens)."""
    m = _CUSTOM_RANGE_RE.search((text or "").strip())
    if not m:
        return None
    start = parse_user_date(m.group(1))
    end = parse_user_date(m.group(2))
    if not start or not end:
        return None
    if end < start:
        start, end = end, start
    return start, end


def tehran_now() -> datetime:
    return datetime.now(TEHRAN)


# --- Month/year range (گزارش‌ها: از ماه/سال تا ماه/سال) ---

PERSIAN_MONTH_NAMES: dict[int, str] = {
    1: "فروردین",
    2: "اردیبهشت",
    3: "خرداد",
    4: "تیر",
    5: "مرداد",
    6: "شهریور",
    7: "مهر",
    8: "آبان",
    9: "آذر",
    10: "دی",
    11: "بهمن",
    12: "اسفند",
}

PERSIAN_MONTH_NAME_TO_NUM: dict[str, int] = {
    name: num for num, name in PERSIAN_MONTH_NAMES.items()
}

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

_MONTH_YEAR_TOKEN = re.compile(
    r"(?:"
    r"(?P<y1>\d{4})\s*[/-]\s*(?P<m1>\d{1,2})"
    r"|"
    r"(?P<name>" + "|".join(PERSIAN_MONTH_NAMES.values()) + r")\s+(?P<y2>\d{4})"
    r"|"
    r"(?P<m2>\d{1,2})\s*[/-]\s*(?P<y3>\d{4})"
    r")",
    re.UNICODE,
)

_MONTH_YEAR_RANGE_RE = re.compile(
    r"از\s*(.+?)\s*تا\s*(.+)$",
    re.UNICODE,
)


def _latin_digits(text: str) -> str:
    return (text or "").translate(_PERSIAN_DIGITS)


def jalali_today() -> jdatetime.date:
    return jdatetime.date.fromgregorian(date=tehran_now().date())


def ym_key(year: int, month: int) -> int:
    return int(year) * 12 + int(month)


def parse_month_year_token(text: str) -> Optional[tuple[int, int]]:
    """Parse a single Jalali month/year token.

    Accepts ``1405/01``, ``1405-1``, ``فروردین 1405``, ``01/1405``.
    """
    raw = _latin_digits((text or "").strip())
    if not raw:
        return None
    m = _MONTH_YEAR_TOKEN.fullmatch(raw) or _MONTH_YEAR_TOKEN.search(raw)
    if not m:
        return None
    try:
        if m.group("y1") is not None:
            year, month = int(m.group("y1")), int(m.group("m1"))
        elif m.group("name") is not None:
            year = int(m.group("y2"))
            month = PERSIAN_MONTH_NAME_TO_NUM[m.group("name")]
        else:
            month, year = int(m.group("m2")), int(m.group("y3"))
    except (TypeError, ValueError, KeyError):
        return None
    if not (1200 <= year <= 1500 and 1 <= month <= 12):
        return None
    return year, month


def parse_month_year_range(text: str) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
    """Parse «از ۱۴۰۵/۰۱ تا ۱۴۰۵/۰۶» or «از فروردین 1405 تا شهریور 1405»."""
    raw = _latin_digits((text or "").strip())
    m = _MONTH_YEAR_RANGE_RE.search(raw)
    if not m:
        # bare "YYYY/MM تا YYYY/MM" without از
        if "تا" in raw:
            left, _, right = raw.partition("تا")
            start = parse_month_year_token(left)
            end = parse_month_year_token(right)
            if start and end:
                if ym_key(*end) < ym_key(*start):
                    start, end = end, start
                return start, end
        return None
    start = parse_month_year_token(m.group(1))
    end = parse_month_year_token(m.group(2))
    if not start or not end:
        return None
    if ym_key(*end) < ym_key(*start):
        start, end = end, start
    return start, end


def format_month_year(year: int, month: int, *, named: bool = True) -> str:
    """Format as «فروردین ۱۴۰۵» or ``1405/01``."""
    if named:
        name = PERSIAN_MONTH_NAMES.get(int(month), str(month))
        return f"{name} {int(year)}"
    return f"{int(year):04d}/{int(month):02d}"


def format_month_year_range(
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    named: bool = True,
) -> str:
    """Persian caption like «از فروردین 1405 تا شهریور 1405»."""
    return (
        f"از {format_month_year(start[0], start[1], named=named)} "
        f"تا {format_month_year(end[0], end[1], named=named)}"
    )


def month_year_to_gregorian_bounds(
    start: tuple[int, int],
    end: tuple[int, int],
) -> tuple[date, date]:
    """Inclusive Gregorian date bounds covering Jalali [start_ym .. end_ym]."""
    sy, sm = int(start[0]), int(start[1])
    ey, em = int(end[0]), int(end[1])
    start_g = jdatetime.date(sy, sm, 1).togregorian()
    if em == 12:
        next_first = jdatetime.date(ey + 1, 1, 1)
    else:
        next_first = jdatetime.date(ey, em + 1, 1)
    end_g = next_first.togregorian() - timedelta(days=1)
    if end_g < start_g:
        start_g, end_g = end_g, start_g
    return start_g, end_g


def resolve_month_year_preset(
    preset: str,
    *,
    today: jdatetime.date | None = None,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """
    preset: current | 3m | ytd

    Returns inclusive ((start_y, start_m), (end_y, end_m)).
    """
    today = today or jalali_today()
    key = _latin_digits((preset or "").strip()).casefold()
    end = (today.year, today.month)
    if key in {"current", "ماه جاری", "this_month", "current_month"}:
        return end, end
    if key in {"3m", "3", "۳ ماه اخیر", "3 ماه اخیر", "last_3", "recent_3"}:
        # inclusive: current month and two prior months
        y, m = today.year, today.month
        for _ in range(2):
            m -= 1
            if m < 1:
                m = 12
                y -= 1
        return (y, m), end
    if key in {"ytd", "year", "از ابتدای سال", "year_to_date", "start_of_year"}:
        return (today.year, 1), end
    raise ValueError(f"بازه ماه/سال از پیش‌تعریف‌شده نامعتبر: {preset}")


def iter_month_years(
    start: tuple[int, int],
    end: tuple[int, int],
) -> list[tuple[int, int]]:
    """List inclusive (year, month) pairs from start to end."""
    y, m = int(start[0]), int(start[1])
    ey, em = int(end[0]), int(end[1])
    out: list[tuple[int, int]] = []
    while ym_key(y, m) <= ym_key(ey, em):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
        if len(out) > 240:  # safety
            break
    return out


def year_choices_around(today: jdatetime.date | None = None, before: int = 3, after: int = 1) -> list[int]:
    """Years for reply-keyboard picker (newest last)."""
    today = today or jalali_today()
    return list(range(today.year - before, today.year + after + 1))


def days_in_jalali_month(year: int, month: int) -> int:
    """Number of days in a Jalali month (1..12). Esfand is 29 or 30."""
    y, m = int(year), int(month)
    if m < 1 or m > 12:
        raise ValueError(f"ماه نامعتبر: {month}")
    if m <= 6:
        return 31
    if m <= 11:
        return 30
    # Esfand: next Farvardin 1 minus this Esfand 1
    next_first = jdatetime.date(y + 1, 1, 1).togregorian()
    this_first = jdatetime.date(y, 12, 1).togregorian()
    return (next_first - this_first).days
