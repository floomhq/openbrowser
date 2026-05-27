# Chrome Profile Import

OpenBrowser Broker can mirror Chrome profile metadata from a workstation into broker identities.

## What Gets Copied

- Profile labels and account metadata from Chrome profile preferences.
- Safe profile files needed to create a Linux Chrome profile directory.
- Bookmarks, preferences, and other non-secret profile state where portable.
- Isolated broker profile directories under the configured browser pool path.

## What Is Never Copied

- Raw cookies
- Password databases
- Tokens
- Keychain material
- Browser databases that depend on a workstation secret store

macOS Chrome encrypts sensitive website, session, and password state through Keychain. Linux Chrome cannot consume that as a portable session bundle. The broker therefore has no raw-token fallback path by design.

## Auth Fallback

The supported fallback is human auth into the broker profile:

1. Create or mirror the identity.
2. Call `auth_request(..., identity_id="<identity>")`.
3. The human logs in through the temporary noVNC portal.
4. Chrome stores the resulting session in that broker identity profile.

During human login, imported identities can launch without the pool's normal sync-disabling flags. Chrome Sync can sync Chrome-managed passwords, bookmarks, and extensions when the human enables Sync in the broker Chrome profile. Website login sessions still depend on each website and often require login on the broker host.

## Remote Workstation Tunnel

If a workstation profile needs to be inspected remotely, use a reverse SSH tunnel and a dedicated Chrome CDP profile. The installer template is:

```bash
OPENBROWSER_BROKER_HOST=browser.example.com \
OPENBROWSER_BROKER_USER=root \
scripts/install-mac-reverse-tunnel.sh
```

The reverse SSH agent exposes workstation SSH only on broker localhost, not publicly.

## Autosync

The optional autosync timer mirrors profile metadata when the workstation tunnel is reachable:

```bash
cp systemd/ax-mac-profile-autosync.service /etc/systemd/system/openbrowser-profile-autosync.service
cp systemd/ax-mac-profile-autosync.timer /etc/systemd/system/openbrowser-profile-autosync.timer
systemctl daemon-reload
systemctl enable --now openbrowser-profile-autosync.timer
```

Pinned and proxied identities remain fixed to their configured slot and proxy policy.
