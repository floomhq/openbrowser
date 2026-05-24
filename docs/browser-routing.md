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
| Mac depontefede CDP | Federico explicitly expects Mac Chrome, Mac IP, saved passwords, or already-open personal sessions | `/root/.codex/scripts/mac-chrome-cdp ensure`, then connect to `http://127.0.0.1:19333` | Verified Mac profile `/Users/federicodeponte/.hermes/chrome-cdp-clone`, Chrome `Profile 3`, signed into `depontefede@gmail.com`. User-explicit exception path only. |
| Discord identity | Discord account work after Federico accepts a dedicated Discord profile | `/root/ax-browser-broker/bin/ax-openbrowser --identity discord-main ...` | Uses `/root/browser-pool/profiles/discord-main`. This is separate from Federico's personal Chrome profile. |
| Federico Chrome identity | Work that must look like Federico's personal Chrome profile or needs SSO continuity | `/root/ax-browser-broker/bin/ax-openbrowser --identity chrome-depontefede ...` | Uses `/root/browser-pool/profiles/chrome-depontefede`. Imported Mac profile metadata does not include Mac Keychain cookies/passwords/tokens. |
| gstack `/browse` or disposable browser tools | Anonymous QA, local dev-server screenshots, public pages, no Federico account state | Skill/tool-specific command | Fast isolated browser lane. No saved personal cookies or account sessions. |
| Shared authenticated Chrome / `chrome-devtools` / authenticated-browser | Explicitly authorized dashboard exception, performance/network inspection, migration fallback that requires the already logged-in shared Chrome profile | Only via the named skill/tool for that exception | Raw shared profile path. Record telemetry and run broker audit afterward. |
| Raw pool CDP ports `9223`, `9224`, `9225` | Never directly from agents | Use broker lease instead | Pool slots belong to the broker lease manager. |

## If Unsure

1. If Federico says Mac, saved passwords, Mac IP, or already-logged-in depontefede profile, use Mac depontefede CDP.
2. If the task needs AX41 account state or saved browser identity, use broker with an identity such as `chrome-*` or `linkedin-main`.
3. If login or password entry appears in AX41, use `auth_request`; Federico completes login through the handoff portal.
4. If the task is anonymous page QA or local UI screenshots, use disposable browser tooling such as gstack `/browse`.
5. If the task names OpenBrowser, use `/root/ax-browser-broker/bin/ax-openbrowser`; never aim raw OpenBrowser at `9222`, `9223`, `9224`, or `9225`.
6. If the task is Discord, choose the identity explicitly: Mac depontefede CDP when Federico expects his normal personal Mac Chrome profile; `discord-main` only when a separate Discord profile is acceptable.
7. If the task explicitly requires the shared logged-in Chrome session, record the exception in telemetry, use the authenticated-browser or chrome-devtools exception path, then run `broker_audit(hours=24)`.

## Auth Verification

- After QR, password, passkey, or 2FA, verify the page is past the login wall and verify expected site cookies exist without printing cookie values.
- If a QR scan returns to the login screen, record a feedback issue and leave the auth request uncompleted. Do not describe this as logged in.
- Proxy routing is identity-specific. `linkedin-main` has `iproyal:linkedin-main`; `discord-main` currently has no `proxy_ref` unless `ax-browser-identity status` says otherwise.
- Do not ask Federico to re-enter a password in AX41 when he explicitly asked for the Mac/depontefede browser. Use Mac depontefede CDP.

## Identity Examples

```bash
/root/ax-browser-broker/bin/ax-browser-identity status
/root/ax-browser-broker/bin/ax-browser-identity mirror-mac-profiles
/root/ax-browser-broker/bin/ax-browser-use --identity linkedin-main --json state
/root/ax-browser-broker/bin/ax-openbrowser --identity chrome-depontefede status
/root/ax-browser-broker/bin/ax-openbrowser --identity discord-main status
```

## Mac Profile Mirror

`mirror-mac-profiles` creates AX41 broker identities for Federico's Mac Chrome profiles and copies safe profile files into `/root/browser-pool/profiles/chrome-*`.

```bash
/root/ax-browser-broker/bin/ax-browser-identity mirror-mac-profiles --dry-run
/root/ax-browser-broker/bin/ax-browser-identity mirror-mac-profiles
/root/ax-browser-broker/bin/ax-mac-profile-sync status
/root/ax-browser-broker/bin/ax-mac-profile-sync sync --report-issue
/root/ax-browser-broker/bin/ax-browser-identity activate chrome-depontefede --slot pool-a
```

The mirror excludes raw cookie, password, token, and keychain-backed browser databases. Site login state that Chrome stores only in the Mac Keychain remains available through Mac depontefede CDP, not through Linux profile files.

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
