from __future__ import annotations

import json

from ax_browser_broker import audit


def test_audit_scores_clean_usage(tmp_path, monkeypatch) -> None:
    telemetry_file = tmp_path / "telemetry.jsonl"
    issue_file = tmp_path / "issues.json"
    lease_file = tmp_path / "leases.json"
    session_file = tmp_path / "session.jsonl"
    telemetry_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "created_at": 100,
                        "source": "agent-a",
                        "event_type": "lease",
                        "message": "Lease created",
                        "lease_id": "lease-a",
                    }
                ),
                json.dumps(
                    {
                        "created_at": 101,
                        "source": "agent-a",
                        "event_type": "browser_action",
                        "message": "Browser navigate",
                        "lease_id": "lease-a",
                    }
                ),
                json.dumps(
                    {
                        "created_at": 102,
                        "source": "broker-api",
                        "event_type": "lease",
                        "message": "Lease released",
                        "lease_id": "lease-a",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    issue_file.write_text(json.dumps({"issues": {}}), encoding="utf-8")
    lease_file.write_text(json.dumps({"leases": {}}), encoding="utf-8")
    session_file.write_text(json.dumps({"content": "Used browser_lease and browser_release"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(audit, "TELEMETRY_STATE_FILE", telemetry_file)
    monkeypatch.setattr(audit, "ISSUE_STATE_FILE", issue_file)
    monkeypatch.setattr(audit, "POOL_STATE_FILE", lease_file)

    result = audit.run_audit(hours=1, session_paths=[session_file], now=200)

    assert result["score"] == 100
    assert result["event_count"] == 3
    assert result["findings"] == []


def test_audit_flags_raw_cdp_and_active_lease(tmp_path, monkeypatch) -> None:
    telemetry_file = tmp_path / "telemetry.jsonl"
    issue_file = tmp_path / "issues.json"
    lease_file = tmp_path / "leases.json"
    session_file = tmp_path / "session.jsonl"
    telemetry_file.write_text(
        json.dumps(
            {
                "created_at": 100,
                "source": "agent-a",
                "event_type": "lease",
                "message": "Lease created",
                "lease_id": "lease-a",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    issue_file.write_text(json.dumps({"issues": {}}), encoding="utf-8")
    lease_file.write_text(json.dumps({"leases": {"lease-a": {"owner": "agent-a", "created_at": 100}}}), encoding="utf-8")
    session_file.write_text(json.dumps({"content": "connect_over_cdp http://127.0.0.1:9222"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(audit, "TELEMETRY_STATE_FILE", telemetry_file)
    monkeypatch.setattr(audit, "ISSUE_STATE_FILE", issue_file)
    monkeypatch.setattr(audit, "POOL_STATE_FILE", lease_file)

    result = audit.run_audit(hours=1, session_paths=[session_file], now=200)
    codes = {finding["code"] for finding in result["findings"]}

    assert result["score"] < 100
    assert "raw_cdp_bypass_mentions" in codes
    assert "active_lease" in codes
