#!/usr/bin/env python3
"""Smoke: import web app, hit routes with TestClient (no BALE token)."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402  — temp DB/reports/uploads before config import

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from fastapi.testclient import TestClient

    from db.models import Database
    from web.app import create_app
    from web.deps import reset_db_singleton

    reset_db_singleton()
    db = Database()
    with db.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    print(f"journal_mode={mode}")

    app = create_app()
    paths = list(app.openapi().get("paths", {}).keys())
    needed = ["/login", "/home", "/stock", "/materials/request", "/materials/return", "/reports", "/tundish-report", "/tundish-report/settings"]
    for p in needed:
        assert p in paths or (p + "/") in paths, f"missing route {p} in {paths}"
    print("routes_ok", len(paths))

    client = TestClient(app)
    r = client.get("/login")
    assert r.status_code == 200 and "ورود" in r.text, r.text[:300]
    r2 = client.get("/home", follow_redirects=False)
    assert r2.status_code in (303, 307)
    print("login_page_ok")

    owners = [u for u in db.list_users(active_only=True) if u.get("role") == "owner"]
    owner = owners[0] if owners else None
    print("owner", owner.get("bale_user_id") if owner else None, owner.get("role") if owner else None)
    print("SMOKE_WEB_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
