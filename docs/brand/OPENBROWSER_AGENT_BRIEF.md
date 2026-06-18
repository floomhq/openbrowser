# OpenBrowser Agent Brief

Use this when asking another agent to build landing pages, web app screens, docs, or product visuals.

## Core idea

OpenBrowser lets AI agents safely use real logged-in browsers.

The product is not a browser agent. It is the browser infrastructure and identity layer underneath agents.

## Must communicate

- agents lease browser sessions
- profiles stay signed in
- humans handle login / 2FA / passkeys
- credentials are not sent to the agent
- browser sessions are isolated
- proxy routes and telemetry are part of the infrastructure

## Visual direction

Use:

- light mode
- Apple-style glassmorphism
- subtle shadows
- soft translucent white panels
- monochrome OpenBrowser icons
- real app logos only for connected sites
- washed classical landscape backgrounds for hero visuals

Avoid:

- dark cyberpunk dashboards
- neon gradients
- random analytics charts
- "usage this month" SaaS billing widgets
- futuristic holograms
- copy that sounds like scraping or bypassing

## Hero visual recipe

OpenBrowser app shell:

- left: Browser Sessions with app logos
- center: Live Browser Session
- right: Session State with Profile, Residential Proxy, Human Handoff, Connection, Telemetry
- floating card: Human auth request
- bottom strip: Real browsers / Human in the loop / Audit ready

The live session should be recognizably inside OpenBrowser. Do not let the external app screenshot overpower the product.

## Voice

Short. Concrete. Builder-led.

Good example:

> A browser API my agents can call when they need to use the web like I would.

Do not write generic "future of work" copy.
