"""Role-based access control helpers."""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from config import ADMIN_ROLES, CATALOG_ADMIN_ROLES, FILE_TYPES, FULL_DATA_ROLES, ROLES
from db.models import Database


ROLE_LABELS = ROLES


def ensure_registered(db: Database, bale_user_id: str | int, display_name: str | None = None) -> Optional[dict]:
    """Return active user record or None if not registered."""
    user = db.get_user(bale_user_id)
    if not user or not user.get("active"):
        return None
    if display_name and display_name != user.get("display_name"):
        db.upsert_user(
            bale_user_id,
            role=user["role"],
            display_name=display_name,
            scope=user.get("scope"),
            active=True,
        )
        user = db.get_user(bale_user_id)
    return user


def require_manager(user: dict | None) -> bool:
    """True for owner or manager (admin menu / user management)."""
    return bool(user and user.get("active") and user.get("role") in ADMIN_ROLES)


def require_owner(user: dict | None) -> bool:
    return bool(user and user.get("active") and user.get("role") == "owner")



def can_configure_catalog(user: dict | None) -> bool:
    """Owner / manager / responsible_officer may assign catalog items to groups."""
    return bool(user and user.get("active") and user.get("role") in CATALOG_ADMIN_ROLES)


def can_request_materials(user: dict | None) -> bool:
    """Owner / manager / responsible_officer may run the material-request workflow."""
    return bool(user and user.get("active") and user.get("role") in CATALOG_ADMIN_ROLES)


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def filter_dataframe_for_user(df: pd.DataFrame, user: dict[str, Any]) -> pd.DataFrame:
    """
    Apply RBAC row filters:
    - owner / manager / responsible_officer (FULL_DATA_ROLES): all rows
    - If domain/assignee columns are absent (e.g. منابع اصلی / warehouse inventory plant-wide):
      all authorized users see the full inventory.
    - technician: rows where assignee_id or assignee_name matches the user;
      plant-wide (no domain/assignee) stays full view.
    Identity is by bale_user_id; officer scope text is not used for filtering.
    """
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()

    role = user.get("role")
    if role in FULL_DATA_ROLES:
        return df.copy()

    work = df.copy()
    cols = {c.lower(): c for c in work.columns}
    has_domain = bool(cols.get("domain") or cols.get("scope"))
    has_assignee = bool(cols.get("assignee_id") or cols.get("assignee_name"))

    # Plant-wide datasets (warehouse inventory): no domain/assignee → full view
    if not has_domain and not has_assignee:
        return work

    if role == "technician":
        uid = str(user.get("bale_user_id", "")).strip()
        name = str(user.get("display_name") or "").strip().lower()
        id_col = cols.get("assignee_id")
        name_col = cols.get("assignee_name")
        mask = pd.Series([False] * len(work), index=work.index)
        if id_col:
            mask = mask | (work[id_col].astype(str).str.strip() == uid)
        if name_col and name:
            mask = mask | (work[name_col].astype(str).str.strip().str.lower() == name)
        return work.loc[mask].copy()

    return work.iloc[0:0].copy()


def can_generate_report(user: dict, session: dict, completeness: dict[str, bool]) -> tuple[bool, str]:
    """
    Owner/manager need all three files.
    Officer/technician can generate with all three as well (filtered view).
    """
    if not user or not user.get("active"):
        return False, "دسترسی ندارید."
    if not all(completeness.values()):
        missing = [k for k, v in completeness.items() if not v]
        names = "، ".join(FILE_TYPES[m]["label_fa"] for m in missing if m in FILE_TYPES)
        return False, f"هنوز این فایل‌ها دریافت نشده‌اند:\n{names}"
    return True, ""
