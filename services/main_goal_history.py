"""«گزارش هدف اصلی» — سابقهٔ چندماهه + دو سناریو (shared bot + web).

هر ماه = یک مجموعهٔ ۴ فایلی (آمار تولید + مصرف تاندیش بیلت/بلوم/اسلب) با بازهٔ
یکسان؛ اعتبارسنجی بازه همان ``main_goal_report.compute_main_goal`` است. ماه‌ها
جداگانه در جدول ``main_goal_months`` ذخیره می‌شوند (آپلود دوباره = جایگزینی).

نرخ‌های تاریخی (pooled روی همهٔ ماه‌های ذخیره‌شده):
  • تن بر تاندیش = Σ تناژ ÷ Σ تعداد تاندیش   (هر بخش)
  • ذوب بر تاندیش = Σ ذوب ÷ Σ تاندیش
  • نرخ ماده بر تن = Σ مصرف ÷ Σ تناژ همان بخش
  • نرخ ماده بر تاندیش = Σ مصرف ÷ Σ تاندیش

سناریو ۱ — تناژ هدف (بازه + بخش): تاندیش لازم ≈ تناژ ÷ (تن/تاندیش)، مواد ≈ نرخ بر تن × تناژ
سناریو ۲ — پیش‌بینی N ماه آینده: روند خطی تناژ ماهانهٔ هر بخش (≥۳ ماه)،
            وگرنه میانگین؛ مواد ≈ نرخ بر تن × تناژ پیش‌بینی‌شده
"""
from __future__ import annotations

import logging

import json
import math
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from bot.jalali import PERSIAN_MONTH_NAMES, format_date, format_month_year, jalali_today, tehran_now
from config import ADMIN_ROLES, UPLOAD_DIR
from services import main_goal_report as mg

MIN_RECOMMENDED_MONTHS = 3
SECTIONS: tuple[str, ...] = ("billet", "bloom", "slab")
SECTION_TOTAL = "total"
SECTION_CHOICES_FA: dict[str, str] = {
    "billet": "بیلت",
    "bloom": "بلوم",
    "slab": "اسلب",
    SECTION_TOTAL: "کل (همه بخش‌ها)",
}
MAX_FORECAST_MONTHS = 24
HISTORY_DIR_NAME = "main_goal_history"


def can_delete_month(user: dict | None) -> bool:
    return bool(user and user.get("active") and user.get("role") in ADMIN_ROLES)


# ---------------------------------------------------------------- serialization
def _sort_key(period: mg.PeriodKey) -> str:
    if period.kind == "month" and period.year and period.month:
        return f"{period.year:04d}-{period.month:02d}"
    if period.kind == "range" and period.start:
        parts = re.findall(r"\d+", mg.normalize_digits(period.start))
        if len(parts) >= 3:
            return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    return "9999-" + mg.normalize_text(period.raw)


def _stats_json(result: mg.MainGoalResult) -> str:
    return json.dumps(
        {
            "period": result.period.__dict__ if result.period else None,
            "period_sources": result.period_sources,
            "production": result.production.as_dict() if result.production else None,
            "consumptions": {k: v.as_dict() for k, v in result.consumptions.items()},
            "warnings": result.warnings,
        },
        ensure_ascii=False,
    )


def _production_from(d: dict | None) -> mg.ProductionStats:
    d = d or {}
    return mg.ProductionStats(
        slab_tons=float(d.get("slab_tons") or 0),
        bloom_tons=float(d.get("bloom_tons") or 0),
        billet_tons=float(d.get("billet_tons") or 0),
        notes=list(d.get("notes") or []),
        missing=list(d.get("missing") or []),
    )


def _consumption_from(section: str, d: dict | None) -> mg.ConsumptionStats:
    d = d or {}
    mats = []
    for m in d.get("materials") or []:
        try:
            mats.append(
                mg.MaterialLine(
                    name=str(m.get("name") or ""),
                    quantity=float(m.get("quantity") or 0),
                    unit=str(m.get("unit") or "kg"),
                    item_id=m.get("item_id"),
                    keyword=m.get("keyword"),
                )
            )
        except (TypeError, ValueError):
            continue
    return mg.ConsumptionStats(
        section=section,
        tundish_count=d.get("tundish_count"),
        melt_count=d.get("melt_count"),
        materials=mats,
        notes=list(d.get("notes") or []),
        missing=list(d.get("missing") or []),
    )


@dataclass
class MonthRecord:
    id: int
    period_key: str
    label: str
    kind: str
    year: int | None
    month: int | None
    sort_key: str
    production: mg.ProductionStats
    consumptions: dict[str, mg.ConsumptionStats]
    jalali_date: str = ""
    actor: str = ""
    source: str = ""

    def ordinal(self) -> int | None:
        if self.kind == "month" and self.year and self.month:
            return int(self.year) * 12 + int(self.month) - 1
        return None

    def tons(self, section: str) -> float:
        return float(getattr(self.production, f"{section}_tons", 0) or 0)


def month_record_from_row(row: dict) -> MonthRecord:
    stats = json.loads(row.get("stats_json") or "{}")
    cons = {
        sec: _consumption_from(sec, (stats.get("consumptions") or {}).get(sec))
        for sec in SECTIONS
        if (stats.get("consumptions") or {}).get(sec) is not None
    }
    return MonthRecord(
        id=int(row["id"]),
        period_key=str(row["period_key"]),
        label=str(row.get("period_label") or row["period_key"]),
        kind=str(row.get("period_kind") or "month"),
        year=row.get("year"),
        month=row.get("month"),
        sort_key=str(row.get("sort_key") or ""),
        production=_production_from(stats.get("production")),
        consumptions=cons,
        jalali_date=str(row.get("jalali_date") or ""),
        actor=str(row.get("actor_display_name") or row.get("bale_user_id") or ""),
        source=str(row.get("source") or ""),
    )


def load_history(db: Any) -> list[MonthRecord]:
    """Prefer normalized production/consumption tables; legacy months as fallback."""
    try:
        from services import main_goal_persist as mgp
        return mgp.load_history_from_db(db, include_incomplete=True)
    except Exception:  # noqa: BLE001
        return [month_record_from_row(r) for r in db.list_main_goal_months()]


# ---------------------------------------------------------------- store a month set
@dataclass
class StoreOutcome:
    ok: bool
    error_fa: str | None
    result: mg.MainGoalResult | None
    row: dict | None = None
    replaced: bool = False

    def text_fa(self, history_count: int | None = None) -> str:
        if not self.ok or not self.result or not self.result.period:
            return self.error_fa or "خطا"
        r = self.result
        p = r.production
        verb = "جایگزین شد (آپلود قبلی همین ماه به‌روز شد)" if self.replaced else "ذخیره شد"
        lines = [
            f"✅ مجموعهٔ ۴ فایلی «{r.period.label_fa()}» {verb}.",
            f"تناژ: بیلت {p.billet_tons:,.0f} | بلوم {p.bloom_tons:,.0f} | اسلب {p.slab_tons:,.0f} | جمع {p.total_tons:,.0f} تن",
        ]
        for sec in SECTIONS:
            c = r.consumptions.get(sec)
            if not c:
                continue
            tc = f"{c.tundish_count:g}" if c.tundish_count is not None else "—"
            lines.append(f"{mg.SECTION_LABEL_FA[sec]}: تاندیش {tc}، اقلام مصرفی {len(c.materials)}")
        if r.warnings:
            lines.append("⚠ " + "؛ ".join(r.warnings[:4]))
        if history_count is not None:
            lines.append("")
            lines.append(history_count_line(history_count))
        return "\n".join(lines)



def consecutive_month_gap_warning(months: list[MonthRecord]) -> str | None:
    """Warn only when Jalali months in DB have a hole (e.g. Tir+Shahrivar without Mordad)."""
    dated = sorted(
        {(int(m.year), int(m.month)) for m in months if m.kind == "month" and m.year and m.month}
    )
    if len(dated) < 2:
        return None
    missing = []
    for (y1, m1), (y2, m2) in zip(dated, dated[1:]):
        cy, cm = y1, m1
        while True:
            cm += 1
            if cm > 12:
                cm = 1
                cy += 1
            if (cy, cm) >= (y2, m2):
                break
            missing.append(format_month_year(cy, cm, named=True))
    if not missing:
        return None
    return "وقفه در ماه‌های متوالی سابقه: " + "، ".join(missing) + " — لطفاً ماه‌های جاافتاده را ثبت کنید."


def history_count_line(n: int, *, gap_warning: str | None = None) -> str:
    """Status line. Does NOT alarm merely because n < 3; gap_warning is separate."""
    base = f"📚 ماه‌های ذخیره‌شده: {n}."
    if gap_warning:
        return base + "\n⚠ " + gap_warning
    if n >= 1:
        return base + " سری ماه‌ها پیوسته است (هشدار فقط در صورت وقفه در ماه‌های متوالی)."
    return base + " هنوز ماهی ثبت نشده."


def _safe_dir_name(period_key: str) -> str:
    return re.sub(r"[^0-9A-Za-z_\-]+", "_", period_key).strip("_") or "period"


def store_month_set(
    db: Any,
    files: dict[str, Path | str],
    *,
    filenames: dict[str, str] | None,
    user: dict,
    source: str,
    inventory: pd.DataFrame | None = None,
) -> StoreOutcome:
    """Validate the 4 files (same period) → persist files + parsed stats as one month."""
    result = mg.compute_main_goal(
        {k: Path(v) for k, v in files.items()},
        filenames=filenames or {},
        inventory=inventory,
    )
    if not result.ok or not result.period:
        return StoreOutcome(ok=False, error_fa=result.error_fa, result=result)

    period = result.period
    dest_dir = UPLOAD_DIR / HISTORY_DIR_NAME / _safe_dir_name(period.key())
    dest_dir.mkdir(parents=True, exist_ok=True)
    file_meta: dict[str, Any] = {}
    for kind in mg.FILE_KIND_ORDER:
        src = Path(files[kind])
        dest = dest_dir / f"{kind}.xlsx"
        try:
            if src.resolve() != dest.resolve():
                shutil.copy2(src, dest)
        except OSError:
            dest = src
        file_meta[kind] = {
            "path": str(dest),
            "filename": (filenames or {}).get(kind) or src.name,
            "period_source": result.period_sources.get(kind),
        }
    now = tehran_now()
    row, replaced = db.upsert_main_goal_month(
        period_key=period.key(),
        period_label=period.label_fa(),
        period_kind=period.kind,
        year=period.year,
        month=period.month,
        sort_key=_sort_key(period),
        files_json=json.dumps(file_meta, ensure_ascii=False),
        stats_json=_stats_json(result),
        summary_text=result.summary_text(),
        source=source,
        bale_user_id=user["bale_user_id"],
        actor_display_name=user.get("display_name"),
        created_at_tehran=now.isoformat(),
        jalali_date=format_date(now),
    )
    # Also persist normalized production + consumption rows for DB-backed reports
    # (was a call to an undefined helper whose NameError was swallowed → web 4-file
    # uploads never reached the normalized tables used by every DB-backed report)
    _persist_normalized_from_files(db, file_meta, user=user, source=source, inventory=inventory)
    return StoreOutcome(ok=True, error_fa=None, result=result, row=row, replaced=replaced)


def _persist_normalized_from_files(
    db: Any, file_meta: dict[str, Any], *, user: dict, source: str, inventory: pd.DataFrame | None
) -> None:
    """Store a validated 4-file month also in main_goal_production / _consumption."""
    from services import main_goal_persist as mgp

    for kind, meta in file_meta.items():
        path, fname = Path(meta["path"]), meta.get("filename") or ""
        try:
            if kind == "production":
                mgp.store_production_from_excel(db, path, user=user, source=source, filename=fname)
            elif kind.endswith("_consumption"):
                mgp.store_consumption_from_excel(
                    db, path, kind.split("_", 1)[0], user=user, source=source,
                    filename=fname, inventory=inventory,
                )
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("normalized main-goal store failed (%s)", kind)


# ---------------------------------------------------------------- historical model
@dataclass
class SectionModel:
    section: str
    months: int = 0
    tons_series: list[float] = field(default_factory=list)
    tundish_series: list[float | None] = field(default_factory=list)
    melt_series: list[float | None] = field(default_factory=list)
    x_series: list[float] = field(default_factory=list)  # month ordinal (gaps respected)
    total_tons: float = 0.0
    tons_with_tundish: float = 0.0
    total_tundish: float = 0.0
    total_melts: float = 0.0
    tundish_with_melts: float = 0.0

    @property
    def tons_per_tundish(self) -> float | None:
        if self.total_tundish > 0 and self.tons_with_tundish > 0:
            return self.tons_with_tundish / self.total_tundish
        return None

    @property
    def heats_per_tundish(self) -> float | None:
        if self.tundish_with_melts > 0 and self.total_melts > 0:
            return self.total_melts / self.tundish_with_melts
        return None

    @property
    def avg_monthly_tons(self) -> float:
        return (sum(self.tons_series) / len(self.tons_series)) if self.tons_series else 0.0

    @property
    def avg_monthly_tundish(self) -> float | None:
        vals = [v for v in self.tundish_series if v is not None]
        return (sum(vals) / len(vals)) if vals else None

    def trend(self) -> tuple[float, float, str]:
        """(intercept, slope per month, method) for monthly tonnage."""
        ys = self.tons_series
        xs = self.x_series
        n = len(ys)
        if n == 0:
            return 0.0, 0.0, "بدون داده"
        if n < MIN_RECOMMENDED_MONTHS:
            return self.avg_monthly_tons, 0.0, "میانگین (کمتر از ۳ ماه)"
        mx = sum(xs) / n
        my = sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx <= 0:
            return my, 0.0, "میانگین"
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        intercept = my - slope * mx
        return intercept, slope, "روند خطی"

    def forecast_tons(self, x: float) -> float:
        intercept, slope, _ = self.trend()
        val = intercept + slope * x if slope else intercept
        hi = max(self.tons_series) * 1.5 if self.tons_series else 0.0
        return max(0.0, min(val, hi) if hi > 0 else val)


@dataclass
class MaterialModel:
    section: str
    name: str
    unit: str
    item_id: str | None
    total_qty: float = 0.0
    qty_for_tons: float = 0.0
    tons_basis: float = 0.0
    qty_for_tundish: float = 0.0
    tundish_basis: float = 0.0
    months_present: int = 0
    monthly_qty: list[float] = field(default_factory=list)
    matched_source: str | None = None
    stock: float | None = None

    @property
    def per_ton(self) -> float | None:
        return (self.qty_for_tons / self.tons_basis) if self.tons_basis > 0 else None

    @property
    def per_tundish(self) -> float | None:
        return (self.qty_for_tundish / self.tundish_basis) if self.tundish_basis > 0 else None


@dataclass
class HistoryModel:
    months: list[MonthRecord]
    sections: dict[str, SectionModel]
    materials: dict[str, list[MaterialModel]]

    @property
    def n_months(self) -> int:
        return len(self.months)

    def warnings(self) -> list[str]:
        out: list[str] = []
        gap = consecutive_month_gap_warning(self.months)
        if gap:
            out.append(gap)
        for sec in SECTIONS:
            sm = self.sections[sec]
            if sm.tons_per_tundish is None and sm.total_tons > 0:
                out.append(f"{mg.SECTION_LABEL_FA[sec]}: تعداد تاندیش در سابقه نیست — تاندیش لازم محاسبه نمی‌شود.")
        return out

    def section_share(self) -> dict[str, float]:
        total = sum(self.sections[s].total_tons for s in SECTIONS)
        if total <= 0:
            return {s: 1.0 / len(SECTIONS) for s in SECTIONS}
        return {s: self.sections[s].total_tons / total for s in SECTIONS}

    def last_month_ym(self) -> tuple[int, int] | None:
        for m in reversed(self.months):
            if m.kind == "month" and m.year and m.month:
                return int(m.year), int(m.month)
        return None


def build_history_model(months: list[MonthRecord], inventory: pd.DataFrame | None = None) -> HistoryModel:
    sections = {sec: SectionModel(section=sec) for sec in SECTIONS}
    mats: dict[str, dict[str, MaterialModel]] = {sec: {} for sec in SECTIONS}
    # pass 1: material catalogue per section (first-seen name/unit wins)
    for m in months:
        for sec in SECTIONS:
            cons = m.consumptions.get(sec)
            for line in (cons.materials if cons else []):
                key = mg.fold_key(line.name)
                if key and key not in mats[sec]:
                    mats[sec][key] = MaterialModel(section=sec, name=line.name, unit=line.unit, item_id=line.item_id)
    # pass 2: per-month accumulation (absent material in a parsed month = 0 consumption)
    for idx, m in enumerate(months):
        x = float(m.ordinal() if m.ordinal() is not None else idx)
        for sec in SECTIONS:
            sm = sections[sec]
            tons = m.tons(sec)
            cons = m.consumptions.get(sec)
            tc = float(cons.tundish_count) if cons and cons.tundish_count else None
            mc = float(cons.melt_count) if cons and cons.melt_count else None
            sm.months += 1
            sm.tons_series.append(tons)
            sm.tundish_series.append(tc)
            sm.melt_series.append(mc)
            sm.x_series.append(x)
            sm.total_tons += tons
            if tc and tc > 0:
                sm.total_tundish += tc
                sm.tons_with_tundish += tons
                if mc and mc > 0:
                    sm.total_melts += mc
                    sm.tundish_with_melts += tc
            if not cons or not cons.materials:
                continue  # file had no material rows → month excluded from material rates
            qty_by_key: dict[str, float] = {}
            for line in cons.materials:
                key = mg.fold_key(line.name)
                if key:
                    qty_by_key[key] = qty_by_key.get(key, 0.0) + float(line.quantity)
            for key, mm in mats[sec].items():
                q = qty_by_key.get(key, 0.0)
                mm.monthly_qty.append(q)
                mm.total_qty += q
                if q:
                    mm.months_present += 1
                if tons > 0:
                    mm.qty_for_tons += q
                    mm.tons_basis += tons
                if tc and tc > 0:
                    mm.qty_for_tundish += q
                    mm.tundish_basis += tc
    out_mats: dict[str, list[MaterialModel]] = {}
    for sec in SECTIONS:
        lst = sorted(mats[sec].values(), key=lambda mm: -mm.total_qty)
        for mm in lst:
            try:
                matched, iid, _kw, stock = mg.match_inventory_stock(
                    mg.MaterialLine(name=mm.name, quantity=mm.total_qty, unit=mm.unit, item_id=mm.item_id),
                    inventory,
                    section=sec,
                )
                mm.matched_source, mm.item_id, mm.stock = matched, iid, stock
            except Exception:  # noqa: BLE001
                pass
        out_mats[sec] = lst
    return HistoryModel(months=months, sections=sections, materials=out_mats)


# ---------------------------------------------------------------- input parsing
_FA_NUM_WORDS = {
    "یک": 1, "یه": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6, "شیش": 6,
    "هفت": 7, "هشت": 8, "نه": 9, "ده": 10, "یازده": 11, "دوازده": 12,
}


def parse_number(text: Any) -> float | None:
    raw = mg.normalize_digits(text).replace(",", "").replace("٬", "").replace("،", "").strip()
    raw = re.sub(r"\s*(تن|ton|tons|t)\s*$", "", raw, flags=re.I)
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def parse_horizon_months(text: Any) -> float | None:
    """«۶ ماه» → 6، «۱ سال» → 12، «۲ هفته» → 0.46، «مهر ۱۴۰۵» → 1، «از مهر تا آذر ۱۴۰۵» → 3."""
    s = mg.normalize_text(text)
    if not s:
        return None
    for word, val in _FA_NUM_WORDS.items():
        s = re.sub(rf"(?<!\S){word}(?=\s*(ماه|سال|هفته|روز|فصل))", str(val), s)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ماه|month)", s, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(سال|year)", s, re.I)
    if m:
        return float(m.group(1)) * 12
    m = re.search(r"(\d+(?:\.\d+)?)\s*(هفته|week)", s, re.I)
    if m:
        return round(float(m.group(1)) * 7 / 30.4, 3)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(روز|day)", s, re.I)
    if m:
        return round(float(m.group(1)) / 30.4, 3)
    if "فصل" in s or "سه ماهه" in s:
        return 3.0
    if "سال" in s:
        return 12.0
    from bot.jalali import parse_month_year_range, parse_month_year_token

    rng = parse_month_year_range(s)
    if rng:
        (y1, m1), (y2, m2) = rng
        n = (y2 * 12 + m2) - (y1 * 12 + m1) + 1
        if n > 0:
            return float(n)
    if parse_month_year_token(s) or any(name in s for name in PERSIAN_MONTH_NAMES.values()):
        return 1.0
    v = parse_number(s)
    if v is not None and 0 < v <= 120:
        return v
    return None


_SECTION_WORDS = {
    "بیلت": "billet", "billet": "billet",
    "بلوم": "bloom", "bloom": "bloom",
    "اسلب": "slab", "slab": "slab",
    "کل": SECTION_TOTAL, "جمع": SECTION_TOTAL, "total": SECTION_TOTAL, "همه": SECTION_TOTAL,
}


def section_from_text(text: Any) -> str | None:
    s = mg.normalize_text(text).casefold()
    for word, sec in _SECTION_WORDS.items():
        if word in s:
            return sec
    return None


def parse_targets_text(text: Any) -> dict[str, float]:
    """«بیلت ۲۰۰۰۰ بلوم ۱۰۰۰۰ اسلب ۳۰۰۰۰» / «کل 60000» → {section: tons}."""
    s = mg.normalize_digits(mg.normalize_text(text)).replace(",", "").replace("٬", "")
    out: dict[str, float] = {}
    pattern = r"(بیلت|billet|بلوم|bloom|اسلب|slab|کل|جمع|total|همه)[^\d]{0,12}(\d+(?:\.\d+)?)"
    for m in re.finditer(pattern, s, re.I):
        sec = _SECTION_WORDS.get(m.group(1).casefold())
        if sec:
            out[sec] = float(m.group(2))
    return out


def resolve_targets(model: HistoryModel, targets: dict[str, float]) -> tuple[dict[str, float], list[str]]:
    """Explicit sections win; «کل» − Σ(explicit) is split across the others by historical share."""
    notes: list[str] = []
    explicit = {s: float(v) for s, v in targets.items() if s in SECTIONS and v and v > 0}
    total = float(targets.get(SECTION_TOTAL) or 0)
    out = dict(explicit)
    if total > 0:
        rest_secs = [s for s in SECTIONS if s not in explicit]
        remainder = total - sum(explicit.values())
        if rest_secs and remainder > 0:
            share = model.section_share()
            denom = sum(share[s] for s in rest_secs) or 1.0
            for s in rest_secs:
                out[s] = remainder * share[s] / denom
            pct = "، ".join(f"{mg.SECTION_LABEL_FA[s]} {share[s] * 100:.0f}٪" for s in rest_secs)
            notes.append(f"تناژ کل ({total:,.0f} تن) به نسبت سهم تاریخی تقسیم شد: {pct}.")
        elif remainder < 0:
            notes.append("مجموع تناژ بخش‌ها از «کل» بیشتر است؛ مقادیر بخش‌ها ملاک قرار گرفت.")
    return out, notes


# ---------------------------------------------------------------- report helpers
def _r(n: float | None, digits: int = 2) -> float | str:
    if n is None:
        return "—"
    if abs(n) >= 100:
        return round(n, 1)
    return round(n, digits)


def history_sections(model: HistoryModel) -> list[dict[str, Any]]:
    rows = []
    for m in model.months:
        row: dict[str, Any] = {"ماه": m.label}
        for sec in SECTIONS:
            cons = m.consumptions.get(sec)
            row[f"تناژ_{mg.SECTION_LABEL_FA[sec]}"] = _r(m.tons(sec), 1)
            row[f"تاندیش_{mg.SECTION_LABEL_FA[sec]}"] = _r(cons.tundish_count, 1) if cons and cons.tundish_count is not None else "—"
        row["جمع_تناژ"] = _r(m.production.total_tons, 1)
        rows.append(row)
    cols = ["ماه"]
    for sec in SECTIONS:
        cols += [f"تناژ_{mg.SECTION_LABEL_FA[sec]}", f"تاندیش_{mg.SECTION_LABEL_FA[sec]}"]
    cols.append("جمع_تناژ")

    sec_rows = []
    for sec in SECTIONS:
        sm = model.sections[sec]
        _i, slope, method = sm.trend()
        sec_rows.append(
            {
                "بخش": mg.SECTION_LABEL_FA[sec],
                "ماه‌ها": sm.months,
                "میانگین_تناژ_ماهانه": _r(sm.avg_monthly_tons, 1),
                "میانگین_تاندیش_ماهانه": _r(sm.avg_monthly_tundish, 1),
                "تن_بر_تاندیش": _r(sm.tons_per_tundish),
                "ذوب_بر_تاندیش": _r(sm.heats_per_tundish),
                "روند_تن_در_ماه": _r(slope, 1) if slope else 0,
                "روش": method,
            }
        )
    rate_rows = []
    for sec in SECTIONS:
        for mm in model.materials[sec]:
            rate_rows.append(
                {
                    "بخش": mg.SECTION_LABEL_FA[sec],
                    "ماده": mm.name,
                    "واحد": mm.unit,
                    "مصرف_کل_سابقه": _r(mm.total_qty, 1),
                    "ماه‌های_مصرف": mm.months_present,
                    "نرخ_بر_تن": _r(mm.per_ton, 4),
                    "نرخ_بر_تاندیش": _r(mm.per_tundish, 3),
                    "اتصال_منبع_اصلی": mm.matched_source or "—",
                }
            )
    return [
        {"title": f"سابقهٔ ماهانه ({model.n_months} ماه)", "columns": cols, "rows": rows},
        {
            "title": "شاخص‌های تاریخی هر بخش",
            "columns": ["بخش", "ماه‌ها", "میانگین_تناژ_ماهانه", "میانگین_تاندیش_ماهانه", "تن_بر_تاندیش", "ذوب_بر_تاندیش", "روند_تن_در_ماه", "روش"],
            "rows": sec_rows,
        },
        {
            "title": "نرخ مصرف مواد (میانگین وزنی همهٔ ماه‌ها)",
            "columns": ["بخش", "ماده", "واحد", "مصرف_کل_سابقه", "ماه‌های_مصرف", "نرخ_بر_تن", "نرخ_بر_تاندیش", "اتصال_منبع_اصلی"],
            "rows": rate_rows,
        },
    ]


def _notes_section(lines: list[str]) -> dict[str, Any]:
    return {"title": "قواعد و محدودیت‌ها", "columns": ["یادداشت"], "rows": [{"یادداشت": l} for l in lines]}


def _material_need_rows(model: HistoryModel, tons_by_sec: dict[str, float], tundish_by_sec: dict[str, float | None]) -> list[dict[str, Any]]:
    rows = []
    for sec in SECTIONS:
        tons = tons_by_sec.get(sec) or 0.0
        if tons <= 0:
            continue
        tun = tundish_by_sec.get(sec)
        for mm in model.materials[sec]:
            by_ton = mm.per_ton * tons if mm.per_ton is not None else None
            by_tun = mm.per_tundish * tun if (mm.per_tundish is not None and tun) else None
            need = by_ton if by_ton is not None else by_tun
            shortage = (need - mm.stock) if (need is not None and mm.stock is not None) else None
            rows.append(
                {
                    "بخش": mg.SECTION_LABEL_FA[sec],
                    "ماده": mm.name,
                    "واحد": mm.unit,
                    "نیاز_بر_اساس_تن": _r(by_ton, 1),
                    "نیاز_بر_اساس_تاندیش": _r(by_tun, 1),
                    "نیاز_پیشنهادی": _r(need, 1),
                    "موجودی_منبع_اصلی": _r(mm.stock, 1),
                    "کسری": _r(shortage, 1) if shortage is not None and shortage > 0 else ("0" if shortage is not None else "—"),
                }
            )
    return rows


_NEED_COLS = ["بخش", "ماده", "واحد", "نیاز_بر_اساس_تن", "نیاز_بر_اساس_تاندیش", "نیاز_پیشنهادی", "موجودی_منبع_اصلی", "کسری"]


def _base_notes(model: HistoryModel) -> list[str]:
    labels = "، ".join(m.label for m in model.months) or "—"
    return [
        f"ماه‌های سابقه: {labels}",
        "نگاشت CCM: CCM1 و CCM2 → اسلب، CCM3 → بلوم، CCM4 و CCM5 → بیلت.",
        "تن بر تاندیش = Σ تناژ ÷ Σ تاندیش؛ نرخ بر تن = Σ مصرف ÷ Σ تناژ؛ نرخ بر تاندیش = Σ مصرف ÷ Σ تاندیش (روی همهٔ ماه‌ها).",
        "نیاز پیشنهادی = نرخ بر تن × تناژ (اگر نرخ بر تن نباشد: نرخ بر تاندیش × تاندیش لازم).",
        "کسری = نیاز پیشنهادی − موجودی منبع اصلی (فقط اقلامی که با شناسه/کلیدواژه وصل شده‌اند).",
    ]


# ---------------------------------------------------------------- scenario 1: target tonnage
@dataclass
class ScenarioResult:
    ok: bool
    error_fa: str | None
    kind: str  # "target" | "forecast"
    title: str
    subtitle: str
    sections: list[dict[str, Any]]
    summary: str
    warnings: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    total_tons: float | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "kind": self.kind,
                "params": self.params,
                "summary": self.summary,
                "warnings": self.warnings,
                "sections": self.sections,
                "saved_at": tehran_now().isoformat(),
            },
            ensure_ascii=False,
            default=str,
        )


def _no_history() -> ScenarioResult:
    return ScenarioResult(
        ok=False,
        error_fa=(
            "هنوز هیچ ماهی در سابقهٔ گزارش هدف اصلی ذخیره نشده است.\n"
            f"ابتدا فایل‌های ۴گانهٔ حداقل {MIN_RECOMMENDED_MONTHS} ماه اخیر را آپلود کنید."
        ),
        kind="",
        title="",
        subtitle="",
        sections=[],
        summary="",
    )


def scenario_target(
    model: HistoryModel,
    targets: dict[str, float],
    *,
    period_text: str = "",
) -> ScenarioResult:
    if model.n_months == 0:
        return _no_history()
    tons_by_sec, notes = resolve_targets(model, targets)
    if not tons_by_sec:
        return ScenarioResult(
            ok=False, error_fa="تناژ هدف معتبری وارد نشده است (عدد مثبت برای بیلت/بلوم/اسلب/کل).",
            kind="target", title="", subtitle="", sections=[], summary="",
        )
    horizon = parse_horizon_months(period_text) if period_text else None
    tundish_by_sec: dict[str, float | None] = {}
    melts_by_sec: dict[str, float | None] = {}
    plan_rows = []
    for sec in SECTIONS:
        tons = tons_by_sec.get(sec)
        if not tons:
            continue
        sm = model.sections[sec]
        tpt = sm.tons_per_tundish
        tun = math.ceil(tons / tpt) if tpt else None
        hpt = sm.heats_per_tundish
        melts = (tun * hpt) if (tun and hpt) else None
        tundish_by_sec[sec] = float(tun) if tun else None
        melts_by_sec[sec] = melts
        monthly_target = (tons / horizon) if horizon else None
        cap = (monthly_target / sm.avg_monthly_tons * 100) if (monthly_target and sm.avg_monthly_tons > 0) else None
        plan_rows.append(
            {
                "بخش": mg.SECTION_LABEL_FA[sec],
                "تناژ_هدف": _r(tons, 1),
                "تن_بر_تاندیش_تاریخی": _r(tpt),
                "تاندیش_لازم": tun if tun is not None else "—",
                "ذوب_تقریبی": _r(melts, 0) if melts else "—",
                "هدف_ماهانه": _r(monthly_target, 1),
                "میانگین_ماهانه_سابقه": _r(sm.avg_monthly_tons, 1),
                "نسبت_به_سابقه": f"{cap:.0f}٪" if cap is not None else "—",
            }
        )
    total = sum(tons_by_sec.values())
    tun_total = sum(v for v in tundish_by_sec.values() if v)
    plan_rows.append(
        {
            "بخش": "جمع",
            "تناژ_هدف": _r(total, 1),
            "تن_بر_تاندیش_تاریخی": "—",
            "تاندیش_لازم": int(tun_total) if tun_total else "—",
            "ذوب_تقریبی": _r(sum(v for v in melts_by_sec.values() if v), 0) if any(melts_by_sec.values()) else "—",
            "هدف_ماهانه": _r(total / horizon, 1) if horizon else "—",
            "میانگین_ماهانه_سابقه": _r(sum(model.sections[s].avg_monthly_tons for s in SECTIONS), 1),
            "نسبت_به_سابقه": "—",
        }
    )
    period_label = period_text.strip() or "بازهٔ نامشخص"
    sections = [
        {
            "title": f"سناریو ۱ — تناژ هدف ({period_label})",
            "columns": ["بخش", "تناژ_هدف", "تن_بر_تاندیش_تاریخی", "تاندیش_لازم", "ذوب_تقریبی", "هدف_ماهانه", "میانگین_ماهانه_سابقه", "نسبت_به_سابقه"],
            "rows": plan_rows,
        },
        {
            "title": "مواد مورد نیاز برای تناژ هدف",
            "columns": _NEED_COLS,
            "rows": _material_need_rows(model, tons_by_sec, tundish_by_sec),
        },
        *history_sections(model),
    ]
    warns = model.warnings()
    note_lines = _base_notes(model) + notes
    if horizon:
        note_lines.append(f"طول بازه ≈ {horizon:g} ماه؛ «نسبت به سابقه» = هدف ماهانه ÷ میانگین تناژ ماهانهٔ سابقه.")
    else:
        note_lines.append("طول بازه تشخیص نشد؛ مقایسه با ظرفیت ماهانه انجام نشد (تاندیش و مواد فقط تابع تناژ است).")
    note_lines += warns
    sections.append(_notes_section(note_lines))

    lines = [f"🎯 سناریو ۱ — تناژ هدف ({period_label})", f"سابقه: {model.n_months} ماه"]
    for sec in SECTIONS:
        if sec in tons_by_sec:
            tun = tundish_by_sec.get(sec)
            lines.append(
                f"• {mg.SECTION_LABEL_FA[sec]}: {tons_by_sec[sec]:,.0f} تن → "
                f"تاندیش لازم ≈ {int(tun) if tun else '—'}"
            )
    lines.append(f"جمع: {total:,.0f} تن، تاندیش ≈ {int(tun_total) if tun_total else '—'}")
    n_mats = sum(len(model.materials[s]) for s in tons_by_sec)
    lines.append(f"اقلام مواد برآوردشده: {n_mats}")
    lines += [f"⚠ {w}" for w in warns + notes]
    return ScenarioResult(
        ok=True,
        error_fa=None,
        kind="target",
        title=f"{mg.TITLE_FA} — سناریو تناژ هدف",
        subtitle=f"بازه: {period_label} | سابقه: {model.n_months} ماه",
        sections=sections,
        summary="\n".join(lines),
        warnings=warns,
        params={"targets": targets, "resolved": tons_by_sec, "period_text": period_text, "horizon_months": horizon},
        total_tons=total,
    )


# ---------------------------------------------------------------- scenario 2: N-month forecast
def _future_months(model: HistoryModel, n: int) -> list[tuple[str, float, tuple[int, int] | None]]:
    """Labels + x ordinal for the next ``n`` months after the last stored month."""
    last = model.last_month_ym()
    if last is None:
        t = jalali_today()
        last = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    y, m = last
    base_x = model.sections["billet"].x_series[-1] if model.sections["billet"].x_series else y * 12 + m - 1
    out = []
    for k in range(1, n + 1):
        mm = m + k
        yy = y + (mm - 1) // 12
        mm = (mm - 1) % 12 + 1
        x = (yy * 12 + mm - 1) if model.last_month_ym() else base_x + k
        out.append((format_month_year(yy, mm, named=True), float(x), (yy, mm)))
    return out


def scenario_forecast(model: HistoryModel, months_ahead: int) -> ScenarioResult:
    if model.n_months == 0:
        return _no_history()
    n = int(months_ahead)
    if n < 1 or n > MAX_FORECAST_MONTHS:
        return ScenarioResult(
            ok=False, error_fa=f"تعداد ماه باید بین ۱ و {MAX_FORECAST_MONTHS} باشد.",
            kind="forecast", title="", subtitle="", sections=[], summary="",
        )
    future = _future_months(model, n)
    month_rows = []
    tons_total = {s: 0.0 for s in SECTIONS}
    tun_total: dict[str, float | None] = {s: 0.0 for s in SECTIONS}
    for label, x, _ym in future:
        row: dict[str, Any] = {"ماه": label}
        tot = 0.0
        for sec in SECTIONS:
            sm = model.sections[sec]
            t = sm.forecast_tons(x)
            tpt = sm.tons_per_tundish
            tun = (t / tpt) if tpt else None
            tons_total[sec] += t
            if tun is None:
                tun_total[sec] = None
            elif tun_total[sec] is not None:
                tun_total[sec] = (tun_total[sec] or 0) + tun
            row[f"تناژ_{mg.SECTION_LABEL_FA[sec]}"] = _r(t, 1)
            row[f"تاندیش_{mg.SECTION_LABEL_FA[sec]}"] = _r(tun, 1) if tun is not None else "—"
            tot += t
        row["جمع_تناژ"] = _r(tot, 1)
        month_rows.append(row)
    cols = ["ماه"]
    for sec in SECTIONS:
        cols += [f"تناژ_{mg.SECTION_LABEL_FA[sec]}", f"تاندیش_{mg.SECTION_LABEL_FA[sec]}"]
    cols.append("جمع_تناژ")

    sum_rows = []
    for sec in SECTIONS:
        sm = model.sections[sec]
        _i, slope, method = sm.trend()
        tun = tun_total[sec]
        sum_rows.append(
            {
                "بخش": mg.SECTION_LABEL_FA[sec],
                "تناژ_پیش‌بینی_کل": _r(tons_total[sec], 1),
                "تاندیش_پیش‌بینی_کل": math.ceil(tun) if tun else "—",
                "میانگین_ماهانه_سابقه": _r(sm.avg_monthly_tons, 1),
                "روند_تن_در_ماه": _r(slope, 1) if slope else 0,
                "روش": method,
            }
        )
    grand = sum(tons_total.values())
    tun_known = [math.ceil(v) for v in tun_total.values() if v]
    sum_rows.append(
        {
            "بخش": "جمع",
            "تناژ_پیش‌بینی_کل": _r(grand, 1),
            "تاندیش_پیش‌بینی_کل": sum(tun_known) if tun_known else "—",
            "میانگین_ماهانه_سابقه": _r(sum(model.sections[s].avg_monthly_tons for s in SECTIONS), 1),
            "روند_تن_در_ماه": "—",
            "روش": "—",
        }
    )
    horizon_fa = f"{n} ماه آینده ({future[0][0]} تا {future[-1][0]})"
    sections = [
        {
            "title": f"سناریو ۲ — پیش‌بینی {horizon_fa}",
            "columns": ["بخش", "تناژ_پیش‌بینی_کل", "تاندیش_پیش‌بینی_کل", "میانگین_ماهانه_سابقه", "روند_تن_در_ماه", "روش"],
            "rows": sum_rows,
        },
        {"title": "پیش‌بینی ماه‌به‌ماه تناژ و تاندیش", "columns": cols, "rows": month_rows},
        {
            "title": f"مواد مورد نیاز برای {n} ماه آینده",
            "columns": _NEED_COLS,
            "rows": _material_need_rows(
                model,
                tons_total,
                {s: (math.ceil(v) if v else None) for s, v in tun_total.items()},
            ),
        },
        *history_sections(model),
    ]
    warns = model.warnings()
    note_lines = _base_notes(model) + [
        "روند: رگرسیون خطی تناژ ماهانهٔ هر بخش روی ماه‌های سابقه (با ≥۳ ماه)؛ کمتر از ۳ ماه → میانگین ثابت.",
        "پیش‌بینی هر ماه در بازهٔ [۰، ۱٫۵ × بیشینهٔ تناژ ماهانهٔ سابقه] محدود می‌شود.",
        "تاندیش هر ماه = تناژ پیش‌بینی ÷ تن بر تاندیش تاریخی.",
    ] + warns
    sections.append(_notes_section(note_lines))
    lines = [f"🔮 سناریو ۲ — پیش‌بینی {horizon_fa}", f"سابقه: {model.n_months} ماه"]
    for sec in SECTIONS:
        tun = tun_total[sec]
        lines.append(
            f"• {mg.SECTION_LABEL_FA[sec]}: {tons_total[sec]:,.0f} تن، تاندیش ≈ {math.ceil(tun) if tun else '—'}"
        )
    lines.append(f"جمع تناژ پیش‌بینی: {grand:,.0f} تن")
    lines += [f"⚠ {w}" for w in warns]
    return ScenarioResult(
        ok=True,
        error_fa=None,
        kind="forecast",
        title=f"{mg.TITLE_FA} — پیش‌بینی {n} ماه آینده",
        subtitle=f"{horizon_fa} | سابقه: {model.n_months} ماه",
        sections=sections,
        summary="\n".join(lines),
        warnings=warns,
        params={"months_ahead": n},
        total_tons=grand,
    )


# ---------------------------------------------------------------- export + persist (bot & web)
def export_scenario(
    result: ScenarioResult,
    *,
    stem: str,
    report_dir: Path,
    letterhead_path: Path | str | None = None,
) -> tuple[Path, Path]:
    from excel.simple_report import generate_simple_report_xlsx
    from pdf.generator import generate_simple_report_pdf

    report_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = generate_simple_report_pdf(
        result.title,
        subtitle=result.subtitle,
        sections=result.sections,
        empty_message="داده‌ای برای این سناریو نیست.",
        output_path=report_dir / f"{stem}.pdf",
        filename_stem=stem,
        letterhead_path=letterhead_path,
    )
    xlsx_path = generate_simple_report_xlsx(
        result.title,
        subtitle=result.subtitle,
        sections=result.sections,
        empty_message="داده‌ای برای این سناریو نیست.",
        output_path=report_dir / f"{stem}.xlsx",
        filename_stem=stem,
    )
    return Path(pdf_path), Path(xlsx_path)


def persist_scenario(db: Any, result: ScenarioResult, model: HistoryModel, *, user: dict, source: str) -> dict:
    now = tehran_now()
    label = result.subtitle.split("|")[0].strip()
    kind_fa = "سناریو تناژ هدف" if result.kind == "target" else "سناریو پیش‌بینی"
    return db.insert_main_goal_report(
        period_key=f"scenario:{result.kind}",
        period_label=f"{kind_fa} — {label}",
        period_json=json.dumps({"months": [m.period_key for m in model.months]}, ensure_ascii=False),
        files_json=json.dumps({}, ensure_ascii=False),
        results_json=result.to_json(),
        summary_text=result.summary,
        target_tons=result.total_tons,
        source=source,
        bale_user_id=user["bale_user_id"],
        actor_display_name=user.get("display_name"),
        created_at=mg.utc_now_iso(),
        created_at_tehran=now.isoformat(),
        jalali_date=format_date(now),
    )


def history_overview_text(months: list[MonthRecord]) -> str:
    if not months:
        return "📚 هنوز ماهی ذخیره نشده است."
    lines = [f"📚 ماه‌های ذخیره‌شده ({len(months)}):"]
    for i, m in enumerate(months, 1):
        lines.append(f"{i}) {m.label} — {m.production.total_tons:,.0f} تن — ثبت {m.jalali_date} ({m.actor})")
    lines.append(history_count_line(len(months), gap_warning=consecutive_month_gap_warning(months)))
    return "\n".join(lines)
