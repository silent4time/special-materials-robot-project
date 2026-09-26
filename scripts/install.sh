#!/usr/bin/env bash
# نصب و راه‌اندازی بازوی مواد تاندیش (بله) روی سرور لینوکس
# Usage:
#   bash install.sh                 # فقط venv + deps + راهنما
#   bash install.sh --seed-admin    # + seed مالک
#   bash install.sh --systemd       # + نصب unit systemd
#   bash install.sh --start         # اجرا (foreground یا systemd)
#   bash install.sh --update        # git pull + pip + restart
# Env:
#   INSTALL_DIR=/path/to/dir        # مسیر کلون/نصب (اختیاری)
set -euo pipefail

REPO_URL="https://github.com/silent4time/special-materials-robot-project.git"
REPO_NAME="special-materials-robot-project"
SERVICE_NAME="nasoz-bot"
MIN_PY_MAJOR=3
MIN_PY_MINOR=10

DO_SEED=0
DO_SYSTEMD=0
DO_START=0
DO_UPDATE=0

for arg in "$@"; do
  case "$arg" in
    --seed-admin) DO_SEED=1 ;;
    --systemd)    DO_SYSTEMD=1 ;;
    --start)      DO_START=1 ;;
    --update)     DO_UPDATE=1 ;;
    -h|--help)
      cat <<'HELP'
Usage: bash install.sh [options]

  (default)     ساخت venv، نصب وابستگی‌ها، کپی .env.example در صورت نبود
  --seed-admin  اجرای scripts/seed_admin.py (بعد از تنظیم .env)
  --systemd     نصب سرویس systemd از scripts/nasoz-bot.service.in
  --start       اجرای ربات (اگر systemd نصب باشد: systemctl start؛ وگرنه foreground)
  --update      git pull + pip install -r requirements.txt + restart systemd
  -h, --help    این راهنما

متغیر محیطی:
  INSTALL_DIR   مسیر نصب / کلون (پیش‌فرض: cwd اگر داخل ریپو باشد، وگرنه ./special-materials-robot-project)
HELP
      exit 0
      ;;
    *)
      echo "پرچم ناشناخته: $arg (از --help ببینید)" >&2
      exit 2
      ;;
  esac
done

log()  { printf '%s\n' "$*"; }
warn() { printf '⚠ %s\n' "$*" >&2; }
die()  { printf '✗ %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

# --- Python version check ---
check_python() {
  if ! need_cmd python3; then
    return 1
  fi
  local ver major minor
  ver="$(python3 -c 'import sys; print("%d.%d" % (sys.version_info[0], sys.version_info[1]))')"
  major="${ver%%.*}"
  minor="${ver#*.}"
  if (( major < MIN_PY_MAJOR )) || (( major == MIN_PY_MAJOR && minor < MIN_PY_MINOR )); then
    die "Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ لازم است (فعلی: ${ver})."
  fi
  log "✓ Python ${ver}"
  return 0
}

# --- apt helpers (Ubuntu/Debian-like) ---
maybe_sudo() {
  if (( EUID == 0 )); then
    "$@"
  elif need_cmd sudo; then
    sudo "$@"
  else
    die "برای نصب بسته‌ها root یا sudo لازم است: $*"
  fi
}

ensure_apt_packages() {
  local missing=()
  need_cmd python3 || missing+=(python3)
  python3 -c 'import venv' 2>/dev/null || missing+=(python3-venv)
  need_cmd git || missing+=(git)
  # ensure pip module available for venv bootstrap on some distros
  python3 -c 'import ensurepip' 2>/dev/null || true

  if ((${#missing[@]} == 0)); then
    return 0
  fi

  if ! need_cmd apt-get; then
    die "بسته‌های لازم یافت نشد (${missing[*]}) و apt-get در دسترس نیست. دستی نصب کنید."
  fi

  log "نصب بسته‌های سیستم: ${missing[*]}"
  maybe_sudo apt-get update -y
  maybe_sudo apt-get install -y "${missing[@]}"
}

# --- Locate / clone repo ---
is_repo_root() {
  local d="$1"
  [[ -f "$d/main.py" && -f "$d/requirements.txt" && -d "$d/scripts" ]]
}

resolve_workdir() {
  local here script_dir candidate
  here="$(pwd)"
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  if [[ -n "${INSTALL_DIR:-}" ]]; then
    candidate="$INSTALL_DIR"
    if is_repo_root "$candidate"; then
      echo "$candidate"
      return
    fi
    if [[ -d "$candidate" ]] && ! is_repo_root "$candidate"; then
      # empty-ish dir: clone into it if no main.py yet
      if [[ ! -e "$candidate/main.py" ]]; then
        log "کلون به INSTALL_DIR=$candidate ..."
        git clone "$REPO_URL" "$candidate"
      fi
      is_repo_root "$candidate" || die "INSTALL_DIR ریپوی معتبر نیست: $candidate"
      echo "$candidate"
      return
    fi
    mkdir -p "$(dirname "$candidate")"
    git clone "$REPO_URL" "$candidate"
    echo "$candidate"
    return
  fi

  # Running from scripts/ inside a checkout
  if is_repo_root "$(dirname "$script_dir")"; then
    echo "$(cd "$(dirname "$script_dir")" && pwd)"
    return
  fi

  # Already in repo cwd
  if is_repo_root "$here"; then
    echo "$here"
    return
  fi

  # Clone beside cwd
  candidate="$here/$REPO_NAME"
  if is_repo_root "$candidate"; then
    echo "$candidate"
    return
  fi
  log "کلون ریپو به $candidate ..."
  git clone "$REPO_URL" "$candidate"
  echo "$candidate"
}

setup_venv() {
  local root="$1"
  cd "$root"
  if [[ ! -d .venv ]]; then
    log "ساخت محیط مجازی .venv ..."
    python3 -m venv .venv
  else
    log "✓ .venv موجود است"
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -U pip
  pip install -r requirements.txt
  log "✓ وابستگی‌های Python نصب شد"
}

ensure_env() {
  local root="$1"
  cd "$root"
  if [[ -f .env ]]; then
    log "✓ فایل .env موجود است"
    return 0
  fi
  if [[ ! -f .env.example ]]; then
    die ".env.example پیدا نشد."
  fi
  cp .env.example .env
  cat <<'PERSIAN'

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
فایل .env ساخته شد — حتماً ویرایش کنید:

  nano .env

حداقل این دو مقدار را پر کنید (توکن جعلی نسازید):

  BALE_BOT_TOKEN=...          # از @botfather در بله
  ADMIN_BALE_USER_ID=...      # شناسه عددی شما در بله

سپس:

  bash install.sh --seed-admin
  bash install.sh --systemd --start
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PERSIAN
}

env_looks_configured() {
  local root="$1"
  [[ -f "$root/.env" ]] || return 1
  # Non-placeholder token: not empty and not the example stub
  grep -qE '^BALE_BOT_TOKEN=[0-9]+:.+' "$root/.env" 2>/dev/null || return 1
  grep -qE '^ADMIN_BALE_USER_ID=[0-9]+' "$root/.env" 2>/dev/null || return 1
  # Reject obvious placeholders from .env.example
  if grep -qE '^BALE_BOT_TOKEN=123456789:' "$root/.env" 2>/dev/null; then
    return 1
  fi
  if grep -qE '^ADMIN_BALE_USER_ID=123456789$' "$root/.env" 2>/dev/null; then
    return 1
  fi
  return 0
}

run_seed_admin() {
  local root="$1"
  cd "$root"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  if ! env_looks_configured "$root"; then
    warn "به نظر می‌رسد .env هنوز با توکن/شناسه واقعی پر نشده."
    warn "ابتدا nano .env را ویرایش کنید، بعد دوباره --seed-admin بزنید."
    return 1
  fi
  python scripts/seed_admin.py
}

install_systemd() {
  local root="$1"
  local user template unit_path rendered
  user="$(id -un)"
  template="$root/scripts/nasoz-bot.service.in"
  [[ -f "$template" ]] || die "قالب سرویس پیدا نشد: $template"
  unit_path="/etc/systemd/system/${SERVICE_NAME}.service"
  rendered="$(mktemp)"
  sed -e "s|__WORKDIR__|${root}|g" -e "s|__USER__|${user}|g" "$template" > "$rendered"
  log "نصب systemd unit به $unit_path (User=$user) ..."
  maybe_sudo cp "$rendered" "$unit_path"
  rm -f "$rendered"
  maybe_sudo systemctl daemon-reload
  maybe_sudo systemctl enable "$SERVICE_NAME"
  log "✓ سرویس $SERVICE_NAME فعال شد (هنوز start نشده مگر با --start)"
}

service_installed() {
  [[ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]] \
    || systemctl list-unit-files "${SERVICE_NAME}.service" 2>/dev/null | grep -q "$SERVICE_NAME"
}

do_start() {
  local root="$1"
  if service_installed && need_cmd systemctl; then
    log "شروع سرویس systemd: $SERVICE_NAME"
    maybe_sudo systemctl start "$SERVICE_NAME"
    maybe_sudo systemctl --no-pager --full status "$SERVICE_NAME" || true
    log "لاگ زنده: journalctl -u $SERVICE_NAME -f"
    return 0
  fi
  cd "$root"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  if ! env_looks_configured "$root"; then
    die "قبل از --start باید .env را با توکن واقعی پر کنید."
  fi
  warn "systemd نصب نیست — اجرای foreground. برای توقف: Ctrl+C"
  warn "فقط یک نمونه polling با یک توکن مجاز است."
  exec python main.py
}

do_update() {
  local root="$1"
  cd "$root"
  log "git pull ..."
  git pull --ff-only
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -U pip
  pip install -r requirements.txt
  if service_installed && need_cmd systemctl; then
    log "restart $SERVICE_NAME ..."
    maybe_sudo systemctl restart "$SERVICE_NAME"
    maybe_sudo systemctl --no-pager --full status "$SERVICE_NAME" || true
  else
    log "systemd نصب نیست؛ در صورت اجرای دستی، ربات را خودتان restart کنید."
  fi
  log "✓ به‌روزرسانی تمام شد"
}

print_next_steps() {
  local root="$1"
  cat <<PERSIAN

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
نصب پایه تمام شد. مسیر پروژه: $root

مراحل بعدی:

  1) ویرایش تنظیمات:
       cd $root && nano .env
     (BALE_BOT_TOKEN و ADMIN_BALE_USER_ID)

  2) ثبت مالک:
       bash install.sh --seed-admin

  3) اجرای دائمی:
       bash install.sh --systemd --start

هشدار: فقط یک نمونه از بازو با همان توکن باید long-poll کند.
قبل از سرور تولید، ربات موقت روی ماشین توسعه را خاموش کنید.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PERSIAN
}

# ========== main ==========
ensure_apt_packages
check_python || { ensure_apt_packages; check_python; }

WORKDIR="$(resolve_workdir)"
log "WORKDIR=$WORKDIR"

if (( DO_UPDATE )); then
  do_update "$WORKDIR"
  exit 0
fi

setup_venv "$WORKDIR"
ensure_env "$WORKDIR"

if (( DO_SEED )); then
  run_seed_admin "$WORKDIR" || true
fi

if (( DO_SYSTEMD )); then
  install_systemd "$WORKDIR"
fi

if (( DO_START )); then
  do_start "$WORKDIR"
  exit 0
fi

print_next_steps "$WORKDIR"
