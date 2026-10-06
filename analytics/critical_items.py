"""اقلام بحرانی — monthly tundish-count × منبع اصلی rates.

Shared by Bale bot and web panel. Do not duplicate this logic elsewhere.

MODES (نوسازی): the user picks one before generating.
  • «با نوسازی» (RENO_WITH, default / previous behavior): per-tundish material
    for a type = renovation + patching (e.g. billet_renovation + billet_patching).
  • «بدون نوسازی» (RENO_WITHOUT): per-tundish material = patching only; every
    *_renovation column is ignored. Items whose need comes only from renovation
    (e.g. «بتن 86 نوسازی», patching rate 0) get need 0 and are NOT listed.
  The سطح ریخته گری (casting_floor) share is kept in BOTH modes — it is consumed
  by casting on the floor, independent of tundish renovation.
Rates stay separate in منبع اصلی (billet/bloom/slab × renovation/patching).

SPLIT (پیمانکار / شرکت): rows of منبع اصلی are split BEFORE category aggregation
by the material-id rule (``excel.id_parse.is_contractor_material_id``: 2nd group of
four chars == «0000» → پیمانکار, else شرکت) — authoritative since 1405-07-14; the
``contractor_or_company`` cell is only a fallback for ids too short to tell (still
unknown → شرکت). The primary report lists ONLY «شرکت» rows; «پیمانکار» rows form a
separate report/section. کد 1800 (اقلام مازاد) is never a consumable here.

AGGREGATION (per 4-digit کد دسته‌بندی, within one segment):
  • rows with اولویت 0 («بدون اولویت» = unused) are dropped first (blank → 1);
  • NO quantity threshold — rows with موجودی < 100 (or 0) are included;
  • موجودی (per-row column) = SUM of quantity over ALL remaining rows of the
    code, rated or not (e.g. 1450 بتن ملات: rated row 0, siblings hold stock);
  • rates (renovation/patching/casting_floor) and نقطه بحرانی are PER-CODE
    values = MAX over the same rows. The importer copies a merged rate cell into
    every row it spans (excel.processor.fill_merged_cells), so the value must be
    taken ONCE per code, never summed across rows;
  • a code is listed when its active-mode rates give need > 0; alert when
    موجودی ≤ نقطه بحرانی.

SECTION ATTRIBUTION (analytics.section_rules, before the segment split): a
  code with live rows on BOTH شرکت and پیمانکار takes BILLET rates only from
  company rows, BLOOM rates only from contractor rows, SLAB rates from
  contractor rows and from company rows whose محل استفاده contains «اسلب».

SHARED NEED (merged rate crossing several codes, column ``rate_group``):
  config.CRITICAL_SHARED_MERGE_MODE = "pooled" (default): the codes form ONE
  group. Merged columns count ONCE for the group (max); other rate columns are
  summed over member codes. Output = one group row (codes joined by «/»,
  combined stock vs shared need, days of cover, critical flag) followed by one
  row per member code with its OWN stock and «نیاز مشترک گروه» (no own need).
  "per_code": every member code gets the full rate as a normal item.
  Groups never cross segments (segment split happens first).

ASSUMPTION (casting_floor): the سطح ریخته گری rate is treated as per-tundish
material multiplied by (count_billet + count_bloom + count_slab). Refine when
daily casting-floor logs exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from analytics.tundish import (
    NO_PRIORITY,
    _priority_values,
)
from bot.jalali import PERSIAN_MONTH_NAMES, days_in_jalali_month
import config as _config
from analytics.section_rules import apply_section_rate_attribution
from excel.id_parse import is_contractor_material_id

SEGMENT_COMPANY = "company"
SEGMENT_CONTRACTOR = "contractor"
SEGMENTS = (SEGMENT_COMPANY, SEGMENT_CONTRACTOR)
SEGMENT_LABEL_FA = {SEGMENT_COMPANY: "شرکت", SEGMENT_CONTRACTOR: "پیمانکار"}
CONTRACTOR_COLUMN = "contractor_or_company"

RENO_WITH = "with"
RENO_WITHOUT = "without"
RENO_MODES = (RENO_WITH, RENO_WITHOUT)
RENO_LABEL_FA = {RENO_WITH: "با نوسازی", RENO_WITHOUT: "بدون نوسازی"}
RENOVATION_COLS = ("billet_renovation", "bloom_renovation", "slab_renovation")


def normalize_reno_mode(value: object) -> str:
    """Accept with/without, 1/0, true/false or the Persian labels; default RENO_WITH."""
    text = "".join(str(value or "").split()).replace("\u200c", "").lower()
    if text in {"without", "without_renovation", "0", "false", "no", "بدوننوسازی", "بدون"}:
        return RENO_WITHOUT
    return RENO_WITH


def active_rate_cols(reno_mode: str = RENO_WITH) -> tuple[str, ...]:
    """Rate columns that contribute to need in the given mode."""
    if normalize_reno_mode(reno_mode) == RENO_WITHOUT:
        return tuple(c for c in RATE_COLS if c not in RENOVATION_COLS)
    return RATE_COLS


SHARED_POOLED = "pooled"
SHARED_PER_CODE = "per_code"
SHARED_NEED_LABEL = "نیاز مشترک گروه"
ROW_ITEM = "item"
ROW_GROUP = "group"
ROW_MEMBER = "member"
RATE_GROUP_COLUMN = "rate_group"


def shared_merge_mode(value: str | None = None) -> str:
    raw = value if value is not None else getattr(_config, "CRITICAL_SHARED_MERGE_MODE", SHARED_POOLED)
    return SHARED_PER_CODE if str(raw or "").strip().lower() == SHARED_PER_CODE else SHARED_POOLED


RATE_COLS = (
    "billet_renovation",
    "billet_patching",
    "bloom_renovation",
    "bloom_patching",
    "slab_renovation",
    "slab_patching",
    "casting_floor",
)

REPORT_COLUMNS = [
    "کد چهاررقمی",
    "ردیف",
    "کد و شرح کالا",
    "موجودی",
    "واحد",
    "نیاز",
    "حد تحمل(روز)",
]


@dataclass(frozen=True)
class TundishMonthCounts:
    jalali_year: int
    jalali_month: int
    count_billet: int
    count_bloom: int
    count_slab: int

    @property
    def total(self) -> int:
        return int(self.count_billet) + int(self.count_bloom) + int(self.count_slab)

    def month_label(self) -> str:
        name = PERSIAN_MONTH_NAMES.get(int(self.jalali_month), str(self.jalali_month))
        return f"{name} {int(self.jalali_year)}"


def _norm_fa(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.strip().lower() in {"", "nan", "none"}:
        return ""
    text = (
        text.replace("ي", "ی")
        .replace("ى", "ی")
        .replace("ك", "ک")
        .replace("\u200c", "")
        .replace("\u200f", "")
        .replace("\u200e", "")
    )
    return "".join(text.split()).lower()


_CONTRACTOR_TOKENS = ("پیمانکار", "contractor")
_COMPANY_TOKENS = ("شرکت", "company")


def classify_contractor_or_company(value: object, item_id: object = None) -> str:
    """Map a row to SEGMENT_COMPANY/CONTRACTOR.

    شناسه مواد is authoritative (chars 5–8 == «0000» → پیمانکار, else شرکت); the
    «پیمانکار / شرکت» cell is only a fallback when the id is too short / blank.
    """
    flag = is_contractor_material_id(item_id)
    if flag is not None:
        return SEGMENT_CONTRACTOR if flag else SEGMENT_COMPANY
    text = _norm_fa(value)
    if text:
        if any(tok in text for tok in _CONTRACTOR_TOKENS):
            return SEGMENT_CONTRACTOR
        if any(tok in text for tok in _COMPANY_TOKENS):
            return SEGMENT_COMPANY
    flag = is_contractor_material_id(item_id)
    if flag is True:
        return SEGMENT_CONTRACTOR
    return SEGMENT_COMPANY


def segment_series(inventory_df: pd.DataFrame) -> pd.Series:
    """Per-row segment (company / contractor) for an inventory frame."""
    if inventory_df is None or inventory_df.empty:
        return pd.Series(dtype=object)
    vals = (
        inventory_df[CONTRACTOR_COLUMN]
        if CONTRACTOR_COLUMN in inventory_df.columns
        else pd.Series([None] * len(inventory_df), index=inventory_df.index)
    )
    ids = (
        inventory_df["id"]
        if "id" in inventory_df.columns
        else pd.Series([None] * len(inventory_df), index=inventory_df.index)
    )
    return pd.Series(
        [classify_contractor_or_company(v, i) for v, i in zip(vals, ids)],
        index=inventory_df.index,
        dtype=object,
    )


def filter_inventory_segment(
    inventory_df: pd.DataFrame | None, segment: str | None
) -> pd.DataFrame | None:
    """Rows of one segment; ``segment=None`` returns the frame unchanged."""
    if inventory_df is None or segment is None or inventory_df.empty:
        return inventory_df
    if segment not in SEGMENTS:
        raise ValueError(f"unknown segment: {segment}")
    seg = segment_series(inventory_df)
    return inventory_df.loc[seg == segment].copy()


def contractor_column_summary(inventory_df: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Distinct raw values of «پیمانکار / شرکت» with row counts and mapped segment."""
    if inventory_df is None or inventory_df.empty:
        return []
    raw = (
        inventory_df[CONTRACTOR_COLUMN]
        if CONTRACTOR_COLUMN in inventory_df.columns
        else pd.Series([""] * len(inventory_df), index=inventory_df.index)
    )
    seg = segment_series(inventory_df)
    work = pd.DataFrame({"value": raw.map(lambda v: "" if _norm_fa(v) == "" else str(v).strip()), "segment": seg})
    out = []
    for (value, segment), grp in work.groupby(["value", "segment"], sort=True):
        out.append({"value": value, "segment": segment, "rows": int(len(grp))})
    return out


def _num_series(work: pd.DataFrame, col: str) -> pd.Series:
    if col not in work.columns:
        return pd.Series(0.0, index=work.index, dtype=float)
    return pd.to_numeric(work[col], errors="coerce").fillna(0.0)


def _short_desc(text: object, limit: int = 80) -> str:
    s = str(text or "").strip()
    s = " ".join(s.split())
    if len(s) <= limit:
        return s
    return s[: max(1, limit - 1)].rstrip() + "…"


def _pick_description(
    group: pd.DataFrame, rate_cols: tuple[str, ...] | None = None
) -> tuple[str, str]:
    """Return (description, unit) from best keyword / product_name row."""
    work = group.copy()
    prio = (
        _priority_values(work["priority"])
        if "priority" in work.columns
        else pd.Series(1.0, index=work.index)
    )
    work["_prio"] = prio
    rate_sum = sum(_num_series(work, c) for c in (rate_cols or RATE_COLS))
    work["_has_rate"] = rate_sum > 0
    candidates = work.loc[work["_has_rate"]].copy()
    if candidates.empty:
        candidates = work
    # Prefer priority ≥ 1; among those, lowest priority number wins; else any.
    live = candidates.loc[candidates["_prio"] != float(NO_PRIORITY)]
    pool = live if not live.empty else candidates
    pool = pool.sort_values(by=["_prio"], kind="stable")
    row = pool.iloc[0]
    keyword = str(row.get("keyword") or "").strip()
    product = str(row.get("product_name") or "").strip()
    desc = keyword if keyword else _short_desc(product)
    if not desc:
        desc = _short_desc(product) or "—"
    unit = str(row.get("unit") or "").strip()
    return desc, unit


def drop_priority_zero_rows(inventory_df: pd.DataFrame) -> pd.DataFrame:
    """Rows whose اولویت ≠ 0 (blank counts as 1). No quantity filter."""
    if inventory_df is None or inventory_df.empty or "priority" not in inventory_df.columns:
        return inventory_df
    prio = _priority_values(inventory_df["priority"])
    return inventory_df.loc[prio != float(NO_PRIORITY)].copy()


def drop_surplus_rows(inventory_df: pd.DataFrame) -> pd.DataFrame:
    """Drop کد دسته 1800 (اقلام مازاد) — never a consumable for critical items / main goal."""
    from config import SURPLUS_CATEGORY_CODE

    if inventory_df is None or inventory_df.empty or "category_code" not in inventory_df.columns:
        return inventory_df
    codes = inventory_df["category_code"].map(
        lambda v: str(v).strip()[:-2] if str(v).strip().endswith(".0") else str(v).strip()
    )
    return inventory_df.loc[codes != SURPLUS_CATEGORY_CODE].copy()


def _real_stock(group: pd.DataFrame) -> float:
    return float(_num_series(group, "quantity").sum())


def _critical_point_value(group: pd.DataFrame) -> float | None:
    if "critical_point" not in group.columns:
        return None
    vals = pd.to_numeric(group["critical_point"], errors="coerce").dropna()
    if vals.empty:
        return None
    return float(vals.max())


def monthly_need_for_rates(
    *,
    billet_renovation: float,
    billet_patching: float,
    bloom_renovation: float,
    bloom_patching: float,
    slab_renovation: float,
    slab_patching: float,
    casting_floor: float,
    counts: TundishMonthCounts,
    reno_mode: str = RENO_WITH,
) -> float:
    """Monthly need from per-tundish rates × counts (+ casting_floor rule).

    «با نوسازی»: per tundish = renovation + patching. «بدون نوسازی»: patching
    only. casting_floor is applied in both modes.
    """
    reno = 1.0 if normalize_reno_mode(reno_mode) == RENO_WITH else 0.0
    per_billet = reno * float(billet_renovation) + float(billet_patching)
    per_bloom = reno * float(bloom_renovation) + float(bloom_patching)
    per_slab = reno * float(slab_renovation) + float(slab_patching)
    need = (
        float(counts.count_billet) * per_billet
        + float(counts.count_bloom) * per_bloom
        + float(counts.count_slab) * per_slab
    )
    # ASSUMPTION: casting_floor × total tundish count for the month.
    cf = float(casting_floor)
    if cf > 0 and counts.total > 0:
        need += cf * float(counts.total)
    elif cf > 0 and counts.total == 0:
        # No tundishes entered — still surface the raw casting-floor rate as need.
        need += cf
    return need


def build_critical_items_rows(
    inventory_df: pd.DataFrame | None,
    counts: TundishMonthCounts,
    *,
    days_in_month: int | None = None,
    segment: str | None = None,
    reno_mode: str = RENO_WITH,
    shared_mode: str | None = None,
) -> pd.DataFrame:
    """One row per category_code with any renovation/patching/casting_floor > 0.

    ``segment`` = SEGMENT_COMPANY / SEGMENT_CONTRACTOR restricts the input rows
    (stock, rates, critical-point totals) to that «پیمانکار / شرکت» side before
    aggregation. None keeps every row (legacy / tests).

    ``reno_mode`` = RENO_WITH (renovation + patching) or RENO_WITHOUT (patching
    only; renovation-only categories drop out). casting_floor counts in both.

    Columns match the sample: کد چهاررقمی | ردیف | کد و شرح کالا | موجودی |
    واحد | نیاز | حد تحمل(روز). Extra columns (critical_point, filtered_stock,
    below_threshold) help callers highlight without changing the printed set.
    """
    empty_cols = REPORT_COLUMNS + [
        "critical_point",
        "filtered_stock",
        "below_threshold",
        "daily_need",
        "row_kind",
        "group_codes",
    ]
    if inventory_df is None or inventory_df.empty or "category_code" not in inventory_df.columns:
        return pd.DataFrame(columns=empty_cols)
    # Section attribution on the FULL frame (needs both segments): codes with
    # شرکت + پیمانکار rows → billet from company, bloom from contractor, slab
    # from contractor + company rows located «اسلب» (analytics.section_rules).
    # کد 1800 = اقلام مازاد → never a consumable (rule B, 1405-07-14).
    inventory_df = drop_surplus_rows(inventory_df)
    inventory_df, _changes = apply_section_rate_attribution(inventory_df)
    inventory_df = filter_inventory_segment(inventory_df, segment)
    if inventory_df is None or inventory_df.empty:
        return pd.DataFrame(columns=empty_cols)
    # Shared-need groups come from the merge structure (all rows of the segment).
    group_source = inventory_df
    # اولویت 0 = unused → excluded from stock, rates and critical point.
    # (No quantity threshold: rows with موجودی < 100 are kept.)
    inventory_df = drop_priority_zero_rows(inventory_df)
    if inventory_df.empty:
        return pd.DataFrame(columns=empty_cols)

    work = inventory_df.copy()
    work["category_code"] = work["category_code"].map(
        lambda v: str(v).strip() if v is not None and str(v).strip() not in {"", "nan", "None"} else ""
    )
    # Normalize 4-digit-ish codes (drop trailing .0)
    work["category_code"] = work["category_code"].map(
        lambda s: s[:-2] if isinstance(s, str) and s.endswith(".0") and s[:-2].isdigit() else s
    )
    work = work.loc[work["category_code"] != ""].copy()
    if work.empty:
        return pd.DataFrame(columns=empty_cols)

    for c in RATE_COLS:
        work[c] = _num_series(work, c)

    reno_mode = normalize_reno_mode(reno_mode)
    use_cols = active_rate_cols(reno_mode)
    days = int(days_in_month) if days_in_month is not None else days_in_jalali_month(
        counts.jalali_year, counts.jalali_month
    )
    days = max(1, days)

    per_code: dict[str, dict[str, Any]] = {}
    for code, group in work.groupby("category_code", sort=True):
        per_code[str(code)] = {
            "rates": {c: float(_num_series(group, c).max()) for c in RATE_COLS},
            "cp": _critical_point_value(group),
            "stock": _real_stock(group),
            "group": group,
        }

    groups = (
        shared_need_groups(group_source, set(per_code))
        if shared_merge_mode(shared_mode) == SHARED_POOLED
        else []
    )
    grouped_codes = {code for g in groups for code in g["codes"]}

    def _metrics(rates: dict[str, float], stock: float, cp: float | None) -> dict[str, Any] | None:
        if sum(rates[c] for c in use_cols) <= 0 or sum(rates.values()) <= 0:
            return None
        need = monthly_need_for_rates(counts=counts, reno_mode=reno_mode, **rates)
        if need <= 0:
            return None
        daily = need / float(days)
        days_cover = stock / daily if daily > 0 else 0.0
        return {
            "need": need,
            "daily": daily,
            "days_cover": days_cover,
            "below": bool(cp is not None and stock <= float(cp)),
        }

    def _row(code: str, desc: str, unit: str, stock: float, m: dict[str, Any], cp, kind: str) -> dict[str, Any]:
        return {
            "کد چهاررقمی": code,
            "ردیف": 0,
            "کد و شرح کالا": desc,
            "موجودی": stock,
            "واحد": unit,
            "نیاز": round(m["need"], 3) if abs(m["need"] - round(m["need"])) > 1e-9 else int(round(m["need"])),
            "حد تحمل(روز)": int(round(m["days_cover"])) if m["days_cover"] > 0 else 0,
            "critical_point": cp,
            "filtered_stock": stock,
            "below_threshold": m["below"],
            "daily_need": m["daily"],
            "row_kind": kind,
            "group_codes": "",
        }

    blocks: list[tuple[tuple, list[dict[str, Any]]]] = []
    for code, info in per_code.items():
        if code in grouped_codes:
            continue
        m = _metrics(info["rates"], info["stock"], info["cp"])
        if m is None:
            continue
        desc, unit = _pick_description(info["group"], use_cols)
        row = _row(code, desc, unit, info["stock"], m, info["cp"], ROW_ITEM)
        blocks.append(((not m["below"], row["حد تحمل(روز)"], code), [row]))

    for g in groups:
        members = list(g["codes"])
        merged_cols = set(g["columns"])
        rates = {}
        for c in RATE_COLS:
            vals = [per_code[k]["rates"][c] for k in members]
            rates[c] = max(vals) if c in merged_cols else sum(vals)
        cps = [per_code[k]["cp"] for k in members if per_code[k]["cp"] is not None]
        if not cps:
            cp = None
        elif "critical_point" in merged_cols:
            cp = max(cps)
        else:
            cp = float(sum(cps))
        stock = float(sum(per_code[k]["stock"] for k in members))
        m = _metrics(rates, stock, cp)
        if m is None:
            continue
        descs = []
        unit = ""
        for k in members:
            d, u = _pick_description(per_code[k]["group"], use_cols)
            descs.append(d)
            unit = unit or u
        label_codes = "/".join(members)
        head = _row(label_codes, _group_label(descs), unit, stock, m, cp, ROW_GROUP)
        head["group_codes"] = label_codes
        rows_block = [head]
        for k, d in zip(members, descs):
            _d, u = _pick_description(per_code[k]["group"], use_cols)
            rows_block.append(
                {
                    "کد چهاررقمی": k,
                    "ردیف": "",
                    "کد و شرح کالا": d,
                    "موجودی": per_code[k]["stock"],
                    "واحد": u or unit,
                    "نیاز": SHARED_NEED_LABEL,
                    "حد تحمل(روز)": "—",
                    "critical_point": None,
                    "filtered_stock": per_code[k]["stock"],
                    "below_threshold": None,
                    "daily_need": None,
                    "row_kind": ROW_MEMBER,
                    "group_codes": label_codes,
                }
            )
        blocks.append(((not m["below"], head["حد تحمل(روز)"], members[0]), rows_block))

    if not blocks:
        return pd.DataFrame(columns=empty_cols)
    blocks.sort(key=lambda b: b[0])
    rows: list[dict[str, Any]] = []
    n = 0
    for _key, block in blocks:
        for row in block:
            if row["row_kind"] != ROW_MEMBER:
                n += 1
                row["ردیف"] = n
            rows.append(row)
    out = pd.DataFrame(rows)
    for col in ("موجودی", "نیاز"):
        out[col] = out[col].map(_pretty_num)
    return out


def critical_item_count(df: pd.DataFrame | None) -> int:
    """Listed items: plain codes + shared-need groups (member rows excluded)."""
    if df is None or df.empty:
        return 0
    if "row_kind" not in df.columns:
        return int(len(df))
    return int((df["row_kind"] != ROW_MEMBER).sum())


def group_row_indices(df: pd.DataFrame | None) -> list[int]:
    """0-based positions of shared-need group subtotal rows (for emphasis)."""
    if df is None or df.empty or "row_kind" not in df.columns:
        return []
    return [i for i, k in enumerate(df["row_kind"].tolist()) if k == ROW_GROUP]


def _group_label(descs: list[str]) -> str:
    """Common leading words of member descriptions (e.g. «نازل»), else first."""
    words = [str(d or "").split() for d in descs if str(d or "").strip()]
    if not words:
        return "گروه مشترک"
    common: list[str] = []
    for parts in zip(*words):
        if all(p == parts[0] for p in parts):
            common.append(parts[0])
        else:
            break
    base = " ".join(common) if common else words[0][0]
    return f"{base} (گروه با نیاز مشترک)"


def _parse_rate_group(value: object) -> tuple[str, tuple[str, ...]] | None:
    text = "" if value is None else str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    gid, _, cols = text.partition(":")
    return gid.strip(), tuple(c.strip() for c in cols.split(",") if c.strip())


def shared_need_groups(
    frame: pd.DataFrame | None, present_codes: set[str] | None = None
) -> list[dict[str, Any]]:
    """Code groups linked by ``rate_group`` (merged rate crossing codes).

    ``frame`` must already be segment-filtered (groups never cross segments).
    Codes sharing any rate_group id are unioned. Only codes in
    ``present_codes`` (priority ≠ 0 rows exist) are kept; groups with < 2
    such codes are dropped (the code is then a normal item).
    """
    if frame is None or frame.empty or RATE_GROUP_COLUMN not in frame.columns:
        return []
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    cols_by_root: dict[str, set[str]] = {}
    for _, row in frame.iterrows():
        parsed = _parse_rate_group(row.get(RATE_GROUP_COLUMN))
        code = _norm_code(row.get("category_code"))
        if parsed is None or not code:
            continue
        gid, cols = parsed
        a, b = find("g:" + gid), find("c:" + code)
        if a != b:
            parent[b] = a
        cols_by_root.setdefault("g:" + gid, set()).update(cols)
    comps: dict[str, dict[str, Any]] = {}
    for node in list(parent):
        root = find(node)
        comp = comps.setdefault(root, {"codes": set(), "columns": set()})
        if node.startswith("c:"):
            comp["codes"].add(node[2:])
        else:
            comp["columns"].update(cols_by_root.get(node, set()))
    out = []
    for comp in comps.values():
        codes = sorted(
            c for c in comp["codes"] if present_codes is None or c in present_codes
        )
        if len(codes) >= 2:
            out.append({"codes": codes, "columns": sorted(comp["columns"])})
    return sorted(out, key=lambda g: g["codes"])


def _norm_code(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if text in {"nan", "None"}:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def _pretty_num(val: object) -> int | float:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return val  # type: ignore[return-value]
    if abs(num - round(num)) < 1e-9:
        return int(round(num))
    return round(num, 3)


def report_title(
    counts: TundishMonthCounts,
    segment: str | None = None,
    reno_mode: str | None = None,
) -> str:
    seg = f" — {SEGMENT_LABEL_FA[segment]}" if segment in SEGMENT_LABEL_FA else ""
    mode = (
        f" — {RENO_LABEL_FA[normalize_reno_mode(reno_mode)]}" if reno_mode is not None else ""
    )
    return f"لیست اقلام بحرانی نسوز تاندیش{seg}{mode} ({counts.month_label()})"


def report_subtitle(
    counts: TundishMonthCounts,
    *,
    row_count: int,
    segment: str | None = None,
    reno_mode: str | None = None,
) -> str:
    seg = f"اقلام {SEGMENT_LABEL_FA[segment]}: " if segment in SEGMENT_LABEL_FA else ""
    mode = (
        f"حالت: {RENO_LABEL_FA[normalize_reno_mode(reno_mode)]} | "
        if reno_mode is not None
        else ""
    )
    return (
        f"{mode}{seg}{row_count} قلم | تعداد تاندیش — بیلت: {counts.count_billet}، "
        f"بلوم: {counts.count_bloom}، اسلب: {counts.count_slab}"
    )


def reno_mode_note(reno_mode: str | None = None) -> str:
    if normalize_reno_mode(reno_mode) == RENO_WITHOUT:
        return (
            "حالت محاسبه: «بدون نوسازی» — مصرف هر تاندیش فقط نرخ پچینگ "
            "(بیلت/بلوم/اسلب) است و ستون‌های نوسازی نادیده گرفته می‌شوند؛ اقلامی که "
            "نیازشان فقط از نوسازی است (نرخ پچینگ صفر، مثل «بتن 86 نوسازی») فهرست نمی‌شوند."
        )
    return (
        "حالت محاسبه: «با نوسازی» — برای هر نوع تاندیش، مصرف هر تاندیش = "
        "نوسازی + پچینگ (تا وقتی لاگ روزانه نوسازی/پچینگ جدا شود)."
    )


def report_footer_notes(
    counts: TundishMonthCounts, reno_mode: str | None = None
) -> list[str]:
    """توضیحات footer matching the sample sheet style."""
    return [
        (
            f"جهت ریخته‌گری بیلت تعداد {counts.count_billet} تاندیش، "
            f"بلوم {counts.count_bloom} و اسلب {counts.count_slab} "
            f"در نظر گرفته شده است."
        ),
        reno_mode_note(reno_mode),
        (
            "فرض سطح ریخته‌گری: نرخ ستون × مجموع تعداد تاندیش‌های ماه "
            "(در هر دو حالت با/بدون نوسازی منظور می‌شود)."
        ),
        (
            f"حد تحمل (روز) = موجودی واقعی ÷ (نیاز ماهانه ÷ "
            f"{days_in_jalali_month(counts.jalali_year, counts.jalali_month)} روز)."
        ),
        (
            "تجمیع بر اساس کد ۴ رقمی (در همان بخش شرکت/پیمانکار، فقط ردیف‌های با "
            "اولویت غیر صفر — اولویت ۰ یعنی استفاده نمی‌شود): موجودی = جمع موجودی همه "
            "ردیف‌های کد، با نرخ یا بدون نرخ و بدون هیچ حد حداقل (مثل زیر ۱۰۰)؛ نرخ‌های "
            "مصرف، سطح ریخته‌گری و نقطه بحرانی مقدار واحدِ هر کد هستند (بیشینه ردیف‌ها، "
            "یک بار) و بین ردیف‌ها جمع نمی‌شوند."
        ),
        (
            "خانه‌های ادغام‌شده (Merge) در فایل منبع اصلی: مقدار به همه ردیف‌های زیر "
            "آن تعلق دارد و در ورود اطلاعات به همه آن ردیف‌ها کپی می‌شود؛ فقط موجودی "
            "(مقدار هر ردیف) کپی نمی‌شود."
        ),
        shared_need_note(),
        (
            "تخصیص بخش برای کدی که هم ردیف «شرکت» و هم «پیمانکار» (با اولویت غیر صفر) "
            "دارد: مصرف بیلت فقط از ردیف‌های شرکت، مصرف بلوم فقط از ردیف‌های پیمانکار، "
            "و مصرف اسلب از ردیف‌های پیمانکار و ردیف‌های شرکت با محل استفاده «اسلب» "
            "حساب می‌شود (حتی اگر خانه ادغام‌شده نرخ را به ردیف بخش دیگر کپی کرده باشد)."
        ),
        (
            "نقطه بحرانی: هشدار وقتی همین جمع موجودی کد به حد نقطه بحرانی دسته برسد."
        ),
        (
            "تفکیک شرکت/پیمانکار از روی شناسه مواد: اگر رقم‌های ۵ تا ۸ شناسه «0000» "
            "باشد پیمانکار، وگرنه شرکت. گزارش اصلی فقط اقلام «شرکت» است و اقلام "
            "«پیمانکار» در گزارش/بخش جداگانه می‌آید؛ موجودی، نرخ و نقطه بحرانی هر بخش "
            "فقط از ردیف‌های همان بخش محاسبه می‌شود. اقلام کد ۱۸۰۰ (مازاد) مصرفی حساب نمی‌شوند."
        ),
    ]


def shared_need_note(mode: str | None = None) -> str:
    if shared_merge_mode(mode) == SHARED_PER_CODE:
        return (
            "نرخ ادغام‌شده روی چند کد ۴ رقمی (مثل نازل‌ها): هر کد نرخ کامل را "
            "جداگانه می‌گیرد (حالت per_code)."
        )
    return (
        "نیاز مشترک گروه: وقتی یک نرخ مصرف در فایل روی چند کد ۴ رقمی ادغام شده "
        "(مثل نازل‌های بیلت در اندازه‌های مختلف)، آن نرخ یک نیاز مشترک برای کل گروه "
        "است و فقط یک بار حساب می‌شود. ردیف گروه (کدها با «/») جمع موجودی کدها را "
        "با نیاز مشترک مقایسه می‌کند و حد تحمل و وضعیت بحرانی از همین ردیف است؛ "
        "زیر آن، هر کد با موجودی خودش و عبارت «نیاز مشترک گروه» می‌آید."
    )


def rows_for_simple_report(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Dict rows with only the sample print columns."""
    if df is None or df.empty:
        return []
    work = df.copy()
    for c in REPORT_COLUMNS:
        if c not in work.columns:
            work[c] = None
    return work[REPORT_COLUMNS].to_dict(orient="records")
