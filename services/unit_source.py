"""Unit (واحد) of every report comes from منبع اصلی (main source) — by item ID, then by
4-digit code when that code has a single unit there. The input file's own unit is
kept only for items absent from the main source. Read-only: neither the main source
nor any uploaded file is modified; only the in-memory report frames are relabelled.

Example: the plant monthly export writes «SH» for 378471411121U / 378700009162X
(code 1473) while منبع اصلی says «No» → reports show «No».
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

_CACHE: dict[Any, tuple[dict[str, str], dict[str, str]]] = {}
_ID_RE = re.compile(r"^\s*([0-9]{6,}[A-Za-z]?)\b")


def _norm_id(v: Any) -> str:
    s = str(v or "").strip().upper()
    return "" if s in {"", "NAN", "NONE"} else s


def _norm_code(v: Any) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return "" if s.lower() in {"", "nan", "none"} else s


def item_id_of(row_id: Any, name: Any = None) -> str:
    rid = _norm_id(row_id)
    if rid:
        return rid
    m = _ID_RE.match(str(name or ""))
    return m.group(1).upper() if m else ""


def maps_from_inventory(inv: pd.DataFrame | None) -> tuple[dict[str, str], dict[str, str]]:
    """(unit by ID, unit by 4-digit code — only codes with one distinct unit)."""
    by_id: dict[str, str] = {}
    code_units: dict[str, set[str]] = {}
    if inv is None or inv.empty or "unit" not in inv.columns:
        return by_id, {}
    for _, r in inv.iterrows():
        u = str(r.get("unit") or "").strip()
        if not u or u.lower() == "nan":
            continue
        iid = _norm_id(r.get("id"))
        if iid:
            by_id.setdefault(iid, u)
        code = _norm_code(r.get("category_code"))
        if code:
            code_units.setdefault(code, set()).add(u)
    by_code = {c: next(iter(s)) for c, s in code_units.items() if len(s) == 1}
    return by_id, by_code


def main_source_maps(db: Any) -> tuple[dict[str, str], dict[str, str]]:
    """Maps from the latest cleaned منبع اصلی (cached per extract id)."""
    try:
        row = db.get_latest_extracted_any("product_inventory")
    except Exception:  # noqa: BLE001
        row = None
    if not row:
        return {}, {}
    key = (str(getattr(db, "path", id(db))), row.get("id"), row.get("clean_path"))
    if key in _CACHE:
        return _CACHE[key]
    inv = None
    try:
        from analytics.frames import load_extract_frame

        owner = next((u for u in db.list_users() if u.get("role") == "owner"), None) or {
            "bale_user_id": row.get("bale_user_id"), "role": "owner"}
        inv = load_extract_frame(db, "product_inventory", owner)
    except Exception:  # noqa: BLE001
        inv = None
    maps = maps_from_inventory(inv)
    _CACHE.clear()
    _CACHE[key] = maps
    return maps


def unit_for(maps: tuple[dict[str, str], dict[str, str]], item_id: Any, code: Any, fallback: Any, name: Any = None) -> Any:
    """ID in main source → its unit; else 4-digit code with one unit there → that; else ``fallback``."""
    by_id, by_code = maps
    iid = item_id_of(item_id, name)
    if iid and iid in by_id:
        return by_id[iid]
    c = _norm_code(code)
    if c and c in by_code:
        return by_code[c]
    return fallback


def apply_units(
    df: pd.DataFrame | None,
    maps: tuple[dict[str, str], dict[str, str]],
    *,
    unit_col: str = "unit",
    id_col: str = "id",
    code_col: str = "category_code",
    name_col: str = "material_name",
) -> pd.DataFrame | None:
    """Copy of ``df`` whose unit column follows the main source (fallback = own unit)."""
    if df is None or df.empty or unit_col not in df.columns or not (maps[0] or maps[1]):
        return df
    out = df.copy()
    n = len(out)
    col = lambda c: out[c] if c in out.columns else [None] * n  # noqa: E731
    out[unit_col] = [unit_for(maps, i, c, u, nm) for i, c, u, nm in
                     zip(col(id_col), col(code_col), out[unit_col], col(name_col))]
    return out
