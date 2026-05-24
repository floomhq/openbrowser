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
