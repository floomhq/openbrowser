# AX41 Browser Broker

Local browser automation broker for AX41 agents.

## Services

- API: `http://127.0.0.1:8767`
- MCP stdio command: `/root/ax-browser-broker/bin/ax-browser-mcp`
- Pool slots: `9223`, `9224`, `9225`

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
```

The importer creates `chrome-*` identities with isolated AX41 profile directories and `slot: "auto"` so different profiles can run concurrently when free pool slots exist. It copies no raw cookies, passwords, or tokens from macOS. Use `auth_request(..., identity_id="chrome-...")` once per imported identity to log in through local noVNC, then agents can lease that identity through the broker.

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

## Agent Docs And Feedback

Agents can call `broker_docs` through MCP for live runbook topics:

- `topics`
- `quickstart`
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

Agents can record and inspect telemetry through MCP:

- `telemetry_record_event`
- `telemetry_list_events`
- `telemetry_summary`

The telemetry store is append-only JSONL at `/root/ax-browser-broker/state/telemetry.jsonl` and is ignored by git. Sensitive keys such as password, token, cookie, secret, authorization, and totp are redacted before storage. Browser typing telemetry stores text length, not typed text.

Agents and operators can audit broker usage:

- MCP: `broker_audit(hours=24)`
- API: `GET /audit?hours=24`
- CLI: `/root/ax-browser-broker/bin/ax-browser-audit --json`

The audit checks telemetry, feedback issues, active leases, and session logs. It flags raw CDP mentions, unreleased leases, open issues, and broker failures that lack issue reports.

## Auth flow

Agents create an auth request with `/auth/request` or `auth_request`.
The broker returns a one-time portal URL.
The portal can launch noVNC against authenticated Chrome for human login.
When `identity_id` is provided, the portal launches a temporary graphical Chrome using that identity's AX41 profile directory.

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
