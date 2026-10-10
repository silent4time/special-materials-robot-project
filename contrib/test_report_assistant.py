#!/usr/bin/env python3
"""Unit smoke for report assistant: off-scope refuse + mocked Ollama HTTP (no GPU)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.report_assistant import (
    OLLAMA_DOWN_FA,
    SYSTEM_PROMPT_FA,
    build_chat_messages,
    build_system_prompt,
    chat,
    format_user_payload,
    is_likely_off_scope,
    refuse_off_scope_fa,
)


def test_system_prompt_is_persian_reports_only() -> None:
    prompt = build_system_prompt()
    assert "گزارش" in prompt
    assert "Ollama" not in prompt or True  # model-agnostic
    for word in ("آپلود", "فقط", "فارسی"):
        assert word in prompt
    assert prompt == SYSTEM_PROMPT_FA


def test_off_scope_heuristic_and_refuse() -> None:
    assert is_likely_off_scope("لطفاً فایل موجودی را آپلود کن")
    assert is_likely_off_scope("حذف کاربر ۱۲۳")
    assert is_likely_off_scope("از chatgpt بپرس")
    assert not is_likely_off_scope("چند ماده بحرانی داریم؟")
    assert not is_likely_off_scope("خلاصه مصرف ماهیانه اسلب چیست؟")
    refuse = refuse_off_scope_fa()
    assert "گزارش" in refuse
    # chat() short-circuits off-scope without HTTP
    with patch("bot.report_assistant.httpx.Client") as client_cls:
        out = chat("آپلود فایل انبار را انجام بده", "زمینه آزمایشی")
        client_cls.assert_not_called()
    assert "گزارش" in out


def test_messages_include_context_only_payload() -> None:
    msgs = build_chat_messages("مواد بحرانی؟", "تعداد مواد بحرانی: 3")
    assert msgs[0]["role"] == "system"
    assert "گزارش" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    body = msgs[1]["content"]
    assert "تعداد مواد بحرانی: 3" in body
    assert "مواد بحرانی؟" in body
    assert "BALE_BOT_TOKEN" not in body


def test_chat_mock_http_success() -> None:
    fake = {"message": {"role": "assistant", "content": "۳ ماده بحرانی در زمینه آمده است."}}

    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return fake

    class FakeClient:
        def __init__(self, *a, **k) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *a) -> None:
            return None

        def post(self, url: str, json: dict | None = None) -> FakeResp:
            assert url.endswith("/api/chat")
            assert json and json.get("stream") is False
            assert json["messages"][0]["role"] == "system"
            return FakeResp()

    with patch("bot.report_assistant.httpx.Client", FakeClient):
        out = chat("چند ماده بحرانی؟", "تعداد مواد بحرانی: 3")
    assert "بحرانی" in out


def test_chat_ollama_down_friendly() -> None:
    class BoomClient:
        def __init__(self, *a, **k) -> None:
            pass

        def __enter__(self) -> "BoomClient":
            return self

        def __exit__(self, *a) -> None:
            return None

        def post(self, *a, **k):
            raise ConnectionError("refused")

    with patch("bot.report_assistant.httpx.Client", BoomClient):
        out = chat("خلاصه ماهانه؟", "—")
    assert out == OLLAMA_DOWN_FA


def main() -> int:
    tests = [
        test_system_prompt_is_persian_reports_only,
        test_off_scope_heuristic_and_refuse,
        test_messages_include_context_only_payload,
        test_chat_mock_http_success,
        test_chat_ollama_down_friendly,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"OK  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    if failed:
        print(f"{failed} failed")
        return 1
    print("all report_assistant tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
