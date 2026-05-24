# Mac Chrome Profiles

AX41 Browser Broker can mirror Federico's Mac Chrome people/profiles as broker identities.

## What Gets Imported

- Chrome profile metadata from Mac Chrome `Local State`.
- Profile labels.
- Account email identifiers.
- Source profile directory names.
- Isolated AX41 profile directories under `/root/browser-pool/profiles/<identity>`.

## What Never Gets Imported

- Raw cookies.
- Raw saved passwords.
- Raw session tokens.
- macOS Keychain material.
- Chrome `Login Data`, `Cookies`, or token databases from the Mac profile.

macOS Chrome encrypts sensitive website/session/password state through Keychain. Linux Chrome on AX41 cannot consume that state as a portable session bundle. The broker therefore has no raw-token fallback path by design.

## Auth Fallback

The supported fallback is human auth into the AX41 profile:

1. Import Mac Chrome profile metadata with `ax-browser-identity import-mac-profiles`.
2. Create an auth request with `identity_id="chrome-..."`.
3. Federico logs in through the local noVNC portal.
4. Chrome stores the resulting session in that AX41 identity profile.
5. Agents later lease the same `identity_id`.

If Chrome Sync is enabled during human login, Chrome-managed passwords/bookmarks/extensions may sync through Google's normal Chrome account flow. Website login sessions still depend on the website and normally require login on AX41.

## Mirror And Tunnel Verification

Use the profile sync wrapper for the whole Mac dependency chain:

```bash
/root/ax-browser-broker/bin/ax-mac-profile-sync status
/root/ax-browser-broker/bin/ax-mac-profile-sync sync --dry-run
/root/ax-browser-broker/bin/ax-mac-profile-sync sync --report-issue
```

The wrapper verifies:

- Mac reverse SSH is reachable at AX41 `127.0.0.1:2222`.
- Mac Chrome CDP is reachable through AX41 `http://127.0.0.1:19333`.
- Mac Chrome profiles can be mirrored into `/root/browser-pool/profiles/chrome-*`.

If the Mac reverse tunnel is missing, reinstall the Mac launch agents from the Mac:

```bash
/root/ax-browser-broker/scripts/install-mac-reverse-tunnel.sh
```

The installer creates:

- `~/Library/LaunchAgents/dev.ax41.mac-reverse-ssh.plist`
- `~/Library/LaunchAgents/dev.ax41.chrome-cdp.plist`

The reverse SSH agent exposes Mac SSH only on AX41 localhost, not publicly. The Chrome CDP launch agent uses `~/.hermes/chrome-cdp-clone` and port `9333` on the Mac; AX41 then connects through `/root/.codex/scripts/mac-chrome-cdp ensure`.

## Slot Behavior

Imported `chrome-*` identities use `slot: "auto"` by default.

- Two different Chrome identities can run concurrently when free pool slots exist.
- The same identity remains exclusive and cannot be leased twice.
- Pinned/proxied identities such as `linkedin-main` remain fixed to their configured slot.
- Auto Chrome identities do not overwrite pinned/proxied slots.
- Direct activation refuses a slot that already has an active lease.
- Failed or contended lease attempts are recorded as broker telemetry events with `event_type: "error"`.

## Concurrency Guard

Lease selection runs under the broker lease-state file lock. During that locked section the broker:

- Garbage-collects stale leases.
- Rejects duplicate leases for the same identity.
- Checks which slots are already in use.
- Skips reserved pinned/proxied slots for auto Chrome identities.
- Activates the selected identity on the chosen concrete slot.
- Rechecks browser health before returning the lease.

That sequence is the runtime race mitigation for parallel agents requesting different Chrome profiles at the same time.

## Auth Cleanup

The auth handoff uses temporary local-only VNC credentials.

- If VNC startup is refused or fails, the temporary password file is removed.
- If VNC starts successfully, completion stops VNC, websockify, Chrome, and Xvfb helper processes.
- Completion removes the temporary password file.
- Ports `6081` and `5901` are expected to be closed when no auth handoff is active.
