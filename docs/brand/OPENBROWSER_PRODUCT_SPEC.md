# OpenBrowser Product Spec

## One-liner

OpenBrowser is a browser API and broker that lets AI agents lease real browser sessions with persistent profiles, human auth handoff, proxy-aware identities, and auditability.

## Problem

Agents can operate clean APIs and public pages. They fail when work moves into the logged-in web:

- login and 2FA
- cookies and browser state
- account-specific profiles
- browser sessions colliding
- no useful API
- sensitive actions that need human approval

OpenBrowser gives agents a safe browser identity layer.

## Core concepts

### Broker

The service that owns browser slots, profiles, auth handoffs, proxies, telemetry, and lease lifecycle.

### Lease

A temporary right for an agent to use a browser session.

Fields:

- lease_id
- owner
- identity_id / profile_id
- status
- ttl
- browser endpoint
- created_at
- last_activity_at

### Profile

A persistent browser identity with cookies and session state.

Fields:

- profile_id
- label
- site / connected app
- profile_dir
- auth_policy
- proxy_ref
- max_parallel_sessions

### Human auth request

A one-time handoff where the agent asks a human to complete login, 2FA, passkey, or a sensitive step.

Fields:

- request_id
- lease_id
- profile_id
- reason
- portal_url
- status
- expires_at

### Proxy route

A network route attached to a profile or session.

Fields:

- proxy_ref
- region
- type
- health

### Audit event

A record of what happened without exposing secrets.

Fields:

- event_id
- lease_id
- actor
- action
- target
- timestamp
- redaction_status

## Critical flows

### Lease flow

1. Agent requests a browser lease.
2. Broker chooses an available slot.
3. Broker attaches the requested profile.
4. Broker applies proxy route if configured.
5. Agent receives lease ID and browser endpoint.
6. Agent acts.
7. Agent releases the lease.
8. Broker writes telemetry and audit trail.

### Auth handoff flow

1. Agent hits login / 2FA / passkey / sensitive action.
2. Agent creates auth request.
3. Broker returns one-time portal URL.
4. Human opens portal and completes the step.
5. Agent continues using the same browser session.

### Remote agent flow

1. Broker runs on a server controlled by the user.
2. Remote agent calls the API or MCP server.
3. Broker leases a browser session.
4. Agent never receives raw credentials.

## Boundaries

OpenBrowser is not:

- a CAPTCHA solver
- a credential extractor
- a scraping bypass product
- an autonomous browser agent
- a replacement for stable product APIs

OpenBrowser is infrastructure. Agents or automation libraries decide what to do.

## Success criteria

A new user should be able to:

1. start the broker
2. define a profile
3. complete human login once
4. lease that profile from an agent
5. observe session state
6. audit what happened
