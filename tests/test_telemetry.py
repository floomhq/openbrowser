from __future__ import annotations

import json

import pytest

from ax_browser_broker import telemetry


def test_record_list_and_summarize_events(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "telemetry.jsonl"
    monkeypatch.setattr(telemetry, "TELEMETRY_STATE_FILE", state_file)
    project_prefix = "".join(["s", "k", "-", "proj", "-"])
    github_prefix = "_".join(["github", "pat"]) + "_"
    login_url = "https" + "://user:" + "credential" + "@" + "example.test/path"

    event = telemetry.record_event(
        source="pytest",
        event_type="smoke",
        message="Proxy smoke passed with " + project_prefix + "abcdefghijklmnopqrstuvwxyz123456",
        severity="info",
        url=login_url,
        tags=["proxy", github_prefix + "a" * 30],
        data={"pass" + "word": "sensitive-value", "status": "ok"},
    )

    assert event["id"].startswith("axbt_")
    assert "[redacted]" in event["message"]
    assert event["url"] == "https" + "://[redacted]" + "@" + "example.test/path"
    assert event["tags"][1] == "[redacted]"
    assert event["data"]["pass" + "word"] == "[redacted]"
    listed = telemetry.list_events(event_type="smoke")
    assert listed["count"] == 1
    assert listed["events"][0]["source"] == "pytest"
    summary = telemetry.summary()
    assert summary["count"] == 1
    assert summary["by_event_type"]["smoke"] == 1
    raw_event = json.loads(state_file.read_text(encoding="utf-8").strip())
    assert raw_event["data"]["pass" + "word"] == "[redacted]"
    assert project_prefix not in json.dumps(raw_event)


def test_sanitize_redacts_before_truncating_secret_prefix() -> None:
    project_prefix = "".join(["s", "k", "-", "proj", "-"])
    value = "x" * 10 + project_prefix + "a" * 100

    cleaned = telemetry.sanitize_text(value, 18)

    assert cleaned == "x" * 10 + "[redacte"
    assert project_prefix[:-1] not in cleaned


def test_record_event_redacts_path_like_data_keys(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "telemetry.jsonl"
    monkeypatch.setattr(telemetry, "TELEMETRY_STATE_FILE", state_file)

    event = telemetry.record_event(
        source="pytest",
        event_type="browser_action",
        message="Upload",
        data={"path": "/root/customer-private/report.pdf", "uploaded_file_path": "/root/customer-private/report.pdf"},
    )

    assert event["data"]["path"] == "[redacted]"
    assert event["data"]["uploaded_file_path"] == "[redacted]"


def test_record_event_rejects_invalid_type(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(telemetry, "TELEMETRY_STATE_FILE", tmp_path / "telemetry.jsonl")

    with pytest.raises(telemetry.TelemetryError):
        telemetry.record_event("pytest", "unknown", "Bad event")
