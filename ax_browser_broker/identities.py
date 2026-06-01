from __future__ import annotations

import argparse
import ast
import json
import os
import socket
import stat
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import BROWSER_POOL_DIR, IDENTITIES_FILE, POOL_CONFIG_DIR, PROXIES_FILE, SLOTS, ensure_dirs
from .iproyal import redact


class IdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserIdentity:
    identity_id: str
    label: str
    slot: str
    profile_dir: Path
    proxy_ref: str | None
    timezone: str | None
    lang: str
    max_parallel_sessions: int = 1


@dataclass(frozen=True)
class ProxyCredential:
    ref: str
    scheme: str
    host: str
    port: int
    username: str | None
    password: str | None
    country: str | None = None

    def url(self) -> str:
        auth = ""
        if self.username:
            auth = urllib.parse.quote(self.username, safe="")
            if self.password:
                auth += ":" + urllib.parse.quote(self.password, safe="")
            auth += "@"
        return f"{self.scheme}://{auth}{self.host}:{self.port}"


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _slot_names() -> set[str]:
    return {slot.name for slot in SLOTS}


def _slot_by_name(slot_name: str):
    return next((slot for slot in SLOTS if slot.name == slot_name), None)


def local_proxy_port_for_slot(slot_name: str) -> int:
    for index, slot in enumerate(SLOTS, start=1):
        if slot.name == slot_name:
            return 18800 + index
    raise IdentityError(f"Unknown slot: {slot_name}")


def read_slot_config(slot_name: str) -> dict[str, str]:
    if slot_name not in _slot_names():
        raise IdentityError(f"Unknown slot: {slot_name}")
    path = POOL_CONFIG_DIR / f"{slot_name}.env"
    if not path.exists():
        return {}
    config: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        try:
            config[key] = str(ast.literal_eval(value))
        except (SyntaxError, ValueError):
            config[key] = value
    return config


def active_identity_id(slot_name: str) -> str | None:
    return read_slot_config(slot_name).get("IDENTITY_ID")


def _healthy(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _slot_proxy_ready(slot_name: str, timeout: float = 0.3) -> bool:
    config = read_slot_config(slot_name)
    if not config.get("PROXY_REF"):
        return True
    try:
        local_port = int(config.get("PROXY_LOCAL_PORT") or 0)
    except ValueError:
        return False
    if local_port <= 0:
        return False
    try:
        with socket.create_connection(("127.0.0.1", local_port), timeout=timeout):
            return True
    except OSError:
        return False


def _slot_ready(slot_name: str, port: int) -> bool:
    return _healthy(port) and _slot_proxy_ready(slot_name)


def _slot_has_active_lease(slot_name: str) -> bool:
    if not (BROWSER_POOL_DIR / "state" / "leases.json").exists():
        return False
    try:
        state = json.loads((BROWSER_POOL_DIR / "state" / "leases.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return any(str(item.get("name")) == slot_name for item in state.get("leases", {}).values())


def load_identities(path: Path | None = None) -> dict[str, BrowserIdentity]:
    path = path or IDENTITIES_FILE
    raw = _read_json(path, {"identities": {}})
    identities: dict[str, BrowserIdentity] = {}
    for identity_id, item in raw.get("identities", {}).items():
        slot = str(item.get("slot", "")).strip()
        if slot != "auto" and slot not in _slot_names():
            raise IdentityError(f"Identity {identity_id} references unknown slot {slot!r}")
        profile_dir = Path(str(item.get("profile_dir") or BROWSER_POOL_DIR / "profiles" / identity_id))
        policy = item.get("policy") if isinstance(item.get("policy"), dict) else {}
        max_parallel_sessions = max(1, min(int(policy.get("max_parallel_sessions") or 1), len(SLOTS)))
        identities[identity_id] = BrowserIdentity(
            identity_id=identity_id,
            label=str(item.get("label") or identity_id),
            slot=slot,
            profile_dir=profile_dir,
            proxy_ref=str(item["proxy_ref"]).strip() if item.get("proxy_ref") else None,
            timezone=str(item["timezone"]).strip() if item.get("timezone") else None,
            lang=str(item.get("lang") or "en-US"),
            max_parallel_sessions=max_parallel_sessions,
        )
    return identities


def require_identity(identity_id: str) -> BrowserIdentity:
    identities = load_identities()
    if identity_id not in identities:
        raise IdentityError(f"Identity not found: {identity_id}")
    return identities[identity_id]


def load_proxies(path: Path | None = None) -> dict[str, ProxyCredential]:
    path = path or PROXIES_FILE
    raw = _read_json(path, {"proxies": {}})
    proxies: dict[str, ProxyCredential] = {}
    for ref, item in raw.get("proxies", {}).items():
        host = str(item.get("host", "")).strip()
        port = int(item.get("port", 0))
        if not host or port <= 0:
            raise IdentityError(f"Proxy {ref} is missing host or port")
        proxies[ref] = ProxyCredential(
            ref=ref,
            scheme=str(item.get("scheme") or "http"),
            host=host,
            port=port,
            username=str(item["username"]) if item.get("username") else None,
            password=str(item["password"]) if item.get("password") else None,
            country=str(item["country"]) if item.get("country") else None,
        )
    return proxies


def require_proxy(ref: str) -> ProxyCredential:
    proxies = load_proxies()
    if ref not in proxies:
        raise IdentityError(f"Proxy not found: {ref}")
    return proxies[ref]


def save_proxy(ref: str, proxy: dict[str, Any], path: Path | None = None) -> None:
    path = path or PROXIES_FILE
    ensure_dirs()
    raw = _read_json(path, {"proxies": {}})
    raw.setdefault("proxies", {})[ref] = proxy
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def redacted_status() -> dict[str, Any]:
    identities = load_identities()
    proxies = load_proxies()
    out: dict[str, Any] = {"identities": {}, "proxy_refs": sorted(proxies)}
    for identity_id, identity in identities.items():
        proxy = proxies.get(identity.proxy_ref or "")
        out["identities"][identity_id] = {
            "label": identity.label,
            "slot": identity.slot,
            "proxy_ref": identity.proxy_ref,
            "proxy": None
            if proxy is None
            else {
                "scheme": proxy.scheme,
                "host": redact(proxy.host),
                "port": proxy.port,
                "username": redact(proxy.username),
                "country": proxy.country,
            },
            "timezone": identity.timezone,
            "lang": identity.lang,
            "max_parallel_sessions": identity.max_parallel_sessions,
            "active_on_slot": any(active_identity_id(slot.name) == identity.identity_id for slot in SLOTS)
            if identity.slot == "auto"
            else active_identity_id(identity.slot) == identity.identity_id,
        }
    return out


def identity_replica_profile_dir(identity: BrowserIdentity, slot_name: str) -> Path:
    if slot_name not in _slot_names():
        raise IdentityError(f"Unknown slot: {slot_name}")
    return BROWSER_POOL_DIR / "profiles" / ".replicas" / identity.identity_id / slot_name


def _sync_replica_profile(source: Path, target: Path) -> None:
    if not source.exists():
        target.mkdir(parents=True, exist_ok=True)
        return
    if source.resolve() == target.resolve():
        return
    replica_root = BROWSER_POOL_DIR / "profiles" / ".replicas"
    try:
        target.resolve().relative_to(replica_root.resolve())
    except ValueError as error:
        raise IdentityError(f"Refusing to sync replica outside {replica_root}") from error
    target.mkdir(parents=True, exist_ok=True)
    excludes = [
        "Singleton*",
        "Crashpad",
        "BrowserMetrics*",
        "Cache",
        "Code Cache",
        "GPUCache",
        "ShaderCache",
        "GrShaderCache",
        "DawnCache",
        "Default/Cache",
        "Default/Code Cache",
        "Default/GPUCache",
        "Default/Service Worker/CacheStorage",
    ]
    cmd = ["rsync", "-a", "--delete"]
    for pattern in excludes:
        cmd.extend(["--exclude", pattern])
    cmd.extend([str(source) + "/", str(target) + "/"])
    subprocess.run(cmd, check=True)


def write_slot_config(
    identity_id: str,
    local_proxy_port: int = 18801,
    slot_name: str | None = None,
    profile_dir_override: Path | None = None,
) -> Path:
    identity = require_identity(identity_id)
    raw_identity = _read_json(IDENTITIES_FILE, {"identities": {}}).get("identities", {}).get(identity_id, {})
    target_slot = slot_name or identity.slot
    if target_slot == "auto":
        raise IdentityError(f"Identity {identity_id} uses auto slot; pass a concrete slot")
    if target_slot not in _slot_names():
        raise IdentityError(f"Unknown slot: {target_slot}")
    ensure_dirs()
    profile_dir = profile_dir_override or identity.profile_dir
    if profile_dir != identity.profile_dir:
        _sync_replica_profile(identity.profile_dir, profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    if identity.proxy_ref:
        require_proxy(identity.proxy_ref)
    path = POOL_CONFIG_DIR / f"{target_slot}.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Generated by ax-browser-broker. No upstream proxy passwords live in this file.",
        f"IDENTITY_ID={identity.identity_id!r}",
        f"PROFILE_DIR={str(profile_dir)!r}",
        f"CHROME_LANG={identity.lang!r}",
    ]
    if identity.timezone:
        lines.append(f"TZ={identity.timezone!r}")
    if raw_identity.get("source", {}).get("type") == "mac-chrome-profile":
        lines.append("CHROME_DISABLE_SYNC='0'")
    if identity.proxy_ref:
        lines.extend(
            [
                f"PROXY_REF={identity.proxy_ref!r}",
                f"PROXY_LOCAL_PORT={int(local_proxy_port)}",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def clear_auto_identity_from_other_slots(identity: BrowserIdentity, target_slot: str) -> list[str]:
    if identity.slot != "auto":
        return []
    cleared: list[str] = []
    for slot in SLOTS:
        if slot.name == target_slot:
            continue
        if active_identity_id(slot.name) != identity.identity_id:
            continue
        if _slot_has_active_lease(slot.name):
            raise IdentityError(f"Identity {identity.identity_id} also has an active lease on {slot.name}")
        config_path = POOL_CONFIG_DIR / f"{slot.name}.env"
        if config_path.exists():
            config_path.unlink()
        cleared.append(slot.name)
    return cleared


def activate_identity(
    identity_id: str,
    slot_name: str | None = None,
    check_leases: bool = True,
    profile_dir_override: Path | None = None,
    clear_existing: bool = True,
) -> dict[str, Any]:
    identity = require_identity(identity_id)
    target_slot = slot_name or identity.slot
    if target_slot == "auto":
        raise IdentityError(f"Identity {identity_id} uses auto slot; pass a concrete slot")
    slot = _slot_by_name(target_slot)
    if slot is None:
        raise IdentityError(f"Unknown slot: {target_slot}")
    if check_leases and _slot_has_active_lease(target_slot):
        raise IdentityError(f"Slot has an active lease: {target_slot}")
    local_proxy_port = local_proxy_port_for_slot(target_slot)
    cleared_slots = clear_auto_identity_from_other_slots(identity, target_slot) if clear_existing else []
    active_profile_dir = profile_dir_override or identity.profile_dir
    write_slot_config(identity_id, local_proxy_port, target_slot, active_profile_dir)
    active_profile_dir.mkdir(parents=True, exist_ok=True)
    supervisor_was_active = subprocess.run(
        ["systemctl", "is-active", "--quiet", "browser-pool-supervisor.service"],
        check=False,
    ).returncode == 0
    try:
        if supervisor_was_active:
            subprocess.run(["systemctl", "stop", "browser-pool-supervisor.service"], check=True)
        health_ports = [(slot.name, slot.port)]
        for cleared_slot in cleared_slots:
            cleared = _slot_by_name(cleared_slot)
            if cleared is not None:
                subprocess.run([str(BROWSER_POOL_DIR / "bin" / "launch_chrome.sh"), cleared.name, str(cleared.port)], check=True)
                health_ports.append((cleared.name, cleared.port))
        subprocess.run([str(BROWSER_POOL_DIR / "bin" / "launch_chrome.sh"), target_slot, str(slot.port)], check=True)
    finally:
        if supervisor_was_active:
            subprocess.run(["systemctl", "start", "browser-pool-supervisor.service"], check=False)
    deadline = time.time() + 10
    while time.time() < deadline:
        if all(_slot_ready(slot_name, port) for slot_name, port in health_ports):
            return {
                "identity_id": identity.identity_id,
                "slot": target_slot,
                "profile_dir": str(active_profile_dir),
                "active": True,
            }
        time.sleep(0.2)
    raise IdentityError(f"Activated {identity_id}, but slot {target_slot} did not become healthy")


def check_proxy(ref: str, timeout: float = 20.0) -> dict[str, Any]:
    proxy = require_proxy(ref)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy.url(), "https": proxy.url()})
    )
    request = urllib.request.Request("https://ipinfo.io/json", headers={"User-Agent": "ax-browser-broker/1.0"})
    with opener.open(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    return {
        "ref": ref,
        "ip": data.get("ip"),
        "city": data.get("city"),
        "region": data.get("region"),
        "country": data.get("country"),
        "org": data.get("org"),
        "timezone": data.get("timezone"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Identity and proxy helper for OpenBrowser Broker")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("mac-inventory")
    mac_import = sub.add_parser("import-mac-profiles")
    mac_import.add_argument("--chrome-dir")
    mac_import.add_argument("--prefix", default="chrome")
    mac_import.add_argument("--slot", default="auto")
    mac_import.add_argument("--dry-run", action="store_true")
    mac_mirror = sub.add_parser("mirror-mac-profiles")
    mac_mirror.add_argument("--chrome-dir")
    mac_mirror.add_argument("--prefix", default="chrome")
    mac_mirror.add_argument("--dry-run", action="store_true")
    configure = sub.add_parser("configure-slot")
    configure.add_argument("identity_id")
    configure.add_argument("--local-proxy-port", type=int, default=18801)
    configure.add_argument("--slot")
    activate = sub.add_parser("activate")
    activate.add_argument("identity_id")
    activate.add_argument("--slot")
    seed = sub.add_parser("seed-profile")
    seed.add_argument("identity_id")
    seed.add_argument("--force", action="store_true")
    check = sub.add_parser("check-proxy")
    check.add_argument("proxy_ref")
    args = parser.parse_args(argv)

    if args.cmd == "status":
        print(json.dumps(redacted_status(), indent=2))
    elif args.cmd == "mac-inventory":
        from .mac_chrome import redacted_inventory

        print(json.dumps({"profiles": redacted_inventory()}, indent=2))
    elif args.cmd == "import-mac-profiles":
        from .mac_chrome import import_profiles

        print(
            json.dumps(
                import_profiles(
                    chrome_dir=args.chrome_dir,
                    prefix=args.prefix,
                    slot=args.slot,
                    dry_run=args.dry_run,
                ),
                indent=2,
            )
        )
    elif args.cmd == "mirror-mac-profiles":
        from .mac_chrome import MacChromeAccessError, mirror_profiles

        try:
            result = mirror_profiles(
                chrome_dir=args.chrome_dir,
                prefix=args.prefix,
                dry_run=args.dry_run,
            )
        except MacChromeAccessError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            return 2
        print(json.dumps(result, indent=2))
    elif args.cmd == "configure-slot":
        print(
            json.dumps(
                {"slot_config": str(write_slot_config(args.identity_id, args.local_proxy_port, args.slot))},
                indent=2,
            )
        )
    elif args.cmd == "activate":
        print(json.dumps(activate_identity(args.identity_id, args.slot), indent=2))
    elif args.cmd == "seed-profile":
        from .profiles import seed_identity

        print(json.dumps(seed_identity(args.identity_id, args.force), indent=2))
    elif args.cmd == "check-proxy":
        print(json.dumps(check_proxy(args.proxy_ref), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
