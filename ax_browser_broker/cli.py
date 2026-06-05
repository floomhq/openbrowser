from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import BROKER_PORT, OPENBROWSER_API_KEYS_FILE


BASE_URL = f"http://127.0.0.1:{BROKER_PORT}"


class CliError(RuntimeError):
    pass


def _load_api_key() -> str:
    env_key = os.environ.get("OPENBROWSER_API_KEY") or os.environ.get("AX_OPENBROWSER_API_KEY")
    if env_key:
        return env_key.strip()
    if OPENBROWSER_API_KEYS_FILE.exists():
        data = json.loads(OPENBROWSER_API_KEYS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            tokens = data.get("tokens")
            if isinstance(tokens, dict):
                for value in tokens.values():
                    if str(value).strip():
                        return str(value).strip()
            keys = data.get("keys")
            if isinstance(keys, list) and keys:
                return str(keys[0]).strip()
    raise SystemExit("OPENBROWSER_API_KEY is not set and no local key file is available")


def _request(method: str, path: str, body: dict[str, Any] | None = None, auth: bool = False) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"content-type": "application/json"}
    if auth:
        headers["authorization"] = f"Bearer {_load_api_key()}"
    request = urllib.request.Request(BASE_URL + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace").strip()
        detail = body
        try:
            parsed = json.loads(body)
            detail = str(parsed.get("detail") or parsed)
        except Exception:
            pass
        raise CliError(f"HTTP {error.code}: {detail}") from None
    except urllib.error.URLError as error:
        raise CliError(f"Connection failed: {error.reason}") from None


def _print(data: dict[str, Any]) -> int:
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def _compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": snapshot.get("title"),
        "url": snapshot.get("url"),
        "bodyText": str(snapshot.get("bodyText") or "")[:1200],
        "element_count": len(snapshot.get("elements") or []),
        "slot": snapshot.get("slot"),
    }


def _compact_screenshot(screenshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in screenshot.items() if key != "base64"}


def cmd_status(_args: argparse.Namespace) -> int:
    return _print(_request("GET", "/status"))


def cmd_docs(args: argparse.Namespace) -> int:
    topic = urllib.parse.quote(args.topic)
    return _print(_request("GET", f"/agent-docs?topic={topic}"))


def cmd_open(args: argparse.Namespace) -> int:
    result = _request(
        "POST",
        "/openbrowser/v1/open",
        {
            "owner": args.owner,
            "identity_id": args.identity,
            "url": args.url,
            "ttl_seconds": args.ttl,
        },
        auth=True,
    )
    lease_id = str((result.get("lease") or {}).get("lease_id") or "")
    if lease_id and not args.no_verify:
        snapshot = _request("POST", "/openbrowser/v1/browser/snapshot", {"lease_id": lease_id}, auth=True)
        result["snapshot"] = _compact_snapshot(snapshot)
    if lease_id and args.screenshot:
        screenshot = _request(
            "POST",
            "/openbrowser/v1/browser/screenshot",
            {"lease_id": lease_id, "full_page": False},
            auth=True,
        )
        result["screenshot"] = _compact_screenshot(screenshot)
    if lease_id and args.control:
        control = _request(
            "POST",
            "/openbrowser/v1/lease-control/request",
            {
                "owner": args.control_owner or args.owner,
                "lease_id": lease_id,
                "ttl_seconds": args.control_ttl,
            },
            auth=True,
        )
        result["control"] = control
        result["portal_url"] = control.get("portal_url")
    return _print(result)


def cmd_auth(args: argparse.Namespace) -> int:
    return _print(
        _request(
            "POST",
            "/openbrowser/v1/auth/request",
            {
                "owner": args.owner,
                "identity_id": args.identity,
                "url": args.url,
                "reason": args.reason,
            },
            auth=True,
        )
    )


def cmd_lease_control(args: argparse.Namespace) -> int:
    return _print(
        _request(
            "POST",
            "/openbrowser/v1/lease-control/request",
            {
                "owner": args.owner,
                "lease_id": args.lease_id,
                "ttl_seconds": args.ttl,
            },
            auth=True,
        )
    )


def cmd_audit(args: argparse.Namespace) -> int:
    return _print(_request("GET", f"/openbrowser/v1/audit?hours={int(args.hours)}", auth=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenBrowser local CLI for the broker API")
    sub = parser.add_subparsers(dest="cmd", required=True)

    status = sub.add_parser("status", help="Show broker slot and lease status")
    status.set_defaults(func=cmd_status)

    docs = sub.add_parser("docs", help="Show agent docs topic")
    docs.add_argument("topic", nargs="?", default="quickstart")
    docs.set_defaults(func=cmd_docs)

    open_cmd = sub.add_parser("open", help="Open a URL in a leased browser")
    open_cmd.add_argument("url")
    open_cmd.add_argument("--identity", default=None)
    open_cmd.add_argument("--owner", default="openbrowser-cli")
    open_cmd.add_argument("--ttl", type=int, default=900)
    open_cmd.add_argument("--control", action="store_true", help="Return a temporary human-control portal URL")
    open_cmd.add_argument("--control-owner", default=None)
    open_cmd.add_argument("--control-ttl", type=int, default=900)
    open_cmd.add_argument("--screenshot", action="store_true", help="Capture a compact screenshot receipt without base64 output")
    open_cmd.add_argument("--no-verify", action="store_true", help="Skip the post-navigation snapshot receipt")
    open_cmd.set_defaults(func=cmd_open)

    auth = sub.add_parser("auth", help="Create an auth handoff or active lease-control response")
    auth.add_argument("url")
    auth.add_argument("--identity", required=True)
    auth.add_argument("--owner", default="openbrowser-cli")
    auth.add_argument("--reason", default="login_required")
    auth.set_defaults(func=cmd_auth)

    control = sub.add_parser("lease-control", help="Create a human control URL for an active lease")
    control.add_argument("lease_id")
    control.add_argument("--owner", default="openbrowser-cli")
    control.add_argument("--ttl", type=int, default=900)
    control.set_defaults(func=cmd_lease_control)

    audit = sub.add_parser("audit", help="Run broker audit")
    audit.add_argument("--hours", type=int, default=24)
    audit.set_defaults(func=cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return int(args.func(args))
    except CliError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
