# OpenBrowser Landing Page Spec

## Goal

Explain OpenBrowser in under five seconds:

> Agents can safely use real logged-in browsers.

## Hero

Headline: `OpenBrowser`

Subheadline: `The browser infrastructure for AI agents.`

Support line: `Real browsers. Real logins. Human in the loop.`

Primary CTA: GitHub / Quick start

Secondary CTA: Docs

## Hero visual

Use the reference composition:

- large translucent OpenBrowser app shell
- left: Browser Sessions with app logos
- center: Live Browser Session
- right: Session State with Profile, Residential Proxy, Human Handoff, Connection
- floating card: Human auth request
- bottom strip: Real browsers / Human in the loop / Audit ready

## Section order

1. Hero
2. Problem: agents break at the logged-in web
3. How it works: lease -> act -> handoff -> continue -> release
4. Core primitives
5. Use cases
6. API / MCP quick start
7. Safety model
8. Open source / GitHub CTA

## Problem copy

Keep it concrete:

- public web is easy
- real work happens behind accounts
- login, 2FA, cookies, profiles, IPs, and browser state are the hard part
- agents should not see credentials

## Core primitives

- Browser lease
- Persistent profile
- Human auth handoff
- Residential proxy / network route
- CDP/API/MCP connection
- Audit and telemetry

## Use cases

- recruiting and sourcing workflows
- sales research and outreach prep
- vendor and ops portals
- travel booking before payment
- QA on logged-in apps
- internal admin work

## Safety language

Say:

- human approval for sensitive actions
- credentials are never sent to the agent
- raw cookies and tokens are not exposed through tools
- every session can leave an audit trail

Do not lead with proxy language. Explain routing as part of identity/session stability.
