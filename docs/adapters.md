# Broker Adapters

## browser-use

Use:

```bash
/root/ax-browser-broker/bin/ax-browser-use --json open https://example.com
```

The wrapper leases a broker slot, passes `--cdp-url` to browser-use, exports common CDP environment variables, then releases the lease when the command exits.

## OpenBrowser

Use:

```bash
/root/ax-browser-broker/bin/ax-openbrowser status --format json
```

The wrapper leases a broker slot and gives OpenBrowser a temporary config whose `cdpPort` and `profileDir` point at the leased slot.

## Raw Lease

Use:

```bash
/root/ax-browser-broker/bin/ax-browser-lease --owner manual
```

This prints a lease JSON object for custom scripts.
