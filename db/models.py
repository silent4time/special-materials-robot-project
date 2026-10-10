"""SQLite persistence for users, roles, scopes, sessions, extracts, category codes, and reports."""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator, Iterable, Optional

from config import (
    DATABASE_PATH,
    DEFAULT_CATEGORY_CODES,
    DEFAULT_CATEGORY_LABELS,
    ROLES,
    SITE_STOCK_GROUP_KEYS,
    SITE_STOCK_GROUP_SQL,
    SURPLUS_CATEGORY_CODE,
    SURPLUS_CATEGORY_LABEL,
    ensure_dirs,
)


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
                        CHECK(tundish_group IN ({SITE_GROUP_SQL})),
                    assigned_by TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES catalog_items(id)
                );

                CREATE TABLE IF NOT EXISTS site_stock_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bale_user_id TEXT NOT NULL,
                    tundish_group TEXT NOT NULL
                        CHECK(tundish_group IN ({SITE_GROUP_SQL})),
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

                CREATE TABLE IF NOT EXISTS monthly_tundish_counts (
                    jalali_year INTEGER NOT NULL,
                    jalali_month INTEGER NOT NULL,
                    count_billet INTEGER NOT NULL DEFAULT 0,
                    count_bloom INTEGER NOT NULL DEFAULT 0,
                    count_slab INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT,
                    PRIMARY KEY (jalali_year, jalali_month)
                );
                CREATE INDEX IF NOT EXISTS idx_monthly_tundish_counts_updated
                    ON monthly_tundish_counts(updated_at DESC);

                -- گزارش تاندیش بعد از ریخته‌گری: configurable items per section
                CREATE TABLE IF NOT EXISTS tundish_report_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    section TEXT NOT NULL CHECK(section IN ('slab','bloom','billet')),
                    label TEXT NOT NULL,
                    item_type TEXT NOT NULL CHECK(item_type IN ('choice','number','text')),
                    options_json TEXT,
                    required INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tundish_report_items_section
                    ON tundish_report_items(section, sort_order, id);

                -- One row per submitted post-casting tundish report
                CREATE TABLE IF NOT EXISTS tundish_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    section TEXT NOT NULL,
                    line TEXT,
                    tundish_no TEXT,
                    sequence INTEGER,
                    melt_count INTEGER,
                    steel_grade TEXT,
                    fields_json TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    summary_text TEXT,
                    raw_text TEXT,
                    source TEXT NOT NULL DEFAULT 'bot',
                    bale_user_id TEXT NOT NULL,
                    actor_display_name TEXT,
                    created_at TEXT NOT NULL,
                    created_at_tehran TEXT NOT NULL,
                    jalali_date TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tundish_reports_created
                    ON tundish_reports(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_tundish_reports_section
                    ON tundish_reports(section, created_at DESC);

                -- Normalized item answers (label/type snapshot at submit time)
                CREATE TABLE IF NOT EXISTS tundish_report_values (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id INTEGER NOT NULL REFERENCES tundish_reports(id) ON DELETE CASCADE,
                    item_id INTEGER,
                    label TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    value_text TEXT,
                    value_num REAL,
                    sort_order INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_tundish_report_values_report
                    ON tundish_report_values(report_id);

                -- گزارش هدف اصلی: uploads + periods + computed results
                CREATE TABLE IF NOT EXISTS main_goal_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_key TEXT,
                    period_label TEXT,
                    period_json TEXT,
                    files_json TEXT NOT NULL,
                    results_json TEXT,
                    summary_text TEXT,
                    target_tons REAL,
                    source TEXT NOT NULL DEFAULT 'bot',
                    bale_user_id TEXT NOT NULL,
                    actor_display_name TEXT,
                    created_at TEXT NOT NULL,
                    created_at_tehran TEXT NOT NULL,
                    jalali_date TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_main_goal_reports_created
                    ON main_goal_reports(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_main_goal_reports_period
                    ON main_goal_reports(period_key, created_at DESC);

                -- گزارش هدف اصلی: سابقهٔ ماهانه (هر ماه = ۴ فایل با بازهٔ یکسان)
                CREATE TABLE IF NOT EXISTS main_goal_months (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_key TEXT NOT NULL UNIQUE,
                    period_label TEXT NOT NULL,
                    period_kind TEXT NOT NULL DEFAULT 'month',
                    year INTEGER,
                    month INTEGER,
                    sort_key TEXT NOT NULL,
                    files_json TEXT NOT NULL,
                    stats_json TEXT NOT NULL,
                    summary_text TEXT,
                    source TEXT NOT NULL DEFAULT 'bot',
                    bale_user_id TEXT NOT NULL,
                    actor_display_name TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_at_tehran TEXT NOT NULL,
                    jalali_date TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_main_goal_months_sort
                    ON main_goal_months(sort_key);

                -- Normalized production inputs (photo OCR / Excel / manual)
                CREATE TABLE IF NOT EXISTS main_goal_production (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_key TEXT NOT NULL UNIQUE,
                    period_label TEXT NOT NULL,
                    year INTEGER,
                    month INTEGER,
                    sort_key TEXT NOT NULL,
                    slab_tons REAL NOT NULL DEFAULT 0,
                    bloom_tons REAL NOT NULL DEFAULT 0,
                    billet_tons REAL NOT NULL DEFAULT 0,
                    total_tons REAL NOT NULL DEFAULT 0,
                    melt_count REAL,
                    melt_weight_kg REAL,
                    product_weight_kg REAL,
                    slab_count REAL,
                    bloom_billet_count REAL,
                    melts_per_day REAL,
                    report_tab TEXT,
                    source_status TEXT,
                    slab_melt_count REAL,
                    bloom_melt_count REAL,
                    billet_melt_count REAL,
                    ccm1_tons REAL,
                    ccm2_tons REAL,
                    ccm3_tons REAL,
                    ccm4_tons REAL,
                    ccm5_tons REAL,
                    source_type TEXT NOT NULL DEFAULT 'ocr',
                    source_path TEXT,
                    source_filename TEXT,
                    ocr_raw_text TEXT,
                    ocr_confidence REAL,
                    ocr_fields_json TEXT,
                    manual_corrected INTEGER NOT NULL DEFAULT 0,
                    notes_json TEXT,
                    missing_json TEXT,
                    bale_user_id TEXT NOT NULL,
                    actor_display_name TEXT,
                    source TEXT NOT NULL DEFAULT 'bot',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_at_tehran TEXT NOT NULL,
                    jalali_date TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_main_goal_production_sort
                    ON main_goal_production(sort_key);

                -- Normalized tundish consumption per section (from Excel)
                CREATE TABLE IF NOT EXISTS main_goal_consumption (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_key TEXT NOT NULL,
                    period_label TEXT NOT NULL,
                    year INTEGER,
                    month INTEGER,
                    sort_key TEXT NOT NULL,
                    section TEXT NOT NULL,
                    tundish_count REAL,
                    melt_count REAL,
                    patch_count REAL,
                    renovate_count REAL,
                    source_path TEXT,
                    source_filename TEXT,
                    notes_json TEXT,
                    missing_json TEXT,
                    bale_user_id TEXT NOT NULL,
                    actor_display_name TEXT,
                    source TEXT NOT NULL DEFAULT 'bot',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_at_tehran TEXT NOT NULL,
                    jalali_date TEXT NOT NULL,
                    UNIQUE(period_key, section)
                );
                CREATE INDEX IF NOT EXISTS idx_main_goal_consumption_period
                    ON main_goal_consumption(period_key, section);
                CREATE INDEX IF NOT EXISTS idx_main_goal_consumption_sort
                    ON main_goal_consumption(sort_key);

                CREATE TABLE IF NOT EXISTS main_goal_consumption_materials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    consumption_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    quantity REAL NOT NULL DEFAULT 0,
                    unit TEXT NOT NULL DEFAULT 'kg',
                    item_id TEXT,
                    keyword TEXT,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (consumption_id) REFERENCES main_goal_consumption(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_main_goal_cons_mats
                    ON main_goal_consumption_materials(consumption_id);

                CREATE TABLE IF NOT EXISTS main_goal_tundish_sequences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_key TEXT NOT NULL,
                    period_label TEXT NOT NULL,
                    year INTEGER,
                    month INTEGER,
                    section TEXT NOT NULL,
                    machine TEXT,
                    tundish_no TEXT,
                    operator TEXT,
                    melt_count REAL,
                    sequence_minutes REAL,
                    isg TEXT,
                    first_melt_no TEXT,
                    first_cast_start TEXT,
                    last_melt_no TEXT,
                    last_cast_end TEXT,
                    shroud_replaced INTEGER NOT NULL DEFAULT 0,
                    outer_nozzle_replaced INTEGER NOT NULL DEFAULT 0,
                    tube_changer INTEGER NOT NULL DEFAULT 0,
                    source_path TEXT,
                    source_filename TEXT,
                    source TEXT NOT NULL DEFAULT 'bot',
                    bale_user_id TEXT NOT NULL,
                    actor_display_name TEXT,
                    created_at TEXT NOT NULL,
                    jalali_date TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_main_goal_seq_period
                    ON main_goal_tundish_sequences(period_key, section);
                """.replace("{SITE_GROUP_SQL}", SITE_STOCK_GROUP_SQL)
            )
            self._migrate_users_role_check(conn)
            self._migrate_add_columns(conn)
            self._migrate_site_group_check(conn)
            self._ensure_default_category_codes(conn)
            self._ensure_tundish_report_seed(conn)

    def _ensure_default_category_codes(self, conn: sqlite3.Connection) -> None:
        """Insert missing DEFAULT_CATEGORY_CODES; backfill reserved surplus label.

        Does not overwrite non-empty labels or reactivate deactivated codes.
        For code 1800 (SURPLUS_CATEGORY_CODE): if missing → INSERT with label
        «اقلام مازاد»; if present with NULL/empty label → set the surplus label.
        """
        now = _utcnow()
        for code in DEFAULT_CATEGORY_CODES:
            label = DEFAULT_CATEGORY_LABELS.get(code)
            conn.execute(
                """
                INSERT OR IGNORE INTO category_codes (code, label, active, created_at, created_by)
                VALUES (?, ?, 1, ?, ?)
                """,
                (code, label, now, "bootstrap"),
            )
        # Existing DBs: backfill empty/NULL label for reserved surplus category.
        surplus_label = DEFAULT_CATEGORY_LABELS.get(
            SURPLUS_CATEGORY_CODE, SURPLUS_CATEGORY_LABEL
        )
        conn.execute(
            """
            UPDATE category_codes
            SET label = ?
            WHERE code = ?
              AND (label IS NULL OR TRIM(label) = '')
            """,
            (surplus_label, SURPLUS_CATEGORY_CODE),
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

    def _migrate_site_group_check(self, conn: sqlite3.Connection) -> None:
        """Widen CHECK(tundish_group IN …) on site-stock tables to all SITE_STOCK_GROUPS
        (adds the سطح ریخته‌گری groups). Rebuilds the table, keeping rows + indexes."""
        import re as _re

        want = f"CHECK(tundish_group IN ({SITE_STOCK_GROUP_SQL}))"
        for table in ("catalog_group_assignments", "site_stock_entries"):
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not row or not row[0] or want in row[0]:
                continue
            sql = _re.sub(r"CHECK\(tundish_group IN \([^)]*\)\)", want, row[0], count=1)
            sql = _re.sub(rf"CREATE TABLE\s+\"?{table}\"?", f"CREATE TABLE {table}__new", sql, count=1)
            idx = [
                r[0] for r in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                    (table,),
                ).fetchall()
            ]
            cols = ", ".join(
                f'"{r[1]}"' for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
            )
            conn.commit()
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                conn.execute("BEGIN")
                conn.execute(sql)
                conn.execute(f"INSERT INTO {table}__new ({cols}) SELECT {cols} FROM {table}")
                conn.execute(f"DROP TABLE {table}")
                conn.execute(f"ALTER TABLE {table}__new RENAME TO {table}")
                for isql in idx:
                    conn.execute(isql)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")

    def _migrate_add_columns(self, conn: sqlite3.Connection) -> None:
        """Safe ALTER TABLE ADD COLUMN for older DBs."""
        additions = [
            ("catalog_group_assignments", "assigned_by", "TEXT"),
            ("reports", "created_by", "TEXT"),
            ("site_stock_entries", "actor_display_name", "TEXT"),
            ("main_goal_production", "melt_weight_kg", "REAL"),
            ("main_goal_production", "product_weight_kg", "REAL"),
            ("main_goal_production", "slab_count", "REAL"),
            ("main_goal_production", "bloom_billet_count", "REAL"),
            ("main_goal_production", "melts_per_day", "REAL"),
            ("main_goal_production", "report_tab", "TEXT"),
            ("main_goal_production", "source_status", "TEXT"),
            ("main_goal_production", "slab_melt_count", "REAL"),
            ("main_goal_production", "bloom_melt_count", "REAL"),
            ("main_goal_production", "billet_melt_count", "REAL"),
            # اقلام بحرانی basis: a manual entry can be kept for audit but excluded
            # (e.g. the Shahrivar 1405 SAMPLE 70/100, superseded by the sequence log).
            ("monthly_tundish_counts", "exclude_from_basis", "INTEGER NOT NULL DEFAULT 0"),
            ("monthly_tundish_counts", "basis_note", "TEXT"),
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
        # Production photos must come from the CASTING tab only (user decision 1405-07).
        # Legacy furnace-tab rows are kept for audit but flagged provisional and
        # excluded from section-tonnage calculations (see services.main_goal_persist).
        try:
            conn.execute(
                """
                UPDATE main_goal_production
                SET source_status = 'furnace_tab_provisional'
                WHERE report_tab = 'furnace'
                  AND COALESCE(source_status, '') <> 'furnace_tab_provisional'
                """
            )
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

    def get_extracted_before(
        self, extract_id: int, file_type: str
    ) -> Optional[dict[str, Any]]:
        """Plant-wide previous extract with id strictly less than ``extract_id``."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM extracted_datasets
                WHERE file_type = ? AND id < ?
                ORDER BY id DESC LIMIT 1
                """,
                (str(file_type), int(extract_id)),
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

    # Daily-stock input lines generated from منبع اصلی (services.site_stock_lists) use
    # synthetic ids «SS:…»; they are not warehouse items, so catalog listings
    # (material requests, catalog menus) skip them unless asked.
    _SITE_LINE_SQL = "id NOT LIKE 'SS:%'"

    def list_catalog_items(
        self, active_only: bool = True, *, include_site_lines: bool = False
    ) -> list[dict[str, Any]]:
        conds = []
        if active_only:
            conds.append("active = 1")
        if not include_site_lines:
            conds.append(self._SITE_LINE_SQL)
        q = "SELECT * FROM catalog_items"
        if conds:
            q += " WHERE " + " AND ".join(conds)
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
        self, active_only: bool = True, *, include_site_lines: bool = False
    ) -> list[dict[str, Any]]:
        q = """
            SELECT c.id, c.name_desc, c.category_code, c.active,
                   a.tundish_group, a.assigned_by, a.updated_at AS assigned_at
            FROM catalog_items c
            LEFT JOIN catalog_group_assignments a ON a.item_id = c.id
            WHERE 1 = 1
        """
        if active_only:
            q += " AND c.active = 1"
        if not include_site_lines:
            q += " AND c.id NOT LIKE 'SS:%'"
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
            WHERE a.item_id IS NULL AND c.id NOT LIKE 'SS:%'
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
        "site_stock_report_group_id",
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


    # ---------- monthly tundish counts (اقلام بحرانی) ----------

    def upsert_monthly_tundish_counts(
        self,
        *,
        jalali_year: int,
        jalali_month: int,
        count_billet: int,
        count_bloom: int,
        count_slab: int,
        updated_by: str | int | None = None,
    ) -> dict[str, Any]:
        """Persist monthly billet/bloom/slab tundish counts (integers ≥ 0)."""
        y, m = int(jalali_year), int(jalali_month)
        if m < 1 or m > 12:
            raise ValueError(f"ماه نامعتبر: {jalali_month}")
        for label, val in (
            ("بیلت", count_billet),
            ("بلوم", count_bloom),
            ("اسلب", count_slab),
        ):
            if int(val) < 0:
                raise ValueError(f"تعداد تاندیش {label} نمی‌تواند منفی باشد.")
        now = _utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO monthly_tundish_counts
                    (jalali_year, jalali_month, count_billet, count_bloom, count_slab,
                     updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jalali_year, jalali_month) DO UPDATE SET
                    count_billet = excluded.count_billet,
                    count_bloom = excluded.count_bloom,
                    count_slab = excluded.count_slab,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by,
                    exclude_from_basis = 0,
                    basis_note = NULL
                """,
                (
                    y,
                    m,
                    int(count_billet),
                    int(count_bloom),
                    int(count_slab),
                    now,
                    str(updated_by) if updated_by is not None else None,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM monthly_tundish_counts
                WHERE jalali_year = ? AND jalali_month = ?
                """,
                (y, m),
            ).fetchone()
        return dict(row) if row else {}

    def get_monthly_tundish_counts(
        self, jalali_year: int, jalali_month: int
    ) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM monthly_tundish_counts
                WHERE jalali_year = ? AND jalali_month = ?
                """,
                (int(jalali_year), int(jalali_month)),
            ).fetchone()
            return dict(row) if row else None

    def set_monthly_tundish_counts_exclusion(
        self, jalali_year: int, jalali_month: int, *, exclude: bool, note: str | None = None
    ) -> bool:
        """Keep a manual monthly entry for audit but (not) use it as critical-items basis."""
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE monthly_tundish_counts
                SET exclude_from_basis = ?, basis_note = ?
                WHERE jalali_year = ? AND jalali_month = ?
                """,
                (1 if exclude else 0, note, int(jalali_year), int(jalali_month)),
            )
            return cur.rowcount > 0

    def sequence_tundish_counts(self, jalali_year: int, jalali_month: int) -> dict[str, int]:
        """{section: number of sequence rows} from main_goal_tundish_sequences for a month
        (each sequence = one tundish use). Empty dict when the month has no log."""
        key = f"m:{int(jalali_year)}-{int(jalali_month):02d}"
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT section, COUNT(*) AS n FROM main_goal_tundish_sequences
                WHERE period_key = ? GROUP BY section
                """,
                (key,),
            ).fetchall()
        return {str(r["section"]): int(r["n"]) for r in rows}

    def list_monthly_tundish_counts(self, limit: int = 24) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM monthly_tundish_counts
                ORDER BY jalali_year DESC, jalali_month DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]

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

    # ---------- گزارش تاندیش بعد از ریخته‌گری ----------

    def _ensure_tundish_report_seed(self, conn: sqlite3.Connection) -> None:
        """Seed default items once per section (flag in bot_settings).

        The flag prevents re-seeding after an admin deletes every item.
        """
        from services.tundish_report_defaults import DEFAULT_ITEMS

        now = _utcnow()
        for section, items in DEFAULT_ITEMS.items():
            flag = f"tundish_report_seeded:{section}"
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key = ?", (flag,)
            ).fetchone()
            if row:
                continue
            has_any = conn.execute(
                "SELECT 1 FROM tundish_report_items WHERE section = ? LIMIT 1",
                (section,),
            ).fetchone()
            if not has_any:
                for order, it in enumerate(items, start=1):
                    conn.execute(
                        """
                        INSERT INTO tundish_report_items
                            (section, label, item_type, options_json, required,
                             sort_order, created_at, updated_at, updated_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'bootstrap')
                        """,
                        (
                            section,
                            it["label"],
                            it["item_type"],
                            json.dumps(it.get("options") or [], ensure_ascii=False),
                            1 if it.get("required", True) else 0,
                            order,
                            now,
                            now,
                        ),
                    )
            conn.execute(
                """
                INSERT OR REPLACE INTO bot_settings (key, value, updated_at, updated_by)
                VALUES (?, '1', ?, 'bootstrap')
                """,
                (flag, now),
            )

    @staticmethod
    def _tundish_item_row(row: sqlite3.Row | dict) -> dict[str, Any]:
        item = dict(row)
        raw = item.pop("options_json", None)
        try:
            opts = json.loads(raw) if raw else []
        except (TypeError, ValueError, json.JSONDecodeError):
            opts = []
        item["options"] = [str(o) for o in opts if str(o).strip()]
        item["required"] = bool(item.get("required"))
        return item

    def list_tundish_report_items(self, section: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tundish_report_items
                WHERE section = ?
                ORDER BY sort_order, id
                """,
                (str(section),),
            ).fetchall()
        return [self._tundish_item_row(r) for r in rows]

    def get_tundish_report_item(self, item_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tundish_report_items WHERE id = ?", (int(item_id),)
            ).fetchone()
        return self._tundish_item_row(row) if row else None

    def add_tundish_report_item(
        self,
        *,
        section: str,
        label: str,
        item_type: str,
        options: list[str] | None = None,
        required: bool = True,
        updated_by: str | int | None = None,
    ) -> dict[str, Any]:
        now = _utcnow()
        with self.connect() as conn:
            mx = conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) FROM tundish_report_items WHERE section = ?",
                (section,),
            ).fetchone()[0]
            cur = conn.execute(
                """
                INSERT INTO tundish_report_items
                    (section, label, item_type, options_json, required, sort_order,
                     created_at, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    section,
                    label,
                    item_type,
                    json.dumps(list(options or []), ensure_ascii=False),
                    1 if required else 0,
                    int(mx or 0) + 1,
                    now,
                    now,
                    str(updated_by) if updated_by is not None else None,
                ),
            )
            new_id = int(cur.lastrowid)
        return self.get_tundish_report_item(new_id) or {}

    def update_tundish_report_item(
        self,
        item_id: int,
        *,
        label: str | None = None,
        item_type: str | None = None,
        options: list[str] | None = None,
        required: bool | None = None,
        updated_by: str | int | None = None,
    ) -> Optional[dict[str, Any]]:
        sets: list[str] = []
        params: list[Any] = []
        if label is not None:
            sets.append("label = ?")
            params.append(label)
        if item_type is not None:
            sets.append("item_type = ?")
            params.append(item_type)
        if options is not None:
            sets.append("options_json = ?")
            params.append(json.dumps(list(options), ensure_ascii=False))
        if required is not None:
            sets.append("required = ?")
            params.append(1 if required else 0)
        if not sets:
            return self.get_tundish_report_item(item_id)
        sets.extend(["updated_at = ?", "updated_by = ?"])
        params.extend([_utcnow(), str(updated_by) if updated_by is not None else None])
        params.append(int(item_id))
        with self.connect() as conn:
            conn.execute(
                f"UPDATE tundish_report_items SET {', '.join(sets)} WHERE id = ?",
                params,
            )
        return self.get_tundish_report_item(item_id)

    def delete_tundish_report_item(self, item_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT section FROM tundish_report_items WHERE id = ?", (int(item_id),)
            ).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM tundish_report_items WHERE id = ?", (int(item_id),))
            section = row["section"]
        self._renumber_tundish_items(section)
        return True

    def _renumber_tundish_items(self, section: str, ordered_ids: list[int] | None = None) -> None:
        with self.connect() as conn:
            if ordered_ids is None:
                ordered_ids = [
                    int(r["id"])
                    for r in conn.execute(
                        "SELECT id FROM tundish_report_items WHERE section = ? ORDER BY sort_order, id",
                        (section,),
                    ).fetchall()
                ]
            for pos, iid in enumerate(ordered_ids, start=1):
                conn.execute(
                    "UPDATE tundish_report_items SET sort_order = ? WHERE id = ? AND section = ?",
                    (pos, int(iid), section),
                )

    def move_tundish_report_item(self, item_id: int, new_position: int) -> Optional[dict[str, Any]]:
        """Move an item to 1-based ``new_position`` within its section."""
        item = self.get_tundish_report_item(item_id)
        if not item:
            return None
        ids = [int(it["id"]) for it in self.list_tundish_report_items(item["section"])]
        ids.remove(int(item_id))
        pos = max(1, min(int(new_position), len(ids) + 1))
        ids.insert(pos - 1, int(item_id))
        self._renumber_tundish_items(item["section"], ids)
        return self.get_tundish_report_item(item_id)

    def insert_tundish_report(
        self,
        *,
        section: str,
        fields: dict[str, Any],
        items: list[dict[str, Any]],
        summary_text: str | None,
        raw_text: str | None,
        source: str,
        bale_user_id: str | int,
        actor_display_name: str | None,
        created_at: str,
        created_at_tehran: str,
        jalali_date: str,
    ) -> dict[str, Any]:
        def _int(v: Any) -> Optional[int]:
            try:
                return int(v) if v is not None and str(v).strip() != "" else None
            except (TypeError, ValueError):
                return None

        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO tundish_reports
                    (section, line, tundish_no, sequence, melt_count, steel_grade,
                     fields_json, items_json, summary_text, raw_text, source,
                     bale_user_id, actor_display_name, created_at,
                     created_at_tehran, jalali_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    section,
                    fields.get("line"),
                    None if fields.get("tundish_no") is None else str(fields.get("tundish_no")),
                    _int(fields.get("sequence")),
                    _int(fields.get("melt_count")),
                    fields.get("steel_grade"),
                    json.dumps(fields, ensure_ascii=False),
                    json.dumps(items, ensure_ascii=False),
                    summary_text,
                    raw_text,
                    source,
                    str(bale_user_id),
                    actor_display_name,
                    created_at,
                    created_at_tehran,
                    jalali_date,
                ),
            )
            rid = int(cur.lastrowid)
            for pos, it in enumerate(items, start=1):
                val = it.get("value")
                num: Optional[float] = None
                if it.get("item_type") == "number" and val is not None:
                    try:
                        num = float(val)
                    except (TypeError, ValueError):
                        num = None
                conn.execute(
                    """
                    INSERT INTO tundish_report_values
                        (report_id, item_id, label, item_type, value_text, value_num, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rid,
                        it.get("item_id"),
                        it.get("label") or "",
                        it.get("item_type") or "text",
                        None if val is None else str(val),
                        num,
                        pos,
                    ),
                )
        return self.get_tundish_report(rid) or {}

    @staticmethod
    def _tundish_report_row(row: sqlite3.Row | dict) -> dict[str, Any]:
        rep = dict(row)
        for key, out in (("fields_json", "fields"), ("items_json", "items")):
            raw = rep.pop(key, None)
            try:
                rep[out] = json.loads(raw) if raw else ({} if out == "fields" else [])
            except (TypeError, ValueError, json.JSONDecodeError):
                rep[out] = {} if out == "fields" else []
        return rep

    def get_tundish_report(self, report_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tundish_reports WHERE id = ?", (int(report_id),)
            ).fetchone()
            if not row:
                return None
            rep = self._tundish_report_row(row)
            vals = conn.execute(
                "SELECT * FROM tundish_report_values WHERE report_id = ? ORDER BY sort_order, id",
                (int(report_id),),
            ).fetchall()
        rep["values"] = [dict(v) for v in vals]
        return rep

    def list_tundish_reports(
        self, *, section: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tundish_reports"
        params: list[Any] = []
        if section:
            sql += " WHERE section = ?"
            params.append(section)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(int(limit))
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._tundish_report_row(r) for r in rows]

    def recent_tundish_steel_grades(self, section: str, limit: int = 4) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT steel_grade, MAX(created_at) AS last_at
                FROM tundish_reports
                WHERE section = ? AND steel_grade IS NOT NULL AND TRIM(steel_grade) != ''
                GROUP BY steel_grade
                ORDER BY last_at DESC
                LIMIT ?
                """,
                (section, int(limit)),
            ).fetchall()
        return [str(r["steel_grade"]) for r in rows]

    # ------------------------------------------------------------------ main goal report
    def insert_main_goal_report(
        self,
        *,
        period_key: str | None,
        period_label: str | None,
        period_json: str | None,
        files_json: str,
        results_json: str | None,
        summary_text: str | None,
        target_tons: float | None,
        source: str,
        bale_user_id: str | int,
        actor_display_name: str | None,
        created_at: str,
        created_at_tehran: str,
        jalali_date: str,
    ) -> dict:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO main_goal_reports
                    (period_key, period_label, period_json, files_json, results_json,
                     summary_text, target_tons, source, bale_user_id, actor_display_name,
                     created_at, created_at_tehran, jalali_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    period_key,
                    period_label,
                    period_json,
                    files_json,
                    results_json,
                    summary_text,
                    target_tons,
                    source,
                    str(bale_user_id),
                    actor_display_name,
                    created_at,
                    created_at_tehran,
                    jalali_date,
                ),
            )
            rid = int(cur.lastrowid)
        return self.get_main_goal_report(rid) or {}

    def get_main_goal_report(self, report_id: int):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM main_goal_reports WHERE id = ?", (int(report_id),)
            ).fetchone()
        return dict(row) if row else None

    def list_main_goal_reports(self, *, limit: int = 20) -> list:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, period_key, period_label, summary_text, target_tons,
                       source, bale_user_id, actor_display_name,
                       created_at, created_at_tehran, jalali_date
                FROM main_goal_reports
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ main goal monthly history
    def upsert_main_goal_month(
        self,
        *,
        period_key: str,
        period_label: str,
        period_kind: str,
        year: int | None,
        month: int | None,
        sort_key: str,
        files_json: str,
        stats_json: str,
        summary_text: str | None,
        source: str,
        bale_user_id: str | int,
        actor_display_name: str | None,
        created_at_tehran: str,
        jalali_date: str,
    ) -> tuple[dict, bool]:
        """Insert or replace one month set. Returns (row, replaced_existing)."""
        now = _utcnow()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM main_goal_months WHERE period_key = ?", (period_key,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE main_goal_months
                    SET period_label = ?, period_kind = ?, year = ?, month = ?,
                        sort_key = ?, files_json = ?, stats_json = ?, summary_text = ?,
                        source = ?, bale_user_id = ?, actor_display_name = ?,
                        updated_at = ?, created_at_tehran = ?, jalali_date = ?
                    WHERE id = ?
                    """,
                    (
                        period_label, period_kind, year, month, sort_key, files_json,
                        stats_json, summary_text, source, str(bale_user_id),
                        actor_display_name, now, created_at_tehran, jalali_date,
                        int(existing["id"]),
                    ),
                )
                rid = int(existing["id"])
            else:
                cur = conn.execute(
                    """
                    INSERT INTO main_goal_months
                        (period_key, period_label, period_kind, year, month, sort_key,
                         files_json, stats_json, summary_text, source, bale_user_id,
                         actor_display_name, created_at, updated_at, created_at_tehran,
                         jalali_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        period_key, period_label, period_kind, year, month, sort_key,
                        files_json, stats_json, summary_text, source, str(bale_user_id),
                        actor_display_name, now, now, created_at_tehran, jalali_date,
                    ),
                )
                rid = int(cur.lastrowid)
        return (self.get_main_goal_month(rid) or {}), bool(existing)

    def get_main_goal_month(self, month_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM main_goal_months WHERE id = ?", (int(month_id),)
            ).fetchone()
        return dict(row) if row else None

    def list_main_goal_months(self) -> list[dict[str, Any]]:
        """All stored month sets, oldest first (chronological)."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM main_goal_months ORDER BY sort_key ASC, id ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def main_goal_month_keys(self) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT period_key FROM main_goal_months").fetchall()
        return {str(r["period_key"]) for r in rows}

    def delete_main_goal_month(self, month_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT period_key FROM main_goal_months WHERE id = ?", (int(month_id),)
            ).fetchone()
            period_key = str(row["period_key"]) if row else None
            cur = conn.execute(
                "DELETE FROM main_goal_months WHERE id = ?", (int(month_id),)
            )
            deleted = cur.rowcount > 0
        if period_key:
            self.delete_main_goal_inputs_by_key(period_key)
        return deleted

    # ------------------------------------------------------------------ main goal normalized production
    def upsert_main_goal_production(self, **kw) -> tuple[dict, bool]:
        """Insert/replace one production month. Returns (row, replaced)."""
        now = _utcnow()
        period_key = kw["period_key"]
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM main_goal_production WHERE period_key = ?", (period_key,)
            ).fetchone()
            cols = (
                "period_label", "year", "month", "sort_key",
                "slab_tons", "bloom_tons", "billet_tons", "total_tons", "melt_count",
                "melt_weight_kg", "product_weight_kg", "slab_count", "bloom_billet_count",
                "melts_per_day", "report_tab", "source_status",
                "slab_melt_count", "bloom_melt_count", "billet_melt_count",
                "ccm1_tons", "ccm2_tons", "ccm3_tons", "ccm4_tons", "ccm5_tons",
                "source_type", "source_path", "source_filename",
                "ocr_raw_text", "ocr_confidence", "ocr_fields_json", "manual_corrected",
                "notes_json", "missing_json",
                "bale_user_id", "actor_display_name", "source",
                "created_at_tehran", "jalali_date",
            )
            vals = [kw.get(c) for c in cols]
            vals[cols.index("bale_user_id")] = str(kw["bale_user_id"])
            vals[cols.index("manual_corrected")] = int(kw.get("manual_corrected") or 0)
            if existing:
                set_clause = ", ".join(f"{c} = ?" for c in cols) + ", updated_at = ?"
                conn.execute(
                    f"UPDATE main_goal_production SET {set_clause} WHERE id = ?",
                    (*vals, now, int(existing["id"])),
                )
                rid = int(existing["id"])
            else:
                col_list = "period_key, " + ", ".join(cols) + ", created_at, updated_at"
                placeholders = ", ".join("?" for _ in range(len(cols) + 3))
                cur = conn.execute(
                    f"INSERT INTO main_goal_production ({col_list}) VALUES ({placeholders})",
                    (period_key, *vals, now, now),
                )
                rid = int(cur.lastrowid)
        return (self.get_main_goal_production(rid) or {}), bool(existing)

    def get_main_goal_production(self, prod_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM main_goal_production WHERE id = ?", (int(prod_id),)
            ).fetchone()
        return dict(row) if row else None

    def get_main_goal_production_by_key(self, period_key: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM main_goal_production WHERE period_key = ?", (period_key,)
            ).fetchone()
        return dict(row) if row else None

    def list_main_goal_production(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM main_goal_production ORDER BY sort_key ASC, id ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_main_goal_production(self, prod_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM main_goal_production WHERE id = ?", (int(prod_id),)
            )
            return cur.rowcount > 0

    def delete_main_goal_production_by_key(self, period_key: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM main_goal_production WHERE period_key = ?", (period_key,)
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------ main goal normalized consumption
    def upsert_main_goal_consumption(
        self,
        *,
        materials: list[dict[str, Any]] | None = None,
        **kw,
    ) -> tuple[dict, bool]:
        """Insert/replace one section consumption (+ replace material rows)."""
        now = _utcnow()
        period_key = kw["period_key"]
        section = kw["section"]
        materials = materials or []
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM main_goal_consumption WHERE period_key = ? AND section = ?",
                (period_key, section),
            ).fetchone()
            cols = (
                "period_label", "year", "month", "sort_key",
                "tundish_count", "melt_count", "patch_count", "renovate_count",
                "source_path", "source_filename", "notes_json", "missing_json",
                "bale_user_id", "actor_display_name", "source",
                "created_at_tehran", "jalali_date",
            )
            vals = [kw.get(c) for c in cols]
            vals[cols.index("bale_user_id")] = str(kw["bale_user_id"])
            if existing:
                set_clause = ", ".join(f"{c} = ?" for c in cols) + ", updated_at = ?"
                conn.execute(
                    f"UPDATE main_goal_consumption SET {set_clause} WHERE id = ?",
                    (*vals, now, int(existing["id"])),
                )
                rid = int(existing["id"])
                conn.execute(
                    "DELETE FROM main_goal_consumption_materials WHERE consumption_id = ?",
                    (rid,),
                )
            else:
                col_list = "period_key, section, " + ", ".join(cols) + ", created_at, updated_at"
                placeholders = ", ".join("?" for _ in range(len(cols) + 4))
                cur = conn.execute(
                    f"INSERT INTO main_goal_consumption ({col_list}) VALUES ({placeholders})",
                    (period_key, section, *vals, now, now),
                )
                rid = int(cur.lastrowid)
            for i, m in enumerate(materials):
                conn.execute(
                    """
                    INSERT INTO main_goal_consumption_materials
                        (consumption_id, name, quantity, unit, item_id, keyword, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rid,
                        str(m.get("name") or ""),
                        float(m.get("quantity") or 0),
                        str(m.get("unit") or "kg"),
                        m.get("item_id"),
                        m.get("keyword"),
                        int(m.get("sort_order", i)),
                    ),
                )
        return (self.get_main_goal_consumption(rid) or {}), bool(existing)

    def get_main_goal_consumption(self, cons_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM main_goal_consumption WHERE id = ?", (int(cons_id),)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            mats = conn.execute(
                """
                SELECT name, quantity, unit, item_id, keyword, sort_order
                FROM main_goal_consumption_materials
                WHERE consumption_id = ?
                ORDER BY sort_order ASC, id ASC
                """,
                (int(cons_id),),
            ).fetchall()
        d["materials"] = [dict(m) for m in mats]
        return d

    def list_main_goal_consumption(
        self, *, period_key: str | None = None
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if period_key:
                rows = conn.execute(
                    """
                    SELECT * FROM main_goal_consumption
                    WHERE period_key = ?
                    ORDER BY section ASC
                    """,
                    (period_key,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM main_goal_consumption
                    ORDER BY sort_key ASC, section ASC, id ASC
                    """
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                mats = conn.execute(
                    """
                    SELECT name, quantity, unit, item_id, keyword, sort_order
                    FROM main_goal_consumption_materials
                    WHERE consumption_id = ?
                    ORDER BY sort_order ASC, id ASC
                    """,
                    (int(d["id"]),),
                ).fetchall()
                d["materials"] = [dict(m) for m in mats]
                out.append(d)
        return out

    def delete_main_goal_consumption(self, cons_id: int) -> bool:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM main_goal_consumption_materials WHERE consumption_id = ?",
                (int(cons_id),),
            )
            cur = conn.execute(
                "DELETE FROM main_goal_consumption WHERE id = ?", (int(cons_id),)
            )
            return cur.rowcount > 0

    def delete_main_goal_inputs_by_key(self, period_key: str) -> None:
        """Delete normalized production + consumptions + sequences for a period."""
        with self.connect() as conn:
            ids = [
                int(r["id"])
                for r in conn.execute(
                    "SELECT id FROM main_goal_consumption WHERE period_key = ?",
                    (period_key,),
                ).fetchall()
            ]
            for cid in ids:
                conn.execute(
                    "DELETE FROM main_goal_consumption_materials WHERE consumption_id = ?",
                    (cid,),
                )
            conn.execute(
                "DELETE FROM main_goal_consumption WHERE period_key = ?", (period_key,)
            )
            try:
                conn.execute(
                    "DELETE FROM main_goal_tundish_sequences WHERE period_key = ?",
                    (period_key,),
                )
            except Exception:
                pass
            conn.execute(
                "DELETE FROM main_goal_production WHERE period_key = ?", (period_key,)
            )

    def list_main_goal_period_keys_union(self) -> list[str]:
        """All period_keys seen in production, consumption, or legacy months."""
        with self.connect() as conn:
            keys = set()
            for table in (
                "main_goal_production",
                "main_goal_consumption",
                "main_goal_months",
            ):
                try:
                    rows = conn.execute(f"SELECT DISTINCT period_key FROM {table}").fetchall()
                except sqlite3.OperationalError:
                    continue
                keys.update(str(r["period_key"]) for r in rows if r["period_key"])
        return sorted(keys)

    def replace_main_goal_sequences(
        self,
        *,
        period_key: str,
        section: str,
        rows: list[dict],
        meta: dict,
    ) -> int:
        """Replace all sequence rows for (period_key, section); return count inserted."""
        now = _utcnow()
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM main_goal_tundish_sequences WHERE period_key = ? AND section = ?",
                (period_key, section),
            )
            n = 0
            for r in rows:
                conn.execute(
                    """
                    INSERT INTO main_goal_tundish_sequences
                        (period_key, period_label, year, month, section, machine, tundish_no,
                         operator, melt_count, sequence_minutes, isg, first_melt_no,
                         first_cast_start, last_melt_no, last_cast_end,
                         shroud_replaced, outer_nozzle_replaced, tube_changer,
                         source_path, source_filename, source, bale_user_id,
                         actor_display_name, created_at, jalali_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        period_key,
                        meta.get("period_label"),
                        meta.get("year"),
                        meta.get("month"),
                        section,
                        r.get("machine"),
                        r.get("tundish_no"),
                        r.get("operator"),
                        r.get("melt_count"),
                        r.get("sequence_minutes"),
                        r.get("isg"),
                        r.get("first_melt_no"),
                        r.get("first_cast_start"),
                        r.get("last_melt_no"),
                        r.get("last_cast_end"),
                        int(r.get("shroud_replaced") or 0),
                        int(r.get("outer_nozzle_replaced") or 0),
                        int(r.get("tube_changer") or 0),
                        meta.get("source_path"),
                        meta.get("source_filename"),
                        meta.get("source") or "bot",
                        str(meta.get("bale_user_id")),
                        meta.get("actor_display_name"),
                        now,
                        meta.get("jalali_date") or "",
                    ),
                )
                n += 1
        return n

    def list_main_goal_sequences(
        self, *, period_key: str | None = None, section: str | None = None
    ) -> list[dict]:
        with self.connect() as conn:
            q = "SELECT * FROM main_goal_tundish_sequences WHERE 1=1"
            args: list = []
            if period_key:
                q += " AND period_key = ?"
                args.append(period_key)
            if section:
                q += " AND section = ?"
                args.append(section)
            q += " ORDER BY id ASC"
            return [dict(r) for r in conn.execute(q, args).fetchall()]

    def aggregate_sequences(self, period_key: str, section: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS tundish_count,
                       COALESCE(SUM(melt_count), 0) AS melt_count,
                       COALESCE(SUM(shroud_replaced), 0) AS shroud_replacements,
                       COALESCE(SUM(outer_nozzle_replaced), 0) AS nozzle_replacements
                FROM main_goal_tundish_sequences
                WHERE period_key = ? AND section = ?
                """,
                (period_key, section),
            ).fetchone()
        return dict(row) if row else {}
