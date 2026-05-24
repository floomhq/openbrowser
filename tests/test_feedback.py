from __future__ import annotations

import json

import pytest

from ax_browser_broker import feedback


def test_report_list_and_resolve_issue(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "issues.json"
    monkeypatch.setattr(feedback, "ISSUE_STATE_FILE", state_file)
    token = "github" + "_pat_" + "a" * 30

    issue = feedback.report_issue(
        source="pytest",
        title=f"Proxy smoke failed {token}",
        details=f"Chrome did not use the proxy. token={token}",
        severity="high",
        tags=["proxy", token],
    )

    assert issue["id"].startswith("axbi_")
    assert token not in json.dumps(issue)
    assert feedback.list_issues()["count"] == 1
    resolved = feedback.update_issue(issue["id"], status="resolved", note=f"Fixed launcher port kill {token}.")
    assert resolved["status"] == "resolved"
    assert resolved["notes"][0]["text"] == "Fixed launcher port kill [redacted]."
    assert feedback.list_issues(status="open")["count"] == 0
    assert json.loads(state_file.read_text(encoding="utf-8"))["issues"][issue["id"]]["status"] == "resolved"


def test_report_issue_rejects_invalid_severity(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(feedback, "ISSUE_STATE_FILE", tmp_path / "issues.json")

    with pytest.raises(feedback.FeedbackError):
        feedback.report_issue("pytest", "Bad", "Details", severity="urgent")
