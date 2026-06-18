# OpenBrowser Web App Spec

## Product frame

The web app is an operations cockpit for browser identities used by agents.

The user should immediately understand:

- which sessions are active
- which profile is being used
- which website is open
- whether auth is needed
- whether connection/proxy/telemetry are healthy

## Main dashboard layout

### Left column: Browser Sessions

Each row:

- connected app logo or profile logo
- session name, e.g. `work-main`, `research-01`, `sales-bot`
- state: Active lease, Idle, Waiting for auth, Expired
- optional chevron

Do not show "usage this month" in the main visual. This is infrastructure, not a billing SaaS dashboard.

Better small stats:

- Active leases
- Browsers ready
- Auth requests

### Center column: Live Browser Session

Show:

- browser chrome
- URL bar
- live website content
- current profile label
- selected lease label
- one subtle agent action panel

The live website is proof that OpenBrowser controls a real browser. It should not overpower the OpenBrowser shell.

### Right column: Session State

Cards:

1. Active lease
2. Profile
3. Residential proxy
4. Human handoff
5. Connection
6. Telemetry

Profile card should show:

- profile name, e.g. `work-main`
- avatar for the signed-in human account
- connected website logo if relevant
- signed-in identity, e.g. `Maria Santos`

Residential proxy card:

- region, e.g. `US-West (CA)`
- state, e.g. `Active`

Human handoff card:

- Waiting for approval
- Ready
- Completed

Connection card:

- CDP connected
- API connected
- MCP available

## Auth request card

Fields:

- app logo
- "LinkedIn sign-in needs approval"
- "for work-main browser profile"
- Decline / Approve

Keep it small, calm, and Apple-like. No warning colors unless there is a real problem.

## Component rules

- Use translucent white panels.
- Use monochrome line icons for product primitives.
- Use product logos only for external websites and profile identities.
- Use one active green dot.
- Avoid heavy charts unless building an observability screen.

## Empty states

- No active leases: "No agents are using a browser right now."
- No auth requests: "No handoffs waiting."
- No proxy: "Direct connection."

## Important product objects

- Lease
- Profile
- Auth request
- Browser slot
- Proxy route
- Agent owner
- Audit event
- Telemetry event
