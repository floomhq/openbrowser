# AX41 Browser Broker

Local browser automation broker for AX41 agents.

## Services

- API: `http://127.0.0.1:8767`
- MCP stdio command: `/root/ax-browser-broker/bin/ax-browser-mcp`
- Pool slots: `9223` through `9230` (`pool-a` through `pool-h`)

## Core flow

1. Lease a slot through `/lease` or `browser_lease`.
2. Run browser actions with the returned `lease_id`.
3. Release the slot through `/release/{lease_id}` or `browser_release`.

All action endpoints validate the lease before touching a browser.

For authenticated LinkedIn work, pass `identity_id: "linkedin-main"` to `browser_lease` or `--identity linkedin-main` to the wrappers. This pins the work to `pool-c`, `/root/browser-pool/profiles/linkedin-main`, and the configured US ISP proxy.

For Federico's Mac Chrome people/profiles, import metadata into broker identities:

```bash
/root/ax-browser-broker/bin/ax-browser-identity mac-inventory
/root/ax-browser-broker/bin/ax-browser-identity import-mac-profiles --dry-run
/root/ax-browser-broker/bin/ax-browser-identity import-mac-profiles
/root/ax-browser-broker/bin/ax-mac-profile-sync status
/root/ax-browser-broker/bin/ax-mac-profile-sync sync --dry-run
/root/ax-browser-broker/bin/ax-mac-profile-autosync
```

If the Mac reverse tunnel is absent, the Mac-side installer is:

```bash
curl -fsSL https://openbrowser-auth.floom.dev/mac/install-reverse-tunnel.sh | bash
```

The importer creates `chrome-*` identities with isolated AX41 profile directories and `slot: "auto"` so different profiles can run concurrently when free pool slots exist. Auto identities use free non-reserved slots; pinned/proxied identities such as `linkedin-main` keep their dedicated slot and are not overwritten by generic Chrome profile work. The importer copies no raw cookies, passwords, or tokens from macOS because macOS Chrome secret state is Keychain-backed and not portable Linux Chrome session state.

Use `auth_request(..., identity_id="chrome-...")` once per imported identity to log in through local noVNC, then agents can lease that identity through the broker. If an auth handoff is refused or fails before VNC starts, the broker removes the temporary VNC password file. Successful handoff completion stops VNC/Chrome/Xvfb helper processes and removes the temporary password file.

## Commands

```bash
/root/ax-browser-broker/bin/ax-browser-lease --owner manual
/root/ax-browser-broker/bin/ax-browser-use --json open https://example.com
/root/ax-browser-broker/bin/ax-browser-use --identity linkedin-main --json state
/root/ax-browser-broker/bin/ax-openbrowser status --format json
/root/ax-browser-broker/bin/ax-openbrowser --identity linkedin-main status
/root/ax-browser-broker/bin/ax-browser-mcp
```

## Verification

```bash
PYTHONPATH=/root/ax-browser-broker python3 -m pytest -q /root/ax-browser-broker/tests
curl -fsS http://127.0.0.1:8767/health
```

Operational verification and rollback notes live in `docs/operations.md`.
Mac Chrome profile import and auth edge cases live in `docs/mac-chrome-profiles.md`.
Browser tool routing lives in `docs/browser-routing.md`.

## Agent Docs And Feedback

Agents can call `broker_docs` through MCP for live runbook topics:

- `topics`
- `quickstart`
- `routing`
- `identities`
- `browser-use`
- `openbrowser`
- `auth`
- `feedback`
- `telemetry`
- `audit`
- `safety`

Agents can report issues through MCP:

- `feedback_report_issue`
- `feedback_list_issues`
- `feedback_update_issue`

The issue store is local at `/root/ax-browser-broker/state/issues.json` and is ignored by git.
OpenBrowser and browser-use wrapper failures file issues automatically when the adapter process exits nonzero.

## Remote OpenBrowser API

OpenBrowser is also exposed as a bearer-token-protected remote API at:

```text
https://openbrowser-auth.floom.dev/openbrowser/v1
```

Use it from any trusted machine with `Authorization: Bearer <OPENBROWSER_API_KEY>`. It supports leases, navigation, snapshots, screenshots, tabs, clicks, typing, waits, and one-shot `open` calls. See `docs/openbrowser-api.md`.
Logged-in Chrome identities can also opt into controlled parallel sessions with `policy.max_parallel_sessions`. Parallel identity leases use per-slot profile replicas under `/root/browser-pool/profiles/.replicas/`, so Chrome profile locks do not block separate agents.
Issue title, details, URL, tags, and notes are sanitized before storage.

Agents can record and inspect telemetry through MCP:

- `telemetry_record_event`
- `telemetry_list_events`
- `telemetry_summary`

The telemetry store is append-only JSONL at `/root/ax-browser-broker/state/telemetry.jsonl` and is ignored by git. Sensitive keys such as password, token, cookie, secret, authorization, and totp are redacted before storage. Browser typing telemetry stores text length, not typed text.
The broker also emits failure telemetry for browser API action exceptions. The OpenBrowser and browser-use wrappers emit adapter start/completion/failure telemetry with duration and exit code.

Agents and operators can audit broker usage:

- MCP: `broker_audit(hours=24)`
- API: `GET /audit?hours=24`
- CLI: `/root/ax-browser-broker/bin/ax-browser-audit --json`

The audit checks telemetry, feedback issues, active leases, and session logs. It flags raw CDP mentions, unreleased leases, open issues, and broker failures that lack issue reports.
Audit JSON includes `issue_log_contexts`, keyed by issue id, so an agent can inspect direct issue-specific Claude/Codex session-log snippets before fixing a browser-tool problem.

## Auth flow

Agents create an auth request with `/auth/request` or `auth_request`.
The broker returns a one-time portal URL. On AX41 this is `https://openbrowser-auth.floom.dev/auth/<token>`; `local_portal_url` is kept only as a localhost diagnostic fallback.
The public hostname is routed through Cloudflare Tunnel to a localhost-only nginx proxy that exposes `/auth/*` and the temporary noVNC surface, not the full broker API.
The portal can launch noVNC against authenticated Chrome or an identity-specific temporary Chrome for human login.
When `identity_id` is provided, the portal launches a temporary graphical Chrome using that identity's AX41 profile directory.
Identity auth pauses the matching pool slot with a maintenance marker so headless Chrome cannot re-lock the profile. If that identity has `proxy_ref`, the temporary auth Chrome uses the same local proxy-forwarder path as pool Chrome.

Normal tools do not return raw cookies or password data.

## Run

```bash
/root/ax-browser-broker/bin/ax-browser-broker
```

Install as systemd:

```bash
cp /root/ax-browser-broker/systemd/ax-browser-broker.service /etc/systemd/system/ax-browser-broker.service
systemctl daemon-reload
systemctl enable --now ax-browser-broker.service
```
