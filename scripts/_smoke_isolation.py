"""Keep smoke runs out of the real ``uploads/`` tree.

Main-goal stores copy source files to ``UPLOAD_DIR/main_goal_history/<month>/`` (audit copies
referenced by DB rows). Smokes use temp DBs but used to write those copies into the REAL
uploads dir, overwriting e.g. ``m_1405-04/slab_sequences.xlsx``. Import this module right
after ``sys.path`` setup and before any project module that does ``from config import UPLOAD_DIR``.
"""
from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

import config

REAL_UPLOAD_DIR = Path(config.UPLOAD_DIR)
SMOKE_UPLOAD_DIR = Path(tempfile.mkdtemp(prefix="smoke_uploads_"))


def isolate_uploads() -> Path:
    config.UPLOAD_DIR = SMOKE_UPLOAD_DIR
    for mod in list(sys.modules.values()):
        try:
            if Path(getattr(mod, "UPLOAD_DIR")) == REAL_UPLOAD_DIR:
                mod.UPLOAD_DIR = SMOKE_UPLOAD_DIR
        except Exception:  # noqa: BLE001 - attribute missing / not a path
            continue
    return SMOKE_UPLOAD_DIR


isolate_uploads()
atexit.register(shutil.rmtree, SMOKE_UPLOAD_DIR, True)
