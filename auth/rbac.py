"""Role-based access control helpers."""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from config import ROLES
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
    return bool(user and user.get("role") == "manager" and user.get("active"))


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def filter_dataframe_for_user(df: pd.DataFrame, user: dict[str, Any]) -> pd.DataFrame:
    """
    Apply RBAC row filters:
    - manager: all rows
    - responsible_officer: rows where domain/scope matches user.scope
    - technician: rows where assignee_id or assignee_name matches the user
    """
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()

    role = user.get("role")
    if role == "manager":
        return df.copy()

    work = df.copy()
    # normalize column names already done in excel loader; still guard
    cols = {c.lower(): c for c in work.columns}

    if role == "responsible_officer":
        scope = (user.get("scope") or "").strip()
        if not scope:
            return work.iloc[0:0].copy()
        domain_col = cols.get("domain") or cols.get("scope")
        if not domain_col:
            return work.iloc[0:0].copy()
        mask = work[domain_col].astype(str).str.strip().str.lower() == scope.lower()
        return work.loc[mask].copy()

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
    Manager needs all three files.
    Officer/technician can generate with all three as well (filtered view).
    """
    if not user or not user.get("active"):
        return False, "دسترسی ندارید."
    if not all(completeness.values()):
        missing = [k for k, v in completeness.items() if not v]
        labels = {
            "tank_consumption": "مقدار مصرفی هر تانک",
            "product_inventory": "موجودی محصولات",
            "monthly_consumption": "مصرف ماهانه مواد",
        }
        names = "، ".join(labels[m] for m in missing)
        return False, f"هنوز این فایل‌ها دریافت نشده‌اند:\n{names}"
    return True, ""
