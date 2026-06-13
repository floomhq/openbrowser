# Adapters

OpenBrowser Broker includes wrappers for tools that normally expect a direct CDP endpoint. The wrappers lease a browser slot, inject the leased CDP URL, run the tool, record telemetry, and release the slot.

## browser-use

```bash
openbrowser-use --json open https://example.com
openbrowser-use --identity work-main --json state
openbrowser-use --beta-check
```

Use `--identity <id>` when account state or proxy routing is required.

Browser Use 0.13's Rust-backed beta driver is an optional engine, not a replacement for OpenBrowser. Keep it behind the wrapper so every run still goes through broker leases, persisted identities, proxy routing, auth handoff, telemetry, and audit.

`openbrowser-use --beta-check` reports whether `browser_use.beta` exists in the current Python environment. `openbrowser-use --beta ...` exits before leasing when the beta module is unavailable, so agents cannot silently run the wrong Browser Use path.

When the wrapped Browser Use process exits, the adapter also cleans up broker-scoped Browser Use daemon processes for the same lease/session. This prevents orphaned daemons from holding a CDP target after the broker lease is released.

## OpenBrowser

```bash
openbrowser status
openbrowser docs quickstart
openbrowser auth https://example.com/login --identity work-main --owner agent-name
```

OpenBrowser is broker-backed, not a separate browser pool. Use the CLI for broker status, docs, auth handoffs, and lease-control links instead of connecting raw OpenBrowser processes to pool CDP ports.

## Manual Lease

```bash
openbrowser-broker &
curl -fsS http://127.0.0.1:8767/lease \
  -H "content-type: application/json" \
  -d '{"owner":"manual","ttl_seconds":300}'
```

## Feedback And Telemetry

Adapters record start, completion, failure, duration, and exit-code telemetry. When an adapter exits nonzero, it files a feedback issue automatically.

For expected app-level failures, record telemetry only. File issues when the browser service, lease manager, identity/proxy activation, auth handoff, upload, screenshot, keyboard, or adapter layer blocks the task.
