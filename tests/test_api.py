from __future__ import annotations

from fastapi.testclient import TestClient

from ax_browser_broker import api, auth, feedback, telemetry


def test_auth_portal_escapes_request_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", tmp_path / "auth.json")

    request = auth.create_auth_request("<owner>", "https://example.com/?x=<script>")
    client = TestClient(api.app)
    response = client.get("/auth/" + request["token"])

    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "&lt;owner&gt;" in response.text


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
