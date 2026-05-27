# Open-Source Publishing Checklist

## GitHub SEO

Recommended repository description:

```text
Open-source browser automation broker for AI agents: persistent Chrome profiles, proxy-aware identities, remote API, MCP tools, browser-use/OpenBrowser adapters, human auth handoff, telemetry, and audits.
```

Recommended topics:

```text
browser-automation
ai-agents
mcp
model-context-protocol
openbrowser
browser-use
playwright
chrome-devtools-protocol
chrome-cdp
persistent-browser
browser-profiles
proxy
proxy-management
automation
headful-browser
human-in-the-loop
fastapi
python
remote-browser
agent-tools
```

## README SEO Targets

Primary keyword:

- browser automation broker

Secondary keywords:

- MCP browser automation
- persistent Chrome profiles
- browser-use adapter
- OpenBrowser API
- proxy browser automation
- AI agent browser sessions
- human auth handoff

## Release Gate

- README explains why the project exists in the first 150 words.
- `pyproject.toml` contains package metadata, keywords, scripts, license, and project URLs.
- `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, and `.env.example` exist.
- No tracked secrets, state files, profile data, screenshots, or local-only config.
- `pytest -q` passes.
- Public API smoke passes.
- Remote MCP smoke passes.
- Broker audit returns 100.

## GitHub Settings

Before making the repo public:

1. Confirm secret scanning is enabled.
2. Add a custom social preview image.
3. Add the repository description and topics above.
4. Enable issues.
5. Add the first release tag.
