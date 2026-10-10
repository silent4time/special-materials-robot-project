"""
Thin HTTPS client for Bale Bot API (Telegram-compatible).

Chosen approach: direct HTTPS calls to https://tapi.bale.ai/bot<TOKEN>/<METHOD>
via httpx — avoids native build of python-bale-bot/aiohttp on some hosts, and
matches Bale's documented HTTP API.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import httpx

from config import BALE_API_BASE, BALE_BOT_TOKEN, POLL_TIMEOUT
from log_redact import redact

logger = logging.getLogger(__name__)


class BaleAPIError(RuntimeError):
    def __init__(self, method: str, description: str, payload: Any = None) -> None:
        # httpx errors embed the request URL (= token); never surface it to logs/users.
        description = redact(description)
        super().__init__(f"Bale API {method} failed: {description}")
        self.method = method
        self.description = description
        self.payload = payload


class BaleClient:
    def __init__(self, token: str | None = None, timeout: float = 60.0) -> None:
        self.token = (token or BALE_BOT_TOKEN).strip()
        if not self.token:
            raise ValueError("BALE_BOT_TOKEN تنظیم نشده است.")
        self.base = f"https://tapi.bale.ai/bot{self.token}"
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)
        self._offset = 0

    def close(self) -> None:
        self._client.close()

    def _call(self, method: str, data: dict | None = None, files: dict | None = None) -> Any:
        url = f"{self.base}/{method}"
        try:
            if files:
                resp = self._client.post(url, data=data or {}, files=files)
            else:
                resp = self._client.post(url, json=data or {})
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPError as exc:
            raise BaleAPIError(method, str(exc)) from exc
        if not body.get("ok"):
            raise BaleAPIError(method, body.get("description", "unknown"), body)
        return body.get("result")

    def get_me(self) -> dict:
        return self._call("getMe")

    def delete_webhook(self) -> bool:
        return bool(self._call("deleteWebhook"))

    def get_updates(self, timeout: int | None = None, limit: int = 50) -> list[dict]:
        payload = {
            "offset": self._offset,
            "timeout": timeout if timeout is not None else POLL_TIMEOUT,
            "limit": limit,
        }
        updates = self._call("getUpdates", payload) or []
        if updates:
            self._offset = max(u["update_id"] for u in updates) + 1
        return updates

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_markup: dict | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict:
        data: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup:
            data["reply_markup"] = reply_markup
        if reply_to_message_id is not None:
            data["reply_to_message_id"] = reply_to_message_id
        return self._call("sendMessage", data)

    def send_document(
        self,
        chat_id: int | str,
        file_path: Path | str,
        caption: str | None = None,
    ) -> dict:
        path = Path(file_path)
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        suffix = path.suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif suffix == ".xls":
            mime = "application/vnd.ms-excel"
        else:
            mime = "application/pdf"
        with path.open("rb") as fh:
            files = {"document": (path.name, fh, mime)}
            return self._call("sendDocument", data=data, files=files)

    def send_photo(
        self,
        chat_id: int | str,
        file_path: Path | str,
        caption: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict:
        """Send a local image via sendPhoto (multipart)."""
        path = Path(file_path)
        if not path.is_file():
            raise BaleAPIError("sendPhoto", f"file not found: {path}")
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
        with path.open("rb") as fh:
            files = {"photo": (path.name, fh, mime)}
            return self._call("sendPhoto", data=data, files=files)

    def try_set_my_photo(self, file_path: Path | str) -> tuple[bool, str]:
        """Attempt to set the bot profile photo. Bale may not support this.

        Returns (ok, detail). On unsupported API, returns (False, reason) without raising.
        """
        path = Path(file_path)
        if not path.is_file():
            return False, f"file not found: {path}"
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
        last_detail = "unsupported"
        for method in ("setMyPhoto", "setMyProfilePhoto", "setBotPhoto"):
            try:
                with path.open("rb") as fh:
                    files = {"photo": (path.name, fh, mime)}
                    self._call(method, data={}, files=files)
                return True, method
            except BaleAPIError as exc:
                last_detail = f"{method}: {exc.description}"
                logger.info("bot profile photo API unavailable via %s: %s", method, exc.description)
            except Exception as exc:  # noqa: BLE001
                last_detail = f"{method}: {exc}"
                logger.info("bot profile photo API error via %s: %s", method, exc)
        return False, last_detail

    def get_file(self, file_id: str) -> dict:
        return self._call("getFile", {"file_id": file_id})

    def download_file(self, file_id: str, dest: Path | str) -> Path:
        """
        Download a file by file_id.
        Bale returns file_path; we try tapi.bale.ai/file/bot<token>/<file_path>
        and fall back to getFile content URL patterns used by the platform.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        info = self.get_file(file_id)
        file_path = info.get("file_path") if isinstance(info, dict) else None

        candidates = []
        if file_path:
            candidates.append(f"https://tapi.bale.ai/file/bot{self.token}/{file_path}")
            if file_path.startswith("http"):
                candidates.insert(0, file_path)
        # Some Bale deployments return download via file_id endpoint
        candidates.append(f"{self.base}/getFile?file_id={file_id}")

        last_err: Exception | None = None
        for url in candidates:
            try:
                # Prefer binary fetch; skip JSON-only getFile response
                if url.endswith(str(file_id)) and "getFile" in url and not file_path:
                    continue
                r = self._client.get(url, timeout=120.0)
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                if "application/json" in ctype:
                    continue
                dest.write_bytes(r.content)
                if dest.stat().st_size > 0:
                    return dest
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning("download attempt failed for %s: %s", redact(url), redact(exc))

        # Last resort: if getFile result embeds file bytes (rare) — not available.
        # Try file_path relative to api host again with follow redirects.
        if file_path:
            url = f"https://tapi.bale.ai/file/bot{self.token}/{file_path.lstrip('/')}"
            try:
                r = self._client.get(url, follow_redirects=True, timeout=120.0)
                r.raise_for_status()
            except httpx.HTTPError as exc:
                raise BaleAPIError("download_file", str(exc)) from None
            dest.write_bytes(r.content)
            return dest

        raise BaleAPIError("download_file", f"نتوانست فایل را دانلود کند: {last_err}")

    @staticmethod
    def reply_keyboard(rows: list[list[str]], resize: bool = True) -> dict:
        return {
            "keyboard": [[{"text": t} for t in row] for row in rows],
            "resize_keyboard": resize,
            "one_time_keyboard": False,
        }

    @staticmethod
    def inline_url_keyboard(button_text: str, url: str) -> dict:
        """Inline keyboard with a single URL button (dict, same as reply_keyboard)."""
        return {
            "inline_keyboard": [[{"text": button_text, "url": url}]]
        }

    @staticmethod
    def inline_keyboard(rows: list[list[dict[str, str]]]) -> dict:
        """Inline keyboard from rows of button dicts (text + callback_data and/or url)."""
        return {"inline_keyboard": rows}

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        data: dict[str, Any] = {"callback_query_id": str(callback_query_id)}
        if text:
            data["text"] = text[:200]
        if show_alert:
            data["show_alert"] = True
        return bool(self._call("answerCallbackQuery", data))

    def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
    ) -> dict:
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup is not None:
            data["reply_markup"] = reply_markup
        return self._call("editMessageText", data)

    def edit_message_reply_markup(
        self,
        chat_id: int | str,
        message_id: int,
        reply_markup: dict | None = None,
    ) -> dict:
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        if reply_markup is not None:
            data["reply_markup"] = reply_markup
        return self._call("editMessageReplyMarkup", data)

    @staticmethod
    def remove_keyboard() -> dict:
        return {"remove_keyboard": True}
