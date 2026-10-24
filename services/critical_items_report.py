"""اقلام بحرانی — shared PDF + xlsx builder for Bale bot AND web panel.

One code path (do not duplicate in bot/web):
  • inventory = ``analytics.frames.load_primary_inventory`` (ledger applied ONCE
    there — callers must NOT re-apply ``apply_inventory_ledger``)
  • rows split by «تأمین‌کننده» (شرکت/پیمانکار) (``analytics.critical_items`` segments)
  • mode «با نوسازی» (renovation + patching) / «بدون نوسازی» (patching only)
  • company PDF (primary, only «شرکت» items), contractor PDF (only «پیمانکار»),
    one xlsx with one sheet per segment (شرکت | پیمانکار)
  • TEMPLATE-LOCKED look (``excel.critical_template``): every sheet / PDF copies the
    user's sample «140506» (2026-10-05) — title bar, per-column fills, «توضیحات» at
    the bottom of the SAME sheet. Exempt from the shared Excel styler and from the
    material base-columns rule (CONTRIBUTING.md → template-locked reports).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from analytics.critical_items import (
    COL_DAYS,
    SOURCE_MANUAL,
    SOURCE_SEQUENCE_LOG,
    basis_from_months,
    previous_complete_months,
    COL_FORECAST,
    COL_HORIZON,
    COL_MONTHLY,
    COL_NEED,
    COL_ORIGIN,
    RENO_LABEL_FA,
    RENO_WITH,
    RENO_WITHOUT,
    REPORT_COLUMNS,
    SEGMENT_COMPANY,
    SEGMENT_CONTRACTOR,
    SEGMENT_LABEL_FA,
    SEGMENTS,
    TundishMonthCounts,
    average_tundish_basis,
    basis_counts_text,
    build_critical_items_rows,
    horizon_header_note,
    origin_audit,
    critical_item_count,
    group_row_indices,
    normalize_reno_mode,
    report_footer_notes,
    report_subtitle,
    report_title,
    rows_for_simple_report,
)
from bot.jalali import PERSIAN_MONTH_NAMES
from config import REPORT_DIR, ensure_dirs
from db.models import Database
from services.units import unit_fa

SEGMENT_FILE_SUFFIX = {SEGMENT_COMPANY: "شرکت", SEGMENT_CONTRACTOR: "پیمانکار"}
RENO_FILE_SUFFIX = {RENO_WITH: "با_نوسازی", RENO_WITHOUT: "بدون_نوسازی"}


@dataclass
class CriticalItemsResult:
    counts: TundishMonthCounts | None = None
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    pdfs: dict[str, Path] = field(default_factory=dict)
    xlsx: Path | None = None
    titles: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    reno_mode: str = RENO_WITH
    rates_hint: bool = False
    rates_note: str = ""
    # v1.3.1: per segment «نرخ‌دار: بیلت N، اسلب M …» (shows missing rates).
    rates_lines: dict[str, str] = field(default_factory=dict)
    audit: dict[str, list[str]] = field(default_factory=dict)
    # template sheets (rows + fixed notes, no user notes) — the draft/approve
    # workflow (services.critical_report_archive) re-renders files from these.
    sheets: list[dict[str, Any]] = field(default_factory=list)

    @property
    def header_note(self) -> str:
        return horizon_header_note(self.counts) if self.counts is not None else ""

    @property
    def reno_label(self) -> str:
        return RENO_LABEL_FA[self.reno_mode]

    def row_count(self, segment: str) -> int:
        # Items = plain codes + shared-need groups (member rows not counted).
        return critical_item_count(self.frames.get(segment))

    @property
    def company_pdf(self) -> Path | None:
        return self.pdfs.get(SEGMENT_COMPANY)

    @property
    def contractor_pdf(self) -> Path | None:
        return self.pdfs.get(SEGMENT_CONTRACTOR)


def report_date_parts(report_date: Any = None) -> tuple[int, int, int]:
    """(year, month, day) of the report date; default = today (Asia/Tehran, Jalali)."""
    if report_date is None:
        from bot.jalali import jalali_today

        report_date = jalali_today()
    if isinstance(report_date, (tuple, list)):
        y, m, d = (int(x) for x in report_date)
        return y, m, d
    return int(report_date.year), int(report_date.month), int(report_date.day)


TODAY_COUNTS_KEY = "critical_today_counts"


def get_today_counts(db: Database) -> dict[str, Any] | None:
    """Latest «تعداد تاندیش ماهانه از امروز» entry: billet/bloom/slab + entry_date (Jalali)."""
    import json

    raw = db.get_setting(TODAY_COUNTS_KEY)
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return {"billet": int(d["billet"]), "bloom": int(d["bloom"]), "slab": int(d["slab"]),
                "entry_date": str(d["entry_date"]), "by": str(d.get("by") or "")}
    except Exception:  # noqa: BLE001
        return None


def save_today_counts(db: Database, *, billet: int, bloom: int, slab: int,
                      user: dict[str, Any] | None = None) -> dict[str, Any]:
    """Store today's monthly tundish counts (dated by ENTRY date, not a month)."""
    import json

    vals = [int(billet), int(bloom), int(slab)]
    if any(v < 0 for v in vals):
        raise ValueError("تعداد تاندیش باید عدد صحیح ≥ ۰ باشد.")
    y, m, d = report_date_parts(None)
    entry = {"billet": vals[0], "bloom": vals[1], "slab": vals[2],
             "entry_date": f"{y}/{m:02d}/{d:02d}",
             "by": str((user or {}).get("bale_user_id") or "")}
    db.set_setting(TODAY_COUNTS_KEY, json.dumps(entry, ensure_ascii=False))
    return entry


def today_counts_line(db: Database) -> str:
    """«آخرین ثبت دستی: 1405/08/02 — اسلب 70، بیلت 34، بلوم 0» (+ whether used today)."""
    e = get_today_counts(db)
    if not e:
        return "ثبت دستی تعداد تاندیش برای امروز: ندارد (مبنا = میانگین لاگ توالی تاندیش ۳ ماه کامل گذشته)."
    y, m, d = report_date_parts(None)
    used = e["entry_date"] == f"{y}/{m:02d}/{d:02d}"
    return (
        f"آخرین ثبت دستی: {e['entry_date']} — اسلب {e['slab']}، بیلت {e['billet']}، بلوم {e['bloom']}"
        + ("" if used else " (مربوط به امروز نیست؛ مبنای امروز = میانگین لاگ توالی تاندیش)")
    )


def critical_basis(db: Database, report_date: Any = None) -> TundishMonthCounts | None:
    """Tundish basis of a critical-items report generated on ``report_date`` (default today).

    Months = the 3 COMPLETE months before the report date (1405/07/14 → تیر, مرداد,
    شهریور). Per month the tundish sequence log (main_goal_tundish_sequences; one
    sequence = one tundish use) is preferred; a manual «تعداد تاندیش ماهانه» entry is
    used only for a month without a log and only if not ``exclude_from_basis``.
    A section without sequences in a logged month counts 0. Months with no data at
    all are skipped (header says how many months were averaged).
    """
    y, m, d = report_date_parts(report_date)
    entry = get_today_counts(db)
    if entry and entry["entry_date"] == f"{y}/{m:02d}/{d:02d}":
        b, bl, s = entry["billet"], entry["bloom"], entry["slab"]
        return TundishMonthCounts(
            jalali_year=y, jalali_month=m, count_billet=b, count_bloom=bl, count_slab=s,
            basis_months=((y, m, b, bl, s, SOURCE_MANUAL),),
            report_date=f"{y}/{m:02d}/{d:02d}", manual_entry_date=entry["entry_date"],
        )
    manual = {
        (int(r["jalali_year"]), int(r["jalali_month"])): r
        for r in db.list_monthly_tundish_counts(limit=240)
        if not int(r.get("exclude_from_basis") or 0)
    }
    used: list[tuple] = []
    for yy, mm in previous_complete_months(y, m):
        seq = db.sequence_tundish_counts(yy, mm)
        if seq:
            used.append((yy, mm, seq.get("billet", 0), seq.get("bloom", 0), seq.get("slab", 0),
                         SOURCE_SEQUENCE_LOG))
        elif (yy, mm) in manual:
            r = manual[(yy, mm)]
            used.append((yy, mm, int(r["count_billet"]), int(r["count_bloom"]),
                         int(r["count_slab"]), SOURCE_MANUAL))
    if not used:
        # T14: no data in the 3-month window → latest (up to 3) months that DO have
        # data before it (the header names the months actually used).
        win = previous_complete_months(y, m)
        idx = win[0][0] * 12 + win[0][1] - 1 - 1
        for _ in range(36):
            if len(used) >= 3:
                break
            yy, mm = idx // 12, idx % 12 + 1
            idx -= 1
            seq = db.sequence_tundish_counts(yy, mm)
            if seq:
                used.append((yy, mm, seq.get("billet", 0), seq.get("bloom", 0), seq.get("slab", 0),
                             SOURCE_SEQUENCE_LOG))
            elif (yy, mm) in manual:
                r = manual[(yy, mm)]
                used.append((yy, mm, int(r["count_billet"]), int(r["count_bloom"]),
                             int(r["count_slab"]), SOURCE_MANUAL))
        used.sort(key=lambda t: (t[0], t[1]))
    return basis_from_months(used, report_date=f"{y}/{m:02d}/{d:02d}", year=y, month=m)


def month_counts(db: Database, jalali_year: int, jalali_month: int) -> TundishMonthCounts | None:
    """Tundish basis = AVERAGE of the manual monthly counts of the 3 months ending at
    (jalali_year, jalali_month); months without an entry are skipped (named in header)."""
    return average_tundish_basis(
        int(jalali_year), int(jalali_month), db.list_monthly_tundish_counts(limit=240)
    )


def build_critical_items_segments(
    inventory_df: pd.DataFrame | None,
    counts: TundishMonthCounts,
    reno_mode: str = RENO_WITH,
) -> dict[str, pd.DataFrame]:
    """{company: df, contractor: df} — rows already split before aggregation."""
    return {
        seg: build_critical_items_rows(
            inventory_df, counts, segment=seg, reno_mode=reno_mode
        )
        for seg in SEGMENTS
    }


# Columns of the user's sample «140506» ONLY (1405/07/25 follow-up): the 4 helper
# columns (مبدأ، افق، میانگین مصرف ماهانه، مصرف پیش‌بینی‌شده) stay internal — the need
# is still computed from them in analytics.critical_items, they are just not printed.
TEMPLATE_COLUMNS = ["ردیف", "کد چهاررقمی", "کد و شرح کالا", "موجودی", "واحد", COL_NEED, COL_DAYS]
# «توضیحات» block: the essential notes + the user's own notes, padded with empty
# bordered lines (sample style) up to this many rows.
MIN_NOTE_ROWS = 5
HORIZON_NOTE = "افق محاسبه: اجناس داخلی ۳ ماه، اجناس وارداتی ۶ ماه."


def _segment_table_section(segment: str, df: pd.DataFrame, reno_mode: str) -> dict[str, Any]:
    """Table of one segment in the sample's column set (no base material columns)."""
    label = SEGMENT_LABEL_FA[segment]
    mode = RENO_LABEL_FA[reno_mode]
    rows = [{c: r.get(c) for c in TEMPLATE_COLUMNS} for r in rows_for_simple_report(df)]
    return {
        "title": f"اقلام بحرانی — {label} ({mode})",
        "sheet_title": f"اقلام بحرانی — {label}",
        "columns": list(TEMPLATE_COLUMNS),
        "rows": rows,
        "bold_rows": group_row_indices(df),
        "empty_message": (
            f"قلم «{label}» با کسری (نیاز مثبت) در افق ۳/۶ ماه ({mode}) یافت نشد."
        ),
    }


def fixed_notes(counts: TundishMonthCounts) -> list[str]:
    """The only printed notes: the horizon + the report date / tundish basis."""
    if counts.manual_entry_date:
        return [HORIZON_NOTE, f"تاریخ گزارش: {counts.report_date} — مبنا: از امروز، مصرف هر "
                f"تاندیش (منبع اصلی) × تعداد تاندیش ماهانهٔ ثبت دستی ({basis_counts_text(counts)}؛ "
                f"ثبت‌شده در {counts.manual_entry_date})؛ موجودی امروز."]
    basis = counts.basis_label()
    basis_txt = (
        f" — مبنا: مصرف هر تاندیش (منبع اصلی) × میانگین ماهانه تعداد تاندیش {basis} "
        f"({basis_counts_text(counts)})"
        if basis else ""
    )
    return [HORIZON_NOTE, f"تاریخ گزارش: {counts.report_date or counts.month_label()}{basis_txt}."]


def user_note_lines(text: str | None) -> list[str]:
    """User's typed notes → one line per non-empty line (whitespace trimmed)."""
    return [ln.strip() for ln in str(text or "").replace("\r", "\n").split("\n") if ln.strip()]


def compose_notes(fixed: list[str], user_notes: str | None = None,
                  min_rows: int = MIN_NOTE_ROWS) -> list[str]:
    """fixed notes + user notes, then "" (empty bordered rows) up to ``min_rows``."""
    lines = [n for n in fixed if str(n).strip()] + user_note_lines(user_notes)
    return lines + [""] * max(0, min_rows - len(lines))


def _notes_section(
    counts: TundishMonthCounts, reno_mode: str, audit: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    return {
        "title": "توضیحات",
        "columns": ["توضیح"],
        "rows": [{"توضیح": n} for n in report_footer_notes(counts, reno_mode, audit)],
        "empty_message": "",
    }


def template_sheet_name(counts: TundishMonthCounts, segment: str) -> str:
    """Sample sheet name is the Jalali «yyyymm» (e.g. 140506) + the segment."""
    try:
        y, m = (int(x) for x in str(counts.report_date).split("/")[:2])
        ym = f"{y}{m:02d}"
    except Exception:  # noqa: BLE001
        ym = "اقلام بحرانی"
    return f"{ym} {SEGMENT_FILE_SUFFIX[segment]}"


_SECTION_RATE_LABELS = (
    ("billet", "بیلت", ("billet_renovation", "billet_patching")),
    ("slab", "اسلب", ("slab_renovation", "slab_patching")),
    ("bloom", "بلوم", ("bloom_renovation", "bloom_patching")),
    ("casting_floor", "سطح ریخته‌گری", ("casting_floor",)),
)


def section_rate_counts(inv: pd.DataFrame | None, segment: str,
                        reno_mode: str = RENO_WITH) -> dict[str, int]:
    """{billet|slab|bloom|casting_floor: number of 4-digit codes with a rate > 0} in a
    segment, after the same preparation as the report (1800/priority 0 dropped,
    section attribution). Only the rate columns active in ``reno_mode`` count."""
    from analytics.critical_items import active_rate_cols, prepare_code_rates

    out = {k: 0 for k, _l, _c in _SECTION_RATE_LABELS}
    try:
        prep = prepare_code_rates(inv, segment=segment)
    except Exception:  # noqa: BLE001
        prep = None
    if not prep:
        return out
    active = set(active_rate_cols(reno_mode))
    for info in prep[0].values():
        rates = info.get("rates") or {}
        for key, _lab, cols in _SECTION_RATE_LABELS:
            if any(c in active and float(rates.get(c) or 0) > 0 for c in cols):
                out[key] += 1
    return out


def section_rates_line(inv: pd.DataFrame | None, segment: str, counts: TundishMonthCounts | None,
                       reno_mode: str = RENO_WITH) -> str:
    """«اقلام نرخ‌دار (پیمانکار): اسلب ۱۲ قلم، بیلت ۰ قلم ⚠️ …» — makes a section with
    no rates (rates import not applied) or with 0 tundish visible in the header."""
    from analytics.critical_items import filter_inventory_segment

    try:
        seg_df = filter_inventory_segment(inv, segment)
    except Exception:  # noqa: BLE001
        seg_df = None
    if seg_df is None or seg_df.empty:
        return (f"⚠️ در منبع اصلی هیچ قلمی با تأمین‌کنندهٔ «{SEGMENT_LABEL_FA[segment]}» "
                "ثبت نشده (ستون شرکت/پیمانکار را بررسی کنید).")
    rc = section_rate_counts(inv, segment, reno_mode)
    tc = {"billet": getattr(counts, "count_billet", 0), "slab": getattr(counts, "count_slab", 0),
          "bloom": getattr(counts, "count_bloom", 0)}
    parts = []
    for key, lab, _c in _SECTION_RATE_LABELS:
        n = rc[key]
        txt = f"{lab} {n} قلم"
        if key in tc:
            by_rule = (segment, key) in {(SEGMENT_CONTRACTOR, "billet"), (SEGMENT_COMPANY, "bloom")}
            if n == 0 and tc[key] and not by_rule:
                txt += " ⚠️بدون نرخ"
            elif n and not tc[key]:
                txt += " (تعداد تاندیش ۰ → بدون نیاز)"
        parts.append(txt)
    return f"اقلام دارای نرخ مصرف ({SEGMENT_LABEL_FA[segment]}): " + "، ".join(parts) + "."


def template_sheet(
    counts: TundishMonthCounts,
    segment: str,
    df: pd.DataFrame | None,
    reno_mode: str,
    audit: dict[str, list[str]] | None = None,
    user_notes: str | None = None,
    rates_line: str | None = None,
) -> dict[str, Any]:
    sec = _segment_table_section(segment, df if df is not None else pd.DataFrame(), reno_mode)
    fixed = fixed_notes(counts) + ([rates_line] if rates_line else [])
    return {
        "segment": segment,
        "name": template_sheet_name(counts, segment),
        "title": report_title(counts, segment, reno_mode),
        "columns": sec["columns"],
        "rows": sec["rows"],
        "bold_rows": sec["bold_rows"],
        "empty_message": sec["empty_message"],
        "fixed_notes": fixed,
        "notes": compose_notes(fixed, user_notes),
    }


def _json_safe(v: Any) -> Any:
    if hasattr(v, "item"):
        try:
            v = v.item()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(v, float) and v != v:  # NaN
        return None
    return v


def sheets_payload(sheets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """JSON-safe copy of template sheets (stored with a draft / approved report)."""
    out = []
    for sh in sheets:
        d = {k: v for k, v in sh.items() if k != "notes"}
        d["rows"] = [{k: _json_safe(v) for k, v in r.items()} for r in sh.get("rows") or []]
        d["bold_rows"] = [int(i) for i in sh.get("bold_rows") or []]
        out.append(d)
    return out


def render_critical_files(
    sheets: list[dict[str, Any]],
    *,
    out_dir: Path,
    prefix: str,
    user_notes: str | None = None,
    letterhead_path: Path | str | None = None,
) -> tuple[Path, dict[str, Path]]:
    """(xlsx, {segment: pdf}) from template sheets + the user's notes (same notes in
    every sheet / PDF). A segment without rows gets a sheet but no PDF."""
    from excel.critical_template import write_critical_items_pdf, write_critical_items_xlsx

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ready = []
    pdfs: dict[str, Path] = {}
    for sh in sheets:
        sheet = dict(sh)
        sheet["notes"] = compose_notes(sh.get("fixed_notes") or [], user_notes)
        ready.append(sheet)
        seg = sh.get("segment")
        if sheet.get("rows") and seg in SEGMENT_FILE_SUFFIX:
            pdf_path = out_dir / f"{prefix}_{SEGMENT_FILE_SUFFIX[seg]}.pdf"
            write_critical_items_pdf(sheet, pdf_path, letterhead_path=letterhead_path)
            pdfs[seg] = pdf_path
    xlsx = write_critical_items_xlsx(ready, out_dir / f"{prefix}.xlsx")
    return xlsx, pdfs


def generate_critical_items_files(
    db: Database,
    user: dict[str, Any],
    *,
    report_date: Any = None,
    output_dir: Path | None = None,
    file_prefix: str | None = None,
    letterhead_path: Path | str | None = None,
    inventory_df: pd.DataFrame | None = None,
    reno_mode: str = RENO_WITH,
    user_notes: str | None = None,
) -> CriticalItemsResult:
    """Build company PDF, contractor PDF (when it has rows) and a 2-sheet xlsx
    (شرکت | پیمانکار; each laid out like the sample with «توضیحات» at the bottom).

    ``reno_mode``: RENO_WITH «با نوسازی» (default) | RENO_WITHOUT «بدون نوسازی».
    ``report_date`` (default today, Jalali): titles / file names carry it; the tundish
    basis = 3 complete months before it (``critical_basis``) — no month selection.
    """
    from analytics.frames import load_primary_inventory

    reno_mode = normalize_reno_mode(reno_mode)
    res = CriticalItemsResult(reno_mode=reno_mode)
    counts = critical_basis(db, report_date)
    if counts is None:
        ry, rm, _rd = report_date_parts(report_date)
        months = "، ".join(
            f"{PERSIAN_MONTH_NAMES[mm]} {yy}" for yy, mm in previous_complete_months(ry, rm)
        )
        res.error = (
            f"برای ۳ ماه کامل گذشته ({months}) نه لاگ توالی تاندیش هست و نه «تعداد تاندیش "
            "ماهانه» (مبنای میانگین مصرف ماهانه)."
        )
        return res
    res.counts = counts
    # load_primary_inventory already applies inventory_ledger once.
    inv = inventory_df if inventory_df is not None else load_primary_inventory(db, user)
    if inv is None or inv.empty:
        res.error = "منبع اصلی یافت نشد."
        return res
    res.frames = build_critical_items_segments(inv, counts, reno_mode)
    res.rates_lines = {seg: section_rates_line(inv, seg, counts, reno_mode) for seg in SEGMENTS}
    from analytics.critical_items import BELOW_CP_LABEL

    for seg in SEGMENTS:
        df_s = res.frames.get(seg)
        if df_s is not None and not df_s.empty and "below_threshold" in df_s.columns:
            n_cp = int(df_s["کد و شرح کالا"].astype(str).str.contains(BELOW_CP_LABEL, regex=False).sum())
            if n_cp:
                res.rates_lines[seg] += (
                    f" {n_cp} قلم «{BELOW_CP_LABEL}» (موجودی ≤ نقطه بحرانی) هم آمده؛ "
                    "درخواست = بیشترِ نیاز و (نقطه بحرانی − موجودی).")
    res.audit = {"missing": [], "mixed": []}
    for seg in SEGMENTS:
        a = origin_audit(inv, seg)
        for k in res.audit:
            res.audit[k] += [c for c in a.get(k, []) if c not in res.audit[k]]
    if all(df is None or df.empty for df in res.frames.values()):
        res.error = (
            "قلمی با کسری (نیاز مثبت) در افق ۳ ماه (داخلی) / ۶ ماه (وارداتی) "
            f"({RENO_LABEL_FA[reno_mode]}) یافت نشد. {horizon_header_note(counts)}"
        )
        res.error += "\n" + "\n".join(res.rates_lines.values())
        from services.rates_import import presence_message_fa

        res.rates_hint = True
        res.rates_note = presence_message_fa(inv)
        res.error += "\n" + res.rates_note
        return res

    ensure_dirs()
    out_dir = Path(output_dir) if output_dir else REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = file_prefix or f"critical_items_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    date_tag = counts.report_date.replace("/", "-")
    if date_tag and date_tag not in prefix:
        prefix = f"{prefix}_{date_tag}"
    prefix = f"{prefix}_{RENO_FILE_SUFFIX[reno_mode]}"
    for seg in SEGMENTS:
        res.titles[seg] = report_title(counts, seg, reno_mode)
        res.sheets.append(template_sheet(counts, seg, res.frames[seg], reno_mode, res.audit,
                                         user_notes=user_notes,
                                         rates_line=res.rates_lines.get(seg)))
    res.xlsx, res.pdfs = render_critical_files(
        res.sheets, out_dir=out_dir, prefix=prefix, user_notes=user_notes,
        letterhead_path=letterhead_path,
    )
    xlsx_title = report_title(counts, None, reno_mode)
    res.titles["xlsx"] = xlsx_title
    return res


BOT_TOP_ROWS = 8


def critical_items_bot_lines(res: CriticalItemsResult, top: int = BOT_TOP_ROWS) -> list[str]:
    """Bot summary text: per segment count + the most urgent rows in the sample's
    columns (کد، شرح، موجودی، نیاز، حد تحمل), plus origin flags."""
    lines: list[str] = []
    for seg, tag in ((SEGMENT_COMPANY, "شرکت (گزارش اصلی)"),
                     (SEGMENT_CONTRACTOR, "پیمانکار (گزارش جداگانه)")):
        df = res.frames.get(seg)
        n = res.row_count(seg)
        lines.append(f"• {tag}: {n} قلم" + ("" if n else " — قلمی با نیاز مثبت نبود (PDF جداگانه ندارد)"))
        if res.rates_lines.get(seg):
            lines.append(f"  {res.rates_lines[seg]}")
        if df is None or df.empty:
            continue
        shown = 0
        for _, r in df.iterrows():
            if str(r.get("ردیف", "")).strip() == "":
                continue  # member rows of a shared-need group
            if shown >= top:
                break
            shown += 1
            lines.append(
                f"  {r.get('کد چهاررقمی', '')} {r.get('کد و شرح کالا', '')} — "
                f"موجودی {r.get('موجودی', '')} {unit_fa(r.get('واحد', ''))} | "
                f"{COL_NEED}: {r.get(COL_NEED, '')} | {COL_DAYS}: {r.get(COL_DAYS, '')}"
            )
        if n > shown:
            lines.append(f"  … و {n - shown} قلم دیگر (در PDF/اکسل)")
    audit = res.audit or {}
    if audit.get("missing"):
        lines.append(
            "⚠️ مبدأ نامشخص (داخلی/۳ ماه فرض شد): " + "، ".join(audit["missing"])
        )
    if audit.get("mixed"):
        lines.append("ℹ️ مبدأ مختلط (افق ۶ ماه): " + "، ".join(audit["mixed"]))
    return lines
