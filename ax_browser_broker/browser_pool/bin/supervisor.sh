#!/usr/bin/env bash
set -euo pipefail

BROWSER_POOL_DIR="${OPENBROWSER_BROWSER_POOL_DIR:-/var/lib/openbrowser/pool}"

build_slots() {
  if [[ -n "${OPENBROWSER_SLOTS:-}" ]]; then
    tr ',' '\n' <<<"$OPENBROWSER_SLOTS"
    return
  fi
  local count="${OPENBROWSER_SLOT_COUNT:-8}"
  local start="${OPENBROWSER_SLOT_PORT_START:-9223}"
  python3 - "$count" "$start" <<'PY'
import string
import sys

count = max(1, int(sys.argv[1]))
start = int(sys.argv[2])
for index in range(count):
    suffix = string.ascii_lowercase[index] if index < 26 else str(index + 1)
    print(f"pool-{suffix}:{start + index}")
PY
}

maintenance_active() {
  local file="$1"
  [[ -f "$file" ]] || return 1
  python3 - "$file" <<'PY'
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
}

proxy_active() {
  local config_file="${BROWSER_POOL_DIR}/config/${1}.env"
  [[ -f "$config_file" ]] || return 0
  local has_proxy
  has_proxy="$(grep -E '^PROXY_REF=' "$config_file" 2>/dev/null || true)"
  [[ -n "$has_proxy" ]] || return 0
  local proxy_port
  proxy_port="$(python3 - "$config_file" <<'PY'
import ast
import sys

config = {}
for line in open(sys.argv[1], encoding="utf-8"):
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    key, value = stripped.split("=", 1)
    try:
        config[key] = ast.literal_eval(value)
    except Exception:
        config[key] = value
print(config.get("PROXY_LOCAL_PORT") or "")
PY
)"
  [[ -n "$proxy_port" ]] || return 1
  python3 - "$proxy_port" <<'PY'
import socket
import sys

try:
    with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=0.3):
        sys.exit(0)
except OSError:
    sys.exit(1)
PY
}

while true; do
  while IFS= read -r slot; do
    [[ -n "$slot" ]] || continue
    name="${slot%%:*}"
    port="${slot##*:}"
    if maintenance_active "${BROWSER_POOL_DIR}/state/maintenance/${name}.json"; then
      continue
    fi
    if ! curl -fsS "http://127.0.0.1:${port}/json/version" >/dev/null 2>&1 || ! proxy_active "$name"; then
      "${BROWSER_POOL_DIR}/bin/launch_chrome.sh" "$name" "$port" || true
      sleep 2
    fi
  done < <(build_slots)
  sleep 10
done
