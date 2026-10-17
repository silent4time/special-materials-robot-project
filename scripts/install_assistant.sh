#!/usr/bin/env bash
# shellcheck disable=SC2317,SC2016  # functions run indirectly via part(); $1/$2 in bash -c are intentional
# نصب دستیار هوشمند صوتی نسوز (اختیاری) بدون نیاز به ollama.com
#   Ollama  : باینری رسمی از GitHub releases (فقط کتابخانه های CPU) در /usr/local + کاربر ollama + سرویس systemd
#   مدل     : GGUF از hf-mirror.com سپس huggingface.co و import با ollama create؛ در آخر registry.ollama.ai
#   Whisper : faster-whisper از PyPI + مدل Systran/faster-whisper-<size> از hf-mirror.com سپس huggingface.co
#   Piper   : piper-tts از PyPI + صدای فارسی rhasspy/piper-voices (اختیاری)
# هر بخش غیر بحرانی است: خطا = هشدار فارسی + ادامه. ربات بدون دستیار هم کار می کند.
#
#   sudo bash scripts/install_assistant.sh                     # آنلاین
#   sudo bash scripts/install_assistant.sh --offline FILE.tar  # از بسته nasoz_assistant_*.tar (یا پوشه باز شده)
#   bash scripts/install_assistant.sh --check                  # فقط بررسی دسترسی منابع (کد HTTP)
#   گزینه ها: --model qwen2.5:3b  --whisper small  --voice fa_IR-amir-medium  --no-piper  --no-whisper
#             --dry-run  --workdir DIR
#
# ترتیب منابع (با فاصله، به همین ترتیب امتحان می شوند):
#   ASSISTANT_OLLAMA_SOURCES="github official"        (url:https://... هم مجاز است: tar.zst یا tgz)
#   ASSISTANT_MODEL_SOURCES="hf-mirror huggingface registry"
#   ASSISTANT_HF_SOURCES="hf-mirror huggingface"      (Whisper و Piper؛ url:https://mirror هم مجاز است)
#   ASSISTANT_PIP_INDEXES="https://pypi.org/simple"
# دیگر: ASSISTANT_GGUF_REPO / ASSISTANT_GGUF_FILE (مدل دلخواه)، ASSISTANT_PREFIX (/usr/local)،
#   ASSISTANT_CACHE (/var/cache/nasoz-assistant)، ASSISTANT_PIPER_DIR (/opt/piper/models)،
#   ASSISTANT_KEEP_GPU=1 (کتابخانه های CUDA/ROCm هم نصب شوند)، ASSISTANT_KEEP_CACHE=1، ASSISTANT_VERIFY=0،
#   HTTPS_PROXY (پروکسی اختیاری؛ curl و pip از آن استفاده می کنند)
#
# خروجی: 0 = Ollama + مدل + Whisper آماده (دستیار قابل فعال سازی است)؛ 3 = ناقص. هیچ وقت نصب اصلی را متوقف نمی کند.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR="${WORKDIR:-$(dirname "$HERE")}"
MODEL="${ASSISTANT_MODEL:-}"
WHISPER_SIZE="${ASSISTANT_WHISPER_MODEL:-small}"
VOICE="${ASSISTANT_PIPER_VOICE:-fa_IR-amir-medium}"
BOT_VOICE="fa_IR-donya-medium"            # نامی که services/voice_assistant.py انتظار دارد
OFFLINE_SRC=""; DRY_RUN=0; CHECK_ONLY=0; DO_WHISPER=1; DO_PIPER=1

usage() { sed -n '3,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
while (( $# )); do
  case "$1" in
    --offline) OFFLINE_SRC="${2:-}"; shift ;;
    --offline=*) OFFLINE_SRC="${1#--offline=}" ;;
    --model) MODEL="${2:-}"; shift ;;
    --model=*) MODEL="${1#--model=}" ;;
    --whisper) WHISPER_SIZE="${2:-small}"; shift ;;
    --whisper=*) WHISPER_SIZE="${1#--whisper=}" ;;
    --voice) VOICE="${2:-$VOICE}"; shift ;;
    --workdir) WORKDIR="${2:-$WORKDIR}"; shift ;;
    --no-piper) DO_PIPER=0 ;;
    --no-whisper) DO_WHISPER=0 ;;
    --dry-run) DRY_RUN=1 ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'پرچم ناشناخته: %s (bash scripts/install_assistant.sh --help)\n' "$1" >&2; exit 2 ;;
  esac
  shift
done
WORKDIR="$(cd "$WORKDIR" 2>/dev/null && pwd || printf '%s' "$WORKDIR")"

# ---------- output (stderr), progress bar, log ----------
IND=""; [[ -n "${_NASOZ_MENU_CHILD:-}${_NASOZ_ASSIST_NESTED:-}" ]] && IND="  "
log()  { printf '%s\n' "$*" >&2; }
ok()   { printf '%s✓ %s\n' "$IND" "$*" >&2; }
warn() { printf '%s⚠ %s\n' "$IND" "$*" >&2; }
need_cmd() { command -v "$1" >/dev/null 2>&1; }
fa_digits() { printf '%s' "$1" | sed 'y/0123456789/۰۱۲۳۴۵۶۷۸۹/'; }
if [[ -t 2 ]]; then PROGRESS_TTY=1; else PROGRESS_TTY=0; fi
if (( PROGRESS_TTY )) && [[ -z "${NO_COLOR:-}" && "${TERM:-dumb}" != dumb ]]; then
  C_B=$'\033[1m'; C_G=$'\033[32m'; C_R=$'\033[31m'; C_Y=$'\033[33m'; C_0=$'\033[0m'
else
  C_B=""; C_G=""; C_R=""; C_Y=""; C_0=""
fi
if [[ -n "${_NASOZ_INSTALL_LOG:-}" ]]; then
  INSTALL_LOG="$_NASOZ_INSTALL_LOG"
else
  mkdir -p "$WORKDIR/data/logs" 2>/dev/null || true
  INSTALL_LOG="$WORKDIR/data/logs/nasoz_assistant_$(date +%Y%m%d_%H%M%S).log"
  [[ -d "$WORKDIR/data/logs" && -w "$WORKDIR/data/logs" ]] || INSTALL_LOG="${TMPDIR:-/tmp}/nasoz_assistant_$(date +%Y%m%d_%H%M%S).log"
fi
: >>"$INSTALL_LOG" 2>/dev/null || INSTALL_LOG=/dev/null
STEP_N=0; STEP_TOTAL=7
step() {
  STEP_N=$((STEP_N + 1))
  local pct=$(( STEP_N * 100 / STEP_TOTAL )) bar="" i
  for (( i = 0; i < 10; i++ )); do if (( i < pct / 10 )); then bar+="█"; else bar+="░"; fi; done
  printf '\n%s%s[دستیار %s/%s] %s٪ %s %s...%s\n' "$IND" "$C_B" "$(fa_digits "$STEP_N")" "$(fa_digits "$STEP_TOTAL")" \
    "$(fa_digits "$pct")" "$bar" "$1" "$C_0" >&2
  printf '\n===== [assistant %s/%s] %s (%s) =====\n' "$STEP_N" "$STEP_TOTAL" "$1" "$(date '+%F %T')" >>"$INSTALL_LOG" 2>/dev/null || true
}
logf() { printf '%s\n' "$*" >>"$INSTALL_LOG" 2>/dev/null || true; }
run_long() {  # run_long "توضیح" cmd… → output only to the log; spinner + elapsed on TTY
  local label="$1"; shift
  local start=$SECONDS rc=0 pid sp="|/-\\" i=0
  logf "\$ $*"
  if (( PROGRESS_TTY )); then
    "$@" >>"$INSTALL_LOG" 2>&1 </dev/null & pid=$!
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r%s  %s %s (%s ثانیه)   ' "$IND" "${sp:i++%4:1}" "$label" "$(fa_digits $((SECONDS - start)))" >&2
      sleep 0.5
    done
    wait "$pid" || rc=$?
    printf '\r\033[K' >&2
  else
    log "$IND  … $label"
    "$@" >>"$INSTALL_LOG" 2>&1 </dev/null || rc=$?
  fi
  if (( rc != 0 )); then
    printf '%s%s  ✗ %s ناموفق بود (کد %s)%s\n' "$IND" "$C_R" "$label" "$(fa_digits "$rc")" "$C_0" >&2
    tail -n 8 "$INSTALL_LOG" 2>/dev/null | sed "s/^/$IND    │ /" >&2
    return "$rc"
  fi
  printf '%s%s  ✓ %s (%s ثانیه)%s\n' "$IND" "$C_G" "$label" "$(fa_digits $((SECONDS - start)))" "$C_0" >&2
}

# ---------- environment ----------
IS_ROOT=0; [[ "$(id -u)" == 0 ]] && IS_ROOT=1
ARCH="$(uname -m)"; case "$ARCH" in x86_64|amd64) ARCH=amd64 ;; aarch64|arm64) ARCH=arm64 ;; esac
if (( IS_ROOT )); then
  PREFIX="${ASSISTANT_PREFIX:-/usr/local}"; CACHE="${ASSISTANT_CACHE:-/var/cache/nasoz-assistant}"
  PIPER_DIR="${ASSISTANT_PIPER_DIR:-/opt/piper/models}"
else
  PREFIX="${ASSISTANT_PREFIX:-$HOME/.local}"; CACHE="${ASSISTANT_CACHE:-$HOME/.cache/nasoz-assistant}"
  PIPER_DIR="${ASSISTANT_PIPER_DIR:-/opt/piper/models}"
fi
OLLAMA_URL="http://127.0.0.1:11434"
OLLAMA_SOURCES="${ASSISTANT_OLLAMA_SOURCES:-github official}"
OLLAMA_GITHUB_URL="${ASSISTANT_OLLAMA_GITHUB_URL:-https://github.com/ollama/ollama/releases/latest/download/ollama-linux-${ARCH}.tar.zst}"
OLLAMA_OFFICIAL_URL="${ASSISTANT_OLLAMA_INSTALL_URL:-https://ollama.com/install.sh}"
OLLAMA_REGISTRY="${ASSISTANT_OLLAMA_REGISTRY:-https://registry.ollama.ai}"
MODEL_SOURCES="${ASSISTANT_MODEL_SOURCES:-hf-mirror huggingface registry}"
HF_SOURCES="${ASSISTANT_HF_SOURCES:-hf-mirror huggingface}"
HF_MIRROR_URL="${ASSISTANT_HF_MIRROR:-https://hf-mirror.com}"
HF_URL="${ASSISTANT_HF_URL:-https://huggingface.co}"
PIP_INDEXES="${ASSISTANT_PIP_INDEXES:-https://pypi.org/simple}"
VENV_PY="$WORKDIR/.venv/bin/python"
export PATH="$PREFIX/bin:$PATH"

env_get() {
  local f="$WORKDIR/.env" v
  [[ -f "$f" ]] || return 0
  v="$(grep -E "^$1=" "$f" | tail -1 | cut -d= -f2- | tr -d '\r' || true)"
  v="${v#\"}"; v="${v%\"}"; printf '%s' "$v"
}
env_set() {  # upsert, keeps chmod 600
  local key="$1" value="$2" f="$WORKDIR/.env" tmp
  (( DRY_RUN )) && { log "$IND  [dry-run] .env: $key=$value"; return 0; }
  [[ -f "$f" ]] || { printf '# تولید شده توسط install.sh — این فایل را commit نکنید\n' > "$f" || return 1; }
  chmod 600 "$f"
  tmp="$(mktemp "$WORKDIR/.env.XXXXXX")" || return 1
  awk -v k="$key" -v v="$value" 'BEGIN{d=0} $0 ~ "^"k"=" { if(!d){print k"="v; d=1}; next } {print} END{ if(!d) print k"="v }' "$f" > "$tmp"
  chmod 600 "$tmp"; mv "$tmp" "$f"
}
MODEL_GIVEN=0; [[ -n "$MODEL" ]] && MODEL_GIVEN=1
[[ -n "$MODEL" ]] || MODEL="$(env_get OLLAMA_MODEL)"
[[ -n "$MODEL" ]] || MODEL="qwen2.5:3b"
have_systemd() { [[ -d /run/systemd/system ]] && need_cmd systemctl; }

# user that runs the bot (owner of the HF cache Whisper reads)
bot_user() {
  local u=""
  [[ -f /etc/systemd/system/nasoz-bot.service ]] && u="$(sed -n 's/^User=//p' /etc/systemd/system/nasoz-bot.service | head -n1)"
  [[ -n "$u" ]] || u="$(stat -c %U "$WORKDIR" 2>/dev/null || id -un)"
  [[ "$u" == UNKNOWN ]] && u="$(id -un)"
  printf '%s' "$u"
}
BOT_USER="$(bot_user)"
as_user() {  # as_user USER cmd… → root runs it as USER (venv / sqlite / HF cache keep their owner)
  local u="$1"; shift
  if (( IS_ROOT )) && [[ -n "$u" && "$u" != root ]] && need_cmd runuser; then runuser -u "$u" -- "$@"; else "$@"; fi
}
VENV_OWNER="$(stat -c %U "$WORKDIR/.venv" 2>/dev/null || id -un)"
BOT_HOME="$(getent passwd "$BOT_USER" 2>/dev/null | cut -d: -f6)"; [[ -n "$BOT_HOME" ]] || BOT_HOME="$HOME"
HF_HOME_DIR="${ASSISTANT_HF_HOME:-$BOT_HOME/.cache/huggingface}"

# ---------- network helpers ----------
CURL_NET=(--connect-timeout 15 --retry 4 --retry-delay 5 --speed-limit 4096 --speed-time 90)
probe() {  # probe URL → "CODE FINAL_HOST" (1-byte ranged GET after redirects; 000 = blocked/timeout)
  local r
  r="$(curl -sS -L -o /dev/null -r 0-0 -w '%{http_code} %{url_effective}' --connect-timeout 8 --max-time 25 "$1" 2>/dev/null)" || true
  [[ -n "$r" ]] || r="000 -"
  local code="${r%% *}" host="${r#* }"; host="${host#*://}"; host="${host%%/*}"
  logf "probe $1 → $code ($host)"
  printf '%s %s' "$code" "$host"
}
probe_ok() { local c; c="$(probe "$1")"; [[ "${c%% *}" =~ ^(200|206)$ ]]; }
remote_size() {  # bytes after redirects, or empty
  curl -sSIL --connect-timeout 8 --max-time 30 "$1" 2>/dev/null | tr -d '\r' \
    | awk 'tolower($1)=="content-length:"{v=$2} tolower($1)=="x-linked-size:"{x=$2} END{print (x!=""?x:v)}'
}
human_mb() { awk -v b="${1:-0}" 'BEGIN{ if (b>=1073741824) printf "%.2fGB", b/1073741824; else if (b>=1048576) printf "%.0fMB", b/1048576; else printf "%.0fKB", b/1024 }'; }
ZSTD=""; for _z in $(type -ap zstd 2>/dev/null); do if "$_z" -V >/dev/null 2>&1; then ZSTD="$_z"; break; fi; done   # skip broken copies on PATH
download() {  # download URL DEST "label" → resumable, progress bar with size/percent/speed
  local url="$1" dest="$2" label="$3" part="$2.part" total pid rc=0 start=$SECONDS cur pct bar i el spd
  mkdir -p "$(dirname "$dest")" || return 1
  if [[ -s "$dest" ]]; then log "$IND  ✓ $label از قبل دانلود شده است ($(human_mb "$(stat -c %s "$dest")"))"; return 0; fi
  total="$(remote_size "$url")"; [[ "$total" =~ ^[0-9]+$ ]] || total=0
  logf "download $url → $dest (size ${total:-?})"
  curl -fL -sS "${CURL_NET[@]}" -C - -o "$part" "$url" >>"$INSTALL_LOG" 2>&1 </dev/null & pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    if (( PROGRESS_TTY )); then
      cur="$(stat -c %s "$part" 2>/dev/null || echo 0)"; el=$(( SECONDS - start )); (( el > 0 )) || el=1
      spd="$(human_mb $(( cur / el )))/s"
      if (( total > 0 )); then
        pct=$(( cur * 100 / total )); bar=""
        for (( i = 0; i < 20; i++ )); do if (( i < pct / 5 )); then bar+="█"; else bar+="░"; fi; done
        printf '\r%s  ⇣ %s %s %s٪ %s/%s %s   ' "$IND" "$label" "$bar" "$(fa_digits "$pct")" "$(human_mb "$cur")" "$(human_mb "$total")" "$spd" >&2
      else
        printf '\r%s  ⇣ %s %s %s   ' "$IND" "$label" "$(human_mb "$cur")" "$spd" >&2
      fi
    fi
    sleep 1
  done
  wait "$pid" || rc=$?
  (( PROGRESS_TTY )) && printf '\r\033[K' >&2
  if (( rc != 0 )); then
    printf '%s%s  ✗ دانلود %s ناموفق بود (کد curl %s)%s\n' "$IND" "$C_R" "$label" "$rc" "$C_0" >&2
    logf "download failed rc=$rc ($url)"
    return 1
  fi
  mv -f "$part" "$dest" || return 1
  printf '%s%s  ✓ %s دانلود شد (%s، %s ثانیه)%s\n' "$IND" "$C_G" "$label" "$(human_mb "$(stat -c %s "$dest")")" "$(fa_digits $((SECONDS - start)))" "$C_0" >&2
}
hf_base() {  # hf_base SOURCE → endpoint URL
  case "$1" in
    hf-mirror) printf '%s' "$HF_MIRROR_URL" ;;
    huggingface|hf) printf '%s' "$HF_URL" ;;
    url:*) printf '%s' "${1#url:}" ;;
    *) printf '%s' "$1" ;;
  esac
}

# ---------- offline bundle ----------
BUNDLE=""
src_desc() { if [[ -n "$BUNDLE" ]]; then printf 'بسته آفلاین'; else printf '%s' "$1"; fi; }
open_bundle() {
  local src="$1" x
  if (( DRY_RUN )); then
    [[ -e "$src" ]] || { warn "بسته دستیار پیدا نشد: $src"; return 1; }
    log "$IND  [dry-run] بسته آفلاین دستیار: $src (باز نمی شود)"; BUNDLE="$src"; return 0
  fi
  if [[ -d "$src" ]]; then BUNDLE="$(cd "$src" && pwd)"
  elif [[ -f "$src" ]]; then
    x="$CACHE/offline"; rm -rf "$x"; mkdir -p "$x" || return 1
    local need avail; need="$(stat -c %s "$src")"; avail="$(df -PB1 "$x" | awk 'NR==2{print $4}')"
    if [[ "$avail" =~ ^[0-9]+$ ]] && (( avail < need * 2 )); then
      warn "فضای دیسک برای باز کردن بسته کم است: آزاد $(human_mb "$avail")، لازم حدود $(human_mb $((need * 2)))"
    fi
    run_long "باز کردن بسته دستیار ($(human_mb "$need"))" tar -xf "$src" -C "$x" || return 1
    BUNDLE="$(find "$x" -mindepth 1 -maxdepth 1 -type d | head -n1)"
  else
    warn "بسته دستیار پیدا نشد: $src"; return 1
  fi
  [[ -n "$BUNDLE" && -f "$BUNDLE/MANIFEST" ]] || { warn "بسته دستیار نامعتبر است (MANIFEST ندارد): $src"; return 1; }
  if [[ -f "$BUNDLE/SHA256SUMS" ]]; then
    run_long "بررسی sha256 فایل های بسته" bash -c 'cd "$1" && sha256sum -c --quiet SHA256SUMS' _ "$BUNDLE" \
      || { warn "SHA256SUMS بسته دستیار مطابقت ندارد؛ فایل خراب است."; return 1; }
  fi
  local m; m="$(sed -n 's/^MODEL=//p' "$BUNDLE/MANIFEST" | head -n1)"
  if [[ -n "$m" && "$m" != "$MODEL" ]]; then
    if (( MODEL_GIVEN )); then warn "بسته آفلاین مدل $m دارد؛ همین مدل به جای $MODEL نصب می شود."; else log "$IND  مدل بسته: $m"; fi
    MODEL="$m"
  fi
  ok "بسته آفلاین دستیار: $BUNDLE"
}

# ---------- 1) prerequisites ----------
apt_install() {
  (( IS_ROOT )) && need_cmd apt-get || return 1
  run_long "نصب $* با apt" env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$@" \
    || { run_long "apt-get update" apt-get update -q && run_long "نصب $* با apt" env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$@"; }
}
prereqs() {
  local mem_kb disk
  need_cmd curl || apt_install curl || { FAIL_REASON="curl نصب نیست"; return 1; }
  if [[ -z "$BUNDLE" && -z "$ZSTD" ]]; then
    if apt_install zstd; then ZSTD="$(command -v zstd)"; else warn "zstd نصب نشد؛ باز کردن tar.zst رسمی Ollama ممکن نیست"; fi
  fi
  [[ -x "$VENV_PY" ]] || warn "محیط پایتون برنامه پیدا نشد ($VENV_PY)؛ ابتدا ربات را نصب کنید."
  mem_kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  disk="$(df -PB1 "$(dirname "$CACHE")" 2>/dev/null | awk 'NR==2{print $4}')"
  log "$IND  معماری: $ARCH | حافظه: $(human_mb $((mem_kb * 1024))) | فضای آزاد: $(human_mb "${disk:-0}") | کاربر ربات: $BOT_USER"
  if (( mem_kb > 0 && mem_kb < 3500000 )) && [[ "$MODEL" == qwen2.5:3b ]]; then
    warn "حافظه کمتر از ۴ گیگ است؛ مدل qwen2.5:3b کند است. مدل سبک تر: --model qwen2.5:1.5b"
  fi
  if [[ "${disk:-0}" =~ ^[0-9]+$ ]] && (( disk > 0 && disk < 6000000000 )); then
    warn "فضای دیسک کمتر از ۶ گیگ است؛ دانلود مدل ممکن است کامل نشود."
  fi
  [[ "$ARCH" == amd64 || "$ARCH" == arm64 ]] || { FAIL_REASON="معماری $ARCH پشتیبانی نمی شود"; return 1; }
  return 0
}

# ---------- 2) Ollama binary ----------
install_ollama_tree() {  # install_ollama_tree DIR(with bin/ollama, lib/ollama)
  local src="$1" keep="${ASSISTANT_KEEP_GPU:-0}" d
  [[ -x "$src/bin/ollama" ]] || { warn "فایل bin/ollama در بسته نیست"; return 1; }
  mkdir -p "$PREFIX/bin" "$PREFIX/lib" || return 1
  rm -rf "$PREFIX/lib/ollama"; mkdir -p "$PREFIX/lib/ollama"
  install -m 755 "$src/bin/ollama" "$PREFIX/bin/ollama" || return 1
  if [[ -d "$src/lib/ollama" ]]; then
    cp -a "$src/lib/ollama/." "$PREFIX/lib/ollama/" || return 1
    if [[ "$keep" != 1 ]]; then
      for d in "$PREFIX"/lib/ollama/*/; do
        case "$(basename "$d")" in cuda*|rocm*|vulkan*|mlx*) rm -rf "$d" ;; esac
      done
    fi
  fi
  logf "ollama installed: $("$PREFIX/bin/ollama" --version 2>&1 | tail -n1)"
}
ollama_from_github() {
  local url="${1:-$OLLAMA_GITHUB_URL}" f x code
  code="$(probe "$url")"; log "$IND  منبع GitHub: HTTP ${code%% *} (${code#* })"
  [[ "${code%% *}" =~ ^(200|206)$ ]] || return 1
  f="$CACHE/$(basename "${url%%\?*}")"
  download "$url" "$f" "Ollama ($(basename "$f"))" || return 1
  x="$CACHE/ollama_x"; rm -rf "$x"; mkdir -p "$x"
  case "$f" in
    *.tar.zst|*.tzst)
      [[ -n "$ZSTD" ]] || { warn "برای باز کردن $f برنامه zstd لازم است (apt install zstd)"; return 1; }
      run_long "باز کردن Ollama" bash -c '"$3" -dc "$1" | tar -x -C "$2" --exclude="lib/ollama/cuda_*" --exclude="lib/ollama/rocm*" --exclude="lib/ollama/vulkan*" --exclude="lib/ollama/mlx*"' _ "$f" "$x" "$ZSTD" || return 1 ;;
    *) run_long "باز کردن Ollama" tar -xzf "$f" -C "$x" || return 1 ;;
  esac
  [[ "${ASSISTANT_KEEP_GPU:-0}" == 1 ]] && { rm -rf "$x"; mkdir -p "$x"; run_long "باز کردن Ollama (با GPU)" bash -c '"$3" -dc "$1" | tar -x -C "$2"' _ "$f" "$x" "$ZSTD" || return 1; }
  install_ollama_tree "$x" || return 1
  rm -rf "$x"
  [[ "${ASSISTANT_KEEP_CACHE:-0}" == 1 ]] || rm -f "$f"
}
ollama_official() {
  local code tmp
  code="$(probe "$OLLAMA_OFFICIAL_URL")"; log "$IND  منبع ollama.com: HTTP ${code%% *}"
  [[ "${code%% *}" =~ ^(200|206)$ ]] || return 1
  tmp="$(mktemp)"; curl -fsSL --connect-timeout 10 --max-time 120 -o "$tmp" "$OLLAMA_OFFICIAL_URL" || { rm -f "$tmp"; return 1; }
  run_long "نصب کننده رسمی Ollama" timeout 3600 sh "$tmp"; local rc=$?; rm -f "$tmp"; return "$rc"
}
install_ollama() {
  local s
  if need_cmd ollama; then ok "Ollama از قبل نصب است: $(command -v ollama) ($(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | tail -n1))"; return 0; fi
  if (( DRY_RUN )); then log "$IND  [dry-run] منابع Ollama به ترتیب: $(src_desc "$OLLAMA_SOURCES")"; return 0; fi
  if [[ -n "$BUNDLE" ]]; then
    install_ollama_tree "$BUNDLE/ollama" && { ok "Ollama از بسته آفلاین نصب شد ($PREFIX/bin/ollama)"; return 0; }
    FAIL_REASON="نصب Ollama از بسته آفلاین ناموفق بود"; return 1
  fi
  for s in $OLLAMA_SOURCES; do
    case "$s" in
      github) ollama_from_github && { ok "Ollama از GitHub نصب شد ($PREFIX/bin/ollama)"; return 0; } ;;
      official) ollama_official && need_cmd ollama && { ok "Ollama با نصب کننده رسمی نصب شد"; return 0; } ;;
      url:*) ollama_from_github "${s#url:}" && { ok "Ollama از ${s#url:} نصب شد"; return 0; } ;;
      *) warn "منبع ناشناخته Ollama: $s" ;;
    esac
    warn "منبع $s برای Ollama جواب نداد؛ منبع بعدی..."
  done
  FAIL_REASON="باینری Ollama از هیچ منبعی دریافت نشد ($OLLAMA_SOURCES)"; return 1
}

# ---------- 3) Ollama service ----------
ollama_up() { curl -fsS --max-time 3 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; }
wait_ollama() { local i; for i in $(seq 1 "${1:-30}"); do ollama_up && return 0; sleep 1; done; return 1; }
write_unit() {
  cat > /etc/systemd/system/ollama.service <<UNIT
[Unit]
Description=Ollama (local LLM for nasoz assistant)
After=network-online.target

[Service]
ExecStart=$PREFIX/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="PATH=$PREFIX/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

[Install]
WantedBy=multi-user.target
UNIT
}
start_ollama() {
  if (( DRY_RUN )); then log "$IND  [dry-run] کاربر ollama + /etc/systemd/system/ollama.service (127.0.0.1:11434)"; return 0; fi
  if ollama_up; then ok "Ollama روی $OLLAMA_URL پاسخ می دهد"; return 0; fi
  if (( IS_ROOT )); then
    if ! id ollama >/dev/null 2>&1; then
      useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama 2>>"$INSTALL_LOG" || warn "ساخت کاربر ollama ناموفق بود"
    fi
    if have_systemd; then
      write_unit && systemctl daemon-reload && systemctl enable --now ollama >>"$INSTALL_LOG" 2>&1
      if wait_ollama 40; then ok "سرویس systemd ollama فعال شد (برای بوت هم فعال است)"; return 0; fi
      warn "سرویس systemd ollama بالا نیامد (journalctl -u ollama)؛ اجرای مستقیم..."
    fi
    if id ollama >/dev/null 2>&1 && need_cmd runuser; then
      nohup runuser -u ollama -- env HOME=/usr/share/ollama OLLAMA_HOST=127.0.0.1:11434 "$PREFIX/bin/ollama" serve \
        >>/var/log/nasoz-ollama.log 2>&1 </dev/null &
      echo $! > /run/nasoz-ollama.pid 2>/dev/null || true
    else
      nohup env OLLAMA_HOST=127.0.0.1:11434 "$PREFIX/bin/ollama" serve >>/var/log/nasoz-ollama.log 2>&1 </dev/null &
    fi
  else
    nohup env OLLAMA_HOST=127.0.0.1:11434 "$(command -v ollama)" serve >>"$CACHE/ollama-serve.log" 2>&1 </dev/null &
  fi
  if wait_ollama 40; then ok "Ollama با nohup اجرا شد (بعد از ریبوت: ollama serve را دوباره اجرا کنید)"; return 0; fi
  FAIL_REASON="سرویس Ollama بالا نیامد"; return 1
}

# ---------- 4) language model ----------
gguf_map() {  # gguf_map MODEL → "repo file"
  if [[ -n "${ASSISTANT_GGUF_REPO:-}" && -n "${ASSISTANT_GGUF_FILE:-}" ]]; then printf '%s %s' "$ASSISTANT_GGUF_REPO" "$ASSISTANT_GGUF_FILE"; return 0; fi
  case "$1" in
    qwen2.5:3b|qwen2.5:3b-instruct) printf 'Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q4_k_m.gguf' ;;
    qwen2.5:1.5b|qwen2.5:1.5b-instruct) printf 'Qwen/Qwen2.5-1.5B-Instruct-GGUF qwen2.5-1.5b-instruct-q4_k_m.gguf' ;;
    qwen2.5:0.5b|qwen2.5:0.5b-instruct) printf 'Qwen/Qwen2.5-0.5B-Instruct-GGUF qwen2.5-0.5b-instruct-q4_k_m.gguf' ;;
    *) return 1 ;;
  esac
}
write_modelfile() {  # write_modelfile DIR GGUF_NAME  (ChatML template for Qwen2.5)
  cat > "$1/Modelfile" <<MF
FROM ./$2
TEMPLATE """{{- if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{- range .Messages }}<|im_start|>{{ .Role }}
{{ .Content }}<|im_end|>
{{ end }}<|im_start|>assistant
"""
SYSTEM """تو یک دستیار مفید هستی و به فارسی پاسخ می دهی."""
PARAMETER stop "<|im_start|>"
PARAMETER stop "<|im_end|>"
PARAMETER temperature 0.6
PARAMETER num_ctx 4096
MF
}
model_present() { ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx -e "$1" -e "$1:latest"; }
import_gguf() {  # import_gguf DIR (Modelfile + gguf)
  local dir="$1" g
  g="$(sed -n 's#^FROM \./##p' "$dir/Modelfile" | head -n1)"
  [[ -s "$dir/$g" ]] || { warn "فایل GGUF پیدا نشد: $dir/$g"; return 1; }
  [[ "$(head -c 4 "$dir/$g")" == GGUF ]] || { warn "فایل $g یک GGUF معتبر نیست (شاید صفحه خطا دانلود شده)"; rm -f "$dir/$g"; return 1; }
  run_long "ساخت مدل $MODEL در Ollama از GGUF" timeout 3600 ollama create "$MODEL" -f "$dir/Modelfile"
}
model_from_hf() {  # model_from_hf SOURCE
  local base map repo file dir code
  base="$(hf_base "$1")"
  map="$(gguf_map "$MODEL")" || { warn "برای مدل $MODEL فایل GGUF تعریف نشده (ASSISTANT_GGUF_REPO و ASSISTANT_GGUF_FILE را بدهید)"; return 1; }
  repo="${map% *}"; file="${map#* }"
  local url="$base/$repo/resolve/main/$file"
  code="$(probe "$url")"; log "$IND  منبع $1: HTTP ${code%% *} (${code#* })"
  [[ "${code%% *}" =~ ^(200|206)$ ]] || return 1
  dir="$CACHE/model"; mkdir -p "$dir"
  download "$url" "$dir/$file" "مدل $MODEL ($file)" || return 1
  write_modelfile "$dir" "$file"
  import_gguf "$dir" || return 1
  [[ "${ASSISTANT_KEEP_CACHE:-0}" == 1 ]] || rm -f "$dir/$file"
}
model_from_registry() {
  local name="${MODEL%%:*}" tag="latest" code
  [[ "$MODEL" == *:* ]] && tag="${MODEL#*:}"
  code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 8 --max-time 25 \
    -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' "$OLLAMA_REGISTRY/v2/library/$name/manifests/$tag" 2>/dev/null)" || true
  log "$IND  منبع registry.ollama.ai: HTTP ${code:-000}"; logf "registry manifest $name:$tag → ${code:-000}"
  [[ "$code" == 200 ]] || return 1
  run_long "دریافت مدل $MODEL از registry (ollama pull)" timeout "${ASSISTANT_PULL_TIMEOUT:-5400}" ollama pull "$MODEL"
}
install_model() {
  local s
  if (( DRY_RUN )); then log "$IND  [dry-run] مدل $MODEL؛ منابع به ترتیب: $(src_desc "$MODEL_SOURCES")"; return 0; fi
  if model_present "$MODEL"; then ok "مدل $MODEL از قبل در Ollama هست"; return 0; fi
  if [[ -n "$BUNDLE" ]]; then
    import_gguf "$BUNDLE/model" && { ok "مدل $MODEL از بسته آفلاین ساخته شد"; return 0; }
    FAIL_REASON="ساخت مدل از بسته آفلاین ناموفق بود"; return 1
  fi
  for s in $MODEL_SOURCES; do
    case "$s" in
      registry) model_from_registry && model_present "$MODEL" && { ok "مدل $MODEL از registry دریافت شد"; return 0; } ;;
      *) model_from_hf "$s" && model_present "$MODEL" && { ok "مدل $MODEL از $s دریافت و ساخته شد"; return 0; } ;;
    esac
    warn "منبع $s برای مدل جواب نداد؛ منبع بعدی..."
  done
  FAIL_REASON="مدل $MODEL از هیچ منبعی دریافت نشد ($MODEL_SOURCES)"; return 1
}
verify_model() {
  [[ "${ASSISTANT_VERIFY:-1}" == 1 ]] || return 0
  local r
  r="$(curl -sS --max-time 300 "$OLLAMA_URL/api/generate" -d "{\"model\":\"$MODEL\",\"prompt\":\"سلام\",\"stream\":false,\"options\":{\"num_predict\":12}}" 2>/dev/null)" || true
  logf "verify generate: ${r:0:300}"
  if [[ "$r" == *'"response"'* ]]; then ok "مدل پاسخ داد (تست سلامت مدل)"; else warn "مدل نصب شد ولی تست پاسخ ناموفق بود (حافظه کم یا بار زیاد؟)"; fi
}

# ---------- 5) Python packages ----------
pip_install() {
  local pkgs=(faster-whisper "av<19" requests) idx   # av 19 dropped metadata_errors= used by faster-whisper 1.2
  (( DO_PIPER )) && pkgs+=(piper-tts)
  if (( DRY_RUN )); then log "$IND  [dry-run] pip install ${pkgs[*]} (منبع: $(src_desc "$PIP_INDEXES"))"; return 0; fi
  [[ -x "$VENV_PY" ]] || { FAIL_REASON="محیط پایتون برنامه ($VENV_PY) وجود ندارد"; return 1; }
  if [[ -n "$BUNDLE" ]]; then
    run_long "نصب ${pkgs[*]} از wheels بسته" as_user "$VENV_OWNER" "$VENV_PY" -m pip install --no-index --find-links "$BUNDLE/wheels" "${pkgs[@]}" \
      && { ok "بسته های پایتون دستیار نصب شدند"; return 0; }
    FAIL_REASON="نصب بسته های پایتون از wheels بسته ناموفق بود"; return 1
  fi
  for idx in $PIP_INDEXES; do
    run_long "نصب ${pkgs[*]} از $idx" as_user "$VENV_OWNER" timeout 2400 "$VENV_PY" -m pip install --disable-pip-version-check -i "$idx" "${pkgs[@]}" \
      && { ok "بسته های پایتون دستیار نصب شدند"; return 0; }
  done
  FAIL_REASON="نصب faster-whisper از PyPI ناموفق بود"; return 1
}
link_piper() {
  (( DO_PIPER && ! DRY_RUN )) || return 0
  [[ -x "$WORKDIR/.venv/bin/piper" ]] || return 0
  if (( IS_ROOT )); then ln -sf "$WORKDIR/.venv/bin/piper" /usr/local/bin/piper && log "$IND  piper → /usr/local/bin/piper"
  else mkdir -p "$HOME/.local/bin" && ln -sf "$WORKDIR/.venv/bin/piper" "$HOME/.local/bin/piper"; fi
}

# ---------- 6) Whisper model (HF cache layout: models--Systran--faster-whisper-<size>) ----------
WHISPER_FILES=(config.json model.bin tokenizer.json vocabulary.txt)
fix_owner() { (( IS_ROOT )) && [[ "$BOT_USER" != root ]] && chown -R "$BOT_USER": "$@" 2>/dev/null; return 0; }
whisper_from_hf() {
  local base repo="Systran/faster-whisper-$WHISPER_SIZE" rev d f code
  base="$(hf_base "$1")"
  code="$(probe "$base/$repo/resolve/main/config.json")"; log "$IND  منبع $1: HTTP ${code%% *} (${code#* })"
  [[ "${code%% *}" =~ ^(200|206)$ ]] || return 1
  rev="$(curl -fsS --max-time 20 "$base/api/models/$repo" 2>/dev/null | grep -oE '"sha":"[0-9a-f]{40}"' | head -n1 | cut -d'"' -f4)" || true
  [[ -n "$rev" ]] || rev="nasoz-local"
  d="$HF_HOME_DIR/hub/models--Systran--faster-whisper-$WHISPER_SIZE"
  mkdir -p "$d/refs" "$d/snapshots/$rev" || return 1
  for f in "${WHISPER_FILES[@]}"; do
    download "$base/$repo/resolve/main/$f" "$d/snapshots/$rev/$f" "Whisper $WHISPER_SIZE: $f" || return 1
  done
  printf '%s' "$rev" > "$d/refs/main"
  fix_owner "$HF_HOME_DIR"
}
whisper_check() {
  [[ "${ASSISTANT_VERIFY:-1}" == 1 ]] || return 0
  run_long "تست بارگذاری Whisper $WHISPER_SIZE (آفلاین)" as_user "$BOT_USER" env HF_HOME="$HF_HOME_DIR" HF_HUB_OFFLINE=1 timeout 600 \
    "$VENV_PY" -c "from faster_whisper import WhisperModel; WhisperModel('$WHISPER_SIZE', device='cpu', compute_type='int8'); print('whisper ok')"
}
install_whisper() {
  local s
  (( DO_WHISPER )) || { log "$IND  Whisper رد شد (--no-whisper)"; return 0; }
  if (( DRY_RUN )); then log "$IND  [dry-run] مدل Systran/faster-whisper-$WHISPER_SIZE در $HF_HOME_DIR؛ منابع: $(src_desc "$HF_SOURCES")"; return 0; fi
  if [[ -n "$BUNDLE" ]]; then
    mkdir -p "$HF_HOME_DIR/hub" && cp -a "$BUNDLE/whisper/." "$HF_HOME_DIR/hub/" && fix_owner "$HF_HOME_DIR" \
      && whisper_check && { ok "مدل Whisper $WHISPER_SIZE از بسته آفلاین نصب شد"; return 0; }
    FAIL_REASON="نصب مدل Whisper از بسته آفلاین ناموفق بود"; return 1
  fi
  for s in $HF_SOURCES; do
    whisper_from_hf "$s" && whisper_check && { ok "مدل Whisper $WHISPER_SIZE از $s نصب شد"; return 0; }
    warn "منبع $s برای Whisper جواب نداد؛ منبع بعدی..."
  done
  FAIL_REASON="مدل Whisper از هیچ منبعی دریافت نشد ($HF_SOURCES)"; return 1
}

# ---------- 7) Piper voice ----------
voice_path() {  # fa_IR-amir-medium → fa/fa_IR/amir/medium/fa_IR-amir-medium
  local lang name q
  IFS=- read -r lang name q <<<"$1"
  printf '%s/%s/%s/%s/%s' "${lang%%_*}" "$lang" "$name" "$q" "$1"
}
piper_alias() {  # the bot looks for fa_IR-donya-medium
  [[ "$VOICE" == "$BOT_VOICE" || -e "$PIPER_DIR/$BOT_VOICE.onnx" ]] && return 0
  ln -sf "$VOICE.onnx" "$PIPER_DIR/$BOT_VOICE.onnx"; ln -sf "$VOICE.onnx.json" "$PIPER_DIR/$BOT_VOICE.onnx.json"
}
piper_check() {
  [[ "${ASSISTANT_VERIFY:-1}" == 1 ]] || return 0
  local p="$WORKDIR/.venv/bin/piper" out="$CACHE/piper_test.wav"
  [[ -x "$p" ]] || return 0
  rm -f "$out"
  printf 'سلام' | timeout 300 "$p" --model "$PIPER_DIR/$VOICE.onnx" --output_file "$out" >>"$INSTALL_LOG" 2>&1
  if [[ -s "$out" ]]; then ok "تست صدای Piper موفق بود"; rm -f "$out"; else warn "Piper نصب شد ولی تست صدا ناموفق بود (جزئیات در لاگ)"; fi
}
install_piper() {
  local s base vp
  (( DO_PIPER )) || { log "$IND  Piper رد شد (--no-piper)"; return 0; }
  if (( DRY_RUN )); then log "$IND  [dry-run] صدای $VOICE در $PIPER_DIR؛ منابع: $(src_desc "$HF_SOURCES")"; return 0; fi
  mkdir -p "$PIPER_DIR" 2>/dev/null || { FAIL_REASON="ساخت $PIPER_DIR ممکن نیست (با sudo اجرا کنید)"; return 1; }
  if [[ -n "$BUNDLE" ]]; then
    cp -a "$BUNDLE/piper/." "$PIPER_DIR/" && piper_alias && { ok "صدای فارسی Piper از بسته نصب شد"; piper_check; return 0; }
    FAIL_REASON="نصب صدای Piper از بسته ناموفق بود"; return 1
  fi
  vp="$(voice_path "$VOICE")"
  for s in $HF_SOURCES; do
    base="$(hf_base "$s")/rhasspy/piper-voices/resolve/main/$vp"
    local code; code="$(probe "$base.onnx.json")"; log "$IND  منبع $s: HTTP ${code%% *} (${code#* })"
    if [[ "${code%% *}" =~ ^(200|206)$ ]] && download "$base.onnx" "$PIPER_DIR/$VOICE.onnx" "صدای $VOICE" \
       && download "$base.onnx.json" "$PIPER_DIR/$VOICE.onnx.json" "تنظیمات صدای $VOICE"; then
      piper_alias; ok "صدای فارسی Piper نصب شد ($PIPER_DIR)"; piper_check; return 0
    fi
    warn "منبع $s برای Piper جواب نداد؛ منبع بعدی..."
  done
  FAIL_REASON="صدای Piper دریافت نشد"; return 1
}

# ---------- 8) .env + bot setting ----------
write_config() {
  local own; own="$(stat -c %u:%g "$WORKDIR/.env" 2>/dev/null || true)"
  env_set OLLAMA_BASE_URL "$OLLAMA_URL"; env_set OLLAMA_MODEL "$MODEL"; env_set OLLAMA_TIMEOUT 120
  [[ "${R[whisper]:-}" == ok ]] && env_set HF_HUB_OFFLINE 1   # model is local; never wait for blocked HF at runtime
  (( DRY_RUN )) && return 0
  [[ -n "$own" ]] && (( IS_ROOT )) && chown "$own" "$WORKDIR/.env" 2>/dev/null   # keep the original owner
  if [[ -x "$VENV_PY" && -f "$WORKDIR/scripts/menu_helper.py" ]]; then
    if (cd "$WORKDIR" && as_user "$(stat -c %U "$WORKDIR/data" 2>/dev/null || echo "$BOT_USER")" "$VENV_PY" scripts/menu_helper.py assistant-model "$MODEL" >>"$INSTALL_LOG" 2>&1); then
      log "$IND  مدل ربات (تنظیمات دستیار): $MODEL"
    else logf "assistant-model setting skipped"; fi
  fi
  return 0
}

# ---------- --check: reachability of every source ----------
check_sources() {
  local u s map
  log "بررسی دسترسی منابع دستیار (کد HTTP بعد از ریدایرکت؛ ۰۰۰ یعنی مسدود یا timeout):"
  map="$(gguf_map "$MODEL" || echo "- -")"
  for u in "$OLLAMA_GITHUB_URL" "$OLLAMA_OFFICIAL_URL" "$OLLAMA_REGISTRY/v2/library/${MODEL%%:*}/manifests/${MODEL#*:}"; do
    s="$(probe "$u")"; printf '  %-4s %-28s %s\n' "${s%% *}" "${s#* }" "$u" >&2
  done
  for s in $HF_SOURCES; do
    for u in "$(hf_base "$s")/${map% *}/resolve/main/${map#* }" "$(hf_base "$s")/Systran/faster-whisper-$WHISPER_SIZE/resolve/main/model.bin" \
             "$(hf_base "$s")/rhasspy/piper-voices/resolve/main/$(voice_path "$VOICE").onnx"; do
      local c; c="$(probe "$u")"; printf '  %-4s %-28s %s\n' "${c%% *}" "${c#* }" "$u" >&2
    done
  done
  for u in $PIP_INDEXES; do s="$(probe "$u/faster-whisper/")"; printf '  %-4s %-28s %s\n' "${s%% *}" "${s#* }" "$u" >&2; done
}

# ========== main ==========
declare -A R=()
FAIL_REASON=""
part() {  # part KEY "label" func → runs func in a subshell-safe way, records ok/fail, never aborts
  local key="$1" label="$2" fn="$3"
  FAIL_REASON=""
  if "$fn"; then R[$key]=ok; else
    R[$key]=fail
    printf '%s%s  ⚠ %s ناموفق بود: %s%s\n' "$IND" "$C_Y" "$label" "${FAIL_REASON:-جزئیات در لاگ}" "$C_0" >&2
    logf "ASSISTANT PART FAILED ($key): ${FAIL_REASON:-?}"
  fi
}
if (( CHECK_ONLY )); then check_sources; exit 0; fi
# helpers for scripts/build_assistant_bundle.sh (single source of truth for the GGUF map and the Modelfile)
case "${_NASOZ_ASSIST_HELPER:-}" in
  gguf) if gguf_map "$MODEL"; then echo; exit 0; fi; exit 1 ;;
  modelfile) write_modelfile "$_NASOZ_HELPER_DIR" "$_NASOZ_HELPER_FILE"; exit $? ;;
  voicepath) voice_path "$VOICE"; echo; exit 0 ;;
esac

log "${IND}━━━━━━━━ نصب دستیار هوشمند صوتی (اختیاری) ━━━━━━━━"
(( IS_ROOT )) || warn "با کاربر root اجرا نمی شود؛ Ollama در $PREFIX نصب می شود و سرویس systemd ساخته نمی شود."
mkdir -p "$CACHE" 2>/dev/null || { CACHE="${TMPDIR:-/tmp}/nasoz-assistant"; mkdir -p "$CACHE"; }
step "پیش نیازها"
if [[ -n "$OFFLINE_SRC" ]]; then open_bundle "$OFFLINE_SRC" || { warn "بسته آفلاین قابل استفاده نیست؛ نصب دستیار انجام نشد."; exit 3; }; fi
log "${IND}مدل: $MODEL | Whisper: $WHISPER_SIZE | صدا: $([[ $DO_PIPER == 1 ]] && echo "$VOICE" || echo '-') | مسیر: $WORKDIR"
log "${IND}لاگ: $INSTALL_LOG"
part prereq "پیش نیازها" prereqs
step "باینری Ollama"
part ollama "نصب Ollama" install_ollama
step "سرویس Ollama"
if [[ "${R[ollama]}" == ok ]]; then part service "سرویس Ollama" start_ollama; else R[service]=skip; log "$IND  رد شد: Ollama نصب نیست"; fi
step "مدل زبانی $MODEL"
if [[ "${R[service]}" == ok ]]; then part model "مدل زبانی" install_model; else R[model]=skip; log "$IND  رد شد: سرویس Ollama آماده نیست"; fi
[[ "${R[model]}" == ok ]] && (( ! DRY_RUN )) && verify_model
step "بسته های پایتون (faster-whisper$([[ $DO_PIPER == 1 ]] && echo '، piper-tts'))"
part pip "بسته های پایتون" pip_install
[[ "${R[pip]}" == ok ]] && link_piper
step "مدل گفتار به متن Whisper ($WHISPER_SIZE)"
if [[ "${R[pip]}" == ok ]]; then part whisper "Whisper" install_whisper; else R[whisper]=skip; log "$IND  رد شد: faster-whisper نصب نیست"; fi
step "صدای فارسی Piper (اختیاری)"
if [[ "${R[pip]}" == ok ]]; then part piper "Piper" install_piper; else R[piper]=skip; log "$IND  رد شد: piper-tts نصب نیست"; fi
(( DO_WHISPER )) || R[whisper]=skip
(( DO_PIPER )) || R[piper]=skip
write_config
if [[ -n "$BUNDLE" && "$BUNDLE" == "$CACHE/offline/"* && "${ASSISTANT_KEEP_CACHE:-0}" != 1 ]]; then rm -rf "$CACHE/offline"; fi

mark() { case "${R[$1]:-skip}" in ok) printf '✓' ;; fail) printf '✗' ;; *) printf '–' ;; esac; }
log ""
log "${IND}خلاصه دستیار هوشمند:"
log "${IND}  $(mark ollama) Ollama          $(command -v ollama 2>/dev/null || echo '-')"
log "${IND}  $(mark service) سرویس Ollama    $OLLAMA_URL"
log "${IND}  $(mark model) مدل زبانی       $MODEL"
log "${IND}  $(mark pip) پایتون          faster-whisper, requests$([[ $DO_PIPER == 1 ]] && echo ', piper-tts')"
log "${IND}  $(mark whisper) Whisper         $WHISPER_SIZE"
log "${IND}  $(mark piper) Piper (اختیاری) $([[ $DO_PIPER == 1 ]] && echo "$VOICE" || echo 'رد شد')"
if (( DRY_RUN )); then log "${IND}[dry-run] هیچ چیزی نصب نشد."; exit 0; fi
if [[ "${R[model]}" == ok && "${R[pip]}" == ok ]] && [[ "${R[whisper]}" == ok || $DO_WHISPER == 0 ]]; then
  ok "دستیار هوشمند آماده است. فعال سازی در بله: ⚙️ تنظیمات ← 🤖 دستیار هوشمند صوتی ← فعال سازی"
  [[ -n "${_NASOZ_ASSIST_NESTED:-}" ]] || log "${IND}  (ربات را یک بار ری استارت کنید تا .env جدید خوانده شود: منوی نصب کننده، گزینه ۷)"
  exit 0
fi
[[ -n "${_NASOZ_ASSIST_NESTED:-}" ]] && exit 3   # install.sh prints the retry hints
warn "دستیار هوشمند کامل نصب نشد؛ ربات بدون دستیار کار می کند. لاگ: $INSTALL_LOG"
warn "بررسی دسترسی منابع: bash scripts/install_assistant.sh --check"
warn "دوباره امتحان (دانلودها ادامه پیدا می کنند): cd $WORKDIR && sudo bash install.sh --with-assistant"
warn "یا با بسته آفلاین: sudo bash install.sh --assistant-offline /root/nasoz_assistant_….tar"
exit 3
