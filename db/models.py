"""SQLite persistence for users, roles, scopes, sessions, extracts, category codes, and reports."""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator, Iterable, Optional

from config import DATABASE_PATH, DEFAULT_CATEGORY_CODES, ROLES, SITE_STOCK_GROUP_KEYS, ensure_dirs


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or DATABASE_PATH)
        ensure_dirs()
        self._init_schema()

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
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
                    created_by TEXT,
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

                CREATE TABLE IF NOT EXISTS category_codes (
                    code TEXT NOT NULL UNIQUE,
                    label TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    created_by TEXT
                );

                CREATE TABLE IF NOT EXISTS catalog_items (
                    id TEXT PRIMARY KEY,
                    name_desc TEXT NOT NULL,
                    category_code TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS catalog_group_assignments (
                    item_id TEXT NOT NULL PRIMARY KEY,
                    tundish_group TEXT NOT NULL
                        CHECK(tundish_group IN ('slab','bloom','billet')),
                    assigned_by TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES catalog_items(id)
                );

                CREATE TABLE IF NOT EXISTS site_stock_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bale_user_id TEXT NOT NULL,
                    tundish_group TEXT NOT NULL
                        CHECK(tundish_group IN ('slab','bloom','billet')),
                    item_id TEXT NOT NULL,
                    item_name_snapshot TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    pallet_qty REAL,
                    unit_qty REAL,
                    quantity_detail TEXT,
                    entry_date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    session_id INTEGER,
                    actor_display_name TEXT,
                    UNIQUE(entry_date, tundish_group, item_id),
                    FOREIGN KEY(item_id) REFERENCES catalog_items(id),
                    FOREIGN KEY(session_id) REFERENCES upload_sessions(id)
                );


                CREATE TABLE IF NOT EXISTS invites (
                    token TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    scope TEXT,
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    used_by TEXT,
                    used_at TEXT,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_invites_active ON invites(active);

                CREATE INDEX IF NOT EXISTS idx_users_bale ON users(bale_user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON upload_sessions(bale_user_id);
                CREATE INDEX IF NOT EXISTS idx_extracted_user_type
                    ON extracted_datasets(bale_user_id, file_type, created_at);
                CREATE INDEX IF NOT EXISTS idx_extracted_session
                    ON extracted_datasets(session_id);
                CREATE INDEX IF NOT EXISTS idx_category_codes_active
                    ON category_codes(active);
                CREATE INDEX IF NOT EXISTS idx_catalog_items_active
                    ON catalog_items(active);
                CREATE INDEX IF NOT EXISTS idx_catalog_assign_group
                    ON catalog_group_assignments(tundish_group);
                CREATE INDEX IF NOT EXISTS idx_site_stock_date_group
                    ON site_stock_entries(entry_date, tundish_group);
                CREATE INDEX IF NOT EXISTS idx_site_stock_item
                    ON site_stock_entries(item_id, entry_date);

                CREATE TABLE IF NOT EXISTS material_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bale_user_id TEXT NOT NULL,
                    actor_display_name TEXT,
                    coverage_days REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'confirmed',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS material_request_lines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER NOT NULL,
                    item_id TEXT,
                    item_name TEXT NOT NULL,
                    unit TEXT,
                    avg_daily REAL,
                    remaining_qty REAL,
                    quantity REAL NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES material_requests(id)
                );

                CREATE TABLE IF NOT EXISTS inventory_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_key TEXT NOT NULL,
                    item_id TEXT,
                    item_name TEXT,
                    delta REAL NOT NULL,
                    reason TEXT,
                    ref_type TEXT NOT NULL,
                    ref_id INTEGER,
                    bale_user_id TEXT,
                    actor_display_name TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_material_requests_created
                    ON material_requests(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_material_request_lines_req
                    ON material_request_lines(request_id);
                CREATE INDEX IF NOT EXISTS idx_inventory_ledger_key
                    ON inventory_ledger(item_key);
                CREATE INDEX IF NOT EXISTS idx_inventory_ledger_ref
                    ON inventory_ledger(ref_type, ref_id);
                
                CREATE TABLE IF NOT EXISTS warehouse_returns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bale_user_id TEXT NOT NULL,
                    actor_display_name TEXT,
                    status TEXT NOT NULL DEFAULT 'confirmed',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS warehouse_return_lines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    return_id INTEGER NOT NULL,
                    item_id TEXT,
                    item_name TEXT NOT NULL,
                    unit TEXT,
                    site_qty REAL,
                    surplus_qty REAL,
                    quantity REAL NOT NULL,
                    surplus_reason TEXT,
                    FOREIGN KEY(return_id) REFERENCES warehouse_returns(id)
                );

                CREATE INDEX IF NOT EXISTS idx_warehouse_returns_created
                    ON warehouse_returns(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_warehouse_return_lines_ret
                    ON warehouse_return_lines(return_id);

                CREATE TABLE IF NOT EXISTS web_credentials (
                    bale_user_id TEXT NOT NULL PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(bale_user_id) REFERENCES users(bale_user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_web_credentials_username
                    ON web_credentials(username);

                CREATE TABLE IF NOT EXISTS user_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bale_user_id TEXT NOT NULL,
                    display_name TEXT,
                    action_key TEXT NOT NULL,
                    message_fa TEXT NOT NULL,
                    details_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_user_activity_created
                    ON user_activity(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_user_activity_user
                    ON user_activity(bale_user_id, created_at DESC);
                """
            )
            self._migrate_users_role_check(conn)
            self._migrate_add_columns(conn)
            self._ensure_default_category_codes(conn)

    def _ensure_default_category_codes(self, conn: sqlite3.Connection) -> None:
        """Insert missing DEFAULT_CATEGORY_CODES (does not overwrite or reactivate)."""
        now = _utcnow()
        for code in DEFAULT_CATEGORY_CODES:
            conn.execute(
                """
                INSERT OR IGNORE INTO category_codes (code, label, active, created_at, created_by)
                VALUES (?, ?, 1, ?, ?)
                """,
                (code, None, now, "bootstrap"),
            )

    def ensure_default_category_codes(self) -> int:
        """Public seed helper; returns number of codes newly inserted."""
        before = {r["code"] for r in self.list_category_codes(active_only=False)}
        with self.connect() as conn:
            self._ensure_default_category_codes(conn)
        after = {r["code"] for r in self.list_category_codes(active_only=False)}
        return len(after - before)

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

    def _migrate_add_columns(self, conn: sqlite3.Connection) -> None:
        """Safe ALTER TABLE ADD COLUMN for older DBs."""
        additions = [
            ("catalog_group_assignments", "assigned_by", "TEXT"),
            ("reports", "created_by", "TEXT"),
            ("site_stock_entries", "actor_display_name", "TEXT"),
        ]
        for table, column, coltype in additions:
            try:
                cols = {
                    r[1]
                    for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
            except sqlite3.OperationalError:
                continue
            if column in cols:
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            except sqlite3.OperationalError:
                pass
        try:
            conn.execute(
                """
                UPDATE reports
                SET created_by = bale_user_id
                WHERE created_by IS NULL OR created_by = ''
                """
            )
        except sqlite3.OperationalError:
            pass

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

    def count_active_owners(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE active = 1 AND role = 'owner'"
            ).fetchone()
            return int(row["c"] if row else 0)

    def try_claim_first_owner(
        self,
        bale_user_id: str | int,
        display_name: str | None = None,
    ) -> Optional[dict[str, Any]]:
        """Race-safe: if no active owners, promote/insert this user as owner.

        Returns the owner user dict when this caller wins the claim, else None
        (another owner already exists). Uses BEGIN IMMEDIATE so concurrent
        first /start calls cannot both become owner.
        """
        uid = str(bale_user_id)
        now = _utcnow()
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        claimed = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE active = 1 AND role = 'owner'"
            ).fetchone()
            if int(row["c"] if row else 0) > 0:
                conn.rollback()
                return None
            existing = conn.execute(
                "SELECT * FROM users WHERE bale_user_id = ?", (uid,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE users
                    SET display_name = COALESCE(?, display_name),
                        role = 'owner',
                        scope = NULL,
                        active = 1,
                        updated_at = ?
                    WHERE bale_user_id = ?
                    """,
                    (display_name, now, uid),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO users
                    (bale_user_id, display_name, role, scope, active, created_at, updated_at)
                    VALUES (?, ?, 'owner', NULL, 1, ?, ?)
                    """,
                    (uid, display_name or uid, now, now),
                )
            conn.commit()
            claimed = True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if not claimed:
            return None
        return self.get_user(uid)

    # --- invites (deep-link onboarding) ---
    def create_invite(
        self,
        role: str,
        scope: str | None = None,
        created_by: str | None = None,
        expires_days: int | None = 7,
    ) -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"نقش نامعتبر: {role}")
        token = secrets.token_urlsafe(16)
        now = _utcnow()
        expires_at = None
        if expires_days is not None and expires_days > 0:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO invites
                    (token, role, scope, created_by, created_at, expires_at, used_by, used_at, active)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 1)
                """,
                (token, role, scope, str(created_by) if created_by else None, now, expires_at),
            )
        return self.get_invite(token)  # type: ignore[return-value]

    def get_invite(self, token: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM invites WHERE token = ?", (str(token),)
            ).fetchone()
            return dict(row) if row else None

    def consume_invite(
        self,
        token: str,
        bale_user_id: str | int,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """Validate invite, upsert user with invite role/scope, mark used. Raises ValueError."""
        invite = self.get_invite(token)
        if not invite or not invite.get("active"):
            raise ValueError("لینک دعوت نامعتبر یا منقضی شده است. از مدیر لینک جدید بگیرید.")
        if invite.get("used_by"):
            raise ValueError("این لینک دعوت قبلاً استفاده شده است. از مدیر لینک جدید بگیرید.")
        expires_at = invite.get("expires_at")
        if expires_at:
            try:
                exp = datetime.fromisoformat(expires_at)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp:
                    raise ValueError("لینک دعوت منقضی شده است. از مدیر لینک جدید بگیرید.")
            except ValueError as exc:
                if "منقضی" in str(exc) or "نامعتبر" in str(exc):
                    raise
                # bad date format → treat as expired for safety
                raise ValueError("لینک دعوت نامعتبر یا منقضی شده است. از مدیر لینک جدید بگیرید.") from exc

        role = invite["role"]
        scope = invite.get("scope")
        user = self.upsert_user(
            bale_user_id,
            role=role,
            display_name=display_name,
            scope=scope,
            active=True,
        )
        now = _utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE invites
                SET used_by = ?, used_at = ?, active = 0
                WHERE token = ? AND used_by IS NULL AND active = 1
                """,
                (str(bale_user_id), now, str(token)),
            )
        # Re-check race: if another redeem won, ensure we still return consistent user
        refreshed = self.get_invite(token)
        if refreshed and refreshed.get("used_by") and str(refreshed["used_by"]) != str(bale_user_id):
            raise ValueError("این لینک دعوت قبلاً استفاده شده است. از مدیر لینک جدید بگیرید.")
        return user


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

    def get_previous_extracted(
        self, bale_user_id: str | int, file_type: str
    ) -> Optional[dict[str, Any]]:
        """Second-latest extract for a user/type (baseline for inventory inbound)."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM extracted_datasets
                WHERE bale_user_id = ? AND file_type = ?
                ORDER BY id DESC LIMIT 1 OFFSET 1
                """,
                (str(bale_user_id), file_type),
            ).fetchone()
            return dict(row) if row else None

    def update_extracted_clean_path(self, extract_id: int, clean_path: str) -> None:
        """Point an extract row at a preserved snapshot file (after overwrite)."""
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE extracted_datasets
                SET clean_path = ?
                WHERE id = ?
                """,
                (str(clean_path), int(extract_id)),
            )

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

    # --- category codes (warehouse inventory allowlist) ---
    @staticmethod
    def validate_category_code(code: str | int) -> str:
        """Require exactly 4 digits (Excel floats like 1201.0 → 1201 OK; short codes rejected)."""
        text = str(code).strip()
        if text.endswith(".0") and text[:-2].isdigit():
            text = text[:-2]
        if len(text) != 4 or not text.isdigit():
            raise ValueError("کد دسته بندی باید دقیقاً ۴ رقم باشد.")
        return text

    def add_category_code(
        self,
        code: str | int,
        *,
        label: str | None = None,
        created_by: str | int | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        normalized = self.validate_category_code(code)
        now = _utcnow()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM category_codes WHERE code = ?", (normalized,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE category_codes
                    SET label = COALESCE(?, label),
                        active = ?,
                        created_by = COALESCE(?, created_by)
                    WHERE code = ?
                    """,
                    (
                        label,
                        1 if active else 0,
                        str(created_by) if created_by is not None else None,
                        normalized,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO category_codes (code, label, active, created_at, created_by)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        normalized,
                        label,
                        1 if active else 0,
                        now,
                        str(created_by) if created_by is not None else None,
                    ),
                )
        return self.get_category_code(normalized)  # type: ignore[return-value]

    def get_category_code(self, code: str | int) -> Optional[dict[str, Any]]:
        try:
            normalized = self.validate_category_code(code)
        except ValueError:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM category_codes WHERE code = ?", (normalized,)
            ).fetchone()
            return dict(row) if row else None

    def list_category_codes(self, active_only: bool = True) -> list[dict[str, Any]]:
        q = "SELECT * FROM category_codes"
        if active_only:
            q += " WHERE active = 1"
        q += " ORDER BY code"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(q).fetchall()]

    def active_category_code_set(self) -> set[str]:
        return {r["code"] for r in self.list_category_codes(active_only=True)}

    def deactivate_category_code(self, code: str | int) -> None:
        normalized = self.validate_category_code(code)
        with self.connect() as conn:
            conn.execute(
                "UPDATE category_codes SET active = 0 WHERE code = ?",
                (normalized,),
            )

    def save_report(
        self,
        bale_user_id: str | int,
        session_id: int,
        pdf_path: str,
        row_counts: dict[str, int],
        *,
        created_by: str | int | None = None,
    ) -> int:
        actor = str(created_by if created_by is not None else bale_user_id)
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO reports
                (bale_user_id, session_id, pdf_path, row_counts_json, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(bale_user_id),
                    session_id,
                    pdf_path,
                    json.dumps(row_counts, ensure_ascii=False),
                    _utcnow(),
                    actor,
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


    # --- catalog items (site stock master list) ---
    def upsert_catalog_item(
        self,
        item_id: str,
        name_desc: str,
        category_code: str | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        iid = str(item_id).strip()
        if not iid:
            raise ValueError("شناسه کالا خالی است.")
        name = (name_desc or "").strip() or iid
        now = _utcnow()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM catalog_items WHERE id = ?", (iid,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE catalog_items
                    SET name_desc = ?,
                        category_code = COALESCE(?, category_code),
                        active = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (name, category_code, 1 if active else 0, now, iid),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO catalog_items
                    (id, name_desc, category_code, active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (iid, name, category_code, 1 if active else 0, now, now),
                )
        return self.get_catalog_item(iid)  # type: ignore[return-value]

    def get_catalog_item(self, item_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM catalog_items WHERE id = ?", (str(item_id).strip(),)
            ).fetchone()
            return dict(row) if row else None

    def list_catalog_items(self, active_only: bool = True) -> list[dict[str, Any]]:
        q = "SELECT * FROM catalog_items"
        if active_only:
            q += " WHERE active = 1"
        q += " ORDER BY name_desc COLLATE NOCASE, id"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(q).fetchall()]

    def deactivate_catalog_item(self, item_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE catalog_items SET active = 0, updated_at = ? WHERE id = ?",
                (_utcnow(), str(item_id).strip()),
            )

    def seed_catalog_from_inventory_extract(
        self,
        clean_path: str | Path,
        *,
        only_missing: bool = True,
    ) -> dict[str, int]:
        """Seed catalog_items from a cleaned product_inventory Excel.

        Returns counts: inserted / updated / skipped / total_rows.
        """
        import pandas as pd

        path = Path(clean_path)
        if not path.exists():
            raise FileNotFoundError(f"فایل موجودی یافت نشد: {path}")
        df = pd.read_excel(path, engine="openpyxl")
        if df is None or df.empty:
            return {"inserted": 0, "updated": 0, "skipped": 0, "total_rows": 0}
        cols = {str(c).strip().lower(): c for c in df.columns}
        id_col = cols.get("id")
        name_col = (
            cols.get("product_name")
            or cols.get("item_code_desc")  # legacy cleans
            or cols.get("name_desc")
            or cols.get("material_name")
        )
        cat_col = cols.get("category_code")
        if not id_col:
            raise ValueError("ستون id در فایل تمیز موجودی یافت نشد.")
        inserted = updated = skipped = 0
        for _, row in df.iterrows():
            raw_id = row.get(id_col)
            if raw_id is None or (isinstance(raw_id, float) and pd.isna(raw_id)):
                skipped += 1
                continue
            iid = str(raw_id).strip()
            if not iid or iid.lower() == "nan":
                skipped += 1
                continue
            name = ""
            if name_col is not None:
                val = row.get(name_col)
                if val is not None and not (isinstance(val, float) and pd.isna(val)):
                    name = str(val).strip()
            if not name:
                name = iid
            cat = None
            if cat_col is not None:
                cval = row.get(cat_col)
                if cval is not None and not (isinstance(cval, float) and pd.isna(cval)):
                    cat = str(cval).strip()
                    if cat.endswith(".0") and cat[:-2].isdigit():
                        cat = cat[:-2]
            existing = self.get_catalog_item(iid)
            if existing and only_missing:
                skipped += 1
                continue
            before = existing
            self.upsert_catalog_item(iid, name, category_code=cat, active=True)
            if before:
                updated += 1
            else:
                inserted += 1
        return {
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "total_rows": int(len(df)),
        }

    def seed_catalog_from_latest_warehouse(
        self, bale_user_id: str | int | None = None
    ) -> dict[str, Any]:
        """Seed from the newest product_inventory extract (any user if uid omitted)."""
        with self.connect() as conn:
            if bale_user_id is not None:
                row = conn.execute(
                    """
                    SELECT * FROM extracted_datasets
                    WHERE file_type = 'product_inventory' AND bale_user_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (str(bale_user_id),),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM extracted_datasets
                    WHERE file_type = 'product_inventory'
                    ORDER BY id DESC LIMIT 1
                    """
                ).fetchone()
        if not row:
            return {"ok": False, "error": "هیچ منبع اصلی یافت نشد.", "counts": {}}
        extract = dict(row)
        counts = self.seed_catalog_from_inventory_extract(extract["clean_path"])
        return {"ok": True, "extract": extract, "counts": counts}

    # --- catalog group assignments ---
    def assign_item_to_group(
        self,
        item_id: str,
        tundish_group: str,
        assigned_by: str | int | None = None,
    ) -> dict[str, Any]:
        group = (tundish_group or "").strip().lower()
        if group not in SITE_STOCK_GROUP_KEYS:
            raise ValueError("گروه نامعتبر. یکی از: slab / bloom / billet")
        iid = str(item_id).strip()
        item = self.get_catalog_item(iid)
        if not item or not item.get("active"):
            raise KeyError("کالا در کاتالوگ یافت نشد یا غیرفعال است.")
        now = _utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO catalog_group_assignments
                    (item_id, tundish_group, assigned_by, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    tundish_group = excluded.tundish_group,
                    assigned_by = excluded.assigned_by,
                    updated_at = excluded.updated_at
                """,
                (
                    iid,
                    group,
                    str(assigned_by) if assigned_by is not None else None,
                    now,
                ),
            )
        return self.get_item_assignment(iid)  # type: ignore[return-value]

    def unassign_item(self, item_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM catalog_group_assignments WHERE item_id = ?",
                (str(item_id).strip(),),
            )

    def get_item_assignment(self, item_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT a.*, c.name_desc, c.category_code, c.active AS item_active
                FROM catalog_group_assignments a
                JOIN catalog_items c ON c.id = a.item_id
                WHERE a.item_id = ?
                """,
                (str(item_id).strip(),),
            ).fetchone()
            return dict(row) if row else None

    def list_items_for_group(
        self, tundish_group: str, active_only: bool = True
    ) -> list[dict[str, Any]]:
        group = (tundish_group or "").strip().lower()
        if group not in SITE_STOCK_GROUP_KEYS:
            raise ValueError("گروه نامعتبر.")
        q = """
            SELECT c.id, c.name_desc, c.category_code, c.active,
                   a.tundish_group, a.assigned_by, a.updated_at AS assigned_at
            FROM catalog_group_assignments a
            JOIN catalog_items c ON c.id = a.item_id
            WHERE a.tundish_group = ?
        """
        if active_only:
            q += " AND c.active = 1"
        q += " ORDER BY c.name_desc COLLATE NOCASE, c.id"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(q, (group,)).fetchall()]

    def list_catalog_with_assignments(
        self, active_only: bool = True
    ) -> list[dict[str, Any]]:
        q = """
            SELECT c.id, c.name_desc, c.category_code, c.active,
                   a.tundish_group, a.assigned_by, a.updated_at AS assigned_at
            FROM catalog_items c
            LEFT JOIN catalog_group_assignments a ON a.item_id = c.id
        """
        if active_only:
            q += " WHERE c.active = 1"
        q += " ORDER BY (a.tundish_group IS NULL) DESC, a.tundish_group, c.name_desc COLLATE NOCASE, c.id"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(q).fetchall()]

    def list_unassigned_catalog_items(
        self, active_only: bool = True
    ) -> list[dict[str, Any]]:
        q = """
            SELECT c.*
            FROM catalog_items c
            LEFT JOIN catalog_group_assignments a ON a.item_id = c.id
            WHERE a.item_id IS NULL
        """
        if active_only:
            q += " AND c.active = 1"
        q += " ORDER BY c.name_desc COLLATE NOCASE, c.id"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(q).fetchall()]

    def sync_catalog_groups_from_work_order_map(
        self,
        item_to_group: dict[str, str],
        *,
        only_unassigned_or_auto: bool = True,
        assigned_by: str | None = None,
    ) -> dict[str, int]:
        """Assign catalog items to slab/bloom/billet from a work_order-derived map.

        By default only fills unassigned rows, or overwrites previous auto
        assignments (assigned_by == system:work_order). Manual assignments win.
        """
        from excel.work_order import WO_AUTO_ASSIGNED_BY

        actor = assigned_by if assigned_by is not None else WO_AUTO_ASSIGNED_BY
        assigned = skipped_missing = skipped_manual = skipped_same = 0
        for raw_id, group in (item_to_group or {}).items():
            iid = str(raw_id).strip()
            g = (group or "").strip().lower()
            if not iid or g not in SITE_STOCK_GROUP_KEYS:
                continue
            item = self.get_catalog_item(iid)
            if not item or not item.get("active"):
                skipped_missing += 1
                continue
            current = self.get_item_assignment(iid)
            if current:
                cur_g = (current.get("tundish_group") or "").strip().lower()
                cur_by = (current.get("assigned_by") or "").strip()
                if only_unassigned_or_auto:
                    if cur_by and cur_by != WO_AUTO_ASSIGNED_BY:
                        skipped_manual += 1
                        continue
                if cur_g == g:
                    skipped_same += 1
                    continue
            self.assign_item_to_group(iid, g, assigned_by=actor)
            assigned += 1
        return {
            "assigned": assigned,
            "skipped_missing": skipped_missing,
            "skipped_manual": skipped_manual,
            "skipped_same": skipped_same,
            "mapped": len(item_to_group or {}),
        }

    def sync_catalog_groups_from_monthly_path(
        self,
        monthly_path: str | Path,
        *,
        only_unassigned_or_auto: bool = True,
    ) -> dict[str, Any]:
        """Build item→group from a monthly Excel and sync catalog assignments."""
        from pathlib import Path as _Path

        from excel.monthly_summary import load_monthly_detail
        from excel.work_order import build_item_to_group_map

        path = _Path(monthly_path)
        if not path.exists():
            return {"ok": False, "error": f"فایل یافت نشد: {path}", "counts": {}}
        try:
            detail = load_monthly_detail(path)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "counts": {}}
        # weight by |qty * coeff| for dominant WO per item
        work = detail.copy()
        import pandas as pd

        qty = pd.to_numeric(work["مقدار"], errors="coerce").fillna(0).abs() if "مقدار" in work.columns else 0
        coef = (
            pd.to_numeric(work["ضریب"], errors="coerce").fillna(1.0).abs()
            if "ضریب" in work.columns
            else 1.0
        )
        work["_w"] = qty * coef
        mapping = build_item_to_group_map(
            work, id_col="کد کالا", work_order_col="سفارش کار", weight_col="_w"
        )
        counts = self.sync_catalog_groups_from_work_order_map(
            mapping, only_unassigned_or_auto=only_unassigned_or_auto
        )
        return {"ok": True, "mapping_size": len(mapping), "counts": counts, "path": str(path)}

    def sync_catalog_groups_from_latest_monthly(
        self, bale_user_id: str | int | None = None
    ) -> dict[str, Any]:
        """Use newest monthly_consumption extract (prefer plant raw when present)."""
        with self.connect() as conn:
            if bale_user_id is not None:
                row = conn.execute(
                    """
                    SELECT * FROM extracted_datasets
                    WHERE file_type = 'monthly_consumption' AND bale_user_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (str(bale_user_id),),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM extracted_datasets
                    WHERE file_type = 'monthly_consumption'
                    ORDER BY id DESC LIMIT 1
                    """
                ).fetchone()
        if not row:
            return {
                "ok": False,
                "error": "هیچ استخراج مصرف ماهیانه‌ای یافت نشد.",
                "counts": {},
            }
        extract = dict(row)
        # Prefer raw plant file (has سفارش کار sheet); fall back to clean
        candidates = [extract.get("raw_path"), extract.get("clean_path")]
        last_err = None
        for cand in candidates:
            if not cand:
                continue
            result = self.sync_catalog_groups_from_monthly_path(cand)
            if result.get("ok"):
                result["extract"] = extract
                return result
            last_err = result.get("error")
        return {
            "ok": False,
            "error": last_err or "همگام‌سازی از مصرف ماهیانه ناموفق بود.",
            "counts": {},
            "extract": extract,
        }

    # --- site stock daily entries ---
    @staticmethod
    def tehran_today() -> str:
        """Asia/Tehran calendar date as YYYY-MM-DD."""
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo("Asia/Tehran")).date().isoformat()
        except Exception:
            # fallback: use local date (box is configured Asia/Tehran)
            return datetime.now().date().isoformat()

    def upsert_site_stock_entry(
        self,
        *,
        bale_user_id: str | int,
        tundish_group: str,
        item_id: str,
        quantity: float,
        item_name_snapshot: str | None = None,
        actor_display_name: str | None = None,
        pallet_qty: float | None = None,
        unit_qty: float | None = None,
        quantity_detail: dict | list | None = None,
        entry_date: str | None = None,
        session_id: int | None = None,
    ) -> dict[str, Any]:
        group = (tundish_group or "").strip().lower()
        if group not in SITE_STOCK_GROUP_KEYS:
            raise ValueError("گروه نامعتبر.")
        iid = str(item_id).strip()
        item = self.get_catalog_item(iid)
        if not item:
            raise KeyError("کالا در کاتالوگ یافت نشد.")
        name = (item_name_snapshot or item.get("name_desc") or iid).strip()
        try:
            qty = float(quantity)
        except (TypeError, ValueError) as exc:
            raise ValueError("مقدار باید عدد باشد.") from exc
        day = (entry_date or self.tehran_today()).strip()
        detail_json = (
            json.dumps(quantity_detail, ensure_ascii=False)
            if quantity_detail is not None
            else None
        )
        now = _utcnow()
        with self.connect() as conn:
            actor_name = (actor_display_name or "").strip() or None
            conn.execute(
                """
                INSERT INTO site_stock_entries
                    (bale_user_id, tundish_group, item_id, item_name_snapshot,
                     quantity, pallet_qty, unit_qty, quantity_detail,
                     entry_date, created_at, session_id, actor_display_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_date, tundish_group, item_id) DO UPDATE SET
                    bale_user_id = excluded.bale_user_id,
                    item_name_snapshot = excluded.item_name_snapshot,
                    quantity = excluded.quantity,
                    pallet_qty = excluded.pallet_qty,
                    unit_qty = excluded.unit_qty,
                    quantity_detail = excluded.quantity_detail,
                    created_at = excluded.created_at,
                    session_id = COALESCE(excluded.session_id, site_stock_entries.session_id),
                    actor_display_name = COALESCE(excluded.actor_display_name, site_stock_entries.actor_display_name)
                """,
                (
                    str(bale_user_id),
                    group,
                    iid,
                    name,
                    qty,
                    pallet_qty,
                    unit_qty,
                    detail_json,
                    day,
                    now,
                    session_id,
                    actor_name,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM site_stock_entries
                WHERE entry_date = ? AND tundish_group = ? AND item_id = ?
                """,
                (day, group, iid),
            ).fetchone()
        return dict(row) if row else {}

    def list_site_stock_entries(
        self,
        *,
        entry_date: str | None = None,
        tundish_group: str | None = None,
        item_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if entry_date:
            clauses.append("entry_date = ?")
            params.append(entry_date)
        if tundish_group:
            group = tundish_group.strip().lower()
            if group not in SITE_STOCK_GROUP_KEYS:
                raise ValueError("گروه نامعتبر.")
            clauses.append("tundish_group = ?")
            params.append(group)
        if item_id:
            clauses.append("item_id = ?")
            params.append(str(item_id).strip())
        q = "SELECT * FROM site_stock_entries"
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY tundish_group, item_name_snapshot, item_id"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(q, params).fetchall()]

    def get_latest_site_stock_date(self) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT entry_date FROM site_stock_entries ORDER BY entry_date DESC LIMIT 1"
            ).fetchone()
            return row["entry_date"] if row else None

    def site_stock_as_remaining_rows(
        self, entry_date: str | None = None
    ) -> list[dict[str, Any]]:
        """Rows compatible with analytics.remaining() input (product_name/quantity)."""
        day = entry_date or self.get_latest_site_stock_date()
        if not day:
            return []
        entries = self.list_site_stock_entries(entry_date=day)
        return [
            {
                "product_name": e.get("item_name_snapshot") or e["item_id"],
                "quantity": e["quantity"],
                "id": e["item_id"],
                "location": e["tundish_group"],
                "unit": None,
            }
            for e in entries
        ]



    # --- material requests + inventory ledger ---
    def create_material_request(
        self,
        bale_user_id: str | int,
        *,
        actor_display_name: str | None,
        coverage_days: float | int,
        lines: list[dict[str, Any]],
        status: str = "confirmed",
    ) -> dict[str, Any]:
        """Persist a confirmed material request + lines and inventory_ledger deductions.

        Each line dict may include: item_id, item_name, unit, avg_daily,
        remaining_qty, quantity (requested qty). Quantity <= 0 is skipped.
        """
        uid = str(bale_user_id)
        now = _utcnow()
        days = float(coverage_days)
        kept = [
            ln
            for ln in (lines or [])
            if float(ln.get("quantity") or 0) > 0 and str(ln.get("item_name") or "").strip()
        ]
        if not kept:
            raise ValueError("هیچ قلمی با مقدار مثبت برای ثبت وجود ندارد.")
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO material_requests
                    (bale_user_id, actor_display_name, coverage_days, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (uid, actor_display_name, days, status, now),
            )
            request_id = int(cur.lastrowid)
            for ln in kept:
                item_id = str(ln.get("item_id") or "").strip() or None
                item_name = str(ln.get("item_name") or "").strip()
                qty = float(ln["quantity"])
                unit = ln.get("unit")
                avg_daily = ln.get("avg_daily")
                rem_qty = ln.get("remaining_qty")
                conn.execute(
                    """
                    INSERT INTO material_request_lines
                        (request_id, item_id, item_name, unit, avg_daily,
                         remaining_qty, quantity)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        item_id,
                        item_name,
                        str(unit) if unit is not None else None,
                        float(avg_daily) if avg_daily is not None else None,
                        float(rem_qty) if rem_qty is not None else None,
                        qty,
                    ),
                )
                item_key = item_id or item_name
                conn.execute(
                    """
                    INSERT INTO inventory_ledger
                        (item_key, item_id, item_name, delta, reason,
                         ref_type, ref_id, bale_user_id, actor_display_name, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_key,
                        item_id,
                        item_name,
                        -qty,
                        "material_request",
                        "material_request",
                        request_id,
                        uid,
                        actor_display_name,
                        now,
                    ),
                )
        return self.get_material_request(request_id)  # type: ignore[return-value]

    def get_material_request(self, request_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM material_requests WHERE id = ?", (int(request_id),)
            ).fetchone()
            if not row:
                return None
            header = dict(row)
            lines = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT * FROM material_request_lines
                    WHERE request_id = ?
                    ORDER BY id
                    """,
                    (int(request_id),),
                ).fetchall()
            ]
        header["lines"] = lines
        return header

    def list_material_requests(self, limit: int = 10) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 50))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*,
                       (SELECT COUNT(*) FROM material_request_lines l
                        WHERE l.request_id = r.id) AS line_count,
                       (SELECT COALESCE(SUM(l.quantity), 0) FROM material_request_lines l
                        WHERE l.request_id = r.id) AS total_qty
                FROM material_requests r
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        return [dict(r) for r in rows]


    def create_warehouse_return(
        self,
        bale_user_id: str | int,
        *,
        actor_display_name: str | None,
        lines: list[dict[str, Any]],
        status: str = "confirmed",
    ) -> dict[str, Any]:
        """Persist confirmed return-to-warehouse + positive inventory_ledger deltas."""
        uid = str(bale_user_id)
        now = _utcnow()
        kept = [
            ln
            for ln in (lines or [])
            if float(ln.get("quantity") or 0) > 0 and str(ln.get("item_name") or "").strip()
        ]
        if not kept:
            raise ValueError("هیچ قلمی با مقدار مثبت برای برگشت وجود ندارد.")
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO warehouse_returns
                    (bale_user_id, actor_display_name, status, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (uid, actor_display_name, status, now),
            )
            return_id = int(cur.lastrowid)
            for ln in kept:
                item_id = str(ln.get("item_id") or "").strip() or None
                item_name = str(ln.get("item_name") or "").strip()
                qty = float(ln["quantity"])
                conn.execute(
                    """
                    INSERT INTO warehouse_return_lines
                        (return_id, item_id, item_name, unit, site_qty,
                         surplus_qty, quantity, surplus_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        return_id,
                        item_id,
                        item_name,
                        str(ln["unit"]) if ln.get("unit") is not None else None,
                        float(ln["site_qty"]) if ln.get("site_qty") is not None else None,
                        float(ln["surplus_qty"]) if ln.get("surplus_qty") is not None else None,
                        qty,
                        str(ln["surplus_reason"]) if ln.get("surplus_reason") is not None else None,
                    ),
                )
                item_key = item_id or item_name
                conn.execute(
                    """
                    INSERT INTO inventory_ledger
                        (item_key, item_id, item_name, delta, reason,
                         ref_type, ref_id, bale_user_id, actor_display_name, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_key,
                        item_id,
                        item_name,
                        qty,  # positive: add back to warehouse
                        "return_to_warehouse",
                        "warehouse_return",
                        return_id,
                        uid,
                        actor_display_name,
                        now,
                    ),
                )
        return self.get_warehouse_return(return_id)  # type: ignore[return-value]

    def get_warehouse_return(self, return_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM warehouse_returns WHERE id = ?", (int(return_id),)
            ).fetchone()
            if not row:
                return None
            header = dict(row)
            lines = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT * FROM warehouse_return_lines
                    WHERE return_id = ?
                    ORDER BY id
                    """,
                    (int(return_id),),
                ).fetchall()
            ]
        header["lines"] = lines
        return header

    def list_warehouse_returns(self, limit: int = 10) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 50))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*,
                       (SELECT COUNT(*) FROM warehouse_return_lines l
                        WHERE l.return_id = r.id) AS line_count,
                       (SELECT COALESCE(SUM(l.quantity), 0) FROM warehouse_return_lines l
                        WHERE l.return_id = r.id) AS total_qty
                FROM warehouse_returns r
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        return [dict(r) for r in rows]

    def inventory_ledger_sums(self) -> dict[str, dict[str, float]]:
        """Aggregate ledger deltas keyed by item_id and by item_name (casefold)."""
        by_id: dict[str, float] = {}
        by_name: dict[str, float] = {}
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT item_id, item_name, item_key, SUM(delta) AS total_delta
                FROM inventory_ledger
                GROUP BY item_id, item_name, item_key
                """
            ).fetchall()
        for r in rows:
            delta = float(r["total_delta"] or 0)
            iid = str(r["item_id"] or "").strip()
            name = str(r["item_name"] or "").strip()
            key = str(r["item_key"] or "").strip()
            if iid:
                by_id[iid] = by_id.get(iid, 0.0) + delta
            elif key and key != name:
                by_id[key] = by_id.get(key, 0.0) + delta
            if name:
                nkey = name.casefold()
                by_name[nkey] = by_name.get(nkey, 0.0) + delta
            elif key:
                by_name[key.casefold()] = by_name.get(key.casefold(), 0.0) + delta
        return {"by_id": by_id, "by_name": by_name}


    # --- bot settings (invite / welcome / logo / letterhead) ---
    BOT_SETTING_KEYS = frozenset({
        "invite_text",
        "invite_image_path",
        "welcome_text",
        "welcome_image_path",
        "logo_path",
        "letterhead_pdf",
    })

    def get_setting(self, key: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key = ?", (str(key),)
            ).fetchone()
        if not row:
            return None
        value = row["value"]
        if value is None:
            return None
        value = str(value)
        return value if value.strip() else None

    def set_setting(
        self,
        key: str,
        value: str | None,
        updated_by: str | int | None = None,
    ) -> None:
        key = str(key)
        now = _utcnow()
        by = str(updated_by) if updated_by is not None else None
        with self.connect() as conn:
            # Empty / None clears the key so callers fall back to defaults.
            if value is None or not str(value).strip():
                conn.execute("DELETE FROM bot_settings WHERE key = ?", (key,))
                return
            conn.execute(
                """
                INSERT INTO bot_settings (key, value, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (key, str(value), now, by),
            )

    def clear_setting(self, key: str, updated_by: str | int | None = None) -> None:
        """Remove a setting key (used for clearing images)."""
        self.set_setting(key, None, updated_by=updated_by)

    def get_bot_settings(self) -> dict[str, Optional[str]]:
        """Return known bot setting keys (missing → None)."""
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM bot_settings").fetchall()
        found = {r["key"]: (str(r["value"]) if r["value"] is not None else None) for r in rows}
        out: dict[str, Optional[str]] = {}
        for key in self.BOT_SETTING_KEYS:
            val = found.get(key)
            if val is not None and not str(val).strip():
                val = None
            out[key] = val
        return out

    # ---------- web credentials (dashboard login) ----------
    def get_web_credential_by_username(self, username: str) -> Optional[dict[str, Any]]:
        uname = (username or "").strip()
        if not uname:
            return None
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT w.*, u.display_name, u.role, u.scope, u.active
                FROM web_credentials w
                JOIN users u ON u.bale_user_id = w.bale_user_id
                WHERE w.username = ? COLLATE NOCASE
                """,
                (uname,),
            ).fetchone()
            return dict(row) if row else None

    def get_web_credential_by_bale_id(self, bale_user_id: str | int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM web_credentials WHERE bale_user_id = ?",
                (str(bale_user_id),),
            ).fetchone()
            return dict(row) if row else None

    def list_web_credentials(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT w.bale_user_id, w.username, w.created_at, w.updated_at,
                       u.display_name, u.role, u.active
                FROM web_credentials w
                JOIN users u ON u.bale_user_id = w.bale_user_id
                ORDER BY w.username COLLATE NOCASE
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def upsert_web_credential(
        self,
        *,
        bale_user_id: str | int,
        username: str,
        password_hash: str,
    ) -> dict[str, Any]:
        uid = str(bale_user_id).strip()
        uname = (username or "").strip()
        if not uid or not uname or not password_hash:
            raise ValueError("شناسه کاربر، نام کاربری و رمز الزامی است.")
        user = self.get_user(uid)
        if not user:
            raise KeyError("کاربر بله یافت نشد.")
        now = _utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO web_credentials
                    (bale_user_id, username, password_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(bale_user_id) DO UPDATE SET
                    username = excluded.username,
                    password_hash = excluded.password_hash,
                    updated_at = excluded.updated_at
                """,
                (uid, uname, password_hash, now, now),
            )
            row = conn.execute(
                "SELECT * FROM web_credentials WHERE bale_user_id = ?",
                (uid,),
            ).fetchone()
        return dict(row) if row else {}

    def count_web_credentials(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM web_credentials").fetchone()
            return int(row["c"] if row else 0)

    def get_latest_extracted_any(self, file_type: str) -> Optional[dict[str, Any]]:
        """Plant-wide newest extract for a file type (any user)."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM extracted_datasets
                WHERE file_type = ?
                ORDER BY id DESC LIMIT 1
                """,
                (str(file_type),),
            ).fetchone()
            return dict(row) if row else None

    # ---------- user activity log ----------

    def insert_user_activity(
        self,
        *,
        bale_user_id: str | int,
        display_name: str | None,
        action_key: str,
        message_fa: str,
        details: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> int:
        """Insert one activity row; returns new id."""
        now = created_at or _utcnow()
        details_json = json.dumps(details, ensure_ascii=False) if details else None
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO user_activity
                    (bale_user_id, display_name, action_key, message_fa, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(bale_user_id),
                    (display_name or None),
                    str(action_key),
                    str(message_fa),
                    details_json,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def list_user_activities(
        self,
        *,
        start_iso: str | None = None,
        end_iso: str | None = None,
        newest_first: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """List activity rows filtered by created_at ISO range (UTC)."""
        clauses: list[str] = []
        params: list[Any] = []
        if start_iso:
            clauses.append("created_at >= ?")
            params.append(start_iso)
        if end_iso:
            clauses.append("created_at <= ?")
            params.append(end_iso)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        order = "DESC" if newest_first else "ASC"
        lim = f" LIMIT {int(limit)}" if limit else ""
        sql = f"""
            SELECT id, bale_user_id, display_name, action_key, message_fa,
                   details_json, created_at
            FROM user_activity
            {where}
            ORDER BY created_at {order}, id {order}
            {lim}
        """
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            raw = item.pop("details_json", None)
            if raw:
                try:
                    item["details"] = json.loads(raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    item["details"] = None
            else:
                item["details"] = None
            out.append(item)
        return out
