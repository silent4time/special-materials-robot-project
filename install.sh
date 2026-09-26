#!/usr/bin/env bash
# Thin wrapper / curl|bash bootstrap → scripts/install.sh
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/silent4time/special-materials-robot-project/main/install.sh | bash
#   bash install.sh [--seed-admin|--systemd|--start|--update]
set -euo pipefail

REPO_URL="https://github.com/silent4time/special-materials-robot-project.git"
REPO_NAME="special-materials-robot-project"

# Local checkout: exec sibling scripts/install.sh
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "-" && "${BASH_SOURCE[0]}" != "/dev/stdin" ]]; then
  if SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"; then
    if [[ -f "$SCRIPT_DIR/scripts/install.sh" ]]; then
      exec bash "$SCRIPT_DIR/scripts/install.sh" "$@"
    fi
  fi
fi

# Piped from curl (or missing scripts/): clone then re-exec
need_cmd() { command -v "$1" >/dev/null 2>&1; }
if ! need_cmd git; then
  if need_cmd apt-get; then
    if (( EUID == 0 )); then apt-get update -y && apt-get install -y git
    elif need_cmd sudo; then sudo apt-get update -y && sudo apt-get install -y git
    else echo "git لازم است." >&2; exit 1
    fi
  else
    echo "git لازم است." >&2; exit 1
  fi
fi

DEST="${INSTALL_DIR:-$PWD/$REPO_NAME}"
if [[ -f "$DEST/scripts/install.sh" ]]; then
  echo "ریپو موجود است: $DEST"
else
  echo "کلون $REPO_URL → $DEST ..."
  git clone "$REPO_URL" "$DEST"
fi
cd "$DEST"
exec bash "$DEST/scripts/install.sh" "$@"
