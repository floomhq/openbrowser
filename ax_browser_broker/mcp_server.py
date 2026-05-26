from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import BROKER_PORT


BROKER_URL = f"http://127.0.0.1:{BROKER_PORT}"
mcp = FastMCP("ax41-browser-broker")


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        BROKER_URL + path,
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        payload = error.read().decode("utf-8")
        raise RuntimeError(payload) from error


@mcp.tool()
def browser_status() -> dict[str, Any]:
    """Return broker, pool, slot, and active lease status."""
    return _request("GET", "/status")


@mcp.tool()
def broker_docs(topic: str = "quickstart") -> dict[str, Any]:
    """Return agent-facing docs. Topics: topics, quickstart, identities, browser-use, openbrowser, auth, feedback, telemetry, audit, safety."""
    return _request("GET", f"/agent-docs?topic={urllib.parse.quote(topic)}")


@mcp.tool()
def broker_audit(hours: int = 24) -> dict[str, Any]:
    """Audit whether agents used the broker correctly, including telemetry, issues, active leases, and session-log evidence."""
    return _request("GET", f"/audit?hours={int(hours)}")


@mcp.tool()
def browser_lease(owner: str = "agent", ttl_seconds: int = 14400, identity_id: str | None = None) -> dict[str, Any]:
    """Lease an isolated browser session. Pass identity_id for a pinned profile/proxy identity."""
    return _request("POST", "/lease", {"owner": owner, "ttl_seconds": ttl_seconds, "identity_id": identity_id})


@mcp.tool()
def browser_release(lease_id: str) -> dict[str, Any]:
    """Release a browser lease."""
    return _request("POST", f"/release/{lease_id}")


@mcp.tool()
def browser_heartbeat(lease_id: str) -> dict[str, Any]:
    """Refresh a browser lease TTL."""
    return _request("POST", f"/heartbeat/{lease_id}")


@mcp.tool()
def browser_navigate(lease_id: str, url: str) -> dict[str, Any]:
    """Navigate a leased browser session to a URL."""
    return _request("POST", "/browser/navigate", {"lease_id": lease_id, "url": url})


@mcp.tool()
def browser_snapshot(lease_id: str) -> dict[str, Any]:
    """Return text and interactive element snapshot for the active page."""
    return _request("POST", "/browser/snapshot", {"lease_id": lease_id})


@mcp.tool()
def browser_screenshot(lease_id: str, full_page: bool = False) -> dict[str, Any]:
    """Capture a screenshot for the active page."""
    return _request("POST", "/browser/screenshot", {"lease_id": lease_id, "full_page": full_page})


@mcp.tool()
def browser_click(lease_id: str, selector: str) -> dict[str, Any]:
    """Click an element by CSS selector in a leased browser."""
    return _request("POST", "/browser/click", {"lease_id": lease_id, "selector": selector})


@mcp.tool()
def browser_type(lease_id: str, selector: str, text: str, submit: bool = False) -> dict[str, Any]:
    """Fill an element by CSS selector in a leased browser. Rich-text textboxes use real keyboard events."""
    return _request("POST", "/browser/type", {"lease_id": lease_id, "selector": selector, "text": text, "submit": submit})


@mcp.tool()
def browser_keyboard_type(lease_id: str, text: str, selector: str | None = None, delay_ms: int = 0) -> dict[str, Any]:
    """Type through real keyboard events. Use for Slack, Discord, Notion, Linear, X, and other rich-text editors."""
    return _request(
        "POST",
        "/browser/keyboard-type",
        {"lease_id": lease_id, "selector": selector, "text": text, "delay_ms": delay_ms},
    )


@mcp.tool()
def browser_keyboard_press(lease_id: str, key: str, selector: str | None = None) -> dict[str, Any]:
    """Press a real keyboard key such as Enter, Tab, Escape, ArrowDown, or Control+Enter."""
    return _request("POST", "/browser/keyboard-press", {"lease_id": lease_id, "selector": selector, "key": key})


@mcp.tool()
def lease_control_request(lease_id: str, owner: str = "agent", ttl_seconds: int = 900) -> dict[str, Any]:
    """Create a short-lived human control link for an existing lease. Use for manual login/challenge handoff."""
    return _request(
        "POST",
        "/lease-control/request",
        {"lease_id": lease_id, "owner": owner, "ttl_seconds": ttl_seconds},
    )


@mcp.tool()
def browser_wait(lease_id: str, selector: str | None = None, timeout_ms: int = 1000) -> dict[str, Any]:
    """Wait for a selector or fixed timeout in a leased browser."""
    return _request("POST", "/browser/wait", {"lease_id": lease_id, "selector": selector, "timeout_ms": timeout_ms})


@mcp.tool()
def browser_tabs(lease_id: str) -> dict[str, Any]:
    """List tabs in a leased browser."""
    return _request("POST", "/browser/tabs", {"lease_id": lease_id})


@mcp.tool()
def browser_new_tab(lease_id: str, url: str | None = None) -> dict[str, Any]:
    """Open a new tab in a leased browser."""
    return _request("POST", "/browser/new-tab", {"lease_id": lease_id, "url": url})


@mcp.tool()
def browser_switch_tab(lease_id: str, index: int) -> dict[str, Any]:
    """Switch active tab by index in a leased browser."""
    return _request("POST", "/browser/switch-tab", {"lease_id": lease_id, "index": index})


@mcp.tool()
def browser_upload(lease_id: str, selector: str, path: str) -> dict[str, Any]:
    """Upload a local file through an input selector."""
    return _request("POST", "/browser/upload", {"lease_id": lease_id, "selector": selector, "path": path})


@mcp.tool()
def auth_status() -> dict[str, Any]:
    """List pending and completed auth-refresh requests."""
    return _request("GET", "/auth/status")


@mcp.tool()
def auth_request(owner: str, url: str, reason: str = "login_required", identity_id: str | None = None) -> dict[str, Any]:
    """Create a one-time human auth request when an agent hits a login wall. Pass identity_id to log into that profile."""
    return _request("POST", "/auth/request", {"owner": owner, "url": url, "reason": reason, "identity_id": identity_id})


@mcp.tool()
def profile_status() -> dict[str, Any]:
    """Return authenticated and golden profile status without exposing cookies."""
    return _request("GET", "/profiles/status")


@mcp.tool()
def feedback_report_issue(
    source: str,
    title: str,
    details: str,
    severity: str = "medium",
    lease_id: str | None = None,
    url: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Report browser broker feedback or a task issue into the shared local issue tracker."""
    return _request(
        "POST",
        "/feedback/issues",
        {
            "source": source,
            "title": title,
            "details": details,
            "severity": severity,
            "lease_id": lease_id,
            "url": url,
            "tags": tags or [],
        },
    )


@mcp.tool()
def feedback_list_issues(status: str = "open", limit: int = 50) -> dict[str, Any]:
    """List browser broker feedback issues. Status can be open, resolved, or all."""
    return _request("GET", f"/feedback/issues?status={urllib.parse.quote(status)}&limit={int(limit)}")


@mcp.tool()
def feedback_update_issue(issue_id: str, status: str | None = None, note: str | None = None) -> dict[str, Any]:
    """Update a browser broker feedback issue by adding a note and/or changing status."""
    return _request("POST", f"/feedback/issues/{urllib.parse.quote(issue_id)}", {"status": status, "note": note})


@mcp.tool()
def telemetry_record_event(
    source: str,
    event_type: str,
    message: str,
    severity: str = "info",
    lease_id: str | None = None,
    issue_id: str | None = None,
    url: str | None = None,
    tags: list[str] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a sanitized telemetry event for browser sessions, agent actions, failures, or smoke tests."""
    return _request(
        "POST",
        "/telemetry/events",
        {
            "source": source,
            "event_type": event_type,
            "message": message,
            "severity": severity,
            "lease_id": lease_id,
            "issue_id": issue_id,
            "url": url,
            "tags": tags or [],
            "data": data or {},
        },
    )


@mcp.tool()
def telemetry_list_events(
    source: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    lease_id: str | None = None,
    issue_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List recent sanitized telemetry events with optional filters."""
    params = {
        "limit": str(int(limit)),
    }
    if source:
        params["source"] = source
    if event_type:
        params["event_type"] = event_type
    if severity:
        params["severity"] = severity
    if lease_id:
        params["lease_id"] = lease_id
    if issue_id:
        params["issue_id"] = issue_id
    return _request("GET", "/telemetry/events?" + urllib.parse.urlencode(params))


@mcp.tool()
def telemetry_summary(window_seconds: int = 86400) -> dict[str, Any]:
    """Return telemetry counts by event type, severity, and source for a time window."""
    return _request("GET", f"/telemetry/summary?window_seconds={int(window_seconds)}")


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
