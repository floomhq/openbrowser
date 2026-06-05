from __future__ import annotations

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
    assert result["snapshot"]["bodyText"] == "B" * 1200
    assert result["snapshot"]["element_count"] == 2
    assert calls == [
        ("POST", "/lease", {"owner": "pytest-mcp", "ttl_seconds": 600, "identity_id": "chrome-work"}),
        ("POST", "/browser/navigate", {"lease_id": "lease-mcp", "url": "https://lovable.dev"}),
        ("POST", "/browser/snapshot", {"lease_id": "lease-mcp"}),
        ("POST", "/lease-control/request", {"lease_id": "lease-mcp", "owner": "pytest-mcp", "ttl_seconds": 300}),
    ]


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
