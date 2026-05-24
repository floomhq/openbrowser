from __future__ import annotations

from typing import Any


TOPICS: dict[str, dict[str, Any]] = {
    "quickstart": {
        "title": "AX41 Browser Broker Quickstart",
        "steps": [
            "Call browser_lease with owner and optional identity_id.",
            "Use browser_navigate, browser_snapshot, browser_click, browser_type, and related tools with the returned lease_id.",
            "Call browser_release when finished.",
        ],
        "examples": [
            {"tool": "browser_lease", "args": {"owner": "agent-name", "identity_id": "linkedin-main"}},
            {"tool": "browser_navigate", "args": {"lease_id": "<lease_id>", "url": "https://www.linkedin.com/feed/"}},
            {"tool": "browser_release", "args": {"lease_id": "<lease_id>"}},
        ],
    },
    "identities": {
        "title": "Identities",
        "facts": [
            "linkedin-main is pinned to pool-c.",
            "linkedin-main uses /root/browser-pool/profiles/linkedin-main.",
            "linkedin-main routes Chrome through local proxy http://127.0.0.1:18803.",
            "Identity leases are exclusive; a second lease for the same identity returns a conflict.",
            "Imported Mac Chrome people use chrome-* identities, isolated AX41 profile directories, and auto slot selection.",
            "When a chrome-* identity is leased, the broker activates the identity on a free non-reserved slot before returning the lease.",
            "Auto Chrome identities do not overwrite pinned/proxied identities such as linkedin-main.",
            "Mac Keychain-backed cookies/passwords/tokens are not copied; auth state is established on AX41 through human auth handoff or Chrome sync.",
            "Failed or contended identity lease attempts are recorded as error telemetry.",
            "Lease selection runs under the broker lease-state lock and rechecks browser health before returning a lease.",
        ],
        "commands": [
            "/root/ax-browser-broker/bin/ax-browser-identity status",
            "/root/ax-browser-broker/bin/ax-browser-identity mac-inventory",
            "/root/ax-browser-broker/bin/ax-browser-identity import-mac-profiles --dry-run",
            "/root/ax-browser-broker/bin/ax-browser-use --identity linkedin-main --json state",
            "/root/ax-browser-broker/bin/ax-openbrowser --identity linkedin-main status",
        ],
        "runbooks": [
            "/root/ax-browser-broker/docs/mac-chrome-profiles.md",
        ],
    },
    "browser-use": {
        "title": "browser-use Wrapper",
        "commands": [
            "/root/ax-browser-broker/bin/ax-browser-use --identity linkedin-main --json open https://api.ipify.org?format=json",
            "/root/ax-browser-broker/bin/ax-browser-use --identity linkedin-main --json eval 'document.body.innerText'",
        ],
        "notes": [
            "The wrapper leases a broker slot, injects the CDP URL, runs browser-use, and releases the lease.",
            "Use identity_id linkedin-main for authenticated LinkedIn work.",
            "Use the generic pool for unrelated browsing.",
        ],
    },
    "openbrowser": {
        "title": "OpenBrowser Wrapper",
        "commands": [
            "/root/ax-browser-broker/bin/ax-openbrowser --identity linkedin-main status",
            "/root/ax-browser-broker/bin/ax-openbrowser status",
        ],
        "notes": [
            "The wrapper generates a temporary OpenBrowser config pointing at the leased CDP port and profile.",
            "OpenBrowser is useful for session diagnostics and its built-in MCP surface.",
            "Do not run OpenBrowser directly against shared ports when using agents; use the wrapper.",
        ],
    },
    "auth": {
        "title": "Human Auth Handoff",
        "steps": [
            "When an agent hits a login wall, call auth_request with owner, url, and reason.",
            "For an imported Chrome person, pass identity_id so the noVNC login opens that identity profile.",
            "Send the returned local portal URL to the human operator.",
            "The portal starts local-only noVNC for login and marks completion.",
            "After completion, lease the same identity_id; the saved AX41 profile state is reused.",
            "If an identity auth handoff is refused before VNC starts, the temporary VNC password file is removed.",
            "When completion runs, the broker stops VNC/websockify/Chrome/Xvfb helper processes and removes the temporary password file.",
        ],
        "examples": [
            {"tool": "auth_request", "args": {"owner": "agent-name", "url": "https://accounts.google.com/", "reason": "google_profile_login", "identity_id": "chrome-openpaper"}},
        ],
    },
    "feedback": {
        "title": "Native Feedback And Issue Tracking",
        "steps": [
            "Call feedback_report_issue when a browser task fails or a workflow gap appears.",
            "Include source, severity, title, details, and optional lease_id/url/tags.",
            "Call feedback_list_issues to inspect open issues.",
            "Call feedback_update_issue to resolve or annotate an issue.",
            "Issue reports automatically write linked telemetry events.",
        ],
    },
    "telemetry": {
        "title": "Telemetry",
        "steps": [
            "Call telemetry_record_event for structured session evidence, smoke-test results, and non-issue observations.",
            "Use event_type values: auth, browser_action, docs, error, feedback, issue, lease, profile, proxy, session, smoke.",
            "Use severity values: info, warning, error, critical.",
            "Filter with telemetry_list_events by source, event_type, severity, lease_id, or issue_id.",
            "Use telemetry_summary for counts by event type, severity, and source.",
        ],
        "privacy": [
            "Telemetry redacts sensitive keys such as password, token, cookie, secret, authorization, and totp.",
            "Browser type telemetry stores text length, not typed text.",
            "State lives in /root/ax-browser-broker/state/telemetry.jsonl and is ignored by git.",
        ],
    },
    "audit": {
        "title": "Agent Usage Audit",
        "steps": [
            "Call broker_audit after browser-agent work to check correct broker usage.",
            "The audit correlates telemetry, feedback issues, active leases, and session logs.",
            "Findings flag direct CDP mentions, active leases, missing release telemetry, open issues, and broker failures without issues.",
            "Use /root/ax-browser-broker/bin/ax-browser-audit --json for the same audit from shell.",
            "After remediation, use /root/ax-browser-broker/bin/ax-browser-audit --baseline-current --replace-baseline --json once to mark old findings as historical; new raw-CDP hits still fail later audits.",
        ],
        "pass_criteria": [
            "Score is 80 or higher.",
            "No unexpected active leases remain.",
            "No raw CDP bypass findings are present unless they are known documentation/test snippets.",
            "Known historical bypasses are counted under baselined_raw_cdp_bypass_count, not active findings.",
            "Browser failures have linked feedback issues.",
        ],
    },
    "safety": {
        "title": "Safety Boundary",
        "rules": [
            "No raw cookie or proxy password exposure.",
            "No raw macOS Chrome cookie, password, or token database copying.",
            "No CAPTCHA bypass or ban-circumvention tooling.",
            "Use human auth handoff for passwords and login challenges.",
            "One LinkedIn identity lease at a time.",
            "Release every lease after use.",
        ],
    },
}


def docs(topic: str = "quickstart") -> dict[str, Any]:
    key = topic.strip().lower()
    if key == "topics":
        return {"topics": sorted(TOPICS)}
    if key not in TOPICS:
        return {"error": f"Unknown docs topic: {topic}", "available_topics": sorted(TOPICS)}
    return {"topic": key, **TOPICS[key], "available_topics": sorted(TOPICS)}
