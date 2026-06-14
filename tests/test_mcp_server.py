from __future__ import annotations

from typing import get_args, get_type_hints

from ax_browser_broker import mcp_server


def test_browser_open_control_composes_open_verify_and_control(monkeypatch) -> None:
    calls = []

    def fake_request(method, path, body=None):
        calls.append((method, path, body))
        if path == "/lease":
            return {"lease_id": "lease-mcp", "name": "pool-d", "identity_id": "chrome-work"}
        if path == "/browser/navigate":
            return {"lease_id": "lease-mcp", "slot": "pool-d", "url": "https://lovable.dev/dashboard", "title": "Home | Lovable"}
        if path == "/browser/snapshot":
            return {
                "lease_id": "lease-mcp",
                "slot": "pool-d",
                "title": "Home | Lovable",
                "url": "https://lovable.dev/dashboard",
                "bodyText": "B" * 1400,
                "elements": [{}, {}],
            }
        if path == "/lease-control/request":
            return {"lease_id": "lease-mcp", "portal_url": "https://browser.example.com/auth/lease-control/tok"}
        raise AssertionError(path)

    monkeypatch.setattr(mcp_server, "_request", fake_request)

    result = mcp_server.browser_open_control(
        owner="pytest-mcp",
        url="https://lovable.dev",
        identity_id="chrome-work",
        ttl_seconds=600,
        control_ttl_seconds=300,
    )

    assert result["portal_url"].endswith("/tok")
    assert result["snapshot"]["bodyText"] == "B" * 300
    assert result["snapshot"]["body_text_length"] == 1400
    assert result["snapshot"]["element_count"] == 2
    assert calls == [
        ("POST", "/lease", {"owner": "pytest-mcp", "ttl_seconds": 600, "identity_id": "chrome-work"}),
        ("POST", "/browser/navigate", {"lease_id": "lease-mcp", "url": "https://lovable.dev"}),
        ("POST", "/browser/snapshot", {"lease_id": "lease-mcp"}),
        ("POST", "/lease-control/request", {"lease_id": "lease-mcp", "owner": "pytest-mcp", "ttl_seconds": 300}),
    ]


def test_auth_request_forwards_lease_control_options(monkeypatch) -> None:
    calls = []

    def fake_request(method, path, body=None):
        calls.append((method, path, body))
        return {"mode": "lease_control"}

    monkeypatch.setattr(mcp_server, "_request", fake_request)

    result = mcp_server.auth_request(
        owner="pytest-mcp",
        url="https://example.com/login",
        identity_id="work-main",
        ttl_seconds=120,
        control_ttl_seconds=180,
        wait_until="load",
        verify=False,
    )

    assert result == {"mode": "lease_control"}
    assert calls == [
        (
            "POST",
            "/auth/request",
            {
                "owner": "pytest-mcp",
                "url": "https://example.com/login",
                "reason": "login_required",
                "identity_id": "work-main",
                "mode": "lease_control",
                "ttl_seconds": 120,
                "control_ttl_seconds": 180,
                "wait_until": "load",
                "verify": False,
            },
        )
    ]


def test_auth_request_mode_annotation_exposes_enum() -> None:
    assert get_args(get_type_hints(mcp_server.auth_request)["mode"]) == ("lease_control", "vnc")


def test_browser_open_control_releases_on_navigation_failure(monkeypatch) -> None:
    calls = []

    def fake_request(method, path, body=None):
        calls.append((method, path, body))
        if path == "/lease":
            return {"lease_id": "lease-mcp", "name": "pool-d"}
        if path == "/browser/navigate":
            raise RuntimeError("navigation failed")
        if path == "/release/lease-mcp":
            return {"released": "lease-mcp", "slot": "pool-d"}
        raise AssertionError(path)

    monkeypatch.setattr(mcp_server, "_request", fake_request)

    try:
        mcp_server.browser_open_control(owner="pytest-mcp", url="https://lovable.dev")
    except RuntimeError as error:
        assert str(error) == "navigation failed"
    else:
        raise AssertionError("navigation failure did not propagate")

    assert calls[-1] == ("POST", "/release/lease-mcp", None)


def test_browser_open_control_reuses_matching_active_identity_lease(monkeypatch) -> None:
    calls = []

    def fake_request(method, path, body=None):
        calls.append((method, path, body))
        if path == "/lease":
            raise RuntimeError('{"detail":"Identity already leased at max parallel sessions: chrome-work"}')
        if path == "/status":
            return {
                "leases": {
                    "lease-active": {"lease_id": "lease-active", "name": "pool-f", "identity_id": "chrome-work", "owner": "other-agent"}
                }
            }
        if path == "/browser/tabs":
            return {"tabs": [{"index": 0, "active": True, "url": "https://lovable.dev/dashboard", "title": "Home | Lovable"}]}
        if path == "/browser/snapshot":
            return {
                "lease_id": "lease-active",
                "slot": "pool-f",
                "title": "Home | Lovable",
                "url": "https://lovable.dev/dashboard",
                "bodyText": "Lovable dashboard",
                "elements": [{}],
            }
        if path == "/lease-control/request":
            return {"lease_id": "lease-active", "portal_url": "https://browser.example.com/auth/lease-control/tok"}
        raise AssertionError(path)

    monkeypatch.setattr(mcp_server, "_request", fake_request)

    result = mcp_server.browser_open_control(owner="pytest-mcp", url="https://lovable.dev", identity_id="chrome-work")

    assert result["reused_existing_lease"] is True
    assert result["lease"]["lease_id"] == "lease-active"
    assert result["navigation"]["status"] == "already_open"
    assert result["portal_url"].endswith("/tok")
    assert calls == [
        ("POST", "/lease", {"owner": "pytest-mcp", "ttl_seconds": 900, "identity_id": "chrome-work"}),
        ("GET", "/status", None),
        ("POST", "/browser/tabs", {"lease_id": "lease-active"}),
        ("POST", "/browser/snapshot", {"lease_id": "lease-active"}),
        ("POST", "/lease-control/request", {"lease_id": "lease-active", "owner": "pytest-mcp", "ttl_seconds": 900}),
    ]
