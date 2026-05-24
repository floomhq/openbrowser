from __future__ import annotations

import fcntl
import json
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from .config import TELEMETRY_STATE_FILE, ensure_dirs


VALID_SEVERITIES = {"info", "warning", "error", "critical"}
VALID_EVENT_TYPES = {
    "auth",
    "browser_action",
    "docs",
    "error",
    "feedback",
    "issue",
    "lease",
    "profile",
    "proxy",
    "session",
    "smoke",
}
SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "passwd",
    "password",
    "secret",
    "token",
    "totp",
)
MAX_DATA_STRING = 2000
MAX_EVENTS_RETURNED = 500


class TelemetryError(RuntimeError):
    pass


def _clean_text(value: str | None, default: str) -> str:
    cleaned = (value or "").strip()
    return cleaned or default


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            cleaned[text_key] = "[redacted]" if _is_sensitive_key(text_key) else _sanitize(item)
        return cleaned
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:100]]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value[:100]]
    if isinstance(value, str):
        return value[:MAX_DATA_STRING]
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:MAX_DATA_STRING]


def _state_file() -> Path:
    ensure_dirs()
    TELEMETRY_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TELEMETRY_STATE_FILE.touch(mode=0o600, exist_ok=True)
    return TELEMETRY_STATE_FILE


def record_event(
    source: str,
    event_type: str,
    message: str,
    severity: str = "info",
    lease_id: str | None = None,
    issue_id: str | None = None,
    url: str | None = None,
    tags: list[str] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_type = event_type.strip().lower()
    if normalized_type not in VALID_EVENT_TYPES:
        raise TelemetryError(f"Invalid event_type {event_type!r}; use one of {sorted(VALID_EVENT_TYPES)}")
    normalized_severity = severity.strip().lower()
    if normalized_severity not in VALID_SEVERITIES:
        raise TelemetryError(f"Invalid severity {severity!r}; use one of {sorted(VALID_SEVERITIES)}")
    clean_message = message.strip()
    if not clean_message:
        raise TelemetryError("Telemetry message is required")

    event = {
        "id": "axbt_" + uuid.uuid4().hex[:12],
        "created_at": int(time.time()),
        "source": _clean_text(source, "unknown"),
        "event_type": normalized_type,
        "severity": normalized_severity,
        "message": clean_message[:MAX_DATA_STRING],
        "lease_id": lease_id,
        "issue_id": issue_id,
        "url": url,
        "tags": [tag.strip() for tag in (tags or []) if tag.strip()][:25],
        "data": _sanitize(data or {}),
    }
    path = _state_file()
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(event, sort_keys=True) + "\n")
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return event


def _read_events() -> list[dict[str, Any]]:
    path = _state_file()
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return events


def list_events(
    source: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    lease_id: str | None = None,
    issue_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    bounded = max(1, min(int(limit), MAX_EVENTS_RETURNED))
    events = _read_events()
    if source:
        events = [event for event in events if event.get("source") == source]
    if event_type:
        events = [event for event in events if event.get("event_type") == event_type]
    if severity:
        events = [event for event in events if event.get("severity") == severity]
    if lease_id:
        events = [event for event in events if event.get("lease_id") == lease_id]
    if issue_id:
        events = [event for event in events if event.get("issue_id") == issue_id]
    events.sort(key=lambda event: int(event.get("created_at", 0)), reverse=True)
    return {"events": events[:bounded], "count": len(events), "limit": bounded}


def summary(window_seconds: int = 86400) -> dict[str, Any]:
    now = int(time.time())
    bounded_window = max(60, min(int(window_seconds), 60 * 60 * 24 * 30))
    events = [event for event in _read_events() if now - int(event.get("created_at", 0)) <= bounded_window]
    return {
        "window_seconds": bounded_window,
        "count": len(events),
        "by_event_type": dict(Counter(str(event.get("event_type", "unknown")) for event in events)),
        "by_severity": dict(Counter(str(event.get("severity", "unknown")) for event in events)),
        "by_source": dict(Counter(str(event.get("source", "unknown")) for event in events)),
        "latest": sorted(events, key=lambda event: int(event.get("created_at", 0)), reverse=True)[:25],
    }
