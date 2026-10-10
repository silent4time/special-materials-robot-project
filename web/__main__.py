"""python -m web — run dashboard (no BALE_BOT_TOKEN required)."""
from __future__ import annotations

import uvicorn

from config import WEB_HOST, WEB_PORT
from log_redact import install_log_redaction


def main() -> None:
    install_log_redaction()
    uvicorn.run("web.app:app", host=WEB_HOST, port=WEB_PORT, reload=False)


if __name__ == "__main__":
    main()
