#!/usr/bin/env bash
# Thin wrapper — one-liner UX: curl …/install.sh | bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/scripts/install.sh" "$@"
