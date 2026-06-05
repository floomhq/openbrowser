---
name: openbrowser
description: "OpenBrowser authenticated browser automation on AX41. Use for any task that requires logging into a website (Lovable, Gmail, LinkedIn, etc.) and then performing actions in that browser. Replaces generic browser-use for authenticated sessions."
---

# OpenBrowser

Use this skill whenever the user asks to log into a website, perform browser automation on an authenticated page, or "take over" after a manual login step.

Do NOT use the generic browser-use plugin for authenticated sites — that plugin targets Codex's in-app browser, not the AX41 broker. Always prefer this OpenBrowser skill for login + automation workflows.

## Setup

The broker runs on AX41 at `http://127.0.0.1:8767`. All API calls go through SSH:

```bash
ssh ax41 "curl -fsS -X POST http://127.0.0.1:8767/<endpoint> ..."
```

Bearer token (already configured on AX41):
`ob_UUj9MK8Kh_66Vsp6HVuoEe4v1O2r3rrmYRCyB3392lE`

## Auth handoff (human logs in)

1. Request an auth portal:

```bash
ssh ax41 "curl -fsS -X POST http://127.0.0.1:8767/openbrowser/v1/auth/request \
  -H 'Authorization: Bearer ob_UUj9MK8Kh_66Vsp6HVuoEe4v1O2r3rrmYRCyB3392lE' \
  -H 'Content-Type: application/json' \
  -d '{\"url\":\"https://TARGET_SITE\",\"profile\":\"chrome-depontefede\"}'"
```

2. Extract `portal_url` from the JSON response and show it to the user.

3. Wait for the user to confirm they logged in.

## Lease the browser (after auth)

```bash
LEASE=$(ssh ax41 "curl -fsS -X POST http://127.0.0.1:8767/lease \
  -H 'content-type: application/json' \
  -d '{\"profile\":\"chrome-depontefede\",\"pool\":\"a\"}'")
```

Extract `lease_id` from the response.

## CRITICAL: Check current page before navigating

After leasing, the browser may already be on the page the human left it on. **Do NOT navigate away blindly.**

1. **Screenshot first:**

```bash
ssh ax41 "curl -fsS -X POST http://127.0.0.1:8767/browser/screenshot \
  -H 'content-type: application/json' \
  -d '{\"lease_id\":\"LEASE_ID\"}'"
```

2. **If the page is already the target page** (e.g. the user was already inside a Lovable project), skip `browser_navigate` entirely and proceed with clicks/typing.

3. **Only navigate if** the current page is blank, a new tab, or unrelated to the task:

```bash
ssh ax41 "curl -fsS -X POST http://127.0.0.1:8767/browser/navigate \
  -H 'content-type: application/json' \
  -d '{\"lease_id\":\"LEASE_ID\",\"url\":\"https://TARGET\"}'"
```

## Interaction

- **Snapshot** (text content): `POST /browser/snapshot`
- **Click**: `POST /browser/click` with `x`, `y`
- **Type**: `POST /browser/type` with `text`, `selector` (or `x`, `y`)
- **Screenshot**: `POST /browser/screenshot`
- **Wait**: `POST /browser/wait` with `ms`

## Cleanup

Always release the lease when done:

```bash
ssh ax41 "curl -fsS -X POST http://127.0.0.1:8767/release/LEASE_ID"
```

## Troubleshooting

- If the lease returns pool-e (or any pool other than pool-a), the requested pool was busy. The response still works — just use the returned `lease_id` and `slot`.
- If `chrome-devtools` or `browser-use` MCP tools are visible, ignore them for authenticated sessions. Use this skill instead.
- If screenshots return base64 JSON, extract the `path` field and `scp` the file from AX41 to view it.
