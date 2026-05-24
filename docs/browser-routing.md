# Browser Routing On AX41

This is the canonical routing guide for Claude, Codex, browser-use, OpenBrowser, and other browser agents on AX41.

## One-Line Rule

Use AX41 Browser Broker for agent browser work. Raw authenticated Chrome and raw CDP ports are exception paths.

## Tool Routing

| Route | Use For | How To Start | Notes |
|-------|---------|--------------|-------|
| AX41 Browser Broker MCP | Normal browser agents, authenticated identities, concurrent sessions, feedback, telemetry, audits | `broker_docs("routing")`, `browser_lease`, `browser_release`, `broker_audit` | Default route for agents. Leases isolate sessions and prevent agents from blocking each other. |
| `ax-browser-use` | browser-use task execution against broker-leased browsers | `/root/ax-browser-broker/bin/ax-browser-use --identity <id> ...` | Wrapper leases a slot, injects CDP details, runs browser-use, then releases the slot. |
| `ax-openbrowser` | OpenBrowser diagnostics and OpenBrowser MCP surface | `/root/ax-browser-broker/bin/ax-openbrowser --identity <id> ...` | OpenBrowser is an adapter on top of broker leases, not a separate browser setup. |
| gstack `/browse` or disposable browser tools | Anonymous QA, local dev-server screenshots, public pages, no Federico account state | Skill/tool-specific command | Fast isolated browser lane. No saved personal cookies or account sessions. |
| Shared authenticated Chrome / `chrome-devtools` / authenticated-browser | Explicitly authorized dashboard exception, performance/network inspection, migration fallback that requires the already logged-in shared Chrome profile | Only via the named skill/tool for that exception | Raw shared profile path. Record telemetry and run broker audit afterward. |
| Raw pool CDP ports `9223`, `9224`, `9225` | Never directly from agents | Use broker lease instead | Pool slots belong to the broker lease manager. |

## If Unsure

1. If the task needs Federico's account state or saved browser identity, use broker first with an identity such as `chrome-*` or `linkedin-main`.
2. If login or password entry appears, use `auth_request`; Federico completes login through the handoff portal.
3. If the task is anonymous page QA or local UI screenshots, use disposable browser tooling such as gstack `/browse`.
4. If the task names OpenBrowser, use `/root/ax-browser-broker/bin/ax-openbrowser`; never aim raw OpenBrowser at `9222`, `9223`, `9224`, or `9225`.
5. If the task explicitly requires the shared logged-in Chrome session, record the exception in telemetry, use the authenticated-browser or chrome-devtools exception path, then run `broker_audit(hours=24)`.

## Identity Examples

```bash
/root/ax-browser-broker/bin/ax-browser-identity status
/root/ax-browser-broker/bin/ax-browser-use --identity linkedin-main --json state
/root/ax-browser-broker/bin/ax-openbrowser --identity chrome-depontefede status
```

## MCP Examples

```text
broker_docs(topic="routing")
browser_lease(owner="agent-name", identity_id="chrome-depontefede")
browser_navigate(lease_id="<lease_id>", url="https://accounts.google.com/")
browser_release(lease_id="<lease_id>")
broker_audit(hours=24)
```

## Terms

- Broker: the API/MCP lease manager at `127.0.0.1:8767`.
- Pool slots: broker-owned Chrome instances on `9223`, `9224`, and `9225`.
- Authenticated Chrome: the shared real Chrome profile on `9222`; exception path only.
- OpenBrowser: a consumer of broker leases through `ax-openbrowser`.
- browser-use: a consumer of broker leases through `ax-browser-use`.
