# Broker Adapters

## browser-use

Use:

```bash
/root/ax-browser-broker/bin/ax-browser-use --json open https://example.com
/root/ax-browser-broker/bin/ax-browser-use --identity linkedin-main --json open https://api.ipify.org?format=json
/root/ax-browser-broker/bin/ax-browser-use --identity linkedin-main --json eval 'document.body.innerText'
```

The wrapper leases a broker slot, passes `--cdp-url` to browser-use, exports common CDP environment variables, then releases the lease when the command exits.

Use `--identity linkedin-main` for LinkedIn. That identity is exclusive, proxy-routed, and seeded with the LinkedIn session.

## OpenBrowser

Use:

```bash
/root/ax-browser-broker/bin/ax-openbrowser status
/root/ax-browser-broker/bin/ax-openbrowser --identity linkedin-main status
```

The `status` command is broker-native and reports cookie presence by name only, never cookie values. Other OpenBrowser commands lease a broker slot and give OpenBrowser a temporary config whose `cdpPort` and `profileDir` point at the leased slot.

OpenBrowser is best for session diagnostics. browser-use is better for task execution against a leased identity.

## Raw Lease

Use:

```bash
/root/ax-browser-broker/bin/ax-browser-lease --owner manual
```

This prints a lease JSON object for custom scripts.

## Feedback

Agents report browser issues through MCP:

```text
feedback_report_issue(source="agent-name", title="Short title", details="What failed and evidence", severity="medium", tags=["browser-use"])
feedback_list_issues(status="open")
feedback_update_issue(issue_id="axbi_...", status="resolved", note="Verification command passed")
```

Agents record operational telemetry through MCP:

```text
telemetry_record_event(source="agent-name", event_type="smoke", message="LinkedIn proxy smoke passed", severity="info", tags=["linkedin-main"])
telemetry_list_events(event_type="browser_action", limit=25)
telemetry_summary(window_seconds=86400)
```

Use issues for human-actionable failures. Use telemetry for session evidence, smoke-test receipts, non-blocking observations, and debugging breadcrumbs.
