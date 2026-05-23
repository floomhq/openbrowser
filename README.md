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

## Commands

```bash
/root/ax-browser-broker/bin/ax-browser-lease --owner manual
/root/ax-browser-broker/bin/ax-browser-use --json open https://example.com
/root/ax-browser-broker/bin/ax-openbrowser status --format json
/root/ax-browser-broker/bin/ax-browser-mcp
```

## Verification

```bash
PYTHONPATH=/root/ax-browser-broker python3 -m pytest -q /root/ax-browser-broker/tests
curl -fsS http://127.0.0.1:8767/health
```

## Auth flow

Agents create an auth request with `/auth/request` or `auth_request`.
The broker returns a one-time portal URL.
The portal can launch noVNC against authenticated Chrome for human login.

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
