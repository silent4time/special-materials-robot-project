#!/usr/bin/env bash
# ساخت بسته آفلاین دستیار هوشمند صوتی (روی سیستمی که به GitHub و Hugging Face دسترسی دارد)
#   bash scripts/build_assistant_bundle.sh [--model qwen2.5:3b] [--whisper small] [--voice fa_IR-amir-medium]
#        [--no-piper] [--out dist] [--cache DIR] [--python 312] [--arch amd64]
# خروجی: dist/nasoz_assistant_<تاریخ>_<مدل>.tar (بدون فشرده سازی؛ GGUF فشرده نمی شود) شامل:
#   ollama/  (bin/ollama + کتابخانه های CPU)   model/ (GGUF + Modelfile)
#   whisper/ (کش Hugging Face مدل Systran/faster-whisper-<size>)   piper/ (صدای فارسی)
#   wheels/  (faster-whisper، requests، piper-tts و وابستگی ها برای cp<python> manylinux)
#   MANIFEST  SHA256SUMS  README.txt
# روی سرور: sudo bash install.sh --assistant-offline /root/nasoz_assistant_….tar
# ترتیب منابع مثل نصب کننده: ASSISTANT_HF_SOURCES="hf-mirror huggingface" و ASSISTANT_OLLAMA_GITHUB_URL
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="qwen2.5:3b"; WHISPER="small"; VOICE="fa_IR-amir-medium"; DO_PIPER=1
OUT="$ROOT/dist"; CACHE="${ASSISTANT_CACHE:-$HOME/.cache/nasoz-assistant-build}"; PYV=312; ARCH=amd64
while (( $# )); do
  case "$1" in
    --model) MODEL="$2"; shift ;;
    --whisper) WHISPER="$2"; shift ;;
    --voice) VOICE="$2"; shift ;;
    --no-piper) DO_PIPER=0 ;;
    --out) OUT="$2"; shift ;;
    --cache) CACHE="$2"; shift ;;
    --python) PYV="$2"; shift ;;
    --arch) ARCH="$2"; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "پرچم ناشناخته: $1" >&2; exit 2 ;;
  esac
  shift
done
HF_SOURCES="${ASSISTANT_HF_SOURCES:-hf-mirror huggingface}"
OLLAMA_URL="${ASSISTANT_OLLAMA_GITHUB_URL:-https://github.com/ollama/ollama/releases/latest/download/ollama-linux-${ARCH}.tar.zst}"
case "$ARCH" in amd64) MARCH=x86_64 ;; arm64) MARCH=aarch64 ;; *) echo "arch نامعتبر: $ARCH" >&2; exit 2 ;; esac
PLATS=(--platform "manylinux2014_$MARCH"); for g in 17 24 27 28 31 34 35; do PLATS+=(--platform "manylinux_2_${g}_$MARCH"); done
log() { printf '%s\n' "$*" >&2; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }
hf_base() { case "$1" in hf-mirror) echo "${ASSISTANT_HF_MIRROR:-https://hf-mirror.com}" ;; huggingface|hf) echo "${ASSISTANT_HF_URL:-https://huggingface.co}" ;; url:*) echo "${1#url:}" ;; *) echo "$1" ;; esac; }
fetch() {  # fetch URL DEST (resumable, cached)
  [[ -s "$2" ]] && { log "  ✓ از کش: $(basename "$2")"; return 0; }
  mkdir -p "$(dirname "$2")"
  log "  ⇣ $1"
  curl -fL --retry 4 --retry-delay 5 --connect-timeout 15 -C - -# -o "$2.part" "$1" && mv -f "$2.part" "$2"
}
fetch_hf() {  # fetch_hf REPO PATH DEST → tries HF sources in order
  local s
  for s in $HF_SOURCES; do fetch "$(hf_base "$s")/$1/resolve/main/$2" "$3" && return 0; log "  ⚠ منبع $s ناموفق؛ منبع بعدی..."; done
  return 1
}
helper() { env WORKDIR="$ROOT" _NASOZ_INSTALL_LOG=/dev/null "$@" bash "$ROOT/scripts/install_assistant.sh" --model "$MODEL" --voice "$VOICE" --no-piper --dry-run; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 لازم است"; }
need curl; need python3; need sha256sum
ZSTD=""; for z in $(type -ap zstd 2>/dev/null); do if "$z" -V >/dev/null 2>&1; then ZSTD="$z"; break; fi; done
[[ -n "$ZSTD" ]] || die "zstd لازم است (apt install zstd)"

safe_model="$(printf '%s' "$MODEL" | tr ':/' '-_')"
JDATE="$(python3 -c 'import jdatetime;print(jdatetime.date.today().strftime("%Y-%m-%d"))' 2>/dev/null || date +%Y-%m-%d)"
NAME="nasoz_assistant_${JDATE}_${safe_model}"
STAGE="$CACHE/stage/nasoz_assistant"
rm -rf "$CACHE/stage"; mkdir -p "$STAGE"/{ollama,model,whisper,piper,wheels} "$OUT"

log "[۱/۶] Ollama از GitHub releases ($ARCH، فقط CPU)"
zst="$CACHE/$(basename "$OLLAMA_URL")"
fetch "$OLLAMA_URL" "$zst"
"$ZSTD" -dc "$zst" | tar -x -C "$STAGE/ollama" --exclude='lib/ollama/cuda_*' --exclude='lib/ollama/rocm*' \
  --exclude='lib/ollama/vulkan*' --exclude='lib/ollama/mlx*'
[[ -x "$STAGE/ollama/bin/ollama" ]] || die "bin/ollama در آرشیو نبود"
OLLAMA_VER="$(curl -fsSIL -o /dev/null -w '%{url_effective}' https://github.com/ollama/ollama/releases/latest 2>/dev/null | sed 's#.*/tag/##')" || OLLAMA_VER="?"

log "[۲/۶] مدل زبانی $MODEL (GGUF)"
map="$(helper _NASOZ_ASSIST_HELPER=gguf)" || die "برای مدل $MODEL فایل GGUF تعریف نشده (ASSISTANT_GGUF_REPO و ASSISTANT_GGUF_FILE)"
repo="${map% *}"; gfile="${map#* }"
fetch_hf "$repo" "$gfile" "$CACHE/gguf/$gfile" || die "دانلود GGUF ناموفق بود"
[[ "$(head -c 4 "$CACHE/gguf/$gfile")" == GGUF ]] || die "فایل GGUF نامعتبر است"
ln "$CACHE/gguf/$gfile" "$STAGE/model/$gfile" 2>/dev/null || cp "$CACHE/gguf/$gfile" "$STAGE/model/$gfile"
helper _NASOZ_ASSIST_HELPER=modelfile _NASOZ_HELPER_DIR="$STAGE/model" _NASOZ_HELPER_FILE="$gfile"

log "[۳/۶] مدل Whisper $WHISPER"
wrepo="Systran/faster-whisper-$WHISPER"
rev="$(curl -fsS --max-time 20 "https://huggingface.co/api/models/$wrepo" 2>/dev/null | grep -oE '"sha":"[0-9a-f]{40}"' | head -n1 | cut -d'"' -f4)" || true
[[ -n "$rev" ]] || rev="nasoz-local"
wd="$STAGE/whisper/models--Systran--faster-whisper-$WHISPER"
mkdir -p "$wd/refs" "$wd/snapshots/$rev"
for f in config.json model.bin tokenizer.json vocabulary.txt; do
  fetch_hf "$wrepo" "$f" "$CACHE/whisper-$WHISPER/$f" || die "دانلود $f ناموفق بود"
  cp "$CACHE/whisper-$WHISPER/$f" "$wd/snapshots/$rev/$f"
done
printf '%s' "$rev" > "$wd/refs/main"

if (( DO_PIPER )); then
  log "[۴/۶] صدای فارسی Piper $VOICE"
  vp="$(helper _NASOZ_ASSIST_HELPER=voicepath)"
  fetch_hf rhasspy/piper-voices "$vp.onnx" "$STAGE/piper/$VOICE.onnx" || die "دانلود صدای Piper ناموفق بود"
  fetch_hf rhasspy/piper-voices "$vp.onnx.json" "$STAGE/piper/$VOICE.onnx.json" || die "دانلود تنظیمات صدای Piper ناموفق بود"
else
  log "[۴/۶] Piper رد شد"
fi

log "[۵/۶] wheels پایتون (cp$PYV، manylinux $MARCH، glibc ≤ 2.35)"
pkgs=(faster-whisper "av<19" requests); (( DO_PIPER )) && pkgs+=(piper-tts)
python3 -m pip download -q --disable-pip-version-check --only-binary=:all: --python-version "$PYV" \
  --implementation cp "${PLATS[@]}" -d "$STAGE/wheels" "${pkgs[@]}"

log "[۶/۶] MANIFEST، SHA256SUMS و tar"
cat > "$STAGE/MANIFEST" <<EOF
MODEL=$MODEL
GGUF=$repo/$gfile
WHISPER=$WHISPER
VOICE=$([[ $DO_PIPER == 1 ]] && echo "$VOICE")
OLLAMA=$OLLAMA_VER
ARCH=$ARCH
PYTHON=cp$PYV
BUILT=$(date '+%Y-%m-%d %H:%M %Z')
EOF
cat > "$STAGE/README.txt" <<EOF
بسته آفلاین دستیار هوشمند صوتی نسوز ($MODEL، Whisper $WHISPER)
1) فایل tar را با WinSCP در /root سرور بگذارید (این فایل را تکه تکه نکنید).
2) sha256sum nasoz_assistant_*.tar   و با sha256 گزارش شده مقایسه کنید.
3) cd /opt/nasoz && sudo bash install.sh --assistant-offline /root/$NAME.tar
   (یا منوی نصب کننده: گزینه 10 و دادن مسیر همین فایل)
4) در بله: ⚙️ تنظیمات ← 🤖 دستیار هوشمند صوتی ← فعال سازی
EOF
(cd "$STAGE" && find . -type f ! -name SHA256SUMS -printf '%P\n' | LC_ALL=C sort | xargs -d '\n' sha256sum > SHA256SUMS)
tar -cf "$OUT/$NAME.tar" -C "$CACHE/stage" nasoz_assistant
rm -rf "$CACHE/stage"
size="$(stat -c %s "$OUT/$NAME.tar")"
log "✓ $OUT/$NAME.tar"
log "  حجم:   $size بایت ($(awk -v b="$size" 'BEGIN{printf "%.2f GB", b/1073741824}'))"
log "  sha256: $(sha256sum "$OUT/$NAME.tar" | cut -d' ' -f1)"
