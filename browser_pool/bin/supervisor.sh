#!/usr/bin/env bash
set -euo pipefail

SLOTS=("pool-a:9223" "pool-b:9224" "pool-c:9225")

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

while true; do
  for slot in "${SLOTS[@]}"; do
    name="${slot%%:*}"
    port="${slot##*:}"
    if maintenance_active "/root/browser-pool/state/maintenance/${name}.json"; then
      continue
    fi
    if ! curl -fsS "http://127.0.0.1:${port}/json/version" >/dev/null 2>&1; then
      /root/ax-browser-broker/browser_pool/bin/launch_chrome.sh "$name" "$port" || true
      sleep 2
    fi
  done
  sleep 10
done
