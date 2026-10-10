"""python -m web — run dashboard (no BALE_BOT_TOKEN required)."""
from __future__ import annotations

import uvicorn

from config import WEB_HOST, WEB_PORT
from log_redact import install_log_redaction


def main() -> None:
    from services.housekeeping import setup_logging

    # single web log: data/web.log, rotating 5×2 MB; uvicorn loggers propagate to root
    setup_logging("web")
    install_log_redaction()
    uvicorn.run("web.app:app", host=WEB_HOST, port=WEB_PORT, reload=False, log_config=None)


if __name__ == "__main__":
    main()
