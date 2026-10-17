"""دستیار صوتی آفلاین (اختیاری): Whisper (گفتار→متن) + Ollama (مدل زبانی) + Piper (متن→گفتار).

همهٔ وابستگی‌ها اختیاری‌اند و فقط هنگام نیاز import می‌شوند؛ اگر نصب نباشند ربات از کار
نمی‌افتد و فقط پیام «دستیار صوتی روی سرور نصب نیست» نشان داده می‌شود.
فقط مالک می‌تواند آن را روشن/خاموش کند (⚙️ تنظیمات ← 🤖 دستیار هوشمند صوتی).

نصب لازم برای اجرای واقعی (هیچ‌کدام خودکار دانلود نمی‌شود):
  pip install faster-whisper requests        (+ مدل Whisper، «small» ≈ ۴۶۰MB)
  Ollama + مدل (مثلاً qwen2.5:3b ≈ ۱.۹GB)  روی http://127.0.0.1:11434
  باینری piper + مدل فارسی fa_IR-*-medium.onnx (≈ ۶۰MB) در /opt/piper/models/
"""
from __future__ import annotations

import importlib.util
import logging
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_WHISPER = "small"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_LLM = "qwen2.5:3b"
PIPER_MODEL_DIR = Path("/opt/piper/models")
DEFAULT_PIPER_MODEL = "fa_IR-donya-medium"

SETTING_ENABLED = "assistant_enabled"
SETTING_MODEL = "assistant_model"
NOT_INSTALLED_FA = "دستیار صوتی روی سرور نصب نیست."


def _ollama_up(base_url: str = DEFAULT_OLLAMA_URL) -> bool:
    try:
        import requests  # noqa: PLC0415

        return requests.get(f"{base_url}/api/tags", timeout=2).ok
    except Exception:  # noqa: BLE001
        return False


def availability() -> dict[str, bool]:
    """Which components exist on this server (cheap checks, no model loading)."""
    return {
        "whisper": importlib.util.find_spec("faster_whisper") is not None,
        "ollama": _ollama_up(),
        "piper": shutil.which("piper") is not None,
    }


def is_installed() -> bool:
    """Minimum to answer: speech-to-text + LLM (Piper optional → text reply)."""
    a = availability()
    return a["whisper"] and a["ollama"]


def status_text_fa(enabled: bool, model: str | None) -> str:
    a = availability()
    ok = lambda b: "✅" if b else "❌"  # noqa: E731
    lines = [
        "🤖 دستیار هوشمند صوتی",
        f"وضعیت: {'فعال' if enabled else 'غیرفعال'}",
        f"مدل زبانی: {model or DEFAULT_LLM}",
        "",
        f"{ok(a['whisper'])} Whisper (گفتار→متن)",
        f"{ok(a['ollama'])} Ollama (مدل زبانی محلی)",
        f"{ok(a['piper'])} Piper (متن→گفتار، اختیاری)",
    ]
    if not (a["whisper"] and a["ollama"]):
        lines += ["", "⚠ " + NOT_INSTALLED_FA + " تا نصب نشود قابل فعال‌سازی نیست."]
    lines += ["", "فقط مالک می‌تواند این تنظیمات را تغییر دهد."]
    return "\n".join(lines)


@lru_cache(maxsize=2)
def _whisper(model: str):
    from faster_whisper import WhisperModel  # noqa: PLC0415

    return WhisperModel(model, device="cpu", compute_type="int8")


def transcribe(audio_path: Path, model: str = DEFAULT_WHISPER) -> str:
    try:
        segments, _ = _whisper(model).transcribe(str(audio_path), language="fa")
        return " ".join(s.text.strip() for s in segments if s.text).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("whisper transcribe failed: %s", exc)
        return ""


def ask_ollama(prompt: str, model: str = DEFAULT_LLM, base_url: str = DEFAULT_OLLAMA_URL, timeout: int = 120) -> str:
    try:
        import requests  # noqa: PLC0415

        r = requests.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "system": "تو دستیار ربات مواد نسوز هستی. کوتاه و فقط به فارسی پاسخ بده.",
                "stream": False,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return (r.json().get("response") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ollama failed: %s", exc)
        return ""


def synthesize(text: str, out_path: Path, model: str = DEFAULT_PIPER_MODEL) -> bool:
    if not text.strip() or not shutil.which("piper"):
        return False
    onnx = PIPER_MODEL_DIR / f"{model}.onnx"
    try:
        proc = subprocess.run(
            ["piper", "--model", str(onnx), "--output_file", str(out_path)],
            input=text.encode("utf-8"), capture_output=True, timeout=90, check=False,
        )
        return proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 100
    except Exception as exc:  # noqa: BLE001
        logger.warning("piper failed: %s", exc)
        return False
