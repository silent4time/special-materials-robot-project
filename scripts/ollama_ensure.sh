#!/usr/bin/env bash
# Start Ollama on hosts WITHOUT systemd (containers, sandboxes) and keep it running.
# Idempotent: does nothing if http://127.0.0.1:11434 already answers.
# A detached supervisor (setsid nohup) restarts "ollama serve" if it exits (like Restart=always).
#   bash scripts/ollama_ensure.sh            # start if down (as root: runs as user ollama)
#   bash scripts/ollama_ensure.sh --status   # print state, exit 0 if up
#   bash scripts/ollama_ensure.sh --stop     # stop supervisor + server
# Env: OLLAMA_HOST (default 127.0.0.1:11434), OLLAMA_BIN, NASOZ_OLLAMA_STATE (pid/log dir).
set -u
HOSTPORT="${OLLAMA_HOST:-127.0.0.1:11434}"
URL="http://$HOSTPORT"
BIN="${OLLAMA_BIN:-$(command -v ollama 2>/dev/null || echo /usr/local/bin/ollama)}"
if [[ $EUID -eq 0 ]]; then STATE="${NASOZ_OLLAMA_STATE:-/var/lib/nasoz-ollama}"; else STATE="${NASOZ_OLLAMA_STATE:-$HOME/.cache/nasoz-ollama}"; fi
# non-root --status/--stop also see a supervisor started by root
[[ $EUID -ne 0 && -z "${NASOZ_OLLAMA_STATE:-}" && -f /var/lib/nasoz-ollama/supervisor.pid && "${1:-}" == --status ]] && STATE=/var/lib/nasoz-ollama
PIDF="$STATE/supervisor.pid"; LOG="$STATE/ollama.log"
up() { curl -fsS -m 3 "$URL/api/tags" >/dev/null 2>&1; }
sup_alive() { [[ -f "$PIDF" ]] && ps -p "$(cat "$PIDF" 2>/dev/null)" >/dev/null 2>&1; }
case "${1:-}" in
  --status)
    if up; then echo "ollama: up ($URL)"; else echo "ollama: down ($URL)"; fi
    sup_alive && echo "supervisor: pid $(cat "$PIDF")" || echo "supervisor: not running"
    up; exit $? ;;
  --stop)
    if sup_alive; then kill -- -"$(cat "$PIDF")" 2>/dev/null || kill "$(cat "$PIDF")"; fi
    rm -f "$PIDF"; pkill -f "$BIN serve" 2>/dev/null; echo "stopped"; exit 0 ;;
esac
if up; then echo "ollama: already up ($URL)"; exit 0; fi
[[ -x "$BIN" ]] || { echo "ollama binary not found ($BIN); run scripts/install_assistant.sh" >&2; exit 2; }
mkdir -p "$STATE"
if sup_alive; then echo "supervisor running (pid $(cat "$PIDF")); waiting..."; else
  RUNAS=(); OHOME="$HOME"
  if [[ $EUID -eq 0 ]] && id ollama >/dev/null 2>&1 && command -v runuser >/dev/null; then
    RUNAS=(runuser -u ollama --); OHOME="$(getent passwd ollama | cut -d: -f6)"; chown ollama: "$STATE" 2>/dev/null || true
  fi
  # setsid: own session, survives the parent shell/terminal; loop restarts the server if it dies.
  setsid nohup bash -c 'while :; do "$@"; echo "[$(date "+%F %T")] ollama serve exited ($?); restart in 3s"; sleep 3; done' _ \
    "${RUNAS[@]}" env HOME="$OHOME" OLLAMA_HOST="$HOSTPORT" "$BIN" serve >>"$LOG" 2>&1 </dev/null &
  echo $! > "$PIDF"
fi
for _ in $(seq 1 40); do up && { echo "ollama: up ($URL), supervisor pid $(cat "$PIDF"), log $LOG"; exit 0; }; sleep 1; done
echo "ollama did not come up in 40s; see $LOG" >&2; exit 1
