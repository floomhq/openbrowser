from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from ax_browser_broker import api, auth, feedback, lease_control, telemetry
from ax_browser_broker.pool import Lease


def make_lease() -> Lease:
    return Lease(
        lease_id="lease-api",
        name="pool-b",
        port=9224,
        owner="pytest",
        created_at=1,
        heartbeat_at=1,
        expires_at=2,
        cdp="http://127.0.0.1:9224",
        profile_dir="/tmp/profile",
    )


def test_auth_portal_escapes_request_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", tmp_path / "auth.json")

    request = auth.create_auth_request("<owner>", "https://example.com/?x=<script>")
    client = TestClient(api.app)
    response = client.get("/auth/" + request["token"])

    assert response.status_code == 200
    assert "https://example.com/?x=<script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "&lt;owner&gt;" in response.text


def test_auth_portal_autostarts_and_embeds_password_for_trusted_ip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", tmp_path / "auth.json")
    monkeypatch.setattr(api, "AUTH_PORTAL_AUTOSTART", True)
    monkeypatch.setattr(api, "AUTH_TRUST_X_FORWARDED_FOR", True)
    monkeypatch.setattr(api, "AUTH_TRUSTED_CIDRS", ("203.0.113.10/32",))
    monkeypatch.setattr(api, "current_auth_vnc", lambda _token: None)
    monkeypatch.setattr(
        api,
        "start_auth_vnc",
        lambda token: {
            "token": token,
            "display": ":870",
            "websocket_url": "https://browser.example.com/vnc.html?autoconnect=1&resize=remote",
            "local_websocket_url": "http://127.0.0.1:6081/vnc.html?autoconnect=1&resize=remote",
            "websocket_port": 6081,
            "vnc_port": 5901,
            "password": "trust-pass",
        },
    )

    request = auth.create_auth_request("tester", "https://example.com/login", identity_id=None)
    client = TestClient(api.app)
    response = client.get("/auth/" + request["token"], headers={"x-forwarded-for": "203.0.113.10"})

    assert response.status_code == 200
    assert "OpenBrowser" in response.text
    assert "The browser API for AI agents" in response.text
    assert "Browser Sessions" in response.text
    assert "Live Browser Session" in response.text
    assert "Session State" in response.text
    assert "Night mode" in response.text
    assert "resize=scale" in response.text
    assert "resize=remote" not in response.text
    assert "#password=trust-pass" in response.text
    assert "Trusted connection" in response.text
    assert 'data-async-action="Auth handoff marked complete"' in response.text
    assert "Temporary VNC password" not in response.text


def test_auth_portal_keeps_password_prompt_for_untrusted_ip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", tmp_path / "auth.json")
    monkeypatch.setattr(api, "AUTH_PORTAL_AUTOSTART", True)
    monkeypatch.setattr(api, "AUTH_TRUST_X_FORWARDED_FOR", False)
    monkeypatch.setattr(api, "AUTH_TRUSTED_CIDRS", ("203.0.113.10/32",))
    monkeypatch.setattr(api, "current_auth_vnc", lambda _token: None)
    monkeypatch.setattr(
        api,
        "start_auth_vnc",
        lambda token: {
            "token": token,
            "display": ":870",
            "websocket_url": "https://browser.example.com/vnc.html?autoconnect=1&resize=remote",
            "local_websocket_url": "http://127.0.0.1:6081/vnc.html?autoconnect=1&resize=remote",
            "websocket_port": 6081,
            "vnc_port": 5901,
            "password": "manual-pass",
        },
    )

    request = auth.create_auth_request("tester", "https://example.com/login")
    client = TestClient(api.app)
    response = client.get("/auth/" + request["token"], headers={"x-forwarded-for": "203.0.113.10"})

    assert response.status_code == 200
    assert "Human auth request" in response.text
    assert "resize=scale" in response.text
    assert "resize=remote" not in response.text
    assert "Temporary VNC password" in response.text
    assert "enter it in the browser prompt" in response.text
    assert "manual-pass" in response.text
    assert "#password=manual-pass" not in response.text
    assert 'id="authPasswordCard"' in response.text
    assert 'id="showPasswordCard"' in response.text
    assert "authPasswordCard?.classList.add('is-hidden')" in response.text
    assert "showPasswordCard?.classList.add('is-visible')" in response.text


def test_auth_portal_reuses_existing_vnc_without_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", tmp_path / "auth.json")
    monkeypatch.setattr(api, "AUTH_PORTAL_AUTOSTART", True)
    monkeypatch.setattr(api, "AUTH_TRUSTED_CIDRS", ())
    started = []
    monkeypatch.setattr(
        api,
        "current_auth_vnc",
        lambda _token: {
            "token": "tok",
            "display": ":870",
            "websocket_url": "https://browser.example.com/vnc.html?autoconnect=1&resize=remote",
            "password": "existing-pass",
        },
    )
    monkeypatch.setattr(api, "start_auth_vnc", lambda _token: started.append(_token) or {})

    request = auth.create_auth_request("tester", "https://example.com/login")
    client = TestClient(api.app)
    response = client.get("/auth/" + request["token"])

    assert response.status_code == 200
    assert "resize=scale" in response.text
    assert "resize=remote" not in response.text
    assert "existing-pass" in response.text
    assert started == []


def test_auth_portal_rejects_expired_requests_before_vnc(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", tmp_path / "auth.json")
    monkeypatch.setattr(api, "AUTH_PORTAL_AUTOSTART", True)
    started = []
    monkeypatch.setattr(api, "start_auth_vnc", lambda _token: started.append(_token) or {})

    request = auth.create_auth_request("tester", "https://example.com/login")
    data = auth.json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    data["requests"][request["token"]]["expires_at"] = 1
    (tmp_path / "auth.json").write_text(auth.json.dumps(data), encoding="utf-8")

    client = TestClient(api.app)
    response = client.get("/auth/" + request["token"])

    assert response.status_code == 410
    assert started == []
    assert "vnc.html" not in response.text


def test_auth_complete_returns_gone_for_expired_request(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", tmp_path / "auth.json")

    request = auth.create_auth_request("tester", "https://example.com/login")
    data = auth.json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    data["requests"][request["token"]]["expires_at"] = 1
    (tmp_path / "auth.json").write_text(auth.json.dumps(data), encoding="utf-8")

    client = TestClient(api.app)
    response = client.post("/auth/" + request["token"] + "/complete")

    assert response.status_code == 410


def test_auth_portal_redirects_active_identity_to_lease_control(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "get_pending_auth_request",
        lambda _token: {
            "token": "tok",
            "owner": "agent",
            "url": "https://app.slack.com/",
            "reason": "login_required",
            "status": "pending",
            "identity_id": "work-main",
        },
    )
    monkeypatch.setattr(api, "current_auth_vnc", lambda _token: None)
    monkeypatch.setattr(api, "AUTH_PORTAL_AUTOSTART", True)
    monkeypatch.setattr(api, "start_auth_vnc", lambda _token: (_ for _ in ()).throw(api.AuthError("Identity is actively leased: work-main")))
    monkeypatch.setattr(
        api,
        "status",
        lambda: {
            "leases": {
                "lease-one": {
                    "lease_id": "lease-one",
                    "identity_id": "work-main",
                }
            }
        },
    )
    monkeypatch.setattr(
        api,
        "create_control_session",
        lambda owner, lease_id, **_kwargs: {
            "token": "control-token",
            "owner": owner,
            "lease_id": lease_id,
            "portal_url": "https://browser.example.com/auth/lease-control/control-token",
        },
    )
    monkeypatch.setattr(api, "_safe_record_event", lambda **_kwargs: {"id": "event"})
    client = TestClient(api.app)

    response = client.get("/auth/tok", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "https://browser.example.com/auth/lease-control/control-token"


def test_lifespan_starts_and_stops_controller(monkeypatch) -> None:
    events = []

    async def fake_start() -> None:
        events.append("start")

    async def fake_stop() -> None:
        events.append("stop")

    monkeypatch.setattr(api.controller, "start", fake_start)
    monkeypatch.setattr(api.controller, "stop", fake_stop)

    with TestClient(api.app) as client:
        assert client.get("/status").status_code == 200

    assert events == ["start", "stop"]


def test_agent_docs_endpoint() -> None:
    client = TestClient(api.app)

    response = client.get("/agent-docs?topic=feedback")

    assert response.status_code == 200
    assert response.json()["topic"] == "feedback"


def test_audit_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(api, "run_audit", lambda hours=24: {"score": 100, "window_hours": hours})
    client = TestClient(api.app)

    response = client.get("/audit?hours=3")

    assert response.status_code == 200
    assert response.json() == {"score": 100, "window_hours": 3}


def test_openbrowser_dashboard_explains_remote_setup_without_leaking_key(tmp_path, monkeypatch) -> None:
    key_file = tmp_path / "keys.json"
    key_file.write_text('{"tokens":{"test":"super-secret-openbrowser-key"}}', encoding="utf-8")
    monkeypatch.setattr(api, "OPENBROWSER_API_KEYS_FILE", key_file)
    monkeypatch.setattr(api, "PUBLIC_OPENBROWSER_BASE_URL", "https://browser.example.com")
    client = TestClient(api.app)

    response = client.get("/openbrowser")

    assert response.status_code == 200
    assert "Remote API base" in response.text
    assert "https://browser.example.com/openbrowser/v1" in response.text
    assert "openbrowser-remote-mcp" in response.text
    assert "OPENBROWSER_API_KEY" in response.text
    assert "Identities And Proxies" in response.text
    assert "Sessions And Audit" in response.text
    assert "Operator console" in response.text
    assert "Public broker summary" in response.text
    assert 'href="/openbrowser/reference"' in response.text
    assert "sessionStorage" in response.text
    assert "textContent = String(title" in response.text
    assert "replaceChildren" in response.text
    assert "innerHTML" not in response.text
    assert "white-space: pre-wrap" in response.text
    assert "overflow-wrap: break-word" in response.text
    assert "Copy failed" in response.text
    assert "data-copy-target=\"remoteMcpSnippet\"" in response.text
    assert "API Smoke Test" in response.text
    assert "Live operator summary" in response.text
    assert "apiBaseUrl = new URL('/openbrowser/v1', window.location.origin)" in response.text
    assert "Live load failed:" in response.text
    assert "telemetry.count" in response.text
    assert "super-secret-openbrowser-key" not in response.text


def test_openbrowser_reference_is_specific_to_openbrowser(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "PUBLIC_OPENBROWSER_BASE_URL", "https://browser.example.com")
    client = TestClient(api.app)

    response = client.get("/openbrowser/reference")

    assert response.status_code == 200
    assert "OpenBrowser API Reference" in response.text
    assert "https://browser.example.com/openbrowser/v1" in response.text
    assert "POST" in response.text
    assert "/browser/keyboard-type" in response.text
    assert "/auth/batch" in response.text
    assert "/profiles/status" in response.text
    assert "/browser/upload" in response.text
    assert "/telemetry/events" in response.text


def test_openbrowser_dashboard_canonicalizes_full_base_url(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "OPENBROWSER_API_KEYS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(api, "PUBLIC_OPENBROWSER_BASE_URL", "https://browser.example.com/openbrowser/v1")
    client = TestClient(api.app)

    response = client.get("/openbrowser")

    assert response.status_code == 200
    assert "https://browser.example.com/openbrowser/v1/openbrowser/v1" not in response.text
    assert "Bearer token" in response.text
    assert "Missing" in response.text


def test_openbrowser_dashboard_script_escapes_configured_base_url(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "OPENBROWSER_API_KEYS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(api, "PUBLIC_OPENBROWSER_BASE_URL", "https://example.com</script><script>alert(1)</script>")
    client = TestClient(api.app)

    response = client.get("/openbrowser")

    assert response.status_code == 200
    assert "</script><script>alert(1)</script>" not in response.text
    assert "\\u003c/script\\u003e\\u003cscript\\u003ealert(1)\\u003c/script\\u003e/openbrowser/v1" in response.text


def test_openbrowser_api_requires_bearer_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")
    client = TestClient(api.app)

    missing = client.get("/openbrowser/v1/docs")
    wrong = client.get("/openbrowser/v1/docs", headers={"authorization": "Bearer wrong"})
    ok = client.get("/openbrowser/v1/docs", headers={"authorization": "Bearer test-openbrowser-key"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert ok.status_code == 200
    assert ok.json()["service"] == "openbrowser"
    assert ok.json()["dashboard"] == "/openbrowser"
    assert ok.json()["endpoints"]["keyboard_type"] == "POST /openbrowser/v1/browser/keyboard-type"
    assert ok.json()["endpoints"]["keyboard_press"] == "POST /openbrowser/v1/browser/keyboard-press"
    assert ok.json()["endpoints"]["upload"] == "POST /openbrowser/v1/browser/upload"


def test_openbrowser_health_redacts_profile_paths(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")
    monkeypatch.setattr(
        api,
        "status",
        lambda: {
            "slots": [
                {
                    "name": "pool-a",
                    "profile_dir": "/var/lib/openbrowser/pool/profiles/pool-a",
                    "healthy": True,
                }
            ],
            "leases": {
                "lease-one": {
                    "lease_id": "lease-one",
                    "profile_dir": "/var/lib/openbrowser/pool/profiles/pool-a",
                    "owner": "pytest",
                }
            },
            "expired": [],
        },
    )
    client = TestClient(api.app)

    response = client.get("/openbrowser/v1/health", headers={"authorization": "Bearer test-openbrowser-key"})

    assert response.status_code == 200
    body = response.json()
    assert body["pool"]["slots"][0]["name"] == "pool-a"
    assert "profile_dir" not in body["pool"]["slots"][0]
    assert "profile_dir" not in body["pool"]["leases"]["lease-one"]
    assert "/var/lib/openbrowser/pool" not in response.text


def test_openbrowser_docs_reflect_live_identity_metadata(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")
    monkeypatch.setattr(
        api,
        "redacted_status",
        lambda: {
            "identities": {
                "chrome-test": {
                    "label": "Test",
                    "proxy_ref": "residential:test",
                    "max_parallel_sessions": 2,
                    "active_on_slot": True,
                    "profile_dir": "/secret/profile",
                }
            },
            "proxy_refs": ["residential:test"],
        },
    )
    client = TestClient(api.app)

    response = client.get("/openbrowser/v1/docs", headers={"authorization": "Bearer test-openbrowser-key"})

    assert response.status_code == 200
    configured = response.json()["identities"]["configured"]["chrome-test"]
    assert configured["label"] == "Test"
    assert configured["proxy_ref"] == "residential:test"
    assert configured["max_parallel_sessions"] == 2
    assert "profile_dir" not in configured


def test_openbrowser_identities_requires_key_and_returns_redacted_status(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")
    monkeypatch.setattr(api, "redacted_status", lambda: {"identities": {"chrome-one": {"label": "One"}}, "proxy_refs": []})
    client = TestClient(api.app)

    missing = client.get("/openbrowser/v1/identities")
    ok = client.get("/openbrowser/v1/identities", headers={"authorization": "Bearer test-openbrowser-key"})

    assert missing.status_code == 401
    assert ok.status_code == 200
    assert ok.json()["identities"]["chrome-one"]["label"] == "One"


def test_openbrowser_auth_request_is_protected(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")
    monkeypatch.setattr(api, "status", lambda: {"leases": {}})
    monkeypatch.setattr(
        api,
        "create_auth_request",
        lambda owner, url, reason, identity_id: {
            "token": "tok",
            "owner": owner,
            "url": url,
            "reason": reason,
            "identity_id": identity_id,
            "portal_url": "https://browser.example.com/auth/tok",
            "status": "pending",
        },
    )
    monkeypatch.setattr(api, "_safe_record_event", lambda **_kwargs: {"id": "event"})
    client = TestClient(api.app)

    missing = client.post(
        "/openbrowser/v1/auth/request",
        json={"owner": "pytest", "identity_id": "chrome-one", "url": "https://accounts.google.com/"},
    )
    ok = client.post(
        "/openbrowser/v1/auth/request",
        json={"owner": "pytest", "identity_id": "chrome-one", "url": "https://accounts.google.com/"},
        headers={"authorization": "Bearer test-openbrowser-key"},
    )

    assert missing.status_code == 401
    assert ok.status_code == 200
    assert ok.json()["portal_url"].endswith("/auth/tok")
    assert ok.json()["identity_id"] == "chrome-one"


def test_openbrowser_auth_request_accepts_legacy_profile_alias(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")
    monkeypatch.setattr(api, "status", lambda: {"leases": {}})
    created = []

    def fake_create_auth_request(owner, url, reason, identity_id):
        created.append((owner, url, reason, identity_id))
        return {
            "token": "tok",
            "owner": owner,
            "url": url,
            "reason": reason,
            "identity_id": identity_id,
            "portal_url": "https://browser.example.com/auth/tok",
            "status": "pending",
        }

    monkeypatch.setattr(api, "create_auth_request", fake_create_auth_request)
    monkeypatch.setattr(api, "_safe_record_event", lambda **_kwargs: {"id": "event"})
    client = TestClient(api.app)

    response = client.post(
        "/openbrowser/v1/auth/request",
        json={"owner": "pytest", "profile": "work-main", "url": "https://lovable.dev/"},
        headers={"authorization": "Bearer test-openbrowser-key"},
    )

    assert response.status_code == 200
    assert response.json()["identity_id"] == "work-main"
    assert created == [("pytest", "https://lovable.dev/", "login_required", "work-main")]


def test_openbrowser_auth_request_returns_lease_control_for_active_identity(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")
    monkeypatch.setattr(
        api,
        "status",
        lambda: {
            "leases": {
                "lease-active": {
                    "lease_id": "lease-active",
                    "identity_id": "work-main",
                }
            }
        },
    )
    monkeypatch.setattr(
        api,
        "create_control_session",
        lambda owner, lease_id: {
            "token": "control-token",
            "owner": owner,
            "lease_id": lease_id,
            "portal_url": "https://browser.example.com/auth/lease-control/control-token",
            "local_portal_url": "http://127.0.0.1:8767/auth/lease-control/control-token",
        },
    )
    monkeypatch.setattr(api, "create_auth_request", lambda *_args: (_ for _ in ()).throw(AssertionError("must not create pending auth request")))
    monkeypatch.setattr(api, "_safe_record_event", lambda **_kwargs: {"id": "event"})
    client = TestClient(api.app)

    response = client.post(
        "/openbrowser/v1/auth/request",
        json={"owner": "pytest", "profile": "work-main", "url": "https://lovable.dev/"},
        headers={"authorization": "Bearer test-openbrowser-key"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "active_identity_leased"
    assert response.json()["identity_id"] == "work-main"
    assert response.json()["active_lease_id"] == "lease-active"
    assert response.json()["portal_url"].endswith("/auth/lease-control/control-token")
    assert "Inspect tabs/snapshot/screenshot" in response.json()["warning"]


def test_openbrowser_auth_request_rejects_conflicting_profile_alias(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")
    monkeypatch.setattr(api, "create_auth_request", lambda *_args: (_ for _ in ()).throw(AssertionError("must not create auth request")))
    client = TestClient(api.app)

    response = client.post(
        "/openbrowser/v1/auth/request",
        json={
            "owner": "pytest",
            "identity_id": "chrome-one",
            "profile": "chrome-two",
            "url": "https://lovable.dev/",
        },
        headers={"authorization": "Bearer test-openbrowser-key"},
    )

    assert response.status_code == 422
    assert "profile and identity_id must match" in response.text


def test_openbrowser_auth_request_rejects_unknown_identity(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")
    client = TestClient(api.app)

    response = client.post(
        "/openbrowser/v1/auth/request",
        json={"owner": "pytest", "identity_id": "missing-identity", "url": "https://example.com/"},
        headers={"authorization": "Bearer test-openbrowser-key"},
    )

    assert response.status_code == 400
    assert "Identity not found: missing-identity" in response.text


def test_openbrowser_auth_batch_creates_requests(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")
    created = []

    def fake_create_auth_request(owner, url, reason, identity_id):
        created.append((owner, url, reason, identity_id))
        return {
            "token": identity_id + "-token",
            "owner": owner,
            "url": url,
            "reason": reason,
            "identity_id": identity_id,
            "portal_url": f"https://browser.example.com/auth/{identity_id}-token",
            "status": "pending",
        }

    monkeypatch.setattr(api, "create_auth_request", fake_create_auth_request)
    monkeypatch.setattr(api, "_safe_record_event", lambda **_kwargs: {"id": "event"})
    client = TestClient(api.app)

    response = client.post(
        "/openbrowser/v1/auth/batch",
        json={"owner": "pytest", "identity_ids": ["chrome-one", "chrome-two"], "url": "https://accounts.google.com/"},
        headers={"authorization": "Bearer test-openbrowser-key"},
    )

    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert [item["identity_id"] for item in response.json()["requests"]] == ["chrome-one", "chrome-two"]
    assert created == [
        ("pytest", "https://accounts.google.com/", "profile_login", "chrome-one"),
        ("pytest", "https://accounts.google.com/", "profile_login", "chrome-two"),
    ]


def test_openbrowser_ops_endpoints_are_protected(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")
    monkeypatch.setattr(api, "run_audit", lambda hours=24: {"score": 100, "window_hours": hours})
    monkeypatch.setattr(api, "profile_status", lambda: {"profiles": {"count": 1}})
    client = TestClient(api.app)

    missing_audit = client.get("/openbrowser/v1/audit")
    ok_audit = client.get(
        "/openbrowser/v1/audit?hours=2",
        headers={"authorization": "Bearer test-openbrowser-key"},
    )
    ok_profiles = client.get(
        "/openbrowser/v1/profiles/status",
        headers={"authorization": "Bearer test-openbrowser-key"},
    )

    assert missing_audit.status_code == 401
    assert ok_audit.status_code == 200
    assert ok_audit.json() == {"score": 100, "window_hours": 2}
    assert ok_profiles.json()["profiles"]["count"] == 1


def test_openbrowser_feedback_and_telemetry_are_protected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")
    monkeypatch.setattr(feedback, "ISSUE_STATE_FILE", tmp_path / "issues.json")
    monkeypatch.setattr(telemetry, "TELEMETRY_STATE_FILE", tmp_path / "telemetry.jsonl")
    client = TestClient(api.app)

    missing_issue = client.post(
        "/openbrowser/v1/feedback/issues",
        json={"source": "pytest", "title": "Blocked", "details": "Browser failed."},
    )
    created_issue = client.post(
        "/openbrowser/v1/feedback/issues",
        json={"source": "pytest", "title": "Blocked", "details": "Browser failed."},
        headers={"authorization": "Bearer test-openbrowser-key"},
    )
    issue_id = created_issue.json()["id"]
    listed = client.get(
        "/openbrowser/v1/feedback/issues?status=open",
        headers={"authorization": "Bearer test-openbrowser-key"},
    )
    updated = client.post(
        f"/openbrowser/v1/feedback/issues/{issue_id}",
        json={"status": "resolved", "note": "Verified"},
        headers={"authorization": "Bearer test-openbrowser-key"},
    )
    event = client.post(
        "/openbrowser/v1/telemetry/events",
        json={"source": "pytest", "event_type": "smoke", "message": "Remote MCP smoke", "data": {"token": "secret"}},
        headers={"authorization": "Bearer test-openbrowser-key"},
    )
    events = client.get(
        "/openbrowser/v1/telemetry/events?event_type=smoke",
        headers={"authorization": "Bearer test-openbrowser-key"},
    )
    summary = client.get(
        "/openbrowser/v1/telemetry/summary",
        headers={"authorization": "Bearer test-openbrowser-key"},
    )

    assert missing_issue.status_code == 401
    assert created_issue.status_code == 200
    assert listed.json()["count"] == 1
    assert updated.json()["status"] == "resolved"
    assert event.json()["data"]["token"] == "[redacted]"
    assert events.json()["count"] == 1
    assert summary.json()["by_event_type"]["smoke"] == 1


def test_openbrowser_open_releases_lease_on_navigation_failure(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")
    released = []

    async def fake_create_lease(_request):
        return {"lease_id": "lease-open", "name": "pool-b"}

    async def fake_browser_navigate(_request):
        raise api.LeaseError("navigation failed")

    async def fake_release(lease_id):
        released.append(lease_id)
        return {"released": lease_id, "slot": "pool-b"}

    monkeypatch.setattr(api, "create_lease", fake_create_lease)
    monkeypatch.setattr(api, "browser_navigate", fake_browser_navigate)
    monkeypatch.setattr(api, "release_lease", fake_release)
    client = TestClient(api.app)

    response = client.post(
        "/openbrowser/v1/open",
        json={"owner": "pytest", "url": "https://example.com"},
        headers={"authorization": "Bearer test-openbrowser-key"},
    )

    assert response.status_code == 409
    assert released == ["lease-open"]


def test_openbrowser_open_can_return_verified_control_link(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")

    async def fake_create_lease(_request):
        return {"lease_id": "lease-open", "name": "pool-b", "identity_id": "chrome-work"}

    async def fake_browser_navigate(_request):
        return {"lease_id": "lease-open", "slot": "pool-b", "url": "https://lovable.dev/dashboard", "title": "Home | Lovable"}

    async def fake_browser_snapshot(_request):
        return {
            "lease_id": "lease-open",
            "slot": "pool-b",
            "title": "Home | Lovable",
            "url": "https://lovable.dev/dashboard",
            "bodyText": "A" * 1400,
            "elements": [{"selector": "button"}],
        }

    async def fake_browser_screenshot(_request):
        return {
            "lease_id": "lease-open",
            "slot": "pool-b",
            "path": "/tmp/shot.png",
            "mime_type": "image/png",
            "base64": "secret-image-data",
        }

    async def fake_lease_control(_request):
        return {
            "token": "control-token",
            "lease_id": "lease-open",
            "portal_url": "https://browser.example.com/auth/lease-control/control-token",
        }

    monkeypatch.setattr(api, "create_lease", fake_create_lease)
    monkeypatch.setattr(api, "browser_navigate", fake_browser_navigate)
    monkeypatch.setattr(api, "browser_snapshot", fake_browser_snapshot)
    monkeypatch.setattr(api, "browser_screenshot", fake_browser_screenshot)
    monkeypatch.setattr(api, "lease_control_request", fake_lease_control)
    client = TestClient(api.app)

    response = client.post(
        "/openbrowser/v1/open",
        json={
            "owner": "pytest-open",
            "url": "https://lovable.dev",
            "identity_id": "chrome-work",
            "control": True,
            "screenshot": True,
        },
        headers={"authorization": "Bearer test-openbrowser-key"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["portal_url"].endswith("/auth/lease-control/control-token")
    assert data["snapshot"]["title"] == "Home | Lovable"
    assert data["snapshot"]["bodyText"] == "A" * 1200
    assert data["snapshot"]["element_count"] == 1
    assert data["screenshot"]["path"] == "/tmp/shot.png"
    assert "base64" not in data["screenshot"]


def test_lease_failure_records_telemetry(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(api, "lease", lambda *_args, **_kwargs: (_ for _ in ()).throw(api.LeaseError("No healthy free browser slots")))
    monkeypatch.setattr(api, "record_event", lambda **kwargs: events.append(kwargs) or {"id": "event"})
    client = TestClient(api.app)

    response = client.post(
        "/lease",
        json={"owner": "pytest", "identity_id": "chrome-openpaper", "ttl_seconds": 120},
    )

    assert response.status_code == 409
    assert events[0]["event_type"] == "error"
    assert events[0]["message"] == "Lease failed"
    assert events[0]["data"]["identity_id"] == "chrome-openpaper"


def test_browser_action_failure_records_telemetry(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(api, "require_lease", lambda *_args, **_kwargs: (_ for _ in ()).throw(api.LeaseError("Lease not found")))
    monkeypatch.setattr(api, "record_event", lambda **kwargs: events.append(kwargs) or {"id": "event"})
    client = TestClient(api.app)

    response = client.post("/browser/click", json={"lease_id": "missing-lease", "selector": "#submit"})

    assert response.status_code == 409
    assert events[0]["event_type"] == "error"
    assert events[0]["message"] == "Browser click failed"
    assert events[0]["lease_id"] == "missing-lease"
    assert events[0]["data"]["selector"] == "#submit"


def test_browser_keyboard_type_endpoint_records_text_length_only(monkeypatch) -> None:
    events = []
    lease = make_lease()

    async def fake_keyboard_type(lease_obj, text, selector, delay_ms):
        assert lease_obj == lease
        assert text == "secret-ish message"
        assert selector == "#editor"
        assert delay_ms == 12
        return {
            "lease_id": lease_obj.lease_id,
            "slot": lease_obj.name,
            "selector": selector,
            "typed": True,
            "text_length": len(text),
            "delay_ms": delay_ms,
            "url": "https://example.com",
        }

    monkeypatch.setattr(api, "require_lease", lambda _lease_id: lease)
    monkeypatch.setattr(api.controller, "keyboard_type", fake_keyboard_type)
    monkeypatch.setattr(api, "record_event", lambda **kwargs: events.append(kwargs) or {"id": "event"})
    client = TestClient(api.app)

    response = client.post(
        "/browser/keyboard-type",
        json={"lease_id": "lease-api", "selector": "#editor", "text": "secret-ish message", "delay_ms": 12},
    )

    assert response.status_code == 200
    assert response.json()["text_length"] == 18
    assert events[0]["message"] == "Browser keyboard type"
    assert events[0]["data"]["text_length"] == 18
    assert "secret-ish message" not in str(events[0])


def test_browser_keyboard_press_endpoint_records_key(monkeypatch) -> None:
    events = []
    lease = make_lease()

    async def fake_keyboard_press(lease_obj, key, selector):
        assert lease_obj == lease
        assert key == "Enter"
        assert selector == "#editor"
        return {
            "lease_id": lease_obj.lease_id,
            "slot": lease_obj.name,
            "selector": selector,
            "pressed": key,
            "url": "https://example.com",
        }

    monkeypatch.setattr(api, "require_lease", lambda _lease_id: lease)
    monkeypatch.setattr(api.controller, "keyboard_press", fake_keyboard_press)
    monkeypatch.setattr(api, "record_event", lambda **kwargs: events.append(kwargs) or {"id": "event"})
    client = TestClient(api.app)

    response = client.post(
        "/browser/keyboard-press",
        json={"lease_id": "lease-api", "selector": "#editor", "key": "Enter"},
    )

    assert response.status_code == 200
    assert response.json()["pressed"] == "Enter"
    assert events[0]["message"] == "Browser keyboard press"
    assert events[0]["data"]["key"] == "Enter"


def test_lease_control_request_creates_handoff_link(monkeypatch) -> None:
    events = []
    lease = make_lease()

    def fake_create_control_session(owner, lease_id, ttl_seconds, *, slot=None, identity_id=None):
        assert owner == "pytest-control"
        assert lease_id == "lease-api"
        assert ttl_seconds == 600
        assert slot == "pool-b"
        assert identity_id == lease.identity_id
        return {
            "token": "control-token",
            "owner": owner,
            "lease_id": lease_id,
            "slot": slot,
            "identity_id": identity_id,
            "ttl_seconds": ttl_seconds,
            "portal_url": "https://browser.example.com/auth/lease-control/control-token",
        }

    monkeypatch.setattr(api, "require_lease", lambda _lease_id: lease)
    monkeypatch.setattr(api, "create_control_session", fake_create_control_session)
    monkeypatch.setattr(api, "record_event", lambda **kwargs: events.append(kwargs) or {"id": "event"})
    client = TestClient(api.app)

    response = client.post(
        "/lease-control/request",
        json={"lease_id": "lease-api", "owner": "pytest-control", "ttl_seconds": 600},
    )

    assert response.status_code == 200
    assert response.json()["portal_url"].endswith("/auth/lease-control/control-token")
    assert events[0]["message"] == "Lease control session created"
    assert events[0]["data"]["slot"] == "pool-b"


def test_openbrowser_lease_control_request_is_protected(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEYS", "test-openbrowser-key")

    async def fake_lease_control_request(_request):
        return {"portal_url": "https://browser.example.com/auth/lease-control/tok"}

    monkeypatch.setattr(api, "lease_control_request", fake_lease_control_request)
    client = TestClient(api.app)

    missing = client.post("/openbrowser/v1/lease-control/request", json={"lease_id": "lease-api"})
    ok = client.post(
        "/openbrowser/v1/lease-control/request",
        json={"lease_id": "lease-api"},
        headers={"authorization": "Bearer test-openbrowser-key"},
    )

    assert missing.status_code == 401
    assert ok.status_code == 200
    assert ok.json()["portal_url"].endswith("/auth/lease-control/tok")


def test_lease_control_portal_and_screenshot(monkeypatch) -> None:
    lease = make_lease()

    def fake_get_control_session(_token):
        return {
            "token": "tok",
            "owner": "<human>",
            "lease_id": "lease-api",
            "slot": "pool-a",
            "identity_id": "chrome-depontefede",
            "expires_at": 123,
        }

    async def fake_screenshot(lease_obj, full_page):
        assert lease_obj == lease
        assert full_page is False
        return {"base64": base64.b64encode(b"png-bytes").decode("ascii")}

    monkeypatch.setattr(api, "get_control_session", fake_get_control_session)
    monkeypatch.setattr(api, "require_lease", lambda _lease_id: lease)
    monkeypatch.setattr(api.controller, "screenshot", fake_screenshot)
    client = TestClient(api.app)

    portal = client.get("/auth/lease-control/tok")
    shot = client.get("/auth/lease-control/tok/screenshot")

    assert portal.status_code == 200
    assert "&lt;human&gt;" in portal.text
    assert "OpenBrowser" in portal.text
    assert "Browser Sessions" in portal.text
    assert "Live Browser Session" in portal.text
    assert "Manual control" in portal.text
    assert "chrome-depontefede" in portal.text
    assert "pool-a" in portal.text
    assert "Browser image" in portal.text
    assert "Text to type" in portal.text
    assert "Keyboard key" in portal.text
    assert "Click directly on the screenshot" in portal.text
    assert "Cookies, passwords, saved browser sessions, and proxy credentials stay hidden from this page." in portal.text
    assert 'data-key="PageDown"' in portal.text
    assert 'id="refreshTop"' in portal.text
    assert "Refreshing screenshot..." in portal.text
    assert "Screenshot refreshed" in portal.text
    assert "Unix time" not in portal.text
    assert "OpenBrowser Manual Control" in portal.text
    assert "if (!response.ok) throw new Error" in portal.text
    assert shot.status_code == 200
    assert shot.headers["content-type"] == "image/png"
    assert shot.content == b"png-bytes"


def test_lease_control_click_records_coordinates(monkeypatch) -> None:
    events = []
    lease = make_lease()

    monkeypatch.setattr(
        api,
        "get_control_session",
        lambda _token: {"token": "tok", "owner": "pytest-control", "lease_id": "lease-api"},
    )
    monkeypatch.setattr(api, "require_lease", lambda _lease_id: lease)

    async def fake_mouse_click(lease_obj, x, y):
        assert lease_obj == lease
        return {"lease_id": lease_obj.lease_id, "slot": lease_obj.name, "clicked": {"x": x, "y": y}, "url": "https://example.com"}

    monkeypatch.setattr(api.controller, "mouse_click", fake_mouse_click)
    monkeypatch.setattr(api, "record_event", lambda **kwargs: events.append(kwargs) or {"id": "event"})
    client = TestClient(api.app)

    response = client.post("/auth/lease-control/tok/click", json={"x": 10, "y": 20})

    assert response.status_code == 200
    assert response.json()["clicked"] == {"x": 10, "y": 20}
    assert events[0]["message"] == "Lease control click"
    assert events[0]["data"]["x"] == 10


def test_lease_control_state_lifecycle(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(lease_control, "LEASE_CONTROL_STATE_FILE", tmp_path / "lease_control.json")
    monkeypatch.setattr(lease_control, "PUBLIC_AUTH_BASE_URL", "https://browser.example.com")

    session = lease_control.create_control_session("pytest", "lease-api", ttl_seconds=60)
    loaded = lease_control.get_control_session(session["token"])
    completed = lease_control.complete_control_session(session["token"])

    assert session["portal_url"].startswith("https://browser.example.com/auth/lease-control/")
    assert loaded["lease_id"] == "lease-api"
    assert completed["owner"] == "pytest"


def test_feedback_issue_api(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(feedback, "ISSUE_STATE_FILE", tmp_path / "issues.json")
    monkeypatch.setattr(telemetry, "TELEMETRY_STATE_FILE", tmp_path / "telemetry.jsonl")
    client = TestClient(api.app)

    created = client.post(
        "/feedback/issues",
        json={
            "source": "pytest",
            "title": "Browser issue",
            "details": "Observed failure.",
            "severity": "medium",
            "tags": ["test"],
        },
    )

    assert created.status_code == 200
    issue_id = created.json()["id"]
    listed = client.get("/feedback/issues")
    assert listed.json()["count"] == 1
    resolved = client.post(f"/feedback/issues/{issue_id}", json={"status": "resolved", "note": "Done"})
    assert resolved.json()["status"] == "resolved"
    events = client.get(f"/telemetry/events?issue_id={issue_id}")
    assert events.json()["count"] == 2


def test_telemetry_api_redacts_sensitive_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(telemetry, "TELEMETRY_STATE_FILE", tmp_path / "telemetry.jsonl")
    client = TestClient(api.app)

    created = client.post(
        "/telemetry/events",
        json={
            "source": "pytest",
            "event_type": "smoke",
            "message": "Credential redaction smoke",
            "severity": "info",
            "data": {"token": "abc123", "result": "ok"},
        },
    )

    assert created.status_code == 200
    assert created.json()["data"]["token"] == "[redacted]"
    listed = client.get("/telemetry/events?event_type=smoke")
    assert listed.json()["count"] == 1
    summary = client.get("/telemetry/summary")
    assert summary.json()["by_event_type"]["smoke"] == 1
