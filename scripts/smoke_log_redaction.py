#!/usr/bin/env python3
"""Smoke: Bale token never reaches logs (httpx quiet + redaction filter), bot + web."""
from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAKE = "bot123456789:FAKEsecretFAKEsecret_-x"  # synthetic, not a real token


def main() -> int:
    import log_redact
    from log_redact import install_log_redaction, redact

    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(name)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(h)
    root.setLevel(logging.INFO)
    install_log_redaction()
    install_log_redaction()  # idempotent

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    assert "FAKEsecret" not in redact(f"https://tapi.bale.ai/{FAKE}/getUpdates")
    assert redact(f"https://tapi.bale.ai/{FAKE}/getMe") == "https://tapi.bale.ai/bot[REDACTED]/getMe"

    logging.getLogger("httpx").info('HTTP Request: POST https://tapi.bale.ai/%s/getUpdates "200 OK"', FAKE)
    logging.getLogger("x.y").warning("download attempt failed for https://tapi.bale.ai/file/%s/a.xlsx", FAKE)
    logging.getLogger("x.y").info("args form %s", f"https://tapi.bale.ai/{FAKE}/sendMessage")
    try:
        raise RuntimeError(f"boom https://tapi.bale.ai/{FAKE}/getFile")
    except RuntimeError:
        logging.getLogger("x.y").exception("call failed")
    out = buf.getvalue()
    assert "FAKEsecret" not in out, out
    assert "httpx HTTP Request" not in out, out  # INFO from httpx suppressed
    assert out.count("bot[REDACTED]") >= 3, out

    # uvicorn access log formatter unpacks record.args (5-tuple) — must keep working
    import uvicorn.logging as ulog

    abuf = io.StringIO()
    ah = logging.StreamHandler(abuf)
    ah.setFormatter(ulog.AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False))
    alog = logging.getLogger("uvicorn.access.smoke")
    alog.addHandler(ah)
    alog.propagate = False
    alog.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", f"/x?u=https://tapi.bale.ai/{FAKE}/getMe", "1.1", 200)
    aout = abuf.getvalue()
    assert '"GET /x?u=https://tapi.bale.ai/bot[REDACTED]/getMe HTTP/1.1" 200' in aout, aout
    assert "FAKEsecret" not in aout

    # configured token value redacted even without the bot prefix
    log_redact._configured_token = lambda: "987654:ConfiguredSecretValue"  # type: ignore[assignment]
    assert "ConfiguredSecretValue" not in redact("x 987654:ConfiguredSecretValue y")

    from bot.bale_api import BaleAPIError

    e = BaleAPIError("getUpdates", f"Client error for url 'https://tapi.bale.ai/{FAKE}/getUpdates'")
    assert "FAKEsecret" not in str(e) and "FAKEsecret" not in e.description, str(e)

    # web entry: importing the app installs redaction as well
    import web.app  # noqa: F401
    import web.__main__  # noqa: F401

    print("SMOKE_LOG_REDACTION_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
