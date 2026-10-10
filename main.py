#!/usr/bin/env python3
"""
بازوی بله — گزارش تاندیش (سه فایل Excel → یک PDF)

API: فراخوانی مستقیم HTTPS به https://tapi.bale.ai/bot<TOKEN>/<METHOD>
(کتابخانه python-bale-bot به‌خاطر وابستگی aiohttp روی بعضی محیط‌ها سخت نصب می‌شود؛
httpx جایگزین سبک و پایدار است.)
"""
from __future__ import annotations

import logging
import sys
import time

from bot.bale_api import BaleAPIError, BaleClient
from bot.handlers import BotApp
from config import ADMIN_BALE_USER_ID, BALE_BOT_TOKEN, ensure_dirs
from db.models import Database
from services import mandatory_reminders

REMINDER_TICK_SECONDS = 60
HOUSEKEEPING_SECONDS = 24 * 3600

from log_redact import install_log_redaction  # noqa: E402

install_log_redaction()  # httpx/httpcore → WARNING; token never reaches the log
logger = logging.getLogger("bale-materials-bot")


_TRANSIENT_MARKS = (
    "timed out", "timeout", "temporarily", "connection reset", "502", "503", "504",
    "remote protocol", "server disconnected", "unexpected_eof", "eof occurred", "connection refused",
)


def _is_transient(exc: BaleAPIError) -> bool:
    text = str(getattr(exc, "description", "") or exc).lower()
    return any(m in text for m in _TRANSIENT_MARKS)


def main() -> int:
    from services.housekeeping import setup_logging

    # single bot log: data/bot.log, rotating 5×2 MB (stdout file only gets crash output)
    setup_logging("bot")
    install_log_redaction()
    if not BALE_BOT_TOKEN:
        logger.error("BALE_BOT_TOKEN در فایل .env تنظیم نشده است.")
        return 1
    ensure_dirs()
    from services import housekeeping

    housekeeping.run_all()
    db = Database()
    if ADMIN_BALE_USER_ID:
        db.bootstrap_admin(ADMIN_BALE_USER_ID)
        logger.info("Admin bootstrap: %s", ADMIN_BALE_USER_ID)

        # Keep the first-run demo usable after a restart, but never replace a
        # real warehouse upload once an inventory extract exists.
        if db.get_latest_extracted(ADMIN_BALE_USER_ID, "product_inventory") is None:
            try:
                from scripts.seed_real_samples import seed as seed_real_samples

                seeded = seed_real_samples(ADMIN_BALE_USER_ID, db=db)
                logger.info(
                    "Seeded real samples: inventory=%s, monthly=%s, catalog=%s",
                    seeded["inventory"]["kept"],
                    seeded["monthly"]["kept"],
                    seeded["catalog"],
                )
            except Exception:  # noqa: BLE001
                logger.exception("Initial real sample seed failed")
    else:
        logger.info(
            "ADMIN_BALE_USER_ID خالی است — مالک با اولین /start ساخته می‌شود."
        )

    client = BaleClient()
    app = BotApp(client, db)
    app.bg.enable(workers=2)  # 19c: heavy reports / OCR off the polling thread
    try:
        me = client.get_me()
        uname = (me.get("username") or "").strip().lstrip("@")
        if uname:
            app._bot_username = uname
        logger.info("Bot online: %s (@%s)", me.get("first_name"), me.get("username"))
        try:
            client.delete_webhook()
        except BaleAPIError as exc:
            logger.warning("deleteWebhook: %s", exc)

        logger.info("Polling started…")
        last_reminder_tick = 0.0
        last_housekeeping = time.monotonic()
        while True:
            try:
                updates = client.get_updates()
                for upd in updates:
                    app.handle_update(upd)
                # یادآور گزارش‌های الزامی (never raises; respects DB settings)
                if time.monotonic() - last_reminder_tick >= REMINDER_TICK_SECONDS:
                    last_reminder_tick = time.monotonic()
                    mandatory_reminders.tick(client, db)
                if time.monotonic() - last_housekeeping >= HOUSEKEEPING_SECONDS:
                    last_housekeeping = time.monotonic()
                    housekeeping.run_all()
            except BaleAPIError as exc:
                if exc.method == "getUpdates" and _is_transient(exc):
                    # long-poll read timeouts / brief network blips are normal
                    logger.warning("getUpdates transient: %s", exc.description)
                    time.sleep(1)
                else:
                    logger.error("API error: %s", exc)
                    time.sleep(3)
            except KeyboardInterrupt:
                logger.info("Stopped by user")
                break
            except Exception:  # noqa: BLE001
                logger.exception("Unexpected loop error")
                time.sleep(2)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
