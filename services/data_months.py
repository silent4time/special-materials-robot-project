"""T14b: which Jalali months ACTUALLY hold data, per report — and presets resolved on them.

Preset windows («ماه جاری»، «۳ ماه اخیر»، «از ابتدای سال»، ۶/۱۲ ماه) used to be pure
calendar windows: on 1405/07/29 «۳ ماه اخیر» = مرداد–مهر even though مهر has no
upload yet. Now a preset = the latest N months THAT HAVE DATA (not after today), and
the output states the actual basis («مبنا: ۳ ماه تیر تا شهریور ۱۴۰۵ — درخواست: …»).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import jdatetime

PRESET_N = {"current": 1, "3m": 3, "6m": 6, "12m": 12}
PRESET_LABEL = {"current": "ماه جاری", "3m": "۳ ماه اخیر", "6m": "۶ ماه اخیر", "12m": "۱۲ ماه اخیر", "ytd": "از ابتدای سال"}


def _any_user(db: Any, user: dict | None) -> dict | None:
    if user:
        return user
    try:
        for u in db.list_users():
            if u.get("role") == "owner" and int(u.get("is_active", 1) or 0):
                return u
    except Exception:  # noqa: BLE001
        pass
    return None


def monthly_frame(db: Any, user: dict | None = None):
    """Latest uploaded «مصرف ماهیانه» extract (factory-wide), or None."""
    from analytics.frames import load_extract_frame

    u = _any_user(db, user)
    if not u:
        return None
    try:
        return load_extract_frame(db, "monthly_consumption", u)
    except Exception:  # noqa: BLE001
        return None


def _greg_to_ym(v: Any) -> tuple[int, int] | None:
    try:
        d = v if isinstance(v, date) else datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    j = jdatetime.date.fromgregorian(date=d)
    return j.year, j.month


def report_months(db: Any, user: dict | None = None, mode: str = "period", section: str | None = None) -> list[tuple[int, int]]:
    """Months with data for a report ``mode`` (period / monthly_summary / daily / remaining /
    surplus / comprehensive / analytics_pdf)."""
    from analytics.tundish import months_present

    out: set[tuple[int, int]] = set()
    mf = monthly_frame(db, user)
    if mf is not None and not mf.empty:
        out |= set(months_present(mf))
    if mode == "monthly_summary":
        return sorted(out)
    if mode == "period":
        try:
            with db.connect() as conn:
                q = "SELECT DISTINCT year, month FROM main_goal_consumption WHERE year IS NOT NULL"
                args: tuple = ()
                if section:
                    q += " AND section = ?"
                    args = (section,)
                out |= {(int(r[0]), int(r[1])) for r in conn.execute(q, args).fetchall() if r[0] and r[1]}
        except Exception:  # noqa: BLE001
            pass
        try:
            # a site month counts only when a difference is computable there
            # (an earlier entry of the same item exists)
            by: dict[tuple[str, str], list[str]] = {}
            for e in db.list_site_stock_entries():
                if section and str(e.get("tundish_group") or "").removeprefix("cast_") != section:
                    continue
                by.setdefault((str(e.get("tundish_group")), str(e.get("item_id"))), []).append(str(e.get("entry_date") or "")[:10])
            for ds in by.values():
                for d in sorted(set(ds))[1:]:
                    ym = _greg_to_ym(d)
                    if ym:
                        out.add(ym)
        except Exception:  # noqa: BLE001
            pass
    else:
        from analytics.frames import load_extract_frame

        u = _any_user(db, user)
        try:
            tf = load_extract_frame(db, "tank_consumption", u) if u else None
        except Exception:  # noqa: BLE001
            tf = None
        if tf is not None and not tf.empty and "date" in tf.columns:
            out |= {ym for ym in (_greg_to_ym(v) for v in tf["date"]) if ym}
    return sorted(out)


def resolve_preset_by_data(
    preset: str, months: list[tuple[int, int]], today: jdatetime.date | None = None
) -> tuple[tuple[int, int], tuple[int, int], list[tuple[int, int]], str] | None:
    """(start_ym, end_ym, months used, basis note) or None when no month has data."""
    from analytics.tundish import basis_label_fa

    from bot.jalali import jalali_today

    t = today or jalali_today()
    avail = [m for m in sorted(set(months)) if m <= (t.year, t.month)]
    if not avail:
        return None
    if preset == "ytd":
        used = [m for m in avail if m[0] == t.year] or [m for m in avail if m[0] == avail[-1][0]]
    else:
        n = PRESET_N.get(preset) or int(str(preset).rstrip("m") or 3)
        used = avail[-n:]
    req = PRESET_LABEL.get(preset) or f"{str(preset).rstrip('m')} ماه اخیر"
    note = basis_label_fa(used, req).replace("فقط ماه‌های دارای داده", "آخرین ماه‌های دارای داده")
    return used[0], used[-1], used, note


# ---------------------------------------------------------------- «ماه جاری» = calendar month
# Bug 1405/08/02: «ماه جاری» used to be resolved as «the latest month that HAS data»
# (resolve_preset_by_data, T14b) — so in مهر it returned شهریور (the uploaded monthly
# file ended there). «ماه جاری» is now ALWAYS the calendar Jalali month of today; when it
# has no data the user is told so and offered the latest month with data (+ covered days).

def _fa(x: Any) -> str:
    return str(x).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def month_name_fa(ym: tuple[int, int]) -> str:
    from bot.jalali import PERSIAN_MONTH_NAMES

    return f"{PERSIAN_MONTH_NAMES.get(int(ym[1]), ym[1])} {_fa(int(ym[0]))}"


def month_coverage(
    db: Any, user: dict | None, mode: str, ym: tuple[int, int], section: str | None = None
) -> tuple[int, jdatetime.date | None]:
    """(covered days, last covered date) of month ``ym`` for report ``mode``."""
    from analytics.tundish import _jalali_ym_from_row

    last: jdatetime.date | None = None
    mf = monthly_frame(db, user)
    if mf is not None and not mf.empty and "date" in mf.columns:
        months_col = mf["month"] if "month" in mf.columns else [None] * len(mf)
        for d, m in zip(mf["date"], months_col):
            if _jalali_ym_from_row(d, m) != tuple(ym):
                continue
            digits = "".join(ch for ch in str(d) if ch.isdigit())
            try:
                cand = jdatetime.date(int(ym[0]), int(ym[1]), int(digits[6:8]))
            except (TypeError, ValueError):
                continue
            if last is None or cand > last:
                last = cand
    if mode == "period":
        try:
            from services import period_select as ps

            n, upto = ps.coverage(int(ym[0]), int(ym[1]), ps.latest_data_date(db))
            cand = upto or jdatetime.date(int(ym[0]), int(ym[1]), ps.month_days(int(ym[0]), int(ym[1])))
            if n and (last is None or cand > last):
                last = cand
        except Exception:  # noqa: BLE001
            pass
    if last is None:
        from services import period_select as ps

        return ps.month_days(int(ym[0]), int(ym[1])), None
    return last.day, last


def resolve_current_month(
    db: Any, user: dict | None, mode: str, section: str | None = None,
    today: jdatetime.date | None = None,
) -> dict:
    """«ماه جاری» → the CALENDAR month. Returns
    ``{"ym", "has_data", "latest", "latest_days", "latest_upto", "message"}``; ``message``
    is the Persian no-data text (empty when the current month has data)."""
    from bot.jalali import jalali_today

    t = today or jalali_today()
    ym = (t.year, t.month)
    months = [m for m in report_months(db, user, mode, section) if m <= ym]
    out: dict = {"ym": ym, "has_data": ym in months, "latest": None, "latest_days": 0,
                 "latest_upto": None, "message": ""}
    if out["has_data"]:
        return out
    msg = f"برای ماه جاری ({month_name_fa(ym)}) هنوز داده‌ای ثبت نشده است."
    if months:
        lt = months[-1]
        days, upto = month_coverage(db, user, mode, lt, section)
        out.update(latest=lt, latest_days=days, latest_upto=upto)
        upto_s = f"، تا {_fa(upto.strftime('%Y/%m/%d'))}" if upto else ""
        msg += f"\nآخرین ماه دارای داده: {month_name_fa(lt)} ({_fa(days)} روز{upto_s})."
        out["offer_label"] = f"{month_name_fa(lt)} ({_fa(days)} روز{upto_s})"
    else:
        msg += "\nهیچ ماهی برای این گزارش داده ندارد."
    out["message"] = msg
    return out
