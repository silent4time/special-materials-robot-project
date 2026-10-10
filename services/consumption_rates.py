"""Consumption-rate window shared by «🛒 درخواست مواد» (bot + web).

Rate window is independent of the coverage horizon (cleanup item 2): the rate is
the average DAILY consumption over the last ``n`` COMPLETE Jalali months (monthly
consumption total ÷ calendar days of the window); the coverage days only multiply
that rate. Materials without monthly rows fall back to site/tank daily data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import jdatetime
import pandas as pd

from analytics.tundish import daily_rates
from bot.jalali import format_month_year_range, jalali_today, month_year_to_gregorian_bounds

RATE_MONTHS = 3


@dataclass
class RateWindow:
    start_ym: tuple[int, int]
    end_ym: tuple[int, int]
    start: date
    end: date
    days: int
    label: str


def last_complete_months(n: int = RATE_MONTHS, today: jdatetime.date | None = None) -> RateWindow:
    t = today or jalali_today()
    idx = t.year * 12 + (t.month - 1) - 1  # previous month = last complete month
    end_ym = (idx // 12, idx % 12 + 1)
    sidx = idx - (n - 1)
    start_ym = (sidx // 12, sidx % 12 + 1)
    start, end = month_year_to_gregorian_bounds(start_ym, end_ym)
    return RateWindow(
        start_ym=start_ym,
        end_ym=end_ym,
        start=start,
        end=end,
        days=(end - start).days + 1,
        label=format_month_year_range(start_ym, end_ym),
    )


def rates_last_complete_months(
    tank: pd.DataFrame | None,
    monthly: pd.DataFrame | None,
    *,
    n: int = RATE_MONTHS,
    today: jdatetime.date | None = None,
) -> tuple[pd.DataFrame, str, RateWindow]:
    """(rates frame like ``daily_rates``, Persian basis line, window)."""
    w = last_complete_months(n, today)
    parts: list[pd.DataFrame] = []
    m = daily_rates(None, monthly, start=w.start, end=w.end) if monthly is not None else pd.DataFrame()
    if m is not None and not m.empty:
        m = m.copy()
        m["days_span"] = w.days
        m["avg_daily"] = pd.to_numeric(m["total_qty"], errors="coerce").fillna(0) / float(w.days)
        m["source"] = "monthly"
        parts.append(m)
    have = set(m["material_name"].astype(str)) if m is not None and not m.empty else set()
    t = daily_rates(tank, None, start=w.start, end=w.end) if tank is not None else pd.DataFrame()
    used_tank = False
    if t is not None and not t.empty:
        t = t.loc[~t["material_name"].astype(str).isin(have)]
        if not t.empty:
            used_tank = True
            parts.append(t)
    out = pd.concat(parts, ignore_index=True) if parts else daily_rates(None, None)
    basis = (
        f"مبنای نرخ مصرف: میانگین روزانهٔ {n} ماه کامل گذشته ({w.label}، {w.days} روز) "
        "از مصرف ماهیانه"
        + ("؛ اقلام بدون مصرف ماهیانه از دادهٔ روزانهٔ سایت" if used_tank else "")
        + ". روزهای پوشش فقط افق درخواست است."
    )
    if out.empty:
        basis = f"در {n} ماه کامل گذشته ({w.label}) مصرفی ثبت نشده است."
    return out, basis, w
