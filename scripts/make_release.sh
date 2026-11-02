#!/usr/bin/env bash
# v1.6.0 — build the release package for the built-in updater.
#   dist/nasoz-bot.zip         git archive --prefix=special-materials-robot-project/ <ref>
#   dist/nasoz-bot.zip.sha256  "<sha256>  nasoz-bot.zip"
# Upload both as assets of the GitHub release vX.Y.Z (non-draft, non-prerelease).
set -euo pipefail
cd "$(dirname "$0")/.."
REF="${1:-HEAD}"
SLUG="special-materials-robot-project"
mkdir -p dist
git archive --format=zip --prefix="$SLUG/" -o dist/nasoz-bot.zip "$REF"
( cd dist && sha256sum nasoz-bot.zip > nasoz-bot.zip.sha256 )
echo "version: $(git show "$REF:VERSION")"
cat dist/nasoz-bot.zip.sha256
