import {
  Activity,
  ArrowRight,
  Bot,
  Check,
  ChevronRight,
  Cloud,
  Code2,
  ExternalLink,
  Github,
  KeyRound,
  Layers3,
  Lock,
  Moon,
  MousePointer2,
  Network,
  ShieldCheck,
  Sun,
  Terminal,
  UserRoundCheck,
  Globe2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./components/ui/card";
import { Separator } from "./components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { cn } from "./lib/utils";

const navItems = [
  { label: "Product", href: "#product" },
  { label: "API", href: "#api" },
  { label: "MCP", href: "#mcp" },
  { label: "Install", href: "#install" },
];

const proofItems = ["MIT licensed", "MCP native", "200 tests passing"];

const features = [
  {
    icon: Layers3,
    title: "Session-per-agent leases",
    body: "Agents reserve isolated Chrome sessions, heartbeat during work, then release the browser cleanly.",
  },
  {
    icon: KeyRound,
    title: "Persistent identities",
    body: "Named profiles keep account state for Gmail, LinkedIn, Slack, Microsoft, GitHub, and internal tools.",
  },
  {
    icon: UserRoundCheck,
    title: "Human login handoff",
    body: "Passwords, passkeys, 2FA, QR codes, and approvals happen in a browser portal — not in chat.",
  },
  {
    icon: Globe2,
    title: "Proxy-aware profiles",
    body: "Pin selected identities to residential or ISP routes while public QA sessions stay generic.",
  },
  {
    icon: MousePointer2,
    title: "Browser tools, not a black box",
    body: "Expose screenshots, snapshots, clicks, keyboard input, tabs, uploads, and navigation to the agent.",
  },
  {
    icon: Activity,
    title: "Telemetry and feedback",
    body: "Track leases, failures, browser actions, auth events, and issue reports for later audit.",
  },
];

const integrations = [
  "anthropic",
  "cursor",
  "googlechrome",
  "modelcontextprotocol",
  "github",
  "notion",
  "linear",
  "discord",
  "fastapi",
  "python",
  "docker",
  "cloudflare",
];

function ThemeToggle() {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem("openbrowser-theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = stored ? stored === "dark" : prefersDark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
  }, []);

  function toggleTheme() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("openbrowser-theme", next ? "dark" : "light");
  }

  return (
    <Button variant="outline" size="sm" onClick={toggleTheme} aria-label="Toggle theme">
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      <span className="hidden sm:inline">{dark ? "Light" : "Dark"}</span>
    </Button>
  );
}

function Header() {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur-xl">
      <div className="container flex h-16 items-center justify-between gap-4">
        <a href="/" className="flex min-w-0 items-center gap-3" aria-label="OpenBrowser home">
          <img src="/assets/brand/openbrowser-mark.svg" alt="" className="h-8 w-8 dark:invert" />
          <span className="text-base font-semibold tracking-normal">OpenBrowser</span>
        </a>
        <nav className="hidden items-center gap-1 md:flex" aria-label="Primary navigation">
          {navItems.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className="rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              {item.label}
            </a>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Button asChild size="sm">
            <a href="https://github.com/floomhq/openbrowser">
              <Github className="h-4 w-4" />
              <span className="hidden sm:inline">GitHub</span>
            </a>
          </Button>
        </div>
      </div>
    </header>
  );
}

function ProductPreview() {
  const sessions = [
    { name: "chrome-depontefede", state: "Active — leased by agent", logo: "googlechrome", active: true },
    { name: "chrome-work-main", state: "Idle", logo: "googlechrome" },
    { name: "linkedin-scraper", state: "Idle", logo: "googlechrome" },
  ];

  return (
    <Card className="product-shadow overflow-hidden">
      {/* Browser chrome titlebar */}
      <div className="flex h-11 items-center gap-3 border-b bg-muted/40 px-4">
        <div className="flex gap-1.5" aria-hidden="true">
          <span className="h-2 w-2 rounded-full bg-red-400" />
          <span className="h-2 w-2 rounded-full bg-amber-400" />
          <span className="h-2 w-2 rounded-full bg-emerald-400" />
        </div>
        <div className="ml-1 flex min-w-0 flex-1 items-center gap-2 rounded border bg-background px-2.5 py-1 text-xs text-muted-foreground">
          <Lock className="h-3 w-3 shrink-0 text-emerald-600" />
          <span className="truncate font-mono">openbrowser-auth.floom.dev/v1</span>
        </div>
      </div>

      {/* Two-column layout */}
      <div className="grid bg-background lg:grid-cols-[1fr_260px]">
        {/* Left: sessions + auth flow */}
        <section className="border-b p-5 lg:border-b-0 lg:border-r">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Sessions</p>
          <div className="mt-2.5 space-y-1.5">
            {sessions.map((s) => (
              <div
                key={s.name}
                className={cn(
                  "flex items-center gap-2.5 rounded border px-3 py-2.5",
                  s.active
                    ? "border-emerald-200 bg-emerald-50/60 dark:border-emerald-900 dark:bg-emerald-950/20"
                    : "bg-card",
                )}
              >
                <img src={`/assets/logos/${s.logo}.svg`} alt="" className="h-4 w-4 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium leading-none">{s.name}</div>
                  <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                    <span
                      className={cn(
                        "h-1.5 w-1.5 shrink-0 rounded-full",
                        s.active ? "bg-emerald-500" : "bg-muted-foreground/40",
                      )}
                    />
                    {s.state}
                  </div>
                </div>
                {s.active && (
                  <span className="shrink-0 rounded bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">
                    same tab
                  </span>
                )}
              </div>
            ))}
          </div>

          {/* Auth handoff */}
          <div className="mt-4 rounded border bg-muted/30 p-3.5">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 shrink-0 text-emerald-600" />
              <span className="text-sm font-medium">Login wall detected</span>
            </div>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              Agent paused. Human portal sent — password stays out of chat.
            </p>
            <div className="mt-2.5 flex items-center gap-1.5 rounded border bg-background px-2.5 py-1.5 font-mono text-xs text-muted-foreground">
              <Lock className="h-3 w-3 shrink-0 text-emerald-600" />
              <span className="truncate">openbrowser-auth.floom.dev/auth/tok_…</span>
            </div>
          </div>

          {/* Three-step flow */}
          <div className="mt-3.5 grid grid-cols-3 gap-2">
            {["1  lease", "2  handoff", "3  resume"].map((step) => (
              <div
                key={step}
                className="rounded border bg-card px-2 py-2 text-center font-mono text-xs font-medium text-muted-foreground"
              >
                {step}
              </div>
            ))}
          </div>
        </section>

        {/* Right: tool surface + security model */}
        <aside className="bg-muted/20 p-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Tool surface</p>
          <div className="mt-2 space-y-1.5">
            {[
              [MousePointer2, "Click and type"],
              [Code2, "Snapshot DOM"],
              [Cloud, "Upload files"],
              [Activity, "Record audit"],
            ].map(([Icon, label]) => (
              <div key={label as string} className="flex items-center gap-2.5 rounded border bg-card px-3 py-2 text-sm">
                <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span>{label as string}</span>
              </div>
            ))}
          </div>

          <p className="mt-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Security boundary</p>
          <div className="mt-2 rounded border border-zinc-800 bg-zinc-950 p-3">
            <div className="space-y-1.5 font-mono text-xs">
              {[
                ["agent sees", "pixels + DOM"],
                ["user enters", "password + 2FA"],
                ["agent never", "cookies + secrets"],
              ].map(([k, v]) => (
                <div key={k} className="flex items-center justify-between gap-2">
                  <span className="text-zinc-500">{k}</span>
                  <span className="text-emerald-300">{v}</span>
                </div>
              ))}
            </div>
          </div>
        </aside>
      </div>
    </Card>
  );
}

function Hero() {
  return (
    <section className="border-b bg-background">
      <div className="container py-16 lg:py-24">
        <div className="mx-auto max-w-3xl text-center">
          <h1 className="text-4xl font-semibold tracking-tight text-foreground sm:text-5xl lg:text-6xl">
            Keep AI agents logged in.
          </h1>
          <p className="mx-auto mt-5 max-w-xl text-base leading-7 text-muted-foreground sm:text-lg">
            OpenBrowser gives Claude, Codex, Cursor, and browser-use persistent Chrome profiles, safe human login
            handoff, and an auditable browser API — without raw CDP.
          </p>
          <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row">
            <Button asChild size="lg">
              <a href="#install">
                Install OpenBrowser
                <ArrowRight className="h-4 w-4" />
              </a>
            </Button>
            <Button asChild variant="outline" size="lg">
              <a href="https://github.com/floomhq/openbrowser">
                <Github className="h-4 w-4" />
                View on GitHub
              </a>
            </Button>
          </div>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-x-5 gap-y-2">
            {proofItems.map((item) => (
              <span key={item} className="flex items-center gap-1.5 text-sm text-muted-foreground">
                <Check className="h-3.5 w-3.5 text-emerald-600" />
                {item}
              </span>
            ))}
          </div>
        </div>
        <div id="product" className="mx-auto mt-14 max-w-6xl">
          <ProductPreview />
        </div>
      </div>
    </section>
  );
}

function LogoStrip() {
  return (
    <section className="border-b bg-muted/20">
      <div className="container py-8">
        <p className="mb-5 text-center text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Works with the stack you already run
        </p>
        <div className="flex flex-wrap items-center justify-center gap-3">
          {integrations.map((name) => (
            <div key={name} className="flex h-10 w-10 items-center justify-center rounded border bg-card p-2">
              <img src={`/assets/logos/${name}.svg`} alt={`${name} logo`} className="h-5 w-5 max-w-full opacity-70" />
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Features() {
  return (
    <section className="container py-20">
      <div className="mx-auto max-w-2xl text-center">
        <Badge variant="secondary" className="mb-4">
          Core capabilities
        </Badge>
        <h2 className="text-3xl font-semibold tracking-tight sm:text-4xl">One browser contract for every agent.</h2>
        <p className="mt-4 text-base leading-7 text-muted-foreground sm:text-lg">
          Stop fighting one shared CDP target. Give each agent a lease, a profile, a browser tool surface, and an audit
          trail.
        </p>
      </div>
      <div className="mt-12 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {features.map((feature) => (
          <Card key={feature.title}>
            <CardHeader>
              <div className="mb-2 flex h-9 w-9 items-center justify-center rounded border bg-muted">
                <feature.icon className="h-4 w-4" />
              </div>
              <CardTitle className="text-lg">{feature.title}</CardTitle>
              <CardDescription className="text-sm leading-6">{feature.body}</CardDescription>
            </CardHeader>
          </Card>
        ))}
      </div>
    </section>
  );
}

function CodeTabs() {
  const commands = useMemo(
    () => ({
      api: `curl "$BASE/leases" \\
  -H "authorization: Bearer $OPENBROWSER_API_KEY" \\
  -H "content-type: application/json" \\
  -d '{"owner":"codex","identity_id":"work-main"}'`,
      mcp: `{
  "mcpServers": {
    "openbrowser": {
      "command": "openbrowser-remote-mcp",
      "env": {
        "OPENBROWSER_BASE_URL": "https://browser.example.com/openbrowser/v1",
        "OPENBROWSER_API_KEY": "..."
      }
    }
  }
}`,
      auth: `openbrowser auth https://example.com/login \\
  --identity chrome-depontefede \\
  --owner agent-name`,
    }),
    [],
  );

  return (
    <Tabs defaultValue="api" className="w-full">
      <TabsList className="grid w-full grid-cols-3">
        <TabsTrigger value="api">Remote API</TabsTrigger>
        <TabsTrigger value="mcp">MCP</TabsTrigger>
        <TabsTrigger value="auth">Auth</TabsTrigger>
      </TabsList>
      {Object.entries(commands).map(([key, value]) => (
        <TabsContent key={key} value={key}>
          <pre className="overflow-x-auto rounded border bg-zinc-950 p-4 text-sm text-zinc-50">
            <code>{value}</code>
          </pre>
        </TabsContent>
      ))}
    </Tabs>
  );
}

function ApiSection() {
  return (
    <section id="api" className="border-y bg-muted/20">
      <div className="container grid gap-10 py-20 lg:grid-cols-[0.85fr_1.15fr]">
        <div className="flex flex-col justify-center">
          <Badge variant="outline" className="mb-4 w-fit">
            Remote API
          </Badge>
          <h2 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            Call a real browser from any machine.
          </h2>
          <p className="mt-4 text-base leading-7 text-muted-foreground">
            Put OpenBrowser behind HTTPS and give agents a bearer token. The same API handles leases, tabs, navigation,
            screenshots, keyboard events, auth handoff, telemetry, and audits.
          </p>
          <div className="mt-7 space-y-2.5">
            {[
              "No raw CDP ports for normal agent work",
              "Named profiles for persistent account state",
              "Same-lease login handoff for passwords and passkeys",
            ].map((item) => (
              <div key={item} className="flex items-start gap-3 text-sm">
                <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                {item}
              </div>
            ))}
          </div>
        </div>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Terminal className="h-4 w-4" />
              Developer surface
            </CardTitle>
            <CardDescription className="text-sm">
              API, MCP, and CLI share the same broker-backed session model.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <CodeTabs />
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

function FlowSection() {
  const steps = [
    [Bot, "Agent leases", "Reserve an isolated Chrome slot with an optional persistent identity."],
    [Cloud, "Broker opens", "Navigate, snapshot, screenshot, click, type, upload, and inspect tabs."],
    [UserRoundCheck, "Human approves", "Use /auth links for passwords, passkeys, 2FA, and login walls."],
    [Activity, "Audit remains", "Telemetry and feedback show who used which browser, when, and why."],
  ] as const;

  return (
    <section id="mcp" className="container py-20">
      <div className="mb-10 flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <Badge variant="secondary" className="mb-4">
            MCP native
          </Badge>
          <h2 className="max-w-xl text-3xl font-semibold tracking-tight sm:text-4xl">
            Browser control that stays understandable.
          </h2>
        </div>
        <p className="max-w-sm text-base leading-7 text-muted-foreground">
          A reliable browser layer that lets the calling model steer the work — not a mystery agent.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-4">
        {steps.map(([Icon, title, body], index) => (
          <Card key={title} className="relative overflow-hidden">
            <CardHeader>
              <div className="mb-4 flex items-center justify-between">
                <div className="flex h-9 w-9 items-center justify-center rounded bg-primary text-primary-foreground">
                  <Icon className="h-4 w-4" />
                </div>
                <div className="flex items-center gap-1">
                  <span className="font-mono text-xs text-muted-foreground">{index + 1}</span>
                  {index < steps.length - 1 && (
                    <ChevronRight className="hidden h-4 w-4 text-muted-foreground/50 md:block" />
                  )}
                </div>
              </div>
              <CardTitle className="text-base">{title}</CardTitle>
              <CardDescription className="text-sm leading-6">{body}</CardDescription>
            </CardHeader>
          </Card>
        ))}
      </div>
    </section>
  );
}

function InstallSection() {
  return (
    <section id="install" className="border-y bg-zinc-950 text-zinc-50">
      <div className="container grid gap-10 py-20 lg:grid-cols-[0.9fr_1.1fr]">
        <div>
          <Badge className="mb-4 bg-zinc-800 text-zinc-100">Open source</Badge>
          <h2 className="text-3xl font-semibold tracking-tight sm:text-4xl">Run it on your own browser host.</h2>
          <p className="mt-4 text-base leading-7 text-zinc-400">
            Install the broker, expose the API through your own HTTPS domain, and connect local or remote agents through
            MCP or HTTP.
          </p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Button asChild variant="secondary" size="lg">
              <a href="https://github.com/floomhq/openbrowser">
                <Github className="h-4 w-4" />
                View repository
              </a>
            </Button>
            <Button
              asChild
              variant="outline"
              size="lg"
              className="border-zinc-700 bg-transparent text-zinc-50 hover:bg-zinc-900"
            >
              <a href="https://github.com/floomhq/openbrowser/blob/main/docs/openbrowser-api.md">
                API docs
                <ExternalLink className="h-4 w-4" />
              </a>
            </Button>
          </div>
        </div>
        <div className="rounded border border-zinc-800 bg-zinc-900/80 p-4">
          <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
            <Code2 className="h-3.5 w-3.5" />
            Quick start
          </div>
          <pre className="overflow-x-auto text-sm leading-7 text-zinc-100">
            <code>{`git clone https://github.com/floomhq/openbrowser.git
cd openbrowser
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
openbrowser-broker

OPENBROWSER_API_KEYS="$(openssl rand -base64 48)"
openbrowser docs quickstart`}</code>
          </pre>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="container py-10">
      <Separator className="mb-8" />
      <div className="flex flex-col justify-between gap-4 text-sm text-muted-foreground md:flex-row md:items-center">
        <div className="flex items-center gap-3">
          <img src="/assets/brand/openbrowser-mark.svg" alt="" className="h-6 w-6 dark:invert" />
          <span>OpenBrowser</span>
        </div>
        <div className="flex flex-wrap items-center gap-5">
          <a className="hover:text-foreground" href="https://github.com/floomhq/openbrowser">
            GitHub
          </a>
          <a
            className="hover:text-foreground"
            href="https://github.com/floomhq/openbrowser/blob/main/docs/openbrowser-api.md"
          >
            API docs
          </a>
          <a
            className="hover:text-foreground"
            href="https://github.com/floomhq/openbrowser/blob/main/LICENSE"
          >
            MIT License
          </a>
        </div>
      </div>
    </footer>
  );
}

export default function App() {
  return (
    <div className="min-h-screen bg-background">
      <Header />
      <main>
        <Hero />
        <LogoStrip />
        <Features />
        <ApiSection />
        <FlowSection />
        <InstallSection />
      </main>
      <Footer />
    </div>
  );
}
