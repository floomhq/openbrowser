# AX41 Browser Broker Operations

## Service

- Unit: `ax-browser-broker.service`
- API: `http://127.0.0.1:8767`
- Bind address: `127.0.0.1`
- Runtime user: `root`
- Hardening: `UMask=077`, `NoNewPrivileges=true`
- Browser pool supervisor unit: `browser-pool-supervisor.service`
- Browser pool supervisor script: `/root/ax-browser-broker/browser_pool/bin/supervisor.sh`
- Browser pool launcher script: `/root/ax-browser-broker/browser_pool/bin/launch_chrome.sh`

## Verification Commands

```bash
PYTHONPATH=/root/ax-browser-broker python3 -m pytest -q /root/ax-browser-broker/tests
systemctl is-active ax-browser-broker.service browser-pool-supervisor.service authenticated-chrome.service
systemctl show ax-browser-broker.service -p UMask -p NoNewPrivileges
python3 /root/browser-pool/bin/session_manager.py status
ss -ltnp '( sport = :8767 or sport = :6081 or sport = :5901 )'
find /root/ax-browser-broker/profiles/golden /root/browser-pool/profiles -name .totp-secret -type f -print
```

## LinkedIn Identity Verification

```bash
/root/ax-browser-broker/bin/ax-browser-identity status
/root/ax-browser-broker/bin/ax-browser-identity check-proxy iproyal:linkedin-main
/root/ax-browser-broker/bin/ax-openbrowser --identity linkedin-main status
/root/ax-browser-broker/bin/ax-browser-use --identity linkedin-main --json open 'https://api.ipify.org?format=json'
/root/ax-browser-broker/bin/ax-browser-use --identity linkedin-main --json eval 'document.body.innerText'
```

Expected facts:

- `linkedin-main` is on `pool-c`.
- The local proxy forwarder listens on `127.0.0.1:18803`.
- Chrome on `pool-c` launches with `--proxy-server=http://127.0.0.1:18803`.
- LinkedIn session is active in OpenBrowser status.

## Rollback

```bash
systemctl stop ax-browser-broker.service
systemctl disable ax-browser-broker.service
rm -f /etc/systemd/system/ax-browser-broker.service
systemctl daemon-reload
```

The existing browser pool and authenticated Chrome services are independent:

```bash
systemctl restart browser-pool-supervisor.service authenticated-chrome.service
```

## Auth Portal Security

- Public handoff hostname: `https://openbrowser-auth.floom.dev`.
- Cloudflare Tunnel routes that hostname to `http://localhost:8768`.
- nginx listens on `127.0.0.1:8768` and exposes only `/auth/*`, `/healthz`, `/mac/install-reverse-tunnel.sh`, and temporary noVNC traffic.
- `/mac/install-reverse-tunnel.sh` is a static no-secret Mac LaunchAgent installer for the reverse SSH/CDP route.
- nginx access logging is disabled for this auth proxy because auth request paths contain one-time tokens.
- The full broker API remains bound to `127.0.0.1:8767` and is not exposed through this hostname.
- Cloudflare Access is not active for this hostname from AX41 because the available Cloudflare API credential cannot manage Zero Trust Access. The active protection is Cloudflare Tunnel, unguessable expiring broker tokens, and a temporary VNC password.
- Identity auth writes per-slot maintenance markers under `/root/browser-pool/state/maintenance/` before stopping a headless pool Chrome. The pool supervisor and launcher skip marked slots until auth completion or marker expiry, preventing profile-lock collisions.
- Identity auth Chrome honors the identity `proxy_ref` by starting a temporary local `ax-proxy-forwarder` and passing Chrome `--proxy-server=http://127.0.0.1:<port>`. Identities with no `proxy_ref` use direct AX41 egress.
- noVNC starts only for an active auth request.
- noVNC binds to `127.0.0.1`.
- Completion calls `/auth/{token}/complete`.
- Completion stops VNC processes and removes the generated VNC password file.
- Idle state has no listeners on `6081` or `5901`.
- Auth portal output escapes request owner, status, and URL before rendering HTML.

## Profile Security

Golden and pool profile seeding excludes:

- `.totp-secret`
- `.com.google.Chrome.*`
- Chrome caches and lock files

`rsync` uses `--delete-excluded` so excluded material is removed from existing snapshots.

## Known Auth Freshness

Google and LinkedIn sessions are active in the seeded pool profiles. GitHub is missing because the source authenticated Chrome profile has no `user_session` cookie. Refresh path:

1. Create an auth request for GitHub with `auth_request`.
2. Complete GitHub login through the auth portal.
3. Snapshot golden profile.
4. Seed pool profiles.
5. Verify with `/root/ax-browser-broker/bin/ax-openbrowser status --format json`.

## Issue Tracking

Issue state lives at `/root/ax-browser-broker/state/issues.json`.

Agents use MCP tools:

- `feedback_report_issue`
- `feedback_list_issues`
- `feedback_update_issue`

Issue creation and updates also emit telemetry events linked by `issue_id`.
OpenBrowser and browser-use adapter wrappers automatically create high-severity issues when their subprocess exits nonzero.
Issue text, URLs, tags, and notes are redacted for common secret-shaped strings before storage.

## Telemetry

Telemetry state lives at `/root/ax-browser-broker/state/telemetry.jsonl`.

Agents use MCP tools:

- `telemetry_record_event`
- `telemetry_list_events`
- `telemetry_summary`

Event types:

- `auth`
- `browser_action`
- `docs`
- `error`
- `feedback`
- `issue`
- `lease`
- `profile`
- `proxy`
- `session`
- `smoke`

Severity values:

- `info`
- `warning`
- `error`
- `critical`

The broker records lease lifecycle, browser actions, auth requests, auth completion, issue creation, and issue updates. Agent-created telemetry accepts structured `data` and redacts sensitive keys before writing to disk.
Browser API failures emit `error` telemetry with the action name and lease id. OpenBrowser and browser-use wrappers emit adapter start, completion, failure, duration, and exit-code telemetry.
Messages, URLs, tags, and string values are redacted for common secret-shaped strings before storage; browser typing records text length only.

## Agent Usage Audit

Run:

```bash
/root/ax-browser-broker/bin/ax-browser-audit --hours 24 --json
```

Or call MCP:

- `broker_audit(hours=24)`

The audit combines:

- `/root/ax-browser-broker/state/telemetry.jsonl`
- `/root/ax-browser-broker/state/issues.json`
- `/root/browser-pool/state/leases.json`
- Claude session JSONL under `/root/.claude/projects`
- Codex history and TUI logs under `/root/.codex`

Findings include direct CDP mentions, active leases, missing release telemetry, open issues, and broker failure mentions without issue reports.
Audit JSON includes `issue_log_contexts` for direct issue-specific session-log snippets by issue id, source, lease id, title, and tags. Check those snippets before resolving browser-tool issues.

After a routing cleanup, baseline the already-reviewed historical raw-CDP findings once:

```bash
/root/ax-browser-broker/bin/ax-browser-audit --hours 24 --baseline-current --replace-baseline --json
```

Later audits ignore those exact historical entries, keep counting them under `baselined_raw_cdp_bypass_count`, and still fail on any new raw-CDP bypass.
