#!/usr/bin/env bash
# Bootstrap نصب بازوی مواد (بله) → scripts/install.sh
# نصب یک‌خطی (پیشنهادی):
#   bash <(curl -fsSL https://raw.githubusercontent.com/silent4time/special-materials-robot-project/main/install.sh)
# یا:
#   curl -fsSL https://raw.githubusercontent.com/silent4time/special-materials-robot-project/main/install.sh | bash
# داخل ریپو:
#   bash install.sh [--update|--repair|--web-login|--dry-run|--help]
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/silent4time/special-materials-robot-project.git}"
REPO_NAME="special-materials-robot-project"
REPO_BRANCH="${REPO_BRANCH:-main}"

# اگر stdin ترمینال نیست (curl | bash) ولی ترمینال کنترلی داریم، ورودی را از /dev/tty بخوان
# تا ویزارد بتواند سؤال بپرسد. (اسکریپت قبلاً کامل در bash بارگذاری شده چون همه‌چیز داخل main است.)
reattach_tty() {
  if [[ ! -t 0 ]] && [[ "${NONINTERACTIVE:-0}" != "1" ]] && (exec </dev/tty) 2>/dev/null; then
    exec </dev/tty
  fi
}

need_cmd() { command -v "$1" >/dev/null 2>&1; }

main() {
  # Local checkout: exec sibling scripts/install.sh
  local src="${BASH_SOURCE[0]:-}" script_dir
  if [[ -n "$src" && "$src" != "-" && "$src" != /dev/stdin && "$src" != /dev/fd/* && "$src" != /proc/* && -f "$src" ]]; then
    script_dir="$(cd "$(dirname "$src")" && pwd)"
    if [[ -f "$script_dir/scripts/install.sh" ]]; then
      reattach_tty
      exec bash "$script_dir/scripts/install.sh" "$@"
    fi
  fi

  # Piped from curl / process substitution: clone (or reuse) then exec
  if ! need_cmd git; then
    echo "نصب git ..."
    if need_cmd apt-get; then
      if (( EUID == 0 )); then apt-get update -y && apt-get install -y git ca-certificates
      elif need_cmd sudo; then sudo apt-get update -y && sudo apt-get install -y git ca-certificates
      else echo "✗ git لازم است و root/sudo در دسترس نیست." >&2; exit 1
      fi
    else
      echo "✗ git لازم است (apt-get پیدا نشد)." >&2; exit 1
    fi
  fi

  local dest="${INSTALL_DIR:-$PWD/$REPO_NAME}"
  if [[ -f "$dest/scripts/install.sh" ]]; then
    echo "ریپو موجود است: $dest (به‌روزرسانی امن با git pull --ff-only)"
    git -C "$dest" pull --ff-only || echo "⚠ git pull ناموفق بود؛ با نسخهٔ موجود ادامه می‌دهیم." >&2
  else
    echo "کلون $REPO_URL → $dest ..."
    git clone --branch "$REPO_BRANCH" "$REPO_URL" "$dest"
  fi
  cd "$dest"
  export INSTALL_DIR="$dest"
  reattach_tty
  exec bash "$dest/scripts/install.sh" "$@"
}

main "$@"
