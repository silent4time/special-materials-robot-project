"""«📊 گزارش جامع» shared core (phase 3 item 19f) — the ONE builder used by bot and web.

* frames come from ``analytics.frames.load_frames`` (factory-wide latest extracts);
* the analytics bundle (rates, period, remaining+ledger, critical, forecast, suggest);
* PDF (``pdf.generator.generate_report``) + XLSX companion with the same tables.
Title/sections are therefore identical in both front-ends.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from analytics.frames import data_completeness, inventory_with_ledger, load_frames
from analytics.tundish import (
    critical_materials,
    daily_rates,
    filter_by_tundish_type,
    forecast,
    missing_files_for_goal,
    period_consumption,
    range_day_count,
    remaining,
    suggest_requests,
)
from config import CRITICAL_DAYS

logger = logging.getLogger(__name__)

SECTION_LABELS = {"slab": "اسلب", "bloom": "بلوم", "billet": "بیلت"}


def section_label(section: str | None) -> str:
    return SECTION_LABELS.get(section or "", "همه بخش‌ها")


def section_frames(frames: dict, section: str | None) -> dict:
    if not section:
        return frames
    return {k: (filter_by_tundish_type(f, section) if f is not None else f) for k, f in frames.items()}


def metas_for(frames: dict) -> dict[str, dict]:
    from excel.processor import FILE_TYPES

    out: dict[str, dict] = {}
    for key, df in frames.items():
        if df is None:
            continue
        out[key] = {
            "file_type": key,
            "label": FILE_TYPES.get(key, {}).get("label_fa", key),
            "total_rows": int(len(df)),
            "visible_rows": int(len(df)),
            "missing_optional": [],
        }
    return out


def load(db: Any, user: dict) -> tuple[dict, dict]:
    """Present frames only (bot semantics) + lightweight metas."""
    frames = {k: v for k, v in load_frames(db, user).items() if v is not None}
    return frames, metas_for(frames)


def missing_inputs(db: Any, user: dict, session: dict | None = None) -> list[str]:
    return missing_files_for_goal("full", data_completeness(db, user, session=session))


def build_bundle(
    db: Any,
    frames: dict,
    *,
    start: date | None = None,
    end: date | None = None,
    forecast_days: float | None = None,
) -> dict[str, Any]:
    tank = frames.get("tank_consumption")
    inv = frames.get("product_inventory")
    monthly = frames.get("monthly_consumption")
    if start is None or end is None:
        end = end or date.today()
        start = start or (end - timedelta(days=29))
    days = forecast_days if forecast_days is not None else float(range_day_count(start, end))
    rates = daily_rates(tank, monthly)
    rates_in_range = daily_rates(tank, monthly, start=start, end=end)
    rem = remaining(inventory_with_ledger(db, inv))
    period = period_consumption(tank, start, end)
    crit = critical_materials(rates, rem, CRITICAL_DAYS)
    base = rates_in_range if not rates_in_range.empty else rates
    fc = forecast(base, days)
    sug = suggest_requests(base, rem, days, inventory_df=inv)
    return {
        "start": start,
        "end": end,
        "days": days,
        "critical_days": CRITICAL_DAYS,
        "daily_rates": rates,
        "period_consumption": period,
        "remaining": rem,
        "critical": crit,
        "forecast": fc,
        "suggest": sug,
    }


@dataclass
class ComprehensiveResult:
    title: str = ""
    range_label: str = ""
    pdf: Path | None = None
    xlsx: Path | None = None
    metas: dict = field(default_factory=dict)
    error: str | None = None


def title_for(range_label: str, section: str | None) -> str:
    return f"گزارش جامع — {range_label} — {section_label(section)}"


def generate_files(
    db: Any,
    user: dict,
    start: date,
    end: date,
    *,
    range_label: str,
    section: str | None = None,
    letterhead_path: Path | str | None = None,
    frames: dict | None = None,
    metas: dict | None = None,
) -> ComprehensiveResult:
    from excel.simple_report import generate_analytics_report_xlsx
    from pdf.generator import generate_report

    res = ComprehensiveResult(range_label=f"{range_label} — {section_label(section)}")
    res.title = title_for(range_label, section)
    if frames is None:
        missing = missing_inputs(db, user)
        if missing:
            res.error = "برای گزارش جامع این داده‌ها لازم است:\n• " + "\n• ".join(missing)
            return res
        frames, metas = load(db, user)
    metas = metas if metas is not None else metas_for(frames)
    frames = section_frames(frames, section)
    analytics = build_bundle(db, frames, start=start, end=end)
    res.metas = metas
    res.pdf = generate_report(frames, metas, user, analytics=analytics, letterhead_path=letterhead_path)
    try:
        res.xlsx = generate_analytics_report_xlsx(
            frames, metas, analytics=analytics, output_path=res.pdf.with_suffix(".xlsx"),
            filename_stem="comprehensive",
        )
    except Exception:  # noqa: BLE001
        logger.exception("comprehensive excel companion failed")
        res.xlsx = None
    return res
