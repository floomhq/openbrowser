# OpenBrowser Broker

Open-source browser automation infrastructure for AI agents: a browser pool, persistent Chrome profiles, proxy-aware identities, human login handoff, a remote API, and MCP tools.

OpenBrowser Broker lets Claude, Codex, Cursor, browser-use, OpenBrowser, and custom agents share real Chrome browsers without fighting over one CDP port. Agents lease isolated browser sessions, use persisted profiles when account state is needed, route selected identities through proxies, hand login challenges to a human, and leave behind telemetry plus issue reports that can be audited later.

## Why

Most browser agents break in the same ways:

- several agents connect to the same Chrome instance and block each other
- logged-in sessions are tied to one fragile browser profile
- passwords and 2FA prompts become unsafe chat messages
- rich-text apps such as Slack, Discord, Notion, Linear, and X ignore DOM fill calls
- failures vanish into logs, so the next agent repeats the same mistake

OpenBrowser Broker gives agents a single operating contract: lease, act, release, report.

## Features

- **Browser pool**: multiple isolated Chrome slots with CDP endpoints managed behind one broker.
- **Persistent profiles**: named identities reuse Chrome profile directories and session cookies.
- **Profile replicas**: selected identities can run in parallel without Chrome profile-lock conflicts.
- **Proxy routing**: identities can pin traffic to an HTTP/SOCKS proxy via `proxy_ref`.
- **Remote API**: bearer-token protected `/openbrowser/v1` API for agents on any machine.
- **MCP servers**: local MCP for same-host agents and remote MCP for HTTPS-backed access.
- **Human auth handoff**: one-time portal links for login, 2FA, passkeys, and manual challenges.
- **Active lease control**: short-lived manual control links for a browser tab already held by an agent.
- **Rich-text keyboard tools**: real keyboard events for editors that reject simple DOM value changes.
- **Telemetry and issues**: sanitized events, feedback issue tracking, and usage audits.
- **browser-use and OpenBrowser adapters**: wrappers lease a slot, run the tool, then release the slot.

## Architecture

```text
Agent / MCP client / API client
        |
        v
OpenBrowser Broker API
        |
        +-- lease manager
        +-- profile and identity manager
        +-- proxy forwarders
        +-- telemetry and issue store
        |
        v
Chrome pool: pool-a, pool-b, pool-c, ...
```

## Quick Start

### Python

```bash
git clone https://github.com/federicodeponte/ax-browser-broker.git openbrowser-broker
cd openbrowser-broker
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
playwright install chromium
cp .env.example .env
cp config/identities.example.json config/identities.local.json
```

Start the broker:

```bash
openbrowser-broker
```

### Docker

```bash
git clone https://github.com/federicodeponte/ax-browser-broker.git openbrowser-broker
cd openbrowser-broker
OPENBROWSER_API_KEYS="$(openssl rand -base64 48)" docker compose up --build
```

Lease a browser:

```bash
curl -fsS http://127.0.0.1:8767/lease \
  -H "content-type: application/json" \
  -d '{"owner":"demo","ttl_seconds":300}'
```

Use the returned `lease_id`:

```bash
curl -fsS http://127.0.0.1:8767/browser/navigate \
  -H "content-type: application/json" \
  -d '{"lease_id":"<lease_id>","url":"https://example.com"}'

curl -fsS http://127.0.0.1:8767/browser/snapshot \
  -H "content-type: application/json" \
  -d '{"lease_id":"<lease_id>"}'

curl -fsS -X POST http://127.0.0.1:8767/release/<lease_id>
```

## Remote API

Expose the broker behind your HTTPS proxy or tunnel and configure:

```bash
OPENBROWSER_API_KEYS="your-long-random-api-key"
OPENBROWSER_PUBLIC_OPENBROWSER_BASE_URL="https://browser.example.com/openbrowser/v1"
```

Then call:

```bash
BASE=https://browser.example.com/openbrowser/v1
KEY=your-long-random-api-key

curl -fsS "$BASE/docs" \
  -H "authorization: Bearer $KEY" \
  -H "user-agent: openbrowser-client/1.0"
```

The public API covers leases, navigation, snapshots, screenshots, clicks, typing, keyboard events, tabs, auth handoff, lease control, profiles, feedback issues, telemetry, and audits.

## MCP

Local MCP, for agents running on the broker host:

```json
{
  "mcpServers": {
    "openbrowser-broker": {
      "command": "openbrowser-mcp"
    }
  }
}
```

Remote MCP, for agents running anywhere:

```json
{
  "mcpServers": {
    "openbrowser-remote": {
      "command": "openbrowser-remote-mcp",
      "env": {
        "OPENBROWSER_API_KEY": "<OPENBROWSER_API_KEY>",
        "OPENBROWSER_BASE_URL": "https://browser.example.com/openbrowser/v1"
      }
    }
  }
}
```

Core MCP tools:

- `browser_lease`, `browser_release`, `browser_heartbeat`
- `browser_navigate`, `browser_snapshot`, `browser_screenshot`
- `browser_click`, `browser_type`, `browser_keyboard_type`, `browser_keyboard_press`
- `browser_tabs`, `browser_new_tab`, `browser_switch_tab`, `browser_wait`
- `auth_request`, `auth_status`, `lease_control_request`
- `feedback_report_issue`, `feedback_list_issues`, `feedback_update_issue`
- `telemetry_record_event`, `telemetry_list_events`, `telemetry_summary`
- `broker_audit`, `broker_docs`, `profile_status`

## Persistent Profiles

Identities are configured in `config/identities.local.json`:

```json
{
  "identities": {
    "work-main": {
      "label": "Work account",
      "site": "example.com",
      "slot": "auto",
      "profile_dir": "/var/lib/openbrowser-broker/profiles/work-main",
      "proxy_ref": "residential:work-main",
      "timezone": "America/New_York",
      "lang": "en-US",
      "policy": {
        "max_parallel_sessions": 1,
        "requires_human_auth": true
      }
    }
  }
}
```

When an identity needs login:

```bash
curl -fsS "$BASE/auth/request" \
  -H "authorization: Bearer $KEY" \
  -H "content-type: application/json" \
  -d '{"owner":"setup","identity_id":"work-main","url":"https://example.com/login","reason":"initial_login"}'
```

Open the returned `portal_url`, complete login in the browser view, then mark the request complete. Future leases for that identity reuse the saved profile state.

## Proxy Routing

Add proxy credentials in `secrets/proxies.json`:

```json
{
  "proxies": {
    "residential:work-main": {
      "scheme": "http",
      "host": "proxy.example.net",
      "port": 12345,
      "username": "user",
      "password": "pass"
    }
  }
}
```

Then set `"proxy_ref": "residential:work-main"` on the identity. The broker starts a local proxy forwarder and launches Chrome with the matching proxy for that profile.

## Safety Model

- Raw cookies, passwords, tokens, proxy credentials, and VNC passwords are never returned by tools.
- Telemetry redacts sensitive keys and secret-shaped strings.
- Browser typing telemetry stores text length, not typed text.
- Login and challenge handling use human handoff portals instead of secrets in chat.
- CAPTCHA solving and ban-circumvention automation are outside the project boundary.

## Operations

```bash
openbrowser-audit --json
openbrowser-use --json open https://example.com
openbrowser-adapter status --format json
```

Systemd examples live in `systemd/`. Detailed runbooks live in `docs/`.

## Development

```bash
python3 -m compileall ax_browser_broker tests
pytest -q
```

## Project Status

OpenBrowser Broker is production-oriented infrastructure that is being prepared for public open-source release. Compatibility wrappers remain available for existing deployments; new installs can use the generic commands and environment variables above.

## License

MIT
