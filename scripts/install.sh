#!/usr/bin/env bash
# نصب و راه‌اندازی بازوی مواد تاندیش (بله) روی سرور لینوکس
# Usage:
#   bash install.sh                 # venv + deps + ویزارد تعاملی .env (در TTY)
#   bash install.sh --seed-admin    # اختیاری؛ مالک با اولین /start ساخته می‌شود
#   bash install.sh --systemd       # + نصب unit systemd
#   bash install.sh --start         # اجرا (foreground یا systemd)
#   bash install.sh --update        # git pull + pip + restart
# Env:
#   INSTALL_DIR=/path/to/dir        # مسیر کلون/نصب (اختیاری)
#   BALE_BOT_TOKEN=...              # در حالت غیرتعاملی، از env هم پذیرفته می‌شود
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

  (default)     ساخت venv، نصب وابستگی‌ها، ویزارد تعاملی .env (در ترمینال واقعی)
  --seed-admin  اختیاری؛ مالک با اولین /start ساخته می‌شود (scripts/seed_admin.py)
  --systemd     نصب سرویس systemd از scripts/nasoz-bot.service.in
  --start       اجرای ربات (اگر systemd نصب باشد: systemctl start؛ وگرنه foreground)
  --update      git pull + pip install -r requirements.txt + restart systemd
  -h, --help    این راهنما

متغیر محیطی:
  INSTALL_DIR      مسیر نصب / کلون (پیش‌فرض: cwd اگر داخل ریپو باشد، وگرنه ./special-materials-robot-project)
  BALE_BOT_TOKEN   در نصب غیرتعاملی (piped)، اگر ست باشد در .env نوشته می‌شود
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
  if env_looks_configured "$root"; then
    log "✓ فایل .env پیکربندی شده است (توکن موجود)"
    return 0
  fi

  # Prefer interactive wizard when stdin is a TTY
  if [[ -t 0 ]]; then
    run_env_wizard "$root"
    return $?
  fi

  # Non-interactive (e.g. curl | bash): env var or stub
  if [[ -n "${BALE_BOT_TOKEN:-}" ]] && token_format_ok "${BALE_BOT_TOKEN}"; then
    write_env_file "$root" "${BALE_BOT_TOKEN}" "${BOT_USERNAME:-nasoz_bot}" "${CRITICAL_DAYS:-3}" ""
    log "✓ .env از متغیر محیطی BALE_BOT_TOKEN نوشته شد (…${BALE_BOT_TOKEN: -4})"
    return 0
  fi

  if [[ ! -f .env ]]; then
    if [[ ! -f .env.example ]]; then
      die ".env.example پیدا نشد."
    fi
    cp .env.example .env
  fi
  cat <<PERSIAN

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
نصب غیرتعاملی (بدون TTY): فایل .env از روی نمونه ساخته/باقی ماند.

برای ویزارد تعاملی توکن، در یک ترمینال واقعی اجرا کنید:

  cd $root && bash install.sh

یا قبل از نصب متغیر را ست کنید:

  export BALE_BOT_TOKEN='digits:rest'
  bash install.sh

مالک با اولین /start در بله ساخته می‌شود (ADMIN لازم نیست).
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PERSIAN
}

token_format_ok() {
  local t="$1"
  [[ "$t" =~ ^[0-9]+:.+$ ]] || return 1
  # reject obvious placeholder
  [[ "$t" != 123456789:* ]] || return 1
  return 0
}

validate_token_getme() {
  local token="$1"
  local url resp ok
  GETME_USERNAME=""
  url="https://tapi.bale.ai/bot${token}/getMe"
  if ! need_cmd curl; then
    warn "curl نیست — اعتبارسنجی getMe رد شد."
    return 0
  fi
  resp="$(curl -fsS --max-time 12 "$url" 2>/dev/null || true)"
  if printf '%s' "$resp" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
    local uname json
    json="$(printf '%s' "$resp" | tr -d '\r\n')"
    uname="$(printf '%s' "$json" | sed -n 's/.*"result"[[:space:]]*:[[:space:]]*{[^}]*"username"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    if [[ -n "$uname" ]]; then
      GETME_USERNAME="$uname"
      log "✓ توکن معتبر است (getMe: @$uname)"
    else
      log "✓ توکن معتبر است (getMe ok)"
    fi
    return 0
  fi
  warn "getMe ناموفق بود — توکن را دوباره بررسی کنید."
  return 1
}

write_env_file() {
  local root="$1" token="$2" bot_user="$3" critical="$4" admin_id="$5"
  local tmp
  tmp="$(mktemp)"
  {
    printf '%s\n' "# تولید شده توسط install.sh — توکن را در گیت commit نکنید"
    printf 'BALE_BOT_TOKEN=%s\n' "$token"
    printf '\n'
    printf '%s\n' "# اختیاری: اگر خالی باشد، اولین /start مالک می‌شود"
    if [[ -n "$admin_id" ]]; then
      printf 'ADMIN_BALE_USER_ID=%s\n' "$admin_id"
    else
      printf '%s\n' "# ADMIN_BALE_USER_ID="
    fi
    printf '\n'
    if [[ -n "$bot_user" ]]; then
      printf 'BOT_USERNAME=%s\n' "$bot_user"
    else
      printf '%s\n' "# BOT_USERNAME=nasoz_bot"
    fi
    printf 'CRITICAL_DAYS=%s\n' "${critical:-3}"
  } > "$tmp"
  mv "$tmp" "$root/.env"
  chmod 600 "$root/.env" 2>/dev/null || true
}

prompt_default() {
  # usage: prompt_default "پرسش" "پیش‌فرض" → echoes answer
  local prompt="$1" default="${2:-}" reply
  if [[ -n "$default" ]]; then
    read -r -p "${prompt} [${default}]: " reply || true
    printf '%s' "${reply:-$default}"
  else
    read -r -p "${prompt}: " reply || true
    printf '%s' "$reply"
  fi
}

run_env_wizard() {
  local root="$1"
  local token bot_user bot_user_default suggested_username previous_bot_user do_systemd critical admin_existing
  cd "$root"

  cat <<'WIZ'

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ویزارد پیکربندی بازو (بله)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WIZ

  # Token (required)
  while true; do
    read -r -p "۱) توکن بازو (BALE_BOT_TOKEN): " token || true
    token="$(printf '%s' "$token" | tr -d '[:space:]')"
    if [[ -z "$token" ]]; then
      warn "توکن الزامی است."
      continue
    fi
    if ! token_format_ok "$token"; then
      warn "فرمت توکن نامعتبر است (باید شبیه digits:rest باشد)."
      continue
    fi
    if validate_token_getme "$token"; then
      suggested_username="$GETME_USERNAME"
      break
    fi
    local retry
    read -r -p "با همین توکن ادامه دهیم؟ (y/N): " retry || true
    case "${retry:-}" in
      y|Y|yes|YES) break ;;
      *) continue ;;
    esac
  done

  # Keep an existing username as a fallback, but always ask the question.
  previous_bot_user=""
  if [[ -f .env ]]; then
    previous_bot_user="$(sed -n 's/^BOT_USERNAME=//p' .env | head -1 || true)"
    previous_bot_user="${previous_bot_user#\"}"
    previous_bot_user="${previous_bot_user%\"}"
    previous_bot_user="${previous_bot_user#\'}"
    previous_bot_user="${previous_bot_user%\'}"
    previous_bot_user="$(printf '%s' "$previous_bot_user" | tr -d '@[:space:]')"
  fi
  bot_user_default="${suggested_username:-$previous_bot_user}"
  while true; do
    bot_user="$(prompt_default "۲) نام کاربری ربات در بله (بدون @، برای لینک دعوت ضروری است)" "$bot_user_default")"
    bot_user="$(printf '%s' "$bot_user" | tr -d '@[:space:]')"
    if [[ -n "$bot_user" ]]; then
      break
    fi
    warn "نام کاربری ربات الزامی است."
  done

  do_systemd="$(prompt_default "۳) آیا همین الان سرویس systemd نصب و استارت شود؟ (y/N)" "N")"

  critical="$(prompt_default "۴) CRITICAL_DAYS" "3")"
  if ! [[ "$critical" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    warn "CRITICAL_DAYS نامعتبر — از ۳ استفاده می‌شود."
    critical=3
  fi

  write_env_file "$root" "$token" "$bot_user" "$critical" ""
  log "✓ فایل .env نوشته شد (توکن …${token: -4})"
  log "نام کاربری ذخیره‌شده: @${bot_user}"
  log "نکته: ADMIN_BALE_USER_ID لازم نیست — اولین /start مالک می‌شود."

  case "${do_systemd:-}" in
    y|Y|yes|YES)
      DO_SYSTEMD=1
      DO_START=1
      ;;
  esac
  return 0
}

env_looks_configured() {
  local root="$1" line token
  [[ -f "$root/.env" ]] || return 1
  line="$(grep -E '^BALE_BOT_TOKEN=' "$root/.env" 2>/dev/null | head -1 || true)"
  [[ -n "$line" ]] || return 1
  token="${line#BALE_BOT_TOKEN=}"
  token="$(printf '%s' "$token" | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//')"
  token_format_ok "$token"
}

run_seed_admin() {
  local root="$1"
  cd "$root"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  if ! env_looks_configured "$root"; then
    warn "به نظر می‌رسد .env هنوز با توکن واقعی پر نشده."
    warn "ابتدا bash install.sh را در ترمینال واقعی بزنید یا nano .env کنید."
    return 1
  fi
  # Soften: seed only works if ADMIN_BALE_USER_ID is set
  if ! grep -qE '^ADMIN_BALE_USER_ID=[0-9]+' "$root/.env" 2>/dev/null; then
    warn "--seed-admin اختیاری است؛ ADMIN_BALE_USER_ID خالی است."
    warn "مالک با اولین /start ساخته می‌شود — seed رد شد."
    return 0
  fi
  if grep -qE '^ADMIN_BALE_USER_ID=123456789$' "$root/.env" 2>/dev/null; then
    warn "ADMIN_BALE_USER_ID هنوز مقدار نمونه است — seed رد شد."
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
    die "قبل از --start باید .env را با توکن واقعی پر کنید (ویزارد: bash install.sh)."
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

  1) اگر هنوز توکن ندارید، در ترمینال واقعی:
       cd $root && bash install.sh
     (ویزارد توکن و نام کاربری ربات را می‌پرسد و .env می‌نویسد)

  2) ربات را روشن کنید و در بله /start بزنید —
     اولین کاربر به‌صورت خودکار مالک می‌شود.
     (--seed-admin اختیاری است اگر ADMIN_BALE_USER_ID را دستی گذاشته‌اید)

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
