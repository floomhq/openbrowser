#!/usr/bin/env bash
set -euo pipefail

NAME="${1:?name}"
PORT="${2:?port}"
BROWSER_POOL_DIR="${OPENBROWSER_BROWSER_POOL_DIR:-/root/browser-pool}"
BROKER_ROOT="${OPENBROWSER_BROKER_ROOT:-/root/ax-browser-broker}"
CHROME_BIN="${OPENBROWSER_CHROME_BIN:-/usr/bin/google-chrome-stable}"
CONFIG_FILE="${BROWSER_POOL_DIR}/config/${NAME}.env"
PROFILE_DIR="${BROWSER_POOL_DIR}/profiles/${NAME}"
LOG="${BROWSER_POOL_DIR}/logs/${NAME}.log"
MAINTENANCE_FILE="${BROWSER_POOL_DIR}/state/maintenance/${NAME}.json"
PROXY_PID_FILE="${BROWSER_POOL_DIR}/state/${NAME}.proxy.pid"
PROXY_ARGS=()
SYNC_ARGS=()
CHROME_LANG="en-US"

if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$CONFIG_FILE"
fi

mkdir -p "$PROFILE_DIR" "${BROWSER_POOL_DIR}/logs" "${BROWSER_POOL_DIR}/state"
if [[ -f "$MAINTENANCE_FILE" ]]; then
  if python3 - "$MAINTENANCE_FILE" <<'PY'
import json
import os
import sys
import time

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    sys.exit(0)

expires_at = int(data.get("expires_at", 0))
if expires_at and time.time() > expires_at:
    try:
        os.unlink(path)
    except OSError:
        pass
    sys.exit(1)

sys.exit(0)
PY
  then
    echo "$(date -Is) ${NAME} skipped: maintenance marker active at ${MAINTENANCE_FILE}" >>"$LOG"
    exit 0
  fi
fi

pkill -f -- "--user-data-dir=${PROFILE_DIR}" 2>/dev/null || true
pkill -f -- "--remote-debugging-port=${PORT}" 2>/dev/null || true
if [[ -f "$PROXY_PID_FILE" ]]; then
  old_proxy_pid="$(cat "$PROXY_PID_FILE" 2>/dev/null || true)"
  if [[ -n "$old_proxy_pid" ]]; then
    kill "$old_proxy_pid" 2>/dev/null || true
  fi
  rm -f "$PROXY_PID_FILE"
fi
sleep 1

if [[ -n "${PROXY_REF:-}" ]]; then
  PROXY_LOCAL_PORT="${PROXY_LOCAL_PORT:-18801}"
  nohup "${BROKER_ROOT}/bin/ax-proxy-forwarder" \
    --proxy-ref "$PROXY_REF" \
    --listen-host 127.0.0.1 \
    --listen-port "$PROXY_LOCAL_PORT" \
    >>"$LOG" 2>&1 &
  echo $! > "$PROXY_PID_FILE"
  sleep 1
  PROXY_ARGS=(--proxy-server="http://127.0.0.1:${PROXY_LOCAL_PORT}")
fi

if [[ "${CHROME_DISABLE_SYNC:-1}" != "0" ]]; then
  SYNC_ARGS=(--disable-sync)
fi

(
  if [[ -w "/proc/${BASHPID}/oom_score_adj" ]]; then
    echo 300 >"/proc/${BASHPID}/oom_score_adj" || true
  fi
  exec "$CHROME_BIN" \
    --headless \
    --user-data-dir="$PROFILE_DIR" \
    --no-sandbox \
    --disable-gpu \
    --disable-gpu-sandbox \
    --disable-dev-shm-usage \
    --disable-extensions \
    --disable-component-extensions-with-background-pages \
    --remote-debugging-port="$PORT" \
    --remote-debugging-address=127.0.0.1 \
    --disable-background-timer-throttling \
    --disable-renderer-backgrounding \
    --disable-backgrounding-occluded-windows \
    --no-first-run \
    "${SYNC_ARGS[@]}" \
    --lang="$CHROME_LANG" \
    --window-size=1280,800 \
    --window-position=0,0 \
    --remote-allow-origins='*' \
    "${PROXY_ARGS[@]}"
) >"$LOG" 2>&1 &
CHROME_PID=$!
echo "$CHROME_PID" > "${BROWSER_POOL_DIR}/state/${NAME}.pid"
if [[ -w "/proc/${CHROME_PID}/oom_score_adj" ]]; then
  echo 300 >"/proc/${CHROME_PID}/oom_score_adj" || true
fi
sleep 0.5
for CHILD_PID in $(pgrep -f -- "--user-data-dir=${PROFILE_DIR}" 2>/dev/null || true); do
  if [[ -w "/proc/${CHILD_PID}/oom_score_adj" ]]; then
    echo 300 >"/proc/${CHILD_PID}/oom_score_adj" || true
  fi
done
