"""Per-tundish consumption by tundish type (بیلت / بلوم / اسلب) — 1405/07/25.

RATES USED IN EVERY CALCULATION (اقلام بحرانی، نیاز N تاندیش، ترکیب تاندیش):
  the user's per-tundish columns of منبع اصلی (نوسازی / پچینگ for each type + سطح
  ریخته‌گری per tundish) via ``services.n_tundish_report.rate_entries`` — one tundish of
  a type consumes «نوسازی + پچینگ» («با نوسازی») or «پچینگ» («بدون نوسازی»).

EMPIRICAL CHECK (comparison only, never used in a need):
  warehouse issues of «مصرف ماهیانه مواد» (latest upload; درخواستی − برگشتی, split by
  سفارش کار → اسلب/بلوم/بیلت) in the months that ALSO have the tundish sequence log
  (main_goal_tundish_sequences), divided by the logged tundish count of that type in
  those months. Issues are not exact consumption (stocking timing, returns, unknown
  work orders), so it is shown next to the منبع اصلی rate, not instead of it.
Read-only: never writes the DB or the source files.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from bot.jalali import PERSIAN_MONTH_NAMES

TYPES = ("billet", "bloom", "slab")
TYPE_FA = {"billet": "بیلت", "bloom": "بلوم", "slab": "اسلب"}
KG_UNITS = {"kg", "کیلوگرم", "كيلوگرم", "کیلو"}


def _cat(v: Any) -> str:
    s = str(v if v is not None else "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s if s.isdigit() else ""


def _year(v: Any) -> int | None:
    s = "".join(ch for ch in str(v if v is not None else "") if ch.isdigit())
    return int(s[:4]) if len(s) >= 4 and 1300 <= int(s[:4]) <= 1500 else None


def _monthly_source(db, user) -> Path | None:
    ex = db.get_latest_extracted_any("monthly_consumption")
    if not ex and user:
        ex = db.get_latest_extracted(user["bale_user_id"], "monthly_consumption")
    if not ex:
        return None
    for key in ("raw_path", "clean_path"):
        p = ex.get(key)
        if p and Path(str(p)).is_file():
            return Path(str(p))
    return None


def empirical_rates(db, user=None) -> dict[str, Any]:
    """{months, counts, rates: {code: {type: {qty, kg, unit}}}, unknown_rows, source, error}."""
    from excel.monthly_summary import _month_int, _signed_quantity, load_monthly_detail
    from excel.work_order import group_for_work_order

    out: dict[str, Any] = {"months": [], "counts": {t: 0.0 for t in TYPES}, "rates": {},
                           "unknown_rows": 0, "source": "", "error": None}
    src = _monthly_source(db, user)
    if src is None:
        out["error"] = "فایل مصرف ماهیانه مواد (حواله انبار) یافت نشد."
        return out
    out["source"] = src.name
    detail = load_monthly_detail(src)
    rows = []
    for _, r in detail.iterrows():
        y, m = _year(r.get("تاریخ")), _month_int(r.get("ماه"))
        if y and m:
            rows.append((y, int(m), r))
    months = sorted({(y, m) for y, m, _ in rows})
    used = []
    for y, m in months:
        seq = db.sequence_tundish_counts(y, m) or {}
        if sum(float(seq.get(t, 0) or 0) for t in TYPES) > 0:
            used.append((y, m))
            for t in TYPES:
                out["counts"][t] += float(seq.get(t, 0) or 0)
    out["months"] = used
    if not used:
        out["error"] = "هیچ ماهی هم حواله انبار و هم لاگ توالی تاندیش ندارد."
        return out
    keep = set(used)
    for y, m, r in rows:
        if (y, m) not in keep:
            continue
        code = _cat(r.get("کد دسته بندی"))
        if not code:
            continue
        grp = group_for_work_order(r.get("سفارش کار"))
        if grp not in TYPES:
            out["unknown_rows"] += 1
            continue
        try:
            qty = _signed_quantity(float(r.get("مقدار")), r.get("درخواستی-برگشتی"))
        except (TypeError, ValueError):
            continue
        try:
            coeff = float(r.get("ضریب"))
            coeff = coeff if coeff == coeff and coeff > 0 else 1.0
        except (TypeError, ValueError):
            coeff = 1.0
        d = out["rates"].setdefault(code, {}).setdefault(grp, {"qty": 0.0, "kg": 0.0, "unit": ""})
        d["qty"] += qty
        d["kg"] += qty * coeff
        d["unit"] = d["unit"] or str(r.get("واحد") or "").strip()
    return out


def months_label(months: list[tuple[int, int]]) -> str:
    if not months:
        return ""
    (y0, m0), (y1, m1) = months[0], months[-1]
    a = f"{PERSIAN_MONTH_NAMES.get(m0, m0)} {y0}"
    b = f"{PERSIAN_MONTH_NAMES.get(m1, m1)} {y1}"
    return a if a == b else f"{a} تا {b}"


def empirical_col(t: str) -> str:
    return f"حواله انبار هر تاندیش {TYPE_FA[t]} (مقایسه)"


def empirical_per_tundish(emp: dict[str, Any], codes: list[str], main_unit: str) -> dict[str, float | None]:
    """Observed issue per ONE tundish of each type for ``codes`` (summed), in the
    منبع اصلی unit (kg = مقدار × ضریب when the main unit is kilogram)."""
    use_kg = str(main_unit or "").strip().lower() in KG_UNITS
    res: dict[str, float | None] = {}
    for t in TYPES:
        n = float(emp["counts"].get(t) or 0)
        vals = [emp["rates"].get(c, {}).get(t) for c in codes]
        vals = [v for v in vals if v]
        if n <= 0 or not vals:
            res[t] = None
            continue
        total = sum(v["kg"] if use_kg else v["qty"] for v in vals)
        res[t] = total / n
    return res


def attach_empirical(res, emp: dict[str, Any]) -> None:
    """Add the 3 comparison columns + توضیحات (source, period, counts, gaps) to a
    ``n_tundish_report.MixResult`` built by ``build_mix_rows``."""
    from services.n_tundish_report import COL_CODE, COL_ROW, COL_UNIT

    cols = [empirical_col(t) for t in TYPES]
    res.extra_columns = cols
    lacking: list[str] = []
    for r in res.rows:
        if r.get(COL_ROW) in ("", None):
            for c in cols:
                r[c] = "—"
            continue
        codes = [c for c in str(r[COL_CODE]).split("/") if c]
        per = empirical_per_tundish(emp, codes, r.get(COL_UNIT)) if not emp.get("error") else {}
        for t, c in zip(TYPES, cols):
            v = per.get(t)
            r[c] = "—" if v is None else round(v, 3)
        if all(per.get(t) is None for t in TYPES):
            lacking.append(str(r[COL_CODE]))
    c = emp.get("counts") or {}
    if emp.get("error"):
        res.notes = [f"ستون‌های «حواله انبار هر تاندیش» خالی‌اند: {emp['error']}"]
        return
    res.notes = [
        "نرخ‌های محاسبه = ستون‌های مصرف هر تاندیش منبع اصلی (نوسازی/پچینگ هر نوع + سطح ریخته‌گری)؛ "
        "همان نرخ‌های اقلام بحرانی و نیاز N تاندیش.",
        f"ستون‌های «حواله انبار هر تاندیش» فقط برای مقایسه‌اند: حوالهٔ خالص (درخواستی − برگشتی) فایل "
        f"«{emp.get('source')}» در {months_label(emp['months'])} بر اساس سفارش کار ÷ تعداد تاندیش همان "
        f"نوع در لاگ توالی تاندیش (بیلت {c.get('billet', 0):g}، بلوم {c.get('bloom', 0):g}، "
        f"اسلب {c.get('slab', 0):g})؛ واحد = واحد منبع اصلی (کیلوگرم = مقدار × ضریب).",
        f"ردیف‌های حواله بدون سفارش کار اسلب/بلوم/بیلت (کنار گذاشته شد): {emp.get('unknown_rows', 0)}. "
        "حواله برای هر کد چهاررقمی است و شرکت/پیمانکار را از هم جدا نمی‌کند.",
    ]
    if lacking:
        res.notes.append("بدون حواله در این دوره (فقط نرخ منبع اصلی): " + "، ".join(lacking) + ".")
    if float(c.get("bloom") or 0) < 5:
        res.notes.append(f"تاندیش بلوم در این دوره فقط {c.get('bloom', 0):g} عدد بود؛ نرخ تجربی بلوم قابل اتکا نیست.")
