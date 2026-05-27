from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .config import AUDIT_BASELINE_FILE, ISSUE_STATE_FILE, POOL_STATE_FILE, TELEMETRY_STATE_FILE
from .telemetry import sanitize_text


DEFAULT_SESSION_PATHS = (
    Path("/root/.claude/projects"),
    Path("/root/.codex/history.jsonl"),
    Path("/root/.codex/log/codex-tui.log"),
)
RAW_CDP_PATTERN = re.compile(r"(127\.0\.0\.1|localhost):(?:9222|9223|9224|9225)\b|--remote-debugging-port=(?:9222|9223|9224|9225)\b")
BROKER_PATTERN = re.compile(r"ax-browser-broker|ax-browser-mcp|ax-browser-use|ax-openbrowser|browser_lease|/lease|telemetry_|feedback_", re.I)
FAILURE_PATTERN = re.compile(r"\b(error|failed|failure|traceback|exception|blocked|timeout|not found|refused)\b", re.I)
ISSUE_CONTEXT_PATTERN = re.compile(r"openbrowser|browser-use|browser|lease-[A-Za-z0-9_-]+|axbt_|issue-|failed|failure|error|exception|timeout", re.I)
RAW_CDP_BYPASS_PATTERN = re.compile(r"connect_?over_?cdp|curl\s+-[^\n]*https?://(?:127\.0\.0\.1|localhost):9222|chrome-devtools MCP connects to CDP", re.I)
RAW_CDP_REFERENCE_PATTERN = re.compile(r"SKILL\.md|README|docs/|description|Relevant context|attachment|broker_docs|ax-browser-broker", re.I)
HISTORY_REFERENCE_FILES = {"history.jsonl"}
LEASE_TERMINAL_MESSAGES = {"Lease released", "Lease expired"}
MAX_LOG_MATCHES = 50
MAX_ISSUE_CANDIDATES = 200
MAX_SESSION_BYTES = 2_000_000
MAX_ISSUE_CONTEXT_HITS = 8


def _hit_fingerprint(hit: dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "file": hit.get("file"),
            "line": hit.get("line"),
            "snippet": hit.get("snippet"),
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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


def _baseline_fingerprints(path: Path | None = None) -> set[str]:
    path = path or AUDIT_BASELINE_FILE
    data = _read_json(path, {"raw_cdp_bypass_fingerprints": []})
    if not isinstance(data, dict):
        return set()
    values = data.get("raw_cdp_bypass_fingerprints", [])
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values}


def _write_baseline(fingerprints: set[str], path: Path | None = None) -> None:
    path = path or AUDIT_BASELINE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": int(time.time()),
        "raw_cdp_bypass_fingerprints": sorted(fingerprints),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


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
    issue_context_mentions: list[dict[str, Any]] = []
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
                    "snippet": sanitize_text(text, 500),
                }
                if RAW_CDP_PATTERN.search(text):
                    if file_path.name == "codex-tui.log" or file_path.name in HISTORY_REFERENCE_FILES:
                        if len(raw_cdp_reference) < MAX_LOG_MATCHES:
                            raw_cdp_reference.append(hit)
                    elif RAW_CDP_BYPASS_PATTERN.search(text) and not RAW_CDP_REFERENCE_PATTERN.search(text):
                        if len(raw_cdp_bypass) < MAX_LOG_MATCHES:
                            raw_cdp_bypass.append(hit)
                    elif len(raw_cdp_reference) < MAX_LOG_MATCHES:
                        raw_cdp_reference.append(hit)
                if BROKER_PATTERN.search(text) and len(broker_mentions) < MAX_LOG_MATCHES:
                    broker_mentions.append(hit)
                if BROKER_PATTERN.search(text) and FAILURE_PATTERN.search(text) and len(failure_mentions) < MAX_LOG_MATCHES:
                    failure_mentions.append(hit)
                if ISSUE_CONTEXT_PATTERN.search(text) and len(issue_context_mentions) < MAX_ISSUE_CANDIDATES:
                    issue_context_mentions.append(hit)
    return {
        "files_scanned": len(files),
        "raw_cdp_bypass_mentions": raw_cdp_bypass,
        "raw_cdp_reference_mentions": raw_cdp_reference,
        "broker_mentions": broker_mentions,
        "broker_failure_mentions": failure_mentions,
        "issue_context_mentions": issue_context_mentions,
    }


def _telemetry_events(since_ts: int) -> list[dict[str, Any]]:
    return [event for event in _read_jsonl(TELEMETRY_STATE_FILE) if int(event.get("created_at", 0)) >= since_ts]


def _issues(since_ts: int) -> list[dict[str, Any]]:
    data = _read_json(ISSUE_STATE_FILE, {"issues": {}})
    issues = list(data.get("issues", {}).values()) if isinstance(data, dict) else []
    return [issue for issue in issues if int(issue.get("created_at", 0)) >= since_ts or int(issue.get("updated_at", 0)) >= since_ts]


def _issue_terms(issue: dict[str, Any]) -> list[str]:
    terms = [
        str(issue.get("id") or ""),
        str(issue.get("lease_id") or ""),
        str(issue.get("source") or ""),
        str(issue.get("title") or ""),
    ]
    terms.extend(str(tag) for tag in issue.get("tags", []) if tag)
    return [term.lower() for term in terms if len(term.strip()) >= 4]


def _issue_log_context(issue: dict[str, Any], session_scan: dict[str, Any]) -> list[dict[str, Any]]:
    terms = _issue_terms(issue)
    if not terms:
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    buckets = (
        "broker_failure_mentions",
        "issue_context_mentions",
        "broker_mentions",
        "raw_cdp_bypass_mentions",
        "raw_cdp_reference_mentions",
        "baselined_raw_cdp_bypass_mentions",
    )
    for bucket in buckets:
        for hit in session_scan.get(bucket, []):
            text = " ".join(str(hit.get(key, "")) for key in ("file", "snippet")).lower()
            if not any(term in text for term in terms):
                continue
            marker = (str(hit.get("file", "")), int(hit.get("line", 0)))
            if marker in seen:
                continue
            seen.add(marker)
            hits.append({**hit, "bucket": bucket})
            if len(hits) >= MAX_ISSUE_CONTEXT_HITS:
                return hits
    return hits


def _scan_issue_log_contexts(paths: list[Path], since_ts: int, issues: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    issue_terms = {str(issue.get("id", "")): _issue_terms(issue) for issue in issues if issue.get("id")}
    issue_terms = {issue_id: terms for issue_id, terms in issue_terms.items() if terms}
    if not issue_terms:
        return {}

    contexts: dict[str, list[dict[str, Any]]] = {issue_id: [] for issue_id in issue_terms}
    seen: dict[str, set[tuple[str, int]]] = {issue_id: set() for issue_id in issue_terms}
    files = _iter_session_files(paths)
    for file_path in files:
        if all(len(hits) >= MAX_ISSUE_CONTEXT_HITS for hits in contexts.values()):
            break
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
                lowered = text.lower()
                for issue_id, terms in issue_terms.items():
                    if len(contexts[issue_id]) >= MAX_ISSUE_CONTEXT_HITS:
                        continue
                    if not any(term in lowered for term in terms):
                        continue
                    marker = (str(file_path), line_no)
                    if marker in seen[issue_id]:
                        continue
                    seen[issue_id].add(marker)
                    contexts[issue_id].append(
                        {
                            "file": str(file_path),
                            "line": line_no,
                            "snippet": sanitize_text(text, 500),
                            "bucket": "issue_specific_scan",
                        }
                    )
    return contexts


def _issue_log_contexts(issues: list[dict[str, Any]], session_scan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    contexts: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        issue_id = str(issue.get("id", ""))
        if issue_id:
            contexts[issue_id] = _issue_log_context(issue, session_scan)
    return contexts


def _merge_issue_contexts(*context_sets: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = {}
    seen: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for contexts in context_sets:
        for issue_id, hits in contexts.items():
            bucket = merged.setdefault(issue_id, [])
            for hit in hits:
                marker = (str(hit.get("file", "")), int(hit.get("line", 0)))
                if marker in seen[issue_id] or len(bucket) >= MAX_ISSUE_CONTEXT_HITS:
                    continue
                seen[issue_id].add(marker)
                bucket.append(hit)
    return merged


def _active_leases() -> dict[str, Any]:
    data = _read_json(POOL_STATE_FILE, {"leases": {}})
    return data.get("leases", {}) if isinstance(data, dict) else {}


def run_audit(
    hours: int = 24,
    session_paths: list[Path] | None = None,
    now: int | None = None,
    use_baseline: bool = True,
) -> dict[str, Any]:
    now_ts = int(now or time.time())
    bounded_hours = max(1, min(int(hours), 24 * 30))
    since_ts = now_ts - bounded_hours * 3600
    paths = session_paths or list(DEFAULT_SESSION_PATHS)

    events = _telemetry_events(since_ts)
    issues = _issues(since_ts)
    leases = _active_leases()
    session_scan = _scan_session_logs(paths, since_ts)
    baseline = _baseline_fingerprints() if use_baseline else set()
    all_raw_cdp_bypass = session_scan["raw_cdp_bypass_mentions"]
    active_raw_cdp_bypass: list[dict[str, Any]] = []
    baselined_raw_cdp_bypass: list[dict[str, Any]] = []
    for hit in all_raw_cdp_bypass:
        if _hit_fingerprint(hit) in baseline:
            baselined_raw_cdp_bypass.append(hit)
        else:
            active_raw_cdp_bypass.append(hit)
    session_scan["raw_cdp_bypass_mentions"] = active_raw_cdp_bypass
    session_scan["baselined_raw_cdp_bypass_mentions"] = baselined_raw_cdp_bypass
    session_scan["raw_cdp_bypass_mentions_total"] = len(all_raw_cdp_bypass)
    issue_log_contexts = _merge_issue_contexts(
        _issue_log_contexts(issues, session_scan),
        _scan_issue_log_contexts(paths, since_ts, issues),
    )

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
        has_release = any(event.get("message") in LEASE_TERMINAL_MESSAGES for event in lease_items)
        is_active = lease_id in leases
        if has_create and not has_release and not is_active:
            findings.append(
                {
                    "severity": "high",
                    "code": "missing_release_telemetry",
                    "message": f"Lease {lease_id} has creation telemetry but no terminal release/expiry telemetry and is not active.",
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
                "log_context_count": len(issue_log_contexts.get(str(issue.get("id")), [])),
            }
        )
        score -= 10 if issue.get("severity") != "blocker" else 20

    error_events = [event for event in events if event.get("event_type") == "error"]
    issue_ids = {str(issue.get("id")) for issue in issues if issue.get("id")}
    untriaged_error_events = [
        event
        for event in error_events
        if str(event.get("issue_id") or "") not in issue_ids
    ]
    if untriaged_error_events and not issues:
        findings.append(
            {
                "severity": "medium",
                "code": "error_telemetry_without_issues",
                "message": "Broker recorded error telemetry but no feedback issue was filed in the audit window.",
                "count": len(untriaged_error_events),
                "sources": dict(Counter(str(event.get("source", "unknown")) for event in untriaged_error_events)),
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
        "baselined_raw_cdp_bypass_count": len(baselined_raw_cdp_bypass),
        "by_source": dict(by_source),
        "by_event_type": dict(by_type),
        "findings": findings,
        "issue_log_contexts": issue_log_contexts,
        "session_logs": session_scan,
    }


def baseline_current_raw_cdp(
    hours: int = 24,
    session_paths: list[Path] | None = None,
    now: int | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    now_ts = int(now or time.time())
    bounded_hours = max(1, min(int(hours), 24 * 30))
    since_ts = now_ts - bounded_hours * 3600
    paths = session_paths or list(DEFAULT_SESSION_PATHS)
    session_scan = _scan_session_logs(paths, since_ts)
    existing = set() if replace else _baseline_fingerprints()
    current = {_hit_fingerprint(hit) for hit in session_scan["raw_cdp_bypass_mentions"]}
    merged = existing | current
    _write_baseline(merged)
    return {
        "baseline_file": str(AUDIT_BASELINE_FILE),
        "window_hours": bounded_hours,
        "added": len(merged - existing),
        "total": len(merged),
        "raw_cdp_bypass_count": len(current),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit OpenBrowser Broker agent usage.")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--session-path", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-baseline", action="store_true", help="Do not ignore previously baselined raw-CDP findings.")
    parser.add_argument("--baseline-current", action="store_true", help="Mark current raw-CDP findings as historical baseline.")
    parser.add_argument("--replace-baseline", action="store_true", help="Replace the audit baseline instead of merging into it.")
    args = parser.parse_args(argv)

    paths = [Path(item) for item in args.session_path] if args.session_path else None
    if args.baseline_current:
        result = baseline_current_raw_cdp(hours=args.hours, session_paths=paths, replace=args.replace_baseline)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"baseline_file: {result['baseline_file']}")
            print(f"added: {result['added']}")
            print(f"total: {result['total']}")
        return 0

    result = run_audit(hours=args.hours, session_paths=paths, use_baseline=not args.no_baseline)
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
