from __future__ import annotations

import json

import pytest

from ax_browser_broker import feedback


def test_report_list_and_resolve_issue(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "issues.json"
    monkeypatch.setattr(feedback, "ISSUE_STATE_FILE", state_file)

    issue = feedback.report_issue(
        source="pytest",
        title="Proxy smoke failed",
        details="Chrome did not use the proxy.",
        severity="high",
        tags=["proxy", "chrome"],
    )

    assert issue["id"].startswith("axbi_")
    assert feedback.list_issues()["count"] == 1
    resolved = feedback.update_issue(issue["id"], status="resolved", note="Fixed launcher port kill.")
    assert resolved["status"] == "resolved"
    assert resolved["notes"][0]["text"] == "Fixed launcher port kill."
    assert feedback.list_issues(status="open")["count"] == 0
    assert json.loads(state_file.read_text(encoding="utf-8"))["issues"][issue["id"]]["status"] == "resolved"


def test_report_issue_rejects_invalid_severity(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(feedback, "ISSUE_STATE_FILE", tmp_path / "issues.json")

    with pytest.raises(feedback.FeedbackError):
        feedback.report_issue("pytest", "Bad", "Details", severity="urgent")
