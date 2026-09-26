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
            cols.get("item_code_desc")
            or cols.get("product_name")
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
            return {"ok": False, "error": "هیچ استخراج موجودی انباری یافت نشد.", "counts": {}}
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

