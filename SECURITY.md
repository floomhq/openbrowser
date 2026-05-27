# Security Policy

## Supported Versions

Security fixes target the current `main` branch until versioned releases begin.

## Reporting A Vulnerability

Please report security issues privately to the repository owner. Do not open a public issue for vulnerabilities involving authentication, proxy credentials, profile data, browser sessions, or remote-control surfaces.

Include:

- affected commit or version
- reproduction steps
- expected impact
- whether secrets, cookies, profile data, or browser control were exposed

## Security Boundaries

OpenBrowser Broker is designed to avoid returning raw cookies, passwords, tokens, proxy credentials, or VNC passwords through API or MCP tools. Telemetry redacts sensitive fields and common secret-shaped values.

Human login handoff is intentionally manual. The project does not include CAPTCHA solving, credential theft, session-token extraction, or ban-circumvention automation.

## Deployment Notes

- Put the public API behind HTTPS.
- Use long random API keys.
- Keep `secrets/`, `state/`, profile directories, and browser pool directories out of git.
- Restrict access to auth handoff routes.
- Rotate API keys after sharing them with any external agent host.
