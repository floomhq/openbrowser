from __future__ import annotations

import json

import pytest

from ax_browser_broker import telemetry


def test_record_list_and_summarize_events(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "telemetry.jsonl"
    monkeypatch.setattr(telemetry, "TELEMETRY_STATE_FILE", state_file)

    event = telemetry.record_event(
        source="pytest",
        event_type="smoke",
        message="Proxy smoke passed",
        severity="info",
        tags=["proxy"],
        data={"password": "secret-value", "status": "ok"},
    )

    assert event["id"].startswith("axbt_")
    assert event["data"]["password"] == "[redacted]"
    listed = telemetry.list_events(event_type="smoke")
    assert listed["count"] == 1
    assert listed["events"][0]["source"] == "pytest"
    summary = telemetry.summary()
    assert summary["count"] == 1
    assert summary["by_event_type"]["smoke"] == 1
    raw_event = json.loads(state_file.read_text(encoding="utf-8").strip())
    assert raw_event["data"]["password"] == "[redacted]"


def test_record_event_rejects_invalid_type(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(telemetry, "TELEMETRY_STATE_FILE", tmp_path / "telemetry.jsonl")

    with pytest.raises(telemetry.TelemetryError):
        telemetry.record_event("pytest", "unknown", "Bad event")
