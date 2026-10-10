"""Daily site stock (موجودی روزانه سایت) input lists, generated from منبع اصلی.

Shared by the bot («موجودی روزانه» → اسلب / بلوم / بیلت) and the web ``/stock`` form.

Rule (user, 1405-07-18): each list = the live منبع اصلی rows of that section, one
input line per (4-digit code, «کلید واژه») — the item NAME shown is the keyword
column (forward-filled inside a code for merged cells; falls back to «شرح کالا» when
empty). Nozzle codes 1710–1718 thus give one line per code/size (نازل 16.5 … نازل 19).

Row eligibility (never modifies منبع اصلی):
  * اولویت ≠ 0 (priority 0 = unused) and code ≠ 1800 (مازاد; daily stock has no
    surplus section);
  * section by «محل استفاده» (contains بیلت / بلوم / اسلب; «بلوم / بیلت» → both); a
    row with no section word falls back to its section rate columns (> 0);
  * «سطح ریخته گری …» (casting floor, e.g. شرود 1581) is NOT a tundish item: it goes
    to the separate groups «موجودی سطح ریخته‌گری اسلب/بلوم/بیلت» (cast_*), never
    to the slab/bloom/billet lists (user, 1405-07-18);
  * the shared شرکت / پیمانکار rule of :mod:`analytics.section_rules` for codes with
    BOTH sides: billet = company rows, bloom = contractor rows, slab = contractor rows
    + company rows whose محل استفاده has «اسلب».

Lines are stored as catalog items with synthetic ids ``SS:<section>:<code>:<hash>``
assigned with ``assigned_by = system:main_source_keyword`` (one row per section,
so a nozzle code can be in both bloom and billet). They are kept OUT of the
warehouse catalog listings (material requests etc.). :func:`ensure_site_stock_lists`
re-syncs whenever the generated lists change (called on every list open / save and
after a منبع اصلی upload), so the lists follow the live source automatically.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from config import (
    SITE_STOCK_GROUP_KEYS,
    SITE_STOCK_GROUPS,
    SURPLUS_CATEGORY_CODE,
)

logger = logging.getLogger(__name__)

SITE_LINE_PREFIX = "SS:"
SITE_LINE_ASSIGNED_BY = "system:main_source_keyword"
SIG_SETTING_KEY = "site_stock_lists_signature"
TUNDISH_SECTIONS = ("slab", "bloom", "billet")
# Tundish sections first, then the سطح ریخته‌گری (casting floor) groups.
SECTION_ORDER = (*TUNDISH_SECTIONS, "cast_slab", "cast_bloom", "cast_billet")
SECTION_TOKENS = {"slab": "اسلب", "bloom": "بلوم", "billet": "بیلت"}
CASTING_FLOOR_TOKEN = "سطح ریخته گری"  # «محل استفاده» phrase (ZWNJ → space)
SECTION_RATE_COLS = {
    "billet": ("billet_renovation", "billet_patching"),
    "bloom": ("bloom_renovation", "bloom_patching"),
    "slab": ("slab_renovation", "slab_patching"),
}


def _txt(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).replace("ي", "ی").replace("ك", "ک").strip()
    if s.lower() in {"nan", "none"}:
        return ""
    return " ".join(s.split())


def _code(v: object) -> str:
    s = _txt(v)
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    if s.isdigit() and len(s) < 4:
        s = s.zfill(4)
    return s


def _num(v: object) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if pd.isna(f) else f


def is_site_line_id(item_id: object) -> bool:
    return str(item_id or "").startswith(SITE_LINE_PREFIX)


def line_id(section: str, code: str, keyword: str) -> str:
    h = hashlib.sha1(keyword.encode("utf-8")).hexdigest()[:8]
    return f"{SITE_LINE_PREFIX}{section}:{code or '----'}:{h}"


@dataclass
class SiteStockLists:
    lines: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    fallbacks: list[dict[str, Any]] = field(default_factory=list)   # keyword empty → شرح کالا
    duplicates: list[dict[str, Any]] = field(default_factory=list)  # same keyword, ≠ codes
    unplaced: list[dict[str, Any]] = field(default_factory=list)    # no section found
    rate_fallback: list[dict[str, Any]] = field(default_factory=list)  # section from rates

    def counts(self) -> dict[str, int]:
        return {g: len(self.lines.get(g, [])) for g in SECTION_ORDER}

    def signature(self) -> str:
        payload = {
            g: [(ln["id"], ln["name_desc"], ln["category_code"]) for ln in self.lines.get(g, [])]
            for g in SECTION_ORDER
        }
        return hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()


def _keyword_series(df: pd.DataFrame) -> pd.Series:
    """«کلید واژه» forward-filled within the same 4-digit code (merged cells)."""
    codes = df["category_code"].map(_code) if "category_code" in df.columns else pd.Series("", index=df.index)
    raw = df["keyword"].map(_txt) if "keyword" in df.columns else pd.Series("", index=df.index)
    out: list[str] = []
    prev_code, prev_kw = None, ""
    for code, kw in zip(codes, raw):
        if kw:
            prev_kw = kw
        elif code and code == prev_code and prev_kw:
            kw = prev_kw
        else:
            prev_kw = ""
        prev_code = code
        out.append(kw)
    return pd.Series(out, index=df.index, dtype=object)


def location_groups(location: object) -> list[str]:
    """Daily-stock groups for one «محل استفاده» value.

    Parts are split on «،» / «,» / «+». A part with «سطح ریخته گری» is a casting-floor
    part → ``cast_<section>`` for every section word in it («سطح ریخته گری اسلب/بلوم»
    → cast_slab + cast_bloom); it NEVER feeds the tundish list (شرود 1581 is a
    casting-floor item, not a slab-tundish item). Other parts → tundish sections
    by word («بلوم / بیلت» → bloom + billet, «بلوم/اسلب» → bloom + slab).
    """
    text = _txt(location).replace("\u200c", " ")
    text = " ".join(text.split())
    out: list[str] = []
    for part in re.split(r"[،,+]", text):
        part = part.strip()
        if not part:
            continue
        secs = [g for g in TUNDISH_SECTIONS if SECTION_TOKENS[g] in part]
        if CASTING_FLOOR_TOKEN in part:
            groups = [f"cast_{g}" for g in (secs or ["slab"])]
        else:
            groups = secs
        for g in groups:
            if g not in out:
                out.append(g)
    return sorted(out, key=SECTION_ORDER.index)


def build_site_stock_lists(df: pd.DataFrame | None) -> SiteStockLists:
    """Generate the three daily-stock input lists from a منبع اصلی frame (read-only)."""
    from analytics.section_rules import section_row_mask
    from analytics.tundish import NO_PRIORITY, _priority_values

    res = SiteStockLists(lines={g: [] for g in SECTION_ORDER})
    if df is None or df.empty or "id" not in df.columns:
        return res
    work = df.reset_index(drop=True).copy()
    work["_code"] = work["category_code"].map(_code) if "category_code" in work.columns else ""
    work["_kw"] = _keyword_series(work)
    names = work["product_name"].map(_txt) if "product_name" in work.columns else pd.Series("", index=work.index)
    live = (
        _priority_values(work["priority"]) != float(NO_PRIORITY)
        if "priority" in work.columns
        else pd.Series(True, index=work.index)
    )
    eligible = live & (work["_code"] != SURPLUS_CATEGORY_CODE) & work["id"].map(lambda v: bool(_txt(v)))
    masks = {g: section_row_mask(work, g) for g in TUNDISH_SECTIONS}
    loc = work["usage_location"].map(_txt) if "usage_location" in work.columns else pd.Series("", index=work.index)

    groups: dict[str, dict[tuple[str, str], dict[str, Any]]] = {g: {} for g in SECTION_ORDER}
    for i in work.index[eligible]:
        iid = _txt(work.at[i, "id"])
        code = work.at[i, "_code"]
        kw = work.at[i, "_kw"]
        if not kw:
            kw = names.at[i] or iid
            res.fallbacks.append({"id": iid, "category_code": code, "name": kw})
        sections = location_groups(loc.at[i])
        if not sections:
            sections = [
                g for g in TUNDISH_SECTIONS
                if any(_num(work.at[i, c]) > 0 for c in SECTION_RATE_COLS[g] if c in work.columns)
            ]
            if sections:
                res.rate_fallback.append({"id": iid, "category_code": code, "keyword": kw, "sections": sections})
        if not sections:
            res.unplaced.append({"id": iid, "category_code": code, "keyword": kw, "usage_location": loc.at[i]})
            continue
        for g in sections:
            if g in masks and not bool(masks[g].iat[i]):
                continue  # شرکت/پیمانکار rule (codes with both sides; not casting floor)
            key = (code, kw)
            line = groups[g].get(key)
            if line is None:
                line = groups[g][key] = {
                    "id": line_id(g, code, kw),
                    "name_desc": kw,
                    "category_code": code or None,
                    "keyword": kw,
                    "member_ids": [],
                }
            line["member_ids"].append(iid)

    for g in SECTION_ORDER:
        lines = sorted(groups[g].values(), key=lambda ln: (ln["category_code"] or "", ln["keyword"]))
        by_kw: dict[str, list[dict[str, Any]]] = {}
        for ln in lines:
            by_kw.setdefault(ln["keyword"], []).append(ln)
        for kw, same in by_kw.items():
            if len(same) > 1:  # same keyword on different codes → disambiguate by code
                res.duplicates.append({
                    "section": g, "keyword": kw,
                    "codes": [ln["category_code"] for ln in same],
                })
                for ln in same:
                    ln["name_desc"] = f"{kw} (کد {ln['category_code']})"
        res.lines[g] = lines
    return res


def report_text_fa(res: SiteStockLists) -> str:
    parts = [
        "فهرست‌های موجودی روزانه (از منبع اصلی، نام = کلید واژه): "
        + "، ".join(f"{SITE_STOCK_GROUPS[g]}: {n}" for g, n in res.counts().items())
    ]
    if res.fallbacks:
        parts.append(f"کلید واژه خالی ({len(res.fallbacks)}) → شرح کالا")
    if res.duplicates:
        parts.append(f"کلید واژه تکراری ({len(res.duplicates)}) → با کد جدا شد")
    if res.unplaced:
        parts.append(f"بدون بخش ({len(res.unplaced)})")
    return " | ".join(parts)


def sync_site_stock_lists(db, res: SiteStockLists, *, force: bool = False) -> dict[str, Any]:
    """Write generated lines into catalog_items / catalog_group_assignments.

    Stale generated lines are unassigned + deactivated. WO auto / manual per-id
    assignments are left as they are (they no longer show in the site lists while
    generated lines exist, except manual ones — see :func:`items_for_group`).
    """
    sig = res.signature()
    if not force and db.get_setting(SIG_SETTING_KEY) == sig:
        return {"changed": False, "signature": sig, "counts": res.counts()}
    wanted: set[str] = set()
    for g in SECTION_ORDER:
        for ln in res.lines.get(g, []):
            wanted.add(ln["id"])
            db.upsert_catalog_item(ln["id"], ln["name_desc"], category_code=ln["category_code"], active=True)
            db.assign_item_to_group(ln["id"], g, assigned_by=SITE_LINE_ASSIGNED_BY)
    removed = 0
    with db.connect() as conn:
        stale = [
            r[0] for r in conn.execute(
                "SELECT id FROM catalog_items WHERE id LIKE ? AND active = 1",
                (SITE_LINE_PREFIX + "%",),
            ).fetchall()
            if r[0] not in wanted
        ]
    for iid in stale:
        db.unassign_item(iid)
        db.deactivate_catalog_item(iid)
        removed += 1
    db.set_setting(SIG_SETTING_KEY, sig, updated_by="system")
    return {"changed": True, "signature": sig, "counts": res.counts(), "removed": removed}


def ensure_site_stock_lists(db, *, force: bool = False) -> SiteStockLists | None:
    """Rebuild from the live منبع اصلی and sync if changed. None when no source."""
    from services.main_source import load_primary_frame

    try:
        df = load_primary_frame(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("site stock lists: منبع اصلی load failed: %s", exc)
        return None
    if df is None or df.empty:
        return None
    res = build_site_stock_lists(df)
    if not any(res.lines.values()):
        return None
    try:
        sync_site_stock_lists(db, res, force=force)
    except Exception:  # noqa: BLE001
        logger.exception("site stock lists sync failed")
        return None
    return res


def items_for_group(db, group: str) -> list[dict[str, Any]]:
    """Input list for one section (bot + web). Generated lines (+ manual per-id
    assignments by a user); legacy assignments only when no منبع اصلی exists."""
    g = (group or "").strip().lower()
    if g not in SITE_STOCK_GROUP_KEYS:
        return []
    res = ensure_site_stock_lists(db)
    items = db.list_items_for_group(g, active_only=True)
    if res is None:
        return items
    order = {ln["id"]: n for n, ln in enumerate(res.lines.get(g, []))}
    generated = [it for it in items if it["id"] in order]
    generated.sort(key=lambda it: order[it["id"]])
    manual = [
        it for it in items
        if not is_site_line_id(it["id"])
        and not str(it.get("assigned_by") or "").startswith("system:")
    ]
    return generated + manual
