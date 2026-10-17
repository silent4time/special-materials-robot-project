#!/usr/bin/env bash
# ساخت بستهٔ نصب آفلاین برای سرورهای بدون دسترسی به GitHub/PyPI (Ubuntu 24.04، x86_64، Python 3.12)
# usage: bash scripts/build_offline_bundle.sh [--with-debs] [--out DIR]
#   --with-debs: .deb های tesseract/python3-venv/pip برای noble (فقط وقتی سرور به هیچ mirror apt نرسد)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/dist"; WITH_DEBS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-debs) WITH_DEBS=1 ;;
    --no-debs) WITH_DEBS=0 ;;
    --out) OUT="$2"; shift ;;
    -h|--help) sed -n '2,4p' "$0"; exit 0 ;;
    *) echo "گزینهٔ ناشناخته: $1" >&2; exit 2 ;;
  esac
  shift
done
DEB_ROOTS=(tesseract-ocr tesseract-ocr-fas tesseract-ocr-eng python3.12-venv python3-venv python3-pip)
PLATFORMS=(--platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 --platform linux_x86_64)

cd "$ROOT"
git diff --quiet HEAD -- . ':!*.sh' 2>/dev/null || echo "هشدار: تغییرات commit‌نشده در بسته نمی‌آید (git archive HEAD)." >&2
COMMIT="$(git rev-parse --short HEAD)"
JDATE="$(python3 -c 'import jdatetime;print(jdatetime.date.today().strftime("%Y-%m-%d"))' 2>/dev/null || date +%Y-%m-%d)"
NAME="nasoz_offline_${JDATE}_${COMMIT}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
B="$WORK/$NAME"; mkdir -p "$B/app" "$B/wheels" "$OUT"

echo "[۱/۵] کد برنامه (git archive $COMMIT)"
git archive --format=tar HEAD | tar -x -C "$B/app"
rm -rf "$B/app/data" "$B/app/uploads" "$B/app/.env" "$B/app/dist"
echo "$COMMIT" > "$B/app/.offline_commit"

echo "[۲/۵] wheel ها برای cp312 manylinux x86_64"
PIP=(python3 -m pip)
"${PIP[@]}" download -q --only-binary=:all: "${PLATFORMS[@]}" --python-version 3.12 --implementation cp --abi cp312 \
  -r requirements.txt pip setuptools wheel -d "$B/wheels"
# sdist-only fallback: build in a py3.12 env (requires uv)
if ls "$B"/wheels/*.tar.gz >/dev/null 2>&1; then
  command -v uv >/dev/null || { echo "بستهٔ فقط-سورس پیدا شد و uv نیست" >&2; exit 1; }
  uv python install 3.12 >/dev/null
  for s in "$B"/wheels/*.tar.gz; do uv run -p 3.12 --with build python -m pip wheel --no-deps "$s" -w "$B/wheels" && rm -f "$s"; done
fi

echo "[۳/۵] بسته‌های .deb (اختیاری، --with-debs)"
if (( WITH_DEBS )); then python3 scripts/offline_debs.py "$B/debs" "${DEB_ROOTS[@]}"; else echo "  (رد شد)"; fi

echo "[۴/۵] نصب‌کنندهٔ آفلاین"
cat > "$B/install_offline.sh" <<'INNER'
#!/usr/bin/env bash
# نصب/به‌روزرسانی آفلاین ربات نسوز — بدون GitHub/PyPI
# نصب تازه:   bash install_offline.sh            (ویزارد، سرویس‌ها، تست سلامت)
# به‌روزرسانی: bash install_offline.sh --update   (پوشهٔ نصب موجود: INSTALL_DIR یا /opt/nasoz)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
(cd "$HERE" && sha256sum -c --quiet SHA256SUMS) || { echo "✗ SHA256SUMS نامعتبر — بسته خراب است" >&2; exit 1; }
DEST="${INSTALL_DIR:-/opt/nasoz}"
for a in "$@"; do [[ "$a" == "--update" || "$a" == "--full-update" ]] && UPD=1; done
if [[ "${UPD:-0}" == 1 ]]; then
  [[ -f "$DEST/.env" ]] || { echo "نصب موجودی در $DEST نیست (INSTALL_DIR=...)" >&2; exit 1; }
  INSTALL_DIR="$DEST" exec bash "$HERE/app/scripts/install.sh" "$@" --offline "$HERE"
fi
if [[ -f "$DEST/scripts/install.sh" && -f "$DEST/.env" ]]; then
  echo "نصب موجود در $DEST پیدا شد؛ برای به‌روزرسانی: bash install_offline.sh --update" >&2; exit 1
fi
mkdir -p "$DEST"; cp -a "$HERE/app/." "$DEST/"
cd "$DEST" && exec bash scripts/install.sh --offline "$HERE" "$@"
INNER
chmod +x "$B/install_offline.sh"
cp "$ROOT/DEPLOY.md" "$B/README_OFFLINE.md"

echo "[۵/۵] SHA256SUMS و فشرده‌سازی"
(cd "$B" && find . -type f ! -name SHA256SUMS -printf '%P\n' | LC_ALL=C sort | xargs -d "\n" sha256sum > "$WORK/sums" && mv "$WORK/sums" SHA256SUMS)
# guard: no secrets/data
if find "$B" \( -name .env -o -name '*.db' -o -path '*/data/*' -o -path '*/uploads/*' \) | grep -q .; then echo "✗ داده/‏.env در بسته!" >&2; exit 1; fi
tar -C "$WORK" -czf "$OUT/$NAME.tar.gz" "$NAME"
echo "✓ $OUT/$NAME.tar.gz ($(du -h "$OUT/$NAME.tar.gz" | cut -f1))"
du -sh "$B/app" "$B/wheels" "$B/debs" 2>/dev/null || true
