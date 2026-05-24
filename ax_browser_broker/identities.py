from __future__ import annotations

import argparse
import json
import os
import stat
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


def load_identities(path: Path | None = None) -> dict[str, BrowserIdentity]:
    path = path or IDENTITIES_FILE
    raw = _read_json(path, {"identities": {}})
    identities: dict[str, BrowserIdentity] = {}
    for identity_id, item in raw.get("identities", {}).items():
        slot = str(item.get("slot", "")).strip()
        if slot not in _slot_names():
            raise IdentityError(f"Identity {identity_id} references unknown slot {slot!r}")
        profile_dir = Path(str(item.get("profile_dir") or BROWSER_POOL_DIR / "profiles" / identity_id))
        identities[identity_id] = BrowserIdentity(
            identity_id=identity_id,
            label=str(item.get("label") or identity_id),
            slot=slot,
            profile_dir=profile_dir,
            proxy_ref=str(item["proxy_ref"]).strip() if item.get("proxy_ref") else None,
            timezone=str(item["timezone"]).strip() if item.get("timezone") else None,
            lang=str(item.get("lang") or "en-US"),
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
            "profile_dir": str(identity.profile_dir),
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
        }
    return out


def write_slot_config(identity_id: str, local_proxy_port: int = 18801) -> Path:
    identity = require_identity(identity_id)
    ensure_dirs()
    identity.profile_dir.mkdir(parents=True, exist_ok=True)
    if identity.proxy_ref:
        require_proxy(identity.proxy_ref)
    path = POOL_CONFIG_DIR / f"{identity.slot}.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Generated by ax-browser-broker. No upstream proxy passwords live in this file.",
        f"IDENTITY_ID={identity.identity_id!r}",
        f"PROFILE_DIR={str(identity.profile_dir)!r}",
        f"CHROME_LANG={identity.lang!r}",
    ]
    if identity.timezone:
        lines.append(f"TZ={identity.timezone!r}")
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
    parser = argparse.ArgumentParser(description="Identity and proxy helper for AX41 Browser Broker")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    configure = sub.add_parser("configure-slot")
    configure.add_argument("identity_id")
    configure.add_argument("--local-proxy-port", type=int, default=18801)
    seed = sub.add_parser("seed-profile")
    seed.add_argument("identity_id")
    seed.add_argument("--force", action="store_true")
    check = sub.add_parser("check-proxy")
    check.add_argument("proxy_ref")
    args = parser.parse_args(argv)

    if args.cmd == "status":
        print(json.dumps(redacted_status(), indent=2))
    elif args.cmd == "configure-slot":
        print(json.dumps({"slot_config": str(write_slot_config(args.identity_id, args.local_proxy_port))}, indent=2))
    elif args.cmd == "seed-profile":
        from .profiles import seed_identity

        print(json.dumps(seed_identity(args.identity_id, args.force), indent=2))
    elif args.cmd == "check-proxy":
        print(json.dumps(check_proxy(args.proxy_ref), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
