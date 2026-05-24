from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .config import ISSUE_STATE_FILE, POOL_STATE_FILE, TELEMETRY_STATE_FILE


DEFAULT_SESSION_PATHS = (
    Path("/root/.claude/projects"),
    Path("/root/.codex/history.jsonl"),
    Path("/root/.codex/log/codex-tui.log"),
)
RAW_CDP_PATTERN = re.compile(r"(127\.0\.0\.1|localhost):(?:9222|9223|9224|9225)\b|--remote-debugging-port=(?:9222|9223|9224|9225)\b")
BROKER_PATTERN = re.compile(r"ax-browser-broker|ax-browser-mcp|ax-browser-use|ax-openbrowser|browser_lease|/lease|telemetry_|feedback_", re.I)
FAILURE_PATTERN = re.compile(r"\b(error|failed|failure|traceback|exception|blocked|timeout|not found|refused)\b", re.I)
RAW_CDP_BYPASS_PATTERN = re.compile(r"connect_?over_?cdp|curl\s+-[^\n]*https?://(?:127\.0\.0\.1|localhost):9222|chrome-devtools MCP connects to CDP", re.I)
RAW_CDP_REFERENCE_PATTERN = re.compile(r"SKILL\.md|README|docs/|description|Relevant context|attachment|broker_docs|ax-browser-broker", re.I)
MAX_LOG_MATCHES = 50
MAX_SESSION_BYTES = 2_000_000


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return json.loads(raw) if raw else fallback
    except Exception:
        return fallback


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
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
    except Exception:
        return events
    return events


def _iter_session_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(path.rglob("*.jsonl")))
            files.extend(sorted(path.rglob("*.log")))
    return files


def _line_text(line: str) -> str:
    raw = line.strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict):
        parts: list[str] = []
        for key in ("content", "text", "message", "tool_input", "result"):
            value = data.get(key)
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, dict | list):
                parts.append(json.dumps(value, sort_keys=True))
        if parts:
            return " ".join(parts)
        return json.dumps(data, sort_keys=True)
    return raw


def _scan_session_logs(paths: list[Path], since_ts: int) -> dict[str, Any]:
    files = _iter_session_files(paths)
    raw_cdp_bypass: list[dict[str, Any]] = []
    raw_cdp_reference: list[dict[str, Any]] = []
    broker_mentions: list[dict[str, Any]] = []
    failure_mentions: list[dict[str, Any]] = []
    for file_path in files:
        try:
            stat = file_path.stat()
        except OSError:
            continue
        if since_ts and int(stat.st_mtime) < since_ts:
            continue
        try:
            raw_handle = file_path.open("rb")
        except OSError:
            continue
        with raw_handle:
            if stat.st_size > MAX_SESSION_BYTES:
                raw_handle.seek(-MAX_SESSION_BYTES, 2)
                raw_handle.readline()
            raw_lines = raw_handle.readlines()
            for line_no, raw_line in enumerate(raw_lines, start=1):
                text = _line_text(raw_line.decode("utf-8", errors="replace"))
                if not text:
                    continue
                hit = {
                    "file": str(file_path),
                    "line": line_no,
                    "snippet": text[:500],
                }
                if RAW_CDP_PATTERN.search(text):
                    if RAW_CDP_BYPASS_PATTERN.search(text) and not RAW_CDP_REFERENCE_PATTERN.search(text):
                        if len(raw_cdp_bypass) < MAX_LOG_MATCHES:
                            raw_cdp_bypass.append(hit)
                    elif len(raw_cdp_reference) < MAX_LOG_MATCHES:
                        raw_cdp_reference.append(hit)
                if BROKER_PATTERN.search(text) and len(broker_mentions) < MAX_LOG_MATCHES:
                    broker_mentions.append(hit)
                if BROKER_PATTERN.search(text) and FAILURE_PATTERN.search(text) and len(failure_mentions) < MAX_LOG_MATCHES:
                    failure_mentions.append(hit)
    return {
        "files_scanned": len(files),
        "raw_cdp_bypass_mentions": raw_cdp_bypass,
        "raw_cdp_reference_mentions": raw_cdp_reference,
        "broker_mentions": broker_mentions,
        "broker_failure_mentions": failure_mentions,
    }


def _telemetry_events(since_ts: int) -> list[dict[str, Any]]:
    return [event for event in _read_jsonl(TELEMETRY_STATE_FILE) if int(event.get("created_at", 0)) >= since_ts]


def _issues(since_ts: int) -> list[dict[str, Any]]:
    data = _read_json(ISSUE_STATE_FILE, {"issues": {}})
    issues = list(data.get("issues", {}).values()) if isinstance(data, dict) else []
    return [issue for issue in issues if int(issue.get("created_at", 0)) >= since_ts or int(issue.get("updated_at", 0)) >= since_ts]


def _active_leases() -> dict[str, Any]:
    data = _read_json(POOL_STATE_FILE, {"leases": {}})
    return data.get("leases", {}) if isinstance(data, dict) else {}


def run_audit(hours: int = 24, session_paths: list[Path] | None = None, now: int | None = None) -> dict[str, Any]:
    now_ts = int(now or time.time())
    bounded_hours = max(1, min(int(hours), 24 * 30))
    since_ts = now_ts - bounded_hours * 3600
    paths = session_paths or list(DEFAULT_SESSION_PATHS)

    events = _telemetry_events(since_ts)
    issues = _issues(since_ts)
    leases = _active_leases()
    session_scan = _scan_session_logs(paths, since_ts)

    by_source = Counter(str(event.get("source", "unknown")) for event in events)
    by_type = Counter(str(event.get("event_type", "unknown")) for event in events)
    lease_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        lease_id = event.get("lease_id")
        if lease_id:
            lease_events[str(lease_id)].append(event)

    findings: list[dict[str, Any]] = []
    score = 100

    if not events:
        findings.append({"severity": "high", "code": "no_telemetry", "message": "No broker telemetry events found in audit window."})
        score -= 35

    if session_scan["raw_cdp_bypass_mentions"]:
        findings.append(
            {
                "severity": "medium",
                "code": "raw_cdp_bypass_mentions",
                "message": "Session logs contain direct CDP commands that look like broker bypasses.",
                "count": len(session_scan["raw_cdp_bypass_mentions"]),
            }
        )
        score -= min(30, 10 * len(session_scan["raw_cdp_bypass_mentions"]))

    for lease_id, lease in leases.items():
        owner = str(lease.get("owner", "unknown"))
        created_at = int(lease.get("created_at", lease.get("ts", now_ts)))
        age_minutes = max(0, (now_ts - created_at) // 60)
        severity = "medium" if age_minutes < 240 else "high"
        findings.append(
            {
                "severity": severity,
                "code": "active_lease",
                "message": f"Lease {lease_id} is still active for {owner}.",
                "lease_id": lease_id,
                "owner": owner,
                "age_minutes": age_minutes,
            }
        )
        score -= 8 if severity == "medium" else 18

    for lease_id, lease_items in lease_events.items():
        has_create = any(event.get("message") == "Lease created" for event in lease_items)
        has_release = any(event.get("message") == "Lease released" for event in lease_items)
        is_active = lease_id in leases
        if has_create and not has_release and not is_active:
            findings.append(
                {
                    "severity": "high",
                    "code": "missing_release_telemetry",
                    "message": f"Lease {lease_id} has creation telemetry but no release telemetry and is not active.",
                    "lease_id": lease_id,
                }
            )
            score -= 20

    open_issues = [issue for issue in issues if issue.get("status") == "open"]
    for issue in open_issues:
        findings.append(
            {
                "severity": "medium" if issue.get("severity") != "blocker" else "high",
                "code": "open_issue",
                "message": str(issue.get("title", "Open browser broker issue")),
                "issue_id": issue.get("id"),
                "source": issue.get("source"),
            }
        )
        score -= 10 if issue.get("severity") != "blocker" else 20

    if session_scan["broker_failure_mentions"] and not issues:
        findings.append(
            {
                "severity": "medium",
                "code": "failure_mentions_without_issues",
                "message": "Session logs mention broker failures but no issue was filed in the audit window.",
                "count": len(session_scan["broker_failure_mentions"]),
            }
        )
        score -= 15

    score = max(0, min(100, score))
    return {
        "score": score,
        "window_hours": bounded_hours,
        "since_ts": since_ts,
        "event_count": len(events),
        "issue_count": len(issues),
        "active_lease_count": len(leases),
        "by_source": dict(by_source),
        "by_event_type": dict(by_type),
        "findings": findings,
        "session_logs": session_scan,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit AX41 browser broker agent usage.")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--session-path", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    paths = [Path(item) for item in args.session_path] if args.session_path else None
    result = run_audit(hours=args.hours, session_paths=paths)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"score: {result['score']}")
        print(f"events: {result['event_count']}")
        print(f"issues: {result['issue_count']}")
        print(f"active_leases: {result['active_lease_count']}")
        for finding in result["findings"]:
            print(f"- {finding['severity']} {finding['code']}: {finding['message']}")
    return 0 if result["score"] >= 80 else 1


if __name__ == "__main__":
    raise SystemExit(main())
