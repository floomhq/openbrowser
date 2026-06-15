# Browser Routing

This is the canonical routing guide for agents using OpenBrowser Broker.

```mermaid
flowchart TD
  Task["Browser task"] --> NeedAuth{"Needs account state?"}
  NeedAuth -->|No| Generic["Lease generic broker slot"]
  NeedAuth -->|Yes| Identity["Lease named identity"]
  Identity --> Login{"Login wall?"}
  Login -->|No| Act["Navigate, click, type, screenshot"]
  Login -->|Yes| Handoff["auth_request -> /auth/<token>"]
  Generic --> Act
  Handoff --> Act
  Act --> Release["Release lease"]
  Release --> Audit["Run broker_audit"]
```

## Routes

| Route | Use For | Start |
| --- | --- | --- |
| Broker MCP | Normal browser agents, authenticated identities, concurrent sessions, feedback, telemetry, audits | `broker_docs`, `browser_lease`, `browser_release`, `broker_audit` |
| Remote MCP | Agents running outside the browser host | `openbrowser-remote-mcp` with `OPENBROWSER_API_KEY` and `OPENBROWSER_BASE_URL` |
| OpenBrowser CLI | Broker status, docs, auth handoffs, active lease-control links, and smoke checks | `openbrowser <status|docs|auth|open|lease-control|audit> ...` |
| browser-use wrapper | browser-use task execution against broker-leased browsers | `openbrowser-use --identity <id> ...` |
| Fast disposable browser | Anonymous QA, local dev-server screenshots, public pages, no account state | gstack `/browse` or another disposable-browser command |

## Rules

1. Lease before browser work.
2. Use identities only when account state or proxy routing is required.
3. Use `auth_request` for login, passkeys, 2FA, or password entry.
4. Use `lease_control_request` only when a human must control the currently leased tab without entering credentials.
5. Release every lease.
6. Run `broker_audit` after browser-agent work.
7. Do not connect custom scripts directly to raw pool CDP ports during normal agent work.

## Identities

`config/identities.local.json` maps identity names to profile directories, proxy refs, locale, timezone, and parallel-session policy.

```bash
openbrowser status
openbrowser auth https://example.com/login --identity work-main --owner agent-name
openbrowser-use --identity work-main --json open https://example.com
```

When `policy.max_parallel_sessions` is greater than one, parallel leases use per-slot replicas under `profiles/.replicas/<identity>/<slot>`.

A human auth handoff (`/auth/*`) logs in against the identity's **base** profile dir, not a replica. So that the agent's next lease sees the freshly-authenticated session, completing an auth request now invalidates that identity's stale replicas, and a lease that would be served from a replica whose cookies predate the base profile re-syncs the replica from base before handing it out. The auth-complete response also includes a `cookie_verification` block confirming the target-origin cookie actually landed in the base profile.

Do not launch several independent Chrome processes against the same `profile_dir`. Chrome profile locks, SQLite databases, and local state files are single-writer resources. On a laptop, several Chrome windows for the same profile still belong to one Chrome process; on the broker, separate agents normally receive separate Chrome processes.

Use these identity concurrency modes:

| Mode | Use For | Tradeoff |
| --- | --- | --- |
| Single canonical lease | Login, settings changes, sensitive account actions | Strongest persistence; one lease owner at a time; that owner can open multiple tabs |
| Profile replicas | Parallel read/QA/background flows with the same seeded identity | Independent slots; replicas re-sync from base after an auth handoff or when their cookies predate the base profile |
| Shared live browser coordinator | Future mode for several agents attached to one running Chrome process | Not the default lease contract; needs focus/navigation arbitration |

The default contract is a single canonical lease unless the identity explicitly opts into replicas with `policy.max_parallel_sessions`.

`max_parallel_sessions` is a policy cap, not a Chrome feature cap. The hard broker cap is the configured slot count. The default pool has eight slots (`pool-a` through `pool-h`), and each live Chrome slot consumes CPU, RAM, profile disk I/O, and possibly a proxy lane. Keep high-risk identities lower than the pool maximum unless the task explicitly needs more parallelism.

## Fast QA Lane

Use gstack `/browse` for public pages, local dev-server checks, screenshots, and fast UI assertions that do not need Federico's account state. It is intentionally disposable and optimized for quick verification.

Use OpenBrowser Broker for persisted profiles, cookies, proxy-backed identities, login handoffs, rich-text keyboard events, telemetry, feedback issues, and auditable multi-agent browser work. The two routes are complementary: fast disposable browser for anonymous QA, OpenBrowser for anything authenticated or identity-sensitive.

## Browser Use Engine

Browser Use can be used as an OpenBrowser engine through `openbrowser-use`. The wrapper owns the lease, injects the leased CDP URL, records telemetry, and releases the slot. Agents do not call Browser Use directly against raw pool ports.

```bash
openbrowser-use --identity work-main --json state
openbrowser-use --beta-check
```

The Rust-backed Browser Use beta path is gated by runtime detection. If `browser_use.beta` is absent, beta mode exits before a lease is created. This keeps beta experiments explicit and prevents fallback to the wrong action model.

The broker also retries once after closed Playwright/CDP transport errors by clearing the cached page/browser handle and reconnecting to the same slot. Ordinary selector failures and app-level errors are not retried as infrastructure faults.

## Profile Import

Chrome profile metadata can be mirrored from a workstation into broker identities. Raw cookies, passwords, tokens, and keychain-backed browser databases are excluded. Website login state is established through human auth handoff or Chrome Sync.

See `docs/mac-chrome-profiles.md`.
