"""Keep the Bale bot token out of logs (bot + web entry points).

Bale API URLs embed the token (``https://tapi.bale.ai/bot<digits>:<secret>/...``).
``install_log_redaction()``:
  * quiets URL-logging HTTP client loggers (httpx / httpcore) to WARNING;
  * installs a global LogRecord factory + handler filter that replaces any
    ``bot<digits>:<secret>`` (and the configured token value) with ``bot[REDACTED]``
    in the message, args and exception text of every record (defense in depth;
    covers uvicorn's own handlers too).
"""
from __future__ import annotations

import logging
import re

TOKEN_RE = re.compile(r"bot\d{3,}:[A-Za-z0-9_\-]{8,}")
REDACTED = "[REDACTED]"
QUIET_LOGGERS = ("httpx", "httpcore", "httpcore.http11", "httpcore.connection", "urllib3", "hpack")


def _configured_token() -> str:
    try:
        from config import BALE_BOT_TOKEN

        return (BALE_BOT_TOKEN or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def redact(text: object) -> str:
    """Return ``text`` as str with any Bale token removed."""
    s = text if isinstance(text, str) else str(text)
    s = TOKEN_RE.sub("bot" + REDACTED, s)
    tok = _configured_token()
    if tok and tok in s:
        s = s.replace(tok, REDACTED)
    return s


def _has_token(value: object) -> bool:
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:  # noqa: BLE001
        return False
    return redact(text) != text


def _redact_arg(value: object) -> object:
    """Redacted str when the argument's text carries a token, else unchanged."""
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(value) if _has_token(value) else value


def _redact_record(record: logging.LogRecord) -> logging.LogRecord:
    if getattr(record, "_token_redacted", False):
        return record
    # Keep ``args`` (formatters such as uvicorn's access log unpack them); redact
    # the template and every argument whose text carries a token instead.
    if isinstance(record.msg, str):
        record.msg = redact(record.msg)
    elif _has_token(record.msg):
        record.msg = redact(record.msg)
    if isinstance(record.args, tuple):
        record.args = tuple(_redact_arg(a) for a in record.args)
    elif isinstance(record.args, dict):
        record.args = {k: _redact_arg(v) for k, v in record.args.items()}
    if record.exc_info and not record.exc_text:
        try:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        except Exception:  # noqa: BLE001
            record.exc_text = None
    if record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)
    record._token_redacted = True  # type: ignore[attr-defined]
    return record


class RedactTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        _redact_record(record)
        return True


_installed = False


def _attach_filters() -> None:
    flt = RedactTokenFilter()
    loggers = [logging.getLogger()] + [
        lg for lg in logging.Logger.manager.loggerDict.values() if isinstance(lg, logging.Logger)
    ]
    for lg in loggers:
        for h in lg.handlers:
            if not any(isinstance(f, RedactTokenFilter) for f in h.filters):
                h.addFilter(flt)


def install_log_redaction() -> None:
    """Idempotent. Call after logging handlers are configured (also safe before)."""
    global _installed
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    if not _installed:
        base_factory = logging.getLogRecordFactory()

        def factory(*args, **kwargs):  # type: ignore[no-untyped-def]
            return _redact_record(base_factory(*args, **kwargs))

        logging.setLogRecordFactory(factory)
        _installed = True
    _attach_filters()
