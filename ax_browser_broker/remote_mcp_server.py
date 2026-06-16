from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP


DEFAULT_BASE_URL = "http://127.0.0.1:8767/openbrowser/v1"
DEFAULT_USER_AGENT = "openbrowser-mcp/1.0"

mcp = FastMCP("openbrowser-remote")


def _base_url() -> str:
    return os.environ.get("OPENBROWSER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _api_key() -> str:
    key = os.environ.get("OPENBROWSER_API_KEY") or os.environ.get("AX_OPENBROWSER_API_KEY") or ""
    key = key.strip()
    if not key:
        raise RuntimeError("OPENBROWSER_API_KEY is required for the remote OpenBrowser MCP server.")
    return key


def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = _base_url() + path
    if query:
        params = {key: value for key, value in query.items() if value is not None}
        if params:
            url += "?" + urllib.parse.urlencode(params)

    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "authorization": f"Bearer {_api_key()}",
            "content-type": "application/json",
            "user-agent": os.environ.get("OPENBROWSER_USER_AGENT", DEFAULT_USER_AGENT),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        payload = error.read().decode("utf-8")
        raise RuntimeError(f"OpenBrowser API request failed: HTTP {error.code}: {payload}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"OpenBrowser API request failed: {error.reason}") from error


@mcp.tool()
def openbrowser_health() -> dict[str, Any]:
    """Check public OpenBrowser API health and broker pool status."""
    return _request("GET", "/health")


@mcp.tool()
def broker_docs() -> dict[str, Any]:
    """Return public OpenBrowser API docs, endpoints, identities, and auth format."""
    return _request("GET", "/docs")


@mcp.tool()
def browser_status() -> dict[str, Any]:
    """Return authenticated public broker health including pool status."""
    return _request("GET", "/health")


@mcp.tool()
def broker_audit(hours: int = 24) -> dict[str, Any]:
    """Audit broker usage, active leases, feedback issues, and telemetry."""
    return _request("GET", "/audit", query={"hours": int(hours)})


@mcp.tool()
def browser_lease(owner: str = "remote-agent", ttl_seconds: int = 300, identity_id: str | None = None) -> dict[str, Any]:
    """Lease an isolated browser session. Pass identity_id for a persisted profile such as work-main."""
    return _request("POST", "/leases", {"owner": owner, "ttl_seconds": ttl_seconds, "identity_id": identity_id})


@mcp.tool()
def browser_release(lease_id: str) -> dict[str, Any]:
    """Release a browser lease."""
    return _request("POST", f"/leases/{urllib.parse.quote(lease_id)}/release")


@mcp.tool()
def browser_heartbeat(lease_id: str) -> dict[str, Any]:
    """Refresh a browser lease TTL."""
    return _request("POST", f"/leases/{urllib.parse.quote(lease_id)}/heartbeat")


@mcp.tool()
def browser_open(owner: str, url: str, identity_id: str | None = None, ttl_seconds: int = 300) -> dict[str, Any]:
    """Lease a browser and navigate it to a URL in one call. Release the returned lease when finished."""
    return _request("POST", "/open", {"owner": owner, "url": url, "identity_id": identity_id, "ttl_seconds": ttl_seconds})


@mcp.tool()
def browser_open_control(
    owner: str,
    url: str,
    identity_id: str | None = None,
    ttl_seconds: int = 900,
    control_ttl_seconds: int = 900,
    screenshot: bool = False,
) -> dict[str, Any]:
    """Open a URL, verify the page, and return a Take Over Tab link in one call."""
    result = browser_open(owner=owner, url=url, identity_id=identity_id, ttl_seconds=ttl_seconds)
    lease_id = str((result.get("lease") or {}).get("lease_id") or result.get("lease_id") or "")
    if lease_id:
        snapshot = browser_snapshot(lease_id)
        result["snapshot"] = {
            "title": snapshot.get("title"),
            "url": snapshot.get("url"),
            "bodyText": str(snapshot.get("bodyText") or "")[:300],
            "body_text_length": len(str(snapshot.get("bodyText") or "")),
            "element_count": len(snapshot.get("elements") or []),
            "slot": snapshot.get("slot"),
        }
        if screenshot:
            result["screenshot"] = {key: value for key, value in browser_screenshot(lease_id).items() if key != "base64"}
        control = takeover_request(lease_id=lease_id, owner=owner, ttl_seconds=control_ttl_seconds)
        result["control"] = control
        result["takeover"] = control
        result["portal_url"] = control.get("portal_url")
    return result


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
    """Fill an element by CSS selector. Rich-text textboxes are handled with keyboard events by the broker."""
    return _request("POST", "/browser/type", {"lease_id": lease_id, "selector": selector, "text": text, "submit": submit})


@mcp.tool()
def browser_keyboard_type(lease_id: str, text: str, selector: str | None = None, delay_ms: int = 0) -> dict[str, Any]:
    """Type through real keyboard events for Discord, Slack, Notion, Linear, X, and other rich-text editors."""
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
def lease_control_request(lease_id: str, owner: str = "remote-agent", ttl_seconds: int = 900) -> dict[str, Any]:
    """Compatibility alias for takeover_request. Do not use for login or credential entry."""
    return takeover_request(lease_id=lease_id, owner=owner, ttl_seconds=ttl_seconds)


@mcp.tool()
def takeover_request(lease_id: str, owner: str = "remote-agent", ttl_seconds: int = 900) -> dict[str, Any]:
    """Create a Take Over Tab link for the exact tab already held by an agent. Not for login or credential entry."""
    return _request("POST", "/takeover/request", {"lease_id": lease_id, "owner": owner, "ttl_seconds": ttl_seconds})


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
def auth_status() -> dict[str, Any]:
    """List pending and completed auth handoff requests."""
    return _request("GET", "/auth/status")


@mcp.tool()
def auth_request(
    owner: str,
    url: str,
    reason: str = "login_required",
    identity_id: str | None = None,
    mode: Literal["same_lease", "vnc"] = "same_lease",
    ttl_seconds: int = 900,
    control_ttl_seconds: int = 900,
    wait_until: str = "domcontentloaded",
    verify: bool = True,
) -> dict[str, Any]:
    """Create a real /auth/<token> login handoff. Default same_lease returns the exact browser lease the agent continues with."""
    return _request(
        "POST",
        "/auth/request",
        {
            "owner": owner,
            "url": url,
            "reason": reason,
            "identity_id": identity_id,
            "mode": mode,
            "ttl_seconds": ttl_seconds,
            "control_ttl_seconds": control_ttl_seconds,
            "wait_until": wait_until,
            "verify": verify,
        },
    )


@mcp.tool()
def auth_batch(owner: str, identity_ids: list[str], url: str = "https://accounts.google.com/", reason: str = "profile_login") -> dict[str, Any]:
    """Create login handoff links for several identities."""
    return _request("POST", "/auth/batch", {"owner": owner, "identity_ids": identity_ids, "url": url, "reason": reason})


@mcp.tool()
def profile_status() -> dict[str, Any]:
    """Return redacted profile and identity status without cookies or passwords."""
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
    """Report a browser broker issue. Do not include passwords, cookies, tokens, or proxy credentials."""
    return _request(
        "POST",
        "/feedback/issues",
        {"source": source, "title": title, "details": details, "severity": severity, "lease_id": lease_id, "url": url, "tags": tags or []},
    )


@mcp.tool()
def feedback_list_issues(status: str = "open", limit: int = 50) -> dict[str, Any]:
    """List browser broker feedback issues. Status can be open, resolved, or all."""
    return _request("GET", "/feedback/issues", query={"status": status, "limit": int(limit)})


@mcp.tool()
def feedback_update_issue(issue_id: str, status: str | None = None, note: str | None = None) -> dict[str, Any]:
    """Update a browser broker issue by adding a note or changing status."""
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
    """Record a sanitized telemetry event for remote MCP/browser work."""
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
    """List recent sanitized telemetry events."""
    return _request(
        "GET",
        "/telemetry/events",
        query={
            "source": source,
            "event_type": event_type,
            "severity": severity,
            "lease_id": lease_id,
            "issue_id": issue_id,
            "limit": int(limit),
        },
    )


@mcp.tool()
def telemetry_summary(window_seconds: int = 86400) -> dict[str, Any]:
    """Return telemetry counts by event type, severity, and source for a time window."""
    return _request("GET", "/telemetry/summary", query={"window_seconds": int(window_seconds)})


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
