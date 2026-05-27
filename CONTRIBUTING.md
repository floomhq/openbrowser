# Contributing

Thanks for helping improve OpenBrowser Broker.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
playwright install chromium
pytest -q
```

## Pull Request Checklist

- Keep secrets, cookies, tokens, proxy credentials, profile data, screenshots, and telemetry state out of git.
- Add or update tests for behavior changes.
- Run `python3 -m compileall ax_browser_broker tests`.
- Run `pytest -q`.
- Keep browser automation behind leases; do not add raw shared-CDP shortcuts.
- Use human auth handoff for login challenges and secret entry.
- Do not add CAPTCHA bypass or ban-circumvention automation.

## Tool Design

MCP and API tools need concise names, structured JSON outputs, actionable errors, and sanitized telemetry. Prefer workflow-safe tools over exposing low-level browser internals.
