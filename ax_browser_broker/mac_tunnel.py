from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import ROOT
from .feedback import report_issue
from .mac_chrome import MacChromeAccessError, mirror_profiles


MAC_SSH_HOST = os.environ.get("OPENBROWSER_MAC_SSH_HOST", "mac")
MAC_SSH_PORT = int(os.environ.get("OPENBROWSER_MAC_SSH_PORT", "2222"))
MAC_CDP_LOCAL_URL = os.environ.get("OPENBROWSER_MAC_CDP_LOCAL_URL", "http://127.0.0.1:19333")
MAC_CDP_REMOTE_PORT = int(os.environ.get("OPENBROWSER_MAC_CDP_REMOTE_PORT", "9333"))
MAC_CHROME_CDP_HELPER = os.environ.get("OPENBROWSER_MAC_CHROME_CDP_HELPER", "/root/.codex/scripts/mac-chrome-cdp")
BROKER_PUBLIC_HOST = os.environ.get("OPENBROWSER_BROKER_PUBLIC_HOST", "browser-host.example.com")
SYNC_LOCK = Path(os.environ.get("OPENBROWSER_MAC_PROFILE_SYNC_LOCK", str(ROOT / "state" / "mac-profile-sync" / "sync.lock")))


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    command: str
    detail: str


def _run_probe(args: list[str], timeout: float = 5) -> ProbeResult:
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return ProbeResult(False, " ".join(args), f"timeout after {timeout:g}s")
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode == 0:
        return ProbeResult(True, " ".join(args), detail or "ok")
    return ProbeResult(False, " ".join(args), detail or f"exit {result.returncode}")


def check_ssh() -> ProbeResult:
    return _run_probe(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=3",
            "-o",
            "ServerAliveInterval=3",
            "-o",
            "ServerAliveCountMax=1",
            MAC_SSH_HOST,
            "echo ok",
        ],
        timeout=8,
    )


def check_cdp() -> ProbeResult:
    try:
        with urllib.request.urlopen(f"{MAC_CDP_LOCAL_URL}/json/version", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except (TimeoutError, urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        return ProbeResult(False, f"GET {MAC_CDP_LOCAL_URL}/json/version", str(exc))
    browser = str(data.get("Browser") or "")
    websocket = str(data.get("webSocketDebuggerUrl") or "")
    if response.status == 200 and browser and ":19333/" in websocket:
        return ProbeResult(True, f"GET {MAC_CDP_LOCAL_URL}/json/version", browser)
    return ProbeResult(False, f"GET {MAC_CDP_LOCAL_URL}/json/version", f"unexpected response: {browser}")


def ensure_cdp() -> ProbeResult:
    if check_cdp().ok:
        return check_cdp()
    probe = _run_probe([MAC_CHROME_CDP_HELPER, "ensure"], timeout=15)
    if not probe.ok:
        return probe
    return check_cdp()


def status() -> dict[str, Any]:
    ssh = check_ssh()
    cdp = check_cdp()
    return {
        "ok": ssh.ok and cdp.ok,
        "ssh": asdict(ssh),
        "cdp": asdict(cdp),
        "expected_mac_reverse_tunnel": {
            "broker_listener": f"127.0.0.1:{MAC_SSH_PORT}",
            "mac_command": f"ssh -N -R 127.0.0.1:{MAC_SSH_PORT}:127.0.0.1:22 root@{BROKER_PUBLIC_HOST}",
        },
        "expected_mac_chrome_cdp": {
            "broker_url": MAC_CDP_LOCAL_URL,
            "mac_remote_port": MAC_CDP_REMOTE_PORT,
        },
    }


def sync_profiles(dry_run: bool = False, report: bool = False) -> dict[str, Any]:
    SYNC_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with SYNC_LOCK.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"ok": False, "stage": "lock", "error": "Mac profile sync already running"}
        return _sync_profiles_unlocked(dry_run=dry_run, report=report)


def _sync_profiles_unlocked(dry_run: bool = False, report: bool = False) -> dict[str, Any]:
    ssh = check_ssh()
    if not ssh.ok:
        result = {
            "ok": False,
            "stage": "ssh",
            "error": ssh.detail,
            "status": status(),
        }
        if report:
            report_issue(
                source="ax-mac-profile-sync",
                title="Mac reverse tunnel unavailable for profile sync",
                details=f"ssh mac probe failed: {ssh.detail}",
                severity="high",
                tags=["mac", "profile-mirror", "ssh"],
            )
        return result
    cdp = ensure_cdp()
    try:
        mirror = mirror_profiles(dry_run=dry_run)
    except MacChromeAccessError as exc:
        return {"ok": False, "stage": "mirror", "error": str(exc), "status": status()}
    return {
        "ok": bool(mirror.get("ok")) and cdp.ok,
        "dry_run": dry_run,
        "ssh": asdict(ssh),
        "cdp": asdict(cdp),
        "mirror": mirror,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check and sync Mac Chrome profiles into OpenBrowser Broker")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sync = sub.add_parser("sync")
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--report-issue", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "status":
        result = status()
    elif args.cmd == "sync":
        result = sync_profiles(dry_run=args.dry_run, report=args.report_issue)
    else:
        raise AssertionError(args.cmd)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
