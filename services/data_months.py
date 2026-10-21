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
