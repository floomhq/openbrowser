from __future__ import annotations

import io
import json
import urllib.error

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
