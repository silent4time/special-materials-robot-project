"""Jalali (هجری شمسی) display helpers for user-facing dates/times.

Internal storage stays Gregorian ISO where convenient; every message/PDF
shown to the user should go through these helpers.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
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
