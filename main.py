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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bale-materials-bot")


def main() -> int:
    if not BALE_BOT_TOKEN:
        logger.error("BALE_BOT_TOKEN در فایل .env تنظیم نشده است.")
        return 1
    ensure_dirs()
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
        logger.warning("ADMIN_BALE_USER_ID خالی است — مدیر اولیه ساخته نشد.")

    client = BaleClient()
    app = BotApp(client, db)
    try:
        me = client.get_me()
        logger.info("Bot online: %s (@%s)", me.get("first_name"), me.get("username"))
        try:
            client.delete_webhook()
        except BaleAPIError as exc:
            logger.warning("deleteWebhook: %s", exc)

        logger.info("Polling started…")
        while True:
            try:
                updates = client.get_updates()
                for upd in updates:
                    app.handle_update(upd)
            except BaleAPIError as exc:
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
