# AX41 Browser Broker Operations

## Service

- Unit: `ax-browser-broker.service`
- API: `http://127.0.0.1:8767`
- Bind address: `127.0.0.1`
- Runtime user: `root`
- Hardening: `UMask=077`, `NoNewPrivileges=true`

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
