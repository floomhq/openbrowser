from __future__ import annotations

from typing import Any


TOPICS: dict[str, dict[str, Any]] = {
    "quickstart": {
        "title": "OpenBrowser Broker Quickstart",
        "steps": [
            "For simple user handoff requests such as 'open Lovable for me', call browser_open_control with owner, url, and optional identity_id. It opens or reuses the page, verifies state with a compact snapshot, and returns the Take Over Tab URL. Do not call browser_screenshot for this path unless the user explicitly asks for visual proof.",
            "Call browser_lease with owner and optional identity_id.",
            "Immediately call browser_snapshot or browser_screenshot to see the current page state before doing anything else.",
            "Do NOT call browser_navigate if the current page is already meaningful (e.g. after a human auth handoff the browser is on the target page). Only navigate when the current page is blank, a new tab, or unrelated to the task.",
            "Use browser_click, browser_type, browser_snapshot, and related tools with the returned lease_id.",
            "For rich-text editors such as Slack, Discord, Notion, Linear, or X, use browser_keyboard_type and browser_keyboard_press.",
            "For Slack and other modal-heavy apps, press Escape before clicking header or tab-rail buttons if a popover may be intercepting pointer events.",
            "Call browser_release when finished.",
            "Call broker_audit after browser-agent work.",
        ],
        "examples": [
            {"tool": "browser_open_control", "args": {"owner": "agent-name", "identity_id": "work-main", "url": "https://example.com"}},
            {"tool": "browser_lease", "args": {"owner": "agent-name", "identity_id": "work-main"}},
            {"tool": "browser_snapshot", "args": {"lease_id": "<lease_id>"}},
            {"tool": "browser_navigate", "args": {"lease_id": "<lease_id>", "url": "https://example.com"}},
            {"tool": "browser_release", "args": {"lease_id": "<lease_id>"}},
        ],
    },
    "routing": {
        "title": "Browser Tool Routing",
        "default": "Use OpenBrowser Broker for agent browser work. Raw shared CDP ports are exception paths.",
        "routes": [
            {
                "route": "OpenBrowser Broker MCP",
                "use_for": "Normal browser agents, authenticated identities, concurrent sessions, feedback, telemetry, and audits.",
                "start": "broker_docs('routing'), browser_lease, browser_release, broker_audit",
            },
            {
                "route": "openbrowser-use",
                "use_for": "browser-use task execution against broker-leased browsers.",
                "start": "openbrowser-use --identity <id> ...",
            },
            {
                "route": "openbrowser",
                "use_for": "OpenBrowser diagnostics and OpenBrowser MCP surface.",
                "start": "openbrowser <status|docs|open|auth|takeover|audit> ...",
                "note": "OpenBrowser is an adapter on top of broker leases, not a separate browser pool.",
            },
            {
                "route": "Remote OpenBrowser MCP",
                "use_for": "Agents running outside the browser host.",
                "start": "OPENBROWSER_API_KEY=... OPENBROWSER_BASE_URL=https://browser.example.com/openbrowser/v1 openbrowser-remote-mcp",
            },
            {
                "route": "Disposable browser tools",
                "use_for": "Anonymous QA, local dev-server screenshots, public pages, and work with no account state.",
                "start": "Use the relevant disposable browser skill or tool.",
            },
        ],
        "rules": [
            "Use broker identities such as work-main or qa-generic when account state is needed.",
            "Use auth_request for login or password handoff; auth_request returns a real /auth/<token> portal by default.",
            "Use takeover_request only when an already-leased browser needs human current-tab control without credential entry. The compatibility tool name is lease_control_request.",
            "Use OpenBrowser wrappers instead of aiming custom scripts directly at raw pool CDP ports.",
            "For chat/editor submission, prefer broker keyboard tools over DOM fill because modern editors maintain internal state.",
            "For Slack header/tab controls, dismiss open ReactModal/popovers with Escape before selector clicks.",
            "Raw pool CDP ports belong to the broker lease manager.",
        ],
        "runbooks": [
            "docs/browser-routing.md",
            "docs/openbrowser-api.md",
        ],
    },
    "identities": {
        "title": "Identities",
        "facts": [
            "Identities map a stable name to a Chrome profile directory, optional proxy_ref, locale, timezone, and policy.",
            "Identity leases reuse persisted browser state from the configured profile directory.",
            "When policy.max_parallel_sessions is greater than one, replica profiles avoid Chrome profile-lock conflicts.",
            "Pinned or proxied identities are not overwritten by generic profile work.",
            "macOS Keychain-backed cookies, passwords, and tokens are not copied; auth state is established through human auth handoff or Chrome Sync.",
            "Failed or contended identity lease attempts are recorded as error telemetry.",
            "Lease selection runs under the broker lease-state lock and rechecks browser health before returning a lease.",
        ],
        "commands": [
            "openbrowser status",
            "openbrowser auth https://example.com/login --identity work-main --owner agent-name",
            "openbrowser-use --identity work-main --json open https://example.com",
        ],
        "runbooks": [
            "docs/mac-chrome-profiles.md",
            "docs/openbrowser-api.md",
        ],
    },
    "browser-use": {
        "title": "browser-use Wrapper",
        "commands": [
            "openbrowser-use --json open https://example.com",
            "openbrowser-use --identity work-main --json state",
            "openbrowser-use --beta-check",
        ],
        "notes": [
            "The wrapper leases a broker slot, injects the CDP URL, runs browser-use, and releases the lease.",
            "Use an identity_id when account state or proxy routing is required.",
            "Use the generic pool for unrelated public browsing.",
            "The Browser Use 0.13 Rust-backed beta driver is treated as an optional engine inside OpenBrowser. It is never allowed to bypass broker leases, identity profiles, proxies, auth handoff, telemetry, or audits.",
            "Use --beta-check before beta experiments. If browser_use.beta is unavailable, --beta exits before leasing instead of silently falling back.",
            "Broker browser actions recover once from closed Playwright/CDP transports by clearing cached page/browser handles and reconnecting to the same leased slot.",
        ],
    },
    "openbrowser": {
        "title": "OpenBrowser CLI",
        "commands": [
            "openbrowser status",
            "openbrowser docs quickstart",
            "openbrowser open https://example.com --identity work-main --control",
            "openbrowser auth https://example.com/login --identity work-main --owner agent-name",
        ],
        "notes": [
            "The CLI talks to the local broker API and reads the local server-side API key file when needed.",
            "Use open --control for simple 'open this for me' requests; it returns a verified Take Over Tab URL in one command.",
            "openbrowser auth returns a real /auth/<token> login portal by default. Use open --control or takeover for current-tab Take Over Tab links.",
            "Use it for status, docs, auth handoffs, Take Over Tab links, and quick smoke checks.",
            "Use Broker MCP directly for normal click/type/screenshot workflows when tools are available.",
        ],
    },
    "auth": {
        "title": "Human Auth Handoff",
        "steps": [
            "When an agent hits a login wall, call auth_request with owner, url, reason, and identity_id.",
            "auth_request returns a real /auth/<token> login portal by default. Send this link when the user must sign in, enter a password, use a passkey, solve 2FA, or complete a provider login flow.",
            "If the requested identity is already leased, do not convert login auth into Take Over Tab. Release your own stale lease first, or wait if another agent owns the lease.",
            "Use takeover_request only when the user explicitly needs to control an already-open current tab, not for credential entry. The compatibility tool name is lease_control_request.",
            "If no identity_id is supplied, auth_request creates a neutral auth portal and does not use a personal authenticated profile.",
            "For login tasks, send only a portal_url that starts with /auth/ and not /auth/lease-control/. The latter is Take Over Tab, not login.",
            "Use mode=lease_control only as the low-level compatibility flag for explicit current-tab Take Over Tab fallback. Do not use it for login.",
            "During identity auth, the broker pauses the matching pool slot with a maintenance marker so headless Chrome cannot re-lock the profile.",
            "If the identity has proxy_ref, the legacy temporary auth Chrome also uses that proxy through ax-proxy-forwarder.",
            "If an identity auth handoff is refused before VNC starts, the temporary VNC password file is removed.",
            "When auth completion runs, the broker stops VNC/websockify/Chrome/Xvfb helper processes and removes the temporary password file.",
            "After auth completion, lease the same identity, snapshot first, and continue from the authenticated state. After Take Over Tab, continue from the same lease_id/current tab.",
            "After browser work, confirm state with snapshot or screenshot and run broker_audit.",
        ],
        "examples": [
            {"tool": "auth_request", "args": {"owner": "agent-name", "url": "https://example.com/login", "reason": "profile_login", "identity_id": "work-main"}},
            {"tool": "takeover_request", "args": {"owner": "agent-name", "lease_id": "<lease_id>", "ttl_seconds": 900}},
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
            "Telemetry state is ignored by git.",
        ],
    },
    "audit": {
        "title": "Agent Usage Audit",
        "steps": [
            "Call broker_audit after browser-agent work to check correct broker usage.",
            "The audit correlates telemetry, feedback issues, active leases, and session logs.",
            "Findings flag direct CDP mentions, active leases, missing release telemetry, open issues, and broker failures without issues.",
            "When issues exist, issue_log_contexts links direct issue-specific session-log snippets for faster repair.",
            "Use openbrowser-audit --json for the same audit from shell.",
        ],
        "pass_criteria": [
            "Score is 80 or higher.",
            "No unexpected active leases remain.",
            "No raw CDP bypass findings are present unless they are known documentation/test snippets.",
            "Browser failures have linked feedback issues.",
        ],
    },
    "safety": {
        "title": "Safety Boundary",
        "rules": [
            "No raw cookie or proxy password exposure.",
            "No raw Chrome cookie, password, or token database copying.",
            "No CAPTCHA bypass or ban-circumvention tooling.",
            "Use human auth handoff for passwords and login challenges.",
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
