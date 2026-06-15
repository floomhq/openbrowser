from __future__ import annotations

import io
import json
import urllib.error
from typing import get_args, get_type_hints

import pytest

from ax_browser_broker import remote_mcp_server


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_remote_mcp_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENBROWSER_API_KEY", raising=False)
    monkeypatch.delenv("AX_OPENBROWSER_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENBROWSER_API_KEY is required"):
        remote_mcp_server.openbrowser_health()


def test_remote_mcp_sends_bearer_auth_and_user_agent(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["timeout"] = timeout
        captured["authorization"] = request.get_header("Authorization")
        captured["user_agent"] = request.get_header("User-agent")
        return FakeResponse({"ok": True})

    monkeypatch.setenv("OPENBROWSER_API_KEY", "secret-key")
    monkeypatch.setenv("OPENBROWSER_BASE_URL", "https://broker.example/openbrowser/v1/")
    monkeypatch.setenv("OPENBROWSER_USER_AGENT", "pytest-agent/1.0")
    monkeypatch.setattr(remote_mcp_server.urllib.request, "urlopen", fake_urlopen)

    result = remote_mcp_server.broker_audit(hours=3)

    assert result == {"ok": True}
    assert captured == {
        "url": "https://broker.example/openbrowser/v1/audit?hours=3",
        "method": "GET",
        "timeout": 60,
        "authorization": "Bearer secret-key",
        "user_agent": "pytest-agent/1.0",
    }


def test_remote_mcp_browser_lease_posts_to_public_api(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"lease_id": "lease-123", "identity_id": "chrome-work"})

    monkeypatch.setenv("OPENBROWSER_API_KEY", "secret-key")
    monkeypatch.delenv("OPENBROWSER_BASE_URL", raising=False)
    monkeypatch.setattr(remote_mcp_server.urllib.request, "urlopen", fake_urlopen)

    result = remote_mcp_server.browser_lease(owner="pytest", identity_id="chrome-work", ttl_seconds=120)

    assert captured["url"] == "http://127.0.0.1:8767/openbrowser/v1/leases"
    assert captured["body"] == {"owner": "pytest", "ttl_seconds": 120, "identity_id": "chrome-work"}
    assert result["lease_id"] == "lease-123"


def test_remote_mcp_browser_open_control_posts_one_step_payload(monkeypatch) -> None:
    captured = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        captured.append((request.full_url, body))
        if request.full_url.endswith("/open"):
            return FakeResponse({"lease": {"lease_id": "lease-remote"}, "navigation": {"url": "https://lovable.dev/dashboard"}})
        if request.full_url.endswith("/browser/snapshot"):
            return FakeResponse({"title": "Home | Lovable", "url": "https://lovable.dev/dashboard", "bodyText": "C" * 1400, "elements": [{}], "slot": "pool-b"})
        if request.full_url.endswith("/browser/screenshot"):
            return FakeResponse({"path": "/tmp/shot.png", "base64": "image-data"})
        if request.full_url.endswith("/takeover/request"):
            return FakeResponse({"portal_url": "https://browser.example.com/auth/lease-control/tok"})
        raise AssertionError(request.full_url)

    monkeypatch.setenv("OPENBROWSER_API_KEY", "secret-key")
    monkeypatch.setattr(remote_mcp_server.urllib.request, "urlopen", fake_urlopen)

    result = remote_mcp_server.browser_open_control(
        owner="pytest",
        url="https://lovable.dev",
        identity_id="chrome-work",
        ttl_seconds=600,
        control_ttl_seconds=300,
        screenshot=True,
    )

    assert captured == [
        (
            "http://127.0.0.1:8767/openbrowser/v1/open",
            {"owner": "pytest", "url": "https://lovable.dev", "identity_id": "chrome-work", "ttl_seconds": 600},
        ),
        ("http://127.0.0.1:8767/openbrowser/v1/browser/snapshot", {"lease_id": "lease-remote"}),
        ("http://127.0.0.1:8767/openbrowser/v1/browser/screenshot", {"lease_id": "lease-remote", "full_page": False}),
        (
            "http://127.0.0.1:8767/openbrowser/v1/takeover/request",
            {"lease_id": "lease-remote", "owner": "pytest", "ttl_seconds": 300},
        ),
    ]
    assert result["portal_url"].endswith("/tok")
    assert result["takeover"]["portal_url"].endswith("/tok")
    assert result["snapshot"]["bodyText"] == "C" * 300
    assert result["snapshot"]["body_text_length"] == 1400
    assert "base64" not in result["screenshot"]


def test_remote_mcp_takeover_request_posts_primary_endpoint(monkeypatch) -> None:
    captured = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        captured.append((request.full_url, body))
        return FakeResponse({"portal_url": "https://browser.example.com/auth/lease-control/tok"})

    monkeypatch.setenv("OPENBROWSER_API_KEY", "secret-key")
    monkeypatch.setattr(remote_mcp_server.urllib.request, "urlopen", fake_urlopen)

    result = remote_mcp_server.takeover_request("lease-remote", owner="pytest", ttl_seconds=300)

    assert captured == [
        (
            "http://127.0.0.1:8767/openbrowser/v1/takeover/request",
            {"lease_id": "lease-remote", "owner": "pytest", "ttl_seconds": 300},
        )
    ]
    assert result["portal_url"].endswith("/tok")


def test_remote_mcp_lease_control_request_is_takeover_alias(monkeypatch) -> None:
    captured = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        captured.append((request.full_url, body))
        return FakeResponse({"portal_url": "https://browser.example.com/auth/lease-control/tok"})

    monkeypatch.setenv("OPENBROWSER_API_KEY", "secret-key")
    monkeypatch.setattr(remote_mcp_server.urllib.request, "urlopen", fake_urlopen)

    result = remote_mcp_server.lease_control_request("lease-remote", owner="pytest", ttl_seconds=300)

    assert captured == [
        (
            "http://127.0.0.1:8767/openbrowser/v1/takeover/request",
            {"lease_id": "lease-remote", "owner": "pytest", "ttl_seconds": 300},
        ),
    ]
    assert result["portal_url"].endswith("/tok")


def test_remote_mcp_auth_request_forwards_vnc_options_by_default(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"mode": "vnc"})

    monkeypatch.setenv("OPENBROWSER_API_KEY", "secret-key")
    monkeypatch.setattr(remote_mcp_server.urllib.request, "urlopen", fake_urlopen)

    result = remote_mcp_server.auth_request(
        owner="pytest",
        url="https://example.com/login",
        identity_id="work-main",
        ttl_seconds=120,
        control_ttl_seconds=180,
        wait_until="load",
        verify=False,
    )

    assert result == {"mode": "vnc"}
    assert captured == {
        "url": "http://127.0.0.1:8767/openbrowser/v1/auth/request",
        "body": {
            "owner": "pytest",
            "url": "https://example.com/login",
            "reason": "login_required",
            "identity_id": "work-main",
            "mode": "vnc",
            "ttl_seconds": 120,
            "control_ttl_seconds": 180,
            "wait_until": "load",
            "verify": False,
        },
    }


def test_remote_mcp_auth_request_mode_annotation_exposes_enum() -> None:
    assert get_args(get_type_hints(remote_mcp_server.auth_request)["mode"]) == ("vnc", "lease_control")


def test_remote_mcp_http_errors_are_actionable(monkeypatch) -> None:
    def fake_urlopen(_request, timeout):
        assert timeout == 60
        raise urllib.error.HTTPError(
            url="https://broker.example/openbrowser/v1/leases",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"detail":"Unauthorized"}'),
        )

    monkeypatch.setenv("OPENBROWSER_API_KEY", "bad-key")
    monkeypatch.setattr(remote_mcp_server.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match='HTTP 401: \\{"detail":"Unauthorized"\\}'):
        remote_mcp_server.browser_lease(owner="pytest")
