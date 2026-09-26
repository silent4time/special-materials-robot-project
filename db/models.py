"""SQLite persistence for users, roles, scopes, sessions, extracts, and reports."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Iterable, Optional

from config import DATABASE_PATH, ROLES, ensure_dirs


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or DATABASE_PATH)
        ensure_dirs()
        self._init_schema()

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bale_user_id TEXT NOT NULL UNIQUE,
                    display_name TEXT,
                    role TEXT NOT NULL CHECK(role IN ('owner','manager','responsible_officer','technician')),
                    scope TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS upload_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bale_user_id TEXT NOT NULL,
                    pending_file_type TEXT,
                    tank_path TEXT,
                    inventory_path TEXT,
                    monthly_path TEXT,
                    status TEXT NOT NULL DEFAULT 'collecting',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bale_user_id TEXT NOT NULL,
                    session_id INTEGER,
                    pdf_path TEXT NOT NULL,
                    row_counts_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES upload_sessions(id)
                );

                CREATE TABLE IF NOT EXISTS extracted_datasets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bale_user_id TEXT NOT NULL,
                    session_id INTEGER,
                    file_type TEXT NOT NULL,
                    raw_path TEXT NOT NULL,
                    clean_path TEXT NOT NULL,
                    row_count INTEGER,
                    columns_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES upload_sessions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_users_bale ON users(bale_user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON upload_sessions(bale_user_id);
                CREATE INDEX IF NOT EXISTS idx_extracted_user_type
                    ON extracted_datasets(bale_user_id, file_type, created_at);
                CREATE INDEX IF NOT EXISTS idx_extracted_session
                    ON extracted_datasets(session_id);
                """
            )
            self._migrate_users_role_check(conn)


    def _migrate_users_role_check(self, conn: sqlite3.Connection) -> None:
        """Recreate users table if CHECK constraint predates the owner role."""
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if not row or not row[0]:
            return
        sql = row[0]
        if "'owner'" in sql:
            return
        conn.executescript(
            """
            CREATE TABLE users_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bale_user_id TEXT NOT NULL UNIQUE,
                display_name TEXT,
                role TEXT NOT NULL CHECK(role IN ('owner','manager','responsible_officer','technician')),
                scope TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO users_new
                (id, bale_user_id, display_name, role, scope, active, created_at, updated_at)
            SELECT id, bale_user_id, display_name, role, scope, active, created_at, updated_at
            FROM users;
            DROP TABLE users;
            ALTER TABLE users_new RENAME TO users;
            CREATE INDEX IF NOT EXISTS idx_users_bale ON users(bale_user_id);
            """
        )

    # --- users ---
    def upsert_user(
        self,
        bale_user_id: str | int,
        role: str,
        display_name: str | None = None,
        scope: str | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"نقش نامعتبر: {role}")
        uid = str(bale_user_id)
        now = _utcnow()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE bale_user_id = ?", (uid,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE users
                    SET display_name = COALESCE(?, display_name),
                        role = ?,
                        scope = ?,
                        active = ?,
                        updated_at = ?
                    WHERE bale_user_id = ?
                    """,
                    (display_name, role, scope, 1 if active else 0, now, uid),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO users
                    (bale_user_id, display_name, role, scope, active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (uid, display_name or uid, role, scope, 1 if active else 0, now, now),
                )
        return self.get_user(uid)  # type: ignore[return-value]

    def get_user(self, bale_user_id: str | int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE bale_user_id = ?", (str(bale_user_id),)
            ).fetchone()
            return dict(row) if row else None

    def list_users(self, active_only: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM users"
        if active_only:
            q += " WHERE active = 1"
        q += " ORDER BY role, display_name"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(q).fetchall()]

    def set_role(self, bale_user_id: str | int, role: str) -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"نقش نامعتبر: {role}")
        user = self.get_user(bale_user_id)
        if not user:
            raise KeyError("کاربر یافت نشد")
        return self.upsert_user(
            bale_user_id,
            role=role,
            display_name=user.get("display_name"),
            scope=user.get("scope"),
            active=bool(user.get("active")),
        )

    def set_scope(self, bale_user_id: str | int, scope: str | None) -> dict[str, Any]:
        user = self.get_user(bale_user_id)
        if not user:
            raise KeyError("کاربر یافت نشد")
        return self.upsert_user(
            bale_user_id,
            role=user["role"],
            display_name=user.get("display_name"),
            scope=scope,
            active=bool(user.get("active")),
        )

    def deactivate_user(self, bale_user_id: str | int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET active = 0, updated_at = ? WHERE bale_user_id = ?",
                (_utcnow(), str(bale_user_id)),
            )

    # --- upload sessions ---
    def get_or_create_session(self, bale_user_id: str | int) -> dict[str, Any]:
        uid = str(bale_user_id)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM upload_sessions
                WHERE bale_user_id = ? AND status = 'collecting'
                ORDER BY id DESC LIMIT 1
                """,
                (uid,),
            ).fetchone()
            if row:
                return dict(row)
            now = _utcnow()
            cur = conn.execute(
                """
                INSERT INTO upload_sessions
                (bale_user_id, pending_file_type, status, created_at, updated_at)
                VALUES (?, NULL, 'collecting', ?, ?)
                """,
                (uid, now, now),
            )
            sid = cur.lastrowid
        return self.get_session(sid)  # type: ignore[return-value]

    def get_session(self, session_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM upload_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    def set_pending_file_type(
        self, bale_user_id: str | int, file_type: str | None
    ) -> dict[str, Any]:
        session = self.get_or_create_session(bale_user_id)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE upload_sessions
                SET pending_file_type = ?, updated_at = ?
                WHERE id = ?
                """,
                (file_type, _utcnow(), session["id"]),
            )
        return self.get_session(session["id"])  # type: ignore[return-value]

    def store_file_slot(
        self, bale_user_id: str | int, file_type: str, path: str
    ) -> dict[str, Any]:
        column = {
            "tank_consumption": "tank_path",
            "product_inventory": "inventory_path",
            "monthly_consumption": "monthly_path",
        }.get(file_type)
        if not column:
            raise ValueError(f"نوع فایل نامعتبر: {file_type}")
        session = self.get_or_create_session(bale_user_id)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE upload_sessions
                SET {column} = ?, pending_file_type = NULL, updated_at = ?
                WHERE id = ?
                """,
                (path, _utcnow(), session["id"]),
            )
        return self.get_session(session["id"])  # type: ignore[return-value]

    def session_completeness(self, session: dict[str, Any]) -> dict[str, bool]:
        return {
            "tank_consumption": bool(session.get("tank_path")),
            "product_inventory": bool(session.get("inventory_path")),
            "monthly_consumption": bool(session.get("monthly_path")),
        }

    def all_files_ready(self, session: dict[str, Any]) -> bool:
        return all(self.session_completeness(session).values())

    def mark_session_done(self, session_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE upload_sessions
                SET status = 'done', pending_file_type = NULL, updated_at = ?
                WHERE id = ?
                """,
                (_utcnow(), session_id),
            )

    def reset_session(self, bale_user_id: str | int) -> dict[str, Any]:
        uid = str(bale_user_id)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE upload_sessions
                SET status = 'cancelled', pending_file_type = NULL, updated_at = ?
                WHERE bale_user_id = ? AND status = 'collecting'
                """,
                (_utcnow(), uid),
            )
        return self.get_or_create_session(uid)

    # --- extracted datasets (clean Excel copies after row/column extract) ---
    def save_extracted(
        self,
        bale_user_id: str | int,
        session_id: int | None,
        file_type: str,
        raw_path: str,
        clean_path: str,
        row_count: int,
        columns: list[str] | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO extracted_datasets
                (bale_user_id, session_id, file_type, raw_path, clean_path,
                 row_count, columns_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(bale_user_id),
                    session_id,
                    file_type,
                    raw_path,
                    clean_path,
                    int(row_count),
                    json.dumps(columns or [], ensure_ascii=False),
                    _utcnow(),
                ),
            )
            return int(cur.lastrowid)

    def get_latest_extracted(
        self, bale_user_id: str | int, file_type: str
    ) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM extracted_datasets
                WHERE bale_user_id = ? AND file_type = ?
                ORDER BY id DESC LIMIT 1
                """,
                (str(bale_user_id), file_type),
            ).fetchone()
            return dict(row) if row else None

    def list_extracted_for_session(self, session_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM extracted_datasets
                WHERE session_id = ?
                ORDER BY id
                """,
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_report(

        self,
        bale_user_id: str | int,
        session_id: int,
        pdf_path: str,
        row_counts: dict[str, int],
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO reports
                (bale_user_id, session_id, pdf_path, row_counts_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(bale_user_id),
                    session_id,
                    pdf_path,
                    json.dumps(row_counts, ensure_ascii=False),
                    _utcnow(),
                ),
            )
            return int(cur.lastrowid)

    def bootstrap_admin(self, admin_bale_user_id: str, display_name: str = "مالک سیستم") -> None:
        if not admin_bale_user_id:
            return
        existing = self.get_user(admin_bale_user_id)
        if existing:
            if existing["role"] not in ("owner", "manager"):
                self.set_role(admin_bale_user_id, "owner")
            return
        self.upsert_user(admin_bale_user_id, role="owner", display_name=display_name)
