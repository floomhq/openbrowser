from __future__ import annotations

from typing import Any


TOPICS: dict[str, dict[str, Any]] = {
    "quickstart": {
        "title": "AX41 Browser Broker Quickstart",
        "steps": [
            "Call browser_lease with owner and optional identity_id.",
            "Use browser_navigate, browser_snapshot, browser_click, browser_type, and related tools with the returned lease_id.",
            "For rich-text editors such as Discord, Slack, Notion, Linear, or X, use browser_keyboard_type and browser_keyboard_press, or browser_type submit=true on a rich-text textbox.",
            "Call browser_release when finished.",
        ],
        "examples": [
            {"tool": "browser_lease", "args": {"owner": "agent-name", "identity_id": "linkedin-main"}},
            {"tool": "browser_navigate", "args": {"lease_id": "<lease_id>", "url": "https://www.linkedin.com/feed/"}},
            {"tool": "browser_release", "args": {"lease_id": "<lease_id>"}},
        ],
    },
    "routing": {
        "title": "Browser Tool Routing",
        "default": "Use AX41 Browser Broker for agent browser work. Raw authenticated Chrome and raw CDP ports are exception paths.",
        "routes": [
            {
                "route": "AX41 Browser Broker MCP",
                "use_for": "Normal browser agents, authenticated identities, concurrent sessions, feedback, telemetry, and audits.",
                "start": "broker_docs('routing'), browser_lease, browser_release, broker_audit",
            },
            {
                "route": "ax-browser-use",
                "use_for": "browser-use task execution against broker-leased browsers.",
                "start": "/root/ax-browser-broker/bin/ax-browser-use --identity <id> ...",
            },
            {
                "route": "ax-openbrowser",
                "use_for": "OpenBrowser diagnostics and OpenBrowser MCP surface.",
                "start": "/root/ax-browser-broker/bin/ax-openbrowser --identity <id> ...",
                "note": "OpenBrowser is an adapter on top of broker leases, not a separate browser setup.",
            },
            {
                "route": "Mac depontefede CDP",
                "use_for": "Federico explicitly expects Mac Chrome, Mac IP, saved passwords, or already-open personal sessions.",
                "start": "/root/.codex/scripts/mac-chrome-cdp ensure, then connect to http://127.0.0.1:19333",
                "note": "User-explicit exception path only; this is not an AX41 persistent broker profile.",
            },
            {
                "route": "Federico Chrome identity",
                "use_for": "AX41 work that must look like Federico's personal Chrome profile or needs SSO continuity.",
                "start": "/root/ax-browser-broker/bin/ax-openbrowser --identity chrome-depontefede ...",
                "note": "Uses /root/browser-pool/profiles/chrome-depontefede. Imported Mac profile metadata does not include Mac Keychain cookies/passwords/tokens.",
            },
            {
                "route": "Discord identity",
                "use_for": "Discord account work only after Federico accepts a separate dedicated Discord profile.",
                "start": "/root/ax-browser-broker/bin/ax-openbrowser --identity discord-main ...",
                "note": "Separate from Federico's personal Chrome profile. For normal personal-profile continuity, use chrome-depontefede instead.",
            },
            {
                "route": "gstack browse or disposable browser tools",
                "use_for": "Anonymous QA, local dev-server screenshots, public pages, and work with no Federico account state.",
                "start": "Use the relevant disposable browser skill or tool.",
            },
            {
                "route": "shared authenticated Chrome / chrome-devtools / authenticated-browser",
                "use_for": "Explicitly authorized dashboard exception, performance/network inspection, or migration fallback that requires the already logged-in shared Chrome profile.",
                "start": "Use the named exception skill/tool, record telemetry, then run broker_audit.",
            },
        ],
        "rules": [
            "Use broker identities such as chrome-* or linkedin-main when account state is needed.",
            "Use auth_request for login or password handoff.",
            "Use lease_control_request when an already-leased browser hits a human challenge or login prompt that must be handled in the current tab.",
            "For Discord, use chrome-depontefede when Federico expects his normal personal browser; use discord-main only for a separate Discord-only profile.",
            "Use /root/ax-browser-broker/bin/ax-openbrowser when a task names OpenBrowser.",
            "For chat/editor submission, prefer broker keyboard tools over DOM fill because modern editors maintain internal state.",
            "Never aim raw OpenBrowser or custom scripts directly at 9222, 9223, 9224, or 9225 for normal agent work.",
            "Raw pool CDP ports 9223, 9224, and 9225 belong to the broker lease manager.",
        ],
        "runbooks": [
            "/root/ax-browser-broker/docs/browser-routing.md",
        ],
    },
    "identities": {
        "title": "Identities",
        "facts": [
            "linkedin-main is pinned to the configured dedicated slot.",
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
            "Send the returned portal_url to the human operator; on AX41 it is https://openbrowser-auth.floom.dev/auth/<token>.",
            "If portal_url is localhost on AX41, file a browser issue because public auth handoff config is broken.",
            "The public hostname exposes only /auth/* and temporary noVNC traffic through a localhost-only nginx proxy.",
            "The portal starts noVNC for login and marks completion.",
            "During identity auth, the broker pauses the matching pool slot with a maintenance marker so headless Chrome cannot re-lock the profile.",
            "If the identity has proxy_ref, the temporary auth Chrome also uses that proxy through ax-proxy-forwarder.",
            "After completion, lease the same identity_id; the saved AX41 profile state is reused.",
            "If a challenge appears inside an active headless lease, use lease_control_request for a short-lived manual control link instead of starting a second VNC auth browser.",
            "If an identity auth handoff is refused before VNC starts, the temporary VNC password file is removed.",
            "When completion runs, the broker stops VNC/websockify/Chrome/Xvfb helper processes and removes the temporary password file.",
        ],
        "examples": [
            {"tool": "auth_request", "args": {"owner": "agent-name", "url": "https://accounts.google.com/", "reason": "google_profile_login", "identity_id": "chrome-openpaper"}},
            {"tool": "lease_control_request", "args": {"owner": "agent-name", "lease_id": "<lease_id>", "ttl_seconds": 900}},
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
            "Adapter wrappers file an issue automatically when OpenBrowser or browser-use exits nonzero.",
            "Do not file issues for ordinary expected assertion failures, missing app content, or one-off selector misses during product QA.",
            "File an issue when the browser service, lease manager, identity/proxy activation, auth handoff, upload, screenshot, keyboard, or adapter layer blocks the task.",
            "File an issue when the same browser action fails repeatedly and the agent cannot complete the workflow by changing selectors or waiting.",
            "Audit output includes issue_log_contexts with direct issue-specific session-log snippets matched by issue id, source, lease, title, or tags.",
        ],
    },
    "telemetry": {
        "title": "Telemetry",
        "steps": [
            "Call telemetry_record_event for structured session evidence, smoke-test results, and non-issue observations.",
            "Use telemetry-only records for expected negative test cases and normal app-level validation failures.",
            "Use event_type values: auth, browser_action, docs, error, feedback, issue, lease, profile, proxy, session, smoke.",
            "Use severity values: info, warning, error, critical.",
            "Filter with telemetry_list_events by source, event_type, severity, lease_id, or issue_id.",
            "Use telemetry_summary for counts by event type, severity, and source.",
            "Broker API browser actions emit success telemetry and failure telemetry.",
            "OpenBrowser and browser-use wrappers emit start, completion, failure, duration, and exit-code telemetry.",
        ],
        "privacy": [
            "Telemetry redacts sensitive keys such as password, token, cookie, secret, authorization, and totp.",
            "Telemetry also redacts common secret-shaped strings in messages, urls, tags, and data values.",
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
            "When issues exist, issue_log_contexts links direct issue-specific session-log snippets for faster repair.",
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
