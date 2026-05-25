# OpenBrowser Remote API

OpenBrowser is the public API surface for AX41 Browser Broker. It is callable from remote machines through the Cloudflare-backed host and uses the same leased browser pool, identities, auth handoff, telemetry, and feedback stores as MCP.

Base URL:

```text
https://openbrowser-auth.floom.dev/openbrowser/v1
```

Authentication:

```text
Authorization: Bearer <OPENBROWSER_API_KEY>
```

Keys are loaded from `OPENBROWSER_API_KEYS`, `AX_OPENBROWSER_API_KEYS`, or `/root/ax-browser-broker/secrets/openbrowser_api_keys.json`.

Use a normal API client user agent such as `openbrowser-client/1.0`, `curl`, or your app's own product user agent. Cloudflare blocks Python's default `Python-urllib/*` user agent on this hostname.

## Core Flow

```bash
BASE=https://openbrowser-auth.floom.dev/openbrowser/v1
KEY="$(jq -r '.tokens.federico' /root/ax-browser-broker/secrets/openbrowser_api_keys.json)"

LEASE="$(
  curl -fsS "$BASE/leases" \
    -H "authorization: Bearer $KEY" \
    -H "user-agent: openbrowser-client/1.0" \
    -H "content-type: application/json" \
    -d '{"owner":"remote-smoke","ttl_seconds":300}'
)"

LEASE_ID="$(printf '%s' "$LEASE" | jq -r '.lease_id')"

curl -fsS "$BASE/browser/navigate" \
  -H "authorization: Bearer $KEY" \
  -H "user-agent: openbrowser-client/1.0" \
  -H "content-type: application/json" \
  -d "{\"lease_id\":\"$LEASE_ID\",\"url\":\"https://example.com\"}"

curl -fsS "$BASE/browser/snapshot" \
  -H "authorization: Bearer $KEY" \
  -H "user-agent: openbrowser-client/1.0" \
  -H "content-type: application/json" \
  -d "{\"lease_id\":\"$LEASE_ID\"}"

curl -fsS -X POST "$BASE/leases/$LEASE_ID/release" \
  -H "authorization: Bearer $KEY" \
  -H "user-agent: openbrowser-client/1.0"
```

## One-Shot Open

`POST /openbrowser/v1/open` leases a browser and navigates it in one request. It returns the lease; callers still release it.

```bash
curl -fsS "$BASE/open" \
  -H "authorization: Bearer $KEY" \
  -H "user-agent: openbrowser-client/1.0" \
  -H "content-type: application/json" \
  -d '{"owner":"remote-smoke","url":"https://example.com","ttl_seconds":300}'
```

## Identities

Pass `identity_id` only when account state is required.

- Omit `identity_id` for generic public-page QA.
- Use `chrome-depontefede` for Federico's AX41 Chrome identity with persisted Google/Discord login.
- Use `linkedin-main` for LinkedIn. It is proxy-routed and exclusive.

Generic leases never use an active personal identity slot. If all neutral slots are busy, the allocator may recycle an idle proxied identity slot back to its neutral pool profile, then that identity can be reactivated on demand later.

## Endpoints

- `GET /health`
- `GET /docs`
- `POST /leases`
- `POST /leases/{lease_id}/release`
- `POST /leases/{lease_id}/heartbeat`
- `POST /open`
- `POST /browser/navigate`
- `POST /browser/snapshot`
- `POST /browser/screenshot`
- `POST /browser/click`
- `POST /browser/type`
- `POST /browser/wait`
- `POST /browser/tabs`
- `POST /browser/new-tab`
- `POST /browser/switch-tab`

## Safety

The API never exposes cookies, passwords, raw Discord tokens, proxy credentials, or VNC passwords. Human login remains under `/auth/<token>` and noVNC remains temporary.
