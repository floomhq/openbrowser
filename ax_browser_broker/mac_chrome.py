from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import BROWSER_POOL_DIR, IDENTITIES_FILE, SLOTS, ensure_dirs


DEFAULT_MAC_CHROME_DIR = Path("/Users/federicodeponte/Library/Application Support/Google/Chrome")


@dataclass(frozen=True)
class MacChromeProfile:
    profile_dir_name: str
    label: str
    account_email: str
    gaia_name: str
    path: Path
    exists: bool


def _chrome_dir(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    return Path(os.environ.get("AX_MAC_CHROME_DIR", str(DEFAULT_MAC_CHROME_DIR)))


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except PermissionError:
        mac_home = "/Users/federicodeponte/"
        path_text = str(path)
        if not path_text.startswith(mac_home):
            raise
        code = "import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text(), end='')"
        output = subprocess.check_output(
            ["ssh", "mac", f"python3 -c {shlex.quote(code)} {shlex.quote(path_text)}"],
            text=True,
        )
        return json.loads(output)


def slugify(value: str, fallback: str = "profile") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def mask_email(value: str) -> str:
    if "@" not in value:
        return value
    local, domain = value.split("@", 1)
    if len(local) <= 2:
        masked_local = local[:1] + "***"
    else:
        masked_local = local[:2] + "***"
    return f"{masked_local}@{domain}"


def inventory(chrome_dir: str | Path | None = None) -> list[MacChromeProfile]:
    root = _chrome_dir(chrome_dir)
    local_state = root / "Local State"
    raw = _read_json(local_state, {})
    info_cache = raw.get("profile", {}).get("info_cache", {})
    profiles: list[MacChromeProfile] = []
    for profile_dir_name, item in sorted(info_cache.items()):
        label = str(item.get("name") or profile_dir_name).strip()
        account_email = str(item.get("user_name") or "").strip()
        gaia_name = str(item.get("gaia_name") or "").strip()
        path = root / profile_dir_name
        profiles.append(
            MacChromeProfile(
                profile_dir_name=str(profile_dir_name),
                label=label,
                account_email=account_email,
                gaia_name=gaia_name,
                path=path,
                exists=path.exists(),
            )
        )
    return profiles


def redacted_inventory(chrome_dir: str | Path | None = None) -> list[dict[str, Any]]:
    rows = []
    for profile in inventory(chrome_dir):
        item = asdict(profile)
        item["path"] = str(profile.path)
        item["account_email"] = mask_email(profile.account_email)
        rows.append(item)
    return rows


def identity_id_for(profile: MacChromeProfile, prefix: str = "chrome") -> str:
    if profile.account_email:
        base = profile.account_email.split("@", 1)[0]
    elif profile.label and profile.label.lower() not in {"person 1", "person"}:
        base = profile.label
    else:
        base = profile.profile_dir_name
    return f"{slugify(prefix, 'chrome')}-{slugify(base, slugify(profile.profile_dir_name))}"


def build_identity_entry(profile: MacChromeProfile, identity_id: str, slot: str = "auto") -> dict[str, Any]:
    return {
        "label": profile.label or profile.profile_dir_name,
        "slot": slot,
        "profile_dir": str(BROWSER_POOL_DIR / "profiles" / identity_id),
        "timezone": "Europe/Berlin",
        "lang": "en-US",
        "source": {
            "type": "mac-chrome-profile",
            "chrome_dir": str(profile.path.parent),
            "profile_dir_name": profile.profile_dir_name,
        },
        "account_email": profile.account_email,
        "gaia_name": profile.gaia_name,
        "policy": {
            "max_parallel_sessions": 1,
            "requires_human_auth": True,
            "secret_copying": "disabled",
        },
    }


def _slot_names() -> set[str]:
    return {slot.name for slot in SLOTS}


def import_profiles(
    chrome_dir: str | Path | None = None,
    identities_path: Path | None = None,
    prefix: str = "chrome",
    slot: str = "auto",
    dry_run: bool = False,
) -> dict[str, Any]:
    if slot != "auto" and slot not in _slot_names():
        raise ValueError(f"Unknown slot: {slot}")
    path = identities_path or IDENTITIES_FILE
    raw = _read_json(path, {"identities": {}})
    raw.setdefault("identities", {})
    created: list[dict[str, str]] = []
    existing: list[dict[str, str]] = []
    profiles = inventory(chrome_dir)
    used_ids = set(raw["identities"])
    for profile in profiles:
        if not profile.exists:
            continue
        base_identity_id = identity_id_for(profile, prefix)
        identity_id = base_identity_id
        suffix = 2
        while identity_id in used_ids:
            existing_source = raw["identities"][identity_id].get("source", {})
            if existing_source.get("type") == "mac-chrome-profile" and existing_source.get("profile_dir_name") == profile.profile_dir_name:
                existing.append({"identity_id": identity_id, "profile_dir_name": profile.profile_dir_name})
                break
            identity_id = f"{base_identity_id}-{suffix}"
            suffix += 1
        else:
            raw["identities"][identity_id] = build_identity_entry(profile, identity_id, slot)
            used_ids.add(identity_id)
            created.append({"identity_id": identity_id, "profile_dir_name": profile.profile_dir_name})
    if not dry_run:
        ensure_dirs()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return {
        "ok": True,
        "dry_run": dry_run,
        "chrome_dir": str(_chrome_dir(chrome_dir)),
        "profile_count": len(profiles),
        "created_count": len(created),
        "existing_count": len(existing),
        "created": created,
        "existing": existing,
        "safety": {
            "copied_raw_cookies": False,
            "copied_raw_passwords": False,
            "copied_raw_tokens": False,
        },
    }
