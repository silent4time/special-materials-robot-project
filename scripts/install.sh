#!/usr/bin/env bash
# نصب و راه اندازی کامل بازوی مواد (بله) + پنل وب روی سرور لینوکس (Ubuntu 22.04/24.04، Debian 12)
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
RESTORE_FILE=""
OFFLINE_SRC="${_NASOZ_OFFLINE_DIR:-}"     # --offline DIR|bundle.tar.gz (no GitHub/PyPI)

usage() {
  cat <<'HELP'
Usage: bash install.sh [options]

  (بدون پرچم)        در ترمینال تعاملی: منوی اصلی فارسی (نصب، به روزرسانی، وضعیت، بک آپ، بازیابی،
                     تعمیر، سرویس ها، لاگ ها، تنظیمات). بدون ترمینال: نصب کامل مثل --install
  --install          نصب کامل بدون منو: بسته های سیستم + venv + ویزارد .env + سرویس ها + تست سلامت
  --menu             نمایش منو حتی با پرچم های دیگر (مثلا --menu --offline DIR یا --menu --dry-run)
  --update           به روزرسانی برنامه: پشتیبان DB + git pull + pip install -r requirements + migrate
                     + restart سرویس ها + تست سلامت (.env و data حفظ می شود)
  --system-upgrade   ارتقای سیستم عامل: apt-get update && apt-get upgrade -y (غیرتعاملی، فایل های
                     پیکربندی موجود حفظ می شوند) + restart سرویس ها
  --full-update      هر دو: --system-upgrade و سپس --update
  --no-system-upgrade  در نصب تازه ارتقای سیستم را نپرس/انجام نده
  --offline DIR|FILE.tar.gz  نصب/به روزرسانی بدون GitHub/PyPI از بسته آفلاین (wheels/ و کد)؛
                     با --update کد جایگزین می شود (.env و data/ می ماند). ساخت بسته: bash scripts/build_offline_bundle.sh
  --restore FILE.zip بازگردانی بک آپ (فقط سرور): توقف سرویس ها ← بک آپ خودکار «پیش از بازگردانی» ←
                     بررسی manifest/sha256 ← بازگردانی DB + uploads + bot_assets ← migrate ← استارت ← تست سلامت
  --repair           فقط خودترمیم: بسته های apt (tesseract و ...) + venv + restart
  --reconfigure      ویزارد .env را دوباره اجرا کن (مقادیر فعلی پیش فرض اند)
  --web-login        ساخت/بازنشانی ورود پنل وب برای مالک (بعد از اولین /start)
  --systemd          نصب سرویس های systemd بدون پرسش      --no-systemd   اجرا با nohup
  --start            فقط (دوباره) استارت ربات و وب
  --with-assistant / --no-assistant   دستیار هوشمند صوتی (Ollama از GitHub + مدل از hf-mirror/HF + Whisper/Piper
                     از PyPI)؛ روی سرور نصب شده فقط دستیار را نصب می کند. پیش فرض: نصب نشود
  --assistant-offline FILE.tar|DIR  نصب دستیار از بسته آفلاین (ساخت: bash scripts/build_assistant_bundle.sh)
  --model NAME       مدل زبانی دستیار (پیش فرض qwen2.5:3b؛ با RAM کمتر از ۶ گیگ: qwen2.5:1.5b؛ همچنین qwen2.5:0.5b)
  --whisper SIZE     مدل گفتار به متن (پیش فرض small؛ مثلا base یا tiny برای سرور ضعیف)
  --no-piper / --no-whisper  بدون Piper (متن به گفتار) / بدون Whisper
                     این چهار گزینه همراه --with-assistant یا --assistant-offline به کار می روند
  --seed-admin       (قدیمی) — ساخت ورود وب برای مالک ADMIN_BALE_USER_ID
  --dry-run          هیچ چیز نصب/استارت نمی شود؛ بسته ها و فایل های systemd فقط چاپ می شوند
  -h, --help

متغیرهای محیطی برای نصب خودکار (بدون ترمینال):
  BALE_BOT_TOKEN  BOT_USERNAME  ADMIN_BALE_USER_ID  WEB_PORT(8000)  WEB_HOST(0.0.0.0)
  WEB_ADMIN_USERNAME(admin)  WEB_ADMIN_PASSWORD(خودکار)  CRITICAL_DAYS(3)
  INSTALL_SYSTEMD=y|n  NONINTERACTIVE=1  SKIP_GETME=1  SKIP_APT=1  INSTALL_DIR=/path
  SERVICE_USER=کاربر سرویس (پیش فرض: SUDO_USER یا کاربر فعلی)

مالک با اولین /start: اگر ADMIN_BALE_USER_ID خالی باشد، اولین کسی که در بله به ربات /start بزند مالک می شود.
HELP
}

_want_restore=0; _want_offline=0; _want_abundle=0; ASSISTANT_BUNDLE_ARG=""; WANT_MENU=0; NASOZ_ARGC=$#; EXPLICIT_INSTALL=0
_want_aopt=""; ASSISTANT_ARGS=()   # --model / --whisper / --no-piper / --no-whisper → scripts/install_assistant.sh
for arg in "$@"; do
  if [[ -n "$_want_aopt" ]]; then
    [[ -n "$arg" && "$arg" != --* ]] || { echo "مقدار $_want_aopt را بدهید (مثلا $_want_aopt qwen2.5:1.5b)" >&2; exit 2; }
    ASSISTANT_ARGS+=("$_want_aopt" "$arg"); _want_aopt=""; continue
  fi
  if (( _want_abundle )); then ASSISTANT_BUNDLE_ARG="$arg"; _want_abundle=0; continue; fi
  if (( _want_restore )); then RESTORE_FILE="$arg"; _want_restore=0; continue; fi
  if (( _want_offline )); then OFFLINE_SRC="$arg"; _want_offline=0; continue; fi
  case "$arg" in
    --restore) _want_restore=1 ;;
    --restore=*) RESTORE_FILE="${arg#--restore=}" ;;
    --offline) _want_offline=1 ;;
    --offline=*) OFFLINE_SRC="${arg#--offline=}" ;;
    --install) EXPLICIT_INSTALL=1 ;;   # explicit fresh install (skips the menu)
    --menu) WANT_MENU=1 ;;
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
    --assistant-offline) DO_ASSISTANT=1; _want_abundle=1 ;;
    --assistant-offline=*) DO_ASSISTANT=1; ASSISTANT_BUNDLE_ARG="${arg#--assistant-offline=}" ;;
    --no-assistant) DO_ASSISTANT=2 ;;
    --model|--whisper) _want_aopt="$arg" ;;
    --model=*|--whisper=*) [[ -n "${arg#*=}" ]] || { echo "مقدار ${arg%%=*} خالی است" >&2; exit 2; }; ASSISTANT_ARGS+=("${arg%%=*}" "${arg#*=}") ;;
    --no-piper|--no-whisper) ASSISTANT_ARGS+=("$arg") ;;
    -h|--help) usage; exit 0 ;;
    *) echo "پرچم ناشناخته: $arg (bash install.sh --help)" >&2; exit 2 ;;
  esac
done

if [[ -n "$_want_aopt" ]]; then echo "مقدار $_want_aopt را بدهید (مثلا $_want_aopt qwen2.5:1.5b)" >&2; exit 2; fi
if (( _want_abundle )); then echo "مسیر بسته دستیار را بدهید: --assistant-offline nasoz_assistant_….tar" >&2; exit 2; fi
if [[ -n "$ASSISTANT_BUNDLE_ARG" ]]; then
  [[ -e "$ASSISTANT_BUNDLE_ARG" ]] || { echo "بسته دستیار پیدا نشد: $ASSISTANT_BUNDLE_ARG" >&2; exit 2; }
  ASSISTANT_BUNDLE_ARG="$(cd "$(dirname "$ASSISTANT_BUNDLE_ARG")" && pwd)/$(basename "$ASSISTANT_BUNDLE_ARG")"
fi

# ---------- output helpers (logs → stderr so $(...) captures stay clean) ----------
log()  { printf '%s\n' "$*" >&2; }
ok()   { printf '✓ %s\n' "$*" >&2; }
warn() { printf '⚠ %s\n' "$*" >&2; }
die()  { printf '\n✗ %s\n' "$*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1; }
# ---------- progress bar + install log (no tput needed; NO_COLOR / non-TTY degrade) ----------
STEP_N="${_NASOZ_STEP_N:-0}"; STEP_TOTAL=0; STEP_NAME=""
INSTALL_LOG="${_NASOZ_INSTALL_LOG:-${TMPDIR:-/tmp}/nasoz_install_$(date +%Y%m%d_%H%M%S).log}"
: >>"$INSTALL_LOG" 2>/dev/null || INSTALL_LOG="/dev/null"
if [[ -t 2 ]]; then PROGRESS_TTY=1; else PROGRESS_TTY=0; fi
if (( PROGRESS_TTY )) && [[ -z "${NO_COLOR:-}" && "${TERM:-dumb}" != dumb ]]; then
  C_B=$'\033[1m'; C_G=$'\033[32m'; C_R=$'\033[31m'; C_0=$'\033[0m'
else
  C_B=""; C_G=""; C_R=""; C_0=""
fi
# ---------- logo (plain ASCII + box drawing; ≤ 80 columns; color only if tput colors ≥ 8) ----------
logo_colors() {
  [[ -z "${NO_COLOR:-}" && -t 2 && "${TERM:-dumb}" != dumb ]] || return 1
  local n; n="$(tput colors 2>/dev/null || echo 0)"
  [[ "$n" =~ ^[0-9]+$ ]] && (( n >= 8 ))
}
app_version() {  # app_version DIR → "commit/tag (date)" from VERSION, git or the offline bundle stamp
  local d="${1:-}" v="" dt=""
  [[ -n "$d" && -d "$d" ]] || { printf 'نامشخص'; return 0; }
  if [[ -f "$d/VERSION" ]]; then v="$(head -n1 "$d/VERSION" | tr -d '\r')"; fi
  if [[ -d "$d/.git" ]] && need_cmd git; then
    [[ -n "$v" ]] || v="$(git -C "$d" describe --tags --always 2>/dev/null || true)"
    dt="$(git -C "$d" log -1 --format=%cd --date=format:'%Y-%m-%d %H:%M' 2>/dev/null || true)"
  fi
  if [[ -z "$v" && -f "$d/.offline_commit" ]]; then
    v="$(head -n1 "$d/.offline_commit") (بسته آفلاین)"; dt="$(date -r "$d/.offline_commit" '+%Y-%m-%d' 2>/dev/null || true)"
  fi
  printf '%s%s' "${v:-نامشخص}" "${dt:+ ، $dt}"
}
print_logo() {  # print_logo [version-dir]
  local f="" b="" s="" z="" ver
  if logo_colors; then f=$'\033[1;33m'; b=$'\033[1;31m'; s=$'\033[36m'; z=$'\033[0m'; fi
  ver="$(app_version "${1:-}")"
  cat >&2 <<LOGO

${f}     (  )  (    ${b} ███╗   ██╗ █████╗ ███████╗ ██████╗ ███████╗${z}
${f}    ) (  )  )   ${b} ████╗  ██║██╔══██╗██╔════╝██╔═══██╗╚══███╔╝${z}
${f}   (  ) (  (    ${b} ██╔██╗ ██║███████║███████╗██║   ██║  ███╔╝ ${z}
${s}  \\~~~~~~~~~~/  ${b} ██║╚██╗██║██╔══██║╚════██║██║   ██║ ███╔╝  ${z}
${s}   \\________/   ${b} ██║ ╚████║██║  ██║███████║╚██████╔╝███████╗${z}
${s}      |  |      ${b} ╚═╝  ╚═══╝╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚══════╝${z}
${s}  ════════════════════════════════════════════════════════════${z}
   ربات مدیریت مواد نسوز تاندیش   |   نسخه: ${ver}
LOGO
}
fa_digits() { printf '%s' "$1" | LC_ALL=C.UTF-8 sed 'y/0123456789/۰۱۲۳۴۵۶۷۸۹/'; }
step() {  # step "نام مرحله" → «[۳/۱۰] ۳۰٪ ███░░░░░░░ نام...»
  STEP_N=$((STEP_N + 1)); STEP_NAME="$1"
  local total=$(( STEP_TOTAL > 0 ? STEP_TOTAL : STEP_N )) pct filled bar="" i
  (( STEP_N > total )) && total=$STEP_N
  pct=$(( STEP_N * 100 / total )); filled=$(( pct / 10 ))
  for (( i = 0; i < 10; i++ )); do if (( i < filled )); then bar+="█"; else bar+="░"; fi; done
  printf '\n%s[%s/%s] %s٪ %s %s...%s\n' "$C_B" "$(fa_digits "$STEP_N")" "$(fa_digits "$total")" \
    "$(fa_digits "$pct")" "$bar" "$1" "$C_0" >&2
  printf '\n===== [%s/%s] %s (%s) =====\n' "$STEP_N" "$total" "$1" "$(date '+%F %T')" >>"$INSTALL_LOG" 2>/dev/null || true
}
run_long() {  # run_long "توضیح" cmd… → output only to $INSTALL_LOG; spinner+elapsed on TTY
  local label="$1"; shift
  local start=$SECONDS rc=0 pid sp="|/-\\" i=0
  printf '$ %s\n' "$*" >>"$INSTALL_LOG" 2>/dev/null || true
  if (( PROGRESS_TTY )); then
    "$@" >>"$INSTALL_LOG" 2>&1 </dev/null & pid=$!
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r  %s %s (%s ثانیه)   ' "${sp:i++%4:1}" "$label" "$(fa_digits $((SECONDS - start)))" >&2
      sleep 0.5
    done
    wait "$pid" || rc=$?
    printf '\r\033[K' >&2
  else
    log "  … $label (جزئیات: $INSTALL_LOG)"
    "$@" >>"$INSTALL_LOG" 2>&1 </dev/null || rc=$?
  fi
  if (( rc != 0 )); then
    printf '%s  ✗ %s ناموفق بود (کد %s). آخرین خطوط لاگ:%s\n' "$C_R" "$label" "$(fa_digits "$rc")" "$C_0" >&2
    tail -n 15 "$INSTALL_LOG" 2>/dev/null | sed 's/^/    │ /' >&2
    return "$rc"
  fi
  printf '%s  ✓ %s (%s ثانیه)%s\n' "$C_G" "$label" "$(fa_digits $((SECONDS - start)))" "$C_0" >&2
}
move_log_to() {  # keep one log per run under <repo>/data/logs/
  local dir="$1/data/logs" dest
  mkdir -p "$dir" 2>/dev/null || return 0
  dest="$dir/$(basename "$INSTALL_LOG")"
  if [[ "$INSTALL_LOG" != /dev/null && "$INSTALL_LOG" != "$dest" ]]; then
    cat "$INSTALL_LOG" >>"$dest" 2>/dev/null && rm -f "$INSTALL_LOG"
    INSTALL_LOG="$dest"
  fi
}
# shellcheck disable=SC2317  # invoked via trap
on_exit() {
  local rc=$?
  if (( rc != 0 )); then
    printf '\n%s✗ نصب با خطا متوقف شد (کد %s)%s\n' "$C_R" "$(fa_digits "$rc")" "$C_0" >&2
    [[ -n "$STEP_NAME" ]] && printf '  مرحله ناموفق: [%s/%s] %s\n' "$(fa_digits "$STEP_N")" "$(fa_digits "$STEP_TOTAL")" "$STEP_NAME" >&2
    printf '  لاگ کامل: %s\n  اجرای دوباره امن است: bash install.sh\n' "$INSTALL_LOG" >&2
  fi
}
trap on_exit EXIT

# ---------- terminal detection: stdin, else /dev/tty ----------
TTY_FD=""
if [[ "${NONINTERACTIVE:-0}" != "1" ]]; then
  if [[ -t 0 || "${NASOZ_TTY_STDIN:-0}" == 1 ]]; then   # NASOZ_TTY_STDIN=1: scripted tests feed stdin
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
  (نصب بدون توکن یک ربات خراب می سازد؛ برای همین ادامه نمی دهیم.)

یکی از این راه ها را در یک ترمینال SSH واقعی اجرا کنید:

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
    [[ -n "$OFFLINE_DIR" && "$p" == git ]] && continue
    dpkg -s "$p" >/dev/null 2>&1 || out+=("$p")
  done
  ((${#out[@]})) && printf '%s\n' "${out[@]}"
  return 0
}
ensure_system_packages() {
  if [[ "${SKIP_APT:-0}" == "1" ]]; then warn "SKIP_APT=1 — نصب بسته های سیستم رد شد."; return 0; fi
  if ! need_cmd apt-get || ! need_cmd dpkg; then
    warn "apt-get پیدا نشد؛ این بسته ها را دستی نصب کنید: ${APT_PKGS[*]}"
    return 0
  fi
  local missing=()
  mapfile -t missing < <(missing_apt_pkgs)
  if ((${#missing[@]} == 0)); then ok "بسته های سیستم کامل است (python3-venv، tesseract fas/eng، ...)"; return 0; fi
  if (( DRY_RUN )); then log "[dry-run] apt-get install -y ${missing[*]}"; return 0; fi
  log "نصب بسته های سیستم: ${missing[*]}"
  if [[ -n "$OFFLINE_DIR" ]]; then
    if run_long "apt-get update (حداکثر ۹۰ ثانیه)" maybe_sudo env DEBIAN_FRONTEND=noninteractive timeout 90 apt-get update -y -o Acquire::http::Timeout=20 \
       && run_long "نصب ${#missing[@]} بسته سیستمی" maybe_sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"; then
      ok "بسته های سیستم نصب شد"; return 0
    fi
    [[ -f "$OFFLINE_DIR/debs/Packages" ]] || die "apt به mirror نرسید و بسته آفلاین .deb ندارد. یک mirror ایرانی در /etc/apt/sources.list تنظیم کنید."
    warn "apt به mirror نرسید — نصب از .deb های بسته"
    local lists src; lists="$(mktemp -d)"; mkdir -p "$lists/partial"; src="$lists/offline.list"
    echo "deb [trusted=yes] file:$OFFLINE_DIR/debs ./" > "$src"
    local o=(-o "Dir::Etc::SourceList=$src" -o Dir::Etc::SourceParts=/dev/null -o "Dir::State::Lists=$lists")
    run_long "فهرست .deb های بسته" maybe_sudo apt-get "${o[@]}" update || die "خواندن .deb های بسته ناموفق بود."
    run_long "نصب از .deb های بسته" maybe_sudo env DEBIAN_FRONTEND=noninteractive apt-get "${o[@]}" install -y --no-install-recommends "${missing[@]}" \
      || die "نصب از .deb های بسته ناموفق بود."
    ok "بسته های سیستم از بسته آفلاین نصب شد"; return 0
  fi
  run_long "apt-get update" maybe_sudo env DEBIAN_FRONTEND=noninteractive apt-get update -y || die "به روزرسانی فهرست بسته ها ناموفق بود."
  run_long "نصب ${#missing[@]} بسته سیستمی" maybe_sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}" \
    || die "نصب بسته های سیستمی ناموفق بود."
  ok "بسته های سیستم نصب شد"
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
  log "ارتقای سیستم عامل (apt-get update && apt-get upgrade -y) ..."
  run_long "apt-get update" maybe_sudo "${SYSUP_CMD_UPDATE[@]}" || die "apt-get update ناموفق بود."
  run_long "apt-get upgrade (ممکن است چند دقیقه طول بکشد)" maybe_sudo "${SYSUP_CMD_UPGRADE[@]}" || die "apt-get upgrade ناموفق بود."
  ok "سیستم عامل ارتقا یافت (فایل های پیکربندی موجود حفظ شد)"
  if [[ -f /var/run/reboot-required ]]; then warn "سیستم به ریبوت نیاز دارد (کرنل/کتابخانه ها) — در زمان مناسب: sudo reboot"; fi
}
maybe_system_upgrade() {  # fresh install: offered in the wizard, default yes
  case "$DO_SYSUP" in
    1) system_upgrade ;;
    2) log "ارتقای سیستم رد شد (--no-system-upgrade)" ;;
    *) if ! interactive || yes_no Y "ارتقای سیستم عامل (apt-get update && apt-get upgrade -y) هم انجام شود؟"; then system_upgrade
       else log "ارتقای سیستم رد شد (بعدا: bash install.sh --system-upgrade)"; fi ;;
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
  if ! need_cmd tesseract; then warn "tesseract نصب نیست — OCR عکس های تولید کار نمی کند (bash install.sh --repair)"; return 0; fi
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
  local pipx=()
  [[ -n "$OFFLINE_DIR" ]] && pipx=(--no-index --find-links "$OFFLINE_DIR/wheels")
  if (( DRY_RUN )); then log "[dry-run] python3 -m venv .venv && pip install ${pipx[*]} -r requirements.txt"; return 0; fi
  if [[ -d "$root/.venv" ]] && ! venv_healthy "$root"; then
    warn ".venv خراب است (مثلا بعد از ارتقای python) — ساخت دوباره"
    rm -rf "$root/.venv"
  fi
  if [[ ! -d "$root/.venv" ]]; then
    log "ساخت محیط مجازی .venv ..."
    run_long "ساخت .venv" python3 -m venv "$root/.venv" || { rm -rf "$root/.venv"; die "ساخت venv ناموفق بود. بسته python3-venv را نصب کنید: sudo apt install python3-venv"; }
  fi
  run_long "به روزرسانی pip" "$VENV_PY" -m pip install "${pipx[@]}" -U pip wheel || die "به روزرسانی pip ناموفق بود."
  run_long "نصب وابستگی های Python (چند دقیقه)" "$VENV_PY" -m pip install "${pipx[@]}" -r "$root/requirements.txt" \
    || die "نصب وابستگی های Python ناموفق بود."
  ok "وابستگی های Python نصب شد"
}
verify_imports() {
  local root="$1"
  (( DRY_RUN )) && return 0
  (cd "$root" && "$VENV_PY" - <<'PY') || die "import ماژول های برنامه ناموفق بود (خروجی بالا). bash install.sh --repair"
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
  ok "import ماژول های اصلی موفق (bot, web, db, OCR, PDF)"
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
(Enter = مقدار داخل [ ] پذیرفته می شود)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WIZ
  cur="$(env_get BALE_BOT_TOKEN)"
  while true; do
    if token_format_ok "$cur"; then
      token="$(ask_secret "۱) توکن بازو از @botfather (Enter = توکن فعلی …${cur: -4})")"
      token="${token:-$cur}"
    else
      token="$(ask_secret "۱) توکن بازو از @botfather در بله (مخفی تایپ می شود)")"
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
    admin_id="$(ask "۳) شناسه عددی مالک در بله (اختیاری؛ خالی = اولین /start مالک می شود)" "$(env_get ADMIN_BALE_USER_ID)")"
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
  host="$(ask "۵) آدرس گوش دادن وب (0.0.0.0 = از بیرون، 127.0.0.1 = فقط پشت nginx)" "${def:-0.0.0.0}")"
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
    if yes_no Y "۹) سرویس دائمی systemd (روشن شدن خودکار بعد از ریبوت) نصب شود؟"; then SYSTEMD_CHOICE=y; else SYSTEMD_CHOICE=n; fi
  fi
  if (( DO_ASSISTANT == 0 )); then
    if yes_no N "۱۰) دستیار هوشمند صوتی (Ollama + Whisper، حدود ۳ گیگ دانلود) هم نصب شود؟"; then DO_ASSISTANT=1; else DO_ASSISTANT=2; fi
  fi
  ok ".env نوشته شد (chmod 600؛ توکن …${token: -4})"
}

ensure_env() {
  if (( DRY_RUN )); then log "[dry-run] ویزارد .env رد شد"; return 0; fi
  if env_configured && (( ! RECONFIGURE )); then
    ok ".env از قبل پیکربندی شده است — حفظ شد"
    if interactive && [[ -z "${BALE_BOT_TOKEN:-}" ]] && yes_no N "می خواهید تنظیمات (.env) را دوباره وارد کنید؟"; then run_wizard; fi
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
  ok "سرویس های $BOT_SERVICE و $WEB_SERVICE نصب و برای بوت فعال شدند"
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
  if (( DRY_RUN )); then log "[dry-run] استارت سرویس ها رد شد"; return 0; fi
  if [[ "$SYSTEMD_CHOICE" == y ]] || systemd_installed; then
    if have_systemd; then
      maybe_sudo systemctl restart "$BOT_SERVICE" "$WEB_SERVICE"
      ok "سرویس ها (re)start شدند"
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
  ری استارت:   sudo systemctl restart $BOT_SERVICE $WEB_SERVICE
  توقف:       sudo systemctl stop $BOT_SERVICE $WEB_SERVICE
  لاگ زنده:   journalctl -u $BOT_SERVICE -f    |    journalctl -u $WEB_SERVICE -f
  لاگ برنامه: tail -f $WORKDIR/data/bot.log"
  else
    svc="  (بدون systemd — با nohup؛ بعد از ریبوت دوباره: bash install.sh --start)
  توقف:  kill \$(cat $WORKDIR/data/bot.pid) \$(cat $WORKDIR/data/web.pid)
  لاگ:   tail -f $WORKDIR/data/bot.log $WORKDIR/data/web.log"
  fi
  if [[ -n "$(env_get ADMIN_BALE_USER_ID)" ]]; then owner_line="شناسه $(env_get ADMIN_BALE_USER_ID) مالک است."; else owner_line="اولین کسی که /start بزند مالک می شود."; fi
  if (( WEB_LOGIN_READY )); then login_line="  ✓ ورود وب فعال است."; else login_line="  ورود وب بعد از اولین /start فعال می شود:  cd $WORKDIR && bash install.sh --web-login"; fi
  if (( HEALTH_OK )); then status_line="✅ نصب کامل شد؛ ربات و پنل وب در حال اجرا هستند."; else status_line="⚠ نصب انجام شد ولی تست سلامت کامل نبود (هشدارهای بالا را ببینید)."; fi
  cat >&2 <<SUM

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
$status_line
مسیر: $WORKDIR

ربات بله: @$(env_get BOT_USERNAME)
  در بله به ربات /start بزنید — $owner_line

پنل وب:   http://${ip:-IP-سرور}:${port}/login   (گوش دادن روی ${host}:${port})
  نام کاربری: ${user}
  رمز:       grep WEB_ADMIN_PASSWORD $WORKDIR/.env
$login_line
  فایروال: اگر ufw فعال است: sudo ufw allow ${port}/tcp

سرویس ها:
$svc

به روزرسانی:  cd $WORKDIR && bash install.sh --update
خودترمیم:    cd $WORKDIR && bash install.sh --repair
تنظیم دوباره: cd $WORKDIR && bash install.sh --reconfigure

هشدار: فقط یک نمونه از ربات با یک توکن باید اجرا شود؛ نسخه های دیگر را خاموش کنید.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUM
}

# --- دستیار هوشمند صوتی (اختیاری): scripts/install_assistant.sh ---
# بدون ollama.com: Ollama از GitHub releases، مدل GGUF از hf-mirror.com/huggingface.co، Whisper/Piper از PyPI + HF.
# Every failure is non-fatal: warn in Persian, log, keep ASSISTANT_ENABLED=0 and continue the main install.
ASSISTANT_BUNDLE="$ASSISTANT_BUNDLE_ARG"   # --assistant-offline FILE.tar|DIR

http_code() {  # http_code URL → HTTP status after redirects ("000" = no connection / DNS / timeout); fails fast
  local code
  code="$(curl -sSL -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 15 "$1" 2>/dev/null)" || true
  printf '%s' "${code:-000}"
}

assistant_failed() {  # never aborts the install
  local reason="${1:-نامشخص}"
  warn "دستیار هوشمند کامل نصب نشد (${reason}). ربات بدون دستیار کار می کند."
  warn "دوباره امتحان (دانلودها ادامه پیدا می کنند): cd $WORKDIR && sudo bash install.sh --with-assistant"
  warn "یا با بسته آفلاین: sudo bash install.sh --assistant-offline /root/nasoz_assistant_….tar"
  warn "بررسی دسترسی منابع: bash scripts/install_assistant.sh --check"
  printf 'ASSISTANT FAILED (non-fatal): %s\n' "$reason" >>"$INSTALL_LOG" 2>/dev/null || true
  env_set ASSISTANT_ENABLED 0 || true
}

run_assistant_installer() {  # → 0 ready, 3 partial, other = unexpected; never exits the caller
  local script args=() rc=0
  script="$(dirname "$SELF_SCRIPT")/install_assistant.sh"   # the copy next to this installer (offline bundle: newer)
  [[ -f "$script" ]] || script="$WORKDIR/scripts/install_assistant.sh"
  [[ -f "$script" ]] || { assistant_failed "فایل scripts/install_assistant.sh پیدا نشد"; return 0; }
  [[ -n "$ASSISTANT_BUNDLE" ]] && args+=(--offline "$ASSISTANT_BUNDLE")
  (( DRY_RUN )) && args+=(--dry-run)
  args+=("${ASSISTANT_ARGS[@]}")
  local -a envs=(WORKDIR="$WORKDIR" _NASOZ_INSTALL_LOG="$INSTALL_LOG" _NASOZ_ASSIST_NESTED=1) v
  for v in $(compgen -e); do   # sources/paths/proxy survive sudo (sudo resets the environment)
    case "$v" in ASSISTANT_*|HTTPS_PROXY|HTTP_PROXY|NO_PROXY|https_proxy|http_proxy|no_proxy|ALL_PROXY|NO_COLOR|TERM) envs+=("$v=${!v}") ;; esac
  done
  if (( EUID == 0 )) || ! have_root; then   # root, or no sudo: per-user install (~/.local, no systemd)
    env "${envs[@]}" bash "$script" "${args[@]}" || rc=$?
  else
    maybe_sudo env "${envs[@]}" bash "$script" "${args[@]}" || rc=$?
  fi
  case "$rc" in
    0) ;;
    3) assistant_failed "بعضی بخش ها نصب نشدند؛ خلاصه بالا را ببینید" ;;
    *) assistant_failed "خطای غیرمنتظره در نصب کننده دستیار (کد $rc)" ;;
  esac
  return 0
}

maybe_assistant() {
  if (( DO_ASSISTANT != 1 )); then log "دستیار هوشمند نصب نشد (بعدا: bash install.sh --with-assistant)"; return 0; fi
  run_assistant_installer
}

# ========== interactive main menu (no flags + terminal; flags always skip it) ==========
SELF_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
MENU_DIR=""   # install dir found WITHOUT cloning ("" = nothing here yet)
menu_find_dir() {
  local c
  for c in "${INSTALL_DIR:-}" "$(dirname "$(dirname "$SELF_SCRIPT")")" "$PWD" /opt/nasoz "$PWD/$REPO_NAME" "/root/$REPO_NAME"; do
    [[ -n "$c" ]] && is_repo_root "$c" || continue
    # prefer a configured install; the bundle's own app/ is never "the install"
    if [[ -f "$c/.env" ]] && token_format_ok "$(WORKDIR="$c" env_get BALE_BOT_TOKEN)"; then (cd "$c" && pwd); return 0; fi
  done
  for c in "${INSTALL_DIR:-}" "$(dirname "$(dirname "$SELF_SCRIPT")")"; do
    [[ -n "$c" ]] && is_repo_root "$c" && [[ "$(basename "$c")" != app ]] && { (cd "$c" && pwd); return 0; }
  done
  return 0
}
menu_installed() { [[ -n "$MENU_DIR" ]] && WORKDIR="$MENU_DIR" env_configured; }
menu_need_install() {
  if menu_installed; then return 0; fi
  warn "ربات هنوز روی این سرور نصب نشده است (فایل .env با توکن پیدا نشد)."
  log "  ابتدا گزینه 1 (نصب جدید) را انتخاب کنید."
  return 1
}
menu_pause() { local _r; printf '\nبرای بازگشت به منو Enter بزنید...' >&2; IFS= read -r -u "$TTY_FD" _r || true; }
menu_run() {  # menu_run args… → run a child installer; its failure never ends the menu
  local rc=0
  log ""
  _NASOZ_MENU_CHILD=1 bash "$@" || rc=$?
  if (( rc == 0 )); then ok "انجام شد."; else warn "عملیات با خطا تمام شد (کد $rc). جزئیات در پیام های بالا و لاگ نصب."; fi
  return 0
}
menu_script() { if [[ -n "$MENU_DIR" ]]; then printf '%s' "$MENU_DIR/scripts/install.sh"; else printf '%s' "$SELF_SCRIPT"; fi; }
MENU_DRY=(); if (( DRY_RUN )); then MENU_DRY=(--dry-run); fi
redact_stream() {  # never let the bot token reach the terminal
  local t; t="$(WORKDIR="$MENU_DIR" env_get BALE_BOT_TOKEN 2>/dev/null || true)"
  awk -v t="$t" '{ if (t != "") while ((i = index($0, t)) > 0) $0 = substr($0, 1, i - 1) "<توکن مخفی>" substr($0, i + length(t)); print }' \
    | LC_ALL=C sed -E 's/[0-9]{6,}:[A-Za-z0-9_-]{20,}/<توکن مخفی>/g; s/\xE2\x80\x8C/ /g; s/\xE2\x80[\x8B\x8D\x8E\x8F]//g'   # + ZWNJ → space (PuTTY/CMD)
}
menu_py() { if [[ -x "$MENU_DIR/.venv/bin/python" ]]; then printf '%s' "$MENU_DIR/.venv/bin/python"; else printf '%s' python3; fi; }

# ---- 3) status ----
url_check() {  # url_check LABEL URL → "✓/✗ LABEL (HTTP code)"
  local code mark="✗"
  code="$(http_code "$2")"
  if [[ "$code" =~ ^[23] || "$code" == 401 || "$code" == 404 || "$code" == 405 ]]; then mark="✓"; fi
  if [[ "$code" == 000 ]]; then printf '  %s  بدون اتصال  %s\n' "$mark" "$1" >&2
  else printf '  %s  HTTP %s    %s\n' "$mark" "$code" "$1" >&2; fi
}
apt_mirror_url() {
  local u=""
  u="$(grep -hsE '^[[:space:]]*deb[[:space:]]' /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null \
       | awk '{for (i = 2; i <= NF; i++) if ($i ~ /^https?:/) {print $i; exit}}')" || true
  [[ -n "$u" ]] || u="$(grep -hsE '^URIs:' /etc/apt/sources.list.d/*.sources 2>/dev/null | awk '{print $2; exit}')" || true
  printf '%s' "$u"
}
svc_line() {  # svc_line systemd-name label pid-name script
  local st since secs up=""
  if systemd_installed && have_systemd; then
    st="$(systemctl is-active "$1" 2>/dev/null || true)"
    if [[ "$st" == active ]]; then
      since="$(systemctl show -p ActiveEnterTimestamp --value "$1" 2>/dev/null || true)"
      if [[ -n "$since" ]] && secs=$(( $(date +%s) - $(date -d "$since" +%s 2>/dev/null || date +%s) )); then
        up="، روشن از $(( secs / 86400 )) روز و $(( secs % 86400 / 3600 )) ساعت و $(( secs % 3600 / 60 )) دقیقه پیش"
      fi
      printf '  ✓ %s: فعال (systemd: %s%s)\n' "$2" "$1" "$up" >&2
    else
      printf '  ✗ %s: %s (systemd: %s)\n' "$2" "${st:-نامشخص}" "$1" >&2
    fi
  elif pid_alive "$MENU_DIR/data/$3.pid"; then
    up="$(ps -o etime= -p "$(cat "$MENU_DIR/data/$3.pid")" 2>/dev/null | tr -d ' ' || true)"
    printf '  ✓ %s: در حال اجرا (nohup، pid %s، مدت %s)\n' "$2" "$(cat "$MENU_DIR/data/$3.pid")" "${up:-?}" >&2
  else
    printf '  ✗ %s: خاموش\n' "$2" >&2
  fi
}
menu_status() {
  local d="$MENU_DIR" tok port host k v pyv
  log ""; log "━━━━━━━━ وضعیت ━━━━━━━━"
  log "نسخه ربات:    $(app_version "$d")"
  log "مسیر نصب:     ${d:-نصب نشده}"
  if [[ -x "$d/.venv/bin/python" ]]; then pyv="$("$d/.venv/bin/python" -V 2>&1) (.venv)"; else pyv="$(python3 -V 2>&1 || echo 'python3 پیدا نشد') (سیستم)"; fi
  log "پایتون:       $pyv"
  if ! menu_installed; then
    log ""; warn "ربات هنوز نصب نشده است (.env با توکن پیدا نشد) — برای نصب گزینه 1 را بزنید."
    d=""
  fi
  if [[ -n "$d" ]]; then
    tok="$(WORKDIR="$d" env_get BALE_BOT_TOKEN)"
    port="$(WORKDIR="$d" env_get WEB_PORT)"; port="${port:-8000}"; host="$(WORKDIR="$d" env_get WEB_HOST)"
    log ""; log "تنظیمات (.env):"
    if token_format_ok "$tok"; then
      local rc=0; getme "$tok" || rc=$?
      case "$rc" in
        0) log "  توکن:          تنظیم شده (مخفی) — ✓ getMe: @${GETME_USERNAME}" ;;
        1) log "  توکن:          تنظیم شده (مخفی) — ✗ بله این توکن را رد کرد" ;;
        *) log "  توکن:          تنظیم شده (مخفی) — ✗ دسترسی به tapi.bale.ai برای getMe نیست" ;;
      esac
    else
      log "  توکن:          تنظیم نشده"
    fi
    log "  نام کاربری ربات: @$(WORKDIR="$d" env_get BOT_USERNAME)"
    local aid; aid="$(WORKDIR="$d" env_get ADMIN_BALE_USER_ID)"
    log "  شناسه مالک (.env): ${aid:-- (اولین /start مالک می شود)}"
    log "  پنل وب:        ${host:-0.0.0.0}:${port}  (کاربر: $(WORKDIR="$d" env_get WEB_ADMIN_USERNAME))"
    log ""; log "سرویس ها:"
    svc_line "$BOT_SERVICE" "ربات" bot main.py
    svc_line "$WEB_SERVICE" "پنل وب" web web_main.py
    log ""; log "دستیار هوشمند (اختیاری؛ نصب: گزینه 10):"
    local om; om="$(WORKDIR="$d" env_get OLLAMA_MODEL)"
    if need_cmd ollama; then log "  ✓ Ollama:       $(command -v ollama)"; else log "  ✗ Ollama:       نصب نیست"; fi
    if curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags 2>/dev/null | grep -q "\"${om:-qwen2.5:3b}\""; then
      log "  ✓ مدل زبانی:    ${om:-qwen2.5:3b} (سرویس 127.0.0.1:11434 فعال)"
    else log "  ✗ مدل زبانی:    ${om:-qwen2.5:3b} آماده نیست"; fi
    if [[ -d "$d/.venv/lib" ]] && compgen -G "$d/.venv/lib/python3*/site-packages/faster_whisper" >/dev/null; then
      log "  ✓ Whisper:      faster-whisper نصب است"; else log "  ✗ Whisper:      faster-whisper نصب نیست"; fi
    if need_cmd piper; then log "  ✓ Piper:        $(command -v piper)"; else log "  - Piper:        نصب نیست (اختیاری)"; fi
  fi
  log ""; log "اتصال ها:"
  url_check "tapi.bale.ai" "https://tapi.bale.ai/"
  [[ -n "$d" ]] && url_check "پنل وب /login" "http://127.0.0.1:${port}/login"
  url_check "github.com" "https://github.com/"
  url_check "pypi.org" "https://pypi.org/simple/"
  local m; m="$(apt_mirror_url)"
  if [[ -n "$m" ]]; then url_check "mirror apt" "$m"; else log "  - mirror apt: پیدا نشد"; fi
  if [[ -n "$d" ]]; then
    log ""; log "داده و بک آپ:"
    log "  حجم data/:     $(du -sh "$d/data" 2>/dev/null | cut -f1 || echo -)   (فضای آزاد دیسک: $(df -h "$d" 2>/dev/null | awk 'NR==2{print $4}'))"
    if [[ -f "$d/data/bot.db" || -x "$d/.venv/bin/python" ]]; then
      while IFS='=' read -r k v; do
        case "$k" in
          DB_SIZE) log "  حجم پایگاه داده: $v" ;;
          OWNERS) log "  مالک ها (بله):  $v" ;;
          ADMINS) log "  مدیرها (بله):   $v" ;;
          USERS) log "  کاربران فعال:  $v" ;;
          LAST_BACKUP) log "  آخرین بک آپ:    $v" ;;
          BACKUP_COUNT) log "  تعداد بک آپ ها: $v (در data/backups)" ;;
          NIGHTLY) log "  بک آپ شبانه:    $v" ;;
          DB_ERROR) log "  ✗ خواندن پایگاه داده ناموفق بود ($v)" ;;
        esac
      done < <(cd "$d" && timeout 60 "$(menu_py)" scripts/menu_helper.py status 2>/dev/null | redact_stream || true)
    fi
  fi
}

# ---- 4) backup ----
menu_backup() {
  menu_need_install || return 0
  local send=""
  if yes_no N "بک آپ برای مالک در بله هم فرستاده شود؟"; then send="--send"; fi
  if (( DRY_RUN )); then log "[dry-run] $(menu_py) scripts/menu_helper.py backup $send"; return 0; fi
  log "ساخت بک آپ کامل (پایگاه داده + uploads + bot_assets) ..."
  (cd "$MENU_DIR" && "$(menu_py)" scripts/menu_helper.py backup ${send:+"$send"} 2>&1 | redact_stream) || warn "ساخت بک آپ ناموفق بود."
}

# ---- 5) restore ----
menu_restore() {
  menu_need_install || return 0
  local -a files=() ; local line i=0 pick file owners oid
  log ""; log "بک آپ های موجود در $MENU_DIR/data/backups:"
  while IFS='|' read -r line rest; do
    [[ -n "$line" ]] || continue
    files+=("$line"); i=$((i + 1))
    printf '  %2d) %s  (%s)\n' "$i" "$(basename "$line")" "$rest" >&2
  done < <(cd "$MENU_DIR" && "$(menu_py)" scripts/menu_helper.py list 2>/dev/null | redact_stream || true)
  (( i )) || log "  (هیچ بک آپی پیدا نشد)"
  pick="$(ask "شماره بک آپ یا مسیر کامل فایل zip (0 = انصراف)" "0")"
  if [[ "$pick" == 0 ]]; then log "لغو شد."; return 0; fi
  if [[ "$pick" =~ ^[0-9]+$ ]] && (( pick >= 1 && pick <= i )); then file="${files[pick-1]}"; else file="$pick"; fi
  [[ -f "$file" ]] || { warn "فایل پیدا نشد: $file"; return 0; }
  # owner-level confirmation: the owner's Bale ID (from DB or .env) must be typed
  owners="$(cd "$MENU_DIR" && "$(menu_py)" scripts/menu_helper.py status 2>/dev/null | sed -n 's/^OWNERS=//p' | tr -d ' ' | tr '،' ' ')"
  [[ "$owners" == "-" ]] && owners=""
  owners="$owners $(WORKDIR="$MENU_DIR" env_get ADMIN_BALE_USER_ID)"
  if [[ -n "${owners// /}" ]]; then
    oid="$(ask "تایید مالک: شناسه عددی مالک در بله را وارد کنید")"
    if [[ -z "$oid" || " $owners " != *" $oid "* ]]; then warn "شناسه مالک درست نیست؛ بازیابی انجام نشد."; return 0; fi
  else
    warn "هنوز مالکی ثبت نشده است؛ فقط با تایید متنی ادامه می دهیم."
  fi
  log "قبل از بازیابی یک بک آپ خودکار «پیش از بازگردانی» گرفته می شود."
  menu_run "$(menu_script)" --restore "$file" "${MENU_DRY[@]}"
}

# ---- 7) services ----
menu_services() {
  local act tgt units=() names=()
  log ""; log "مدیریت سرویس ها:  1) استارت   2) توقف   3) ری استارت   4) وضعیت   0) بازگشت"
  act="$(ask "انتخاب" "0")"
  case "$act" in 1|2|3|4) ;; *) return 0 ;; esac
  menu_need_install || return 0
  if [[ "$act" == 4 ]]; then svc_line "$BOT_SERVICE" "ربات" bot main.py; svc_line "$WEB_SERVICE" "پنل وب" web web_main.py; return 0; fi
  tgt="$(ask "کدام؟  1) ربات   2) پنل وب   3) هر دو" "3")"
  case "$tgt" in 1) units=("$BOT_SERVICE"); names=(bot) ;; 2) units=("$WEB_SERVICE"); names=(web) ;; *) units=("$BOT_SERVICE" "$WEB_SERVICE"); names=(bot web) ;; esac
  local verb; case "$act" in 1) verb=start ;; 2) verb=stop ;; 3) verb=restart ;; esac
  if (( DRY_RUN )); then log "[dry-run] $verb ${units[*]}"; return 0; fi
  WORKDIR="$MENU_DIR"; VENV_PY="$MENU_DIR/.venv/bin/python"
  if systemd_installed && have_systemd; then
    maybe_sudo systemctl "$verb" "${units[@]}" && ok "systemctl $verb ${units[*]}" || warn "systemctl $verb ناموفق بود: journalctl -u ${units[0]} -n 50"
    return 0
  fi
  local n s p
  for n in "${names[@]}"; do
    if [[ "$n" == bot ]]; then s=main.py; else s=web_main.py; fi
    if [[ "$verb" == stop ]]; then
      if pid_alive "$MENU_DIR/data/$n.pid"; then p="$(cat "$MENU_DIR/data/$n.pid")"; kill "$p" 2>/dev/null || true; ok "$n متوقف شد (pid $p)"; else log "$n از قبل خاموش است"; fi
    else
      nohup_restart "$n" "$s"; ok "$n با nohup اجرا شد (pid $(cat "$MENU_DIR/data/$n.pid"))"
    fi
  done
}

# ---- 8) logs ----
menu_logs() {
  local c n f
  log ""; log "مشاهده لاگ ها:  1) ربات   2) پنل وب   3) آخرین لاگ نصب   4) journalctl ربات   5) journalctl وب   0) بازگشت"
  c="$(ask "انتخاب" "0")"
  case "$c" in 1|2|3|4|5) ;; *) return 0 ;; esac
  [[ -n "$MENU_DIR" ]] || { menu_need_install; return 0; }
  n="$(ask "تعداد خط های آخر" "50")"; [[ "$n" =~ ^[0-9]+$ ]] || n=50
  case "$c" in
    1) f="$MENU_DIR/data/bot.log" ;;
    2) f="$MENU_DIR/data/web.log" ;;
    3) f="$(find "$MENU_DIR/data/logs" -maxdepth 1 -name 'nasoz_install_*.log' -printf '%T@ %p\n' 2>/dev/null | sort -rn | awk 'NR==1{print $2}')" ;;
    4|5) if need_cmd journalctl; then
           local u="$BOT_SERVICE"; [[ "$c" == 5 ]] && u="$WEB_SERVICE"
           log "━━━━ journalctl -u $u (آخرین $n خط) ━━━━"
           journalctl -u "$u" -n "$n" --no-pager 2>&1 | redact_stream >&2 || true
         else warn "journalctl روی این سیستم نیست."; fi
         return 0 ;;
  esac
  if [[ -z "$f" || ! -f "$f" ]]; then warn "فایل لاگ پیدا نشد: ${f:-data/logs}"; return 0; fi
  log "━━━━ $f (آخرین $n خط) ━━━━"
  tail -n "$n" "$f" | redact_stream >&2
}

# ---- 2) update ----
menu_update() {
  menu_need_install || return 0
  local mode=--update src="" def=""
  if yes_no N "ارتقای سیستم عامل (apt-get upgrade) هم انجام شود؟"; then mode=--full-update; fi
  if [[ -n "$OFFLINE_SRC" ]]; then def="$OFFLINE_SRC"; fi
  src="$(ask "مسیر بسته آفلاین (.tar.gz یا پوشه)؛ خالی = آنلاین از GitHub" "$def")"
  if [[ -n "$src" ]]; then
    [[ -e "$src" ]] || { warn "بسته پیدا نشد: $src"; return 0; }
    menu_run "$MENU_DIR/scripts/install.sh" "$mode" --offline "$src" "${MENU_DRY[@]}"
  else
    menu_run "$MENU_DIR/scripts/install.sh" "$mode" "${MENU_DRY[@]}"
  fi
}

# ---- 1) fresh install ----
menu_install() {
  if menu_installed; then
    warn "ربات از قبل در $MENU_DIR نصب شده است."
    yes_no N "دوباره نصب شود؟ (.env و data/ حفظ می شود؛ برای تغییر تنظیمات گزینه 9)" || return 0
  fi
  if [[ -n "$OFFLINE_SRC" && -f "$OFFLINE_SRC/install_offline.sh" ]] && ! menu_installed; then
    menu_run "$OFFLINE_SRC/install_offline.sh" --install "${MENU_DRY[@]}"
  elif [[ -n "$OFFLINE_SRC" ]]; then
    menu_run "$(menu_script)" --install --offline "$OFFLINE_SRC" "${MENU_DRY[@]}"
  else
    menu_run "$(menu_script)" --install "${MENU_DRY[@]}"
  fi
}

# ---- 10) voice assistant (optional) ----
menu_assistant() {
  menu_need_install || return 0
  local def="" src="" f
  for f in "${OFFLINE_SRC:+$(dirname "$OFFLINE_SRC")}"/nasoz_assistant_*.tar /root/nasoz_assistant_*.tar "$PWD"/nasoz_assistant_*.tar; do
    [[ -e "$f" ]] && { def="$f"; break; }
  done
  log "دستیار هوشمند صوتی: Ollama (از GitHub) + مدل زبانی (از hf-mirror.com / huggingface.co) + Whisper و Piper (از PyPI)."
  log "  حدود ۳ گیگ دانلود و ۴ گیگ حافظه لازم است؛ هر خطا فقط هشدار است و ربات بدون دستیار کار می کند."
  src="$(ask "مسیر بسته آفلاین دستیار (nasoz_assistant_….tar)؛ خالی = دانلود آنلاین" "$def")"
  if [[ -n "$src" ]]; then
    [[ -e "$src" ]] || { warn "بسته پیدا نشد: $src"; return 0; }
    log "  مدل زبانی همان مدل داخل بسته است."
    menu_run "$MENU_DIR/scripts/install.sh" --assistant-offline "$src" "${MENU_DRY[@]}"
    return 0
  fi
  local mem_kb mem_gb model defm="qwen2.5:3b"
  mem_kb="${_NASOZ_TEST_MEM_KB:-$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)}"   # test hook
  mem_gb="$(awk -v k="${mem_kb:-0}" 'BEGIN{printf "%.1f", k/1048576}')"
  if (( ${mem_kb:-0} > 0 && mem_kb < 6291456 )); then
    defm="qwen2.5:1.5b"
    warn "حافظه این سرور ${mem_gb} گیگ است (کمتر از ۶ گیگ)؛ مدل سبک تر qwen2.5:1.5b پیشنهاد می شود."
  else
    log "  حافظه این سرور: ${mem_gb} گیگ"
  fi
  log "  مدل ها: qwen2.5:3b (حدود ۲ گیگ، ۴ گیگ RAM) | qwen2.5:1.5b (حدود ۱ گیگ، ۲ تا ۳ گیگ RAM) | qwen2.5:0.5b (۵۰۰ مگ، ضعیف)"
  model="$(ask "کدام مدل نصب شود؟" "$defm")"
  model="$(printf '%s' "$model" | tr -d '[:space:]')"
  [[ -n "$model" ]] || model="$defm"
  menu_run "$MENU_DIR/scripts/install.sh" --with-assistant --model "$model" "${MENU_DRY[@]}"
}

main_menu() {
  local choice
  while :; do
    MENU_DIR="$(menu_find_dir)"
    print_logo "${MENU_DIR:-$(dirname "$(dirname "$SELF_SCRIPT")")}"
    if menu_installed; then log "   نصب شده در: $MENU_DIR"; else log "   هنوز نصب نشده است — گزینه 1"; fi
    [[ -n "$OFFLINE_SRC" ]] && log "   بسته آفلاین: $OFFLINE_SRC"
    (( DRY_RUN )) && log "   [dry-run] هیچ تغییری انجام نمی شود"
    cat >&2 <<'MENU'

  1) نصب جدید
  2) به روزرسانی
  3) وضعیت
  4) گرفتن بک آپ
  5) بازیابی بک آپ
  6) تعمیر
  7) مدیریت سرویس ها
  8) مشاهده لاگ ها
  9) تغییر تنظیمات (.env)
 10) نصب دستیار هوشمند (اختیاری)
  0) خروج

MENU
    printf 'گزینه را وارد کنید: ' >&2
    if ! IFS= read -r -u "$TTY_FD" choice; then log ""; log "پایان ورودی — خروج."; break; fi
    choice="$(printf '%s' "$choice" | tr -d '[:space:]')"
    choice="$(printf '%s' "$choice" | LC_ALL=C.UTF-8 sed 'y/۰۱۲۳۴۵۶۷۸۹/0123456789/')"
    case "$choice" in
      1) menu_install ;;
      2) menu_update ;;
      3) menu_status ;;
      4) menu_backup ;;
      5) menu_restore ;;
      6) if menu_need_install; then menu_run "$MENU_DIR/scripts/install.sh" --repair "${MENU_DRY[@]}"; fi ;;
      7) menu_services ;;
      8) menu_logs ;;
      9) if menu_need_install; then menu_run "$MENU_DIR/scripts/install.sh" --reconfigure "${MENU_DRY[@]}"; fi ;;
      10) menu_assistant ;;
      0|q|Q|exit) log "خروج."; break ;;
      "") continue ;;
      *) warn "گزینه نامعتبر: $choice (0 تا 10)"; continue ;;
    esac
    menu_pause
  done
  [[ -s "$INSTALL_LOG" ]] || rm -f "$INSTALL_LOG" 2>/dev/null || true
  trap - EXIT; exit 0
}

# ========== main ==========
if (( WANT_MENU )) || { (( NASOZ_ARGC == 0 )) && [[ -z "${_NASOZ_REEXEC:-}${_NASOZ_MENU_CHILD:-}" ]] && interactive; }; then
  interactive || die "منو به ترمینال تعاملی نیاز دارد (یا از پرچم ها استفاده کنید: bash install.sh --help)."
  main_menu
fi
if [[ -z "${_NASOZ_REEXEC:-}${_NASOZ_MENU_CHILD:-}" ]]; then print_logo "$(dirname "$(dirname "$SELF_SCRIPT")")"; fi
log "━━━━━━━━ نصب بازوی مواد (بله) ━━━━━━━━"
if interactive; then
  if [[ $TTY_FD == 0 ]]; then log "ترمینال: stdin"; else log "ترمینال: /dev/tty"; fi
else
  log "حالت غیرتعاملی"
fi

# total steps per mode (one «step» call per item below)
plan_total() {
  if [[ -n "$RESTORE_FILE" ]]; then echo 6
  elif (( DO_SYSUP_ONLY && ! DO_UPDATE )); then echo 3
  elif (( DO_START_ONLY )); then echo 2
  elif (( DO_UPDATE )); then echo $(( 10 + (DO_SYSUP == 1 ? 1 : 0) ))
  elif (( DO_REPAIR )); then echo 8
  else echo $(( 11 + (DO_SYSUP == 2 ? 0 : 1) ))
  fi
}
STEP_TOTAL="$(plan_total)"
if (( DO_WEB_LOGIN )); then STEP_TOTAL=0; fi

OFFLINE_DIR=""
if [[ -n "${_NASOZ_REEXEC:-}" && -n "${_NASOZ_OFFLINE_DIR:-}" ]]; then OFFLINE_SRC="$_NASOZ_OFFLINE_DIR"; fi
if [[ -n "$OFFLINE_SRC" ]]; then
  if [[ -f "$OFFLINE_SRC" ]]; then   # bundle.tar.gz → extract next to it
    _x="$(mktemp -d "${TMPDIR:-/tmp}/nasoz_offline.XXXXXX")"
    tar -xzf "$OFFLINE_SRC" -C "$_x" || die "باز کردن بسته آفلاین ناموفق بود: $OFFLINE_SRC"
    OFFLINE_SRC="$(find "$_x" -mindepth 1 -maxdepth 1 -type d | head -n1)"
  fi
  [[ -d "$OFFLINE_SRC/wheels" && -d "$OFFLINE_SRC/app" ]] || die "بسته آفلاین نامعتبر است (wheels/ و app/ لازم است): $OFFLINE_SRC"
  OFFLINE_DIR="$(cd "$OFFLINE_SRC" && pwd)"
  if [[ -z "${_NASOZ_REEXEC:-}" && -f "$OFFLINE_DIR/SHA256SUMS" ]]; then
    (cd "$OFFLINE_DIR" && sha256sum -c --quiet SHA256SUMS) || die "SHA256SUMS بسته آفلاین مطابقت ندارد — فایل خراب است."
  fi
  export _NASOZ_OFFLINE_DIR="$OFFLINE_DIR"
  if (( DO_SYSUP == 0 )); then DO_SYSUP=2; fi   # offline: no OS upgrade unless asked (--system-upgrade/--full-update)
  if (( ! DO_WEB_LOGIN )); then STEP_TOTAL="$(plan_total)"; fi
  ok "حالت آفلاین: $OFFLINE_DIR (بدون GitHub/PyPI)"
fi
if [[ -z "$OFFLINE_DIR" ]]; then need_cmd git || ensure_system_packages; fi
WORKDIR="$(resolve_workdir)"
export WORKDIR
cd "$WORKDIR"
move_log_to "$WORKDIR"
log "مسیر نصب: $WORKDIR"
log "لاگ نصب: $INSTALL_LOG"
VENV_PY="$WORKDIR/.venv/bin/python"

# fail fast (before minutes of apt/pip) when a fresh install cannot get a token
if (( ! DRY_RUN && ! DO_UPDATE && ! DO_REPAIR && ! DO_WEB_LOGIN && ! DO_START_ONLY && ! _want_restore && ! DO_SYSUP_ONLY )) && [[ -z "$RESTORE_FILE" ]] \
   && ! env_configured && [[ -z "${BALE_BOT_TOKEN:-}" ]] && ! interactive; then
  no_tty_die
fi

if (( DO_WEB_LOGIN )); then
  seed_web_login 0
  if (( WEB_LOGIN_READY )); then log "نام کاربری: $(env_get WEB_ADMIN_USERNAME) — رمز: grep WEB_ADMIN_PASSWORD $WORKDIR/.env"; fi
  trap - EXIT; exit 0
fi
# --with-assistant / --assistant-offline on an installed server (no other action) → only the assistant
if (( DO_ASSISTANT == 1 && ! EXPLICIT_INSTALL && ! DO_UPDATE && ! DO_REPAIR && ! DO_START_ONLY && ! DO_SYSUP_ONLY && ! RECONFIGURE && ! _want_restore )) \
   && [[ -z "$RESTORE_FILE" ]] && env_configured; then
  STEP_TOTAL=2; STEP_N=0
  step "دستیار هوشمند صوتی"; run_assistant_installer
  step "ری استارت ربات (خواندن .env جدید)"
  if (( DRY_RUN )); then log "[dry-run] ری استارت ربات رد شد"
  elif systemd_installed && have_systemd && systemctl is-active --quiet "$BOT_SERVICE" 2>/dev/null; then
    maybe_sudo systemctl restart "$BOT_SERVICE" && ok "سرویس $BOT_SERVICE ری استارت شد"
  elif pid_alive "$WORKDIR/data/bot.pid"; then nohup_restart bot main.py; ok "ربات (nohup) ری استارت شد"
  else log "ربات در حال اجرا نیست؛ بعدا استارت کنید: bash install.sh --start"
  fi
  log "لاگ کامل: $INSTALL_LOG"
  trap - EXIT; exit 0
fi
if (( _want_restore )); then die "مسیر فایل بک آپ را بدهید: bash install.sh --restore nasoz_backup_….zip"; fi
if [[ -n "$RESTORE_FILE" ]]; then
  [[ -f "$RESTORE_FILE" ]] || die "فایل بک آپ پیدا نشد: $RESTORE_FILE"
  RESTORE_FILE="$(cd "$(dirname "$RESTORE_FILE")" && pwd)/$(basename "$RESTORE_FILE")"
  PY="$VENV_PY"; [[ -x "$PY" ]] || PY="python3"
  step "بررسی فایل بک آپ (manifest و sha256)"
  (cd "$WORKDIR" && "$PY" scripts/restore_backup.py --check "$RESTORE_FILE") || die "فایل بک آپ نامعتبر است؛ هیچ تغییری داده نشد."
  if (( DRY_RUN )); then
    step "توقف سرویس ها"; log "[dry-run] توقف $BOT_SERVICE، $WEB_SERVICE یا pid های data/"
    step "بک آپ پیش از بازگردانی و بازگردانی داده ها"; log "[dry-run] $PY scripts/restore_backup.py $RESTORE_FILE"
    step "به روزرسانی ساختار پایگاه داده (migrate)"; log "[dry-run] migrate رد شد"
    step "استارت ربات و وب"; log "[dry-run] استارت سرویس ها رد شد"
    step "تست سلامت"; log "[dry-run] تمام شد — هیچ چیزی تغییر نکرد."
    trap - EXIT; exit 0
  fi
  if interactive && ! yes_no N "همه داده های فعلی با این بک آپ جایگزین می شود. ادامه؟"; then die "لغو شد."; fi
  step "توقف سرویس ها"
  if systemd_installed && have_systemd; then maybe_sudo systemctl stop "$BOT_SERVICE" "$WEB_SERVICE" || true
  else for n in bot web; do if pid_alive "$WORKDIR/data/$n.pid"; then kill "$(cat "$WORKDIR/data/$n.pid")" 2>/dev/null || true; fi; done; sleep 2; fi
  ok "سرویس ها متوقف شدند"
  step "بک آپ پیش از بازگردانی و بازگردانی داده ها"
  # shellcheck disable=SC2016  # $1..$3 expand inside the child bash
  run_long "بازگردانی" bash -c 'cd "$1" && "$2" scripts/restore_backup.py "$3"' _ "$WORKDIR" "$PY" "$RESTORE_FILE" || warn "بازگردانی ناموفق بود — بک آپ «پیش از بازگردانی» در data/backups است."
  [[ -x "$VENV_PY" ]] || VENV_PY="$PY"
  step "به روزرسانی ساختار پایگاه داده (migrate)"; init_db "$WORKDIR"
  step "استارت ربات و وب"; start_services
  step "تست سلامت"; health_check; print_summary; trap - EXIT; exit 0
fi
if (( DO_SYSUP_ONLY && ! DO_UPDATE )); then
  step "ارتقای سیستم عامل"; system_upgrade
  step "استارت دوباره ربات و وب"
  if (( ! DRY_RUN )) && env_configured; then start_services; else log "  (رد شد)"; fi
  step "تست سلامت"
  if (( ! DRY_RUN )) && env_configured; then health_check; else log "  (رد شد)"; fi
  if (( DRY_RUN )); then log "[dry-run] تمام شد — هیچ چیزی نصب یا اجرا نشد."; fi
  trap - EXIT; exit 0
fi
if (( DO_START_ONLY )); then
  env_configured || die ".env هنوز توکن ندارد — bash install.sh"
  step "استارت ربات و وب"; start_services
  step "تست سلامت"; health_check; print_summary; trap - EXIT; exit 0
fi

if [[ -z "${_NASOZ_REEXEC:-}" ]]; then   # (a re-exec after git pull continues the same numbering)
  if (( DO_UPDATE && DO_SYSUP == 1 )); then step "ارتقای سیستم عامل"; system_upgrade; fi   # --full-update
  if (( DO_UPDATE )); then step "پشتیبان پایگاه داده"; backup_db "$WORKDIR"; fi
  if (( DO_UPDATE )) && [[ -n "$OFFLINE_DIR" ]]; then
    step "جایگزینی کد از بسته آفلاین ($(cat "$OFFLINE_DIR/app/.offline_commit" 2>/dev/null || echo '?'))"
    if (( DRY_RUN )); then log "[dry-run] کپی $OFFLINE_DIR/app → $WORKDIR (.env، data/، uploads/، .venv دست نمی خورد)"
    else
      tar -C "$OFFLINE_DIR/app" --exclude=./.env --exclude=./data --exclude=./uploads --exclude=./.venv -cf - . | tar -C "$WORKDIR" -xf - \
        || die "جایگزینی کد ناموفق بود."
      ok "کد به نسخه بسته به روز شد"
      export _NASOZ_REEXEC=1 _NASOZ_STEP_N="$STEP_N" _NASOZ_INSTALL_LOG="$INSTALL_LOG"
      trap - EXIT; exec bash "$WORKDIR/scripts/install.sh" "$@"
    fi
  fi
  if (( DO_UPDATE )) && [[ -z "$OFFLINE_DIR" ]]; then step "دریافت آخرین نسخه (git pull)"; fi
  if (( DO_UPDATE )) && [[ -z "$OFFLINE_DIR" ]] && (( DRY_RUN )); then log "[dry-run] git -C $WORKDIR pull --ff-only"; fi
  if (( DO_UPDATE )) && [[ -z "$OFFLINE_DIR" ]] && [[ -d "$WORKDIR/.git" ]] && (( ! DRY_RUN )); then
    run_long "git pull" git -C "$WORKDIR" pull --ff-only || warn "git pull ناموفق بود (تغییرات محلی؟) — با نسخه فعلی ادامه می دهیم. git -C $WORKDIR status"
    # the script on disk may have changed; re-exec the new version once
    export _NASOZ_REEXEC=1 _NASOZ_STEP_N="$STEP_N" _NASOZ_INSTALL_LOG="$INSTALL_LOG"
    trap - EXIT; exec bash "$WORKDIR/scripts/install.sh" "$@"
  fi
fi

if (( ! DO_UPDATE && ! DO_REPAIR )) && (( DO_SYSUP != 2 )); then step "ارتقای سیستم عامل"; maybe_system_upgrade; fi   # fresh install (wizard question, default yes)
step "نصب/بررسی بسته های سیستمی"; ensure_system_packages
step "بررسی پایتون و tesseract"; check_python; check_tesseract
if (( ! DO_UPDATE && ! DO_REPAIR )); then step "پیکربندی (.env)"; ensure_env; fi   # ask questions early, then long installs
step "محیط پایتون و وابستگی ها"; setup_venv "$WORKDIR"
step "بررسی ماژول های برنامه"; verify_imports "$WORKDIR"
step "آماده سازی پایگاه داده (migrate)"; init_db "$WORKDIR"

if (( DO_UPDATE || DO_REPAIR )); then
  step "به روزرسانی تنظیمات سرویس ها"
  if (( ! DRY_RUN )); then
    env_configured || die ".env توکن ندارد — ابتدا: bash install.sh"
    ensure_common_env
  fi
  if { systemd_installed && have_systemd; } || (( DRY_RUN )); then install_units; fi   # refresh units
else
  step "دستیار هوشمند (اختیاری)"; maybe_assistant
  step "نصب سرویس ها"
  if [[ -z "$SYSTEMD_CHOICE" ]]; then
    if have_systemd && (( EUID == 0 )); then SYSTEMD_CHOICE=y; else SYSTEMD_CHOICE=n; fi
  fi
  if (( DRY_RUN )); then install_units
  elif [[ "$SYSTEMD_CHOICE" == y ]]; then
    if have_systemd; then install_units; else warn "systemd در دسترس نیست — اجرا با nohup"; SYSTEMD_CHOICE=n; fi
  fi
fi

step "استارت ربات و وب"; start_services
if (( ! DO_UPDATE && ! DO_REPAIR )); then step "ورود پنل وب"; seed_web_login 1
elif (( DO_SEED )); then seed_web_login 1; fi
step "تست سلامت"; health_check
log "لاگ کامل نصب: $INSTALL_LOG"
if (( DRY_RUN )); then log "[dry-run] تمام شد — هیچ چیزی نصب یا اجرا نشد."; trap - EXIT; exit 0; fi
print_summary
trap - EXIT
exit 0
