"""Per-process cache of Excel-read DataFrames keyed by (path, mtime_ns, size, kind).

Phase 3 item 19a: منبع اصلی (and other cleaned extracts) are re-read on almost every
menu action; reading a ~2k-row xlsx with openpyxl costs ~0.3–1 s. The key changes
whenever the file is rewritten (mtime/size), so a stale frame is never served.
Callers always receive a copy (safe to mutate). Thread-safe; small LRU.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Callable

import pandas as pd

MAX_ENTRIES = 12
_lock = threading.Lock()
_cache: "OrderedDict[tuple, pd.DataFrame]" = OrderedDict()
stats = {"hits": 0, "misses": 0}


def _key(path: Path | str, kind: str) -> tuple | None:
    try:
        p = Path(path).resolve()
        st = p.stat()
    except OSError:
        return None
    return (str(p), st.st_mtime_ns, st.st_size, kind)


def cached_frame(path: Path | str, kind: str, loader: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    key = _key(path, kind)
    if key is None:
        return loader()
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            stats["hits"] += 1
            return hit.copy()
    df = loader()
    if isinstance(df, pd.DataFrame):
        with _lock:
            stats["misses"] += 1
            # drop older versions of the same file/kind
            for k in [k for k in _cache if k[0] == key[0] and k[3] == kind]:
                _cache.pop(k, None)
            _cache[key] = df.copy()
            while len(_cache) > MAX_ENTRIES:
                _cache.popitem(last=False)
    return df


def clear() -> None:
    with _lock:
        _cache.clear()
        stats.update(hits=0, misses=0)
