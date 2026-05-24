from __future__ import annotations

import fcntl
import json
import time
import uuid
from contextlib import contextmanager
from typing import Any

from .config import ISSUE_STATE_FILE, ensure_dirs


VALID_SEVERITIES = {"low", "medium", "high", "blocker"}
VALID_STATUSES = {"open", "resolved"}


class FeedbackError(RuntimeError):
    pass


@contextmanager
def locked_issues() -> Any:
    ensure_dirs()
    ISSUE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ISSUE_STATE_FILE.exists():
        ISSUE_STATE_FILE.write_text(json.dumps({"issues": {}}, indent=2), encoding="utf-8")
    with ISSUE_STATE_FILE.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        raw = handle.read().strip()
        state = json.loads(raw) if raw else {"issues": {}}
        state.setdefault("issues", {})
        yield state
        handle.seek(0)
        handle.truncate(0)
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def report_issue(
    source: str,
    title: str,
    details: str,
    severity: str = "medium",
    lease_id: str | None = None,
    url: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    normalized = severity.strip().lower()
    if normalized not in VALID_SEVERITIES:
        raise FeedbackError(f"Invalid severity {severity!r}; use one of {sorted(VALID_SEVERITIES)}")
    clean_title = title.strip()
    clean_details = details.strip()
    if not clean_title:
        raise FeedbackError("Issue title is required")
    if not clean_details:
        raise FeedbackError("Issue details are required")
    now = int(time.time())
    issue_id = "axbi_" + uuid.uuid4().hex[:12]
    issue = {
        "id": issue_id,
        "status": "open",
        "severity": normalized,
        "source": source.strip() or "unknown",
        "title": clean_title,
        "details": clean_details,
        "lease_id": lease_id,
        "url": url,
        "tags": [tag.strip() for tag in (tags or []) if tag.strip()],
        "created_at": now,
        "updated_at": now,
        "notes": [],
    }
    with locked_issues() as state:
        state["issues"][issue_id] = issue
    return issue


def list_issues(status: str = "open", limit: int = 50) -> dict[str, Any]:
    if status not in VALID_STATUSES and status != "all":
        raise FeedbackError(f"Invalid status {status!r}; use open, resolved, or all")
    bounded = max(1, min(int(limit), 200))
    with locked_issues() as state:
        items = list(state["issues"].values())
    if status != "all":
        items = [item for item in items if item.get("status") == status]
    items.sort(key=lambda item: int(item.get("updated_at", 0)), reverse=True)
    return {"issues": items[:bounded], "count": len(items), "status": status}


def update_issue(issue_id: str, status: str | None = None, note: str | None = None) -> dict[str, Any]:
    normalized_status = status.strip().lower() if status else None
    if normalized_status and normalized_status not in VALID_STATUSES:
        raise FeedbackError(f"Invalid status {status!r}; use one of {sorted(VALID_STATUSES)}")
    now = int(time.time())
    with locked_issues() as state:
        if issue_id not in state["issues"]:
            raise FeedbackError(f"Issue not found: {issue_id}")
        issue = state["issues"][issue_id]
        if normalized_status:
            issue["status"] = normalized_status
        if note and note.strip():
            issue.setdefault("notes", []).append({"text": note.strip(), "created_at": now})
        issue["updated_at"] = now
        return issue
