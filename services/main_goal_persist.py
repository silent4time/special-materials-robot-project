"""Persist normalized main-goal inputs (production OCR / tundish Excel) and
build MonthRecord history + range reports from DB.

Shared by bot + web. Legacy ``main_goal_months.stats_json`` is kept in sync when
a month has production + all three section consumptions.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from bot.jalali import format_date, format_month_year, jalali_today, tehran_now
from config import UPLOAD_DIR
from services import main_goal_history as mgh
from services import main_goal_production_ocr as ocr
from services import main_goal_report as mg

HISTORY_DIR_NAME = mgh.HISTORY_DIR_NAME
SECTIONS = mgh.SECTIONS

# Production-row source status (main_goal_production.source_status)
STATUS_VALID = "valid"
STATUS_CASTING_UNVALIDATED = "casting_needs_validation"
STATUS_FURNACE_PROVISIONAL = "furnace_tab_provisional"
NEEDS_CASTING_NOTE_FA = "نیاز به عکس تب ریخته‌گری"
PROVISIONAL_LABEL_FA = "⚠ موقت (تب کوره) — " + NEEDS_CASTING_NOTE_FA


def is_provisional_production(row: dict | None) -> bool:
    """Furnace-tab production rows are provisional: kept for audit, excluded from calcs."""
    if not row:
        return False
    return (row.get("source_status") == STATUS_FURNACE_PROVISIONAL) or (
        (row.get("report_tab") or "") == "furnace"
    )


def usable_production(db: Any, period_key: str) -> dict | None:
    """Production row usable for section tonnage (None when absent or provisional)."""
    row = db.get_main_goal_production_by_key(period_key)
    return None if is_provisional_production(row) else row


def period_sort_key(year: int | None, month: int | None, period_key: str) -> str:
    if year and month:
        return f"{int(year):04d}-{int(month):02d}"
    return "9999-" + mg.normalize_text(period_key)


def _safe_dir(period_key: str) -> str:
    return re.sub(r"[^0-9A-Za-z_\-]+", "_", period_key).strip("_") or "period"


# ---------------------------------------------------------------- store production
@dataclass
class InputStoreOutcome:
    ok: bool
    error_fa: str | None = None
    period_key: str | None = None
    period_label: str = ""
    replaced: bool = False
    summary: str = ""
    ocr_result: ocr.ProductionOCRResult | None = None
    needs_confirm: bool = False
    tab_rejected: bool = False
    missing_parts: list[str] = field(default_factory=list)


def store_production_from_ocr(
    db: Any,
    image_path: Path | str,
    *,
    user: dict,
    source: str,
    filename: str | None = None,
    ocr_result: ocr.ProductionOCRResult | None = None,
    manual_corrected: bool = False,
) -> InputStoreOutcome:
    path = Path(image_path)
    result = ocr_result or ocr.ocr_production_image(path)
    tab = (getattr(result, "report_tab", None) or "unknown").strip() or "unknown"
    if getattr(result, "tab_rejected", False) or tab in {"furnace", "unknown"}:
        # Only CASTING-tab photos (or manual/excel values) may be stored.
        return InputStoreOutcome(
            ok=False,
            error_fa=result.error_fa
            or (ocr.ALARM_FURNACE_FA if tab == "furnace" else ocr.ALARM_UNKNOWN_FA),
            ocr_result=result,
            tab_rejected=True,
            summary="",
        )
    if not result.period_key or result.total_tons <= 0:
        return InputStoreOutcome(
            ok=False,
            error_fa=result.error_fa or "استخراج تولید از عکس ناموفق بود.",
            ocr_result=result,
            needs_confirm=True,
            summary=ocr.result_summary_fa(result),
        )
    dest_dir = UPLOAD_DIR / HISTORY_DIR_NAME / _safe_dir(result.period_key)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"production{path.suffix.lower() or '.png'}"
    try:
        if path.resolve() != dest.resolve():
            shutil.copy2(path, dest)
    except OSError:
        dest = path
    now = tehran_now()
    if tab == "casting" and getattr(result, "needs_validation", False) and not manual_corrected:
        status = STATUS_CASTING_UNVALIDATED
    else:
        status = STATUS_VALID
    sec_melts = result.section_melts() if hasattr(result, "section_melts") else {}
    row, replaced = db.upsert_main_goal_production(
        period_key=result.period_key,
        period_label=result.period_label,
        year=result.year,
        month=result.month,
        sort_key=period_sort_key(result.year, result.month, result.period_key),
        slab_tons=result.slab_tons,
        bloom_tons=result.bloom_tons,
        billet_tons=result.billet_tons,
        total_tons=result.total_tons,
        melt_count=result.melt_count,
        melt_weight_kg=getattr(result, "melt_weight_kg", None),
        product_weight_kg=getattr(result, "product_weight_kg", None),
        slab_count=getattr(result, "slab_count", None),
        bloom_billet_count=getattr(result, "bloom_billet_count", None),
        melts_per_day=getattr(result, "melts_per_day", None),
        report_tab=tab,
        source_status=status,
        slab_melt_count=sec_melts.get("slab"),
        bloom_melt_count=sec_melts.get("bloom"),
        billet_melt_count=sec_melts.get("billet"),
        ccm1_tons=result.ccm_tons.get(1),
        ccm2_tons=result.ccm_tons.get(2),
        ccm3_tons=result.ccm_tons.get(3),
        ccm4_tons=result.ccm_tons.get(4),
        ccm5_tons=result.ccm_tons.get(5),
        source_type="manual" if manual_corrected else "ocr",
        source_path=str(dest),
        source_filename=filename or path.name,
        ocr_raw_text=result.ocr_raw_text,
        ocr_confidence=result.ocr_confidence,
        ocr_fields_json=result.to_fields_json(),
        manual_corrected=1 if manual_corrected else 0,
        notes_json=json.dumps(result.notes, ensure_ascii=False),
        missing_json=json.dumps(result.missing, ensure_ascii=False),
        bale_user_id=user["bale_user_id"],
        actor_display_name=user.get("display_name"),
        source=source,
        created_at_tehran=now.isoformat(),
        jalali_date=format_date(now),
    )
    sync_month_aggregate(db, result.period_key, user=user, source=source)
    verb = "جایگزین شد" if replaced else "ذخیره شد"
    summary = (
        f"✅ آمار تولید «{result.period_label}» {verb} (از عکس).\n"
        + ocr.result_summary_fa(result)
    )
    return InputStoreOutcome(
        ok=True,
        period_key=result.period_key,
        period_label=result.period_label,
        replaced=replaced,
        summary=summary,
        ocr_result=result,
        missing_parts=_missing_parts(db, result.period_key),
    )


def store_production_from_excel(
    db: Any,
    path: Path | str,
    *,
    user: dict,
    source: str,
    filename: str | None = None,
    period: mg.PeriodKey | None = None,
) -> InputStoreOutcome:
    path = Path(path)
    period = period or mg.detect_period(path, filename=filename)[0]
    if not period or period.kind != "month" or not period.year or not period.month:
        return InputStoreOutcome(
            ok=False,
            error_fa="ماه جلالی از فایل آمار تولید Excel تشخیص داده نشد.",
        )
    stats = mg.parse_production(path, period=period)
    if stats.total_tons <= 0:
        return InputStoreOutcome(
            ok=False,
            error_fa="تناژ از فایل Excel استخراج نشد: " + "؛ ".join(stats.missing or ["بدون مقدار"]),
        )
    # wrap as OCR-like result for store path
    fake = ocr.ProductionOCRResult(
        ok=True,
        year=period.year,
        month=period.month,
        period_key=period.key(),
        period_label=period.label_fa(),
        slab_tons=stats.slab_tons,
        bloom_tons=stats.bloom_tons,
        billet_tons=stats.billet_tons,
        notes=list(stats.notes),
        missing=list(stats.missing),
        fields={"source": "excel"},
    )
    dest_dir = UPLOAD_DIR / HISTORY_DIR_NAME / _safe_dir(fake.period_key)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "production.xlsx"
    try:
        if path.resolve() != dest.resolve():
            shutil.copy2(path, dest)
    except OSError:
        dest = path
    now = tehran_now()
    row, replaced = db.upsert_main_goal_production(
        period_key=fake.period_key,
        period_label=fake.period_label,
        year=fake.year,
        month=fake.month,
        sort_key=period_sort_key(fake.year, fake.month, fake.period_key),
        slab_tons=fake.slab_tons,
        bloom_tons=fake.bloom_tons,
        billet_tons=fake.billet_tons,
        total_tons=fake.total_tons,
        melt_count=None,
        melt_weight_kg=None,
        product_weight_kg=None,
        slab_count=None,
        bloom_billet_count=None,
        melts_per_day=None,
        report_tab="excel",
        source_status=STATUS_VALID,
        slab_melt_count=None,
        bloom_melt_count=None,
        billet_melt_count=None,
        ccm1_tons=None,
        ccm2_tons=None,
        ccm3_tons=None,
        ccm4_tons=None,
        ccm5_tons=None,
        source_type="excel",
        source_path=str(dest),
        source_filename=filename or path.name,
        ocr_raw_text=None,
        ocr_confidence=None,
        ocr_fields_json=fake.to_fields_json(),
        manual_corrected=0,
        notes_json=json.dumps(fake.notes, ensure_ascii=False),
        missing_json=json.dumps(fake.missing, ensure_ascii=False),
        bale_user_id=user["bale_user_id"],
        actor_display_name=user.get("display_name"),
        source=source,
        created_at_tehran=now.isoformat(),
        jalali_date=format_date(now),
    )
    sync_month_aggregate(db, fake.period_key, user=user, source=source)
    verb = "جایگزین شد" if replaced else "ذخیره شد"
    return InputStoreOutcome(
        ok=True,
        period_key=fake.period_key,
        period_label=fake.period_label,
        replaced=replaced,
        summary=(
            f"✅ آمار تولید «{fake.period_label}» {verb} (از Excel).\n"
            f"تناژ: اسلب {fake.slab_tons:,.0f} | بلوم {fake.bloom_tons:,.0f} | "
            f"بیلت {fake.billet_tons:,.0f}"
        ),
        missing_parts=_missing_parts(db, fake.period_key),
    )


# ---------------------------------------------------------------- store tundish excel
def store_consumption_from_excel(
    db: Any,
    path: Path | str,
    section: str,
    *,
    user: dict,
    source: str,
    filename: str | None = None,
    period: mg.PeriodKey | None = None,
    inventory: pd.DataFrame | None = None,
) -> InputStoreOutcome:
    if section not in SECTIONS:
        return InputStoreOutcome(ok=False, error_fa=f"بخش نامعتبر: {section}")
    path = Path(path)
    period = period or mg.detect_period(path, filename=filename)[0]
    if not period or not period.year or not period.month:
        return InputStoreOutcome(
            ok=False,
            error_fa=f"ماه از فایل مصرف تاندیش {mg.SECTION_LABEL_FA[section]} تشخیص داده نشد.",
        )
    stats = mg.parse_consumption(path, section, period=period)
    # optional: patch+renovate as tundish if explicit tundish missing
    # (user said monthly tundish usage = patching + renovation summed)
    if stats.tundish_count is None:
        patch = renovate = None
        for line in stats.materials:
            fk = mg.fold_key(line.name)
            if "patch" in fk or "پچ" in line.name or "پatch" in fk:
                patch = (patch or 0) + line.quantity
            if "renov" in fk or "relin" in fk or "نوسازی" in line.name or "تعمیر" in line.name:
                renovate = (renovate or 0) + line.quantity
        # Also scan notes / raw labels via parse — materials skip those rows;
        # re-read for NO. PATCH / NO. RELIN style meta rows is already in parse_consumption
        # as tundish if labeled. Keep patch/renovate fields for audit.
    else:
        patch = renovate = None

    dest_dir = UPLOAD_DIR / HISTORY_DIR_NAME / _safe_dir(period.key())
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{section}_consumption.xlsx"
    try:
        if path.resolve() != dest.resolve():
            shutil.copy2(path, dest)
    except OSError:
        dest = path

    # Try extract patch/renovate counts from sheet labels
    patch_count, renovate_count = _extract_patch_renovate(path)
    if stats.tundish_count is None and (patch_count or renovate_count):
        stats.tundish_count = float(patch_count or 0) + float(renovate_count or 0)
        stats.notes.append(
            f"تعداد تاندیش = پچ({patch_count or 0:g}) + نوسازی({renovate_count or 0:g})"
        )

    mats = []
    for i, m in enumerate(stats.materials):
        mats.append(
            {
                "name": m.name,
                "quantity": m.quantity,
                "unit": m.unit,
                "item_id": m.item_id,
                "keyword": m.keyword,
                "sort_order": i,
            }
        )
    now = tehran_now()
    _row, replaced = db.upsert_main_goal_consumption(
        period_key=period.key(),
        period_label=period.label_fa(),
        year=period.year,
        month=period.month,
        sort_key=period_sort_key(period.year, period.month, period.key()),
        section=section,
        tundish_count=stats.tundish_count,
        melt_count=stats.melt_count,
        patch_count=patch_count,
        renovate_count=renovate_count,
        source_path=str(dest),
        source_filename=filename or path.name,
        notes_json=json.dumps(stats.notes, ensure_ascii=False),
        missing_json=json.dumps(stats.missing, ensure_ascii=False),
        bale_user_id=user["bale_user_id"],
        actor_display_name=user.get("display_name"),
        source=source,
        created_at_tehran=now.isoformat(),
        jalali_date=format_date(now),
        materials=mats,
    )
    sync_month_aggregate(db, period.key(), user=user, source=source, inventory=inventory)
    tc = f"{stats.tundish_count:g}" if stats.tundish_count is not None else "—"
    verb = "جایگزین شد" if replaced else "ذخیره شد"
    return InputStoreOutcome(
        ok=True,
        period_key=period.key(),
        period_label=period.label_fa(),
        replaced=replaced,
        summary=(
            f"✅ مصرف تاندیش {mg.SECTION_LABEL_FA[section]} «{period.label_fa()}» {verb}.\n"
            f"تاندیش: {tc} | اقلام مواد: {len(mats)}"
        ),
        missing_parts=_missing_parts(db, period.key()),
    )


def _extract_patch_renovate(path: Path) -> tuple[float | None, float | None]:
    patch = renovate = None
    try:
        sheets = mg._read_all_sheets(path)  # noqa: SLF001 — shared parser helpers
    except Exception:  # noqa: BLE001
        return None, None
    for _name, df in sheets:
        for r in range(len(df)):
            label = mg.normalize_text(mg._row_label(df, r))  # noqa: SLF001
            if not label:
                continue
            f = mg.fold_key(label)
            val = None
            for c in range(min(df.shape[1], 6)):
                v = mg._to_float(df.iat[r, c])  # noqa: SLF001
                if v is not None and c > 0:
                    val = v
                    break
            if val is None:
                continue
            if "patch" in f or "پچ" in label or "لکه" in label:
                patch = (patch or 0) + float(val)
            if "renov" in f or "relin" in f or "نوسازی" in label or "رلاين" in label or "رلاین" in label:
                renovate = (renovate or 0) + float(val)
    return patch, renovate


# ---------------------------------------------------------------- completeness + sync
def _missing_parts(db: Any, period_key: str) -> list[str]:
    missing = []
    prod = db.get_main_goal_production_by_key(period_key)
    if not prod:
        missing.append("آمار تولید")
    elif is_provisional_production(prod):
        missing.append(f"آمار تولید ({NEEDS_CASTING_NOTE_FA})")
    have = {c["section"] for c in db.list_main_goal_consumption(period_key=period_key)}
    for sec in SECTIONS:
        if sec not in have:
            missing.append(f"مصرف تاندیش {mg.SECTION_LABEL_FA[sec]}")
    return missing


def month_completeness(db: Any) -> list[dict[str, Any]]:
    """Overview of every known period and which inputs are present."""
    keys = set(db.list_main_goal_period_keys_union())
    # also include keys only in legacy months
    out = []
    for key in sorted(keys):
        prod = db.get_main_goal_production_by_key(key)
        cons = {c["section"]: c for c in db.list_main_goal_consumption(period_key=key)}
        label = (
            (prod or {}).get("period_label")
            or next((c["period_label"] for c in cons.values()), None)
            or key
        )
        year = (prod or {}).get("year") or next((c.get("year") for c in cons.values()), None)
        month = (prod or {}).get("month") or next((c.get("month") for c in cons.values()), None)
        missing = _missing_parts(db, key)
        out.append(
            {
                "period_key": key,
                "label": label,
                "year": year,
                "month": month,
                "has_production": bool(prod) and not is_provisional_production(prod),
                "production_provisional": is_provisional_production(prod),
                "production_status": (prod or {}).get("source_status") or "",
                "production_note": (
                    PROVISIONAL_LABEL_FA if is_provisional_production(prod)
                    else ("⚠ پارسر ریخته‌گری اعتبارسنجی نشده"
                          if (prod or {}).get("source_status") == STATUS_CASTING_UNVALIDATED else "")
                ),
                "provisional_total_tons": (
                    float((prod or {}).get("total_tons") or 0) if is_provisional_production(prod) else None
                ),
                "sections": {s: s in cons for s in SECTIONS},
                "missing": missing,
                "complete": not missing,
            }
        )
    out.sort(key=lambda r: period_sort_key(r["year"], r["month"], r["period_key"]))
    return out


def sync_month_aggregate(
    db: Any,
    period_key: str,
    *,
    user: dict,
    source: str,
    inventory: pd.DataFrame | None = None,
) -> dict | None:
    """When production + 3 consumptions exist, refresh legacy main_goal_months row."""
    prod = usable_production(db, period_key)
    cons_rows = {c["section"]: c for c in db.list_main_goal_consumption(period_key=period_key)}
    if not prod or any(s not in cons_rows for s in SECTIONS):
        return None
    production = mg.ProductionStats(
        slab_tons=float(prod.get("slab_tons") or 0),
        bloom_tons=float(prod.get("bloom_tons") or 0),
        billet_tons=float(prod.get("billet_tons") or 0),
        notes=json.loads(prod.get("notes_json") or "[]"),
        missing=json.loads(prod.get("missing_json") or "[]"),
    )
    consumptions: dict[str, mg.ConsumptionStats] = {}
    for sec in SECTIONS:
        c = cons_rows[sec]
        mats = [
            mg.MaterialLine(
                name=str(m.get("name") or ""),
                quantity=float(m.get("quantity") or 0),
                unit=str(m.get("unit") or "kg"),
                item_id=m.get("item_id"),
                keyword=m.get("keyword"),
            )
            for m in (c.get("materials") or [])
        ]
        consumptions[sec] = mg.ConsumptionStats(
            section=sec,
            tundish_count=c.get("tundish_count"),
            melt_count=c.get("melt_count"),
            materials=mats,
            notes=json.loads(c.get("notes_json") or "[]"),
            missing=json.loads(c.get("missing_json") or "[]"),
        )
    rate_rows = mg.build_rate_rows(production, consumptions, inventory=inventory)

    files_meta = {
        "production": {
            "path": prod.get("source_path"),
            "filename": prod.get("source_filename"),
            "source_type": prod.get("source_type"),
        }
    }
    for sec in SECTIONS:
        c = cons_rows[sec]
        files_meta[f"{sec}_consumption"] = {
            "path": c.get("source_path"),
            "filename": c.get("source_filename"),
        }
    result = mg.MainGoalResult(
        ok=True,
        error_fa=None,
        period=mg.PeriodKey(
            kind="month",
            year=prod.get("year"),
            month=prod.get("month"),
            raw=prod.get("period_label") or "",
        ),
        period_sources={},
        production=production,
        consumptions=consumptions,
        rate_rows=rate_rows or [],
        sections=[],
        target_tons=None,
        warnings=[],
    )
    now = tehran_now()
    row, _ = db.upsert_main_goal_month(
        period_key=period_key,
        period_label=prod.get("period_label") or period_key,
        period_kind="month",
        year=prod.get("year"),
        month=prod.get("month"),
        sort_key=period_sort_key(prod.get("year"), prod.get("month"), period_key),
        files_json=json.dumps(files_meta, ensure_ascii=False),
        stats_json=mgh._stats_json(result),  # noqa: SLF001
        summary_text=result.summary_text() if hasattr(result, "summary_text") else "",
        source=source,
        bale_user_id=user["bale_user_id"],
        actor_display_name=user.get("display_name"),
        created_at_tehran=now.isoformat(),
        jalali_date=format_date(now),
    )
    return row


# ---------------------------------------------------------------- load history from normalized DB
def _production_from_row(row: dict) -> mg.ProductionStats:
    return mg.ProductionStats(
        slab_tons=float(row.get("slab_tons") or 0),
        bloom_tons=float(row.get("bloom_tons") or 0),
        billet_tons=float(row.get("billet_tons") or 0),
        notes=json.loads(row.get("notes_json") or "[]"),
        missing=json.loads(row.get("missing_json") or "[]"),
    )


def _consumption_from_row(row: dict) -> mg.ConsumptionStats:
    mats = []
    for m in row.get("materials") or []:
        mats.append(
            mg.MaterialLine(
                name=str(m.get("name") or ""),
                quantity=float(m.get("quantity") or 0),
                unit=str(m.get("unit") or "kg"),
                item_id=m.get("item_id"),
                keyword=m.get("keyword"),
            )
        )
    return mg.ConsumptionStats(
        section=str(row["section"]),
        tundish_count=row.get("tundish_count"),
        melt_count=row.get("melt_count"),
        materials=mats,
        notes=json.loads(row.get("notes_json") or "[]"),
        missing=json.loads(row.get("missing_json") or "[]"),
    )


def load_history_from_db(db: Any, *, include_incomplete: bool = True) -> list[mgh.MonthRecord]:
    """Build MonthRecords from normalized tables; fall back to legacy months."""
    by_key: dict[str, mgh.MonthRecord] = {}
    # legacy first (may be overwritten by normalized)
    for row in db.list_main_goal_months():
        try:
            by_key[str(row["period_key"])] = mgh.month_record_from_row(row)
        except Exception:  # noqa: BLE001
            continue
    # overlay / add from normalized production + consumption
    prods = {p["period_key"]: p for p in db.list_main_goal_production()}
    all_cons = db.list_main_goal_consumption()
    cons_by_key: dict[str, dict[str, dict]] = {}
    for c in all_cons:
        cons_by_key.setdefault(c["period_key"], {})[c["section"]] = c

    keys = set(prods) | set(cons_by_key) | set(by_key)
    out: list[mgh.MonthRecord] = []
    for key in keys:
        prod = prods.get(key)
        cons_map = cons_by_key.get(key) or {}
        if not prod and not cons_map:
            if key in by_key:
                out.append(by_key[key])
            continue
        if not include_incomplete and (
            not prod or is_provisional_production(prod) or any(s not in cons_map for s in SECTIONS)
        ):
            continue
        if prod and is_provisional_production(prod):
            if not cons_map:
                # furnace-tab only month: keep out of history until a casting photo arrives
                continue
            sample = next(iter(cons_map.values()))
            production = mg.ProductionStats(
                missing=[f"آمار تولید ({NEEDS_CASTING_NOTE_FA}) — ردیف تب کوره موقت است"]
            )
            year, month = prod.get("year") or sample.get("year"), prod.get("month") or sample.get("month")
            label = prod.get("period_label") or sample.get("period_label") or key
            sort_key = prod.get("sort_key") or period_sort_key(year, month, key)
            actor = sample.get("actor_display_name") or ""
            source = sample.get("source") or ""
            jalali = sample.get("jalali_date") or ""
            rid = int(sample["id"])
        elif prod:
            production = _production_from_row(prod)
            year, month = prod.get("year"), prod.get("month")
            label = prod.get("period_label") or key
            sort_key = prod.get("sort_key") or period_sort_key(year, month, key)
            actor = prod.get("actor_display_name") or ""
            source = prod.get("source") or ""
            jalali = prod.get("jalali_date") or ""
            rid = int(prod["id"])
        else:
            production = mg.ProductionStats()
            sample = next(iter(cons_map.values()))
            year, month = sample.get("year"), sample.get("month")
            label = sample.get("period_label") or key
            sort_key = sample.get("sort_key") or period_sort_key(year, month, key)
            actor = sample.get("actor_display_name") or ""
            source = sample.get("source") or ""
            jalali = sample.get("jalali_date") or ""
            rid = int(sample["id"])
        consumptions = {
            sec: _consumption_from_row(cons_map[sec]) for sec in SECTIONS if sec in cons_map
        }
        out.append(
            mgh.MonthRecord(
                id=rid,
                period_key=key,
                label=label,
                kind="month",
                year=year,
                month=month,
                sort_key=sort_key,
                production=production,
                consumptions=consumptions,
                jalali_date=jalali,
                actor=actor,
                source=source,
            )
        )
    out.sort(key=lambda m: m.sort_key or "")
    return out


# ---------------------------------------------------------------- range selection
@dataclass
class RangeSpec:
    kind: str  # last_n | custom
    n_months: int | None = None
    start: tuple[int, int] | None = None  # (year, month)
    end: tuple[int, int] | None = None
    label_fa: str = ""


def range_last_n(n: int) -> RangeSpec:
    return RangeSpec(kind="last_n", n_months=n, label_fa=f"{n} ماه اخیر")


def range_custom(start: tuple[int, int], end: tuple[int, int]) -> RangeSpec:
    return RangeSpec(
        kind="custom",
        start=start,
        end=end,
        label_fa=f"از {format_month_year(*start, named=True)} تا {format_month_year(*end, named=True)}",
    )


def parse_range_choice(text: str) -> RangeSpec | None:
    """«۳ ماهه» / «۶ ماهه» / «یکساله» / «از مهر ۱۴۰۵ تا آذر ۱۴۰۵»."""
    s = mg.normalize_text(text)
    if not s:
        return None
    if re.search(r"۳\s*ماه|3\s*ماه|سه\s*ماه", s) or s in {"۳ ماهه", "3 ماهه"}:
        return range_last_n(3)
    if re.search(r"۶\s*ماه|6\s*ماه|شش\s*ماه", s) or s in {"۶ ماهه", "6 ماهه"}:
        return range_last_n(6)
    if re.search(r"۱۲\s*ماه|12\s*ماه|یک\s*سال|1\s*سال|سالانه|یکساله", s) or s in {"یکساله", "سالیانه"}:
        return range_last_n(12)
    from bot.jalali import parse_month_year_range, parse_month_year_token

    rng = parse_month_year_range(s)
    if rng:
        return range_custom(rng[0], rng[1])
    # single month → custom one-month window
    tok = parse_month_year_token(s)
    if tok:
        return range_custom(tok, tok)
    return None


def filter_months_by_range(
    months: list[mgh.MonthRecord], spec: RangeSpec
) -> tuple[list[mgh.MonthRecord], list[str]]:
    """Return (selected months, warning lines about gaps)."""
    dated = [m for m in months if m.kind == "month" and m.year and m.month]
    dated.sort(key=lambda m: (int(m.year), int(m.month)))
    warns: list[str] = []
    if spec.kind == "last_n":
        n = int(spec.n_months or 3)
        # anchor = last available month (or today)
        if dated:
            selected = dated[-n:]
        else:
            selected = []
        if len(selected) < n:
            warns.append(
                f"فقط {len(selected)} ماه در DB موجود است (درخواست: {n} ماه اخیر)."
            )
        return selected, warns

    assert spec.start and spec.end
    y1, m1 = spec.start
    y2, m2 = spec.end
    if (y2, m2) < (y1, m1):
        y1, m1, y2, m2 = y2, m2, y1, m1
    selected = [
        m for m in dated if (int(m.year), int(m.month)) >= (y1, m1) and (int(m.year), int(m.month)) <= (y2, m2)
    ]
    # expected months in range
    expected = []
    y, m = y1, m1
    while (y, m) <= (y2, m2):
        expected.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    have = {(int(m.year), int(m.month)) for m in selected}
    missing_months = [format_month_year(y, m, named=True) for y, m in expected if (y, m) not in have]
    if missing_months:
        warns.append("ماه‌های بدون داده در بازه: " + "، ".join(missing_months))
    # per-month incomplete sections
    for mrec in selected:
        miss = []
        if mrec.production.total_tons <= 0:
            if any(NEEDS_CASTING_NOTE_FA in x for x in (mrec.production.missing or [])):
                miss.append(f"تولید — {NEEDS_CASTING_NOTE_FA}")
            else:
                miss.append("تولید")
        for sec in SECTIONS:
            if sec not in mrec.consumptions:
                miss.append(mg.SECTION_LABEL_FA[sec])
        if miss:
            warns.append(f"{mrec.label}: ناقص ({'، '.join(miss)})")
    return selected, warns


# ---------------------------------------------------------------- range report
def scenario_range_report(
    model: mgh.HistoryModel,
    spec: RangeSpec,
    *,
    warnings: list[str] | None = None,
) -> mgh.ScenarioResult:
    """Materials-vs-steel report for an aggregated DB range (rates from those months)."""
    if model.n_months == 0:
        return mgh._no_history()
    warns = list(warnings or []) + model.warnings()
    # Use historical pooled rates; present tonnage / tundish / materials for the window
    tons_by_sec = {sec: model.sections[sec].total_tons for sec in SECTIONS}
    tundish_by_sec: dict[str, float | None] = {}
    for sec in SECTIONS:
        sm = model.sections[sec]
        tundish_by_sec[sec] = sm.total_tundish if sm.total_tundish else None

    plan_rows = []
    for sec in SECTIONS:
        sm = model.sections[sec]
        plan_rows.append(
            {
                "بخش": mg.SECTION_LABEL_FA[sec],
                "تناژ_بازه": mgh._r(sm.total_tons, 1),
                "تاندیش_بازه": mgh._r(sm.total_tundish, 1) if sm.total_tundish else "—",
                "ذوب_بازه": mgh._r(sm.total_melts, 0) if sm.total_melts else "—",
                "تن_بر_تاندیش": mgh._r(sm.tons_per_tundish),
                "ذوب_بر_تاندیش": mgh._r(sm.heats_per_tundish),
                "میانگین_تناژ_ماهانه": mgh._r(sm.avg_monthly_tons, 1),
            }
        )
    total = sum(tons_by_sec.values())
    plan_rows.append(
        {
            "بخش": "جمع",
            "تناژ_بازه": mgh._r(total, 1),
            "تاندیش_بازه": mgh._r(sum(model.sections[s].total_tundish for s in SECTIONS), 1),
            "ذوب_بازه": mgh._r(sum(model.sections[s].total_melts for s in SECTIONS), 0),
            "تن_بر_تاندیش": "—",
            "ذوب_بر_تاندیش": "—",
            "میانگین_تناژ_ماهانه": mgh._r(sum(model.sections[s].avg_monthly_tons for s in SECTIONS), 1),
        }
    )
    sections = [
        {
            "title": f"گزارش بازه — {spec.label_fa}",
            "columns": [
                "بخش", "تناژ_بازه", "تاندیش_بازه", "ذوب_بازه",
                "تن_بر_تاندیش", "ذوب_بر_تاندیش", "میانگین_تناژ_ماهانه",
            ],
            "rows": plan_rows,
        },
        {
            "title": "مواد مصرفی بازه (و نیاز معادل بر اساس نرخ)",
            "columns": mgh._NEED_COLS,
            "rows": mgh._material_need_rows(model, tons_by_sec, tundish_by_sec),
        },
        *mgh.history_sections(model),
    ]
    note_lines = mgh._base_notes(model) + [
        f"بازه گزارش: {spec.label_fa}",
        "نرخ‌ها از تجمیع ماه‌های همین بازه در پایگاه داده محاسبه شده‌اند.",
    ] + warns
    sections.append(mgh._notes_section(note_lines))
    lines = [
        f"📊 گزارش هدف اصلی — {spec.label_fa}",
        f"ماه‌های استفاده‌شده: {model.n_months}",
        f"جمع تناژ: {total:,.0f} تن",
    ]
    for sec in SECTIONS:
        sm = model.sections[sec]
        lines.append(
            f"• {mg.SECTION_LABEL_FA[sec]}: {sm.total_tons:,.0f} تن، "
            f"تاندیش {sm.total_tundish:g}" if sm.total_tundish else
            f"• {mg.SECTION_LABEL_FA[sec]}: {sm.total_tons:,.0f} تن، تاندیش —"
        )
    lines += [f"⚠ {w}" for w in warns]
    return mgh.ScenarioResult(
        ok=True,
        error_fa=None,
        kind="range",
        title=f"{mg.TITLE_FA} — گزارش بازه",
        subtitle=f"{spec.label_fa} | {model.n_months} ماه از DB",
        sections=sections,
        summary="\n".join(lines),
        warnings=warns,
        params={"range_kind": spec.kind, "n_months": spec.n_months, "start": spec.start, "end": spec.end},
        total_tons=total,
    )


def build_range_report(
    db: Any,
    spec: RangeSpec,
    *,
    inventory: pd.DataFrame | None = None,
    require_complete: bool = False,
) -> tuple[mgh.HistoryModel, mgh.ScenarioResult, list[str]]:
    months = load_history_from_db(db, include_incomplete=not require_complete)
    selected, warns = filter_months_by_range(months, spec)
    model = mgh.build_history_model(selected, inventory)
    result = scenario_range_report(model, spec, warnings=warns)
    return model, result, warns


def store_sequences_from_excel(
    db: Any,
    path: Path | str,
    *,
    user: dict,
    source: str,
    filename: str | None = None,
    section: str | None = None,
) -> InputStoreOutcome:
    """Parse post-cast sequence log → sequence rows + consumption aggregate.

    Rows are split by (start month × machine section) — a multi-month / multi-section log
    stores each (month, section) separately (replacing that pair only). ``section`` applies
    only to single-section files (see ``split_rows_by_period_section``).
    """
    from services import main_goal_sequences as seq

    path = Path(path)
    parsed = seq.parse_sequence_excel(path)
    if not parsed.ok:
        return InputStoreOutcome(ok=False, error_fa=parsed.error_fa or "خواندن سکوئنس ناموفق")
    if not (section or parsed.section):
        return InputStoreOutcome(ok=False, error_fa="بخش (اسلب/بلوم/بیلت) از فایل تشخیص داده نشد")
    groups, split_notes = seq.split_rows_by_period_section(parsed, section)
    if not groups:
        return InputStoreOutcome(ok=False, error_fa="هیچ ردیف سکوئنس با ماه/بخش معتبر یافت نشد")

    lines: list[str] = []
    touched: list[str] = []
    for (year, month, sec), rows in groups.items():
        period_key = f"m:{year:04d}-{month:02d}"
        label = format_month_year(year, month, named=True)
        n, agg = _store_sequence_group(
            db, path, period_key=period_key, period_label=label, year=year, month=month,
            section=sec, rows=rows, user=user, source=source, filename=filename,
            notes=split_notes,
        )
        if period_key not in touched:
            touched.append(period_key)
        lines.append(
            f"• {mg.SECTION_LABEL_FA[sec]} «{label}»: سکوئنس/تاندیش {n} | جمع ذوب {agg.get('melt_count') or 0:g} | "
            f"تعویض شرود {agg.get('shroud_replacements') or 0:g} | نازل بیرونی {agg.get('nozzle_replacements') or 0:g}"
        )
    for pk in touched:
        sync_month_aggregate(db, pk, user=user, source=source)
    last_key = touched[-1]
    last_label = next(format_month_year(y, m, named=True) for (y, m, _s) in reversed(list(groups)))
    head = (
        "✅ سکوئنس‌های تاندیش ذخیره شد"
        + (f" ({len(groups)} ماه×بخش):" if len(groups) > 1 else ":")
    )
    summary = "\n".join([head, *lines, *(f"ℹ {t}" for t in split_notes)])
    return InputStoreOutcome(
        ok=True,
        period_key=last_key,
        period_label=last_label,
        summary=summary,
        missing_parts=_missing_parts(db, last_key),
    )


def _store_sequence_group(
    db: Any,
    path: Path,
    *,
    period_key: str,
    period_label: str,
    year: int,
    month: int,
    section: str,
    rows: list,
    user: dict,
    source: str,
    filename: str | None,
    notes: list[str],
) -> tuple[int, dict]:
    sec = section
    dest_dir = UPLOAD_DIR / HISTORY_DIR_NAME / _safe_dir(period_key)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{sec}_sequences.xlsx"
    try:
        if path.resolve() != dest.resolve():
            shutil.copy2(path, dest)
    except OSError:
        dest = path

    now = tehran_now()
    meta = {
        "period_label": period_label,
        "year": year,
        "month": month,
        "source_path": str(dest),
        "source_filename": filename or path.name,
        "source": source,
        "bale_user_id": user["bale_user_id"],
        "actor_display_name": user.get("display_name"),
        "jalali_date": format_date(now),
    }
    row_dicts = [
        {
            "machine": r.machine,
            "tundish_no": r.tundish_no,
            "operator": r.operator,
            "melt_count": r.melt_count,
            "sequence_minutes": r.sequence_minutes,
            "isg": r.isg,
            "first_melt_no": r.first_melt_no,
            "first_cast_start": r.first_cast_start,
            "last_melt_no": r.last_melt_no,
            "last_cast_end": r.last_cast_end,
            "shroud_replaced": r.shroud_replaced,
            "outer_nozzle_replaced": r.outer_nozzle_replaced,
            "tube_changer": r.tube_changer,
        }
        for r in rows
    ]
    n = db.replace_main_goal_sequences(
        period_key=period_key, section=sec, rows=row_dicts, meta=meta
    )
    agg = db.aggregate_sequences(period_key, sec)
    # Also upsert consumption aggregate (tundish_count = sequence rows)
    materials = [
        {
            "name": "تعویض شرود",
            "quantity": float(agg.get("shroud_replacements") or 0),
            "unit": "عدد",
            "sort_order": 0,
        },
        {
            "name": "تعویض نازل بیرونی",
            "quantity": float(agg.get("nozzle_replacements") or 0),
            "unit": "عدد",
            "sort_order": 1,
        },
    ]
    melt_sum = sum(float(r.melt_count or 0) for r in rows)
    db.upsert_main_goal_consumption(
        period_key=period_key,
        period_label=period_label,
        year=year,
        month=month,
        sort_key=period_sort_key(year, month, period_key),
        section=sec,
        tundish_count=float(agg.get("tundish_count") or n),
        melt_count=float(agg.get("melt_count") or 0),
        patch_count=None,
        renovate_count=None,
        source_path=str(dest),
        source_filename=filename or path.name,
        notes_json=json.dumps(
            [f"{n} سکوئنس، جمع ذوب {melt_sum:g}", *notes, f"از لاگ سکوئنس ({n} ردیف)"],
            ensure_ascii=False,
        ),
        missing_json=json.dumps([], ensure_ascii=False),
        bale_user_id=user["bale_user_id"],
        actor_display_name=user.get("display_name"),
        source=source,
        created_at_tehran=now.isoformat(),
        jalali_date=format_date(now),
        materials=materials,
    )
    return n, agg
