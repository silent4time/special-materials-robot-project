"""«📦 گزارش موجودی انبار» — shared bot + web service (v1.5.0).

Base list = منبع اصلی (one row per شناسه مواد; duplicate work-order rows repeat the
same stock → ONE value, see ``services.inbound_report.stock_frame_by_id``).

Modes
- ``today``     «گزارش روز»: the latest stock = current منبع اصلی + inventory ledger
                (the same canonical warehouse quantities every other report uses).
- ``month_end`` «موجودی پایان ماه گذشته»: per ID, the last stock recorded by a
                «📥 موجودی انبار» stock upload (``inventory_stock_uploads`` snapshots)
                up to 23:59 of the last day of the previous Jalali month (Tehran).
                No snapshot up to that moment → a clear Persian message, no file.

Segments (شناسه مواد authoritative): chars 5–8 == «0000» → پیمانکار, else شرکت
(``analytics.critical_items.classify_contractor_or_company``); ``both`` = two sections.

Columns: کد ۴ رقمی | آی‌دی کد | شرح محصول | موجودی (با واحد) | واژه.
Output: ONE format per request (``fmt`` = "pdf" | "xlsx").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jdatetime
import pandas as pd

from analytics.critical_items import (
    SEGMENT_COMPANY,
    SEGMENT_CONTRACTOR,
    classify_contractor_or_company,
)
from bot.jalali import PERSIAN_MONTH_NAMES, TEHRAN
from config import REPORT_DIR
from db.models import Database
from services.inbound_report import STOCK_UPLOAD_KIND, load_snapshot, stock_frame_by_id
from services.units import fmt_num, unit_fa

MODE_TODAY = "today"
MODE_MONTH_END = "month_end"
MODES = (MODE_TODAY, MODE_MONTH_END)
MODE_LABEL_FA = {MODE_TODAY: "گزارش روز", MODE_MONTH_END: "موجودی پایان ماه گذشته"}

SEG_BOTH = "both"
SEGMENT_CHOICES = (SEGMENT_CONTRACTOR, SEGMENT_COMPANY, SEG_BOTH)
SEGMENT_LABEL_FA = {
    SEGMENT_CONTRACTOR: "اقلام پیمانکار",
    SEGMENT_COMPANY: "اقلام شرکت",
    SEG_BOTH: "هر دو",
}

COLUMNS = ["code4", "item_id", "product_name", "stock", "keyword"]
HEADER_FA = {
    "code4": "کد ۴ رقمی",
    "item_id": "آی‌دی کد",
    "product_name": "شرح محصول",
    "stock": "موجودی",
    "keyword": "واژه",
}
FORMATS = ("pdf", "xlsx")
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_digits(s: Any) -> str:
    return str(s).translate(_FA_DIGITS)


@dataclass
class StockReport:
    mode: str
    segment: str
    title: str = ""
    subtitle: str = ""
    sections: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    n_rows: int = 0
    n_unrecorded: int = 0
    basis: str = ""

    def bot_text(self) -> str:
        lines = [self.title, self.basis]
        for s in self.sections:
            lines.append(f"• {s['title']}: {fa_digits(len(s['rows']))} قلم")
        if self.n_unrecorded:
            lines.append(
                f"⚠️ {fa_digits(self.n_unrecorded)} قلم منبع اصلی تا آن تاریخ در هیچ بارگذاری "
                "موجودی انبار ثبت نشده بود و در گزارش نیامده است."
            )
        return "\n".join(x for x in lines if x)


def prev_month_cutoff(now: datetime | None = None) -> tuple[int, int, datetime, str]:
    """(jy, jm of previous month, cutoff in UTC (exclusive = first moment of this
    month, Tehran), Persian label «پایان مهر ۱۴۰۵ (۱۴۰۵/۰۷/۳۰ ساعت ۲۳:۵۹)»)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    jt = jdatetime.date.fromgregorian(date=now.astimezone(TEHRAN).date())
    first = jdatetime.date(jt.year, jt.month, 1)
    start_local = datetime.combine(first.togregorian(), datetime.min.time(), tzinfo=TEHRAN)
    last = jdatetime.date.fromgregorian(date=(first.togregorian() - timedelta(days=1)))
    label = (
        f"پایان {PERSIAN_MONTH_NAMES[last.month]} {fa_digits(last.year)} "
        f"({fa_digits(f'{last.year}/{last.month:02d}/{last.day:02d}')} ساعت ۲۳:۵۹)"
    )
    return last.year, last.month, start_local.astimezone(timezone.utc), label


def _parse_utc(s: Any) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _stock_uploads_before(db: Database, cutoff_utc: datetime) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM inventory_stock_uploads WHERE kind = ? ORDER BY id",
            (STOCK_UPLOAD_KIND,),
        ).fetchall()
    out = []
    for r in rows:
        d = _parse_utc(dict(r).get("created_at"))
        if d is not None and d < cutoff_utc:
            out.append(dict(r))
    return out


def _fmt_local(iso: Any) -> str:
    d = _parse_utc(iso)
    if d is None:
        return ""
    loc = d.astimezone(TEHRAN)
    j = jdatetime.datetime.fromgregorian(datetime=loc.replace(tzinfo=None))
    return fa_digits(j.strftime("%Y/%m/%d %H:%M"))


def _row(rec: dict[str, Any], qty: Any) -> dict[str, Any]:
    q = fmt_num(qty) if qty is not None else ""
    u = unit_fa(rec.get("unit"))
    code = str(rec.get("category_code") or "").strip() or str(rec.get("id") or "")[:4]
    return {
        "code4": code,
        "item_id": rec.get("id") or "",
        "product_name": rec.get("product_name") or "",
        "stock": f"{q} {u}".strip() if q != "" else "—",
        "keyword": rec.get("keyword") or "",
    }


def month_end_unavailable(db: Database, now: datetime | None = None) -> str | None:
    """Clear Persian message when no stock upload exists up to the previous month's end."""
    _jy, _jm, cutoff, label = prev_month_cutoff(now)
    if _stock_uploads_before(db, cutoff):
        return None
    return (
        f"برای {label} هیچ ثبت موجودی انبار (بارگذاری «📥 موجودی انبار») در سامانه "
        "ذخیره نشده است؛ گزارش موجودی پایان ماه گذشته قابل تهیه نیست."
    )


def build(db: Database, user: dict[str, Any] | None, mode: str, segment: str,
          *, now: datetime | None = None) -> StockReport:
    from analytics.frames import load_primary_inventory

    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    if segment not in SEGMENT_CHOICES:
        raise ValueError(f"unknown segment: {segment}")
    rep = StockReport(mode=mode, segment=segment)
    live = load_primary_inventory(db, user or {})
    if live is None or live.empty:
        rep.error = "هنوز فایل منبع اصلی بارگذاری نشده است؛ گزارش موجودی انبار ساخته نمی‌شود."
        return rep
    base = stock_frame_by_id(live)
    qty: dict[str, Any] = {k: q for k, q in zip(base["_key"], base["quantity"])}
    if mode == MODE_TODAY:
        rep.basis = "مبنا: آخرین موجودی منبع اصلی (همراه با ثبت‌های حواله/برگشت به انبار)."
        rep.title = "گزارش موجودی انبار — گزارش روز"
    else:
        _jy, _jm, cutoff, label = prev_month_cutoff(now)
        uploads = _stock_uploads_before(db, cutoff)
        rep.title = f"گزارش موجودی انبار — موجودی {label}"
        if not uploads:
            rep.error = (
                f"برای {label} هیچ ثبت موجودی انبار (بارگذاری «📥 موجودی انبار») در سامانه "
                "ذخیره نشده است؛ گزارش موجودی پایان ماه گذشته قابل تهیه نیست."
            )
            return rep
        qty = {}
        for up in uploads:  # oldest → newest; missing IDs keep their earlier value
            snap = load_snapshot(up)
            if snap is None:
                continue
            s = stock_frame_by_id(snap)
            for k, q in zip(s["_key"], s["quantity"]):
                if q is not None and q == q:
                    qty[k] = q
        rep.basis = (
            f"مبنا: آخرین موجودی ثبت‌شده تا {label}؛ آخرین بارگذاری موجودی انبار پیش از آن: "
            f"{_fmt_local(uploads[-1].get('created_at'))} (به وقت تهران)."
        )
    segs = [segment] if segment != SEG_BOTH else [SEGMENT_CONTRACTOR, SEGMENT_COMPANY]
    rows_by: dict[str, list] = {s: [] for s in segs}
    for _, rec in base.iterrows():
        r = rec.to_dict()
        seg = classify_contractor_or_company(r.get("contractor_or_company"), r.get("id"))
        if seg not in rows_by:
            continue
        k = r["_key"]
        if k not in qty:
            rep.n_unrecorded += 1
            continue
        rows_by[seg].append(_row(r, qty[k]))
    for s in segs:
        rows = sorted(rows_by[s], key=lambda x: (str(x["code4"]), str(x["item_id"])))
        rep.sections.append({
            "title": SEGMENT_LABEL_FA[s],
            "sheet_title": SEGMENT_LABEL_FA[s],
            "columns": list(COLUMNS),
            "rows": rows,
            "empty_message": f"در {SEGMENT_LABEL_FA[s]} قلمی نیست.",
        })
        rep.n_rows += len(rows)
    rep.subtitle = f"{SEGMENT_LABEL_FA[segment]} | {rep.basis}"
    if rep.n_rows == 0:
        rep.error = f"{rep.title}: در «{SEGMENT_LABEL_FA[segment]}» قلمی یافت نشد."
    return rep


def write_file(rep: StockReport, fmt: str, *, letterhead_path: Path | str | None = None,
               output_dir: Path | None = None) -> Path:
    """Build ONE file (PDF or Excel) of a built report."""
    from services.file_names import fa_stem, unique_dir

    if fmt not in FORMATS:
        raise ValueError(f"unknown format: {fmt}")
    seg = SEGMENT_LABEL_FA[rep.segment].replace("اقلام ", "")
    stem = fa_stem(f"موجودی انبار {MODE_LABEL_FA[rep.mode]} {seg}")
    out_dir = output_dir or unique_dir(REPORT_DIR, "warehouse_stock")
    out = Path(out_dir) / f"{stem}.{fmt}"
    if fmt == "pdf":
        from pdf.generator import generate_simple_report_pdf

        return generate_simple_report_pdf(
            rep.title, subtitle=rep.subtitle, sections=rep.sections, output_path=out,
            header_map=HEADER_FA, filename_stem="warehouse_stock",
            letterhead_path=letterhead_path, landscape=False,
        )
    from excel.simple_report import generate_simple_report_xlsx

    return generate_simple_report_xlsx(
        rep.title, subtitle=rep.subtitle, sections=rep.sections, output_path=out,
        header_map=HEADER_FA, filename_stem="warehouse_stock",
    )
