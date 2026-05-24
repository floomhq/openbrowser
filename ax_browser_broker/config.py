from __future__ import annotations

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
POOL_CONFIG_DIR = BROWSER_POOL_DIR / "config"
AUTH_STATE_FILE = STATE_DIR / "auth_requests.json"
ISSUE_STATE_FILE = STATE_DIR / "issues.json"
TELEMETRY_STATE_FILE = STATE_DIR / "telemetry.jsonl"
IDENTITIES_FILE = CONFIG_DIR / "identities.local.json"
PROXIES_FILE = SECRET_DIR / "proxies.json"
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 8767
LEASE_TTL_SECONDS = 60 * 60 * 4
AUTH_REQUEST_TTL_SECONDS = 15 * 60
MAX_SNAPSHOT_CHARS = 18000
SLOTS = (
    Slot("pool-a", 9223),
    Slot("pool-b", 9224),
    Slot("pool-c", 9225),
)


def ensure_dirs() -> None:
    for path in (STATE_DIR, ARTIFACT_DIR, SCREENSHOT_DIR, LOG_DIR, PROFILE_DIR, CONFIG_DIR, SECRET_DIR, POOL_CONFIG_DIR):
        path.mkdir(parents=True, exist_ok=True)
