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


def test_audit_baseline_ignores_known_raw_cdp(tmp_path, monkeypatch) -> None:
    telemetry_file = tmp_path / "telemetry.jsonl"
    issue_file = tmp_path / "issues.json"
    lease_file = tmp_path / "leases.json"
    baseline_file = tmp_path / "audit_baseline.json"
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
    session_file.write_text(json.dumps({"content": "connect_over_cdp http://127.0.0.1:9222"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(audit, "TELEMETRY_STATE_FILE", telemetry_file)
    monkeypatch.setattr(audit, "ISSUE_STATE_FILE", issue_file)
    monkeypatch.setattr(audit, "POOL_STATE_FILE", lease_file)
    monkeypatch.setattr(audit, "AUDIT_BASELINE_FILE", baseline_file)

    baseline = audit.baseline_current_raw_cdp(hours=1, session_paths=[session_file], now=200)
    result = audit.run_audit(hours=1, session_paths=[session_file], now=200)

    assert baseline["added"] == 1
    assert result["score"] == 100
    assert result["baselined_raw_cdp_bypass_count"] == 1
    assert result["findings"] == []


def test_audit_treats_codex_tui_raw_cdp_as_reference(tmp_path, monkeypatch) -> None:
    telemetry_file = tmp_path / "telemetry.jsonl"
    issue_file = tmp_path / "issues.json"
    lease_file = tmp_path / "leases.json"
    session_file = tmp_path / "codex-tui.log"
    telemetry_file.write_text(
        json.dumps(
            {
                "created_at": 100,
                "source": "agent-a",
                "event_type": "smoke",
                "message": "Audit smoke",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    issue_file.write_text(json.dumps({"issues": {}}), encoding="utf-8")
    lease_file.write_text(json.dumps({"leases": {}}), encoding="utf-8")
    session_file.write_text(
        'ToolCall: exec_command {"cmd":"rg -n \\"connect_over_cdp http://127.0.0.1:9222\\" /root"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "TELEMETRY_STATE_FILE", telemetry_file)
    monkeypatch.setattr(audit, "ISSUE_STATE_FILE", issue_file)
    monkeypatch.setattr(audit, "POOL_STATE_FILE", lease_file)

    result = audit.run_audit(hours=1, session_paths=[session_file], now=200, use_baseline=False)

    assert result["score"] == 100
    assert result["session_logs"]["raw_cdp_reference_mentions"]
    assert result["session_logs"]["raw_cdp_bypass_mentions"] == []


def test_audit_links_issue_log_context(tmp_path, monkeypatch) -> None:
    telemetry_file = tmp_path / "telemetry.jsonl"
    issue_file = tmp_path / "issues.json"
    lease_file = tmp_path / "leases.json"
    session_file = tmp_path / "session.jsonl"
    telemetry_file.write_text(
        json.dumps(
            {
                "created_at": 100,
                "source": "openbrowser",
                "event_type": "error",
                "message": "OpenBrowser adapter failed",
                "lease_id": "lease-a",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    issue_file.write_text(
        json.dumps(
            {
                "issues": {
                    "issue-a": {
                        "id": "issue-a",
                        "status": "open",
                        "severity": "high",
                        "source": "openbrowser",
                        "title": "OpenBrowser adapter exited nonzero",
                        "lease_id": "lease-a",
                        "tags": ["openbrowser", "nonzero-exit"],
                        "created_at": 101,
                        "updated_at": 101,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    lease_file.write_text(json.dumps({"leases": {}}), encoding="utf-8")
    session_file.write_text(
        json.dumps({"content": "openbrowser browser_lease lease-a failed during adapter run"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "TELEMETRY_STATE_FILE", telemetry_file)
    monkeypatch.setattr(audit, "ISSUE_STATE_FILE", issue_file)
    monkeypatch.setattr(audit, "POOL_STATE_FILE", lease_file)

    result = audit.run_audit(hours=1, session_paths=[session_file], now=200)

    assert result["issue_log_contexts"]["issue-a"][0]["bucket"] == "broker_failure_mentions"
    open_issue = next(finding for finding in result["findings"] if finding["code"] == "open_issue")
    assert open_issue["log_context_count"] == 1


def test_audit_links_issue_context_without_broker_marker(tmp_path, monkeypatch) -> None:
    telemetry_file = tmp_path / "telemetry.jsonl"
    issue_file = tmp_path / "issues.json"
    lease_file = tmp_path / "leases.json"
    session_file = tmp_path / "session.jsonl"
    telemetry_file.write_text("", encoding="utf-8")
    issue_file.write_text(
        json.dumps(
            {
                "issues": {
                    "issue-b": {
                        "id": "issue-b",
                        "status": "open",
                        "severity": "medium",
                        "source": "other-agent",
                        "title": "Upload timeout",
                        "lease_id": "lease-b",
                        "tags": ["upload"],
                        "created_at": 101,
                        "updated_at": 101,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    lease_file.write_text(json.dumps({"leases": {}}), encoding="utf-8")
    session_file.write_text(
        json.dumps({"content": "lease-b upload failed after file chooser timeout"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "TELEMETRY_STATE_FILE", telemetry_file)
    monkeypatch.setattr(audit, "ISSUE_STATE_FILE", issue_file)
    monkeypatch.setattr(audit, "POOL_STATE_FILE", lease_file)

    result = audit.run_audit(hours=1, session_paths=[session_file], now=200)

    assert result["issue_log_contexts"]["issue-b"][0]["bucket"] == "issue_context_mentions"


def test_audit_links_issue_specific_log_without_generic_failure_terms(tmp_path, monkeypatch) -> None:
    telemetry_file = tmp_path / "telemetry.jsonl"
    issue_file = tmp_path / "issues.json"
    lease_file = tmp_path / "leases.json"
    session_file = tmp_path / "session.jsonl"
    telemetry_file.write_text("", encoding="utf-8")
    issue_file.write_text(
        json.dumps(
            {
                "issues": {
                    "issue-c": {
                        "id": "issue-c",
                        "status": "open",
                        "severity": "medium",
                        "source": "other-agent",
                        "title": "Upload chooser stalled",
                        "tags": ["upload"],
                        "created_at": 101,
                        "updated_at": 101,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    lease_file.write_text(json.dumps({"leases": {}}), encoding="utf-8")
    session_file.write_text(
        json.dumps({"content": "upload chooser stalled on customer file dialog"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "TELEMETRY_STATE_FILE", telemetry_file)
    monkeypatch.setattr(audit, "ISSUE_STATE_FILE", issue_file)
    monkeypatch.setattr(audit, "POOL_STATE_FILE", lease_file)

    result = audit.run_audit(hours=1, session_paths=[session_file], now=200)

    assert result["issue_log_contexts"]["issue-c"][0]["bucket"] == "issue_specific_scan"


def test_audit_default_session_paths_work_with_issue_context_scan(tmp_path, monkeypatch) -> None:
    telemetry_file = tmp_path / "telemetry.jsonl"
    issue_file = tmp_path / "issues.json"
    lease_file = tmp_path / "leases.json"
    session_file = tmp_path / "session.jsonl"
    telemetry_file.write_text("", encoding="utf-8")
    issue_file.write_text(
        json.dumps(
            {
                "issues": {
                    "issue-d": {
                        "id": "issue-d",
                        "status": "open",
                        "severity": "low",
                        "source": "other-agent",
                        "title": "Default path scan",
                        "created_at": 101,
                        "updated_at": 101,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    lease_file.write_text(json.dumps({"leases": {}}), encoding="utf-8")
    session_file.write_text(json.dumps({"content": "default path scan context"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(audit, "TELEMETRY_STATE_FILE", telemetry_file)
    monkeypatch.setattr(audit, "ISSUE_STATE_FILE", issue_file)
    monkeypatch.setattr(audit, "POOL_STATE_FILE", lease_file)
    monkeypatch.setattr(audit, "DEFAULT_SESSION_PATHS", (session_file,))

    result = audit.run_audit(hours=1, now=200)

    assert result["issue_log_contexts"]["issue-d"][0]["bucket"] == "issue_specific_scan"
