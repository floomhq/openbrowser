from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from .config import BROKER_PORT


BROKER_URL = f"http://127.0.0.1:{BROKER_PORT}"


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        BROKER_URL + path,
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _lease(owner: str, identity_id: str | None = None) -> dict[str, Any]:
    return _request("POST", "/lease", {"owner": owner, "ttl_seconds": 14400, "identity_id": identity_id})


def _release(lease_id: str) -> None:
    try:
        _request("POST", f"/release/{lease_id}")
    except Exception as error:
        print(f"release failed for {lease_id}: {error}", file=sys.stderr)


def print_env(owner: str, identity_id: str | None = None) -> int:
    lease = _lease(owner, identity_id)
    print(json.dumps(lease, indent=2))
    return 0


def run_browser_use(args: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--identity")
    parsed, passthrough = parser.parse_known_args(args)
    lease = _lease("browser-use", parsed.identity)
    print(f"leased {lease['name']} at {lease['cdp']} for browser-use", file=sys.stderr)
    env = os.environ.copy()
    env["AX_BROWSER_LEASE_ID"] = lease["lease_id"]
    env["AX_BROWSER_CDP_URL"] = lease["cdp"]
    env["BROWSER_USE_CDP_URL"] = lease["cdp"]
    env["PLAYWRIGHT_CDP_ENDPOINT"] = lease["cdp"]
    command = [
        "browser-use",
        "--cdp-url",
        lease["cdp"],
        "--session",
        f"broker-{lease['lease_id']}",
        *passthrough,
    ]
    try:
        return subprocess.call(command, env=env)
    finally:
        _release(lease["lease_id"])


def run_openbrowser(args: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--identity")
    parsed, passthrough = parser.parse_known_args(args)
    lease = _lease("openbrowser", parsed.identity)
    print(f"leased {lease['name']} at {lease['cdp']} for openbrowser", file=sys.stderr)
    env = os.environ.copy()
    env["AX_BROWSER_LEASE_ID"] = lease["lease_id"]
    env["AX_BROWSER_CDP_URL"] = lease["cdp"]
    with tempfile.TemporaryDirectory(prefix="ax-openbrowser-") as tmp:
        home = Path(tmp)
        config_dir = home / ".openbrowser"
        config_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "cdpPort": lease["port"],
            "profileDir": lease["profile_dir"],
            "timezone": "Europe/Berlin",
            "vncPassword": "broker-managed",
            "vncPort": 0,
            "xvfbDisplay": ":0",
        }
        (config_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        env["HOME"] = str(home)
        try:
            return subprocess.call(["node", "/root/openbrowser/dist/index.js", *passthrough], env=env)
        finally:
            _release(lease["lease_id"])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "browser-use":
        return run_browser_use(argv[1:])
    if argv and argv[0] == "openbrowser":
        return run_openbrowser(argv[1:])
    parser = argparse.ArgumentParser(description="AX41 browser broker adapters")
    sub = parser.add_subparsers(dest="cmd", required=True)
    env_cmd = sub.add_parser("env")
    env_cmd.add_argument("--owner", default="manual")
    env_cmd.add_argument("--identity")
    parsed = parser.parse_args(argv)
    if parsed.cmd == "env":
        return print_env(parsed.owner, parsed.identity)
    raise RuntimeError(f"Unhandled command: {parsed.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
