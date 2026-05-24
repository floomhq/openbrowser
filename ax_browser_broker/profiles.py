from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import AUTHENTICATED_PROFILE_DIR, BROWSER_POOL_DIR, GOLDEN_PROFILE_DIR, ensure_dirs
from .identities import require_identity
from .pool import status


EXCLUDES = [
    "Singleton*",
    ".totp-secret",
    ".com.google.Chrome.*",
    "Crashpad",
    "BrowserMetrics*",
    "Cache",
    "Code Cache",
    "GPUCache",
    "ShaderCache",
    "GrShaderCache",
    "DawnCache",
    "component_crx_cache",
    "*/LOCK",
    "*.log",
]


def _rsync_profile(source: Path, dest: Path) -> None:
    if not source.exists():
        raise RuntimeError(f"Profile source not found: {source}")
    dest.mkdir(parents=True, exist_ok=True)
    args = ["rsync", "-a", "--delete", "--delete-excluded"]
    for pattern in EXCLUDES:
        args.extend(["--exclude", pattern])
    args.extend([str(source) + "/", str(dest) + "/"])
    subprocess.run(args, check=True)


def snapshot_golden() -> dict[str, Any]:
    ensure_dirs()
    _rsync_profile(AUTHENTICATED_PROFILE_DIR, GOLDEN_PROFILE_DIR)
    stamp_path = GOLDEN_PROFILE_DIR / ".snapshot.json"
    data = {
        "source": str(AUTHENTICATED_PROFILE_DIR),
        "dest": str(GOLDEN_PROFILE_DIR),
        "snapshotted_at": int(time.time()),
    }
    stamp_path.write_text(__import__("json").dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def seed_slot(slot_name: str, force: bool = False) -> dict[str, Any]:
    pool_status = status()
    slot = next((item for item in pool_status["slots"] if item["name"] == slot_name), None)
    if slot is None:
        raise RuntimeError(f"Unknown slot: {slot_name}")
    if pool_status["leases"] and not force:
        raise RuntimeError("Browser pool has active leases")
    if not GOLDEN_PROFILE_DIR.exists():
        raise RuntimeError("Golden profile snapshot not found")
    dest = Path(slot["profile_dir"])
    port = str(slot["port"])
    supervisor_was_active = subprocess.run(
        ["systemctl", "is-active", "--quiet", "browser-pool-supervisor.service"],
        check=False,
    ).returncode == 0
    try:
        if supervisor_was_active:
            subprocess.run(["systemctl", "stop", "browser-pool-supervisor.service"], check=True)
        subprocess.run(["pkill", "-f", "--", f"--user-data-dir={dest}"], check=False)
        time.sleep(1)
        _rsync_profile(GOLDEN_PROFILE_DIR, dest)
        subprocess.run([str(BROWSER_POOL_DIR / "bin" / "launch_chrome.sh"), slot_name, port], check=True)
    finally:
        if supervisor_was_active:
            subprocess.run(["systemctl", "start", "browser-pool-supervisor.service"], check=False)
    return {"slot": slot_name, "profile_dir": str(dest), "seeded_at": int(time.time()), "restarted": True}


def seed_identity(identity_id: str, force: bool = False) -> dict[str, Any]:
    identity = require_identity(identity_id)
    pool_status = status()
    if pool_status["leases"] and not force:
        raise RuntimeError("Browser pool has active leases")
    if not GOLDEN_PROFILE_DIR.exists():
        raise RuntimeError("Golden profile snapshot not found")
    slot = next((item for item in pool_status["slots"] if item["name"] == identity.slot), None)
    if slot is None:
        raise RuntimeError(f"Unknown slot: {identity.slot}")
    port = str(slot["port"])
    dest = identity.profile_dir
    supervisor_was_active = subprocess.run(
        ["systemctl", "is-active", "--quiet", "browser-pool-supervisor.service"],
        check=False,
    ).returncode == 0
    try:
        if supervisor_was_active:
            subprocess.run(["systemctl", "stop", "browser-pool-supervisor.service"], check=True)
        subprocess.run(["pkill", "-f", "--", f"--remote-debugging-port={port}"], check=False)
        subprocess.run(["pkill", "-f", "--", f"--user-data-dir={dest}"], check=False)
        time.sleep(1)
        _rsync_profile(GOLDEN_PROFILE_DIR, dest)
        subprocess.run([str(BROWSER_POOL_DIR / "bin" / "launch_chrome.sh"), identity.slot, port], check=True)
    finally:
        if supervisor_was_active:
            subprocess.run(["systemctl", "start", "browser-pool-supervisor.service"], check=False)
    return {
        "identity_id": identity.identity_id,
        "slot": identity.slot,
        "profile_dir": str(dest),
        "seeded_at": int(time.time()),
        "restarted": True,
    }


def profile_status() -> dict[str, Any]:
    return {
        "authenticated_profile": {
            "path": str(AUTHENTICATED_PROFILE_DIR),
            "exists": AUTHENTICATED_PROFILE_DIR.exists(),
        },
        "golden_profile": {
            "path": str(GOLDEN_PROFILE_DIR),
            "exists": GOLDEN_PROFILE_DIR.exists(),
            "bytes": sum(path.stat().st_size for path in GOLDEN_PROFILE_DIR.rglob("*") if path.is_file())
            if GOLDEN_PROFILE_DIR.exists()
            else 0,
        },
    }
