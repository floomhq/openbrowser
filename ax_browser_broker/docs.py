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
        ],
        "commands": [
            "/root/ax-browser-broker/bin/ax-browser-identity status",
            "/root/ax-browser-broker/bin/ax-browser-use --identity linkedin-main --json state",
            "/root/ax-browser-broker/bin/ax-openbrowser --identity linkedin-main status",
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
            "Send the returned local portal URL to the human operator.",
            "The portal starts local-only noVNC for login and marks completion.",
            "After completion, snapshot/seed profiles when the authenticated browser state changed.",
        ],
    },
    "feedback": {
        "title": "Native Feedback And Issue Tracking",
        "steps": [
            "Call feedback_report_issue when a browser task fails or a workflow gap appears.",
            "Include source, severity, title, details, and optional lease_id/url/tags.",
            "Call feedback_list_issues to inspect open issues.",
            "Call feedback_update_issue to resolve or annotate an issue.",
        ],
    },
    "safety": {
        "title": "Safety Boundary",
        "rules": [
            "No raw cookie or proxy password exposure.",
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
