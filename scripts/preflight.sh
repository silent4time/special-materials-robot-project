#!/usr/bin/env bash
# ExecStartPre (runs as root via "+"): self-heal lost system deps / venv before bot or web starts.
# Never blocks startup (unit uses "-" prefix). Output goes to journalctl.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec 9>/tmp/nasoz-preflight.lock
flock -w 900 9 || exit 0
PKGS=(tesseract-ocr tesseract-ocr-fas tesseract-ocr-eng python3-venv)
missing=()
if command -v dpkg >/dev/null; then
  for p in "${PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
fi
if ((${#missing[@]})) && [[ $EUID -eq 0 ]] && command -v apt-get >/dev/null; then
  echo "preflight: reinstalling ${missing[*]}"
  export DEBIAN_FRONTEND=noninteractive
  timeout 600 apt-get install -y --no-install-recommends "${missing[@]}" \
    || { timeout 300 apt-get update -y && timeout 600 apt-get install -y --no-install-recommends "${missing[@]}"; } || true
fi
if ! "$ROOT/.venv/bin/python" -c 'import uvicorn, httpx, pytesseract, dotenv' >/dev/null 2>&1; then
  owner="$(stat -c %U "$ROOT")"
  echo "preflight: repairing venv (as $owner)"
  run() { if [[ $EUID -eq 0 && "$owner" != root ]]; then runuser -u "$owner" -- "$@"; else "$@"; fi; }
  if ! "$ROOT/.venv/bin/python" -m pip --version >/dev/null 2>&1; then rm -rf "$ROOT/.venv"; run python3 -m venv "$ROOT/.venv"; fi
  run "$ROOT/.venv/bin/python" -m pip install -q -r "$ROOT/requirements.txt" || true
fi
# assistant: if Ollama is installed but not answering, bring it up (systemd unit, else setsid supervisor)
if command -v ollama >/dev/null 2>&1 && ! curl -fsS -m 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  if [[ -d /run/systemd/system ]] && systemctl cat ollama >/dev/null 2>&1; then
    [[ $EUID -eq 0 ]] && systemctl start ollama >/dev/null 2>&1 || true
  else
    timeout 60 bash "$ROOT/scripts/ollama_ensure.sh" || true
  fi
fi
exit 0
