from __future__ import annotations

import argparse
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import BROKER_PORT
from .feedback import report_issue
from .telemetry import record_event


BROKER_URL = f"http://127.0.0.1:{BROKER_PORT}"
LEASE_RETRY_SECONDS = 15
LEASE_RETRY_INTERVAL_SECONDS = 1
SAFE_COMMAND_WORDS = {
    "browser-use",
    "help",
    "login",
    "node",
    "openbrowser",
    "run",
    "status",
}
BOOLEAN_FLAGS = {
    "-h",
    "--help",
    "--json",
    "--verbose",
    "--version",
}


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
    deadline = time.monotonic() + LEASE_RETRY_SECONDS
    while True:
        try:
            return _request("POST", "/lease", {"owner": owner, "ttl_seconds": 14400, "identity_id": identity_id})
        except urllib.error.HTTPError as error:
            if error.code != 409 or time.monotonic() >= deadline:
                raise
            time.sleep(LEASE_RETRY_INTERVAL_SECONDS)
        except urllib.error.URLError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(LEASE_RETRY_INTERVAL_SECONDS)


def _release(lease_id: str) -> None:
    try:
        _request("POST", f"/release/{lease_id}")
    except Exception as error:
        print(f"release failed for {lease_id}: {error}", file=sys.stderr)


def _safe_record_event(**kwargs: Any) -> None:
    try:
        record_event(**kwargs)
    except Exception:
        return


def _safe_report_issue(**kwargs: Any) -> dict[str, Any] | None:
    try:
        return report_issue(**kwargs)
    except Exception:
        return None


def _command_shape(command: list[str]) -> list[str]:
    shape: list[str] = []
    redact_next = False
    for index, raw_token in enumerate(command[:20]):
        token = str(raw_token)
        if index == 0:
            shape.append(Path(token).name)
            continue
        if redact_next:
            shape.append("[redacted]")
            redact_next = False
            continue
        if token in BOOLEAN_FLAGS:
            shape.append(token)
            continue
        if token.startswith("-"):
            if "=" in token:
                flag = token.split("=", 1)[0]
                shape.append(f"{flag}=[redacted]")
            else:
                shape.append(token)
                redact_next = True
            continue
        if token in SAFE_COMMAND_WORDS:
            shape.append(token)
            continue
        if token.endswith(".js"):
            shape.append(Path(token).name)
            continue
        shape.append("[redacted]")
    return shape


def _adapter_data(lease: dict[str, Any], identity_id: str | None, command: list[str] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "slot": lease.get("name"),
        "identity_id": identity_id or lease.get("identity_id"),
        "port": lease.get("port"),
    }
    if command is not None:
        data["command_shape"] = _command_shape(command)
        data["argc"] = len(command)
    return data


def print_env(owner: str, identity_id: str | None = None) -> int:
    lease = _lease(owner, identity_id)
    print(json.dumps(lease, indent=2))
    return 0


def _is_help(args: list[str]) -> bool:
    return any(item in {"-h", "--help"} for item in args)


def _openbrowser_command(args: list[str]) -> list[str]:
    configured = os.environ.get("OPENBROWSER_CLI", "openbrowser")
    return [*shlex.split(configured), *args]


def run_browser_use(args: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--identity")
    parsed, passthrough = parser.parse_known_args(args)
    if _is_help(passthrough):
        return subprocess.call(["browser-use", *passthrough])
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
    started_at = time.monotonic()
    _safe_record_event(
        source="browser-use",
        event_type="session",
        message="browser-use adapter started",
        lease_id=lease["lease_id"],
        tags=["adapter", "browser-use", "start"],
        data=_adapter_data(lease, parsed.identity, command),
    )
    try:
        exit_code = subprocess.call(command, env=env)
        duration_ms = int((time.monotonic() - started_at) * 1000)
        failed = exit_code != 0
        _safe_record_event(
            source="browser-use",
            event_type="error" if failed else "session",
            message="browser-use adapter failed" if failed else "browser-use adapter completed",
            severity="error" if failed else "info",
            lease_id=lease["lease_id"],
            tags=["adapter", "browser-use", "failure" if failed else "complete"],
            data={**_adapter_data(lease, parsed.identity, command), "exit_code": exit_code, "duration_ms": duration_ms},
        )
        if failed:
            issue = _safe_report_issue(
                source="browser-use",
                title="browser-use adapter exited nonzero",
                details=f"browser-use exited with code {exit_code}. Run audit and inspect lease/session logs for this lease.",
                severity="high",
                lease_id=lease["lease_id"],
                tags=["adapter", "browser-use", "nonzero-exit"],
            )
            if issue:
                _safe_record_event(
                    source="browser-use",
                    event_type="issue",
                    message="browser-use adapter issue filed",
                    severity="error",
                    lease_id=lease["lease_id"],
                    issue_id=issue["id"],
                    tags=["adapter", "browser-use", "issue"],
                    data={"exit_code": exit_code},
                )
        return exit_code
    except Exception as error:
        _safe_record_event(
            source="browser-use",
            event_type="error",
            message="browser-use adapter exception",
            severity="error",
            lease_id=lease["lease_id"],
            tags=["adapter", "browser-use", "exception"],
            data={
                **_adapter_data(lease, parsed.identity, command),
                "error": str(error),
                "duration_ms": int((time.monotonic() - started_at) * 1000),
            },
        )
        raise
    finally:
        _release(lease["lease_id"])


def run_openbrowser(args: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--identity")
    parsed, passthrough = parser.parse_known_args(args)
    if _is_help(passthrough):
        return subprocess.call(_openbrowser_command(passthrough))
    lease = _lease("openbrowser", parsed.identity)
    print(f"leased {lease['name']} at {lease['cdp']} for openbrowser", file=sys.stderr)
    started_at = time.monotonic()
    _safe_record_event(
        source="openbrowser",
        event_type="session",
        message="OpenBrowser adapter started",
        lease_id=lease["lease_id"],
        tags=["adapter", "openbrowser", "start"],
        data=_adapter_data(lease, parsed.identity, ["openbrowser", *passthrough]),
    )
    if passthrough and passthrough[0] == "status":
        try:
            print(json.dumps(_openbrowser_status(lease), indent=2, sort_keys=True))
            _safe_record_event(
                source="openbrowser",
                event_type="session",
                message="OpenBrowser status completed",
                lease_id=lease["lease_id"],
                tags=["adapter", "openbrowser", "status"],
                data={**_adapter_data(lease, parsed.identity), "duration_ms": int((time.monotonic() - started_at) * 1000)},
            )
            return 0
        finally:
            _release(lease["lease_id"])
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
        command = _openbrowser_command(passthrough)
        try:
            exit_code = subprocess.call(command, env=env)
            duration_ms = int((time.monotonic() - started_at) * 1000)
            failed = exit_code != 0
            _safe_record_event(
                source="openbrowser",
                event_type="error" if failed else "session",
                message="OpenBrowser adapter failed" if failed else "OpenBrowser adapter completed",
                severity="error" if failed else "info",
                lease_id=lease["lease_id"],
                tags=["adapter", "openbrowser", "failure" if failed else "complete"],
                data={**_adapter_data(lease, parsed.identity, command), "exit_code": exit_code, "duration_ms": duration_ms},
            )
            if failed:
                issue = _safe_report_issue(
                    source="openbrowser",
                    title="OpenBrowser adapter exited nonzero",
                    details=f"OpenBrowser exited with code {exit_code}. Run audit and inspect lease/session logs for this lease.",
                    severity="high",
                    lease_id=lease["lease_id"],
                    tags=["adapter", "openbrowser", "nonzero-exit"],
                )
                if issue:
                    _safe_record_event(
                        source="openbrowser",
                        event_type="issue",
                        message="OpenBrowser adapter issue filed",
                        severity="error",
                        lease_id=lease["lease_id"],
                        issue_id=issue["id"],
                        tags=["adapter", "openbrowser", "issue"],
                        data={"exit_code": exit_code},
                    )
            return exit_code
        except Exception as error:
            _safe_record_event(
                source="openbrowser",
                event_type="error",
                message="OpenBrowser adapter exception",
                severity="error",
                lease_id=lease["lease_id"],
                tags=["adapter", "openbrowser", "exception"],
                data={
                    **_adapter_data(lease, parsed.identity, command),
                    "error": str(error),
                    "duration_ms": int((time.monotonic() - started_at) * 1000),
                },
            )
            raise
        finally:
            _release(lease["lease_id"])


def _cookie_names(profile_dir: str, domain_like: str) -> list[str]:
    cookie_db = Path(profile_dir) / "Default" / "Cookies"
    if not cookie_db.exists():
        return []
    try:
        with sqlite3.connect(f"file:{cookie_db}?mode=ro", uri=True, timeout=5) as connection:
            rows = connection.execute(
                "select distinct name from cookies where host_key like ? order by name",
                (f"%{domain_like}",),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [str(row[0]) for row in rows]


def _openbrowser_status(lease: dict[str, Any]) -> dict[str, Any]:
    linkedin_required = ["li_at", "JSESSIONID", "bcookie", "bscookie", "lidc"]
    linkedin_names = _cookie_names(str(lease["profile_dir"]), "linkedin.com")
    return {
        "ok": True,
        "adapter": "openbrowser",
        "lease": {
            "slot": lease.get("name"),
            "identity_id": lease.get("identity_id"),
            "profile_dir": lease.get("profile_dir"),
            "cdp": lease.get("cdp"),
        },
        "cookies": {
            "linkedin": {
                "present": [name for name in linkedin_required if name in linkedin_names],
                "missing": [name for name in linkedin_required if name not in linkedin_names],
                "count": len(linkedin_names),
            }
        },
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "browser-use":
        return run_browser_use(argv[1:])
    if argv and argv[0] == "openbrowser":
        return run_openbrowser(argv[1:])
    parser = argparse.ArgumentParser(description="OpenBrowser Broker adapters")
    sub = parser.add_subparsers(dest="cmd", required=True)
    env_cmd = sub.add_parser("env")
    env_cmd.add_argument("--owner", default="manual")
    env_cmd.add_argument("--identity")
    parsed = parser.parse_args(argv)
    if parsed.cmd == "env":
        return print_env(parsed.owner, parsed.identity)
    raise RuntimeError(f"Unhandled command: {parsed.cmd}")


def main_browser_use() -> int:
    return run_browser_use(sys.argv[1:])


def main_openbrowser() -> int:
    return run_openbrowser(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
