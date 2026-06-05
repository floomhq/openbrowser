from __future__ import annotations

import os
import shutil
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


ROOT = Path(os.environ.get("OPENBROWSER_BROKER_ROOT", "/root/ax-browser-broker"))
BROWSER_POOL_DIR = Path(os.environ.get("OPENBROWSER_BROWSER_POOL_DIR", "/root/browser-pool"))
STATE_DIR = ROOT / "state"
ARTIFACT_DIR = ROOT / "artifacts"
SCREENSHOT_DIR = ARTIFACT_DIR / "screenshots"
LOG_DIR = ROOT / "logs"
PROFILE_DIR = ROOT / "profiles"
CONFIG_DIR = ROOT / "config"
SECRET_DIR = ROOT / "secrets"
GOLDEN_PROFILE_DIR = PROFILE_DIR / "golden"
AUTHENTICATED_PROFILE_DIR = Path(os.environ.get("OPENBROWSER_AUTHENTICATED_PROFILE_DIR", "/root/.config/authenticated-chrome"))
POOL_STATE_FILE = BROWSER_POOL_DIR / "state" / "leases.json"
BROWSER_POOL_MAINTENANCE_DIR = BROWSER_POOL_DIR / "state" / "maintenance"
POOL_CONFIG_DIR = BROWSER_POOL_DIR / "config"
AUTH_STATE_FILE = STATE_DIR / "auth_requests.json"
LEASE_CONTROL_STATE_FILE = STATE_DIR / "lease_control.json"
ISSUE_STATE_FILE = STATE_DIR / "issues.json"
TELEMETRY_STATE_FILE = STATE_DIR / "telemetry.jsonl"
AUDIT_BASELINE_FILE = STATE_DIR / "audit_baseline.json"
IDENTITIES_FILE = CONFIG_DIR / "identities.local.json"
PROXIES_FILE = SECRET_DIR / "proxies.json"
OPENBROWSER_API_KEYS_FILE = SECRET_DIR / "openbrowser_api_keys.json"
BROKER_HOST = os.environ.get("OPENBROWSER_BROKER_HOST", "127.0.0.1")
BROKER_PORT = int(os.environ.get("OPENBROWSER_BROKER_PORT", "8767"))
PUBLIC_AUTH_BASE_URL = os.environ.get("OPENBROWSER_PUBLIC_AUTH_BASE_URL", os.environ.get("AX_BROWSER_PUBLIC_AUTH_BASE_URL", "")).rstrip("/")
PUBLIC_NOVNC_BASE_URL = os.environ.get("OPENBROWSER_PUBLIC_NOVNC_BASE_URL", os.environ.get("AX_BROWSER_PUBLIC_NOVNC_BASE_URL", "")).rstrip("/")
PUBLIC_OPENBROWSER_BASE_URL = os.environ.get(
    "OPENBROWSER_PUBLIC_OPENBROWSER_BASE_URL",
    os.environ.get("AX_BROWSER_PUBLIC_OPENBROWSER_BASE_URL", PUBLIC_AUTH_BASE_URL),
).rstrip("/")
AUTH_PORTAL_AUTOSTART = os.environ.get("OPENBROWSER_AUTH_PORTAL_AUTOSTART", "1").strip().lower() not in {"0", "false", "no", "off"}
AUTH_TRUST_X_FORWARDED_FOR = os.environ.get("OPENBROWSER_AUTH_TRUST_X_FORWARDED_FOR", "0").strip().lower() in {"1", "true", "yes", "on"}
AUTH_TRUSTED_CIDRS = tuple(
    item.strip()
    for item in os.environ.get("OPENBROWSER_AUTH_TRUSTED_CIDRS", "").split(",")
    if item.strip()
)
LEASE_TTL_SECONDS = 60 * 60 * 4
AUTH_REQUEST_TTL_SECONDS = 15 * 60
LEASE_CONTROL_TTL_SECONDS = 15 * 60
MAX_SNAPSHOT_CHARS = 18000
def _slot_suffix(index: int) -> str:
    if index < 26:
        return chr(ord("a") + index)
    return str(index + 1)


def _parse_slots() -> tuple[Slot, ...]:
    explicit = os.environ.get("OPENBROWSER_SLOTS", "").strip()
    if explicit:
        slots = []
        for item in explicit.split(","):
            name, _, port = item.strip().partition(":")
            if not name or not port:
                raise ValueError(f"Invalid OPENBROWSER_SLOTS item: {item!r}")
            slots.append(Slot(name, int(port)))
        return tuple(slots)

    count = max(1, int(os.environ.get("OPENBROWSER_SLOT_COUNT", "8")))
    port_start = int(os.environ.get("OPENBROWSER_SLOT_PORT_START", "9223"))
    return tuple(Slot(f"pool-{_slot_suffix(index)}", port_start + index) for index in range(count))


SLOTS = _parse_slots()


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
    _install_browser_pool_scripts()


def _install_browser_pool_scripts() -> None:
    source_dir = Path(__file__).parent / "browser_pool" / "bin"
    target_dir = BROWSER_POOL_DIR / "bin"
    if not source_dir.exists():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    for source in source_dir.iterdir():
        if not source.is_file():
            continue
        target = target_dir / source.name
        shutil.copy2(source, target)
        target.chmod(target.stat().st_mode | 0o755)
