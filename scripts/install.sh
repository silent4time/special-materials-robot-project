#!/usr/bin/env bash
# نصب و راه‌اندازی کامل بازوی مواد (بله) + پنل وب روی سرور لینوکس (Ubuntu 22.04/24.04، Debian 12)
# Usage: bash install.sh [options]   (--help)
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/silent4time/special-materials-robot-project.git}"
REPO_NAME="special-materials-robot-project"
BOT_SERVICE="nasoz-bot"
WEB_SERVICE="nasoz-web"
MIN_PY_MINOR=10
ONE_LINER="bash <(curl -fsSL https://raw.githubusercontent.com/silent4time/special-materials-robot-project/main/install.sh)"

DO_SEED=0; DO_UPDATE=0; DO_REPAIR=0; DO_WEB_LOGIN=0; DRY_RUN=0; DO_START_ONLY=0
SYSTEMD_CHOICE="${INSTALL_SYSTEMD:-}"   # y / n / "" (ask)
DO_ASSISTANT=0                          # 0=ask, 1=install, 2=skip
RECONFIGURE=0
DO_SYSUP=0                              # 0=ask (fresh install, default yes), 1=yes, 2=no
DO_SYSUP_ONLY=0

usage() {
  cat <<'HELP'
Usage: bash install.sh [options]

  (بدون پرچم)        نصب کامل: بسته‌های سیستم + venv + ویزارد .env + سرویس‌ها + تست سلامت
  --update           به‌روزرسانی برنامه: پشتیبان DB + git pull + pip install -r requirements + migrate
                     + restart سرویس‌ها + تست سلامت (.env و data حفظ می‌شود)
  --system-upgrade   ارتقای سیستم‌عامل: apt-get update && apt-get upgrade -y (غیرتعاملی، فایل‌های
                     پیکربندی موجود حفظ می‌شوند) + restart سرویس‌ها
  --full-update      هر دو: --system-upgrade و سپس --update
  --no-system-upgrade  در نصب تازه ارتقای سیستم را نپرس/انجام نده
  --repair           فقط خودترمیم: بسته‌های apt (tesseract و ...) + venv + restart
  --reconfigure      ویزارد .env را دوباره اجرا کن (مقادیر فعلی پیش‌فرض‌اند)
  --web-login        ساخت/بازنشانی ورود پنل وب برای مالک (بعد از اولین /start)
  --systemd          نصب سرویس‌های systemd بدون پرسش      --no-systemd   اجرا با nohup
  --start            فقط (دوباره) استارت ربات و وب
  --with-assistant / --no-assistant   دستیار هوشمند Ollama (پیش‌فرض: نصب نشود)
  --seed-admin       (قدیمی) — ساخت ورود وب برای مالکِ ADMIN_BALE_USER_ID
  --dry-run          هیچ چیز نصب/استارت نمی‌شود؛ بسته‌ها و فایل‌های systemd فقط چاپ می‌شوند
  -h, --help

متغیرهای محیطی برای نصب خودکار (بدون ترمینال):
  BALE_BOT_TOKEN  BOT_USERNAME  ADMIN_BALE_USER_ID  WEB_PORT(8000)  WEB_HOST(0.0.0.0)
  WEB_ADMIN_USERNAME(admin)  WEB_ADMIN_PASSWORD(خودکار)  CRITICAL_DAYS(3)
  INSTALL_SYSTEMD=y|n  NONINTERACTIVE=1  SKIP_GETME=1  SKIP_APT=1  INSTALL_DIR=/path
  SERVICE_USER=کاربر‌سرویس (پیش‌فرض: SUDO_USER یا کاربر فعلی)

مالک با اولین /start: اگر ADMIN_BALE_USER_ID خالی باشد، اولین کسی که در بله به ربات /start بزند مالک می‌شود.
HELP
}

for arg in "$@"; do
  case "$arg" in
    --seed-admin) DO_SEED=1 ;;
    --systemd) SYSTEMD_CHOICE=y ;;
    --no-systemd) SYSTEMD_CHOICE=n ;;
    --start) DO_START_ONLY=1 ;;
    --update) DO_UPDATE=1 ;;
    --system-upgrade) DO_SYSUP=1; DO_SYSUP_ONLY=1 ;;
    --full-update) DO_UPDATE=1; DO_SYSUP=1 ;;
    --no-system-upgrade) DO_SYSUP=2 ;;
    --repair) DO_REPAIR=1 ;;
    --reconfigure) RECONFIGURE=1 ;;
    --web-login) DO_WEB_LOGIN=1 ;;
    --dry-run|--print-units) DRY_RUN=1 ;;
    --with-assistant) DO_ASSISTANT=1 ;;
    --no-assistant) DO_ASSISTANT=2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "پرچم ناشناخته: $arg (bash install.sh --help)" >&2; exit 2 ;;
  esac
done

# ---------- output helpers (logs → stderr so $(...) captures stay clean) ----------
log()  { printf '%s\n' "$*" >&2; }
ok()   { printf '✓ %s\n' "$*" >&2; }
warn() { printf '⚠ %s\n' "$*" >&2; }
die()  { printf '\n✗ %s\n' "$*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1; }
trap 'rc=$?; if (( rc != 0 )); then printf "\n✗ نصب با خطا متوقف شد (کد %s). خروجی بالا را ببینید؛ اجرای دوباره امن است: bash install.sh\n" "$rc" >&2; fi' EXIT

# ---------- terminal detection: stdin, else /dev/tty ----------
TTY_FD=""
if [[ "${NONINTERACTIVE:-0}" != "1" ]]; then
  if [[ -t 0 ]]; then
    TTY_FD=0
  elif { exec 3</dev/tty; } 2>/dev/null; then
    TTY_FD=3
  fi
fi
interactive() { [[ -n "$TTY_FD" ]]; }

ask() {  # ask "prompt" "default" → stdout
  local prompt="$1" def="${2:-}" reply=""
  if [[ -n "$def" ]]; then printf '%s [%s]: ' "$prompt" "$def" >&2; else printf '%s: ' "$prompt" >&2; fi
  IFS= read -r -u "$TTY_FD" reply || true
  reply="$(printf '%s' "$reply" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  printf '%s' "${reply:-$def}"
}
ask_secret() {  # masked input (never echoed)
  local prompt="$1" reply=""
  printf '%s: ' "$prompt" >&2
  IFS= read -r -s -u "$TTY_FD" reply || true
  printf '\n' >&2
  printf '%s' "$reply" | tr -d '[:space:]'
}
yes_no() {  # yes_no Y|N "prompt" → 0 = yes
  local def="$1" ans; shift
  ans="$(ask "$* (y/n)" "$def")"
  case "$ans" in y|Y|yes|YES|بله|ب|آره) return 0 ;; *) return 1 ;; esac
}

no_tty_die() {
  cat >&2 <<MSG

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✗ نصب متوقف شد: ترمینال تعاملی پیدا نشد و توکن بازو هم داده نشده است.
  (نصب بدون توکن یک ربات خراب می‌سازد؛ برای همین ادامه نمی‌دهیم.)

یکی از این راه‌ها را در یک ترمینال SSH واقعی اجرا کنید:

  1) $ONE_LINER

  2) git clone $REPO_URL
     cd $REPO_NAME && bash install.sh

  3) نصب خودکار بدون پرسش:
     BALE_BOT_TOKEN='123:abc' INSTALL_SYSTEMD=y NONINTERACTIVE=1 bash install.sh
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MSG
  trap - EXIT
  exit 1
}

# ---------- privilege ----------
maybe_sudo() {
  if (( EUID == 0 )); then "$@"
  elif need_cmd sudo; then sudo "$@"
  else die "برای این کار root یا sudo لازم است: $*"
  fi
}
have_root() { (( EUID == 0 )) || { need_cmd sudo && sudo -n true 2>/dev/null; } || { need_cmd sudo && interactive; }; }
have_systemd() { [[ -d /run/systemd/system ]] && need_cmd systemctl; }

# ---------- system packages (self-healing) ----------
APT_PKGS=(python3 python3-venv python3-pip git curl ca-certificates iproute2 tesseract-ocr tesseract-ocr-fas tesseract-ocr-eng sqlite3 fontconfig)
missing_apt_pkgs() {
  local p pyv out=()
  pyv="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || true)"
  local pkgs=("${APT_PKGS[@]}")
  # Ubuntu/Debian: ensurepip lives in python3.X-venv
  if [[ -n "$pyv" ]] && apt-cache show "python${pyv}-venv" >/dev/null 2>&1; then pkgs+=("python${pyv}-venv"); fi
  for p in "${pkgs[@]}"; do
    dpkg -s "$p" >/dev/null 2>&1 || out+=("$p")
  done
  ((${#out[@]})) && printf '%s\n' "${out[@]}"
  return 0
}
ensure_system_packages() {
  if [[ "${SKIP_APT:-0}" == "1" ]]; then warn "SKIP_APT=1 — نصب بسته‌های سیستم رد شد."; return 0; fi
  if ! need_cmd apt-get || ! need_cmd dpkg; then
    warn "apt-get پیدا نشد؛ این بسته‌ها را دستی نصب کنید: ${APT_PKGS[*]}"
    return 0
  fi
  local missing=()
  mapfile -t missing < <(missing_apt_pkgs)
  if ((${#missing[@]} == 0)); then ok "بسته‌های سیستم کامل است (python3-venv، tesseract fas/eng، ...)"; return 0; fi
  if (( DRY_RUN )); then log "[dry-run] apt-get install -y ${missing[*]}"; return 0; fi
  log "نصب بسته‌های سیستم: ${missing[*]}"
  maybe_sudo env DEBIAN_FRONTEND=noninteractive apt-get update -y
  maybe_sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
  ok "بسته‌های سیستم نصب شد"
}
# ---------- system upgrade (apt-get update && apt-get upgrade -y) ----------
SYSUP_CMD_UPDATE=(env DEBIAN_FRONTEND=noninteractive apt-get update -y)
SYSUP_CMD_UPGRADE=(env DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)
system_upgrade() {
  if [[ "${SKIP_APT:-0}" == "1" ]]; then warn "SKIP_APT=1 — ارتقای سیستم رد شد."; return 0; fi
  if ! need_cmd apt-get; then warn "apt-get پیدا نشد — ارتقای سیستم رد شد."; return 0; fi
  if (( DRY_RUN )); then
    log "[dry-run] ${SYSUP_CMD_UPDATE[*]}"
    log "[dry-run] ${SYSUP_CMD_UPGRADE[*]}"
    return 0
  fi
  log "ارتقای سیستم‌عامل (apt-get update && apt-get upgrade -y) ..."
  maybe_sudo "${SYSUP_CMD_UPDATE[@]}"
  maybe_sudo "${SYSUP_CMD_UPGRADE[@]}"
  ok "سیستم‌عامل ارتقا یافت (فایل‌های پیکربندی موجود حفظ شد)"
  [[ -f /var/run/reboot-required ]] && warn "سیستم به ریبوت نیاز دارد (کرنل/کتابخانه‌ها) — در زمان مناسب: sudo reboot" || true
}
maybe_system_upgrade() {  # fresh install: offered in the wizard, default yes
  case "$DO_SYSUP" in
    1) system_upgrade ;;
    2) log "ارتقای سیستم رد شد (--no-system-upgrade)" ;;
    *) if ! interactive || yes_no Y "ارتقای سیستم‌عامل (apt-get update && apt-get upgrade -y) هم انجام شود؟"; then system_upgrade
       else log "ارتقای سیستم رد شد (بعداً: bash install.sh --system-upgrade)"; fi ;;
  esac
}
backup_db() {
  local root="$1" ts
  [[ -f "$root/data/bot.db" ]] || return 0
  ts="$(date +%Y%m%d_%H%M%S)"
  if (( DRY_RUN )); then log "[dry-run] backup data/bot.db → data/backups/bot_pre_update_$ts.db"; return 0; fi
  mkdir -p "$root/data/backups"
  python3 - "$root/data/bot.db" "$root/data/backups/bot_pre_update_$ts.db" <<'PY' && ok "پشتیبان پایگاه داده: data/backups/bot_pre_update_$ts.db"
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
PY
}

check_python() {
  need_cmd python3 || die "python3 نصب نیست."
  local minor; minor="$(python3 -c 'import sys;print(sys.version_info[1] if sys.version_info[0]==3 else 0)')"
  (( minor >= MIN_PY_MINOR )) || die "Python 3.${MIN_PY_MINOR}+ لازم است (فعلی: $(python3 -V))."
  ok "$(python3 -V)"
}
check_tesseract() {
  if ! need_cmd tesseract; then warn "tesseract نصب نیست — OCR عکس‌های تولید کار نمی‌کند (bash install.sh --repair)"; return 0; fi
  local langs; langs="$(tesseract --list-langs 2>/dev/null | tail -n +2 | tr '\n' ' ')"
  [[ " $langs " == *" fas "* ]] || warn "زبان fas برای tesseract نصب نیست (tesseract-ocr-fas)"
  ok "tesseract: $langs"
}

# ---------- repo ----------
is_repo_root() { [[ -f "$1/main.py" && -f "$1/requirements.txt" && -d "$1/scripts" ]]; }
resolve_workdir() {
  local script_dir dest; script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -n "${INSTALL_DIR:-}" ]] && is_repo_root "$INSTALL_DIR"; then (cd "$INSTALL_DIR" && pwd); return; fi
  if is_repo_root "$(dirname "$script_dir")"; then (cd "$(dirname "$script_dir")" && pwd); return; fi
  if is_repo_root "$PWD"; then echo "$PWD"; return; fi
  dest="${INSTALL_DIR:-$PWD/$REPO_NAME}"
  if ! is_repo_root "$dest"; then
    log "کلون ریپو به $dest ..."
    git clone "$REPO_URL" "$dest" >&2
  fi
  (cd "$dest" && pwd)
}

# ---------- venv ----------
VENV_PY=""
venv_healthy() { [[ -x "$1/.venv/bin/python" ]] && "$1/.venv/bin/python" -m pip --version >/dev/null 2>&1; }
setup_venv() {
  local root="$1"
  VENV_PY="$root/.venv/bin/python"
  if (( DRY_RUN )); then log "[dry-run] python3 -m venv .venv && pip install -r requirements.txt"; return 0; fi
  if [[ -d "$root/.venv" ]] && ! venv_healthy "$root"; then
    warn ".venv خراب است (مثلاً بعد از ارتقای python) — ساخت دوباره"
    rm -rf "$root/.venv"
  fi
  if [[ ! -d "$root/.venv" ]]; then
    log "ساخت محیط مجازی .venv ..."
    python3 -m venv "$root/.venv" || { rm -rf "$root/.venv"; die "ساخت venv ناموفق بود. بسته python3-venv را نصب کنید: sudo apt install python3-venv"; }
  fi
  "$VENV_PY" -m pip install -q -U pip wheel
  log "نصب وابستگی‌های Python (چند دقیقه) ..."
  "$VENV_PY" -m pip install -q -r "$root/requirements.txt"
  ok "وابستگی‌های Python نصب شد"
}
verify_imports() {
  local root="$1"
  (( DRY_RUN )) && return 0
  (cd "$root" && "$VENV_PY" - <<'PY') || die "import ماژول‌های برنامه ناموفق بود (خروجی بالا). bash install.sh --repair"
import importlib, sys
mods = ["config", "db.models", "bot", "web.app", "pytesseract", "PIL", "reportlab", "pandas", "openpyxl", "uvicorn", "fastapi"]
bad = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:  # noqa: BLE001
        bad.append(f"{m}: {e}")
if bad:
    print("\n".join(bad), file=sys.stderr); sys.exit(1)
PY
  ok "import ماژول‌های اصلی موفق (bot, web, db, OCR, PDF)"
}
init_db() {
  local root="$1"
  (( DRY_RUN )) && return 0
  mkdir -p "$root/data/backups" "$root/data/bot_assets" "$root/uploads" "$root/reports"
  (cd "$root" && "$VENV_PY" -c 'from db.models import Database; Database()' >/dev/null) \
    || die "ساخت/مهاجرت پایگاه داده ناموفق بود."
  ok "پایگاه داده آماده شد (migration انجام شد)"
}

# ---------- .env ----------
env_get() {  # env_get KEY → value from .env (unquoted)
  local f="$WORKDIR/.env" v
  [[ -f "$f" ]] || return 0
  v="$(grep -E "^$1=" "$f" | tail -1 | cut -d= -f2- | tr -d '\r' || true)"
  v="${v#\"}"; v="${v%\"}"; v="${v#\'}"; v="${v%\'}"
  printf '%s' "$v"
}
env_set() {  # env_set KEY VALUE (upsert, keeps other lines, chmod 600)
  local key="$1" value="$2" f="$WORKDIR/.env" tmp
  (( DRY_RUN )) && return 0
  [[ -f "$f" ]] || printf '# تولید شده توسط install.sh — این فایل را commit نکنید\n' > "$f"
  chmod 600 "$f"
  tmp="$(mktemp "$WORKDIR/.env.XXXXXX")"
  awk -v k="$key" -v v="$value" 'BEGIN{d=0} $0 ~ "^"k"=" { if(!d){print k"="v; d=1}; next } {print} END{ if(!d) print k"="v }' "$f" > "$tmp"
  chmod 600 "$tmp"; mv "$tmp" "$f"
}
env_unset() { [[ -f "$WORKDIR/.env" ]] && sed -i "/^$1=/d" "$WORKDIR/.env"; return 0; }
token_format_ok() { [[ "$1" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] && [[ "$1" != 123456789:* ]]; }
GETME_USERNAME=""
getme() {  # 0=ok 1=rejected by Bale 2=network error
  local resp
  GETME_USERNAME=""
  resp="$(curl -sS --max-time 15 "https://tapi.bale.ai/bot$1/getMe" 2>/dev/null)" || return 2
  if printf '%s' "$resp" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
    GETME_USERNAME="$(printf '%s' "$resp" | tr -d '\r\n' | sed -n 's/.*"username"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    return 0
  fi
  [[ -n "$resp" ]] && return 1
  return 2
}
env_configured() { token_format_ok "$(env_get BALE_BOT_TOKEN)"; }
port_in_use() { ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"; }
gen_secret() { python3 -c "import secrets;print(secrets.token_urlsafe(${1:-48}))"; }

ensure_common_env() {
  (( DRY_RUN )) && return 0
  local sk; sk="$(env_get WEB_SECRET_KEY)"
  [[ -n "$sk" && "$sk" != "change-me-in-production" ]] || env_set WEB_SECRET_KEY "$(gen_secret 48)"
  [[ -n "$(env_get WEB_HOST)" ]] || env_set WEB_HOST "${WEB_HOST:-0.0.0.0}"
  [[ -n "$(env_get WEB_PORT)" ]] || env_set WEB_PORT "${WEB_PORT:-8000}"
  [[ -n "$(env_get WEB_ADMIN_USERNAME)" ]] || env_set WEB_ADMIN_USERNAME "${WEB_ADMIN_USERNAME:-admin}"
  [[ -n "$(env_get WEB_ADMIN_PASSWORD)" ]] || env_set WEB_ADMIN_PASSWORD "${WEB_ADMIN_PASSWORD:-$(gen_secret 12)}"
  [[ -n "$(env_get CRITICAL_DAYS)" ]] || env_set CRITICAL_DAYS "${CRITICAL_DAYS:-3}"
  [[ -n "$(env_get ASSISTANT_ENABLED)" ]] || env_set ASSISTANT_ENABLED 0
  chmod 600 "$WORKDIR/.env"
}

env_from_vars() {  # non-interactive (automation)
  local token="${BALE_BOT_TOKEN:-}" rc=0
  token_format_ok "$token" || die "BALE_BOT_TOKEN نامعتبر است (باید شبیه 123456:ABC-def باشد)."
  if [[ "${SKIP_GETME:-0}" != "1" ]]; then
    getme "$token" || rc=$?
    case $rc in
      0) ok "توکن معتبر است (getMe: @${GETME_USERNAME})" ;;
      1) die "بله این توکن را رد کرد (getMe ناموفق). توکن را از @botfather دوباره بگیرید. (رد این بررسی: SKIP_GETME=1)" ;;
      *) warn "اتصال به tapi.bale.ai برقرار نشد — بررسی توکن رد شد؛ اینترنت/فایروال سرور را چک کنید." ;;
    esac
  fi
  env_set BALE_BOT_TOKEN "$token"
  env_set BOT_USERNAME "${BOT_USERNAME:-${GETME_USERNAME:-nasoz_bot}}"
  if [[ -n "${ADMIN_BALE_USER_ID:-}" ]]; then env_set ADMIN_BALE_USER_ID "$ADMIN_BALE_USER_ID"; fi
  if [[ -n "${WEB_PORT:-}" ]]; then env_set WEB_PORT "$WEB_PORT"; fi
  if [[ -n "${WEB_HOST:-}" ]]; then env_set WEB_HOST "$WEB_HOST"; fi
  if [[ -n "${WEB_ADMIN_USERNAME:-}" ]]; then env_set WEB_ADMIN_USERNAME "$WEB_ADMIN_USERNAME"; fi
  if [[ -n "${WEB_ADMIN_PASSWORD:-}" ]]; then env_set WEB_ADMIN_PASSWORD "$WEB_ADMIN_PASSWORD"; fi
  if [[ -n "${CRITICAL_DAYS:-}" ]]; then env_set CRITICAL_DAYS "$CRITICAL_DAYS"; fi
  ok ".env از متغیرهای محیطی نوشته شد (توکن …${token: -4})"
}

run_wizard() {
  local token cur bot_user admin_id port host wuser wpass crit rc def
  cat >&2 <<'WIZ'

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ویزارد پیکربندی بازوی مواد (بله) + پنل وب
(Enter = مقدار داخل [ ] پذیرفته می‌شود)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WIZ
  cur="$(env_get BALE_BOT_TOKEN)"
  while true; do
    if token_format_ok "$cur"; then
      token="$(ask_secret "۱) توکن بازو از @botfather (Enter = توکن فعلی …${cur: -4})")"
      token="${token:-$cur}"
    else
      token="$(ask_secret "۱) توکن بازو از @botfather در بله (مخفی تایپ می‌شود)")"
    fi
    if ! token_format_ok "$token"; then warn "فرمت توکن نامعتبر است (مثل 123456789:AbC-dEf…)."; continue; fi
    if [[ "${SKIP_GETME:-0}" == "1" ]]; then warn "SKIP_GETME=1 — بررسی getMe رد شد."; break; fi
    rc=0; getme "$token" || rc=$?
    if (( rc == 0 )); then ok "توکن معتبر است — ربات: @${GETME_USERNAME}"; break; fi
    if (( rc == 1 )); then warn "بله این توکن را رد کرد (getMe ناموفق)."; else warn "اتصال به tapi.bale.ai برقرار نشد (اینترنت/DNS/فایروال)."; fi
    yes_no N "با همین توکن ادامه دهیم؟" && break
  done
  env_set BALE_BOT_TOKEN "$token"

  while true; do
    bot_user="$(ask "۲) نام کاربری ربات (بدون @)" "${GETME_USERNAME:-$(env_get BOT_USERNAME)}")"
    bot_user="${bot_user#@}"
    [[ "$bot_user" =~ ^[A-Za-z0-9_]{3,}$ ]] && break
    warn "نام کاربری نامعتبر است."
  done
  env_set BOT_USERNAME "$bot_user"

  while true; do
    admin_id="$(ask "۳) شناسه عددی مالک در بله (اختیاری؛ خالی = اولین /start مالک می‌شود)" "$(env_get ADMIN_BALE_USER_ID)")"
    [[ -z "$admin_id" || "$admin_id" =~ ^[0-9]+$ ]] && break
    warn "فقط عدد."
  done
  if [[ -n "$admin_id" ]]; then env_set ADMIN_BALE_USER_ID "$admin_id"; else env_unset ADMIN_BALE_USER_ID; fi

  def="$(env_get WEB_PORT)"; def="${def:-${WEB_PORT:-8000}}"
  while true; do
    port="$(ask "۴) پورت پنل وب" "$def")"
    if ! [[ "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then warn "پورت نامعتبر"; continue; fi
    if port_in_use "$port" && [[ "$port" != "$(env_get WEB_PORT)" ]]; then
      yes_no N "پورت $port الان اشغال است. همین بماند؟" || continue
    fi
    break
  done
  env_set WEB_PORT "$port"
  def="$(env_get WEB_HOST)"
  host="$(ask "۵) آدرس گوش‌دادن وب (0.0.0.0 = از بیرون، 127.0.0.1 = فقط پشت nginx)" "${def:-0.0.0.0}")"
  env_set WEB_HOST "$host"
  def="$(env_get WEB_ADMIN_USERNAME)"
  wuser="$(ask "۶) نام کاربری ورود پنل وب" "${def:-admin}")"
  env_set WEB_ADMIN_USERNAME "$wuser"
  if [[ -n "$(env_get WEB_ADMIN_PASSWORD)" ]]; then def="رمز فعلی"; else def="ساخت خودکار"; fi
  wpass="$(ask_secret "۷) رمز ورود پنل وب (Enter = $def)")"
  if [[ -n "$wpass" ]]; then env_set WEB_ADMIN_PASSWORD "$wpass"; fi
  def="$(env_get CRITICAL_DAYS)"
  crit="$(ask "۸) آستانه روزهای بحرانی (CRITICAL_DAYS)" "${def:-3}")"
  [[ "$crit" =~ ^[0-9]+([.][0-9]+)?$ ]] || { warn "نامعتبر — ۳"; crit=3; }
  env_set CRITICAL_DAYS "$crit"

  if [[ -z "$SYSTEMD_CHOICE" ]] && have_systemd && have_root; then
    if yes_no Y "۹) سرویس دائمی systemd (روشن‌شدن خودکار بعد از ریبوت) نصب شود؟"; then SYSTEMD_CHOICE=y; else SYSTEMD_CHOICE=n; fi
  fi
  if (( DO_ASSISTANT == 0 )); then
    if yes_no N "۱۰) دستیار هوشمند محلی (Ollama، چند گیگ دانلود) هم نصب شود؟"; then DO_ASSISTANT=1; else DO_ASSISTANT=2; fi
  fi
  ok ".env نوشته شد (chmod 600؛ توکن …${token: -4})"
}

ensure_env() {
  if (( DRY_RUN )); then log "[dry-run] ویزارد .env رد شد"; return 0; fi
  if env_configured && (( ! RECONFIGURE )); then
    ok ".env از قبل پیکربندی شده است — حفظ شد"
    if interactive && [[ -z "${BALE_BOT_TOKEN:-}" ]] && yes_no N "می‌خواهید تنظیمات (.env) را دوباره وارد کنید؟"; then run_wizard; fi
  elif [[ -n "${BALE_BOT_TOKEN:-}" ]]; then
    env_from_vars
  elif interactive; then
    run_wizard
  else
    no_tty_die
  fi
  ensure_common_env
}

# ---------- service user / systemd ----------
service_user() {
  if [[ -n "${SERVICE_USER:-}" ]]; then echo "$SERVICE_USER"
  elif (( EUID == 0 )) && [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != root ]]; then echo "$SUDO_USER"
  else id -un; fi
}
render_unit() { sed -e "s|__WORKDIR__|${WORKDIR}|g" -e "s|__USER__|$(service_user)|g" "$1"; }
systemd_installed() { [[ -f "/etc/systemd/system/${BOT_SERVICE}.service" ]]; }
install_units() {
  local s tpl
  for s in "$BOT_SERVICE" "$WEB_SERVICE"; do
    tpl="$WORKDIR/scripts/$s.service.in"
    [[ -f "$tpl" ]] || die "قالب سرویس پیدا نشد: $tpl"
    if (( DRY_RUN )); then
      log "----- [dry-run] /etc/systemd/system/$s.service -----"
      render_unit "$tpl" >&2
    else
      render_unit "$tpl" | maybe_sudo tee "/etc/systemd/system/$s.service" >/dev/null
    fi
  done
  (( DRY_RUN )) && return 0
  maybe_sudo systemctl daemon-reload
  maybe_sudo systemctl enable "$BOT_SERVICE" "$WEB_SERVICE" >/dev/null 2>&1
  ok "سرویس‌های $BOT_SERVICE و $WEB_SERVICE نصب و برای بوت فعال شدند"
}

# ---------- nohup fallback ----------
pid_alive() { local p; p="$(cat "$1" 2>/dev/null || true)"; [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null; }
nohup_restart() {  # nohup_restart name script
  local name="$1" script="$2" pidf="$WORKDIR/data/$1.pid" p
  if pid_alive "$pidf"; then
    p="$(cat "$pidf")"
    if tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q "$script"; then
      kill "$p" 2>/dev/null || true
      for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$p" 2>/dev/null || break; sleep 0.5; done
    fi
  fi
  (
    cd "$WORKDIR" || exit 1
    nohup "$VENV_PY" "$script" >>"$WORKDIR/data/${name}_stdout.log" 2>&1 </dev/null &
    echo $! > "$pidf"
  )
}
start_services() {
  if (( DRY_RUN )); then log "[dry-run] استارت سرویس‌ها رد شد"; return 0; fi
  if [[ "$SYSTEMD_CHOICE" == y ]] || systemd_installed; then
    if have_systemd; then
      maybe_sudo systemctl restart "$BOT_SERVICE" "$WEB_SERVICE"
      ok "سرویس‌ها (re)start شدند"
      return 0
    fi
    warn "systemd روی این سیستم فعال نیست — اجرا با nohup"
  fi
  nohup_restart bot main.py
  nohup_restart web web_main.py
  ok "ربات و وب با nohup اجرا شدند (pid در data/bot.pid و data/web.pid)"
}

# ---------- web login ----------
WEB_LOGIN_READY=0
seed_web_login() {  # $1=1 → quiet if no owner yet
  local quiet="${1:-0}" admin rc=0
  (( DRY_RUN )) && return 0
  admin="$(env_get ADMIN_BALE_USER_ID)"
  if [[ -n "$admin" ]]; then
    (cd "$WORKDIR" && "$VENV_PY" -c "from db.models import Database; Database().bootstrap_admin('$admin')" >/dev/null 2>&1) || true
  fi
  (cd "$WORKDIR" && "$VENV_PY" scripts/seed_web_admin.py --username "$(env_get WEB_ADMIN_USERNAME)" \
      --password "$(env_get WEB_ADMIN_PASSWORD)" --force >/dev/null 2>&1) || rc=$?
  if (( rc == 0 )); then ok "ورود پنل وب برای مالک فعال شد"; WEB_LOGIN_READY=1; return 0; fi
  (( quiet )) || warn "هنوز مالکی در ربات ثبت نشده — ابتدا در بله به ربات /start بزنید، سپس: bash install.sh --web-login"
  return 0
}

# ---------- health ----------
HEALTH_OK=1
health_check() {
  (( DRY_RUN )) && return 0
  local token port code="" rc=0
  token="$(env_get BALE_BOT_TOKEN)"; port="$(env_get WEB_PORT)"; port="${port:-8000}"
  log ""; log "━━━━━━━━ تست سلامت ━━━━━━━━"
  if [[ "${SKIP_GETME:-0}" == "1" ]]; then warn "getMe: رد شد (SKIP_GETME=1)"
  else
    getme "$token" || rc=$?
    if (( rc == 0 )); then ok "getMe: @${GETME_USERNAME}"; else warn "getMe ناموفق (توکن یا اینترنت)"; HEALTH_OK=0; fi
  fi
  sleep 5
  if systemd_installed && have_systemd; then
    if systemctl is-active --quiet "$BOT_SERVICE"; then ok "سرویس ربات فعال است"; else warn "سرویس ربات فعال نیست: journalctl -u $BOT_SERVICE -n 50"; HEALTH_OK=0; fi
  elif pid_alive "$WORKDIR/data/bot.pid"; then ok "فرآیند ربات زنده است (pid $(cat "$WORKDIR/data/bot.pid"))"
  else warn "فرآیند ربات زنده نیست: tail -50 $WORKDIR/data/bot.log $WORKDIR/data/bot_stdout.log"; HEALTH_OK=0
  fi
  if ! { systemd_installed && have_systemd; } && ! pid_alive "$WORKDIR/data/web.pid"; then
    warn "فرآیند وب زنده نیست (پورت $port اشغال است؟): tail -30 $WORKDIR/data/web_stdout.log"; HEALTH_OK=0
  fi
  for _ in $(seq 1 25); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${port}/login" || true)"
    [[ "$code" == 200 ]] && break
    sleep 1
  done
  if [[ "$code" == 200 ]]; then ok "پنل وب: /login → 200 روی پورت $port"; else warn "پنل وب روی پورت $port پاسخ نداد (کد: ${code:--}) — لاگ: data/web.log"; HEALTH_OK=0; fi
}

print_summary() {
  local port host user ip svc owner_line login_line status_line
  port="$(env_get WEB_PORT)"; host="$(env_get WEB_HOST)"; user="$(env_get WEB_ADMIN_USERNAME)"
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  if systemd_installed && have_systemd; then
    svc="  وضعیت:      sudo systemctl status $BOT_SERVICE $WEB_SERVICE
  ری‌استارت:   sudo systemctl restart $BOT_SERVICE $WEB_SERVICE
  توقف:       sudo systemctl stop $BOT_SERVICE $WEB_SERVICE
  لاگ زنده:   journalctl -u $BOT_SERVICE -f    |    journalctl -u $WEB_SERVICE -f
  لاگ برنامه: tail -f $WORKDIR/data/bot.log"
  else
    svc="  (بدون systemd — با nohup؛ بعد از ریبوت دوباره: bash install.sh --start)
  توقف:  kill \$(cat $WORKDIR/data/bot.pid) \$(cat $WORKDIR/data/web.pid)
  لاگ:   tail -f $WORKDIR/data/bot.log $WORKDIR/data/web.log"
  fi
  if [[ -n "$(env_get ADMIN_BALE_USER_ID)" ]]; then owner_line="شناسه $(env_get ADMIN_BALE_USER_ID) مالک است."; else owner_line="اولین کسی که /start بزند مالک می‌شود."; fi
  if (( WEB_LOGIN_READY )); then login_line="  ✓ ورود وب فعال است."; else login_line="  ورود وب بعد از اولین /start فعال می‌شود:  cd $WORKDIR && bash install.sh --web-login"; fi
  if (( HEALTH_OK )); then status_line="✅ نصب کامل شد؛ ربات و پنل وب در حال اجرا هستند."; else status_line="⚠ نصب انجام شد ولی تست سلامت کامل نبود (هشدارهای بالا را ببینید)."; fi
  cat >&2 <<SUM

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
$status_line
مسیر: $WORKDIR

ربات بله: @$(env_get BOT_USERNAME)
  در بله به ربات /start بزنید — $owner_line

پنل وب:   http://${ip:-IP-سرور}:${port}/login   (گوش‌دادن روی ${host}:${port})
  نام کاربری: ${user}
  رمز:       grep WEB_ADMIN_PASSWORD $WORKDIR/.env
$login_line
  فایروال: اگر ufw فعال است: sudo ufw allow ${port}/tcp

سرویس‌ها:
$svc

به‌روزرسانی:  cd $WORKDIR && bash install.sh --update
خودترمیم:    cd $WORKDIR && bash install.sh --repair
تنظیم دوباره: cd $WORKDIR && bash install.sh --reconfigure

هشدار: فقط یک نمونه از ربات با یک توکن باید اجرا شود؛ نسخه‌های دیگر را خاموش کنید.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUM
}

# --- دستیار هوشمند (Ollama محلی) ---
DEFAULT_OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:3b}"
DEFAULT_OLLAMA_URL="http://127.0.0.1:11434"
DEFAULT_OLLAMA_TIMEOUT="60"

upsert_env_var() {
  # usage: upsert_env_var ROOT KEY VALUE
  local root="$1" key="$2" value="$3" tmp
  cd "$root"
  if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
      cp .env.example .env
    else
      touch .env
    fi
    chmod 600 .env 2>/dev/null || true
  fi
  tmp="$(mktemp)"
  if grep -qE "^${key}=" .env 2>/dev/null; then
    # replace existing assignment (keep other lines)
    awk -v k="$key" -v v="$value" '
      BEGIN { done=0 }
      $0 ~ "^"k"=" {
        if (!done) { print k"="v; done=1; next }
      }
      { print }
      END { if (!done) print k"="v }
    ' .env > "$tmp"
  else
    cat .env > "$tmp"
    printf '\n%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  mv "$tmp" .env
  chmod 600 .env 2>/dev/null || true
}

write_ollama_env() {
  local root="$1"
  local model="${2:-$DEFAULT_OLLAMA_MODEL}"
  upsert_env_var "$root" "OLLAMA_BASE_URL" "$DEFAULT_OLLAMA_URL"
  upsert_env_var "$root" "OLLAMA_MODEL" "$model"
  upsert_env_var "$root" "OLLAMA_TIMEOUT" "$DEFAULT_OLLAMA_TIMEOUT"
  log "✓ متغیرهای OLLAMA_* در .env نوشته شد (مدل: $model)"
}

install_ollama_binary() {
  if need_cmd ollama; then
    log "✓ Ollama از قبل نصب است: $(command -v ollama)"
    return 0
  fi
  log "نصب Ollama ..."
  if ! need_cmd curl; then
    if need_cmd apt-get; then
      maybe_sudo apt-get update -y
      maybe_sudo apt-get install -y curl
    else
      die "curl برای نصب Ollama لازم است."
    fi
  fi
  curl -fsSL https://ollama.com/install.sh | sh
  need_cmd ollama || die "نصب Ollama ناموفق بود (دستور ollama پیدا نشد)."
  log "✓ Ollama نصب شد"
}

ensure_ollama_running() {
  # Best-effort: if API answers, ok; else try serve in background
  if curl -fsS --max-time 3 "$DEFAULT_OLLAMA_URL/api/tags" >/dev/null 2>&1; then
    log "✓ سرویس Ollama پاسخ می‌دهد ($DEFAULT_OLLAMA_URL)"
    return 0
  fi
  if need_cmd systemctl && systemctl is-active --quiet ollama 2>/dev/null; then
    sleep 2
    if curl -fsS --max-time 3 "$DEFAULT_OLLAMA_URL/api/tags" >/dev/null 2>&1; then
      log "✓ سرویس systemd ollama فعال است"
      return 0
    fi
  fi
  warn "Ollama روی $DEFAULT_OLLAMA_URL پاسخ نداد — تلاش برای ollama serve در پس‌زمینه..."
  nohup ollama serve >/tmp/ollama-serve-install.log 2>&1 &
  sleep 3
  if curl -fsS --max-time 5 "$DEFAULT_OLLAMA_URL/api/tags" >/dev/null 2>&1; then
    log "✓ ollama serve شروع شد"
    return 0
  fi
  warn "سرویس Ollama هنوز آماده نیست؛ بعداً «ollama serve» یا systemctl را بررسی کنید."
  return 1
}

pull_ollama_model() {
  local model="${1:-$DEFAULT_OLLAMA_MODEL}"
  log "کشیدن مدل Ollama: $model ..."
  if ollama pull "$model"; then
    log "✓ مدل $model آماده است"
    ollama list || true
    return 0
  fi
  warn "کشیدن مدل $model ناموفق بود — دستی: ollama pull $model"
  return 1
}

setup_assistant() {
  local root="$1"
  local model="${OLLAMA_MODEL:-$DEFAULT_OLLAMA_MODEL}"
  log "━━━━━━━━ نصب دستیار هوشمند (Ollama محلی) ━━━━━━━━"
  install_ollama_binary
  ensure_ollama_running || true
  pull_ollama_model "$model" || true
  write_ollama_env "$root" "$model"
  log "راهنما: docs/راهنمای_نصب_دستیار_هوشمند.pdf"
}

# Ollama helpers write through env_set (keeps chmod 600)
upsert_env_var() { env_set "$2" "$3"; }

maybe_assistant() {
  if (( DO_ASSISTANT == 1 )) && (( ! DRY_RUN )); then setup_assistant "$WORKDIR"
  else log "دستیار هوشمند نصب نشد (بعداً: bash install.sh --with-assistant)"; fi
}

# ========== main ==========
log "━━━━━━━━ نصب بازوی مواد (بله) ━━━━━━━━"
if interactive; then
  if [[ $TTY_FD == 0 ]]; then log "ترمینال: stdin"; else log "ترمینال: /dev/tty"; fi
else
  log "حالت غیرتعاملی"
fi

need_cmd git || ensure_system_packages
WORKDIR="$(resolve_workdir)"
export WORKDIR
cd "$WORKDIR"
log "مسیر نصب: $WORKDIR"
VENV_PY="$WORKDIR/.venv/bin/python"

# fail fast (before minutes of apt/pip) when a fresh install cannot get a token
if (( ! DRY_RUN && ! DO_UPDATE && ! DO_REPAIR && ! DO_WEB_LOGIN && ! DO_START_ONLY )) \
   && ! env_configured && [[ -z "${BALE_BOT_TOKEN:-}" ]] && ! interactive; then
  no_tty_die
fi

if (( DO_WEB_LOGIN )); then
  seed_web_login 0
  if (( WEB_LOGIN_READY )); then log "نام کاربری: $(env_get WEB_ADMIN_USERNAME) — رمز: grep WEB_ADMIN_PASSWORD $WORKDIR/.env"; fi
  trap - EXIT; exit 0
fi
if (( DO_SYSUP_ONLY && ! DO_UPDATE )); then
  system_upgrade
  if (( ! DRY_RUN )) && env_configured; then start_services; health_check; fi
  if (( DRY_RUN )); then log "[dry-run] تمام شد — هیچ چیزی نصب یا اجرا نشد."; fi
  trap - EXIT; exit 0
fi
if (( DO_START_ONLY )); then
  env_configured || die ".env هنوز توکن ندارد — bash install.sh"
  start_services; health_check; print_summary; trap - EXIT; exit 0
fi

if (( DO_UPDATE && DO_SYSUP == 1 )) && [[ -z "${_NASOZ_REEXEC:-}" ]]; then system_upgrade; fi   # --full-update
if (( DO_UPDATE )) && [[ -z "${_NASOZ_REEXEC:-}" ]]; then backup_db "$WORKDIR"; fi
if (( DO_UPDATE )) && (( DRY_RUN )); then log "[dry-run] git -C $WORKDIR pull --ff-only"; fi
if (( DO_UPDATE )) && [[ -d "$WORKDIR/.git" ]] && (( ! DRY_RUN )); then
  log "git pull ..."
  git -C "$WORKDIR" pull --ff-only || warn "git pull ناموفق بود (تغییرات محلی؟) — با نسخهٔ فعلی ادامه می‌دهیم. git -C $WORKDIR status"
  # the script on disk may have changed; re-exec the new version once
  if [[ -z "${_NASOZ_REEXEC:-}" ]]; then export _NASOZ_REEXEC=1; trap - EXIT; exec bash "$WORKDIR/scripts/install.sh" "$@"; fi
fi

if (( ! DO_UPDATE && ! DO_REPAIR )); then maybe_system_upgrade; fi   # fresh install (wizard question, default yes)
ensure_system_packages
check_python
check_tesseract
if (( ! DO_UPDATE && ! DO_REPAIR )); then ensure_env; fi   # ask questions early, then long installs
setup_venv "$WORKDIR"
verify_imports "$WORKDIR"
init_db "$WORKDIR"

if (( DO_UPDATE || DO_REPAIR )); then
  if (( ! DRY_RUN )); then
    env_configured || die ".env توکن ندارد — ابتدا: bash install.sh"
    ensure_common_env
  fi
  if { systemd_installed && have_systemd; } || (( DRY_RUN )); then install_units; fi   # refresh units
else
  maybe_assistant
  if [[ -z "$SYSTEMD_CHOICE" ]]; then
    if have_systemd && (( EUID == 0 )); then SYSTEMD_CHOICE=y; else SYSTEMD_CHOICE=n; fi
  fi
  if (( DRY_RUN )); then install_units
  elif [[ "$SYSTEMD_CHOICE" == y ]]; then
    if have_systemd; then install_units; else warn "systemd در دسترس نیست — اجرا با nohup"; SYSTEMD_CHOICE=n; fi
  fi
fi

start_services
if (( ! DO_UPDATE && ! DO_REPAIR )) || (( DO_SEED )); then seed_web_login 1; fi
health_check
if (( DRY_RUN )); then log "[dry-run] تمام شد — هیچ چیزی نصب یا اجرا نشد."; trap - EXIT; exit 0; fi
print_summary
trap - EXIT
exit 0
