from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import BROWSER_POOL_DIR, IDENTITIES_FILE, SLOTS, ensure_dirs


DEFAULT_MAC_CHROME_DIR = Path("/Users/federicodeponte/Library/Application Support/Google/Chrome")
MAC_SSH_ARGS = [
    "ssh",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=5",
    "-o",
    "ServerAliveInterval=5",
    "-o",
    "ServerAliveCountMax=1",
    "mac",
]
MAC_RSYNC_SSH = "ssh -o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=1"


@dataclass(frozen=True)
class MacChromeProfile:
    profile_dir_name: str
    label: str
    account_email: str
    gaia_name: str
    path: Path
    exists: bool


class MacChromeAccessError(RuntimeError):
    pass


def _chrome_dir(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    return Path(os.environ.get("AX_MAC_CHROME_DIR", str(DEFAULT_MAC_CHROME_DIR)))


def _mac_ssh_output(command: str, timeout: float = 10) -> str:
    try:
        return subprocess.check_output(MAC_SSH_ARGS + [command], text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MacChromeAccessError(f"Mac SSH command timed out after {timeout:g}s") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if isinstance(exc.stderr, str) else ""
        detail = stderr or f"exit {exc.returncode}"
        raise MacChromeAccessError(f"Mac SSH command failed: {detail}") from exc


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    mac_home = "/Users/federicodeponte/"
    path_text = str(path)
    if path_text.startswith(mac_home):
        code = r"""
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.exists():
    print("__MISSING__")
else:
    print(path.read_text(), end="")
"""
        output = _mac_ssh_output(f"python3 -c {shlex.quote(code)} {shlex.quote(path_text)}", timeout=10)
        if output == "__MISSING__\n":
            return default
        return json.loads(output)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except PermissionError:
        if not path_text.startswith(mac_home):
            raise
        code = "import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text(), end='')"
        output = _mac_ssh_output(f"python3 -c {shlex.quote(code)} {shlex.quote(path_text)}", timeout=10)
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


def _remote_mac_inventory(root: Path) -> list[MacChromeProfile]:
    code = r"""
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
local_state = root / "Local State"
if not local_state.exists():
    print("[]")
    raise SystemExit
raw = json.loads(local_state.read_text(errors="ignore"))
info_cache = raw.get("profile", {}).get("info_cache", {})
rows = []
for profile_dir_name, item in sorted(info_cache.items()):
    path = root / profile_dir_name
    label = str(item.get("name") or profile_dir_name).strip()
    account_email = str(item.get("user_name") or "").strip()
    gaia_name = str(item.get("gaia_name") or "").strip()
    rows.append({
        "profile_dir_name": str(profile_dir_name),
        "label": label,
        "account_email": account_email,
        "gaia_name": gaia_name,
        "path": str(path),
        "exists": path.exists(),
    })
print(json.dumps(rows))
"""
    output = _mac_ssh_output(f"python3 -c {shlex.quote(code)} {shlex.quote(str(root))}", timeout=30)
    rows = json.loads(output)
    return [
        MacChromeProfile(
            profile_dir_name=str(item["profile_dir_name"]),
            label=str(item["label"]),
            account_email=str(item["account_email"]),
            gaia_name=str(item["gaia_name"]),
            path=Path(str(item["path"])),
            exists=bool(item["exists"]),
        )
        for item in rows
    ]


def inventory(chrome_dir: str | Path | None = None) -> list[MacChromeProfile]:
    root = _chrome_dir(chrome_dir)
    if str(root).startswith("/Users/federicodeponte/"):
        return _remote_mac_inventory(root)
    local_state = root / "Local State"
    raw = _read_json(local_state, {})
    info_cache = raw.get("profile", {}).get("info_cache", {})
    profiles: list[MacChromeProfile] = []
    for profile_dir_name, item in sorted(info_cache.items()):
        label = str(item.get("name") or profile_dir_name).strip()
        account_email = str(item.get("user_name") or "").strip()
        gaia_name = str(item.get("gaia_name") or "").strip()
        path = root / profile_dir_name
        prefs = _read_json(path / "Preferences", {})
        account_info = prefs.get("account_info", [])
        if isinstance(account_info, list):
            first_account = next((entry for entry in account_info if isinstance(entry, dict)), {})
            account_email = str(first_account.get("email") or account_email).strip()
            gaia_name = str(first_account.get("full_name") or gaia_name).strip()
        label = str(prefs.get("profile", {}).get("name") or label).strip()
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


MIRROR_EXCLUDES = [
    "Singleton*",
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
    "Shared Dictionary",
    "Extensions",
    "Extension Cookies",
    "Extension Cookies-journal",
    "Extension Rules",
    "Extension Scripts",
    "Extension State",
    "Local Extension Settings",
    "Managed Extension Settings",
    "Sync Extension Settings",
    "Safe Browsing",
    "ClientCertificates",
    "CertificateRevocation",
    "TrustTokenKeyCommitments",
    "IndexedDB",
    "Local Storage",
    "Session Storage",
    "Service Worker",
    "Storage",
    "WebStorage",
    "File System",
    "Reporting and NEL",
    "*/LOCK",
    "*.log",
    "Cookies",
    "Cookies-journal",
    "Login Data",
    "Login Data-journal",
    "Login Data For Account",
    "Login Data For Account-journal",
    "Web Data",
    "Web Data-journal",
    "Network",
    "Network/Cookies",
    "Network/Cookies-journal",
    "Network Persistent State",
    "Network Action Predictor",
    "Network Action Predictor-journal",
    "TransportSecurity",
    "Trust Tokens",
    "Trust Tokens-journal",
    "DIPS",
    "DIPS-wal",
    "DIPS-shm",
    "ServerCertificate",
    "ServerCertificate-journal",
    "Safe Browsing Cookies",
    "Safe Browsing Cookies-journal",
    "._*",
]


def _rsync_profile(source: Path, dest: Path) -> None:
    is_remote_mac_source = str(source).startswith("/Users/federicodeponte/")
    if not is_remote_mac_source and not source.exists():
        raise RuntimeError(f"Profile source not found: {source}")
    dest.mkdir(parents=True, exist_ok=True)
    if is_remote_mac_source:
        _copy_remote_mac_profile(source, dest)
        return
    args = ["rsync", "-a", "-e", MAC_RSYNC_SSH, "--delete", "--delete-excluded"]
    for pattern in MIRROR_EXCLUDES:
        args.extend(["--exclude", pattern])
    args.extend([str(source) + "/", str(dest) + "/"])
    subprocess.run(args, check=True)


def _copy_remote_mac_profile(source: Path, dest: Path) -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix=f".{dest.name}.", dir=str(dest.parent)))
    try:
        extract_dir = tmp_root / "profile"
        extract_dir.mkdir()
        tar_args = " ".join(shlex.quote(f"--exclude={pattern}") for pattern in MIRROR_EXCLUDES)
        remote_command = f"cd {shlex.quote(str(source))} && tar {tar_args} -cf - ."
        ssh = subprocess.Popen(MAC_SSH_ARGS + [remote_command], stdout=subprocess.PIPE)
        try:
            extract = subprocess.run(
                ["tar", "--warning=no-unknown-keyword", "-xf", "-", "-C", str(extract_dir)],
                stdin=ssh.stdout,
                check=False,
            )
        finally:
            if ssh.stdout:
                ssh.stdout.close()
        ssh_return = ssh.wait()
        if ssh_return != 0:
            raise subprocess.CalledProcessError(ssh_return, MAC_SSH_ARGS + [remote_command])
        if extract.returncode != 0:
            raise subprocess.CalledProcessError(
                extract.returncode,
                ["tar", "--warning=no-unknown-keyword", "-xf", "-", "-C", str(extract_dir)],
            )
        subprocess.run(["rsync", "-a", "--delete", str(extract_dir) + "/", str(dest) + "/"], check=True)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def mirror_profiles(
    chrome_dir: str | Path | None = None,
    identities_path: Path | None = None,
    prefix: str = "chrome",
    dry_run: bool = False,
) -> dict[str, Any]:
    import_result = import_profiles(chrome_dir=chrome_dir, identities_path=identities_path, prefix=prefix, dry_run=dry_run)
    path = identities_path or IDENTITIES_FILE
    raw = _read_json(path, {"identities": {}})
    profiles = inventory(chrome_dir)
    mirrored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    identities = raw.get("identities", {})
    for profile in profiles:
        identity_id = None
        for candidate_id, item in identities.items():
            source = item.get("source", {})
            if source.get("type") == "mac-chrome-profile" and source.get("profile_dir_name") == profile.profile_dir_name:
                identity_id = candidate_id
                break
        if not identity_id:
            skipped.append({"profile_dir_name": profile.profile_dir_name, "reason": "identity_missing"})
            continue
        dest = Path(str(identities[identity_id].get("profile_dir") or BROWSER_POOL_DIR / "profiles" / identity_id))
        if not dry_run:
            _rsync_profile(profile.path, dest)
            stamp = {
                "source": str(profile.path),
                "identity_id": identity_id,
                "mirrored_at": int(time.time()),
                "copied_raw_cookies": False,
                "copied_raw_passwords": False,
                "copied_raw_tokens": False,
                "note": "Mac Keychain-backed cookies/passwords/tokens are not portable to AX41 Linux Chrome.",
            }
            (dest / ".mac-profile-mirror.json").write_text(json.dumps(stamp, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        mirrored.append(
            {
                "identity_id": identity_id,
                "profile_dir_name": profile.profile_dir_name,
                "label": profile.label,
                "account_email": mask_email(profile.account_email),
                "dest": str(dest),
            }
        )
    return {
        "ok": True,
        "dry_run": dry_run,
        "chrome_dir": str(_chrome_dir(chrome_dir)),
        "import": import_result,
        "mirrored_count": len(mirrored),
        "skipped_count": len(skipped),
        "mirrored": mirrored,
        "skipped": skipped,
        "safety": {
            "copied_raw_cookies": False,
            "copied_raw_passwords": False,
            "copied_raw_tokens": False,
            "secret_copying": "disabled",
        },
    }
