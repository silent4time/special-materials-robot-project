"""Shared Excel upload pipeline for «📥 به‌روزرسانی موجودی انبار», «📥 مصرف ماهیانه اقلام» and
the full منبع اصلی replacement — used by BOTH the Bale bot and the web panel (no duplicated
logic). Rules live in services.main_source (stock update = quantity only, known-code new ids
with sibling prefill, never 1800/unknown codes, rates-wipe refusal).
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from bot.activity import log_activity, resolve_display_name
from config import FILE_TYPES
from excel.processor import (
    ExcelValidationError,
    extract_and_save_clean,
    load_excel,
    looks_like_product_inventory,
    merge_clean_frames,
    write_clean_excel,
)
from services import inbound_report as inbound_svc
from services import main_source as main_source_svc
from services import permissions as perm
from services import user_errors

logger = logging.getLogger(__name__)


@dataclass
class UploadOutcome:
    ok: bool
    kind: str
    pending: str
    text: str
    session: dict | None = None
    inbound_report: dict | None = None


def process_upload(
    db: Any,
    user: dict,
    dest: Path,
    pending: str,
    *,
    session: dict,
    upload_origin: str | None,
    actor_fa: str,
) -> UploadOutcome:
    """``dest`` = raw .xlsx already saved under UPLOAD_DIR/<uid>/<session>/<pending>.xlsx.

    ``upload_origin == "main_source"`` + MAIN_SOURCE_EDIT = full منبع اصلی replacement,
    otherwise a product_inventory upload is a stock update.
    """
    dest = Path(dest)
    dest_dir = dest.parent
    # If a «مصرف ماهیانه / مواد مصرفی» upload actually matches the
    # منبع اصلی (product_inventory) cleaned format, treat it as a primary
    # inventory refresh so warehouse master data stays current.
    format_redirect_note = ""
    if pending == "monthly_consumption":
        try:
            peek = load_excel(dest, strict_tundish=False)
            if looks_like_product_inventory(peek):
                pending = "product_inventory"
                # Rename raw slot file to inventory name for clarity
                inv_dest = dest_dir / "product_inventory.xlsx"
                if dest.resolve() != inv_dest.resolve():
                    dest.replace(inv_dest)
                    dest = inv_dest
                format_redirect_note = (
                    "\n(فایل با قالب منبع اصلی شناسایی شد — "
                    "جدول اصلی موجودی به‌روز شد.)"
                )
                logger.info(
                    "monthly/consumables upload redirected to product_inventory for user %s",
                    user["bale_user_id"],
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("inventory-format peek failed: %s", exc)

    allowlist = None
    if pending == "product_inventory":
        allowlist = db.active_category_code_set()
        if not allowlist:
            dest.unlink(missing_ok=True)
            return UploadOutcome(ok=False, kind="no_codes", pending=pending, text=(
                "لیست کدهای دسته‌بندی خالی است.\n"
                "ابتدا از «📤 ورود فایل‌ها» → «📦 منبع اصلی» → «➕ افزودن کد دسته» "
                "حداقل یک کد ۴ رقمی ثبت کنید، سپس دوباره فایل را بفرستید."))

    # Snapshot previous clean DF before extract overwrites the same clean_path.
    # For product_inventory also freeze the previous extract file so inbound
    # delta can compare against a readable pre-upload baseline later.
    slot_col = {
        "tank_consumption": "tank_path",
        "product_inventory": "inventory_path",
        "monthly_consumption": "monthly_path",
    }.get(pending)
    previous_clean = Path(session[slot_col]) if slot_col and session.get(slot_col) else None
    old_df = None
    prev_kept = 0
    if pending == "product_inventory":
        latest_inv = db.get_latest_extracted_any("product_inventory")
        if latest_inv and latest_inv.get("clean_path"):
            latest_path = Path(latest_inv["clean_path"])
            if latest_path.exists():
                try:
                    snap_dir = latest_path.parent / "snapshots"
                    snap_dir.mkdir(parents=True, exist_ok=True)
                    snap_path = snap_dir / f"product_inventory_{latest_inv['id']}.xlsx"
                    if not snap_path.exists():
                        shutil.copy2(latest_path, snap_path)
                    db.update_extracted_clean_path(
                        int(latest_inv["id"]), str(snap_path)
                    )
                    previous_clean = snap_path
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "could not preserve inventory snapshot: %s", exc
                    )
    if previous_clean is not None and previous_clean.exists():
        try:
            old_df = pd.read_excel(previous_clean, engine="openpyxl")
            prev_kept = int(len(old_df))
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not load previous clean for merge: %s", exc)
            old_df = None
            prev_kept = 0

    try:
        result = extract_and_save_clean(
            dest, pending, category_allowlist=allowlist
        )
    except ExcelValidationError as exc:
        dest.unlink(missing_ok=True)
        return UploadOutcome(ok=False, kind="extract", pending=pending, text=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("extract failed")
        dest.unlink(missing_ok=True)
        return UploadOutcome(ok=False, kind="extract_error", pending=pending,
                             text=user_errors.error_fa("استخراج داده از فایل ناموفق بود", exc))

    new_kept = int(result.kept_row_count)
    merge_note = ""
    stock_note = ""
    source_guard_error = ""
    skipped_note = ""
    # t221u: «📥 موجودی انبار» (stock update) vs authorized full منبع اصلی upload.
    # Only stock updates feed the inbound report / move its baseline.
    full_source = (
        pending == "product_inventory"
        and upload_origin == "main_source"
        and not format_redirect_note
        and perm.can(user, perm.MAIN_SOURCE_EDIT)
    )
    is_stock_upload = pending == "product_inventory" and not full_source
    uploaded_stock_df: pd.DataFrame | None = None
    inbound_rejected: list[dict] = []
    if is_stock_upload:
        try:
            uploaded_stock_df = pd.read_excel(result.clean_path, engine="openpyxl")
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not read uploaded stock frame for inbound: %s", exc)
    if old_df is not None and not old_df.empty:
        try:
            new_df = pd.read_excel(result.clean_path, engine="openpyxl")
            if pending == "product_inventory":
                # منبع اصلی is the reference: a stock update never adds a new
                # 4-digit code nor a NEW 1800 row; an authorized full-source
                # upload may add both (shared rule in services.main_source).
                new_df, skipped = main_source_svc.filter_inventory_upload(
                    old_df, new_df, allow_new_codes=full_source
                )
                skipped_note = main_source_svc.skipped_rows_note_fa(skipped)
                if is_stock_upload:
                    inbound_rejected = list(skipped)
                if skipped:
                    logger.info(
                        "inventory upload: %d rows skipped (full_source=%s)",
                        len(skipped), full_source,
                    )
            if is_stock_upload:
                merged, stock_info = main_source_svc.merge_stock_update(old_df, new_df)
                stock_note = main_source_svc.stock_update_note_fa(stock_info)
            else:
                merged = merge_clean_frames(old_df, new_df, pending)
            if pending == "product_inventory":
                from excel.id_parse import apply_id_segment_rule

                merged, _old_mism = apply_id_segment_rule(merged)
                if main_source_svc.rates_wiped(old_df, merged):
                    source_guard_error = main_source_svc.RATES_WIPED_FA
                    raise RuntimeError("rates would be wiped")
            write_clean_excel(merged, result.clean_path, pending)
            result.kept_row_count = int(len(merged))
            merge_note = (
                f"\nهمسان‌سازی: قبلی {prev_kept} + جدید {new_kept} → نهایی {result.kept_row_count}."
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("merge clean frames failed: %s", exc)
            if pending == "product_inventory":
                # never save an unmerged/damaging file over منبع اصلی
                if previous_clean is not None and latest_inv and latest_inv.get("clean_path"):
                    orig = Path(str(latest_inv["clean_path"]))
                    try:
                        if Path(previous_clean).resolve() != orig.resolve():
                            shutil.copy2(previous_clean, orig)
                        db.update_extracted_clean_path(int(latest_inv["id"]), str(orig))
                    except Exception:  # noqa: BLE001
                        logger.exception("could not restore منبع اصلی after refused upload")
                db.set_pending_file_type(user["bale_user_id"], None)
                return UploadOutcome(ok=False, kind="guard", pending=pending, text=(
                    source_guard_error
                    or user_errors.error_fa("همسان‌سازی منبع اصلی ناموفق بود؛ چیزی ذخیره نشد", exc)))
            merge_note = "\n(" + user_errors.error_fa("همسان‌سازی انجام نشد", exc) + ")"

    # Session slots point at CLEAN file so analytics/PDF use filtered data
    session = db.store_file_slot(
        user["bale_user_id"], pending, str(result.clean_path)
    )
    extract_id = db.save_extracted(
        bale_user_id=user["bale_user_id"],
        session_id=session["id"],
        file_type=pending,
        raw_path=str(result.raw_path),
        clean_path=str(result.clean_path),
        row_count=result.kept_row_count,
        columns=result.columns,
    )

    catalog_note = ""
    inbound_report = None
    inbound_note = ""
    if pending == "product_inventory":
        # automatic catalog sync after every منبع اصلی change (names updated too)
        from services.catalog_sync import note_fa as _cat_note, sync_after_change

        catalog_note = _cat_note(sync_after_change(db, result.clean_path))
        if is_stock_upload and uploaded_stock_df is not None:
            try:
                live_after = pd.read_excel(result.clean_path, engine="openpyxl")
                inbound_report = inbound_svc.record_stock_upload(
                    db,
                    uploaded=uploaded_stock_df,
                    live_before=old_df,
                    live_after=live_after,
                    rejected=inbound_rejected,
                    bale_user_id=user["bale_user_id"],
                    actor_display_name=resolve_display_name(user),
                    extract_id=extract_id,
                    raw_path=dest,
                )
                inbound_note = "\n\n" + inbound_svc.summary_text_fa(inbound_report)
            except Exception as exc:  # noqa: BLE001
                logger.exception("inbound report after stock upload failed")
                inbound_note = "\n(" + user_errors.error_fa("گزارش اقلام ورودی ساخته نشد", exc) + ")"
        elif full_source:
            inbound_note = (
                "\nآپلود کامل منبع اصلی — پایه «گزارش اقلام ورودی به انبار» تغییر نکرد."
            )
    elif pending == "monthly_consumption":
        try:
            monthly_clean = pd.read_excel(result.clean_path, engine="openpyxl")
            usage_sync = main_source_svc.sync_usage_from_monthly(
                db,
                monthly_clean,
                bale_user_id=user["bale_user_id"],
            )
            if usage_sync.get("ok") and usage_sync.get("updated"):
                catalog_note += (
                    f"\nمحل استفاده در منبع اصلی برای "
                    f"{usage_sync.get('updated', 0)} قلم به‌روز شد "
                    f"(از {usage_sync.get('mapped', 0)} نگاشت مصرف)."
                )
            elif usage_sync.get("reason") == "no_inventory":
                catalog_note += (
                    "\n(منبع اصلی برای پر کردن محل استفاده هنوز موجود نیست.)"
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("usage_location sync after monthly failed: %s", exc)

    label = FILE_TYPES[pending]["label_fa"]
    reasons = result.drop_reasons or {}
    if pending == "product_inventory" and reasons:
        dropped_note = (
            f"\nحذف‌شده‌ها: دسته نامجاز={reasons.get('wrong_category', 0)}، "
            f"شناسه نامعتبر={reasons.get('bad_id', 0)}، "
            f"دسته خالی={reasons.get('bad_category', 0)}، "
            f"موجودی نامعتبر={reasons.get('bad_quantity', 0)}"
        )
    elif result.dropped_row_count > 0:
        dropped_note = f"\n({result.dropped_row_count} ردیف اضافی/نامعتبر حذف شد)"
    else:
        dropped_note = ""
    if pending == "product_inventory":
        from excel.id_parse import segment_mismatch_note_fa

        dropped_note += segment_mismatch_note_fa(result.segment_mismatches)
        dropped_note += skipped_note + stock_note
    extra_cols_note = ""
    if result.extra_columns_dropped:
        extra_cols_note = "\nستون‌های اضافی کنار گذاشته شد."
    actor_line = f"ثبت‌کننده: {actor_fa}"
    if pending == "product_inventory":
        log_activity(db, user, "upload_product_inventory")
    elif pending == "monthly_consumption":
        log_activity(db, user, "upload_monthly_consumption")
    return UploadOutcome(
        ok=True, kind="ok", pending=pending, session=session, inbound_report=inbound_report,
        text=(
            f"✅ فایل «{label}» دریافت شد.\n"
            f"از {result.raw_row_count} ردیف خام، {new_kept} ردیف نگه داشته شد."
            f"{merge_note}{dropped_note}{extra_cols_note}{catalog_note}{inbound_note}{format_redirect_note}\n"
            f"نسخه تمیز ذخیره و در پایگاه‌داده ثبت شد.\n"
            f"{actor_line}"
        ),
    )
