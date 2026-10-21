"""Day-based periods for historical reports (bot + web share this module).

* A day is 00:00–23:59 (Jalali calendar day).
* Latest data date = newest sequence-log cast start/end (capped at today); a month with
  data only up to day X counts as X days and is labelled «مهر ۱۴۰۵ (۲۹ روز، تا ۰۷/۲۹)».
* Presets: «۳ ماه اخیر» = last 90 days ending at the latest data date (6 → 180, 12 → 365).
* Custom: number of days, a from/to date, or months picked from a Jalali year; months/dates
  after today are not selectable (future). Forecast / need scenarios are NOT restricted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import jdatetime

from bot.jalali import PERSIAN_MONTH_NAMES, jalali_today

PRESET_DAYS = {3: 90, 6: 180, 12: 365}
_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def fa(x: Any) -> str:
    return str(x).translate(_FA)


def month_days(y: int, m: int) -> int:
    if m <= 6:
        return 31
    if m <= 11:
        return 30
    return 30 if jdatetime.date(int(y), 1, 1).isleap() else 29


def jd(y: int, m: int, d: int) -> jdatetime.date:
    return jdatetime.date(int(y), int(m), int(d))


def fmt_date(d: jdatetime.date) -> str:
    return fa(f"{d.year:04d}/{d.month:02d}/{d.day:02d}")


def _parse_dt(s: Any) -> jdatetime.date | None:
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", str(s or "").translate(_EN))
    if not m:
        return None
    try:
        return jd(*map(int, m.groups()))
    except ValueError:
        return None


def today() -> jdatetime.date:
    return jalali_today()


def latest_data_date(db: Any) -> jdatetime.date:
    """Newest sequence-log date (cast start/end); production-only months → their last day."""
    best: jdatetime.date | None = None
    try:
        with db.connect() as conn:
            for a, b in conn.execute(
                "SELECT MAX(first_cast_start), MAX(last_cast_end) FROM main_goal_tundish_sequences"
            ):
                for v in (a, b):
                    d = _parse_dt(v)
                    if d and (best is None or d > best):
                        best = d
    except Exception:  # noqa: BLE001
        best = None
    try:
        for r in db.list_main_goal_production():
            if r.get("year") and r.get("month"):
                d = jd(r["year"], r["month"], month_days(r["year"], r["month"]))
                if best is None or d > best:
                    best = d
    except Exception:  # noqa: BLE001
        pass
    t = today()
    if best is None or best > t:
        best = t
    return best


def coverage(y: int, m: int, latest: jdatetime.date) -> tuple[int, jdatetime.date | None]:
    """(covered days, «up to» date when partial). Months after the latest data date → 0."""
    n = month_days(y, m)
    if (y, m) > (latest.year, latest.month):
        return 0, None
    if (y, m) == (latest.year, latest.month) and latest.day < n:
        return latest.day, latest
    return n, None


def month_name(y: int, m: int) -> str:
    return f"{PERSIAN_MONTH_NAMES.get(int(m), m)} {fa(int(y))}"


def month_label(y: int, m: int, latest: jdatetime.date, overlap: int | None = None) -> str:
    """«مهر ۱۴۰۵ (۲۹ روز، تا ۰۷/۲۹)» for a partial month; «مرداد ۱۴۰۵ (۳۰ از ۳۱ روز)» for a cut."""
    cov, upto = coverage(y, m, latest)
    base = month_name(y, m)
    if overlap is not None and 0 < overlap < cov:
        return f"{base} ({fa(overlap)} از {fa(cov)} روز)"
    if upto:
        return f"{base} ({fa(cov)} روز، تا {fa(f'{upto.month:02d}/{upto.day:02d}')})"
    return base


@dataclass
class Window:
    start: jdatetime.date
    end: jdatetime.date
    label: str
    months: list[tuple[int, int]] = field(default_factory=list)  # explicit month pick

    @property
    def days(self) -> int:
        return (self.end.togregorian() - self.start.togregorian()).days + 1


def preset_window(latest: jdatetime.date, n_months: int) -> Window:
    days = PRESET_DAYS.get(int(n_months), int(n_months) * 30)
    start = jdatetime.date.fromgregorian(date=latest.togregorian() - jdatetime.timedelta(days=days - 1))
    lab = {3: "۳ ماه اخیر", 6: "۶ ماه اخیر", 12: "یک سال اخیر"}.get(int(n_months), f"{fa(n_months)} ماه اخیر")
    return Window(start, latest, f"{lab} ({fa(days)} روز تا {fmt_date(latest)})")


def days_window(latest: jdatetime.date, n_days: int) -> Window:
    start = jdatetime.date.fromgregorian(date=latest.togregorian() - jdatetime.timedelta(days=int(n_days) - 1))
    return Window(start, latest, f"{fa(n_days)} روز اخیر (تا {fmt_date(latest)})")


def months_window(months: list[tuple[int, int]], latest: jdatetime.date) -> Window:
    ms = sorted(set((int(y), int(m)) for y, m in months))
    a, b = ms[0], ms[-1]
    end = jd(b[0], b[1], month_days(*b))
    if end > latest:
        end = latest
    label = "ماه‌های انتخابی: " + "، ".join(month_label(y, m, latest) for y, m in ms)
    return Window(jd(a[0], a[1], 1), end, label, months=ms)


def is_future_month(y: int, m: int, t: jdatetime.date | None = None) -> bool:
    t = t or today()
    return (int(y), int(m)) > (t.year, t.month)


def parse_custom(text: str, latest: jdatetime.date) -> tuple[Window | None, str | None]:
    """«۹۰ روز» / «90» / «1405/05/01 تا 1405/07/29» → Window (error text on failure)."""
    s = str(text or "").translate(_EN).strip()
    t = today()
    dates = re.findall(r"\d{4}/\d{1,2}/\d{1,2}", s)
    if len(dates) >= 2:
        a, b = _parse_dt(dates[0]), _parse_dt(dates[1])
        if not a or not b:
            return None, "تاریخ نامعتبر است (مثلاً 1405/05/01 تا 1405/07/29)."
        if b < a:
            a, b = b, a
        if b > t:
            return None, f"🔒 تاریخ آینده قابل انتخاب نیست (امروز {fmt_date(t)})."
        return Window(a, b, f"از {fmt_date(a)} تا {fmt_date(b)}"), None
    m = re.fullmatch(r"(\d{1,4})\s*(روز)?", s)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= 3660:
            return None, "تعداد روز باید بین ۱ و ۳۶۶۰ باشد."
        return days_window(latest, n), None
    return None, None


def year_month_states(year: int, latest: jdatetime.date | None = None) -> list[tuple[int, str]]:
    """[(month, state)] state: past | current (partial) | future (locked)."""
    t = today()
    out = []
    for m in range(1, 13):
        if (year, m) > (t.year, t.month):
            out.append((m, "future"))
        elif (year, m) == (t.year, t.month):
            out.append((m, "current"))
        else:
            out.append((m, "past"))
    return out
