"""Consumption-rate window shared by «🛒 درخواست مواد» (bot + web).

Rate window is independent of the coverage horizon (cleanup item 2): the rate is
the average DAILY consumption over the last ``n`` COMPLETE Jalali months (net monthly
consumption ÷ days of the months in that window that actually have data — months
without an uploaded file are excluded, never counted as zero); the coverage days only multiply
that rate. Materials without monthly rows fall back to site/tank daily data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import jdatetime
import pandas as pd

from analytics.tundish import basis_label_fa, daily_rates, months_present
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
    """(rates frame like ``daily_rates``, Persian basis line, window).

    T14: only months that ACTUALLY have uploaded monthly data count — the average
    divides by the days of those months, never by the whole calendar window. When
    no month of the window has data, the latest ``n`` months that do (before the
    window end) are used instead, and the basis line says so.
    """
    w = last_complete_months(n, today)
    requested = f"{n} ماه کامل گذشته ({w.label})"
    parts: list[pd.DataFrame] = []
    m = daily_rates(None, monthly, start=w.start, end=w.end) if monthly is not None else pd.DataFrame()
    fallback = False
    if (m is None or m.empty) and monthly is not None and not monthly.empty:
        avail = [ym for ym in months_present(monthly) if ym <= w.end_ym][-n:]
        if avail:
            s0, e0 = month_year_to_gregorian_bounds(avail[0], avail[-1])
            m = daily_rates(None, monthly, start=s0, end=e0)
            fallback = True
    basis_months = list((m.attrs or {}).get("basis_months") or []) if m is not None else []
    if m is not None and not m.empty:
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
    out.attrs["basis_months"] = basis_months
    if basis_months:
        basis = (
            "مبنای نرخ مصرف: میانگین روزانه از مصرف ماهیانه — "
            + basis_label_fa(basis_months, requested)
            + ("؛ در بازهٔ درخواستی هیچ ماهی داده نداشت، آخرین ماه‌های دارای داده استفاده شد" if fallback else "")
            + ("؛ اقلام بدون مصرف ماهیانه از دادهٔ روزانهٔ سایت" if used_tank else "")
            + ". روزهای پوشش فقط افق درخواست است."
        )
    elif used_tank:
        basis = f"مبنای نرخ مصرف: دادهٔ روزانهٔ سایت در {requested}. روزهای پوشش فقط افق درخواست است."
    else:
        basis = f"در {requested} و پیش از آن مصرفی ثبت نشده است."
    return out, basis, w
