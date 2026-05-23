from __future__ import annotations

import json
import urllib.error
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
def browser_lease(owner: str = "agent", ttl_seconds: int = 14400) -> dict[str, Any]:
    """Lease an isolated browser session. Use the returned lease_id for every browser action."""
    return _request("POST", "/lease", {"owner": owner, "ttl_seconds": ttl_seconds})


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
    """Fill an element by CSS selector in a leased browser."""
    return _request("POST", "/browser/type", {"lease_id": lease_id, "selector": selector, "text": text, "submit": submit})


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
def auth_request(owner: str, url: str, reason: str = "login_required") -> dict[str, Any]:
    """Create a one-time human auth request when an agent hits a login wall."""
    return _request("POST", "/auth/request", {"owner": owner, "url": url, "reason": reason})


@mcp.tool()
def profile_status() -> dict[str, Any]:
    """Return authenticated and golden profile status without exposing cookies."""
    return _request("GET", "/profiles/status")


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
