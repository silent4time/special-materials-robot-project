"""«🧮 نیاز مواد برای N تاندیش» — shared by bot and web (read-only report).

Reuses the «اقلام بحرانی» rate pipeline exactly (``analytics.critical_items``):
1800 excluded, section/supplier attribution, priority ≠ 0 per 4-digit code,
merged-cell rates counted once per code, renovation+patching («با نوسازی») or
patching only («بدون نوسازی»), + casting-floor × N, nozzle shared-need groups
(need once + per-code stock + subtotal). Company (شرکت) and contractor
(پیمانکار) rows are computed separately and listed together with a
«تأمین‌کننده» column.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from analytics.critical_items import (
    RENO_LABEL_FA,
    RENO_WITH,
    SEGMENT_LABEL_FA,
    SEGMENTS,
    SHARED_NEED_LABEL,
    TundishMonthCounts,
    _pick_description,
    active_rate_cols,
    group_rates,
    monthly_need_for_rates,
    normalize_reno_mode,
    prepare_code_rates,
)

SECTIONS: dict[str, str] = {"slab": "اسلب", "bloom": "بلوم", "billet": "بیلت"}
SECTION_BY_FA: dict[str, str] = {v: k for k, v in SECTIONS.items()}
MAX_N = 500

COL_ROW = "ردیف"
COL_CODE = "کد دسته"
COL_KEYWORD = "کلید واژه"
COL_UNIT = "واحد"
COL_SUPPLIER = "تأمین‌کننده"
COL_RATE = "نرخ به ازای هر تاندیش"
COL_NEED = "نیاز کل برای N تاندیش"
COL_STOCK = "موجودی فعلی"
COL_SHORT = "کسری"
COLUMNS = [COL_ROW, COL_CODE, COL_KEYWORD, COL_UNIT, COL_SUPPLIER, COL_RATE, COL_NEED, COL_STOCK, COL_SHORT]

_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def to_ascii_digits(text: str | None) -> str:
    return (text or "").translate(_FA_DIGITS)


def parse_count(text: str | None) -> int | None:
    """Positive integer N (Persian/Arabic digits ok). None if invalid."""
    s = to_ascii_digits(text).strip()
    if not re.fullmatch(r"\d{1,4}", s):
        return None
    n = int(s)
    return n if 1 <= n <= MAX_N else None


def parse_shortcut(text: str | None) -> tuple[str, int] | None:
    """«اسلب ۴» / «۴ اسلب» → ("slab", 4)."""
    s = to_ascii_digits(text).strip()
    m = re.fullmatch(r"(اسلب|بلوم|بیلت)\s*(\d{1,4})", s) or re.fullmatch(r"(\d{1,4})\s*(اسلب|بلوم|بیلت)", s)
    if not m:
        return None
    a, b = m.group(1), m.group(2)
    sec_fa, num = (a, b) if not a.isdigit() else (b, a)
    n = parse_count(num)
    if n is None:
        return None
    return SECTION_BY_FA[sec_fa], n


def need_column_label(n: int) -> str:
    return f"نیاز کل برای {n} تاندیش"


@dataclass
class NTundishResult:
    section: str
    n: int
    reno_mode: str
    report_date: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    bold_rows: list[int] = field(default_factory=list)
    highlight_rows: list[int] = field(default_factory=list)
    error: str | None = None
    pdf: Path | None = None
    xlsx: Path | None = None

    @property
    def title(self) -> str:
        return f"نیاز مواد برای {self.n} تاندیش {SECTIONS[self.section]} — {self.report_date}"

    @property
    def basis_line(self) -> str:
        mode = RENO_LABEL_FA[self.reno_mode]
        per = "نوسازی + پچینگ" if self.reno_mode == RENO_WITH else "فقط پچینگ"
        return (
            f"مبنا: نرخ مصرف به ازای هر تاندیش از منبع اصلی ({mode}: {per}) + سطح ریخته‌گری × {self.n}؛ "
            "همان قواعد اقلام بحرانی (اولویت ۰ و کد ۱۸۰۰ حذف، نرخ سلول ادغام‌شده یک‌بار برای هر کد، "
            "گروه نازل‌ها با نیاز مشترک). کسری = نیاز − موجودی فعلی."
        )

    @property
    def subtitle(self) -> str:
        return (
            f"بخش: {SECTIONS[self.section]} | تعداد تاندیش: {self.n} | حالت: {RENO_LABEL_FA[self.reno_mode]} | "
            f"تاریخ: {self.report_date} | {self.basis_line}"
        )

    def item_count(self) -> int:
        return sum(1 for r in self.rows if r.get(COL_ROW) not in ("", None))

    def shortage_count(self) -> int:
        return len([i for i in self.highlight_rows if self.rows[i].get(COL_ROW) not in ("", None)])

    def sections(self) -> list[dict[str, Any]]:
        need_col = need_column_label(self.n)
        cols = [need_col if c == COL_NEED else c for c in COLUMNS]
        rows = [{(need_col if k == COL_NEED else k): v for k, v in r.items()} for r in self.rows]
        return [
            {
                "title": f"اقلام با نیاز مثبت — {SECTIONS[self.section]} ({RENO_LABEL_FA[self.reno_mode]})",
                "sheet_title": "نیاز N تاندیش",
                "columns": cols,
                "rows": rows,
                "bold_rows": list(self.bold_rows),
                "highlight_rows": list(self.highlight_rows),
                "empty_message": "قلمی با نیاز مثبت یافت نشد.",
                "header_bg": "#1f4e79",
            }
        ]

    def bot_text(self, top: int = 10) -> str:
        lines = [f"🧮 {self.title}", self.subtitle, ""]
        lines.append(f"{self.item_count()} قلم با نیاز مثبت؛ {self.shortage_count()} قلم کسری دارد.")
        shown = 0
        for i in self.highlight_rows:
            r = self.rows[i]
            if r.get(COL_ROW) in ("", None):
                continue
            lines.append(
                f"• {r[COL_CODE]} {r[COL_KEYWORD]} ({r[COL_SUPPLIER]}): نیاز {r[COL_NEED]} | "
                f"موجودی {r[COL_STOCK]} | کسری {r[COL_SHORT]} {r[COL_UNIT]}"
            )
            shown += 1
            if shown >= top:
                break
        return "\n".join(lines)


def _num(v: float, nd: int = 2) -> int | float:
    v = round(float(v), nd)
    return int(v) if abs(v - round(v)) < 1e-9 else v


def _ceil(v: float) -> int:
    return int(math.ceil(float(v) - 1e-6)) if v > 0 else 0


def build_rows(
    inventory_df: pd.DataFrame | None,
    section: str,
    n: int,
    reno_mode: str = RENO_WITH,
    *,
    report_date: str = "",
    shared_mode: str | None = None,
) -> NTundishResult:
    reno_mode = normalize_reno_mode(reno_mode)
    res = NTundishResult(section=section, n=int(n), reno_mode=reno_mode, report_date=report_date)
    if section not in SECTIONS:
        res.error = "بخش نامعتبر است (اسلب / بلوم / بیلت)."
        return res
    if not n or int(n) < 1:
        res.error = "تعداد تاندیش باید عدد صحیح مثبت باشد."
        return res
    if inventory_df is None or inventory_df.empty:
        res.error = "منبع اصلی یافت نشد."
        return res
    counts = TundishMonthCounts(
        jalali_year=0,
        jalali_month=1,
        count_billet=float(n) if section == "billet" else 0.0,
        count_bloom=float(n) if section == "bloom" else 0.0,
        count_slab=float(n) if section == "slab" else 0.0,
        report_date=report_date,
    )
    use_cols = active_rate_cols(reno_mode)

    def _need(rates: dict[str, float]) -> float:
        if sum(rates.values()) <= 0:
            return 0.0
        return float(monthly_need_for_rates(counts=counts, reno_mode=reno_mode, **rates))

    blocks: list[tuple[str, list[dict[str, Any]], bool]] = []
    for seg in SEGMENTS:
        prepared = prepare_code_rates(inventory_df, segment=seg, shared_mode=shared_mode)
        if prepared is None:
            continue
        per_code, groups, grouped = prepared
        sup = SEGMENT_LABEL_FA[seg]
        for code, info in per_code.items():
            if code in grouped:
                continue
            need = _need(info["rates"])
            if need <= 1e-9:
                continue
            desc, unit = _pick_description(info["group"], use_cols)
            stock = float(info["stock"])
            blocks.append(
                (
                    seg + code,
                    [
                        {
                            COL_ROW: 0,
                            COL_CODE: code,
                            COL_KEYWORD: desc,
                            COL_UNIT: unit,
                            COL_SUPPLIER: sup,
                            COL_RATE: _num(need / n),
                            COL_NEED: _num(need),
                            COL_STOCK: _num(stock),
                            COL_SHORT: _ceil(need - stock),
                        }
                    ],
                    False,
                )
            )
        for g in groups:
            members = list(g["codes"])
            need = _need(group_rates(per_code, g))
            if need <= 1e-9:
                continue
            stock = float(sum(per_code[k]["stock"] for k in members))
            descs, unit = [], ""
            for k in members:
                d, u = _pick_description(per_code[k]["group"], use_cols)
                descs.append(d)
                unit = unit or u
            head = {
                COL_ROW: 0,
                COL_CODE: "/".join(members),
                COL_KEYWORD: "جمع گروه: " + "، ".join(dict.fromkeys(descs)),
                COL_UNIT: unit,
                COL_SUPPLIER: sup,
                COL_RATE: _num(need / n),
                COL_NEED: _num(need),
                COL_STOCK: _num(stock),
                COL_SHORT: _ceil(need - stock),
            }
            members_rows = [
                {
                    COL_ROW: "",
                    COL_CODE: k,
                    COL_KEYWORD: d,
                    COL_UNIT: unit,
                    COL_SUPPLIER: sup,
                    COL_RATE: "—",
                    COL_NEED: SHARED_NEED_LABEL,
                    COL_STOCK: _num(per_code[k]["stock"]),
                    COL_SHORT: "—",
                }
                for k, d in zip(members, descs)
            ]
            # members first, subtotal (shared need once) last
            blocks.append((seg + members[0], members_rows + [head], True))

    blocks.sort(key=lambda b: (0 if b[0].startswith(SEGMENTS[0]) else 1, b[0]))
    i = 0
    for _key, block, is_group in blocks:
        for row in block:
            if row[COL_ROW] != "":
                i += 1
                row[COL_ROW] = i
            idx = len(res.rows)
            res.rows.append(row)
            if is_group and row[COL_ROW] != "":
                res.bold_rows.append(idx)
            if isinstance(row[COL_SHORT], (int, float)) and row[COL_SHORT] > 0:
                res.highlight_rows.append(idx)
    if not res.rows:
        res.error = (
            f"برای {n} تاندیش {SECTIONS[section]} ({RENO_LABEL_FA[reno_mode]}) قلمی با نیاز مثبت "
            "در منبع اصلی یافت نشد (نرخ‌های مصرف خالی‌اند)."
        )
    return res


def generate_files(
    db: Any,
    user: dict[str, Any],
    section: str,
    n: int,
    reno_mode: str = RENO_WITH,
    *,
    letterhead_path: Path | str | None = None,
    output_dir: Path | None = None,
    inventory_df: pd.DataFrame | None = None,
) -> NTundishResult:
    """Build PDF (A4 portrait + letterhead) and XLSX. Read-only (no DB writes)."""
    from analytics.frames import load_primary_inventory
    from bot.jalali import format_date
    from config import REPORT_DIR, ensure_dirs
    from excel.simple_report import generate_simple_report_xlsx
    from pdf.generator import generate_simple_report_pdf

    inv = inventory_df if inventory_df is not None else load_primary_inventory(db, user)
    today = format_date(datetime.now().date())
    res = build_rows(inv, section, n, reno_mode, report_date=today)
    if res.error:
        return res
    ensure_dirs()
    from services.file_names import fa_digits, fa_stem, unique_dir

    out = unique_dir(Path(output_dir) if output_dir else REPORT_DIR, "n_tundish")
    # «نیاز_مواد_۲_تاندیش_بلوم_با_نوسازی_1405-07-19»
    stem = fa_stem("نیاز مواد", fa_digits(n), "تاندیش", SECTIONS[section], RENO_LABEL_FA[res.reno_mode])
    res.pdf = generate_simple_report_pdf(
        res.title,
        subtitle=res.subtitle,
        sections=res.sections(),
        output_path=out / f"{stem}.pdf",
        filename_stem="n_tundish",
        letterhead_path=letterhead_path,
    )
    res.xlsx = generate_simple_report_xlsx(
        res.title,
        subtitle=res.subtitle,
        sections=res.sections(),
        output_path=out / f"{stem}.xlsx",
        filename_stem="n_tundish",
    )
    return res
