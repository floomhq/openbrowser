from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Slot:
    name: str
    port: int

    @property
    def cdp(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def profile_dir(self) -> Path:
        return BROWSER_POOL_DIR / "profiles" / self.name


ROOT = Path("/root/ax-browser-broker")
BROWSER_POOL_DIR = Path("/root/browser-pool")
STATE_DIR = ROOT / "state"
ARTIFACT_DIR = ROOT / "artifacts"
SCREENSHOT_DIR = ARTIFACT_DIR / "screenshots"
LOG_DIR = ROOT / "logs"
PROFILE_DIR = ROOT / "profiles"
CONFIG_DIR = ROOT / "config"
SECRET_DIR = ROOT / "secrets"
GOLDEN_PROFILE_DIR = PROFILE_DIR / "golden"
AUTHENTICATED_PROFILE_DIR = Path("/root/.config/authenticated-chrome")
POOL_STATE_FILE = BROWSER_POOL_DIR / "state" / "leases.json"
BROWSER_POOL_MAINTENANCE_DIR = BROWSER_POOL_DIR / "state" / "maintenance"
POOL_CONFIG_DIR = BROWSER_POOL_DIR / "config"
AUTH_STATE_FILE = STATE_DIR / "auth_requests.json"
ISSUE_STATE_FILE = STATE_DIR / "issues.json"
TELEMETRY_STATE_FILE = STATE_DIR / "telemetry.jsonl"
AUDIT_BASELINE_FILE = STATE_DIR / "audit_baseline.json"
IDENTITIES_FILE = CONFIG_DIR / "identities.local.json"
PROXIES_FILE = SECRET_DIR / "proxies.json"
OPENBROWSER_API_KEYS_FILE = SECRET_DIR / "openbrowser_api_keys.json"
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 8767
PUBLIC_AUTH_BASE_URL = os.environ.get("AX_BROWSER_PUBLIC_AUTH_BASE_URL", "").rstrip("/")
PUBLIC_NOVNC_BASE_URL = os.environ.get("AX_BROWSER_PUBLIC_NOVNC_BASE_URL", "").rstrip("/")
PUBLIC_OPENBROWSER_BASE_URL = os.environ.get("AX_BROWSER_PUBLIC_OPENBROWSER_BASE_URL", PUBLIC_AUTH_BASE_URL).rstrip("/")
LEASE_TTL_SECONDS = 60 * 60 * 4
AUTH_REQUEST_TTL_SECONDS = 15 * 60
MAX_SNAPSHOT_CHARS = 18000
SLOTS = (
    Slot("pool-a", 9223),
    Slot("pool-b", 9224),
    Slot("pool-c", 9225),
    Slot("pool-d", 9226),
    Slot("pool-e", 9227),
    Slot("pool-f", 9228),
    Slot("pool-g", 9229),
    Slot("pool-h", 9230),
)


def ensure_dirs() -> None:
    for path in (
        STATE_DIR,
        ARTIFACT_DIR,
        SCREENSHOT_DIR,
        LOG_DIR,
        PROFILE_DIR,
        CONFIG_DIR,
        SECRET_DIR,
        BROWSER_POOL_MAINTENANCE_DIR,
        POOL_CONFIG_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
