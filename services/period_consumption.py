"""«📅 گزارش مصرف بازه‌ای» — rebuilt from data the system actually has (bot + web).

Sources (the header states which were used):
  (الف) موجودی روزانهٔ سایت: per (بخش، قلم) the sum of DECREASES between consecutive
        daily entries whose later date is inside the range (the entry just before the
        range is the baseline). Increases = ورودی/شارژ, reported separately, never
        counted as consumption.
  (ب) جداول مصرف تاندیش ماهانهٔ «هدف اصلی» (بیلت/بلوم/اسلب): only Jalali months that
        lie COMPLETELY inside the range; quantities summed per section + material, plus
        the month's tundish count.
Never asks for a technician Excel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import jdatetime

SECTION_FA = {"slab": "اسلب", "bloom": "بلوم", "billet": "بیلت"}

SITE_COLS = ["ردیف", "بخش", "شناسه کالا", "شرح کالا", "مصرف (کاهش موجودی)", "افزایش (ورودی)", "تعداد ثبت در بازه"]
MG_COLS = ["ردیف", "بخش", "ماده", "واحد", "مصرف", "ماه‌ها"]
MG_COUNT_COLS = ["بخش", "ماه", "تعداد تاندیش"]


def _num(v: float) -> int | float:
    v = round(float(v), 2)
    return int(v) if abs(v - round(v)) < 1e-9 else v


def _to_date(s: Any) -> date | None:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def full_months_in_range(start: date, end: date) -> list[tuple[int, int]]:
    """Jalali (year, month) whose whole span lies inside [start, end]."""
    out: list[tuple[int, int]] = []
    j = jdatetime.date.fromgregorian(date=start)
    y, m = j.year, j.month
    while True:
        first = jdatetime.date(y, m, 1).togregorian()
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        last = jdatetime.date(ny, nm, 1).togregorian() - timedelta(days=1)
        if first > end:
            break
        if first >= start and last <= end:
            out.append((y, m))
        y, m = ny, nm
    return out


@dataclass
class PeriodConsumptionResult:
    start: date
    end: date
    range_label: str
    section: str | None = None
    site_rows: list[dict[str, Any]] = field(default_factory=list)
    site_entry_count: int = 0
    mg_rows: list[dict[str, Any]] = field(default_factory=list)
    mg_counts: list[dict[str, Any]] = field(default_factory=list)
    mg_months: list[tuple[int, int]] = field(default_factory=list)
    error: str | None = None
    pdf: Path | None = None
    xlsx: Path | None = None

    @property
    def section_label(self) -> str:
        return SECTION_FA.get(self.section or "", "همه بخش‌ها")

    @property
    def title(self) -> str:
        return f"گزارش مصرف بازه‌ای — {self.range_label} — {self.section_label}"

    def source_line(self) -> str:
        parts = []
        if self.site_rows:
            parts.append(
                f"(الف) اختلاف ثبت‌های «موجودی روزانه سایت» ({self.site_entry_count} ثبت؛ "
                "فقط کاهش موجودی = مصرف، افزایش = ورودی)"
            )
        else:
            parts.append("(الف) موجودی روزانه سایت: برای هیچ قلمی دو ثبت (مبنا + ثبت در بازه) نیست — اختلافی محاسبه نشد")
        if self.mg_rows or self.mg_counts:
            from bot.jalali import PERSIAN_MONTH_NAMES

            months = "، ".join(f"{PERSIAN_MONTH_NAMES[m]} {y}" for y, m in self.mg_months)
            parts.append(f"(ب) جداول مصرف تاندیش ماهانهٔ «هدف اصلی» برای ماه‌های کامل: {months}")
        return "منبع داده: " + (" + ".join(parts) if parts else "—")

    @property
    def subtitle(self) -> str:
        return f"بخش: {self.section_label} | {self.source_line()}"

    def sections(self) -> list[dict[str, Any]]:
        secs: list[dict[str, Any]] = []
        if self.site_rows:
            secs.append(
                {
                    "title": "الف) مصرف از اختلاف موجودی روزانهٔ سایت",
                    "sheet_title": "موجودی روزانه سایت",
                    "columns": SITE_COLS,
                    "rows": self.site_rows,
                    "empty_message": "—",
                    "header_bg": "#2e7d32",
                }
            )
        if self.mg_rows:
            secs.append(
                {
                    "title": "ب) مصرف از جداول تاندیش ماهانه (هدف اصلی)",
                    "sheet_title": "مصرف تاندیش ماهانه",
                    "columns": MG_COLS,
                    "rows": self.mg_rows,
                    "empty_message": "—",
                    "header_bg": "#1f4e79",
                }
            )
        if self.mg_counts:
            secs.append(
                {
                    "title": "تعداد تاندیش ماه‌های کامل (هدف اصلی)",
                    "sheet_title": "تعداد تاندیش",
                    "columns": MG_COUNT_COLS,
                    "rows": self.mg_counts,
                    "empty_message": "—",
                    "header_bg": "#6a1b9a",
                }
            )
        return secs

    def bot_text(self, top: int = 8) -> str:
        lines = [f"📅 {self.title}", self.source_line(), ""]
        if self.site_rows:
            lines.append(f"الف) موجودی روزانه سایت: {len(self.site_rows)} قلم")
            for r in sorted(self.site_rows, key=lambda r: -float(r["مصرف (کاهش موجودی)"] or 0))[:top]:
                lines.append(f"• {r['بخش']} | {r['شرح کالا'][:40]}: {r['مصرف (کاهش موجودی)']}")
        if self.mg_rows:
            lines.append(f"ب) جداول تاندیش ماهانه: {len(self.mg_rows)} ردیف")
            for r in self.mg_rows[:top]:
                lines.append(f"• {r['بخش']} | {r['ماده']}: {r['مصرف']} {r['واحد']}")
        if self.mg_counts:
            lines.append("تعداد تاندیش ماه‌های کامل:")
            for r in self.mg_counts[: top * 2]:
                lines.append(f"• {r['بخش']} | {r['ماه']}: {r['تعداد تاندیش']} تاندیش")
        lines.append("")
        lines.append("جزئیات کامل در فایل PDF و اکسل.")
        return "\n".join(lines)


def _group_fa(grp: str) -> str:
    base = grp.removeprefix("cast_")
    fa = SECTION_FA.get(base, base)
    return f"{fa} (ریخته‌گری)" if grp.startswith("cast_") else fa


def _site_rows(db: Any, start: date, end: date, section: str | None) -> tuple[list[dict], int]:
    # cast_* groups (casting-floor concretes) belong to their base section
    entries = [
        e for e in db.list_site_stock_entries()
        if not section or str(e.get("tundish_group") or "").removeprefix("cast_") == section
    ]
    by_key: dict[tuple[str, str], list[dict]] = {}
    for e in entries:
        d = _to_date(e.get("entry_date"))
        if d is None or d > end:
            continue
        by_key.setdefault((str(e["tundish_group"]), str(e["item_id"])), []).append({**e, "_d": d})
    rows: list[dict] = []
    n_in_range = 0
    for (grp, item_id), lst in by_key.items():
        lst.sort(key=lambda r: r["_d"])
        before = [r for r in lst if r["_d"] < start]
        inside = [r for r in lst if start <= r["_d"] <= end]
        if not inside:
            continue
        n_in_range += len(inside)
        seq = ([before[-1]] if before else []) + inside
        used = 0.0
        added = 0.0
        for a, b in zip(seq, seq[1:]):
            diff = float(a["quantity"] or 0) - float(b["quantity"] or 0)
            if diff > 0:
                used += diff
            else:
                added += -diff
        if len(seq) < 2:
            continue  # one entry, no baseline → no difference to report
        rows.append(
            {
                "بخش": _group_fa(grp),
                "شناسه کالا": item_id,
                "شرح کالا": str(inside[-1].get("item_name_snapshot") or ""),
                "مصرف (کاهش موجودی)": _num(used),
                "افزایش (ورودی)": _num(added),
                "تعداد ثبت در بازه": len(inside),
                "_sort": (grp, -used),
            }
        )
    rows.sort(key=lambda r: r.pop("_sort"))
    for i, r in enumerate(rows, 1):
        r["ردیف"] = i
    return rows, n_in_range


def _mg_rows(db: Any, months: list[tuple[int, int]], section: str | None) -> tuple[list[dict], list[dict], list[tuple[int, int]]]:
    from bot.jalali import PERSIAN_MONTH_NAMES

    if not months:
        return [], [], []
    wanted = set(months)
    agg: dict[tuple[str, str, str], dict[str, Any]] = {}
    counts: list[dict] = []
    used_months: set[tuple[int, int]] = set()
    with db.connect() as conn:
        cons = [dict(r) for r in conn.execute("SELECT * FROM main_goal_consumption ORDER BY sort_key, section").fetchall()]
        for c in cons:
            ym = (int(c.get("year") or 0), int(c.get("month") or 0))
            if ym not in wanted or (section and c["section"] != section):
                continue
            used_months.add(ym)
            label = f"{PERSIAN_MONTH_NAMES.get(ym[1], ym[1])} {ym[0]}"
            if c.get("tundish_count") is not None:
                counts.append(
                    {"بخش": SECTION_FA.get(c["section"], c["section"]), "ماه": label, "تعداد تاندیش": _num(c["tundish_count"])}
                )
            for m in conn.execute(
                "SELECT name, quantity, unit, keyword FROM main_goal_consumption_materials WHERE consumption_id = ? ORDER BY sort_order",
                (int(c["id"]),),
            ).fetchall():
                name = str(m["keyword"] or m["name"] or "").strip()
                key = (c["section"], name, str(m["unit"] or ""))
                a = agg.setdefault(key, {"qty": 0.0, "months": []})
                a["qty"] += float(m["quantity"] or 0)
                if label not in a["months"]:
                    a["months"].append(label)
    rows = []
    for (sec, name, unit), a in sorted(agg.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if a["qty"] <= 0:
            continue
        rows.append(
            {"بخش": SECTION_FA.get(sec, sec), "ماده": name, "واحد": unit, "مصرف": _num(a["qty"]), "ماه‌ها": "، ".join(a["months"])}
        )
    for i, r in enumerate(rows, 1):
        r["ردیف"] = i
    return rows, counts, sorted(used_months)


def data_availability(db: Any, section: str | None = None) -> dict[str, Any]:
    """What the two sources actually hold (for clear «insufficient data» messages)."""
    entries = [
        e for e in db.list_site_stock_entries()
        if not section or str(e.get("tundish_group") or "").removeprefix("cast_") == section
    ]
    dates = sorted({d for d in (_to_date(e.get("entry_date")) for e in entries) if d})
    per_item: dict[tuple[str, str], int] = {}
    for e in entries:
        k = (str(e.get("tundish_group")), str(e.get("item_id")))
        per_item[k] = per_item.get(k, 0) + 1
    with db.connect() as conn:
        q = "SELECT DISTINCT year, month FROM main_goal_consumption"
        args: tuple = ()
        if section:
            q += " WHERE section = ?"
            args = (section,)
        months = sorted({(int(r[0] or 0), int(r[1] or 0)) for r in conn.execute(q, args).fetchall()} - {(0, 0)})
    return {
        "site_dates": dates,
        "site_entries": len(entries),
        "site_items_with_diff": sum(1 for n in per_item.values() if n >= 2),
        "mg_months": months,
    }


def availability_text_fa(av: dict[str, Any]) -> str:
    from bot.jalali import PERSIAN_MONTH_NAMES, format_date

    dates = av.get("site_dates") or []
    if not dates:
        site = "• موجودی روزانهٔ سایت: هیچ ثبتی وجود ندارد."
    elif len(dates) == 1:
        site = (
            f"• موجودی روزانهٔ سایت: فقط ثبت تاریخ {format_date(dates[0])} موجود است "
            f"({av.get('site_entries', 0)} قلم)؛ برای محاسبهٔ مصرف حداقل دو ثبت در دو تاریخ "
            "برای همان قلم لازم است."
        )
    else:
        site = (
            f"• موجودی روزانهٔ سایت: ثبت‌ها از {format_date(dates[0])} تا {format_date(dates[-1])} "
            f"({len(dates)} تاریخ، {av.get('site_items_with_diff', 0)} قلم با حداقل دو ثبت)."
        )
    months = av.get("mg_months") or []
    if months:
        mg = "• مصرف تاندیش ماهانهٔ هدف اصلی: " + "، ".join(
            f"{PERSIAN_MONTH_NAMES.get(m, m)} {y}" for y, m in months
        ) + " (فقط ماه‌هایی که کامل داخل بازه باشند استفاده می‌شوند)."
    else:
        mg = "• مصرف تاندیش ماهانهٔ هدف اصلی: هیچ ماهی ثبت نشده است."
    return "دادهٔ موجود:\n" + site + "\n" + mg


def has_any_data(av: dict[str, Any]) -> bool:
    return bool(av.get("site_items_with_diff") or av.get("mg_months"))


def build(db: Any, start: date, end: date, *, range_label: str, section: str | None = None) -> PeriodConsumptionResult:
    if section not in (None, "", "slab", "bloom", "billet"):
        section = None
    res = PeriodConsumptionResult(start=start, end=end, range_label=range_label, section=section or None)
    res.site_rows, res.site_entry_count = _site_rows(db, start, end, res.section)
    res.mg_rows, res.mg_counts, res.mg_months = _mg_rows(db, full_months_in_range(start, end), res.section)
    if not res.site_rows and not res.mg_rows and not res.mg_counts:
        from bot.jalali import format_date

        res.error = (
            f"⚠️ برای بازهٔ «{range_label}» ("
            + ("" if format_date(start) in range_label else f"{format_date(start)} تا {format_date(end)} — ")
            + f"{res.section_label}) "
            "دادهٔ کافی برای گزارش مصرف نیست.\n"
            "لازم است: دو ثبت «موجودی روزانه سایت» برای یک قلم (یکی قبل یا داخل بازه و یکی داخل بازه)، "
            "یا جدول مصرف تاندیش ماهانهٔ «هدف اصلی» برای ماهی که کامل داخل بازه باشد.\n\n"
            + availability_text_fa(data_availability(db, res.section))
        )
    return res


def generate_files(
    db: Any,
    start: date,
    end: date,
    *,
    range_label: str,
    section: str | None = None,
    letterhead_path: Path | str | None = None,
    output_dir: Path | None = None,
) -> PeriodConsumptionResult:
    from config import REPORT_DIR, ensure_dirs
    from excel.simple_report import generate_simple_report_xlsx
    from pdf.generator import generate_simple_report_pdf

    res = build(db, start, end, range_label=range_label, section=section)
    if res.error:
        return res
    ensure_dirs()
    from bot.jalali import format_date

    out = (Path(output_dir) if output_dir else REPORT_DIR) / f"period_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    out.mkdir(parents=True, exist_ok=True)
    sec_fa = res.section_label.replace(" ", "_")
    stem = (
        f"گزارش_مصرف_بازه‌ای_{sec_fa}_"
        f"{format_date(res.start).replace('/', '-')}_تا_{format_date(res.end).replace('/', '-')}"
    )
    res.pdf = generate_simple_report_pdf(
        res.title, subtitle=res.subtitle, sections=res.sections(),
        output_path=out / f"{stem}.pdf", filename_stem="period_consumption", letterhead_path=letterhead_path,
    )
    res.xlsx = generate_simple_report_xlsx(
        res.title, subtitle=res.subtitle, sections=res.sections(),
        output_path=out / f"{stem}.xlsx", filename_stem="period_consumption",
    )
    return res
