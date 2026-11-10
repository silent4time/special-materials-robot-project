#!/usr/bin/env python3
"""سنگ دیواره: 1+1 per billet tundish, 2 bloom stones per bloom tundish."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from analytics.wall_stones import wall_stone_kind
from services.n_tundish_report import build_rows


def _row(code, keyword, location, segment, item_id, **rates):
    base = {
        "category_code": code,
        "id": item_id,
        "product_name": keyword,
        "keyword": keyword,
        "usage_location": location,
        "quantity": 10,
        "priority": 1,
        "contractor_or_company": segment,
        "unit": "عدد",
        "billet_renovation": 0,
        "billet_patching": 0,
        "bloom_renovation": 0,
        "bloom_patching": 0,
        "slab_renovation": 0,
        "slab_patching": 0,
        "casting_floor": 0,
    }
    base.update(rates)
    return base


def _need(df, section, n, keyword):
    res = build_rows(df, section, n)
    assert not res.error, res.error
    rows = [r for r in res.rows if keyword in str(r.get("کلید واژه") or r.get("شرح") or "")]
    # column is COL_KEYWORD
    from services.n_tundish_report import COL_KEYWORD, COL_NEED

    rows = [r for r in res.rows if keyword in str(r.get(COL_KEYWORD) or "")]
    assert rows, [r.get(COL_KEYWORD) for r in res.rows]
    return sum(float(r[COL_NEED]) for r in rows if isinstance(r[COL_NEED], (int, float)))


def main() -> None:
    assert wall_stone_kind("سنگ دیواره بیلت") == "billet"
    assert wall_stone_kind("سنگ دیواره بلومی") == "bloom"
    assert wall_stone_kind("بتن بیلت") is None

    one = pd.DataFrame([
        _row("1637", "سنگ دیواره بلوم", "بلوم", "پیمانکار", "163700009001", bloom_patching=9, billet_patching=9),
        _row("1640", "سنگ دیواره بیلت", "بیلت", "شرکت", "164011110001", billet_patching=4),
        _row("1203", "بتن بیلت", "بیلت", "شرکت", "120311110001", billet_patching=2),
    ])
    assert _need(one, "billet", 5, "سنگ دیواره بلوم") == 5
    assert _need(one, "billet", 5, "سنگ دیواره بیلت") == 5
    assert _need(one, "bloom", 4, "سنگ دیواره بلوم") == 8
    bloom_billet = build_rows(one, "bloom", 4)
    from services.n_tundish_report import COL_KEYWORD
    assert all("سنگ دیواره بیلت" not in str(r[COL_KEYWORD]) for r in bloom_billet.rows)
    assert _need(one, "billet", 5, "بتن بیلت") == 10

    # Same code on both sides must not be added twice.
    both = pd.DataFrame([
        _row("1637", "سنگ دیواره بلومی", "بیلت", "شرکت", "163711110001"),
        _row("1637", "سنگ دیواره بلوم", "بلوم", "پیمانکار", "163700009002"),
    ])
    assert _need(both, "billet", 5, "سنگ دیواره") == 5, _need(both, "billet", 5, "سنگ دیواره")
    assert _need(both, "bloom", 5, "سنگ دیواره") == 10

    split = pd.DataFrame([
        _row("1638", "سنگ دیواره بیلت", "بیلت", "شرکت", "163811110001"),
        _row("1638", "سنگ دیواره بلوم", "بلوم", "پیمانکار", "163800009002"),
    ])
    # One code: rates are max() within a segment, and the two segments add.
    # Billet tundish must still see both stones → need 2 per tundish (1+1).
    assert _need(split, "billet", 5, "سنگ دیواره") == 10, build_rows(split, "billet", 5).rows
    assert _need(split, "bloom", 3, "سنگ دیواره") == 6
    print("smoke_wall_stones: ALL OK")


if __name__ == "__main__":
    main()
