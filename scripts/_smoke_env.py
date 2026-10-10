"""Imported FIRST by every scripts/smoke_*.py (phase 3 item 19e).

Points DATABASE_PATH / REPORT_DIR / UPLOAD_DIR at a fresh temp dir *before*
``config`` is imported, so no smoke run can touch the live data/bot.db or write
files into the live reports/ or uploads/ folders. Removed at interpreter exit.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

SMOKE_TMP = Path(tempfile.mkdtemp(prefix="smoke_env_"))
os.environ["DATABASE_PATH"] = str(SMOKE_TMP / "guard.db")
os.environ["REPORT_DIR"] = str(SMOKE_TMP / "reports")
os.environ["UPLOAD_DIR"] = str(SMOKE_TMP / "uploads")
(SMOKE_TMP / "reports").mkdir()
(SMOKE_TMP / "uploads").mkdir()
atexit.register(shutil.rmtree, SMOKE_TMP, ignore_errors=True)
