# Adapters

OpenBrowser Broker includes wrappers for tools that normally expect a direct CDP endpoint. The wrappers lease a browser slot, inject the leased CDP URL, run the tool, record telemetry, and release the slot.

## browser-use

```bash
openbrowser-use --json open https://example.com
openbrowser-use --identity work-main --json state
```

Use `--identity <id>` when account state or proxy routing is required.

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
